"""
Pruebas de los extractores deterministas.

Lo que se valida aqui decide el costo del sistema: cada campo que el algoritmo
resuelve bien es una llamada al modelo que no se paga. Pero resolver mal es peor
que no resolver, asi que se prueba con el mismo rigor la abstencion: cuando el
dato es ambiguo, el extractor DEBE escalar con el motivo en lugar de adivinar.
"""
import pytest

from models.enums import TipoDocumento
from pipeline.extraccion import (
    UMBRAL_CONFIANZA,
    extraer_documento,
    extraer_nombre_afectado,
    extraer_nombre_remitente,
    extraer_oficina,
    extraer_sexo,
    extraer_telefono,
    extraer_tipo_documento,
    extraer_todo,
)


# ── Contrato: todo hallazgo lleva evidencia y confianza ──

def test_todo_hallazgo_trae_evidencia_y_confianza():
    """Sin procedencia no hay auditoria; sin confianza no se puede decidir escalar."""
    h, _ = extraer_documento("El trabajador con CC 41234567 solicita")
    assert h is not None
    assert h.evidencia, "la evidencia es obligatoria"
    assert 0.0 <= h.confianza <= 1.0
    assert h.metodo, "el metodo permite saber que capa lo resolvio"


def test_todo_no_resuelto_trae_motivo():
    """Un None sin motivo no le sirve ni a la IA ni a la revision manual."""
    h, nr = extraer_documento("Solicito informacion general sin numeros")
    assert h is None
    assert nr is not None
    assert nr.motivo
    assert nr.detalle


# ── Documento ──

def test_resuelve_un_documento_unico_y_marcado():
    h, _ = extraer_documento("Incapacidad del trabajador con CC 41234567")
    assert h.valor == "41234567"
    assert h.confianza >= 0.9


def test_se_abstiene_con_varios_candidatos_ambiguos():
    """
    Adivinar aqui es lo que produce cedulas equivocadas. Preferir el primero, o
    el mas largo, seria inventar un criterio que nadie definio.
    """
    h, nr = extraer_documento(
        "Radicado 20260012345 del trabajador 41234567 en la empresa 900123456"
    )
    assert h is None
    assert nr.motivo == "VARIOS_CANDIDATOS_AMBIGUOS"
    assert "candidatos" in nr.detalle


def test_desambigua_cuando_solo_uno_tiene_marca():
    h, nr = extraer_documento(
        "Factura numero 7788991 del trabajador identificado con CC 41234567"
    )
    if h is not None:
        assert h.valor == "41234567"
        assert h.metodo in ("unica_marca_explicita", "candidato_unico")
    else:
        # Si el clasificador ve marca en ambos, abstenerse tambien es correcto
        assert nr.motivo == "VARIOS_CANDIDATOS_AMBIGUOS"


def test_elige_el_documento_mas_cercano_al_nombre_del_afectado():
    """
    Con varios documentos marcados gana el que esta junto al nombre. Es el caso
    normal: la empresa escribe con su NIT por un trabajador con su CC.
    """
    texto = (
        "AGREMPRESARIAL SAS con NIT 900123456 solicita el pago de la "
        "incapacidad del trabajador PEDRO RUIZ MORA con CC 41234567. "
        "Radicado anterior 20260012345."
    )
    h, nr = extraer_documento(texto, nombre_afectado="PEDRO RUIZ MORA")
    assert h is not None, f"deberia desambiguar: {nr.motivo if nr else ''}"
    assert h.valor == "41234567"
    assert h.metodo == "cercania_al_nombre"


def test_se_abstiene_si_dos_documentos_estan_igual_de_cerca():
    """Elegir entre dos empatados seria adivinar."""
    texto = "CC 41234567 PEDRO RUIZ y CC 80111222 PEDRO RUIZ"
    h, nr = extraer_documento(texto, nombre_afectado="PEDRO RUIZ")
    assert h is None
    assert nr.motivo == "VARIOS_CANDIDATOS_AMBIGUOS"


