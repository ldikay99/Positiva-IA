"""
Lo que el OCR deja de una direccion que no pudo leer no se envia.

Con las pistas en la tarea, el agente encontraba el rotulo de la direccion en
un formulario escaneado y reportaba la lectura tal como salio
("cale 6 A N.&! -3.."); la compuerta la dejaba pasar porque trae "barrio".
"""
import pytest

from pipeline.compuerta import _revisar_direccion


def _revisar(direccion):
    reparos, correcciones = [], {}
    _revisar_direccion(direccion, reparos, correcciones)
    return reparos, correcciones


def test_una_lectura_ilegible_no_es_una_direccion():
    reparos, correcciones = _revisar("cale 6 A N.&! -3.. BARRIO. LO.. san.")
    assert correcciones == {"direccion_afectado": ""}
    assert "ilegible" in reparos[0].detalle
    assert not reparos[0].bloqueante


@pytest.mark.parametrize("direccion", [
    "CL 165A NORTE #8 H - 60 APTO 203",
    "VEREDA TAMBIRAL CALLEJON LAS PALMAS - ANDALUCIA",
    "CARRERA 7 £ 24 - 09 BARRIO SIN BARRIO",   # un "#" mal leido no basta
    "Carrera 7B N° 24-09",
    "KM 3 VIA AL MAR; FINCA LA ESPERANZA",
    "Calle 10 # 5-20 (segundo piso)",
])
def test_una_direccion_legible_se_conserva(direccion):
    assert _revisar(direccion) == ([], {})
