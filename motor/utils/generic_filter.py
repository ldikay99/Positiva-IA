"""
Filtro de nombres e identificaciones genéricas, y extracción de correo real
en correos reenviados internamente por áreas de Positiva o desde buzones
automatizados (notificaciones, no-reply, postmaster, etc.).
"""

import re
from difflib import SequenceMatcher
import unicodedata
from typing import List, Optional, Tuple

# Regex para capturar correos electrónicos en texto plano
_EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Dominios considerados internos de Positiva (nunca deben aparecer como
# remitente final del ciudadano).
_INTERNAL_DOMAINS = (
    "@positiva.gov.co",
    "@positivaseguros.com.co",
)

# Prefijos/locales típicos de buzones automatizados o no humanos.
# Se compara contra la parte local del correo (antes del @).
_NON_HUMAN_LOCAL_PARTS = (
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "notificacion",
    "notificaciones",
    "notification",
    "notifications",
    "postmaster",
    "mailer-daemon",
    "mailerdaemon",
    "mail-delivery",
    "automatic",
    "automatico",
    "automatica",
    "system",
    "sistema",
    "alerts",
    "alertas",
    "newsletter",
    "bounces",
    "correo-notificaciones",
    "correonotificaciones",
)

# Fragmentos de display name que delatan buzones no humanos.
_NON_HUMAN_NAME_PATTERNS = (
    "notificacion",
    "notificaciones",
    "notification",
    "no reply",
    "no-reply",
    "noreply",
    "do not reply",
    "donotreply",
    "correo notificaciones",
    "mail delivery",
    "mailer-daemon",
    "postmaster",
    "automatic reply",
    "respuesta automatica",
    "respuesta automática",
)


def _is_internal_domain(address: str) -> bool:
    addr = (address or "").lower().strip()
    return any(addr.endswith(d) for d in _INTERNAL_DOMAINS)


def es_buzon_de_positiva(address: str) -> bool:
    """True si la dirección pertenece a un dominio de Positiva."""
    return _is_internal_domain(address)


def _is_non_human_local(address: str) -> bool:
    addr = (address or "").lower().strip()
    if "@" not in addr:
        return False
    local = addr.split("@", 1)[0]
    return any(part in local for part in _NON_HUMAN_LOCAL_PARTS)


def _is_non_human_name(name: str) -> bool:
    n = (name or "").lower().strip()
    if not n:
        return False
    return any(p in n for p in _NON_HUMAN_NAME_PATTERNS)


def is_buzon_no_humano(from_name: str, from_address: str) -> bool:
    """
    True si el remitente corresponde a un dominio interno de Positiva,
    a un buzón automatizado/no humano (noreply, postmaster, notificaciones)
    o a un display name que delata un buzón automatizado.

    En estos casos NO se debe usar el From tal cual como remitente; hay
    que buscar el remitente externo en el cuerpo del hilo.
    """
    return (
        _is_internal_domain(from_address)
        or _is_non_human_local(from_address)
        or _is_non_human_name(from_name)
    )


def _es_correo_externo_valido(correo: str) -> bool:
    correo = (correo or "").lower().strip()
    if not correo or "@" not in correo:
        return False
    if _is_internal_domain(correo):
        return False
    if _is_non_human_local(correo):
        return False
    return True


# Prefijos que delatan que el display name es una URL y no un nombre.
_URL_PREFIJOS = ("http://", "https://", "www.")


def es_nombre_persona_valido(nombre: str) -> bool:
    """
    True si 'nombre' parece un nombre real de persona o una razón social.

    Existe para impedir que el display name del encabezado 'De:' termine
    guardado como nombre del remitente cuando en realidad es la dirección de
    correo. Microsoft Graph rellena el campo 'name' con la propia dirección
    cuando el buzón no tiene display name configurado, lo que es habitual en
    buzones corporativos genéricos (recursoshumanos@, contacto@, juridica@).
    """
    n = (nombre or "").strip()
    if len(n) < 3:
        return False
    # Es una dirección de correo, completa o parcial
    if "@" in n or _EMAIL_REGEX.search(n):
        return False
    # Es una URL
    if n.lower().startswith(_URL_PREFIJOS):
        return False
    # Sin una sola letra: números, guiones o símbolos sueltos
    if not any(c.isalpha() for c in n):
        return False
    # Un único token con dígitos es el handle del buzón, no un nombre
    # ("yagopi69", "usuario123"). Las razones sociales con números
    # ("GRUPO 3T SAS") llevan espacios, así que no se descartan.
    if " " not in n and any(c.isdigit() for c in n):
        return False
    return True


# Marcas de razón social: forma jurídica o palabra de entidad. Una empresa
# colombiana casi siempre las lleva escritas en el nombre, y ninguna de ellas es
# apellido ni nombre de pila, así que su presencia distingue una organización de
# una persona sin necesidad de una lista de empresas.
_MARCAS_DE_RAZON_SOCIAL = frozenset("""
sas s.a.s sa s.a ltda limitada eu e.u sca scs bic esp e.s.p ese e.s.e
eps ips arl afp ccf
fundacion corporacion asociacion cooperativa federacion sindicato consorcio
grupo empresa empresas sociedad compania comercializadora distribuidora
constructora constructores inversiones inmobiliaria agropecuaria industrial
industrias servicios soluciones logistica transportes transportadora
municipio alcaldia gobernacion ministerio superintendencia
hospital clinica universidad universitaria colegio instituto institucion
laboratorio banco camara comite consejo
""".split())


def _palabras_del_nombre(nombre: str) -> frozenset:
    """Palabras con peso de un nombre, sin partículas ni restos de OCR."""
    return frozenset(
        p for p in _normaliza(nombre).replace(".", " ").split()
        if len(p) > 2 and p not in _CONECTORES
    )


def es_la_misma_persona(uno: str, otro: str) -> bool:
    """
    True si los dos nombres se refieren a la misma persona.

    Iguales tras normalizar, o uno escrito más corto que el otro: el display
    name del correo puede traer solo el nombre y un apellido, y la firma el
    nombre completo con los dos apellidos. Comparar con igualdad exacta leería
    eso como dos personas distintas.

    No basta compartir una palabra: dos personas del mismo correo pueden
    compartir apellido, así que se exige que TODAS las palabras del más corto
    estén en el más largo.
    """
    a, b = _palabras_del_nombre(uno), _palabras_del_nombre(otro)
    if not a or not b:
        return False
    return a <= b or b <= a


def _distancia_de_edicion(a: str, b: str, tope: int) -> int:
    """Levenshtein que deja de contar al pasar de `tope`."""
    if abs(len(a) - len(b)) > tope:
        return tope + 1
    previa = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        fila = [i]
        for j, cb in enumerate(b, 1):
            fila.append(min(previa[j] + 1, fila[j - 1] + 1,
                            previa[j - 1] + (ca != cb)))
        if min(fila) > tope:
            return tope + 1
        previa = fila
    return previa[-1]


def _palabras_equivalentes(a: str, b: str) -> bool:
    """
    Si dos palabras son la misma con un error de escritura.

    La tolerancia crece con el largo: en una palabra corta, una letra ya cambia
    el nombre.
    """
    if a == b:
        return True
    largo = min(len(a), len(b))
    if largo < 4:
        return False
    return _distancia_de_edicion(a, b, 1 if largo < 8 else 2) <= (1 if largo < 8 else 2)


# Cuanto se tienen que parecer dos grafias completas cuando una sola palabra no
# coincide: dos apellidos iguales con el nombre de pila muy distinto quedan por
# debajo, y son justo la madre y la hija.
PARECIDO_DEL_MISMO_TITULAR = 0.88


def es_el_mismo_titular(uno: str, otro: str) -> bool:
    """
    Si dos nombres escritos para un MISMO documento son la misma persona.

    Solo sirve cuando el documento ya los une: con ese vinculo, "LIZETH" y
    "LISETH", una ñ perdida o los apellidos antepuestos son una forma de
    escribirlo. Para comparar a dos personas sin documento en común se usa
    `es_la_misma_persona`, que no tolera errores: dos hermanos pueden diferir en
    una sola letra.

    Compartir apellidos no alcanza: de cada nombre se exigen todas las palabras
    del más corto, o tres coincidencias.
    """
    if es_la_misma_persona(uno, otro):
        return True
    a, b = sorted((list(_palabras_del_nombre(uno)), list(_palabras_del_nombre(otro))),
                  key=len)
    if len(a) < 2:
        return False
    libres = list(b)
    coinciden = 0
    for palabra in sorted(a, key=len, reverse=True):
        par = next((p for p in libres if _palabras_equivalentes(palabra, p)), None)
        if par is not None:
            libres.remove(par)
            coinciden += 1
    if coinciden == len(a) or coinciden >= 3:
        return True
    parecido = SequenceMatcher(None, _normaliza(uno), _normaliza(otro)).ratio()
    return coinciden >= 2 and parecido >= PARECIDO_DEL_MISMO_TITULAR


def parece_razon_social(nombre: str) -> bool:
    """
    True si el nombre lleva una marca de razón social.

    Sirve para comprobar que el nombre y el tipo de documento hablen de lo
    mismo: 'SIETE M SAS' con un NIT es coherente, 'Otilia Duarte Ocampo' con un
    NIT no. La ausencia de marca no prueba que sea una persona, solo que el
    texto no dice que sea una empresa.

    El '&' cuenta por sí solo: ningún nombre de persona lo lleva, y hay
    comercios que se llaman así de una sola palabra ('MINIPRECIOS&SURTIBABY').
    """
    if "&" in (nombre or ""):
        return True
    palabras = {
        _normaliza(p).strip(".,;:()") for p in (nombre or "").split()
    }
    return bool(palabras & _MARCAS_DE_RAZON_SOCIAL)


# Con lo que se nombra un buzon. `_es_solo_un_rol` no los alcanza porque exige
# que TODAS las palabras sean del vocabulario, y un buzon se llama por su
# funcion mas un apellido cualquiera: "Notificaciones Judiciales",
# "Notificaciones Oriente", "Notificaciones SGD Orfeo". Lo que lo delata es con
# que EMPIEZA.
_ARRANQUE_DE_BUZON = frozenset({
    "asistencia", "soporte", "mesa", "atencion", "contacto", "buzon",
    "correspondencia", "radicacion", "servicioalcliente", "administracion",
    "gestionhumana", "recepcion", "comunicaciones", "informacion", "info",
    "admin", "noreply",
})

# La unica familia que se reconoce por prefijo. Un buzon de notificaciones se
# escribe de todas las formas —"NotificacionesPHP", "Notificacion_Servicios"—
# y ninguna palabra del castellano que empiece asi nombra a una persona. Las
# demas van por igualdad: "informatica" empieza como "info" y si puede ser una
# empresa.
_PREFIJO_DE_BUZON = "notificacion"


def _empieza_como_un_buzon(nombre: str) -> bool:
    """
    Si el nombre arranca con la funcion de un buzon.

    Una razon social queda fuera aunque arranque igual: "Asistencia Medica
    Domiciliaria SAS" es una empresa, y su marca de sociedad lo dice.
    """
    if parece_razon_social(nombre):
        return False
    palabras = _normaliza(nombre).split()
    if not palabras:
        return False
    primera = palabras[0].strip(".,;:_-")
    return primera.startswith(_PREFIJO_DE_BUZON) or primera in _ARRANQUE_DE_BUZON


def _posicion_de_la_marca(palabras: List[str]) -> int:
    """Donde empieza la razón social dentro del nombre, o -1 si no lleva marca."""
    for i, palabra in enumerate(palabras):
        if "&" in palabra or _normaliza(palabra).strip(".,;:()") in _MARCAS_DE_RAZON_SOCIAL:
            return i
    return -1


def _area_antes_de_la_organizacion(nombre: str) -> str:
    """
    El área que abre el nombre de una organización, o cadena vacía.

    La marca de razón social hace pasar por empresa a "Departamento de Medicina
    Laboral EPS Sura": la empresa es lo que va después, y lo de antes solo dice
    qué parte de ella escribe.
    """
    palabras = (nombre or "").split()
    marca = _posicion_de_la_marca(palabras)
    if marca <= 0:
        return ""
    antes = " ".join(palabras[:marca])
    return antes if _es_solo_un_rol(antes) else ""


def organizacion_tras_el_area(nombre: str) -> str:
    """
    "EPS Sura" a partir de "Departamento de Medicina Laboral EPS Sura".

    Cadena vacía si lo que queda no se sostiene como nombre de una organización:
    una sola palabra, o un resto con paréntesis como "ARL (ANEXO 2)".
    """
    if not _area_antes_de_la_organizacion(nombre):
        return ""
    palabras = nombre.split()
    resto = palabras[_posicion_de_la_marca(palabras):]
    if len(resto) < 2 or any(c in " ".join(resto) for c in "()[]:"):
        return ""
    return " ".join(resto)


# "yopal-casanare.gov.co", "mail.uniatlantico.edu.co": el display name de un
# buzon institucional suele llevar el dominio. Se exige un sufijo conocido para
# no confundirlo con una abreviatura con punto ("Ma. Fernanda").
_DOMINIO = re.compile(
    r"[\w-]\.(?:com|co|org|net|edu|gov|mil|info)\b",
    re.IGNORECASE,
)


def motivo_por_el_que_no_identifica_a_nadie(nombre: str) -> str:
    """
    Por que ese texto no nombra a ninguna parte del caso, o cadena vacia.

    Se aplica tambien a una razon social, que el resto de las comprobaciones de
    nombre deja pasar: Positiva es la entidad que RECIBE la PQRD y no puede ser
    ni la afectada ni la remitente, y un area o un cargo no identifican a nadie
    aunque lleven la marca de una organizacion.
    """
    t = " ".join(str(nombre or "").split())
    if not t:
        return ""
    if "positiva" in _normaliza(t):
        return "es la entidad que recibe la PQRD, no una parte del caso"
    if "@" in t:
        return "es una direccion de correo, no el nombre de nadie"
    if _DOMINIO.search(t):
        return "lleva un dominio de correo, que nombra un buzon y no a nadie"
    repetidas = _normaliza(t).split()
    if len(repetidas) > 1 and len(set(repetidas)) == 1:
        # "urgencias urgencias", "notificaciones notificaciones": Outlook arma
        # asi el nombre de un buzon con nombre y apellido iguales.
        return "repite la misma palabra: es el nombre de un buzon, no de nadie"
    if _es_solo_un_rol(t):
        return "es un cargo o un area, y no identifica a ninguna persona"
    if _area_antes_de_la_organizacion(t):
        return "es un area de una organizacion, no la organizacion ni una persona"
    if _empieza_como_un_buzon(t):
        return "es el nombre de un buzon, no de una persona ni de una empresa"
    return ""


def _es_handle_del_correo(nombre: str, from_address: str) -> bool:
    """
    True si el display name es únicamente la parte local de su propia
    dirección (p.ej. nombre 'recursoshumanos' con correo
    'recursoshumanos@agrempresarial.com'). Es el buzón, no una persona.
    """
    n = (nombre or "").strip().lower().replace(" ", "")
    addr = (from_address or "").strip().lower()
    if not n or "@" not in addr:
        return False
    return n == addr.split("@", 1)[0]


# Fórmulas de despedida que anteceden a la firma del remitente.
_DESPEDIDAS = (
    "cordialmente", "cordial saludo", "atentamente", "atte",
    "saludos", "saludos cordiales", "un saludo",
    "muchas gracias", "mil gracias", "gracias",
    "quedo atento", "quedo atenta", "quedamos atentos", "quedamos atentas",
    "agradezco su atencion", "agradezco su gestion", "agradeciendo su atencion",
    "feliz dia", "feliz tarde", "respetuosamente",
)

# Cargos y áreas: describen un rol, no identifican a nadie.
# OJO: aquí NO van palabras que suelen ABRIR una razón social legítima
# ("grupo", "area", "oficina", "asesoria"), porque descartarían empresas
# reales como "GRUPO 3T SAS" y las mandarían a revisión manual sin motivo.
# Esas van en _CONTENEDORES, que se ignoran al evaluar, no descartan.
_ROL_PALABRAS = (
    "recursos humanos", "recurso humano", "talento humano", "gestion humana",
    "rrhh", "nomina", "contabilidad", "tesoreria", "cartera",
    "juridica", "juridico", "gerencia", "administracion",
    "administrativo", "administrativa", "secretaria", "secretario",
    "asistente", "auxiliar", "analista", "coordinador", "coordinadora",
    "director", "directora", "gerente", "jefe", "jefatura", "profesional",
    "representante legal", "contador", "contadora", "revisor fiscal",
    "notificaciones", "notificacion", "servicio al cliente",
    "atencion al cliente", "seguridad y salud", "sst", "sgsst",
    "seguridad y salud en el trabajo", "salud ocupacional",
    "seguridad industrial", "facturacion",
    "medicina laboral", "atencion integral", "servicio al ciudadano",
    "medica", "medico", "vicepresidencia", "operaciones", "gerencia medica",
    "personal", "cobranza", "cobranzas", "radicacion",
    "urgencias", "dpto", "depto",
    # Etiquetas de datos de contacto que quedan solas al recortar la firma
    "tel", "tels", "telefono", "telefonos", "cel", "celular", "movil",
    "fijo", "ext", "extension", "pbx", "fax", "email", "correo",
    "direccion", "web", "whatsapp", "wpp", "skype", "nit", "cc",
    # Palabras de documentos y adjuntos: aparecen capitalizadas en listados
    # ('Copia Cedula Trabajador') y podrían pasar por razón social.
    "copia", "copias", "adjunto", "adjuntos", "anexo", "anexos",
    "soporte", "soportes", "documento", "documentos", "archivo", "archivos",
    "incapacidad", "incapacidades", "historia", "certificado", "certificados",
    "factura", "facturas", "formato", "formatos", "planilla", "planillas",
    "cedula", "epicrisis", "furat", "furel", "dictamen", "trabajador",
    "afiliado", "paciente", "empleado", "solicitud", "solicitudes",
    "certificacion", "certificaciones", "recobro", "recobros",
)

# Palabras que solo enmarcan un área ("area de nomina", "departamento
# juridico"). No descartan por sí solas: se ignoran y se evalúa el resto,
# para no confundirlas con el arranque de una razón social.
_CONTENEDORES = frozenset({
    "area", "areas", "oficina", "departamento", "dependencia", "grupo",
    "direccion", "coordinacion", "seccion", "unidad", "equipo", "centro",
})

# Nexos que no aportan significado al comparar cargos.
_CONECTORES = frozenset({"de", "del", "la", "el", "los", "las", "y", "e", "en", "al", "a"})

# Los mismos cargos sin sus nexos. `_es_solo_un_rol` compara despues de
# quitarlos, asi que una frase del vocabulario que LLEVA nexos ("seguridad y
# salud") no podia coincidir nunca dentro de un marco de area ("Area de
# Seguridad y Salud en el Trabajo").
_ROLES_SIN_CONECTORES = frozenset(
    " ".join(p for p in rol.split() if p not in _CONECTORES)
    for rol in _ROL_PALABRAS
)

# Encabezados de reenvío que traen nombre Y correo: son los más confiables
# porque permiten verificar que el correo asociado sea externo y humano.
_PATRONES_ENCABEZADO_CON_CORREO = (
    # "De: NOMBRE <correo>"
    re.compile(
        r"(?:^|\n)[ \t>]*(?:De|From|Remitente)[ \t]*:[ \t]*(.+?)[ \t]*"
        r"<([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>",
        re.IGNORECASE,
    ),
    # "De: NOMBRE [mailto:correo]"  (formato clásico de Outlook)
    re.compile(
        r"(?:^|\n)[ \t>]*(?:De|From|Remitente)[ \t]*:[ \t]*(.+?)[ \t]*"
        r"\[mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\]",
        re.IGNORECASE,
    ),
    # "De: NOMBRE (correo)"
    re.compile(
        r"(?:^|\n)[ \t>]*(?:De|From|Remitente)[ \t]*:[ \t]*(.+?)[ \t]*"
        r"\(([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\)",
        re.IGNORECASE,
    ),
)

# Encabezado de reenvío sin correo en la misma línea: "De: NOMBRE".
# Menos confiable (no se puede verificar el dominio), se usa al final.
_PATRON_ENCABEZADO_SIN_CORREO = re.compile(
    r"(?:^|\n)[ \t>]*(?:De|From|Remitente)[ \t]*:[ \t]*([^\n<\[\(@]+?)[ \t]*(?:\n|$)",
    re.IGNORECASE,
)


def _normaliza(texto: str) -> str:
    """Minúsculas sin acentos, para comparar despedidas y cargos."""
    n = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in n if unicodedata.category(c) != "Mn").strip()


