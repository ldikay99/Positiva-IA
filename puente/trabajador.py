"""
Trabajador del motor de radicación.

Corre el pipeline de producción (`motor/`) sobre un correo de la bandeja local
y anuncia cada paso como evento. No modifica el motor: lo observa por los
ganchos que ya tiene (cronómetro de etapas, `emitir`, el modelo, `invocar` de
las herramientas y el grabador de MODO_TEST).

Con la compuerta de radicación APAGADA (siempre, salvo que una persona la encienda
y el despliegue tenga PERMITIR_RADICACION_REAL=si) nunca escribe en un sistema real:
MODO_TEST corta el envío a SGDEA y las trazas, el buzón real va con un cliente que
solo hace GET y lo que el job escribiría queda como escritura retenida. Con las
credenciales reales en el entorno del flujo LEE de verdad: Graph (buzón, hilo,
radicados) y Supabase (con `solo_lectura.ConsultaSupabase`, solo GET). Sin ellas,
los dobles de siempre, y lo dice en el evento «fuentes». Vertex y el registro de
personas son lecturas reales siempre.

Con la compuerta ENCENDIDA (RADICACION_REAL=si y PERMITIR_RADICACION_REAL=si) corre
como el job: MODO_TEST=false, OutlookService, RadicacionService y SupabaseService
reales, escrituras reales. Solo atiende correos traídos del buzón real.

Protocolo: una orden JSON por línea en stdin; eventos por stdout, cada uno en
una línea que empieza con MARCA. Lo demás que imprima el motor sale como
evento `consola`.
"""
from __future__ import annotations

import asyncio
import contextvars
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

import solo_lectura  # noqa: E402  (junto a este archivo)

# La compuerta se decide UNA vez, al arrancar: el servidor reemplaza al trabajador si cambia.
REAL = solo_lectura.radicacion_real()
_mascara = solo_lectura.Mascara().recordar()  # antes de tocar el entorno
_SECRETOS_DEL_JOB = ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID",
                     "OUTLOOK_REFRESH_TOKEN", "OUTLOOK_FOLDER_ID", "SUPABASE_URL",
                     "SUPABASE_SERVICE_ROLE_KEY", "SGDEA_CLIENT_SECRET", "SGDEA_PASSWORD")
# Lo que la configuración del motor exige al importarse y no vino en el entorno del flujo.
FALTANTES = solo_lectura.faltantes(_SECRETOS_DEL_JOB)

# Antes de importar el motor: su configuración se lee al importarse.
os.environ["MODO_TEST"] = "false" if REAL else "true"
os.environ["DEDUP_EN_PRUEBAS"] = "false"
for _var in _SECRETOS_DEL_JOB + ("REDIS_PASSWORD",):
    if not solo_lectura.definida(_var):  # lo real del entorno del flujo se conserva
        os.environ[_var] = "x"
if not solo_lectura.definida("SUPABASE_URL"):
    os.environ["SUPABASE_URL"] = "http://127.0.0.1:9"
os.environ.setdefault("TEST_OUTPUT_DIR", str(TRABAJO / "grabador"))
os.environ.setdefault("RUTA_LOGS", str(TRABAJO / "logs"))
os.chdir(MOTOR)  # el motor no carga ningún .env: no hay uno en su carpeta
sys.path.insert(0, str(MOTOR))

import visor_adjuntos as adjuntos  # noqa: E402  (junto a este archivo, no en el motor)
import buzon_vivo  # noqa: E402

VIVO = Path(os.environ.get("RUTA_VIVO", RAIZ / "datos" / "vivo"))
_salida = sys.stdout
_cerrojo = threading.Lock()  # el modelo corre en un hilo aparte


class _PorCorreo:
    """
    El estado del correo que se está procesando, uno por tarea.

    En un lote varios correos corren a la vez en este mismo proceso, como en el
    job: lo que se anuncia de uno no puede llevar la corrida de otro. Vive en
    un `ContextVar`: cada tarea de asyncio tiene el suyo y los hilos que abre
    `asyncio.to_thread` (el modelo, la conversión) lo heredan.
    """

    _var: contextvars.ContextVar = contextvars.ContextVar("correo_del_visor")

    def _d(self) -> Dict[str, Any]:
        d = self._var.get(None)
        if d is None:
            d = {"corrida": None}
            self._var.set(d)
        return d

    def nuevo(self, **valores: Any) -> None:
        self._var.set({"corrida": None, **valores})

    def __getitem__(self, clave: str) -> Any:
        return self._d()[clave]

    def __setitem__(self, clave: str, valor: Any) -> None:
        self._d()[clave] = valor

    def get(self, clave: str, defecto: Any = None) -> Any:
        return self._d().get(clave, defecto)

    def pop(self, clave: str, *defecto: Any) -> Any:
        return self._d().pop(clave, *defecto)


_actual = _PorCorreo()
_buzon_de_la_tarea: contextvars.ContextVar = contextvars.ContextVar("buzon_del_visor")


def evento(tipo: str, **datos: Any) -> None:
    linea = _mascara(json.dumps({"tipo": tipo, "corrida": _actual["corrida"], "t": time.time(), **datos},
                                ensure_ascii=False, default=str))
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

