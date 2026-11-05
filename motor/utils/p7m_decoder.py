"""
Decodificador de archivos .p7m (correo certificado)
"""
import re
import base64
from typing import List


def decode_quoted_printable(text: str) -> str:
    """
    Decodifica contenido Quoted-Printable.

    Args:
        text: Texto en formato Quoted-Printable

    Returns:
        Texto decodificado
    """
    # Eliminar saltos de linea suaves
    result = re.sub(r"=\r?\n", "", text)

    # Convertir secuencias =XX a caracteres
    result = re.sub(
        r"=([0-9A-Fa-f]{2})",
        lambda m: chr(int(m.group(1), 16)),
        result
    )

    # Asegurar conversion de simbolos igual
    result = result.replace("=3D", "=")

    return result


def extract_urls_from_p7m(p7m_content: str) -> List[str]:
    """
    Extrae URLs de archivos .p7m (correo certificado).

    Args:
        p7m_content: Contenido del archivo .p7m en base64

    Returns:
        Lista de URLs encontradas
    """
    # Convertir de base64 a texto
    try:
        p7m_bytes = base64.b64decode(p7m_content)
        p7m_text = p7m_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return []

    # Decodificar Quoted-Printable
    clean_text = decode_quoted_printable(p7m_text)

    # Buscar URLs con messageid (tipicas de correo certificado)
    url_regex = r"https?://[^\s<>\"'|]+[a-zA-Z0-9/]"
    all_urls = re.findall(url_regex, clean_text, re.IGNORECASE)

    # Filtrar URLs relevantes
    filtered_urls = [
        url for url in all_urls
        if "messageid" in url.lower() or "descarga" in url.lower()
    ]

    # Eliminar duplicados
    return list(dict.fromkeys(filtered_urls))


def is_p7m_file(file_name: str) -> bool:
    """
    Verifica si un archivo es de tipo smime.p7m (correo certificado encriptado).

    Args:
        file_name: Nombre del archivo

    Returns:
        True si es archivo .p7m
    """
    return file_name.lower() == "smime.p7m"
