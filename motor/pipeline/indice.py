"""
Recuperacion lexica dentro del workspace: el RAG ligero del agente.

Cada correo trae pocos documentos, asi que no hace falta una base vectorial ni
llamadas extra al modelo: basta con que encontrar un dato no dependa de como
lo escribio el OCR. Tres piezas:

- `plegar`: quita tildes y mayusculas conservando la longitud del texto, para
  que una posicion en el texto plegado sea la misma en el original.
- `paginas_de`: parte un adjunto convertido por sus secciones `## Pagina N`
  (con cualquier sufijo: "(OCR)", "(OCR, confianza 0.58)").
- `clasificar_hoja` y `describir_paginas`: que es cada hoja, en una linea, para
  que el inventario diga donde mirar sin mandar contenido.
- `BM25`: puntua tramos de lineas contra una pregunta en lenguaje natural.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

# ── Plegado ──


@lru_cache(maxsize=4096)
def _plegar_caracter(c: str) -> str:
    base = "".join(ch for ch in unicodedata.normalize("NFD", c)
                   if not unicodedata.combining(ch))
    r = base.lower()
    if len(r) == 1:
        return r
    r = c.lower()
    return r if len(r) == 1 else c


def plegar(texto: str) -> str:
    """
    Minusculas y sin diacriticos ("Cédula" -> "cedula", "ñ" -> "n").

    Conserva la longitud caracter a caracter: una coincidencia hallada en el
    texto plegado se recorta del original con las mismas posiciones.
    """
    if not texto:
        return ""
    if texto.isascii():
        return texto.lower()
    rapido = "".join(ch for ch in unicodedata.normalize("NFD", texto)
                     if not unicodedata.combining(ch)).lower()
    if len(rapido) == len(texto):
        return rapido
    return "".join(_plegar_caracter(c) for c in texto)


_ESCAPE = re.compile(r"\\.", re.DOTALL)


def plegar_patron(patron: str) -> str:
    """
    Pliega una expresion regular sin tocar sus escapes.

    Bajar a minusculas `\\D`, `\\S` o `\\W` les invierte el sentido; solo se
    pliega lo que esta entre escapes.
    """
    partes: List[str] = []
    pos = 0
    for m in _ESCAPE.finditer(patron):
        partes.append(plegar(patron[pos:m.start()]))
        partes.append(m.group(0))
        pos = m.end()
    partes.append(plegar(patron[pos:]))
    return "".join(partes)


_CIFRA = re.compile(r"[\d .,\-]+")


def patron_de_cifra(patron: str) -> Optional[str]:
    """
    Si el patron es una cifra de cinco o mas digitos, la version que admite
    separadores de miles: "1087995336" encuentra "1.087.995.336" y
    "1 087 995 336". Devuelve None si el patron no es una cifra.
    """
    p = patron.strip()
    if not p or not _CIFRA.fullmatch(p):
        return None
    digitos = re.sub(r"\D", "", p)
    if len(digitos) < 5:
        return None
    return r"[ .,\-]?".join(digitos)


# ── Paginas ──

_ENCABEZADO_PAGINA = re.compile(r"^##\s+Pagina\s+(\d+)\b(.*)$", re.IGNORECASE)
_CONFIANZA = re.compile(r"confianza\s*[:=]?\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)


@dataclass
class Pagina:
    """Una seccion `## Pagina N` de un adjunto, por rango de lineas."""

    numero: int
    inicio: int            # linea del encabezado
    fin: int               # exclusivo
    ocr: bool = False
    confianza: Optional[float] = None


def paginas_de(lineas: Sequence[str]) -> List[Pagina]:
    """Secciones de pagina de un documento. Vacio si no tiene."""
    paginas: List[Pagina] = []
    for n, linea in enumerate(lineas):
        m = _ENCABEZADO_PAGINA.match(linea.strip())
        if not m:
            continue
        if paginas:
            paginas[-1].fin = n
        sufijo = m.group(2) or ""
        conf = _CONFIANZA.search(sufijo)
        confianza = None
        if conf:
            try:
                confianza = float(conf.group(1).replace(",", "."))
            except ValueError:
                confianza = None
        paginas.append(Pagina(
            numero=int(m.group(1)), inicio=n, fin=len(lineas),
            ocr="ocr" in sufijo.lower(), confianza=confianza))
    return paginas


def pagina_de_linea(paginas: Sequence[Pagina], linea: int) -> Optional[int]:
    for p in paginas:
        if p.inicio <= linea < p.fin:
            return p.numero
    return None


# ── Que es cada hoja ──

# (expresion sobre texto plegado, peso). Las marcas fuertes pesan 3: con una
# sola basta. Las debiles suman para desempatar.
_CLASES: Dict[str, List[Tuple[str, int]]] = {
    "cedula": [
        (r"identificacion personal", 3), (r"registrador(?:a|ia)? nacional", 3),
        (r"indice derecho", 3), (r"\bestatura\b", 3), (r"g\.?\s?s\.?\s*rh", 3),
        (r"fecha y lugar de (?:nacimiento|expedicion)", 3),
        (r"cedula de (?:ciudadania|extranjeria)", 1),
        (r"tarjeta de identidad", 1), (r"documento de identidad", 1),
        (r"republica de colombia", 1), (r"\bnuip\b", 2),
        (r"lugar de nacimiento", 1), (r"\bpasaporte\b", 1),
    ],
    "historia_clinica": [
        (r"historia clinica", 3), (r"\bepicrisis\b", 3),
        (r"motivo de consulta", 3), (r"enfermedad actual", 3),
        (r"examen fisico", 2), (r"signos vitales", 2), (r"\bcie\s?-?10\b", 2),
        (r"evolucion", 1), (r"diagnostico", 1), (r"antecedentes", 1),
        (r"plan de manejo", 2), (r"\bpaciente\b", 1), (r"\bips\b", 1),
        (r"tension arterial|frecuencia cardiaca", 2), (r"\bhc\b", 1),
        (r"nota de (?:evolucion|ingreso|egreso)", 3), (r"\bconsulta\b", 1),
    ],
    "incapacidad": [
        (r"certificado de incapacidad", 3), (r"incapacidad medica", 3),
        (r"dias de incapacidad", 3), (r"\bincapacidad(?:es)?\b", 2),
        (r"\bprorroga\b", 2), (r"licencia de (?:maternidad|paternidad)", 3),
        (r"fecha (?:de )?inicio", 1), (r"fecha (?:de )?(?:fin|final|terminacion)", 1),
    ],
    "furat": [
        (r"\bfurat\b", 3), (r"\bfurel\b", 3),
        (r"reporte de (?:accidente|enfermedad)", 3),
        (r"informe (?:de|del) accidente", 3), (r"persona que se accidento", 3),
        (r"investigacion (?:de|del) (?:accidente|incidente)", 3),
        (r"accidente de trabajo", 2), (r"\baccidente\b", 1),
        (r"descripcion del (?:accidente|evento)", 2), (r"jefe inmediato", 1),
        (r"testigos?", 1),
    ],
    "dictamen": [
        (r"\bdictamen\b", 3), (r"perdida de (?:la )?capacidad laboral", 3),
        (r"\bpcl\b", 2), (r"junta (?:regional|nacional) de calificacion", 3),
        (r"calificacion (?:de|del) origen", 3), (r"\bcalificacion\b", 1),
        (r"deficiencia", 1), (r"manual unico", 2), (r"fecha de estructuracion", 3),
        (r"medicina laboral", 1),
    ],
    "peticion": [
        (r"derecho de peticion", 3), (r"articulo 23", 2), (r"ley 1755", 3),
        (r"\bsolicito\b", 1), (r"respetuosamente", 2), (r"\bpeticion\b", 1),
        (r"\bsolicitud\b", 1), (r"cordial(?:mente)? saludo", 1),
        (r"\batentamente\b", 1), (r"\bhechos\b", 1), (r"\bpretensiones\b", 2),
        (r"\bsenores\b", 1), (r"\breclamo\b", 1), (r"\bqueja\b", 1),
    ],
    "tutela": [
        (r"accion de tutela", 3), (r"\bjuzgado\b", 3), (r"\baccionante\b", 3),
        (r"\baccionad[oa]s?\b", 3), (r"auto admisorio", 3), (r"\bfallo\b", 1),
        (r"\btutela\b", 2), (r"\bsentencia\b", 1), (r"\bdesacato\b", 3),
        (r"\bincidente\b", 1), (r"secretari[oa] (?:del juzgado|judicial)", 2),
        (r"rama judicial", 3),
    ],
    "certificado": [
        (r"certificado laboral", 3), (r"certificacion laboral", 3),
        (r"certificado de aportes", 3), (r"certificacion de aportes", 3),
        (r"(?:certifica|hace constar)(?: que)?", 2), (r"a quien (?:le )?interese", 3),
        (r"\blabora\b|\blaboro\b|presta(?:ba)? sus servicios", 2),
        (r"contrato (?:a termino|de trabajo|laboral)", 2), (r"\bcargo\b", 1),
        (r"\bsalario\b", 1), (r"realizo los siguientes aportes", 3),
        (r"certificado de afiliacion", 3),
    ],
    "planilla": [
        (r"\bplanilla\b", 2), (r"\bpila\b", 3), (r"\baportante\b", 2),
        (r"\bibc\b", 2), (r"periodo (?:de )?(?:pago|cotizacion|salud|pension)", 2),
        (r"dias cotizados", 3), (r"operador de informacion", 3),
        (r"planilla integrada", 3), (r"\bcotizante\b", 2),
        (r"tipo de cotizante", 3), (r"\bnovedad(?:es)?\b", 1),
    ],
    "factura": [
        (r"factura (?:electronica|de venta)", 3), (r"\bcufe\b", 3),
        (r"\bsubtotal\b", 2), (r"\biva\b", 1), (r"valor total", 2),
        (r"resolucion (?:dian|de facturacion)", 3), (r"\bfactura\b", 1),
        (r"total a pagar", 2), (r"\bcuenta de cobro\b", 3),
    ],
    "formulario": [
        (r"\bformulario\b", 2), (r"\bformato\b", 1), (r"\bdiligenci", 2),
        (r"firma del (?:solicitante|afiliado|trabajador|representante)", 1),
        (r"tipo de documento", 1), (r"(?:primer|segundo) apellido", 2),
        (r"(?:primer|segundo) nombre", 1), (r"\bmarque\b|\bcasilla\b", 2),
        (r"version\s*:?\s*\d", 1), (r"codigo\s*:?\s*[a-z]{2,4}-", 1),
        (r"registro unico tributario|uso exclusivo de la dian", 3),
    ],
}
_CLASES_RE = {c: [(re.compile(p), w) for p, w in lista]
              for c, lista in _CLASES.items()}

# Puntaje minimo para no llamarla "otro".
_UMBRAL_CLASE = 3
# El encabezado de la hoja dice mas que el resto: cuenta doble.
_CABECERA_HOJA = 600

_ILEGIBLE = re.compile(r"^_\((?:pagina sin texto extraible|no se pudo leer)", re.M)


def clasificar_hoja(texto: str) -> str:
    """Que es una hoja, por palabras clave sobre el texto plegado."""
    plegado = plegar(texto or "")
    cuerpo = plegado.strip()
    if not cuerpo or (_ILEGIBLE.search(cuerpo) and len(cuerpo) < 300):
        return "ilegible"
    # Una fuente sin mapa de caracteres sale como basura de control: hay
    # "texto", pero nada que leer.
    raros = sum(1 for c in cuerpo if c not in "\n\t\r"
                and unicodedata.category(c) in ("Cc", "Co", "Cn"))
    if raros > 0.1 * len(cuerpo):
        return "ilegible"
    cabeza = plegado[:_CABECERA_HOJA]
    puntajes: Dict[str, int] = {}
    for clase, marcas in _CLASES_RE.items():
        total = 0
        for rx, peso in marcas:
            if rx.search(plegado):
                total += peso
                if rx.search(cabeza):
                    total += peso
        puntajes[clase] = total
    clase, puntaje = max(puntajes.items(), key=lambda kv: kv[1])
    return clase if puntaje >= _UMBRAL_CLASE else "otro"


def _identificaciones(texto: str) -> Tuple[int, List[str]]:
    """
    Cuantos numeros con marca de documento de identidad hay, y que marcas.

    Solo cuenta los que traen marca (CC, NIT, TI, CE o el rotulo pegado): un
    numero suelto no le dice al agente que la hoja identifica a alguien.
    """
    try:
        from utils.documento_candidatos import extraer_candidatos_documento
        candidatos = extraer_candidatos_documento(texto or "")
    except Exception:
        return 0, []
    n = 0
    marcas: set = set()
    for c in candidatos:
        propias = []
        if c.get("tiene_cc"):
            propias.append("CC")
        if c.get("tiene_nit"):
            propias.append("NIT")
        if c.get("tiene_ti"):
            propias.append("TI")
        if c.get("tiene_ce"):
            propias.append("CE")
        if c.get("tiene_pasaporte"):
            propias.append("PA")
        if propias or c.get("marca_pegada"):
            n += 1
            marcas.update(propias)
    orden = ("CC", "NIT", "TI", "CE", "PA")
    return n, [m for m in orden if m in marcas]


@dataclass
class DescripcionHoja:
    desde: int
    hasta: int
    tipo: str
    ocr: bool = False
    confianza: Optional[float] = None
    ids: int = 0
    marcas: List[str] = field(default_factory=list)

    def linea(self) -> str:
        rango = f"p{self.desde}" if self.desde == self.hasta else f"p{self.desde}-{self.hasta}"
        partes = [rango, self.tipo]
        if self.ocr:
            partes.append("ocr" if self.confianza is None
                          else f"ocr{self.confianza:.2f}")
        if self.ids:
            partes.append(f"id{self.ids}" + (":" + "/".join(self.marcas)
                                             if self.marcas else ""))
        return " ".join(partes)


def describir_texto(texto: str) -> str:
    """Una linea para un documento sin secciones de pagina."""
    tipo = clasificar_hoja(texto)
    n, marcas = _identificaciones(texto)
    d = DescripcionHoja(0, 0, tipo, ids=n, marcas=marcas)
    return " ".join(d.linea().split(" ")[1:])


# Desde cuantas paginas se agrupan rangos aunque la hoja tenga documentos.
_PAGINAS_PARA_AGRUPAR_TODO = 8


def hojas_de(lineas: Sequence[str],
             paginas: Optional[Sequence[Pagina]] = None) -> List[DescripcionHoja]:
    """La descripcion de cada hoja, sin agrupar."""
    if paginas is None:
        paginas = paginas_de(lineas)
    hojas: List[DescripcionHoja] = []
    for p in paginas:
        texto = "\n".join(lineas[p.inicio + 1:p.fin])
        n, marcas = _identificaciones(texto)
        hojas.append(DescripcionHoja(
            p.numero, p.numero, clasificar_hoja(texto),
            ocr=p.ocr, confianza=p.confianza, ids=n, marcas=marcas))
    return hojas


# Cuanto se comprime el indice de un adjunto. El inventario entero viaja en
# una respuesta que el agente recorta, asi que un adjunto de treinta hojas
# variadas tiene que poder decirse en pocas lineas.
NIVEL_DETALLE = 0      # una linea por hoja o rango sin documentos
NIVEL_RANGOS = 1       # rangos del mismo tipo aunque traigan documentos
NIVEL_POR_TIPO = 2     # una linea por tipo con sus paginas
NIVELES = (NIVEL_DETALLE, NIVEL_RANGOS, NIVEL_POR_TIPO)


def _rangos(numeros: Sequence[int]) -> str:
    partes: List[str] = []
    for n in sorted(numeros):
        if partes and partes[-1][1] + 1 == n:
            partes[-1][1] = n
        else:
            partes.append([n, n])
    return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in partes)


def describir_paginas(lineas: Sequence[str],
                      paginas: Optional[Sequence[Pagina]] = None,
                      nivel: int = NIVEL_DETALLE,
                      hojas: Optional[List[DescripcionHoja]] = None) -> List[str]:
    """
    Una linea corta por hoja: "p3 cedula ocr0.58 id1:CC".

    Las hojas seguidas del mismo tipo se agrupan en un rango cuando no traen
    documentos; si el adjunto es largo (o `nivel` lo pide) se agrupan siempre.
    En `NIVEL_POR_TIPO` queda una linea por tipo ("historia_clinica
    p1,6-7,11") mas una con las hojas que traen documentos ("id p1,5:CC").
    """
    if hojas is None:
        hojas = hojas_de(lineas, paginas)
    hojas = [DescripcionHoja(h.desde, h.hasta, h.tipo, h.ocr, h.confianza,
                             h.ids, list(h.marcas)) for h in hojas]
    if nivel >= NIVEL_POR_TIPO:
        por_tipo: Dict[str, List[int]] = {}
        for h in hojas:
            por_tipo.setdefault(h.tipo, []).append(h.desde)
        salida_tipo = [f"{t} p{_rangos(ns)}" for t, ns in por_tipo.items()]
        con_ids = [h.desde for h in hojas if h.ids]
        if con_ids:
            marcas = [m for m in ("CC", "NIT", "TI", "CE", "PA")
                      if any(m in h.marcas for h in hojas)]
            salida_tipo.append(f"id p{_rangos(con_ids)}"
                               + (":" + "/".join(marcas) if marcas else ""))
        ocr = [h.desde for h in hojas if h.ocr]
        if ocr:
            confs = [h.confianza for h in hojas if h.confianza is not None]
            salida_tipo.append(f"ocr p{_rangos(ocr)}"
                               + (f" min{min(confs):.2f}" if confs else ""))
        return salida_tipo
    agrupar_todo = nivel >= NIVEL_RANGOS or len(hojas) > _PAGINAS_PARA_AGRUPAR_TODO
    salida: List[DescripcionHoja] = []
    for h in hojas:
        prev = salida[-1] if salida else None
        if (prev is not None and prev.tipo == h.tipo and prev.ocr == h.ocr
                and prev.hasta + 1 == h.desde
                and (agrupar_todo or (not prev.ids and not h.ids))):
            prev.hasta = h.desde
            prev.ids += h.ids
            prev.marcas = [m for m in ("CC", "NIT", "TI", "CE", "PA")
                           if m in prev.marcas or m in h.marcas]
            if h.confianza is not None:
                prev.confianza = (h.confianza if prev.confianza is None
                                  else min(prev.confianza, h.confianza))
            continue
        salida.append(h)
    return [h.linea() for h in salida]


# ── BM25 ──

PALABRA = re.compile(r"[a-z0-9]+")
_VACIAS = frozenset("""
a al algo ante con contra cual cuales de del desde donde el ella ellas ellos en
entre era es esa ese eso esta este esto la las le les lo los mas me mi mis muy
nada ni no nos o otra otro para pero por que quien se segun ser si sin sobre su
sus te tiene tu un una uno unos unas y ya hay cual como cuando esta estan fue
documento documentos dato datos numero
""".split())


def terminos(texto: str) -> List[str]:
    """Palabras plegadas, sin vacias. Las cifras se unen sin separadores."""
    plegado = plegar(texto)
    # "1.087.995.336" -> "1087995336": una cedula escrita con puntos es el
    # mismo termino que sin ellos.
    plegado = re.sub(r"(?<=\d)[.,](?=\d)", "", plegado)
    # "C.C." y "c. c." son la sigla de la cedula, no dos letras sueltas.
    plegado = re.sub(r"\b([a-z])\.\s?([a-z])\.", r"\1\2 ", plegado)
    return [raiz(t) for t in PALABRA.findall(plegado)
            if t not in _VACIAS and (len(t) > 1 or t.isdigit())]


def raiz(t: str) -> str:
    """Plural a singular, lo justo para que "incapacidades" sea "incapacidad"."""
    if len(t) > 4 and t.isalpha():
        if t.endswith("des") or t.endswith("nes") or t.endswith("res"):
            return t[:-2]
        if t.endswith("s"):
            return t[:-1]
    return t


class BM25:
    """BM25 clasico (k1=1.5, b=0.75) sobre una lista de tramos ya tokenizados."""

    def __init__(self, tramos: Sequence[Sequence[str]], k1: float = 1.5,
                 b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.frecuencias = [Counter(t) for t in tramos]
        self.largos = [len(t) for t in tramos]
        self.promedio = (sum(self.largos) / len(self.largos)) if tramos else 0.0
        df: Counter = Counter()
        for f in self.frecuencias:
            df.update(f.keys())
        n = len(tramos)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def puntaje(self, i: int, consulta) -> float:
        """
        `consulta` es una lista de terminos o un dict termino -> peso (los
        sinonimos de `expandir` pesan menos que lo que se pregunto).
        """
        pesos = consulta if isinstance(consulta, dict) else dict.fromkeys(consulta, 1.0)
        f = self.frecuencias[i]
        largo = self.largos[i] or 1
        total = 0.0
        for t, peso in pesos.items():
            tf = f.get(t, 0)
            if not tf:
                continue
            norma = self.k1 * (1 - self.b + self.b * largo / (self.promedio or 1))
            total += peso * self.idf.get(t, 0.0) * tf * (self.k1 + 1) / (tf + norma)
        return total


# Como se escribe lo mismo en estos documentos. Solo se usa al consultar: la
# pregunta la redacta el modelo y el documento lo redacto otro.
_SINONIMOS: Dict[str, Tuple[str, ...]] = {
    "cedula": ("cc", "identificacion", "identificado", "identificada"),
    "cc": ("cedula", "identificacion"),
    "identificacion": ("cc", "cedula", "identificado", "identificada"),
    "trabajador": ("afiliado", "empleado", "colaborador", "paciente", "accidentado"),
    "afiliado": ("trabajador", "cotizante", "paciente"),
    "afectado": ("afiliado", "trabajador", "paciente", "accidentado"),
    "paciente": ("afiliado", "usuario"),
    "empresa": ("empleador", "nit", "razon", "aportante"),
    "empleador": ("empresa", "nit", "aportante"),
    "telefono": ("celular", "tel", "cel", "movil", "contacto"),
    "celular": ("telefono", "cel", "movil"),
    "direccion": ("domicilio", "residencia", "dir"),
    "domicilio": ("direccion", "residencia"),
    "nombre": ("nombres", "apellido", "apellidos"),
    "sexo": ("genero", "masculino", "femenino"),
    "accidente": ("evento", "siniestro", "furat"),
    "incapacidad": ("dias", "prorroga"),
    "dictamen": ("calificacion", "pcl", "junta"),
}


def expandir(consulta: Sequence[str], peso: float = 0.5) -> Dict[str, float]:
    """Los terminos de la pregunta (peso 1) mas sus sinonimos (peso menor)."""
    pesos: Dict[str, float] = {t: 1.0 for t in consulta}
    for t in consulta:
        for s in _SINONIMOS.get(t, ()):
            s = raiz(s)
            if s not in pesos:
                pesos[s] = peso
    return pesos
