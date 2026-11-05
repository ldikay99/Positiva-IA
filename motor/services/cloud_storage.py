"""
Descargador de documentos desde almacenamiento en la nube.
Soporta: Google Drive, OneDrive (links públicos/compartidos).
"""
import re
import base64
import sys
from typing import Optional, Tuple

import httpx


def _es_google_drive(url: str) -> bool:
    return "drive.google.com" in url or "docs.google.com" in url


def _es_onedrive(url: str) -> bool:
    return (
        "onedrive.live.com" in url
        or "1drv.ms" in url
        or "sharepoint.com" in url
    )


def _google_drive_a_descarga(url: str) -> Optional[str]:
    """
    Convierte un link de Google Drive a URL de descarga directa.

    Soporta formatos:
    - https://drive.google.com/file/d/{id}/view
    - https://drive.google.com/open?id={id}
    - https://docs.google.com/document/d/{id}/...
    - https://docs.google.com/spreadsheets/d/{id}/...
    """
    # Formato /file/d/{id}/ o /document/d/{id}/ o /spreadsheets/d/{id}/
    match = re.search(r"/d/([a-zA-Z0-9_-]{20,})", url)
    if match:
        file_id = match.group(1)
        # Documentos de Google (Docs, Sheets) → exportar como PDF
        if "docs.google.com" in url:
            if "spreadsheet" in url:
                return f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx"
            return f"https://docs.google.com/document/d/{file_id}/export?format=pdf"
        # Archivos normales de Drive
        return f"https://drive.google.com/uc?export=download&id={file_id}"

    # Formato ?id={id}
    match = re.search(r"[?&]id=([a-zA-Z0-9_-]{20,})", url)
    if match:
        return f"https://drive.google.com/uc?export=download&id={match.group(1)}"

    return None


def _onedrive_a_descarga(url: str) -> Optional[str]:
    """
    Convierte un link compartido de OneDrive a URL de descarga directa
    usando la API de OneDrive para shares anónimos.

    Codifica la URL en base64url y llama a:
    https://api.onedrive.com/v1.0/shares/{encoded}/root/content
    """
    try:
        # Codificar la URL como base64url sin padding (formato Microsoft)
        encoded = base64.b64encode(url.encode("utf-8")).decode("utf-8")
        encoded = encoded.rstrip("=").replace("+", "-").replace("/", "_")
        return f"https://api.onedrive.com/v1.0/shares/u!{encoded}/root/content"
    except Exception:
        return None


