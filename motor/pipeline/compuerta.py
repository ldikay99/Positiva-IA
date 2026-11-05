"""
Compuerta de validacion previa a radicar.

Verifica coherencia entre campos, no cada campo por separado: un documento
valido y un nombre valido pueden ser incoherentes entre si. Es enteramente
deterministica y no consulta al modelo: lo que juzga es precisamente lo que el
modelo propuso, asi que preguntarle otra vez no seria una segunda opinion.

Veredictos: APROBADO, CORREGIDO (con los ajustes aplicados) o RECHAZADO.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from models.enums import TipoDocumento
from pipeline.extraccion import Hallazgo, motivo_por_el_que_no_es_un_nombre
from utils.contacto_extractor import telefono_existe
from utils.generic_filter import (
    es_el_mismo_titular,
    es_la_misma_persona,
    organizacion_tras_el_area,
    parece_razon_social,
)
from utils.tipos_documento import (
    MAX_DIGITOS,
    MIN_DIGITOS,
    TIPOS_ALFANUMERICOS,
    TIPOS_DE_ORGANIZACION,
    es_pasaporte,
    longitud_valida,
    rango_aceptable,
    solo_digitos,
)

CONFIANZA_PARA_RADICAR = 0.60

# Campos sin los cuales no se puede radicar.
OBLIGATORIOS = (
    "numero_documento_afectado",
    "nombre_afectado",
    "nombre_remitente",
    "tipo_solicitud",
)

# El piso general supone que descartar equivale a ABSTENERSE, y eso solo es
# cierto donde el campo puede quedar vacio. `tipo_solicitud` no puede: es
# obligatorio, asi que descartarlo manda el correo a revision manual, y si se
# rellena por defecto es con "Otra", que enruta a una oficina concreta. En los
# dos casos descartar afirma algo, de modo que la eleccion no es entre un dato
# dudoso y ningun dato, sino entre dos datos. Se acepta lo que el agente
# reporte y queda anotado en los avisos.
PISO_POR_CAMPO: Dict[str, float] = {"tipo_solicitud": 0.0}


def confianza_exigida(campo: str, base: float = CONFIANZA_PARA_RADICAR) -> float:
    """Confianza minima de un campo. Punto unico para las dos capas que filtran."""
    return PISO_POR_CAMPO.get(campo, base)


class Veredicto(str, Enum):
    APROBADO = "aprobado"
    CORREGIDO = "corregido"
    RECHAZADO = "rechazado"


@dataclass
class Reparo:
    """Un problema encontrado."""

    campo: str
    codigo: str
    detalle: str
    bloqueante: bool = True


@dataclass
class Dictamen:
    veredicto: Veredicto
    reparos: List[Reparo] = field(default_factory=list)
    correcciones: Dict[str, Any] = field(default_factory=dict)
    motivo_manual: str = ""

    @property
    def puede_radicar(self) -> bool:
        return self.veredicto is not Veredicto.RECHAZADO

    @property
    def bloqueantes(self) -> List[Reparo]:
        return [r for r in self.reparos if r.bloqueante]


def _normaliza(texto: Any) -> str:
    n = unicodedata.normalize("NFD", str(texto or "").lower())
    return " ".join(
        "".join(c for c in n if unicodedata.category(c) != "Mn").split()
    )


def _digitos_ascii(valor: Any) -> str:
    """
    Los digitos del valor, traducidos a ASCII sea cual sea su alfabeto.

    `solo_digitos` quita puntos, espacios y guiones, pero conserva el caracter
    tal cual viene: la expresion regular con la que quita lo que NO es digito
    reconoce como digito cualquier alfabeto, no solo el 0-9. Un documento
    escrito en digitos de ancho completo o arabigo-indios pasa entonces la
    longitud y el digito de verificacion como si fuera un numero cualquiera, y
    queda en SGDEA con un glifo que ninguna cedula real usa: ni una busqueda
    posterior ni un cruce con el registro lo van a encontrar.
    """
    convertido = []
    for c in solo_digitos(valor):
        try:
            convertido.append(str(unicodedata.digit(c)))
        except (TypeError, ValueError):
            convertido.append(c)
    return "".join(convertido)


def _revisar_documento(campo, numero, tipo, reparos, digito_verificacion_nit,
                       crudo=None):
    """
    Comprueba forma y digito de verificacion de un documento.

    La longitud se mide contra `utils.tipos_documento`, la misma tabla que usa
    el extractor. `crudo` es el valor tal como se extrajo, antes de quitarle lo
    que no son digitos: hace falta para distinguir un documento de algo que
    quedo en su campo.
    """
    try:
        declarado = int(tipo)
    except (TypeError, ValueError):
        declarado = None

    original = "" if crudo is None else str(crudo).strip()
    # Los separadores de miles y los espacios son forma de escribirlo, no otro
    # dato: "41.234.567" es un documento. Lo que queda despues de quitarlos
    # tiene que ser un numero, salvo en los tipos que admiten letras.
    sin_separadores = re.sub(r"[.\s,\-]", "", original)
    # Un pasaporte lleva letras, y el registro de una sociedad extranjera sigue
    # las reglas de su pais. Sin tipo declarado se acepta la forma de pasaporte:
    # no toda persona atendida es colombiana. Con un tipo numerico declarado no
    # hay excepcion, porque una cedula con letras no es una cedula.
    admite_letras = (
        declarado in TIPOS_ALFANUMERICOS
        or (declarado is None and es_pasaporte(original))
    )
    if original and not admite_letras and not sin_separadores.isdigit():
        # Quitarle las letras a "ABC12345" produce "12345", que tiene longitud
        # de documento y pasaria cualquier comprobacion posterior. Lo que no es
        # un numero no es un documento incompleto: es otra cosa en el campo.
        reparos.append(Reparo(
            campo, "DOCUMENTO_NO_NUMERICO",
            f"'{original[:30]}' no es un numero de documento",
        ))
        return

    if not numero:
        return

    try:
        codigo = int(tipo)
    except (TypeError, ValueError):
        codigo = None

    encaja = longitud_valida(numero, codigo)
    if encaja is False:
        minimo, maximo = rango_aceptable(codigo)
        reparos.append(Reparo(
            campo, "LONGITUD_IMPROBABLE",
            f"{len(numero)} digitos, y ese tipo de documento lleva entre "
            f"{minimo} y {maximo}",
        ))
    elif encaja is None and not MIN_DIGITOS <= len(numero) <= MAX_DIGITOS:
        reparos.append(Reparo(
            campo, "LONGITUD_IMPROBABLE",
            f"{len(numero)} digitos, y ningun tipo de documento lleva menos de "
            f"{MIN_DIGITOS} ni mas de {MAX_DIGITOS}",
        ))

    if len(set(numero)) == 1:
        reparos.append(Reparo(campo, "DOCUMENTO_DE_RELLENO",
                              f"'{numero}' tiene todos los digitos iguales"))

    if codigo == int(TipoDocumento.NI) and len(numero) == 10:
        if int(numero[9]) != digito_verificacion_nit(numero[:9]):
            reparos.append(Reparo(
                campo, "DV_NIT_INCORRECTO",
                "el digito de verificacion del NIT no cuadra",
            ))


# Numeros que existen en el plan de numeracion y aun asi no llaman a nadie:
# son el ejemplo del formulario o un relleno para pasar la validacion.
_TELEFONOS_DE_EJEMPLO = frozenset({
    "6017654821", "6027654321", "7654321", "3000000000", "1234567890",
    "3001234567", "0000000000", "9999999999",
})

# Positiva es quien RECIBE la PQRD: nunca puede ser quien la envia ni la
# persona afectada por ella. Su NIT o su linea institucional aparecen en el
# membrete de un adjunto o en el pie de un correo reenviado, y sin esta
# comprobacion terminan pegados al campo del documento como si identificaran
# a quien radica. Es el mismo criterio, aplicado al numero, con el que
# `motivo_por_el_que_no_identifica_a_nadie` descarta su nombre.
_IDENTIDAD_DE_POSITIVA = frozenset({
    "860011153", "8600111536",  # NIT, sin y con digito de verificacion
    "6502200", "6016502200",    # linea institucional, sin y con indicativo
})


def _revisar_no_es_positiva(campo, numero, reparos):
    """Bloquea el documento cuando es el NIT o el telefono de Positiva."""
    if numero in _IDENTIDAD_DE_POSITIVA:
        reparos.append(Reparo(
            campo, "DOCUMENTO_ES_DE_POSITIVA",
            f"'{numero}' es el NIT o la linea institucional de Positiva, que "
            f"recibe la PQRD y no puede ser quien la envia ni quien resulta "
            f"afectada",
        ))


def _telefono_de_relleno(digitos: str) -> str:
    """
    Por que el numero no sirve para llamar, o cadena vacia.

    Un numero con la forma correcta puede seguir sin identificar a nadie. Se
    corrige a vacio en vez de bloquear: el telefono no es obligatorio, y
    abstenerse vale mas que escribir en el registro un contacto que no existe.
    """
    if not digitos:
        return ""
    if digitos in _TELEFONOS_DE_EJEMPLO:
        return "es un numero de ejemplo, no un contacto real"
    if len(set(digitos)) == 1:
        return "repite el mismo digito"
    if len(digitos) >= 7 and all(
        int(digitos[i + 1]) - int(digitos[i]) == 1 for i in range(len(digitos) - 1)
    ):
        return "es una secuencia de digitos consecutivos"
    return ""


def _revisar_telefono(numero, documentos, reparos, correcciones):
    """
    Comprueba el telefono contra el plan de numeracion, igual que el extractor,
    y que no sea el numero de documento de la propia persona.

    No bloquea: el telefono no es obligatorio, y rechazar el correo entero por
    un contacto mal escrito manda a revision manual un caso que se puede
    radicar. Se corrige a vacio, para no escribir en la base un numero que no
    sirve para llamar a nadie.
    """
    if not numero:
        return

    digitos = solo_digitos(numero)
    if digitos and digitos in documentos:
        reparos.append(Reparo(
            "telefono_afectado", "TELEFONO_ES_EL_DOCUMENTO",
            f"'{numero}' es el numero de documento, no un telefono",
            bloqueante=False,
        ))
        correcciones["telefono_afectado"] = ""
        return

    if digitos in _IDENTIDAD_DE_POSITIVA:
        # Un numero real, que si contesta, pero contesta en Positiva: el
        # membrete del correo lo deja junto a los datos de quien escribe y el
        # extractor lo toma por el contacto de esa persona.
        reparos.append(Reparo(
            "telefono_afectado", "TELEFONO_ES_DE_POSITIVA",
            f"'{numero}' es la linea institucional de Positiva, no el "
            f"contacto de quien radica",
            bloqueante=False,
        ))
        correcciones["telefono_afectado"] = ""
        return

    motivo = _telefono_de_relleno(digitos)
    if motivo:
        reparos.append(Reparo(
            "telefono_afectado", "TELEFONO_DE_RELLENO",
            f"'{numero}' {motivo}",
            bloqueante=False,
        ))
        correcciones["telefono_afectado"] = ""
        return

    if telefono_existe(numero):
        return
    reparos.append(Reparo(
        "telefono_afectado", "TELEFONO_INEXISTENTE",
        f"'{numero}' no corresponde a ninguna numeracion vigente",
        bloqueante=False,
    ))
    correcciones["telefono_afectado"] = ""


# Formas con que un formulario representa "sin dato". Llegan como texto y se
# guardan como si fueran una direccion.
_DIRECCION_VACIA = re.compile(
    r"^\s*(?:no\s*(?:reporta|registra|aplica|tiene|posee)"
    r"|n\s*[/.]?\s*[rah]"
    r"|undefined|null|none|ninguna?|sin\s+direccion|\"\"|'')\s*\.?\s*$",
    re.IGNORECASE,
)
_CORREO_EN_TEXTO = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
_SOLO_UN_NUMERO = re.compile(r"^\s*\+?\d[\d\s().+-]{5,}\s*$")


def _revisar_direccion(direccion, reparos, correcciones):
    """
    Deja vacia la direccion que no es una direccion.

    No bloquea ni exige una forma urbana: buena parte de las direcciones del
    pais son rurales y legitimas —"CORREGIMIENTO EL SALADO LOTE 1", "KILOMETRO
    17 VIA DINAMARCA"— y exigir "calle" o "carrera" las descartaria. Solo se
    retira lo que se puede afirmar que no es una direccion: el marcador con que
    un formulario representa la ausencia de dato, un correo electronico y un
    numero telefonico suelto.
    """
    if not direccion:
        return
    texto = str(direccion).strip()

    motivo = ""
    if _DIRECCION_VACIA.match(texto):
        motivo = "es la marca de que no se reporto direccion, no una direccion"
    elif _CORREO_EN_TEXTO.search(texto):
        motivo = "contiene un correo electronico"
    elif _SOLO_UN_NUMERO.match(texto):
        motivo = "es un numero telefonico, no una direccion"
    if not motivo:
        return

    reparos.append(Reparo(
        "direccion_afectado", "DIRECCION_NO_ES_DIRECCION",
        f"'{texto[:60]}' {motivo}",
        bloqueante=False,
    ))
    correcciones["direccion_afectado"] = ""


def _revisar_caracteres_de_control(campo, valor_original, reparos, correcciones):
    """
    Quita del nombre los caracteres de formato invisibles.

    Categoria Unicode "Cf": el override de direccion (bidi), el espacio de
    ancho cero, el guion suave. Ninguno se ve en el editor donde se escribe el
    correo, y el de direccion (RLO/LRO) invierte en pantalla todo lo que le
    sigue: lo que un operador lee no es lo que quedo escrito en SGDEA, y una
    busqueda posterior por el nombre visible no encuentra el registro.
    """
    if not valor_original:
        return
    texto = str(valor_original)
    limpio = "".join(c for c in texto if unicodedata.category(c) != "Cf")
    if limpio == texto:
        return
    reparos.append(Reparo(
        campo, "CARACTERES_DE_CONTROL_EN_NOMBRE",
        f"'{texto[:60]}' lleva caracteres de formato invisibles que alteran "
        f"como se ve en pantalla",
        bloqueante=False,
    ))
    correcciones[campo] = limpio


# Un radicado de SGDEA pegado al nombre: dos a cuatro letras y una tirada larga
# de digitos, sin espacio en medio.
_RADICADO_EN_NOMBRE = re.compile(r"\b[A-Z]{2,4}\d{6,}\b", re.IGNORECASE)
# Una tirada de digitos suelta, tolerando los separadores con que se escribe un
# documento o un telefono.
_TIRADA_DE_DIGITOS = re.compile(r"(?<![A-Za-z0-9])\d[\d.\s,\-]{4,}\d(?![A-Za-z0-9])")
# Cuantos digitos hacen que una tirada sea un documento o un telefono y no parte
# de una razon social. Por debajo hay numeros que si nombran: "CONSTRUCTORA 2000
# SAS", "INVERSIONES 5 ESTRELLAS LTDA".
DIGITOS_QUE_NO_NOMBRAN = 6


def _sin_numeros_ajenos(nombre: str) -> str:
    """El nombre sin el documento, el telefono ni el radicado que traiga pegado."""
    limpio = _RADICADO_EN_NOMBRE.sub(" ", str(nombre or ""))

    def _quitar_si_es_largo(m):
        digitos = re.sub(r"\D", "", m.group())
        return " " if len(digitos) >= DIGITOS_QUE_NO_NOMBRAN else m.group()

    limpio = _TIRADA_DE_DIGITOS.sub(_quitar_si_es_largo, limpio)
    return " ".join(limpio.split())


def _revisar_numeros_en_el_nombre(campo, nombre, reparos, correcciones):
    """
    Retira del nombre el numero que se colo con el.

    Ninguna persona se llama con digitos, y el campo del nombre viaja al
    registro de SGDEA tal como se envie: un telefono, una cedula, un NIT o un
    radicado pegados quedan ahi para siempre. No bloquea, porque quitar el
    numero deja el nombre correcto —"JUAN CARLOS PEREZ 3105551234" es Juan
    Carlos Perez— y bloquear mandaria a revision manual una radicacion valida.

    Los numeros cortos se conservan: una razon social si los lleva.
    """
    if not nombre:
        return
    texto = str(nombre).strip()
    limpio = _sin_numeros_ajenos(texto)
    if limpio == texto:
        return

    if not limpio:
        # No quedo nada: el campo traia un numero y nada mas. Corregirlo a
        # vacio radicaria a alguien sin nombre.
        reparos.append(Reparo(
            campo, "NOMBRE_ES_SOLO_UN_NUMERO",
            f"'{texto[:60]}' es un numero, no el nombre de nadie",
        ))
        return

    reparos.append(Reparo(
        campo, "NOMBRE_CON_NUMERO_AJENO",
        f"'{texto[:60]}' lleva un numero que no es parte del nombre",
        bloqueante=False,
    ))
    correcciones[campo] = limpio


# Solo cargos: una palabra de area ("medicina", "mano") tambien es ajena a un
# nombre, pero lo que va antes de ella no suele ser quien firma.
_CARGOS = frozenset("""
analista asistente auxiliar coordinador coordinadora director directora gerente
asesor asesora jefe secretario secretaria contador contadora abogado abogada
ingeniero ingeniera profesional tecnico tecnica representante
""".split())


def _nombre_sin_el_cargo(nombre: Any) -> str:
    """
    El nombre de quien firma "Ana Lopez Analista de Cartera" o "Auxiliar (Ana Lopez)".

    Devuelve cadena vacia si el texto ya es un nombre o si no queda uno al quitar
    el cargo: un area sin persona no se convierte en nadie.
    """
    texto = " ".join(str(nombre or "").split())
    if not texto or not motivo_por_el_que_no_es_un_nombre(texto):
        return ""
    organizacion = organizacion_tras_el_area(texto)
    if organizacion and not motivo_por_el_que_no_es_un_nombre(organizacion):
        return organizacion
    entre_parentesis = re.search(r"\(([^()]+)\)", texto)
    candidatos = [entre_parentesis.group(1).strip()] if entre_parentesis else []
    palabras = texto.split()
    for i, palabra in enumerate(palabras):
        if _normaliza(palabra).strip(".,;:") in _CARGOS:
            candidatos.append(" ".join(palabras[:i]))
            break
    for candidato in candidatos:
        if len(candidato.split()) >= 2 and not motivo_por_el_que_no_es_un_nombre(candidato):
            return candidato
    return ""


# Formas de llegar al documento del remitente sin haberlo leido junto a su firma.
_METODOS_QUE_NO_PRUEBAN_LA_FIRMA = frozenset({
    "remitente_es_el_afectado",
    "el_tramite_es_del_remitente",
})


def _escribe_el_afectado_desde_otro_buzon(hallazgos: Dict[str, Hallazgo]) -> bool:
    """
    Si el remitente es el afectado y solo el nombre del buzon lo contradice.

    Exige que el documento del remitente sea el del afectado y que se haya leido
    del correo. El copiado porque se declaro que son la misma persona, o porque
    quien escribe habla de "mi caso", no prueba nada: es justo la declaracion que
    el nombre distinto pone en duda, y un apoderado tambien escribe asi.
    """
    def valor(campo: str) -> Any:
        h = hallazgos.get(campo)
        return h.valor if h else None

    del_remitente = hallazgos.get("numero_documento_remitente")
    nombre, remitente = valor("nombre_afectado"), valor("nombre_remitente")
    if (valor("tipo_remitente") != 3 or del_remitente is None or not nombre
            or not remitente or del_remitente.metodo in _METODOS_QUE_NO_PRUEBAN_LA_FIRMA):
        return False
    doc, doc_rem = (_digitos_ascii(valor("numero_documento_afectado")),
                    _digitos_ascii(del_remitente.valor))
    return bool(doc and doc == doc_rem and not es_el_mismo_titular(nombre, remitente))


def _revisar_naturaleza(campo_nombre, nombre, tipo, reparos):
    """
    Anota, sin bloquear, un nombre de persona junto a un documento de empresa.

    No bloquea porque el par SI puede ser cierto: en Colombia el NIT de una
    persona natural es su propia cedula, y un empleador puede serlo, como en
    "la empresa JHON MAURICIO GOMEZ PEÑA (NIT 81367440)". Bloquearlo manda a
    revision manual una radicacion valida.

    Se conserva como aviso porque la otra lectura tambien existe —que el
    extractor haya juntado el NIT de la empresa con el nombre de la persona— y
    quien revise el informe necesita verlo. La forma de resolverlo sin adivinar
    es preguntarle al registro de personas por ese documento.

    La ausencia de marca de razon social no prueba que el nombre sea de una
    persona, asi que solo se anota en la direccion que se puede afirmar: el
    documento dice empresa y el nombre no lo dice por ningun lado.
    """
    if not nombre or tipo is None:
        return
    try:
        codigo = int(tipo)
    except (TypeError, ValueError):
        return
    if codigo not in TIPOS_DE_ORGANIZACION:
        # El otro sentido SI bloquea. La razon social es una marca explicita
        # —SAS, LTDA, FUNDACION— y una sociedad no tiene cedula: la pareja es
        # imposible, no dudosa. Uno de los dos datos esta mal y no se puede
        # saber cual, asi que radicarla escribe una identidad que no existe.
        if parece_razon_social(nombre):
            reparos.append(Reparo(
                campo_nombre, "EMPRESA_CON_DOCUMENTO_DE_PERSONA",
                f"'{nombre}' es una razon social y el documento es de tipo "
                f"{codigo}, que identifica a una persona natural; el nombre "
                f"del afectado o el tipo de documento no corresponden",
            ))
        return
    if parece_razon_social(nombre):
        return
    reparos.append(Reparo(
        campo_nombre, "NOMBRE_SIN_FORMA_DE_EMPRESA",
        f"el documento es de tipo {codigo}, que identifica a una organizacion, "
        f"y '{nombre}' no lleva ninguna marca de razon social; puede ser una "
        f"persona natural con NIT, o el tipo de documento estar equivocado",
        bloqueante=False,
    ))


def validar(
    hallazgos: Dict[str, Hallazgo],
    catalogos: Optional[Dict[str, Dict[Any, str]]] = None,
    confianza_minima: float = CONFIANZA_PARA_RADICAR,
    motivos_no_resueltos: Optional[Dict[str, str]] = None,
) -> Dictamen:
    """Aplica las reglas de coherencia y devuelve el dictamen."""
    from servidor_mcp.herramientas import CATALOGOS, _digito_verificacion_nit

    catalogos = catalogos or CATALOGOS
    reparos: List[Reparo] = []
    correcciones: Dict[str, Any] = {}

    def valor(campo: str) -> Any:
        h = hallazgos.get(campo)
        return h.valor if h else None

    # ── Obligatorios ──
    for campo in OBLIGATORIOS:
        h = hallazgos.get(campo)
        if h is None or h.valor in (None, "", []):
            reparos.append(Reparo(campo, "FALTA_CAMPO_OBLIGATORIO",
                                  "sin este campo no se puede radicar"))

    # ── Evidencia y confianza de TODO hallazgo ──
    # El piso rige para cualquier campo que vaya a la base, no solo para los
    # obligatorios. Sin un obligatorio no se puede radicar; los demas se
    # descartan y el radicador envia su valor por defecto, que queda anotado.
    for campo, h in sorted(hallazgos.items()):
        if not h.evidencia:
            reparos.append(Reparo(campo, "SIN_EVIDENCIA",
                                  "todo dato debe citar su procedencia"))
        exigida = confianza_exigida(campo, confianza_minima)
        if h.valor in (None, "", []) or h.confianza >= exigida:
            continue
        obligatorio = campo in OBLIGATORIOS
        reparos.append(Reparo(
            campo, "CONFIANZA_INSUFICIENTE",
            f"confianza {h.confianza:.2f} por debajo de {exigida:.2f}",
            bloqueante=obligatorio,
        ))
        if not obligatorio:
            correcciones[campo] = ""

    # ── Codigos contra catalogo ──
    #
    # Las claves de los catalogos son enteros. La capa determinista produce
    # enteros, pero el agente reporta texto porque su herramienta declara
    # `valor` como cadena, y "2" no pertenece a un catalogo con la clave 2.
    def _codigo_de(v: Any) -> Any:
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return v

    for campo, nombre_catalogo in (
        ("tipo_remitente", "tipo_remitente"),
        ("tipo_documento_afectado", "tipo_documento"),
        ("tipo_documento_remitente", "tipo_documento"),
        ("tipo_solicitud", "tipo_solicitud"),
        ("sexo_afectado", "sexo"),
    ):
        v = valor(campo)
        if v is None:
            continue
        catalogo = catalogos.get(nombre_catalogo)
        if not catalogo:
            # Sin catalogo la comprobacion no se hizo. Callarlo escribe en SGDEA
            # un codigo que nadie verifico.
            reparos.append(Reparo(
                campo, "CATALOGO_NO_DISPONIBLE",
                f"no se pudo verificar '{v}': el catalogo {nombre_catalogo} "
                f"no esta cargado",
            ))
        elif _codigo_de(v) not in catalogo:
            reparos.append(Reparo(
                campo, "FUERA_DE_CATALOGO",
                f"'{v}' no esta en el catalogo {nombre_catalogo}",
            ))
        elif _codigo_de(v) != v:
            # El agente reporta el codigo como texto porque su herramienta
            # declara `valor` como cadena. Se guarda como entero, que es lo que
            # el catalogo y el payload usan.
            correcciones[campo] = _codigo_de(v)

    def valor_corregido(campo: str) -> Any:
        return correcciones.get(campo, valor(campo))

    # ── Un nombre sin los caracteres de formato invisibles ──
    # Va primero para que el resto de los criterios de nombre juzguen el texto
    # que de verdad se va a leer: un caracter de override bidireccional no se
    # ve en el correo de origen, pero invierte en pantalla todo lo que lo
    # sigue, y ninguna busqueda posterior por el nombre visible encuentra lo
    # que quedo escrito en SGDEA.
    for campo in ("nombre_afectado", "nombre_remitente"):
        _revisar_caracteres_de_control(campo, valor(campo), reparos, correcciones)

    # ── Un nombre nunca es una direccion de correo ──
    for campo in ("nombre_afectado", "nombre_remitente"):
        v = valor_corregido(campo)
        if v and ("@" in str(v) or _normaliza(v).startswith(("http", "www."))):
            reparos.append(Reparo(campo, "NOMBRE_ES_CORREO_O_URL",
                                  f"'{v}' no es un nombre"))

    # ── Un nombre tampoco lleva un documento, un telefono ni un radicado ──
    # Va antes del criterio de nombre para que este juzgue el valor ya limpio:
    # "JUAN CARLOS PEREZ 3105551234" es un nombre valido en cuanto se le quita
    # el telefono, y bloquearlo mandaria a revision manual algo radicable.
    for campo in ("nombre_afectado", "nombre_remitente"):
        _revisar_numeros_en_el_nombre(campo, valor_corregido(campo), reparos, correcciones)

    # ── Quien firma con la cedula del afectado es el afectado ──
    # Va antes de revisar el nombre: el del buzon desde el que escribe es de
    # otra persona, o ni siquiera es de una persona, y no es el que va.
    if _escribe_el_afectado_desde_otro_buzon(hallazgos):
        anterior = valor_corregido("nombre_remitente")
        reparos.append(Reparo(
            "nombre_remitente", "REMITENTE_CON_NOMBRE_DEL_BUZON",
            f"'{anterior}' es el buzon desde el que escribe; quien firma lleva "
            f"la cedula del afectado",
            bloqueante=False,
        ))
        correcciones["nombre_remitente"] = valor_corregido("nombre_afectado")

    # ── A quien firma con su cargo se le deja el nombre ──
    limpio = _nombre_sin_el_cargo(valor_corregido("nombre_remitente"))
    if limpio:
        reparos.append(Reparo(
            "nombre_remitente", "NOMBRE_CON_CARGO",
            f"'{valor_corregido('nombre_remitente')}' lleva el cargo junto al nombre",
            bloqueante=False,
        ))
        correcciones["nombre_remitente"] = limpio

    # ── El nombre cumple el mismo criterio que exige el extractor ──
    for campo in ("nombre_afectado", "nombre_remitente"):
        motivo = motivo_por_el_que_no_es_un_nombre(valor_corregido(campo))
        if motivo:
            reparos.append(Reparo(campo, "NO_PARECE_UN_NOMBRE",
                                  f"'{valor_corregido(campo)}': {motivo}"))

    # ── Documento: forma y digito de verificacion del NIT ──
    # El documento del remitente tambien va a SGDEA: se le exige lo mismo que
    # al del afectado.
    doc = _digitos_ascii(valor("numero_documento_afectado"))
    doc_rem = _digitos_ascii(valor("numero_documento_remitente"))
    for campo, numero in (("numero_documento_afectado", doc),
                          ("numero_documento_remitente", doc_rem)):
        crudo = valor(campo)
        if numero and numero != solo_digitos(crudo):
            # La traduccion cambio el valor: el original llevaba digitos de
            # otro alfabeto. Se guarda ya en ASCII, que es la forma que
            # cualquier busqueda posterior por ese documento va a usar.
            reparos.append(Reparo(
                campo, "DIGITOS_NO_ASCII",
                f"'{crudo}' esta escrito con digitos de otro alfabeto; se "
                f"guarda como '{numero}'",
                bloqueante=False,
            ))
            correcciones[campo] = numero
        _revisar_documento(
            campo, numero,
            valor(campo.replace("numero_", "tipo_")),
            reparos, _digito_verificacion_nit,
            crudo=crudo,
        )
        _revisar_no_es_positiva(campo, numero, reparos)

    # ── El nombre y el tipo de documento hablan del mismo sujeto ──
    for quien in ("afectado", "remitente"):
        _revisar_naturaleza(
            f"nombre_{quien}", valor(f"nombre_{quien}"),
            valor(f"tipo_documento_{quien}"), reparos,
        )

    # ── El telefono sirve para llamar a alguien ──
    _revisar_telefono(
        valor("telefono_afectado"), {d for d in (doc, doc_rem) if d},
        reparos, correcciones,
    )

    # ── La direccion es una direccion ──
    _revisar_direccion(valor("direccion_afectado"), reparos, correcciones)

    # ── Coherencia entre remitente y afectado ──
    tipo_rem = valor("tipo_remitente")
    nom_af = _normaliza(valor("nombre_afectado"))
    nom_rem = _normaliza(valor("nombre_remitente"))

    documentos_distintos = bool(doc and doc_rem and doc != doc_rem)
    # Un nombre con menos apellidos que el otro es la misma persona: el display
    # name del correo suele traer menos que la firma.
    misma_persona = es_la_misma_persona(nom_af, nom_rem)

    # El documento IDENTIFICA a la persona; el nombre solo la escribe. Quien firma
    # con la cedula del afectado desde el buzon de otro, o con un apodo, es el
    # afectado aunque el nombre no coincida. El documento copiado porque se declaro
    # que son la misma persona no cuenta: es la declaracion que aqui se comprueba.
    del_remitente = hallazgos.get("numero_documento_remitente")
    copiado = (del_remitente is not None
               and del_remitente.metodo == "remitente_es_el_afectado")
    mismo_documento = bool(doc and doc_rem and doc == doc_rem and not copiado)
    if (tipo_rem == 3 and nom_af and nom_rem and not mismo_documento
            and not es_el_mismo_titular(nom_af, nom_rem)):
        reparos.append(Reparo(
            "tipo_remitente", "TIPO_3_CON_NOMBRES_DISTINTOS",
            f"se declaro que el remitente es el afectado, pero los nombres "
            f"difieren: '{valor('nombre_remitente')}' y '{valor('nombre_afectado')}'",
        ))

    if (tipo_rem in (1, 2) and nom_af and nom_rem and misma_persona
            and not documentos_distintos):
        # No bloquea: puede ser que el tipo este mal y el dato bien. La
        # correccion a 3 es segura porque los dos nombres coinciden y ningun
        # documento los desmiente: dos personas pueden llamarse igual, y ahi
        # el documento manda.
        reparos.append(Reparo(
            "tipo_remitente", "TIPO_1_O_2_CON_MISMO_NOMBRE",
            "el remitente y el afectado tienen el mismo nombre",
            bloqueante=False,
        ))
        correcciones["tipo_remitente"] = 3

    if tipo_rem in (1, 2) and doc and doc_rem and doc == doc_rem and not misma_persona:
        # El documento del remitente no es obligatorio: se retira y el caso sigue.
        # Si ademas es un NIT junto al nombre de una persona, probablemente tampoco
        # es del afectado sino de la empresa que escribe, y eso lo decide alguien.
        try:
            nit_de_otro = (int(valor("tipo_documento_afectado")) in TIPOS_DE_ORGANIZACION
                           and not parece_razon_social(valor("nombre_afectado")))
        except (TypeError, ValueError):
            nit_de_otro = False
        reparos.append(Reparo(
            "numero_documento_remitente", "DOCUMENTO_HEREDADO_DEL_AFECTADO",
            "el remitente lleva el mismo documento que el afectado pese a ser "
            "una persona distinta",
            bloqueante=nit_de_otro,
        ))
        if not nit_de_otro:
            correcciones["numero_documento_remitente"] = ""
            correcciones["tipo_documento_remitente"] = ""

    # ── Tipologia y oficina ──
    tipo_sol = valor("tipo_solicitud")
    oficina = valor("oficina")
    if tipo_sol is not None and oficina is not None:
        try:
            from business_rules import get_office_id
        except ImportError as e:
            # Sin la tabla no se puede verificar la oficina. No bloquea, pero
            # tragarselo dejaria pasar cualquier oficina como si estuviera
            # comprobada.
            reparos.append(Reparo(
                "oficina", "TABLA_DE_OFICINAS_NO_DISPONIBLE",
                f"no se pudo verificar la oficina: {e}", bloqueante=False,
            ))
        else:
            esperada = get_office_id(tipo_sol)
            if esperada and esperada != oficina:
                # No bloquea: la tabla es la fuente de verdad y no tiene
                # ambiguedad, asi que se corrige en vez de bloquear, para no
                # mandar a revision manual un correo cuya respuesta correcta
                # ya se conoce.
                reparos.append(Reparo(
                    "oficina", "OFICINA_NO_CORRESPONDE",
                    f"para el tipo {tipo_sol} la tabla indica {esperada}, no {oficina}",
                    bloqueante=False,
                ))
                correcciones["oficina"] = esperada

    # ── Veredicto ──
    if any(r.bloqueante for r in reparos):
        return Dictamen(
            veredicto=Veredicto.RECHAZADO,
            reparos=reparos,
            motivo_manual=_motivo(reparos, motivos_no_resueltos or {}),
        )
    if correcciones:
        return Dictamen(Veredicto.CORREGIDO, reparos, correcciones)
    return Dictamen(Veredicto.APROBADO, reparos)


# Motivo del extractor -> motivo de revision manual. Las tres causas de "falta
# el documento" se traducen a motivos distintos: el operador tiene que saber si
# el dato no esta, si esta en un escaneo ilegible o si hay varios candidatos.
_MOTIVO_POR_CAUSA = {
    "DOCUMENTO_EN_ADJUNTO_ILEGIBLE": "ADJUNTO_ILEGIBLE",
    "SIN_CANDIDATOS_DE_DOCUMENTO": "SIN_DOCUMENTO",
    "SIN_NINGUN_NUMERO_EN_EL_TEXTO": "SIN_DOCUMENTO",
    "VARIOS_CANDIDATOS_AMBIGUOS": "DOCUMENTO_AMBIGUO",
    "SOLO_IDENTIFICA_UNA_EMPRESA": "SOLO_EMPRESA_SIN_AFECTADO",
}


# Campo que falta -> motivo de revision manual. Cada uno tiene el suyo: el
# motivo generico describe datos incoherentes y manda a buscar un conflicto que
# no existe cuando lo que pasa es que falta un campo.
_MOTIVO_POR_CAMPO = {
    "nombre_remitente": "SIN_NOMBRE_REMITENTE",
    "nombre_afectado": "SIN_NOMBRE_AFECTADO",
    "numero_documento_afectado": "SIN_DOCUMENTO",
    "tipo_solicitud": "SIN_TIPO_SOLICITUD",
}

# Reparos que nombran su propia causa. Quien atiende el correo necesita saber
# que fue, y `VALIDACION_RECHAZO` no distingue un caso de otro.
_MOTIVO_POR_CODIGO = {
    "EMPRESA_CON_DOCUMENTO_DE_PERSONA": "EMPRESA_CON_DOCUMENTO_DE_PERSONA",
}


def _motivo(reparos: List[Reparo], causas: Dict[str, str]) -> str:
    """
    Motivo de revision manual a partir de los reparos bloqueantes.

    Se recorren TODOS buscando uno que se pueda traducir, y solo si ninguno lo
    permite se cae al motivo generico: asi el motivo no depende del orden en que
    `validar` va añadiendo reparos.
    """
    bloqueantes = [r for r in reparos if r.bloqueante]
    if not bloqueantes:
        return ""

    # Los que tienen un motivo propio se traducen antes que nada: dicen que
    # paso, y `VALIDACION_RECHAZO` no distingue un caso de otro.
    for r in bloqueantes:
        propio = _MOTIVO_POR_CODIGO.get(r.codigo)
        if propio:
            return propio

    for r in bloqueantes:
        if r.codigo != "FALTA_CAMPO_OBLIGATORIO":
            continue
        causa = causas.get(r.campo)
        if causa and causa in _MOTIVO_POR_CAUSA:
            return _MOTIVO_POR_CAUSA[causa]
        especifico = _MOTIVO_POR_CAMPO.get(r.campo)
        if especifico:
            return especifico
    return "VALIDACION_RECHAZO"


def aplicar(
    hallazgos: Dict[str, Hallazgo], dictamen: Dictamen
) -> Dict[str, Hallazgo]:
    """Devuelve los hallazgos con las correcciones del dictamen aplicadas."""
    if not dictamen.correcciones:
        return dict(hallazgos)
    corregidos = dict(hallazgos)
    for campo, nuevo in dictamen.correcciones.items():
        anterior = corregidos.get(campo)
        corregidos[campo] = Hallazgo(
            campo=campo,
            valor=nuevo,
            evidencia=(
                "corregido por la compuerta"
                + (f" (antes {anterior.valor})" if anterior else "")
            ),
            confianza=anterior.confianza if anterior else 0.75,
            metodo="compuerta",
        )
    return corregidos


def avisos(dictamen: Dictamen) -> List[str]:
    """
    Un aviso de una linea por reparo, con el codigo delante.

    El codigo va primero para que el informe agrupe por causa: treinta correos
    rechazados por lo mismo salen en una sola clase que dice cual es.
    """
    salida = []
    for r in dictamen.reparos:
        marca = "rechazo" if r.bloqueante else "aviso"
        salida.append(f"compuerta {marca} {r.codigo}: {r.campo} — {r.detalle}")
    for campo, valor in dictamen.correcciones.items():
        salida.append(f"compuerta corrigio {campo}: se envia {valor!r}")
    return salida


def informe(dictamen: Dictamen) -> str:
    lineas = [f"VEREDICTO: {dictamen.veredicto.value.upper()}"]
    if dictamen.motivo_manual:
        lineas.append(f"  motivo manual: {dictamen.motivo_manual}")
    for r in dictamen.reparos:
        marca = "BLOQUEA" if r.bloqueante else "aviso  "
        lineas.append(f"  [{marca}] {r.campo}: {r.codigo} — {r.detalle}")
    for campo, valor in dictamen.correcciones.items():
        lineas.append(f"  corregido: {campo} -> {valor}")
    return "\n".join(lineas)
