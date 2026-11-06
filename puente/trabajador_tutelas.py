"""
Trabajador del motor de radicación de tutelas (`motores/tutelas`).

Corre `OrquestadorRadicacion.procesar_hilo` —el código del job, sin cambios—
sobre un correo de la bandeja y anuncia cada paso con el protocolo del visor.
Los pasos salen del propio motor: `op.paso`, `op.ok` y `op.fallo`, que el job
imprime en su consola, se anuncian como etapas y decisiones.

Nunca escribe en un sistema real:
  - Outlook: los adjuntos salen de disco; etiquetar, responder y mover se
    anotan como «lo que el job haría» y devuelven éxito sin llamar a Graph.
  - Redis: en memoria (la deduplicación entre corridas no aplica aquí).
  - SGDEA: la radicación se anota con su carga y devuelve un radicado de prueba.
  - Juzgados: la tabla del repositorio (juzgados_actualizada.csv), con la misma
    búsqueda que hace la consulta SQL, en solo lectura.
La IA es la real: el AIExtractor del motor sobre Vertex.
"""
from __future__ import annotations

import asyncio
import contextvars
import csv
import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

MARCA = "\x1eEVT "
RAIZ = Path(__file__).resolve().parent.parent
MOTOR = Path(os.environ.get("RUTA_MOTOR_TUTELAS", RAIZ / "motores" / "tutelas"))
BANDEJA = Path(os.environ.get("RUTA_BANDEJA", RAIZ / "datos" / "bandeja"))
VIVO = Path(os.environ.get("RUTA_VIVO", RAIZ / "datos" / "vivo"))

# Antes de importar el motor: su configuración se lee al importarse y exige
# estos secretos aunque esté en prueba. Aquí no se usa ninguno.
os.environ["MODO_TEST"] = "true"
os.environ.pop("MODO_UAT", None)
for _s in ("TUTELAS-MICROSOFT-CLIENT-SECRET", "TUTELAS-REDIS-PASSWORD", "TUTELAS-SGDEA-CLIENT-SECRET",
           "TUTELAS-SGDEA-PASSWORD", "TUTELAS-DATABASE-PASSWORD"):
    os.environ[_s] = "no-se-usa"
os.environ.setdefault("TUTELAS-GCP-PROJECT", os.environ.get("GCP_PROJECT", "positiva-sgda-prd-416417"))
os.environ.setdefault("TEST_OUTPUT_DIR", str(Path(os.environ.get("RUTA_TRABAJO", RAIZ / "datos" / "trabajo")) / "tutelas"))
os.chdir(MOTOR)
sys.path.insert(0, str(MOTOR))

_salida = sys.stdout
_cerrojo = threading.Lock()
_corrida: contextvars.ContextVar = contextvars.ContextVar("corrida", default=None)

# El número de paso del motor -> el nodo del catálogo (catalogo/tutelas.json).
NODO_DEL_PASO = {"1": "remitente", "2": "tramite", "3": "radicado", "3b": "radicado_pdf", "4": "juzgado",
                 "5": "anexos", "6": "duplicados", "7": "radicar", "8": "trazabilidad", "9": "informar", "10": "archivar"}


def evento(tipo: str, **datos: Any) -> None:
    linea = json.dumps({"tipo": tipo, "corrida": _corrida.get(), "t": time.time(), **datos}, ensure_ascii=False, default=str)
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


# ------------------------------------------------------------- estado del correo

class Estado:
    def __init__(self) -> None:
        self.nodo: Optional[str] = None
        self.inicio = 0.0
        self.etiqueta = ""
        self.escrituras: List[str] = []
        self.radicacion: Optional[dict] = None


_estado: contextvars.ContextVar = contextvars.ContextVar("estado")


def _cerrar_nodo(fallo: str = "") -> None:
    e = _estado.get()
    if e.nodo:
        evento("etapa_fin", etapa=e.nodo, ms=int((time.time() - e.inicio) * 1000), fallo=fallo)
        e.nodo = None


