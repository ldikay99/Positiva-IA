"""
El telefono se valida por la estructura del numero, no por una lista.

El campo de SGDEA se llama "telefono celular". Guardar ahi una cedula, un
numero de recibo o un fijo de conmutador deja un contacto que no sirve.
"""
import pytest

from pipeline.extraccion import extraer_telefono
from utils.contacto_extractor import extraer_telefonos, mejor_telefono


def _numeros(texto):
    return [t["numero"] for t in extraer_telefonos(texto)]


# ── Numeracion colombiana ──

@pytest.mark.parametrize("texto,esperado,tipo", [
    ("Celular 3105551234", "3105551234", "celular"),
    ("Cel: 310 555 1234", "3105551234", "celular"),
    ("Tel +57 310 555 1234", "3105551234", "celular"),
    ("Telefono 6013334444", "6013334444", "fijo"),
])
def test_los_formatos_colombianos_se_reconocen(texto, esperado, tipo):
    tels = extraer_telefonos(texto)
    assert tels, texto
    assert tels[0]["numero"] == esperado
    assert tels[0]["tipo"] == tipo


def test_un_fijo_de_siete_digitos_ya_no_es_un_telefono():
    """Colombia usa numeracion de diez digitos desde 2022: siete no bastan."""
    assert not _numeros("Tel 3334444")


# ── Dos telefonos en la misma linea ──

@pytest.mark.parametrize("texto,esperados", [
    ("Teléfono: 3111701816 - 3171380215", ["3111701816", "3171380215"]),
    ("CEL 3504754866 - 3147135330", ["3504754866", "3147135330"]),
    ("Cel 3105551234 / 3171380215", ["3105551234", "3171380215"]),
    ("Contacto 3105551234, 3171380215", ["3105551234", "3171380215"]),
])
def test_dos_telefonos_en_una_lista_no_se_pegan(texto, esperados):
    """Dos telefonos en una lista son dos numeros distintos, no uno de doce digitos."""
    numeros = _numeros(texto)
    for esperado in esperados:
        assert esperado in numeros, f"{texto} -> {numeros}"


@pytest.mark.parametrize("texto,esperado", [
    ("Cel 310-555-1234", "3105551234"),
    ("Tel (601) 333 4444", "6013334444"),
    ("Celular 310.555.1234", "3105551234"),
])
def test_el_guion_interno_sigue_siendo_parte_del_numero(texto, esperado):
    """Solo el guion con espacio a los dos lados separa dos numeros."""
    assert esperado in _numeros(texto), texto


@pytest.mark.parametrize("prefijo", ["380", "390", "361", "393", "307"])
def test_un_prefijo_movil_que_no_existe_se_rechaza(prefijo):
    """
    Diez digitos empezando en 3 no basta: el prefijo tiene que estar asignado.
    Los reales son 300-305, 310-324 y 350-351.
    """
    assert not _numeros(f"Celular {prefijo}5551234")


# ── El numero de otro pais tambien se valida ──

@pytest.mark.parametrize("texto", [
    "Contacto +265840403391",     # +265 es Malawi y ese numero no existe
    "Tel +999 123 456 789",       # indicativo inexistente
])
def test_un_numero_internacional_que_no_existe_se_rechaza(texto):
    """Un numero internacional se valida contra el plan de su pais, no solo por su longitud."""
    assert not _numeros(texto), texto


def test_un_numero_internacional_valido_si_se_toma():
    """
    Perder el telefono de un afectado extranjero es perder al mas dificil de
    contactar: la validacion no puede llevarse por delante los que si existen.
    """
    numeros = _numeros("Contacto +1 202 555 0143")
    assert numeros and numeros[0].startswith("+1")


def test_un_internacional_rechazado_no_reaparece_partido():
    """
    Su tramo queda reservado: si el barrido colombiano lo releyera, produciria
    un candidato falso con los digitos cortados.
    """
    for numero in _numeros("Contacto +265840403391"):
        assert "840403391" not in numero


def test_una_linea_018000_se_reconoce_como_numero_valido():
    """
    Es un numero real segun el plan de numeracion. Se comprueba la
    clasificacion, no la extraccion: en el correo estas lineas van precedidas
    de "linea nacional" y se descartan aparte, porque son el conmutador de la
    empresa y no el contacto del afectado.
    """
    from utils.contacto_extractor import _clasificar_telefono

    assert _clasificar_telefono("018000931683", "018000931683") is not None


def test_una_linea_corporativa_no_es_el_contacto_del_afectado():
    """Reconocer el numero no significa usarlo: la 018000 es de la empresa."""
    assert not _numeros("Linea nacional 018000931683")


def test_un_numero_extranjero_con_indicativo_no_se_pierde():
    """
    Descartar lo que no encaja en la numeracion colombiana deja sin telefono
    justo al afectado extranjero, que es al que mas cuesta contactar.
    """
    tels = extraer_telefonos("Celular +58 412 1234567")
    assert tels, "se perdio un numero con indicativo de otro pais"
    assert tels[0]["tipo"] == "internacional"
    assert tels[0]["numero"].startswith("+58")


# ── Lo que NO es un telefono ──

@pytest.mark.parametrize("texto", [
    "identificado con CC 3163177477",
    "cedula 3679265374",
    "NIT 3001234567",
    "radicado 3105551234",
])
def test_un_documento_no_pasa_por_telefono(texto):
    """
    Una cedula que empieza por 3 y tiene diez digitos parece un celular. Lo que
    lo desmiente es la palabra que la acompaña.
    """
    assert not _numeros(texto), f"tomo un documento como telefono: {texto}"


def test_un_fijo_suelto_en_un_pie_de_pagina_no_cuenta():
    """Sin palabra de contacto, un fijo suele ser el conmutador de la empresa."""
    assert not _numeros("Carrera 7 No 32-12 6013334444 Bogota")


# ── Prioridad: el campo destino es "celular" ──

def test_el_celular_gana_al_fijo_aunque_aparezca_despues():
    texto = "Telefono fijo 6013334444 y celular 3105551234"
    assert mejor_telefono(texto) == "3105551234"


def test_el_tipo_queda_en_la_evidencia():
    """Un fijo guardado como celular es un dato que despues nadie puede usar."""
    h, _ = extraer_telefono("Telefono 6013334444")
    assert h is not None
    assert h.metodo == "telefono_fijo"
    assert "fijo" in h.evidencia


def test_sin_telefono_se_escala_con_motivo():
    h, nr = extraer_telefono("Buen dia, adjunto los soportes")
    assert h is None
    assert nr.motivo == "SIN_TELEFONO"
