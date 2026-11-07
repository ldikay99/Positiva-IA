"""
Microsoft 365 por Microsoft Graph (REST, sin SDK): Outlook (correo y
calendario), OneDrive, SharePoint (bibliotecas y listas), Teams y Excel en
línea, con el token de una conexión microsoft_oauth (la persona que conectó:
/me) o microsoft_graph (una aplicación de Entra ID: /users/<buzon>).

Bloqueos duros: nunca un buzón o una dirección de Positiva (ni como cuenta, ni
como remitente, ni como destinatario o invitado), ni sus sitios de SharePoint.
Quien llama decide si una escritura sale (permitir_escritura). `GRAPH` se
cambia en las pruebas por un servidor local.
"""
from __future__ import annotations

import base64
import re
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import rest
from .base import ErrorDeNegocio, direccion_vetada, direcciones

GRAPH = "https://graph.microsoft.com/v1.0"
CARPETAS = {"inbox", "archive", "deleteditems", "junkemail", "drafts", "sentitems", "outbox", "conversationhistory", "msgfolderroot",
            "clutter", "recoverableitemsdeletions", "scheduled", "searchfolders", "serverfailures", "syncissues"}
SITIOS_VETADOS = ("positiva.sharepoint.com", "positiva-my.sharepoint.com")
TROZO = 5 * 1024 * 1024  # múltiplo de 320 KiB, como pide Graph
DIRECTO_MAX = 4 * 1024 * 1024
MAX_ADJUNTO = 15_000_000
ID_GRAPH = re.compile(r"^[A-Za-z0-9=_+/!.,:-]{1,600}$")


def revisar(d: str, que: str) -> None:
    motivo = direccion_vetada(d)
    if motivo:
        raise ErrorDeNegocio(f"bloqueado por seguridad: {que}: {motivo}")


def q(x: str) -> str:
    return urllib.parse.quote(str(x), safe="")


def ruta_segura(ruta: str) -> str:
    """Una ruta de OneDrive o SharePoint («/Carpeta/archivo.pdf») codificada; ErrorDeNegocio con «..»."""
    r = "/" + str(ruta or "").strip().replace("\\", "/").strip("/")
    if any(p in ("..", ".") for p in r.split("/")) or "\x00" in r or ":" in r:
        raise ErrorDeNegocio(f"la ruta «{ruta[:120]}» no es válida (sin «..» ni «:»)")
    return urllib.parse.quote(r, safe="/")


def es_id(x: str) -> bool:
    v = str(x or "").strip()
    return bool(v) and "/" not in v and "." not in v.rsplit("!", 1)[-1][-6:] and len(v) >= 10 and bool(ID_GRAPH.match(v))


