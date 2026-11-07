"""
Almacenamiento (plan 5d, fase 2): FTP/FTPS con ftplib (biblioteca estándar) y
almacenamiento compatible con S3 (Amazon S3, MinIO, Google Cloud Storage por
interoperabilidad con llaves HMAC) firmado con AWS Signature V4 a mano sobre el
cliente HTTP de los conectores.

¿Por qué no boto3 (Apache-2.0, sí se podía)? Porque resuelve el DNS por su
cuenta: no deja conectar a la IP que ya revisó `destino_seguro` (rebinding de
DNS) y pesa unos 90 MB con botocore. Firmar SigV4 son 40 líneas y así todo sale
por `rest.pedir`, con el mismo bloqueo SSRF que los demás conectores.

SFTP NO está: las librerías de SSH para Python son LGPL (paramiko, ssh2-python)
o EPL/GPL (asyncssh); ver docs/PLAN.md.

Reglas: el destino se revisa con `destino_seguro` (IP privadas solo con
red_privada en una conexión que creó una persona); las rutas remotas no pueden
salir de la carpeta o prefijo base de la conexión (nada de «..»); solo modo
pasivo en FTP y los datos van a la MISMA IP del control (ftplib ignora la IP que
dice el servidor en PASV); tope de MAX_BYTES por archivo.
"""
from __future__ import annotations

import datetime
import ftplib
import hashlib
import hmac
import posixpath
import re
import socket
import ssl
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import rest
from .base import MAX_BYTES_ARCHIVO, ErrorDeNegocio, ErrorTransitorio, destino_seguro

MAX_BYTES = MAX_BYTES_ARCHIVO
ahora: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc)  # noqa: E731  (las pruebas lo fijan)


def ruta_remota(base: str, relativa: str, carpeta: bool = False) -> str:
    """Una ruta dentro de la carpeta base de la conexión (sin «..»); ErrorDeNegocio si intenta salir."""
    texto = str(relativa or "").strip().replace("\\", "/")
    if "\x00" in texto or any(c in texto for c in "\r\n"):
        raise ErrorDeNegocio("la ruta remota tiene caracteres de control")
    partes = [p for p in texto.split("/") if p not in ("", ".")]
    if any(p == ".." for p in partes):
        raise ErrorDeNegocio(f"la ruta «{texto[:120]}» no puede salir de la carpeta base de la conexión (..)")
    b = "/" + "/".join(p for p in str(base or "").replace("\\", "/").split("/") if p not in ("", "."))
    r = posixpath.join(b, *partes) if partes else b
    if carpeta and r != "/":
        return r.rstrip("/") + "/"
    return r


def nombre_libre(existentes: List[str], nombre: str) -> str:
    """«informe.pdf» → «informe (1).pdf» si ya está."""
    if nombre not in existentes:
        return nombre
    raiz, ext = posixpath.splitext(nombre)
    i = 1
    while f"{raiz} ({i}){ext}" in existentes:
        i += 1
    return f"{raiz} ({i}){ext}"


# =====================================================================
#   FTP / FTPS
# =====================================================================

class _Conectado:
    """Conecta el canal de control a la IP ya revisada (el nombre queda para TLS/SNI)."""

    def conectar_a(self, host: str, ip: str, puerto: int, timeout: float) -> str:
        self.host, self.port, self.timeout = host, puerto, timeout  # type: ignore[attr-defined]
        self.sock = socket.create_connection((ip, puerto), timeout)  # type: ignore[attr-defined]
        self.af = self.sock.family  # type: ignore[attr-defined]
        self.file = self.sock.makefile("r", encoding=self.encoding)  # type: ignore[attr-defined]
        self.welcome = self.getresp()  # type: ignore[attr-defined]
        return self.welcome  # type: ignore[attr-defined]


class _FTP(_Conectado, ftplib.FTP):
    pass


class _FTPS(_Conectado, ftplib.FTP_TLS):
    pass


