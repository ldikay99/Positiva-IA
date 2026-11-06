"""
Extraccion determinista. Lo que se resuelve aqui no paga modelo.

  - Cada extractor devuelve `Hallazgo(valor, evidencia, confianza)` o None.
  - Ante la duda devuelve None con el motivo, para que la IA lo intente.
  - Reutiliza los extractores de `utils/`, ya probados en produccion.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from models.enums import (
    CODIGO_TO_TIPO_DOCUMENTO,
    TIPO_DOCUMENTO_CODIGO,
    TipoDocumento,
    TipoPqrd,
)
from utils.contacto_extractor import (
    extraer_telefonos,
    rango_fino,
)
from utils.documento_candidatos import (
    TIPOS_PEGADOS_DE_PERSONA,
    extraer_candidatos_documento,
    tipo_pegado_que_manda,
)
from utils.generic_filter import (
    _es_de_un_buzon_interno,
    _nombre_plausible_o_vacio,
    es_la_misma_persona,
    es_nombre_persona_valido,
    extraer_nombre_remitente_real,
    is_buzon_no_humano,
    nombres_de_firma,
    palabras_de_cargo,
    parece_razon_social,
)
from utils.tipos_documento import (
    MAX_DIGITOS,
    MIN_DIGITOS,
    TIPOS_DE_ORGANIZACION,
    sin_leyendas,
    tramos_de_leyenda,
)


@dataclass
class Hallazgo:
    """Un dato extraido, con de donde salio y que tan seguro es."""

    campo: str
    valor: Any
    evidencia: str
    confianza: float
    metodo: str = ""
    # Por que este valor tiene que pasar por el agente antes de radicarse, o
    # cadena vacia. Un valor resuelto no se le muestra al agente, asi que un
    # acierto parcial —el algoritmo tenia dos candidatos y eligio uno— se
    # radica sin que nadie mas lo mire. Con este motivo el campo viaja a la
    # tarea con su valor, y el agente lo confirma o lo sustituye.
    a_confirmar: str = ""

    def __post_init__(self) -> None:
        self.confianza = max(0.0, min(1.0, float(self.confianza)))


@dataclass
class NoResuelto:
    """Campo sin resolver. El motivo alimenta el prompt y la revision manual."""

    campo: str
    motivo: str
    detalle: str = ""


# Umbral por debajo del cual no se acepta un valor determinista y se escala.
# Se declara aqui para que sea ajustable, no a ojo.
UMBRAL_CONFIANZA = 0.70


def _normaliza(texto: str) -> str:
    n = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in n if unicodedata.category(c) != "Mn").strip()


# Tabla fija de un caracter por caracter: la sustitucion conserva la longitud y
# se aplica de una pasada. Recorrer el texto caracter a caracter con
# `unicodedata` duplicaba el tiempo de la suite. Lo que no este en la tabla se
# queda igual, que para estos patrones da lo mismo.
_ACENTOS = str.maketrans(
    "áàäâãéèëêíìïîóòöôõúùüûñçÁÀÄÂÃÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇ",
    "aaaaaeeeeiiiiooooouuuuncAAAAAEEEEIIIIOOOOOUUUUNC",
)


def _sin_acentos(texto: str) -> str:
    """
    Como `_normaliza` pero conservando la longitud y las posiciones.

    Permite buscar sobre el texto sin acentos y citar el ORIGINAL en la misma
    posicion. `_normaliza` recorta los extremos, y ese recorte desplaza todas
    las posiciones: la evidencia acabaria señalando otro fragmento.
    """
    return (texto or "").translate(_ACENTOS).lower()


def _cita(texto: str, posicion: int, ancho: int = 70) -> str:
    """Fragmento alrededor de una posicion, como evidencia citable."""
    ini = max(0, posicion - ancho // 2)
    fin = min(len(texto), posicion + ancho // 2)
    return "..." + " ".join(texto[ini:fin].split()) + "..."


# ── Documento de identidad ──

# Distancia maxima entre el nombre y el documento para considerarlos del mismo
# sujeto. Mas alla, la cercania deja de ser evidencia.
MAX_DISTANCIA_NOMBRE_DOC = 220
# Cuanto mas cerca debe estar el ganador respecto del segundo para decidir.
FACTOR_VENTAJA = 2.0
# Distancia maxima para atribuirle un documento a quien escribe. Es mas
# estrecha que la del afectado: el documento del remitente aparece pegado a su
# firma, y mas lejos la cercania ya no distingue de quien es.
MAX_DISTANCIA_NOMBRE_DEL_REMITENTE = 60

# Banderas que pone `extraer_candidatos_documento` cuando el texto dice de que
# documento se trata. Se listan una sola vez: repetirlas en cada uso ya dejo
# fuera el pasaporte sin que nadie lo notara.
_BANDERAS_DE_MARCA = ("tiene_cc", "tiene_nit", "tiene_ti", "tiene_ce",
                      "tiene_pasaporte")

# Cualquier racha de digitos con la longitud de un documento, sin exigirle marca
# ni contexto. Solo sirve para elegir el motivo cuando no hay candidatos: es
# deliberadamente mas laxa que el extractor de candidatos, para no afirmar que
# no hay nada cuando hay algo que el contexto descarto.
_ALGUNA_RACHA_DE_DOCUMENTO = re.compile(
    rf"(?<!\d)\d[\d.\s\-]{{{MIN_DIGITOS - 2},{MAX_DIGITOS + 4}}}\d(?!\d)")

# El NIT de la propia Positiva aparece en el membrete, la firma corporativa o
# el pie del correo, y es el numero de la ENTIDAD, nunca el del afectado: es
# la misma razon por la que `utils.generic_filter.motivo_por_el_que_no_identifica_a_nadie`
# descarta a "positiva" como nombre de una parte del caso.
#
# No se generaliza a cualquier NIT con sufijo societario (SA, SAS, LTDA...):
# el afectado de una PQRD puede ser una persona juridica ajena a Positiva
# (la empresa deudora en una constitucion en mora, el empleador que radica en
# nombre de su cuenta), y ahi el NIT de ESA empresa es el dato correcto.
_NIT_DE_POSITIVA = re.compile(r"\bpositiva\b[^\d]{0,60}\bn\.?\s?i\.?\s?t\.?\b"
                              r"|\bn\.?\s?i\.?\s?t\.?\b[^\d]{0,60}\bpositiva\b",
                              re.IGNORECASE)


def _es_nit_de_organizacion_ajena(candidato: Dict) -> bool:
    """
    Si el candidato es el NIT de la propia Positiva nombrada junto al numero.

    Solo se aplica cuando NIT es la unica marca: si el mismo numero tambien
    lleva la marca de un documento de persona, esa es la que manda, como en
    la planilla de aportes donde un independiente escribe su cedula en la
    columna rotulada NIT.
    """
    if not candidato.get("tiene_nit"):
        return False
    if any(candidato.get(k) for k in
           ("tiene_cc", "tiene_ti", "tiene_ce", "tiene_pasaporte")):
        return False
    return bool(_NIT_DE_POSITIVA.search(candidato.get("contexto", "")))


# Dos años consecutivos escritos como rango ("2025 - 2024") sin espacio entre
# ellos quedan, para el barrido de digitos, como una sola racha de ocho
# cifras. Solo se descarta sin ninguna marca de documento: un numero real de
# ocho digitos que por coincidencia empiece por un año valido igual podria
# corresponder a alguien, y ahi la marca de contexto es lo que decide.
_DOS_ANIOS_PEGADOS = re.compile(r"^(19|20)\d{2}(19|20)\d{2}$")
DIFERENCIA_MAXIMA_ENTRE_ANIOS = 5


def _es_rango_de_anios_sin_marca(candidato: Dict) -> bool:
    """Si el numero es un rango de dos años pegados por el OCR o el markdown."""
    if any(candidato.get(k) for k in _BANDERAS_DE_MARCA):
        return False
    numero = candidato.get("numero", "")
    m = _DOS_ANIOS_PEGADOS.match(numero)
    if not m:
        return False
    primero, segundo = int(numero[:4]), int(numero[4:])
    return abs(primero - segundo) <= DIFERENCIA_MAXIMA_ENTRE_ANIOS


# El conmutador de una central telefonica (el PBX) es el numero de la
# entidad, no el de una persona, pero `utils.documento_candidatos` solo
# descarta un numero como telefono cuando el contexto trae "tel", "cel" o
# "linea directa/nacional/gratuita/de atencion". "PBX" y "conmutador" quedan
# fuera de esa lista, y un numero de switchboard de 6 a 11 digitos sin marca
# de documento pasa el barrido igual que una cedula.
_ES_CONMUTADOR = re.compile(r"\bpbx\b|\bconmutador\b", re.IGNORECASE)


def _es_conmutador_sin_marca(candidato: Dict) -> bool:
    """Si el numero es el PBX o el conmutador de una entidad, no un documento."""
    if any(candidato.get(k) for k in _BANDERAS_DE_MARCA):
        return False
    return bool(_ES_CONMUTADOR.search(candidato.get("contexto", "")))


# Cuanto pueden separarse dos palabras del nombre y seguir siendo la misma
# mencion. Cubre el segundo apellido y los restos que deja el OCR en medio.
SEPARACION_DENTRO_DEL_NOMBRE = 40


def _anclas_del_nombre(plano: str, partes: List[str]) -> List[int]:
    """
    Posiciones donde concurren al menos DOS palabras distintas del nombre.

    Una sola palabra no identifica a nadie dentro de un documento lleno de
    nombres: en una planilla de aportes, el nombre de pila de quien escribe
    aparece como segundo nombre de otra persona y la cercania apunta a la
    cedula equivocada.
    """
    por_parte: Dict[str, List[int]] = {}
    for parte in partes:
        posiciones = []
        desde = 0
        while True:
            i = plano.find(parte, desde)
            if i < 0:
                break
            posiciones.append(i)
            desde = i + 1
        if posiciones:
            por_parte[parte] = posiciones
    if len(por_parte) < 2:
        return []

    anclas = []
    for parte, posiciones in por_parte.items():
        for p in posiciones:
            acompanada = any(
                abs(q - p) <= SEPARACION_DENTRO_DEL_NOMBRE
                for otra, otras in por_parte.items() if otra != parte
                for q in otras
            )
            if acompanada:
                anclas.append(p)
    return anclas


def _mas_cercano_al_nombre(texto: str, candidatos: List[Dict], nombre: str):
    """
    Candidato mas proximo al nombre en el texto, o None si no hay uno claro.

    Solo decide cuando el ganador esta al menos al doble de cerca que el
    siguiente: si dos documentos estan a distancia parecida del nombre, elegir
    seria adivinar.
    """
    plano = _normaliza(texto)
    partes = [_normaliza(p) for p in (nombre or "").split() if len(p) > 2]
    if len(partes) < 2:
        return None

    posiciones_nombre = _anclas_del_nombre(plano, partes)
    if not posiciones_nombre:
        return None

    distancias = []
    for c in candidatos:
        numero = c.get("numero", "")
        if not numero:
            continue
        # Solo la posicion del numero. El prefijo del contexto no sirve como
        # clave: el relleno se repite y `find` lo ubica en otro lugar,
        # produciendo una distancia falsa.
        posiciones_doc = []
        desde = 0
        while True:
            i = plano.find(numero, desde)
            if i < 0:
                break
            posiciones_doc.append(i)
            desde = i + 1
        if not posiciones_doc:
            continue
        d = min(
            abs(pd - pn) for pd in posiciones_doc for pn in posiciones_nombre
        )
        distancias.append((d, c))

    if not distancias:
        return None
    distancias.sort(key=lambda par: par[0])
    mejor_d, mejor_c = distancias[0]
    if mejor_d > MAX_DISTANCIA_NOMBRE_DOC:
        return None
    if len(distancias) > 1:
        segunda = distancias[1][0]
        if segunda < mejor_d * FACTOR_VENTAJA:
            return None
    return mejor_c, mejor_d


def extraer_documento(
    texto: str,
    para: str = "afectado",
    nombre_afectado: str = "",
    adjuntos_ilegibles: Optional[Sequence[str]] = None,
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Documento de identidad, desambiguando entre varios candidatos.

    Se apoya en `extraer_candidatos_documento`, que ya clasifica cada racha de
    digitos por su contexto (cc, nit, ti, ce). La decision determinista solo se
    toma cuando hay UN candidato claro o cuando la marca de contexto lo resuelve
    sin ambiguedad; en cualquier otro caso se escala con el motivo.
    """
    campo = f"numero_documento_{para}"
    todos = extraer_candidatos_documento(texto or "")
    de_organizacion = [c for c in todos if _es_nit_de_organizacion_ajena(c)]
    de_anios = [c for c in todos if _es_rango_de_anios_sin_marca(c)]
    de_conmutador = [c for c in todos if _es_conmutador_sin_marca(c)]
    descartados = {id(c) for c in de_organizacion + de_anios + de_conmutador}
    candidatos = [c for c in todos if id(c) not in descartados]
    if not candidatos:
        if de_organizacion:
            return None, NoResuelto(
                campo, "SOLO_NIT_DE_ORGANIZACION_EN_EL_TEXTO",
                "el unico numero con forma de documento es el NIT de una "
                "organizacion nombrada junto a el ("
                + ", ".join(c["numero"] for c in de_organizacion[:3])
                + "), no el de una persona natural",
            )
        # Un adjunto ilegible da un motivo distinto que la ausencia real de
        # documento: son problemas distintos y se resuelven en sitios distintos.
        if adjuntos_ilegibles:
            return None, NoResuelto(
                campo, "DOCUMENTO_EN_ADJUNTO_ILEGIBLE",
                f"no hay documento en el texto y {len(adjuntos_ilegibles)} "
                f"adjunto(s) no se pudieron leer: "
                + ", ".join(list(adjuntos_ilegibles)[:3]),
            )
        # Dos problemas distintos con el mismo aspecto: que el texto no traiga
        # NINGUN numero, y que traiga varios y todos queden descartados por su
        # contexto. Del primero no hay nada que hallar, ni para el modelo; del
        # segundo si, y con un solo motivo no se distinguian.
        if not _ALGUNA_RACHA_DE_DOCUMENTO.search(texto or ""):
            return None, NoResuelto(
                campo, "SIN_NINGUN_NUMERO_EN_EL_TEXTO",
                f"el texto no trae ninguna racha de {MIN_DIGITOS} a "
                f"{MAX_DIGITOS} digitos: no hay documento que hallar",
            )
        return None, NoResuelto(
            campo, "SIN_CANDIDATOS_DE_DOCUMENTO",
            "hay numeros en el texto pero ninguno con forma de documento: "
            "quedaron descartados por su contexto (una direccion de correo, "
            "una URL, un radicado o el titulo de un adjunto)",
        )

    if len(candidatos) == 1:
        c = candidatos[0]
        marcado = any(c.get(k) for k in _BANDERAS_DE_MARCA)
        return Hallazgo(
            campo=campo,
            valor=c["numero"],
            evidencia=f"unico candidato en el texto, contexto: \"{c.get('contexto', '')[:60]}\"",
            confianza=0.95 if marcado else 0.80,
            metodo="candidato_unico",
        ), None

    con_marca = [c for c in candidatos
                 if any(c.get(k) for k in _BANDERAS_DE_MARCA)]
    # Un NIT no gana por ser el unico marcado si otro numero lleva pegado un
    # rotulo de persona ("Identificacion: 65.735.022"): el correo de una
    # entidad sobre una persona trae los dos, y elegir el NIT da por afectada
    # a la entidad. Decide quien lee el texto.
    nit_frente_a_persona = (
        len(con_marca) == 1 and con_marca[0].get("tiene_nit")
        and any(c is not con_marca[0] and c.get("marca_pegada")
                and c.get("tipo_pegado") != "NI" for c in candidatos))
    if len(con_marca) == 1 and not nit_frente_a_persona:
        c = con_marca[0]
        return Hallazgo(
            campo=campo,
            valor=c["numero"],
            evidencia=f"unico candidato con marca de documento entre "
                      f"{len(candidatos)}, contexto: \"{c.get('contexto', '')[:60]}\"",
            confianza=0.88,
            metodo="unica_marca_explicita",
        ), None

    # Con varios marcados gana el mas cercano al nombre del afectado, medido en
    # caracteres sobre el texto completo y no dentro del contexto del candidato.
    if nit_frente_a_persona:
        con_marca = [c for c in candidatos if c is con_marca[0] or c.get("marca_pegada")]
    if nombre_afectado and len(con_marca) > 1:
        elegido = _mas_cercano_al_nombre(texto, con_marca, nombre_afectado)
        if elegido is not None:
            c, distancia = elegido
            return Hallazgo(
                campo=campo, valor=c["numero"],
                evidencia=f"candidato mas cercano al nombre del afectado "
                          f"({distancia} caracteres), contexto: "
                          f"\"{c.get('contexto', '')[:60]}\"",
                confianza=0.84, metodo="cercania_al_nombre",
            ), None

    # Todos los marcados coinciden: da igual cual se tome.
    if (con_marca and len({c["numero"] for c in con_marca}) == 1
            and not nit_frente_a_persona):
        c = con_marca[0]
        return Hallazgo(
            campo=campo, valor=c["numero"],
            evidencia=f"el mismo numero aparece {len(con_marca)} veces con "
                      f"marca de documento",
            confianza=0.90, metodo="repetido_con_marca",
        ), None

    return None, NoResuelto(
        campo,
        "VARIOS_CANDIDATOS_AMBIGUOS",
        f"{len(candidatos)} candidatos, {len(con_marca)} con marca de documento: "
        + ", ".join(c["numero"] for c in candidatos[:6]),
    )