class BuzonDelLote:
    """
    El `outlook` del orquestador. Cada correo trae su propio buzón (el de disco o
    el real de solo lectura), y en un lote un mismo orquestador atiende varios a
    la vez: cada llamada va al buzón del correo de la tarea que la hace.
    """

    def __getattr__(self, nombre):
        return getattr(_buzon_de_la_tarea.get(), nombre)


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

    @property
    def vuelta(self) -> int:
        """Las vueltas son del correo, no del modelo: en un lote lo comparten varios."""
        return _actual.get("vuelta", 0)

    def transcribir_imagen(self, jpeg: bytes) -> str:
        """Lo que el OCR no leyó lo transcribe el modelo con visión: se anuncia con su hoja."""
        t = time.time()
        try:
            texto = self.real.transcribir_imagen(jpeg)
        except Exception as e:
            ms = int((time.time() - t) * 1000)
            donde = adjuntos.vision(ms, 0, False, f"{type(e).__name__}: {e}")
            evento("vision", ms=ms, ok=False, error=f"{type(e).__name__}: {e}"[:300], bytes=len(jpeg), **donde)
            raise
        ms = int((time.time() - t) * 1000)
        sin_texto = (texto or "").strip().upper() == "SIN_TEXTO"
        donde = adjuntos.vision(ms, len(texto or ""), not sin_texto)
        evento("vision", ms=ms, ok=True, sin_texto=sin_texto, caracteres=len(texto or ""), bytes=len(jpeg),
               muestra=(texto or "")[:600], **donde)
        return texto

    def generar(self, sistema, mensajes, herramientas):
        from pipeline import revisor, seguimiento

        _actual["vuelta"] = self.vuelta + 1
        quien = ("revisor" if sistema == revisor.SISTEMA
                 else "seguimiento" if sistema == seguimiento.SISTEMA else "agente")
        adjuntos.quien(quien)
        ultimo = mensajes[-1] if mensajes else {}
        evento("modelo_inicio", vuelta=self.vuelta, quien=quien, modelo=self.nombre,
               mensajes=len(mensajes),
               ultimo=str(ultimo.get("contenido", ultimo.get("content", "")))[:1500],
               sistema=sistema[:6000] if self.vuelta == 1 or quien != "agente" else "")
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

    if REAL:
        # Como el job: sin MODO_TEST y con todos sus secretos; si falta uno, no arranca.
        if settings.MODO_TEST:
            raise RuntimeError("la compuerta está encendida pero el motor quedó en MODO_TEST")
        if FALTANTES:
            raise RuntimeError("la compuerta está encendida pero faltan en el entorno del flujo: " + ", ".join(FALTANTES))
    elif not settings.MODO_TEST:
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
        adjuntos.herramienta(nombre)
        try:
            r = invocar_original(ctx, nombre, argumentos)
        finally:
            adjuntos.herramienta(None)
        d = r.a_dict()
        extra = {}
        if nombre == "reportar_hallazgo" and isinstance(argumentos, dict):
            extra["ubicacion"] = adjuntos.ubicar(argumentos.get("valor"), argumentos.get("evidencia"))
        evento("herramienta", herramienta=nombre, argumentos=argumentos or {},
               ms=int((time.time() - t) * 1000), ok=r.ok,
               resultado=json.dumps(d.get("datos", d.get("error")), ensure_ascii=False,
                                    default=str)[:2500], **extra)
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

    instalar_cache_conversion()
    adjuntos.instalar(evento)
    instalar_observadores()
    instalar_reglas()


def instalar_cache_conversion() -> None:
    """La conversión de cada adjunto pasa por la caché por huella (cache_conversion.py), por debajo de los
    observadores: el visor anuncia lo mismo. Un acierto descuenta del presupuesto del correo lo que costó la
    primera vez, así el orquestador decide igual que sin caché. Solo con la compuerta apagada (simulación):
    con la radicación real, el motor corre exactamente como el job."""
    if REAL:
        return
    import cache_conversion
    from pipeline import conversion

    try:
        from pipeline import cache_conversion as cache_del_motor
    except ImportError:
        cache_del_motor = None
    if cache_del_motor is not None:
        # El motor ya trae su caché por contenido (memoria y disco): se le da la
        # carpeta de la plataforma y solo se anuncian los aciertos. Envolverla
        # aquí también guardaría cada lectura dos veces.
        if not cache_conversion.activa():
            os.environ["CACHE_CONVERSION"] = "no"
        os.environ.setdefault("RUTA_CACHE_CONVERSION", str(cache_conversion.carpeta() / "pqrd"))

        def observar(nombre: str, como: str) -> None:
            evento("cache_conversion", archivo=nombre, acierto=como == "acierto")
            if como == "acierto":
                evento("consola", linea=f"[caché] {nombre}: ya se había leído (misma huella y opciones): "
                                        "no se repite la conversión ni el OCR")

        cache_del_motor.OBSERVADOR = observar
        return

    original = conversion.convertir

    def convertir(datos, nombre, claves_candidatas=None, _profundidad_sobre=0, presupuesto=None):
        if _profundidad_sobre:
            return original(datos, nombre, claves_candidatas, _profundidad_sobre, presupuesto)
        c, como = cache_conversion.convertir(conversion, original, datos, nombre, claves_candidatas, presupuesto=presupuesto)
        if como == "acierto":
            evento("consola", linea=f"[caché] {nombre}: ya se había leído (misma huella y opciones): no se repite la conversión ni el OCR")
        return c

    conversion.convertir = convertir
    _reemplazar_en_modulos(original, convertir)


# --------------------------------------------------------------- observadores
#
# Cada decisión de negocio del motor se anuncia como un evento `paso` (qué
# etapa, qué decidió y por qué) y cada búsqueda de un campo como un evento
# `busqueda` (qué buscó, si lo encontró, con qué confianza y dónde). Son
# envoltorios que llaman a la función original sin tocar sus argumentos.

def _corto(v: Any, n: int = 160) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _paso(nodo: str, titulo: str, tono: str = "info", detalle: str = "", **datos: Any) -> None:
    evento("paso", nodo=nodo, titulo=titulo, tono=tono, detalle=detalle[:1500], **datos)


