"""
A donde va cada correo.

No todo lo que llega al buzon es una PQRD. Radicar una tutela como peticion la
mete en la cola equivocada y le pone un termino de respuesta que no le
corresponde.
"""
import pytest

from business_rules.clasificacion_remitente import (
    DESTINO_MEDICINA_LABORAL,
    DESTINO_NOTIFICACIONES_JUDICIALES,
)
from pipeline.enrutamiento import TOPE_DEL_CUERPO, Destino, decidir


# ── Lo judicial ──

@pytest.mark.parametrize("asunto", [
    "Accion de tutela 2026-00123",
    "Notificacion fallo de tutela",
    "Juzgado 15 Civil del Circuito",
    "Incidente de desacato",
    "Auto admisorio",
    "Tribunal Superior de Bogota",
])
def test_lo_judicial_va_a_notificaciones_judiciales(asunto):
    r = decidir(asunto=asunto, cuerpo="", correo_remitente="x@empresa.com")
    assert r.destino is Destino.JUDICIAL
    assert r.correo_destino == DESTINO_NOTIFICACIONES_JUDICIALES
    assert r.se_reenvia


def test_la_marca_judicial_tambien_cuenta_en_el_cuerpo():
    r = decidir(asunto="Remito documento",
                cuerpo="Adjunto la accion de tutela radicada ayer.")
    assert r.destino is Destino.JUDICIAL


def test_los_acentos_y_las_mayusculas_no_lo_esconden():
    r = decidir(asunto="ACCIÓN DE TUTELA — URGENTE")
    assert r.destino is Destino.JUDICIAL


def test_la_evidencia_dice_que_marca_lo_decidio():
    """Sin evidencia, un reenvio equivocado no se puede revisar."""
    r = decidir(asunto="Fallo de tutela 2026-00123")
    assert "tutela" in r.evidencia


# ── Medicina Laboral ──

def test_el_buzon_de_digitalizacion_va_a_medicina_laboral():
    r = decidir(asunto="Correspondencia", cuerpo="",
                correo_remitente="radicacion@e-entrega.co")
    assert r.destino is Destino.MEDICINA_LABORAL
    assert r.correo_destino == DESTINO_MEDICINA_LABORAL


@pytest.mark.parametrize("texto", [
    "Junta Regional de Calificacion",
    "Respuesta JRCI Cundinamarca",
    "concepto de la JNCI",
    "Junta Nacional de Calificacion de Invalidez",
])
def test_la_junta_de_calificacion_va_a_medicina_laboral(texto):
    r = decidir(asunto=texto, correo_remitente="persona@gmail.com")
    assert r.destino is Destino.MEDICINA_LABORAL


# ── Lo que se queda ──

def test_una_pqrd_corriente_se_radica():
    r = decidir(asunto="Solicitud de estado de cuenta",
                cuerpo="Solicito el estado de cuenta de mis aportes.",
                correo_remitente="persona@gmail.com")
    assert r.destino is Destino.RADICAR
    assert not r.se_reenvia
    assert not r.correo_destino


def test_enrutar_un_correo_vacio_no_revienta():
    assert decidir().destino is Destino.RADICAR


def test_una_palabra_que_contiene_tutela_no_dispara():
    """`tutelar` y `tutelaba` no son una tutela."""
    r = decidir(asunto="Solicito tutelar mis derechos como afiliado",
                cuerpo="El empleador tutelaba antes esta funcion.")
    assert r.destino is Destino.RADICAR


def test_lo_que_esta_muy_abajo_en_el_cuerpo_no_decide():
    """
    Mas alla del tope empiezan los hilos citados y los avisos legales, donde
    'juzgado' aparece en cualquier pie de pagina.
    """
    cuerpo = ("Solicito el pago de mi incapacidad.\n" * 200
              + "\nAviso legal: cualquier controversia se dirime ante el "
                "juzgado competente.")
    assert len(cuerpo) > TOPE_DEL_CUERPO
    assert decidir(asunto="Pago de incapacidad", cuerpo=cuerpo).destino \
        is Destino.RADICAR


# ── Cuando concurren dos ──

def test_lo_judicial_manda_sobre_medicina_laboral():
    """
    Una tutela sobre un dictamen sigue siendo una tutela, con su termino y su
    area, aunque hable de medicina laboral de principio a fin.
    """
    r = decidir(asunto="Accion de tutela contra el dictamen de la Junta Regional",
                cuerpo="", correo_remitente="radicacion@e-entrega.co")
    assert r.destino is Destino.JUDICIAL


