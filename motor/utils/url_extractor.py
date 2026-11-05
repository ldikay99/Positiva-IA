"""
Extractor de URLs de documentos desde correos
"""
import re
from typing import List, Tuple
from urllib.parse import unquote

from .html_cleaner import decode_html_entities
from models.email import ExtractedUrl

# Dominios prioritarios para documentos
PRIORITY_DOMAINS = [
    "correocertificado4-72.com.co",
    "e-entrega.co",
    "enviaelectronico.co",
    "technokey.co",
    "sealmail",
    "nec.positiva.gov.co",
    "acrobat.adobe.com",
    "docusign.com",
    "firmadigital.co",
    "certificacion.com.co",
]

# Patrones a ignorar (redes sociales, imagenes, etc.)
IGNORE_PATTERNS = [
    "facebook.com",
    "twitter.com",
    "youtube.com",
    "linkedin.com",
    "instagram.com",
    "outlook.office365.com/owa",
    "$metadata",
    "cid:",
    "mailto:",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".bmp",
    "unsubscribe.php",
    "unsuscribe.php",
    "optout.php",
    "/themes_images",
    "/assets/",
    "/static/",
]

# Patrones requeridos para documentos
REQUIRED_PATTERNS = [
    "viewmessage.php?messageid=",
    "download.php",
    "getdocument.php",
    "attachment.php",
    "urn:aaid:sc:",
    "docusign.net/signing/",
    "documento.aspx",
]


def decode_safe_link(url: str) -> str:
    """
    Decodifica SafeLinks de Outlook.

    Args:
        url: URL a decodificar

    Returns:
        URL original sin SafeLinks
    """
    if "safelinks.protection.outlook.com" in url:
        match = re.search(r"[?&]url=([^&]+)", url)
        if match:
            return unquote(match.group(1))
    return url


def classify_url(url: str) -> Tuple[str, str]:
    """
    Clasifica una URL por tipo y prioridad.

    Args:
        url: URL a clasificar

    Returns:
        Tupla (tipo, prioridad)
    """
    url_lower = url.lower()

    if "viewmessage" in url_lower and "messageid" in url_lower:
        return ("correo_certificado", "alta")

    if "download" in url_lower or "attachment" in url_lower:
        return ("descarga_directa", "alta")

    if any(d in url_lower for d in PRIORITY_DOMAINS):
        return ("servicio_documentos", "media")

    return ("otro", "baja")


def extract_document_urls(
    html_content: str,
    email_id: str = "",
    subject: str = ""
) -> List[ExtractedUrl]:
    """
    Extrae y clasifica URLs de documentos desde contenido HTML.

    Args:
        html_content: Contenido HTML del correo
        email_id: ID del correo (para logging)
        subject: Asunto del correo (para logging)

    Returns:
        Lista de URLs extraidas y clasificadas
    """
    if not html_content:
        return []

    # 1. Decodificar entidades HTML
    content = decode_html_entities(html_content)

    # 2. Extraer todas las URLs
    url_regex = r"https?://[^\s<>\"')\]]+"
    matched_urls = re.findall(url_regex, content, re.IGNORECASE)

    # 3. Decodificar SafeLinks de Outlook
    decoded_urls = [decode_safe_link(url) for url in matched_urls]

    # 4. Limpiar URLs (remover caracteres finales no deseados)
    urls = [re.sub(r"[,;.)\]]+$", "", url) for url in decoded_urls]

    # 5. Filtrar URLs relevantes
    relevant_urls = []
    for url in urls:
        url_lower = url.lower()

        # Ignorar patrones no deseados
        if any(pattern.lower() in url_lower for pattern in IGNORE_PATTERNS):
            continue

        # Incluir si tiene patron requerido
        if any(pattern.lower() in url_lower for pattern in REQUIRED_PATTERNS):
            relevant_urls.append(url)
            continue

        # Incluir si es dominio prioritario con patron de documento
        is_priority = any(d in url_lower for d in PRIORITY_DOMAINS)
        if is_priority:
            doc_patterns = ["messageid", "download", "attachment", "documento", "adjunto", "file", "view"]
            if any(p in url_lower for p in doc_patterns):
                relevant_urls.append(url)

    # 6. Eliminar duplicados
    unique_urls = list(dict.fromkeys(relevant_urls))

    # 7. Ordenar por prioridad (viewmessage primero)
    unique_urls.sort(
        key=lambda u: (0 if "viewmessage.php?messageid=" in u.lower() else 1)
    )

    # 8. Clasificar y transformar URLs
    result = []
    for url in unique_urls:
        url_type, priority = classify_url(url)
        result.append(ExtractedUrl(url=url, type=url_type, priority=priority))

    return result
