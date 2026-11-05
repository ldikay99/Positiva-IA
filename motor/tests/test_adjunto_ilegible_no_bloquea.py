"""
Un adjunto que no se puede leer no impide radicar.

Un adjunto protegido, ilegible o sin motor de OCR es un SOPORTE que falta, no
un DATO que falta. Si el correo trae el nombre, el documento y el tramite, la
radicacion tiene todo lo que necesita y el soporte se adjunta igual.

Solo cuando el dato vivia unicamente en ese adjunto el correo llega a una
persona, y entonces el motivo nombra al adjunto.
"""
import pytest

from pipeline.errores import Bitacora, Severidad, clasificar


# ── Un fallo de conversion degrada, no corta ──

@pytest.mark.parametrize("texto,codigo", [
    ("el PDF esta protegido con contraseña", "ADJUNTO_PROTEGIDO"),
    ("paginas sin texto extraible", "ADJUNTO_ILEGIBLE"),
    ("TesseractNotFoundError: tesseract is not installed", "SIN_OCR"),
])
def test_un_adjunto_que_no_se_lee_no_es_fatal(texto, codigo):
    clase = clasificar(cuerpo=texto)

    assert clase.codigo == codigo
    assert clase.severidad is Severidad.DEGRADA


@pytest.mark.parametrize("texto", [
    "el PDF esta protegido con contraseña",
    "paginas sin texto extraible",
    "TesseractNotFoundError: tesseract is not installed",
])
def test_con_ese_fallo_la_radicacion_sigue_en_pie(texto):
    b = Bitacora()
    b.registrar("CONVERSION", texto, cuerpo=texto)

    assert b.puede_radicar, "un soporte ilegible no impide radicar"


# ── Los datos se toman de donde esten ──

def test_el_cuerpo_basta_cuando_el_adjunto_no_se_lee():
    """El adjunto aporta soporte; los datos pueden venir del cuerpo."""
    from pipeline.extraccion import extraer_todo

    e = extraer_todo(
        asunto="Pago de incapacidad",
        cuerpo="Solicito el pago de la incapacidad de CLARA VELEZ MORA,\n"
               "identificada con C.C. 41234567. Celular 3105551234.\n"
               "Adjunto la historia clinica.",
        from_name="Clara Velez", from_address="clara@correo.com",
        texto_adjuntos="",
    )

    for campo in ("nombre_afectado", "numero_documento_afectado",
                  "tipo_documento_afectado", "tipo_solicitud"):
        assert campo in e.hallazgos, campo


def test_el_adjunto_aporta_lo_que_el_cuerpo_no_trae():
    """Y al reves: si el cuerpo no lo dice, se lee del adjunto convertido."""
    from pipeline.extraccion import extraer_todo

    e = extraer_todo(
        asunto="Envio de soportes",
        cuerpo="Buen dia, adjunto los documentos solicitados.",
        from_name="Clara Velez", from_address="clara@correo.com",
        texto_adjuntos="HISTORIA CLINICA\nPaciente: CLARA VELEZ MORA\n"
                       "Identificacion: C.C. 41234567\n",
    )

    assert "numero_documento_afectado" in e.hallazgos


@pytest.mark.asyncio
async def test_un_correo_con_adjunto_ilegible_se_radica(tmp_path):
    """
    De punta a punta: el adjunto no se pudo convertir y el correo sale
    radicado, porque los datos estaban en el cuerpo.
    """
    from tests.test_orquestador import (
        OutlookFalso,
        RadicadorFalso,
        _email,
        _modelo_que_clasifica,
    )
    from pipeline.orquestador import Orquestador

    class OutlookConAdjuntoIlegible(OutlookFalso):
        async def get_attachments(self, id_correo):
            # Un PDF que no es un PDF: la conversion no saca texto de el.
            return [("historia_clinica.pdf", b"no-es-un-pdf-de-verdad")]

    outlook = OutlookConAdjuntoIlegible()
    radicador = RadicadorFalso()
    orq = Orquestador(modelo=_modelo_que_clasifica(), outlook=outlook,
                      radicador=radicador, base_workspace=str(tmp_path))

    r = await orq.procesar(_email())

    assert r.accion == "radicado", r.motivo_manual
    assert radicador.payloads, "el correo tenia los datos en el cuerpo"


# ── Cuando el dato SI vivia solo en el adjunto ──

def test_si_el_documento_solo_estaba_en_el_adjunto_se_dice_cual_fue_la_causa():
    """
    Ahi el adjunto si es la causa, y el motivo lo nombra en vez de decir que
    falto un documento sin explicar por que.
    """
    from pipeline.compuerta import validar
    from pipeline.extraccion import Hallazgo

    d = validar(
        {"nombre_afectado": Hallazgo("nombre_afectado", "CLARA VELEZ", "e", 0.9),
         "nombre_remitente": Hallazgo("nombre_remitente", "CLARA VELEZ", "e", 0.9),
         "tipo_solicitud": Hallazgo("tipo_solicitud", 16, "e", 0.9)},
        motivos_no_resueltos={
            "numero_documento_afectado": "DOCUMENTO_EN_ADJUNTO_ILEGIBLE"},
    )

    assert d.motivo_manual == "ADJUNTO_ILEGIBLE"
