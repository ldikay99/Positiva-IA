"""
Atribucion del documento de quien escribe.

Solo se resuelve cuando el texto lo permite sin adivinar: o el remitente y el
afectado son la misma persona, o hay un documento rotulado junto al nombre de
quien firma y lejos del nombre del afectado.
"""
import pytest

from pipeline.extraccion import (
    MAX_DISTANCIA_NOMBRE_DEL_REMITENTE,
    extraer_documento_del_remitente,
    extraer_todo,
)
from utils.documento_candidatos import extraer_candidatos_documento


def _numeros(texto):
    return [c["numero"] for c in extraer_candidatos_documento(texto)]


# ── Quien escribe declara su propio documento ──

@pytest.mark.parametrize("cuerpo,esperado", [
    ("Por medio del presente correo, yo, ANA CAMILA BASTO VELANDIA, mayor de "
     "edad, identificada con Cédula de Ciudadanía No. 4806359721, solicito",
     "4806359721"),
    ("Cordial saludo. Yo, Liliana Pechené Muelas, identificada con cédula de "
     "ciudadanía 59087933, me dirijo a ustedes", "59087933"),
    ("quien presentó la solicitud fui yo, Ángel José Ramírez, identificado "
     "con cédula de ciudadanía No. 9199635734.", "9199635734"),
    ("La suscrita, MARIA SOLIS, identificada con C.C. 41234567, manifiesta",
     "41234567"),
    ("Mi nombre es PEDRO RUIZ y estoy identificado con TI 1012345678",
     "1012345678"),
])
def test_la_autoidentificacion_resuelve_el_documento_del_remitente(
        cuerpo, esperado):
    """
    No depende del encabezado del correo, asi que sirve igual cuando el buzon
    no trae un nombre usable.
    """
    h, nr = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is not None, nr
    assert h.valor == esperado
    assert h.metodo == "autoidentificacion_del_remitente"


@pytest.mark.parametrize("cuerpo,esperado", [
    ("Mi nombre es Patricia Melendrez ahumada me identifico con la cedula "
     "28506532, quisiera saber como va mi proceso", "28506532"),
    ("Yo, ANA LOPEZ, me identifico con C.C. 41234567", "41234567"),
    ("La suscrita, ANA LOPEZ, identificandome con cedula 41234567, solicito",
     "41234567"),
])
def test_la_autoidentificacion_acepta_el_verbo_en_primera_persona(
        cuerpo, esperado):
    """"me identifico con" es la misma afirmacion que "identificado con", solo conjugada."""
    h, nr = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is not None, nr
    assert h.valor == esperado
    assert h.metodo == "autoidentificacion_del_remitente"


@pytest.mark.parametrize("cuerpo", [
    "Yo remito el caso de PEDRO SOLIS, quien se identifica con C.C. 76530545",
    "Mi nombre es ANA LOPEZ y adjunto lo del trabajador PEDRO SOLIS, "
    "que se identifica con cedula 76530545",
])
def test_la_tercera_persona_no_es_una_autoidentificacion(cuerpo):
    """
    "quien se identifica con" habla de otro: ese documento no es de quien
    escribe.
    """
    h, _ = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is None or h.metodo != "autoidentificacion_del_remitente"


@pytest.mark.parametrize("cuerpo", [
    "El trabajador PEDRO RUIZ identificado con C.C. 41234567 solicita",
    "Adjunto la cedula 41234567 del afiliado",
    "yo solicito una cita para el proximo mes",
    "identificado con cedula de ciudadania, sin numero visible",
])
def test_sin_autoidentificacion_no_se_toma_el_documento_de_otro(cuerpo):
    """
    La primera persona tiene que estar: "el trabajador X identificado con CC"
    habla de un tercero.
    """
    h, _ = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is None or h.metodo != "autoidentificacion_del_remitente"


def test_la_autoidentificacion_no_cruza_el_punto_de_otra_frase():
    """El limite evita que un "yo" lejano se enganche con cualquier documento."""
    cuerpo = ("Yo escribo por otro asunto. El trabajador PEDRO RUIZ "
              "identificado con C.C. 41234567 pide el pago.")
    h, _ = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is None or h.metodo != "autoidentificacion_del_remitente"