class Graph:
    """Graph con el token (token_de() lo da) sobre /me o /users/<buzon>."""

    def __init__(self, token_de: Callable[[], str], base_usuario: str, timeout: float) -> None:
        self.token_de, self.base, self.timeout = token_de, base_usuario, timeout

    def r(self, metodo: str, ruta: str, **kw: Any) -> rest.Respuesta:
        url = ruta if ruta.startswith("http") else f"{GRAPH}{ruta}"
        return rest.pedir(metodo, url, token=self.token_de(), timeout=self.timeout, servicio="Microsoft Graph", **kw)

    def j(self, metodo: str, ruta: str, **kw: Any) -> Any:
        r = self.r(metodo, ruta, **kw)
        return r.json() if r.content else {}

    def usuario(self, ruta: str) -> str:
        return f"{self.base}{ruta}"

    # ----------------------------------------------------------------- correo
    def carpeta(self, nombre: str) -> str:
        n = str(nombre or "inbox").strip()
        if n.lower() in CARPETAS:
            return n.lower()
        if es_id(n) and len(n) > 40:
            return n
        filtro = urllib.parse.quote(f"displayName eq '{n.replace(chr(39), chr(39) * 2)}'")
        for ruta in (self.usuario(f"/mailFolders?$filter={filtro}"), self.usuario(f"/mailFolders/inbox/childFolders?$filter={filtro}")):
            v = self.j("GET", ruta).get("value") or []
            if v:
                return str(v[0]["id"])
        raise ErrorDeNegocio(f"el buzón no tiene la carpeta «{n}»")

    def mensajes(self, carpeta: str, consulta: str, filtros: List[str], maximo: int) -> List[dict]:
        sel = "id,internetMessageId,conversationId,subject,from,toRecipients,ccRecipients,replyTo,receivedDateTime,body,hasAttachments,isRead,categories"
        params: Dict[str, Any] = {"$top": maximo, "$select": sel}
        if consulta:
            params["$search"] = f'"{consulta.replace(chr(34), " ")}"'
        else:
            params["$orderby"] = "receivedDateTime desc"
            if filtros:
                params["$filter"] = " and ".join(filtros)
        return self.j("GET", self.usuario(f"/mailFolders/{q(self.carpeta(carpeta))}/messages"), params=params).get("value") or []

    def mensaje(self, gid: str) -> dict:
        return self.j("GET", self.usuario(f"/messages/{q(gid)}"))

    def adjuntos(self, gid: str) -> List[dict]:
        return self.j("GET", self.usuario(f"/messages/{q(gid)}/attachments")).get("value") or []

    def delta(self, carpeta: str, enlace: str) -> Tuple[List[dict], str]:
        """(mensajes nuevos o cambiados, deltaLink) desde el deltaLink anterior (o desde ahora si no hay)."""
        url = enlace or f"{GRAPH}{self.usuario(f'/mailFolders/{q(self.carpeta(carpeta))}/messages/delta')}?$select=id,receivedDateTime,isRead,subject"
        if enlace:
            u = urllib.parse.urlsplit(enlace)
            g = urllib.parse.urlsplit(GRAPH)
            if (u.scheme, u.netloc) != (g.scheme, g.netloc):
                raise ErrorDeNegocio("el deltaLink guardado no es de Microsoft Graph")
        vistos: List[dict] = []
        for _ in range(30):
            d = self.j("GET", url)
            vistos += [m for m in d.get("value") or [] if "@removed" not in m]
            if d.get("@odata.nextLink"):
                url = d["@odata.nextLink"]
                continue
            return vistos, str(d.get("@odata.deltaLink") or "")
        return vistos, ""

    def enviar(self, mensaje: dict, guardar: bool) -> dict:
        self.j("POST", self.usuario("/sendMail"), json={"message": cuerpo_correo(mensaje), "saveToSentItems": guardar})
        return {"enviado": True}

    def responder(self, gid: str, mensaje: dict, todos: bool, incluir: bool) -> dict:
        contenido = mensaje.get("html") or mensaje.get("cuerpo") or ""
        accion = "createReplyAll" if todos else "createReply"
        borrador = self.j("POST", self.usuario(f"/messages/{q(gid)}/{accion}"), json={"comment": contenido} if incluir else {})
        did = str(borrador.get("id") or "")
        if not did:
            raise ErrorDeNegocio("Microsoft no devolvió el borrador de la respuesta")
        cambios: Dict[str, Any] = {}
        if mensaje.get("para"):
            cambios["toRecipients"] = destinos(mensaje.get("para"))
        if mensaje.get("cc"):
            cambios["ccRecipients"] = destinos(mensaje.get("cc"))
        if mensaje.get("asunto"):
            cambios["subject"] = mensaje["asunto"]
        if not incluir:
            cambios["body"] = {"contentType": "HTML" if mensaje.get("html") else "Text", "content": contenido}
        # los destinatarios finales del borrador (también los que puso createReplyAll) no pueden ser de Positiva
        final = {**borrador, **cambios}
        for campo in ("toRecipients", "ccRecipients", "bccRecipients"):
            for x in final.get(campo) or []:
                revisar(str((x.get("emailAddress") or {}).get("address") or ""), "destinatario")
        url = self.usuario(f"/messages/{q(did)}")
        if cambios:
            self.j("PATCH", url, json=cambios)
        for a in adjuntos_graph(mensaje.get("adjuntos") or []):
            self.j("POST", f"{url}/attachments", json=a)
        self.j("POST", f"{url}/send", json={})
        return {"enviado": True, "id": did, "en_respuesta_a": gid}

    def mover(self, gid: str, carpeta: str) -> dict:
        r = self.j("POST", self.usuario(f"/messages/{q(gid)}/move"), json={"destinationId": self.carpeta(carpeta)})
        return {"movido": True, "nuevo_id": r.get("id") or ""}

    def categorizar(self, gid: str, cambios: dict) -> dict:
        r = self.j("PATCH", self.usuario(f"/messages/{q(gid)}"), json=cambios)
        return {"categorias": r.get("categories") or cambios.get("categories") or []}

    # -------------------------------------------------------------- calendario
    def eventos(self, calendario: str, desde: str, hasta: str, maximo: int, zona: str) -> List[dict]:
        base = self.usuario(f"/calendars/{q(calendario)}" if calendario else "/calendar")
        params = {"startDateTime": desde, "endDateTime": hasta, "$top": maximo, "$orderby": "start/dateTime"}
        v = self.j("GET", f"{base}/calendarView", params=params, headers={"Prefer": f'outlook.timezone="{zona}"'}).get("value") or []
        return [evento(e) for e in v]

    def crear_evento(self, calendario: str, cuerpo: dict) -> dict:
        base = self.usuario(f"/calendars/{q(calendario)}" if calendario else "/calendar")
        return evento(self.j("POST", f"{base}/events", json=cuerpo))

    # ------------------------------------------------------ OneDrive / SharePoint
    def unidad_propia(self) -> str:
        return self.usuario("/drive")

    def sitio(self, sitio: str) -> str:
        s = str(sitio or "").strip()
        if not s:
            raise ErrorDeNegocio("falta el sitio de SharePoint")
        if s.startswith("http"):
            u = urllib.parse.urlsplit(s)
            host, ruta = (u.hostname or "").lower(), u.path.rstrip("/")
        elif ":" in s and "." in s.split(":", 1)[0]:
            host, ruta = s.split(":", 1)[0].lower(), s.split(":", 1)[1].rstrip("/")
        else:
            if not ID_GRAPH.match(s):
                raise ErrorDeNegocio(f"sitio «{s[:80]}» no es una URL, «host:/sites/…» ni un id de sitio")
            return f"/sites/{q(s)}"
        if host in SITIOS_VETADOS or host.endswith(".positiva.gov.co") or "sgdea" in host:
            raise ErrorDeNegocio(f"bloqueado por seguridad: {host} es de Positiva: esta plataforma no toca sus sitios")
        if not host.endswith(".sharepoint.com") and host not in ("127.0.0.1",):
            raise ErrorDeNegocio(f"{host} no es un sitio de SharePoint (*.sharepoint.com)")
        sid = self.j("GET", f"/sites/{host}:{urllib.parse.quote(ruta or '/', safe='/')}").get("id")
        if not sid:
            raise ErrorDeNegocio(f"no se encontró el sitio {s}")
        return f"/sites/{q(sid)}"

    def unidad_de_sitio(self, sitio: str, biblioteca: str) -> str:
        base = self.sitio(sitio)
        if not biblioteca:
            return f"{base}/drive"
        for d in self.j("GET", f"{base}/drives").get("value") or []:
            if str(d.get("name") or "").lower() == biblioteca.lower() or d.get("id") == biblioteca:
                return f"/drives/{q(d['id'])}"
        raise ErrorDeNegocio(f"el sitio no tiene la biblioteca «{biblioteca}»")

    @staticmethod
    def item(unidad: str, ref: str) -> str:
        """El elemento por ruta («/a/b.pdf») o por id."""
        r = str(ref or "").strip()
        if not r or r == "/":
            return f"{unidad}/root"
        if r.startswith("/") or not es_id(r):
            return f"{unidad}/root:{ruta_segura(r)}:"
        return f"{unidad}/items/{q(r)}"

    def listar(self, unidad: str, carpeta: str, buscar: str, maximo: int) -> List[dict]:
        if buscar:
            v = self.j("GET", f"{unidad}/root/search(q='{q(buscar.replace(chr(39), chr(39) * 2))}')", params={"$top": maximo}).get("value") or []
        else:
            v = self.j("GET", f"{self.item(unidad, carpeta)}/children", params={"$top": maximo}).get("value") or []
        return [elemento(x) for x in v][:maximo]

    def metadatos(self, unidad: str, ref: str) -> dict:
        return self.j("GET", self.item(unidad, ref))

    def bajar(self, unidad: str, ref: str) -> bytes:
        return self.r("GET", f"{self.item(unidad, ref)}/content", seguir=True).content

    def subir(self, unidad: str, carpeta: str, nombre: str, datos: bytes, tipo: str, conflicto: str) -> dict:
        comportamiento = {"renombrar": "rename", "reemplazar": "replace", "fallar": "fail"}.get(conflicto, "rename")
        ruta = f"{carpeta.rstrip('/')}/{nombre}" if carpeta and carpeta != "/" else f"/{nombre}"
        destino = f"{unidad}/root:{ruta_segura(ruta)}:"
        if len(datos) <= DIRECTO_MAX:
            r = self.j("PUT", f"{destino}/content", params={"@microsoft.graph.conflictBehavior": comportamiento}, data=datos,
                       headers={"Content-Type": tipo or "application/octet-stream"})
            return elemento(r)
        sesion = self.j("POST", f"{destino}/createUploadSession", json={"item": {"@microsoft.graph.conflictBehavior": comportamiento}})
        url = str(sesion.get("uploadUrl") or "")
        if not url:
            raise ErrorDeNegocio("Graph no abrió la sesión de carga")
        total, r = len(datos), {}
        for i in range(0, total, TROZO):
            parte = datos[i:i + TROZO]
            # la URL de carga ya va firmada: no lleva Authorization
            resp = rest.pedir("PUT", url, data=parte, timeout=max(self.timeout, 60), servicio="Microsoft Graph",
                              headers={"Content-Range": f"bytes {i}-{i + len(parte) - 1}/{total}", "Content-Length": str(len(parte))})
            r = resp.json() if resp.content else {}
        return elemento(r)

    # ------------------------------------------------------------------ listas
    def lista(self, sitio_base: str, lista: str) -> str:
        n = str(lista or "").strip()
        if re.fullmatch(r"[0-9a-fA-F-]{36}", n):
            return f"{sitio_base}/lists/{q(n)}"
        filtro = urllib.parse.quote(f"displayName eq '{n.replace(chr(39), chr(39) * 2)}'")
        v = self.j("GET", f"{sitio_base}/lists?$filter={filtro}").get("value") or []
        if not v:
            raise ErrorDeNegocio(f"el sitio no tiene la lista «{n}»")
        return f"{sitio_base}/lists/{q(v[0]['id'])}"

    def items(self, base_lista: str, filtro: str, maximo: int) -> List[dict]:
        r: List[dict] = []
        params: Dict[str, Any] = {"expand": "fields", "$top": min(maximo, 999), **({"$filter": filtro} if filtro else {})}
        url = f"{base_lista}/items"
        for _ in range(50):
            d = self.j("GET", url, params=params, headers={"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"})
            r += [{"id": x.get("id"), **{k: v for k, v in (x.get("fields") or {}).items() if not k.startswith("@")}} for x in d.get("value") or []]
            if len(r) >= maximo or not d.get("@odata.nextLink"):
                break
            url, params = d["@odata.nextLink"], {}
        return r[:maximo]

    def agregar_item(self, base_lista: str, campos: dict) -> dict:
        x = self.j("POST", f"{base_lista}/items", json={"fields": campos})
        return {"id": x.get("id"), "enlace": x.get("webUrl") or ""}

    # ------------------------------------------------------------------- Teams
    def publicar_canal(self, equipo: str, canal: str, cuerpo: dict) -> dict:
        x = self.j("POST", f"/teams/{q(equipo)}/channels/{q(canal)}/messages", json=cuerpo)
        return {"id": x.get("id"), "enlace": x.get("webUrl") or ""}

    def publicar_chat(self, chat: str, cuerpo: dict) -> dict:
        x = self.j("POST", f"/chats/{q(chat)}/messages", json=cuerpo)
        return {"id": x.get("id"), "enlace": x.get("webUrl") or ""}

    # ------------------------------------------------------------------- Excel
    def libro(self, unidad: str, archivo: str) -> str:
        return f"{self.item(unidad, archivo)}/workbook"

    def valores_tabla(self, libro: str, tabla: str) -> Tuple[List[str], List[List[Any]]]:
        t = f"{libro}/tables/{q(tabla)}"
        cab = (self.j("GET", f"{t}/headerRowRange").get("values") or [[]])[0]
        cuerpo = self.j("GET", f"{t}/dataBodyRange").get("values") or []
        return [str(x) for x in cab], cuerpo

    def anexar_tabla(self, libro: str, tabla: str, filas: List[List[Any]]) -> dict:
        x = self.j("POST", f"{libro}/tables/{q(tabla)}/rows", json={"values": filas})
        return {"agregadas": len(filas), "indice": x.get("index")}

    def rango(self, libro: str, hoja: str, direccion: str) -> List[List[Any]]:
        h = f"{libro}/worksheets/{q(hoja)}"
        url = f"{h}/range(address='{q(direccion)}')" if direccion else f"{h}/usedRange"
        return self.j("GET", url).get("values") or []

    def escribir_rango(self, libro: str, hoja: str, direccion: str, valores: List[List[Any]]) -> dict:
        x = self.j("PATCH", f"{libro}/worksheets/{q(hoja)}/range(address='{q(direccion)}')", json={"values": valores})
        return {"rango": x.get("address") or direccion, "filas": len(valores)}


