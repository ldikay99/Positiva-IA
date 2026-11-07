"""
Correo: leer por IMAP o Microsoft Graph (siempre en solo lectura: EXAMINE y
BODY.PEEK, sin marcar como leído) y enviar por SMTP o Microsoft Graph.

Nunca toca los buzones de los jobs de PQRD o tutelas ni direcciones de
Positiva (bloqueo duro en `base.direccion_vetada`). Quien llama decide si un
envío sale de verdad (credencial con permitir_escritura) o queda bloqueado.

`fabrica_imap`, `fabrica_smtp` y `pedir_https` se pueden reemplazar en las
pruebas por versiones falsas.
"""
from __future__ import annotations

import base64
import email
import email.policy
import email.utils
import http.client
import imaplib
import json
import mimetypes
import re
import smtplib
import socket
import ssl
import urllib.parse
import unicodedata
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import ErrorDeNegocio, ErrorTransitorio, destino_seguro, direccion_vetada, direcciones

MAX_CORREOS = 100
MAX_ADJUNTO = 15_000_000
# Lo que lleva un correo que sale: cuántos adjuntos, cuánto en total y, por Graph (un solo pedido), cada uno.
MAX_ADJUNTOS_ENVIO = 10
MAX_BYTES_ENVIO = 20_000_000
MAX_BYTES_GRAPH = 3_000_000
MESES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def texto_plano(html: str) -> str:
    t = re.sub(r"<(style|script)[\s\S]*?</\1>", " ", html or "", flags=re.I)
    t = re.sub(r"<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", t)).strip()


