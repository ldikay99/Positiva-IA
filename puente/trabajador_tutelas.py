"""
Trabajador del motor de radicación de tutelas (`motores/tutelas`).

Corre `OrquestadorRadicacion.procesar_hilo` —el código del job, sin cambios—
sobre un HILO de la bandeja, como `_ejecutar_lote`: el correo pedido y los demás
correos guardados con su mismo conversationId, del más antiguo al más reciente
(`obtener_correos_por_conversacion`). Si el hilo lanza una excepción, se cierra
con ERROR-INESPERADO igual que en el job. Cada paso se anuncia con el protocolo
del visor a partir de los mensajes del propio motor (`op.paso`, `op.ok`,
`op.fallo`, `op.omitido`, `op.no_radicado`, `op.radicado`).

Nunca escribe en un sistema real:
  - Outlook: los correos y adjuntos salen de disco; etiquetar, responder y mover
    se anotan como «lo que el job haría» y devuelven éxito sin llamar a Graph.
  - Redis: en memoria y vacío en cada corrida (no hay duplicados entre corridas).
  - SGDEA: corre el `radicar_tutela` real, que arma el cuerpo y los archivos,
    pero con una sesión HTTP de prueba que anota la petición y responde un
    radicado PRUEBA-…; el módulo no puede hacer ninguna otra petición.
  - Juzgados: la tabla del repositorio (juzgados_actualizada.csv), con la misma
    búsqueda que hace la consulta SQL, en solo lectura.
La IA es la real: el AIExtractor del motor sobre Vertex.

Con las credenciales reales en el entorno del flujo (los nombres TUTELAS-… del job),
con la compuerta de radicación APAGADA lee de verdad sin escribir:
  - Redis: si el radicado ya fue reclamado (GET de la llave, `RedisSoloLectura`);
    reclamar, liberar y marcar procesado siguen retenidos.
  - Postgres: la tabla juzgados, con una sesión de solo lectura; si no conecta,
    la tabla del repositorio.
SGDEA sigue simulado: su única lectura en el job es sobre el número que devuelve la
radicación. Cada corrida dice en el evento «fuentes» qué fue real y qué un doble.

Con la compuerta ENCENDIDA (RADICACION_REAL=si y PERMITIR_RADICACION_REAL=si) corre
como el job: MODO_TEST=false, OutlookService, RedisService (con el cerrojo del buzón),
JuzgadosService y SGDEAService reales. Solo atiende hilos traídos del buzón real.
"""
from __future__ import annotations

import asyncio
import contextvars
import csv
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MARCA = "\x1eEVT "
RAIZ = Path(__file__).resolve().parent.parent
MOTOR = Path(os.environ.get("RUTA_MOTOR_TUTELAS", RAIZ / "motores" / "tutelas"))
BANDEJA = Path(os.environ.get("RUTA_BANDEJA", RAIZ / "datos" / "bandeja"))
VIVO = Path(os.environ.get("RUTA_VIVO", RAIZ / "datos" / "vivo"))

import solo_lectura  # noqa: E402  (junto a este archivo)

# La compuerta se decide UNA vez, al arrancar: el servidor reemplaza al trabajador si cambia.
REAL = solo_lectura.radicacion_real()
_mascara = solo_lectura.Mascara().recordar()  # antes de tocar el entorno
_SECRETOS = ("TUTELAS-MICROSOFT-CLIENT-SECRET", "TUTELAS-REDIS-PASSWORD", "TUTELAS-SGDEA-CLIENT-SECRET",
             "TUTELAS-SGDEA-PASSWORD", "TUTELAS-DATABASE-PASSWORD")
FALTANTES = solo_lectura.faltantes(_SECRETOS)

# Antes de importar el motor: su configuración se lee al importarse y exige estos
# secretos aunque esté en prueba. Los que trae el entorno del flujo se conservan.
os.environ["MODO_TEST"] = "false" if REAL else "true"
os.environ.pop("MODO_UAT", None)
for _s in _SECRETOS:
    if not solo_lectura.definida(_s):
        os.environ[_s] = "no-se-usa"
os.environ.setdefault("TUTELAS-GCP-PROJECT", os.environ.get("GCP_PROJECT", "positiva-sgda-prd-416417"))
# El Cloud Run Job de producción define TUTELAS-RADICAR-ENLACE-CON-ADJUNTOS=true
# (gcloud run jobs describe ms-radicacion-ia-tutelas): con enlace y PDF anexo, radica.
os.environ.setdefault("TUTELAS-RADICAR-ENLACE-CON-ADJUNTOS", "true")
os.environ.setdefault("TEST_OUTPUT_DIR", str(Path(os.environ.get("RUTA_TRABAJO", RAIZ / "datos" / "trabajo")) / "tutelas"))
os.chdir(MOTOR)
sys.path.insert(0, str(MOTOR))

_salida = sys.stdout
_cerrojo = threading.Lock()
_corrida: contextvars.ContextVar = contextvars.ContextVar("corrida", default=None)

