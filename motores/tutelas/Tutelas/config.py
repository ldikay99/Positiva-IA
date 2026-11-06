# Configuración de credenciales y endpoints
# Proyecto: Radicación Ministerio de Trabajo

import os

# Carga opcional de .env para desarrollo local (no requerido en Cloud Run).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ============================================
# MODO_TEST (dry-run): cuando es True, el agente LEE normal (buzón, Gemini,
# consulta juzgados/persona) pero NO ejecuta escrituras (radicar en SGDEA,
# etiquetar/mover/responder en Outlook, guardar dedup en Redis). Las graba en
# CSV (ver Tutelas/test_recorder.py). Default False -> inerte en produccion.
# ============================================
MODO_TEST = os.getenv("MODO_TEST", "false").strip().lower() in ("1", "true", "yes", "si")
TEST_OUTPUT_DIR = os.getenv("TEST_OUTPUT_DIR", "salidas_prueba")

# ============================================
# MODO_UAT: cuando es True, el agente SI radica de verdad (POST real a SGDEA,
# apuntando a UAT via las variables TUTELAS-SGDEA-* configuradas en ese Cloud
# Run Job) pero NO toca el correo de origen (no etiqueta, no mueve, no
# responde) ni el dedup en Redis. Distinto de MODO_TEST: aqui la radicacion
# es real. Default False -> inerte en produccion.
# ============================================
MODO_UAT = os.getenv("MODO_UAT", "false").strip().lower() in ("1", "true", "yes", "si")

# Combinado: True si no se debe mover/etiquetar/responder el correo ni escribir en
# el dedup de Redis. Usar SIEMPRE este flag (no repetir "MODO_TEST or MODO_UAT") en
# los metodos que tocan correo/dedup, para que un metodo nuevo no se quede sin el
# gate de MODO_UAT por error. sgdea_service.radicar_tutela NO debe usar este flag:
# sigue gateado solo por MODO_TEST, porque en MODO_UAT debe radicar de verdad.
OMITIR_ESCRITURAS = MODO_TEST or MODO_UAT

# ============================================
# DISCLAIMER DE IA: aviso obligatorio en todo correo/mensaje y documento (PDF)
# generado total o parcialmente por el agente. Un solo lugar -> mismo texto en
# todos los puntos de salida.
# ============================================
DISCLAIMER_IA = (
    "Este mensaje fue generado total o parcialmente mediante Inteligencia Artificial "
    "como apoyo a los procesos de automatización. Aunque se han aplicado controles para "
    "garantizar su calidad, el contenido puede contener imprecisiones o errores. En caso "
    "de duda, prevalecerá la información validada por la entidad."
)


def _secret(nombre: str) -> str:
    """Lee un secreto OBLIGATORIO de entorno. Falla claro si no esta definido."""
    valor = os.environ.get(nombre)
    if not valor:
        raise RuntimeError(
            f"Falta la variable de entorno requerida: {nombre}. "
            f"En Cloud Run debe venir de Secret Manager; en local, del archivo .env."
        )
    return valor


# ============================================
# MICROSOFT GRAPH API (Outlook)
# Usa Client Credentials / ROPC Flow con MSAL
# ============================================
OUTLOOK_CONFIG = {
    # Identificador del tenant. Debe ser el id concreto y no el alias
    # "organizations": con el alias la petición no queda asociada al tenant de la
    # entidad y las políticas de acceso condicional rechazan la emisión del token.
    "tenant": os.getenv("TUTELAS-MICROSOFT-TENANT", "efd62394-8e75-4b0b-a5b1-8bf03bc5ac1c"),
    "client_id": os.getenv("TUTELAS-MICROSOFT-CLIENT-ID", "7fda2e67-4716-4be1-ad1d-52cb82190673"),
    "client_secret": _secret("TUTELAS-MICROSOFT-CLIENT-SECRET"),
    # Buzón que atiende el agente. Con token de aplicación se nombra de forma
    # explícita en la ruta de Graph, porque el token no representa a un usuario.
    "username": os.getenv("TUTELAS-MICROSOFT-USERNAME", "notificacionesjudiciales@positiva.gov.co"),
    # La contraseña del buzón ya no interviene en la autenticación; se conserva
    # como opcional para no romper despliegues que todavía la definan.
    "password": os.getenv("TUTELAS-MICROSOFT-PASSWORD", ""),
    "scopes": [
        "https://graph.microsoft.com/Mail.ReadWrite",
        "https://graph.microsoft.com/Mail.Send",
        "https://graph.microsoft.com/User.Read",
    ],
    "graph_url": os.getenv("TUTELAS-MICROSOFT-GRAPH-URL", "https://graph.microsoft.com/v1.0"),
    # Carpetas de Outlook (IDs)
    "folder_entrada": os.getenv(
        "TUTELAS-OUTLOOK-FOLDER-ENTRADA",
        "AAMkAGY4NTYyMGVmLTdhNjktNDY3MS1iYWM0LTY2NTAyMzg2OGVlNwAuAAAAAACp4tL_xJxmSaR_hdYCH6R4AQDzRNVdGODBQpgUNL_7WQFPAAhbc-kxAAA=",
    ),
    "folder_revision_manual": os.getenv(
        "TUTELAS-OUTLOOK-FOLDER-REVISION-MANUAL",
        "AAMkAGY4NTYyMGVmLTdhNjktNDY3MS1iYWM0LTY2NTAyMzg2OGVlNwAuAAAAAACp4tL_xJxmSaR_hdYCH6R4AQDzRNVdGODBQpgUNL_7WQFPAAhbc-lVAAA=",
    ),
    "folder_radicacion_exitosa": os.getenv(
        "TUTELAS-OUTLOOK-FOLDER-RADICACION-EXITOSA",
        "AAMkAGY4NTYyMGVmLTdhNjktNDY3MS1iYWM0LTY2NTAyMzg2OGVlNwAuAAAAAACp4tL_xJxmSaR_hdYCH6R4AQDzRNVdGODBQpgUNL_7WQFPAAhbc-lUAAA=",
    ),
    # Carpetas para flujo de hilos
    "folder_tutelas_nombre": "TUTELAS",  # Se resuelve dinámicamente por nombre
    "folder_inbox": "Inbox",  # Alias de Graph API para bandeja de entrada
}