class Ftp:
    def __init__(self, valores: dict, red_privada: bool, timeout: float) -> None:
        self.v = valores
        self.host = str(valores.get("host") or "").strip().lower()
        self.puerto = int(valores.get("puerto") or 21)
        self.tls = str(valores.get("tls") or "explicito")
        self.base = str(valores.get("carpeta_base") or "/")
        self.red_privada = red_privada
        self.timeout = timeout
        self.c: Optional[ftplib.FTP] = None

    def __enter__(self) -> "Ftp":
        ip = destino_seguro(self.host, self.puerto, red_privada=self.red_privada)
        try:
            if self.tls == "ninguno":
                c: ftplib.FTP = _FTP(timeout=self.timeout, encoding="utf-8")
            else:
                c = _FTPS(timeout=self.timeout, context=ssl.create_default_context(), encoding="utf-8")
            c.trust_server_pasv_ipv4_address = False  # los datos van a la IP del control, nunca a la que dice el servidor
            c.conectar_a(self.host, ip, self.puerto, self.timeout)  # type: ignore[attr-defined]
            c.login(str(self.v.get("usuario") or "anonymous"), str(self.v.get("clave") or ""))
            if isinstance(c, ftplib.FTP_TLS):
                c.prot_p()
            c.set_pasv(True)
        except ftplib.error_perm as e:
            raise ErrorDeNegocio(f"el servidor FTP rechazó la conexión: {str(e)[:200]}") from e
        except (socket.timeout, TimeoutError) as e:
            raise ErrorTransitorio(f"el servidor FTP {self.host} no respondió a tiempo") from e
        except ssl.SSLError as e:
            raise ErrorDeNegocio(f"FTPS con {self.host} falló ({e.reason or e}); si el servidor no tiene TLS pon tls «ninguno»") from e
        except (ConnectionError, OSError, EOFError, ftplib.Error) as e:
            raise ErrorTransitorio(f"no se pudo conectar con el FTP {self.host}: {type(e).__name__}") from e
        self.c = c
        return self

    def __exit__(self, *a: Any) -> None:
        try:
            if self.c:
                self.c.quit()
        except Exception:
            try:
                self.c.close()  # type: ignore[union-attr]
            except Exception:
                pass

    def _envolver(self, fn: Callable[[], Any], que: str) -> Any:
        try:
            return fn()
        except ftplib.error_perm as e:
            raise ErrorDeNegocio(f"FTP: {que}: {str(e)[:200]}") from e
        except (socket.timeout, TimeoutError) as e:
            raise ErrorTransitorio(f"FTP: {que}: tiempo agotado") from e
        except (ConnectionError, OSError, EOFError, ftplib.error_temp, ftplib.error_reply) as e:
            raise ErrorTransitorio(f"FTP: {que}: {type(e).__name__}") from e

    def listar(self, carpeta: str, maximo: int = 100) -> List[dict]:
        ruta = ruta_remota(self.base, carpeta)
        c = self.c
        assert c

        def hacer() -> List[dict]:
            r: List[dict] = []
            try:
                for nombre, hechos in c.mlsd(ruta, facts=["type", "size", "modify"]):
                    if nombre in (".", "..") or hechos.get("type") in ("cdir", "pdir"):
                        continue
                    mod = hechos.get("modify", "")
                    r.append({"nombre": nombre, "ruta": posixpath.join(ruta, nombre), "tipo": "carpeta" if hechos.get("type") == "dir" else "archivo",
                              "tamano": int(hechos["size"]) if str(hechos.get("size", "")).isdigit() else None,
                              "modificado": f"{mod[0:4]}-{mod[4:6]}-{mod[6:8]}T{mod[8:10]}:{mod[10:12]}:{mod[12:14]}Z" if len(mod) >= 14 else ""})
                    if len(r) >= maximo:
                        break
            except ftplib.error_perm as e:
                if not str(e).startswith("500") and not str(e).startswith("502"):
                    raise
                for x in c.nlst(ruta)[:maximo]:  # sin MLSD: solo los nombres
                    nombre = posixpath.basename(x.rstrip("/"))
                    if nombre not in (".", ".."):
                        r.append({"nombre": nombre, "ruta": posixpath.join(ruta, nombre), "tipo": "", "tamano": None, "modificado": ""})
            return r

        return self._envolver(hacer, f"listar {ruta}")

    def leer(self, archivo: str, max_bytes: int = MAX_BYTES) -> Tuple[str, bytes]:
        ruta = ruta_remota(self.base, archivo)
        c = self.c
        assert c
        trozos: List[bytes] = []
        total = [0]

        def guardar(b: bytes) -> None:
            total[0] += len(b)
            if total[0] > max_bytes:
                raise ErrorDeNegocio(f"«{posixpath.basename(ruta)}» pasa de {max_bytes // 1_000_000} MB")
            trozos.append(b)

        self._envolver(lambda: c.retrbinary(f"RETR {ruta}", guardar), f"leer {ruta}")
        return ruta, b"".join(trozos)

    def subir(self, carpeta: str, nombre: str, datos: bytes, conflicto: str = "renombrar") -> dict:
        import io
        dir_ = ruta_remota(self.base, carpeta)
        c = self.c
        assert c
        existentes = [posixpath.basename(x.rstrip("/")) for x in self._envolver(lambda: _nlst(c, dir_), f"listar {dir_}")]
        if nombre in existentes:
            if conflicto == "fallar":
                raise ErrorDeNegocio(f"ya existe {posixpath.join(dir_, nombre)} (conflicto: fallar)")
            if conflicto != "reemplazar":
                nombre = nombre_libre(existentes, nombre)
        ruta = posixpath.join(dir_, nombre)
        self._envolver(lambda: c.storbinary(f"STOR {ruta}", io.BytesIO(datos)), f"subir {ruta}")
        return {"ruta": ruta, "nombre": nombre, "bytes": len(datos)}

    def probar(self) -> str:
        c = self.c
        assert c
        return self._envolver(lambda: c.pwd(), "pwd")


