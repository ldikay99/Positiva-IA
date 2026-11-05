"""
Servicio de Redis para deduplicacion
"""
import logging
import redis.asyncio as aioredis
from typing import Optional
from config import settings
from config.settings import dedup_desactivada
from utils.hash import get_redis_key

logger = logging.getLogger(__name__)


class RedisService:
    """Cliente Redis async para deduplicacion de correos"""

    def __init__(self):
        self._client: Optional[aioredis.Redis] = None

    def _get_client(self) -> aioredis.Redis:
        """Obtiene el cliente Redis async (singleton)"""
        if self._client is None:
            self._client = aioredis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD or None,
                decode_responses=True,
            )
        return self._client

    # Ultimo fallo de una consulta, o cadena vacia. El metodo devuelve False
    # cuando Redis se cae, igual que cuando el hash no existe: son la misma
    # respuesta para dos cosas opuestas. El llamador nuevo lee esto para poder
    # distinguirlas y decir en el informe que la capa no comprobo nada. Asi
    # estuvo Redis caido semanas sin aparecer en ningun lado.
    ultimo_error: str = ""

    async def email_hash_exists(self, hash_value: str) -> bool:
        """Verifica si un hash de correo ya existe"""
        self.ultimo_error = ""
        if dedup_desactivada():
            return False
        try:
            client = self._get_client()
            key = get_redis_key(hash_value)
            result = await client.get(key)
            return result is not None
        except Exception as e:
            # Si Redis falla, retornar False para no bloquear el flujo
            logger.warning("Error verificando hash en Redis: %s", e)
            self.ultimo_error = f"{type(e).__name__}: {e}"
            return False

    async def save_email_hash(self, hash_value: str, ttl_seconds: int = 2592000) -> bool:
        """Guarda un hash de correo (default: 30 dias). Retorna True si se guardó exitosamente."""
        # MODO_TEST: NO persistir en Redis (evita que el agente de PRD luego
        # salte estos correos por creerlos ya procesados). Solo se registra.
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().registrar("REDIS_SAVE", destino="redis", detalle={"hash": hash_value})
            return True
        try:
            client = self._get_client()
            key = get_redis_key(hash_value)
            await client.setex(key, ttl_seconds, "1")
            return True
        except Exception as e:
            # Si Redis falla, loguear pero no bloquear
            logger.warning("Error guardando hash en Redis: %s", e)
            return False

    async def close(self) -> None:
        """Cierra la conexion"""
        if self._client:
            await self._client.aclose()
            self._client = None
