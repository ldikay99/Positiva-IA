"""
Pruebas de las integraciones (plan, sección 5d) con dobles locales: un solo
servidor http.server en 127.0.0.1 hace de Gmail, Calendar, Docs, Drive, Forms,
Microsoft Graph, los servidores de tokens de Google y Microsoft y una API
«petstore» descrita con OpenAPI. Nada sale a internet y no hay cuentas reales:
todo lo de Google y Microsoft está probado SOLO contra estos dobles.

    PYTHONPATH=motor python puente/pruebas_integraciones.py        (desde la raíz; en el contenedor: /app)
"""
from __future__ import annotations

import base64
import email
import email.policy
import json
import os
import shutil
import subprocess
import sys
import threading
import time
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
from conectores import google as c_google  # noqa: E402
from conectores import microsoft as c_ms  # noqa: E402
from conectores import oauth as c_oauth  # noqa: E402
from conectores import sheets as c_sheets  # noqa: E402
from pruebas_declarativo import Plataforma, cred, flujo, nodo  # noqa: E402

AHORA = 1_794_300_000.0  # 2026-11-10 (aprox.), fijo para que las fechas no dependan del reloj
PETSTORE = {
    "openapi": "3.0.0", "info": {"title": "Petstore", "version": "1.0.0"}, "servers": [{"url": "/api"}],
    "paths": {
        "/pets": {
            "get": {"operationId": "listPets", "summary": "Lista mascotas",
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}, {"name": "cursor", "in": "query", "schema": {"type": "string"}}]},
            "post": {"operationId": "createPets", "summary": "Crea una mascota", "requestBody": {"required": True, "content": {"application/json": {}}}},
        },
        "/pets/{petId}": {"get": {"operationId": "showPetById", "parameters": [{"$ref": "#/components/parameters/PetId"}]}},
    },
    "components": {"parameters": {"PetId": {"name": "petId", "in": "path", "required": True, "schema": {"type": "integer"}}}},
}


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


class Estado:
    def __init__(self) -> None:
        self.llamadas: list = []
        self.tokens = {"google": 0, "ms": 0}
        self.enviados: list = []
        self.etiquetas = [{"id": "INBOX", "name": "INBOX"}, {"id": "UNREAD", "name": "UNREAD"}]
        self.historia = 100
        self.nuevos: list = []
        self.eventos: list = []
        self.busy: list = []
        self.subidas: list = []
        self.trozos: list = []
        self.items_lista: list = []
        self.filas_tabla: list = []
        self.delta_vuelta = 0


