"""
Extractor determinista de NUMEROS CANDIDATOS a documento de identidad.

Idea: en vez de pedirle al LLM que "encuentre y limpie" el documento (lo que
provoca alucinaciones cuando el numero no esta), el regex extrae TODOS los
numeros que podrian ser un documento, ya limpios y con su contexto, filtrando
lo que claramente NO es documento (celulares, fechas, radicados, telefonos).

El LLM despues solo ELIGE cual candidato corresponde al afectado/remitente.
Si la lista esta vacia, no hay nada que elegir -> "no encontrado" real.
"""
import re
from typing import Dict, List, Tuple

from utils.tipos_documento import MAX_DIGITOS, MIN_DIGITOS, sin_leyendas

# Separadores de miles que aparecen dentro de un numero. Ademas del punto, el
# espacio y el guion, se admiten las comillas y acentos que deja el OCR o el
# teclado: "901`642982" es un NIT, y sin la comilla el barrido solo veria
# "642982", que es un NIT mutilado con forma de cedula.
_SEPARADORES = r".\s\-`´'‘’"
# Token numerico. Desde cuatro digitos, porque las cedulas antiguas son
# cortas. Lo que decide si un token corto se acepta es la ventana de longitud.
#
# Empieza en un limite de verdad, no dentro de otra cosa. Sin esa condicion el
# barrido parte por la mitad lo que no es un documento y produce un numero con
# forma de cedula: "ENT3413618689913" da "3413618689913" y "ABC12345" da
# "12345". Se excluye tambien el digito anterior, porque de lo contrario basta
# con arrancar una posicion mas adelante: "S001223884" seguiria dando
# "01223884". Un documento alfanumerico se reconoce entero, con su propio
# patron, no cortandole las letras.
_TOKEN = re.compile(rf"(?<![A-Za-z0-9])\d[\d{_SEPARADORES}]{{2,16}}\d")

# Longitudes admitidas. Los numeros SIN marca de contexto pasan por una
# ventana estrecha; los que la traen se miden contra el rango completo de
# `utils.tipos_documento`, que es la unica definicion de esos limites.
MIN_SIN_MARCA, MAX_SIN_MARCA = 6, 11
MIN_CON_MARCA, MAX_CON_MARCA = MIN_DIGITOS, MAX_DIGITOS

# Pasaporte: alfanumerico. Un extractor de digitos no lo ve nunca.
_PASAPORTE = re.compile(
    r"\bpasaporte\b\s*(?:n[o°º]\.?|nro\.?|#)?\s*[:\-]?\s*"
    r"([A-Z]{1,3}\s?\d{5,9}|\d{6,9}[A-Z]{0,2})",
    re.IGNORECASE,
)

# Fecha tipo 12/03/2025, 12.03.2025, 2025-03-12
_FECHA = re.compile(r"\b\d{1,4}[./\-]\d{1,2}[./\-]\d{2,4}\b")

# Direcciones de correo y URL. Sus digitos no identifican a nadie: la parte
# local de "456086@empresa.com" tiene forma de documento pero es un buzon.
_CORREO = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

