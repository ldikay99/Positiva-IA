"""
El sexo se lee del texto: nunca se deduce del nombre de pila.

Dos formas de que el texto lo declare: el rotulo "Sexo: F" y la concordancia
gramatical junto al nombre del afectado ("mi trabajadora ANA LOPEZ"). La
segunda es una afirmacion de quien escribe sobre esa persona, no una
inferencia a partir de como se llama.
"""
import pytest

from pipeline.extraccion import (
    SEXO_FEMENINO,
    SEXO_MASCULINO,
    VENTANA_DE_CONCORDANCIA,
    extraer_sexo,
)

NOMBRE = "ANA LOPEZ RUIZ"


def _sexo(texto, nombre=NOMBRE):
    h, _ = extraer_sexo(texto, nombre_afectado=nombre)
    return h.valor if h else None


def _motivo(texto, nombre=NOMBRE):
    _, nr = extraer_sexo(texto, nombre_afectado=nombre)
    return nr.motivo if nr else ""


# ── El rotulo explicito manda ──

@pytest.mark.parametrize("texto,esperado", [
    ("Sexo: Femenino", SEXO_FEMENINO),
    ("Sexo : Masculino", SEXO_MASCULINO),
    ("Genero: M", SEXO_MASCULINO),
    ("género - mujer", SEXO_FEMENINO),
])
def test_el_rotulo_explicito_resuelve_el_sexo(texto, esperado):
    h, _ = extraer_sexo(texto)
    assert h is not None and h.valor == esperado
    assert h.metodo == "sexo_declarado_en_el_texto"


def test_el_rotulo_gana_a_la_concordancia():
    texto = f"El trabajador {NOMBRE} ... Sexo: Femenino"
    h, _ = extraer_sexo(texto, nombre_afectado=NOMBRE)
    assert h.valor == SEXO_FEMENINO
    assert h.metodo == "sexo_declarado_en_el_texto"


# ── Concordancia junto al nombre ──

@pytest.mark.parametrize("texto,esperado", [
    (f"Solicito el pago de mi trabajadora {NOMBRE} JEFE DE COCINA",
     SEXO_FEMENINO),
    (f"{NOMBRE}, identificada con C.C. 41234567", SEXO_FEMENINO),
    (f"la señora {NOMBRE} presenta la solicitud", SEXO_FEMENINO),
    (f"el trabajador {NOMBRE} sufrio un accidente", SEXO_MASCULINO),
    (f"{NOMBRE}, identificado con cedula 41234567", SEXO_MASCULINO),
    (f"el afiliado {NOMBRE} requiere autorizacion", SEXO_MASCULINO),
])
def test_la_concordancia_junto_al_nombre_resuelve_el_sexo(texto, esperado):
    h, nr = extraer_sexo(texto, nombre_afectado=NOMBRE)
    assert h is not None, nr
    assert h.valor == esperado
    assert h.metodo == "concordancia_junto_al_nombre"


def test_la_evidencia_nombra_la_marca_que_decidio():
    h, _ = extraer_sexo(f"mi trabajadora {NOMBRE} solicita",
                        nombre_afectado=NOMBRE)
    assert '"trabajadora"' in h.evidencia


@pytest.mark.parametrize("texto,marca", [
    (f"mi trabajadora {NOMBRE} solicita", "trabajadora"),
    (f"{NOMBRE}, identificada con C.C. 41234567", "identificada"),
    (f"el afiliado {NOMBRE} requiere", "afiliado"),
    (f"Señor {NOMBRE}: su solicitud", "Señor"),
])
def test_la_evidencia_no_puede_contradecir_la_decision(texto, marca):
    """
    Citar una ventana distinta de la que decidio deja al operador leyendo
    "señora" en un registro guardado como masculino, que es peor que no dar
    evidencia.
    """
    from pipeline.extraccion import _GENERO_FEMENINO, _GENERO_MASCULINO

    h, _ = extraer_sexo(texto, nombre_afectado=NOMBRE)
    assert h is not None
    assert f'"{marca}"' in h.evidencia
    patron = _GENERO_FEMENINO if h.valor == SEXO_FEMENINO else _GENERO_MASCULINO
    assert patron.fullmatch(marca), (
        f"la marca citada no corresponde al sexo resuelto: {marca}")