def _envolver(objeto: Any, nombre: str, despues, asincrona: bool = False) -> None:
    """Cambia `objeto.nombre` (y sus copias importadas) por una versión que anuncia su resultado."""
    import functools

    original = getattr(objeto, nombre)

    if asincrona:
        @functools.wraps(original)
        async def envoltorio(*a, **k):
            r = await original(*a, **k)
            try:
                despues(r, a, k)
            except Exception as e:  # anunciar nunca rompe el motor
                evento("consola", linea=f"[visor] no se pudo anunciar {nombre}: {e}")
            return r
    else:
        @functools.wraps(original)
        def envoltorio(*a, **k):
            r = original(*a, **k)
            try:
                despues(r, a, k)
            except Exception as e:
                evento("consola", linea=f"[visor] no se pudo anunciar {nombre}: {e}")
            return r

    setattr(objeto, nombre, envoltorio)
    if inspect.ismodule(objeto):
        _reemplazar_en_modulos(original, envoltorio)


_FUNCION_DEL_CAMPO = {
    "extraer_nombre_afectado": "nombre del afectado",
    "extraer_documento": "documento del afectado",
    "extraer_tipo_documento": "tipo de documento",
    "extraer_telefono": "teléfono",
    "extraer_nombre_remitente": "nombre del remitente",
    "extraer_sexo": "sexo del afectado",
    "extraer_tipo_pqrd": "tipo de PQRD",
    "extraer_documento_del_remitente": "documento del remitente",
    "extraer_tipo_remitente": "tipo de remitente",
    "extraer_tipo_solicitud": "tipo de solicitud",
    "extraer_direccion": "dirección",
    "extraer_geografia": "departamento y municipio",
    "extraer_oficina": "oficina destino",
}


def _umbral_vigente() -> float:
    from pipeline import extraccion

    f = inspect.unwrap(extraccion.extraer_todo)
    return float(inspect.signature(f).parameters["umbral"].default)