# El número de paso del motor (op.paso) -> el nodo del catálogo (catalogo/tutelas.json).
# «hilo» y «enlace» no tienen número en el motor: los abre este trabajador.
NODO_DEL_PASO = {"1": "remitente", "2": "tramite", "3": "radicado", "3b": "radicado_pdf", "4": "juzgado",
                 "5": "anexos", "6": "duplicados", "7": "radicar", "8": "trazabilidad", "9": "informar", "10": "archivar"}
# Mensajes que el motor emite después de cerrar la búsqueda del radicado pero
# antes del paso 4: son del nodo «radicado» aunque el último paso abierto sea el 3b.
NODO_DEL_MENSAJE = {"Codigo de juzgado": "radicado", "Afectado identificado": "radicado"}


def evento(tipo: str, **datos: Any) -> None:
    linea = _mascara(json.dumps({"tipo": tipo, "corrida": _corrida.get(), "t": time.time(), **datos}, ensure_ascii=False, default=str))
    with _cerrojo:
        _salida.write(MARCA + linea + "\n")
        _salida.flush()


class _Consola:
    def __init__(self) -> None:
        self._p = ""

    def write(self, t: str) -> int:
        self._p += t
        while "\n" in self._p:
            linea, self._p = self._p.split("\n", 1)
            if linea.strip():
                evento("consola", linea=linea[:4000])
        return len(t)

    def flush(self) -> None:
        pass


# ------------------------------------------------------------- estado del hilo

class Estado:
    def __init__(self) -> None:
        self.nodo: Optional[str] = None
        self.inicio = 0.0
        self.etiqueta = ""
        self.escrituras: List[str] = []
        self.radicacion: Optional[dict] = None
        self.numero_radicado = ""
        self.origen_radicado = ""


_estado: contextvars.ContextVar = contextvars.ContextVar("estado")


def _abrir_nodo(nodo: str) -> None:
    _cerrar_nodo()
    e = _estado.get()
    e.nodo, e.inicio = nodo, time.time()
    evento("etapa_inicio", etapa=nodo)


def _cerrar_nodo(fallo: str = "") -> None:
    e = _estado.get()
    if e.nodo:
        evento("etapa_fin", etapa=e.nodo, ms=int((time.time() - e.inicio) * 1000), fallo=fallo)
        e.nodo = None


def _nodo_de(nombre: str) -> str:
    return NODO_DEL_MENSAJE.get(nombre) or _estado.get().nodo or "hilo"


def instalar_operaciones() -> None:
    """Los mensajes del propio motor se anuncian como etapas y decisiones."""
    from Tutelas import operaciones as op

    paso, ok, fallo, omitido, no_radicado, radicado = op.paso, op.ok, op.fallo, op.omitido, op.no_radicado, op.radicado

    def _paso(numero, nombre: str, detalle: str = ""):
        _abrir_nodo(NODO_DEL_PASO.get(str(numero), f"paso_{numero}"))
        return paso(numero, nombre, detalle)

    def _ok(nombre: str, detalle: str = ""):
        if nombre == "Radicado obtenido":
            _estado.get().origen_radicado = str(detalle or "")
        evento("paso", nodo=_nodo_de(nombre), titulo=nombre, tono="ok", detalle=str(detalle or "")[:1500])
        return ok(nombre, detalle)

    def _omitido(nombre: str, detalle: str = ""):
        evento("paso", nodo=_nodo_de(nombre), titulo=nombre, tono="info", detalle=str(detalle or "")[:1500])
        return omitido(nombre, detalle)

    def _fallo(nombre: str, detalle: str = ""):
        evento("paso", nodo=_nodo_de(nombre), titulo=nombre, tono="error", detalle=str(detalle or "")[:1500])
        return fallo(nombre, detalle)

    def _no_radicado(etiqueta: str):
        _estado.get().etiqueta = etiqueta
        _cerrar_nodo(fallo=etiqueta)
        _abrir_nodo("no_radicado")
        evento("paso", nodo="no_radicado", titulo=f"No se radica ({etiqueta})", tono="aviso",
               detalle=f"{op.motivo(etiqueta)} {op.accion(etiqueta)}", etiqueta=etiqueta)
        return no_radicado(etiqueta)

    def _radicado(numero_radicado: str, radicado_23: str, n_archivos: int):
        _estado.get().numero_radicado = str(numero_radicado or "")
        evento("paso", nodo=_nodo_de(""), titulo="Radicado en SGDEA", tono="ok",
               detalle=f"{numero_radicado} | proceso {radicado_23} | {n_archivos} archivo(s)")
        return radicado(numero_radicado, radicado_23, n_archivos)

    op.paso, op.ok, op.fallo, op.omitido, op.no_radicado, op.radicado = _paso, _ok, _fallo, _omitido, _no_radicado, _radicado


# ------------------------------------------------------------- correos de disco

_indice: Dict[Path, Tuple[float, str, str, str]] = {}


def _cabecera(ruta: Path) -> Tuple[str, str, str]:
    """(conversationId, id, _flujo) de un mensaje.json, con caché por mtime."""
    mt = ruta.stat().st_mtime
    c = _indice.get(ruta)
    if c and c[0] == mt:
        return c[1], c[2], c[3]
    try:
        m = json.loads(ruta.read_text(encoding="utf-8"))
        r = (m.get("conversationId") or "", m.get("id") or "", m.get("_flujo") or "")
    except Exception:
        r = ("", "", "")
    _indice[ruta] = (mt, *r)
    return r


