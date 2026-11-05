"""Compartir el asunto de un radicado no basta para ser ese mismo caso."""
import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline.duplicados import estan_vinculados, texto_propio, textos_iguales

TEXTO = "Solicito el pago de la incapacidad del mes de marzo, adjunto los soportes del caso."


def _email(cuerpo=TEXTO, de="ana@empresa.com", asunto="Derecho de peticion"):
    return EmailMessage(
        id="actual", subject=asunto, body=EmailBody(content=cuerpo),
        from_address=EmailAddress(name="Ana Lopez", address=de),
        conversation_id="hilo-nuevo",
    )


class Respuesta:
    def __init__(self, datos, estado=200):
        self.datos = datos
        self.status_code = estado

    def json(self):
        return self.datos

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _outlook(monkeypatch, radicados, adjuntos=None, estado_lectura=200):
    """Un servicio real, sin red: solo se sustituyen sus dos lecturas."""
    from config.settings import settings
    from services.outlook import OutlookService

    monkeypatch.setattr(settings, "DEDUP_EN_PRUEBAS", True)
    servicio = OutlookService()
    leidos = []

    async def consultar(url, params, extra=None, timeout=30):
        if params and ("$search" in params or "$filter" in params):
            filtro = params.get("$filter", "")
            return Respuesta({"value": [
                {"id": r["id"], "subject": r.get("asunto", "Derecho de peticion"),
                 "categories": r.get("marcas", [f"ENT2026000{r['id']}"]),
                 "from": {"emailAddress": {"address": r["de"]}}}
                for r in radicados
                if not filtro or f"'{r['de']}'" in filtro
            ]})
        identificador = url.rsplit("/", 1)[-1]
        leidos.append(identificador)
        cuerpo = next(r["cuerpo"] for r in radicados if r["id"] == identificador)
        return Respuesta({"body": {"content": cuerpo}}, estado_lectura)

    async def get_attachments(message_id):
        return (adjuntos or {}).get(message_id, [])

    monkeypatch.setattr(servicio, "_consultar", consultar)
    monkeypatch.setattr(servicio, "get_attachments", get_attachments)
    servicio.leidos = leidos
    return servicio


# ── Del mismo remitente ──

@pytest.mark.asyncio
async def test_el_mismo_correo_enviado_otra_vez_es_duplicado(monkeypatch):
    s = _outlook(monkeypatch, [{"id": "1", "de": "ana@empresa.com", "cuerpo": TEXTO}])
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email()) == "ENT20260001"


@pytest.mark.asyncio
async def test_la_misma_persona_pidiendo_otra_cosa_no_es_duplicado(monkeypatch):
    otro = "Necesito el certificado de afiliacion de mi empresa para una licitacion publica."
    s = _outlook(monkeypatch, [{"id": "1", "de": "ana@empresa.com", "cuerpo": otro}])
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email()) is None


@pytest.mark.asyncio
async def test_un_texto_corto_lo_deciden_los_adjuntos(monkeypatch):
    adjuntos = {"actual": [{"name": "cedula.pdf"}], "1": [{"name": "Cedula.PDF"}]}
    s = _outlook(monkeypatch, [{"id": "1", "de": "ana@empresa.com", "cuerpo": "Adjunto"}], adjuntos)
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email("Adjunto")) == "ENT20260001"


@pytest.mark.asyncio
async def test_un_texto_corto_con_otros_adjuntos_no_es_duplicado(monkeypatch):
    adjuntos = {"actual": [{"name": "cedula.pdf"}], "1": [{"name": "factura.pdf"}]}
    s = _outlook(monkeypatch, [{"id": "1", "de": "ana@empresa.com", "cuerpo": "Adjunto"}], adjuntos)
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email("Adjunto")) is None


@pytest.mark.asyncio
async def test_sin_texto_ni_adjuntos_no_hay_con_que_afirmarlo(monkeypatch):
    s = _outlook(monkeypatch, [{"id": "1", "de": "ana@empresa.com", "cuerpo": ""}])
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email("")) is None


# ── De otro remitente ──

@pytest.mark.asyncio
async def test_otra_persona_con_el_mismo_asunto_no_es_duplicado(monkeypatch):
    s = _outlook(monkeypatch, [{"id": "1", "de": "luis@otra.com", "cuerpo": TEXTO}])
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email()) is None


@pytest.mark.asyncio
async def test_el_reenvio_de_un_area_del_correo_radicado_es_duplicado(monkeypatch):
    reenvio = "Para su tramite.\nDe: Luis Ruiz &lt;luis@otra.com&gt;\nAsunto: Derecho de peticion\n" + TEXTO
    s = _outlook(monkeypatch, [{"id": "1", "de": "luis@otra.com", "cuerpo": TEXTO}])
    correo = _email(reenvio, de="indemnizaciones@positiva.gov.co")
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", correo) == "ENT20260001"


