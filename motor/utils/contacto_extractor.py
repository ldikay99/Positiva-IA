"""
Extractor determinista de TELEFONO desde el texto del correo (cuerpo + firma).
Sin IA.

El email NO se extrae aqui: el remitente real ya lo resuelve
utils.generic_filter.extraer_correo_remitente_real (maneja reenvios internos).

Telefonos colombianos:
  - Celular: 10 digitos que empiezan por 3 (300-350).
  - Fijo nuevo: 10 digitos que empiezan por 60 (numeracion unificada 2022).
  - Fijo viejo: 7 digitos.
  - Puede venir con prefijo pais +57 / 57.
"""
import re
from typing import List, Dict, Optional

# Separadores que un telefono lleva DENTRO: espacios, puntos, parentesis y el
# guion. El guion con espacio a los DOS lados no entra: ahi separa dos numeros
# de una lista ("Telefono: 3111701816 - 3171380215"), y unirlos da un numero de
# doce digitos que el plan de numeracion rechaza, con lo que se pierden los dos.
_DENTRO_DEL_TELEFONO = r"(?:[\d\s.()]|(?<! )-|-(?! ))"
# Token telefonico: digitos con separadores, opcional prefijo de pais
_TEL_TOKEN = re.compile(
    rf"(?:\+?57[\s\-.]*)?\(?\d{_DENTRO_DEL_TELEFONO}{{5,13}}\d")

# Numero de otro pais escrito con su indicativo: +58 412 1234567
_TEL_INTERNACIONAL = re.compile(r"\+(\d{1,3})[\s\-.]*(\d(?:[\d\s\-.()]{5,15})\d)")

# Palabras que confirman que un numero es telefono
_RE_TEL_KW = re.compile(
    r"\b(tel|tel[eé]fono|cel|celular|m[oó]vil|movil|contacto|whatsapp|wpp|fijo|fax|n[uú]mero)\b"
)

# Palabras que indican que el numero es OTRA cosa (no telefono).
# Las raices llevan `\w*` en vez de `\b`: "identificad\b" no casa nunca con
# "identificado", porque entre la d y la o no hay frontera de palabra.
_RE_NO_TEL_KW = re.compile(
    r"\b(?:c\.?c\.?|c[eé]dula\w*|nit|documento\w*|identificad\w*|radicad\w*"
    r"|factura\w*|siniestr\w*|expedient\w*|ent|rad|sal)\b"
)

# Rotulos que abren el bloque de datos de OTRO sujeto nombrado. El FURAT lleva
# un campo para el jefe inmediato, y una historia clinica cierra con el membrete
# del medico, donde "R.M." es su registro medico. El telefono que aparece ahi es
# de esa persona, no del afectado.
_DE_OTRO_SUJETO = re.compile(
    r"(?i)(?:datos\s+del\s+jefe\s+inmediato"
    r"|r\.?\s?m\.?\s*\d{4}"
    r"|derechos\s+reservados)")
# Cierre del bloque: el rotulo de la seccion siguiente, una fila de tabla o un
# encabezado de pagina.
_SIGUIENTE_SECCION = re.compile(
    r"(?im)^\s*(?:[IVX]+\s+[A-ZÁÉÍÓÚÑ]|##\s|\|)")
# Un bloque de datos es corto. Sin tope, el tramo se extiende hasta el final del
# documento y se lleva por delante la firma del propio afectado.
TOPE_DEL_BLOQUE_AJENO = 300


def _bloques_de_otro_sujeto(texto: str) -> List[tuple]:
    """Tramos (inicio, fin) del texto que dan los datos de un tercero."""
    tramos = []
    for m in _DE_OTRO_SUJETO.finditer(texto):
        siguiente = _SIGUIENTE_SECCION.search(texto, m.end())
        fin = min(siguiente.start() if siguiente else len(texto),
                  m.start() + TOPE_DEL_BLOQUE_AJENO)
        tramos.append((m.start(), fin))
    return tramos


def _contexto(texto: str, inicio: int, fin: int) -> str:
    """
    Ventana estrecha alrededor del numero.

    Ensancharla dejaria entrar la etiqueta de un numero vecino, que decidiria
    por este.
    """
    return " ".join(
        texto[max(0, inicio - 15): min(len(texto), fin + 8)].split()
    ).lower()


# Respuestas de la consulta al plan de numeracion. `SIN_TABLA` significa "no
# se pudo comprobar"; `INVALIDO`, "se comprobo y el numero no existe".
SIN_TABLA = "sin_tabla"
INVALIDO = "invalido"