# ── Entes de control ──
#
# Una comunicacion de un ente de control no es una PQRD: tiene su propio termino
# y su propia area. Radicarla como peticion la mete en la cola equivocada, que
# es lo que estaba generando vencimientos.

@pytest.mark.parametrize("correo", [
    "notificaciones@contraloria.gov.co",
    "quejas@procuraduria.gov.co",
    "denuncias@fiscalia.gov.co",
    "requerimientos@mintrabajo.gov.co",
    "vigilancia@supersalud.gov.co",
    "notificaciones@defensoria.gov.co",
    "juzgado01@ramajudicial.gov.co",
])
def test_lo_que_llega_de_un_ente_de_control_no_se_radica(correo):
    """
    Que el correo LLEGUE de un ente es la evidencia mas fuerte: no lo nombra,
    lo es.
    """
    r = decidir(asunto="Comunicacion oficial", cuerpo="Buen dia.",
                correo_remitente=correo)
    assert r.destino is Destino.JUDICIAL
    assert r.correo_destino == DESTINO_NOTIFICACIONES_JUDICIALES


@pytest.mark.parametrize("asunto", [
    "Requerimiento Contraloria General de la Republica",
    "PROCURADURIA - Solicitud de informacion",
    "Traslado Superintendencia Nacional de Salud",
    "Oficio Ministerio de Trabajo radicado 2026-001",
    "Comunicacion Defensoria del Pueblo",
])
def test_el_ente_en_el_asunto_decide(asunto):
    """Quien escribe lo pone en el asunto para decir de que va el correo."""
    r = decidir(asunto=asunto, cuerpo="", correo_remitente="x@empresa.com")
    assert r.destino is Destino.JUDICIAL


@pytest.mark.parametrize("texto", [
    "Se remite incidente de desacato del expediente 2026-00123.",
    "Notificacion de proceso ordinario laboral contra la entidad.",
])
def test_el_desacato_y_el_ordinario_laboral_son_judiciales_por_si_mismos(texto):
    r = decidir(asunto="Comunicacion", cuerpo=texto,
                correo_remitente="x@empresa.com")
    assert r.destino is Destino.JUDICIAL


def test_en_el_cuerpo_hace_falta_que_el_ente_REQUIERA():
    r = decidir(asunto="Comunicacion",
                cuerpo="Adjunto el requerimiento de la Contraloria General.",
                correo_remitente="x@empresa.com")
    assert r.destino is Destino.JUDICIAL


# ── Nombrar un ente NO basta ──
#
# Nombrar un ente aparece tambien en correos que SI son PQRD: el ciudadano
# adjunta su propia denuncia como prueba, el membrete de un banco dice "vigilada
# por la Superintendencia Financiera" y un FURAT cita al Ministerio de Trabajo
# en su encabezado.

@pytest.mark.parametrize("cuerpo", [
    "Adjunto la denuncia que puse ante la Fiscalia como soporte de mi caso.",
    "El banco es un establecimiento vigilado por la Superintendencia Financiera.",
    "El formato lleva el logo del Ministerio de Trabajo en el encabezado.",
    "Solicito copia de la sancion que me impusieron en el trabajo.",
    "Adjunto el fallo medico de mi incapacidad.",
])
def test_mencionar_un_ente_de_paso_no_saca_la_pqrd_del_flujo(cuerpo):
    r = decidir(asunto="Solicitud de incapacidad", cuerpo=cuerpo,
                correo_remitente="persona@gmail.com")
    assert r.destino is Destino.RADICAR, r.evidencia


def test_un_dominio_parecido_no_cuenta():
    """`contraloria-falsa.com` no es la Contraloria."""
    r = decidir(asunto="Solicitud", cuerpo="",
                correo_remitente="info@contraloria-falsa.com")
    assert r.destino is Destino.RADICAR


# ── El nombre del area acompaña al destino ──

def test_cada_destino_sabe_como_se_llama():
    """
    El nombre viaja con la decision. Si lo pusiera quien redacta el correo,
    tendria que volver a deducirlo del destino y podria equivocarse.
    """
    assert decidir(asunto="Accion de tutela").nombre_del_area == (
        "Notificaciones Judiciales")
    assert decidir(asunto="Junta Regional de Calificacion").nombre_del_area == (
        "Medicina Laboral")
    assert decidir(asunto="Pago de incapacidad").nombre_del_area == ""


