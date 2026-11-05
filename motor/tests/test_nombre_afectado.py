"""
Un nombre equivocado es peor que ningun nombre.

Si el extractor no encuentra a la persona, el correo va a revision manual y
alguien lo resuelve. Si devuelve una frase cualquiera, se radica una PQRD a
nombre de nadie y el error queda en la base. Estas pruebas fijan esa asimetria:
casi todas verifican que NO se acepte algo que no es un nombre.
"""
import pytest

from pipeline.extraccion import extraer_documento, extraer_nombre_afectado

DOC = "41234567"


def _valor(texto, asunto=""):
    h, _ = extraer_nombre_afectado(texto, asunto)
    return h.valor if h else None


# ── Lo que si es un nombre ──

@pytest.mark.parametrize("texto,esperado", [
    ("OMAR CALDERON NIÑO CC 32718260", "OMAR CALDERON NIÑO"),
    ("Victor Alfonso Martinez Cédula 1366235150", "Victor Alfonso Martinez"),
    ("SANTIAGO LOZADA MORA c.c. 71278869", "SANTIAGO LOZADA MORA"),
    ("JORGE BARRERA NOSSA CC: 73423155", "JORGE BARRERA NOSSA"),
    ("JOHANA MORENO ESCOBAR identificado(a) con CC 1761314578",
     "JOHANA MORENO ESCOBAR"),
    ("yo Johani Valencia Lopez identificado con cédula de ciudadanía No. 33431550",
     "Johani Valencia Lopez"),
    ("Nombre completo: Angie Vanesa Iglesias Díaz", "Angie Vanesa Iglesias Díaz"),
    ("Mi nombre es Patricia Melendrez y mi cedula es 28506532",
     "Patricia Melendrez"),
])
def test_el_nombre_pegado_a_su_documento_se_resuelve(texto, esperado):
    """
    El correo real casi nunca dice "afectado". Dice el nombre y a continuacion
    la cedula, con cualquier combinacion de conectores en medio.
    """
    assert _valor(texto) == esperado


def test_se_conservan_enie_y_tildes():
    """El dato viaja tal cual a SGDEA; normalizar aqui corromperia el nombre."""
    assert _valor("El trabajador MUÑOZ PEÑA JOSÉ MARÍA") == "MUÑOZ PEÑA JOSÉ MARÍA"


def test_el_nombre_conserva_sus_particulas():
    assert _valor("La paciente Maria del Carmen Gomez Perez") == \
        "Maria del Carmen Gomez Perez"


# ── Lo que NO es un nombre ──

@pytest.mark.parametrize("texto", [
    "el trabajador de esta información será asumida por la empresa",
    "El paciente se encontraba en el patio cuando ocurrio",
    "afiliado que si tiene diagnósticos de origen laboral",
])
def test_una_frase_en_minusculas_no_es_un_nombre(texto):
    """El patron exige mayuscula inicial: una frase en prosa no es un nombre."""
    assert _valor(texto) is None


@pytest.mark.parametrize("texto,esperado", [
    ("El trabajador SEGUIMIENTO CASO GABRIEL CASTAÑEDA", "GABRIEL CASTAÑEDA"),
    ("El paciente IDENTIFICACION GERENTE YAMILE PEÑARANDA PARRA",
     "YAMILE PEÑARANDA PARRA"),
    ("Cordialmente OMAR CALDERON NIÑO CC 32718260", "OMAR CALDERON NIÑO"),
])
def test_la_cabeza_administrativa_se_descarta_sin_perder_el_nombre(texto, esperado):
    """Cortar en la primera palabra invalida vaciaria el candidato."""
    assert _valor(texto) == esperado


@pytest.mark.parametrize("texto", [
    "El trabajador Cordial Saludo",
    "La paciente ACTIVIDAD ECONOMICA",
    "El afiliado SIN SECUELAS DERIVADAS",
    "El trabajador REPORTE DE NOVEDADES",
    "El titular CENTRO DE TRABAJO",
])
def test_el_vocabulario_administrativo_no_pasa_por_nombre(texto):
    assert _valor(texto) is None


