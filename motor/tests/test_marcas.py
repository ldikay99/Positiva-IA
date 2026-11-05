"""
Las marcas del sistema conviven entre si y con las que pone una persona.

Graph manda la lista entera en cada PATCH: si el calculo de esa lista se
equivoca, la perdida no se ve hasta que un correo se radica dos veces o una
categoria de un funcionario desaparece.
"""
import pytest

from pipeline import marcas
from pipeline.marcas import (
    EN_PROCESO,
    REINTENTO,
    familia_de,
    fusionar,
    marcas_ajenas,
    marcas_del_sistema,
)


# ── De quien es cada categoria ──

@pytest.mark.parametrize("categoria, familia", [
    ("EN-PROCESO:1", EN_PROCESO),
    ("REINTENTO:3", REINTENTO),
    ("REVISION-MANUAL:SIN_DOCUMENTO", marcas.REVISION_MANUAL),
    ("REENVIADO:judicial", marcas.REENVIADO),
    ("ENT20260001602279", marcas.FAMILIA_RADICADO),
    ("RADICADO:2026-abc", marcas.FAMILIA_RADICADO),
])
def test_las_marcas_del_sistema_se_reconocen(categoria, familia):
    assert familia_de(categoria) == familia


@pytest.mark.parametrize("categoria", [
    "Urgente", "Rojo", "Tutela 2026", "Pendiente de revisar", "Entrada",
])
def test_lo_que_puso_una_persona_no_es_de_ninguna_familia(categoria):
    """
    Sin familia, `fusionar` la conserva. Es lo unico que impide que el job
    borre el trabajo de clasificacion de un funcionario.
    """
    assert familia_de(categoria) == ""


def test_entrada_no_se_confunde_con_un_radicado():
    """`ENT` seguido de letras no es un radicado: hace falta el digito."""
    assert familia_de("Entrada") == ""
    assert familia_de("ENT20260001") == marcas.FAMILIA_RADICADO


# ── Fusionar ──

def test_una_marca_sustituye_a_la_de_su_familia():
    assert fusionar(["EN-PROCESO:1"], "EN-PROCESO:2") == ["EN-PROCESO:2"]


def test_una_marca_no_toca_las_de_otras_familias():
    """El contador de aplazamientos sobrevive a la reclamacion."""
    quedan = fusionar(["REINTENTO:3"], "EN-PROCESO:1")
    assert "REINTENTO:3" in quedan
    assert "EN-PROCESO:1" in quedan


def test_la_categoria_de_un_funcionario_sobrevive_a_todo():
    quedan = ["Urgente - Tutela"]
    for marca in ("EN-PROCESO:1", "REINTENTO:1", "ENT20260001"):
        quedan = fusionar(quedan, marca)
    assert "Urgente - Tutela" in quedan


def test_al_cerrar_se_retiran_las_marcas_de_proceso():
    """
    El desenlace ya consta en su propia marca. Dejar EN-PROCESO puesto haria
    que la corrida siguiente leyera trabajo a medias donde no lo hay.
    """
    quedan = fusionar(
        ["Urgente", "EN-PROCESO:2", "REINTENTO:1"], "ENT20260001",
        quitar_de_proceso=True)
    assert quedan == ["Urgente", "ENT20260001"]


def test_el_radicado_sobrevive_a_la_limpieza_de_proceso():
    quedan = fusionar(["ENT20260001", "EN-PROCESO:1"], "", quitar_de_proceso=True)
    assert quedan == ["ENT20260001"]


def test_poner_dos_veces_la_misma_marca_no_la_duplica():
    assert fusionar(["EN-PROCESO:1"], "EN-PROCESO:1") == ["EN-PROCESO:1"]


def test_las_categorias_vacias_se_descartan():
    assert fusionar(["", "  ", "Urgente"], "EN-PROCESO:1") == [
        "Urgente", "EN-PROCESO:1"]


def test_sin_marca_solo_limpia():
    assert fusionar(["Urgente", "EN-PROCESO:1"], "") == ["Urgente", "EN-PROCESO:1"]


def test_una_lista_vacia_devuelve_solo_la_marca():
    assert fusionar(None, "EN-PROCESO:1") == ["EN-PROCESO:1"]


# ── Separar lo propio de lo ajeno ──

def test_se_distingue_lo_que_escribio_el_sistema():
    todas = ["Urgente", "EN-PROCESO:1", "Tutela", "ENT20260001"]
    assert marcas_del_sistema(todas) == ["EN-PROCESO:1", "ENT20260001"]
    assert marcas_ajenas(todas) == ["Urgente", "Tutela"]
