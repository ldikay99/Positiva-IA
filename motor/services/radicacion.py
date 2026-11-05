"""
Servicio de radicacion de PQRD
"""
import io
import time
import zipfile
import httpx
from typing import List, Tuple, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import settings
from models.radicacion import RadicacionPayload, RadicacionResponse, AsignacionPayload, TrazabilidadPayload


# Decorador de retry para llamadas HTTP
def http_retry(attempts=3):
    return retry(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True
    )


class RadicacionService:
    """Cliente para radicacion de PQRD en SGDEA"""

    def __init__(self, timeout: float = 120):
        """
        `timeout` acota cada peticion a SGDEA.

        Se puede bajar cuando quien llama tiene una ventana de tiempo: con tres
        intentos por radicacion, el valor por defecto permite que un solo correo
        consuma varios minutos.
        """
        self._token: Optional[str] = None
        self._token_expires_at: float = 0  # timestamp unix cuando expira
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Cierra el cliente HTTP compartido"""
        await self._client.aclose()

    def _invalidate_token(self):
        """Invalida el token actual para forzar renovación"""
        self._token = None
        self._token_expires_at = 0

    def _is_token_valid(self) -> bool:
        """Verifica si el token existe y no está por expirar (margen de 60 segundos)"""
        return bool(self._token) and time.time() < (self._token_expires_at - 60)

    @http_retry()
    async def _get_token(self, force_refresh: bool = False) -> str:
        """Obtiene token de acceso SGDEA via Keycloak"""
        if not force_refresh and self._is_token_valid():
            return self._token

        # Usar Keycloak directamente con password grant
        token_url = settings.SGDEA_TOKEN_URL
        payload = {
            "grant_type": "password",
            "client_id": settings.SGDEA_CLIENT_ID,
            "client_secret": settings.SGDEA_CLIENT_SECRET,
            "username": settings.SGDEA_USERNAME,
            "password": settings.SGDEA_PASSWORD,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        response = await self._client.post(token_url, data=payload, headers=headers, timeout=60)
        response.raise_for_status()
        token_data = response.json()

        self._token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 300)  # default 5 min si no viene
        self._token_expires_at = time.time() + expires_in
        return self._token
    
    async def download_file(self, url: str) -> Optional[bytes]:
        """Descarga un archivo desde una URL"""
        try:
            response = await self._client.post(
                f"{settings.AGENTS_IA_URL}/download",
                json={"url": url},
                timeout=30,
            )
            if response.status_code >= 400:
                return None
            return response.content
        except Exception:
            return None
    
    def create_zip_archive(self, files: List[Tuple[str, bytes]]) -> bytes:
        """Crea un archivo ZIP con los archivos proporcionados"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in files:
                zf.writestr(name, content)
        return buffer.getvalue()
    
    def _normalize_text(self, text: str) -> str:
        """Normaliza texto para evitar problemas de encoding"""
        import unicodedata
        # Normalizar a forma NFKD y luego a ASCII ignorando errores
        if not text:
            return text
        # Reemplazar caracteres especiales comunes
        replacements = {
            'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
            'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
            'Ñ': 'N', 'ñ': 'n', 'Ü': 'U', 'ü': 'u',
        }
        for orig, repl in replacements.items():
            text = text.replace(orig, repl)
        return text

    def _get_mime_type(self, filename: str) -> str:
        """Obtiene el MIME type basado en la extensión del archivo"""
        ext = filename.lower().split('.')[-1] if '.' in filename else ''
        mime_types = {
            # Documentos
            'pdf': 'application/pdf',
            'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'odt': 'application/vnd.oasis.opendocument.text',
            'rtf': 'application/rtf',
            'txt': 'text/plain',
            # Hojas de cálculo
            'xls': 'application/vnd.ms-excel',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'csv': 'text/csv',
            'ods': 'application/vnd.oasis.opendocument.spreadsheet',
            # Presentaciones
            'ppt': 'application/vnd.ms-powerpoint',
            'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'odp': 'application/vnd.oasis.opendocument.presentation',
            # Imágenes
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'bmp': 'image/bmp',
            'tif': 'image/tiff',
            'tiff': 'image/tiff',
            'webp': 'image/webp',
            'svg': 'image/svg+xml',
            # Archivos comprimidos
            'zip': 'application/zip',
            'rar': 'application/vnd.rar',
            '7z': 'application/x-7z-compressed',
            # Web
            'html': 'text/html',
            'xml': 'application/xml',
            'json': 'application/json',
            # Firma digital
            'p7m': 'application/pkcs7-mime',
            'p7s': 'application/pkcs7-signature',
            'p12': 'application/x-pkcs12',
        }
        return mime_types.get(ext, 'application/octet-stream')

    async def radicar_pqrd(self, payload: RadicacionPayload, attachment_files: List[Tuple[str, bytes]]) -> RadicacionResponse:
        """Radica una PQRD en SGDEA con archivos individuales.

        Implementa reintentos con refresh de token en caso de 401.
        """
        # MODO_TEST: no se radica en SGDEA; se graba el payload en CSV y se
        # devuelve una respuesta stub para que el pipeline continue igual.
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().registrar_radicacion(payload, n_adjuntos=len(attachment_files))
            return RadicacionResponse(
                id=0,
                id_radicado="DRY-RUN-ENT",
                fecha_radicacion="",
            )

        max_attempts = 3
        last_error = None

        for attempt in range(max_attempts):
            # Forzar refresh del token en reintentos
            force_refresh = attempt > 0
            token = await self._get_token(force_refresh=force_refresh)

            form_data = payload.to_form_data()
            # Normalizar todos los valores para evitar problemas de encoding
            form_data = {k: self._normalize_text(v) if isinstance(v, str) else v for k, v in form_data.items()}

            # Construir lista de archivos para multipart
            # Deduplicar nombres para evitar 409 en Alfresco
            files_list = []
            html_fallback = None
            nombres_usados: dict = {}
            for filename, content in attachment_files:
                if filename.lower().endswith('.html') and 'correo' in filename.lower():
                    html_fallback = (filename, content, 'text/html')
                    continue

                # Si el nombre ya existe, agregar sufijo numerico
                nombre_final = filename
                if filename in nombres_usados:
                    nombres_usados[filename] += 1
                    base, ext = (filename.rsplit('.', 1) + [''])[:2]
                    nombre_final = f"{base}_{nombres_usados[filename]}.{ext}" if ext else f"{base}_{nombres_usados[filename]}"
                else:
                    nombres_usados[filename] = 0

                mime_type = self._get_mime_type(filename)
                files_list.append(('files', (nombre_final, content, mime_type)))

            # El endpoint solo acepta multipart/form-data, y httpx elige el tipo
            # de contenido segun lo que reciba: con `files` vacio envia
            # x-www-form-urlencoded y el backend responde 500
            # HttpMediaTypeNotSupported. El documento del correo se guarda
            # aparte para cubrir ese caso sin duplicarlo cuando la PQRD ya trae
            # adjuntos propios.
            if not files_list and html_fallback:
                files_list.append(('files', html_fallback))

            if not files_list:
                # Enviarlo asi produce un 500 cuyo texto no dice que falto un
                # documento. Se corta antes, nombrando la causa.
                raise ValueError(
                    "la radicacion no lleva ningun documento: el endpoint solo "
                    "acepta multipart/form-data y sin archivos la peticion se "
                    "envia como formulario, que el backend rechaza. Debe "
                    "adjuntarse al menos el documento del correo."
                )

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://docu.positiva.gov.co",
                "Referer": "https://docu.positiva.gov.co/",
            }

            try:
                response = await self._client.post(
                    f"{settings.MS_SGDEA_BETA_URL}/pqrd/radicar-pqrd",
                    data=form_data,
                    files=files_list,
                    headers=headers,
                )

                # Éxito
                if response.status_code < 400:
                    return RadicacionResponse.from_api_response(response.json())

                # Error 401: token expirado, invalidar y reintentar
                if response.status_code == 401:
                    self._invalidate_token()
                    last_error = f"Radicacion fallida: 401 - Token expirado (intento {attempt + 1}/{max_attempts})"
                    continue

                # Radicar no es idempotente: crea un radicado nuevo en cada
                # llamada. Un 5xx describe lo que le paso al servidor DESPUES
                # de recibir la peticion, y no dice si alcanzo a crear el
                # registro, asi que repetirla puede dejar dos. Se devuelve el
                # fallo y lo decide la capa de arriba, que aplaza el correo a
                # la corrida siguiente; alli las capas de deduplicacion vuelven
                # a mirar antes de radicar.
                raise Exception(f"Radicacion fallida: {response.status_code} - {response.text}")

            except httpx.TimeoutException:
                # Tampoco se repite: agotar el tiempo de espera es el caso con
                # mas probabilidad de que el servidor si haya radicado.
                raise Exception(
                    "Timeout en radicacion: la peticion pudo haberse "
                    "procesado, no se repite para no duplicar el radicado")
            except httpx.ConnectError as e:
                # La conexion no llego a establecerse: el servidor no vio la
                # peticion y repetirla no puede duplicar nada.
                last_error = f"Error de conexion: {e} (intento {attempt + 1}/{max_attempts})"
                if attempt < max_attempts - 1:
                    continue

        # Si llegamos aquí, todos los intentos fallaron
        raise Exception(last_error or "Radicacion fallida despues de varios intentos")
    
    @http_retry()
    async def asignar_pqrd(self, pqrd_id: int, payload: AsignacionPayload) -> None:
        """Asigna una PQRD a un funcionario"""
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().registrar("ASIGNAR", destino=f"pqrd/{pqrd_id}", detalle=payload.to_dict())
            return

        token = await self._get_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        try:
            response = await self._client.post(
                f"{settings.MS_SGDEA_BETA_URL}/pqrd/asignar/{pqrd_id}",
                json=payload.to_dict(),
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
        except httpx.TimeoutException:
            raise Exception(f"Timeout asignando PQRD {pqrd_id} a funcionario {payload.id_gestionador}")
        except httpx.HTTPStatusError as e:
            raise Exception(f"Error HTTP {e.response.status_code} asignando PQRD: {e.response.text}")
    
    @http_retry()
    async def crear_trazabilidad(self, payload: TrazabilidadPayload) -> None:
        """Crea un registro de trazabilidad"""
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().registrar("TRAZABILIDAD", destino="SGDEA", detalle=payload.to_dict())
            return

        token = await self._get_token()
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        
        response = await self._client.post(
            f"{settings.MS_SGDEA_BETA_URL}/trazabilidad/crear",
            json=payload.to_dict(),
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
