"""
Pruebas del anonimizador del corpus.

Lo critico aqui no es que oculte datos, es que los oculte CONSISTENTEMENTE y
PRESERVANDO LA ESTRUCTURA. Un anonimizador que rompe la estructura produce un
corpus que no prueba nada: si la cedula del asunto y la del adjunto salen
distintas, el caso deja de ejercitar la desambiguacion de documentos.
"""
import pytest

from servidor_mcp.herramientas import _digito_verificacion_nit

from harness.anonimizar import Anonimizador, fugas_de_original, verificar_sin_pii


@pytest.fixture
def anon():
    return Anonimizador(sal="prueba")


# ── Consistencia: la propiedad que hace util al corpus ──

def test_el_mismo_documento_da_siempre_el_mismo_reemplazo(anon):
    a = anon.documento("41234567")
    b = anon.documento("41234567")
    assert a == b


def test_el_mismo_documento_con_separadores_da_el_mismo_reemplazo(anon):
    """'41.234.567' y '41234567' son el mismo documento."""
    assert anon.documento("41.234.567") == anon.documento("41234567")


def test_documentos_distintos_dan_reemplazos_distintos(anon):
    assert anon.documento("41234567") != anon.documento("80123456")


# ── Fidelidad: el corpus tiene que poder ejercitar las reglas ──

@pytest.mark.parametrize("nit", [
    "9001234567", "8300456781", "8909012345", "9012345678",
])
def test_un_nit_falso_conserva_su_digito_de_verificacion(anon, nit):
    """El NIT falso sigue cuadrando con su digito, para que la compuerta lo
    acepte igual que al original."""
    falso = anon.documento(nit)
    assert len(falso) == len(nit)
    assert int(falso[9]) == _digito_verificacion_nit(falso[:9]), falso


def test_una_cedula_no_se_toca_por_la_regla_del_nit(anon):
    """La regla del digito solo aplica a diez cifras: una cedula de 8 no."""
    falso = anon.documento("41234567")
    assert len(falso) == 8


def test_el_nit_falso_sigue_siendo_estable(anon):
    """Recalcular el digito no puede romper la consistencia."""
    assert anon.documento("9001234567") == anon.documento("900.123.456-7".replace("-", ""))


# ── Alcance del anonimizador ──

def test_se_detectan_los_nombres_que_no_alcanzo_a_sustituir():
    """
    Un nombre que solo aparece en el texto de un adjunto no entra al mapa, asi
    que `fugas_de_original` no lo ve: lo reporta `nombres_probablemente_reales`.
    """
    from harness.anonimizar import nombres_probablemente_reales

    texto = "Historia clinica de WILMER ANTONIO ZAPATA GIRALDO, folio 3"
    assert "WILMER ANTONIO ZAPATA GIRALDO" in nombres_probablemente_reales(texto)


def test_un_nombre_del_vocabulario_falso_no_se_reporta():
    """Si marcara los falsos, el aviso seria ruido y nadie lo miraria."""
    from harness.anonimizar import nombres_probablemente_reales

    assert not nombres_probablemente_reales(
        "Cordialmente, Alonso Montoya Galindo")


@pytest.mark.parametrize("prosa", [
    "COMERCIO AL POR MAYOR DE PRODUCTOS TEXTILES",
    "OTRAS ACTIVIDADES CLASIFICADOS BAJO OTROS EPIGRAFES",
    "EXHIBICION DE PELICULAS CINEMATOGRAFICAS",
])
def test_la_prosa_en_mayuscula_no_se_reporta_como_nombre(prosa):
    """El catalogo de actividades economicas viene entero en mayusculas: dos palabras en mayuscula no son un nombre."""
    from harness.anonimizar import nombres_probablemente_reales

    assert not nombres_probablemente_reales(prosa)


