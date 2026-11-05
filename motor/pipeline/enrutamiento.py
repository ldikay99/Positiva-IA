"""
A donde va un correo: radicarlo como PQRD, o mandarlo a otra area.

No todo lo que llega al buzon es una PQRD. Una tutela va a notificaciones
judiciales y un tramite medico-laboral a Medicina Laboral; radicar cualquiera de
los dos como peticion lo mete en la cola equivocada y le pone un termino de
respuesta que no le corresponde.

La decision va ANTES de descargar adjuntos y de llamar al modelo: un correo que
se reenvia no necesita OCR ni agente.

Solo se decide por señales que estan escritas en el correo. Distinguir "envio mi
historia clinica como prueba del caso" de "reclamo porque me negaron la
incapacidad, y adjunto la historia clinica" exige leer la intencion, y eso no se
resuelve con una lista de palabras: ante la duda el correo sigue su camino
normal y se radica.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from business_rules.clasificacion_remitente import (
    DESTINO_MEDICINA_LABORAL,
    DESTINO_NOTIFICACIONES_JUDICIALES,
    JUDICIAL_KEYWORD_PATTERNS,
)


class Destino(str, Enum):
    """Que hacer con el correo."""

    RADICAR = "radicar"
    MEDICINA_LABORAL = "medicina_laboral"
    JUDICIAL = "judicial"
    NO_SE_RADICA = "no_se_radica"


@dataclass
class Enrutamiento:
    """A donde va el correo y por que."""

    destino: Destino = Destino.RADICAR
    correo_destino: str = ""
    evidencia: str = ""

    @property
    def se_reenvia(self) -> bool:
        return self.destino in (Destino.MEDICINA_LABORAL, Destino.JUDICIAL)

    @property
    def se_radica(self) -> bool:
        return self.destino is Destino.RADICAR

    @property
    def nombre_del_area(self) -> str:
        """Como se llama el area destino en un texto que lee una persona."""
        return {
            Destino.MEDICINA_LABORAL: "Medicina Laboral",
            Destino.JUDICIAL: "Notificaciones Judiciales",
        }.get(self.destino, "")


# Direcciones cuyos correos no se radican. Se comparan completas, no por
# dominio: de `@positiva.gov.co` llegan PQRD de ciudadanos que un area reenvia,
# y el remitente real se saca del hilo. Excluir el dominio entero las perderia.
REMITENTES_QUE_NO_SE_RADICAN = frozenset({
    # Buzon de facturacion electronica: lo que manda son facturas y notas, que
    # tienen su propio tramite y no son peticiones de nadie.
    "facturacionelectronica@hci.gov.co",
})
MOTIVO_REMITENTE_EXCLUIDO = "REMITENTE_EXCLUIDO"
# Un buzon de Positiva que no trae a nadie de afuera en el hilo: lo que llega es
# un asunto interno, no la PQRD de alguien.
MOTIVO_REMITENTE_NO_IDENTIFICABLE = "REMITENTE_NO_IDENTIFICABLE"


# Buzon de la empresa que digitaliza la correspondencia fisica de Medicina
# Laboral. Todo lo que llega de ahi es de esa area, sin excepcion.
DOMINIO_DE_DIGITALIZACION = "@e-entrega.co"

# ── Entes de control ──
#
# Una comunicacion de un ente de control no es una PQRD: tiene su propio termino
# y su propia area, y radicarla como peticion la mete en la cola equivocada.
#
# El nombre del ente NO basta por si solo: aparece tambien en correos que si son
# PQRD. El ciudadano adjunta su propia denuncia ante la Fiscalia como prueba, el
# membrete de un banco dice "vigilada por la Superintendencia Financiera", y un
# FURAT cita al Ministerio de Trabajo en su encabezado. Lo que distingue es QUIEN
# escribe, no que se nombre.
_ENTES = (
    r"contralor[ií]a|procuradur[ií]a|fiscal[ií]a\s+general"
    r"|superintendencia|ministerio\s+de\s+(?:trabajo|salud)"
    r"|mintrabajo|minsalud|supersalud|superfinanciera"
    r"|defensor[ií]a\s+del\s+pueblo|personer[ií]a"
)

# Que el correo LLEGUE de un ente es la evidencia mas fuerte que hay: no lo
# nombra, lo es.
_DOMINIO_DE_ENTE = re.compile(
    r"@(?:[\w.-]*\.)?(?:contraloria|procuraduria|fiscalia|mintrabajo|minsalud"
    r"|supersalud|superfinanciera|superservicios|defensoria|personeria"
    r"|ramajudicial|cendoj|consejodeestado|corteconstitucional)\."
    r"(?:gov|edu)\.co",
    re.IGNORECASE,
)

# En el ASUNTO el nombre si decide: quien escribe lo pone ahi para decir de que
# va el correo.
_ENTE_EN_EL_ASUNTO = re.compile(_ENTES, re.IGNORECASE)

# En el CUERPO hace falta que el ente sea quien requiere, no una mencion de
# paso: sin esa exigencia la regla se lleva PQRD legitimas que solo lo citan.
_ENTE_QUE_REQUIERE = re.compile(
    r"(?:requerimiento|oficio|solicitud|comunicaci[oó]n|traslado|auto|"
    r"resoluci[oó]n|hallazgo|indagaci[oó]n)\s+(?:\w+\s+){0,3}"
    r"(?:de|del|ante)\s+la?\s+(?:" + _ENTES + r")"
    r"|(?:" + _ENTES + r")\s+(?:\w+\s+){0,3}"
    r"(?:requiere|solicita|remite|traslada|notifica)",
    re.IGNORECASE,
)

# El desacato y el proceso ordinario laboral son judiciales por si mismos, sin
# necesidad de que se nombre un ente.
_JUDICIAL_EXTRA = re.compile(
    r"\bdesacatos?\b|\bproceso\s+ordinario\s+laboral\b"
    r"|\bincidente\s+de\s+desacato\b",
    re.IGNORECASE,
)

# El juzgado ACTUANDO. Es lo que hace judicial a un correo, aunque quien lo
# teclee escriba desde un correo personal: un apoderado notificando un auto
# admisorio esta notificando de verdad.
_LA_RAMA_ACTUA = re.compile(
    r"\bse\s+notifica\b|\bnotificaci[oó]n\s+(?:de|del)\s+(?:auto|fallo|sentencia)"
    r"|\bauto\s+(?:admisorio|interlocutorio)\b"
    r"|\b(?:juzgado|tribunal|despacho)\s+(?:\w+\s+){0,4}"
    r"(?:ordena|dispone|resuelve|notifica|requiere|remite)"
    r"|\bordena\s+(?:al?\s+)?(?:la\s+)?(?:entidad|positiva|arl)"
    r"|\bincidente\s+de\s+desacato\b|\bhabeas\s+corpus\b"
    r"|\bdemanda\s+ordinaria\s+laboral\b|\btraslado\s+de\s+la\s+demanda\b"
    r"|\bactuaci[oó]n\s+procesal\b|\bauto\s+avoca\b|\bimpugnaci[oó]n\s+(?:de\s+)?tutela\b",
    re.IGNORECASE,
)

# Quien PIDE que se cumpla un fallo, o se queja de que no se lo cumplen, no
# esta notificando nada: le esta pidiendo algo a Positiva, y eso es una PQRD.
# Nombrar la tutela ahi dice de que trata la peticion, no de donde viene.
_PIDE_SOBRE_LO_JUDICIAL = re.compile(
    r"solicit\w*\s+(?:\w+\s+){0,2}cumplimiento"
    r"|cumplimiento\s+(?:de|del)\s+(?:la\s+|el\s+)?(?:fallo|sentencia|tutela)"
    r"|no\s+(?:me\s+|nos\s+)?(?:han|ha|se\s+ha)\s+(?:dado\s+)?cumpli"
    r"|(?:aun|todavia)\s+no\s+(?:me\s+|nos\s+)?cumpl"
    r"|reitero\s+(?:mi\s+)?solicitud",
    re.IGNORECASE,
)


# La Junta de Calificacion decide el origen y el porcentaje de perdida de
# capacidad laboral. Es un tramite medico-laboral por definicion, y sus siglas
# no significan otra cosa en este dominio.
_JUNTA = re.compile(
    r"\bjunta\s+(?:regional|nacional)\b|\bjrci\b|\bjnci\b"
    r"|\bjunta\s+de\s+calificaci[oó]n\b",
    re.IGNORECASE,
)

# Nombrarla no basta. Quien pregunta por SU propio tramite, o se queja de que
# se demora, esta pidiendole algo a la entidad: es una PQRD, y derivarla la
# manda a un area que no atiende ciudadanos.
_PIDE_SOBRE_LA_JUNTA = re.compile(
    r"\bmi\s+(?:calificaci[oó]n|dictamen|valoraci[oó]n|junta|pcl|caso)\b"
    r"|\b(?:quiero|deseo|necesito)\s+saber\b"
    r"|\bllevo\s+\w+\s+(?:meses|dias|semanas|a[nñ]os)\b"
    r"|\b(?:solicito|solicitud\s+de)\s+(?:\w+\s+){0,2}"
    r"(?:informaci[oó]n|respuesta|copia|certificado|estado)\b"
    r"|\bqueja\b|\breclamo\b|\binaceptable\b|\bderecho\s+de\s+petici[oó]n\b",
    re.IGNORECASE,
)

# La Junta o una entidad de salud ACTUANDO. Es lo que hace el tramite suyo.
_LA_JUNTA_ACTUA = re.compile(
    r"\b(?:junta|jrci|jnci)\s+(?:\w+\s+){0,3}"
    r"(?:remite|convoca|cita|emite|solicita|requiere|notifica|traslada)"
    r"|\bcitaci[oó]n\s+(?:a|para)\s+(?:\w+\s+){0,2}valoraci[oó]n"
    r"|\bdictamen\s+(?:emitido|proferido|adjunto\s+por)"
    r"|\bdeterminaci[oó]n\s+de\s+origen\b",
    re.IGNORECASE,
)

# ── Lo que se queda en Servicio al Cliente por encima de todo lo demas ──
#
# Un derecho de peticion se atiende aqui venga de donde venga. Un juzgado
# tambien es empleador y escribe por asuntos administrativos: eso es una
# peticion, no una actuacion judicial.
_ES_DERECHO_DE_PETICION = re.compile(
    r"\bderecho\s+de\s+petici[oó]n\b|\bart[ií]culo\s+23\s+de\s+la\s+constituci[oó]n\b",
    re.IGNORECASE,
)

# Cuanto del cuerpo se mira. Lo que define el tramite va arriba; mas abajo
# empiezan los hilos citados y los avisos legales, donde "juzgado" aparece en
# cualquier pie de pagina.
TOPE_DEL_CUERPO = 3000

# Un buzon no dice de que va el mensaje: dice a quien se le mando. El
# encabezado que arrastra un reenvio trae la lista de destinatarios con sus
# nombres, y entre ellos figuran areas cuyo NOMBRE contiene justo las palabras
# que deciden el enrutamiento. Leerlo convierte una peticion corriente en una
# notificacion judicial por la lista de copiados que venia pegada.
#
# Se retira el bloque de encabezado completo —de "De:" hasta "Asunto:"— y
# cualquier lista de destinatarios suelta. No se toca lo que hay debajo: ahi
# esta el texto del caso, que en un reenvio es lo unico que dice de que se
# trata. La direccion de QUIEN escribe tampoco pasa por aqui: esa se mira
# aparte, y ahi si decide.
# Los dos cortan contra el siguiente rotulo del propio encabezado, no contra un
# numero de caracteres: la lista de copiados de un correo institucional pasa de
# los novecientos caracteres con facilidad, y un tope fijo deja fuera justo los
# encabezados mas largos, que son los que mas buzones nombran.
_ROTULOS = r"Asunto|Subject|Enviado|Sent|De|From|Para|To|CC|CCO|Cc|Bcc"
_ENCABEZADO_CITADO = re.compile(
    r"\b(?:De|From)\s*:(?:(?!\b(?:Asunto|Subject)\s*:).)*\b(?:Asunto|Subject)\s*:",
    re.IGNORECASE | re.DOTALL,
)
_LISTA_DE_DESTINATARIOS = re.compile(
    r"\b(?:Para|To|CC|CCO|Cc|Bcc)\s*:(?:(?!\b(?:" + _ROTULOS + r")\s*:).)*",
    re.IGNORECASE | re.DOTALL,
)
_DIRECCION = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}", re.IGNORECASE)


def _sin_buzones(texto: str) -> str:
    """El texto sin los encabezados de reenvio ni las direcciones sueltas."""
    limpio = _ENCABEZADO_CITADO.sub(" ", texto or "")
    limpio = _LISTA_DE_DESTINATARIOS.sub(" ", limpio)
    return _DIRECCION.sub(" ", limpio)


def _sin_acentos(texto: str) -> str:
    nfd = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _judicial(asunto: str, cuerpo: str, correo: str = "") -> Optional[str]:
    """
    La marca judicial encontrada, o None.

    Que el texto NOMBRE una tutela no la convierte en un asunto de la Rama. Lo
    que decide es quien escribe y en calidad de que: si el correo llega de un
    despacho, o si presenta al juzgado actuando, es judicial. Si es alguien
    pidiendole a Positiva que cumpla un fallo, es una peticion y se radica.
    """
    asunto_plano = _sin_acentos(asunto)
    cuerpo_plano = _sin_acentos(cuerpo[:TOPE_DEL_CUERPO])
    plano = f"{asunto_plano}\n{cuerpo_plano}"

    encontrada = None
    for patron in JUDICIAL_KEYWORD_PATTERNS:
        rx = re.compile(r"\b" + patron, re.IGNORECASE)
        if rx.search(asunto_plano) or rx.search(cuerpo_plano):
            encontrada = patron.replace(r"\b", "")
            break

    if not encontrada:
        # La lista heredada nombra la figura —tutela, juzgado, fallo— y hay
        # actuaciones que no la nombran: una comunicacion de actuacion procesal
        # o un auto que avoca. Que la Rama este actuando basta por si solo.
        actuando = _LA_RAMA_ACTUA.search(plano)
        return actuando.group(0) if actuando else None

    # Llega de un despacho: no hay nada que interpretar.
    if _DOMINIO_DE_ENTE.search(correo or ""):
        return encontrada
    if _LA_RAMA_ACTUA.search(plano):
        return encontrada
    if _PIDE_SOBRE_LO_JUDICIAL.search(plano):
        return None
    return encontrada


def _ente_de_control(asunto: str, cuerpo: str, correo: str) -> str:
    """
    Por que este correo es de un ente de control, o cadena vacia.

    Tres señales, de la mas fuerte a la mas debil. Ninguna es "el texto nombra
    un ente": eso solo dice que el ente se menciona, y en un correo de ARL se
    menciona constantemente sin que el correo venga de ahi.
    """
    if _DOMINIO_DE_ENTE.search(correo or ""):
        return f"el correo llega de un ente de control: {correo}"

    m = _ENTE_EN_EL_ASUNTO.search(_sin_acentos(asunto))
    if m:
        return f"el asunto nombra un ente de control: \"{m.group(0)}\""

    plano = _sin_acentos(f"{asunto}\n{cuerpo[:TOPE_DEL_CUERPO]}")
    m = _JUDICIAL_EXTRA.search(plano)
    if m:
        return f"tramite judicial nombrado: \"{m.group(0)}\""

    m = _ENTE_QUE_REQUIERE.search(plano)
    if m:
        return (f"el cuerpo lo presenta como requerimiento de un ente: "
                f"\"{' '.join(m.group(0).split())[:70]}\"")
    return ""


def _seria_un_bucle(ruta: "Enrutamiento", correo_remitente: str) -> bool:
    """
    Si el correo volveria al area que lo mando.

    Un area responde desde su buzon y ese correo vuelve a entrar aqui: sin esta
    comprobacion se le devuelve, y queda rebotando entre las dos sin que nadie
    lo atienda.
    """
    de = (correo_remitente or "").strip().lower()
    return bool(de) and de == (ruta.correo_destino or "").strip().lower()


def decidir(
    asunto: str = "",
    cuerpo: str = "",
    correo_remitente: str = "",
) -> Enrutamiento:
    """
    A donde va el correo.

    El orden importa. Lo judicial manda sobre lo demas: una tutela sobre un
    dictamen sigue siendo una tutela, con su termino y su area, aunque hable de
    medicina laboral de principio a fin.

    Ninguna regla puede devolver el correo al area que lo envio: eso no lo
    atiende nadie y vuelve a entrar en la siguiente corrida.
    """
    de = (correo_remitente or "").strip().lower()
    if de in REMITENTES_QUE_NO_SE_RADICAN:
        return Enrutamiento(
            destino=Destino.NO_SE_RADICA,
            evidencia=f"lo escribe {de}, que no radica por este canal",
        )

    ruta = _decidir_area(asunto, cuerpo, correo_remitente)
    if ruta.se_reenvia and _seria_un_bucle(ruta, correo_remitente):
        # Un area que escribe a este buzon esta entregando el caso, no
        # notificando: devolverselo lo deja rebotando. Se radica.
        return Enrutamiento(
            evidencia=f"lo remitio {correo_remitente}, que es el area a la que "
                      f"iria: se radica en vez de devolverselo",
        )
    return ruta


def _decidir_area(asunto: str, cuerpo: str, correo_remitente: str) -> Enrutamiento:
    """El area que corresponde por contenido, sin mirar de donde vino."""
    asunto = _sin_buzones(asunto)
    cuerpo = _sin_buzones(cuerpo)
    plano = _sin_acentos(f"{asunto}\n{cuerpo[:TOPE_DEL_CUERPO]}")
    # Un derecho de peticion es de Servicio al Cliente aunque lo firme un
    # juzgado, salvo que ademas la Rama este actuando en el mismo correo.
    if _ES_DERECHO_DE_PETICION.search(plano) and not _LA_RAMA_ACTUA.search(plano):
        return Enrutamiento(
            evidencia="es un derecho de peticion: lo atiende Servicio al Cliente")

    judicial = _judicial(asunto, cuerpo, correo_remitente)
    if judicial:
        return Enrutamiento(
            destino=Destino.JUDICIAL,
            correo_destino=DESTINO_NOTIFICACIONES_JUDICIALES,
            evidencia=f"marca judicial en el asunto o el cuerpo: \"{judicial}\"",
        )

    ente = _ente_de_control(asunto, cuerpo, correo_remitente)
    if ente:
        return Enrutamiento(
            destino=Destino.JUDICIAL,
            correo_destino=DESTINO_NOTIFICACIONES_JUDICIALES,
            evidencia=ente,
        )

    if (correo_remitente or "").strip().lower().endswith(DOMINIO_DE_DIGITALIZACION):
        return Enrutamiento(
            destino=Destino.MEDICINA_LABORAL,
            correo_destino=DESTINO_MEDICINA_LABORAL,
            evidencia=f"el remitente escribe desde {DOMINIO_DE_DIGITALIZACION}, "
                      f"que digitaliza la correspondencia de Medicina Laboral",
        )

    plano_junta = f"{asunto}\n{cuerpo[:TOPE_DEL_CUERPO]}"
    junta = _JUNTA.search(plano_junta)
    if junta and not (
        _PIDE_SOBRE_LA_JUNTA.search(plano_junta)
        and not _LA_JUNTA_ACTUA.search(plano_junta)
    ):
        return Enrutamiento(
            destino=Destino.MEDICINA_LABORAL,
            correo_destino=DESTINO_MEDICINA_LABORAL,
            evidencia=f"nombra la Junta de Calificacion: \"{junta.group(0)}\"",
        )

    return Enrutamiento()
