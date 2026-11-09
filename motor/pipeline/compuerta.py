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
from pipeline.extraccion import (
    Hallazgo,
    cortar_en_el_rotulo_siguiente,
    motivo_por_el_que_no_es_un_nombre,
)
from utils.contacto_extractor import (
    solo_en_renglones_de_entidad,
    telefono_existe,
)
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

    # La tarjeta de identidad de un menor de hoy es su NUIP: diez u once
    # digitos que empiezan por 1. Uno que empiece por otra cifra es una lectura
    # del OCR ("4 449.935.863" por "1 119.935.863"), no un documento, y
    # radicarlo registra al menor con un numero que no es el suyo.
    if (codigo == int(TipoDocumento.TI) and numero.isdigit()
            and encaja is not False and not numero.startswith("1")):
        reparos.append(Reparo(
            campo, "TI_CON_FORMA_IMPROBABLE",
            f"'{numero}' no empieza por 1, y una tarjeta de identidad vigente "
            f"es el NUIP del menor, que si: puede ser un error de lectura",
        ))

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
# Lo que el OCR deja de una direccion que no pudo leer: signos que ninguna
# direccion lleva ("cale 6 A N.&! -3.."). Uno suelto puede ser un "#" mal leido;
# dos ya no es una direccion que lleve a alguna parte.
_SIGNO_AJENO_A_UNA_DIRECCION = re.compile(r"[^\w\s#\-.,°º/()'\"]", re.UNICODE)
MAX_SIGNOS_AJENOS_EN_DIRECCION = 1
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

    # El rotulo del campo siguiente del formulario arrastrado con la direccion
    # ("CALLE 21 N 14-19E-mail:"): se corta ahi y se juzga lo que queda.
    limpia = cortar_en_el_rotulo_siguiente(texto)
    if limpia and limpia != texto:
        reparos.append(Reparo(
            "direccion_afectado", "DIRECCION_CON_ROTULO_AJENO",
            f"'{texto[:60]}' arrastra el rotulo de otro campo del formulario: "
            f"se envia '{limpia[:60]}'",
            bloqueante=False,
        ))
        correcciones["direccion_afectado"] = limpia
        texto = limpia

    motivo = ""
    if _DIRECCION_VACIA.match(texto):
        motivo = "es la marca de que no se reporto direccion, no una direccion"
    elif _CORREO_EN_TEXTO.search(texto):
        motivo = "contiene un correo electronico"
    elif _SOLO_UN_NUMERO.match(texto):
        motivo = "es un numero telefonico, no una direccion"
    elif len(_SIGNO_AJENO_A_UNA_DIRECCION.findall(texto)) > MAX_SIGNOS_AJENOS_EN_DIRECCION:
        motivo = "es lectura ilegible del OCR, no una direccion"
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
        # Lo mismo una entidad publica ("Imprenta Nacional"), que tampoco
        # tiene cedula aunque no lleve SAS ni LTDA.
        if _parece_entidad(nombre):
            reparos.append(Reparo(
                campo_nombre, "EMPRESA_CON_DOCUMENTO_DE_PERSONA",
                f"'{nombre}' es una razon social y el documento es de tipo "
                f"{codigo}, que identifica a una persona natural; el nombre "
                f"del afectado o el tipo de documento no corresponden",
            ))
        return
    if _parece_entidad(nombre):
        return
    reparos.append(Reparo(
        campo_nombre, "NOMBRE_SIN_FORMA_DE_EMPRESA",
        f"el documento es de tipo {codigo}, que identifica a una organizacion, "
        f"y '{nombre}' no lleva ninguna marca de razon social; puede ser una "
        f"persona natural con NIT, o el tipo de documento estar equivocado",
        bloqueante=False,
    ))


# ── Lo que un nombre de persona no lleva ──
#
# Palabras del tramite: cuando aparecen en el nombre es que se tomo un trozo
# del asunto ("NIEGA PERMISO SINDICAL AURA"). Ninguna es un nombre ni un
# apellido. No se aplican a una razon social, que si puede llevarlas.
_PALABRAS_DEL_TRAMITE = frozenset("""
niega negacion negativa negado negada permiso permisos sindical solicitud
solicita solicito solicitamos reclamacion reclamo queja peticion tutela
radicacion radicado respuesta remision remite traslado pago pagos reembolso
incapacidad incapacidades calificacion dictamen accidente afiliacion
certificado certificacion cobro factura urgente asunto referencia
notificacion citacion recurso reposicion apelacion desacato requerimiento
documentos soporte soportes anexo anexos novedad reintegro prestaciones
auxilio envio salvo
""".split())

# Tratamientos: se quitan sin mas, porque lo que sigue es el nombre entero.
# "Yo," abre la presentacion de una carta ("Yo, MARIA FATIMA CANAL") y tampoco
# es parte del nombre.
_TRATAMIENTOS = frozenset("""
senor senora sr sra srta don dona doctor doctora dr dra ing lic yo
""".split())

# Roles con que un texto presenta a la persona ("la servidora publica ...",
# "el trabajador ..."). Lo que sigue suele ser el nombre, pero cuando viene
# de un asunto o de un renglon cortado puede faltarle el apellido.
_ROLES_DELANTE = frozenset("""
servidor servidora funcionario funcionaria trabajador trabajadora empleado
empleada docente contratista paciente afiliado afiliada usuario usuaria
colaborador colaboradora publico publica
""".split())

# Con el rol quitado, un nombre de menos palabras que estas puede estar cortado.
PALABRAS_DE_UN_NOMBRE_COMPLETO = 3

# Organizaciones que `parece_razon_social` no marca porque no son sociedades.
_PALABRAS_DE_ENTIDAD = frozenset("""
imprenta registraduria fiscalia procuraduria contraloria personeria
defensoria notaria juzgado tribunal policia ejercito armada nacional distrital
departamental drogueria farmacia ferreteria supermercado
droguerias farmacias ferreterias supermercados almacen almacenes
""".split())

_CONECTORES_DE_NOMBRE = frozenset("de del la las los y e da van".split())


def _palabras(texto: Any) -> List[str]:
    return [p.strip(".,;:()") for p in _normaliza(texto).split() if p.strip(".,;:()")]


# Codigo de SGDEA para un remitente que es un tercero: una empresa u otra entidad.
TIPO_REMITENTE_TERCERO = 2


def _empresa_que_escribe(h: Any) -> str:
    from pipeline.extraccion import organizacion_que_escribe

    return organizacion_que_escribe(h)


def _parece_entidad(nombre: Any) -> bool:
    """Si el texto nombra una organizacion y no a una persona."""
    if not nombre:
        return False
    return parece_razon_social(str(nombre)) or bool(
        set(_palabras(nombre)) & _PALABRAS_DE_ENTIDAD)


def _es_nombre_de_persona(nombre: Any) -> bool:
    return bool(nombre) and not _parece_entidad(nombre) and not \
        motivo_por_el_que_no_es_un_nombre(str(nombre))


def _revisar_palabras_del_tramite(campo, nombre, reparos):
    """Bloquea el nombre que trae palabras del asunto: no se sabe donde empieza."""
    if not nombre or _parece_entidad(nombre):
        return
    sobrantes = [p for p in str(nombre).split()
                 if _normaliza(p).strip(".,;:()") in _PALABRAS_DEL_TRAMITE]
    if sobrantes:
        reparos.append(Reparo(
            campo, "NOMBRE_CON_PALABRAS_DEL_TRAMITE",
            f"'{nombre}' lleva palabras del tramite ({', '.join(sobrantes)}): "
            f"se tomo un trozo del asunto y no se sabe donde empieza el nombre",
        ))


def _nombre_sin_reparos(nombre: Any) -> bool:
    """Si el nombre pasa, tal cual, los criterios de nombre de la compuerta."""
    if not nombre or motivo_por_el_que_no_es_un_nombre(str(nombre)):
        return False
    reparos: List[Reparo] = []
    correcciones: Dict[str, Any] = {}
    _revisar_rol_delante("x", nombre, reparos, correcciones)
    _revisar_palabras_del_tramite("x", nombre, reparos)
    return not any(r.bloqueante for r in reparos)


def _revisar_rol_delante(campo, nombre, reparos, correcciones):
    """
    Quita el tratamiento o el rol que precede al nombre.

    "Sr. Juan Perez Gomez" es Juan Perez Gomez. Con un rol ("SERVIDORA PUBLICA
    LYDA ESMERALDA") lo que queda puede estar cortado: si le quedan menos de
    tres palabras se bloquea, porque radicar "LYDA ESMERALDA" escribe un nombre
    que no es el de nadie.
    """
    if not nombre or _parece_entidad(nombre):
        return
    palabras = str(nombre).split()
    i, hubo_rol = 0, False
    while i < len(palabras):
        p = _normaliza(palabras[i]).strip(".,;:()")
        if p in _TRATAMIENTOS:
            i += 1
        elif p in _ROLES_DELANTE:
            hubo_rol = True
            i += 1
        elif p in ("el", "la") and i + 1 < len(palabras) and (
                _normaliza(palabras[i + 1]).strip(".,;:()")
                in _ROLES_DELANTE | _TRATAMIENTOS):
            i += 1
        else:
            break
    if i == 0:
        return
    resto = " ".join(palabras[i:])
    propias = [p for p in _palabras(resto) if p not in _CONECTORES_DE_NOMBRE]
    if hubo_rol and len(propias) < PALABRAS_DE_UN_NOMBRE_COMPLETO:
        reparos.append(Reparo(
            campo, "NOMBRE_CON_ROL_Y_CORTADO",
            f"'{nombre}' empieza por un cargo o rol y lo que queda "
            f"('{resto}') puede ser un nombre incompleto",
        ))
        return
    if len(propias) < 2:
        return  # el criterio general de nombre lo rechaza
    reparos.append(Reparo(
        campo, "NOMBRE_CON_ROL_DELANTE",
        f"'{nombre}' lleva delante un tratamiento o un rol",
        bloqueante=False,
    ))
    correcciones[campo] = resto


# Lo que un texto antepone a una razon social ("la empresa SUPERMERCADOS
# OLIVAN"). No es parte del nombre registrado. "Sociedad" no va: abre razones
# sociales reales ("Sociedad Medica X SAS").
_ANTEPUESTO_A_UNA_ENTIDAD = frozenset("empresa entidad compania firma".split())


