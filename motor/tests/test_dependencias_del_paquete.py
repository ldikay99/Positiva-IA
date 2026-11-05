"""El codigo que se despliega no depende del harness de medicion."""
import ast
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DESPLEGABLES = ("pipeline", "servidor_mcp")


def _importados(ruta: Path):
    """Los paquetes de primer nivel que importa el archivo."""
    for n in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            for a in n.names:
                yield a.name.split(".")[0], n.lineno
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            yield n.module.split(".")[0], n.lineno


def _archivos(paquete: str):
    return sorted((RAIZ / paquete).rglob("*.py"))


@pytest.mark.parametrize("paquete", DESPLEGABLES)
def test_se_encontraron_archivos(paquete):
    assert _archivos(paquete), f"{paquete}/ no tiene .py que revisar"


@pytest.mark.parametrize("paquete", DESPLEGABLES)
def test_no_importa_el_harness(paquete):
    """
    `harness/` es herramienta de desarrollo y no entra en la imagen. Un import
    desde aqui obliga a copiarlo a produccion o revienta el arranque.
    """
    culpables = [
        f"{ruta.relative_to(RAIZ).as_posix()}:{linea}"
        for ruta in _archivos(paquete)
        for modulo, linea in _importados(ruta)
        if modulo == "harness"
    ]
    assert not culpables, "importan harness: " + ", ".join(culpables)


def test_correr_tampoco_importa_el_harness():
    culpables = [
        linea for modulo, linea in _importados(RAIZ / "correr.py")
        if modulo == "harness"
    ]
    assert not culpables, f"correr.py importa harness en las lineas {culpables}"
