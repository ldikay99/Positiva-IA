"""
Búsqueda robusta, la misma en todos los nodos que comparan o extraen.

- Comparación «flexible» (por defecto): no importan mayúsculas, tildes ni
  espacios de más («PETICIÓN», «peticion», « Petición  » y «peticiòn» son lo
  mismo). «exacta»: tal cual, letra por letra. «parecida»: tolera errores de
  escritura (similitud ≥ umbral, 0,85 por defecto). `distinguir_mayusculas`,
  `distinguir_tildes` y `distinguir_espacios` afinan la flexible.
- Regex que ignoran tildes y devuelven el texto ORIGINAL encontrado: el texto
  se pliega carácter por carácter (cada letra a su base sin tildes) guardando
  la posición de cada carácter, el patrón se pliega igual, y lo encontrado se
  corta del texto original.
- Operadores de texto, números («1.234,56», «$ 1,234.56»), fechas (ISO,
  DD/MM/AAAA, DD-MM-AAAA, «7 de noviembre de 2026», hoy/ayer/mañana) y
  longitud (caracteres, dígitos, letras o palabras).
- Varias condiciones («todas» o «alguna»), cada una sobre uno o varios campos.
"""
from __future__ import annotations

import datetime
import difflib
import functools
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

COMPARACIONES = ("flexible", "exacta", "parecida")
MODOS = ("todas", "alguna")
CONTAR = ("caracteres", "digitos", "letras", "palabras")
UMBRAL = 0.85

OPS_TEXTO = ("contiene", "no_contiene", "igual", "distinto", "empieza_con", "termina_con", "palabra",
             "contiene_alguna", "contiene_todas", "en_lista", "no_en_lista", "parecido", "coincide", "no_coincide",
             "vacio", "no_vacio")
OPS_NUMERO = ("mayor", "menor", "mayor_igual", "menor_igual", "entre")
OPS_FECHA = ("antes_de", "despues_de", "entre_fechas")
OPS_LONGITUD = ("longitud_igual", "longitud_mayor", "longitud_menor", "longitud_entre")
OPERADORES = OPS_TEXTO + OPS_NUMERO + OPS_FECHA + OPS_LONGITUD
# Con varios campos, los negativos tienen que cumplirse en TODOS (ninguno contiene…); los demás, en alguno.
NEGATIVOS = frozenset({"no_contiene", "distinto", "no_coincide", "no_en_lista", "vacio"})
CON_LISTA = frozenset({"contiene_alguna", "contiene_todas", "en_lista", "no_en_lista"})
CON_RANGO = frozenset({"entre", "entre_fechas", "longitud_entre"})
CON_VALOR = frozenset({"no_contiene", "igual", "distinto", "empieza_con", "termina_con", "palabra", "parecido", "coincide", "no_coincide",
                       "mayor", "menor", "mayor_igual", "menor_igual", "antes_de", "despues_de",
                       "longitud_igual", "longitud_mayor", "longitud_menor"})
REGEX = frozenset({"coincide", "no_coincide"})
OPCIONES_BOOL = ("distinguir_mayusculas", "distinguir_tildes", "distinguir_espacios")

_ESPACIOS = re.compile(r"\s+")
_NO_PALABRA = re.compile(r"[^\w]+")


# ------------------------------------------------------------------ texto