def _revisar_entidad_con_antepuesto(campo, nombre, reparos, correcciones):
    """
    Quita "empresa" o "la empresa" delante de una organizacion.

    Solo cuando la palabra va en minuscula, que es como la escribe la frase que
    la presenta ("la empresa SUPERMERCADOS OLIVAN"), o con el articulo delante
    si todo va en mayuscula. Con mayuscula inicial abre el nombre propio:
    "Empresa Social del Estado ESE IMSALUD" es la razon social entera, y
    quitarle la primera palabra registra una entidad que no existe.
    """
    if not nombre:
        return
    palabras = str(nombre).split()
    i = 0
    if palabras and _normaliza(palabras[0]).strip(".,;:()") in ("la", "el"):
        i = 1
    if i >= len(palabras) or _normaliza(palabras[i]).strip(".,;:()") \
            not in _ANTEPUESTO_A_UNA_ENTIDAD:
        return
    escrita = palabras[i].strip(".,;:()")
    todo_mayuscula = str(nombre).upper() == str(nombre)
    if not (escrita.islower() or (todo_mayuscula and i == 1)):
        return
    resto = " ".join(palabras[i + 1:])
    # "Empresa de Energia del Pacifico SA ESP": ahi "Empresa" abre el nombre.
    if _palabras(resto)[:1] and _palabras(resto)[0] in _CONECTORES_DE_NOMBRE:
        return
    if len(_palabras(resto)) < 2 or not _parece_entidad(resto):
        return
    reparos.append(Reparo(
        campo, "ENTIDAD_CON_PALABRA_ANTEPUESTA",
        f"'{nombre}' lleva delante una palabra que no es del nombre",
        bloqueante=False,
    ))
    correcciones[campo] = resto


# ── Documento de una persona juridica ──

def _es_nit_de_persona_juridica(numero: str, digito_verificacion_nit,
                                fuentes: Optional["Fuentes"] = None) -> bool:
    """
    Si el numero tiene la forma del NIT de una persona juridica.

    El NIT de una sociedad o entidad publica es de nueve digitos que empiezan
    por 8 o 9, mas el digito de verificacion: diez digitos con ese digito
    correcto no son una cedula, porque las de diez empiezan por 1.

    Con nueve digitos no se puede saber por la forma: la cedula de ocho mas
    su digito de verificacion (el NIT de una persona natural) tiene la misma.
    Solo se afirma si el correo lo escribe seguido de su propio digito de
    verificacion, que es como se escribe el NIT de una entidad.
    """
    if not numero or numero[0] not in "89" or not numero.isdigit():
        return False
    if len(numero) == 10:
        return int(numero[9]) == digito_verificacion_nit(numero[:9])
    if len(numero) == 9 and fuentes is not None:
        return fuentes.contiene_cifras(numero + str(digito_verificacion_nit(numero)))
    return False


# ── Telefono, direccion y geografia ──

_MARCAS_DE_DIRECCION = frozenset("""
calle cl cll carrera cra kr kra cr crr avenida av ave avda diagonal dg diag
transversal tv tr transv trans circular cq autopista via km kilometro vereda
vda corregimiento barrio br manzana mz mza lote lt casa apartamento apto
bloque torre edificio edif piso oficina local conjunto urbanizacion urb finca
hacienda parcela sector carretera predio etapa interior int no nro
""".split())
_NUMERACION_DE_DIRECCION = re.compile(r"#|\bn[°ºo]\.?\s*\d|\d+\s*[a-z]?\s*-\s*\d+",
                                      re.IGNORECASE)
# Mas palabras que estas ya es una frase, no una direccion.
MAX_PALABRAS_DIRECCION = 16


def _motivo_direccion_sin_forma(texto: str) -> str:
    palabras = _palabras(texto)
    if len(palabras) > MAX_PALABRAS_DIRECCION:
        return "es una frase, no una direccion"
    tokens = set(re.findall(r"[a-z]+", _normaliza(texto)))
    if tokens & _MARCAS_DE_DIRECCION or _NUMERACION_DE_DIRECCION.search(texto):
        return ""
    return "no lleva ninguna marca de direccion (calle, carrera, vereda, #...)"


def _revisar_geografia(departamento, municipio, reparos, correcciones):
    """El municipio tiene que pertenecer al departamento, segun el catalogo."""
    if departamento in (None, "") or municipio in (None, ""):
        return
    try:
        from pipeline.geografia import _catalogo
        por_departamento = _catalogo()[1]
        dep, mun = int(departamento), int(municipio)
    except Exception:
        return  # sin catalogo no hay con que comparar
    if mun in por_departamento.get(dep, {}).values():
        return
    reparos.append(Reparo(
        "municipio_afectado", "MUNICIPIO_NO_ES_DEL_DEPARTAMENTO",
        f"el municipio {municipio} no pertenece al departamento {departamento}",
        bloqueante=False,
    ))
    correcciones["departamento_afectado"] = ""
    correcciones["municipio_afectado"] = ""


# ── Sustento: lo que dice el agente tiene que estar en el correo ──

# Metodos con que llega un valor propuesto por el modelo. Lo del registro de
# personas y lo de las reglas deterministas ya citan su procedencia.
METODOS_DEL_AGENTE = frozenset({"agente_ia"})

# Campo -> forma de buscarlo en las fuentes. Los codigos de catalogo no se
# buscan: "1" o "16" no se escriben en el correo.
_SUSTENTO_POR_CAMPO = {
    "nombre_afectado": "palabras",
    "nombre_remitente": "palabras",
    "numero_documento_afectado": "cifras",
    "numero_documento_remitente": "cifras",
    "telefono_afectado": "telefono",
    "direccion_afectado": "direccion",
}

_SEPARADOR_DE_CIFRAS = re.compile(r"(?<=\d)[.\s,\-'’`´_]+(?=\d)")
_MINUSCULA_MAYUSCULA = re.compile(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Þ])")
_CC_PEGADO = re.compile(r"(?i)(?<=[a-zà-ÿ])(?=c\.\s?c\.\s?(?:no\.?\s?)?\d)")
_MARCA_DE_DOCUMENTO = re.compile(
    r"\b(?:c\s?\.?\s?c|cedula|nit|documento|identificacion|radicado|rad"
    r"|expediente|siniestro)\b[^a-z0-9]{0,6}(?:(?:no|nro|numero|de)\b"
    r"[^a-z0-9]{0,4}){0,2}(\d{6,})")
_MARCA_DE_NIT = re.compile(
    r"\bnit\b[^a-z0-9]{0,6}(?:(?:no|nro|numero)\b[^a-z0-9]{0,4})?(\d{9,10})(?!\d)")
_MARCA_DE_TELEFONO = re.compile(
    r"\b(?:tel|telefono|cel|celular|movil|whatsapp|contacto|fijo)\b"
    r"[^a-z0-9]{0,6}(?:(?:no|nro|numero|de)\b[^a-z0-9]{0,4}){0,2}"
    r"\+?(\d{7,})")


class Fuentes:
    """
    El texto del correo plegado para comparar: sin tildes, sin mayusculas y
    con las cifras sin separadores ("41.234.567" -> "41234567").
    """

    def __init__(self, texto: str) -> None:
        # El texto extraido de un PDF pega renglones: "Angelica PerezCedula".
        # La mayuscula tras una minuscula se separa antes de plegar.
        separado = _MINUSCULA_MAYUSCULA.sub(" ", str(texto or ""))
        self.plegado = _SEPARADOR_DE_CIFRAS.sub("", _normaliza(separado))
        self.compacto = re.sub(r"[^a-z0-9]", "", self.plegado)
        self.palabras = re.findall(r"[a-z]+|[0-9]+", self.plegado)
        self.indice: Dict[str, List[int]] = {}
        for i, p in enumerate(self.palabras):
            self.indice.setdefault(p, []).append(i)
        self.de_documento = {m.group(1) for m in _MARCA_DE_DOCUMENTO.finditer(self.plegado)}
        self.de_telefono = {m.group(1) for m in _MARCA_DE_TELEFONO.finditer(self.plegado)}
        # Con y sin el digito de verificacion: se escribe de las dos formas.
        self.de_nit = {n for m in _MARCA_DE_NIT.finditer(self.plegado)
                       for n in (m.group(1), m.group(1)[:9])}

    def contiene_cifras(self, digitos: str) -> bool:
        return bool(digitos) and digitos in self.plegado

    def contiene_palabras(self, valor: Any, holgura: int = 6) -> bool:
        """Todas las palabras del valor, en cualquier orden, a poca distancia."""
        palabras = [p for p in re.findall(r"[a-z]+|[0-9]+", _normaliza(valor))
                    if len(p) > 1 and p not in _CONECTORES_DE_NOMBRE]
        if not palabras:
            return True
        # Escrito tal cual pero sin espacios ("JUANPEREZ" en un PDF mal leido).
        if "".join(palabras) in self.compacto:
            return True
        posiciones = [self.indice.get(p, []) for p in palabras]
        if not all(posiciones):
            return False
        ventana = len(palabras) + holgura
        from bisect import bisect_left
        for ancla in min(posiciones, key=len):
            if all(
                (k := bisect_left(ps, ancla - ventana)) < len(ps)
                and ps[k] <= ancla + ventana
                for ps in posiciones
            ):
                return True
        return False

    def proporcion_de_palabras(self, valor: Any) -> float:
        palabras = [p for p in re.findall(r"[a-z]+", _normaliza(valor)) if len(p) > 2]
        if not palabras:
            return 1.0
        return sum(1 for p in palabras if p in self.indice) / len(palabras)


def _telefono_en_digitos(valor: Any) -> str:
    digitos = _digitos_ascii(valor)
    if digitos.startswith("57") and len(digitos) in (11, 12):
        digitos = digitos[2:]
    return digitos


def _tiene_sustento(forma: str, valor: Any, fuentes: Fuentes) -> bool:
    if forma == "palabras":
        return fuentes.contiene_palabras(valor)
    if forma == "cifras":
        return fuentes.contiene_cifras(_digitos_ascii(valor))
    if forma == "telefono":
        return fuentes.contiene_cifras(_telefono_en_digitos(valor))
    if forma == "direccion":
        cifras = re.findall(r"\d+", _normaliza(valor))
        return (all(fuentes.contiene_cifras(c) for c in cifras)
                and fuentes.proporcion_de_palabras(valor) >= 0.6)
    return True


def _revisar_sustento(hallazgos, valor_corregido, fuentes, reparos, correcciones):
    """
    Exige que lo que propuso el agente aparezca en el correo.

    El agente lee el correo y responde con texto libre: puede completar un
    apellido que no esta, juntar el nombre de un renglon con el apellido de
    otro, o reportar la entidad que deduce del dominio. Un dato que no esta en
    ninguna fuente no se envia. En un obligatorio eso es revision manual; en
    un opcional se envia vacio.
    """
    for campo, forma in _SUSTENTO_POR_CAMPO.items():
        h = hallazgos.get(campo)
        if h is None or h.metodo not in METODOS_DEL_AGENTE:
            continue
        v = valor_corregido(campo)
        if v in (None, "", []) or _tiene_sustento(forma, v, fuentes):
            continue
        obligatorio = campo in OBLIGATORIOS
        reparos.append(Reparo(
            campo, "SIN_SUSTENTO_EN_EL_CORREO",
            f"'{str(v)[:60]}' lo propuso el agente y no aparece en el asunto, "
            f"el cuerpo, el hilo ni los adjuntos",
            bloqueante=obligatorio,
        ))
        if not obligatorio:
            correcciones[campo] = ""
            if campo == "numero_documento_remitente":
                correcciones["tipo_documento_remitente"] = ""


