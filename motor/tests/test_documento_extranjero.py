"""
Extraccion de documentos que no son una cedula colombiana.

Cubre el pasaporte, que es alfanumerico, el PEP de quince digitos, la cedula
de extranjeria y el permiso por proteccion temporal.
"""
import pytest

from models.enums import TipoDocumento
from pipeline.extraccion import extraer_documento, extraer_tipo_documento
from utils.documento_candidatos import extraer_candidatos_documento


def _numeros(texto):
    return [c["numero"] for c in extraer_candidatos_documento(texto)]


# ── Pasaporte ──

@pytest.mark.parametrize("texto,esperado", [
    ("Adjunto pasaporte AV1234567 del afiliado", "AV1234567"),
    ("Pasaporte No. C01234567", "C01234567"),
    ("pasaporte: 123456789", "123456789"),
])
def test_el_pasaporte_alfanumerico_se_reconoce(texto, esperado):
    assert esperado in _numeros(texto)


def test_el_pep_de_quince_digitos_no_se_descarta():
    """15 digitos: fuera del rango de una cedula, pero es un documento valido."""
    assert "123456789012345" in _numeros("PEP 123456789012345 del solicitante")


def test_una_cedula_antigua_corta_no_se_descarta():
    """Las cedulas mas antiguas tienen menos de seis digitos."""
    assert "23456" in _numeros("El trabajador con C.C. 23456")


def test_la_cedula_de_extranjeria_se_reconoce():
    assert "123456" in _numeros("Cedula de extranjeria No. 123456")


# ── El rango ancho solo aplica cuando el texto dice que documento es ──

def test_una_cifra_corta_sin_marca_no_entra():
    """
    Sin rotulo, aceptar cuatro digitos convertiria cualquier cifra del correo
    en candidato. La ventana estrecha es lo que hace utilizable la lista.
    """
    assert "2345" not in _numeros("Radicamos 2345 solicitudes este mes")


# ── El tipo que se guarda en SGDEA ──

@pytest.mark.parametrize("texto,tipo", [
    ("Pasaporte AV1234567", TipoDocumento.PA),
    ("Cedula de extranjeria 123456", TipoDocumento.CE),
    ("Tarjeta de identidad 1012345678", TipoDocumento.TI),
    ("NIT 900123456-7", TipoDocumento.NI),
])
def test_el_tipo_acompana_al_numero_elegido(texto, tipo):
    h_doc, _ = extraer_documento(texto)
    assert h_doc is not None, texto
    h_tipo, _ = extraer_tipo_documento(texto, numero=str(h_doc.valor))
    assert h_tipo is not None
    assert h_tipo.valor == tipo


def test_un_pasaporte_no_se_reporta_como_cedula():
    """Guardar un pasaporte con tipo CC deja el registro inservible."""
    h_doc, _ = extraer_documento("Afiliado con pasaporte AV1234567")
    h_tipo, _ = extraer_tipo_documento(
        "Afiliado con pasaporte AV1234567", numero=str(h_doc.valor))
    assert h_tipo.valor != TipoDocumento.CC
