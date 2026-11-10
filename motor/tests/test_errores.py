"""Clasificacion de fallos y politica de reintentos."""
import pytest

from pipeline.errores import (
    CLASES,
    Bitacora,
    Severidad,
    clasificar,
    debe_reintentar,
    espera_para,
)


# ── 403 de Cloudflare frente a 403 de autorizacion ──

CUERPO_CLOUDFLARE = (
    "<!DOCTYPE html><html><head>"
    "<title>Attention Required! | Cloudflare</title></head>"
    "<body><div class='captcha-container'></div>"
    "<span>Cloudflare Ray ID: 8a1b2c3d</span></body></html>"
)


def test_un_403_con_pagina_de_cloudflare_es_reintentable():
    """El challenge es transitorio: rendirse al primer intento pierde la radicacion."""
    clase = clasificar(estado=403, cuerpo=CUERPO_CLOUDFLARE)
    assert clase.codigo == "CLOUDFLARE_CHALLENGE"
    assert clase.reintentable is True
    assert clase.max_intentos > 1
    assert clase.motivo_manual == "CLOUDFLARE_403"


def test_un_403_de_autorizacion_no_es_reintentable():
    """El mismo codigo HTTP, politica opuesta: reintentar no cambia un permiso."""
    clase = clasificar(estado=403, cuerpo='{"error":"forbidden: invalid scope"}')
    assert clase.codigo == "SIN_PERMISO"
    assert clase.reintentable is False


def test_el_cuerpo_manda_sobre_el_codigo_http():
    """Los dos 403 exigen politicas opuestas: el codigo HTTP no alcanza."""
    por_cuerpo = clasificar(estado=403, cuerpo=CUERPO_CLOUDFLARE)
    por_codigo = clasificar(estado=403, cuerpo="")
    assert por_cuerpo.codigo != por_codigo.codigo


@pytest.mark.parametrize("cuerpo", [
    "Attention Required! | Cloudflare",
    "cf-mitigated: challenge",
    "<!-- /.captcha-container -->",
    "Just a moment...",
])
def test_reconoce_las_variantes_del_challenge(cuerpo):
    assert clasificar(estado=403, cuerpo=cuerpo).codigo == "CLOUDFLARE_CHALLENGE"


# ── Error 522 ──

def test_el_522_de_cloudflare_es_origen_inalcanzable():
    """No es Cloudflare bloqueando sino el backend caido: exige mas espera."""
    cuerpo = ('{"status":522,"cloudflare_error":true,"detail":"Cloudflare could '
              'not establish a TCP connection to origin"}')
    clase = clasificar(estado=522, cuerpo=cuerpo)
    assert clase.codigo == "ORIGEN_INALCANZABLE"
    assert clase.reintentable is True
    assert clase.espera_base_s >= 10.0, "un origen caido necesita espera mayor"


# ── Fallos que degradan sin detener la radicacion ──

def test_redis_con_dns_caido_degrada_pero_queda_registrado():
    """La radicacion continua, pero la perdida de dedup queda registrada."""
    clase = clasificar(
        cuerpo="Error -2 connecting to redis-14842...redis-cloud.com:14842. "
               "Name or service not known."
    )
    assert clase.codigo == "DNS_NO_RESUELVE"
    assert clase.severidad is Severidad.DEGRADA


def test_tabla_con_esquema_desalineado_no_se_reintenta():
    """42703: la columna no existe. Reintentar no la crea, hay que migrar."""
    clase = clasificar(
        cuerpo="{'S': 'ERROR', 'C': '42703', 'M': 'column \"numero_documento_afectado\" "
               "of relation \"iapqrd_radicados_pqrd\" does not exist'}"
    )
    assert clase.codigo == "ESQUEMA_DESALINEADO"
    assert clase.reintentable is False
    assert clase.severidad is Severidad.DEGRADA


def test_fallo_transitorio_del_modelo_se_reintenta():
    clase = clasificar(
        cuerpo="Response payload is not completed: <TransferEncodingError: 400, "
               "message='Not enough data to satisfy transfer length header.'>"
    )
    assert clase.codigo == "MODELO_TRANSITORIO"
    assert clase.reintentable is True