def _ascii(x: Any) -> str:
    t = unicodedata.normalize("NFKD", str(x or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r'["\\\r\n]', " ", t.encode("ascii", "ignore").decode()).strip()


def revisar_buzon(direccion: str) -> None:
    motivo = direccion_vetada(direccion)
    if motivo:
        raise ErrorDeNegocio(f"bloqueado por seguridad: {motivo}")


# ------------------------------------------------------------------- IMAP

def _imap_real(host: str, puerto: int, tls: str, timeout: float):
    ip = destino_seguro(host, puerto, red_privada=True)
    if tls == "ssl":
        return imaplib.IMAP4_SSL(host, puerto, ssl_context=ssl.create_default_context(), timeout=timeout)
    con = imaplib.IMAP4(ip, puerto, timeout=timeout)
    if tls == "starttls":
        con.starttls(ssl_context=ssl.create_default_context())
    return con


fabrica_imap: Callable[..., Any] = _imap_real


def _modo_tls(x: Any, defecto: str) -> str:
    if x is True:
        return "ssl"
    if x is False:
        return "ninguno"
    v = str(x or defecto).strip().lower()
    return v if v in ("ssl", "starttls", "ninguno") else defecto


def _criterios(filtro: Dict[str, Any], desde: str) -> List[str]:
    c: List[str] = []
    if filtro.get("no_leidos"):
        c.append("UNSEEN")
    if desde:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", desde)
        if not m:
            raise ErrorDeNegocio(f"desde «{desde}» tiene que ser una fecha AAAA-MM-DD")
        c += ["SINCE", f"{int(m.group(3)):02d}-{MESES[int(m.group(2)) - 1]}-{m.group(1)}"]
    if filtro.get("remitente"):
        c += ["FROM", f'"{_ascii(filtro["remitente"])}"']
    if filtro.get("asunto"):
        c += ["SUBJECT", f'"{_ascii(filtro["asunto"])}"']
    if filtro.get("texto"):
        c += ["TEXT", f'"{_ascii(filtro["texto"])}"']
    return c or ["ALL"]


def _de_mime(crudo: bytes, uid: str, incluir_adjuntos: bool) -> dict:
    m = email.message_from_bytes(crudo, policy=email.policy.default)
    nombre, direccion = email.utils.parseaddr(str(m.get("From") or ""))
    cuerpo = ""
    parte = m.get_body(preferencelist=("plain", "html"))
    if parte is not None:
        try:
            contenido = parte.get_content()
        except Exception:
            contenido = ""
        cuerpo = texto_plano(contenido) if parte.get_content_subtype() == "html" else contenido
    adjuntos = []
    for a in m.iter_attachments():
        n = a.get_filename() or "adjunto"
        datos = a.get_payload(decode=True) or b""
        x = {"nombre": n, "tipo": a.get_content_type(), "tamano": len(datos)}
        if incluir_adjuntos and len(datos) <= MAX_ADJUNTO:
            x["_datos"] = datos
        adjuntos.append(x)
    fecha = ""
    try:
        fecha = email.utils.parsedate_to_datetime(str(m.get("Date"))).isoformat()
    except Exception:
        fecha = str(m.get("Date") or "")
    return {"id": str(m.get("Message-ID") or f"uid-{uid}").strip(), "uid": uid, "asunto": str(m.get("Subject") or ""),
            "remitente": f"{nombre} <{direccion}>" if nombre else direccion, "remitente_correo": direccion,
            "remitente_nombre": nombre, "para": str(m.get("To") or ""), "cc": str(m.get("Cc") or ""), "fecha": fecha,
            "cuerpo": (cuerpo or "")[:60000], "adjuntos": adjuntos,
            # para responder en el mismo hilo (In-Reply-To y References)
            "referencias": " ".join(str(m.get("References") or "").split())[:4000], "responder_a": str(m.get("Reply-To") or "")}


def leer_imap(valores: dict, carpeta: str, filtro: Dict[str, Any], desde: str, maximo: int, incluir_adjuntos: bool,
              timeout: float) -> List[dict]:
    host, puerto = str(valores.get("host") or ""), int(valores.get("puerto") or 993)
    revisar_buzon(str(valores.get("usuario") or ""))
    try:
        con = fabrica_imap(host, puerto, _modo_tls(valores.get("tls"), "ssl"), timeout)
    except (socket.timeout, TimeoutError) as e:
        raise TimeoutError(f"el servidor IMAP {host} no respondió a tiempo") from e
    except ErrorDeNegocio:
        raise
    except OSError as e:
        raise ConnectionError(f"no se pudo conectar a IMAP {host}: {type(e).__name__}") from e
    try:
        try:
            con.login(str(valores.get("usuario") or ""), str(valores.get("clave") or ""))
        except imaplib.IMAP4.error as e:
            raise ErrorDeNegocio("IMAP rechazó el usuario o la clave") from e
        estado, _ = con.select(f'"{_ascii(carpeta or "INBOX")}"', readonly=True)  # EXAMINE: solo lectura
        if estado != "OK":
            raise ErrorDeNegocio(f"no existe la carpeta «{carpeta}» en el buzón")
        estado, datos = con.uid("SEARCH", None, *_criterios(filtro, desde))
        if estado != "OK":
            raise ErrorDeNegocio("el servidor IMAP rechazó la búsqueda")
        uids = (datos[0] or b"").split() if datos else []
        uids = uids[-maximo:][::-1]  # los más recientes primero
        r = []
        for uid in uids:
            u = uid.decode() if isinstance(uid, bytes) else str(uid)
            estado, partes = con.uid("FETCH", u, "(BODY.PEEK[])")  # PEEK: no marca como leído
            crudo = next((p[1] for p in partes or [] if isinstance(p, tuple) and len(p) > 1), None)
            if estado == "OK" and crudo:
                r.append(_de_mime(crudo, u, incluir_adjuntos))
        return r
    finally:
        try:
            con.logout()
        except Exception:
            pass


# ------------------------------------------------------------ HTTPS (Graph)

def _pedir_https_real(metodo: str, url: str, cuerpo: Optional[bytes], encabezados: Dict[str, str], timeout: float) -> Tuple[int, bytes]:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or not u.hostname:
        raise ErrorDeNegocio("solo se habla con Microsoft por https")
    destino_seguro(u.hostname, u.port or 443)
    con = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=ssl.create_default_context())
    try:
        con.request(metodo, (u.path or "/") + (f"?{u.query}" if u.query else ""), body=cuerpo, headers=encabezados)
        r = con.getresponse()
        return r.status, r.read(30_000_000)
    except (socket.timeout, TimeoutError) as e:
        raise TimeoutError("Microsoft no respondió a tiempo") from e
    finally:
        con.close()


pedir_https: Callable[..., Tuple[int, bytes]] = _pedir_https_real


