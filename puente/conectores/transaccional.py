"""
Mensajería transaccional (plan 5d, fase 2) por HTTP, sin SDK: Telegram (Bot
API), SendGrid y Mailgun (correo) y Twilio (SMS). Todo sale por `rest.pedir`
(https, destino revisado, sin redirecciones). Quien llama decide si sale: estas
funciones solo se usan dentro de `_escribir` (escritura habilitada por una
persona); sin eso, el mensaje queda armado y retenido.

Nunca a direcciones de Positiva ni a los buzones protegidos (remitente,
destinatarios, copia y copia oculta); el dominio de Mailgun tampoco puede ser
de Positiva.
"""
from __future__ import annotations

import base64
import re
from typing import Any, Dict, List

from . import rest
from .base import ErrorDeNegocio, direccion_vetada, direcciones, host_vetado

BASES = {"telegram": "https://api.telegram.org", "sendgrid": "https://api.sendgrid.com", "mailgun_us": "https://api.mailgun.net",
         "mailgun_eu": "https://api.eu.mailgun.net", "twilio": "https://api.twilio.com"}
TOKEN_TELEGRAM = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,60}$")
TELEFONO = re.compile(r"^\+[1-9]\d{6,14}$")
CORREO = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
MAX_TELEGRAM = 4096
MAX_SMS = 1600


def revisar_correos(m: Dict[str, Any]) -> None:
    """ErrorDeNegocio si algún remitente o destinatario es de Positiva, un buzón protegido o no es un correo."""
    if not direcciones(m.get("para")):
        raise ErrorDeNegocio("falta para (los destinatarios)")
    for campo in ("remitente", "para", "cc", "cco", "responder_a"):
        for d in direcciones(m.get(campo)):
            if not CORREO.match(d):
                raise ErrorDeNegocio(f"{campo}: «{d[:80]}» no es una dirección de correo")
            motivo = direccion_vetada(d)
            if motivo:
                raise ErrorDeNegocio(f"bloqueado por seguridad: {motivo}")


def telefono(x: Any, que: str = "para") -> str:
    t = re.sub(r"[\s().-]", "", str(x or ""))
    if not TELEFONO.match(t):
        raise ErrorDeNegocio(f"{que}: «{str(x)[:40]}» no es un número en formato internacional (+57…)")
    return t


def trozos(texto: str, tope: int) -> List[str]:
    """Parte un texto largo en mensajes de hasta `tope` caracteres, cortando en saltos de línea si se puede."""
    r, t = [], texto
    while len(t) > tope:
        corte = t.rfind("\n", 0, tope)
        corte = corte if corte > tope // 2 else tope
        r.append(t[:corte])
        t = t[corte:].lstrip("\n")
    return r + ([t] if t else [])


# -------------------------------------------------------------- Telegram

def telegram(token: str, chat: str, texto: str, formato: str, archivos: List[dict], silencioso: bool, timeout: float) -> dict:
    if not TOKEN_TELEGRAM.match(token or ""):
        raise ErrorDeNegocio("el token del bot de Telegram no tiene la forma 123456:ABC… (lo da @BotFather)")
    base = f"{BASES['telegram']}/bot{token}"
    ids = []
    modo = {"html": "HTML", "markdown": "MarkdownV2"}.get(formato or "texto")
    for parte in trozos(texto, MAX_TELEGRAM) if texto else []:
        cuerpo: Dict[str, Any] = {"chat_id": chat, "text": parte, "disable_notification": bool(silencioso)}
        if modo:
            cuerpo["parse_mode"] = modo
        r = _telegram_ok(rest.pedir("POST", f"{base}/sendMessage", json=cuerpo, timeout=timeout, servicio="Telegram", errores=False))
        ids.append(r.get("message_id"))
    for a in archivos:
        datos, tipo = rest.multipart([("chat_id", "", str(chat).encode(), "text/plain"), ("document", a["nombre"], a["datos"], a["tipo"]),
                                      ("disable_notification", "", b"true" if silencioso else b"false", "text/plain")])
        r = _telegram_ok(rest.pedir("POST", f"{base}/sendDocument", data=datos, headers={"Content-Type": tipo}, timeout=timeout,
                                    servicio="Telegram", errores=False))
        ids.append(r.get("message_id"))
    return {"ids": ids, "mensajes": len(ids)}


