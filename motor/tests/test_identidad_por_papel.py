"""
El afectado es la persona del tramite, no otra que el correo nombra con otro
papel: el representante que firma por su empresa, el medico que atendio, la
contratante del certificado, el trabajador de una lista. Tambien el tipo de
documento que el texto le da al numero, sin suponer cedula.

Todos los nombres, documentos y NIT son ficticios.
"""
import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from models.enums import TipoDocumento
from pipeline import compuerta
from pipeline.compuerta import Veredicto, informe, validar
from pipeline.extraccion import Hallazgo, extraer_tipo_solicitud
from pipeline.orquestador import Orquestador, Resultado
from utils.documento_candidatos import extraer_candidatos_documento
from utils.tipos_documento import longitud_valida, tramos_de_leyenda


def _h(campo, valor, confianza=0.9, evidencia="cuerpo del correo", metodo=""):
    return Hallazgo(campo, valor, evidencia, confianza, metodo=metodo)


def _base(**valores):
    datos = {
        "numero_documento_afectado": "41234567",
        "nombre_afectado": "PEDRO RUIZ MEJIA",
        "nombre_remitente": "Ana Lopez Rojas",
        "tipo_solicitud": 16,
        "tipo_remitente": 2,
        "tipo_documento_afectado": int(TipoDocumento.CC),
    }
    datos.update(valores)
    return {c: _h(c, v) for c, v in datos.items() if v is not None}


def _codigos(d):
    return {r.codigo for r in d.reparos}


# ── Tipo de documento ──

def test_el_ppt_de_siete_digitos_es_valido():
    assert longitud_valida("2133540", int(TipoDocumento.PPT))


def test_dos_casillas_seguidas_son_el_menu_del_formulario():
    """"X TI CE No." es el menu con la casilla de CC tachada, no una CE."""
    texto = "identificado con X TI CE No.\n7040374537"
    assert tramos_de_leyenda(texto)
    assert not tramos_de_leyenda("Marque CC o TI segun corresponda")
    candidatos = extraer_candidatos_documento(texto)
    assert [c["numero"] for c in candidatos] == ["7040374537"]
    assert candidatos[0]["tipo_pegado"] == ""


def test_pt_con_comas_de_miles_es_un_permiso_por_proteccion_temporal():
    c = extraer_candidatos_documento("PACIENTE: ANA LOPEZ IDENTIFICACION: PT 5,123,456 |")
    assert [(x["numero"], x["tipo_pegado"]) for x in c] == [("5123456", "PT")]
    assert extraer_candidatos_documento("valor total 5,123,456 pesos") == []


def test_sin_tipo_se_toma_el_que_rotula_el_numero():
    hallazgos = _base(numero_documento_afectado="5123456", tipo_documento_afectado=None)
    d = validar(hallazgos, fuentes="PACIENTE: PEDRO RUIZ MEJIA IDENTIFICACION: PT 5,123,456")
    assert d.correcciones.get("tipo_documento_afectado") == int(TipoDocumento.PPT), informe(d)
    assert d.puede_radicar


def test_sin_tipo_y_rotulado_como_cedula_va_como_cedula():
    hallazgos = _base(tipo_documento_afectado=None)
    d = validar(hallazgos, fuentes="PEDRO RUIZ MEJIA cedula 41234567")
    assert d.correcciones.get("tipo_documento_afectado") == int(TipoDocumento.CC)


def test_sin_tipo_no_se_inventa_la_cedula_si_el_correo_habla_de_otro():
    hallazgos = _base(tipo_documento_afectado=None)
    d = validar(hallazgos, fuentes="Soy venezolano. PEDRO RUIZ MEJIA 41234567")
    assert "TIPO_DE_DOCUMENTO_SIN_RESOLVER" in _codigos(d)
    assert not d.puede_radicar


def test_sin_tipo_ni_indicio_de_otro_se_queda_la_cedula_por_defecto():
    hallazgos = _base(tipo_documento_afectado=None)
    d = validar(hallazgos, fuentes="Solicito el pago. PEDRO RUIZ MEJIA 41234567")
    assert "TIPO_DE_DOCUMENTO_POR_DEFECTO" in _codigos(d)
    assert d.puede_radicar


def test_el_paciente_pegado_a_la_firma_del_medico_no_es_el_medico():
    """En el PDF las columnas se mezclan: el registro es de otro numero."""
    fuentes = ("Daniel Mora Gil ANA MARIA ROJAS DIAZ CC No. 1032000001 / RM "
               "1032000001 CC 45000001")
    d = validar(_base(nombre_afectado="ANA MARIA ROJAS DIAZ",
                      numero_documento_afectado="45000001"), fuentes=fuentes)
    assert "NOMBRE_DE_UN_PROFESIONAL" not in _codigos(d), informe(d)


@pytest.mark.parametrize("numero, radica", [("1119935863", True), ("4449935863", False)])
def test_la_tarjeta_de_identidad_empieza_por_uno(numero, radica):
    hallazgos = _base(numero_documento_afectado=numero,
                      tipo_documento_afectado=int(TipoDocumento.TI))
    d = validar(hallazgos)
    assert d.puede_radicar is radica, informe(d)