def _a_correo(carpeta: Path, msg: dict):
    """Un mensaje de disco como el `Correo` que arma `OutlookService.obtener_correos`."""
    from Tutelas.models.correo import Correo
    from Tutelas.services.outlook_service import OutlookService

    de = (msg.get("from") or {}).get("emailAddress") or {}
    cuerpo = (msg.get("body") or {}).get("content", "")
    fecha = msg.get("receivedDateTime") or datetime.now(timezone.utc).isoformat()
    return Correo(id=msg.get("id") or carpeta.name, subject=msg.get("subject", "") or "", body_content=cuerpo,
                  body_text=OutlookService._strip_html(OutlookService.__new__(OutlookService), cuerpo),
                  remitente_email=de.get("address", "") or "", remitente_nombre=de.get("name", "") or "",
                  fecha_recibido=datetime.fromisoformat(fecha.replace("Z", "+00:00")),
                  conversation_id=msg.get("conversationId"), adjuntos=[])


def _hilo(caso: str) -> List[Tuple[Path, dict, Any]]:
    """El hilo del caso: sus correos guardados con el mismo conversationId, en orden
    cronológico (el último es el principal). Solo se juntan correos del mismo origen
    (la misma carpeta y el mismo `_flujo`), como los del job salen de una sola carpeta."""
    for base in (VIVO, BANDEJA):
        if (base / caso / "mensaje.json").exists():
            break
    else:
        raise FileNotFoundError(f"no existe el caso {caso}")
    propio = base / caso
    msg = json.loads((propio / "mensaje.json").read_text(encoding="utf-8"))
    conv, flujo = msg.get("conversationId") or "", msg.get("_flujo") or ""
    carpetas = [propio]
    if conv:
        vistos = {msg.get("id") or caso}
        for ruta in sorted(base.glob("*/mensaje.json")):
            if ruta.parent == propio:
                continue
            c, i, f = _cabecera(ruta)
            if c == conv and f == flujo and i not in vistos:
                vistos.add(i)
                carpetas.append(ruta.parent)
    hilo = []
    for carpeta in carpetas:
        m = msg if carpeta == propio else json.loads((carpeta / "mensaje.json").read_text(encoding="utf-8"))
        hilo.append((carpeta, m, _a_correo(carpeta, m)))
    hilo.sort(key=lambda x: x[2].fecha_recibido)
    return hilo


# --------------------------------------------------------------------- dobles

class OutlookDeDisco:
    """El buzón del hilo, servido desde disco. Toda escritura se anota y no sale."""

    def __init__(self, hilo: List[Tuple[Path, dict, Any]]) -> None:
        self.por_id = {c.id: (carpeta, m) for carpeta, m, c in hilo}
        self.principal = hilo[-1][2].id

    def obtener_adjuntos(self, correo_id: str):
        """Como GET /messages/{id}/attachments: todos, también los incrustados."""
        from Tutelas.models.correo import Adjunto

        if correo_id not in self.por_id:
            print("      [OUTLOOK] Mensaje no encontrado (404), omitiendo adjuntos")
            return []
        _, m = self.por_id[correo_id]
        return [Adjunto(id=a.get("archivo") or f"sin-contenido-{i}", nombre=a.get("name", "") or "", content_type=a.get("contentType", "") or "")
                for i, a in enumerate(m.get("_adjuntos", []))]

    def descargar_adjunto(self, correo_id: str, adjunto_id: str) -> bytes:
        """Como GET /attachments/{id}: los adjuntos sin contentBytes vienen vacíos."""
        carpeta, _ = self.por_id.get(correo_id, (None, None))
        ruta = carpeta / "adj" / adjunto_id if carpeta else None
        return ruta.read_bytes() if ruta and ruta.is_file() else b""

    def _escribir(self, operacion: str, correo_id: str, detalle: List[str], resultado: Any, **extra: Any):
        _estado.get().escrituras.append(operacion)
        evento("escritura", sistema="buzon", operacion=operacion, bloqueada=True, detalle=detalle,
               propio=correo_id == self.principal, **extra)
        return resultado

    def etiquetar_correo(self, correo_id: str, etiqueta: str) -> bool:
        return self._escribir("marcar", correo_id, [etiqueta], True, marca=etiqueta, quitar_de_proceso=False)

    def responder_no_radicado(self, correo_id: str, motivo: str, radicado_23=None, accion: str = "", asunto_original=None) -> bool:
        return self._escribir("responder_correo", correo_id,
                              ["a notificacionesjudiciales@positiva.gov.co: Radicación no realizada", motivo, f"proceso {radicado_23 or '—'}", accion], True)

    def responder_radicacion_exitosa(self, correo_id: str, numero_radicado: str) -> bool:
        return self._escribir("responder_correo", correo_id,
                              ["a notificacionesjudiciales@positiva.gov.co: Radicación exitosa con IA", f"radicado {numero_radicado}"], True)

    def _mover(self, ids, destino: str) -> int:
        for i in ids:
            self._escribir("move_email", i, [destino], True, destino=destino)
        return len(ids)

    def mover_hilo_a_tutelas(self, ids) -> int:
        return self._mover(ids, "TUTELAS")

    def mover_hilo_a_bandeja_entrada(self, ids) -> int:
        return self._mover(ids, "Bandeja de entrada")

    def __getattr__(self, nombre: str):
        if nombre.startswith("_"):
            raise AttributeError(nombre)

        def _anotar(*a, **k):
            return self._escribir(nombre, str(a[0]) if a else "", [str(x)[:120] for x in a][:3], True)
        return _anotar


