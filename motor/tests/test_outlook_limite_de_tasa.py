"""
Reintento del limite de tasa de Graph al leer y escribir las categorias.

Es la peticion con la que se reclama el correo. Perderla deja el correo sin
dueño y permite que dos corridas lo trabajen.
"""
import pytest

from services.outlook import (
    ESPERA_MAXIMA_POR_LIMITE_S,
    ESPERA_POR_DEFECTO_S,
    espera_que_pide_graph,
)


class RespuestaFalsa:
    def __init__(self, retry_after=None):
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}


@pytest.mark.parametrize("cabecera,esperado", [
    ("7", 7.0),
    ("2.5", 2.5),
])
def test_se_respeta_lo_que_pide_graph(cabecera, esperado):
    assert espera_que_pide_graph(RespuestaFalsa(cabecera)) == esperado


@pytest.mark.parametrize("cabecera", [None, "", "  ", "en un rato", "0", "-5"])
def test_sin_una_cabecera_util_se_usa_la_espera_por_defecto(cabecera):
    """Graph no siempre la manda, y una espera de cero seria un bucle."""
    assert espera_que_pide_graph(RespuestaFalsa(cabecera)) == ESPERA_POR_DEFECTO_S


def test_la_espera_se_acota():
    """Graph puede pedir minutos y la corrida no los tiene."""
    assert (espera_que_pide_graph(RespuestaFalsa("600"))
            == ESPERA_MAXIMA_POR_LIMITE_S)


# ── La marca de reclamacion se escribe siempre ──

class _Respuesta:
    def __init__(self, status_code=200, datos=None):
        self.status_code = status_code
        self.headers = {}
        self.text = ""
        self._datos = datos or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._datos


class _GraphFalso:
    """
    Servidor con ETag. `choca` simula que otra corrida escribio entre la
    lectura y la escritura, que es la carrera contra la que existe `If-Match`.
    """

    def __init__(self, categorias=(), choca=False):
        self.categorias = list(categorias)
        self.etag = 'W/"v1"'
        self.choca = choca
        self.patches = 0

    async def get(self, url, **kw):
        return _Respuesta(200, {"categories": list(self.categorias),
                                "@odata.etag": self.etag})

    async def patch(self, url, headers=None, json=None, **kw):
        self.patches += 1
        if self.choca:
            return _Respuesta(412)
        self.categorias = list((json or {}).get("categories") or [])
        return _Respuesta(200)


def _servicio(cliente):
    from services.outlook import OutlookService

    svc = OutlookService()
    svc._client = cliente
    svc._access_token = "token-de-prueba"
    svc._token_expires_at = 9e18
    return svc


@pytest.fixture
def sin_modo_test(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "MODO_TEST", False)


@pytest.mark.asyncio
async def test_el_reclamo_se_escribe_aunque_la_marca_ya_este_puesta(sin_modo_test):
    """
    Lo que reclama el correo no es el texto de la marca sino el PATCH con
    `If-Match`. Dos corridas que arrancan a la vez calculan las dos
    `EN-PROCESO:1`: si la escritura se omite por coincidir con lo que ya hay,
    ninguna recibe el 412 y las dos se creen dueñas del correo.
    """
    cliente = _GraphFalso(["EN-PROCESO:1"])

    await _servicio(cliente).marcar("id-1", "EN-PROCESO:1")

    assert cliente.patches == 1


@pytest.mark.asyncio
async def test_el_reclamo_que_choca_suelta_el_correo(sin_modo_test):
    """El 412 es lo que le dice a la segunda corrida que el correo tiene dueño."""
    from pipeline.marcas import OtraCorridaLoTomo

    cliente = _GraphFalso(["EN-PROCESO:1"], choca=True)

    with pytest.raises(OtraCorridaLoTomo):
        await _servicio(cliente).marcar("id-1", "EN-PROCESO:1")


@pytest.mark.asyncio
async def test_una_marca_de_desenlace_que_no_cambia_nada_no_se_escribe(sin_modo_test):
    """
    El atajo se conserva donde sirve: una marca de cierre que ya esta puesta no
    reclama nada, y repetir la peticion solo gasta una llamada a Graph.
    """
    cliente = _GraphFalso(["REVISION-MANUAL:SIN_DOCUMENTO"])

    await _servicio(cliente).marcar("id-1", "REVISION-MANUAL:SIN_DOCUMENTO")

    assert cliente.patches == 0


# ── Las consultas de deduplicacion tambien esperan el 429 ──

class _GraphQueLimita:
    """
    Contesta 429 las primeras `limites` veces y despues responde.

    `Retry-After: 0` para que la prueba no espere: el valor se acota abajo por
    la espera por defecto, asi que se sustituye el reloj.
    """

    def __init__(self, limites, datos):
        self.limites = limites
        self.datos = datos
        self.consultas = 0

    async def get(self, url, headers=None, params=None, **kw):
        self.consultas += 1
        if self.limites > 0:
            self.limites -= 1
            r = _Respuesta(429)
            r.headers = {"Retry-After": "1"}
            return r
        return _Respuesta(200, self.datos)


@pytest.fixture
def sin_esperas(monkeypatch):
    """El reintento del limite de tasa no tiene que costar segundos de prueba."""
    import asyncio

    async def _ya(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _ya)


@pytest.fixture
def dedup_encendida(monkeypatch):
    import services.outlook as modulo

    monkeypatch.setattr(modulo, "dedup_desactivada", lambda: False)


@pytest.mark.asyncio
async def test_la_busqueda_por_asunto_espera_el_limite_de_tasa(
    sin_modo_test, sin_esperas, dedup_encendida
):
    """
    Es la capa que reconoce un correo ya radicado cuando un area interna lo
    reenvia y Outlook abre otro hilo. Rendirse ante el primer 429 la apaga, y
    apagada el correo se radica por segunda vez. Graph limita `$search` antes
    que nada, y el limite llega justo cuando el lote es grande.
    """
    cliente = _GraphQueLimita(2, {"value": [
        {"id": "1", "subject": "Solicitud de certificado",
         "categories": ["ENT20260001712120"]},
    ]})
    svc = _servicio(cliente)

    async def es_el_mismo_caso(_email, _radicado):
        return True

    svc._es_el_mismo_caso = es_el_mismo_caso

    encontrado = await svc.buscar_etiqueta_ent_por_asunto(
        "Solicitud de certificado", None)

    assert encontrado == "ENT20260001712120"
    assert svc.ultimo_error == ""
    assert cliente.consultas == 3


@pytest.mark.asyncio
async def test_la_busqueda_por_hilo_espera_el_limite_de_tasa(
    sin_modo_test, sin_esperas, dedup_encendida
):
    cliente = _GraphQueLimita(1, {"value": [
        {"id": "1", "categories": ["ENT20260001712121"]},
    ]})

    encontrado = await _servicio(cliente).hilo_ya_radicado("hilo-1")

    assert encontrado == "ENT20260001712121"


@pytest.mark.asyncio
async def test_un_limite_que_no_cede_queda_registrado(
    sin_modo_test, sin_esperas, dedup_encendida
):
    """
    Agotados los reintentos, la capa se declara caida. Devolver None sin mas
    diria "no hay duplicado", que es lo que nunca se comprobo.
    """
    cliente = _GraphQueLimita(99, {"value": []})
    svc = _servicio(cliente)

    assert await svc.buscar_etiqueta_ent_por_asunto("Solicitud de algo", None) is None
    assert "429" in svc.ultimo_error
