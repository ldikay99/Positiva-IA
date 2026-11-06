"""
Un correo del hilo de un radicado que trae documentos nuevos se radica como
anexo y no se archiva como duplicado.
"""
import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline.anexos import documentos_nuevos, es_documento, nombre_normalizado
from pipeline.duplicados import Detector, Veredicto


def _adj(nombre, tamano=120_000, inline=False):
    return {"name": nombre, "size": tamano, "isInline": inline}


def _email(de="ana@gmail.com", adjuntos=True):
    return EmailMessage(
        id="m2", subject="RE: Incapacidad", body=EmailBody(content="Adjunto la historia clinica."),
        from_address=EmailAddress(name="Ana", address=de),
        conversation_id="hilo-1", has_attachments=adjuntos)


# ── La comparacion de documentos ──

def test_un_documento_que_el_radicado_no_tiene_es_nuevo():
    assert documentos_nuevos(
        [_adj("historia clinica.pdf", 300_000)], [_adj("cedula.pdf", 90_000)]
    ) == ["historia clinica.pdf"]


def test_el_mismo_documento_reenviado_no_es_nuevo_aunque_cambie_el_nombre():
    # Mismo tamaño exacto: es el mismo archivo con otro nombre.
    assert documentos_nuevos([_adj("scan_001.pdf", 90_123)], [_adj("cedula.pdf", 90_123)]) == []
    # Mismo nombre con marca de copia.
    assert documentos_nuevos([_adj("Cedula (1).pdf", 1)], [_adj("cedula.pdf", 2)]) == []


def test_las_imagenes_de_la_firma_no_son_documentos():
    assert not es_documento(_adj("image001.png", 200_000))
    assert not es_documento(_adj("foto.jpg", 8_000))
    assert not es_documento(_adj("x.pdf", inline=True))
    assert es_documento(_adj("foto incapacidad.jpg", 450_000))
    assert documentos_nuevos([_adj("image002.png", 5_000)], []) == []


def test_el_nombre_se_normaliza():
    assert nombre_normalizado("  Cedula  MAURO [1330].PDF ") == "cedula mauro.pdf"


# ── El detector ──

class OutlookFalso:
    def __init__(self, nuevos=(), falla=False):
        self.nuevos, self.falla, self.preguntas = list(nuevos), falla, 0
        self.ultimo_error = ""

    async def hilo_ya_radicado(self, conversacion):
        return "ENT20260001"

    async def buscar_etiqueta_ent_por_asunto(self, asunto, email):
        return None

    async def documentos_nuevos_frente_al_radicado(self, email, etiqueta):
        self.preguntas += 1
        if self.falla:
            raise RuntimeError("Graph no responde")
        return self.nuevos


@pytest.mark.asyncio
async def test_con_documentos_nuevos_no_es_duplicado_sino_anexo():
    v = await Detector(outlook=OutlookFalso(["historia.pdf"])).comprobar(_email())
    assert not v.es_duplicado
    assert v.anexo_de == "ENT20260001"
    assert v.documentos_nuevos == ["historia.pdf"]


@pytest.mark.asyncio
async def test_sin_documentos_nuevos_sigue_siendo_duplicado():
    v = await Detector(outlook=OutlookFalso([])).comprobar(_email())
    assert v.es_duplicado and v.radicado == "ENT20260001" and not v.anexo_de


@pytest.mark.asyncio
async def test_sin_adjuntos_no_se_compara():
    o = OutlookFalso(["x.pdf"])
    v = await Detector(outlook=o).comprobar(_email(adjuntos=False))
    assert v.es_duplicado and o.preguntas == 0


@pytest.mark.asyncio
async def test_lo_que_escribe_la_entidad_en_el_hilo_no_es_anexo():
    """La respuesta de Positiva lleva su carta adjunta: no es una peticion."""
    o = OutlookFalso(["SAL2026.pdf"])
    v = await Detector(outlook=o).comprobar(_email(de="servicio@positiva.gov.co"))
    assert v.es_duplicado and o.preguntas == 0


@pytest.mark.asyncio
async def test_si_no_se_pueden_comparar_los_documentos_queda_duplicado_y_consta():
    v = await Detector(outlook=OutlookFalso(falla=True)).comprobar(_email())
    assert v.es_duplicado
    assert "sin comparar documentos" in v.motivo


# ── El servicio de Outlook ──