# ── Rol del documento: de quien es el numero segun como lo presenta el texto ──
#
# Un numero con forma de cedula no es la cedula del afectado por estar cerca de
# su nombre. El texto dice que es: "siniestro no. 60741136" es un siniestro
# aunque la frase empiece por "identificado con"; "CC 1018477636 / RM
# 1018477636" es la firma de un medico; "DEMANDANTE COOPERATIVA X (NIT ...)" es
# la otra parte del proceso. Se juzga por el ROTULO MAS CERCANO que precede al
# numero en cada aparicion, que es el que lo nombra.
#
# Es conservador a proposito: si alguna aparicion lleva una marca de identidad
# junto al nombre del afectado, el numero es suyo y no se toca.

# Rotulos de registro profesional. Un paciente no tiene registro medico: el
# numero que lo lleva es de quien firma, aunque coincida con su cedula.
_ROTULO_PROFESIONAL = (
    r"\bregistro\s+medico\b|\breg\.?\s*medico\b|\br\.?\s?m\b\.?"
    r"|\btarjeta\s+profesional\b|\bt\.?\s?p\b\.?")
# Rotulos de numeros que no identifican a nadie.
_ROTULO_AJENO = (
    r"\bsiniestros?\b|\bradicad[oa]s?\b|\bradicacion\b|\brad\b\.?|\bfolios?\b"
    r"|\bexpedientes?\b|\bfacturas?\b|\bcodigo\s+de\s+barras\b|\bconsecutivo\b"
    r"|\bpolizas?\b")
# Rotulos de identidad.
_ROTULO_IDENTIDAD = (
    r"\bc\.?\s?c\b\.?|\bcedula\b|\bidentificad[oa]s?\b|\bidentificacion\b"
    r"|\bdocumento\b|\bnit\b|\bn\.\s?i\.\s?t\b\.?|\bt\.?\s?i\b\.?|\bc\.\s?e\b\.?"
    r"|\bpasaporte\b|\bnuip\b")
_ROTULOS = re.compile(
    rf"(?P<prof>{_ROTULO_PROFESIONAL})|(?P<ajeno>{_ROTULO_AJENO})"
    rf"|(?P<ident>{_ROTULO_IDENTIDAD})")
# Roles de OTRA persona que se nombra con su documento junto al del caso.
_ROL_DE_OTRO = re.compile(
    r"\b(?:demandante|apoderad[oa]|medic[oa]|doctor[a]?|dra?\b\.?|profesional"
    r"|especialista|abogad[oa]|psiquiatra|psicolog[oa]|convocante"
    # Los firmantes de un contrato o de un acta: quien supervisa, quien ordena
    # el gasto, quien contrata o da fe. Su cedula va en el mismo formulario
    # que la del contratista y no es la suya.
    r"|supervisor[a]?|ordenador[a]?|interventor[a]?|contratante|testigo)\b")
# Hasta donde un rotulo nombra al numero que lo sigue.
ALCANCE_DEL_ROTULO = 40
# Ventana en la que se busca el nombre de la persona antes del numero.
VENTANA_DEL_TITULAR = 120
_CONECTORES_TRAS_EL_NUMERO = frozenset("de del a nombre y".split())


def _texto_para_roles(texto: str) -> str:
    """Plegado sin tildes ni mayusculas, pero SIN fundir las cifras vecinas."""
    separado = _MINUSCULA_MAYUSCULA.sub(" ", str(texto or ""))
    # Una firma pegada ("CASTAÑOC.C. 60.335.252") lleva el rotulo de la cedula
    # aunque el texto extraido no deje espacio delante.
    separado = _CC_PEGADO.sub(" ", separado)
    return _normaliza(separado)


def _patron_del_numero(numero: str) -> Optional["re.Pattern[str]"]:
    digitos = _digitos_ascii(numero).lstrip("0")
    if len(digitos) < 5:
        return None
    cuerpo = r"[.\s,'\-]?".join(digitos)
    return re.compile(rf"(?<![\d])0*{cuerpo}(?![\d])")


def _palabras_del_titular(nombre: Any) -> List[str]:
    return [p for p in re.findall(r"[a-z]+", _normaliza(nombre))
            if len(p) > 2 and p not in _CONECTORES_DE_NOMBRE]


def _nombra_al_titular(tramo: str, palabras: List[str]) -> bool:
    """
    Si el tramo nombra a esa persona y no a un pariente.

    Con tres o mas palabras se exigen todas menos una: dos hermanos comparten
    los dos apellidos, y "Karen Bravo Martinez" no es "Leimer Miguel Bravo
    Martinez".
    """
    if not palabras:
        return False
    presentes = set(re.findall(r"[a-z]+", tramo))
    exigidas = max(min(2, len(palabras)), len(palabras) - 1)
    return sum(1 for p in palabras if p in presentes) >= exigidas


# Cuanto puede haber entre el final del nombre y el rotulo del documento:
# ", mayor de edad, " o ", vecino de Neiva, ".
ENTRE_NOMBRE_Y_ROTULO = 45


def _titular_delante(antes: str, ini_rotulo: int, palabras: List[str]) -> bool:
    """
    Si el nombre va justo antes del rotulo: "JUAN PEREZ, identificado con CC".

    Entre el nombre y el rotulo no puede haber una razon social ni el rol de
    otra persona: en "trabajador X empresa Y SAS NIT ..." el NIT es de Y.
    """
    if not palabras or ini_rotulo < 0:
        return False
    tramo = antes[max(0, ini_rotulo - ENTRE_NOMBRE_Y_ROTULO - 60): ini_rotulo]
    tokens = re.findall(r"[a-z]+(?:\.[a-z]+)*\.?", tramo)
    suyas = set(palabras)
    ultimo = max((i for i, t in enumerate(tokens) if t.strip(".") in suyas),
                 default=-1)
    if ultimo < 0:
        return False
    # El nombre se lee seguido, hacia atras desde su ultima palabra: en "Yo,
    # Karen Margarita Bravo Martinez" los apellidos que comparte con su hermano
    # no lo nombran a el.
    bloque = []
    for t in reversed(tokens[:ultimo + 1]):
        limpio = t.strip(".")
        if limpio in suyas or limpio in _CONECTORES_DE_NOMBRE:
            bloque.append(limpio)
        else:
            break
    if not _nombra_al_titular(" ".join(bloque), palabras):
        return False
    resto = tokens[ultimo + 1:]
    if len(" ".join(resto)) > ENTRE_NOMBRE_Y_ROTULO:
        return False
    return not any(parece_razon_social(t) or _ROL_DE_OTRO.fullmatch(t)
                   for t in resto)


def _rotulo_cercano(antes: str):
    """(clase, inicio) del rotulo mas cercano que nombra al numero, o ('', -1)."""
    ultimo = None
    for m in _ROTULOS.finditer(antes):
        ultimo = m
    if ultimo is None:
        return "", -1
    entre = antes[ultimo.end():]
    if len(entre) > ALCANCE_DEL_ROTULO or re.search(r"\d{3,}", entre):
        return "", -1
    # El inicio de la frase de identidad: en "identificado con la cedula No."
    # el nombre va antes de "identificado", no antes de "cedula".
    inicio = ultimo.start()
    if ultimo.lastgroup == "ident":
        for previo in _ROTULOS.finditer(antes[max(0, inicio - 40): inicio]):
            if previo.lastgroup == "ident":
                inicio = max(0, inicio - 40) + previo.start()
                break
    return ultimo.lastgroup, inicio


def _titular_detras(despues: str, palabras: List[str]) -> bool:
    """Si el nombre sigue al numero sin nada en medio mas que un conector."""
    tokens = re.findall(r"[a-z]+", despues[:60])
    while tokens and (tokens[0] in _CONECTORES_TRAS_EL_NUMERO or len(tokens[0]) == 1):
        tokens.pop(0)
    # El nombre tiene que empezar ahi mismo: en "(NIT ...) DEMANDADO JUAN
    # PEREZ" lo que sigue al numero es el rotulo de otra parte.
    if not tokens or tokens[0] not in palabras:
        return False
    return _nombra_al_titular(" ".join(tokens[:len(palabras) + 1]), palabras)


@dataclass
class Aparicion:
    """Como aparece un numero en un punto del texto."""

    clase: str          # prof | ajeno | ident | ""
    del_titular: bool   # con rotulo de identidad junto al nombre dado
    de_otro: bool       # con rotulo de identidad tras el rol de otra persona
    en_tira: bool       # sin rotulo, entre otras cifras (codigo de barras, OCR)


def apariciones_del_numero(numero: Any, nombre: Any, texto: str) -> List[Aparicion]:
    """Cada aparicion del numero en el texto, con lo que dice de el su entorno."""
    patron = _patron_del_numero(str(numero or ""))
    if patron is None or not texto:
        return []
    plegado = texto if texto.islower() else _texto_para_roles(texto)
    palabras = _palabras_del_titular(nombre)
    salida: List[Aparicion] = []
    for m in patron.finditer(plegado):
        antes = plegado[max(0, m.start() - VENTANA_DEL_TITULAR): m.start()]
        despues = plegado[m.end(): m.end() + 60]
        clase, ini_rotulo = _rotulo_cercano(antes)
        roles = list(_ROL_DE_OTRO.finditer(antes))
        del_titular = clase == "ident" and (
            _titular_delante(antes, ini_rotulo, palabras)
            or _titular_detras(despues, palabras))
        de_otro = (clase == "ident" and bool(roles) and not del_titular)
        vecinas = re.findall(r"(?<![\d])\d{2,}(?![\d])",
                             antes[-30:] + " " + despues[:30])
        en_tira = clase == "" and len(vecinas) >= 2
        salida.append(Aparicion(clase, del_titular, de_otro, en_tira))
    return salida


def motivo_de_rol_ajeno(numero: Any, nombre: Any, texto: str) -> tuple:
    """
    (codigo, detalle) si el texto presenta el numero como algo que no es el
    documento de esa persona; ('', '') si es suyo o no se puede afirmar.
    """
    apariciones = apariciones_del_numero(numero, nombre, texto)
    if not apariciones:
        return "", ""
    if any(a.clase == "prof" for a in apariciones):
        return ("DOCUMENTO_DE_UN_PROFESIONAL",
                "el numero aparece como registro medico o tarjeta profesional: "
                "es de quien firma el documento, no de la persona del caso")
    if any(a.del_titular for a in apariciones):
        return "", ""
    if all(a.clase == "ajeno" for a in apariciones):
        return ("DOCUMENTO_CON_ROTULO_AJENO",
                "el numero solo aparece rotulado como siniestro, radicado, folio, "
                "expediente, factura, poliza o consecutivo: no identifica a nadie")
    con_identidad = [a for a in apariciones if a.clase == "ident"]
    if con_identidad and all(a.de_otro for a in con_identidad):
        return ("DOCUMENTO_DE_OTRA_PERSONA_NOMBRADA",
                "el numero aparece como documento de otra persona nombrada con "
                "otro rol (demandante, apoderado, medico...)")
    if all(a.en_tira or a.clase == "ajeno" for a in apariciones) and any(
            a.en_tira for a in apariciones):
        return ("DOCUMENTO_EN_UNA_TIRA_DE_CIFRAS",
                "el numero solo aparece sin rotulo entre otras cifras (codigo de "
                "barras o lectura del OCR), no como documento de nadie")
    return "", ""