def _consultar_plan_de_numeracion(crudo: str, region: str = "CO") -> str:
    """
    Clasifica contra los planes de numeracion reales de cada pais.

    `phonenumbers` es el port de libphonenumber: trae los prefijos y
    longitudes vigentes, incluidas las lineas 018000.

    Devuelve 'celular', 'fijo', INVALIDO o SIN_TABLA.
    """
    try:
        import phonenumbers
        from phonenumbers import NumberParseException, PhoneNumberType
    except ImportError:
        return SIN_TABLA
    try:
        n = phonenumbers.parse(crudo, region)
    except NumberParseException:
        return INVALIDO
    if not phonenumbers.is_valid_number(n):
        return INVALIDO
    tipo = phonenumbers.number_type(n)
    if tipo in (PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE):
        return "celular"
    if tipo in (PhoneNumberType.FIXED_LINE, PhoneNumberType.VOIP,
                PhoneNumberType.TOLL_FREE):
        return "fijo"
    return INVALIDO


def _clasificar_telefono(digitos: str, crudo: str = "") -> Optional[str]:
    """
    Devuelve 'celular', 'fijo' o None si el numero no existe.

    Si la tabla esta disponible, MANDA: cuando dice que el numero no existe se
    descarta, sin caer a las reglas propias. Caer ahi anularia el beneficio,
    que es justamente rechazar lo que esas reglas aceptarian de mas.

    Las reglas propias solo actuan cuando la tabla NO esta, que es el unico
    caso en que no hay contra que comprobar.
    """
    veredicto = _consultar_plan_de_numeracion(crudo or digitos)
    if veredicto == INVALIDO:
        return None
    if veredicto != SIN_TABLA:
        return veredicto

    # Sin la libreria instalada: reglas propias como ultimo recurso.
    if digitos.startswith("57") and len(digitos) in (11, 12):
        digitos = digitos[2:]
    if len(digitos) == 10 and digitos[0] == "3":
        return "celular"
    if len(digitos) == 10 and digitos.startswith("60"):
        return "fijo"
    if len(digitos) == 7:
        return "fijo"
    return None


def telefono_existe(numero: str) -> bool:
    """
    Si el plan de numeracion reconoce ese numero.

    Mismo criterio con el que `extraer_telefonos` acepta un candidato; se expone
    para que la compuerta aplique el mismo.
    """
    crudo = str(numero or "").strip()
    digitos = re.sub(r"\D", "", crudo)
    if not digitos:
        return False
    return _clasificar_telefono(digitos, crudo) is not None


def normalizar_telefono(numero: str) -> str:
    """
    El numero en la forma en que lo escribe `extraer_telefonos`.

    Un numero colombiano queda en digitos pelados, sin el indicativo 57; uno de
    otro pais conserva su indicativo detras del '+'. Es la forma que va a la
    base: el mismo telefono escrito con espacios y sin ellos son dos valores
    distintos en la misma columna.
    """
    crudo = str(numero or "").strip()
    digitos = re.sub(r"\D", "", crudo)
    if not digitos:
        return ""
    if digitos.startswith("57") and len(digitos) in (11, 12):
        return digitos[2:]
    return f"+{digitos}" if crudo.startswith("+") else digitos


