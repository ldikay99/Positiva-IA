"""
La etiqueta del correo es el eslabon del que cuelga toda la deduplicacion.

El pipeline la escribe al radicar y la lee en la corrida siguiente para saber
que el caso ya se atendio. Dos formas de romperla, las dos silenciosas:

  - que el lector sea mas laxo que el escritor: "ENTREGADO" pasa por radicado y
    se descarta una PQRD legitima, que es peor que duplicarla;
  - que SGDEA cambie el formato del identificador y se escriba tal cual: la
    etiqueta deja de reconocerse y a partir de ahi todo se radica dos veces.
"""
import pytest

from utils.radicado import (
    PREFIJO_PROPIO,
    es_radicado,
    etiqueta_de_radicado,
    etiqueta_para,
)


# ── Lo que se reconoce ──

@pytest.mark.parametrize("etiqueta", [
    "ENT20260001", "ENT-20260001", "ENT 20260001", "ent20260001",
    "  ENT20260001  ", f"{PREFIJO_PROPIO}20260001",
])
def test_se_reconoce_un_radicado(etiqueta):
    assert es_radicado(etiqueta)


@pytest.mark.parametrize("etiqueta", [
    "ENTREGADO", "ENTIDAD EXTERNA", "ENT", "ENT-", "",
    "REVISION-MANUAL:SIN_DOCUMENTO", None, PREFIJO_PROPIO,
])
def test_no_se_confunde_con_otra_etiqueta(etiqueta):
    assert not es_radicado(etiqueta)


def test_las_etiquetas_viejas_del_buzon_siguen_valiendo():
    """
    El buzon tiene miles de correos etiquetados por el pipeline anterior. Si el
    lector dejara de reconocerlos, todos se volverian a radicar.
    """
    assert es_radicado("ENT20250912")


# ── Lo que se escribe ──

def test_un_radicado_normal_se_escribe_tal_cual():
    etiqueta, anomalia = etiqueta_para("ENT20260001")
    assert etiqueta == "ENT20260001"
    assert anomalia == ""


def test_un_formato_inesperado_se_envuelve_y_se_reporta():
    """
    El formato lo decide SGDEA, no nosotros. Escribirlo tal cual dejaria la
    etiqueta irreconocible y la deduplicacion muerta a partir de ese momento.
    """
    etiqueta, anomalia = etiqueta_para("2026-0001234")
    assert etiqueta == f"{PREFIJO_PROPIO}2026-0001234"
    assert es_radicado(etiqueta), "lo escrito tiene que poder leerse despues"
    assert "revisar el contrato" in anomalia


def test_sin_radicado_no_se_inventa_una_etiqueta():
    etiqueta, anomalia = etiqueta_para("")
    assert etiqueta == ""
    assert anomalia


def test_lo_escrito_siempre_se_puede_volver_a_leer():
    """La propiedad que sostiene la deduplicacion, sea cual sea el formato."""
    for devuelto in ("ENT20260001", "2026-0001234", "R-99", "abc123"):
        etiqueta, _ = etiqueta_para(devuelto)
        assert etiqueta_de_radicado([etiqueta]) == etiqueta, devuelto


# ── Enganche con el pipeline ──

@pytest.mark.asyncio
async def test_el_correo_radicado_queda_con_una_etiqueta_legible(tmp_path):
    from tests.test_orquestador import (
        OutlookFalso,
        RadicadorFalso,
        _email,
        _modelo_que_clasifica,
    )
    from pipeline.orquestador import Orquestador

    outlook = OutlookFalso()
    orq = Orquestador(modelo=_modelo_que_clasifica(), outlook=outlook,
                      radicador=RadicadorFalso("ENT20260001"),
                      base_workspace=str(tmp_path))
    r = await orq.procesar(_email())

    assert r.accion == "radicado", r.motivo_manual
    puestas = [e for _, e in outlook.etiquetas]
    assert any(es_radicado(e) for e in puestas), puestas


@pytest.mark.asyncio
async def test_un_radicado_con_formato_raro_no_mata_la_deduplicacion(tmp_path):
    from tests.test_orquestador import (
        OutlookFalso,
        RadicadorFalso,
        _email,
        _modelo_que_clasifica,
    )
    from pipeline.orquestador import Orquestador

    outlook = OutlookFalso()
    orq = Orquestador(modelo=_modelo_que_clasifica(), outlook=outlook,
                      radicador=RadicadorFalso("2026-0001234"),
                      base_workspace=str(tmp_path))
    r = await orq.procesar(_email())

    puestas = [e for _, e in outlook.etiquetas]
    assert any(es_radicado(e) for e in puestas), puestas
    assert any("revisar el contrato" in a for a in r.avisos), r.avisos