class Doble(BaseHTTPRequestHandler):
    E: Estado = Estado()
    B = ""

    def log_message(self, *a) -> None:  # noqa: D401
        pass

    def _cuerpo(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json(self, codigo: int, x, encabezados=None) -> None:
        d = json.dumps(x).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        for k, v in (encabezados or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def _bytes(self, codigo: int, d: bytes, tipo: str) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def do_GET(self):
        self._todo("GET")

    def do_POST(self):
        self._todo("POST")

    def do_PATCH(self):
        self._todo("PATCH")

    def do_PUT(self):
        self._todo("PUT")

    def _todo(self, metodo: str) -> None:
        E = self.E
        u = urllib.parse.urlsplit(self.path)
        ruta, q = urllib.parse.unquote(u.path), dict(urllib.parse.parse_qsl(u.query))
        cuerpo = self._cuerpo()
        E.llamadas.append({"metodo": metodo, "ruta": ruta, "q": q, "cuerpo": cuerpo, "auth": self.headers.get("Authorization", ""),
                           "tipo": self.headers.get("Content-Type", ""), "rango": self.headers.get("Content-Range", "")})
        j = json.loads(cuerpo) if cuerpo[:1] in (b"{", b"[") else {}
        # ------------------------------------------------------------- tokens
        if ruta == "/token/google" or ruta.startswith("/token/ms/"):
            f = dict(urllib.parse.parse_qsl(cuerpo.decode()))
            if f.get("refresh_token") == "vencido":
                return self._json(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            clave = "google" if "google" in ruta else "ms"
            E.tokens[clave] += 1
            r = {"access_token": f"acceso-{clave}-{E.tokens[clave]}", "expires_in": 3600}
            if clave == "ms" and f.get("grant_type") == "refresh_token":
                r["refresh_token"] = "refresco-ms-rotado"
            return self._json(200, r)
        # -------------------------------------------------------------- Gmail
        if ruta.startswith("/gmail"):
            r = ruta[len("/gmail"):]
            if r == "/profile":
                return self._json(200, {"emailAddress": "ana@empresa.test", "historyId": str(E.historia)})
            if r == "/labels" and metodo == "GET":
                return self._json(200, {"labels": E.etiquetas})
            if r == "/labels":
                nueva = {"id": f"Label_{len(E.etiquetas)}", "name": j["name"]}
                E.etiquetas.append(nueva)
                return self._json(200, nueva)
            if r == "/messages" and metodo == "GET":
                ids = ["m1", "m2"] if "factura" in q.get("q", "") or not q.get("q") else []
                return self._json(200, {"messages": [{"id": x} for x in ids + E.nuevos if x not in ("",)]})
            if r == "/history":
                if q.get("startHistoryId") == "1":
                    return self._json(404, {"error": {"message": "Requested entity was not found."}})
                return self._json(200, {"history": [{"messagesAdded": [{"message": {"id": x}}]} for x in E.nuevos], "historyId": str(E.historia + 5)})
            if r.startswith("/messages/") and r.endswith("/modify"):
                return self._json(200, {"id": r.split("/")[2], "labelIds": j.get("addLabelIds", [])})
            if "/attachments/" in r:
                return self._json(200, {"data": _b64u(b"%PDF-1.4 factura de prueba")})
            if r.startswith("/messages/") and metodo == "GET":
                mid = r.split("/")[2]
                return self._json(200, {"id": mid, "threadId": f"hilo-{mid}", "labelIds": ["INBOX", "UNREAD"], "snippet": "Hola",
                                        "payload": {"mimeType": "multipart/mixed", "headers": [
                                            {"name": "From", "value": "Proveedor <proveedor@cliente.test>"}, {"name": "To", "value": "ana@empresa.test"},
                                            {"name": "Cc", "value": "otro@cliente.test"},
                                            {"name": "Subject", "value": f"Factura {mid}"}, {"name": "Message-ID", "value": f"<{mid}@cliente.test>"},
                                            {"name": "Date", "value": "Tue, 10 Nov 2026 10:00:00 -0500"}],
                                            "parts": [{"mimeType": "text/plain", "body": {"data": _b64u("Adjunto la factura.".encode())}},
                                                      {"mimeType": "application/pdf", "filename": "factura.pdf", "body": {"attachmentId": "adj1", "size": 26}}]}})
            if r == "/messages/send":
                E.enviados.append(j)
                return self._json(200, {"id": "enviado-1", "threadId": j.get("threadId") or "hilo-nuevo"})
        # ----------------------------------------------------------- Calendar
        if ruta.startswith("/calendar"):
            r = ruta[len("/calendar"):]
            if r == "/freeBusy":
                return self._json(200, {"calendars": {i["id"]: {"busy": E.busy} for i in j["items"]}})
            if r.endswith("/events") and metodo == "GET":
                return self._json(200, {"items": E.eventos})
            if r.endswith("/events"):
                ev = {"id": "ev1", "htmlLink": "https://calendar.google.com/ev1", **j,
                      **({"conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc"}]}} if j.get("conferenceData") else {})}
                E.eventos.append(ev)
                return self._json(200, ev)
            if "/events/" in r:
                return self._json(200, {"id": r.rsplit("/", 1)[-1], "start": {"dateTime": "2026-11-10T10:00:00-05:00"}, "end": {"dateTime": "2026-11-10T11:00:00-05:00"},
                                        "attendees": [{"email": "uno@cliente.test"}], **j})
        # ------------------------------------------------------- Drive y Docs
        if ruta.startswith("/drive/files/") and ruta.endswith("/copy"):
            return self._json(200, {"id": "copia-doc-123456", "name": j.get("name"), "webViewLink": "https://docs.google.com/copia"})
        if ruta.startswith("/drive/files/") and ruta.endswith("/export"):
            return self._bytes(200, b"%PDF-1.4 exportado" if q.get("mimeType") == "application/pdf" else _docx(), q.get("mimeType", ""))
        if ruta.startswith("/drive/files"):
            return self._json(200, {"files": [{"id": "archivo-1234567890", "name": "informe.pdf", "mimeType": "application/pdf"}]})
        if ruta.startswith("/docs/") and ruta.endswith(":batchUpdate"):
            return self._json(200, {"replies": [{"replaceAllText": {"occurrencesChanged": 1}} for _ in j.get("requests", [])]})
        if ruta.startswith("/docs/"):
            return self._json(200, {"body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Hola {{nombre}}, radicado {{radicado}}"}}]}}]}})
        if ruta.startswith("/forms/"):
            if ruta.endswith("/responses"):
                return self._json(200, {"responses": [{"responseId": "r1", "lastSubmittedTime": "2026-11-09T10:00:00Z", "respondentEmail": "x@cliente.test",
                                                       "answers": {"q1": {"textAnswers": {"answers": [{"value": "Ana"}]}}}}]})
            return self._json(200, {"info": {"title": "Inscripción"}, "items": [{"title": "Nombre", "questionItem": {"question": {"questionId": "q1"}}}]})
        if ruta.startswith("/sheets/"):
            return self._json(200, {"values": [["nombre", "monto"], ["Ana", 10]]})
        # -------------------------------------------------------------- Graph
        if ruta.startswith("/graph"):
            return self._graph(metodo, ruta[len("/graph"):], q, j, cuerpo)
        if ruta.startswith("/subida/"):
            E.trozos.append(self.headers.get("Content-Range"))
            fin = int(self.headers["Content-Range"].split("/")[-1])
            if self.headers["Content-Range"].split("-")[1].split("/")[0] == str(fin - 1):
                return self._json(201, {"id": "grande-1", "name": "grande.bin", "size": fin, "webUrl": "https://onedrive/grande"})
            return self._json(202, {"nextExpectedRanges": []})
        # ------------------------------------------------------------ OpenAPI
        if ruta == "/spec.json":
            return self._json(200, PETSTORE)
        if ruta == "/api/pets" and metodo == "GET":
            pagina = q.get("cursor") or "0"
            sig = {"0": "c1", "c1": "c2"}.get(pagina)
            return self._json(200, {"items": [{"id": f"{pagina}-{i}"} for i in range(2)], **({"next": sig} if sig else {})})
        if ruta == "/api/pets":
            return self._json(201, {"id": j.get("id"), "creada": True})
        if ruta.startswith("/api/pets/"):
            return self._json(200, {"id": int(ruta.rsplit("/", 1)[-1]), "name": "Firulais"})
        # ------------------------------------------------------- http avanzado
        if ruta == "/lista":
            p = int(q.get("page") or 1)
            return self._json(200, {"data": [p * 10, p * 10 + 1] if p <= 3 else []})
        if ruta == "/enlace":
            p = int(q.get("p") or 1)
            enc = {"Link": f'<{self.B}/enlace?p={p + 1}>; rel="next"'} if p < 2 else {}
            return self._json(200, [p], enc)
        if ruta.startswith("/descarga/"):
            return self._bytes(200, b"texto del contrato", "text/plain")
        if ruta == "/archivo.pdf":
            return self._bytes(200, b"%PDF-1.4 descargado", "application/pdf")
        if ruta == "/subir":
            return self._json(200, {"tipo": self.headers.get("Content-Type", ""), "bytes": len(cuerpo)})
        return self._json(404, {"error": {"message": f"no existe {ruta}"}})

    def _graph(self, metodo, r, q, j, cuerpo):
        E = self.E
        if r.startswith("/me/mailFolders") and "/messages/delta" in r:
            E.delta_vuelta += 1
            value = [{"id": "g-viejo"}] if E.delta_vuelta == 1 else [{"id": x} for x in E.nuevos]
            return self._json(200, {"value": value, "@odata.deltaLink": f"{self.B}/graph/me/mailFolders/inbox/messages/delta?$deltatoken=t{E.delta_vuelta}"})
        if r.startswith("/me/mailFolders") and r.endswith("/messages"):
            return self._json(200, {"value": [_msg("g1", "Reunión del lunes", True)]})
        if r.startswith("/me/mailFolders"):
            return self._json(200, {"value": [{"id": "carpeta-atendidos-" + "x" * 40, "displayName": "Atendidos"}]})
        if r.endswith("/attachments") and metodo == "GET":
            return self._json(200, {"value": [{"name": "agenda.pdf", "contentType": "application/pdf", "size": 9, "contentBytes": base64.b64encode(b"%PDF-1.4 x").decode()}]})
        if r == "/me/sendMail":
            E.enviados.append(j)
            return self._bytes(202, b"", "text/plain")
        if r.endswith("/createReply") or r.endswith("/createReplyAll"):
            return self._json(201, {"id": "borrador-1", "toRecipients": [{"emailAddress": {"address": "proveedor@cliente.test"}}]})
        if r.endswith("/send") or r.endswith("/attachments"):
            return self._json(202, {})
        if r.endswith("/move"):
            return self._json(201, {"id": "movido-1"})
        if r.startswith("/me/messages/") and metodo == "PATCH":
            return self._json(200, {"categories": j.get("categories", [])})
        if r.startswith("/me/messages/"):
            return self._json(200, {**_msg(r.split("/")[3], "Hola", False), "categories": ["Vieja"]})
        if r.startswith("/me/calendar/events"):
            E.eventos.append(j)
            return self._json(201, {"id": "evo1", "webLink": "https://outlook/evo1", "onlineMeeting": {"joinUrl": "https://teams/x"} if j.get("isOnlineMeeting") else None, **j})
        if r.startswith("/me/calendar/calendarView"):
            return self._json(200, {"value": [{"id": "e1", "subject": "Comité", "start": {"dateTime": "2026-11-10T10:00:00"}, "end": {"dateTime": "2026-11-10T11:00:00"}}]})
        if r.startswith("/me/drive/root:") and r.endswith("/createUploadSession"):
            return self._json(200, {"uploadUrl": f"{self.B}/subida/sesion1"})
        if r.startswith("/me/drive/root:") and r.endswith(":/content") and metodo == "PUT":
            E.subidas.append({"ruta": r, "bytes": len(cuerpo), "conflicto": q.get("@microsoft.graph.conflictBehavior")})
            return self._json(201, {"id": "od-1", "name": r.split(":")[1].rsplit("/", 1)[-1], "size": len(cuerpo), "webUrl": "https://onedrive/od-1"})
        if r.endswith("/workbook/tables/Registro/headerRowRange"):
            return self._json(200, {"values": [["Fecha", "Asunto"]]})
        if r.endswith("/workbook/tables/Registro/dataBodyRange"):
            return self._json(200, {"values": [["2026-11-01", "uno"]] + E.filas_tabla})
        if r.endswith("/workbook/tables/Registro/rows"):
            E.filas_tabla += j["values"]
            return self._json(201, {"index": len(E.filas_tabla)})
        if r.startswith("/me/drive/root/children") or (r.startswith("/me/drive/root:") and r.endswith(":/children")):
            return self._json(200, {"value": [{"id": "it1", "name": "contrato.txt", "file": {"mimeType": "text/plain"}, "size": 20}]})
        if r.startswith("/me/drive/root:") and r.endswith(":/content"):
            self.send_response(302)
            self.send_header("Location", f"{self.B}/descarga/contrato.txt")
            self.end_headers()
            return
        if r.startswith("/me/drive/root:"):
            return self._json(200, {"id": "it1", "name": "contrato.txt", "file": {"mimeType": "text/plain"}})
        if r.startswith("/sites/contoso.sharepoint.com:"):
            return self._json(200, {"id": "contoso,sitio1"})
        if r.startswith("/sites/") and "/lists" in r and r.endswith("/items") and metodo == "POST":
            E.items_lista.append(j)
            return self._json(201, {"id": "7", "webUrl": "https://contoso/7"})
        if r.startswith("/sites/") and "/lists/" in r and r.endswith("/items"):
            return self._json(200, {"value": [{"id": "1", "fields": {"Title": "Pendiente uno", "Estado": "Pendiente", "@odata.etag": "x"}}]})
        if r.startswith("/sites/") and r.endswith("/lists"):
            return self._json(200, {"value": [{"id": "lista-1"}]})
        if r.startswith("/teams/") or r.startswith("/chats/"):
            E.enviados.append(j)
            return self._json(201, {"id": "msj-1", "webUrl": "https://teams/msj-1"})
        if r.startswith("/me"):
            return self._json(200, {"mail": "ana@empresa.test", "displayName": "Ana"})
        return self._json(404, {"error": {"message": f"Graph no tiene {r}"}})


