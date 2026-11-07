"""
Evaluador de expresiones seguro: se parsea con `ast` y se recorre con una
lista blanca; nunca se usa eval ni exec.

Admite: números, textos, true/false/null (y True/False/None), listas y
objetos literales, aritmética (+ - * / // % **), comparaciones (también
«in»), and/or/not, «a if cond else b», índices y cortes (x[0], x[1:3]),
campos de objetos con punto (entrada.monto), las funciones len, min, max,
sum, round, abs, int, float, str, bool, lista, ordenar, cualquiera, todos,
redondear, minusculas, mayusculas, recortar, contiene, numero, texto y
algunos métodos de texto, lista y objeto (lower, upper, strip, split, join,
replace, startswith, endswith, get, keys, values…).

No admite: comprensiones, lambdas, asignaciones, f-strings, atributos que
empiecen por «_», ni nada que no esté en la lista. Topes: 2.000 caracteres,
300 nodos, textos y listas de hasta 100.000 elementos, potencias acotadas.
"""
from __future__ import annotations

import ast
import math
import operator
from typing import Any, Callable, Dict

from .base import ErrorDeNegocio

MAX_LARGO = 2000
MAX_NODOS = 300
MAX_TAMANO = 100_000


class ErrorDeExpresion(ErrorDeNegocio):
    pass


def _tam(v: Any) -> Any:
    if isinstance(v, (str, list, tuple, dict)) and len(v) > MAX_TAMANO:
        raise ErrorDeExpresion(f"el resultado pasa de {MAX_TAMANO:,} elementos")
    if isinstance(v, int) and not isinstance(v, bool) and abs(v) > 10 ** 30:
        raise ErrorDeExpresion("el número es demasiado grande")
    return v


def _num(x: Any) -> Any:
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, (int, float)):
        return x
    try:
        t = str(x).strip().replace(" ", "")
        if "," in t and "." in t:  # 1.234,5 (es-CO) o 1,234.5 (en)
            t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
        elif "," in t:
            t = t.replace(",", ".")
        f = float(t)
        return int(f) if f.is_integer() and "." not in t and "e" not in t.lower() else f
    except (TypeError, ValueError):
        raise ErrorDeExpresion(f"«{str(x)[:60]}» no es un número")


def _potencia(a: Any, b: Any) -> Any:
    if isinstance(b, (int, float)) and abs(b) > 100:
        raise ErrorDeExpresion("el exponente pasa de 100")
    if isinstance(a, (int, float)) and abs(a) > 10 ** 6 and abs(b) > 2:
        raise ErrorDeExpresion("la potencia es demasiado grande")
    return operator.pow(a, b)


def _mult(a: Any, b: Any) -> Any:
    if isinstance(a, (str, list)) and isinstance(b, int) and len(a) * max(b, 0) > MAX_TAMANO:
        raise ErrorDeExpresion("el texto o la lista quedaría demasiado grande")
    if isinstance(b, (str, list)) and isinstance(a, int) and len(b) * max(a, 0) > MAX_TAMANO:
        raise ErrorDeExpresion("el texto o la lista quedaría demasiado grande")
    return operator.mul(a, b)


def _div(a: Any, b: Any) -> Any:
    if b == 0:
        raise ErrorDeExpresion("división por cero")
    return operator.truediv(a, b)


def _divent(a: Any, b: Any) -> Any:
    if b == 0:
        raise ErrorDeExpresion("división por cero")
    return operator.floordiv(a, b)


def _mod(a: Any, b: Any) -> Any:
    if isinstance(a, str):
        raise ErrorDeExpresion("% sobre un texto no está permitido")
    if b == 0:
        raise ErrorDeExpresion("división por cero")
    return operator.mod(a, b)


BINARIOS: Dict[type, Callable] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: _mult, ast.Div: _div,
                                  ast.FloorDiv: _divent, ast.Mod: _mod, ast.Pow: _potencia}
UNARIOS: Dict[type, Callable] = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}
COMPARA: Dict[type, Callable] = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
                                 ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a, b: a in b,
                                 ast.NotIn: lambda a, b: a not in b, ast.Is: operator.is_, ast.IsNot: operator.is_not}


def _redondear(x: Any, n: Any = 0) -> Any:
    return round(_num(x), int(n))


def _lista(x: Any = None) -> list:
    if x is None:
        return []
    if isinstance(x, (list, tuple, str)):
        return _tam(list(x))
    if isinstance(x, dict):
        return list(x.keys())
    raise ErrorDeExpresion("lista() necesita un texto, una lista o un objeto")


def _ordenar(x: Any, clave: Any = None, desc: bool = False) -> list:
    xs = list(x or [])
    if clave is not None:
        return sorted(xs, key=lambda e: (e or {}).get(clave) if isinstance(e, dict) else e, reverse=bool(desc))
    return sorted(xs, reverse=bool(desc))