def instalar_observadores() -> None:
    from pipeline import compuerta, extraccion, facturas, volumen, agente, conversion, enrutamiento
    from pipeline import personas, saneamiento, duplicados
    from utils import generic_filter

    # --- búsquedas de la extracción determinista, campo por campo
    def anunciar_busqueda(funcion: str):
        def despues(r, a, k):
            umbral = _actual.get("umbral") or UMBRAL_POR_DEFECTO
            pares = [(h, None) for h in r] if isinstance(r, list) else [r]
            if not pares:
                evento("busqueda", funcion=funcion, que=_FUNCION_DEL_CAMPO.get(funcion, funcion),
                       campo=funcion.replace("extraer_", ""), encontrado=False,
                       motivo="SIN_CANDIDATO", detalle="no apareció en el texto")
            for h, nr in pares:
                if h is not None:
                    evento("busqueda", funcion=funcion, que=_FUNCION_DEL_CAMPO.get(funcion, funcion),
                           campo=h.campo, encontrado=True, valor=h.valor,
                           confianza=round(h.confianza, 3), metodo=h.metodo,
                           evidencia=_corto(h.evidencia, 260), umbral=umbral,
                           aceptado=h.confianza >= umbral, a_confirmar=h.a_confirmar,
                           ubicacion=adjuntos.ubicar(h.valor, h.evidencia))
                elif nr is not None:
                    evento("busqueda", funcion=funcion, que=_FUNCION_DEL_CAMPO.get(funcion, funcion),
                           campo=nr.campo, encontrado=False, motivo=nr.motivo,
                           detalle=_corto(nr.detalle, 260))
        return despues

    global UMBRAL_POR_DEFECTO
    UMBRAL_POR_DEFECTO = _umbral_vigente()
    for funcion in _FUNCION_DEL_CAMPO:
        if hasattr(extraccion, funcion):
            _envolver(extraccion, funcion, anunciar_busqueda(funcion))

    def fin_extraccion(r, a, k):
        _paso("extraccion_determinista",
              f"El algoritmo resolvió {len(r.hallazgos)} campos; {len(r.no_resueltos)} pasan al agente IA",
              "ok" if not r.no_resueltos else "aviso",
              "\n".join(f"{c}: {nr.motivo} {nr.detalle}" for c, nr in r.no_resueltos.items()),
              resueltos=sorted(r.hallazgos), pendientes=sorted(r.no_resueltos))
    _envolver(extraccion, "extraer_todo", fin_extraccion)

    # --- deduplicación
    def dedup(nodo: str, capas: str):
        def despues(v, a, k):
            if v:
                _paso(nodo, f"Duplicado ({capas}): {v.motivo}", "aviso", f"radicado previo: {v.radicado}")
            else:
                _paso(nodo, f"No es duplicado ({capas})", "ok", v.motivo or "")
        return despues
    _envolver(duplicados.Detector, "comprobar", dedup("deduplicacion", "capas 1-4"), asincrona=True)
    _envolver(duplicados.Detector, "comprobar_contenido", dedup("deduplicacion_contenido", "capas 5-6"), asincrona=True)

    def en_la_corrida(r, a, k):
        v, fallo = r
        if v:
            _paso("deduplicacion", f"Este remitente ya radicó este caso en esta misma corrida ({v.radicado})",
                  "aviso", v.motivo, radicado=v.radicado, capa="corrida")
        elif fallo:
            _paso("deduplicacion", "No se pudo comparar con lo radicado en esta corrida", "aviso", fallo)
    _envolver(duplicados.Detector, "_radicado_en_esta_corrida", en_la_corrida, asincrona=True)

    # --- anexos: lo que llega en el caso de un radicado con documentos nuevos
    def anexos(v, a, k):
        email, radicado = a[1], a[2]
        if v.anexo_de:
            _paso("anexos", f"Trae {len(v.documentos_nuevos)} documento(s) que el radicado {radicado} no tiene: va a revisión manual",
                  "aviso", "\n".join(v.documentos_nuevos[:20]), radicado=radicado, documentos=v.documentos_nuevos[:20])
        elif "sin comparar" in (v.motivo or ""):
            _paso("anexos", f"No se pudieron comparar los documentos con {radicado}: queda como duplicado", "aviso", v.motivo,
                  radicado=radicado)
        elif getattr(email, "has_attachments", False):
            _paso("anexos", f"Sus documentos ya están en el radicado {radicado}", "ok", v.motivo, radicado=radicado)
        else:
            _paso("anexos", f"Sin adjuntos que comparar con el radicado {radicado}", "info", v.motivo, radicado=radicado)
    _envolver(duplicados.Detector, "_salvo_documentos_nuevos", anexos, asincrona=True)

    # --- seguimiento: ¿insiste en lo radicado o pide otra cosa?
    from pipeline import orquestador, seguimiento

    def clasificado(c, a, k):
        _actual["seguimiento_consultado"] = True
        if c.es_solicitud_nueva:
            _paso("seguimiento", "La IA ve una solicitud distinta de lo radicado", "aviso", f"«{c.cita}»",
                  veredicto=c.veredicto, cita=c.cita, tokens_entrada=c.tokens_entrada)
        else:
            _paso("seguimiento", "La IA lo ve como seguimiento de lo radicado" if not c.descartada
                  else f"Queda como seguimiento: {c.descartada}", "ok" if not c.descartada else "aviso",
                  c.descartada or "insiste, reitera o pregunta por lo mismo", veredicto=c.veredicto,
                  tokens_entrada=c.tokens_entrada)
    _envolver(seguimiento, "clasificar", clasificado)

    def pide_algo_nuevo(cita, a, k):
        repetido = a[2]
        if not (repetido.radicado and (repetido.motivo or "").startswith(orquestador.Orquestador._CAPAS_DEL_MISMO_CASO)):
            return
        if cita:
            _paso("seguimiento", f"Pide algo distinto del radicado {repetido.radicado}: va a revisión manual", "aviso",
                  f"«{cita}»", radicado=repetido.radicado, cita=cita)
        elif not _actual.get("seguimiento_consultado"):
            _paso("seguimiento", f"Seguimiento del radicado {repetido.radicado}, sin consultar a la IA", "info",
                  "no hace falta: reenvío exacto, otro remitente, un buzón de Positiva, sin texto propio "
                  "o el radicado no tiene texto con qué comparar", radicado=repetido.radicado)
        _actual.pop("seguimiento_consultado", None)
    _envolver(orquestador.Orquestador, "_pide_algo_nuevo", pide_algo_nuevo, asincrona=True)

    # --- el resto del hilo que sale con el correo
    import functools

    def cerrando(nombre: str, nodo: str) -> None:
        original = getattr(orquestador.Orquestador, nombre)

        @functools.wraps(original)
        async def envoltorio(*a, **k):
            # Antes de llamar: el resto del hilo se archiva dentro de esta llamada.
            _actual["cerrando"] = nodo
            return await original(*a, **k)
        setattr(orquestador.Orquestador, nombre, envoltorio)
    cerrando("_mover_a_exitosa", "cierre_outlook")
    cerrando("_archivar_duplicado", "duplicado")

    def resto_del_hilo(_r, a, k):
        avisos = a[2] if len(a) > 2 else k.get("avisos")
        lineas = [x for x in (avisos or []) if "correo(s)" in x and "del mismo hilo" in x]
        nodo = _actual.get("cerrando") or "duplicado"
        if lineas:
            _paso(nodo, "Qué pasa con el resto del hilo", "info", "\n".join(lineas[-3:]))
    _envolver(orquestador.Orquestador, "_archivar_el_resto_del_hilo", resto_del_hilo, asincrona=True)

    # --- enrutamiento, facturas, remitente
    def ruta(r, a, k):
        destino = getattr(r.destino, "value", str(r.destino))
        textos = {"radicar": "Se radica en Positiva", "medicina_laboral": "Se reenvía a Medicina Laboral",
                  "judicial": "Se reenvía a Notificaciones Judiciales", "no_se_radica": "Remitente excluido: no se radica"}
        _paso("enrutamiento", textos.get(destino, destino), "ok" if destino == "radicar" else "aviso",
              r.evidencia or "", destino=destino, correo_destino=r.correo_destino)
    _envolver(enrutamiento, "decidir", ruta)

    def factura(r, a, k):
        if r:
            _paso("facturas", "Es una factura electrónica: va a revisión manual", "aviso", r)
        else:
            _paso("facturas", "No es factura electrónica", "ok")
    _envolver(facturas, "es_factura", factura)

    def remitente(r, a, k):
        _paso("remitente_identificable",
              "Hay un remitente externo identificable" if r else "Ningún remitente externo en el hilo",
              "ok" if r else "aviso", str(a[1] if len(a) > 1 else ""))
    _envolver(generic_filter, "tiene_remitente_externo_identificable", remitente)

    def medir(v, a, k):
        topes = f"topes: {volumen.MAX_ADJUNTOS} adjuntos, {volumen.MAX_PAGINAS} páginas, {volumen.MAX_MEGABYTES} MB"
        _paso("volumen", f"{v.adjuntos} adjuntos útiles · {v.paginas} páginas · {v.megabytes:.1f} MB".replace(".", ","),
              "aviso" if v.excesos else "ok", "; ".join(v.excesos or []) or topes,
              adjuntos=v.adjuntos, paginas=v.paginas, megabytes=round(v.megabytes, 2),
              topes={"adjuntos": volumen.MAX_ADJUNTOS, "paginas": volumen.MAX_PAGINAS, "megabytes": volumen.MAX_MEGABYTES})
    _envolver(volumen, "medir", medir)

    # --- conversión y OCR, documento por documento
    def convertido(c, a, k):
        nombre = a[1] if len(a) > 1 else k.get("nombre", "")
        if c.error:
            _paso("conversion_a_markdown", f"{nombre}: no se pudo leer", "error", c.error)
            return
        partes = [c.tipo_real or "?", f"{len(c.markdown):,} caracteres".replace(",", ".")]
        if c.paginas:
            partes.append(f"{c.paginas} pág.")
        if c.ocr_aplicado:
            partes.append("con OCR")
        if c.paginas_ilegibles:
            partes.append(f"{c.paginas_ilegibles} pág. ilegibles")
        _paso("conversion_a_markdown", f"{nombre} → Markdown", "aviso" if c.paginas_ilegibles else "ok",
              " · ".join(partes) + (f"\n{c.aviso}" if c.aviso else ""), ocr=c.ocr_aplicado)
    _envolver(conversion, "convertir", convertido)

    def sospechas(r, a, k):
        if r:
            _paso("saneamiento", f"{len(r)} posibles intentos de inyección", "aviso",
                  "\n".join(f"línea {s.linea} · {s.categoria}: {_corto(s.fragmento, 90)}" for s in r[:8]))
        else:
            _paso("saneamiento", "Sin instrucciones sospechosas en el texto", "ok")
    _envolver(saneamiento, "detectar", sospechas)

    # --- combinación, registro y compuerta
    def combinado(r, a, k):
        _paso("combinacion", f"{len(r)} campos combinados (algoritmo + agente)", "ok",
              ", ".join(sorted(r)))
    _envolver(agente, "combinar", combinado)

    def registro(r, a, k):
        if r:
            _paso("registro_de_personas", f"El registro completó {len(r)} campos", "ok",
                  "\n".join(f"{c} = {_corto(getattr(h, 'valor', h), 80)}" for c, h in r.items()),
                  campos={c: _corto(getattr(h, "valor", h), 120) for c, h in r.items()})
        else:
            _paso("registro_de_personas", "El registro no aportó campos nuevos", "info")
    _envolver(personas, "rellenar_desde_el_registro", registro)

    instalar_ciclo_de_validacion()


