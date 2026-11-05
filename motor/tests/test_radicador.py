"""Armado del payload y politica de reintentos al radicar."""
import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline.errores import Bitacora
from pipeline.extraccion import Hallazgo
from pipeline.radicador import (
    CANAL_EMAIL_IA,
    NOMBRE_DEL_CORREO,
    Radicador,
    armar_payload,
    documento_del_correo,
)

CUERPO_CLOUDFLARE = "<title>Attention Required! | Cloudflare</title>"


def _email():
    return EmailMessage(
        id="correo-1",
        subject="Solicitud de incapacidad",
        body=EmailBody(content="cuerpo del correo"),
        from_address=EmailAddress(name="Ana Lopez", address="ana@empresa.com"),
        received_datetime="2026-08-24T13:30:00Z",
    )


def _hallazgos(**extra):
    base = {
        "nombre_afectado": Hallazgo("nombre_afectado", "MUÑOZ PEÑA JOSÉ", "e", 0.9),
        "nombre_remitente": Hallazgo("nombre_remitente", "Ana Lopez", "e", 0.9),
        "numero_documento_afectado": Hallazgo(
            "numero_documento_afectado", "41234567", "e", 0.9),
        "tipo_documento_afectado": Hallazgo("tipo_documento_afectado", 1, "e", 0.9),
        "tipo_solicitud": Hallazgo("tipo_solicitud", 16, "e", 0.9),
        "tipo_remitente": Hallazgo("tipo_remitente", 2, "e", 0.9),
        "oficina": Hallazgo("oficina", 2, "e", 1.0),
    }
    base.update(extra)
    return base


# ── Payload ──

def test_el_payload_lleva_los_datos_extraidos():
    d = armar_payload(_email(), _hallazgos()).to_form_data()
    assert d["datosAfectadoNumeroDocumento"] == "41234567"
    assert d["datosRemitenteNombre"] == "Ana Lopez"
    assert d["solicitudTipoSolicitudId"] == 16
    assert d["oficina"] == 2


def test_el_payload_conserva_la_enie_y_las_tildes():
    """El dato debe llegar a SGDEA tal como se escribe."""
    d = armar_payload(_email(), _hallazgos()).to_form_data()
    assert d["datosAfectadoNombre"] == "MUÑOZ PEÑA JOSÉ"
    assert "Ñ" in d["datosAfectadoNombre"]
    assert d["datosAfectadoNombre"].encode("utf-8").decode("utf-8") == "MUÑOZ PEÑA JOSÉ"


def test_el_canal_identifica_a_la_ia():
    assert armar_payload(_email(), _hallazgos()).to_form_data()[
        "canalRadicacion"] == CANAL_EMAIL_IA


def test_las_observaciones_llevan_asunto_y_fecha():
    obs = armar_payload(_email(), _hallazgos(), "texto del cuerpo").to_form_data()[
        "solicitudObservaciones"]
    assert "Solicitud de incapacidad" in obs
    assert "24/08/2026" in obs
    assert "texto del cuerpo" in obs


def test_un_tercero_no_hereda_el_documento_del_afectado():
    """
    Heredarlo registra a la empresa con la cedula del trabajador: el
    radicador no lo rellena por su cuenta, y lo que rellena queda anotado.
    """
    suposiciones = []
    d = armar_payload(_email(), _hallazgos(),      # tipo_remitente = 2
                      suposiciones=suposiciones).to_form_data()
    assert d["datosRemitenteNumeroDocumento"] == ""
    assert any("NO se hereda del afectado" in s for s in suposiciones), suposiciones


def test_si_el_remitente_es_el_afectado_si_se_hereda():
    """Ahi son la misma persona: el mismo documento es el dato correcto."""
    h = _hallazgos(tipo_remitente=Hallazgo("tipo_remitente", 3, "e", 0.9))
    suposiciones = []
    d = armar_payload(_email(), h, suposiciones=suposiciones).to_form_data()
    assert d["datosRemitenteNumeroDocumento"] == "41234567"
    assert any("misma persona" in s for s in suposiciones), suposiciones


def test_el_documento_propio_del_remitente_manda():
    h = _hallazgos(numero_documento_remitente=Hallazgo(
        "numero_documento_remitente", "9001234567", "e", 0.9))
    d = armar_payload(_email(), h).to_form_data()
    assert d["datosRemitenteNumeroDocumento"] == "9001234567"


