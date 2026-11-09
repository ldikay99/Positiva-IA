"""
Lo que el OCR deja de una direccion que no pudo leer no se envia.

Con las pistas en la tarea, el agente encontraba el rotulo de la direccion en
un formulario escaneado y reportaba la lectura tal como salio
("calle 9 B N.&! -7.."); la compuerta la dejaba pasar porque trae "barrio".
"""
import pytest

from pipeline.compuerta import _revisar_direccion


def _revisar(direccion):
    reparos, correcciones = [], {}
    _revisar_direccion(direccion, reparos, correcciones)
    return reparos, correcciones


def test_una_lectura_ilegible_no_es_una_direccion():
    reparos, correcciones = _revisar("calle 9 B N.&! -7.. BARRIO. XO.. sur.")
    assert correcciones == {"direccion_afectado": ""}
    assert "ilegible" in reparos[0].detalle
    assert not reparos[0].bloqueante


@pytest.mark.parametrize("direccion", [
    "CL 100A NORTE #7 H - 10 APTO 301",
    "VEREDA LA ESPERANZA CALLEJON LOS PINOS - MUNICIPIO",
    "CARRERA 7 £ 24 - 09 BARRIO SIN BARRIO",   # un "#" mal leido no basta
    "Carrera 7B N° 24-09",
    "KM 3 VIA AL MAR; FINCA LA ESPERANZA",
    "Calle 10 # 5-20 (segundo piso)",
])
def test_una_direccion_legible_se_conserva(direccion):
    assert _revisar(direccion) == ([], {})
