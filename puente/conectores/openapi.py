"""
El conector genérico por OpenAPI (3.x) y Swagger (2.0): lee la especificación
(JSON o YAML, pegada o por URL), saca sus operaciones con sus parámetros y
arma cada petición revisando lo que pide la especificación (parámetros
obligatorios, tipos, enumeraciones y cuerpo obligatorio).

No ejecuta nada de la especificación: solo la lee como datos. La URL de la
especificación y la URL base pasan por los mismos bloqueos que todo conector
(`destino_seguro`); las IP privadas solo con red_privada en una conexión creada
por una persona.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from .base import ErrorDeNegocio

METODOS = ("get", "post", "put", "patch", "delete", "head", "options")
MAX_SPEC = 5_000_000


def cargar_texto(texto: str) -> dict:
    t = (texto or "").strip()
    if not t:
        raise ErrorDeNegocio("la especificación está vacía")
    if len(t) > MAX_SPEC:
        raise ErrorDeNegocio("la especificación pasa de 5 MB")
    if t[:1] in "{[":
        try:
            d = json.loads(t)
        except ValueError as e:
            raise ErrorDeNegocio(f"la especificación no es un JSON válido: {e}") from e
    else:
        try:
            import yaml  # PyYAML (MIT); safe_load no construye objetos de Python
        except ImportError as e:
            raise ErrorDeNegocio("la especificación parece YAML y aquí no está PyYAML: pégala en JSON") from e
        try:
            d = yaml.safe_load(t)
        except Exception as e:
            raise ErrorDeNegocio(f"la especificación no es un YAML válido: {type(e).__name__}") from e
    if not isinstance(d, dict) or not ("openapi" in d or "swagger" in d) or not isinstance(d.get("paths"), dict):
        raise ErrorDeNegocio("eso no es una especificación OpenAPI o Swagger (falta openapi/swagger y paths)")
    return d


def _ref(spec: dict, x: Any, prof: int = 0) -> Any:
    """Resuelve un $ref local (#/components/…, #/parameters/…, #/definitions/…)."""
    if not isinstance(x, dict) or "$ref" not in x or prof > 8:
        return x
    r = str(x["$ref"])
    if not r.startswith("#/"):
        raise ErrorDeNegocio(f"la especificación usa una referencia externa ({r[:80]}); solo se aceptan las locales (#/…)")
    v: Any = spec
    for parte in r[2:].split("/"):
        parte = parte.replace("~1", "/").replace("~0", "~")
        if not isinstance(v, dict) or parte not in v:
            raise ErrorDeNegocio(f"la especificación no tiene {r[:80]}")
        v = v[parte]
    return _ref(spec, v, prof + 1)


def base_de(spec: dict) -> str:
    if spec.get("swagger"):
        host = str(spec.get("host") or "")
        esquema = (spec.get("schemes") or ["https"])[0]
        return f"{esquema}://{host}{spec.get('basePath') or ''}".rstrip("/") if host else ""
    for s in spec.get("servers") or []:
        url = str((s or {}).get("url") or "")
        for k, v in ((s or {}).get("variables") or {}).items():
            url = url.replace("{" + k + "}", str((v or {}).get("default") or ""))
        if url:
            return url.rstrip("/")
    return ""


def _tipo(esq: Any, spec: dict) -> Dict[str, Any]:
    e = _ref(spec, esq) if isinstance(esq, dict) else {}
    return {"tipo": e.get("type") or "", **({"enum": e["enum"]} if isinstance(e.get("enum"), list) else {}), **({"formato": e["format"]} if e.get("format") else {})}


def operaciones(spec: dict) -> List[dict]:
    """[{id, metodo, ruta, resumen, parametros: [{nombre, en, obligatorio, tipo, enum?}], cuerpo, cuerpo_obligatorio, escritura}]."""
    r = []
    for ruta, item in (spec.get("paths") or {}).items():
        item = _ref(spec, item) or {}
        comunes = [_ref(spec, p) for p in item.get("parameters") or []]
        for metodo in METODOS:
            op = item.get(metodo)
            if not isinstance(op, dict):
                continue
            params: Dict[Tuple[str, str], dict] = {}
            for p in comunes + [_ref(spec, x) for x in op.get("parameters") or []]:
                if isinstance(p, dict) and p.get("name") and p.get("in") in ("path", "query", "header", "body", "formData", "cookie"):
                    params[(p["name"], p["in"])] = p
            cuerpo_ob, hay_cuerpo = False, False
            lista = []
            for (nombre, en), p in params.items():
                if en == "body":  # Swagger 2
                    hay_cuerpo, cuerpo_ob = True, bool(p.get("required"))
                    continue
                if en == "cookie":
                    continue
                t = _tipo(p.get("schema") or p, spec)
                lista.append({"nombre": nombre, "en": "query" if en == "formData" else en, "obligatorio": bool(p.get("required")) or en == "path",
                              "tipo": t["tipo"], **({"enum": t["enum"]} if "enum" in t else {}), "descripcion": str(p.get("description") or "")[:200]})
            rb = _ref(spec, op.get("requestBody")) if op.get("requestBody") else None
            if isinstance(rb, dict):
                hay_cuerpo, cuerpo_ob = True, bool(rb.get("required"))
            oid = str(op.get("operationId") or f"{metodo.upper()} {ruta}")
            r.append({"id": oid, "metodo": metodo.upper(), "ruta": ruta, "resumen": str(op.get("summary") or op.get("description") or "")[:200],
                      "parametros": lista, "cuerpo": hay_cuerpo, "cuerpo_obligatorio": cuerpo_ob, "escritura": metodo not in ("get", "head", "options")})
    return r


def buscar(ops: List[dict], operacion: str) -> dict:
    o = str(operacion or "").strip()
    for x in ops:
        if x["id"] == o:
            return x
    m = re.match(r"^([A-Za-z]+)\s+(\S+)$", o)
    if m:
        for x in ops:
            if x["metodo"] == m.group(1).upper() and x["ruta"] == m.group(2):
                return x
    cerca = [x["id"] for x in ops if o.lower() in x["id"].lower()][:5] or [x["id"] for x in ops][:8]
    raise ErrorDeNegocio(f"la especificación no tiene la operación «{o}» (hay: {', '.join(cerca)})")


def _convertir(valor: Any, p: dict) -> Any:
    t = p.get("tipo")
    v = valor
    if t == "integer":
        try:
            v = int(str(valor).strip())
        except ValueError:
            raise ErrorDeNegocio(f"el parámetro {p['nombre']} tiene que ser un entero (llegó «{str(valor)[:40]}»)")
    elif t == "number":
        try:
            v = float(str(valor).strip().replace(",", "."))
        except ValueError:
            raise ErrorDeNegocio(f"el parámetro {p['nombre']} tiene que ser un número (llegó «{str(valor)[:40]}»)")
    elif t == "boolean":
        s = str(valor).strip().lower()
        if s not in ("true", "false", "1", "0", "si", "sí", "no"):
            raise ErrorDeNegocio(f"el parámetro {p['nombre']} tiene que ser true o false")
        v = s in ("true", "1", "si", "sí")
    if p.get("enum") and v not in p["enum"] and str(v) not in [str(x) for x in p["enum"]]:
        raise ErrorDeNegocio(f"el parámetro {p['nombre']} vale «{v}» y la API acepta: {', '.join(map(str, p['enum']))[:200]}")
    return v


def armar(op: dict, base: str, valores: Dict[str, Any], cuerpo: Any) -> dict:
    """{metodo, url, query, encabezados, cuerpo} de la operación con esos valores, revisados contra la especificación."""
    faltan = [p["nombre"] for p in op["parametros"] if p["obligatorio"] and valores.get(p["nombre"]) in (None, "")]
    if faltan:
        raise ErrorDeNegocio(f"la operación {op['id']} necesita {', '.join(faltan)} (en parametros)")
    conocidos = {p["nombre"] for p in op["parametros"]}
    ajenos = [k for k in valores if k not in conocidos]
    if ajenos:
        raise ErrorDeNegocio(f"la operación {op['id']} no tiene los parámetros {', '.join(ajenos)} (tiene: {', '.join(sorted(conocidos)) or 'ninguno'})")
    if op.get("cuerpo_obligatorio") and cuerpo in (None, "", {}):
        raise ErrorDeNegocio(f"la operación {op['id']} necesita cuerpo")
    if cuerpo not in (None, "") and not op.get("cuerpo"):
        raise ErrorDeNegocio(f"la operación {op['id']} ({op['metodo']}) no lleva cuerpo")
    ruta = op["ruta"]
    query: Dict[str, Any] = {}
    encabezados: Dict[str, str] = {}
    for p in op["parametros"]:
        v = valores.get(p["nombre"])
        if v in (None, ""):
            continue
        v = _convertir(v, p)
        if p["en"] == "path":
            ruta = ruta.replace("{" + p["nombre"] + "}", urllib.parse.quote(str(v).lower() if isinstance(v, bool) else str(v), safe=""))
        elif p["en"] == "query":
            query[p["nombre"]] = str(v).lower() if isinstance(v, bool) else v
        elif p["en"] == "header":
            if p["nombre"].lower() in ("authorization", "cookie", "proxy-authorization"):
                raise ErrorDeNegocio(f"el encabezado {p['nombre']} lo pone la conexión, no el flujo")
            encabezados[p["nombre"]] = str(v)
    if "{" in ruta:
        raise ErrorDeNegocio(f"faltan parámetros de ruta en {ruta}")
    if not base:
        raise ErrorDeNegocio("la especificación no dice su servidor: pon la URL base en la conexión")
    return {"metodo": op["metodo"], "url": base.rstrip("/") + ruta, "query": query, "encabezados": encabezados, "cuerpo": cuerpo}
