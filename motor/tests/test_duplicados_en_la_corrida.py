"""
Dos envios del mismo caso en la misma corrida no se radican los dos.

En produccion los 6 pares de radicados identicos del 5 y 6 de noviembre
salieron de la misma corrida: se procesaban a la vez y ninguno veia al otro.
"""
import asyncio

import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline.duplicados import Detector

TEXTO = ("Cordial saludo, respetuosamente adjunto solicitud de verificacion y "
         "determinacion del origen laboral del accidente.")


def _email(id_, asunto="Solicitud de verificacion", de="ana@gmail.com", texto=TEXTO, hilo=None):
    return EmailMessage(id=id_, subject=asunto, body=EmailBody(content=texto),
                        from_address=EmailAddress(name="Ana", address=de),
                        conversation_id=hilo or f"hilo-{id_}")


def _adj(nombre, tamano):
    return {"name": nombre, "size": tamano, "isInline": False}


class Outlook:
    def __init__(self, adjuntos, mismo_caso=True):
        self.adjuntos, self.mismo_caso, self.ultimo_error = adjuntos, mismo_caso, ""

    async def _es_el_mismo_caso(self, email, radicado):
        return self.mismo_caso

    async def get_attachments(self, id_):
        return self.adjuntos.get(id_, [])

    async def hilo_ya_radicado(self, conversacion):
        return None

    async def buscar_etiqueta_ent_por_asunto(self, asunto, email):
        return None


DOCS = [_adj("solicitud.pdf", 120_000), _adj("historia.pdf", 800_000)]


@pytest.mark.asyncio
async def test_el_mismo_asunto_y_el_mismo_caso_es_duplicado_del_radicado_de_la_corrida():
    d = Detector(outlook=Outlook({}))
    d.recordar_del_lote(_email("m1"), "ENT20260001")
    v = await d.comprobar(_email("m2"))
    assert v.es_duplicado and v.radicado == "ENT20260001"


@pytest.mark.asyncio
async def test_el_mismo_asunto_con_otro_caso_no_es_duplicado():
    """El criterio es el de la capa del asunto: otro texto es otra solicitud."""
    d = Detector(outlook=Outlook({}, mismo_caso=False))
    d.recordar_del_lote(_email("m1"), "ENT20260001")
    assert not (await d.comprobar(_email("m2"))).es_duplicado


@pytest.mark.asyncio
async def test_otro_asunto_con_el_mismo_texto_y_los_mismos_documentos_es_duplicado():
    d = Detector(outlook=Outlook({"m1": DOCS, "m2": list(reversed(DOCS))}))
    d.recordar_del_lote(_email("m1", asunto="Solicitud de verificacion del origen laboral del accidente"), "ENT1")
    v = await d.comprobar(_email("m2", asunto="Solicitud de verificacion del origen laboral"))
    assert v.es_duplicado and v.radicado == "ENT1"


@pytest.mark.asyncio
async def test_otro_asunto_con_otro_documento_no_es_duplicado():
    """La misma firma en solicitudes distintas: lo que las separa son los documentos."""
    d = Detector(outlook=Outlook({"m1": DOCS, "m2": [_adj("solicitud.pdf", 120_000), _adj("orden.pdf", 50_000)]}))
    d.recordar_del_lote(_email("m1", asunto="Traslado del 27"), "ENT1")
    assert not (await d.comprobar(_email("m2", asunto="Medicamento Sertralina"))).es_duplicado


@pytest.mark.asyncio
async def test_otro_asunto_sin_documentos_no_es_duplicado():
    d = Detector(outlook=Outlook({}))
    d.recordar_del_lote(_email("m1", asunto="Uno"), "ENT1")
    assert not (await d.comprobar(_email("m2", asunto="Otro"))).es_duplicado


@pytest.mark.asyncio
async def test_otro_remitente_no_se_compara():
    d = Detector(outlook=Outlook({}))
    d.recordar_del_lote(_email("m1"), "ENT1")
    assert not (await d.comprobar(_email("m2", de="luis@gmail.com"))).es_duplicado


@pytest.mark.asyncio
async def test_si_la_comparacion_falla_queda_constancia_y_sigue():
    class Rota(Outlook):
        async def _es_el_mismo_caso(self, email, radicado):
            raise RuntimeError("Graph no responde")

    d = Detector(outlook=Rota({}))
    d.recordar_del_lote(_email("m1"), "ENT1")
    v = await d.comprobar(_email("m2"))
    assert not v.es_duplicado and "esta corrida fallo" in v.motivo


@pytest.mark.asyncio
async def test_los_correos_del_mismo_remitente_se_procesan_de_a_uno(tmp_path):
    from pipeline.orquestador import Orquestador

    orq = Orquestador.__new__(Orquestador)
    activos, maximo = {"ana@gmail.com": 0, "luis@gmail.com": 0}, {"ana@gmail.com": 0, "luis@gmail.com": 0}

    async def _procesar(email):
        de = email.from_address.address.lower()
        activos[de] += 1
        maximo[de] = max(maximo[de], activos[de])
        await asyncio.sleep(0.01)
        activos[de] -= 1
        return de

    orq._procesar = _procesar
    await asyncio.gather(
        orq.procesar(_email("a1")), orq.procesar(_email("a2")), orq.procesar(_email("a3")),
        orq.procesar(_email("l1", de="luis@gmail.com")), orq.procesar(_email("l2", de="Luis@gmail.com")))
    assert maximo == {"ana@gmail.com": 1, "luis@gmail.com": 1}
