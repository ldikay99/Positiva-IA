"""
Direcciones cuyos correos no se radican.

La lista es de direcciones completas, no de dominios. Del dominio de Positiva
llegan PQRD de ciudadanos que un area reenvia, y el remitente real se saca del
hilo citado: excluir un dominio entero las perderia todas.
"""
import pytest

from pipeline.enrutamiento import (
    MOTIVO_REMITENTE_EXCLUIDO,
    REMITENTES_QUE_NO_SE_RADICAN,
    Destino,
    decidir,
)

FACTURACION = "facturacionelectronica@hci.gov.co"
BUZON_PROPIO = "servicioalcliente@positiva.gov.co"


def test_el_buzon_de_facturacion_esta_en_la_lista():
    """Lo que manda son facturas y notas, que tienen su propio tramite."""
    assert FACTURACION in REMITENTES_QUE_NO_SE_RADICAN


@pytest.mark.parametrize("de", [
    FACTURACION,
    FACTURACION.upper(),
    f"  {FACTURACION}  ",
])
def test_su_correo_no_se_radica(de):
    ruta = decidir(asunto="800084362;HOSPITAL CIVIL;FE1234;01;",
                   cuerpo="Adjunto documento electronico.", correo_remitente=de)

    assert ruta.destino is Destino.NO_SE_RADICA
    assert not ruta.se_radica
    assert FACTURACION in ruta.evidencia


def test_no_se_reenvia_a_ninguna_parte():
    """Devolverselo a quien lo escribio lo deja rebotando entre dos buzones."""
    ruta = decidir(asunto="Factura", cuerpo="", correo_remitente=FACTURACION)

    assert not ruta.se_reenvia
    assert ruta.correo_destino == ""


def test_manda_sobre_cualquier_otra_regla():
    """
    Una factura que ademas nombre una tutela sigue sin ser una PQRD: la manda
    un buzon que no radica por este canal.
    """
    ruta = decidir(
        asunto="Notificacion de fallo de tutela",
        cuerpo="El juzgado ordena a la entidad dar cumplimiento.",
        correo_remitente=FACTURACION)

    assert ruta.destino is Destino.NO_SE_RADICA


# ── Lo que la lista NO puede llevarse por delante ──

@pytest.mark.parametrize("de", [
    BUZON_PROPIO,
    "solicitudesmedicinalaboral@positiva.gov.co",
    "notificacionesjudiciales@positiva.gov.co",
    "indemnizaciones@positiva.gov.co",
    "nombre.apellido@positiva.gov.co",
])
def test_un_buzon_de_positiva_sigue_su_camino(de):
    """
    Un area reenvia la PQRD de un ciudadano y el remitente real sale del hilo.
    Excluir el dominio entero perderia esos casos.
    """
    ruta = decidir(asunto="Solicitud de incapacidad",
                   cuerpo="Solicito el pago.", correo_remitente=de)

    assert ruta.destino is not Destino.NO_SE_RADICA


def test_otro_buzon_de_facturacion_no_queda_excluido_por_llamarse_asi():
    """
    La lista es de direcciones, no de palabras: una empresa que escribe desde
    su buzon de facturacion puede estar pidiendo un tramite propio.
    """
    ruta = decidir(asunto="Actualizacion de codigos CIIU",
                   cuerpo="Solicito actualizar la actividad economica.",
                   correo_remitente="facturacion@zyntragrp.com")

    assert ruta.se_radica


def test_la_lista_no_lleva_dominios():
    """Una entrada sin parte local excluiria a todo el que escriba desde ahi."""
    sin_local = [d for d in REMITENTES_QUE_NO_SE_RADICAN if d.startswith("@")]
    assert not sin_local, sin_local


def test_las_direcciones_de_la_lista_estan_normalizadas():
    for d in REMITENTES_QUE_NO_SE_RADICAN:
        assert d == d.strip().lower(), d
        assert "@" in d and not d.startswith("@"), d


def test_un_remitente_corriente_no_se_toca():
    ruta = decidir(asunto="Solicitud de incapacidad",
                   cuerpo="Solicito el pago.",
                   correo_remitente="ciudadano@gmail.com")

    assert ruta.se_radica


def test_sin_remitente_no_revienta():
    assert decidir(asunto="Solicitud", cuerpo="", correo_remitente="").se_radica


# ── Como se explica ──

def test_el_motivo_tiene_su_redaccion_interna_y_externa():
    from pipeline.notificacion import MOTIVO_PARA_EL_CORREO
    from pipeline.telemetria import MOTIVOS_MANUAL

    assert MOTIVO_REMITENTE_EXCLUIDO in MOTIVOS_MANUAL
    assert MOTIVO_REMITENTE_EXCLUIDO in MOTIVO_PARA_EL_CORREO


def test_la_redaccion_externa_no_nombra_el_buzon():
    """El aviso externo no publica direcciones internas."""
    from pipeline.notificacion import MOTIVO_PARA_EL_CORREO

    assert "@" not in MOTIVO_PARA_EL_CORREO[MOTIVO_REMITENTE_EXCLUIDO]