@pytest.mark.asyncio
async def test_el_original_de_un_reenvio_ya_radicado_es_duplicado(monkeypatch):
    reenvio = "Para su tramite.\nDe: Ana Lopez &lt;ana@empresa.com&gt;\nAsunto: Derecho de peticion\n" + TEXTO
    s = _outlook(monkeypatch, [{"id": "1", "de": "indemnizaciones@positiva.gov.co", "cuerpo": reenvio}])
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email()) == "ENT20260001"


@pytest.mark.asyncio
async def test_dos_areas_que_reenvian_al_mismo_ciudadano_son_el_mismo_caso(monkeypatch):
    def reenvio(area):
        return f"De {area}.\nDe: Luis Ruiz &lt;luis@otra.com&gt;\nAsunto: Derecho de peticion\n{TEXTO}"

    s = _outlook(monkeypatch, [{"id": "1", "de": "juridica@positiva.gov.co", "cuerpo": reenvio("juridica")}])
    correo = _email(reenvio("cartera"), de="cartera@positiva.gov.co")
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", correo) == "ENT20260001"


@pytest.mark.asyncio
async def test_dos_areas_con_ciudadanos_distintos_no_son_el_mismo_caso(monkeypatch):
    def reenvio(de):
        return f"Tramitar.\nDe: Persona &lt;{de}&gt;\nAsunto: Derecho de peticion\n{TEXTO}"

    s = _outlook(monkeypatch, [{"id": "1", "de": "juridica@positiva.gov.co", "cuerpo": reenvio("luis@otra.com")}])
    correo = _email(reenvio("marta@tercera.com"), de="cartera@positiva.gov.co")
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", correo) is None


# ── Lo que se revisa ──

@pytest.mark.asyncio
async def test_otro_asunto_y_sin_marca_no_se_leen(monkeypatch):
    s = _outlook(monkeypatch, [
        {"id": "1", "de": "ana@empresa.com", "cuerpo": TEXTO, "asunto": "Otro asunto"},
        {"id": "2", "de": "ana@empresa.com", "cuerpo": TEXTO, "marcas": ["PARA TRAMITE"]},
    ])
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email()) is None
    assert s.leidos == [], "se leyo un correo que no podia ser el radicado"


@pytest.mark.asyncio
async def test_el_radicado_correcto_puede_no_ser_el_primero(monkeypatch):
    s = _outlook(monkeypatch, [
        {"id": "1", "de": "luis@otra.com", "cuerpo": "Otra cosa distinta por completo, de otra persona."},
        {"id": "2", "de": "ana@empresa.com", "cuerpo": TEXTO},
    ])
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email()) == "ENT20260002"


@pytest.mark.asyncio
async def test_muchos_radicados_ajenos_no_tapan_el_del_mismo_remitente(monkeypatch):
    ajenos = [{"id": str(i), "de": f"persona{i}@otra.com", "cuerpo": "Otro texto cualquiera."}
              for i in range(1, 30)]
    s = _outlook(monkeypatch, ajenos + [{"id": "99", "de": "ana@empresa.com", "cuerpo": TEXTO}])
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email()) == "ENT202600099"
    assert s.leidos == ["99"]


@pytest.mark.parametrize("asunto", ["DERECHO DE PETICIÓN", "Derecho_de_peticion", "RV: Derecho de petición."])
@pytest.mark.asyncio
async def test_el_asunto_escrito_de_otra_forma_es_el_mismo(monkeypatch, asunto):
    s = _outlook(monkeypatch, [{"id": "1", "de": "ana@empresa.com", "cuerpo": TEXTO, "asunto": asunto}])
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email()) == "ENT20260001"


@pytest.mark.asyncio
async def test_no_poder_leer_el_radicado_no_pasa_por_limpio(monkeypatch):
    s = _outlook(monkeypatch, [{"id": "1", "de": "ana@empresa.com", "cuerpo": TEXTO}], estado_lectura=503)
    assert await s.buscar_etiqueta_ent_por_asunto("Derecho de peticion", _email()) is None
    assert "503" in s.ultimo_error


# ── Las piezas ──

def test_el_hilo_citado_no_cuenta_como_texto_propio():
    assert texto_propio("Gracias.\nDe: Ana <a@b.co>\nAsunto: x\nOtro texto") == "gracias."


def test_el_texto_igual_se_reconoce_aunque_cambie_el_formato():
    assert textos_iguales(TEXTO, f"  {TEXTO.upper()}\n\n")


def test_un_hilo_que_cita_al_remitente_del_otro_los_vincula():
    assert estan_vinculados("a@positiva.gov.co", "De: L <l@x.co>\n", "l@x.co", "texto")


def test_sin_remitente_no_hay_vinculo():
    assert not estan_vinculados("", "texto", "l@x.co", "texto")


# ── La auditoria historica ──

