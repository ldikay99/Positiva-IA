"""
Utilidades de hashing para deduplicacion

Estrategia: hash = asunto_limpio + remitente_original
- asunto_limpio: sin prefijos RV:/RE:/FW:
- remitente_original: el primer remitente externo del hilo (no @positiva.gov.co)

Esto garantiza que si Ana envia un correo y luego 3 areas de Positiva
lo reenvian, todos generen el mismo hash porque el asunto y el remitente
original son los mismos.
"""
import hashlib
import re
import unicodedata
from typing import List


def limpiar_asunto(subject: str) -> str:
    """Limpia prefijos de reenvio/respuesta del asunto"""
    asunto = subject or ""
    # Remover multiples prefijos (puede tener RV: RV: RE: etc)
    cambio = True
    while cambio:
        cambio = False
        for prefijo in [
            # Español
            "RV: ", "RV:", "RE: ", "RE:",
            # Ingles
            "FW: ", "FW:", "Fwd: ", "Fwd:", "Fw: ", "Fw:",
            "RE: ", "RE:", "Re: ", "Re:",
            # Portugues
            "ENC: ", "ENC:", "RES: ", "RES:",
            # Outlook variantes
            "SV: ", "SV:",   # Sueco/Nordico
            "VS: ", "VS:",   # Danes
            "WG: ", "WG:",   # Aleman
            "TR: ", "TR:",   # Turco
            "I: ", "I:",     # Italiano (Inoltro)
            # Outlook con corchetes
            "[FWD]", "[RV]", "[RE]",
        ]:
            if asunto.startswith(prefijo):
                asunto = asunto[len(prefijo):].strip()
                cambio = True
    return asunto.strip()


def asunto_comparable(subject: str) -> str:
    """
    El asunto limpio, sin tildes, mayusculas ni signos.

    "Derecho de petición" y "DERECHO DE PETICION" son el mismo asunto escrito de
    dos formas.
    """
    nfd = unicodedata.normalize("NFD", limpiar_asunto(subject).lower())
    sin_tildes = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sin_tildes).split())


def extraer_remitente_original(from_email: str, hilo_emails: List = None) -> str:
    """Extrae el remitente original (externo) del correo o hilo.

    Busca el primer remitente que NO sea de @positiva.gov.co.
    Si el hilo tiene correos, busca en el mas antiguo primero.
    Si todos son internos, usa el from del correo principal.
    """
    # Si tenemos el hilo, buscar el remitente externo mas antiguo
    if hilo_emails:
        # Ordenar por fecha (mas antiguo primero)
        emails_ordenados = sorted(
            hilo_emails,
            key=lambda e: e.received_datetime or "",
        )
        for em in emails_ordenados:
            addr = em.from_address.address if em.from_address else ""
            if addr and not addr.lower().endswith("@positiva.gov.co"):
                return addr.lower().strip()

    # Fallback: usar el from del correo actual
    return (from_email or "").lower().strip()


def generate_email_hash(subject: str, remitente_original: str, attachment_names: str = "") -> str:
    """
    Genera un hash SHA256 para deduplicacion.

    Llave: asunto_limpio + remitente_original + nombres_adjuntos

    Esto detecta:
    - Reenvios internos (RV: mismo asunto, areas de Positiva reenviando)
    - Mismo correo llegando por multiples vias
    - Correos con body diferente pero mismo caso

    Args:
        subject: Asunto del correo
        remitente_original: Email del remitente externo original
        attachment_names: Nombres de adjuntos separados por | (opcional)

    Returns:
        Hash SHA256 en formato hexadecimal
    """
    asunto_limpio = limpiar_asunto(subject)
    remitente = (remitente_original or "").lower().strip()

    content = f"{asunto_limpio}\x00{remitente}\x00{attachment_names}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def get_redis_key(hash_value: str) -> str:
    """Genera la clave de Redis para un hash de correo."""
    return f"pqrd:{hash_value}"
