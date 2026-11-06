"""
Conversion de adjuntos firmados digitalmente.

`.p7m` es un sobre PKCS#7 que lleva el documento real dentro. El conversor lo
abre y devuelve el contenido, en vez de marcar el adjunto como ilegible.
"""
import base64

import pytest

from pipeline.conversion import (
    MAX_PROFUNDIDAD_SOBRE,
    convertir,
    detectar_tipo,
)

# Cabecera DER de un PKCS#7 real: SEQUENCE, longitud larga, OID signedData.
CABECERA_DER = (
    b"\x30\x82\x0a\x1f\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02"
    + b"\x30\x82" + b"\x00" * 60
)


def _pdf(texto=b"CC 41234567 PEDRO RUIZ MEJIA"):
    """PDF minimo con texto extraible."""
    contenido = b"BT /F1 12 Tf 72 720 Td (" + texto + b") Tj ET"
    objetos = [
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj",
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj",
        b"4 0 obj<</Length " + str(len(contenido)).encode() + b">>stream\n"
        + contenido + b"\nendstream endobj",
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj",
    ]
    cuerpo = b"%PDF-1.4\n" + b"\n".join(objetos)
    return cuerpo + b"\ntrailer<</Root 1 0 R/Size 6>>\n%%EOF"


def _sobre(carga: bytes) -> bytes:
    """Sobre firmado: certificados delante, documento detras, cola de firma."""
    return CABECERA_DER + carga + b"\x00" * 128


# ── Deteccion ──

def test_un_p7m_se_reconoce_por_su_nombre():
    assert detectar_tipo(_sobre(_pdf()), "smime.p7m") == "p7m"


# ── Apertura del sobre ──

def test_el_pdf_de_dentro_del_sobre_se_lee():
    c = convertir(_sobre(_pdf()), "dictamen.pdf.p7m")
    assert not c.error, c.error
    assert "41234567" in c.markdown
    assert c.tipo_real.startswith("p7m/")


def test_queda_dicho_que_venia_en_un_sobre():
    """Sin la procedencia no se puede auditar de donde salio el dato."""
    c = convertir(_sobre(_pdf()), "dictamen.pdf.p7m")
    assert "sobre firmado" in c.aviso


def test_un_sobre_en_base64_tambien_se_abre():
    """Varios llegan como texto base64 en vez de DER."""
    crudo = base64.b64encode(_sobre(_pdf()))
    c = convertir(crudo, "smime.p7m")
    assert not c.error, c.error
    assert "41234567" in c.markdown


def test_el_documento_se_recorta_hasta_el_fin_del_pdf():
    """La cola de firma detras del %%EOF romperia el lector de PDF."""
    c = convertir(_sobre(_pdf()), "adjunto.p7m")
    assert not c.error, c.error


# ── Cuando no se puede, se dice por que ──

def test_un_sobre_sin_documento_lo_declara():
    c = convertir(CABECERA_DER + b"\x11" * 400, "vacio.p7m")
    assert c.error
    assert "no trae ningun documento" in c.error
    assert c.tipo_real == "p7m"


def test_un_sobre_corrupto_no_revienta():
    c = convertir(b"\x30\x82" + b"\xff" * 200, "roto.p7m")
    assert c.error
    assert c.markdown == ""


def test_el_anidamiento_tiene_tope():
    """Un archivo corrupto no puede hacer ciclar la conversion."""
    datos = _sobre(_pdf())
    for _ in range(MAX_PROFUNDIDAD_SOBRE + 2):
        datos = base64.b64encode(datos)
    c = convertir(datos, "anidado.p7m")
    assert c.error


def test_un_archivo_vacio_no_es_un_sobre():
    c = convertir(b"", "smime.p7m")
    assert c.error
    assert c.tipo_real == "vacio"


# ── El OCR no puede colgar el lote ──

def test_el_ocr_tiene_presupuesto_de_tiempo(monkeypatch):
    """
    Un escaneo de sesenta paginas a 200 dpi puede tardar minutos. Sin tope, un
    solo adjunto frena el lote entero y el job parece colgado.
    """
    from pipeline import conversion

    assert conversion.PRESUPUESTO_OCR_SEGUNDOS > 0

    llamadas = []
    monkeypatch.setattr(conversion, "PRESUPUESTO_OCR_SEGUNDOS", 0.0)
    monkeypatch.setattr(
        conversion, "_ocr_de_pagina",
        lambda pagina, *_: (llamadas.append(1), ("texto", ""))[1])

    c = conversion.convertir(_pdf_escaneado(), "escaneo.pdf")
    assert not llamadas, "se aplico OCR con el presupuesto agotado"
    assert "presupuesto de OCR" in c.aviso, c.aviso


def _pdf_escaneado(paginas=3):
    """PDF con paginas sin texto extraible, que es lo que dispara el OCR."""
    objetos = ["1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj"]
    kids = " ".join(f"{i + 3} 0 R" for i in range(paginas))
    objetos.append(
        f"2 0 obj<</Type/Pages/Kids[{kids}]/Count {paginas}>>endobj")
    for i in range(paginas):
        objetos.append(
            f"{i + 3} 0 obj<</Type/Page/Parent 2 0 R"
            f"/MediaBox[0 0 612 792]>>endobj")
    cuerpo = "%PDF-1.4\n" + "\n".join(objetos)
    return (cuerpo + "\ntrailer<</Root 1 0 R/Size 9>>\n%%EOF").encode()


def test_un_pdf_sin_texto_intenta_ocr():
    """Si no lo intentara, la prueba del presupuesto no probaria nada."""
    from pipeline import conversion

    llamadas = []

    def espia(pagina, *_):
        llamadas.append(1)
        return "", "sin motor en el entorno de pruebas"

    original = conversion._ocr_de_pagina
    conversion._ocr_de_pagina = espia
    try:
        conversion.convertir(_pdf_escaneado(), "escaneo.pdf")
    finally:
        conversion._ocr_de_pagina = original
    assert llamadas, "no se intento OCR sobre paginas sin texto"