def documento_junto_al_nombre(numero: Any, nombre: Any, texto: str) -> bool:
    """Si alguna aparicion del numero lleva rotulo de identidad junto a ese nombre."""
    return any(a.del_titular for a in apariciones_del_numero(numero, nombre, texto))


# Lo que puede ir entre un nombre y su numero sin separarlos: "c.c.", "No.".
_ENTRE_NOMBRE_Y_NUMERO = frozenset("""
c cc c.c ce t ti nit no nro num numero n cedula ciudadania documento de del
identificado identificada con la el
""".split())


_CIFRA_CON_SEPARADORES = re.compile(r"(?<![\d.])\d(?:[\d.]|(?<=\d) (?=\d)){4,16}\d(?![\d])")


def documento_del_que_es_un_recorte(numero: Any, nombre: Any, texto: str) -> str:
    """
    El documento del que `numero` es un recorte (uno o dos digitos menos al
    principio o al final), si el texto lo escribe rotulado o pegado al nombre.

    En el universo el agente tomo "6.769.353" del OCR de la foto de la cedula
    cuando el cuerpo decia dos veces "Cc.16769353" junto al mismo nombre: la
    lectura de la imagen perdio el primer digito.
    """
    corto = re.sub(r"\D", "", str(numero or ""))
    if len(corto) < 5 or not texto or not nombre:
        return ""
    vistos = set()
    for m in _CIFRA_CON_SEPARADORES.finditer(texto):
        largo = re.sub(r"\D", "", m.group(0))
        if (largo in vistos or not 1 <= len(largo) - len(corto) <= 2
                or not (largo.startswith(corto) or largo.endswith(corto))):
            continue
        vistos.add(largo)
        if (documento_junto_al_nombre(largo, nombre, texto)
                or numero_pegado_al_nombre(largo, nombre, texto)):
            return largo
    return ""


# Lo que junto a un nombre no es su documento: un celular o un fijo de diez
# digitos y una fecha o un periodo ("202301", "20260115").
_NO_ES_UN_DOCUMENTO = re.compile(r"^(?:3\d{9}|60\d{8}|(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:\d{2})?)$")


def documento_rotulado_de_la_persona(numero: Any, nombre: Any, texto: str) -> str:
    """
    Otro documento que el texto si escribe junto a esa persona, cuando `numero`
    no aparece junto a ella en ninguna parte. Cadena vacia si no lo hay.

    En el universo el agente tomo de un FURAT el documento del "Responsable" que
    firma, y de una planilla el de otra fila, mientras el nombre del archivo y
    el cuerpo traian el de la persona con su nombre.
    """
    propio = re.sub(r"\D", "", str(numero or ""))
    if len(propio) < 5 or not texto or not nombre:
        return ""
    if (documento_junto_al_nombre(propio, nombre, texto)
            or numero_pegado_al_nombre(propio, nombre, texto)):
        return ""
    vistos = {propio}
    for m in _CIFRA_CON_SEPARADORES.finditer(texto):
        otro = re.sub(r"\D", "", m.group(0))
        # Un trozo del propio numero (el OCR lo partio) no es otro documento.
        if (otro in vistos or not 5 <= len(otro) <= 11 or _NO_ES_UN_DOCUMENTO.match(otro)
                or otro in propio or propio in otro):
            continue
        vistos.add(otro)
        if (documento_junto_al_nombre(otro, nombre, texto)
                or numero_pegado_al_nombre(otro, nombre, texto)):
            return otro
    return ""


def numero_pegado_al_nombre(numero: Any, nombre: Any, texto: str) -> bool:
    """
    Si el texto escribe el numero pegado al nombre, con o sin rotulo:
    "1073.441067 johan mauricio guerra", "Ricardo Cifuentes CC 85613077".

    Es prueba de que el numero esta en las fuentes junto a esa persona; no dice
    que tipo de documento es.
    """
    patron = _patron_del_numero(str(numero or ""))
    palabras = _palabras_del_titular(nombre)
    if patron is None or not texto or len(palabras) < 2:
        return False
    plegado = texto if texto.islower() else _texto_para_roles(texto)
    for m in patron.finditer(plegado):
        if _titular_detras(plegado[m.end(): m.end() + 60], palabras):
            return True
        tokens = re.findall(r"[a-z]+", plegado[max(0, m.start() - 90): m.start()])
        while tokens and (tokens[-1] in _ENTRE_NOMBRE_Y_NUMERO or len(tokens[-1]) == 1):
            tokens.pop()
        bloque: List[str] = []
        for t in reversed(tokens):
            if t in palabras or t in _CONECTORES_DE_NOMBRE:
                bloque.insert(0, t)
            else:
                break
        if bloque and _nombra_al_titular(" ".join(bloque), palabras):
            return True
    return False


_ROTULO_DE_CEDULA = frozenset(
    "c cc cedula ce ti documento identificado identificada ciudadania".split())
_ANTES_DEL_NOMBRE = frozenset(
    "yo mi su el la los las sr sra senor senora don dona".split())


def titular_rotulado_de_otro(numero: Any, nombre: Any, texto: str) -> str:
    """
    El nombre de OTRA persona que el texto escribe con ese numero como su
    cedula ("Ricardo Cifuentes Ospina CC 85613077"), cuando en ningun sitio lo
    escribe junto al nombre dado. Cadena vacia si no hay o no se puede afirmar.
    """
    patron = _patron_del_numero(str(numero or ""))
    if patron is None or not texto or not nombre:
        return ""
    plegado = texto if texto.islower() else _texto_para_roles(texto)
    if numero_pegado_al_nombre(numero, nombre, plegado):
        return ""
    for m in patron.finditer(plegado):
        linea = plegado[max(0, m.start() - 120): m.start()].split("\n")[-1]
        tokens = re.findall(r"[a-z]+", linea)
        rotulado = False
        while tokens and (tokens[-1] in _ENTRE_NOMBRE_Y_NUMERO or len(tokens[-1]) == 1):
            rotulado = rotulado or tokens[-1] in _ROTULO_DE_CEDULA
            tokens.pop()
        if not rotulado:
            continue
        # Nombres de 2 a 5 palabras (sin contar "de", "la") que acaban justo
        # antes del rotulo. Si alguno es la persona dada, el numero es suyo: un
        # corte mas corto ("elvira torres de la hoz") pierde el primer nombre.
        candidatos: List[str] = []
        for largo in range(min(8, len(tokens)), 1, -1):
            cola = tokens[-largo:]
            while cola and cola[0] in _ANTES_DEL_NOMBRE:
                cola = cola[1:]
            utiles = [t for t in cola if t not in _CONECTORES_DE_NOMBRE]
            if (len(utiles) < 2 or len(utiles) > 5
                    or cola[0] in _CONECTORES_DE_NOMBRE):
                continue
            candidato = " ".join(cola)
            if candidato in candidatos or (
                    motivo_por_el_que_no_es_un_nombre(candidato)
                    or set(cola) & _PALABRAS_DEL_TRAMITE or _parece_entidad(candidato)):
                continue
            if (es_el_mismo_titular(candidato, str(nombre))
                    or es_la_misma_persona(_normaliza(candidato), _normaliza(str(nombre)))):
                return ""
            candidatos.append(candidato)
        if candidatos:
            return candidatos[0]
    return ""


# ── El papel con que el texto presenta al afectado ──
#
# Las reglas de arriba miran el ROTULO del numero. Estas miran a la PERSONA:
# el texto puede dar su nombre y su cedula juntos, con el rotulo correcto, y
# aun asi presentarla como quien firma por otro —el representante legal de la
# empresa, el medico que atendio, la contratante del certificado—. Esa persona
# existe y su cedula es suya, pero no es la del caso.

def ocurrencias_del_nombre(nombre: Any, texto: str) -> List[tuple]:
    """
    (inicio, fin) de cada sitio donde el texto plegado nombra a esa persona.

    Admite el nombre con un apellido de menos o en otro orden ("RAUL MIGUEL
    GARCIA" por "Raul Miguel Garcia Rodriguez"), con el mismo criterio que
    `_nombra_al_titular`.
    """
    palabras = _palabras_del_titular(nombre)
    if len(palabras) < 2 or not texto:
        return []
    suyas = set(palabras)
    tokens = list(re.finditer(r"[a-z]+", texto))
    largo = len(palabras) + 1
    salida: List[tuple] = []
    i = 0
    while i < len(tokens):
        if tokens[i].group() not in suyas:
            i += 1
            continue
        tramo = tokens[i:i + largo]
        utiles = [t for t in tramo
                  if t.group() in suyas or t.group() in _CONECTORES_DE_NOMBRE]
        # El nombre va seguido: se corta en la primera palabra ajena.
        seguidos = []
        for t in tramo:
            if t.group() in suyas or t.group() in _CONECTORES_DE_NOMBRE:
                seguidos.append(t)
            else:
                break
        if utiles and _nombra_al_titular(" ".join(t.group() for t in seguidos),
                                         palabras):
            ultimo = max(j for j, t in enumerate(seguidos) if t.group() in suyas)
            salida.append((seguidos[0].start(), seguidos[ultimo].end()))
            i += ultimo + 1
            continue
        i += 1
    return salida


# Quien actua por una organizacion. "Representante" a secas tambien: el
# certificado de camara de comercio escribe "Representante RUTH ... C.C. ...".
_ROL_DE_REPRESENTANTE = re.compile(
    r"\brepresentante\b|\bgerente\b|\ben\s+representacion\s+de\b"
    r"|\bactuando\s+(?:como|en\s+calidad\s+de)\s+(?:representante|gerente"
    r"|apoderad[oa])")
# Lo que puede separar el nombre de su cedula sin que la frase cambie de
# sujeto: ", identificado con cedula de ciudadania No. 1.038.545.143,".
_HASTA_LA_CEDULA = re.compile(
    r"^[\s,;:()\-]*(?:(?:identificad[oa]|con|la|el|de|ciudadania|cedula|c\.?\s?c"
    r"|no|n|numero|documento|nit)[\s.,;:()\-]*){0,8}[\d][\d.\s\-]{4,14}\d")