@pytest.mark.parametrize("texto,esperado", [
    ("El trabajador PEDRO RUIZ MEJIA solicita el pago", "PEDRO RUIZ MEJIA"),
    ("La paciente ANA LOPEZ RUIZ reporta el accidente", "ANA LOPEZ RUIZ"),
    ("El afiliado JUAN PEREZ MORA manifiesta inconformidad", "JUAN PEREZ MORA"),
    ("El titular MARIA GOMEZ DIAZ adjunta los soportes", "MARIA GOMEZ DIAZ"),
])
def test_un_verbo_pegado_al_nombre_se_recorta(texto, esperado):
    """El patron admite palabras interiores en minuscula para aceptar apellidos como "Soto cano"."""
    assert _valor(texto) == esperado


@pytest.mark.parametrize("nombre", [
    "Eduardo Andres Soto cano",
    "Juan Manuel Julio teherán",
    "Patricia Meléndrez ahumada",
])
def test_un_apellido_en_minuscula_sigue_siendo_parte_del_nombre(nombre):
    """Un apellido escrito en minuscula sigue siendo parte del nombre: recortarlo lo pierde."""
    assert _valor(f"El trabajador {nombre}, con CC 41234567") == nombre


def test_un_cargo_no_pasa_por_nombre_del_afectado():
    """"Recursos Humanos" es un cargo, no el nombre de una persona, sea remitente o afectado."""
    assert _valor("El trabajador Recursos Humanos solicita") is None
    assert _valor("La paciente Gerencia Medica requiere") is None


def test_un_fragmento_de_palabra_no_arranca_un_nombre():
    """Sin `\\b` inicial, 'PRESTACIONES SOCIALES' produce 'ES SOCIALES'."""
    assert _valor("El trabajador reclama PRESTACIONES SOCIALES") is None


# ── Forma completa del nombre ──

def test_un_nombre_de_dos_palabras_se_completa_con_el_del_mismo_texto():
    """
    El correo nombra a la persona en corto arriba y entera mas abajo. Radicar
    'Ana Marcela' deja el registro sin apellidos.
    """
    texto = ("Sra. Ana Marcela buenas tardes, solicitamos documentos.\n"
             "Cordialmente, la titular ANA MARCELA SANCHEZ FLOREZ CC 41234567")
    assert _valor(texto) == "ANA MARCELA SANCHEZ FLOREZ"


def test_solo_se_completa_con_una_forma_que_contenga_al_nombre():
    """
    Compartir una palabra no basta: dos personas del mismo correo pueden
    compartir apellido y quedarse con la mas larga las confundiria.
    """
    texto = ("La titular Ana Marcela CC 41234567\n"
             "El trabajador PEDRO MARCELA GOMEZ RUIZ CC 80123456")
    assert _valor(texto) == "Ana Marcela"


def test_un_nombre_de_tres_palabras_ya_no_se_completa():
    """
    A partir de tres palabras la forma mas larga del texto suele traer pegada
    una palabra que no es del nombre: un mes, o una ciudad partida por el OCR.
    """
    texto = ("El trabajador Johani Valencia Lopez CC 41234567\n"
             "Planilla DICIEMBRE JOHANI VALENCIA LOPEZ CC 41234567")
    assert _valor(texto) == "Johani Valencia Lopez"


# ── Vocabulario de etiquetas del afectado ──

def test_el_lesionado_es_una_etiqueta_de_afectado():
    """Es el termino estandar de ARL, junto a siniestrado y accidentado."""
    assert _valor("1. Nombre del lesionado GABRIEL DE JESUS CASTAÑEDA LUNA") == \
        "GABRIEL DE JESUS CASTAÑEDA LUNA"


def test_el_conector_admite_el_al_que_escribe_el_correo():
    """"identificado con al numero de cedula" es lo que trae el correo real."""
    assert _valor(
        "aportes realizados a favor de CAMILO HERNAN VARGAS FERNANDEZ, "
        "identificado con al número de cedula 80645773") == \
        "CAMILO HERNAN VARGAS FERNANDEZ"


# ── Certificado de aportes: quien paga no es el afectado ──

def test_quien_paga_los_aportes_no_es_el_afectado():
    """
    En un certificado de aportes el primer nombre es del pagador y el afiliado
    va en el listado. Tomar el primero radica al empleador por el trabajador.
    """
    texto = ("Certificado de Aportes. Se certifica que JOHANA MILENA MORENO "
             "ESCOBAR identificado(a) con CC 1761314573 realizo los siguientes "
             "aportes al Sistema de Seguridad Social: PARDO ESGUERRA ANA MARIA "
             "CC 5998638897 Periodo 2026-03")
    assert _valor(texto) == "PARDO ESGUERRA ANA MARIA"


