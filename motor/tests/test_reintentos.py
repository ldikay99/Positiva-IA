"""
Correos que se aplazan a la siguiente corrida en vez de ir a revision manual.

Un fallo de infraestructura no dice nada del correo. Mandarlo a una cola humana
le cuesta el tiempo a una persona para que descubra que no habia nada que
revisar.
"""
import pytest

from pipeline.reintentos import (
    MAX_CICLOS,
    MOTIVOS_QUE_SE_APLAZAN,
    decidir,
    etiqueta_de_intento,
    intentos_previos,
    se_aplaza,
)


# ── Que se aplaza y que no ──

@pytest.mark.parametrize("motivo", sorted(MOTIVOS_QUE_SE_APLAZAN))
def test_un_fallo_pasajero_se_aplaza(motivo):
    assert se_aplaza(motivo)


@pytest.mark.parametrize("motivo", [
    "ADJUNTO_ILEGIBLE",     # el adjunto es el que esta ilegible
    "ADJUNTO_PROTEGIDO",    # la contraseña la tiene quien lo envio
    "SIN_DOCUMENTO",        # falta un dato del correo
    "SIN_NOMBRE_AFECTADO",
    "VALIDACION_RECHAZO",   # los datos no cuadran entre si
    "VOLUMEN_EXCESIVO",     # el correo trae mas de lo que se tramita solo
    "FACTURA_ELECTRONICA",  # no es una PQRD
])
def test_lo_que_una_persona_si_puede_resolver_le_llega(motivo):
    """
    El criterio no es si el fallo se repara solo, sino si quien abre el correo
    puede hacer algo con el. Un permiso o un motor de OCR tampoco se reparan en
    media hora, y aun asi no son suyos.
    """
    assert not se_aplaza(motivo)


def test_los_motivos_que_se_aplazan_existen_en_el_catalogo():
    """Un motivo mal escrito aqui no daria error: dejaria de aplazar y ya."""
    from pipeline.telemetria import MOTIVOS_MANUAL

    assert MOTIVOS_QUE_SE_APLAZAN <= set(MOTIVOS_MANUAL)


# ── La cuenta vive en la etiqueta del correo ──

def test_sin_etiquetas_no_hay_intentos_previos():
    assert intentos_previos(None) == 0
    assert intentos_previos([]) == 0
    assert intentos_previos(["ENT20260001", "otra cosa"]) == 0


def test_la_etiqueta_lleva_el_numero():
    assert intentos_previos([etiqueta_de_intento(2)]) == 2


def test_con_varias_etiquetas_manda_la_mas_alta():
    """
    Si una corrida etiqueto y no pudo limpiar la anterior, el correo lleva dos
    marcas. Quedarse con la primera daria un limite mas laxo del declarado.
    """
    assert intentos_previos(
        [etiqueta_de_intento(1), "ENT20260001", etiqueta_de_intento(2)]) == 2


def test_una_etiqueta_parecida_no_cuenta():
    assert intentos_previos(["REINTENTOS", "REINTENTO:", "REINTENTO:abc"]) == 0


# ── La decision ──

def test_el_primer_fallo_pasajero_se_aplaza():
    aplaza, intento, explicacion = decidir("MODELO_NO_RESPONDIO", [])
    assert aplaza
    assert intento == 1
    assert "siguiente corrida" in explicacion


def test_al_agotar_los_ciclos_deja_de_aplazarse():
    """Sin tope, un fallo permanente lo dejaria dando vueltas para siempre."""
    categorias = [etiqueta_de_intento(MAX_CICLOS - 1)]
    aplaza, _, explicacion = decidir("MODELO_NO_RESPONDIO", categorias)
    assert not aplaza
    assert "revision manual" in explicacion


def test_el_ciclo_avanza_de_uno_en_uno():
    vistos = []
    categorias = []
    while True:
        aplaza, intento, _ = decidir("MODELO_NO_RESPONDIO", categorias)
        if not aplaza:
            break
        vistos.append(intento)
        categorias = [etiqueta_de_intento(intento)]
    assert vistos == list(range(1, MAX_CICLOS))


def test_lo_que_no_se_aplaza_no_gasta_ciclos():
    aplaza, _, explicacion = decidir("SIN_DOCUMENTO", [])
    assert not aplaza
    assert not explicacion


def test_la_explicacion_dice_en_que_intento_va():
    """Sin eso, quien lee el informe no sabe si es la primera o la ultima vez."""
    _, _, explicacion = decidir("SGDEA_CAIDO", [])
    assert "intento 1" in explicacion
