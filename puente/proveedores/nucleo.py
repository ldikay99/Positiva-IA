"""
La única puerta a la IA: `generar` (una respuesta, texto o JSON), `probar` (una
llamada mínima de verdad) y `conversar_con_herramientas` (varias vueltas con
function calling). Igual para todos los proveedores.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from proveedores import catalogo
from proveedores.base import (ErrorDeNegocio, ErrorTransitorio, Parte, Peticion, Resultado, balde, con_reintentos, enmascarar,
                              errores_esquema, huella, json_de, partes_de, secretos_de, semaforo)
from proveedores.registro import obtener

Aviso = Optional[Callable[..., None]]


def _turnos(mensajes: Any, tarea: Optional[str], partes: Any) -> List[dict]:
    turnos: List[dict] = []
    for m in mensajes or []:
        rol = {"user": "usuario", "assistant": "asistente", "model": "asistente"}.get(str(m.get("rol") or m.get("role") or "usuario"), str(m.get("rol") or "usuario"))
        if rol == "usuario":
            turnos.append({"rol": "usuario", "partes": partes_de(m.get("partes") if m.get("partes") is not None else m.get("texto", m.get("content", "")))})
        elif rol == "asistente":
            turnos.append({"rol": "asistente", "texto": str(m.get("texto", m.get("content", "")) or ""), "llamadas": m.get("llamadas") or []})
        else:
            turnos.append(m)
    if tarea is not None:
        turnos.append({"rol": "usuario", "partes": partes_de(tarea)})
    extra = partes_de(partes)
    if extra:
        if not turnos or turnos[-1]["rol"] != "usuario":
            turnos.append({"rol": "usuario", "partes": []})
        turnos[-1]["partes"] = list(turnos[-1]["partes"]) + extra
    if not turnos:
        raise ErrorDeNegocio("no hay nada que pedirle al modelo (sin mensajes ni tarea)")
    return turnos


def revisar_partes(proveedor: str, modelo: str, turnos: List[dict]) -> None:
    tipos = {x.tipo for t in turnos if t["rol"] == "usuario" for x in t["partes"]}
    for tipo, cap, que in (("imagen", "imagenes", "imágenes"), ("pdf", "pdf", "PDF")):
        if tipo in tipos and catalogo.acepta(proveedor, modelo, cap) is False:
            raise ErrorDeNegocio(f"el modelo «{modelo}» de {obtener(proveedor).nombre} no acepta {que}")


def _llamada(proveedor: str, cred: dict, flujo: str, por_minuto: Optional[float], aviso: Aviso):
    a = obtener(proveedor)
    h = huella(cred)
    sem = semaforo(proveedor, h)
    cubeta = balde(proveedor, h, flujo, por_minuto)
    secretos = secretos_de(cred)

    def una(p: Peticion) -> Resultado:
        cubeta.tomar(p.timeout)
        if not sem.acquire(timeout=p.timeout):
            raise ErrorTransitorio(f"{a.nombre}: no se liberó un cupo de concurrencia a tiempo")
        try:
            return a.llamar(p, cred, flujo)
        finally:
            sem.release()

    def correr(p: Peticion) -> tuple:
        try:
            return con_reintentos(lambda: una(p), aviso=aviso, secretos=secretos)
        except Exception as e:
            # Nada sale con la llave, ni en el mensaje ni en la traza encadenada.
            msg = enmascarar(str(e), secretos)
            if msg != str(e):
                e.args = (msg,)
            raise
    return a, correr


def _costo(proveedor: str, modelo: str, entrada: int, salida: int) -> tuple:
    c = catalogo.costo(proveedor, modelo, entrada, salida)
    return (c if c is not None else 0.0), c is not None


def generar(proveedor: str, modelo: str, sistema: str = "", mensajes: Optional[List[dict]] = None, tarea: Optional[str] = None,
            esquema_json: Optional[dict] = None, temperatura: Optional[float] = None, partes: Any = None, timeout: float = 120.0, *,
            credencial: Optional[dict] = None, json_libre: bool = False, flujo: str = "", por_minuto: Optional[float] = None,
            aviso: Aviso = None, sin_razonamiento: bool = False, max_tokens: Optional[int] = None) -> Dict[str, Any]:
    """Una respuesta del modelo. Con esquema_json (o json_libre) trae `json` validado (si no cumple, se pide una reparación).

    Devuelve {texto, json, tokens_entrada, tokens_salida, costo_estimado_usd, costo_conocido, proveedor, modelo, ms, intentos, reparado, errores_json}."""
    cred = dict(credencial or {})
    a = obtener(proveedor)
    a.revisar_credencial(cred)
    modelo = str(modelo or "").strip()
    if not modelo:
        raise ErrorDeNegocio(f"falta el modelo de {a.nombre}")
    f = catalogo.ficha(proveedor, modelo)
    if f and f.get("solo_voz"):
        raise ErrorDeNegocio(f"«{modelo}» es un modelo de voz en vivo: no sirve para generar texto en un flujo")
    turnos = _turnos(mensajes, tarea, partes)
    revisar_partes(proveedor, modelo, turnos)
    _, correr = _llamada(proveedor, cred, flujo, por_minuto, aviso)
    p = Peticion(modelo=modelo, sistema=sistema or "", turnos=turnos, esquema=esquema_json or None, json_libre=bool(json_libre),
                 temperatura=temperatura, timeout=float(timeout or 120), max_tokens=max_tokens, sin_razonamiento=sin_razonamiento)
    t0 = time.time()
    r, intentos = correr(p)
    entrada, salida, texto, valor, errores, reparado = r.tokens_entrada, r.tokens_salida, r.texto, None, [], False
    if esquema_json or json_libre:
        valor = r.json if r.json is not None else json_de(r.texto)
        errores = ["la respuesta no es JSON"] if valor is None else errores_esquema(valor, esquema_json)
        if errores:
            arreglo = Peticion(**{**p.__dict__, "instruir_json": True, "turnos": turnos + [
                {"rol": "asistente", "texto": r.texto or "(vacío)"},
                {"rol": "usuario", "partes": [Parte("texto", texto="Tu respuesta anterior no sirve: " + "; ".join(errores[:8])
                                                     + ". Devuelve SOLO el JSON corregido, sin texto alrededor.")]}]})
            r2, k2 = correr(arreglo)
            intentos += k2
            entrada, salida, reparado = entrada + r2.tokens_entrada, salida + r2.tokens_salida, True
            v2 = r2.json if r2.json is not None else json_de(r2.texto)
            e2 = ["la respuesta no es JSON"] if v2 is None else errores_esquema(v2, esquema_json)
            if v2 is not None and len(e2) <= len(errores):
                valor, errores, texto = v2, e2, r2.texto
    costo, conocido = _costo(proveedor, modelo, entrada, salida)
    return {"texto": texto or "", "json": valor, "tokens_entrada": entrada, "tokens_salida": salida, "costo_estimado_usd": costo,
            "costo_conocido": conocido, "proveedor": proveedor, "modelo": modelo, "ms": int((time.time() - t0) * 1000),
            "intentos": intentos, "reparado": reparado, "errores_json": errores}


def probar(proveedor: str, modelo: str, credencial: Optional[dict] = None, timeout: float = 60.0) -> Dict[str, Any]:
    """Una llamada mínima de verdad: {ok, ms, texto, tokens_entrada, tokens_salida, costo_estimado_usd} o {ok: false, error}."""
    t0 = time.time()
    try:
        r = generar(proveedor, modelo, "Eres una prueba de conexión.", tarea="Responde solo: ok", temperatura=0.0, timeout=timeout,
                    credencial=credencial, flujo="prueba", max_tokens=None)
        return {"ok": True, "ms": r["ms"], "texto": r["texto"][:200], "tokens_entrada": r["tokens_entrada"], "tokens_salida": r["tokens_salida"],
                "costo_estimado_usd": r["costo_estimado_usd"], "proveedor": proveedor, "modelo": modelo}
    except Exception as e:  # noqa: BLE001 - el error es la respuesta
        return {"ok": False, "ms": int((time.time() - t0) * 1000), "error": enmascarar(f"{e}", secretos_de(credencial))[:500],
                "status": getattr(e, "status_code", None), "proveedor": proveedor, "modelo": modelo}


def conversar_con_herramientas(proveedor: str, modelo: str, credencial_valores: Optional[dict], sistema: str, mensajes: List[dict],
                               herramientas: List[dict], ejecutar: Callable[[str, dict], Any], max_vueltas: int = 8, aviso: Aviso = None, *,
                               temperatura: Optional[float] = None, timeout: float = 120.0, flujo: str = "vera",
                               max_tokens: Optional[int] = None) -> Dict[str, Any]:
    """Una conversación con herramientas (function calling), igual para Gemini/Vertex, los estilo OpenAI y Claude.

    - mensajes: [{rol: "usuario"|"asistente", texto?, partes?: [texto | {tipo: "imagen"|"pdf", datos, mime}]}]
    - herramientas: [{nombre, descripcion, parametros (JSON Schema)}]
    - ejecutar(nombre, args) -> resultado (lo que sea serializable); si lanza, el modelo recibe {"error": ...}
    - aviso(tipo, **datos): «modelo_inicio», «modelo_fin», «herramienta_inicio», «herramienta_fin», «reintento»

    Devuelve {texto, pasos: [{herramienta, args, resultado, ms, error?}], vueltas, tokens_entrada, tokens_salida,
    costo_estimado_usd, costo_conocido, proveedor, modelo, ms, historial (la conversación neutral, para seguirla)}."""
    cred = dict(credencial_valores or {})
    a = obtener(proveedor)
    a.revisar_credencial(cred)
    if herramientas and catalogo.acepta(proveedor, modelo, "herramientas") is False:
        raise ErrorDeNegocio(f"el modelo «{modelo}» de {a.nombre} no acepta herramientas")
    turnos = _turnos(mensajes, None, None)
    revisar_partes(proveedor, modelo, turnos)
    _, correr = _llamada(proveedor, cred, flujo, None, aviso)
    avisar = aviso or (lambda *x, **y: None)
    t0 = time.time()
    entrada = salida = 0
    pasos: List[dict] = []
    texto = ""
    vuelta = 0
    for vuelta in range(1, max(1, max_vueltas) + 1):
        p = Peticion(modelo=modelo, sistema=sistema or "", turnos=turnos, temperatura=temperatura, herramientas=herramientas or [],
                     timeout=float(timeout), max_tokens=max_tokens)
        avisar("modelo_inicio", vuelta=vuelta, proveedor=proveedor, modelo=modelo)
        ti = time.time()
        r, _ = correr(p)
        entrada, salida = entrada + r.tokens_entrada, salida + r.tokens_salida
        avisar("modelo_fin", vuelta=vuelta, proveedor=proveedor, modelo=modelo, ms=int((time.time() - ti) * 1000),
               tokens_entrada=r.tokens_entrada, tokens_salida=r.tokens_salida, llamadas=[c["nombre"] for c in r.llamadas])
        turnos = turnos + [{"rol": "asistente", "texto": r.texto, "llamadas": r.llamadas, "nativo": r.nativo}]
        texto = r.texto or texto
        if not r.llamadas:
            break
        resultados = []
        for c in r.llamadas:
            avisar("herramienta_inicio", nombre=c["nombre"], args=c.get("args") or {})
            th = time.time()
            paso: Dict[str, Any] = {"herramienta": c["nombre"], "args": c.get("args") or {}}
            try:
                res = ejecutar(c["nombre"], c.get("args") or {})
            except Exception as e:  # noqa: BLE001 - el modelo se entera del fallo
                res = {"error": f"{type(e).__name__}: {e}"[:500]}
                paso["error"] = res["error"]
            paso.update(resultado=res, ms=int((time.time() - th) * 1000))
            pasos.append(paso)
            avisar("herramienta_fin", nombre=c["nombre"], ms=paso["ms"], error=paso.get("error"))
            resultados.append({"id": c["id"], "nombre": c["nombre"], "resultado": res})
        turnos = turnos + [{"rol": "herramienta", "resultados": resultados}]
    costo, conocido = _costo(proveedor, modelo, entrada, salida)
    return {"texto": texto, "pasos": pasos, "vueltas": vuelta, "tokens_entrada": entrada, "tokens_salida": salida, "costo_estimado_usd": costo,
            "costo_conocido": conocido, "proveedor": proveedor, "modelo": modelo, "ms": int((time.time() - t0) * 1000), "historial": turnos}