@pytest.mark.parametrize("texto,nombre", [
    ("JUAN PEREZ MORENO identificado con CC 41234567", "JUAN PEREZ MORENO"),
    ("El trabajador WILMER ZAPATA GIRALDO, con incapacidad vigente",
     "WILMER ZAPATA GIRALDO"),
    ("Atentamente,\nCARID HERNANDEZ PALACIOS", "CARID HERNANDEZ PALACIOS"),
    ("Historia clinica de WILMER ANTONIO ZAPATA GIRALDO, folio 3",
     "WILMER ANTONIO ZAPATA GIRALDO"),
])
def test_se_reporta_el_nombre_en_posicion_de_nombre(texto, nombre):
    """Junto a un documento, tras una etiqueta, tras una despedida o tras un
    sustantivo documental: ahi es donde un nombre real delata una fuga."""
    from harness.anonimizar import nombres_probablemente_reales

    assert nombre in nombres_probablemente_reales(texto)


def test_la_misma_sal_reproduce_el_mismo_corpus():
    uno = Anonimizador(sal="fija")
    dos = Anonimizador(sal="fija")
    assert uno.documento("41234567") == dos.documento("41234567")
    assert uno.nombre_persona("Maria Gomez") == dos.nombre_persona("Maria Gomez")


def test_salt_distinta_produce_corpus_distinto():
    uno = Anonimizador(sal="a")
    dos = Anonimizador(sal="b")
    assert uno.documento("41234567") != dos.documento("41234567")


# ── Preservacion de estructura ──

@pytest.mark.parametrize("original", ["123456", "1234567", "41234567", "1016031469"])
def test_el_documento_conserva_la_longitud(anon, original):
    assert len(anon.documento(original)) == len(original)


def test_el_documento_no_empieza_en_cero(anon):
    """Un documento que empieza en 0 no parece real y rompe validaciones."""
    for original in ("123456", "41234567", "1016031469", "999999999"):
        assert not anon.documento(original).startswith("0")


def test_el_documento_es_solo_digitos(anon):
    assert anon.documento("41.234.567").isdigit()


@pytest.mark.parametrize("original,tokens", [
    ("Maria Gomez", 2),
    ("Maria Fernanda Gomez", 3),
    ("Luz Marina Moreno Rojas", 4),
    ("Ana", 1),
])
def test_el_nombre_conserva_la_cantidad_de_tokens(anon, original, tokens):
    assert len(anon.nombre_persona(original).split()) == tokens


def test_el_nombre_conserva_mayusculas(anon):
    assert anon.nombre_persona("MARIA GOMEZ").isupper()


def test_el_nombre_conserva_formato_titulo(anon):
    falso = anon.nombre_persona("Maria Gomez")
    assert falso[0].isupper() and not falso.isupper()


def test_el_telefono_celular_sigue_pareciendo_celular(anon):
    """Los extractores de telefono validan que el celular empiece en 3."""
    falso = anon.telefono("3105551234")
    assert falso.startswith("3")
    assert len(falso) == 10


def test_una_cedula_de_8_digitos_no_se_confunde_con_telefono(anon):
    """
    Una racha de 8 digitos encaja tanto en cedula como en fijo con indicativo.
    Debe dar el mismo reemplazo por las dos vias, o el caso queda inservible.
    """
    limpio = anon.texto("CC 41234567")
    numero = "".join(c for c in limpio if c.isdigit())
    assert numero == anon.documento("41234567")


def test_un_celular_con_contexto_si_se_trata_como_telefono(anon):
    limpio = anon.texto("celular 3105551234")
    numero = "".join(c for c in limpio if c.isdigit())
    assert numero.startswith("3") and len(numero) == 10


def test_un_fijo_con_separador_se_trata_como_telefono(anon):
    limpio = anon.texto("601 333 4444")
    assert "601 333 4444" not in limpio


# ── Numero con indicativo de otro pais ──

@pytest.mark.parametrize("texto,resto_real", [
    ("Contacto +1 202 555 0143", "0143"),
    ("Contacto +265 840 403 391", "391"),
    ("Contacto +34 612 345 678", "678"),
])
def test_un_numero_extranjero_no_se_sustituye_a_medias(anon, texto, resto_real):
    """Un numero extranjero se sustituye entero: dejar los ultimos digitos deja PII real en el corpus."""
    limpio = anon.texto(texto)
    assert resto_real not in limpio, limpio


