"""
Configuracion del microservicio usando Pydantic Settings

Variables de entorno requeridas (solo secretos):
  MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET, MICROSOFT_TENANT_ID,
  OUTLOOK_REFRESH_TOKEN, OUTLOOK_FOLDER_ID,

  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
  SGDEA_CLIENT_SECRET, SGDEA_PASSWORD
"""
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuracion del microservicio de radicacion PQRD"""

    # ── Secretos (OBLIGATORIOS como variable de entorno) ──
    MICROSOFT_CLIENT_ID: str
    MICROSOFT_CLIENT_SECRET: str
    MICROSOFT_TENANT_ID: str
    OUTLOOK_REFRESH_TOKEN: str
    OUTLOOK_FOLDER_ID: str
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SGDEA_CLIENT_SECRET: str
    SGDEA_PASSWORD: str

    # ── Configuracion hardcodeada (no requiere env vars) ──

    # Lee normal y no ejecuta ninguna escritura: radicacion, traza, asignacion,
    # etiquetado, movimiento y reenvio se graban en CSV bajo TEST_OUTPUT_DIR.
    MODO_TEST: bool = False
    TEST_OUTPUT_DIR: str = "salidas_prueba"

    # Ejecuta las consultas de deduplicacion durante MODO_TEST. Siguen sin
    # escribir nada, pero los correos ya radicados se saltan.
    DEDUP_EN_PRUEBAS: bool = False

    # Buzon interno al que se remite el resultado del procesamiento, para que
    # quien revisa entienda el desenlace sin abrir los registros.
    #
    # Vacio por defecto y NO se envia nada. El aviso jamas puede volver a quien
    # escribio: un ciudadano, una empresa o un juzgado no tienen por que recibir
    # que un sistema automatico no pudo con su caso. De eso responde la entidad.
    CORREO_DE_AVISOS: str = ""

    # Vertex AI. Autenticacion por ADC (gcloud auth application-default login).
    GCP_PROJECT: str = "positiva-sgda-prd-416417"
    GCP_LOCATION: str = "us-east1"

    # Redis. Sin contraseña el cliente no se construye y la capa de hashes no
    # se usa; no es obligatoria para que arranque el pipeline.
    REDIS_PASSWORD: str = ""
    REDIS_HOST: str = "redis-14842.c279.us-central1-1.gce.redns.redis-cloud.com"
    REDIS_PORT: int = 14842

    # SGDEA Authentication
    SGDEA_TOKEN_URL: str = "https://sgdea-prod.proyectos-3t.tech/realms/positiva/protocol/openid-connect/token"
    SGDEA_CLIENT_ID: str = "cliente-registrar"
    SGDEA_USERNAME: str = "AsignadoIA"

    # Microservices URLs
    MS_DOCUM_SGDEA_URL: str = "https://ms-sgdea-replica-a-276879800950.us-east1.run.app"
    MS_SGDEA_BETA_URL: str = "https://ms-sgdea-beta.proyectos-3t.tech"
    AGENTS_IA_URL: str = "https://agents-ia-276879800950.us-east1.run.app"
    CONFIGURACION_ASIGNACION_IA_URL: str = "https://ms-sgdea-prd.proyectos-3t.tech"

    # Outlook (servicioalcliente@positiva.gov.co)
    OUTLOOK_USER_EMAIL: str = "servicioalcliente@positiva.gov.co"
    OUTLOOK_FOLDER_DUPLICADOS: str = "AAMkADcyMDJmMDA3LTVhNzItNDVlNS04NWViLWVjZmUyNWJiYTcxMQAuAAAAAAANrHHRd1pDTamvbofpSEoqAQBLcuGHFAssTaRrL8tDDEqfAAeYJzX8AAA="
    OUTLOOK_FOLDER_ERROR_RADICAR: str = "AAMkADcyMDJmMDA3LTVhNzItNDVlNS04NWViLWVjZmUyNWJiYTcxMQAuAAAAAAANrHHRd1pDTamvbofpSEoqAQBLcuGHFAssTaRrL8tDDEqfAAeYJzX9AAA="
    OUTLOOK_FOLDER_RADICACION_EXITOSA: str = "AAMkADcyMDJmMDA3LTVhNzItNDVlNS04NWViLWVjZmUyNWJiYTcxMQAuAAAAAAANrHHRd1pDTamvbofpSEoqAQBLcuGHFAssTaRrL8tDDEqfAAdL0MCYAAA="
    OUTLOOK_FOLDER_REENVIADO_OTRA_AREA: str = "AAMkADcyMDJmMDA3LTVhNzItNDVlNS04NWViLWVjZmUyNWJiYTcxMQAuAAAAAAANrHHRd1pDTamvbofpSEoqAQBLcuGHFAssTaRrL8tDDEqfAAeYJzX_AAA="
    OUTLOOK_FOLDER_REVISAR_MANUAL: str = "AAMkADcyMDJmMDA3LTVhNzItNDVlNS04NWViLWVjZmUyNWJiYTcxMQAuAAAAAAANrHHRd1pDTamvbofpSEoqAQBLcuGHFAssTaRrL8tDDEqfAAckGdkYAAA="

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class ConfiguracionIncompleta(RuntimeError):
    """Falta un secreto obligatorio. Dice cual y donde ponerlo."""

    def __init__(self, faltantes):
        self.faltantes = list(faltantes)
        ruta = Path(__file__).resolve().parent.parent / ".env"
        super().__init__(
            "Faltan variables de entorno obligatorias:\n"
            + "".join(f"  - {v}\n" for v in self.faltantes)
            + f"\nPonerlas en {ruta} (hay una plantilla en .env.example) "
            "o exportarlas en la sesion.\n"
            "Las pruebas no las necesitan: tests/conftest.py las rellena."
        )


@lru_cache
def get_settings() -> Settings:
    """
    Configuracion, cacheada.

    Si falta algun secreto obligatorio lanza `ConfiguracionIncompleta`, que
    nombra las variables que faltan y donde ponerlas.
    """
    try:
        return Settings()
    except ValidationError as e:
        faltantes = [
            str(err["loc"][0]) for err in e.errors()
            if err.get("type") == "missing"
        ]
        if not faltantes:
            raise
        raise ConfiguracionIncompleta(faltantes) from e


settings = get_settings()


def dedup_desactivada() -> bool:
    """
    Si las consultas de deduplicacion estan apagadas.

    Unico criterio para Outlook, Redis y Supabase, de modo que las tres capas
    queden siempre encendidas o apagadas a la vez.
    """
    return bool(settings.MODO_TEST) and not bool(settings.DEDUP_EN_PRUEBAS)