def test_con_nombres_distintos_el_defecto_no_dice_que_es_el_afectado():
    """
    Este campo se rellena DESPUES de la compuerta, asi que ella no puede ver el
    valor por defecto. Enviar "Afectado" con dos nombres distintos afirma una
    identidad que el correo desmiente, y ademas es lo que la compuerta rechaza
    con TIPO_3_CON_NOMBRES_DISTINTOS cuando el valor si le llega.
    """
    h = _hallazgos()   # el afectado y el remitente tienen nombres distintos
    del h["tipo_remitente"]
    d = armar_payload(_email(), h).to_form_data()
    assert d["datosRemitenteTipoRemitenteId"] == 2


def test_ni_con_el_mismo_nombre_el_defecto_afirma_que_es_el_afectado():
    """
    Que los dos nombres coincidan no prueba identidad, y un "Afectado" por
    defecto autoriza a copiarle el documento del afectado al remitente. Cuando
    el correo SI muestra que quien escribe es la persona del caso, el campo se
    resuelve antes y el defecto no llega a aplicarse.
    """
    h = _hallazgos(
        nombre_remitente=Hallazgo("nombre_remitente", "MUÑOZ PEÑA JOSÉ", "e", 0.9))
    del h["tipo_remitente"]
    d = armar_payload(_email(), h).to_form_data()
    assert d["datosRemitenteTipoRemitenteId"] == 2


def test_con_documentos_distintos_el_defecto_es_tercero():
    h = _hallazgos(
        nombre_remitente=Hallazgo("nombre_remitente", "MUÑOZ PEÑA JOSÉ", "e", 0.9),
        numero_documento_remitente=Hallazgo(
            "numero_documento_remitente", "76530545", "e", 0.9))
    del h["tipo_remitente"]
    d = armar_payload(_email(), h).to_form_data()
    assert d["datosRemitenteTipoRemitenteId"] == 2


def test_el_defecto_nunca_arrastra_el_documento_del_afectado():
    """
    Es la consecuencia que hace preferible "Tercero": con el defecto puesto,
    `_documento_del_remitente` no hereda, y el remitente no queda registrado con
    la cedula del trabajador.
    """
    h = _hallazgos()
    del h["tipo_remitente"]
    suposiciones = []
    d = armar_payload(_email(), h, suposiciones=suposiciones).to_form_data()
    assert d["datosRemitenteNumeroDocumento"] == ""
    assert any("NO se hereda del afectado" in s for s in suposiciones), suposiciones


def test_el_valor_por_defecto_del_tipo_de_remitente_queda_anotado():
    """Sin la anotacion, un tipo inventado se ve igual que uno leido."""
    h = _hallazgos()
    del h["tipo_remitente"]
    suposiciones = []
    armar_payload(_email(), h, suposiciones=suposiciones)
    assert any("tipo_remitente" in s for s in suposiciones), suposiciones


def test_sin_documento_del_remitente_su_tipo_no_hereda_el_del_afectado():
    """
    Con el afectado en NIT y sin documento del remitente, el remitente no
    puede heredar tipo NIT con numero vacio: eso no describe ningun
    documento, y pone el de la empresa del caso en una persona.
    """
    h = _hallazgos(
        tipo_documento_afectado=Hallazgo("tipo_documento_afectado", 10, "e", 0.9))
    d = armar_payload(_email(), h).to_form_data()
    assert d["datosRemitenteNumeroDocumento"] == ""
    assert d["datosRemitenteTipoDocumentoId"] == 1


def test_el_tipo_del_remitente_sin_documento_queda_anotado():
    h = _hallazgos()
    suposiciones = []
    armar_payload(_email(), h, suposiciones=suposiciones)
    assert any("tipo_documento_remitente" in s for s in suposiciones), suposiciones


def test_con_documento_del_remitente_se_respeta_su_tipo():
    h = _hallazgos(
        numero_documento_remitente=Hallazgo(
            "numero_documento_remitente", "76530545", "e", 0.9),
        tipo_documento_remitente=Hallazgo(
            "tipo_documento_remitente", 2, "e", 0.9))
    d = armar_payload(_email(), h).to_form_data()
    assert d["datosRemitenteNumeroDocumento"] == "76530545"
    assert d["datosRemitenteTipoDocumentoId"] == 2