# ── El bloque de firma ──

@pytest.mark.parametrize("cuerpo,esperado", [
    ("Agradezco su atencion y colaboracion\nEduardo Andres Soto Cano "
     "C.C 20615944 Celular 3107279270", "20615944"),
    ("Mil gracias\nAndres Callamand\ncc 95821926", "95821926"),
    ("Se despide atentamente Ramiro Augusto Duque Guarin cedula 95533085",
     "95533085"),
    ("Atentamente: ANGY YULYED GOMEZ MORA C.C. 1389129062", "1389129062"),
    ("Att Johan Mauricio Guerra Mahecha Cedula 41234567", "41234567"),
])
def test_la_firma_resuelve_el_documento_del_remitente(cuerpo, esperado):
    h, nr = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is not None, nr
    assert h.valor == esperado
    assert h.metodo == "documento_de_la_firma"


@pytest.mark.parametrize("cuerpo", [
    "Atentamente, radicado en nombre del señor PEDRO SOLIS MORA, C.C. 76530545",
    "Cordialmente, a nombre de PEDRO SOLIS MORA con cedula 76530545",
    "Agradezco el reporte del trabajador PEDRO SOLIS MORA CC 76530545",
    "Cordialmente, comision al funcionario PEDRO SOLIS MORA "
    "identificado con cc 76530545",
    "Atentamente, se autoriza a la colaboradora ANA LOPEZ RUIZ CC 41234567",
    "Agradezco lo del siniestrado PEDRO SOLIS MORA con cedula 76530545",
    "Atentamente, el caso del asegurado PEDRO SOLIS MORA CC 76530545",
    "Atentamente adjunto lo de la afiliada ANA LOPEZ RUIZ C.C. 41234567",
    "Cordialmente, en nombre de la paciente ANA LOPEZ RUIZ cedula 41234567",
])
def test_quien_firma_puede_estar_hablando_de_otro(cuerpo):
    """
    "en nombre del señor X, C.C. NNN" y "del trabajador X CC NNN" nombran al
    afectado. Tomarlo como documento de quien escribe lo registra en SGDEA con
    la identidad de otra persona.
    """
    h, _ = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is None or h.metodo != "documento_de_la_firma", \
        f"atribuyo {h.valor if h else ''} a quien firma"


def test_la_guarda_de_tercera_persona_cubre_todas_las_etiquetas_del_afectado():
    """Toda palabra que presenta al afectado bloquea la atribucion a quien firma."""
    from pipeline.extraccion import (
        _ETIQUETAS_AFECTADO,
        _PRIMERA_PERSONA_EN_ETIQUETAS,
        _regex_en_nombre_de_otro,
    )

    guarda = _regex_en_nombre_de_otro()
    terceros = [e for e in _ETIQUETAS_AFECTADO
                if e not in _PRIMERA_PERSONA_EN_ETIQUETAS]
    assert terceros
    for etiqueta in terceros:
        assert etiqueta in guarda.pattern, etiqueta


def test_la_primera_persona_no_bloquea_la_atribucion():
    """
    "yo" y "mi nombre es" presentan a quien escribe: si bloquearan, la firma de
    la propia persona dejaria de contar.
    """
    from pipeline.extraccion import _regex_en_nombre_de_otro

    guarda = _regex_en_nombre_de_otro()
    assert not guarda.search("cordialmente, yo ")
    assert not guarda.search("mi nombre es ")


def test_un_saludo_de_apertura_no_delimita_una_firma():
    """
    "Cordial saludo" abre el correo: lo que sigue es el relato del caso, no la
    firma de quien escribe.
    """
    cuerpo = ("Cordial saludo. Adjunto el reporte del trabajador "
              "PEDRO SOLIS MORA con C.C. 76530545 para su tramite.")
    h, _ = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is None or h.metodo != "documento_de_la_firma"


def test_una_firma_de_verdad_sigue_valiendo_aunque_antes_se_nombre_a_otro():
    cuerpo = ("Se radica en nombre del señor PEDRO SOLIS MORA, C.C. 76530545.\n"
              "Atentamente, ANA LOPEZ RUIZ C.C. 41234567")
    h, nr = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is not None, nr
    assert h.valor == "41234567"


