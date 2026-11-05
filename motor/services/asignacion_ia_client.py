"""
Cliente para consumir el API de configuracion de asignaciones IA en MS_SGDEA
"""
import time
import httpx
from typing import Optional, Dict, Any

from config import settings
from logging_system import RichLogger


class AsignacionIAClient:
    """Cliente para consumir el API de configuracion de asignaciones IA en MS_SGDEA"""

    def __init__(self, verbose: bool = False):
        self._base_url: str = settings.CONFIGURACION_ASIGNACION_IA_URL
        self._token: Optional[str] = None
        self._token_expires_at: float = 0
        self._client = httpx.AsyncClient(timeout=30)
        self.logger = RichLogger(verbose=verbose)

    async def close(self) -> None:
        """Cierra el cliente HTTP compartido"""
        await self._client.aclose()

    def _is_token_valid(self) -> bool:
        """Verifica si el token existe y no esta por expirar (margen de 60 segundos)"""
        return bool(self._token) and time.time() < (self._token_expires_at - 60)

    def _invalidate_token(self) -> None:
        """Invalida el token actual para forzar renovacion"""
        self._token = None
        self._token_expires_at = 0

    async def _get_token(self, force_refresh: bool = False) -> str:
        """Obtiene token de acceso SGDEA via Keycloak (mismo patron que RadicacionService)"""
        if not force_refresh and self._is_token_valid():
            return self._token

        token_url = settings.SGDEA_TOKEN_URL
        payload = {
            "grant_type": "password",
            "client_id": settings.SGDEA_CLIENT_ID,
            "client_secret": settings.SGDEA_CLIENT_SECRET,
            "username": settings.SGDEA_USERNAME,
            "password": settings.SGDEA_PASSWORD,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        response = await self._client.post(token_url, data=payload, headers=headers, timeout=60)
        #response.raise_for_status()
        if response.status_code >= 400:
            raise Exception(
                f"ERROR TOKEN ASIGNACION IA {response.status_code} | "
                f"url={token_url} | body={response.text}"
            )
        token_data = response.json()

        self._token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 300)
        self._token_expires_at = time.time() + expires_in
        return self._token

    async def _get_auth_headers(self, force_refresh: bool = False) -> Dict[str, str]:
        """Obtiene headers de autorizacion con token valido"""
        token = await self._get_token(force_refresh=force_refresh)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def resolver_asignacion(
        self, tipo_solicitud_id: int, departamento_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Llama GET /configuracion-asignacion-ia/resolver?tipoSolicitudId=X&departamentoId=Y

        Retorna dict con: funcionarioId, funcionarioNombre, oficinaId,
        oficinaNombre, departamentoId, orden.
        Retorna None si el API falla o no tiene configuracion.
        """
        if not self._base_url:
            self.logger.step_warning(
                "CONFIGURACION_ASIGNACION_IA_URL no configurada, saltando asignacion via API"
            )
            return None

        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                force_refresh = attempt > 0
                headers = await self._get_auth_headers(force_refresh=force_refresh)

                response = await self._client.get(
                    f"{self._base_url}/configuracion-asignacion-ia/resolver",
                    params={
                        "tipoSolicitudId": tipo_solicitud_id,
                        "departamentoId": departamento_id,
                    },
                    headers=headers,
                )

                if response.status_code == 401 and attempt < max_attempts - 1:
                    self._invalidate_token()
                    continue

                if response.status_code == 404:
                    self.logger.step_info(
                        f"No hay configuracion de asignacion para tipoSolicitud={tipo_solicitud_id}, "
                        f"departamento={departamento_id}"
                    )
                    return None

                #response.raise_for_status()
                if response.status_code >= 400:
                    raise Exception(
                        f"ERROR TOKEN ASIGNACION IA {response.status_code} | "
                        f"url={token_url} | body={response.text}"
                    )
                return response.json()

            except httpx.TimeoutException:
                self.logger.step_warning(
                    f"Timeout consultando asignacion IA (intento {attempt + 1}/{max_attempts})"
                )
                if attempt < max_attempts - 1:
                    continue
                return None
            except httpx.ConnectError as e:
                self.logger.step_warning(
                    f"Error de conexion con API asignacion IA: {e}"
                )
                return None
            except httpx.HTTPStatusError as e:
                self.logger.step_warning(
                    f"Error HTTP {e.response.status_code} consultando asignacion IA: {e.response.text}"
                )
                return None
            except Exception as e:
                self.logger.step_warning(
                    f"Error inesperado consultando asignacion IA: {e}"
                )
                return None

        return None

    async def obtener_oficina_por_tipo(self, tipo_solicitud_id: int) -> Optional[int]:
        """
        Llama GET /configuracion-asignacion-ia/tipo-solicitud-oficina
        Filtra por tipoSolicitudId y retorna el oficinaId.
        Retorna None si el API falla.
        """
        if not self._base_url:
            return None

        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                force_refresh = attempt > 0
                headers = await self._get_auth_headers(force_refresh=force_refresh)

                response = await self._client.get(
                    f"{self._base_url}/configuracion-asignacion-ia/tipo-solicitud-oficina",
                    params={"tipoSolicitudId": tipo_solicitud_id},
                    headers=headers,
                )

                if response.status_code == 401 and attempt < max_attempts - 1:
                    self._invalidate_token()
                    continue

                if response.status_code == 404:
                    return None

                #response.raise_for_status()
                url = f"{self._base_url}/configuracion-asignacion-ia/tipo-solicitud-oficina"

                if response.status_code >= 400:
                    raise Exception(
                        f"ERROR OFICINA POR TIPO {response.status_code} | "
                        f"url={url} | body={response.text}"
                    )
                data = response.json()

                # Si la respuesta es una lista, buscar el registro correspondiente
                if isinstance(data, list):
                    for item in data:
                        if item.get("tipoSolicitudId") == tipo_solicitud_id:
                            return item.get("oficinaId")
                    # Si no se encontro, retornar el primero si hay
                    if data:
                        return data[0].get("oficinaId")
                    return None

                # Si es un objeto directo
                if isinstance(data, dict):
                    return data.get("oficinaId")

                return None

            except httpx.TimeoutException:
                if attempt < max_attempts - 1:
                    continue
                return None
            except Exception as e:
                self.logger.step_warning(
                    f"Error obteniendo oficina por tipo solicitud desde API: {e}"
                )
                return None

        return None

    async def incrementar_counter(self, oficina_id: int, departamento_id: int) -> None:
        """
        Llama POST /configuracion-asignacion-ia/resolver/increment
        Body: {"oficinaId": X, "departamentoId": Y}

        No lanza excepcion si falla - el incremento es best-effort.
        """
        if not self._base_url:
            return

        try:
            headers = await self._get_auth_headers()

            response = await self._client.post(
                f"{self._base_url}/configuracion-asignacion-ia/resolver/increment",
                json={
                    "oficinaId": oficina_id,
                    "departamentoId": departamento_id,
                },
                headers=headers,
                timeout=15,
            )
            #response.raise_for_status()
            if response.status_code >= 400:
                raise Exception(
                    f"ERROR TOKEN ASIGNACION IA {response.status_code} | "
                    f"url={token_url} | body={response.text}"
                )

        except Exception as e:
            self.logger.step_warning(
                f"Error incrementando counter de asignacion (no critico): {e}"
            )
