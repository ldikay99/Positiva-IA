"""
La hoja que tesseract lee por debajo de CONFIANZA_PARA_VISION la transcribe el
modelo; la que lee bien, no. Medido sobre hojas reales el 2026-11-06.
"""
from PIL import Image

from pipeline import conversion

TEXTO_FLOJO = "1isnel Fegec Mura ac Kesonenoa Nagé-ca No Moli a Ve ms1% OlroST asiorvo"
TRANSCRITO = "Manuel Feged Murcia cc 79123456 Resonancia magnetica simple de columna lumbosacra"


def _respaldo(confianza, monkeypatch):
    llamadas = []
    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA", lambda jpeg: llamadas.append(1) or TRANSCRITO)
    presupuesto = conversion.PresupuestoDeOcr()
    texto, conf, _ = conversion._con_respaldo_ia(Image.new("RGB", (40, 40), "white"), TEXTO_FLOJO, confianza, "", presupuesto)
    return texto, conf, llamadas, presupuesto


def test_una_hoja_entre_60_y_80_la_transcribe_el_modelo(monkeypatch):
    texto, conf, llamadas, presupuesto = _respaldo(0.72, monkeypatch)
    assert llamadas and "Manuel Feged Murcia" in texto and conf is None
    assert texto.startswith(conversion.MARCA_TRANSCRITO_POR_IA)
    assert presupuesto.imagenes_ia == conversion.MAX_IMAGENES_IA_POR_CORREO - 1


def test_una_hoja_bien_leida_no_gasta_una_llamada(monkeypatch):
    texto, conf, llamadas, _ = _respaldo(0.88, monkeypatch)
    assert not llamadas and texto == TEXTO_FLOJO and conf == 0.88


def test_el_umbral_de_vision_no_mueve_el_de_la_relectura():
    """Subir el de la relectura busca el giro en hojas derechas: cuesta minutos."""
    assert conversion.CONFIANZA_REINTENTO_OSD < conversion.CONFIANZA_PARA_VISION
