"""
Pruebas de la fase 3 de integraciones (plan 5d), trabajo y clientes, con dobles
locales, sin cuentas reales ni salida a internet: un http.server en 127.0.0.1
hace de Jira Cloud, Trello, Asana, Notion, HubSpot, Salesforce (con su servidor
de tokens), Zoho CRM (y su servidor de cuentas) y DocuSign (OAuth con JWT que
el doble verifica con la llave pública, userinfo y la API de sobres).

Se prueba lo nuestro: que leer salga, que todo lo que escribe quede retenido sin
la escritura habilitada y salga bien armado con ella, la paginación, el escape
de JQL/SOQL/criterios, el bloqueo de direcciones de Positiva, que «Probar
conexión» nunca escriba, que no se filtren secretos y la paridad con el zod.

    PYTHONPATH=motor python puente/pruebas_trabajo.py        (desde la raíz; en el contenedor: /app)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
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
import nodos_integraciones as ni  # noqa: E402
import nodos_trabajo as nt  # noqa: E402
from conectores import oauth as c_oauth  # noqa: E402
from conectores import trabajo as c_tr  # noqa: E402
from pruebas_declarativo import Plataforma, cred, flujo, nodo  # noqa: E402

TOKEN_JIRA = "jira-TOKEN-secreto-123"
TRELLO_KEY, TRELLO_TOKEN = "trello-KEY-secreta", "trello-TOKEN-secreto"
TOKEN_ASANA, TOKEN_NOTION, TOKEN_HUBSPOT = "asana-PAT-secreto", "ntn_secreto_NOTION", "pat-na1-secreto-HUBSPOT"
SF_SECRETO, ZOHO_SECRETO, ZOHO_REFRESCO = "sf-SECRETO-cliente", "zoho-SECRETO-cliente", "1000.refresco-ZOHO-secreto"
SOBRE = "4b7c2d1e-0f3a-4b5c-8d9e-0a1b2c3d4e5f"
TABLERO, LISTA, LISTA2, TARJETA = "5f1a2b3c4d5e6f7a8b9c0d10", "5f1a2b3c4d5e6f7a8b9c0d11", "5f1a2b3c4d5e6f7a8b9c0d12", "5f1a2b3c4d5e6f7a8b9c0d13"
BASE_NOTION = "2f1c4b5a6d7e8f90a1b2c3d4e5f60718"
PAGINA_NOTION = "9a8b7c6d5e4f30211a2b3c4d5e6f7081"


def _llaves():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()
    return pem, k.public_key()


PRIVADA, PUBLICA = _llaves()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Estado:
    def __init__(self) -> None:
        self.llamadas: list = []
        self.escritas: list = []
        self.tokens = {"salesforce": 0, "zoho": 0, "docusign": 0}
        self.jwt: dict = {}


class Doble(BaseHTTPRequestHandler):
    E = Estado()
    B = ""

    def log_message(self, *a) -> None:
        pass

    def _resp(self, codigo, cuerpo=b"", tipo="application/json"):
        if isinstance(cuerpo, (dict, list)):
            cuerpo = json.dumps(cuerpo).encode()
        elif isinstance(cuerpo, str):
            cuerpo = cuerpo.encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        self._todo("GET")

    def do_POST(self):
        self._todo("POST")

    def do_PUT(self):
        self._todo("PUT")

    def do_PATCH(self):
        self._todo("PATCH")

    def _escrita(self, servicio, ruta, j):
        self.E.escritas.append({"servicio": servicio, "metodo": self.command, "ruta": ruta, "cuerpo": j})

    def _todo(self, metodo):  # noqa: C901 (un doble de ocho servicios)
        E = self.E
        u = urllib.parse.urlsplit(self.path)
        ruta, q = urllib.parse.unquote(u.path), dict(urllib.parse.parse_qsl(u.query))
        n = int(self.headers.get("Content-Length") or 0)
        crudo = self.rfile.read(n) if n else b""
        h = {k.lower(): v for k, v in self.headers.items()}
        try:
            j = json.loads(crudo) if crudo[:1] in (b"{", b"[") else dict(urllib.parse.parse_qsl(crudo.decode()))
        except ValueError:
            j = {}
        E.llamadas.append({"metodo": metodo, "ruta": ruta, "q": q, "h": h, "cuerpo": j, "url": self.path})
        # ------------------------------------------------------------------ Jira
        if ruta.startswith("/rest/api/3/"):
            if h.get("authorization") != "Basic " + base64.b64encode(f"ana@empresa.test:{TOKEN_JIRA}".encode()).decode():
                return self._resp(401, {"errorMessages": ["no autorizado"]})
            r = ruta[len("/rest/api/3"):]
            issue = lambda k, s="Abierto": {"id": "1" + k[-1], "key": k, "fields": {"summary": f"Resumen {k}", "status": {"name": s}, "issuetype": {"name": "Bug"},  # noqa: E731
                                                                               "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hola"}]}]}}}
            if r == "/myself":
                return self._resp(200, {"displayName": "Ana", "emailAddress": "ana@empresa.test"})
            if r == "/search/jql":
                if q.get("nextPageToken") == "p2":
                    return self._resp(200, {"issues": [issue("SOP-3")], "isLast": True})
                return self._resp(200, {"issues": [issue("SOP-1"), issue("SOP-2")], "nextPageToken": "p2"})
            if r == "/issue" and metodo == "POST":
                self._escrita("jira", r, j)
                return self._resp(201, {"id": "10010", "key": "SOP-10"})
            if r == "/issue/SOP-1" and metodo == "GET":
                return self._resp(200, issue("SOP-1"))
            if r == "/issue/SOP-1/comment":
                self._escrita("jira", r, j)
                return self._resp(201, {"id": "c1"})
            if r == "/issue/SOP-1/transitions":
                if metodo == "GET":
                    return self._resp(200, {"transitions": [{"id": "21", "name": "Iniciar", "to": {"name": "En curso"}}, {"id": "31", "name": "Cerrar", "to": {"name": "Hecho"}}]})
                self._escrita("jira", r, j)
                return self._resp(204, b"")
            return self._resp(404, {"errorMessages": [f"no existe {r}"]})
        # ---------------------------------------------------------------- Trello
        if ruta.startswith("/trello/"):
            if h.get("authorization") != f'OAuth oauth_consumer_key="{TRELLO_KEY}", oauth_token="{TRELLO_TOKEN}"':
                return self._resp(401, "invalid token", "text/plain")
            r = ruta[len("/trello"):]
            tarjeta = lambda i, lista=LISTA: {"id": i, "name": f"Tarjeta {i[-2:]}", "desc": "", "idList": lista, "due": None, "shortUrl": f"https://trello.com/c/{i[-8:]}", "labels": []}  # noqa: E731
            if r == "/members/me":
                return self._resp(200, {"username": "ana", "fullName": "Ana"})
            if r == "/members/me/boards":
                return self._resp(200, [{"id": TABLERO, "name": "Operaciones", "shortUrl": "https://trello.com/b/AbCd1234"}])
            if r == "/boards/AbCd1234/lists":
                return self._resp(200, [{"id": LISTA, "name": "Por hacer"}, {"id": LISTA2, "name": "Hecho"}])
            if r == f"/lists/{LISTA}/cards":
                return self._resp(200, [tarjeta(TARJETA), tarjeta("5f1a2b3c4d5e6f7a8b9c0d14")])
            if r == "/cards" and metodo == "POST":
                self._escrita("trello", r, j)
                return self._resp(200, {"id": "5f1a2b3c4d5e6f7a8b9c0d99", "shortUrl": "https://trello.com/c/Nueva123", "name": j.get("name")})
            if r == f"/cards/{TARJETA}" and metodo == "PUT":
                self._escrita("trello", r, j)
                return self._resp(200, {"id": TARJETA, "idList": j.get("idList")})
            if r == f"/cards/{TARJETA}/actions/comments":
                self._escrita("trello", r, j)
                return self._resp(200, {"id": "accion1"})
            return self._resp(404, "no existe", "text/plain")
        # ----------------------------------------------------------------- Asana
        if ruta.startswith("/asana/"):
            if h.get("authorization") != f"Bearer {TOKEN_ASANA}":
                return self._resp(401, {"errors": [{"message": "Not Authorized"}]})
            r = ruta[len("/asana"):]
            tarea = lambda g: {"gid": g, "name": f"Tarea {g}", "completed": False, "due_on": "2026-12-01", "assignee": {"name": "Ana"}, "permalink_url": f"https://app.asana.com/0/1/{g}"}  # noqa: E731
            if r == "/users/me":
                return self._resp(200, {"data": {"name": "Ana", "email": "ana@empresa.test", "workspaces": [{"gid": "111", "name": "Equipo"}]}})
            if r == "/tasks" and metodo == "GET":
                if q.get("offset") == "o2":
                    return self._resp(200, {"data": [tarea("3")], "next_page": None})
                return self._resp(200, {"data": [tarea("1"), tarea("2")], "next_page": {"offset": "o2"}})
            if r == "/tasks" and metodo == "POST":
                self._escrita("asana", r, j)
                return self._resp(201, {"data": {"gid": "999", "name": j["data"]["name"], "permalink_url": "https://app.asana.com/0/1/999"}})
            if r == "/tasks/1" and metodo == "PUT":
                self._escrita("asana", r, j)
                return self._resp(200, {"data": {"gid": "1", "completed": True}})
            if r == "/tasks/1/stories":
                self._escrita("asana", r, j)
                return self._resp(201, {"data": {"gid": "s1"}})
            if r == "/tasks/1":
                return self._resp(200, {"data": tarea("1")})
            return self._resp(404, {"errors": [{"message": "no existe"}]})
        # ---------------------------------------------------------------- Notion
        if ruta.startswith("/notion/"):
            if h.get("authorization") != f"Bearer {TOKEN_NOTION}" or h.get("notion-version") != c_tr.NOTION_VERSION:
                return self._resp(401, {"message": "API token is invalid."})
            r = ruta[len("/notion"):]
            bd = c_tr.id_notion(BASE_NOTION, "x")
            pg = c_tr.id_notion(PAGINA_NOTION, "x")
            pagina = lambda i, nombre: {"id": i, "url": f"https://www.notion.so/{i}", "properties": {  # noqa: E731
                "Nombre": {"type": "title", "title": [{"plain_text": nombre}]}, "Estado": {"type": "status", "status": {"name": "Pendiente"}},
                "Monto": {"type": "number", "number": 10}, "Etiquetas": {"type": "multi_select", "multi_select": [{"name": "a"}]}}}
            if r == "/users/me":
                return self._resp(200, {"name": "Positiva-IA", "type": "bot"})
            if r == f"/databases/{bd}/query":
                if j.get("start_cursor") == "c2":
                    return self._resp(200, {"results": [pagina("p3", "Tres")], "has_more": False, "next_cursor": None})
                return self._resp(200, {"results": [pagina("p1", "Uno"), pagina("p2", "Dos")], "has_more": True, "next_cursor": "c2"})
            if r == f"/databases/{bd}":
                return self._resp(200, {"properties": {"Nombre": {"type": "title"}, "Estado": {"type": "status"}, "Fecha": {"type": "date"}, "Monto": {"type": "number"},
                                                       "Etiquetas": {"type": "multi_select"}, "Urgente": {"type": "checkbox"}, "Correo": {"type": "email"}}})
            if r == "/pages" and metodo == "POST":
                self._escrita("notion", r, j)
                return self._resp(200, {"id": pg, "url": f"https://www.notion.so/{pg}"})
            if r == f"/pages/{pg}" and metodo == "GET":
                return self._resp(200, pagina(pg, "Uno"))
            if r == f"/pages/{pg}" and metodo == "PATCH":
                self._escrita("notion", r, j)
                return self._resp(200, {"id": pg, "url": f"https://www.notion.so/{pg}"})
            if r == f"/blocks/{pg}/children":
                self._escrita("notion", r, j)
                return self._resp(200, {"results": []})
            return self._resp(404, {"message": f"no existe {r}"})
        # --------------------------------------------------------------- HubSpot
        if ruta.startswith("/hubspot/"):
            if h.get("authorization") != f"Bearer {TOKEN_HUBSPOT}":
                return self._resp(401, {"message": "Authentication credentials not found"})
            r = ruta[len("/hubspot"):]
            if r == "/account-info/v3/details":
                return self._resp(200, {"portalId": 123, "accountType": "STANDARD"})
            if r == "/crm/v3/objects/contacts/search":
                if j.get("after") == "2":
                    return self._resp(200, {"results": [{"id": "502", "properties": {"email": "luis@cliente.test"}}]})
                return self._resp(200, {"results": [{"id": "501", "properties": {"email": "ana@cliente.test", "firstname": "Ana"}}], "paging": {"next": {"after": "2"}}})
            if r == "/crm/v3/objects/contacts/ana@cliente.test":
                return self._resp(200, {"id": "501", "properties": {"email": "ana@cliente.test"}, "createdAt": "2026-01-01"})
            if r == "/crm/v3/objects/contacts" and metodo == "POST":
                self._escrita("hubspot", r, j)
                return self._resp(201, {"id": "777", "createdAt": "2026-11-07"})
            if r == "/crm/v3/objects/deals/888" and metodo == "PATCH":
                self._escrita("hubspot", r, j)
                return self._resp(200, {"id": "888", "updatedAt": "2026-11-07"})
            if r == "/crm/v3/objects/notes":
                self._escrita("hubspot", r, j)
                return self._resp(201, {"id": "n1"})
            return self._resp(404, {"message": f"no existe {r}"})
        # ------------------------------------------------------------ Salesforce
        if ruta == "/services/oauth2/token":
            if j.get("grant_type") != "client_credentials" or j.get("client_secret") != SF_SECRETO:
                return self._resp(400, {"error": "invalid_client"})
            E.tokens["salesforce"] += 1
            return self._resp(200, {"access_token": "sf-acceso", "instance_url": self.B, "token_type": "Bearer"})
        if ruta.startswith("/services/data/v61.0/"):
            if h.get("authorization") != "Bearer sf-acceso":
                return self._resp(401, [{"errorCode": "INVALID_SESSION_ID", "message": "Session expired"}])
            r = ruta[len("/services/data/v61.0"):]
            if r == "/limits":
                return self._resp(200, {"DailyApiRequests": {"Max": 15000, "Remaining": 14999}})
            if r == "/query":
                return self._resp(200, {"totalSize": 3, "done": False, "nextRecordsUrl": "/services/data/v61.0/query/01g-2000",
                                        "records": [{"attributes": {"type": "Account"}, "Id": "001000000000001AAA", "Name": "Uno", "Owner": {"attributes": {"type": "User"}, "Name": "Ana"}},
                                                    {"attributes": {"type": "Account"}, "Id": "001000000000002AAA", "Name": "Dos"}]})
            if r == "/query/01g-2000":
                return self._resp(200, {"totalSize": 3, "done": True, "records": [{"attributes": {"type": "Account"}, "Id": "001000000000003AAA", "Name": "Tres"}]})
            if r == "/sobjects/Case" and metodo == "POST":
                self._escrita("salesforce", r, j)
                return self._resp(201, {"id": "500000000000001AAA", "success": True, "errors": []})
            if r == "/sobjects/Case/500000000000001AAA" and metodo == "PATCH":
                self._escrita("salesforce", r, j)
                return self._resp(204, b"")
            if r == "/sobjects/Contact/003000000000001AAA":
                return self._resp(200, {"attributes": {"type": "Contact"}, "Id": "003000000000001AAA", "Name": "Ana", "Email": "ana@cliente.test"})
            return self._resp(404, [{"errorCode": "NOT_FOUND", "message": "no existe"}])
        # --------------------------------------------------------------- Zoho
        if ruta == "/zohoc/oauth/v2/token":
            if j.get("refresh_token") != ZOHO_REFRESCO:
                return self._resp(200, {"error": "invalid_code"})
            E.tokens["zoho"] += 1
            return self._resp(200, {"access_token": "zoho-acceso", "expires_in": 3600})
        if ruta.startswith("/zoho/crm/v6/"):
            if h.get("authorization") != "Zoho-oauthtoken zoho-acceso":
                return self._resp(401, {"code": "INVALID_TOKEN"})
            r = ruta[len("/zoho/crm/v6"):]
            if r == "/users":
                return self._resp(200, {"users": [{"full_name": "Ana Zoho", "email": "ana@empresa.test"}]})
            if r == "/Leads/search":
                if "nadie" in json.dumps(q):
                    return self._resp(204, b"")
                return self._resp(200, {"data": [{"id": "5000000001", "Full_Name": "Ana", "Email": "ana@cliente.test"}], "info": {"more_records": False}})
            if r == "/Leads" and metodo == "POST":
                self._escrita("zoho", r, j)
                return self._resp(201, {"data": [{"code": "SUCCESS", "status": "success", "details": {"id": "5000000009"}}]})
            if r == "/Leads/5000000002" and metodo == "PUT":
                self._escrita("zoho", r, j)
                return self._resp(202, {"data": [{"code": "INVALID_DATA", "status": "error", "message": "invalid data", "details": {"api_name": "Email"}}]})
            return self._resp(404, {"code": "INVALID_URL_PATTERN"})
        # ------------------------------------------------------------- DocuSign
        if ruta == "/ds/oauth/token":
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            cab, cuerpo, firma = j.get("assertion", "..").split(".")
            try:
                PUBLICA.verify(_b64d(firma), f"{cab}.{cuerpo}".encode(), padding.PKCS1v15(), hashes.SHA256())
            except Exception:
                return self._resp(400, {"error": "invalid_grant", "error_description": "firma"})
            E.jwt = json.loads(_b64d(cuerpo))
            if E.jwt.get("sub") == "sin-consentimiento":
                return self._resp(400, {"error": "consent_required"})
            E.tokens["docusign"] += 1
            return self._resp(200, {"access_token": "ds-acceso", "expires_in": 3600})
        if ruta == "/ds/oauth/userinfo":
            return self._resp(200, {"name": "Ana", "email": "ana@empresa.test", "accounts": [{"account_id": "acc-1", "account_name": "Pruebas", "base_uri": self.B, "is_default": True}]})
        if ruta.startswith("/restapi/v2.1/accounts/acc-1/"):
            if h.get("authorization") != "Bearer ds-acceso":
                return self._resp(401, {"errorCode": "AUTHORIZATION_INVALID_TOKEN"})
            r = ruta[len("/restapi/v2.1/accounts/acc-1"):]
            if r == "/envelopes" and metodo == "POST":
                self._escrita("docusign", r, j)
                return self._resp(201, {"envelopeId": SOBRE, "status": j.get("status"), "statusDateTime": "2026-11-07T10:00:00Z"})
            if r == f"/envelopes/{SOBRE}":
                return self._resp(200, {"status": "completed", "emailSubject": "Contrato", "sentDateTime": "2026-11-07", "completedDateTime": "2026-11-08"})
            if r == f"/envelopes/{SOBRE}/recipients":
                return self._resp(200, {"signers": [{"name": "Ana", "email": "ana@cliente.test", "status": "completed", "signedDateTime": "2026-11-08", "routingOrder": "1"}]})
            if r == f"/envelopes/{SOBRE}/documents/combined":
                return self._resp(200, b"%PDF-1.4 firmado", "application/pdf")
            return self._resp(404, {"errorCode": "NOT_FOUND"})
        return self._resp(404, {"error": f"no existe {ruta}"})


class Trabajo(Plataforma):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Doble)
        cls.B = Doble.B = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        super().setUp()
        Doble.E = self.E = Estado()
        fd._IPS_PERMITIDAS_PRUEBAS.add("127.0.0.1")
        self._prev = dict(c_tr.BASES)
        c_tr.BASES.update({"trello": f"{self.B}/trello", "asana": f"{self.B}/asana", "notion": f"{self.B}/notion", "hubspot": f"{self.B}/hubspot",
                           "zoho_cuentas": f"{self.B}/zohoc", "zoho_api": f"{self.B}/zoho", "docusign_demo": f"{self.B}/ds", "docusign": f"{self.B}/ds"})
        c_tr.olvidar()
        c_oauth.olvidar()
        B = self.B
        v = {
            "jira": ("jira", ["api_token"], dict(sitio=B, correo="ana@empresa.test", api_token=TOKEN_JIRA)),
            "trello": ("trello", ["api_key", "token"], dict(api_key=TRELLO_KEY, token=TRELLO_TOKEN)),
            "asana": ("asana", ["token"], dict(token=TOKEN_ASANA, espacio="111")),
            "notion": ("notion", ["token"], dict(token=TOKEN_NOTION)),
            "hubspot": ("hubspot", ["token"], dict(token=TOKEN_HUBSPOT)),
            "sf": ("salesforce", ["client_secret"], dict(instancia=B, client_id="sf-cliente", client_secret=SF_SECRETO)),
            "zoho": ("zoho", ["client_secret", "refresh_token"], dict(region="com", client_id="zoho-cliente", client_secret=ZOHO_SECRETO, refresh_token=ZOHO_REFRESCO)),
            "ds": ("docusign", ["private_key"], dict(entorno="demo", integration_key="ik-1", user_id="u-1", account_id="acc-1", private_key=PRIVADA)),
        }
        for nombre, (tipo, secretos, valores) in v.items():
            self.creds[nombre] = cred(tipo, False, secretos, **valores)
            self.creds[f"{nombre}-escribe"] = cred(tipo, True, secretos, **valores)

    def tearDown(self):
        c_tr.BASES.clear()
        c_tr.BASES.update(self._prev)
        super().tearDown()

    def correr1(self, *nodos, exigir_ok=True, **orden):
        ns = [nodo("inicio", "entrada_manual")] + list(nodos) + [nodo("fin", "salida", desenlace="manual")]
        d = flujo(ns, [{"desde": a["id"], "hacia": b["id"]} for a, b in zip(ns, ns[1:])])
        self.assertEqual(fd.validar(d), [])
        r, ev = self.correr_orden(d, exigir_ok=exigir_ok, **orden)
        for s in (TOKEN_JIRA, TRELLO_TOKEN, TRELLO_KEY, TOKEN_ASANA, TOKEN_NOTION, TOKEN_HUBSPOT, SF_SECRETO, ZOHO_SECRETO, ZOHO_REFRESCO, PRIVADA[40:90]):
            self.assertNotIn(s, self.salida.getvalue())  # ningún secreto en eventos, resultado ni consola
        return r, ev

    def escritura(self, r, nid):
        return next(x for x in r["escrituras"] if x["nodo"] == nid)

    def escritas(self, servicio):
        return [x for x in self.E.escritas if x["servicio"] == servicio]


class Jira(Trabajo):
    def test_buscar_pagina_y_escapa_el_jql(self):
        r, _ = self.correr1(nodo("b", "jira_issues", credencial="jira", jql='project = SOP AND summary ~ "{{entrada.t}}"', max=10, salida="issues"),
                            nodo("v", "jira_issues", credencial="jira", operacion="ver", issue="sop-1", salida="uno"), entrada={"t": 'dice "hola" \\ x'})
        issues = r["variables"]["issues"]
        self.assertEqual([x["clave"] for x in issues], ["SOP-1", "SOP-2", "SOP-3"])  # dos páginas con nextPageToken
        self.assertEqual(issues[0]["url"], f"{self.B}/browse/SOP-1")
        self.assertEqual(issues[0]["descripcion"], "hola")
        b = [x for x in self.E.llamadas if x["ruta"] == "/rest/api/3/search/jql"]
        self.assertEqual(b[0]["q"]["jql"], 'project = SOP AND summary ~ "dice \\"hola\\" \\\\ x"')
        self.assertEqual(b[1]["q"]["nextPageToken"], "p2")
        self.assertEqual(r["variables"]["uno"]["estado"], "Abierto")
        self.assertEqual(self.E.escritas, [])

    def test_crear_comentar_y_cambiar_estado_retenidos_y_con_permiso(self):
        pasos = lambda c: [nodo("c", "jira_issues", credencial=c, operacion="crear", proyecto="SOP", tipo_issue="Bug", resumen="Error: {{entrada.asunto}}",  # noqa: E731
                                descripcion="Línea uno\n- punto a\n- punto b", prioridad="High", etiquetas=["correo"], campos_extra={"duedate": "2026-12-01"}, salida="issue"),
                           nodo("k", "jira_issues", credencial=c, operacion="comentar", issue="SOP-1", comentario="Visto {{issue.clave}}"),
                           nodo("e", "jira_issues", credencial=c, operacion="cambiar_estado", issue="SOP-1", estado="en curso", salida="estado")]
        r, ev = self.correr1(*pasos("jira"), entrada={"asunto": "falla el pago"})
        self.assertEqual(self.E.escritas, [])
        self.assertTrue(all(self.escritura(r, x)["bloqueado"] for x in ("c", "k", "e")))
        self.assertEqual(self.escritura(r, "c")["resumen"], "Error: falla el pago")
        self.assertTrue(r["variables"]["issue"]["bloqueado"])
        self.assertTrue(any(e["tipo"] == "escritura" and e["bloqueada"] for e in ev))
        r, _ = self.correr1(*pasos("jira-escribe"), entrada={"asunto": "falla el pago"})
        crear, comentar, transicion = self.escritas("jira")
        f = crear["cuerpo"]["fields"]
        self.assertEqual((f["project"], f["issuetype"], f["summary"], f["priority"], f["labels"], f["duedate"]),
                         ({"key": "SOP"}, {"name": "Bug"}, "Error: falla el pago", {"name": "High"}, ["correo"], "2026-12-01"))
        self.assertEqual(f["description"]["content"][1]["type"], "bulletList")  # las líneas «- » quedan como lista en ADF
        self.assertEqual(r["variables"]["issue"]["clave"], "SOP-10")
        self.assertEqual(comentar["cuerpo"]["body"]["content"][0]["content"][0]["text"], "Visto SOP-10")
        self.assertEqual(transicion["cuerpo"], {"transition": {"id": "21"}})  # por el estado destino, sin mayúsculas
        self.assertEqual(r["variables"]["estado"]["estado"], "En curso")
        r, _ = self.correr1(nodo("e", "jira_issues", credencial="jira-escribe", operacion="cambiar_estado", issue="SOP-1", estado="Archivado"), exigir_ok=False)
        self.assertIn("Iniciar → En curso", r["motivo"])

    def test_nunca_positiva(self):
        for texto in ("Escribir a alguien@positiva.gov.co", "copia a notificacionesjudiciales@positiva.gov.co"):
            r, ev = self.correr1(nodo("c", "jira_issues", credencial="jira-escribe", operacion="crear", proyecto="SOP", resumen="x", descripcion=texto), exigir_ok=False)
            self.assertIn("bloqueado por seguridad", r["motivo"])
            self.assertTrue(any(e["tipo"] == "escritura" and e.get("vetado") for e in ev))
        self.assertEqual(self.E.escritas, [])
        r, _ = self.correr1(nodo("p", "jira_issues", credencial="jira", jql="x"), exigir_ok=False,
                            credenciales={**self.creds, "jira": cred("jira", False, ["api_token"], sitio="https://evil.example.com", correo="a@b.co", api_token="t")})
        self.assertIn("atlassian.net", r["motivo"])


class TrelloYAsana(Trabajo):
    def test_trello(self):
        r, _ = self.correr1(nodo("t", "trello_tarjetas", credencial="trello", operacion="tableros", salida="tableros"),
                            nodo("l", "trello_tarjetas", credencial="trello", operacion="listas", tablero="https://trello.com/b/AbCd1234/operaciones", salida="listas"),
                            nodo("c", "trello_tarjetas", credencial="trello", operacion="tarjetas", lista=LISTA, salida="tarjetas"),
                            nodo("n", "trello_tarjetas", credencial="trello", operacion="crear", lista="{{listas.0.id}}", nombre="Pedido {{entrada.n}}", vence="2026-12-01",
                                 salida="nueva"))
        v = r["variables"]
        self.assertEqual((v["tableros"][0]["nombre"], [x["nombre"] for x in v["listas"]], len(v["tarjetas"])), ("Operaciones", ["Por hacer", "Hecho"], 2))
        self.assertTrue(v["nueva"]["bloqueado"])
        self.assertEqual(self.escritura(r, "n")["lista"], LISTA)
        self.assertFalse(any(TRELLO_TOKEN in x["url"] for x in self.E.llamadas))  # la llave y el token van en el encabezado, no en la URL
        r, _ = self.correr1(nodo("n", "trello_tarjetas", credencial="trello-escribe", operacion="crear", lista=LISTA, nombre="Pedido", posicion="arriba", salida="nueva"),
                            nodo("m", "trello_tarjetas", credencial="trello-escribe", operacion="mover", tarjeta=TARJETA, lista=LISTA2),
                            nodo("k", "trello_tarjetas", credencial="trello-escribe", operacion="comentar", tarjeta=TARJETA, comentario="Listo: {{nueva.url}}"))
        crear, mover, comentar = self.escritas("trello")
        self.assertEqual((crear["cuerpo"]["idList"], crear["cuerpo"]["pos"]), (LISTA, "top"))
        self.assertEqual(mover["cuerpo"], {"idList": LISTA2, "pos": "bottom"})
        self.assertEqual(comentar["cuerpo"], {"text": "Listo: https://trello.com/c/Nueva123"})

    def test_asana(self):
        r, _ = self.correr1(nodo("l", "asana_tareas", credencial="asana", proyecto="1204567890123456", salida="tareas"),
                            nodo("m", "asana_tareas", credencial="asana", asignado="me", max=2, salida="mias"),
                            nodo("c", "asana_tareas", credencial="asana", operacion="crear", nombre="Revisar", vence="2026-12-01"))
        v = r["variables"]
        self.assertEqual([x["id"] for x in v["tareas"]], ["1", "2", "3"])  # paginado con offset
        self.assertEqual(len(v["mias"]), 2)
        mias = [x for x in self.E.llamadas if x["ruta"] == "/asana/tasks" and x["q"].get("assignee")]
        self.assertEqual((mias[0]["q"]["assignee"], mias[0]["q"]["workspace"], mias[0]["q"]["completed_since"]), ("me", "111", "now"))
        self.assertTrue(self.escritura(r, "c")["bloqueado"])
        self.assertEqual(self.escritura(r, "c")["workspace"], "111")
        r, _ = self.correr1(nodo("c", "asana_tareas", credencial="asana-escribe", operacion="crear", proyecto="1204567890123456", nombre="Revisar {{entrada.x}}",
                                 asignado="luis@cliente.test", salida="t"),
                            nodo("h", "asana_tareas", credencial="asana-escribe", operacion="completar", tarea="1"),
                            nodo("k", "asana_tareas", credencial="asana-escribe", operacion="comentar", tarea="1", comentario="Hecho"), entrada={"x": "caso"})
        crear, completar, comentar = self.escritas("asana")
        self.assertEqual(crear["cuerpo"]["data"], {"name": "Revisar caso", "notes": "", "projects": ["1204567890123456"], "assignee": "luis@cliente.test"})
        self.assertEqual(completar["cuerpo"], {"data": {"completed": True}})
        self.assertEqual(comentar["cuerpo"], {"data": {"text": "Hecho"}})
        self.assertEqual(r["variables"]["t"]["id"], "999")
        r, _ = self.correr1(nodo("c", "asana_tareas", credencial="asana-escribe", operacion="crear", nombre="x", asignado="jefe@positiva.gov.co"), exigir_ok=False)
        self.assertIn("bloqueado por seguridad", r["motivo"])
        self.assertEqual(len(self.escritas("asana")), 3)


class NotionYHubSpot(Trabajo):
    def test_notion(self):
        r, _ = self.correr1(nodo("q", "notion_paginas", credencial="notion", base_datos=f"https://www.notion.so/equipo/{BASE_NOTION}?v=1",
                                 filtro={"property": "Estado", "status": {"equals": "{{entrada.e}}"}}, salida="paginas"), entrada={"e": "Pendiente"})
        p = r["variables"]["paginas"]
        self.assertEqual([x["propiedades"]["Nombre"] for x in p], ["Uno", "Dos", "Tres"])
        self.assertEqual(p[0]["propiedades"]["Etiquetas"], ["a"])
        consultas = [x for x in self.E.llamadas if x["ruta"].endswith("/query")]
        self.assertEqual(consultas[0]["cuerpo"]["filter"]["status"]["equals"], "Pendiente")
        props = {"nombre": "Caso {{entrada.n}}", "Estado": "Hecho", "Fecha": "2026-11-07", "Monto": "1.234,5".replace(".", ""), "Etiquetas": "a, b", "Urgente": "sí"}
        r, _ = self.correr1(nodo("c", "notion_paginas", credencial="notion-escribe", operacion="crear", base_datos=BASE_NOTION, propiedades=props,
                                 contenido="# Resumen\nTexto\n- uno\n[x] hecho", salida="pagina"),
                            nodo("a", "notion_paginas", credencial="notion-escribe", operacion="actualizar", pagina="{{pagina.id}}", propiedades={"Estado": "Pendiente"}),
                            nodo("b", "notion_paginas", credencial="notion-escribe", operacion="agregar_bloques", pagina=PAGINA_NOTION, contenido="\n".join(f"- {i}" for i in range(130))),
                            entrada={"n": "7"})
        crear, actualizar, b1, b2 = self.escritas("notion")
        pr = crear["cuerpo"]["properties"]
        self.assertEqual(pr["Nombre"], {"title": [{"type": "text", "text": {"content": "Caso 7"}}]})  # sin distinguir mayúsculas
        self.assertEqual((pr["Estado"], pr["Fecha"], pr["Monto"], pr["Etiquetas"], pr["Urgente"]),
                         ({"status": {"name": "Hecho"}}, {"date": {"start": "2026-11-07"}}, {"number": 1234.5}, {"multi_select": [{"name": "a"}, {"name": "b"}]}, {"checkbox": True}))
        self.assertEqual([x["type"] for x in crear["cuerpo"]["children"]], ["heading_1", "paragraph", "bulleted_list_item", "to_do"])
        self.assertEqual(actualizar["cuerpo"], {"properties": {"Estado": {"status": {"name": "Pendiente"}}}})
        self.assertEqual((len(b1["cuerpo"]["children"]), len(b2["cuerpo"]["children"])), (100, 30))  # en tandas de 100
        r, _ = self.correr1(nodo("c", "notion_paginas", credencial="notion-escribe", operacion="crear", base_datos=BASE_NOTION, propiedades={"Inexistente": "x"}), exigir_ok=False)
        self.assertIn("no tiene la propiedad «Inexistente»", r["motivo"])

    def test_hubspot(self):
        r, _ = self.correr1(nodo("b", "hubspot_crm", credencial="hubspot", filtros=[{"propiedad": "email", "operador": "CONTAINS_TOKEN", "valor": "{{entrada.d}}"}], salida="contactos"),
                            nodo("v", "hubspot_crm", credencial="hubspot", operacion="ver", id="ana@cliente.test", salida="ana"),
                            nodo("c", "hubspot_crm", credencial="hubspot", operacion="crear", datos={"email": "{{entrada.correo}}"}), entrada={"d": "*cliente.test", "correo": "x@cliente.test"})
        self.assertEqual([x["id"] for x in r["variables"]["contactos"]], ["501", "502"])
        busq = [x for x in self.E.llamadas if x["ruta"].endswith("/search")]
        self.assertEqual(busq[0]["cuerpo"]["filterGroups"], [{"filters": [{"propertyName": "email", "operator": "CONTAINS_TOKEN", "value": "*cliente.test"}]}])
        self.assertEqual(busq[1]["cuerpo"]["after"], "2")
        ver = next(x for x in self.E.llamadas if x["ruta"].endswith("ana@cliente.test"))
        self.assertEqual(ver["q"]["idProperty"], "email")
        self.assertTrue(self.escritura(r, "c")["bloqueado"])
        r, _ = self.correr1(nodo("c", "hubspot_crm", credencial="hubspot-escribe", operacion="crear", datos={"email": "x@cliente.test", "firstname": "{{entrada.n}}"}, salida="nuevo"),
                            nodo("a", "hubspot_crm", credencial="hubspot-escribe", objeto="negocios", operacion="actualizar", id="888", datos={"dealstage": "closedwon"}),
                            nodo("n", "hubspot_crm", credencial="hubspot-escribe", operacion="nota", id="{{nuevo.id}}", nota="Pidió cotización"), entrada={"n": "Equis"})
        crear, act, nota = self.escritas("hubspot")
        self.assertEqual(crear["cuerpo"], {"properties": {"email": "x@cliente.test", "firstname": "Equis"}})
        self.assertEqual(act["cuerpo"], {"properties": {"dealstage": "closedwon"}})
        self.assertEqual(nota["cuerpo"]["associations"][0], {"to": {"id": "777"}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]})
        r, _ = self.correr1(nodo("c", "hubspot_crm", credencial="hubspot-escribe", operacion="crear", datos={"email": "{{entrada.c}}"}), entrada={"c": "x@positiva.gov.co"},
                            exigir_ok=False)
        self.assertIn("bloqueado por seguridad", r["motivo"])
        self.assertEqual(len(self.escritas("hubspot")), 3)


class Clientes(Trabajo):
    def test_salesforce(self):
        r, _ = self.correr1(nodo("q", "salesforce_registros", credencial="sf", soql="SELECT Id, Name, Owner.Name FROM Account WHERE Name = '{{entrada.n}}'", salida="cuentas"),
                            nodo("v", "salesforce_registros", credencial="sf", operacion="ver", objeto="Contact", id="003000000000001AAA", salida="contacto"),
                            nodo("c", "salesforce_registros", credencial="sf", operacion="crear", objeto="Case", datos={"Subject": "x"}), entrada={"n": "O'Brien"})
        c = r["variables"]["cuentas"]
        self.assertEqual(([x["Name"] for x in c["filas"]], c["total"], c["truncado"]), (["Uno", "Dos", "Tres"], 3, False))
        self.assertEqual(c["filas"][0]["Owner"], {"Name": "Ana", "_tipo": "User"})  # sin «attributes»
        consulta = next(x for x in self.E.llamadas if x["ruta"].endswith("/query"))
        self.assertEqual(consulta["q"]["q"], "SELECT Id, Name, Owner.Name FROM Account WHERE Name = 'O\\'Brien'")
        self.assertEqual(self.E.tokens["salesforce"], 1)  # el token de cliente se pide una vez y se reutiliza
        self.assertEqual(r["variables"]["contacto"]["Email"], "ana@cliente.test")
        self.assertTrue(self.escritura(r, "c")["bloqueado"])
        r, _ = self.correr1(nodo("c", "salesforce_registros", credencial="sf-escribe", operacion="crear", objeto="Case", datos={"Subject": "{{entrada.s}}", "Origin": "Email"}, salida="caso"),
                            nodo("a", "salesforce_registros", credencial="sf-escribe", operacion="actualizar", objeto="Case", id="{{caso.id}}", datos={"Status": "Closed"}, salida="act"),
                            entrada={"s": "Pago"})
        crear, act = self.escritas("salesforce")
        self.assertEqual((crear["cuerpo"], act["cuerpo"], r["variables"]["act"]["cambiados"]), ({"Subject": "Pago", "Origin": "Email"}, {"Status": "Closed"}, ["Status"]))
        # el SOQL tiene que empezar por SELECT escrito tal cual: una plantilla no puede convertirlo en otra cosa
        self.assertIn("SELECT", " ".join(fd._validar_config({"id": "q", "tipo": "salesforce_registros", "config": {"credencial": "sf", "soql": "{{entrada.q}}"}})))

    def test_zoho(self):
        r, _ = self.correr1(nodo("b", "zoho_crm", credencial="zoho", modulo="Leads", criterio="(Company:equals:{{entrada.e}})", salida="leads"),
                            nodo("n", "zoho_crm", credencial="zoho", modulo="Leads", correo="nadie@cliente.test", salida="nada"),
                            nodo("c", "zoho_crm", credencial="zoho", modulo="Leads", operacion="crear", datos={"Last_Name": "Pérez"}), entrada={"e": "Acme (Colombia), S.A."})
        self.assertEqual(r["variables"]["leads"][0]["id"], "5000000001")
        self.assertEqual(r["variables"]["nada"], [])  # Zoho responde 204 cuando no hay nada
        b = next(x for x in self.E.llamadas if x["ruta"] == "/zoho/crm/v6/Leads/search")
        self.assertEqual(b["q"]["criteria"], "(Company:equals:Acme \\(Colombia\\)\\, S.A.)")
        self.assertEqual(self.E.tokens["zoho"], 1)
        self.assertTrue(self.escritura(r, "c")["bloqueado"])
        r, _ = self.correr1(nodo("c", "zoho_crm", credencial="zoho-escribe", modulo="Leads", operacion="crear", datos={"Last_Name": "Pérez", "Email": "{{entrada.c}}"}, salida="lead"),
                            entrada={"c": "p@cliente.test"})
        self.assertEqual(self.escritas("zoho")[0]["cuerpo"], {"data": [{"Last_Name": "Pérez", "Email": "p@cliente.test"}]})
        self.assertEqual(r["variables"]["lead"]["id"], "5000000009")
        r, _ = self.correr1(nodo("a", "zoho_crm", credencial="zoho-escribe", modulo="Leads", operacion="actualizar", id="5000000002", datos={"Email": "malo"}), exigir_ok=False)
        self.assertIn("INVALID_DATA", r["motivo"])


class DocuSign(Trabajo):
    def test_enviar_retenido_con_permiso_estado_y_descargar(self):
        doc = nodo("doc", "documento_generar", formato="pdf", plantilla="Contrato de {{entrada.nombre}}\n\n/firma/", nombre="contrato")
        enviar = lambda c: nodo("s", "docusign_sobre", credencial=c, documento="{{doc}}", asunto="Contrato {{entrada.nombre}}", mensaje="Por favor firma.",  # noqa: E731
                                firmantes=[{"nombre": "{{entrada.nombre}}", "correo": "{{entrada.correo}}"}], copias=[{"nombre": "Archivo", "correo": "archivo@cliente.test"}],
                                ancla="/firma/", salida="firma")
        entrada = {"nombre": "Ana", "correo": "ana@cliente.test"}
        r, _ = self.correr1(doc, enviar("ds"), entrada=entrada)
        self.assertEqual(self.E.escritas, [])
        e = self.escritura(r, "s")
        self.assertTrue(e["bloqueado"])
        self.assertEqual((e["firmantes"], e["documento"]["nombre"]), ([{"nombre": "Ana", "correo": "ana@cliente.test"}], "contrato.pdf"))
        self.assertFalse(next(m for m in r["mensajes"] if m.get("operacion") == "docusign_sobre")["enviado"])
        r, _ = self.correr1(doc, enviar("ds-escribe"), entrada=entrada)
        sobre = self.escritas("docusign")[0]["cuerpo"]
        self.assertEqual((sobre["status"], sobre["emailSubject"], sobre["documents"][0]["fileExtension"]), ("sent", "Contrato Ana", "pdf"))
        self.assertTrue(base64.b64decode(sobre["documents"][0]["documentBase64"]).startswith(b"%PDF"))
        firm = sobre["recipients"]["signers"][0]
        self.assertEqual((firm["email"], firm["routingOrder"], firm["tabs"]["signHereTabs"][0]["anchorString"]), ("ana@cliente.test", "1", "/firma/"))
        self.assertEqual(sobre["recipients"]["carbonCopies"][0]["routingOrder"], "2")
        self.assertEqual(r["variables"]["firma"]["sobre"], SOBRE)
        self.assertEqual((self.E.jwt["iss"], self.E.jwt["sub"], self.E.jwt["scope"]), ("ik-1", "u-1", "signature impersonation"))  # el doble verificó la firma RS256
        r, _ = self.correr1(nodo("e", "docusign_sobre", credencial="ds", operacion="estado", sobre=SOBRE, salida="estado"),
                            nodo("d", "docusign_sobre", credencial="ds", operacion="descargar", sobre=SOBRE, salida="firmado"))
        self.assertEqual((r["variables"]["estado"]["estado"], r["variables"]["estado"]["firmados"]), ("completed", 1))
        self.assertEqual(r["variables"]["firmado"]["nombre"], f"firmado-{SOBRE[:8]}.pdf")
        self.assertEqual(r["variables"]["firmado"]["sha256"], hashlib.sha256(b"%PDF-1.4 firmado").hexdigest())
        self.assertEqual(len(self.escritas("docusign")), 1)  # estado y descargar no escriben

    def test_nunca_firmantes_de_positiva_y_consentimiento(self):
        doc = nodo("doc", "documento_generar", formato="pdf", plantilla="x", nombre="c")
        for correo in ("jefe@positiva.gov.co", "servicioalcliente@positiva.gov.co"):
            r, ev = self.correr1(doc, nodo("s", "docusign_sobre", credencial="ds-escribe", documento="{{doc}}", asunto="x", firmantes=[{"nombre": "J", "correo": "{{entrada.c}}"}]),
                                 entrada={"c": correo}, exigir_ok=False)
            self.assertIn("bloqueado por seguridad", r["motivo"])
            self.assertTrue(any(e["tipo"] == "escritura" and e.get("vetado") for e in ev))
        self.assertEqual(self.E.escritas, [])
        p = ni.probar_conexion({"tipo": "docusign", "valores": {**self.creds["ds"]["valores"], "user_id": "sin-consentimiento"}})
        self.assertFalse(p["ok"])
        self.assertIn("consentimiento", p["detalle"])


class Conexiones(Trabajo):
    def test_probar_conexion_solo_lee(self):
        esperado = {"jira": "Jira de Ana", "trello": "Trello de @ana", "asana": "espacios: Equipo (111)", "notion": "«Positiva-IA»", "hubspot": "portal 123",
                    "sf": "14999 de 15000", "zoho": "Ana Zoho", "ds": "cuenta «Pruebas»"}
        for nombre, texto in esperado.items():
            c = self.creds[nombre]
            p = ni.probar_conexion({"tipo": c["tipo"], "valores": dict(c["valores"])})
            self.assertTrue(p["ok"], (nombre, p))
            self.assertIn(texto, p["detalle"])
        self.assertEqual(self.E.escritas, [])  # ninguna escritura: solo GET y pedidos de token
        self.assertTrue(all(x["metodo"] == "GET" or x["ruta"].endswith("/token") for x in self.E.llamadas))
        p = ni.probar_conexion({"tipo": "notion", "valores": {"token": "otro"}})
        self.assertFalse(p["ok"])
        p = ni.probar_conexion({"tipo": "zoho", "valores": {**self.creds["zoho"]["valores"], "refresh_token": "malo"}})
        self.assertIn("Zoho no entregó un token", p["detalle"])

    def test_la_cli_no_repite_secretos(self):
        orden = {"tipo": "jira", "valores": {"sitio": "https://evil.example.com", "correo": "a@b.co", "api_token": TOKEN_JIRA}}
        p = subprocess.run([sys.executable, str(AQUI / "nodos_integraciones.py"), "probar"], input=json.dumps(orden), capture_output=True, text=True, encoding="utf-8",
                           timeout=60, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertFalse(r["ok"])
        self.assertIn("atlassian.net", r["detalle"])
        self.assertNotIn(TOKEN_JIRA, p.stdout)


class Paridad(unittest.TestCase):
    MALOS = [
        ("jira_issues", {}), ("jira_issues", {"credencial": "j"}), ("jira_issues", {"credencial": "j", "operacion": "crear", "proyecto": "SOP"}),
        ("jira_issues", {"credencial": "j", "operacion": "ver", "issue": "sop 1"}), ("jira_issues", {"credencial": "j", "operacion": "comentar", "issue": "SOP-1"}),
        ("jira_issues", {"credencial": "j", "operacion": "borrar"}), ("jira_issues", {"credencial": "j", "jql": "x", "max": 0}),
        ("jira_issues", {"credencial": "j", "operacion": "crear", "proyecto": "S", "resumen": "x", "campos_extra": "no"}),
        ("jira_issues", {"credencial": "j", "operacion": "crear", "proyecto": "S", "resumen": "x", "etiquetas": ["con espacio"]}),
        ("jira_issues", {"credencial": "j", "operacion": "cambiar_estado", "issue": "SOP-1"}),
        ("trello_tarjetas", {"credencial": "t", "operacion": "listas"}), ("trello_tarjetas", {"credencial": "t", "operacion": "tarjetas"}),
        ("trello_tarjetas", {"credencial": "t", "operacion": "mover", "tarjeta": "x"}),
        ("trello_tarjetas", {"credencial": "t", "operacion": "crear", "lista": "l", "nombre": "n", "posicion": "medio"}),
        ("asana_tareas", {"credencial": "a"}), ("asana_tareas", {"credencial": "a", "operacion": "completar"}),
        ("asana_tareas", {"credencial": "a", "operacion": "crear", "nombre": "x", "vence": "mañana"}),
        ("notion_paginas", {"credencial": "n"}), ("notion_paginas", {"credencial": "n", "operacion": "crear", "base_datos": "x"}),
        ("notion_paginas", {"credencial": "n", "base_datos": "x", "filtro": "Estado=1"}), ("notion_paginas", {"credencial": "n", "operacion": "agregar_bloques", "pagina": "x"}),
        ("notion_paginas", {"credencial": "n", "base_datos": "x", "orden": ["Fecha"]}),
        ("hubspot_crm", {"credencial": "h", "objeto": "tickets"}), ("hubspot_crm", {"credencial": "h", "operacion": "actualizar", "id": "1"}),
        ("hubspot_crm", {"credencial": "h", "filtros": [{"operador": "EQ"}]}), ("hubspot_crm", {"credencial": "h", "filtros": [{"propiedad": "email", "operador": "IGUAL"}]}),
        ("hubspot_crm", {"credencial": "h", "operacion": "nota", "id": "1"}),
        ("salesforce_registros", {"credencial": "s"}), ("salesforce_registros", {"credencial": "s", "soql": "DELETE FROM Account"}),
        ("salesforce_registros", {"credencial": "s", "soql": "SELECT Id FROM A; SELECT Id FROM B"}), ("salesforce_registros", {"credencial": "s", "soql": "SELECT Id FROM A;"}),
        ("salesforce_registros", {"credencial": "s", "operacion": "crear", "objeto": "Case"}),
        ("salesforce_registros", {"credencial": "s", "operacion": "ver", "objeto": "Case", "id": "123"}),
        ("salesforce_registros", {"credencial": "s", "operacion": "crear", "objeto": "Ca se", "datos": {"a": 1}}),
        ("salesforce_registros", {"credencial": "s", "soql": "SELECT Id FROM A", "max": 5000}),
        ("zoho_crm", {"credencial": "z", "modulo": "Leads"}), ("zoho_crm", {"credencial": "z"}),
        ("zoho_crm", {"credencial": "z", "modulo": "Leads", "operacion": "actualizar", "id": "1"}), ("zoho_crm", {"credencial": "z", "modulo": "Le ads", "correo": "a@b.co"}),
        ("docusign_sobre", {"credencial": "d"}),
        ("docusign_sobre", {"credencial": "d", "documento": "{{doc}}", "asunto": "x", "firmantes": [{"nombre": "A", "correo": "a@positiva.gov.co"}]}),
        ("docusign_sobre", {"credencial": "d", "documento": "{{doc}}", "asunto": "x", "firmantes": [{"nombre": "A", "correo": "no-es-correo"}]}),
        ("docusign_sobre", {"credencial": "d", "documento": "{{doc}}", "asunto": "x", "firmantes": [{"nombre": "A"}]}),
        ("docusign_sobre", {"credencial": "d", "documento": "{{doc}}", "asunto": "x", "firmantes": [{"correo": "a@b.co", "orden": 0}]}),
        ("docusign_sobre", {"credencial": "d", "documento": "{{doc}}", "asunto": "x", "firmantes": [{"correo": "a@b.co"}], "copias": [{"correo": "c@positiva.gov.co"}]}),
        ("docusign_sobre", {"credencial": "d", "operacion": "estado", "sobre": "123"}), ("docusign_sobre", {"credencial": "d", "operacion": "descargar"}),
    ]

    def test_python_rechaza_lo_que_no_vale_y_acepta_los_ejemplos(self):
        for t, c in self.MALOS:
            self.assertTrue(fd._validar_config({"id": "n", "tipo": t, "config": c}), (t, c))
        for t in nt.TIPOS:
            s = nd.TIPOS[t]
            self.assertTrue(s.get("preparar") and s.get("consejos") and s.get("ejemplos") and s.get("descripcion"), t)
            self.assertTrue(s["escritura"], t)
            for c in [s["ejemplo"]["config"], *s["ejemplos"]]:
                self.assertEqual(fd._validar_config({"id": "n", "tipo": t, "config": c}), [], (t, c))
            for k in s["config"]["properties"]["credencial"]["x-credencial-tipos"]:
                self.assertIn(k, nd.TIPOS_CREDENCIAL)
            self.assertEqual(s["config"]["properties"]["operacion"]["enum"], nt.OPERACIONES[t])
        # las salidas dependen de la operación: las que listan pueden salir por «vacio»
        self.assertEqual(nt.salidas("jira_issues", {})[0], ["ok", "vacio"])
        self.assertEqual(nt.salidas("jira_issues", {"operacion": "crear"})[0], ["ok"])

    def test_zod_igual(self):
        dist = Path(os.environ.get("RUTA_SERVIDOR_DIST", "/app/servidor/dist")) / "flujos.js"
        node = shutil.which("node")
        if not node or not dist.exists():
            self.skipTest("no hay servidor compilado o node")
        buenos = [{"tipo": t, "config": c} for t in sorted(nt.TIPOS) for c in [nd.TIPOS[t]["ejemplo"]["config"], *nd.TIPOS[t]["ejemplos"]]]
        guion = ("const f = await import(process.argv[1]); const d = JSON.parse(process.argv[2]);"
                 "const r = (xs) => xs.map((e) => f.CONFIG_DE[e.tipo].safeParse(e.config).success);"
                 "console.log(JSON.stringify({tipos: [...f.TIPOS_DE_NODO], buenos: r(d.buenos), malos: r(d.malos)}));")
        p = subprocess.run([node, "--input-type=module", "-e", guion, dist.as_uri(), json.dumps({"buenos": buenos, "malos": [{"tipo": t, "config": c} for t, c in self.MALOS]})],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertTrue(set(nt.TIPOS) <= set(r["tipos"]))
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
        esperados = {"jira": ["api_token"], "trello": ["api_key", "token"], "asana": ["token"], "notion": ["token"], "hubspot": ["token"], "salesforce": ["client_secret"],
                     "zoho": ["client_secret", "refresh_token"], "docusign": ["private_key"]}
        for t, secretos in esperados.items():
            self.assertEqual(tipos.get(t), secretos, t)
        self.assertTrue(set(tipos) >= set(nd.TIPOS_CREDENCIAL) - set(nd.TIPOS_CREDENCIAL_IA))


class Piezas(unittest.TestCase):
    def test_adf_bloques_y_escapes(self):
        self.assertEqual(c_tr.adf_a_texto(c_tr.adf("uno\n- a\n- b")), "uno\n- a\n- b")
        self.assertEqual(c_tr.id_notion("https://www.notion.so/x/Mi-pagina-2f1c4b5a6d7e8f90a1b2c3d4e5f60718", "p"), "2f1c4b5a-6d7e-8f90-a1b2-c3d4e5f60718")
        self.assertEqual(c_tr.id_trello("https://trello.com/c/AbCd1234/12-tarjeta", "t"), "AbCd1234")
        self.assertEqual(c_tr.gid("https://app.asana.com/0/1204567890123456/1209999999999999", "g"), "1209999999999999")
        self.assertEqual(c_tr.escapar_zoho("a(b),c"), "a\\(b\\)\\,c")
        with self.assertRaises(c_tr.ErrorDeNegocio):
            c_tr.revisar_datos({"a": [{"b": "Para: x@sub.positiva.gov.co"}]})
        c_tr.revisar_datos({"a": "ana@cliente.test"})
        with self.assertRaises(c_tr.ErrorDeNegocio):
            c_tr.sf_instancia({"instancia": "https://login.evil.com"})
        self.assertEqual(c_tr.sf_instancia({"instancia": "https://acme.my.salesforce.com/"}), "https://acme.my.salesforce.com")
        with self.assertRaises(c_tr.ErrorDeNegocio):
            c_tr.jira_base({"sitio": "https://acme.atlassian.net.evil.com"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
