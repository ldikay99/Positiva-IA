"""
Las hojas escaneadas de un PDF se leen a la vez, en orden y con el mismo
presupuesto; las hojas en blanco no pasan por OCR.
"""
import time

import pytest

from pipeline import conversion


# ── OCR en paralelo y hojas en blanco ──

def _pdf_escaneado(paginas):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for _ in range(paginas):
        doc.new_page()
    datos = doc.tobytes()
    doc.close()
    return datos


def test_las_hojas_escaneadas_se_leen_a_la_vez_y_en_orden(monkeypatch):
    monkeypatch.setattr(conversion, "PAGINAS_OCR_EN_PARALELO", 4)

    def lenta(pagina, presupuesto=None):
        numero = pagina.number
        time.sleep(0.3)
        return conversion._LecturaDePagina(f"HOJA {numero + 1} CC 41234567", "", 0.9)

    monkeypatch.setattr(conversion, "_ocr_de_pagina", lenta)
    inicio = time.monotonic()
    c = conversion.convertir(_pdf_escaneado(8), "escaneo.pdf")
    transcurrido = time.monotonic() - inicio
    assert transcurrido < 8 * 0.3 * 0.6, transcurrido
    posiciones = [c.markdown.index(f"HOJA {i}") for i in range(1, 9)]
    assert posiciones == sorted(posiciones)
    assert c.paginas == 8 and c.paginas_ilegibles == 0


def test_el_presupuesto_se_respeta_con_hojas_en_paralelo(monkeypatch):
    monkeypatch.setattr(conversion, "PAGINAS_OCR_EN_PARALELO", 2)

    def lenta(pagina, presupuesto=None):
        time.sleep(0.4)
        return conversion._LecturaDePagina("texto leido CC 41234567", "", 0.9)

    monkeypatch.setattr(conversion, "_ocr_de_pagina", lenta)
    presupuesto = conversion.PresupuestoDeOcr(total_s=0.5)
    c = conversion.convertir(_pdf_escaneado(10), "escaneo.pdf", presupuesto=presupuesto)
    assert c.paginas_ilegibles > 0
    assert "presupuesto" in c.aviso


def test_una_hoja_en_blanco_no_pasa_por_tesseract(monkeypatch):
    from PIL import Image

    monkeypatch.setattr(conversion, "_texto_de_imagen",
                        lambda *a, **k: pytest.fail("se leyo una hoja en blanco"))
    monkeypatch.setattr(conversion, "_motor_de_ocr_que_falta", lambda: "")
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    pagina = doc.new_page()
    texto, motivo = conversion._ocr_de_pagina(pagina)
    assert texto == "" and motivo == conversion.MOTIVO_EN_BLANCO
    assert conversion._en_blanco(Image.new("RGB", (800, 1000), "white"))


def test_una_hoja_con_una_linea_no_es_en_blanco():
    from PIL import Image, ImageDraw

    hoja = Image.new("L", (2480, 3508), 250)
    dibujo = ImageDraw.Draw(hoja)
    # Una sola linea de unos treinta caracteres a 300 dpi.
    dibujo.rectangle((300, 400, 1500, 440), fill=20)
    assert not conversion._en_blanco(hoja)


def test_la_imagen_de_la_hoja_es_la_misma_que_por_png():
    from PIL import Image
    import io

    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_text((72, 72), "CC 41234567 PEDRO RUIZ", fontsize=14)
    rapida = conversion._imagen_de_pagina(pagina, 150)
    por_png = Image.open(io.BytesIO(pagina.get_pixmap(dpi=150).tobytes("png")))
    assert rapida.mode == por_png.mode and rapida.tobytes() == por_png.tobytes()


def test_una_hoja_enorme_se_renderiza_con_tope_de_pixeles():
    """Una pagina del tamaño de la foto que lleva no se lee a 155 megapixeles."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    dpi = conversion._dpi_de_pagina(doc.new_page(width=2550, height=3507))
    assert conversion._dpi_de_pagina(doc.new_page(width=612, height=792)) == 300
    assert 3507 * dpi / 72 <= conversion.LADO_MAXIMO_OCR_PX
    assert dpi < 300


def test_tesseract_recibe_la_hoja_sin_comprimir():
    from PIL import Image

    assert conversion._sin_comprimir(Image.new("L", (10, 10))).format == "PPM"
    assert conversion._sin_comprimir(Image.new("RGB", (10, 10))).format == "PPM"
    assert conversion._sin_comprimir(Image.new("RGBA", (10, 10))).format is None