def presentado_como_representante(nombre: Any, texto: str) -> bool:
    """
    Si el texto presenta a esa persona como representante, gerente o quien
    actua en nombre de una organizacion.

    Mira lo que va pegado al nombre: el cargo justo delante ("GERENTE JOHANNS
    ..."), justo detras ("JOHANNS VALDES EUSE Gerente"), o detras de su cedula
    ("Yo, X, identificado con CC N, actuando en calidad de Representante
    Legal de ..."). Una ventana amplia alrededor no basta: en un FURAT el
    representante legal del empleador va unas lineas antes de los datos del
    trabajador.
    """
    for ini, fin in ocurrencias_del_nombre(nombre, texto):
        # Pegado delante: solo signos o "legal" entre el cargo y el nombre.
        if re.search(r"(?:representante(?:\s+legal)?|gerente(?:\s+general)?)"
                     r"[\s,:;()\-]*$", texto[max(0, ini - 30): ini]):
            return True
        despues = texto[fin: fin + 120]
        if _ROL_DE_REPRESENTANTE.match(re.sub(r"^[\s,;:()\-]+", "", despues[:30])):
            return True
        cedula = _HASTA_LA_CEDULA.match(despues)
        if cedula and _ROL_DE_REPRESENTANTE.search(
                despues[cedula.end(): cedula.end() + 60]):
            return True
    return False


# El nombre seguido de su cedula y de un registro profesional: es la firma de
# quien atendio ("Daniel ... CC No. 1032502331 / RM 1032502331").
_FIRMA_PROFESIONAL_DETRAS = re.compile(
    r"^[\s,;:|/()\-]*(?:(?:c\.?\s?c|cedula|identificacion|documento|no|n)"
    r"[\s.:,;|/()\-]*){0,4}\d[\d.\s]{4,14}\d[\s,;:|/()\-]*"
    r"(?:r\.?\s?m\b|registro\s+(?:medico|profesional)|reg\.?\s+(?:medico|profesional)"
    r"|tarjeta\s+profesional|t\.?\s?p\b)")
# Rotulos que ponen delante del nombre a quien firma o imprime un documento
# clinico, o a quien contrata. Lo que sigue a cualquiera de ellos es otra
# persona que la del caso.
_ROTULO_DE_OTRO_DELANTE = re.compile(
    r"(?:impresion\s+realizada\s+por|firmado\s+(?:electronicamente\s+)?por"
    r"|elaborado\s+por|medico\s+tratante|nombre\s+del\s+profesional"
    r"|informacion\s+del\s+(?:contratante|empleador)|datos\s+del\s+(?:contratante"
    r"|empleador|jefe)|contratante|empleador|supervisor[a]?|ordenador[a]?\s+del\s+gasto"
    r"|jefe\s+inmediato)\b[^a-z0-9]{0,40}(?:(?:nombres?|y|o|razon|social|apellidos?)"
    r"[^a-z0-9]{0,40}){0,5}$")


def motivo_de_nombre_con_otro_papel(nombre: Any, texto: str,
                                    documento: Any = "") -> tuple:
    """
    (codigo, detalle) si el texto presenta al afectado como otra persona del
    tramite: el profesional que firma o la parte que contrata. ('', '') si no.

    La firma profesional cuenta solo si el numero que la acompaña es el
    documento que se va a enviar: en el PDF de una historia clinica las
    columnas se mezclan, y el nombre del paciente puede quedar justo delante
    de la cedula y el registro del medico.
    """
    ocurrencias = ocurrencias_del_nombre(nombre, texto)
    if not ocurrencias:
        return "", ""
    doc = _digitos_ascii(documento)

    def firma_con_su_documento(fin: int) -> bool:
        m = _FIRMA_PROFESIONAL_DETRAS.match(texto[fin: fin + 70])
        return bool(m and doc and doc in re.sub(r"\D", "", m.group(0)))

    if any(firma_con_su_documento(fin) for _, fin in ocurrencias):
        return ("NOMBRE_DE_UN_PROFESIONAL",
                "esa persona firma con su registro medico o tarjeta profesional: "
                "es quien atendio, no la persona del caso")
    if all(_ROTULO_DE_OTRO_DELANTE.search(texto[max(0, ini - 90): ini])
           for ini, _ in ocurrencias):
        return ("NOMBRE_CON_ROL_AJENO",
                "el texto solo nombra a esa persona como contratante, empleador, "
                "supervisor o quien firma el documento: no es la persona del caso")
    return "", ""


# Una persona presentada por su papel en el tramite: "el funcionario IJ.
# RODRIGUEZ VALBUENA JOHAN JAVIER", "la trabajadora ANA LOPEZ PEREZ". Se lee
# sobre el texto con mayusculas, que es lo que distingue el nombre de la frase.
_SUJETO_POR_PAPEL = re.compile(
    r"(?i:\b(?:el|la|al|del|nuestr[oa]|su)\s+(?:funcionari[oa]|trabajador[a]?"
    r"|colaborador[a]?|emplead[oa]|afiliad[oa]|asegurad[oa]|paciente"
    r"|servidor[a]?\s+public[oa]))[\s,:]+(?:[A-ZÁÉÍÓÚÑ]{1,3}\.\s*)?"
    r"((?:[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñü'’-]+\s+(?:(?:de|del|la|las|los|DE|DEL"
    r"|LA|LOS)\s+)?){1,5}[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñü'’-]+)")


def otra_persona_como_sujeto(nombre_afectado: Any, principal: str) -> str:
    """
    El nombre de otra persona que el correo presenta como sujeto del tramite,
    o cadena vacia.

    Solo cuenta un nombre completo —tres palabras propias o mas— que no sea el
    del afectado: "el trabajador Juan" puede ser el mismo afectado nombrado en
    corto.
    """
    if not principal or not nombre_afectado:
        return ""
    for m in _SUJETO_POR_PAPEL.finditer(principal):
        candidato = " ".join(m.group(1).split())
        propias = [p for p in _palabras(candidato) if p not in _CONECTORES_DE_NOMBRE]
        if len(propias) < PALABRAS_DE_UN_NOMBRE_COMPLETO:
            continue
        if motivo_por_el_que_no_es_un_nombre(candidato) or _parece_entidad(candidato):
            continue
        if set(propias) & _PALABRAS_DEL_TRAMITE:
            continue
        if (es_el_mismo_titular(candidato, nombre_afectado)
                or es_la_misma_persona(_normaliza(candidato),
                                       _normaliza(nombre_afectado))
                or _nombra_al_titular(" ".join(propias),
                                      _palabras_del_titular(nombre_afectado))):
            continue
        return candidato
    return ""


# ── Varias personas con documento en un mismo correo ──

# Numero con forma de documento en el cuerpo: seis a diez cifras, con puntos.
_NUMERO_DE_PERSONA = re.compile(r"(?<![A-Za-z0-9])\d[\d.]{4,12}\d(?![\d])")
# Lo que va entre el nombre y su numero en una lista: "c.c.", "CC No.", ":".
_ROTULO_ENTRE_NOMBRE_Y_NUMERO = re.compile(
    r"(?i)(?:[\s,;:\-–]|\b(?:identificad[oa]|con|c\.?\s?c\.?|cc|c[eé]dula"
    r"|ciudadan[ií]a|de|no\.?|n[°º]\.?|n[uú]mero|documento|t\.?\s?i\.?)(?=[\s.,;:\-]|$)\.?)*$")
_NOMBRE_AL_FINAL = re.compile(
    r"((?:[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñü'’-]+\s+(?:(?:de|del|la|las|los|DE|DEL"
    r"|LA|LOS)\s+)?){1,6}[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñü'’-]+)$")
# El documento en el nombre de un adjunto: "CC_45459599_...pdf".
_DOCUMENTO_EN_ADJUNTO = re.compile(
    r"(?i)(?<![a-z])(?:cc|c\.c\.?|ti|ce|cedula)[\s_.\-]*(\d{6,10})(?!\d)")


def personas_con_documento(texto: str) -> List[tuple]:
    """
    Pares (nombre, documento) escritos uno junto al otro en el texto: "LUIS
    PEREZ GOMEZ 13906826", "1. Raul Restrepo Valencia c.c. 6.462574".
    """
    pares: List[tuple] = []
    for m in _NUMERO_DE_PERSONA.finditer(texto or ""):
        digitos = re.sub(r"\D", "", m.group(0))
        if not 6 <= len(digitos) <= 10 or (len(digitos) == 10 and digitos[0] == "3"):
            continue
        antes = texto[max(0, m.start() - 90): m.start()]
        antes = antes.split("\n")[-1]
        antes = re.split(r"\d", antes)[-1]
        rotulo = _ROTULO_ENTRE_NOMBRE_Y_NUMERO.search(antes)
        if rotulo:
            antes = antes[: rotulo.start()]
        nombre = _NOMBRE_AL_FINAL.search(antes.rstrip())
        if not nombre:
            continue
        candidato = " ".join(nombre.group(1).split())
        propias = [p for p in _palabras(candidato) if p not in _CONECTORES_DE_NOMBRE]
        if (len(propias) < 2 or set(propias) & _PALABRAS_DEL_TRAMITE
                or motivo_por_el_que_no_es_un_nombre(candidato)
                or _parece_entidad(candidato)):
            continue
        pares.append((candidato, digitos))
    return pares


def _mismo_documento(uno: str, otro: str) -> bool:
    """El NIT de una persona natural es su cedula mas el digito de verificacion."""
    return uno == otro or uno.startswith(otro) and len(uno) == len(otro) + 1 \
        or otro.startswith(uno) and len(otro) == len(uno) + 1


def motivo_de_varios_afectados(principal: str, nombres_adjuntos, nombre_afectado: Any,
                               documento_afectado: str, nombre_remitente: Any) -> str:
    """
    Por que el correo trata de varias personas, o cadena vacia.

    Una lista de trabajadores con su cedula, la misma persona escrita con dos
    cedulas distintas o adjuntos rotulados con documentos de personas
    distintas: radicar a la primera deja sin tramite a las demas, o a la
    persona con un documento que no se sabe si es el suyo. Exige que el
    afectado elegido sea una de ellas.
    """
    doc = _digitos_ascii(documento_afectado)

    def es_el_afectado(nombre: str, numero: str) -> bool:
        return bool((doc and _mismo_documento(numero, doc)) or (
            nombre_afectado and (es_el_mismo_titular(nombre, nombre_afectado)
                                 or es_la_misma_persona(_normaliza(nombre),
                                                        _normaliza(nombre_afectado)))))

    def es_quien_escribe(nombre: str) -> bool:
        return bool(nombre_remitente) and (
            es_el_mismo_titular(nombre, nombre_remitente)
            or es_la_misma_persona(_normaliza(nombre), _normaliza(nombre_remitente)))

    pares = [p for p in personas_con_documento(principal)
             if not es_quien_escribe(p[0]) or es_el_afectado(*p)]
    if any(es_el_afectado(*p) for p in pares):
        # Agrupa por persona: dos nombres que son la misma persona van juntos.
        personas: List[List[tuple]] = []
        for nombre, numero in pares:
            for grupo in personas:
                # El mismo documento es la misma persona aunque el texto la
                # nombre de otra forma ("CLAVE PDF: <cedula>").
                if es_la_misma_persona(_normaliza(nombre), _normaliza(grupo[0][0])) \
                        or any(_mismo_documento(numero, n) for _, n in grupo):
                    grupo.append((nombre, numero))
                    break
            else:
                personas.append([(nombre, numero)])
        for grupo in personas:
            numeros = []
            for _, n in grupo:
                if not any(_mismo_documento(n, v) for v in numeros):
                    numeros.append(n)
            if len(numeros) > 1 and es_el_afectado(*grupo[0]):
                return (f"el correo escribe a '{grupo[0][0]}' con {len(numeros)} "
                        f"documentos distintos y no dice cual es el suyo")
        if len(personas) >= 2:
            return (f"el correo relaciona a {len(personas)} personas con su "
                    f"documento: no se sabe a cual corresponde el tramite")

    en_adjuntos = []
    for nombre_adjunto in nombres_adjuntos or ():
        for m in _DOCUMENTO_EN_ADJUNTO.finditer(str(nombre_adjunto)):
            if not any(_mismo_documento(m.group(1), v) for v in en_adjuntos):
                en_adjuntos.append(m.group(1))
    if len(en_adjuntos) >= 2 and doc and any(_mismo_documento(doc, v)
                                             for v in en_adjuntos):
        return (f"los adjuntos van rotulados con {len(en_adjuntos)} documentos "
                f"de personas distintas")
    return ""