def _json_https(metodo: str, url: str, timeout: float, token: str = "", datos: Any = None, form: Optional[dict] = None) -> Any:
    enc = {"Accept": "application/json", "User-Agent": "Positiva-IA/flujos"}
    cuerpo = None
    if token:
        enc["Authorization"] = f"Bearer {token}"
    if form is not None:
        cuerpo = urllib.parse.urlencode(form).encode()
        enc["Content-Type"] = "application/x-www-form-urlencoded"
    elif datos is not None:
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        enc["Content-Type"] = "application/json"
    status, crudo = pedir_https(metodo, url, cuerpo, enc, timeout)
    if status in (408, 429) or status >= 500:
        raise ErrorTransitorio(f"Microsoft respondió {status}")
    texto = crudo.decode("utf-8", errors="replace")
    if status >= 400:
        try:
            err = json.loads(texto)
            detalle = (err.get("error") or {}).get("message") if isinstance(err.get("error"), dict) else err.get("error_description") or err.get("error")
        except ValueError:
            detalle = texto[:200]
        raise ErrorDeNegocio(f"Microsoft respondió {status}: {str(detalle or '')[:300]}")
    return json.loads(texto) if texto.strip() else {}


def _token_graph(valores: dict, timeout: float) -> str:
    tenant = str(valores.get("tenant") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9.-]{3,80}", tenant):
        raise ErrorDeNegocio("la credencial de Microsoft Graph necesita tenant (id o dominio)")
    r = _json_https("POST", f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", timeout, form={
        "client_id": str(valores.get("client_id") or ""), "client_secret": str(valores.get("client_secret") or ""),
        "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"})
    if not r.get("access_token"):
        raise ErrorDeNegocio("Microsoft no entregó un token")
    return r["access_token"]


def _buzon_graph(valores: dict) -> str:
    buzon = str(valores.get("buzon") or "").strip()
    if not re.fullmatch(r"[^@\s/]+@[^@\s/]+", buzon):
        raise ErrorDeNegocio("la credencial de Microsoft Graph necesita buzon (dirección del buzón)")
    revisar_buzon(buzon)
    return buzon


def leer_graph(valores: dict, carpeta: str, filtro: Dict[str, Any], desde: str, maximo: int, incluir_adjuntos: bool,
               timeout: float) -> List[dict]:
    buzon = _buzon_graph(valores)
    token = _token_graph(valores, timeout)
    q = {"$top": str(maximo), "$orderby": "receivedDateTime desc",
         "$select": "id,internetMessageId,subject,from,toRecipients,ccRecipients,receivedDateTime,body,hasAttachments,isRead"}
    filtros = []
    if desde:
        if not re.match(r"^\d{4}-\d{2}-\d{2}", desde):
            raise ErrorDeNegocio(f"desde «{desde}» tiene que ser una fecha AAAA-MM-DD")
        filtros.append(f"receivedDateTime ge {desde[:10]}T00:00:00Z")
    if filtro.get("no_leidos"):
        filtros.append("isRead eq false")
    if filtros:
        q["$filter"] = " and ".join(filtros)
    carpeta = carpeta or "inbox"
    if not re.fullmatch(r"[A-Za-z0-9=_-]{1,200}", carpeta):
        raise ErrorDeNegocio("carpeta de Graph: usa un nombre conocido (inbox, archive…) o el id de la carpeta")
    base = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(buzon)}"
    r = _json_https("GET", f"{base}/mailFolders/{carpeta}/messages?{urllib.parse.urlencode(q)}", timeout, token)
    salida = []
    for m in r.get("value") or []:
        de = (m.get("from") or {}).get("emailAddress") or {}
        cuerpo = m.get("body") or {}
        x = {"id": m.get("internetMessageId") or m.get("id"), "graph_id": m.get("id"), "asunto": m.get("subject") or "",
             "remitente": f"{de.get('name', '')} <{de.get('address', '')}>", "remitente_correo": de.get("address", ""),
             "remitente_nombre": de.get("name", ""),
             "para": ", ".join((t.get("emailAddress") or {}).get("address", "") for t in m.get("toRecipients") or []),
             "cc": ", ".join((t.get("emailAddress") or {}).get("address", "") for t in m.get("ccRecipients") or []),
             "fecha": m.get("receivedDateTime") or "",
             "cuerpo": (texto_plano(cuerpo.get("content", "")) if cuerpo.get("contentType") == "html" else cuerpo.get("content", ""))[:60000],
             "adjuntos": []}
        if filtro.get("remitente") and _ascii(filtro["remitente"]).lower() not in _ascii(x["remitente"]).lower():
            continue
        if filtro.get("asunto") and _ascii(filtro["asunto"]).lower() not in _ascii(x["asunto"]).lower():
            continue
        if filtro.get("texto") and _ascii(filtro["texto"]).lower() not in _ascii(x["asunto"] + " " + x["cuerpo"]).lower():
            continue
        if m.get("hasAttachments"):
            adj = _json_https("GET", f"{base}/messages/{urllib.parse.quote(m['id'])}/attachments", timeout, token)
            for a in adj.get("value") or []:
                y = {"nombre": a.get("name") or "adjunto", "tipo": a.get("contentType") or "", "tamano": a.get("size") or 0}
                if incluir_adjuntos and a.get("contentBytes") and (a.get("size") or 0) <= MAX_ADJUNTO and not a.get("isInline"):
                    y["_datos"] = base64.b64decode(a["contentBytes"])
                x["adjuntos"].append(y)
        salida.append(x)
    return salida


