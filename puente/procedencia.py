"""
De dónde sale cada cifra de un documento que arma Vera.

Las cifras de un documento (conteos, tiempos, costos, porcentajes) tienen que salir de lo que devolvieron
las herramientas en la conversación, de lo que dijo o adjuntó la persona, o de un cálculo explícito sobre
esas cifras. Lo demás es inventado y el documento no se genera.

- `cifras_del_documento(texto)`: las cifras que afirma un texto (sin fechas, horas, años ni la numeración
  de títulos y listas).
- `Fuentes`: las cifras disponibles (de resultados de herramientas, textos y adjuntos), más los conteos que
  se deducen de una lista (cuántos elementos, cuántos con cada valor).
- `revisar(texto, fuentes, calculos)`: las cifras sin procedencia, con su contexto. Lo que Vera calcula
  (porcentajes, variaciones, promedios, sumas) va en `calculos` con su fórmula, que se comprueba.
"""
from __future__ import annotations

import ast
import json
import math
import operator
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

MESES = r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre|ene|feb|mar|abr|may|jun|jul|ago|sep|sept|oct|nov|dic)"
DIAS = r"(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)"

# Lo que no es una cifra afirmada: fechas, horas, años, numeración de títulos y listas, versiones del formato.
_NO_CIFRAS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?\s?m\.?)?", re.I),
    # «9 al 11 de noviembre», «9, 10 y 11 de noviembre de 2026», «del 2 al 8 de nov»
    re.compile(r"\b\d{1,2}(?:\s*(?:,|y|al|a|-|–|hasta el)\s*\d{1,2})*\s+de\s+" + MESES + r"(?:\s+(?:de\s+|del\s+)?\d{4})?\b", re.I),
    re.compile(r"\b" + DIAS + r",?\s+\d{1,2}\b", re.I),
    re.compile(r"\b" + MESES + r"\.?\s+(?:de\s+|del\s+)?\d{4}\b", re.I),
    re.compile(r"\b(?:19[5-9]\d|20[0-4]\d)\b"),  # años
    re.compile(r"^\s*(?:#{1,6}\s*|[-*+]\s+)?\d{1,2}[.)](?=\s)", re.M),  # «1. », «## 2) », «- 3) »
    re.compile(r"\b(?:semana|trimestre|semestre|diapositiva|hoja|paso|fase|anexo|art[ií]culo|numeral|cap[ií]tulo)\s+\d{1,3}\b", re.I),
]

# Un número escrito en el texto: 1.267 · 1,267 · 12,5 · 0.33 · 74,7 % · $ 1.200.000 · -12,2 %
_NUMERO = re.compile(r"(?<![\w.,/\-])(-?\d+(?:[.,]\d+)*)(?![\w\-/]|[.,]\d)(\s?%|\s?por\s?ciento)?", re.I)
_UNIDADES = re.compile(r"(?<=\d)(s|seg|ms|min|h|mb|gb|kb|usd|cop|x|k)\b", re.I)
_UNIDAD_DESPUES = re.compile(r"\s?(?:s|seg|segundos?|ms|min|minutos?|h|horas?|usd|d[oó]lares|cop|pesos|mb|gb|kb)\b", re.I)


def _valores(token: str) -> Set[float]:
    """Las lecturas posibles de un número escrito a la colombiana o a la inglesa («1.267» puede ser mil doscientos o 1,267)."""
    t = token.strip().lstrip("+")
    neg = t.startswith("-")
    t = t.lstrip("-")
    vals: Set[float] = set()
    if re.fullmatch(r"[1-9]\d{0,2}(?:[.,]\d{3})+", t):
        vals.add(float(re.sub(r"[.,]", "", t)))
    if re.fullmatch(r"\d+(?:[.,]\d+)?", t):
        vals.add(float(t.replace(",", ".")))
    m = re.fullmatch(r"(\d{1,3}(?:\.\d{3})+),(\d+)", t) or re.fullmatch(r"(\d{1,3}(?:,\d{3})+)\.(\d+)", t)
    if m:
        vals.add(float(re.sub(r"[.,]", "", m.group(1)) + "." + m.group(2)))
    if not vals:
        try:
            vals.add(float(re.sub(r"[^\d.]", "", t.replace(",", "."))))
        except ValueError:
            pass
    return {-v if neg else v for v in vals}