class Respuesta:
    def __init__(self, datos, estado=200):
        self._datos, self.status_code = datos, estado

    def json(self):
        return self._datos

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@pytest.mark.asyncio
async def test_el_servicio_compara_contra_los_radicados_del_hilo(monkeypatch):
    from config.settings import settings
    from services.outlook import OutlookService

    monkeypatch.setattr(settings, "DEDUP_EN_PRUEBAS", True)
    s = OutlookService()
    adjuntos = {
        "m2": [_adj("cedula.pdf", 90_000), _adj("historia.pdf", 300_000)],
        "m1": [_adj("cedula.pdf", 90_000)],
    }

    async def consultar(url, params, extra=None, timeout=30):
        return Respuesta({"value": [
            {"id": "m1", "categories": ["ENT20260001"], "hasAttachments": True},
            {"id": "m2", "categories": [], "hasAttachments": True},
            {"id": "m3", "categories": [], "hasAttachments": True},
        ]})

    async def get_attachments(mid):
        return adjuntos.get(mid, [_adj("otro.pdf", 1)])

    monkeypatch.setattr(s, "_consultar", consultar)
    monkeypatch.setattr(s, "get_attachments", get_attachments)
    assert await s.documentos_nuevos_frente_al_radicado(_email(), "ENT20260001") == ["historia.pdf"]


# ── El orquestador ──

@pytest.mark.asyncio
async def test_el_anexo_va_a_revision_manual_con_el_radicado_y_no_se_radica(tmp_path):
    """
    Radicarlo abriria un segundo numero para el mismo caso, y archivarlo
    perderia los documentos: lo recibe una persona con la referencia.
    """
    from config import settings
    from tests.test_fallos_visibles import OutlookConHilo, _email as email_de_prueba, _modelo
    from pipeline.orquestador import Orquestador

    class Radicador:
        bitacora, suposiciones = None, ()

        async def radicar(self, *a, **k):
            raise AssertionError("un anexo no se radica")

    class DetectorDeAnexo:
        async def comprobar(self, email):
            return Veredicto(anexo_de="ENT20260001", documentos_nuevos=["historia.pdf"])

        async def comprobar_contenido(self, email, nombres):
            raise AssertionError("un anexo no sigue a las capas de contenido")

    class Outlook(OutlookConHilo):
        avisos = []

        async def forward_email(self, id_correo, destino, cuerpo):
            self.avisos.append(cuerpo)

    outlook = Outlook()
    orq = Orquestador(modelo=_modelo(), outlook=outlook,
                      radicador=Radicador(), base_workspace=str(tmp_path))
    orq.duplicados = DetectorDeAnexo()
    original = settings.CORREO_DE_AVISOS
    settings.CORREO_DE_AVISOS = "revision@positiva.gov.co"
    try:
        r = await orq.procesar(email_de_prueba())
    finally:
        settings.CORREO_DE_AVISOS = original

    assert (r.accion, r.motivo_manual) == ("manual", "ANEXO_DE_RADICADO"), r.avisos
    assert r.anexo_de == "ENT20260001" and not r.radicado
    # Solo el correo; el resto del hilo no se arrastra a revision manual.
    assert [c for _, c in outlook.movidos] == [settings.OUTLOOK_FOLDER_REVISAR_MANUAL]
    # Quien lo revisa recibe el numero del radicado y los documentos nuevos.
    assert outlook.avisos and "ENT20260001" in outlook.avisos[0]
    assert "historia.pdf" in outlook.avisos[0]


def test_las_cartas_de_la_entidad_y_los_correos_adjuntos_no_son_documentos_nuevos():
    assert documentos_nuevos([_adj("SAL20260001761621.pdf", 280_205)], []) == []
    assert documentos_nuevos([_adj("2-SAL20260001589747.pdf")], []) == []
    assert documentos_nuevos([_adj("ENT20260001874593.pdf", 26_113)], []) == []
    item = {"@odata.type": "#microsoft.graph.itemAttachment", "name": "RV: certificacion", "size": 1_162_020}
    assert documentos_nuevos([item], []) == []


@pytest.mark.asyncio
async def test_el_resto_del_hilo_deja_en_la_bandeja_lo_que_trae_adjuntos_de_afuera():
    from config import settings
    from pipeline.orquestador import Orquestador

    class Outlook:
        movidos = []

        async def mensajes_del_hilo(self, carpeta, conversacion):
            def m(i, de, adj):
                return {"id": i, "hasAttachments": adj, "from": {"emailAddress": {"address": de}}}
            return [m("a", "ana@gmail.com", False), m("b", "ana@gmail.com", True),
                    m("c", "area@positiva.gov.co", True)]

        async def move_email(self, i, carpeta):
            self.movidos.append(i)

    o = Outlook()
    orq = Orquestador.__new__(Orquestador)
    orq.outlook = o
    avisos = []
    await orq._archivar_el_resto_del_hilo(_email(), avisos)
    assert o.movidos == ["a", "c"]
    assert settings.OUTLOOK_FOLDER_DUPLICADOS
