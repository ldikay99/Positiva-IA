"""
Servicio de Supabase para persistencia
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Any
from supabase import create_client, Client
from config import settings
from config.settings import dedup_desactivada

logger = logging.getLogger(__name__)

# La tabla guarda la fecha sin zona y el correo la trae en UTC: el margen evita
# que esa diferencia deje por fuera un radicado del mismo dia.
MARGEN_DE_ZONA_HORARIA = timedelta(days=1)


def _piso_de_fecha(recibido: str) -> str:
    """Desde cuando buscar radicados de un correo, o vacio si no hay fecha valida."""
    try:
        llegada = datetime.fromisoformat((recibido or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (llegada.replace(tzinfo=None) - MARGEN_DE_ZONA_HORARIA).isoformat()


class SupabaseService:
    """Cliente Supabase para guardar radicados"""
    
    def __init__(self):
        # MODO_TEST: no crear cliente real (permite credenciales dummy).
        if settings.MODO_TEST:
            self._client = None
            return
        self._client: Client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_ROLE_KEY,
        )

    # Ultimo fallo de una consulta, o cadena vacia. `False` significa a la vez
    # "no hay duplicado" y "no se pudo comprobar": el llamador nuevo lee esto
    # para distinguirlas y dejar constancia de que la capa no comprobo nada.
    ultimo_error: str = ""

    async def email_exists(
        self, correo_remitente: str, asunto: str, recibido: str = ""
    ) -> bool:
        """
        Verifica si ya existe un radicado con el mismo correo y asunto.

        Con `recibido` solo cuentan los radicados posteriores a la llegada del
        correo: uno anterior no puede ser este correo, y esta tabla ya no recibe
        registros nuevos, asi que sin el limite bloquea para siempre a quien
        repita un asunto.
        """
        self.ultimo_error = ""
        if dedup_desactivada():
            return False
        try:
            consulta = self._client.table("radicados_pqrd") \
                .select("numero_radicado") \
                .eq("correo_remitente", correo_remitente) \
                .eq("asunto", asunto)
            piso = _piso_de_fecha(recibido)
            if piso:
                consulta = consulta.gte("fecha_radicacion", piso)
            response = consulta.limit(1).execute()

            return len(response.data) > 0
        except Exception as e:
            # Si falla la consulta, retornar False para no bloquear
            logger.warning("Error verificando duplicado en Supabase: %s", e)
            self.ultimo_error = f"{type(e).__name__}: {e}"
            return False

    async def radicado_exists_by_doc(self, numero_documento: str, asunto: str) -> bool:
        """Verifica si ya existe un radicado con el mismo documento del afectado y asunto similar"""
        if settings.MODO_TEST:
            return False
        try:
            # Limpiar prefijos de reenvio del asunto
            asunto_limpio = asunto
            for prefijo in ["RV: ", "RE: ", "FW: ", "Fwd: ", "RV:", "RE:", "FW:", "Fwd:"]:
                asunto_limpio = asunto_limpio.replace(prefijo, "").strip()

            response = self._client.table("radicados_pqrd") \
                .select("numero_radicado") \
                .eq("numero_documento_afectado", numero_documento) \
                .ilike("asunto", f"%{asunto_limpio[:80]}%") \
                .limit(1) \
                .execute()

            return len(response.data) > 0
        except Exception:
            return False

    async def save_radicado_record(
        self,
        numero_radicado: str,
        fecha_radicacion: str,
        asunto: str = "",
        correo_remitente: str = "",
        asignado_automatico: bool = False,
        persona_asignada: str = "",
        email_hash: str = "",
        email_id: str = "",
        numero_documento_afectado: str = "",
    ) -> None:
        """Guarda un registro de radicado en Supabase"""
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().registrar(
                "SUPABASE_SAVE", destino="radicados_pqrd",
                detalle={
                    "numero_radicado": numero_radicado,
                    "correo_remitente": correo_remitente,
                    "numero_documento_afectado": numero_documento_afectado,
                },
            )
            return
        record = {
            "numero_radicado": numero_radicado,
            "asunto": asunto,
            "correo_remitente": correo_remitente,
            "fecha_radicacion": fecha_radicacion,
            "asignado_automatico": asignado_automatico,
            "persona_asignada": persona_asignada if persona_asignada else None,
            "numero_documento_afectado": numero_documento_afectado or "",
        }

        # Nota: email_hash y email_id no se guardan porque la tabla no tiene esas columnas
        # Si en el futuro se agregan a la tabla, descomentar estas líneas:
        # if email_hash:
        #     record["email_hash"] = email_hash
        # if email_id:
        #     record["email_id"] = email_id

        self._client.table("radicados_pqrd").insert(record).execute()
    
    async def get_today_stats(self) -> Dict[str, int]:
        """Obtiene estadisticas de radicados del dia"""
        if settings.MODO_TEST:
            return {"total": 0, "exitosos": 0, "fallidos": 0, "asignados": 0}
        today = datetime.now().strftime("%Y-%m-%d")

        response = self._client.table("radicados_pqrd").select("numero_radicado,asignado_automatico").gte(
            "fecha_radicacion", f"{today}T00:00:00"
        ).lte("fecha_radicacion", f"{today}T23:59:59").execute()

        data = response.data or []
        total = len(data)
        asignados = sum(1 for r in data if r.get("asignado_automatico"))

        return {"total": total, "exitosos": total, "fallidos": 0, "asignados": asignados}

    def close(self) -> None:
        """Cierra la conexion del cliente Supabase"""
        # Supabase client no tiene close() explicito, pero limpiamos la referencia
        self._client = None