def _valores(hallazgos: Any) -> Dict[str, Any]:
    return {c: getattr(h, "valor", h) for c, h in (hallazgos or {}).items()}


def instalar_ciclo_de_validacion() -> None:
    """
    La compuerta, la ronda de corrección y el revisor obligatorio.

    La compuerta rechaza un campo → se le devuelve a la IA con el motivo → la
    IA corrige → la compuerta vuelve a validar. Si la compuerta lo deja pasar,
    el revisor comprueba la identidad; lo que objeta vuelve por la misma ronda.
    """
    import functools
    from pipeline import orquestador

    O = orquestador.Orquestador
    corregir = O._corregir

    @functools.wraps(corregir)
    async def _corregir(self, ctx, determinista, hallazgos, a_corregir, *resto, **k):
        origen = "revisor" if any(str(m).startswith("REVISOR_") for m in (a_corregir or {}).values()) else "compuerta"
        antes = {c: getattr(hallazgos.get(c), "valor", None) for c in (a_corregir or {})}
        _actual["fase_validacion"] = f"tras corregir lo que objetó la {'revisión' if origen == 'revisor' else 'compuerta'}"
        evento("correccion", fase="inicio", origen=origen,
               campos={c: {"motivo": str(m)[:400], "antes": antes.get(c)} for c, m in (a_corregir or {}).items()})
        t = time.time()
        r = await corregir(self, ctx, determinista, hallazgos, a_corregir, *resto, **k)
        try:
            nuevos, dictamen = r
            despues = {c: getattr(nuevos.get(c), "valor", None) for c in (a_corregir or {})}
            evento("correccion", fase="fin", origen=origen, ms=int((time.time() - t) * 1000),
                   veredicto=getattr(dictamen.veredicto, "value", str(dictamen.veredicto)),
                   campos={c: {"antes": antes.get(c), "despues": despues.get(c),
                               "corregido": despues.get(c) not in (None, "") and str(despues.get(c)) != str(antes.get(c))}
                           for c in (a_corregir or {})})
        except Exception as e:
            evento("consola", linea=f"[visor] no se pudo anunciar la corrección: {e}")
        return r

    O._corregir = _corregir

    llamar = O._llamar_al_revisor

    @functools.wraps(llamar)
    def _llamar_al_revisor(self, ws, valores, hallazgos, fuentes, email, res):
        _actual["revisiones"] = _actual.get("revisiones", 0) + 1
        ronda = _actual["revisiones"]
        evento("revision", fase="inicio", ronda=ronda, valores=dict(valores or {}))
        t = time.time()
        r = llamar(self, ws, valores, hallazgos, fuentes, email, res)
        if r is None:
            evento("revision", fase="fin", ronda=ronda, ok=False, ms=int((time.time() - t) * 1000),
                   detalle="el revisor no se pudo ejecutar: la compuerta decide sola")
        else:
            evento("revision", fase="fin", ronda=ronda, ok=True, ms=int((time.time() - t) * 1000),
                   vueltas=r.vueltas, valores=dict(valores or {}),
                   veredictos={c: {"veredicto": x.veredicto, "valor": x.valor, "detalle": x.detalle}
                               for c, x in (r.reparos or {}).items()},
                   ilegibles=r.ilegibles[:5])
        return r

    O._llamar_al_revisor = _llamar_al_revisor

    reparos = orquestador._reparos_de_identidad

    @functools.wraps(reparos)
    def _reparos_de_identidad(revision, valores, texto="", avisos=None):
        previos = len(avisos) if avisos is not None else 0
        r = reparos(revision, valores, texto, avisos)
        try:
            nuevos_avisos = list(avisos[previos:]) if avisos is not None else []
            evento("revision", fase="decision", devueltos={c: str(m)[:300] for c, m in r.items()},
                   se_mantienen=nuevos_avisos[:10])
            _actual["fase_validacion"] = "tras la revisión" if not r else _actual.get("fase_validacion", "")
        except Exception:
            pass
        return r

    orquestador._reparos_de_identidad = _reparos_de_identidad


UMBRAL_POR_DEFECTO = 0.7


# ---------------------------------------------------- reglas personalizadas
#
# Reglas que se crean desde la web y corren dentro del motor en dos puntos:
#  - entrada: antes de abrir los adjuntos, sobre asunto, cuerpo, remitente o
#    nombres de adjuntos. Mandar a revisión manual usa el mismo camino que el
#    motor usa para las facturas, con el motivo que diga la regla.
#  - validacion: justo antes de la compuerta, sobre los campos ya resueltos.
#    Puede fijar un valor, avisar o rechazar (revisión manual).