# Lo que una empresa pide para si misma, no para un trabajador.
_TRAMITE_DE_LA_EMPRESA = re.compile(
    r"estado\s+de\s+(?:cuenta|cartera)|\bcartera\b|paz\s+y\s+salvo"
    r"|habilitaci[oó]n\s+(?:de(?:l)?\s+)?usuario|usuario\s+(?:y\s+contrase|transaccional"
    r"|de\s+la\s+plataforma)|asesor[ií]a|capacitaci[oó]n|concepto\s+t[eé]cnico"
    r"|profesiograma|copias?\s+de\s+(?:las\s+|los\s+)?(?:afiliaciones|formularios)"
    r"|relaci[oó]n\s+de\s+pagos|devoluci[oó]n\s+de\s+(?:los\s+)?aportes",
    re.IGNORECASE)
# Lo que dice que el tramite es de una persona aunque escriba una empresa.
_TRAMITE_PERSONAL = re.compile(
    r"\bmis?\s+(?:incapacidad|incapacidades|pensi[oó]n|afiliaci[oó]n|calificaci[oó]n"
    r"|accidente|dictamen|historia|reembolso|tratamiento|terapias?|citas?)\b",
    re.IGNORECASE)
_NIT_ROTULADO = re.compile(
    r"(?i)\bn\.?\s?i\.?\s?t\.?\b[\s.:#°º\-]*(?:no\.?\s*)?[a-z]?\s*"
    r"(\d[\d.\s]{6,12}\d(?:\s*-\s*\d)?)(?!\d)")
_FIN_DE_LA_RAZON_SOCIAL = re.compile(
    r"(?i)(?:[\s,;:(\[\-–]|\b(?:identificad[oa]|con|el|la|y)\b)*$")
_RAZON_SOCIAL_AL_FINAL = re.compile(
    r"((?:[A-ZÁÉÍÓÚÑ&][A-Za-zÁÉÍÓÚÑáéíóúñü0-9&.'’-]*\s+(?:(?:de|del|la|las|los|y|e)"
    r"\s+)?){0,8}[A-ZÁÉÍÓÚÑ&][A-Za-zÁÉÍÓÚÑáéíóúñü0-9&.'’-]*)$")
_NIT_DE_POSITIVA = "860011153"


def _razon_social_antes(texto: str, posicion: int) -> str:
    """La razon social escrita justo antes del rotulo NIT, o cadena vacia."""
    antes = texto[max(0, posicion - 160): posicion]
    antes = antes.split("\n\n")[-1]
    antes = " ".join(antes.split())
    antes = _FIN_DE_LA_RAZON_SOCIAL.sub("", antes)
    m = _RAZON_SOCIAL_AL_FINAL.search(antes)
    if not m:
        return ""
    nombre = m.group(1).strip(" .,;:-")
    # El articulo con que la frase la presenta no es parte del nombre.
    nombre = re.sub(r"^(?:El|La|EL|LA)\s+", "", nombre)
    return nombre if _parece_entidad(nombre) else ""


_PALABRAS_GENERICAS_DE_ENTIDAD = frozenset("""
sas s.a.s sa s.a ltda limitada eu e.u esp e.s.p ese e.s.e de del la las los y e
empresa sociedad grupo
""".split())


def _palabras_propias_de_entidad(nombre: str) -> set:
    return {p.replace(".", "") for p in _palabras(nombre)
            if p not in _PALABRAS_GENERICAS_DE_ENTIDAD and len(p.replace(".", "")) > 2}


def empresa_con_nit(fuentes: str, nombre_remitente: Any = "",
                    correo_remitente: str = "", nombre_del_buzon: Any = ""):
    """
    (razon_social, nit) de la organizacion que escribe, identificada en el
    correo con su NIT, o None.

    La organizacion tiene que ser la del remitente: su nombre comparte palabras
    con el del buzon o el de quien escribe, o con el dominio del correo. El NIT
    de la IPS que atendio o de la otra ARL tambien sale en los adjuntos y no es
    de quien pide. Si quedan dos NIT distintos para ella, no se elige.
    """
    if not fuentes:
        return None
    # El buzon se llama como la empresa aunque no escriba su forma juridica
    # ("grupo san jose inmobiliario"); el remitente, solo si es una entidad.
    referencias = {p for p in _palabras_propias_de_entidad(str(nombre_del_buzon or ""))
                   if len(p) >= 4}
    if nombre_remitente and _parece_entidad(nombre_remitente):
        referencias |= _palabras_propias_de_entidad(str(nombre_remitente))
    dominio = (correo_remitente or "").rpartition("@")[2].lower()
    etiqueta = re.sub(r"[^a-z]", "", dominio.split(".")[0]) if dominio else ""
    if etiqueta in _DOMINIOS_PUBLICOS:
        etiqueta = ""

    def es_de_quien_escribe(nombre: str) -> bool:
        propias = _palabras_propias_de_entidad(nombre)
        if propias & referencias:
            return True
        return bool(etiqueta) and any(len(p) >= 5 and p in etiqueta for p in propias)

    from servidor_mcp.herramientas import _digito_verificacion_nit

    halladas: Dict[str, List[str]] = {}
    for m in _NIT_ROTULADO.finditer(fuentes):
        digitos = re.sub(r"\D", "", m.group(1))
        if len(digitos) == 10 and int(digitos[9]) == _digito_verificacion_nit(digitos[:9]):
            base = digitos[:9]
        elif len(digitos) == 9:
            base = digitos
        else:
            continue
        if base[0] not in "89" or base == _NIT_DE_POSITIVA:
            continue
        nombre = _razon_social_antes(fuentes, m.start())
        if nombre and es_de_quien_escribe(nombre):
            halladas.setdefault(base, []).append(nombre)
        elif not nombre and base in halladas:
            halladas[base].append("")
    con_nombre = {b: [n for n in ns if n] for b, ns in halladas.items()}
    con_nombre = {b: ns for b, ns in con_nombre.items() if ns}
    if len(con_nombre) != 1:
        return None
    base, nombres = next(iter(con_nombre.items()))
    nombre = max(nombres, key=len)
    return (nombre.upper() if nombre.islower() else nombre), base


def tramite_de_la_empresa(principal: str) -> bool:
    """Si lo que se pide es un tramite de la empresa y no el de una persona."""
    return bool(principal and _TRAMITE_DE_LA_EMPRESA.search(principal)
                and not _TRAMITE_PERSONAL.search(principal))


# Una hoja de vida no es una PQRD: no se le pide nada a la ARL. Se reconoce por
# el asunto, y solo si el correo no pide ademas un tramite.
_HOJA_DE_VIDA = re.compile(r"(?i)\bhoja\s+de\s+vida\b|\bcurr[ií]cul(?:um|o)\b")
_TRAMITE_PEDIDO = re.compile(
    r"incapacidad|accidente|afiliaci|\bpago|calificaci|autoriza|reembolso|pension"
    r"|dictamen|aportes|siniestro|reclam|derecho\s+de\s+peticion")


# Lo que delata que un nombre es el final del nombre de una entidad: "ESE CENTRO
# DE SALUD CAMILO RUEDA" no es la persona Camilo Rueda.
_MARCAS_DE_ENTIDAD_DELANTE = frozenset("""
ese e.s.e ips eps hospital clinica centro alcaldia municipio gobernacion
fundacion corporacion asociacion cooperativa empresa contraloria personeria
""".split())
_NIT_TRAS_EL_NOMBRE = re.compile(
    r"^[^0-9]{0,25}?\b(?:nit|n\.\s?i\.\s?t\.?)\b[^0-9]{0,12}(\d[\d.\s\-]{7,14}\d)")


def entidad_del_afectado(nombre: Any, texto: str):
    """
    (nombre_de_la_entidad, nit) si el afectado es una entidad identificada con
    su NIT junto al nombre; None si no.

    Vale tanto para un nombre que ya es de entidad ("Contraloria de Palmira")
    como para el que es el final de uno ("CAMILO RUEDA" en "ESE CENTRO DE SALUD
    CAMILO RUEDA NIT ..."): en ese caso se devuelve el nombre entero.
    """
    palabras = _palabras_del_titular(nombre)
    if not palabras or not texto:
        return None
    original = " ".join(str(texto).split())
    plegado = _normaliza(original)
    # `_normaliza` conserva la longitud cuando el texto ya va en una linea: las
    # posiciones del plegado sirven para cortar el original.
    if len(plegado) != len(original):
        original = plegado
    patron = re.compile(
        r"\b" + r"[\s.,]+(?:(?:de|del|la|las|los|y|e)[\s.,]+)?".join(
            re.escape(p) for p in palabras) + r"\b")
    ya_es_entidad = _parece_entidad(nombre)
    for m in patron.finditer(plegado):
        nit = _NIT_TRAS_EL_NOMBRE.match(plegado[m.end(): m.end() + 60])
        if not nit:
            continue
        numero = re.sub(r"\D", "", nit.group(1))
        if not 9 <= len(numero) <= 10 or numero[0] not in "89":
            continue
        if ya_es_entidad:
            return str(nombre), numero
        delante = plegado[max(0, m.start() - 50): m.start()].split()[-5:]
        marcas = [i for i, p in enumerate(delante)
                  if p.strip(".,;:()") in _MARCAS_DE_ENTIDAD_DELANTE
                  or parece_razon_social(p)]
        if marcas:
            inicio_rel = " ".join(delante[marcas[0]:])
            ini = m.start() - len(inicio_rel) - 1
            completo = original[max(0, ini):m.end()].strip(" .,;:-")
            return completo.upper() if completo.islower() else completo, numero
    return None