def test_se_abstiene_si_el_nombre_esta_lejos_de_todo_documento():
    """Mas alla de cierta distancia la cercania deja de ser evidencia."""
    texto = (
        "PEDRO RUIZ MORA presenta la siguiente solicitud.\n"
        + "Texto de relleno. " * 40
        + "NIT 900123456 y CC 41234567 de la empresa."
    )
    h, nr = extraer_documento(texto, nombre_afectado="PEDRO RUIZ MORA")
    assert h is None or h.metodo != "cercania_al_nombre"


def test_distingue_documento_ausente_de_adjunto_ilegible():
    """Cada causa da su propio motivo y el ilegible nombra el archivo."""
    _, sin_nada = extraer_documento("Solicito informacion general")
    assert sin_nada.motivo == "SIN_NINGUN_NUMERO_EN_EL_TEXTO"

    _, con_adjunto = extraer_documento(
        "Solicito informacion general",
        adjuntos_ilegibles=["cedula-escaneada.jpg"],
    )
    assert con_adjunto.motivo == "DOCUMENTO_EN_ADJUNTO_ILEGIBLE"
    assert "cedula-escaneada.jpg" in con_adjunto.detalle


def test_el_motivo_del_extractor_llega_a_la_taxonomia_manual():
    from pipeline.compuerta import validar

    d = validar({}, motivos_no_resueltos={
        "numero_documento_afectado": "DOCUMENTO_EN_ADJUNTO_ILEGIBLE",
    })
    assert d.motivo_manual == "ADJUNTO_ILEGIBLE"

    d2 = validar({}, motivos_no_resueltos={
        "numero_documento_afectado": "VARIOS_CANDIDATOS_AMBIGUOS",
    })
    assert d2.motivo_manual == "DOCUMENTO_AMBIGUO"


def test_sin_ningun_numero_en_el_texto_lo_dice():
    """
    No hay nada que hallar: ni el algoritmo ni el modelo, que lee el mismo
    texto. Es distinto de que haya numeros y todos queden descartados.
    """
    h, nr = extraer_documento("Solicito una cita para el proximo mes")
    assert h is None
    assert nr.motivo == "SIN_NINGUN_NUMERO_EN_EL_TEXTO"


@pytest.mark.parametrize("texto", [
    "Escribeme a juan.perez41234567@empresa-uno.com para coordinar",
    "El detalle esta en https://portal.positiva.gov.co/casos/20260001357146",
    "Adjunto el radicado ENT20260001306725 para su revision",
])
def test_con_numeros_descartados_el_motivo_es_otro(texto):
    """
    Aqui SI hay numeros y el contexto los descarto. El modelo puede juzgarlos de
    otra forma, asi que el campo se le sigue pidiendo.
    """
    h, nr = extraer_documento(texto)
    assert h is None
    assert nr.motivo == "SIN_CANDIDATOS_DE_DOCUMENTO"
    assert "descartados por su contexto" in nr.detalle


def test_solo_la_ausencia_total_se_deja_de_preguntar():
    from pipeline.extraccion import MOTIVOS_DE_AUSENCIA_CONSTATADA

    assert "SIN_NINGUN_NUMERO_EN_EL_TEXTO" in MOTIVOS_DE_AUSENCIA_CONSTATADA
    assert "SIN_CANDIDATOS_DE_DOCUMENTO" not in MOTIVOS_DE_AUSENCIA_CONSTATADA


def test_los_dos_motivos_van_a_la_misma_taxonomia_manual():
    """Para el operador son lo mismo: el correo no trae el documento."""
    from pipeline.compuerta import validar

    for causa in ("SIN_NINGUN_NUMERO_EN_EL_TEXTO",
                  "SIN_CANDIDATOS_DE_DOCUMENTO"):
        d = validar({}, motivos_no_resueltos={
            "numero_documento_afectado": causa})
        assert d.motivo_manual == "SIN_DOCUMENTO", causa


# ── Lo que no esta en el texto no se le pregunta al modelo ──

def _sin_telefono():
    from pipeline.extraccion import extraer_todo

    return extraer_todo(
        asunto="Solicitud de certificado",
        cuerpo="Buenos dias, solicito el certificado de afiliacion. Gracias.",
        from_name="Ana Lopez", from_address="ana@empresa.com",
    )


