"""
Motor de asignacion de funcionarios

Incluye metodo async para resolver asignacion desde API con fallback a logica hardcoded.
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, TYPE_CHECKING
import random

from .funcionario_directory import get_funcionario_id, ASIGNADOR_IA_ID
from .sucursal_config import normalize_sucursal, get_sucursal_config

if TYPE_CHECKING:
    from services.asignacion_ia_client import AsignacionIAClient

@dataclass
class AssignmentResult:
    datos_caso: dict = field(default_factory=dict)
    validacion: dict = field(default_factory=dict)
    asignacion: Optional[dict] = None

def calcular_paridad(documento: str) -> bool:
    if not documento:
        return False
    try:
        ultimo_digito = int(str(documento).strip()[-1])
        return ultimo_digito % 2 == 0
    except (ValueError, IndexError):
        return False

def seleccionar_funcionario(lista_candidatos: List[str], documento: str) -> Optional[str]:
    if not lista_candidatos:
        return None
    if len(lista_candidatos) == 1:
        return lista_candidatos[0]
    try:
        ultimo_digito = int(str(documento).strip()[-1])
        if ultimo_digito in (0, 1):
            return random.choice(lista_candidatos)
        elif ultimo_digito >= 6:
            return lista_candidatos[0]
        else:
            return lista_candidatos[1]
    except (ValueError, IndexError):
        return lista_candidatos[0]

def determinar_grupo(tipo_solicitud: str) -> Optional[str]:
    tipo_upper = tipo_solicitud.upper().strip()
    if "ACTUALIZACION" in tipo_upper or "NOVEDADES" in tipo_upper:
        return "ACTUALIZACION"
    if "ESTADO" in tipo_upper or "PAGO DE APORTES" in tipo_upper:
        return "FINANCIERA"
    if "APORTE" in tipo_upper or "ACCIDENTE" in tipo_upper:
        return "ACCIDENTES"
    return None

class AssignmentEngine:
    @staticmethod
    def process_assignment(radicado: str, documento: str, tipo_solicitud: str, sucursal: str) -> AssignmentResult:
        tipo_solicitud_upper = tipo_solicitud.upper().strip()
        sucursal_normalizada = normalize_sucursal(sucursal)

        result = AssignmentResult(
            datos_caso={"radicado": radicado, "documento": documento, "tipoSolicitud": tipo_solicitud_upper, "sucursal": sucursal},
            validacion={"aplica": True, "requiereIA": False, "motivo": ""},
            asignacion=None,
        )

        config_sucursal = get_sucursal_config(sucursal_normalizada)
        if not config_sucursal:
            result.validacion["aplica"] = False
            result.validacion["motivo"] = f"Sucursal '{sucursal}' no tiene configuracion de asignacion."
            return result

        if "ASESORIA" in tipo_solicitud_upper or "SGSST" in tipo_solicitud_upper:
            result.validacion["requiereIA"] = True
            result.validacion["motivo"] = "Requiere validacion IA (SGSST)"
            return result

        es_par = calcular_paridad(documento)
        paridad_txt = "PAR" if es_par else "IMPAR"
        grupo = determinar_grupo(tipo_solicitud)

        if not grupo:
            result.validacion["aplica"] = False
            result.validacion["motivo"] = f"Tipo '{tipo_solicitud}' no valido para asignacion automatica."
            return result

        grupo_destino = config_sucursal.get(grupo, {})
        lista_candidatos = grupo_destino.get("PARES" if es_par else "IMPARES", [])
        nombre_funcionario = seleccionar_funcionario(lista_candidatos, documento)

        if not nombre_funcionario:
            result.validacion["aplica"] = False
            result.validacion["motivo"] = f"No hay funcionarios disponibles para {grupo} en {sucursal_normalizada}"
            return result

        id_funcionario = get_funcionario_id(nombre_funcionario)
        result.validacion["motivo"] = f"Asignacion {sucursal_normalizada}: {grupo} (Doc {documento} es {paridad_txt})"
        result.asignacion = {
            "idFuncionario": id_funcionario,
            "nombreFuncionario": nombre_funcionario,
            "idAsignador": ASIGNADOR_IA_ID,
        }
        return result

    @staticmethod
    def is_sgsst(tipo_solicitud: str) -> bool:
        upper = tipo_solicitud.upper()
        return "ASESORIA" in upper or "SGSST" in upper

    @staticmethod
    def is_otra(tipo_solicitud: str) -> bool:
        return "OTRA" in tipo_solicitud.upper()


async def resolver_asignacion_desde_api(
    tipo_solicitud_id: int,
    departamento_id: int,
    client: "AsignacionIAClient",
) -> Optional[Dict[str, Any]]:
    """Resuelve la asignacion de funcionario via API de configuracion.

    Llama al endpoint /configuracion-asignacion-ia/resolver del backend
    y retorna un diccionario con los datos del funcionario asignado.

    Args:
        tipo_solicitud_id: ID del tipo de solicitud PQRD.
        departamento_id: ID del departamento del afectado.
        client: Instancia de AsignacionIAClient.

    Returns:
        Dict con claves: funcionarioId, funcionarioNombre, oficinaId,
        oficinaNombre, departamentoId, orden.
        None si el API falla o no tiene configuracion para esos parametros.
    """
    try:
        resultado = await client.resolver_asignacion(tipo_solicitud_id, departamento_id)
        if resultado and resultado.get("funcionarioId"):
            return resultado
        return None
    except Exception:
        return None