# ── Nombrar una tutela no la hace un asunto de la Rama ──
#
# Notificaciones Judiciales devuelve los correos que no le corresponden. Lo que
# decide no es que el texto nombre la tutela, sino quien escribe y en calidad de
# que: un juzgado notificando, o un ciudadano pidiendole algo a la entidad.

@pytest.mark.parametrize("asunto, cuerpo", [
    ("Solicitud de cumplimiento de Fallo de Tutela",
     "Para su conocimiento y fines pertinentes."),
    ("SOLICITUD DE CUMPLIMIENTO INMEDIATO DE FALLO DE TUTELA",
     "Solicito se de cumplimiento a lo ordenado."),
    ("Queja: no me han cumplido el fallo",
     "Llevo tres meses esperando que cumplan la sentencia de tutela."),
    ("Reitero mi solicitud",
     "Reitero mi solicitud de cumplimiento del fallo de tutela."),
])
def test_pedir_que_se_cumpla_un_fallo_es_una_pqrd(asunto, cuerpo):
    """
    Quien pide que Positiva cumpla no esta notificando: le esta pidiendo algo a
    la entidad, y eso se radica.
    """
    r = decidir(asunto=asunto, cuerpo=cuerpo, correo_remitente="persona@gmail.com")
    assert r.destino is Destino.RADICAR, r.evidencia


@pytest.mark.parametrize("asunto, cuerpo, correo", [
    ("Notificacion accion de tutela 2026-00123",
     "El Juzgado 15 Civil del Circuito notifica la admision.",
     "j15@cendoj.ramajudicial.gov.co"),
    ("NOTIFICACION AUTO ADMISORIO DEMANDA ORDINARIA LABORAL",
     "Se notifica el auto admisorio de la demanda.",
     "apoderado@gmail.com"),
    ("Accion de tutela 2026-00123", "Adjunto la accion.",
     "juzgado01@ramajudicial.gov.co"),
    ("Incidente de desacato", "Se admite el incidente de desacato.",
     "persona@gmail.com"),
])
def test_la_rama_actuando_sigue_yendo_a_judicial(asunto, cuerpo, correo):
    """
    Exigir que el correo venga de un dominio judicial seria demasiado: un
    apoderado notifica un auto admisorio desde su correo personal.
    """
    r = decidir(asunto=asunto, cuerpo=cuerpo, correo_remitente=correo)
    assert r.destino is Destino.JUDICIAL, r.evidencia


def test_pedir_cumplimiento_desde_un_despacho_si_es_judicial():
    """Del dominio de la Rama no hay nada que interpretar."""
    r = decidir(asunto="Solicitud de cumplimiento de fallo de tutela",
                cuerpo="Se requiere a la entidad.",
                correo_remitente="juzgado01@ramajudicial.gov.co")
    assert r.destino is Destino.JUDICIAL


# ── Nombrar la Junta tampoco basta ──

@pytest.mark.parametrize("asunto, cuerpo", [
    ("Consulta estado de mi calificacion",
     "Quiero saber cuando sale el dictamen de la Junta Regional de mi caso."),
    ("Queja por demora",
     "Llevo ocho meses esperando que la Junta Regional me califique."),
    ("Solicitud de informacion",
     "Solicito copia del dictamen de la Junta Regional de mi proceso."),
])
def test_preguntar_por_lo_propio_ante_la_junta_es_una_pqrd(asunto, cuerpo):
    """
    Medicina Laboral no atiende ciudadanos: derivar la consulta la deja donde
    nadie la responde.
    """
    r = decidir(asunto=asunto, cuerpo=cuerpo, correo_remitente="persona@gmail.com")
    assert r.destino is Destino.RADICAR, r.evidencia


@pytest.mark.parametrize("asunto, cuerpo", [
    ("Citacion", "La Junta Regional convoca al afiliado a valoracion."),
    ("Determinacion de origen",
     "Se remite determinacion de origen del caso. Junta Regional."),
    ("Dictamen", "Dictamen emitido por la Junta Regional de Calificacion."),
])
def test_la_junta_actuando_si_va_a_medicina_laboral(asunto, cuerpo):
    r = decidir(asunto=asunto, cuerpo=cuerpo, correo_remitente="sec@juntavalle.com")
    assert r.destino is Destino.MEDICINA_LABORAL, r.evidencia


# ── Un derecho de peticion lo atiende Servicio al Cliente ──

