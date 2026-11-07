"""
Trabajo y clientes (plan 5d, fase 3) por HTTP, sin SDK: Jira Cloud (API REST v3),
Trello, Asana, Notion, HubSpot (CRM v3), Salesforce (REST + SOQL), Zoho CRM (v6)
y DocuSign (eSignature v2.1). Todo sale por `rest.pedir` (https, destino
revisado, la IP ya revisada, sin redirecciones).

Quien llama decide si se escribe: las funciones que crean, cambian o envían
solo se usan dentro de `_escribir` (escritura habilitada por una persona); sin
eso, lo que se iba a hacer queda armado y retenido. Las de lectura (buscar,
ver, listar, consultar, estado) sí salen.

Nunca direcciones de Positiva ni los buzones protegidos en lo que se escribe
(`revisar_datos` busca correos en cualquier texto del cuerpo), ni sitios de
Jira, instancias de Salesforce o cuentas de DocuSign fuera de sus dominios.
`BASES` se cambia en las pruebas (servidores locales).
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import rest
from .base import IPS_PERMITIDAS_PRUEBAS, ErrorDeNegocio, direccion_vetada, host_vetado

BASES = {
    "trello": "https://api.trello.com/1", "asana": "https://app.asana.com/api/1.0", "notion": "https://api.notion.com/v1",
    "hubspot": "https://api.hubapi.com", "zoho_cuentas": "https://accounts.{d}", "zoho_api": "https://www.zohoapis.{d}",
    "docusign_demo": "https://account-d.docusign.com", "docusign": "https://account.docusign.com",
}
NOTION_VERSION = "2022-06-28"
SALESFORCE_VERSION = "v61.0"
ZOHO_DOMINIOS = {"com": "zoho.com", "eu": "zoho.eu", "in": "zoho.in", "au": "zoho.com.au", "jp": "zoho.jp", "ca": "zohocloud.ca",
                 "sa": "zoho.sa", "uk": "zoho.uk", "cn": "zoho.com.cn"}
CORREO = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CORREO_EXACTO = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
CLAVE_JIRA = re.compile(r"^([A-Z][A-Z0-9_]{0,30}-\d{1,9}|\d{1,12})$")
ID_TRELLO = re.compile(r"^[A-Za-z0-9]{8,24}$")
GID_ASANA = re.compile(r"^\d{1,20}$")
OBJETO_SF = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")
ID_SF = re.compile(r"^[A-Za-z0-9]{15}([A-Za-z0-9]{3})?$")
MODULO_ZOHO = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,60}$")
ID_DOCUSIGN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_cache: Dict[str, Tuple[Any, float]] = {}
_cerrojo = threading.Lock()


def olvidar() -> None:
    with _cerrojo:
        _cache.clear()


# =====================================================================
#   lo común
# =====================================================================

def revisar_datos(x: Any, que: str = "los datos") -> None:
    """ErrorDeNegocio si en cualquier texto de x (anidado) hay una dirección de Positiva o un buzón protegido."""
    if isinstance(x, dict):
        for k, v in x.items():
            revisar_datos(k, que)
            revisar_datos(v, que)
    elif isinstance(x, (list, tuple)):
        for v in x:
            revisar_datos(v, que)
    elif isinstance(x, str) and "@" in x:
        for d in CORREO.findall(x):
            motivo = direccion_vetada(d)
            if motivo:
                raise ErrorDeNegocio(f"bloqueado por seguridad: {motivo} (en {que})")


def revisar_correo(d: Any, que: str) -> str:
    d = str(d or "").strip()
    if not CORREO_EXACTO.match(d):
        raise ErrorDeNegocio(f"{que}: «{d[:80]}» no es una dirección de correo")
    motivo = direccion_vetada(d)
    if motivo:
        raise ErrorDeNegocio(f"bloqueado por seguridad: {motivo}")
    return d


def _base_propia(url: Any, sufijos: Tuple[str, ...], que: str, ejemplo: str) -> str:
    """La URL base (sin «/» final) de un sitio propio del cliente, solo en los dominios del servicio."""
    u = rest.revisar_url(str(url or "").strip().rstrip("/") or "x")
    host = (u.hostname or "").lower()
    if host not in IPS_PERMITIDAS_PRUEBAS and not any(host == s or host.endswith("." + s) for s in sufijos):
        raise ErrorDeNegocio(f"{que} tiene que ser una dirección de {' o '.join(sufijos)} (p. ej. {ejemplo})")
    vetado = host_vetado(host)
    if vetado:
        raise ErrorDeNegocio(f"bloqueado por seguridad: {vetado}")
    return urllib.parse.urlunsplit((u.scheme, u.netloc, "", "", "")).rstrip("/")


def _j(r: rest.Respuesta) -> Any:
    try:
        return r.json()
    except ValueError:
        return {}


def _texto(x: Any) -> str:
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str) if x is not None else ""


def _en_cache(clave: str, fn: Callable[[], Tuple[Any, float]]) -> Any:
    with _cerrojo:
        x = _cache.get(clave)
        if x and x[1] - time.time() > 120:
            return x[0]
    valor, vence = fn()
    with _cerrojo:
        _cache[clave] = (valor, vence)
    return valor


def escapar_comillas(v: str) -> str:
    """Un valor dentro de comillas en JQL ("…") o SOQL ('…'): sin salir de ellas."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'").replace("\n", " ").replace("\r", " ")


def escapar_zoho(v: str) -> str:
    """Un valor dentro de un criterio de Zoho ((Campo:equals:valor)): paréntesis y comas escapados."""
    return re.sub(r"([(),\\])", r"\\\1", str(v)).replace("\n", " ")


# =====================================================================
#   Jira Cloud (REST v3)
# =====================================================================

def jira_base(v: dict) -> str:
    return _base_propia(v.get("sitio"), ("atlassian.net", "jira.com"), "sitio", "https://miempresa.atlassian.net")


def _jira(v: dict, metodo: str, ruta: str, timeout: float, **kw: Any) -> rest.Respuesta:
    correo = str(v.get("correo") or "").strip()
    if direccion_vetada(correo):
        raise ErrorDeNegocio(f"bloqueado por seguridad: {direccion_vetada(correo)}")
    auth = "Basic " + base64.b64encode(f"{correo}:{v.get('api_token') or ''}".encode()).decode()
    return rest.pedir(metodo, jira_base(v) + ruta, headers={"Authorization": auth, **kw.pop("headers", {})}, timeout=timeout, servicio="Jira", **kw)


def adf(texto: str) -> dict:
    """Texto plano → documento ADF de Jira (un párrafo por línea; las líneas «- » como lista)."""
    contenido: List[dict] = []
    viñetas: List[dict] = []

    def cerrar() -> None:
        if viñetas:
            contenido.append({"type": "bulletList", "content": list(viñetas)})
            viñetas.clear()
    for linea in str(texto or "").splitlines():
        if re.match(r"^\s*[-*•]\s+", linea):
            viñetas.append({"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": re.sub(r"^\s*[-*•]\s+", "", linea)}]}]})
            continue
        cerrar()
        contenido.append({"type": "paragraph", "content": [{"type": "text", "text": linea}] if linea.strip() else []})
    cerrar()
    return {"type": "doc", "version": 1, "content": contenido or [{"type": "paragraph", "content": []}]}


def adf_a_texto(n: Any) -> str:
    if isinstance(n, str):
        return n
    if not isinstance(n, dict):
        return ""
    if n.get("type") == "text":
        return str(n.get("text") or "")
    if n.get("type") in ("hardBreak",):
        return "\n"
    partes = [adf_a_texto(x) for x in n.get("content") or []]
    sep = "\n" if n.get("type") in ("doc", "bulletList", "orderedList") else ""
    t = sep.join(partes)
    return ("- " + t) if n.get("type") == "listItem" else t


def jira_issue(x: dict, base: str) -> dict:
    f = x.get("fields") or {}
    nombre = lambda k, c="name": (f.get(k) or {}).get(c, "") if isinstance(f.get(k), dict) else ""  # noqa: E731
    simple = {"clave": x.get("key", ""), "id": x.get("id", ""), "url": f"{base}/browse/{x.get('key', '')}", "resumen": f.get("summary", ""),
              "estado": nombre("status"), "tipo": nombre("issuetype"), "prioridad": nombre("priority"), "proyecto": nombre("project", "key"),
              "asignado": nombre("assignee", "displayName"), "informante": nombre("reporter", "displayName"), "creado": f.get("created", ""),
              "actualizado": f.get("updated", ""), "etiquetas": f.get("labels") or [], "descripcion": adf_a_texto(f.get("description"))[:20000]}
    otros = {k: val for k, val in f.items() if k not in ("summary", "status", "issuetype", "priority", "project", "assignee", "reporter", "created",
                                                         "updated", "labels", "description") and val not in (None, "", [], {})}
    if otros:
        simple["campos"] = otros
    return simple


def jira_buscar(v: dict, jql: str, campos: List[str], maximo: int, timeout: float) -> Tuple[List[dict], bool]:
    """La búsqueda nueva de Jira Cloud (/search/jql con nextPageToken), hasta `maximo` issues."""
    base, issues, token = jira_base(v), [], ""
    campos = campos or ["summary", "status", "issuetype", "priority", "project", "assignee", "reporter", "created", "updated", "labels", "description"]
    for _ in range(50):
        params = {"jql": jql, "fields": ",".join(campos), "maxResults": min(100, maximo - len(issues))}
        if token:
            params["nextPageToken"] = token
        d = _j(_jira(v, "GET", "/rest/api/3/search/jql", timeout, params=params))
        issues += [jira_issue(x, base) for x in d.get("issues") or []]
        token = str(d.get("nextPageToken") or "")
        if len(issues) >= maximo or not token or d.get("isLast") is True:
            break
    return issues[:maximo], bool(token) and len(issues) >= maximo


def jira_ver(v: dict, clave: str, timeout: float) -> dict:
    return jira_issue(_j(_jira(v, "GET", f"/rest/api/3/issue/{urllib.parse.quote(clave)}", timeout)), jira_base(v))


def jira_crear(v: dict, campos: dict, timeout: float) -> dict:
    revisar_datos(campos, "el issue")
    d = _j(_jira(v, "POST", "/rest/api/3/issue", timeout, json={"fields": campos}))
    return {"id": d.get("id", ""), "clave": d.get("key", ""), "url": f"{jira_base(v)}/browse/{d.get('key', '')}"}


def jira_comentar(v: dict, clave: str, texto: str, timeout: float) -> dict:
    revisar_datos(texto, "el comentario")
    d = _j(_jira(v, "POST", f"/rest/api/3/issue/{urllib.parse.quote(clave)}/comment", timeout, json={"body": adf(texto)}))
    return {"id": d.get("id", ""), "clave": clave}


def jira_cambiar_estado(v: dict, clave: str, estado: str, timeout: float) -> dict:
    """Busca la transición por su nombre o por el estado al que lleva (sin mayúsculas ni tildes) y la aplica."""
    from unicodedata import normalize

    def n(x: Any) -> str:
        return normalize("NFKD", str(x or "")).encode("ascii", "ignore").decode().strip().lower()
    ts = _j(_jira(v, "GET", f"/rest/api/3/issue/{urllib.parse.quote(clave)}/transitions", timeout)).get("transitions") or []
    t = next((x for x in ts if n(x.get("name")) == n(estado) or n((x.get("to") or {}).get("name")) == n(estado)), None)
    if not t:
        raise ErrorDeNegocio(f"{clave} no puede pasar a «{estado}» desde su estado actual; transiciones posibles: "
                             + ", ".join(f"{x.get('name')} → {(x.get('to') or {}).get('name')}" for x in ts)[:300])
    _jira(v, "POST", f"/rest/api/3/issue/{urllib.parse.quote(clave)}/transitions", timeout, json={"transition": {"id": str(t.get("id"))}})
    return {"clave": clave, "transicion": t.get("name", ""), "estado": (t.get("to") or {}).get("name", "")}


def jira_probar(v: dict, timeout: float = 15) -> str:
    d = _j(_jira(v, "GET", "/rest/api/3/myself", timeout))
    if direccion_vetada(str(d.get("emailAddress") or "")):
        raise ErrorDeNegocio("bloqueado por seguridad: la cuenta de Jira es de Positiva")
    return f"Jira de {d.get('displayName', '')} en {urllib.parse.urlsplit(jira_base(v)).hostname} (solo lectura)"


# =====================================================================
#   Trello
# =====================================================================

def _trello(v: dict, metodo: str, ruta: str, timeout: float, **kw: Any) -> Any:
    auth = f'OAuth oauth_consumer_key="{v.get("api_key") or ""}", oauth_token="{v.get("token") or ""}"'
    return _j(rest.pedir(metodo, BASES["trello"] + ruta, headers={"Authorization": auth}, timeout=timeout, servicio="Trello", **kw))


def id_trello(x: Any, que: str) -> str:
    x = str(x or "").strip()
    m = re.search(r"trello\.com/[bc]/([A-Za-z0-9]{8})", x)
    x = m.group(1) if m else x
    if not ID_TRELLO.match(x):
        raise ErrorDeNegocio(f"{que}: «{x[:40]}» no es un id de Trello (24 caracteres, o los 8 del enlace corto)")
    return x


def trello_tarjeta(x: dict) -> dict:
    return {"id": x.get("id", ""), "nombre": x.get("name", ""), "descripcion": x.get("desc", ""), "lista": x.get("idList", ""), "vence": x.get("due") or "",
            "completa": bool(x.get("dueComplete")), "url": x.get("shortUrl") or x.get("url", ""), "etiquetas": [e.get("name") or e.get("color") for e in x.get("labels") or []],
            "actividad": x.get("dateLastActivity", "")}


def trello_leer(v: dict, que: str, tablero: str, lista: str, maximo: int, timeout: float) -> List[dict]:
    if que == "tableros":
        xs = _trello(v, "GET", "/members/me/boards", timeout, params={"filter": "open", "fields": "name,url,desc,shortUrl"})
        return [{"id": x.get("id"), "nombre": x.get("name", ""), "url": x.get("shortUrl") or x.get("url", ""), "descripcion": x.get("desc", "")} for x in xs][:maximo]
    if que == "listas":
        xs = _trello(v, "GET", f"/boards/{tablero}/lists", timeout, params={"filter": "open", "fields": "name,pos"})
        return [{"id": x.get("id"), "nombre": x.get("name", ""), "tablero": tablero} for x in xs][:maximo]
    campos = {"fields": "name,desc,idList,due,dueComplete,url,shortUrl,labels,dateLastActivity", "limit": min(1000, maximo)}
    xs = _trello(v, "GET", f"/lists/{lista}/cards" if lista else f"/boards/{tablero}/cards/open", timeout, params=campos)
    return [trello_tarjeta(x) for x in xs][:maximo]


def trello_crear(v: dict, lista: str, datos: dict, timeout: float) -> dict:
    revisar_datos(datos, "la tarjeta")
    x = _trello(v, "POST", "/cards", timeout, json={"idList": lista, **datos})
    return {"id": x.get("id", ""), "url": x.get("shortUrl") or x.get("url", ""), "nombre": x.get("name", "")}


def trello_mover(v: dict, tarjeta: str, lista: str, posicion: str, timeout: float) -> dict:
    x = _trello(v, "PUT", f"/cards/{tarjeta}", timeout, json={"idList": lista, "pos": posicion})
    return {"id": x.get("id", tarjeta), "lista": x.get("idList", lista), "url": x.get("shortUrl") or x.get("url", "")}


def trello_comentar(v: dict, tarjeta: str, texto: str, timeout: float) -> dict:
    revisar_datos(texto, "el comentario")
    x = _trello(v, "POST", f"/cards/{tarjeta}/actions/comments", timeout, json={"text": texto})
    return {"id": x.get("id", ""), "tarjeta": tarjeta}


def trello_probar(v: dict, timeout: float = 15) -> str:
    x = _trello(v, "GET", "/members/me", timeout, params={"fields": "username,fullName"})
    return f"Trello de @{x.get('username', '')} ({x.get('fullName', '')}), solo lectura"


# =====================================================================
#   Asana
# =====================================================================

def _asana(v: dict, metodo: str, ruta: str, timeout: float, **kw: Any) -> Any:
    return _j(rest.pedir(metodo, BASES["asana"] + ruta, token=str(v.get("token") or ""), timeout=timeout, servicio="Asana", **kw))


def gid(x: Any, que: str) -> str:
    x = str(x or "").strip()
    m = re.search(r"app\.asana\.com/\d+/(?:\d+/)*(\d+)", x)
    x = m.group(1) if m else x
    if not GID_ASANA.match(x):
        raise ErrorDeNegocio(f"{que}: «{x[:40]}» no es un gid de Asana (solo dígitos)")
    return x


CAMPOS_ASANA = "name,notes,completed,completed_at,due_on,assignee.name,assignee.email,permalink_url,modified_at,created_at,projects.name"


def asana_tarea(x: dict) -> dict:
    return {"id": x.get("gid", ""), "nombre": x.get("name", ""), "notas": x.get("notes", ""), "completada": bool(x.get("completed")), "vence": x.get("due_on") or "",
            "asignado": (x.get("assignee") or {}).get("name", ""), "url": x.get("permalink_url", ""), "modificada": x.get("modified_at", ""),
            "proyectos": [p.get("name", "") for p in x.get("projects") or []]}


def asana_listar(v: dict, proyecto: str, asignado: str, espacio: str, pendientes: bool, maximo: int, timeout: float) -> Tuple[List[dict], bool]:
    params: Dict[str, Any] = {"opt_fields": CAMPOS_ASANA, "limit": min(100, maximo)}
    if proyecto:
        params["project"] = proyecto
    else:
        params.update(assignee=asignado, workspace=espacio)
    if pendientes:
        params["completed_since"] = "now"
    tareas: List[dict] = []
    for _ in range(50):
        d = _asana(v, "GET", "/tasks", timeout, params=params)
        tareas += [asana_tarea(x) for x in d.get("data") or []]
        sig = (d.get("next_page") or {}).get("offset")
        if not sig or len(tareas) >= maximo:
            break
        params["offset"] = sig
    return tareas[:maximo], len(tareas) > maximo or (len(tareas) >= maximo and bool(sig))


def asana_ver(v: dict, tarea: str, timeout: float) -> dict:
    return asana_tarea(_asana(v, "GET", f"/tasks/{tarea}", timeout, params={"opt_fields": CAMPOS_ASANA}).get("data") or {})


def asana_crear(v: dict, datos: dict, timeout: float) -> dict:
    revisar_datos(datos, "la tarea")
    x = _asana(v, "POST", "/tasks", timeout, json={"data": datos}).get("data") or {}
    return {"id": x.get("gid", ""), "url": x.get("permalink_url", ""), "nombre": x.get("name", "")}


def asana_completar(v: dict, tarea: str, timeout: float) -> dict:
    x = _asana(v, "PUT", f"/tasks/{tarea}", timeout, json={"data": {"completed": True}}).get("data") or {}
    return {"id": x.get("gid", tarea), "completada": bool(x.get("completed", True))}


def asana_comentar(v: dict, tarea: str, texto: str, timeout: float) -> dict:
    revisar_datos(texto, "el comentario")
    x = _asana(v, "POST", f"/tasks/{tarea}/stories", timeout, json={"data": {"text": texto}}).get("data") or {}
    return {"id": x.get("gid", ""), "tarea": tarea}


def asana_probar(v: dict, timeout: float = 15) -> str:
    x = _asana(v, "GET", "/users/me", timeout).get("data") or {}
    if direccion_vetada(str(x.get("email") or "")):
        raise ErrorDeNegocio("bloqueado por seguridad: la cuenta de Asana es de Positiva")
    espacios = ", ".join(f"{w.get('name')} ({w.get('gid')})" for w in x.get("workspaces") or [])
    return f"Asana de {x.get('name', '')} · espacios: {espacios[:200] or 'ninguno'}"


# =====================================================================
#   Notion
# =====================================================================

def _notion(v: dict, metodo: str, ruta: str, timeout: float, **kw: Any) -> Any:
    return _j(rest.pedir(metodo, BASES["notion"] + ruta, token=str(v.get("token") or ""), headers={"Notion-Version": NOTION_VERSION},
                         timeout=timeout, servicio="Notion", **kw))


def id_notion(x: Any, que: str) -> str:
    s = str(x or "").strip()
    hexa = [x.replace("-", "") for x in re.findall(r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})(?![0-9a-fA-F])", s)]
    if not hexa:
        raise ErrorDeNegocio(f"{que}: «{s[:60]}» no es un id de Notion (32 caracteres hexadecimales, o el enlace de la página)")
    h = hexa[-1].lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _rico(texto: str) -> List[dict]:
    t = str(texto or "")
    return [{"type": "text", "text": {"content": t[i:i + 2000]}} for i in range(0, len(t), 2000)] or [{"type": "text", "text": {"content": ""}}]


def _plano(rico: Any) -> str:
    return "".join(str(x.get("plain_text") or (x.get("text") or {}).get("content") or "") for x in rico or [] if isinstance(x, dict))


def valor_notion(p: dict) -> Any:
    t = p.get("type")
    x = p.get(t)
    if t in ("title", "rich_text"):
        return _plano(x)
    if t in ("select", "status"):
        return (x or {}).get("name", "")
    if t == "multi_select":
        return [s.get("name", "") for s in x or []]
    if t == "date":
        return (x or {}).get("start", "") if not (x or {}).get("end") else {"inicio": x.get("start"), "fin": x.get("end")}
    if t == "people":
        return [u.get("name") or u.get("id") for u in x or []]
    if t == "relation":
        return [r.get("id") for r in x or []]
    if t == "formula":
        return (x or {}).get((x or {}).get("type"))
    if t == "rollup":
        return (x or {}).get((x or {}).get("type"))
    if t in ("created_by", "last_edited_by"):
        return (x or {}).get("name") or (x or {}).get("id")
    if t == "files":
        return [f.get("name") for f in x or []]
    if t == "unique_id":
        return f"{(x or {}).get('prefix') or ''}{'-' if (x or {}).get('prefix') else ''}{(x or {}).get('number')}"
    return x


def notion_pagina(x: dict) -> dict:
    return {"id": x.get("id", ""), "url": x.get("url", ""), "creada": x.get("created_time", ""), "editada": x.get("last_edited_time", ""),
            "propiedades": {k: valor_notion(p) for k, p in (x.get("properties") or {}).items() if isinstance(p, dict)}}


def propiedad_notion(nombre: str, tipo: str, valor: Any) -> Any:
    """Un valor simple → el formato de Notion para ese tipo de propiedad (o el valor tal cual si ya viene en ese formato)."""
    if isinstance(valor, dict) and tipo in valor:
        return valor[tipo]
    if tipo in ("title", "rich_text"):
        return _rico(_texto(valor))
    if tipo == "number":
        if valor in ("", None):
            return None
        try:
            return float(str(valor).replace(",", ".")) if not isinstance(valor, (int, float)) else valor
        except ValueError:
            raise ErrorDeNegocio(f"la propiedad «{nombre}» es un número y llegó «{str(valor)[:40]}»") from None
    if tipo in ("select", "status"):
        return {"name": _texto(valor)[:100]} if valor not in ("", None) else None
    if tipo == "multi_select":
        xs = valor if isinstance(valor, list) else [s.strip() for s in _texto(valor).split(",") if s.strip()]
        return [{"name": _texto(s)[:100]} for s in xs]
    if tipo == "date":
        if isinstance(valor, dict):
            return {"start": valor.get("inicio") or valor.get("start"), **({"end": valor.get("fin") or valor.get("end")} if valor.get("fin") or valor.get("end") else {})}
        return {"start": _texto(valor)} if valor not in ("", None) else None
    if tipo == "checkbox":
        return valor if isinstance(valor, bool) else _texto(valor).strip().lower() in ("true", "si", "sí", "1", "x")
    if tipo in ("url", "phone_number"):
        return _texto(valor) or None
    if tipo == "email":
        return revisar_correo(valor, nombre) if valor not in ("", None) else None
    if tipo == "relation":
        xs = valor if isinstance(valor, list) else [s for s in _texto(valor).split(",") if s.strip()]
        return [{"id": id_notion(s, nombre)} for s in xs]
    if tipo == "people":
        xs = valor if isinstance(valor, list) else [s for s in _texto(valor).split(",") if s.strip()]
        return [{"id": id_notion(s, nombre)} for s in xs]
    raise ErrorDeNegocio(f"la propiedad «{nombre}» es de tipo {tipo}: pásala en el formato de Notion ({{\"{tipo}\": …}}) o quítala")


def propiedades_notion(esquema: Dict[str, dict], valores: Dict[str, Any]) -> Dict[str, Any]:
    r = {}
    sin_tildes = {k.lower(): k for k in esquema}
    for nombre, valor in valores.items():
        real = nombre if nombre in esquema else sin_tildes.get(str(nombre).lower())
        if not real:
            raise ErrorDeNegocio(f"la base de Notion no tiene la propiedad «{nombre}» (tiene: {', '.join(list(esquema)[:20])})")
        tipo = esquema[real].get("type")
        r[real] = {tipo: propiedad_notion(real, tipo, valor)}
    return r


def bloques(texto: str) -> List[dict]:
    """Texto → bloques de Notion: «# », «## », «### » títulos; «- » viñetas; «1. » numeradas; «[ ] »/«[x] » tareas; lo demás párrafos."""
    r: List[dict] = []
    for linea in str(texto or "").splitlines():
        s = linea.rstrip()
        if not s.strip():
            continue
        for patron, tipo, extra in ((r"^###\s+", "heading_3", {}), (r"^##\s+", "heading_2", {}), (r"^#\s+", "heading_1", {}),
                                    (r"^\s*\[\s\]\s+", "to_do", {"checked": False}), (r"^\s*\[[xX]\]\s+", "to_do", {"checked": True}),
                                    (r"^\s*[-*•]\s+", "bulleted_list_item", {}), (r"^\s*\d+[.)]\s+", "numbered_list_item", {})):
            if re.match(patron, s):
                r.append({"object": "block", "type": tipo, tipo: {"rich_text": _rico(re.sub(patron, "", s)), **extra}})
                break
        else:
            r.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rico(s)}})
    return r


def notion_consultar(v: dict, base: str, filtro: Optional[dict], orden: List[dict], maximo: int, timeout: float) -> Tuple[List[dict], bool]:
    paginas: List[dict] = []
    cuerpo: Dict[str, Any] = {"page_size": min(100, maximo)}
    if filtro:
        cuerpo["filter"] = filtro
    if orden:
        cuerpo["sorts"] = orden
    sigue = False
    for _ in range(50):
        d = _notion(v, "POST", f"/databases/{base}/query", timeout, json=cuerpo)
        paginas += [notion_pagina(x) for x in d.get("results") or []]
        sigue = bool(d.get("has_more")) and bool(d.get("next_cursor"))
        if not sigue or len(paginas) >= maximo:
            break
        cuerpo["start_cursor"] = d["next_cursor"]
    return paginas[:maximo], sigue or len(paginas) > maximo


def notion_esquema(v: dict, base: str, timeout: float) -> Dict[str, dict]:
    return _notion(v, "GET", f"/databases/{base}", timeout).get("properties") or {}


def notion_crear(v: dict, base: str, valores: dict, contenido: str, timeout: float) -> dict:
    revisar_datos(valores, "la página")
    revisar_datos(contenido, "el contenido")
    cuerpo: Dict[str, Any] = {"parent": {"database_id": base}, "properties": propiedades_notion(notion_esquema(v, base, timeout), valores)}
    hijos = bloques(contenido)
    if hijos:
        cuerpo["children"] = hijos[:100]
    x = _notion(v, "POST", "/pages", timeout, json=cuerpo)
    if len(hijos) > 100:
        notion_agregar(v, x.get("id", ""), hijos[100:], timeout)
    return {"id": x.get("id", ""), "url": x.get("url", ""), "bloques": len(hijos)}


def notion_actualizar(v: dict, pagina: str, valores: dict, timeout: float) -> dict:
    revisar_datos(valores, "la página")
    actual = _notion(v, "GET", f"/pages/{pagina}", timeout).get("properties") or {}
    x = _notion(v, "PATCH", f"/pages/{pagina}", timeout, json={"properties": propiedades_notion(actual, valores)})
    return {"id": x.get("id", pagina), "url": x.get("url", ""), "cambiadas": list(valores)}


def notion_agregar(v: dict, pagina: str, hijos: List[dict], timeout: float) -> dict:
    revisar_datos(hijos, "el contenido")
    for i in range(0, len(hijos), 100):
        _notion(v, "PATCH", f"/blocks/{pagina}/children", timeout, json={"children": hijos[i:i + 100]})
    return {"id": pagina, "bloques": len(hijos)}


def notion_probar(v: dict, timeout: float = 15) -> str:
    x = _notion(v, "GET", "/users/me", timeout)
    return f"integración «{x.get('name', '')}» de Notion (solo lectura)"


# =====================================================================
#   HubSpot (CRM v3)
# =====================================================================

OBJETOS_HUBSPOT = {"contactos": "contacts", "empresas": "companies", "negocios": "deals"}
PROPIEDADES_HUBSPOT = {"contacts": ["email", "firstname", "lastname", "phone", "company", "lifecyclestage"],
                       "companies": ["name", "domain", "city", "phone", "industry"],
                       "deals": ["dealname", "amount", "dealstage", "pipeline", "closedate"]}
ASOCIACION_NOTA = {"contacts": 202, "companies": 190, "deals": 214}


def _hubspot(v: dict, metodo: str, ruta: str, timeout: float, **kw: Any) -> Any:
    return _j(rest.pedir(metodo, BASES["hubspot"] + ruta, token=str(v.get("token") or ""), timeout=timeout, servicio="HubSpot", **kw))


def hubspot_registro(x: dict) -> dict:
    return {"id": x.get("id", ""), **(x.get("properties") or {}), "creado": x.get("createdAt", ""), "actualizado": x.get("updatedAt", "")}


def hubspot_buscar(v: dict, objeto: str, texto: str, filtros: List[dict], propiedades: List[str], maximo: int, timeout: float) -> Tuple[List[dict], bool]:
    cuerpo: Dict[str, Any] = {"properties": propiedades or PROPIEDADES_HUBSPOT[objeto], "limit": min(100, maximo)}
    if texto:
        cuerpo["query"] = texto
    if filtros:
        cuerpo["filterGroups"] = [{"filters": filtros}]
    r: List[dict] = []
    sig = ""
    for _ in range(50):
        d = _hubspot(v, "POST", f"/crm/v3/objects/{objeto}/search", timeout, json=cuerpo)
        r += [hubspot_registro(x) for x in d.get("results") or []]
        sig = str(((d.get("paging") or {}).get("next") or {}).get("after") or "")
        if not sig or len(r) >= maximo:
            break
        cuerpo["after"] = sig
    return r[:maximo], bool(sig) and len(r) >= maximo


