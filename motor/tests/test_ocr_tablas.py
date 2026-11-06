"""
OCR de hojas escaneadas con tablas: cuadricula borrada y segunda lectura de
cifras. Las planillas traen la cedula de cada trabajador dentro de una celda.
"""
from __future__ import annotations

import pytest

from pipeline import conversion
from pipeline.conversion import _sin_cuadricula

Image = pytest.importorskip("PIL.Image")
np = pytest.importorskip("numpy")
from PIL import ImageDraw, ImageFont  # noqa: E402


def _fuente(tamano: int):
    for nombre in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(nombre, tamano)
        except OSError:
            continue
    pytest.skip("sin fuente escalable para dibujar el texto de prueba")


CEDULAS = ["1062965207", "52203642", "1030643973", "79044618"]


def _planilla() -> "Image.Image":
    """Una tabla con rejilla y una cedula por fila, como una planilla."""
    ancho, alto_fila = 2400, 110
    imagen = Image.new("L", (ancho, alto_fila * (len(CEDULAS) + 1) + 40), 255)
    d = ImageDraw.Draw(imagen)
    fuente = _fuente(46)
    cols = [20, 160, 300, 800, 2380]
    for i, ced in enumerate(["CEDULA"] + CEDULAS):
        y = 20 + i * alto_fila
        d.text((cols[1] + 20, y + 30), "CC" if i else "TIPO", fill=0, font=fuente)
        d.text((cols[2] + 20, y + 30), ced, fill=0, font=fuente)
        d.text((cols[3] + 20, y + 30), "TRABAJADOR" if i else "NOMBRE", fill=0, font=fuente)
    for i in range(len(CEDULAS) + 2):
        y = 20 + i * alto_fila
        d.line([(cols[0], y), (cols[-1], y)], fill=0, width=4)
    for x in cols:
        d.line([(x, 20), (x, 20 + (len(CEDULAS) + 1) * alto_fila)], fill=0, width=4)
    return imagen


def test_borra_las_lineas_de_tabla_y_respeta_las_letras():
    hoja = _planilla()
    limpia = np.asarray(_sin_cuadricula(hoja))
    original = np.asarray(hoja)
    # La raya horizontal de arriba desaparece.
    assert original[21, 1000] < 128 and limpia[21, 1000] == 255
    # La raya vertical de la primera columna desaparece.
    assert original[300, 161] < 128 and limpia[300, 161] == 255
    # El texto queda: sigue habiendo tinta en la zona de las cedulas.
    zona = limpia[150:250, 320:780]
    assert (zona < 128).sum() > 500


def test_una_hoja_sin_tabla_queda_igual():
    hoja = Image.new("L", (1200, 400), 255)
    ImageDraw.Draw(hoja).text((40, 40), "Solicito copia", fill=0, font=_fuente(48))
    assert np.array_equal(np.asarray(_sin_cuadricula(hoja)), np.asarray(hoja))


def test_sin_numpy_la_hoja_se_lee_como_esta(monkeypatch):
    import builtins

    real = builtins.__import__

    def sin_numpy(nombre, *a, **k):
        if nombre == "numpy":
            raise ImportError("sin numpy")
        return real(nombre, *a, **k)

    monkeypatch.setattr(builtins, "__import__", sin_numpy)
    hoja = _planilla()
    assert _sin_cuadricula(hoja) is hoja


def test_una_hoja_que_se_corta_a_300_dpi_se_relee_a_200(monkeypatch):
    """Una lectura peor es mejor que una hoja que la IA nunca ve."""
    llamadas = []

    def lectura(imagen, segundos, preparar=True):
        llamadas.append(imagen.width)
        if len(llamadas) == 1:
            return "", 0.0, "el OCR de una pagina paso de 26s y se corto"
        return "CC 1062965207 TRABAJADOR", 0.8, ""

    monkeypatch.setattr(conversion, "_texto_de_imagen", lectura)
    monkeypatch.setattr(conversion, "_motor_de_ocr_que_falta", lambda: "")
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    pagina = doc.new_page(width=612, height=792)
    lectura_final = conversion._ocr_de_pagina(pagina)
    texto, motivo = lectura_final
    assert texto.startswith("CC 1062965207") and motivo == ""
    assert len(llamadas) == 2 and llamadas[1] < llamadas[0]