def test_certificar_otra_cosa_si_nombra_al_afectado():
    """
    La exclusion pide las dos partes de la frase. Sin la clausula de aportes,
    'se certifica que X' es la forma normal de nombrar a la persona del caso.
    """
    texto = ("Se certifica que JOHANA MILENA MORENO ESCOBAR identificada con "
             "CC 1761314573 sufrio un accidente de trabajo el 12 de marzo.")
    assert _valor(texto) == "JOHANA MILENA MORENO ESCOBAR"


def test_certificamos_en_plural_tambien_se_excluye():
    texto = ("Certificamos que MUNICIPIO DE TABIO identificado con NIT "
             "8000111234 realizo los siguientes aportes: LUIS ALBERTO MONGUI "
             "GARCIA CC 41234567 Periodo 2026-01")
    assert _valor(texto) == "LUIS ALBERTO MONGUI GARCIA"


def test_una_particula_no_puede_abrir_un_nombre():
    assert _valor("", "Solicitud DE BOGOTA") is None


def test_en_el_asunto_una_particula_delata_un_lugar():
    """'X DE Y' en un asunto es un municipio o una entidad, no una persona."""
    assert _valor("", "Afiliacion Concejal del MUNICIPIO DE TABIO") is None


@pytest.mark.parametrize("nombre", [
    "Ana Maria Pereira Gomez",       # Pereira es apellido, no solo ciudad
    "Florencia Restrepo Diaz",       # Florencia es nombre de pila
    "Leticia Muñoz Rojas",           # Leticia tambien
    "Carlos Palmira Soto",
    "Sandra Milena Quindio Vera",
    "Juan Bello Caldas",
])
def test_un_apellido_que_tambien_es_un_municipio_no_se_recorta(nombre):
    """
    Media Colombia se apellida como un municipio. Listar ciudades para descartar
    lugares mutila personas reales, que es un dato falso en la radicacion.
    """
    assert _valor(f"El trabajador {nombre} con CC {DOC}") == nombre


def test_un_apellido_repetido_no_esconde_el_recorte():
    """El recorte se calcula por posicion, asi que queda anotado en la
    evidencia aunque la palabra descartada se repita dentro del nombre."""
    h, _ = extraer_nombre_afectado(
        f"El trabajador CARLOS SANCHEZ SANCHEZ Cordialmente con CC {DOC}")
    assert h is not None
    assert h.valor == "CARLOS SANCHEZ SANCHEZ"
    assert "Cordialmente" in h.evidencia


def test_un_nombre_con_apellido_repetido_se_conserva_entero():
    assert _valor(f"El trabajador CARLOS JAVIER SANCHEZ SANCHEZ con CC {DOC}") \
        == "CARLOS JAVIER SANCHEZ SANCHEZ"


def test_el_recorte_queda_escrito_en_la_evidencia():
    """
    El recorte se apoya en una lista mantenida a mano. Si alguna vez coincide
    con un apellido real, tiene que poder auditarse: nada se descarta callado.
    """
    h, _ = extraer_nombre_afectado(
        f"El trabajador Cordialmente GABRIEL CASTAÑEDA con CC {DOC}")
    assert h is not None
    assert h.valor == "GABRIEL CASTAÑEDA"
    assert "Cordialmente" in h.evidencia


def test_un_nombre_recortado_vale_menos_que_uno_intacto():
    intacto, _ = extraer_nombre_afectado(f"El trabajador GABRIEL CASTAÑEDA CC {DOC}")
    recortado, _ = extraer_nombre_afectado(
        f"El trabajador SEGUIMIENTO GABRIEL CASTAÑEDA CC {DOC}")
    assert recortado.valor == intacto.valor
    assert recortado.confianza < intacto.confianza


def test_el_nombre_pegado_a_un_nit_es_una_empresa():
    """El afectado es siempre una persona."""
    assert _valor("Razón Social HUERTA EXPRESS NIT 8948855875") is None