_reglas: list = []
_motivo_facturas: Dict[str, str] = {}


def _cumple(regla: dict, texto: Any) -> tuple:
    op = regla.get("operador", "coincide")
    vacio = texto is None or str(texto).strip() == ""
    if op == "falta":
        return vacio, "el campo está vacío" if vacio else ""
    if op == "existe":
        return (not vacio), "el campo tiene valor" if not vacio else ""
    banderas = re.IGNORECASE if regla.get("ignorarMayusculas", True) else 0
    m = re.search(regla.get("patron") or "", "" if vacio else str(texto), banderas)
    if op == "no_coincide":
        return m is None, "no aparece el patrón"
    return m is not None, f"coincide «{_corto(m.group(0), 80)}»" if m else ""


def _anunciar_regla(regla: dict, cumple: bool, detalle: str) -> None:
    evento("regla", id=regla.get("id"), nombre=regla.get("nombre"), punto=regla.get("punto"),
           cumple=cumple, accion=regla.get("accion"), detalle=detalle, campo=regla.get("campo"),
           valor=regla.get("valor") if regla.get("accion") == "fijar" else None,
           motivo=regla.get("motivo") if cumple and regla.get("accion") == "manual" else "")


def instalar_reglas() -> None:
    import functools
    from pipeline import compuerta, facturas
    from pipeline.extraccion import Hallazgo

    _motivo_facturas["original"] = facturas.MOTIVO
    es_factura = facturas.es_factura

    @functools.wraps(es_factura)
    def con_reglas_de_entrada(asunto: str = "", cuerpo: str = "", *a, **k):
        facturas.MOTIVO = _motivo_facturas["original"]
        correo = _actual.get("correo") or {}
        fuentes = {"asunto": asunto, "cuerpo": cuerpo, "remitente": correo.get("remitente", ""),
                   "adjuntos": "\n".join(correo.get("adjuntos", []))}
        for regla in [r for r in _reglas if r.get("punto") == "entrada"]:
            try:
                cumple, detalle = _cumple(regla, fuentes.get(regla.get("campo"), ""))
            except re.error as e:
                _anunciar_regla(regla, False, f"patrón inválido: {e}")
                continue
            _anunciar_regla(regla, cumple, detalle)
            if cumple and regla.get("accion") == "manual":
                facturas.MOTIVO = regla.get("motivo") or "REGLA_PERSONALIZADA"
                return f"regla personalizada «{regla.get('nombre')}»: {detalle}"
        return es_factura(asunto, cuerpo, *a, **k)

    facturas.es_factura = con_reglas_de_entrada

    validar = compuerta.validar

    @functools.wraps(validar)
    def con_reglas_de_validacion(hallazgos, *a, **k):
        extra = []
        for regla in [r for r in _reglas if r.get("punto") == "validacion"]:
            campo = regla.get("campo", "")
            h = hallazgos.get(campo)
            try:
                cumple, detalle = _cumple(regla, getattr(h, "valor", None) if h else None)
            except re.error as e:
                _anunciar_regla(regla, False, f"patrón inválido: {e}")
                continue
            _anunciar_regla(regla, cumple, detalle)
            if not cumple:
                continue
            if regla.get("accion") == "fijar":
                hallazgos[campo] = Hallazgo(campo=campo, valor=regla.get("valor", ""),
                                            evidencia=f"regla personalizada «{regla.get('nombre')}»",
                                            confianza=1.0, metodo=f"regla:{regla.get('id')}")
            elif regla.get("accion") == "manual":
                extra.append(compuerta.Reparo(campo=campo, codigo=regla.get("motivo") or "REGLA_PERSONALIZADA",
                                              detalle=f"«{regla.get('nombre')}»: {detalle}", bloqueante=True))
        d = validar(hallazgos, *a, **k)
        if extra:
            d.reparos.extend(extra)
            d.veredicto = compuerta.Veredicto.RECHAZADO
            d.motivo_manual = extra[0].codigo
        return d

    compuerta.validar = con_reglas_de_validacion
    _reemplazar_en_modulos(validar, con_reglas_de_validacion)
    # El observador va por fuera: el dictamen que se anuncia ya trae las reglas.
    _envolver(compuerta, "validar", _anunciar_dictamen)


def _anunciar_dictamen(d, a, k) -> None:
    veredicto = getattr(d.veredicto, "value", str(d.veredicto))
    textos = {"aprobado": "Aprobado: se puede radicar", "corregido": "Aprobado con correcciones",
              "rechazado": f"Rechazado: {d.motivo_manual}"}
    _actual["rondas"] = _actual.get("rondas", 0) + 1
    ronda = _actual["rondas"]
    contexto = "primera validación" if ronda == 1 else (_actual.get("fase_validacion") or "otra validación")
    _paso("compuerta", f"{textos.get(veredicto, veredicto)} · ronda {ronda}" if ronda > 1 else textos.get(veredicto, veredicto),
          "error" if veredicto == "rechazado" else "aviso" if veredicto == "corregido" else "ok",
          "\n".join(f"{'✗' if r.bloqueante else '•'} {r.codigo} · {r.campo}: {r.detalle}" for r in d.reparos[:20]),
          ronda=ronda, contexto=contexto, motivo=d.motivo_manual or "",
          campos={c: _corto(v, 120) for c, v in _valores(a[0] if a else k.get("hallazgos")).items()},
          veredicto=veredicto,
          reparos=[{"campo": r.campo, "codigo": r.codigo, "detalle": str(r.detalle)[:300], "bloqueante": r.bloqueante}
                   for r in d.reparos[:30]],
          correcciones={c: _corto(v, 80) for c, v in (d.correcciones or {}).items()})


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
    f = inspect.unwrap(getattr(importlib.import_module(modulo), funcion))
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
    os.environ["MODO_TEST"] = "false" if REAL else "true"
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
            # La transcripción por visión va sin instrucciones de sistema y con
            # temperatura 0: esa no se toca.
            if _ia["temperatura"] is not None and config is not None and getattr(config, "system_instruction", None):
                config.temperature = _ia["temperatura"]
            return generar_original(*a, **k)

        modelos_api.generate_content = _generar
        _modelos[clave] = m
    return _modelos[clave]