# Dominios de correo que no son de nadie en particular.
_DOMINIOS_PUBLICOS = frozenset("""
gmail hotmail outlook yahoo live msn icloud aol protonmail positiva correo
""".split())


def entidad_del_dominio(correo: str, texto: str):
    """
    (nombre, nit) de la entidad cuyo nombre ES el dominio del buzon, o None.

    "fac-granjas@esecarmenemiliaospina.gov.co" escribe por la "ESE CARMEN
    EMILIA OSPINA" que el adjunto identifica con su NIT. Exige el nombre
    escrito en el texto, con el NIT detras: el dominio solo no nombra a nadie.
    """
    dominio = (correo or "").rpartition("@")[2].lower()
    etiqueta = dominio.split(".")[0] if dominio else ""
    letras = re.sub(r"[^a-z]", "", etiqueta)
    if len(letras) < 8 or etiqueta in _DOMINIOS_PUBLICOS or not texto:
        return None
    original = " ".join(str(texto).split())
    plegado = _normaliza(original)
    if len(plegado) != len(original):
        original = plegado
    tokens = list(re.finditer(r"[a-z]+", plegado))
    for i in range(len(tokens)):
        junto, mejor = "", None
        # Se queda con el tramo mas largo que siga siendo parte del dominio:
        # "ESE CARMEN EMILIA" tambien lo es, pero le falta "OSPINA".
        for j in range(i, min(i + 8, len(tokens))):
            junto += tokens[j].group()
            if junto not in letras:
                break
            if j == i or len(junto) < max(8, 0.7 * len(letras)):
                continue
            fin = tokens[j].end()
            nit = _NIT_TRAS_EL_NOMBRE.match(plegado[fin: fin + 60])
            if not nit:
                continue
            numero = re.sub(r"\D", "", nit.group(1))
            if 9 <= len(numero) <= 10 and numero[0] in "89":
                mejor = (original[tokens[i].start(): fin], numero)
        if mejor:
            nombre, numero = mejor
            return (nombre.upper() if nombre.islower() else nombre), numero
    return None


def sin_ceros_a_la_izquierda(numero: Any, tipo: Any = None) -> str:
    """
    El documento sin los ceros con que algunos sistemas lo rellenan a ancho fijo.

    "000000019314619" es la cedula 19314619. Ninguna cedula ni NIT empieza por
    cero; el pasaporte es alfanumerico y se deja como esta.
    """
    digitos = _digitos_ascii(numero)
    try:
        codigo = int(tipo)
    except (TypeError, ValueError):
        codigo = None
    if not digitos or codigo in TIPOS_ALFANUMERICOS or not digitos.startswith("0"):
        return digitos
    return digitos.lstrip("0")


def _campos_de_otra_persona(tipo_remitente: Any) -> tuple:
    """
    Lo que se devuelve cuando el afectado elegido es otra persona del tramite.

    Si se habia dicho que quien escribe es el afectado, eso tambien cae: con
    el afectado corregido, el 3 dejaria a dos personas distintas como una.
    """
    campos = ("nombre_afectado", "numero_documento_afectado")
    try:
        es_3 = int(tipo_remitente) == 3
    except (TypeError, ValueError):
        es_3 = False
    return campos + ("tipo_remitente",) if es_3 else campos


def _tipo_que_dice_el_texto(doc: str, fuentes: str, reparos, correcciones) -> None:
    """
    El tipo del documento del afectado cuando no se resolvio, por como el texto
    rotula ese numero. Corrige si el rotulo lo dice; si no, pide el campo.
    """
    from models.enums import CODIGO_TO_TIPO_DOCUMENTO
    from utils.documento_candidatos import (
        extraer_candidatos_documento,
        tipo_pegado_que_manda,
    )

    pegados, cedula = set(), False
    for c in extraer_candidatos_documento(fuentes):
        if c.get("numero") != doc:
            continue
        pegados |= set(c.get("tipos_pegados") or ())
        contexto = _normaliza(c.get("contexto", ""))
        cedula = cedula or bool(c.get("cc_explicita")) or bool(
            re.search(r"\bcedula\b(?!\s+de\s+extranjer)", contexto))
    manda = tipo_pegado_que_manda(pegados)
    campo = "tipo_documento_afectado"
    if manda == "?":
        reparos.append(Reparo(
            campo, "TIPO_DE_DOCUMENTO_EN_DUDA",
            f"el numero {doc} aparece rotulado como {', '.join(sorted(pegados))}: "
            f"no se sabe cual es su tipo"))
        return
    codigo = CODIGO_TO_TIPO_DOCUMENTO.get(manda) if manda else None
    if codigo is None and cedula:
        codigo = int(TipoDocumento.CC)
    if codigo is not None and codigo not in TIPOS_DE_ORGANIZACION:
        reparos.append(Reparo(
            campo, "TIPO_DE_DOCUMENTO_POR_SU_ROTULO",
            f"no se resolvio; el texto rotula {doc} como "
            f"{manda or 'cedula'}", bloqueante=False))
        correcciones[campo] = codigo
        return
    # Sin rotulo en el numero, la cedula es la lectura que el radicador hace
    # por defecto. Solo se sostiene si nada en el correo habla de otro tipo:
    # un permiso, un extranjero o un menor la desmienten, y entonces suponerla
    # es inventarla.
    if _OTRO_TIPO_EN_EL_TEXTO.search(_normaliza(fuentes)):
        reparos.append(Reparo(
            campo, "TIPO_DE_DOCUMENTO_SIN_RESOLVER",
            f"el texto no dice de que tipo es el documento {doc} y habla de "
            f"extranjeros, permisos o menores: enviarlo como cedula seria "
            f"suponerlo"))
        return
    reparos.append(Reparo(
        campo, "TIPO_DE_DOCUMENTO_POR_DEFECTO",
        f"el texto no rotula el documento {doc}; nada en el correo sugiere otro "
        f"tipo que la cedula", bloqueante=False))


_OTRO_TIPO_EN_EL_TEXTO = re.compile(
    r"\bp\.?\s?p\.?\s?t\b|\bp\.?\s?e\.?\s?p\b|permiso\s+(?:por|de)\s+proteccion"
    r"|permiso\s+especial|extranjer|venezolan|pasaporte|tarjeta\s+de\s+identidad"
    r"|menor\s+de\s+edad|registro\s+civil")