def _redis_en_memoria(cliente=None):
    """
    El Redis del hilo con la compuerta apagada. Con `cliente` (un `RedisSoloLectura`
    con las credenciales del job) consulta de verdad si la llave ya existe, que es lo
    que decide el SET NX del job; el reclamo en sí queda retenido, en memoria.
    """
    from Tutelas.config import REDIS_CONFIG
    from Tutelas.services.redis_service import RedisService

    class RedisEnMemoria(RedisService):
        """SET NX en memoria con las mismas llaves (hash SHA-256 del texto) que el real."""

        def __init__(self) -> None:
            self.reclamados: set = set()
            self.config = REDIS_CONFIG

        def _ya_reclamado_en_redis(self, h: str) -> bool:
            if cliente is None or not h:
                return False
            t = time.time()
            try:
                existe = cliente.get(f"{self.config['prefix']}{h}") is not None
            except Exception as e:
                evento("lectura_real", sistema="redis", operacion="get", ok=False, ms=int((time.time() - t) * 1000),
                       error=_mascara(f"{type(e).__name__}: {e}"[:300]), detalle="sin respuesta: se sigue como si no estuviera")
                return False
            evento("lectura_real", sistema="redis", operacion="get", ok=True, ms=int((time.time() - t) * 1000),
                   resultado="ya reclamado (duplicado)" if existe else "no reclamado")
            return existe

        def reclamar(self, texto, ttl_segundos: int = 2592000):
            h = self.generar_hash(texto)
            if not h:
                return h, True
            nuevo = h not in self.reclamados and not self._ya_reclamado_en_redis(h)
            self.reclamados.add(h)
            evento("escritura", sistema="redis", operacion="reclamar", bloqueada=True, detalle=[str(texto)[:160]], nuevo=nuevo)
            return h, nuevo

        def liberar(self, h) -> bool:
            self.reclamados.discard(h)
            return True

        def marcar_procesado(self, h) -> bool:
            evento("escritura", sistema="redis", operacion="marcar_procesado", bloqueada=True, detalle=[str(h)[:16]])
            return True

        def close(self) -> None:
            pass

    return RedisEnMemoria()


class _Respuesta:
    def __init__(self, codigo: int, cuerpo: Any) -> None:
        self.status_code, self._cuerpo = codigo, cuerpo
        self.text = json.dumps(cuerpo, ensure_ascii=False)
        self.headers = {"Content-Type": "application/json"}
        self.content = self.text.encode()

    def json(self):
        return self._cuerpo


class _SesionSgdeaDePrueba:
    """La sesión HTTP de SGDEAService: anota cada petición y responde como SGDEA, sin red."""

    def __init__(self, api: str, token: str) -> None:
        self.api, self.token = api.rstrip("/"), token

    def post(self, url, headers=None, data=None, files=None, json=None, timeout=None, **k):  # noqa: A002
        u = str(url)
        if u == self.token:
            return _Respuesta(200, {"access_token": "prueba", "expires_in": 3600})
        if u == f"{self.api}/tutela":
            import json as _json

            cuerpo = _json.loads((data or {}).get("body") or "{}")
            archivos = [{"nombre": f[1][0], "bytes": len(f[1][1] or b""), "tipo": f[1][2]} for f in (files or [])]
            numero = f"PRUEBA-{str(cuerpo.get('codigoProceso') or 'TUTELA')[-10:]}"
            carga = {"url": u, "body": cuerpo, "archivos": archivos, "radicado_de_prueba": numero}
            _estado.get().radicacion = carga
            evento("escritura", sistema="sgdea", operacion="radicar", bloqueada=True, n_adjuntos=len(archivos), payload=carga)
            return _Respuesta(201, {"radicado": numero})
        if u == f"{self.api}/trazabilidad/crear":
            evento("escritura", sistema="sgdea", operacion="trazabilidad", bloqueada=True, payload=json)
            return _Respuesta(201, {"ok": True})
        raise PermissionError(f"el visor no escribe en SGDEA: POST {u[:120]}")

    def get(self, url, headers=None, timeout=None, **k):
        u = str(url)
        if u.startswith(f"{self.api}/tutela/info/"):
            numero = u.rsplit("/", 1)[-1]
            return _Respuesta(200, {"idTutela": "prueba", "idRadicado": numero})
        raise PermissionError(f"el visor no consulta SGDEA: GET {u[:120]}")

    def close(self) -> None:
        pass


