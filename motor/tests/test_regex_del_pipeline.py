"""
Las expresiones regulares del pipeline corren sobre texto que viene de fuera.

Se le exige al modelo que no use patrones con cuantificadores anidados porque
cuelgan el proceso. El mismo criterio tiene que valer para las nuestras: el
saneamiento, la extraccion y el detector de candidatos procesan el contenido
del correo, y ahi es donde un atacante puede poner la linea que dispare la
explosion. Si la defensa se cuelga, la defensa es la vulnerabilidad.
"""
import re
from pathlib import Path

import pytest

from servidor_mcp.herramientas import _cuantificador_anidado

RAIZ = Path(__file__).resolve().parent.parent

# Modulos que ven contenido del correo o de sus adjuntos.
MODULOS = [
    "pipeline/saneamiento.py",
    "pipeline/extraccion.py",
    "pipeline/conversion.py",
    "utils/documento_candidatos.py",
    "utils/contacto_extractor.py",
    "utils/generic_filter.py",
    "utils/hash.py",
    "utils/radicado.py",
]

# Literales de patron dentro de re.compile(...) o re.search(r"...", ...).
# El prefijo `r` va fuera del grupo de la comilla: si entrara, la
# retro-referencia buscaria `r"` y la comilla de cierre no la cerraria.
_LITERAL = re.compile(
    r"""re\.(?:compile|search|match|findall|finditer|sub|split)\s*\(\s*"""
    r"""r?(?P<comilla>["'])(?P<patron>(?:\\.|(?!(?P=comilla))[^\\])*)(?P=comilla)""",
    re.VERBOSE,
)


def _patrones_de(ruta: Path):
    fuente = ruta.read_text(encoding="utf-8")
    return [m.group("patron") for m in _LITERAL.finditer(fuente)]


def _todos():
    for nombre in MODULOS:
        ruta = RAIZ / nombre
        if not ruta.exists():
            continue
        for patron in _patrones_de(ruta):
            yield nombre, patron


def test_se_encontraron_patrones_que_revisar():
    """Si el extractor dejara de encontrar nada, la prueba pasaria en vacio."""
    encontrados = list(_todos())
    assert len(encontrados) >= 15, encontrados


@pytest.mark.parametrize("nombre,patron", list(_todos()))
def test_ninguna_expresion_del_pipeline_puede_colgarse(nombre, patron):
    motivo = _cuantificador_anidado(patron)
    assert not motivo, f"{nombre}: {motivo}"


def test_el_detector_de_inyeccion_no_se_cuelga_con_texto_hostil():
    """
    La prueba directa: el saneamiento corre sobre lo que llega en el correo.
    Una linea larga de un solo caracter es lo que dispara el retroceso
    catastrofico si algun patron tuviera esa forma.
    """
    import time

    from pipeline.saneamiento import detectar

    for hostil in ("a" * 20000, "(" * 5000, "a b" * 8000, "\\" * 10000):
        inicio = time.monotonic()
        detectar(hostil)
        assert time.monotonic() - inicio < 3.0, f"tardo demasiado con {hostil[:12]!r}"


def test_los_extractores_no_se_cuelgan_con_texto_hostil():
    import time

    from pipeline.extraccion import extraer_todo

    hostil = ("AAAA " * 4000) + ("1" * 5000)
    inicio = time.monotonic()
    extraer_todo(asunto=hostil[:200], cuerpo=hostil, from_name="Ana",
                 from_address="ana@x.com")
    assert time.monotonic() - inicio < 5.0
