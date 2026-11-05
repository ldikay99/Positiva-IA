"""
Reclamar un correo antes de trabajarlo.

Las capas de deduplicacion son de lectura y se apoyan en marcas que se escriben
al final. Entre radicar y etiquetar cabe otra corrida entera, y por ahi sale el
mismo caso radicado dos veces.
"""
import pytest

from pipeline.reclamo import (
    MAX_RECLAMOS,
    decidir,
    esta_reclamado,
    marca,
    otro_lo_radico,
    reclamos_previos,
)


# ── La marca ──

def test_un_correo_nuevo_no_esta_reclamado():
    assert not esta_reclamado(None)
    assert not esta_reclamado([])
    assert not esta_reclamado(["ENT20260001", "Rojo"])


def test_la_marca_lleva_el_numero():
    assert reclamos_previos([marca(2)]) == 2
    assert esta_reclamado([marca(1)])


def test_con_varias_marcas_manda_la_mas_alta():
    """Dos marcas significan que una corrida etiqueto y no pudo limpiar."""
    assert reclamos_previos([marca(1), "ENT20260001", marca(3)]) == 3


def test_una_marca_de_reclamo_parecida_no_cuenta():
    assert reclamos_previos(["EN-PROCESO", "EN-PROCESOS:1", "EN-PROCESO:x"]) == 0


# ── La decision ──

def test_un_correo_nuevo_se_trabaja():
    trabajar, intento, explicacion = decidir([])
    assert trabajar
    assert intento == 1
    assert not explicacion


def test_un_correo_reclamado_se_retoma_y_consta():
    """
    Si vuelve con la marca puesta, la corrida anterior no llego al final. Se
    retoma, pero queda dicho: no es lo mismo que un correo nuevo.
    """
    trabajar, intento, explicacion = decidir([marca(1)])
    assert trabajar
    assert intento == 2
    assert "no termino" in explicacion


def test_tras_agotar_los_reclamos_va_a_una_persona():
    """
    Un correo con el que el pipeline se cae siempre no se puede reintentar
    eternamente: es un fallo que alguien tiene que mirar.
    """
    trabajar, _, explicacion = decidir([marca(MAX_RECLAMOS)])
    assert not trabajar
    assert "revision manual" in explicacion


def test_el_reclamo_avanza_de_uno_en_uno():
    vistos, categorias = [], []
    while True:
        trabajar, intento, _ = decidir(categorias)
        if not trabajar:
            break
        vistos.append(intento)
        categorias = [marca(intento)]
    assert vistos == list(range(1, MAX_RECLAMOS + 1))


# ── La ultima comprobacion antes de radicar ──

def test_se_detecta_que_otra_corrida_ya_radico():
    """
    Es la unica ocasion de no escribir dos veces en SGDEA, que es lo unico del
    pipeline que no se puede deshacer.
    """
    assert otro_lo_radico(["ENT20260001602261"]) == "ENT20260001602261"


def test_sin_radicado_de_otro_no_hay_nada_que_detener():
    assert otro_lo_radico([marca(1)]) == ""
    assert otro_lo_radico(None) == ""


def test_el_motivo_de_reclamo_agotado_esta_en_el_catalogo():
    """Un motivo fuera del catalogo sale como 'motivo no catalogado'."""
    from pipeline.telemetria import MOTIVOS_MANUAL

    assert "RECLAMO_AGOTADO" in MOTIVOS_MANUAL