def _nlst(c: ftplib.FTP, ruta: str) -> List[str]:
    try:
        return c.nlst(ruta)
    except ftplib.error_perm as e:
        if str(e).startswith("550"):  # carpeta vacía o inexistente en algunos servidores
            return []
        raise


# =====================================================================
#   S3 (y compatibles): firma AWS Signature V4
# =====================================================================

VACIO = hashlib.sha256(b"").hexdigest()


def _hmac(k: bytes, m: str) -> bytes:
    return hmac.new(k, m.encode("utf-8"), hashlib.sha256).digest()


def _q(x: str, seguro: str = "-_.~") -> str:
    return urllib.parse.quote(x, safe=seguro)


def firmar(metodo: str, host: str, ruta: str, consulta: Dict[str, str], encabezados: Dict[str, str], hash_cuerpo: str,
           region: str, llave: str, secreto: str, momento: datetime.datetime, servicio: str = "s3") -> Dict[str, str]:
    """Los encabezados firmados (Authorization, x-amz-date, x-amz-content-sha256, host) de una petición SigV4."""
    amz = momento.strftime("%Y%m%dT%H%M%SZ")
    dia = amz[:8]
    cab = {k.lower().strip(): " ".join(str(v).strip().split()) for k, v in encabezados.items()}
    cab.update({"host": host, "x-amz-date": amz, "x-amz-content-sha256": hash_cuerpo})
    firmados = ";".join(sorted(cab))
    canon_cab = "".join(f"{k}:{cab[k]}\n" for k in sorted(cab))
    canon_q = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(consulta.items()))
    canonica = "\n".join([metodo.upper(), _q(ruta, "/-_.~"), canon_q, canon_cab, firmados, hash_cuerpo])
    alcance = f"{dia}/{region}/{servicio}/aws4_request"
    a_firmar = "\n".join(["AWS4-HMAC-SHA256", amz, alcance, hashlib.sha256(canonica.encode("utf-8")).hexdigest()])
    k = _hmac(_hmac(_hmac(_hmac(("AWS4" + secreto).encode("utf-8"), dia), region), servicio), "aws4_request")
    firma = hmac.new(k, a_firmar.encode("utf-8"), hashlib.sha256).hexdigest()
    cab["authorization"] = f"AWS4-HMAC-SHA256 Credential={llave}/{alcance}, SignedHeaders={firmados}, Signature={firma}"
    return cab


