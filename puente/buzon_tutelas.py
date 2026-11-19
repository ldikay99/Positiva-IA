"""
El buzón de producción de TUTELAS, SOLO LECTURA, para que el visor corra el motor como el job.

El job (`motores/tutelas`, Cloud Run Job ms-radicacion-ia-tutelas) lee con token de
aplicación (client_credentials) la carpeta TUTELAS-OUTLOOK-FOLDER-ENTRADA del buzón
TUTELAS-MICROSOFT-USERNAME y toma los hilos con `obtener_correos_por_conversacion`:
los 50 correos más recientes, agrupados por conversationId, cada hilo del más
antiguo al más reciente. El job solo define como variable el secreto de la
aplicación (TUTELAS-MICROSOFT-CLIENT-SECRET, de Secret Manager); tenant, cliente,
buzón y carpetas son los valores por defecto de Tutelas/config.py, y aquí se usan
los mismos (o los de entorno, si se definen).

Igual que `buzon_vivo.py` para PQRD, el `OutlookService` del motor queda con dos
candados para que nada se escriba:

  1. el adaptador `BuzonDeTutelas` solo deja pasar los métodos de lectura
     (`LECTURA`); cualquier otro lanza `PermissionError`;
  2. el módulo `requests` que usa `outlook_service` se cambia por un cliente que
     solo hace GET a Graph y POST al endpoint de token de Microsoft. Cualquier otra
     petición (PATCH de categorías, POST de move o reply…) lanza `PermissionError`,
     aunque un método de lectura cambie mañana y escriba.

Además el motor se importa con MODO_TEST=true, que por sí solo omite sus escrituras.
Las credenciales se leen de Secret Manager con el ADC montado en el contenedor,
viven solo en memoria y nunca se imprimen.

Uso por línea de comandos (lo llama servidor/src/buzon_tutelas.ts):
    python buzon_tutelas.py listar N           -> JSON con los N correos que tomaría el job, por hilo
    python buzon_tutelas.py traer DESTINO ID…  -> los baja (con su hilo) al formato de la bandeja
    python buzon_tutelas.py disponible         -> {"error": ""} si se puede leer el buzón
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Lo único que el job toma de Secret Manager para Graph. El resto de OUTLOOK_CONFIG
# (tenant, cliente, buzón, carpetas) son los valores por defecto de config.py.
SECRETOS = {"client_secret": "TUTELAS-MICROSOFT-CLIENT-SECRET"}
GRAPH = "https://graph.microsoft.com/"
TOKEN = "https://login.microsoftonline.com/"

# Lo único que el visor le puede pedir al OutlookService de tutelas.
LECTURA = frozenset({
    "obtener_correos", "obtener_correos_por_conversacion", "obtener_adjuntos",
    "descargar_adjunto", "_get_folder_id", "_headers", "_get_base_url", "_strip_html",
})

CAMPOS = ("id,subject,body,from,sender,toRecipients,receivedDateTime,sentDateTime,"
          "hasAttachments,internetMessageId,isRead,conversationId,categories,parentFolderId")

_credenciales: Dict[str, str] = {}


def credenciales() -> Dict[str, str]:
    """
    El secreto de la aplicación de Graph: el del entorno del flujo tutelas si lo tiene
    (TUTELAS-MICROSOFT-CLIENT-SECRET, el nombre del job), o si no, leído una vez de
    Secret Manager con el ADC.
    """
    if _credenciales:
        return _credenciales
    from solo_lectura import definida

    if all(definida(v) for v in SECRETOS.values()):
        _credenciales.update({clave: os.environ[v].strip() for clave, v in SECRETOS.items()})
        return _credenciales
    import google.auth
    import google.auth.transport.requests
    import requests

    from proyecto_gcp import proyecto_secretos

    proyecto = proyecto_secretos()  # antes de pedir credenciales: sin proyecto no hay a quién pedirle
    cred, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    cred.refresh(google.auth.transport.requests.Request())
    leidos = {}
    for clave, secreto in SECRETOS.items():
        r = requests.get(
            f"https://secretmanager.googleapis.com/v1/projects/{proyecto}"
            f"/secrets/{secreto}/versions/latest:access",
            headers={"Authorization": f"Bearer {cred.token}"}, timeout=20)
        if r.status_code != 200:
            raise PermissionError(f"Secret Manager respondió {r.status_code} para {secreto}")
        leidos[clave] = base64.b64decode(r.json()["payload"]["data"]).decode().strip()
    _credenciales.update(leidos)
    return _credenciales


class ClienteSoloLectura:
    """Lo que ve `outlook_service` como módulo `requests`: GET a Graph y el token, nada más."""

    def __init__(self, real) -> None:
        self._real = real

    @staticmethod
    def _negar(metodo: str, url: Any) -> None:
        raise PermissionError(f"el visor no escribe en el buzón de tutelas: {metodo} {str(url)[:120]}")

    def get(self, url, **k):
        if not str(url).startswith(GRAPH):
            self._negar("GET", url)
        k.setdefault("timeout", 60)
        return self._real.get(url, **k)

    def post(self, url, **k):
        u = str(url)
        if u.startswith(TOKEN) and u.endswith("/oauth2/v2.0/token"):
            k.setdefault("timeout", 30)
            return self._real.post(url, **k)
        self._negar("POST", url)

    def patch(self, url, **k):
        self._negar("PATCH", url)

    def put(self, url, **k):
        self._negar("PUT", url)

    def delete(self, url, **k):
        self._negar("DELETE", url)

    def request(self, metodo, url, **k):
        m = str(metodo).upper()
        if m == "GET":
            return self.get(url, **k)
        if m == "POST":
            return self.post(url, **k)
        self._negar(m, url)

    def __getattr__(self, nombre: str):
        # Las excepciones se necesitan para los `except requests.…`; una Session no.
        if nombre in ("exceptions", "RequestException", "HTTPError", "Timeout", "ConnectionError"):
            return getattr(self._real, nombre)
        raise PermissionError(f"el visor no usa requests.{nombre} contra el buzón de tutelas")


class BuzonDeTutelas:
    """El OutlookService de tutelas con solo sus métodos de lectura."""

    def __init__(self, real) -> None:
        self._real = real

    @property
    def config(self) -> Dict[str, Any]:
        return self._real.config

    def __getattr__(self, nombre: str):
        if nombre in LECTURA:
            return getattr(self._real, nombre)
        raise PermissionError(f"el visor no escribe en el buzón de tutelas: {nombre}")


def _arrancar_motor() -> None:
    """Lo mismo que hace el trabajador de tutelas antes de importar el motor."""
    raiz = Path(__file__).resolve().parent.parent
    motor = Path(os.environ.get("RUTA_MOTOR_TUTELAS", raiz / "motores" / "tutelas"))
    sys.path.insert(0, str(raiz / "puente"))
    from solo_lectura import definida

    # Este script solo lista y baja correos: siempre en modo prueba, con su cliente de solo lectura.
    os.environ["MODO_TEST"] = "true"
    os.environ.pop("MODO_UAT", None)
    for var in ("TUTELAS-MICROSOFT-CLIENT-SECRET", "TUTELAS-REDIS-PASSWORD", "TUTELAS-SGDEA-CLIENT-SECRET",
                "TUTELAS-SGDEA-PASSWORD", "TUTELAS-DATABASE-PASSWORD"):
        if not definida(var):  # lo que trae el entorno del flujo se conserva
            os.environ[var] = "no-se-usa"
    os.environ.setdefault("TEST_OUTPUT_DIR", str(Path(os.environ.get("RUTA_TRABAJO", "/tmp")) / "tutelas"))
    os.chdir(motor)
    sys.path.insert(0, str(motor))


def servicio() -> BuzonDeTutelas:
    """Un `OutlookService` del motor de tutelas con los dos candados puestos."""
    import requests
    from Tutelas.services import outlook_service as m

    if not isinstance(m.requests, ClienteSoloLectura):
        m.requests = ClienteSoloLectura(requests)
    s = m.OutlookService()
    s.config = {**s.config, **credenciales()}
    return BuzonDeTutelas(s)


def _leer(s: BuzonDeTutelas, ruta: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """GET a Graph sobre el buzón del job, con el cliente de solo lectura."""
    from Tutelas.services import outlook_service as m

    r = m.requests.get(f"{s._get_base_url()}{ruta}", headers=s._headers(), params=params or {})
    if r.status_code != 200:
        raise RuntimeError(f"Graph respondió {r.status_code} en {ruta.split('?')[0][:60]}")
    return r.json()


def caso_de(id_graph: str) -> str:
    """El nombre de carpeta de un correo del buzón de tutelas: estable y sin caracteres raros."""
    return "t" + hashlib.sha1(id_graph.encode()).hexdigest()[:14]


@contextlib.contextmanager
def _consola_a_stderr():
    """El motor imprime su progreso; la respuesta JSON es lo único que va a stdout."""
    real = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = real


def disponible() -> str:
    try:
        credenciales()
        return ""
    except Exception as e:
        return f"{type(e).__name__}: {e}"[:300]


def listar(n: int) -> Dict[str, Any]:
    """Lo que tomaría el job ahora: los hilos en su orden y, en cada uno, sus correos."""
    s = servicio()
    carpeta_id = s.config["folder_entrada"]
    with _consola_a_stderr():
        # El método del job: $top=n, $orderby=receivedDateTime desc, agrupados por hilo.
        hilos = s.obtener_correos_por_conversacion(limite=n)
        # Lo mismo con los campos que el job no pide (categorías, adjuntos), para la vista.
        extra = {m["id"]: m for m in _leer(s, f"/mailFolders/{carpeta_id}/messages",
                                          {"$top": n, "$orderby": "receivedDateTime desc",
                                           "$select": "id,categories,hasAttachments"}).get("value", [])}
        try:
            carpeta = _leer(s, f"/mailFolders/{carpeta_id}", {"$select": "displayName,totalItemCount,unreadItemCount"})
        except Exception:
            carpeta = {}
    correos = []
    for i, hilo in enumerate(hilos):
        for j, c in enumerate(hilo):
            x = extra.get(c.id, {})
            correos.append({
                "id": c.id, "caso": caso_de(c.id), "asunto": c.subject or "",
                "remitente": c.remitente_nombre or "", "remitenteCorreo": c.remitente_email or "",
                "recibido": c.fecha_recibido.isoformat().replace("+00:00", "Z"),
                "categorias": list(x.get("categories") or []), "conAdjuntos": bool(x.get("hasAttachments")),
                "hilo": (c.conversation_id or c.id)[-10:], "ordenHilo": i + 1,
                "correosEnHilo": len(hilo), "principal": j == len(hilo) - 1,
            })
    return {"carpeta": carpeta.get("displayName", ""), "total": carpeta.get("totalItemCount"), "correos": correos}


def _bajar(s: BuzonDeTutelas, id_graph: str, destino: Path) -> Dict[str, Any]:
    msg = _leer(s, f"/messages/{id_graph}", {"$select": CAMPOS})
    caso = caso_de(id_graph)
    carpeta = destino / caso
    (carpeta / "adj").mkdir(parents=True, exist_ok=True)
    adjuntos = []
    if msg.get("hasAttachments"):
        for i, a in enumerate(_leer(s, f"/messages/{id_graph}/attachments").get("value", [])):
            item = {"id": a.get("id"), "name": a.get("name"), "contentType": a.get("contentType"),
                    "size": a.get("size"), "isInline": a.get("isInline", False), "tipo": a.get("@odata.type")}
            if a.get("@odata.type") == "#microsoft.graph.fileAttachment":
                contenido = base64.b64decode(a["contentBytes"]) if a.get("contentBytes") else s.descargar_adjunto(id_graph, a["id"])
                if contenido:
                    archivo = f"a{i:03d}.bin"
                    (carpeta / "adj" / archivo).write_bytes(contenido)
                    item["archivo"] = archivo
            adjuntos.append(item)
    msg["_adjuntos"] = adjuntos
    msg["_vivo"] = True
    msg["_flujo"] = "tutelas"
    msg["_traido"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    msg["_caso"] = caso
    return msg


def traer(destino: Path, ids: List[str]) -> Dict[str, Any]:
    """Baja cada correo con sus adjuntos y los demás correos de su hilo en la carpeta de
    entrada (como los agruparía el job). Devuelve id -> caso y id -> caso principal del hilo."""
    s = servicio()
    carpeta_id = s.config["folder_entrada"]
    casos: Dict[str, str] = {}
    principales: Dict[str, str] = {}
    hechos: Dict[str, Dict[str, Any]] = {}
    with _consola_a_stderr():
        for id_graph in ids:
            try:
                msg = hechos.get(id_graph) or _bajar(s, id_graph, destino)
            except Exception as e:
                print(f"[tutelas] no se trajo un correo: {e}", file=sys.stderr)
                continue
            hechos[id_graph] = msg
            miembros = {id_graph: msg}
            conv = msg.get("conversationId")
            if conv:
                # El hilo, como lo agrupa el job: los correos de la misma carpeta con el
                # mismo conversationId (la de entrada, para lo que lista `listar`).
                filtro = "conversationId eq '" + conv.replace("'", "''") + "'"
                for m in _leer(s, f"/mailFolders/{msg.get('parentFolderId') or carpeta_id}/messages",
                               {"$filter": filtro, "$select": "id,receivedDateTime", "$top": 50}).get("value", []):
                    if m["id"] not in miembros:
                        try:
                            miembros[m["id"]] = hechos.get(m["id"]) or _bajar(s, m["id"], destino)
                            hechos[m["id"]] = miembros[m["id"]]
                        except Exception as e:
                            print(f"[tutelas] no se trajo un correo del hilo: {e}", file=sys.stderr)
            orden = sorted(miembros.values(), key=lambda x: x.get("receivedDateTime") or "")
            hilo = [x["_caso"] for x in orden]
            for x in orden:
                x["_hilo"] = hilo
            casos[id_graph] = msg["_caso"]
            principales[id_graph] = hilo[-1]
    for msg in hechos.values():
        (destino / msg["_caso"] / "mensaje.json").write_text(json.dumps(msg, ensure_ascii=False), encoding="utf-8")
    return {"casos": casos, "principales": principales, "traidos": len(hechos)}


def principal(argv: List[str]) -> int:
    _arrancar_motor()
    if len(argv) >= 2 and argv[0] == "listar":
        r = listar(max(1, min(150, int(argv[1]))))
    elif len(argv) >= 3 and argv[0] == "traer":
        r = traer(Path(argv[1]), argv[2:])
    elif argv[:1] == ["disponible"]:
        r = {"error": disponible()}
    else:
        print(json.dumps({"error": "uso: listar N | traer DESTINO ID... | disponible"}))
        return 2
    sys.stdout.write(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(principal(sys.argv[1:]))
    except Exception as e:
        sys.stdout.write(json.dumps({"error": f"{type(e).__name__}: {e}"[:500]}))
        sys.exit(1)
