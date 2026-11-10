"""
Salidas a la medida (plan, sección 5d): los formatos que faltaban y la
biblioteca de plantillas de documentos reutilizables entre flujos.

- Formatos: PowerPoint (.pptx, con python-pptx, MIT), HTML, XML, JSON, CSV y ZIP
  (biblioteca estándar). Word, PDF y Excel siguen en `documentos.py`.
- Plantillas de documentos: Word, Excel, PowerPoint y correo HTML con
  {{variables}}, logos y estilos, guardadas en la plataforma
  (`<estado>/documentos/<id>/`). Se llenan con los datos de un flujo, con datos
  dados o con lo que escribe la IA.

Sintaxis de una plantilla (igual en los cuatro formatos):

    {{variable}} {{cliente.nombre}}     un valor (en HTML se escapa; {{{x}}} va sin escapar)
    {{#items}}                          en una fila de tabla (Word, Excel, PowerPoint) o en un párrafo:
                                        la fila (o el párrafo) se repite por cada elemento de «items»,
                                        con {{item.campo}} y {{indice}}; en HTML: {{#items}} … {{/items}}
    {{imagen:logo}} {{imagen:logo 4cm}}  una imagen: un recurso de la plantilla (su logo) o una variable
                                        con un archivo de la ejecución (p. ej. {{imagen:imagenes.0}})
    {{contenido}}                       dónde va lo que redacta la IA (desde_ia modo completo)

Las variables de `entorno` nunca se leen desde una plantilla de la biblioteca.

Como programa (lo usa el servidor):
    python salidas.py analizar RUTA FORMATO                 → JSON {variables, repeticiones, imagenes, contenido, texto, ...}
    python salidas.py crear FORMATO SALIDA  (stdin {titulo, texto})          → crea un .docx/.pptx/.xlsx/.html desde texto
    python salidas.py llenar RUTA FORMATO SALIDA (stdin {datos, recursos})   → JSON {faltantes, usadas}
"""
from __future__ import annotations

import base64
import copy
import csv
import datetime
import hashlib
import html as m_html
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

FORMATOS_PLANTILLA = ("docx", "xlsx", "pptx", "html")
MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "html": "text/html; charset=utf-8", "xml": "application/xml", "json": "application/json", "csv": "text/csv",
    "txt": "text/plain; charset=utf-8", "md": "text/markdown; charset=utf-8", "zip": "application/zip", "pdf": "application/pdf",
}
ID_PLANTILLA = re.compile(r"^[a-z0-9][a-z0-9-]{1,59}$")
NOMBRE_RECURSO = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MAX_DIAPOSITIVAS = 60
MAX_REPETICIONES = 2000
MAX_ZIP = 50
PROHIBIDAS = ("entorno",)

# Azules de Linktic para lo que se arma desde cero (títulos, acentos, encabezados de tabla).
AZUL = (0x0B, 0x4F, 0x8A)
AZUL_CLARO = (0xE8, 0xF1, 0xFB)
GRIS = (0x33, 0x41, 0x55)

MARCA = re.compile(r"\{\{\{\s*([^{}]*?)\s*\}\}\}|\{\{\s*([^{}]*?)\s*\}\}")
REPETIR = re.compile(r"\{\{\s*#\s*([A-Za-z_][A-Za-z0-9_.\[\]=-]*)\s*\}\}")
CIERRE = re.compile(r"\{\{\s*/\s*([A-Za-z_][A-Za-z0-9_.\[\]=-]*)\s*\}\}")
IMAGEN = re.compile(r"\{\{\s*imagen:\s*([A-Za-z0-9_.\[\]=-]+)(?:\s+(\d+(?:[.,]\d+)?)\s*cm)?\s*\}\}")
CONTENIDO = re.compile(r"\{\{\s*contenido\s*\}\}")
SECCION = re.compile(r"\{\{\s*#\s*([A-Za-z_][A-Za-z0-9_.\[\]=-]*)\s*\}\}(.*?)\{\{\s*/\s*\1\s*\}\}", re.S)


class ErrorDePlantilla(Exception):
    """Algo de la plantilla o de los datos que impide armar el documento (lo ve la persona)."""


# =====================================================================
#   el contexto con que se llena una plantilla
# =====================================================================

def _a_texto(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "sí" if v else "no"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _navegar(base: Any, ref: str) -> Any:
    """item.campo, item.0, item[0] dentro de un valor (para los ámbitos de una repetición)."""
    v = base
    for parte in [p for p in re.split(r"[.\[\]]", ref) if p != ""]:
        if isinstance(v, dict):
            v = v.get(parte)
        elif isinstance(v, list) and parte.isdigit():
            i = int(parte)
            v = v[i] if i < len(v) else None
        else:
            return None
    return v


class Contexto:
    """Con qué se llena una plantilla: un resolvedor de referencias (las variables del flujo y los datos), las
    imágenes, el contenido de la IA y los ámbitos de cada repetición. Anota lo usado y lo que quedó vacío."""

    def __init__(self, valor: Callable[[str], Any], imagen: Optional[Callable[[str], Optional[bytes]]] = None,
                 a_texto: Callable[[Any], str] = _a_texto, contenido: Any = None, prohibidas: Sequence[str] = PROHIBIDAS) -> None:
        self._valor, self._imagen, self.a_texto, self.contenido = valor, imagen, a_texto, contenido
        self.prohibidas = tuple(prohibidas)
        self.ambitos: List[Dict[str, Any]] = []
        self.usadas: Set[str] = set()
        self.faltantes: Set[str] = set()
        self.bloqueadas: Set[str] = set()
        self.sin_imagen: Set[str] = set()
        self.contenido_puesto = False

    def valor(self, ref: str) -> Any:
        ref = ref.strip()
        raiz = re.split(r"[.\[]", ref, 1)[0]
        if raiz in self.prohibidas:
            self.bloqueadas.add(ref)
            return None
        for amb in reversed(self.ambitos):
            if raiz in amb:
                return _navegar(amb, ref)
        try:
            return self._valor(ref)
        except Exception:
            return None

    def lista(self, ref: str) -> List[Any]:
        v = self.valor(ref)
        if isinstance(v, str) and v.strip().startswith("["):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        if v in (None, "", []):
            self.faltantes.add(f"#{ref}")
            return []
        return (v if isinstance(v, list) else [v])[:MAX_REPETICIONES]

    def imagen(self, ref: str) -> Optional[bytes]:
        datos = None
        if self._imagen:
            try:
                datos = self._imagen(ref)
            except Exception:
                datos = None
        if not datos:
            self.sin_imagen.add(ref)
            return None
        return _png_o_jpeg(datos)

    class _Ambito:
        def __init__(self, ctx: "Contexto", amb: Dict[str, Any]) -> None:
            self.ctx, self.amb = ctx, amb

        def __enter__(self) -> None:
            self.ctx.ambitos.append(self.amb)

        def __exit__(self, *_: Any) -> None:
            self.ctx.ambitos.pop()

    def con(self, **amb: Any) -> "Contexto._Ambito":
        return Contexto._Ambito(self, amb)

    def texto(self, plantilla: str, escapar: Optional[Callable[[str], str]] = None) -> str:
        """Una sola pasada: lo que se pone no se vuelve a leer como plantilla (un valor con «{{…}}» queda tal cual)."""
        def poner(m: "re.Match[str]") -> str:
            crudo = m.group(1) is not None
            ref = (m.group(1) if crudo else m.group(2)).strip()
            if not ref or ref[0] in "#/" or ref.startswith("imagen:") or ref == "contenido":
                return ""
            v = self.valor(ref)
            self.usadas.add(ref)
            if v is None or v == "":
                self.faltantes.add(ref)
            t = self.a_texto(v)
            return t if (crudo or not escapar) else escapar(t)
        return MARCA.sub(poner, plantilla or "")


def _png_o_jpeg(datos: bytes) -> bytes:
    """Word y PowerPoint aceptan PNG y JPEG: lo demás (WebP, GIF animado, HEIC…) se pasa a PNG."""
    if datos[:8] == b"\x89PNG\r\n\x1a\n" or datos[:3] == b"\xff\xd8\xff":
        return datos
    from PIL import Image  # HPND
    s = io.BytesIO()
    Image.open(io.BytesIO(datos)).convert("RGBA").save(s, format="PNG")
    return s.getvalue()


def _medidas_imagen(datos: bytes) -> Tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(datos)) as im:
        return im.size


