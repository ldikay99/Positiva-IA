"""
Donde vive el afectado, leido del texto.

El identificador de municipio lo decide SGDEA y no se puede componer a partir
del nombre: `business_rules/municipios.json` congela el catalogo que devuelve el
backend, para no pagar una consulta por correo contra un servicio que no siempre
responde.

Sin este modulo el municipio solo lo aporta el registro de personas, y cuando el
registro no tiene a la persona el radicador envia la capital por defecto. Eso
escribe una ciudad de residencia que nadie leyo, y de ese dato depende la
entrega cuando la respuesta sale por correo fisico.

Se resuelve de forma conservadora: un municipio equivocado manda la respuesta a
otra ciudad, que es peor que no decir nada y dejar el valor por defecto con su
anotacion. De ahi que se exija un rotulo o el departamento al lado, y que un
nombre ambiguo se descarte en vez de elegir uno.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from business_rules.geo_mapping import DEPARTAMENTOS_MAP

CATALOGO = Path(__file__).resolve().parent.parent / "business_rules" / "municipios.json"

# Nombres que no se aceptan solos. Son municipios reales, pero tambien son lo
# que queda cuando el rotulo esta vacio o cuando quien escribe pone ahi
# cualquier otra cosa. Con el departamento al lado si se aceptan.
_NO_SOLOS = frozenset({"COLOMBIA", "CENTRO", "PROVIDENCIA", "SANTA ROSA"})


def _normaliza(texto: str) -> str:
    n = unicodedata.normalize("NFD", (texto or "").upper())
    return " ".join(
        "".join(c for c in n if unicodedata.category(c) != "Mn").split())


@lru_cache(maxsize=1)
def _catalogo() -> Tuple[Dict[str, List[Tuple[int, int]]], Dict[int, Dict[str, int]]]:
    """
    Devuelve (por_nombre, por_departamento).

    `por_nombre` mapea el nombre normalizado a la lista de (departamento,
    municipio) donde existe, que es lo que permite saber si es ambiguo.
    """
    datos = json.loads(CATALOGO.read_text(encoding="utf-8"))
    por_nombre: Dict[str, List[Tuple[int, int]]] = {}
    por_departamento: Dict[int, Dict[str, int]] = {}
    for departamento, municipios in datos.items():
        dep = int(departamento)
        por_departamento[dep] = {}
        for m in municipios:
            nombre = _normaliza(m["nombre"])
            por_nombre.setdefault(nombre, []).append((dep, m["id"]))
            por_departamento[dep][nombre] = m["id"]
    return por_nombre, por_departamento


# Los departamentos, del nombre mas largo al mas corto: "NORTE DE SANTANDER"
# tiene que ganarle a "SANTANDER", que esta contenido en el.
@lru_cache(maxsize=1)
def _departamentos_por_largo() -> List[Tuple[str, int]]:
    return sorted(((_normaliza(n), i) for n, i in DEPARTAMENTOS_MAP.items()),
                  key=lambda p: -len(p[0]))


# El rotulo con el que se escribe la ciudad de residencia. "ciudad" va con
# frontera de palabra: sin ella lo captura "ciudadania", y lo que sigue a una
# cedula es la ciudad donde se EXPIDIO, no donde vive la persona.
_ROTULO = re.compile(
    r"\b(?:ciudad(?:\s+y\s+departamento)?|municipio|lugar\s+de\s+residencia"
    r"|residencia|domicilio)\s*:\s*"
    r"([^\n\r|]{3,60})",
    re.IGNORECASE,
)

# Sin rotulo, el verbo que dice que ahi se vive.
_RESIDE = re.compile(
    r"\b(?:resido|reside|residente|residenciad[oa]|domiciliad[oa]|vivo|vive)\s+"
    r"en\s+(?:el\s+municipio\s+de\s+|la\s+ciudad\s+de\s+|)"
    r"([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.'-]*(?:\s+[\wÁÉÍÓÚÑáéíóúñ.'-]+){0,3})",
    re.IGNORECASE,
)

# Lo que corta el nombre: a partir de aqui ya no es la ciudad.
_CORTE = re.compile(
    # El punto corta solo si cierra la frase: "BOGOTA D.C." lleva dos dentro
    # del nombre, y ahi el punto no separa nada.
    r"\s*(?:[,;()/|]|\.(?=\s|$)|\s-\s|\bdepartamento\b|\bdepto\b|\bdpto\b"
    r"|\btel\b|\bcel\b|\bcorreo\b|\bdireccion\b|\bbarrio\b|\bcra\b|\bcalle\b"
    r"|$)",
    re.IGNORECASE,
)


def _departamento_en(texto: str) -> Tuple[Optional[int], str]:
    """
    El departamento que nombra ese fragmento y el fragmento sin el.

    Se retira porque va pegado al municipio y sin separador: "Ciudad:
    venadillo Tolima" nombra los dos y "venadillo Tolima" no es ningun
    municipio.
    """
    plano = _normaliza(texto)
    for nombre, ident in _departamentos_por_largo():
        # El cierre no puede ser `\b`: "BOGOTA D.C." termina en punto y ahi no
        # hay frontera de palabra, asi que el nombre nunca casaria.
        patron = rf"\b{re.escape(nombre)}(?!\w)"
        if re.search(patron, plano):
            return ident, re.sub(patron, " ", plano)
    return None, plano


def _resolver(nombre: str, departamento: Optional[int]) -> Optional[Tuple[int, int]]:
    """
    (departamento, municipio) para ese nombre, o None si no se puede afirmar.

    Con departamento conocido se busca solo ahi. Sin el, el nombre tiene que
    existir en UN solo departamento: sesenta y siete nombres se repiten entre
    varios, y elegir uno manda la respuesta a otra region del pais.
    """
    por_nombre, por_departamento = _catalogo()
    clave = _normaliza(nombre)
    if not clave:
        return None

    if departamento is not None:
        del_departamento = por_departamento.get(departamento, {})
        municipio = del_departamento.get(clave)
        if municipio is not None:
            return departamento, municipio
        # El catalogo escribe algunos nombres completos y el correo los abrevia:
        # "BOGOTA D.C." por "Bogota" y "SAN JOSE DE CUCUTA" por "Cucuta". Vale
        # si UN solo municipio del departamento lo lleva como primera o como
        # ultima palabra; con mas de uno no se puede saber cual es.
        parciales = [i for n, i in del_departamento.items()
                     if n.startswith(clave + " ") or n.endswith(" " + clave)]
        if len(parciales) == 1:
            return departamento, parciales[0]
        # El departamento se nombro pero el municipio no encaja en el. No se
        # cae al nombre suelto: la pareja es la que describe un lugar.
        return None

    if clave in _NO_SOLOS:
        return None
    candidatos = por_nombre.get(clave)
    if candidatos is None:
        # El catalogo escribe el nombre largo —"SAN JOSE DE CUCUTA"— y el
        # correo la forma corta. Solo si en todo el pais hay uno asi.
        candidatos = [par for nombre, pares in por_nombre.items()
                      if nombre.endswith(" " + clave) for par in pares]
    if not candidatos or len(candidatos) > 1:
        return None
    return candidatos[0]


def _de_un_solo_municipio(
    departamento: int, resto: str
) -> Optional[Tuple[int, int]]:
    """
    El municipio cuando el fragmento solo nombra el departamento.

    Vale unicamente si ese departamento tiene UN municipio, que es el caso de
    Bogota: es distrito y departamento a la vez, y quien escribe "Ciudad:
    Bogota" no esta nombrando una region sino donde vive. Con cualquier otro,
    nombrar el departamento no dice el municipio y se deja sin resolver.
    """
    _, por_departamento = _catalogo()
    municipios = por_departamento.get(departamento, {})
    if len(municipios) != 1 or resto.strip(" .-"):
        return None
    return departamento, next(iter(municipios.values()))


def _fragmentos(texto: str) -> List[str]:
    """Los trozos del texto donde puede estar escrita la ciudad."""
    trozos = []
    for patron in (_ROTULO, _RESIDE):
        for m in patron.finditer(texto or ""):
            trozos.append(m.group(1))
    return trozos


def buscar(texto: str) -> Optional[Tuple[int, int, str]]:
    """
    (departamento, municipio, evidencia) leidos del texto, o None.

    Se recorren los fragmentos en el orden en que aparecen y gana el primero
    que resuelve. Un fragmento que nombra un departamento que no contiene al
    municipio no resuelve nada: describe un lugar que no existe.
    """
    for bruto in _fragmentos(texto):
        fragmento = " ".join(str(bruto).split())
        if not fragmento:
            continue
        departamento, sin_departamento = _departamento_en(fragmento)
        # El nombre es lo que va antes del primer separador; el departamento
        # suele ir detras, en el mismo fragmento, y ya se retiro.
        corte = _CORTE.search(sin_departamento)
        nombre = sin_departamento[:corte.start()] if corte else sin_departamento
        nombre = nombre.strip(" .-")

        par = _resolver(nombre, departamento)
        if par is None and departamento is not None:
            par = _de_un_solo_municipio(departamento, nombre)
        # Con el departamento nombrado no se cae a la busqueda nacional: si el
        # municipio no pertenece a el, la pareja se contradice y no se sabe
        # cual de los dos escribio mal quien redacto. Elegir el municipio y
        # descartar el departamento manda la respuesta a otra region.
        if par is None:
            continue
        dep, mun = par
        return dep, mun, f'el texto dice donde vive: "{fragmento[:60]}"'
    return None