def a_texto(v: Any) -> str:
    """Igual que el motor: listas de valores simples con coma, objetos en JSON, booleanos «sí»/«no»."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "sí" if v else "no"
    if isinstance(v, list) and all(isinstance(x, (str, int, float)) for x in v):
        return ", ".join(str(x) for x in v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def quitar_tildes(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


@dataclass(frozen=True)
class Opciones:
    comparacion: str = "flexible"
    mayusculas: bool = False  # distinguirlas
    tildes: bool = False
    espacios: bool = False
    umbral: float = UMBRAL
    contar: str = "caracteres"


FLEXIBLE = Opciones()


def opciones_de(d: Any) -> Opciones:
    """Las opciones de comparación de una condición, un campo o un nodo."""
    d = d if isinstance(d, dict) else {}
    comp = d.get("comparacion") if d.get("comparacion") in COMPARACIONES else "flexible"
    exacta = comp == "exacta"
    u = d.get("umbral")
    umbral = float(u) if isinstance(u, (int, float)) and not isinstance(u, bool) and 0 <= u <= 1 else UMBRAL
    return Opciones(comp, exacta or d.get("distinguir_mayusculas") is True, exacta or d.get("distinguir_tildes") is True,
                    exacta or d.get("distinguir_espacios") is True, umbral, d.get("contar") if d.get("contar") in CONTAR else "caracteres")


def normalizar(x: Any, o: Opciones = FLEXIBLE) -> str:
    """El texto listo para comparar según las opciones (por defecto: sin mayúsculas, tildes ni espacios de más)."""
    t = a_texto(x)
    if o.comparacion == "exacta":
        return t
    t = unicodedata.normalize("NFC", t) if o.tildes else quitar_tildes(t)
    if not o.espacios:
        t = _ESPACIOS.sub(" ", t).strip()
    return t if o.mayusculas else t.casefold()


# ------------------------------------------------------- regex sin tildes

def _plegar_letra(c: str) -> str:
    """Un carácter a su base sin tildes (siempre 0 o 1 caracteres, para no perder la posición)."""
    if c.isascii():
        return c
    if unicodedata.combining(c):
        return ""
    base = "".join(x for x in unicodedata.normalize("NFD", c) if not unicodedata.combining(x))
    return base if len(base) == 1 else c


@functools.lru_cache(maxsize=64)
def _plegado(texto: str) -> Tuple[str, Optional[Tuple[int, ...]]]:
    """(texto plegado, posición en el original de cada carácter plegado o None si es uno a uno)."""
    if texto.isascii():
        return texto, None
    partes, pos = [], []
    for i, c in enumerate(texto):
        p = _plegar_letra(c)
        if p:
            partes.append(p)
            pos.append(i)
    s = "".join(partes)
    return s, (None if len(s) == len(texto) else tuple(pos))


def plegar_patron(p: str) -> str:
    """Quita las tildes de las letras del patrón sin tocar su sintaxis («[áa]» sigue siendo una clase)."""
    if p.isascii():
        return p
    r, i = [], 0
    while i < len(p):
        c = p[i]
        if c == "\\" and i + 1 < len(p):
            sig = p[i + 1]
            if sig.isascii():
                r.append(c + sig)
            else:  # «\é» es la letra tal cual: sin la barra, para no formar un escape como «\e»
                f = _plegar_letra(sig)
                r.append(f if (f.isalnum() or not f) else "\\" + f)
            i += 2
            continue
        r.append(_plegar_letra(c))
        i += 1
    return "".join(r)


_REFERENCIA = re.compile(r"\\(?:g<([^>]*)>|([0-9]{1,2})|(.))", re.S)


class Coincidencia:
    """Como un re.Match, pero sus textos y posiciones son los del texto ORIGINAL (con tildes)."""

    def __init__(self, m: "re.Match[str]", texto: str, pos: Optional[Tuple[int, ...]]):
        self.m, self.texto, self.pos = m, texto, pos

    def _original(self, a: int, b: int) -> Tuple[int, int]:
        if self.pos is None:
            return a, b
        largo = len(self.texto)
        ini = self.pos[a] if a < len(self.pos) else largo
        if b <= a:
            return ini, ini
        fin = self.pos[b - 1] + 1
        while fin < largo and unicodedata.combining(self.texto[fin]):
            fin += 1
        return ini, fin

    def span(self, g: Any = 0) -> Tuple[int, int]:
        a, b = self.m.span(g)
        return (-1, -1) if a < 0 else self._original(a, b)

    def start(self, g: Any = 0) -> int:
        return self.span(g)[0]

    def end(self, g: Any = 0) -> int:
        return self.span(g)[1]

    def group(self, g: Any = 0) -> Optional[str]:
        a, b = self.span(g)
        return None if a < 0 else self.texto[a:b]

    def groups(self) -> Tuple[Optional[str], ...]:
        return tuple(self.group(i) for i in range(1, self.m.re.groups + 1))

    def groupdict(self) -> Dict[str, Optional[str]]:
        return {k: self.group(k) for k in self.m.re.groupindex}

    def expand(self, plantilla: str) -> str:
        """\\1, \\g<1>, \\g<nombre>, \\n y \\t, con los textos originales."""
        def f(x: "re.Match[str]") -> str:
            if x.group(1) is not None:
                k = x.group(1)
                return self.group(int(k) if k.isdigit() else k) or ""
            if x.group(2) is not None:
                return self.group(int(x.group(2))) or ""
            e = x.group(3)
            return {"n": "\n", "t": "\t", "r": "\r", "\\": "\\"}.get(e, "\\" + e)
        return _REFERENCIA.sub(f, plantilla)


class Patron:
    """Una regex que por defecto no distingue mayúsculas ni tildes y devuelve el texto original."""

    def __init__(self, patron: str, distinguir_mayusculas: bool = False, distinguir_tildes: bool = False, multilinea: bool = True):
        flags = (0 if distinguir_mayusculas else re.I) | (re.M if multilinea else 0)
        self.original = patron
        self.plegar = not distinguir_tildes
        rx = None
        if self.plegar:
            try:
                rx = re.compile(plegar_patron(patron), flags)
            except re.error:
                rx, self.plegar = None, False  # p. ej. un rango que al plegarlo queda al revés: se usa tal cual
        self.rx = rx or re.compile(patron, flags)
        self.groups = self.rx.groups

    @classmethod
    def de(cls, patron: str, d: Any = None) -> "Patron":
        o = opciones_de(d)
        return cls(patron, o.mayusculas, o.tildes)

    def _preparar(self, texto: str) -> Tuple[str, Optional[Tuple[int, ...]]]:
        return _plegado(texto) if self.plegar else (texto, None)

    def finditer(self, texto: Any) -> Iterator[Coincidencia]:
        texto = a_texto(texto)
        s, pos = self._preparar(texto)
        for m in self.rx.finditer(s):
            yield Coincidencia(m, texto, pos)

    def search(self, texto: Any) -> Optional[Coincidencia]:
        texto = a_texto(texto)
        s, pos = self._preparar(texto)
        m = self.rx.search(s)
        return Coincidencia(m, texto, pos) if m else None

    def fullmatch(self, texto: Any) -> Optional[Coincidencia]:
        texto = a_texto(texto)
        s, pos = self._preparar(texto)
        m = self.rx.fullmatch(s)
        return Coincidencia(m, texto, pos) if m else None

    def sub(self, reemplazo: str, texto: Any, count: int = 0) -> str:
        """Reemplaza sobre el texto original lo que se encontró en el plegado."""
        texto = a_texto(texto)
        partes, ultimo, n = [], 0, 0
        for c in self.finditer(texto):
            a, b = c.span()
            partes += [texto[ultimo:a], c.expand(reemplazo or "")]
            ultimo, n = b, n + 1
            if count and n >= count:
                break
        partes.append(texto[ultimo:])
        return "".join(partes)


# ---------------------------------------------------------------- parecido

def _para_parecido(t: str) -> str:
    return _ESPACIOS.sub(" ", _NO_PALABRA.sub(" ", t)).strip()


para_parecido = _para_parecido  # sin signos ni espacios de más, para medir el parecido


def similitud(a: Any, b: Any) -> float:
    a, b = a_texto(a), a_texto(b)
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def parecido_en(texto: str, buscado: str, umbral: float = UMBRAL, ventanas: bool = True) -> float:
    """La mejor similitud entre lo buscado y el texto entero o un trozo de tantas palabras como lo buscado (±1)."""
    texto, buscado = _para_parecido(texto), _para_parecido(buscado)
    if not buscado:
        return 1.0 if not texto else 0.0
    mejor = similitud(texto, buscado)
    if mejor >= umbral or not ventanas or not texto:
        return mejor
    palabras = texto.split(" ")
    k = len(buscado.split(" "))
    sm = difflib.SequenceMatcher(None)
    sm.set_seq2(buscado)
    for largo in sorted({max(1, k - 1), k, k + 1}):
        for i in range(0, max(1, len(palabras) - largo + 1)):
            sm.set_seq1(" ".join(palabras[i:i + largo]))
            if sm.real_quick_ratio() < umbral or sm.quick_ratio() < umbral:
                continue
            r = sm.ratio()
            if r > mejor:
                mejor = r
                if mejor >= umbral:
                    return mejor
    return mejor


# ----------------------------------------------------------------- números

_NUMERO = re.compile(r"(?<![\d.,])([-+−]?)\s*(\d[\d.,']*)")


def _interpretar(c: str) -> Optional[float]:
    c = c.replace("'", "")
    p, k = c.count("."), c.count(",")
    if p and k:
        dec = "." if c.rfind(".") > c.rfind(",") else ","
        c = c.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif p or k:
        sep = "." if p else ","
        partes = c.split(sep)
        if len(partes) > 2:
            c = "".join(partes)  # 1.234.567 o 1,234,567: separador de miles
        else:
            ent, frac = partes
            # «1.234» o «12,500»: miles; «1,5», «0,500» o «3.14»: decimales
            c = ent + frac if len(frac) == 3 and 1 <= len(ent) <= 3 and ent != "0" else f"{ent}.{frac}"
    try:
        return float(c)
    except ValueError:
        return None


def numero(x: Any) -> Optional[float]:
    """El primer número de un valor: 5, «5», «1.234,56», «$ 1,234.56», «-3», «12,5 %». None si no hay."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    m = _NUMERO.search(a_texto(x))
    if not m:
        return None
    v = _interpretar(m.group(2).rstrip(".,'"))
    if v is None:
        return None
    return -v if m.group(1) in ("-", "−") else v