def palabras_de_cargo() -> frozenset:
    """
    Las palabras suelta de los cargos, sin nexos ni marcos de área.

    Se expone para que el vocabulario de nombres de `pipeline.extraccion` la
    incluya. Un cargo no identifica a nadie, ni al remitente ni al afectado, y
    con dos listas separadas 'Recursos Humanos' pasaría por nombre del
    afectado porque esas dos palabras solo estarían en esta.
    """
    return frozenset(
        p for rol in _ROL_PALABRAS
        for p in _normaliza(rol).split()
        if p not in _CONECTORES and p not in _CONTENEDORES
    )


def _es_solo_un_rol(nombre: str) -> bool:
    """
    True si el texto describe un cargo o un área en vez de identificar a
    alguien: 'Recursos Humanos', 'Analista de Nomina', 'Area de Cartera'.

    Solo descarta cuando TODO el texto es rol. Si queda cualquier palabra
    ajena al vocabulario de cargos se acepta, para no rechazar razones
    sociales ('GRUPO 3T SAS') ni nombres precedidos de un cargo
    ('Coordinadora Maria Gomez').
    """
    n = _normaliza(nombre).strip(" .,-—:")
    if not n:
        return True
    if n in _ROL_PALABRAS:
        return True
    # Sin la puntuacion de las abreviaturas: "Dpto." es "dpto".
    tokens = [
        t for t in (p.strip(".,;:") for p in n.split())
        if t and t not in _CONECTORES and t not in _CONTENEDORES
    ]
    # Solo quedaron nexos o marcos de área: "area de", "el grupo"
    if not tokens:
        return True
    # El resto, ya sin marcos, es exactamente un cargo: "area de nomina"
    if " ".join(tokens) in _ROLES_SIN_CONECTORES:
        return True
    # Todas las palabras restantes son cargos: "analista de nomina"
    return all(t in _ROL_PALABRAS for t in tokens)


