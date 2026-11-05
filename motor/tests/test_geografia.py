"""
Donde vive el afectado, leido del texto.

Un municipio equivocado manda la respuesta a otra ciudad cuando sale por correo
fisico, asi que casi todas estas pruebas verifican que NO se resuelva lo que no
consta. El valor por defecto, con su anotacion, es mejor que una ciudad
inventada.
"""
import pytest

from pipeline.extraccion import extraer_geografia
from pipeline.geografia import buscar

BOGOTA = (6, 149)


@pytest.mark.parametrize("texto,esperado", [
    ("Ciudad: Bogota", BOGOTA),
    ("Ciudad: Bogotá D.C.", BOGOTA),
    ("Ciudad: Cali", (31, 1006)),
    ("Ciudad: Medellin", (2, 1)),
    ("Municipio: Yotoco Valle del Cauca", (31, 1045)),
    ("Ciudad: venadillo Tolima", (30, 1003)),
    ("Ciudad y departamento: Medellin, Antioquia", (2, 1)),
    ("Resido en Girardot, Cundinamarca", (16, 494)),
])
def test_la_ciudad_rotulada_se_resuelve(texto, esperado):
    r = buscar(texto)
    assert r is not None, texto
    assert r[:2] == esperado


def test_el_nombre_largo_del_catalogo_admite_la_forma_corta():
    """El catalogo dice "SAN JOSE DE CUCUTA" y el correo dice "Cucuta"."""
    assert buscar("Ciudad: Cucuta")[:2] == (24, 780)


# ── Lo que NO se resuelve ──

def test_la_ciudad_de_expedicion_de_la_cedula_no_es_donde_vive():
    """
    "cedula de ciudadania No. NNN de Cali" dice donde se expidio el documento.
    Sin la frontera de palabra, el rotulo "ciudad" lo captura dentro de
    "ciudadania" y registra a la persona donde no vive.
    """
    assert buscar(
        "identificado con cedula de ciudadania No. 66.949.024 de Cali") is None


def test_el_membrete_de_la_carta_no_dice_nada():
    """"Señores ... Ciudad" es la formula de una carta, no una direccion."""
    assert buscar("Señores Positiva Compania de Seguros Ciudad "
                  "Reciba un cordial saludo") is None


def test_un_nombre_que_esta_en_varios_departamentos_no_se_elige_al_azar():
    """Elegir uno manda la respuesta a otra region del pais."""
    assert buscar("Ciudad: La Union") is None
    assert buscar("Ciudad: La Union, Valle del Cauca")[:2] == (31, 1029)


def test_nombrar_el_departamento_no_dice_el_municipio():
    assert buscar("Ciudad: Tolima") is None
    assert buscar("Ciudad: Santander") is None


def test_el_municipio_que_no_pertenece_al_departamento_nombrado_no_vale():
    """La pareja es la que describe un lugar; suelta, cada una miente."""
    assert buscar("Ciudad: Medellin, Tolima") is None


# ── Como llega a la extraccion ──

def test_departamento_y_municipio_viajan_juntos():
    hallazgos = extraer_geografia("Ciudad: Sogamoso Boyaca")

    assert {h.campo for h in hallazgos} == {
        "departamento_afectado", "municipio_afectado"}
    assert all(h.metodo == "geografia_del_texto" for h in hallazgos)


def test_sin_ciudad_en_el_texto_no_se_reporta_ninguno():
    assert extraer_geografia("Solicito una copia de mi historia clinica") == []
