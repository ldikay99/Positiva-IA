"""
Los nodos de la fase 2 de integraciones (plan 5d): páginas sin API (web_leer,
rss_leer, rss_nuevo), almacenamiento (ftp_archivos, s3_archivos), datos
(mongo_consulta, mongo_escribir, bigquery_consulta, bigquery_ejecutar,
firestore_documentos) y mensajería transaccional (telegram_mensaje,
correo_transaccional, sms_twilio).

La ficha está en `especificacion_externos.py`. Este módulo no importa el motor:
`nodos_integraciones` le delega lo que el motor pregunta de estos tipos
(salidas, lo que deja, lo que lee, la validación, el sondeo y la prueba de la
conexión) y su clase `NodosIntegraciones` hereda de `NodosExternos`.

Reglas de siempre: toda escritura pasa por `_escribir` (sale solo si una
persona habilitó la escritura de la conexión; si no, queda armada y retenida);
nunca destinos de Positiva, SGDEA, metadatos ni IP internas; nunca sus
direcciones de correo ni los buzones protegidos.
"""
from __future__ import annotations

import datetime
import json
import mimetypes
import posixpath
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from conectores import almacen as c_almacen
from conectores import datos as c_datos
from conectores import oauth as c_oauth
from conectores import transaccional as c_tx
from conectores import web as c_web
from conectores.base import IPS_PERMITIDAS_PRUEBAS, ErrorDeNegocio, host_vetado, timeout_de
from especificacion_externos import CREDENCIALES_DE, DISPARADORES, ESCRITURA, LECTURA, TIPOS  # noqa: F401

ni: Any = None  # nodos_integraciones (lo enlaza él mismo al importarse)
OBJETOS = ("filtro", "proyeccion", "orden", "cambios", "documento", "datos")
EXT_TEXTO = (".txt", ".csv", ".json", ".md", ".xml", ".html", ".htm", ".log", ".tsv", ".yaml", ".yml")


def fd() -> Any:
    return ni.fd


# =====================================================================
#   lo que el motor necesita saber de cada tipo
# =====================================================================

def salidas(t: str, c: dict) -> Tuple[List[str], List[str]]:
    if t in ("web_leer", "rss_leer", "mongo_consulta", "bigquery_consulta"):
        return ["ok", "vacio"], []
    if t in ("ftp_archivos", "s3_archivos") and (c.get("operacion") or "listar") == "listar":
        return ["ok", "vacio"], []
    if t == "firestore_documentos" and (c.get("operacion") or "listar") != "guardar":
        return ["ok", "vacio"], []
    return ["ok"], []


def produce(t: str, c: dict, nid: str) -> Set[str]:
    if t in DISPARADORES:
        return {"entrada"}
    s = c.get("salida") or nid
    return {s} if isinstance(s, str) else set()


def referencias(t: str, c: dict, plantillas_en: Callable, ref_de_fuente: Callable) -> List[Tuple[str, Set[str]]]:
    if t in DISPARADORES:
        return []
    refs: List[Tuple[str, Set[str]]] = [(r, set()) for r in plantillas_en(c)]
    subir = t in ("ftp_archivos", "s3_archivos") and c.get("operacion") == "subir"
    for k in ("adjuntos", "archivos") + (("archivo",) if subir else ()):
        if k in c:
            refs += [(r, set()) for r in fd().np_.refs_archivos(c.get(k), ref_de_fuente)]
    for k in ("de", "documentos"):
        refs += [(r, set()) for r in ref_de_fuente(c.get(k))]
    for e in c.get("entradas") or []:
        if isinstance(e, str):
            refs.append((fd()._ref_limpia(e), set()))
    return refs


def _url_fija(c: dict, k: str, q: Callable[[str], None], plantilla: bool = True) -> None:
    u = c.get(k)
    if not isinstance(u, str) or not u.strip() or (plantilla and "{{" in u):
        return
    p = urllib.parse.urlsplit(u.strip())
    if p.hostname in IPS_PERMITIDAS_PRUEBAS:  # solo las pruebas lo llenan (servidores locales); en la plataforma está vacío
        return
    if p.scheme != "https" or not p.hostname:
        q(f"{k} tiene que ser una dirección https:// (p. ej. https://www.ejemplo.com/pagina)")
        return
    vetado = host_vetado(p.hostname)
    if vetado or p.hostname == "localhost" or p.hostname.endswith(".internal") or re.fullmatch(r"[\d.]+", p.hostname) or ":" in p.hostname:
        q(f"{k}: bloqueado por seguridad: {vetado or 'solo hosts públicos por su nombre (nunca IP ni localhost)'}")


def validar(t: str, c: dict, q: Callable[[str], None]) -> None:
    for k in OBJETOS if t.startswith("mongo_") else ("datos",) if t == "firestore_documentos" else ():
        if k in c and c[k] is not None and not isinstance(c[k], dict):
            q(f"{k} tiene que ser un objeto JSON {{…}}")
    if t == "web_leer":
        _url_fija(c, "url", q)
        if c.get("extraer") == "selector":
            sel = c.get("selector")
            if not (isinstance(sel, str) and sel.strip()):
                q("web_leer con extraer selector necesita selector (CSS, p. ej. «article h2 a»)")
            else:
                malo = c_web.revisar_selector(sel)
                if malo:
                    q(f"selector «{sel[:80]}» no es un selector CSS válido: {malo}")
        elif c.get("selector"):
            q("selector solo aplica con extraer: selector")
    elif t in ("rss_leer", "rss_nuevo"):
        _url_fija(c, "url", q, plantilla=t == "rss_leer")
        if t == "rss_nuevo" and isinstance(c.get("url"), str) and "{{" in c["url"]:
            q("rss_nuevo necesita la URL fija del feed (sin plantillas): el disparador la revisa solo")
    elif t in ("ftp_archivos", "s3_archivos"):
        op = c.get("operacion") or "listar"
        a = c.get("archivo")
        if op == "leer":
            if not (isinstance(a, str) and a.strip()):
                q(f"{t} con operacion leer necesita archivo (la ruta o clave en el servidor)")
        elif op == "subir":
            if not ni._vacio(a):
                fd().np_.problemas_archivos(c, "archivo", q)
                if c.get("contenido") or c.get("de"):
                    q(f"{t}: usa archivo (un archivo tal cual) o contenido/de (un texto), no los dos")
            else:
                if ni._vacio(c.get("nombre")):
                    q(f"{t} subir necesita archivo (p. ej. «{{{{documento}}}}») o nombre con contenido o de")
                if not (c.get("contenido") or c.get("de")):
                    q(f"{t} subir necesita archivo, contenido (plantilla) o de (variable)")
        if isinstance(c.get("nombre"), str) and ("/" in c["nombre"] or "\\" in c["nombre"]):
            q("nombre no puede llevar «/» (pon la carpeta en carpeta)")
        for k in ("carpeta", "archivo"):
            if isinstance(c.get(k), str) and ".." in c[k].replace("\\", "/").split("/"):
                q(f"{k} no puede salir de la carpeta base de la conexión (..)")
    elif t == "mongo_consulta":
        op = c.get("operacion") or "buscar"
        for k, donde in (("filtro", "filtro"), ("pipeline", "pipeline")):
            try:
                c_datos.revisar_mongo(c.get(k), donde)
            except ErrorDeNegocio as e:
                q(str(e))
        if op == "distintos" and ni._vacio(c.get("campo")):
            q("mongo_consulta distintos necesita campo")
        if op == "agregar":
            p = c.get("pipeline")
            if not (isinstance(p, list) and p and all(isinstance(x, dict) and len(x) == 1 for x in p)):
                q("mongo_consulta agregar necesita pipeline: una lista de etapas {\"$match\": …}, {\"$group\": …}")
        if isinstance(c.get("orden"), dict) and not all(v in (1, -1) for v in c["orden"].values()):
            q("orden: {campo: 1 o -1}")
    elif t == "mongo_escribir":
        op = c.get("operacion") or "insertar"
        for k in ("filtro", "cambios", "documento"):
            try:
                c_datos.revisar_mongo(c.get(k), k)
            except ErrorDeNegocio as e:
                q(str(e))
        if op == "insertar":
            if ni._vacio(c.get("documento")) and ni._vacio(c.get("documentos")):
                q("mongo_escribir insertar necesita documento {campo: valor} o documentos (una variable con la lista)")
        else:
            if ni._vacio(c.get("filtro")) and c.get("permitir_sin_filtro") is not True:
                q(f"mongo_escribir {op} sin filtro tocaría toda la colección: pon filtro (o permitir_sin_filtro: true)")
            if op == "actualizar":
                ca = c.get("cambios")
                if not (isinstance(ca, dict) and ca and all(str(k).startswith("$") for k in ca)):
                    q("mongo_escribir actualizar necesita cambios con operadores: {\"$set\": {…}}, {\"$inc\": {…}}…")
    elif t in ("bigquery_consulta", "bigquery_ejecutar"):
        fd()._validar_mapa_plantillas(c, "parametros", q)
        if isinstance(c.get("sql"), str) and re.search(r";\s*\S", c["sql"]):
            q(f"{t}: una sola sentencia (hay un «;» en medio)")
    elif t == "firestore_documentos":
        op = c.get("operacion") or "listar"
        if op == "obtener" and ni._vacio(c.get("documento")):
            q("firestore_documentos obtener necesita documento (su id)")
        if op == "guardar" and ni._vacio(c.get("datos")):
            q("firestore_documentos guardar necesita datos {campo: valor}")
        if op != "consultar" and (c.get("filtros") or c.get("orden")):
            q("filtros y orden solo aplican con operacion consultar")
    elif t == "correo_transaccional":
        fd().np_.problemas_archivos(c, "adjuntos", q)
    elif t == "telegram_mensaje":
        fd().np_.problemas_archivos(c, "archivos", q)


# =====================================================================
#   los métodos (NodosIntegraciones hereda de aquí)
# =====================================================================