def destinos(xs: Any) -> List[dict]:
    r = []
    for d in direcciones(xs):
        revisar(d, "destinatario")
        r.append({"emailAddress": {"address": d}})
    return r


def adjuntos_graph(adjuntos: List[dict]) -> List[dict]:
    return [{"@odata.type": "#microsoft.graph.fileAttachment", "name": str(a.get("nombre") or "adjunto"),
             "contentType": str(a.get("tipo") or "application/octet-stream"), "contentBytes": base64.b64encode(a.get("datos") or b"").decode()}
            for a in adjuntos]


def cuerpo_correo(m: dict) -> dict:
    return {"subject": m.get("asunto") or "", "body": {"contentType": "HTML" if m.get("html") else "Text", "content": m.get("html") or m.get("cuerpo") or ""},
            "toRecipients": destinos(m.get("para")), "ccRecipients": destinos(m.get("cc")), "bccRecipients": destinos(m.get("cco")),
            **({"attachments": adjuntos_graph(m["adjuntos"])} if m.get("adjuntos") else {})}


def correo(m: dict) -> dict:
    """Un mensaje de Graph con la forma de los flujos."""
    from .correo import texto_plano

    de = (m.get("from") or {}).get("emailAddress") or {}
    cuerpo = m.get("body") or {}
    return {"id": m.get("internetMessageId") or m.get("id"), "graph_id": m.get("id"), "hilo": m.get("conversationId") or "",
            "asunto": m.get("subject") or "", "remitente": f"{de.get('name', '')} <{de.get('address', '')}>", "remitente_correo": de.get("address", ""),
            "remitente_nombre": de.get("name", ""),
            "para": ", ".join((t.get("emailAddress") or {}).get("address", "") for t in m.get("toRecipients") or []),
            "cc": ", ".join((t.get("emailAddress") or {}).get("address", "") for t in m.get("ccRecipients") or []),
            "responder_a": ", ".join((t.get("emailAddress") or {}).get("address", "") for t in m.get("replyTo") or []),
            "fecha": m.get("receivedDateTime") or "", "leido": bool(m.get("isRead")), "categorias": list(m.get("categories") or []),
            "cuerpo": (texto_plano(cuerpo.get("content", "")) if str(cuerpo.get("contentType") or "").lower() == "html" else cuerpo.get("content", ""))[:60000],
            "adjuntos": [], "_tiene_adjuntos": bool(m.get("hasAttachments"))}


