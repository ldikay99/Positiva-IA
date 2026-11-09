"""
Espacio de trabajo efimero por correo.

Deja en disco lo que el correo produjo, para poder auditar que leyo la IA y
reprocesar sin repetir el OCR.

Estructura:

    <raiz>/<id_correo>/
        manifest.json           inventario de todo lo materializado
        correo.md               cuerpo del correo actual
        hilo.md                 mensajes anteriores, atribuidos
        adjuntos/001-nombre.md  cada adjunto convertido a Markdown
        extraccion.json         lo que resolvieron los algoritmos
        resolucion.json         lo que resolvio la IA
        validacion.json         veredicto de la compuerta final

Se usa como gestor de contexto para que la limpieza este garantizada incluso
si el procesamiento revienta:

    with Workspace.crear(id_correo) as ws:
        ws.escribir_correo(cuerpo_md)
        ...
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pipeline import indice

# Estados posibles de un archivo materializado. Explicitos para que un fallo
# de conversion quede registrado en lugar de desaparecer.
ESTADO_OK = "convertido"
ESTADO_VACIO = "sin_contenido"
ESTADO_NO_SOPORTADO = "tipo_no_soportado"
ESTADO_ERROR = "error_conversion"
ESTADO_DEMASIADO_GRANDE = "excede_limite"

# Caracteres que pueden ocupar entre todos los indices por hoja del
# inventario. El agente recorta cada respuesta de herramienta a unos 2.500, y
# el resto del inventario (ids, nombres, tamaños) tambien tiene que caber.
PRESUPUESTO_INDICE = 1_000


@dataclass
class EntradaManifiesto:
    """Un archivo materializado en el workspace."""

    nombre_original: str
    ruta_md: str
    tipo_declarado: str = ""      # lo que decia la extension o el mime
    tipo_real: str = ""           # lo que dicen los bytes
    bytes_originales: int = 0
    sha256: str = ""
    estado: str = ESTADO_OK
    detalle: str = ""             # motivo cuando el estado no es OK
    caracteres_md: int = 0
    fragmentos: int = 1
    ocr_aplicado: bool = False


@dataclass
class Manifiesto:
    """Inventario completo de lo que produjo un correo."""

    id_correo: str
    asunto: str = ""
    remitente_nombre: str = ""
    remitente_correo: str = ""
    entradas: List[EntradaManifiesto] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)

    def agregar(self, entrada: EntradaManifiesto) -> None:
        self.entradas.append(entrada)

    def avisar(self, mensaje: str) -> None:
        """Registra un fallo que no detiene el procesamiento."""
        if mensaje not in self.avisos:
            self.avisos.append(mensaje)

    @property
    def convertidos(self) -> List[EntradaManifiesto]:
        return [e for e in self.entradas if e.estado == ESTADO_OK]

    @property
    def fallidos(self) -> List[EntradaManifiesto]:
        return [e for e in self.entradas if e.estado not in (ESTADO_OK, ESTADO_VACIO)]

    def a_dict(self) -> Dict[str, Any]:
        return {
            "id_correo": self.id_correo,
            "asunto": self.asunto,
            "remitente_nombre": self.remitente_nombre,
            "remitente_correo": self.remitente_correo,
            "total_entradas": len(self.entradas),
            "convertidos": len(self.convertidos),
            "fallidos": len(self.fallidos),
            "avisos": self.avisos,
            "entradas": [asdict(e) for e in self.entradas],
        }


class Workspace:
    """
    Directorio efimero de trabajo para un correo.

    No se instancia directamente: usar `crear`, que garantiza un directorio
    limpio y aislado.
    """

    def __init__(self, raiz: Path, id_correo: str) -> None:
        self.raiz = Path(raiz)
        self.id_correo = id_correo
        self.manifiesto = Manifiesto(id_correo=id_correo)
        self._temporal = False
        self.dir_adjuntos = self.raiz / "adjuntos"

    # ── Ciclo de vida ──

    @classmethod
    def crear(
        cls,
        id_correo: str,
        base: Optional[Path] = None,
        temporal: bool = True,
    ) -> "Workspace":
        """
        Crea el workspace. Si `base` es None se usa un temporal del sistema.

        `temporal=True` significa que se borra al salir del contexto. Se pone
        en False para poder inspeccionar el resultado durante el desarrollo.
        """
        seguro = nombre_de_directorio(id_correo)
        if base is None:
            raiz = Path(tempfile.mkdtemp(prefix=f"pqrd-{seguro[:24]}-"))
            temporal = True
        else:
            raiz = Path(base) / seguro
            raiz.mkdir(parents=True, exist_ok=True)
        ws = cls(raiz, id_correo)
        ws._temporal = temporal
        ws.dir_adjuntos.mkdir(parents=True, exist_ok=True)
        return ws

    @property
    def es_temporal(self) -> bool:
        """Si `cerrar` borra el directorio con todo lo que tenga dentro."""
        return self._temporal

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *_excepcion) -> None:
        self.cerrar()

    def cerrar(self) -> None:
        """Escribe el manifiesto (siempre, incluso si fallo) y limpia."""
        try:
            self.escribir_manifiesto()
        finally:
            if self._temporal and self.raiz.exists():
                shutil.rmtree(self.raiz, ignore_errors=True)

    # ── Escritura ──

    def escribir_correo(self, markdown: str) -> Path:
        return self._escribir(self.raiz / "correo.md", markdown)

    def escribir_hilo(self, markdown: str) -> Path:
        return self._escribir(self.raiz / "hilo.md", markdown)

    def escribir_adjunto(self, indice: int, nombre: str, markdown: str) -> Path:
        destino = self.dir_adjuntos / f"{indice:03d}-{_slug(nombre)}.md"
        return self._escribir(destino, markdown)

    def escribir_json(self, nombre: str, datos: Any) -> Path:
        destino = self.raiz / nombre
        destino.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return destino

    def escribir_manifiesto(self) -> Path:
        return self.escribir_json("manifest.json", self.manifiesto.a_dict())

    def _escribir(self, destino: Path, contenido: str) -> Path:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(contenido or "", encoding="utf-8")
        return destino

    # ── Lectura, que es lo que consumiran las herramientas MCP ──

    def listar(self) -> List[Dict[str, Any]]:
        """
        Inventario sin contenido, para que la IA decida que leer.

        Cada adjunto con secciones de pagina lleva su indice por hoja, una
        linea corta por hoja o rango ("p2-4 historia_clinica",
        "p5 cedula ocr0.58 id1:CC"): dice donde mirar sin mandar el texto.
        """
        salida: List[Dict[str, Any]] = []
        for nombre, ruta in (("correo", self.raiz / "correo.md"),
                             ("hilo", self.raiz / "hilo.md")):
            if ruta.exists():
                texto = ruta.read_text(encoding="utf-8")
                salida.append({
                    "id": nombre, "tipo": nombre,
                    "caracteres": len(texto),
                    "lineas": texto.count("\n") + 1,
                })
        hojas_por_doc: Dict[int, List[Any]] = {}
        for entrada in self.manifiesto.entradas:
            doc: Dict[str, Any] = {
                "id": Path(entrada.ruta_md).stem,
                "tipo": "adjunto",
                "nombre_original": entrada.nombre_original,
                "caracteres": entrada.caracteres_md,
                "estado": entrada.estado,
                "ocr": entrada.ocr_aplicado,
            }
            if entrada.estado == ESTADO_OK:
                hojas, clase = self._indice_de(Path(entrada.ruta_md))
                if hojas:
                    hojas_por_doc[len(salida)] = hojas
                    doc["paginas"] = indice.describir_paginas(
                        [], hojas=hojas)
                elif clase:
                    doc["clase"] = clase
            salida.append(doc)
        self._comprimir_indices(salida, hojas_por_doc)
        return salida

    def _comprimir_indices(self, salida: List[Dict[str, Any]],
                           hojas_por_doc: Dict[int, List[Any]]) -> None:
        """
        Sube el nivel de agrupacion del indice mas largo hasta que todos
        juntos quepan en `PRESUPUESTO_INDICE`: el inventario viaja en una
        respuesta que el agente recorta, y lo que se corta no se ve.
        """
        nivel = {i: indice.NIVEL_DETALLE for i in hojas_por_doc}

        def largo(i: int) -> int:
            return len(json.dumps(salida[i]["paginas"], ensure_ascii=False))

        while sum(largo(i) for i in hojas_por_doc) > PRESUPUESTO_INDICE:
            candidatos = [i for i in hojas_por_doc
                          if nivel[i] < indice.NIVELES[-1]]
            if not candidatos:
                break
            i = max(candidatos, key=largo)
            nivel[i] += 1
            salida[i]["paginas"] = indice.describir_paginas(
                [], hojas=hojas_por_doc[i], nivel=nivel[i])

    def _ids_de_documentos(self) -> List[str]:
        """Los documentos legibles, sin calcular el indice por hoja."""
        ids = [n for n in ("correo", "hilo") if (self.raiz / f"{n}.md").exists()]
        ids.extend(Path(e.ruta_md).stem for e in self.manifiesto.entradas
                   if e.ruta_md and e.estado == ESTADO_OK)
        return ids

    def _lineas_de(self, id_documento: str) -> Optional[List[str]]:
        ruta = self._resolver(id_documento)
        if ruta is None or not ruta.exists():
            return None
        return ruta.read_text(encoding="utf-8").splitlines()

    def _indice_de(self, ruta_md: Path) -> Tuple[List[Any], str]:
        """
        Las hojas de un adjunto (o, si no tiene paginas, la clase del texto
        entero), cacheadas por ruta, tamaño y fecha: el inventario se pide en
        cada correo y clasificar es leer el documento entero.
        """
        ruta = ruta_md if ruta_md.is_absolute() else self.raiz / ruta_md
        if not ruta.exists():
            ruta = self._resolver(ruta_md.stem) or ruta
        if not ruta.exists():
            return [], ""
        try:
            st = ruta.stat()
            clave = (str(ruta), st.st_size, st.st_mtime_ns)
        except OSError:
            return [], ""
        cache = self.__dict__.setdefault("_cache_indice", {})
        if clave not in cache:
            lineas = ruta.read_text(encoding="utf-8").splitlines()
            paginas = indice.paginas_de(lineas)
            if paginas:
                cache[clave] = (indice.hojas_de(lineas, paginas), "")
            else:
                cache[clave] = ([], indice.describir_texto("\n".join(lineas)))
        return cache[clave]

    def leer_pagina(self, id_documento: str, pagina: int) -> Tuple[str, int]:
        """
        Una seccion `## Pagina N` entera. Devuelve el texto y la linea donde
        empieza, para que la IA pueda seguir leyendo por rango si se corta.
        """
        lineas = self._lineas_de(id_documento)
        if lineas is None:
            raise FileNotFoundError(f"documento no encontrado: {id_documento}")
        paginas = indice.paginas_de(lineas)
        for p in paginas:
            if p.numero == pagina:
                return "\n".join(lineas[p.inicio:p.fin]), p.inicio
        if not paginas:
            raise ValueError(
                f"'{id_documento}' no tiene secciones de pagina: lee por lineas")
        raise ValueError(
            f"'{id_documento}' no tiene pagina {pagina}; tiene de "
            f"{paginas[0].numero} a {paginas[-1].numero}")

    def leer(self, id_documento: str, desde: int = 0, hasta: int = 0) -> str:
        """
        Lee un documento por rango de lineas.

        El rango existe para controlar el gasto: la IA pide el fragmento que
        necesita en vez de recibir el documento completo, que es exactamente
        lo que hace caro el pipeline actual.
        """
        ruta = self._resolver(id_documento)
        if ruta is None or not ruta.exists():
            raise FileNotFoundError(f"documento no encontrado: {id_documento}")
        lineas = ruta.read_text(encoding="utf-8").splitlines()
        if hasta <= 0:
            hasta = len(lineas)
        desde = max(0, desde)
        return "\n".join(lineas[desde:hasta])

    def buscar(self, patron: str, contexto: int = 1,
               documento: str = "") -> List[Dict[str, Any]]:
        """
        Busca en todos los documentos y devuelve ubicaciones con contexto.

        Cada resultado trae documento, numero de linea, pagina (si el
        documento tiene secciones de pagina) y las lineas vecinas.

        La busqueda no depende de como lo escribio el OCR: patron y texto se
        comparan sin tildes ni mayusculas ("cedula" encuentra "CÉDULA"), un
        dato partido por un salto de linea se encuentra igual, y una cifra de
        cinco o mas digitos admite separadores de miles ("1087995336"
        encuentra "1.087.995.336").
        """
        cifra = indice.patron_de_cifra(patron)
        fuente = cifra if cifra is not None else indice.plegar_patron(patron)
        try:
            regex = re.compile(fuente, re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"patron invalido: {e}") from e
        resultados: List[Dict[str, Any]] = []
        ids = self._ids_de_documentos()
        if documento:
            # El modelo nombra el documento por su id o por el nombre del
            # archivo; las dos formas valen.
            ruta = self._resolver(documento)
            if ruta is None:
                ruta = next((self.raiz / e.ruta_md for e in self.manifiesto.entradas
                             if e.ruta_md and e.nombre_original == documento), None)
            if ruta is None:
                raise FileNotFoundError(f"documento no encontrado: {documento}")
            ids = [i for i in ids if self._resolver(i) == ruta]
        for id_doc in ids:
            lineas = self._lineas_de(id_doc)
            if lineas is None:
                continue
            plegadas = [indice.plegar(l) for l in lineas]
            paginas = indice.paginas_de(lineas)
            n = 0
            while n < len(lineas):
                m = regex.search(plegadas[n])
                cruza = False
                if m is None and n + 1 < len(lineas):
                    # El OCR parte las frases donde se le acaba el renglon.
                    # Solo cuenta si la coincidencia cruza el salto: si cabe
                    # entera en la siguiente, se reporta en la siguiente.
                    corte = len(plegadas[n])
                    unidas = plegadas[n] + " " + plegadas[n + 1]
                    for mu in regex.finditer(unidas):
                        if mu.start() < corte < mu.end() and mu.end() > mu.start():
                            m, cruza = mu, True
                            break
                if m is None or m.end() == m.start():
                    n += 1
                    continue
                if cruza:
                    original = lineas[n] + " " + lineas[n + 1]
                else:
                    original = lineas[n]
                ini = max(0, n - contexto)
                fin = min(len(lineas), n + (2 if cruza else 1) + contexto)
                resultado = {
                    "documento": id_doc,
                    "linea": n,
                    "coincidencia": _alrededor(original, m.start(), m.end(), 200),
                    "contexto": _acotar_contexto(
                        lineas[ini:fin], n - ini, m.start(), m.end()),
                }
                pagina = indice.pagina_de_linea(paginas, n)
                if pagina is not None:
                    resultado["pagina"] = pagina
                resultados.append(resultado)
                n += 2 if cruza else 1
        return resultados

    def consultar(self, pregunta: str, maximo: int = 3,
                  caracteres: int = 1800) -> List[Dict[str, Any]]:
        """
        Los tramos que mejor responden a una pregunta, por BM25.

        Se puntuan ventanas de unas quince lineas dentro de cada pagina (o del
        documento, si no tiene paginas) sobre texto plegado. Devuelve como
        mucho `maximo` tramos que no se solapan, y entre todos no pasan de
        `caracteres`: la respuesta de una herramienta viaja en cada vuelta.
        """
        consulta = indice.terminos(pregunta)
        if not consulta:
            return []
        tramos: List[Tuple[str, Optional[int], int, int]] = []
        tokens: List[List[str]] = []
        cache_lineas: Dict[str, List[str]] = {}
        for id_doc in self._ids_de_documentos():
            lineas = self._lineas_de(id_doc)
            if not lineas:
                continue
            cache_lineas[id_doc] = lineas
            paginas = indice.paginas_de(lineas)
            secciones = ([(p.numero, p.inicio + 1, p.fin) for p in paginas]
                         or [(None, 0, len(lineas))])
            for numero, ini, fin in secciones:
                utiles = [i for i in range(ini, fin) if lineas[i].strip()]
                if not utiles:
                    continue
                paso = _VENTANA_LINEAS // 2
                for k in range(0, max(1, len(utiles) - paso), paso):
                    grupo = utiles[k:k + _VENTANA_LINEAS]
                    tramos.append((id_doc, numero, grupo[0], grupo[-1] + 1))
                    tokens.append(indice.terminos(
                        "\n".join(lineas[grupo[0]:grupo[-1] + 1])))
        if not tramos:
            return []
        bm25 = indice.BM25(tokens)
        # Lo que se pregunto pesa entero; sus sinonimos ("cedula" -> "cc"),
        # la mitad.
        pesos = indice.expandir(consulta)
        puntuados = sorted(
            ((bm25.puntaje(i, pesos), i) for i in range(len(tramos))),
            key=lambda x: -x[0])
        maximo = max(1, maximo)
        elegidos: List[Tuple[float, int]] = []
        for puntaje, i in puntuados:
            if puntaje <= 0 or len(elegidos) >= maximo:
                break
            doc, _, ini, fin = tramos[i]
            if any(tramos[j][0] == doc and tramos[j][2] < fin and ini < tramos[j][3]
                   for _, j in elegidos):
                continue
            # El mismo soporte adjunto dos veces no es un segundo hallazgo.
            if any(tokens[j] == tokens[i] for _, j in elegidos):
                continue
            elegidos.append((puntaje, i))
        if not elegidos:
            return []
        por_tramo = max(200, caracteres // len(elegidos))
        terminos_consulta = set(pesos)
        salida: List[Dict[str, Any]] = []
        for puntaje, i in elegidos:
            doc, pagina, ini, fin = tramos[i]
            lineas = cache_lineas[doc]
            # La linea del tramo con mas terminos de la pregunta es el centro.
            mejor = max(range(ini, fin), key=lambda n: (
                len(terminos_consulta & set(indice.terminos(lineas[n]))), -n))
            desde, hasta, texto = _recortar_alrededor(lineas, ini, fin, mejor,
                                                      por_tramo, terminos_consulta)
            fragmento: Dict[str, Any] = {"documento": doc}
            if pagina is not None:
                fragmento["pagina"] = pagina
            fragmento.update({"linea": desde, "puntaje": round(puntaje, 2),
                              "texto": texto})
            salida.append(fragmento)
        return salida

    def _resolver(self, id_documento: str) -> Optional[Path]:
        if id_documento in ("correo", "correo.md"):
            return self.raiz / "correo.md"
        if id_documento in ("hilo", "hilo.md"):
            return self.raiz / "hilo.md"
        candidato = self.dir_adjuntos / f"{id_documento}.md"
        if candidato.exists():
            return candidato
        for ruta in self.dir_adjuntos.glob("*.md"):
            if ruta.stem == id_documento or id_documento in ruta.stem:
                return ruta
        return None

    def texto_completo(self) -> str:
        """
        Todo el contenido concatenado. Solo para la linea base comparativa:
        el pipeline no debe mandar todo al modelo.
        """
        partes: List[str] = []
        for id_doc in self._ids_de_documentos():
            ruta = self._resolver(id_doc)
            if ruta and ruta.exists():
                partes.append(ruta.read_text(encoding="utf-8"))
        return "\n\n".join(partes)


# Lineas no vacias por ventana de la consulta BM25.
_VENTANA_LINEAS = 15
# Tope del contexto de una coincidencia: el OCR deja paginas enteras en una
# sola linea, y sin tope una coincidencia traeria la pagina completa.
_MAX_CONTEXTO_BUSQUEDA = 800


def _alrededor(texto: str, inicio: int, fin: int, largo: int) -> str:
    """El trozo de `texto` de unos `largo` caracteres centrado en [inicio, fin)."""
    if len(texto) <= largo:
        return texto.strip()
    sobra = max(0, largo - (fin - inicio))
    desde = max(0, inicio - sobra // 2)
    hasta = min(len(texto), desde + largo)
    desde = max(0, hasta - largo)
    return ("…" if desde > 0 else "") + texto[desde:hasta].strip() + (
        "…" if hasta < len(texto) else "")


def _acotar_contexto(lineas: List[str], fila: int, inicio: int, fin: int) -> str:
    """Las lineas vecinas, recortadas alrededor de la coincidencia si son largas."""
    unido = "\n".join(lineas)
    if len(unido) <= _MAX_CONTEXTO_BUSQUEDA:
        return unido
    base = sum(len(l) + 1 for l in lineas[:fila])
    return _alrededor(unido, base + inicio, base + fin, _MAX_CONTEXTO_BUSQUEDA)


def _recortar_alrededor(lineas: List[str], ini: int, fin: int, centro: int,
                        limite: int, terminos: set) -> Tuple[int, int, str]:
    """
    Lineas del tramo [ini, fin) que caben en `limite`, creciendo desde
    `centro` hacia los dos lados. Si la linea central sola no cabe, se corta
    alrededor del primer termino de la pregunta que aparezca en ella.
    """
    linea = lineas[centro]
    if len(linea) > limite:
        plegada = indice.plegar(linea)
        pos = 0
        for m in indice.PALABRA.finditer(plegada):
            if indice.raiz(m.group(0)) in terminos:
                pos = m.start()
                break
        return centro, centro + 1, _alrededor(linea, pos, pos + 1, limite)
    desde, hasta, total = centro, centro + 1, len(linea)
    while True:
        crecio = False
        if desde > ini and total + len(lineas[desde - 1]) + 1 <= limite:
            desde -= 1
            total += len(lineas[desde]) + 1
            crecio = True
        if hasta < fin and total + len(lineas[hasta]) + 1 <= limite:
            total += len(lineas[hasta]) + 1
            hasta += 1
            crecio = True
        if not crecio:
            break
    return desde, hasta, "\n".join(lineas[desde:hasta]).strip("\n")


def sha256_de(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


# Cuanto del identificador se conserva legible en el nombre del directorio, y
# cuanto del resumen se le pega para distinguirlo.
_LARGO_LEGIBLE = 40
_LARGO_DEL_RESUMEN = 16


def nombre_de_directorio(id_correo: str) -> str:
    """
    Nombre de carpeta unico por correo.

    El identificador solo no sirve: los de Microsoft Graph del mismo buzon
    miden mas de ciento cincuenta caracteres y coinciden en casi todos, asi que
    recortarlos da la misma carpeta para todos los correos de la bandeja, y
    entonces cada uno pisa los documentos del anterior. Se le pega un resumen
    del identificador COMPLETO, que si depende de la parte que los distingue.
    """
    completo = str(id_correo or "")
    resumen = hashlib.sha256(completo.encode("utf-8")).hexdigest()[:_LARGO_DEL_RESUMEN]
    legible = _slug(completo)[:_LARGO_LEGIBLE].strip("-._")
    return f"{legible}-{resumen}" if legible else resumen


def _slug(nombre: str) -> str:
    """
    Nombre seguro para el sistema de archivos.

    Se normaliza para quitar acentos y se limita la longitud: los nombres de
    adjunto reales traen tildes, espacios, parentesis y a veces 200 caracteres.
    """
    base = unicodedata.normalize("NFKD", nombre or "")
    base = "".join(c for c in base if not unicodedata.combining(c))
    limpio = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in base)
    while "--" in limpio:
        limpio = limpio.replace("--", "-")
    limpio = limpio.strip("-._").lower()
    return (limpio[:80] or "documento")