# ── Nombres que la compuerta no debe mutilar ──

def test_empresa_con_mayuscula_abre_la_razon_social():
    hallazgos = _base(nombre_afectado="Empresa Social del Estado ESE FICTICIA",
                      numero_documento_afectado="8070043523",
                      tipo_documento_afectado=int(TipoDocumento.NI))
    d = validar(hallazgos)
    assert "nombre_afectado" not in d.correcciones, informe(d)


def test_yo_delante_del_nombre_se_quita():
    d = validar(_base(nombre_remitente="Yo, MARIA FATIMA CANAL ROJAS"))
    assert d.correcciones.get("nombre_remitente") == "MARIA FATIMA CANAL ROJAS"


def test_un_trozo_del_asunto_no_se_copia_al_remitente():
    hallazgos = _base(nombre_afectado="SALVO DE RAMIRO ANDRES GIL", tipo_remitente=3,
                      nombre_remitente="servicios ficticios")
    hallazgos["numero_documento_remitente"] = _h(
        "numero_documento_remitente", "41234567", metodo="autoidentificacion_del_remitente")
    d = validar(hallazgos)
    assert "NOMBRE_CON_PALABRAS_DEL_TRAMITE" in _codigos(d)
    assert d.correcciones.get("nombre_remitente") != "SALVO DE RAMIRO ANDRES GIL"


# ── El papel con que el texto presenta al afectado ──

CARTA_DEL_REPRESENTANTE = (
    "Yo, PEDRO RUIZ MEJIA, identificado con cedula de ciudadania No. 41.234.567,\n"
    "actuando en calidad de Representante Legal de la empresa ACME FICTICIA\n"
    "S.A.S., identificada con NIT 901000001, solicito el estado de cuenta.")


def test_el_representante_legal_no_es_el_afectado():
    hallazgos = _base(nombre_remitente="PEDRO RUIZ MEJIA", tipo_remitente=2)
    d = validar(hallazgos, fuentes=CARTA_DEL_REPRESENTANTE)
    assert "AFECTADO_ES_EL_REPRESENTANTE" in _codigos(d), informe(d)
    assert not d.puede_radicar
    # El 2 no se cambia a 3 por llamarse igual que el afectado.
    assert "tipo_remitente" not in d.correcciones


def test_el_trabajador_con_su_cedula_no_es_un_representante():
    fuentes = ("el trabajador PEDRO RUIZ MEJIA identificado con C.C. 41234567 "
               "solicita el pago. Atentamente, Ana Lopez Rojas, Representante Legal")
    d = validar(_base(), fuentes=fuentes)
    assert "AFECTADO_ES_EL_REPRESENTANTE" not in _codigos(d), informe(d)
    assert d.puede_radicar


def test_el_medico_que_firma_no_es_el_paciente():
    fuentes = ("DATOS DEL PACIENTE Nombres: ANA MARIA Apellidos: ROJAS DIAZ "
               "No. de Identificacion: 45000001\nPaciente\nPEDRO RUIZ MEJIA\n"
               "CC No. 41234567 / RM 41234567\nImpresion realizada por: PEDRO RUIZ MEJIA")
    d = validar(_base(), fuentes=fuentes)
    assert "NOMBRE_DE_UN_PROFESIONAL" in _codigos(d), informe(d)
    assert {r.campo for r in d.bloqueantes} >= {"nombre_afectado",
                                               "numero_documento_afectado"}


def test_la_contratante_del_certificado_no_es_la_afiliada():
    fuentes = ("ana lopez rojas identificada con CC. 1000000001 registra la siguiente "
               "informacion. INFORMACION DEL CONTRATANTE Nombres y/o Razon "
               "PEDRO RUIZ MEJIA | Tipo y Numero de Documento | CC - 41234567")
    d = validar(_base(), fuentes=fuentes)
    assert "NOMBRE_CON_ROL_AJENO" in _codigos(d), informe(d)


def test_el_supervisor_del_contrato_tiene_otro_papel():
    fuentes = ("Contratista PEDRO RUIZ MEJIA. Supervisor Luis Gomez Diaz Tipo de "
               "documento Cedula de Ciudadania Numero de documento 41234567")
    d = validar(_base(), fuentes=fuentes)
    assert "DOCUMENTO_DE_OTRA_PERSONA_NOMBRADA" in _codigos(d), informe(d)


def test_quien_escribe_sobre_un_funcionario_no_es_el_afectado():
    principal = ("Novedad medica correspondiente al funcionario IJ. GOMEZ DIAZ "
                 "LUIS ALBERTO, adscrito al centro.")
    hallazgos = _base(nombre_remitente="PEDRO RUIZ MEJIA", tipo_remitente=3)
    d = validar(hallazgos, principal=principal)
    assert "EL_CORREO_ES_SOBRE_OTRA_PERSONA" in _codigos(d), informe(d)
    d = validar(_base(tipo_remitente=3, nombre_remitente="PEDRO RUIZ MEJIA"),
                principal="Envio la incapacidad de la colaboradora PEDRO RUIZ MEJIA")
    assert "EL_CORREO_ES_SOBRE_OTRA_PERSONA" not in _codigos(d)