def evento(e: dict) -> dict:
    ini, fin = e.get("start") or {}, e.get("end") or {}
    return {"id": e.get("id"), "titulo": e.get("subject") or "", "inicio": ini.get("dateTime") or "", "fin": fin.get("dateTime") or "",
            "zona": ini.get("timeZone") or "", "todo_el_dia": bool(e.get("isAllDay")), "lugar": (e.get("location") or {}).get("displayName") or "",
            "descripcion": e.get("bodyPreview") or "", "organizador": ((e.get("organizer") or {}).get("emailAddress") or {}).get("address") or "",
            "enlace": e.get("webLink") or "", "teams": ((e.get("onlineMeeting") or {}).get("joinUrl")) or "",
            "asistentes": [{"correo": (a.get("emailAddress") or {}).get("address"), "nombre": (a.get("emailAddress") or {}).get("name") or "",
                            "respuesta": (a.get("status") or {}).get("response") or ""} for a in e.get("attendees") or []]}


def cuerpo_evento(titulo: str, inicio: str, fin: str, zona: str, todo_el_dia: bool, descripcion: str, lugar: str, asistentes: List[str],
                  con_teams: bool, recordatorio: Optional[int]) -> dict:
    for a in asistentes:
        revisar(a, "invitado")
    c: Dict[str, Any] = {"subject": titulo, "start": {"dateTime": inicio[:19], "timeZone": zona}, "end": {"dateTime": fin[:19], "timeZone": zona},
                         "isAllDay": bool(todo_el_dia)}
    if todo_el_dia:
        c["start"]["dateTime"], c["end"]["dateTime"] = inicio[:10] + "T00:00:00", fin[:10] + "T00:00:00"
    if descripcion:
        c["body"] = {"contentType": "text", "content": descripcion}
    if lugar:
        c["location"] = {"displayName": lugar}
    if asistentes:
        c["attendees"] = [{"emailAddress": {"address": a}, "type": "required"} for a in asistentes]
    if con_teams:
        c["isOnlineMeeting"], c["onlineMeetingProvider"] = True, "teamsForBusiness"
    if recordatorio is not None:
        c["isReminderOn"], c["reminderMinutesBeforeStart"] = True, int(recordatorio)
    return c