def test_el_nombre_se_corta_en_cuatro_palabras():
    """Dos nombres y dos apellidos; la quinta ya es la palabra siguiente."""
    assert _valor("El trabajador HEIDI MADELYN CAMARGO SAMPER LUIS") == \
        "HEIDI MADELYN CAMARGO SAMPER"


# ── Correos que no tienen afectado natural ──

@pytest.mark.parametrize("cuerpo", [
    "Razon Social de la empresa HUERTA EXPRESS NIT 8948855872 solicita la "
    "recuperacion de la clave para ingresar a la plataforma",
    "Solicitud de estado de cuenta de PYM CONSTRUCTORES NIT 324450955",
    "Solicitud de aclaracion de cartera informada SUBAGAN S.A. NIT 301277477",
])
def test_un_correo_que_solo_identifica_una_empresa_lo_dice_asi(cuerpo):
    """Una solicitud del empleador sobre su propia cuenta no tiene trabajador."""
    h, nr = extraer_nombre_afectado(cuerpo)
    assert h is None
    assert nr.motivo == "SOLO_IDENTIFICA_UNA_EMPRESA"
    assert "NIT" in nr.detalle


@pytest.mark.parametrize("cuerpo", [
    "La empresa CONSTRUCTORA SAS NIT 900123456 reporta al trabajador con "
    "C.C. 41234567",
    "Adjunto la cedula 41234567 del afiliado",
    "Solicito informacion general sobre el tramite",
])
def test_el_motivo_de_empresa_no_se_usa_si_hay_documento_de_persona(cuerpo):
    """
    Con un documento de persona en el correo el afectado existe, aunque no se
    haya podido nombrar: el motivo tiene que seguir siendo el generico.
    """
    _, nr = extraer_nombre_afectado(cuerpo)
    assert nr is None or nr.motivo != "SOLO_IDENTIFICA_UNA_EMPRESA"


# ── Cargos y rotulos pegados al nombre ──

@pytest.mark.parametrize("bruto,esperado", [
    ("REVISOR FISCAL YANETH ORTIZ BORJA", "YANETH ORTIZ BORJA"),
    ("SUBGERENTE ELKIN ARMANDO PEREZ", "ELKIN ARMANDO PEREZ"),
    ("EJECUTIVA ANA MARCELA SANCHEZ", "ANA MARCELA SANCHEZ"),
    ("Profesional Intramural Sandra Ximena Silva", "Sandra Ximena Silva"),
])
def test_un_cargo_delante_del_nombre_se_recorta(bruto, esperado):
    """El cargo acompaña al nombre en las firmas y no forma parte de el."""
    assert _valor(f"El afiliado {bruto} con CC {DOC}") == esperado


@pytest.mark.parametrize("basura", [
    "PERSONA QUE SE ACCIDENTO",
    "VACANTE VACANTE CARGO",
    "Profesional Siniestrado",
    "Solidaridad Pensional",
    "ORDENES DE MEDICAMENTOS",
    "recomendacion Obtener Outlook",
    "NOTIFICACION CON OBJECION",
    "Documento Autoreporte",
    "CERTIFICADO DE ACCIDENTALIDAD",
])
def test_una_frase_de_formulario_no_es_un_nombre(basura):
    """Salen de rejillas y encabezados de formulario: "Solidaridad Pensional" es un fondo, no una persona."""
    assert _valor(f"{basura} CC {DOC}") is None, basura


# ── Una palabra del nombre en minuscula ──

@pytest.mark.parametrize("texto,esperado", [
    ("Afiliado: José Sebastián ballesteros Pachón",
     "José Sebastián ballesteros Pachón"),
    ("El trabajador Juan Manuel Julio teherán",
     "Juan Manuel Julio teherán"),
    ("paciente Christian camilo Bernal Gonzalez",
     "Christian camilo Bernal Gonzalez"),
    ("afiliado Ricardo cifuentes Ospina", "Ricardo cifuentes Ospina"),
])
def test_una_palabra_interior_en_minuscula_no_corta_el_nombre(texto, esperado):
    """El correo real escribe apellidos en minuscula y el nombre queda entero."""
    assert _valor(f"{texto} con CC {DOC}") == esperado


