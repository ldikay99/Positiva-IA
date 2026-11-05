"""
Acentos y ñ en toda la cadena de nombres.

Regla: se normaliza para COMPARAR, se preserva para GUARDAR. Un nombre que
llega a SGDEA sin sus tildes es un dato mal escrito.
"""
import pytest

from pipeline.compuerta import Veredicto, validar
from pipeline.extraccion import (
    Hallazgo,
    extraer_nombre_afectado,
    extraer_nombre_remitente,
)
from utils.generic_filter import (
    _esta_capitalizado_como_nombre,
    es_nombre_persona_valido,
    extraer_nombre_de_firma,
)
from pipeline.extraccion import extraer_sexo

NOMBRES = [
    "José Ramírez Peña",
    "MUÑOZ GARCÍA ANDRÉS",
    "María Ángeles Ibáñez",
    "Nuñez Peñaloza Iván",
    "Begoña Argüello Sáenz",
    "JOSÉ MARÍA NÚÑEZ",
]


@pytest.mark.parametrize("nombre", NOMBRES)
def test_un_nombre_acentuado_es_valido(nombre):
    assert es_nombre_persona_valido(nombre) is True
    assert _esta_capitalizado_como_nombre(nombre) is True


@pytest.mark.parametrize("nombre", NOMBRES)
def test_el_afectado_conserva_sus_tildes(nombre):
    h, _ = extraer_nombre_afectado(f"Historia clinica del paciente {nombre}")
    assert h is not None
    assert h.valor == nombre


@pytest.mark.parametrize("nombre", NOMBRES)
def test_la_firma_conserva_sus_tildes(nombre):
    assert extraer_nombre_de_firma(f"Gracias\nCordialmente,\n{nombre}") == nombre


@pytest.mark.parametrize("nombre", NOMBRES)
def test_el_remitente_conserva_sus_tildes(nombre):
    h, _ = extraer_nombre_remitente(nombre, "x@empresa.com", "cuerpo")
    assert h is not None
    assert h.valor == nombre


@pytest.mark.parametrize("con,sin", [
    ("Sexo: Femenino", "Sexo: femenino"),
    ("Género: Masculino", "Genero: masculino"),
])
def test_el_sexo_declarado_no_depende_del_acento(con, sin):
    a, _ = extraer_sexo(con)
    b, _ = extraer_sexo(sin)
    assert a is not None and b is not None
    assert a.valor == b.valor


def _hallazgos(**extra):
    base = {
        "nombre_afectado": Hallazgo("nombre_afectado", "José Ramírez Peña", "e", 0.9),
        "nombre_remitente": Hallazgo("nombre_remitente", "María Ángeles Ibáñez", "e", 0.9),
        "numero_documento_afectado": Hallazgo(
            "numero_documento_afectado", "41234567", "e", 0.9),
        "tipo_solicitud": Hallazgo("tipo_solicitud", 16, "e", 0.9),
    }
    base.update(extra)
    return base


def test_la_compuerta_aprueba_nombres_acentuados():
    assert validar(_hallazgos()).veredicto is Veredicto.APROBADO


def test_el_mismo_nombre_con_y_sin_acento_es_la_misma_persona():
    """Si no, un remitente que es el afectado se marcaria como incoherente."""
    d = validar(_hallazgos(
        tipo_remitente=Hallazgo("tipo_remitente", 3, "e", 0.9),
        nombre_remitente=Hallazgo("nombre_remitente", "Jose Ramirez Pena", "e", 0.9),
    ))
    assert not any(r.codigo == "TIPO_3_CON_NOMBRES_DISTINTOS" for r in d.reparos)


def test_una_dieresis_no_rompe_la_extraccion():
    h, _ = extraer_nombre_afectado("Historia del paciente Begoña Argüello Sáenz")
    assert h is not None and "ü" in h.valor