# ============================================
# REDIS (Deduplicación)
# ============================================
REDIS_CONFIG = {
    "host": os.getenv("TUTELAS-REDIS-HOST", "redis-18240.c278.us-east-1-4.ec2.cloud.redislabs.com"),
    "port": int(os.getenv("TUTELAS-REDIS-PORT", "18240")),
    "username": os.getenv("TUTELAS-REDIS-USERNAME", "default"),
    "password": _secret("TUTELAS-REDIS-PASSWORD"),
    "prefix": "tutelas:",  # Prefijo para las keys
}

# ============================================
# SGDEA API (Radicación)
# ============================================
SGDEA_CONFIG = {
    "token_url": os.getenv(
        "TUTELAS-SGDEA-TOKEN-URL",
        "https://sgdea-prod.proyectos-3t.tech/realms/positiva/protocol/openid-connect/token",
    ),
    "api_url": os.getenv("TUTELAS-SGDEA-API-URL", "https://ms-sgdea-beta.proyectos-3t.tech"),
    "client_id": os.getenv("TUTELAS-SGDEA-CLIENT-ID", "integracion_sgdea"),
    "client_secret": _secret("TUTELAS-SGDEA-CLIENT-SECRET"),
    "username": os.getenv("TUTELAS-SGDEA-USERNAME", "radicardorIA"),
    "password": _secret("TUTELAS-SGDEA-PASSWORD"),
}

# ============================================
# GEMINI AI (Vertex)
# ============================================
GEMINI_CONFIG = {
    "project_id": os.getenv("TUTELAS-GCP-PROJECT", "positiva-sgda-prd-416417"),
    "location": os.getenv("TUTELAS-GCP-LOCATION", "us-east1"),
    "model": os.getenv("TUTELAS-GEMINI-MODEL", "gemini-2.5-flash"),
}

# ============================================
# BASE DE DATOS PRODUCCIÓN (Juzgados)
# En Cloud Run: conexión directa a la IP privada de la instancia.
# Local: túnel SSH en 127.0.0.1:5432
# ============================================
DATABASE_CONFIG = {
    "host": os.environ.get("DATABASE_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DATABASE_PORT", "5432")),
    "database": os.getenv("TUTELAS-DB-NAME", "gcpprolinktic"),
    "user": os.getenv("TUTELAS-DB-USER", "postgres"),
    "password": _secret("TUTELAS-DATABASE-PASSWORD"),
}

# ============================================
# VALIDACIÓN DE CORREOS
# ============================================
# Dominios de la Rama Judicial. Un hilo solo se radica si alguno de sus correos
# proviene de esta lista, y ese es el remitente que se reporta a SGDEA.
#
# El dominio del buzón institucional (positiva.gov.co) NO va aqui: aparece en
# practicamente todos los hilos por los reenvios internos, de modo que aceptarlo
# como origen valido permitiria radicar el correo de un particular con solo
# reenviarlo dentro de la entidad.
DOMINIOS_JUDICIALES = [
    "cendoj.ramajudicial.gov.co",
    "notificacionesrj.gov.co",
]

# Dominio del buzón institucional que opera el agente.
DOMINIO_INTERNO = "positiva.gov.co"

# Radicar los correos que traen un enlace al expediente y ademas anexan los PDF.
# El area pidio que estos casos tampoco se radiquen automaticamente, porque el
# expediente completo esta detras del enlace y lo anexado puede ser parcial.
# Queda como parámetro para poder volver al comportamiento anterior sin desplegar.
RADICAR_ENLACE_CON_ADJUNTOS = os.getenv(
    "TUTELAS-RADICAR-ENLACE-CON-ADJUNTOS", "false"
).strip().lower() in ("1", "true", "yes", "si")