@pytest.mark.parametrize("texto,codigo", [
    ("PDF protegido con contraseña de usuario", "ADJUNTO_PROTEGIDO"),
    ("the PDF file is password protected", "ADJUNTO_PROTEGIDO"),
    ("documento encrypted, no se pudo abrir", "ADJUNTO_PROTEGIDO"),
    ("TesseractNotFoundError: tesseract is not installed", "SIN_OCR"),
])
def test_clasifica_los_fallos_de_conversion(texto, codigo):
    assert clasificar(cuerpo=texto).codigo == codigo


@pytest.mark.parametrize("estado,cuerpo,esperado", [
    (401, '{"error_description":"Invalid user password"}', "TOKEN_EXPIRADO"),
    (403, '{"message":"password expired for user AsignadoIA"}', "SIN_PERMISO"),
    (500, "TLS handshake failed: encrypted alert", "ORIGEN_INALCANZABLE"),
    (400, "the password field is required", "PETICION_INVALIDA"),
    (500, "tesseract is not installed", "ORIGEN_INALCANZABLE"),
])
def test_los_fallos_de_conversion_no_se_buscan_en_una_respuesta_http(
        estado, cuerpo, esperado):
    """
    ADJUNTO_PROTEGIDO y SIN_OCR solo pueden originarse al convertir un archivo
    local. Sus palabras clave dentro de un cuerpo HTTP no cambian la clase.
    """
    assert clasificar(estado=estado, cuerpo=cuerpo).codigo == esperado


def test_un_401_es_reintentable_aunque_su_cuerpo_hable_de_contraseñas():
    c = clasificar(estado=401, cuerpo='{"error_description":"Invalid password"}')
    assert c.reintentable
    assert debe_reintentar(c, 1)


# ── Codigos HTTP ──

@pytest.mark.parametrize("estado,codigo", [
    (400, "PETICION_INVALIDA"),
    (401, "TOKEN_EXPIRADO"),
    (422, "PETICION_INVALIDA"),
    (429, "LIMITE_DE_TASA"),
    (502, "ORIGEN_INALCANZABLE"),
    (503, "ORIGEN_INALCANZABLE"),
])
def test_clasifica_por_codigo_http(estado, codigo):
    assert clasificar(estado=estado).codigo == codigo


def test_el_401_se_reintenta_sin_espera():
    """Renovar el token es inmediato: esperar solo alarga la corrida."""
    clase = clasificar(estado=401)
    assert clase.reintentable is True
    assert clase.espera_base_s == 0.0


def test_un_5xx_desconocido_se_trata_como_origen_inalcanzable():
    assert clasificar(estado=599).codigo == "ORIGEN_INALCANZABLE"


def test_un_fallo_sin_pistas_queda_como_desconocido():
    """No es reintentable: reintentar un fallo opaco puede duplicar una escritura."""
    clase = clasificar(excepcion=ValueError("algo raro"))
    assert clase.codigo == "DESCONOCIDO"
    assert clase.reintentable is False
    assert clase.motivo_manual == "ERROR_INESPERADO"


# ── Politica de reintentos ──

def test_la_espera_crece_exponencialmente():
    clase = clasificar(estado=403, cuerpo=CUERPO_CLOUDFLARE)
    e1, e2, e3 = (espera_para(clase, i) for i in (1, 2, 3))
    assert e1 < e2 < e3


def test_la_espera_esta_acotada():
    """El job tiene limite de tiempo: esperar de mas es peor que ir a manual."""
    clase = clasificar(estado=429)
    assert espera_para(clase, 10) <= 60.0


def test_no_se_espera_por_algo_que_no_se_reintenta():
    assert espera_para(clasificar(estado=403, cuerpo="forbidden"), 1) == 0.0


def test_debe_reintentar_respeta_el_maximo():
    clase = clasificar(estado=403, cuerpo=CUERPO_CLOUDFLARE)
    assert debe_reintentar(clase, 1) is True
    assert debe_reintentar(clase, clase.max_intentos) is False


