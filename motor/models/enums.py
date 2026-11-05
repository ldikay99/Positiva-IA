"""
Enumeraciones y constantes del sistema PQRD
"""
from enum import IntEnum
from typing import Dict


class TipoDocumento(IntEnum):
    """Tipos de documento de identificacion"""
    CC = 1   # Cedula Ciudadania
    CE = 2   # Cedula Extranjeria
    CD = 3   # Carne Diplomatico
    PPT = 4  # Permiso por Proteccion Temporal
    PE = 5   # Permiso Especial de Permanencia
    TI = 6   # Tarjeta de Identidad
    NP = 7   # Numero Unico de Identificacion Personal (NUIP)
    SE = 8   # Sociedad Extranjera sin NIT en Colombia
    FI = 9   # Fideicomisos
    NI = 10  # Numero de Identificacion Tributaria (NIT)
    PA = 11  # Pasaporte
    NA = 12  # Ninguno


class TipoPqrd(IntEnum):
    """Tipos de PQRD"""
    SOLICITUD = 1
    DERECHO_PETICION = 2
    FELICITACION = 3
    SUGERENCIA = 4
    QUEJA = 5
    RECLAMO = 6
    PQRD_EXPRESS = 7
    DERECHO_PETICION_VERBAL = 8


class TipoSolicitud(IntEnum):
    """Tipos de solicitud PQRD. Espejo de pqrd_config_tipo_solicitud_oficina_ia."""
    ACTUALIZACION_DATOS = 1
    ASESORIA_SGSST = 3
    ASISTENTE_VIRTUAL = 4
    AUTORIZACION_MEDICA = 5
    CALL_CENTER = 6
    CONTROVERSIA = 7
    ESTADO_CUENTA_ARL = 8
    FACTURACION_VIDA = 9
    INTERNET = 10
    MODIFICACIONES_VIDA = 11
    NOVEDADES_AFILIACION = 12
    OFICINAS = 13
    OTRA = 14
    PAGO_REEMBOLSO = 15
    PAGO_INCAPACIDAD = 16
    PENSIONES = 17
    RECLAMACIONES_VIDA = 18
    RENOVACIONES_VIDA = 19
    IVR = 20
    SOLICITUD_MEDICAMENTOS = 21
    SUSCRIPCION_VIDA = 22
    TRASLADO_SERVICIO = 23
    APORTE_PRUEBAS_AT = 24
    APLICACIONES_MOVILES = 25
    PAGO_APORTES_ARL = 26
    ACCAI = 27
    CUENTAS_MEDICAS = 28


# Mapeos de codigo
TIPO_DOCUMENTO_CODIGO: Dict[int, str] = {
    1: "CC",
    2: "CE",
    3: "CD",
    4: "PT",
    5: "PE",
    6: "TI",
    7: "NP",
    8: "SE",
    9: "FI",
    10: "NI",
    11: "PA",
    12: "NA",
}

CODIGO_TO_TIPO_DOCUMENTO: Dict[str, int] = {
    "CC": 1,
    "CE": 2,
    "CD": 3,
    "PT": 4,
    "PPT": 4,
    "PE": 5,
    "TI": 6,
    "NP": 7,
    "SE": 8,
    "FI": 9,
    "NI": 10,
    "PA": 11,
    "NA": 12,
}

TIPO_PQRD_NOMBRE: Dict[int, str] = {
    1: "Solicitud",
    2: "Derecho de Peticion",
    3: "Felicitacion",
    4: "Sugerencia",
    5: "Queja",
    6: "Reclamo",
    7: "PQRD Express",
    8: "Derecho de peticion verbal",
}

TIPO_SOLICITUD_NOMBRE: Dict[int, str] = {
    1: "Actualizacion de datos en la afiliacion",
    3: "Asesoria en SGSST",
    4: "Asistente Virtual",
    5: "Autorizacion medica",
    6: "Centro de Atencion telefonica",
    7: "Controversia",
    8: "Estado de Cuenta Aportes ARL",
    9: "Facturacion y cobro Polizas de Vida",
    10: "Internet",
    11: "Modificaciones Polizas de Vida",
    12: "Novedades de Afiliacion",
    13: "Oficinas",
    14: "Otra",
    15: "Pago de Reembolso",
    16: "Pago de Incapacidad",
    17: "Pensiones",
    18: "Reclamaciones Polizas de Vida",
    19: "Renovaciones Polizas de Vida",
    20: "IVR",
    21: "Solicitud de medicamentos",
    22: "Suscripcion de Polizas de Vida",
    23: "Traslado para prestacion de servicio",
    24: "Aporte de Pruebas (AT)",
    25: "Aplicaciones moviles",
    26: "Pago de Aportes ARL",
    27: "ACCAI",
    28: "Cuentas medicas",
}


def get_tipo_documento_codigo(tipo_id: int) -> str:
    """Obtiene el codigo del tipo de documento"""
    return TIPO_DOCUMENTO_CODIGO.get(tipo_id, "CC")


def get_tipo_documento_id(codigo: str) -> int:
    """Obtiene el ID del tipo de documento por codigo"""
    return CODIGO_TO_TIPO_DOCUMENTO.get(codigo.upper(), 1)


def get_tipo_pqrd_nombre(tipo_id: int) -> str:
    """Obtiene el nombre del tipo de PQRD"""
    return TIPO_PQRD_NOMBRE.get(tipo_id, "Solicitud")


def get_tipo_solicitud_nombre(tipo_id: int) -> str:
    """Obtiene el nombre del tipo de solicitud"""
    return TIPO_SOLICITUD_NOMBRE.get(tipo_id, "Otra")
