"""
Servicio para descargar documentos desde carpetas compartidas de SharePoint
de los juzgados (etbcsj-my.sharepoint.com)
"""
import re
import requests
from typing import List, Optional, Tuple
from urllib.parse import unquote, quote


class SharePointService:
    """Descarga expedientes desde enlaces compartidos de SharePoint judicial"""

    # Dominio de SharePoint judicial
    SHAREPOINT_DOMAIN = "etbcsj-my.sharepoint.com"

    # Portales donde los despachos publican el expediente en vez de anexarlo.
    # El documento vive detras del enlace, por lo que el correo no trae la
    # documentacion que debe radicarse.
    DOMINIOS_EXPEDIENTE = [
        "etbcsj-my.sharepoint.com",
        "samai.ramajudicial.gov.co",
        "samairj.ramajudicial.gov.co",
        "procesojudicial.ramajudicial.gov.co",
        "consultaprocesos.ramajudicial.gov.co",
        "portalrestituciondetierras.ramajudicial.gov.co",
        "onedrive.live.com",
        "drive.google.com",
        "we.tl",
        "wetransfer.com",
    ]

    def extraer_enlace_expediente(self, body_html: str) -> Optional[str]:
        """Busca un enlace a un portal de expediente en el cuerpo del correo.

        Cubre SharePoint judicial y los demas portales por los que los despachos
        comparten el expediente (SAMAI, consulta de procesos, unidades en la nube).
        Retorna la primera URL encontrada o None.
        """
        if not body_html:
            return None
        for dominio in self.DOMINIOS_EXPEDIENTE:
            patron = rf'https?://[^\s"<>\']*{re.escape(dominio)}/[^\s"<>\'&;]*'
            enlaces = re.findall(patron, body_html, re.IGNORECASE)
            if enlaces:
                return enlaces[0]
        return None

    def extraer_enlace_sharepoint(self, body_html: str) -> Optional[str]:
        """
        Busca un enlace de SharePoint judicial en el HTML del correo.
        Retorna la primera URL encontrada o None.
        """
        if not body_html:
            return None

        # Buscar URLs de etbcsj-my.sharepoint.com (en href o texto plano)
        patron = rf'https://{re.escape(self.SHAREPOINT_DOMAIN)}/[^\s"<>\'&;]+'
        enlaces = re.findall(patron, body_html, re.IGNORECASE)

        if not enlaces:
            return None

        # Limpiar el enlace (quitar tracking params de Outlook como xsdata, sdata)
        url = enlaces[0]
        # Quedarse solo hasta el parámetro 'e=' (token de acceso)
        match = re.match(r'(https://[^?]+\?[^&]*e=[^&]+)', url)
        if match:
            return match.group(1)

        return url

    def descargar_expediente(self, share_url: str) -> Tuple[List[Tuple[str, bytes, str]], Optional[str]]:
        """
        Descarga todos los archivos de una carpeta compartida de SharePoint.

        Args:
            share_url: URL del enlace compartido de SharePoint

        Returns:
            (adjuntos[(nombre, contenido_bytes, content_type)], radicado_23)
        """
        adjuntos = []
        radicado = None

        try:
            # 1. Acceder al enlace compartido (obtiene cookies FedAuth)
            session = requests.Session()
            print(f"      [SHAREPOINT] Accediendo al enlace compartido...")
            resp = session.get(share_url, timeout=30)

            if resp.status_code != 200:
                print(f"      [SHAREPOINT] Error accediendo al enlace: {resp.status_code}")
                return adjuntos, radicado

            # 2. Extraer folder path de la URL final
            folder_path = self._extraer_folder_path(resp.url)
            if not folder_path:
                print(f"      [SHAREPOINT] No se pudo extraer el path de la carpeta")
                return adjuntos, radicado

            print(f"      [SHAREPOINT] Carpeta: {folder_path}")

            # 3. Extraer radicado de 23 dígitos del path
            radicado = self._extraer_radicado_de_path(folder_path)
            if radicado:
                print(f"      [SHAREPOINT] Radicado extraído del path: {radicado}")

            # 4. Extraer base URL del sitio personal
            base_url = self._extraer_base_url(resp.url)
            if not base_url:
                print(f"      [SHAREPOINT] No se pudo determinar la URL base")
                return adjuntos, radicado

            # 5. Extraer FormDigest del HTML
            form_digest = self._extraer_form_digest(resp.text)

            # 6. Listar archivos via API de SharePoint
            archivos = self._listar_archivos(session, base_url, folder_path, form_digest)
            print(f"      [SHAREPOINT] Archivos encontrados: {len(archivos)}")

            # 7. Descargar solo PDFs y Word
            EXTENSIONES_VALIDAS = (".pdf", ".doc", ".docx")
            for archivo in archivos:
                nombre = archivo["nombre"]
                if not nombre.lower().endswith(EXTENSIONES_VALIDAS):
                    print(f"      [SHAREPOINT] Ignorando (no es PDF/Word): {nombre}")
                    continue

                server_url = archivo["server_relative_url"]
                print(f"      [SHAREPOINT] Descargando: {nombre} ({archivo['size']//1024} KB)...")

                contenido = self._descargar_archivo(session, base_url, server_url)
                if contenido:
                    ext = nombre.lower().rsplit(".", 1)[-1]
                    content_types = {
                        "pdf": "application/pdf",
                        "doc": "application/msword",
                        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    }
                    content_type = content_types.get(ext, "application/octet-stream")
                    adjuntos.append((nombre, contenido, content_type))
                else:
                    print(f"      [SHAREPOINT] Error descargando {nombre}")

            print(f"      [SHAREPOINT] {len(adjuntos)} archivos descargados exitosamente")

        except requests.exceptions.Timeout:
            print(f"      [SHAREPOINT] Timeout accediendo a SharePoint")
        except Exception as e:
            print(f"      [SHAREPOINT] Error: {type(e).__name__}: {e}")

        return adjuntos, radicado

    def _extraer_folder_path(self, url: str) -> Optional[str]:
        """Extrae el path de la carpeta de la URL redirigida"""
        match = re.search(r'id=([^&]+)', url)
        if match:
            return unquote(match.group(1))
        return None

    def _extraer_base_url(self, url: str) -> Optional[str]:
        """Extrae la URL base del sitio personal de SharePoint"""
        match = re.match(r'(https://[^/]+/personal/[^/]+)', url)
        if match:
            return match.group(1)
        return None

    def _extraer_form_digest(self, html: str) -> str:
        """Extrae el FormDigest del HTML de SharePoint"""
        match = re.findall(r'"formDigestValue":"([^"]+)"', html)
        return match[0] if match else ""

    def _extraer_radicado_de_path(self, folder_path: str) -> Optional[str]:
        """Busca un radicado de 23 dígitos en el path de la carpeta"""
        match = re.search(r'\b(\d{23})\b', folder_path)
        return match.group(1) if match else None

    def _listar_archivos(self, session: requests.Session, base_url: str,
                         folder_path: str, form_digest: str, _depth: int = 0) -> List[dict]:
        """
        Lista los archivos de una carpeta via API de SharePoint.
        Itera recursivamente sobre subcarpetas (máximo 5 niveles de profundidad).
        """
        if _depth > 5:
            print(f"      [SHAREPOINT] Profundidad máxima alcanzada en: {folder_path}")
            return []

        indent = "   " * _depth
        headers = {
            "Accept": "application/json;odata=nometadata",
            "X-RequestDigest": form_digest,
        }

        archivos = []

        # 1. Listar archivos directos en esta carpeta
        files_url = f"{base_url}/_api/web/GetFolderByServerRelativePath(decodedurl='{folder_path}')/Files"
        resp = session.get(files_url, headers=headers, timeout=30)
        if resp.status_code == 200:
            for f in resp.json().get("value", []):
                archivos.append({
                    "nombre": f.get("Name", ""),
                    "server_relative_url": f.get("ServerRelativeUrl", ""),
                    "size": int(f.get("Length", 0)),
                })
        else:
            print(f"      [SHAREPOINT]{indent} Error listando archivos en {folder_path}: {resp.status_code}")

        # 2. Listar subcarpetas e iterar recursivamente
        folders_url = f"{base_url}/_api/web/GetFolderByServerRelativePath(decodedurl='{folder_path}')/Folders"
        resp_folders = session.get(folders_url, headers=headers, timeout=30)
        if resp_folders.status_code == 200:
            subcarpetas = resp_folders.json().get("value", [])
            # Filtrar carpetas del sistema de SharePoint
            subcarpetas = [
                sc for sc in subcarpetas
                if sc.get("Name", "") not in ("Forms", "_private", "_catalogs")
            ]
            if subcarpetas:
                print(f"      [SHAREPOINT]{indent} {len(subcarpetas)} subcarpeta(s) encontradas en: {folder_path.split('/')[-1]}")
            for subcarpeta in subcarpetas:
                sub_name = subcarpeta.get("Name", "")
                sub_path = subcarpeta.get("ServerRelativeUrl", "")
                if not sub_path:
                    continue
                print(f"      [SHAREPOINT]{indent} → Entrando a subcarpeta: {sub_name}")
                archivos_sub = self._listar_archivos(session, base_url, sub_path, form_digest, _depth + 1)
                archivos.extend(archivos_sub)
        else:
            print(f"      [SHAREPOINT]{indent} Error listando subcarpetas en {folder_path}: {resp_folders.status_code}")

        return archivos

    def _descargar_archivo(self, session: requests.Session, base_url: str,
                           server_relative_url: str) -> Optional[bytes]:
        """Descarga un archivo por su ServerRelativeUrl"""
        # Construir URL completa
        domain_match = re.match(r'(https://[^/]+)', base_url)
        if not domain_match:
            return None

        download_url = f"{domain_match.group(1)}{quote(server_relative_url)}"

        resp = session.get(download_url, timeout=60)
        if resp.status_code == 200 and len(resp.content) > 0:
            return resp.content

        return None