def test_un_campo_de_ausencia_constatada_no_entra_en_la_tarea():
    """
    El algoritmo barrio el texto buscando forma de telefono y no habia
    ninguna. El modelo lee el mismo texto: pedirselo gasta una tarea para
    recibir la misma respuesta.
    """
    e = _sin_telefono()
    assert e.no_resueltos["telefono_afectado"].motivo == "SIN_TELEFONO"
    assert "telefono_afectado" in e.campos_pendientes
    assert "telefono_afectado" not in e.campos_a_buscar
    assert "telefono_afectado" in e.campos_sin_nada_que_buscar


def test_la_ausencia_se_informa_pero_no_se_pide():
    """Callarla seria peor: el motivo es lo que explica el campo vacio."""
    resumen = _sin_telefono().resumen_para_ia()
    antes, _, despues = resumen.partition(
        "Constatado que el correo no lo trae")
    assert despues, resumen
    assert "telefono_afectado" in despues
    assert "telefono_afectado" not in antes


def test_un_campo_pendiente_normal_si_entra_en_la_tarea():
    e = _sin_telefono()
    assert "tipo_solicitud" in e.campos_a_buscar
    assert "tipo_solicitud" in e.resumen_para_ia()


def test_sin_nada_que_buscar_no_se_llama_al_modelo():
    """
    Quedan campos pendientes, pero ninguno que el modelo pueda resolver: la
    llamada seria gasto puro.
    """
    from pipeline.agente import resolver
    from pipeline.extraccion import Extraccion, NoResuelto

    class ModeloQueNoDebeUsarse:
        def generar(self, *a, **k):
            raise AssertionError("no se debia llamar al modelo")

    e = Extraccion(
        hallazgos={},
        no_resueltos={"telefono_afectado": NoResuelto(
            "telefono_afectado", "SIN_TELEFONO", "no hay numero con forma de telefono")},
    )
    r = resolver(ModeloQueNoDebeUsarse(), None, e)
    assert r.iteraciones == 0
    assert "telefono_afectado" in r.sin_resolver


# ── Tipo de documento: tabla de mapeo, no criterio ──

@pytest.mark.parametrize("texto,esperado", [
    ("Empresa con NIT 900123456", TipoDocumento.NI),
    ("Cedula de extranjeria numero 123456", TipoDocumento.CE),
    ("Tarjeta de identidad del menor", TipoDocumento.TI),
    ("Adjunto pasaporte del afiliado", TipoDocumento.PA),
    ("Registro civil de nacimiento", TipoDocumento.NP),
    ("Permiso por proteccion temporal PPT 8123456", TipoDocumento.PPT),
    ("Permiso especial de permanencia", TipoDocumento.PE),
    ("Carne diplomatico del titular", TipoDocumento.CD),
])
def test_el_tipo_de_documento_sale_de_la_marca_textual(texto, esperado):
    """El codigo resuelto es el de SGDEA (`models.enums.TipoDocumento`)."""
    h, nr = extraer_tipo_documento(texto)
    assert h is not None, f"deberia resolver: {texto}"
    assert h.valor == esperado


def test_el_mismo_numero_con_dos_tipos_de_persona_se_escala():
    """
    Cuando alguien cambia de tarjeta de identidad a cedula conserva el numero,
    y el correo menciona los dos rotulos. Cual vale HOY es semantica.
    """
    texto = (
        "YO, ANGEL JOSE RAMIREZ, IDENTIFICADO ACTUALMENTE CON CEDULA DE "
        "CIUDADANIA NO. 9199635734, TENIENDO EN CUENTA QUE ANTERIORMENTE ME "
        "ENCONTRABA IDENTIFICADO CON LA TARJETA DE IDENTIDAD NO. 9199635734."
    )
    h, nr = extraer_tipo_documento(texto, numero="9199635734")
    assert h is None
    assert nr.motivo == "EL_MISMO_NUMERO_CON_DOS_TIPOS"
    assert "TI" in nr.detalle and "CC" in nr.detalle