# Separadores con los que las firmas pegan el cargo o los datos de contacto
# detrás del nombre: "Diana Lopez | Coordinadora SST | 3105551234".
_SEPARADORES_CARGO = (" | ", "|", " - ", " – ", " — ", " / ", " · ", " • ", "\t", " * ")

# Marcadores de documento: lo que viene después no es parte del nombre.
_MARCADORES_DOC = frozenset({
    "cc", "c.c", "c.c.", "nit", "ce", "ti", "tp", "rut",
    "cedula", "identificacion", "identificado", "identificada",
})

# Rachas de 4 o más dígitos: teléfonos, cédulas, NIT. Nunca parte de un nombre.
# Las razones sociales con números ("GRUPO 3T", "24/7") usan rachas de 1 o 2.
_RUN_DIGITOS = re.compile(r"\d{4,}")


def _limpiar_candidato(texto: str) -> str:
    """Quita comillas, viñetas y puntuación de borde de un candidato a nombre."""
    t = (texto or "").strip().strip('"').strip("'").strip("*")
    return t.strip(" \t.,;:-—_|>").strip()


def _depurar_candidato_nombre(texto: str) -> str:
    """
    Recorta un candidato hasta dejar solo su parte nominal.

    Las firmas reales no traen el nombre limpio: lo pegan al cargo, al
    teléfono o a la cédula. Recortar en vez de descartar permite rescatar
    el nombre en lugar de mandar el correo a revisión manual:
      'Diana Lopez | Coordinadora SST | 3105551234' -> 'Diana Lopez'
      'Martha Ligia Osorio CC 41234567'             -> 'Martha Ligia Osorio'
      'Tel 6013334444'                              -> 'Tel' (luego se descarta)
    """
    t = _limpiar_candidato(texto)
    if not t:
        return ""
    # 1. Cortar en el separador que introduce cargo o datos de contacto
    for sep in _SEPARADORES_CARGO:
        if sep in t:
            t = t.split(sep, 1)[0]
    # 2. Cortar en el marcador de documento o en la primera racha de dígitos
    tokens = t.split()
    corte = len(tokens)
    for i, tok in enumerate(tokens):
        base = _normaliza(tok).strip(".,;:")
        if base in _MARCADORES_DOC or _RUN_DIGITOS.search(tok):
            corte = i
            break
    tokens = tokens[:corte]
    # 3. Un nombre no empieza ni termina en preposición o artículo. Recortar
    #    puede dejar colgando el nexo: 'Copia de la CC 123' -> 'Copia de la'.
    #    Sin esto, ese resto pasa por nombre y se radica un dato falso.
    while tokens and _normaliza(tokens[0]).strip(".,;:") in _CONECTORES:
        tokens.pop(0)
    while tokens and _normaliza(tokens[-1]).strip(".,;:") in _CONECTORES:
        tokens.pop()
    return _limpiar_candidato(" ".join(tokens))