class NodosExternos:
    # ------------------------------------------------------------- utilidades
    def _entregar_leido(self, n: dict, nombre: str, datos: bytes, tipo: str, origen: str, extra: dict) -> str:
        """Lo leído de un almacenamiento: como archivo de la ejecución o como texto (con la conversión y OCR del motor)."""
        c = self._cfg(n)
        tipo = tipo or mimetypes.guess_type(nombre)[0] or "application/octet-stream"
        if (c.get("como") or "texto") == "archivo":
            ficha = self._guardar_archivo(nombre, datos, tipo, origen)
            self.vars[self._sal(n)] = {**ficha, **extra}
            fd()._paso(n["id"], f"Archivo traído: {nombre}", "ok", f"{len(datos):,} bytes · id {ficha['id']}", archivo=ficha["id"])
            return "ok"
        texto = fd().c_hojas._texto(datos) if nombre.lower().endswith(EXT_TEXTO) else self._convertir(datos, nombre, c.get("ocr", True) is not False)
        tope = int(c.get("max_caracteres") or 60000)
        self.vars[self._sal(n)] = {"nombre": nombre, "tipo": tipo, "texto": texto[:tope], "truncado": len(texto) > tope, "bytes": len(datos), **extra}
        fd()._paso(n["id"], f"Leído: {nombre}", "ok" if texto.strip() else "aviso", f"{len(texto):,} caracteres\n\n{texto[:600]}")
        return "ok"

    def _a_subir(self, n: dict) -> Tuple[bytes, str, str, Optional[dict]]:
        """(bytes, tipo, nombre, ficha del archivo o None) de lo que se sube: un archivo de la ejecución o un texto, JSON o CSV."""
        c = self._cfg(n)
        archivo = None
        if c.get("archivo") not in (None, "", []):
            archivos, _ = self._para_envio(c["archivo"], n["tipo"], max_archivos=1, max_mb=c_almacen.MAX_BYTES // 1_000_000)
            if not archivos:
                raise ErrorDeNegocio(f"{n['tipo']}: el archivo no está (revisa la referencia, p. ej. «{{{{documento}}}}»)")
            archivo = archivos[0]
            datos, tipo = archivo["datos"], archivo["tipo"]
        else:
            valor, formato = self._contenido_a_guardar(c)
            if formato == "json":
                datos, tipo = json.dumps(valor, ensure_ascii=False, indent=2, default=str).encode(), "application/json"
            elif formato == "csv":
                datos, tipo = fd().c_archivos._csv(valor).encode(), "text/csv"
            else:
                datos, tipo = (valor if isinstance(valor, str) else fd()._a_texto(valor)).encode(), "text/plain"
        nombre = self.render(c.get("nombre") or "").strip() or (archivo or {}).get("nombre", "")
        if not nombre or "/" in nombre or "\\" in nombre:
            raise ErrorDeNegocio("nombre: el nombre del archivo, sin «/» (la carpeta va en carpeta)")
        return datos, tipo, nombre, archivo

    @staticmethod
    def _detalle_subida(carpeta: str, nombre: str, datos: bytes, tipo: str, archivo: Optional[dict], conflicto: str) -> dict:
        return {"carpeta": carpeta or "/", "nombre": nombre, "tipo": tipo, "bytes": len(datos), "conflicto": conflicto,
                **({"archivo": {k: archivo[k] for k in ("id", "nombre", "tipo", "tamano")}} if archivo else {"vista": datos[:600].decode("utf-8", "replace")})}

    def _listado(self, n: dict, items: List[dict], que: str) -> str:
        self.vars[self._sal(n)] = items
        fd()._paso(n["id"], f"{len(items)} elemento(s) en {que}", "ok" if items else "info",
                   "\n".join(f"· {'[carpeta] ' if x.get('tipo') == 'carpeta' else ''}{x['nombre']}" for x in items[:20]), total=len(items))
        return "ok" if items else "vacio"

    def _host_vetado_escritura(self, n: dict, sistema: str, host: str, detalle: dict) -> None:
        vetado = host_vetado(host)
        if vetado:
            e = ErrorDeNegocio(f"bloqueado por seguridad: {vetado}")
            self._retener_vetado(n, sistema, "escribir", host, detalle, e)
            raise e

    def _token_google(self, n: dict, alcance: str) -> Tuple[Callable[[], str], dict]:
        c = self._cfg(n)
        cred = self.cred(c.get("credencial"), CREDENCIALES_DE[n["tipo"]])
        if cred["tipo"] == "google_oauth":
            c_oauth.revisar_cuenta(cred["valores"])
        t = timeout_de(c)
        return (lambda: c_oauth.google(cred, [alcance], t)), cred

    # ======================================================= páginas sin API
    def web_leer(self, n: dict) -> str:
        c = self._cfg(n)
        url = self.render(c.get("url") or "").strip()
        t = timeout_de(c)
        host = urllib.parse.urlsplit(url).hostname or url[:60]
        r = self._r(n, lambda: c_web.bajar(url, timeout=t, max_bytes=int(c.get("max_kb") or 2000) * 1000), f"la página {host}")
        html, extraer, maximo = r["texto"], c.get("extraer") or "texto", int(c.get("max_elementos") or 100)
        es_html = "html" in r["tipo"] or "<html" in html[:2000].lower() or not r["tipo"]
        meta = c_web.metadatos(c_web._sopa(html), r["url"]) if es_html else {"titulo": "", "descripcion": "", "idioma": "", "canonica": ""}
        salida: Dict[str, Any] = {"url": r["url"], **meta, "bytes": r["bytes"], "status": r["status"]}
        if extraer == "texto":
            txt = c_web.texto_limpio(html, c.get("completo") is True) if es_html else html
            tope = int(c.get("max_caracteres") or 20000)
            salida.update(texto=txt[:tope], truncado=len(txt) > tope, total=len(txt))
            hay, resumen = bool(txt.strip()), txt[:700]
        elif extraer == "enlaces":
            xs = c_web.enlaces(html, r["url"], c.get("solo_mismo_sitio") is True, maximo)
            salida.update(enlaces=xs, total=len(xs))
            hay, resumen = bool(xs), "\n".join(f"· {x['texto'][:60]} → {x['url']}" for x in xs[:15])
        elif extraer == "tablas":
            xs = c_web.tablas(html, maximo)
            salida.update(tablas=xs, total=len(xs))
            hay, resumen = bool(xs), "\n".join(f"· {x['titulo'] or 'tabla'}: {len(x['filas'])} fila(s), columnas {', '.join(x['columnas'][:8])}" for x in xs[:10])
        else:
            xs = c_web.seleccionar(html, str(c.get("selector") or ""), r["url"], str(c.get("atributo") or ""), maximo)
            salida.update(elementos=xs, total=len(xs))
            hay, resumen = bool(xs), "\n".join(f"· {x.get('valor') or x['texto'][:100]}" for x in xs[:15])
        self.vars[self._sal(n)] = salida
        fd()._paso(n["id"], f"{meta['titulo'] or host} · {extraer}", "ok" if hay else "info",
                   f"{r['url']} · {r['bytes']:,} bytes · {r['ms']} ms (robots.txt permite)\n\n{resumen}", total=salida.get("total"), destino=r["url"])
        return "ok" if hay else "vacio"

    def _feed(self, url: str, timeout: float, condicional: Optional[Dict[str, str]] = None) -> Tuple[dict, dict]:
        aceptar = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, text/html;q=0.5"
        r = c_web.bajar(url, timeout=timeout, aceptar=aceptar, condicional=condicional)
        if r.get("sin_cambios"):
            return r, {"titulo": "", "enlace": "", "tipo": "", "elementos": []}
        if "html" in r["tipo"] and "<rss" not in r["texto"][:3000] and "<feed" not in r["texto"][:3000]:
            otro = c_web.descubrir_feed(r["texto"], r["url"])
            if not otro:
                raise ErrorDeNegocio(f"{url[:120]} es una página sin feed anunciado (no tiene <link rel=\"alternate\" type=\"application/rss+xml\">)")
            r = c_web.bajar(otro, timeout=timeout, aceptar=aceptar)
        return r, c_web.leer_feed(r.get("crudo") or r["texto"], r["url"])

    def rss_leer(self, n: dict) -> str:
        c = self._cfg(n)
        url = self.render(c.get("url") or "").strip()
        t = timeout_de(c)
        r, feed = self._r(n, lambda: self._feed(url, t), "el feed")
        elementos = feed["elementos"]
        if c.get("desde") and self.render(c["desde"]).strip():
            limite = ni._fecha(self.render(c["desde"]), ni.ZONA)
            elementos = [x for x in elementos if not x.get("fecha") or _despues(x["fecha"], limite)]
        if c.get("contiene") and self.render(c["contiene"]).strip():
            buscar = fd()._norm(self.render(c["contiene"]))
            elementos = [x for x in elementos if buscar in fd()._norm(f"{x.get('titulo', '')} {x.get('resumen', '')}")]
        elementos = elementos[:int(c.get("max") or 20)]
        self.vars[self._sal(n)] = {**{k: feed[k] for k in ("titulo", "enlace", "tipo", "descripcion")}, "url": r["url"], "elementos": elementos, "total": len(elementos)}
        fd()._paso(n["id"], f"{len(elementos)} elemento(s) de «{feed['titulo'] or r['url']}»", "ok" if elementos else "info",
                   "\n".join(f"· {x['fecha'][:16]} {x['titulo'][:100]}" for x in elementos[:15]), total=len(elementos), destino=r["url"])
        return "ok" if elementos else "vacio"

    def rss_nuevo(self, n: dict) -> str:
        self.vars["entrada"] = self.entrada if isinstance(self.entrada, dict) else {}
        el = self.vars["entrada"].get("elemento") or {}
        fd()._paso(n["id"], f"Nuevo en el feed: {el.get('titulo') or '(sin título)'}", "info", f"{el.get('fecha', '')} · {el.get('enlace', '')}")
        return "ok"

    # ========================================================= almacenamiento
    def _ftp(self, n: dict) -> Tuple[Callable[[], c_almacen.Ftp], dict]:
        c = self._cfg(n)
        cred = self.cred(c.get("credencial"), CREDENCIALES_DE["ftp_archivos"])
        t = timeout_de(c)
        return (lambda: c_almacen.Ftp(cred["valores"], ni.red_privada_de(cred), t)), cred

    def ftp_archivos(self, n: dict) -> str:
        c = self._cfg(n)
        abrir, cred = self._ftp(n)
        host = str(cred["valores"].get("host") or "")
        op = c.get("operacion") or "listar"
        carpeta = self.render(c.get("carpeta") or "").strip()
        if op == "listar":
            def listar() -> List[dict]:
                with abrir() as f:
                    return f.listar(carpeta, int(c.get("max") or 100))
            return self._listado(n, self._r(n, listar, "el FTP"), f"ftp://{host}{c_almacen.ruta_remota(cred['valores'].get('carpeta_base') or '/', carpeta)}")
        if op == "leer":
            ruta = self.render(c.get("archivo") if isinstance(c.get("archivo"), str) else "").strip()

            def leer() -> Tuple[str, bytes]:
                with abrir() as f:
                    return f.leer(ruta)
            completa, datos = self._r(n, leer, "el FTP")
            return self._entregar_leido(n, posixpath.basename(completa), datos, "", "ftp", {"ruta": completa})
        datos, tipo, nombre, archivo = self._a_subir(n)
        base = cred["valores"].get("carpeta_base") or "/"
        destino = c_almacen.ruta_remota(base, posixpath.join(carpeta or "/", nombre))
        detalle = {**self._detalle_subida(carpeta, nombre, datos, tipo, archivo, c.get("conflicto") or "renombrar"), "servidor": host, "ruta": destino}
        self._host_vetado_escritura(n, "ftp", host, detalle)

        def subir() -> dict:
            with abrir() as f:
                return f.subir(carpeta, nombre, datos, c.get("conflicto") or "renombrar")
        r = self._escribir(n, "ftp", "subir archivo", f"ftp://{host}{destino}", detalle, cred, subir)
        self._guardar_salida(n, r, ("ruta", "nombre", "bytes"))
        return "ok"

    def s3_archivos(self, n: dict) -> str:
        c = self._cfg(n)
        cred = self.cred(c.get("credencial"), CREDENCIALES_DE["s3_archivos"])
        s3 = c_almacen.S3(cred["valores"], ni.red_privada_de(cred), timeout_de(c))
        op = c.get("operacion") or "listar"
        carpeta = self.render(c.get("carpeta") or "").strip()
        if op == "listar":
            items = self._r(n, lambda: s3.listar(carpeta, int(c.get("max") or 100), c.get("recursivo") is True), "el almacenamiento S3")
            return self._listado(n, items, f"s3://{s3.bucket}/{s3.clave(carpeta, True) if carpeta or s3.prefijo else ''}")
        if op == "leer":
            ref = self.render(c.get("archivo") if isinstance(c.get("archivo"), str) else "").strip()
            clave, datos, tipo = self._r(n, lambda: s3.leer(ref), "el almacenamiento S3")
            return self._entregar_leido(n, clave.rsplit("/", 1)[-1] or "objeto", datos, tipo.split(";")[0], "s3", {"clave": clave, "bucket": s3.bucket})
        datos, tipo, nombre, archivo = self._a_subir(n)
        destino = s3.clave(posixpath.join(carpeta, nombre))
        detalle = {**self._detalle_subida(carpeta, nombre, datos, tipo, archivo, c.get("conflicto") or "renombrar"), "bucket": s3.bucket, "clave": destino,
                   "endpoint": s3.u.hostname}
        self._host_vetado_escritura(n, "s3", s3.u.hostname or "", detalle)
        r = self._escribir(n, "s3", "subir objeto", f"s3://{s3.bucket}/{destino}", detalle, cred,
                           lambda: s3.subir(carpeta, nombre, datos, tipo, c.get("conflicto") or "renombrar"))
        self._guardar_salida(n, r, ("clave", "nombre", "bytes", "etag"))
        return "ok"

    # =================================================================== datos
    def mongo_consulta(self, n: dict) -> str:
        c = self._cfg(n)
        cred = self.cred(c.get("credencial"), CREDENCIALES_DE["mongo_consulta"])
        op = c.get("operacion") or "buscar"
        filtro = self.render_profundo(c.get("filtro") or {})
        r = self._r(n, lambda: c_datos.mongo_leer(cred["valores"], self.render(c.get("coleccion") or "").strip(), op, filtro, c.get("proyeccion"),
                                                     c.get("orden"), self.render_profundo(c.get("pipeline") or []), str(c.get("campo") or ""),
                                                     int(c.get("max") or 100), timeout_de(c)), "MongoDB")
        if op == "contar":
            self.vars[self._sal(n)] = r
            fd()._paso(n["id"], f"{r['total']} documento(s)", "ok", json.dumps(filtro, ensure_ascii=False, default=str)[:400], total=r["total"])
            return "ok" if r["total"] else "vacio"
        lista = r.get("documentos") if op != "distintos" else r.get("valores")
        self.vars[self._sal(n)] = lista
        if r.get("truncado"):
            self.avisos.append(f"{n['id']}: la consulta trajo más de {c.get('max') or 100}; se cortó")
        fd()._paso(n["id"], f"{len(lista)} {'valor(es)' if op == 'distintos' else 'documento(s)'} de {c.get('coleccion')}", "ok" if lista else "info",
                   fd()._a_texto(lista[:3])[:700], total=len(lista), truncado=r.get("truncado"))
        return "ok" if lista else "vacio"

    def mongo_escribir(self, n: dict) -> str:
        c = self._cfg(n)
        cred = self.cred(c.get("credencial"), CREDENCIALES_DE["mongo_escribir"])
        op = c.get("operacion") or "insertar"
        coleccion = self.render(c.get("coleccion") or "").strip()
        filtro = self.render_profundo(c.get("filtro") or {})
        cambios = self.render_profundo(c.get("cambios") or {})
        if op == "insertar":
            docs = [self.render_profundo(c["documento"])] if c.get("documento") else self.valor_de(c.get("documentos"))
            docs = docs if isinstance(docs, list) else [docs] if isinstance(docs, dict) else []
            if not docs or not all(isinstance(d, dict) for d in docs):
                raise ErrorDeNegocio("mongo_escribir insertar: documentos tiene que ser un objeto o una lista de objetos")
            if len(docs) > 1000:
                raise ErrorDeNegocio(f"mongo_escribir: {len(docs)} documentos de una vez; el tope es 1.000")
        else:
            docs = []
            if not filtro and c.get("permitir_sin_filtro") is not True:
                raise ErrorDeNegocio(f"mongo_escribir {op}: el filtro quedó vacío al llenarlo; no se toca toda la colección")
        v = cred["valores"]
        for h, _ in c_datos.hosts_mongo(v):
            self._host_vetado_escritura(n, "mongodb", h, {"coleccion": coleccion, "operacion": op})
        detalle = {"base": v.get("base"), "coleccion": coleccion, "operacion": op, "varios": c.get("varios") is True,
                   **({"documentos": docs[:20], "cantidad": len(docs)} if docs else {}), **({"filtro": filtro} if filtro else {}), **({"cambios": cambios} if cambios else {})}
        r = self._escribir(n, "mongodb", f"{op} en MongoDB", f"mongodb:{v.get('host', '')}/{v.get('base', '')}.{coleccion}", detalle, cred,
                           lambda: c_datos.mongo_escribir(v, coleccion, op, filtro, docs, cambios, c.get("varios") is True, timeout_de(c)))
        self._guardar_salida(n, r, ("insertados", "ids", "coincidentes", "modificados", "borrados"))
        return "ok"

    def _bigquery(self, n: dict, alcance: str = c_datos.ALCANCE_BIGQUERY) -> Tuple[c_datos.BigQuery, dict, Dict[str, Any]]:
        c = self._cfg(n)
        token, cred = self._token_google(n, alcance)
        bq = c_datos.BigQuery(token, self.render(c.get("proyecto") or "").strip(), timeout_de(c), str(c.get("ubicacion") or ""))
        params = {k: self.render_profundo(v) for k, v in (c.get("parametros") or {}).items()}
        return bq, cred, params

    def bigquery_consulta(self, n: dict) -> str:
        c = self._cfg(n)
        bq, _, params = self._bigquery(n)
        r = self._r(n, lambda: bq.leer(str(c.get("sql") or ""), params, int(c.get("max") or 500)), "BigQuery")
        self.vars[self._sal(n)] = r
        if r["truncado"]:
            self.avisos.append(f"{n['id']}: la consulta trajo más de {c.get('max') or 500} filas; se cortó")
        fd()._paso(n["id"], f"{r['total']} fila(s) de BigQuery", "ok" if r["filas"] else "info",
                   f"columnas: {', '.join(r['columnas'])} · {r['bytes_procesados']:,} bytes procesados", total=r["total"], columnas=r["columnas"])
        return "ok" if r["filas"] else "vacio"

    def bigquery_ejecutar(self, n: dict) -> str:
        c = self._cfg(n)
        bq, cred, params = self._bigquery(n)
        sql = str(c.get("sql") or "")
        tipo = self._r(n, lambda: bq.revisar_escritura(sql, params), "BigQuery")  # en seco: falla aunque no se escriba
        detalle = {"proyecto": bq.proyecto, "tipo": tipo, "sql": sql[:2000], "parametros": params}
        r = self._escribir(n, "bigquery", f"{tipo} en BigQuery", f"bigquery:{bq.proyecto}", detalle, cred, lambda: bq.escribir(sql, params))
        self._guardar_salida(n, r, ("afectadas", "trabajo"), tipo=tipo)
        return "ok"

    def firestore_documentos(self, n: dict) -> str:
        c = self._cfg(n)
        token, cred = self._token_google(n, c_datos.ALCANCE_FIRESTORE)
        fs = c_datos.Firestore(token, self.render(c.get("proyecto") or "").strip(), str(c.get("base") or "(default)"), timeout_de(c))
        op = c.get("operacion") or "listar"
        col, doc = self.render(c.get("coleccion") or "").strip(), self.render(c.get("documento") or "").strip()
        maximo = int(c.get("max") or 100)
        if op == "guardar":
            datos = self.render_profundo(c.get("datos") or {})
            detalle = {"proyecto": fs.raiz.split("/")[1], "coleccion": col, "documento": doc or "(nuevo)", "datos": datos, "combinar": c.get("combinar", True) is not False}
            r = self._escribir(n, "firestore", "guardar documento", f"firestore:{col}/{doc or '(nuevo)'}", detalle, cred,
                               lambda: fs.guardar(col, doc, datos, c.get("combinar", True) is not False))
            self._guardar_salida(n, r, ("id", "ruta"))
            return "ok"
        if op == "obtener":
            d = self._r(n, lambda: fs.obtener(col, doc), "Firestore")
            self.vars[self._sal(n)] = d or {}
            fd()._paso(n["id"], f"{col}/{doc}: {'encontrado' if d else 'no existe'}", "ok" if d else "info", fd()._a_texto(d or {})[:700])
            return "ok" if d else "vacio"
        if op == "consultar":
            filtros = [{**f, "valor": self.render_profundo(f.get("valor"))} for f in c.get("filtros") or []]
            docs = self._r(n, lambda: fs.consultar(col, filtros, c.get("orden") or [], maximo), "Firestore")
        else:
            docs = self._r(n, lambda: fs.listar(col, maximo), "Firestore")
        self.vars[self._sal(n)] = docs
        fd()._paso(n["id"], f"{len(docs)} documento(s) de {col}", "ok" if docs else "info", fd()._a_texto(docs[:3])[:700], total=len(docs))
        return "ok" if docs else "vacio"

    # ================================================= mensajería transaccional
    def telegram_mensaje(self, n: dict) -> str:
        c = self._cfg(n)
        cred = self.cred(c.get("credencial"), CREDENCIALES_DE["telegram_mensaje"])
        v = cred["valores"]
        chat = self.render(c.get("chat") or "").strip() or str(v.get("chat_id") or "").strip()
        if not chat:
            raise ErrorDeNegocio("telegram_mensaje necesita chat (o un chat por defecto en la conexión)")
        texto = self.render(c.get("texto") or "")
        archivos, resumen = self._para_envio(c.get("archivos"), "el mensaje de Telegram", max_mb=45) if c.get("archivos") not in (None, "", []) else ([], [])
        detalle = {"chat": chat, "texto": texto[:4000], "formato": c.get("formato") or "texto", "partes": len(c_tx.trozos(texto, c_tx.MAX_TELEGRAM)),
                   **({"archivos": resumen} if resumen else {})}
        r = self._escribir(n, "telegram", "enviar mensaje", f"telegram:{chat}", detalle, cred,
                           lambda: c_tx.telegram(str(v.get("token") or ""), chat, texto, str(c.get("formato") or "texto"), archivos,
                                                 c.get("silencioso") is True, timeout_de(c)))
        self.mensajes.append({"nodo": n["id"], "operacion": "telegram_mensaje", "para": chat, "cuerpo": texto[:2000], "adjuntos": resumen,
                              "enviado": not r["bloqueado"], "bloqueado": r["bloqueado"]})
        self._guardar_salida(n, r, ("ids", "mensajes"), enviado=not r["bloqueado"], chat=chat)
        return "ok"

    def correo_transaccional(self, n: dict) -> str:
        c = self._cfg(n)
        cred = self.cred(c.get("credencial"), CREDENCIALES_DE["correo_transaccional"])
        v = cred["valores"]
        m = {k: self.render(c.get(k) or "").strip() for k in ("para", "cc", "cco", "asunto", "nombre_remitente", "responder_a")}
        m["cuerpo"] = self.render(c.get("cuerpo") or "")
        if c.get("html"):
            m["html"] = self.render(c["html"])
        m["remitente"] = self.render(c.get("remitente") or "").strip() or str(v.get("remitente") or "").strip()
        m["nombre_remitente"] = m["nombre_remitente"] or str(v.get("nombre_remitente") or "")
        servicio = "SendGrid" if cred["tipo"] == "sendgrid" else "Mailgun"
        adjuntos, resumen = self._para_envio(c.get("adjuntos"), "el correo") if c.get("adjuntos") not in (None, "", []) else ([], [])
        detalle = {**{k: m.get(k, "") for k in ("remitente", "para", "cc", "cco", "asunto", "cuerpo")}, **({"html": m["html"][:20000]} if m.get("html") else {}),
                   "servicio": servicio, **({"adjuntos": resumen, "adjuntos_bytes": sum(a["tamano"] for a in resumen)} if resumen else {})}
        try:
            if not m["remitente"]:
                raise ErrorDeNegocio("falta remitente (en el nodo o en la conexión)")
            c_tx.revisar_correos(m)
            if cred["tipo"] == "mailgun":
                c_tx._dominio(str(v.get("dominio") or ""))
        except ErrorDeNegocio as e:
            self._retener_vetado(n, cred["tipo"], "enviar correo", m.get("para", ""), detalle, e)
            raise
        if cred["tipo"] == "sendgrid":
            hacer = lambda: c_tx.sendgrid(str(v.get("api_key") or ""), m, adjuntos, timeout_de(c))  # noqa: E731
        else:
            hacer = lambda: c_tx.mailgun(str(v.get("api_key") or ""), str(v.get("dominio") or ""), str(v.get("region") or "us"), m, adjuntos, timeout_de(c))  # noqa: E731
        r = self._escribir(n, cred["tipo"], "enviar correo", m["para"], detalle, cred, hacer)
        self.mensajes.append({**{k: detalle.get(k) for k in ("para", "cc", "asunto", "cuerpo", "adjuntos")}, "nodo": n["id"], "operacion": "correo_transaccional",
                              "enviado": not r["bloqueado"], "bloqueado": r["bloqueado"]})
        self._guardar_salida(n, r, ("id",), enviado=not r["bloqueado"], para=m["para"], asunto=m["asunto"], adjuntos=resumen)
        return "ok"

    def sms_twilio(self, n: dict) -> str:
        c = self._cfg(n)
        cred = self.cred(c.get("credencial"), CREDENCIALES_DE["sms_twilio"])
        v = cred["valores"]
        texto = self.render(c.get("texto") or "")
        if len(texto) > c_tx.MAX_SMS:
            raise ErrorDeNegocio(f"el SMS tiene {len(texto)} caracteres; el tope es {c_tx.MAX_SMS}")
        para = c_tx.telefono(self.render(c.get("para") or ""))
        remitente = self.render(c.get("remitente") or "").strip() or str(v.get("remitente") or "")
        detalle = {"para": para, "remitente": remitente, "texto": texto, "caracteres": len(texto), "segmentos_aprox": max(1, -(-len(texto) // 153)) if len(texto) > 160 else 1}
        r = self._escribir(n, "twilio", "enviar SMS", f"sms:{para}", detalle, cred,
                           lambda: c_tx.twilio_sms(str(v.get("account_sid") or ""), str(v.get("auth_token") or ""), remitente, para, texto, timeout_de(c)))
        self.mensajes.append({"nodo": n["id"], "operacion": "sms_twilio", "para": para, "cuerpo": texto, "enviado": not r["bloqueado"], "bloqueado": r["bloqueado"]})
        self._guardar_salida(n, r, ("id", "estado", "segmentos"), enviado=not r["bloqueado"], para=para)
        return "ok"


def _despues(fecha: str, limite: datetime.datetime) -> bool:
    try:
        d = datetime.datetime.fromisoformat(fecha)
    except ValueError:
        return True
    if d.tzinfo is None:
        d = d.replace(tzinfo=limite.tzinfo)
    return d >= limite


# =====================================================================
#   el disparador rss_nuevo: sondeo sin repetir
# =====================================================================

def sondear(e: Any, n: dict, ruta: Path) -> dict:
    c = n.get("config") or {}
    estado = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    url = str(c.get("url") or "").strip()
    maximo = max(1, min(50, int(c.get("max_por_sondeo") or 10)))
    if estado.get("url") != url:  # otra URL: se empieza de nuevo
        estado = {"url": url}
    primera = "vistos" not in estado
    vistos: List[str] = list(estado.get("vistos") or [])
    r, feed = e._feed(url, timeout_de(c), {"etag": estado.get("etag", ""), "modificado": estado.get("modificado", "")} if not primera else None)
    casos: List[dict] = []
    if not r.get("sin_cambios"):
        buscar = fd()._norm(str(c.get("contiene") or ""))
        nuevos = [x for x in feed["elementos"] if c_web.clave_elemento(x) not in vistos]
        if primera and c.get("procesar_existentes") is not True:
            vistos += [c_web.clave_elemento(x) for x in nuevos]
            nuevos = []
        for x in reversed(nuevos):  # del más viejo al más nuevo (los feeds traen primero lo último)
            clave = c_web.clave_elemento(x)
            if len(casos) >= maximo:
                break
            vistos.append(clave)
            if buscar and buscar not in fd()._norm(f"{x.get('titulo', '')} {x.get('resumen', '')}"):
                continue
            casos.append({"id": clave, "asunto": x.get("titulo", ""), "entrada": {"elemento": x, "feed": {"titulo": feed["titulo"], "enlace": feed["enlace"], "url": r["url"]}}})
        estado.update(etag=r.get("etag", ""), modificado=r.get("modificado", ""))
    estado["vistos"] = vistos[-2000:]
    estado["ultimo"] = ni._reloj()
    ruta.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "casos": casos, "primera": primera, "sin_cambios": bool(r.get("sin_cambios"))}


# =====================================================================
#   probar una conexión (sin escribir nada)
# =====================================================================

def probar_conexion(tipo: str, v: dict, cred: dict) -> Optional[dict]:
    """{ok, detalle} para los tipos de esta fase, o None si no es uno de ellos."""
    if tipo == "ftp":
        with c_almacen.Ftp(v, ni.red_privada_de(cred), 20) as f:
            return {"ok": True, "detalle": f"el FTP respondió (carpeta actual {f.probar()})"}
    if tipo == "s3":
        s3 = c_almacen.S3(v, ni.red_privada_de(cred), 20)
        s3.listar("", 1)
        return {"ok": True, "detalle": f"el bucket {s3.bucket} responde (lectura)"}
    if tipo == "mongodb":
        return {"ok": True, "detalle": c_datos.mongo_probar(v)}
    if tipo in ("sqlserver", "oracle"):
        from conectores import sql as c_sql
        c_sql.ejecutar(tipo, v, "SELECT 1 FROM DUAL" if tipo == "oracle" else "SELECT 1 AS uno", {}, "lectura", 1, 15)
        return {"ok": True, "detalle": "la base respondió a SELECT 1 (solo lectura)"}
    if tipo == "telegram_bot":
        return {"ok": True, "detalle": c_tx.telegram_probar(str(v.get("token") or ""))}
    if tipo == "sendgrid":
        return {"ok": True, "detalle": c_tx.sendgrid_probar(str(v.get("api_key") or ""))}
    if tipo == "mailgun":
        return {"ok": True, "detalle": c_tx.mailgun_probar(str(v.get("api_key") or ""), str(v.get("dominio") or ""), str(v.get("region") or "us"))}
    if tipo == "twilio":
        return {"ok": True, "detalle": c_tx.twilio_probar(str(v.get("account_sid") or ""), str(v.get("auth_token") or ""))}
    return None