# ------------------------------------------------------------------ fechas

MESES = {"enero": 1, "ene": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3, "abril": 4, "abr": 4, "mayo": 5, "may": 5,
         "junio": 6, "jun": 6, "julio": 7, "jul": 7, "agosto": 8, "ago": 8, "septiembre": 9, "setiembre": 9, "sept": 9, "sep": 9,
         "set": 9, "octubre": 10, "oct": 10, "noviembre": 11, "nov": 11, "diciembre": 12, "dic": 12}
_MES = "|".join(sorted(MESES, key=len, reverse=True))
_HORA = r"(?:[t\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
_FECHAS = [
    ("amd", re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)" + _HORA)),
    ("dma", re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?!\d)" + _HORA)),
    ("d_mes_a", re.compile(r"(?<!\d)(\d{1,2})\s*(?:de\s+)?(" + _MES + r")\.?,?\s*(?:de\s+|del\s+)?(\d{4})(?!\d)")),
    ("mes_d_a", re.compile(r"\b(" + _MES + r")\.?\s+(\d{1,2}),?\s*(?:de\s+|del\s+)?(\d{4})(?!\d)")),
]
_RELATIVAS = {"hoy": 0, "ayer": -1, "manana": 1, "pasado manana": 2, "anteayer": -2}


def _hoy() -> datetime.date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/Bogota")).date()
    except Exception:
        return (datetime.datetime.utcnow() - datetime.timedelta(hours=5)).date()


