"""
Mapeo de Tipo de Solicitud a Oficinas SGDEA

Datos tomados de la tabla pqrd_config_tipo_solicitud_oficina_ia de PRD.
Cuando el endpoint /configuracion-asignacion-ia/tipo-solicitud-oficina este
desplegado en produccion, reemplazar este mapeo por la consulta al API.
"""
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from services.asignacion_ia_client import AsignacionIAClient

# Datos de produccion: tabla pqrd_config_tipo_solicitud_oficina_ia (PRD = UAT)
# Ultima sincronizacion: 2026-04-01
OFFICE_MAPPING: Dict[int, int] = {
    1:  55,  # Actualizacion de datos en la afiliacion -> GERENCIA DE AFILIACIONES Y NOVEDADES
    3:  55,  # Asesoria en SGSST -> GERENCIA DE AFILIACIONES Y NOVEDADES
    4:  15,  # Asistente Virtual -> GRUPO DE ATENCION INTEGRAL Y DE SERVICIO AL CIUDADANO
    5:   6,  # Autorizacion medica -> GRUPO PQRD MEDICA
    6:  15,  # Centro de Atencion telefonica -> GRUPO DE ATENCION INTEGRAL Y DE SERVICIO AL CIUDADANO
    7:   6,  # Controversia -> GRUPO PQRD MEDICA
    8:  63,  # Estado de Cuenta Aportes ARL -> GERENCIA DE RECAUDO Y CARTERA
    9:  91,  # Facturacion y cobro Polizas de Vida -> GERENCIA TECNICA DE VIDA
    10: 15,  # Internet -> GRUPO DE ATENCION INTEGRAL Y DE SERVICIO AL CIUDADANO
    11: 91,  # Modificaciones Polizas de Vida -> GERENCIA TECNICA DE VIDA
    12: 55,  # Novedades de Afiliacion -> GERENCIA DE AFILIACIONES Y NOVEDADES
    13: 15,  # Oficinas -> GRUPO DE ATENCION INTEGRAL Y DE SERVICIO AL CIUDADANO
    14: 15,  # Otra -> GRUPO DE ATENCION INTEGRAL Y DE SERVICIO AL CIUDADANO
    15:  2,  # Pago de Reembolso -> GRUPO PQRD INDEMNIZACIONES
    16:  2,  # Pago de Incapacidad -> GRUPO PQRD INDEMNIZACIONES
    17:  3,  # Pensiones -> GRUPO ADMINISTRACION DE PENSIONES
    18:  4,  # Reclamaciones Polizas de Vida -> GRUPO RECLAMACIONES VIDA
    19: 91,  # Renovaciones Polizas de Vida -> GERENCIA TECNICA DE VIDA
    20: 15,  # IVR -> GRUPO DE ATENCION INTEGRAL Y DE SERVICIO AL CIUDADANO
    21:  6,  # Solicitud de medicamentos -> GRUPO PQRD MEDICA
    22: 91,  # Suscripcion de Polizas de Vida -> GERENCIA TECNICA DE VIDA
    23:  6,  # Traslado para prestacion de servicio -> GRUPO PQRD MEDICA
    24: 96,  # Aporte de Pruebas (AT) -> VICEPRESIDENCIA DE PROMOCION Y PREVENCION
    25: 15,  # Aplicaciones moviles -> GRUPO DE ATENCION INTEGRAL Y DE SERVICIO AL CIUDADANO
    26: 63,  # Pago de Aportes ARL -> GERENCIA DE RECAUDO Y CARTERA
    27: 57,  # ACCAI -> GERENCIA DE EXPERIENCIA DEL CLIENTE
    28: 59,  # Cuentas medicas -> GERENCIA DE INDEMNIZACIONES
}


# Tipos de solicitud: misma tabla, columna de descripcion.
TIPO_SOLICITUD_NOMBRES: Dict[int, str] = {
    1:  "Actualizacion de datos en la afiliacion",
    3:  "Asesoria en SGSST",
    4:  "Asistente Virtual",
    5:  "Autorizacion medica",
    6:  "Centro de Atencion telefonica",
    7:  "Controversia",
    8:  "Estado de Cuenta Aportes ARL",
    9:  "Facturacion y cobro Polizas de Vida",
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


# Nombres de oficinas: tabla oficina de PRD
# Ultima sincronizacion: 2026-04-01
OFFICE_NAMES: Dict[int, str] = {
    2:  "GRUPO PQRD INDEMNIZACIONES",
    3:  "GRUPO ADMINISTRACIÓN DE PENSIONES",
    4:  "GRUPO RECLAMACIONES VIDA",
    6:  "GRUPO PQRD MÉDICA",
    13: "Mesa Back Medicina Laboral",
    15: "GRUPO DE ATENCIÓN INTEGRAL Y DE SERVICIO AL CIUDADANO",
    55: "GERENCIA DE AFILIACIONES Y NOVEDADES",
    57: "GERENCIA DE EXPERIENCIA DEL CLIENTE",
    59: "GERENCIA DE INDEMNIZACIONES",
    63: "GERENCIA DE RECAUDO Y CARTERA",
    91: "GERENCIA TÉCNICA DE VIDA",
    96: "VICEPRESIDENCIA DE PROMOCIÓN Y PREVENCIÓN",
}


def get_office_id(tipo_solicitud_id: int) -> Optional[int]:
    """Retorna la oficina para el tipo de solicitud. None si no existe."""
    return OFFICE_MAPPING.get(tipo_solicitud_id)


def get_office_name(oficina_id: int) -> str:
    """Retorna el nombre de la oficina por ID."""
    return OFFICE_NAMES.get(oficina_id, "OFICINA DESCONOCIDA")


async def get_office_id_from_api(
    tipo_solicitud_id: int, client: "AsignacionIAClient"
) -> Optional[int]:
    """Consulta la oficina destino desde el API de configuracion.

    Lee de la tabla pqrd_config_tipo_solicitud_oficina_ia.
    Retorna None si el API falla o no tiene datos.
    """
    try:
        oficina_id = await client.obtener_oficina_por_tipo(tipo_solicitud_id)
        return oficina_id
    except Exception:
        return None