def _esta_capitalizado_como_nombre(texto: str) -> bool:
    """
    True si el texto está capitalizado como un nombre propio o una razón
    social: toda palabra con peso empieza en mayúscula.

    Es la señal que distingue un nombre de una frase del cuerpo del correo,
    y evita el fallo de tomar 'Se adjuntan los siguientes documentos' como
    nombre del remitente solo porque venía después de un 'Gracias'.

    Acepta  : 'Maria Fernanda Gomez', 'ANGELA MARIA LOZANO', 'GRUPO 3T SAS',
              'Maria de los Angeles', "O'Brien Martinez"
    Rechaza : 'Se adjuntan los siguientes documentos', 'Favor radicar el caso'
    """
    for tok in (texto or "").split():
        limpio = tok.strip(".,;:()[]\"'-—_*")
        if len(limpio) < 3:
            continue
        if _normaliza(limpio) in _CONECTORES:
            continue
        # Tokens que arrancan en dígito ("3T", "24/7") no llevan mayúscula
        if limpio[0].isdigit():
            continue
        if not limpio[0].isupper():
            return False
    return True


def _nombre_plausible_o_vacio(texto: str, min_tokens: int = 1) -> str:
    """
    Depura el candidato y devuelve el nombre si resiste la validación, o
    cadena vacía. Es la validación reforzada para candidatos sacados del
    cuerpo del correo, donde el riesgo de capturar una línea que no es un
    nombre es mucho mayor que en el encabezado.
    """
    t = _depurar_candidato_nombre(texto)
    if not t or not es_nombre_persona_valido(t):
        return ""
    if len(t) > 70:
        return ""
    if "positiva" in _normaliza(t):
        return ""
    if _is_non_human_name(t):
        return ""
    # Cargos, áreas y etiquetas de contacto ('Tel', 'Correo') no son nombres
    if _es_solo_un_rol(t):
        return ""
    # Quedó un dos puntos o una arroba: es una línea de datos, no un nombre
    if ":" in t or "@" in t:
        return ""
    tokens = t.split()
    if not (min_tokens <= len(tokens) <= 8):
        return ""
    # El primer token debe tener letras y al menos la mitad de los tokens
    # también, para admitir "SOLUCIONES 24/7 S.A.S" sin admitir "Tel 300"
    con_letras = [any(c.isalpha() for c in tok) for tok in tokens]
    if not con_letras[0]:
        return ""
    if sum(con_letras) * 2 < len(tokens):
        return ""
    if not _esta_capitalizado_como_nombre(t):
        return ""
    return t


