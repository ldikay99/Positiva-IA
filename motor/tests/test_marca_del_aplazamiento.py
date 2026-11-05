"""
El correo aplazado no se queda sin la cuenta de sus aplazamientos.

La cuenta vive en la etiqueta del propio correo. Si no se escribe, la corrida
siguiente lo ve como nuevo, lo vuelve a aplazar, y asi: el correo da vueltas
sin llegar nunca a una persona. Es el riesgo que el propio informe declaraba.
"""
import httpx
import pytest

from pipeline import reintentos
from pipeline.marcas import OtraCorridaLoTomo


def _respuesta(estado):
    """Respuesta con su peticion: `raise_for_status` la exige."""
    return httpx.Response(
        estado, json={},
        request=httpx.Request("PATCH", "https://graph.invalido/v1.0/x"))


# ── La escritura resiste lo que se repara solo ──

@pytest.mark.asyncio
async def test_un_corte_de_red_no_pierde_la_marca(monkeypatch):
    """Un timeout no dice nada del correo: el mismo PATCH vuelve a ser seguro."""
    from services import outlook as modulo

    intentos = []

    class ClienteQueFallaUnaVez:
        async def patch(self, url, headers=None, json=None, timeout=None):
            intentos.append(json)
            if len(intentos) == 1:
                raise httpx.ConnectTimeout("la red no respondio")
            return _respuesta(200)

    servicio = modulo.OutlookService.__new__(modulo.OutlookService)
    servicio.base_url = "https://graph.invalido/v1.0"
    servicio.user_email = "buzon@positiva.gov.co"
    servicio._client = ClienteQueFallaUnaVez()

    async def sin_esperar(_):
        return None

    monkeypatch.setattr(modulo.asyncio, "sleep", sin_esperar)
    monkeypatch.setattr(modulo.settings, "MODO_TEST", False)
    monkeypatch.setattr(servicio, "_get_headers", _cabeceras)
    monkeypatch.setattr(servicio, "_leer_categorias", _categorias_vacias)

    quedaron = await servicio.marcar("c1", "REINTENTO:3")

    assert len(intentos) == 2, "no se reintento la escritura"
    assert "REINTENTO:3" in quedaron


@pytest.mark.asyncio
async def test_el_backend_caido_tampoco_pierde_la_marca(monkeypatch):
    from services import outlook as modulo

    intentos = []

    class ClienteConUn503:
        async def patch(self, url, headers=None, json=None, timeout=None):
            intentos.append(json)
            if len(intentos) == 1:
                return _respuesta(503)
            return _respuesta(200)

    servicio = modulo.OutlookService.__new__(modulo.OutlookService)
    servicio.base_url = "https://graph.invalido/v1.0"
    servicio.user_email = "buzon@positiva.gov.co"
    servicio._client = ClienteConUn503()

    async def sin_esperar(_):
        return None

    monkeypatch.setattr(modulo.asyncio, "sleep", sin_esperar)
    monkeypatch.setattr(modulo.settings, "MODO_TEST", False)
    monkeypatch.setattr(servicio, "_get_headers", _cabeceras)
    monkeypatch.setattr(servicio, "_leer_categorias", _categorias_vacias)

    await servicio.marcar("c1", "REINTENTO:3")

    assert len(intentos) == 2


@pytest.mark.asyncio
async def test_otra_corrida_que_se_adelanta_no_se_reintenta(monkeypatch):
    """
    Un 412 NO es un fallo transitorio: dice que el correo ya tiene dueño.
    Reintentarlo sobrescribiria lo que la otra corrida escribio.
    """
    from services import outlook as modulo

    intentos = []

    class ClienteCon412:
        async def patch(self, url, headers=None, json=None, timeout=None):
            intentos.append(json)
            return _respuesta(412)

    servicio = modulo.OutlookService.__new__(modulo.OutlookService)
    servicio.base_url = "https://graph.invalido/v1.0"
    servicio.user_email = "buzon@positiva.gov.co"
    servicio._client = ClienteCon412()

    monkeypatch.setattr(modulo.settings, "MODO_TEST", False)
    monkeypatch.setattr(servicio, "_get_headers", _cabeceras)
    monkeypatch.setattr(servicio, "_leer_categorias", _categorias_vacias)

    with pytest.raises(OtraCorridaLoTomo):
        await servicio.marcar("c1", "REINTENTO:3")

    assert len(intentos) == 1, "un 412 no se reintenta"


async def _cabeceras():
    return {"Authorization": "Bearer x"}


async def _categorias_vacias(message_id):
    return [], ""


# ── Si aun asi no se escribe, el correo no queda dando vueltas ──

@pytest.mark.asyncio
async def test_sin_marca_escrita_el_correo_se_cierra(tmp_path):
    """
    Sin la cuenta, la corrida siguiente lo ve como nuevo y lo vuelve a aplazar
    para siempre. Cerrarlo a revision manual deja un desenlace que si consta.
    """
    from tests.test_orquestador import (
        OutlookFalso,
        RadicadorFalso,
        _email,
        _modelo_que_clasifica,
    )
    from pipeline.orquestador import Orquestador

    class OutlookQueNoMarca(OutlookFalso):
        async def marcar(self, id_correo, marca="", quitar_de_proceso=False):
            self.etiquetas.append((id_correo, marca))
            if marca.startswith(reintentos.PREFIJO):
                raise TimeoutError("Graph no respondio")
            from pipeline.marcas import fusionar

            self.categorias = fusionar(self.categorias, marca, quitar_de_proceso)
            return list(self.categorias)

    class RadicadorQueFalla(RadicadorFalso):
        async def radicar(self, *a, **kw):
            raise RuntimeError("503 Service Unavailable")

    outlook = OutlookQueNoMarca()
    orq = Orquestador(modelo=_modelo_que_clasifica(), outlook=outlook,
                      radicador=RadicadorQueFalla(), base_workspace=str(tmp_path))
    r = await orq.procesar(_email())

    assert r.accion == "manual", "sin cuenta no puede quedarse en la bandeja"
    assert outlook.movidos, "tiene que salir a revision manual"
    assert any("no quedo escrita la marca" in a for a in r.avisos), r.avisos


@pytest.mark.asyncio
async def test_con_la_marca_escrita_si_se_queda_en_la_bandeja(tmp_path):
    """El caso normal no cambia: con cuenta, el correo espera a la corrida siguiente."""
    from tests.test_orquestador import (
        OutlookFalso,
        RadicadorFalso,
        _email,
        _modelo_que_clasifica,
    )
    from pipeline.orquestador import Orquestador

    class RadicadorQueFalla(RadicadorFalso):
        async def radicar(self, *a, **kw):
            raise RuntimeError("503 Service Unavailable")

    outlook = OutlookFalso()
    orq = Orquestador(modelo=_modelo_que_clasifica(), outlook=outlook,
                      radicador=RadicadorQueFalla(), base_workspace=str(tmp_path))
    r = await orq.procesar(_email())

    assert r.accion == "aplazado"
    assert not outlook.movidos
    assert any("REINTENTO:1" in e for _, e in
               [(i, m) for i, m in outlook.etiquetas])