# ── Varias personas ──

LISTA = ("Solicito el consolidado de los siguientes trabajadores\n"
         "PEDRO RUIZ MEJIA   41234567 LUIS ALBERTO GOMEZ DIAZ  3803911\n"
         "CARLOS ANDRES PINTO SOTO   8785111\n")


def test_una_lista_de_trabajadores_va_a_manual():
    d = validar(_base(), principal=LISTA)
    assert "VARIOS_AFECTADOS" in _codigos(d)
    assert d.motivo_manual == "DOCUMENTO_AMBIGUO"


def test_la_misma_persona_con_dos_cedulas_va_a_manual():
    principal = ("1. Pedro Ruiz Mejia c.c. 4.123456\nNo. Radicado FURAT 7000001\n"
                 "2. Pedro Ruiz Mejia c.c. 4.523.456\n")
    d = validar(_base(numero_documento_afectado="4123456"), principal=principal)
    assert d.motivo_manual == "DOCUMENTO_AMBIGUO", informe(d)


def test_adjuntos_de_varias_personas_van_a_manual():
    d = validar(_base(), nombres_adjuntos=["CC_41234567_hc.pdf", "CC_22000001_inc.pdf"])
    assert "VARIOS_AFECTADOS" in _codigos(d)


def test_el_apoderado_y_su_poderdante_no_son_varios_afectados():
    principal = ("Yo, ANA LOPEZ ROJAS, identificada con CC 52000001, apoderada de "
                 "PEDRO RUIZ MEJIA CC 41234567")
    d = validar(_base(nombre_remitente="ANA LOPEZ ROJAS", tipo_remitente=1),
                principal=principal)
    assert "VARIOS_AFECTADOS" not in _codigos(d), informe(d)


def test_una_hoja_de_vida_no_se_radica():
    d = validar(_base(tipo_solicitud=14), principal="hoja de vida profesional SST\nenvio")
    assert "NO_ES_UNA_PQRD" in _codigos(d)
    d = validar(_base(tipo_solicitud=16), principal="pago de incapacidad, adjunto hoja de vida")
    assert "NO_ES_UNA_PQRD" not in _codigos(d)


# ── El tramite propio de la empresa ──

def _email(asunto, cuerpo, nombre, correo):
    return EmailMessage(id="x", subject=asunto, body=EmailBody(content=cuerpo),
                        from_address=EmailAddress(name=nombre, address=correo),
                        received_datetime="2026-08-24T13:30:00Z")


def test_la_empresa_que_pide_su_estado_de_cuenta_es_la_afectada():
    email = _email("Solicitud de estado de cuenta", "Solicito el estado de cuenta",
                   "acme ficticia", "contabilidad@yahoo.com")
    hallazgos = _base(nombre_remitente="acme ficticia", tipo_remitente=3,
                      tipo_solicitud=8)
    hallazgos["numero_documento_remitente"] = _h("numero_documento_remitente", "41234567")
    res = Resultado(email_id="x")
    Orquestador._tramite_de_la_empresa(
        hallazgos, res, email, "Solicito el estado de cuenta", CARTA_DEL_REPRESENTANTE)
    assert hallazgos["numero_documento_afectado"].valor == "901000001"
    assert hallazgos["tipo_documento_afectado"].valor == int(TipoDocumento.NI)
    assert "ACME FICTICIA" in hallazgos["nombre_afectado"].valor
    assert hallazgos["tipo_remitente"].valor == 2
    assert hallazgos["nombre_remitente"].valor == "PEDRO RUIZ MEJIA"


def test_el_tramite_de_un_trabajador_no_pasa_a_la_empresa():
    email = _email("Pago de incapacidad", "Solicito el pago de mi incapacidad",
                   "PEDRO RUIZ MEJIA", "pedro@gmail.com")
    hallazgos = _base(nombre_remitente="PEDRO RUIZ MEJIA", tipo_remitente=3)
    res = Resultado(email_id="x")
    Orquestador._tramite_de_la_empresa(
        hallazgos, res, email, "Solicito el pago de mi incapacidad",
        "ACME FICTICIA S.A.S. NIT 901000001")
    assert hallazgos["numero_documento_afectado"].valor == "41234567"


def test_el_nit_de_otra_entidad_no_es_el_de_quien_escribe():
    assert compuerta.empresa_con_nit(
        "Atendido en CLINICA FICTICIA S.A.S. NIT 900000001", "Ana Lopez",
        "ana@gmail.com", "Ana Lopez") is None


# ── Tipo de solicitud ──

@pytest.mark.parametrize("asunto, esperado", [
    ("Conciliacion de glosas de la clinica", 28),
    ("Pago de indemnizacion por incapacidad permanente parcial", 16),
    ("Quiero saber si estoy activo o inactivo, tengo dictamen de la junta nacional "
     "de calificacion", 12),
])
def test_tramites_nombrados(asunto, esperado):
    h, _ = extraer_tipo_solicitud(asunto, "")
    assert h is not None and h.valor == esperado
