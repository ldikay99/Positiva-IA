# -*- coding: utf-8 -*-
"""
Genera `arquitectura-mcp.pdf`, el documento de referencia tecnica del pipeline
(`pipeline/`, `servidor_mcp/`, `correr.py`, `harness/`).

    pip install reportlab
    python docs/arquitectura.py

`reportlab` no esta en `requirements.txt`: solo hace falta para construir este
documento, no para ejecutar el pipeline, y la imagen del contenedor no tiene por
que cargar con el.

El PDF se versiona junto a este archivo para que quien lo lea no tenga que
generarlo, y este archivo se versiona para que el PDF se pueda regenerar cuando
el codigo cambie.
"""
import math
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageBreak, PageTemplate,
    Paragraph, Preformatted, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

SALIDA = Path(__file__).with_name("arquitectura-mcp.pdf")

PAGE_W, PAGE_H = A4
MARGIN = 2.2 * cm

# ─────────────────────────── Estilos ───────────────────────────

styles = getSampleStyleSheet()

AZUL = colors.HexColor("#1a3a5c")
AZUL_CLARO = colors.HexColor("#eef3f8")
GRIS = colors.HexColor("#555555")
GRIS_CLARO = colors.HexColor("#f2f2f0")
BORDE = colors.HexColor("#c9c9c9")

styles.add(ParagraphStyle(
    name="Portada", fontName="Helvetica-Bold", fontSize=25, leading=30,
    textColor=AZUL, alignment=TA_LEFT, spaceAfter=6,
))
styles.add(ParagraphStyle(
    name="Subportada", fontName="Helvetica", fontSize=13, leading=18,
    textColor=GRIS, alignment=TA_LEFT, spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="H1", fontName="Helvetica-Bold", fontSize=17, leading=21,
    textColor=AZUL, spaceBefore=22, spaceAfter=10,
    keepWithNext=True,
))
styles.add(ParagraphStyle(
    name="H2", fontName="Helvetica-Bold", fontSize=12.5, leading=16,
    textColor=AZUL, spaceBefore=14, spaceAfter=6,
    keepWithNext=True,
))
styles.add(ParagraphStyle(
    name="H3", fontName="Helvetica-Bold", fontSize=10.5, leading=13,
    textColor=colors.HexColor("#2f2f2f"), spaceBefore=10, spaceAfter=4,
    keepWithNext=True,
))
styles.add(ParagraphStyle(
    name="Body", fontName="Helvetica", fontSize=9.6, leading=13.6,
    alignment=TA_JUSTIFY, spaceAfter=6, textColor=colors.HexColor("#1a1a1a"),
))
styles.add(ParagraphStyle(
    name="BodyTight", parent=styles["Body"], spaceAfter=2,
))
styles.add(ParagraphStyle(
    name="Vineta", parent=styles["Body"], leftIndent=14, bulletIndent=2,
    spaceAfter=3,
))
styles.add(ParagraphStyle(
    name="Nota", parent=styles["Body"], fontName="Helvetica-Oblique",
    fontSize=8.8, textColor=GRIS, leftIndent=8, spaceBefore=2,
))
styles.add(ParagraphStyle(
    name="CodigoMono", fontName="Courier", fontSize=7.9, leading=10.3,
    textColor=colors.HexColor("#1a1a1a"),
))
styles.add(ParagraphStyle(
    name="TOCH1", fontName="Helvetica-Bold", fontSize=11, leading=15,
    textColor=AZUL, spaceBefore=8,
))
styles.add(ParagraphStyle(
    name="TOCH2", fontName="Helvetica", fontSize=9.5, leading=13,
    leftIndent=14, textColor=colors.HexColor("#333333"),
))
styles.add(ParagraphStyle(
    name="TableHead", fontName="Helvetica-Bold", fontSize=8.7, leading=11,
    textColor=colors.white,
))
styles.add(ParagraphStyle(
    name="TableCell", fontName="Helvetica", fontSize=8.7, leading=11.4,
))
styles.add(ParagraphStyle(
    name="TableCellMono", fontName="Courier", fontSize=8.0, leading=10.4,
))

# ─────────────────────────── Utilidades de texto ───────────────────────────

def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def md(s: str) -> str:
    """Escapa y aplica un marcado minimo: **negrita** y `codigo`."""
    t = esc(s)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`]+)`", r'<font face="Courier" size="8.6">\1</font>', t)
    return t


def P(text, style="Body"):
    return Paragraph(md(text), styles[style])


def cell(text, mono=False):
    st = "TableCellMono" if mono else "TableCell"
    return Paragraph(md(text), styles[st])


def head(text):
    return Paragraph(md(text), styles["TableHead"])

# ─────────────────────────── Documento con TOC y numeracion ───────────────────────────

class Documento(BaseDocTemplate):
    def __init__(self, filename, **kw):
        super().__init__(filename, pagesize=A4, **kw)
        frame = Frame(MARGIN, MARGIN, PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN,
                       id="normal", topPadding=6, bottomPadding=10)
        self.addPageTemplates([
            PageTemplate(id="Portada", frames=[frame], onPage=self._pie_portada),
            PageTemplate(id="normal", frames=[frame], onPage=self._pie_normal),
        ])

    def _marco(self, canv, doc, mostrar_pagina):
        canv.saveState()
        canv.setStrokeColor(BORDE)
        canv.setLineWidth(0.4)
        canv.line(MARGIN, PAGE_H - MARGIN + 14, PAGE_W - MARGIN, PAGE_H - MARGIN + 14)
        canv.setFont("Helvetica", 7.6)
        canv.setFillColor(GRIS)
        canv.drawString(MARGIN, PAGE_H - MARGIN + 18,
                         "Arquitectura del pipeline PQRD — mcp/pipeline")
        canv.line(MARGIN, MARGIN - 12, PAGE_W - MARGIN, MARGIN - 12)
        if mostrar_pagina:
            canv.drawRightString(PAGE_W - MARGIN, MARGIN - 24, f"Página {doc.page}")
        canv.drawString(MARGIN, MARGIN - 24, "Positiva ARL — documento de referencia técnica")
        canv.restoreState()

    def _pie_portada(self, canv, doc):
        pass

    def _pie_normal(self, canv, doc):
        self._marco(canv, doc, mostrar_pagina=True)

    def afterFlowable(self, flowable):
        if not isinstance(flowable, Paragraph):
            return
        style = flowable.style.name
        text = flowable.getPlainText()
        if style == "H1":
            self.notify("TOCEntry", (0, text, self.page))
            key = f"h1-{self.page}-{abs(hash(text)) % 100000}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=0, closed=False)
        elif style == "H2":
            self.notify("TOCEntry", (1, text, self.page))
            key = f"h2-{self.page}-{abs(hash(text)) % 100000}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=1, closed=True)


def caja_codigo(texto: str, ancho_col=None):
    """Bloque monoespaciado con fondo, para diagramas y fragmentos de codigo."""
    pre = Preformatted(texto, styles["CodigoMono"])
    t = Table([[pre]], colWidths=[PAGE_W - 2 * MARGIN - 0.2 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GRIS_CLARO),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDE),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


ANCHO_DIAGRAMA = PAGE_W - 2 * MARGIN


def flecha(dibujo, x1, y1, x2, y2, color=None, ancho_linea=1.0):
    """Linea recta con una punta de flecha triangular en (x2, y2)."""
    color = color or GRIS
    dibujo.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=ancho_linea))
    angulo = math.atan2(y2 - y1, x2 - x1)
    tam, apertura = 4.5, math.radians(24)
    p1x = x2 - tam * math.cos(angulo - apertura)
    p1y = y2 - tam * math.sin(angulo - apertura)
    p2x = x2 - tam * math.cos(angulo + apertura)
    p2y = y2 - tam * math.sin(angulo + apertura)
    dibujo.add(Polygon([x2, y2, p1x, p1y, p2x, p2y], fillColor=color, strokeColor=color))


def caja(dibujo, x, y, w, h, titulo, subtitulo="", relleno=None, borde=None, color_texto=None):
    """Caja rotulada, con un titulo en negrita y, si se da, un subtitulo monoespaciado."""
    relleno = relleno if relleno is not None else AZUL_CLARO
    borde = borde if borde is not None else AZUL
    color_texto = color_texto if color_texto is not None else AZUL
    dibujo.add(Rect(x, y, w, h, rx=4, ry=4, fillColor=relleno, strokeColor=borde, strokeWidth=1))
    cx = x + w / 2
    if subtitulo:
        dibujo.add(String(cx, y + h / 2 + 4, titulo, fontName="Helvetica-Bold",
                           fontSize=7.9, fillColor=color_texto, textAnchor="middle"))
        dibujo.add(String(cx, y + h / 2 - 8, subtitulo, fontName="Courier",
                           fontSize=6.7, fillColor=GRIS, textAnchor="middle"))
    else:
        dibujo.add(String(cx, y + h / 2 - 3, titulo, fontName="Helvetica-Bold",
                           fontSize=8.0, fillColor=color_texto, textAnchor="middle"))


def etiqueta(dibujo, x, y, texto, color=None, tam=6.6, ancla="middle", cursiva=False):
    """Texto suelto junto a una flecha (una condicion, un resultado)."""
    color = color or GRIS
    fuente = "Helvetica-Oblique" if cursiva else "Helvetica"
    dibujo.add(String(x, y, texto, fontName=fuente, fontSize=tam, fillColor=color, textAnchor=ancla))


def diagrama_capas():
    """Las tres capas del pipeline: extraccion determinista, agente unico, compuerta."""
    ancho = ANCHO_DIAGRAMA
    alto = 258
    d = Drawing(ancho, alto)
    cx = ancho / 2

    d.add(String(cx, alto - 8, "Correo de Outlook (+ adjuntos)", fontName="Helvetica-Bold",
                  fontSize=8.4, fillColor=AZUL, textAnchor="middle"))
    flecha(d, cx, alto - 14, cx, alto - 28)

    caja(d, 8, 198, ancho - 16, 32, "1. Extracción determinista", "extraccion.py :: extraer_todo()")

    x_izq = ancho * 0.26
    x_der = ancho * 0.72
    agente_w = ancho * 0.46
    agente_x = x_der - agente_w / 2

    flecha(d, x_izq, 198, x_izq, 96)
    etiqueta(d, x_izq + 4, 172, "Hallazgo(valor,", ancla="start")
    etiqueta(d, x_izq + 4, 163, "evidencia, confianza)", ancla="start")

    flecha(d, x_der, 198, x_der, 162)
    etiqueta(d, x_der + 4, 182, "NoResuelto(motivo)", ancla="start")
    caja(d, agente_x, 130, agente_w, 32, "2. Agente único", "agente.py :: resolver()")

    flecha(d, x_der, 130, x_der, 96)

    caja(d, 8, 64, ancho - 16, 32, "3. Compuerta de validación", "compuerta.py :: validar()")

    left_w = right_w = ancho * 0.42
    left_x = 8
    right_x = ancho - 8 - right_w
    x_ok = left_x + left_w / 2
    x_no = right_x + right_w / 2
    flecha(d, x_ok, 64, x_ok, 38)
    flecha(d, x_no, 64, x_no, 38)
    etiqueta(d, x_ok + 5, 50, "APROBADO / CORREGIDO", tam=6.4, ancla="start")
    etiqueta(d, x_no - 5, 50, "RECHAZADO", tam=6.4, ancla="end")

    caja(d, left_x, 8, left_w, 30, "RADICAR en SGDEA")
    caja(d, right_x, 8, right_w, 30, "REVISIÓN MANUAL", "correo etiquetado con el motivo")

    return d


def diagrama_flujo_correo():
    """El recorrido completo de un correo: deduplicacion, enrutamiento, extraccion, compuerta."""
    ancho = ANCHO_DIAGRAMA
    main_w = ancho * 0.56
    main_x = 6
    side_w = ancho * 0.32
    side_x = ancho - 6 - side_w
    cx = main_x + main_w / 2

    H1 = 18          # fila de una sola linea
    H2 = 26          # fila con titulo y subtitulo
    H_GRANDE = 30    # enrutamiento y compuerta: titulo y subtitulo mas largo
    GAP = 10         # flecha entre filas consecutivas

    alto = 570
    d = Drawing(ancho, alto)

    def fila(y_top, h, titulo, sub=""):
        y_bottom = y_top - h
        caja(d, main_x, y_bottom, main_w, h, titulo, sub)
        return y_bottom

    def fila_capa(y_top, h, titulo, sub, texto_lateral):
        y_bottom = fila(y_top, h, titulo, sub)
        y_mid = y_bottom + h / 2
        flecha(d, main_x + main_w, y_mid, side_x, y_mid)
        caja(d, side_x, y_mid - h / 2, side_w, h, texto_lateral,
             relleno=GRIS_CLARO, borde=BORDE, color_texto=GRIS)
        return y_bottom

    y = fila(alto - 6, H1, "Outlook — bandeja de entrada")

    for titulo, sub in [
        ("Capa 1", "etiqueta de radicado en el propio correo"),
        ("Capa 2", "hilo ya visto en este mismo lote"),
        ("Capa 3", "hilo (conversationId) ya radicado, según Graph"),
        ("Capa 4", "asunto ya radicado, según Graph"),
    ]:
        flecha(d, cx, y, cx, y - GAP)
        y = fila_capa(y - GAP, H2, titulo, sub, "DUPLICADO")

    flecha(d, cx, y, cx, y - GAP)
    y_bottom = fila(y - GAP, H_GRANDE, "Enrutamiento",
                     "decidir(): RADICAR, JUDICIAL o MEDICINA_LABORAL")
    y_mid = y_bottom + H_GRANDE / 2
    flecha(d, main_x + main_w, y_mid, side_x, y_mid)
    caja(d, side_x, y_mid - H_GRANDE / 2, side_w, H_GRANDE, "Reenviar",
         "a la oficina correspondiente",
         relleno=GRIS_CLARO, borde=BORDE, color_texto=GRIS)
    y = y_bottom

    flecha(d, cx, y, cx, y - GAP)
    y = fila(y - GAP, H1, "Descargar adjuntos")

    for titulo, sub in [
        ("Capa 5", "hash de contenido en la caché (Redis)"),
        ("Capa 6", "remitente + asunto en la auditoría (Supabase)"),
    ]:
        flecha(d, cx, y, cx, y - GAP)
        y = fila_capa(y - GAP, H2, titulo, sub, "DUPLICADO")

    for titulo, sub in [
        ("Materializar", "workspace: correo.md, hilo.md, adjuntos/*.md"),
        ("Saneamiento", "detectar intentos de inyección, anotar sin borrar"),
    ]:
        flecha(d, cx, y, cx, y - GAP)
        y = fila(y - GAP, H2, titulo, sub)

    flecha(d, cx, y, cx, y - GAP)
    y -= GAP
    separacion = 22
    mitad = (main_w - separacion) / 2
    caja(d, main_x, y - H1, mitad, H1, "Extracción determinista")
    caja(d, main_x + mitad + separacion, y - H1, mitad, H1, "Agente único")
    flecha(d, main_x + mitad, y - H1 / 2, main_x + mitad + separacion, y - H1 / 2)
    y -= H1

    flecha(d, cx, y, cx, y - GAP)
    y = fila(y - GAP, H2, "Relleno desde el registro de personas", "SGDEA, solo huecos")

    flecha(d, cx, y, cx, y - GAP)
    y_bottom = fila(y - GAP, H_GRANDE, "Compuerta de validación", "compuerta.py :: validar()")
    y_mid = y_bottom + H_GRANDE / 2

    x_ok = main_x + main_w * 0.27
    flecha(d, x_ok, y_bottom, x_ok, y_bottom - 18)
    etiqueta(d, x_ok + 3, y_bottom - 11, "aprobado / corregido", tam=6.0, ancla="start")

    flecha(d, main_x + main_w, y_mid, side_x, y_mid)
    caja(d, side_x, y_mid - H_GRANDE / 2, side_w, H_GRANDE, "Revisión manual",
         "correo etiquetado con el motivo",
         relleno=GRIS_CLARO, borde=BORDE, color_texto=GRIS)
    etiqueta(d, (main_x + main_w + side_x) / 2, y_mid + 5, "rechazado", tam=6.0)

    y = fila(y_bottom - 18, H1, "Radicar en SGDEA")

    flecha(d, cx, y, cx, y - GAP)
    fila(y - GAP, H2, "Registrar hash + etiquetar + mover", 'a "radicación exitosa"')

    return d


def tabla(encabezados, filas, anchos=None, mono_cols=()):
    datos = [[head(h) for h in encabezados]]
    for fila in filas:
        datos.append([
            cell(str(v), mono=(i in mono_cols)) for i, v in enumerate(fila)
        ])
    if anchos is None:
        ancho_total = PAGE_W - 2 * MARGIN
        anchos = [ancho_total / len(encabezados)] * len(encabezados)
    t = Table(datos, colWidths=anchos, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, AZUL_CLARO]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def bullets(items):
    return [P("•  " + it, "Vineta") for it in items]


def h1(text):
    return Paragraph(md(text), styles["H1"])


def h2(text):
    return Paragraph(md(text), styles["H2"])


def h3(text):
    return Paragraph(md(text), styles["H3"])

# ─────────────────────────── Contenido ───────────────────────────

story = []

# Portada
story.append(Spacer(1, 5.5 * cm))
story.append(P("Arquitectura del pipeline de radicación de PQRD", "Portada"))
story.append(P("Positiva ARL — extracción determinista, agente único y compuerta de validación", "Subportada"))
story.append(Spacer(1, 0.6 * cm))
story.append(P("Documento de referencia técnica del código en `mcp/pipeline`, "
               "`mcp/servidor_mcp`, `mcp/correr.py` y `mcp/harness`.", "Subportada"))
story.append(Spacer(1, 8 * cm))
story.append(PageBreak())

# TOC
story.append(h1("Contenido"))
toc = TableOfContents()
toc.levelStyles = [styles["TOCH1"], styles["TOCH2"]]
story.append(toc)
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 1. QUE HACE EL SISTEMA
# ═══════════════════════════════════════════════════════════════
story.append(h1("1. Qué hace el sistema"))

