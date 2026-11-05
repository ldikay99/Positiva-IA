"""Ninguna corrida puede empezar a gastar sin que alguien lo haya pedido."""
import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CORRER = RAIZ / "correr.py"
PROBAR = RAIZ / "harness" / "probar_caso.py"


def _arbol(ruta: Path) -> ast.Module:
    return ast.parse(ruta.read_text(encoding="utf-8"))


def _construcciones(ruta: Path) -> list:
    """Cada `NombreDeClase(...)` del archivo, con su linea."""
    return [
        (n.func.id, n.lineno)
        for n in ast.walk(_arbol(ruta))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]


def _guardas_de_la_bandera(ruta: Path) -> list:
    """
    Cada `if` que consulta `args.vertex` y sale de la funcion, con su linea.

    Sirve para distinguir la guarda —que corta la corrida— de cualquier otro
    `if` que solo mire la bandera de pasada.
    """
    guardas = []
    for nodo in ast.walk(_arbol(ruta)):
        if not isinstance(nodo, ast.If):
            continue
        mira_la_bandera = any(
            isinstance(h, ast.Attribute) and h.attr == "vertex"
            for h in ast.walk(nodo.test)
        )
        sale = any(isinstance(h, ast.Return) for h in ast.walk(nodo))
        if mira_la_bandera and sale:
            guardas.append(nodo.lineno)
    return guardas


def test_correr_ofrece_la_bandera_para_pedir_vertex():
    assert "--vertex" in CORRER.read_text(encoding="utf-8"), (
        "correr.py no ofrece --vertex: no hay forma explicita de pedir Vertex")


def test_sin_la_bandera_correr_se_detiene_antes_de_construir_el_modelo():
    """
    No hay backend gratuito de reserva: sin `--vertex` la corrida tiene que
    terminar sin llamar al modelo, no elegir uno por su cuenta.

    Se comprueba el orden, no la mera presencia de la guarda: una guarda
    escrita despues de construir `ModeloVertex` no impide el gasto.
    """
    construcciones = [
        ln for nombre, ln in _construcciones(CORRER) if nombre == "ModeloVertex"
    ]
    if not construcciones:
        return
    guardas = _guardas_de_la_bandera(CORRER)
    assert guardas, (
        "correr.py construye ModeloVertex sin comprobar antes --vertex: "
        "toda corrida gastaria")
    assert min(guardas) < min(construcciones), (
        "la comprobacion de --vertex esta despues de construir ModeloVertex: "
        "no llega a impedir el gasto")


def test_probar_caso_nunca_puede_gastar():
    nombres = [n for n, _ in _construcciones(PROBAR)]
    assert "ModeloVertex" not in nombres, (
        "harness/probar_caso.py puede construir ModeloVertex")


def test_ninguna_medida_del_harness_construye_vertex():
    """`linea_base` es la unica que puede, y solo con `--gastar-modelo`."""
    for ruta in sorted((RAIZ / "harness").glob("*.py")):
        nombres = [n for n, _ in _construcciones(ruta)]
        if "ModeloVertex" not in nombres:
            continue
        assert "--gastar-modelo" in ruta.read_text(encoding="utf-8"), (
            f"harness/{ruta.name} construye ModeloVertex sin exigir --gastar-modelo")
