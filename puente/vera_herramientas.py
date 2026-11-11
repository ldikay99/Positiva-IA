"""
Las herramientas de Vera: UN registro y UNA forma de ejecutarlas, para el chat escrito y para la voz.

Dónde se agrega una herramienta nueva (las dos maneras de hablar con Vera la reciben sin hacer nada más):
  1. Su declaración en `asistente.HERRAMIENTAS` y su código en `asistente._mover_pantalla`, como siempre.
  2. Su grupo en `asistente.GRUPOS`: el chat escrito manda al modelo solo los grupos del pedido (núcleo +
     los que pida); la voz las declara todas al abrir la sesión.
  3. Aquí, solo si hace falta:
     - `LARGAS` si puede tardar: corre como tarea en segundo plano, con seguimiento y aviso al terminar;
     - `asistente.PARALELAS` si solo lee: puede correr a la vez que otras (lo demás es barrera y va solo);
     - `SOLO_VOZ` / `SOLO_CHAT`, con su motivo, si no tiene sentido en un modo (pruebas_voz.py lo exige).

El ejecutor (`Ejecutor`) es el mismo para los dos modos:
  - corre una vuelta de llamadas por tandas (las de solo lectura a la vez, las barreras solas y en orden),
    avisa cada paso con su `indice` y anota lo que deja (flujos cambiados, propuestas con botón);
  - a cada resultado le pone `situacion`: confirmado, en_curso, propuesto, no_confirmado o fallo;
  - lo largo se lanza como tarea: en el chat espera unos segundos en línea (si termina, el resultado va
    en la misma respuesta) y si no, responde «en_curso» y el resultado llega después; en la voz, en_curso
    de inmediato. Las corridas lanzadas sin esperar (correr_correos, correr_buzon…) también se siguen;
  - las tareas se registran en el servidor (`/api/chats/:id/tareas`), que las muestra en el panel del chat
    y deja consultarlas con `estado_tareas` desde cualquier proceso de la conversación.

`verificar_respuesta` revisa que lo que Vera dice («guardé», «corrí», «ya lo tienes», «se radicó», «listo»)
lo respalde una herramienta confirmada del turno; si no, el que llama le pide corregirlo.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import asistente

# ------------------------------------------------------------------ registro
# Corren como tarea en segundo plano (pueden tardar): el resultado llega después.
LARGAS = {"responder_por_escrito", "probar_flujo", "correr_flujo", "correr_buzon", "ver_buzon", "listar_correos", "ver_logs",
          "subir_adjuntos_a_flujo", "leer_buzon_flujo", "clonar_flujo", "diagnosticar_flujo", "probar_paso", "probar_ia",
          "generar_documento", "aplicar_marca"}
# Lanzan corridas sin esperarlas: se siguen hasta que terminen.
LANZAN_CORRIDAS = {"correr_correos", "correr_buzon", "correr_flujo", "reintentar_corrida", "subir_adjuntos_a_flujo"}
TOPE_LARGA = int(os.environ.get("VERA_TOPE_TAREA", "300"))
TOPE_CORTA = 45
ESPERA_CHAT = float(os.environ.get("VERA_ESPERA_EN_LINEA", "20"))

ESTADO_TAREAS = {
    "name": "estado_tareas",
    "description": "Las tareas largas que lanzaste en esta conversación (por escrito o en la llamada): cuáles siguen en curso, "
                   "cuáles terminaron y con qué resultado. Úsala cuando la persona pregunte cómo va algo, antes de decir que algo "
                   "terminó, o con tarea=<número o id> para el resultado completo de una.",
    "parameters": {"type": "object", "properties": {"tarea": {"type": "string"}}},
}
# Comunes a los dos modos y definidas aquí (no son de la API de la plataforma, sino del seguimiento).
PROPIAS: List[Dict[str, Any]] = [ESTADO_TAREAS]

DE_VOZ: List[Dict[str, Any]] = [
    {"name": "responder_por_escrito", "description": "Pasa un pedido al proceso escrito (más capaz): crea o cambia flujos, analiza, explica, compara o arma listas y tablas. Lo resuelve con todas las herramientas, lo deja escrito en el chat y te devuelve lo que escribió para que digas lo esencial en una frase.", "parameters": {"type": "object", "properties": {"pedido": {"type": "string", "description": "Qué hay que hacer, completo y concreto, con los ids o nombres que se mencionaron."}}, "required": ["pedido"]}},
    {"name": "escribir_en_chat", "description": "Escribe en el chat (markdown) algo corto que ya tienes y conviene leer: un id, un dato, una lista breve.", "parameters": {"type": "object", "properties": {"texto": {"type": "string"}}, "required": ["texto"]}},
    {"name": "terminar_llamada", "description": "Cuelga la llamada. Úsala solo cuando la persona se despidió o lo pidió, después de despedirte.", "parameters": {"type": "object", "properties": {}}},
]
SOLO_VOZ: Dict[str, str] = {
    "responder_por_escrito": "en el chat escrito Vera ya es el proceso escrito: no tiene a quién delegar",
    "escribir_en_chat": "en el chat escrito todo lo que responde ya queda escrito en el chat",
    "terminar_llamada": "solo hay algo que colgar en una llamada",
}
SOLO_CHAT: Dict[str, str] = {
    # La llamada declara todas sus herramientas al abrir la sesión de Gemini Live y no se pueden agregar a mitad: no hay grupos que cargar.
    "cargar_herramientas": "la sesión de voz ya trae todas las herramientas desde que se abre",
}


def registro() -> List[Dict[str, Any]]:
    """Todas las herramientas de Vera, sin repetir (si una se declara dos veces, manda la de asistente.HERRAMIENTAS)."""
    vistos, r = set(), []
    for h in list(asistente.HERRAMIENTAS) + PROPIAS + DE_VOZ:
        if h["name"] not in vistos:
            vistos.add(h["name"])
            r.append(h)
    return r


def de_modo(modo: str) -> List[Dict[str, Any]]:
    """Las herramientas del chat escrito («chat») o de la voz («voz»)."""
    fuera = SOLO_VOZ if modo == "chat" else SOLO_CHAT
    return [h for h in registro() if h["name"] not in fuera]


def declaracion(nombre: str) -> Optional[Dict[str, Any]]:
    return next((h for h in registro() if h["name"] == nombre), None)


def es_larga(nombre: str, args: Dict[str, Any]) -> bool:
    if nombre not in LARGAS:
        return False
    if nombre == "correr_flujo" and not args.get("esperar"):
        return False  # sin esperar responde al instante; la corrida se sigue aparte
    if nombre == "subir_adjuntos_a_flujo" and not args.get("correr"):
        return False
    return True


def etiqueta(nombre: str, args: Dict[str, Any]) -> str:
    """Cómo se llama una tarea en el panel del chat y en la llamada."""
    f = args.get("flujo") or args.get("id") or ""
    base = {
        "responder_por_escrito": "Resolviendo por escrito", "probar_flujo": "Probando el flujo", "correr_flujo": "Corriendo el flujo",
        "correr_buzon": "Corriendo el buzón", "ver_buzon": "Leyendo el buzón", "listar_correos": "Buscando correos", "ver_logs": "Leyendo el registro",
        "subir_adjuntos_a_flujo": "Corriendo con los adjuntos", "leer_buzon_flujo": "Leyendo el buzón", "clonar_flujo": "Clonando",
        "diagnosticar_flujo": "Diagnosticando", "probar_paso": "Probando el paso", "probar_ia": "Probando la IA", "generar_documento": "Generando el documento",
        "aplicar_marca": "Poniendo la marca", "seguir_corridas": "Esperando las corridas",
    }.get(nombre, nombre.replace("_", " ").capitalize())
    return f"{base} {f}".strip() if isinstance(f, str) and f and len(f) < 60 else base


# ------------------------------------------------------------------ situación de cada resultado
def situacion(nombre: str, res: Any) -> str:
    """confirmado | en_curso | propuesto | no_confirmado | fallo — lo que Vera puede decir de ese resultado."""
    if not isinstance(res, dict):
        return "confirmado" if res is not None else "fallo"
    if res.get("estado") == "en_curso" or res.get("situacion") == "en_curso":
        return "en_curso"
    if res.get("error") or res.get("guardado") is False or res.get("ok") is False:
        return "fallo"
    if res.get("propuesto"):
        return "propuesto"
    if "confirmado" in res and res.get("confirmado") is not True:
        return "no_confirmado"
    if nombre in ("correr_correos", "correr_buzon", "reintentar_corrida") or (nombre == "correr_flujo" and "desenlace" not in res):
        return "en_curso"  # creó corridas: todavía no hay resultado
    if res.get("estado") == "sigue corriendo":
        return "en_curso"
    return "confirmado"


NOTAS_SITUACION = {
    "en_curso": "Sigue en curso: di que está en curso y NO digas que terminó ni des su resultado; te llegará cuando termine (o consúltalo con estado_tareas).",
    "no_confirmado": "No quedó confirmado: no digas que se hizo; di lo que pasó de verdad.",
    "fallo": "No se hizo: dilo así y di por qué.",
    "propuesto": "Solo quedó propuesto (un botón para la persona): no digas que se hizo.",
}


def marcar(nombre: str, res: Any) -> Any:
    if isinstance(res, dict) and "situacion" not in res:
        res["situacion"] = situacion(nombre, res)
    return res


def _resumir(res: Any, n: int = 1500) -> str:
    return json.dumps(res, ensure_ascii=False, default=str)[:n]


def cifras_de(res: Any) -> List[str]:
    """Las cifras de un resultado completo (para que la procedencia de un documento las encuentre en turnos siguientes,
    aunque el resultado guardado en la conversación vaya recortado)."""
    import procedencia
    f = procedencia.Fuentes()
    f.agregar(res, "x")
    return [f"{v:g}" for v in sorted(f.valores)][:400]


# ------------------------------------------------------------------ tareas
class Tarea:
    def __init__(self, n: int, ident: str, nombre: str, args: Dict[str, Any], firma: str, origen: str) -> None:
        self.n, self.id, self.nombre, self.args, self.firma, self.origen = n, ident, nombre, args, firma, origen
        self.etiqueta = etiqueta(nombre, args)
        self.desde = time.time()
        self.hasta: Optional[float] = None
        self.resultado: Any = None
        self.listo = threading.Event()
        self.publicada = False
        self.entregada = False

    @property
    def estado(self) -> str:
        if not self.listo.is_set():
            return "en_curso"
        return "fallo" if situacion(self.nombre, self.resultado) == "fallo" else "lista"

    def publico(self, con_resultado: bool = False) -> Dict[str, Any]:
        d = {"id": self.id, "n": self.n, "nombre": self.nombre, "etiqueta": self.etiqueta, "estado": self.estado, "origen": self.origen,
             "desde": int(self.desde * 1000), "hasta": int(self.hasta * 1000) if self.hasta else None, "firma": self.firma[:400], "pid": os.getpid(),
             "argumentos": asistente._resumen_args(self.args)}
        if con_resultado and self.hasta:
            d["resultado"] = _resumir(self.resultado, 6000)
        return d


_ACTIVOS: List["Ejecutor"] = []
_ACTIVOS_CANDADO = threading.Lock()


class Ejecutor:
    """Una sola forma de ejecutar herramientas, para el chat escrito y la voz.

    avisar(tipo, **datos): «herramienta» {nombre, argumentos, indice, paralelo?}, «resultado_herramienta» {nombre, resultado,
      indice, situacion, cifras}, «tarea_iniciada» {tarea, id, nombre, etiqueta}, «tarea_lista» {tarea, id, nombre, estado}.
    espera_en_linea: segundos que una tarea larga espera antes de pasar a segundo plano (0: de inmediato; None: nunca,
      corre en línea hasta su tope, como dentro de responder_por_escrito).
    manejadores: herramientas propias del modo (las de la voz), nombre → función(args) → resultado.
    al_resultado(nombre, args, resumen, res): cada resultado (también el de una tarea al terminar).
    al_terminar_tarea(tarea): una tarea en segundo plano terminó (la voz se lo cuenta en una pausa; el chat lo reporta).
    """

    def __init__(self, modo: str, avisar: Callable[..., None], *, espera_en_linea: Optional[float] = None,
                 manejadores: Optional[Dict[str, Callable[[Dict[str, Any]], Any]]] = None,
                 al_resultado: Optional[Callable[[str, Dict[str, Any], Dict[str, Any], Any], None]] = None,
                 al_terminar_tarea: Optional[Callable[[Tarea], None]] = None, conversacion: Optional[str] = None,
                 tope_corta: Optional[float] = None) -> None:
        self.modo = modo
        self.tope_corta = tope_corta
        self._candado_aviso = threading.Lock()
        self._avisar = avisar
        self.espera = espera_en_linea
        self.manejadores = manejadores or {}
        self.al_resultado = al_resultado
        self.al_terminar_tarea = al_terminar_tarea
        self.conversacion = conversacion or asistente.CONVERSACION.get("id")
        self.pasos: List[Dict[str, Any]] = []
        self.registro: List[Tuple[str, Dict[str, Any], Any]] = []
        self.cambiados: set = set()
        self.propuestas: List[Dict[str, Any]] = []
        self.tareas: Dict[int, Tarea] = {}
        self._n = 0
        self._candado = threading.Lock()
        self._prefijo = f"{modo[0]}{secrets.token_hex(2)}"
        with _ACTIVOS_CANDADO:
            _ACTIVOS.append(self)

    def cerrar(self) -> None:
        with _ACTIVOS_CANDADO:
            if self in _ACTIVOS:
                _ACTIVOS.remove(self)

    def avisar(self, tipo: str, **d: Any) -> None:
        with self._candado_aviso:
            try:
                self._avisar(tipo, **d)
            except Exception:  # noqa: BLE001 - un aviso que falla no tumba la herramienta
                pass

    # -------------------------------------------------------------- una vuelta
    def vuelta(self, llamadas: List[Tuple[str, Dict[str, Any]]]) -> List[Any]:
        """Corre las llamadas de una vuelta (las independientes a la vez) y devuelve sus resultados en el orden pedido."""
        base = len(self.pasos)
        resumenes = [asistente._resumen_args(a) for _, a in llamadas]

        def anunciar(tanda: List[int]) -> None:
            for i in tanda:
                self.avisar("herramienta", nombre=llamadas[i][0], argumentos=resumenes[i], indice=base + i, **({"paralelo": len(tanda)} if len(tanda) > 1 else {}))

        def correr(i: int, nombre: str, args: Dict[str, Any]) -> Any:
            res = self.una(nombre, args)
            self.avisar("resultado_herramienta", nombre=nombre, resultado=_resumir(res), indice=base + i,
                        situacion=situacion(nombre, res), cifras=cifras_de(res))
            return res

        resultados = asistente.correr_llamadas(llamadas, correr, anunciar)
        for i, ((nombre, args), res) in enumerate(zip(llamadas, resultados)):
            self._anotar(nombre, args, resumenes[i], res)
        return resultados

    def _anotar(self, nombre: str, args: Dict[str, Any], resumen: Dict[str, Any], res: Any) -> None:
        asistente.anotar_resultado(nombre, args, res, self.cambiados, self.propuestas)
        with self._candado:
            self.registro.append((nombre, args, res))
            self.pasos.append({"nombre": nombre, "argumentos": resumen, "resultado": _resumir(res, 600), "cifras": cifras_de(res)})
        if self.al_resultado:
            try:
                self.al_resultado(nombre, args, resumen, res)
            except Exception:  # noqa: BLE001
                pass

    def una(self, nombre: str, args: Dict[str, Any]) -> Any:
        """Una herramienta, con su situación marcada. No lanza."""
        args = dict(args or {})
        try:
            if nombre == "estado_tareas":
                res = self.estado_tareas(args)
            elif self.espera is not None and es_larga(nombre, args):
                res = self._lanzar(nombre, args)
            else:
                res = self._correr(nombre, args, self.tope_corta)
        except Exception as e:  # noqa: BLE001 - el modelo se entera del fallo
            res = {"error": f"{type(e).__name__}: {e}"}
        res = marcar(nombre, res)
        if isinstance(res, dict) and res.get("situacion") in NOTAS_SITUACION and "nota_situacion" not in res:
            res["nota_situacion"] = NOTAS_SITUACION[res["situacion"]]
        if nombre in LANZAN_CORRIDAS and self.espera is not None:
            self._seguir_corridas(nombre, args, res)
        return res

    def _correr(self, nombre: str, args: Dict[str, Any], tope: Optional[float]) -> Any:
        if nombre in self.manejadores:
            fn: Callable[[], Any] = lambda: self.manejadores[nombre](args)
        elif nombre in SOLO_VOZ:
            return {"error": f"{nombre} solo existe en la llamada de voz"}
        else:
            fn = lambda: asistente.ejecutar(nombre, args)
        if not tope:
            return fn()
        caja: Dict[str, Any] = {}

        def correr() -> None:
            try:
                caja["r"] = fn()
            except Exception as e:  # noqa: BLE001
                caja["r"] = {"error": f"{type(e).__name__}: {e}"}
        h = threading.Thread(target=correr, daemon=True, name=f"herr-{nombre}")
        h.start()
        h.join(tope)
        return caja["r"] if "r" in caja else {"error": "tardó demasiado; puede seguir en curso, vuelve a intentarlo o míralo después", "situacion": "fallo"}

    # -------------------------------------------------------------- tareas en segundo plano
    def _firma(self, nombre: str, args: Dict[str, Any]) -> str:
        return nombre + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)

    def _en_curso_igual(self, firma: str) -> Optional[Dict[str, Any]]:
        with self._candado:
            t = next((t for t in self.tareas.values() if t.firma == firma and not t.listo.is_set()), None)
        if t:
            return {"tarea": t.n, "id": t.id}
        for x in tareas_del_servidor(self.conversacion):
            if x.get("estado") == "en_curso" and x.get("firma") == firma[:400]:
                return {"tarea": x.get("n"), "id": x.get("id")}
        return None

    def _lanzar(self, nombre: str, args: Dict[str, Any], correr: Optional[Callable[[], Any]] = None) -> Any:
        firma = self._firma(nombre, args)
        igual = self._en_curso_igual(firma) if correr is None else None
        if igual:
            return {"estado": "en_curso", **igual, "nota": "Esa misma tarea ya está corriendo; no la repitas. Su resultado llegará solo (o mira estado_tareas)."}
        with self._candado:
            self._n += 1
            t = Tarea(self._n, f"{self._prefijo}-{self._n}", nombre, args, firma, self.modo)
            self.tareas[t.n] = t

        def trabajar() -> None:
            try:
                t.resultado = correr() if correr else self._correr(nombre, args, None)
            except Exception as e:  # noqa: BLE001
                t.resultado = {"error": f"{type(e).__name__}: {e}"}
            t.resultado = marcar(nombre, t.resultado)
            t.hasta = time.time()
            t.listo.set()

        hilo = threading.Thread(target=trabajar, name=f"tarea-{t.id}", daemon=True)
        hilo.start()
        # Lo que termina en un momento (un documento chico) responde en línea y no parpadea en el panel; lo demás se publica.
        # En la voz el momento es corto (0,6 s) para que siga hablando; en el chat, hasta 1,5 s antes de mostrarlo.
        momento = 0.6 if self.espera == 0 else min(1.5, self.espera or 1.5)
        if not t.listo.wait(momento):
            self._publicar(t)
        if self.espera:
            t.listo.wait(max(0.0, self.espera - momento))
        if t.listo.is_set():
            # terminó a tiempo: su resultado va en la misma respuesta
            t.entregada = True
            if t.publicada:
                self._publicar(t)
            with self._candado:
                self.tareas.pop(t.n, None)
            return t.resultado
        threading.Thread(target=self._vigilar, args=(t, hilo), name=f"vigila-{t.id}", daemon=True).start()
        return {"estado": "en_curso", "situacion": "en_curso", "tarea": t.n, "id": t.id, "etiqueta": t.etiqueta,
                "nota": f"Corre en segundo plano (tarea {t.n}). Di en pocas palabras que está en curso y sigue atendiendo; "
                        f"cuando termine te llegará «[Resultado de la tarea {t.n}]» (o consúltala con estado_tareas). No digas que terminó."}

    def _vigilar(self, t: Tarea, hilo: threading.Thread) -> None:
        if not t.listo.wait(TOPE_LARGA):
            t.resultado = {"error": f"pasaron {TOPE_LARGA} s y no terminó; puede seguir corriendo en la plataforma (míralo con ultimas_corridas o historial)",
                           "situacion": "fallo"}
            t.hasta = time.time()
            t.listo.set()
        self._publicar(t)
        resumen = asistente._resumen_args(t.args)
        self._anotar(t.nombre, t.args, resumen, t.resultado)
        if self.al_terminar_tarea:
            try:
                self.al_terminar_tarea(t)
            except Exception:  # noqa: BLE001
                pass

    def _publicar(self, t: Tarea) -> None:
        t.publicada = True
        pub = t.publico(con_resultado=True)
        if t.listo.is_set():
            self.avisar("tarea_lista", tarea=t.n, id=t.id, nombre=t.nombre, etiqueta=t.etiqueta, estado=t.estado)
        else:
            self.avisar("tarea_iniciada", tarea=t.n, id=t.id, nombre=t.nombre, etiqueta=t.etiqueta)
        if self.conversacion:
            try:
                asistente.api("PUT", f"/api/chats/{self.conversacion}/tareas", pub)
            except Exception:  # noqa: BLE001 - el seguimiento no tumba la tarea
                pass

    def _seguir_corridas(self, nombre: str, args: Dict[str, Any], res: Any) -> None:
        """Las corridas que lanzó una herramienta sin esperarlas se siguen como una tarea: al terminar, su resultado llega."""
        if not isinstance(res, dict) or res.get("situacion") != "en_curso" or res.get("tarea"):
            return
        ids = [x.get("id") for x in res.get("creadas") or res.get("corridas") or [] if isinstance(x, dict) and x.get("id")]
        if isinstance(res.get("corrida"), str):
            ids.append(res["corrida"])
        if nombre == "reintentar_corrida" and res.get("id"):
            ids.append(res["id"])
        ids = list(dict.fromkeys(ids))[:10]
        if not ids:
            return

        def esperar() -> Any:
            fin = time.time() + TOPE_LARGA
            salida = []
            for cid in ids:
                c = asistente._esperar_corrida(cid, max(5, int(fin - time.time())))
                salida.append(asistente._resumen_corrida(c) if c else {"corrida": cid, "estado": "sigue corriendo"})
            return {"corridas": salida, "terminadas": sum(1 for x in salida if x.get("estado") in ("terminada", "fallida")), "de": len(ids)}

        r = self._lanzar("seguir_corridas", {"de": nombre, "corridas": ids, **({"flujo": args.get("flujo") or args.get("id")} if args.get("flujo") or args.get("id") else {})}, esperar)
        if isinstance(r, dict) and r.get("tarea"):
            res["tarea"] = r["tarea"]
            res["nota_situacion"] = (f"Las corridas siguen en curso y las sigo como tarea {r['tarea']}: di que están corriendo; "
                                     "cuando terminen te llega su resultado. No digas cómo terminaron todavía.")
        elif isinstance(r, dict) and r.get("corridas"):
            res["resultado_de_las_corridas"] = r  # terminaron mientras esperaba
            res["situacion"] = "confirmado"
            res.pop("nota_situacion", None)

    def en_curso(self) -> List[Tarea]:
        with self._candado:
            return [t for t in self.tareas.values() if not t.listo.is_set()]

    def pendientes_de_reporte(self) -> List[Tarea]:
        with self._candado:
            return [t for t in self.tareas.values() if not t.entregada]

    def esperar_siguiente(self, tope: float) -> Optional[Tarea]:
        """La próxima tarea que termine y no se haya reportado (None si no queda ninguna o pasa el tope)."""
        fin = time.time() + tope
        while time.time() < fin:
            pend = self.pendientes_de_reporte()
            if not pend:
                return None
            lista = next((t for t in sorted(pend, key=lambda x: x.hasta or 0) if t.listo.is_set()), None)
            if lista:
                lista.entregada = True
                return lista
            time.sleep(0.3)
        return None

    def esperar_tareas(self, tope: float) -> None:
        fin = time.time() + tope
        for t in self.en_curso():
            t.listo.wait(max(0.0, fin - time.time()))

    def estado_tareas(self, args: Dict[str, Any]) -> Dict[str, Any]:
        locales = {t.id: t.publico(con_resultado=True) for t in list(self.tareas.values())}
        todas = {x.get("id"): x for x in tareas_del_servidor(self.conversacion, con_resultado=True) if x.get("id")}
        todas.update({k: v for k, v in locales.items() if k not in todas or v.get("estado") != "en_curso"})
        lista = sorted(todas.values(), key=lambda x: x.get("desde") or 0)
        q = str(args.get("tarea") or "").strip()
        if q:
            una = next((x for x in lista if q in (str(x.get("id")), str(x.get("n")))), None)
            if not una:
                return {"error": f"no encuentro la tarea {q}", "tareas": [{k: x.get(k) for k in ("id", "n", "etiqueta", "estado")} for x in lista]}
            return una
        return {"en_curso": [{k: x.get(k) for k in ("id", "n", "etiqueta", "estado", "desde", "origen")} for x in lista if x.get("estado") == "en_curso"],
                "terminadas": [{**{k: x.get(k) for k in ("id", "n", "etiqueta", "estado", "hasta", "origen")}, "resultado": str(x.get("resultado") or "")[:700]}
                               for x in lista if x.get("estado") != "en_curso"][-10:],
                "nota": "Lo en curso todavía no tiene resultado: dilo así. Lo terminado, con su resultado."}

    @property
    def lanzadas(self) -> List[Tuple[str, Dict[str, Any]]]:
        with self._candado:
            return [(n, a) for n, a, _ in self.registro if n not in self.manejadores]


def tareas_del_servidor(conversacion: Optional[str], con_resultado: bool = False) -> List[Dict[str, Any]]:
    if not conversacion:
        return []
    try:
        r = asistente.api("GET", f"/api/chats/{conversacion}/tareas" + ("?resultado=1" if con_resultado else ""))
    except Exception:  # noqa: BLE001
        return []
    return [x for x in (r or {}).get("tareas") or [] if isinstance(x, dict)] if isinstance(r, dict) else []


def registros_activos() -> List[Tuple[str, Dict[str, Any], Any]]:
    """Lo que devolvieron las herramientas en este proceso (todas las conversaciones que corren en él son la misma)."""
    with _ACTIVOS_CANDADO:
        activos = list(_ACTIVOS)
    r: List[Tuple[str, Dict[str, Any], Any]] = []
    for e in activos:
        with e._candado:
            r.extend(e.registro)
    return r


# ------------------------------------------------------------------ procedencia de las cifras de un documento
NO_SON_DATOS = {"ayuda_nodo", "tipos_de_nodo", "reglas_de_flujos", "ayuda_documentos", "cargar_herramientas", "plantillas", "generar_documento",
                "aplicar_marca", "escribir_en_chat", "terminar_llamada", "renombrar_chat"}


def fuentes_de_cifras(extra: Iterable[Tuple[str, Dict[str, Any], Any]] = (), con_adjuntos: bool = False) -> Any:
    """Las cifras que un documento puede usar: lo que devolvieron las herramientas en este turno y en la conversación,
    lo que escribió la persona y el texto de lo que adjuntó, y la fecha de hoy."""
    import procedencia
    f = procedencia.Fuentes()
    for nombre, _args, res in list(registros_activos()) + list(extra):
        if nombre not in NO_SON_DATOS:
            f.agregar(res, nombre)
    cid = asistente.CONVERSACION.get("id")
    conv = asistente.api("GET", f"/api/chats/{cid}") if cid else None
    for m in (conv or {}).get("mensajes") or [] if isinstance(conv, dict) else []:
        if m.get("rol") == "usuario":
            f.agregar_texto(m.get("texto") or "", "lo que escribió la persona")
            for adj in (m.get("adjuntos") or []) if con_adjuntos else []:
                try:
                    t = asistente.api("GET", f"/api/chats/{cid}/adjuntos/{adj.get('id')}/texto")
                    f.agregar_texto(str((t or {}).get("texto") or "")[:400000], f"adjunto {adj.get('nombre')}")
                except Exception:  # noqa: BLE001
                    pass
        for p in m.get("pasos") or []:
            if p.get("nombre") in NO_SON_DATOS:
                continue
            for c in p.get("cifras") or []:
                f.agregar_texto(str(c), p.get("nombre") or "herramienta")
            f.agregar(p.get("resultado") or "", p.get("nombre") or "herramienta")
    f.agregar_texto(asistente.fecha_de_hoy().split(",")[0], "la fecha de hoy")  # día y año, sin la hora (sus minutos no son cifras de nadie)
    return f


def revisar_cifras_documento(a: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """None si todas las cifras del documento tienen procedencia; si no, el rechazo con lo que falta."""
    import procedencia
    texto = procedencia.texto_de_documento(a)
    if not procedencia.cifras_del_documento(texto):
        return None
    r = procedencia.revisar(texto, fuentes_de_cifras(), a.get("calculos"))
    if not r["ok"] and r["sin_procedencia"]:
        # lo que falta puede venir de un documento que adjuntó la persona (se lee solo si hace falta: es lento)
        r = procedencia.revisar(texto, fuentes_de_cifras(con_adjuntos=True), a.get("calculos"))
    if r["ok"]:
        return None
    sin = r["sin_procedencia"][:15]
    return {"error": "No generé el documento: tiene cifras que no salen de ninguna herramienta ni de lo que dijo o adjuntó la persona. "
                     "No las supongas: consúltalas antes (historial con resumen=true y agrupar=dia, resumen_tablero, ultimas_corridas, ver_corrida, "
                     "diagnosticar_flujo, ver_ia_flujo, modelos_ia) y vuelve a generarlo SOLO con lo que devuelvan. Lo que calcules tú "
                     "(porcentajes, variaciones, promedios, sumas) va en «calculos» con su fórmula sobre cifras consultadas, "
                     "p. ej. {\"valor\": \"74,8 %\", \"formula\": \"77/103*100\"}. Si un dato no existe, escribe «sin dato» en vez de un número.",
            "sin_procedencia": sin, "problemas_calculos": r["problemas_calculos"][:8], "situacion": "fallo"}


# ------------------------------------------------------------------ nunca «de dientes para afuera»
_SIN_MARCAS = re.compile(r"[*_`#>|]")
_NEGACION = re.compile(r"\b(no|nunca|sin|ni|a[úu]n no|todav[íi]a no|no se|no pude|no he|no logr[ée])\b[^.!?\n]{0,25}$", re.I)
_ANTES = re.compile(r"\b(antes|anterior|ayer|hace un momento|ya estaba|la vez pasada|en el mensaje anterior|previamente|desde antes)\b", re.I)
_EN_CURSO = re.compile(r"en curso|corriendo|sigue[n]? (en|procesando|corriendo|trabajando)|todav[íi]a|a[úu]n (no|est|sig)|te aviso|te cuento|te escribo|"
                       r"cuando termine|en segundo plano|estoy en eso|lo estoy|la estoy|los estoy|est[áa]n? procesando|en proceso|en marcha|lanc[ée]|"
                       r"puse a correr|qued[óo] corriendo|quedaron corriendo|ya arranc|apenas termine|en cuanto termine|al terminar|"
                       r"un momento|un segundo|dame|ya te digo|d[ée]jame|voy a|lo reviso|lo miro|"
                       r"\b(revisando|buscando|procesando|generando|preparando|armando|cargando|leyendo|consultando|probando|diagnosticando|"
                       r"clonando|esperando|trabajando|mirando|resolviendo|analizando)\b", re.I)

# Solo las formas con tilde: «guardé» afirma, «¿quieres que lo guarde?» no.
AFIRMACIONES: List[Tuple[str, re.Pattern]] = [
    ("radico", re.compile(r"\b(se radic[óo]|se radicaron|qued[óo] radicad[oa]|quedaron radicad[oa]s|radiqué|fueron radicad[oa]s|fue radicad[oa])\b", re.I)),
    ("borro", re.compile(r"\b(borré|eliminé (el|los|la|las) flujos?|qued[óo] borrad[oa]|quedaron borrad[oa]s)\b", re.I)),
    ("escribio", re.compile(r"\b(guardé|creé|cambié|agregué|añadí|quité|eliminé|actualicé|configuré|renombré|cloné|dupliqué|"
                            r"restauré|apagué|encendí|desactivé|activé|conecté|traje|encadené|programé|ajusté|definí|instalé|"
                            r"inserté|corregí|arreglé|apliqué|reinicié|deshice|"
                            r"(qued[óo]|quedaron) (guardad|cread|actualizad|configurad|aplicad|instalad|corregid|arreglad|cambiad|clonad|restaurad)\w*)", re.I)),
    ("corrio", re.compile(r"\b(corrí|ejecuté|procesé|probé|reintenté|ya termin[óo]|terminaron|ya corri[óo]|ya corrieron)(?!\w)", re.I)),
    ("pantalla", re.compile(r"\b(abrí|te llevé|te mostré|ya (lo|la|los|las) (tienes|ves)|ah[íi] (lo|la|los|las) tienes|"
                            r"ya est[áa] en (tu )?pantalla|en pantalla tienes|lo tienes en pantalla)", re.I)),
    ("documento", re.compile(r"\b((generé|armé|hice|preparé|elaboré|creé|dejé) (el|la|los|las|un|una|tu|tus)? ?"
                             r"(documento|informe|pdf|word|excel|powerpoint|presentaci[óo]n|acta|archivo|reporte|hoja de c[áa]lculo)|"
                             r"aqu[íi] (tienes|est[áa]) (el|la|tu) (documento|informe|pdf|word|excel|powerpoint|presentaci[óo]n|acta|archivo|reporte))", re.I)),
    # «listo» suelto puede ser muletilla («Lista, ¿qué hacemos?»): cuenta como afirmación si hubo acciones que no se confirmaron.
    ("generico", re.compile(r"^\s*(listo|lista|hecho|ya qued[óo]|ya est[áa]|qued[óo] list[oa])\b", re.I)),
]
CORREN = {"correr_flujo", "probar_flujo", "correr_correos", "correr_buzon", "reintentar_corrida", "probar_paso", "subir_adjuntos_a_flujo",
          "probar_ia", "seguir_corridas", "responder_por_escrito"}
PANTALLA = {"enfocar_flujo", "navegar", "abrir_vista", "abrir_corrida", "centrar_nodo", "abrir_correo", "abrir_correspondencia", "filtrar_correos",
            "seleccionar_correos", "abrir_adjuntos", "abrir_proceso", "ajustar_lienzo", "controlar_editor", "abrir_entorno", "pedir_credencial",
            "pedir_secreto", "instalar_plantilla", "clonar_flujo"}
DOCUMENTOS = {"generar_documento", "aplicar_marca", "archivos_de_corrida", "crear_plantilla_documento", "probar_paso", "responder_por_escrito"}


def _hay(registro: List[Tuple[str, Dict[str, Any], Any]], nombres: Optional[set], estados: Tuple[str, ...] = ("confirmado",), excluir: set = frozenset()) -> bool:
    return any((nombres is None or n in nombres) and n not in excluir and situacion(n, r) in estados for n, _, r in registro)


def verificar_respuesta(texto: str, registro: List[Tuple[str, Dict[str, Any], Any]], en_curso: Optional[int] = None) -> List[str]:
    """Lo que la respuesta afirma sin una herramienta confirmada que lo respalde (vacío si todo cuadra)."""
    if not (texto or "").strip():
        return []
    limpio = _SIN_MARCAS.sub("", texto)
    frases = [f for f in re.split(r"(?<=[.!?\n])\s+", limpio) if f.strip()]
    solo_leen = set(asistente.PARALELAS) - {"generar_documento", "aplicar_marca"} | {"estado_tareas", "ver_pantalla", "escribir_en_chat"}
    problemas: List[str] = []
    vistos: set = set()
    # cuántas siguen corriendo al responder (quien llama lo sabe; si no, lo que quedó «en_curso» en el turno)
    en_curso_turno = en_curso if en_curso is not None else sum(1 for n, _, r in registro if situacion(n, r) == "en_curso")

    def agregar(clave: str, texto_problema: str) -> None:
        if clave not in vistos:
            vistos.add(clave)
            problemas.append(texto_problema)

    for frase in frases:
        if _ANTES.search(frase):
            continue  # habla de un turno anterior
        for clase, patron in AFIRMACIONES:
            for m in patron.finditer(frase):
                if _NEGACION.search(frase[:m.start()]):
                    continue
                dicho = m.group(0).strip()
                if clase == "radico":
                    if not re.search(r"producci[óo]n", frase, re.I):
                        agregar("radico", f"dijiste «{dicho}»: en modo prueba nada se radica en el SGDEA; di «se radicaría» o «quedó listo para radicar (en modo prueba)»")
                elif clase == "borro":
                    if _hay(registro, {"proponer_borrado", "borrar_flujo"}, ("propuesto",)):
                        agregar("borro", f"dijiste «{dicho}», pero solo dejaste el botón para que la persona confirme el borrado: dilo así")
                    else:
                        agregar("borro", f"dijiste «{dicho}» y nada se borró: borrar solo se propone con proponer_borrado")
                elif clase == "escribio":
                    if not _hay(registro, None, excluir=solo_leen | PANTALLA):
                        fallas = [n for n, _, r in registro if n not in solo_leen and situacion(n, r) in ("fallo", "no_confirmado", "en_curso", "propuesto")]
                        agregar("escribio", f"dijiste «{dicho}», pero ninguna herramienta de este turno lo confirmó"
                                + (f" ({', '.join(sorted(set(fallas)))} no quedaron confirmadas)" if fallas else " (no se llamó ninguna que cambie algo)"))
                elif clase == "corrio":
                    if not _hay(registro, CORREN | {"historial", "ultimas_corridas", "ver_corrida", "estado_tareas", "diagnosticar_flujo"}):
                        if _hay(registro, CORREN, ("en_curso",)) or en_curso_turno:
                            agregar("corrio", f"dijiste «{dicho}», pero sigue en curso: di que está corriendo y que el resultado llega al terminar")
                        else:
                            agregar("corrio", f"dijiste «{dicho}», pero no corriste nada en este turno")
                elif clase == "pantalla":
                    if not _hay(registro, PANTALLA):
                        nada = [n for n, _, r in registro if n in PANTALLA]
                        agregar("pantalla", f"dijiste «{dicho}», pero " + ("la pantalla no quedó confirmada (confirmado no es true): di lo que ve de verdad (en_pantalla) o usa ver_pantalla"
                                                                         if nada else "no moviste la pantalla en este turno: ábreselo con la herramienta o no lo afirmes"))
                elif clase == "documento":
                    if not _hay(registro, DOCUMENTOS):
                        agregar("documento", f"dijiste «{dicho}», pero ningún documento quedó generado en este turno"
                                + (" (generar_documento falló: di por qué)" if _hay(registro, {"generar_documento"}, ("fallo",)) else ""))
                elif clase == "generico":
                    acciones = [(n, r) for n, _, r in registro if n not in solo_leen]
                    muletilla = dicho.lower() in ("listo", "lista")
                    if (acciones and situacion(*acciones[-1]) in ("fallo", "no_confirmado")) or (not registro and not muletilla):
                        agregar("generico", f"empezaste con «{dicho}», pero lo último que hiciste no quedó confirmado o no hiciste nada: di lo que pasó de verdad")
    if en_curso_turno and not _EN_CURSO.search(limpio):
        agregar("en_curso", "hay tareas que siguen en curso y tu respuesta no lo dice: di que están en curso y que el resultado llega al terminar")
    # cifras de tokens dichas de memoria (en un documento las revisa generar_documento; aquí, las de la respuesta)
    try:
        problemas += tokens_sin_procedencia(texto, registro)
    except Exception:  # noqa: BLE001 - sin la API no se bloquea la respuesta
        pass
    return problemas


_TOKENS = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)*)\s*(millones|mill[óo]n|mil|M|k)?\s*(?:de\s+)?tokens\b", re.I)
_MULTIPLO = {"millones": 1e6, "millón": 1e6, "millon": 1e6, "m": 1e6, "mil": 1e3, "k": 1e3}


def tokens_sin_procedencia(texto: str, registro: List[Tuple[str, Dict[str, Any], Any]]) -> List[str]:
    """Los conteos de tokens que dice la respuesta y no salen de ninguna herramienta («~258 tokens por imagen» de memoria).
    Acepta lo redondeado a lo que se escribe («2,25 millones de tokens» por 2.250.447)."""
    import procedencia
    hallados = list(_TOKENS.finditer(_SIN_MARCAS.sub("", texto or "")))
    if not hallados:
        return []
    f = fuentes_de_cifras(registro)
    malos = []
    for m in hallados:
        mult = _MULTIPLO.get((m.group(2) or "").lower(), 1.0)
        d = procedencia._decimales(m.group(1))
        tolerancia = 0.5 * 10 ** -d * mult if mult > 1 else 0.0
        if not any(abs(s - v * mult) <= tolerancia for v in procedencia._valores(m.group(1)) for s in f.valores):
            malos.append(m.group(0))
    if not malos:
        return []
    return [f"dijiste {', '.join(f'«{x}»' for x in dict.fromkeys(malos))}, y ningún resultado de herramienta trae ese conteo: tokens, costos y precios "
            "salen solo de ver_corrida, ver_ia_flujo o modelos_ia; si no los tienes, di que no tienes ese dato"]


def pedido_de_correccion(problemas: List[str]) -> str:
    return ("(Sistema, revisión de veracidad) Tu respuesta afirma cosas que las herramientas no confirmaron:\n- " + "\n- ".join(problemas)
            + "\nReescribe la respuesta completa, sin esas afirmaciones: di solo lo confirmado, lo que sigue en curso como «en curso» "
              "y lo que falló como falló. Si de verdad falta hacer algo, hazlo con la herramienta antes de afirmarlo.")


def nota_de_veracidad(problemas: List[str]) -> str:
    """Si tras corregir sigue afirmando lo no confirmado, se le agrega a la respuesta lo que de verdad pasó."""
    return "\n\n_Ojo: " + "; ".join(p.split(":", 1)[-1].strip() if "dijiste" in p else p for p in problemas[:3]) + "._"
