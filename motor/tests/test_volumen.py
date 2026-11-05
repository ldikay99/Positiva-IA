"""
Desde cuando un correo deja de pasar por el modelo.

Los topes salen de medir el buzon: sobre 60 correos reales, el corriente trae
un adjunto y dos paginas, y el percentil 95 son cinco adjuntos y diecinueve
paginas. Lo que se comprueba aqui es que el corte no alcance a ese correo
corriente y si al que se sale de rango.
"""
import pytest

from pipeline import volumen


def _pdf(paginas=1, texto="Solicito el certificado"):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for i in range(paginas):
        doc.new_page().insert_text((72, 72), f"{texto} {i + 1}")
    datos = doc.tobytes()
    doc.close()
    return datos


# ── El correo corriente pasa ──

def test_el_correo_corriente_no_se_corta():
    """Un adjunto y dos paginas es la mediana del buzon."""
    v = volumen.medir([("incapacidad.pdf", _pdf(2))])

    assert v.se_pasa is False
    assert v.adjuntos == 1 and v.paginas == 2


def test_el_percentil_95_del_buzon_tampoco_se_corta():
    """Cinco adjuntos y diecinueve paginas se siguen procesando."""
    adjuntos = [(f"soporte-{i}.pdf", _pdf(4)) for i in range(4)]
    adjuntos.append(("historia.pdf", _pdf(3)))
    v = volumen.medir(adjuntos)

    assert v.adjuntos == 5 and v.paginas == 19
    assert v.se_pasa is False, v.detalle()


def test_un_correo_sin_adjuntos_no_se_corta():
    assert volumen.medir([]).se_pasa is False
    assert volumen.medir(None).se_pasa is False


# ── Lo que se sale de rango sí ──

def test_demasiadas_paginas_cortan_el_correo():
    v = volumen.medir([("expediente.pdf", _pdf(volumen.MAX_PAGINAS + 1))])

    assert v.se_pasa is True
    assert "paginas" in v.detalle()


def test_demasiados_adjuntos_cortan_el_correo():
    adjuntos = [(f"a-{i}.pdf", _pdf(1)) for i in range(volumen.MAX_ADJUNTOS + 1)]
    v = volumen.medir(adjuntos)

    assert v.se_pasa is True
    assert "adjuntos" in v.detalle()


def test_demasiado_tamano_corta_el_correo():
    grande = b"%PDF-1.4\n" + b"\x00" * (volumen.MAX_MEGABYTES * 1048576 + 1)
    v = volumen.medir([("escaneo.pdf", grande)])

    assert v.se_pasa is True
    assert "MB" in v.detalle()


def test_el_detalle_dice_que_se_paso_y_de_cuanto():
    """Quien lo revise tiene que saber por que llego a sus manos."""
    v = volumen.medir([("expediente.pdf", _pdf(volumen.MAX_PAGINAS + 5))])

    assert str(volumen.MAX_PAGINAS) in v.detalle()
    assert str(volumen.MAX_PAGINAS + 5) in v.detalle()


# ── Lo que no se cuenta ──

def test_las_firmas_y_los_logos_no_cuentan():
    """
    No llegan al conversor. Contarlos sacaria de la ia correos que no traen
    nada: un hilo largo arrastra una firma por mensaje.
    """
    adjuntos = [(f"image00{i}.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
                for i in range(volumen.MAX_ADJUNTOS + 5)]
    v = volumen.medir(adjuntos)

    assert v.adjuntos == 0
    assert v.se_pasa is False


def test_un_pdf_que_no_se_puede_abrir_no_se_juzga_por_su_volumen():
    """
    No se sabe cuantas paginas tiene, y suponer muchas lo mandaria a revision
    manual por el motivo equivocado: la conversion ya le pone el suyo.
    """
    v = volumen.medir([("roto.pdf", b"%PDF-1.4 basura sin estructura")])

    assert v.paginas == 0
    assert v.se_pasa is False


def test_un_adjunto_que_no_es_documento_no_aporta_paginas():
    v = volumen.medir([("datos.txt", b"CC 41234567 del trabajador")])

    assert v.adjuntos == 1 and v.paginas == 0
    assert v.se_pasa is False


# ── Los topes se ajustan sin desplegar ──

@pytest.mark.parametrize("variable,funcion", [
    ("MAX_PAGINAS_POR_CORREO", "MAX_PAGINAS"),
    ("MAX_ADJUNTOS_POR_CORREO", "MAX_ADJUNTOS"),
    ("MAX_MEGABYTES_POR_CORREO", "MAX_MEGABYTES"),
])
def test_cada_tope_se_puede_ajustar_por_entorno(monkeypatch, variable, funcion):
    monkeypatch.setenv(variable, "7")
    assert volumen._tope(variable, 40) == 7


@pytest.mark.parametrize("crudo", ["", "  ", "muchas", "0", "-3"])
def test_un_tope_que_no_es_un_numero_util_no_se_aplica(monkeypatch, crudo):
    """Un cero apagaria el procesamiento entero."""
    monkeypatch.setenv("MAX_PAGINAS_POR_CORREO", crudo)
    assert volumen._tope("MAX_PAGINAS_POR_CORREO", 40) == 40


# ── El motivo se comporta como los demas ──

def test_el_motivo_esta_en_el_catalogo_y_tiene_redaccion_externa():
    from pipeline.notificacion import MOTIVO_PARA_EL_CORREO
    from pipeline.telemetria import MOTIVOS_MANUAL

    assert volumen.MOTIVO in MOTIVOS_MANUAL
    assert volumen.MOTIVO in MOTIVO_PARA_EL_CORREO


def test_el_motivo_no_se_aplaza():
    """El correo no va a traer menos paginas en la corrida siguiente."""
    from pipeline.reintentos import se_aplaza

    assert se_aplaza(volumen.MOTIVO) is False


def test_el_desenlace_lo_decidio_el_contenido_del_correo():
    """
    Se corto por lo que trae, no por infraestructura: la explicacion del
    correo si es pertinente.
    """
    from pipeline.telemetria import lo_decidio_el_contenido

    assert lo_decidio_el_contenido(volumen.MOTIVO) is True
