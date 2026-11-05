"""`extraer_tipo_solicitud`: reconoce el vocabulario del catalogo o se abstiene."""
import pytest

from pipeline.extraccion import (
    _TIPO_SOLICITUD_DECLARADO,
    TOPE_PARA_LA_SOLICITUD,
    extraer_tipo_solicitud,
)


def _valor(asunto="", cuerpo=""):
    h, _ = extraer_tipo_solicitud(asunto, cuerpo)
    return h.valor if h else None


# ── Cada marca del catalogo ──

@pytest.mark.parametrize("texto,codigo", [
    ("Solicito el estado de cuenta de la empresa", 8),
    ("solicito el estado de cartera actualizado", 8),
    ("La presente para solicitar devolucion de aportes a otra ARL", 26),
    ("solicito la devolución de los aportes de mayo", 26),
    ("PAGO DE INCAPACIDAD del trabajador", 16),
    ("solicito el reconocimiento de la incapacidad temporal", 16),
    ("presento recurso contra el dictamen de calificacion", 7),
    ("radique formalmente mi controversia", 7),
    ("quisiera saber como va mi calificacion de PCL", 7),
    ("el tramite ante la Junta Regional de Calificación", 7),
    ("por perdida de capacidad laboral", 7),
    ("solicito el reembolso de los gastos de transporte", 15),
    ("Solicitud de actualizacion de direccion del centro de trabajo", 1),
    ("necesito corregir la razón social de la empresa", 1),
    ("solicito la correccion de la informacion registrada", 1),
    ("solicitamos la autorizacion del procedimiento quirurgico", 5),
    ("solicito autorización de cita con el especialista", 5),
    ("que me autoricen la entrega del medicamento", 21),
    ("sobre el pago de mi pensión de invalidez", 17),
    ("reclamación de póliza de vida", 18),
    ("solicitud de revision del retiro de los trabajadores", 12),
])
def test_reconoce_el_tramite_nombrado(texto, codigo):
    assert _valor(cuerpo=texto) == codigo, texto


def test_tambien_lo_lee_del_asunto():
    assert _valor(asunto="SOLICITUD DE ESTADO DE CUENTA", cuerpo="Buen dia.") == 8


# ── El orden entre marcas ──

def test_la_devolucion_de_aportes_le_gana_a_la_mora():
    """
    Las dos marcas van al mismo codigo, pero el orden importa igual: la
    especifica se declara antes para que la evidencia cite lo que decide.
    """
    h, _ = extraer_tipo_solicitud("", "devolucion de aportes por mora en aportes")
    assert h.valor == 26
    assert "devolucion de aportes" in h.evidencia.lower()


def test_el_dictamen_le_gana_a_la_autorizacion_medica():
    """
    Un correo que discute un dictamen y de paso pide autorizar algo es una
    controversia: lo que define el tramite es el dictamen en disputa.
    """
    assert _valor(cuerpo=(
        "presento recurso contra el dictamen y solicito autorizacion de cita"
    )) == 7


# ── Cuando no reconoce, calla ──

def test_sin_marca_no_resuelve_y_dice_por_que():
    h, nr = extraer_tipo_solicitud("Historia clinica", "Adjunto los documentos.")
    assert h is None
    assert nr.motivo == "REQUIERE_CLASIFICACION_SEMANTICA"
    assert "no nombra ningun tramite" in nr.detalle


def test_un_correo_vacio_no_revienta():
    h, nr = extraer_tipo_solicitud("", "")
    assert h is None and nr is not None


def test_no_mira_mas_alla_del_arranque_del_correo():
    """
    La peticion va al principio. Detras vienen la firma, el aviso legal y la
    cadena de reenvio, donde el vocabulario de otro tramite es ruido.
    """
    cuerpo = "Buen dia.\n" + ("relleno " * 400) + "\nestado de cuenta"
    assert len(cuerpo) > TOPE_PARA_LA_SOLICITUD
    assert _valor(cuerpo=cuerpo) is None


# ── La evidencia ──

def test_la_evidencia_cita_el_original_con_sus_acentos():
    h, _ = extraer_tipo_solicitud("", "solicito la devolución de aportes de mayo")
    assert "devolución" in h.evidencia, h.evidencia


def test_el_metodo_no_es_de_los_que_suponen():
    """
    Este hallazgo se lee del texto, no se aplica por ausencia de otro. Si
    entrara en METODOS_POR_DEFECTO, las medidas lo contarian como suposicion.
    """
    from pipeline.extraccion import METODOS_POR_DEFECTO

    h, _ = extraer_tipo_solicitud("", "solicito el estado de cuenta")
    assert h.metodo not in METODOS_POR_DEFECTO


def test_la_confianza_pasa_la_compuerta():
    from pipeline.compuerta import CONFIANZA_PARA_RADICAR

    h, _ = extraer_tipo_solicitud("", "solicito el estado de cuenta")
    assert h.confianza >= CONFIANZA_PARA_RADICAR


# ── Estructural: ningun codigo sin oficina ──

def test_todo_codigo_marcado_existe_y_tiene_oficina():
    """
    Un codigo sin oficina bloquea la radicacion: resolverlo aqui seria peor que
    escalarlo, porque la compuerta lo rechaza mas adelante sin poder explicarlo.
    """
    from business_rules.office_mapping import OFFICE_MAPPING, get_office_id

    for codigo, patron in _TIPO_SOLICITUD_DECLARADO:
        assert codigo in OFFICE_MAPPING, f"el codigo {codigo} no esta en la tabla de PRD"
        assert get_office_id(codigo) is not None, f"el codigo {codigo} no tiene oficina"


# ── El asunto se escribe telegrafico ──

@pytest.mark.parametrize("texto,codigo", [
    ("Devolucion aportes ARL Positiva a ARL Colmena", 26),
    ("PAGO INCAPACIDAD DE TRABAJADOR", 16),
])
def test_reconoce_el_tramite_sin_la_preposicion(texto, codigo):
    """En un asunto se omite el 'de': 'Devolucion aportes', 'PAGO INCAPACIDAD'."""
    assert _valor(asunto=texto) == codigo