def fecha(x: Any) -> Optional[Tuple[datetime.datetime, bool]]:
    """(fecha, si trae hora) de la primera fecha que haya en el valor, o None.
    Entiende ISO (2026-11-07, 2026-11-07T10:30), 07/11/2026, 7-11-2026, 07.11.26, «7 de noviembre de 2026»,
    «noviembre 7 de 2026», «7 nov 2026» y hoy, ayer, mañana."""
    if isinstance(x, datetime.datetime):
        return x.replace(tzinfo=None), True
    if isinstance(x, datetime.date):
        return datetime.datetime(x.year, x.month, x.day), False
    t = normalizar(x)
    if not t:
        return None
    if t in _RELATIVAS:
        d = _hoy() + datetime.timedelta(days=_RELATIVAS[t])
        return datetime.datetime(d.year, d.month, d.day), False
    candidatas = []
    for forma, rx in _FECHAS:
        for m in rx.finditer(t):
            g = m.groups()
            try:
                if forma == "amd":
                    a, me, d, h, mi, s = int(g[0]), int(g[1]), int(g[2]), g[3], g[4], g[5]
                elif forma == "dma":
                    d, me, a, h, mi, s = int(g[0]), int(g[1]), int(g[2]), g[3], g[4], g[5]
                    a += 2000 if a < 100 else 0
                elif forma == "d_mes_a":
                    d, me, a, h, mi, s = int(g[0]), MESES[g[1]], int(g[2]), None, None, None
                else:
                    me, d, a, h, mi, s = MESES[g[0]], int(g[1]), int(g[2]), None, None, None
                f = datetime.datetime(a, me, d, int(h or 0), int(mi or 0), int(s or 0))
            except (ValueError, KeyError):
                continue
            candidatas.append((m.start(), f, h is not None))
            break
    if not candidatas:
        return None
    _, f, con_hora = min(candidatas, key=lambda x: x[0])
    return f, con_hora


def _comparar_fechas(a: Tuple[datetime.datetime, bool], b: Tuple[datetime.datetime, bool]) -> int:
    x, y = (a[0], b[0]) if a[1] and b[1] else (a[0].date(), b[0].date())
    return (x > y) - (x < y)


# ---------------------------------------------------------------- longitud

