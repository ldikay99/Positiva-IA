"""
Mensajes hacia afuera: Microsoft Teams y Slack por sus webhooks entrantes, y un
webhook genérico que recibe JSON. Todo pasa por las mismas reglas:

- la URL sale de una credencial (es un secreto: quien la tiene puede publicar);
- solo https (en las pruebas, el loopback de IPS_PERMITIDAS_PRUEBAS puede ir por http);
- Teams solo a *.webhook.office.com, *.logic.azure.com y *.powerplatform.com;
  Slack solo a hooks.slack.com; el genérico a cualquier host público;
- `destino_seguro`: nunca Positiva, SGDEA, metadatos, IP privadas ni loopback;
- sin seguir redirecciones; 429 y 5xx son pasajeros (se reintentan).
Quien llama decide si se envía (permitir_escritura de la credencial).
"""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .base import IPS_PERMITIDAS_PRUEBAS, ErrorDeNegocio, ErrorTransitorio, destino_seguro, host_vetado

HOSTS_DE = {"webhook_teams": ("webhook.office.com", "logic.azure.com", "powerplatform.com", "powerautomate.com"),
            "webhook_slack": ("hooks.slack.com",)}
MAX_RESPUESTA = 200_000


class _Http(http.client.HTTPConnection):
    def __init__(self, host: str, ip: str, puerto: int, timeout: float) -> None:
        super().__init__(host, puerto, timeout=timeout)
        self._ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _Https(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, puerto: int, timeout: float) -> None:
        super().__init__(host, puerto, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        self.sock = self._context.wrap_socket(socket.create_connection((self._ip, self.port), self.timeout), server_hostname=self.host)


def revisar_url(url: str, tipo: str) -> urllib.parse.SplitResult:
    """La URL de la credencial, revisada; ErrorDeNegocio si no se puede usar."""
    u = urllib.parse.urlsplit(str(url or "").strip())
    host = (u.hostname or "").lower().rstrip(".")
    if not host:
        raise ErrorDeNegocio("la credencial no tiene una url válida")
    vetado = host_vetado(host)
    if vetado:
        raise ErrorDeNegocio(f"bloqueado por seguridad: {vetado}")
    de_prueba = host in IPS_PERMITIDAS_PRUEBAS
    if u.scheme != "https" and not (de_prueba and u.scheme == "http"):
        raise ErrorDeNegocio("la url del webhook tiene que ser https://")
    if u.username or u.password:
        raise ErrorDeNegocio("la url no puede llevar usuario ni clave")
    sufijos = HOSTS_DE.get(tipo)
    if sufijos and not de_prueba and not any(host == s or host.endswith("." + s) for s in sufijos):
        raise ErrorDeNegocio(f"una credencial {tipo} solo publica en {', '.join(sufijos)}; {host} no es de ahí")
    return u


def solo_host(url: str) -> str:
    """Lo que se puede mostrar de la URL (el host): el resto es secreto."""
    return (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()


def multipart(cuerpo: Any, archivos: List[Dict[str, Any]]) -> Tuple[bytes, str]:
    """Un cuerpo multipart/form-data: el JSON en el campo «datos» y cada archivo en «archivo» ({nombre, tipo, datos})."""
    frontera = f"----positiva-ia-{uuid.uuid4().hex}"
    partes: List[bytes] = [
        f"--{frontera}\r\nContent-Disposition: form-data; name=\"datos\"\r\nContent-Type: application/json; charset=utf-8\r\n\r\n".encode()
        + json.dumps(cuerpo, ensure_ascii=False).encode("utf-8") + b"\r\n"]
    for a in archivos:
        nombre = str(a.get("nombre") or "archivo").replace('"', "").replace("\r", "").replace("\n", "")
        partes.append(f"--{frontera}\r\nContent-Disposition: form-data; name=\"archivo\"; filename=\"{nombre}\"; "
                      f"filename*=UTF-8''{urllib.parse.quote(nombre)}\r\nContent-Type: {a.get('tipo') or 'application/octet-stream'}\r\n\r\n".encode("utf-8")
                      + (a.get("datos") or b"") + b"\r\n")
    partes.append(f"--{frontera}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={frontera}"


def publicar(url: str, tipo: str, cuerpo: Any, encabezados: Optional[Dict[str, str]] = None, timeout: float = 15.0,
             archivos: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """POST JSON a la URL (ya permitido por quien llama). Con archivos va como multipart/form-data. {status, ms, respuesta}."""
    u = revisar_url(url, tipo)
    host = (u.hostname or "").lower()
    puerto = u.port or (443 if u.scheme == "https" else 80)
    ip = destino_seguro(host, puerto)
    if archivos:
        datos, tipo_contenido = multipart(cuerpo, archivos)
    else:
        datos, tipo_contenido = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
    cab = {"Content-Type": tipo_contenido, "User-Agent": "Positiva-IA/flujos", **(encabezados or {})}
    for k, v in cab.items():
        if "\r" in str(v) or "\n" in str(v) or "\r" in k or "\n" in k:
            raise ErrorDeNegocio(f"el encabezado {k} tiene saltos de línea")
    clase = _Https if u.scheme == "https" else _Http
    con = clase(host, ip, puerto, timeout)
    t = time.time()
    try:
        con.request("POST", (u.path or "/") + (f"?{u.query}" if u.query else ""), body=datos, headers=cab)
        r = con.getresponse()
        texto = r.read(MAX_RESPUESTA).decode("utf-8", errors="replace")
        status = r.status
    except (socket.timeout, TimeoutError) as e:
        raise ErrorTransitorio(f"{host} no respondió a tiempo") from e
    except (ConnectionError, OSError) as e:
        if isinstance(e, ErrorDeNegocio):
            raise
        raise ErrorTransitorio(f"no se pudo conectar con {host}: {type(e).__name__}") from e
    finally:
        con.close()
    if status in (408, 425, 429) or status >= 500:
        raise ErrorTransitorio(f"{host} respondió {status}")
    if 300 <= status < 400:
        raise ErrorDeNegocio(f"{host} respondió {status} (redirección; no se sigue)")
    if status >= 400:
        raise ErrorDeNegocio(f"{host} respondió {status}: {texto[:200]}")
    try:
        respuesta: Any = json.loads(texto) if texto.strip()[:1] in ("{", "[") else texto[:2000]
    except ValueError:
        respuesta = texto[:2000]
    return {"status": status, "ms": int((time.time() - t) * 1000), "respuesta": respuesta}


def cuerpo_teams(titulo: str, texto: str, hechos: Optional[Dict[str, Any]] = None, enlace: str = "",
                 archivos: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Un mensaje con tarjeta adaptable (lo que aceptan los flujos de trabajo de Teams y los webhooks entrantes).
    archivos: [{nombre, url}] — un botón «Descargar» por archivo (enlace de descarga con vencimiento)."""
    cuerpo: list = []
    if titulo:
        cuerpo.append({"type": "TextBlock", "text": titulo, "weight": "Bolder", "size": "Medium", "wrap": True})
    if texto:
        cuerpo.append({"type": "TextBlock", "text": texto, "wrap": True})
    if hechos:
        cuerpo.append({"type": "FactSet", "facts": [{"title": str(k), "value": str(v)} for k, v in hechos.items()]})
    if archivos:
        cuerpo.append({"type": "TextBlock", "text": "Archivos: " + ", ".join(str(a.get("nombre") or "") for a in archivos), "wrap": True, "isSubtle": True})
    tarjeta: Dict[str, Any] = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4", "body": cuerpo}
    acciones = ([{"type": "Action.OpenUrl", "title": "Abrir", "url": enlace}] if enlace else []) + [
        {"type": "Action.OpenUrl", "title": f"Descargar {str(a.get('nombre') or '')[:40]}", "url": str(a.get("url") or "")} for a in (archivos or []) if a.get("url")]
    if acciones:
        tarjeta["actions"] = acciones[:6]
    return {"type": "message", "text": titulo or texto[:200],
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": tarjeta}]}


def cuerpo_slack(titulo: str, texto: str, hechos: Optional[Dict[str, Any]] = None, enlace: str = "",
                 archivos: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    lineas = ([f"*{titulo}*"] if titulo else []) + ([texto] if texto else []) + [f"• *{k}:* {v}" for k, v in (hechos or {}).items()]
    if enlace:
        lineas.append(f"<{enlace}|Abrir>")
    lineas += [f"• Archivo: <{a['url']}|{a.get('nombre') or 'archivo'}>" for a in archivos or [] if a.get("url")]
    return {"text": "\n".join(lineas)}