def test_la_planilla_se_lee_con_sus_cedulas():
    pytesseract = pytest.importorskip("pytesseract")
    try:
        pytesseract.get_tesseract_version()
        if "spa" not in set(pytesseract.get_languages(config="")):
            pytest.skip("tesseract sin el idioma spa")
    except Exception:
        pytest.skip("sin binario de tesseract en este entorno")
    texto, _conf, fallo = conversion._texto_de_imagen(
        _planilla(), 25, preparar=False)
    assert not fallo
    digitos = "".join(c for c in texto if c.isdigit() or c == "\n")
    leidas = [c for c in CEDULAS if c in digitos.replace("\n", " ")
              or c in "".join(ch for ch in texto if ch.isdigit())]
    assert len(leidas) >= 3


# ── Respaldo con el modelo para lo que tesseract no lee ──

def _hoja_vacia():
    return Image.new("RGB", (400, 300), "white")


def test_lo_que_tesseract_no_lee_lo_transcribe_el_modelo(monkeypatch):
    monkeypatch.setattr(conversion, "_texto_de_imagen_tesseract",
                        lambda *a, **k: ("", 0.0, ""))
    llamadas = []
    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA",
                        lambda jpeg: llamadas.append(jpeg) or
                        "ORDEN DE SERVICIOS Identificacion: CC 80.239.271")
    presupuesto = conversion.PresupuestoDeOcr()
    antes = presupuesto.imagenes_ia
    texto, confianza, fallo = conversion._texto_de_imagen(
        _hoja_vacia(), 10, presupuesto=presupuesto)
    assert texto.startswith(conversion.MARCA_TRANSCRITO_POR_IA)
    assert "80.239.271" in texto and confianza is None and fallo == ""
    assert presupuesto.imagenes_ia == antes - 1 and llamadas[0][:2] == b"\xff\xd8"


def test_sin_presupuesto_o_sin_modelo_no_se_llama(monkeypatch):
    monkeypatch.setattr(conversion, "_texto_de_imagen_tesseract",
                        lambda *a, **k: ("", 0.0, ""))
    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA",
                        lambda jpeg: pytest.fail("no debia llamarse"))
    presupuesto = conversion.PresupuestoDeOcr()
    presupuesto.imagenes_ia = 0
    assert conversion._texto_de_imagen(_hoja_vacia(), 10, presupuesto=presupuesto)[0] == ""
    assert conversion._texto_de_imagen(_hoja_vacia(), 10)[0] == ""
    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA", None)
    presupuesto.imagenes_ia = 5
    assert conversion._texto_de_imagen(_hoja_vacia(), 10, presupuesto=presupuesto)[0] == ""


def test_una_buena_lectura_de_tesseract_no_gasta_modelo(monkeypatch):
    bueno = "Identificacion: CC 80.239.271 " * 5
    monkeypatch.setattr(conversion, "_texto_de_imagen_tesseract",
                        lambda *a, **k: (bueno, 0.92, ""))
    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA",
                        lambda jpeg: pytest.fail("no debia llamarse"))
    texto, confianza, _ = conversion._texto_de_imagen(
        _hoja_vacia(), 10, presupuesto=conversion.PresupuestoDeOcr())
    assert texto == bueno and confianza == 0.92


def test_si_el_modelo_dice_sin_texto_queda_lo_de_tesseract(monkeypatch):
    monkeypatch.setattr(conversion, "_texto_de_imagen_tesseract",
                        lambda *a, **k: ("DER", 0.3, ""))
    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA", lambda jpeg: "SIN_TEXTO")
    texto, _, _ = conversion._texto_de_imagen(
        _hoja_vacia(), 10, presupuesto=conversion.PresupuestoDeOcr())
    assert texto == "DER"
