"""
La propia suite no puede perder pruebas en silencio.

Dos funciones con el mismo nombre en un archivo no son un error de Python: la
segunda reemplaza a la primera y la comprobacion que hacia la primera deja de
ejecutarse sin que nada lo diga.
"""
import ast
from collections import Counter
from pathlib import Path

import pytest

DIRECTORIO = Path(__file__).resolve().parent
ARCHIVOS = sorted(DIRECTORIO.glob("test_*.py"))


def _nombres_de_prueba(ruta: Path):
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    return [
        nodo.name
        for nodo in arbol.body
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef))
        and nodo.name.startswith("test_")
    ]


def test_hay_archivos_de_prueba():
    assert ARCHIVOS


@pytest.mark.parametrize("ruta", ARCHIVOS, ids=lambda r: r.name)
def test_ninguna_prueba_se_repite_dentro_de_un_archivo(ruta):
    repetidos = [n for n, veces in Counter(_nombres_de_prueba(ruta)).items()
                 if veces > 1]
    assert not repetidos, (
        f"{ruta.name} define dos veces {repetidos}: la segunda tapa a la "
        f"primera y esa comprobacion ya no corre")


def test_ninguna_prueba_se_repite_entre_archivos():
    """
    Entre archivos no se tapan, pero el nombre repetido hace que un fallo
    apunte al archivo equivocado.
    """
    donde = {}
    choques = []
    for ruta in ARCHIVOS:
        for nombre in _nombres_de_prueba(ruta):
            if nombre in donde:
                choques.append(f"{nombre}: {donde[nombre]} y {ruta.name}")
            else:
                donde[nombre] = ruta.name
    assert not choques, "\n".join(choques)


def test_ningun_fuente_empieza_con_bom():
    """
    Python se come el BOM al importar, pero `ast.parse` sobre el texto leido
    como utf-8 no: falla con U+FEFF. Un archivo con BOM es invisible para toda
    comprobacion estructural que barra las fuentes, y se salta en silencio.
    """
    raiz = Path(__file__).resolve().parent.parent
    carpetas = ("pipeline", "utils", "harness", "models", "servidor_mcp",
                "business_rules", "tests")
    con_bom = [
        str(f.relative_to(raiz))
        for carpeta in carpetas
        for f in (raiz / carpeta).rglob("*.py")
        if f.read_bytes().startswith(b"\xef\xbb\xbf")
    ]
    assert not con_bom, con_bom