def validar(
    hallazgos: Dict[str, Hallazgo],
    catalogos: Optional[Dict[str, Dict[Any, str]]] = None,
    confianza_minima: float = CONFIANZA_PARA_RADICAR,
    motivos_no_resueltos: Optional[Dict[str, str]] = None,
    fuentes: Optional[str] = None,
    principal: Optional[str] = None,
    nombres_adjuntos: Optional[List[str]] = None,
) -> Dictamen:
    """
    Aplica las reglas de coherencia y devuelve el dictamen.

    `fuentes` es el texto del correo (asunto, cuerpo, hilo, adjuntos y lo que
    devolvio el registro de personas). Con el, todo valor del agente tiene que
    aparecer en el; sin el, esa comprobacion no se hace.

    `principal` es el asunto y el cuerpo, sin adjuntos: lo que el remitente
    escribio. Ahi se lee si el correo trata de varias personas o de otra
    distinta a la elegida. `nombres_adjuntos` son los nombres de los archivos.
    """
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
        # Vacio no es un codigo: si el campo es obligatorio ya lo rechaza
        # FALTA_CAMPO_OBLIGATORIO, y si no lo es (el tipo de documento de un
        # remitente que la propia compuerta dejo sin documento) se envia vacio.
        if v is None or v == "":
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
    # Solo si el nombre del afectado es un nombre: copiarlo antes de que se
    # revise propaga al remitente el trozo de asunto que se leyo como nombre
    # ("SALVO DE RAMIRO ..."), y si luego se corrige el del afectado, el del
    # remitente se queda con el trozo.
    if (_escribe_el_afectado_desde_otro_buzon(hallazgos)
            and _nombre_sin_reparos(valor_corregido("nombre_afectado"))):
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

    # ── Ni un tratamiento o rol delante, ni palabras del tramite ──
    for campo in ("nombre_afectado", "nombre_remitente"):
        _revisar_rol_delante(campo, valor_corregido(campo), reparos, correcciones)
        _revisar_entidad_con_antepuesto(
            campo, valor_corregido(campo), reparos, correcciones)
        _revisar_palabras_del_tramite(campo, valor_corregido(campo), reparos)

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
    # Ceros de relleno: "000000019314619" es la cedula 19314619. Se quitan antes
    # de medir la longitud, que si no la rechaza por quince digitos.
    sin_ceros: Dict[str, str] = {}
    for campo, numero in (("numero_documento_afectado", doc),
                          ("numero_documento_remitente", doc_rem)):
        limpio = sin_ceros_a_la_izquierda(
            numero, valor(campo.replace("numero_", "tipo_")))
        if limpio and limpio != numero:
            reparos.append(Reparo(
                campo, "DOCUMENTO_CON_CEROS_A_LA_IZQUIERDA",
                f"'{numero}' lleva ceros de relleno; se envia '{limpio}'",
                bloqueante=False,
            ))
            correcciones[campo] = limpio
            sin_ceros[campo] = limpio
    doc = sin_ceros.get("numero_documento_afectado", doc)
    doc_rem = sin_ceros.get("numero_documento_remitente", doc_rem)

    # ── Sin tipo de documento no se inventa la cedula ──
    # El radicador envia CC cuando el tipo falta. Si el texto rotula el numero
    # con otro tipo (PT, CE, TI, PEP) ese es el suyo; si lo da como cedula, se
    # dice; si no dice nada, se pide antes de suponerlo.
    if doc and valor("tipo_documento_afectado") in (None, "") and fuentes:
        _tipo_que_dice_el_texto(doc, fuentes, reparos, correcciones)

    for campo, numero in (("numero_documento_afectado", doc),
                          ("numero_documento_remitente", doc_rem)):
        crudo = sin_ceros.get(campo, valor(campo))
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
            valor_corregido(campo.replace("numero_", "tipo_")),
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

    plegadas = Fuentes(fuentes) if fuentes else None

    # ── Una persona no se identifica con el NIT de una entidad ──
    # El NIT de la alcaldia o de la empresa que escribe aparece en el membrete
    # y termina como "cedula" de la trabajadora. Con nombre de persona y numero
    # con forma de NIT de persona juridica, la identidad no existe. La forma
    # sola no basta: tiene que estar declarado como NIT, por el tipo o por el
    # rotulo con que aparece en el correo.
    def _declarado_nit(numero: str, quien: str) -> bool:
        try:
            es_ni = int(valor(f"tipo_documento_{quien}")) == int(TipoDocumento.NI)
        except (TypeError, ValueError):
            es_ni = False
        return es_ni or bool(plegadas and numero in plegadas.de_nit)

    if (doc and _es_nombre_de_persona(valor_corregido("nombre_afectado"))
            and _es_nit_de_persona_juridica(doc, _digito_verificacion_nit, plegadas)
            and _declarado_nit(doc, "afectado")):
        reparos.append(Reparo(
            "numero_documento_afectado", "NIT_DE_ENTIDAD_EN_PERSONA",
            f"'{doc}' tiene la forma del NIT de una persona juridica y "
            f"'{valor_corregido('nombre_afectado')}' es el nombre de una persona",
        ))
    if (doc_rem and doc_rem != doc
            and _es_nombre_de_persona(valor_corregido("nombre_remitente"))
            and _es_nit_de_persona_juridica(doc_rem, _digito_verificacion_nit, plegadas)
            and _declarado_nit(doc_rem, "remitente")):
        empresa = _empresa_que_escribe(hallazgos.get("nombre_remitente"))
        try:
            es_tercero = int(valor("tipo_remitente")) == TIPO_REMITENTE_TERCERO
        except (TypeError, ValueError):
            es_tercero = False
        if es_tercero and empresa:
            # Una empresa escribe por medio de una persona que firma, y el
            # remitente es un tercero: quien remite es la empresa, con su NIT.
            # Vaciar el NIT dejaba la radicacion a nombre de la auxiliar que
            # firmo y sin documento, con el tipo de remitente diciendo empresa.
            reparos.append(Reparo(
                "nombre_remitente", "NIT_DE_ENTIDAD_EN_PERSONA",
                f"el remitente es un tercero y '{doc_rem}' es el NIT de "
                f"'{empresa}', que escribe por medio de "
                f"'{valor_corregido('nombre_remitente')}': remite la empresa",
                bloqueante=False,
            ))
            correcciones["nombre_remitente"] = empresa
        else:
            # El documento del remitente no es obligatorio: se envia vacio.
            reparos.append(Reparo(
                "numero_documento_remitente", "NIT_DE_ENTIDAD_EN_PERSONA",
                f"'{doc_rem}' tiene la forma del NIT de una persona juridica y "
                f"'{valor_corregido('nombre_remitente')}' es el nombre de una persona",
                bloqueante=False,
            ))
            correcciones["numero_documento_remitente"] = ""
            correcciones["tipo_documento_remitente"] = ""

    # ── El documento es de la persona: no un siniestro ni la firma del medico ──
    # Lo decide el rotulo con que el correo presenta el numero. Se aplica a
    # cualquier origen: el algoritmo elige por cercania al nombre y un
    # siniestro o la cedula del medico que firma pueden ser lo mas cercano.
    if fuentes:
        texto_roles = _texto_para_roles(fuentes)
        if doc:
            codigo, detalle = motivo_de_rol_ajeno(
                doc, valor_corregido("nombre_afectado"), texto_roles)
            if codigo:
                reparos.append(Reparo(
                    "numero_documento_afectado", codigo, f"'{doc}': {detalle}"))
            nombre_del_doc = valor_corregido("nombre_afectado")
            otro = (titular_rotulado_de_otro(doc, nombre_del_doc, texto_roles)
                    if not codigo and _es_nombre_de_persona(nombre_del_doc) else "")
            if otro:
                for campo in ("nombre_afectado", "numero_documento_afectado"):
                    reparos.append(Reparo(
                        campo, "DOCUMENTO_DE_OTRO_NOMBRE",
                        f"el texto escribe '{doc}' como documento de '{otro}', "
                        f"no de '{nombre_del_doc}'"))
            completo = (documento_del_que_es_un_recorte(doc, nombre_del_doc, texto_roles)
                        if not codigo and not otro else "")
            if completo:
                reparos.append(Reparo(
                    "numero_documento_afectado", "DOCUMENTO_RECORTADO",
                    f"'{doc}' es parte de '{completo}', que el texto escribe como "
                    f"documento junto a '{nombre_del_doc}': el OCR de una imagen "
                    f"suele perder o cambiar digitos, y vale lo escrito"))
            h_doc = hallazgos.get("numero_documento_afectado")
            rival = (documento_rotulado_de_la_persona(doc, nombre_del_doc, texto_roles)
                     if (not codigo and not otro and not completo and h_doc is not None
                         and h_doc.metodo == "agente_ia"
                         and _es_nombre_de_persona(nombre_del_doc)) else "")
            if rival:
                reparos.append(Reparo(
                    "numero_documento_afectado", "DOCUMENTO_LEJOS_DEL_AFECTADO",
                    f"'{doc}' no aparece en ninguna parte junto a '{nombre_del_doc}', "
                    f"y '{rival}' si: el documento que se propone es de otro bloque "
                    f"(otra persona del formulario, un responsable, una planilla)"))
        h_rem = hallazgos.get("numero_documento_remitente")
        if (doc_rem and doc_rem != doc and h_rem is not None
                and h_rem.metodo != "remitente_es_el_afectado"
                and "numero_documento_remitente" not in correcciones):
            codigo, detalle = motivo_de_rol_ajeno(
                doc_rem, valor_corregido("nombre_remitente"), texto_roles)
            if codigo:
                # No es obligatorio: se envia vacio.
                reparos.append(Reparo(
                    "numero_documento_remitente", codigo,
                    f"'{doc_rem}': {detalle}", bloqueante=False))
                correcciones["numero_documento_remitente"] = ""
                correcciones["tipo_documento_remitente"] = ""

    # ── La persona del caso, y no otra que el texto presenta con otro papel ──
    # El rotulo del numero puede ser correcto —es la cedula de esa persona— y
    # la persona no ser la del caso: el representante legal que firma por la
    # empresa, el medico que atendio, la contratante del certificado o quien
    # escribe sobre un trabajador. Se devuelven el nombre y el documento juntos,
    # porque el uno sin el otro no se puede corregir.
    nombre_af = valor_corregido("nombre_afectado")
    try:
        tipo_af = int(valor_corregido("tipo_documento_afectado"))
    except (TypeError, ValueError):
        tipo_af = None
    afectado_es_persona = bool(nombre_af) and _es_nombre_de_persona(nombre_af) \
        and tipo_af not in TIPOS_DE_ORGANIZACION
    representante = False
    if fuentes and afectado_es_persona:
        texto_roles = _texto_para_roles(fuentes)
        representante = presentado_como_representante(nombre_af, texto_roles)
        if representante:
            codigo, detalle = ("AFECTADO_ES_EL_REPRESENTANTE",
                               f"el texto presenta a '{nombre_af}' como representante "
                               f"o gerente de una organizacion: firma por ella, y el "
                               f"tramite es de la organizacion o de la persona por la "
                               f"que escribe")
        else:
            codigo, detalle = motivo_de_nombre_con_otro_papel(
                nombre_af, texto_roles, doc)
        if codigo:
            for campo in _campos_de_otra_persona(valor("tipo_remitente")):
                reparos.append(Reparo(campo, codigo, f"'{nombre_af}': {detalle}"))
    if principal and afectado_es_persona:
        escribe_el_afectado = valor("tipo_remitente") == 3 or es_la_misma_persona(
            _normaliza(nombre_af), _normaliza(valor_corregido("nombre_remitente")))
        otro = otra_persona_como_sujeto(nombre_af, principal) \
            if escribe_el_afectado else ""
        if otro:
            for campo in _campos_de_otra_persona(valor("tipo_remitente")):
                reparos.append(Reparo(
                    campo, "EL_CORREO_ES_SOBRE_OTRA_PERSONA",
                    f"'{nombre_af}' es quien escribe, y el correo trata de "
                    f"'{otro}'"))

    # ── Una sola persona afectada ──
    if principal or nombres_adjuntos:
        motivo = motivo_de_varios_afectados(
            principal or "", nombres_adjuntos or [], nombre_af, doc,
            valor_corregido("nombre_remitente"))
        if motivo:
            reparos.append(Reparo("afectado", "VARIOS_AFECTADOS", motivo))

    # ── Una hoja de vida no es una peticion ──
    if principal and _HOJA_DE_VIDA.search(principal.split("\n", 1)[0]) \
            and not _TRAMITE_PEDIDO.search(_normaliza(principal[:800])):
        reparos.append(Reparo(
            "correo", "NO_ES_UNA_PQRD",
            "el correo envia una hoja de vida: no le pide nada a la ARL"))

    # ── El telefono sirve para llamar a alguien ──
    _revisar_telefono(
        valor("telefono_afectado"), {d for d in (doc, doc_rem) if d},
        reparos, correcciones,
    )
    tel = _telefono_en_digitos(valor("telefono_afectado"))
    if (plegadas and tel and "telefono_afectado" not in correcciones
            and tel in plegadas.de_documento and tel not in plegadas.de_telefono):
        reparos.append(Reparo(
            "telefono_afectado", "TELEFONO_ES_UN_DOCUMENTO",
            f"'{valor('telefono_afectado')}' aparece en el correo como numero "
            f"de documento o de radicado, no como telefono",
            bloqueante=False,
        ))
        correcciones["telefono_afectado"] = ""
    # El telefono que en el correo solo sale en el membrete o el pie de una
    # entidad (la clinica, la empresa, el laboratorio) contesta en ella, no en
    # casa del afectado. Vale para el del algoritmo y para el del agente.
    if (fuentes and tel and "telefono_afectado" not in correcciones
            and solo_en_renglones_de_entidad(tel, fuentes)):
        reparos.append(Reparo(
            "telefono_afectado", "TELEFONO_DE_UNA_ENTIDAD",
            f"'{valor('telefono_afectado')}' solo aparece en el membrete o el "
            f"renglon de contacto de una entidad, no en los datos del afectado",
            bloqueante=False,
        ))
        correcciones["telefono_afectado"] = ""

    # ── La direccion es una direccion ──
    _revisar_direccion(valor_corregido("direccion_afectado"), reparos, correcciones)
    direccion = valor_corregido("direccion_afectado")
    if direccion:
        motivo = _motivo_direccion_sin_forma(str(direccion))
        if motivo:
            reparos.append(Reparo(
                "direccion_afectado", "DIRECCION_SIN_FORMA_DE_DIRECCION",
                f"'{str(direccion)[:60]}' {motivo}",
                bloqueante=False,
            ))
            correcciones["direccion_afectado"] = ""

    # ── El municipio es del departamento ──
    _revisar_geografia(valor("departamento_afectado"), valor("municipio_afectado"),
                       reparos, correcciones)

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

    # Quien firma como representante de una organizacion no pasa a ser el
    # afectado por llamarse igual que el: el 2 lo puso quien leyo que escribe
    # la empresa, y es el afectado lo que esta mal.
    if (tipo_rem in (1, 2) and nom_af and nom_rem and misma_persona
            and not documentos_distintos and not representante):
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

    # ── Lo que propuso el agente esta en el correo ──
    # Va al final para juzgar el valor que de verdad se enviaria, ya corregido.
    if plegadas is not None:
        _revisar_sustento(hallazgos, valor_corregido, plegadas, reparos, correcciones)

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
    "VARIOS_AFECTADOS": "DOCUMENTO_AMBIGUO",
    "TIPO_DE_DOCUMENTO_EN_DUDA": "DOCUMENTO_AMBIGUO",
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
