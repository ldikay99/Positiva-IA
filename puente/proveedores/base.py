"""
Lo común a todos los proveedores de IA: la petición y el resultado normalizados,
los errores, los reintentos, los límites de concurrencia y de tasa, la máscara
de llaves, la red segura y la validación de JSON contra un esquema.

Límites (configurables por variables de entorno del contenedor):

- Concurrencia: un semáforo por proveedor + credencial (`IA_CONCURRENCIA`, 4 por
  defecto; `IA_CONCURRENCIA_<PROVEEDOR>` para uno solo, p. ej.
  `IA_CONCURRENCIA_OPENAI=8`). Si no se libera un cupo en el tiempo de la
  llamada, falla como error pasajero.
- Tasa: un balde de fichas por proveedor + credencial + flujo (`IA_POR_MINUTO`,
  120 por defecto; `IA_POR_MINUTO_<PROVEEDOR>`; un flujo puede bajar el suyo con
  `definicion.ia.por_minuto`). El balde es por flujo: la ráfaga de un flujo no
  gasta las fichas de otro.
- Reintentos: 429, 408, 409, 425, 5xx, tiempo agotado y conexión caída, hasta
  `IA_INTENTOS` (3 por defecto) intentos en total, con espera exponencial al
  azar (full jitter) y respetando Retry-After (tope 60 s).

Cada flujo corre en sus propios trabajadores (procesos), así que nada de esto se
comparte entre flujos; dentro de un proceso, los clientes se guardan por
proveedor + huella de la credencial + flujo.
"""
from __future__ import annotations

import base64
import email.utils
import hashlib
import json
import os
import random
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from conectores.base import ErrorDeNegocio, ErrorTransitorio, destino_seguro  # noqa: F401

MASCARA = "••••"
INTENTOS = int(os.environ.get("IA_INTENTOS") or 3)
ESPERA_INICIAL_S, ESPERA_MAXIMA_S, TOPE_RETRY_AFTER_S = 1.0, 20.0, 60.0
TIMEOUT_S = 120.0

# Las pruebas los cambian para no dormir ni esperar de verdad.
dormir: Callable[[float], None] = time.sleep
reloj: Callable[[], float] = time.monotonic


# ------------------------------------------------------------------ errores

class ErrorDeProveedor(Exception):
    """Un error del servicio de IA, con su código HTTP (si lo hay) y el mensaje ya enmascarado."""

    def __init__(self, mensaje: str, status_code: Optional[int] = None, retry_after: Optional[float] = None,
                 proveedor: str = "") -> None:
        super().__init__(mensaje)
        self.status_code = status_code
        self.code = status_code
        self.retry_after = retry_after
        self.proveedor = proveedor


def es_pasajero(e: BaseException) -> bool:
    if isinstance(e, ErrorDeNegocio):
        return False
    if isinstance(e, (ErrorTransitorio, TimeoutError, ConnectionError)):
        return True
    codigo = getattr(e, "status_code", None) or getattr(e, "code", None)
    if isinstance(codigo, int) and not isinstance(codigo, bool):
        return codigo in (408, 409, 425, 429) or codigo >= 500
    nombre = type(e).__name__
    return nombre in {"ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "ConnectError", "ReadError",
                      "RemoteProtocolError", "ServerError", "ServiceUnavailable", "TooManyRequests", "DeadlineExceeded"}


def retry_after(valor: Any) -> Optional[float]:
    """Segundos de un encabezado Retry-After (número o fecha HTTP)."""
    if valor in (None, ""):
        return None
    try:
        return max(0.0, float(valor))
    except (TypeError, ValueError):
        pass
    try:
        fecha = email.utils.parsedate_to_datetime(str(valor))
        return max(0.0, fecha.timestamp() - time.time())
    except (TypeError, ValueError, IndexError):
        return None


# ------------------------------------------------------------------- máscara

_PATRONES_LLAVE = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), re.compile(r"sk-(?:proj-|or-v1-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), re.compile(r"gsk_[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}"), re.compile(r"(?i)((?:api[-_]?key|key|token)=)[^&\s\"']{8,}"),
]


def enmascarar(texto: Any, secretos: Iterable[Any] = ()) -> str:
    """El texto sin llaves: los valores dados (tal cual, en JSON y en URL) y los formatos de llave conocidos."""
    t = "" if texto is None else str(texto)
    valores = sorted({str(s) for s in secretos if s is not None and len(str(s)) >= 4}, key=len, reverse=True)
    for s in valores:
        for v in {s, json.dumps(s, ensure_ascii=False)[1:-1], urllib.parse.quote(s, safe="")}:
            if v and v in t:
                t = t.replace(v, MASCARA)
    for p in _PATRONES_LLAVE:
        t = p.sub(lambda m: (m.group(1) if m.groups() else "") + MASCARA, t)
    return t