def test_dos_firmas_con_documentos_distintos_se_escalan():
    """
    Un reenvio arrastra la firma de otra persona. Elegir entre las dos seria
    adivinar quien de ellas escribio este correo.
    """
    cuerpo = (
        "Cordialmente, ANA LOPEZ RUIZ C.C. 41234567\n"
        "---------- Forwarded message ---------\n"
        "Atentamente, PEDRO SOLIS MORA C.C. 76530545"
    )
    h, nr = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is None or h.metodo != "documento_de_la_firma"


def test_dos_firmas_con_el_mismo_documento_si_deciden():
    cuerpo = ("Cordialmente, ANA LOPEZ RUIZ C.C. 41234567\n"
              "Atentamente, ANA LOPEZ RUIZ cedula 41.234.567")
    h, _ = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is not None and h.valor == "41234567"


def test_un_documento_lejos_del_cierre_no_es_la_firma():
    relleno = "detalle del tramite " * 12
    cuerpo = f"Cordialmente,\n{relleno}\nPEDRO SOLIS MORA C.C. 76530545"
    h, _ = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h is None or h.metodo != "documento_de_la_firma"


def test_la_autoidentificacion_gana_a_la_firma():
    """
    "yo, NOMBRE, identificado con" lo dice la persona sobre si misma; la firma
    puede ser de quien tramita en su nombre.
    """
    cuerpo = ("Yo, ANA LOPEZ RUIZ, identificada con cedula 41234567, solicito.\n"
              "Cordialmente, PEDRO SOLIS MORA C.C. 76530545")
    h, _ = extraer_documento_del_remitente(
        cuerpo, nombre_remitente="", nombre_afectado="", documento_afectado="")
    assert h.valor == "41234567"
    assert h.metodo == "autoidentificacion_del_remitente"


# ── El remitente es el afectado ──

def test_si_los_nombres_coinciden_el_documento_es_el_mismo():
    h, nr = extraer_documento_del_remitente(
        "El trabajador PEDRO RUIZ MEJIA con C.C. 41234567 solicita el pago.",
        nombre_remitente="PEDRO RUIZ MEJIA",
        nombre_afectado="PEDRO RUIZ MEJIA",
        documento_afectado="41234567",
    )
    assert h is not None and nr is None
    assert h.valor == "41234567"
    assert h.metodo == "remitente_es_el_afectado"


@pytest.mark.parametrize("remitente,afectado", [
    ("Pedro Ruiz Mejia", "PEDRO RUIZ MEJIA"),
    ("PEDRO  RUIZ   MEJIA", "PEDRO RUIZ MEJIA"),
    ("Pedro Ruíz Mejía", "PEDRO RUIZ MEJIA"),
])
def test_los_nombres_se_comparan_sin_tildes_ni_espacios_de_mas(remitente, afectado):
    """Mismo criterio que usa la compuerta para decidir que son la misma persona."""
    h, _ = extraer_documento_del_remitente(
        "texto", nombre_remitente=remitente, nombre_afectado=afectado,
        documento_afectado="41234567")
    assert h is not None and h.valor == "41234567"


def test_misma_persona_sin_documento_del_afectado_se_escala():
    h, nr = extraer_documento_del_remitente(
        "Solicito informacion", nombre_remitente="PEDRO RUIZ",
        nombre_afectado="PEDRO RUIZ", documento_afectado="")
    assert h is None
    assert nr.motivo == "MISMA_PERSONA_SIN_DOCUMENTO"


def test_la_voz_propia_tambien_hereda_el_documento_del_afectado():
    """El anonimizador sustituye el display name y deja el del cuerpo, asi que los nombres pueden diferir siendo la misma persona."""
    h, nr = extraer_documento_del_remitente(
        "Solicito el pago de mi incapacidad.",
        nombre_remitente="Nicolas Escalante",
        nombre_afectado="Victor Alfonso Martinez",
        documento_afectado="1366235159",
        voz_propia="mi incapacidad",
    )
    assert h is not None, nr.motivo if nr else ""
    assert h.valor == "1366235159"
    assert h.metodo == "el_tramite_es_del_remitente"
    assert "mi incapacidad" in h.evidencia