class S3:
    def __init__(self, valores: dict, red_privada: bool, timeout: float) -> None:
        self.v = valores
        self.region = str(valores.get("region") or "us-east-1").strip()
        ep = str(valores.get("endpoint") or "").strip().rstrip("/") or (
            "https://s3.amazonaws.com" if self.region == "us-east-1" else f"https://s3.{self.region}.amazonaws.com")
        self.u = rest.revisar_url(ep)
        self.bucket = str(valores.get("bucket") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", self.bucket):
            raise ErrorDeNegocio("la conexión S3 necesita bucket (minúsculas, números, punto y guion; 3 a 63)")
        self.ruta_estilo = str(valores.get("estilo") or "virtual") == "ruta" or "." in self.bucket
        self.prefijo = str(valores.get("prefijo") or "").strip().strip("/")
        self.red_privada, self.timeout = red_privada, timeout

    def clave(self, relativa: str, carpeta: bool = False) -> str:
        r = ruta_remota(self.prefijo, relativa, carpeta).lstrip("/")
        return r

    def _pedir(self, metodo: str, clave: str = "", consulta: Optional[Dict[str, str]] = None, cuerpo: bytes = b"",
               encabezados: Optional[Dict[str, str]] = None, max_bytes: int = MAX_BYTES + 1_000_000) -> rest.Respuesta:
        consulta = consulta or {}
        puerto = self.u.port
        base_host = (self.u.hostname or "").lower()
        if self.ruta_estilo:
            host, ruta = base_host, f"/{self.bucket}" + (f"/{clave}" if clave else "/")
        else:
            host, ruta = f"{self.bucket}.{base_host}", f"/{clave}"
        netloc = host + (f":{puerto}" if puerto else "")
        cab = firmar(metodo, netloc, ruta, consulta, encabezados or {}, hashlib.sha256(cuerpo).hexdigest(), self.region,
                     str(self.v.get("access_key") or ""), str(self.v.get("secret_key") or ""), ahora())
        url = f"{self.u.scheme}://{netloc}{_q(ruta, '/-_.~')}" + ("?" + "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(consulta.items())) if consulta else "")
        cab_http = {("Host" if k == "host" else k): v for k, v in cab.items()}
        r = rest.pedir(metodo, url, data=cuerpo if metodo in ("PUT", "POST") else None, headers=cab_http, timeout=self.timeout,
                       red_privada=self.red_privada, servicio="el almacenamiento S3", errores=False, max_bytes=max_bytes)
        if r.status in (408, 425, 429) or r.status >= 500:
            raise ErrorTransitorio(f"el almacenamiento S3 respondió {r.status}")
        if r.status >= 300:
            m = re.search(r"<Code>([^<]+)</Code>.*?(?:<Message>([^<]*)</Message>)?", r.text, re.S)
            detalle = f"{m.group(1)}: {m.group(2) or ''}".strip(": ") if m else r.text[:200]
            if r.status == 404:
                raise ErrorDeNegocio(f"S3: no existe {clave or self.bucket} ({detalle})")
            raise ErrorDeNegocio(f"S3 respondió {r.status}: {detalle[:300]}")
        return r

    def listar(self, carpeta: str, maximo: int = 100, recursivo: bool = False) -> List[dict]:
        pref = self.clave(carpeta, carpeta=True) if str(carpeta or "").strip(" /") or self.prefijo else ""
        q = {"list-type": "2", "max-keys": str(max(1, min(1000, maximo)))}
        if pref:
            q["prefix"] = pref
        if not recursivo:
            q["delimiter"] = "/"
        r = self._pedir("GET", "", q)
        raiz = ET.fromstring(r.content)
        ns = raiz.tag.split("}")[0] + "}" if raiz.tag.startswith("{") else ""
        items: List[dict] = []
        for p in raiz.findall(f"{ns}CommonPrefixes"):
            k = (p.findtext(f"{ns}Prefix") or "")
            items.append({"nombre": k.rstrip("/").rsplit("/", 1)[-1], "clave": k, "tipo": "carpeta", "tamano": None, "modificado": ""})
        for o in raiz.findall(f"{ns}Contents"):
            k = o.findtext(f"{ns}Key") or ""
            if k == pref:
                continue
            items.append({"nombre": k.rsplit("/", 1)[-1], "clave": k, "tipo": "archivo", "tamano": int(o.findtext(f"{ns}Size") or 0),
                          "modificado": o.findtext(f"{ns}LastModified") or "", "etag": (o.findtext(f"{ns}ETag") or "").strip('"')})
        return items[:maximo]

    def leer(self, archivo: str, max_bytes: int = MAX_BYTES) -> Tuple[str, bytes, str]:
        k = self.clave(archivo)
        r = self._pedir("GET", k, max_bytes=max_bytes + 1)
        if len(r.content) > max_bytes:
            raise ErrorDeNegocio(f"«{k}» pasa de {max_bytes // 1_000_000} MB")
        return k, r.content, r.encabezados.get("content-type", "")

    def existe(self, k: str) -> bool:
        try:
            self._pedir("HEAD", k)
            return True
        except ErrorDeNegocio as e:
            if "no existe" in str(e) or "404" in str(e):
                return False
            raise

    def subir(self, carpeta: str, nombre: str, datos: bytes, tipo: str, conflicto: str = "renombrar") -> dict:
        k = self.clave(posixpath.join(str(carpeta or ""), nombre))
        if conflicto != "reemplazar" and self.existe(k):
            if conflicto == "fallar":
                raise ErrorDeNegocio(f"ya existe {k} (conflicto: fallar)")
            raiz, ext = posixpath.splitext(k)
            i = 1
            while self.existe(f"{raiz} ({i}){ext}"):
                i += 1
            k = f"{raiz} ({i}){ext}"
        r = self._pedir("PUT", k, cuerpo=datos, encabezados={"content-type": tipo or "application/octet-stream"})
        return {"clave": k, "nombre": k.rsplit("/", 1)[-1], "bytes": len(datos), "etag": r.encabezados.get("etag", "").strip('"')}

    def probar(self) -> int:
        return len(self.listar("", 1))
