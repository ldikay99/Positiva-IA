"""
Longitud valida de un documento segun su tipo.

Cada entidad emisora fija el rango de su documento:
  CC   5-10   las expedidas antes de los setenta son mas cortas
  TI   10-11  NUIP del menor
  NP   10-11  el registro civil usa el mismo NUIP
  CE   6-7    las asigna Migracion Colombia
  PPT  8-9    permiso por proteccion temporal
  PE   15     permiso especial de permanencia
  NI   9-10   nueve digitos mas el de verificacion
  PA          alfanumerico: no se mide por digitos
"""
import pytest

from models.enums import TipoDocumento
from servidor_mcp.herramientas import Contexto, validar_documento
from utils.tipos_documento import (
    LONGITUD_POR_TIPO,
    MAX_DIGITOS,
    MIN_DIGITOS,
    es_pasaporte,
    longitud_valida,
    rango_aceptable,
    tipos_compatibles,
)


@pytest.fixture
def ctx():
    return Contexto(workspace=None)


def _observaciones(ctx, numero, tipo=""):
    return " | ".join(validar_documento(ctx, numero, tipo).datos["observaciones"])


# ── La tabla es coherente ──

def test_todo_rango_tiene_minimo_menor_o_igual_al_maximo():
    for codigo, (minimo, maximo) in LONGITUD_POR_TIPO.items():
        assert 0 < minimo <= maximo, codigo


def test_los_limites_salen_de_la_tabla():
    """`MIN_DIGITOS` y `MAX_DIGITOS` se derivan de `LONGITUD_POR_TIPO`."""
    assert MIN_DIGITOS == min(m for m, _ in LONGITUD_POR_TIPO.values())
    assert MAX_DIGITOS == max(m for _, m in LONGITUD_POR_TIPO.values())


def test_los_codigos_de_la_tabla_existen_en_sgdea():
    validos = {int(t) for t in TipoDocumento}
    assert set(LONGITUD_POR_TIPO) <= validos


# ── Numeros fuera de todo rango ──

@pytest.mark.parametrize("basura", ["123", "12", "1", "0", "1234"])
def test_un_numero_demasiado_corto_no_es_un_documento(ctx, basura):
    """Por debajo del minimo de la tabla no encaja con ningun tipo."""
    assert "no corresponden a ningun tipo" in _observaciones(ctx, basura)
    assert not tipos_compatibles(basura)


def test_un_numero_demasiado_largo_tampoco(ctx):
    assert "no corresponden a ningun tipo" in _observaciones(ctx, "1" * 18)


@pytest.mark.parametrize("con_letras", ["ABC", "12A45", "cedula"])
def test_algo_con_letras_no_pasa_como_numero(ctx, con_letras):
    r = validar_documento(ctx, con_letras)
    assert not r.ok
    assert "no son digitos" in r.error


def test_el_rechazo_dice_cual_es_el_rango(ctx):
    """Un rechazo sin explicacion no se puede revisar."""
    obs = _observaciones(ctx, "123")
    assert str(MIN_DIGITOS) in obs and str(MAX_DIGITOS) in obs


# ── Extranjeros: cada documento con su rango ──

@pytest.mark.parametrize("numero,tipo", [
    ("123456", TipoDocumento.CE),          # cedula de extranjeria
    ("1234567", TipoDocumento.CE),
    ("12345678", TipoDocumento.PPT),       # permiso por proteccion temporal
    ("123456789", TipoDocumento.PPT),
    ("123456789012345", TipoDocumento.PE), # permiso especial de permanencia
])
def test_un_documento_de_extranjero_encaja_con_su_tipo(numero, tipo):
    assert longitud_valida(numero, int(tipo)) is True


def test_un_pep_de_quince_digitos_no_se_descarta_por_largo(ctx):
    assert "no corresponden a ningun tipo" not in _observaciones(
        ctx, "123456789012345", "PE")


@pytest.mark.parametrize("pasaporte", ["AV1234567", "C01234567", "123456789"])
def test_un_pasaporte_no_se_mide_por_digitos(pasaporte):
    assert es_pasaporte(pasaporte)
    assert longitud_valida(pasaporte, int(TipoDocumento.PA)) is None


# ── Cedula de ciudadania ──

