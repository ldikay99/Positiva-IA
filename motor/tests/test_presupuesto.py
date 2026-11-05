"""
El presupuesto de tiempo de una corrida.

Sin tope, una corrida lenta se solapa con la siguiente y dos procesos miran la
misma bandeja. Con tope, los correos que no se alcanzan quedan sin tocar y los
recoge la corrida siguiente.
"""
import time

import pytest

from pipeline.presupuesto import MINIMO_POR_CORREO_S, Presupuesto


def _presupuesto(total, gastado=0.0):
    """Un presupuesto con `gastado` segundos ya consumidos."""
    p = Presupuesto(total_s=total, reserva_s=10.0)
    p.inicio = time.monotonic() - gastado
    return p


def test_al_empezar_queda_todo_menos_la_reserva():
    assert _presupuesto(600).queda() == pytest.approx(590.0, abs=0.5)


def test_lo_gastado_se_descuenta():
    assert _presupuesto(600, gastado=100).queda() == pytest.approx(490.0, abs=0.5)


def test_pasado_el_total_no_queda_tiempo_negativo():
    assert _presupuesto(600, gastado=9999).queda() == 0.0


def test_se_agota_antes_de_llegar_a_cero():
    """
    Empezar un correo con menos del minimo lo deja a medias, y un correo a
    medias hay que rescatarlo despues.
    """
    holgado = _presupuesto(600, gastado=100)
    apurado = _presupuesto(600, gastado=600 - 10 - MINIMO_POR_CORREO_S + 1)
    assert not holgado.agotado()
    assert apurado.agotado()


def test_una_espera_que_cabe_no_se_recorta():
    assert _presupuesto(600).recortar(30) == 30  # cabe entero, no se toca


def test_una_espera_que_no_cabe_se_recorta_a_lo_disponible():
    """Esperar hasta agotar el presupuesto deja el correo sin radicar Y sin tiempo."""
    p = _presupuesto(600, gastado=590 - MINIMO_POR_CORREO_S - 5)
    assert p.recortar(60) == pytest.approx(5, abs=0.5)


def test_sin_margen_no_se_espera_nada():
    assert _presupuesto(600, gastado=595).recortar(60) == 0.0


def test_una_espera_de_cero_sigue_siendo_cero():
    assert _presupuesto(600).recortar(0) == 0.0


def test_el_informe_dice_cuanto_se_uso_y_cuanto_queda():
    texto = _presupuesto(600, gastado=120).informe()
    assert "presupuesto" in texto and "queda" in texto
