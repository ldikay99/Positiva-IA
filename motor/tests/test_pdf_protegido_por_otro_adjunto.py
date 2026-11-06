"""Un PDF protegido con la cedula que viene en OTRO adjunto se abre igual."""
import pytest

from pipeline.materializar import materializar_adjuntos
from pipeline.workspace import Workspace

pymupdf = pytest.importorskip("pymupdf")


def _pdf(texto, clave=None):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), texto)
    if clave:
        return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                           user_pw=clave, owner_pw="propietario")
    return doc.tobytes()


def test_se_abre_con_la_cedula_de_otro_adjunto(tmp_path):
    protegido = _pdf("RESUMEN HISTORIA CLINICA", clave="80239271")
    orden = _pdf("ORDEN Identificacion: CC 80239271 JOSE PEREZ")
    with Workspace(tmp_path, "x") as ws:
        assert materializar_adjuntos(ws, [("resumen.pdf", protegido),
                                          ("orden.pdf", orden)]) == 2
        estados = {e.nombre_original: e.estado for e in ws.manifiesto.entradas}
    assert estados == {"resumen.pdf": "convertido", "orden.pdf": "convertido"}


def test_sin_la_clave_en_ningun_lado_queda_como_error(tmp_path):
    protegido = _pdf("RESUMEN", clave="11111111")
    orden = _pdf("ORDEN sin documento")
    with Workspace(tmp_path, "x") as ws:
        assert materializar_adjuntos(ws, [("resumen.pdf", protegido),
                                          ("orden.pdf", orden)]) == 1
        resumen = [e for e in ws.manifiesto.entradas
                   if e.nombre_original == "resumen.pdf"]
    assert len(resumen) == 1 and "contraseña" in resumen[0].detalle