def instalar_operaciones() -> None:
    """Los mensajes del propio motor se anuncian como etapas y decisiones."""
    from Tutelas import operaciones as op

    paso, ok, fallo, omitido, no_radicado = op.paso, op.ok, op.fallo, op.omitido, op.no_radicado

    def _paso(numero, nombre: str, detalle: str = ""):
        _cerrar_nodo()
        e = _estado.get()
        e.nodo, e.inicio = NODO_DEL_PASO.get(str(numero), f"paso_{numero}"), time.time()
        evento("etapa_inicio", etapa=e.nodo)
        return paso(numero, nombre, detalle)

    def _ok(nombre: str, detalle: str = ""):
        evento("paso", nodo=_estado.get().nodo or "remitente", titulo=nombre, tono="ok", detalle=str(detalle or "")[:1500])
        return ok(nombre, detalle)

    def _omitido(nombre: str, detalle: str = ""):
        evento("paso", nodo=_estado.get().nodo or "remitente", titulo=nombre, tono="info", detalle=str(detalle or "")[:1500])
        return omitido(nombre, detalle)

    def _fallo(nombre: str, detalle: str = ""):
        evento("paso", nodo=_estado.get().nodo or "remitente", titulo=nombre, tono="error", detalle=str(detalle or "")[:1500])
        return fallo(nombre, detalle)

    def _no_radicado(etiqueta: str):
        _estado.get().etiqueta = etiqueta
        _cerrar_nodo(fallo=etiqueta)
        evento("paso", nodo="no_radicado", titulo=f"No se radica: {op.motivo(etiqueta)}", tono="aviso", detalle=op.accion(etiqueta), etiqueta=etiqueta)
        return no_radicado(etiqueta)

    op.paso, op.ok, op.fallo, op.omitido, op.no_radicado = _paso, _ok, _fallo, _omitido, _no_radicado


# --------------------------------------------------------------------- dobles

class OutlookDeDisco:
    """Sirve los adjuntos desde disco; toda escritura se anota y no sale."""

    def __init__(self, carpeta: Path, mensaje: dict) -> None:
        self.carpeta, self.mensaje = carpeta, mensaje

    def obtener_adjuntos(self, correo_id: str):
        from Tutelas.models.correo import Adjunto

        return [Adjunto(id=a["archivo"], nombre=a.get("name") or a["archivo"], content_type=a.get("contentType") or "application/octet-stream")
                for a in self.mensaje.get("_adjuntos", []) if a.get("archivo") and not a.get("isInline")]

    def descargar_adjunto(self, correo_id: str, adjunto_id: str) -> bytes:
        return (self.carpeta / "adj" / adjunto_id).read_bytes()

    def _escribir(self, nombre: str, detalle: Any, resultado: Any = True):
        _estado.get().escrituras.append(nombre)
        evento("escritura", sistema="buzon", operacion=nombre, bloqueada=True, detalle=detalle)
        return resultado

    def etiquetar_correo(self, correo_id: str, etiqueta: str) -> bool:
        _estado.get().etiqueta = _estado.get().etiqueta or etiqueta
        return self._escribir("marcar", [etiqueta], True)

    def responder_no_radicado(self, correo_id: str, motivo: str, *a, **k) -> bool:
        return self._escribir("responder_correo", [motivo, str(k.get("accion", ""))], True)

    def responder_radicacion_exitosa(self, correo_id: str, numero_radicado: str) -> bool:
        return self._escribir("responder_correo", [f"radicado {numero_radicado}"], True)

    def mover_hilo_a_tutelas(self, ids):
        return self._escribir("move_email", ["TUTELAS", f"{len(ids)} correo(s)"], len(ids))

    def mover_hilo_a_bandeja_entrada(self, ids):
        return self._escribir("move_email", ["Bandeja de entrada", f"{len(ids)} correo(s)"], len(ids))

    def __getattr__(self, nombre: str):
        def _anotar(*a, **k):
            return self._escribir(nombre, [str(x)[:120] for x in a][:3], True)
        return _anotar


def _redis_en_memoria():
    from Tutelas.services.redis_service import RedisService

    class RedisEnMemoria(RedisService):
        def __init__(self) -> None:
            self.reclamados: set = set()

        def reclamar(self, clave):
            nuevo = clave not in self.reclamados
            self.reclamados.add(clave)
            return clave, nuevo

        def liberar(self, h) -> None:
            self.reclamados.discard(h)

        def marcar_procesado(self, h) -> None:
            pass

        def close(self) -> None:
            pass

    return RedisEnMemoria()


