"""
Modelos de radicacion PQRD
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Tuple


@dataclass
class ExtractedData:
    """Datos extraidos por IA"""
    tipo_remitente: int = 3  # 1: Familiar, 2: Tercero, 3: Afectado
    numero_documento_afectado: str = ""
    tipo_documento_afectado: int = 1
    numero_documento_remitente: str = ""
    tipo_documento_remitente: int = 1
    sexo_afectado: int = 1  # 1: Masculino, 2: Femenino, 3: Otro
    tipo_pqrd: int = 1
    tipo_solicitud: int = 14


@dataclass
class RadicacionPayload:
    """Payload para radicar PQRD"""
    # Datos del Remitente
    datos_remitente_tipo_remitente_id: int = 3
    datos_remitente_tipo_documento_id: int = 1
    datos_remitente_numero_documento: str = ""
    datos_remitente_nombre: str = ""

    # Datos del Afectado
    datos_afectado_tipo_documento_id: int = 1
    datos_afectado_numero_documento: str = ""
    datos_afectado_nombre: str = ""
    datos_afectado_lgtbtq: bool = False
    datos_afectado_genero_id: int = 3  # 3 = Otro (default seguro como curl)
    datos_afectado_correo_electronico: str = ""
    datos_afectado_telefono_celular: str = ""
    datos_afectado_pais_id_pais: int = 425
    datos_afectado_departamento_id_departamento: int = 6
    datos_afectado_municipio_id_municipio: Optional[int] = 149
    datos_afectado_direccion: str = ""
    datos_afectado_medio_respuesta_id: int = 1

    # Datos de la Solicitud
    solicitud_tipo_pqrd_id: int = 1
    solicitud_tipo_solicitud_id: int = 14
    solicitud_tipo_producto_id: int = 15
    solicitud_acceso_informacion_publica: bool = False
    solicitud_descripcion_acceso_informacion_id: int = 2
    solicitud_observaciones: str = ""
    solicitud_detalle: str = ""

    # Datos de Control
    usuario_creador: str = "Asignador IA"
    canal_radicacion: int = 7  # 7 = EMAIL-IA (backend usa template web)
    oficina: int = 15  # Oficina por defecto, se sobreescribe desde tabla pqrd_config_tipo_solicitud_oficina_ia
    detalle_afectado: str = ""
    listado_condiciones: str = "[1]"  # [1] como en curl produccion

    def to_form_data(self) -> Dict[str, Any]:
        """Convierte el payload a diccionario para form-data

        IMPORTANTE: Mantener tipos nativos (int, bool) para que Spring Boot
        pueda deserializar correctamente. NO convertir todo a string.
        """
        return {
            # Tipos nativos para números - Spring Boot los convierte automáticamente
            "datosRemitenteTipoRemitenteId": self.datos_remitente_tipo_remitente_id,
            "datosRemitenteTipoDocumentoId": self.datos_remitente_tipo_documento_id,
            "datosRemitenteNumeroDocumento": self.datos_remitente_numero_documento,
            "datosRemitenteNombre": self.datos_remitente_nombre,

            "datosAfectadoTipoDocumentoId": self.datos_afectado_tipo_documento_id,
            "datosAfectadoNumeroDocumento": self.datos_afectado_numero_documento,
            "datosAfectadoNombre": self.datos_afectado_nombre,
            "datosAfectadoLgtbtq": self.datos_afectado_lgtbtq,  # Boolean nativo
            "datosAfectadoGeneroId": self.datos_afectado_genero_id,
            "datosAfectadoCorreoElectronico": self.datos_afectado_correo_electronico,
            "datosAfectadoTelefonoCelular": self.datos_afectado_telefono_celular,
            "datosAfectadoPaisIdPais": self.datos_afectado_pais_id_pais,
            "datosAfectadoDepartamentoIdDepartamento": self.datos_afectado_departamento_id_departamento,
            # Se envia lo que decidio quien armo el payload. Sustituir aqui un
            # valor vacio por un municipio concreto afirma que la persona vive
            # en un sitio, y lo hace en el ultimo paso, donde no queda en las
            # suposiciones del informe: el radicado sale con un municipio que
            # nadie leyo y nada lo dice.
            "datosAfectadoMunicipioIdMunicipio": self.datos_afectado_municipio_id_municipio,
            "datosAfectadoDireccion": self.datos_afectado_direccion,
            "datosAfectadoMedioRespuestaId": self.datos_afectado_medio_respuesta_id,

            "solicitudTipoPqrdId": self.solicitud_tipo_pqrd_id,
            "solicitudTipoSolicitudId": self.solicitud_tipo_solicitud_id,
            "solicitudTipoproductoId": self.solicitud_tipo_producto_id,
            "solicitudAccesoInformacionPublica": self.solicitud_acceso_informacion_publica,  # Boolean nativo
            "solicitudDescripcionAccesoInformacionId": self.solicitud_descripcion_acceso_informacion_id,
            "solicitudObservaciones": self.solicitud_observaciones,
            "solicitudDetalle": self.solicitud_detalle,

            "usuarioCreador": self.usuario_creador,
            "canalRadicacion": self.canal_radicacion,
            "detalleAfectado": self.detalle_afectado,
            "listadoCondiciones": self.listado_condiciones,
            "oficina": self.oficina,
        }


@dataclass
class RadicacionResponse:
    """Respuesta de radicacion exitosa"""
    id: int = 0
    id_radicado: str = ""
    fecha_radicacion: str = ""
    remitente: Dict[str, Any] = field(default_factory=dict)
    afectado: Dict[str, Any] = field(default_factory=dict)
    solicitud: Dict[str, Any] = field(default_factory=dict)
    # Nombres de los campos que trajo la respuesta, sin sus valores. Cuando el
    # numero de radicado no viene, es lo unico que permite saber si el backend
    # no lo mando o si lo mando con otro nombre. Solo los nombres: el cuerpo
    # lleva los datos de una persona y no tiene por que acabar en un registro.
    claves_recibidas: Tuple[str, ...] = ()

    @classmethod
    def from_api_response(cls, data: dict) -> "RadicacionResponse":
        """Crea RadicacionResponse desde respuesta de API"""
        return cls(
            id=data.get("id", 0),
            id_radicado=data.get("idRadicado", ""),
            fecha_radicacion=data.get("fechaRadicacion", ""),
            remitente=data.get("remitente", {}),
            afectado=data.get("afectado", {}),
            solicitud=data.get("solicitud", {}),
            claves_recibidas=tuple(sorted(data)) if isinstance(data, dict) else (),
        )


@dataclass
class AsignacionPayload:
    """Payload para asignar PQRD"""
    id_asignador: int = 0
    id_gestionador: int = 0
    motivo_asignacion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convierte a diccionario"""
        return {
            "idAsignador": self.id_asignador,
            "idGestionador": self.id_gestionador,
            "motivoAsignacion": self.motivo_asignacion,
        }


@dataclass
class TrazabilidadPayload:
    """Payload para crear trazabilidad"""
    proceso: str = ""
    secuencia: str = ""
    estado: str = ""
    descripcion: str = ""
    comentario: str = ""
    nombre: str = ""
    accion: str = ""
    tramite: str = ""

    def to_dict(self) -> Dict[str, str]:
        """Convierte a diccionario"""
        return {
            "proceso": self.proceso,
            "secuencia": self.secuencia,
            "estado": self.estado,
            "descripcion": self.descripcion,
            "comentario": self.comentario,
            "nombre": self.nombre,
            "accion": self.accion,
            "tramite": self.tramite,
        }