def test_la_voz_propia_no_inventa_un_documento_que_no_hay():
    h, nr = extraer_documento_del_remitente(
        "Solicito el pago de mi incapacidad.",
        nombre_remitente="Nicolas Escalante", nombre_afectado="",
        documento_afectado="", voz_propia="mi incapacidad")
    assert h is None
    assert nr.motivo


# ── Documento rotulado junto a la firma ──

def test_un_documento_rotulado_junto_al_remitente_se_le_atribuye():
    """El bloque de firma es la via preferente: el cierre lo delimita."""
    texto = (
        "Solicito el pago de la incapacidad de la trabajadora GILMA GOMEZ MINA "
        "identificada con C.C. 46414257.\n\n"
        "Cordialmente, SANTIAGO IDARRAGA c.c. 76530545, encargado de nomina."
    )
    h, nr = extraer_documento_del_remitente(
        texto, nombre_remitente="SANTIAGO IDARRAGA",
        nombre_afectado="GILMA GOMEZ MINA", documento_afectado="46414257")
    assert h is not None, nr
    assert h.valor == "76530545"
    assert h.metodo == "documento_de_la_firma"


def test_sin_formula_de_cierre_decide_la_cercania_al_nombre():
    """Sin "cordialmente" no hay firma que delimitar: queda la cercania."""
    texto = (
        "Reporte de la trabajadora GILMA GOMEZ MINA identificada con "
        "C.C. 46414257.\nSANTIAGO IDARRAGA c.c. 76530545, encargado de nomina."
    )
    h, nr = extraer_documento_del_remitente(
        texto, nombre_remitente="SANTIAGO IDARRAGA",
        nombre_afectado="GILMA GOMEZ MINA", documento_afectado="46414257")
    assert h is not None, nr
    assert h.valor == "76530545"
    assert h.metodo == "documento_rotulado_junto_al_remitente"


def test_no_se_atribuye_un_numero_sin_rotulo():
    """
    En una tabla, el numero mas cercano al nombre es la celda de al lado. El
    rotulo pegado es lo que separa una cedula de una cifra cualquiera.
    """
    texto = ("| ANA LOPEZ RUIZ | 774602 | 28 | $3.601.174 |\n"
             "| BEATRIZ SOLIS MORA | 812345 | 30 | $1.750.905 |")
    h, nr = extraer_documento_del_remitente(
        texto, nombre_remitente="ANA LOPEZ RUIZ",
        nombre_afectado="BEATRIZ SOLIS MORA", documento_afectado="")
    assert h is None
    assert nr.motivo in ("SIN_DOCUMENTO_MARCADO_DEL_REMITENTE",
                         "SIN_DOCUMENTO_APARTE_DEL_AFECTADO")


def test_un_documento_lejos_de_la_firma_no_se_atribuye():
    relleno = "detalle del tramite " * 12
    texto = (f"ANA LOPEZ RUIZ escribe.\n{relleno}\nC.C. 76530545 del expediente")
    h, nr = extraer_documento_del_remitente(
        texto, nombre_remitente="ANA LOPEZ RUIZ",
        nombre_afectado="BEATRIZ SOLIS MORA", documento_afectado="")
    assert h is None
    assert nr.motivo in ("DOCUMENTO_LEJOS_DEL_REMITENTE", "REQUIERE_ATRIBUCION")


def test_sin_nombre_del_remitente_no_se_atribuye_nada():
    h, nr = extraer_documento_del_remitente(
        "C.C. 76530545", nombre_remitente="", nombre_afectado="",
        documento_afectado="")
    assert h is None
    assert nr.motivo == "SIN_NOMBRE_DEL_REMITENTE"


def test_si_solo_esta_el_documento_del_afectado_se_dice_asi():
    h, nr = extraer_documento_del_remitente(
        "La trabajadora BEATRIZ SOLIS MORA con C.C. 46414257.",
        nombre_remitente="ANA LOPEZ RUIZ",
        nombre_afectado="BEATRIZ SOLIS MORA", documento_afectado="46414257")
    assert h is None
    assert nr.motivo == "SIN_DOCUMENTO_APARTE_DEL_AFECTADO"