def _texto(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


FUNCIONES: Dict[str, Callable] = {
    "len": len, "largo": len, "min": min, "max": max, "sum": sum, "suma": sum, "round": _redondear, "redondear": _redondear,
    "abs": abs, "int": lambda x: int(_num(x)), "entero": lambda x: int(_num(x)), "float": lambda x: float(_num(x)),
    "numero": _num, "str": _texto, "texto": _texto, "bool": bool, "list": _lista, "lista": _lista,
    "sorted": _ordenar, "ordenar": _ordenar, "any": any, "cualquiera": any, "all": all, "todos": all,
    "minusculas": lambda s: _texto(s).lower(), "mayusculas": lambda s: _texto(s).upper(), "recortar": lambda s: _texto(s).strip(),
    "contiene": lambda a, b: _texto(b).lower() in _texto(a).lower() if isinstance(a, str) else b in (a or []),
    "piso": lambda x: math.floor(_num(x)), "techo": lambda x: math.ceil(_num(x)),
}
METODOS_TEXTO = {"lower", "upper", "strip", "lstrip", "rstrip", "split", "join", "replace", "startswith", "endswith", "title",
                 "capitalize", "find", "count", "zfill", "isdigit", "isalpha", "isalnum", "splitlines", "casefold"}
METODOS_LISTA = {"index", "count"}
METODOS_OBJETO = {"get", "keys", "values", "items"}
CONSTANTES = {"true": True, "false": False, "null": None, "verdadero": True, "falso": False, "nulo": None}


class _Evaluador:
    def __init__(self, variables: Dict[str, Any]) -> None:
        self.vars = variables

    def ev(self, n: ast.AST) -> Any:
        m = getattr(self, f"_{type(n).__name__}", None)
        if m is None:
            raise ErrorDeExpresion(f"«{type(n).__name__}» no está permitido en una expresión")
        return _tam(m(n))

    def _Expression(self, n):
        return self.ev(n.body)

    def _Constant(self, n):
        if not isinstance(n.value, (int, float, str, bool, type(None))):
            raise ErrorDeExpresion("ese literal no está permitido")
        return n.value

    def _Name(self, n):
        if n.id.startswith("_"):
            raise ErrorDeExpresion(f"«{n.id}» no está permitido (nada que empiece por _)")
        if n.id in CONSTANTES:
            return CONSTANTES[n.id]
        if n.id in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[n.id]
        if n.id in self.vars:
            return self.vars[n.id]
        if n.id in FUNCIONES:
            raise ErrorDeExpresion(f"{n.id} es una función: úsala con paréntesis")
        raise ErrorDeExpresion(f"la variable «{n.id}» no existe")

    def _List(self, n):
        return [self.ev(x) for x in n.elts]

    _Tuple = _List

    def _Dict(self, n):
        if any(k is None for k in n.keys):
            raise ErrorDeExpresion("«**» no está permitido")
        return {self.ev(k): self.ev(v) for k, v in zip(n.keys, n.values)}

    def _BinOp(self, n):
        f = BINARIOS.get(type(n.op))
        if not f:
            raise ErrorDeExpresion("ese operador no está permitido")
        try:
            return f(self.ev(n.left), self.ev(n.right))
        except TypeError as e:
            raise ErrorDeExpresion(f"operación inválida: {e}") from e

    def _UnaryOp(self, n):
        f = UNARIOS.get(type(n.op))
        if not f:
            raise ErrorDeExpresion("ese operador no está permitido")
        try:
            return f(self.ev(n.operand))
        except TypeError as e:
            raise ErrorDeExpresion(f"operación inválida: {e}") from e

    def _BoolOp(self, n):
        if isinstance(n.op, ast.And):
            v = True
            for x in n.values:
                v = self.ev(x)
                if not v:
                    return v
            return v
        v = False
        for x in n.values:
            v = self.ev(x)
            if v:
                return v
        return v

    def _Compare(self, n):
        izq = self.ev(n.left)
        for op, der in zip(n.ops, n.comparators):
            d = self.ev(der)
            try:
                if not COMPARA[type(op)](izq, d):
                    return False
            except TypeError as e:
                raise ErrorDeExpresion(f"comparación inválida: {e}") from e
            izq = d
        return True

    def _IfExp(self, n):
        return self.ev(n.body) if self.ev(n.test) else self.ev(n.orelse)

    def _Subscript(self, n):
        v = self.ev(n.value)
        if isinstance(n.slice, ast.Slice):
            s = n.slice
            corte = slice(*(self.ev(x) if x is not None else None for x in (s.lower, s.upper, s.step)))
            if not isinstance(v, (str, list)):
                raise ErrorDeExpresion("solo se corta un texto o una lista")
            return v[corte]
        k = self.ev(n.slice)
        if isinstance(k, str) and k.startswith("_"):
            raise ErrorDeExpresion("claves que empiezan por _ no están permitidas")
        try:
            if isinstance(v, dict):
                return v.get(k)
            if isinstance(v, (list, str)) and isinstance(k, int) and not isinstance(k, bool):
                return v[k] if -len(v) <= k < len(v) else None
        except TypeError:
            pass
        raise ErrorDeExpresion("índice inválido")

    def _Attribute(self, n):
        if n.attr.startswith("_"):
            raise ErrorDeExpresion(f"«{n.attr}» no está permitido (nada que empiece por _)")
        v = self.ev(n.value)
        if isinstance(v, dict):
            return v.get(n.attr)
        raise ErrorDeExpresion(f"«.{n.attr}» solo vale sobre un objeto (o como método permitido con paréntesis)")

    def _Call(self, n):
        if n.keywords and any(k.arg is None for k in n.keywords):
            raise ErrorDeExpresion("«**» no está permitido")
        if any(isinstance(a, ast.Starred) for a in n.args):
            raise ErrorDeExpresion("«*» no está permitido")
        args = [self.ev(a) for a in n.args]
        kw = {k.arg: self.ev(k.value) for k in n.keywords}
        if isinstance(n.func, ast.Name):
            f = FUNCIONES.get(n.func.id)
            if f is None:
                raise ErrorDeExpresion(f"la función «{n.func.id}» no está permitida")
            if kw and n.func.id not in ("sorted", "ordenar", "round", "redondear"):
                raise ErrorDeExpresion(f"{n.func.id} no admite argumentos con nombre")
            try:
                return f(*args, **kw)
            except ErrorDeNegocio:
                raise
            except (TypeError, ValueError) as e:
                raise ErrorDeExpresion(f"{n.func.id}: {e}") from e
        if isinstance(n.func, ast.Attribute):
            nombre = n.func.attr
            if nombre.startswith("_"):
                raise ErrorDeExpresion(f"«{nombre}» no está permitido (nada que empiece por _)")
            obj = self.ev(n.func.value)
            if isinstance(obj, str) and nombre in METODOS_TEXTO:
                pass
            elif isinstance(obj, list) and nombre in METODOS_LISTA:
                pass
            elif isinstance(obj, dict) and nombre in METODOS_OBJETO:
                r = getattr(obj, nombre)(*args)
                return list(r) if nombre in ("keys", "values", "items") else r
            else:
                raise ErrorDeExpresion(f"el método «{nombre}» no está permitido sobre {type(obj).__name__}")
            if nombre == "zfill" and args and isinstance(args[0], int) and args[0] > MAX_TAMANO:
                raise ErrorDeExpresion("el texto quedaría demasiado grande")
            if nombre == "join" and not all(isinstance(x, str) for x in (args[0] if args else [])):
                args = [[_texto(x) for x in (args[0] if args else [])]]
            if nombre == "replace" and len(args) >= 2 and isinstance(args[1], str) and len(args[1]) > 1 and len(obj) * len(args[1]) > MAX_TAMANO:
                raise ErrorDeExpresion("el texto quedaría demasiado grande")
            try:
                return getattr(obj, nombre)(*args)
            except (TypeError, ValueError) as e:
                raise ErrorDeExpresion(f"{nombre}: {e}") from e
        raise ErrorDeExpresion("solo se pueden llamar funciones y métodos permitidos")


def revisar(texto: Any) -> ast.Expression:
    """Parsea y revisa la forma (sin evaluar); ErrorDeExpresion si no se admite."""
    if not isinstance(texto, str) or not texto.strip():
        raise ErrorDeExpresion("la expresión está vacía")
    if len(texto) > MAX_LARGO:
        raise ErrorDeExpresion(f"la expresión pasa de {MAX_LARGO} caracteres")
    try:
        arbol = ast.parse(texto.strip(), mode="eval")
    except SyntaxError as e:
        raise ErrorDeExpresion(f"la expresión no se entiende: {e.msg}") from e
    nodos = list(ast.walk(arbol))
    if len(nodos) > MAX_NODOS:
        raise ErrorDeExpresion(f"la expresión es demasiado larga ({len(nodos)} partes; el tope es {MAX_NODOS})")
    prohibidos = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.NamedExpr, ast.JoinedStr,
                  ast.FormattedValue, ast.Starred, ast.Await, ast.Yield, ast.YieldFrom, ast.Set)
    for x in nodos:
        if isinstance(x, prohibidos):
            raise ErrorDeExpresion(f"«{type(x).__name__}» no está permitido en una expresión")
        if isinstance(x, ast.Attribute) and x.attr.startswith("_"):
            raise ErrorDeExpresion(f"«{x.attr}» no está permitido (nada que empiece por _)")
        if isinstance(x, ast.Name) and x.id.startswith("_"):
            raise ErrorDeExpresion(f"«{x.id}» no está permitido (nada que empiece por _)")
    return arbol


def nombres(texto: str) -> set:
    """Las variables que usa una expresión (para la validación del flujo)."""
    arbol = revisar(texto)
    llamadas = {id(x.func) for x in ast.walk(arbol) if isinstance(x, ast.Call)}
    return {x.id for x in ast.walk(arbol) if isinstance(x, ast.Name) and id(x) not in llamadas
            and x.id not in CONSTANTES and x.id not in ("True", "False", "None")}


def evaluar(texto: str, variables: Dict[str, Any]) -> Any:
    arbol = revisar(texto)
    try:
        return _Evaluador(variables).ev(arbol)
    except RecursionError as e:
        raise ErrorDeExpresion("la expresión anida demasiado") from e