story.append(P(
    "Positiva ARL recibe en el buzón `servicioalcliente@positiva.gov.co` peticiones, quejas, "
    "reclamos y denuncias — PQRD — de afiliados, empleadores y terceros. El pipeline lee esos "
    "correos, extrae los datos que exige el backend SGDEA (identidad del afectado, identidad de "
    "quien escribe, tipo de trámite, tipo de PQRD, oficina destino) y radica el caso, o lo envía a "
    "revisión manual cuando no logra reunir lo obligatorio con suficiente certeza."
))
story.append(P(
    "Corre como un Cloud Run Job disparado cada 30 minutos. `correr.py` es su punto de entrada: "
    "cada ejecución toma un lote de correos de la bandeja de entrada (Microsoft Graph, a través de "
    "`OutlookService`), los procesa de a varios a la vez dentro de un presupuesto de tiempo "
    "(sección 12) y cierra con un informe de la corrida (`pipeline/telemetria.py`)."
))

story.append(h2("Entrada y salida"))
story.append(P(
    "**Entrada**: un correo — asunto, cuerpo, remitente, hilo — más sus adjuntos, que llegan en "
    "cualquier combinación de PDF nativo o escaneado, imagen, Word, Excel, ZIP o sobre firmado "
    "digitalmente (`.p7m`)."
))
story.append(P(
    "**Salida**: para cada correo, uno de ocho desenlaces — `radicado`, `duplicado`, `reenviado`, "
    "`aplazado`, `manual`, `error`, `en_proceso` o `sin_reclamar` — que queda registrado en "
    "`pipeline/orquestador.py::Resultado`. Un correo radicado "
    "recibe un número de radicado de SGDEA, se etiqueta y se mueve a la carpeta de radicación "
    "exitosa. Uno enviado a revisión manual queda etiquetado con el motivo "
    "(`REVISION-MANUAL:<motivo>`) para que una persona lo complete."
))