class TablaFalsa:
    """Registra lo que se le pregunta y responde lo que se le indique."""

    def __init__(self, datos):
        self.datos = datos
        self.filtros = []

    def table(self, _):
        return self

    def select(self, _):
        return self

    def eq(self, *a):
        return self

    def gte(self, campo, valor):
        self.filtros.append((campo, valor))
        return self

    def limit(self, _):
        return self

    def execute(self):
        return type("R", (), {"data": self.datos})()


def _auditoria(monkeypatch, datos):
    from config.settings import settings
    from services.supabase_client import SupabaseService

    monkeypatch.setattr(settings, "DEDUP_EN_PRUEBAS", True)
    s = SupabaseService()
    s._client = TablaFalsa(datos)
    return s


@pytest.mark.asyncio
async def test_solo_cuentan_los_radicados_desde_que_llego_el_correo(monkeypatch):
    s = _auditoria(monkeypatch, [])
    assert not await s.email_exists("a@b.co", "Solicitud", "2026-10-27T15:00:00Z")
    assert s._client.filtros == [("fecha_radicacion", "2026-10-26T15:00:00")]


@pytest.mark.asyncio
async def test_sin_fecha_valida_se_consulta_sin_limite(monkeypatch):
    s = _auditoria(monkeypatch, [{"numero_radicado": "ENT1"}])
    assert await s.email_exists("a@b.co", "Solicitud", "no es una fecha")
    assert s._client.filtros == []


@pytest.mark.asyncio
async def test_el_detector_le_pasa_la_llegada_del_correo():
    from pipeline.duplicados import Detector

    recibidos = []

    class Auditoria:
        async def email_exists(self, correo, asunto, recibido=""):
            recibidos.append(recibido)
            return False

    correo = _email()
    correo.received_datetime = "2026-10-27T15:00:00Z"
    await Detector(auditoria=Auditoria()).comprobar_contenido(correo)
    assert recibidos == ["2026-10-27T15:00:00Z"]


# ── Buzones de Positiva sin nadie de afuera ──

def _orquestador(tmp_path):
    from pipeline.modelo import ModeloFalso
    from pipeline.orquestador import Orquestador

    modelo = ModeloFalso(turnos=[])
    return Orquestador(modelo=modelo, base_workspace=str(tmp_path)), modelo


@pytest.mark.asyncio
async def test_un_buzon_de_positiva_sin_externo_no_se_radica(tmp_path):
    orq, modelo = _orquestador(tmp_path)
    r = await orq.procesar(_email(
        "Peticion de nivelacion salarial para los abogados de sucursales.",
        de="funcionaria@positiva.gov.co", asunto="Nivelacion"))
    assert r.accion == "manual"
    assert r.motivo_manual == "REMITENTE_NO_IDENTIFICABLE"
    assert modelo.veces_llamado == 0


@pytest.mark.asyncio
async def test_si_el_hilo_cita_otra_area_tampoco_hay_externo(tmp_path):
    orq, _ = _orquestador(tmp_path)
    r = await orq.procesar(_email(
        "Para revisar.\nDe: Area &lt;area@positiva.gov.co&gt;\nAsunto: Nivelacion\nTexto.",
        de="funcionaria@positiva.gov.co", asunto="Nivelacion"))
    assert r.motivo_manual == "REMITENTE_NO_IDENTIFICABLE"


@pytest.mark.asyncio
async def test_un_reenvio_con_el_ciudadano_en_el_hilo_sigue_su_camino(tmp_path):
    orq, _ = _orquestador(tmp_path)
    r = await orq.procesar(_email(
        "Para su tramite.\nDe: Luis Ruiz &lt;luis@gmail.com&gt;\nAsunto: Pago\nSolicito el pago.",
        de="indemnizaciones@positiva.gov.co", asunto="RV: Pago"))
    assert r.motivo_manual != "REMITENTE_NO_IDENTIFICABLE"


@pytest.mark.asyncio
async def test_un_buzon_externo_automatizado_no_cae_en_esta_regla(tmp_path):
    orq, _ = _orquestador(tmp_path)
    r = await orq.procesar(_email(
        "Solicito certificado de afiliacion.",
        de="sistemasintegrados@empresa.com", asunto="Certificado"))
    assert r.motivo_manual != "REMITENTE_NO_IDENTIFICABLE"


@pytest.mark.asyncio
async def test_lo_que_va_a_otra_area_se_sigue_reenviando(tmp_path):
    orq, _ = _orquestador(tmp_path)
    r = await orq.procesar(_email(
        "El juzgado ordena a la entidad dar cumplimiento.",
        de="funcionaria@positiva.gov.co", asunto="Notificacion de fallo de tutela"))
    assert r.accion == "reenviado"


# ── El log dice que capa decidio ──

@pytest.mark.asyncio
async def test_el_resultado_dice_que_capa_lo_detecto(tmp_path):
    orq, _ = _orquestador(tmp_path)
    correo = _email()
    correo.categories = ["ENT20260009"]
    r = await orq.procesar(correo)
    assert r.accion == "duplicado"
    assert r.razon_del_duplicado == "el correo ya tiene etiqueta de radicado"