class _SinRed:
    """Reemplaza el módulo `requests` dentro de sgdea_service: solo deja las excepciones."""

    def __init__(self, real) -> None:
        self._real = real

    def __getattr__(self, nombre: str):
        if nombre == "exceptions":
            return self._real.exceptions
        raise PermissionError(f"el visor no hace peticiones a SGDEA (requests.{nombre})")


def _sgdea_de_prueba():
    """El SGDEAService real (cuerpo, archivos, reintentos, trazabilidad) sobre la sesión de prueba.

    `radicar_tutela` y `crear_trazabilidad` solo devuelven un stub cuando el módulo
    está en MODO_TEST; aquí se apaga esa bandera en el módulo para que corra el código
    real, y a cambio el módulo pierde la red: su sesión es la de prueba y `requests`
    solo conserva las excepciones.
    """
    import requests
    from Tutelas.services import sgdea_service as m

    m.MODO_TEST = False
    if not isinstance(m.requests, _SinRed):
        m.requests = _SinRed(requests)

    class SgdeaDePrueba(m.SGDEAService):
        def __init__(self) -> None:
            self.config = m.SGDEA_CONFIG
            self.access_token = "prueba"
            self.token_expires = datetime.now() + timedelta(days=1)
            self.session = _SesionSgdeaDePrueba(self.config["api_url"], self.config["token_url"])

    return SgdeaDePrueba()


def _juzgados_del_csv():
    from Tutelas.models.correo import Juzgado
    from Tutelas.services.juzgados_service import JuzgadosService

    filas = list(csv.DictReader(open(MOTOR / "juzgados_actualizada.csv", encoding="utf-8-sig")))
    solo_digitos = lambda s: "".join(c for c in str(s or "") if c.isdigit())  # noqa: E731
    for f in filas:
        f["_digitos"] = solo_digitos(f.get("codigo"))

    def a_juzgado(f: dict) -> Juzgado:
        return Juzgado(id_juzgado=int(f.get("idJuzgado") or 0) or None, nombre=f.get("nombre", "") or "", direccion=f.get("direccion", "") or "",
                       pais=f.get("pais") or "Colombia", departamento=f.get("departamento", "") or "", municipio=f.get("municipio", "") or "",
                       codigo=f.get("codigo", "") or "", telefono=f.get("telefono", "") or "", celular=f.get("celular", "") or "", correo=f.get("correo", "") or "")

    class JuzgadosDelCsv(JuzgadosService):
        """La misma búsqueda que la consulta SQL, sobre la tabla del repositorio."""

        def __init__(self) -> None:
            pass

        def buscar_por_codigo(self, codigo):
            digitos = solo_digitos(codigo)
            if not digitos:
                return None
            f = next((f for f in filas if f["_digitos"] == digitos), None)
            evento("proveedor", proveedor="juzgados", operacion="buscar_por_codigo", ok=True, encontrado=bool(f), documento=digitos, nombre=(f or {}).get("nombre", ""), ms=0)
            return a_juzgado(f) if f else None

        def buscar_por_nombre(self, nombre, ciudad=None):
            if not nombre:
                return None
            n, c = nombre.upper(), (ciudad or "").upper()
            f = next((f for f in filas if n in (f.get("nombre") or "").upper() and (not c or c in (f.get("municipio") or "").upper())), None)
            evento("proveedor", proveedor="juzgados", operacion="buscar_por_nombre", ok=True, encontrado=bool(f), documento=nombre, nombre=(f or {}).get("nombre", ""), ms=0)
            return a_juzgado(f) if f else None

        def close(self) -> None:
            pass

    return JuzgadosDelCsv()


def _cliente_redis_solo_lectura():
    """El Redis del job (TUTELAS-REDIS-*) con un cliente que solo lee, o None si no hay clave."""
    if not solo_lectura.definida("TUTELAS-REDIS-PASSWORD"):
        _fuentes["redis"] = {"real": False, "detalle": "sin TUTELAS-REDIS-PASSWORD en el entorno del flujo: memoria vacía en cada corrida"}
        return None
    import redis
    from Tutelas.config import REDIS_CONFIG

    c = REDIS_CONFIG
    real = redis.Redis(host=c["host"], port=c["port"], username=c["username"], password=c["password"],
                       decode_responses=True, socket_timeout=10, socket_connect_timeout=10)
    _fuentes["redis"] = {"real": True, "detalle": "consulta (GET) si el radicado ya fue reclamado; reclamar y marcar quedan retenidos"}
    return solo_lectura.RedisSoloLectura(real)


