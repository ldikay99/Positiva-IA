"""
Enlaza cada regla del catálogo con los parámetros que su código usa de verdad,
y describe las búsquedas de la extracción determinista campo por campo.

Lee el código del motor con `ast` (no lo importa) y sigue las funciones del
mismo módulo que la regla llama, hasta tres niveles. Escribe el resultado en
`catalogo.json`: `reglas[].usa` y `nodos[extraccion_determinista].busquedas`.

    py catalogo/generador/enlazar.py
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
MOTOR = RAIZ / "motor"
CATALOGO = RAIZ / "catalogo" / "catalogo.json"

BUSQUEDAS = [
    ("extraer_nombre_afectado", ["nombre_afectado"], "Nombre del afectado"),
    ("extraer_documento", ["numero_documento_afectado"], "Documento del afectado"),
    ("extraer_tipo_documento", ["tipo_documento_afectado", "tipo_documento_remitente"], "Tipo de documento"),
    ("extraer_telefono", ["telefono_afectado"], "Teléfono"),
    ("extraer_nombre_remitente", ["nombre_remitente"], "Nombre del remitente"),
    ("extraer_sexo", ["sexo_afectado"], "Sexo del afectado"),
    ("extraer_tipo_pqrd", ["tipo_pqrd"], "Tipo de PQRD"),
    ("extraer_documento_del_remitente", ["numero_documento_remitente"], "Documento del remitente"),
    ("extraer_tipo_remitente", ["tipo_remitente"], "Tipo de remitente"),
    ("extraer_tipo_solicitud", ["tipo_solicitud"], "Tipo de solicitud"),
    ("extraer_direccion", ["direccion_afectado"], "Dirección"),
    ("extraer_geografia", ["departamento_afectado", "municipio_afectado"], "Departamento y municipio"),
    ("extraer_oficina", ["oficina"], "Oficina destino"),
]


class Modulo:
    def __init__(self, ruta: Path) -> None:
        self.arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        self.funciones: dict[str, ast.AST] = {}
        self.globales: dict[str, ast.AST] = {}
        for n in self.arbol.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funciones[n.name] = n
            elif isinstance(n, ast.ClassDef):
                for m in n.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.funciones[f"{n.name}.{m.name}"] = m
            elif isinstance(n, (ast.Assign, ast.AnnAssign)):
                objetivos = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in objetivos:
                    if isinstance(t, ast.Name):
                        self.globales[t.id] = n

    def nombres(self, nodo: ast.AST) -> set[str]:
        return {x.id for x in ast.walk(nodo) if isinstance(x, ast.Name)} | {
            x.attr for x in ast.walk(nodo) if isinstance(x, ast.Attribute)}

    def alcance(self, funcion: str, profundidad: int = 3) -> set[str]:
        """Nombres que usa la función y las del módulo que llama, y las constantes que esas citan."""
        partes = funcion.split(".")
        nodo = self.funciones.get(funcion) or self.funciones.get(partes[0]) or self.funciones.get(".".join(partes[:2]))
        if nodo is None:
            return set()
        vistos: set[str] = set()
        pendientes = [(nodo, 0)]
        usados: set[str] = set()
        while pendientes:
            n, d = pendientes.pop()
            for nombre in self.nombres(n):
                usados.add(nombre)
                if d < profundidad and nombre in self.funciones and nombre not in vistos:
                    vistos.add(nombre)
                    pendientes.append((self.funciones[nombre], d + 1))
                # Una constante compilada a partir de otras (regex armadas por partes).
                if nombre in self.globales and nombre not in vistos:
                    vistos.add(nombre)
                    pendientes.append((self.globales[nombre], d))
        return usados


def docstring(m: Modulo, funcion: str) -> str:
    n = m.funciones.get(funcion)
    texto = ast.get_docstring(n) if n else ""
    return (texto or "").split("\n\n")[0].replace("\n", " ").strip()


def main() -> None:
    catalogo = json.loads(CATALOGO.read_text(encoding="utf-8"))
    por_modulo: dict[str, set[str]] = {}
    for n in catalogo["nodos"]:
        for p in n["parametros"]:
            por_modulo.setdefault(p["modulo"], set()).add(p["nombre"])

    cache: dict[str, Modulo] = {}

    def modulo(ruta: str) -> Modulo | None:
        if ruta not in cache:
            archivo = MOTOR / ruta
            cache[ruta] = Modulo(archivo) if archivo.exists() else None
        return cache[ruta]

    enlazadas = 0
    for n in catalogo["nodos"]:
        for r in n.get("reglas") or []:
            ruta, funcion = r.get("modulo"), (r.get("funcion") or "").split(" ")[0]
            m = modulo(ruta) if ruta else None
            if not m or not funcion:
                continue
            nombre_mod = ruta.removesuffix(".py").replace("/", ".")
            usados = m.alcance(funcion)
            # Las constantes que la descripción de la regla nombra también cuentan.
            usados |= {p for p in por_modulo.get(nombre_mod, set()) if p in r.get("descripcion", "")}
            r["usa"] = sorted(f"{nombre_mod}.{x}" for x in usados & por_modulo.get(nombre_mod, set()))
            enlazadas += bool(r["usa"])

    ext = next(n for n in catalogo["nodos"] if n["id"] == "extraccion_determinista")
    m = modulo("pipeline/extraccion.py")
    params = por_modulo.get("pipeline.extraccion", set())
    ext["busquedas"] = [
        {"funcion": f, "campos": campos, "nombre": nombre, "descripcion": docstring(m, f),
         "usa": sorted(f"pipeline.extraccion.{x}" for x in m.alcance(f) & params)}
        for f, campos, nombre in BUSQUEDAS if f in m.funciones
    ]
    CATALOGO.write_text(json.dumps(catalogo, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    total = sum(len(n.get("reglas") or []) for n in catalogo["nodos"])
    print(f"reglas enlazadas: {enlazadas}/{total}; búsquedas: {len(ext['busquedas'])}")
    for b in ext["busquedas"]:
        print(f"  {b['nombre']}: {len(b['usa'])} parámetros")


if __name__ == "__main__":
    main()