def test_una_fecha_invalida_no_revienta():
    e = _email()
    e.received_datetime = "no es una fecha"
    assert "no es una fecha" in armar_payload(e, _hallazgos()).to_form_data()[
        "solicitudObservaciones"]


# ── Reintentos ──

class ServicioFalso:
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.intentos = 0

    async def radicar_pqrd(self, payload, archivos):
        self.intentos += 1
        r = self.respuestas.pop(0) if self.respuestas else None
        if isinstance(r, Exception):
            raise r
        return r


class Respuesta:
    def __init__(self, id_radicado):
        self.id_radicado = id_radicado


async def _nada(_s):
    return None


@pytest.mark.asyncio
async def test_radica_a_la_primera():
    s = ServicioFalso([Respuesta("ENT20260001")])
    r = Radicador(servicio=s, dormir=_nada)
    assert await r.radicar(_email(), _hallazgos()) == "ENT20260001"
    assert s.intentos == 1


@pytest.mark.asyncio
async def test_el_captcha_de_cloudflare_se_reintenta():
    """Rendirse al primer 403 pierde una radicacion que el reintento resuelve."""
    s = ServicioFalso([
        Exception(f"Radicacion fallida: 403 - {CUERPO_CLOUDFLARE}"),
        Respuesta("ENT20260002"),
    ])
    b = Bitacora()
    r = Radicador(servicio=s, bitacora=b, dormir=_nada)
    assert await r.radicar(_email(), _hallazgos()) == "ENT20260002"
    assert s.intentos == 2
    assert any(f.clase.codigo == "CLOUDFLARE_CHALLENGE" for f in b.fallos)


@pytest.mark.asyncio
async def test_un_403_de_autorizacion_no_se_reintenta():
    s = ServicioFalso([Exception("403 forbidden: invalid scope")])
    r = Radicador(servicio=s, dormir=_nada)
    assert await r.radicar(_email(), _hallazgos()) == ""
    assert s.intentos == 1


@pytest.mark.asyncio
async def test_se_rinde_al_agotar_los_intentos():
    s = ServicioFalso([
        Exception(f"403 - {CUERPO_CLOUDFLARE}") for _ in range(10)
    ])
    r = Radicador(servicio=s, dormir=_nada)
    assert await r.radicar(_email(), _hallazgos()) == ""
    assert s.intentos <= 5, "debe respetar el maximo de la clase"


@pytest.mark.asyncio
async def test_cada_intento_queda_en_la_bitacora():
    s = ServicioFalso([
        Exception(f"403 - {CUERPO_CLOUDFLARE}"),
        Exception(f"403 - {CUERPO_CLOUDFLARE}"),
        Respuesta("ENT20260003"),
    ])
    b = Bitacora()
    r = Radicador(servicio=s, bitacora=b, dormir=_nada)
    await r.radicar(_email(), _hallazgos())
    assert len(b.fallos) == 2
    assert [f.intento for f in b.fallos] == [1, 2]


# ── La traza: el radicado por si solo no dice de donde salio ──

async def _no_esperar(_segundos):
    """El reintento no puede costar segundos reales en una prueba."""


class ServicioQueRegistra:
    """Doble del servicio de SGDEA: apunta lo que se le pide, sin red."""

    def __init__(self, id_radicado="ENT20260001", falla_la_traza=False):
        self.id_radicado = id_radicado
        self.falla_la_traza = falla_la_traza
        self.radicaciones = []
        self.trazas = []

    async def radicar_pqrd(self, payload, archivos):
        self.radicaciones.append(payload)

        class R:
            id = 4321
            id_radicado = self.id_radicado
        return R()

    async def crear_trazabilidad(self, payload):
        if self.falla_la_traza:
            raise RuntimeError("503 Service Unavailable")
        self.trazas.append(payload)


def _hallazgos_completos():
    return _hallazgos(oficina=Hallazgo("oficina", 15, "e", 1.0))


@pytest.mark.asyncio
async def test_al_radicar_se_deja_la_traza():
    servicio = ServicioQueRegistra()
    r = Radicador(servicio=servicio)
    radicado = await r.radicar(_email(), _hallazgos_completos())

    assert radicado == "ENT20260001"
    assert len(servicio.trazas) == 1
    traza = servicio.trazas[0]
    assert traza.secuencia == radicado
    assert radicado in traza.accion