def _juzgados():
    """La tabla juzgados de Postgres en solo lectura, o la del repositorio si no hay clave o no conecta."""
    if not solo_lectura.definida("TUTELAS-DATABASE-PASSWORD"):
        _fuentes["postgres"] = {"real": False, "detalle": "sin TUTELAS-DATABASE-PASSWORD en el entorno del flujo: tabla juzgados_actualizada.csv del repositorio"}
        return _juzgados_del_csv()
    from Tutelas.services.juzgados_service import JuzgadosService

    class JuzgadosSoloLectura(JuzgadosService):
        """El JuzgadosService del job sobre una sesión donde ninguna transacción escribe."""

        def _conectar(self):
            try:
                self._conn = solo_lectura.conectar_postgres_solo_lectura(self.db_config)
            except Exception as e:
                self.error = _mascara(f"{type(e).__name__}: {e}"[:300])
                self._conn = None

    j = JuzgadosSoloLectura()
    if j._conn is None:
        _fuentes["postgres"] = {"real": False, "detalle": f"la base no respondió desde aquí ({getattr(j, 'error', '')}): tabla del repositorio"}
        return _juzgados_del_csv()
    _fuentes["postgres"] = {"real": True, "detalle": "tabla juzgados con sesión de solo lectura"}
    for nombre in ("buscar_por_codigo", "buscar_por_nombre"):
        original = getattr(j, nombre)

        def envolver(f, n):
            def envoltorio(*a, **k):
                t = time.time()
                r = f(*a, **k)
                evento("proveedor", proveedor="juzgados", operacion=n, ok=True, encontrado=bool(r), documento=str(a[0] if a else ""),
                       nombre=getattr(r, "nombre", "") if r else "", ms=int((time.time() - t) * 1000), real=True)
                return r
            return envoltorio
        setattr(j, nombre, envolver(original, nombre))
    return j


class OutlookQueEscribe:
    """Solo con la compuerta encendida: el OutlookService del job, con cada escritura anunciada."""

    def __init__(self, real, principal: str) -> None:
        if not solo_lectura.radicacion_real():
            raise PermissionError("el buzón que escribe solo existe con la compuerta de radicación encendida")
        self._real, self.principal = real, principal

    def __getattr__(self, nombre: str):
        import buzon_tutelas

        f = getattr(self._real, nombre)
        if nombre in buzon_tutelas.LECTURA or not callable(f):
            return f

        def _escribir(*a, **k):
            _estado.get().escrituras.append(nombre)
            r = f(*a, **k)
            evento("escritura", sistema="buzon", operacion=nombre, bloqueada=False, ok=bool(r),
                   detalle=[str(x)[:120] for x in a][:3], propio=bool(a) and str(a[0]) == self.principal)
            return r
        return _escribir


_fuentes: Dict[str, Any] = {}


def _anunciar_fuentes() -> None:
    evento("compuerta", radicacion_real=REAL, texto=solo_lectura.ROTULO_ENCENDIDA if REAL else solo_lectura.ROTULO_APAGADA)
    if REAL:
        lecturas = [{"sistema": s, "real": True, "detalle": "como el job: lee y escribe"} for s in ("Microsoft Graph", "Redis", "Postgres", "SGDEA")]
    else:
        lecturas = [
            {"sistema": "Microsoft Graph", "real": True, "detalle": "el hilo y sus adjuntos se leyeron del buzón real al traerlos (solo GET); etiquetar, responder y mover quedan retenidos"},
            {"sistema": "Redis", **_fuentes.get("redis", {"real": False, "detalle": "memoria"})},
            {"sistema": "Postgres", **_fuentes.get("postgres", {"real": False, "detalle": "tabla del repositorio"})},
            {"sistema": "SGDEA", "real": False, "detalle": "radicar y trazabilidad quedan retenidas (sesión de prueba sin red)"},
        ]
    lecturas.append({"sistema": "Vertex AI", "real": True, "detalle": "modelo real con el ADC"})
    evento("fuentes", lecturas=lecturas)


def _ia_observada():
    """El AIExtractor real; se anuncian sus tres llamadas al modelo (extraer_todo solo las combina)."""
    from Tutelas.services.ai_extractor import AIExtractor

    ia = AIExtractor()
    for nombre in ("extraer_radicado_y_juzgado", "extraer_documento_afectado", "extraer_radicado_de_pdf"):
        original = getattr(ia, nombre)

        def envolver(f, n):
            def envoltorio(*a, **k):
                ultimo = f"{a[0]}\n{a[2][:600]}" if len(a) >= 3 and isinstance(a[2], str) else f"{n}: PDF de {len(a[0]) if a else 0} bytes"
                evento("modelo_inicio", vuelta=1, quien=n, modelo=getattr(ia, "model", "gemini"), sistema="", ultimo=ultimo[:800])
                t = time.time()
                try:
                    r = f(*a, **k)
                except Exception as e:
                    evento("modelo_fin", vuelta=1, quien=n, ms=int((time.time() - t) * 1000), ok=False, error=f"{type(e).__name__}: {e}"[:300])
                    raise
                evento("modelo_fin", vuelta=1, quien=n, ms=int((time.time() - t) * 1000), ok=True, tokens_entrada=0, tokens_salida=0,
                       texto=json.dumps(getattr(r, "__dict__", r), ensure_ascii=False, default=str)[:1500], llamadas=[])
                return r
            return envoltorio

        setattr(ia, nombre, envolver(original, nombre))
    return ia


# --------------------------------------------------------------------- un hilo

_servicios: Dict[str, Any] = {}


