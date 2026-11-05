"""Compuerta de regresion de la resolucion determinista."""
import json

import pytest

from harness.caso import cargar_corpus
from harness.regresion import (
    REFERENCIA,
    TOLERANCIA_PP,
    Instantanea,
    cargar,
    comparar,
    guardar,
    informe,
    medir,
)
from tests.test_flujo_completo import RAIZ_CORPUS


def _inst(total, **campos):
    return Instantanea(casos=10, total=total, por_campo=dict(campos))


# ── Comparacion ──

def test_una_caida_mayor_que_la_tolerancia_es_regresion():
    ref = _inst(50, documento=80)
    act = _inst(50, documento=80 - TOLERANCIA_PP - 5)
    regresiones, _ = comparar(act, ref)
    assert any("documento" in r for r in regresiones)


def test_una_variacion_dentro_de_la_tolerancia_no_alarma():
    ref = _inst(50, documento=80)
    act = _inst(50, documento=80 - TOLERANCIA_PP)
    regresiones, mejoras = comparar(act, ref)
    assert regresiones == [] and mejoras == []


def test_un_campo_nuevo_no_cuenta_como_regresion_del_total():
    """Un campo ausente en la referencia queda fuera del total comparable."""
    ref = _inst(80, documento=80, nombre=80)
    act = _inst(58, documento=80, nombre=80, tipo_pqrd=18)
    regresiones, mejoras = comparar(act, ref)
    assert not any("TOTAL" in r for r in regresiones), regresiones
    assert any("tipo_pqrd" in m for m in mejoras)


def test_una_caida_real_del_total_si_alarma():
    """Una caida en los campos comparables si dispara la compuerta."""
    ref = _inst(80, documento=80, nombre=80)
    act = _inst(60, documento=60, nombre=60, tipo_pqrd=90)
    regresiones, _ = comparar(act, ref)
    assert any("TOTAL" in r for r in regresiones), regresiones


def test_sin_campos_en_comun_no_se_compara_el_total():
    ref = _inst(80, documento=80)
    act = _inst(10, otra_cosa=10)
    regresiones, _ = comparar(act, ref)
    assert not any("TOTAL" in r for r in regresiones)


def test_una_subida_se_reporta_como_mejora():
    ref = _inst(50, documento=40)
    act = _inst(50, documento=70)
    _, mejoras = comparar(act, ref)
    assert any("documento" in m for m in mejoras)


def test_un_campo_que_deja_de_evaluarse_es_regresion():
    ref = _inst(50, documento=80, telefono=60)
    act = _inst(50, documento=80)
    regresiones, _ = comparar(act, ref)
    assert any("telefono" in r and "ya no se evalua" in r for r in regresiones)


def test_un_campo_nuevo_es_mejora():
    ref = _inst(50, documento=80)
    act = _inst(50, documento=80, oficina=100)
    _, mejoras = comparar(act, ref)
    assert any("oficina" in m and "nuevo" in m for m in mejoras)


def test_la_caida_del_total_se_reporta_aparte():
    """
    Un cambio puede mejorar un campo y empeorar el conjunto: el total se vigila
    por separado.
    """
    ref = _inst(60, a=60, b=60)
    act = _inst(40, a=80, b=10)
    regresiones, mejoras = comparar(act, ref)
    assert any("TOTAL" in r for r in regresiones)
    assert any("a:" in m for m in mejoras)


# ── Persistencia ──

def test_guardar_y_cargar_conservan_la_instantanea(tmp_path):
    ruta = tmp_path / "ref.json"
    original = _inst(36, documento=42, nombre_remitente=100)
    guardar(original, ruta)
    recuperada = cargar(ruta)
    assert recuperada.total == 36
    assert recuperada.por_campo["nombre_remitente"] == 100


def test_cargar_una_referencia_inexistente_devuelve_none(tmp_path):
    assert cargar(tmp_path / "no-existe.json") is None


def test_el_informe_sin_referencia_lo_dice():
    texto = informe(_inst(36, documento=42), None)
    assert "Sin referencia" in texto


def test_el_informe_muestra_la_comparacion():
    texto = informe(_inst(30, documento=40), _inst(50, documento=80))
    assert "REGRESIONES" in texto
    assert "referencia 80%" in texto


# ── Sobre el corpus real ──

@pytest.fixture(scope="module")
def corpus():
    casos = cargar_corpus(RAIZ_CORPUS)
    if not casos:
        pytest.skip("corpus no recolectado")
    return casos


@pytest.fixture(scope="module")
def instantanea(corpus):
    """La medicion del corpus, una sola vez para todo el modulo."""
    return medir(corpus)


def test_medir_produce_tasas_coherentes(corpus, instantanea):
    assert instantanea.casos == len(corpus)
    assert 0 <= instantanea.total <= 100
    for campo, pct in instantanea.por_campo.items():
        assert 0 <= pct <= 100, f"{campo} fuera de rango: {pct}"


def test_el_nombre_del_remitente_se_resuelve_casi_siempre(instantanea):
    """Es el campo que la cascada determinista debe cubrir por completo."""
    assert instantanea.por_campo.get("nombre_remitente", 0) >= 95


def test_el_tipo_de_solicitud_se_resuelve_solo_cuando_el_texto_lo_nombra(instantanea):
    """
    Son 27 categorias: elegir por la intencion del correo exige leerlo, y eso
    no se resuelve por algoritmo. Lo que si se resuelve es el caso en que el
    remitente NOMBRA el tramite con el vocabulario del catalogo.

    El techo esta para que no vuelva a clasificar por intencion sin que nadie
    lo note: cubrir a la mayoria significaria que dejo de leer marcas y empezo
    a interpretar.
    """
    cubierto = instantanea.por_campo.get("tipo_solicitud", 0)
    assert 20 <= cubierto <= 60, cubierto


def test_el_tipo_de_remitente_no_se_resuelve_por_heuristica(instantanea):
    """
    Se resuelve por identidad comprobada o por la voz del propio texto. Que
    cubriera a la mayoria significaria que paso a clasificar la relacion con el
    afectado, que es lo que no puede hacer.
    """
    assert instantanea.por_campo.get("tipo_remitente", 0) <= 55


def test_no_hay_regresion_contra_la_referencia_guardada(instantanea):
    """Si no hay referencia, se omite: la fija `guardar` cuando se decida."""
    referencia = cargar(REFERENCIA)
    if referencia is None:
        pytest.skip("sin referencia guardada")
    regresiones, _ = comparar(instantanea, referencia)
    assert not regresiones, "\n".join(regresiones)