@pytest.mark.asyncio
async def test_la_traza_dice_a_que_oficina_entro():
    """Auditar una PQRD sin esto obliga a mirar los logs del job."""
    from business_rules import get_office_name

    servicio = ServicioQueRegistra()
    await Radicador(servicio=servicio).radicar(_email(), _hallazgos_completos())

    assert get_office_name(15) in servicio.trazas[0].nombre


@pytest.mark.asyncio
async def test_si_no_se_radica_no_hay_traza():
    class ServicioQueFalla(ServicioQueRegistra):
        async def radicar_pqrd(self, payload, archivos):
            raise RuntimeError("400 Bad Request")

    servicio = ServicioQueFalla()
    radicado = await Radicador(servicio=servicio, dormir=_no_esperar).radicar(
        _email(), _hallazgos_completos())

    assert radicado == ""
    assert not servicio.trazas


@pytest.mark.asyncio
async def test_un_fallo_de_la_traza_no_tumba_la_radicacion():
    """La radicacion ya ocurrio y no se puede deshacer; el fallo consta."""
    from pipeline.errores import Bitacora

    bitacora = Bitacora()
    servicio = ServicioQueRegistra(falla_la_traza=True)
    radicado = await Radicador(servicio=servicio, bitacora=bitacora).radicar(
        _email(), _hallazgos_completos())

    assert radicado == "ENT20260001"
    assert any("TRAZABILIDAD" in a for a in bitacora.avisos()), bitacora.avisos()


@pytest.mark.asyncio
async def test_un_servicio_sin_trazabilidad_no_revienta():
    class ServicioViejo:
        async def radicar_pqrd(self, payload, archivos):
            class R:
                id_radicado = "ENT20260001"
            return R()

    assert await Radicador(servicio=ServicioViejo()).radicar(
        _email(), _hallazgos_completos()) == "ENT20260001"


# ── Toda radicacion lleva al menos un documento ──

class ServicioQueApuntaArchivos:
    """Doble del servicio de SGDEA: guarda los nombres que se le enviaron."""

    def __init__(self):
        self.envios = []

    async def radicar_pqrd(self, payload, archivos):
        self.envios.append([nombre for nombre, _ in archivos])

        class R:
            id = 1
            id_radicado = "ENT20260001"
        return R()

    async def crear_trazabilidad(self, payload):
        pass


@pytest.mark.asyncio
async def test_un_correo_sin_adjuntos_se_radica_con_su_propio_documento():
    """
    El endpoint solo acepta multipart, y sin ningun archivo la peticion sale
    como formulario y el backend la rechaza. Ademas, una PQRD sin soportes
    quedaria radicada sin nada que leer.
    """
    servicio = ServicioQueApuntaArchivos()
    radicado = await Radicador(servicio=servicio).radicar(
        _email(), _hallazgos_completos(), [], "cuerpo del correo")

    assert radicado == "ENT20260001"
    assert servicio.envios[0] == [NOMBRE_DEL_CORREO]


@pytest.mark.asyncio
async def test_el_documento_del_correo_acompana_a_los_adjuntos_propios():
    servicio = ServicioQueApuntaArchivos()
    await Radicador(servicio=servicio).radicar(
        _email(), _hallazgos_completos(), [("soporte.pdf", b"%PDF-1.4")], "cuerpo")

    assert servicio.envios[0] == ["soporte.pdf", NOMBRE_DEL_CORREO]


@pytest.mark.asyncio
async def test_no_se_duplica_si_ya_venia_el_documento_del_correo():
    servicio = ServicioQueApuntaArchivos()
    await Radicador(servicio=servicio).radicar(
        _email(), _hallazgos_completos(), [(NOMBRE_DEL_CORREO, b"<html>")], "cuerpo")

    assert servicio.envios[0].count(NOMBRE_DEL_CORREO) == 1


def test_el_documento_del_correo_no_deja_pasar_marcado_del_cuerpo():
    """El asunto y el cuerpo son texto ajeno: van escapados, no como HTML."""
    documento = documento_del_correo(
        EmailMessage(subject="<script>alert(1)</script>",
                     body=EmailBody(content="Torres & Cia <b>urgente</b>")),
        "Torres & Cia <b>urgente</b>",
    )

    assert b"<script>" not in documento
    assert b"&lt;script&gt;" in documento
    assert b"Torres &amp; Cia" in documento


