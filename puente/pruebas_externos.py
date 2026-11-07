"""
Pruebas de la fase 2 de integraciones (plan 5d) con dobles locales, sin cuentas
reales ni salida a internet:

- un http.server en 127.0.0.1 hace de sitio web (robots.txt, páginas, feeds RSS
  y Atom), de bucket S3 (estilo ruta), de BigQuery, de Firestore y de las API de
  Telegram, SendGrid, Mailgun y Twilio;
- un servidor FTP mínimo (socketserver) con modo pasivo;
- MongoDB, SQL Server y Oracle con dobles del controlador (no hay servidores de
  verdad que levantar aquí): se prueba lo nuestro (solo lectura, filtros,
  retenciones, bloqueos), no el controlador.

    PYTHONPATH=motor python puente/pruebas_externos.py        (desde la raíz; en el contenedor: /app)
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
os.environ["CACHE_CONVERSION"] = "no"

import flujo_declarativo as fd  # noqa: E402
import nodos_declarativos as nd  # noqa: E402
import nodos_externos as nx  # noqa: E402
import nodos_integraciones as ni  # noqa: E402
from conectores import almacen as c_almacen  # noqa: E402
from conectores import datos as c_datos  # noqa: E402
from conectores import oauth as c_oauth  # noqa: E402
from conectores import sql as c_sql  # noqa: E402
from conectores import transaccional as c_tx  # noqa: E402
from conectores import web as c_web  # noqa: E402
from pruebas_declarativo import Plataforma, cred, flujo, nodo  # noqa: E402
from pruebas_integraciones import oauth_g  # noqa: E402

TOKEN_TG = "123456789:AAEhBP0av28M0Q_secreto-telegram-XYZ123"
SID = "AC" + "0123456789abcdef" * 2
PAGINA = """<!doctype html><html lang="es"><head><title>Tarifas 2026</title>
<meta name="description" content="Las tarifas del año"><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head>
<body><nav><a href="/inicio">Inicio</a> menú que no va</nav><script>var x = "no va";</script>
<main><h1>Tarifas</h1><p>Texto <b>principal</b> de la página.</p>
<table><caption>Tabla A</caption><tr><th>Plan</th><th>Valor</th></tr><tr><td>Básico</td><td>10</td></tr><tr><td>Plus</td><td>20</td></tr></table>
<article class="nota"><h2><a href="/n/1">Primera nota</a></h2></article><article class="nota"><h2><a href="https://otro.example/n/2">Segunda</a></h2></article>
</main><footer>pie que no va</footer></body></html>"""


def _rss(items):
    cuerpo = "".join(f"<item><title>{t}</title><link>https://www.ejemplo.test/n/{g}</link><guid>{g}</guid><pubDate>{f}</pubDate>"
                     f"<description>&lt;p&gt;Resumen de {t}&lt;/p&gt;</description></item>" for g, t, f in items)
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Noticias</title><link>https://www.ejemplo.test</link>'
            f"<description>Del sector</description>{cuerpo}</channel></rss>").encode()


ATOM = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title><link href="https://blog.test/"/>
<entry><id>urn:uuid:1</id><title>Hola Atom</title><link rel="alternate" href="/post/1"/><updated>2026-11-05T10:00:00Z</updated>
<author><name>Ana</name></author><summary>Primer post</summary></entry></feed>"""


class Estado:
    def __init__(self) -> None:
        self.llamadas: list = []
        self.items = [("n2", "Circular de riesgos", "Fri, 06 Nov 2026 10:00:00 GMT"), ("n1", "Noticia vieja", "Thu, 05 Nov 2026 10:00:00 GMT")]
        self.objetos = {"facturas/f-1.txt": b"factura uno", "facturas/2026/f-2.pdf": b"%PDF-1.4 dos"}
        self.docs_fs = {"clientes/ana": {"nombre": {"stringValue": "Ana"}, "edad": {"integerValue": "40"}}}
        self.enviados: list = []


