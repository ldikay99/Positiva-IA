"""
El buzón de producción, SOLO LECTURA, para que el visor corra el motor como el job.

El job lee la carpeta AUTOMATIZACION de servicioalcliente@positiva.gov.co y, para
decidir si un correo es duplicado, anexo o seguimiento, consulta el resto del
buzón: el hilo, los radicados con el mismo asunto, los documentos del radicado.
Sin eso el visor solo puede mostrar la mitad del flujo. Aquí el motor recibe su
propio `OutlookService`, con dos candados para que nada se escriba:

  1. el adaptador solo deja pasar los métodos de lectura (`LECTURA`); marcar,
     mover, reenviar y responder se anotan como evento ("lo que el job haría")
     y devuelven lo que habría devuelto Graph, sin llamarlo;
  2. el cliente HTTP del servicio se cambia por uno que solo hace GET a Graph y
     POST al endpoint de token de Microsoft. Cualquier otra petición lanza
     `PermissionError`, aunque un método de lectura cambie mañana y escriba.

Las credenciales se leen de Secret Manager con el ADC montado en el
contenedor, viven solo en memoria y nunca se imprimen.

Uso por línea de comandos (lo llama el servidor):
    python buzon_vivo.py listar N           -> JSON con los N correos que tomaría el job
    python buzon_vivo.py traer DESTINO ID…  -> los baja al formato de la bandeja
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SECRETOS = {
    "MICROSOFT_TENANT_ID": "PQRD_MICROSOFT_TENANT_ID",
    "MICROSOFT_CLIENT_ID": "PQRD_MICROSOFT_CLIENT_ID",
    "MICROSOFT_CLIENT_SECRET": "PQRD_MICROSOFT_CLIENT_SECRET",
    "OUTLOOK_FOLDER_ID": "PQRD_OUTLOOK_FOLDER_ID",
}
GRAPH = "https://graph.microsoft.com/"
TOKEN = "https://login.microsoftonline.com/"

# Lo único que el motor puede pedirle al buzón real. Todo lo demás es escritura.
LECTURA = frozenset({
    "get_emails", "get_conversation_message_ids", "hilo_ya_radicado",
    "documentos_nuevos_frente_al_radicado", "correo_con_etiqueta",
    "mensajes_del_hilo", "buscar_etiqueta_ent_por_asunto", "_es_el_mismo_caso",
    "_nombres_de_adjuntos", "get_email_by_id", "get_attachments",
    "download_attachment", "list_folders", "_leer_categorias",
})
ESCRITURA = frozenset({
    "marcar", "etiquetar_correo", "move_email", "mover_hilo_completo",
    "forward_email", "responder_correo",
})

_credenciales: Dict[str, str] = {}
_origen: Dict[str, str] = {"de": ""}


def origen_credenciales() -> str:
    """De dónde salieron las credenciales de Graph: «entorno del flujo» o «Secret Manager»."""
    return _origen["de"]


def credenciales() -> Dict[str, str]:
    """
    Los secretos de Graph: los del entorno del flujo pqrd si los tiene (los mismos
    nombres del job), o si no, leídos una vez de Secret Manager con el ADC.
    """
    if _credenciales:
        return _credenciales
    from solo_lectura import definida

    if all(definida(v) for v in SECRETOS):
        _credenciales.update({v: os.environ[v].strip() for v in SECRETOS})
        _origen["de"] = "entorno del flujo"
        return _credenciales
    import google.auth
    import google.auth.transport.requests
    import requests

    from proyecto_gcp import proyecto_secretos

    proyecto = proyecto_secretos()  # antes de pedir credenciales: sin proyecto no hay a quién pedirle
    cred, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    cred.refresh(google.auth.transport.requests.Request())
    leidos = {}
    for variable, secreto in SECRETOS.items():
        r = requests.get(
            f"https://secretmanager.googleapis.com/v1/projects/{proyecto}"
            f"/secrets/{secreto}/versions/latest:access",
            headers={"Authorization": f"Bearer {cred.token}"}, timeout=20)
        if r.status_code != 200:
            raise PermissionError(f"Secret Manager respondió {r.status_code} para {secreto}")
        leidos[variable] = base64.b64decode(r.json()["payload"]["data"]).decode().strip()
    _credenciales.update(leidos)
    _origen["de"] = "Secret Manager"
    return _credenciales


def disponible() -> str:
    """Vacío si el buzón real se puede usar; si no, por qué."""
    try:
        credenciales()
        return ""
    except Exception as e:
        return f"{type(e).__name__}: {e}"[:300]


class ClienteSoloLectura:
    """El cliente HTTP del servicio, sin poder escribir: GET a Graph y el token."""

    def __init__(self, real) -> None:
        self._real = real

    @staticmethod
    def _negar(metodo: str, url: str) -> None:
        raise PermissionError(f"el visor no escribe en el buzón: {metodo} {str(url)[:120]}")

    async def get(self, url, **k):
        if not str(url).startswith(GRAPH):
            self._negar("GET", url)
        return await self._real.get(url, **k)

    async def post(self, url, **k):
        u = str(url)
        if u.startswith(TOKEN) and u.endswith("/oauth2/v2.0/token"):
            return await self._real.post(url, **k)
        self._negar("POST", url)

    async def patch(self, url, **k):
        self._negar("PATCH", url)

    async def put(self, url, **k):
        self._negar("PUT", url)

    async def delete(self, url, **k):
        self._negar("DELETE", url)

    async def request(self, metodo, url, **k):
        if str(metodo).upper() == "GET":
            return await self.get(url, **k)
        if str(metodo).upper() == "POST":
            return await self.post(url, **k)
        self._negar(str(metodo).upper(), url)

    async def aclose(self):
        await self._real.aclose()

    @property
    def is_closed(self):
        return self._real.is_closed


def preparar_settings() -> None:
    """Pone en la configuración del motor las credenciales reales de Graph."""
    from config import settings

    for variable, valor in credenciales().items():
        setattr(settings, variable, valor)


def servicio(escribir: bool = False):
    """
    Un `OutlookService` del motor con el cliente HTTP de solo lectura. Solo con la
    compuerta de radicación encendida (las dos llaves) el trabajador lo pide para
    escribir, como el job.
    """
    from services.outlook import OutlookService
    from solo_lectura import radicacion_real

    preparar_settings()
    s = OutlookService()
    if not (escribir and radicacion_real()):
        s._client = ClienteSoloLectura(s._client)
    return s


def nombre_de_carpeta(id_carpeta: Any) -> str:
    """El nombre de una carpeta del job a partir de su id de Graph."""
    from config import settings

    nombres = {
        "OUTLOOK_FOLDER_ID": "AUTOMATIZACION (bandeja del job)",
        "OUTLOOK_FOLDER_DUPLICADOS": "Duplicados",
        "OUTLOOK_FOLDER_RADICACION_EXITOSA": "Radicación exitosa",
        "OUTLOOK_FOLDER_REVISAR_MANUAL": "Revisión manual",
        "OUTLOOK_FOLDER_REENVIADO_OTRA_AREA": "Reenviado a otra área",
        "OUTLOOK_FOLDER_ERROR_RADICAR": "Error al radicar",
    }
    for atributo, nombre in nombres.items():
        if id_carpeta and getattr(settings, atributo, None) == id_carpeta:
            return nombre
    return str(id_carpeta)[:40]


def _categorias_tras(categorias: List[str], marca: str, quitar: bool) -> List[str]:
    from pipeline.marcas import fusionar

    return fusionar(categorias or [], marca, quitar)


class _Buzon:
    """Lo común a los dos buzones del visor: sirven el correo y anotan las escrituras."""

    def __init__(self, carpeta: Path, mensaje: dict, evento: Callable[..., None]) -> None:
        self.carpeta = carpeta
        self.mensaje = mensaje
        self.evento = evento
        self.escrituras: List[str] = []
        self.categorias = list(mensaje.get("categories") or [])
        self.id_propio = mensaje.get("id") or ""

    def _es_el_propio(self, id_correo: Any) -> bool:
        return not self.id_propio or str(id_correo) == self.id_propio

    async def _adjuntos_de_disco(self):
        return [{"id": a["archivo"], "name": a["name"], "size": a.get("size", 0),
                 "isInline": a.get("isInline", False), "contentType": a.get("contentType"),
                 "@odata.type": a.get("tipo") or "#microsoft.graph.fileAttachment"}
                for a in self.mensaje.get("_adjuntos", []) if a.get("archivo")]

    async def _descargar_de_disco(self, adjunto: str) -> str:
        return base64.b64encode((self.carpeta / "adj" / adjunto).read_bytes()).decode()

    def _escritura(self, nombre: str, a: tuple, k: dict):
        """Lo que el job escribiría: se anuncia y se devuelve lo que diría Graph."""
        self.escrituras.append(nombre)
        detalle = [str(x)[:300] for x in a][:4] + [f"{c}={str(v)[:120]}" for c, v in k.items()][:3]
        marca = k.get("marca", a[1] if len(a) > 1 else "") if nombre == "marcar" else ""
        quitar = k.get("quitar_de_proceso", a[2] if len(a) > 2 else False) if nombre == "marcar" else False
        destino = ""
        if nombre == "move_email":
            destino = nombre_de_carpeta(k.get("destination_folder_id", a[1] if len(a) > 1 else ""))
        elif nombre == "forward_email":
            destino = str(k.get("to_email", a[1] if len(a) > 1 else ""))
        self.evento("escritura", sistema="buzon", operacion=nombre, bloqueada=True, detalle=detalle,
                    propio=self._es_el_propio(a[0] if a else ""), marca=marca,
                    quitar_de_proceso=bool(quitar), destino=destino)
        if nombre == "marcar":
            if self._es_el_propio(a[0] if a else ""):
                self.categorias = _categorias_tras(self.categorias, marca, quitar)
                return list(self.categorias)
            return _categorias_tras([], marca, quitar)
        if nombre == "move_email":
            return ""
        if nombre == "mover_hilo_completo":
            return 1
        return None


class BuzonLocal(_Buzon):
    """Un correo guardado en disco, sin buzón real detrás: las consultas no encuentran nada."""

    NEUTRO = {
        "get_emails": [], "get_conversation_message_ids": [], "mensajes_del_hilo": [],
        "hilo_ya_radicado": None, "buscar_etiqueta_ent_por_asunto": None,
        "documentos_nuevos_frente_al_radicado": [], "correo_con_etiqueta": None,
        "_es_el_mismo_caso": False, "_nombres_de_adjuntos": [], "get_email_by_id": None,
        "list_folders": [], "_leer_categorias": None,
    }

    async def get_attachments(self, _id):
        return await self._adjuntos_de_disco()

    async def download_attachment(self, _id, adjunto):
        return await self._descargar_de_disco(adjunto)

    def __getattr__(self, nombre):
        if nombre.startswith("ultimo"):
            return ""
        if nombre in self.NEUTRO:
            async def _sin_buzon(*a, **k):
                self.evento("buzon", operacion=nombre, real=False,
                            resultado="sin buzón real: la carpeta local no tiene el resto del buzón")
                return self.NEUTRO[nombre]
            return _sin_buzon
        if nombre.startswith("_"):
            raise AttributeError(nombre)

        async def _anotar(*a, **k):
            return self._escritura(nombre, a, k)
        return _anotar


class BuzonVivo(_Buzon):
    """El buzón de producción para un correo de AUTOMATIZACION: lee de verdad, no escribe."""

    def __init__(self, real, carpeta: Path, mensaje: dict, evento: Callable[..., None]) -> None:
        super().__init__(carpeta, mensaje, evento)
        self._real = real

    @property
    def ultimo_error(self) -> str:
        return getattr(self._real, "ultimo_error", "") or ""

    async def get_attachments(self, id_correo):
        if self._es_el_propio(id_correo):
            return await self._adjuntos_de_disco()
        return await self._leer("get_attachments", (id_correo,), {})

    async def download_attachment(self, id_correo, adjunto):
        if self._es_el_propio(id_correo) and (self.carpeta / "adj" / str(adjunto)).exists():
            return await self._descargar_de_disco(adjunto)
        return await self._leer("download_attachment", (id_correo, adjunto), {})

    async def _leer(self, nombre: str, a: tuple, k: dict):
        t = time.time()
        try:
            r = await getattr(self._real, nombre)(*a, **k)
        except Exception as e:
            self.evento("buzon", operacion=nombre, real=True, ok=False,
                        ms=int((time.time() - t) * 1000), error=f"{type(e).__name__}: {e}"[:300])
            raise
        self.evento("buzon", operacion=nombre, real=True, ok=True,
                    ms=int((time.time() - t) * 1000), resultado=_resumen(r),
                    error=getattr(self._real, "ultimo_error", "") or "")
        return r

    def __getattr__(self, nombre):
        if nombre in LECTURA:
            async def _lectura(*a, **k):
                return await self._leer(nombre, a, k)
            return _lectura
        if nombre.startswith("_"):
            raise AttributeError(nombre)

        async def _anotar(*a, **k):
            return self._escritura(nombre, a, k)
        return _anotar


class BuzonQueEscribe(BuzonVivo):
    """
    Solo con la compuerta de radicación encendida: el buzón de producción como lo usa
    el job. Lee igual que BuzonVivo y ESCRIBE de verdad (marcar, mover, reenviar,
    responder); cada escritura se anuncia con bloqueada=False.
    """

    def __init__(self, real, carpeta: Path, mensaje: dict, evento: Callable[..., None]) -> None:
        from solo_lectura import radicacion_real

        if not radicacion_real():
            raise PermissionError("el buzón que escribe solo existe con la compuerta de radicación encendida")
        super().__init__(real, carpeta, mensaje, evento)

    def __getattr__(self, nombre):
        if nombre in LECTURA:
            async def _lectura(*a, **k):
                return await self._leer(nombre, a, k)
            return _lectura
        if nombre.startswith("_"):
            raise AttributeError(nombre)

        async def _escribir(*a, **k):
            self.escrituras.append(nombre)
            t = time.time()
            try:
                r = await getattr(self._real, nombre)(*a, **k)
            except Exception as e:
                self.evento("escritura", sistema="buzon", operacion=nombre, bloqueada=False, ok=False,
                            ms=int((time.time() - t) * 1000), error=f"{type(e).__name__}: {e}"[:300],
                            propio=self._es_el_propio(a[0] if a else ""))
                raise
            self.evento("escritura", sistema="buzon", operacion=nombre, bloqueada=False, ok=True,
                        ms=int((time.time() - t) * 1000), propio=self._es_el_propio(a[0] if a else ""),
                        detalle=[str(x)[:120] for x in a][:3])
            return r
        return _escribir


def _resumen(r: Any) -> str:
    if r is None:
        return "nada"
    if isinstance(r, bool):
        return "sí" if r else "no"
    if isinstance(r, (list, tuple)):
        return f"{len(r)} resultado(s)" + (f": {', '.join(str(x)[:60] for x in r[:4])}" if r and isinstance(r[0], str) else "")
    if isinstance(r, dict):
        asunto = r.get("subject") or ""
        return f"correo «{asunto[:80]}»" if asunto else "un registro"
    return str(r)[:160]


# ----------------------------------------------------------------- bandeja real

CAMPOS = ("id,subject,body,from,sender,toRecipients,receivedDateTime,sentDateTime,"
          "hasAttachments,internetMessageId,isRead,conversationId,categories")


def caso_de(id_graph: str) -> str:
    """El nombre de carpeta de un correo del buzón real: estable y sin caracteres raros."""
    return "v" + hashlib.sha1(id_graph.encode()).hexdigest()[:14]


async def listar(n: int) -> Dict[str, Any]:
    """Los correos que tomaría el job ahora, en su orden, con la carpeta y su tamaño."""
    from config import settings

    s = servicio()
    try:
        correos = await s.get_emails(settings.OUTLOOK_FOLDER_ID, limit=n)
        r = await s._consultar(
            f"{s.base_url}/users/{s.user_email}/mailFolders/{settings.OUTLOOK_FOLDER_ID}",
            {"$select": "displayName,totalItemCount,unreadItemCount"})
        carpeta = r.json() if r.status_code == 200 else {}
        return {
            "carpeta": carpeta.get("displayName", ""),
            "total": carpeta.get("totalItemCount"),
            "correos": [{
                "id": c.id, "caso": caso_de(c.id), "asunto": c.subject or "",
                "remitente": (c.from_address.name if c.from_address else "") or "",
                "remitenteCorreo": (c.from_address.address if c.from_address else "") or "",
                "recibido": c.received_datetime or "", "categorias": list(c.categories or []),
                "conAdjuntos": bool(c.has_attachments), "hilo": (c.conversation_id or "")[-10:],
            } for c in correos],
        }
    finally:
        await s.close()


async def traer(destino: Path, ids: List[str]) -> Dict[str, str]:
    """Baja cada correo con sus adjuntos al formato de la bandeja. Devuelve id -> caso."""
    s = servicio()
    hechos: Dict[str, str] = {}
    try:
        for id_graph in ids:
            r = await s._consultar(f"{s.base_url}/users/{s.user_email}/messages/{id_graph}",
                                   {"$select": CAMPOS})
            if r.status_code != 200:
                continue
            msg = r.json()
            caso = caso_de(id_graph)
            carpeta = destino / caso
            (carpeta / "adj").mkdir(parents=True, exist_ok=True)
            adjuntos = []
            if msg.get("hasAttachments"):
                ra = await s._consultar(
                    f"{s.base_url}/users/{s.user_email}/messages/{id_graph}/attachments", {})
                for i, a in enumerate(ra.json().get("value", []) if ra.status_code == 200 else []):
                    item = {"id": a.get("id"), "name": a.get("name"), "contentType": a.get("contentType"),
                            "size": a.get("size"), "isInline": a.get("isInline", False),
                            "tipo": a.get("@odata.type")}
                    if a.get("@odata.type") == "#microsoft.graph.fileAttachment" and a.get("contentBytes"):
                        archivo = f"a{i:03d}.bin"
                        (carpeta / "adj" / archivo).write_bytes(base64.b64decode(a["contentBytes"]))
                        item["archivo"] = archivo
                    adjuntos.append(item)
            msg["_adjuntos"] = adjuntos
            msg["_vivo"] = True
            msg["_traido"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            (carpeta / "mensaje.json").write_text(json.dumps(msg, ensure_ascii=False), encoding="utf-8")
            hechos[id_graph] = caso
    finally:
        await s.close()
    return hechos


def _arrancar_motor() -> None:
    """Lo mismo que hace el trabajador antes de importar el motor."""
    raiz = Path(__file__).resolve().parent.parent
    motor = Path(os.environ.get("RUTA_MOTOR", raiz / "motor"))
    sys.path.insert(0, str(raiz / "puente"))
    from solo_lectura import definida

    # Este script solo lista y baja correos: siempre en modo prueba, con su cliente de solo lectura.
    os.environ["MODO_TEST"] = "true"
    os.environ["DEDUP_EN_PRUEBAS"] = "false"
    for var in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID",
                "OUTLOOK_REFRESH_TOKEN", "OUTLOOK_FOLDER_ID", "REDIS_PASSWORD",
                "SGDEA_CLIENT_SECRET", "SGDEA_PASSWORD"):
        if not definida(var):  # lo que trae el entorno del flujo se conserva
            os.environ[var] = "x"
    os.chdir(motor)
    sys.path.insert(0, str(motor))


def principal(argv: List[str]) -> int:
    import asyncio

    _arrancar_motor()
    if len(argv) >= 2 and argv[0] == "listar":
        r = asyncio.run(listar(max(1, min(150, int(argv[1])))))
    elif len(argv) >= 3 and argv[0] == "traer":
        r = asyncio.run(traer(Path(argv[1]), argv[2:]))
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