@pytest.mark.parametrize("texto,esperado", [
    ("Cedula de extranjeria No. 123456", TipoDocumento.CE),
    ("documento de identidad: pasaporte AV1234567", TipoDocumento.PA),
    ("tarjeta de identidad No. 1012345678", TipoDocumento.TI),
])
def test_una_palabra_generica_junto_al_tipo_no_es_un_conflicto(texto, esperado):
    """
    "documento" e "identificado" acompañan a cualquier tipo, y "cedula" esta
    dentro de "cedula de extranjeria": ninguna afirma que el tipo sea CC.
    """
    numero = "".join(c for c in texto if c.isdigit())
    h, nr = extraer_tipo_documento(texto, numero=numero)
    assert h is not None, nr
    assert h.valor == esperado


@pytest.mark.parametrize("texto", [
    "companias nacionales o extranjeras con o sin caracter de filiales",
    "podra celebrar contratos con sociedades extranjeras",
])
def test_extranjeras_como_adjetivo_no_es_cedula_de_extranjeria(texto):
    """El objeto social de cualquier empresa dice "extranjeras"."""
    h, nr = extraer_tipo_documento(f"{texto} NIT 900123456")
    assert h is None or h.valor != TipoDocumento.CE


def test_se_abstiene_cuando_hay_cc_y_nit_a_la_vez():
    """
    Caso frecuentisimo: la empresa escribe con su NIT por un trabajador con CC.
    Cual corresponde a quien no se puede decidir sin contexto semantico.
    """
    h, nr = extraer_tipo_documento(
        "La empresa NIT 900123456 solicita por el trabajador CC 41234567"
    )
    assert h is None
    assert nr.motivo == "VARIOS_TIPOS_MENCIONADOS"
    assert "CC" in nr.detalle and "NI" in nr.detalle


def test_el_rotulo_pegado_gana_a_una_mencion_lejana_del_mismo_numero():
    """En la planilla de aportes la columna del aportante se rotula NIT y un independiente escribe ahi su propia cedula."""
    texto = (
        "Atentamente, MIGUEL ANDRES ALZATE TORRES C.C. 93659751 EMPLEADOR\n"
        "# planilla.pdf\n## Pagina 1\n"
        "TIPO NUMERO NOMBRE APORTANTE NIT 93659751 MIGUEL ANDRES ALZATE"
    )
    h, nr = extraer_tipo_documento(texto, numero="93659751", para="remitente")
    assert h is not None, nr
    assert h.valor == TipoDocumento.CC
    assert "pegado" in h.evidencia


def test_dos_tipos_de_persona_pegados_al_mismo_numero_se_escalan():
    texto = ("identificado con C.C. 41234567 y antes con T.I. 41234567")
    h, nr = extraer_tipo_documento(texto, numero="41234567")
    assert h is None
    assert nr.motivo == "EL_MISMO_NUMERO_CON_DOS_TIPOS"
    assert "CC" in nr.detalle and "TI" in nr.detalle


def test_con_el_numero_conocido_manda_la_marca_que_lo_acompana():
    """
    El barrido de todo el texto responde "que tipos menciona el correo", que no
    es la pregunta. Si se sabe cual es el numero, decide la marca que lo toca:
    el NIT de la empresa y la cedula del trabajador conviven en el mismo correo
    y el barrido daria NIT para una persona.
    """
    texto = ("La empresa CONSTRUCTORA DEL RIO SAS con NIT 900123456 reporta al "
             "trabajador PEDRO RUIZ identificado con C.C. 41234567.")
    h, nr = extraer_tipo_documento(texto, numero="41234567")
    assert h is not None, nr
    assert h.valor == TipoDocumento.CC
    assert h.metodo == "marca_del_numero_elegido"


def test_sin_numero_y_con_varios_tipos_no_se_atribuye_ninguno():
    texto = ("La empresa CONSTRUCTORA DEL RIO SAS con NIT 900123456 reporta al "
             "trabajador PEDRO RUIZ identificado con C.C. 41234567.")
    h, nr = extraer_tipo_documento(texto, numero="")
    assert h is None
    assert nr.motivo == "VARIOS_TIPOS_MENCIONADOS"