class Doble(BaseHTTPRequestHandler):
    E = Estado()

    def log_message(self, *a) -> None:
        pass

    def _resp(self, codigo, cuerpo=b"", tipo="text/html; charset=utf-8", extra=None):
        if isinstance(cuerpo, (dict, list)):
            cuerpo, tipo = json.dumps(cuerpo).encode(), "application/json"
        elif isinstance(cuerpo, str):
            cuerpo = cuerpo.encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(cuerpo)

    def do_GET(self):
        self._todo("GET")

    def do_POST(self):
        self._todo("POST")

    def do_PUT(self):
        self._todo("PUT")

    def do_PATCH(self):
        self._todo("PATCH")

    def do_HEAD(self):
        self._todo("HEAD")

    def _todo(self, metodo):
        E = self.E
        u = urllib.parse.urlsplit(self.path)
        ruta, q = urllib.parse.unquote(u.path), dict(urllib.parse.parse_qsl(u.query))
        n = int(self.headers.get("Content-Length") or 0)
        cuerpo = self.rfile.read(n) if n else b""
        E.llamadas.append({"metodo": metodo, "ruta": ruta, "q": q, "cuerpo": cuerpo, "h": {k.lower(): v for k, v in self.headers.items()}})
        j = json.loads(cuerpo) if cuerpo[:1] in (b"{", b"[") else {}
        # ------------------------------------------------------------ sitio web
        if ruta == "/robots.txt":
            return self._resp(200, "User-agent: *\nDisallow: /privado\n", "text/plain")
        if ruta == "/pagina":
            return self._resp(200, PAGINA)
        if ruta.startswith("/privado"):
            return self._resp(200, "<p>secreto</p>")
        if ruta == "/redirige":
            return self._resp(302, "", extra={"Location": "/pagina"})
        if ruta == "/grande":
            return self._resp(200, "<p>" + "x" * 5000 + "</p>")
        if ruta == "/imagen.png":
            return self._resp(200, b"\x89PNG", "image/png")
        if ruta == "/feed.xml":
            etag = '"v%d"' % len(E.items)
            if self.headers.get("If-None-Match") == etag:
                return self._resp(304)
            return self._resp(200, _rss(E.items), "application/rss+xml", {"ETag": etag})
        if ruta == "/atom.xml":
            return self._resp(200, ATOM, "application/atom+xml")
        if ruta == "/malo.xml":
            return self._resp(200, b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><title>&a;</title></channel></rss>', "application/xml")
        # -------------------------------------------------------------- S3 (ruta)
        if ruta.startswith("/cubeta-pruebas"):
            return self._s3(metodo, ruta[len("/cubeta-pruebas"):].lstrip("/"), q, cuerpo)
        # ------------------------------------------------------------ BigQuery
        if ruta.startswith("/bigquery/v2/projects/analitica-123/jobs"):
            sql = j["configuration"]["query"]["query"]
            tipo = sql.split()[0].upper()
            return self._resp(200, {"statistics": {"totalBytesProcessed": "2048", "query": {"statementType": "SCRIPT" if ";" in sql else tipo}}})
        if ruta.startswith("/bigquery/v2/projects/analitica-123/queries"):
            if j["query"].upper().startswith("SELECT"):
                return self._resp(200, {"jobComplete": True, "totalRows": "2", "schema": {"fields": [{"name": "region", "type": "STRING"}, {"name": "total", "type": "INTEGER"},
                                                                                                         {"name": "activo", "type": "BOOLEAN"}]},
                                        "rows": [{"f": [{"v": "Norte"}, {"v": "15"}, {"v": "true"}]}, {"f": [{"v": "Sur"}, {"v": "7"}, {"v": "false"}]}]})
            return self._resp(200, {"jobComplete": True, "numDmlAffectedRows": "3", "jobReference": {"jobId": "job-1"}})
        # ----------------------------------------------------------- Firestore
        if ruta.startswith("/firestore/v1/projects/app-movil-123/databases/(default)/documents"):
            r = ruta[len("/firestore/v1/projects/app-movil-123/databases/(default)/documents"):].lstrip("/")
            nombre = lambda p: f"projects/app-movil-123/databases/(default)/documents/{p}"  # noqa: E731
            if r.endswith(":runQuery") or ruta.endswith(":runQuery"):
                return self._resp(200, [{"document": {"name": nombre(k), "fields": v}} for k, v in E.docs_fs.items()] + [{"readTime": "x"}])
            if metodo == "GET" and r in E.docs_fs:
                return self._resp(200, {"name": nombre(r), "fields": E.docs_fs[r]})
            if metodo == "GET" and "/" not in r:
                return self._resp(200, {"documents": [{"name": nombre(k), "fields": v} for k, v in E.docs_fs.items() if k.startswith(r + "/")]})
            if metodo in ("PATCH", "POST"):
                clave = r if metodo == "PATCH" else f"{r}/nuevo1"
                E.docs_fs[clave] = j["fields"]
                E.enviados.append({"firestore": clave, "q": q, "campos": j["fields"]})
                return self._resp(200, {"name": nombre(clave), "fields": j["fields"]})
            return self._resp(404, {"error": {"message": "no existe"}})
        # ------------------------------------------------------------ Telegram
        if ruta.startswith(f"/telegram/bot{TOKEN_TG}/"):
            m = ruta.rsplit("/", 1)[-1]
            if m == "getMe":
                return self._resp(200, {"ok": True, "result": {"username": "avisos_bot"}})
            E.enviados.append({"telegram": m, "cuerpo": j or cuerpo[:300]})
            return self._resp(200, {"ok": True, "result": {"message_id": len(E.enviados)}})
        if ruta.startswith("/telegram/"):
            return self._resp(401, {"ok": False, "description": "Unauthorized"})
        # ----------------------------------------------------- SendGrid y Mailgun
        if ruta == "/sendgrid/v3/mail/send":
            E.enviados.append({"sendgrid": j, "auth": self.headers.get("Authorization")})
            return self._resp(202, b"", "text/plain", {"X-Message-Id": "sg-1"})
        if ruta.startswith("/mailgun/v3/mg.empresa.test/messages"):
            E.enviados.append({"mailgun": cuerpo, "auth": self.headers.get("Authorization")})
            return self._resp(200, {"id": "<mg-1@mg.empresa.test>", "message": "Queued. Thank you."})
        # -------------------------------------------------------------- Twilio
        if ruta == f"/twilio/2010-04-01/Accounts/{SID}/Messages.json":
            E.enviados.append({"twilio": dict(urllib.parse.parse_qsl(cuerpo.decode())), "auth": self.headers.get("Authorization")})
            return self._resp(201, {"sid": "SM1", "status": "queued", "num_segments": "1"})
        return self._resp(404, {"error": {"message": f"no existe {ruta}"}})

    def _s3(self, metodo, clave, q, cuerpo):
        E = self.E
        h = {k.lower(): v for k, v in self.headers.items()}
        auth = h.get("authorization", "")
        if not auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDPRUEBA/"):
            return self._resp(403, "<Error><Code>AccessDenied</Code><Message>sin firma</Message></Error>", "application/xml")
        # la firma se rehace con lo que llegó: si el cliente firmó otra ruta u otro host, no coincide
        firmados = auth.split("SignedHeaders=")[1].split(",")[0].split(";")
        momento = datetime.datetime.strptime(h["x-amz-date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
        otra = c_almacen.firmar(metodo, h["host"], urllib.parse.urlsplit(self.path).path and urllib.parse.unquote(urllib.parse.urlsplit(self.path).path), q,
                                {k: h[k] for k in firmados if k not in ("host", "x-amz-date", "x-amz-content-sha256")}, h["x-amz-content-sha256"],
                                "us-east-1", "AKIDPRUEBA", "secreto-S3-ABC", momento)
        if otra["authorization"] != auth:
            return self._resp(403, "<Error><Code>SignatureDoesNotMatch</Code><Message>firma</Message></Error>", "application/xml")
        if metodo == "GET" and not clave:
            pref, delim = q.get("prefix", ""), q.get("delimiter")
            cont, comunes = [], set()
            for k, v in sorted(E.objetos.items()):
                if not k.startswith(pref):
                    continue
                resto = k[len(pref):]
                if delim and delim in resto:
                    comunes.add(pref + resto.split(delim)[0] + delim)
                else:
                    cont.append(f"<Contents><Key>{k}</Key><Size>{len(v)}</Size><LastModified>2026-11-01T00:00:00Z</LastModified><ETag>\"e\"</ETag></Contents>")
            xml = ('<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">' + "".join(cont)
                   + "".join(f"<CommonPrefixes><Prefix>{p}</Prefix></CommonPrefixes>" for p in sorted(comunes)) + "</ListBucketResult>")
            return self._resp(200, xml, "application/xml")
        if metodo in ("GET", "HEAD"):
            if clave not in E.objetos:
                return self._resp(404, "" if metodo == "HEAD" else "<Error><Code>NoSuchKey</Code><Message>no</Message></Error>", "application/xml")
            return self._resp(200, E.objetos[clave], "text/plain" if clave.endswith(".txt") else "application/pdf")
        if metodo == "PUT":
            E.objetos[clave] = cuerpo
            E.enviados.append({"s3": clave, "tipo": h.get("content-type")})
            return self._resp(200, b"", "text/plain", {"ETag": '"etag-nuevo"'})
        return self._resp(405)


# ------------------------------------------------------------------ FTP doble

class _ManejadorFtp(socketserver.StreamRequestHandler):
    def _r(self, texto):
        self.wfile.write((texto + "\r\n").encode())

    def _datos(self):
        con, _ = self.pasivo.accept()
        self.pasivo.close()
        return con

    def handle(self):
        S = self.server
        self._r("220 doble FTP")
        while True:
            linea = self.rfile.readline()
            if not linea:
                break
            cmd, _, arg = linea.decode("utf-8").strip().partition(" ")
            cmd = cmd.upper()
            S.comandos.append((cmd, arg))
            if cmd == "USER":
                self._r("331 clave")
            elif cmd == "PASS":
                self._r("230 dentro" if arg == "clave-FTP-777" else "530 no")
            elif cmd == "PWD":
                self._r('257 "/"')
            elif cmd in ("TYPE", "OPTS"):
                self._r("200 ok")
            elif cmd == "PASV":
                self.pasivo = socket.socket()
                self.pasivo.bind(("127.0.0.1", 0))
                self.pasivo.listen(1)
                p = self.pasivo.getsockname()[1]
                self._r(f"227 Entering Passive Mode (10,9,9,9,{p // 256},{p % 256})")  # una IP «mentirosa»: el cliente debe ignorarla
            elif cmd in ("MLSD", "NLST"):
                ruta = arg.rstrip("/") or "/"
                hijos = sorted({k[len(ruta):].lstrip("/").split("/")[0] for k in S.archivos if k.startswith(ruta.rstrip("/") + "/")})
                self._r("150 va")
                con = self._datos()
                for h in hijos:
                    completo = f"{ruta.rstrip('/')}/{h}"
                    if cmd == "MLSD":
                        tipo = "file" if completo in S.archivos else "dir"
                        tam = f"size={len(S.archivos[completo])};" if tipo == "file" else ""
                        con.sendall(f"type={tipo};{tam}modify=20261101120000; {h}\r\n".encode())
                    else:
                        con.sendall(f"{completo}\r\n".encode())
                con.close()
                self._r("226 listo")
            elif cmd == "RETR":
                if arg not in S.archivos:
                    self._r("550 no existe")
                    continue
                self._r("150 va")
                con = self._datos()
                con.sendall(S.archivos[arg])
                con.close()
                self._r("226 listo")
            elif cmd == "STOR":
                self._r("150 manda")
                con = self._datos()
                trozos = []
                while True:
                    b = con.recv(65536)
                    if not b:
                        break
                    trozos.append(b)
                con.close()
                S.archivos[arg] = b"".join(trozos)
                self._r("226 guardado")
            elif cmd == "QUIT":
                self._r("221 adiós")
                break
            else:
                self._r("502 no")


class FtpDoble(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _ManejadorFtp)
        self.archivos = {"/entrantes/novedades.csv": "documento,estado\n111,activo\n".encode(), "/entrantes/viejo.txt": b"viejo"}
        self.comandos: list = []


# --------------------------------------------------------- MongoDB y SQL dobles

class _Res:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Cursor(list):
    def limit(self, n):
        return _Cursor(self[:n])

    def sort(self, orden):
        k, d = orden[0]
        return _Cursor(sorted(self, key=lambda x: x.get(k), reverse=d == -1))


class ColeccionFalsa:
    def __init__(self, docs, registro):
        self.docs, self.registro = docs, registro

    def _filtrar(self, f):
        return [d for d in self.docs if all(d.get(k) == v for k, v in f.items() if not isinstance(v, dict))]

    def find(self, f, proy=None, max_time_ms=None):
        self.registro.append(("find", f, proy, max_time_ms))
        return _Cursor(self._filtrar(f))

    def count_documents(self, f, maxTimeMS=None):
        self.registro.append(("count", f))
        return len(self._filtrar(f))

    def distinct(self, campo, f, maxTimeMS=None):
        return sorted({d.get(campo) for d in self._filtrar(f)})

    def aggregate(self, etapas, maxTimeMS=None):
        self.registro.append(("aggregate", etapas))
        return [{"_id": "Norte", "total": 15}]

    def insert_one(self, d):
        self.registro.append(("insert_one", d))
        return _Res(inserted_id="6563a1b2c3d4e5f601234567")

    def insert_many(self, ds, ordered=True):
        self.registro.append(("insert_many", ds))
        return _Res(inserted_ids=list(range(len(ds))))

    def update_one(self, f, c):
        self.registro.append(("update_one", f, c))
        return _Res(matched_count=1, modified_count=1)

    update_many = update_one

    def delete_one(self, f):
        self.registro.append(("delete_one", f))
        return _Res(deleted_count=1)

    delete_many = delete_one


class ClienteFalso:
    def __init__(self, registro):
        self.registro = registro
        self.admin = _Res(command=lambda c: {"ok": 1})

    def __getitem__(self, base):
        docs = [{"_id": "a1", "estado": "pendiente", "creada": 2}, {"_id": "a2", "estado": "pendiente", "creada": 5}, {"_id": "a3", "estado": "cerrada", "creada": 1}]
        return {"solicitudes": ColeccionFalsa(docs, self.registro)}

    def close(self):
        pass


class CursorSql:
    def __init__(self, registro):
        self.registro, self.description, self.rowcount = registro, None, -1

    def execute(self, sql, params=None):
        self.registro.append((sql, params))
        if "SELECT" in sql.upper() and not sql.upper().startswith("SET"):
            self.description = [("id",), ("nombre",)]
            self.filas = [(1, "Ana"), (2, "Luis"), (3, "Eva")]
        else:
            self.description, self.rowcount = None, 2

    def fetchmany(self, n):
        return self.filas[:n]


class ConexionSql:
    def __init__(self, registro):
        self.registro = registro
        self.call_timeout = 0

    def cursor(self):
        return CursorSql(self.registro)

    def rollback(self):
        self.registro.append(("ROLLBACK", None))

    def commit(self):
        self.registro.append(("COMMIT", None))

    def close(self):
        pass


# =====================================================================

class Externos(Plataforma):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Doble)
        cls.B = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.ftp = FtpDoble()
        threading.Thread(target=cls.ftp.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.ftp.shutdown()
        cls.ftp.server_close()

    def setUp(self):
        super().setUp()
        Doble.E = self.E = Estado()
        fd._IPS_PERMITIDAS_PRUEBAS.add("127.0.0.1")
        self._prev = (dict(c_datos.BASES), dict(c_tx.BASES), c_web.INTERVALO_S, c_web.dormir, c_datos.cliente_mongo, c_sql.conectar_sqlserver,
                      c_sql.conectar_oracle)
        c_web.INTERVALO_S = 0.0
        c_web._robots.clear()
        c_datos.BASES.update({"bigquery": f"{self.B}/bigquery/v2", "firestore": f"{self.B}/firestore/v1"})
        c_tx.BASES.update({k: f"{self.B}/{k.split('_')[0]}" for k in c_tx.BASES})
        c_oauth.olvidar()
        self.mongo: list = []
        c_datos.cliente_mongo = lambda v, t: ClienteFalso(self.mongo)
        self.sql: list = []
        c_sql.conectar_sqlserver = lambda **kw: (self.sql.append(("conectar", kw)), ConexionSql(self.sql))[1]
        c_sql.conectar_oracle = lambda **kw: (self.sql.append(("conectar", kw)), ConexionSql(self.sql))[1]
        puerto = self.ftp.server_address[1]
        self.creds.update({
            "ftp": cred("ftp", False, ["clave"], host="127.0.0.1", puerto=puerto, usuario="ana", clave="clave-FTP-777", tls="ninguno", carpeta_base="/"),
            "ftp-escribe": cred("ftp", True, ["clave"], host="127.0.0.1", puerto=puerto, usuario="ana", clave="clave-FTP-777", tls="ninguno", carpeta_base="/"),
            "s3": cred("s3", False, ["secret_key"], access_key="AKIDPRUEBA", secret_key="secreto-S3-ABC", bucket="cubeta-pruebas", region="us-east-1",
                       endpoint=self.B, estilo="ruta"),
            "s3-escribe": cred("s3", True, ["secret_key"], access_key="AKIDPRUEBA", secret_key="secreto-S3-ABC", bucket="cubeta-pruebas", region="us-east-1",
                               endpoint=self.B, estilo="ruta"),
            "mongo": cred("mongodb", False, ["clave"], host="mongo.empresa.test", base="operaciones", usuario="lector", clave="clave-MONGO-1"),
            "mongo-escribe": cred("mongodb", True, ["clave"], host="mongo.empresa.test", base="operaciones", usuario="escritor", clave="clave-MONGO-2"),
            "gcp": oauth_g(), "gcp-escribe": oauth_g(True),
            "bot": cred("telegram_bot", False, ["token"], token=TOKEN_TG, chat_id="-100123"),
            "bot-escribe": cred("telegram_bot", True, ["token"], token=TOKEN_TG, chat_id="-100123"),
            "sendgrid": cred("sendgrid", True, ["api_key"], api_key="SG.llave-SENDGRID-secreta", remitente="avisos@empresa.test"),
            "mailgun": cred("mailgun", True, ["api_key"], api_key="llave-MAILGUN-secreta", dominio="mg.empresa.test", region="us", remitente="avisos@mg.empresa.test"),
            "twilio": cred("twilio", False, ["auth_token"], account_sid=SID, auth_token="token-TWILIO-secreto", remitente="+15550001111"),
            "twilio-escribe": cred("twilio", True, ["auth_token"], account_sid=SID, auth_token="token-TWILIO-secreto", remitente="+15550001111"),
            "mssql": cred("sqlserver", False, ["clave"], host="sql.empresa.test", puerto=1433, base="ventas", usuario="lector", clave="clave-SQL-1", cifrado=False),
            "oracle": cred("oracle", True, ["clave"], host="ora.empresa.test", puerto=1521, servicio="ORCLPDB1", usuario="app", clave="clave-ORA-1"),
        })
        # los hosts de los dobles de Mongo, SQL Server y Oracle no existen: se «resuelven» a una IP pública de documentación
        self._getaddrinfo = socket.getaddrinfo

        def resolver(host, *a, **k):
            if str(host).endswith("empresa.test"):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", a[0] if a else 0))]
            return self._getaddrinfo(host, *a, **k)
        socket.getaddrinfo = resolver

    def tearDown(self):
        socket.getaddrinfo = self._getaddrinfo
        c_datos.BASES.clear()
        c_datos.BASES.update(self._prev[0])
        c_tx.BASES.clear()
        c_tx.BASES.update(self._prev[1])
        c_web.INTERVALO_S, c_web.dormir, c_datos.cliente_mongo, c_sql.conectar_sqlserver, c_sql.conectar_oracle = self._prev[2:]
        super().tearDown()

    def correr1(self, *nodos, exigir_ok=True, **orden):
        ns = [nodo("inicio", "entrada_manual")] + list(nodos) + [nodo("fin", "salida", desenlace="manual")]
        d = flujo(ns, [{"desde": a["id"], "hacia": b["id"]} for a, b in zip(ns, ns[1:])])
        self.assertEqual(fd.validar(d), [])
        return self.correr_orden(d, exigir_ok=exigir_ok, **orden)

    def escritura(self, r, nid):
        return next(x for x in r["escrituras"] if x["nodo"] == nid)


class PaginasSinApi(Externos):
    def test_texto_tablas_enlaces_y_selector(self):
        r, _ = self.correr1(nodo("t", "web_leer", url="{{entrada.u}}", salida="pagina"),
                            nodo("tb", "web_leer", url="{{entrada.u}}", extraer="tablas", salida="tablas"),
                            nodo("e", "web_leer", url="{{entrada.u}}", extraer="enlaces", solo_mismo_sitio=True, salida="enlaces"),
                            nodo("s", "web_leer", url="{{entrada.u}}", extraer="selector", selector="article.nota h2 a", atributo="href", salida="notas"),
                            entrada={"u": f"{self.B}/pagina"})
        v = r["variables"]
        self.assertEqual(v["pagina"]["titulo"], "Tarifas 2026")
        self.assertIn("Texto principal de la página.", v["pagina"]["texto"])
        for fuera in ("menú que no va", "pie que no va", "no va\""):
            self.assertNotIn(fuera, v["pagina"]["texto"])
        self.assertEqual(v["tablas"]["tablas"][0]["filas"], [{"Plan": "Básico", "Valor": "10"}, {"Plan": "Plus", "Valor": "20"}])
        self.assertEqual([x["url"] for x in v["enlaces"]["enlaces"]], [f"{self.B}/inicio", f"{self.B}/n/1"])
        self.assertEqual([x["valor"] for x in v["notas"]["elementos"]], [f"{self.B}/n/1", "https://otro.example/n/2"])
        self.assertEqual(len([x for x in self.E.llamadas if x["ruta"] == "/robots.txt"]), 1)  # robots.txt una sola vez (se recuerda)
        self.assertTrue(all("Positiva-IA" in x["h"].get("user-agent", "") for x in self.E.llamadas))

    def test_robots_redireccion_tipo_y_destinos_vetados(self):
        r, _ = self.correr1(nodo("t", "web_leer", url="{{entrada.u}}"), entrada={"u": f"{self.B}/privado/x"}, exigir_ok=False)
        self.assertIn("robots.txt", r["motivo"])
        self.assertFalse([x for x in self.E.llamadas if x["ruta"].startswith("/privado")])
        r, _ = self.correr1(nodo("t", "web_leer", url="{{entrada.u}}", salida="p"), entrada={"u": f"{self.B}/redirige"})
        self.assertEqual(r["variables"]["p"]["url"], f"{self.B}/pagina")
        r, _ = self.correr1(nodo("t", "web_leer", url="{{entrada.u}}"), entrada={"u": f"{self.B}/imagen.png"}, exigir_ok=False)
        self.assertIn("no es una página", r["motivo"])
        for malo in ("https://www.positiva.gov.co/x", "https://169.254.169.254/latest", "http://localhost:8095/api", "https://metadata.google.internal/"):
            r, _ = self.correr1(nodo("t", "web_leer", url="{{entrada.u}}"), entrada={"u": malo}, exigir_ok=False)
            self.assertEqual(r["accion"], "error", malo)
            self.assertTrue("bloqueado" in r["motivo"] or "https" in r["motivo"], (malo, r["motivo"]))
        # y en la validación, con la URL fija
        self.assertTrue(fd._validar_config({"id": "n", "tipo": "web_leer", "config": {"url": "https://intranet.positiva.gov.co/"}}))
        self.assertTrue(fd._validar_config({"id": "n", "tipo": "web_leer", "config": {"url": "https://10.0.0.5/"}}))

    def test_frecuencia_por_sitio_y_tamano(self):
        esperas = []
        c_web.dormir = esperas.append
        c_web.INTERVALO_S = 5.0
        c_web.esperar_turno("sitio.test", 5.0)
        c_web.esperar_turno("sitio.test", 5.0)
        self.assertEqual(len(esperas), 1)
        self.assertGreater(esperas[0], 4.0)
        c_web.esperar_turno("otro.test", 5.0)  # otro sitio no espera
        self.assertEqual(len(esperas), 1)
        with self.assertRaises(fd.ErrorTransitorio):
            c_web.esperar_turno("sitio.test", 60.0)  # más de 15 s de espera: fallo pasajero, no se queda colgado
        c_web.INTERVALO_S = 0.0
        with self.assertRaises(fd.ErrorDeNegocio):  # la página pasa del tope (1.000 bytes): se corta, no se lee entera
            c_web.bajar(f"{self.B}/grande", max_bytes=1000)

    def test_rss_atom_descubrir_y_entidades(self):
        r, _ = self.correr1(nodo("f", "rss_leer", url="{{entrada.u}}", salida="noticias"), entrada={"u": f"{self.B}/feed.xml"})
        n = r["variables"]["noticias"]
        self.assertEqual(n["titulo"], "Noticias")
        self.assertEqual([x["titulo"] for x in n["elementos"]], ["Circular de riesgos", "Noticia vieja"])
        self.assertEqual(n["elementos"][0]["resumen"], "Resumen de Circular de riesgos")
        self.assertTrue(n["elementos"][0]["fecha"].startswith("2026-11-06T10:00:00"))
        r, _ = self.correr1(nodo("f", "rss_leer", url="{{entrada.u}}", contiene="CIRCULAR", desde="2026-11-06", salida="x"), entrada={"u": f"{self.B}/pagina"})
        self.assertEqual([x["titulo"] for x in r["variables"]["x"]["elementos"]], ["Circular de riesgos"])  # la página anuncia el feed
        r, _ = self.correr1(nodo("f", "rss_leer", url="{{entrada.u}}", salida="a"), entrada={"u": f"{self.B}/atom.xml"})
        a = r["variables"]["a"]["elementos"][0]
        self.assertEqual((a["titulo"], a["autor"], a["enlace"]), ("Hola Atom", "Ana", f"{self.B}/post/1"))
        r, _ = self.correr1(nodo("f", "rss_leer", url="{{entrada.u}}"), entrada={"u": f"{self.B}/malo.xml"}, exigir_ok=False)
        self.assertIn("ENTITY", r["motivo"])

    def test_rss_nuevo_sin_repetir_y_resumen(self):
        d = flujo([nodo("llega", "rss_nuevo", url=f"{self.B}/feed.xml", cada_minutos=30),
                   nodo("leer", "web_leer", url=f"{self.B}/pagina", salida="pagina"),
                   nodo("fin", "salida", desenlace="manual", motivo="{{entrada.elemento.titulo}}")],
                  [{"desde": "llega", "hacia": "leer"}, {"desde": "leer", "hacia": "fin"}])
        self.assertEqual(fd.validar(d), [])
        p = fd.sondear({"flujo": "prueba", "definicion": d, "credenciales": {}})
        self.assertTrue(p["ok"] and p["primera"] and p["casos"] == [], p)  # la primera solo memoriza
        p = fd.sondear({"flujo": "prueba", "definicion": d, "credenciales": {}})
        self.assertTrue(p["sin_cambios"])  # ETag igual: 304, ni se baja
        self.assertEqual(self.E.llamadas[-1]["h"].get("if-none-match"), '"v2"')
        self.E.items.insert(0, ("n3", "Nueva resolución", "Sat, 07 Nov 2026 08:00:00 GMT"))
        p = fd.sondear({"flujo": "prueba", "definicion": d, "credenciales": {}})
        self.assertEqual([x["entrada"]["elemento"]["titulo"] for x in p["casos"]], ["Nueva resolución"])
        self.assertEqual(fd.sondear({"flujo": "prueba", "definicion": d, "credenciales": {}})["casos"], [])
        r, _ = self.correr_orden(d, entrada=p["casos"][0]["entrada"])
        self.assertEqual(r["motivo"], "Nueva resolución")
        self.assertIn("Texto principal", r["variables"]["pagina"]["texto"])


class Almacenamiento(Externos):
    def test_ftp_listar_leer_y_subir_retenido_o_con_permiso(self):
        r, _ = self.correr1(nodo("l", "ftp_archivos", credencial="ftp", carpeta="/entrantes", salida="archivos"),
                            nodo("x", "ftp_archivos", credencial="ftp", operacion="leer", archivo="/entrantes/novedades.csv", salida="csv"),
                            nodo("s", "ftp_archivos", credencial="ftp", operacion="subir", carpeta="/salida", nombre="r.txt", contenido="hola {{csv.nombre}}"))
        self.assertEqual([x["nombre"] for x in r["variables"]["archivos"]], ["novedades.csv", "viejo.txt"])
        self.assertEqual(r["variables"]["archivos"][0]["tamano"], len(self.ftp.archivos["/entrantes/novedades.csv"]))
        self.assertEqual(r["variables"]["archivos"][0]["tipo"], "archivo")
        self.assertIn("111,activo", r["variables"]["csv"]["texto"])
        w = self.escritura(r, "s")
        self.assertTrue(w["bloqueado"])
        self.assertEqual(w["vista"], "hola novedades.csv")
        self.assertNotIn("/salida/r.txt", self.ftp.archivos)
        self.assertFalse([c for c in self.ftp.comandos if c[0] == "STOR"])
        r, _ = self.correr1(nodo("s", "ftp_archivos", credencial="ftp-escribe", operacion="subir", carpeta="/entrantes", nombre="viejo.txt", contenido="nuevo"))
        self.assertEqual(self.ftp.archivos["/entrantes/viejo (1).txt"], b"nuevo")  # conflicto: renombrar
        self.assertNotIn("clave-FTP-777", json.dumps(r))

    def test_ftp_no_sale_de_la_carpeta_base(self):
        self.assertTrue(fd._validar_config({"id": "n", "tipo": "ftp_archivos", "config": {"credencial": "ftp", "operacion": "leer", "archivo": "../etc/passwd"}}))
        with self.assertRaises(fd.ErrorDeNegocio):
            c_almacen.ruta_remota("/base", "a/../../x")
        self.assertEqual(c_almacen.ruta_remota("/base", "/sub/a.txt"), "/base/sub/a.txt")

    def test_s3_firma_listar_leer_y_subir(self):
        r, _ = self.correr1(nodo("l", "s3_archivos", credencial="s3", carpeta="facturas", salida="lista"),
                            nodo("x", "s3_archivos", credencial="s3", operacion="leer", archivo="facturas/2026/f-2.pdf", como="archivo", salida="f"),
                            nodo("s", "s3_archivos", credencial="s3", operacion="subir", carpeta="respuestas", archivo="{{f}}"))
        self.assertEqual([(x["nombre"], x["tipo"]) for x in r["variables"]["lista"]], [("2026", "carpeta"), ("f-1.txt", "archivo")])
        self.assertTrue(r["variables"]["f"]["id"].startswith("a-"))
        self.assertTrue(self.escritura(r, "s")["bloqueado"])
        self.assertNotIn("respuestas/f-2.pdf", self.E.objetos)
        r, _ = self.correr1(nodo("s", "s3_archivos", credencial="s3-escribe", operacion="subir", carpeta="facturas", nombre="f-1.txt", contenido="otra"),
                            nodo("t", "s3_archivos", credencial="s3-escribe", operacion="subir", carpeta="informes", nombre="nota de prueba.json", de="entrada", formato="json"),
                            entrada={"a": 1})
        self.assertEqual(self.E.objetos["facturas/f-1 (1).txt"], b"otra")
        self.assertEqual(json.loads(self.E.objetos["informes/nota de prueba.json"]), {"a": 1})
        self.assertNotIn("secreto-S3-ABC", json.dumps(r))

    def test_firma_sigv4_con_el_ejemplo_de_aws(self):
        # «Example: GET Object» de la documentación de Signature V4 para S3
        cab = c_almacen.firmar("GET", "examplebucket.s3.amazonaws.com", "/test.txt", {}, {"range": "bytes=0-9"}, c_almacen.VACIO, "us-east-1",
                               "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                               datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc))
        self.assertTrue(cab["authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"), cab["authorization"])


class Datos(Externos):
    def test_mongo_consulta_solo_lectura(self):
        r, _ = self.correr1(nodo("b", "mongo_consulta", credencial="mongo", coleccion="solicitudes", filtro={"estado": "{{entrada.estado}}"}, orden={"creada": -1},
                                 salida="pendientes"),
                            nodo("c", "mongo_consulta", credencial="mongo", coleccion="solicitudes", operacion="contar", filtro={"estado": "cerrada"}, salida="n"),
                            nodo("a", "mongo_consulta", credencial="mongo", coleccion="solicitudes", operacion="agregar",
                                 pipeline=[{"$group": {"_id": "$region", "total": {"$sum": 1}}}], salida="agg"),
                            entrada={"estado": "pendiente"})
        self.assertEqual([x["_id"] for x in r["variables"]["pendientes"]], ["a2", "a1"])
        self.assertEqual(r["variables"]["n"]["total"], 1)
        self.assertEqual(self.mongo[-1][1][-1], {"$limit": 101})  # agregar siempre con tope
        for malo in ({"$where": "this.a == 1"}, {"x": {"$function": {"body": "1"}}}):
            self.assertTrue(fd._validar_config({"id": "n", "tipo": "mongo_consulta", "config": {"credencial": "m", "coleccion": "c", "filtro": malo}}))
        self.assertTrue(fd._validar_config({"id": "n", "tipo": "mongo_consulta", "config": {"credencial": "m", "coleccion": "c", "operacion": "agregar",
                                                                                           "pipeline": [{"$out": "copia"}]}}))

    def test_mongo_escribir_retenido_y_con_permiso(self):
        r, _ = self.correr1(nodo("i", "mongo_escribir", credencial="mongo", coleccion="solicitudes", documento={"doc": "{{entrada.doc}}"}),
                            entrada={"doc": "111"})
        self.assertTrue(self.escritura(r, "i")["bloqueado"])
        self.assertEqual(self.escritura(r, "i")["documentos"], [{"doc": "111"}])
        self.assertFalse([x for x in self.mongo if x[0].startswith("insert")])
        r, _ = self.correr1(nodo("u", "mongo_escribir", credencial="mongo-escribe", coleccion="solicitudes", operacion="actualizar", filtro={"_id": "a1"},
                                 cambios={"$set": {"estado": "atendida"}}, salida="u"))
        self.assertEqual(r["variables"]["u"]["modificados"], 1)
        self.assertEqual(self.mongo[-1], ("update_one", {"_id": "a1"}, {"$set": {"estado": "atendida"}}))
        self.assertTrue(fd._validar_config({"id": "n", "tipo": "mongo_escribir", "config": {"credencial": "m", "coleccion": "c", "operacion": "borrar"}}))
        self.assertTrue(fd._validar_config({"id": "n", "tipo": "mongo_escribir", "config": {"credencial": "m", "coleccion": "c", "operacion": "actualizar",
                                                                                           "filtro": {"a": 1}, "cambios": {"estado": "x"}}}))
        self.creds["mongo-positiva"] = cred("mongodb", True, ["clave"], host="bd.positiva.gov.co", base="x")
        r, _ = self.correr1(nodo("i", "mongo_escribir", credencial="mongo-positiva", coleccion="c", documento={"a": 1}), exigir_ok=False)
        self.assertIn("Positiva", r["motivo"])

    def test_sql_server_y_oracle(self):
        r, _ = self.correr1(nodo("q", "sql_consulta", credencial="mssql", consulta="SELECT id, nombre FROM [clientes activos] WHERE pct LIKE '10%' AND id > :desde",
                                 parametros={"desde": "0"}, max_filas=2, salida="filas"))
        self.assertEqual(r["variables"]["filas"], [{"id": 1, "nombre": "Ana"}, {"id": 2, "nombre": "Luis"}])
        sentencia = [x for x in self.sql if x[0] not in ("conectar", "ROLLBACK") and "pct" in str(x[0])][0]
        self.assertIn("'10%%'", sentencia[0])
        self.assertIn("%(desde)s", sentencia[0])
        self.assertIn(("ROLLBACK", None), self.sql)  # la lectura se deshace siempre
        self.assertEqual(self.sql[0][1]["server"], "203.0.113.10")  # sin cifrado: a la IP ya revisada
        for malo in ("SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'y')", "SELECT 1; WAITFOR DELAY '0:0:5'", "EXEC xp_cmdshell 'dir'", "SELECT * INTO copia FROM t"):
            if fd._validar_config({"id": "q", "tipo": "sql_consulta", "config": {"credencial": "mssql", "consulta": malo}}):
                continue  # ya lo rechaza la validación
            r, _ = self.correr1(nodo("q", "sql_consulta", credencial="mssql", consulta=malo), exigir_ok=False)
            self.assertEqual(r["accion"], "error", malo)
        self.sql.clear()
        r, _ = self.correr1(nodo("o", "sql_consulta", credencial="oracle", consulta="SELECT id, nombre FROM clientes WHERE id = :id", parametros={"id": "1"}, salida="o"),
                            nodo("w", "sql_ejecutar", credencial="oracle", sentencia="UPDATE clientes SET estado = :e WHERE id = :id", parametros={"e": "x", "id": "1"}))
        textos = [x[0] for x in self.sql if x[0] != "conectar"]
        self.assertIn("SET TRANSACTION READ ONLY", textos)
        self.assertTrue(any("FETCH FIRST 501 ROWS ONLY" in t for t in textos if isinstance(t, str)))
        self.assertIn("COMMIT", textos)  # la credencial oracle tiene la escritura habilitada
        r, _ = self.correr1(nodo("o", "sql_consulta", credencial="oracle", consulta="SELECT dbms_pipe.receive_message('x') FROM dual"), exigir_ok=False)
        self.assertIn("DBMS_PIPE", r["motivo"])

    def test_bigquery_lee_en_seco_y_retiene_dml(self):
        r, _ = self.correr1(nodo("q", "bigquery_consulta", credencial="gcp", proyecto="analitica-123", sql="SELECT region, total FROM `analitica-123.v.t` WHERE x = @x",
                                 parametros={"x": "{{entrada.x}}"}, salida="ventas"), entrada={"x": 5})
        v = r["variables"]["ventas"]
        self.assertEqual(v["filas"], [{"region": "Norte", "total": 15, "activo": True}, {"region": "Sur", "total": 7, "activo": False}])
        self.assertEqual(v["bytes_procesados"], 2048)
        seco = json.loads([x for x in self.E.llamadas if x["ruta"].endswith("/jobs")][0]["cuerpo"])
        self.assertTrue(seco["configuration"]["dryRun"])
        self.assertEqual(seco["configuration"]["query"]["queryParameters"][0]["parameterType"]["type"], "INT64")
        r, _ = self.correr1(nodo("q", "bigquery_consulta", credencial="gcp", proyecto="analitica-123", sql="DELETE FROM t WHERE 1=1"), exigir_ok=False)
        self.assertIn("solo lee", r["motivo"])
        r, _ = self.correr1(nodo("w", "bigquery_ejecutar", credencial="gcp", proyecto="analitica-123", sql="UPDATE t SET a = 1 WHERE id = @id", parametros={"id": "7"}))
        self.assertTrue(self.escritura(r, "w")["bloqueado"])
        self.assertFalse([x for x in self.E.llamadas if x["ruta"].endswith("/queries")][1:])
        r, _ = self.correr1(nodo("w", "bigquery_ejecutar", credencial="gcp-escribe", proyecto="analitica-123", sql="UPDATE t SET a = 1 WHERE id = 7", salida="w"))
        self.assertEqual(r["variables"]["w"]["afectadas"], 3)
        r, _ = self.correr1(nodo("w", "bigquery_ejecutar", credencial="gcp-escribe", proyecto="analitica-123", sql="DROP TABLE t"), exigir_ok=False)
        self.assertIn("DDL", r["motivo"])

    def test_firestore(self):
        r, _ = self.correr1(nodo("o", "firestore_documentos", credencial="gcp", proyecto="app-movil-123", operacion="obtener", coleccion="clientes", documento="ana", salida="ana"),
                            nodo("c", "firestore_documentos", credencial="gcp", proyecto="app-movil-123", operacion="consultar", coleccion="clientes",
                                 filtros=[{"campo": "edad", "operador": ">=", "valor": 18}], salida="adultos"),
                            nodo("g", "firestore_documentos", credencial="gcp", proyecto="app-movil-123", operacion="guardar", coleccion="clientes", documento="ana",
                                 datos={"estado": "activo"}))
        self.assertEqual(r["variables"]["ana"]["edad"], 40)
        q = json.loads([x for x in self.E.llamadas if x["ruta"].endswith(":runQuery")][0]["cuerpo"])
        self.assertEqual(q["structuredQuery"]["where"]["fieldFilter"], {"field": {"fieldPath": "edad"}, "op": "GREATER_THAN_OR_EQUAL", "value": {"integerValue": "18"}})
        self.assertTrue(self.escritura(r, "g")["bloqueado"])
        self.correr1(nodo("g", "firestore_documentos", credencial="gcp-escribe", proyecto="app-movil-123", operacion="guardar", coleccion="clientes", documento="ana",
                          datos={"estado": "activo"}))
        self.assertEqual(self.E.enviados[-1]["q"], {"updateMask.fieldPaths": "estado"})


class Mensajeria(Externos):
    def test_telegram_retenido_y_con_permiso(self):
        r, _ = self.correr1(nodo("t", "telegram_mensaje", credencial="bot", texto="Hola {{entrada.n}}"), entrada={"n": "Ana"})
        self.assertTrue(self.escritura(r, "t")["bloqueado"])
        self.assertEqual(self.E.enviados, [])
        r, _ = self.correr1(nodo("doc", "documento_generar", formato="pdf", plantilla="Informe", nombre="informe"),
                            nodo("t", "telegram_mensaje", credencial="bot-escribe", texto="x" * 5000, formato="html", archivos=["{{doc}}"], salida="tg"))
        self.assertEqual([x["telegram"] for x in self.E.enviados], ["sendMessage", "sendMessage", "sendDocument"])  # 5.000 caracteres: dos mensajes
        self.assertEqual(self.E.enviados[0]["cuerpo"]["chat_id"], "-100123")
        self.assertEqual(r["variables"]["tg"]["mensajes"], 3)
        self.assertNotIn(TOKEN_TG, json.dumps(r))

    def test_correo_transaccional_sendgrid_y_mailgun(self):
        r, _ = self.correr1(nodo("doc", "documento_generar", formato="pdf", plantilla="Certificado", nombre="certificado"),
                            nodo("s", "correo_transaccional", credencial="sendgrid", para="cliente@cliente.test", asunto="Hola", cuerpo="Adjunto", html="<p>Adjunto</p>",
                                 adjuntos=["{{doc}}"]),
                            nodo("m", "correo_transaccional", credencial="mailgun", para="a@cliente.test, b@cliente.test", asunto="Hola", cuerpo="Texto",
                                 nombre_remitente="Mesa"))
        sg = self.E.enviados[0]
        self.assertEqual(sg["auth"], "Bearer SG.llave-SENDGRID-secreta")
        self.assertEqual(sg["sendgrid"]["from"], {"email": "avisos@empresa.test"})
        self.assertEqual(sg["sendgrid"]["attachments"][0]["filename"], "certificado.pdf")
        mg = self.E.enviados[1]["mailgun"]
        self.assertIn(b"Mesa <avisos@mg.empresa.test>", mg)
        self.assertEqual(mg.count(b'name="to"'), 2)
        self.assertNotIn("SG.llave-SENDGRID-secreta", json.dumps(r))
        for para in ("alguien@positiva.gov.co", "servicioalcliente@positiva.gov.co"):
            r, _ = self.correr1(nodo("s", "correo_transaccional", credencial="sendgrid", para=para, asunto="x", cuerpo="x"), exigir_ok=False)
            self.assertIn("bloqueado por seguridad", r["motivo"])
        self.assertEqual(len(self.E.enviados), 2)

    def test_sms_twilio(self):
        r, _ = self.correr1(nodo("s", "sms_twilio", credencial="twilio", para="+57 300 123 4567", texto="Hola"))
        self.assertTrue(self.escritura(r, "s")["bloqueado"])
        self.assertEqual(self.escritura(r, "s")["para"], "+573001234567")
        r, _ = self.correr1(nodo("s", "sms_twilio", credencial="twilio-escribe", para="+573001234567", texto="Su radicado {{entrada.r}}", salida="sms"), entrada={"r": "R-1"})
        self.assertEqual(self.E.enviados[0]["twilio"], {"To": "+573001234567", "Body": "Su radicado R-1", "From": "+15550001111"})
        self.assertEqual(self.E.enviados[0]["auth"], "Basic " + base64.b64encode(f"{SID}:token-TWILIO-secreto".encode()).decode())
        self.assertEqual(r["variables"]["sms"]["estado"], "queued")
        r, _ = self.correr1(nodo("s", "sms_twilio", credencial="twilio-escribe", para="3001234567", texto="x"), exigir_ok=False)
        self.assertIn("internacional", r["motivo"])

    def test_probar_conexiones_sin_escribir(self):
        p = ni.probar_conexion({"tipo": "telegram_bot", "valores": {"token": TOKEN_TG}})
        self.assertEqual(p, {"ok": True, "detalle": "bot @avisos_bot"})
        p = ni.probar_conexion({"tipo": "ftp", "valores": dict(self.creds["ftp"]["valores"])})
        self.assertTrue(p["ok"], p)
        p = ni.probar_conexion({"tipo": "s3", "valores": dict(self.creds["s3"]["valores"])})
        self.assertTrue(p["ok"], p)
        p = ni.probar_conexion({"tipo": "s3", "valores": {**self.creds["s3"]["valores"], "secret_key": "otra"}})
        self.assertFalse(p["ok"])
        self.assertIn("SignatureDoesNotMatch", p["detalle"])


class Paridad(unittest.TestCase):
    MALOS = [
        ("web_leer", {}), ("web_leer", {"url": "https://www.ejemplo.com", "extraer": "selector"}), ("web_leer", {"url": "http://www.ejemplo.com"}),
        ("web_leer", {"url": "https://www.positiva.gov.co/x"}), ("web_leer", {"url": "https://10.1.2.3/x"}), ("web_leer", {"url": "https://a.co", "max_kb": 99999}),
        ("web_leer", {"url": "https://a.co", "selector": "p"}), ("rss_leer", {"url": "https://a.co", "max": 0}),
        ("rss_nuevo", {"url": "{{entrada.url}}"}), ("rss_nuevo", {"url": "https://a.co/feed", "cada_minutos": 1}), ("rss_nuevo", {}),
        ("ftp_archivos", {}), ("ftp_archivos", {"credencial": "f", "operacion": "leer"}), ("ftp_archivos", {"credencial": "f", "operacion": "subir"}),
        ("ftp_archivos", {"credencial": "f", "operacion": "borrar"}), ("ftp_archivos", {"credencial": "f", "operacion": "leer", "archivo": "../x"}),
        ("s3_archivos", {"credencial": "s", "operacion": "subir", "archivo": "{{doc}}", "contenido": "x"}),
        ("mongo_consulta", {"credencial": "m"}), ("mongo_consulta", {"credencial": "m", "coleccion": "c", "filtro": {"$where": "1"}}),
        ("mongo_consulta", {"credencial": "m", "coleccion": "c", "operacion": "distintos"}), ("mongo_consulta", {"credencial": "m", "coleccion": "c", "operacion": "agregar"}),
        ("mongo_consulta", {"credencial": "m", "coleccion": "c", "pipeline": [{"$merge": "x"}]}), ("mongo_consulta", {"credencial": "m", "coleccion": "c", "filtro": "estado=1"}),
        ("mongo_escribir", {"credencial": "m", "coleccion": "c"}), ("mongo_escribir", {"credencial": "m", "coleccion": "c", "operacion": "borrar"}),
        ("mongo_escribir", {"credencial": "m", "coleccion": "c", "operacion": "actualizar", "filtro": {"a": 1}, "cambios": {"b": 2}}),
        ("bigquery_consulta", {"credencial": "g", "proyecto": "p-123456"}), ("bigquery_consulta", {"credencial": "g", "proyecto": "p-123456", "sql": "SELECT 1; DROP TABLE t"}),
        ("bigquery_ejecutar", {"credencial": "g", "sql": "UPDATE t SET a=1 WHERE b=2"}),
        ("firestore_documentos", {"credencial": "g", "proyecto": "p-123456", "coleccion": "c", "operacion": "obtener"}),
        ("firestore_documentos", {"credencial": "g", "proyecto": "p-123456", "coleccion": "c", "operacion": "guardar"}),
        ("firestore_documentos", {"credencial": "g", "proyecto": "p-123456", "coleccion": "c", "filtros": [{"campo": "a", "valor": 1}]}),
        ("telegram_mensaje", {"credencial": "t"}), ("telegram_mensaje", {"credencial": "t", "texto": "x", "formato": "rtf"}),
        ("correo_transaccional", {"credencial": "s", "para": "a@b.co", "asunto": "x"}), ("sms_twilio", {"credencial": "t", "para": "+57300"}),
    ]

    def test_python_rechaza_lo_que_no_vale_y_acepta_los_ejemplos(self):
        for t, c in self.MALOS:
            self.assertTrue(fd._validar_config({"id": "n", "tipo": t, "config": c}), (t, c))
        for t in nx.TIPOS:
            s = nd.TIPOS[t]
            self.assertTrue(s.get("preparar") and s.get("consejos") and s.get("ejemplos"), t)
            for c in [s["ejemplo"]["config"], *s["ejemplos"]]:
                self.assertEqual(fd._validar_config({"id": "n", "tipo": t, "config": c}), [], (t, c))
            for k in s["config"].get("properties", {}).get("credencial", {}).get("x-credencial-tipos", []):
                self.assertIn(k, nd.TIPOS_CREDENCIAL)

    def test_zod_igual(self):
        dist = Path(os.environ.get("RUTA_SERVIDOR_DIST", "/app/servidor/dist")) / "flujos.js"
        node = shutil.which("node")
        if not node or not dist.exists():
            self.skipTest("no hay servidor compilado o node")
        buenos = [{"tipo": t, "config": c} for t in nx.TIPOS for c in [nd.TIPOS[t]["ejemplo"]["config"], *nd.TIPOS[t]["ejemplos"]]]
        guion = ("const f = await import(process.argv[1]); const d = JSON.parse(process.argv[2]);"
                 "const r = (xs) => xs.map((e) => f.CONFIG_DE[e.tipo].safeParse(e.config).success);"
                 "console.log(JSON.stringify({tipos: [...f.TIPOS_DE_NODO], buenos: r(d.buenos), malos: r(d.malos)}));")
        p = subprocess.run([node, "--input-type=module", "-e", guion, dist.as_uri(), json.dumps({"buenos": buenos, "malos": [{"tipo": t, "config": c} for t, c in self.MALOS]})],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertTrue(set(nx.TIPOS) <= set(r["tipos"]))
        self.assertEqual([b for b, ok in zip(buenos, r["buenos"]) if not ok], [])
        self.assertEqual([m for m, ok in zip(self.MALOS, r["malos"]) if ok], [])

    def test_credenciales_del_servidor(self):
        """Los tipos de conexión nuevos existen en credenciales.ts con sus secretos marcados (se cifran y nunca se devuelven)."""
        dist = Path(os.environ.get("RUTA_SERVIDOR_DIST", "/app/servidor/dist")) / "credenciales.js"
        node = shutil.which("node")
        if not node or not dist.exists():
            self.skipTest("no hay servidor compilado o node")
        guion = "const c = await import(process.argv[1]); console.log(JSON.stringify(c.tipos().map((t) => [t.tipo, t.campos.filter((x) => x.secreto).map((x) => x.nombre)])));"
        p = subprocess.run([node, "--input-type=module", "-e", guion, dist.as_uri()], capture_output=True, text=True, encoding="utf-8", timeout=60)
        tipos = dict(json.loads(p.stdout.strip().splitlines()[-1]))
        esperados = {"ftp": ["clave"], "s3": ["secret_key"], "mongodb": ["clave"], "sqlserver": ["clave"], "oracle": ["clave"], "telegram_bot": ["token"],
                     "sendgrid": ["api_key"], "mailgun": ["api_key"], "twilio": ["auth_token"]}
        for t, secretos in esperados.items():
            self.assertEqual(tipos.get(t), secretos, t)
        self.assertTrue(set(tipos) >= set(nd.TIPOS_CREDENCIAL) - set(nd.TIPOS_CREDENCIAL_IA))


if __name__ == "__main__":
    unittest.main(verbosity=2)