def secretos_de(cred: Optional[dict]) -> List[str]:
    """Los valores que nunca salen de una credencial de IA (llaves y encabezados)."""
    cred = cred or {}
    r = [cred.get(k) for k in ("api_key", "clave", "token")]
    enc = cred.get("encabezados")
    if isinstance(enc, str) and enc.strip():
        try:
            enc = json.loads(enc)
        except ValueError:
            enc = {}
    if isinstance(enc, dict):
        r += [v for v in enc.values() if isinstance(v, str) and "{{" not in v]
    return [x for x in r if isinstance(x, str) and len(x) >= 4]


def huella(cred: Optional[dict]) -> str:
    """Una huella corta de la credencial: separa clientes y semáforos sin guardar la llave."""
    if not cred:
        return "-"
    return hashlib.sha256(json.dumps(cred, sort_keys=True, default=str).encode()).hexdigest()[:16]


# -------------------------------------------------------- petición/resultado

@dataclass
class Parte:
    """Un trozo del mensaje: texto, imagen o PDF."""
    tipo: str  # texto | imagen | pdf
    texto: str = ""
    datos: bytes = b""
    mime: str = ""
    nombre: str = ""

    def b64(self) -> str:
        return base64.b64encode(self.datos).decode()


def partes_de(x: Any) -> List[Parte]:
    """Normaliza: texto, Parte, {tipo, texto|datos(bytes o base64), mime}."""
    if x is None:
        return []
    if isinstance(x, str):
        return [Parte("texto", texto=x)]
    if isinstance(x, Parte):
        return [x]
    if isinstance(x, dict):
        tipo = str(x.get("tipo") or ("texto" if "texto" in x else "imagen"))
        datos = x.get("datos") or b""
        if isinstance(datos, str):
            datos = base64.b64decode(datos)
        mime = str(x.get("mime") or ("application/pdf" if tipo == "pdf" else "image/jpeg" if tipo == "imagen" else ""))
        return [Parte(tipo, texto=str(x.get("texto") or ""), datos=datos, mime=mime, nombre=str(x.get("nombre") or ""))]
    if isinstance(x, (list, tuple)):
        r: List[Parte] = []
        for y in x:
            r += partes_de(y)
        return r
    return [Parte("texto", texto=str(x))]


@dataclass
class Peticion:
    """Lo que se le pide al modelo, igual para todos los proveedores.

    `turnos` es la conversación neutral:
      {"rol": "usuario", "partes": [Parte]}
      {"rol": "asistente", "texto": str, "llamadas": [{id, nombre, args}], "nativo": <lo que devolvió el proveedor>}
      {"rol": "herramienta", "resultados": [{id, nombre, resultado}]}
    """
    modelo: str
    sistema: str = ""
    turnos: List[dict] = field(default_factory=list)
    esquema: Optional[dict] = None
    json_libre: bool = False
    temperatura: Optional[float] = None
    herramientas: List[dict] = field(default_factory=list)  # [{nombre, descripcion, parametros}]
    timeout: float = TIMEOUT_S
    max_tokens: Optional[int] = None
    sin_razonamiento: bool = False
    instruir_json: bool = False  # el adaptador no lo hace nativo: va en el sistema


@dataclass
class Resultado:
    texto: str = ""
    json: Any = None
    llamadas: List[dict] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_salida: int = 0
    nativo: Any = None
    fin: str = ""


# ------------------------------------------------------------------ límites

def _entero_env(nombre: str, defecto: int) -> int:
    try:
        return max(1, int(os.environ.get(nombre) or defecto))
    except ValueError:
        return defecto


class Balde:
    """Balde de fichas: `por_minuto` fichas por minuto, ráfaga de hasta `capacidad`."""

    def __init__(self, por_minuto: float, capacidad: Optional[float] = None) -> None:
        self.tasa = max(0.001, por_minuto / 60.0)
        self.capacidad = max(1.0, capacidad if capacidad is not None else max(1.0, por_minuto / 10.0))
        self.fichas = self.capacidad
        self.t = reloj()
        self.cerrojo = threading.Lock()

    def tomar(self, tope_s: float) -> float:
        """Espera hasta que haya una ficha; devuelve cuánto esperó. ErrorTransitorio si pasaría de tope_s."""
        esperado = 0.0
        while True:
            with self.cerrojo:
                ahora = reloj()
                self.fichas = min(self.capacidad, self.fichas + (ahora - self.t) * self.tasa)
                self.t = ahora
                if self.fichas >= 1:
                    self.fichas -= 1
                    return esperado
                falta = (1 - self.fichas) / self.tasa
            if esperado + falta > tope_s:
                raise ErrorTransitorio(f"límite de tasa del proveedor: haría falta esperar {falta:.1f} s más")
            dormir(falta)
            esperado += falta


