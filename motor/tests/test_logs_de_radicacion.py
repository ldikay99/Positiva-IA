"""El log dice que campos se enviaron a SGDEA y por que un correo no se radico."""
import json

import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline.extraccion import Hallazgo
from pipeline.radicador import Radicador, campos_para_el_log

CUERPO = "Tengo una historia clinica reservada que no debe quedar en el registro."


@pytest.fixture(autouse=True)
def json_forzado(monkeypatch):
    monkeypatch.setenv("FORMATO_LOG", "json")


def _email(cuerpo=CUERPO):
    return EmailMessage(
        id="AAMkAD-correo-de-prueba=", subject="Solicitud",
        body=EmailBody(content=cuerpo),
        from_address=EmailAddress(name="Ana Lopez", address="ana@empresa.com"),
        conversation_id="hilo-1",
        internet_message_id="<mensaje-1@empresa.com>",
        received_datetime="2026-11-04T15:00:00Z",
    )


def _assert_ubica_el_correo(entrada):
    assert entrada["internet_message_id"] == "<mensaje-1@empresa.com>"
    assert entrada["remitente_correo"] == "ana@empresa.com"
    assert entrada["asunto"] == "Solicitud"
    assert entrada["recibido"] == "2026-11-04T15:00:00Z"


def _h(campo, valor):
    return Hallazgo(campo=campo, valor=valor, evidencia="del correo", confianza=0.9, metodo="prueba")


def _hallazgos():
    return {
        "numero_documento_afectado": _h("numero_documento_afectado", "41234567"),
        "nombre_afectado": _h("nombre_afectado", "Ana Lopez Rojas"),
        "nombre_remitente": _h("nombre_remitente", "Ana Lopez Rojas"),
        "tipo_remitente": _h("tipo_remitente", 3),
        "tipo_solicitud": _h("tipo_solicitud", 14),
    }


def _entradas(capsys, texto):
    return [e for e in (json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip())
            if texto in e["message"]]


class Respuesta:
    id = 1
    id_radicado = "ENT20260001"


class Servicio:
    def __init__(self, fallo=None):
        self.fallo = fallo

    async def radicar_pqrd(self, payload, archivos):
        if self.fallo:
            raise self.fallo
        return Respuesta()


@pytest.mark.asyncio
async def test_al_enviar_se_registran_los_campos(capsys):
    r = await Radicador(servicio=Servicio()).radicar(_email(), _hallazgos(), [], CUERPO)
    assert r == "ENT20260001"
    entrada = _entradas(capsys, "radicacion enviada")[0]
    assert entrada["campos_enviados"]["datosAfectadoNumeroDocumento"] == "41234567"
    assert entrada["campos_enviados"]["datosAfectadoNombre"]
    assert entrada["correo"] == "-de-prueba"
    assert entrada["adjuntos"] == ["correo.html"]
    _assert_ubica_el_correo(entrada)


@pytest.mark.asyncio
async def test_se_ve_que_se_envio_vacio_y_que_por_defecto(capsys):
    await Radicador(servicio=Servicio()).radicar(_email(), _hallazgos(), [], CUERPO)
    entrada = _entradas(capsys, "radicacion enviada")[0]
    assert "datosAfectadoDireccion" in entrada["campos_vacios"]
    assert any("departamento" in s for s in entrada["por_defecto"])


@pytest.mark.asyncio
async def test_el_cuerpo_del_correo_no_va_al_registro(capsys):
    await Radicador(servicio=Servicio()).radicar(_email(), _hallazgos(), [], CUERPO)
    salida = capsys.readouterr().out
    assert "historia clinica reservada" not in salida
    assert "caracteres)" in salida


@pytest.mark.asyncio
async def test_si_sgdea_rechaza_se_registran_los_campos_y_el_fallo(capsys):
    servicio = Servicio(fallo=RuntimeError("Radicacion fallida: 400 - campo invalido"))
    r = await Radicador(servicio=servicio).radicar(_email(), _hallazgos(), [], CUERPO)
    assert r == ""
    entrada = _entradas(capsys, "radicacion rechazada")[0]
    assert "campo invalido" in entrada["fallo"]
    assert entrada["campos_enviados"]["datosAfectadoNumeroDocumento"] == "41234567"
    assert entrada["severity"] == "WARNING"
    _assert_ubica_el_correo(entrada)


@pytest.mark.asyncio
async def test_un_fallo_enorme_se_recorta(capsys):
    servicio = Servicio(fallo=RuntimeError("x" * 90000))
    await Radicador(servicio=servicio).radicar(_email(), _hallazgos(), [], CUERPO)
    assert len(_entradas(capsys, "radicacion rechazada")[0]["fallo"]) == 500


def test_registrar_no_cambia_lo_que_se_envia():
    from pipeline.radicador import armar_payload

    payload = armar_payload(_email(), _hallazgos(), CUERPO)
    antes = payload.to_form_data()["solicitudObservaciones"]
    campos_para_el_log(payload)
    assert CUERPO in antes
    assert payload.to_form_data()["solicitudObservaciones"] == antes


@pytest.mark.asyncio
async def test_si_la_compuerta_lo_frena_se_registra_que_falto(tmp_path, capsys):
    from pipeline.modelo import ModeloFalso, Turno
    from pipeline.orquestador import Orquestador

    orq = Orquestador(modelo=ModeloFalso(turnos=[Turno(texto="LISTO")]), base_workspace=str(tmp_path))
    r = await orq.procesar(_email("Solicito informacion sobre mi caso, sin mas datos."))
    assert r.accion == "manual"
    entrada = _entradas(capsys, "no se radica")[0]
    assert entrada["motivo_manual"] == r.motivo_manual
    assert "numero_documento_afectado" in entrada["campos_faltantes"]
    assert any("FALTA_CAMPO_OBLIGATORIO" in x for x in entrada["rechazos"])
    assert "campos_leidos" in entrada


def test_si_el_registro_lo_contradice_se_registra_la_razon(capsys):
    from pipeline.orquestador import Orquestador

    Orquestador._registrar_lo_no_radicado(
        _email(), "DOCUMENTO_DE_OTRA_PERSONA", _hallazgos(), ["el registro lo tiene a otro nombre"])
    entrada = _entradas(capsys, "no se radica")[0]
    assert entrada["rechazos"] == ["el registro lo tiene a otro nombre"]
    assert entrada["campos_leidos"]["nombre_afectado"] == "Ana Lopez Rojas"
    assert entrada["campos_faltantes"] == []
    _assert_ubica_el_correo(entrada)