class CloudStorageDownloader:
    """Descarga documentos desde Google Drive y OneDrive públicos."""

    TIMEOUT = 30
    MAX_SIZE_MB = 20

    async def descargar(self, url: str) -> Optional[Tuple[str, bytes]]:
        """
        Descarga un archivo desde Google Drive o OneDrive.

        Returns:
            Tupla (nombre_archivo, bytes) o None si falla.
        """
        url = url.strip()

        if _es_google_drive(url):
            return await self._descargar_google_drive(url)

        if _es_onedrive(url):
            return await self._descargar_onedrive(url)

        return None

    async def _descargar_google_drive(self, url: str) -> Optional[Tuple[str, bytes]]:
        """Descarga archivo de Google Drive."""
        download_url = _google_drive_a_descarga(url)
        if not download_url:
            print(f"[CLOUD] Drive: No se pudo convertir URL: {url}", file=sys.stderr)
            return None

        print(f"[CLOUD] Drive: Descargando desde {download_url}", file=sys.stderr)

        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=True,
            ) as client:
                response = await client.get(download_url)

                # Drive a veces devuelve una página de confirmación para archivos grandes
                if response.status_code == 200 and b"virus scan warning" in response.content.lower():
                    # Buscar el link de descarga directa en el HTML
                    match = re.search(
                        rb'href="(/uc\?export=download[^"]+)"',
                        response.content,
                    )
                    if match:
                        confirm_url = "https://drive.google.com" + match.group(1).decode()
                        response = await client.get(confirm_url)

                if response.status_code != 200:
                    print(
                        f"[CLOUD] Drive: HTTP {response.status_code}",
                        file=sys.stderr,
                    )
                    return None

                content = response.content
                if len(content) > self.MAX_SIZE_MB * 1024 * 1024:
                    print(
                        f"[CLOUD] Drive: Archivo muy grande ({len(content) // 1024 // 1024} MB), omitiendo",
                        file=sys.stderr,
                    )
                    return None

                nombre = self._extraer_nombre(response, "documento_drive.pdf")
                print(
                    f"[CLOUD] Drive: Descargado '{nombre}' ({len(content)} bytes)",
                    file=sys.stderr,
                )
                return (nombre, content)

        except Exception as e:
            print(f"[CLOUD] Drive: Error: {e}", file=sys.stderr)
            return None

    async def _descargar_onedrive(self, url: str) -> Optional[Tuple[str, bytes]]:
        """
        Descarga archivo de OneDrive.
        Intenta dos estrategias:
        1. API de shares de OneDrive (sin auth, para links públicos)
        2. Seguir redirects del link compartido directamente
        """
        # Estrategia 1: API de shares de OneDrive
        api_url = _onedrive_a_descarga(url)
        if api_url:
            print(f"[CLOUD] OneDrive: Intentando API: {api_url}", file=sys.stderr)
            resultado = await self._get_bytes(api_url, "documento_onedrive.pdf")
            if resultado:
                return resultado

        # Estrategia 2: Seguir el redirect del link compartido
        print(
            f"[CLOUD] OneDrive: Intentando redirect directo: {url}",
            file=sys.stderr,
        )
        return await self._get_bytes(url, "documento_onedrive.pdf")

    async def _get_bytes(
        self, url: str, nombre_defecto: str
    ) -> Optional[Tuple[str, bytes]]:
        """Descarga bytes desde una URL siguiendo redirects."""
        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)

                if response.status_code not in (200, 206):
                    print(
                        f"[CLOUD] HTTP {response.status_code} para {url}",
                        file=sys.stderr,
                    )
                    return None

                content = response.content
                if not content or len(content) < 100:
                    print(f"[CLOUD] Respuesta vacía o muy pequeña", file=sys.stderr)
                    return None

                if len(content) > self.MAX_SIZE_MB * 1024 * 1024:
                    print(
                        f"[CLOUD] Archivo muy grande ({len(content) // 1024 // 1024} MB), omitiendo",
                        file=sys.stderr,
                    )
                    return None

                nombre = self._extraer_nombre(response, nombre_defecto)
                print(
                    f"[CLOUD] Descargado '{nombre}' ({len(content)} bytes)",
                    file=sys.stderr,
                )
                return (nombre, content)

        except httpx.TimeoutException:
            print(f"[CLOUD] Timeout descargando {url}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"[CLOUD] Error: {e}", file=sys.stderr)
            return None

    def _extraer_nombre(self, response: httpx.Response, defecto: str) -> str:
        """Extrae el nombre del archivo desde los headers de la respuesta."""
        content_disposition = response.headers.get("content-disposition", "")
        if content_disposition:
            match = re.search(
                r'filename[^;=\n]*=\s*["\']?([^"\';\n]+)',
                content_disposition,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip().strip('"').strip("'")
        return defecto


def extraer_urls_cloud(texto: str) -> list:
    """
    Extrae URLs de Google Drive y OneDrive desde texto plano o HTML.

    Returns:
        Lista de URLs encontradas.
    """
    url_regex = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
    urls_encontradas = []

    for match in url_regex.finditer(texto):
        url = match.group(0).rstrip(".,;)")
        if _es_google_drive(url) or _es_onedrive(url):
            urls_encontradas.append(url)

    return list(dict.fromkeys(urls_encontradas))  # eliminar duplicados