def _msg(gid, asunto, adjuntos):
    return {"id": gid, "internetMessageId": f"<{gid}@cliente.test>", "conversationId": "conv-1", "subject": asunto,
            "from": {"emailAddress": {"name": "Proveedor", "address": "proveedor@cliente.test"}}, "toRecipients": [{"emailAddress": {"address": "ana@empresa.test"}}],
            "receivedDateTime": "2026-11-10T15:00:00Z", "body": {"contentType": "text", "content": "¿Nos vemos el lunes a las 10?"}, "hasAttachments": adjuntos, "isRead": False}


def _docx() -> bytes:
    try:
        import docx
        import io
        d = docx.Document()
        d.add_paragraph("Hola {{nombre}}, su radicado es {{radicado}}.")
        s = io.BytesIO()
        d.save(s)
        return s.getvalue()
    except ImportError:
        return b"PK sin python-docx"


def oauth_g(permitir=False, **extra):
    v = {"client_id": "cid", "client_secret": "secreto-google-XYZ", "refresh_token": "refresco-g", "access_token": "acceso-viejo",
         "expira": int((time.time() + 3000) * 1000), "cuenta": "ana@empresa.test", **extra}
    return cred("google_oauth", permitir, ["client_secret", "refresh_token", "access_token"], **v)


def oauth_m(permitir=False, **extra):
    v = {"tenant": "contoso.onmicrosoft.com", "client_id": "cid", "client_secret": "secreto-ms-XYZ", "refresh_token": "refresco-m", "access_token": "acceso-m",
         "expira": int((time.time() + 3000) * 1000), "cuenta": "ana@empresa.test", **extra}
    return cred("microsoft_oauth", permitir, ["client_secret", "refresh_token", "access_token"], **v)