# =====================================================================
#   variables de una plantilla
# =====================================================================

def _refs(texto: str) -> Tuple[List[str], List[str], List[str], bool]:
    """(variables, repeticiones, imágenes, ¿tiene {{contenido}}?) de un texto."""
    variables, reps, imgs = [], [], []
    for m in IMAGEN.finditer(texto):
        imgs.append(m.group(1))
    for m in REPETIR.finditer(texto):
        reps.append(m.group(1))
    for m in MARCA.finditer(texto):
        ref = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
        if ref and ref[0] not in "#/" and not ref.startswith("imagen:") and ref != "contenido":
            variables.append(ref)
    return variables, reps, imgs, bool(CONTENIDO.search(texto))


class _Analisis:
    def __init__(self) -> None:
        self.variables: List[str] = []
        self.repeticiones: Dict[str, List[str]] = {}
        self.imagenes: List[str] = []
        self.contenido = False
        self.texto: List[str] = []

    def unidad(self, texto: str) -> None:
        """Una fila o un párrafo: si repite una lista, sus {{item.x}} son campos de esa lista."""
        if not texto.strip():
            return
        self.texto.append(texto)
        variables, reps, imgs, cont = _refs(texto)
        self.imagenes += imgs
        self.contenido = self.contenido or cont
        if reps:
            for r in reps:
                campos = self.repeticiones.setdefault(r, [])
                campos += [v[5:] for v in variables if v.startswith("item.") and v[5:] not in campos]
            variables = [v for v in variables if not (v.startswith("item.") or v in ("item", "indice"))]
        self.variables += variables

    def resultado(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        vistas: List[str] = []
        for v in self.variables:
            if v not in vistas and not v.startswith(PROHIBIDAS):
                vistas.append(v)
        texto = "\n".join(self.texto)
        return {"variables": vistas, "repeticiones": self.repeticiones, "imagenes": list(dict.fromkeys(self.imagenes)),
                "contenido": self.contenido, "texto": texto[:6000], "prohibidas": sorted({v for v in self.variables if v.startswith(PROHIBIDAS)}),
                **(extra or {})}


def analizar(datos: bytes, formato: str) -> Dict[str, Any]:
    """Qué pide una plantilla: sus variables, sus listas que se repiten (con sus campos), sus imágenes y si tiene {{contenido}}."""
    a = _Analisis()
    extra: Dict[str, Any] = {}
    if formato == "docx":
        import docx
        d = docx.Document(io.BytesIO(datos))
        for t in _tablas_docx(d):
            for fila in t.rows:
                a.unidad(" | ".join(c.text for c in fila.cells))
        for p in _parrafos_docx(d, con_tablas=False):
            a.unidad(p.text)
    elif formato == "xlsx":
        import openpyxl
        libro = openpyxl.load_workbook(io.BytesIO(datos))
        extra["hojas"] = libro.sheetnames
        for ws in libro.worksheets:
            for fila in ws.iter_rows():
                a.unidad(" | ".join(str(c.value) for c in fila if isinstance(c.value, str)))
    elif formato == "pptx":
        from pptx import Presentation
        prs = Presentation(io.BytesIO(datos))
        extra["diapositivas"] = len(prs.slides)
        for slide in prs.slides:
            for forma in _formas(slide.shapes):
                if forma.has_table:
                    for fila in forma.table.rows:
                        a.unidad(" | ".join(c.text for c in fila.cells))
                elif forma.has_text_frame:
                    for p in forma.text_frame.paragraphs:
                        a.unidad("".join(r.text for r in p.runs))
            if slide.has_notes_slide:
                a.unidad(slide.notes_slide.notes_text_frame.text)
    elif formato == "html":
        texto = _decodificar(datos)
        for m in SECCION.finditer(texto):
            variables, _, imgs, _ = _refs(m.group(2))
            campos = a.repeticiones.setdefault(m.group(1), [])
            campos += [v[5:] for v in variables if v.startswith("item.") and v[5:] not in campos]
        sin_secciones = SECCION.sub(lambda m: re.sub(r"\{\{\{?\s*item\b[^}]*\}\}\}?|\{\{\s*indice\s*\}\}", "", m.group(2)), texto)
        variables, _, imgs, cont = _refs(sin_secciones)
        a.variables += variables
        a.imagenes += imgs
        a.contenido = cont
        a.texto.append(_texto_de_html(texto))
        titulo = re.search(r"<title>(.*?)</title>", texto, re.S | re.I)
        extra["titulo"] = m_html.unescape(titulo.group(1)).strip() if titulo else ""
    else:
        raise ErrorDePlantilla(f"formato de plantilla «{formato}» (válidos: {', '.join(FORMATOS_PLANTILLA)})")
    return a.resultado(extra)


def _decodificar(datos: bytes) -> str:
    for cod in ("utf-8-sig", "latin-1"):
        try:
            return datos.decode(cod)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", "replace")


def _texto_de_html(texto: str) -> str:
    t = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", texto)
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|h[1-6]|li|tr|table)>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = m_html.unescape(t)
    return "\n".join(x.strip() for x in t.splitlines() if x.strip())


def formato_de(nombre: str, datos: bytes = b"") -> str:
    """docx, xlsx, pptx o html según el nombre (y el contenido, si viene)."""
    ext = nombre.lower().rsplit(".", 1)[-1] if "." in nombre else ""
    if ext == "htm":
        ext = "html"
    if datos[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(datos)) as z:
                nombres = set(z.namelist())
            if "word/document.xml" in nombres:
                return "docx"
            if any(n.startswith("xl/") for n in nombres):
                return "xlsx"
            if any(n.startswith("ppt/") for n in nombres):
                return "pptx"
        except zipfile.BadZipFile:
            pass
    return ext


# =====================================================================
#   Word
# =====================================================================

def _tablas_docx(d: Any) -> Iterable[Any]:
    """Todas las tablas: del cuerpo (también las que van dentro de una celda) y de encabezados y pies."""
    def de(contenedor: Any) -> Iterable[Any]:
        for t in contenedor.tables:
            yield t
            for fila in t.rows:
                for celda in fila.cells:
                    yield from de(celda)
    yield from de(d)
    for s in d.sections:
        for parte in (s.header, s.footer):
            yield from de(parte)


def _parrafos_docx(d: Any, con_tablas: bool = True) -> Iterable[Any]:
    for p in d.paragraphs:
        yield p
    if con_tablas:
        for t in _tablas_docx(d):
            for fila in t.rows:
                for celda in fila.cells:
                    yield from celda.paragraphs
    for s in d.sections:
        for parte in (s.header, s.footer):
            yield from parte.paragraphs


def _texto_parrafo(p: Any) -> str:
    return "".join(r.text for r in p.runs)


def _poner_texto(p: Any, nuevo: str) -> None:
    """El texto nuevo queda en el primer tramo (con su formato) y los demás se vacían."""
    if p.runs:
        p.runs[0].text = nuevo
        for r in p.runs[1:]:
            r.text = ""
    elif nuevo:
        p.add_run(nuevo)


def _llenar_parrafo_docx(p: Any, ctx: Contexto, hechos: Dict[int, Any]) -> None:
    if id(p._p) in hechos:
        return
    hechos[id(p._p)] = p._p  # el elemento queda vivo: su id() no se reutiliza
    texto = _texto_parrafo(p)
    if "{{" not in texto:
        return
    if IMAGEN.search(texto):
        import docx
        partes = IMAGEN.split(texto)  # [texto, ref, ancho, texto, ref, ancho, …]
        _poner_texto(p, "")
        primero = True
        for i in range(0, len(partes), 3):
            segmento = ctx.texto(partes[i])
            if segmento:
                if primero and p.runs:
                    p.runs[0].text = segmento
                else:
                    p.add_run(segmento)
            primero = False
            if i + 1 < len(partes):
                datos = ctx.imagen(partes[i + 1])
                if datos:
                    ancho = float(partes[i + 2].replace(",", ".")) if partes[i + 2] else 4.0
                    p.add_run().add_picture(io.BytesIO(datos), width=docx.shared.Cm(max(0.5, min(25.0, ancho))))
        return
    _poner_texto(p, ctx.texto(texto))