@pytest.mark.parametrize("texto", [
    "El trabajador ospina identificado con CC",
    "el afiliado perez con CC",
])
def test_una_sola_palabra_capitalizada_no_sostiene_un_nombre(texto):
    """
    Las palabras en minuscula se admiten DENTRO del nombre, nunca como su
    unico contenido: hacen falta dos capitalizadas.
    """
    assert _valor(f"{texto} {DOC}") is None


def test_una_frase_entera_en_minuscula_sigue_sin_ser_un_nombre():
    assert _valor(f"de esta informacion sera asumida con CC {DOC}") is None


def test_un_apellido_solo_no_basta():
    assert _valor("El trabajador Ospina CC 41234567") is None


def test_el_conector_no_se_come_una_palabra_del_nombre():
    """
    'CORONADO Identificado con CC' cabe en el patron y desplaza el inicio del
    nombre: sin margen, 'VICTOR' se pierde.
    """
    assert _valor("cliente VICTOR JULIO BONILLA CORONADO\nIdentificado con\nCC 1791318") \
        == "VICTOR JULIO BONILLA CORONADO"


def test_la_cola_que_deja_el_ocr_se_descarta():
    assert _valor(f"CC {DOC} de YESENIA ALVAREZ MORALES JA") == \
        "YESENIA ALVAREZ MORALES"


# ── Enlace con el resto de la extraccion ──

def test_el_nombre_resuelto_desambigua_el_documento():
    """
    Es la razon de extraer el nombre primero: con dos cedulas en el texto, la
    del afectado es la que esta a su lado.
    """
    texto = (
        "El trabajador PEDRO RUIZ MEJIA con CC 41234567 requiere autorizacion. "
        + "Relleno sin relacion. " * 30
        + "La contadora ANA LOPEZ con CC 52987654 firma."
    )
    h, _ = extraer_documento(texto, nombre_afectado="PEDRO RUIZ MEJIA")
    assert h is not None
    assert h.valor == "41234567"


# ── "CASO" en el asunto ──

def test_el_rotulo_caso_del_asunto_no_se_queda_con_el_nombre():
    """
    El asunto pone el tramite delante y la persona detras de "CASO". La regla
    del nombre pegado al documento capturaba la frase entera y el recorte se
    quedaba con el lado equivocado: devolvia el tramite y tiraba a la persona.
    """
    h, _ = extraer_nombre_afectado(
        "", "Solicitud de Atencion Prioritaria CASO CLARA VELEZ CC 41234567")

    assert h is not None
    assert h.valor == "CLARA VELEZ"


def test_caso_solo_es_etiqueta_en_el_asunto():
    """
    En prosa la palabra aparece en cualquier parte, y lo que va detras no es
    nadie. Admitirla en el cuerpo nombraria al afectado con lo primero que
    siguiera.
    """
    cuerpo = ("En este caso Positiva Compania de Seguros debe responder al "
              "trabajador PEDRO RUIZ MEJIA con CC 41234567.")

    h, _ = extraer_nombre_afectado(cuerpo, "")

    assert h is not None
    assert h.valor == "PEDRO RUIZ MEJIA"


def test_tirar_algo_con_forma_de_nombre_baja_la_confianza():
    """
    El recorte corta en la primera palabra del vocabulario y conserva lo de
    antes. Si detras quedaban dos palabras capitalizadas, la eleccion no fue
    evidente: el campo tiene que llegar a la compuerta con menos confianza en
    vez de pasar como si fuera seguro.
    """
    from pipeline.extraccion import PENALIZACION_POR_RECORTE, _candidato_afectado

    frase = "Marta Ochoa REPORTE CLARA VELEZ"
    h = _candidato_afectado(frase, frase, 0, "nombre_antes_del_documento", 0.86)

    assert h is not None
    assert h.valor == "Marta Ochoa"
    assert h.confianza == round(0.86 - PENALIZACION_POR_RECORTE, 2)


def test_una_cola_que_no_nombra_a_nadie_no_penaliza():
    """La cola normal es el desborde del regex sobre la marca del documento."""
    from pipeline.extraccion import _candidato_afectado

    frase = "CLARA VELEZ CC"
    h = _candidato_afectado(frase, frase, 0, "nombre_antes_del_documento", 0.86)

    assert h is not None
    assert h.valor == "CLARA VELEZ"
    assert h.confianza == 0.86