class Integraciones(Plataforma):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Doble)
        cls.B = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        Doble.B = cls.B
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        super().setUp()
        Doble.E = self.E = Estado()
        fd._IPS_PERMITIDAS_PRUEBAS.add("127.0.0.1")
        self._prev = (dict(c_google.BASES), c_ms.GRAPH, dict(c_oauth.ENDPOINTS), c_sheets.BASE_TOKEN, ni._reloj)
        c_google.BASES.update({k: f"{self.B}/{k}" for k in ("gmail", "calendar", "docs", "forms", "drive")})
        c_google.BASES["drive_subida"] = f"{self.B}/upload"
        c_ms.GRAPH = f"{self.B}/graph"
        c_oauth.ENDPOINTS.update({"google_token": f"{self.B}/token/google", "microsoft_token": f"{self.B}/token/ms/{{tenant}}"})
        c_sheets.BASE_TOKEN = f"{self.B}/sheets"
        c_oauth.olvidar()
        ni._specs.clear()
        self.creds.update({"gmail": oauth_g(), "gmail-escribe": oauth_g(True), "m365": oauth_m(), "m365-escribe": oauth_m(True),
                           "petstore": cred("api_openapi", False, ["token"], spec_url=f"{self.B}/spec.json", auth="api_key", llave_nombre="X-API-Key", token="llave-PET-123"),
                           "petstore-escribe": cred("api_openapi", True, ["token"], spec_url=f"{self.B}/spec.json", auth="bearer", token="tok-PET-456")})

    def tearDown(self):
        c_google.BASES.clear()
        c_google.BASES.update(self._prev[0])
        c_ms.GRAPH = self._prev[1]
        c_oauth.ENDPOINTS.clear()
        c_oauth.ENDPOINTS.update(self._prev[2])
        c_sheets.BASE_TOKEN, ni._reloj = self._prev[3], self._prev[4]
        super().tearDown()

    def llamadas(self, texto):
        return [x for x in self.E.llamadas if texto in x["ruta"]]

    def correr1(self, *nodos, aristas=None, exigir_ok=True, **orden):
        ns = [nodo("inicio", "entrada_manual")] + list(nodos) + [nodo("fin", "salida", desenlace="manual")]
        d = flujo(ns, aristas if aristas is not None else [{"desde": a["id"], "hacia": b["id"]} for a, b in zip(ns, ns[1:])])
        self.assertEqual(fd.validar(d), [])
        return self.correr_orden(d, exigir_ok=exigir_ok, **orden)


class Gmail(Integraciones):
    def test_buscar_deja_los_adjuntos_como_archivos(self):
        r, _ = self.correr1(nodo("buscar", "gmail_buscar", credencial="gmail", consulta="factura", salida="correos"))
        correos = r["variables"]["correos"]
        self.assertEqual([x["asunto"] for x in correos], ["Factura m1", "Factura m2"])
        self.assertTrue(correos[0]["adjuntos"][0]["id"].startswith("a-"))
        self.assertTrue(any(a["nombre"] == "factura.pdf" for a in r["archivos"]))
        self.assertTrue(all(x["auth"] == "Bearer acceso-viejo" for x in self.llamadas("/gmail/")))

    def test_enviar_queda_retenido_sin_permiso_y_sale_con_permiso(self):
        r, _ = self.correr1(nodo("doc", "documento_generar", formato="pdf", plantilla="Informe", nombre="informe"),
                            nodo("env", "gmail_enviar", credencial="gmail", para="cliente@cliente.test", asunto="Informe", cuerpo="Adjunto", adjuntos=["{{doc}}"]))
        w = next(x for x in r["escrituras"] if x["nodo"] == "env")
        self.assertTrue(w["bloqueado"])
        self.assertEqual(w["adjuntos"][0]["nombre"], "informe.pdf")
        self.assertEqual(w["cuerpo"], "Adjunto")
        self.assertEqual(self.E.enviados, [])
        r, _ = self.correr1(nodo("doc", "documento_generar", formato="pdf", plantilla="Informe", nombre="informe"),
                            nodo("env", "gmail_enviar", credencial="gmail-escribe", para="cliente@cliente.test", asunto="Informe", cuerpo="Adjunto", adjuntos=["{{doc}}"]))
        m = email.message_from_bytes(base64.urlsafe_b64decode(self.E.enviados[0]["raw"] + "=="), policy=email.policy.default)
        self.assertEqual(m["To"], "cliente@cliente.test")
        self.assertEqual([a.get_filename() for a in m.iter_attachments()], ["informe.pdf"])

    def test_bloqueos_de_positiva(self):
        r, _ = self.correr1(nodo("env", "gmail_enviar", credencial="gmail-escribe", para="alguien@positiva.gov.co", asunto="x", cuerpo="x"), exigir_ok=False)
        self.assertEqual(r["accion"], "error")
        self.assertIn("Positiva", r["motivo"])
        self.creds["gmail-positiva"] = oauth_g(True, cuenta="ana@positiva.gov.co")
        r, _ = self.correr1(nodo("b", "gmail_buscar", credencial="gmail-positiva"), exigir_ok=False)
        self.assertIn("Positiva", r["motivo"])
        self.assertEqual(self.E.enviados, [])

    def test_responder_en_el_hilo_y_etiquetar(self):
        r, _ = self.correr1(nodo("resp", "gmail_responder", credencial="gmail-escribe", mensaje="m1", cuerpo="Recibido", todos=True),
                            nodo("etq", "gmail_etiquetar", credencial="gmail-escribe", mensaje="m1", agregar=["Procesado"], marcar_leido=True))
        env = self.E.enviados[0]
        self.assertEqual(env["threadId"], "hilo-m1")
        m = email.message_from_bytes(base64.urlsafe_b64decode(env["raw"] + "=="), policy=email.policy.default)
        self.assertEqual(m["In-Reply-To"], "<m1@cliente.test>")
        self.assertEqual(m["Subject"], "Re: Factura m1")
        self.assertIn("otro@cliente.test", m["Cc"])
        self.assertIn("> Adjunto la factura.", m.get_body().get_content())
        mod = json.loads(self.llamadas("/modify")[0]["cuerpo"])
        self.assertEqual(mod, {"addLabelIds": ["Label_2"], "removeLabelIds": ["UNREAD"]})

    def test_token_vencido_se_renueva_y_uno_revocado_avisa(self):
        self.creds["gmail"] = oauth_g(expira=int((time.time() - 10) * 1000))
        self.correr1(nodo("b", "gmail_buscar", credencial="gmail"))
        self.assertEqual(self.E.tokens["google"], 1)
        self.assertTrue(all(x["auth"] == "Bearer acceso-google-1" for x in self.llamadas("/gmail/")))
        c_oauth.olvidar()
        self.creds["gmail"] = oauth_g(expira=0, refresh_token="vencido")
        r, _ = self.correr1(nodo("b", "gmail_buscar", credencial="gmail"), exigir_ok=False)
        self.assertIn("vuelve a conectarla", r["motivo"])
        self.assertNotIn("secreto-google-XYZ", json.dumps(r))