def leer(tipo: str, valores: dict, carpeta: str, filtro: Dict[str, Any], desde: str, maximo: int, incluir_adjuntos: bool,
         timeout: float) -> List[dict]:
    maximo = max(1, min(MAX_CORREOS, int(maximo or 20)))
    if tipo == "imap":
        return leer_imap(valores, carpeta, filtro, desde, maximo, incluir_adjuntos, timeout)
    if tipo == "microsoft_graph":
        return leer_graph(valores, carpeta, filtro, desde, maximo, incluir_adjuntos, timeout)
    raise ErrorDeNegocio(f"para leer correo la credencial tiene que ser imap o microsoft_graph (es {tipo})")


# ------------------------------------------------------------------- envío

def _smtp_real(host: str, puerto: int, tls: str, timeout: float):
    ip = destino_seguro(host, puerto, red_privada=True)
    if tls == "ssl":
        return smtplib.SMTP_SSL(host, puerto, timeout=timeout, context=ssl.create_default_context())
    s = smtplib.SMTP(ip, puerto, timeout=timeout)
    s._host = host  # para el STARTTLS con el nombre del servidor
    if tls == "starttls":
        s.starttls(context=ssl.create_default_context())
    return s


fabrica_smtp: Callable[..., Any] = _smtp_real


def revisar_destinatarios(mensaje: dict) -> List[str]:
    """Todos los destinatarios del mensaje; ErrorDeNegocio si alguno es vetado o no parece una dirección."""
    todos = direcciones(mensaje.get("para")) + direcciones(mensaje.get("cc")) + direcciones(mensaje.get("cco"))
    if not direcciones(mensaje.get("para")):
        raise ErrorDeNegocio("el correo no tiene destinatario (para)")
    for d in todos:
        if not re.fullmatch(r"[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+", d):
            raise ErrorDeNegocio(f"«{d[:120]}» no es una dirección de correo")
        revisar_buzon(d)
    if len(todos) > 50:
        raise ErrorDeNegocio("un correo no puede ir a más de 50 destinatarios")
    return todos


def revisar_adjuntos(adjuntos: List[dict], tipo: str = "") -> None:
    """Los límites de los adjuntos de un correo: cuántos, cuánto pesan en total y, por Graph, cada uno (un solo pedido)."""
    if len(adjuntos) > MAX_ADJUNTOS_ENVIO:
        raise ErrorDeNegocio(f"son {len(adjuntos)} adjuntos y un correo lleva hasta {MAX_ADJUNTOS_ENVIO}")
    total = sum(len(a.get("datos") or b"") for a in adjuntos)
    if total > MAX_BYTES_ENVIO:
        raise ErrorDeNegocio(f"los adjuntos suman {total / 1_000_000:.1f} MB y un correo lleva hasta {MAX_BYTES_ENVIO // 1_000_000} MB en total")
    if tipo == "microsoft_graph":
        grandes = [a.get("nombre") for a in adjuntos if len(a.get("datos") or b"") > MAX_BYTES_GRAPH]
        if grandes:
            raise ErrorDeNegocio(f"por Microsoft Graph cada adjunto va hasta {MAX_BYTES_GRAPH // 1_000_000} MB: «{', '.join(map(str, grandes))[:200]}» pasa "
                                 "(usa una credencial smtp o un enlace de descarga)")