def _repetir_filas_docx(tabla: Any, ctx: Contexto, hechos: Dict[int, Any]) -> None:
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
    for fila in list(tabla.rows):
        tr = fila._tr
        texto = " ".join(c.text for c in fila.cells)
        m = REPETIR.search(texto)
        if not m:
            continue
        items = ctx.lista(m.group(1))
        for i, item in enumerate(items, 1):
            nuevo = copy.deepcopy(tr)
            tr.addprevious(nuevo)
            with ctx.con(item=item, indice=i):
                for p_el in nuevo.iter(qn("w:p")):
                    p = Paragraph(p_el, tabla)
                    t = _texto_parrafo(p)
                    if REPETIR.search(t):
                        t = REPETIR.sub("", t)
                        _poner_texto(p, t)
                    _llenar_parrafo_docx(p, ctx, hechos)
        tr.getparent().remove(tr)


def _repetir_parrafos_docx(d: Any, ctx: Contexto, hechos: Dict[int, Any]) -> None:
    """Un párrafo con {{#lista}} (fuera de tablas) se repite por cada elemento: viñetas, cláusulas, firmas…"""
    from docx.text.paragraph import Paragraph
    contenedores = [d] + [parte for s in d.sections for parte in (s.header, s.footer)]
    for cont in contenedores:
        for p in list(cont.paragraphs):
            texto = _texto_parrafo(p)
            m = REPETIR.search(texto)
            if not m:
                continue
            base = REPETIR.sub("", texto)
            for i, item in enumerate(ctx.lista(m.group(1)), 1):
                nuevo_el = copy.deepcopy(p._p)
                p._p.addprevious(nuevo_el)
                nuevo = Paragraph(nuevo_el, p._parent)
                _poner_texto(nuevo, base)
                with ctx.con(item=item, indice=i):
                    _llenar_parrafo_docx(nuevo, ctx, hechos)
            p._p.getparent().remove(p._p)


def _estilo(d: Any, *nombres: str) -> Optional[str]:
    for n in nombres:
        try:
            d.styles[n]
            return n
        except KeyError:
            continue
    return None


def _bloques_en_docx(d: Any, bloques: List[Tuple[str, Any]], antes_de: Any = None) -> None:
    """Escribe bloques (título, subtítulo, párrafo, viñeta, tabla) en el documento, en el lugar de `antes_de` o al final."""
    import docx
    from docx.shared import RGBColor

    def colocar(el: Any) -> None:
        if antes_de is not None:
            antes_de.addprevious(el)

    for tipo, v in bloques:
        if tipo in ("titulo", "subtitulo"):
            estilo = _estilo(d, "Heading 1" if tipo == "titulo" else "Heading 2", "Título 1" if tipo == "titulo" else "Título 2", "Title")
            p = d.add_paragraph(str(v), style=estilo) if estilo else d.add_paragraph()
            if not estilo:
                r = p.add_run(str(v))
                r.bold = True
                r.font.size = docx.shared.Pt(16 if tipo == "titulo" else 13)
                r.font.color.rgb = RGBColor(*AZUL)
            colocar(p._p)
        elif tipo == "vineta":
            estilo = _estilo(d, "List Bullet", "Lista con viñetas")
            p = d.add_paragraph(str(v) if estilo else f"• {v}", style=estilo)
            colocar(p._p)
        elif tipo == "tabla":
            cols = max((len(f) for f in v), default=0)
            if not cols:
                continue
            t = d.add_table(rows=0, cols=cols)
            estilo = _estilo(d, "Table Grid", "Tabla con cuadrícula")
            if estilo:
                t.style = estilo
            for i, fila in enumerate(v):
                celdas = t.add_row().cells
                for k in range(cols):
                    celdas[k].text = str(fila[k]) if k < len(fila) and fila[k] is not None else ""
                    if i == 0:
                        for r in celdas[k].paragraphs[0].runs:
                            r.bold = True
            colocar(t._tbl)
            colocar(d.add_paragraph()._p)
        elif tipo == "imagen":
            jpeg = v[0] if isinstance(v, tuple) else v
            d.add_picture(io.BytesIO(jpeg), width=docx.shared.Cm(15))
            colocar(d.paragraphs[-1]._p)
        else:
            colocar(d.add_paragraph(str(v))._p)


def llenar_docx(datos: bytes, ctx: Contexto) -> bytes:
    """Una plantilla de Word llena: variables en el cuerpo, tablas, encabezados y pies (con su formato), filas y párrafos
    que se repiten, imágenes y, con ctx.contenido (bloques), lo que redactó la IA en {{contenido}} o al final."""
    import docx
    d = docx.Document(io.BytesIO(datos))
    hechos: Dict[int, Any] = {}
    guardados: List[Any] = []  # que los elementos procesados sigan vivos (id() estable)
    for t in list(_tablas_docx(d)):
        guardados.append(t._tbl)
        _repetir_filas_docx(t, ctx, hechos)
    _repetir_parrafos_docx(d, ctx, hechos)
    for p in list(_parrafos_docx(d)):
        guardados.append(p._p)
        if CONTENIDO.fullmatch(_texto_parrafo(p).strip() or "-") and ctx.contenido is not None:
            _bloques_en_docx(d, ctx.contenido, p._p)
            p._p.getparent().remove(p._p)
            ctx.contenido_puesto = True
            continue
        _llenar_parrafo_docx(p, ctx, hechos)
    if ctx.contenido is not None and not ctx.contenido_puesto:
        _bloques_en_docx(d, ctx.contenido)
        ctx.contenido_puesto = True
    s = io.BytesIO()
    d.save(s)
    return s.getvalue()


def docx_de_texto(texto: str, titulo: str = "") -> bytes:
    import documentos as m_doc
    return m_doc.docx_de_bloques(m_doc.bloques_de_texto(texto), titulo)


# =====================================================================
#   Excel
# =====================================================================

def _valor_celda(plantilla: str, ctx: Contexto) -> Any:
    """Una celda que es solo {{x}} conserva el tipo del valor (número, fecha); si no, texto."""
    m = MARCA.fullmatch(plantilla.strip())
    if m and m.group(2) is not None:
        ref = m.group(2).strip()
        if ref and ref[0] not in "#/" and not ref.startswith("imagen:") and ref != "contenido":
            v = ctx.valor(ref)
            ctx.usadas.add(ref)
            if v is None or v == "":
                ctx.faltantes.add(ref)
                return ""
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
            if isinstance(v, (datetime.date, datetime.datetime)):
                return v
            import documentos as m_doc
            return m_doc._valor_celda(ctx.a_texto(v))
    return ctx.texto(plantilla)


def _imagen_en_celda(ws: Any, celda: Any, ctx: Contexto) -> None:
    from openpyxl.drawing.image import Image as XLImage
    texto = str(celda.value)
    m = IMAGEN.search(texto)
    celda.value = ctx.texto(IMAGEN.sub("", texto)) or None
    if not m:
        return
    datos = ctx.imagen(m.group(1))
    if not datos:
        return
    img = XLImage(io.BytesIO(datos))
    ancho_cm = float(m.group(2).replace(",", ".")) if m.group(2) else 4.0
    w, h = _medidas_imagen(datos)
    img.width = int(ancho_cm / 2.54 * 96)
    img.height = int(img.width * h / max(1, w))
    ws.add_image(img, celda.coordinate)


def _escribir_hoja(ws: Any, fila0: int, col0: int, columnas: List[str], filas: List[List[Any]]) -> None:
    import documentos as m_doc
    from openpyxl.styles import Font, PatternFill
    if columnas:
        for k, c in enumerate(columnas):
            celda = ws.cell(row=fila0, column=col0 + k, value=c)
            celda.font = Font(bold=True, color="FFFFFF")
            celda.fill = PatternFill("solid", fgColor="%02X%02X%02X" % AZUL)
        fila0 += 1
    for i, f in enumerate(filas):
        for k, x in enumerate(f):
            ws.cell(row=fila0 + i, column=col0 + k, value=m_doc._valor_celda(x))


