"""
Repara valores falsos del corpus que quedaron estructuralmente invalidos.

    python -m harness.reparar_corpus --revisar    solo informa
    python -m harness.reparar_corpus              aplica los cambios

Un valor falso con la longitud correcta puede seguir siendo imposible: un
celular con un prefijo que no existe en el plan de numeracion, o un NIT cuyo
digito de verificacion no cuadra. El pipeline los rechaza, y esa medicion no
dice nada del pipeline.

Tambien puede ser ambiguo sin serlo en la realidad: un documento de diez
digitos que empieza en 3 tiene la forma exacta de un movil, y ninguna regla
determinista puede separarlos. Un documento colombiano de diez digitos empieza
en 1.

La sustitucion cambia un valor inventado por otro inventado bien formado, y es
consistente dentro del caso: el mismo numero se reemplaza igual en el asunto,
el cuerpo, los adjuntos y el esperado, para que el caso siga ejercitando la
desambiguacion.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from harness.anonimizar import (
    _celular_valido, _documento_falso, _semilla_de, tiene_forma_de_movil)
from harness.caso import Caso, cargar_corpus
from servidor_mcp.herramientas import _digito_verificacion_nit
from utils.contacto_extractor import telefono_existe

RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"

_CELULAR = re.compile(r"(?<!\d)3\d{9}(?!\d)")
# Numero con indicativo de otro pais.
_TELEFONO_INTERNACIONAL = re.compile(r"\+\d(?:[\s\-.]?\d){6,15}")
# Documento con marca explicita de tipo de persona delante. La marca es lo que
# permite afirmar que la racha es un documento y no un telefono.
_DOC_MARCADO = re.compile(
    r"(?i)\b(?:c\.?\s?c\.?|c[eé]dula(?:\s+de\s+ciudadan[ií]a)?"
    r"|t\.?\s?i\.?|c\.?\s?e\.?)[\s.:#°No-]{0,6}(3\d{9})(?!\d)")
_DIEZ_DIGITOS = re.compile(r"(?<!\d)\d{10}(?!\d)")
# El NIT se escribe casi siempre con el digito de verificacion detras de un
# guion, y los miles con puntos: "469921223-2", "900.569886-2".
_NIT_CON_GUION = re.compile(
    r"(?<!\d)(\d[\d.\s]{7,12}\d)\s*[-–]\s*(\d)(?!\d)")
# Sin guion, el digito de verificacion queda separado por un espacio o un
# tabulador. Ahi si hace falta el rotulo NIT delante: sin el, cualquier
# "1234567 8" del texto entraria como si fuera un NIT.
_NIT_CON_ROTULO = re.compile(
    r"(?i)\bnit\b[\s.:#°No-]{0,6}(\d[\d.\s]{7,12}\d)[ \t]+(\d)(?!\d)")


def _celular_invalido(numero: str) -> bool:
    """Si el plan de numeracion no reconoce ese celular."""
    try:
        import phonenumbers
    except ImportError:
        return False
    try:
        return not phonenumbers.is_valid_number(
            phonenumbers.parse(numero, "CO"))
    except Exception:
        return True


def _nit_invalido(numero: str) -> bool:
    return int(numero[9]) != _digito_verificacion_nit(numero[:9])


def _internacional_falso(crudo: str, semilla: int) -> str:
    """
    Numero falso con el MISMO indicativo de pais y valido en ese plan.

    El indicativo lo determina la libreria: los codigos de pais tienen uno,
    dos o tres digitos.

    Se prueban candidatos derivados de la semilla y se acepta el primero que la
    tabla da por valido, igual que con el celular colombiano; si ninguno encaja
    se usa el numero de ejemplo del pais.

    Cadena vacia significa que no se pudo construir uno valido. Entonces el
    numero se deja como esta y se informa: sustituirlo por otro invalido haria
    que el reparador nunca terminase de repararlo.
    """
    try:
        import phonenumbers
    except ImportError:
        return ""
    try:
        n = phonenumbers.parse(crudo, None)
    except phonenumbers.NumberParseException:
        return ""
    indicativo = n.country_code
    largo = len(str(n.national_number))
    for intento in range(200):
        nacional = str(semilla // (intento + 1)).zfill(largo)[-largo:]
        candidato = f"+{indicativo}{nacional}"
        try:
            if phonenumbers.is_valid_number(phonenumbers.parse(candidato, None)):
                return candidato
        except phonenumbers.NumberParseException:
            continue
    region = phonenumbers.region_code_for_country_code(indicativo)
    ejemplo = phonenumbers.example_number(region) if region else None
    return f"+{indicativo}{ejemplo.national_number}" if ejemplo else ""


@dataclass
class Reparacion:
    """Sustituciones pendientes en un caso, por valor original."""

    caso_id: str
    telefonos: Dict[str, str] = field(default_factory=dict)
    nits: Dict[str, str] = field(default_factory=dict)
    documentos: Dict[str, str] = field(default_factory=dict)
    # Valores mal formados detectados que no se pudieron reemplazar.
    sin_reparar: List[str] = field(default_factory=list)

    @property
    def hay_algo(self) -> bool:
        return bool(self.telefonos or self.nits or self.documentos
                    or self.sin_reparar)

    def describir(self) -> List[str]:
        lineas = []
        for viejo, nuevo in self.telefonos.items():
            lineas.append(f"    telefono {viejo} -> {nuevo}  (prefijo inexistente)")
        for viejo, nuevo in self.nits.items():
            lineas.append(f"    NIT      {viejo} -> {nuevo}  (digito de verificacion)")
        for viejo, nuevo in self.documentos.items():
            lineas.append(f"    documento {viejo} -> {nuevo}  (forma de movil)")
        for aviso in self.sin_reparar:
            lineas.append(f"    SIN REPARAR: {aviso}")
        return lineas


def _textos(caso: Caso) -> List[str]:
    return [caso.asunto, caso.cuerpo_md, caso.hilo_md] + list(caso.adjuntos.values())


def planear(caso: Caso) -> Reparacion:
    """Que valores del caso estan mal formados y con cual se reemplazan."""
    r = Reparacion(caso_id=caso.id)
    unido = "\n".join(_textos(caso))

    for numero in set(_CELULAR.findall(unido)):
        if _celular_invalido(numero):
            r.telefonos[numero] = _celular_valido(
                _semilla_de(numero, "reparacion-tel"))

    # Con indicativo de otro pais: se conserva el indicativo y se cambia la
    # parte nacional por una que el plan de ese pais reconozca, para que el
    # caso siga ejercitando el camino internacional del extractor.
    for m in _TELEFONO_INTERNACIONAL.finditer(unido):
        crudo = m.group(0)
        digitos = re.sub(r"\D", "", crudo)
        if digitos.startswith("57"):
            continue          # el colombiano lo cubren las reglas de arriba
        if telefono_existe(crudo):
            continue
        nuevo = _internacional_falso(
            crudo, _semilla_de(digitos, "reparacion-tel-int"))
        if not nuevo:
            r.sin_reparar.append(
                f"telefono {crudo}: no se pudo construir uno valido con ese "
                f"indicativo de pais")
            continue
        r.telefonos[crudo] = nuevo

    for numero in set(_DIEZ_DIGITOS.findall(unido)):
        if numero in r.telefonos or numero.startswith("3"):
            continue
        if _nit_invalido(numero):
            r.nits[numero] = numero[:9] + str(_digito_verificacion_nit(numero[:9]))

    for patron in (_NIT_CON_GUION, _NIT_CON_ROTULO):
        for m in patron.finditer(unido):
            _anotar_nit_separado(r, m)

    # Documento con marca de tipo cuya racha tiene forma de movil. No se toca
    # si el mismo numero aparece tambien como telefono en el caso: ahi
    # sustituirlo cambiaria los dos datos a la vez.
    for numero in set(_DOC_MARCADO.findall(unido)):
        if numero in r.telefonos or _es_tambien_telefono(unido, numero):
            continue
        r.documentos[numero] = _documento_falso(
            _semilla_de(numero, "reparacion-doc"), len(numero))
    return r


def _es_tambien_telefono(texto: str, numero: str) -> bool:
    """Si la misma racha aparece rotulada como telefono en algun sitio."""
    return bool(re.search(
        r"(?i)\b(?:tel|tels|telefono|tel[eé]fono|cel|celular|m[oó]vil|movil|"
        r"whatsapp|contacto|pbx)\b[\s.:#°No-]{0,8}" + re.escape(numero),
        texto))


def _anotar_nit_separado(r: Reparacion, m: "re.Match") -> None:
    """
    Anota la correccion de un NIT cuyo digito de verificacion va aparte.

    La clave de sustitucion es el texto EXACTO que trae el correo, no una forma
    reconstruida: el separador puede ser un guion, un guion con espacios, un
    espacio solo o un tabulador.
    """
    escrito, base_escrita, dv = m.group(0), m.group(1), m.group(2)
    base = re.sub(r"\D", "", base_escrita)
    if len(base) != 9:
        return
    correcto = _digito_verificacion_nit(base)
    if int(dv) == correcto:
        return
    r.nits[escrito] = escrito[: escrito.rfind(dv)] + str(correcto)


def aplicar(caso: Caso, reparacion: Reparacion) -> Caso:
    """Devuelve el caso con las sustituciones hechas en TODOS sus textos."""
    cambios = {**reparacion.telefonos, **reparacion.nits,
               **reparacion.documentos}
    if not cambios:
        return caso

    def sustituir(texto: str) -> str:
        for viejo, nuevo in cambios.items():
            texto = re.sub(rf"(?<!\d){re.escape(viejo)}(?!\d)", nuevo, texto or "")
        return texto

    caso.asunto = sustituir(caso.asunto)
    caso.cuerpo_md = sustituir(caso.cuerpo_md)
    caso.hilo_md = sustituir(caso.hilo_md)
    caso.adjuntos = {n: sustituir(t) for n, t in caso.adjuntos.items()}
    # El esperado tambien: si no, el caso deja de coincidir consigo mismo.
    for campo in ("numero_documento_afectado", "numero_documento_remitente",
                  "telefono_afectado"):
        valor = getattr(caso.esperado, campo, None)
        if valor in cambios:
            setattr(caso.esperado, campo, cambios[valor])
    return caso


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--revisar", action="store_true",
                   help="solo informa, no escribe")
    args = p.parse_args()

    casos = cargar_corpus(RAIZ_CORPUS)
    if not casos:
        print("Corpus vacio.")
        return 1

    total_tel = total_nit = total_doc = total_sin = con_cambios = 0
    for caso in casos:
        r = planear(caso)
        if not r.hay_algo:
            continue
        con_cambios += 1
        total_tel += len(r.telefonos)
        total_nit += len(r.nits)
        total_doc += len(r.documentos)
        total_sin += len(r.sin_reparar)
        print(f"  {caso.id}")
        for linea in r.describir():
            print(linea)
        if not args.revisar:
            aplicar(caso, r).guardar(RAIZ_CORPUS)

    print(f"\n{con_cambios} de {len(casos)} casos con valores mal formados")
    print(f"  telefonos con prefijo inexistente   {total_tel}")
    print(f"  NIT con digito que no cuadra        {total_nit}")
    print(f"  documentos con forma de movil       {total_doc}")
    if total_sin:
        print(f"  detectados y NO reparados           {total_sin}")
    if args.revisar:
        print("\n(solo revision: no se escribio nada)")
    # Un valor detectado y no reparado deja el corpus a medias: sale distinto de
    # cero para que no pase por una corrida limpia.
    return 1 if total_sin else 0


if __name__ == "__main__":
    raise SystemExit(main())