def _sgdea_de_prueba():
    from Tutelas.services.sgdea_service import SGDEAService

    class SgdeaDePrueba(SGDEAService):
        def __init__(self) -> None:
            pass

        def radicar_tutela(self, correo, juzgado, radicado_23, adjuntos, max_reintentos: int = 3):
            carga = {"radicado_23": radicado_23, "juzgado": getattr(juzgado, "nombre", ""), "codigo_juzgado": getattr(juzgado, "codigo", ""),
                     "id_juzgado": getattr(juzgado, "id_juzgado", None), "asunto": correo.subject, "remitente": correo.remitente_email,
                     "documento_afectado": getattr(correo.datos_extraidos, "numero_documento", None) if getattr(correo, "datos_extraidos", None) else None,
                     "archivos": [n for n, _, _ in adjuntos]}
            _estado.get().radicacion = carga
            evento("escritura", sistema="sgdea", operacion="radicar", n_adjuntos=len(adjuntos), payload=carga)
            return True, {"radicado": f"PRUEBA-{radicado_23[-8:] if radicado_23 else 'TUTELA'}"}, None, False

        def obtener_info_radicado(self, numero):
            return {"idTutela": "prueba", "idRadicado": "prueba"}

        def crear_trazabilidad(self, *a, **k) -> bool:
            evento("escritura", sistema="sgdea", operacion="trazabilidad", detalle=[str(x) for x in a])
            return True

        def close(self) -> None:
            pass

    return SgdeaDePrueba()


def _juzgados_del_csv():
    from Tutelas.models.correo import Juzgado
    from Tutelas.services.juzgados_service import JuzgadosService

    filas = list(csv.DictReader(open(MOTOR / "juzgados_actualizada.csv", encoding="utf-8-sig")))

    def a_juzgado(f: dict) -> Juzgado:
        return Juzgado(id_juzgado=int(f.get("idJuzgado") or 0) or None, nombre=f.get("nombre", ""), direccion=f.get("direccion", ""),
                       pais=f.get("pais") or "Colombia", departamento=f.get("departamento", ""), municipio=f.get("municipio", ""),
                       codigo=f.get("codigo", ""), telefono=f.get("telefono", ""), celular=f.get("celular", ""), correo=f.get("correo", ""))

    class JuzgadosDelCsv(JuzgadosService):
        """La misma búsqueda que la consulta SQL, sobre la tabla del repositorio."""

        def __init__(self) -> None:
            pass

        def buscar_por_codigo(self, codigo):
            digitos = "".join(c for c in str(codigo or "") if c.isdigit())
            f = next((f for f in filas if digitos and "".join(c for c in f.get("codigo", "") if c.isdigit()) == digitos), None)
            evento("proveedor", proveedor="juzgados", operacion="buscar_por_codigo", ok=True, encontrado=bool(f), documento=digitos, nombre=(f or {}).get("nombre", ""), ms=0)
            return a_juzgado(f) if f else None

        def buscar_por_nombre(self, nombre, ciudad=None):
            if not nombre:
                return None
            f = next((f for f in filas if nombre.upper() in f.get("nombre", "").upper() and (not ciudad or ciudad.upper() in f.get("municipio", "").upper())), None)
            evento("proveedor", proveedor="juzgados", operacion="buscar_por_nombre", ok=True, encontrado=bool(f), documento=nombre, nombre=(f or {}).get("nombre", ""), ms=0)
            return a_juzgado(f) if f else None

        def close(self) -> None:
            pass

    return JuzgadosDelCsv()


def _ia_observada():
    from Tutelas.services.ai_extractor import AIExtractor

    ia = AIExtractor()
    for nombre in ("extraer_todo", "extraer_radicado_de_pdf", "extraer_documento_afectado", "extraer_radicado_y_juzgado"):
        original = getattr(ia, nombre, None)
        if original is None:
            continue

        def envolver(f, n):
            def envoltorio(*a, **k):
                evento("modelo_inicio", vuelta=1, quien=n, modelo=getattr(ia, "model", "gemini"), sistema="", ultimo=str(a[0])[:600] if a and isinstance(a[0], str) else f"{n}: documento")
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


# ------------------------------------------------------------------- un correo