# ── Cuando el texto se contradice, no se decide ──

def test_dos_concordancias_opuestas_se_escalan():
    """
    Pasa en correos reales: el cuerpo escribe "el señor" y el adjunto formal
    "Señora:". Elegir una seria inventar.
    """
    texto = (f"hacemos constar que el señor {NOMBRE} con cedula 41234567\n"
             f"Señora: {NOMBRE} CEDULA CIUDADANIA: 41234567")
    assert _sexo(texto) is None
    assert _motivo(texto) == "SEXO_SIN_CONCORDANCIA_UTILIZABLE"


def test_dos_rotulos_opuestos_se_escalan():
    texto = "Sexo: Masculino ... mas abajo ... Sexo: Femenino"
    _, nr = extraer_sexo(texto)
    assert nr.motivo == "SEXO_DECLARADO_CONTRADICTORIO"


def test_la_marca_pegada_al_nombre_gana_a_la_contraria_lejana():
    """En una historia clinica el nombre sale decenas de veces y en alguna cae dentro de la ventana la fila de otra persona."""
    relleno = "descripcion del procedimiento " * 2
    texto = (f"Paciente: {NOMBRE} identificada con CC 41234567\n"
             f"{relleno}\nel trabajador PEDRO RUIZ MEJIA {NOMBRE}")
    h, nr = extraer_sexo(texto, nombre_afectado=NOMBRE)
    assert h is not None, nr.motivo if nr else ""
    assert h.valor == SEXO_FEMENINO


def test_dos_marcas_opuestas_igual_de_cerca_siguen_escalando():
    """
    Una a cada lado del nombre y las dos pegadas: la ventaja no es clara y
    elegir seria adivinar. Pasa en correos reales, donde el cuerpo trata a la
    persona en masculino y el formulario la rotula en femenino.
    """
    texto = f"el trabajador {NOMBRE}, identificada con CC 41234567"
    assert _sexo(texto) is None
    assert _motivo(texto) == "SEXO_SIN_CONCORDANCIA_UTILIZABLE"


def test_la_marca_inmediatamente_anterior_al_nombre_manda():
    """Con una marca pegada al nombre y la contraria mas lejos, se decide."""
    texto = f"el señor y la señora {NOMBRE} con CC 41234567"
    assert _sexo(texto) == SEXO_FEMENINO


# ── La persona se localiza por sus palabras, no por la cadena entera ──

def test_una_mencion_en_corto_tambien_ancla_la_lectura():
    """
    El correo nombra entero al afectado en un sitio y en corto en otro
    ("Sra. Ana Lopez"). Buscar la cadena completa y literal no encuentra la
    mencion corta, y ahi es donde suele estar el tratamiento.
    """
    texto = (f"Datos del contrato\nNombre completo: {NOMBRE}\nCC 41234567\n"
             f"Sra. ANA LOPEZ, agradecemos su gestion")
    h, nr = extraer_sexo(texto, nombre_afectado=NOMBRE)
    assert h is not None, nr.motivo if nr else ""
    assert h.valor == SEXO_FEMENINO


def test_un_salto_de_linea_dentro_del_nombre_no_impide_la_lectura():
    texto = f"la señora ANA\nLOPEZ RUIZ presenta la solicitud"
    h, nr = extraer_sexo(texto, nombre_afectado=NOMBRE)
    assert h is not None, nr.motivo if nr else ""
    assert h.valor == SEXO_FEMENINO


def test_la_evidencia_conserva_los_acentos_del_correo():
    """
    La busqueda se hace sin acentos, pero la cita sale del original: el
    operador tiene que leer lo que escribio el remitente.
    """
    h, _ = extraer_sexo(f"la señora {NOMBRE} presenta la solicitud",
                        nombre_afectado=NOMBRE)
    assert h is not None
    assert "señora" in h.evidencia


# ── Concordancia en primera persona ──