def test_sin_marca_de_tipo_lo_dice():
    h, nr = extraer_tipo_documento("El numero es 41234567")
    assert h is None
    assert nr.motivo == "SIN_MARCA_DE_TIPO_DOCUMENTO"


# ── Telefono ──

def test_resuelve_un_telefono_con_palabra_clave():
    h, _ = extraer_telefono("Cualquier duda al celular 310 555 1234")
    assert h is not None
    assert "3105551234" in str(h.valor).replace(" ", "").replace("-", "")


def test_sin_telefono_lo_dice():
    h, nr = extraer_telefono("Solicito el certificado laboral")
    assert h is None
    assert nr.motivo == "SIN_TELEFONO"


# ── Nombre del remitente: reutiliza la cascada probada ──

def test_el_nombre_del_remitente_usa_el_display_name_valido():
    h, _ = extraer_nombre_remitente(
        "Karen Sofia Contreras", "karen@empresa.com", "cuerpo del correo"
    )
    assert h.valor == "Karen Sofia Contreras"
    assert h.confianza >= 0.9
    assert "display name" in h.evidencia


def test_el_correo_como_display_name_escala_con_motivo():
    """Un correo como display name no es un nombre: se escala con el motivo."""
    h, nr = extraer_nombre_remitente(
        "contacto@mywak.com.co", "contacto@mywak.com.co", "sin firma"
    )
    assert h is None
    assert nr.motivo == "SIN_NOMBRE_EN_NINGUNA_FUENTE"
    assert "contacto@mywak.com.co" in nr.detalle


def test_recupera_del_cuerpo_con_confianza_menor():
    """
    Un nombre interpretado de una firma es menos fiable que el display name, y
    la confianza debe reflejarlo para que la compuerta final lo sepa.
    """
    h, _ = extraer_nombre_remitente(
        "contacto@mywak.com.co", "contacto@mywak.com.co",
        "De: Maria Fernanda Gomez <maria@agrempresarial.com>",
    )
    assert h is not None
    assert h.valor == "Maria Fernanda Gomez"
    assert h.confianza < 0.9, "debe ser menos fiable que el display name"


# ── Nombre del afectado: solo el caso inequivoco ──

@pytest.mark.parametrize("texto,esperado", [
    ("Solicito la incapacidad del trabajador JUAN PEREZ GOMEZ", "JUAN PEREZ GOMEZ"),
    ("trabajador: Maria Fernanda Rojas", "Maria Fernanda Rojas"),
    ("Adjunto historia del paciente Carlos Andres Ruiz", "Carlos Andres Ruiz"),
    ("Datos del afiliado Luz Marina Moreno", "Luz Marina Moreno"),
])
def test_resuelve_el_afectado_con_etiqueta_explicita(texto, esperado):
    h, nr = extraer_nombre_afectado(texto)
    assert h is not None, f"deberia resolver: {texto}"
    assert h.valor == esperado


def test_se_abstiene_sin_etiqueta():
    """Sin etiqueta que marque al afectado, el campo se escala al modelo."""
    h, nr = extraer_nombre_afectado("Buen dia, adjunto los soportes solicitados")
    assert h is None
    assert nr.motivo == "SIN_ETIQUETA_DE_AFECTADO"


@pytest.mark.parametrize("nombre", [
    "Carlos Andres Ruiz",      # contiene "arl"
    "Felipe Sastoque Ramos",   # contiene "sas"
    "Ana Epstein Mora",        # contiene "eps"
    "Luis Felipe Salas",
])
def test_un_nombre_no_se_descarta_por_llevar_dentro_una_sigla(nombre):
    """Las siglas de entidad se comparan como palabra, no como subcadena."""
    h, nr = extraer_nombre_afectado(f"Historia del paciente {nombre}")
    assert h is not None, f"se descarto un nombre valido: {nombre}"
    assert h.valor == nombre


@pytest.mark.parametrize("entidad", [
    "Seguros Bolivar SAS",
    "Salud Total EPS",
    "Medicina Integral IPS",
])
def test_una_entidad_si_se_descarta(entidad):
    h, _ = extraer_nombre_afectado(f"Historia del paciente {entidad}")
    assert h is None