def _correo(caso: str):
    from Tutelas.models.correo import Correo

    for base in (VIVO, BANDEJA):
        if (base / caso / "mensaje.json").exists():
            carpeta = base / caso
            break
    else:
        raise FileNotFoundError(f"no existe el caso {caso}")
    msg = json.loads((carpeta / "mensaje.json").read_text(encoding="utf-8"))
    de = (msg.get("from") or {}).get("emailAddress") or {}
    cuerpo = (msg.get("body") or {}).get("content", "")
    texto = re.sub(r"\s+\n", "\n", re.sub(r"<[^>]+>", " ", re.sub(r"<(style|script)[\s\S]*?</\1>", " ", cuerpo, flags=re.I)))
    fecha = msg.get("receivedDateTime") or datetime.now(timezone.utc).isoformat()
    correo = Correo(id=msg.get("id") or caso, subject=msg.get("subject") or "", body_content=cuerpo, body_text=texto,
                    remitente_email=(de.get("address") or "").lower(), remitente_nombre=de.get("name") or "",
                    fecha_recibido=datetime.fromisoformat(fecha.replace("Z", "+00:00")), conversation_id=msg.get("conversationId"))
    return carpeta, msg, correo


_servicios: Dict[str, Any] = {}


def procesar(orden: dict) -> None:
    from Tutelas.main import OrquestadorRadicacion
    from Tutelas.services.sharepoint_service import SharePointService

    caso = orden["caso"]
    _estado.set(Estado())
    t = time.time()
    try:
        carpeta, msg, correo = _correo(caso)
    except Exception as e:
        evento("resultado", caso=caso, accion="error", motivo=f"{type(e).__name__}: {e}"[:400], segundos=0)
        return
    if "ia" not in _servicios:
        _servicios["ia"] = _ia_observada()
        _servicios["juzgados"] = _juzgados_del_csv()
    o = OrquestadorRadicacion.__new__(OrquestadorRadicacion)
    o.outlook = OutlookDeDisco(carpeta, msg)
    o.redis = _redis_en_memoria()
    o.ai = _servicios["ia"]
    o.juzgados = _servicios["juzgados"]
    o.sgdea = _sgdea_de_prueba()
    o.sharepoint = SharePointService()
    evento("inicio", caso=caso, asunto=correo.subject, modelo="tutelas", flujo="tutelas")
    try:
        ok = o.procesar_hilo([correo])
        _cerrar_nodo()
        e = _estado.get()
        d = getattr(correo, "datos_extraidos", None)
        j = getattr(correo, "juzgado", None)
        campos = {
            "radicado_23": getattr(d, "radicado_23", None), "codigo_juzgado": getattr(d, "codigo_juzgado", None),
            "juzgado_en_el_texto": getattr(d, "juzgado_nombre_ocr", None), "ciudad": getattr(d, "ciudad", None),
            "documento_afectado": getattr(d, "numero_documento", None), "tipo_documento": getattr(d, "tipo_documento", None),
            "juzgado": getattr(j, "nombre", None), "municipio_juzgado": getattr(j, "municipio", None),
        }
        hallazgos = {k: {"valor": v or "", "metodo": "tutelas", "confianza": 1 if v else 0, "evidencia": ""} for k, v in campos.items()}
        from Tutelas import operaciones as op
        evento("resultado", caso=caso, accion="radicado" if ok else "manual",
               motivo="" if ok else e.etiqueta, explicacion="" if ok else (op.motivo(e.etiqueta) if e.etiqueta else ""),
               radicado=(e.radicacion or {}).get("radicado_23", "") and "PRUEBA" if ok else "",
               hallazgos=hallazgos, atribucion={k: "tutelas" for k, v in campos.items() if v},
               sin_resolver=[k for k, v in campos.items() if not v], solicitud=e.radicacion,
               avisos=[], escrituras_buzon=e.escrituras, segundos=round(time.time() - t, 1))
    except Exception as ex:
        _cerrar_nodo(fallo=f"{type(ex).__name__}: {ex}")
        evento("resultado", caso=caso, accion="error", motivo=f"{type(ex).__name__}: {ex}"[:500], traza=traceback.format_exc()[-2500:], segundos=round(time.time() - t, 1))


async def principal() -> None:
    sys.stdout = _Consola()
    try:
        _estado.set(Estado())
        instalar_operaciones()
        evento("listo", pid=os.getpid(), motor=str(MOTOR))
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
            for item in items:
                _corrida.set(item["corrida"])
                await asyncio.to_thread(contextvars.copy_context().run, procesar, {**orden, **item})
            evento("libre")
            _corrida.set(None)


if __name__ == "__main__":
    asyncio.run(principal())
