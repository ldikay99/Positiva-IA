"""
Dos fallos vistos en produccion el 2026-11-05 que no eran del contenido.

1. Un adjunto en un formato que no se convierte (.msg, .xls de Office 97)
   aplazaba el correo entero como ERROR_INESPERADO aunque ya tuviera todos sus
   datos, y la corrida siguiente volvia a fallar igual.
2. Graph devolvio la bandeja cortada a mitad del JSON y se cayo la corrida.
"""
import json

import pytest

from pipeline.errores import POR_CODIGO, Bitacora, Severidad, clasificar


def test_un_adjunto_no_soportado_degrada_y_no_tumba_el_correo():
    clase = clasificar(cuerpo="tipo no soportado para conversion a Markdown: "
                              "ofimatica_antigua")
    assert clase.codigo == "ADJUNTO_NO_LEIDO"
    assert clase.severidad is Severidad.DEGRADA
    b = Bitacora()
    b.registrar("CONVERSION", "x.msg: tipo no soportado para conversion a "
                              "Markdown: ofimatica_antigua",
                cuerpo="tipo no soportado para conversion a Markdown")
    assert b.puede_radicar


def test_el_motivo_nuevo_tiene_su_texto_para_el_operador():
    from pipeline.notificacion import MOTIVO_PARA_EL_CORREO
    assert "ADJUNTO_NO_LEIDO" in MOTIVO_PARA_EL_CORREO
    assert POR_CODIGO["ADJUNTO_NO_LEIDO"].motivo_manual == "ADJUNTO_NO_LEIDO"


class _Respuesta:
    def __init__(self, cuerpo):
        self.status_code = 200
        self.headers = {}
        self._cuerpo = cuerpo

    def raise_for_status(self):
        pass

    def json(self):
        return json.loads(self._cuerpo)


class _GraphQueCorta:
    def __init__(self, cortes):
        self.cortes = cortes
        self.tops = []

    async def get(self, url, params=None, **kw):
        self.tops.append(params["$top"])
        if self.cortes:
            self.cortes -= 1
            return _Respuesta('{"value": [{"id": "a", "subj')
        return _Respuesta(json.dumps({"value": []}))


def _servicio(cliente, monkeypatch):
    from services import outlook

    async def sin_espera(_s):
        return None

    monkeypatch.setattr(outlook.asyncio, "sleep", sin_espera)
    svc = outlook.OutlookService()
    svc._client = cliente
    svc._access_token = "token-de-prueba"
    svc._token_expires_at = 9e18
    return svc


@pytest.mark.asyncio
async def test_una_bandeja_cortada_se_vuelve_a_pedir_con_menos(monkeypatch):
    graph = _GraphQueCorta(cortes=1)
    correos = await _servicio(graph, monkeypatch).get_emails("carpeta", limit=50)
    assert correos == []
    assert graph.tops == [150, 75]


@pytest.mark.asyncio
async def test_si_sigue_cortada_falla_como_antes(monkeypatch):
    graph = _GraphQueCorta(cortes=10)
    with pytest.raises(ValueError):
        await _servicio(graph, monkeypatch).get_emails("carpeta", limit=50)