def test_no_toma_un_nombre_de_positiva_como_afectado():
    h, nr = extraer_nombre_afectado("El trabajador Positiva Compania de Seguros")
    assert h is None or "positiva" not in h.valor.lower()


def test_texto_vacio_no_revienta():
    h, nr = extraer_nombre_afectado("")
    assert h is None
    assert nr.motivo == "TEXTO_VACIO"


# ── Sexo: se lee, no se adivina ──

@pytest.mark.parametrize("texto,esperado", [
    ("Identificacion: 62352472 Sexo: Masculino Fecha Nacimiento: 01/01/1979", 1),
    ("Sexo Femenino Estado Civil: Union libre", 2),
    ("Genero: Mujer Direccion: Calle 3", 2),
    ("SEXO: M", 1),
    ("sexo: f", 2),
])
def test_el_sexo_se_lee_del_texto(texto, esperado):
    h, _ = extraer_sexo(texto)
    assert h is not None
    assert h.valor == esperado
    assert h.metodo == "sexo_declarado_en_el_texto"


@pytest.mark.parametrize("nombre", [
    "Maria Fernanda Gomez",
    "Carlos Andres Ruiz",
    "Xyzabc Qwerty",
    "Aleksandra Nowak",
])
def test_el_sexo_no_se_deduce_del_nombre(nombre):
    """
    Deducirlo exige una lista de nombres imposible de completar (extranjeros,
    compuestos, otra grafia) y escribe un dato falso sobre una persona real.
    Si el documento no lo dice, se escala.
    """
    h, nr = extraer_sexo(f"El trabajador {nombre} con CC 41234567")
    assert h is None
    assert nr.motivo == "SEXO_NO_DECLARADO"


def test_dos_sexos_declarados_no_se_atribuyen_a_nadie():
    """Una historia clinica con varias personas no permite decidir aqui."""
    h, nr = extraer_sexo("Paciente Sexo: Masculino. Acompañante Sexo: Femenino")
    assert h is None
    assert nr.motivo == "SEXO_DECLARADO_CONTRADICTORIO"


def test_sin_texto_no_deduce_sexo():
    h, nr = extraer_sexo("")
    assert h is None
    assert nr.motivo == "TEXTO_VACIO"


# ── Oficina: tabla de 27 filas ──

def test_la_oficina_sale_de_la_tabla_con_confianza_total():
    h, nr = extraer_oficina(14)
    if h is not None:
        assert h.confianza == 1.0, "una tabla de mapeo no tiene incertidumbre"
        assert h.metodo == "tabla_de_mapeo"
    else:
        assert nr.motivo in ("TIPO_SOLICITUD_SIN_OFICINA", "TABLA_NO_DISPONIBLE")


def test_sin_tipo_de_solicitud_no_hay_oficina():
    h, nr = extraer_oficina(None)
    assert h is None
    assert nr.motivo == "SIN_TIPO_SOLICITUD"


# ── Orquestacion ──

def test_extraer_todo_separa_resuelto_de_pendiente():
    e = extraer_todo(
        asunto="Incapacidad del trabajador JUAN PEREZ",
        cuerpo="Adjunto incapacidad del trabajador JUAN PEREZ con CC 41234567.\n"
               "Contacto al celular 310 555 1234.\n"
               "Cordialmente, Maria Gomez",
        from_name="Maria Gomez",
        from_address="maria.gomez@empresa.com",
    )
    assert "numero_documento_afectado" in e.campos_resueltos
    assert "nombre_remitente" in e.campos_resueltos
    # La clasificacion semantica siempre va a la IA
    assert "tipo_solicitud" in e.campos_pendientes
    assert "tipo_remitente" in e.campos_pendientes