@pytest.mark.parametrize("correo", [
    "hsuarezg@cendoj.ramajudicial.gov.co",
    "notificaciones@procuraduria.gov.co",
    "persona@gmail.com",
])
def test_un_derecho_de_peticion_no_se_deriva(correo):
    """
    Un juzgado tambien es empleador y escribe por asuntos administrativos: eso
    es una peticion, no una actuacion judicial.
    """
    r = decidir(asunto="RE: Derecho de peticion",
                cuerpo="Solicito informacion sobre la modalidad de trabajo.",
                correo_remitente=correo)
    assert r.destino is Destino.RADICAR, r.evidencia


def test_un_juzgado_que_notifica_manda_sobre_el_derecho_de_peticion():
    """Si en el mismo correo la Rama actua, la actuacion decide."""
    r = decidir(asunto="Derecho de peticion y notificacion",
                cuerpo="Se notifica el auto admisorio de la accion de tutela.",
                correo_remitente="j15@ramajudicial.gov.co")
    assert r.destino is Destino.JUDICIAL


# ── Nunca devolverle el correo al area que lo mando ──

@pytest.mark.parametrize("correo, asunto, cuerpo", [
    ("notificacionesjudiciales@positiva.gov.co", "RE: Oficio 013",
     "Se notifica el auto admisorio adjunto."),
    ("solicitudesmedicinalaboral@positiva.gov.co", "RV: Junta Regional",
     "La Junta Regional remite el dictamen."),
])
def test_un_area_no_recibe_de_vuelta_lo_que_ella_mando(correo, asunto, cuerpo):
    """
    Un area responde desde su buzon y ese correo vuelve a entrar aqui.
    Un area que escribe a este buzon esta entregando el caso, no notificando:
    devolverselo lo deja rebotando sin que nadie lo atienda.
    """
    r = decidir(asunto=asunto, cuerpo=cuerpo, correo_remitente=correo)
    assert r.destino is Destino.RADICAR
    assert not r.se_reenvia
    assert correo in r.evidencia



# ── El encabezado del hilo citado no decide a donde va el correo ──

ENCABEZADO_CITADO = (
    "De: Oscar Gonzalez <ogonzalezs@cndj.gov.co> "
    "Enviado: viernes, 4 de septiembre de 2026 8:48 a. m. "
    "Para: servicioalcliente@positiva.gov.co <servicioalcliente@positiva.gov.co>; "
    "Autorizaciones <autorizaciones@positiva.gov.co>; "
    "Gestion Preventiva Tutelas <gestionpreventiva.tutelas@positiva.gov.co>; "
    "ramajudicial <ramajudicial@positiva.gov.co>; "
    "lineaoro <lineaoro@positiva.gov.co> "
    "Asunto: Solicitud de autorizacion"
)


def test_un_buzon_del_encabezado_no_hace_judicial_al_correo():
    """
    La lista de copiados que arrastra un reenvio nombra areas, y una de ellas
    lleva la palabra en su nombre. Leerla convierte una peticion corriente en
    una notificacion judicial por a quien se le copio.
    """
    r = decidir(
        asunto="Solicito el CIE-10 y el diagnostico de las enfermedades",
        cuerpo="Buenos dias, solicito el documento. " + ENCABEZADO_CITADO,
        correo_remitente="ogonzalezs@cndj.gov.co",
    )

    assert not r.se_reenvia, r.evidencia


def test_una_lista_de_copiados_larga_tampoco():
    """El encabezado de un correo institucional pasa del millar de caracteres."""
    relleno = "; ".join(
        f"Area {i} <area{i}@positiva.gov.co>" for i in range(60))
    r = decidir(
        asunto="Solicitud de certificado",
        cuerpo=("Solicito un certificado. De: Alguien <a@b.gov.co> "
                f"Para: {relleno}; Gestion Tutelas <tutelas@positiva.gov.co> "
                "Asunto: otro"),
        correo_remitente="ciudadano@gmail.com",
    )

    assert not r.se_reenvia, r.evidencia


def test_el_texto_propio_sigue_decidiendo():
    """Quitar el encabezado no puede tapar lo que dice el correo de verdad."""
    r = decidir(
        asunto="Notificacion de auto admisorio",
        cuerpo="Se notifica el auto admisorio. " + ENCABEZADO_CITADO,
        correo_remitente="j01ejecc@cendoj.ramajudicial.gov.co",
    )

    assert r.se_reenvia
    assert r.destino is Destino.JUDICIAL
