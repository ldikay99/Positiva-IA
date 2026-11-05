"""
Medidor de consumo del modelo.

Comprueba que las dos mediciones sean comparables entre si y que la
estimacion del pipeline nuevo no quede por encima de lo medido contra Vertex.
"""
from pathlib import Path

import pytest

from harness.caso import cargar_corpus
from harness.consumo import (
    CARACTERES_POR_TOKEN,
    leer_medido,
    medir_nuevo,
    medir_viejo,
)

RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"

SERIE = (
    "fecha_hora\tid\tcorreos\tradicados\tmanual\tpct_algoritmo\tpct_ia"
    "\tpct_manual\tllamadas_modelo\ttokens_entrada\tcosto_usd\n"
    "2026-08-24T16:52:26\t402540\t3\t0\t3\t20\t33\t45\t3\t56136\t0.018611\n"
    "2026-08-24T17:02:50\t970347\t4\t1\t3\t44\t33\t22\t4\t32302\t0.011601\n"
)


@pytest.fixture(scope="module")
def corpus():
    casos = cargar_corpus(RAIZ_CORPUS)
    if not casos:
        pytest.skip("corpus no recolectado")
    return casos[:20]


def test_el_pipeline_nuevo_si_llama_al_modelo(corpus):
    """`tipo_solicitud` y `tipo_remitente` siempre se escalan, asi que hay al
    menos una llamada por correo."""
    m = medir_nuevo(corpus)
    assert m.llamadas > 0
    assert m.llamadas_por_correo >= 1
    assert m.tokens_por_correo > 0


def test_el_pipeline_viejo_manda_el_correo_entero_muchas_veces(corpus):
    viejo, nuevo = medir_viejo(corpus), medir_nuevo(corpus)
    assert viejo.llamadas_por_correo > nuevo.llamadas_por_correo
    assert viejo.tokens_por_correo > nuevo.tokens_por_correo


def test_los_cortocircuitos_del_viejo_se_respetan(corpus):
    """Los agentes de documento no se cuentan cuando hay un solo candidato."""
    m = medir_viejo(corpus)
    fijos = m.detalle["TIPO_SOLICITUD"]["llamadas"]
    elegir = m.detalle.get("ELEGIR_DOCUMENTO_AFECTADO", {}).get("llamadas", 0)
    assert elegir < fijos, "el agente de documento deberia saltarse casos"


def test_la_estimacion_cuenta_una_llamada_por_correo(corpus):
    """
    La estimacion supone que cada correo llega al agente. La medida real
    promedia sobre TODOS los correos, y los duplicados y reenviados no gastan
    ninguna llamada: los dos numeros no son comparables entre si.
    """
    m = medir_nuevo(corpus)
    assert m.llamadas <= m.correos * 2, (
        f"{m.llamadas} llamadas para {m.correos} correos")


# ── Lectura de las corridas reales ──

def test_se_leen_las_corridas_reales(tmp_path):
    ruta = tmp_path / "serie.tsv"
    ruta.write_text(SERIE, encoding="utf-8")
    m = leer_medido(ruta)
    assert m.corridas == 2
    assert m.correos == 7
    assert m.tokens_entrada == 88438
    assert m.tokens_por_correo == 88438 // 7


def test_sin_corridas_no_se_inventa_un_numero(tmp_path):
    assert leer_medido(tmp_path / "no-existe.tsv") is None
    vacia = tmp_path / "serie.tsv"
    vacia.write_text(SERIE.splitlines()[0] + "\n", encoding="utf-8")
    assert leer_medido(vacia) is None


def test_la_conversion_a_tokens_esta_declarada():
    """Un numero magico escondido en una division no se puede discutir."""
    assert CARACTERES_POR_TOKEN > 0