_semaforos: Dict[Tuple[str, str], threading.BoundedSemaphore] = {}
_baldes: Dict[Tuple[str, str, str], Balde] = {}
_cerrojo_limites = threading.Lock()


def concurrencia_de(proveedor: str) -> int:
    return _entero_env(f"IA_CONCURRENCIA_{proveedor.upper()}", _entero_env("IA_CONCURRENCIA", 4))


def por_minuto_de(proveedor: str) -> int:
    return _entero_env(f"IA_POR_MINUTO_{proveedor.upper()}", _entero_env("IA_POR_MINUTO", 120))


def semaforo(proveedor: str, cred_huella: str, cupo: Optional[int] = None) -> threading.BoundedSemaphore:
    with _cerrojo_limites:
        k = (proveedor, cred_huella)
        if k not in _semaforos:
            _semaforos[k] = threading.BoundedSemaphore(cupo or concurrencia_de(proveedor))
        return _semaforos[k]


def balde(proveedor: str, cred_huella: str, flujo: str, por_minuto: Optional[float] = None) -> Balde:
    with _cerrojo_limites:
        k = (proveedor, cred_huella, flujo or "-")
        tasa = float(por_minuto or por_minuto_de(proveedor))
        b = _baldes.get(k)
        if b is None or abs(b.tasa * 60 - tasa) > 1e-9:
            b = _baldes[k] = Balde(tasa)
        return b


def reiniciar_limites() -> None:
    with _cerrojo_limites:
        _semaforos.clear()
        _baldes.clear()


# ---------------------------------------------------------------- reintentos

def con_reintentos(fn: Callable[[], Resultado], intentos: int = INTENTOS, aviso: Optional[Callable[..., None]] = None,
                   secretos: Iterable[str] = ()) -> Tuple[Resultado, int]:
    """Corre fn; ante un error pasajero espera (Retry-After o backoff con jitter) y repite. Devuelve (resultado, intentos usados)."""
    secretos = list(secretos)
    for k in range(1, max(1, intentos) + 1):
        try:
            return fn(), k
        except Exception as e:
            if k >= intentos or not es_pasajero(e):
                raise
            ra = getattr(e, "retry_after", None)
            if isinstance(ra, (int, float)) and ra >= 0:
                espera = min(TOPE_RETRY_AFTER_S, float(ra)) + random.uniform(0, 0.25)
            else:
                espera = random.uniform(0, min(ESPERA_MAXIMA_S, ESPERA_INICIAL_S * 2 ** (k - 1)))
            if aviso:
                aviso("reintento", intento=k, espera_s=round(espera, 2), error=enmascarar(f"{type(e).__name__}: {e}", secretos)[:300],
                      status=getattr(e, "status_code", None))
            dormir(espera)
    raise RuntimeError("sin intentos")  # no se llega


# --------------------------------------------------------------------- red

