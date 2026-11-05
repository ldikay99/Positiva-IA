"""
Trabajador del motor de radicación.

Corre el pipeline de producción (`motor/`) sobre un correo de la bandeja local
y anuncia cada paso como evento. No modifica el motor: lo observa por los
ganchos que ya tiene (cronómetro de etapas, `emitir`, el modelo, `invocar` de
las herramientas y el grabador de MODO_TEST).

Nunca escribe en un sistema real: MODO_TEST corta el envío a SGDEA, el buzón
es de disco y las credenciales de Graph, SGDEA, Redis y Supabase son falsas.
Sí consulta Vertex y el registro de personas, que son lecturas.

Protocolo: una orden JSON por línea en stdin; eventos por stdout, cada uno en
una línea que empieza con MARCA. Lo demás que imprima el motor sale como
evento `consola`.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import importlib
import json
import os
import re
import inspect
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict

MARCA = "\x1eEVT "
RAIZ = Path(__file__).resolve().parent.parent
MOTOR = Path(os.environ.get("RUTA_MOTOR", RAIZ / "motor"))
BANDEJA = Path(os.environ.get("RUTA_BANDEJA", RAIZ / "datos" / "bandeja"))
TRABAJO = Path(os.environ.get("RUTA_TRABAJO", RAIZ / "datos" / "trabajo"))

# Antes de importar el motor: su configuración se lee al importarse.
os.environ["MODO_TEST"] = "true"
os.environ["DEDUP_EN_PRUEBAS"] = "false"
for _var in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID",
             "OUTLOOK_REFRESH_TOKEN", "OUTLOOK_FOLDER_ID", "REDIS_PASSWORD",
             "SUPABASE_SERVICE_ROLE_KEY", "SGDEA_CLIENT_SECRET", "SGDEA_PASSWORD"):
    os.environ[_var] = "x"
os.environ["SUPABASE_URL"] = "http://127.0.0.1:9"
os.environ.setdefault("TEST_OUTPUT_DIR", str(TRABAJO / "grabador"))
os.environ.setdefault("RUTA_LOGS", str(TRABAJO / "logs"))
os.chdir(MOTOR)  # el motor no carga ningún .env: no hay uno en su carpeta
sys.path.insert(0, str(MOTOR))

_salida = sys.stdout
_actual: Dict[str, Any] = {"corrida": None}
_cerrojo = threading.Lock()  # el modelo corre en un hilo aparte


def evento(tipo: str, **datos: Any) -> None:
    linea = json.dumps({"tipo": tipo, "corrida": _actual["corrida"], "t": time.time(), **datos},
                       ensure_ascii=False, default=str)
    with _cerrojo:
        _salida.write(MARCA + linea + "\n")
        _salida.flush()


class _Consola:
    """Lo que el motor imprime llega a la web como evento, línea por línea."""

    def __init__(self) -> None:
        self._pendiente = ""

    def write(self, texto: str) -> int:
        self._pendiente += texto
        while "\n" in self._pendiente:
            linea, self._pendiente = self._pendiente.split("\n", 1)
            if linea.strip():
                evento("consola", linea=linea[:4000])
        return len(texto)

    def flush(self) -> None:
        pass


# ---------------------------------------------------------------- buzón local

class BuzonLocal:
    """Sirve el correo y sus adjuntos desde disco. Toda escritura queda solo anotada."""

    def __init__(self, carpeta: Path, mensaje: dict) -> None:
        self.carpeta = carpeta
        self.mensaje = mensaje
        self.escrituras: list = []

    async def get_attachments(self, _id):
        return [{"id": a["archivo"], "name": a["name"], "size": a.get("size", 0),
                 "isInline": a.get("isInline", False), "contentType": a.get("contentType")}
                for a in self.mensaje.get("_adjuntos", []) if a.get("archivo")]

    async def download_attachment(self, _id, adjunto):
        return base64.b64encode((self.carpeta / "adj" / adjunto).read_bytes()).decode()

    async def get_email_by_id(self, _id):
        return None

    def __getattr__(self, nombre):
        if nombre.startswith("ultimo"):
            return ""

        async def _anotar(*a, **k):
            self.escrituras.append(nombre)
            evento("escritura", sistema="buzon", operacion=nombre,
                   detalle=[str(x)[:300] for x in a][:4])
            return None
        return _anotar


class RegistroObservado:
    """El registro de personas real (solo consulta), con cada consulta anunciada."""

    def __init__(self, real) -> None:
        self.real = real

    def consultar(self, documento, tipo=None):
        t = time.time()
        try:
            datos = self.real.consultar(documento, tipo)
        except Exception as e:
            evento("proveedor", proveedor="registro_personas", operacion="consultar",
                   ms=int((time.time() - t) * 1000), ok=False, error=str(e)[:300])
            raise
        evento("proveedor", proveedor="registro_personas", operacion="consultar",
               ms=int((time.time() - t) * 1000), ok=True, documento=str(documento),
               encontrado=bool(datos), nombre=(datos or {}).get("nombre", "") if datos else "")
        return datos

    def __getattr__(self, nombre):
        return getattr(self.real, nombre)


class ModeloObservado:
    """El modelo real; cada vuelta anuncia su pedido, su respuesta y sus tokens."""

    def __init__(self, real) -> None:
        self.real = real
        self.nombre = real.nombre
        self.vuelta = 0

    def generar(self, sistema, mensajes, herramientas):
        from pipeline import revisor

        self.vuelta += 1
        quien = "revisor" if sistema == revisor.SISTEMA else "agente"
        ultimo = mensajes[-1] if mensajes else {}
        evento("modelo_inicio", vuelta=self.vuelta, quien=quien, modelo=self.nombre,
               mensajes=len(mensajes),
               ultimo=str(ultimo.get("contenido", ultimo.get("content", "")))[:1500],
               sistema=sistema[:6000] if self.vuelta == 1 or quien == "revisor" else "")
        t = time.time()
        try:
            turno = self.real.generar(sistema, mensajes, herramientas)
        except Exception as e:
            evento("modelo_fin", vuelta=self.vuelta, quien=quien, ms=int((time.time() - t) * 1000),
                   ok=False, error=f"{type(e).__name__}: {e}"[:500])
            raise
        evento("modelo_fin", vuelta=self.vuelta, quien=quien, ms=int((time.time() - t) * 1000), ok=True,
               tokens_entrada=turno.tokens_entrada, tokens_salida=turno.tokens_salida,
               texto=(turno.texto or "")[:3000],
               llamadas=[{"herramienta": l.herramienta, "argumentos": l.argumentos}
                         for l in turno.llamadas])
        return turno

    def __getattr__(self, nombre):
        return getattr(self.real, nombre)


# -------------------------------------------------------------------- ganchos

def _reemplazar_en_modulos(original, nuevo) -> None:
    """Cambia una función importada por nombre en todos los módulos del motor."""
    for mod in list(sys.modules.values()):
        archivo = getattr(mod, "__file__", "") or ""
        if not archivo.startswith(str(MOTOR)):
            continue
        for nombre, valor in list(vars(mod).items()):
            if valor is original:
                setattr(mod, nombre, nuevo)


def instalar_ganchos() -> None:
    from config import settings

    if not settings.MODO_TEST:
        raise RuntimeError("el motor no quedó en MODO_TEST: el visor no arranca")

    from pipeline import agente, orquestador, radicador, revisor, salida  # noqa: F401  cargados antes de reemplazar
    from servidor_mcp import herramientas
    from logging_system import test_recorder

    crono = orquestador._Cronometro
    entrar, salir = crono.__enter__, crono.__exit__

    def _entrar(self):
        evento("etapa_inicio", etapa=self.etapa)
        return entrar(self)

    def _salir(self, tipo, valor, traza):
        r = salir(self, tipo, valor, traza)
        evento("etapa_fin", etapa=self.etapa, ms=int((time.time() - self.inicio) * 1000),
               fallo=f"{tipo.__name__}: {valor}"[:500] if tipo else "")
        return r

    crono.__enter__, crono.__exit__ = _entrar, _salir

    emitir_original = salida.emitir

    def _emitir(mensaje, severidad=salida.INFO, solo_json=(), en_su_linea=(), **campos):
        evento("log", mensaje=mensaje, severidad=severidad, campos=campos)
        return emitir_original(mensaje, severidad, solo_json, en_su_linea, **campos)

    _reemplazar_en_modulos(emitir_original, _emitir)

    invocar_original = herramientas.invocar

    def _invocar(ctx, nombre, argumentos=None):
        t = time.time()
        r = invocar_original(ctx, nombre, argumentos)
        d = r.a_dict()
        evento("herramienta", herramienta=nombre, argumentos=argumentos or {},
               ms=int((time.time() - t) * 1000), ok=r.ok,
               resultado=json.dumps(d.get("datos", d.get("error")), ensure_ascii=False,
                                    default=str)[:2500])
        return r

    _reemplazar_en_modulos(invocar_original, _invocar)

    grabador = test_recorder.TestRecorder
    rad_original, reg_original = grabador.registrar_radicacion, grabador.registrar

    def _radicacion(self, payload, n_adjuntos=0):
        evento("escritura", sistema="sgdea", operacion="radicar", n_adjuntos=n_adjuntos,
               payload=payload.to_dict() if hasattr(payload, "to_dict") else str(payload))
        return rad_original(self, payload, n_adjuntos)

    def _registrar(self, accion, destino="", detalle=None):
        evento("escritura", sistema="sgdea" if destino == "SGDEA" or "pqrd/" in destino else "otro",
               operacion=accion, destino=destino, detalle=detalle)
        return reg_original(self, accion, destino, detalle)

    grabador.registrar_radicacion, grabador.registrar = _radicacion, _registrar


# -------------------------------------------------------------- configuración

_originales: Dict[str, Any] = {}
_entorno_previo: Dict[str, Any] = {}
_defaults_previos: list = []

# Lo que haría que el visor escriba en un sistema real o cambie de credenciales
# no se ajusta nunca, venga de donde venga la orden.
_PROHIBIDOS = ("config.settings", "correr.", "env.")
_ENTORNO_PERMITIDO = {"MAX_VUELTAS_AGENTE"}

# Constantes que el motor fijó como valor por defecto de una función al
# importarse: cambiar el atributo no basta, hay que cambiar ese valor por defecto.
_POR_DEFECTO = {
    "pipeline.compuerta.CONFIANZA_PARA_RADICAR": [("pipeline.compuerta", "confianza_exigida", "base"),
                                                 ("pipeline.compuerta", "validar", "confianza_minima"),
                                                 ("pipeline.agente", "resolver", "confianza_minima")],
    "pipeline.extraccion.UMBRAL_CONFIANZA": [("pipeline.extraccion", "extraer_todo", "umbral")],
    "pipeline.revisor.MAX_VUELTAS_DEL_REVISOR": [("pipeline.revisor", "revisar", "max_vueltas")],
    "pipeline.agente.MAX_ITERACIONES": [("pipeline.agente", "resolver", "max_iteraciones")],
}


def _cambiar_por_defecto(modulo: str, funcion: str, parametro: str, valor: Any) -> None:
    f = getattr(importlib.import_module(modulo), funcion)
    if f.__kwdefaults__ and parametro in f.__kwdefaults__:
        _defaults_previos.append((f, "kw", dict(f.__kwdefaults__)))
        f.__kwdefaults__ = {**f.__kwdefaults__, parametro: valor}
        return
    posicionales = [p for p in inspect.signature(f).parameters.values()
                    if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    con_defecto = [p.name for p in posicionales if p.default is not p.empty]
    nuevos = list(f.__defaults__)
    nuevos[con_defecto.index(parametro)] = valor
    _defaults_previos.append((f, "pos", f.__defaults__))
    f.__defaults__ = tuple(nuevos)


# Lo que el modelo ve de cada herramienta. El orquestador guardó la misma lista
# al importarse, así que se cambia en su sitio y solo las descripciones: los
# nombres los usa el código para despachar.
_ESQUEMAS = "pipeline.agente.ESQUEMAS_HERRAMIENTAS"
_esquemas_previos: list = []


def _cambiar_esquemas(nuevos: Any) -> None:
    from pipeline import agente

    lista = agente.ESQUEMAS_HERRAMIENTAS
    por_nombre = {e.get("name"): e for e in nuevos if isinstance(e, dict)}
    if not _esquemas_previos:
        _esquemas_previos.extend(copy.deepcopy(lista))
    for esquema in lista:
        nuevo = por_nombre.get(esquema["name"])
        if not nuevo:
            continue
        if isinstance(nuevo.get("description"), str):
            esquema["description"] = nuevo["description"]
        props_nuevas = ((nuevo.get("parameters") or {}).get("properties") or {})
        for nombre, prop in ((esquema.get("parameters") or {}).get("properties") or {}).items():
            descripcion = (props_nuevas.get(nombre) or {}).get("description")
            if isinstance(descripcion, str):
                prop["description"] = descripcion


def _restaurar_esquemas() -> None:
    from pipeline import agente

    if not _esquemas_previos:
        return
    for esquema, previo in zip(agente.ESQUEMAS_HERRAMIENTAS, _esquemas_previos):
        esquema.clear()
        esquema.update(copy.deepcopy(previo))
    _esquemas_previos.clear()


def _coaccionar(original: Any, valor: Any) -> Any:
    """Devuelve el valor con la forma de Python que tenía el original (JSON no la conserva)."""
    if isinstance(original, re.Pattern):
        patron = valor.get("regex") if isinstance(valor, dict) else valor
        return re.compile(str(patron), original.flags)
    if isinstance(original, tuple) and isinstance(valor, list):
        return tuple(_coaccionar(original[0], v) if original else v for v in valor)
    if isinstance(original, (set, frozenset)) and isinstance(valor, list):
        return type(original)(valor)
    if isinstance(original, dict) and isinstance(valor, dict) and original:
        k0, v0 = next(iter(original.items()))
        tipo_clave = type(k0)
        return {(tipo_clave(k) if tipo_clave in (int, float) else k): _coaccionar(v0, v)
                for k, v in valor.items()}
    if isinstance(original, float) and isinstance(valor, int):
        return float(valor)
    return valor


def aplicar_configuracion(parametros: Dict[str, Any], entorno: Dict[str, str]) -> list:
    """Sobrescribe constantes de módulo para esta corrida. Devuelve las que no se aplicaron."""
    fallidas = []
    for clave, valor in parametros.items():
        modulo, _, nombre = clave.rpartition(".")
        if clave.startswith(_PROHIBIDOS) or "MODO_TEST" in clave or "DEDUP_EN_PRUEBAS" in clave:
            fallidas.append({"clave": clave, "error": "no se ajusta desde el visor"})
            continue
        if clave == _ESQUEMAS:
            try:
                _cambiar_esquemas(valor)
            except Exception as e:
                fallidas.append({"clave": clave, "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        try:
            mod = importlib.import_module(modulo)
            if clave not in _originales:
                _originales[clave] = copy.deepcopy(getattr(mod, nombre))
            valor = _coaccionar(_originales[clave], valor)
            setattr(mod, nombre, valor)
            if clave == "pipeline.compuerta.CONFIANZA_PARA_RADICAR":
                agente = importlib.import_module("pipeline.agente")
                _originales.setdefault("pipeline.agente.CONFIANZA_MINIMA", agente.CONFIANZA_MINIMA)
                agente.CONFIANZA_MINIMA = valor
            for destino in _POR_DEFECTO.get(clave, []):
                _cambiar_por_defecto(*destino, valor)
        except Exception as e:
            fallidas.append({"clave": clave, "error": f"{type(e).__name__}: {e}"[:200]})
    for var, valor in entorno.items():
        if var not in _ENTORNO_PERMITIDO:
            fallidas.append({"clave": var, "error": "variable no permitida en el visor"})
            continue
        _entorno_previo.setdefault(var, os.environ.get(var))
        if valor in (None, ""):
            os.environ.pop(var, None)
        else:
            os.environ[var] = str(valor)
    os.environ["MODO_TEST"] = "true"
    os.environ["DEDUP_EN_PRUEBAS"] = "false"
    return fallidas


def restaurar_configuracion() -> None:
    _restaurar_esquemas()
    for f, tipo, previos in reversed(_defaults_previos):
        if tipo == "kw":
            f.__kwdefaults__ = previos
        else:
            f.__defaults__ = previos
    _defaults_previos.clear()
    for clave, valor in _originales.items():
        modulo, _, nombre = clave.rpartition(".")
        setattr(importlib.import_module(modulo), nombre, copy.deepcopy(valor))
    for var, valor in _entorno_previo.items():
        if valor is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = valor
    _entorno_previo.clear()


# --------------------------------------------------------------------- correr

_modelos: Dict[str, Any] = {}
_registro: Any = None
_ia: Dict[str, Any] = {"temperatura": None}


def modelo_vertex(nombre: str, ubicacion: str):
    clave = f"{nombre}@{ubicacion}"
    if clave not in _modelos:
        import google.auth
        from config import settings
        from pipeline.modelo import ModeloVertex

        cred, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        m = ModeloVertex(proyecto=os.environ.get("GCP_PROJECT", settings.GCP_PROJECT),
                         ubicacion=ubicacion, nombre=nombre, credenciales=cred,
                         endpoint_global=True)
        # La temperatura está escrita dentro de `generar`: se ajusta en la
        # configuración que llega al cliente, justo antes de cada llamada.
        modelos_api = m._obtener_cliente().models
        generar_original = modelos_api.generate_content

        def _generar(*a, **k):
            config = k.get("config")
            if _ia["temperatura"] is not None and config is not None:
                config.temperature = _ia["temperatura"]
            return generar_original(*a, **k)

        modelos_api.generate_content = _generar
        _modelos[clave] = m
    return _modelos[clave]


async def procesar(orden: Dict[str, Any]) -> None:
    global _registro
    from models.email import EmailMessage
    from pipeline.atribucion import Origen
    from pipeline.orquestador import Orquestador
    from pipeline.personas import ConsultaDePersonas
    from pipeline.radicador import Radicador
    from services.radicacion import RadicacionService
    from config import settings

    caso = orden["caso"]
    carpeta = BANDEJA / caso
    crudo = json.loads((carpeta / "mensaje.json").read_text(encoding="utf-8"))
    limpio = {k: v for k, v in crudo.items() if not k.startswith("_")}
    limpio["categories"] = []
    email = EmailMessage.from_graph_api(limpio)

    ia = orden.get("ia") or {}
    _ia["temperatura"] = ia.get("temperatura")
    modelo =ModeloObservado(modelo_vertex(ia.get("modelo", "gemini-2.5-flash"),
                                           ia.get("ubicacion", "global")))
    if _registro is None:
        _registro = ConsultaDePersonas(url_base=settings.MS_SGDEA_BETA_URL)
    buzon = BuzonLocal(carpeta, crudo)
    servicio = RadicacionService(timeout=45)
    orq = Orquestador(modelo=modelo, outlook=buzon, radicador=Radicador(servicio=servicio),
                      base_workspace=str(TRABAJO / "ws" / _actual["corrida"]),
                      sgdea=RegistroObservado(_registro))

    evento("inicio", caso=caso, asunto=crudo.get("subject", ""), modelo=modelo.nombre)
    t = time.time()
    try:
        r = await asyncio.wait_for(orq.procesar(email), orden.get("tope_s", 600))
        evento("resultado", caso=caso, accion=r.accion, motivo=r.motivo_manual,
               radicado=r.radicado, avisos=r.avisos, explicacion=r.explicacion_del_agente,
               vueltas=r.turnos_del_agente, tokens_entrada=r.tokens_entrada,
               tokens_salida=r.tokens_salida, etapas_ms=r.etapas_ms, duracion_ms=r.duracion_ms,
               area_destino=r.area_destino, razon_del_reenvio=r.razon_del_reenvio,
               razon_del_duplicado=r.razon_del_duplicado,
               hallazgos={c: {"valor": h.valor, "metodo": h.metodo, "confianza": h.confianza,
                              "evidencia": str(h.evidencia)[:400]}
                          for c, h in r.hallazgos.items()},
               sin_resolver=sorted(c for c, o in r.atribucion.items() if o is Origen.NO_RESUELTO),
               atribucion={c: getattr(o, "value", str(o)) for c, o in r.atribucion.items()},
               escrituras_buzon=buzon.escrituras, segundos=round(time.time() - t, 1))
    except Exception as e:
        evento("resultado", caso=caso, accion="error", motivo=f"{type(e).__name__}: {e}"[:500],
               traza=traceback.format_exc()[-3000:], segundos=round(time.time() - t, 1))
    finally:
        await servicio.close()


async def principal() -> None:
    sys.stdout = _Consola()
    try:
        instalar_ganchos()
        evento("listo", pid=os.getpid(), motor=str(MOTOR))
    except Exception as e:
        evento("fallo_inicio", error=f"{type(e).__name__}: {e}", traza=traceback.format_exc()[-3000:])
        return
    while True:
        linea = await asyncio.to_thread(sys.stdin.readline)
        if not linea:
            break
        try:
            orden = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if orden.get("cmd") == "procesar":
            _actual["corrida"] = orden["corrida"]
            fallidas = aplicar_configuracion(orden.get("parametros", {}), orden.get("entorno", {}))
            if fallidas:
                evento("configuracion", fallidas=fallidas)
            try:
                await procesar(orden)
            finally:
                restaurar_configuracion()
                evento("libre")
                _actual["corrida"] = None
        elif orden.get("cmd") == "ping":
            evento("pong")


if __name__ == "__main__":
    asyncio.run(principal())
