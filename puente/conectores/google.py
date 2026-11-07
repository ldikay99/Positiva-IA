"""
Google Workspace por REST (sin el SDK de Google): Gmail, Calendar, Docs, Forms
y Drive, con el token de una conexión google_oauth (la persona que conectó) o
google_servicio (lo que se le compartió a la cuenta de servicio).

Cada método hace una sola cosa y devuelve datos ya traducidos a la forma de los
flujos. Quien llama decide si una escritura sale (permitir_escritura). Las
direcciones de Positiva quedan bloqueadas siempre (`base.direccion_vetada`).
`BASES` se cambia en las pruebas por un servidor local.
"""
from __future__ import annotations

import base64
import re
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import rest
from .base import ErrorDeNegocio, direccion_vetada, direcciones

BASES = {
    "gmail": "https://gmail.googleapis.com/gmail/v1/users/me",
    "calendar": "https://www.googleapis.com/calendar/v3",
    "docs": "https://docs.googleapis.com/v1/documents",
    "forms": "https://forms.googleapis.com/v1/forms",
    "drive": "https://www.googleapis.com/drive/v3",
    "drive_subida": "https://www.googleapis.com/upload/drive/v3/files",
}
A = "https://www.googleapis.com/auth/"
ALCANCES = {"gmail": [A + "gmail.modify"], "calendar": [A + "calendar"], "docs": [A + "documents", A + "drive"],
            "forms": [A + "forms.responses.readonly", A + "forms.body.readonly"], "drive": [A + "drive"]}
ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
SISTEMA = {"INBOX", "UNREAD", "STARRED", "IMPORTANT", "SPAM", "TRASH", "SENT", "DRAFT", "CATEGORY_PERSONAL", "CATEGORY_SOCIAL",
           "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_FORUMS"}
MAX_ADJUNTO = 15_000_000


def id_de(x: Any, que: str) -> str:
    """Un id de Google (o el id que trae un enlace /d/<id>/ o ?id=)."""
    v = str(x or "").strip()
    m = re.search(r"/d/(?:e/)?([A-Za-z0-9_-]{10,})", v) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", v)
    if m:
        v = m.group(1)
    if not ID.match(v):
        raise ErrorDeNegocio(f"{que} «{v[:80]}» no es un id de Google (ni un enlace que lo tenga)")
    return v


def revisar_direcciones(xs: Iterable[str], que: str) -> None:
    for d in xs:
        motivo = direccion_vetada(d)
        if motivo:
            raise ErrorDeNegocio(f"bloqueado por seguridad: {que}: {motivo}")


def _b64(datos: str) -> bytes:
    return base64.urlsafe_b64decode(datos + "=" * (-len(datos) % 4))