def _decimales(token: str) -> int:
    m = re.search(r"[.,](\d+)$", token)
    if not m or re.fullmatch(r"-?[1-9]\d{0,2}(?:[.,]\d{3})+", token):
        return 0
    return len(m.group(1))


def cifras_del_documento(texto: str) -> List[Dict[str, Any]]:
    """Las cifras que afirma un texto: [{texto, valores, decimales, porcentaje, contexto}]."""
    limpio = _UNIDADES.sub(lambda m: " " + m.group(1), texto or "")
    for patron in _NO_CIFRAS:
        limpio = patron.sub(lambda m: " " * len(m.group(0)), limpio)
    r = []
    for m in _NUMERO.finditer(limpio):
        token = m.group(1)
        vals = _valores(token)
        if not vals:
            continue
        ini, fin = max(0, m.start() - 40), min(len(limpio), m.end() + 30)
        unidad = bool(m.group(2)) or bool(_UNIDAD_DESPUES.match(limpio, m.end())) or limpio[max(0, m.start() - 2):m.start()].strip().endswith("$")
        r.append({"texto": token + (m.group(2) or ""), "valores": vals, "decimales": _decimales(token), "unidad": unidad,
                  "porcentaje": bool(m.group(2)), "contexto": " ".join((texto or "")[ini:fin].split())})
    return r


# ---------------------------------------------------------------- de dónde pueden salir
_NUM_EN_TEXTO = re.compile(r"-?\d+(?:[.,]\d+)*")
CLAVES_SIN_CIFRAS = {"creada", "t", "ts", "hace_ms", "inicio", "fin", "actualizada", "id", "pid", "huella", "sha", "tamano", "tamaño"}


class Fuentes:
    """Las cifras que se pueden poner en un documento, con el nombre de dónde salió cada una."""

    def __init__(self) -> None:
        self.valores: Dict[float, str] = {}

    def _poner(self, v: float, origen: str) -> None:
        if math.isfinite(v):
            self.valores.setdefault(round(v, 6), origen)

    def agregar_texto(self, texto: str, origen: str) -> None:
        for t in _NUM_EN_TEXTO.findall(texto or ""):
            for v in _valores(t):
                self._poner(v, origen)

    def agregar(self, dato: Any, origen: str, _prof: int = 0) -> None:
        """Las cifras de un resultado (JSON ya leído o texto JSON), y los conteos de sus listas."""
        if _prof > 12:
            return
        if isinstance(dato, str):
            s = dato.strip()
            if s[:1] in "[{" and len(s) < 2_000_000:
                try:
                    self.agregar(json.loads(s), origen, _prof + 1)
                    return
                except ValueError:
                    pass
            self.agregar_texto(s, origen)
        elif isinstance(dato, bool) or dato is None:
            return
        elif isinstance(dato, (int, float)):
            self._poner(float(dato), origen)
        elif isinstance(dato, dict):
            for k, v in dato.items():
                if str(k).lower() in CLAVES_SIN_CIFRAS and isinstance(v, (int, float)) and abs(v) > 10**9:
                    continue  # marcas de tiempo e ids: no son cifras de nadie
                self.agregar(v, origen, _prof + 1)
        elif isinstance(dato, (list, tuple)):
            self._poner(float(len(dato)), origen + " (cuántos)")
            filas = [x for x in dato if isinstance(x, dict)]
            if filas:
                # cuántos tienen cada valor: «36 radicados» sale de contar los «desenlace: radicado» de la lista
                conteo: Dict[Tuple[str, str], int] = {}
                for f in filas:
                    for k, v in f.items():
                        if isinstance(v, (str, bool)) and len(str(v)) <= 80:
                            conteo[(k, str(v))] = conteo.get((k, str(v)), 0) + 1
                for n in conteo.values():
                    self._poner(float(n), origen + " (conteo)")
            for x in list(dato)[:5000]:
                self.agregar(x, origen, _prof + 1)

    def explica(self, cifra: Dict[str, Any]) -> Optional[str]:
        """De dónde sale una cifra del documento (None si de ningún lado).

        Un número tal cual sale de las fuentes. Redondeado (29,83 → 29,8 o «30 s») solo vale si el documento lo
        escribe con decimales o con su unidad: un conteo suelto («9») tiene que estar tal cual, porque entre tantos
        tiempos con decimales casi cualquier entero se parece a alguno. Lo calculado (porcentajes, variaciones,
        promedios, sumas) no se adivina: se declara en «calculos» con su fórmula."""
        d = cifra["decimales"]
        for v in cifra["valores"]:
            for cand in (v, -v):  # «una mejora del 48,4 %» es la variación de -48,4 %
                if round(cand, 6) in self.valores:
                    return self.valores[round(cand, 6)]
                if d > 0 or cifra.get("unidad"):
                    for s, origen in self.valores.items():
                        if s != int(s) and round(s, d) == round(cand, d):
                            return origen + " (redondeado)"
        return None