def _hallazgos(principal, origen) -> Tuple[dict, dict, list]:
    """Lo que el job dejó resuelto en el hilo, con quién lo resolvió."""
    e = _estado.get()
    d = getattr(principal, "datos_extraidos", None)
    j = getattr(principal, "juzgado", None)
    valido = lambda v: v if v and str(v).upper() not in ("NO_ENCONTRADO", "ERROR_INCOMPLETO") else None  # noqa: E731
    origen_rad = e.origen_radicado
    por_ia = "lectura del correo" in origen_rad or "lectura del anexo" in origen_rad
    campos = {
        "radicado_23": (valido(getattr(d, "radicado_23", None)) if getattr(d, "es_valido", False) else None, "ia" if por_ia else "algoritmo", origen_rad or "IA / regex"),
        "codigo_juzgado": (valido(getattr(d, "codigo_juzgado", None)) if getattr(d, "es_valido", False) else None,
                           "ia" if "lectura del correo" in origen_rad else "algoritmo", "IA" if "lectura del correo" in origen_rad else "12 primeros dígitos del radicado"),
        "juzgado_en_el_texto": (valido(getattr(d, "juzgado_nombre_ocr", None)), "ia", "IA sobre el correo principal"),
        "ciudad": (valido(getattr(d, "ciudad", None)), "ia", "IA sobre el correo principal"),
        "documento_afectado": (valido(getattr(d, "numero_documento", None)), "ia", f"IA (fuente: {getattr(d, 'fuente_documento', None) or 'N/A'})"),
        "tipo_documento": (getattr(d, "tipo_documento", None) if valido(getattr(d, "numero_documento", None)) else None, "ia", "IA"),
        "juzgado": (getattr(j, "nombre", None), "registro", "tabla de juzgados"),
        "municipio_juzgado": (getattr(j, "municipio", None), "registro", "tabla de juzgados"),
        "remitente_judicial": (getattr(origen, "remitente_email", None) if origen else None, "algoritmo", "primer correo del hilo de la Rama Judicial"),
    }
    hallazgos = {k: {"valor": v or "", "metodo": m, "confianza": 1 if v else 0, "evidencia": ""} for k, (v, _, m) in campos.items()}
    atribucion = {k: a for k, (v, a, _) in campos.items() if v}
    return hallazgos, atribucion, [k for k, (v, _, _) in campos.items() if not v]


def procesar(orden: dict) -> None:
    from Tutelas import operaciones as op
    from Tutelas.main import OrquestadorRadicacion
    from Tutelas.services.sharepoint_service import SharePointService

    caso = orden["caso"]
    _estado.set(Estado())
    t = time.time()
    try:
        hilo = _hilo(caso)
    except Exception as e:
        evento("resultado", caso=caso, accion="error", motivo=f"{type(e).__name__}: {e}"[:400], segundos=0)
        return
    if REAL and not all(m.get("_vivo") and m.get("_flujo") == "tutelas" for _, m, _ in hilo):
        # Una copia local no es un hilo de la bandeja del job: radicarla duplicaría.
        evento("resultado", caso=caso, accion="error", segundos=0,
               motivo="con la radicación real solo se corren hilos traídos del buzón real de tutelas")
        return
    if "ia" not in _servicios:
        _servicios["ia"] = _ia_observada()
        if REAL:
            from Tutelas.services.juzgados_service import JuzgadosService
            from Tutelas.services.outlook_service import OutlookService
            from Tutelas.services.redis_service import RedisService
            from Tutelas.services.sgdea_service import SGDEAService

            _servicios["juzgados"] = JuzgadosService()
            _servicios["outlook_real"] = OutlookService()
            _servicios["redis_real"] = RedisService()
            _servicios["sgdea_real"] = SGDEAService()
        else:
            _servicios["juzgados"] = _juzgados()
            _servicios["redis_lectura"] = _cliente_redis_solo_lectura()
    correos = [c for _, _, c in hilo]
    principal = correos[-1]
    o = OrquestadorRadicacion.__new__(OrquestadorRadicacion)
    if REAL:
        o.outlook = OutlookQueEscribe(_servicios["outlook_real"], principal.id)
        o.redis = _servicios["redis_real"]
        o.sgdea = _servicios["sgdea_real"]
    else:
        o.outlook = OutlookDeDisco(hilo)
        o.redis = _redis_en_memoria(_servicios.get("redis_lectura"))
        o.sgdea = _sgdea_de_prueba()
    o.ai = _servicios["ia"]
    o.juzgados = _servicios["juzgados"]
    o.sharepoint = SharePointService()

    # El paso 2.5 del job (enlace al expediente) no tiene número: se abre su nodo
    # cuando el motor llama a la misma función con que lo resuelve.
    buscar_enlace = o._buscar_enlace_sharepoint

    def _buscar_enlace(correos_hilo):
        _abrir_nodo("enlace")
        enlace = buscar_enlace(correos_hilo)
        evento("paso", nodo="enlace", titulo="Enlace al expediente" if enlace else "Sin enlace al expediente",
               tono="info", detalle=(enlace or "ningún correo del hilo remite el expediente por enlace")[:300])
        return enlace
    o._buscar_enlace_sharepoint = _buscar_enlace

    evento("inicio", caso=caso, asunto=principal.subject, modelo="tutelas", flujo="tutelas", radicacion_real=REAL,
           hilo=[{"caso": carpeta.name, "recibido": c.fecha_recibido.isoformat(), "remitente": c.remitente_email} for carpeta, _, c in hilo])
    _anunciar_fuentes()
    _abrir_nodo("hilo")
    evento("paso", nodo="hilo", titulo="Hilo de conversación", tono="ok",
           detalle=f"{len(correos)} correo(s) con el mismo conversationId; principal: el más reciente ({hilo[-1][0].name})")

    # Como `ejecutar` del job: con escrituras reales, el buzón se reserva para que esta
    # corrida y el job no se pisen. Si otra ejecución lo tiene, este hilo no se toca.
    cerrojo = f"positiva-ia-{os.getpid()}-{caso}" if REAL else ""
    if REAL and not o.redis.tomar_cerrojo(cerrojo):
        _cerrar_nodo()
        evento("resultado", caso=caso, accion="error", segundos=round(time.time() - t, 1),
               motivo=f"otra ejecución tiene reservado el buzón ({o.redis.titular_cerrojo()}): el hilo no se toca")
        return

    ok, error = False, None
    try:
        ok = bool(o.procesar_hilo(correos))
    except Exception as ex:
        # Lo mismo que _ejecutar_lote: el hilo se cierra con ERROR-INESPERADO.
        error = ex
        op.fallo("Procesar hilo", f"{type(ex).__name__}: {ex}")
        traceback.print_exc(file=sys.stdout)
        try:
            o._mover_hilo_fallido(correos, "ERROR-INESPERADO")
        except Exception as e2:
            op.fallo("Cerrar hilo con error", f"{type(e2).__name__}: {e2}")
    finally:
        if REAL:
            try:
                o.redis.soltar_cerrojo(cerrojo)
            except Exception as e3:
                op.fallo("Soltar el cerrojo del buzón", f"{type(e3).__name__}: {e3}")
    _cerrar_nodo()

    try:
        e = _estado.get()
        origen = o.buscar_correo_judicial(correos)
        hallazgos, atribucion, sin_resolver = _hallazgos(principal, origen)
        etiqueta = "" if ok else (e.etiqueta or "ERROR-INESPERADO")
        evento("resultado", caso=caso, accion="radicado" if ok else "manual",
               motivo=etiqueta, etiqueta=etiqueta,
               explicacion="" if ok else f"{op.motivo(etiqueta)} {op.accion(etiqueta)}",
               motivo_funcional="" if ok else op.motivo(etiqueta), accion_area="" if ok else op.accion(etiqueta),
               radicado=e.numero_radicado if ok else "",
               radicado_23=hallazgos["radicado_23"]["valor"],
               hallazgos=hallazgos, atribucion=atribucion, sin_resolver=sin_resolver, solicitud=e.radicacion,
               hilo=[carpeta.name for carpeta, _, _ in hilo],
               avisos=[f"{type(error).__name__}: {error}"[:300]] if error else [],
               traza="".join(traceback.format_exception(error))[-2500:] if error else None,
               escrituras_buzon=e.escrituras, segundos=round(time.time() - t, 1))
    except Exception as ex:
        evento("resultado", caso=caso, accion="error", motivo=f"{type(ex).__name__}: {ex}"[:500], traza=traceback.format_exc()[-2500:], segundos=round(time.time() - t, 1))