def nombres_de_firma(cuerpo_correo: str) -> List[str]:
    """
    Nombres de todas las firmas del cuerpo, en el orden en que aparecen.

    Un correo reenviado trae varias firmas apiladas, y la primera no siempre
    sirve: cuando pertenece a un buzón interno hay que poder pasar a la
    siguiente en vez de abandonar el cuerpo.

    Reconoce los dos formatos habituales:
      - "Cordialmente, Maria Gomez"      (misma línea)
      - "Cordialmente,"  /  "Maria Gomez" (línea siguiente)
    """
    lineas = [l.strip() for l in (cuerpo_correo or "").splitlines()]
    encontrados: List[str] = []
    for i, linea in enumerate(lineas):
        if not linea:
            continue
        base = _normaliza(linea).rstrip(",.:;!¡")
        # Caso A: la despedida y el nombre van en la misma línea
        m = re.match(r"^(.+?)\s*[,:;]\s*(.+)$", linea)
        if m and _normaliza(m.group(1)) in _DESPEDIDAS:
            nombre = _nombre_plausible_o_vacio(m.group(2), min_tokens=2)
            if nombre:
                encontrados.append(nombre)
            continue
        # Caso B: la línea es solo la despedida; el nombre viene después
        if base not in _DESPEDIDAS:
            continue
        vistas = 0
        for siguiente in lineas[i + 1:]:
            if not siguiente:
                continue
            vistas += 1
            if vistas > 3:
                break
            nombre = _nombre_plausible_o_vacio(siguiente, min_tokens=2)
            if nombre:
                encontrados.append(nombre)
                break
    return encontrados