class Calendario(Integraciones):
    def test_crear_con_meet_e_invitados(self):
        r, _ = self.correr1(nodo("ag", "calendar_crear", credencial="gmail-escribe", titulo="Revisión", inicio="2026-11-12 10:00", duracion_min=45,
                                 asistentes="uno@cliente.test, dos@cliente.test", con_meet=True, salida="ev"))
        self.assertEqual(r["variables"]["ev"]["meet"], "https://meet.google.com/abc")
        c = self.llamadas("/events")[0]
        self.assertEqual(c["q"]["sendUpdates"], "all")
        cuerpo = json.loads(c["cuerpo"])
        self.assertEqual(cuerpo["start"]["dateTime"], "2026-11-12T10:00:00-05:00")
        self.assertEqual(cuerpo["end"]["dateTime"], "2026-11-12T10:45:00-05:00")
        self.assertEqual([a["email"] for a in cuerpo["attendees"]], ["uno@cliente.test", "dos@cliente.test"])
        r, _ = self.correr1(nodo("ag", "calendar_crear", credencial="gmail-escribe", titulo="x", inicio="2026-11-12 10:00", asistentes=["jefe@positiva.gov.co"]),
                            exigir_ok=False)
        self.assertIn("Positiva", r["motivo"])

    def test_disponibilidad_y_franjas(self):
        self.E.busy = [{"start": "2026-11-12T08:00:00-05:00", "end": "2026-11-12T09:30:00-05:00"}]
        r, _ = self.correr1(nodo("d", "calendar_disponibilidad", credencial="gmail", desde="2026-11-12 08:00", hasta="2026-11-12 17:00", duracion_min=30, salida="agenda"),
                            aristas=[{"desde": "inicio", "hacia": "d"}, {"desde": "d", "hacia": "fin"}])
        ag = r["variables"]["agenda"]
        self.assertFalse(ag["libre"])
        self.assertEqual(ag["franjas"][0]["inicio"], "2026-11-12T09:30:00-05:00")

    def test_actualizar_conserva_la_duracion_y_retiene_sin_permiso(self):
        r, _ = self.correr1(nodo("mv", "calendar_actualizar", credencial="gmail", evento="ev12345678", inicio="2026-11-13 15:00", agregar_asistentes="tres@cliente.test"))
        w = next(x for x in r["escrituras"] if x["nodo"] == "mv")
        self.assertTrue(w["bloqueado"])
        self.assertEqual(w["cambios"]["end"]["dateTime"], "2026-11-13T16:00:00-05:00")
        self.assertEqual([a["email"] for a in w["cambios"]["attendees"]], ["uno@cliente.test", "tres@cliente.test"])
        self.assertFalse([x for x in self.E.llamadas if x["metodo"] == "PATCH"])


class DocsYForms(Integraciones):
    def test_docs_en_drive_con_permiso(self):
        r, _ = self.correr1(nodo("d", "docs_crear_desde_plantilla", credencial="gmail-escribe", plantilla="https://docs.google.com/document/d/1PlantillaDoc0123/edit",
                                 variables={"nombre": "Ana", "radicado": "R-1"}, en_drive=True, exportar="pdf", nombre="Carta Ana", salida="documento"))
        doc = r["variables"]["documento"]
        self.assertEqual(doc["nombre"], "Carta Ana.pdf")
        self.assertEqual(doc["doc_id"], "copia-doc-123456")
        pedidos = json.loads(self.llamadas(":batchUpdate")[0]["cuerpo"])["requests"]
        self.assertEqual({p["replaceAllText"]["containsText"]["text"]: p["replaceAllText"]["replaceText"] for p in pedidos}, {"{{nombre}}": "Ana", "{{radicado}}": "R-1"})

    def test_docs_sin_permiso_llena_aqui_sin_copiar(self):
        try:
            import docx  # noqa: F401
        except ImportError:
            self.skipTest("sin python-docx")
        r, _ = self.correr1(nodo("d", "docs_crear_desde_plantilla", credencial="gmail", plantilla="1PlantillaDoc0123", variables={"nombre": "Ana", "radicado": "R-9"},
                                 exportar="docx", en_drive=True, salida="documento"))
        self.assertFalse(self.llamadas("/copy"))
        self.assertTrue(next(x for x in r["escrituras"] if x["nodo"] == "d")["bloqueado"])
        import documentos
        ruta = next(Path(p) for p in (self.archivos / "prueba" / "entradas").rglob("*.docx"))
        self.assertIn("R-9", documentos.texto_docx(ruta.read_bytes()))

    def test_forms(self):
        r, _ = self.correr1(nodo("f", "forms_respuestas", credencial="gmail", formulario="1FAIpQLSdEjemplo0123", desde="2026-11-01", salida="respuestas"),
                            aristas=[{"desde": "inicio", "hacia": "f"}, {"desde": "f", "hacia": "fin"}])
        self.assertEqual(r["variables"]["respuestas"][0]["respuestas"], {"Nombre": "Ana"})
        self.assertEqual(self.llamadas("/responses")[0]["q"]["filter"], "timestamp > 2026-11-01T05:00:00Z")

    def test_sheets_y_drive_con_oauth(self):
        r, _ = self.correr1(nodo("h", "hoja_leer", origen="sheets", credencial="gmail", hoja_id="1" + "a" * 25, salida="filas"),
                            nodo("l", "drive_listar", credencial="gmail", salida="lista"),
                            aristas=[{"desde": "inicio", "hacia": "h"}, {"desde": "h", "hacia": "l"}, {"desde": "l", "hacia": "fin"}])
        self.assertEqual(r["variables"]["filas"], [{"nombre": "Ana", "monto": 10}])
        self.assertEqual(r["variables"]["lista"][0]["nombre"], "informe.pdf")


