"""
Un fallo que no se pudo clasificar no condena el correo.

`DESCONOCIDO` es el cajon de lo que ninguna firma reconoce. Cerrar el correo
hacia una persona afirma que el problema esta en el correo, y eso es justo lo
que no se sabe. Mientras el limite de ciclos aguante, el correo vuelve a la
bandeja y la corrida siguiente lo reintenta.
"""
import pytest

from pipeline import reintentos
from pipeline.errores import Bitacora, clasificar


# ── La decision ──

def test_el_fallo_sin_clasificar_se_aplaza():
    assert reintentos.se_aplaza("ERROR_INESPERADO")


@pytest.mark.parametrize("motivo", [
    "SIN_DOCUMENTO",
    "DOCUMENTO_AMBIGUO",
    "VALIDACION_RECHAZO",
    "SIN_NOMBRE_AFECTADO",
    "ADJUNTO_ILEGIBLE",
    "ADJUNTO_PROTEGIDO",
    "VOLUMEN_EXCESIVO",
    "FACTURA_ELECTRONICA",
    "SOLO_EMPRESA_SIN_AFECTADO",
    "DOCUMENTO_DE_OTRA_PERSONA",
])
def test_lo_que_si_dice_algo_del_correo_sigue_yendo_a_una_persona(motivo):
    """Aplazar esto seria dar vueltas: el dato no va a aparecer solo."""
    assert not reintentos.se_aplaza(motivo)


@pytest.mark.parametrize("motivo", [
    "MODELO_NO_RESPONDIO",
    "MODELO_SIN_PERMISO",
    "SGDEA_CAIDO",
    "CLOUDFLARE_403",
    "PETICION_MAL_FORMADA",
    "SIN_OCR",
    "TIEMPO_DE_CORRIDA_AGOTADO",
    "ERROR_INESPERADO",
])
def test_ningun_fallo_del_entorno_le_llega_a_una_persona(motivo):
    """
    El criterio es uno solo: si quien recibe el correo no puede hacer nada con
    el, no se le manda. Un permiso, un motor de OCR o un backend caido no son
    algo que se resuelva mirando el correo.
    """
    assert reintentos.se_aplaza(motivo)


def test_todo_motivo_del_entorno_esta_cubierto():
    """
    Si mañana se agrega un motivo de infraestructura y nadie lo mete en la
    lista, el correo empezaria a cerrarse en falso sin que nada lo avise.
    """
    from pipeline.telemetria import MOTIVOS_AJENOS_AL_CONTENIDO

    sin_aplazar = sorted(MOTIVOS_AJENOS_AL_CONTENIDO
                         - reintentos.MOTIVOS_QUE_SE_APLAZAN)
    # Los dos que quedan fuera tienen su razon, y no es que el correo este mal:
    #   RADICACION_SIN_CONFIRMAR  el registro pudo quedar creado en SGDEA, y
    #                             hay que buscarlo antes de repetir.
    #   RECLAMO_AGOTADO           ya es el corte tras doce reclamos; aplazarlo
    #                             seria un tope dentro de otro tope.
    assert sin_aplazar == ["RADICACION_SIN_CONFIRMAR", "RECLAMO_AGOTADO"], \
        sin_aplazar


def test_el_correo_vuelve_a_la_bandeja_y_no_a_la_cola_humana():
    aplazar, intento, explicacion = reintentos.decidir("ERROR_INESPERADO", [])

    assert aplazar
    assert intento == 1
    assert "siguiente corrida" in explicacion


def test_el_contador_avanza_con_cada_corrida():
    aplazar, intento, _ = reintentos.decidir(
        "ERROR_INESPERADO", ["REINTENTO:3"])

    assert aplazar
    assert intento == 4


def test_al_agotar_los_ciclos_si_va_a_una_persona():
    """
    El limite es lo que impide el bucle: si tras doce corridas sigue fallando,
    el problema ya no parece del entorno.
    """
    ultimo = reintentos.MAX_CICLOS - 1
    aplazar, _, explicacion = reintentos.decidir(
        "ERROR_INESPERADO", [f"REINTENTO:{ultimo}"])

    assert not aplazar
    assert "sigue fallando" in explicacion


def test_la_penultima_corrida_todavia_lo_reintenta():
    """El corte es exacto: un ciclo antes del limite el correo sigue vivo."""
    aplazar, intento, _ = reintentos.decidir(
        "ERROR_INESPERADO", [f"REINTENTO:{reintentos.MAX_CICLOS - 2}"])

    assert aplazar
    assert intento == reintentos.MAX_CICLOS - 1


def test_el_contador_no_depende_de_redis_ni_de_la_auditoria():
    """
    Vive en la etiqueta del correo. Un fallo de infraestructura puede ser
    precisamente que esos servicios esten caidos.
    """
    assert reintentos.intentos_previos(
        ["REINTENTO:2", "EN-PROCESO:1", "otra"]) == 2


def test_dos_marcas_a_la_vez_se_resuelven_por_la_mas_alta():
    """Si una corrida etiqueto y no pudo limpiar la anterior, la buena es la mayor."""
    assert reintentos.intentos_previos(["REINTENTO:1", "REINTENTO:5"]) == 5


# ── La traza que antes se perdia ──

def test_el_fallo_sin_clasificar_guarda_el_tipo_de_excepcion():
    """
    La clase pide "mirar la traza" y la traza no se guardaba en ninguna parte:
    sin el tipo no hay como escribir la firma que falta.
    """
    b = Bitacora()
    fallo = b.registrar("radicacion", "algo se rompio",
                        excepcion=ZeroDivisionError("x"))

    assert fallo.clase.codigo == "DESCONOCIDO"
    assert fallo.excepcion == "ZeroDivisionError"
    assert "ZeroDivisionError" in fallo.linea_log


def test_un_fallo_ya_clasificado_no_repite_el_tipo():
    """La clase ya nombra la causa: repetirlo alarga cada linea sin añadir nada."""
    b = Bitacora()
    fallo = b.registrar("radicacion", "gateway timeout", estado=504)

    assert fallo.clase.codigo == "TIEMPO_AGOTADO"
    assert fallo.excepcion == ""


def test_sin_excepcion_no_se_inventa_un_tipo():
    b = Bitacora()
    fallo = b.registrar("radicacion", "algo raro")

    assert fallo.clase.codigo == "DESCONOCIDO"
    assert fallo.excepcion == ""


# ── El motivo con que sale ──

def test_un_fallo_de_red_sin_firma_conocida_acaba_aplazandose():
    """
    Es el caso que motiva todo esto: una excepcion que ninguna firma reconoce
    no deberia gastar el tiempo de una persona.
    """
    clase = clasificar(excepcion=RuntimeError("algo que nadie ha visto"))

    assert clase.codigo == "DESCONOCIDO"
    assert reintentos.se_aplaza(clase.motivo_manual)