class Google:
    """Las APIs de Google con un token (token_de(alcances) lo da)."""

    def __init__(self, token_de: Callable[[List[str]], str], timeout: float) -> None:
        self.token_de, self.timeout = token_de, timeout

    def _r(self, metodo: str, api: str, ruta: str, servicio: str, **kw: Any) -> rest.Respuesta:
        url = ruta if ruta.startswith("http") else f"{BASES[api]}{ruta}"
        return rest.pedir(metodo, url, token=self.token_de(ALCANCES.get(api, ALCANCES["drive"])), timeout=self.timeout, servicio=servicio, **kw)

    # ------------------------------------------------------------------ Gmail
    def gmail(self, metodo: str, ruta: str, **kw: Any) -> Any:
        r = self._r(metodo, "gmail", ruta, "Gmail", **kw)
        return r.json() if r.content else {}

    def etiquetas(self) -> List[dict]:
        return self.gmail("GET", "/labels").get("labels") or []

    def id_etiqueta(self, nombre: str, crear: bool = False, simular: bool = False) -> Optional[str]:
        n = str(nombre or "").strip()
        if not n:
            return None
        if n.upper() in SISTEMA:
            return n.upper()
        for e in self.etiquetas():
            if e.get("id") == n or str(e.get("name") or "").lower() == n.lower():
                return str(e["id"])
        if not crear:
            raise ErrorDeNegocio(f"Gmail no tiene la etiqueta «{n}»")
        if simular:
            return f"(nueva) {n}"
        return str(self.gmail("POST", "/labels", json={"name": n, "labelListVisibility": "labelShow", "messageListVisibility": "show"})["id"])

    def buscar(self, consulta: str, etiqueta: str, maximo: int) -> List[str]:
        params: Dict[str, Any] = {"maxResults": maximo}
        if consulta:
            params["q"] = consulta
        if etiqueta:
            params["labelIds"] = self.id_etiqueta(etiqueta)
        return [str(m["id"]) for m in self.gmail("GET", "/messages", params=params).get("messages") or []][:maximo]

    def mensaje(self, mid: str, adjuntos: bool = True) -> dict:
        """Un correo de Gmail con la forma de los flujos; los adjuntos traen _datos (bytes) si adjuntos."""
        m = self.gmail("GET", f"/messages/{id_de(mid, 'mensaje')}", params={"format": "full"})
        cab = {str(h.get("name") or "").lower(): str(h.get("value") or "") for h in (m.get("payload") or {}).get("headers") or []}
        texto, html, lista = "", "", []

        def recorrer(p: dict) -> None:
            nonlocal texto, html
            tipo = str(p.get("mimeType") or "")
            cuerpo = p.get("body") or {}
            if p.get("filename"):
                a = {"nombre": p["filename"], "tipo": tipo, "tamano": int(cuerpo.get("size") or 0), "_adjunto": cuerpo.get("attachmentId"),
                     "_inline": cuerpo.get("data")}
                lista.append(a)
            elif tipo == "text/plain" and cuerpo.get("data") and not texto:
                texto = _b64(cuerpo["data"]).decode("utf-8", errors="replace")
            elif tipo == "text/html" and cuerpo.get("data") and not html:
                html = _b64(cuerpo["data"]).decode("utf-8", errors="replace")
            for x in p.get("parts") or []:
                recorrer(x)

        recorrer(m.get("payload") or {})
        from .correo import texto_plano
        import email.utils

        nombre, direccion = email.utils.parseaddr(cab.get("from", ""))
        r = {"id": str(m.get("id")), "hilo": str(m.get("threadId") or ""), "message_id": cab.get("message-id", ""),
             "referencias": " ".join(cab.get("references", "").split())[:4000], "responder_a": cab.get("reply-to", ""),
             "asunto": cab.get("subject", ""), "remitente": cab.get("from", ""), "remitente_correo": direccion, "remitente_nombre": nombre,
             "para": cab.get("to", ""), "cc": cab.get("cc", ""), "fecha": cab.get("date", ""), "etiquetas": list(m.get("labelIds") or []),
             "fragmento": m.get("snippet") or "", "cuerpo": (texto or texto_plano(html))[:60000], "adjuntos": []}
        try:
            r["fecha"] = email.utils.parsedate_to_datetime(r["fecha"]).isoformat()
        except Exception:
            pass
        for a in lista:
            x = {"nombre": a["nombre"], "tipo": a["tipo"], "tamano": a["tamano"]}
            if adjuntos and a["tamano"] <= MAX_ADJUNTO:
                if a.get("_inline"):
                    x["_datos"] = _b64(a["_inline"])
                elif a.get("_adjunto"):
                    d = self.gmail("GET", f"/messages/{r['id']}/attachments/{a['_adjunto']}")
                    x["_datos"] = _b64(str(d.get("data") or ""))
            r["adjuntos"].append(x)
        return r

    def enviar(self, mime: bytes, hilo: str = "") -> dict:
        cuerpo: Dict[str, Any] = {"raw": base64.urlsafe_b64encode(mime).decode().rstrip("=")}
        if hilo:
            cuerpo["threadId"] = hilo
        r = self.gmail("POST", "/messages/send", json=cuerpo)
        return {"enviado": True, "id": r.get("id"), "hilo": r.get("threadId")}

    def modificar(self, mid: str, agregar: List[str], quitar: List[str]) -> dict:
        r = self.gmail("POST", f"/messages/{id_de(mid, 'mensaje')}/modify", json={"addLabelIds": agregar, "removeLabelIds": quitar})
        return {"etiquetas": r.get("labelIds") or []}

    def historial(self, desde: str, etiqueta: str) -> Tuple[List[str], str]:
        """(ids de mensajes agregados desde ese historyId, el historyId nuevo). ErrorDeNegocio «historial vencido» si Google ya no lo tiene."""
        ids: List[str] = []
        token, ultimo = None, desde
        for _ in range(20):
            params = {"startHistoryId": desde, "historyTypes": "messageAdded", **({"labelId": etiqueta} if etiqueta else {}),
                      **({"pageToken": token} if token else {})}
            r = self._r("GET", "gmail", "/history", "Gmail", params=params, errores=False)
            if r.status == 404:
                raise ErrorDeNegocio("historial vencido")
            if r.status in (408, 429) or r.status >= 500:
                from .base import ErrorTransitorio
                raise ErrorTransitorio(f"Gmail respondió {r.status} al leer el historial")
            if r.status >= 400:
                raise ErrorDeNegocio(f"Gmail respondió {r.status} al leer el historial")
            d = r.json()
            for h in d.get("history") or []:
                for x in h.get("messagesAdded") or []:
                    mid = str((x.get("message") or {}).get("id") or "")
                    if mid and mid not in ids:
                        ids.append(mid)
            ultimo = str(d.get("historyId") or ultimo)
            token = d.get("nextPageToken")
            if not token:
                break
        return ids, ultimo

    def perfil(self) -> dict:
        return self.gmail("GET", "/profile")

    # ---------------------------------------------------------------- Calendar
    def calendar(self, metodo: str, ruta: str, **kw: Any) -> Any:
        r = self._r(metodo, "calendar", ruta, "Google Calendar", **kw)
        return r.json() if r.content else {}

    def eventos(self, calendario: str, desde: str, hasta: str, consulta: str, maximo: int, zona: str) -> List[dict]:
        params = {"timeMin": desde, "timeMax": hasta, "singleEvents": "true", "orderBy": "startTime", "maxResults": maximo, "timeZone": zona,
                  **({"q": consulta} if consulta else {})}
        return [evento(e) for e in self.calendar("GET", f"/calendars/{_cal(calendario)}/events", params=params).get("items") or []]

    def crear_evento(self, calendario: str, cuerpo: dict, notificar: bool) -> dict:
        params = {"sendUpdates": "all" if notificar else "none", "conferenceDataVersion": 1}
        return evento(self.calendar("POST", f"/calendars/{_cal(calendario)}/events", params=params, json=cuerpo))

    def actualizar_evento(self, calendario: str, eid: str, cambios: dict, notificar: bool) -> dict:
        params = {"sendUpdates": "all" if notificar else "none", "conferenceDataVersion": 1}
        return evento(self.calendar("PATCH", f"/calendars/{_cal(calendario)}/events/{id_de(eid, 'evento')}", params=params, json=cambios))

    def obtener_evento(self, calendario: str, eid: str) -> dict:
        return self.calendar("GET", f"/calendars/{_cal(calendario)}/events/{id_de(eid, 'evento')}")

    def ocupado(self, calendarios: List[str], desde: str, hasta: str, zona: str) -> Dict[str, List[dict]]:
        r = self.calendar("POST", "/freeBusy", json={"timeMin": desde, "timeMax": hasta, "timeZone": zona, "items": [{"id": c} for c in calendarios]})
        salida: Dict[str, List[dict]] = {}
        for c, v in (r.get("calendars") or {}).items():
            if v.get("errors"):
                raise ErrorDeNegocio(f"no se pudo ver la disponibilidad de «{c}»: {(v['errors'][0] or {}).get('reason', 'sin acceso')}")
            salida[c] = [{"inicio": b.get("start"), "fin": b.get("end")} for b in v.get("busy") or []]
        return salida

    # ------------------------------------------------------------ Drive y Docs
    def drive(self, metodo: str, ruta: str, **kw: Any) -> rest.Respuesta:
        return self._r(metodo, "drive", ruta, "Google Drive", **kw)

    def copiar(self, archivo: str, nombre: str, carpeta: str) -> dict:
        cuerpo = {"name": nombre, **({"parents": [carpeta]} if carpeta else {})}
        return self.drive("POST", f"/files/{id_de(archivo, 'plantilla')}/copy", params={"supportsAllDrives": "true", "fields": "id,name,webViewLink"},
                          json=cuerpo).json()

    def exportar(self, archivo: str, tipo: str) -> bytes:
        return self.drive("GET", f"/files/{id_de(archivo, 'archivo')}/export", params={"mimeType": tipo}).content

    def documento(self, doc: str) -> dict:
        return self._r("GET", "docs", f"/{id_de(doc, 'documento')}", "Google Docs").json()

    def reemplazar(self, doc: str, valores: Dict[str, str]) -> int:
        pedidos = [{"replaceAllText": {"containsText": {"text": "{{" + k + "}}", "matchCase": True}, "replaceText": v}} for k, v in valores.items()]
        if not pedidos:
            return 0
        r = self._r("POST", "docs", f"/{id_de(doc, 'documento')}:batchUpdate", "Google Docs", json={"requests": pedidos}).json()
        return sum(int(((x or {}).get("replaceAllText") or {}).get("occurrencesChanged") or 0) for x in r.get("replies") or [])

    # ------------------------------------------------------------------- Forms
    def formulario(self, fid: str) -> dict:
        return self._r("GET", "forms", f"/{id_de(fid, 'formulario')}", "Google Forms").json()

    def respuestas(self, fid: str, desde: str, maximo: int) -> List[dict]:
        r: List[dict] = []
        token = None
        while len(r) < maximo:
            params = {"pageSize": min(5000, maximo - len(r)), **({"filter": f"timestamp > {desde}"} if desde else {}), **({"pageToken": token} if token else {})}
            d = self._r("GET", "forms", f"/{id_de(fid, 'formulario')}/responses", "Google Forms", params=params).json()
            r += d.get("responses") or []
            token = d.get("nextPageToken")
            if not token:
                break
        return r[:maximo]


