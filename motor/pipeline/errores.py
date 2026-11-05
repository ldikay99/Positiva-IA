"""
Clasificacion de fallos y politica de reintentos.

Cada fallo se clasifica por severidad (fatal o degrada), si es reintentable, y
a que motivo de revision manual corresponde. Ningun fallo se descarta sin
registrar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class Severidad(str, Enum):
    FATAL = "fatal"          # no se puede radicar este correo
    DEGRADA = "degrada"      # se puede radicar, pero se perdio una capacidad
    AVISO = "aviso"          # no afecta el resultado, queda anotado


@dataclass(frozen=True)
class Clase:
    """Una clase de error, con su politica."""

    codigo: str
    severidad: Severidad
    reintentable: bool
    max_intentos: int
    espera_base_s: float
    motivo_manual: str
    descripcion: str


# ── Catalogo ──
# El orden importa: se evalua de arriba a abajo y gana la primera coincidencia,
# asi que lo especifico va antes que lo generico.

CLASES: Tuple[Clase, ...] = (
    Clase(
        codigo="CLOUDFLARE_CHALLENGE",
        severidad=Severidad.FATAL,
        reintentable=True, max_intentos=4, espera_base_s=5.0,
        motivo_manual="CLOUDFLARE_403",
        descripcion="captcha de Cloudflare: transitorio, se resuelve al reintentar",
    ),
    Clase(
        codigo="ORIGEN_INALCANZABLE",
        severidad=Severidad.FATAL,
        reintentable=True, max_intentos=3, espera_base_s=10.0,
        motivo_manual="SGDEA_CAIDO",
        descripcion="el backend no responde (502, 503, 504 o 522 de Cloudflare)",
    ),
    Clase(
        codigo="TOKEN_EXPIRADO",
        severidad=Severidad.FATAL,
        reintentable=True, max_intentos=2, espera_base_s=0.0,
        motivo_manual="",
        descripcion="401: hay que renovar el token y repetir de inmediato",
    ),
    Clase(
        codigo="SIN_PERMISO",
        severidad=Severidad.FATAL,
        reintentable=False, max_intentos=1, espera_base_s=0.0,
        motivo_manual="ERROR_INESPERADO",
        descripcion="403 real de autorizacion: reintentar no cambia nada",
    ),
    Clase(
        codigo="PETICION_INVALIDA",
        severidad=Severidad.FATAL,
        reintentable=False, max_intentos=1, espera_base_s=0.0,
        motivo_manual="VALIDACION_RECHAZO",
        descripcion="400 o 422: el payload esta mal y reintentarlo igual no sirve",
    ),
    Clase(
        codigo="FORMATO_RECHAZADO",
        # El backend contesta 500, que por codigo seria "servicio caido" y
        # reintentable. No lo es: la peticion sale mal formada de aqui y
        # repetirla da el mismo 500 en cada ciclo hasta agotar los reintentos.
        severidad=Severidad.FATAL,
        reintentable=False, max_intentos=1, espera_base_s=0.0,
        motivo_manual="PETICION_MAL_FORMADA",
        descripcion=(
            "el backend rechaza el tipo de contenido de la peticion "
            "(HttpMediaTypeNotSupported)"
        ),
    ),
    Clase(
        codigo="RESPUESTA_SIN_RADICADO",
        # El backend acepto la peticion pero no devolvio el numero. No se sabe
        # si creo el registro, asi que no se repite: repetirla dejaria dos si lo
        # creo. Quien revise tiene que buscarlo antes de radicar a mano.
        severidad=Severidad.FATAL,
        reintentable=False, max_intentos=1, espera_base_s=0.0,
        motivo_manual="RADICACION_SIN_CONFIRMAR",
        descripcion=(
            "la radicacion se acepto pero la respuesta no trae el numero de "
            "radicado"
        ),
    ),
    Clase(
        codigo="TIEMPO_AGOTADO",
        severidad=Severidad.FATAL,
        reintentable=True, max_intentos=3, espera_base_s=3.0,
        motivo_manual="SGDEA_CAIDO",
        descripcion="la peticion no respondio dentro del limite",
    ),
    Clase(
        codigo="DNS_NO_RESUELVE",
        # Un corte de resolucion no dura dos segundos: dura lo que tarde en
        # reponerse el resolutor. Con un solo reintento corto, el lote entero
        # se va a revision manual y el job no lo vuelve a mirar hasta la
        # siguiente media hora.
        severidad=Severidad.DEGRADA,
        reintentable=True, max_intentos=4, espera_base_s=5.0,
        motivo_manual="",
        descripcion="no resuelve el nombre del servicio (tiene Redis fuera de servicio)",
    ),
    Clase(
        codigo="ESQUEMA_DESALINEADO",
        severidad=Severidad.DEGRADA,
        reintentable=False, max_intentos=1, espera_base_s=0.0,
        motivo_manual="",
        descripcion=(
            "la tabla destino no tiene la columna que se intenta escribir "
            "(PGRST204). No se arregla reintentando: hay que migrar el esquema"
        ),
    ),
    Clase(
        codigo="MODELO_TRANSITORIO",
        severidad=Severidad.DEGRADA,
        reintentable=True, max_intentos=3, espera_base_s=2.0,
        motivo_manual="",
        descripcion="fallo transitorio del modelo (transferencia incompleta, reset)",
    ),
    Clase(
        codigo="LIMITE_DE_TASA",
        severidad=Severidad.DEGRADA,
        reintentable=True, max_intentos=5, espera_base_s=15.0,
        motivo_manual="",
        descripcion="429: hay que esperar mas y reintentar",
    ),
    Clase(
        codigo="ADJUNTO_PROTEGIDO",
        severidad=Severidad.DEGRADA,
        reintentable=False, max_intentos=1, espera_base_s=0.0,
        motivo_manual="ADJUNTO_PROTEGIDO",
        descripcion="el adjunto tiene contraseña y su contenido no se puede leer",
    ),
    Clase(
        codigo="ADJUNTO_ILEGIBLE",
        severidad=Severidad.DEGRADA,
        reintentable=False, max_intentos=1, espera_base_s=0.0,
        motivo_manual="ADJUNTO_ILEGIBLE",
        descripcion=(
            "el adjunto no tiene texto extraible y el OCR tampoco lo resolvio. "
            "Degrada y no bloquea: el archivo viaja igual a SGDEA, y si los "
            "campos obligatorios salieron de otra parte la radicacion procede"
        ),
    ),
    Clase(
        codigo="SIN_OCR",
        severidad=Severidad.DEGRADA,
        reintentable=False, max_intentos=1, espera_base_s=0.0,
        motivo_manual="SIN_OCR",
        descripcion="no hay motor de OCR y el adjunto es una imagen",
    ),
    Clase(
        codigo="DESCONOCIDO",
        severidad=Severidad.FATAL,
        reintentable=False, max_intentos=1, espera_base_s=0.0,
        motivo_manual="ERROR_INESPERADO",
        descripcion="fallo sin clasificar: hay que mirar la traza y clasificarlo",
    ),
)

POR_CODIGO: Dict[str, Clase] = {c.codigo: c for c in CLASES}
DESCONOCIDO = POR_CODIGO["DESCONOCIDO"]


# ── Clasificacion ──

# Firmas textuales. Se buscan en el cuerpo de la respuesta o en el mensaje de
# la excepcion, porque el codigo HTTP solo no alcanza para distinguir un 403 de
# Cloudflare de un 403 de autorizacion, que exigen politicas opuestas.
#
# `solo_local` marca las firmas que NO pueden venir de una respuesta HTTP: un
# adjunto con contraseña o un OCR sin motor son fallos de la conversion, que
# ocurre en este proceso y no en el backend. Sin esa distincion, la palabra
# "password" dentro del cuerpo de un 401 —"Invalid user password"— se
# clasificaria como ADJUNTO_PROTEGIDO: severidad degrada en vez de fatal, sin
# reintento, y el token no se renovaria.
_FIRMAS: Tuple[Tuple[str, str, bool], ...] = (
    ("CLOUDFLARE_CHALLENGE", r"attention\s+required|cf-mitigated|captcha-container|"
                             r"cloudflare.*(?:ray\s*id|challenge)|just\s+a\s+moment",
     False),
    # Va antes que las firmas de ORIGEN_INALCANZABLE: el cuerpo de este rechazo
    # llega dentro de un 500 cuyo texto dice "Internal Server Error", y esa
    # firma generica lo tomaria por un servicio caido y lo reintentaria.
    ("RESPUESTA_SIN_RADICADO", r"sin numero de radicado", False),
    ("FORMATO_RECHAZADO", r"httpmediatypenotsupported|"
                          r"content\s*type\W{0,3}(?:is\s+)?not\s+supported|"
                          r"unsupported\s+media\s+type", False),
    ("ORIGEN_INALCANZABLE", r"error\s*52[0-9]|cloudflare_error|"
                            r"could\s+not\s+establish\s+a\s+tcp\s+connection|"
                            r"web\s+server\s+is\s+down", False),
    ("ESQUEMA_DESALINEADO", r"PGRST\d+|could\s+not\s+find\s+the\s+'.*'\s+column",
     False),
    ("DNS_NO_RESUELVE", r"name\s+or\s+service\s+not\s+known|"
                        r"nodename\s+nor\s+servname|getaddrinfo\s+failed|"
                        r"temporary\s+failure\s+in\s+name\s+resolution|error\s+-2",
     False),
    # El corte de conexion a media lectura se nombra de muchas formas: el SDK
    # lo llama ReadError, Linux lo llama connection reset y Windows lo numera.
    # Se casa tambien el numero porque el texto de WinError viene traducido al
    # idioma del sistema y una firma en ingles no lo alcanza: 10053 es la
    # conexion anulada por el equipo local y 10054 la cerrada por el remoto.
    ("MODELO_TRANSITORIO", r"transferencoding|transfer\s*length|"
                           r"response\s+payload\s+is\s+not\s+completed|"
                           r"connectionreset|connection\s+reset|"
                           r"connection\s+aborted|winerror\s*1005[34]|"
                           r"\breaderror\b|remotedisconnected|"
                           r"server\s+disconnected", False),
    ("ADJUNTO_PROTEGIDO",
     r"protegido\s+con\s+contrase[nñ]a|"
     r"(?:pdf|documento|adjunto|archivo|file)[^.\n]{0,40}"
     r"(?:password|encrypted|contrase[nñ]a)|"
     r"(?:password|encrypted)[^.\n]{0,20}(?:protected|pdf|document)",
     True),
    ("SIN_OCR", r"tesseractnotfound|tesseract\s+is\s+not\s+installed", True),
    # Un escaneo que ni el extractor de texto ni el OCR resolvieron. Es del
    # adjunto, no del sistema: reintentarlo da exactamente lo mismo.
    ("ADJUNTO_ILEGIBLE", r"sin\s+texto\s+extra[ií]ble|p[aá]ginas\s+sin\s+texto",
     True),
    # Los SDK de Vertex y gRPC no devuelven un codigo HTTP: lanzan una
    # excepcion con el nombre del estado dentro del mensaje.
    ("LIMITE_DE_TASA", r"resource_exhausted|quota\s+exceeded|rate\s*limit", False),
    ("SIN_PERMISO", r"permission_denied", False),
    ("ORIGEN_INALCANZABLE", r"\bunavailable\b|\binternal\s+error\b|"
                            r"connection\s+refused|\berrno\s+111\b|"
                            r"despu[eé]s\s+de\s+varios\s+intentos", False),
    ("TIEMPO_AGOTADO", r"timeout|timed\s+out|deadline_exceeded", False),
)

# Codigo HTTP embebido en el texto. Un servicio que envuelve la respuesta en un
# `Exception` pierde el estado por el camino, y sin el la tabla `_POR_ESTADO` no
# llega a consultarse: el fallo cae en DESCONOCIDO y deja de ser reintentable.
_ESTADO_EN_TEXTO = re.compile(
    r"(?:fallida|fallido|fallo|error|status|c[oó]digo|http)\W{0,3}(\d{3})\b",
    re.IGNORECASE,
)

_POR_ESTADO: Dict[int, str] = {
    400: "PETICION_INVALIDA",
    401: "TOKEN_EXPIRADO",
    403: "SIN_PERMISO",          # solo si NO hay firma de Cloudflare
    422: "PETICION_INVALIDA",
    429: "LIMITE_DE_TASA",
    500: "ORIGEN_INALCANZABLE",
    502: "ORIGEN_INALCANZABLE",
    503: "ORIGEN_INALCANZABLE",
    504: "TIEMPO_AGOTADO",
    522: "ORIGEN_INALCANZABLE",
}


# Clases en las que consta que la peticion NO llego a ejecutarse en el servidor:
# la corto Cloudflare en el borde, no se resolvio el nombre, o el token se
# rechazo antes de mirar el cuerpo.
#
# Importa para las operaciones que CREAN algo. Radicar no es idempotente: cada
# llamada produce un radicado nuevo. Un 5xx describe lo que le paso al servidor
# despues de recibir la peticion y no dice si alcanzo a crear el registro, y un
# tiempo de espera agotado es justo el caso donde mas probable es que si. En
# esos casos repetir puede dejar dos radicados para el mismo correo, asi que se
# prefiere aplazar: la corrida siguiente vuelve a pasar por las capas de
# deduplicacion antes de radicar.
SIN_EFECTO_EN_EL_SERVIDOR = frozenset({
    "CLOUDFLARE_CHALLENGE",
    "DNS_NO_RESUELVE",
    "TOKEN_EXPIRADO",
})


def puede_repetirse_una_escritura(clase: Clase) -> bool:
    """Si se puede repetir una operacion que crea algo, sin arriesgar dos."""
    return clase.reintentable and clase.codigo in SIN_EFECTO_EN_EL_SERVIDOR


def clasificar(
    estado: Optional[int] = None,
    cuerpo: str = "",
    excepcion: Optional[BaseException] = None,
) -> Clase:
    """
    Determina la clase de un fallo.

    El CUERPO manda sobre el codigo HTTP, y esa precedencia es el punto: un 403
    con la pagina de Cloudflare es transitorio y hay que reintentar, mientras
    que un 403 de autorizacion es definitivo. Mirar solo el codigo confunde
    los dos y pierde radicaciones que si se reintentarian.

    Si no se recibe `estado`, se busca el codigo dentro del texto: los fallos
    que llegan envueltos en un `Exception` lo llevan ahi y en ninguna otra
    parte.
    """
    texto = " ".join(filter(None, [
        cuerpo or "",
        f"{type(excepcion).__name__}: {excepcion}" if excepcion else "",
    ]))

    hubo_respuesta = estado is not None
    for codigo, patron, solo_local in _FIRMAS:
        if solo_local and hubo_respuesta:
            # Vino de la red: no puede ser un fallo de la conversion local.
            continue
        if re.search(patron, texto, re.IGNORECASE):
            return POR_CODIGO[codigo]

    if estado is None:
        m = _ESTADO_EN_TEXTO.search(texto)
        if m and 100 <= int(m.group(1)) < 600:
            estado = int(m.group(1))

    if estado is not None:
        codigo = _POR_ESTADO.get(int(estado))
        if codigo:
            return POR_CODIGO[codigo]
        if 500 <= int(estado) < 600:
            return POR_CODIGO["ORIGEN_INALCANZABLE"]

    return DESCONOCIDO


# Tope por defecto de una sola espera. El job corre cada 30 minutos y atiende
# el lote entero dentro de esa ventana: una espera larga no la paga el correo
# que fallo, la pagan los que venian detras.
TOPE_DE_ESPERA_S = 30.0


def espera_para(clase: Clase, intento: int, tope: float = TOPE_DE_ESPERA_S) -> float:
    """
    Espera antes del siguiente intento, con retroceso exponencial.

    `intento` empieza en 1. `tope` acota una sola espera; quien conoce el
    tiempo que le queda a la corrida lo pasa mas bajo para no agotarlo
    esperando.
    """
    if not clase.reintentable or intento < 1:
        return 0.0
    return min(clase.espera_base_s * (2 ** (intento - 1)), max(0.0, tope))


def debe_reintentar(clase: Clase, intento: int) -> bool:
    """True si queda algun intento disponible para esta clase."""
    return clase.reintentable and intento < clase.max_intentos


# ── Registro de fallos ──

# Un mismo fallo tecnico significa cosas distintas segun donde ocurre: un
# timeout radicando dice que SGDEA no respondio, y uno pidiendole al modelo no
# dice nada de SGDEA. El motivo es lo que se escribe en la etiqueta del correo
# y lo que guia a quien revisa a mano.
MOTIVO_POR_PASO: Dict[Tuple[str, str], str] = {
    ("TIEMPO_AGOTADO", "MODELO"): "MODELO_NO_RESPONDIO",
    ("ORIGEN_INALCANZABLE", "MODELO"): "MODELO_NO_RESPONDIO",
    ("LIMITE_DE_TASA", "MODELO"): "MODELO_NO_RESPONDIO",
    ("DNS_NO_RESUELVE", "MODELO"): "MODELO_NO_RESPONDIO",
    ("DESCONOCIDO", "MODELO"): "MODELO_NO_RESPONDIO",
    ("SIN_PERMISO", "MODELO"): "MODELO_SIN_PERMISO",
    # DNS_NO_RESUELVE no distingue a que host no se pudo resolver: el mismo
    # texto de error cubre tanto un Redis fuera de servicio (una degradacion
    # sin consecuencia) como el propio SGDEA inalcanzable por su nombre en vez
    # de por su respuesta. Cuando ocurre radicando, la resolucion de nombres es
    # tan parte de "llegar a SGDEA" como la conexion misma, y se aplaza igual
    # que un backend caido: un corte de DNS no dura media hora.
    ("DNS_NO_RESUELVE", "RADICACION"): "SGDEA_CAIDO",
}


class FalloDelModelo(RuntimeError):
    """
    El modelo no pudo responder. Lleva el motivo con el que ir a revision.

    El motivo viaja dentro de la excepcion para que el manejador general no
    vuelva a clasificar el fallo: quien la lanza es el unico punto donde consta
    que el paso era el modelo.
    """

    def __init__(self, motivo: str, causa: BaseException) -> None:
        self.motivo = motivo
        super().__init__(f"{motivo}: {causa}")


@dataclass
class Fallo:
    """Un fallo concreto, ya clasificado."""

    clase: Clase
    paso: str
    detalle: str
    intento: int = 1
    # Tipo de la excepcion que lo origino. Solo se conserva cuando el fallo no
    # se pudo clasificar: es lo unico que permite escribir la firma que falta,
    # y sin el la clase DESCONOCIDO pide "mirar la traza" sin dejar ninguna.
    excepcion: str = ""

    @property
    def motivo_manual(self) -> str:
        """El motivo del catalogo, ajustado al paso donde ocurrio el fallo."""
        return MOTIVO_POR_PASO.get(
            (self.clase.codigo, self.paso), self.clase.motivo_manual)

    @property
    def linea_log(self) -> str:
        """
        El fallo en UN renglon.

        El detalle puede traer el cuerpo de una respuesta HTTP: la pagina de
        desafio de Cloudflare son varias lineas de HTML, y con sus saltos
        dentro el aviso partia en dos el informe de la corrida. Se colapsa el
        espacio en blanco antes de recortar, para que el recorte cuente
        caracteres utiles y no saltos de linea.
        """
        detalle = " ".join(str(self.detalle or "").split())
        tipo = f" [{self.excepcion}]" if self.excepcion else ""
        return (
            f"[{self.clase.severidad.value.upper()}] {self.clase.codigo} "
            f"en {self.paso}{tipo} (intento {self.intento}): {detalle[:200]}"
        )


@dataclass
class Bitacora:
    """
    Acumula los fallos de un correo.

    Existe para que la regla "ningun fallo en silencio" sea verificable: al
    final del procesamiento se pregunta a la bitacora, y si hay algo degradado
    queda en el informe aunque la radicacion haya salido bien.
    """

    fallos: List[Fallo] = field(default_factory=list)

    def registrar(
        self,
        paso: str,
        detalle: str,
        estado: Optional[int] = None,
        cuerpo: str = "",
        excepcion: Optional[BaseException] = None,
        intento: int = 1,
    ) -> Fallo:
        clase = clasificar(estado=estado, cuerpo=cuerpo, excepcion=excepcion)
        # Solo en lo que no se pudo clasificar: en el resto la clase ya nombra
        # la causa, y repetir el tipo alargaria cada linea sin añadir nada.
        tipo = (type(excepcion).__name__
                if excepcion is not None and clase is DESCONOCIDO else "")
        fallo = Fallo(clase=clase, paso=paso, detalle=detalle, intento=intento,
                      excepcion=tipo)
        self.fallos.append(fallo)
        return fallo

    @property
    def fatales(self) -> List[Fallo]:
        return [f for f in self.fallos if f.clase.severidad is Severidad.FATAL]

    @property
    def degradaciones(self) -> List[Fallo]:
        return [f for f in self.fallos if f.clase.severidad is Severidad.DEGRADA]

    @property
    def puede_radicar(self) -> bool:
        return not self.fatales

    def motivo_manual(self, paso: str = "") -> str:
        """
        Motivo con el que iria a revision manual, si aplica.

        Se toma del primer fatal con motivo. Si no hay ninguno y se nombra un
        `paso`, se cae a la primera degradacion DE ESE PASO: una degradacion no
        impide radicar en general, pero si ocurrio en el paso que se quedo sin
        completar —un DNS que no resuelve mientras se llama a SGDEA— es el
        unico motivo registrado, y sin este segundo turno el correo saldria con
        ERROR_INESPERADO en vez de con uno de los que se aplazan solos.

        La caida exige el paso a proposito. Sin acotarla, un adjunto ilegible de
        la conversion —que no impidio nada— se reportaria como la causa de que
        la radicacion no se completara.
        """
        for f in self.fatales:
            if f.motivo_manual:
                return f.motivo_manual
        if paso:
            for f in self.degradaciones:
                if f.paso == paso and f.motivo_manual:
                    return f.motivo_manual
        return ""

    def avisos(self) -> List[str]:
        """Lineas para el informe. Las degradaciones SIEMPRE aparecen."""
        return [f.linea_log for f in self.fallos]

    def resumen(self) -> str:
        if not self.fallos:
            return "sin fallos"
        return (
            f"{len(self.fatales)} fatal(es), "
            f"{len(self.degradaciones)} degradacion(es)"
        )
