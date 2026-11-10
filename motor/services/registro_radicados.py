"""
Registro de radicados en Cloud SQL (Postgres de SGDEA, base gcpprolinktic).

Es la auditoria del job: guarda cada radicado y responde si un remitente ya
radico un asunto (capa 6 de la deduplicacion, `pipeline/duplicados.py`).

La conexion sale de variables de entorno que infra monta desde Secret Manager
(CLOUDSQL_HOST, CLOUDSQL_USER, CLOUDSQL_PASSWORD) y la tabla de
CLOUDSQL_TABLE_RADICADOS_IAPQRD. La instancia solo tiene IP privada y exige
cifrado: el job llega por la VPC y la conexion va con TLS.

pg8000 es Python puro (BSD-3): no agrega librerias de sistema a la imagen.
"""
from __future__ import annotations

import asyncio
import logging
import re
import ssl
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from config import settings
from config.settings import dedup_desactivada

logger = logging.getLogger(__name__)

# Una conexion que no responde en este tiempo se da por caida: el job corre
# cada media hora y no puede quedarse esperando a la base en cada correo.
TIEMPO_DE_CONEXION_S = 10
# Tras un fallo de conexion no se reintenta en cada correo de la corrida: cada
# intento costaria el tiempo de espera completo.
PAUSA_TRAS_CAIDA_S = 300
# El correo trae su llegada en UTC y el radicado se guarda con hora de Colombia:
# el margen evita dejar por fuera un radicado del mismo dia.
MARGEN_DE_ZONA_HORARIA = timedelta(days=1)

_IDENTIFICADOR = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def nombre_de_tabla(nombre: str) -> str:
    """El nombre de la tabla entre comillas, o error si no es un identificador simple."""
    limpio = (nombre or "").strip().lower()
    if not _IDENTIFICADOR.match(limpio):
        raise ValueError(f"nombre de tabla no valido: {nombre!r}")
    return f'"{limpio}"'


def piso_de_fecha(recibido: str) -> str:
    """Desde cuando buscar radicados de un correo (UTC, sin zona), o vacio si no hay fecha valida."""
    try:
        llegada = datetime.fromisoformat((recibido or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (llegada.replace(tzinfo=None) - MARGEN_DE_ZONA_HORARIA).isoformat()


def _contexto_tls() -> ssl.SSLContext:
    # La instancia exige cifrado, pero su certificado lo firma la CA propia de
    # la instancia, que rota; el trafico no sale de la VPC.
    contexto = ssl.create_default_context()
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE
    return contexto


def _conectar_pg8000(**kw: Any):
    import pg8000.native

    return pg8000.native.Connection(**kw)


class RegistroDeRadicados:
    """Auditoria de radicados en Postgres."""

    # Ultimo fallo, o cadena vacia. `False` en `email_exists` significa a la vez
    # "no hay duplicado" y "no se pudo comprobar": el detector lee esto para
    # distinguirlas.
    ultimo_error: str = ""

    def __init__(self, conectar: Optional[Callable[..., Any]] = None):
        self.tabla = nombre_de_tabla(settings.CLOUDSQL_TABLE_RADICADOS_IAPQRD)
        self._conectar = conectar or _conectar_pg8000
        self._caida_hasta = 0.0
        self.ultimo_error = ""

    @staticmethod
    def configurado() -> bool:
        return bool(settings.CLOUDSQL_HOST and settings.CLOUDSQL_USER
                    and settings.CLOUDSQL_PASSWORD)

    def _abrir(self):
        conexion = self._conectar(
            host=settings.CLOUDSQL_HOST,
            port=settings.CLOUDSQL_PORT,
            database=settings.CLOUDSQL_DB,
            user=settings.CLOUDSQL_USER,
            password=settings.CLOUDSQL_PASSWORD,
            ssl_context=_contexto_tls() if settings.CLOUDSQL_SSL else None,
            timeout=TIEMPO_DE_CONEXION_S,
            application_name="ms-radicacion-ia-pqrd",
        )
        # Las fechas sin zona se leen como hora de Colombia, la misma del
        # historico cargado en la tabla.
        conexion.run("SET TIME ZONE 'America/Bogota'")
        return conexion

    async def _ejecutar(self, sql: str, **parametros: Any):
        if time.monotonic() < self._caida_hasta:
            raise ConnectionError("la base de radicados no respondio hace poco en esta corrida")

        def trabajo():
            conexion = self._abrir()
            try:
                return conexion.run(sql, **parametros)
            finally:
                try:
                    conexion.close()
                except Exception:
                    pass

        try:
            return await asyncio.to_thread(trabajo)
        except (OSError, ConnectionError, TimeoutError) as e:
            self._caida_hasta = time.monotonic() + PAUSA_TRAS_CAIDA_S
            raise ConnectionError(f"{type(e).__name__}: {e}") from e

    async def email_exists(
        self, correo_remitente: str, asunto: str, recibido: str = ""
    ) -> bool:
        """Si ya hay un radicado de este remitente con este asunto (desde su llegada)."""
        self.ultimo_error = ""
        if dedup_desactivada():
            return False
        sql = (f"SELECT numero_radicado FROM {self.tabla} "
               "WHERE correo_remitente = :remitente AND asunto = :asunto")
        parametros = {"remitente": correo_remitente, "asunto": asunto}
        piso = piso_de_fecha(recibido)
        if piso:
            sql += " AND fecha_radicacion >= (CAST(:piso AS timestamp) AT TIME ZONE 'UTC')"
            parametros["piso"] = piso
        try:
            filas = await self._ejecutar(sql + " LIMIT 1", **parametros)
            return bool(filas)
        except Exception as e:
            logger.warning("Error verificando duplicado en la base de radicados: %s", e)
            self.ultimo_error = f"{type(e).__name__}: {e}"
            return False

    async def guardar_radicado(
        self,
        numero_radicado: str,
        asunto: str = "",
        correo_remitente: str = "",
        numero_documento_afectado: str = "",
        asignado_automatico: bool = False,
        persona_asignada: str = "",
    ) -> bool:
        """
        Guarda el radicado. Devuelve True si quedo una fila nueva.

        No duplica: si otra corrida ya guardo ese numero, no inserta otra fila.
        Los errores se propagan para que el llamador los anote en el informe.
        """
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder

            get_recorder().registrar(
                "REGISTRO_RADICADO", destino=self.tabla.strip('"'),
                detalle={
                    "numero_radicado": numero_radicado,
                    "correo_remitente": correo_remitente,
                    "numero_documento_afectado": numero_documento_afectado,
                },
            )
            return False
        filas = await self._ejecutar(
            f"INSERT INTO {self.tabla} (numero_radicado, asunto, correo_remitente, "
            "fecha_radicacion, asignado_automatico, persona_asignada, "
            "numero_documento_afectado) "
            "SELECT :radicado, :asunto, :remitente, NOW(), :asignado, :persona, :documento "
            f"WHERE NOT EXISTS (SELECT 1 FROM {self.tabla} "
            "WHERE UPPER(numero_radicado) = UPPER(:radicado)) "
            "RETURNING id",
            radicado=numero_radicado,
            asunto=asunto or "",
            remitente=correo_remitente or "",
            asignado=bool(asignado_automatico),
            persona=persona_asignada or None,
            documento=numero_documento_afectado or None,
        )
        return bool(filas)

    def close(self) -> None:
        """Sin conexion abierta que cerrar: cada consulta abre y cierra la suya."""