def _telegram_ok(r: rest.Respuesta) -> dict:
    from .base import ErrorTransitorio
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status == 429 or r.status >= 500:
        raise ErrorTransitorio(f"Telegram respondió {r.status}" + (f" (reintentar en {(d.get('parameters') or {}).get('retry_after')} s)" if d.get("parameters") else ""))
    if not d.get("ok"):
        raise ErrorDeNegocio(f"Telegram no aceptó el mensaje: {str(d.get('description') or r.status)[:300]}")
    return d.get("result") or {}


def telegram_probar(token: str, timeout: float = 15) -> str:
    if not TOKEN_TELEGRAM.match(token or ""):
        raise ErrorDeNegocio("el token del bot de Telegram no tiene la forma 123456:ABC…")
    r = _telegram_ok(rest.pedir("GET", f"{BASES['telegram']}/bot{token}/getMe", timeout=timeout, servicio="Telegram", errores=False))
    return f"bot @{r.get('username', '')}"


# -------------------------------------------------------------- SendGrid

def _lista(x: Any) -> List[Dict[str, str]]:
    return [{"email": d} for d in direcciones(x)]


def sendgrid(clave: str, m: Dict[str, Any], adjuntos: List[dict], timeout: float) -> dict:
    revisar_correos(m)
    per: Dict[str, Any] = {"to": _lista(m["para"])}
    if direcciones(m.get("cc")):
        per["cc"] = _lista(m["cc"])
    if direcciones(m.get("cco")):
        per["bcc"] = _lista(m["cco"])
    contenido = [{"type": "text/plain", "value": m.get("cuerpo") or " "}]
    if m.get("html"):
        contenido.append({"type": "text/html", "value": m["html"]})
    cuerpo: Dict[str, Any] = {"personalizations": [per], "from": {"email": m["remitente"], **({"name": m["nombre_remitente"]} if m.get("nombre_remitente") else {})},
                              "subject": m.get("asunto") or "", "content": contenido}
    if m.get("responder_a"):
        cuerpo["reply_to"] = {"email": direcciones(m["responder_a"])[0]}
    if adjuntos:
        cuerpo["attachments"] = [{"content": base64.b64encode(a["datos"]).decode(), "filename": a["nombre"], "type": a["tipo"], "disposition": "attachment"}
                                 for a in adjuntos]
    r = rest.pedir("POST", f"{BASES['sendgrid']}/v3/mail/send", json=cuerpo, headers={"Authorization": f"Bearer {clave}"}, timeout=timeout,
                   servicio="SendGrid")
    return {"id": r.encabezados.get("x-message-id", ""), "status": r.status}


def sendgrid_probar(clave: str, timeout: float = 15) -> str:
    r = rest.pedir("GET", f"{BASES['sendgrid']}/v3/scopes", headers={"Authorization": f"Bearer {clave}"}, timeout=timeout, servicio="SendGrid").json()
    alcances = r.get("scopes") or []
    return "la llave de SendGrid responde" + (" y puede enviar (mail.send)" if "mail.send" in alcances else " pero NO tiene mail.send")


# -------------------------------------------------------------- Mailgun

def _base_mailgun(region: str) -> str:
    return BASES["mailgun_eu" if region == "eu" else "mailgun_us"]


