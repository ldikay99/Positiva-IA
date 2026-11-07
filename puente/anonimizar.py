"""
Datos personales en textos colombianos: encontrarlos y anonimizarlos sin red,
para mandar un texto a una IA externa o a un informe sin datos de nadie.

Encuentra cédulas (C.C., C.E., T.I., pasaporte o números con puntos), NIT (con
dígito de verificación o con la palabra NIT), teléfonos (celulares 3xx, fijos
60x y con +57), correos, direcciones (Calle, Carrera, Av., Diagonal,
Transversal… con #), cuentas bancarias (con la palabra cuenta, o tarjetas que
pasan Luhn) y nombres (después de «señor», «paciente», «nombre:»…, los que
empiezan con un nombre de pila común y los que se dan en `nombres_extra`).

Tres modos:
- enmascarar: conserva la forma («1.0**.***.789», «A. M. P.», «a***@empresa.co»);
- etiqueta: «[CÉDULA]», «[NOMBRE]»…;
- seudonimo: «[NOMBRE_1]», «[CÉDULA_2]»…, el mismo para el mismo valor en toda la
  corrida; con el mapa (que solo vive en la corrida) se puede revertir.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Tuple

TIPOS = ("cedula", "nit", "nombre", "telefono", "correo", "direccion", "cuenta")
MODOS = ("enmascarar", "etiqueta", "seudonimo")
ETIQUETAS = {"cedula": "CÉDULA", "nit": "NIT", "nombre": "NOMBRE", "telefono": "TELÉFONO", "correo": "CORREO",
             "direccion": "DIRECCIÓN", "cuenta": "CUENTA"}

_MAY = "A-ZÁÉÍÓÚÑÜ"
_MIN = "a-záéíóúñü"
_PALABRA = rf"(?:[{_MAY}][{_MIN}]+|[{_MAY}]{{2,}})"
_PARTICULA = r"(?:de|del|de\s+la|de\s+las|de\s+los|y|DE|DEL|DE\s+LA)"
_NOMBRE = rf"{_PALABRA}(?:\s+(?:{_PARTICULA}\s+)?{_PALABRA}){{1,4}}"
_NUM_SEP = r"(?:No\.?|N°|Nº|n[uú]mero|nro\.?|#|:)?\s*:?\s*"

# Los nombres de pila más comunes en Colombia (sin tildes, en minúsculas): una secuencia de 2 a 5 palabras con mayúscula
# que empieza por uno de ellos se toma como nombre.
NOMBRES_COMUNES = set("""
ana maria jose juan carlos luis jorge andres andrea diana paula laura camila daniela valentina sofia isabella mariana natalia
carolina claudia sandra martha marta gloria luz adriana patricia monica angela liliana beatriz lucia esperanza rosa elena
juliana catalina alejandra viviana johana yolanda nancy blanca carmen teresa margarita mercedes olga pilar fernanda gabriela
manuela antonia salome sara valeria luisa jimena lorena marcela milena paola tatiana yaneth yesenia ximena alba amparo
david daniel diego felipe fernando francisco gabriel german gustavo hector hernan ivan jaime javier jhon john jonathan jhonatan
julian julio leonardo manuel mario mauricio miguel nelson nicolas oscar pablo pedro rafael ricardo roberto rodrigo santiago
sebastian sergio victor william wilson yeison alejandro alberto alvaro antonio arturo camilo cesar cristian edgar eduardo
enrique esteban fabian fredy guillermo harold hugo humberto jairo jesus orlando omar raul ramiro rene ruben samuel saul
tomas wilmer yesid alexander brayan kevin steven duvan edwin elkin fabio gilberto hernando jhonny libardo luciano
""".split())

_CON_TECLA = re.compile(r"[\s.\-]")


def _sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


def _digitos(t: str) -> str:
    return re.sub(r"\D", "", t)


def dv_nit(nit: str) -> int:
    """El dígito de verificación de un NIT (DIAN)."""
    pesos = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]
    d = _digitos(nit)[::-1]
    s = sum(int(x) * pesos[i] for i, x in enumerate(d) if i < len(pesos))
    r = s % 11
    return r if r < 2 else 11 - r


def luhn(numero: str) -> bool:
    d = [int(x) for x in _digitos(numero)][::-1]
    if not 13 <= len(d) <= 19:
        return False
    total = sum(x if i % 2 == 0 else (x * 2 - 9 if x * 2 > 9 else x * 2) for i, x in enumerate(d))
    return total % 10 == 0


# ------------------------------------------------------------- los detectores
# Cada uno da (inicio, fin) del dato dentro del texto; se aplican en orden y lo ya tomado no se vuelve a tomar.

_RX_CORREO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_RX_NIT_CLAVE = re.compile(rf"(?i:(?<![A-Za-z])N\.?\s?I\.?\s?T\.?)\s*{_NUM_SEP}(\d{{1,3}}(?:[.\s]?\d{{3}}){{2,3}}(?:\s?-\s?\d)?)")
_RX_NIT_DV = re.compile(r"(?<![\d.])(\d{3}\.?\d{3}\.?\d{3})\s?-\s?(\d)(?![\d])")
_RX_TEL = re.compile(r"(?<![\d+])(?:\+\s?57[\s.-]?|(?<!\d)57[\s.-])?(?:\(?\s?60[1-8]\s?\)?[\s.-]?\d{3}[\s.-]?\d{4}|3\d{2}[\s.-]?\d{3}[\s.-]?\d{4})(?![\d])")
_RX_TEL_CLAVE = re.compile(rf"(?i:(?<![A-Za-z])(?:tel[eé]fono|tel|cel(?:ular)?|m[oó]vil|whatsapp|contacto|fijo)\.?)\s*{_NUM_SEP}(\(?\d[\d\s().-]{{5,16}}\d)")
_RX_CUENTA = re.compile(rf"(?i:(?<![A-Za-z])(?:n[uú]mero\s+de\s+cuenta|cuenta(?:\s+(?:de\s+)?(?:ahorros|corriente|bancaria))?|cta\.?(?:\s+(?:de\s+)?(?:ahorros|corriente))?|tarjeta(?:\s+de\s+cr[eé]dito|\s+d[eé]bito)?))\s*{_NUM_SEP}(\d[\d\s-]{{4,24}}\d)")
_RX_TARJETA = re.compile(r"(?<![\d.])(?:\d[ -]?){12,18}\d(?![\d.])")
_RX_CEDULA_CLAVE = re.compile(
    r"(?i:(?<![A-Za-z])(?:c\.\s?c\.?|cc|c[eé]dula(?:\s+de\s+(?:ciudadan[ií]a|extranjer[ií]a))?|c\.\s?e\.?|t\.\s?i\.?|tarjeta\s+de\s+identidad|"
    r"documento(?:\s+de\s+identidad)?(?:\s+n[uú]mero)?|identificad[oa]\s+con(?:\s+(?:la\s+)?(?:c[eé]dula(?:\s+de\s+ciudadan[ií]a)?|c\.\s?c\.?|cc))?|"
    r"pasaporte|n[uú]mero\s+de\s+identificaci[oó]n|identificaci[oó]n|nuip))(?![A-Za-z])\s*" + _NUM_SEP +
    r"(\d{1,3}(?:[.\s]\d{3}){1,3}|\d{5,11}|[A-Z]{1,3}\d{5,9})")
_RX_CEDULA_PUNTOS = re.compile(r"(?<![\d.,$])(\d{1,3}(?:\.\d{3}){2,3})(?![\d,]|\.\d)")
_RX_CEDULA_10 = re.compile(r"(?<![\d.,$])(1[0-2]\d{8})(?![\d.,])")
_RX_DIRECCION = re.compile(
    r"(?i:(?<![A-Za-zÁÉÍÓÚáéíóúñÑ])(?:avenida(?:\s+(?:calle|carrera))?|av\.?|ak\.?|ac\.?|calle|cll\.?|cl\.?|carrera|cra\.?|kra\.?|kr\.?|cr\.?|"
    r"diagonal|dg\.?|transversal|tv\.?|tr\.?|circular|cq\.?|autopista)\s*\d{1,3}\s?[a-z]?(?:\s?bis)?(?:\s?[a-z])?(?:\s+(?:sur|este|norte))?"
    r"\s*(?:#|no\.?|n°|nº|n[uú]mero|nro\.?)\s*\d{1,3}\s?[a-z]?(?:\s?bis)?\s*-\s*\d{1,3}(?:\s+(?:sur|este|norte))?"
    r"(?:\s*,?\s*(?:apto\.?|apartamento|ap\.?|int\.?|interior|torre|casa|oficina|of\.?|local|piso|bloque)\s*[\w-]+){0,3})")
_RX_NOMBRE_CLAVE = re.compile(
    r"(?i:(?<![A-Za-zÁÉÍÓÚáéíóúñÑ])(?:señor(?:a|ita)?|sr\.|sra\.|srta\.|don|doña|dr\.|dra\.|doctor(?:a)?|paciente|trabajador(?:a)?|"
    r"afiliad[oa]|accionante|ciudadan[oa]|nombres?(?:\s+completos?)?(?:\s+del?\s+\w+)?|suscrit[oa]|apoderad[oa]|firmado\s+por|"
    r"atentamente|cordialmente|yo))(?![A-Za-zÁÉÍÓÚáéíóúñÑ])\s*[:,]?\s*(" + _NOMBRE + r")")
_RX_SECUENCIA = re.compile(_NOMBRE)


def _monto(texto: str, i: int, j: int) -> bool:
    """Si el número parece dinero: $ o COP delante, o «pesos»/«COP» después."""
    antes = texto[max(0, i - 6):i].lower()
    despues = texto[j:j + 8].lower()
    return "$" in antes or "cop" in antes or despues.lstrip().startswith(("pesos", "cop", "m/cte", "mcte"))


def _detectar(texto: str, tipos: List[str], nombres_extra: List[str]) -> List[Tuple[int, int, str]]:
    tomados: List[Tuple[int, int, str]] = []

    def libre(i: int, j: int) -> bool:
        return all(j <= a or i >= b for a, b, _ in tomados)

    def poner(i: int, j: int, tipo: str) -> None:
        # sin espacios ni puntuación en los bordes
        while i < j and texto[i] in " \t.,;:-":
            i += 1
        while j > i and texto[j - 1] in " \t.,;:-":
            j -= 1
        if j > i and libre(i, j):
            tomados.append((i, j, tipo))

    quiere = set(tipos)
    if "correo" in quiere:
        for m in _RX_CORREO.finditer(texto):
            poner(m.start(), m.end(), "correo")
    if "nit" in quiere:
        for m in _RX_NIT_CLAVE.finditer(texto):
            poner(m.start(1), m.end(1), "nit")
        for m in _RX_NIT_DV.finditer(texto):
            if dv_nit(m.group(1)) == int(m.group(2)):
                poner(m.start(), m.end(), "nit")
    if "cuenta" in quiere:
        for m in _RX_CUENTA.finditer(texto):
            poner(m.start(1), m.end(1), "cuenta")
        for m in _RX_TARJETA.finditer(texto):
            if luhn(m.group(0)):
                poner(m.start(), m.end(), "cuenta")
    if "telefono" in quiere:
        for m in _RX_TEL.finditer(texto):
            poner(m.start(), m.end(), "telefono")
        for m in _RX_TEL_CLAVE.finditer(texto):
            if 7 <= len(_digitos(m.group(1))) <= 13:
                poner(m.start(1), m.end(1), "telefono")
    if "cedula" in quiere:
        for m in _RX_CEDULA_CLAVE.finditer(texto):
            poner(m.start(1), m.end(1), "cedula")
        for rx in (_RX_CEDULA_PUNTOS, _RX_CEDULA_10):
            for m in rx.finditer(texto):
                if not _monto(texto, m.start(1), m.end(1)):
                    poner(m.start(1), m.end(1), "cedula")
    if "direccion" in quiere:
        for m in _RX_DIRECCION.finditer(texto):
            poner(m.start(), m.end(), "direccion")
    if "nombre" in quiere:
        plano = _plegar_1a1(texto)
        for extra in sorted({x.strip() for x in nombres_extra if isinstance(x, str) and len(x.strip()) >= 3}, key=len, reverse=True):
            buscado = _plegar_1a1(extra)
            for m in re.finditer(r"(?<!\w)" + re.escape(buscado) + r"(?!\w)", plano):
                poner(m.start(), m.end(), "nombre")
        for m in _RX_NOMBRE_CLAVE.finditer(texto):
            poner(m.start(1), m.end(1), "nombre")
        for m in _RX_SECUENCIA.finditer(texto):
            primera = _sin_tildes(m.group(0).split()[0]).casefold()
            if primera in NOMBRES_COMUNES:
                poner(m.start(), m.end(), "nombre")
    return sorted(tomados)


def _plegar_1a1(texto: str) -> str:
    """Sin tildes ni mayúsculas, letra por letra: el resultado tiene el mismo largo que el original (las posiciones sirven)."""
    r = []
    for c in texto:
        b = _sin_tildes(c).casefold()
        r.append(b[0] if b else c)
    return "".join(r)


# ------------------------------------------------------------------ reemplazos

def _enmascarar(valor: str, tipo: str) -> str:
    if tipo == "correo":
        usuario, _, dominio = valor.partition("@")
        return (usuario[:1] + "***@" + dominio) if usuario else valor
    if tipo == "nombre":
        return " ".join(p[0].upper() + "." for p in valor.split() if p[:1].isalpha() and p.lower() not in ("de", "del", "la", "las", "los", "y"))
    if tipo == "direccion":
        return re.sub(r"\d", "*", valor)
    # números: se ocultan todos los dígitos menos los últimos 3 (4 en cuentas), con sus separadores
    dejar = 4 if tipo == "cuenta" else 3
    total = len(_digitos(valor))
    vistos, r = 0, []
    for ch in valor:
        if ch.isdigit():
            vistos += 1
            r.append(ch if vistos > total - dejar else "*")
        elif ch.isalpha() and tipo == "cedula":
            r.append("*")
        else:
            r.append(ch)
    return "".join(r)


def _clave(valor: str, tipo: str) -> str:
    if tipo in ("cedula", "nit", "telefono", "cuenta"):
        d = _digitos(valor)
        if tipo == "telefono" and len(d) == 12 and d.startswith("57"):
            d = d[2:]
        return d or valor
    return re.sub(r"\s+", " ", _sin_tildes(valor)).strip().casefold()


def nuevo_estado() -> Dict[str, Any]:
    """Lo que comparten los nodos anonimizar de una corrida: los seudónimos y el mapa para revertir."""
    return {"tabla": {}, "contador": {}, "mapa": {}}


def anonimizar(texto: Any, tipos: Optional[List[str]] = None, modo: str = "seudonimo", estado: Optional[Dict[str, Any]] = None,
               nombres_extra: Optional[List[str]] = None) -> Tuple[str, Dict[str, int]]:
    """(texto anonimizado, cuántos de cada tipo). `estado` (nuevo_estado()) guarda los seudónimos y el mapa."""
    t = "" if texto is None else str(texto)
    if not t:
        return t, {}
    t = unicodedata.normalize("NFC", t)
    estado = estado if estado is not None else nuevo_estado()
    tramos = _detectar(t, list(tipos or TIPOS), list(nombres_extra or []))
    conteo: Dict[str, int] = {}
    partes, pos = [], 0
    for i, j, tipo in tramos:
        valor = t[i:j]
        conteo[tipo] = conteo.get(tipo, 0) + 1
        if modo == "enmascarar":
            nuevo = _enmascarar(valor, tipo)
        elif modo == "etiqueta":
            nuevo = f"[{ETIQUETAS[tipo]}]"
        else:
            k = (tipo, _clave(valor, tipo))
            nuevo = estado["tabla"].get(k)
            if not nuevo:
                n = estado["contador"].get(tipo, 0) + 1
                estado["contador"][tipo] = n
                nuevo = f"[{ETIQUETAS[tipo]}_{n}]"
                estado["tabla"][k] = nuevo
                estado["mapa"][nuevo] = valor
        partes.append(t[pos:i])
        partes.append(nuevo)
        pos = j
    partes.append(t[pos:])
    return "".join(partes), conteo


def anonimizar_valor(v: Any, hacer: Callable[[str], Tuple[str, Dict[str, int]]], conteo: Dict[str, int],
                     solo_campos: Optional[List[str]] = None, _clave_actual: str = "") -> Any:
    """Anonimiza los textos dentro de objetos y listas (solo en solo_campos, si se dan)."""
    if isinstance(v, str):
        if solo_campos and _clave_actual not in solo_campos:
            return v
        r, c = hacer(v)
        for k, n in c.items():
            conteo[k] = conteo.get(k, 0) + n
        return r
    if isinstance(v, dict):
        return {k: anonimizar_valor(x, hacer, conteo, solo_campos, str(k)) for k, x in v.items()}
    if isinstance(v, list):
        return [anonimizar_valor(x, hacer, conteo, solo_campos, _clave_actual) for x in v]
    if isinstance(v, (int, float)) and not isinstance(v, bool) and (not solo_campos or _clave_actual in solo_campos):
        r, c = hacer(str(v))
        if c:
            for k, n in c.items():
                conteo[k] = conteo.get(k, 0) + n
            return r
    return v


def revertir(texto: Any, mapa: Dict[str, str]) -> Tuple[str, int]:
    """Pone de vuelta los valores originales de cada seudónimo; (texto, cuántos se devolvieron)."""
    t = "" if texto is None else str(texto)
    n = 0
    for seud in sorted(mapa, key=len, reverse=True):
        if seud in t:
            n += t.count(seud)
            t = t.replace(seud, mapa[seud])
    return t, n