story.append(h2("Frente al esquema anterior"))
story.append(P(
    "Este pipeline reemplaza un esquema que enviaba el correo completo a siete agentes de Gemini en "
    "paralelo, uno por campo (`orchestrator/pqrd_orchestrator.py`, `services/gemini_agents.py`). Su "
    "idea central es la contraria: resolver primero con reglas deterministas todo lo que se pueda "
    "resolver sin modelo, y usar un solo agente, con herramientas, únicamente para lo que quede "
    "pendiente."
))
story.append(tabla(
    ["Aspecto", "Pipeline antiguo", "Pipeline nuevo"],
    [
        ["Punto de entrada", "main.py", "correr.py"],
        ["Extracción", "7 agentes Gemini en paralelo", "Reglas deterministas + 1 agente"],
        ["Qué recibe el modelo", "El correo completo, por cada campo", "Solo lo que quedó pendiente"],
        ["Validación previa a radicar", "Dispersa en el orquestador", "pipeline/compuerta.py, un solo punto"],
    ],
    anchos=[4.6 * cm, 5.6 * cm, 6.0 * cm],
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 2. ARQUITECTURA EN CAPAS
# ═══════════════════════════════════════════════════════════════
story.append(h1("2. Arquitectura en capas"))

story.append(P(
    "El pipeline resuelve cada correo en tres capas estrictamente secuenciales. Cada una "
    "existe para hacer una sola cosa y se niega explícitamente a hacer las demás."
))

story.append(diagrama_capas())

story.append(h2("Capa 1 — extracción determinista"))
story.append(P(
    "Corre siempre, sobre todos los campos, y no cuesta llamada a modelo. Cada campo tiene su "
    "propio extractor (`extraer_documento`, `extraer_nombre_afectado`, `extraer_sexo`, etc.), y "
    "cada uno devuelve un par: un `Hallazgo` con su evidencia y su confianza, o un `NoResuelto` con "
    "el motivo exacto por el que no se pudo decidir. Se niega a interpretar significado: reconoce "
    "patrones, marcas textuales y cercanía entre elementos del texto, pero no infiere una intención "
    "que el texto no declara. Cuando hay ambigüedad real, no elige: abstiene."
))

story.append(h2("Capa 2 — el agente único"))
story.append(P(
    "Solo se invoca si, tras la capa 1, queda algo por resolver que valga la pena preguntarle "
    "(`Extraccion.campos_a_buscar`, sección 4). Recibe una tarea acotada — no el correo completo — "
    "y un juego de herramientas de solo lectura sobre el workspace del correo. Se niega a tocar "
    "campos ya resueltos, a inventar un valor sin evidencia y a tener cualquier efecto secundario: "
    "la única herramienta de escritura que tiene, `reportar_hallazgo`, solo acumula en memoria — no "
    "radica ni modifica nada."
))

story.append(h2("Capa 3 — la compuerta de validación"))
story.append(P(
    "Es el único punto por el que pasa todo hallazgo — venga del algoritmo, del agente o del "
    "registro de personas — antes de que algo se escriba en SGDEA. Verifica coherencia entre "
    "campos, no cada campo aislado: un documento con forma válida y un nombre con forma válida "
    "pueden seguir siendo incoherentes entre sí (un tipo de remitente que dice \"es el afectado\" "
    "con dos nombres que no coinciden, por ejemplo). Es enteramente determinista — no hace ninguna "
    "llamada a modelo — y se niega a dejar pasar un dato sin evidencia o por debajo del piso de "
    "confianza que le corresponde a ese campo."
))

story.append(h2("Qué evita este diseño"))
story.append(P(
    "La capa determinista nunca decide por mayoría de indicios débiles: o hay una señal "
    "suficientemente clara — un candidato único, una marca textual inequívoca, una cercanía muy "
    "superior a la del segundo candidato — o el campo queda pendiente con su motivo. El agente "
    "nunca ve el correo completo salvo que de verdad lo necesite pieza por pieza a través de "
    "`buscar_en_documentos` y `leer_documento`, lo que mantiene la ventana de contexto pequeña y el "
    "consumo de tokens bajo (sección 5 y 9). La compuerta nunca corrige silenciosamente: toda "
    "corrección queda anotada en el dictamen y aparece en el informe de la corrida."
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 3. EL FLUJO DE UN CORREO
# ═══════════════════════════════════════════════════════════════
story.append(h1("3. El flujo de un correo"))
story.append(P(
    "`pipeline/orquestador.py::Orquestador.procesar()` ejecuta el pipeline sobre un correo en un "
    "orden fijo: deduplicar, materializar, sanear, extraer, resolver, validar, radicar. Cada etapa "
    "se cronometra (`Resultado.cronometrar`) y cada fallo se registra en una `Bitacora` "
    "(sección 8) en lugar de perderse."
))

story.append(diagrama_flujo_correo())

story.append(h2("Las seis capas de deduplicación"))
story.append(P(
    "`pipeline/duplicados.py::Detector` las corre en orden de costo, todas antes de invocar el "
    "modelo. Las cuatro primeras (`comprobar`) no necesitan los adjuntos; las dos últimas "
    "(`comprobar_contenido`) sí, porque el hash incluye sus nombres. Cada servicio (Outlook, cache, "
    "auditoría) se inyecta y puede faltar: si falta o falla, esa capa se pierde pero el correo se "
    "procesa igual, y la pérdida queda anotada en `motivo` — nunca se pierde en silencio."
))
story.append(P(
    "La sexta pregunta por un par —remitente y asunto— que NO identifica un caso: bajo el asunto "
    "genérico de la app entran PQRD distintas de la misma persona, y darlas por repetidas dejaba de "
    "radicarlas. Medido sobre el correo real de un periodo: agrupaba 193 correos y solo 56 eran "
    "repeticiones. Por eso su respuesta ya no basta — solo decide cuando la capa del contenido no "
    "pudo responder — y el asunto se limpia antes de preguntar, porque un reenvío cambia el texto "
    "sin cambiar el caso.",
    "Nota"
))
story.append(P(
    "La capa 5 está construida pero hoy no se usa: el servidor de caché al que apuntaba dejó de "
    "resolver en DNS, de modo que la capa no comprobaba nada y cada correo arrastraba un aviso de "
    "degradación. `correr.py` ya no construye ese cliente. La exclusión entre ejecuciones no depende "
    "de ella, sino de la reclamación que se describe a continuación, que no necesita ningún servicio "
    "externo.", "Nota"
))
story.append(tabla(
    ["Capa", "Qué comprueba", "Fuente", "Necesita red"],
    [
        ["1", "El propio correo ya lleva etiqueta de radicado", "categories del correo", "No"],
        ["2", "Otro correo del mismo hilo ya se procesó en este lote", "memoria del proceso", "No"],
        ["3", "El hilo (por conversationId) ya fue radicado", "Microsoft Graph", "Sí"],
        ["4", "El asunto (limpio) ya fue radicado", "Microsoft Graph", "Sí"],
        ["5", "El hash de asunto+remitente+adjuntos ya existe", "caché (fuera de uso)", "—"],
        ["6", "Ya hay un registro con ese remitente y ese asunto limpio", "auditoría Supabase", "Sí"],
    ],
    anchos=[1.3 * cm, 8.0 * cm, 4.0 * cm, 2.9 * cm],
))
story.append(P(
    "La capa 4 existe aparte de la 3 porque un reenvío interno abre un `conversationId` nuevo: la "
    "comprobación por hilo deja de ver el radicado, pero el asunto —limpio de prefijos de reenvío— "
    "sigue siendo el mismo, así que la búsqueda por asunto en Graph todavía lo encuentra."
))
story.append(P(
    "Las dos consultas a Graph esperan lo que pide un 429 en `Retry-After` y reintentan, hasta "
    "cuatro veces. Son de lectura y repetirlas no tiene efecto, mientras que rendirse ante el primer "
    "429 apaga la capa: apagada dice \"no hay duplicado\", que es justamente lo que no se comprobó. "
    "`$search` —la capa 4— es el endpoint que Graph limita antes, y el límite se alcanza cuando el "
    "lote es grande, que es cuando la capa hace más falta."
))
story.append(P(
    "El hash de contenido (`duplicados.py::hash_del_correo`, que delega en `utils.hash`) se calcula "
    "sobre el asunto sin prefijos de reenvío, el remitente y los nombres de los adjuntos ordenados: "
    "sin limpiar los prefijos, \"RV: RE: Solicitud\" y \"Solicitud\" generarían llaves distintas y "
    "el mismo caso se radicaría una vez por cada reenvío interno."
))

story.append(h2("Reclamar el correo antes de trabajarlo"))
story.append(P(
    "Las seis capas anteriores son de **lectura**, y las marcas que las alimentan —la etiqueta con "
    "el número de radicado y el hash en la caché— se escriben al final del proceso. Entre que SGDEA "
    "devuelve el radicado y que el correo queda etiquetado transcurren la conversión, el OCR y el "
    "modelo: en esa ventana cabe otra ejecución completa, y el mismo caso puede radicarse dos veces."
))
story.append(P(
    "`pipeline/reclamo.py` la cierra por los dos extremos. Al pasar la deduplicación se escribe "
    "`EN-PROCESO:N` en el correo, **antes** de descargar adjuntos y de invocar al modelo, de modo "
    "que ningún tramo del proceso queda sin rastro. Y justo antes de radicar —lo único del pipeline "
    "que no se puede deshacer— se relee el correo y se comprueba si otra ejecución ya le puso "
    "etiqueta de radicado; si la tiene, se devuelve ese número en lugar de radicar de nuevo."
))
story.append(P(
    "Esa escritura es **atómica**. `OutlookService.marcar()` lee el correo con su ETag, calcula la "
    "lista de categorías que corresponde y la escribe con `If-Match` sobre ese ETag. Si otra "
    "ejecución escribió en medio, Graph responde 412 y se lanza `OtraCorridaLoTomo`: el correo se "
    "deja intacto, con desenlace `en_proceso`, sin convertir adjuntos, sin invocar al modelo, sin "
    "radicar y sin dejar aviso —lo deja la ejecución que ganó la reclamación—. Es la única exclusión "
    "mutua real entre dos ejecuciones que miran la misma bandeja."
))
story.append(P(
    "La marca de reclamación se escribe SIEMPRE, aunque el valor a escribir coincida con el que ya "
    "está puesto (`marcas.reclama_el_correo`). Lo que reclama el correo no es el texto de la marca "
    "sino el PATCH con `If-Match`: dos ejecuciones que arrancan a la vez sobre un correo nunca "
    "tocado calculan las dos `EN-PROCESO:1`, y si la escritura se omite por coincidir, ninguna "
    "recibe el 412 que le diría que la otra se adelantó. Las dos se creerían dueñas y las dos "
    "radicarían. El atajo se conserva para las demás marcas, donde ahorrar la petición no cuesta "
    "nada.", "Nota"
))
story.append(P(
    "Un correo con desenlace `duplicado` **sale de la bandeja**, a "
    "`OUTLOOK_FOLDER_DUPLICADOS`. No lleva marca de desenlace propia: la que lo identifica ya está "
    "puesta —es la del radicado, que es justamente por la que se detectó— así que solo se retiran "
    "las de proceso. Quedándose en la bandeja se vuelve a tomar en cada corrida: ocupa un cupo del "
    "lote, se vuelve a detectar y se vuelve a avisar, sin llegar nunca a un desenlace que lo saque "
    "de ahí.", "Nota"
))
story.append(P(
    "Que la escritura FALLE es distinto de que otra ejecución se adelante, y también detiene el "
    "trabajo. Si la marca no se puede escribir, el correo no tiene dueño: seguir procesándolo "
    "permitiría que la ejecución siguiente lo tomara también y las dos lo radicaran. Sale como "
    "`ReclamoNoEscrito` y el correo queda con desenlace `sin_reclamar`, tal como llegó y sin ninguna "
    "marca. Es el único estado que no puede producir un duplicado, porque la corrida siguiente lo ve "
    "como nuevo; tampoco deja aviso, ya que no se hizo nada con él."
))
story.append(P(
    "La causa típica de ese fallo es el límite de tasa de Microsoft Graph. Tanto la lectura de las "
    "categorías como su escritura reintentan el 429 esperando lo que pide la cabecera `Retry-After`, "
    "acotado para que la espera no se coma el presupuesto de la corrida. Sin ese reintento, un "
    "límite de tasa deja sin reclamar correos que no tienen nada de malo.", "Nota"
))
story.append(P(
    "El límite es una **cuenta**, no una marca de tiempo: una hora no dice si otra ejecución sigue "
    "viva o murió. Si el correo vuelve con la marca puesta, la ejecución anterior no llegó al final "
    "y se retoma; pasados `MAX_RECLAMOS = 12` intentos va a revisión manual con motivo "
    "`RECLAMO_AGOTADO`. Son los mismos ciclos que `reintentos.MAX_CICLOS`, y por la misma razón: un "
    "correo se queda reclamado sin terminar cuando la ejecución se cayó, y las ejecuciones se caen "
    "por lo mismo que se cae todo —el backend, la red, el modelo—, no por el correo. Con un tope "
    "corto, una caída de horas agotaba el contador de todo lo que estuviera en vuelo. "
    "Si el correo no se puede releer antes de radicar, **se radica igual** y "
    "queda anotado: un fallo de lectura no es evidencia de que otra ejecución lo radicó, y tratarlo "
    "como tal dejaría el caso sin radicar sin que nadie lo supiera.", "Nota"
))

story.append(h2("Las marcas que quedan sobre el correo"))
story.append(P(
    "Microsoft Graph no sabe \"agregar una categoría\": el `PATCH` manda la lista entera, así que "
    "escribir solo la marca nueva borra todo lo demás. `pipeline/marcas.py` agrupa las marcas por "
    "**familia** —identificada por su prefijo— y `fusionar()` sustituye únicamente las de la misma "
    "familia, respetando las demás."
))
story.append(tabla(
    ["Marca", "Qué significa"],
    [
        ["EN-PROCESO:N", "Una ejecución lo tomó y todavía no ha terminado."],
        ["REINTENTO:N", "Se aplazó N veces por fallos que no dicen nada del correo (sección 12)."],
        ["REVISION-MANUAL:X", "Se cerró hacia una persona, con el motivo del catálogo."],
        ["REENVIADO:X", "Se derivó a otra área."],
        ["ENT… / RADICADO:", "Se radicó. Es la marca de la que viven las capas 1, 3 y 4."],
    ],
    anchos=[4.6 * cm, 11.6 * cm],
    mono_cols=(0,),
))
story.append(P(
    "Dos consecuencias. La marca de proceso y el contador de reintentos **conviven** en vez de "
    "sobrescribirse, que es lo que mantiene la cuenta a lo largo de varias corridas. Y lo que no "
    "lleva un prefijo conocido es de alguien más —la categoría que un funcionario asigna a mano "
    "desde el buzón— y no se toca nunca. Al cerrar el correo se retiran las marcas de proceso "
    "(`quitar_de_proceso`): el desenlace ya consta en su propia marca, y dejarlas haría que la "
    "corrida siguiente leyera trabajo a medias donde no lo hay.", "Nota"
))

story.append(h2("Enrutamiento: correos que no son PQRD"))
story.append(P(
    "No todo correo que llega al buzón es una PQRD. `pipeline/enrutamiento.py::decidir()` decide, "
    "antes de descargar adjuntos y antes de invocar al agente, si el correo se queda en el flujo de "
    "radicación o se reenvía a otra área — un correo que se reenvía no necesita OCR ni modelo, así "
    "que la decisión también le ahorra ese costo."
))
story.append(P(
    "Antes incluso de eso, `pipeline/facturas.py::es_factura()` aparta las facturas electrónicas. "
    "Una factura no es la petición de nadie y tampoco es de ningún área, así que no se reenvía: va "
    "a revisión manual con motivo `FACTURA_ELECTRONICA`. La reconoce por la FORMA del asunto con "
    "que la emite un proveedor de facturación (`NIT;EMPRESA;NUMERO;TIPO`, con el separador final "
    "opcional), por el código único que la DIAN pone a cada documento electrónico (`CUFE`/`CUDE`), "
    "o porque el cuerpo se declara documento electrónico."
))
story.append(P(
    "Que el correo venga de un buzón llamado \"facturación\" NO basta: una empresa escribe desde "
    "ese buzón para pedir un trámite propio, y eso sí es una PQRD. Por la misma razón la palabra "
    "\"factura\" a secas no decide — aparece en reclamaciones legítimas por un cobro. Medido sobre "
    "el correo real del periodo, la regla aparta 3 correos y ninguno de los que hoy se radican.",
    "Nota"
))
story.append(P(
    "`decidir()` devuelve un `Enrutamiento` con un `Destino` — `RADICAR`, `MEDICINA_LABORAL` o "
    "`JUDICIAL` —, la dirección de destino y la evidencia que lo decidió. `RADICAR` deja el correo "
    "en su camino normal, hacia la descarga de adjuntos; los otros dos lo sacan del pipeline y lo "
    "reenvían."
))
story.append(tabla(
    ["Destino", "Correo destino", "Cuándo aplica"],
    [
        ["JUDICIAL", "notificacionesjudiciales@positiva.gov.co",
         "El correo llega de un despacho o de un ente de control; o el texto presenta a la "
         "autoridad actuando (\"se notifica\", \"auto admisorio\", \"el juzgado ordena\", "
         "desacato, demanda ordinaria laboral)."],
        ["MEDICINA_LABORAL", "solicitudesmedicinalaboral@positiva.gov.co",
         "El remitente escribe desde @e-entrega.co; o la Junta de Calificación está actuando "
         "(convoca, cita a valoración, emite dictamen, determinación de origen)."],
        ["NO_SE_RADICA", "—",
         "El remitente está en `REMITENTES_QUE_NO_SE_RADICAN`. No se reenvía a ninguna parte: va a "
         "revisión manual con motivo `REMITENTE_EXCLUIDO`."],
        ["RADICAR", "—", "Ninguna de las anteriores: el correo sigue su flujo normal de PQRD."],
    ],
    anchos=[3.4 * cm, 6.4 * cm, 6.4 * cm],
))
story.append(P(
    "`REMITENTES_QUE_NO_SE_RADICAN` lleva direcciones COMPLETAS, nunca dominios. Del dominio de "
    "Positiva llegan PQRD de ciudadanos que un área reenvía, y el remitente real se saca del hilo "
    "citado: excluir el dominio entero las perdería —medido sobre una semana de correo real, serían "
    "94 radicaciones legítimas—. Se comprueba antes que ninguna otra regla, y el correo no se "
    "devuelve a quien lo escribió, que lo dejaría rebotando entre dos buzones."
))
story.append(P(
    "Lo judicial manda sobre lo demás: una tutela sobre un dictamen de pérdida de capacidad laboral "
    "sigue siendo una tutela, con su propio término de respuesta, aunque hable de medicina laboral "
    "de principio a fin. `decidir()` evalúa primero la marca judicial y solo si no la encuentra "
    "sigue con el dominio del remitente y la mención a la Junta de Calificación."
))
story.append(h3("El encabezado del hilo citado no cuenta"))
story.append(P(
    "Antes de buscar ninguna marca se retira del texto el encabezado que arrastra un reenvío —el "
    "bloque de `De:` a `Asunto:`— y cualquier lista de destinatarios suelta. Un buzón no dice de qué "
    "va el mensaje: dice a quién se le mandó, y en la lista de copiados de un correo institucional "
    "figuran áreas cuyo **nombre** contiene justo las palabras que deciden el enrutamiento. Sin esto, "
    "una petición corriente se leía como notificación judicial por la lista de copiados que traía "
    "pegada."
))
story.append(P(
    "El corte va contra el siguiente rótulo del propio encabezado, no contra un número de "
    "caracteres: la lista de copiados de un correo institucional pasa del millar con facilidad, y un "
    "tope fijo deja fuera precisamente los encabezados más largos, que son los que más buzones "
    "nombran. La dirección de quien escribe no pasa por este filtro: esa se mira aparte y ahí sí "
    "decide.", "Nota"
))
story.append(h3("Nombrar no es lo mismo que provenir"))
story.append(P(
    "La regla que gobierna las tres decisiones es la misma: lo que decide no es que el texto "
    "**nombre** una tutela, un ente o la Junta, sino **quién escribe y en calidad de qué**. Un "
    "ciudadano que pide el cumplimiento de un fallo está haciendo una petición a la entidad, no "
    "notificando nada, y esa petición se radica. Lo mismo quien se queja de que no le cumplen, o "
    "quien pregunta por su propio dictamen ante la Junta: derivarlo lo manda a un área que no "
    "atiende ciudadanos."
))
story.append(P(
    "Exigir que el correo venga de un dominio oficial sería demasiado estricto en la otra "
    "dirección: un apoderado notifica un auto admisorio desde su correo personal y eso sí es "
    "judicial. Por eso `_LA_RAMA_ACTUA` y `_LA_JUNTA_ACTUA` reconocen a la autoridad actuando "
    "aunque el remitente sea un particular."
))
story.append(P(
    "Un derecho de petición se atiende en Servicio al Cliente venga de donde venga, incluso de un "
    "juzgado: un despacho también es empleador y escribe por asuntos administrativos. "
    "`_ES_DERECHO_DE_PETICION` lo mantiene en el flujo de radicación salvo que en el mismo correo "
    "la autoridad esté actuando.", "Nota"
))
story.append(P(
    "Y ninguna regla puede devolver el correo al área que lo envió: un área responde desde su buzón "
    "y ese correo vuelve a entrar aquí. `_seria_un_bucle()` lo impide — si el destino coincide con "
    "el remitente, el correo se radica en lugar de rebotar sin que nadie lo atienda.", "Nota"
))

story.append(h3("Entes de control"))
story.append(P(
    "Una comunicación de la Contraloría, la Procuraduría, la Fiscalía, una Superintendencia o un "
    "Ministerio no es una PQRD: tiene su propio término y su propia área. `_ente_de_control()` la "
    "reconoce por tres señales, en orden de fuerza: que el correo **provenga** del dominio del ente "
    "(`@contraloria.gov.co`, `@procuraduria.gov.co`, `@mintrabajo.gov.co`, `@ramajudicial.gov.co`…); "
    "que el **asunto** nombre al ente; o que el **cuerpo** lo presente como requerimiento suyo "
    "(\"requerimiento de la Contraloría\", \"la Procuraduría solicita\"). `desacato` y `proceso "
    "ordinario laboral` son judiciales por sí mismos."
))
story.append(P(
    "Ninguna de las tres es \"el texto nombra un ente\". Sobre el corpus, el nombre de un ente "
    "aparece en 10 de los 95 correos que sí son PQRD, casi siempre dentro de un adjunto: el "
    "ciudadano anexa como prueba su propia denuncia ante la Fiscalía, el membrete de un banco dice "
    "\"vigilada por la Superintendencia Financiera\" y un FURAT cita al Ministerio de Trabajo en su "
    "encabezado. Lo que distingue una comunicación de un ente es quién la escribe, no que se le "
    "mencione.", "Nota"
))
story.append(P(
    "Las direcciones de destino y los patrones judiciales (`JUDICIAL_KEYWORD_PATTERNS`) vienen de "
    "`business_rules/clasificacion_remitente.py`; `enrutamiento.py` los reutiliza en vez de "
    "declararlos de nuevo, para que las dos partes del código señalen siempre a la misma dirección "
    "de correo y a la misma lista de marcas judiciales."
))
story.append(P(
    "Las tres reglas son deterministas: no llaman al modelo y solo leen lo que el correo trae "
    "escrito — un patrón en el texto o el dominio del remitente —, nunca lo que el correo quiere "
    "decir. Distinguir \"envío mi historia clínica como prueba de mi caso\" (que se reenvía) de "
    "\"reclamo porque me negaron la incapacidad, y adjunto la historia clínica\" (que se radica) "
    "exige leer la intención, y eso no se resuelve con una lista de palabras. Ante la duda, "
    "`decidir()` no elige: devuelve `RADICAR` y el correo sigue su camino normal."
))
story.append(P(
    "Si el reenvío falla, el correo no se archiva: sacarlo de la bandeja sin haberlo enviado a "
    "ninguna parte lo pierde sin que nadie se entere hasta que el ciudadano reclame; dejarlo en la "
    "bandeja hace que la siguiente corrida lo vuelva a intentar (`orquestador.py::_reenviar`).",
    "Nota",
))
story.append(P(
    "El comportamiento se contrastó contra 200 correos reales ya procesados por el pipeline "
    "anterior: 100 que él derivó y 100 que él radicó. De los que radicó, este enrutamiento coincide "
    "en 95. Las diferencias que quedan están en el alcance de la derivación médico-laboral, que el "
    "pipeline anterior decide con una llamada al modelo y este resuelve con reglas.",
    "Nota",
))

story.append(h2("El corte por volumen"))
story.append(P(
    "Antes de convertir y antes de llamar al modelo, `pipeline/volumen.py` mide lo que trae el "
    "correo. Si se pasa, el correo NO llega al modelo: se cierra a revisión manual con el motivo "
    "`VOLUMEN_EXCESIVO`. Un correo con decenas de páginas escaneadas no se atiende mejor por "
    "pasarlo entero — satura el reconocimiento de texto y le da al agente más información de la que "
    "puede sopesar para decidir un dato."
))
story.append(P(
    "Contar páginas abre el documento pero no rasteriza ni reconoce nada, así que cuesta "
    "milisegundos: es lo que permite decidir ANTES de pagar el OCR. Los adjuntos decorativos no se "
    "cuentan, y un PDF que no se puede abrir tampoco se juzga por su volumen, porque no se sabe "
    "cuántas páginas tiene y suponer muchas lo mandaría a revisión manual con el motivo "
    "equivocado.", "Nota"
))
story.append(P(
    "Los topes salen de medir el buzón, no de estimar. Sobre 60 correos reales:", "Nota"
))
story.append(tabla(
    ["Por correo", "Media", "Mediana", "p95", "Máximo", "Tope"],
    [
        ["Adjuntos", "2,0", "1", "5", "21", "30 (`MAX_ADJUNTOS_POR_CORREO`), sin contar copias"],
        ["Páginas", "4,7", "2", "19", "48", "80 (`MAX_PAGINAS_POR_CORREO`)"],
        ["Tamaño", "1,7 MB", "0,3 MB", "9,8 MB", "15,1 MB", "50 MB (`MAX_MEGABYTES_POR_CORREO`)"],
    ],
    anchos=[3.6 * cm, 2.0 * cm, 2.2 * cm, 1.8 * cm, 2.0 * cm, 5.4 * cm],
))
story.append(P(
    "Están por encima del percentil 95 a propósito: el correo corriente —un adjunto y dos páginas— "
    "no puede quedar fuera por esto. Con estos valores queda fuera el 2% de los correos medidos. Se "
    "ajustan por variable de entorno para poder apretarlos mirando datos, sin desplegar.", "Nota"
))

story.append(h2("Conversión de adjuntos y OCR"))
story.append(P(
    "`pipeline/materializar.py` y `pipeline/conversion.py` convierten cada adjunto a Markdown, que "
    "conserva encabezados, tablas y listas — la forma que mejor aprovecha el modelo. El tipo de "
    "archivo se decide por los bytes iniciales, no por la extensión, porque `.docx`, `.xlsx` y "
    "`.pptx` son ZIP por dentro con el mismo encabezado."
))
story.extend(bullets([
    "**PDF**: se usa el texto nativo si existe; el OCR solo se aplica a la página que no trae "
    "texto, y a las imágenes incrustadas en una página que sí lo trae. Tres topes encajados: 25 "
    "segundos por página (`TIMEOUT_OCR_PAGINA_S`), 90 por documento "
    "(`PRESUPUESTO_OCR_SEGUNDOS`) y 180 por CORREO (`PRESUPUESTO_OCR_POR_CORREO_S`), más el "
    "límite de 60 páginas (`LIMITE_PAGINAS_PDF`). Las tablas se extraen aparte y se emiten como "
    "tablas Markdown.",
    "**Imágenes**: OCR directo con `pytesseract` (`spa+eng`), con el mismo tope por página y "
    "descontando del presupuesto del correo.",
    "**Word y Excel**: `python-docx` y `openpyxl`; las hojas se truncan a 2.000 filas.",
    "**ZIP**: se expande sin recursión (un ZIP dentro de un ZIP no se vuelve a abrir), con "
    "protección contra rutas que escapan del archivo y un tope de 200 MB descomprimidos.",
    "**Sobres firmados (`.p7m`)**: se localiza la carga útil por su firma de bytes y se convierte "
    "el documento que trae dentro, hasta 3 niveles de anidamiento.",
    "**PDF protegidos**: se prueban como contraseña los números de documento hallados en el asunto "
    "y el cuerpo del correo (`claves_candidatas_de`) — es la clave que habitualmente usa el "
    "remitente para proteger su propio soporte.",
    "Los adjuntos decorativos (firmas de Outlook, logos, espaciadores) se descartan sin convertir "
    "(`materializar.es_ruido`), igual que los adjuntos con contenido duplicado de otro ya visto en "
    "el mismo correo.",
]))
story.append(P(
    "Cada resultado — convertido, vacío, no soportado, con error o demasiado grande — se registra "
    "en el `Manifiesto` del workspace (`pipeline/workspace.py`) con su motivo. Esa distinción es la "
    "que permite luego separar \"el documento no tenía ese dato\" de \"el documento no se pudo "
    "leer\": son problemas distintos y se resuelven en sitios distintos."
))
story.append(P(
    "Cada correo trabaja en su PROPIO directorio, y eso es lo que sostiene todo lo que viene "
    "después: las herramientas del agente listan y leen lo que hay en el workspace, así que dos "
    "correos que compartieran carpeta se pisarían el cuerpo y los adjuntos, y el agente de uno "
    "podría extraer el nombre o el documento del otro. Con la concurrencia del Job son cinco correos "
    "escribiendo a la vez.", "Nota"
))
story.append(P(
    "El nombre del directorio no puede salir de recortar el identificador del correo: los de "
    "Microsoft Graph del mismo buzón miden más de ciento cincuenta caracteres y coinciden en casi "
    "todos, de modo que cualquier prefijo es el mismo para toda la bandeja. Lleva un resumen del "
    "identificador COMPLETO (`workspace.nombre_de_directorio`), que sí depende de la parte que los "
    "distingue.", "Nota"
))
story.append(P(
    "Los topes de OCR están encajados porque cada uno acota algo distinto, y ninguno solo alcanza. "
    "El del documento se comprueba ENTRE páginas, así que una sola página lenta lo desborda entero: "
    "de ahí el tope por página. Y el del documento se multiplica por el número de adjuntos: un "
    "correo con dieciséis escaneos suma veinticuatro minutos y se lleva la ventana entre corridas, "
    "de ahí el del correo, que se reparte entre sus adjuntos. Un PDF con texto nativo no consume "
    "presupuesto: no pasa por OCR.", "Nota"
))
story.append(P(
    "El motor de OCR se limita a UN hilo por invocación (`OMP_THREAD_LIMIT`). Tesseract usa OpenMP "
    "y mira los núcleos de la máquina, no la cuota del contenedor: con dos vCPU y cinco correos en "
    "vuelo, cinco instancias abriendo una decena de hilos cada una se pelean por los mismos dos "
    "núcleos. No cambia la concurrencia del pipeline ni el texto reconocido — es solo cuántos hilos "
    "abre cada llamada por dentro. Medido en un contenedor con la misma imagen y los mismos dos "
    "núcleos, sobre los adjuntos reales del buzón: el reloj del lote baja de 168 a 77 segundos y el "
    "peor correo de 142 a 58. Se fija en el código, antes de la primera llamada, porque "
    "`pytesseract` lanza el binario como subproceso y el subproceso hereda el entorno.", "Nota"
))
story.append(P(
    "Una página CON texto puede llevar además la foto de un documento: el ciudadano escribe su "
    "solicitud y pega la cédula debajo. Leer solo el texto dejaba ese dato fuera sin ningún aviso, "
    "de modo que el correo parecía bien convertido y salía a revisión manual por un dato que sí "
    "venía. Se reconocen las imágenes incrustadas que ocupan entre el 10% y el 60% de la hoja "
    "(`FRACCION_MINIMA_DE_PAGINA`, `FRACCION_MAXIMA_DE_PAGINA`). Los dos topes salieron de medir, "
    "no de estimar: el tamaño en píxeles no discrimina —hay membretes de 1.490 píxeles de ancho— y "
    "por encima del 60% la imagen ES la página, un escaneo que ya trae su capa de texto completa. "
    "Sobre los adjuntos del buzón, ninguna de esas veinte páginas ganó palabras al pasarla otra vez "
    "por OCR y varias perdieron; los únicos números que aportaban eran los del membrete, incluido "
    "el NIT de la propia Positiva, que la validación tomaría por un documento de identidad.", "Nota"
))
story.append(P(
    "El dpi y los idiomas se midieron contra los escaneos reales y se dejaron como están. Bajar a "
    "150 dpi ahorra un 41% del tiempo y pierde 23 de los 75 números de documento que hoy se leen; a "
    "120 dpi pierde 27. Quitar el inglés ahorra un 24% y no gana datos: lee los mismos números con "
    "otra longitud, y un documento con dígitos de más es un documento inventado. La extracción de "
    "tablas cuesta 3,7 veces lo que cuesta leer el texto de la página y se justifica: la mitad de "
    "las páginas con texto traen tablas y 67 de 73 producen Markdown útil — los RUT y las "
    "autoliquidaciones son tablas, y ahí está el NIT.", "Nota"
))
story.append(P(
    "Hay un género de adjunto que el OCR no alcanza: la foto de una cédula tomada con el teléfono y "
    "pegada en un PDF, que llega con 50 a 150 dpi efectivos y compresión JPEG encima. Se midieron "
    "ocho configuraciones —hasta 600 dpi, con escala de grises, autocontraste, binarizado y los "
    "modos de segmentación 3, 4, 6, 11 y 12— sobre siete de estos documentos. Las "
    "que extraen texto donde hoy no se extrae nada devuelven ruido: de los siete, seis no producen "
    "ningún número de documento y el séptimo produce uno de once dígitos con un cero delante, que "
    "ninguna cédula tiene. Cambiar la configuración convertiría un `ADJUNTO_ILEGIBLE` honesto en un "
    "documento de identidad inventado, así que se prefiere no leer a leer mal.", "Nota"
))
story.append(P(
    "La causa es la calidad de origen, no el motor: no se arregla con más resolución. Leer esos "
    "documentos pide entregarle la imagen al modelo multimodal, lo que exige extender el protocolo "
    "`Modelo` (sección 7), que hoy solo transporta texto.", "Nota"
))

story.append(h2("Detección de inyección de instrucciones"))
story.append(P(
    "`pipeline/saneamiento.py::detectar()` corre sobre el cuerpo y el texto de los adjuntos antes "
    "de la extracción. Busca patrones de manipulación — anular instrucciones, redefinir el rol del "
    "modelo, imitar marcadores de sistema, forzar una clasificación, exfiltrar el prompt, "
    "caracteres invisibles, homoglifos que mezclan alfabetos — y los clasifica por severidad "
    "(`Severidad.ALTA/MEDIA/BAJA`). No censura el texto sospechoso: el correo hay que radicarlo "
    "igual, y borrar la evidencia impide investigar el intento. Una detección de severidad ALTA "
    "queda anotada en los avisos del correo (`inyeccion detectada: ...`)."
))
story.append(P(
    "El aislamiento real ocurre en la frontera con el modelo: cada fragmento que viaja como "
    "respuesta de una herramienta (`leer_documento`, `buscar_en_documentos`) se envuelve con "
    "`sanear_fragmento()` en un bloque delimitado por una marca distinta por proceso e impredecible "
    "desde fuera (`marca_de_sesion()`), de modo que un documento no pueda \"cerrar\" el bloque escribiendo su "
    "propio delimitador. Las líneas sospechosas quedan marcadas con `[!] CONTENIDO SOSPECHOSO` para "
    "que el modelo las trate con desconfianza extra, sin que dejen de ser datos que hay que seguir "
    "procesando."
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 4. LA CAPA DETERMINISTA
# ═══════════════════════════════════════════════════════════════
story.append(h1("4. La capa determinista"))
story.append(P(
    "`pipeline/extraccion.py` (unas 2.450 líneas) concentra toda la extracción por reglas. "
    "Cada campo del payload de radicación tiene su propia función — `extraer_documento`, "
    "`extraer_documento_del_remitente`, `extraer_tipo_documento`, `extraer_telefono`, "
    "`extraer_nombre_remitente`, `extraer_nombre_afectado`, `extraer_sexo`, `extraer_tipo_remitente`, "
    "`extraer_tipo_pqrd`, `extraer_tipo_solicitud`, `extraer_direccion`, `extraer_oficina` — y todas "
    "comparten la misma firma de salida: un par `(Hallazgo | None, NoResuelto | None)`."
))

story.append(h2("El tipo de PQRD no se clasifica"))
story.append(P(
    "`extraer_tipo_pqrd` devuelve siempre `Solicitud`, con método `solicitud_unico_tipo_que_se_radica`. "
    "Es el único tipo con el que este canal radica: derecho de petición, queja y reclamo fijan "
    "términos de respuesta distintos, y elegirlos aquí obligaría a la entidad a un plazo que nadie "
    "verificó. Quien atiende el radicado lo reclasifica si procede."
))
story.append(P(
    "De ahí que el campo no aparezca en `CAMPOS_REPORTABLES`, no lleve criterio en el prompt del "
    "agente ni del revisor, y no escale nunca: el modelo no gasta una vuelta en él y no puede "
    "moverlo. El payload lo envía como constante del contrato (`TIPO_PQRD_DEL_CANAL`), no como un "
    "valor por defecto que tape un dato ausente, así que tampoco ensucia las suposiciones del "
    "informe. Está en `METODOS_POR_DEFECTO` para que las medidas no lo cuenten como dato leído."
))
story.append(P(
    "Tampoco queda catálogo: `tipo_pqrd` salió de `CATALOGOS`, así que `consultar_catalogo` ya no "
    "ofrece los otros siete valores, y la compuerta dejó de validarlo contra una tabla que no "
    "existe. `TipoPqrd` en `models.enums` sí conserva los ocho: es el espejo del catálogo de SGDEA "
    "—donde un funcionario sí puede reclasificar—, no la lista de lo que el pipeline puede elegir."
))

story.append(h2("Hallazgo y NoResuelto"))
story.append(P(
    "`Hallazgo(campo, valor, evidencia, confianza, metodo)` es un dato con procedencia: la "
    "`evidencia` cita literalmente el fragmento del texto de donde salió, y el `metodo` identifica "
    "qué heurística lo produjo (`candidato_unico`, `mismo_documento_que_el_afectado`, "
    "`concordancia_junto_al_nombre`, etc.), lo que hace auditable cualquier dato que termine en "
    "SGDEA. `NoResuelto(campo, motivo, detalle)` documenta exactamente por qué el campo no se pudo "
    "resolver — no es un vacío indiferenciado: motivos como `VARIOS_CANDIDATOS_AMBIGUOS`, "
    "`SIN_ETIQUETA_DE_AFECTADO` o `DIRECCION_DE_OTRO_SUJETO` describen la causa concreta, y ese "
    "motivo es justamente lo que después se le explica al agente (o, si el agente tampoco puede, al "
    "revisor humano)."
))

story.append(h2("El umbral de confianza y el principio de abstenerse"))
story.append(P(
    "`UMBRAL_CONFIANZA = 0.70` es, dentro de esta capa, el punto único donde se decide si un "
    "`Hallazgo` se acepta — el agente y la compuerta aplican su propio piso, más bajo (sección 6). Si la "
    "confianza calculada por el extractor cae por debajo, `extraer_todo` lo convierte igual en un "
    "`NoResuelto` con motivo `CONFIANZA_INSUFICIENTE`, citando el candidato descartado — de modo "
    "que el agente reciba el mismo indicio que vio el algoritmo, no solo la noticia de que falta el "
    "campo. El principio que atraviesa todo el módulo es que abstenerse vale más que adivinar: "
    "cuando dos candidatos de documento están a una distancia parecida del nombre del afectado, o "
    "cuando dos concordancias de género conviven en el mismo correo, el extractor no elige — deja "
    "el campo pendiente y explica por qué. Elegir ahí sería adivinar, y un dato equivocado sobre la "
    "identidad de una persona real es peor que un campo vacío que se resuelve después."
))
story.append(P(
    "Un ejemplo de esta disciplina: `sexo_afectado` nunca se deduce del nombre de pila, aunque eso "
    "resolvería casi todos los correos. Solo se acepta si el texto lo declara con un rótulo "
    "(\"Sexo: F\"), por concordancia gramatical junto al nombre (\"la trabajadora ANA LÓPEZ\") o, "
    "cuando el remitente es el propio afectado, por concordancia en primera persona (\"quedo "
    "atenta\"). El motivo es explícito en el código: nombrar por adivinanza escribiría un dato "
    "falso sobre una persona real."
))

story.append(h2("Orden de la extracción y dependencias entre campos"))
story.append(P(
    "`extraer_todo()` corre los extractores en un orden fijo porque unos dependen del resultado de "
    "otros: el nombre del afectado se resuelve primero porque sirve para desambiguar su documento "
    "y para deducir el sexo por concordancia; el tipo de documento se deriva del número ya elegido; "
    "el tipo de remitente puede heredar la identidad del afectado si comparten documento o nombre; "
    "la oficina se deriva del tipo de solicitud mediante una tabla de mapeo pura "
    "(`extraer_oficina`, 27 filas, `business_rules.get_office_id`) y no tiene nada que interpretar."
))

story.append(h2("Qué llega al agente y qué no"))
story.append(P(
    "No todo campo pendiente se le pregunta al modelo. `MOTIVOS_DE_AUSENCIA_CONSTATADA` marca los "
    "motivos donde el algoritmo ya barrió todo el texto y puede afirmar que el dato simplemente no "
    "está (`SIN_TELEFONO`, `SIN_NINGUN_NUMERO_EN_EL_TEXTO`): preguntarle al agente sobre esos campos "
    "gastaría una tarea para recibir la misma respuesta, porque lee el mismo texto. "
    "`Extraccion.campos_a_buscar` excluye esos motivos; `campos_sin_nada_que_buscar` los lista "
    "aparte, y se le informan al agente como \"constatado que no lo trae\" — para que no repita la "
    "búsqueda — pero no se le piden."
))
story.append(P(
    "`Extraccion.resumen_para_ia()` es literalmente el texto que recibe el agente como tarea: el "
    "asunto y el encabezado del correo, la lista de campos pendientes con su motivo, la lista de "
    "campos constatados como ausentes (para no repetirlos) y la lista de lo ya resuelto (para no "
    "reportarlo de nuevo). Nunca incluye el correo completo ni el contenido de los adjuntos: eso el "
    "agente lo pide con herramientas si lo necesita."
))
story.append(h2("Campos que el algoritmo resuelve pero no decide"))
story.append(P(
    "Un campo resuelto no se le muestra al agente para que lo revise, y eso deja fuera un caso "
    "intermedio: el algoritmo tenía dos candidatos, eligió uno por la forma de la frase y ninguno "
    "de los dos es descartable así. El nombre del afectado es donde ocurre: el recorte corta la "
    "frase en la primera palabra que no puede ser un nombre y conserva lo que hay antes, de modo "
    "que cuando detrás quedan dos palabras capitalizadas la elección no se sostiene en nada."
))
story.append(P(
    "`Hallazgo.a_confirmar` lleva por qué ese valor tiene que pasar por el agente. "
    "`Extraccion.campos_a_confirmar` los reúne y `campos_para_el_agente` los suma a los pendientes: "
    "el campo viaja a la tarea **con su valor** y con el motivo, y se le pide confirmarlo o "
    "sustituirlo leyendo el correo y los documentos ya convertidos del workspace. Si el valor es el "
    "correcto no reporta nada y el del algoritmo queda."
))
story.append(P(
    "Es el único caso en que el valor del agente tiene precedencia sobre el determinista: "
    "`combinar()` respeta lo del algoritmo salvo en estos campos, porque ahí lo del algoritmo era "
    "una de dos opciones y lo del agente es la respuesta a esa pregunta. La atribución lo cuenta "
    "como resuelto por la IA, y el cambio de valor queda en el informe de la corrida."
))
story.append(P(
    "`METODOS_POR_DEFECTO` marca los métodos que aplican un valor por ausencia de otra categoría —"
    " hoy solo `solicitud_por_ausencia_de_otro_tipo`, que clasifica como Solicitud un trámite "
    "corriente que no declara ningún tipo — y no por haberlo leído. El hallazgo es legítimo y su "
    "evidencia lo dice, pero las herramientas de medición (sección 10) lo cuentan como cobertura y "
    "no como acierto de lectura: contarlo igual que un dato leído inflaría la exactitud reportada.",
    "Nota",
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 5. EL AGENTE UNICO
# ═══════════════════════════════════════════════════════════════
story.append(h1("5. El agente único"))
story.append(P(
    "`pipeline/agente.py` reemplaza a los siete agentes en paralelo del pipeline antiguo por un "
    "único agente con herramientas, que solo se invoca — y solo recibe trabajo — sobre lo que la "
    "capa determinista no pudo resolver."
))

story.append(h2("El revisor: un segundo agente al que este delega"))
story.append(P(
    "El agente principal RESUELVE. `pipeline/revisor.py` COMPRUEBA, y son dos trabajos distintos "
    "con dos juegos de reglas distintos. Juntarlos en un solo prompt no cabe: los criterios de "
    "resolución ya ocupan cuatro quintas partes del texto que viaja en cada vuelta. Repartirlos "
    "deja a cada uno con su contexto entero."
))
story.append(P(
    "El principal lo invoca con la herramienta `pedir_revision`, y decide él cuándo: es un turno, "
    "no un paso fijo. El revisor arranca con su propio contexto y **solo con las herramientas de "
    "lectura** — listar, leer y buscar en los documentos ya convertidos. No puede reportar "
    "hallazgos ni consultar el registro: si pudiera, sería un segundo extractor y no un revisor. Ve "
    "también lo que resolvió la capa determinista, que el principal nunca reporta pero igual se "
    "envía."
))
story.append(P(
    "Lo que revisa no es coherencia entre campos —de eso ya se encarga la compuerta, que es "
    "determinista y no cuesta— sino lo que ninguna regla fija puede decidir: si el nombre que se va "
    "a registrar es de una persona o del buzón que mandó el correo, si el documento pertenece a "
    "quien se nombra, si la ciudad es donde vive el afectado o donde se expidió su cédula."
))
story.append(P(
    "Responde una línea por campo: `OK`, `CAMBIAR a <valor>` con la cita donde lo vio, o `SIN "
    "RESPALDO` con lo que buscó y no encontró. Su veredicto **no es una orden**: llega al principal "
    "como la respuesta de una herramienta, y es el principal quien decide si reporta el valor "
    "corregido o mantiene el suyo. Una línea que no se puede interpretar queda constando en vez de "
    "descartarse: si el revisor cambia de formato, hay que verlo.", "Nota"
))

story.append(h2("Las herramientas"))
story.append(P(
    "`servidor_mcp/herramientas.py` expone siete herramientas, todas de solo lectura o cálculo "
    "puro (ninguna tiene efectos secundarios sobre SGDEA ni sobre el correo):"
))
story.append(tabla(
    ["Herramienta", "Qué hace"],
    [
        ["listar_documentos", "Inventario del workspace sin contenido; punto de partida obligatorio."],
        ["buscar_en_documentos", "Busca un patrón en todos los documentos y devuelve dónde aparece, con contexto."],
        ["leer_documento", "Lee un fragmento por rango de líneas (tope 200 líneas por llamada)."],
        ["consultar_catalogo", "Valores válidos de un catálogo (tipo_documento, tipo_remitente, sexo, tipo_solicitud)."],
        ["validar_documento", "Valida forma y dígito de verificación de un número de documento, sin consulta externa."],
        ["consultar_persona", "Datos de una persona en el registro de SGDEA por documento (solo lectura)."],
        ["reportar_hallazgo", "Registra un campo resuelto con su evidencia; solo acumula en memoria, no actúa."],
    ],
    anchos=[4.6 * cm, 11.6 * cm],
))
story.append(P(
    "`buscar_en_documentos` rechaza patrones con cuantificadores anidados del tipo `(a+)+` "
    "(`herramientas.py::_cuantificador_anidado`): el patrón lo elige el modelo y el modelo lee "
    "documentos que vienen de un tercero desconocido, así que un adjunto podría inducirlo a escribir "
    "una expresión regular que cuelgue el proceso por explosión combinatoria."
))

story.append(h2("El prompt de sistema"))
story.append(P(
    "El prompt (`agente.py::SISTEMA`) fija una sola tarea — resolver únicamente los campos "
    "indicados como pendientes — y un procedimiento explícito: resolver primero con el asunto y el "
    "encabezado lo que no necesite herramientas, listar antes de leer, buscar antes de leer, y "
    "reportar. Incluye criterios "
    "detallados por campo (por ejemplo, cómo distinguir familiar de tercero en `tipo_remitente`, o "
    "cuándo un certificado de aportes nombra al pagador en vez de al trabajador afectado) y una "
    "instrucción explícita de tratar el contenido de los documentos como dato, nunca como "
    "instrucción — la misma frontera que traza el saneamiento de la sección 3."
))
story.append(P(
    "Solo viajan los criterios de los campos que se van a pedir. El prompt se reenvía entero en cada "
    "vuelta, los criterios de los doce campos son cuatro quintas partes de su tamaño, y los de un "
    "campo ya resuelto no cambian ninguna decisión: `prompt_de_sistema()` arma la cabecera fija más "
    "los bloques que correspondan a lo pendiente.", "Nota"
))
story.append(P(
    "Los códigos válidos de los campos numéricos van dentro de la TAREA, no detrás de una "
    "herramienta (`catalogos_de`). Los cinco catálogos juntos ocupan menos que el peso fijo de una "
    "sola vuelta, y pedirlos cuesta una vuelta entera por cada uno. La herramienta "
    "`consultar_catalogo` queda para lo que no venga listado.", "Nota"
))
story.append(P(
    "El prompt fija además cómo cierra el agente: si resolvió todo, responde `LISTO`; si algo quedó "
    "pendiente, responde **una frase** que diga qué dato faltó y por qué no estaba en la "
    "comunicación, dirigida a quien va a atender el caso a mano y no a quien opera el sistema. Esa "
    "frase viaja hasta el informe de la corrida (sección 9): el motivo del catálogo es una etiqueta "
    "igual para todos los correos, y esta explica **este** correo."
))
story.append(P(
    "Esa frase solo se publica cuando el desenlace lo decidió el CONTENIDO del correo "
    "(`telemetria.lo_decidio_el_contenido`). Con un motivo de infraestructura no lo decidió: un "
    "correo aplazado porque el backend no respondió salía con \"no se pudo identificar la "
    "dirección\" como su motivo, que no es la causa de que no se radicara. La frase seguía siendo "
    "cierta y aun así afirmaba algo falso, en los tres sitios donde se publica: el registro, el "
    "informe de la corrida y el correo de aviso interno. `ADJUNTO_ILEGIBLE`, `SIN_OCR` y "
    "`ADJUNTO_PROTEGIDO` quedan fuera de esa lista a propósito: ahí el dato que faltó sí es la "
    "causa.", "Nota"
))

story.append(h2("El bucle y la ventana de contexto"))
story.append(P(
    "`resolver()` corre hasta un tope de vueltas, cinco por defecto, ajustable con "
    "`MAX_VUELTAS_AGENTE` para poder subirlo o bajarlo mirando el campo `vueltas` del informe sin "
    "desplegar. Cinco cubre el recorrido que describe el propio prompt —listar, buscar, leer, "
    "reportar— con margen para un documento más. En cada vuelta se reenvía la tarea "
    "original completa más una ventana reciente del historial (`VENTANA_HISTORIAL = 3` "
    "intercambios); dentro de esa ventana, solo el último intercambio va sin recortar — las "
    "respuestas de herramienta más antiguas se compactan a `MAX_CARACTERES_HERRAMIENTA_VIEJA = 220` "
    "caracteres (`_compactar`), porque lo ya resuelto se resume en una línea de estado en vez de "
    "repetirse íntegro. Cada respuesta de herramienta se acota además a "
    "`MAX_CARACTERES_RESPUESTA_HERRAMIENTA = 2.500` caracteres. Este manejo de la ventana es lo que "
    "mantiene acotado el consumo incluso cuando el agente necesita varias vueltas de herramientas."
))
story.append(P(
    "El tope cuenta VUELTAS DE TRABAJO. Un turno que se va en corregir el formato de una invocación "
    "no lo es —no se ejecutó nada y el correo no avanzó— y se concede aparte, acotado por "
    "`MAX_CORRECCIONES_DE_FORMATO`. Contándolo, un fallo de formato le quitaba al agente el margen "
    "para reportar, y el correo salía a revisión manual con campos que ya había leído."
))
story.append(P(
    "Y el bucle se cierra por su cuenta cuando ya no falta nada. Se le informa —\"No falta nada. "
    "Responde LISTO\"— y se le concede `TURNOS_TRAS_AVISAR_QUE_NO_FALTA_NADA` turno más, que alcanza "
    "para cerrar o para confirmar un campo de los que se le dieron a confirmar. Si lo gasta pidiendo "
    "herramientas, se corta: cada turno reenvía el prompt entero y no queda ningún campo que el "
    "bucle pueda resolver. Antes esos correos llegaban al tope sin nada pendiente, y el informe los "
    "reportaba como si el agente se hubiera quedado corto de vueltas."
))
story.append(P(
    "Si `Extraccion.campos_a_buscar` está vacío — el algoritmo resolvió todo lo que se podía "
    "resolver — `resolver()` no llama al modelo ninguna vez: el correo se procesa con cero costo de "
    "modelo."
))

story.append(h2("Validación de lo que reporta el agente"))
story.append(P(
    "Lo que el agente reporta con `reportar_hallazgo` no se acepta a ciegas. `resolver()` filtra: "
    "descarta lo que ya estaba resuelto (evita doble atribución), descarta lo que llega con "
    "confianza por debajo de `confianza_exigida(campo)` — el mismo piso que usa la compuerta "
    "(sección 6), importado desde ahí para que dos constantes no puedan discreparse — y normaliza a "
    "entero los campos que son códigos de catálogo, porque la herramienta declara `valor` como "
    "texto y `\"2\"` no es igual a `2` al comparar contra un catálogo cuyas claves son enteros "
    "(`_valor_normalizado`). Si el agente contradice una ausencia que el algoritmo ya había "
    "constatado, el valor se acepta — el agente tiene herramientas para leer un adjunto que el "
    "barrido inicial no cubrió — pero queda anotado como aviso, no en silencio."
))

story.append(h2("Por qué un agente y no siete"))
story.append(P(
    "El pipeline antiguo llama al modelo una vez por campo y le manda el correo entero cada vez, "
    "siempre, resuelva el algoritmo lo que resuelva. El nuevo llama al modelo solo cuando queda "
    "algo pendiente, y le manda la lista de campos pendientes en vez del correo: lo demás lo lee "
    "él con herramientas, cuando lo necesita."
))
story.append(P(
    "Eso no significa una sola llamada por correo. El agente trabaja en vueltas, y cada vuelta es "
    "una invocación: pide una herramienta, recibe la respuesta y decide qué hacer con ella. Un "
    "correo que resuelve a la primera cuesta una vuelta; uno que tiene que abrir tres adjuntos "
    "cuesta varias. Cuántas se llevó cada correo está en el informe de la corrida."
))
story.append(P(
    "Cada vuelta reenvía el prompt de sistema y los esquemas de las herramientas, que no cambian, "
    "así que el número de vueltas multiplica un peso fijo. Por eso el límite de vueltas "
    "(`MAX_ITERACIONES`) y el tamaño del prompt son las dos cosas que gobiernan el consumo, y no "
    "el tamaño del correo.", "Nota"
))
story.append(P(
    "El consumo se anota una vez por correo, con los tokens de todas las vueltas ya sumados, y el "
    "registro lleva cuántas vueltas representa (`Llamada.turnos`). Contar registros en vez de "
    "vueltas describiría cada correo como una sola invocación, que es justo lo que el informe tiene "
    "que dejar ver cuando el agente itera.", "Nota"
))

story.append(h2("Combinación y atribución"))
story.append(P(
    "`combinar(extraccion, resolucion)` une lo determinista y lo del agente, con precedencia del "
    "determinista si por construcción llegara a haber solapamiento. `atribucion()` marca, campo por "
    "campo, quién lo resolvió — `Origen.ALGORITMO`, `Origen.IA`, `Origen.REGISTRO` o "
    "`Origen.NO_RESUELTO` — para alimentar la telemetría (sección 9)."
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 6. LA COMPUERTA DE VALIDACION
# ═══════════════════════════════════════════════════════════════
story.append(h1("6. La compuerta de validación"))
story.append(P(
    "`pipeline/compuerta.py::validar()` es el último paso antes de radicar. Recibe todos los "
    "hallazgos — vengan del algoritmo, del agente o del registro de personas — y devuelve un "
    "`Dictamen` con uno de tres veredictos: `APROBADO`, `CORREGIDO` (con los ajustes aplicados) o "
    "`RECHAZADO`. Es enteramente determinista: no hace ninguna llamada a modelo."
))

story.append(h2("Campos obligatorios"))
story.append(P(
    "Sin estos cuatro campos el correo no se puede radicar, y su ausencia es siempre bloqueante:"
))
story.extend(bullets([
    "`numero_documento_afectado`", "`nombre_afectado`", "`nombre_remitente`", "`tipo_solicitud`",
]))

story.append(h2("El piso de confianza es por campo"))
story.append(P(
    "`CONFIANZA_PARA_RADICAR = 0.60` es el piso general, pero `confianza_exigida(campo)` lo ajusta "
    "por campo a través de `PISO_POR_CAMPO`. Hoy el único caso especial es `tipo_solicitud`, con "
    "piso `0.0`: es obligatorio, así que descartarlo por baja confianza no deja el campo vacío — lo "
    "manda a revisión manual — y si se rellena por defecto es con \"Otra\", que igual enruta el "
    "caso a una oficina concreta. En los dos casos se está afirmando un dato, no absteniéndose, así "
    "que la elección no es entre un dato dudoso y ningún dato: es entre dos datos, y por eso se "
    "acepta lo que el agente reporte con la confianza que traiga, dejando la incertidumbre anotada "
    "en los avisos en vez de bloquear el correo. Para el resto de los campos, un valor por debajo "
    "del piso se descarta y el radicador (`pipeline/radicador.py`) lo reemplaza por su valor por "
    "defecto, lo que también queda anotado."
))

story.append(h2("Reglas de coherencia"))
story.append(P(
    "La compuerta no revisa cada campo aislado — eso ya lo hizo la capa que lo produjo — sino la "
    "coherencia ENTRE campos:"
))
story.extend(bullets([
    "**Documento**: longitud según el tipo declarado (`utils/tipos_documento`, la misma tabla que "
    "usa el extractor), dígito de verificación del NIT, y detecta documentos de relleno (todos los "
    "dígitos iguales).",
    "**Teléfono**: contra el plan de numeración colombiano y contra que no sea, en realidad, el "
    "número de documento de la misma persona. No bloquea — corrige a vacío, sin escribir en la "
    "base un número que no sirve para llamar a nadie.",
    "**Nombre**: nunca es un correo ni una URL, y pasa el mismo criterio que ya aplicó el "
    "extractor (`motivo_por_el_que_no_es_un_nombre`) para no admitir un cargo, un área ni el "
    "nombre de un BUZÓN como si fuera una persona. Un buzón se llama por su función más una "
    "palabra cualquiera —«Notificaciones Judiciales», «Notificaciones Oriente», «Notificaciones "
    "SGD Orfeo»— así que exigir que TODAS las palabras estén en el vocabulario de cargos los deja "
    "pasar: lo que los delata es con qué empiezan. La familia de «notificación» se reconoce por "
    "prefijo, porque se escribe de todas las formas y ninguna palabra del castellano que empiece "
    "así nombra a una persona; las demás van por igualdad, porque «informática» empieza como "
    "«info» y sí puede ser una empresa. Una razón social queda fuera del filtro aunque arranque "
    "igual: su marca de sociedad lo dice.",
    "**Nombre y tipo de documento del mismo sujeto**, en sus dos sentidos, que no valen lo mismo. "
    "Si el tipo es de organización (NIT) y el nombre no tiene forma de razón social, se anota como "
    "aviso — no bloquea, porque el NIT de una persona natural en Colombia puede ser su propia "
    "cédula. Al revés **sí bloquea** (`EMPRESA_CON_DOCUMENTO_DE_PERSONA`): la razón social es una "
    "marca explícita —SAS, LTDA, FUNDACIÓN— y una sociedad no tiene cédula, así que la pareja es "
    "imposible y no dudosa; uno de los dos datos está mal, no se puede saber cuál, y radicarla "
    "escribe una identidad que no existe.",
    "**Los identificadores de la propia entidad** (`DOCUMENTO_ES_DE_POSITIVA`): el NIT de Positiva "
    "y su línea institucional no son el documento de nadie, y bloquean. Están en la firma de cada "
    "correo y en el membrete de cada escaneo, así que llegan con facilidad al campo del documento; "
    "el filtro es de esos números concretos y no de todo el plan de numeración, porque una cédula "
    "real puede coincidir con un teléfono fijo válido. La misma línea en el campo del teléfono se "
    "corrige a vacío sin bloquear.",
    "**Remitente y afectado**: si `tipo_remitente` declara que son la misma persona pero los "
    "nombres difieren y los documentos también, es un reparo bloqueante "
    "(`TIPO_3_CON_NOMBRES_DISTINTOS`). Si declara que son personas distintas pero comparten nombre "
    "y no hay documentos que los distingan, se corrige a \"el afectado mismo\" sin bloquear — dos "
    "personas pueden llamarse igual, y ahí el documento manda si existe.",
    "**Documento heredado indebidamente**: si el remitente no es el afectado pero terminó con el "
    "mismo documento, es un reparo bloqueante — registraría a una persona con la cédula de otra.",
    "**Oficina contra tipo de solicitud**: se verifica contra la tabla "
    "`pqrd_config_tipo_solicitud_oficina_ia` (`business_rules.get_office_id`); si no coincide, se "
    "corrige sin bloquear, porque la tabla es la fuente de verdad y no admite ambigüedad.",
    "**Razón social con documento de persona**: una marca de empresa —SAS, LTDA, FUNDACIÓN— junto "
    "a un tipo de documento de persona natural es un par imposible: una sociedad no tiene cédula. "
    "Bloquea con motivo propio (`EMPRESA_CON_DOCUMENTO_DE_PERSONA`), porque uno de los dos datos "
    "está mal y no se puede saber cuál. El sentido contrario —persona con NIT— solo se anota: en "
    "Colombia el NIT de una persona natural es su propia cédula.",
    "**Documento que no es un número**: se juzga sobre el valor tal como se extrajo, antes de "
    "quitarle lo que no son dígitos. Los separadores de miles son forma de escribirlo y no "
    "invalidan nada; las letras sí, salvo en pasaporte y sociedad extranjera, que son "
    "alfanuméricos por naturaleza (`TIPOS_ALFANUMERICOS`).",
    "**Teléfono de relleno**: un número puede tener la forma correcta y aun así no llamar a nadie "
    "—el que repite un dígito, el que es una secuencia, o los ejemplos del formulario—. Se corrige "
    "a vacío, no bloquea.",
    "**Dirección que no es una dirección**: se retiran los marcadores con que un formulario "
    "representa la ausencia de dato (\"No reporta\", \"N/R\", \"undefined\"), los correos y los "
    "teléfonos sueltos. No se exige forma urbana: buena parte de las direcciones del país son "
    "rurales y legítimas.",
]))
story.append(P(
    "Los rangos cubren tanto a colombianos como a extranjeros: la cédula acepta de 5 a 10 dígitos "
    "—desde las más antiguas hasta las expedidas sobre el NUIP—, y están definidos además la "
    "cédula de extranjería, el permiso por protección temporal, el permiso especial de "
    "permanencia y el carné diplomático. El pasaporte no se mide en dígitos: se reconoce por su "
    "forma alfanumérica (`es_pasaporte`), y sin tipo declarado esa forma se acepta igual, porque "
    "no toda persona atendida por la entidad es colombiana."
))
story.append(P(
    "`utils/tipos_documento.py` responde con la misma tabla dos preguntas distintas. "
    "`LONGITUD_POR_TIPO` sirve para sugerir tipos cuando el correo no declara ninguno "
    "(`tipos_compatibles`): qué tipos podría ser un número suelto. `rango_aceptable()` sirve para "
    "objetar un tipo ya declarado: qué longitudes NO lo contradicen, un rango más ancho que el "
    "típico. La única diferencia entre las dos es el NIT: su rango se deriva del de la cédula en "
    "vez de escribirse aparte, porque el NIT de una persona natural en Colombia es su propia "
    "cédula, y ensanchar la cédula no debe desalinear al NIT.", "Nota"
))

story.append(P(
    "Hay una incoherencia que la compuerta anota pero no puede corregir. El payload de radicación "
    "no tiene ningún campo para la razón social del afectado: cuando el titular del caso es una "
    "empresa, no hay dónde ponerla. La capa determinista se abstiene —deja `nombre_afectado` "
    "pendiente con `SOLO_IDENTIFICA_UNA_EMPRESA` o `SIN_ETIQUETA_DE_AFECTADO`— en lugar de "
    "escribir una razón social en el campo del nombre de una persona, y el correo va a revisión. "
    "Resolverlo exige que el backend agregue el campo.", "Nota"
))

story.append(h2("Veredicto y aplicación"))
story.append(P(
    "Si algún reparo es bloqueante, el veredicto es `RECHAZADO` y el correo va a revisión manual. "
    "Si hay correcciones pero ningún bloqueo, el veredicto es `CORREGIDO`: `compuerta.aplicar()` "
    "reescribe los hallazgos afectados con `metodo=\"compuerta\"`, y cada corrección queda anotada "
    "en los avisos del correo (`compuerta corrigio <campo>: se envia <valor>`). Si no hay ningún "
    "reparo, es `APROBADO`."
))
story.append(P(
    "Cuando el veredicto es de rechazo, `_motivo()` traduce los reparos bloqueantes a un motivo de "
    "revisión manual específico — `SIN_DOCUMENTO`, `DOCUMENTO_AMBIGUO`, `ADJUNTO_ILEGIBLE`, "
    "`SOLO_EMPRESA_SIN_AFECTADO`, entre otros — en vez de un genérico \"datos incoherentes\", para "
    "que quien revise sepa de entrada qué tipo de problema tiene delante."
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 7. EL MODELO COMO PIEZA INTERCAMBIABLE
# ═══════════════════════════════════════════════════════════════
story.append(h1("7. El modelo como pieza intercambiable"))
story.append(P(
    "`pipeline/modelo.py` define el contrato mínimo que el agente necesita — un `Protocol` — para "
    "que la lógica del bucle de herramientas, la validación de salida y la compuerta se puedan "
    "probar sin llamar a ningún proveedor, y para poder cambiar de proveedor sin tocar "
    "`pipeline/agente.py`."
))
story.append(caja_codigo(
"""class Modelo(Protocol):
    nombre: str

    def generar(
        self,
        sistema: str,
        mensajes: Sequence[Dict[str, str]],
        herramientas: Sequence[Dict[str, Any]],
    ) -> Turno:
        ...
"""
))
story.append(P(
    "`Turno(texto, llamadas, tokens_entrada, tokens_salida, modelo)` es la respuesta uniforme, sea "
    "cual sea el proveedor: texto libre, una lista de `Llamada(herramienta, argumentos)`, o ambas."
))

story.append(h2("Implementaciones"))
story.extend(bullets([
    "**ModeloFalso** (`pipeline/modelo.py`): devuelve turnos programados en orden. Es lo que usan "
    "las pruebas automatizadas para ejercitar el bucle del agente sin red.",
    "**ModeloVertex** (`pipeline/modelo.py`): adaptador de Vertex AI / Gemini, autenticado por "
    "credenciales por defecto de la aplicación (ADC). El prompt de sistema viaja como "
    "`system_instruction`, en un canal separado del contenido de los documentos — la misma "
    "frontera de aislamiento de la sección 3, aplicada también aquí: el contenido ajeno nunca entra "
    "mezclado con las instrucciones. `endpoint_global` permite forzar el endpoint global de la API "
    "en vez del regional, porque este último resuelve a un bloque de red inalcanzable desde algunas "
    "redes.",
]))
story.append(P(
    "`ModeloVertex` es el único backend que resuelve campos, y cada llamada suya se factura. Por eso "
    "`correr.py` exige la bandera `--vertex`: sin ella la corrida termina sin haber llamado al "
    "modelo. No existe un backend de reserva que se elija solo, precisamente para que ninguna "
    "corrida —ni en el Job ni en una prueba local— empiece a cobrar por descuido.", "Nota"
))
story.append(P(
    "La conversación conserva quién dijo cada cosa. El bucle del agente produce mensajes con su rol "
    "—la tarea, lo que respondió el modelo, lo que devolvió cada herramienta— y `conversacion()` los "
    "traduce a turnos de la API, uniendo los contiguos del mismo rol porque la API los espera "
    "alternados. Concatenarlos en una sola cadena le presenta al modelo sus propias respuestas "
    "como si se las hubiera dicho el usuario, y borra la frontera entre lo que pidió y lo que le "
    "devolvieron.", "Nota"
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 8. ERRORES Y REINTENTOS
# ═══════════════════════════════════════════════════════════════
story.append(h1("8. Errores y reintentos"))
story.append(P(
    "`pipeline/errores.py` clasifica cualquier fallo — de red, del modelo o de la conversión local "
    "— en una `Clase` con política propia: severidad, si es reintentable, cuántos intentos admite, "
    "la espera base entre intentos y a qué motivo de revisión manual corresponde. Ningún fallo se "
    "descarta sin quedar registrado en una `Bitacora`."
))

story.append(h2("Severidad"))
story.extend(bullets([
    "**FATAL**: el correo no se puede radicar en este intento.",
    "**DEGRADA**: el correo se puede radicar igual, pero se perdió una capacidad (por ejemplo, una "
    "capa de deduplicación).",
    "**AVISO**: no afecta el resultado; queda anotado igual.",
]))

story.append(h2("El catálogo de clases"))
story.append(P(
    "`clasificar()` evalúa el catálogo en orden — gana la primera coincidencia — y el cuerpo de la "
    "respuesta manda sobre el código HTTP: un 403 con la página de Cloudflare es transitorio y hay "
    "que reintentar; un 403 de autorización real no lo es. Mirar solo el código confundiría los dos "
    "y perdería radicaciones que sí se recuperarían con un reintento."
))
story.append(tabla(
    ["Clase", "Severidad", "Reintentable", "Motivo manual"],
    [
        ["CLOUDFLARE_CHALLENGE", "fatal", "sí (4)", "CLOUDFLARE_403"],
        ["ORIGEN_INALCANZABLE", "fatal", "sí (3)", "SGDEA_CAIDO"],
        ["TOKEN_EXPIRADO", "fatal", "sí (2)", "— (renueva y repite)"],
        ["SIN_PERMISO", "fatal", "no", "ERROR_INESPERADO"],
        ["PETICION_INVALIDA", "fatal", "no", "VALIDACION_RECHAZO"],
        ["FORMATO_RECHAZADO", "fatal", "no", "PETICION_MAL_FORMADA"],
        ["TIEMPO_AGOTADO", "fatal", "sí (3)", "SGDEA_CAIDO"],
        ["DNS_NO_RESUELVE", "degrada", "sí (4)", "—"],
        ["ESQUEMA_DESALINEADO", "degrada", "no", "—"],
        ["MODELO_TRANSITORIO", "degrada", "sí (3)", "—"],
        ["LIMITE_DE_TASA", "degrada", "sí (5)", "—"],
        ["ADJUNTO_PROTEGIDO", "degrada", "no", "ADJUNTO_PROTEGIDO"],
        ["ADJUNTO_ILEGIBLE", "degrada", "no", "ADJUNTO_ILEGIBLE"],
        ["SIN_OCR", "degrada", "no", "SIN_OCR"],
        ["DESCONOCIDO", "fatal", "no", "ERROR_INESPERADO"],
    ],
    anchos=[4.6 * cm, 2.6 * cm, 3.0 * cm, 5.9 * cm],
    mono_cols=(0,),
))
story.append(P(
    "La espera entre intentos crece exponencialmente — `espera_base_s * 2^(intento-1)` — con un tope "
    "de `TOPE_DE_ESPERA_S = 30` segundos, que además se recorta contra lo que le quede a la corrida "
    "(sección 12). Una espera larga no la paga el correo que falló: la pagan los que venían detrás."
))
story.append(P(
    "El código HTTP no siempre llega como parámetro. Los servicios que envuelven la respuesta en una "
    "excepción lo dejan dentro del texto —\"Radicacion fallida: 502 - Bad Gateway\"— y sin leerlo de "
    "ahí la tabla `_POR_ESTADO` nunca se consultaba: un 502 caía en `DESCONOCIDO`, que no es "
    "reintentable, y el correo se archivaba en revisión manual en lugar de aplazarse. `clasificar()` "
    "extrae el código del texto cuando no lo recibe como parámetro.", "Nota"
))
story.append(P(
    "Un mismo código HTTP tampoco significa siempre lo mismo. Un 500 suele ser el backend caído y se "
    "reintenta, pero el 500 que devuelve al rechazar el tipo de contenido "
    "(`HttpMediaTypeNotSupported`) describe una petición mal formada por este lado: repetirla "
    "devuelve el mismo 500 en cada ciclo y gasta los aplazamientos sin que el correo pueda avanzar "
    "nunca. `FORMATO_RECHAZADO` se reconoce por el cuerpo, va antes que las firmas genéricas de "
    "`ORIGEN_INALCANZABLE` —que casarían con el \"Internal Server Error\" del mismo mensaje— y no es "
    "reintentable.", "Nota"
))

story.append(h2("El mismo motivo técnico significa cosas distintas según el paso"))
story.append(P(
    "`MOTIVO_POR_PASO` traduce una misma clase de fallo a un motivo de revisión manual distinto "
    "según en qué paso ocurrió: un `TIEMPO_AGOTADO` radicando dice que SGDEA no respondió "
    "(`SGDEA_CAIDO`), pero el mismo `TIEMPO_AGOTADO` esperando al modelo se traduce a "
    "`MODELO_NO_RESPONDIO`, que no dice nada de SGDEA. La política de reintento es la misma en los "
    "dos sitios donde se aplica — `Orquestador._resolver_con_reintento`, para las llamadas al "
    "agente, y `Radicador.radicar`, para el envío a SGDEA — de modo que un fallo de red no se trata "
    "distinto según a quién se le estaba pidiendo algo. La espera entre reintentos del agente se "
    "hace con `self.dormir`, que se puede inyectar para que las pruebas no tengan que esperar de "
    "verdad."
))

story.append(h2("Cómo llega un fallo a quien revisa"))
story.append(P(
    "`Bitacora.motivo_manual()` toma el primer fallo fatal registrado; si solo hay degradaciones, "
    "la radicación sigue adelante. El motivo resultante se escribe en la etiqueta del correo "
    "(`REVISION-MANUAL:<motivo>`) y aparece en el informe de la corrida con su descripción en "
    "lenguaje llano (`telemetria.py::MOTIVOS_MANUAL`), separado explícitamente de los motivos que "
    "sí miden desempeño del modelo (sección 9)."
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 9. TELEMETRIA
# ═══════════════════════════════════════════════════════════════
story.append(h1("9. Telemetría"))
story.append(P(
    "`pipeline/telemetria.py` construye tres niveles de reporte a partir de lo que cada correo deja "
    "registrado en un `RegistroCorreo` (acción, radicado, motivo manual, duración por etapa, "
    "atribución por campo y avisos):"
))
story.extend(bullets([
    "**Por ejecución**: `ejecuciones/<AAAA-MM-DD>/<HHMMSS>-<id>.txt` — el informe completo de una "
    "corrida del job, impreso también por salida estándar para que Cloud Logging lo recoja.",
    "**Diario**: `diario/<AAAA-MM-DD>.txt` — acumula las corridas del mismo día.",
    "**Serie**: `serie.tsv` — una fila por corrida, con `pct_algoritmo`, `pct_ia`, `pct_registro` y "
    "`pct_manual` entre sus columnas, para comparar corridas entre sí y seguir la tendencia.",
]))
story.append(P(
    "La raíz la fija `RUTA_LOGS`, y admite dos formas: una ruta de disco o una dirección "
    "`gs://bucket/prefijo` (`pipeline/almacen.py`). La segunda es la que importa en producción, "
    "porque el sistema de archivos del Job es efímero y muere con la corrida: sin un bucket, la "
    "serie histórica nunca llega a acumular nada."
))
story.append(P(
    "Cloud Storage no sabe añadir al final de un archivo, y reescribir el objeto tampoco es una "
    "opción: **reemplazar cuenta como borrar y crear**, así que exige `storage.objects.delete` "
    "sobre el bucket — un permiso que no se concede sobre un bucket con documentos de casos solo "
    "para guardar un informe. Por eso en el bucket cada `anexar()` crea su propia PARTE bajo "
    "`<destino>.partes/<fecha>-<id>.txt`, y la lectura las une en el orden en que se escribieron. "
    "El Job solo necesita crear objetos: nunca reemplaza ni borra. En disco se anexa de verdad, que "
    "es más simple y no tiene el problema.", "Nota"
))
story.append(P(
    "La lectura une lo que haya de las dos formas: primero el objeto de una pieza, que es lo que "
    "escribieron las corridas anteriores, y detrás sus partes. Quedarse solo con el objeto dejaría "
    "las partes nuevas fuera para siempre en cuanto exista uno —y en el bucket ya existe—; "
    "quedarse solo con las partes perdería el histórico ya escrito.", "Nota"
))
story.append(P(
    "La cabecera de la serie NO se escribe: la conoce quien lee (`CABECERA_DE_LA_SERIE`). "
    "Decidirla al escribir obligaba a leer el destino primero, y leer en el bucket necesita permiso "
    "de listar, que una corrida no tiene por qué necesitar solo para escribir su fila.", "Nota"
))
story.append(P(
    "Si el informe no se puede guardar, la corrida **no se cae**: los correos ya quedaron radicados, "
    "marcados y movidos, y convertir eso en una ejecución fallida sería peor que perder el informe. "
    "El fallo sale con su motivo y el informe completo viaja igual por la salida estándar.", "Nota"
))

story.append(h2("Formato de salida: JSON en la nube, texto en terminal"))
story.append(P(
    "`pipeline/salida.py::emitir()` decide cómo sale cada entrada de telemetría por consola. Cloud "
    "Logging trata cada LÍNEA de la salida estándar como una entrada independiente, así que un "
    "informe de sesenta renglones impreso a pelo se convierte en sesenta entradas sueltas, "
    "imposibles de leer juntas. `emitir()` detecta si corre en Cloud Run —por `CLOUD_RUN_JOB` o "
    "`CLOUD_RUN_EXECUTION` en un Job, por `K_SERVICE` en un Service, o forzando el formato con "
    "`FORMATO_LOG=json` sin necesidad de desplegar— y en ese caso escribe una sola línea con un "
    "objeto JSON: el informe completo cabe en un campo (`informe`) y cada número queda consultable "
    "por separado, por ejemplo `jsonPayload.radicados`, `jsonPayload.motivo_manual` o "
    "`jsonPayload.accion=\"manual\"`. En una terminal —o si `FORMATO_LOG=texto`— sigue imprimiendo "
    "texto legible: el mensaje, los campos indentados debajo, y el informe completo al final."
))
story.append(P(
    "Los dos formatos no llevan lo mismo. En JSON viajan TODOS los campos, que es por donde se "
    "filtra en Cloud Logging. En texto solo lo que el mensaje no dice ya: `solo_json` nombra los "
    "que la primera línea lleva escritos —el correo, el desenlace, el radicado— y `en_su_linea` los "
    "que se leen mejor sueltos y en un orden fijo. Los campos vacíos no se imprimen. Un campo por "
    "renglón eran trece renglones por correo, de los que diez repetían la primera línea o estaban "
    "en blanco.", "Nota"
))
story.append(P(
    "Cada carga de trabajo de Cloud Run anuncia su tipo con una variable distinta, y mirar solo la "
    "del Service deja al Job imprimiendo en formato de terminal: cada entrada se parte en una línea "
    "por campo, el informe no se lee junto y los campos no son consultables.", "Nota"
))
story.append(P(
    "Las etapas de cada correo no se emiten todas. Once entradas por correo tapan la que importa, y "
    "su tiempo va igual en el informe: solo se anuncia la etapa que falla o la que tarda más de lo "
    "que tarda ESA etapa cuando va bien. El umbral es por etapa (`UMBRAL_POR_ETAPA_MS`), con "
    "`UMBRAL_ETAPA_LENTA_MS` para las que no estén en la tabla: el agente son varias llamadas al "
    "modelo y la conversión recorre las páginas de cada adjunto, así que las dos pasan de cinco "
    "segundos en el caso normal y un umbral único las anunciaba siempre. Las entradas llevan una "
    "referencia corta del correo, tomada del FINAL de su identificador: los de Graph del mismo "
    "buzón coinciden en casi todo su largo, así que cortarlos por el principio da la misma cadena "
    "para todos y no permite seguir a ninguno.",
    "Nota"
))
story.append(P(
    "El informe de la corrida lleva los números de ESA corrida y nada más. La leyenda de cómo se "
    "lee —los cuatro niveles de resolución y cómo interpretar un aumento de `manual`— está en este "
    "documento, que es donde no se repite: en el informe eran diecinueve renglones fijos por "
    "delante de los datos, cuarenta y ocho veces al día.", "Nota"
))
story.append(P(
    "Un turno del modelo sin llamadas a herramientas significa que el agente cerró, salvo que lo que "
    "escribió sea la invocación que debió emitir. El historial le muestra sus propios turnos como "
    "`Invoco: herramienta(...)` y a veces devuelve esa línea como texto: no se ejecuta nada, el "
    "bucle se cierra y el campo que iba a reportar se pierde. `herramienta_pedida_en_texto` lo "
    "reconoce —exige el paréntesis de argumentos y un nombre del catálogo, para que mencionar una "
    "herramienta en una frase no cuente— y se le pide de nuevo, hasta "
    "`MAX_CORRECCIONES_DE_FORMATO` veces. Ese texto tampoco se acepta como la frase de cierre: "
    "describe lo que el modelo quería hacer, no por qué faltó un dato.", "Nota"
))
story.append(P(
    "La severidad distingue qué tan raro es el resultado, no si el sistema falló: un correo radicado "
    "sale como `INFO`, uno enviado a revisión manual como `WARNING` —el sistema hizo lo correcto al "
    "no inventar un dato, pero conviene que resalte sobre el resto— y uno fallido como `ERROR`, "
    "porque ahí nadie decidió nada sobre el correo. `Ejecucion.resumen()` (`pipeline/telemetria.py`) "
    "es lo que hace posible que esos campos existan: devuelve los números de la corrida —radicados, "
    "manual, duplicados, llamadas al modelo, porcentaje por origen, motivos de revisión manual— como "
    "un diccionario en vez de solo como texto, y es ese diccionario lo que `emitir()` adjunta al "
    "JSON de cada ejecución."
))

story.append(h2("Avance etapa por etapa"))
story.append(P(
    "Además de una entrada por correo, cada ETAPA anuncia su fin con el tiempo que tardó. Lo hace "
    "`Resultado.cronometrar()`, que al cerrar avisa a la traza que el orquestador recibe en su "
    "constructor (`Orquestador(traza=...)`); sin traza no imprime nada, que es como corren las "
    "pruebas. Una etapa que revienta también se anuncia, con el tipo de la excepción: es justo el "
    "caso en el que hace falta saber dónde se quedó."
))
story.append(P(
    "Con una sola entrada por correo, un fallo a mitad no dice en qué etapa ocurrió. Con esto la "
    "consola se lee como una secuencia — `deduplicacion`, `enrutamiento`, `descarga_adjuntos`, "
    "`conversion_a_markdown`, `saneamiento`, `extraccion_determinista`, `agente`, "
    "`registro_de_personas`, `compuerta`, `radicacion`, `cierre_outlook` — y en Cloud Logging cada "
    "una es un campo: `jsonPayload.etapa=\"agente\"` para ver solo lo que tarda el modelo, o "
    "`jsonPayload.fallo!=\"\"` para encontrar dónde se rompió.", "Nota"
))

story.append(h2("Radicar no es idempotente"))
story.append(P(
    "Cada llamada a `radicar-pqrd` crea un radicado nuevo, así que repetir una que falló puede "
    "dejar dos para el mismo correo. El código de error no basta para decidir: un 5xx describe lo "
    "que le pasó al servidor **después** de recibir la petición y no dice si alcanzó a crear el "
    "registro, y un tiempo de espera agotado es justo el caso donde más probable es que sí."
))
story.append(P(
    "Por eso la radicación solo se repite cuando consta que la petición no llegó a ejecutarse: la "
    "cortó Cloudflare en el borde, no se resolvió el nombre del servicio, o el token se rechazó "
    "antes de mirar el cuerpo (`errores.SIN_EFECTO_EN_EL_SERVIDOR`). Todo lo demás **no se repite**: "
    "el correo se aplaza y lo toma la corrida siguiente, que vuelve a pasar por las seis capas de "
    "deduplicación antes de radicar. Esperar media hora cuesta menos que un radicado duplicado.",
    "Nota"
))

story.append(h2("El documento que acompaña a la radicación"))
story.append(P(
    "Toda radicación viaja con al menos un documento: los adjuntos de la PQRD y, siempre, el correo "
    "mismo convertido a HTML (`pipeline/radicador.py::documento_del_correo`, con el nombre "
    "`correo.html`). Cumple dos funciones a la vez. Una PQRD sin adjuntos quedaría radicada sin "
    "ningún soporte —quien la atiende vería el número y los campos, pero no lo que la persona "
    "escribió—, y el endpoint de radicación solo acepta `multipart/form-data`: sin ningún archivo, "
    "el cliente HTTP envía la petición como formulario y el backend la rechaza."
))
story.append(P(
    "`RadicacionService` reconoce ese documento por su nombre y lo envía solo cuando la PQRD no "
    "trae adjuntos propios, para no duplicar en Alfresco algo que ya está en el cuerpo del caso. Si "
    "aun así la lista de archivos quedara vacía, la petición no sale: se corta nombrando la causa, "
    "porque el 500 que devolvería el backend no dice que faltó un documento.", "Nota"
))
story.append(P(
    "El asunto y el cuerpo se escapan antes de incrustarse en el HTML. Es texto que escribió un "
    "tercero y termina en un documento que abrirá un funcionario.", "Nota"
))

story.append(h2("Trazabilidad en SGDEA"))
story.append(P(
    "Una PQRD radicada deja además un registro de trazabilidad en SGDEA "
    "(`pipeline/radicador.py::Radicador._dejar_traza`), con el número de radicado, el estado "
    "inicial (`Por asignar`), la oficina a la que entró y quién la radicó. Es lo que permite "
    "auditar un caso sin mirar los logs del job: el número de radicado por sí solo no dice que lo "
    "puso la IA ni por qué cola entró."
))
story.append(P(
    "Un fallo al dejar la traza no tumba la radicación, que ya ocurrió y no se puede deshacer, pero "
    "queda en la `Bitacora`: sin traza el caso existe y nadie sabe de dónde salió.", "Nota"
))

story.append(h2("Qué preguntas responde el informe de una corrida"))
story.append(P(
    "`Ejecucion.informe()` arma, en este orden: cuántos correos se radicaron, quedaron duplicados o "
    "fueron a manual; una tabla de **quién resolvió cada campo** — algoritmo, IA, registro de "
    "personas o manual — por campo y en total, que es la lectura central para saber si el sistema "
    "está costando menos por el mismo resultado; el desglose de tiempo por etapa "
    "(`_lineas_de_tiempo`), calculado sobre lo medido y no sobre el total, para no repartir tiempo "
    "no medido entre etapas que en realidad no lo gastaron; el consumo del modelo "
    "(`pipeline/costo.py::Contador`); los motivos de revisión manual agrupados con su descripción "
    "en lenguaje llano, separando los que son de infraestructura (Cloudflare, SGDEA caído, sin OCR) "
    "de los que sí reflejan desempeño del modelo o del algoritmo; el detalle por correo; y los "
    "avisos agrupados por clase (`_clase_de_aviso` corta en el primer detalle variable — dos "
    "puntos, paréntesis o flecha — para que un nombre de archivo o un identificador no genere una "
    "clase de aviso distinta por cada correo)."
))
story.append(P(
    "En el detalle por correo, junto al asunto, aparece la línea `por que:` con la frase que el "
    "propio agente escribió sobre ese correo (sección 5). Es lo que permite entender un desenlace "
    "concreto sin abrir nada más: la etiqueta del correo lleva el código del motivo, igual para "
    "todos, y esta línea explica el caso. El mismo texto viaja a Cloud Logging como "
    "`jsonPayload.por_que`."
))
story.append(P(
    "`explica_el_desenlace()` decide cuándo se publica, y exige **las dos cosas**: que el correo se "
    "haya cerrado hacia una persona y que la causa la decidiera el contenido. La frase la escribe el "
    "agente al cerrar y dice qué dato NO encontró, así que en un correo radicado no explica nada — "
    "ese dato no impidió nada— y además se escribe antes de que el registro de personas rellene y "
    "antes de la compuerta, de modo que puede nombrar un campo que para cuando se radicó ya estaba "
    "resuelto. Publicarla ahí presenta como problema un correo que salió bien.", "Nota"
))

story.append(P(
    "Un correo reenviado no tiene número de radicado ni motivo de revisión manual, que es lo que "
    "describe a los demás desenlaces, y tampoco frase del agente: la decisión se toma antes de "
    "llamarlo. Su línea salía vacía, con el destino y la razón viviendo solo en el bloque de avisos, "
    "agregados por clase, de modo que no se podía saber a dónde fue un correo concreto. "
    "`area_destino` y `razon_del_reenvio` los llevan ahora a la línea del correo, al detalle del "
    "informe y a `jsonPayload.area_destino`."
))

story.append(h2("Consumo del modelo"))
story.append(P(
    "`pipeline/costo.py::Contador` acumula cada llamada al modelo (`Llamada`, con su etiqueta, "
    "modelo y tokens) durante la corrida. Las tarifas (`TARIFAS_USD_POR_MILLON`) están declaradas "
    "explícitamente en el código, con la fecha de vigencia, para que el cálculo sea auditable y "
    "actualizable; si el modelo usado no está en la tabla, el costo se reporta como \"no "
    "calculable\" en lugar de inventar un número."
))

story.append(h2("Atribución"))
story.append(P(
    "`pipeline/atribucion.py::Origen` es la enumeración que atraviesa telemetría, harness y el "
    "propio agente: `ALGORITMO`, `IA`, `REGISTRO` (lo aportó el registro de personas de SGDEA, no "
    "el correo) y `NO_RESUELTO` (nadie pudo — revisión manual). Se distingue `REGISTRO` de los "
    "otros dos porque su acierto no depende de leer bien el correo, sino de haber extraído bien el "
    "número de documento."
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 10. MEDICION
# ═══════════════════════════════════════════════════════════════
story.append(h1("10. Medición"))
story.append(P(
    "Ningún cambio a las reglas de extracción, al prompt del agente o a la compuerta se acepta sin "
    "medirlo contra el corpus. `harness/` es el conjunto de herramientas que hace esa medición "
    "posible, y `corpus/casos/` es el material sobre el que corren."
))

story.append(h2("El corpus"))
story.append(P(
    "Cada caso es un directorio con el correo y sus adjuntos ya convertidos a Markdown "
    "(`correo.md`, `hilo.md`, `adjuntos/*.md`), metadatos (`caso.json`: asunto, remitente, motivo "
    "por el que el caso está en el corpus, etiquetas para filtrar subconjuntos) y las etiquetas "
    "esperadas (`esperado.json`, modelo `harness/caso.py::Esperado`). Un campo en `None` significa "
    "\"este caso no afirma nada sobre ese campo\"; distinto de la cadena vacía, que significa "
    "\"debe quedar vacío\" — la diferencia es lo que permite etiquetar un caso parcialmente sin que "
    "los campos no revisados cuenten en contra de nada. Los correos son casos reales anonimizados, "
    "y su contenido se mantiene fuera del repositorio."
))
story.append(P(
    "`harness/caso.py::extraer_de()` es el punto único de entrada que comparten todas las "
    "herramientas de medición: corre la extracción determinista sobre un caso exactamente como lo "
    "haría el pipeline en producción."
))

story.append(h2("Las herramientas de medida"))
story.append(tabla(
    ["Herramienta", "Qué mide", "¿Llama al modelo?"],
    [
        ["harness/cobertura.py", "Cuánto resuelve la capa determinista y cuánto escala, y por qué motivo", "No"],
        ["harness/exactitud.py", "De lo que resuelve la capa determinista, cuánto es correcto contra las etiquetas", "No"],
        ["harness/enviado.py", "Lo que realmente llegaría a SGDEA tras la compuerta y los valores por defecto", "No"],
        ["harness/acierto_del_agente.py", "Compara lo que reportó el agente (resolucion.json) contra las etiquetas", "No (lee corridas ya hechas)"],
        ["harness/consumo.py", "Llamadas y tokens por correo, pipeline antiguo contra nuevo", "No"],
        ["harness/regresion.py", "Compara la tasa de resolución actual contra una referencia guardada", "No"],
        ["harness/probar_caso.py", "Corre un caso puntual por la capa determinista y la compuerta, campo por campo", "No"],
    ],
    anchos=[4.9 * cm, 8.4 * cm, 2.9 * cm],
    mono_cols=(0,),
))

story.append(h2("Cobertura no es lo mismo que exactitud"))
story.append(P(
    "`harness/cobertura.py` cuenta cuántas veces el algoritmo RESPONDE; `harness/exactitud.py` "
    "cuenta cuántas de esas respuestas son CORRECTAS contra la etiqueta del corpus. Un campo puede "
    "resolverse siempre y estar mal la mitad de las veces — separar las dos medidas es lo que "
    "impide confundir una cosa con la otra. `exactitud.py` además distingue lo `comparable` (tiene "
    "etiqueta contra la cual medir) de lo `supuesto` (un valor aplicado por ausencia de otra "
    "categoría, vía `METODOS_POR_DEFECTO`, que cuenta como cobertura pero no es un dato leído)."
))

story.append(h2("harness/enviado.py: lo que de verdad llega a SGDEA"))
story.append(P(
    "Entre el extractor y SGDEA hay dos capas que todavía cambian valores: la compuerta corrige, y "
    "el radicador (`pipeline/radicador.py`) rellena huecos con valores por defecto. "
    "`harness/enviado.py` arma el payload caso por caso — siempre, incluso cuando la compuerta "
    "rechazaría el correo, porque sin modelo ningún caso se radicaría y medir solo los aprobados no "
    "mediría nada — y distingue, entre los valores puestos por defecto, los que **afirman mal** "
    "(escriben un dato falso sobre la persona) de los que **se abstienen** (dejan el campo vacío o "
    "\"no determinado\", que no es un dato falso y queda anotado en las suposiciones)."
))

story.append(h2("Regresión"))
story.append(P(
    "`harness/regresion.py` guarda una instantánea de la tasa de resolución determinista por campo "
    "(`referencia.json`) y compara la medición actual contra ella, con una tolerancia de "
    "`TOLERANCIA_PP = 3` puntos porcentuales antes de marcar una caída como regresión — el corpus "
    "cambia cuando se recolecta más, así que una variación pequeña no es una regresión real. Un "
    "campo que deja de evaluarse también cuenta como regresión: significa que el extractor dejó de "
    "considerarlo."
))

story.append(h2("Lo medido"))
story.append(P(
    "El pipeline se ejecutó contra el buzón de producción con `MODO_TEST=false`, radicando de "
    "verdad. En dos corridas sobre 20 correos reales: 8 radicados con número de SGDEA, 4 duplicados "
    "detectados, 6 a revisión manual, 1 aplazado y 1 sin tomar por presupuesto de tiempo. El "
    "aplazado tuvo un `SGDEA_CAIDO` transitorio mientras los demás radicaban bien, y quedó en la "
    "bandeja con `REINTENTO:1` para que la corrida siguiente lo retomara."
))
story.append(tabla(
    ["Origen de la resolución", "% de los campos"],
    [
        ["Capa determinista (algoritmo)", "62%"],
        ["Agente único (modelo)", "13%"],
        ["Registro de personas (SGDEA)", "16%"],
        ["Revisión manual", "6%"],
    ],
    anchos=[9.6 * cm, 6.6 * cm],
))
story.append(P(
    "La geografía del afectado (`departamento_afectado`, `municipio_afectado`) llega, en la mayoría "
    "de los casos, desde el registro de personas: el correo casi nunca declara "
    "el departamento o el municipio de forma explícita, así que ese campo depende de que el número "
    "de documento se haya extraído bien, no de leer el correo. `direccion_afectado` se reparte de "
    "forma más pareja entre las cuatro fuentes: 12% capa determinista, 12% agente, 56% registro y "
    "20% manual."
))
story.append(P(
    "Que el municipio dependa del registro tiene una consecuencia que hay que tener presente: "
    "cuando el registro no responde, `_geografia` envía departamento y municipio POR DEFECTO —los "
    "dos juntos, para no describir un lugar que no existe— y eso queda anotado como suposición en "
    "el informe, pero en el registro de SGDEA figura como si fuera el domicilio de la persona. De "
    "ese dato depende la entrega por correo físico. El municipio no se extrae del texto en ninguna "
    "parte, y resolver un nombre de municipio a su identificador exige el catálogo del backend, así "
    "que hoy no hay otra fuente.", "Nota"
))
story.append(P(
    "Y el registro se está agotando por tiempo: de las veinte consultas anunciadas en una jornada, "
    "las veinte estaban entre 15,1 y 15,6 segundos, o sea todas contra el tope de "
    "`TIEMPO_MAXIMO`. Ese tope se paga por correo, así que veinte correos son cinco minutos de "
    "espera pura dentro de una ventana de treinta, sin sacar un solo dato. Tras "
    "`AGOTAMIENTOS_PARA_DEJAR_DE_PREGUNTAR` agotamientos SEGUIDOS se deja de consultar en esa "
    "corrida, diciendo por qué; cualquier respuesta —incluido un 404 o un 500, que prueban que el "
    "servicio contesta— reinicia la cuenta, para que un bache no apague la consulta durante el "
    "resto del lote.", "Nota"
))
story.append(P(
    "La exactitud de la capa determinista contra el corpus etiquetado es del 96% (420 de 434 campos "
    "comparables), sobre una cobertura del 58% de los campos. La suite de pruebas automatizadas "
    "cuenta 2.262 casos, escritos en 1.207 funciones de prueba."
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 11. DESPLIEGUE
# ═══════════════════════════════════════════════════════════════
story.append(h1("11. Despliegue"))

story.append(h2("El contenedor"))
story.append(P(
    "El `Dockerfile` parte de `python:3.12-slim` e instala tres dependencias de sistema: "
    "`tesseract-ocr` y `tesseract-ocr-spa` (OCR de adjuntos, sección 3) y `poppler-utils`. Corre "
    "como usuario sin privilegios (`appuser`, uid 1001). Copia `config/`, `models/`, "
    "`business_rules/` y `utils/` — los módulos de los que `pipeline/` y `servidor_mcp/` importan "
    "el catálogo de oficinas, los tipos y rangos de documento, y la configuración — y luego "
    "`pipeline/`, `servidor_mcp/` y `correr.py`."
))
story.append(caja_codigo(
"""CMD ["python", "correr.py", "--vertex"]"""
))
story.append(P(
    "La bandera `--vertex` va explícita en el comando del contenedor porque `correr.py` la exige: "
    "sin ella la corrida se detiene antes de construir el modelo (sección 7). Así ninguna corrida "
    "—ni en el Job ni en una prueba local que olvide la bandera— gasta en modelo por descuido."
))

story.append(h2("De dónde sale la imagen"))
story.append(P(
    "El despliegue lo hace `azure-pipelines.yml`. Dispara sobre `dev`, `uat` y `main`, toma el grupo "
    "de variables que corresponde a la rama (`vg-radicacion-ia-pqrd-{dev,uat,prod}`) y encadena dos "
    "etapas, ambas apoyadas en plantillas del repositorio `Infraestructura-DevOps/"
    "devops_util_pipeline`: `Build_Docker` construye y publica la imagen, y `Deploy_CloudRun` "
    "actualiza el Job. Los grupos de variables llevan solo coordenadas de despliegue —proyecto, "
    "registro, nombre del job—, no configuración de la aplicación."
))
story.append(tabla(
    ["Dónde vive la imagen del Job", "Valor"],
    [
        ["Registro", "us-east1-docker.pkg.dev/positiva-sgda-prd-416417/jobs"],
        ["Imagen", "ms-radicacion-ia-pqrd"],
        ["Etiquetas", "el identificador de compilación de Azure, y latest"],
    ],
    anchos=[4.2 * cm, 12.0 * cm],
))
story.append(P(
    "Cada compilación publica la imagen con dos etiquetas: una es el identificador numérico de la "
    "compilación, que no se reutiliza, y la otra es `latest`, que se mueve. El Job apunta a `latest`, así "
    "que la etiqueta numérica es la única referencia estable a una versión concreta: **para volver a "
    "una versión anterior hay que apuntar el Job a su etiqueta numérica**, porque `latest` ya "
    "señalará a la nueva. Conviene anotar esa etiqueta antes de desplegar, no después."
))
story.append(P(
    "`cloudbuild.yaml` está en el repositorio pero **no es el camino de despliegue**. Publica al "
    "repositorio `cloud-run-source-deploy` del registro, que no es de donde sale la imagen del Job, "
    "y los parámetros que declara no son los que el Job tiene aplicados: pide 2 GiB de memoria, "
    "540 segundos de tiempo límite y 2 reintentos, frente a los 8 GiB, 3.600 segundos y 0 reintentos "
    "efectivos. Guiarse por ese archivo para desplegar o para revertir lleva al registro equivocado.",
    "Nota"
))

story.append(h2("El Cloud Run Job"))
story.append(P(
    "Se dispara cada 30 minutos desde Cloud Scheduler. `RUTA_LOGS` viene fijada en el propio "
    "`Dockerfile` a una dirección `gs://` —va versionada con el código, sin depender de la "
    "configuración del Job— y `RUTA_WORKSPACE` sigue siendo configurable por variable de entorno. "
    "El informe de cada ejecución también se imprime por salida estándar (`correr.py`), que es lo "
    "que recoge Cloud Logging con independencia de que el bucket esté o no accesible."
))
story.append(tabla(
    ["Parámetro", "Valor", "Consecuencia"],
    [
        ["Cadencia", "*/30 * * * *", "Una corrida cada media hora"],
        ["Tareas", "1", "Una sola instancia: no hay dos corridas en paralelo por diseño del Job"],
        ["Reintentos", "0", "Una corrida que falla no se repite; sus correos los toma la siguiente"],
        ["Tiempo límite", "3.600 s", "Muy por encima del presupuesto de la corrida, que corta antes"],
        ["Memoria / CPU", "8 GiB / 2", "Cubre el OCR de adjuntos grandes en paralelo"],
    ],
    anchos=[3.0 * cm, 2.6 * cm, 10.6 * cm],
))
story.append(P(
    "Quien limita de verdad la corrida es el presupuesto de tiempo del código (sección 12), no el "
    "tiempo límite del Job: el presupuesto se deriva de la cadencia y corta mucho antes de los "
    "3.600 segundos. El tiempo límite del Job es la red de seguridad de último recurso, y que no sea "
    "él quien corte es lo que permite que los correos no atendidos queden sin marca y los tome la "
    "corrida siguiente, en vez de morir a mitad de un proceso.", "Nota"
))
story.append(P(
    "La cuenta de servicio del Job necesita **solo** `storage.objects.create` sobre el prefijo de "
    "los informes. No hace falta permiso de borrado: nada se reemplaza, cada corrida crea sus "
    "propios objetos. Leer la serie —que es una consulta, no parte de la corrida— necesita además "
    "`storage.objects.list` y `get`. Sin permiso de escritura la corrida sigue funcionando y el "
    "informe llega igual a Cloud Logging; lo que se pierde es la serie histórica.", "Nota"
))
story.append(P(
    "La autenticación contra Vertex intenta primero credenciales por defecto de la aplicación "
    "(ADC, `google.auth.default`) — lo que hay en el Job, vía la cuenta de servicio — y solo si eso "
    "falla recurre a un token de la sesión de `gcloud` (`_credenciales_de_vertex`), que es lo que "
    "suele haber en una máquina de desarrollo. El mismo código sirve en los dos sitios sin una "
    "bandera adicional que alguien tenga que recordar."
))

story.append(h2("El interruptor de seguridad: MODO_TEST"))
story.append(P(
    "`config/settings.py` exige como variables de entorno obligatorias solo los secretos "
    "(credenciales de Microsoft Graph, Supabase y SGDEA); el resto de la configuración va "
    "fija en el código. `MODO_TEST` es el interruptor que decide si la corrida escribe de verdad: "
    "`correr.py` exige `MODO_TEST=true` para aceptar la bandera `--seco` (que además de eso, "
    "tampoco radica ni mueve el correo — construye el `Orquestador` con `radicador=None` y "
    "`outlook=None`), y si se corre sin `--seco` con `MODO_TEST=false` imprime una advertencia "
    "explícita: \"esto RADICA DE VERDAD en SGDEA\"."
))

story.append(h2("Servicios de deduplicación opcionales"))
story.append(P(
    "`_auditoria()` en `correr.py` intenta construir el cliente de la auditoría; si falla, la "
    "corrida sigue sin esa capa y lo anota como aviso en vez de detener el lote. La caché de hashes "
    "no se construye. El `Detector` funciona sin cualquiera de los dos: las capas que no dependen "
    "de red siguen en pie y la pérdida queda anotada en el motivo."
))

story.append(h2("El aviso del resultado"))
story.append(P(
    "El pipeline **nunca le escribe a quien remitió el correo**. Un ciudadano, una empresa o un "
    "juzgado no reciben de aquí que un sistema automático no pudo con su caso: de responderle a "
    "quien escribe se encarga la entidad. La única salida de correo del pipeline es el reenvío de "
    "los correos que se derivan a otra área (sección 3), con una cabecera que declara que lo generó "
    "una IA y nombra el área destino."
))
story.append(P(
    "`pipeline/notificacion.py::redactar()` construye además un aviso con el desenlace, que se "
    "remite a `CORREO_DE_AVISOS` — un buzón interno de revisión. Va **vacío por defecto**, y sin "
    "destino configurado no se envía nada. El desenlace consta igual en la categoría del correo, en "
    "el informe de la corrida y en Cloud Logging."
))
story.append(P(
    "Cada motivo de revisión manual tiene dos redacciones que no son intercambiables: "
    "`telemetria.MOTIVOS_MANUAL` es la interna, del informe, y nombra componentes (\"la compuerta "
    "rechazó los datos\"); `notificacion.MOTIVO_PARA_EL_CORREO` es la externa y dice qué faltó, no "
    "qué pieza falló. Una prueba exige que todo código tenga las dos y prohíbe mecánicamente una "
    "lista de términos internos en el texto que sale.", "Nota"
))

story.append(h2("A qué backend se escribe"))
story.append(P(
    "Las tres escrituras —radicar, trazabilidad y asignación— y la consulta al registro van al host "
    "de `MS_SGDEA_BETA_URL`. El nombre lleva \"beta\" y el host es el que atiende: sirve "
    "`/pqrd/radicar-pqrd`, `/personas`, `/trazabilidad/crear` y también "
    "`/configuracion-asignacion-ia/…`, y es de donde salen los números de radicado reales."
))
story.append(P(
    "En la misma configuración hay un `CONFIGURACION_ASIGNACION_IA_URL` apuntando a un host hermano "
    "con \"prd\" en el nombre. Ese host **no sirve ninguna de esas rutas**: responde 404 en todas, "
    "incluidas las dos que su propio cliente invoca. Solo lo usa el pipeline anterior, que ya no es "
    "el punto de entrada, así que no afecta a la radicación; pero el par de nombres invita a "
    "suponer que uno es de pruebas y el otro de producción, y no es el caso.", "Nota"
))
story.append(P(
    "Ninguna de las dos direcciones se sobrescribe por entorno: no están entre las variables del "
    "Job, así que valen lo que diga el código y son las mismas en los tres ambientes.", "Nota"
))

story.append(h2("Registro de personas"))
story.append(P(
    "`pipeline/personas.py::ConsultaDePersonas` consulta el registro de SGDEA (`MS_SGDEA_BETA_URL`) "
    "solo por documento y solo en lectura; se conecta también en modo seco, porque consultar no "
    "escribe nada. Es la única fuente de la que el pipeline rellena `telefono_afectado`, "
    "`direccion_afectado`, `sexo_afectado`, `departamento_afectado` y `municipio_afectado` cuando "
    "el correo no los trae — nunca el nombre, porque rellenar el nombre convertiría un caso "
    "destinado a revisión manual en una radicación automática sin que nadie lo haya confirmado."
))
story.append(P(
    "El registro se consulta POR DOCUMENTO, así que todo lo que devuelve descansa en que ese "
    "documento sea el del afectado. Si el correo también trae su nombre y no es el mismo, uno de "
    "los dos datos está mal y no se puede saber cuál: el nombre pudo salir de un rótulo del asunto, "
    "o el documento pudo ser el de otra persona del mismo correo. Antes se anotaba y se radicaba "
    "igual; ahora **el correo se cierra a `DOCUMENTO_DE_OTRA_PERSONA`** y lo mira una persona. "
    "Radicar escribe una identidad que no existe: o la PQRD queda a nombre de quien no la puso, o "
    "queda con la cédula de otro."
))
story.append(P(
    "La comparación exige que TODAS las palabras del nombre más corto estén en el más largo, así "
    "que el registro trayendo los dos apellidos y el correo uno sigue siendo la misma persona. Un "
    "desacuerdo es cero palabras en común.", "Nota"
))
story.append(P(
    "El registro y el payload de radicación codifican el sexo con catálogos invertidos entre sí: en "
    "el payload 1 acompaña nombres masculinos y 2 femeninos, y el registro devuelve 2 para las "
    "personas cuyo dato declara masculino. `SEXO_DEL_GENERO_DEL_REGISTRO` traduce el valor en lugar "
    "de copiarlo, y solo para los dos códigos comprobados — cualquier otro código deja el campo sin "
    "resolver, porque un valor que nunca se ha visto no se puede traducir con la misma certeza."
))
story.append(P(
    "`departamento_afectado` y `municipio_afectado` viajan juntos: si solo se resuelve uno de los "
    "dos, `pipeline/radicador.py::_geografia` descarta también el otro y envía los dos valores por "
    "defecto, porque un municipio de un departamento emparejado con otro departamento describe un "
    "lugar que no existe."
))
story.append(P(
    "`pipeline/geografia.py` los lee del texto antes de que intervenga el registro. Sin eso el "
    "municipio solo lo aportaba el registro de personas, y cuando el registro no tiene a la persona "
    "el radicado sale diciendo que vive en la capital: una ciudad que nadie leyó, de la que depende "
    "la entrega cuando la respuesta va por correo físico."
))
story.append(P(
    "El identificador de municipio lo decide SGDEA y no se puede componer a partir del nombre, así "
    "que `business_rules/municipios.json` congela el catálogo que devuelve el backend — 1.121 "
    "municipios en 33 departamentos — en lugar de pagar una consulta por correo contra un servicio "
    "que no siempre responde. Se regenera cuando el catálogo cambie."
))
story.append(P(
    "La lectura es deliberadamente conservadora, porque un municipio equivocado manda la respuesta a "
    "otra ciudad y eso es peor que el valor por defecto con su anotación. Solo se lee dentro de un "
    "rótulo explícito —`Ciudad:`, `Municipio:`, `resido en`— nunca de la prosa suelta. El nombre que "
    "aparece en varios departamentos exige que el texto nombre el suyo; si lo nombra y el municipio "
    "no pertenece a él, la pareja se contradice y no se resuelve nada. Y `ciudad` va con frontera de "
    "palabra: sin ella la captura `ciudadanía`, y lo que sigue a una cédula es la ciudad donde se "
    "expidió, no donde vive la persona.", "Nota"
))
story.append(P(
    "`_geografia` es el ÚNICO punto que decide esos dos valores, y lo que decide queda en las "
    "suposiciones del informe. `RadicacionPayload.to_form_data` no vuelve a sustituir el municipio "
    "vacío por uno concreto: hacerlo afirmaba dónde vive la persona en el último paso, después de "
    "que el informe ya se hubiera escrito, así que el radicado salía con un municipio que nadie "
    "leyó y nada lo decía. El valor por defecto del propio `RadicacionPayload` sigue existiendo "
    "para quien construya un payload sin fijarlos.", "Nota"
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# 12. LA VENTANA DE LA CORRIDA
# ═══════════════════════════════════════════════════════════════
story.append(h1("12. La ventana de la corrida"))
story.append(P(
    "El job arranca cada 30 minutos. Una corrida que se pase de esa ventana se solapa con la "
    "siguiente, y entonces dos procesos miran la misma bandeja: es exactamente la situación en la "
    "que un correo puede trabajarse dos veces. Cuatro mecanismos la acotan."
))

story.append(h2("Cuántos correos toma cada corrida"))
story.append(P(
    "`CORREOS_POR_CORRIDA` fija el tamaño del lote, 50 por defecto y ajustable por entorno sin "
    "desplegar. Multiplicado por los 48 disparos diarios del scheduler es el techo de lo que la "
    "bandeja puede desaguar en un día, y ese techo tiene que quedar por encima del ritmo de "
    "llegada: por debajo, lo que no se toma se represa y la cola crece sin que nada falle."
))
story.append(P(
    "El lote es un techo, no una carga: la corrida toma lo que haya y termina. Con la bandeja al "
    "día no hay cincuenta correos esperando, y la corrida vuelve a durar un par de minutos."
))
story.append(P(
    "Llegada medida sobre 48 horas, contando las seis carpetas donde un correo puede acabar: 1.398 "
    "comunicaciones, 699 al día, concentradas entre las 8 y las 17 de Bogotá con puntas de 90 por "
    "hora. De los correos que una corrida toma, el 82% sale de la bandeja con un desenlace y el "
    "resto se aplaza y vuelve a la cola, así que el lote se dimensiona contra ese 82%.", "Nota"
))
story.append(P(
    "El lote no lo acota el reloj. Repartiendo entre los cinco cupos de la concurrencia las "
    "duraciones reales de 499 correos de producción, en 20.000 repeticiones, un lote de 50 tarda "
    "6,1 minutos de mediana, 9,3 en el percentil 99 y 11,4 en el peor caso, contra los 20 minutos "
    "de presupuesto. Quien corta de verdad es el presupuesto, y lo hace antes de empezar un correo "
    "nuevo."
))

story.append(h2("Presupuesto de tiempo"))
story.append(P(
    "`pipeline/presupuesto.py` acota la corrida **entera**, no cada llamada. Por defecto dos "
    "tercios de la cadencia —20 minutos de los 30—, ajustable con `PRESUPUESTO_S` sin desplegar. "
    "`correr.py` lo comprueba antes de cada correo: si no queda tiempo, los correos restantes se "
    "quedan **sin marca alguna**, tal como llegaron, y la corrida siguiente los ve como nuevos. Es "
    "lo único que no puede producir un duplicado."
))
story.append(P(
    "Dentro de un correo, `recortar()` acota cada espera de reintento contra lo que queda. Si el "
    "tiempo se agota con el correo a medias, el desenlace es `TIEMPO_DE_CORRIDA_AGOTADO`, que está "
    "entre los motivos que se aplazan: vuelve a la bandeja con su cuenta, no a revisión manual."
))

story.append(h2("Tope de un solo correo"))
story.append(P(
    "El presupuesto se comprueba **antes** de empezar cada correo, de modo que uno que se queda "
    "esperando dentro de una llamada no lo consulta nunca: retiene su cupo y con él a la corrida "
    "entera, hasta que el Job la corta a la hora. Ocurrió: una llamada al modelo se quedó 58 "
    "minutos sin volver y esa corrida consumió la ventana de dos.", "Nota"
))
story.append(P(
    "Dos topes lo cierran. `TIMEOUT_DEL_MODELO_MS` acota cada llamada al modelo en 90 segundos —una "
    "normal tarda segundos, así que solo alcanza a las que no van a responder— y el fallo que "
    "produce es reintentable, de modo que el correo se aplaza en vez de perderse. `TOPE_POR_CORREO_S` "
    "abandona a los 10 minutos el correo que sigue sin terminar: conserva su marca de reclamación, "
    "así que la corrida siguiente lo retoma con la cuenta puesta y a la tercera va a una persona. El "
    "valor está muy por encima del correo más lento medido en producción, 4,3 minutos, para que solo "
    "alcance al que no va a terminar. Un correo abandonado se cuenta aparte en el resumen: no tiene "
    "desenlace y sin esa cuenta la corrida no cuadraría."
))

story.append(h2("Varios correos a la vez"))
story.append(P(
    "Casi todo el tiempo de un correo es espera de red —el modelo, Graph, SGDEA— y en serie la "
    "máquina no hace nada mientras espera. `correr.py` atiende el lote con un semáforo de "
    "`CONCURRENCIA` correos simultáneos, 5 por defecto."
))
story.append(P(
    "La concurrencia por sí sola no daba nada: `modelo.generar()` y `materializar()` son síncronas "
    "y bloqueaban el bucle de eventos durante los segundos que tardan, de modo que los correos "
    "\"en paralelo\" hacían fila. Las dos van ahora a un hilo con `asyncio.to_thread`. Medido con "
    "Vertex y OCR reales sobre el mismo lote de 10 correos: 310 segundos en serie contra 78 de a "
    "cinco, es decir de 31 a 7,8 segundos por correo.", "Nota"
))
story.append(P(
    "Lo que hace segura la concurrencia es que nada de un correo vive en el orquestador: la bitácora "
    "va en un `ContextVar` y no en un atributo — como atributo, los fallos de un correo acababan en "
    "el informe de otro."
))

story.append(h2("Aplazar en vez de cerrar hacia una persona"))
story.append(P(
    "Un fallo de infraestructura no dice nada del correo. Si el modelo no responde o SGDEA se cae, "
    "mandarlo a una cola humana le cuesta tiempo a alguien para que descubra que no había nada que "
    "revisar, y además lo saca de la bandeja —que es justo de donde lo tomaría la corrida en la que "
    "el servicio ya responde—. `pipeline/reintentos.py` lo aplaza en su lugar."
))
story.append(tabla(
    ["Motivo", "Qué pasó"],
    [
        ["MODELO_NO_RESPONDIO", "El modelo no contestó a tiempo o no estaba disponible."],
        ["MODELO_SIN_PERMISO", "El modelo rechazó la petición por autorización."],
        ["SGDEA_CAIDO", "El backend de radicación no respondió."],
        ["CLOUDFLARE_403", "Cloudflare devolvió un captcha al radicar."],
        ["PETICION_MAL_FORMADA", "El backend rechazó la forma de la petición."],
        ["SIN_OCR", "La imagen del contenedor no trae el motor de OCR."],
        ["TIEMPO_DE_CORRIDA_AGOTADO", "La corrida se quedó sin presupuesto con el correo a medias."],
        ["ERROR_INESPERADO", "El fallo no lo reconoció ninguna firma del catálogo."],
    ],
    anchos=[5.6 * cm, 10.6 * cm],
    mono_cols=(0,),
))
story.append(P(
    "El criterio es uno solo: **si quien recibe el correo no puede hacer nada con él, no se le "
    "manda**. Un permiso que falta, un motor de OCR ausente o una petición mal formada no se "
    "resuelven mirando el correo — se resuelven con un despliegue o una autorización— y ninguno "
    "dura menos de media hora, que es justo para lo que está el tope de ciclos. Lo que SÍ llega a "
    "una persona es lo que ella puede resolver: un dato que falta, un adjunto ilegible o protegido, "
    "datos que no cuadran, o que no sea una PQRD.", "Nota"
))
story.append(P(
    "El correo se queda en la bandeja con `REINTENTO:N` y sin marca de proceso. El límite es "
    "`MAX_CICLOS = 12`: con el job cada 30 minutos son seis horas de caída absorbidas antes de "
    "cerrar hacia una persona. Los demás motivos de infraestructura quedan fuera a propósito — "
    "`MODELO_SIN_PERMISO` no es pasajero, y un adjunto ilegible no se vuelve legible en media hora."
))
story.append(P(
    "La cuenta vive en la etiqueta del correo, y escribirla puede fallar. `services/outlook.py` "
    "reintenta esa escritura ante un corte de red o un 5xx (`INTENTOS_POR_FALLO_DE_RED`), que es "
    "justo lo que se repara solo; un 412 NO se reintenta, porque dice que otra corrida ya tomó el "
    "correo. Si aun así la marca no queda escrita, el correo se cierra a revisión manual en esa "
    "misma corrida: sin cuenta volvería a aplazarse indefinidamente y no llegaría nunca a una "
    "persona."
))
story.append(P(
    "`ERROR_INESPERADO` entra en la lista por lo que ES: el cajón de la clase `DESCONOCIDO`, donde "
    "cae todo fallo que ninguna firma reconoce. No saber qué falló no autoriza a afirmar que el "
    "problema está en el correo, que es exactamente lo que dice mandarlo a una persona. Si el fallo "
    "resulta ser del correo, las doce corridas lo devuelven igual a revisión manual; si era del "
    "entorno, se radica solo. La asimetría manda: cerrar en falso cuesta el tiempo de alguien que no "
    "tiene nada que revisar.", "Nota"
))
story.append(P(
    "Para poder escribir la firma que falta, `Fallo.excepcion` conserva el TIPO de la excepción "
    "—sólo cuando el fallo cayó en `DESCONOCIDO`— y aparece en `linea_log`. Sin eso, la clase pedía "
    "\"mirar la traza\" sin dejar ninguna."
))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# Fin del documento
# ═══════════════════════════════════════════════════════════════
story.append(h1("Referencias rápidas de código"))
story.append(P("Puntos de entrada y módulos centrales, para navegar el repositorio:"))
refs = [
    ("correr.py", "Punto de entrada del pipeline nuevo."),
    ("pipeline/orquestador.py", "Orquestador.procesar() — el flujo completo de un correo."),
    ("pipeline/extraccion.py", "extraer_todo() y todos los extractores deterministas por campo."),
    ("pipeline/agente.py", "resolver() — el bucle del agente único, y su prompt de sistema."),
    ("servidor_mcp/herramientas.py", "Las siete herramientas del agente e invocar()."),
    ("pipeline/compuerta.py", "validar() — la compuerta de coherencia previa a radicar."),
    ("pipeline/radicador.py", "armar_payload() y Radicador.radicar() — envío a SGDEA."),
    ("pipeline/modelo.py", "El protocolo Modelo y sus implementaciones."),
    ("pipeline/errores.py", "clasificar(), Bitacora — taxonomía de fallos y reintentos."),
    ("pipeline/telemetria.py", "Ejecucion.informe(), Ejecucion.resumen() — el reporte por corrida."),
    ("pipeline/salida.py", "emitir() — formato de salida por consola: JSON en la nube, texto en terminal."),
    ("pipeline/personas.py", "rellenar_desde_el_registro() — consulta a SGDEA."),
    ("pipeline/duplicados.py", "Detector — las seis capas de deduplicación."),
    ("pipeline/enrutamiento.py", "decidir() — a Medicina Laboral, a notificaciones judiciales o RADICAR."),
    ("pipeline/reclamo.py / marcas.py", "Reclamo atómico del correo y las marcas que quedan sobre él."),
    ("pipeline/reintentos.py", "decidir() — qué se aplaza a la corrida siguiente y qué no."),
    ("pipeline/presupuesto.py", "El tiempo que le queda a la corrida."),
    ("pipeline/almacen.py", "Informes en disco o en un bucket, con la misma interfaz."),
    ("pipeline/notificacion.py", "redactar() — el aviso del desenlace, para el buzón interno."),
    ("pipeline/materializar.py / conversion.py", "Conversión de adjuntos a Markdown, OCR."),
    ("pipeline/saneamiento.py", "detectar(), sanear_fragmento() — defensa contra inyección."),
    ("harness/caso.py", "Caso, Esperado, extraer_de() — el corpus y su punto de entrada."),
    ("harness/exactitud.py, cobertura.py, enviado.py", "Medición contra el corpus etiquetado."),
]
story.append(tabla(
    ["Módulo", "Contenido"],
    [[k, v] for k, v in refs],
    anchos=[6.0 * cm, 10.2 * cm],
    mono_cols=(0,),
))

# ─────────────────────────── Construir ───────────────────────────

doc = Documento(str(SALIDA), title="Arquitectura del pipeline PQRD — Positiva ARL",
                 author="Jonathan C")
doc.multiBuild(story)
print(f"PDF generado: {SALIDA}")
