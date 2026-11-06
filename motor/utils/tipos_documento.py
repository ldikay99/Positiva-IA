"""
Reconoce la marca del tipo de documento, distinguiendo declaracion de leyenda.

Los formularios escaneados traen impreso el menu de opciones:

    Tipo Doc  CNV RC TI CC PEP CE PA MSI ASI PPT SC
    Nit (NI) - Cedula Ciudadania (CC) - Cedula Extranjeria (CE) - Pasaporte (PA)

Ahi las siglas no dicen nada del afectado: son las casillas del papel. Tomarlas
como marca escribe en SGDEA el tipo de documento equivocado.

La regla es estructural, no una lista de frases de formulario: una LEYENDA
enumera varios tipos juntos, una DECLARACION menciona uno solo. Se cuentan las
siglas distintas en una ventana; si hay tres o mas, ninguna cuenta.
"""
import re
from typing import Dict, List, Optional, Tuple

from models.enums import TipoDocumento

# Cuantas siglas distintas juntas convierten el tramo en un menu de opciones.
# Dos no bastan: "CC o TI" es una pregunta legitima de un formulario corto.
SIGLAS_PARA_SER_LEYENDA = 3
# Separacion maxima entre dos siglas para considerarlas parte de la misma
# enumeracion. En un menu van pegadas ("RC TI CC PEP") o separadas por su
# nombre ("Cedula Ciudadania (CC) - Cedula Extranjeria (CE)"); mas alla de eso
# ya hay una frase en medio y son menciones independientes.
SEPARACION_MAXIMA = 35

# Las formas SIN punto son ambiguas con palabras corrientes del español: "ti"
# es pronombre, "pa" es coloquial, "ce" aparece en siglas. Se aceptan igual,
# porque los formularios reales las escriben asi, pero solo cuando NO estan
# rodeadas de otras siglas.
_SIGLAS: Tuple[Tuple[str, str], ...] = (
    ("nit", r"\bn\.?\s?i\.?t\.?\b"),
    ("cc", r"\bc\.?\s?c\.?\b"),
    ("ce", r"\bc\.?\s?e\.?\b"),
    ("ti", r"\bt\.?\s?i\.?\b"),
    ("pa", r"\bp\.?\s?a\.?\b"),
    ("ppt", r"\bp\.?\s?p\.?\s?t\.?\b"),
    ("pt", r"\bp\.?\s?t\.?\b"),
    ("pep", r"\bp\.?\s?e\.?\s?p\.?\b"),
    ("pe", r"\bp\.?\s?e\.?\b"),
    ("rc", r"\br\.?\s?c\.?\b"),
    ("cd", r"\bc\.?\s?d\.?\b"),
    ("ni", r"\bn\.?\s?i\.?\b"),
    ("cnv", r"\bc\.?\s?n\.?\s?v\.?\b"),
    ("sc", r"\bs\.?\s?c\.?\b"),
)
_COMPILADAS = tuple((n, re.compile(p, re.IGNORECASE)) for n, p in _SIGLAS)


# Longitud del NUMERO segun el tipo, en digitos. No es un rango inventado:
# cada uno lo fija la entidad que expide el documento.
#
#   CC   entre 5 y 10. Las mas antiguas son cortas y sus titulares siguen
#        radicando; las emitidas desde el 2000 tienen diez y empiezan por 1.
#        El piso de cinco es practico, no legal: por debajo de diez mil los
#        titulares habrian nacido antes de 1900.
#   TI   10 u 11, el NUIP del menor.
#   NP   igual que la TI: el registro civil usa el mismo NUIP.
#   CE   6 o 7, las asigna Migracion Colombia.
#   PPT  7, el que expide Migracion Colombia hoy ("PPT 2133540"); se admite
#        de 6 a 9 porque los primeros y los de otros sistemas varian.
#   PE   15, el permiso especial de permanencia.
#   NI   9, mas el digito de verificacion cuando viene con el.
#   CD   6 a 10, carne diplomatico.
#   PA   alfanumerico: no se mide por digitos (ver `es_pasaporte`).
LONGITUD_POR_TIPO: Dict[int, Tuple[int, int]] = {
    int(TipoDocumento.CC): (5, 10),
    int(TipoDocumento.TI): (10, 11),
    int(TipoDocumento.NP): (10, 11),
    int(TipoDocumento.CE): (6, 7),
    int(TipoDocumento.PPT): (6, 9),
    int(TipoDocumento.PE): (15, 15),
    int(TipoDocumento.NI): (9, 10),
    int(TipoDocumento.CD): (6, 10),
}

