"""
Las herramientas de Vera para las versiones de un flujo (declarativo o de código): listar, comparar, deshacer
el último cambio, restaurar una versión y volver a la última que funcionaba (la última con una corrida que
terminó bien). Usan la API del historial (servidor/src/rutas_versiones.ts).

Cada cambio de Vera queda con su autor y, la primera vez que toca un flujo por un pedido, la versión de antes
queda como punto «antes de: <lo que pidió la persona>» (lo pone el servidor con los encabezados que manda
asistente.api). Lo que cambia el flujo devuelve `confirmado` y la versión real que quedó en uso: sin
`confirmado: true`, Vera no dice que restauró.

Se declaran en asistente.HERRAMIENTAS (asistente.py agrega DECLARACIONES) y las ejecuta `ejecutar`.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

_ID = {"type": "string", "description": "id del flujo"}

DECLARACIONES: List[Dict[str, Any]] = [
    {"name": "comparar_versiones", "description": "Qué cambió entre dos versiones de un flujo (declarativo o de código), paso por paso: pasos agregados y quitados, configuración campo por campo (antes → después), conexiones y datos del flujo. Sin a, la anterior a b; sin b, la actual.",
     "parameters": {"type": "object", "properties": {"id": _ID, "a": {"type": "integer", "description": "la versión de antes"}, "b": {"type": "integer", "description": "la versión de después (por defecto la actual)"}}, "required": ["id"]}},
    {"name": "deshacer_cambio", "description": "Deshace el último cambio de un flujo (declarativo o de código) volviendo a la versión de antes; si el último cambio lo hiciste tú, deshace TODO lo que hiciste en ese pedido (vuelve al punto «antes de: …»). Con solo_ultimo_guardado=true deshace solo el último guardado. Queda como versión nueva (la historia no se borra) y devuelve confirmado y la versión real.",
     "parameters": {"type": "object", "properties": {"id": _ID, "solo_ultimo_guardado": {"type": "boolean"}}, "required": ["id"]}},
    {"name": "restaurar_version_que_funcionaba", "description": "Busca la última versión de un flujo con la que una corrida terminó bien (cruza el historial de versiones con el de corridas) y, con restaurar=true (por defecto), lo devuelve a esa versión como versión nueva. Con restaurar=false solo dice cuál es. Devuelve confirmado y la versión real.",
     "parameters": {"type": "object", "properties": {"id": _ID, "restaurar": {"type": "boolean"}}, "required": ["id"]}},
]

NOMBRES = {d["name"] for d in DECLARACIONES} | {"versiones_flujo", "restaurar_version"}
# Las que cambian el flujo (para anotar qué cambió y para el resumen si se acaban las vueltas).
CAMBIAN = {"restaurar_version", "deshacer_cambio", "restaurar_version_que_funcionaba"}

NOTA_CONFIRMADO = "Quedó hecho: di la versión que quedó en uso (version) y a cuál volvió (restaurada). La historia no se borró."
NOTA_NO = "NO se cambió el flujo: dilo así, con el motivo (error); no digas que restauraste ni que deshiciste."


def _compactar_version(v: Dict[str, Any]) -> Dict[str, Any]:
    c = v.get("corridas") or {}
    r = {k: v[k] for k in ("version", "autor", "etiqueta", "punto", "resumen", "nota", "pedido", "restaurada_de", "por", "del_codigo") if v.get(k) not in (None, "", False)}
    r["actual"] = bool(v.get("actual"))
    if v.get("fecha"):
        import datetime
        r["fecha"] = datetime.datetime.fromtimestamp(v["fecha"] / 1000).strftime("%Y-%m-%d %H:%M")
    if c.get("bien") or c.get("revisar") or c.get("fallo"):
        r["corridas"] = {k: c[k] for k in ("bien", "revisar", "fallo") if c.get(k)}
    return r


def _cambio(r: Any, extra_nota: str = "") -> Dict[str, Any]:
    if not isinstance(r, dict) or r.get("error") or not r.get("confirmado"):
        x = r if isinstance(r, dict) else {"error": str(r)}
        return {"confirmado": False, "error": x.get("error") or "no se pudo", **({"problemas": x["problemas"]} if x.get("problemas") else {}), "nota": NOTA_NO}
    out = {"confirmado": True, "version": r.get("version"), "restaurada": r.get("restaurada")}
    if r.get("sin_cambio"):
        out["sin_cambio"] = True
        out["nota"] = "El flujo ya estaba en esa versión: no hizo falta cambiar nada. Dilo así."
    else:
        out["nota"] = NOTA_CONFIRMADO + extra_nota
    if r.get("deshecha") is not None:
        out["deshecha"] = r["deshecha"]
    return out


def ejecutar(nombre: str, a: Dict[str, Any], api: Callable[..., Any], emitir: Callable[..., None]) -> Any:
    fid = a.get("id") or a.get("flujo")
    if nombre == "versiones_flujo":
        r = api("GET", f"/api/flujos/{fid}/versiones")
        if not isinstance(r, list):
            return r
        n = int(a.get("n") or 15)
        return {"flujo": fid, "total": len(r), "versiones": [_compactar_version(v) for v in r[:n]],
                "nota": "autor «vera» = las hiciste tú; «punto» = el estado antes de un pedido (a donde vuelve deshacer_cambio); corridas = cómo terminaron las que corrieron con esa versión."}
    if nombre == "comparar_versiones":
        q = "&".join(f"{k}={int(a[k])}" for k in ("a", "b") if a.get(k) is not None)
        r = api("GET", f"/api/flujos/{fid}/versiones/comparar" + (f"?{q}" if q else ""))
        if isinstance(r, dict) and not r.get("error"):
            r["nota"] = "a = antes, b = después. Explícalo en palabras: qué pasos se agregaron o quitaron, qué cambió en cada uno y qué conexiones."
        return r
    if nombre == "restaurar_version":
        r = api("POST", f"/api/flujos/{fid}/restaurar", {"version": a.get("version")})
        res = _cambio(r)
        if res["confirmado"]:
            emitir("control", accion="editor", op="recargar")
        return res
    if nombre == "deshacer_cambio":
        r = api("POST", f"/api/flujos/{fid}/deshacer", {"pedido": not a.get("solo_ultimo_guardado")})
        res = _cambio(r, " Si la persona quiere recuperar lo deshecho, se puede rehacer con restaurar_version a la versión deshecha.")
        if res["confirmado"]:
            emitir("control", accion="editor", op="recargar")
        return res
    if nombre == "restaurar_version_que_funcionaba":
        b = api("GET", f"/api/flujos/{fid}/versiones/funcionaba")
        if not isinstance(b, dict) or b.get("error"):
            return {"confirmado": False, "error": (b or {}).get("error") if isinstance(b, dict) else str(b), "nota": NOTA_NO}
        if b.get("version") is None:
            return {"confirmado": False, "encontrada": None, "error": b.get("nota") or "ninguna versión anterior tiene una corrida que haya terminado bien", "nota": NOTA_NO}
        encontrada = {"version": b["version"], "corridas_bien": b.get("corridas_bien"), "actual": b.get("actual")}
        if a.get("restaurar") is False:
            return {"encontrada": encontrada, "nota": "Solo la buscaste: no cambiaste el flujo."}
        r = api("POST", f"/api/flujos/{fid}/restaurar", {"version": b["version"]})
        res = _cambio(r)
        res["encontrada"] = encontrada
        if res["confirmado"]:
            emitir("control", accion="editor", op="recargar")
        return res
    return {"error": f"herramienta desconocida: {nombre}"}