def _cal(c: str) -> str:
    import urllib.parse

    v = str(c or "primary").strip() or "primary"
    revisar_direcciones([v] if "@" in v else [], "calendario")
    if any(ch in v for ch in "/?#\\") or len(v) > 300:
        raise ErrorDeNegocio(f"calendario «{v[:80]}» no es un id de calendario")
    return urllib.parse.quote(v, safe="@.")


def evento(e: dict) -> dict:
    ini, fin = e.get("start") or {}, e.get("end") or {}
    meet = next((x.get("uri") for x in ((e.get("conferenceData") or {}).get("entryPoints") or []) if x.get("entryPointType") == "video"), "") or e.get("hangoutLink") or ""
    return {"id": e.get("id"), "titulo": e.get("summary") or "", "inicio": ini.get("dateTime") or ini.get("date") or "", "fin": fin.get("dateTime") or fin.get("date") or "",
            "todo_el_dia": bool(ini.get("date") and not ini.get("dateTime")), "lugar": e.get("location") or "", "descripcion": e.get("description") or "",
            "estado": e.get("status") or "", "organizador": (e.get("organizer") or {}).get("email") or "", "enlace": e.get("htmlLink") or "", "meet": meet,
            "asistentes": [{"correo": a.get("email"), "nombre": a.get("displayName") or "", "respuesta": a.get("responseStatus") or ""} for a in e.get("attendees") or []]}