# Titulo con el que el workspace encabeza cada adjunto ("# 17139237 (5).pdf").
# El nombre del archivo suele ser el numero de la planilla o del radicado, y
# ahi no identifica a ninguna persona.
_TITULO_DE_ARCHIVO = re.compile(
    r"^#{1,6}[^\n]*\.(?:pdf|docx?|xlsx?|xlsm|jpe?g|png|gif|tiff?|zip|rar|p7m"
    r"|txt|csv|pptx?|eml|msg)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Marcadores de contexto
_RE_CC = re.compile(r"\bc\.?\s?c\.?\b|c[eé]dula|identificad|documento|afiliad|trabajador|paciente|titular")
# Igual que `_RE_CC` pero sin las palabras genericas. "documento" o
# "identificado" acompañan a cualquier tipo, y "cedula" tambien a la de
# extranjeria: solo esto afirma que el tipo es cedula de ciudadania.
_RE_CC_EXPLICITA = re.compile(
    r"\bc\.?\s?c\.?\b|c[eé]dula\s+de\s+ciudadan[ií]a")
_RE_NIT = re.compile(r"\bnit\b")
_RE_TI = re.compile(r"\bt\.?\s?i\.?\b|tarjeta de identidad")
_RE_CE = re.compile(r"\bc\.?\s?e\.?\b|extranjer")
_RE_TEL = re.compile(
    r"\b(tel|tel[eé]fono|cel|celular|m[oó]vil|contacto|whatsapp|fax)\b"
    r"|l[ií]nea\s+(directa|nacional|gratuita|de\s+atenci[oó]n)"
)
_RE_RADICADO = re.compile(r"(ent|rad|sal|exp|radicad|siniestr)[\s\-]?\d")
_RE_FECHA_CTX = re.compile(r"\bfecha\b")
_RE_NACIMIENTO = re.compile(r"nacimient|nacid[oa]")

# Meses y encabezados de reenvio. El OCR de "de julio de 2026 11:38" deja
# "2026 11", que tiene forma de documento de 6 digitos.
_RE_MES = re.compile(
    r"\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|"
    r"octubre|noviembre|diciembre|january|february|march|april|june|july|"
    r"august|september|october|november|december|enviado|sent|received)\b"
)
_ANIO = re.compile(r"^(19|20)\d{2}[.\s\-]")

# Lo que aparece pegado justo antes del numero manda sobre el resto de la
# ventana: en "cedula 1073.326.9240 celular 412214.6968" el segundo numero es
# un telefono.
_ANTES = 30
_PREFIJO_TELEFONO = re.compile(
    r"(?:\b(?:tel|tel[eé]fono|cel|celular|m[oó]vil|whatsapp|fax|ext)\b"
    r"|l[ií]nea(?:\s+\w+){0,4}"
    r"|\(\s*60\d\s*\))[\s.:#\-]*(?:no\.?|n[°º]\.?)?[\s.:#\-]*$"
)
_PREFIJO_REFERENCIA = re.compile(
    r"\b(?:recibo|acta|libro|orden|oficio|factura|resoluci[oó]n|escritura|"
    r"matr[ií]cula|registro|proceso|c[oó]digo|snies|cuenta|p[oó]liza|"
    r"contrato|autorizaci[oó]n|pila|planilla|remesa|comprobante)\b[\s\w]{0,16}?"
    r"(?:no\.?|n[°º]\.?|nro\.?|#|:)[\s.\-]*[a-z]?[\s.\-]*$"
)
# Marca de documento en las palabras justo antes del numero. Decide si se
# aplica el rango ancho de longitud. No se ancla al final porque el rotulo real
# viene con palabras en medio: "Cedula de extranjeria No. 123456".
_MARCA_PEGADA = re.compile(
    r"\b(?:c\.?\s?c\.?|c\.?\s?e\.?|t\.?\s?i\.?|n\.?\s?i\.?t\.?"
    r"|p\.?\s?p\.?\s?t\.?|p\.?\s?e\.?\s?p\.?|c[eé]dula|documento"
    r"|identificaci[oó]n|nuip|registro\s+civil)\b"
)
# Que tipo dice el rotulo PEGADO al numero, de la marca mas especifica a la
# menos. Es distinto de las banderas `tiene_*`, que miran toda la ventana: el
# mismo numero puede salir en una firma con "c.c." delante y en una columna de
# planilla rotulada "NIT", y sumarlas daba NIT para la cedula de una persona.
_MARCAS_TIPO_PEGADAS = (
    ("PA", re.compile(r"\bpasaporte\b|\bp\.?\s?a\.?\b")),
    ("NI", re.compile(r"\bn\.?\s?i\.?t\.?\b")),
    ("CE", re.compile(r"\bc\.?\s?e\.?\b|c[eé]dula\s+de\s+extranjer[ií]a")),
    ("TI", re.compile(r"\bt\.?\s?i\.?\b|tarjeta\s+de\s+identidad")),
    ("CC", re.compile(r"\bc\.?\s?c\.?\b|c[eé]dula\s+de\s+ciudadan[ií]a")),
)
# Documentos de persona natural. Si una aparicion trae uno de estos pegado y
# otra trae NIT, manda el de persona: en la planilla de aportes la columna del
# aportante se rotula NIT y un independiente escribe ahi su cedula.
TIPOS_PEGADOS_DE_PERSONA = ("PA", "CE", "TI", "CC")


def _tipo_pegado(antes: str) -> str:
    """Sigla del tipo que rotula el numero, o cadena vacia."""
    for sigla, patron in _MARCAS_TIPO_PEGADAS:
        if patron.search(antes):
            return sigla
    return ""


_PREFIJO_MONTO = re.compile(
    r"(?:\$|\bvalor\b|\bmil(?:lones)?\b|\bpesos\b|\bsuma\b|\btotal\b)\s*\(?\s*$"
)
_SUFIJO_DECIMAL = re.compile(r"^[.,]\d{2}\b")

# Rejilla de un formulario escaneado: "1 2 3 4 5 6 7 8 9 10 11". El OCR la lee
# como una racha de digitos, pero ningun documento se escribe asi.
_CONSECUTIVOS = "".join(str(i) for i in range(1, 100))


def _es_rejilla(raw: str) -> bool:
    """
    Solo cuenta como rejilla si el OCR dejo separadores entre las cifras.

    Un numero corrido como "123456" es un documento posible y existe gente con
    esa cedula; "1 2 3 4 5 6" es la numeracion de un formulario. Sin exigir el
    separador, la regla borraria documentos reales.
    """
    grupos = [g for g in re.split(rf"[{_SEPARADORES}]+", raw.strip()) if g]
    if len(grupos) < 2:
        return False
    if len(grupos) >= 5 and all(len(g) <= 2 for g in grupos):
        return True
    digitos = re.sub(r"\D", "", raw)
    return len(digitos) >= 6 and digitos in _CONSECUTIVOS


def extraer_candidatos_documento(texto: str) -> List[Dict]:
    """
    Devuelve una lista de candidatos: cada uno con el numero limpio (solo
    digitos), su contexto y banderas de que tipo de documento sugiere.
    Sin duplicados. Sin celulares/fechas/radicados/telefonos.
    """
    if not texto:
        return []

    candidatos: List[Dict] = []
    vistos = set()

    # Tramos que el barrido de digitos no debe tocar.
    tomados: List[Tuple[int, int]] = [
        (m.start(), m.end())
        for patron in (_CORREO, _URL, _TITULO_DE_ARCHIVO)
        for m in patron.finditer(texto)
    ]

    # Pasaporte primero: es alfanumerico y el barrido de digitos no lo ve.
    # Su tramo queda reservado para que el barrido no vuelva a extraer los
    # digitos de dentro ("AV1234567" -> "1234567") y fabrique una ambiguedad.
    for m in _PASAPORTE.finditer(texto):
        numero = re.sub(r"\s", "", m.group(1)).upper()
        tomados.append((m.start(1), m.end(1)))
        if numero in vistos:
            continue
        vistos.add(numero)
        ini = max(0, m.start() - 45)
        candidatos.append({
            "numero": numero,
            "contexto": " ".join(texto[ini: m.end() + 15].split()).lower(),
            "marca_pegada": True,
            "tipos_pegados": {"PA"},
            "tipo_pegado": "PA",
            "cc_explicita": False,
            "tiene_cc": False, "tiene_nit": False,
            "tiene_ti": False, "tiene_ce": False,
            "tiene_pasaporte": True,
        })

    for m in _TOKEN.finditer(texto):
        if any(m.start() < fin and ini < m.end() for ini, fin in tomados):
            continue  # cae dentro de un correo, una URL o un pasaporte
        raw = m.group(0)
        digitos = re.sub(r"\D", "", raw)

        # Contexto: 45 chars antes + 15 despues, normalizado
        ini = max(0, m.start() - 45)
        contexto_raw = texto[ini : m.end() + 15]
        contexto = " ".join(contexto_raw.split()).lower()
        antes = " ".join(texto[max(0, m.start() - _ANTES): m.start()].split()).lower()
        despues = texto[m.end(): m.end() + 4]

        # El rango depende de si el texto dice de que documento se trata.
        marcado = bool(_MARCA_PEGADA.search(antes))
        minimo, maximo = (
            (MIN_CON_MARCA, MAX_CON_MARCA) if marcado
            else (MIN_SIN_MARCA, MAX_SIN_MARCA)
        )
        if not (minimo <= len(digitos) <= maximo):
            continue

        # --- DESCARTES deterministas ---
        if _FECHA.search(raw):
            continue  # es una fecha
        if len(digitos) == 10 and digitos[0] == "3":
            continue  # celular colombiano (3xx...)
        if _es_rejilla(raw):
            continue  # rejilla numerada de un formulario escaneado
        if _ANIO.match(raw) and _RE_MES.search(contexto):
            continue  # ano y hora de un encabezado de reenvio
        if _RE_NACIMIENTO.search(contexto):
            continue  # fecha de nacimiento
        if _RE_RADICADO.search(contexto):
            continue  # numero de radicado/siniestro/expediente
        if _PREFIJO_TELEFONO.search(antes):
            continue  # el numero viene precedido de "celular", "linea", "(601)"
        if _PREFIJO_REFERENCIA.search(antes):
            continue  # recibo, acta, orden, codigo: no identifica a una persona
        if _PREFIJO_MONTO.search(antes) or _SUFIJO_DECIMAL.match(despues):
            continue  # importe en pesos
        if _RE_TEL.search(contexto) and not _RE_CC.search(contexto):
            continue  # telefono/celular/contacto sin marca de cedula
        if _RE_FECHA_CTX.search(contexto) and not (_RE_CC.search(contexto) or _RE_NIT.search(contexto)):
            continue  # numero junto a la palabra "fecha"

        # Las siglas del menu de casillas de un formulario no dicen el tipo de
        # documento de nadie: "Tipo Doc CNV RC TI CC PEP CE PA SC" pondria las
        # cuatro banderas a la vez. Se miran sobre el contexto sin leyendas.
        ctx_sin_menu = sin_leyendas(contexto)

        nuevo = {
            "numero": digitos,
            "contexto": contexto,
            # `marca_pegada` exige el rotulo justo antes del numero; las
            # banderas `tiene_*` miran toda la ventana, donde en una tabla
            # densa cabe el rotulo de otra columna.
            "marca_pegada": marcado,
            "tipos_pegados": {_tipo_pegado(antes)} - {""},
            "tipo_pegado": _tipo_pegado(antes),
            "tiene_cc": bool(_RE_CC.search(ctx_sin_menu)),
            "cc_explicita": bool(_RE_CC_EXPLICITA.search(ctx_sin_menu)),
            "tiene_nit": bool(_RE_NIT.search(ctx_sin_menu)),
            "tiene_ti": bool(_RE_TI.search(ctx_sin_menu)),
            "tiene_ce": bool(_RE_CE.search(ctx_sin_menu)),
            "tiene_pasaporte": False,
        }

        if digitos in vistos:
            _fundir(candidatos, nuevo)
            continue
        vistos.add(digitos)
        candidatos.append(nuevo)

    return candidatos


_BANDERAS = ("marca_pegada", "tiene_cc", "cc_explicita", "tiene_nit",
             "tiene_ti", "tiene_ce", "tiene_pasaporte")


def _fundir(candidatos: List[Dict], nuevo: Dict) -> None:
    """
    Suma al candidato ya visto lo que aporta una aparicion posterior.

    El mismo numero puede salir varias veces y solo una llevar el rotulo: en
    el asunto va desnudo junto al nombre y en la firma va marcado
    ("cc 41234567"). Quedarse con la primera aparicion perderia la marca.
    """
    for c in candidatos:
        if c["numero"] != nuevo["numero"]:
            continue
        gana_marca = nuevo["marca_pegada"] and not c["marca_pegada"]
        for bandera in _BANDERAS:
            c[bandera] = c[bandera] or nuevo[bandera]
        c["tipos_pegados"] = c["tipos_pegados"] | nuevo["tipos_pegados"]
        c["tipo_pegado"] = tipo_pegado_que_manda(c["tipos_pegados"])
        if gana_marca:
            c["contexto"] = nuevo["contexto"]
        return


def tipo_pegado_que_manda(tipos) -> str:
    """
    Tipo pegado que sobrevive cuando el numero sale rotulado en varios sitios.

    Un tipo de persona natural le gana al NIT: en la planilla de aportes la
    columna del aportante se rotula NIT y un independiente escribe ahi su
    propia cedula. Dos tipos de persona distintos no se pueden conciliar y
    devuelven '?'.
    """
    presentes = {t for t in tipos if t}
    if not presentes:
        return ""
    if len(presentes) == 1:
        return next(iter(presentes))
    de_persona = presentes & set(TIPOS_PEGADOS_DE_PERSONA)
    if len(de_persona) == 1:
        return next(iter(de_persona))
    return "?"