@pytest.mark.parametrize("numero", ["23456", "234567", "41234567", "1012345678"])
def test_una_cedula_de_cualquier_epoca_encaja(numero):
    """El rango de la CC cubre desde cinco hasta diez digitos."""
    assert longitud_valida(numero, int(TipoDocumento.CC)) is True


def test_una_cedula_no_encaja_como_permiso_especial():
    assert longitud_valida("41234567", int(TipoDocumento.PE)) is False


# ── Sin tipo declarado no se supone nada ──

def test_sin_tipo_no_se_afirma_ni_se_niega():
    """Sin tipo la comprobacion no se puede hacer: devuelve None, no False."""
    assert longitud_valida("41234567", None) is None


def test_sin_tipo_se_dice_con_cuales_es_compatible(ctx):
    obs = _observaciones(ctx, "1012345678")
    assert "no se declaro el tipo" in obs
    assert "Tarjeta de identidad" in obs or "Cedula de ciudadania" in obs


def test_seis_digitos_son_compatibles_con_varios_tipos():
    """Es cedula de ciudadania vieja, de extranjeria o carne diplomatico."""
    compatibles = tipos_compatibles("123456")
    assert int(TipoDocumento.CC) in compatibles
    assert int(TipoDocumento.CE) in compatibles


# ── El digito de verificacion es del NIT, no de la cedula ──

def test_una_cedula_de_diez_digitos_no_recibe_objecion_de_nit(ctx):
    """El digito de verificacion solo aplica al NIT."""
    assert "digito de verificacion no cuadra" not in _observaciones(
        ctx, "1012345678", "CC")


# ── El NIT de una persona natural es su cedula ──

@pytest.mark.parametrize("numero", ["23456", "6279177", "81367440"])
def test_un_nit_mas_corto_que_nueve_no_se_objeta(numero):
    """
    Un empleador puede ser persona natural, y entonces su NIT es su cedula.
    Objetarlo por longitud bloquea una radicacion valida.
    """
    assert longitud_valida(numero, int(TipoDocumento.NI)) is True


def test_el_nit_corto_no_se_sugiere_a_partir_del_numero():
    """
    Aceptar el NIT corto no lo convierte en candidato.

    `tipos_compatibles` responde de que tipo PUEDE ser un numero suelto, y
    ofrecer NIT para cualquier cedula empujaria a tipificar como empresa a un
    trabajador.
    """
    assert int(TipoDocumento.NI) not in tipos_compatibles("81367440")


def test_las_dos_preguntas_sobre_la_longitud_no_son_la_misma():
    """`rango_aceptable` solo se ensancha donde la tabla se queda corta."""
    for codigo in LONGITUD_POR_TIPO:
        tipico = LONGITUD_POR_TIPO[codigo]
        aceptable = rango_aceptable(codigo)
        assert aceptable[0] <= tipico[0] and aceptable[1] >= tipico[1]
    assert rango_aceptable(int(TipoDocumento.NI)) != LONGITUD_POR_TIPO[
        int(TipoDocumento.NI)]


def test_declarar_el_tipo_como_cedula_se_respeta(ctx):
    """El tipo declarado manda sobre cualquier deduccion por longitud."""
    datos = validar_documento(ctx, "1012345678", "CC").datos
    assert "digito_verificacion_coincide" not in datos


def test_un_nit_declarado_si_se_verifica(ctx):
    from servidor_mcp.herramientas import _digito_verificacion_nit

    base = "900123456"
    malo = base + str((_digito_verificacion_nit(base) + 1) % 10)
    assert "digito de verificacion del NIT no cuadra" in _observaciones(
        ctx, malo, "NIT")


def test_un_nit_correcto_no_se_objeta(ctx):
    from servidor_mcp.herramientas import _digito_verificacion_nit

    base = "900123456"
    bueno = base + str(_digito_verificacion_nit(base))
    assert "no cuadra" not in _observaciones(ctx, bueno, "NIT")


def test_sin_tipo_se_informa_el_digito_sin_objetar(ctx):
    """
    Informar no es objetar: el agente recibe el dato para decidir, no una
    razon para descartar.
    """
    datos = validar_documento(ctx, "9001234560").datos
    assert "si_fuera_nit_el_digito_seria" in datos
    obs = " ".join(datos["observaciones"])
    assert "si fuera NIT" in obs
    assert "no cuadra" not in obs
