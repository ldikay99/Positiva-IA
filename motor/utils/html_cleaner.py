"""
Utilidades para limpieza de HTML
"""
import re
from typing import Dict

# Entidades HTML comunes para decodificar
HTML_ENTITIES: Dict[str, str] = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&apos;": "'",
    "&aacute;": "a",
    "&eacute;": "e",
    "&iacute;": "i",
    "&oacute;": "o",
    "&uacute;": "u",
    "&ntilde;": "n",
    "&Aacute;": "A",
    "&Eacute;": "E",
    "&Iacute;": "I",
    "&Oacute;": "O",
    "&Uacute;": "U",
    "&Ntilde;": "N",
    "&#39;": "'",
    "&#x2F;": "/",
    "&#x3D;": "=",
}


def decode_html_entities(text: str) -> str:
    """
    Decodifica entidades HTML en texto.

    Args:
        text: Texto con entidades HTML

    Returns:
        Texto decodificado
    """
    result = text

    # Decodificar entidades conocidas
    for entity, char in HTML_ENTITIES.items():
        result = result.replace(entity, char)

    # Decodificar entidades numericas decimales (&#123;)
    result = re.sub(
        r"&#(\d+);",
        lambda m: chr(int(m.group(1))),
        result
    )

    # Decodificar entidades numericas hexadecimales (&#x7B;)
    result = re.sub(
        r"&#x([0-9A-Fa-f]+);",
        lambda m: chr(int(m.group(1), 16)),
        result
    )

    return result


def clean_html_content(html_content: str) -> str:
    """
    Limpia el contenido HTML del correo extrayendo solo el texto.

    Args:
        html_content: Contenido HTML del correo

    Returns:
        Texto limpio sin HTML
    """
    if not html_content:
        return ""

    clean_text = html_content

    # 1. Eliminar bloque de <style> y <head>
    clean_text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r"<head[^>]*>[\s\S]*?</head>", "", clean_text, flags=re.IGNORECASE)

    # 2. Reemplazar etiquetas de bloque por saltos de linea
    clean_text = re.sub(r"<br\s*/?>", "\n", clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r"</p>|</div>|</tr>", "\n", clean_text, flags=re.IGNORECASE)

    # 3. Eliminar todas las etiquetas HTML restantes
    clean_text = re.sub(r"<[^>]+>", "", clean_text)

    # 4. Decodificar entidades HTML
    clean_text = decode_html_entities(clean_text)

    # 5. Limpiar espacios excesivos
    lines = clean_text.split("\n")
    lines = [line.strip() for line in lines if line.strip()]
    clean_text = "\n".join(lines)

    return clean_text


def sanitize_html(html: str) -> str:
    """
    Limpia HTML peligroso pero mantiene el contenido visual.

    Args:
        html: HTML a limpiar

    Returns:
        HTML limpio sin scripts ni iframes
    """
    if not html:
        return "Sin contenido"

    # Solo eliminar scripts e iframes por seguridad
    cleaned = re.sub(
        r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>",
        "",
        html,
        flags=re.IGNORECASE
    )
    cleaned = re.sub(
        r"<iframe\b[^<]*(?:(?!</iframe>)<[^<]*)*</iframe>",
        "",
        cleaned,
        flags=re.IGNORECASE
    )

    return cleaned


def escape_html(text: str) -> str:
    """
    Escapa HTML para prevenir XSS.

    Args:
        text: Texto a escapar

    Returns:
        Texto con HTML escapado
    """
    if not text:
        return ""

    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )
