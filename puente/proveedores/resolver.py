"""
Qué IA usa cada cosa, en tres niveles que nunca se mezclan:

1. Global de la plataforma (`/api/ia/global`): Vertex · gemini-2.5-flash si nadie la cambia.
2. Del flujo (`definicion.ia`): `{usar: "global"}` (lo de los flujos nuevos) o
   `{proveedor, modelo, credencial?, temperatura?, por_minuto?}`. Un flujo sin `ia`
   (los de antes) sigue en Vertex con su `modelo` de siempre.
3. Del nodo de IA: `config.proveedor`, `config.modelo`, `config.credencial` (o hereda).

Orden: nodo → flujo → global. Vera tiene su propia IA (aparte de las tres).
Aquí también está la validación (la misma que hace el servidor con zod).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from proveedores import catalogo
from proveedores.registro import PROVEEDORES

GLOBAL_DEFECTO = {"proveedor": "vertex", "modelo": "gemini-2.5-flash"}
NOMBRE_CREDENCIAL = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
TIPOS_IA = {"ia_clasificar", "ia_decidir", "ia_extraer", "ia_generar", "resumir", "ia_redactar",
            "traducir", "resumir_largo", "preguntar_documentos", "extraer_tablas", "documento_clasificar", "documento_comparar"}
# Los que necesitan JSON con esquema.
TIPOS_JSON = {"ia_clasificar", "ia_decidir", "ia_extraer", "documento_clasificar", "preguntar_documentos", "extraer_tablas"}
CLAVES_IA = ("proveedor", "modelo", "credencial")


def ia_global(valor: Any = None) -> Dict[str, Any]:
    g = dict(GLOBAL_DEFECTO)
    if isinstance(valor, dict) and valor.get("proveedor"):
        g = {k: v for k, v in valor.items() if k in ("proveedor", "modelo", "credencial", "temperatura") and v not in (None, "")}
    return g


def ia_del_flujo(defi: Dict[str, Any], global_: Any = None, modelo_legado: Optional[str] = None) -> Dict[str, Any]:
    ia = defi.get("ia") if isinstance(defi, dict) else None
    if isinstance(ia, dict) and ia.get("proveedor"):
        r = {k: ia.get(k) for k in ("proveedor", "modelo", "credencial", "temperatura", "por_minuto") if ia.get(k) not in (None, "")}
        return {**r, "origen": "flujo"}
    if isinstance(ia, dict) and ia.get("usar") == "global":
        return {**ia_global(global_), "origen": "global"}
    return {"proveedor": "vertex", "modelo": modelo_legado or (defi or {}).get("modelo") or GLOBAL_DEFECTO["modelo"], "origen": "legado"}


def ia_del_nodo(config: Dict[str, Any], del_flujo: Dict[str, Any]) -> Dict[str, Any]:
    c = config or {}
    prov = c.get("proveedor")
    if prov and prov != del_flujo.get("proveedor"):
        r = {"proveedor": prov, "modelo": c.get("modelo") or "", "origen": "nodo"}
        if c.get("credencial"):
            r["credencial"] = c["credencial"]
    else:
        r = dict(del_flujo)
        if c.get("modelo"):
            r["modelo"], r["origen"] = c["modelo"], "nodo"
        if c.get("credencial"):
            r["credencial"], r["origen"] = c["credencial"], "nodo"
    if isinstance(c.get("temperatura"), (int, float)) and not isinstance(c.get("temperatura"), bool):
        r["temperatura"] = c["temperatura"]
    if del_flujo.get("por_minuto") and r.get("proveedor") == del_flujo.get("proveedor"):
        r["por_minuto"] = del_flujo["por_minuto"]
    return r


def necesita(tipo: str, c: Dict[str, Any]) -> List[str]:
    """Lo que un nodo le pide a su modelo."""
    if tipo in TIPOS_JSON or (tipo == "ia_generar" and (c.get("esquema") or c.get("formato") == "json")):
        return ["json_estructurado"]
    if tipo == "leer_adjuntos" and c.get("vision") is True and c.get("ocr", True) is not False:
        return ["imagenes"]
    return []


_QUE = {"json_estructurado": "JSON con esquema", "imagenes": "imágenes (visión)", "pdf": "PDF", "herramientas": "herramientas",
        "voz_en_vivo": "voz en vivo"}


def problemas_de(ia: Dict[str, Any], donde: str, flujo_id: str, credenciales: Optional[Dict[str, dict]], pide: Iterable[str] = ()) -> List[str]:
    """Los problemas de una IA resuelta: proveedor, modelo, credencial (existe, tipo y alcance) y capacidades."""
    p: List[str] = []
    prov = ia.get("proveedor")
    a = PROVEEDORES.get(str(prov or ""))
    if not a:
        return [f"{donde}: proveedor de IA desconocido «{prov}» (válidos: {', '.join(PROVEEDORES)})"]
    modelo = ia.get("modelo")
    if not (isinstance(modelo, str) and modelo.strip()):
        p.append(f"{donde}: falta el modelo de {a.nombre}")
        return p
    nombre = ia.get("credencial")
    if nombre not in (None, ""):
        if not (isinstance(nombre, str) and NOMBRE_CREDENCIAL.match(nombre)):
            p.append(f"{donde}: credencial es el NOMBRE de una credencial guardada, nunca una llave")
        elif not a.tipo_credencial:
            p.append(f"{donde}: {a.nombre} no usa credencial (usa {a.autenticacion})")
        elif credenciales is not None:
            c = credenciales.get(nombre)
            if not c:
                p.append(f"{donde}: la credencial «{nombre}» no existe o este flujo no la puede usar (tiene que ser global o de este flujo)")
            elif c.get("tipo") != a.tipo_credencial:
                p.append(f"{donde}: la credencial «{nombre}» es de tipo {c.get('tipo')}; {a.nombre} necesita una {a.tipo_credencial}")
            elif c.get("alcance") not in (None, "", "global", flujo_id):
                p.append(f"{donde}: la credencial «{nombre}» es del flujo «{c.get('alcance')}»: este flujo no la puede usar")
    elif a.requiere_credencial:
        p.append(f"{donde}: {a.nombre} necesita credencial (una conexión de tipo {a.tipo_credencial}; la llave la pone una persona en Conexiones)")
    f = catalogo.ficha(a.id, modelo)
    if f and f.get("solo_voz"):
        p.append(f"{donde}: «{modelo}» es un modelo de voz en vivo; elige uno de texto")
    for cap in pide:
        if catalogo.acepta(a.id, modelo, cap) is False:
            p.append(f"{donde}: el modelo «{modelo}» de {a.nombre} no acepta {_QUE.get(cap, cap)}; elige otro modelo"
                     + (" o apaga la visión" if cap == "imagenes" else ""))
    return p


def problemas_ia(defi: Dict[str, Any], credenciales: Optional[Dict[str, dict]] = None, global_: Any = None) -> List[str]:
    """Validación de la IA de un flujo y de sus nodos. `credenciales`: {nombre: {tipo, alcance}} (None: no se revisa si existen)."""
    p: List[str] = []
    fid = str((defi or {}).get("id") or "")
    ia = (defi or {}).get("ia")
    if ia is not None:
        if not isinstance(ia, dict):
            return ["ia tiene que ser un objeto: {usar: \"global\"} o {proveedor, modelo, credencial, temperatura}"]
        if "usar" in ia and ia.get("usar") != "global":
            p.append("ia.usar solo puede ser «global»")
        if ia.get("usar") == "global" and ia.get("proveedor"):
            p.append("ia: o {usar: \"global\"} o un proveedor propio, no los dos")
        t = ia.get("temperatura")
        if t is not None and not (isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= t <= 2):
            p.append("ia.temperatura tiene que ser un número entre 0 y 2")
        pm = ia.get("por_minuto")
        if pm is not None and not (isinstance(pm, int) and not isinstance(pm, bool) and 1 <= pm <= 10000):
            p.append("ia.por_minuto tiene que ser un entero entre 1 y 10000")
        if ia.get("proveedor") or not ia.get("usar"):
            p += problemas_de(ia_del_flujo(defi, global_), "ia del flujo", fid, credenciales)
    del_flujo = ia_del_flujo(defi or {}, global_)
    for n in (defi or {}).get("nodos") or []:
        if not isinstance(n, dict):
            continue
        t, c = n.get("tipo"), n.get("config") if isinstance(n.get("config"), dict) else {}
        if t not in TIPOS_IA and t != "leer_adjuntos":
            continue
        nid = n.get("id")
        if c.get("proveedor") is not None and str(c.get("proveedor")) not in PROVEEDORES:
            p.append(f"{nid}: proveedor de IA desconocido «{c.get('proveedor')}» (válidos: {', '.join(PROVEEDORES)})")
            continue
        if t in TIPOS_IA and c.get("credencial") not in (None, "") and not (isinstance(c.get("credencial"), str) and NOMBRE_CREDENCIAL.match(c["credencial"])):
            p.append(f"{nid}: credencial es el NOMBRE de una credencial guardada, nunca una llave")
            continue
        if c.get("proveedor") and not c.get("modelo") and c.get("proveedor") != del_flujo.get("proveedor"):
            p.append(f"{nid}: con proveedor «{c['proveedor']}» hace falta también el modelo")
            continue
        efectiva = ia_del_nodo(c, del_flujo)
        pide = necesita(t, c)
        if efectiva.get("origen") == "nodo" or pide:
            p += [x for x in problemas_de(efectiva, str(nid), fid, credenciales, pide) if efectiva.get("origen") == "nodo" or "no acepta" in x]
    return p