class Microsoft365(Integraciones):
    def test_buscar_responder_mover_y_categorizar(self):
        r, _ = self.correr1(nodo("b", "outlook_buscar", credencial="m365-escribe", salida="correos"),
                            nodo("r", "outlook_responder", credencial="m365-escribe", mensaje="{{correos.0.graph_id}}", cuerpo="Listo", adjuntos=["{{correos.0.adjuntos}}"]),
                            nodo("m", "outlook_mover", credencial="m365-escribe", mensaje="g1", carpeta="Atendidos"),
                            nodo("c", "outlook_categorizar", credencial="m365-escribe", mensaje="g1", categorias=["Atendido"], marcar_leido=True))
        self.assertEqual(r["variables"]["correos"][0]["adjuntos"][0]["nombre"], "agenda.pdf")
        rutas = [x["ruta"] for x in self.E.llamadas if x["metodo"] != "GET"]
        self.assertIn("/graph/me/messages/g1/createReply", rutas)
        self.assertIn("/graph/me/messages/borrador-1/send", rutas)
        self.assertIn("/graph/me/messages/borrador-1/attachments", rutas)
        self.assertEqual(json.loads(self.llamadas("/move")[0]["cuerpo"])["destinationId"], "carpeta-atendidos-" + "x" * 40)
        patch = [json.loads(x["cuerpo"]) for x in self.E.llamadas if x["metodo"] == "PATCH" and x["ruta"] == "/graph/me/messages/g1"]
        self.assertEqual(patch[0], {"categories": ["Vieja", "Atendido"], "isRead": True})

    def test_enviar_retenido_y_calendario_con_teams(self):
        r, _ = self.correr1(nodo("e", "outlook_enviar", credencial="m365", para="a@cliente.test", asunto="Hola", cuerpo="x"),
                            nodo("c", "outlook_calendario_crear", credencial="m365-escribe", titulo="Comité", inicio="2026-11-12 09:00", asistentes="a@cliente.test",
                                 con_teams=True, salida="ev"))
        self.assertTrue(next(x for x in r["escrituras"] if x["nodo"] == "e")["bloqueado"])
        self.assertEqual(self.E.enviados, [])
        self.assertEqual(r["variables"]["ev"]["teams"], "https://teams/x")
        ev = self.E.eventos[0]
        self.assertEqual(ev["start"], {"dateTime": "2026-11-12T09:00:00", "timeZone": "America/Bogota"})
        self.assertTrue(ev["isOnlineMeeting"])

    def test_onedrive_subir_en_partes_y_leer(self):
        grande = self.archivos / "prueba" / "entradas" / "a-grande0001"
        grande.mkdir(parents=True)
        (grande / "grande.bin").write_bytes(b"x" * (6 * 1024 * 1024))
        (grande / "meta.json").write_text(json.dumps({"id": "a-grande0001", "nombre": "grande.bin", "tipo": "application/octet-stream"}), encoding="utf-8")
        r, _ = self.correr1(nodo("s", "onedrive_subir", credencial="m365-escribe", carpeta="/Informes", archivo="a-grande0001", salida="subido"),
                            nodo("t", "onedrive_subir", credencial="m365-escribe", carpeta="/Datos", nombre="nota.txt", contenido="hola"),
                            nodo("l", "onedrive_leer", credencial="m365", archivo="/Contratos/contrato.txt", como="archivo", salida="contrato"))
        self.assertEqual(self.E.trozos, ["bytes 0-5242879/6291456", "bytes 5242880-6291455/6291456"])
        self.assertEqual(r["variables"]["subido"]["id"], "grande-1")
        self.assertEqual(self.E.subidas[0]["ruta"], "/me/drive/root:/Datos/nota.txt:/content")
        self.assertTrue(r["variables"]["contrato"]["id"].startswith("a-"))

    def test_sharepoint_lista_teams_y_excel(self):
        r, _ = self.correr1(nodo("l", "sharepoint_lista", credencial="m365-escribe", sitio="https://contoso.sharepoint.com/sites/Ops", lista="Solicitudes",
                                 operacion="agregar", campos={"Title": "Nueva"}),
                            nodo("t", "teams_mensaje", credencial="m365-escribe", destino="canal", equipo="eq1", canal="19:abc@thread.tacv2", titulo="Aviso", texto="Hola"),
                            nodo("x", "excel_en_linea", credencial="m365-escribe", archivo="/Datos/registro.xlsx", tabla="Registro", operacion="anexar",
                                 fila={"Asunto": "dos", "Fecha": "2026-11-10"}),
                            nodo("y", "excel_en_linea", credencial="m365", archivo="/Datos/registro.xlsx", tabla="Registro", salida="filas"),
                            aristas=None)
        self.assertEqual(self.E.items_lista, [{"fields": {"Title": "Nueva"}}])
        self.assertEqual(self.E.enviados[0], {"body": {"contentType": "text", "content": "Hola"}, "subject": "Aviso"})
        self.assertEqual(self.E.filas_tabla, [["2026-11-10", "dos"]])
        self.assertEqual(r["variables"]["filas"][-1], {"Fecha": "2026-11-10", "Asunto": "dos"})

    def test_sitios_y_buzones_de_positiva(self):
        r, _ = self.correr1(nodo("l", "sharepoint_lista", credencial="m365", sitio="https://positiva.sharepoint.com/sites/Juridica", lista="X"), exigir_ok=False)
        self.assertIn("Positiva", r["motivo"])
        self.creds["graph-app"] = cred("microsoft_graph", True, ["client_secret"], tenant="t.onmicrosoft.com", client_id="c", client_secret="s",
                                       buzon="servicioalcliente@positiva.gov.co")
        r, _ = self.correr1(nodo("e", "outlook_enviar", credencial="graph-app", para="a@cliente.test", asunto="x", cuerpo="x"), exigir_ok=False)
        self.assertIn("bloqueado por seguridad", r["motivo"])


