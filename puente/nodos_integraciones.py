"""
Los nodos de integración del motor declarativo (plan, sección 5d): Google
Workspace (Gmail, Calendar, Docs, Forms), Microsoft 365 (Outlook, calendario,
OneDrive, SharePoint, Teams, Excel en línea), el conector genérico por OpenAPI,
las mejoras del nodo http (paginación, archivos que suben y descarga) y los
disparadores por evento (gmail_nuevo, outlook_nuevo, calendar_evento_proximo).

La ficha de cada tipo está en `especificacion_integraciones.py`; aquí está lo
que el motor necesita (salidas, lo que deja, lo que lee, la validación) y los
métodos que lo ejecutan. Como `nodos_producto`, este módulo no importa el motor:
el motor se enlaza con `enlazar(modulo)`.

Reglas que no cambian: toda escritura sale solo si una persona habilitó la
escritura de la conexión (`_escribir`); si no, se arma completa y queda
retenida en el detalle de la ejecución. Nunca cuentas, remitentes, destinatarios
ni invitados de Positiva; nunca sus hosts, SGDEA, metadatos ni IP internas.

Como programa: `python nodos_integraciones.py probar` (stdin {tipo, valores,
creada_por_persona}) y `python nodos_integraciones.py analizar_openapi` (stdin
{spec_url?, spec?, base_url?, red_privada?, creada_por_persona?}); una línea JSON.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import mimetypes
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conectores import google as c_google  # noqa: E402
from conectores import microsoft as c_ms  # noqa: E402
from conectores import oauth as c_oauth  # noqa: E402
from conectores import openapi as c_openapi  # noqa: E402
from conectores import rest  # noqa: E402
from conectores import sheets as c_sheets  # noqa: E402
from conectores.base import ErrorDeNegocio, ErrorTransitorio, direcciones, host_vetado, timeout_de  # noqa: E402
from especificacion_integraciones import (CON_REINTENTOS, CREDENCIALES_DE, DISPARADORES, DISPARADORES_DE_CORREO,  # noqa: E402,F401
                                          ESCRITURA, LECTURA, TIPOS)
import nodos_externos as nx  # noqa: E402  (fase 2: páginas sin API, almacenamiento, datos y mensajería transaccional)

nx.ni = sys.modules[__name__]

fd: Any = None
_reloj: Callable[[], float] = time.time
ZONA = "America/Bogota"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
LISTAS = {"gmail_buscar", "calendar_eventos", "forms_respuestas", "outlook_buscar", "outlook_calendario_eventos", "onedrive_listar"}
COMUNES = {"entradas", "salida", "reintentos", "deshabilitado", "continuar_si_falla", "timeout_s", "proveedor", "modelo", "temperatura"}
_specs: Dict[str, dict] = {}


def enlazar(modulo: Any) -> None:
    global fd
    fd = modulo


# =====================================================================
#   lo que el motor necesita saber de cada tipo
# =====================================================================

def salidas(t: str, c: dict) -> Tuple[List[str], List[str]]:
    if t in nx.TIPOS:
        return nx.salidas(t, c)
    if t in LISTAS:
        return ["ok", "vacio"], []
    if t == "calendar_disponibilidad":
        return ["libre", "ocupado"], []
    if t == "api_operacion":
        return ["ok", "error_http"], []
    if (t == "sharepoint_archivos" and (c.get("operacion") or "listar") == "listar") or \
            (t in ("sharepoint_lista", "excel_en_linea") and (c.get("operacion") or "leer") == "leer"):
        return ["ok", "vacio"], []
    return ["ok"], []


def produce(t: str, c: dict, nid: str) -> Set[str]:
    if t in nx.TIPOS:
        return nx.produce(t, c, nid)
    if t in DISPARADORES:
        return {"entrada"}
    r = {c.get("salida") or nid}
    if t == "api_operacion":
        r |= {m.get("variable") for m in c.get("mapeo") or [] if isinstance(m, dict) and m.get("variable")}
    return {x for x in r if isinstance(x, str)}


def referencias(t: str, c: dict, plantillas_en: Callable, ref_de_fuente: Callable) -> List[Tuple[str, Set[str]]]:
    if t in nx.TIPOS:
        return nx.referencias(t, c, plantillas_en, ref_de_fuente)
    if t in DISPARADORES:
        return []
    refs: List[Tuple[str, Set[str]]] = [(r, set()) for r in plantillas_en(c)]
    for k in ("adjuntos", "archivos") + (("archivo",) if t == "onedrive_subir" or (t == "sharepoint_archivos" and c.get("operacion") == "subir") else ()):
        refs += [(r, set()) for r in fd.np_.refs_archivos(c.get(k), ref_de_fuente)] if k in c else []
    for k in ("de", "filas"):
        refs += [(r, set()) for r in ref_de_fuente(c.get(k))]
    for m in c.get("mapeo") or [] if t == "api_operacion" else []:
        if isinstance(m, dict):
            refs += [(r, set()) for r in fd._plantillas_todas(m.get("defecto"))]
    for e in c.get("entradas") or []:
        if isinstance(e, str):
            refs.append((fd._ref_limpia(e), set()))
    return refs


def _esquema(t: str) -> dict:
    import nodos_declarativos as nd
    return nd.TIPOS[t]["config"]


def _vacio(x: Any) -> bool:
    return x is None or (isinstance(x, str) and not x.strip()) or (isinstance(x, (list, dict)) and not x)


def _revisar(t: str, c: dict, esq: dict, q: Callable[[str], None], prefijo: str = "") -> None:
    """Lo que dice el esquema: obligatorios, tipos, rangos y opciones (igual que el zod del servidor)."""
    props = esq.get("properties") or {}
    for k in esq.get("required") or []:
        if _vacio(c.get(k)):
            d = (props.get(k) or {}).get("description") or ""
            q(f"{t} necesita {prefijo}{k}" + (f" ({d[:90]})" if d else ""))
    for k, v in c.items():
        p = props.get(k)
        if p is None or (not prefijo and k in COMUNES) or v is None:
            continue
        nombre = prefijo + k
        tipo = p.get("type")
        if "enum" in p:
            if v not in p["enum"]:
                q(f"{nombre} «{v}» (válidos: {', '.join(map(str, p['enum']))})")
        elif tipo == "string":
            if not isinstance(v, str):
                q(f"{nombre} tiene que ser texto")
            elif p.get("format") == "credencial" and v and not fd.NOMBRE_CREDENCIAL.match(v):
                q(f"{nombre} es el NOMBRE de una credencial guardada (minúsculas, números, - _ .), nunca un valor ni una clave")
        elif tipo == "integer":
            lo, hi = p.get("minimum", -10**12), p.get("maximum", 10**12)
            if not fd._entero(v, lo, hi):
                q(f"{nombre} tiene que ser un entero entre {lo} y {hi}")
        elif tipo == "boolean":
            if not isinstance(v, bool):
                q(f"{nombre} tiene que ser true o false")
        elif tipo == "array":
            if not isinstance(v, list):
                q(f"{nombre} tiene que ser una lista")
            elif (p.get("items") or {}).get("type") == "string" and not all(isinstance(x, str) and x.strip() for x in v):
                q(f"{nombre} tiene que ser una lista de textos")
        elif tipo == "object" and p.get("properties"):
            if not isinstance(v, dict):
                q(f"{nombre} tiene que ser un objeto {{{', '.join(p['properties'])}}}")
            else:
                _revisar(t, v, p, q, f"{nombre}.")
        elif tipo == "object":
            fd._validar_mapa_plantillas(c, k, lambda m, n=nombre: q(m.replace(k, n, 1)))


def _textos_o_lista(c: dict, k: str, q: Callable[[str], None]) -> None:
    v = c.get(k)
    if v is not None and not (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v))):
        q(f"{k} tiene que ser un texto (separado por comas) o una lista de textos")


def validar_paginacion(pg: Any, q: Callable[[str], None]) -> None:
    if not isinstance(pg, dict):
        return
    t = pg.get("tipo")
    if t == "cursor" and (_vacio(pg.get("parametro")) or _vacio(pg.get("ruta_cursor"))):
        q("paginacion cursor necesita parametro (el de la consulta) y ruta_cursor (dónde viene el siguiente)")
    if t == "pagina" and _vacio(pg.get("parametro")):
        q("paginacion pagina necesita parametro (p. ej. «page»)")
    for k in ("ruta_cursor", "ruta_siguiente", "ruta_items"):
        if isinstance(pg.get(k), str) and pg[k].strip() and not fd.segmentos(pg[k]):
            q(f"paginacion.{k} «{pg[k]}» no es una ruta válida")


def validar_http(c: dict, q: Callable[[str], None]) -> None:
    """Lo nuevo del nodo http: paginación (solo GET), archivos que suben (POST, PUT, PATCH) y descarga."""
    import nodos_declarativos as nd
    metodo = c.get("metodo", "GET")
    props = nd.TIPOS["http"]["config"]["properties"]
    _revisar("http", {k: c[k] for k in ("paginacion", "modo_archivos", "campo_archivo", "descargar", "nombre_archivo") if k in c},
             {"properties": {k: props[k] for k in ("paginacion", "modo_archivos", "campo_archivo", "descargar", "nombre_archivo")}}, q)
    if "paginacion" in c:
        if metodo != "GET":
            q("paginacion solo aplica a GET")
        validar_paginacion(c["paginacion"], q)
    if "archivos" in c:
        if metodo not in ("POST", "PUT", "PATCH"):
            q("archivos solo se suben con POST, PUT o PATCH")
        fd.np_.problemas_archivos(c, "archivos", q)


def validar(t: str, c: dict, q: Callable[[str], None]) -> None:
    _revisar(t, c, _esquema(t), q)
    if t in nx.TIPOS:
        return nx.validar(t, c, q)
    for k in ("adjuntos", "archivos"):
        fd.np_.problemas_archivos(c, k, q)
    for k in ("asistentes", "agregar_asistentes", "calendarios"):
        _textos_o_lista(c, k, q)
    if t == "gmail_etiquetar" and not (c.get("agregar") or c.get("quitar") or c.get("marcar_leido") is True or c.get("archivar") is True):
        q("gmail_etiquetar necesita agregar, quitar, marcar_leido o archivar (algo que hacer)")
    elif t == "calendar_actualizar" and not (any(not _vacio(c.get(k)) for k in ("titulo", "inicio", "fin", "duracion_min", "descripcion", "lugar",
                                                                                   "asistentes", "agregar_asistentes", "con_meet")) or c.get("cancelar") is True):
        q("calendar_actualizar necesita algo que cambiar (titulo, inicio, fin, descripcion, lugar, asistentes…) o cancelar: true")
    elif t == "docs_crear_desde_plantilla":
        if c.get("exportar") == "ninguno" and c.get("en_drive") is not True:
            q("exportar «ninguno» solo sirve con en_drive: true (si no, no queda ningún documento)")
        if c.get("carpeta") and c.get("en_drive") is not True:
            q("carpeta solo aplica con en_drive: true")
    elif t == "outlook_categorizar" and not (c.get("categorias") or c.get("marcar_leido") is True or c.get("importancia")):
        q("outlook_categorizar necesita categorias, marcar_leido o importancia")
    elif t in ("onedrive_subir",) or (t == "sharepoint_archivos" and c.get("operacion") == "subir"):
        if not _vacio(c.get("archivo")):
            if t == "onedrive_subir":
                fd.np_.problemas_archivos(c, "archivo", q)
            if c.get("contenido") or c.get("de"):
                q(f"{t}: usa archivo (un archivo tal cual) o contenido/de (un texto), no los dos")
        else:
            if _vacio(c.get("nombre")):
                q(f"{t} necesita archivo (p. ej. «{{{{documento}}}}») o nombre con contenido o de")
            if not (c.get("contenido") or c.get("de")):
                q(f"{t} necesita archivo, contenido (plantilla) o de (variable)")
    elif t == "sharepoint_archivos" and c.get("operacion") == "leer" and _vacio(c.get("archivo")):
        q("sharepoint_archivos con operacion leer necesita archivo (ruta o id)")
    elif t == "sharepoint_lista" and c.get("operacion") == "agregar" and _vacio(c.get("campos")):
        q("sharepoint_lista con operacion agregar necesita campos {columna: plantilla}")
    elif t == "teams_mensaje":
        if (c.get("destino") or "canal") == "canal" and (_vacio(c.get("equipo")) or _vacio(c.get("canal"))):
            q("teams_mensaje a un canal necesita equipo y canal (sus ids)")
        if c.get("destino") == "chat" and _vacio(c.get("chat")):
            q("teams_mensaje a un chat necesita chat (su id)")
    elif t == "excel_en_linea":
        op = c.get("operacion") or "leer"
        if _vacio(c.get("tabla")) and _vacio(c.get("hoja")):
            q("excel_en_linea necesita tabla (el nombre de la tabla) o hoja (con rango)")
        if op == "anexar" and (_vacio(c.get("tabla")) or (_vacio(c.get("filas")) and _vacio(c.get("fila")))):
            q("excel_en_linea anexar necesita tabla y filas (una variable) o fila {columna: plantilla}")
        if op == "escribir" and (_vacio(c.get("hoja")) or _vacio(c.get("rango")) or _vacio(c.get("filas"))):
            q("excel_en_linea escribir necesita hoja, rango y filas")
    elif t == "api_operacion":
        validar_paginacion(c.get("paginacion"), q)
        if "mapeo" in c:
            fd._validar_http({"url": "https://x.co", "mapeo": c["mapeo"]}, q)


# =====================================================================
#   utilidades
# =====================================================================

def _lista_correos(v: Any) -> List[str]:
    if isinstance(v, list):
        r = []
        for x in v:
            r += direcciones(x)
        return r
    return direcciones(v)


def _fecha(valor: Any, zona: str, defecto: str = "ahora") -> datetime.datetime:
    from zoneinfo import ZoneInfo
    z = ZoneInfo(zona or ZONA)
    ahora = datetime.datetime.fromtimestamp(_reloj(), z)
    v = (str(valor).strip() if valor not in (None, "") else defecto).lower()
    hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    if v in ("", "ahora"):
        return ahora
    if v == "hoy":
        return hoy
    if v in ("mañana", "manana"):
        return hoy + datetime.timedelta(days=1)
    if v == "ayer":
        return hoy - datetime.timedelta(days=1)
    return fd._a_fecha(str(valor).strip(), zona or ZONA)


def _iso(d: datetime.datetime) -> str:
    return d.isoformat(timespec="seconds")


def _siguiente(pg: dict, url: str, datos: Any, encabezados: Dict[str, str], numero: int) -> Optional[str]:
    """La URL de la página siguiente (o None si no hay)."""
    t = pg.get("tipo")
    u = urllib.parse.urlsplit(url)
    consulta = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    if t == "cursor":
        cursor = fd.navegar(datos, fd.segmentos(pg.get("ruta_cursor")) or []) if isinstance(datos, (dict, list)) else None
        if cursor in (None, ""):
            return None
        consulta[str(pg["parametro"])] = str(cursor)
    elif t == "pagina":
        if not _items(pg, datos):
            return None
        consulta[str(pg["parametro"])] = str(int(pg.get("inicio", 1)) + numero)
    else:
        sig = ""
        if pg.get("ruta_siguiente"):
            sig = str(fd.navegar(datos, fd.segmentos(pg["ruta_siguiente"]) or []) or "")
        if not sig:
            m = re.search(r'<([^>]+)>\s*;\s*rel="?next"?', encabezados.get("link", ""))
            sig = m.group(1) if m else ""
        if not sig:
            return None
        nuevo = urllib.parse.urljoin(url, sig)
        if (urllib.parse.urlsplit(nuevo).hostname or "").lower() != (u.hostname or "").lower():
            raise ErrorDeNegocio(f"la página siguiente va a otro host ({urllib.parse.urlsplit(nuevo).hostname}); no se sigue")
        return nuevo
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(consulta), ""))


def _items(pg: dict, datos: Any) -> List[Any]:
    if pg.get("ruta_items"):
        v = fd.navegar(datos, fd.segmentos(pg["ruta_items"]) or []) if isinstance(datos, (dict, list)) else None
    else:
        v = datos
    return v if isinstance(v, list) else []


def _nombre_de(url: str, encabezados: Dict[str, str], tipo: str) -> str:
    m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)", encabezados.get("content-disposition", ""), re.I)
    if m:
        return urllib.parse.unquote(m.group(1) or m.group(2))
    base = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    if base and "." in base:
        return urllib.parse.unquote(base)
    return (base or "descarga") + (mimetypes.guess_extension(tipo.split(";")[0].strip()) or "")


# =====================================================================
#   los métodos (Ejecucion hereda de aquí)
# =====================================================================

class NodosIntegraciones(nx.NodosExternos):
    # ----------------------------------------------------------- conexiones
    def _cfg(self, n: dict) -> dict:
        return n.get("config") or {}

    def _sal(self, n: dict) -> str:
        return self._cfg(n).get("salida") or n["id"]

    def _google(self, n: dict) -> Tuple[c_google.Google, dict]:
        c = self._cfg(n)
        cred = self.cred(c.get("credencial"), CREDENCIALES_DE[n["tipo"]])
        if cred["tipo"] == "google_oauth":
            c_oauth.revisar_cuenta(cred["valores"])
        t = timeout_de(c)
        return c_google.Google(lambda alcances: c_oauth.google(cred, alcances, t), t), cred

    def _graph(self, n: dict) -> Tuple[c_ms.Graph, dict]:
        c = self._cfg(n)
        cred = self.cred(c.get("credencial"), CREDENCIALES_DE[n["tipo"]])
        v = cred["valores"]
        if cred["tipo"] == "microsoft_oauth":
            c_oauth.revisar_cuenta(v)
            base = "/me"
        elif n["tipo"] in ("sharepoint_archivos", "sharepoint_lista") and not v.get("buzon"):
            base = "/sin-usuario"
        else:
            base = f"/users/{urllib.parse.quote(fd.c_correo._buzon_graph(v))}"
        t = timeout_de(c)
        return c_ms.Graph(lambda: c_oauth.microsoft(cred, t), base, t), cred

    def _r(self, n: dict, fn: Callable[[], Any], que: str) -> Any:
        return self._con_reintentos(n, fn, que)

    def _fichas_adjuntos(self, m: dict, origen: str) -> List[dict]:
        r = []
        for a in m.get("adjuntos") or []:
            datos = a.pop("_datos", None)
            if datos is None:
                r.append({"nombre": a.get("nombre"), "tipo": a.get("tipo"), "tamano": a.get("tamano"), "sin_bajar": True})
            else:
                r.append(self._guardar_archivo(a.get("nombre") or "adjunto", datos, a.get("tipo") or mimetypes.guess_type(a.get("nombre") or "")[0]
                                               or "application/octet-stream", origen))
        return r

    def _retener_vetado(self, n: dict, sistema: str, operacion: str, destino: str, detalle: dict, e: Exception) -> None:
        fd.evento("escritura", sistema=sistema, operacion=operacion, destino=destino, bloqueada=True, simulada=True, detalle=detalle, nodo=n["id"],
                  motivo=str(e), vetado=True)

    def _correo_saliente(self, n: dict, tipo_cred: str) -> Tuple[dict, List[dict]]:
        c = self._cfg(n)
        m = {k: self.render(c.get(k) or "").strip() for k in ("para", "cc", "cco", "asunto")}
        m["cuerpo"] = self.render(c.get("cuerpo") or "")
        if c.get("html"):
            m["html"] = self.render(c["html"])
        m["adjuntos"], resumen = self._para_envio(c.get("adjuntos"), "el correo")
        return m, resumen

    def _revisar_correo(self, n: dict, sistema: str, operacion: str, m: dict, resumen: List[dict], graph: bool, exigir_para: bool = True) -> None:
        try:
            if exigir_para or m.get("para"):
                fd.c_correo.revisar_destinatarios(m)
            fd.c_correo.revisar_adjuntos(m.get("adjuntos") or [], "microsoft_graph" if graph else "")
        except ErrorDeNegocio as e:
            self._retener_vetado(n, sistema, operacion, m.get("para", ""), {**{k: m.get(k) for k in ("para", "cc", "asunto")}, "adjuntos": resumen}, e)
            raise

    @staticmethod
    def _detalle_correo(m: dict, resumen: List[dict], **extra: Any) -> dict:
        return {**{k: m.get(k, "") for k in ("para", "cc", "cco", "asunto", "cuerpo")}, **({"html": m["html"][:20000]} if m.get("html") else {}),
                **({"adjuntos": resumen, "adjuntos_bytes": sum(a["tamano"] for a in resumen)} if resumen else {}), **extra}

    def _guardar_salida(self, n: dict, r: dict, claves: Iterable[str], **extra: Any) -> None:
        self.vars[self._sal(n)] = {**{k: r.get(k) for k in claves if k in r}, "bloqueado": r["bloqueado"], "motivo": r.get("motivo", ""), **extra}

    # ======================================================== disparadores
    def gmail_nuevo(self, n: dict) -> str:
        self.entrada_correo(n)
        self.vars["entrada"] = self.entrada if isinstance(self.entrada, dict) else {}
        return "ok"

    def outlook_nuevo(self, n: dict) -> str:
        return self.gmail_nuevo(n)

    def calendar_evento_proximo(self, n: dict) -> str:
        self.vars["entrada"] = self.entrada if isinstance(self.entrada, dict) else {}
        ev = self.vars["entrada"].get("evento") or {}
        fd._paso(n["id"], f"Evento próximo: {ev.get('titulo') or '(sin título)'}", "info", f"{ev.get('inicio', '')} · {ev.get('lugar', '')}")
        return "ok"

    # =============================================================== Gmail
    def gmail_buscar(self, n: dict) -> str:
        c = self._cfg(n)
        g, _ = self._google(n)
        ids = self._r(n, lambda: g.buscar(self.render(c.get("consulta") or ""), self.render(c.get("etiqueta") or ""), int(c.get("max") or 10)), "Gmail")
        correos = []
        for mid in ids:
            m = self._r(n, lambda mid=mid: g.mensaje(mid, c.get("incluir_adjuntos", True) is not False), "Gmail")
            m["adjuntos"] = self._fichas_adjuntos(m, "gmail")
            correos.append(m)
        self.vars[self._sal(n)] = correos
        fd._paso(n["id"], f"{len(correos)} correo(s) de Gmail", "ok" if correos else "info",
                 "\n".join(f"· {x['fecha'][:16]} {x['remitente_correo']}: {x['asunto']}" for x in correos[:15]), total=len(correos))
        return "ok" if correos else "vacio"

    def gmail_enviar(self, n: dict) -> str:
        g, cred = self._google(n)
        cuenta = c_oauth.revisar_cuenta(cred["valores"])
        m, resumen = self._correo_saliente(n, "gmail")
        self._revisar_correo(n, "gmail", "enviar correo", m, resumen, False)
        detalle = self._detalle_correo(m, resumen, remitente=cuenta)
        r = self._escribir(n, "gmail", "enviar correo", m["para"], detalle, cred,
                           lambda: g.enviar(fd.c_correo._armar_mime(cuenta, m).as_bytes()))
        self.mensajes.append({**{k: detalle.get(k) for k in ("para", "cc", "asunto", "cuerpo", "adjuntos")}, "nodo": n["id"], "operacion": "gmail_enviar",
                              "enviado": not r["bloqueado"], "bloqueado": r["bloqueado"]})
        self._guardar_salida(n, r, ("id", "hilo", "para", "asunto", "adjuntos"), enviado=not r["bloqueado"])
        return "ok"

    def gmail_responder(self, n: dict) -> str:
        c = self._cfg(n)
        g, cred = self._google(n)
        cuenta = c_oauth.revisar_cuenta(cred["valores"])
        mid = self.render(c.get("mensaje") or "").strip() or str((self.msg or {}).get("_gmail_id") or "")
        if not mid:
            raise ErrorDeNegocio("gmail_responder necesita mensaje (el id de Gmail, p. ej. «{{correo_i.id}}») o que la ejecución arranque con gmail_nuevo")
        o = self._r(n, lambda: g.mensaje(mid, False), "Gmail")
        m, resumen = self._correo_saliente(n, "gmail")
        m["para"] = m["para"] or o.get("responder_a") or o.get("remitente_correo") or ""
        if c.get("todos") is True and not m["cc"]:
            propios = {cuenta.lower(), *[x.lower() for x in direcciones(m["para"])]}
            m["cc"] = ", ".join(x for x in direcciones(o.get("para")) + direcciones(o.get("cc")) if x.lower() not in propios)
        asunto = o.get("asunto") or ""
        m["asunto"] = m["asunto"] or (asunto if re.match(r"^\s*(re|rv|fw|fwd)\s*:", asunto, re.I) else f"Re: {asunto}".strip())
        if c.get("incluir_original", True) is not False and o.get("cuerpo"):
            m["cuerpo"] += f"\n\nEl {o.get('fecha', '')}, {o.get('remitente', '')} escribió:\n" + "\n".join(f"> {x}" for x in o["cuerpo"].splitlines()[:300])
        m["en_respuesta_a"], m["referencias"] = o.get("message_id") or "", o.get("referencias") or ""
        self._revisar_correo(n, "gmail", "responder correo", m, resumen, False)
        detalle = self._detalle_correo(m, resumen, remitente=cuenta, en_respuesta_a=o.get("message_id"), hilo=o.get("hilo"), mismo_hilo=True)
        r = self._escribir(n, "gmail", "responder correo", m["para"], detalle, cred,
                           lambda: g.enviar(fd.c_correo._armar_mime(cuenta, m).as_bytes(), o.get("hilo") or ""))
        self.mensajes.append({**{k: detalle.get(k) for k in ("para", "cc", "asunto", "cuerpo", "adjuntos")}, "nodo": n["id"], "operacion": "gmail_responder",
                              "enviado": not r["bloqueado"], "bloqueado": r["bloqueado"]})
        self._guardar_salida(n, r, ("id", "hilo", "para", "asunto", "adjuntos", "en_respuesta_a"), enviado=not r["bloqueado"])
        return "ok"

    def gmail_etiquetar(self, n: dict) -> str:
        c = self._cfg(n)
        g, cred = self._google(n)
        mid = self.render(c.get("mensaje") or "").strip() or str((self.msg or {}).get("_gmail_id") or "")
        if not mid:
            raise ErrorDeNegocio("gmail_etiquetar necesita mensaje (el id de Gmail) o que la ejecución arranque con gmail_nuevo")
        agregar = [self.render(x).strip() for x in c.get("agregar") or [] if self.render(x).strip()]
        quitar = [self.render(x).strip() for x in c.get("quitar") or [] if self.render(x).strip()]
        if c.get("marcar_leido") is True:
            quitar.append("UNREAD")
        if c.get("archivar") is True:
            quitar.append("INBOX")
        crear = c.get("crear_si_falta", True) is not False

        def hacer() -> dict:
            return g.modificar(mid, [x for x in (g.id_etiqueta(e, crear) for e in agregar) if x], [x for x in (g.id_etiqueta(e) for e in quitar) if x])

        r = self._escribir(n, "gmail", "etiquetar correo", f"gmail:{mid}", {"mensaje": mid, "agregar": agregar, "quitar": quitar}, cred, hacer)
        self._guardar_salida(n, r, ("etiquetas",), agregar=agregar, quitar=quitar)
        return "ok"

    # ============================================================ Calendar
    def _rango(self, c: dict) -> Tuple[datetime.datetime, datetime.datetime, str]:
        zona = c.get("zona") or ZONA
        d = _fecha(self.render(c.get("desde") or ""), zona, "ahora")
        h = _fecha(self.render(c["hasta"]), zona) if c.get("hasta") and self.render(c["hasta"]).strip() else d + datetime.timedelta(days=int(c.get("dias") or 7))
        if h <= d:
            raise ErrorDeNegocio("hasta tiene que ser después de desde")
        return d, h, zona

    def calendar_eventos(self, n: dict) -> str:
        c = self._cfg(n)
        g, _ = self._google(n)
        d, h, zona = self._rango(c)
        ev = self._r(n, lambda: g.eventos(self.render(c.get("calendario") or "primary"), _iso(d), _iso(h), self.render(c.get("consulta") or ""),
                                          int(c.get("max") or 50), zona), "Google Calendar")
        self.vars[self._sal(n)] = ev
        fd._paso(n["id"], f"{len(ev)} evento(s)", "ok" if ev else "info", "\n".join(f"· {e['inicio'][:16]} {e['titulo']}" for e in ev[:15]), total=len(ev))
        return "ok" if ev else "vacio"

    def _horario(self, c: dict, zona: str) -> Tuple[datetime.datetime, datetime.datetime, bool]:
        todo = c.get("todo_el_dia") is True
        ini = _fecha(self.render(c.get("inicio") or ""), zona)
        if c.get("fin") and self.render(c["fin"]).strip():
            fin = _fecha(self.render(c["fin"]), zona)
        else:
            fin = ini + (datetime.timedelta(days=1) if todo else datetime.timedelta(minutes=int(c.get("duracion_min") or 30)))
        if fin <= ini:
            raise ErrorDeNegocio("el fin del evento tiene que ser después del inicio")
        return ini, fin, todo

    def _invitados(self, v: Any) -> List[str]:
        if isinstance(v, list):
            return _lista_correos([self.render(x) for x in v])
        return _lista_correos(self.render(v or ""))

    def calendar_crear(self, n: dict) -> str:
        c = self._cfg(n)
        g, cred = self._google(n)
        zona = c.get("zona") or ZONA
        ini, fin, todo = self._horario(c, zona)
        asistentes = self._invitados(c.get("asistentes"))
        titulo, cal = self.render(c.get("titulo") or ""), self.render(c.get("calendario") or "primary")
        try:
            cuerpo = c_google.cuerpo_evento(titulo, _iso(ini), _iso(fin), zona, todo, self.render(c.get("descripcion") or ""),
                                            self.render(c.get("lugar") or ""), asistentes, c.get("con_meet") is True, c.get("recordatorio_min"))
        except ErrorDeNegocio as e:
            self._retener_vetado(n, "google_calendar", "crear evento", cal, {"titulo": titulo, "asistentes": asistentes}, e)
            raise
        notificar = c.get("notificar", True) is not False
        detalle = {"calendario": cal, "titulo": titulo, "inicio": _iso(ini), "fin": _iso(fin), "todo_el_dia": todo, "asistentes": asistentes,
                   "con_meet": c.get("con_meet") is True, "notificar": notificar, "evento": cuerpo}
        r = self._escribir(n, "google_calendar", "crear evento", f"calendar:{cal}", detalle, cred, lambda: g.crear_evento(cal, cuerpo, notificar))
        self._guardar_salida(n, r, ("id", "enlace", "meet"), inicio=_iso(ini), fin=_iso(fin), titulo=titulo)
        return "ok"

    def calendar_actualizar(self, n: dict) -> str:
        c = self._cfg(n)
        g, cred = self._google(n)
        zona = c.get("zona") or ZONA
        cal, eid = self.render(c.get("calendario") or "primary"), self.render(c.get("evento") or "").strip()
        cambios: Dict[str, Any] = {}
        if c.get("cancelar") is True:
            cambios["status"] = "cancelled"
        else:
            for k, g_k in (("titulo", "summary"), ("descripcion", "description"), ("lugar", "location")):
                if c.get(k) not in (None, ""):
                    cambios[g_k] = self.render(c[k])
            actual: Optional[dict] = None
            if c.get("inicio"):
                ini = _fecha(self.render(c["inicio"]), zona)
                if c.get("fin"):
                    fin = _fecha(self.render(c["fin"]), zona)
                elif c.get("duracion_min"):
                    fin = ini + datetime.timedelta(minutes=int(c["duracion_min"]))
                else:  # conserva la duración que tenía
                    actual = self._r(n, lambda: g.obtener_evento(cal, eid), "Google Calendar")
                    a = c_google.evento(actual)
                    try:
                        dur = _fecha(a["fin"], zona) - _fecha(a["inicio"], zona)
                    except ErrorDeNegocio:
                        dur = datetime.timedelta(minutes=30)
                    fin = ini + dur
                cambios["start"], cambios["end"] = {"dateTime": _iso(ini), "timeZone": zona}, {"dateTime": _iso(fin), "timeZone": zona}
            invitados = self._invitados(c["asistentes"]) if c.get("asistentes") not in (None, "", []) else None
            if c.get("agregar_asistentes") not in (None, "", []):
                actual = actual or self._r(n, lambda: g.obtener_evento(cal, eid), "Google Calendar")
                previos = [a.get("email") for a in actual.get("attendees") or [] if a.get("email")]
                invitados = list(dict.fromkeys((invitados if invitados is not None else previos) + self._invitados(c["agregar_asistentes"])))
            if invitados is not None:
                c_google.revisar_direcciones(invitados, "invitado")
                cambios["attendees"] = [{"email": x} for x in invitados]
            if c.get("con_meet") is True:
                cambios["conferenceData"] = {"createRequest": {"requestId": hashlib.sha1(f"{eid}{_reloj()}".encode()).hexdigest()[:20],
                                                               "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
        notificar = c.get("notificar", True) is not False
        r = self._escribir(n, "google_calendar", "cancelar evento" if c.get("cancelar") is True else "actualizar evento", f"calendar:{cal}/{eid}",
                           {"calendario": cal, "evento": eid, "cambios": cambios, "notificar": notificar}, cred,
                           lambda: g.actualizar_evento(cal, eid, cambios, notificar))
        self._guardar_salida(n, r, ("id", "enlace"), cambios=list(cambios))
        return "ok"

    def calendar_disponibilidad(self, n: dict) -> str:
        c = self._cfg(n)
        g, _ = self._google(n)
        zona = c.get("zona") or ZONA
        cals = c.get("calendarios")
        cals = [self.render(x).strip() for x in cals] if isinstance(cals, list) else [x.strip() for x in self.render(cals or "primary").split(",")]
        cals = [x for x in cals if x] or ["primary"]
        c_google.revisar_direcciones([x for x in cals if "@" in x], "calendario")
        d = _fecha(self.render(c.get("desde") or ""), zona)
        dur = datetime.timedelta(minutes=int(c["duracion_min"])) if c.get("duracion_min") else None
        if c.get("hasta") and self.render(c["hasta"]).strip():
            h = _fecha(self.render(c["hasta"]), zona)
        else:
            h = d + (dur if dur else datetime.timedelta(days=5))
        ocupado = self._r(n, lambda: g.ocupado(cals, _iso(d), _iso(h), zona), "Google Calendar")
        bloques = sorted((_fecha(b["inicio"], zona), _fecha(b["fin"], zona)) for v in ocupado.values() for b in v)
        franjas = self._franjas(d, h, bloques, dur, c, zona) if dur else []
        libre = not bloques
        self.vars[self._sal(n)] = {"libre": libre, "ocupado": ocupado, "franjas": franjas, "desde": _iso(d), "hasta": _iso(h)}
        fd._paso(n["id"], "Libre" if libre else f"Ocupado ({len(bloques)} bloque(s))", "ok" if libre else "info",
                 "Franjas libres:\n" + "\n".join(f"· {x['inicio'][:16]}" for x in franjas) if franjas else "")
        return "libre" if libre else "ocupado"

    def _franjas(self, d, h, bloques, dur, c: dict, zona: str) -> List[dict]:
        import festivos as m_fest
        desde_h, hasta_h = str(c.get("horario_desde") or "08:00"), str(c.get("horario_hasta") or "17:00")
        r: List[dict] = []
        dia = d.replace(hour=0, minute=0, second=0, microsecond=0)
        while dia < h and len(r) < int(c.get("max_franjas") or 5):
            if m_fest.dia_habil(dia.date()):
                a = dia.replace(hour=int(desde_h[:2]), minute=int(desde_h[3:5]))
                b = dia.replace(hour=int(hasta_h[:2]), minute=int(hasta_h[3:5]))
                t = max(a, d)
                if t.minute % 15 or t.second:  # a la próxima media hora o cuarto de hora
                    t = (t + datetime.timedelta(minutes=15 - t.minute % 15)).replace(second=0, microsecond=0)
                while t + dur <= min(b, h) and len(r) < int(c.get("max_franjas") or 5):
                    choque = next((y for x, y in bloques if x < t + dur and y > t), None)
                    if choque:
                        t = choque
                        if t.minute % 15:
                            t = (t + datetime.timedelta(minutes=15 - t.minute % 15)).replace(second=0, microsecond=0)
                        continue
                    r.append({"inicio": _iso(t), "fin": _iso(t + dur)})
                    t = t + dur
            dia += datetime.timedelta(days=1)
        return r

    # ======================================================= Docs y Forms
    def docs_crear_desde_plantilla(self, n: dict) -> str:
        import documentos as m_doc
        c = self._cfg(n)
        g, cred = self._google(n)
        pid = c_google.id_de(self.render(c.get("plantilla") or ""), "plantilla")
        variables = {k: self.render(v) if isinstance(v, str) else fd._a_texto(v) for k, v in (c.get("variables") or {}).items()}

        def valor(x: str) -> str:
            x = x.strip()
            return variables[x] if x in variables else fd._a_texto(self.resolver(x))

        def llenar(texto: str) -> str:
            return fd.PLANTILLA.sub(lambda m: valor(m.group(1)), texto)

        nombre = self.render(c.get("nombre") or "").strip() or "documento"
        exportar = c.get("exportar") or "pdf"
        mime = {"pdf": "application/pdf", "docx": DOCX}.get(exportar, "")
        archivo: Dict[str, Any] = {}

        def local() -> int:
            lleno, cambiados = m_doc.llenar_docx(self._r(n, lambda: g.exportar(pid, DOCX), "Google Docs"), llenar)
            archivo["datos"] = lleno if exportar == "docx" else m_doc.pdf_de_bloques(m_doc.bloques_docx(lleno), "")
            return cambiados

        reemplazos: Any = None
        r: Dict[str, Any] = {}
        if c.get("en_drive") is True:
            carpeta = self.render(c.get("carpeta") or "").strip()

            def hacer() -> dict:
                copia = g.copiar(pid, nombre, carpeta)
                doc = g.documento(copia["id"])
                texto = json.dumps(doc.get("body") or {}, ensure_ascii=False)
                marcas = {m.group(1).strip() for m in fd.PLANTILLA.finditer(texto)}
                hechos = g.reemplazar(copia["id"], {x: valor(x) for x in marcas | set(variables)})
                if mime:
                    archivo["datos"] = g.exportar(copia["id"], mime)
                return {"doc_id": copia["id"], "enlace": copia.get("webViewLink") or "", "reemplazos": hechos}

            r = self._escribir(n, "google_docs", "crear documento", f"drive:{carpeta or 'raíz'}/{nombre}",
                               {"plantilla": pid, "nombre": nombre, "carpeta": carpeta, "variables": variables, "exportar": exportar}, cred, hacer)
            reemplazos = r.get("reemplazos")
            if r["bloqueado"] and mime:
                reemplazos = local()  # sin permiso: la copia en Drive queda retenida, pero el documento se llena aquí
        else:
            reemplazos = local()
        ficha: Dict[str, Any] = {}
        if archivo.get("datos") is not None:
            ficha = self._guardar_archivo(f"{nombre}.{exportar}", archivo["datos"], mime, "docs")
        self.vars[self._sal(n)] = {**ficha, "reemplazos": reemplazos, **({k: r.get(k) for k in ("doc_id", "enlace", "bloqueado", "motivo") if k in r})}
        fd._paso(n["id"], f"Documento desde la plantilla: {ficha.get('nombre') or nombre}", "ok", f"{reemplazos} reemplazo(s)", archivo=ficha.get("id"))
        return "ok"

    def forms_respuestas(self, n: dict) -> str:
        c = self._cfg(n)
        g, _ = self._google(n)
        fid = self.render(c.get("formulario") or "")
        f = self._r(n, lambda: g.formulario(fid), "Google Forms")
        preguntas = c_google.preguntas_de(f)
        desde = ""
        if c.get("desde") and self.render(c["desde"]).strip():
            desde = _fecha(self.render(c["desde"]), ZONA).astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rs = self._r(n, lambda: g.respuestas(fid, desde, int(c.get("max") or 500)), "Google Forms")
        lista = sorted((c_google.respuesta_de_forms(x, preguntas) for x in rs), key=lambda x: x["enviada"])
        self.vars[self._sal(n)] = lista
        fd._paso(n["id"], f"{len(lista)} respuesta(s) de «{(f.get('info') or {}).get('title', '')}»", "ok" if lista else "info", ", ".join(preguntas.values())[:600])
        return "ok" if lista else "vacio"

    # ============================================================= Outlook
    def outlook_buscar(self, n: dict) -> str:
        c = self._cfg(n)
        g, _ = self._graph(n)
        filtro = {k: (self.render(v) if isinstance(v, str) else v) for k, v in (c.get("filtro") or {}).items()}
        filtros = []
        desde = self.render(c.get("desde_fecha") or "").strip()
        if desde:
            if not re.match(r"^\d{4}-\d{2}-\d{2}", desde):
                raise ErrorDeNegocio(f"desde_fecha «{desde}» tiene que ser AAAA-MM-DD")
            filtros.append(f"receivedDateTime ge {desde[:10]}T00:00:00Z")
        if filtro.get("no_leidos"):
            filtros.append("isRead eq false")
        consulta = self.render(c.get("consulta") or "").strip()
        crudos = self._r(n, lambda: g.mensajes(self.render(c.get("carpeta") or "inbox"), consulta, filtros, int(c.get("max") or 10)), "Outlook")
        correos = []
        for m in crudos:
            x = c_ms.correo(m)
            if consulta and ((desde and x["fecha"][:10] < desde[:10]) or (filtro.get("no_leidos") and x["leido"])):
                continue
            if not _coincide(x, filtro):
                continue
            if x.pop("_tiene_adjuntos") and c.get("incluir_adjuntos", True) is not False:
                for a in self._r(n, lambda gid=x["graph_id"]: g.adjuntos(gid), "Outlook"):
                    if a.get("isInline") or not a.get("contentBytes") or (a.get("size") or 0) > c_ms.MAX_ADJUNTO:
                        continue
                    import base64
                    x["adjuntos"].append({"nombre": a.get("name") or "adjunto", "tipo": a.get("contentType") or "", "tamano": a.get("size") or 0,
                                          "_datos": base64.b64decode(a["contentBytes"])})
                x["adjuntos"] = self._fichas_adjuntos(x, "outlook")
            correos.append(x)
        self.vars[self._sal(n)] = correos
        fd._paso(n["id"], f"{len(correos)} correo(s) de Outlook", "ok" if correos else "info",
                 "\n".join(f"· {x['fecha'][:16]} {x['remitente_correo']}: {x['asunto']}" for x in correos[:15]), total=len(correos))
        return "ok" if correos else "vacio"

    def _remitente_ms(self, cred: dict) -> str:
        v = cred["valores"]
        return c_oauth.revisar_cuenta(v) if cred["tipo"] == "microsoft_oauth" else fd.c_correo._buzon_graph(v)

    def outlook_enviar(self, n: dict) -> str:
        c = self._cfg(n)
        g, cred = self._graph(n)
        remitente = self._remitente_ms(cred)
        m, resumen = self._correo_saliente(n, "outlook")
        self._revisar_correo(n, "outlook", "enviar correo", m, resumen, True)
        detalle = self._detalle_correo(m, resumen, remitente=remitente)
        r = self._escribir(n, "outlook", "enviar correo", m["para"], detalle, cred, lambda: g.enviar(m, c.get("guardar_enviados", True) is not False))
        self.mensajes.append({**{k: detalle.get(k) for k in ("para", "cc", "asunto", "cuerpo", "adjuntos")}, "nodo": n["id"], "operacion": "outlook_enviar",
                              "enviado": not r["bloqueado"], "bloqueado": r["bloqueado"]})
        self._guardar_salida(n, r, ("para", "asunto", "adjuntos"), enviado=not r["bloqueado"])
        return "ok"

    def _gid(self, n: dict) -> str:
        gid = self.render(self._cfg(n).get("mensaje") or "").strip() or str((self.msg or {}).get("_graph_id") or "")
        if not gid:
            raise ErrorDeNegocio(f"{n['tipo']} necesita mensaje (el graph_id, p. ej. «{{{{correo_i.graph_id}}}}») o que la ejecución arranque con outlook_nuevo")
        if not c_ms.ID_GRAPH.match(gid):
            raise ErrorDeNegocio("mensaje no es un id de Graph")
        return gid

    def outlook_responder(self, n: dict) -> str:
        c = self._cfg(n)
        g, cred = self._graph(n)
        remitente = self._remitente_ms(cred)
        gid = self._gid(n)
        m, resumen = self._correo_saliente(n, "outlook")
        para_visible = m["para"] or (self.correo.get("remitente_correo") if (self.msg or {}).get("_graph_id") == gid else "") or "(el remitente del original)"
        for d in direcciones(m["para"]) + direcciones(m["cc"]) + ([self.correo.get("remitente_correo")] if not m["para"] and self.correo.get("remitente_correo") else []):
            c_ms.revisar(d, "destinatario")
        self._revisar_correo(n, "outlook", "responder correo", m, resumen, True, exigir_para=False)
        incluir, todos = c.get("incluir_original", True) is not False, c.get("todos") is True
        detalle = self._detalle_correo({**m, "para": para_visible}, resumen, remitente=remitente, en_respuesta_a=gid, mismo_hilo=True, todos=todos)
        r = self._escribir(n, "outlook", "responder correo", para_visible, detalle, cred, lambda: g.responder(gid, m, todos, incluir))
        self.mensajes.append({**{k: detalle.get(k) for k in ("para", "cc", "asunto", "cuerpo", "adjuntos")}, "nodo": n["id"], "operacion": "outlook_responder",
                              "enviado": not r["bloqueado"], "bloqueado": r["bloqueado"]})
        self._guardar_salida(n, r, ("para", "asunto", "adjuntos", "en_respuesta_a"), enviado=not r["bloqueado"])
        return "ok"

    def outlook_mover(self, n: dict) -> str:
        g, cred = self._graph(n)
        self._remitente_ms(cred)
        gid, carpeta = self._gid(n), self.render(self._cfg(n).get("carpeta") or "").strip()
        r = self._escribir(n, "outlook", "mover correo", f"outlook:{carpeta}", {"mensaje": gid, "carpeta": carpeta}, cred, lambda: g.mover(gid, carpeta))
        self._guardar_salida(n, r, ("movido", "nuevo_id"), carpeta=carpeta)
        return "ok"

    def outlook_categorizar(self, n: dict) -> str:
        c = self._cfg(n)
        g, cred = self._graph(n)
        self._remitente_ms(cred)
        gid = self._gid(n)
        cats = [self.render(x).strip() for x in c.get("categorias") or [] if self.render(x).strip()]
        modo = c.get("modo") or "agregar"

        def hacer() -> dict:
            cambios: Dict[str, Any] = {}
            if cats:
                actuales = [] if modo == "reemplazar" else list(g.mensaje(gid).get("categories") or [])
                cambios["categories"] = [x for x in actuales if x not in cats] if modo == "quitar" else list(dict.fromkeys(actuales + cats))
            if c.get("marcar_leido") is True:
                cambios["isRead"] = True
            if c.get("importancia"):
                cambios["importance"] = c["importancia"]
            return g.categorizar(gid, cambios)

        r = self._escribir(n, "outlook", "categorizar correo", f"outlook:{gid[:24]}", {"mensaje": gid, "categorias": cats, "modo": modo,
                           "marcar_leido": c.get("marcar_leido") is True, "importancia": c.get("importancia")}, cred, hacer)
        self._guardar_salida(n, r, ("categorias",), pedidas=cats)
        return "ok"

    def outlook_calendario_eventos(self, n: dict) -> str:
        c = self._cfg(n)
        g, _ = self._graph(n)
        d, h, zona = self._rango(c)
        ev = self._r(n, lambda: g.eventos(self.render(c.get("calendario") or ""), _iso(d), _iso(h), int(c.get("max") or 50), zona), "Outlook")
        consulta = fd._norm(self.render(c.get("consulta") or ""))
        if consulta:
            ev = [e for e in ev if consulta in fd._norm(e["titulo"] + " " + e["descripcion"])]
        self.vars[self._sal(n)] = ev
        fd._paso(n["id"], f"{len(ev)} evento(s) de Outlook", "ok" if ev else "info", "\n".join(f"· {e['inicio'][:16]} {e['titulo']}" for e in ev[:15]), total=len(ev))
        return "ok" if ev else "vacio"

    def outlook_calendario_crear(self, n: dict) -> str:
        c = self._cfg(n)
        g, cred = self._graph(n)
        self._remitente_ms(cred)
        zona = c.get("zona") or ZONA
        ini, fin, todo = self._horario(c, zona)
        asistentes = self._invitados(c.get("asistentes"))
        titulo, cal = self.render(c.get("titulo") or ""), self.render(c.get("calendario") or "")
        try:
            cuerpo = c_ms.cuerpo_evento(titulo, ini.strftime("%Y-%m-%dT%H:%M:%S"), fin.strftime("%Y-%m-%dT%H:%M:%S"), zona, todo,
                                        self.render(c.get("descripcion") or ""), self.render(c.get("lugar") or ""), asistentes,
                                        c.get("con_teams") is True, c.get("recordatorio_min"))
        except ErrorDeNegocio as e:
            self._retener_vetado(n, "outlook_calendario", "crear evento", cal or "principal", {"titulo": titulo, "asistentes": asistentes}, e)
            raise
        detalle = {"calendario": cal or "principal", "titulo": titulo, "inicio": _iso(ini), "fin": _iso(fin), "asistentes": asistentes,
                   "con_teams": c.get("con_teams") is True, "evento": cuerpo}
        r = self._escribir(n, "outlook_calendario", "crear evento", f"outlook:{cal or 'calendario'}", detalle, cred, lambda: g.crear_evento(cal, cuerpo))
        self._guardar_salida(n, r, ("id", "enlace", "teams"), inicio=_iso(ini), fin=_iso(fin), titulo=titulo)
        return "ok"

    # ===================================================== OneDrive y SharePoint
    def _listar_unidad(self, n: dict, g: c_ms.Graph, unidad: str) -> str:
        c = self._cfg(n)
        items = self._r(n, lambda: g.listar(unidad, self.render(c.get("carpeta") or ""), self.render(c.get("buscar") or ""), int(c.get("max") or 50)), "OneDrive")
        self.vars[self._sal(n)] = items
        fd._paso(n["id"], f"{len(items)} elemento(s)", "ok" if items else "info", "\n".join(f"· {x['nombre']}" for x in items[:20]), total=len(items))
        return "ok" if items else "vacio"

    def _leer_unidad(self, n: dict, g: c_ms.Graph, unidad: str, ref: str) -> str:
        c = self._cfg(n)
        meta = self._r(n, lambda: g.metadatos(unidad, ref), "OneDrive")
        if meta.get("folder"):
            raise ErrorDeNegocio(f"«{ref}» es una carpeta: para ver lo que tiene usa listar")
        datos = self._r(n, lambda: g.bajar(unidad, ref), "OneDrive")
        nombre, tipo = str(meta.get("name") or "archivo"), ((meta.get("file") or {}).get("mimeType") or mimetypes.guess_type(str(meta.get("name") or ""))[0] or "")
        if (c.get("como") or "texto") == "archivo":
            ficha = self._guardar_archivo(nombre, datos, tipo or "application/octet-stream", "onedrive")
            self.vars[self._sal(n)] = {**ficha, "enlace": meta.get("webUrl") or ""}
            fd._paso(n["id"], f"Archivo traído: {nombre}", "ok", f"{len(datos):,} bytes · id {ficha['id']}", archivo=ficha["id"])
            return "ok"
        texto = fd.c_hojas._texto(datos) if nombre.lower().endswith((".txt", ".csv", ".json", ".md", ".xml", ".html")) else self._convertir(datos, nombre, c.get("ocr", True) is not False)
        tope = int(c.get("max_caracteres") or 60000)
        self.vars[self._sal(n)] = {"id": meta.get("id"), "nombre": nombre, "tipo": tipo, "texto": texto[:tope], "truncado": len(texto) > tope, "enlace": meta.get("webUrl") or ""}
        fd._paso(n["id"], f"Leído: {nombre}", "ok" if texto.strip() else "aviso", f"{len(texto):,} caracteres\n\n{texto[:600]}")
        return "ok"

    def _subir_unidad(self, n: dict, g: c_ms.Graph, unidad: str, cred: dict, sistema: str) -> str:
        c = self._cfg(n)
        archivo = None
        if c.get("archivo") not in (None, "", []):
            archivos, _ = self._para_envio(c["archivo"], n["tipo"], max_archivos=1, max_mb=250)
            if not archivos:
                raise ErrorDeNegocio(f"{n['tipo']}: el archivo no está (revisa la referencia, p. ej. «{{{{documento}}}}»)")
            archivo = archivos[0]
            datos, tipo = archivo["datos"], archivo["tipo"]
        else:
            valor, formato = self._contenido_a_guardar(c)
            if formato == "json":
                datos, tipo = json.dumps(valor, ensure_ascii=False, indent=2, default=str).encode(), "application/json"
            elif formato == "csv":
                datos, tipo = fd.c_archivos._csv(valor).encode(), "text/csv"
            else:
                datos, tipo = (valor if isinstance(valor, str) else fd._a_texto(valor)).encode(), "text/plain"
        nombre = self.render(c.get("nombre") or "").strip() or (archivo or {}).get("nombre", "")
        if "/" in nombre or "\\" in nombre:
            raise ErrorDeNegocio("nombre no puede llevar «/» (pon la carpeta en carpeta)")
        carpeta = self.render(c.get("carpeta") or "").strip()
        c_ms.ruta_segura(f"{carpeta}/{nombre}")
        detalle = {"carpeta": carpeta or "/", "nombre": nombre, "tipo": tipo, "bytes": len(datos), "conflicto": c.get("conflicto") or "renombrar",
                   **({"archivo": {k: archivo[k] for k in ("id", "nombre", "tipo", "tamano")}} if archivo else {"vista": datos[:600].decode("utf-8", "replace")})}
        r = self._escribir(n, sistema, "subir archivo", f"{sistema}:{carpeta or '/'}/{nombre}", detalle, cred,
                           lambda: g.subir(unidad, carpeta, nombre, datos, tipo, c.get("conflicto") or "renombrar"))
        self._guardar_salida(n, r, ("id", "nombre", "enlace"))
        return "ok"

    def onedrive_listar(self, n: dict) -> str:
        g, _ = self._graph(n)
        return self._listar_unidad(n, g, g.unidad_propia())

    def onedrive_leer(self, n: dict) -> str:
        g, _ = self._graph(n)
        return self._leer_unidad(n, g, g.unidad_propia(), self.render(self._cfg(n).get("archivo") or "").strip())

    def onedrive_subir(self, n: dict) -> str:
        g, cred = self._graph(n)
        return self._subir_unidad(n, g, g.unidad_propia(), cred, "onedrive")

    def sharepoint_archivos(self, n: dict) -> str:
        c = self._cfg(n)
        g, cred = self._graph(n)
        unidad = self._r(n, lambda: g.unidad_de_sitio(self.render(c.get("sitio") or ""), self.render(c.get("biblioteca") or "")), "SharePoint")
        op = c.get("operacion") or "listar"
        if op == "listar":
            return self._listar_unidad(n, g, unidad)
        if op == "leer":
            ref = c.get("archivo")
            return self._leer_unidad(n, g, unidad, self.render(ref if isinstance(ref, str) else "").strip())
        return self._subir_unidad(n, g, unidad, cred, "sharepoint")

    def sharepoint_lista(self, n: dict) -> str:
        c = self._cfg(n)
        g, cred = self._graph(n)
        base = self._r(n, lambda: g.sitio(self.render(c.get("sitio") or "")), "SharePoint")
        bl = self._r(n, lambda: g.lista(base, self.render(c.get("lista") or "")), "SharePoint")
        if (c.get("operacion") or "leer") == "leer":
            items = self._r(n, lambda: g.items(bl, self.render(c.get("filtro") or ""), int(c.get("max") or 200)), "SharePoint")
            self.vars[self._sal(n)] = items
            fd._paso(n["id"], f"{len(items)} elemento(s) de la lista", "ok" if items else "info", fd._a_texto(items[:5])[:600], total=len(items))
            return "ok" if items else "vacio"
        campos = {k: self.render(v) if isinstance(v, str) else v for k, v in (c.get("campos") or {}).items()}
        r = self._escribir(n, "sharepoint", "agregar a la lista", f"sharepoint:{self.render(c.get('lista') or '')}", {"lista": self.render(c.get("lista") or ""),
                           "campos": campos}, cred, lambda: g.agregar_item(bl, campos))
        self._guardar_salida(n, r, ("id", "enlace"), campos=campos)
        return "ok"

    def teams_mensaje(self, n: dict) -> str:
        import html as m_html
        c = self._cfg(n)
        g, cred = self._graph(n)
        texto, titulo = self.render(c.get("texto") or ""), self.render(c.get("titulo") or "")
        enlaces = self._enlaces(c.get("archivos"), "el mensaje") if c.get("archivos") not in (None, "", []) else []
        html = (c.get("formato") or "texto") == "html"
        if enlaces:
            if html:
                texto += "<br>" + "<br>".join(f'<a href="{m_html.escape(x["url"])}">Descargar {m_html.escape(x["nombre"])}</a>' for x in enlaces)
            else:
                texto += "\n" + "\n".join(f"Descargar {x['nombre']}: {x['url']}" for x in enlaces)
        cuerpo: Dict[str, Any] = {"body": {"contentType": "html" if html else "text", "content": texto}}
        destino = c.get("destino") or "canal"
        if destino == "canal":
            equipo, canal = self.render(c.get("equipo") or "").strip(), self.render(c.get("canal") or "").strip()
            if titulo:
                cuerpo["subject"] = titulo
            hacer, donde = (lambda: g.publicar_canal(equipo, canal, cuerpo)), f"teams:{equipo[:12]}/{canal[:30]}"
        else:
            chat = self.render(c.get("chat") or "").strip()
            hacer, donde = (lambda: g.publicar_chat(chat, cuerpo)), f"teams:chat/{chat[:30]}"
        detalle = {"destino": destino, "titulo": titulo, "texto": texto[:4000], **({"archivos": [{k: x[k] for k in ("id", "nombre", "tipo", "vence") if k in x} for x in enlaces]} if enlaces else {})}
        r = self._escribir(n, "teams", "publicar mensaje", donde, detalle, cred, hacer)
        self._guardar_salida(n, r, ("id", "enlace"), enviado=not r["bloqueado"])
        return "ok"

    def excel_en_linea(self, n: dict) -> str:
        c = self._cfg(n)
        g, cred = self._graph(n)
        sitio = self.render(c.get("sitio") or "").strip()
        unidad = self._r(n, lambda: g.unidad_de_sitio(sitio, ""), "SharePoint") if sitio else g.unidad_propia()
        archivo = self.render(c.get("archivo") or "").strip()
        libro = g.libro(unidad, archivo)
        tabla, hoja, rango = self.render(c.get("tabla") or "").strip(), self.render(c.get("hoja") or "").strip(), self.render(c.get("rango") or "").strip()
        op = c.get("operacion") or "leer"
        if op == "leer":
            if tabla:
                cab, cuerpo = self._r(n, lambda: g.valores_tabla(libro, tabla), "Excel")
                matriz = [cab] + cuerpo
            else:
                matriz = self._r(n, lambda: g.rango(libro, hoja, rango), "Excel")
            filas, columnas = c_sheets.a_filas(matriz, c.get("encabezados", True) is not False if not tabla else True, int(c.get("max_filas") or 500))
            self.vars[self._sal(n)] = filas
            fd._paso(n["id"], f"{len(filas)} fila(s) de Excel", "ok" if filas else "info", f"columnas: {', '.join(columnas)}", total=len(filas))
            return "ok" if filas else "vacio"
        if c.get("filas") not in (None, ""):
            v = self.valor_de(c["filas"])
            filas = v if isinstance(v, list) else [v]
        else:
            filas = [{k: self.render(x) if isinstance(x, str) else x for k, x in (c.get("fila") or {}).items()}]
        filas = [f for f in filas if isinstance(f, dict)]
        if not filas:
            raise ErrorDeNegocio("no hay filas (objetos {columna: valor}) para escribir")
        if op == "anexar":
            cab, _ = self._r(n, lambda: g.valores_tabla(libro, tabla), "Excel")
            matriz = [[fd._a_texto(f.get(col)) if isinstance(f.get(col), (dict, list)) else ("" if f.get(col) is None else f.get(col)) for col in cab] for f in filas]
            r = self._escribir(n, "excel", "anexar filas", f"excel:{archivo}#{tabla}", {"archivo": archivo, "tabla": tabla, "columnas": cab, "filas": matriz[:200]}, cred,
                               lambda: g.anexar_tabla(libro, tabla, matriz))
        else:
            columnas = list(dict.fromkeys(k for f in filas for k in f))
            matriz = [columnas] + [["" if f.get(k) is None else (fd._a_texto(f[k]) if isinstance(f[k], (dict, list)) else f[k]) for k in columnas] for f in filas]
            direccion = c_ms.rango_para(rango, len(matriz), len(columnas))
            r = self._escribir(n, "excel", "escribir rango", f"excel:{archivo}#{hoja}!{direccion}", {"archivo": archivo, "hoja": hoja, "rango": direccion,
                               "valores": matriz[:200]}, cred, lambda: g.escribir_rango(libro, hoja, direccion, matriz))
        self._guardar_salida(n, r, ("agregadas", "rango", "filas"), filas_pedidas=len(filas))
        return "ok"

    # ============================================================== OpenAPI
    def _spec(self, cred: dict) -> dict:
        v = cred["valores"]
        clave = hashlib.sha1(json.dumps([v.get("spec_url"), (v.get("spec") or "")[:2000], len(v.get("spec") or "")]).encode()).hexdigest()
        if clave not in _specs:
            _specs[clave] = cargar_spec(v, red_privada_de(cred))
        return _specs[clave]

    def api_operacion(self, n: dict) -> str:
        c = self._cfg(n)
        cred = self.cred(c.get("credencial"), ("api_openapi",))
        v = cred["valores"]
        spec = self._spec(cred)
        op = c_openapi.buscar(c_openapi.operaciones(spec), self.render(c.get("operacion") or ""))
        base = base_api(v, spec)
        valores = {k: self.render(x) if isinstance(x, str) else x for k, x in (c.get("parametros") or {}).items()}
        cuerpo = None
        if c.get("cuerpo") not in (None, ""):
            cuerpo = self.render_profundo(c["cuerpo"])
            if isinstance(cuerpo, str):
                try:
                    cuerpo = json.loads(cuerpo)
                except ValueError:
                    pass
        req = c_openapi.armar(op, base, valores, cuerpo)
        u = urllib.parse.urlsplit(req["url"])
        vetado = host_vetado(u.hostname or "")
        salida = self._sal(n)
        vista = {"operacion": op["id"], "metodo": req["metodo"], "url": req["url"], "query": req["query"], "encabezados": req["encabezados"], "cuerpo": req["cuerpo"]}
        if vetado:
            if op["escritura"]:
                self._retener_vetado(n, "api", req["metodo"], req["url"], vista, ErrorDeNegocio(vetado))
            raise ErrorDeNegocio(f"bloqueado por seguridad: {vetado}")
        red = red_privada_de(cred)
        tiempo = timeout_de(c)

        def una(url: str = req["url"], query: Optional[dict] = None) -> dict:
            enc, params = autenticar(v, dict(req["encabezados"]), dict(req["query"] if query is None else query), tiempo, red)
            if isinstance(req["cuerpo"], (dict, list)):
                kw: Dict[str, Any] = {"json": req["cuerpo"]}
            elif req["cuerpo"] not in (None, ""):
                kw = {"data": str(req["cuerpo"]), "headers": {**enc, "Content-Type": "text/plain; charset=utf-8"}}
            else:
                kw = {}
            kw.setdefault("headers", enc)
            r = rest.pedir(req["metodo"], url, params=params, timeout=tiempo, red_privada=red, servicio=u.hostname or "la API", errores=False, **kw)
            d = {"status": r.status, "ok": 200 <= r.status < 400, "datos": r.datos(), "ms": r.ms, "operacion": op["id"], "simulado": False,
                 "encabezados": {k: x for k, x in r.encabezados.items() if k in ("content-type", "link", "etag", "location", "retry-after")}}
            if r.status in (408, 425, 429) or r.status >= 500:
                raise fd.RespuestaTransitoria(d)
            return d

        if op["escritura"] and cred.get("permitir_escritura") is not True:
            self._escribir(n, "api", f"{req['metodo']} {op['id']}", req["url"], vista, cred, lambda: {})
            self.vars[salida] = {**vista, "status": None, "ok": False, "datos": None, "simulado": True, "bloqueado": True}
            self._mapear(n, None, simulado=True)
            return "ok"
        try:
            r = self._con_reintentos(n, una, f"{req['metodo']} {u.hostname}")
        except fd.RespuestaTransitoria as e:
            r = e.respuesta
        if op["escritura"]:
            reg = {**vista, "status": r["status"], "bloqueado": False, "simulado": False, "nodo": n["id"], "sistema": "api", "operacion": f"{req['metodo']} {op['id']}"}
            self.escrituras.append(reg)
            fd.evento("escritura", sistema="api", operacion=f"{req['metodo']} {op['id']}", destino=req["url"], bloqueada=False, simulada=False, detalle=vista,
                      nodo=n["id"], credencial=cred["nombre"], status=r["status"])
        if r["ok"] and isinstance(c.get("paginacion"), dict) and req["metodo"] == "GET":
            pg = c["paginacion"]
            items, url, pagina, datos, enc = list(_items(pg, r["datos"])), req["url"] + ("?" + urllib.parse.urlencode(req["query"]) if req["query"] else ""), 1, r["datos"], r["encabezados"]
            while pagina < int(pg.get("max_paginas") or 5):
                sig = _siguiente(pg, url, datos, enc, pagina)
                if not sig:
                    break
                x = una(sig.split("?", 1)[0], dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(sig).query)))
                if not x["ok"]:
                    self.avisos.append(f"{n['id']}: la página {pagina + 1} respondió {x['status']}; se juntaron las anteriores")
                    break
                url, datos, enc, pagina = sig, x["datos"], x["encabezados"], pagina + 1
                items += _items(pg, datos)
            r = {**r, "items": items, "paginas": pagina}
        self.vars[salida] = r
        self.peticiones.append({**vista, **{k: r[k] for k in ("status", "ok", "ms")}, "nodo": n["id"]})
        error = r["status"] >= 400
        fd._paso(n["id"], f"{req['metodo']} {op['id']} → {r['status']}", "error" if error else "ok",
                 f"{req['url']} · {r['ms']} ms" + (f" · {r.get('paginas')} página(s), {len(r.get('items') or [])} elemento(s)" if "items" in r else "")
                 + f"\n\n{fd._a_texto(r['datos'])[:900]}", status=r["status"], metodo=req["metodo"], destino=req["url"])
        if error:
            self._mapear(n, None, simulado=True)
            return "error_http"
        self._mapear(n, r["datos"], simulado=False)
        return "ok"

    # ============================================================== http (lo nuevo)
    def _http_archivos(self, c: dict, p: dict) -> List[dict]:
        """Sube archivos de la ejecución con la petición: multipart (el cuerpo JSON en «datos») o binario (el primero es el cuerpo)."""
        if c.get("archivos") in (None, "", []) or p["metodo"] == "GET":
            return []
        archivos, resumen = self._para_envio(c["archivos"], "la petición")
        if not archivos:
            return []
        if (c.get("modo_archivos") or "multipart") == "binario":
            a = archivos[0]
            p["cuerpo"] = a["datos"]
            p["encabezados"]["Content-Type"] = a["tipo"] or "application/octet-stream"
            p["vista_cuerpo"] = f"(archivo {a['nombre']}, {a['tamano']:,} bytes)"
            return resumen[:1]
        partes = []
        if p.get("vista_cuerpo") is not None:
            partes.append(("datos", "", json.dumps(p["vista_cuerpo"], ensure_ascii=False).encode("utf-8") if not isinstance(p["vista_cuerpo"], str)
                           else p["vista_cuerpo"].encode("utf-8"), "application/json; charset=utf-8" if not isinstance(p["vista_cuerpo"], str) else "text/plain; charset=utf-8"))
        campo = str(c.get("campo_archivo") or "archivo")
        partes += [(campo, a["nombre"], a["datos"], a["tipo"]) for a in archivos]
        p["cuerpo"], tipo = rest.multipart(partes)
        p["encabezados"] = {k: x for k, x in p["encabezados"].items() if k.lower() != "content-type"}
        p["encabezados"]["Content-Type"] = tipo
        return resumen

    def _paginar_http(self, n: dict, c: dict, p: dict, r: dict, una: Callable[[dict], dict]) -> dict:
        pg = c["paginacion"]
        items, url, datos, enc, pagina = list(_items(pg, r["datos"])), p["url"], r["datos"], r.get("encabezados") or {}, 1
        while pagina < int(pg.get("max_paginas") or 5):
            sig = _siguiente(pg, url, datos, enc, pagina)
            if not sig:
                break
            pp = self._armar_http({**c, "url": sig, "query": {}, "cuerpo": None, "archivos": None})
            if pp["host"] != p["host"]:
                raise ErrorDeNegocio("la página siguiente va a otro host; no se sigue")
            x = self._con_reintentos(n, lambda pp=pp: una(pp), f"GET {pp['host']}")
            if x["status"] >= 400:
                self.avisos.append(f"{n['id']}: la página {pagina + 1} respondió {x['status']}; se juntaron las anteriores")
                break
            url, datos, enc, pagina = sig, x["datos"], x.get("encabezados") or {}, pagina + 1
            items += _items(pg, datos)
        return {**r, "items": items, "paginas": pagina}

    def _descarga_http(self, c: dict, p: dict, r: dict) -> dict:
        crudo = r.pop("crudo", b"") or b""
        tipo = (r.get("encabezados") or {}).get("content-type", "application/octet-stream").split(";")[0].strip()
        nombre = self.render(c.get("nombre_archivo") or "").strip() or _nombre_de(p["url"], r.get("encabezados") or {}, tipo)
        ficha = self._guardar_archivo(nombre, crudo, tipo, "descarga")
        return {**r, "datos": ficha, "archivo": ficha}


# =====================================================================
#   OpenAPI: la especificación y la autenticación de la conexión
# =====================================================================

def red_privada_de(cred: dict) -> bool:
    """IP privadas solo si una persona creó la conexión y marcó red_privada (como las bases de datos)."""
    return (cred.get("valores") or {}).get("red_privada") is True and cred.get("creada_por_persona") is True


def cargar_spec(v: dict, red_privada: bool = False) -> dict:
    if str(v.get("spec") or "").strip():
        return c_openapi.cargar_texto(str(v["spec"]))
    url = str(v.get("spec_url") or "").strip()
    if not url:
        raise ErrorDeNegocio("la conexión no tiene la especificación (spec_url o spec)")
    vetado = host_vetado(urllib.parse.urlsplit(url).hostname or "")
    if vetado:
        raise ErrorDeNegocio(f"bloqueado por seguridad: {vetado}")
    r = rest.pedir("GET", url, timeout=20, red_privada=red_privada, servicio="la especificación", max_bytes=c_openapi.MAX_SPEC,
                   headers={"Accept": "application/json, application/yaml, text/yaml, */*"})
    return c_openapi.cargar_texto(r.text)


def base_api(v: dict, spec: dict) -> str:
    base = str(v.get("base_url") or "").strip() or c_openapi.base_de(spec)
    if base.startswith("/") and v.get("spec_url"):
        base = urllib.parse.urljoin(str(v["spec_url"]), base)
    return base.rstrip("/")


def autenticar(v: dict, encabezados: Dict[str, str], params: Dict[str, Any], timeout: float, red: bool) -> Tuple[Dict[str, str], Dict[str, Any]]:
    import base64
    auth = v.get("auth") or "ninguna"
    if auth == "api_key":
        nombre = str(v.get("llave_nombre") or "X-API-Key")
        if (v.get("llave_en") or "encabezado") == "consulta":
            params[nombre] = str(v.get("token") or "")
        else:
            encabezados[nombre] = str(v.get("token") or "")
    elif auth == "bearer":
        encabezados["Authorization"] = f"Bearer {v.get('token') or ''}"
    elif auth == "basico":
        encabezados["Authorization"] = "Basic " + base64.b64encode(f"{v.get('usuario') or ''}:{v.get('clave') or ''}".encode()).decode()
    elif auth == "oauth2":
        encabezados["Authorization"] = "Bearer " + c_oauth.cliente(str(v.get("token_url") or ""), str(v.get("client_id") or ""), str(v.get("client_secret") or ""),
                                                                   str(v.get("scope") or ""), timeout, red)
    return encabezados, params


# =====================================================================
#   disparadores por evento: el sondeo (solo lectura, sin repetir)
# =====================================================================

def _coincide(x: dict, filtro: dict) -> bool:
    a = fd.c_correo._ascii
    if filtro.get("remitente") and a(filtro["remitente"]).lower() not in a(x.get("remitente", "")).lower():
        return False
    if filtro.get("asunto") and a(filtro["asunto"]).lower() not in a(x.get("asunto", "")).lower():
        return False
    if filtro.get("texto") and a(filtro["texto"]).lower() not in a(x.get("asunto", "") + " " + x.get("cuerpo", "")).lower():
        return False
    if filtro.get("no_leidos") and x.get("leido"):
        return False
    return True


def _guardar_caso(flujo: str, clave: str, m: dict, origen: str) -> dict:
    caso = f"correo-{flujo}-{hashlib.sha1(clave.encode()).hexdigest()[:16]}"
    fd.guardar_correo(fd.RECIBIDOS / caso, m, origen)
    return {"caso": caso, "id": str(m.get("id") or clave), "asunto": m.get("asunto") or ""}


def sondear(e: Any, n: dict, ruta: Path) -> dict:
    """Revisa un disparador gmail_nuevo, outlook_nuevo o calendar_evento_proximo; guarda lo nuevo y lo devuelve (casos o entradas)."""
    if n["tipo"] in nx.TIPOS:
        return nx.sondear(e, n, ruta)
    c = n.get("config") or {}
    estado = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    t = n["tipo"]
    maximo = max(1, min(50, int(c.get("max_por_sondeo") or 10)))
    adj = c.get("leer_adjuntos", True) is not False
    casos: List[dict] = []
    primera = False
    if t == "gmail_nuevo":
        g, _ = e._google(n)
        etiqueta = g.id_etiqueta(c.get("etiqueta") or "INBOX") or ""
        vistos: List[str] = list(estado.get("vistos") or [])
        consulta = str(c.get("consulta") or "").strip()
        if not estado.get("historia"):
            primera = True
            estado["historia"] = str(g.perfil().get("historyId") or "")
            nuevos = g.buscar(consulta, etiqueta, maximo) if c.get("procesar_existentes") is True else []
        else:
            try:
                nuevos, estado["historia"] = g.historial(estado["historia"], etiqueta)
            except ErrorDeNegocio as ex:
                if "vencido" not in str(ex):
                    raise
                estado["historia"] = str(g.perfil().get("historyId") or "")
                nuevos = g.buscar(consulta, etiqueta, maximo)  # el historial venció: se revisan los recientes contra los vistos
            if consulta and nuevos:
                coinciden = set(g.buscar(consulta, etiqueta, 100))
                nuevos = [x for x in nuevos if x in coinciden]
        for gid in [x for x in nuevos if x not in vistos][:maximo]:
            vistos.append(gid)
            m = g.mensaje(gid, adj)
            caso = _guardar_caso(e.flujo, gid, {**m, "id": m.get("message_id") or gid, "gmail_id": gid, "gmail_hilo": m.get("hilo")}, f"gmail_nuevo:{e.flujo}")
            casos.append(caso)
        estado["vistos"] = vistos[-2000:]
    elif t == "outlook_nuevo":
        g, _ = e._graph(n)
        vistos = list(estado.get("vistos") or [])
        carpeta = str(c.get("carpeta") or "inbox")
        enlace = str(estado.get("delta") or "")
        if not enlace or estado.get("carpeta") != carpeta:
            primera = True
            desde = datetime.datetime.fromtimestamp(_reloj() - (86400 if c.get("procesar_existentes") is True else 0), datetime.timezone.utc)
            enlace = ""
            base = f"{c_ms.GRAPH}{g.usuario('/mailFolders/' + c_ms.q(g.carpeta(carpeta)) + '/messages/delta')}"
            url_inicial = base + "?" + urllib.parse.urlencode({"$select": "id,receivedDateTime", "$filter": f"receivedDateTime ge {desde.strftime('%Y-%m-%dT%H:%M:%SZ')}"})
            cambios, nuevo = g.delta(carpeta, url_inicial)
            if c.get("procesar_existentes") is not True:
                vistos += [str(x.get("id")) for x in cambios]
                cambios = []
        else:
            cambios, nuevo = g.delta(carpeta, enlace)
        filtro = c.get("filtro") or {}
        for x in cambios:
            gid = str(x.get("id") or "")
            if not gid or gid in vistos or len(casos) >= maximo:
                continue
            vistos.append(gid)
            m = c_ms.correo(g.mensaje(gid))
            if not _coincide(m, filtro):
                continue
            if m.pop("_tiene_adjuntos") and adj:
                import base64
                m["adjuntos"] = [{"nombre": a.get("name") or "adjunto", "tipo": a.get("contentType") or "", "_datos": base64.b64decode(a["contentBytes"])}
                                 for a in g.adjuntos(gid) if a.get("contentBytes") and not a.get("isInline") and (a.get("size") or 0) <= c_ms.MAX_ADJUNTO]
            casos.append(_guardar_caso(e.flujo, gid, m, f"outlook_nuevo:{e.flujo}"))
        estado.update({"delta": nuevo or enlace, "carpeta": carpeta, "vistos": vistos[-2000:]})
    else:  # calendar_evento_proximo
        minutos = int(c.get("minutos_antes") or 15)
        ahora = datetime.datetime.fromtimestamp(_reloj(), datetime.timezone.utc)
        hasta = ahora + datetime.timedelta(minutes=minutos)
        cred = e.cred(c.get("credencial"), CREDENCIALES_DE[t])
        consulta = fd._norm(str(c.get("consulta") or ""))
        cal = str(c.get("calendario") or "")
        if cred["tipo"].startswith("google"):
            g, _ = e._google(n)
            ev = g.eventos(cal or "primary", _iso(ahora), _iso(hasta + datetime.timedelta(minutes=1)), "", 50, ZONA)
        else:
            g, _ = e._graph(n)
            ev = g.eventos("" if cal == "primary" else cal, _iso(ahora), _iso(hasta + datetime.timedelta(minutes=1)), 50, ZONA)
        avisados: List[str] = list(estado.get("avisados") or [])
        primera = "avisados" not in estado
        for x in ev:
            if x.get("todo_el_dia") or x.get("estado") == "cancelled":
                continue
            try:
                ini = _fecha(x["inicio"], ZONA)
            except ErrorDeNegocio:
                continue
            if not (ahora < ini.astimezone(datetime.timezone.utc) <= hasta):
                continue
            if consulta and consulta not in fd._norm(f"{x.get('titulo', '')} {x.get('descripcion', '')}"):
                continue
            clave = f"{x.get('id')}|{x.get('inicio')}"
            if clave in avisados:
                continue
            avisados.append(clave)
            casos.append({"id": clave, "entrada": {"evento": x, "minutos_antes": minutos, "calendario": cal or "primary"}})
        estado["avisados"] = avisados[-1000:]
    estado["ultimo"] = _reloj()
    ruta.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "casos": casos, "primera": primera}


# =====================================================================
#   probar una conexión y analizar una especificación (para el servidor)
# =====================================================================

def probar_conexion(entrada: dict) -> dict:
    tipo, v = str(entrada.get("tipo") or ""), dict(entrada.get("valores") or {})
    cred = {"tipo": tipo, "valores": v, "creada_por_persona": entrada.get("creada_por_persona") is True, "nombre": "prueba"}
    try:
        externa = nx.probar_conexion(tipo, v, cred)  # FTP, S3, MongoDB, SQL Server, Oracle, Telegram, SendGrid, Mailgun, Twilio
        if externa is not None:
            return externa
        if tipo == "http_oauth2":
            c_oauth.cliente(str(v.get("token_url") or ""), str(v.get("client_id") or ""), str(v.get("client_secret") or ""), str(v.get("scope") or ""))
            return {"ok": True, "detalle": "el servidor de autorización entregó un token"}
        if tipo == "api_openapi":
            spec = cargar_spec(v, red_privada_de(cred))
            ops = c_openapi.operaciones(spec)
            extra = ""
            if v.get("auth") == "oauth2":
                autenticar(v, {}, {}, 20, red_privada_de(cred))
                extra = " · token OAuth2 recibido"
            return {"ok": True, "detalle": f"«{(spec.get('info') or {}).get('title', 'API')}»: {len(ops)} operación(es) en {base_api(v, spec) or '(sin URL base)'}{extra}",
                    "operaciones": len(ops)}
        if tipo == "google_servicio":
            g = c_google.Google(lambda a: c_oauth.google(cred, a, 20), 20)
            u = g.drive("GET", "/about", params={"fields": "user(emailAddress)"}).json()
            return {"ok": True, "detalle": f"cuenta de servicio {(u.get('user') or {}).get('emailAddress', '')}"}
        if tipo in ("google_oauth", "microsoft_oauth", "microsoft_graph"):
            if tipo == "google_oauth":
                p = c_google.Google(lambda a: c_oauth.google(cred, a, 20), 20).perfil()
                return {"ok": True, "detalle": f"Gmail de {p.get('emailAddress', '')}", "cuenta": p.get("emailAddress", "")}
            base = "/me" if tipo == "microsoft_oauth" else f"/users/{urllib.parse.quote(_buzon(v))}"
            u = c_ms.Graph(lambda: c_oauth.microsoft(cred, 20), base, 20).j("GET", f"{base}?$select=mail,userPrincipalName,displayName")
            cuenta = u.get("mail") or u.get("userPrincipalName") or ""
            if fd_vetada(cuenta):
                return {"ok": False, "detalle": f"bloqueado por seguridad: {cuenta} es de Positiva"}
            return {"ok": True, "detalle": f"Microsoft 365 de {u.get('displayName') or ''} <{cuenta}>", "cuenta": cuenta}
        if tipo in ("postgres", "mysql", "sqlite"):
            from conectores import sql as c_sql
            c_sql.ejecutar(tipo, v, "SELECT 1", {}, "lectura", 1, 15)
            return {"ok": True, "detalle": "la base respondió a SELECT 1"}
        if tipo == "imap":
            from conectores import correo as c_correo
            c_correo.leer_imap(v, "INBOX", {}, "", 1, False, 20)
            return {"ok": True, "detalle": "el buzón IMAP respondió (solo lectura)"}
        return {"ok": False, "detalle": f"para {tipo} no hay una prueba sin escribir: pruébala con un nodo en «Probar este paso»"}
    except (ErrorDeNegocio, ErrorTransitorio, OSError, ImportError) as ex:
        return {"ok": False, "detalle": str(ex)[:400]}


def _buzon(v: dict) -> str:
    from conectores import correo as c_correo
    return c_correo._buzon_graph(v)


def fd_vetada(cuenta: str) -> bool:
    from conectores.base import direccion_vetada
    return bool(direccion_vetada(cuenta))


def analizar_openapi(entrada: dict) -> dict:
    v = {k: entrada.get(k) for k in ("spec_url", "spec", "base_url", "red_privada")}
    try:
        spec = cargar_spec(v, v.get("red_privada") is True and entrada.get("creada_por_persona") is True)
        info = spec.get("info") or {}
        return {"ok": True, "titulo": info.get("title") or "", "version": info.get("version") or "", "base_url": base_api(v, spec),
                "operaciones": [{k: o[k] for k in ("id", "metodo", "ruta", "resumen", "parametros", "cuerpo", "escritura")} for o in c_openapi.operaciones(spec)][:500]}
    except (ErrorDeNegocio, ErrorTransitorio, OSError) as ex:
        return {"ok": False, "error": str(ex)[:400]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    entrada = json.loads(sys.stdin.read() or "{}")
    if sys.argv[1:2] == ["probar"]:
        r = probar_conexion(entrada)
    elif sys.argv[1:2] == ["analizar_openapi"]:
        r = analizar_openapi(entrada)
    else:
        r = {"ok": False, "error": "uso: nodos_integraciones.py probar | analizar_openapi (con JSON por stdin)"}
    # nada secreto sale: los valores de la conexión no se repiten en la respuesta
    texto = json.dumps(r, ensure_ascii=False, default=str)
    for k, x in (entrada.get("valores") or {}).items():
        if isinstance(x, str) and len(x) >= 6 and k in ("client_secret", "token", "clave", "refresh_token", "access_token", "json", "secret_key",
                                                         "api_key", "auth_token"):
            texto = texto.replace(x, "••••")
    print(texto)
