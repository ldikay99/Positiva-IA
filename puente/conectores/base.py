"""
Lo que comparten todos los conectores: errores, destinos bloqueados, rutas
seguras del espacio de archivos y los topes de tiempo y de filas.

Bloqueos DUROS (no los levanta ninguna bandera ni credencial):
- hosts que terminan en positiva.gov.co, hosts de SGDEA y los que aparecen en
  las variables de entorno SGDEA_*, PQRD_* y TUTELAS_*;
- los buzones que leen los jobs de PQRD y tutelas (y cualquier dirección
  @positiva.gov.co): esos buzones solo se leen por las rutas de solo lectura
  del buzón que ya tiene la plataforma;
- servicios de metadatos, IP privadas, de loopback, de enlace local, etc.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

RAIZ = Path(__file__).resolve().parent.parent.parent

TIMEOUT_S = 20.0          # tiempo por defecto de todo conector
TIMEOUT_MAX_S = 60.0
MAX_FILAS = 500           # filas por defecto de una consulta
MAX_FILAS_TOPE = 5000
MAX_BYTES_ARCHIVO = 20_000_000

HOSTS_METADATOS = {"metadata", "metadata.google.internal", "metadata.goog", "169.254.169.254", "fd00:ec2::254",
                   "100.100.100.200", "instance-data", "instance-data.ec2.internal"}
DOMINIOS_VETADOS = ("positiva.gov.co",)
BUZONES_PROTEGIDOS = {"servicioalcliente@positiva.gov.co", "notificacionesjudiciales@positiva.gov.co"}
PREFIJOS_ENTORNO_VETADOS = ("PQRD_", "TUTELAS_", "SGDEA_")

# Solo las pruebas lo llenan (p. ej. con 127.0.0.1 para un servidor local): IP que
# se dejan pasar aunque sean de loopback. En la plataforma queda siempre vacío.
IPS_PERMITIDAS_PRUEBAS: Set[str] = set()


class ErrorDeNegocio(Exception):
    """Un fallo que no se arregla reintentando: datos que faltan, destino bloqueado, config imposible."""


class ErrorTransitorio(Exception):
    """Un fallo pasajero (429, 5xx, tiempo agotado, conexión caída): vale la pena reintentar."""


# ------------------------------------------------------------- rutas de datos

def ruta_estado() -> Path:
    """La carpeta de estado de la plataforma (/datos/estado en el contenedor)."""
    return Path(os.environ.get("RUTA_DATOS") or (RAIZ / "datos"))


def ruta_archivos() -> Path:
    """El espacio de archivos de los flujos (/datos/archivos en el contenedor)."""
    r = os.environ.get("RUTA_ARCHIVOS")
    if r:
        return Path(r)
    return Path("/datos/archivos") if Path("/datos").is_dir() else RAIZ / "datos" / "archivos"


def ruta_segura(relativa: Any, base: Optional[Path] = None) -> Path:
    """Una ruta DENTRO del espacio de archivos; ErrorDeNegocio si intenta salir (.., absolutas, enlaces)."""
    base = (base or ruta_archivos()).resolve()
    texto = str(relativa or "").strip().replace("\\", "/")
    if not texto:
        raise ErrorDeNegocio("falta la ruta del archivo")
    if "\x00" in texto or texto.startswith("/") or re.match(r"^[A-Za-z]:", texto) or texto.startswith("~"):
        raise ErrorDeNegocio(f"la ruta «{texto[:120]}» tiene que ser relativa al espacio de archivos")
    partes = [p for p in texto.split("/") if p not in ("", ".")]
    if any(p == ".." for p in partes):
        raise ErrorDeNegocio(f"la ruta «{texto[:120]}» no puede salir del espacio de archivos (..)")
    destino = base.joinpath(*partes).resolve() if partes else base
    if destino != base and base not in destino.parents:
        raise ErrorDeNegocio(f"la ruta «{texto[:120]}» sale del espacio de archivos")
    return destino


# ------------------------------------------------------------ destinos vetados

def _valores_de_entorno_vetados() -> Iterable[str]:
    for k, v in os.environ.items():
        if k.startswith(PREFIJOS_ENTORNO_VETADOS) and v and len(v) < 400:
            yield v.strip()


def _host_de(valor: str) -> str:
    v = valor.strip().lower()
    if "://" in v:
        return (urllib.parse.urlsplit(v).hostname or "").rstrip(".")
    if "@" in v:
        return v.rsplit("@", 1)[1].rstrip(".")
    return v.split("/")[0].split(":")[0].rstrip(".")


def hosts_vetados_de_entorno() -> Set[str]:
    r = set()
    for v in _valores_de_entorno_vetados():
        h = _host_de(v)
        if "." in h and re.fullmatch(r"[a-z0-9.-]+", h):
            r.add(h)
    return r


def host_vetado(host: str) -> Optional[str]:
    """Por qué un host no se toca nunca (bloqueo duro), o None."""
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return "no hay host"
    for d in DOMINIOS_VETADOS:
        if h == d or h.endswith("." + d):
            return f"{h} es de Positiva ({d}): esta plataforma nunca toca sus sistemas"
    if "sgdea" in h:
        return f"{h} es un host de SGDEA: esta plataforma nunca toca SGDEA"
    if h in HOSTS_METADATOS or h.endswith(".internal"):
        return f"{h} es un servicio de metadatos o interno"
    if h in hosts_vetados_de_entorno():
        return f"{h} lo usan los jobs de PQRD o tutelas"
    return None


def buzones_protegidos() -> Set[str]:
    r = set(BUZONES_PROTEGIDOS)
    for v in _valores_de_entorno_vetados():
        for m in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", v):
            r.add(m.lower())
    return r


def direccion_vetada(direccion: str) -> Optional[str]:
    """Por qué no se puede leer ni escribir a una dirección de correo (bloqueo duro), o None."""
    d = (direccion or "").strip().lower()
    m = re.search(r"<([^>]+)>", d)
    if m:
        d = m.group(1).strip()
    if not d:
        return None
    if d in buzones_protegidos():
        return f"{d} es un buzón de los jobs de PQRD o tutelas: solo se lee por el buzón de solo lectura de la plataforma"
    dominio = d.rsplit("@", 1)[-1]
    for v in DOMINIOS_VETADOS:
        if dominio == v or dominio.endswith("." + v):
            return f"{d} es una dirección de Positiva: esta plataforma no lee ni escribe en sus buzones"
    return None


def direcciones(texto: Any) -> List[str]:
    """Las direcciones de un campo «para» (separadas por coma o punto y coma)."""
    if isinstance(texto, list):
        partes = [str(x) for x in texto]
    else:
        partes = re.split(r"[;,]", str(texto or ""))
    r = []
    for p in partes:
        p = p.strip()
        m = re.search(r"<([^>]+)>", p)
        p = (m.group(1) if m else p).strip()
        if p:
            r.append(p)
    return r


# ------------------------------------------------------------------- red

def ip_bloqueada(ip: str, red_privada: bool = False) -> Optional[str]:
    """Por qué no se puede llamar a una IP (SSRF), o None si es pública.

    Con `red_privada` (conexiones a bases de datos y servidores de correo que una
    persona configuró en una credencial) se aceptan las IP privadas (10/8, 172.16/12,
    192.168/16), donde suelen vivir esos servidores; siguen vetados loopback, enlace
    local, metadatos, multicast y reservadas. El nodo http genérico nunca la usa."""
    if ip in IPS_PERMITIDAS_PRUEBAS:
        return None
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return "no es una IP válida"
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    if str(a) in HOSTS_METADATOS:
        return "es un servicio de metadatos"
    for prop, motivo in (("is_loopback", "es de loopback"), ("is_link_local", "es de enlace local"),
                         ("is_multicast", "es multicast"), ("is_unspecified", "no está especificada"), ("is_reserved", "es reservada")):
        if getattr(a, prop):
            return motivo
    if a.is_private:
        return None if red_privada and isinstance(a, ipaddress.IPv4Address) else "es privada"
    if not a.is_global:
        return "no es pública"
    return None


def destino_seguro(host: str, puerto: int, red_privada: bool = False) -> str:
    """Resuelve el host y devuelve la IP a la que conectar; ErrorDeNegocio si es vetado o alguna IP es interna."""
    h = (host or "").lower().rstrip(".")
    if h in HOSTS_METADATOS or h.endswith(".internal") or h == "localhost" or h.endswith(".localhost"):
        raise ErrorDeNegocio(f"bloqueado por seguridad: {host} es un destino interno")
    vetado = host_vetado(h)
    if vetado:
        raise ErrorDeNegocio(f"bloqueado por seguridad: {vetado}")
    try:
        infos = socket.getaddrinfo(h, puerto, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        if e.errno == getattr(socket, "EAI_AGAIN", object()):
            raise ErrorTransitorio(f"no se pudo resolver {host} por ahora") from e
        raise ErrorDeNegocio(f"no se pudo resolver {host}") from e
    ips = sorted({str(i[4][0]) for i in infos})
    if not ips:
        raise ErrorDeNegocio(f"no se pudo resolver {host}")
    for ip in ips:
        motivo = ip_bloqueada(ip, red_privada)
        if motivo:
            raise ErrorDeNegocio(f"bloqueado por seguridad: {host} apunta a {ip}, que {motivo}")
    return ips[0]


# --------------------------------------------------------------- utilidades

def entero(x: Any, defecto: int, minimo: int, maximo: int) -> int:
    try:
        v = int(x)
    except (TypeError, ValueError):
        return defecto
    return max(minimo, min(maximo, v))


def timeout_de(c: Dict[str, Any]) -> float:
    """El tiempo de espera de un nodo (timeout_s, por defecto 20 s, tope 60 s)."""
    try:
        v = float(c.get("timeout_s") or TIMEOUT_S)
    except (TypeError, ValueError):
        v = TIMEOUT_S
    return max(1.0, min(TIMEOUT_MAX_S, v))


def a_json(v: Any) -> Any:
    """Un valor de una base de datos o de una API, convertido a algo que JSON entiende."""
    import base64
    import datetime
    import decimal
    import uuid

    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2 ** 53 else float(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, datetime.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return "base64:" + base64.b64encode(b[:100_000]).decode()
    if isinstance(v, dict):
        return {str(k): a_json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [a_json(x) for x in v]
    return str(v)