def cuerpo_evento(titulo: str, inicio: str, fin: str, zona: str, todo_el_dia: bool, descripcion: str, lugar: str, asistentes: List[str],
                  con_meet: bool, recordatorio: Optional[int]) -> dict:
    revisar_direcciones(asistentes, "invitado")
    if todo_el_dia:
        c: Dict[str, Any] = {"start": {"date": inicio[:10]}, "end": {"date": fin[:10]}}
    else:
        c = {"start": {"dateTime": inicio, "timeZone": zona}, "end": {"dateTime": fin, "timeZone": zona}}
    if titulo:
        c["summary"] = titulo
    if descripcion:
        c["description"] = descripcion
    if lugar:
        c["location"] = lugar
    if asistentes:
        c["attendees"] = [{"email": a} for a in asistentes]
    if con_meet:
        c["conferenceData"] = {"createRequest": {"requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
    if recordatorio is not None:
        c["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": int(recordatorio)}]}
    return c


def respuesta_de_forms(r: dict, preguntas: Dict[str, str]) -> dict:
    respuestas: Dict[str, Any] = {}
    for qid, a in (r.get("answers") or {}).items():
        titulo = preguntas.get(qid, qid)
        vals = [x.get("value") for x in ((a.get("textAnswers") or {}).get("answers") or [])]
        archivos = [x.get("fileName") for x in ((a.get("fileUploadAnswers") or {}).get("answers") or [])]
        v = vals or archivos
        respuestas[titulo] = v[0] if len(v) == 1 else v
    return {"id": r.get("responseId"), "enviada": r.get("lastSubmittedTime") or r.get("createTime") or "", "correo": r.get("respondentEmail") or "",
            "respuestas": respuestas}


def preguntas_de(f: dict) -> Dict[str, str]:
    r: Dict[str, str] = {}
    for it in f.get("items") or []:
        q = (it.get("questionItem") or {}).get("question") or {}
        if q.get("questionId"):
            r[str(q["questionId"])] = str(it.get("title") or q["questionId"])
        for fila in ((it.get("questionGroupItem") or {}).get("questions") or []):
            if fila.get("questionId"):
                r[str(fila["questionId"])] = f"{it.get('title') or ''} · {((fila.get('rowQuestion') or {}).get('title') or '')}".strip(" ·")
    return r


def destinatarios(mensaje: dict) -> List[str]:
    return direcciones(mensaje.get("para")) + direcciones(mensaje.get("cc")) + direcciones(mensaje.get("cco"))
