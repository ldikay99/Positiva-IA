"""
OCR de adjuntos: preparacion de la imagen, giro y confianza.

Las fotos de cedula llegan pequeñas o de lado y sin preparar se leian como
ruido. Las pruebas con tesseract usan imagenes sinteticas dibujadas con PIL y
se saltan si el binario (o el modelo de orientacion) no esta en el entorno.
"""
from __future__ import annotations

import io

import pytest

from pipeline import conversion
from pipeline.conversion import (
    CONFIANZA_DUDOSA,
    _LecturaDePagina,
    _estimar_inclinacion,
    _linea_dudosa,
    _marca_ocr,
    _recortar_bordes,
    _texto_de_imagen,
    _texto_y_confianza,
    _umbral_otsu,
    convertir,
)

Image = pytest.importorskip("PIL.Image")
from PIL import ImageDraw, ImageFont  # noqa: E402

NUMERO = "41234567"
LINEAS = [
    "REPUBLICA DE COLOMBIA",
    "IDENTIFICACION PERSONAL",
    "CEDULA DE CIUDADANIA",
    f"NUMERO {NUMERO}",
    "APELLIDOS PEREZ GOMEZ",
    "NOMBRES JUAN CARLOS",
    "Solicito la calificacion de origen",
    "de la enfermedad reportada en marzo.",
]


def _fuente(tamano: int):
    for nombre in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(nombre, tamano)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tamano)
    except TypeError:
        pytest.skip("sin fuente escalable para dibujar el texto de prueba")


def _documento(ancho: int = 1700, tamano: int = 48) -> "Image.Image":
    """Una hoja blanca con texto negro, como un escaneo limpio."""
    alto = int(ancho * 0.75)
    imagen = Image.new("RGB", (ancho, alto), "white")
    dibujo = ImageDraw.Draw(imagen)
    fuente = _fuente(tamano)
    y = tamano
    for linea in LINEAS:
        dibujo.text((tamano, y), linea, fill="black", font=fuente)
        y += int(tamano * 1.6)
    return imagen


def _con_tesseract(osd: bool = False) -> None:
    pytesseract = pytest.importorskip("pytesseract")
    try:
        pytesseract.get_tesseract_version()
        idiomas = set(pytesseract.get_languages(config=""))
    except Exception:
        pytest.skip("sin binario de tesseract en este entorno")
    if "spa" not in idiomas:
        pytest.skip("tesseract sin el idioma spa")
    if osd and "osd" not in idiomas:
        pytest.skip("tesseract sin el modelo de orientacion (osd)")


def _digitos(texto: str) -> str:
    return "".join(c for c in texto if c.isdigit())


# ── Sin tesseract: la forma del markdown y las piezas de la preparacion ──

def test_la_marca_de_ocr_lleva_la_confianza_y_sigue_empezando_por_ocr():
    assert _marca_ocr(None) == "(OCR)"
    assert _marca_ocr(0.583) == "(OCR, confianza 0.58)"
    assert _marca_ocr(0.9).startswith("(OCR")


def test_solo_la_lectura_floja_se_marca_dudosa():
    assert _linea_dudosa(None, "pagina") == ""
    assert _linea_dudosa(CONFIANZA_DUDOSA, "pagina") == ""
    assert "lectura dudosa" in _linea_dudosa(CONFIANZA_DUDOSA - 0.01, "pagina")


def test_la_lectura_de_pagina_sigue_siendo_un_par():
    """Quien desempaqueta (texto, motivo) no se rompe por la confianza."""
    lectura = _LecturaDePagina("hola", "", 0.7)
    texto, motivo = lectura
    assert (texto, motivo) == ("hola", "")
    assert lectura.confianza == 0.7


def test_la_confianza_se_pondera_por_longitud_y_separa_parrafos():
    datos = {
        "text": ["CEDULA", "41234567", "", "x", "Otro"],
        "conf": ["90", "80", "-1", "10", "70"],
        "block_num": [1, 1, 1, 1, 2],
        "par_num": [1, 1, 1, 1, 1],
        "line_num": [1, 2, 2, 2, 1],
    }
    texto, confianza = _texto_y_confianza(datos)
    assert texto == "CEDULA\n41234567 x\n\nOtro"
    esperada = (90 * 6 + 80 * 8 + 10 * 1 + 70 * 4) / (6 + 8 + 1 + 4) / 100
    assert confianza == pytest.approx(esperada)
    assert _texto_y_confianza({"text": []}) == ("", 0.0)