def extraer_nombre_de_firma(cuerpo_correo: str) -> str:
    """
    Nombre de la primera firma del cuerpo, o cadena vacía.

    Debe recibir SOLO el cuerpo del correo que se está procesando, nunca el
    hilo completo concatenado: la firma de un mensaje anterior pertenece a
    otra persona.
    """
    firmas = nombres_de_firma(cuerpo_correo)
    return firmas[0] if firmas else ""


def _display_name_utilizable(nombre: str, correo: str) -> bool:
    """
    Si un display name sirve como nombre de la persona que escribe.

    Unica validacion para las tres fuentes que traen display name y correo:
    el 'De:', el 'sender' de Graph y los otros correos del hilo. Rechaza el
    handle del propio correo, los buzones no humanos, las menciones a Positiva
    y los cargos.
    """
    if not nombre or not es_nombre_persona_valido(nombre):
        return False
    if _es_handle_del_correo(nombre, correo):
        return False
    if _is_non_human_name(nombre):
        return False
    if "positiva" in _normaliza(nombre):
        return False
    if _es_solo_un_rol(nombre):
        return False
    return True


def _nombre_de_los_metadatos(
    from_name: str,
    from_address: str,
    sender_name: str = "",
    nombres_hilo: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """
    Nombre del remitente segun los campos del mensaje, sin mirar el cuerpo.

    Son las tres fuentes que traen display name y correo juntos, y por eso se
    pueden validar contra el dominio: el 'De:', el 'sender' de Graph y los
    otros mensajes del MISMO remitente en el hilo.

    Si el buzon no es humano NINGUNA de las tres sirve: en un reenvio desde un
    buzon de Positiva las tres traen a la persona de Positiva que reenvio, y
    radicar eso pone a un funcionario como remitente de la PQRD del ciudadano.
    El remitente real esta en el cuerpo.
    """
    if is_buzon_no_humano(from_name, from_address):
        return ""

    if _display_name_utilizable(from_name, from_address):
        return from_name

    if sender_name and _display_name_utilizable(sender_name, from_address):
        return sender_name

    objetivo = (from_address or "").strip().lower()
    for nombre_hilo, correo_hilo in (nombres_hilo or []):
        if (correo_hilo or "").strip().lower() != objetivo or not objetivo:
            continue
        if _display_name_utilizable(nombre_hilo, correo_hilo):
            return nombre_hilo
    return ""


def _es_de_un_buzon_interno(nombre: str, cuerpo_correo: str) -> bool:
    """
    Si el hilo empareja ese nombre con una dirección interna o automatizada.

    Una firma no trae correo al lado, así que por sí sola no se puede validar
    contra el dominio. Los encabezados de reenvío del hilo sí lo traen: si ahí
    ese mismo nombre aparece con una dirección de Positiva, quien firma es la
    persona que reenvió y no el ciudadano.

    La comparación es por subconjunto porque el encabezado suele traer el cargo
    pegado al nombre ('NOMBRE - Contratista') y la firma no.
    """
    for patron in _PATRONES_ENCABEZADO_CON_CORREO:
        for match in patron.finditer(cuerpo_correo or ""):
            del_encabezado = _limpiar_candidato(match.group(1))
            if not del_encabezado:
                continue
            if _es_correo_externo_valido(match.group(2)):
                continue
            if es_la_misma_persona(nombre, del_encabezado):
                return True
    return False


def extraer_nombre_remitente_real(
    from_name: str,
    from_address: str,
    cuerpo_correo: str,
    sender_name: str = "",
    nombres_hilo: Optional[List[Tuple[str, str]]] = None,
    cuerpo_email_actual: str = "",
) -> str:
    """
    Determina el nombre real del remitente recorriendo las fuentes
    disponibles, sin inventar ni tomar el nombre de otra persona.

    Cascada, de la fuente más confiable a la menos:
      1. Display name del 'De:', si el buzón es humano y el valor es válido.
      2. Display name del campo 'sender' de Graph, que difiere del 'De:' en
         envíos delegados o "en nombre de".
      3. Display names del MISMO remitente en otros correos del hilo: Outlook
         a veces trae el nombre en un mensaje y no en otro.
      Si lo que dan 1, 2 o 3 es una sola palabra, la firma del correo actual
      tiene preferencia cuando trae nombre y apellidos: el campo de metadatos
      suele llevar solo el nombre de pila ('DOLORES') y el payload de
      radicación necesita el nombre completo.
      4. Firmas del correo actual ('Cordialmente, NOMBRE'), en orden, hasta
         encontrar una que el hilo no empareje con una dirección interna. Van
         antes que los encabezados porque quien firma es quien escribe,
         mientras que el encabezado puede traer la razón social del buzón
         corporativo. Una firma no trae correo al lado para validar, pero más
         arriba del hilo hay un 'De: NOMBRE <correo>' que sí. Se recorren
         todas: un reenvío apila varias y la primera puede ser la del área de
         Positiva que reenvió.
      5. Encabezados de reenvío del cuerpo con nombre y correo verificables
         ('De: NOMBRE <correo>', '[mailto:]', '(correo)'). Una razón social no se
         devuelve todavía: queda de respaldo y la cascada sigue.
      6. El respaldo del paso 5, si lo hubo.
      7. Encabezado de reenvío sin correo asociado ('De: NOMBRE').
      8. Cadena vacía.

    NUNCA devuelve una dirección de correo, un cargo, un área, ni un nombre de
    Positiva. La cadena vacía significa "no se pudo determinar": el orquestador
    decide entonces si lo recupera con IA o manda el correo a revisión manual.
    """
    candidato = _nombre_de_los_metadatos(
        from_name, from_address, sender_name, nombres_hilo
    )
    if candidato:
        if len(candidato.split()) > 1:
            return candidato
        # Una sola palabra: se prefiere la firma del correo actual si trae
        # nombre y apellidos. No se sigue con las otras fuentes del cuerpo,
        # que pertenecen a mensajes anteriores del hilo y son de otra persona.
        return extraer_nombre_de_firma(cuerpo_email_actual) or candidato

    # --- 4. Firmas del correo que se está procesando, en orden
    #     Solo el cuerpo del correo actual: la firma de un mensaje anterior
    #     del hilo es de otra persona.
    for nombre_firma in nombres_de_firma(cuerpo_email_actual):
        if not _es_de_un_buzon_interno(nombre_firma, cuerpo_correo):
            return nombre_firma

    # --- 5. Encabezados de reenvío con nombre y correo verificables
    razon_social = ""
    for patron in _PATRONES_ENCABEZADO_CON_CORREO:
        for match in patron.finditer(cuerpo_correo or ""):
            nombre = _limpiar_candidato(match.group(1))
            correo_match = match.group(2)
            if not nombre:
                continue
            if not _es_correo_externo_valido(correo_match):
                continue
            if _es_handle_del_correo(nombre, correo_match):
                continue
            depurado = _nombre_plausible_o_vacio(nombre)
            if not depurado:
                continue
            if parece_razon_social(depurado):
                razon_social = razon_social or depurado
                continue
            return depurado

    # --- 6. La razón social del paso 5, si no hubo persona
    if razon_social:
        return razon_social

    # --- 7. Encabezado de reenvío sin correo en la línea
    for match in _PATRON_ENCABEZADO_SIN_CORREO.finditer(cuerpo_correo or ""):
        nombre = _nombre_plausible_o_vacio(match.group(1), min_tokens=2)
        if nombre:
            return nombre

    return ""


def extraer_correo_remitente_real(from_address: str, cuerpo_correo: str, from_name: str = "") -> str:
    """
    Determina el correo efectivo del remitente.

    Si el From es un dominio interno de Positiva o un buzón
    automatizado/no humano, busca en el cuerpo el primer correo externo
    válido. De lo contrario retorna from_address tal cual.
    """
    if not is_buzon_no_humano(from_name, from_address):
        return from_address

    for match in _EMAIL_REGEX.finditer(cuerpo_correo):
        correo = match.group(0).lower()
        if _es_correo_externo_valido(correo):
            return correo

    # No se encontró un externo identificable: devolvemos el original
    # para que el orquestador detecte el caso y mueva a REVISIÓN_MANUAL.
    return from_address


def remitentes_externos_citados(cuerpo_correo: str) -> set:
    """Las direcciones externas que los encabezados del hilo citan como remitentes."""
    return {
        match.group(2).lower()
        for patron in _PATRONES_ENCABEZADO_CON_CORREO
        for match in patron.finditer(cuerpo_correo or "")
        if _es_correo_externo_valido(match.group(2))
    }


def tiene_remitente_externo_identificable(
    from_name: str, from_address: str, cuerpo_correo: str
) -> bool:
    """
    True si:
      - el From ya es un correo externo válido, o
      - el From es interno/no humano pero en el cuerpo se logró identificar
        al menos un correo externo válido.

    Permite al orquestador decidir si radicar o enviar a REVISIÓN_MANUAL
    cuando el hilo solo contiene direcciones internas/automatizadas.
    """
    if not is_buzon_no_humano(from_name, from_address):
        return _es_correo_externo_valido(from_address)

    for match in _EMAIL_REGEX.finditer(cuerpo_correo or ""):
        if _es_correo_externo_valido(match.group(0)):
            return True
    return False


# NITs conocidos que corresponden a afiliaciones genéricas o colectivas
# (no identifican a una persona natural real)
GENERIC_DOCUMENT_NUMBERS = {
    "899999998",
    "8999999980",   # con dígito de verificación
    "89999998",     # variante sin dígito
}

# Fragmentos de nombre que indican identidades genéricas/institucionales
GENERIC_NAME_PATTERNS = [
    "INDEPENDIENTE VOLUNTARIO",
    "INDEPENDIENTE INFORMAL",
    "TRABAJADOR INDEPENDIENTE DECRETO",
    "DECRETO 1563",
    "COTIZANTE INDEPENDIENTE",
]


def is_generic_identity(nombre: str, numero_documento: str) -> bool:
    """
    Retorna True si el nombre o documento corresponde a una identidad
    generica y no a la persona natural: p.ej. NIT 899999998 o
    'Independiente Voluntario Decreto 1563'.
    """
    if not nombre and not numero_documento:
        return False

    doc_limpio = (numero_documento or "").strip().replace("-", "").replace(" ", "")
    if doc_limpio in GENERIC_DOCUMENT_NUMBERS:
        return True

    nombre_upper = (nombre or "").upper()
    for patron in GENERIC_NAME_PATTERNS:
        if patron in nombre_upper:
            return True

    return False