# Tipos que identifican a una ORGANIZACION y no a una persona natural. Un
# documento de estos con un nombre de persona al lado es un par imposible.
TIPOS_DE_ORGANIZACION = frozenset({
    int(TipoDocumento.NI), int(TipoDocumento.SE), int(TipoDocumento.FI),
})

# Tipos cuyo numero NO se mide en digitos. Un pasaporte lleva letras, y el
# registro de una sociedad extranjera sigue las reglas de su pais de origen.
# Exigirles que sean numericos rechazaria documentos validos de personas y
# empresas que no son colombianas.
TIPOS_ALFANUMERICOS = frozenset({
    int(TipoDocumento.PA), int(TipoDocumento.SE),
})

# Un pasaporte no es numerico: letras y digitos, sin separadores.
_PASAPORTE_VALIDO = re.compile(r"^[A-Z]{0,3}\d{5,9}[A-Z]{0,2}$", re.IGNORECASE)

# Fuera de todo rango conocido no vale la pena ni evaluarlo: tres digitos o
# veinte no son un documento de nadie. Se deriva de la tabla para que no quede
# un numero suelto que contradiga los rangos de arriba.
MIN_DIGITOS = min(minimo for minimo, _ in LONGITUD_POR_TIPO.values())
MAX_DIGITOS = max(maximo for _, maximo in LONGITUD_POR_TIPO.values())


def solo_digitos(valor: object) -> str:
    """
    Los digitos del valor, sin puntos, espacios ni guiones.

    Es la forma en que un documento se compara y se almacena: la que valida la
    compuerta tiene que ser la misma que llega al payload.
    """
    return re.sub(r"\D", "", str(valor or ""))


def es_pasaporte(numero: str) -> bool:
    """Un pasaporte lleva letras: no se valida por cantidad de digitos."""
    return bool(_PASAPORTE_VALIDO.match(str(numero or "").strip()))


def rango_aceptable(tipo: int) -> Optional[Tuple[int, int]]:
    """
    Longitudes que NO contradicen al tipo, que es mas ancho que las tipicas.

    `LONGITUD_POR_TIPO` describe la forma habitual de cada documento y sirve
    para sugerir tipos a partir de un numero. Para rechazar hace falta otra
    cosa: solo se objeta lo que el tipo no puede tener.

    La diferencia es el NIT. El de una persona juridica tiene nueve digitos mas
    el de verificacion, pero el de una persona NATURAL es su propia cedula, y un
    empleador puede serlo: "la empresa JHON MAURICIO GOMEZ PEÑA (NIT 81367440)".
    Se deriva del rango de la cedula en vez de escribirse aparte, para que
    ensanchar la cedula no deje esto desfasado.
    """
    if tipo == int(TipoDocumento.NI):
        propio = LONGITUD_POR_TIPO[int(TipoDocumento.NI)]
        cedula = LONGITUD_POR_TIPO[int(TipoDocumento.CC)]
        return min(propio[0], cedula[0]), max(propio[1], cedula[1])
    return LONGITUD_POR_TIPO.get(tipo)


def longitud_valida(numero: str, tipo: Optional[int]) -> Optional[bool]:
    """
    Si la longitud NO contradice al tipo declarado.

    Devuelve None cuando no se puede decidir: sin tipo declarado, o con un
    pasaporte, que no se mide asi. None significa "no se sabe", que no es lo
    mismo que False, y el llamador tiene que distinguirlo para no rechazar un
    documento por una comprobacion que nunca se hizo.
    """
    digitos = solo_digitos(numero)
    if tipo is None:
        return None
    if int(tipo) == int(TipoDocumento.PA):
        return None
    rango = rango_aceptable(int(tipo))
    if rango is None:
        return None
    return rango[0] <= len(digitos) <= rango[1]


def tipos_compatibles(numero: str) -> List[int]:
    """
    Tipos cuya longitud encaja con este numero.

    Sirve cuando el correo no declara el tipo: en vez de suponerlo, se le dice
    al agente entre cuales puede elegir.
    """
    digitos = solo_digitos(numero)
    if not digitos:
        return []
    return sorted(
        codigo for codigo, (minimo, maximo) in LONGITUD_POR_TIPO.items()
        if minimo <= len(digitos) <= maximo
    )