@pytest.mark.parametrize("texto,esperado", [
    ("Quedo atenta a su pronta respuesta", SEXO_FEMENINO),
    ("Quedo atento a su pronta respuesta", SEXO_MASCULINO),
    ("me encuentro afiliada como independiente", SEXO_FEMENINO),
    ("la suscrita solicita el certificado", SEXO_FEMENINO),
    ("el suscrito solicita el certificado", SEXO_MASCULINO),
    ("soy trabajadora de la empresa", SEXO_FEMENINO),
])
def test_quien_escribe_se_concuerda_a_si_mismo(texto, esperado):
    """
    Cuando quien escribe es el afectado, su propia concordancia declara el
    sexo: es una afirmacion sobre si mismo, no una deduccion del nombre.
    """
    h, nr = extraer_sexo("Solicito el certificado", texto_del_remitente=texto)
    assert h is not None, nr.motivo if nr else ""
    assert h.valor == esperado
    assert h.metodo == "concordancia_en_primera_persona"


def test_la_primera_persona_no_cuenta_si_el_remitente_no_es_el_afectado():
    """
    La concordancia de quien escribe describe a quien escribe. Si el afectado
    es otro, tomarla le pone a esa otra persona el sexo del remitente.
    """
    _, nr = extraer_sexo("Solicito el pago", texto_del_remitente="")
    assert nr.motivo == "SEXO_NO_DECLARADO"


def test_las_dos_formas_en_primera_persona_no_deciden():
    h, nr = extraer_sexo(
        "Solicito el certificado",
        texto_del_remitente="quedo atento y agradecida por su gestion")
    assert h is None
    assert nr.motivo == "SEXO_NO_DECLARADO"


def test_la_concordancia_junto_al_nombre_manda_sobre_la_primera_persona():
    """
    La marca junto al nombre habla del afectado directamente; la de primera
    persona solo llega a el porque coinciden.
    """
    h, _ = extraer_sexo(f"la señora {NOMBRE} presenta la solicitud",
                        nombre_afectado=NOMBRE,
                        texto_del_remitente="quedo atento")
    assert h.valor == SEXO_FEMENINO
    assert h.metodo == "concordancia_junto_al_nombre"


# ── Nunca se deduce del nombre ──

@pytest.mark.parametrize("nombre", [
    "MARIA FERNANDA GOMEZ", "JOSE ANTONIO PEREZ", "ANDREA SOLIS MORA",
])
def test_un_nombre_sin_concordancia_no_da_sexo(nombre):
    texto = f"Solicito revision del caso de {nombre} con CC 41234567"
    assert _sexo(texto, nombre) is None
    assert _motivo(texto, nombre) == "SEXO_SIN_CONCORDANCIA_UTILIZABLE"


def test_sin_nombre_del_afectado_no_hay_concordancia_que_leer():
    _, nr = extraer_sexo("mi trabajadora presenta la solicitud",
                         nombre_afectado="")
    assert nr.motivo == "SEXO_NO_DECLARADO"


def test_una_marca_lejana_no_cuenta():
    """
    La concordancia tiene que estar junto al nombre: un correo menciona a
    varias personas y una marca suelta pertenece a cualquiera de ellas.
    """
    relleno = "detalle del tramite " * 8
    texto = f"el trabajador PEDRO RUIZ MEJIA {relleno} {NOMBRE} con CC 41234567"
    assert len(relleno) > VENTANA_DE_CONCORDANCIA
    assert _sexo(texto) is None


def test_el_texto_vacio_se_reporta_como_tal():
    _, nr = extraer_sexo("", nombre_afectado=NOMBRE)
    assert nr.motivo == "TEXTO_VACIO"


# ── Las formas femenina y masculina no se confunden ──

@pytest.mark.parametrize("palabra,esperado", [
    ("trabajadora", SEXO_FEMENINO), ("trabajador", SEXO_MASCULINO),
    ("afiliada", SEXO_FEMENINO), ("afiliado", SEXO_MASCULINO),
    ("pensionada", SEXO_FEMENINO), ("pensionado", SEXO_MASCULINO),
    ("señora", SEXO_FEMENINO), ("señor", SEXO_MASCULINO),
])
def test_la_forma_larga_no_activa_tambien_la_corta(palabra, esperado):
    """
    "trabajador" esta dentro de "trabajadora": sin frontera de palabra, toda
    forma femenina activaria tambien la masculina y se leeria contradiccion.
    """
    h, nr = extraer_sexo(f"la {palabra} {NOMBRE} solicita",
                         nombre_afectado=NOMBRE)
    assert h is not None, f"{palabra}: {nr.motivo if nr else ''}"
    assert h.valor == esperado


