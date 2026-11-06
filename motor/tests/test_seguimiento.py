"""
Un correo del hilo de un radicado que pide algo nuevo no se archiva como duplicado.

Casos reales del 5 y 6 de noviembre: la segunda y la tercera entrega de un
medicamento, y la relacion de incapacidades de otro mes, pedidas en el hilo del
radicado anterior.
"""
import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline.duplicados import Veredicto
from pipeline.modelo import ModeloFalso, Turno
from pipeline.seguimiento import SOLICITUD_NUEVA, clasificar, leer

ANTES = ("Señores autorizaciones, envio la formula de los medicamentos alternativos "
         "para tres meses, autorizar para que se me sean entregados.")
AHORA = ("Señores, autorizacion envio para segunda entrega de medicamentos "
         "alternativos. Quedo atenta.")


def test_la_solicitud_nueva_se_acepta_solo_con_la_cita_del_correo():
    c = leer('{"veredicto": "SOLICITUD_NUEVA", "cita": "envio para segunda entrega de medicamentos"}', AHORA)
    assert c.es_solicitud_nueva and "segunda entrega" in c.cita


def test_una_cita_que_no_esta_en_el_correo_no_vale():
    c = leer('{"veredicto": "SOLICITUD_NUEVA", "cita": "solicito la tercera entrega del mes"}', AHORA)
    assert not c.es_solicitud_nueva and "no esta" in c.descartada


def test_sin_cita_o_con_respuesta_rara_queda_como_seguimiento():
    assert not leer('{"veredicto": "SOLICITUD_NUEVA", "cita": ""}', AHORA).es_solicitud_nueva
    assert not leer("no se", AHORA).es_solicitud_nueva
    assert not leer('{"veredicto": "QUIZAS"}', AHORA).es_solicitud_nueva
    assert not leer('{"veredicto": "SEGUIMIENTO", "cita": ""}', AHORA).es_solicitud_nueva


def test_si_el_modelo_falla_queda_como_seguimiento():
    class Roto:
        nombre = "roto"

        def generar(self, *a):
            raise RuntimeError("Vertex no responde")

    c = clasificar(Roto(), "A", ANTES, "B", AHORA)
    assert not c.es_solicitud_nueva and "fallo" in c.descartada


def test_el_texto_de_los_correos_va_como_dato_y_no_como_instruccion():
    m = ModeloFalso(turnos=[Turno(texto='{"veredicto": "SEGUIMIENTO", "cita": ""}')])
    clasificar(m, "A", ANTES, "B", AHORA)
    enviado = m.recibidos[0]
    assert AHORA not in enviado["sistema"] and AHORA in enviado["mensajes"][0]["contenido"]
    assert enviado["herramientas"] == []


# ── En el orquestador ──

def _email(de="adriana@gmail.com", texto=AHORA):
    return EmailMessage(id="m2", subject="Fwd: Historia clinica", body=EmailBody(content=texto),
                        from_address=EmailAddress(name="Adriana", address=de),
                        conversation_id="hilo-1")


class Outlook:
    def __init__(self, de="adriana@gmail.com", texto=ANTES):
        self.radicado = {"from": {"emailAddress": {"address": de}},
                         "subject": "Historia clinica", "body": {"content": texto}}
        self.movidos, self.avisos = [], []

    async def correo_con_etiqueta(self, etiqueta):
        return self.radicado

    async def marcar(self, id_correo, marca="", quitar_de_proceso=False):
        from pipeline.marcas import fusionar

        return fusionar([], marca, quitar_de_proceso)

    async def move_email(self, id_correo, carpeta):
        self.movidos.append(carpeta)
        return ""

    async def forward_email(self, id_correo, destino, cuerpo):
        self.avisos.append(cuerpo)


class Detector:
    async def comprobar(self, email):
        return Veredicto(True, "ENT20260001", "otro correo del mismo hilo ya fue radicado")


def _orquestador(tmp_path, outlook, veredicto):
    from pipeline.orquestador import Orquestador

    m = ModeloFalso(turnos=[Turno(texto=veredicto)])
    orq = Orquestador(modelo=m, outlook=outlook, radicador=None, base_workspace=str(tmp_path))
    orq.duplicados = Detector()
    return orq, m


NUEVA = '{"veredicto": "SOLICITUD_NUEVA", "cita": "autorizacion envio para segunda entrega de medicamentos"}'


@pytest.mark.asyncio
async def test_lo_que_pide_algo_nuevo_va_a_revision_manual_con_el_radicado(tmp_path):
    from config import settings

    outlook = Outlook()
    orq, m = _orquestador(tmp_path, outlook, NUEVA)
    r = await orq.procesar(_email())
    assert (r.accion, r.motivo_manual) == ("manual", "POSIBLE_SOLICITUD_NUEVA"), r.avisos
    assert r.anexo_de == "ENT20260001" and not r.radicado
    assert outlook.movidos == [settings.OUTLOOK_FOLDER_REVISAR_MANUAL]
    assert any("segunda entrega" in a for a in r.avisos)


@pytest.mark.asyncio
async def test_el_seguimiento_sigue_siendo_duplicado(tmp_path):
    from config import settings

    outlook = Outlook()
    orq, m = _orquestador(tmp_path, outlook, '{"veredicto": "SEGUIMIENTO", "cita": ""}')
    r = await orq.procesar(_email())
    assert r.accion == "duplicado" and r.radicado == "ENT20260001"
    assert outlook.movidos == [settings.OUTLOOK_FOLDER_DUPLICADOS]


@pytest.mark.asyncio
async def test_no_se_consulta_al_modelo_por_un_reenvio_exacto_ni_por_otro_remitente(tmp_path):
    for outlook, email in ((Outlook(texto=AHORA), _email()),
                           (Outlook(de="otra@gmail.com"), _email()),
                           (Outlook(), _email(de="area@positiva.gov.co"))):
        orq, m = _orquestador(tmp_path, outlook, NUEVA)
        r = await orq.procesar(email)
        assert r.accion == "duplicado" and m.veces_llamado == 0


def test_lo_citado_debajo_no_cuenta_como_lo_que_pide_el_correo():
    """La frase del correo viejo citado no es un pedido nuevo de quien reitera."""
    from pipeline.seguimiento import texto_sin_citas

    correo = ("Cordial saludo, reitero la peticion toda vez que han pasado 19 dias habiles. "
              "Johana R. El mié, 16 sep. 2026 a la(s) 3:35 p.m., Joanna <a@b.com> escribió: "
              "cordial saludo, solicito copia de la poliza de la referencia")
    propio = texto_sin_citas(correo)
    assert "reitero la peticion" in propio and "copia de la poliza" not in propio
    c = leer('{"veredicto": "SOLICITUD_NUEVA", "cita": "solicito copia de la poliza de la referencia"}', propio)
    assert not c.es_solicitud_nueva


def test_el_aviso_de_outlook_y_el_reenvio_no_son_texto_propio():
    from pipeline.seguimiento import texto_sin_citas

    assert texto_sin_citas(
        "No suele recibir correo electrónico de x.y@mail.com. Por qué es esto importante<https://aka.ms/x> "
        "Señores: solicito revisar el cobro.\n\nDe: Ana <a@b.co>\nAsunto: viejo") == "Señores: solicito revisar el cobro."
    assert texto_sin_citas("envío para segunda entrega. ---------- Forwarded message --------- De: x") == "envío para segunda entrega."
