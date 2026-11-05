"""
Tipo de PQRD: Solicitud, siempre.

Es el unico tipo con el que este canal radica. Los demas —derecho de peticion,
queja, reclamo— fijan terminos de respuesta distintos, y elegirlos aqui le fija
a la entidad un plazo que nadie verifico. Lo que se prueba es que ni el texto
del correo, ni el modelo, ni el radicador puedan moverlo.
"""
import pytest

from models.enums import TipoPqrd
from pipeline.extraccion import (
    METODOS_POR_DEFECTO,
    extraer_tipo_pqrd,
    extraer_todo,
)


def _valor(asunto, cuerpo="", reenvio_interno=False):
    h, _ = extraer_tipo_pqrd(asunto, cuerpo, reenvio_interno)
    return h.valor if h else None


# ── El valor no depende del texto ──

@pytest.mark.parametrize("asunto", [
    "Pago de incapacidad",
    "Fw: Derecho de Petición",
    "DERECHO DE PETICION - traslado",
    "REF: QUEJA CONTRA LA ARL POSITIVA",
    "Presento un reclamo por la demora",
    "Sugerencia sobre el portal",
    "Felicitaciones al equipo",
    "Derecho de peticion y queja contra la ARL",
    "",
])
def test_siempre_es_solicitud(asunto):
    assert _valor(asunto) == int(TipoPqrd.SOLICITUD)


@pytest.mark.parametrize("cuerpo", [
    "Manifiesto mi inconformidad con la atencion recibida",
    "Llevo tres meses sin respuesta a mi caso",
    "De igual manera pongo la queja por la demora en la atencion",
    "Radico queja por incumplimiento a la respuesta",
    "interpongo formalmente un reclamo",
    "En ejercicio del derecho de peticion del articulo 23",
])
def test_lo_que_diga_el_cuerpo_tampoco_lo_mueve(cuerpo):
    assert _valor("Envio de soportes", cuerpo) == int(TipoPqrd.SOLICITUD)


def test_el_campo_nunca_queda_pendiente():
    """Sin ramas que escalen, el modelo no gasta una vuelta en este campo."""
    _, no_resuelto = extraer_tipo_pqrd("Derecho de peticion", "cuerpo")
    assert no_resuelto is None


def test_el_texto_vacio_no_revienta():
    h, _ = extraer_tipo_pqrd("", "")
    assert h.valor == int(TipoPqrd.SOLICITUD)


# ── Como se anota ──

def test_no_cuenta_como_dato_leido_del_correo():
    """
    El valor es una constante del canal. Contarlo como extraccion inflaria la
    cobertura con un campo que nadie leyo.
    """
    h, _ = extraer_tipo_pqrd("Pago de incapacidad", "cuerpo")
    assert h.metodo in METODOS_POR_DEFECTO


# ── Enganche con el agente y con el payload ──

def test_no_se_le_pide_al_agente():
    e = extraer_todo(asunto="Derecho de peticion y queja",
                     cuerpo="Solicito una respuesta.")
    assert "tipo_pqrd" not in e.no_resueltos
    assert "tipo_pqrd" not in e.campos_para_el_agente
    assert e.hallazgos["tipo_pqrd"].valor == int(TipoPqrd.SOLICITUD)


def test_el_criterio_ya_no_viaja_en_el_prompt():
    """Los criterios se reenvian en cada vuelta: el de un campo fijo se paga en todas y no decide nada."""
    from pipeline.agente import prompt_de_sistema

    assert "tipo_pqrd" not in prompt_de_sistema(
        ["nombre_afectado", "tipo_solicitud", "tipo_remitente"])


def test_el_payload_sale_siempre_con_solicitud():
    from models.email import EmailAddress, EmailBody, EmailMessage
    from pipeline.extraccion import Hallazgo
    from pipeline.radicador import TIPO_PQRD_DEL_CANAL, armar_payload

    email = EmailMessage(
        id="c1", subject="QUEJA formal contra la ARL",
        body=EmailBody(content="interpongo queja por la demora"),
        from_address=EmailAddress(name="Ana Lopez", address="ana@empresa.com"),
    )
    suposiciones = []
    payload = armar_payload(
        email,
        {"nombre_afectado": Hallazgo("nombre_afectado", "PEDRO RUIZ", "e", 0.9),
         "tipo_pqrd": Hallazgo("tipo_pqrd", 5, "e", 0.9)},
        suposiciones=suposiciones,
    )
    assert payload.to_form_data()["solicitudTipoPqrdId"] == TIPO_PQRD_DEL_CANAL
    assert not any("tipo_pqrd" in s for s in suposiciones), suposiciones