# ── Lo que escribe la plantilla no es una afirmacion sobre nadie ──

@pytest.mark.parametrize("plantilla", [
    "identificado(a)", "identificado (a)", "identificado/a",
    "Señor(a)", "afiliado(a)", "trabajador (a)",
])
def test_la_forma_doble_del_formulario_no_declara_el_sexo(plantilla):
    """
    Un formulario impreso no sabe a quien va a atender y escribe las dos
    formas. Leer la masculina escribe en la base el sexo equivocado de la mitad
    de las personas que llenan ese papel.
    """
    h, nr = extraer_sexo(f"{NOMBRE} {plantilla} con CC 41234567",
                         nombre_afectado=NOMBRE)
    assert h is None
    assert nr.motivo == "SEXO_SIN_CONCORDANCIA_UTILIZABLE"


def test_la_misma_palabra_sin_la_forma_doble_si_declara():
    """
    Se anula la aparicion concreta, no la palabra: el mismo documento puede
    traer la plantilla arriba y una mencion real mas abajo.
    """
    texto = f"Señor(a):\n{NOMBRE}\nRespetado Señor:\nen atencion a su oficio"
    h, _ = extraer_sexo(texto, nombre_afectado=NOMBRE)
    assert h is not None
    assert h.valor == SEXO_MASCULINO


def test_el_titulo_del_adjunto_no_declara_el_sexo():
    """
    El encabezado lleva el nombre del formulario, no una afirmacion: "Trabajador
    Dependiente" es el tipo de planilla y suele venir con el nombre al lado,
    dentro de la ventana de concordancia.
    """
    texto = (f"# Trabajador Dependiente CONCEJAL {NOMBRE}.xlsx\n\n"
             f"| CC | 41234567 | {NOMBRE} | 1963-03-28 |")
    h, nr = extraer_sexo(texto, nombre_afectado=NOMBRE)
    assert h is None
    assert nr.motivo == "SEXO_SIN_CONCORDANCIA_UTILIZABLE"


@pytest.mark.parametrize("frase,esperado", [
    (f"PACIENTE FEMENINA DE 43 AÑOS, {NOMBRE}", SEXO_FEMENINO),
    (f"paciente masculino de 38 años, {NOMBRE}", SEXO_MASCULINO),
    (f"{NOMBRE} sexo femenino", SEXO_FEMENINO),
])
def test_el_adjetivo_suelto_declara_el_sexo(frase, esperado):
    """
    Una historia clinica lo escribe asi, sin usar la palabra "sexo" y sin
    concordar con ningun sustantivo: "PACIENTE FEMENINA DE 43 AÑOS".
    """
    h, nr = extraer_sexo(frase, nombre_afectado=NOMBRE)
    assert h is not None, nr.motivo if nr else ""
    assert h.valor == esperado


def test_la_tabla_de_codigos_del_formulario_no_declara_el_sexo():
    """
    Una planilla trae la tabla de codigos con las dos formas pegadas. Ahi las
    dos marcas caen igual de cerca del nombre y abstenerse es lo correcto.
    """
    texto = f"{NOMBRE}\n| M | Masculino |\n| F | Femenino |\n"
    h, nr = extraer_sexo(texto, nombre_afectado=NOMBRE)
    assert h is None
    assert nr.motivo == "SEXO_SIN_CONCORDANCIA_UTILIZABLE"


def test_una_marca_en_el_cuerpo_cuenta_aunque_haya_titulos():
    texto = (f"# Formulario de afiliacion.pdf\n\n"
             f"la trabajadora {NOMBRE} presenta la solicitud")
    h, _ = extraer_sexo(texto, nombre_afectado=NOMBRE)
    assert h is not None
    assert h.valor == SEXO_FEMENINO
