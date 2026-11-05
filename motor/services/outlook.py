"""
Servicio de Microsoft Graph API para Outlook
"""
import asyncio
import time
import httpx
from typing import List, Optional, Dict, Any
from config import settings
from models.email import EmailMessage, EmailAttachment
from config.settings import dedup_desactivada
from utils.radicado import etiqueta_de_radicado


from pipeline.marcas import OtraCorridaLoTomo  # noqa: F401  (se reexporta)

# Graph limita por tasa y responde 429 diciendo en `Retry-After` cuantos
# segundos esperar. Reintentos de la peticion que lee y escribe las categorias:
# es la que reclama el correo, y perderla deja el correo sin dueño para la
# corrida siguiente.
INTENTOS_POR_LIMITE_DE_TASA = 4
# Tope de una sola espera. El job corre cada media hora y atiende un lote: una
# espera larga la pagan los correos que vienen detras.
ESPERA_MAXIMA_POR_LIMITE_S = 20.0
# Cuando Graph no dice cuanto esperar.
ESPERA_POR_DEFECTO_S = 3.0
# Intentos ante un corte de red o un 5xx al escribir la marca. Un timeout no
# dice nada del correo y se repara solo; rendirse al primero deja el correo sin
# la cuenta de sus aplazamientos, y sin cuenta puede dar vueltas sin llegar
# nunca a revision manual.
INTENTOS_POR_FALLO_DE_RED = 3
ESPERA_ENTRE_INTENTOS_S = 2.0
# Hasta donde se busca por asunto. Un asunto generico trae decenas de correos, y
# el radicado del mismo remitente no tiene por que estar entre los primeros.
RESULTADOS_POR_PAGINA_DE_ASUNTO = 50
PAGINAS_POR_ASUNTO = 3
# Cuantos radicados con ese asunto se leen: cada uno cuesta una peticion mas.
CANDIDATOS_POR_ASUNTO = 8


def espera_que_pide_graph(response) -> float:
    """
    Segundos a esperar tras un 429, segun la cabecera `Retry-After`.

    Sin cabecera, o con una que no es un numero, se usa la espera por defecto.
    El valor se acota: Graph puede pedir minutos y la corrida no los tiene.
    """
    crudo = (response.headers.get("Retry-After") or "").strip()
    try:
        segundos = float(crudo)
    except ValueError:
        segundos = ESPERA_POR_DEFECTO_S
    if segundos <= 0:
        segundos = ESPERA_POR_DEFECTO_S
    return min(segundos, ESPERA_MAXIMA_POR_LIMITE_S)