def test_la_clasificacion_semantica_nunca_se_resuelve_sin_ia():
    """
    Elegir entre 27 categorias o distinguir familiar de tercero exige
    interpretar intencion. Fingir que un algoritmo lo resuelve produciria
    clasificaciones equivocadas con confianza alta, que es lo peor.
    """
    e = extraer_todo(asunto="x", cuerpo="y", from_name="Ana Lopez",
                     from_address="ana@x.com")
    assert e.no_resueltos["tipo_solicitud"].motivo == "REQUIERE_CLASIFICACION_SEMANTICA"
    assert e.no_resueltos["tipo_remitente"].motivo == "REQUIERE_CLASIFICACION_SEMANTICA"


def test_el_resumen_para_la_ia_solo_lleva_lo_pendiente():
    """El modelo recibe una lista de tareas con su motivo, no el correo completo."""
    e = extraer_todo(
        asunto="Incapacidad", cuerpo="CC 41234567 del trabajador PEDRO RUIZ",
        from_name="Ana Lopez", from_address="ana@empresa.com",
    )
    resumen = e.resumen_para_ia()
    assert "no pudo resolver" in resumen
    assert "tipo_solicitud" in resumen
    # Y le dice que NO volver a buscar, para que no gaste en ello
    if e.campos_resueltos:
        assert "no lo vuelvas a buscar" in resumen


def test_un_candidato_con_confianza_baja_se_escala_indicandolo():
    e = extraer_todo(
        asunto="", cuerpo="numero 41234567 sin ninguna marca de contexto",
        from_name="Ana Lopez", from_address="ana@x.com", umbral=0.99,
    )
    nr = e.no_resueltos.get("numero_documento_afectado")
    assert nr is not None
    assert nr.motivo == "CONFIANZA_INSUFICIENTE"
    assert "41234567" in nr.detalle, "el motivo debe decir cual era el candidato"


def test_el_umbral_es_configurable():
    assert 0.0 < UMBRAL_CONFIANZA < 1.0


def test_valor_devuelve_el_dato_o_el_defecto():
    e = extraer_todo(asunto="", cuerpo="CC 41234567 del trabajador",
                     from_name="Ana Lopez", from_address="ana@x.com")
    assert e.valor("numero_documento_afectado") == "41234567"
    assert e.valor("campo_inexistente", "defecto") == "defecto"


# ── Numeros que estan en el correo pero no son de nadie ──

def test_el_nit_de_positiva_en_el_pie_no_es_el_documento_del_afectado():
    """
    El pie de un correo de Positiva lleva su propio NIT. Si es el unico numero
    del texto, se aceptaba como documento del afectado y quedaba asi en el
    registro. Es la misma familia del caso que reporto el area.
    """
    texto = (
        "Buen dia, adjunto la solicitud para su tramite.\n\n"
        "POSITIVA COMPANIA DE SEGUROS S.A. NIT 8600111536\n"
    )
    h, nr = extraer_documento(texto)

    assert h is None, f"acepto {h.valor if h else ''} como documento"
    assert nr is not None and nr.motivo == "SOLO_NIT_DE_ORGANIZACION_EN_EL_TEXTO"


def test_un_rango_de_anios_pegado_no_es_un_documento():
    """"2025 - 2024" queda como una racha de ocho digitos."""
    h, nr = extraer_documento(
        "Solicito la certificacion correspondiente al periodo 2025 - 2024")

    assert h is None, f"acepto {h.valor if h else ''} como documento"
    assert nr is not None


def test_un_conmutador_no_es_un_documento():
    """
    El filtro de telefonos reconoce "tel", "cel" y las lineas de atencion,
    pero no "PBX" ni "conmutador", que es como se escribe el numero
    institucional en la mayoria de las firmas.
    """
    for etiqueta in ("PBX", "Conmutador"):
        h, _ = extraer_documento(
            f"Cualquier inquietud al {etiqueta} 6658213824, gracias.")
        assert h is None, f"con '{etiqueta}' acepto {h.valor if h else ''}"


def test_una_cedula_marcada_si_se_extrae_aunque_haya_un_pbx():
    """
    Los filtros no pueden costar el caso normal: el documento del afectado se
    extrae igual cuando la firma trae el conmutador.
    """
    h, _ = extraer_documento(
        "El trabajador con CC 41234567 solicita su certificado. "
        "Atendemos en el PBX 6658213824.")

    assert h is not None and h.valor == "41234567"