def _armar_mime(remitente: str, mensaje: dict) -> EmailMessage:
    """El correo tal como sale por SMTP: texto (y HTML), adjuntos y, si responde, In-Reply-To y References."""
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = remitente, ", ".join(direcciones(mensaje.get("para"))), mensaje.get("asunto") or ""
    if mensaje.get("cc"):
        m["Cc"] = ", ".join(direcciones(mensaje.get("cc")))
    m["Message-ID"] = email.utils.make_msgid(domain=(remitente.rsplit("@", 1)[-1] or "positiva-ia.local"))
    original = str(mensaje.get("en_respuesta_a") or "").strip()
    if original:
        if "\r" in original or "\n" in original:
            raise ErrorDeNegocio("el Message-ID del correo original no es válido")
        m["In-Reply-To"] = original
        refs = " ".join(x for x in str(mensaje.get("referencias") or "").split() if x != original)
        m["References"] = f"{refs} {original}".strip()[-4000:]
    m.set_content(mensaje.get("cuerpo") or "")
    if mensaje.get("html"):
        m.add_alternative(mensaje["html"], subtype="html")
    for a in mensaje.get("adjuntos") or []:
        tipo = str(a.get("tipo") or "") or (mimetypes.guess_type(str(a.get("nombre") or ""))[0] or "application/octet-stream")
        principal, _, sub = tipo.partition("/")
        m.add_attachment(a.get("datos") or b"", maintype=principal or "application", subtype=sub or "octet-stream", filename=str(a.get("nombre") or "adjunto"))
    return m


def _enviar_smtp(valores: dict, mensaje: dict, todos: List[str], timeout: float) -> dict:
    host, puerto = str(valores.get("host") or ""), int(valores.get("puerto") or 587)
    remitente = str(valores.get("remitente") or valores.get("usuario") or "")
    revisar_buzon(remitente)
    m = _armar_mime(remitente, mensaje)
    try:
        s = fabrica_smtp(host, puerto, _modo_tls(valores.get("tls"), "starttls"), timeout)
    except (socket.timeout, TimeoutError) as e:
        raise TimeoutError(f"el servidor SMTP {host} no respondió a tiempo") from e
    try:
        if valores.get("usuario"):
            try:
                s.login(str(valores["usuario"]), str(valores.get("clave") or ""))
            except smtplib.SMTPAuthenticationError as e:
                raise ErrorDeNegocio("SMTP rechazó el usuario o la clave") from e
        s.send_message(m, from_addr=remitente, to_addrs=todos)
    finally:
        try:
            s.quit()
        except Exception:
            pass
    return {"enviado": True, "id": m["Message-ID"], **({"en_respuesta_a": m["In-Reply-To"]} if m["In-Reply-To"] else {})}


def _adjuntos_graph(adjuntos: List[dict]) -> List[dict]:
    return [{"@odata.type": "#microsoft.graph.fileAttachment", "name": str(a.get("nombre") or "adjunto"),
             "contentType": str(a.get("tipo") or "application/octet-stream"), "contentBytes": base64.b64encode(a.get("datos") or b"").decode()}
            for a in adjuntos]


def enviar(tipo: str, valores: dict, mensaje: dict, timeout: float) -> dict:
    """Envía de verdad (quien llama ya revisó permitir_escritura). Devuelve {enviado, id}. mensaje.adjuntos: [{nombre, tipo, datos}]."""
    todos = revisar_destinatarios(mensaje)
    revisar_adjuntos(mensaje.get("adjuntos") or [], tipo)
    if tipo == "smtp":
        return _enviar_smtp(valores, mensaje, todos, timeout)
    if tipo == "microsoft_graph":
        buzon = _buzon_graph(valores)
        token = _token_graph(valores, timeout)
        dest = lambda xs: [{"emailAddress": {"address": d}} for d in direcciones(xs)]  # noqa: E731
        _json_https("POST", f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(buzon)}/sendMail", timeout, token, datos={
            "message": {"subject": mensaje.get("asunto") or "",
                        "body": {"contentType": "HTML" if mensaje.get("html") else "Text", "content": mensaje.get("html") or mensaje.get("cuerpo") or ""},
                        "toRecipients": dest(mensaje.get("para")), "ccRecipients": dest(mensaje.get("cc")), "bccRecipients": dest(mensaje.get("cco")),
                        **({"attachments": _adjuntos_graph(mensaje["adjuntos"])} if mensaje.get("adjuntos") else {})},
            "saveToSentItems": True})
        return {"enviado": True, "id": ""}
    raise ErrorDeNegocio(f"para enviar correo la credencial tiene que ser smtp o microsoft_graph (es {tipo})")