def test_todas_las_clases_son_coherentes():
    """Una clase reintentable con un solo intento no tendria sentido."""
    for c in CLASES:
        if c.reintentable:
            assert c.max_intentos > 1, f"{c.codigo} es reintentable con 1 intento"
        assert c.descripcion, f"{c.codigo} no tiene descripcion"
        if c.severidad is Severidad.FATAL and not c.reintentable:
            assert c.motivo_manual, (
                f"{c.codigo} es fatal y no reintentable: necesita motivo manual"
            )


# ── Bitacora: ningun fallo en silencio ──

def test_la_bitacora_registra_y_clasifica():
    b = Bitacora()
    f = b.registrar("RADICACION", "fallo al radicar", estado=403,
                    cuerpo=CUERPO_CLOUDFLARE)
    assert f.clase.codigo == "CLOUDFLARE_CHALLENGE"
    assert len(b.fallos) == 1
    assert "CLOUDFLARE_CHALLENGE" in f.linea_log


def test_una_degradacion_no_impide_radicar_pero_queda_anotada():
    """Degradar es aceptable; degradar en silencio no."""
    b = Bitacora()
    b.registrar("REDIS", "no se guardo el hash",
                cuerpo="Error -2 ... Name or service not known")
    assert b.puede_radicar is True
    assert len(b.degradaciones) == 1
    assert b.avisos(), "la degradacion debe aparecer en el informe"


def test_un_fatal_impide_radicar_y_da_motivo_manual():
    b = Bitacora()
    b.registrar("RADICACION", "captcha", estado=403, cuerpo=CUERPO_CLOUDFLARE)
    assert b.puede_radicar is False
    assert b.motivo_manual() == "CLOUDFLARE_403"


def test_sin_fallos_no_hay_motivo_manual():
    b = Bitacora()
    assert b.puede_radicar is True
    assert b.motivo_manual() == ""
    assert b.resumen() == "sin fallos"


def test_el_resumen_cuenta_fatales_y_degradaciones():
    b = Bitacora()
    b.registrar("REDIS", "x", cuerpo="Name or service not known")
    b.registrar("RADICACION", "y", estado=403, cuerpo=CUERPO_CLOUDFLARE)
    assert "1 fatal" in b.resumen()
    assert "1 degradacion" in b.resumen()


# ── El codigo HTTP que viaja dentro del texto ──
#
# `services/radicacion.py` envuelve la respuesta en `Exception(f"Radicacion
# fallida: {status} - {texto}")`. El estado no llega como parametro, y sin
# leerlo del texto la tabla `_POR_ESTADO` no se consulta.

@pytest.mark.parametrize("mensaje", [
    "Radicacion fallida: 500 - <html><body>Internal Server Error</body></html>",
    "Radicacion fallida: 502 - Bad Gateway",
    "Radicacion fallida: 503 - Service Unavailable",
    "Radicacion fallida: 504 - Gateway Timeout",
    "Error de conexion: [Errno 111] Connection refused (intento 3/3)",
    "Radicacion fallida despues de varios intentos",
])
def test_una_caida_de_sgdea_se_aplaza_en_vez_de_ir_a_manual(mensaje):
    """
    Un correo que no se radico porque el backend estaba caido se reintenta en
    la siguiente corrida. Clasificarlo como ERROR_INESPERADO lo saca de la
    bandeja y ya nadie lo vuelve a tomar.
    """
    from pipeline.reintentos import MOTIVOS_QUE_SE_APLAZAN

    clase = clasificar(cuerpo=mensaje, excepcion=Exception(mensaje))
    assert clase.reintentable
    assert clase.motivo_manual in MOTIVOS_QUE_SE_APLAZAN


def test_un_payload_invalido_no_se_aplaza():
    """Un 400 no se arregla esperando: el payload seguira estando mal."""
    from pipeline.reintentos import MOTIVOS_QUE_SE_APLAZAN

    mensaje = "Radicacion fallida: 400 - numero de documento invalido"
    clase = clasificar(cuerpo=mensaje, excepcion=Exception(mensaje))
    assert not clase.reintentable
    assert clase.motivo_manual not in MOTIVOS_QUE_SE_APLAZAN


