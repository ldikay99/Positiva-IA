"""
Una herramienta que se documenta como ejecutable tiene que ejecutar algo.

Un modulo sin `main` ni guarda `__main__` corre bajo `python -m`, no imprime
nada y sale 0. Eso es indistinguible de una corrida correcta sin hallazgos, y
una compuerta de regresion que calla no protege nada.
"""
import ast
import re
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent / "harness"
_INVOCACION = re.compile(r"python -m harness\.(\w+)")


def _arbol(ruta: Path) -> ast.Module:
    return ast.parse(ruta.read_text(encoding="utf-8"))


def _documenta_su_invocacion(ruta: Path) -> bool:
    """Si el docstring del modulo se ofrece como `python -m harness.<modulo>`."""
    doc = ast.get_docstring(_arbol(ruta)) or ""
    return ruta.stem in _INVOCACION.findall(doc)


EJECUTABLES = sorted(
    ruta for ruta in HARNESS.glob("*.py")
    if ruta.stem != "__init__" and _documenta_su_invocacion(ruta)
)


def test_se_encontraron_herramientas_ejecutables():
    assert EJECUTABLES, f"ningun modulo de {HARNESS.name} documenta su invocacion"


@pytest.mark.parametrize("ruta", EJECUTABLES, ids=lambda r: r.stem)
def test_define_main(ruta):
    nombres = {n.name for n in _arbol(ruta).body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "main" in nombres, (
        f"harness/{ruta.name} se documenta como `python -m harness.{ruta.stem}` "
        f"pero no define main(): correrlo no hace nada y sale 0")


def _salida_envuelve_a_main(nodo: ast.stmt) -> bool:
    """
    Si la sentencia entrega el retorno de main() al interprete.

    Valen las dos formas equivalentes, `raise SystemExit(main())` y
    `sys.exit(main())`; no vale `main()` a secas, que descarta el codigo.
    """
    if isinstance(nodo, ast.Raise):
        salida = nodo.exc
    elif isinstance(nodo, ast.Expr):
        salida = nodo.value
    else:
        return False
    if not isinstance(salida, ast.Call):
        return False
    nombre = getattr(salida.func, "id", None) or getattr(salida.func, "attr", None)
    if nombre not in ("SystemExit", "exit"):
        return False
    return bool(salida.args
                and isinstance(salida.args[0], ast.Call)
                and getattr(salida.args[0].func, "id", None) == "main")


@pytest.mark.parametrize("ruta", EJECUTABLES, ids=lambda r: r.stem)
def test_la_guarda_main_propaga_el_codigo_de_salida(ruta):
    """
    Llamar `main()` sin envolverlo descarta su codigo de retorno, y entonces la
    herramienta sale 0 aunque haya encontrado un problema.
    """
    guardas = [
        n for n in _arbol(ruta).body
        if isinstance(n, ast.If)
        and any(isinstance(c, ast.Constant) and c.value == "__main__"
                for c in ast.walk(n.test))
    ]
    assert guardas, f"harness/{ruta.name} no tiene guarda __main__"
    assert any(_salida_envuelve_a_main(n) for n in guardas[0].body), (
        f"harness/{ruta.name}: la guarda descarta el codigo de salida de main()")


# ── Ningun modulo del harness puede correr en silencio ──
#
# Las comprobaciones de arriba solo miran los modulos que se ANUNCIAN como
# ejecutables en su docstring, asi que un modulo que olvide anunciarse se les
# escapa entero. Estas miran los trece.

TODOS = sorted(r for r in HARNESS.glob("*.py") if r.stem != "__init__")


def test_se_encontraron_los_modulos_del_harness():
    """
    Con la lista vacia, las pruebas parametrizadas de abajo no corren y el
    modulo pasa sin comprobar nada. Es el mismo fallo que dejaba fuera a los
    modulos que no se anuncian.
    """
    assert len(TODOS) >= 10, [r.name for r in TODOS]


def _guarda_de(ruta: Path):
    for nodo in _arbol(ruta).body:
        if isinstance(nodo, ast.If) and any(
            isinstance(c, ast.Constant) and c.value == "__main__"
            for c in ast.walk(nodo.test)
        ):
            return nodo
    return None


def _sale_con_un_mensaje(nodo: ast.stmt) -> bool:
    """Si la sentencia termina el proceso diciendo algo."""
    if not isinstance(nodo, ast.Raise) or not isinstance(nodo.exc, ast.Call):
        return False
    if getattr(nodo.exc.func, "id", None) != "SystemExit":
        return False
    return bool(nodo.exc.args)


@pytest.mark.parametrize("ruta", TODOS, ids=lambda r: r.stem)
def test_ningun_modulo_corre_en_silencio(ruta):
    """
    Un modulo del harness es una herramienta o es una libreria, y las dos cosas
    tienen que notarse al ejecutarlo. Salir 0 sin imprimir nada se lee igual que
    una corrida limpia, y ahi es donde una medida rota pasa por medida buena.
    """
    guarda = _guarda_de(ruta)
    assert guarda is not None, (
        f"harness/{ruta.name} no tiene guarda __main__: `python -m "
        f"harness.{ruta.stem}` no imprime nada y sale 0"
    )
    define_main = any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "main"
        for n in _arbol(ruta).body
    )
    if define_main:
        assert any(_salida_envuelve_a_main(n) for n in guarda.body), (
            f"harness/{ruta.name}: la guarda descarta el codigo de salida")
    else:
        assert any(_sale_con_un_mensaje(n) for n in guarda.body), (
            f"harness/{ruta.name} no define main(), asi que su guarda tiene que "
            f"salir con un mensaje que diga que no es una herramienta y a cual "
            f"ir en su lugar")