def _id_graph(base: str, original: dict, timeout: float, token: str) -> str:
    """El id de Graph del correo original: el guardado al traerlo o, si no, se busca por su Message-ID (solo lectura)."""
    gid = str(original.get("graph_id") or "").strip()
    if gid:
        if not re.fullmatch(r"[A-Za-z0-9=_+/-]{10,400}", gid):
            raise ErrorDeNegocio("el id de Graph del correo original no es válido")
        return gid
    mid = str(original.get("message_id") or "").strip()
    if not mid or "'" in mid:
        raise ErrorDeNegocio("el correo original no tiene un Message-ID para encontrarlo en el buzón")
    q = urllib.parse.urlencode({"$filter": f"internetMessageId eq '{mid}'", "$select": "id", "$top": "1"})
    r = _json_https("GET", f"{base}/messages?{q}", timeout, token)
    v = r.get("value") or []
    if not v or not v[0].get("id"):
        raise ErrorDeNegocio("el correo original ya no está en el buzón del flujo: no se puede responder en su hilo")
    return str(v[0]["id"])


def responder(tipo: str, valores: dict, original: dict, mensaje: dict, timeout: float) -> dict:
    """Responde en el MISMO hilo (quien llama ya revisó permitir_escritura).

    - microsoft_graph: createReply sobre el correo original (con el texto citado si incluir_original), ajusta para, cc y cuerpo,
      sube los adjuntos al borrador y lo envía (send).
    - smtp: un correo con In-Reply-To y References del original (los clientes de correo lo muestran en el mismo hilo).
    original: {message_id, referencias, graph_id?}; mensaje: {para, cc, asunto, cuerpo, html?, adjuntos, incluir_original, cita}."""
    todos = revisar_destinatarios(mensaje)
    revisar_adjuntos(mensaje.get("adjuntos") or [], tipo)
    if tipo == "smtp":
        cuerpo = mensaje.get("cuerpo") or ""
        if mensaje.get("incluir_original") and mensaje.get("cita"):
            cuerpo = f"{cuerpo}\n\n{mensaje['cita']}"
        return _enviar_smtp(valores, {**mensaje, "cuerpo": cuerpo, "en_respuesta_a": original.get("message_id") or "",
                                      "referencias": original.get("referencias") or ""}, todos, timeout)
    if tipo == "microsoft_graph":
        buzon = _buzon_graph(valores)
        token = _token_graph(valores, timeout)
        base = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(buzon)}"
        gid = _id_graph(base, original, timeout, token)
        contenido = mensaje.get("html") or mensaje.get("cuerpo") or ""
        incluir = bool(mensaje.get("incluir_original"))
        # con el original: el texto va como comentario encima de la cita que arma Outlook; sin él, el cuerpo se reemplaza
        borrador = _json_https("POST", f"{base}/messages/{urllib.parse.quote(gid, safe='')}/createReply", timeout, token,
                               datos={"comment": contenido} if incluir else {})
        did = str(borrador.get("id") or "")
        if not did:
            raise ErrorDeNegocio("Microsoft no devolvió el borrador de la respuesta")
        dest = lambda xs: [{"emailAddress": {"address": d}} for d in direcciones(xs)]  # noqa: E731
        cambios: Dict[str, Any] = {"toRecipients": dest(mensaje.get("para")), "ccRecipients": dest(mensaje.get("cc"))}
        if mensaje.get("asunto"):
            cambios["subject"] = mensaje["asunto"]
        if not incluir:
            cambios["body"] = {"contentType": "HTML" if mensaje.get("html") else "Text", "content": contenido}
        url_borrador = f"{base}/messages/{urllib.parse.quote(did, safe='')}"
        _json_https("PATCH", url_borrador, timeout, token, datos=cambios)
        for a in _adjuntos_graph(mensaje.get("adjuntos") or []):
            _json_https("POST", f"{url_borrador}/attachments", timeout, token, datos=a)
        _json_https("POST", f"{url_borrador}/send", timeout, token, datos={})
        return {"enviado": True, "id": did, "en_respuesta_a": gid}
    raise ErrorDeNegocio(f"para responder un correo la credencial tiene que ser smtp o microsoft_graph (es {tipo})")