# ---------------------------------------------------------------- cálculos que declara Vera
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _evaluar(expr: str) -> Tuple[float, List[float]]:
    """Una fórmula aritmética simple (+ − × ÷ y paréntesis) y los números que usa."""
    usados: List[float] = []
    e = expr.replace("×", "*").replace("÷", "/").replace("x", "*").replace("%", "")
    e = re.sub(r"(\d)\.(\d{3})(?!\d)", r"\1\2", e) if re.search(r"\d\.\d{3}(?!\d)", e) and "," in e else e
    e = e.replace(",", ".")

    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            usados.append(float(n.value))
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        raise ValueError("solo números, + - * / y paréntesis")
    return ev(ast.parse(e, mode="eval")), usados


def revisar_calculos(calculos: Any, fuentes: Fuentes) -> Tuple[Fuentes, List[str]]:
    """Cada cálculo {valor, formula} tiene que dar lo que dice y usar cifras con procedencia; los buenos se vuelven fuente."""
    problemas: List[str] = []
    for c in calculos or []:
        if not isinstance(c, dict):
            continue
        formula, valor = str(c.get("formula") or ""), str(c.get("valor") or "")
        try:
            res, usados = _evaluar(formula)
        except Exception as e:  # noqa: BLE001
            problemas.append(f"el cálculo «{formula}» no se entiende ({e})")
            continue
        sin = [u for u in usados if fuentes.explica({"valores": {u}, "decimales": 6, "porcentaje": False}) is None
               and u not in (100.0, 1000.0, 60.0, 24.0, 7.0, 30.0, 12.0, 2.0)]
        if sin:
            problemas.append(f"el cálculo «{formula}» usa {', '.join(f'{u:g}' for u in sin)}, que no salen de ninguna herramienta")
            continue
        cifra = (cifras_del_documento(valor) or [{"valores": set(), "decimales": 0}])[0]
        d = cifra["decimales"]
        if not any(round(res, d) == round(v, d) or round(-res, d) == round(v, d) for v in cifra["valores"]):
            problemas.append(f"el cálculo «{formula}» da {res:.{max(d, 2)}f}, no {valor}")
            continue
        fuentes._poner(round(res, d), f"cálculo {formula}")
        for v in cifra["valores"]:
            fuentes._poner(v, f"cálculo {formula}")
    return fuentes, problemas


def revisar(texto: str, fuentes: Fuentes, calculos: Any = None) -> Dict[str, Any]:
    """{ok, sin_procedencia: [{cifra, contexto}], problemas_calculos, revisadas}."""
    fuentes, problemas = revisar_calculos(calculos, fuentes)
    cifras = cifras_del_documento(texto)
    sin = []
    vistos: Set[str] = set()
    for c in cifras:
        if fuentes.explica(c) is None and c["texto"] not in vistos:
            vistos.add(c["texto"])
            sin.append({"cifra": c["texto"], "contexto": c["contexto"]})
    return {"ok": not sin and not problemas, "sin_procedencia": sin, "problemas_calculos": problemas, "revisadas": len(cifras)}


def texto_de_documento(a: Dict[str, Any]) -> str:
    """Todo lo que dice un pedido de generar_documento: título, contenido, hojas y datos de plantilla."""
    partes = [str(a.get("titulo") or ""), str(a.get("contenido") or "")]
    for h in a.get("hojas") or []:
        if isinstance(h, dict):
            # los nombres de columnas son rótulos; las celdas, datos (una por línea para que no se peguen)
            for fila in h.get("filas") or []:
                partes.append("\n".join(f" {x} " for x in (fila if isinstance(fila, list) else [fila])))
    def plano(x: Any) -> Iterable[str]:
        if isinstance(x, dict):
            for v in x.values():
                yield from plano(v)
        elif isinstance(x, list):
            for v in x:
                yield from plano(v)
        elif x is not None and not isinstance(x, bool):
            yield f" {x} "
    partes.extend(plano(a.get("datos") or {}))
    return "\n".join(partes)
