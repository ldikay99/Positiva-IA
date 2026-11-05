"""
Coherencia entre los catalogos y los valores que produce el pipeline.

La compuerta valida cada campo contra el catalogo de su herramienta, asi que
todo codigo que un extractor pueda emitir —incluidos los valores por defecto
del radicador— tiene que estar en el, y todo catalogo tiene que derivarse de
`models.enums`.
"""
import pytest

from models.enums import TIPO_PQRD_NOMBRE, TipoDocumento, TipoPqrd
from pipeline.extraccion import (
    _MARCAS_TIPO,
    _TIPO_POR_MARCA,
    SEXO_FEMENINO,
    SEXO_MASCULINO,
    extraer_tipo_pqrd,
)
from servidor_mcp.herramientas import CATALOGOS


# ── El catalogo cubre lo que el codigo puede producir ──

def test_todo_tipo_de_documento_de_sgdea_esta_en_el_catalogo():
    faltan = {int(t) for t in TipoDocumento} - set(CATALOGOS["tipo_documento"])
    assert not faltan, f"codigos de SGDEA ausentes del catalogo: {sorted(faltan)}"


def test_el_tipo_de_pqrd_no_es_un_catalogo_consultable():
    """
    Mientras el catalogo exista, `consultar_catalogo` le ofrece al modelo ocho
    valores para un campo que no elige. La unica via por la que podria acabar en
    SGDEA un tipo distinto de Solicitud es que el codigo se lo ofrezca.
    """
    assert "tipo_pqrd" not in CATALOGOS


@pytest.mark.parametrize("codigo", [c for c, _ in _MARCAS_TIPO])
def test_lo_que_produce_el_extractor_de_tipo_documento_esta_en_el_catalogo(codigo):
    assert int(codigo) in CATALOGOS["tipo_documento"], (
        f"el extractor puede devolver {int(codigo)} y la compuerta lo rechazaria")


@pytest.mark.parametrize("bandera,codigo", _TIPO_POR_MARCA)
def test_lo_que_produce_la_marca_del_candidato_esta_en_el_catalogo(bandera, codigo):
    assert int(codigo) in CATALOGOS["tipo_documento"], bandera


def test_lo_que_produce_el_extractor_de_tipo_pqrd_es_el_tipo_del_canal():
    from pipeline.radicador import TIPO_PQRD_DEL_CANAL

    h, _ = extraer_tipo_pqrd("Pago de incapacidad", "cuerpo del correo")
    assert int(h.valor) == TIPO_PQRD_DEL_CANAL
    assert int(h.valor) == int(TipoPqrd.SOLICITUD)


@pytest.mark.parametrize("codigo", [SEXO_MASCULINO, SEXO_FEMENINO])
def test_lo_que_produce_el_extractor_de_sexo_esta_en_el_catalogo(codigo):
    assert int(codigo) in CATALOGOS["sexo"]


# ── El catalogo dice la verdad ──

def test_el_catalogo_de_documentos_coincide_con_el_enum():
    """El catalogo se deriva de models.enums: una segunda copia escrita a mano puede contradecir a SGDEA."""
    from models.enums import TIPO_DOCUMENTO_CODIGO

    for codigo in CATALOGOS["tipo_documento"]:
        assert codigo in TIPO_DOCUMENTO_CODIGO, codigo


def test_el_enum_conserva_los_tipos_de_sgdea():
    """
    `TipoPqrd` es el espejo del catalogo de SGDEA, no una lista de lo que la IA
    puede elegir: recortarlo describiria mal el backend.
    """
    assert set(TIPO_PQRD_NOMBRE) == {int(t) for t in TipoPqrd}


def test_ninguna_descripcion_es_solo_la_sigla_de_sgdea():
    """
    "PT" o "NP" no le dicen nada al modelo, que tiene que elegir entre ellos
    leyendo el correo. La descripcion tiene que explicar el tipo, no repetir
    su codigo. ("NIT" es corto pero si explica: se mide contra la sigla, que
    es "NI", no contra una longitud inventada.)
    """
    from models.enums import TIPO_DOCUMENTO_CODIGO

    inutiles = [
        (c, d) for c, d in CATALOGOS["tipo_documento"].items()
        if str(d).strip().upper() == TIPO_DOCUMENTO_CODIGO.get(c, "").upper()
    ]
    assert not inutiles, f"descripciones que solo repiten la sigla: {inutiles}"


@pytest.mark.parametrize("nombre", sorted(CATALOGOS))
def test_ningun_catalogo_esta_vacio(nombre):
    assert CATALOGOS[nombre], nombre


# ── Valores por defecto del radicador ──

def test_los_valores_por_defecto_del_radicador_estan_en_el_catalogo():
    """Un campo relleno con su valor por defecto sigue pasando la compuerta."""
    from pipeline.radicador import SEXO_POR_DEFECTO, TIPO_SOLICITUD_OTRA

    assert int(SEXO_POR_DEFECTO) in CATALOGOS["sexo"]
    if "tipo_solicitud" in CATALOGOS:
        assert int(TIPO_SOLICITUD_OTRA) in CATALOGOS["tipo_solicitud"]


# ── El enum es espejo de la tabla de PRD ──

def test_el_enum_de_tipo_solicitud_coincide_con_la_tabla_de_prd():
    """
    `business_rules/office_mapping.py` es la copia de
    pqrd_config_tipo_solicitud_oficina_ia. Un codigo en el enum que no este en
    la tabla no tiene oficina y bloquea la radicacion; uno en la tabla que no
    este en el enum sale como 'Otra' sin que nadie lo note.
    """
    from business_rules.office_mapping import OFFICE_MAPPING
    from models.enums import TipoSolicitud

    del_enum = {t.value for t in TipoSolicitud}
    de_la_tabla = set(OFFICE_MAPPING)
    assert del_enum == de_la_tabla, (
        f"solo en el enum: {sorted(del_enum - de_la_tabla)}, "
        f"solo en la tabla: {sorted(de_la_tabla - del_enum)}")


def test_todo_tipo_de_solicitud_tiene_nombre():
    from models.enums import TIPO_SOLICITUD_NOMBRE, TipoSolicitud

    assert set(TIPO_SOLICITUD_NOMBRE) == {t.value for t in TipoSolicitud}