_vivo: Dict[str, Any] = {"servicio": None}


def _carpeta_del_caso(caso: str) -> Path:
    """Un caso es un correo traído del buzón real (VIVO) o de la carpeta local."""
    for base in (VIVO, BANDEJA):
        carpeta = base / caso
        if (carpeta / "mensaje.json").exists():
            return carpeta
    raise FileNotFoundError(f"no existe el caso {caso}")


def _correo(caso: str):
    from models.email import EmailMessage

    carpeta = _carpeta_del_caso(caso)
    crudo = json.loads((carpeta / "mensaje.json").read_text(encoding="utf-8"))
    vivo = bool(crudo.get("_vivo"))
    limpio = {k: v for k, v in crudo.items() if not k.startswith("_")}
    if not vivo:
        # La carpeta local guardó el correo con las etiquetas que el job le
        # puso después de procesarlo: con ellas sería siempre un duplicado.
        limpio["categories"] = []
    return carpeta, crudo, EmailMessage.from_graph_api(limpio), vivo


def _buzon_para(carpeta: Path, crudo: dict, vivo: bool):
    if not vivo:
        if REAL:
            # Una copia local no es un correo de la bandeja del job: radicarla duplicaría.
            raise PermissionError("con la radicación real solo se corren correos traídos del buzón real")
        return buzon_vivo.BuzonLocal(carpeta, crudo, evento)
    if _vivo["servicio"] is None:
        _vivo["servicio"] = buzon_vivo.servicio(escribir=REAL)
    if REAL:
        return buzon_vivo.BuzonQueEscribe(_vivo["servicio"], carpeta, crudo, evento)
    return buzon_vivo.BuzonVivo(_vivo["servicio"], carpeta, crudo, evento)


_fuentes: Dict[str, Any] = {}


def _auditoria():
    """
    La capa de remitente y asunto (Supabase), como `correr._auditoria` del job.

    Compuerta apagada: con SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY reales, el
    SupabaseService del motor sobre `ConsultaSupabase` (solo GET); sus escrituras
    siguen yendo al grabador de MODO_TEST. Sin ellas, la capa queda apagada.
    Compuerta encendida: el SupabaseService real, como el job.
    """
    from config import settings
    from services.supabase_client import SupabaseService

    if REAL:
        _fuentes["supabase"] = {"real": True, "detalle": "SupabaseService del job: lee y escribe"}
        return SupabaseService()
    if not (solo_lectura.definida("SUPABASE_URL") and solo_lectura.definida("SUPABASE_SERVICE_ROLE_KEY")):
        _fuentes["supabase"] = {"real": False, "detalle": "sin SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en el entorno del flujo: la capa de auditoría queda apagada"}
        return None
    s = SupabaseService()  # en MODO_TEST no crea cliente
    s._client = solo_lectura.ConsultaSupabase(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY, evento, _mascara)
    _fuentes["supabase"] = {"real": True, "detalle": "consulta radicados_pqrd con GET (solo select); las trazas quedan retenidas"}
    return s


def _anunciar_fuentes(vivo: bool) -> None:
    """Qué se lee de verdad y qué es un doble en esta corrida, y en qué estado está la compuerta."""
    evento("compuerta", radicacion_real=REAL, texto=solo_lectura.ROTULO_ENCENDIDA if REAL else solo_lectura.ROTULO_APAGADA)
    graph = ({"real": True, "detalle": f"buzón real ({buzon_vivo.origen_credenciales() or 'credenciales de Graph'}); "
              + ("escribe como el job" if REAL else "solo GET: las escrituras quedan retenidas")}
             if vivo else {"real": False, "detalle": "correo de la carpeta local: las consultas del buzón no encuentran nada"})
    lecturas = [
        {"sistema": "Microsoft Graph", **graph},
        {"sistema": "Supabase", **_fuentes.get("supabase", {"real": False, "detalle": "sin configurar"})},
        {"sistema": "Redis", "real": False, "detalle": "el job no usa la caché de hashes (correr.py no se la pasa al orquestador)"},
        {"sistema": "SGDEA", "real": REAL, "detalle": "radica de verdad" if REAL else "radicar, asignar y trazabilidad quedan retenidas (MODO_TEST)"},
        {"sistema": "Registro de personas", "real": True, "detalle": "consulta (GET) real"},
        {"sistema": "Vertex AI", "real": True, "detalle": "modelo real con el ADC"},
    ]
    evento("fuentes", lecturas=lecturas)


def _orquestador(orden: Dict[str, Any], base: str):
    """Un orquestador como el del job. En un lote es uno solo para todos los correos."""
    global _registro
    from config import settings
    from pipeline.orquestador import Orquestador
    from pipeline.personas import ConsultaDePersonas
    from pipeline.radicador import Radicador
    from services.radicacion import RadicacionService

    ia = orden.get("ia") or {}
    _ia["temperatura"] = ia.get("temperatura")
    modelo = ModeloObservado(modelo_vertex(ia.get("modelo", "gemini-2.5-flash"), ia.get("ubicacion", "global")))
    if _registro is None:
        _registro = ConsultaDePersonas(url_base=settings.MS_SGDEA_BETA_URL)
    servicio = RadicacionService(timeout=45)
    orq = Orquestador(modelo=modelo, outlook=BuzonDelLote(), radicador=Radicador(servicio=servicio),
                      base_workspace=str(TRABAJO / "ws" / base), sgdea=RegistroObservado(_registro),
                      auditoria=_auditoria())
    return orq, servicio, modelo