def medir(valor: Any, contar: str = "caracteres", o: Opciones = FLEXIBLE) -> int:
    """Cuánto mide un valor: caracteres (sin espacios de más, salvo exacta), dígitos, letras o palabras."""
    t = a_texto(valor)
    if o.comparacion != "exacta":
        t = unicodedata.normalize("NFC", t)
        if not o.espacios:
            t = _ESPACIOS.sub(" ", t).strip()
    if contar == "digitos":
        return sum(c.isdigit() for c in t)
    if contar == "letras":
        return sum(c.isalpha() for c in t)
    if contar == "palabras":
        return len(t.split())
    return len(t)


def _entero_no_negativo(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


LONGITUD_CLAVES = ("longitud", "longitud_min", "longitud_max")


def tiene_longitud(d: Any) -> bool:
    return isinstance(d, dict) and any(d.get(k) is not None for k in LONGITUD_CLAVES)


def cumple_longitud(valor: Any, d: Any) -> bool:
    """Si el valor cumple longitud / longitud_min / longitud_max (contando según «contar»)."""
    if not tiene_longitud(d):
        return True
    o = opciones_de(d)
    n = medir(valor, o.contar, o)
    if _entero_no_negativo(d.get("longitud")) and n != d["longitud"]:
        return False
    if _entero_no_negativo(d.get("longitud_min")) and n < d["longitud_min"]:
        return False
    if _entero_no_negativo(d.get("longitud_max")) and n > d["longitud_max"]:
        return False
    return True


def describir_longitud(d: Any) -> str:
    if not isinstance(d, dict):
        return ""
    partes = []
    unidad = {"caracteres": "caracteres", "digitos": "dígitos", "letras": "letras", "palabras": "palabras"}.get(d.get("contar") or "caracteres", "caracteres")
    if d.get("solo_digitos") is True:
        partes.append("solo dígitos")
    if d.get("longitud") is not None:
        partes.append(f"exactamente {d['longitud']} {unidad}")
    elif d.get("longitud_min") is not None and d.get("longitud_max") is not None:
        partes.append(f"entre {d['longitud_min']} y {d['longitud_max']} {unidad}")
    elif d.get("longitud_min") is not None:
        partes.append(f"al menos {d['longitud_min']} {unidad}")
    elif d.get("longitud_max") is not None:
        partes.append(f"como mucho {d['longitud_max']} {unidad}")
    return ", ".join(partes)


def problemas_longitud(d: dict, que: str = "") -> List[str]:
    p, pre = [], f"{que}: " if que else ""
    for k in LONGITUD_CLAVES:
        if d.get(k) is not None and not _entero_no_negativo(d[k]):
            p.append(f"{pre}{k} tiene que ser un entero de 0 en adelante")
    if _entero_no_negativo(d.get("longitud_min")) and _entero_no_negativo(d.get("longitud_max")) and d["longitud_min"] > d["longitud_max"]:
        p.append(f"{pre}longitud_min ({d['longitud_min']}) es mayor que longitud_max ({d['longitud_max']})")
    if "contar" in d and d["contar"] not in CONTAR:
        p.append(f"{pre}contar tiene que ser {', '.join(CONTAR)}")
    if "solo_digitos" in d and not isinstance(d["solo_digitos"], bool):
        p.append(f"{pre}solo_digitos tiene que ser true o false")
    return p


def solo_digitos(t: Any) -> str:
    return re.sub(r"\D", "", a_texto(t))


def extraer(p: Patron, texto: Any, grupo: Optional[int] = None, reglas: Any = None, todos: bool = False) -> Tuple[Any, Optional[Coincidencia]]:
    """Lo que encuentra una regex: el primer candidato que cumple (solo_digitos, longitud), o todos sin repetir.
    Los que no cumplen se saltan y se prueba la siguiente coincidencia. (valor, primera coincidencia aceptada)."""
    reglas = reglas if isinstance(reglas, dict) else {}
    g = grupo if isinstance(grupo, int) and not isinstance(grupo, bool) else (1 if p.groups else 0)
    valores, primera = [], None
    for m in p.finditer(texto):
        v = (m.group(g) or "").strip()
        if reglas.get("solo_digitos") is True:
            v = solo_digitos(v)
        if not v or not cumple_longitud(v, reglas):
            continue
        primera = primera or m
        if not todos:
            return v, m
        if v not in valores:
            valores.append(v)
    return (valores if todos else ""), primera


# ------------------------------------------------------------ condiciones

def campos_de(c: dict, defecto: str = "texto") -> List[str]:
    xs: List[str] = []
    if isinstance(c.get("campos"), list):
        xs += [str(x) for x in c["campos"] if isinstance(x, str) and x.strip()]
    if isinstance(c.get("campo"), str) and c["campo"].strip():
        xs.insert(0, c["campo"])
    return xs or ([defecto] if defecto else [])


def _partir(v: Any) -> List[Any]:
    if isinstance(v, list):
        return v
    if v is None or (isinstance(v, str) and not v.strip()):
        return []
    if isinstance(v, str):
        return [x for x in re.split(r"\s*[|;\n]\s*", v) if x.strip()]
    return [v]


def lista_de(c: dict) -> List[Any]:
    """Los valores de contiene_alguna / contiene_todas / en_lista: «valores» (lista) o «valor» (lista o texto con | o ;)."""
    v = c.get("valores")
    xs = _partir(v) if v not in (None, "", []) else _partir(c.get("valor"))
    return [x for x in xs if a_texto(x).strip()]


def rango_de(c: dict) -> Tuple[Any, Any]:
    """[mínimo, máximo] de entre / entre_fechas / longitud_entre: valor [a, b], o valor y hasta, o «a | b»."""
    v = c.get("valor")
    if isinstance(v, list) and len(v) == 2:
        return v[0], v[1]
    if c.get("hasta") not in (None, ""):
        return v, c.get("hasta")
    if isinstance(v, str):
        xs = _partir(v)
        if len(xs) == 2:
            return xs[0], xs[1]
    return v, None


_VERDADEROS = {"si", "true", "verdadero", "1", "yes", "x"}
_FALSOS = {"no", "false", "falso", "0", ""}


def _igual_flexible(raw: Any, t: str, v: Any, nv: str) -> bool:
    if t == nv:
        return True
    for a, b in ((raw, v), (v, raw)):
        if isinstance(a, bool):
            nb = normalizar(b)
            return (nb in _VERDADEROS) if a else (nb in _FALSOS)
    if isinstance(raw, (int, float)) or isinstance(v, (int, float)):
        x, y = numero(raw), numero(v)
        return x is not None and y is not None and x == y
    return False


def _parte_inicial(t: str, nv: str, final: bool = False) -> str:
    palabras, k = t.split(" "), len(nv.split(" "))
    return " ".join(palabras[-k:] if final else palabras[:k])


def evaluar_valor(op: str, raw: Any, c: dict, o: Optional[Opciones] = None) -> bool:
    """Si UN valor cumple la condición c (con valor, valores y hasta ya resueltos)."""
    o = o or opciones_de(c)
    texto = a_texto(raw)
    v = c.get("valor")
    if op == "vacio":
        return not texto.strip()
    if op == "no_vacio":
        return bool(texto.strip())
    if op in REGEX:
        hay = Patron(a_texto(v), o.mayusculas, o.tildes).search(texto) is not None
        return hay if op == "coincide" else not hay
    if op in OPS_NUMERO:
        n = numero(raw)
        if n is None:
            return False
        if op == "entre":
            a, b = (numero(x) for x in rango_de(c))
            return a is not None and b is not None and min(a, b) <= n <= max(a, b)
        x = numero(v)
        if x is None:
            return False
        return {"mayor": n > x, "menor": n < x, "mayor_igual": n >= x, "menor_igual": n <= x}[op]
    if op in OPS_FECHA:
        f = fecha(raw)
        if f is None:
            return False
        if op == "entre_fechas":
            a, b = (fecha(x) for x in rango_de(c))
            if a is None or b is None:
                return False
            if _comparar_fechas(a, b) > 0:
                a, b = b, a
            return _comparar_fechas(f, a) >= 0 and _comparar_fechas(f, b) <= 0
        x = fecha(v)
        if x is None:
            return False
        r = _comparar_fechas(f, x)
        return r < 0 if op == "antes_de" else r > 0
    if op in OPS_LONGITUD:
        n = medir(texto, o.contar, o)
        if op == "longitud_entre":
            a, b = (numero(x) for x in rango_de(c))
            return a is not None and b is not None and min(a, b) <= n <= max(a, b)
        x = numero(v)
        if x is None:
            return False
        return {"longitud_igual": n == x, "longitud_mayor": n > x, "longitud_menor": n < x}[op]

    # texto
    t = normalizar(texto, o)
    parecida = o.comparacion == "parecida"

    def igual(x: Any) -> bool:
        nx = normalizar(x, o)
        if parecida:
            return similitud(_para_parecido(t), _para_parecido(nx)) >= o.umbral
        return t == nx or (o.comparacion != "exacta" and _igual_flexible(raw, t, x, nx))

    def contiene(x: Any) -> bool:
        nx = normalizar(x, o)
        if parecida and nx:
            return parecido_en(t, nx, o.umbral) >= o.umbral
        return nx in t

    if op == "parecido":
        return parecido_en(t, normalizar(v, o), o.umbral) >= o.umbral
    if op == "contiene":
        return contiene(v)
    if op == "no_contiene":
        return not contiene(v)
    if op == "igual":
        return igual(v)
    if op == "distinto":
        return not igual(v)
    if op in ("empieza_con", "termina_con"):
        nx = normalizar(v, o)
        if parecida:
            return similitud(_para_parecido(_parte_inicial(t, nx, op == "termina_con")), _para_parecido(nx)) >= o.umbral
        return t.startswith(nx) if op == "empieza_con" else t.endswith(nx)
    if op == "palabra":
        nx = normalizar(v, o)
        if parecida:
            return parecido_en(t, nx, o.umbral) >= o.umbral
        return bool(nx) and re.search(r"(?<!\w)" + re.escape(nx) + r"(?!\w)", t) is not None
    xs = lista_de(c)
    if op == "contiene_alguna":
        return any(contiene(x) for x in xs)
    if op == "contiene_todas":
        return bool(xs) and all(contiene(x) for x in xs)
    if op == "en_lista":
        return any(igual(x) for x in xs)
    if op == "no_en_lista":
        return not any(igual(x) for x in xs)
    raise ValueError(f"operador «{op}» desconocido")


def _resolver_valores(c: dict, render: Callable[[Any], Any]) -> dict:
    op = c.get("operador") or "contiene"
    r = dict(c)
    if op in REGEX:
        return r
    for k in ("valor", "valores", "hasta"):
        if k in r:
            r[k] = render(r[k])
    return r


resolver_valores = _resolver_valores  # las plantillas de valor, valores y hasta (no en los patrones)


def _resumen_valor(c: dict) -> str:
    op = c.get("operador") or "contiene"
    if op in ("vacio", "no_vacio"):
        return ""
    if op in CON_LISTA:
        return " «" + " | ".join(a_texto(x) for x in lista_de(c)) + "»"
    if op in CON_RANGO:
        a, b = rango_de(c)
        return f" «{a_texto(a)}» y «{a_texto(b)}»"
    return f" «{a_texto(c.get('valor'))[:120]}»"


def evaluar_condicion(c: dict, obtener: Callable[[str], Any], render: Callable[[Any], Any] = lambda x: x,
                      defecto: str = "texto") -> Tuple[bool, str]:
    """(si se cumple, una línea que lo explica). Con varios campos basta uno (los negativos: todos)."""
    op = c.get("operador") or "contiene"
    rc = _resolver_valores(c, render)
    o = opciones_de(rc)
    campos = campos_de(rc, defecto)
    vistos = [(campo, obtener(campo)) for campo in campos]
    res = [evaluar_valor(op, raw, rc, o) for _, raw in vistos]
    si = all(res) if op in NEGATIVOS else any(res)
    extra = "" if o.comparacion == "flexible" else f" ({o.comparacion}{f' ≥ {o.umbral}' if o.comparacion == 'parecida' else ''})"
    if op == "parecido":
        extra = f" (≥ {o.umbral})"
    muestra = ", ".join(f"{campo}=«{a_texto(raw)[:80]}»" for campo, raw in vistos if campo != "texto")
    linea = f"{'✓' if si else '✗'} {' o '.join(campos)} {op}{_resumen_valor(rc)}{extra}" + (f" · {muestra}" if muestra else "")
    return si, linea


def evaluar(condiciones: Sequence[dict], modo: str, obtener: Callable[[str], Any], render: Callable[[Any], Any] = lambda x: x,
            defecto: str = "texto") -> Tuple[bool, List[str]]:
    """Todas (por defecto) o alguna de las condiciones."""
    res = [evaluar_condicion(c, obtener, render, defecto) for c in condiciones if isinstance(c, dict)]
    si = any(r for r, _ in res) if modo == "alguna" else all(r for r, _ in res)
    return si, [linea for _, linea in res]


# ---------------------------------------------------------------- validación

def _es_plantilla(x: Any) -> bool:
    return isinstance(x, str) and "{{" in x


def _entero_o_texto(x: Any) -> bool:
    """Un entero de 0 en adelante, también escrito como texto («10»), que es lo que deja el formulario."""
    return _entero_no_negativo(x) or (isinstance(x, str) and x.strip().isdigit())


def _vacio(x: Any) -> bool:
    return x is None or (isinstance(x, str) and not x.strip()) or x == []


def problemas_opciones(c: dict, pre: str = "") -> List[str]:
    p = []
    if "comparacion" in c and c["comparacion"] not in COMPARACIONES:
        p.append(f"{pre}comparacion tiene que ser {', '.join(COMPARACIONES)}")
    if "umbral" in c and not (isinstance(c["umbral"], (int, float)) and not isinstance(c["umbral"], bool) and 0 <= c["umbral"] <= 1):
        p.append(f"{pre}umbral tiene que ser un número entre 0 y 1 (p. ej. 0.85)")
    for k in OPCIONES_BOOL:
        if k in c and not isinstance(c[k], bool):
            p.append(f"{pre}{k} tiene que ser true o false")
    if "contar" in c and c["contar"] not in CONTAR:
        p.append(f"{pre}contar tiene que ser {', '.join(CONTAR)}")
    return p


def problemas_condicion(c: Any, que: str = "", exigir_campo: bool = True) -> List[str]:
    """Lo que está mal en una condición {campo | campos, operador, valor, …}."""
    pre = f"{que}: " if que else ""
    if not isinstance(c, dict):
        return [f"{pre}cada condición tiene que ser un objeto {{campo, operador, valor}}"]
    p: List[str] = []
    if "campos" in c and not (isinstance(c["campos"], list) and c["campos"] and all(isinstance(x, str) and x.strip() for x in c["campos"])):
        p.append(f"{pre}campos tiene que ser una lista de nombres (p. ej. [\"asunto\", \"cuerpo\"])")
    if "campo" in c and not (isinstance(c["campo"], str) and c["campo"].strip()):
        p.append(f"{pre}campo tiene que ser un nombre (asunto, cuerpo, una variable…)")
    if exigir_campo and not campos_de(c, ""):
        p.append(f"{pre}falta campo (o campos)")
    op = c.get("operador", "contiene")
    if op not in OPERADORES:
        return p + [f"{pre}operador «{op}» desconocido (válidos: {', '.join(OPERADORES)})"]
    p += problemas_opciones(c, pre)
    v = c.get("valor")
    if op in CON_VALOR and _vacio(v):
        p.append(f"{pre}el operador «{op}» necesita valor")
    elif op in REGEX:
        try:
            re.compile(a_texto(v))
        except re.error as e:
            p.append(f"{pre}patrón inválido: {e}")
    elif op in OPS_NUMERO and op != "entre" and not _es_plantilla(v) and numero(v) is None:
        p.append(f"{pre}«{op}» necesita un número en valor (p. ej. 1000, «1.234,56»)")
    elif op in OPS_FECHA and op != "entre_fechas" and not _es_plantilla(v) and fecha(v) is None:
        p.append(f"{pre}«{op}» necesita una fecha en valor (AAAA-MM-DD, DD/MM/AAAA, «7 de noviembre de 2026» u «hoy»)")
    elif op in OPS_LONGITUD and op != "longitud_entre" and not _es_plantilla(v) and not _entero_o_texto(v):
        p.append(f"{pre}«{op}» necesita un entero en valor")
    if op in CON_LISTA and not lista_de(c):
        p.append(f"{pre}«{op}» necesita valores (una lista, o un texto con los valores separados por | o ;)")
    if op in CON_RANGO:
        a, b = rango_de(c)
        if _vacio(a) or _vacio(b):
            p.append(f"{pre}«{op}» necesita dos límites: valor [mínimo, máximo] o valor y hasta")
        elif op == "entre" and any(not _es_plantilla(x) and numero(x) is None for x in (a, b)):
            p.append(f"{pre}«entre» necesita dos números")
        elif op == "entre_fechas" and any(not _es_plantilla(x) and fecha(x) is None for x in (a, b)):
            p.append(f"{pre}«entre_fechas» necesita dos fechas")
        elif op == "longitud_entre" and any(not _es_plantilla(x) and not _entero_o_texto(x) for x in (a, b)):
            p.append(f"{pre}«longitud_entre» necesita dos enteros")
    if "valores" in c and not isinstance(c["valores"], list):
        p.append(f"{pre}valores tiene que ser una lista")
    return p


def condiciones_de_filtro(filtro: Any) -> List[dict]:
    """El filtro de tabla_consultar: {campo: valor} (cada par es «igual») o una lista de condiciones."""
    if isinstance(filtro, dict):
        return [{"campo": k, "operador": "igual", "valor": v} for k, v in filtro.items()]
    if isinstance(filtro, list):
        return [x for x in filtro if isinstance(x, dict)]
    return []
