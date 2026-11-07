"""
Páginas sin API (plan 5d): leer una página web pública y feeds RSS/Atom, solo
lectura.

Reglas que no se negocian:
- solo GET por https a hosts públicos: el destino se revisa con `destino_seguro`
  (nunca Positiva, SGDEA, metadatos ni IP internas) y se conecta a ESA IP;
- se respeta robots.txt (agente «Positiva-IA»): 401/403 o 5xx al pedirlo cuentan
  como «no se permite»; 404 como «todo permitido»;
- frecuencia: entre dos pedidos al mismo host pasan al menos INTERVALO_S segundos
  (o el Crawl-delay de robots.txt, si es mayor), también entre procesos (marca en
  <estado>/web/ultimas.json); si la espera pasa de ESPERA_MAX_S, es un fallo pasajero;
- tamaño: MAX_BYTES por respuesta (tope MAX_BYTES_TOPE) y solo tipos de texto;
- redirecciones: hasta 3, cada salto se revisa de nuevo (destino y robots).

HTML con BeautifulSoup (MIT) y el analizador de la biblioteca estándar; los
selectores CSS los resuelve soupsieve (MIT). Los feeds se leen con ElementTree
y se rechaza cualquier DOCTYPE con entidades (sin XXE ni «billion laughs»).
"""
from __future__ import annotations

import datetime
import email.utils
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.robotparser
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import rest
from .base import ErrorDeNegocio, ErrorTransitorio, host_vetado, ruta_estado

AGENTE = "Positiva-IA"
USER_AGENT = "Positiva-IA/flujos (lectura; respeta robots.txt)"
INTERVALO_S = float(os.environ.get("WEB_INTERVALO_S") or 2.0)
ESPERA_MAX_S = 15.0
MAX_BYTES = 2_000_000
MAX_BYTES_TOPE = 5_000_000
MAX_REDIRECCIONES = 3
ROBOTS_TTL_S = 3600
TIPOS_TEXTO = ("text/", "application/xhtml", "application/xml", "application/rss", "application/atom", "application/rdf", "application/json",
               "application/feed")

dormir: Callable[[float], None] = time.sleep  # las pruebas lo cambian
reloj: Callable[[], float] = time.time
_robots: Dict[str, Tuple[float, Any]] = {}


# ------------------------------------------------------------------ red

def _revisar(url: str) -> urllib.parse.SplitResult:
    u = rest.revisar_url(url)
    host = (u.hostname or "").lower().rstrip(".")
    vetado = host_vetado(host)
    if vetado:
        raise ErrorDeNegocio(f"bloqueado por seguridad: {vetado}")
    return u


def _ultimas() -> Tuple[Any, Dict[str, float]]:
    ruta = ruta_estado() / "web" / "ultimas.json"
    try:
        return ruta, json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ruta, {}


def esperar_turno(host: str, intervalo: float) -> float:
    """Espera lo que falte para pedir otra vez a este host; devuelve los segundos esperados."""
    ruta, marcas = _ultimas()
    falta = float(marcas.get(host) or 0) + intervalo - reloj()
    if falta > ESPERA_MAX_S:
        raise ErrorTransitorio(f"{host}: hay que esperar {falta:.0f} s entre pedidos (límite de frecuencia)")
    if falta > 0:
        dormir(falta)
    ahora = reloj()
    marcas = {h: t for h, t in marcas.items() if ahora - float(t) < 86400}
    marcas[host] = ahora
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(marcas), encoding="utf-8")
        os.replace(tmp, ruta)
    except OSError:
        pass
    return max(0.0, falta)


def _robots_de(u: urllib.parse.SplitResult, timeout: float) -> Any:
    origen = f"{u.scheme}://{u.netloc}"
    x = _robots.get(origen)
    if x and reloj() - x[0] < ROBOTS_TTL_S:
        return x[1]
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = rest.pedir("GET", origen + "/robots.txt", timeout=timeout, errores=False, max_bytes=500_000, servicio=u.hostname or "",
                       headers={"User-Agent": USER_AGENT, "Accept": "text/plain, */*"})
        status = r.status
    except ErrorTransitorio:
        status = 503
    if status in (401, 403) or status >= 500:
        rp.disallow_all = True
    elif status >= 400 or 300 <= status < 400:
        rp.allow_all = True
    else:
        rp.parse(r.text.splitlines())
    _robots[origen] = (reloj(), rp)
    return rp


def permitido(url: str, timeout: float = 20.0) -> Tuple[bool, float]:
    """(¿robots.txt deja leer esta URL?, Crawl-delay en segundos o 0)."""
    u = _revisar(url)
    rp = _robots_de(u, timeout)
    ok = rp.can_fetch(AGENTE, url)
    try:
        demora = float(rp.crawl_delay(AGENTE) or 0)
    except (TypeError, ValueError):
        demora = 0.0
    return ok, min(demora, 60.0)


def bajar(url: str, *, timeout: float = 20.0, max_bytes: int = MAX_BYTES, encabezados: Optional[Dict[str, str]] = None,
          aceptar: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5", condicional: Optional[Dict[str, str]] = None) -> dict:
    """GET de una página o feed con robots, frecuencia, tamaño y redirecciones revisadas. {url, status, tipo, texto, bytes, ms, etag, modificado}."""
    max_bytes = max(1000, min(int(max_bytes or MAX_BYTES), MAX_BYTES_TOPE))
    actual = str(url or "").strip()
    for salto in range(MAX_REDIRECCIONES + 1):
        u = _revisar(actual)
        ok, demora = permitido(actual, timeout)
        if not ok:
            raise ErrorDeNegocio(f"robots.txt de {u.hostname} no deja leer {u.path or '/'}: no se lee")
        esperar_turno((u.hostname or "").lower(), max(INTERVALO_S, demora))
        cab = {"User-Agent": USER_AGENT, "Accept": aceptar, "Accept-Language": "es,en;q=0.7", **(encabezados or {})}
        if condicional and salto == 0:
            if condicional.get("etag"):
                cab["If-None-Match"] = condicional["etag"]
            if condicional.get("modificado"):
                cab["If-Modified-Since"] = condicional["modificado"]
        try:
            r = rest.pedir("GET", actual, headers=cab, timeout=timeout, errores=False, max_bytes=max_bytes, servicio=u.hostname or "")
        except ErrorDeNegocio as e:
            if "devolvió más de" in str(e):
                raise ErrorDeNegocio(f"{u.hostname}: la página pasa de {max_bytes // 1000:,} KB (sube max_kb, hasta {MAX_BYTES_TOPE // 1000:,})") from e
            raise
        if r.status in (301, 302, 303, 307, 308) and r.encabezados.get("location"):
            actual = urllib.parse.urljoin(actual, r.encabezados["location"])
            continue
        if r.status == 304:
            return {"url": actual, "status": 304, "tipo": "", "texto": "", "bytes": 0, "ms": r.ms, "sin_cambios": True,
                    "etag": (condicional or {}).get("etag", ""), "modificado": (condicional or {}).get("modificado", "")}
        if r.status in (408, 425, 429) or r.status >= 500:
            raise ErrorTransitorio(f"{u.hostname} respondió {r.status}")
        if r.status >= 400:
            raise ErrorDeNegocio(f"{u.hostname} respondió {r.status} al pedir {u.path or '/'}")
        tipo = r.encabezados.get("content-type", "").split(";")[0].strip().lower()
        if tipo and not tipo.startswith(TIPOS_TEXTO) and not tipo.endswith(("+xml", "+json")):
            raise ErrorDeNegocio(f"{actual[:120]} no es una página ni un feed (es {tipo}): para bajar archivos usa http con descargar")
        return {"url": actual, "status": r.status, "tipo": tipo, "texto": r.text, "crudo": r.content, "bytes": len(r.content), "ms": r.ms,
                "etag": r.encabezados.get("etag", ""), "modificado": r.encabezados.get("last-modified", "")}
    raise ErrorDeNegocio(f"{url[:120]} redirige más de {MAX_REDIRECCIONES} veces: no se sigue")


# ------------------------------------------------------------------ HTML

_RUIDO = ("script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed", "form", "button", "input", "select")
_MARCO = ("nav", "header", "footer", "aside")
_BLOQUES = {"p", "div", "section", "article", "main", "li", "ul", "ol", "table", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6",
            "blockquote", "pre", "dd", "dt", "figcaption", "hr", "address"}


def _sopa(html: str) -> Any:
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def _espacios(t: str) -> str:
    t = re.sub(r"[ \t\r\f\v ]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _texto_de(nodo: Any) -> str:
    """El texto de un elemento con saltos en los bloques (sin pegar palabras)."""
    from bs4 import NavigableString, Tag

    partes: List[str] = []

    def ir(x: Any) -> None:
        for h in x.children:
            if isinstance(h, NavigableString):
                if type(h).__name__ in ("Comment", "Doctype", "Declaration", "ProcessingInstruction", "CData"):
                    continue
                partes.append(str(h))
            elif isinstance(h, Tag):
                if h.name in _RUIDO:
                    continue
                bloque = h.name in _BLOQUES
                if bloque:
                    partes.append("\n")
                if h.name in ("td", "th"):
                    partes.append(" ")
                ir(h)
                if bloque:
                    partes.append("\n")

    ir(nodo)
    return _espacios("".join(partes))


def metadatos(s: Any, url: str) -> dict:
    titulo = s.title.get_text(" ", strip=True) if s.title else ""
    desc = ""
    for attrs in ({"name": "description"}, {"property": "og:description"}):
        m = s.find("meta", attrs=attrs)
        if m and m.get("content"):
            desc = str(m["content"]).strip()
            break
    idioma = str((s.html or {}).get("lang") or "") if s.html else ""
    canon = s.find("link", rel="canonical")
    return {"titulo": titulo[:300], "descripcion": desc[:600], "idioma": idioma[:20],
            "canonica": urllib.parse.urljoin(url, canon["href"]) if canon and canon.get("href") else ""}


def texto_limpio(html: str, completo: bool = False) -> str:
    """El texto legible: sin scripts ni estilos; sin menú, encabezado ni pie salvo `completo`; prefiere <main> o <article>."""
    s = _sopa(html)
    for t in s(_RUIDO):
        t.decompose()
    cuerpo = s.body or s
    if not completo:
        principal = s.find("main") or s.find("article") or s.find(attrs={"role": "main"})
        if principal:
            cuerpo = principal
        else:
            for t in cuerpo(_MARCO):
                t.decompose()
    return _texto_de(cuerpo)


def enlaces(html: str, base: str, solo_mismo_host: bool = False, max_n: int = 200) -> List[dict]:
    s = _sopa(html)
    host = (urllib.parse.urlsplit(base).hostname or "").lower()
    r, vistos = [], set()
    for a in s.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        absoluta = urllib.parse.urljoin(base, href).split("#")[0]
        if not absoluta.startswith(("http://", "https://")) or absoluta in vistos:
            continue
        h = (urllib.parse.urlsplit(absoluta).hostname or "").lower()
        if solo_mismo_host and h != host:
            continue
        vistos.add(absoluta)
        r.append({"texto": a.get_text(" ", strip=True)[:200], "url": absoluta, "externo": h != host})
        if len(r) >= max_n:
            break
    return r


def tablas(html: str, max_tablas: int = 20, max_filas: int = 500) -> List[dict]:
    """Cada <table> como {titulo, columnas, filas: [{columna: valor}]}; si no hay encabezado, columnas col1, col2…"""
    s = _sopa(html)
    r = []
    for t in s.find_all("table")[:max_tablas]:
        filas = []
        for tr in t.find_all("tr"):
            if tr.find_parent("table") is not t:
                continue  # las de una tabla anidada van con ella
            celdas = tr.find_all(["th", "td"], recursive=False)
            if celdas:
                filas.append(([c.name == "th" for c in celdas], [_espacios(c.get_text(" ", strip=True)) for c in celdas]))
        if not filas:
            continue
        if all(filas[0][0]):
            columnas = [x or f"col{i + 1}" for i, x in enumerate(filas[0][1])]
            datos = filas[1:]
        else:
            ancho = max(len(f[1]) for f in filas)
            columnas = [f"col{i + 1}" for i in range(ancho)]
            datos = filas
        vistas: Dict[str, int] = {}
        unicas = []
        for c in columnas:
            vistas[c] = vistas.get(c, 0) + 1
            unicas.append(c if vistas[c] == 1 else f"{c}_{vistas[c]}")
        cap = t.find("caption")
        r.append({"titulo": cap.get_text(" ", strip=True) if cap else "", "columnas": unicas,
                  "filas": [dict(zip(unicas, f[1] + [""] * (len(unicas) - len(f[1])))) for f in datos[:max_filas]], "total": len(datos)})
    return r


def seleccionar(html: str, selector: str, base: str, atributo: str = "", max_n: int = 200) -> List[dict]:
    s = _sopa(html)
    try:
        elementos = s.select(selector, limit=max_n)
    except Exception as e:  # soupsieve.SelectorSyntaxError y compañía
        raise ErrorDeNegocio(f"el selector CSS «{selector[:120]}» no es válido: {str(e).splitlines()[0][:160]}") from e
    r = []
    for e in elementos:
        x: Dict[str, Any] = {"texto": _texto_de(e)[:5000], "etiqueta": e.name}
        for k in ("href", "src"):
            if e.get(k):
                x[k] = urllib.parse.urljoin(base, str(e[k]))
        if atributo:
            v = e.get(atributo)
            v = " ".join(v) if isinstance(v, list) else v
            x["valor"] = urllib.parse.urljoin(base, str(v)) if v and atributo in ("href", "src") else (v or "")
        r.append(x)
    return r


def revisar_selector(selector: str) -> Optional[str]:
    """Por qué un selector CSS no vale, o None (para validar sin bajar la página)."""
    try:
        import soupsieve
        soupsieve.compile(selector)
        return None
    except ImportError:
        return None
    except Exception as e:
        return str(e).splitlines()[0][:160]


# ------------------------------------------------------------------ feeds

_NS = {"atom": "http://www.w3.org/2005/Atom", "rss1": "http://purl.org/rss/1.0/", "dc": "http://purl.org/dc/elements/1.1/",
       "content": "http://purl.org/rss/1.0/modules/content/", "media": "http://search.yahoo.com/mrss/"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _hijo(e: Any, *nombres: str) -> Any:
    for h in list(e):
        if _local(h.tag) in nombres:
            return h
    return None


def _txt(e: Any) -> str:
    return "".join(e.itertext()).strip() if e is not None else ""


def _fecha_iso(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    try:
        d = email.utils.parsedate_to_datetime(v)
        return d.isoformat() if d else v
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromisoformat(v.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return v


def _resumen(html: str, tope: int = 1500) -> str:
    if not html:
        return ""
    if "<" not in html:
        return _espacios(html)[:tope]
    try:
        return _texto_de(_sopa(html))[:tope]
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)[:tope]


def leer_feed(texto: str, base: str) -> dict:
    """RSS 2.0, RSS 1.0 (RDF) o Atom → {titulo, enlace, descripcion, tipo, elementos: [{id, titulo, enlace, fecha, autor, resumen, categorias}]}."""
    if isinstance(texto, (bytes, bytearray)):  # los bytes tal cual: ElementTree respeta el encoding de la declaración
        crudo = bytes(texto).lstrip(b"\xef\xbb\xbf \r\n\t")
        cabeza = crudo[:5000].decode("latin-1")
    else:
        t = texto.lstrip("﻿ \r\n\t")
        crudo = re.sub(r"^<\?xml[^>]*\?>", "", t).encode("utf-8")
        cabeza = t[:5000]
    if re.search(r"<!ENTITY", cabeza, re.I):
        raise ErrorDeNegocio("el feed declara entidades XML (<!ENTITY>): no se lee por seguridad")
    try:
        raiz = ET.fromstring(crudo)
    except ET.ParseError as e:
        raise ErrorDeNegocio(f"no es un feed RSS/Atom válido: {e}") from e
    nombre = _local(raiz.tag).lower()
    elementos: List[dict] = []
    if nombre == "feed":  # Atom
        tipo = "atom"
        info = raiz
        for x in raiz:
            if _local(x.tag) != "entry":
                continue
            enlace = ""
            for l in x:
                if _local(l.tag) == "link" and l.get("rel", "alternate") == "alternate" and l.get("href"):
                    enlace = urllib.parse.urljoin(base, l.get("href"))
                    break
            autor = _hijo(x, "author")
            cont = _hijo(x, "summary")
            if cont is None:
                cont = _hijo(x, "content")
            elementos.append({"id": _txt(_hijo(x, "id")) or enlace, "titulo": _txt(_hijo(x, "title")), "enlace": enlace,
                              "fecha": _fecha_iso(_txt(_hijo(x, "published")) or _txt(_hijo(x, "updated"))),
                              "autor": _txt(_hijo(autor, "name")) if autor is not None else "", "resumen": _resumen(_txt(cont)),
                              "categorias": [c.get("term") for c in x if _local(c.tag) == "category" and c.get("term")]})
        enlace_feed = next((urllib.parse.urljoin(base, l.get("href")) for l in raiz if _local(l.tag) == "link" and l.get("rel", "alternate") == "alternate" and l.get("href")), "")
        titulo, desc = _txt(_hijo(info, "title")), _txt(_hijo(info, "subtitle"))
    elif nombre in ("rss", "rdf"):
        tipo = "rss" if nombre == "rss" else "rss1"
        canal = _hijo(raiz, "channel")
        if canal is None:
            raise ErrorDeNegocio("el RSS no tiene <channel>")
        items = [x for x in (canal if nombre == "rss" else raiz) if _local(x.tag) == "item"]
        for x in items:
            enlace = urllib.parse.urljoin(base, _txt(_hijo(x, "link"))) if _txt(_hijo(x, "link")) else ""
            guid = _txt(_hijo(x, "guid")) or (x.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about") or "")
            cont = _hijo(x, "encoded") if _hijo(x, "description") is None else _hijo(x, "description")
            elementos.append({"id": guid or enlace or hashlib.sha1((_txt(_hijo(x, "title")) + _txt(cont)).encode()).hexdigest(),
                              "titulo": _txt(_hijo(x, "title")), "enlace": enlace,
                              "fecha": _fecha_iso(_txt(_hijo(x, "pubDate")) or _txt(_hijo(x, "date"))),
                              "autor": _txt(_hijo(x, "creator")) or _txt(_hijo(x, "author")), "resumen": _resumen(_txt(cont)),
                              "categorias": [_txt(c) for c in x if _local(c.tag) == "category" and _txt(c)]})
        titulo, enlace_feed, desc = _txt(_hijo(canal, "title")), _txt(_hijo(canal, "link")), _txt(_hijo(canal, "description"))
    else:
        raise ErrorDeNegocio(f"no es un feed RSS ni Atom (la raíz es <{nombre}>)")
    return {"titulo": titulo[:300], "enlace": enlace_feed, "descripcion": _resumen(desc, 600), "tipo": tipo, "elementos": elementos}


def clave_elemento(x: dict) -> str:
    """Lo que identifica a un elemento del feed para no repetirlo (id/guid, enlace o huella de título y fecha)."""
    base = x.get("id") or x.get("enlace") or f"{x.get('titulo', '')}|{x.get('fecha', '')}"
    return hashlib.sha1(str(base).encode("utf-8")).hexdigest()[:24]


def descubrir_feed(html: str, base: str) -> str:
    """La URL del feed que anuncia una página (<link rel="alternate" type="application/rss+xml">), o ""."""
    try:
        s = _sopa(html)
    except Exception:
        return ""
    for l in s.find_all("link", href=True):
        rel = " ".join(l.get("rel") or []).lower()
        if "alternate" in rel and str(l.get("type") or "").lower() in ("application/rss+xml", "application/atom+xml", "application/feed+json"):
            return urllib.parse.urljoin(base, str(l["href"]))
    return ""
