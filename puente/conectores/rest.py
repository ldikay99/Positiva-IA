"""
Un cliente HTTPS pequeño y seguro para las APIs de los conectores (Google,
Microsoft Graph, OpenAPI y OAuth2): resuelve el host con `destino_seguro` y se
conecta a ESA IP (el DNS no puede cambiar entre la revisión y la conexión),
nunca sigue redirecciones salvo que se pida para bajar un archivo (y entonces
vuelve a revisar el destino y no manda la autorización), y traduce los
códigos: 408, 425, 429 y 5xx son pasajeros (ErrorTransitorio); los demás
≥ 400, ErrorDeNegocio con el mensaje que da el servicio.

Solo https; en las pruebas, un host de IPS_PERMITIDAS_PRUEBAS puede ir por http.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from .base import IPS_PERMITIDAS_PRUEBAS, ErrorDeNegocio, ErrorTransitorio, destino_seguro
from .mensajeria import _Http, _Https

MAX_RESPUESTA = 30_000_000


class Respuesta:
    """Lo que devolvió el servicio (con la forma que usan los clientes de Drive y Sheets: status_code, content, text, json())."""

    def __init__(self, status: int, encabezados: Dict[str, str], cuerpo: bytes, url: str, ms: int) -> None:
        self.status_code = self.status = status
        self.encabezados = encabezados
        self.content = cuerpo
        self.url = url
        self.ms = ms

    @property
    def text(self) -> str:
        tipo = self.encabezados.get("content-type", "")
        cod = "utf-8"
        if "charset=" in tipo:
            cod = tipo.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            return self.content.decode(cod, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        t = self.text
        return json.loads(t) if t.strip() else {}

    def datos(self) -> Any:
        """JSON si lo es; si no, el texto."""
        tipo = self.encabezados.get("content-type", "").lower()
        t = self.text
        if "json" in tipo or t.lstrip()[:1] in ("{", "["):
            try:
                return json.loads(t) if t.strip() else {}
            except ValueError:
                return t
        return t


def _detalle(r: Respuesta) -> str:
    try:
        d = r.json()
    except ValueError:
        return r.text[:300]
    if isinstance(d, dict):
        e = d.get("error")
        if isinstance(e, dict):
            return str(e.get("message") or e.get("code") or e)[:300]
        if e:
            return f"{e}: {d.get('error_description') or ''}".strip(": ")[:300]
        return str(d.get("message") or d.get("detail") or d.get("title") or "")[:300]
    return str(d)[:300]


def revisar_url(url: str) -> urllib.parse.SplitResult:
    u = urllib.parse.urlsplit(str(url or "").strip())
    host = (u.hostname or "").lower().rstrip(".")
    if not host:
        raise ErrorDeNegocio(f"«{str(url)[:120]}» no es una URL con host")
    if u.username or u.password:
        raise ErrorDeNegocio("la URL no puede llevar usuario ni clave")
    if u.scheme != "https" and not (u.scheme == "http" and host in IPS_PERMITIDAS_PRUEBAS):
        raise ErrorDeNegocio(f"solo se habla por https (no {u.scheme}://{host})")
    return u


def pedir(metodo: str, url: str, *, token: str = "", params: Optional[Dict[str, Any]] = None, json: Any = None, data: Any = None,
          headers: Optional[Dict[str, str]] = None, timeout: float = 20.0, red_privada: bool = False, seguir: bool = False,
          servicio: str = "", errores: bool = True, max_bytes: int = MAX_RESPUESTA, **_: Any) -> Respuesta:
    """Una petición. data: bytes, texto o dict (formulario). params se agregan a la consulta. Con errores=False no lanza por status."""
    import json as _json

    u = revisar_url(url)
    host = (u.hostname or "").lower().rstrip(".")
    servicio = servicio or host
    consulta = u.query
    if params:
        extra = urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None], doseq=True)
        consulta = "&".join(x for x in (consulta, extra) if x)
    ruta = (u.path or "/") + (f"?{consulta}" if consulta else "")
    cab = {"User-Agent": "Positiva-IA/flujos", "Accept": "application/json, */*;q=0.5", **(headers or {})}
    cuerpo: Optional[bytes] = None
    if json is not None:
        cuerpo = _json.dumps(json, ensure_ascii=False).encode("utf-8")
        cab.setdefault("Content-Type", "application/json; charset=utf-8")
    elif isinstance(data, dict):
        cuerpo = urllib.parse.urlencode(data).encode()
        cab.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif isinstance(data, str):
        cuerpo = data.encode("utf-8")
    elif data is not None:
        cuerpo = bytes(data)
    if token:
        cab["Authorization"] = f"Bearer {token}"
    for k, v in cab.items():
        if any(c in str(k) + str(v) for c in "\r\n"):
            raise ErrorDeNegocio(f"el encabezado {k} tiene saltos de línea")
    puerto = u.port or (443 if u.scheme == "https" else 80)
    ip = destino_seguro(host, puerto, red_privada=red_privada)
    con = (_Https if u.scheme == "https" else _Http)(host, ip, puerto, timeout)
    t = time.time()
    try:
        con.request(metodo.upper(), ruta, body=cuerpo, headers=cab)
        r = con.getresponse()
        crudo = r.read(max_bytes + 1)
        status, encabezados = r.status, {k.lower(): v for k, v in r.getheaders()}
    except (socket.timeout, TimeoutError) as e:
        raise ErrorTransitorio(f"{servicio} no respondió a tiempo") from e
    except ErrorDeNegocio:
        raise
    except (ConnectionError, OSError) as e:
        raise ErrorTransitorio(f"no se pudo conectar con {servicio}: {type(e).__name__}") from e
    finally:
        con.close()
    if len(crudo) > max_bytes:
        raise ErrorDeNegocio(f"{servicio} devolvió más de {max_bytes // 1_000_000} MB")
    resp = Respuesta(status, encabezados, crudo, url, int((time.time() - t) * 1000))
    if 300 <= status < 400 and seguir and encabezados.get("location"):
        # una descarga (Graph /content): el destino es una URL firmada; se revisa de nuevo y va sin la autorización
        destino = urllib.parse.urljoin(url, encabezados["location"])
        return pedir("GET", destino, timeout=timeout, red_privada=red_privada, servicio=servicio, errores=errores, max_bytes=max_bytes)
    if not errores:
        return resp
    if status in (408, 425, 429) or status >= 500:
        raise ErrorTransitorio(f"{servicio} respondió {status}")
    if 300 <= status < 400:
        raise ErrorDeNegocio(f"{servicio} respondió {status} (redirección; no se sigue)")
    if status >= 400:
        raise ErrorDeNegocio(f"{servicio} respondió {status}: {_detalle(resp)}")
    return resp


def multipart(campos: List[Tuple[str, str, bytes, str]]) -> Tuple[bytes, str]:
    """Un cuerpo multipart/form-data con partes (nombre, nombre_archivo o "", datos, tipo)."""
    import uuid

    frontera = f"----positiva-ia-{uuid.uuid4().hex}"
    partes: List[bytes] = []
    for nombre, archivo, datos, tipo in campos:
        nombre = nombre.replace('"', "").replace("\r", "").replace("\n", "")
        disp = f'form-data; name="{nombre}"'
        if archivo:
            a = archivo.replace('"', "").replace("\r", "").replace("\n", "")
            disp += f'; filename="{a}"; filename*=UTF-8\'\'{urllib.parse.quote(a)}'
        partes.append(f"--{frontera}\r\nContent-Disposition: {disp}\r\nContent-Type: {tipo or 'application/octet-stream'}\r\n\r\n".encode("utf-8")
                      + datos + b"\r\n")
    partes.append(f"--{frontera}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={frontera}"