def llenar_xlsx(datos: bytes, ctx: Contexto) -> bytes:
    """Una plantilla de Excel llena (con sus estilos, fórmulas y logos): celdas con {{x}} (los números quedan números), filas
    con {{#lista}} que se repiten hacia abajo, {{imagen:logo}} en una celda y, con ctx.contenido (hojas de la IA), la
    primera hoja desde la celda {{contenido}} y las demás como pestañas nuevas."""
    import openpyxl
    libro = openpyxl.load_workbook(io.BytesIO(datos))
    marca_contenido = None
    for ws in libro.worksheets:
        # 1) filas que se repiten, de abajo hacia arriba (insertar no corre las de arriba)
        repetidas = []
        for fila in ws.iter_rows():
            textos = " ".join(str(c.value) for c in fila if isinstance(c.value, str))
            m = REPETIR.search(textos)
            if m:
                repetidas.append((fila[0].row, m.group(1)))
        hechas: Set[Tuple[int, int]] = set()
        for r, nombre in sorted(repetidas, reverse=True):
            plantilla = [(c.column, c.value, copy.copy(c._style)) for c in ws[r]]
            alto = ws.row_dimensions[r].height
            items = ctx.lista(nombre)
            if not items:
                ws.delete_rows(r)
                continue
            if len(items) > 1:
                ws.insert_rows(r + 1, len(items) - 1)
            for i, item in enumerate(items):
                fila = r + i
                if alto is not None:
                    ws.row_dimensions[fila].height = alto
                with ctx.con(item=item, indice=i + 1):
                    for col, valor, estilo in plantilla:
                        celda = ws.cell(row=fila, column=col)
                        celda._style = copy.copy(estilo)
                        if isinstance(valor, str):
                            celda.value = _valor_celda(REPETIR.sub("", valor), ctx) if "{{" in valor else valor
                        else:
                            celda.value = valor
                        hechas.add((fila, col))
        # 2) el resto de las celdas
        for fila in ws.iter_rows():
            for celda in fila:
                if (celda.row, celda.column) in hechas or not isinstance(celda.value, str) or "{{" not in celda.value:
                    continue
                if CONTENIDO.fullmatch(celda.value.strip()) and marca_contenido is None:
                    marca_contenido = (ws, celda.row, celda.column)
                    celda.value = None
                elif IMAGEN.search(celda.value):
                    _imagen_en_celda(ws, celda, ctx)
                else:
                    celda.value = _valor_celda(celda.value, ctx)
    hojas = ctx.contenido if isinstance(ctx.contenido, list) else []
    if hojas:
        ctx.contenido_puesto = True
        resto = hojas
        if marca_contenido:
            ws, r, c = marca_contenido
            _escribir_hoja(ws, r, c, hojas[0]["columnas"], hojas[0]["filas"])
            resto = hojas[1:]
        for h in resto:
            nombre = h["nombre"]
            base, k = nombre, 2
            while nombre in libro.sheetnames:
                nombre = f"{base[:28]} {k}"
                k += 1
            _escribir_hoja(libro.create_sheet(nombre), 1, 1, h["columnas"], h["filas"])
    s = io.BytesIO()
    libro.save(s)
    return s.getvalue()


def xlsx_de_texto(texto: str, titulo: str = "") -> bytes:
    """Una hoja simple desde texto: las líneas con «|» son filas (la primera, el encabezado); lo demás va en la columna A."""
    import openpyxl
    from openpyxl.styles import Font
    libro = openpyxl.Workbook()
    ws = libro.active
    ws.title = (re.sub(r"[\[\]:*?/\\]", " ", titulo).strip() or "Hoja 1")[:31]
    primera = True
    for linea in (texto or "").splitlines():
        s = linea.strip()
        if not s:
            continue
        if "|" in s:
            celdas = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in celdas if c):
                continue
            ws.append(celdas)
            if primera:
                for celda in ws[ws.max_row]:
                    celda.font = Font(bold=True)
                primera = False
        else:
            ws.append([s.lstrip("# ")])
    s = io.BytesIO()
    libro.save(s)
    return s.getvalue()


# =====================================================================
#   PowerPoint
# =====================================================================

ESQUEMA_PRESENTACION: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "subtitulo": {"type": "string"},
        "diapositivas": {"type": "array", "items": {"type": "object", "properties": {
            "titulo": {"type": "string"},
            "puntos": {"type": "array", "items": {"type": "string"}},
            "notas": {"type": "string"},
            "tabla": {"type": "object", "properties": {
                "columnas": {"type": "array", "items": {"type": "string"}},
                "filas": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}},
        }}},
    },
    "required": ["titulo", "diapositivas"],
}


def presentacion_limpia(p: Any) -> Dict[str, Any]:
    """Lo que devolvió la IA (o un dict a mano), revisado: título, subtítulo y diapositivas {titulo, puntos, notas, tabla, imagen}."""
    if not isinstance(p, dict):
        return {"titulo": "", "subtitulo": "", "diapositivas": []}
    dias = []
    for d in (p.get("diapositivas") or p.get("slides") or [])[:MAX_DIAPOSITIVAS]:
        if isinstance(d, str):
            d = {"titulo": d}
        if not isinstance(d, dict):
            continue
        puntos = [str(x).strip() for x in (d.get("puntos") or d.get("lista") or []) if str(x or "").strip()][:14]
        puntos += [str(x).strip() for x in (d.get("parrafos") or []) if str(x or "").strip()][:6]
        t = d.get("tabla") if isinstance(d.get("tabla"), dict) else None
        tabla = None
        if t and (t.get("columnas") or t.get("filas")):
            tabla = {"columnas": [str(c) for c in t.get("columnas") or []][:10],
                     "filas": [[_a_texto(x) for x in (f if isinstance(f, list) else [f])][:10] for f in (t.get("filas") or [])[:25]]}
        dias.append({"titulo": str(d.get("titulo") or "").strip(), "puntos": puntos, "notas": str(d.get("notas") or "").strip(),
                     "tabla": tabla, "imagen": d.get("imagen")})
    return {"titulo": str(p.get("titulo") or "").strip(), "subtitulo": str(p.get("subtitulo") or "").strip(), "diapositivas": dias}


def presentacion_de_bloques(bloques: List[Tuple[str, Any]], titulo: str = "") -> Dict[str, Any]:
    """Bloques de texto (# título, ## subtítulo, párrafos, viñetas, tablas) como diapositivas: el primer «# » es la portada
    y cada «# » o «## » siguiente abre una diapositiva."""
    pres: Dict[str, Any] = {"titulo": titulo, "subtitulo": "", "diapositivas": []}
    actual: Optional[Dict[str, Any]] = None
    for tipo, v in bloques:
        if tipo == "parrafo" and CONTENIDO.fullmatch(str(v).strip()):
            # el lugar de lo que redacte la IA: una diapositiva propia (se cambia por las de la IA)
            pres["diapositivas"].append({"titulo": "{{contenido}}", "puntos": [], "notas": "", "tabla": None})
            actual = None
            continue
        if tipo == "titulo" and not pres["titulo"] and not pres["diapositivas"] and actual is None:
            pres["titulo"] = str(v)
            continue
        if tipo in ("titulo", "subtitulo"):
            actual = {"titulo": str(v), "puntos": [], "notas": "", "tabla": None}
            pres["diapositivas"].append(actual)
            continue
        if actual is None:
            if tipo == "parrafo" and not pres["subtitulo"] and not pres["diapositivas"]:
                pres["subtitulo"] = str(v)
                continue
            actual = {"titulo": "", "puntos": [], "notas": "", "tabla": None}
            pres["diapositivas"].append(actual)
        if tipo == "tabla":
            filas = [list(map(str, f)) for f in v]
            if actual["tabla"] is not None:
                actual = {"titulo": actual["titulo"], "puntos": [], "notas": "", "tabla": None}
                pres["diapositivas"].append(actual)
            actual["tabla"] = {"columnas": filas[0] if filas else [], "filas": filas[1:]}
        elif tipo in ("parrafo", "vineta"):
            actual["puntos"].append(str(v))
    return presentacion_limpia(pres)


def _rgb(c: Tuple[int, int, int]) -> Any:
    from pptx.dml.color import RGBColor
    return RGBColor(*c)


def _vineta(p: Any) -> None:
    """Viñeta de verdad (no un «•» escrito), con sangría francesa."""
    from pptx.oxml.ns import qn
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", "342900")
    pPr.set("indent", "-342900")
    for v in pPr.findall(qn("a:buNone")):
        pPr.remove(v)
    bu = pPr.makeelement(qn("a:buChar"), {"char": "•"})
    pPr.append(bu)