async def principal() -> None:
    sys.stdout = _Consola()
    try:
        _estado.set(Estado())
        from Tutelas import config as conf_tutelas

        if REAL:
            # Como el job: sin MODO_TEST y con todos sus secretos; si falta uno, no arranca.
            if conf_tutelas.MODO_TEST or conf_tutelas.MODO_UAT:
                raise RuntimeError("la compuerta está encendida pero el motor de tutelas quedó en modo prueba")
            if FALTANTES:
                raise RuntimeError("la compuerta está encendida pero faltan en el entorno del flujo: " + ", ".join(FALTANTES))
        elif not conf_tutelas.MODO_TEST:
            raise RuntimeError("el motor de tutelas no quedó en MODO_TEST: el visor no arranca")
        instalar_operaciones()
        evento("listo", pid=os.getpid(), motor=str(MOTOR), radicacion_real=REAL)
    except Exception as e:
        evento("fallo_inicio", error=f"{type(e).__name__}: {e}", traza=traceback.format_exc()[-2500:])
        return
    while True:
        linea = await asyncio.to_thread(sys.stdin.readline)
        if not linea:
            break
        try:
            orden = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if orden.get("cmd") in ("procesar", "procesar_lote"):
            items = [{"corrida": orden["corrida"], "caso": orden["caso"]}] if orden["cmd"] == "procesar" else orden["corridas"]
            # La orden dice con qué compuerta la mandó el servidor: si no es la de este proceso, no se corre.
            if bool(orden.get("radicacion_real", False)) != REAL:
                for item in items:
                    _corrida.set(item["corrida"])
                    evento("resultado", caso=item["caso"], accion="error", segundos=0,
                           motivo="la compuerta de radicación cambió: el trabajador se reinicia y no corre esta orden")
                _corrida.set(None)
                evento("libre")
                return
            for item in items:
                _corrida.set(item["corrida"])
                await asyncio.to_thread(contextvars.copy_context().run, procesar, {**orden, **item})
            evento("libre")
            _corrida.set(None)


if __name__ == "__main__":
    asyncio.run(principal())