def test_el_cuerpo_sigue_mandando_sobre_el_codigo_del_texto():
    """Un 403 con la pagina de Cloudflare es transitorio, no de autorizacion."""
    mensaje = "Radicacion fallida: 403 - <html>Attention Required! | Cloudflare</html>"
    assert clasificar(cuerpo=mensaje).codigo == "CLOUDFLARE_CHALLENGE"


def test_un_numero_de_tres_cifras_suelto_no_se_toma_por_un_estado():
    """Sin una palabra que lo anuncie, un numero es un numero."""
    assert clasificar(cuerpo="el radicado 502 del ciudadano").codigo == "DESCONOCIDO"


# ── Un 500 no siempre significa que el backend este caido ──

CUERPO_MEDIA_TYPE = (
    'Radicacion fallida: 500 - {"status":500,'
    '"error":"Internal Server Error","message":"Error interno del servidor",'
    '"developerMessage":"HttpMediaTypeNotSupportedException: Content type '
    "'application/x-www-form-urlencoded' not supported\"}"
)


def test_el_rechazo_del_tipo_de_contenido_no_se_reintenta():
    """
    Llega como 500, que por codigo seria un backend caido y reintentable. La
    peticion sale mal formada de aqui: repetirla devuelve el mismo 500 y gasta
    los ciclos de aplazamiento sin que el correo pueda avanzar nunca.
    """
    clase = clasificar(cuerpo=CUERPO_MEDIA_TYPE)

    assert clase.codigo == "FORMATO_RECHAZADO"
    assert not clase.reintentable
    assert clase.motivo_manual == "PETICION_MAL_FORMADA"


def test_un_500_de_verdad_sigue_siendo_reintentable():
    """La firma anterior no puede tragarse los fallos reales del backend."""
    assert clasificar(cuerpo="Radicacion fallida: 502 - Bad Gateway").reintentable
    assert clasificar(cuerpo="Radicacion fallida: 500 - Internal Server Error").reintentable


# ── Una degradacion en el paso que fallo si nombra el motivo ──

def test_un_dns_que_no_resuelve_radicando_se_aplaza_como_el_backend_caido():
    """
    `DNS_NO_RESUELVE` degrada, asi que no habia ningun fatal con motivo y el
    correo salia con ERROR_INESPERADO, que no esta entre los que se aplazan:
    iba a una persona en vez de reintentarse. Un corte de resolucion de nombres
    no dura media hora.
    """
    from pipeline.reintentos import se_aplaza

    b = Bitacora()
    b.registrar("RADICACION", "no se pudo resolver el nombre del servicio",
                cuerpo="Temporary failure in name resolution")

    motivo = b.motivo_manual("RADICACION")
    assert motivo == "SGDEA_CAIDO"
    assert se_aplaza(motivo) is True


def test_una_degradacion_de_otro_paso_no_explica_la_radicacion():
    """
    Un adjunto ilegible de la conversion no impidio radicar. Reportarlo como la
    causa de que la radicacion no se completara es afirmar algo que no es.
    """
    b = Bitacora()
    b.registrar("CONVERSION", "paginas sin texto extraible",
                cuerpo="el OCR no encontro nada: paginas sin texto extraible")
    b.registrar("RADICACION", "401 - Token expirado", estado=401)

    assert b.motivo_manual("RADICACION") == ""


def test_sin_nombrar_el_paso_no_se_cae_a_ninguna_degradacion():
    """La caida es deliberada y hay que pedirla: si no, se elige por azar."""
    b = Bitacora()
    b.registrar("CONVERSION", "paginas sin texto extraible",
                cuerpo="el OCR no encontro nada: paginas sin texto extraible")

    assert b.motivo_manual() == ""
    assert b.motivo_manual("CONVERSION") == "ADJUNTO_ILEGIBLE"


def test_un_fatal_con_motivo_manda_sobre_cualquier_degradacion():
    b = Bitacora()
    b.registrar("RADICACION", "no se pudo resolver el nombre",
                cuerpo="Temporary failure in name resolution")
    b.registrar("RADICACION", "400 - payload invalido", estado=400)

    assert b.motivo_manual("RADICACION") == "VALIDACION_RECHAZO"