class OutlookService:
    """Cliente para Microsoft Graph API (Outlook)"""

    # Ultimo fallo de una consulta de deduplicacion, o cadena vacia. Esas
    # consultas devuelven None tanto si no hay duplicado como si la llamada se
    # cayo: sin esto, el llamador da por comprobado algo que nunca se comprobo.
    ultimo_error: str = ""

    def __init__(self):
        self.user_email = settings.OUTLOOK_USER_EMAIL
        self.base_url = "https://graph.microsoft.com/v1.0"
        self._access_token: Optional[str] = None
        self._refresh_token = settings.OUTLOOK_REFRESH_TOKEN
        self._token_expires_at: float = 0
        self._client = httpx.AsyncClient(timeout=60)

    async def close(self) -> None:
        """Cierra el cliente HTTP compartido"""
        await self._client.aclose()

    async def _get_access_token(self, force_refresh: bool = False) -> str:
        """Obtiene token de acceso usando refresh token. Renueva automaticamente."""
        if not force_refresh and self._access_token and time.time() < (self._token_expires_at - 120):
            return self._access_token

        token_url = f"https://login.microsoftonline.com/{settings.MICROSOFT_TENANT_ID}/oauth2/v2.0/token"

        #data = {
        #    "grant_type": "refresh_token",
        #    "client_id": settings.MICROSOFT_CLIENT_ID,
        #    #"client_secret": settings.MICROSOFT_CLIENT_SECRET,
        #    "refresh_token": self._refresh_token,
        #    "scope": "https://graph.microsoft.com/.default offline_access",
        #}

        data = {
            "grant_type": "client_credentials",
            "client_id": settings.MICROSOFT_CLIENT_ID,
            "client_secret": settings.MICROSOFT_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
        }

        response = await self._client.post(token_url, data=data)
        #response.raise_for_status()
        if response.status_code >= 400:
            raise Exception(
                f"ERROR TOKEN MICROSOFT {response.status_code} | "
                f"url={token_url} | body={response.text}"
            )
        token_data = response.json()

        self._access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 3600)
        self._token_expires_at = time.time() + expires_in
        if "refresh_token" in token_data:
            self._refresh_token = token_data["refresh_token"]

        return self._access_token
    
    def _invalidate_token(self) -> None:
        """Invalida el token para forzar renovacion"""
        self._access_token = None
        self._token_expires_at = 0

    async def _get_headers(self) -> Dict[str, str]:
        """Obtiene headers con autorizacion"""
        token = await self._get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    
    async def get_emails(self, folder_id: str, limit: int = 50) -> List[EmailMessage]:
        """Obtiene correos agrupados por conversationId (hilo).

        Trae todos los correos y agrupa por hilo. Retorna solo el mas reciente
        de cada hilo. Los IDs de los demas mensajes del hilo se guardan en
        el campo conversation_id para moverlos despues.
        """
        url = f"{self.base_url}/users/{self.user_email}/mailFolders/{folder_id}/messages"
        params = {
            "$top": limit * 3,
            "$select": "id,subject,body,from,sender,toRecipients,receivedDateTime,sentDateTime,hasAttachments,internetMessageId,isRead,conversationId,categories",
            "$orderby": "receivedDateTime asc",
        }

        response = await self._consultar(url, params)
        response.raise_for_status()
        messages = response.json().get("value", [])

        # Agrupar por conversationId: tomar solo el mas reciente de cada hilo
        seen_conversations = {}
        unique_messages = []
        for msg in messages:
            conv_id = msg.get("conversationId", msg.get("id"))
            if conv_id not in seen_conversations:
                seen_conversations[conv_id] = msg
                unique_messages.append(msg)

        return [EmailMessage.from_graph_api(msg) for msg in unique_messages[:limit]]

    async def get_conversation_message_ids(self, folder_id: str, conversation_id: str) -> List[str]:
        """Obtiene todos los IDs de mensajes de un hilo/conversacion en una carpeta"""
        url = f"{self.base_url}/users/{self.user_email}/mailFolders/{folder_id}/messages"
        params = {
            "$filter": f"conversationId eq '{conversation_id}'",
            "$select": "id",
            "$top": 50,
        }

        response = await self._consultar(url, params)
        response.raise_for_status()
        return [msg["id"] for msg in response.json().get("value", [])]

    async def hilo_ya_radicado(self, conversation_id: str) -> Optional[str]:
        """Busca en TODAS las carpetas si algun correo del hilo ya tiene etiqueta ENT.

        Usa GET /users/{id}/messages (sin mailFolder) para buscar en todo el buzon.

        Returns:
            La etiqueta ENT encontrada, o None si el hilo no fue radicado.
        """
        self.ultimo_error = ""
        if dedup_desactivada():
            return None

        url = f"{self.base_url}/users/{self.user_email}/messages"
        params = {
            "$filter": f"conversationId eq '{conversation_id}'",
            "$select": "id,categories",
            "$top": 50,
        }

        response = await self._consultar(url, params)
        response.raise_for_status()
        data = response.json()

        for msg in data.get("value", []):
            etiqueta = etiqueta_de_radicado(msg.get("categories", []))
            if etiqueta:
                return etiqueta
        return None

    async def buscar_etiqueta_ent_por_asunto(
        self, asunto_limpio: str, email: EmailMessage
    ) -> Optional[str]:
        """Busca en TODO el buzon un radicado con el mismo asunto y del mismo caso.

        Util cuando un area interna reenvia un correo ya radicado y Outlook
        genera un nuevo conversationId, rompiendo la deteccion por hilo.

        Returns:
            La etiqueta ENT* encontrada, o None si no hay match.
        """
        from utils.hash import asunto_comparable

        self.ultimo_error = ""

        # Mismo criterio que `hilo_ya_radicado`: las dos capas se apagan
        # juntas, o un e2e da un resultado que no se puede interpretar.
        if dedup_desactivada():
            return None

        if not asunto_limpio:
            return None

        # Sanitizar el asunto para $search (KQL): quitar comillas y caracteres
        # que rompen la query. Si despues de sanitizar queda muy corto, abortar
        # para evitar matches masivos no relacionados.
        sanitizado = (
            asunto_limpio
            .replace('"', " ")
            .replace("\\", " ")
            .replace("(", " ")
            .replace(")", " ")
        )
        sanitizado = " ".join(sanitizado.split()).strip()
        if len(sanitizado) < 6:
            return None

        url = f"{self.base_url}/users/{self.user_email}/messages"
        asunto_norm = asunto_comparable(asunto_limpio)
        remitente = getattr(getattr(email, "from_address", None), "address", "") or ""
        seleccion = {
            "$select": "id,subject,categories,from",
            "$top": RESULTADOS_POR_PAGINA_DE_ASUNTO,
        }
        # Primero los del mismo remitente: con un asunto generico, los radicados
        # de otras personas agotarian los candidatos antes de llegar a ellos. Van
        # por filtro y no por `$search`, que no siempre trae todos sus correos.
        consultas = [{**seleccion, "$search": f'"subject:{sanitizado}"'}]
        if remitente:
            direccion = remitente.strip().lower().replace("'", "''")
            consultas.insert(0, {
                **seleccion,
                "$filter": f"from/emailAddress/address eq '{direccion}'",
            })
        leidos = set()

        try:
            for consulta in consultas:
                etiqueta = await self._radicado_del_mismo_caso(
                    url, consulta, asunto_norm, email, leidos)
                if etiqueta or self.ultimo_error:
                    return etiqueta
        except Exception as e:
            # None significa "no hay duplicado". Si la consulta se cayo, el
            # llamador tiene que poder distinguirlo o dara por comprobado
            # algo que nunca se comprobo.
            self.ultimo_error = f"{type(e).__name__}: {e}"
        return None

    async def _radicado_del_mismo_caso(
        self, url: str, params: Optional[Dict[str, Any]], asunto_norm: str,
        email: EmailMessage, leidos: set,
    ) -> Optional[str]:
        """La etiqueta del primer resultado de la consulta que sea el mismo caso."""
        from utils.hash import asunto_comparable

        revisados = 0
        for _ in range(PAGINAS_POR_ASUNTO):
            response = await self._consultar(
                url, params, extra={"ConsistencyLevel": "eventual"})
            if response.status_code >= 400:
                self.ultimo_error = (
                    f"Graph respondio {response.status_code} al buscar por asunto")
                return None
            data = response.json()
            for msg in data.get("value", []):
                if msg.get("id") in leidos:
                    continue
                if asunto_comparable(msg.get("subject", "")) != asunto_norm:
                    continue
                etiqueta = etiqueta_de_radicado(msg.get("categories", []))
                if not etiqueta:
                    continue
                if revisados >= CANDIDATOS_POR_ASUNTO:
                    return None
                revisados += 1
                leidos.add(msg.get("id"))
                if await self._es_el_mismo_caso(email, msg):
                    return etiqueta
            url = data.get("@odata.nextLink")
            params = None
            if not url:
                break
        return None

    async def _es_el_mismo_caso(
        self, email: EmailMessage, radicado: Dict[str, Any]
    ) -> bool:
        """
        Si un correo con el asunto de un radicado es ese mismo caso.

        Del mismo remitente, es el mismo caso si repite el texto: con otro texto
        es esa persona pidiendo otra cosa. Si el texto es tan corto que no
        distingue, lo deciden los adjuntos. De otro remitente solo lo es si los
        une un reenvio.
        """
        from pipeline.duplicados import (
            estan_vinculados,
            texto_que_distingue,
            textos_iguales,
        )
        from utils.html_cleaner import clean_html_content

        remitente = (email.from_address.address if email.from_address else "").strip().lower()
        otro = (((radicado.get("from") or {}).get("emailAddress") or {})
                .get("address") or "").strip().lower()
        cuerpo = clean_html_content(email.body.content or "")
        respuesta = await self._consultar(
            f"{self.base_url}/users/{self.user_email}/messages/{radicado['id']}",
            {"$select": "body"})
        respuesta.raise_for_status()
        otro_cuerpo = clean_html_content(
            (respuesta.json().get("body") or {}).get("content") or "")

        if remitente and remitente == otro:
            if not textos_iguales(cuerpo, otro_cuerpo):
                return False
            if texto_que_distingue(cuerpo):
                return True
            nombres = await self._nombres_de_adjuntos(email.id)
            return bool(nombres) and nombres == await self._nombres_de_adjuntos(radicado["id"])
        return estan_vinculados(remitente, cuerpo, otro, otro_cuerpo)

    async def _nombres_de_adjuntos(self, message_id: str) -> List[str]:
        """Los nombres de los adjuntos de un correo, sin las imagenes de la firma."""
        return sorted(
            (a.get("name") or "").lower()
            for a in await self.get_attachments(message_id)
            if not a.get("isInline")
        )

    async def mover_hilo_completo(self, email_id: str, conversation_id: str, folder_origen_id: str, folder_destino_id: str) -> int:
        """Mueve todos los correos de un hilo a otra carpeta.

        Replica la logica de mover_correos_masivo del proyecto de tutelas.
        Primero mueve el correo principal, luego busca los demas del hilo
        en la carpeta origen y los mueve tambien.

        Returns: cantidad de correos movidos
        """
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().registrar(
                "MOVER_HILO", destino=folder_destino_id,
                detalle={"email_id": email_id, "conversation_id": conversation_id},
            )
            return 0

        movidos = 0

        # Mover correo principal
        try:
            await self.move_email(email_id, folder_destino_id)
            movidos += 1
        except Exception:
            raise

        # Mover los demas mensajes del hilo
        if conversation_id:
            try:
                otros_ids = await self.get_conversation_message_ids(folder_origen_id, conversation_id)
                for otro_id in otros_ids:
                    if otro_id != email_id:
                        try:
                            await self.move_email(otro_id, folder_destino_id)
                            movidos += 1
                        except Exception:
                            pass
            except Exception:
                pass

        return movidos
    
    async def get_email_by_id(self, message_id: str) -> EmailMessage:
        """Obtiene un correo por ID. Lanza ValueError si no existe (ya fue movido)."""
        for attempt in range(2):
            headers = await self._get_headers()
            url = f"{self.base_url}/users/{self.user_email}/messages/{message_id}"

            response = await self._client.get(url, headers=headers)
            if response.status_code == 401 and attempt == 0:
                self._invalidate_token()
                continue
            if response.status_code == 404:
                raise ValueError(f"Correo no encontrado (ya fue movido o eliminado)")
            response.raise_for_status()
            return EmailMessage.from_graph_api(response.json())
    
    async def get_attachments(self, message_id: str) -> List[Dict[str, Any]]:
        """Obtiene los adjuntos de un correo. Lanza ValueError si el correo no existe."""
        url = f"{self.base_url}/users/{self.user_email}/messages/{message_id}/attachments"
        params = {"$select": "id,name,contentType,size,isInline"}

        for attempt in range(2):
            headers = await self._get_headers()
            response = await self._client.get(url, headers=headers, params=params)
            if response.status_code == 401 and attempt == 0:
                self._invalidate_token()
                continue
            if response.status_code == 404:
                raise ValueError(f"Correo no encontrado al obtener adjuntos (ya fue movido o eliminado)")
            response.raise_for_status()
            data = response.json()
            break

        return data.get("value", [])
    
    async def download_attachment(self, message_id: str, attachment_id: str) -> str:
        """Descarga el contenido de un adjunto en base64. Lanza ValueError si no existe."""
        url = f"{self.base_url}/users/{self.user_email}/messages/{message_id}/attachments/{attachment_id}"

        for attempt in range(2):
            headers = await self._get_headers()
            response = await self._client.get(url, headers=headers, timeout=120)
            if response.status_code == 401 and attempt == 0:
                self._invalidate_token()
                continue
            if response.status_code == 404:
                raise ValueError(f"Adjunto no encontrado (correo o adjunto ya fue movido o eliminado)")
            response.raise_for_status()
            data = response.json()
            break

        return data.get("contentBytes", "")
    
    async def forward_email(self, message_id: str, to_email: str, comment: str = "") -> None:
        """Reenvía un correo a otra dirección usando Microsoft Graph API"""
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().registrar("REENVIAR", destino=to_email, detalle={"message_id": message_id})
            return

        url = f"{self.base_url}/users/{self.user_email}/messages/{message_id}/forward"

        for attempt in range(2):
            headers = await self._get_headers()
            response = await self._client.post(
                url,
                headers=headers,
                json={
                    "comment": comment,
                    "toRecipients": [{"emailAddress": {"address": to_email}}],
                },
                timeout=30,
            )
            if response.status_code == 401 and attempt == 0:
                self._invalidate_token()
                continue
            response.raise_for_status()
            break

    async def responder_correo(self, message_id: str, cuerpo_html: str) -> None:
        """
        Responde al correo dejando la respuesta en el mismo hilo.

        Se usa para el aviso de lo que la IA hizo con el correo. Va como
        respuesta y no como mensaje suelto para que quede pegada al caso: quien
        revisa el buzon ve el desenlace sin buscarlo en otro sitio.
        """
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().registrar(
                "RESPONDER", destino=message_id,
                detalle={"caracteres": len(cuerpo_html or "")})
            return

        url = f"{self.base_url}/users/{self.user_email}/messages/{message_id}/reply"

        for attempt in range(2):
            headers = await self._get_headers()
            response = await self._client.post(
                url, headers=headers, json={"comment": cuerpo_html}, timeout=30)
            if response.status_code == 401 and attempt == 0:
                self._invalidate_token()
                continue
            response.raise_for_status()
            break

    async def _consultar(self, url: str, params: Dict[str, Any],
                         extra: Optional[Dict[str, str]] = None,
                         timeout: int = 30):
        """
        GET que renueva el token ante un 401 y espera lo que pide un 429.

        Las consultas de deduplicacion son de lectura y repetirlas no tiene
        efecto, asi que el 429 se puede esperar. Rendirse ante el primero apaga
        la capa que lo pidio, y esas capas son las que reconocen un correo ya
        radicado: apagadas, el correo se radica otra vez. `$search` es el
        endpoint que Graph limita primero, y el limite se alcanza justo cuando
        el lote es grande, que es cuando hace mas falta la capa.

        Devuelve la respuesta tal cual. Quien llama decide que hacer con un
        estado de error, porque no todas las capas lo tratan igual.
        """
        reintentos_token = 1
        reintentos_tasa = INTENTOS_POR_LIMITE_DE_TASA
        while True:
            headers = await self._get_headers()
            if extra:
                headers.update(extra)
            response = await self._client.get(
                url, headers=headers, params=params, timeout=timeout)
            if response.status_code == 401 and reintentos_token > 0:
                reintentos_token -= 1
                self._invalidate_token()
                continue
            if response.status_code == 429 and reintentos_tasa > 0:
                reintentos_tasa -= 1
                await asyncio.sleep(espera_que_pide_graph(response))
                continue
            return response

    async def _leer_categorias(self, message_id: str):
        """Categorias actuales y el ETag con el que escribirlas."""
        url = f"{self.base_url}/users/{self.user_email}/messages/{message_id}"
        reintentos_token = 1
        reintentos_tasa = INTENTOS_POR_LIMITE_DE_TASA
        while True:
            headers = await self._get_headers()
            response = await self._client.get(
                url, headers=headers, params={"$select": "id,categories"},
                timeout=15)
            if response.status_code == 401 and reintentos_token > 0:
                reintentos_token -= 1
                self._invalidate_token()
                continue
            if response.status_code == 429 and reintentos_tasa > 0:
                reintentos_tasa -= 1
                await asyncio.sleep(espera_que_pide_graph(response))
                continue
            response.raise_for_status()
            datos = response.json()
            return datos.get("categories", []), datos.get("@odata.etag", "")

    async def marcar(
        self,
        message_id: str,
        marca: str = "",
        quitar_de_proceso: bool = False,
    ) -> List[str]:
        """
        Escribe una marca conservando las demas categorias del correo.

        Lee, fusiona y escribe con `If-Match` sobre el ETag leido. Si otra
        corrida escribio en medio, Graph responde 412 y se lanza
        `OtraCorridaLoTomo` en vez de sobrescribir: asi la reclamacion decide
        quien se queda con el correo antes de que ninguna de las dos lo trabaje.

        Devuelve las categorias que quedaron, para que el llamador mantenga al
        dia su copia en memoria.
        """
        from pipeline.marcas import fusionar, reclama_el_correo

        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().registrar(
                "MARCAR", destino=marca or "(limpieza)",
                detalle={"message_id": message_id})
            return fusionar([], marca, quitar_de_proceso)

        actuales, etag = await self._leer_categorias(message_id)
        nuevas = fusionar(actuales, marca, quitar_de_proceso)
        # La marca de reclamacion se escribe SIEMPRE, aunque el valor coincida
        # con el que ya esta puesto. Lo que reclama el correo no es su texto
        # sino el PATCH con `If-Match`: dos corridas que arrancan a la vez
        # calculan las dos la misma marca, y sin escribirla ninguna recibe el
        # 412 que le diria que la otra se adelanto. Las dos se creerian dueñas
        # y las dos radicarian.
        if nuevas == list(actuales) and not reclama_el_correo(marca):
            return nuevas

        url = f"{self.base_url}/users/{self.user_email}/messages/{message_id}"
        reintentos_token = 1
        reintentos_tasa = INTENTOS_POR_LIMITE_DE_TASA
        reintentos_red = INTENTOS_POR_FALLO_DE_RED
        while True:
            headers = await self._get_headers()
            if etag:
                headers["If-Match"] = etag
            try:
                response = await self._client.patch(
                    url, headers=headers, json={"categories": nuevas},
                    timeout=15)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                # Un corte de red no dice nada del correo. Rendirse aqui deja
                # la marca sin escribir, y con ella la cuenta de aplazamientos.
                if reintentos_red <= 0:
                    raise
                reintentos_red -= 1
                await asyncio.sleep(ESPERA_ENTRE_INTENTOS_S)
                continue
            if response.status_code == 401 and reintentos_token > 0:
                reintentos_token -= 1
                self._invalidate_token()
                continue
            if response.status_code == 429 and reintentos_tasa > 0:
                reintentos_tasa -= 1
                await asyncio.sleep(espera_que_pide_graph(response))
                continue
            if response.status_code == 412:
                raise OtraCorridaLoTomo(
                    f"otra corrida modifico el correo mientras se marcaba "
                    f"'{marca}': no se sobrescribe")
            # El backend caido tampoco dice nada del correo, y el mismo PATCH
            # con el mismo `If-Match` vuelve a ser seguro: o lo aplica, o
            # responde 412 porque otra corrida se adelanto.
            if response.status_code >= 500 and reintentos_red > 0:
                reintentos_red -= 1
                await asyncio.sleep(ESPERA_ENTRE_INTENTOS_S)
                continue
            response.raise_for_status()
            break
        return nuevas

    async def etiquetar_correo(self, message_id: str, etiqueta: str) -> None:
        """Reemplaza las categorias del correo por la etiqueta indicada"""
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().registrar("ETIQUETAR", destino=etiqueta, detalle={"message_id": message_id})
            return

        url = f"{self.base_url}/users/{self.user_email}/messages/{message_id}"

        for attempt in range(2):
            headers = await self._get_headers()
            response = await self._client.patch(url, headers=headers, json={"categories": [etiqueta]}, timeout=15)
            if response.status_code == 401 and attempt == 0:
                self._invalidate_token()
                continue
            response.raise_for_status()
            break

    async def move_email(self, message_id: str, destination_folder_id: str) -> str:
        """
        Mueve un correo a otra carpeta y devuelve su NUEVO identificador.

        Graph le asigna otro id al mensaje movido: el anterior deja de existir y
        cualquier operacion posterior sobre el responde 404. Quien mueva un
        correo y siga trabajando con el tiene que quedarse con este valor.
        """
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().registrar("MOVER", destino=destination_folder_id, detalle={"message_id": message_id})
            return ""

        url = f"{self.base_url}/users/{self.user_email}/messages/{message_id}/move"

        for attempt in range(2):
            headers = await self._get_headers()
            response = await self._client.post(url, headers=headers, json={"destinationId": destination_folder_id}, timeout=30)
            if response.status_code == 401 and attempt == 0:
                self._invalidate_token()
                continue
            response.raise_for_status()
            try:
                return response.json().get("id", "") or ""
            except ValueError:
                return ""
        return ""
    
    async def list_folders(self) -> List[Dict[str, Any]]:
        """Lista todas las carpetas de correo"""
        headers = await self._get_headers()
        url = f"{self.base_url}/users/{self.user_email}/mailFolders"
        params = {"$select": "id,displayName,totalItemCount,childFolderCount", "$top": 50}
        
        response = await self._client.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        folders = []
        for folder in data.get("value", []):
            folders.append({
                "id": folder.get("id"),
                "displayName": folder.get("displayName"),
                "totalItemCount": folder.get("totalItemCount", 0),
            })

            # Obtener subcarpetas si las hay
            if folder.get("childFolderCount", 0) > 0:
                try:
                    child_url = f"{url}/{folder['id']}/childFolders"
                    child_response = await self._client.get(child_url, headers=headers, params={"$select": "id,displayName,totalItemCount"}, timeout=30)
                    if child_response.status_code == 200:
                        for child in child_response.json().get("value", []):
                            folders.append({
                                "id": child.get("id"),
                                "displayName": child.get("displayName"),
                                "totalItemCount": child.get("totalItemCount", 0),
                                "parentFolder": folder.get("displayName"),
                            })
                except Exception:
                    pass

        return folders