def test_otsu_separa_tinta_de_fondo():
    gris = Image.new("L", (100, 100), 230)
    ImageDraw.Draw(gris).rectangle((10, 10, 40, 40), fill=20)
    assert 20 <= _umbral_otsu(gris) < 230


def test_se_recorta_el_marco_negro_del_escaner():
    gris = Image.new("L", (400, 500), 0)
    ImageDraw.Draw(gris).rectangle((30, 30, 369, 469), fill=255)
    recortada = _recortar_bordes(gris)
    assert recortada.size[0] < 400 and recortada.size[1] < 500


def test_la_inclinacion_fina_se_detecta():
    imagen = _documento(ancho=1400, tamano=40).convert("L")
    girada = imagen.rotate(3, resample=Image.BICUBIC, expand=True, fillcolor=255)
    # El angulo que la endereza es el contrario al del giro.
    assert _estimar_inclinacion(girada) == pytest.approx(-3, abs=1.0)
    assert abs(_estimar_inclinacion(imagen)) < 1.0


def test_la_pagina_ocr_lleva_confianza_y_aviso_en_el_markdown(monkeypatch):
    """El encabezado sigue conteniendo "(OCR" para `ocr_aplicado`."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page()  # sin texto: es lo que dispara el OCR
    pdf = doc.tobytes()
    doc.close()

    monkeypatch.setattr(
        conversion, "_ocr_de_pagina",
        lambda pagina, *_: _LecturaDePagina("CEDULA 41234567", "", 0.4))
    c = convertir(pdf, "escaneo.pdf")
    assert "## Pagina 1 (OCR, confianza 0.40)" in c.markdown
    assert "lectura dudosa" in c.markdown
    assert c.ocr_aplicado


# ── Con tesseract: el giro se corrige y la confianza se reporta ──

def test_una_hoja_derecha_se_lee_con_confianza_alta():
    _con_tesseract()
    texto, confianza, fallo = _texto_de_imagen(_documento(), 25)
    assert fallo == ""
    assert NUMERO in _digitos(texto)
    assert 0.0 < confianza <= 1.0
    assert confianza >= CONFIANZA_DUDOSA


@pytest.mark.parametrize("grados", [90, 180, 270])
def test_una_imagen_girada_se_endereza(grados):
    """La foto de la cedula tomada de lado o al reves."""
    _con_tesseract(osd=True)
    girada = _documento().rotate(grados, expand=True, fillcolor="white")
    texto, confianza, fallo = _texto_de_imagen(girada, 25)
    assert fallo == ""
    assert NUMERO in _digitos(texto), texto
    assert "CEDULA" in texto.upper()
    assert confianza >= CONFIANZA_DUDOSA


def test_una_foto_pequena_se_amplia_antes_de_leer():
    """Una cedula de 500 px: sin ampliarla tesseract pierde digitos."""
    _con_tesseract()
    pequena = _documento().resize((500, 375), Image.LANCZOS)
    texto, confianza, fallo = _texto_de_imagen(pequena, 25)
    assert fallo == ""
    assert NUMERO in _digitos(texto), texto
    assert confianza > 0.0


def test_una_foto_pequena_y_de_lado_se_lee():
    _con_tesseract(osd=True)
    foto = _documento().resize((600, 450), Image.LANCZOS).rotate(
        90, expand=True, fillcolor="white")
    texto, _confianza, fallo = _texto_de_imagen(foto, 25)
    assert fallo == ""
    assert NUMERO in _digitos(texto), texto


def test_la_imagen_suelta_reporta_su_confianza_en_el_markdown():
    _con_tesseract()
    buf = io.BytesIO()
    _documento().save(buf, format="PNG")
    c = convertir(buf.getvalue(), "cedula.png")
    assert c.ocr_aplicado
    assert "## Imagen (OCR, confianza " in c.markdown
    assert NUMERO in c.markdown


def test_el_giro_no_se_sale_del_tope_de_la_pagina(monkeypatch):
    """Si la primera lectura agota el tope, no hay OSD ni relectura."""
    _con_tesseract(osd=True)
    llamadas = []
    original = conversion._orientacion
    monkeypatch.setattr(conversion, "_orientacion",
                        lambda *a, **k: (llamadas.append(1), original(*a, **k))[1])
    girada = _documento().rotate(90, expand=True, fillcolor="white")
    _texto_de_imagen(girada, 0.5)
    assert not llamadas