def elemento(x: dict) -> dict:
    ruta = ((x.get("parentReference") or {}).get("path") or "")
    ruta = ruta.split("root:", 1)[1] if "root:" in ruta else ruta
    return {"id": x.get("id"), "nombre": x.get("name"), "ruta": f"{ruta}/{x.get('name') or ''}" if x.get("name") else ruta,
            "tipo": (x.get("file") or {}).get("mimeType") or ("carpeta" if x.get("folder") else ""), "tamano": x.get("size"),
            "modificado": x.get("lastModifiedDateTime"), "enlace": x.get("webUrl") or "", "carpeta": bool(x.get("folder"))}


def columna(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def celda(direccion: str) -> Tuple[int, int]:
    m = re.match(r"^\$?([A-Za-z]{1,3})\$?(\d{1,7})", str(direccion or "A1").strip())
    if not m:
        raise ErrorDeNegocio(f"rango «{direccion}» no es una dirección de Excel (p. ej. A1:C20)")
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return col - 1, int(m.group(2))


def rango_para(direccion: str, filas: int, columnas: int) -> str:
    """El rango que ocupa una matriz de filas × columnas empezando en la primera celda de direccion."""
    c, f = celda(direccion.split(":")[0])
    return f"{columna(c)}{f}:{columna(c + max(1, columnas) - 1)}{f + max(1, filas) - 1}"