def extraer_telefonos(texto: str) -> List[Dict]:
    """
    Telefonos candidatos con tipo (celular/fijo) y si estan junto a una
    palabra clave de telefono. Sin duplicados. Descarta lo que este junto a
    palabras de documento/radicado/factura.
    """
    if not texto:
        return []
    candidatos = []
    vistos = set()
    tomados = []
    # Todas las apariciones de cada numero, incluidas las que se descartan por
    # contexto: hacen falta enteras para saber si el numero SOLO sale en el
    # bloque de un tercero.
    apariciones: Dict[str, List[tuple]] = {}

    # Primero los numeros con indicativo de otro pais. Van aparte porque el
    # barrido normal los descarta por no encajar en la numeracion colombiana, y
    # perder el telefono de un afectado extranjero es perder al mas dificil de
    # contactar. Su tramo se reserva para no volver a leerlo despues.
    for m in _TEL_INTERNACIONAL.finditer(texto):
        pais = m.group(1)
        if pais == "57":
            continue
        digitos = re.sub(r"\D", "", m.group(2))
        if not (8 <= len(pais) + len(digitos) <= 15):
            continue
        # El tramo se reserva antes de validar: ya se sabe que no es un numero
        # colombiano, y dejar que el barrido normal lo relea produce un
        # candidato falso con los digitos partidos.
        tomados.append((m.start(), m.end()))
        apariciones.setdefault(f"+{pais}{digitos}", []).append(
            (m.start(), m.end()))
        # El plan de numeracion tambien manda aqui. Comprobar solo la longitud
        # dejaria pasar un numero que no existe en ningun pais, y la compuerta
        # lo recibiria como bueno.
        if _clasificar_telefono(f"{pais}{digitos}", m.group(0)) is None:
            continue
        contexto = _contexto(texto, m.start(), m.end())
        if _RE_NO_TEL_KW.search(contexto):
            continue
        norm = f"+{pais}{digitos}"
        if norm in vistos:
            continue
        vistos.add(norm)
        candidatos.append({
            "numero": norm, "tipo": "internacional",
            "cerca_kw": bool(_RE_TEL_KW.search(contexto)),
            "posicion": m.start(),
            "etiqueta": etiqueta_de_contacto(contexto),
        })

    for m in _TEL_TOKEN.finditer(texto):
        if any(m.start() < fin and ini < m.end() for ini, fin in tomados):
            continue  # ya lo tomo el barrido internacional
        raw = m.group(0)
        digitos = re.sub(r"\D", "", raw)
        norm = digitos[2:] if (digitos.startswith("57") and len(digitos) in (11, 12)) else digitos
        apariciones.setdefault(norm, []).append((m.start(), m.end()))

        tipo = _clasificar_telefono(digitos, raw)
        if tipo is None:
            continue
        if norm in vistos:
            continue

        contexto = _contexto(texto, m.start(), m.end())

        # Si el numero esta junto a "cedula/nit/documento/radicado", NO es telefono
        if _RE_NO_TEL_KW.search(contexto):
            continue

        cerca_kw = bool(_RE_TEL_KW.search(contexto))

        # Un FIJO sin palabra clave suele ser ruido (directorios en pies de
        # pagina corporativos). El contacto de un ciudadano casi siempre es
        # celular. Por eso solo aceptamos fijos con palabra clave cerca.
        if tipo == "fijo" and not cerca_kw:
            continue

        vistos.add(norm)
        candidatos.append({"numero": norm, "tipo": tipo, "cerca_kw": cerca_kw,
                           "posicion": m.start(),
                           "etiqueta": etiqueta_de_contacto(contexto)})
    return _sin_los_de_otro_sujeto(candidatos, apariciones, texto)


def _sin_los_de_otro_sujeto(candidatos, apariciones, texto) -> List[Dict]:
    """
    Quita los candidatos que solo aparecen en el bloque de datos de un tercero.

    Basta UNA aparicion fuera de esos bloques para conservar el numero: el mismo
    telefono puede estar en el membrete del medico y tambien en los datos del
    paciente, y entonces si es suyo.
    """
    bloques = _bloques_de_otro_sujeto(texto)
    if not bloques:
        return candidatos

    def solo_de_otro(numero: str) -> bool:
        posiciones = apariciones.get(numero) or []
        return bool(posiciones) and all(
            any(inicio <= p < fin for inicio, fin in bloques)
            for p, _ in posiciones
        )

    return [c for c in candidatos if not solo_de_otro(c["numero"])]


# El rotulo que acompaña al numero, cuando lo dice. Una misma persona puede
# dejar varios y el campo de SGDEA es "telefono celular": el que ella misma
# rotula como celular vale mas que el de su casa o el de su oficina.
_RE_ETIQUETA_CELULAR = re.compile(
    r"\b(?:cel|celular|m[oó]vil|movil|whatsapp|wpp)\b")
_RE_ETIQUETA_OTRO = re.compile(
    r"\b(?:casa|residencia|hogar|domicilio|oficina|conmutador|fijo|fax|"
    r"empresa|planta)\b")


def etiqueta_de_contacto(contexto: str) -> str:
    """'celular', 'otro' o '' segun lo que diga el rotulo junto al numero."""
    if _RE_ETIQUETA_CELULAR.search(contexto or ""):
        return "celular"
    if _RE_ETIQUETA_OTRO.search(contexto or ""):
        return "otro"
    return ""


def rango_de_preferencia(t: Dict) -> int:
    if t["tipo"] == "celular":
        return 0 if t["cerca_kw"] else 1
    if t["tipo"] == "internacional":
        return 2 if t["cerca_kw"] else 3
    return 4 if t["cerca_kw"] else 5


def rango_fino(t: Dict) -> tuple:
    """
    Como `rango_de_preferencia` pero desempatando por el rotulo.

    Se devuelve aparte para no cambiar el orden que ya usan otros llamadores:
    el primer elemento es exactamente el rango de siempre.
    """
    etiqueta = t.get("etiqueta", "")
    orden_etiqueta = {"celular": 0, "": 1, "otro": 2}.get(etiqueta, 1)
    return (rango_de_preferencia(t), orden_etiqueta)


def mejor_telefono(texto: str) -> str:
    """Celular con palabra clave primero; el fijo, ultimo."""
    tels = extraer_telefonos(texto)
    if not tels:
        return ""
    return sorted(tels, key=rango_de_preferencia)[0]["numero"]