def _distancia_al_nombre(texto: str, numero: str, nombre: str) -> Optional[int]:
    """Caracteres entre `numero` y la aparicion mas proxima de `nombre`."""
    if not numero or not nombre:
        return None
    elegido = _mas_cercano_al_nombre(texto, [{"numero": numero}], nombre)
    return elegido[1] if elegido else None


def _mismo_nombre(uno: str, otro: str) -> bool:
    """
    Si los dos nombres son de la misma persona.

    Delega en `utils.generic_filter`, que es el mismo criterio con el que
    `pipeline.compuerta` decide que el remitente y el afectado son la misma
    persona: con dos implementaciones, una admitia la forma corta del nombre y
    la otra la leia como otra persona.
    """
    return es_la_misma_persona(uno, otro)


# Quien escribe se presenta en primera persona. El documento que sigue es el
# suyo, lo diga o no el encabezado del correo.
_PRIMERA_PERSONA = (
    r"\byo\b", r"\bmi\s+nombre\s+es\b", r"\bel\s+suscrito\b",
    r"\bla\s+suscrita\b", r"\bquien\s+suscribe\b",
)
# El verbo con que la persona se identifica, en primera persona. La forma de
# tercera ("quien se identifica con") queda fuera a proposito: en "remito el
# caso de X, quien se identifica con CC NNN" el documento es de X.
_SE_IDENTIFICA = r"identificad[oa]|me\s+identifico|me\s+identifiqu[eé]|identificandome"

# El rotulo del documento y su numero, tal como aparecen tras la presentacion.
_AUTOIDENTIFICACION = re.compile(
    r"(?:" + "|".join(_PRIMERA_PERSONA) + r")"
    r"[^.;\n]{0,90}?"
    r"(?:" + _SE_IDENTIFICA + r")\b[^.;\n]{0,60}?"
    r"(?:c\.?\s?c\.?|c[eé]dula(?:\s+de\s+(?:ciudadan[ií]a|extranjer[ií]a))?"
    r"|t\.?\s?i\.?|tarjeta\s+de\s+identidad|pasaporte|nuip)"
    r"[^\d\n]{0,20}"
    r"(\d[\d.\s]{4,14}\d)",
    re.IGNORECASE,
)


# Formulas con que se cierra un correo. Lo que va detras es la firma de quien
# escribe, y el documento que aparece ahi es el suyo.
#
# "cordial saludo" NO esta: abre el correo tantas veces como lo cierra, y como
# apertura lo que sigue es el relato del caso, no la firma.
_CIERRE = (
    r"cordialmente", r"atentamente", r"mil\s+gracias", r"muchas\s+gracias",
    r"agradezco", r"gracias\s+por\s+su\s+atenci[oó]n",
    r"quedo\s+(?:atent[oa]|pendiente|a\s+la\s+espera)", r"att\.?",
)
# Cuanto se mira detras del cierre. Justo lo que ocupa un bloque de firma.
ALCANCE_DE_LA_FIRMA = 180

# Quien firma puede estar hablando de OTRO: "radicado en nombre del señor X,
# C.C. NNN", "el reporte del trabajador X CC NNN" o "comision al funcionario X
# identificado con cc NNN". Ahi el documento es del afectado y atribuirlo a
# quien escribe lo registra con la identidad ajena.
#
# Los roles NO se listan aparte: son los mismos que presentan al afectado en
# `_ETIQUETAS_AFECTADO`, quitando las formas en primera persona. Mantener dos
# listas ya dejo fuera "trabajador", y despues "funcionario".
_PRIMERA_PERSONA_EN_ETIQUETAS = (r"yo", r"mi\s+nombre\s+es",
                                 r"nombre\s+completo",
                                 r"nombres?\s+y\s+apellidos?")
_en_nombre_de_otro = None


def _regex_en_nombre_de_otro():
    """Se compila al primer uso: `_ETIQUETAS_AFECTADO` se define mas abajo."""
    global _en_nombre_de_otro
    if _en_nombre_de_otro is None:
        roles = "|".join(
            e for e in _ETIQUETAS_AFECTADO
            if e not in _PRIMERA_PERSONA_EN_ETIQUETAS
        )
        _en_nombre_de_otro = re.compile(
            r"(?:a|en)\s+nombre\s+(?:de|del)\s*$|"
            r"\b(?:de|del|de\s+la|al|a\s+la|el|la|los|las)\s+"
            r"(?:" + roles + r")\s*[:\-]?\s*$",
            re.IGNORECASE,
        )
    return _en_nombre_de_otro
# Cuanto se mira delante del nombre para ver si habla de un tercero.
_ANTES_DEL_NOMBRE = 40


def _documentos_en_la_firma(texto: str) -> List[Tuple[str, int]]:
    """Pares (numero, posicion) hallados en un bloque de firma."""
    cierre = re.compile(r"\b(?:" + "|".join(_CIERRE) + r")\b", re.IGNORECASE)
    par = re.compile(
        rf"({_NOMBRE})\s*[,\-]?\s*"
        rf"(?i:{_CONECTOR_DOCUMENTO}{_MARCA_DOCUMENTO})"
        rf"\s*(?i:n[o°º]\.?|nro\.?)?\s*[:\-.]?\s*(\d[\d.\s]{{4,14}}\d)"
    )
    salida = []
    for c in cierre.finditer(texto or ""):
        bloque = texto[c.end(): c.end() + ALCANCE_DE_LA_FIRMA]
        for m in par.finditer(bloque):
            numero = re.sub(r"\D", "", m.group(2))
            if not numero:
                continue
            antes = " ".join(
                bloque[max(0, m.start(1) - _ANTES_DEL_NOMBRE): m.start(1)].split())
            if _regex_en_nombre_de_otro().search(antes):
                continue
            salida.append((numero, c.end() + m.start()))
    return salida


def _documento_por_firma(texto: str):
    """
    Documento del bloque de firma, o None.

    Solo decide si todas las firmas del correo apuntan al mismo numero: un
    reenvio arrastra la firma de otra persona, y elegir entre dos seria
    adivinar quien de las dos escribio.
    """
    hallados = _documentos_en_la_firma(texto)
    if not hallados:
        return None
    numeros = {n for n, _ in hallados}
    if len(numeros) > 1:
        return None
    return hallados[0]


def _documento_por_autoidentificacion(texto: str):
    """
    Documento que quien escribe declara como suyo, o None.

    Reconoce "yo, NOMBRE, identificado con cedula de ciudadania No. NNN" y sus
    variantes. No depende del encabezado del correo, asi que sirve igual cuando
    el buzon no trae un nombre usable.
    """
    m = _AUTOIDENTIFICACION.search(texto or "")
    if m is None:
        return None
    numero = re.sub(r"\D", "", m.group(1))
    return (numero, m.start()) if numero else None