# ── Un nombre de pila compartido no identifica a nadie ──

def test_una_sola_palabra_del_nombre_no_ancla_la_cercania():
    """
    En una planilla de aportes el nombre de pila de quien firma aparece como
    segundo nombre de otra persona. Anclar en esa unica palabra le atribuye la
    cedula de un tercero.
    """
    texto = (
        "| 16 | CC | 5928555102 | LUNA CASTRO LILIANA MARCELA | 28 | $3.601.174 |"
    )
    h, nr = extraer_documento_del_remitente(
        texto, nombre_remitente="ULISES MARCELA CARVAJAL",
        nombre_afectado="DAYNER DAVID", documento_afectado="")
    assert h is None, f"atribuyo {h.valor if h else ''} por compartir un nombre"


def test_dos_palabras_del_nombre_si_anclan():
    texto = "Cordialmente, LILIANA MARCELA LUNA CASTRO, C.C. 41234567"
    h, _ = extraer_documento_del_remitente(
        texto, nombre_remitente="LILIANA MARCELA LUNA CASTRO",
        nombre_afectado="DAYNER DAVID SOLIS", documento_afectado="")
    assert h is not None
    assert h.valor == "41234567"


# ── Los digitos de un correo o una URL no son un documento ──

@pytest.mark.parametrize("texto,intruso", [
    ("Escribir a 456086@corp-tres.co para el tramite", "456086"),
    ("Notificaciones: contacto.7654321@empresa.com", "7654321"),
    ("Ver https://portal.empresa.com/tramite/9087654 para el estado", "9087654"),
    ("Descargue en www.empresa.com/doc/4561237", "4561237"),
])
def test_un_numero_dentro_de_un_correo_o_url_no_es_candidato(texto, intruso):
    assert intruso not in _numeros(texto)


def test_el_documento_real_sigue_saliendo_aunque_haya_correos():
    texto = ("Trabajador PEDRO RUIZ con C.C. 41234567. "
             "Respuesta a notificaciones.456086@corp-tres.co")
    numeros = _numeros(texto)
    assert "41234567" in numeros
    assert "456086" not in numeros


# ── Enganche con la extraccion completa ──

def test_el_campo_deja_de_ser_huerfano():
    """El campo se resuelve cuando el texto lo permite, en vez de quedar siempre pendiente."""
    e = extraer_todo(
        asunto="Pago de incapacidad",
        cuerpo=("El trabajador PEDRO RUIZ MEJIA identificado con C.C. 41234567 "
                "solicita el pago.\nCordialmente, PEDRO RUIZ MEJIA"),
        from_name="PEDRO RUIZ MEJIA",
        from_address="pedro@empresa-uno.com",
    )
    assert "numero_documento_remitente" in e.hallazgos
    assert e.hallazgos["numero_documento_remitente"].valor == "41234567"


def test_el_tipo_del_remitente_se_sigue_de_su_documento():
    e = extraer_todo(
        asunto="Pago de incapacidad",
        cuerpo=("El trabajador PEDRO RUIZ MEJIA identificado con C.C. 41234567 "
                "solicita el pago.\nCordialmente, PEDRO RUIZ MEJIA"),
        from_name="PEDRO RUIZ MEJIA",
        from_address="pedro@empresa-uno.com",
    )
    assert "tipo_documento_remitente" in e.hallazgos


def test_cuando_no_se_resuelve_sigue_apareciendo_como_pendiente():
    e = extraer_todo(
        asunto="Solicitud",
        cuerpo="Buen dia, adjunto los soportes solicitados.",
        from_name="Ana Lopez",
        from_address="ana@empresa-uno.com",
    )
    assert "numero_documento_remitente" in e.no_resueltos
    assert "tipo_documento_remitente" in e.no_resueltos
    assert e.no_resueltos["numero_documento_remitente"].motivo


def test_la_distancia_maxima_del_remitente_es_mas_estrecha():
    from pipeline.extraccion import MAX_DISTANCIA_NOMBRE_DOC

    assert MAX_DISTANCIA_NOMBRE_DEL_REMITENTE < MAX_DISTANCIA_NOMBRE_DOC