def test_el_indicativo_de_pais_se_conserva(anon):
    """
    El indicativo de pais identifica un numero extranjero: conservarlo
    mantiene el caso ejercitando el camino internacional del extractor.
    """
    assert anon.texto("Contacto +265840403391").count("+265") == 1
    assert anon.texto("Contacto +1 202 555 0143").startswith("Contacto +1")


def test_un_celular_colombiano_con_indicativo_sigue_siendo_valido(anon):
    """Un celular colombiano con indicativo sigue existiendo despues de anonimizarlo."""
    from utils.contacto_extractor import telefono_existe

    limpio = anon.texto("Cel +57 310 555 1234")
    numero = "+" + "".join(c for c in limpio if c.isdigit())
    assert numero.startswith("+57")
    assert telefono_existe(numero), numero


def test_no_sustituye_dos_veces_el_mismo_dato(anon):
    """
    Un valor falso como clave del mapa delata que se sustituyo dos veces el
    mismo dato.
    """
    anon.texto("celular 3105551234 y cedula 41234567")
    falsos = set(anon.mapa.values())
    claves = set(anon.mapa.keys())
    assert not (falsos & claves), (
        f"un valor falso quedo como clave, hubo doble sustitucion: {falsos & claves}"
    )


# ── Dominios institucionales: el pipeline decide con ellos ──

def test_preserva_el_dominio_de_positiva(anon):
    """is_buzon_no_humano depende de este dominio: si se cambia, el caso miente."""
    assert anon.correo("alguien@positiva.gov.co").endswith("@positiva.gov.co")


def test_generaliza_otros_dominios_gov(anon):
    assert anon.correo("juridica@indesa.gov.co").endswith("@entidad-publica.gov.co")


def test_reemplaza_dominios_privados(anon):
    falso = anon.correo("contacto@mywak.com.co")
    assert "mywak" not in falso
    assert "@" in falso


def test_conserva_el_handle_generico(anon):
    """'contacto@' es un buzon generico: el handle importa para reproducir el caso."""
    assert anon.correo("contacto@mywak.com.co").startswith("contacto@")


def test_reemplaza_la_parte_local_personal(anon):
    """'jorge.pino@' identifica a una persona: hay que cambiarlo."""
    falso = anon.correo("jorge.pino@secutek.com.co")
    assert "jorge" not in falso.lower()
    assert "pino" not in falso.lower()
    assert "." in falso.split("@")[0]  # sigue pareciendo nombre.apellido


# ── Anonimizacion de texto libre ──

def test_anonimiza_un_cuerpo_completo(anon):
    original = (
        "Buen dia, adjunto la incapacidad del trabajador con CC 41234567.\n"
        "Cualquier duda al 3105551234 o a jorge.pino@secutek.com.co\n"
        "Radicado anterior: ENT20260001618738"
    )
    limpio = anon.texto(original)
    assert "41234567" not in limpio
    assert "3105551234" not in limpio
    assert "jorge.pino@secutek.com.co" not in limpio
    assert "ENT20260001618738" not in limpio
    # La estructura sobrevive
    assert "adjunto la incapacidad del trabajador con CC" in limpio
    assert "ENT" in limpio


def test_el_mismo_documento_en_dos_lugares_queda_igual(anon):
    """La propiedad mas importante: si no se cumple, el corpus es inservible."""
    limpio = anon.texto("CC 41234567 en el asunto y 41.234.567 en el adjunto")
    numeros = [t for t in limpio.replace(".", " ").split() if t.isdigit()]
    assert len(set(numeros)) == 1, f"el mismo documento salio distinto: {numeros}"


def test_sustituye_nombres_conocidos(anon):
    original = "Cordialmente, Maria Fernanda Gomez, Analista"
    limpio = anon.sustituir_nombres(original, ["Maria Fernanda Gomez"])
    assert "Maria Fernanda Gomez" not in limpio
    assert "Cordialmente," in limpio
    assert "Analista" in limpio


def test_texto_vacio_o_nulo_no_revienta(anon):
    assert anon.texto("") == ""
    assert anon.texto(None) is None
    assert anon.sustituir_nombres("", []) == ""


# ── Red de seguridad ──

