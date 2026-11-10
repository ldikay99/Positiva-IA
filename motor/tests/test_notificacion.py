"""
Aviso que queda en el hilo del correo con el resultado del procesamiento.

Lo lee personal de la entidad, no quien opera el sistema: no puede contener
terminos internos ni describir como funciona el pipeline por dentro.
"""
import re

import pytest

from pipeline.notificacion import (
    MOTIVO_PARA_EL_CORREO,
    PIE,
    redactar,
)
from pipeline.telemetria import MOTIVOS_MANUAL


def _texto(html_str):
    """El HTML sin etiquetas, que es lo que acaba leyendo una persona."""
    import html as _html

    return _html.unescape(re.sub(r"<[^>]+>", " ", html_str))


# ── Lo que dice cuando radica ──

def test_el_radicado_lleva_su_numero():
    assert "ENT20260001602279" in redactar(
        "radicado", radicado="ENT20260001602279")


def test_el_radicado_dice_a_que_oficina_entro():
    assert "Medicina Laboral" in redactar(
        "radicado", radicado="ENT2026", oficina="Medicina Laboral")


# ── Lo que dice cuando no radica ──

@pytest.mark.parametrize("motivo", sorted(MOTIVOS_MANUAL))
def test_todo_motivo_tiene_redaccion_para_el_correo(motivo):
    """
    Un codigo sin redaccion externa saldria con la interna, que habla de
    componentes del sistema.
    """
    assert motivo in MOTIVO_PARA_EL_CORREO


@pytest.mark.parametrize("motivo", sorted(MOTIVOS_MANUAL))
def test_el_correo_no_lleva_el_codigo_del_motivo(motivo):
    assert motivo not in redactar("manual", motivo=motivo)


@pytest.mark.parametrize("motivo", sorted(MOTIVO_PARA_EL_CORREO))
def test_el_correo_no_nombra_piezas_del_sistema(motivo):
    """
    Terminos que dentro del proyecto son precisos y fuera no significan nada, o
    significan algo distinto.
    """
    prohibidos = (
        "modelo", "pipeline", "compuerta", "cloudflare", "backend", "traza",
        "corpus", "agente", "token", "prompt", "sgdea", "vertex", "gemini",
        "redis", "postgres", "ocr", "regex", "json", "api", "log",
    )
    texto = _texto(redactar("manual", motivo=motivo)).lower()
    encontrados = [p for p in prohibidos if re.search(rf"\b{p}\w*\b", texto)]
    assert not encontrados, f"{motivo}: {encontrados}"


@pytest.mark.parametrize("accion", sorted(
    ["radicado", "duplicado", "reenviado", "aplazado", "manual", "error"]))
def test_ningun_desenlace_explica_como_funciona_el_sistema(accion):
    """
    El aviso dice que paso con la comunicacion, no como esta hecho el sistema
    ni a quien le ahorra trabajo.
    """
    texto = _texto(redactar(accion, radicado="ENT1", motivo="SIN_DOCUMENTO",
                            oficina="Oficina")).lower()
    for frase in ("sin abrir", "sin consultar", "sin pedir", "ahorra",
                  "ejecucion", "corrida", "reintenta sola", "no hay que hacer"):
        assert frase not in texto, f"{accion}: '{frase}'"


def test_el_aviso_dice_que_pasa_despues():
    assert "gestion manual" in _texto(redactar("manual", motivo="SIN_DOCUMENTO"))
    assert "reintentara" in _texto(redactar("aplazado",
                                            motivo="MODELO_NO_RESPONDIO"))
    assert "no corresponde a una PQRD" in _texto(redactar("reenviado"))


def test_el_reenviado_dice_a_donde_fue():
    cuerpo = redactar(
        "reenviado",
        detalle="Remitida a notificacionesjudiciales@positiva.gov.co")
    assert "notificacionesjudiciales@positiva.gov.co" in cuerpo


def test_el_detalle_acompaña_al_motivo_generico():
    cuerpo = _texto(redactar("manual", motivo="SIN_DOCUMENTO",
                             detalle="Datos no obtenidos: telefono."))
    assert MOTIVO_PARA_EL_CORREO["SIN_DOCUMENTO"] in cuerpo
    assert "telefono" in cuerpo


# ── Forma ──

@pytest.mark.parametrize("accion", [
    "radicado", "duplicado", "reenviado", "aplazado", "manual", "error"])
def test_todo_aviso_declara_que_lo_genero_una_ia(accion):
    assert PIE[:40] in redactar(accion, radicado="ENT1", motivo="SIN_DOCUMENTO")


def test_el_texto_del_correo_no_puede_inyectar_html():
    cuerpo = redactar("manual", motivo="SIN_DOCUMENTO",
                      detalle="<script>alert(1)</script>")
    assert "<script>" not in cuerpo
    assert "&lt;script&gt;" in cuerpo


def test_un_desenlace_desconocido_no_revienta():
    assert PIE[:40] in redactar("algo_que_no_existe")


def test_un_motivo_desconocido_sale_con_la_frase_generica():
    assert MOTIVO_PARA_EL_CORREO["ERROR_INESPERADO"] in _texto(
        redactar("manual", motivo="ALGO_QUE_NO_EXISTE"))


# ── La cabecera del reenvio nombra el area a la que va ──

def test_cada_area_recibe_su_propio_nombre():
    """
    Una sola cabecera para los dos destinos hace que lo derivado a
    Notificaciones Judiciales anuncie que se derivo a Medicina Laboral.
    """
    from pipeline.notificacion import encabezado_de_reenvio

    judicial = _texto(encabezado_de_reenvio("Notificaciones Judiciales"))
    laboral = _texto(encabezado_de_reenvio("Medicina Laboral"))

    assert "Notificaciones Judiciales" in judicial
    assert "Medicina Laboral" not in judicial
    assert "Medicina Laboral" in laboral
    assert "Notificaciones Judiciales" not in laboral


def test_el_reenvio_dice_que_lo_hizo_la_ia():
    from pipeline.notificacion import encabezado_de_reenvio

    assert "IA" in _texto(encabezado_de_reenvio("Medicina Laboral"))


def test_la_evidencia_del_reenvio_no_inyecta_html():
    from pipeline.notificacion import encabezado_de_reenvio

    salida = encabezado_de_reenvio("Area", "<script>alert(1)</script>")
    assert "<script>" not in salida
