"""
Servicio para conexión con Microsoft Graph API (Outlook)
Usa ROPC (Resource Owner Password Credentials) Flow
"""
import requests
import re
import base64
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Optional, Dict
from html import escape, unescape

try:
    from Tutelas.config import OUTLOOK_CONFIG, MODO_TEST, OMITIR_ESCRITURAS, DISCLAIMER_IA
    from Tutelas.test_recorder import get_recorder
    from Tutelas.models.correo import Correo, Adjunto
except ImportError:
    from config import OUTLOOK_CONFIG, MODO_TEST, OMITIR_ESCRITURAS, DISCLAIMER_IA
    from test_recorder import get_recorder
    from models.correo import Correo, Adjunto


class OutlookService:
    def __init__(self):
        self.config = OUTLOOK_CONFIG
        self.access_token = None
        self.token_expires = None
        self._folder_ids: Dict[str, str] = {}  # Cache de IDs de carpetas

    def _get_token(self) -> str:
        """Obtiene el token de acceso con las credenciales de la aplicación.

        Autentica la aplicación, no a la persona dueña del buzón, por lo que no
        depende de la contraseña ni queda bloqueado cuando el tenant exige doble
        factor. El acceso al buzón lo dan los permisos de aplicación concedidos al
        registro (Mail.Read, Mail.ReadWrite y Mail.Send).
        """
        if self.access_token and self.token_expires and datetime.now() < self.token_expires:
            return self.access_token

        url = f"https://login.microsoftonline.com/{self.config['tenant']}/oauth2/v2.0/token"

        data = {
            "grant_type": "client_credentials",
            "client_id": self.config["client_id"],
            "client_secret": self.config["client_secret"],
            "scope": "https://graph.microsoft.com/.default",
        }

        response = requests.post(url, data=data, timeout=30)

        if response.status_code != 200:
            # El cuerpo trae el codigo de Microsoft, necesario para distinguir un
            # secreto vencido de un permiso retirado.
            print(f"[OUTLOOK] No se obtuvo el token ({response.status_code}): "
                  f"{response.text[:300]}", flush=True)
        response.raise_for_status()

        result = response.json()
        self.access_token = result["access_token"]

        expires_in = result.get("expires_in", 3600) - 300
        self.token_expires = datetime.now() + timedelta(seconds=expires_in)

        return self.access_token

    def _headers(self) -> dict:
        """Headers para las peticiones a Graph API"""
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def _strip_html(self, html: str) -> str:
        """Limpia HTML y retorna texto plano"""
        if not html:
            return ""
        text = re.sub(r'<[^>]*>', ' ', html)
        text = unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    
    def _get_base_url(self) -> str:
        """URL base del buzón que atiende el agente.

        Se nombra el buzón de forma explícita porque el token es de aplicación y
        no representa a ningun usuario: la ruta /me no aplica en ese caso.
        """
        return f"{self.config['graph_url']}/users/{self.config['username']}"


    def _get_folder_id(self, folder_name: str) -> Optional[str]:
        """Obtiene el ID de una carpeta por su nombre"""
        # Revisar cache
        if folder_name in self._folder_ids:
            return self._folder_ids[folder_name]

        # Buscar la carpeta
        url = f"{self._get_base_url()}/mailFolders"
        params = {"$filter": f"displayName eq '{folder_name}'"}

        response = requests.get(url, headers=self._headers(), params=params)
        if response.status_code != 200:
            print(f"Error buscando carpeta '{folder_name}': {response.status_code}")
            return None

        folders = response.json().get("value", [])
        if folders:
            folder_id = folders[0]["id"]
            self._folder_ids[folder_name] = folder_id
            return folder_id

        # Si no está en el nivel raíz, buscar en subcarpetas
        url = f"{self._get_base_url()}/mailFolders?$expand=childFolders"
        response = requests.get(url, headers=self._headers())
        if response.status_code == 200:
            for folder in response.json().get("value", []):
                for child in folder.get("childFolders", []):
                    if child["displayName"] == folder_name:
                        folder_id = child["id"]
                        self._folder_ids[folder_name] = folder_id
                        return folder_id

        print(f"Carpeta '{folder_name}' no encontrada")
        return None

    def obtener_correos(self, limite: int = 10, solo_con_adjuntos: bool = True) -> List[Correo]:
        """Obtiene los últimos correos de la carpeta de entrada"""
        folder_id = self.config["folder_entrada"]
        url = f"{self._get_base_url()}/mailFolders/{folder_id}/messages"

        params = {
            "$top": limite,
            "$select": "id,subject,body,from,receivedDateTime,hasAttachments,conversationId",
        }

        # No combinar $filter con $orderby (error InefficientFilter)
        if solo_con_adjuntos:
            params["$filter"] = "hasAttachments eq true"
        else:
            params["$orderby"] = "receivedDateTime desc"

        response = requests.get(url, headers=self._headers(), params=params)
        response.raise_for_status()

        correos = []
        for msg in response.json().get("value", []):
            body_content = msg.get("body", {}).get("content", "")

            correo = Correo(
                id=msg["id"],
                subject=msg.get("subject", ""),
                body_content=body_content,
                body_text=self._strip_html(body_content),
                remitente_email=msg.get("from", {}).get("emailAddress", {}).get("address", ""),
                remitente_nombre=msg.get("from", {}).get("emailAddress", {}).get("name", ""),
                fecha_recibido=datetime.fromisoformat(msg["receivedDateTime"].replace("Z", "+00:00")),
                conversation_id=msg.get("conversationId"),
                adjuntos=[],
            )
            correos.append(correo)

        return correos

    def obtener_adjuntos(self, correo_id: str) -> List[Adjunto]:
        """Obtiene la lista de adjuntos de un correo"""
        url = f"{self._get_base_url()}/messages/{correo_id}/attachments"

        response = requests.get(url, headers=self._headers())
        if response.status_code == 404:
            print(f"      [OUTLOOK] Mensaje no encontrado (404), omitiendo adjuntos")
            return []
        response.raise_for_status()

        adjuntos = []
        for att in response.json().get("value", []):
            adjunto = Adjunto(
                id=att["id"],
                nombre=att.get("name", ""),
                content_type=att.get("contentType", ""),
                contenido=b"",
            )
            adjuntos.append(adjunto)

        return adjuntos

    def descargar_adjunto(self, correo_id: str, adjunto_id: str) -> bytes:
        """Descarga el contenido binario de un adjunto"""
        url = f"{self._get_base_url()}/messages/{correo_id}/attachments/{adjunto_id}"

        response = requests.get(url, headers=self._headers())
        if response.status_code == 404:
            print(f"      [OUTLOOK] Adjunto no encontrado (404), omitiendo")
            return b""
        response.raise_for_status()

        data = response.json()
        content_bytes = data.get("contentBytes", "")
        if content_bytes:
            return base64.b64decode(content_bytes)
        return b""

    def etiquetar_correo(self, correo_id: str, etiqueta: str) -> bool:
        """Agrega una categoría/etiqueta al correo sin quitar las existentes"""
        if OMITIR_ESCRITURAS:
            get_recorder().registrar("ETIQUETAR", destino=etiqueta, detalle={"correo_id": correo_id})
            return True
        url = f"{self._get_base_url()}/messages/{correo_id}"
        try:
            # Obtener categorías actuales
            resp = requests.get(url, headers=self._headers(), params={"$select": "categories"}, timeout=15)
            if resp.status_code == 404:
                print(f"      [OUTLOOK] No se pudo etiquetar (404): mensaje no encontrado")
                return False
            resp.raise_for_status()
            categorias = resp.json().get("categories", [])

            # Agregar la nueva sin duplicar
            if etiqueta not in categorias:
                categorias.append(etiqueta)

            # Actualizar
            response = requests.patch(url, headers=self._headers(), json={"categories": categorias}, timeout=15)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"      [OUTLOOK] Error etiquetando correo: {e}")
            return False

    def mover_correo(self, correo_id: str, folder_id: str) -> bool:
        """Mueve un correo a otra carpeta por ID.

        Un 404 aqui significa que el mensaje ya no esta donde se leyo: otra
        ejecucion lo movio primero y el identificador quedo obsoleto.
        """
        if OMITIR_ESCRITURAS:
            get_recorder().registrar("MOVER", destino=folder_id, detalle={"correo_id": correo_id})
            return True
        url = f"{self._get_base_url()}/messages/{correo_id}/move"
        data = {"destinationId": folder_id}

        try:
            response = requests.post(url, headers=self._headers(), json=data, timeout=30)
        except Exception as e:
            print(f"      [OUTLOOK] No se pudo mover el correo: {type(e).__name__}: {e}")
            return False
        if response.status_code == 201:
            return True
        if response.status_code == 404:
            print(f"      [OUTLOOK] No se pudo mover (404): el mensaje ya no esta en la carpeta")
        else:
            print(f"      [OUTLOOK] No se pudo mover: {response.status_code}")
        return False

    def mover_a_revision_manual(self, correo_id: str) -> bool:
        """Mueve el correo a la carpeta de revisión manual"""
        return self.mover_correo(correo_id, self.config["folder_revision_manual"])

    def mover_a_radicacion_exitosa(self, correo_id: str) -> bool:
        """Mueve el correo a la carpeta de radicación exitosa"""
        return self.mover_correo(correo_id, self.config["folder_radicacion_exitosa"])

    def marcar_como_leido(self, correo_id: str) -> bool:
        """Marca un correo como leído"""
        if MODO_TEST:
            get_recorder().registrar("MARCAR_LEIDO", destino=correo_id)
            return True
        url = f"{self._get_base_url()}/messages/{correo_id}"
        data = {"isRead": True}

        response = requests.patch(url, headers=self._headers(), json=data)
        return response.status_code == 200

    def obtener_correos_por_conversacion(self, limite: int = 50, solo_con_adjuntos: bool = False) -> List[List[Correo]]:
        """
        Obtiene correos agrupados por conversationId (hilo de conversación).
        Trae TODOS los correos sin filtrar por adjuntos para capturar hilos completos.
        Retorna una lista de hilos, donde cada hilo es una lista de correos
        ordenados cronológicamente (más antiguo primero, más reciente al final).
        """
        correos = self.obtener_correos(limite=limite, solo_con_adjuntos=solo_con_adjuntos)

        # Agrupar por conversationId
        hilos_dict = defaultdict(list)
        for correo in correos:
            clave = correo.conversation_id or correo.id  # Fallback al id si no hay conversationId
            hilos_dict[clave].append(correo)

        # Ordenar cada hilo cronológicamente (más antiguo primero)
        hilos = []
        for conv_id, correos_hilo in hilos_dict.items():
            correos_hilo.sort(key=lambda c: c.fecha_recibido)
            hilos.append(correos_hilo)

        print(f"   Hilos de conversación encontrados: {len(hilos)}")
        for i, hilo in enumerate(hilos):
            print(f"      Hilo {i+1}: {len(hilo)} correo(s) - '{hilo[-1].subject[:50]}...'")

        return hilos

    def mover_correos_masivo(self, correo_ids: List[str], folder_id: str) -> int:
        """
        Mueve múltiples correos a una carpeta.
        Retorna la cantidad de correos movidos exitosamente.
        """
        movidos = 0
        for correo_id in correo_ids:
            if self.mover_correo(correo_id, folder_id):
                movidos += 1
            else:
                print(f"      [OUTLOOK] Error moviendo correo {correo_id[:20]}...")
        return movidos

    def mover_hilo_a_tutelas(self, correo_ids: List[str]) -> int:
        """Mueve todos los correos de un hilo a la carpeta TUTELAS"""
        folder_id = self._get_folder_id(self.config["folder_tutelas_nombre"])
        if not folder_id:
            print(f"      [OUTLOOK] Error: carpeta '{self.config['folder_tutelas_nombre']}' no encontrada")
            return 0
        return self.mover_correos_masivo(correo_ids, folder_id)

    def mover_hilo_a_bandeja_entrada(self, correo_ids: List[str]) -> int:
        """Mueve todos los correos de un hilo a la Bandeja de Entrada (Inbox)"""
        return self.mover_correos_masivo(correo_ids, self.config["folder_inbox"])

    def responder_radicacion_exitosa(self, correo_id: str, numero_radicado: str) -> bool:
        """
        Responde al correo original con la confirmación de radicación exitosa.
        Envía la respuesta a notificacionesjudiciales@positiva.gov.co

        Args:
            correo_id: ID del correo original
            numero_radicado: Número de radicado generado (ej: ENT20260000331148)

        Returns:
            True si se envió correctamente
        """
        if OMITIR_ESCRITURAS:
            get_recorder().registrar("RESPONDER_CORREO", destino="notificacionesjudiciales@positiva.gov.co",
                                     detalle={"correo_id": correo_id, "radicado": numero_radicado})
            return True

        url = f"{self._get_base_url()}/messages/{correo_id}/reply"

        # Destinatario fijo: notificaciones judiciales de Positiva
        destinatario = "notificacionesjudiciales@positiva.gov.co"

        # Mensaje de confirmación en formato HTML
        mensaje_html = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #0066cc; margin-bottom: 10px;">Notificaciones Judiciales</h2>
            <h3 style="color: #28a745; margin-bottom: 15px;">Radicación exitosa con IA</h3>
            <p style="font-size: 14px; color: #333;">
                Trámite de tutelas generado correctamente, número de radicado <strong>{escape(str(numero_radicado))}</strong>.
            </p>
            <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
            <p style="font-size: 11px; color: #666; font-style: italic;">
                {DISCLAIMER_IA}
            </p>
        </div>
        """

        data = {
            "message": {
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": destinatario,
                            "name": "Notificaciones Judiciales"
                        }
                    }
                ],
                "body": {
                    "contentType": "HTML",
                    "content": mensaje_html
                }
            },
            "comment": ""
        }

        try:
            print(f"      [OUTLOOK] Respondiendo correo con radicado: {numero_radicado}")
            print(f"      [OUTLOOK] Destinatario: {destinatario}")
            response = requests.post(url, headers=self._headers(), json=data)

            if response.status_code == 202:
                print(f"      [OUTLOOK] ✅ Respuesta enviada exitosamente")
                return True
            else:
                print(f"      [OUTLOOK] ❌ Error enviando respuesta: {response.status_code}")
                print(f"      [OUTLOOK] Response: {response.text[:300]}")
                return False
        except Exception as e:
            print(f"      [OUTLOOK] ❌ Error: {type(e).__name__}: {e}")
            return False

    def responder_no_radicado(self, correo_id: str, motivo: str,
                              radicado_23: Optional[str] = None,
                              accion: str = "El caso queda disponible para gestión manual.",
                              asunto_original: Optional[str] = None) -> bool:
        """
        Informa al buzón interno que el correo no se radico automaticamente.

        La respuesta se dirige siempre a notificacionesjudiciales@positiva.gov.co;
        el remitente original del correo no la recibe.

        Args:
            correo_id: ID del correo del hilo
            motivo: descripcion funcional de por que no se radico
            radicado_23: numero de proceso, si se alcanzo a identificar
            accion: que debe hacer el area con el caso
            asunto_original: asunto del correo, para identificarlo en la respuesta

        Returns:
            True si se envio correctamente
        """
        destinatario = "notificacionesjudiciales@positiva.gov.co"

        if OMITIR_ESCRITURAS:
            get_recorder().registrar("RESPONDER_NO_RADICADO", destino=destinatario,
                                     detalle={"correo_id": correo_id, "motivo": motivo,
                                              "radicado": radicado_23, "accion": accion})
            return True

        url = f"{self._get_base_url()}/messages/{correo_id}/reply"

        # El asunto lo controla quien envia el correo, por lo que se escapa antes
        # de insertarlo en el HTML del mensaje.
        detalles = ""
        if asunto_original:
            detalles += (
                f'<p style="font-size: 13px; color: #555; margin: 4px 0;">'
                f'<strong>Correo:</strong> {escape(str(asunto_original))}</p>'
            )
        if radicado_23:
            detalles += (
                f'<p style="font-size: 13px; color: #555; margin: 4px 0;">'
                f'<strong>Número de proceso:</strong> {escape(str(radicado_23))}</p>'
            )
        if detalles:
            detalles = (
                '<div style="background: #f7f7f7; border-left: 3px solid #0066cc; '
                'padding: 10px 14px; margin: 14px 0;">' + detalles + '</div>'
            )

        mensaje_html = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #0066cc; margin-bottom: 10px;">Notificaciones Judiciales</h2>
            <h3 style="color: #c0392b; margin-bottom: 15px;">Radicación no realizada</h3>
            <p style="font-size: 14px; color: #333;">
                Este correo no fue radicado automáticamente por el asistente de
                radicación. {motivo}
            </p>
            {detalles}
            <p style="font-size: 14px; color: #333;">
                {accion}
            </p>
            <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
            <p style="font-size: 11px; color: #666; font-style: italic;">
                {DISCLAIMER_IA}
            </p>
        </div>
        """

        data = {
            "message": {
                "toRecipients": [
                    {"emailAddress": {"address": destinatario,
                                      "name": "Notificaciones Judiciales"}}
                ],
                "body": {"contentType": "HTML", "content": mensaje_html},
            },
            "comment": "",
        }

        try:
            print(f"      [OUTLOOK] Informando no radicacion al buzón interno")
            response = requests.post(url, headers=self._headers(), json=data, timeout=30)
            if response.status_code == 202:
                print(f"      [OUTLOOK] Aviso de no radicacion enviado")
                return True
            print(f"      [OUTLOOK] No se pudo enviar el aviso: {response.status_code}")
            return False
        except Exception as e:
            print(f"      [OUTLOOK] No se pudo enviar el aviso: {type(e).__name__}: {e}")
            return False