async def _atender(orq, modelo, corrida: str, caso: str, tope_s: float, lote: str = "") -> None:
    """Un correo, con su propio estado y su propio buzón, dentro de su tarea."""
    from config import settings
    from pipeline.atribucion import Origen

    _actual.nuevo(corrida=corrida, lote=lote)
    t = time.time()
    try:
        carpeta, crudo, email, vivo = _correo(caso)
        buzon = _buzon_para(carpeta, crudo, vivo)
    except Exception as e:
        evento("resultado", caso=caso, accion="error", motivo=f"{type(e).__name__}: {e}"[:500],
               traza=traceback.format_exc()[-3000:], segundos=0)
        return
    _buzon_de_la_tarea.set(buzon)
    # Con el buzón real las capas que consultan Graph se encienden: son
    # lecturas. Con la carpeta local no hay nada que consultar.
    settings.DEDUP_EN_PRUEBAS = vivo
    de = (crudo.get("from") or {}).get("emailAddress") or {}
    _actual["correo"] = {"remitente": f"{de.get('name', '')} <{de.get('address', '')}>",
                         "adjuntos": [a.get("name", "") for a in crudo.get("_adjuntos", [])]}
    _actual["umbral"] = _umbral_vigente()
    adjuntos.iniciar(corrida, TRABAJO)
    evento("inicio", caso=caso, asunto=crudo.get("subject", ""), modelo=modelo.nombre,
           buzon="real" if vivo else "local", lote=lote, radicacion_real=REAL,
           categorias=list(crudo.get("categories") or []) if vivo else [])
    _anunciar_fuentes(vivo)
    try:
        r = await asyncio.wait_for(orq.procesar(email), tope_s)
        evento("resultado", caso=caso, accion=r.accion, motivo=r.motivo_manual,
               ubicaciones={c: adjuntos.ubicar(h.valor, h.evidencia) for c, h in r.hallazgos.items()},
               revision=r.revision,
               radicado=r.radicado, avisos=r.avisos, explicacion=r.explicacion_del_agente,
               vueltas=r.turnos_del_agente, tokens_entrada=r.tokens_entrada,
               tokens_salida=r.tokens_salida, etapas_ms=r.etapas_ms, duracion_ms=r.duracion_ms,
               area_destino=r.area_destino, razon_del_reenvio=r.razon_del_reenvio,
               razon_del_duplicado=r.razon_del_duplicado, anexo_de=getattr(r, "anexo_de", ""),
               hallazgos={c: {"valor": h.valor, "metodo": h.metodo, "confianza": h.confianza,
                              "evidencia": str(h.evidencia)[:400]}
                          for c, h in r.hallazgos.items()},
               sin_resolver=sorted(c for c, o in r.atribucion.items() if o is Origen.NO_RESUELTO),
               atribucion={c: getattr(o, "value", str(o)) for c, o in r.atribucion.items()},
               buzon="real" if vivo else "local", lote=lote,
               escrituras_buzon=buzon.escrituras, segundos=round(time.time() - t, 1))
    except Exception as e:
        evento("resultado", caso=caso, accion="error", motivo=f"{type(e).__name__}: {e}"[:500],
               traza=traceback.format_exc()[-3000:], segundos=round(time.time() - t, 1))


async def procesar(orden: Dict[str, Any]) -> None:
    """Un correo solo: su propio orquestador."""
    orq, servicio, modelo = _orquestador(orden, orden["corrida"])
    try:
        await _atender(orq, modelo, orden["corrida"], orden["caso"], orden.get("tope_s", 600))
    finally:
        await servicio.close()


async def procesar_lote(orden: Dict[str, Any]) -> None:
    """
    Varios correos como una corrida del job: un solo orquestador (el mismo
    detector de duplicados para todos), de a CONCURRENCIA a la vez y en el orden
    en que llegan.
    """
    from correr import CONCURRENCIA

    lote = orden["lote"]
    orq, servicio, modelo = _orquestador(orden, lote)
    turno = asyncio.Semaphore(max(1, int(orden.get("concurrencia") or CONCURRENCIA)))

    async def uno(item: Dict[str, str]) -> None:
        async with turno:
            await _atender(orq, modelo, item["corrida"], item["caso"], orden.get("tope_s", 600), lote)

    try:
        await asyncio.gather(*(uno(item) for item in orden["corridas"]))
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
        if orden.get("cmd") in ("procesar", "procesar_lote"):
            primera = orden["corrida"] if orden["cmd"] == "procesar" else orden["corridas"][0]["corrida"]
            _actual.nuevo(corrida=primera)
            # La orden dice con qué compuerta la mandó el servidor: si no es la de este proceso, no se corre.
            if bool(orden.get("radicacion_real", False)) != REAL:
                items = [{"corrida": orden["corrida"], "caso": orden["caso"]}] if orden["cmd"] == "procesar" else orden["corridas"]
                for item in items:
                    _actual.nuevo(corrida=item["corrida"])
                    evento("resultado", caso=item["caso"], accion="error", segundos=0,
                           motivo="la compuerta de radicación cambió: el trabajador se reinicia y no corre esta orden")
                _actual.nuevo(corrida=None)
                evento("libre", lote=orden.get("lote", ""))
                return
            fallidas = aplicar_configuracion(orden.get("parametros", {}), orden.get("entorno", {}))
            _reglas[:] = [r for r in orden.get("reglas", []) if isinstance(r, dict) and r.get("activa", True)]
            if fallidas:
                evento("configuracion", fallidas=fallidas)
            try:
                if orden["cmd"] == "procesar":
                    await procesar(orden)
                else:
                    await procesar_lote(orden)
            finally:
                restaurar_configuracion()
                _reglas.clear()
                _actual.nuevo(corrida=None)
                evento("libre", lote=orden.get("lote", ""))
        elif orden.get("cmd") == "ping":
            evento("pong")


if __name__ == "__main__":
    asyncio.run(principal())