# ── Radicar no es idempotente: no se repite lo que pudo haberse creado ──

@pytest.mark.asyncio
async def test_un_500_no_se_repite_aunque_su_clase_sea_reintentable():
    """
    Un 5xx describe lo que le paso al servidor despues de recibir la peticion:
    no dice si alcanzo a crear el radicado. Repetirla puede dejar dos para el
    mismo correo, y el correo se aplaza en su lugar.
    """
    s = ServicioFalso([
        Exception("Radicacion fallida: 500 - Internal Server Error")
        for _ in range(5)
    ])
    r = Radicador(servicio=s, dormir=_nada)

    assert await r.radicar(_email(), _hallazgos()) == ""
    assert s.intentos == 1


@pytest.mark.asyncio
async def test_un_tiempo_agotado_tampoco_se_repite():
    """Es el caso con mas probabilidad de que el servidor si haya radicado."""
    s = ServicioFalso([Exception("timeout esperando a SGDEA") for _ in range(5)])
    r = Radicador(servicio=s, dormir=_nada)

    assert await r.radicar(_email(), _hallazgos()) == ""
    assert s.intentos == 1


@pytest.mark.asyncio
async def test_lo_que_no_llego_al_servidor_si_se_repite():
    """Cloudflare corta en el borde: la aplicacion nunca vio la peticion."""
    s = ServicioFalso([
        Exception(f"Radicacion fallida: 403 - {CUERPO_CLOUDFLARE}"),
        Respuesta("ENT20260009"),
    ])
    r = Radicador(servicio=s, dormir=_nada)

    assert await r.radicar(_email(), _hallazgos()) == "ENT20260009"
    assert s.intentos == 2


# ── Una respuesta aceptada sin numero de radicado ──

class ServicioQueNoDevuelveNumero:
    """Doble del servicio: acepta la peticion y responde sin el radicado."""

    def __init__(self):
        self.intentos = 0

    async def radicar_pqrd(self, payload, archivos):
        self.intentos += 1

        class R:
            id = 987
            id_radicado = ""
        return R()

    async def crear_trazabilidad(self, payload):
        raise AssertionError("no hay radicado que trazar")


@pytest.mark.asyncio
async def test_una_respuesta_sin_numero_no_pasa_por_exito_ni_se_repite():
    """
    El registro pudo quedar creado. Devolver vacio en silencio manda el correo
    a una persona que lo radicaria otra vez, y repetir la peticion dejaria dos.
    """
    servicio = ServicioQueNoDevuelveNumero()
    bitacora = Bitacora()
    r = Radicador(servicio=servicio, bitacora=bitacora, dormir=_nada)

    assert await r.radicar(_email(), _hallazgos_completos()) == ""
    assert servicio.intentos == 1
    assert bitacora.motivo_manual() == "RADICACION_SIN_CONFIRMAR"


@pytest.mark.asyncio
async def test_se_anota_que_campos_trajo_la_respuesta_sin_numero():
    """
    Sin saber que llego no se puede distinguir que el backend no mandara el
    numero de que lo mandara con otro nombre: son dos problemas en dos sitios
    distintos, y el cuerpo no se puede volcar porque lleva datos personales.
    """
    from models.radicacion import RadicacionResponse

    class ServicioConOtraForma:
        async def radicar_pqrd(self, payload, archivos):
            return RadicacionResponse.from_api_response(
                {"id": 9, "numeroRadicado": "ENT1", "fechaRadicacion": "hoy"})

        async def crear_trazabilidad(self, payload):
            pass

    bitacora = Bitacora()
    r = Radicador(servicio=ServicioConOtraForma(), bitacora=bitacora,
                  dormir=_nada)

    assert await r.radicar(_email(), _hallazgos_completos()) == ""
    detalle = " ".join(f.detalle for f in bitacora.fallos)
    assert "numeroRadicado" in detalle
    assert "fechaRadicacion" in detalle


def test_las_claves_recibidas_no_llevan_los_valores():
    """El cuerpo trae el nombre y el documento de una persona."""
    from models.radicacion import RadicacionResponse

    r = RadicacionResponse.from_api_response(
        {"idRadicado": "ENT1", "afectado": {"nombre": "ANA LOPEZ"}})

    assert r.claves_recibidas == ("afectado", "idRadicado")
    assert "ANA LOPEZ" not in str(r.claves_recibidas)
