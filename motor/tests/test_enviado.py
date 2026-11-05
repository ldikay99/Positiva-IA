"""
La medida de lo que llega a SGDEA.

Mide el payload, no el extractor, y por eso distingue dos cosas que el resto de
las herramientas cuentan juntas: un valor por defecto que AFIRMA un dato
equivocado y uno que se ABSTIENE.
"""
import pytest

from harness.caso import CAMPOS_EVALUABLES, Caso, Esperado
from harness.enviado import (
    CAMPO_EN_EL_PAYLOAD,
    Enviado,
    _correo_de,
    _es_abstencion,
)
from pipeline.radicador import SEXO_POR_DEFECTO, armar_payload


# ── El mapeo no puede separarse del contrato del payload ──

def test_todos_los_campos_medidos_existen_en_el_payload():
    """
    Las claves salen de `RadicacionPayload.to_form_data`. Si alguna se renombra,
    la herramienta mediria None contra la etiqueta y lo llamaria fallo.
    """
    claves = armar_payload(
        _correo_de(Caso(id="c", asunto="a", remitente_nombre="Ana Lopez",
                        remitente_correo="ana@empresa.com")),
        {},
    ).to_form_data()
    faltan = [c for c in CAMPO_EN_EL_PAYLOAD.values() if c not in claves]
    assert not faltan, faltan


def test_todos_los_campos_medidos_son_campos_evaluables_del_corpus():
    ajenos = [c for c in CAMPO_EN_EL_PAYLOAD if c not in CAMPOS_EVALUABLES]
    assert not ajenos, ajenos


def test_se_mide_todo_campo_evaluable_que_viaja_en_el_payload():
    """`accion` no va en el payload: la decide el pipeline, no SGDEA."""
    sin_medir = [c for c in CAMPOS_EVALUABLES if c not in CAMPO_EN_EL_PAYLOAD]
    assert sin_medir == ["accion"], sin_medir


# ── Abstenerse no es afirmar un dato falso ──

@pytest.mark.parametrize("campo,valor", [
    ("numero_documento_remitente", ""),
    ("nombre_afectado", ""),
    ("telefono_afectado", None),
    ("sexo_afectado", SEXO_POR_DEFECTO),
    ("sexo_afectado", str(SEXO_POR_DEFECTO)),
])
def test_el_campo_vacio_o_no_determinado_es_abstencion(campo, valor):
    assert _es_abstencion(campo, valor)


@pytest.mark.parametrize("campo,valor", [
    ("sexo_afectado", 1),
    ("sexo_afectado", 2),
    ("numero_documento_afectado", "41234567"),
    ("tipo_remitente", 2),
    ("tipo_solicitud", 14),
])
def test_un_valor_concreto_no_es_abstencion(campo, valor):
    """
    El 14 de tipo_solicitud es "Otra", pero es una categoria del catalogo: se
    envia como dato y decide la oficina.
    """
    assert not _es_abstencion(campo, valor)


# ── Los conteos cuadran ──

def _caso(**esperado):
    return Caso(
        id="caso-0001",
        asunto="Solicitud de incapacidad",
        remitente_nombre="Ana Lopez",
        remitente_correo="ana@empresa.com",
        cuerpo_md="El trabajador MUÑOZ PEÑA JOSE con CC 41234567 solicita.",
        esperado=Esperado(**esperado),
    )


def test_cada_fallo_es_o_afirmacion_o_abstencion():
    """
    Los dos contadores parten los fallos sin solaparse: si no suman, uno de los
    dos esta contando de mas.
    """
    medida = Enviado()
    medida.medir(_caso(tipo_remitente=1, sexo_afectado=2,
                       numero_documento_remitente="99999999"))
    afirma = sum(m.afirma_mal for m in medida.por_campo.values())
    abstiene = sum(m.se_abstiene for m in medida.por_campo.values())
    assert afirma + abstiene == len(medida.fallos)


def test_un_campo_sin_etiqueta_no_se_cuenta():
    medida = Enviado()
    medida.medir(_caso())
    assert medida.por_campo == {}
    assert medida.fallos == []
    assert medida.casos == 1