def _tabla_pptx(slide: Any, tabla: Dict[str, Any], izq: int, arriba: int, ancho: int, alto: int) -> None:
    from pptx.util import Pt
    filas = ([tabla["columnas"]] if tabla.get("columnas") else []) + tabla.get("filas", [])
    if not filas:
        return
    cols = max(len(f) for f in filas)
    forma = slide.shapes.add_table(len(filas), cols, izq, arriba, ancho, min(alto, 370000 * len(filas)))
    t = forma.table
    for i, f in enumerate(filas):
        for k in range(cols):
            celda = t.cell(i, k)
            celda.text = str(f[k]) if k < len(f) else ""
            for p in celda.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(12 if len(filas) > 8 else 14)
                    if i == 0 and tabla.get("columnas"):
                        r.font.bold = True


def _caja(slide: Any, izq: int, arriba: int, ancho: int, alto: int) -> Any:
    tf = slide.shapes.add_textbox(izq, arriba, ancho, alto).text_frame
    tf.word_wrap = True
    return tf


def pptx_de_presentacion(pres: Dict[str, Any], imagen: Optional[Callable[[Any], Optional[bytes]]] = None,
                         marca: Optional[Dict[str, Any]] = None) -> bytes:
    """Una presentación 16:9 desde cero: portada con título y subtítulo, y una diapositiva por punto (título, viñetas,
    tabla, imagen a la derecha y notas del orador), con los azules de Linktic sobre blanco. Con `marca` (marca.cargar)
    lleva sus colores y fuente, y el logo, los banners y la marca de agua sin tapar el título ni el contenido."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Emu, Inches, Pt
    import marca as m_marca

    pres = presentacion_limpia(pres)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    W, H = prs.slide_width, prs.slide_height
    blanco = prs.slide_layouts[6]
    margen = Inches(0.7)
    marca = marca or {}
    colores = marca.get("colores") or {}
    hexa = lambda k, defecto: tuple(int(colores[k][i:i + 2], 16) for i in (0, 2, 4)) if colores.get(k) else defecto  # noqa: E731
    c_titulo, c_acento, c_texto = hexa("titulos", AZUL), hexa("acento", hexa("titulos", AZUL)), hexa("texto", GRIS)
    fuente = marca.get("fuente")
    g = m_marca.diseno_pptx(int(W), int(H), marca) if marca else {"arriba": 0, "abajo": 0, "titulo_izq": 0, "titulo_der": 0}
    dy = int(g["arriba"])  # lo que ocupan arriba el banner (y el logo si va al centro)
    t_izq = max(int(margen), int(g["titulo_izq"]) + int(Inches(0.2)) if g["titulo_izq"] else 0)
    t_ancho = int(W) - t_izq - max(int(margen), int(g["titulo_der"]) + int(Inches(0.2)) if g["titulo_der"] else 0)

    def letra(r: Any) -> None:
        if fuente:
            r.font.name = fuente

    def barra(slide: Any, alto: int = Inches(0.12)) -> None:
        if g.get("banner_arriba"):
            return  # el banner hace de barra
        b = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, W, alto)
        b.fill.solid()
        b.fill.fore_color.rgb = _rgb(c_acento)
        b.line.fill.background()

    # portada
    s = prs.slides.add_slide(blanco)
    fondo = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, W, H)
    fondo.fill.solid()
    fondo.fill.fore_color.rgb = _rgb(AZUL_CLARO)
    fondo.line.fill.background()
    barra(s, Inches(0.25))
    tf = _caja(s, margen, Inches(2.4) + dy // 2, W - 2 * margen, Inches(1.6))
    tf.text = pres["titulo"] or "Presentación"
    r = tf.paragraphs[0].runs[0]
    r.font.size, r.font.bold, r.font.color.rgb = Pt(40), True, _rgb(c_titulo)
    letra(r)
    if pres["subtitulo"]:
        tf2 = _caja(s, margen, Inches(4.1) + dy // 2, W - 2 * margen, Inches(1.2))
        tf2.text = pres["subtitulo"]
        r = tf2.paragraphs[0].runs[0]
        r.font.size, r.font.color.rgb = Pt(22), _rgb(c_texto)
        letra(r)

    for d in pres["diapositivas"]:
        s = prs.slides.add_slide(blanco)
        barra(s)
        tf = _caja(s, t_izq, Inches(0.4) + dy, t_ancho, Inches(1.0))
        tf.text = d["titulo"] or pres["titulo"]
        if tf.paragraphs[0].runs:
            r = tf.paragraphs[0].runs[0]
            r.font.size, r.font.bold, r.font.color.rgb = Pt(30), True, _rgb(c_titulo)
            letra(r)
        linea = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, t_izq, Inches(1.35) + dy, Inches(1.2), Inches(0.06))
        linea.fill.solid()
        linea.fill.fore_color.rgb = _rgb(c_acento)
        linea.line.fill.background()
        arriba, alto = Inches(1.7) + dy, H - Inches(2.3) - dy - int(g["abajo"])
        datos_img = imagen(d["imagen"]) if (imagen and d.get("imagen")) else None
        ancho_texto = (W - 2 * margen) * (0.58 if datos_img else 1.0)
        if d["puntos"]:
            n = len(d["puntos"])
            tam = 22 if n <= 4 else 18 if n <= 7 else 15
            caja = _caja(s, margen, arriba, int(ancho_texto), int(alto if not d["tabla"] else alto * 0.42))
            for i, punto in enumerate(d["puntos"]):
                p = caja.paragraphs[0] if i == 0 else caja.add_paragraph()
                p.text = punto
                _vineta(p)
                p.space_after = Pt(8)
                for r in p.runs:
                    r.font.size, r.font.color.rgb = Pt(tam), _rgb(c_texto)
                    letra(r)
            if d["tabla"]:
                arriba = arriba + int(alto * 0.45)
                alto = int(alto * 0.55)
        if d["tabla"]:
            _tabla_pptx(s, d["tabla"], margen, arriba, int(ancho_texto), alto)
        if datos_img:
            datos_img = _png_o_jpeg(datos_img)
            w, h = _medidas_imagen(datos_img)
            caja_w, caja_h = int((W - 2 * margen) * 0.38), int(alto)
            escala = min(caja_w / max(1, w), caja_h / max(1, h))
            iw, ih = int(w * escala), int(h * escala)
            s.shapes.add_picture(io.BytesIO(datos_img), W - margen - iw, Inches(1.7) + dy + (caja_h - ih) // 2, Emu(iw), Emu(ih))
        if d["notas"]:
            s.notes_slide.notes_text_frame.text = d["notas"]
    if marca:
        # los colores de las tablas (y la fuente en ellas); las piezas de imagen, en cada diapositiva
        m_marca.colores_pptx(prs, {k: v for k, v in marca.items() if k in ("fuente",)} | ({"colores": {k: v for k, v in colores.items() if k.startswith("tabla")}} if colores else {}))
        m_marca.piezas_pptx(prs, marca)
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def _formas(shapes: Any) -> Iterable[Any]:
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    for f in shapes:
        if f.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _formas(f.shapes)
        else:
            yield f


def _llenar_marco(tf: Any, ctx: Contexto) -> None:
    """Los párrafos de un cuadro de texto: {{#lista}} repite el párrafo (viñetas) y cada uno se llena en una pasada."""
    for p in list(tf.paragraphs):
        texto = "".join(r.text for r in p.runs)
        m = REPETIR.search(texto)
        if m:
            base = REPETIR.sub("", texto)
            for i, item in enumerate(ctx.lista(m.group(1)), 1):
                nuevo = copy.deepcopy(p._p)
                p._p.addprevious(nuevo)
                from pptx.text.text import _Paragraph
                q = _Paragraph(nuevo, p._parent)
                with ctx.con(item=item, indice=i):
                    _poner_texto_pptx(q, ctx.texto(base))
            p._p.getparent().remove(p._p)
        elif "{{" in texto:
            _poner_texto_pptx(p, ctx.texto(texto))


def _poner_texto_pptx(p: Any, nuevo: str) -> None:
    if p.runs:
        p.runs[0].text = nuevo
        for r in p.runs[1:]:
            r.text = ""
    elif nuevo:
        p.add_run().text = nuevo


def _layout_de_contenido(prs: Any) -> Any:
    """El diseño «Título y contenido» de la plantilla (título + cuerpo), o el que más se le parezca."""
    from pptx.enum.shapes import PP_PLACEHOLDER
    mejor = None
    for lay in prs.slide_layouts:
        tipos = {ph.placeholder_format.type for ph in lay.placeholders}
        if PP_PLACEHOLDER.TITLE in tipos and (PP_PLACEHOLDER.BODY in tipos or PP_PLACEHOLDER.OBJECT in tipos):
            return lay
        if PP_PLACEHOLDER.TITLE in tipos and mejor is None:
            mejor = lay
    return mejor or prs.slide_layouts[0]


def _diapositivas_en_plantilla(prs: Any, pres: Dict[str, Any], posicion: Optional[int]) -> int:
    """Agrega las diapositivas de la IA con el diseño de la plantilla (sus colores, fuentes y logo del patrón)."""
    from pptx.enum.shapes import PP_PLACEHOLDER
    lay = _layout_de_contenido(prs)
    nuevas = []
    for d in presentacion_limpia(pres)["diapositivas"]:
        s = prs.slides.add_slide(lay)
        nuevas.append(s)
        cuerpo = None
        for ph in s.placeholders:
            t = ph.placeholder_format.type
            if t in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                ph.text = d["titulo"]
            elif t in (PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT) and cuerpo is None:
                cuerpo = ph
        if cuerpo is not None and d["puntos"]:
            tf = cuerpo.text_frame
            for i, punto in enumerate(d["puntos"]):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.text = punto
        elif cuerpo is not None:
            cuerpo._element.getparent().remove(cuerpo._element)
        if d["tabla"]:
            arriba = int(prs.slide_height * (0.55 if d["puntos"] else 0.25))
            _tabla_pptx(s, d["tabla"], int(prs.slide_width * 0.06), arriba, int(prs.slide_width * 0.88), int(prs.slide_height * 0.4))
        if d["notas"]:
            s.notes_slide.notes_text_frame.text = d["notas"]
    if posicion is not None:
        lista = prs.slides._sldIdLst
        ids = list(lista)[-len(nuevas):] if nuevas else []
        for k, el in enumerate(ids):
            lista.remove(el)
            lista.insert(posicion + k, el)
    return len(nuevas)


def _quitar_diapositiva(prs: Any, indice: int) -> None:
    lista = prs.slides._sldIdLst
    el = list(lista)[indice]
    prs.part.drop_rel(el.rId)
    lista.remove(el)


def llenar_pptx(datos: bytes, ctx: Contexto) -> bytes:
    """Una plantilla de PowerPoint llena (con su patrón, colores, fuentes y logos): textos, tablas con filas que se
    repiten, párrafos con {{#lista}}, notas, {{imagen:logo}} (la forma se cambia por la imagen en su lugar y tamaño) y,
    con ctx.contenido (una presentación de la IA), diapositivas nuevas con el diseño de la plantilla donde está
    {{contenido}} (esa diapositiva se quita) o al final."""
    from pptx import Presentation
    from pptx.util import Emu
    prs = Presentation(io.BytesIO(datos))
    marca: Optional[Tuple[int, bool]] = None  # (dónde van las diapositivas de la IA, ¿se quita la diapositiva del marcador?)
    for i, slide in enumerate(prs.slides):
        for forma in list(_formas(slide.shapes)):
            if forma.has_text_frame and CONTENIDO.search(forma.text_frame.text) and marca is None:
                sola = bool(CONTENIDO.fullmatch(forma.text_frame.text.strip()))
                otros = any(f._element is not forma._element and f.has_text_frame and f.text_frame.text.strip() for f in _formas(slide.shapes))
                if sola and not otros:
                    marca = (i, True)  # una diapositiva que solo dice {{contenido}}: se cambia por las de la IA
                    continue
                # el marcador va entre otros textos: se quita y las diapositivas de la IA van después de esta
                for p in list(forma.text_frame.paragraphs):
                    t = "".join(r.text for r in p.runs)
                    if CONTENIDO.search(t):
                        if CONTENIDO.fullmatch(t.strip()) and len(forma.text_frame.paragraphs) > 1:
                            p._p.getparent().remove(p._p)
                        else:
                            _poner_texto_pptx(p, CONTENIDO.sub("", t))
                marca = (i + 1, False)
            if forma.has_table:
                tbl = forma.table._tbl
                for fila in list(forma.table.rows):
                    texto = " ".join(c.text for c in fila.cells)
                    m = REPETIR.search(texto)
                    if not m:
                        continue
                    from pptx.table import _Row
                    for k, item in enumerate(ctx.lista(m.group(1)), 1):
                        nuevo = copy.deepcopy(fila._tr)
                        fila._tr.addprevious(nuevo)
                        with ctx.con(item=item, indice=k):
                            for celda in _Row(nuevo, forma.table).cells:
                                for p in celda.text_frame.paragraphs:
                                    t = "".join(r.text for r in p.runs)
                                    if "{{" in t:
                                        _poner_texto_pptx(p, ctx.texto(REPETIR.sub("", t)))
                    tbl.remove(fila._tr)
                for fila in forma.table.rows:
                    for celda in fila.cells:
                        _llenar_marco(celda.text_frame, ctx)
            elif forma.has_text_frame:
                texto = forma.text_frame.text
                m = IMAGEN.search(texto)
                if m and not IMAGEN.sub("", texto).strip():
                    imagen = ctx.imagen(m.group(1))
                    el = forma._element
                    if imagen:
                        w, h = _medidas_imagen(imagen)
                        escala = min(forma.width / max(1, w), forma.height / max(1, h))
                        iw, ih = int(w * escala), int(h * escala)
                        slide.shapes.add_picture(io.BytesIO(imagen), forma.left + (forma.width - iw) // 2, forma.top + (forma.height - ih) // 2, Emu(iw), Emu(ih))
                    el.getparent().remove(el)
                    continue
                _llenar_marco(forma.text_frame, ctx)
        if slide.has_notes_slide:
            _llenar_marco(slide.notes_slide.notes_text_frame, ctx)
    if isinstance(ctx.contenido, dict):
        ctx.contenido_puesto = True
        n = _diapositivas_en_plantilla(prs, ctx.contenido, marca[0] if marca else None)
        if marca and marca[1]:
            _quitar_diapositiva(prs, marca[0] + n)
    elif marca and marca[1]:
        _quitar_diapositiva(prs, marca[0])
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def pptx_de_texto(texto: str, titulo: str = "") -> bytes:
    """Desde texto: el primer «# » es la portada (el título dado solo si el texto no trae uno)."""
    import documentos as m_doc
    bloques = m_doc.bloques_de_texto(texto)
    tiene_titulo = bool(bloques) and bloques[0][0] == "titulo"
    return pptx_de_presentacion(presentacion_de_bloques(bloques, "" if tiene_titulo else titulo))


def texto_pptx(datos: bytes) -> str:
    """El texto de una presentación, diapositiva por diapositiva (con tablas y notas)."""
    from pptx import Presentation
    prs = Presentation(io.BytesIO(datos))
    partes = []
    for i, slide in enumerate(prs.slides, 1):
        lineas = [f"## Diapositiva {i}"]
        for forma in _formas(slide.shapes):
            if forma.has_table:
                for fila in forma.table.rows:
                    lineas.append("| " + " | ".join(c.text.strip() for c in fila.cells) + " |")
            elif forma.has_text_frame and forma.text_frame.text.strip():
                lineas.append(forma.text_frame.text.strip())
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            lineas.append(f"Notas: {slide.notes_slide.notes_text_frame.text.strip()}")
        partes.append("\n".join(lineas))
    return "\n\n".join(partes)


# =====================================================================
#   HTML (documentos y correos)
# =====================================================================

_ESTILO_HTML = """body{margin:0;background:#f4f8fc;font-family:Arial,Helvetica,sans-serif;color:#334155;line-height:1.55}
.doc{max-width:760px;margin:24px auto;background:#fff;border:1px solid #d6e4f2;border-radius:10px;padding:32px 40px}
h1{color:#0b4f8a;font-size:26px;margin:0 0 12px}h2{color:#0b4f8a;font-size:19px;margin:24px 0 8px}
p{margin:0 0 12px}ul{margin:0 0 12px;padding-left:22px}table{border-collapse:collapse;width:100%;margin:8px 0 16px;font-size:14px}
th{background:#0b4f8a;color:#fff;text-align:left;padding:8px 10px}td{border-bottom:1px solid #d6e4f2;padding:7px 10px}
tr:nth-child(even) td{background:#f4f8fc}img{max-width:100%}"""


def html_de_bloques_cuerpo(bloques: List[Tuple[str, Any]]) -> str:
    """Los bloques como HTML (sin <html>): para poner en {{contenido}} o dentro de un documento."""
    e = m_html.escape
    partes: List[str] = []
    lista: List[str] = []

    def cerrar_lista() -> None:
        if lista:
            partes.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in lista) + "</ul>")
            lista.clear()

    for tipo, v in bloques:
        if tipo == "vineta":
            lista.append(str(v))
            continue
        cerrar_lista()
        if tipo == "titulo":
            partes.append(f"<h1>{e(str(v))}</h1>")
        elif tipo == "subtitulo":
            partes.append(f"<h2>{e(str(v))}</h2>")
        elif tipo == "tabla" and v:
            cab, *filas = v
            partes.append("<table><thead><tr>" + "".join(f"<th>{e(str(c))}</th>" for c in cab) + "</tr></thead><tbody>"
                          + "".join("<tr>" + "".join(f"<td>{e(str(c))}</td>" for c in f) + "</tr>" for f in filas) + "</tbody></table>")
        elif tipo == "imagen":
            jpeg = v[0] if isinstance(v, tuple) else v
            partes.append(f'<p><img alt="" src="data:image/jpeg;base64,{base64.b64encode(jpeg).decode()}"></p>')
        else:
            partes.append(f"<p>{e(str(v))}</p>")
    cerrar_lista()
    return "\n".join(partes)


def html_de_bloques(bloques: List[Tuple[str, Any]], titulo: str = "") -> bytes:
    cuerpo = html_de_bloques_cuerpo(bloques)
    doc = (f'<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
           f"<title>{m_html.escape(titulo or 'Documento')}</title>\n<style>{_ESTILO_HTML}</style>\n</head>\n<body>\n<div class=\"doc\">\n{cuerpo}\n</div>\n</body>\n</html>\n")
    return doc.encode("utf-8")


def html_de_valor(valor: Any, titulo: str = "", columnas: Optional[List[str]] = None) -> bytes:
    """Una variable como HTML: una lista de objetos es una tabla; un objeto, una tabla de dos columnas; un texto, párrafos."""
    filas = _filas_de(valor, columnas)
    if filas is not None:
        cab, cuerpo = filas
        bloques: List[Tuple[str, Any]] = ([("titulo", titulo)] if titulo else []) + [("tabla", [cab] + cuerpo)]
    elif isinstance(valor, dict):
        bloques = ([("titulo", titulo)] if titulo else []) + [("tabla", [["Campo", "Valor"]] + [[k, _a_texto(v)] for k, v in valor.items()])]
    else:
        import documentos as m_doc
        bloques = ([("titulo", titulo)] if titulo else []) + m_doc.bloques_de_texto(_a_texto(valor))
    return html_de_bloques(bloques, titulo)


def _data_uri(datos: bytes) -> str:
    tipo = "image/png" if datos[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if datos[:3] == b"\xff\xd8\xff" else "image/png"
    return f"data:{tipo};base64,{base64.b64encode(datos).decode()}"


def llenar_html(texto: str, ctx: Contexto) -> str:
    """Una plantilla HTML (un correo, un documento) llena: {{x}} escapado, {{{x}}} tal cual, {{#lista}}…{{/lista}} que se
    repite (con {{item.x}} e {{indice}}), {{imagen:logo}} como imagen incrustada y {{contenido}} con lo de la IA."""
    def simple(t: str) -> str:
        def poner(m: "re.Match[str]") -> str:
            crudo = m.group(1) is not None
            ref = (m.group(1) if crudo else m.group(2)).strip()
            mi = IMAGEN.fullmatch(m.group(0).replace("{{{", "{{").replace("}}}", "}}"))
            if mi:
                datos = ctx.imagen(mi.group(1))
                return _data_uri(datos) if datos else ""
            if ref == "contenido":
                if ctx.contenido is not None:
                    ctx.contenido_puesto = True
                    return html_de_bloques_cuerpo(ctx.contenido) if isinstance(ctx.contenido, list) else str(ctx.contenido)
                return ""
            if not ref or ref[0] in "#/":
                return ""
            v = ctx.valor(ref)
            ctx.usadas.add(ref)
            if v is None or v == "":
                ctx.faltantes.add(ref)
            t2 = ctx.a_texto(v)
            return t2 if crudo else m_html.escape(t2)
        return MARCA.sub(poner, t)

    def todo(t: str) -> str:
        salida, i = [], 0
        for m in SECCION.finditer(t):
            salida.append(simple(t[i:m.start()]))
            for k, item in enumerate(ctx.lista(m.group(1)), 1):
                with ctx.con(item=item, indice=k):
                    salida.append(todo(m.group(2)))
            i = m.end()
        salida.append(simple(t[i:]))
        return "".join(salida)

    r = todo(texto)
    if ctx.contenido is not None and not ctx.contenido_puesto:
        extra = html_de_bloques_cuerpo(ctx.contenido) if isinstance(ctx.contenido, list) else str(ctx.contenido)
        r = re.sub(r"(?i)</body>", extra + "\n</body>", r, 1) if re.search(r"(?i)</body>", r) else r + "\n" + extra
        ctx.contenido_puesto = True
    return r


HTML_CORREO_BASE = """<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><title>{titulo}</title></head>
<body style="margin:0;background:#f4f8fc;font-family:Arial,Helvetica,sans-serif;color:#334155">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #d6e4f2;border-radius:10px">
<tr><td style="background:#0b4f8a;height:6px;border-radius:10px 10px 0 0"></td></tr>
<tr><td style="padding:28px 32px;line-height:1.55;font-size:15px">
{cuerpo}
</td></tr>
</table>
</td></tr></table>
</body>
</html>
"""


def html_de_texto(texto: str, titulo: str = "") -> bytes:
    """Un correo HTML simple desde texto (# título, párrafos, viñetas, tablas con «|»), con las variables tal cual."""
    import documentos as m_doc
    cuerpo = html_de_bloques_cuerpo(m_doc.bloques_de_texto(texto))
    # las {{variables}} del texto se escaparon como texto: siguen siendo variables
    return HTML_CORREO_BASE.format(titulo=m_html.escape(titulo or "Correo"), cuerpo=cuerpo).encode("utf-8")


# =====================================================================
#   datos: JSON, XML, CSV, texto
# =====================================================================

def _filas_de(valor: Any, columnas: Optional[List[str]] = None) -> Optional[Tuple[List[str], List[List[str]]]]:
    """(encabezado, filas) de una lista de objetos o de listas; None si el valor no es una tabla."""
    if isinstance(valor, dict) and isinstance(valor.get("filas"), list) and valor["filas"] and all(isinstance(f, dict) for f in valor["filas"]):
        valor = valor["filas"]
    if not isinstance(valor, list) or not valor:
        return None
    if all(isinstance(x, dict) for x in valor):
        cab = list(columnas or [])
        if not cab:
            for x in valor:
                cab += [k for k in x if k not in cab]
        return cab, [[_a_texto(_navegar(x, c) if "." in c else x.get(c)) for c in cab] for x in valor]
    if all(isinstance(x, list) for x in valor):
        filas = [[_a_texto(c) for c in f] for f in valor]
        if columnas:
            return list(columnas), filas
        return filas[0], filas[1:]
    return (list(columnas or ["valor"]), [[_a_texto(x)] for x in valor])


def csv_de_valor(valor: Any, columnas: Optional[List[str]] = None, separador: str = ",") -> bytes:
    filas = _filas_de(valor, columnas)
    if filas is None:
        if isinstance(valor, dict):
            filas = (list(columnas or valor.keys()), [[_a_texto(valor.get(c)) for c in (columnas or valor.keys())]])
        else:
            filas = (["valor"], [[_a_texto(valor)]])
    s = io.StringIO()
    w = csv.writer(s, delimiter=separador)
    w.writerow(filas[0])
    w.writerows(filas[1])
    return ("﻿" + s.getvalue()).encode("utf-8")  # con BOM: Excel lo abre con tildes


def json_de_valor(valor: Any) -> bytes:
    return (json.dumps(valor, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")


def _nombre_xml(k: Any) -> str:
    n = re.sub(r"[^A-Za-z0-9_.\-À-ÿ]", "_", str(k)) or "campo"
    if not re.match(r"[A-Za-z_À-ÿ]", n) or n.lower().startswith("xml"):
        n = f"_{n}"
    return n


def xml_de_valor(valor: Any, raiz: str = "datos", elemento: str = "elemento") -> bytes:
    """Un valor como XML: un objeto da un elemento por campo; una lista, un <elemento> por cosa (o el campo repetido)."""
    import xml.etree.ElementTree as ET

    def poner(padre: Any, v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if isinstance(x, list):
                    for y in x:
                        poner(ET.SubElement(padre, _nombre_xml(k)), y)
                else:
                    poner(ET.SubElement(padre, _nombre_xml(k)), x)
        elif isinstance(v, list):
            for y in v:
                poner(ET.SubElement(padre, _nombre_xml(elemento)), y)
        elif v is not None:
            padre.text = _a_texto(v)

    r = ET.Element(_nombre_xml(raiz or "datos"))
    poner(r, valor)
    ET.indent(r)
    return ET.tostring(r, encoding="utf-8", xml_declaration=True) + b"\n"


def escapar_xml(t: str) -> str:
    return m_html.escape(t, quote=True)


# =====================================================================
#   ZIP
# =====================================================================

def zip_de(archivos: Sequence[Tuple[str, bytes]], max_bytes: int = 30_000_000) -> Tuple[bytes, List[str]]:
    """Un .zip con los archivos dados (nombres sin rutas; los repetidos quedan «nombre (2).ext»). (bytes, nombres)."""
    if len(archivos) > MAX_ZIP:
        raise ErrorDePlantilla(f"un ZIP lleva hasta {MAX_ZIP} archivos (llegaron {len(archivos)})")
    usados: Set[str] = set()
    nombres: List[str] = []
    s = io.BytesIO()
    total = 0
    with zipfile.ZipFile(s, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, datos in archivos:
            total += len(datos)
            if total > max_bytes:
                raise ErrorDePlantilla(f"los archivos del ZIP pasan de {max_bytes // 1_000_000} MB")
            base = re.sub(r'[\x00-\x1f\x7f<>:"|?*/\\]', "_", str(nombre or "archivo")).lstrip(".").strip() or "archivo"
            n, k = base, 2
            while n.lower() in usados:
                raiz, punto, ext = base.rpartition(".")
                n = f"{raiz} ({k}).{ext}" if punto else f"{base} ({k})"
                k += 1
            usados.add(n.lower())
            info = zipfile.ZipInfo(n, date_time=time.localtime()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, datos)
            nombres.append(n)
    if s.tell() > max_bytes:
        raise ErrorDePlantilla(f"el ZIP pasaría de {max_bytes // 1_000_000} MB")
    return s.getvalue(), nombres


def texto_zip(datos: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        return "\n".join(f"- {i.filename} ({i.file_size:,} bytes)" for i in z.infolist() if not i.is_dir())


# =====================================================================
#   la biblioteca de plantillas (<estado>/documentos/<id>/)
# =====================================================================

def carpeta_biblioteca() -> Path:
    from conectores.base import ruta_estado
    return ruta_estado() / "documentos"


def leer_plantilla(pid: str) -> Tuple[Dict[str, Any], bytes]:
    """(meta, bytes) de una plantilla de la biblioteca; ErrorDePlantilla si no existe."""
    pid = str(pid or "").strip()
    if not ID_PLANTILLA.match(pid):
        raise ErrorDePlantilla(f"«{pid[:60]}» no es el id de una plantilla de documentos (minúsculas, números y guiones)")
    carpeta = carpeta_biblioteca() / pid
    try:
        meta = json.loads((carpeta / "meta.json").read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ErrorDePlantilla(f"la plantilla de documentos «{pid}» no existe (míralas en Documentos)") from e
    ruta = (carpeta / str(meta.get("archivo") or "")).resolve()
    if carpeta.resolve() not in ruta.parents or not ruta.is_file():
        raise ErrorDePlantilla(f"la plantilla «{pid}» no tiene su archivo")
    return meta, ruta.read_bytes()


def recurso_de(pid: str, nombre: str) -> Optional[bytes]:
    """Una imagen (logo, firma, sello) guardada con la plantilla."""
    if not (ID_PLANTILLA.match(pid or "") and NOMBRE_RECURSO.match(nombre or "")):
        return None
    carpeta = (carpeta_biblioteca() / pid / "recursos").resolve()
    if not carpeta.is_dir():
        return None
    for p in carpeta.iterdir():
        if p.is_file() and p.stem == nombre:
            return p.read_bytes()
    return None


def listar_biblioteca() -> List[Dict[str, Any]]:
    base = carpeta_biblioteca()
    r = []
    if base.is_dir():
        for d in sorted(base.iterdir()):
            try:
                r.append(json.loads((d / "meta.json").read_text(encoding="utf-8")))
            except Exception:
                continue
    return r


def llenar(formato: str, datos: bytes, ctx: Contexto) -> bytes:
    if formato == "docx":
        return llenar_docx(datos, ctx)
    if formato == "xlsx":
        return llenar_xlsx(datos, ctx)
    if formato == "pptx":
        return llenar_pptx(datos, ctx)
    if formato == "html":
        return llenar_html(_decodificar(datos), ctx).encode("utf-8")
    raise ErrorDePlantilla(f"formato de plantilla «{formato}» (válidos: {', '.join(FORMATOS_PLANTILLA)})")


def crear_desde_texto(formato: str, texto: str, titulo: str = "") -> bytes:
    if formato == "docx":
        return docx_de_texto(texto, titulo)
    if formato == "pptx":
        return pptx_de_texto(texto, titulo)
    if formato == "xlsx":
        return xlsx_de_texto(texto, titulo)
    if formato == "html":
        return html_de_texto(texto, titulo)
    raise ErrorDePlantilla(f"formato «{formato}» (válidos: {', '.join(FORMATOS_PLANTILLA)})")


def huella(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


# =====================================================================
#   como programa (lo llama el servidor)
# =====================================================================

def _principal(args: List[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args[:1] == ["analizar"] and len(args) >= 3:
            r = analizar(Path(args[1]).read_bytes(), args[2])
        elif args[:1] == ["crear"] and len(args) >= 3:
            e = json.loads(sys.stdin.read() or "{}")
            datos = crear_desde_texto(args[1], str(e.get("texto") or ""), str(e.get("titulo") or ""))
            Path(args[2]).write_bytes(datos)
            r = {"ok": True, "tamano": len(datos)}
        elif args[:1] == ["llenar"] and len(args) >= 4:
            e = json.loads(sys.stdin.read() or "{}")
            datos_ = e.get("datos") if isinstance(e.get("datos"), dict) else {}
            recursos = Path(e["recursos"]) if e.get("recursos") else None

            def imagen(ref: str) -> Optional[bytes]:
                if recursos and recursos.is_dir():
                    for p in recursos.iterdir():
                        if p.is_file() and p.stem == ref:
                            return p.read_bytes()
                return None
            ctx = Contexto(lambda ref: _navegar(datos_, ref), imagen)
            salida = llenar(args[2], Path(args[1]).read_bytes(), ctx)
            Path(args[3]).write_bytes(salida)
            r = {"ok": True, "faltantes": sorted(ctx.faltantes), "usadas": sorted(ctx.usadas), "sin_imagen": sorted(ctx.sin_imagen), "tamano": len(salida)}
        else:
            r = {"error": "uso: analizar RUTA FORMATO | crear FORMATO SALIDA | llenar RUTA FORMATO SALIDA"}
    except Exception as e:  # el servidor muestra el mensaje a la persona
        r = {"error": f"{type(e).__name__}: {e}"[:600]}
    sys.stdout.write(json.dumps(r, ensure_ascii=False))
    return 0 if "error" not in r else 1


if __name__ == "__main__":
    raise SystemExit(_principal(sys.argv[1:]))