class OpenAPIYHttp(Integraciones):
    def test_analizar_y_operaciones(self):
        a = ni.analizar_openapi({"spec_url": f"{self.B}/spec.json"})
        self.assertTrue(a["ok"], a)
        self.assertEqual(a["base_url"], f"{self.B}/api")
        self.assertEqual({o["id"]: o["escritura"] for o in a["operaciones"]}, {"listPets": False, "createPets": True, "showPetById": False})
        r, _ = self.correr1(nodo("uno", "api_operacion", credencial="petstore", operacion="showPetById", parametros={"petId": "7"}, salida="m"),
                            nodo("todos", "api_operacion", credencial="petstore", operacion="GET /pets", parametros={"limit": "2"},
                                 paginacion={"tipo": "cursor", "parametro": "cursor", "ruta_cursor": "next", "ruta_items": "items"}, salida="todas"),
                            nodo("crear", "api_operacion", credencial="petstore", operacion="createPets", cuerpo={"id": 9}, salida="creada"),
                            aristas=None)
        self.assertEqual(r["variables"]["m"]["datos"]["name"], "Firulais")
        self.assertEqual(len(r["variables"]["todas"]["items"]), 6)
        self.assertEqual(self.llamadas("/api/pets/7")[0]["q"], {})
        self.assertTrue(all(x["auth"] == "" for x in self.llamadas("/api/")))
        w = next(x for x in r["escrituras"] if x["nodo"] == "crear")
        self.assertTrue(w["bloqueado"])
        self.assertFalse([x for x in self.E.llamadas if x["metodo"] == "POST" and x["ruta"] == "/api/pets"])
        r, _ = self.correr1(nodo("crear", "api_operacion", credencial="petstore-escribe", operacion="createPets", cuerpo={"id": 9}, salida="creada"))
        self.assertEqual(r["variables"]["creada"]["status"], 201)
        self.assertEqual(self.llamadas("/api/pets")[-1]["auth"], "Bearer tok-PET-456")

    def test_validacion_contra_la_especificacion(self):
        r, _ = self.correr1(nodo("uno", "api_operacion", credencial="petstore", operacion="showPetById"), exigir_ok=False)
        self.assertIn("necesita petId", r["motivo"])
        r, _ = self.correr1(nodo("uno", "api_operacion", credencial="petstore", operacion="showPetById", parametros={"petId": "siete"}), exigir_ok=False)
        self.assertIn("entero", r["motivo"])
        r, _ = self.correr1(nodo("uno", "api_operacion", credencial="petstore", operacion="borrarTodo"), exigir_ok=False)
        self.assertIn("no tiene la operación", r["motivo"])

    def test_red_privada_solo_si_la_creo_una_persona(self):
        self.assertFalse(ni.red_privada_de({"valores": {"red_privada": True}, "creada_por_persona": False}))
        self.assertTrue(ni.red_privada_de({"valores": {"red_privada": True}, "creada_por_persona": True}))
        self.assertIsNotNone(fd.ip_bloqueada("10.1.2.3", ni.red_privada_de({"valores": {"red_privada": True}})))

    def test_http_paginacion_subida_y_descarga(self):
        os.environ["HTTP_HOSTS_PERMITIDOS"] = "127.0.0.1"
        self.creds["subidas"] = cred("http_bearer", True, ["token"], token="tok-subidas", hosts="127.0.0.1")
        r, _ = self.correr1(nodo("p", "http", url=f"{self.B}/lista", paginacion={"tipo": "pagina", "parametro": "page", "ruta_items": "data"}, salida="lista"),
                            nodo("e", "http", url=f"{self.B}/enlace", paginacion={"tipo": "enlace"}, salida="enl"),
                            nodo("d", "http", url=f"{self.B}/archivo.pdf", descargar=True, salida="bajado"),
                            nodo("s", "http", metodo="POST", url=f"{self.B}/subir", credencial="subidas", cuerpo={"caso": "1"}, archivos=["{{bajado.archivo}}"], salida="sub"),
                            aristas=None)
        self.assertEqual(r["variables"]["lista"]["items"], [10, 11, 20, 21, 30, 31])
        self.assertEqual(r["variables"]["enl"]["items"], [1, 2])
        self.assertTrue(r["variables"]["bajado"]["archivo"]["id"].startswith("a-"))
        self.assertTrue(r["variables"]["sub"]["datos"]["tipo"].startswith("multipart/form-data"))
        cuerpo = self.llamadas("/subir")[0]["cuerpo"]
        self.assertIn(b'name="datos"', cuerpo)
        self.assertIn(b"%PDF-1.4 descargado", cuerpo)

    def test_http_oauth2_de_cliente(self):
        os.environ["HTTP_HOSTS_PERMITIDOS"] = ""
        self.creds["api-o2"] = cred("http_oauth2", False, ["client_secret"], token_url=f"{self.B}/token/google", client_id="c", client_secret="s", hosts="127.0.0.1")
        self.correr1(nodo("p", "http", url=f"{self.B}/lista", credencial="api-o2", salida="x"))
        self.assertEqual(self.llamadas("/lista")[0]["auth"], "Bearer acceso-google-1")


class Disparadores(Integraciones):
    def sondear(self, d):
        return fd.sondear({"flujo": "prueba", "definicion": d, "credenciales": self.creds})

    def test_gmail_nuevo_sin_repetir_y_responder_en_su_hilo(self):
        d = flujo([nodo("llega", "gmail_nuevo", credencial="gmail-escribe"), nodo("r", "gmail_responder", credencial="gmail-escribe", cuerpo="Gracias"),
                   nodo("fin", "salida", desenlace="responder")], [{"desde": "llega", "hacia": "r"}, {"desde": "r", "hacia": "fin"}])
        self.assertEqual(fd.validar(d), [])
        p = self.sondear(d)
        self.assertTrue(p["ok"] and p["primera"] and p["casos"] == [], p)
        self.E.nuevos = ["m9"]
        p = self.sondear(d)
        self.assertEqual(len(p["casos"]), 1, p)
        self.assertEqual(self.sondear(d)["casos"], [])  # no repite
        r, _ = self.correr_orden(d, caso=p["casos"][0]["caso"])
        self.assertEqual(r["accion"], "respondido")
        self.assertEqual(self.E.enviados[0]["threadId"], "hilo-m9")

    def test_outlook_nuevo_con_delta(self):
        d = flujo([nodo("llega", "outlook_nuevo", credencial="m365"), nodo("fin", "salida", desenlace="manual")], [{"desde": "llega", "hacia": "fin"}])
        p = self.sondear(d)
        self.assertEqual(p["casos"], [])
        self.E.nuevos = ["g7"]
        p = self.sondear(d)
        self.assertEqual(len(p["casos"]), 1, p)
        self.assertTrue(any(x["q"].get("$deltatoken") == "t1" for x in self.E.llamadas))  # siguió desde el deltaLink guardado
        self.assertEqual(self.sondear(d)["casos"], [])

    def test_evento_proximo_una_sola_vez(self):
        import datetime
        ni._reloj = lambda: AHORA
        inicio = datetime.datetime.fromtimestamp(AHORA + 600, datetime.timezone.utc).isoformat()
        self.E.eventos = [{"id": "ev-1", "summary": "Comité", "start": {"dateTime": inicio}, "end": {"dateTime": inicio}},
                          {"id": "ev-2", "summary": "Lejano", "start": {"dateTime": datetime.datetime.fromtimestamp(AHORA + 7200, datetime.timezone.utc).isoformat()}}]
        d = flujo([nodo("pronto", "calendar_evento_proximo", credencial="gmail", minutos_antes=15), nodo("fin", "salida", desenlace="manual")],
                  [{"desde": "pronto", "hacia": "fin"}])
        p = self.sondear(d)
        self.assertEqual([x["entrada"]["evento"]["titulo"] for x in p["casos"]], ["Comité"])
        self.assertEqual(self.sondear(d)["casos"], [])
        r, _ = self.correr_orden(d, entrada=p["casos"][0]["entrada"])
        self.assertEqual(r["accion"], "manual")