def extraer_documento_del_remitente(
    texto: str,
    nombre_remitente: str,
    nombre_afectado: str = "",
    documento_afectado: str = "",
    voz_propia: str = "",
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Documento de quien escribe, cuando el texto permite atribuirlo.

    Tres caminos deterministas:

      1. El remitente y el afectado son la misma persona, porque sus nombres
         coinciden. Entonces el documento es el mismo.
      2. Quien escribe declara que el tramite es SUYO ("mi afiliacion"), que es
         la misma conclusion por otra via: que sean la misma persona y que el
         documento quede sin atribuir son dos caras del mismo hecho.
      3. Los nombres difieren y hay un documento, distinto al del afectado,
         claramente mas cerca del nombre del remitente que del nombre del
         afectado.

    Fuera de esos casos se escala: atribuir un documento a la persona
    equivocada lo registra en SGDEA a nombre de otro.
    """
    campo = "numero_documento_remitente"

    # Se intenta antes que nada porque no depende del encabezado del correo:
    # quien escribe declara su propio documento en el cuerpo.
    propio = _documento_por_autoidentificacion(texto)
    if propio is not None:
        numero, posicion = propio
        return Hallazgo(
            campo=campo, valor=numero,
            evidencia=f"quien escribe declara su documento: "
                      f"{_cita(texto, posicion, 110)}",
            confianza=0.92, metodo="autoidentificacion_del_remitente",
        ), None

    de_la_firma = _documento_por_firma(texto)
    if de_la_firma is not None:
        numero, posicion = de_la_firma
        return Hallazgo(
            campo=campo, valor=numero,
            evidencia=f"documento del bloque de firma: "
                      f"{_cita(texto, posicion, 110)}",
            confianza=0.88, metodo="documento_de_la_firma",
        ), None

    if voz_propia and documento_afectado:
        return Hallazgo(
            campo=campo, valor=documento_afectado,
            evidencia=f"quien escribe habla de su propio tramite "
                      f"(\"{voz_propia}\"): el documento es el del afectado",
            confianza=0.85, metodo="el_tramite_es_del_remitente",
        ), None

    if not nombre_remitente:
        return None, NoResuelto(
            campo, "SIN_NOMBRE_DEL_REMITENTE",
            "sin saber quien escribe no se puede atribuir ningun documento",
        )

    if _mismo_nombre(nombre_remitente, nombre_afectado):
        if not documento_afectado:
            return None, NoResuelto(
                campo, "MISMA_PERSONA_SIN_DOCUMENTO",
                "el remitente es el afectado, pero su documento tampoco se "
                "resolvio",
            )
        return Hallazgo(
            campo=campo, valor=documento_afectado,
            evidencia=f"el remitente y el afectado son '{nombre_remitente}': "
                      f"el documento es el mismo",
            confianza=0.90, metodo="remitente_es_el_afectado",
        ), None

    candidatos = [
        c for c in extraer_candidatos_documento(texto or "")
        if c.get("numero") and c.get("numero") != documento_afectado
    ]
    if not candidatos:
        return None, NoResuelto(
            campo, "SIN_DOCUMENTO_APARTE_DEL_AFECTADO",
            "el correo solo trae el documento del afectado; quien escribe no "
            "declara el suyo",
        )

    # Solo los que llevan el rotulo PEGADO al numero. Un numero suelto junto al
    # nombre de quien firma es tan probablemente una celda de una tabla como
    # una cedula, y aqui equivocarse registra a una persona con la identidad de
    # otra.
    marcados = [c for c in candidatos if c.get("marca_pegada")]
    if not marcados:
        return None, NoResuelto(
            campo, "SIN_DOCUMENTO_MARCADO_DEL_REMITENTE",
            f"hay {len(candidatos)} numero(s) ademas del documento del "
            f"afectado, pero ninguno lleva el rotulo del documento pegado",
        )

    elegido = _mas_cercano_al_nombre(texto or "", marcados, nombre_remitente)
    if elegido is None:
        return None, NoResuelto(
            campo, "REQUIERE_ATRIBUCION",
            f"hay {len(marcados)} documento(s) rotulados ademas del afectado y "
            f"ninguno esta claramente junto al nombre de quien escribe",
        )
    candidato, distancia = elegido

    if distancia > MAX_DISTANCIA_NOMBRE_DEL_REMITENTE:
        return None, NoResuelto(
            campo, "DOCUMENTO_LEJOS_DEL_REMITENTE",
            f"el documento rotulado mas proximo esta a {distancia} caracteres "
            f"del nombre de quien escribe, mas de los {MAX_DISTANCIA_NOMBRE_DEL_REMITENTE} "
            f"que hacen de la cercania una evidencia",
        )

    # El nombre del afectado tambien esta en el texto: si el documento le queda
    # igual de cerca, la cercania deja de distinguir a quien pertenece.
    d_afectado = _distancia_al_nombre(
        texto or "", candidato["numero"], nombre_afectado)
    if d_afectado is not None and d_afectado < distancia * FACTOR_VENTAJA:
        return None, NoResuelto(
            campo, "CERCANO_A_LOS_DOS_NOMBRES",
            f"el documento {candidato['numero']} esta a {distancia} caracteres "
            f"del remitente y a {d_afectado} del afectado: no se puede atribuir",
        )

    return Hallazgo(
        campo=campo, valor=candidato["numero"],
        evidencia=f"documento rotulado a {distancia} caracteres del nombre del "
                  f"remitente '{nombre_remitente}', contexto: "
                  f"\"{candidato.get('contexto', '')[:60]}\"",
        confianza=0.85, metodo="documento_rotulado_junto_al_remitente",
    ), None


# ── Tipo de documento ──

# Los codigos salen de `models.enums.TipoDocumento`. El orden va de la marca
# mas especifica a la menos: "cedula" a secas aparece en casi todo correo y
# solo decide si ninguna otra encaja.
_MARCAS_TIPO = (
    (TipoDocumento.NI, r"\bnit\b|r[eé]gimen\s+com[uú]n|persona\s+jur[ií]dica"),
    (TipoDocumento.PPT, r"\bp\.?\s?p\.?\s?t\.?\b|permiso\s+(?:por|de)\s+protecci[oó]n"),
    (TipoDocumento.PE, r"\bp\.?\s?e\.?\s?p\.?\b|permiso\s+especial\s+de\s+permanencia"),
    (TipoDocumento.PA, r"\bpasaporte\b|\bp\.?\s?a\.?\b"),
    (TipoDocumento.CD, r"carn[eé]\s+diplom[aá]tico"),
    # "extranjer" solo cuenta dentro de la frase del documento: como adjetivo
    # suelto aparece en el objeto social de cualquier empresa ("companias
    # nacionales o extranjeras") y no dice el tipo de documento de nadie.
    (TipoDocumento.CE, r"\bc\.?\s?e\.?\b|c[eé]dula\s+de\s+extranjer[ií]a"
                       r"|documento\s+de\s+extranjer[ií]a"),
    (TipoDocumento.TI, r"\bt\.?\s?i\.?\b|tarjeta\s+de\s+identidad|menor\s+de\s+edad"),
    (TipoDocumento.NP, r"registro\s+civil|\bnuip\b"),
    # "cedula" a secas no cuenta si es la de extranjeria: es la misma frase, no
    # una segunda mencion, y contarla dos veces fabricaba una ambiguedad.
    (TipoDocumento.CC, r"\bc\.?\s?c\.?\b|c[eé]dula\s+de\s+ciudadan[ií]a"
                       r"|\bc[eé]dula\b(?!\s+de\s+extranjer)"),
)

# Marca del candidato -> codigo, de la mas especifica a la menos.
_TIPO_POR_MARCA = (
    ("tiene_pasaporte", TipoDocumento.PA),
    ("tiene_nit", TipoDocumento.NI),
    ("tiene_ce", TipoDocumento.CE),
    ("tiene_ti", TipoDocumento.TI),
    ("tiene_cc", TipoDocumento.CC),
)

# Marcas que afirman el tipo sin ambiguedad. Dos de estas sobre el MISMO
# numero se excluyen entre si. Se usa `cc_explicita` y no `tiene_cc`: esta
# ultima se activa tambien con "documento" o "identificado", que acompañan a
# cualquier tipo, y con la palabra "cedula" de "cedula de extranjeria".
# El NIT queda fuera: junto a una cedula es lo habitual cuando la empresa
# escribe por un trabajador.
_MARCAS_EXCLUYENTES = (
    ("cc_explicita", TipoDocumento.CC),
    ("tiene_ce", TipoDocumento.CE),
    ("tiene_ti", TipoDocumento.TI),
    ("tiene_pasaporte", TipoDocumento.PA),
)


def _tipo_del_candidato(
    texto: str, numero: str
) -> Tuple[Optional[Tuple[int, str]], Optional[str]]:
    """
    Tipo que sugiere el propio numero ya elegido.

    Buscar la marca en todo el texto responde "que tipos se mencionan", que no
    es la pregunta: cuando el correo trae el NIT de la empresa y la CC del
    trabajador ambas marcas aparecen. La marca que importa es la que acompaña
    al numero que se eligio.

    Devuelve (resultado, conflicto). Si el mismo numero aparece con dos marcas
    de documento de persona, no hay resultado: cual de las dos vale es
    semantica, como en "antes con tarjeta de identidad, ahora con cedula".
    """
    marcas = set()
    excluyentes = set()
    pegados = set()
    for c in extraer_candidatos_documento(texto or ""):
        if c.get("numero") != numero:
            continue
        pegados |= set(c.get("tipos_pegados") or ())
        for bandera, codigo in _TIPO_POR_MARCA:
            if c.get(bandera):
                marcas.add(codigo)
        for bandera, codigo in _MARCAS_EXCLUYENTES:
            if c.get(bandera):
                excluyentes.add(int(codigo))

    # El rotulo que TOCA al numero manda sobre las banderas, que miran toda la
    # ventana y suman lo que aparezca en cualquier aparicion del numero.
    pegado = tipo_pegado_que_manda(pegados)
    if pegado == "?":
        return None, ("aparece rotulado como "
                      + ", ".join(sorted(pegados))
                      + ", y cual vale depende de lo que diga el correo")
    if pegado:
        codigo = CODIGO_TO_TIPO_DOCUMENTO.get(pegado)
        if codigo is not None:
            return (codigo, f"rotulo \"{pegado}\" pegado al numero {numero}"), None

    if not marcas:
        return None, None
    if len(excluyentes) > 1:
        nombres = ", ".join(sorted(TIPO_DOCUMENTO_CODIGO.get(c, str(c))
                                   for c in excluyentes))
        return None, (f"aparece rotulado como {nombres}, y cual vale depende "
                      f"de lo que diga el correo")
    for _, codigo in _TIPO_POR_MARCA:
        if codigo in marcas:
            return (codigo, f"marca junto al numero {numero}"), None
    return None, None


def extraer_tipo_documento(
    texto: str, numero: str = "", para: str = "afectado"
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Tipo de documento por la marca textual que lo acompaña.

    Es tabla de mapeo, no criterio: si el texto dice "NIT" el tipo es 10. Hoy
    esto lo decide el modelo, que es gasto sin necesidad.
    """
    campo = f"tipo_documento_{para}"

    if numero:
        directo, conflicto = _tipo_del_candidato(texto, numero)
        if conflicto:
            return None, NoResuelto(
                campo, "EL_MISMO_NUMERO_CON_DOS_TIPOS",
                f"el numero {numero}: {conflicto}",
            )
        if directo is not None:
            codigo, evidencia = directo
            return Hallazgo(
                campo=campo, valor=codigo, evidencia=evidencia,
                confianza=0.93, metodo="marca_del_numero_elegido",
            ), None

    # Los formularios escaneados traen impreso el menu de casillas
    # ("Tipo Doc CNV RC TI CC PEP CE PA SC"). Esas siglas no dicen nada del
    # afectado y hacen ganar al primer tipo de la lista.
    t = sin_leyendas(_normaliza(texto))
    encontrados: List[Tuple[int, str]] = []
    for codigo, patron in _MARCAS_TIPO:
        m = re.search(patron, t, re.IGNORECASE)
        if m:
            encontrados.append((codigo, m.group(0)))

    if not encontrados:
        if tramos_de_leyenda(_normaliza(texto)):
            return None, NoResuelto(
                campo, "SOLO_LEYENDA_DE_FORMULARIO",
                "las unicas siglas del texto son el menu de casillas de un "
                "formulario, no la declaracion del tipo",
            )
        return None, NoResuelto(campo, "SIN_MARCA_DE_TIPO_DOCUMENTO",
                                "el texto no dice CC, NIT, TI, CE ni pasaporte")

    # Este barrido responde "que tipos menciona el correo", no "de que tipo es
    # el documento de esta persona". Con mas de un tipo mencionado la respuesta
    # no se puede atribuir: el NIT de la empresa y la cedula del trabajador
    # conviven en casi cualquier correo de empleador.
    codigos = {int(c) for c, _ in encontrados}
    if len(codigos) > 1:
        return None, NoResuelto(
            campo, "VARIOS_TIPOS_MENCIONADOS",
            "el texto menciona "
            + ", ".join(sorted(TIPO_DOCUMENTO_CODIGO.get(c, str(c))
                               for c in codigos))
            + ": sin saber cual acompaña al documento no se puede atribuir",
        )
    codigo, marca = encontrados[0]
    return Hallazgo(
        campo=campo, valor=codigo,
        evidencia=f"marca textual \"{marca}\" en el contenido",
        confianza=0.92, metodo="marca_textual",
    ), None


# ── Telefono ──

def extraer_telefono(
    texto: str, nombre_afectado: str = ""
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Telefono de contacto.

    El campo de SGDEA es "telefono celular": un celular gana siempre a un fijo,
    aparezca donde aparezca. Entre los que empatan decide la cercania al nombre
    del afectado. El tipo elegido y el criterio quedan en la evidencia.
    """
    campo = "telefono_afectado"
    telefonos = extraer_telefonos(texto or "")
    if not telefonos:
        return None, NoResuelto(
            campo, "SIN_TELEFONO",
            "no hay numero con forma de telefono, ni colombiano ni con "
            "indicativo de otro pais",
        )
    ordenados = sorted(telefonos, key=rango_fino)
    mejor = ordenados[0]
    por_cercania = ""

    # Entre los que empatan en el ranking, gana el que esta junto al nombre del
    # afectado. Un membrete corporativo trae el conmutador de la empresa con su
    # rotulo "Telefono:", indistinguible por ranking del celular del afectado.
    if nombre_afectado:
        empatados = [t for t in ordenados
                     if rango_fino(t) == rango_fino(mejor)]
        elegido = _mas_cercano_al_nombre(
            texto or "",
            [{"numero": t["numero"], "_tel": t} for t in empatados],
            nombre_afectado,
        )
        if elegido is not None:
            candidato, distancia = elegido
            mejor = candidato["_tel"]
            por_cercania = (f", a {distancia} caracteres del nombre del "
                            f"afectado")

    numero = mejor.get("numero", "")
    if not numero:
        return None, NoResuelto(campo, "SIN_TELEFONO", "el extractor no devolvio numero")
    tipo = mejor.get("tipo", "?")
    con_palabra = bool(mejor.get("cerca_kw"))
    return Hallazgo(
        campo=campo, valor=numero,
        evidencia=f"{tipo}"
                  + (" junto a una palabra de contacto" if con_palabra
                     else " sin palabra de contacto cerca")
                  + por_cercania,
        confianza=0.90 if con_palabra else 0.75,
        metodo=f"telefono_{tipo}" + ("_junto_al_nombre" if por_cercania else ""),
    ), None


# ── Nombres ──

def extraer_nombre_remitente(
    from_name: str,
    from_address: str,
    cuerpo_hilo: str,
    sender_name: str = "",
    nombres_hilo: Optional[Sequence[Tuple[str, str]]] = None,
    cuerpo_actual: str = "",
    texto_adjuntos: str = "",
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Nombre del remitente por la cascada de `utils.generic_filter`, agregando
    evidencia y confianza segun la capa que lo resolvio.

    Los adjuntos solo se usan para precisar lo que la cascada ya eligio: quien
    firma la carta de una razon social, o el nombre entero de uno abreviado.
    """
    campo = "nombre_remitente"
    nombre = extraer_nombre_remitente_real(
        from_name, from_address, cuerpo_hilo,
        sender_name=sender_name,
        nombres_hilo=list(nombres_hilo or []),
        cuerpo_email_actual=cuerpo_actual,
    )
    if not nombre:
        return None, NoResuelto(
            campo, "SIN_NOMBRE_EN_NINGUNA_FUENTE",
            f"el 'De:' trae '{from_name[:40]}', y no hay nombre en sender, "
            f"hilo, encabezado de reenvio ni firma",
        )
    # Un buzon institucional se llama por su area ("Gestion del Talento
    # Humano", "Seguridad y Salud en el Trabajo Uniatlantico") o por su
    # dominio ("planeacion mani-casanare.gov.co"). La compuerta lo rechaza con
    # este mismo criterio, asi que aceptarlo aqui solo manda el correo a manual:
    # se deja pendiente para que el modelo lea la firma y reporte la entidad.
    motivo = motivo_por_el_que_no_es_un_nombre(nombre)
    if motivo:
        return None, NoResuelto(
            campo, "AREA_O_BUZON_EN_VEZ_DE_NOMBRE",
            f"'{nombre[:60]}' {motivo}: quien escribe se busca en la firma, "
            f"y si es una entidad se reporta la entidad",
        )
    # La confianza depende de que capa resolvio: el display name del 'De:' es
    # mas fiable que una firma interpretada del cuerpo.
    if nombre == from_name:
        origen, confianza = "display name del 'De:'", 0.95
    elif sender_name and nombre == sender_name:
        origen, confianza = "campo 'sender' de Graph", 0.90
    else:
        origen, confianza = "cuerpo del correo (encabezado de reenvio o firma)", 0.78
    evidencia = f"resuelto por {origen}"

    # Una razon social escribe por medio de una persona. Si una persona firma
    # junto a ella, quien escribe es esa persona: la organizacion es el
    # membrete o el buzon, no el remitente.
    if parece_razon_social(nombre):
        persona = (_persona_que_firma_por(nombre, cuerpo_hilo)
                   or _firma_de_la_carta(nombre, texto_adjuntos, cuerpo_hilo))
        if persona:
            evidencia += f" | '{nombre}' es la organizacion; firma '{persona}'"
            nombre, confianza = persona, 0.78

    return Hallazgo(
        campo=campo, valor=nombre,
        evidencia=evidencia,
        confianza=confianza, metodo="cascada_nombre_remitente",
    ), None


_ORGANIZACION_QUE_ESCRIBE = re.compile(r"'([^']+)' es la organizacion; firma '")


def organizacion_que_escribe(h: Optional[Hallazgo]) -> str:
    """
    La razon social por medio de la cual escribe quien firma, si la cascada
    del nombre del remitente la reconocio (ver `extraer_nombre_remitente`).
    """
    m = _ORGANIZACION_QUE_ESCRIBE.search(getattr(h, "evidencia", "") or "")
    return m.group(1).strip() if m and parece_razon_social(m.group(1)) else ""


def desplegar_iniciales(h: Optional[Hallazgo], texto: str) -> Optional[Hallazgo]:
    """
    El hallazgo de un nombre con sus iniciales desplegadas, si el texto lo trae
    entero (ver `_sin_abreviar`).

    Se aplica al final de la extraccion, no al resolver el nombre: con la
    inicial, "JORGE A. BARRERA" sigue siendo la misma persona que "JORGE AIRIO
    BARRERA" —una errata del adjunto— y las decisiones de identidad (el
    documento del remitente, si es el afectado) no deben depender de que forma
    del nombre se escribio en cada sitio.
    """
    if h is None:
        return h
    completo = _sin_abreviar(str(h.valor), texto)
    if completo == h.valor:
        return h
    return Hallazgo(
        campo=h.campo, valor=completo,
        evidencia=f"{h.evidencia} | forma completa de '{h.valor}' en el texto",
        confianza=h.confianza, metodo=h.metodo, a_confirmar=h.a_confirmar,
    )


_LINEA_DE_ENCABEZADO = re.compile(
    r"(?i)^[ \t>]*(?:de|from|para|to|cc|enviado|sent|asunto|subject|fecha|date)"
    r"[ \t]*:")
# Cuantas lineas no vacias alrededor de la razon social se miran para hallar a
# quien firma por ella. Una firma ocupa nombre, cargo y empresa.
LINEAS_DE_LA_FIRMA = 3
# Hasta donde se lee el mensaje que encabeza la razon social: su firma.
TOPE_DEL_MENSAJE = 3000


def _persona_que_firma_por(razon_social: str, cuerpo: str) -> str:
    """
    La persona que firma por una razon social, o cadena vacia.

    Dos formas: la razon social es una linea de la firma —y la persona va en
    las lineas vecinas— o es el nombre del buzon en un "De: EMPRESA <correo>",
    y la persona firma el mensaje que sigue a ese encabezado.
    """
    lineas = (cuerpo or "").splitlines()
    clave = _normaliza(razon_social)
    if not clave:
        return ""

    def persona(linea: str) -> str:
        if _LINEA_DE_ENCABEZADO.match(linea):
            return ""
        candidato = _nombre_plausible_o_vacio(linea, min_tokens=2)
        if (not candidato or parece_razon_social(candidato)
                or _es_de_un_buzon_interno(candidato, cuerpo)):
            return ""
        return candidato

    for i, linea in enumerate(lineas):
        if clave not in _normaliza(linea):
            continue
        if _LINEA_DE_ENCABEZADO.match(linea):
            # El mensaje que sigue al encabezado, hasta el siguiente "De:".
            resto = "\n".join(lineas[i + 1:])[:TOPE_DEL_MENSAJE]
            corte = re.search(r"(?im)^[ \t>]*(?:de|from)[ \t]*:", resto)
            mensaje = resto[:corte.start()] if corte else resto
            for firma in nombres_de_firma(mensaje):
                if not parece_razon_social(firma) and \
                        not _es_de_un_buzon_interno(firma, cuerpo):
                    return firma
            continue
        if len(_normaliza(linea)) > len(clave) + 40:
            continue  # la razon social va en prosa, no en una firma
        vecinas = []
        for paso in range(1, LINEAS_DE_LA_FIRMA + 1):
            for j in (i - paso, i + paso):
                if 0 <= j < len(lineas) and lineas[j].strip():
                    vecinas.append(lineas[j])
        for vecina in vecinas:
            encontrada = persona(vecina)
            if encontrada:
                return encontrada
    return ""


def _firma_de_la_carta(razon_social: str, texto_adjuntos: str,
                       cuerpo: str) -> str:
    """
    Quien firma, en un adjunto, la carta de la razon social; o cadena vacia.

    El correo de una empresa a menudo lo firma solo el area ("Seguridad y Salud
    en el Trabajo, CONSTRUCTORA X SAS") y la carta adjunta lleva el nombre de
    la persona. En un adjunto hay mas nombres que en un correo —el trabajador,
    el medico—, asi que solo vale el que va tras una despedida ("Atentamente,")
    y a pocas lineas de la razon social.
    """
    if not texto_adjuntos:
        return ""
    clave = _normaliza(razon_social)
    lineas = texto_adjuntos.splitlines()
    con_la_empresa = [i for i, l in enumerate(lineas) if clave in _normaliza(l)]
    if not con_la_empresa:
        return ""
    for nombre in nombres_de_firma(texto_adjuntos):
        if parece_razon_social(nombre) or _es_de_un_buzon_interno(nombre, cuerpo):
            continue
        suyas = [i for i, l in enumerate(lineas) if nombre in l]
        if any(abs(i - j) <= LINEAS_DE_LA_FIRMA
               for i in suyas for j in con_la_empresa):
            return nombre
    return ""


# Una inicial: una letra sola, con o sin punto.
_INICIAL = re.compile(r"^([A-Za-zÁÉÍÓÚÑáéíóúñ])\.?$")


def _sin_abreviar(nombre: str, texto: str) -> str:
    """
    El nombre con sus iniciales desplegadas, si el texto lo trae entero.

    "JORGE A. BARRERA NOSSA" en la firma y "JORGE ALIRIO BARRERA NOSSA" en el
    cuerpo son la misma persona, y el registro necesita el nombre completo. Se
    exige que coincidan TODAS las demas palabras. Si el texto trae dos
    despliegues —uno de ellos con una errata de OCR o de digitacion— gana el
    que mas se repite; empatados, no se sabe cual es el bueno y no se toca.
    """
    palabras = (nombre or "").split()
    if not any(_INICIAL.match(p) for p in palabras):
        return nombre
    partes = []
    for p in palabras:
        m = _INICIAL.match(p)
        partes.append(
            rf"{m.group(1)}[a-záéíóúñ]{{2,}}" if m else re.escape(p))
    patron = re.compile(r"\b" + r"\s+".join(partes) + r"\b", re.IGNORECASE)
    cuenta: Dict[str, int] = {}
    original: Dict[str, str] = {}
    for m in patron.finditer(texto or ""):
        forma = " ".join(m.group(0).split())
        cuenta[forma.lower()] = cuenta.get(forma.lower(), 0) + 1
        original.setdefault(forma.lower(), forma)
    if not cuenta:
        return nombre
    orden = sorted(cuenta.values(), reverse=True)
    if len(orden) > 1 and orden[0] == orden[1]:
        return nombre
    forma = original[max(cuenta, key=cuenta.get)]
    # Se conserva la caja con la que venia el nombre.
    return forma.upper() if nombre.isupper() else forma


_ETIQUETAS_AFECTADO = (
    r"trabajador(?:a)?", r"afiliad[oa]", r"paciente", r"titular",
    r"emplead[oa]", r"señor(?:a)?", r"sr\.?", r"sra\.?",
    r"asegurad[oa]", r"siniestrad[oa]", r"accidentad[oa]", r"usuari[oa]",
    r"lesionad[oa]", r"causante", r"colaborador(?:a)?", r"funcionari[oa]",
    r"nombre\s+completo", r"nombres?\s+y\s+apellidos?",
    r"mi\s+nombre\s+es", r"yo",
)

# Etiquetas que solo valen en el ASUNTO, donde son una convencion de titulo:
# "Solicitud de traslado CASO JUAN PEREZ CC 41234567" nombra detras a la
# persona del caso. En prosa la misma palabra aparece en cualquier parte —"en
# este caso solicitamos"— y lo que venga detras no es nadie.
_ETIQUETAS_SOLO_EN_EL_ASUNTO = (r"caso",)

# Conector entre el nombre y su documento: "JUAN PEREZ identificado(a) con
# C.C. 123". Cada pieza es opcional porque el correo real las combina de todas
# las formas posibles.
_CONECTOR_DOCUMENTO = (
    r"(?:identificad[oa]\s*(?:\(a\))?\s*)?"
    # "al" es lo que escribe el correo real donde caberia "el": "identificado
    # con al numero de cedula". Sin admitirlo, el nombre completo no se ve.
    r"(?:con\s+)?(?:el\s+|la\s+|al\s+)?(?:n[uú]mero\s+de\s+)?"
)
# Solo documentos de PERSONA. El NIT no entra: lo que va pegado a un NIT es la
# razon social de una empresa, y el afectado es siempre una persona.
_MARCA_DOCUMENTO = (
    r"(?:c\.?\s?c\.?|c[eé]dula(?:\s+de\s+(?:ciudadan[ií]a|extranjer[ií]a))?"
    r"|documento(?:\s+de\s+identidad)?|t\.?\s?i\.?|c\.?\s?e\.?)"
)


# Fragmento que reconoce un nombre propio de 2 a 5 palabras, admitiendo
# particulas ("de la", "del") y tanto Titulo como MAYUSCULAS.
_MAY = "A-ZÁÉÍÓÚÜÑ"
_MIN = "a-záéíóúüñ"
_PALABRA = rf"(?:[{_MAY}][{_MIN}']+|[{_MAY}]{{2,}})"
# El `\b` inicial evita engancharse al final de una palabra mas larga: sin el,
# "PRESTACIONES SOCIALES" produce el falso nombre "ES SOCIALES".
# Se admiten hasta cinco palabras aunque un nombre tenga cuatro: el conector
# que sigue suele estar capitalizado ("BONILLA CORONADO Identificado con CC") y
# si no cabe en el patron, el nombre se corta por delante. El limite real lo
# pone `_recortar_vocabulario`, ya sin el conector.
# Las palabras que no son la primera pueden venir en minuscula: el correo real
# a veces escribe en minuscula un apellido o un segundo nombre en medio de un
# nombre por lo demas capitalizado. Lo que impide que una frase en prosa pase
# por nombre no es el patron sino la comprobacion de `_candidato_afectado`,
# que exige dos palabras capitalizadas.
_PALABRA_INTERIOR = rf"(?:{_PALABRA}|[{_MIN}]{{3,}})"
# El nombre no puede empezar en un determinante: "El" va en mayuscula solo por
# encabezar la frase, y ocupando la primera posicion consume el cupo de
# palabras del patron.
_NO_DETERMINANTE = (
    r"(?!(?:[Ee]l|[Ll]a|[Ll]os|[Ll]as|[Dd]e|[Dd]el|[Uu]n|[Uu]na|[Yy])\b)"
)
_NOMBRE = (
    rf"\b{_NO_DETERMINANTE}{_PALABRA}"
    r"(?:\s+(?:de|del|la|los|las|di|da)\b)?"
    rf"(?:\s+{_PALABRA_INTERIOR}){{1,4}}"
)

# Dos nombres y dos apellidos. Las particulas no cuentan: "Maria del Carmen
# Gomez Perez" son cuatro.
MAX_PALABRAS_NOMBRE = 4

# Entidades, no personas. Se comparan como PALABRA COMPLETA: buscarlas como
# subcadena descarta nombres reales ("arl" esta dentro de "Carlos").
# Se comparan ya normalizados (sin acentos ni ñ), asi que basta la forma
# simple: "compañia" llega como "compania".
_NO_ES_AFECTADO = (
    "positiva", "arl", "eps", "ips", "sas", "ltda", "sa", "eu",
    "notificacion", "notificaciones", "seguros", "compania",
)
_FRASES_NO_AFECTADO = (
    "compania de seguros", "servicio al cliente", "atencion al ciudadano",
)

# Vocabulario administrativo: palabras que aparecen pegadas a un nombre pero
# nunca forman parte de el. El candidato se recorta en vez de rechazarse,
# porque "ROSA DIAZ identificado" si trae el nombre, solo que con cola.
# No incluye palabras que tambien son apellido colombiano ("Mora", "Cruz",
# "Rey", "Prado"): recortarlas perderia el nombre.
_VOCABULARIO_NO_NOMBRE = frozenset("""
cc ce ti nit cedula ciudadania extranjeria documento identidad identificado
identificada identificados pasaporte registro civil radicado siniestro furat
planilla periodo folio folios expediente oficio resolucion acta recibo factura
poliza contrato formato formulario anexo adjunto adjuntos soporte soportes
certificado constancia incapacidad historia clinica diagnostico diagnosticos
epicrisis
arp afp ccf sena dian ungrd siarp empresa sociedad agencia entidad
gerencia oficina coordinacion subdireccion recaudo nomina tesoreria juridica
alcaldia gobernacion concejal alcalde ministerio superintendencia hospital
laboratorio empleador aportante afiliado afiliada dependiente independiente
pensionado beneficiario contratista funcionario encargada encargado tramite
tramites asesor asesora analista auxiliar
trabajador trabajadora paciente titular usuario usuaria victima denunciante
querellante causante causahabiente senor senora senorita
profesional profesionales revisor revisora fiscal subgerente subgerencia
ejecutivo ejecutiva intramural extramural vacante cargo persona personas
siniestrado siniestrada accidentado accidentada asegurado asegurada
solidaridad pensional
que se un una unos unas al lo les nos me te mi tu su sus este esta estos estas
ese esa aquel donde cuando como cual quien porque pues sino pero
salud pension pensiones riesgos parafiscales aporte aportes voluntario novedad
novedades reporte retiro ingreso traslado correccion devolucion reintegro pago
pagos valor saldo deuda sexo masculino femenino genero edad telefono celular
correo ciudad municipio departamento pais barrio
solicita solicitan manifiesta manifiestan reporta reportan presenta presentan
informa informan requiere requieren adjunta adjuntan envia envian remite
remiten expone exponen indica indican aclara aclaran responde responden
acepta aceptan objeta objetan certifica certifican declara declaran autoriza
autorizan interpone interponen radica radican reclama reclaman denuncia
denuncian agradece agradecen comunica comunican notifica notifican
refiere refieren niega niegan consulta consultan ingresa ingresan aceptar
entender entiende entienden comprende comprenden firmar firma tolera asiste
cordial saludo saludos atentamente atenta atento quedo agradezco gracias
buenos buenas dias tardes noches reciba respuesta solicitud peticion queja
reclamo derecho tutela asunto referencia confirmacion reserva favor cuando
sin con caso tipo clase nivel centro codigo direccion aviso cobro citacion
requerimiento recurso apelacion reposicion sancion multa acuerdo convenio
informe cuenta extracto liquidacion comprobante autorizacion orden ordenes
remision remisiones medicamento medicamentos examen examenes recomendacion
recomendaciones obtener outlook subject opiniones contenidas jefatura
notificacion notificaciones objecion autoreporte accidentalidad
cita control seguimiento calificacion invalidez accidente enfermedad riesgo
laboral comision fondo representaciones erratas cel actividad economica
cotizante afiliacion reiteracion secuelas cordialmente tarjeta derivado
consulta externa lugar rehabilitacion integral en apoderado representante
legal identificacion gerente talento humano nombre nombres apellido apellidos
principal suplente
calle carrera avenida diagonal transversal apto apartamento piso kilometro
manzana torre bloque edificio local conjunto urbanizacion
fecha area rodilla derecha izquierda izquierdo ortopedia electronica
controversia junta juntas nequi daviplata ibc caja pcl pclo
""".split()) | palabras_de_cargo()
# Los cargos se heredan de `utils.generic_filter` en vez de repetirse aqui: son
# el mismo concepto —un cargo no es el nombre de nadie— y con dos listas
# separadas, "Recursos Humanos" pasaria por nombre del afectado porque esas
# dos palabras solo estarian en una de ellas.
# Sin nombres de ciudades ni departamentos: media Colombia se apellida como un
# municipio. "Pereira", "Florencia" y "Leticia" son apellido y nombres de pila
# corrientes, y listarlos mutilaba a personas reales.

# Particulas que pueden ir DENTRO de un nombre ("Muñoz de la Rosa") pero nunca
# al principio: "DE BOGOTA" no es una persona.
_PARTICULAS = frozenset(("de", "del", "la", "las", "los", "y", "e", "el"))


def _recortar_vocabulario(candidato: str) -> Tuple[str, List[str], List[str]]:
    """
    Corta el candidato en la primera palabra que no puede ser un nombre.

    Devuelve (nombre, cabeza_quitada, cola_quitada). Se separan porque no
    valen lo mismo: quitar de la cola es limpiar el desborde del regex
    ("... CASTAÑEDA CC"), mientras que quitar de la cabeza mueve donde empieza
    el nombre y, si el vocabulario coincidiera con un nombre de pila real,
    se estaria perdiendo justo esa palabra.
    """
    palabras = candidato.split()
    cabeza: List[str] = []
    # Una frase como "SEGUIMIENTO CASO" delante del nombre trae dos palabras
    # que no son parte de el. Cortar en la primera vaciaria el candidato.
    while palabras and _normaliza(palabras[0]).strip(".,;:") in (
        _PARTICULAS | _VOCABULARIO_NO_NOMBRE
    ):
        cabeza.append(palabras.pop(0))
    original = list(palabras)
    corte = len(palabras)
    for i, p in enumerate(palabras):
        if _normaliza(p).strip(".,;:") in _VOCABULARIO_NO_NOMBRE:
            corte = i
            break
    palabras = palabras[:corte]

    propias = 0
    limitadas = []
    for p in palabras:
        if _normaliza(p).strip(".,;:") not in _PARTICULAS:
            propias += 1
            if propias > MAX_PALABRAS_NOMBRE:
                break
        limitadas.append(p)
    palabras = limitadas

    # Cola: particulas sueltas ("Fe de") y restos de dos letras que deja el OCR
    # al partir una palabra ("BARRANCABERME JA").
    while palabras and (
        _normaliza(palabras[-1]).strip(".,;:") in _PARTICULAS
        or len(palabras[-1].strip(".,;:")) <= 2
    ):
        palabras.pop()
    # Por POSICION, no por pertenencia: con `in`, una palabra repetida como
    # "SANCHEZ SANCHEZ CC" sigue en la lista y el recorte se pierde.
    cola = original[len(palabras):]
    return " ".join(palabras), cabeza, cola


# Cuanto baja la confianza cuando el nombre empezo mas adelante de donde lo
# puso el regex. El recorte acierta casi siempre, pero se apoya en una lista
# mantenida a mano: si alguna vez coincidiera con un nombre de pila, el campo
# debe acercarse al umbral en vez de radicarse a ciegas.
PENALIZACION_POR_RECORTE = 0.06


# Confianza de la geografia leida del texto. Va por debajo de la del registro
# de personas: el registro es la fuente de la entidad y esto es lo que el
# ciudadano escribio, que puede ser justo la actualizacion que esta pidiendo.
CONFIANZA_DE_LA_GEOGRAFIA = 0.85


def extraer_geografia(texto: str) -> List[Hallazgo]:
    """
    Departamento y municipio de residencia, o lista vacia.

    Van juntos o no van: un municipio de un departamento emparejado con otro
    describe un lugar que no existe, y quien radica los envia como pareja.
    """
    from pipeline.geografia import buscar

    encontrado = buscar(texto or "")
    if encontrado is None:
        return []
    departamento, municipio, evidencia = encontrado
    return [
        Hallazgo(campo="departamento_afectado", valor=departamento,
                 evidencia=evidencia, confianza=CONFIANZA_DE_LA_GEOGRAFIA,
                 metodo="geografia_del_texto"),
        Hallazgo(campo="municipio_afectado", valor=municipio,
                 evidencia=evidencia, confianza=CONFIANZA_DE_LA_GEOGRAFIA,
                 metodo="geografia_del_texto"),
    ]


def _lleva_un_nombre(palabras: List[str]) -> bool:
    """
    Si esas palabras tienen la forma minima de un nombre de persona.

    Dos capitalizadas es lo que exige `_candidato_afectado` para aceptar un
    candidato, asi que lo descartado por el recorte podria haber sido el
    nombre. Cual de los dos lados lo es depende de la regla que capturo la
    frase y aqui no se sabe, pero la eleccion deja de ser evidente.
    """
    return sum(1 for p in palabras if p[:1].isupper()) >= 2

# En un certificado de aportes, "se certifica que X, identificado con CC NNN,
# realizo los siguientes aportes" nombra en X a quien PAGO. El afiliado va en
# el listado que viene detras, no ahi. Se exigen las dos partes de la frase:
# "se certifica que X sufrio un accidente" si nombra al afectado.
_ANTES_DEL_PAGADOR = re.compile(r"certifica(?:mos)?\s+que$", re.IGNORECASE)
_CLAUSULA_DE_APORTES = re.compile(
    r"realiz[oó]\s+los\s+siguientes\s+aportes", re.IGNORECASE)
# Cuanto se mira delante y detras del candidato para reconocer la clausula.
ANTES_DEL_PAGADOR = 30
ALCANCE_DE_LA_CLAUSULA_DE_APORTES = 220


def _es_el_pagador_de_los_aportes(texto: str, inicio: int) -> bool:
    """Si el candidato ocupa el lugar de quien paga en un certificado de aportes."""
    antes = " ".join(texto[max(0, inicio - ANTES_DEL_PAGADOR):inicio].split())
    if not _ANTES_DEL_PAGADOR.search(antes):
        return False
    despues = texto[inicio: inicio + ALCANCE_DE_LA_CLAUSULA_DE_APORTES]
    return bool(_CLAUSULA_DE_APORTES.search(despues))


def _candidato_afectado(texto: str, bruto: str, inicio: int,
                        metodo: str, confianza: float):
    """Valida un candidato y lo devuelve como Hallazgo, o None."""
    if _es_el_pagador_de_los_aportes(texto or "", inicio):
        return None
    candidato, cabeza, cola = _recortar_vocabulario(
        " ".join((bruto or "").split()))
    if not candidato or not es_nombre_persona_valido(candidato):
        return None
    # La etiqueta se encontro pero lo que la sigue es la frase, no la persona:
    # "trabajador QUE SE ENCONTRABA LABORANDO", "paciente VALORADO POR".
    if motivo_de_frase(candidato):
        return None
    # Las palabras en minuscula no pueden sostener el nombre por si solas: se
    # admiten en medio del nombre pero el candidato tiene que seguir teniendo
    # dos palabras capitalizadas. Sin esto, la relajacion depende de que el
    # vocabulario administrativo este completo, y una palabra que le falte se
    # cuela como nombre.
    if sum(1 for p in candidato.split() if p[:1].isupper()) < 2:
        return None
    n = _normaliza(candidato)
    if any(f in n for f in _FRASES_NO_AFECTADO):
        return None
    palabras = {p.strip(".,;:") for p in n.split()}
    if palabras & set(_NO_ES_AFECTADO):
        return None
    if len(candidato.split()) < 2:
        return None
    evidencia = _cita(texto, inicio)
    if cabeza or cola:
        evidencia += " | se descarto de la frase: " + " ".join(cabeza + cola)
    otro = _lleva_un_nombre(cola)
    if cabeza or otro:
        confianza = round(confianza - PENALIZACION_POR_RECORTE, 2)
    return Hallazgo(
        campo="nombre_afectado", valor=candidato,
        evidencia=evidencia,
        confianza=confianza, metodo=metodo,
        a_confirmar=(
            f"el recorte descarto '{' '.join(cola)}', que tambien tiene forma "
            f"de nombre: cual de los dos es la persona del caso no se decide "
            f"por la forma de la frase" if otro else ""
        ),
    )


# ── Un trozo de frase no es un nombre ──
#
# "ENCONTRABA LABORANDO REALIZANDO", "ANTES DE INICIAR LABORES", "VALORADO POR
# ORTOPEDIA", "NO APLICA": la etiqueta ("trabajador", "paciente") se encontro
# y lo que la sigue es la frase, no la persona. El vocabulario no alcanza a
# listar toda la lengua; la forma de la frase si se reconoce.

# Mas palabras que estas no es un nombre: dos nombres, dos apellidos y dos
# particulas ya son seis.
MAX_PALABRAS_DE_UN_NOMBRE = 6

# Palabras gramaticales que un nombre no lleva en ninguna posicion. Las
# particulas de apellido ("de", "del", "la", "y") no estan: "Maria del Mar de
# la Hoz y Perez" es un nombre.
_PALABRAS_DE_FRASE = frozenset("""
el los un una unos unas al en por para con sin que se lo le les su sus
antes despues durante segun desde hasta sobre entre hacia mediante tras contra
no si ni ya muy mas aun tambien cuando donde como porque pero aplica
""".split())
# Al principio tampoco: "LA ROSA" puede ser apellido en medio, pero un nombre
# que EMPIEZA por articulo es el arranque de una frase.
_ARRANQUE_DE_FRASE = _PALABRAS_DE_FRASE | frozenset("las".split())

# Gerundios e imperfectos. Hay nombres de pila con esa terminacion, y van
# aparte.
_VERBO_CONJUGADO = re.compile(
    r"^[a-z]{3,}(?:iendo|yendo|ando|aban|aba|ieron|aron|ndose)$")
_NOMBRES_CON_FORMA_DE_VERBO = frozenset("""
fernando hernando armando orlando rolando ernando arnando fernanda hernanda
armanda orlanda rolanda ferrando durando
""".split())
# Sustantivos abstractos en -cion/-sion. Algunos son nombres de pila.
_SUSTANTIVO_ABSTRACTO = re.compile(r"^[a-z]{3,}(?:cion|sion|ciones|siones)$")
_NOMBRES_EN_CION = frozenset("""
concepcion asuncion encarnacion ascension purificacion anunciacion
""".split())

_VALORES_DE_RELLENO = frozenset({
    "no aplica", "n a", "na", "ninguno", "ninguna", "sin informacion",
    "no registra", "no reporta", "desconocido", "sin dato", "sin datos",
    "no informa", "pendiente", "por definir",
})


def motivo_de_frase(nombre: str) -> str:
    """Por que el texto es un trozo de frase y no un nombre, o cadena vacia."""
    palabras = [_normaliza(p).strip(".,;:()\"'") for p in str(nombre or "").split()]
    palabras = [p for p in palabras if p]
    if not palabras:
        return ""
    if " ".join(re.findall(r"[a-z]+", " ".join(palabras))) in _VALORES_DE_RELLENO:
        return "es un valor de relleno, no un nombre"
    if len(palabras) > MAX_PALABRAS_DE_UN_NOMBRE:
        return f"tiene {len(palabras)} palabras: es una frase, no un nombre"
    if palabras[0] in _ARRANQUE_DE_FRASE:
        return f"empieza por '{palabras[0]}': es el arranque de una frase"
    gramaticales = [p for p in palabras if p in _PALABRAS_DE_FRASE]
    if gramaticales:
        return ("lleva palabras de una frase que un nombre no lleva: "
                + ", ".join(gramaticales))
    verbos = [p for p in palabras if _VERBO_CONJUGADO.match(p)
              and p not in _NOMBRES_CON_FORMA_DE_VERBO]
    if verbos:
        return "lleva verbos conjugados: " + ", ".join(verbos)
    abstractos = [p for p in palabras if _SUSTANTIVO_ABSTRACTO.match(p)
                  and p not in _NOMBRES_EN_CION]
    if abstractos:
        return "lleva sustantivos de tramite: " + ", ".join(abstractos)
    return ""


def motivo_por_el_que_no_es_un_nombre(nombre: str) -> str:
    """
    Por que ese texto no puede ser el nombre de una persona, o cadena vacia.

    Es el mismo criterio con el que el extractor descarta un candidato,
    expuesto para que la compuerta aplique el mismo.

    Una razon social salta el vocabulario administrativo, que incluye palabras
    que si aparecen en el nombre de una empresa ('sociedad', 'gerencia'). Lo que
    no salta es lo que no nombra a nadie en ningun caso: la propia Positiva y
    los cargos y areas.
    """
    from utils.generic_filter import (
        motivo_por_el_que_no_identifica_a_nadie,
        parece_razon_social,
    )

    limpio = " ".join(str(nombre or "").split())
    if not limpio:
        return ""
    ajeno_al_caso = motivo_por_el_que_no_identifica_a_nadie(limpio)
    if ajeno_al_caso:
        return ajeno_al_caso
    if parece_razon_social(limpio):
        return ""
    frase = motivo_de_frase(limpio)
    if frase:
        return frase
    palabras = limpio.split()
    if len(palabras) < 2:
        return "una sola palabra no identifica a nadie"
    sobrantes = [
        p for p in palabras
        if _normaliza(p).strip(".,;:") in _VOCABULARIO_NO_NOMBRE
    ]
    if sobrantes:
        return ("lleva palabras que nunca son parte de un nombre: "
                + ", ".join(sobrantes))
    return ""


def _nits_sin_documento_de_persona(texto: str) -> List[str]:
    """
    NIT del correo cuando no hay ningun documento de persona, o lista vacia.

    Sirve para decir POR QUE no hay nombre de afectado: una solicitud de la
    empresa sobre su propia cuenta (recuperar una clave, pedir un estado de
    cuenta) no tiene trabajador, y el motivo generico manda a buscar una
    persona que el correo no contiene.
    """
    nits, de_persona = [], False
    for c in extraer_candidatos_documento(texto):
        pegado = c.get("tipo_pegado", "")
        if pegado == "NI":
            nits.append(c["numero"])
        elif pegado in TIPOS_PEGADOS_DE_PERSONA:
            de_persona = True
    if de_persona or not nits:
        return []
    return sorted(set(nits))


def _palabras_significativas(nombre: str) -> set:
    return {p for p in _normaliza(nombre).split()
            if len(p) > 2 and p not in _PARTICULAS}


def _mejor_candidato(texto: str, intentos, sufijo: str = ""):
    """
    Primer candidato a nombre del afectado que valida, o None.

    Los patrones vienen en orden de fiabilidad.
    """
    for patron, metodo, confianza in intentos:
        for m in re.finditer(patron, texto):
            h = _candidato_afectado(
                texto, m.group(1), m.start(), metodo + sufijo, confianza)
            if h is not None:
                return h
    return None


def _por_numero_en_el_asunto(asunto: str, patrones):
    """
    Nombre del asunto que va pegado a un numero de documento, o None.

    El numero tiene que ser un candidato a documento segun
    `extraer_candidatos_documento`, que es quien sabe descartar radicados. Sin
    ese filtro, un asunto como "PQR ENT20260001306725" entrega "PQR ENT" como
    nombre del afectado: las siglas tienen forma de palabra en mayusculas y los
    trece digitos del radicado caen dentro del rango de un documento.
    """
    documentos = {c["numero"] for c in extraer_candidatos_documento(asunto)}
    if not documentos:
        return None
    for patron, metodo, confianza in patrones:
        for m in re.finditer(patron, asunto):
            nombre, numero = (m.group(1), m.group(2)) \
                if metodo == "nombre_pegado_al_numero" \
                else (m.group(2), m.group(1))
            if numero not in documentos:
                continue
            h = _candidato_afectado(
                asunto, nombre, m.start(), f"{metodo}_en_el_asunto", confianza)
            if h is not None:
                return h
    return None


# A partir de tres palabras el nombre se da por completo. Con dos suele estar
# abreviado y el correo lo trae entero en otro sitio: el nombre de pila arriba
# y el nombre completo, con apellidos, en la firma. No se completa un nombre
# que ya tiene tres: la forma mas larga que aparece en el texto suele llevar
# pegada una palabra que no es del nombre —un mes, o una ciudad que el OCR
# partio.
PALABRAS_PARA_DAR_EL_NOMBRE_POR_COMPLETO = 3


def _completar_con_el_texto(texto: str, elegido: Hallazgo, intentos) -> Hallazgo:
    """
    Sustituye el nombre por la forma completa que el mismo texto trae, si la hay.

    Solo cuenta la mencion que CONTIENE todas las palabras del nombre elegido.
    Compartir una palabra no basta: dos personas del mismo correo pueden
    compartir apellido, y quedarse con la mas larga las confundiria.
    """
    palabras = _palabras_significativas(elegido.valor)
    if len(palabras) >= PALABRAS_PARA_DAR_EL_NOMBRE_POR_COMPLETO:
        return elegido

    mejor, suyas = elegido, palabras
    for patron, metodo, confianza in intentos:
        for m in re.finditer(patron, texto):
            h = _candidato_afectado(
                texto, m.group(1), m.start(), metodo, confianza)
            if h is None:
                continue
            propias = _palabras_significativas(h.valor)
            if palabras < propias and len(propias) > len(suyas):
                mejor, suyas = h, propias
    if mejor is elegido:
        return elegido
    return Hallazgo(
        campo=elegido.campo, valor=mejor.valor,
        evidencia=f"{mejor.evidencia} | forma completa de '{elegido.valor}'",
        confianza=mejor.confianza, metodo=mejor.metodo,
        a_confirmar=mejor.a_confirmar,
    )


def _conciliar_con_el_asunto(
    del_texto: Hallazgo, del_asunto: Optional[Hallazgo]
) -> Hallazgo:
    """
    Decide entre el nombre hallado en el cuerpo y el hallado en el asunto.

    Si comparten alguna palabra son la misma persona escrita con distinto
    detalle, y gana el mas completo: el asunto suele abreviar el nombre y el
    cuerpo lo trae entero, con segundo nombre y segundo apellido.

    Si no comparten ninguna son personas distintas, y gana el asunto. El caso
    tipico es una historia clinica a dos columnas que el PDF aplana: la
    etiqueta "Paciente" queda pegada al nombre del medico y no al del paciente.
    """
    if del_asunto is None:
        return del_texto

    comunes = _palabras_significativas(del_texto.valor) & \
        _palabras_significativas(del_asunto.valor)
    if not comunes:
        return Hallazgo(
            campo=del_asunto.campo, valor=del_asunto.valor,
            evidencia=f"{del_asunto.evidencia} | el cuerpo nombra a otra "
                      f"persona ('{del_texto.valor}'): manda el asunto",
            confianza=del_asunto.confianza, metodo=del_asunto.metodo,
            a_confirmar=del_asunto.a_confirmar,
        )

    largo, corto = ((del_texto, del_asunto)
                    if len(_palabras_significativas(del_texto.valor))
                    >= len(_palabras_significativas(del_asunto.valor))
                    else (del_asunto, del_texto))
    if largo.valor == corto.valor:
        return largo
    return Hallazgo(
        campo=largo.campo, valor=largo.valor,
        evidencia=f"{largo.evidencia} | forma mas completa de "
                  f"'{corto.valor}'",
        confianza=largo.confianza, metodo=largo.metodo,
        a_confirmar=largo.a_confirmar,
    )


def _desborda_el_tope(frase: str) -> bool:
    """
    Si la frase con forma de nombre trae mas palabras de las que caben en uno.

    En un asunto en mayusculas no hay como separar el tramite del nombre: en
    "NIEGA PERMISO SINDICAL AURA VANEGAS" todo es capitalizado y el tope de
    cuatro palabras corta por delante, dejando "NIEGA PERMISO SINDICAL AURA".
    Cuando hubo que cortar, donde empieza el nombre es una adivinanza; se deja
    al modelo, que lee la frase. Lo mismo con cualquier palabra que falte en el
    vocabulario: el criterio es la forma, no una lista.
    """
    _, cabeza, _ = _recortar_vocabulario(" ".join(frase.split()))
    resto = " ".join(frase.split()[len(cabeza):])
    propias = [p for p in resto.split()
               if _normaliza(p).strip(".,;:") not in _PARTICULAS]
    return len(propias) > MAX_PALABRAS_NOMBRE


# Por debajo de esta confianza el nombre salio de una regla debil: hubo que
# recortar la frase, se leyo detras del documento o es lo unico con forma de
# nombre en el asunto. Ahi es donde el algoritmo radicaba trozos de frase
# ("No Retenido Nuevo", "IBC Caja"); se le da al agente a confirmar.
CONFIANZA_DE_UN_NOMBRE_FIRME = 0.86


def _a_confirmar_si_es_debil(h: Hallazgo) -> Hallazgo:
    if h.a_confirmar or h.confianza >= CONFIANZA_DE_UN_NOMBRE_FIRME:
        return h
    return replace(h, a_confirmar=(
        f"lo leyo una regla debil ({h.metodo}): confirma en el correo que es "
        f"el nombre completo de la persona del caso y no un trozo de frase, un "
        f"area o una entidad"))


def extraer_nombre_afectado(
    texto: str, asunto: str = ""
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Nombre del afectado, por cercania a una etiqueta o a su documento.

    Se intenta en orden de fiabilidad y se para en el primero que valide. Los
    casos que no encajan se escalan: interpretar un nombre suelto en prosa es
    donde el algoritmo falla y el modelo acierta.
    """
    campo = "nombre_afectado"
    if not texto and not asunto:
        return None, NoResuelto(campo, "TEXTO_VACIO", "no hay contenido que analizar")

    # La etiqueta se busca sin distinguir mayusculas, pero el NOMBRE no: con
    # IGNORECASE global, `[A-ZÁÉÍÓÚÜÑ]` acepta minusculas y entonces cualquier
    # frase de dos a cinco palabras pasa por nombre ("de esta informacion sera
    # asumida"). De ahi el `(?i:...)` acotado a cada etiqueta.
    # La etiqueta va delimitada por palabra completa. Sin el `\b` inicial, la
    # etiqueta "yo" encajaria dentro de un apellido que termine en esas letras
    # ("Tamayo"), y el nombre de otra persona podria leerse como nombre del
    # afectado. El cierre no puede ser `\b` porque "Sr." termina en punto: se
    # exige que no siga una letra.
    etiquetas = rf"\b(?:{'|'.join(_ETIQUETAS_AFECTADO)})(?![{_MAY}{_MIN}])"
    intentos = (
        # "del trabajador JUAN PEREZ" / "paciente: Maria Rojas"
        (rf"(?i:{etiquetas})\s*(?:\(a\))?\s*[:\-]?\s*({_NOMBRE})",
         "etiqueta_explicita", 0.88),
        # "JUAN PEREZ identificado(a) con C.C. 41234567" y sus variantes, hasta
        # la mas escueta: "JUAN PEREZ CC 41234567". Se exige el digito para no
        # aceptar una mencion suelta de la palabra "cedula".
        (rf"({_NOMBRE})\s*[,\-]?\s*(?i:{_CONECTOR_DOCUMENTO}{_MARCA_DOCUMENTO})"
         rf"\s*(?i:n[o°º]\.?|nro\.?)?\s*[:\-.]?\s*\d",
         "nombre_antes_del_documento", 0.86),
        # "CC 41234567 de JUAN PEREZ" / "CC 41234567 - Juan Perez"
        (rf"(?i:{_MARCA_DOCUMENTO})\s*\.?\s*[:\-]?\s*"
         rf"\d[\d.\s]{{4,14}}\d\s*(?i:de|del|\-|,)?\s*({_NOMBRE})",
         "nombre_despues_del_documento", 0.84),
        # "a nombre de JUAN PEREZ"
        (rf"(?i:a\s+nombre\s+de)\s+({_NOMBRE})", "a_nombre_de", 0.82),
    )

    # En el asunto el numero suele ir pegado al nombre sin rotulo ni
    # separador, del tipo "Nombre Apellido.25748191". Se admite solo ahi: el
    # asunto es corto y lo escribio quien manda el correo, mientras que en un
    # adjunto cualquier cifra quedaria junto a cualquier nombre.
    solo_asunto = (
        (rf"({_NOMBRE})\s*[.,;:\-]?\s*(\d{{{MIN_DIGITOS},{MAX_DIGITOS}}})\b",
         "nombre_pegado_al_numero", 0.80),
        (rf"(?<!\d)(\d{{{MIN_DIGITOS},{MAX_DIGITOS}}})\s*[.,;:\-]?\s*({_NOMBRE})",
         "numero_pegado_al_nombre", 0.78),
    )

    # En el asunto la lista de etiquetas es mas amplia: ver
    # `_ETIQUETAS_SOLO_EN_EL_ASUNTO`. Solo cambia la primera regla, que es la
    # que se apoya en una etiqueta.
    etiquetas_del_asunto = (
        rf"\b(?:{'|'.join(_ETIQUETAS_AFECTADO + _ETIQUETAS_SOLO_EN_EL_ASUNTO)})"
        rf"(?![{_MAY}{_MIN}])"
    )
    intentos_del_asunto = (
        (rf"(?i:{etiquetas_del_asunto})\s*(?:\(a\))?\s*[:\-]?\s*({_NOMBRE})",
         "etiqueta_explicita", 0.88),
    ) + intentos[1:]

    # El asunto lo escribe quien manda el correo y dice de quien es el caso.
    # Un adjunto, en cambio, lo redacto un tercero y puede traer a mas gente.
    del_asunto = _mejor_candidato(asunto or "", intentos_del_asunto,
                                  sufijo="_en_el_asunto")
    if del_asunto is None:
        del_asunto = _por_numero_en_el_asunto(asunto or "", solo_asunto)
    del_texto = _mejor_candidato(texto or "", intentos)

    elegido = None
    if del_texto is not None:
        elegido = _conciliar_con_el_asunto(del_texto, del_asunto)
    elif del_asunto is not None:
        elegido = del_asunto
    if elegido is not None:
        return _a_confirmar_si_es_debil(
            _completar_con_el_texto(texto or "", elegido, intentos)), None

    # El asunto suele traer el nombre solo, sin etiqueta. Se acepta con menos
    # confianza y solo si es lo unico con forma de nombre que hay. Se exige
    # ademas que no lleve particula: en un asunto, "X DE Y" es un lugar o una
    # entidad ("MUNICIPIO DE TABIO"), no una persona.
    if asunto:
        encontrados = [
            m for m in re.finditer(_NOMBRE, asunto)
            if not (set(_normaliza(m.group(0)).split()) & _PARTICULAS)
            and not _desborda_el_tope(m.group(0))
            and _candidato_afectado(asunto, m.group(0), m.start(), "asunto", 0.72)
        ]
        if len(encontrados) == 1:
            h = _candidato_afectado(
                asunto, encontrados[0].group(0), encontrados[0].start(),
                "unico_nombre_en_el_asunto", 0.72,
            )
            if h is not None:
                return _a_confirmar_si_es_debil(h), None

    solo_empresa = _nits_sin_documento_de_persona(texto or "")
    if solo_empresa:
        return None, NoResuelto(
            campo, "SOLO_IDENTIFICA_UNA_EMPRESA",
            f"el correo identifica una empresa por su NIT "
            f"({', '.join(solo_empresa[:3])}) y ningun documento de persona: "
            f"puede no haber un afectado natural en este caso",
        )

    return None, NoResuelto(
        campo, "SIN_ETIQUETA_DE_AFECTADO",
        "no hay un nombre junto a una etiqueta de afectado ni junto a un documento",
    )


# ── Sexo ──

# Codigos de SGDEA. Se leen del texto, no se deducen.
SEXO_MASCULINO = 1
SEXO_FEMENINO = 2

_SEXO_DECLARADO = re.compile(
    r"\b(?:sexo|g[eé]nero)\b\s*[:\-]?\s*"
    r"(masculino|femenino|hombre|mujer|[mf])\b",
    re.IGNORECASE,
)
_VALOR_SEXO = {
    "masculino": SEXO_MASCULINO, "hombre": SEXO_MASCULINO, "m": SEXO_MASCULINO,
    "femenino": SEXO_FEMENINO, "mujer": SEXO_FEMENINO, "f": SEXO_FEMENINO,
}

# Concordancia gramatical: el texto no rotula el sexo pero lo declara al
# escribir "mi trabajadora ANA LOPEZ" o "ANA LOPEZ, identificada con". Es una
# afirmacion de quien escribe sobre la persona, no una deduccion a partir del
# nombre de pila: esa exigiria una lista de nombres, que es imposible de
# completar y produce datos falsos sobre personas reales.
#
# Solo cuenta lo que esta JUNTO al nombre del afectado. Un correo menciona a
# varias personas, y una marca suelta pertenece a cualquiera de ellas.
VENTANA_DE_CONCORDANCIA = 60

_GENERO_FEMENINO = re.compile(
    r"\bidentificada\b|\bse[nñ]ora\b|\bsra\.?\b|\bdo[nñ]a\b|\bnacida\b"
    r"|\btrabajadora\b|\bafiliada\b|\bempleada\b|\basegurada\b"
    r"|\baccidentada\b|\bpensionada\b|\bbeneficiaria\b|\busuaria\b"
    r"|\bla\s+(?:paciente|titular|solicitante|cotizante)\b"
    # Una historia clinica declara el sexo con el adjetivo suelto, sin usar la
    # palabra "sexo": "PACIENTE FEMENINA DE 43 AÑOS".
    r"|\bfemenin[ao]\b",
    re.IGNORECASE,
)
_GENERO_MASCULINO = re.compile(
    r"\bidentificado\b|\bse[nñ]or\b|\bsr\.?\b|\bdon\b|\bnacido\b"
    r"|\btrabajador\b|\bafiliado\b|\bempleado\b|\basegurado\b"
    r"|\baccidentado\b|\bpensionado\b|\bbeneficiario\b|\busuario\b"
    r"|\bel\s+(?:paciente|titular|solicitante|cotizante)\b"
    r"|\bmasculin[ao]\b",
    re.IGNORECASE,
)

# Un formulario impreso no sabe a quien va a atender, asi que escribe las dos
# formas: "identificado(a)", "Señor(a)", "afiliado/a". Esa terminacion es de la
# plantilla y no dice nada del titular. Anula solo la aparicion que la lleva:
# "Respetado Señor:" en el mismo documento sigue contando.
_FORMA_DOBLE = re.compile(r"\s*[(/]\s*[ao]\s*\)?")

# Con la marca contraria a esta distancia Y una frontera de celda o de linea en
# medio, el tramo enumera las opciones del formulario ("| M | Masculino | | F |
# Femenino |") y ninguna de las dos habla de una persona. La frontera es lo que
# lo distingue de una frase que menciona a dos personas ("el señor y la señora
# ANA LOPEZ"), donde la marca pegada al nombre si decide.
DISTANCIA_DE_LA_MARCA_CONTRARIA = 30
_FRONTERA_DE_CELDA = re.compile(r"[|\n]")

# Encabezado Markdown: el conversor escribe ahi el nombre del archivo adjunto.
_TITULO_DE_ADJUNTO = re.compile(r"(?m)^#{1,6}[^\n]*$")


# Frases con las que el correo declara que un dato es el EQUIVOCADO y pide
# anularlo o corregirlo. La clausula llega hasta el fin de la frase o de la
# linea.
_PIDE_CORREGIR = re.compile(
    r"(?i)\b(?:anulaci[oó]n|anular|no\s+corresponde|err[oó]ne[ao]"
    r"|incorrect[ao]|registrado\s+en)\b")
_FIN_DE_CLAUSULA = re.compile(r"[.\n]")


def _sin_lo_que_se_pide_corregir(texto: str) -> str:
    """
    El texto con las clausulas de correccion en blanco, misma longitud.

    Un correo de correccion trae dos identidades: la que se cargo por error y la
    correcta, y dice cual es cual. Un valor que SOLO aparece dentro de esa
    clausula es el equivocado; si tambien aparece fuera, sobrevive.
    """
    partes = list(texto or "")
    for m in _PIDE_CORREGIR.finditer(texto or ""):
        fin = _FIN_DE_CLAUSULA.search(texto, m.end())
        for i in range(m.start(), fin.start() if fin else len(partes)):
            if partes[i] != "\n":
                partes[i] = " "
    return "".join(partes)


def _sin_titulos_de_adjunto(texto: str) -> str:
    """
    El texto con los encabezados en blanco, conservando su longitud.

    El titulo de un adjunto es el nombre del formulario ("Trabajador
    Dependiente", "Afiliacion Independiente"), no una afirmacion sobre nadie, y
    suele llevar el nombre de la persona al lado. Se sustituye por espacios en
    vez de borrarse para que las posiciones del resto no se muevan.
    """
    return _TITULO_DE_ADJUNTO.sub(lambda m: " " * len(m.group(0)), texto or "")


# Concordancia en PRIMERA persona: quien escribe se concuerda a si mismo. Solo
# vale cuando el pipeline ya concluyo que el remitente es el afectado; si no,
# describe el sexo de otra persona.
_PRIMERA_PERSONA_FEMENINO = re.compile(
    r"\bqued(?:o|amos)\s+atentas?\b|\bestoy\s+atenta\b"
    r"|\bme\s+encuentro\s+(?:afiliada|incapacitada|pensionada|calificada)\b"
    r"|\bsoy\s+(?:afiliada|trabajadora|pensionada|beneficiaria|independiente\s+afiliada)\b"
    r"|\bestoy\s+(?:dispuesta|incapacitada|afiliada|interesada)\b"
    r"|\bagradecida\b|\bla\s+suscrita\b",
    re.IGNORECASE,
)
_PRIMERA_PERSONA_MASCULINO = re.compile(
    r"\bqued(?:o|amos)\s+atentos?\b|\bestoy\s+atento\b"
    r"|\bme\s+encuentro\s+(?:afiliado|incapacitado|pensionado|calificado)\b"
    r"|\bsoy\s+(?:afiliado|trabajador|pensionado|beneficiario|independiente\s+afiliado)\b"
    r"|\bestoy\s+(?:dispuesto|incapacitado|afiliado|interesado)\b"
    r"|\bagradecido\b|\bel\s+suscrito\b",
    re.IGNORECASE,
)


def _sexo_por_primera_persona(texto: str):
    """
    Sexo que declara quien escribe al concordarse consigo mismo, o None.

    None si aparecen las dos formas: un correo que dice "quedo atento" y
    "agradecida" no permite decidir, y el nombre de pila no se usa para
    desempatar.
    """
    plano = _normaliza(texto)
    f = _PRIMERA_PERSONA_FEMENINO.search(plano)
    m = _PRIMERA_PERSONA_MASCULINO.search(plano)
    if bool(f) == bool(m):
        return None
    encontrada = f or m
    return (SEXO_FEMENINO if f else SEXO_MASCULINO), encontrada.group(0)


def _es_una_enumeracion(plano: str, una: int, otra: int) -> bool:
    """Si las dos marcas de genero son las opciones de un formulario."""
    if abs(una - otra) > DISTANCIA_DE_LA_MARCA_CONTRARIA:
        return False
    inicio, fin = min(una, otra), max(una, otra)
    return bool(_FRONTERA_DE_CELDA.search(plano, inicio, fin))


def _sexo_por_concordancia(texto: str, nombre: str):
    """
    Sexo que declara la concordancia gramatical junto al nombre, o None.

    Devuelve (valor, marca, cita) con la palabra concreta que lo decidio y el
    fragmento donde esta, tal como los escribe el correo.

    La persona se localiza con `_anclas_del_nombre`, igual que para atribuirle
    un documento: buscar el nombre completo y literal no encuentra las
    menciones en corto ("Sra. " seguido solo del nombre de pila) ni las que
    llevan un salto de linea en medio, que es como aparece en un correo real.

    Cuando aparecen marcas de los dos generos gana la que esta pegada al
    nombre, y solo si esta al menos al doble de cerca que la contraria. Es el
    mismo criterio con el que se atribuye un documento. Abstenerse ante
    cualquier desacuerdo dejaria sin leer el sexo en las historias clinicas:
    el nombre sale decenas de veces y en alguna de ellas cae dentro de la
    ventana la fila de otra persona.
    """
    plano = _sin_titulos_de_adjunto(_sin_acentos(texto))
    partes = [_sin_acentos(p) for p in (nombre or "").split() if len(p) > 2]
    if len(partes) < 2:
        return None
    anclas = _anclas_del_nombre(plano, partes)
    if not anclas:
        return None

    contrarias = {
        SEXO_FEMENINO: [m.start() for m in _GENERO_MASCULINO.finditer(plano)],
        SEXO_MASCULINO: [m.start() for m in _GENERO_FEMENINO.finditer(plano)],
    }

    # valor -> (distancia a la mencion mas proxima, palabra, posicion)
    encontradas: Dict[int, Tuple[int, str, int]] = {}
    for patron, valor in ((_GENERO_FEMENINO, SEXO_FEMENINO),
                          (_GENERO_MASCULINO, SEXO_MASCULINO)):
        for m in patron.finditer(plano):
            if _FORMA_DOBLE.match(plano, m.end()):
                continue
            if any(_es_una_enumeracion(plano, m.start(), p)
                   for p in contrarias[valor]):
                continue
            distancia = min(abs(m.start() - a) for a in anclas)
            if distancia > VENTANA_DE_CONCORDANCIA:
                continue
            previo = encontradas.get(valor)
            if previo is None or distancia < previo[0]:
                # Del original: la posicion coincide porque `_sin_acentos`
                # conserva la longitud.
                encontradas[valor] = (
                    distancia, texto[m.start():m.end()], m.start())

    if not encontradas:
        return None
    ordenadas = sorted(encontradas.items(), key=lambda kv: kv[1][0])
    valor, (distancia, marca, posicion) = ordenadas[0]
    if len(ordenadas) > 1:
        contraria = ordenadas[1][1][0]
        if contraria < max(distancia, 1) * FACTOR_VENTAJA:
            return None
    return valor, marca, _cita(texto, posicion, 110)


def extraer_sexo(
    texto: str, nombre_afectado: str = "", texto_del_remitente: str = ""
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Sexo, unicamente cuando el texto lo declara.

    Tres formas de declararlo, de mas a menos explicita: el rotulo "Sexo: F",
    la concordancia gramatical junto al nombre del afectado, y la concordancia
    en primera persona de quien escribe ("quedo atenta") cuando quien escribe
    es el afectado. Nunca se deduce del nombre de pila; si el texto no lo dice
    se escala y SGDEA recibe "no determinado".

    `texto_del_remitente` llega vacio cuando el remitente NO es el afectado: su
    concordancia describiria entonces a otra persona.
    """
    campo = "sexo_afectado"
    if not texto or not texto.strip():
        return None, NoResuelto(campo, "TEXTO_VACIO", "no hay contenido que analizar")

    valores = {
        _VALOR_SEXO[m.group(1).lower()]
        for m in _SEXO_DECLARADO.finditer(texto)
    }
    if len(valores) > 1:
        return None, NoResuelto(
            campo, "SEXO_DECLARADO_CONTRADICTORIO",
            "el texto declara mas de un sexo y no se puede atribuir al afectado",
        )
    if valores:
        valor = valores.pop()
        m = _SEXO_DECLARADO.search(texto)
        return Hallazgo(
            campo=campo, valor=valor,
            evidencia=_cita(texto, m.start()) if m else "declarado en el texto",
            confianza=0.95, metodo="sexo_declarado_en_el_texto",
        ), None

    if nombre_afectado:
        por_concordancia = _sexo_por_concordancia(texto, nombre_afectado)
        if por_concordancia is not None:
            valor, marca, cita = por_concordancia
            return Hallazgo(
                campo=campo, valor=valor,
                evidencia=f"concordancia \"{marca}\" junto al nombre: {cita}",
                confianza=0.85, metodo="concordancia_junto_al_nombre",
            ), None

    if texto_del_remitente:
        propia = _sexo_por_primera_persona(texto_del_remitente)
        if propia is not None:
            valor, marca = propia
            return Hallazgo(
                campo=campo, valor=valor,
                evidencia=f"quien escribe es el afectado y se concuerda a si "
                          f"mismo: \"{marca}\"",
                confianza=0.83, metodo="concordancia_en_primera_persona",
            ), None

    if nombre_afectado:
        return None, NoResuelto(
            campo, "SEXO_SIN_CONCORDANCIA_UTILIZABLE",
            f"el texto no rotula el sexo y junto a '{nombre_afectado}' no hay "
            f"una concordancia unica; no se deduce del nombre",
        )

    return None, NoResuelto(
        campo, "SEXO_NO_DECLARADO",
        "el texto no dice el sexo del afectado; no se deduce del nombre",
    )


# ── Tipo de remitente ──

TIPO_REMITENTE_AFECTADO = 3


# Tipos que no identifican a una persona natural. Un documento de estos
# compartido entre remitente y afectado no dice que sean la misma persona: es
# la empresa escribiendo sobre si misma, que en esta taxonomia es un tercero.
_TIPOS_DE_ORGANIZACION = TIPOS_DE_ORGANIZACION


# Quien escribe habla de SU propio tramite. Es concordancia, no interpretacion
# de la relacion: "mi afiliacion", "me consignen", "mis aportes" son
# afirmaciones de quien escribe sobre si mismo.
_TRAMITE_PROPIO = re.compile(
    r"\bmi\s+(?:afiliacion|incapacidad|pension|calificacion|caso|solicitud|"
    r"tramite|documento|cedula|cuenta|reembolso|accidente|enfermedad|"
    r"diagnostico|historia|dinero|pago|declaracion|clave|contrasena|"
    r"correo|siniestro|proceso|expediente|salud|empresa)\b"
    r"|\bme\s+(?:consignen|adeudan|reembolsen|autoricen|paguen|corrijan|"
    r"actualicen|expidan|certifiquen|informen|respondan)\b"
    r"|\bmis\s+(?:aportes|ingresos|datos|incapacidades|documentos)\b"
    r"|\bsoy\s+(?:el|la)\s+(?:afiliad|trabajador|paciente|titular|pensionad)",
    re.IGNORECASE,
)
# Quien escribe habla del tramite de OTRO. Basta una de estas para abstenerse:
# el correo mezcla las dos voces y decidir seria interpretar.
_TRAMITE_DE_OTRO = re.compile(
    r"\b(?:mi|nuestr[oa])\s+(?:trabajador(?:a)?|emplead[oa]|colaborador(?:a)?)\b"
    r"|\b(?:a|en)\s+nombre\s+(?:de|del)\b"
    # Nombrar al afectado en tercera persona es la misma evidencia con "del
    # trabajador" que con "el trabajador": el articulo tambien cuenta. Salvo
    # detras de "soy", que es justo lo contrario: "soy el afiliado".
    r"|(?<!\bsoy\s)\b(?:de|del|el|la|los|las)\s+"
    r"(?:trabajador(?:a)?|afiliad[oa]|paciente|lesionad[oa]|emplead[oa])\b"
    r"|\byo\s+le\s+ayudo\b|\bapoderad[oa]\b|\brepresento\s+a\b",
    re.IGNORECASE,
)


# Quien firma como empleador no es el trabajador afectado. Se usa solo para
# ABSTENERSE, no para concluir "tercero": un independiente puede ser su propio
# empleador, y ahi la relacion es genuinamente ambigua.
#
# "representante legal" NO sirve para esto: alguien puede firmar como
# representante legal de su propia empresa y ser la persona afectada.
_FIRMA_DE_EMPLEADOR = re.compile(
    r"\bempleador(?:a)?\b|en\s+calidad\s+de\s+empleador", re.IGNORECASE)


def firma_declara_empleador(texto: str) -> bool:
    """Si algun bloque de firma se declara empleador del afectado."""
    cierre = re.compile(r"\b(?:" + "|".join(_CIERRE) + r")\b", re.IGNORECASE)
    for c in cierre.finditer(texto or ""):
        bloque = texto[c.end(): c.end() + ALCANCE_DE_LA_FIRMA]
        if _FIRMA_DE_EMPLEADOR.search(bloque):
            return True
    return False


def voces_del_tramite(texto_del_remitente: str) -> Tuple[str, str]:
    """
    Las marcas con que el texto dice de quien es el tramite.

    Devuelve (propia, de_otro), cada una con la palabra encontrada o cadena
    vacia. Punto unico: el documento del remitente y su tipo se deciden con la
    misma señal, y con dos lecturas separadas podian discrepar sobre si quien
    escribe es el afectado.
    """
    plano = _normaliza(texto_del_remitente)
    propia = _TRAMITE_PROPIO.search(plano)
    de_otro = _TRAMITE_DE_OTRO.search(plano)
    return (propia.group(0) if propia else "",
            de_otro.group(0) if de_otro else "")


# Despedida seguida del nombre en la misma linea, aunque el texto venga
# corrido y sin salto: "...rehabilitacion.Atentamente,CARLOS PEREZ".
_DESPEDIDA_EN_LINEA = re.compile(
    r"(?i)(?:atentamente|cordialmente|respetuosamente|atte)\s*[,.:;]?\s*"
    r"([^\n,.:;]{5,70})")


def firmantes_del_mensaje(texto: str) -> List[str]:
    """
    Los nombres que firman el mensaje, en orden.

    Recibe solo el texto del mensaje que se procesa —sin el hilo citado—:
    la firma de un mensaje anterior es de otra persona.
    """
    nombres = list(nombres_de_firma(texto))
    for m in _DESPEDIDA_EN_LINEA.finditer(texto or ""):
        nombre = _nombre_plausible_o_vacio(m.group(1).strip(), min_tokens=2)
        if nombre and nombre not in nombres:
            nombres.append(nombre)
    return [n for n in nombres if not parece_razon_social(n)]


def extraer_tipo_remitente(
    documento_afectado: str = "",
    documento_remitente: str = "",
    nombre_afectado: str = "",
    nombre_remitente: str = "",
    tipo_documento_afectado: Any = None,
    firma_de_empleador: bool = False,
    texto_del_remitente: str = "",
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Tipo de remitente, solo cuando resulta de una identidad y no de una
    interpretacion.

    Se resuelve si el remitente y el afectado son la misma persona, porque
    coinciden sus documentos o sus nombres. Es el mismo criterio con el que
    `pipeline.compuerta` corrige este campo a 3, de modo que extractor y
    compuerta no pueden discrepar.

    Sin identidad de documento ni de nombre queda la voz del texto: quien habla
    de SU propio tramite es el afectado. Si el mismo texto habla tambien del
    tramite de otro, se abstiene.

    Distinguir familiar de tercero, en cambio, exige leer la relacion, y eso
    va al modelo.
    """
    campo = "tipo_remitente"
    doc_af = re.sub(r"\D", "", str(documento_afectado or ""))
    doc_rem = re.sub(r"\D", "", str(documento_remitente or ""))

    try:
        tipo = int(tipo_documento_afectado)
    except (TypeError, ValueError):
        tipo = None
    de_organizacion = tipo in _TIPOS_DE_ORGANIZACION

    if firma_de_empleador and doc_af and doc_rem and doc_af == doc_rem:
        return None, NoResuelto(
            campo, "LA_FIRMA_SE_DECLARA_EMPLEADOR",
            f"remitente y afectado comparten el documento {doc_af}, pero la "
            f"firma se declara empleador: el documento del afectado se tomo de "
            f"esa misma firma y no identifica al trabajador",
        )

    if doc_af and doc_rem and doc_af == doc_rem and not de_organizacion:
        return Hallazgo(
            campo=campo, valor=TIPO_REMITENTE_AFECTADO,
            evidencia=f"remitente y afectado llevan el mismo documento "
                      f"{doc_af}: es la misma persona",
            confianza=0.93, metodo="mismo_documento_que_el_afectado",
        ), None

    if doc_af and doc_rem and doc_af == doc_rem and de_organizacion:
        return None, NoResuelto(
            campo, "DOCUMENTO_COMPARTIDO_DE_ORGANIZACION",
            f"remitente y afectado comparten el documento {doc_af}, pero es de "
            f"una organizacion: quien escribe es una persona en su nombre y su "
            f"relacion con el afectado no queda fijada",
        )

    if _mismo_nombre(nombre_remitente, nombre_afectado):
        return Hallazgo(
            campo=campo, valor=TIPO_REMITENTE_AFECTADO,
            evidencia=f"remitente y afectado son '{nombre_afectado}'",
            confianza=0.88, metodo="mismo_nombre_que_el_afectado",
        ), None

    # Quien firma el mensaje es quien escribe, venga del buzon que venga: un
    # trabajador que escribe desde el correo de la empresa, o desde el de un
    # familiar, firma con su nombre. Si esa firma es la del afectado, es el.
    # No vale si la firma se declara empleador: ahi el "afectado" se pudo
    # tomar de esa misma firma.
    if not firma_de_empleador and not de_organizacion and nombre_afectado:
        for firmante in firmantes_del_mensaje(texto_del_remitente):
            if _mismo_nombre(firmante, nombre_afectado):
                return Hallazgo(
                    campo=campo, valor=TIPO_REMITENTE_AFECTADO,
                    evidencia=f"el mensaje lo firma '{firmante}', que es el "
                              f"afectado",
                    confianza=0.85, metodo="firma_del_afectado",
                ), None

    propia, de_otro = voces_del_tramite(texto_del_remitente)
    if propia and not de_otro:
        return Hallazgo(
            campo=campo, valor=TIPO_REMITENTE_AFECTADO,
            evidencia=f"quien escribe habla de su propio tramite: \"{propia}\"",
            confianza=0.82, metodo="habla_de_su_propio_tramite",
        ), None
    if propia:
        return None, NoResuelto(
            campo, "HABLA_DE_SU_TRAMITE_Y_DEL_DE_OTRO",
            f"el texto dice \"{propia}\" y tambien \"{de_otro}\": las dos "
            f"voces conviven y la relacion con el afectado no queda fijada",
        )

    return None, NoResuelto(
        campo, "REQUIERE_CLASIFICACION_SEMANTICA",
        "distinguir familiar de tercero exige interpretar la relacion con el "
        "afectado, y no hay identidad de documento ni de nombre que la fije",
    )


# ── Tipo de PQRD ──

# Donde empieza el hilo citado. Lo que va detras lo escribio otro, y en un
# reenvio suele ser Positiva.
_ARRANQUE_DEL_HILO = re.compile(
    r"(?im)^\s*(?:-{2,}\s*forwarded|de\s*:|from\s*:|remitente\s*:|"
    r"enviado\s+el?\s*:|el\s+\w+,?\s+\d{1,2}\s+de\s+\w+)")


def _texto_del_remitente(cuerpo: str, reenvio_interno: bool = False) -> str:
    """
    El cuerpo hasta donde arranca el hilo citado.

    En un reenvio desde un buzon de Positiva la nota de reenvio es lo que queda
    ANTES del corte, y el correo del ciudadano queda detras: ahi su texto es el
    cuerpo completo.
    """
    if reenvio_interno:
        return cuerpo or ""
    corte = _ARRANQUE_DEL_HILO.search(cuerpo or "")
    return (cuerpo or "")[: corte.start()] if corte else (cuerpo or "")


METODO_TIPO_PQRD_FIJO = "solicitud_unico_tipo_que_se_radica"


def extraer_tipo_pqrd(
    asunto: str = "", cuerpo: str = "", reenvio_interno: bool = False
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Tipo de PQRD: siempre Solicitud.

    Es el unico tipo que este canal radica. Los demas —derecho de peticion,
    queja, reclamo— fijan terminos de respuesta distintos y su clasificacion no
    se decide aqui; quien atiende el radicado la reclasifica si procede.

    Los parametros se conservan porque el campo se extrae junto a los demas.
    """
    return Hallazgo(
        campo="tipo_pqrd", valor=int(TipoPqrd.SOLICITUD),
        evidencia="es el unico tipo con el que este canal radica",
        confianza=1.0, metodo=METODO_TIPO_PQRD_FIJO,
    ), None


# Metodos que aplican un valor por ausencia de otro, no por haberlo leido. El
# hallazgo es legitimo y su evidencia lo dice, pero cuenta como cobertura y no
# distinguirlo hace pasar por dato lo que es una suposicion. Las herramientas de
# medida los separan.
METODOS_POR_DEFECTO = frozenset({METODO_TIPO_PQRD_FIJO})


# ── Tipo de solicitud ──

# La primera que case decide: las especificas van antes que las genericas.
_TIPO_SOLICITUD_DECLARADO = (
    # Las facturas de un prestador son cuentas medicas, no aportes: van antes
    # del estado de cartera, que una clinica tambien escribe.
    (28, r"\bglosas?\b|cuentas\s+m[eé]dicas"),
    (8,  r"estado\s+de\s+(?:cuenta|cartera)"),
    # La preposicion es opcional: "Devolucion aportes", "PAGO INCAPACIDAD".
    (26, r"devoluci[oó]n\s+(?:de\s+)?(?:los\s+)?aportes"),
    (26, r"\bmora\b[^.]{0,60}\baportes\b|\baportes\b[^.]{0,60}\bmora\b"),
    # La constitucion en mora es el cobro de aportes adeudados: quien responde
    # a ella discute un pago de aportes, no pide su estado de cuenta.
    (26, r"constituci[oó]n\s+en\s+mora"),
    (16, r"pago\s+(?:de\s+)?(?:la\s+)?incapacidad"
         r"|reconocimiento\s+de\s+(?:la\s+)?incapacidad"
         r"|incapacidad(?:es)?\s+(?:no\s+)?(?:pagad|reconocid)"
         # La indemnizacion por incapacidad permanente parcial se paga tras un
         # dictamen, y el dictamen no la vuelve una controversia.
         r"|indemnizaci[oó]n\s+(?:por\s+)?(?:la\s+)?(?:incapacidad|ipp\b)"
         r"|incapacidad\s+permanente\s+parcial"),
    # Preguntar si se esta activo es el estado de la afiliacion, aunque el
    # mismo correo nombre la junta que califico.
    (12, r"\bactiv[oa]\s+o\s+inactiv[oa]|\bsi\s+(?:estoy|esta|sigo)\s+"
         r"(?:activ[oa]|afiliad[oa])"),
    # Llevar al paciente a otra IPS o cambiarle el prestador. Va antes de la
    # controversia: la PCL se nombra como riesgo de la demora, no como tramite.
    (23, r"traslado\s+(?:y\s+remisi[oó]n\s+)?(?:a|de)\s+(?:una\s+|otra\s+|la\s+)?"
         r"(?:ips|instituci[oó]n|cl[ií]nica|hospital)"
         r"|remisi[oó]n\s+y\s+traslado|cambio\s+de\s+(?:proveedor|ips|prestador)"),
    (7,  r"\bdictamen\b|\bcontroversia\b|\bp[ec]l\b"
         r"|junta\s+(?:regional|nacional)\s+de\s+calificaci[oó]n"
         r"|p[eé]rdida\s+de\s+capacidad\s+laboral"),
    (15, r"reembolso"),
    (1,  r"actualizaci[oó]n\s+de\s+(?:datos|direcci[oó]n|nombre|actividad)"
         r"|corregir\s+(?:el\s+|la\s+)?(?:raz[oó]n\s+social|actividad|riesgo)"
         r"|correcci[oó]n\s+de(?:\s+la)?\s+informaci[oó]n"),
    (5,  r"autoriza(?:ci[oó]n|r)\s+(?:de\s+|del\s+|la\s+|el\s+)?"
         r"(?:procedimiento|cita|m[eé]dic|terapia|examen|resonancia|cirug[ií]a)"
         r"|autorizaci[oó]n\s+m[eé]dica"),
    (21, r"medicamento"),
    (17, r"pensi[oó]n"),
    (18, r"p[oó]liza\s+de\s+vida"),
    (12, r"retiro\s+de(?:\s+los)?\s+trabajador|novedad\s+de\s+retiro"
         r"|afiliaci[oó]n\s+de(?:l)?\s+trabajador"),
)

# La peticion va al arranque del correo. Lo que sigue es firma, aviso legal y
# cadena de reenvio, donde el vocabulario de otro tramite produce falsos
# positivos.
TOPE_PARA_LA_SOLICITUD = 1500


def extraer_tipo_solicitud(
    asunto: str, cuerpo: str,
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Resuelve solo si el texto nombra el tramite; si no, devuelve NoResuelto.

    El asunto es el titulo de la peticion: si nombra un tramite, ese decide. Si
    solo lo nombra el cuerpo, y nombra tramites de DOS codigos distintos, la
    palabra que aparece no dice cual se pide y cual se menciona de paso ("no soy
    pensionado", "la incapacidad ya termino, pido cita"). El de mas prioridad se
    conserva, pero el agente lo confirma leyendo la peticion.
    """
    campo = "tipo_solicitud"
    asunto = asunto or ""
    inicio = (cuerpo or "")[:TOPE_PARA_LA_SOLICITUD]

    def nombrados(texto: str) -> List[Tuple[int, "re.Match"]]:
        plano = _sin_acentos(texto)
        vistos: List[Tuple[int, re.Match]] = []
        for codigo, patron in _TIPO_SOLICITUD_DECLARADO:
            m = re.search(patron, plano, re.IGNORECASE)
            if m and all(c != codigo for c, _ in vistos):
                vistos.append((codigo, m))
        return vistos

    for texto, del_asunto in ((asunto, True), (inicio, False)):
        vistos = nombrados(texto)
        if not vistos:
            continue
        codigo, m = vistos[0]
        otros = [c for c, _ in vistos[1:]]
        a_confirmar = "" if del_asunto or not otros else (
            f"el cuerpo nombra tramites de varios codigos "
            f"({', '.join(str(c) for c in [codigo] + otros)}): confirmar cual "
            f"es el que se PIDE y cual solo se menciona")
        return Hallazgo(
            campo=campo, valor=int(codigo),
            evidencia=f"el {'asunto' if del_asunto else 'texto'} nombra el "
                      f"tramite: \"{_cita(texto, m.start())}\"",
            confianza=0.85, metodo="tramite_nombrado_en_el_texto",
            a_confirmar=a_confirmar,
        ), None

    return None, NoResuelto(
        campo, "REQUIERE_CLASIFICACION_SEMANTICA",
        "el texto no nombra ningun tramite del catalogo: elegir entre 27 "
        "categorias exige interpretar la intencion del correo",
    )


# ── Direccion del afectado ──

_ROTULO_DIRECCION = re.compile(
    r"(?i)direcci[oó]n[ \t]*"
    r"(?:de\s+residencia(?:\s+habitual)?|de\s+notificaci[oó]n)?[ \t]*:[ \t|]*")

# Rotulos que abren el bloque de otro sujeto: la IPS, el empleador, Positiva,
# la Junta o el registro societario. Una direccion bajo cualquiera de ellos no
# es el domicilio del trabajador.
_BLOQUE_DE_OTRO = re.compile(
    r"(?i)codigo\s+de\s+habilitacion|r\.?\s?m\.?\s*\d{4}"
    r"|datos\s+del\s+(?:empleador|jefe|aportante)"
    r"|\bnit\b\s*:?\s*\d|numero\s+de\s+identificacion\s+tributaria"
    r"|positiva\s+compa[nñ]ia|junta\s+(?:regional|nacional)|razon\s+social"
    r"|empresa\s+matriz|controlante|\bciiu\b")

# Un domicilio no lleva numero de consultorio.
_NO_ES_DOMICILIO = re.compile(r"(?i)\bconsultorio\b")

# Donde termina el valor cuando los campos van seguidos en la misma linea, sin
# barra ni salto que los separe.
#
# El rotulo con dos puntos corta tambien cuando va PEGADO al valor: un
# formulario HTML con un campo por <li> se convierte en "CALLE 21 N 14-19E-mail:
# ...", sin espacio entre la direccion y el rotulo siguiente. Pegado se acepta
# tras una cifra o un signo, o con el salto de minuscula a mayuscula
# ("Sur 4-10 apto 2Barrio:"); dentro de una palabra no ("CARLOS:"). Un
# separador de lista (" * ", "•", ";") delante tambien lo abre.
_ROTULOS_DE_FORMULARIO = (
    r"correo(?:\s+electr[oó]nico)?|e-?mail|tel[eé]fonos?|tel|celular|cel"
    r"|m[oó]vil|fax|whatsapp|ciudad|municipio|departamento|pa[ií]s|barrio"
    r"|nacionalidad|ocupaci[oó]n|eps|arl|zona|estrato|documento|c[eé]dula"
    r"|identificaci[oó]n|tipo\s+de\s+documento|nombres?|apellidos?|sexo|g[eé]nero"
    r"|edad|fecha|comentarios?|observaci[oó]n(?:es)?")
_SIGUIENTE_ROTULO = re.compile(
    r"(?:\s*[*•·;]\s*|\s+|(?<=[\d.,#)\-])|(?<=[a-zñáéíóú])(?=[A-ZÑÁÉÍÓÚ]))"
    rf"(?i:{_ROTULOS_DE_FORMULARIO})\b(?:[ \t]+\w+)?[ \t]*:"
    # El rotulo del dato siguiente no siempre lleva dos puntos: en una linea
    # corrida se escribe "Cel 3105551234" o "Tel. 6013334444". Sin este corte
    # la direccion se traga el telefono y llega asi al registro.
    r"|\s+(?i:cel(?:ular)?|tel(?:[eé]fono)?|m[oó]vil|whatsapp|contacto|fax)"
    r"\.?\s*[:#]?\s*(?=\d)"
    # Un correo electronico tampoco es parte de la direccion.
    r"|\s+(?=[\w.+-]+@[\w-]+\.[\w.]{2,})")


def cortar_en_el_rotulo_siguiente(valor: str) -> str:
    """
    El valor de un campo de formulario sin el rotulo del campo siguiente ni lo
    que venga detras: "CALLE 21 N 14-19E-mail: x@y.co" -> "CALLE 21 N 14-19".

    Lo usa la extraccion al leer la direccion y la compuerta para limpiar la
    que llegue de cualquier origen.
    """
    texto = str(valor or "")
    corte = _SIGUIENTE_ROTULO.search(texto)
    if corte:
        texto = texto[:corte.start()]
    return texto.strip(" \t*•·;,-")

# Cuanto se mira hacia atras para encontrar al titular del bloque, y cuanto
# para descartar el rotulo de otro sujeto.
VENTANA_DEL_TITULAR = 320
TOPE_DEL_BLOQUE_AJENO_DIRECCION = 260

LARGO_MINIMO_DIRECCION = 5


def _en_la_fila_del_afectado(plano: str, posicion: int, ancla_nombre: str,
                             ancla_doc: str) -> bool:
    """
    Si el rotulo cae en una fila de tabla que empieza por el afectado.

    Una planilla de afiliacion es una tabla: la cabecera de columnas trae
    "NIT EMPLEADOR" o "RAZON SOCIAL", y debajo cada fila es el registro de UNA
    persona. La direccion que va en la fila cuyo documento es el del afectado
    es suya, aunque la cabecera nombre al empleador justo encima. Se exige que
    sea una fila ("|"), que el documento o el nombre del afectado vaya en ella
    antes del rotulo, y que entre ambos no se nombre a otro sujeto.
    """
    inicio = plano.rfind("\n", 0, posicion) + 1
    fila = plano[inicio:posicion]
    if not fila.lstrip().startswith("|"):
        return False
    celdas = [c.strip() for c in fila.split("|")]
    pos = -1
    if ancla_doc and ancla_doc in celdas:
        pos = fila.find(ancla_doc)
    elif ancla_nombre and ancla_nombre in fila:
        pos = fila.find(ancla_nombre)
    if pos < 0:
        return False
    return not _BLOQUE_DE_OTRO.search(fila[pos:])


def extraer_direccion(
    texto: str, nombre_afectado: str = "", documento_afectado: str = "",
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Domicilio del afectado, solo si el rotulo cae dentro de su bloque.

    El documento suele traer varias direcciones y la mas visible es de otro:
    el membrete de la IPS, el empleador o el destinatario. Se exige que el
    nombre o el documento del afectado aparezcan justo antes del rotulo.
    """
    campo = "direccion_afectado"
    plano = _sin_acentos(texto or "")
    ancla_nombre = _sin_acentos(nombre_afectado or "").strip()
    ancla_doc = _sin_acentos(str(documento_afectado or "")).strip()

    if not ancla_nombre and not ancla_doc:
        return None, NoResuelto(
            campo, "SIN_TITULAR_QUE_ANCLAR",
            "no se conoce el nombre ni el documento del afectado, y sin eso "
            "no se puede saber de quien es cada direccion del documento",
        )

    vistas = 0
    for m in _ROTULO_DIRECCION.finditer(plano):
        valor = re.split(r"[|\n]", texto[m.end():], maxsplit=1)[0].strip()
        valor = cortar_en_el_rotulo_siguiente(valor)
        if len(valor) < LARGO_MINIMO_DIRECCION or _NO_ES_DOMICILIO.search(valor):
            continue
        vistas += 1
        cabecera = plano[max(0, m.start() - TOPE_DEL_BLOQUE_AJENO_DIRECCION):m.start()]
        antes = plano[max(0, m.start() - VENTANA_DEL_TITULAR):m.start()]
        if not _en_la_fila_del_afectado(plano, m.start(), ancla_nombre, ancla_doc):
            if _BLOQUE_DE_OTRO.search(cabecera):
                continue
            titular = (ancla_nombre and ancla_nombre in antes) or (
                ancla_doc and ancla_doc in antes)
            if not titular:
                continue
        return Hallazgo(
            campo=campo, valor=valor,
            evidencia=f"bajo un rotulo de direccion en el bloque del afectado: "
                      f"\"{_cita(texto, m.start())}\"",
            confianza=0.85, metodo="direccion_en_el_bloque_del_afectado",
        ), None

    if vistas:
        return None, NoResuelto(
            campo, "DIRECCION_DE_OTRO_SUJETO",
            f"hay {vistas} direccion(es) en el documento pero ninguna cae en el "
            f"bloque del afectado: son del prestador, del empleador o del destinatario",
        )
    return None, NoResuelto(
        campo, "SIN_DIRECCION_EN_EL_TEXTO",
        "ningun documento trae un rotulo de direccion",
    )


# ── Oficina: tabla de mapeo pura ──

def extraer_oficina(
    tipo_solicitud: Optional[int],
) -> Tuple[Optional[Hallazgo], Optional[NoResuelto]]:
    """
    Oficina destino a partir del tipo de solicitud.

    Es una tabla de 27 filas. No hay nada que interpretar: si se conoce el tipo
    de solicitud, la oficina esta determinada. Hoy se resuelve consultando una
    API y, si falla, con el mapeo local.
    """
    campo = "oficina"
    if tipo_solicitud is None:
        return None, NoResuelto(campo, "SIN_TIPO_SOLICITUD",
                                "la oficina depende del tipo de solicitud, que no se conoce")
    try:
        from business_rules import get_office_id
    except ImportError:
        return None, NoResuelto(campo, "TABLA_NO_DISPONIBLE",
                                "no se pudo cargar el mapeo de oficinas")
    oficina = get_office_id(tipo_solicitud)
    if not oficina:
        return None, NoResuelto(
            campo, "TIPO_SOLICITUD_SIN_OFICINA",
            f"el tipo de solicitud {tipo_solicitud} no esta en la tabla de mapeo",
        )
    return Hallazgo(
        campo=campo, valor=oficina,
        evidencia=f"tabla pqrd_config_tipo_solicitud_oficina_ia: "
                  f"tipo {tipo_solicitud} -> oficina {oficina}",
        confianza=1.0, metodo="tabla_de_mapeo",
    ), None


# ── Orquestacion de la fase determinista ──

# Motivos que constatan una AUSENCIA, no una duda: el algoritmo barrio todo el
# texto buscando una forma y no habia ninguna. Preguntarselo al modelo gasta una
# tarea para que conteste lo mismo, porque lee el mismo texto.
#
# SIN_NINGUN_NUMERO_EN_EL_TEXTO entra porque no hay ni una racha de digitos con
# longitud de documento. Su hermano SIN_CANDIDATOS_DE_DOCUMENTO NO entra: ahi
# si hay numeros, descartados por su contexto, y el modelo puede juzgarlos de
# otra forma.
MOTIVOS_DE_AUSENCIA_CONSTATADA = frozenset({
    "SIN_TELEFONO", "SIN_NINGUN_NUMERO_EN_EL_TEXTO",
})


@dataclass
class Extraccion:
    """Todo lo que el algoritmo resolvio y todo lo que no."""

    hallazgos: Dict[str, Hallazgo]
    no_resueltos: Dict[str, NoResuelto]

    @property
    def campos_resueltos(self) -> List[str]:
        return sorted(self.hallazgos)

    @property
    def campos_pendientes(self) -> List[str]:
        return sorted(self.no_resueltos)

    def valor(self, campo: str, defecto: Any = None) -> Any:
        h = self.hallazgos.get(campo)
        return h.valor if h else defecto

    # Contexto que se adjunta a la tarea. El asunto y el arranque del cuerpo
    # llevan la intencion del correo, que es de donde salen tipo_solicitud y
    # tipo_remitente: buscarlos con herramientas es dar vueltas.
    contexto: str = ""

    @property
    def campos_a_buscar(self) -> List[str]:
        """
        Pendientes que el modelo si puede resolver.

        Deja fuera los que constatan una ausencia: preguntar por un telefono
        que el texto no tiene gasta una tarea para recibir la misma respuesta.
        """
        return [c for c in self.campos_pendientes
                if self.no_resueltos[c].motivo
                not in MOTIVOS_DE_AUSENCIA_CONSTATADA]

    @property
    def campos_a_confirmar(self) -> List[str]:
        """Resueltos que el agente tiene que confirmar o sustituir."""
        return sorted(c for c, h in self.hallazgos.items() if h.a_confirmar)

    @property
    def campos_para_el_agente(self) -> List[str]:
        """Todo lo que se le pide: lo pendiente y lo que hay que confirmar."""
        return sorted(set(self.campos_a_buscar) | set(self.campos_a_confirmar))

    @property
    def campos_sin_nada_que_buscar(self) -> List[str]:
        """Pendientes cuyo motivo es que el dato no esta en el texto."""
        return [c for c in self.campos_pendientes
                if self.no_resueltos[c].motivo
                in MOTIVOS_DE_AUSENCIA_CONSTATADA]

    def resumen_para_ia(self) -> str:
        """Solo lo pendiente, con el motivo. No se le manda el correo completo."""
        a_confirmar = self.campos_a_confirmar
        if not self.no_resueltos and not a_confirmar:
            return "Todos los campos se resolvieron sin IA."
        lineas = []
        if self.contexto:
            lineas += ["ASUNTO Y ENCABEZADO DEL CORREO:", self.contexto, ""]
        a_buscar = self.campos_a_buscar
        if a_buscar:
            lineas.append("Campos que el algoritmo no pudo resolver:")
            for campo in a_buscar:
                nr = self.no_resueltos[campo]
                lineas.append(f"  - {campo}: [{nr.motivo}] {nr.detalle}")
        if a_confirmar:
            lineas.append("")
            lineas.append(
                "Campos con un valor que hay que CONFIRMAR. Lee el correo y "
                "sus documentos: si el valor es el correcto no reportes nada, "
                "y si no lo es reporta el que corresponda.")
            for campo in a_confirmar:
                h = self.hallazgos[campo]
                lineas.append(f"  - {campo} = {h.valor}")
                lineas.append(f"      {h.a_confirmar}")
        sin_nada = self.campos_sin_nada_que_buscar
        if sin_nada:
            # Se informan, pero no se piden: el motivo dice que el dato no esta
            # en el texto, y el modelo lee el mismo texto.
            lineas.append("")
            lineas.append("Constatado que el correo no lo trae (no lo busques):")
            for campo in sin_nada:
                lineas.append(f"  - {campo}: {self.no_resueltos[campo].detalle}")
        resueltos = [c for c in self.campos_resueltos if c not in a_confirmar]
        if resueltos:
            lineas.append("")
            lineas.append("Ya resuelto (no lo vuelvas a buscar):")
            for campo in resueltos:
                lineas.append(f"  - {campo} = {self.hallazgos[campo].valor}")
        return "\n".join(lineas)


def extraer_todo(
    asunto: str,
    cuerpo: str,
    texto_adjuntos: str = "",
    from_name: str = "",
    from_address: str = "",
    sender_name: str = "",
    nombres_hilo: Optional[Sequence[Tuple[str, str]]] = None,
    cuerpo_actual: str = "",
    umbral: float = UMBRAL_CONFIANZA,
    adjuntos_ilegibles: Optional[Sequence[str]] = None,
) -> Extraccion:
    """
    Corre todos los extractores deterministas.

    El orden importa: el sexo depende del nombre del afectado, y la oficina del
    tipo de solicitud. Lo que no se resuelva queda listado con su motivo para
    que la IA reciba solo eso.
    """
    completo = f"{asunto}\n{cuerpo}\n{texto_adjuntos}".strip()
    hallazgos: Dict[str, Hallazgo] = {}
    no_resueltos: Dict[str, NoResuelto] = {}

    def registrar(par: Tuple[Optional[Hallazgo], Optional[NoResuelto]]) -> Optional[Hallazgo]:
        h, nr = par
        if h is not None and h.confianza >= umbral:
            hallazgos[h.campo] = h
            return h
        if h is not None:
            # Resuelto pero con confianza insuficiente: se escala igual, y el
            # motivo dice cual era el candidato para que la IA lo confirme.
            no_resueltos[h.campo] = NoResuelto(
                h.campo, "CONFIANZA_INSUFICIENTE",
                f"candidato '{h.valor}' con confianza {h.confianza:.2f} "
                f"por debajo del umbral {umbral:.2f}",
            )
            return None
        if nr is not None:
            no_resueltos[nr.campo] = nr
        return None

    # De quien es el tramite se lee una sola vez: lo usan el sexo, el documento
    # del remitente y su tipo, y con lecturas separadas podian discrepar.
    # La voz se lee solo en el texto NUEVO, tambien en un reenvio interno: mas
    # abajo hablan Positiva y el ciudadano a la vez, y sus "mi" son de personas
    # distintas.
    reenvio_interno = is_buzon_no_humano(from_name, from_address)
    texto_remitente = f"{asunto}\n{_texto_del_remitente(cuerpo_actual or cuerpo)}"
    voz_propia, voz_de_otro = voces_del_tramite(texto_remitente)
    el_remitente_es_el_afectado = bool(voz_propia) and not voz_de_otro

    # La identidad del afectado se busca sobre el texto sin las clausulas que
    # piden anular o corregir un dato: un correo de correccion trae la identidad
    # equivocada y la correcta, y ahi dice cual es cual.
    identidad = _sin_lo_que_se_pide_corregir(completo)

    # El nombre del afectado va primero: sirve para desambiguar el documento
    # cuando hay varios candidatos, y para deducir el sexo.
    h_afectado = registrar(extraer_nombre_afectado(identidad, asunto))
    nombre_af = h_afectado.valor if h_afectado else ""

    h_doc = registrar(extraer_documento(identidad, para="afectado",
                                        nombre_afectado=nombre_af,
                                        adjuntos_ilegibles=adjuntos_ilegibles))
    registrar(extraer_tipo_documento(
        identidad, numero=str(h_doc.valor) if h_doc else "", para="afectado"))
    registrar(extraer_telefono(f"{cuerpo}\n{texto_adjuntos}",
                               nombre_afectado=nombre_af))
    registrar(extraer_nombre_remitente(
        from_name, from_address, cuerpo,
        sender_name=sender_name, nombres_hilo=nombres_hilo,
        cuerpo_actual=cuerpo_actual or cuerpo, texto_adjuntos=texto_adjuntos,
    ))
    registrar(extraer_sexo(
        completo, nombre_afectado=nombre_af,
        texto_del_remitente=(texto_remitente if el_remitente_es_el_afectado
                             else ""),
    ))
    registrar(extraer_tipo_pqrd(asunto, cuerpo, reenvio_interno))

    h_rem = hallazgos.get("nombre_remitente")
    h_doc_rem = registrar(extraer_documento_del_remitente(
        completo,
        nombre_remitente=h_rem.valor if h_rem else "",
        nombre_afectado=nombre_af,
        documento_afectado=str(h_doc.valor) if h_doc else "",
        voz_propia=voz_propia if el_remitente_es_el_afectado else "",
    ))
    if h_doc_rem is not None:
        registrar(extraer_tipo_documento(
            completo, numero=str(h_doc_rem.valor), para="remitente"))
    else:
        no_resueltos.setdefault("tipo_documento_remitente", NoResuelto(
            "tipo_documento_remitente", "DEPENDE_DEL_DOCUMENTO_DEL_REMITENTE",
            "el tipo se sigue del documento del remitente, que no se conoce",
        ))

    h_tipo_af = hallazgos.get("tipo_documento_afectado")
    registrar(extraer_tipo_remitente(
        documento_afectado=str(h_doc.valor) if h_doc else "",
        documento_remitente=str(h_doc_rem.valor) if h_doc_rem else "",
        nombre_afectado=nombre_af,
        nombre_remitente=h_rem.valor if h_rem else "",
        tipo_documento_afectado=h_tipo_af.valor if h_tipo_af else None,
        firma_de_empleador=firma_declara_empleador(completo),
        texto_del_remitente=texto_remitente,
    ))

    registrar(extraer_tipo_solicitud(asunto, cuerpo))

    registrar(extraer_direccion(
        completo,
        nombre_afectado=nombre_af,
        documento_afectado=str(h_doc.valor) if h_doc else "",
    ))

    # Donde vive. Sin esto el municipio solo lo aporta el registro de personas,
    # y cuando el registro no tiene a la persona el radicador envia la capital:
    # el radicado dice que vive en Bogota y de ese dato depende la entrega
    # cuando la respuesta sale por correo fisico.
    for h in extraer_geografia(completo):
        registrar((h, None))

    # Con todas las decisiones de identidad ya tomadas, el nombre del remitente
    # se registra entero.
    if "nombre_remitente" in hallazgos:
        hallazgos["nombre_remitente"] = desplegar_iniciales(
            hallazgos["nombre_remitente"], completo)

    encabezado = "\n".join(
        [l for l in (cuerpo or "").splitlines() if l.strip()][:14]
    )[:1800]
    contexto = f"Asunto: {asunto}\n\n{encabezado}"
    adjuntos = asuntos_de_los_adjuntos(texto_adjuntos)
    if adjuntos:
        contexto += ("\n\nADJUNTOS (nombre del archivo y asunto que declara "
                     "el escrito):\n" + adjuntos)
    return Extraccion(
        hallazgos=hallazgos,
        no_resueltos=no_resueltos,
        contexto=contexto,
    )


# El rotulo con que un escrito declara de que trata.
_ROTULO_DE_ASUNTO = re.compile(
    r"(?im)^[ \t#*>|]*(?:asunto|referencia|ref\.?)[ \t]*:[ \t]*(.*)$")
# Un escrito se presenta en su primera pagina; mas abajo un "Asunto:" es el de
# otro documento citado.
CARACTERES_DEL_ENCABEZADO_DEL_ADJUNTO = 4000
LARGO_DEL_ASUNTO_DEL_ADJUNTO = 300
MAX_ADJUNTOS_EN_EL_CONTEXTO = 6


def asuntos_de_los_adjuntos(texto_adjuntos: str) -> str:
    """
    Una linea por adjunto: el nombre del archivo y el asunto que declara.

    Muchos correos son solo la carta de remision ("Adjunto derecho de peticion
    con sus anexos"): el cuerpo dice la FORMA del escrito y lo que se pide va en
    el adjunto. Sin esto el agente clasifica la intencion del correo sin verla.
    El nombre del archivo suele resumirla, y el "ASUNTO:" del escrito la dice.
    """
    if not texto_adjuntos:
        return ""
    # Cada adjunto convertido empieza por "# <nombre del archivo>".
    partes = re.split(r"(?m)^# (?=\S)", texto_adjuntos)
    lineas = []
    for parte in partes[1:]:
        titulo, _, resto = parte.partition("\n")
        titulo = " ".join(titulo.split())
        if not titulo:
            continue
        linea = f"- {titulo[:LARGO_DEL_ASUNTO_DEL_ADJUNTO]}"
        m = _ROTULO_DE_ASUNTO.search(resto[:CARACTERES_DEL_ENCABEZADO_DEL_ADJUNTO])
        if m:
            # El asunto sigue en los renglones de abajo hasta el primer blanco.
            cola = resto[m.end():m.end() + 2 * LARGO_DEL_ASUNTO_DEL_ADJUNTO]
            cola = cola.split("\n\n", 1)[0]
            asunto = " ".join(f"{m.group(1)} {cola}".split())
            if asunto:
                linea += f" | ASUNTO: {asunto[:LARGO_DEL_ASUNTO_DEL_ADJUNTO]}"
        lineas.append(linea)
        if len(lineas) >= MAX_ADJUNTOS_EN_EL_CONTEXTO:
            break
    return "\n".join(lineas)
