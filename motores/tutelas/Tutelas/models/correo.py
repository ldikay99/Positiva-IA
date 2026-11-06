from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime, timezone, timedelta


@dataclass
class Adjunto:
    """Representa un archivo adjunto del correo"""
    id: str
    nombre: str
    content_type: str
    contenido: bytes = field(default=b"", repr=False)

    def es_imagen_outlook(self) -> bool:
        """Verifica si es una imagen incrustada del correo (se debe filtrar).

        Las firmas de los despachos llegan como imagen adjunta con nombres
        genericos (image.png, image001.jpg) que no mencionan a Outlook, de modo
        que exigir esa palabra dejaba pasar el logo como si fuera un documento
        del expediente.
        """
        return "image" in self.content_type.lower()

    def es_documento_expediente(self) -> bool:
        """Verifica si el adjunto es documentacion radicable del expediente.

        Solo cuenta el PDF, formato en que los despachos remiten las actuaciones.
        Deja fuera firmas, imagenes incrustadas y cualquier otro archivo accesorio.
        """
        return self.nombre.lower().endswith(".pdf")


@dataclass
class Juzgado:
    """Información del juzgado extraída"""
    id_juzgado: Optional[int] = None
    nombre: str = ""
    direccion: str = ""
    pais: str = "Colombia"
    departamento: str = ""
    municipio: str = ""
    codigo: str = ""
    telefono: str = ""
    celular: str = ""
    correo: str = ""


@dataclass
class DatosExtraidos:
    """Datos extraídos del correo por AI"""
    radicado_23: Optional[str] = None
    codigo_juzgado: Optional[str] = None
    juzgado_nombre_ocr: Optional[str] = None
    ciudad: Optional[str] = None
    es_valido: bool = False

    # Datos del afectado
    numero_documento: Optional[str] = None
    tipo_documento: str = "CC"
    fuente_documento: Optional[str] = None


@dataclass
class Correo:
    """Representa un correo a procesar"""
    id: str
    subject: str
    body_content: str
    body_text: str  # Texto limpio sin HTML
    remitente_email: str
    remitente_nombre: str
    fecha_recibido: datetime
    conversation_id: Optional[str] = None
    adjuntos: List[Adjunto] = field(default_factory=list)

    # Validaciones
    es_dominio_valido: bool = False
    es_tutela: bool = False
    hash_correo: Optional[str] = None
    # Llaves de deduplicacion de cada documento del hilo, para liberarlas si la
    # radicacion no llega a completarse.
    hashes_documentos: List[str] = field(default_factory=list)
    es_duplicado: bool = False

    # Datos extraídos
    datos_extraidos: Optional[DatosExtraidos] = None
    juzgado: Optional[Juzgado] = None

    # Resultado de radicación
    radicado_exitoso: bool = False
    id_tutela: Optional[int] = None
    id_radicado: Optional[str] = None
    error_radicacion: Optional[str] = None

    @property
    def fecha_recibido_local(self) -> datetime:
        """fecha_recibido convertida a hora Colombia (UTC-5, sin horario de verano).

        Microsoft Graph entrega receivedDateTime en UTC. Formatearla sin convertir
        producia un desfase de +5h en la radicacion. Si por alguna razon la fecha
        llega naive (sin zona), se asume UTC antes de convertir.
        """
        dt = self.fecha_recibido
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=-5)))

    def get_dominio_remitente(self) -> str:
        """Extrae el dominio del email del remitente"""
        if "@" in self.remitente_email:
            return self.remitente_email.split("@")[1].lower()
        return ""

    def get_adjuntos_validos(self) -> List[Adjunto]:
        """Retorna adjuntos filtrados (sin imágenes de Outlook)"""
        return [adj for adj in self.adjuntos if not adj.es_imagen_outlook()]