class Paridad(unittest.TestCase):
    MALOS = [
        ("gmail_buscar", {}), ("gmail_buscar", {"credencial": "g", "max": 500}), ("gmail_enviar", {"credencial": "g", "para": "a@b.co", "asunto": "x"}),
        ("gmail_responder", {"credencial": "g"}), ("gmail_etiquetar", {"credencial": "g"}), ("calendar_eventos", {"credencial": "g", "dias": 400}),
        ("calendar_crear", {"credencial": "g", "titulo": "x"}), ("calendar_actualizar", {"credencial": "g", "evento": "e"}),
        ("calendar_disponibilidad", {"credencial": "g", "max_franjas": 99}), ("docs_crear_desde_plantilla", {"credencial": "g", "plantilla": "p", "exportar": "ninguno"}),
        ("docs_crear_desde_plantilla", {"credencial": "g", "plantilla": "p", "exportar": "odt"}), ("forms_respuestas", {"credencial": "g"}),
        ("outlook_buscar", {"credencial": "m", "max": 0}), ("outlook_enviar", {"credencial": "m", "para": "a@b.co", "cuerpo": "x"}),
        ("outlook_mover", {"credencial": "m"}), ("outlook_categorizar", {"credencial": "m"}), ("outlook_calendario_crear", {"credencial": "m", "inicio": "x"}),
        ("onedrive_leer", {"credencial": "m", "como": "zip", "archivo": "/a"}), ("onedrive_subir", {"credencial": "m"}),
        ("onedrive_subir", {"credencial": "m", "archivo": "{{doc}}", "contenido": "x"}), ("sharepoint_archivos", {"credencial": "m", "sitio": "s", "operacion": "leer"}),
        ("sharepoint_archivos", {"credencial": "m", "sitio": "s", "operacion": "borrar"}),
        ("sharepoint_lista", {"credencial": "m", "sitio": "s", "lista": "l", "operacion": "agregar"}),
        ("teams_mensaje", {"credencial": "t", "texto": "x", "destino": "canal", "equipo": "e"}), ("teams_mensaje", {"credencial": "t", "texto": "x", "destino": "chat"}),
        ("excel_en_linea", {"credencial": "m", "archivo": "/a.xlsx"}), ("excel_en_linea", {"credencial": "m", "archivo": "/a.xlsx", "tabla": "T", "operacion": "anexar"}),
        ("api_operacion", {"credencial": "a"}), ("api_operacion", {"credencial": "a", "operacion": "x", "paginacion": {"tipo": "cursor"}}),
        ("api_operacion", {"credencial": "a", "operacion": "x", "paginacion": {"tipo": "magia"}}), ("gmail_nuevo", {}),
        ("outlook_nuevo", {"credencial": "m", "cada_minutos": 0}), ("calendar_evento_proximo", {"credencial": "g", "minutos_antes": 5000}),
        ("http", {"url": "https://a.co/x", "paginacion": {"tipo": "pagina"}}), ("http", {"url": "https://a.co/x", "metodo": "GET", "archivos": ["{{doc}}"]}),
        ("http", {"url": "https://a.co/x", "metodo": "POST", "archivos": ["{{doc}}"], "modo_archivos": "ftp"}),
        ("http", {"url": "https://a.co/x", "metodo": "POST", "paginacion": {"tipo": "enlace"}}),
    ]

    def test_python_rechaza_lo_que_no_vale_y_acepta_los_ejemplos(self):
        for t, c in self.MALOS:
            self.assertTrue(fd._validar_config({"id": "n", "tipo": t, "config": c}), (t, c))
        for t in ni.TIPOS:
            s = nd.TIPOS[t]
            self.assertTrue(s.get("preparar"), t)
            for c in [s["ejemplo"]["config"], *s["ejemplos"]]:
                self.assertEqual(fd._validar_config({"id": "n", "tipo": t, "config": c}), [], (t, c))

    def test_zod_igual(self):
        dist = Path(os.environ.get("RUTA_SERVIDOR_DIST", "/app/servidor/dist")) / "flujos.js"
        node = shutil.which("node")
        if not node or not dist.exists():
            self.skipTest("no hay servidor compilado o node")
        buenos = [{"tipo": t, "config": c} for t in ni.TIPOS | {"http"} for c in [nd.TIPOS[t]["ejemplo"]["config"], *nd.TIPOS[t]["ejemplos"]]]
        guion = ("const f = await import(process.argv[1]); const d = JSON.parse(process.argv[2]);"
                 "const r = (xs) => xs.map((e) => f.CONFIG_DE[e.tipo].safeParse(e.config).success);"
                 "console.log(JSON.stringify({tipos: [...f.TIPOS_DE_NODO], buenos: r(d.buenos), malos: r(d.malos)}));")
        p = subprocess.run([node, "--input-type=module", "-e", guion, dist.as_uri(), json.dumps({"buenos": buenos, "malos": [{"tipo": t, "config": c} for t, c in self.MALOS]})],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(set(r["tipos"]), fd.TIPOS)
        self.assertEqual([b for b, ok in zip(buenos, r["buenos"]) if not ok], [])
        self.assertEqual(r["malos"], [False] * len(self.MALOS))


class Plantillas(Integraciones):
    def test_las_dos_plantillas_validan(self):
        for archivo in ("09-responder-gmail-con-documento.json", "10-agendar-reuniones-desde-outlook.json"):
            p = json.loads((AQUI.parent / "catalogo" / "plantillas" / archivo).read_text(encoding="utf-8"))
            d = {**p["definicion"], "id": "prueba", "modelo": "gemini-falso"}
            self.assertEqual(fd.validar(d), [], archivo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
