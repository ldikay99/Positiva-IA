"""
Facturas electronicas que llegan al buzon de PQRD.

Una factura no es la peticion de nadie. Lo que la distingue es la FORMA con que
la emite un proveedor de facturacion, no que venga de un buzon que se llame
"facturacion": una empresa escribe desde ese buzon para pedir un tramite propio,
y eso si es una PQRD.
"""
import pytest

from pipeline.facturas import MOTIVO, es_factura


# ── La forma del asunto ──

@pytest.mark.parametrize("asunto", [
    "800084362;HOSPITAL CIVIL - EMPRESA SOCIAL DEL ESTADO;FEV1234;01;HOSPITAL CIVIL ESE",
    "813005265;ESE CARMEN EMILIA OSPINA;NC105687;91;ESE CARMEN EMILIA OSPINA",
    "901768746;JUNTA REGIONAL DE CALIFICACION DEL CESAR;JRC2791;01;",
    # Sin el separador final: no todos los proveedores lo ponen.
    "800084362;HOSPITAL CIVIL - EMPRESA SOCIAL DEL ESTADO;FEV1234;01",
    "900123456;COMERCIALIZADORA XYZ SAS;1234;1",
    # El numero mezcla letras y digitos en cualquier orden. Con las letras
    # admitidas solo como prefijo se escapaba un tercio de las notas credito
    # de un emisor real.
    "800084362;HOSPITAL CIVIL - EMPRESA SOCIAL DEL ESTADO;NCR12F45;91;HOSPITAL CIVIL ESE",
    "800084362;HOSPITAL CIVIL - EMPRESA SOCIAL DEL ESTADO;ND9A88;92;",
])
def test_el_asunto_con_forma_de_factura_se_reconoce(asunto):
    assert es_factura(asunto=asunto)


def test_la_evidencia_dice_en_que_se_reconocio():
    """Quien lo revise no tiene por que adivinar."""
    razon = es_factura(asunto="800084362;HOSPITAL CIVIL;FE1234;01;")
    assert "forma con que se emite una factura" in razon


# ── Las marcas del documento electronico ──

@pytest.mark.parametrize("cuerpo", [
    "Adjuntamos la factura electronica de venta correspondiente al periodo.",
    "Se anexa nota credito electronica por el valor indicado.",
    "Nota debito electronica generada el 12 de septiembre.",
    "Representacion grafica de la factura en el archivo adjunto.",
])
def test_un_documento_que_se_declara_electronico_se_reconoce(cuerpo):
    assert es_factura(asunto="Envio de documento", cuerpo=cuerpo)


@pytest.mark.parametrize("cuerpo", [
    "CUFE: a1b2c3d4e5f6a1b2c3d4e5f6",
    "cude = 9f8e7d6c5b4a",
])
def test_el_codigo_de_la_dian_basta_por_si_solo(cuerpo):
    """Ese identificador no aparece en un correo que no sea un documento electronico."""
    assert es_factura(asunto="Documento", cuerpo=cuerpo)


# ── Lo que NO puede llevarse por delante ──

@pytest.mark.parametrize("asunto,cuerpo", [
    ("Solicitud de pago de incapacidad", "Solicito el pago de mi incapacidad."),
    ("Derecho de peticion", "Solicito copia de mi historia clinica."),
    # Un NIT suelto en el asunto no es la forma de una factura.
    ("900123456 solicito revision de mi caso", "Buen dia, requiero ayuda."),
    ("RV: Solicitud de reembolso", "Adjunto la factura del taxi al hospital."),
    # Reclamar un cobro es una PQRD, y nombra la palabra factura.
    ("Reclamo por cobro", "Me llego una factura que no corresponde a mi empresa."),
    ("Cobro indebido", "La factura de aportes esta mal liquidada, solicito correccion."),
    # El segundo segmento no puede ser un numero suelto: eso es una cedula y un
    # telefono escritos con punto y coma, no una razon social.
    ("900123456;123;456;78", "Solicito revision de mi caso."),
])
def test_una_pqrd_corriente_no_se_toma_por_factura(asunto, cuerpo):
    assert not es_factura(asunto=asunto, cuerpo=cuerpo)


def test_el_buzon_de_facturacion_por_si_solo_no_decide():
    """
    Una empresa escribe desde su buzon de facturacion para pedir un tramite
    propio: eso es una PQRD y hay que radicarla.
    """
    assert not es_factura(
        asunto="Actualizacion de codigos CIIU",
        cuerpo="Solicito actualizar la actividad economica de la empresa.")


def test_sin_contenido_no_revienta():
    assert es_factura() == ""
    assert es_factura(asunto="", cuerpo="") == ""


# ── El desenlace ──

@pytest.mark.asyncio
async def test_una_factura_va_a_revision_manual_y_no_se_radica(tmp_path):
    """
    No se reenvia: no es de ningun area, asi que mandarla a otro buzon seria
    pasarle algo que tampoco le toca.
    """
    from tests.test_orquestador import (
        OutlookFalso,
        RadicadorFalso,
        _email,
        _modelo_que_clasifica,
    )
    from pipeline.orquestador import Orquestador

    outlook = OutlookFalso()
    radicador = RadicadorFalso()
    orq = Orquestador(modelo=_modelo_que_clasifica(), outlook=outlook,
                      radicador=radicador, base_workspace=str(tmp_path))
    r = await orq.procesar(_email(
        asunto="800084362;HOSPITAL CIVIL - EMPRESA SOCIAL DEL ESTADO;FE1234;01;"))

    assert r.accion == "manual"
    assert r.motivo_manual == MOTIVO
    assert not radicador.payloads, "una factura no se radica"
    assert not outlook.reenviados, "no es de ningun area"


def test_el_motivo_de_la_factura_se_puede_explicar():
    from pipeline.notificacion import MOTIVO_PARA_EL_CORREO
    from pipeline.telemetria import MOTIVOS_MANUAL

    assert MOTIVO in MOTIVOS_MANUAL
    assert MOTIVO in MOTIVO_PARA_EL_CORREO