def url_segura(url: str, permitir_privada: bool = True) -> str:
    """Revisa una URL base que dio una persona (servicios propios o locales): http(s), sin usuario ni clave,
    nunca Positiva, SGDEA, metadatos, loopback ni enlace local. Las IP privadas sí (servicios en la red interna)."""
    u = urllib.parse.urlsplit(str(url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ErrorDeNegocio("la URL del servicio de IA tiene que empezar por http:// o https://")
    if u.username or u.password:
        raise ErrorDeNegocio("la URL del servicio de IA no puede llevar usuario ni clave: usa la llave de la credencial")
    destino_seguro(u.hostname, u.port or (443 if u.scheme == "https" else 80), red_privada=permitir_privada)
    return str(url).strip().rstrip("/")


_clientes: Dict[Tuple[str, str, str], Any] = {}
_cerrojo_clientes = threading.Lock()


def cliente_http(proveedor: str, cred_huella: str, flujo: str):
    """Un httpx.Client por proveedor + credencial + flujo (conexiones reutilizadas, nada compartido entre flujos)."""
    import httpx

    k = (proveedor, cred_huella, flujo or "-")
    with _cerrojo_clientes:
        c = _clientes.get(k)
        if c is None:
            c = _clientes[k] = httpx.Client(follow_redirects=False, headers={"User-Agent": "positiva-ia/1"})
        return c


def pedir_json(proveedor: str, metodo: str, url: str, encabezados: Dict[str, str], cuerpo: Any = None, timeout: float = TIMEOUT_S,
               secretos: Iterable[str] = (), cred_huella: str = "-", flujo: str = "") -> Any:
    """Una petición HTTP que devuelve JSON. Errores como ErrorDeProveedor (con status y Retry-After), siempre enmascarados."""
    import httpx

    secretos = list(secretos)
    c = cliente_http(proveedor, cred_huella, flujo)
    try:
        r = c.request(metodo, url, headers=encabezados, json=cuerpo, timeout=httpx.Timeout(timeout, connect=min(15.0, timeout)))
    except httpx.TimeoutException as e:
        raise TimeoutError(enmascarar(f"{proveedor}: se agotó la espera ({type(e).__name__})", secretos)) from None
    except httpx.TransportError as e:
        raise ConnectionError(enmascarar(f"{proveedor}: no se pudo conectar ({type(e).__name__}: {e})", secretos)) from None
    if r.status_code >= 400:
        detalle = r.text[:600]
        try:
            j = r.json()
            err = j.get("error") if isinstance(j, dict) else None
            detalle = (err.get("message") if isinstance(err, dict) else err) or (j.get("message") if isinstance(j, dict) else None) or detalle
        except ValueError:
            pass
        raise ErrorDeProveedor(enmascarar(f"{proveedor} respondió {r.status_code}: {detalle}", secretos)[:700], r.status_code,
                               retry_after(r.headers.get("retry-after")), proveedor)
    try:
        return r.json()
    except ValueError:
        raise ErrorDeProveedor(enmascarar(f"{proveedor} devolvió algo que no es JSON: {r.text[:200]}", secretos), r.status_code) from None


def navegar(v: Any, ruta: str) -> Any:
    """a.b.0.c dentro de un JSON; None si no está."""
    for s in [x for x in re.split(r"\.|\[(\d+)\]", str(ruta or "")) if x not in (None, "")]:
        if isinstance(v, list) and s.isdigit():
            i = int(s)
            v = v[i] if i < len(v) else None
        elif isinstance(v, dict):
            v = v.get(s)
        else:
            return None
        if v is None:
            return None
    return v


# --------------------------------------------------------------------- JSON

def json_de(texto: str) -> Any:
    t = (texto or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        m = re.search(r"[\{\[][\s\S]*[\}\]]", t)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


_TIPOS = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}


def errores_esquema(v: Any, esquema: Optional[dict], ruta: str = "$") -> List[str]:
    """Lo que no cumple el esquema (type, properties, required, items, enum). Tolerante: lo que no conoce no lo exige."""
    if not isinstance(esquema, dict) or not esquema:
        return []
    r: List[str] = []
    t = esquema.get("type")
    if isinstance(t, str) and t in _TIPOS:
        ok = isinstance(v, _TIPOS[t]) and not (t in ("integer", "number") and isinstance(v, bool))
        if t == "integer" and isinstance(v, float) and v.is_integer():
            ok = True
        if not ok and not (v is None and esquema.get("nullable")):
            return [f"{ruta}: se esperaba {t}"]
    if "enum" in esquema and isinstance(esquema["enum"], list) and v is not None and v not in esquema["enum"]:
        r.append(f"{ruta}: «{v}» no está en {esquema['enum']}")
    if isinstance(v, dict):
        for k in esquema.get("required") or []:
            if k not in v:
                r.append(f"{ruta}.{k}: falta")
        for k, sub in (esquema.get("properties") or {}).items():
            if k in v:
                r += errores_esquema(v[k], sub, f"{ruta}.{k}")
    if isinstance(v, list) and isinstance(esquema.get("items"), dict):
        for i, x in enumerate(v[:200]):
            r += errores_esquema(x, esquema["items"], f"{ruta}[{i}]")
    return r


def instruccion_json(esquema: Optional[dict]) -> str:
    if esquema:
        return ("\n\nResponde SOLO con un objeto JSON válido (sin texto alrededor ni ```), que cumpla este JSON Schema:\n"
                + json.dumps(esquema, ensure_ascii=False))
    return "\n\nResponde SOLO con JSON válido, sin texto alrededor ni ```."


def esquema_estricto(e: Any) -> Any:
    """Copia del esquema con additionalProperties: false en cada objeto (lo exigen OpenAI y Anthropic)."""
    if isinstance(e, dict):
        r = {k: esquema_estricto(v) for k, v in e.items()}
        if r.get("type") == "object":
            r.setdefault("additionalProperties", False)
            r.setdefault("properties", {})
        return r
    if isinstance(e, list):
        return [esquema_estricto(x) for x in e]
    return e


def a_texto_json(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
