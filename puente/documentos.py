"""
Documentos para los flujos: generar PDF y Word desde plantillas, unir y dividir
PDF, convertir entre formatos y sacar el texto por página (para citar).

Licencias: python-docx (MIT) para Word, openpyxl (MIT) para Excel, pypdfium2
(Apache-2.0 / BSD-3-Clause) para leer, unir, dividir y pasar a imagen los PDF,
Pillow (HPND, permisiva) para imágenes. El PDF se escribe con un generador
propio (texto con Helvetica y JPEG incrustados), sin dependencias.

Sin LibreOffice: una conversión DOCX → PDF conserva el texto, los títulos, las
viñetas y las tablas, no el diseño exacto (fuentes, márgenes, imágenes).
"""
from __future__ import annotations

import csv
import io
import re
import zlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ------------------------------------------------------------- el PDF propio

# Anchos de Helvetica (unidades de 1/1000) de los caracteres de WinAnsi que importan; el resto, 556.
_ANCHOS = {" ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667, "'": 191, "(": 333, ")": 333, "*": 389, "+": 584,
           ",": 278, "-": 333, ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015,
           "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722, "I": 278, "J": 500, "K": 667, "L": 556,
           "M": 833, "N": 722, "O": 778, "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944, "X": 667,
           "Y": 667, "Z": 611, "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556, "`": 333, "a": 556, "b": 556, "c": 500, "d": 556,
           "e": 556, "f": 278, "g": 556, "h": 556, "i": 222, "j": 222, "k": 500, "l": 222, "m": 833, "n": 556, "o": 556, "p": 556,
           "q": 556, "r": 333, "s": 500, "t": 278, "u": 556, "v": 500, "w": 722, "x": 500, "y": 500, "z": 500, "{": 334, "|": 260,
           "}": 334, "~": 584, "¡": 333, "¿": 611, "«": 556, "»": 556, "°": 400, "–": 556, "—": 1000, "“": 333, "”": 333, "‘": 222,
           "’": 222, "•": 350, "…": 1000, "€": 556, "·": 278}
_BASE = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u", "ñ": "n", "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U",
         "Ü": "U", "Ñ": "N", "ç": "c", "Ç": "C", "à": "a", "è": "e", "ì": "i", "ò": "o", "ù": "u"}
A4 = (595.28, 841.89)
MARGEN = 56


def _ancho(texto: str, tam: float, negrita: bool = False) -> float:
    total = sum(_ANCHOS.get(_BASE.get(c, c), 556) for c in texto)
    return total * tam / 1000 * (1.06 if negrita else 1.0)


def _partir(texto: str, ancho: float, tam: float, negrita: bool = False) -> List[str]:
    """Las líneas de un párrafo que caben en `ancho` puntos."""
    lineas: List[str] = []
    for bruto in (texto or "").split("\n"):
        actual = ""
        for palabra in bruto.split(" "):
            prueba = f"{actual} {palabra}" if actual else palabra
            if _ancho(prueba, tam, negrita) <= ancho or not actual:
                actual = prueba
                while _ancho(actual, tam, negrita) > ancho and len(actual) > 1:  # una palabra que no cabe sola se corta
                    corte = max(1, int(len(actual) * ancho / max(1.0, _ancho(actual, tam, negrita))) - 1)
                    lineas.append(actual[:corte])
                    actual = actual[corte:]
            else:
                lineas.append(actual)
                actual = palabra
        lineas.append(actual)
    return lineas


def _cadena_pdf(texto: str) -> bytes:
    datos = texto.encode("cp1252", errors="replace")
    return b"(" + datos.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").replace(b"\r", b"") + b")"


class _PDF:
    """Un PDF de texto (Helvetica, WinAnsi) con páginas A4 y, si hace falta, imágenes JPEG."""

    def __init__(self, titulo: str = "") -> None:
        self.paginas: List[Tuple[bytes, List[Tuple[str, bytes, int, int]]]] = []
        self.ops: List[bytes] = []
        self.imagenes: List[Tuple[str, bytes, int, int]] = []
        self.y = A4[1] - MARGEN
        self.titulo = titulo

    def _nueva(self) -> None:
        if self.ops or self.imagenes:
            self.paginas.append((b"\n".join(self.ops), self.imagenes))
        self.ops, self.imagenes = [], []
        self.y = A4[1] - MARGEN

    def _linea(self, texto: str, tam: float, negrita: bool, x: float = MARGEN) -> None:
        alto = tam * 1.38
        if self.y - alto < MARGEN:
            self._nueva()
        self.y -= alto
        fuente = b"/F2" if negrita else b"/F1"
        self.ops.append(b"BT " + fuente + f" {tam:g} Tf {x:.2f} {self.y:.2f} Td ".encode() + _cadena_pdf(texto) + b" Tj ET")

    def parrafo(self, texto: str, tam: float = 11, negrita: bool = False, sangria: float = 0, vineta: str = "") -> None:
        ancho = A4[0] - 2 * MARGEN - sangria
        lineas = _partir(texto, ancho - (12 if vineta else 0), tam, negrita)
        for k, l_ in enumerate(lineas):
            if vineta and k == 0:
                self._linea(vineta, tam, negrita, MARGEN + sangria)
                self.y += tam * 1.38
            self._linea(l_, tam, negrita, MARGEN + sangria + (12 if vineta else 0))

    def espacio(self, puntos: float = 6) -> None:
        self.y -= puntos

    def tabla(self, filas: Sequence[Sequence[Any]], tam: float = 9.5) -> None:
        if not filas:
            return
        cols = max(len(f) for f in filas)
        ancho = (A4[0] - 2 * MARGEN) / max(1, cols)
        for i, fila in enumerate(filas):
            celdas = [_partir(str(c if c is not None else ""), ancho - 6, tam, i == 0) for c in list(fila) + [""] * (cols - len(fila))]
            alto = max(len(c) for c in celdas)
            if self.y - alto * tam * 1.38 < MARGEN:
                self._nueva()
            base = self.y
            for k, c in enumerate(celdas):
                self.y = base
                for l_ in c:
                    self._linea(l_, tam, i == 0, MARGEN + k * ancho)
            self.y = base - alto * tam * 1.38 - 3
            self.ops.append(f"0.75 G {MARGEN:.2f} {self.y + 1.5:.2f} m {A4[0] - MARGEN:.2f} {self.y + 1.5:.2f} l S 0 G".encode())

    def imagen(self, jpeg: bytes, ancho_px: int, alto_px: int) -> None:
        """Una imagen en su propia página, ajustada a los márgenes."""
        self._nueva()
        disp_w, disp_h = A4[0] - 2 * MARGEN, A4[1] - 2 * MARGEN
        escala = min(disp_w / ancho_px, disp_h / alto_px)
        w, h = ancho_px * escala, alto_px * escala
        nombre = f"Im{len(self.imagenes) + 1}"
        self.imagenes.append((nombre, jpeg, ancho_px, alto_px))
        x, y = MARGEN + (disp_w - w) / 2, A4[1] - MARGEN - h
        self.ops.append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm /{nombre} Do Q".encode())
        self._nueva()

    def bytes(self) -> bytes:
        self._nueva()
        if not self.paginas:
            self.paginas.append((b"", []))
        objetos: List[bytes] = []

        def agregar(o: bytes) -> int:
            objetos.append(o)
            return len(objetos)

        cat = agregar(b"")  # se llena al final
        pags = agregar(b"")
        f1 = agregar(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = agregar(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        hijos = []
        for contenido, imgs in self.paginas:
            xobj = []
            for nombre, jpeg, w, h in imgs:
                n = agregar(f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB /BitsPerComponent 8 "
                            f"/Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n".encode() + jpeg + b"\nendstream")
                xobj.append(f"/{nombre} {n} 0 R")
            comprimido = zlib.compress(contenido)
            c = agregar(f"<< /Length {len(comprimido)} /Filter /FlateDecode >>\nstream\n".encode() + comprimido + b"\nendstream")
            recursos = f"<< /Font << /F1 {f1} 0 R /F2 {f2} 0 R >>" + (f" /XObject << {' '.join(xobj)} >>" if xobj else "") + " >>"
            hijos.append(agregar(f"<< /Type /Page /Parent {pags} 0 R /MediaBox [0 0 {A4[0]} {A4[1]}] /Resources {recursos} /Contents {c} 0 R >>".encode()))
        objetos[pags - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{h} 0 R' for h in hijos)}] /Count {len(hijos)} >>".encode()
        objetos[cat - 1] = f"<< /Type /Catalog /Pages {pags} 0 R >>".encode()
        info = agregar(b"<< /Title " + _cadena_pdf(self.titulo or "Documento") + b" /Producer (Positiva IA) >>")
        salida = io.BytesIO()
        salida.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        posiciones = []
        for i, o in enumerate(objetos, 1):
            posiciones.append(salida.tell())
            salida.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
        xref = salida.tell()
        salida.write(f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode())
        for p in posiciones:
            salida.write(f"{p:010d} 00000 n \n".encode())
        salida.write(f"trailer\n<< /Size {len(objetos) + 1} /Root {cat} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
        return salida.getvalue()


# ----------------------------------------------------------- bloques de texto
# Una plantilla de texto se entiende como bloques: «# » título, «## » subtítulo, «- » viñeta,
# líneas con «|» tabla, y una línea en blanco separa párrafos.

def bloques_de_texto(texto: str) -> List[Tuple[str, Any]]:
    r: List[Tuple[str, Any]] = []
    parrafo: List[str] = []
    tabla: List[List[str]] = []

    def cerrar() -> None:
        if parrafo:
            r.append(("parrafo", " ".join(x.strip() for x in parrafo)))
            parrafo.clear()
        if tabla:
            r.append(("tabla", [list(f) for f in tabla]))
            tabla.clear()

    for linea in (texto or "").replace("\r\n", "\n").split("\n"):
        s = linea.strip()
        if s.startswith("|") or (s.count("|") >= 2 and not s.startswith(("#", "-"))):
            if parrafo:
                r.append(("parrafo", " ".join(x.strip() for x in parrafo)))
                parrafo.clear()
            celdas = [c.strip() for c in s.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in celdas if c):  # la línea --- de markdown no es fila
                tabla.append(celdas)
            continue
        if tabla:
            r.append(("tabla", [list(f) for f in tabla]))
            tabla.clear()
        if not s:
            cerrar()
        elif s.startswith("## "):
            cerrar()
            r.append(("subtitulo", s[3:].strip()))
        elif s.startswith("# "):
            cerrar()
            r.append(("titulo", s[2:].strip()))
        elif re.match(r"^[-*•]\s+", s):
            cerrar()
            r.append(("vineta", re.sub(r"^[-*•]\s+", "", s)))
        else:
            parrafo.append(s)
    cerrar()
    return r


# --------------------------------------------- documento estructurado (IA)
# Lo que devuelve la IA cuando redacta un documento: un título y secciones con párrafos, viñetas y una tabla.

ESQUEMA_DOCUMENTO: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "secciones": {"type": "array", "items": {"type": "object", "properties": {
            "titulo": {"type": "string"},
            "parrafos": {"type": "array", "items": {"type": "string"}},
            "lista": {"type": "array", "items": {"type": "string"}},
            "tabla": {"type": "object", "properties": {
                "columnas": {"type": "array", "items": {"type": "string"}},
                "filas": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}},
        }}},
    },
    "required": ["titulo", "secciones"],
}

# Lo que devuelve la IA cuando arma una hoja de cálculo: hojas con columnas y filas.
ESQUEMA_HOJAS: Dict[str, Any] = {
    "type": "object",
    "properties": {"hojas": {"type": "array", "items": {"type": "object", "properties": {
        "nombre": {"type": "string"},
        "columnas": {"type": "array", "items": {"type": "string"}},
        "filas": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}, "required": ["columnas", "filas"]}}},
    "required": ["hojas"],
}


def bloques_de_estructura(doc: Any, max_secciones: int = 60) -> List[Tuple[str, Any]]:
    """Un documento {titulo, secciones[{titulo, parrafos[], lista[], tabla{columnas, filas}}]} como bloques para PDF y Word."""
    r: List[Tuple[str, Any]] = []
    if not isinstance(doc, dict):
        return [("parrafo", str(doc or ""))] if doc else []
    if str(doc.get("titulo") or "").strip():
        r.append(("titulo", str(doc["titulo"]).strip()))
    for s in (doc.get("secciones") or [])[:max_secciones]:
        if not isinstance(s, dict):
            if str(s or "").strip():
                r.append(("parrafo", str(s).strip()))
            continue
        if str(s.get("titulo") or "").strip():
            r.append(("subtitulo", str(s["titulo"]).strip()))
        for p in s.get("parrafos") or []:
            if str(p or "").strip():
                r.append(("parrafo", str(p).strip()))
        for v in s.get("lista") or []:
            if str(v or "").strip():
                r.append(("vineta", str(v).strip()))
        t = s.get("tabla")
        if isinstance(t, dict) and (t.get("columnas") or t.get("filas")):
            filas = [[str(x if x is not None else "") for x in (f if isinstance(f, list) else [f])] for f in (t.get("filas") or [])[:500]]
            cab = [str(x) for x in t.get("columnas") or []]
            r.append(("tabla", ([cab] if cab else []) + filas))
    return r


def hojas_limpias(datos: Any, max_hojas: int = 20, max_filas: int = 5000) -> List[Dict[str, Any]]:
    """Las hojas {nombre, columnas, filas} que devolvió la IA, revisadas: nombres válidos para Excel y filas del ancho de las columnas."""
    hojas = datos.get("hojas") if isinstance(datos, dict) else datos
    r: List[Dict[str, Any]] = []
    usados: set = set()
    for i, h in enumerate((hojas if isinstance(hojas, list) else [])[:max_hojas], 1):
        if not isinstance(h, dict):
            continue
        nombre = re.sub(r"[\[\]:*?/\\]", " ", str(h.get("nombre") or f"Hoja {i}")).strip()[:31] or f"Hoja {i}"
        base, k = nombre, 2
        while nombre.lower() in usados:
            nombre = f"{base[:28]} {k}"
            k += 1
        usados.add(nombre.lower())
        columnas = [str(c) for c in h.get("columnas") or []]
        filas = []
        for f in (h.get("filas") or [])[:max_filas]:
            f = f if isinstance(f, list) else ([f.get(c) for c in columnas] if isinstance(f, dict) else [f])
            filas.append([("" if x is None else x) for x in f])
        ancho = max([len(columnas)] + [len(f) for f in filas] or [0])
        columnas += [f"columna_{j}" for j in range(len(columnas) + 1, ancho + 1)]
        r.append({"nombre": nombre, "columnas": columnas, "filas": [f + [""] * (ancho - len(f)) for f in filas]})
    return r


def _valor_celda(x: Any) -> Any:
    """Un número que llegó como texto se guarda como número (para sumar en Excel); lo demás, tal cual."""
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return x
    t = str(x if x is not None else "")
    if re.fullmatch(r"-?\d{1,15}([.,]\d+)?", t.strip()) and not re.fullmatch(r"0\d+", t.strip()):
        try:
            return float(t.strip().replace(",", ".")) if re.search(r"[.,]", t) else int(t.strip())
        except ValueError:
            return t
    return t


def xlsx_de_hojas(hojas: List[Dict[str, Any]]) -> bytes:
    """Un .xlsx con una pestaña por hoja (encabezado en negrita y fijo)."""
    import openpyxl  # MIT
    from openpyxl.styles import Font

    libro = openpyxl.Workbook()
    libro.remove(libro.active)
    for h in hojas or [{"nombre": "Hoja 1", "columnas": [], "filas": []}]:
        ws = libro.create_sheet(h["nombre"])
        if h["columnas"]:
            ws.append(h["columnas"])
            for celda in ws[1]:
                celda.font = Font(bold=True)
            ws.freeze_panes = "A2"
        for f in h["filas"]:
            ws.append([_valor_celda(x) for x in f])
        for k, col in enumerate(h["columnas"], 1):
            ancho = max([len(str(col))] + [len(str(f[k - 1])) for f in h["filas"][:200] if k - 1 < len(f)])
            ws.column_dimensions[openpyxl.utils.get_column_letter(k)].width = min(60, max(8, ancho + 2))
    s = io.BytesIO()
    libro.save(s)
    return s.getvalue()


def csv_de_hoja(h: Dict[str, Any]) -> bytes:
    s = io.StringIO()
    w = csv.writer(s)
    if h.get("columnas"):
        w.writerow(h["columnas"])
    for f in h.get("filas") or []:
        w.writerow(["" if x is None else x for x in f])
    return ("﻿" + s.getvalue()).encode("utf-8")  # con BOM: Excel lo abre con tildes


def pdf_de_bloques(bloques: List[Tuple[str, Any]], titulo: str = "") -> bytes:
    p = _PDF(titulo)
    for tipo, v in bloques:
        if tipo == "titulo":
            p.espacio(4)
            p.parrafo(str(v), 16, True)
            p.espacio(4)
        elif tipo == "subtitulo":
            p.espacio(3)
            p.parrafo(str(v), 13, True)
            p.espacio(2)
        elif tipo == "vineta":
            p.parrafo(str(v), 11, False, 10, "•")
        elif tipo == "tabla":
            p.espacio(4)
            p.tabla(v)
            p.espacio(4)
        elif tipo == "imagen":
            jpeg, w, h = v
            p.imagen(jpeg, w, h)
        else:
            p.parrafo(str(v), 11)
            p.espacio(6)
    return p.bytes()


def docx_de_bloques(bloques: List[Tuple[str, Any]], titulo: str = "") -> bytes:
    import docx  # python-docx (MIT)

    d = docx.Document()
    if titulo:
        d.core_properties.title = titulo
    for tipo, v in bloques:
        if tipo == "titulo":
            d.add_heading(str(v), level=1)
        elif tipo == "subtitulo":
            d.add_heading(str(v), level=2)
        elif tipo == "vineta":
            d.add_paragraph(str(v), style="List Bullet")
        elif tipo == "tabla":
            cols = max(len(f) for f in v) if v else 0
            if cols:
                t = d.add_table(rows=0, cols=cols)
                t.style = "Table Grid"
                for i, fila in enumerate(v):
                    celdas = t.add_row().cells
                    for k in range(cols):
                        celdas[k].text = str(fila[k]) if k < len(fila) and fila[k] is not None else ""
                        if i == 0:
                            for run in celdas[k].paragraphs[0].runs:
                                run.bold = True
        elif tipo == "imagen":
            jpeg, _, _ = v
            d.add_picture(io.BytesIO(jpeg), width=docx.shared.Cm(15))
        else:
            d.add_paragraph(str(v))
    s = io.BytesIO()
    d.save(s)
    return s.getvalue()


# ------------------------------------------------------- plantillas de Word

def _parrafos_docx(d: Any) -> Iterable[Any]:
    for p in d.paragraphs:
        yield p
    for t in d.tables:
        for fila in t.rows:
            for celda in fila.cells:
                yield from celda.paragraphs
    for s in d.sections:
        for parte in (s.header, s.footer):
            for p in parte.paragraphs:
                yield p


def llenar_docx(datos: bytes, render) -> Tuple[bytes, int]:
    """Una plantilla .docx con {{variables}} en su texto, llena con `render(texto)`. (bytes, párrafos cambiados).
    Cada párrafo que tiene una plantilla queda con el formato de su primer tramo."""
    import docx

    d = docx.Document(io.BytesIO(datos))
    cambiados = 0
    for p in _parrafos_docx(d):
        texto = "".join(r.text for r in p.runs)
        if "{{" not in texto:
            continue
        nuevo = render(texto)
        if p.runs:
            p.runs[0].text = nuevo
            for r in p.runs[1:]:
                r.text = ""
        cambiados += 1
    s = io.BytesIO()
    d.save(s)
    return s.getvalue(), cambiados


# ------------------------------------------------------------- leer texto

def texto_docx(datos: bytes) -> str:
    return "\n".join(t for _, t in bloques_docx(datos) if isinstance(t, str))


def bloques_docx(datos: bytes) -> List[Tuple[str, Any]]:
    """Los bloques de un .docx en su orden: títulos, párrafos, viñetas y tablas."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(io.BytesIO(datos))
    r: List[Tuple[str, Any]] = []
    for hijo in d.element.body.iterchildren():
        etiqueta = hijo.tag.rsplit("}", 1)[-1]
        if etiqueta == "p":
            p = Paragraph(hijo, d)
            t = p.text.strip()
            if not t:
                continue
            estilo = (p.style.name if p.style is not None else "").lower()
            if estilo.startswith("heading 1") or estilo == "title" or estilo.startswith("título 1"):
                r.append(("titulo", t))
            elif estilo.startswith("heading") or estilo.startswith("título"):
                r.append(("subtitulo", t))
            elif "list" in estilo or "lista" in estilo:
                r.append(("vineta", t))
            else:
                r.append(("parrafo", t))
        elif etiqueta == "tbl":
            t = Table(hijo, d)
            r.append(("tabla", [[c.text.strip() for c in fila.cells] for fila in t.rows]))
    return r


def paginas_pdf(datos: bytes, max_paginas: int = 200) -> List[str]:
    """El texto de cada página de un PDF (pypdfium2)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(datos)
    try:
        r = []
        for i in range(min(len(pdf), max_paginas)):
            pag = pdf[i]
            tp = pag.get_textpage()
            r.append(tp.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n"))
            tp.close()
            pag.close()
        return r
    finally:
        pdf.close()


def cantidad_paginas(datos: bytes) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(datos)
    try:
        return len(pdf)
    finally:
        pdf.close()


# ------------------------------------------------------ unir y dividir PDF

def unir_pdfs(lista: Sequence[bytes]) -> bytes:
    import pypdfium2 as pdfium

    nuevo = pdfium.PdfDocument.new()
    abiertos = []
    try:
        for datos in lista:
            src = pdfium.PdfDocument(datos)
            abiertos.append(src)
            nuevo.import_pages(src)
        s = io.BytesIO()
        nuevo.save(s)
        return s.getvalue()
    finally:
        for x in abiertos:
            x.close()
        nuevo.close()


def rangos_de(texto: str, total: int) -> List[List[int]]:
    """«1-3, 4, 7-9» → [[0,1,2], [3], [6,7,8]] (índices desde 0). ValueError si no se entiende o se sale."""
    r: List[List[int]] = []
    for parte in re.split(r"[,;]", str(texto or "")):
        parte = parte.strip()
        if not parte:
            continue
        m = re.fullmatch(r"(\d+)\s*(?:-\s*(\d+|fin))?", parte, re.I)
        if not m:
            raise ValueError(f"«{parte}» no es una página ni un rango (p. ej. «1-3, 4, 7-fin»)")
        a = int(m.group(1))
        b = total if (m.group(2) or "").lower() == "fin" else int(m.group(2) or a)
        if a < 1 or b < a or b > total:
            raise ValueError(f"«{parte}»: el documento tiene {total} página(s)")
        r.append(list(range(a - 1, b)))
    if not r:
        raise ValueError("no hay rangos")
    return r


def dividir_pdf(datos: bytes, grupos: Sequence[Sequence[int]]) -> List[bytes]:
    import pypdfium2 as pdfium

    src = pdfium.PdfDocument(datos)
    try:
        r = []
        for g in grupos:
            nuevo = pdfium.PdfDocument.new()
            try:
                nuevo.import_pages(src, list(g))
                s = io.BytesIO()
                nuevo.save(s)
                r.append(s.getvalue())
            finally:
                nuevo.close()
        return r
    finally:
        src.close()


def pdf_a_png(datos: bytes, paginas: Optional[Sequence[int]] = None, escala: float = 1.5) -> List[bytes]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(datos)
    try:
        r = []
        for i in (paginas if paginas is not None else range(len(pdf))):
            pag = pdf[i]
            img = pag.render(scale=escala).to_pil()
            s = io.BytesIO()
            img.save(s, format="PNG")
            r.append(s.getvalue())
            pag.close()
        return r
    finally:
        pdf.close()


# ------------------------------------------------------------ conversiones

def jpeg_de_imagen(datos: bytes) -> Tuple[bytes, int, int]:
    from PIL import Image

    img = Image.open(io.BytesIO(datos))
    img = img.convert("RGB")
    s = io.BytesIO()
    img.save(s, format="JPEG", quality=88)
    return s.getvalue(), img.width, img.height


def _texto_de(datos: bytes) -> str:
    for cod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return datos.decode(cod)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", errors="replace")


EXT_IMAGEN = ("png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff")
# desde → hacia que se pueden hacer sin LibreOffice
CONVERSIONES: Dict[str, Tuple[str, ...]] = {
    "docx": ("pdf", "txt", "md"), "pdf": ("txt", "md", "docx", "png"), "txt": ("pdf", "docx"), "md": ("pdf", "docx"),
    "xlsx": ("csv", "pdf"), "csv": ("xlsx", "pdf"), "imagen": ("pdf",), "json": ("txt",), "html": ("txt", "pdf"),
}


def familia(ext: str) -> str:
    e = (ext or "").lower().lstrip(".")
    if e in EXT_IMAGEN:
        return "imagen"
    if e in ("htm", "html"):
        return "html"
    if e in ("markdown",):
        return "md"
    if e in ("xlsm",):
        return "xlsx"
    if e in ("text", "log"):
        return "txt"
    return e


def _filas_xlsx(datos: bytes) -> List[List[Any]]:
    import openpyxl

    libro = openpyxl.load_workbook(io.BytesIO(datos), read_only=True, data_only=True)
    try:
        return [["" if x is None else x for x in f] for f in libro.worksheets[0].iter_rows(values_only=True)]
    finally:
        libro.close()


def _filas_csv(datos: bytes) -> List[List[str]]:
    texto = _texto_de(datos)
    primera = texto.splitlines()[0] if texto else ""
    sep = ";" if primera.count(";") > primera.count(",") else ("\t" if "\t" in primera else ",")
    return [f for f in csv.reader(io.StringIO(texto), delimiter=sep)]


def convertir(datos: bytes, desde: str, hacia: str, titulo: str = "") -> List[Tuple[bytes, str]]:
    """[(bytes, extensión)] del documento convertido (una por página en pdf → png). ValueError si no se puede."""
    f, a = familia(desde), hacia.lower().lstrip(".")
    if a not in CONVERSIONES.get(f, ()):
        posibles = ", ".join(f"{k} → {'/'.join(v)}" for k, v in CONVERSIONES.items())
        raise ValueError(f"no se puede convertir de {desde} a {hacia} sin LibreOffice (se puede: {posibles})")
    if f == "docx":
        bloques = bloques_docx(datos)
        if a == "pdf":
            return [(pdf_de_bloques(bloques, titulo), "pdf")]
        return [(_bloques_a_texto(bloques, a == "md").encode("utf-8"), a)]
    if f == "pdf":
        if a == "png":
            return [(x, "png") for x in pdf_a_png(datos, range(min(cantidad_paginas(datos), 20)))]
        pags = paginas_pdf(datos)
        if a == "docx":
            bloques: List[Tuple[str, Any]] = []
            for i, t in enumerate(pags, 1):
                bloques.append(("subtitulo", f"Página {i}"))
                bloques += [("parrafo", p.strip()) for p in re.split(r"\n\s*\n", t) if p.strip()]
            return [(docx_de_bloques(bloques, titulo), "docx")]
        return [("\n\n".join((f"## Página {i}\n\n" if a == "md" else "") + t for i, t in enumerate(pags, 1)).encode("utf-8"), a)]
    if f in ("txt", "md", "json", "html"):
        texto = _texto_de(datos)
        if f == "html":
            texto = re.sub(r"<[^>]+>", " ", re.sub(r"<(style|script)[\s\S]*?</\1>", " ", texto, flags=re.I))
            texto = re.sub(r"[ \t]+", " ", texto.replace("&nbsp;", " ").replace("&amp;", "&"))
        if a == "txt":
            return [(texto.encode("utf-8"), "txt")]
        bloques = bloques_de_texto(texto) if f == "md" else [("parrafo", p.strip()) for p in re.split(r"\n\s*\n", texto) if p.strip()]
        return [(pdf_de_bloques(bloques, titulo) if a == "pdf" else docx_de_bloques(bloques, titulo), a)]
    if f in ("xlsx", "csv"):
        filas = _filas_xlsx(datos) if f == "xlsx" else _filas_csv(datos)
        if a == "csv":
            s = io.StringIO()
            csv.writer(s).writerows(filas)
            return [(s.getvalue().encode("utf-8"), "csv")]
        if a == "xlsx":
            import openpyxl

            libro = openpyxl.Workbook()
            h = libro.active
            for fila in filas:
                h.append(list(fila))
            s = io.BytesIO()
            libro.save(s)
            return [(s.getvalue(), "xlsx")]
        return [(pdf_de_bloques([("tabla", [[str(x) for x in fila] for fila in filas[:2000]])], titulo), "pdf")]
    if f == "imagen":
        jpeg, w, h = jpeg_de_imagen(datos)
        return [(pdf_de_bloques([("imagen", (jpeg, w, h))], titulo), "pdf")]
    raise ValueError(f"no se puede convertir de {desde} a {hacia}")


def _bloques_a_texto(bloques: List[Tuple[str, Any]], md: bool) -> str:
    partes = []
    for tipo, v in bloques:
        if tipo == "tabla":
            partes.append("\n".join(("| " + " | ".join(str(c) for c in f) + " |") if md else "\t".join(str(c) for c in f) for f in v))
        elif tipo == "titulo":
            partes.append(f"# {v}" if md else str(v).upper())
        elif tipo == "subtitulo":
            partes.append(f"## {v}" if md else str(v))
        elif tipo == "vineta":
            partes.append(f"- {v}")
        else:
            partes.append(str(v))
    return "\n\n".join(partes)