def _dominio(dominio: str) -> str:
    d = str(dominio or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", d):
        raise ErrorDeNegocio("el dominio de Mailgun no es válido (p. ej. mg.empresa.com)")
    vetado = host_vetado(d)
    if vetado:
        raise ErrorDeNegocio(f"bloqueado por seguridad: {vetado}")
    return d


def mailgun(clave: str, dominio: str, region: str, m: Dict[str, Any], adjuntos: List[dict], timeout: float) -> dict:
    revisar_correos(m)
    d = _dominio(dominio)
    de = f"{m['nombre_remitente']} <{m['remitente']}>" if m.get("nombre_remitente") else m["remitente"]
    partes = [("from", "", de.encode(), "text/plain; charset=utf-8")]
    partes += [("to", "", x.encode(), "text/plain") for x in direcciones(m["para"])]
    partes += [("cc", "", x.encode(), "text/plain") for x in direcciones(m.get("cc"))]
    partes += [("bcc", "", x.encode(), "text/plain") for x in direcciones(m.get("cco"))]
    partes += [("subject", "", str(m.get("asunto") or "").encode(), "text/plain; charset=utf-8"),
               ("text", "", str(m.get("cuerpo") or " ").encode(), "text/plain; charset=utf-8")]
    if m.get("html"):
        partes.append(("html", "", str(m["html"]).encode(), "text/html; charset=utf-8"))
    if m.get("responder_a"):
        partes.append(("h:Reply-To", "", direcciones(m["responder_a"])[0].encode(), "text/plain"))
    partes += [("attachment", a["nombre"], a["datos"], a["tipo"]) for a in adjuntos]
    datos, tipo = rest.multipart(partes)
    auth = "Basic " + base64.b64encode(f"api:{clave}".encode()).decode()
    r = rest.pedir("POST", f"{_base_mailgun(region)}/v3/{d}/messages", data=datos, headers={"Content-Type": tipo, "Authorization": auth},
                   timeout=timeout, servicio="Mailgun").json()
    return {"id": r.get("id", ""), "mensaje": r.get("message", "")}


def mailgun_probar(clave: str, dominio: str, region: str, timeout: float = 15) -> str:
    d = _dominio(dominio)
    auth = "Basic " + base64.b64encode(f"api:{clave}".encode()).decode()
    r = rest.pedir("GET", f"{_base_mailgun(region)}/v3/domains/{d}", headers={"Authorization": auth}, timeout=timeout, servicio="Mailgun").json()
    return f"dominio {d} en Mailgun ({(r.get('domain') or {}).get('state', 'sin estado')})"


# -------------------------------------------------------------- Twilio

def _auth_twilio(sid: str, token: str) -> str:
    if not re.fullmatch(r"AC[0-9a-fA-F]{32}", sid or ""):
        raise ErrorDeNegocio("account_sid de Twilio empieza por AC y tiene 34 caracteres")
    return "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode()


def twilio_sms(sid: str, token: str, remitente: str, para: str, texto: str, timeout: float) -> dict:
    auth = _auth_twilio(sid, token)
    datos: Dict[str, str] = {"To": telefono(para), "Body": texto}
    if str(remitente or "").startswith("MG"):
        datos["MessagingServiceSid"] = remitente
    else:
        datos["From"] = telefono(remitente, "remitente")
    r = rest.pedir("POST", f"{BASES['twilio']}/2010-04-01/Accounts/{sid}/Messages.json", data=datos, headers={"Authorization": auth},
                   timeout=timeout, servicio="Twilio").json()
    return {"id": r.get("sid", ""), "estado": r.get("status", ""), "segmentos": r.get("num_segments")}


def twilio_probar(sid: str, token: str, timeout: float = 15) -> str:
    r = rest.pedir("GET", f"{BASES['twilio']}/2010-04-01/Accounts/{sid}.json", headers={"Authorization": _auth_twilio(sid, token)},
                   timeout=timeout, servicio="Twilio").json()
    return f"cuenta de Twilio «{r.get('friendly_name', '')}» ({r.get('status', '')})"