def posiciones_de_siglas(texto: str) -> List[Tuple[int, str]]:
    """Todas las apariciones de una sigla de tipo, como (posicion, sigla)."""
    encontradas: List[Tuple[int, str]] = []
    for nombre, rx in _COMPILADAS:
        for m in rx.finditer(texto or ""):
            encontradas.append((m.start(), nombre))
    encontradas.sort()
    return encontradas


def tramos_de_leyenda(texto: str) -> List[Tuple[int, int]]:
    """
    Tramos del texto donde se enumeran los tipos en vez de declarar uno.

    Devuelve pares (inicio, fin). Se encadenan las siglas contiguas y la cadena
    se corta en un salto de linea o en un hueco grande: mas alla de eso ya hay
    una frase en medio y son menciones independientes, no un menu. Una cadena
    con tres o mas siglas DISTINTAS es una leyenda de formulario.
    """
    posiciones = posiciones_de_siglas(texto)
    if len(posiciones) < 2:
        return []

    tramos: List[Tuple[int, int]] = []
    cadena: List[Tuple[int, str]] = []

    def cerrar() -> None:
        distintas = len({s for _, s in cadena})
        if distintas >= SIGLAS_PARA_SER_LEYENDA:
            tramos.append((cadena[0][0], cadena[-1][0] + 8))
        elif distintas >= 2 and _pegadas_sin_palabra(texto, cadena):
            # Hasta el final exacto de la ultima sigla: el numero puede ir
            # pegado detras y no se puede borrar ni una cifra suya.
            ultima, sigla = cadena[-1]
            m = dict(_COMPILADAS)[sigla].match(texto, ultima)
            tramos.append((cadena[0][0], m.end() if m else ultima + len(sigla)))

    for posicion, sigla in posiciones:
        if cadena:
            anterior = cadena[-1][0]
            entre = texto[anterior:posicion]
            if posicion - anterior > SEPARACION_MAXIMA or "\n" in entre:
                cerrar()
                cadena = []
        cadena.append((posicion, sigla))
    if cadena:
        cerrar()
    return _fusionar(tramos)


# Lo que puede haber entre dos casillas de un menu: espacios, signos y la "x"
# con que se marca una. "CC o TI" lleva una palabra en medio y es una pregunta.
_ENTRE_CASILLAS = re.compile(r"^[\s.,;:/|()\[\]_\-]*(?:x[\s.,;:/|()\[\]_\-]*)?$",
                             re.IGNORECASE)


def _pegadas_sin_palabra(texto: str, cadena: List[Tuple[int, str]]) -> bool:
    """
    Si las siglas de la cadena van una tras otra sin palabra en medio.

    "identificado con X TI CE No." es el menu impreso con la casilla de CC
    tachada: dos siglas seguidas no declaran nada, porque nadie escribe dos
    tipos para un mismo documento. Con un conector en medio ("CC o TI") si es
    una pregunta, y no se toca.
    """
    for (anterior, sigla), (posicion, _) in zip(cadena, cadena[1:]):
        rx = dict(_COMPILADAS)[sigla]
        m = rx.match(texto, anterior)
        fin = m.end() if m else anterior + len(sigla)
        if not _ENTRE_CASILLAS.match(texto[fin:posicion]):
            return False
    return True


def _fusionar(tramos: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Une los tramos que se solapan, para no repetir el mismo trozo."""
    if not tramos:
        return []
    tramos.sort()
    unidos = [tramos[0]]
    for inicio, fin in tramos[1:]:
        ultimo_inicio, ultimo_fin = unidos[-1]
        if inicio <= ultimo_fin:
            unidos[-1] = (ultimo_inicio, max(ultimo_fin, fin))
        else:
            unidos.append((inicio, fin))
    return unidos


def sin_leyendas(texto: str) -> str:
    """
    El mismo texto con los tramos de leyenda en blanco.

    Se sustituyen por espacios en vez de borrarse para que las posiciones del
    resto no se muevan: otros extractores miden distancias sobre este texto.
    """
    tramos = tramos_de_leyenda(texto or "")
    if not tramos:
        return texto or ""
    partes = list(texto)
    for inicio, fin in tramos:
        for i in range(inicio, min(fin, len(partes))):
            if partes[i] != "\n":
                partes[i] = " "
    return "".join(partes)


def esta_en_leyenda(texto: str, posicion: int) -> bool:
    """Si esa posicion cae dentro de un menu de opciones."""
    return any(i <= posicion < f for i, f in tramos_de_leyenda(texto or ""))