def test_verificar_detecta_pii_que_se_escapo():
    hallazgos = verificar_sin_pii("contacto: juan@empresa.com CC 41234567 tel 3105551234")
    tipos = {h.split(":")[0] for h in hallazgos}
    assert "correo" in tipos
    assert "documento" in tipos or "telefono" in tipos


def test_verificar_no_marca_texto_limpio():
    assert verificar_sin_pii("Solicito una copia del certificado laboral") == []


def test_la_compuerta_no_marca_su_propia_salida(anon):
    """
    Un documento falso sigue pareciendo un documento: `verificar_sin_pii` marca
    la salida anonimizada. La compuerta compara contra los originales.
    """
    original = "CC 41234567, tel 310 555 1234, correo jorge.pino@secutek.com.co"
    limpio = anon.texto(original)
    # El detector de patrones si marca la salida: eso es esperado
    assert verificar_sin_pii(limpio), "la salida falsa deberia parecer PII"
    # Pero la compuerta correcta la aprueba, porque no sobrevivio ningun original
    assert fugas_de_original(anon, limpio) == []


def test_la_compuerta_detecta_una_fuga_real(anon):
    anon.texto("CC 41234567")
    # Texto donde el original SI sobrevivio
    assert fugas_de_original(anon, "el documento es 41234567") != []


def test_la_compuerta_no_marca_lo_preservado_a_proposito(anon):
    """
    Los buzones genericos institucionales se preservan por diseño: son roles,
    no personas, y el pipeline decide con ellos. La compuerta no debe tratarlos
    como fuga, o descarta casos validos en masa.
    """
    generico = "postmaster@positiva.gov.co"
    falso = anon.correo(generico)
    assert falso == generico, "este caso se preserva por diseño"
    assert fugas_de_original(anon, f"reenviado por {falso}") == []


def test_la_compuerta_ignora_originales_muy_cortos(anon):
    """Rachas cortas coinciden por azar y no identifican a nadie."""
    anon.documento("123")
    assert fugas_de_original(anon, "el numero 123 aparece aqui") == []


def test_el_texto_anonimizado_no_conserva_el_original(anon):
    """Extremo a extremo: nada del original sobrevive en el resultado."""
    original = "CC 41234567, tel 3105551234, correo jorge.pino@secutek.com.co"
    limpio = anon.texto(original)
    for secreto in ("41234567", "3105551234", "jorge.pino", "secutek"):
        assert secreto not in limpio, f"se filtro: {secreto}"


@pytest.mark.parametrize("numero", [
    "14921480153",        # planilla PILA
    "141240994318",       # referencia interna
    "202607171458488",    # radicado sin el rotulo ENT
])
def test_una_racha_larga_de_digitos_no_queda_intacta(anon, numero):
    """
    Un numero de 11 a 15 digitos no encaja en la alternativa de documento,
    que topa en 10, ni en la de radicado, que exige el prefijo ENT: necesita
    su propio patron.
    """
    limpio = anon.texto(f"planilla {numero} del periodo")
    assert numero not in limpio
    assert "planilla" in limpio and "del periodo" in limpio


def test_la_racha_larga_conserva_su_longitud(anon):
    """El reemplazo tiene que seguir pareciendo el mismo tipo de referencia."""
    limpio = anon.texto("radicado 202607171458488 recibido")
    digitos = [t for t in limpio.split() if t.isdigit()]
    assert digitos and len(digitos[0]) == len("202607171458488")


def test_los_patrones_individual_y_combinado_no_divergen():
    """
    El patron de cada tipo se declara una vez y de ahi salen los dos usos. Si
    se escribieran por separado, corregir uno dejaria el otro viejo y la
    deteccion no coincidiria con la sustitucion.
    """
    from harness import anonimizar as a

    assert a._RE_DOCUMENTO.pattern == a._PAT_DOCUMENTO
    assert a._RE_TELEFONO.pattern == a._PAT_TELEFONO
    assert a._RE_EMAIL.pattern == a._PAT_CORREO
    for nombre, patron in (("documento", a._PAT_DOCUMENTO),
                           ("telefono", a._PAT_TELEFONO),
                           ("correo", a._PAT_CORREO),
                           ("radicado", a._PAT_RADICADO)):
        assert f"(?P<{nombre}>{patron})" in a._RE_TODO_PII.pattern