def hubspot_ver(v: dict, objeto: str, ident: str, propiedades: List[str], timeout: float) -> dict:
    params: Dict[str, Any] = {"properties": ",".join(propiedades or PROPIEDADES_HUBSPOT[objeto])}
    if "@" in ident and objeto == "contacts":
        params["idProperty"] = "email"
    return hubspot_registro(_hubspot(v, "GET", f"/crm/v3/objects/{objeto}/{urllib.parse.quote(ident)}", timeout, params=params))


def hubspot_crear(v: dict, objeto: str, datos: dict, timeout: float) -> dict:
    revisar_datos(datos, "el registro")
    x = _hubspot(v, "POST", f"/crm/v3/objects/{objeto}", timeout, json={"properties": datos})
    return {"id": x.get("id", ""), "creado": x.get("createdAt", "")}


def hubspot_actualizar(v: dict, objeto: str, ident: str, datos: dict, timeout: float) -> dict:
    revisar_datos(datos, "el registro")
    params = {"idProperty": "email"} if "@" in ident and objeto == "contacts" else None
    x = _hubspot(v, "PATCH", f"/crm/v3/objects/{objeto}/{urllib.parse.quote(ident)}", timeout, json={"properties": datos}, params=params)
    return {"id": x.get("id", ident), "actualizado": x.get("updatedAt", "")}


def hubspot_nota(v: dict, objeto: str, ident: str, texto: str, momento_ms: int, timeout: float) -> dict:
    revisar_datos(texto, "la nota")
    if not ident.isdigit():
        raise ErrorDeNegocio("nota: el id del registro tiene que ser el número de HubSpot (búscalo primero con buscar o ver)")
    cuerpo = {"properties": {"hs_note_body": texto, "hs_timestamp": momento_ms},
              "associations": [{"to": {"id": ident}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": ASOCIACION_NOTA[objeto]}]}]}
    x = _hubspot(v, "POST", "/crm/v3/objects/notes", timeout, json=cuerpo)
    return {"id": x.get("id", ""), "asociada_a": ident}


def hubspot_probar(v: dict, timeout: float = 15) -> str:
    x = _hubspot(v, "GET", "/account-info/v3/details", timeout)
    return f"portal {x.get('portalId', '')} de HubSpot ({x.get('accountType', '')}), solo lectura"


# =====================================================================
#   Salesforce (REST + SOQL, OAuth de credenciales de cliente)
# =====================================================================

def sf_instancia(v: dict) -> str:
    return _base_propia(v.get("instancia"), ("salesforce.com", "force.com", "salesforce.mil", "cloudforce.com"), "instancia",
                        "https://miempresa.my.salesforce.com")


def sf_version(v: dict) -> str:
    ver = str(v.get("version") or SALESFORCE_VERSION).strip()
    if not re.fullmatch(r"v\d{2,3}\.0", ver):
        raise ErrorDeNegocio("version de la API de Salesforce: p. ej. v61.0")
    return ver


def _sf(v: dict, metodo: str, ruta: str, timeout: float, **kw: Any) -> rest.Respuesta:
    from . import oauth as c_oauth
    base = sf_instancia(v)
    token = c_oauth.cliente(f"{base}/services/oauth2/token", str(v.get("client_id") or ""), str(v.get("client_secret") or ""), "", timeout)
    return rest.pedir(metodo, base + ruta, token=token, timeout=timeout, servicio="Salesforce", **kw)


def revisar_soql(soql: str) -> str:
    s = str(soql or "").strip()
    if not re.match(r"(?is)^select\s", s):
        raise ErrorDeNegocio("la consulta de Salesforce tiene que ser un SELECT (SOQL de solo lectura)")
    if re.search(r";\s*\S", s) or s.rstrip().endswith(";"):
        raise ErrorDeNegocio("SOQL: una sola consulta, sin «;»")
    return s


def _sin_atributos(x: Any) -> Any:
    if isinstance(x, dict):
        r = {k: _sin_atributos(val) for k, val in x.items() if k != "attributes"}
        if isinstance(x.get("attributes"), dict) and x["attributes"].get("type"):
            r["_tipo"] = x["attributes"]["type"]
        return r
    if isinstance(x, list):
        return [_sin_atributos(val) for val in x]
    return x


def sf_consultar(v: dict, soql: str, maximo: int, timeout: float) -> Tuple[List[dict], int, bool]:
    ver = sf_version(v)
    d = _j(_sf(v, "GET", f"/services/data/{ver}/query", timeout, params={"q": revisar_soql(soql)}))
    filas = [_sin_atributos(x) for x in d.get("records") or []]
    total = int(d.get("totalSize") or len(filas))
    for _ in range(50):
        sig = str(d.get("nextRecordsUrl") or "")
        if d.get("done", True) or not sig or len(filas) >= maximo:
            break
        if not sig.startswith("/services/data/"):
            raise ErrorDeNegocio("Salesforce mandó una página siguiente fuera de /services/data; no se sigue")
        d = _j(_sf(v, "GET", sig, timeout))
        filas += [_sin_atributos(x) for x in d.get("records") or []]
    return filas[:maximo], total, total > min(len(filas), maximo)


def sf_ver(v: dict, objeto: str, ident: str, campos: List[str], timeout: float) -> dict:
    params = {"fields": ",".join(campos)} if campos else None
    return _sin_atributos(_j(_sf(v, "GET", f"/services/data/{sf_version(v)}/sobjects/{objeto}/{ident}", timeout, params=params)))


def sf_crear(v: dict, objeto: str, datos: dict, timeout: float) -> dict:
    revisar_datos(datos, "el registro")
    d = _j(_sf(v, "POST", f"/services/data/{sf_version(v)}/sobjects/{objeto}", timeout, json=datos))
    if d.get("success") is False:
        raise ErrorDeNegocio(f"Salesforce no creó el registro: {_texto(d.get('errors'))[:300]}")
    return {"id": d.get("id", ""), "objeto": objeto}


def sf_actualizar(v: dict, objeto: str, ident: str, datos: dict, timeout: float) -> dict:
    revisar_datos(datos, "el registro")
    _sf(v, "PATCH", f"/services/data/{sf_version(v)}/sobjects/{objeto}/{ident}", timeout, json=datos)
    return {"id": ident, "objeto": objeto, "cambiados": list(datos)}


def sf_probar(v: dict, timeout: float = 15) -> str:
    d = _j(_sf(v, "GET", f"/services/data/{sf_version(v)}/limits", timeout))
    usadas = d.get("DailyApiRequests") or {}
    extra = f" · {usadas.get('Remaining')} de {usadas.get('Max')} llamadas de API disponibles hoy" if usadas else ""
    return f"Salesforce {urllib.parse.urlsplit(sf_instancia(v)).hostname} entregó un token y respondió (API {sf_version(v)}, solo lectura){extra}"


# =====================================================================
#   Zoho CRM (v6, OAuth con refresh_token de un «Self Client»)
# =====================================================================

def _zoho_dominio(v: dict) -> str:
    r = str(v.get("region") or "com").strip().lower()
    if r not in ZOHO_DOMINIOS:
        raise ErrorDeNegocio(f"region de Zoho: {', '.join(ZOHO_DOMINIOS)}")
    return ZOHO_DOMINIOS[r]


def _zoho_token(v: dict, timeout: float) -> str:
    d = _zoho_dominio(v)
    url = BASES["zoho_cuentas"].replace("{d}", d) + "/oauth/v2/token"

    def pedir() -> Tuple[str, float]:
        form = {"grant_type": "refresh_token", "client_id": str(v.get("client_id") or ""), "client_secret": str(v.get("client_secret") or ""),
                "refresh_token": str(v.get("refresh_token") or "")}
        x = _j(rest.pedir("POST", url, data=form, timeout=timeout, servicio="el servidor de autorización de Zoho"))
        if not x.get("access_token"):
            raise ErrorDeNegocio(f"Zoho no entregó un token ({x.get('error') or 'sin detalle'}): revisa client_id, client_secret, refresh_token y la región")
        return str(x["access_token"]), time.time() + float(x.get("expires_in") or 3600)
    return _en_cache(f"zoho:{d}:{v.get('client_id')}:{hash(str(v.get('refresh_token')))}", pedir)


def _zoho(v: dict, metodo: str, ruta: str, timeout: float, **kw: Any) -> rest.Respuesta:
    base = BASES["zoho_api"].replace("{d}", _zoho_dominio(v))
    return rest.pedir(metodo, base + ruta, headers={"Authorization": f"Zoho-oauthtoken {_zoho_token(v, timeout)}"}, timeout=timeout, servicio="Zoho CRM", **kw)


def zoho_buscar(v: dict, modulo: str, criterio: Dict[str, str], maximo: int, timeout: float) -> Tuple[List[dict], bool]:
    r: List[dict] = []
    pagina, mas = 1, False
    for _ in range(50):
        resp = _zoho(v, "GET", f"/crm/v6/{modulo}/search", timeout, params={**criterio, "page": pagina, "per_page": min(200, maximo)})
        if resp.status == 204:
            break
        d = _j(resp)
        r += d.get("data") or []
        mas = bool((d.get("info") or {}).get("more_records"))
        if not mas or len(r) >= maximo:
            break
        pagina += 1
    return r[:maximo], mas or len(r) > maximo


def zoho_ver(v: dict, modulo: str, ident: str, timeout: float) -> dict:
    resp = _zoho(v, "GET", f"/crm/v6/{modulo}/{ident}", timeout)
    return ((_j(resp).get("data") or [{}])[0]) if resp.status != 204 else {}


def _zoho_resultado(d: dict, que: str) -> dict:
    x = (d.get("data") or [{}])[0]
    if str(x.get("status") or "").lower() != "success":
        raise ErrorDeNegocio(f"Zoho no {que} el registro: {x.get('code', '')} {x.get('message', '')} {_texto(x.get('details'))[:200]}".strip())
    return {"id": str((x.get("details") or {}).get("id") or ""), "estado": x.get("code", "")}


def zoho_crear(v: dict, modulo: str, datos: dict, timeout: float) -> dict:
    revisar_datos(datos, "el registro")
    return _zoho_resultado(_j(_zoho(v, "POST", f"/crm/v6/{modulo}", timeout, json={"data": [datos]})), "creó")


def zoho_actualizar(v: dict, modulo: str, ident: str, datos: dict, timeout: float) -> dict:
    revisar_datos(datos, "el registro")
    return _zoho_resultado(_j(_zoho(v, "PUT", f"/crm/v6/{modulo}/{ident}", timeout, json={"data": [datos]})), "actualizó")


def zoho_probar(v: dict, timeout: float = 15) -> str:
    u = (_j(_zoho(v, "GET", "/crm/v6/users", timeout, params={"type": "CurrentUser"})).get("users") or [{}])[0]
    if direccion_vetada(str(u.get("email") or "")):
        raise ErrorDeNegocio("bloqueado por seguridad: la cuenta de Zoho es de Positiva")
    return f"Zoho CRM ({_zoho_dominio(v)}) de {u.get('full_name', '')}, solo lectura"


# =====================================================================
#   DocuSign (eSignature REST v2.1, JWT de una aplicación con consentimiento)
# =====================================================================

def _docusign_oauth(v: dict) -> str:
    return BASES["docusign" if str(v.get("entorno") or "demo") == "produccion" else "docusign_demo"]


def _jwt(v: dict, aud: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    clave = str(v.get("private_key") or "").strip().replace("\\n", "\n")
    try:
        privada = serialization.load_pem_private_key(clave.encode(), password=None)
    except (ValueError, TypeError) as e:
        raise ErrorDeNegocio("private_key de DocuSign: pega la llave RSA privada completa (-----BEGIN RSA PRIVATE KEY----- …)") from e
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    ahora = int(time.time())
    cab = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    cuerpo = b64(json.dumps({"iss": str(v.get("integration_key") or ""), "sub": str(v.get("user_id") or ""), "aud": aud, "iat": ahora,
                             "exp": ahora + 3600, "scope": "signature impersonation"}).encode())
    firma = privada.sign(f"{cab}.{cuerpo}".encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr,call-arg]
    return f"{cab}.{cuerpo}.{b64(firma)}"


def docusign_sesion(v: dict, timeout: float) -> Tuple[str, str, dict]:
    """(token, URL base de la cuenta, cuenta) con el JWT de la aplicación; la URL base solo en *.docusign.net / *.docusign.com."""
    oauth = _docusign_oauth(v)

    def pedir() -> Tuple[Tuple[str, str, dict], float]:
        aud = urllib.parse.urlsplit(oauth).netloc
        r = rest.pedir("POST", f"{oauth}/oauth/token", data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": _jwt(v, aud)},
                       timeout=timeout, servicio="el servidor de autorización de DocuSign", errores=False)
        d = _j(r)
        if r.status >= 400 or not d.get("access_token"):
            if str(d.get("error")) == "consent_required":
                raise ErrorDeNegocio("DocuSign pide el consentimiento de la aplicación: abre una vez "
                                     f"{oauth}/oauth/auth?response_type=code&scope=signature%20impersonation&client_id={v.get('integration_key')}&redirect_uri=<la URI registrada> "
                                     "con el usuario que firma la llave y acepta")
            raise ErrorDeNegocio(f"DocuSign no entregó un token ({r.status}): {d.get('error_description') or d.get('error') or r.text[:200]}")
        token = str(d["access_token"])
        info = _j(rest.pedir("GET", f"{oauth}/oauth/userinfo", token=token, timeout=timeout, servicio="DocuSign"))
        if direccion_vetada(str(info.get("email") or "")):
            raise ErrorDeNegocio("bloqueado por seguridad: el usuario de DocuSign es de Positiva")
        cuentas = info.get("accounts") or []
        cuenta = next((c for c in cuentas if str(c.get("account_id")) == str(v.get("account_id") or "")), None) \
            or (next((c for c in cuentas if c.get("is_default")), None) if not v.get("account_id") else None)
        if not cuenta:
            raise ErrorDeNegocio(f"el usuario de DocuSign no tiene la cuenta {v.get('account_id')} (tiene: {', '.join(str(c.get('account_id')) for c in cuentas)[:200]})")
        base = _base_propia(cuenta.get("base_uri"), ("docusign.net", "docusign.com"), "la base_uri de la cuenta", "https://demo.docusign.net")
        return (token, f"{base}/restapi/v2.1/accounts/{cuenta.get('account_id')}", {"nombre": cuenta.get("account_name", ""), "usuario": info.get("name", "")}), \
            time.time() + float(d.get("expires_in") or 3600)
    return _en_cache(f"docusign:{oauth}:{v.get('integration_key')}:{v.get('user_id')}:{v.get('account_id')}", pedir)


def _ds(v: dict, metodo: str, ruta: str, timeout: float, **kw: Any) -> rest.Respuesta:
    token, base, _ = docusign_sesion(v, timeout)
    return rest.pedir(metodo, base + ruta, token=token, timeout=timeout, servicio="DocuSign", **kw)


def docusign_sobre(asunto: str, mensaje: str, documento: dict, firmantes: List[dict], copias: List[dict], ancla: str, borrador: bool) -> dict:
    """El cuerpo del sobre (sin enviarlo): revisa firmantes y copias (nunca Positiva) y el documento."""
    ext = documento["nombre"].rsplit(".", 1)[-1].lower() if "." in documento["nombre"] else "pdf"
    if ext not in ("pdf", "docx", "doc"):
        raise ErrorDeNegocio(f"DocuSign: el documento tiene que ser PDF o Word (llegó .{ext})")
    if not firmantes:
        raise ErrorDeNegocio("DocuSign: falta al menos un firmante {nombre, correo}")
    firmas, n = [], 0
    for i, f in enumerate(firmantes, 1):
        n += 1
        x: Dict[str, Any] = {"email": revisar_correo(f.get("correo"), f"firmante {i}"), "name": str(f.get("nombre") or "").strip() or f"Firmante {i}",
                             "recipientId": str(n), "routingOrder": str(f.get("orden") or i)}
        if ancla:
            x["tabs"] = {"signHereTabs": [{"anchorString": ancla, "anchorUnits": "pixels", "anchorXOffset": "0", "anchorYOffset": "0",
                                           "anchorIgnoreIfNotPresent": "false"}]}
        firmas.append(x)
    cc = []
    for i, f in enumerate(copias, 1):
        n += 1
        cc.append({"email": revisar_correo(f.get("correo"), f"copia {i}"), "name": str(f.get("nombre") or "").strip() or f"Copia {i}", "recipientId": str(n),
                   "routingOrder": str(max(int(x["routingOrder"]) for x in firmas) + 1)})
    revisar_datos([asunto, mensaje], "el sobre")
    return {"emailSubject": asunto[:100], "emailBlurb": mensaje[:10000], "status": "created" if borrador else "sent",
            "documents": [{"documentId": "1", "name": documento["nombre"], "fileExtension": ext, "documentBase64": base64.b64encode(documento["datos"]).decode()}],
            "recipients": {"signers": firmas, **({"carbonCopies": cc} if cc else {})}}


def docusign_enviar(v: dict, sobre: dict, timeout: float) -> dict:
    d = _j(_ds(v, "POST", "/envelopes", timeout, json=sobre))
    return {"sobre": d.get("envelopeId", ""), "estado": d.get("status", ""), "enviado_en": d.get("statusDateTime", "")}


def docusign_estado(v: dict, sobre: str, timeout: float) -> dict:
    e = _j(_ds(v, "GET", f"/envelopes/{sobre}", timeout))
    rs = _j(_ds(v, "GET", f"/envelopes/{sobre}/recipients", timeout))
    firmantes = [{"nombre": x.get("name", ""), "correo": x.get("email", ""), "estado": x.get("status", ""), "firmado": x.get("signedDateTime", ""),
                  "orden": x.get("routingOrder", "")} for x in rs.get("signers") or []]
    return {"sobre": sobre, "estado": e.get("status", ""), "asunto": e.get("emailSubject", ""), "enviado": e.get("sentDateTime", ""),
            "completado": e.get("completedDateTime", ""), "anulado": e.get("voidedDateTime", ""), "firmantes": firmantes,
            "firmados": sum(1 for x in firmantes if x["estado"] == "completed"), "total_firmantes": len(firmantes)}


def docusign_descargar(v: dict, sobre: str, timeout: float) -> bytes:
    return _ds(v, "GET", f"/envelopes/{sobre}/documents/combined", timeout, headers={"Accept": "application/pdf"}).content


def docusign_probar(v: dict, timeout: float = 15) -> str:
    _, base, cuenta = docusign_sesion(v, timeout)
    return f"DocuSign ({'producción' if v.get('entorno') == 'produccion' else 'demo'}) de {cuenta['usuario']}, cuenta «{cuenta['nombre']}» en " \
           f"{urllib.parse.urlsplit(base).hostname}: token recibido (no se envió nada)"
