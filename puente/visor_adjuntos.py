"""
Cómo lee el motor cada adjunto, hoja por hoja.

Envuelve funciones de `pipeline.conversion` y métodos del `Workspace` sin
cambiar sus argumentos ni su resultado, y anuncia:

- `adjunto_inicio` / `adjunto_fin`: cada adjunto que entra a la conversión;
- `hoja`: cada hoja ya leída, con su imagen (renderizada aquí, en baja
  resolución, solo para mostrarla), cómo se leyó (texto del PDF, OCR, giro,
  relectura a 200 ppp, transcripción con visión) y su confianza;
- `documento_listo`: el texto de cada documento ya convertido, partido por hoja
  como lo ve el índice del motor, con el tipo de hoja que le asigna;
- `inventario`, `consulta`, `lectura`: lo que la IA pide al índice por hoja y
  qué hoja y fragmento le devuelve, con las posiciones para resaltarlo.

Las imágenes y los textos se escriben en la carpeta de trabajo de la corrida
(`RUTA_TRABAJO/visor/<corrida>`), que no se versiona y el servidor sirve solo
a la web local. Anunciar nunca rompe al motor: cualquier fallo de aquí se
reporta como consola y el motor sigue.
"""
from __future__ import annotations

import contextvars
import functools
import io
import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ANCHO_IMAGEN = 1100
LADO_MINIATURA = 220
CORRIDAS_CONSERVADAS = 80
MAX_RESULTADOS = 12

_evento: Callable[..., None] = lambda *a, **k: None  # noqa: E731
_cerrojo = threading.RLock()


class _EstadoPorCorreo:
    """
    El estado de las hojas del correo en curso, uno por tarea.

    En un lote varios correos se convierten a la vez en el mismo proceso: la
    hoja que se lee de uno no puede quedar anotada en otro. Vive en un
    `ContextVar`, que las tareas de asyncio separan y `asyncio.to_thread`
    hereda en el hilo de la conversión.
    """

    _var: contextvars.ContextVar = contextvars.ContextVar("adjuntos_del_visor")

    def _d(self) -> Dict[str, Any]:
        d = self._var.get(None)
        if d is None:
            d = {}
            self._var.set(d)
        return d

    def clear(self) -> None:
        self._var.set({})

    def update(self, valores: Dict[str, Any]) -> None:
        self._d().update(valores)

    def __getitem__(self, clave: str) -> Any:
        return self._d()[clave]

    def __setitem__(self, clave: str, valor: Any) -> None:
        self._d()[clave] = valor

    def get(self, clave: str, defecto: Any = None) -> Any:
        return self._d().get(clave, defecto)


_estado = _EstadoPorCorreo()


def _reiniciar() -> None:
    _estado.clear()
    _estado.update({
        "dir": None,          # carpeta de la corrida
        "n": 0,               # contador de adjuntos
        "profundidad": 0,     # convertir se llama a sí mismo con los .p7m
        "doc": None,          # adjunto en conversión
        "pagina": None,       # hoja en lectura
        "ultimo": None,       # clave del último adjunto convertido
        "textos": {},         # id del workspace -> texto por hoja
        "quien": "agente",
    })


_reiniciar()


def _seguro(nombre: str):
    def decorador(f):
        @functools.wraps(f)
        def envuelta(*a, **k):
            try:
                return f(*a, **k)
            except Exception as e:  # anunciar nunca rompe el motor
                _evento("consola", linea=f"[visor] {nombre}: {type(e).__name__}: {e}"[:400])
                return None
        return envuelta
    return decorador


# --------------------------------------------------------------- por corrida

def iniciar(corrida: str, base: Path) -> None:
    """Prepara la carpeta de la corrida y poda las más viejas."""
    _reiniciar()
    raiz = base / "visor"
    raiz.mkdir(parents=True, exist_ok=True)
    viejas = sorted((p for p in raiz.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    for p in viejas[:-CORRIDAS_CONSERVADAS]:
        shutil.rmtree(p, ignore_errors=True)
    destino = raiz / corrida
    (destino / "textos").mkdir(parents=True, exist_ok=True)
    _estado["dir"] = destino


def quien(rol: str) -> None:
    _estado["quien"] = rol


def herramienta(nombre: Optional[str]) -> None:
    """La herramienta de la IA en curso; fuera de una, las lecturas son del propio motor."""
    _estado["herramienta"] = nombre


def _de_la_ia() -> bool:
    return bool(_estado.get("herramienta"))


def vision(ms: int, caracteres: int, ok: bool, error: str = "") -> Dict[str, Any]:
    """Anota en la hoja en curso que el modelo con visión la transcribió; devuelve dónde."""
    p = _estado.get("pagina")
    doc = _estado.get("doc")
    if p is not None:
        p["pasos"].append({"paso": "vision", "ms": ms, "caracteres": caracteres, "ok": ok, "error": error[:200]})
    return {"adjunto": doc["clave"] if doc else "", "nombre": doc["nombre"] if doc else "",
            "pagina": p["numero"] if p else None, "origen": p["origen"] if p else ""}


def _ruta(nombre: str) -> Optional[Path]:
    d = _estado.get("dir")
    return d / nombre if d else None


# ------------------------------------------------------------------ imágenes

def _guardar_imagen(imagen, base: str) -> Dict[str, Any]:
    """Guarda la hoja en dos tamaños; devuelve los nombres y el tamaño."""
    destino = _ruta(f"{base}.jpg")
    if destino is None or imagen is None:
        return {}
    img = imagen.convert("RGB") if imagen.mode not in ("RGB", "L") else imagen
    grande = img.copy()
    grande.thumbnail((ANCHO_IMAGEN, ANCHO_IMAGEN * 2))
    grande.save(destino, format="JPEG", quality=80, optimize=True)
    mini = img.copy()
    mini.thumbnail((LADO_MINIATURA, LADO_MINIATURA * 1.5))
    mini.save(_ruta(f"{base}-m.jpg"), format="JPEG", quality=72, optimize=True)
    return {"img": f"{base}.jpg", "mini": f"{base}-m.jpg", "ancho": grande.width, "alto": grande.height}


def _abrir_pdf(datos: bytes, claves: Optional[List[str]]):
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    for clave in [None, *(claves or [])]:
        try:
            return pdfium.PdfDocument(datos, password=clave)
        except Exception:
            continue
    return None


def _render_pdf(doc, numero: int):
    pdf = doc.get("pdf")
    if pdf is None or numero < 1 or numero > len(pdf):
        return None
    pagina = pdf[numero - 1]
    try:
        ancho, _alto = pagina.get_size()
        escala = max(0.5, min(2.5, ANCHO_IMAGEN / (ancho or 612)))
        return pagina.render(scale=escala).to_pil()
    finally:
        pagina.close()


# ------------------------------------------------------- conversión por hoja

def _nueva_pagina(numero: int, origen: str) -> Dict[str, Any]:
    return {"numero": numero, "origen": origen, "inicio": time.monotonic(), "pasos": [],
            "lecturas": 0, "giro": None, "girada": 0, "vision": False, "vision_intentada": False,
            "en_giro": False, "tras_giro": None, "captura": None}


def _imagen_de_hoja(doc: Dict[str, Any], numero: int):
    if doc["tipo"] == "pdf":
        try:
            return _render_pdf(doc, numero)
        except Exception:
            return None
    return doc.get("imagen")


def _preparar_hoja(doc: Dict[str, Any], numero: int, captura=None) -> Dict[str, Any]:
    """La imagen de la hoja, guardada una sola vez; sirve antes y después de leerla."""
    cache = doc.setdefault("archivos", {})
    if cache.get(numero):
        return cache[numero]
    imagen = _imagen_de_hoja(doc, numero) or captura
    cache[numero] = _guardar_imagen(imagen, f"{doc['clave']}-p{numero}") if imagen is not None else {}
    return cache[numero]


@_seguro("hoja")
def _empieza_hoja(p: Dict[str, Any]) -> None:
    """Antes de leer una hoja: su imagen ya se puede ver mientras el OCR trabaja."""
    doc = _estado.get("doc")
    if not doc:
        return
    archivos = _preparar_hoja(doc, p["numero"])
    _evento("hoja_inicio", adjunto=doc["clave"], pagina=p["numero"], total=doc.get("paginas") or 0,
            origen=p["origen"], **archivos)


def _anunciar_hoja(p: Dict[str, Any], metodo: str, **extra: Any) -> None:
    doc = _estado.get("doc")
    if not doc:
        return
    numero = p["numero"]
    if p["girada"]:
        # La hoja se leyó girada: se muestra como la leyó el OCR.
        imagen = _imagen_de_hoja(doc, numero) or p.get("captura")
        archivos = _guardar_imagen(imagen.rotate(-p["girada"], expand=True, fillcolor="white"),
                                   f"{doc['clave']}-p{numero}") if imagen is not None else {}
        doc.setdefault("archivos", {})[numero] = archivos
    else:
        archivos = _preparar_hoja(doc, numero, p.get("captura"))
    ms = int((time.monotonic() - p["inicio"]) * 1000)
    doc["hojas"][numero] = metodo
    _evento("hoja", adjunto=doc["clave"], nombre=doc["nombre"], pagina=numero,
            total=doc.get("paginas") or 0, metodo=metodo, ms=ms, pasos=p["pasos"],
            girada=p["girada"], relectura_200=p["lecturas"] > 1, vision=p["vision"],
            vision_intentada=p["vision_intentada"], **archivos, **extra)


def _instalar_conversion(conversion) -> None:
    convertir_original = conversion.convertir

    @functools.wraps(convertir_original)
    def convertir(datos, nombre, *a, **k):
        if _estado["profundidad"] > 0:
            _estado["profundidad"] += 1
            try:
                return convertir_original(datos, nombre, *a, **k)
            finally:
                _estado["profundidad"] -= 1
        _estado["profundidad"] = 1
        _estado["convertido"] = True
        doc = _abrir_adjunto(conversion, datos, nombre, a[0] if a else k.get("claves_candidatas"))
        try:
            c = convertir_original(datos, nombre, *a, **k)
        except Exception as e:
            _cerrar_adjunto(doc, None, error=f"{type(e).__name__}: {e}")
            raise
        finally:
            _estado["profundidad"] = 0
        _cerrar_adjunto(doc, c)
        return c

    conversion.convertir = convertir
    _reemplazar(convertir_original, convertir)

    # Hoja con texto propio: el motor la pasa por aquí después de sacar su texto.
    tablas = conversion._tablas_de_pagina

    @functools.wraps(tablas)
    def tablas_de_pagina(pagina):
        r = tablas(pagina)
        _hoja_con_texto(pagina, r)
        return r

    conversion._tablas_de_pagina = tablas_de_pagina

    # Hoja escaneada: OCR a 300 ppp, giro, relectura a 200 y visión.
    ocr = conversion._ocr_de_pagina

    @functools.wraps(ocr)
    def ocr_de_pagina(pagina, *a, **k):
        p = _nueva_pagina(int(getattr(pagina, "number", 0)) + 1, "ocr")
        _estado["pagina"] = p
        _empieza_hoja(p)
        p["inicio"] = time.monotonic()
        try:
            r = ocr(pagina, *a, **k)
        finally:
            _estado["pagina"] = None
        _hoja_leida(conversion, p, r[0] if r else "", r[1] if r else "", getattr(r, "confianza", None))
        return r

    conversion._ocr_de_pagina = ocr_de_pagina

    imagenes = conversion._ocr_de_imagenes

    @functools.wraps(imagenes)
    def ocr_de_imagenes(doc, pagina, *a, **k):
        p = _nueva_pagina(int(getattr(pagina, "number", 0)) + 1, "incrustada")
        previa = _estado.get("pagina")
        _estado["pagina"] = p
        try:
            r = imagenes(doc, pagina, *a, **k)
        finally:
            _estado["pagina"] = previa
        _imagenes_incrustadas(p, r)
        return r

    conversion._ocr_de_imagenes = ocr_de_imagenes

    imagen_md = conversion._imagen_a_markdown

    @functools.wraps(imagen_md)
    def imagen_a_markdown(datos, nombre, *a, **k):
        p = _nueva_pagina(1, "foto")
        _estado["pagina"] = p
        _empieza_hoja(p)
        p["inicio"] = time.monotonic()
        try:
            c = imagen_md(datos, nombre, *a, **k)
        finally:
            _estado["pagina"] = None
        _foto_leida(conversion, p, c)
        return c

    conversion._imagen_a_markdown = imagen_a_markdown

    lectura = conversion._texto_de_imagen_tesseract

    @functools.wraps(lectura)
    def texto_de_imagen_tesseract(imagen, segundos, preparar=True):
        p = _estado.get("pagina")
        if p is not None:
            p["lecturas"] += 1
            p["lectura_actual"] = 0
            if p.get("captura") is None:
                _capturar(p, imagen)
        return lectura(imagen, segundos, preparar)

    conversion._texto_de_imagen_tesseract = texto_de_imagen_tesseract

    tesseract = conversion._tesseract

    @functools.wraps(tesseract)
    def tesseract_observado(gris, segundos):
        p = _estado.get("pagina")
        t = time.monotonic()
        texto, confianza = tesseract(gris, segundos)
        if p is not None:
            _paso_tesseract(conversion, p, gris, texto, confianza, int((time.monotonic() - t) * 1000))
        return texto, confianza

    conversion._tesseract = tesseract_observado

    giro = conversion._giro_probable

    @functools.wraps(giro)
    def giro_probable(gris, confianza, limite):
        p = _estado.get("pagina")
        if p is not None:
            p["en_giro"] = True
        t = time.monotonic()
        try:
            r = giro(gris, confianza, limite)
        finally:
            if p is not None:
                p["en_giro"] = False
        if p is not None:
            p["pasos"].append({"paso": "giro", "grados": int(r or 0), "ms": int((time.monotonic() - t) * 1000),
                               "confianza_antes": round(float(confianza or 0), 2)})
            if r:
                p["tras_giro"] = {"grados": int(r), "antes": float(confianza or 0)}
        return r

    conversion._giro_probable = giro_probable

    respaldo = conversion._con_respaldo_ia

    @functools.wraps(respaldo)
    def con_respaldo_ia(imagen, texto, confianza, motivo, presupuesto):
        p = _estado.get("pagina")
        antes = getattr(presupuesto, "imagenes_ia", None)
        r = respaldo(imagen, texto, confianza, motivo, presupuesto)
        if p is not None:
            intento = antes is not None and getattr(presupuesto, "imagenes_ia", None) != antes
            if intento:
                p["vision_intentada"] = True
            if r and str(r[0]).startswith(conversion.MARCA_TRANSCRITO_POR_IA):
                p["vision"] = True
        return r

    conversion._con_respaldo_ia = con_respaldo_ia


def _capturar(p: Dict[str, Any], imagen) -> None:
    try:
        copia = imagen.copy()
        copia.thumbnail((ANCHO_IMAGEN, ANCHO_IMAGEN * 2))
        p["captura"] = copia
    except Exception:
        p["captura"] = None


def _paso_tesseract(conversion, p, gris, texto, confianza, ms) -> None:
    if p["en_giro"]:
        p["pasos"].append({"paso": "sondeo", "confianza": round(float(confianza), 2), "ms": ms})
        return
    tras = p.get("tras_giro")
    if tras:
        p["tras_giro"] = None
        usada = float(confianza) > tras["antes"]
        if usada:
            p["girada"] = tras["grados"]
        p["pasos"].append({"paso": "girada", "grados": tras["grados"], "confianza": round(float(confianza), 2),
                           "caracteres": len(texto or ""), "ms": ms, "usada": usada})
        return
    p["lectura_actual"] = p.get("lectura_actual", 0) + 1
    if p["origen"] in ("ocr",):
        dpi = 300 if p["lecturas"] <= 1 else conversion.DPI_DE_RESPALDO_OCR
        nombre = "tesseract" if p["lectura_actual"] == 1 else "sin_preparar"
        p["pasos"].append({"paso": nombre, "dpi": dpi, "confianza": round(float(confianza), 2),
                           "caracteres": len(texto or ""), "ms": ms,
                           "pixeles": f"{gris.width}×{gris.height}"})
    else:
        nombre = "tesseract" if p["lectura_actual"] == 1 else "sin_preparar"
        p["pasos"].append({"paso": nombre, "confianza": round(float(confianza), 2),
                           "caracteres": len(texto or ""), "ms": ms,
                           "pixeles": f"{gris.width}×{gris.height}"})


@_seguro("adjunto")
def _abrir_adjunto(conversion, datos: bytes, nombre: str, claves) -> Optional[Dict[str, Any]]:
    _estado["n"] += 1
    clave = f"a{_estado['n']}"
    tipo = conversion.detectar_tipo(datos, nombre)
    doc: Dict[str, Any] = {"clave": clave, "nombre": nombre, "tipo": tipo, "inicio": time.monotonic(),
                           "hojas": {}, "pdf": None, "imagen": None, "paginas": 0}
    if tipo == "pdf":
        doc["pdf"] = _abrir_pdf(datos, claves)
        if doc["pdf"] is not None:
            doc["paginas"] = len(doc["pdf"])
    elif tipo == "imagen":
        try:
            from PIL import Image, ImageOps

            img = Image.open(io.BytesIO(datos))
            doc["imagen"] = ImageOps.exif_transpose(img).convert("RGB")
            doc["paginas"] = 1
        except Exception:
            doc["imagen"] = None
    _estado["doc"] = doc
    _evento("adjunto_inicio", adjunto=clave, nombre=nombre, tipo_archivo=tipo, bytes=len(datos or b""),
            paginas=doc["paginas"])
    return doc


MAX_HOJAS_DE_CACHE = 80


def _hojas_de_cache(doc: Dict[str, Any]) -> None:
    """
    El adjunto salió de la caché de conversión (ya se había leído en otra corrida): el motor
    no pasó por ninguna hoja, pero la pantalla igual muestra cada una con su imagen, marcada
    como leída antes, en vez de una hoja en blanco.
    """
    for numero in range(1, min(doc.get("paginas") or 0, MAX_HOJAS_DE_CACHE) + 1):
        archivos = _preparar_hoja(doc, numero)
        doc["hojas"][numero] = "cache"
        _evento("hoja", adjunto=doc["clave"], nombre=doc["nombre"], pagina=numero, total=doc.get("paginas") or 0,
                metodo="cache", ms=0, pasos=[{"paso": "cache"}], girada=0, relectura_200=False, vision=False,
                vision_intentada=False, **archivos)


@_seguro("adjunto")
def _cerrar_adjunto(doc, c, error: str = "") -> None:
    if not doc:
        return
    _estado["doc"] = None
    _estado["ultimo"] = doc["clave"]
    # Las hojas de un adjunto que salió de la caché se renderizan antes de cerrar el PDF.
    if not error and not doc["hojas"] and (doc.get("pdf") is not None or doc.get("imagen") is not None):
        _hojas_de_cache(doc)
    if doc.get("pdf") is not None:
        try:
            doc["pdf"].close()
        except Exception:
            pass
    markdown = getattr(c, "markdown", "") or ""
    _evento("adjunto_fin", adjunto=doc["clave"], nombre=doc["nombre"],
            tipo_archivo=getattr(c, "tipo_real", "") or doc["tipo"], error=error or getattr(c, "error", "") or "",
            aviso=getattr(c, "aviso", "") or "", paginas=getattr(c, "paginas", 0) or doc["paginas"],
            ilegibles=getattr(c, "paginas_ilegibles", 0) or 0, ocr=bool(getattr(c, "ocr_aplicado", False)),
            caracteres=len(markdown), truncado=bool(getattr(c, "truncado", False)),
            ms=int((time.monotonic() - doc["inicio"]) * 1000), hojas=doc["hojas"])


@_seguro("hoja")
def _hoja_con_texto(pagina, tablas) -> None:
    doc = _estado.get("doc")
    if not doc or _estado["profundidad"] > 1 and doc["tipo"] != "pdf":
        return
    numero = int(getattr(pagina, "number", 0)) + 1
    p = _nueva_pagina(numero, "texto")
    p["inicio"] = doc.get("fin_hoja", doc["inicio"])
    p["pasos"].append({"paso": "texto_pdf", "tablas": len(tablas or [])})
    _anunciar_hoja(p, "texto", tablas=len(tablas or []))
    doc["fin_hoja"] = time.monotonic()


@_seguro("hoja")
def _hoja_leida(conversion, p, texto: str, motivo: str, confianza) -> None:
    doc = _estado.get("doc")
    if not doc:
        return
    texto = texto or ""
    if p["vision"]:
        metodo = "vision"
    elif texto.strip():
        metodo = "ocr"
    else:
        metodo = "ilegible"
    _anunciar_hoja(p, metodo, confianza=confianza, motivo=motivo or "", caracteres=len(texto),
                   dudosa=confianza is not None and confianza < conversion.CONFIANZA_DUDOSA)
    doc["fin_hoja"] = time.monotonic()


@_seguro("hoja")
def _foto_leida(conversion, p, c) -> None:
    doc = _estado.get("doc")
    if not doc or c is None:
        return
    md = c.markdown or ""
    m = re.search(r"confianza\s+(\d+(?:\.\d+)?)", md)
    confianza = float(m.group(1)) if m else None
    if p["vision"]:
        metodo = "vision"
    elif c.error or c.paginas_ilegibles or "sin texto reconocible" in md:
        metodo = "ilegible"
    else:
        metodo = "ocr"
    _anunciar_hoja(p, metodo, confianza=confianza, motivo=c.error or c.aviso or "",
                   caracteres=len(md), dudosa=confianza is not None and confianza < conversion.CONFIANZA_DUDOSA)


@_seguro("imagenes")
def _imagenes_incrustadas(p, r) -> None:
    doc = _estado.get("doc")
    if not doc or not r:
        return
    md, sin_leer, motivo = r
    leidas = (md or "").count("### Imagen")
    if not leidas and not sin_leer:
        return
    _evento("hoja_imagenes", adjunto=doc["clave"], pagina=p["numero"], leidas=leidas, sin_leer=sin_leer,
            motivo=motivo or "", vision=p["vision"], pasos=p["pasos"])


# ------------------------------------------------------------ textos por hoja

def _paginas_de(lineas: List[str]) -> List[Tuple[int, int, int]]:
    from pipeline import indice

    return [(p.numero, p.inicio, p.fin) for p in indice.paginas_de(lineas)]


@_seguro("texto")
def registrar_texto(id_doc: str, markdown: str, nombre: str = "", clave: str = "") -> None:
    from pipeline import indice

    lineas = (markdown or "").splitlines()
    paginas = _paginas_de(lineas)
    hojas = []
    tipos: Dict[int, Dict[str, Any]] = {}
    if paginas:
        try:
            for h in indice.hojas_de(lineas):
                tipos[h.desde] = {"tipo": h.tipo, "ids": h.ids, "marcas": list(h.marcas)}
        except Exception:
            tipos = {}
        for numero, ini, fin in paginas:
            hojas.append({"numero": numero, "linea": ini + 1, "texto": "\n".join(lineas[ini + 1:fin]),
                          **tipos.get(numero, {})})
    else:
        clase = ""
        try:
            clase = indice.describir_texto("\n".join(lineas))
        except Exception:
            pass
        hojas.append({"numero": 1, "linea": 0, "texto": "\n".join(lineas), "tipo": clase})
    _estado["textos"][id_doc] = {"lineas": lineas, "paginas": paginas, "nombre": nombre, "clave": clave}
    destino = _ruta(f"textos/{_seguro_nombre(id_doc)}.json")
    if destino is not None:
        destino.write_text(json.dumps({"id": id_doc, "nombre": nombre, "adjunto": clave, "hojas": hojas},
                                      ensure_ascii=False), encoding="utf-8")
    _evento("documento_listo", id=id_doc, nombre=nombre, adjunto=clave, sin_hojas=not paginas,
            archivo=f"textos/{_seguro_nombre(id_doc)}.json",
            hojas=[{"numero": h["numero"], "tipo": h.get("tipo", ""), "ids": h.get("ids", 0),
                    "marcas": h.get("marcas", []), "caracteres": len(h["texto"])} for h in hojas])


def _seguro_nombre(id_doc: str) -> str:
    return re.sub(r"[^\w.-]", "_", id_doc)[:120]


def _hoja_de_linea(id_doc: str, linea: int) -> Tuple[Optional[int], int]:
    """Hoja de una línea del documento y dónde empieza esa línea dentro del texto de la hoja."""
    t = _estado["textos"].get(id_doc)
    if not t:
        return None, -1
    lineas = t["lineas"]
    inicio_hoja, numero = 0, 1
    for n, ini, fin in t["paginas"]:
        if ini <= linea < fin:
            numero, inicio_hoja = n, ini + 1
            break
    else:
        if t["paginas"]:
            return None, -1
    if linea < inicio_hoja:
        return numero, 0
    return numero, sum(len(lineas[j]) + 1 for j in range(inicio_hoja, linea))


def _spans_en_linea(id_doc: str, linea: int, regex) -> List[List[int]]:
    t = _estado["textos"].get(id_doc)
    if not t or not (0 <= linea < len(t["lineas"])):
        return []
    from pipeline import indice

    _, base = _hoja_de_linea(id_doc, linea)
    if base < 0:
        return []
    plegada = indice.plegar(t["lineas"][linea])
    return [[base + m.start(), base + m.end()] for m in regex.finditer(plegada) if m.end() > m.start()][:6]


def _tramo(id_doc: str, desde: int, hasta: int) -> Optional[List[int]]:
    t = _estado["textos"].get(id_doc)
    if not t:
        return None
    _, a = _hoja_de_linea(id_doc, desde)
    ultima = max(desde, min(hasta, len(t["lineas"])) - 1)
    _, b = _hoja_de_linea(id_doc, ultima)
    if a < 0 or b < 0:
        return None
    return [a, b + len(t["lineas"][ultima])]


def _nombre_de(id_doc: str) -> str:
    t = _estado["textos"].get(id_doc) or {}
    return t.get("nombre") or id_doc


# -------------------------------------------------------- índice y búsquedas

def _instalar_workspace(workspace_mod) -> None:
    W = workspace_mod.Workspace

    escribir_adjunto = W.escribir_adjunto

    @functools.wraps(escribir_adjunto)
    def _escribir_adjunto(self, indice, nombre, markdown):
        ruta = escribir_adjunto(self, indice, nombre, markdown)
        registrar_texto(Path(ruta).stem, markdown, nombre, _estado.get("ultimo") or "")
        return ruta

    W.escribir_adjunto = _escribir_adjunto

    for metodo, id_doc, titulo in (("escribir_correo", "correo", "Cuerpo del correo"),
                                   ("escribir_hilo", "hilo", "Mensajes anteriores del hilo")):
        original = getattr(W, metodo)

        def hacer(original=original, id_doc=id_doc, titulo=titulo):
            @functools.wraps(original)
            def _escribir(self, markdown):
                ruta = original(self, markdown)
                registrar_texto(id_doc, markdown, titulo, "")
                return ruta
            return _escribir

        setattr(W, metodo, hacer())

    agregar = workspace_mod.Manifiesto.agregar

    @functools.wraps(agregar)
    def _agregar(self, entrada):
        r = agregar(self, entrada)
        _anunciar_entrada(entrada)
        return r

    workspace_mod.Manifiesto.agregar = _agregar

    listar = W.listar

    @functools.wraps(listar)
    def _listar(self):
        r = listar(self)
        if _de_la_ia():
            _anunciar_inventario(r)
        return r

    W.listar = _listar

    buscar = W.buscar

    @functools.wraps(buscar)
    def _buscar(self, patron, contexto=1):
        t = time.monotonic()
        r = buscar(self, patron, contexto)
        _anunciar_busqueda(patron, r, int((time.monotonic() - t) * 1000))
        return r

    W.buscar = _buscar

    consultar = W.consultar

    @functools.wraps(consultar)
    def _consultar(self, pregunta, maximo=3, caracteres=1800):
        t = time.monotonic()
        r = consultar(self, pregunta, maximo, caracteres)
        _anunciar_consulta(pregunta, r, int((time.monotonic() - t) * 1000))
        return r

    W.consultar = _consultar

    leer_pagina = W.leer_pagina

    @functools.wraps(leer_pagina)
    def _leer_pagina(self, id_documento, pagina):
        r = leer_pagina(self, id_documento, pagina)
        if _de_la_ia():
            _evento("lectura", quien=_estado["quien"], id=id_documento, nombre=_nombre_de(id_documento),
                    pagina=int(pagina), caracteres=len(r[0] or ""))
        return r

    W.leer_pagina = _leer_pagina

    leer = W.leer

    @functools.wraps(leer)
    def _leer(self, id_documento, desde=0, hasta=0):
        r = leer(self, id_documento, desde, hasta)
        if _de_la_ia():
            _anunciar_lectura(self, id_documento, desde, hasta, r)
        return r

    W.leer = _leer


@_seguro("manifiesto")
def _anunciar_entrada(e) -> None:
    _evento("manifiesto", nombre=e.nombre_original, estado=e.estado, detalle=(e.detalle or "")[:300],
            tipo_archivo=e.tipo_real or e.tipo_declarado or "", ocr=bool(e.ocr_aplicado), bytes=e.bytes_originales,
            id=Path(e.ruta_md).stem if e.ruta_md else "")


@_seguro("inventario")
def _anunciar_inventario(docs) -> None:
    _evento("inventario", quien=_estado["quien"],
            documentos=[{"id": d.get("id"), "nombre": d.get("nombre_original") or d.get("id"),
                         "hojas": d.get("paginas") or [], "clase": d.get("clase", ""),
                         "caracteres": d.get("caracteres", 0)} for d in (docs or [])][:40])


@_seguro("buscar")
def _anunciar_busqueda(patron: str, resultados, ms: int) -> None:
    from pipeline import indice

    cifra = indice.patron_de_cifra(patron)
    fuente = cifra if cifra is not None else indice.plegar_patron(patron)
    try:
        regex = re.compile(fuente, re.IGNORECASE)
    except re.error:
        regex = None
    salida = []
    for r in (resultados or [])[:MAX_RESULTADOS]:
        id_doc = r.get("documento", "")
        linea = int(r.get("linea", 0))
        pagina, _ = _hoja_de_linea(id_doc, linea)
        spans = _spans_en_linea(id_doc, linea, regex) if regex else []
        if not spans and regex is not None:
            # La coincidencia cruzaba el salto de línea: se resalta el par.
            tramo = _tramo(id_doc, linea, linea + 2)
            spans = [tramo] if tramo else []
        salida.append({"id": id_doc, "nombre": _nombre_de(id_doc), "pagina": r.get("pagina", pagina),
                       "linea": linea, "fragmento": str(r.get("coincidencia", ""))[:300], "spans": spans})
    _evento("consulta", quien=_estado["quien"] if _de_la_ia() else "motor", modo="buscar", consulta=patron, ms=ms,
            total=len(resultados or []), resultados=salida)


@_seguro("consultar")
def _anunciar_consulta(pregunta: str, tramos, ms: int) -> None:
    from pipeline import indice

    terminos = indice.terminos(pregunta)
    pesos = indice.expandir(terminos)
    salida = []
    for r in (tramos or [])[:MAX_RESULTADOS]:
        id_doc = r.get("documento", "")
        desde = int(r.get("linea", 0))
        texto = str(r.get("texto", ""))
        n_lineas = max(1, texto.count("\n") + 1)
        pagina, _ = _hoja_de_linea(id_doc, desde)
        tramo = _tramo(id_doc, desde, desde + n_lineas)
        spans = []
        t = _estado["textos"].get(id_doc)
        if t:
            for k in range(desde, min(len(t["lineas"]), desde + n_lineas)):
                plegada = indice.plegar(t["lineas"][k])
                _, base = _hoja_de_linea(id_doc, k)
                for m in re.finditer(r"[a-z0-9]+", plegada):
                    if indice.raiz(m.group(0)) in pesos:
                        spans.append([base + m.start(), base + m.end()])
        salida.append({"id": id_doc, "nombre": _nombre_de(id_doc), "pagina": r.get("pagina", pagina),
                       "linea": desde, "puntaje": r.get("puntaje"), "fragmento": texto[:400],
                       "tramo": tramo, "spans": spans[:30]})
    _evento("consulta", quien=_estado["quien"] if _de_la_ia() else "motor", modo="consultar", consulta=pregunta, ms=ms,
            terminos=sorted(pesos, key=lambda x: -pesos[x])[:12], total=len(tramos or []), resultados=salida)


@_seguro("leer")
def _anunciar_lectura(ws, id_doc: str, desde: int, hasta: int, texto: str) -> None:
    t = _estado["textos"].get(id_doc)
    paginas = []
    if t and t["paginas"]:
        fin = hasta if hasta > 0 else len(t["lineas"])
        paginas = [n for n, ini, f in t["paginas"] if ini < fin and desde < f]
    _evento("lectura", quien=_estado["quien"], id=id_doc, nombre=_nombre_de(id_doc), desde=desde,
            hasta=hasta, paginas=paginas[:20], caracteres=len(texto or ""))


# ----------------------------------------------------------- dónde está un dato

def ubicar(valor: Any, evidencia: Any = "") -> Optional[Dict[str, Any]]:
    """La hoja y el tramo donde aparece un valor (o su evidencia) en el texto convertido."""
    try:
        return _ubicar(valor, evidencia)
    except Exception:
        return None


def _patron_de_valor(valor: Any):
    from pipeline import indice

    crudo = str(valor or "").strip()
    if not crudo:
        return None
    digitos = re.sub(r"\D", "", crudo)
    if len(digitos) >= 5 and len(digitos) >= len(re.sub(r"\s", "", crudo)) * 0.6:
        return re.compile(r"(?<!\d)0*" + r"[ .,'\-]?".join(digitos.lstrip("0") or digitos) + r"(?!\d)")
    palabras = [re.escape(p) for p in re.findall(r"[a-z0-9]+", indice.plegar(crudo)) if len(p) > 1]
    if not palabras or (len(palabras) == 1 and len(palabras[0]) < 4):
        return None
    return re.compile(r"\b" + r"\W+(?:\w+\W+)?".join(palabras) + r"\b")


def _ubicar(valor: Any, evidencia: Any) -> Optional[Dict[str, Any]]:
    from pipeline import indice

    patron = _patron_de_valor(valor)
    ev = indice.plegar(re.sub(r"\.\.\.|…", " ", str(evidencia or "")))
    palabras_ev = set(re.findall(r"[a-z0-9]{3,}", ev))
    candidatos = []
    orden = sorted(_estado["textos"], key=lambda i: (i == "hilo", i != "correo", i))
    for id_doc in orden:
        t = _estado["textos"][id_doc]
        secciones = [(n, ini + 1, fin) for n, ini, fin in t["paginas"]] or [(1, 0, len(t["lineas"]))]
        for numero, ini, fin in secciones:
            texto = "\n".join(t["lineas"][ini:fin])
            plegado = indice.plegar(texto)
            if patron is not None:
                for m in patron.finditer(plegado):
                    ventana = set(re.findall(r"[a-z0-9]{3,}", plegado[max(0, m.start() - 160):m.end() + 160]))
                    puntaje = len(palabras_ev & ventana) + 0.5
                    candidatos.append((puntaje, -len(candidatos), id_doc, numero, m.start(), m.end()))
            elif len(ev.strip()) >= 12:
                trozo = max(re.split(r"\s{2,}|\n", ev.strip()), key=len)[:80].strip()
                if len(trozo) >= 12:
                    i = plegado.find(trozo)
                    if i >= 0:
                        candidatos.append((1.0, -len(candidatos), id_doc, numero, i, i + len(trozo)))
    if not candidatos:
        return None
    _p, _o, id_doc, numero, a, b = max(candidatos)
    t = _estado["textos"][id_doc]
    return {"id": id_doc, "nombre": t.get("nombre") or id_doc, "adjunto": t.get("clave", ""),
            "pagina": numero, "spans": [[a, b]], "sin_hojas": not t["paginas"]}


# ------------------------------------------------------------------- instalar

def _reemplazar(original, nuevo) -> None:
    import sys

    for mod in list(sys.modules.values()):
        nombre = getattr(mod, "__name__", "") or ""
        if not (nombre.startswith("pipeline") or nombre.startswith("servidor_mcp")):
            continue
        for k, v in list(vars(mod).items()):
            if v is original:
                setattr(mod, k, nuevo)


def instalar(evento: Callable[..., None]) -> None:
    global _evento
    _evento = evento
    from pipeline import conversion, workspace

    _instalar_conversion(conversion)
    _instalar_workspace(workspace)
    _instalar_cache(conversion)


def _instalar_cache(conversion) -> None:
    """
    Un acierto de la caché de conversión del motor no llama a `conversion.convertir`, así que
    el adjunto no pasaba por el visor: sus hojas quedaban sin imagen. Aquí se anuncia igual,
    con cada hoja renderizada y marcada como leída antes.
    """
    try:
        from pipeline import cache_conversion
    except ImportError:
        return
    original = cache_conversion.convertir

    @functools.wraps(original)
    def convertir(datos, nombre, claves_candidatas=None, presupuesto=None):
        # Si el motor convirtió de verdad, el envoltorio de conversion.convertir lo marca en el
        # estado de ESTE correo (los contadores de la caché son de todo el proceso).
        _estado["convertido"] = False
        c = original(datos, nombre, claves_candidatas, presupuesto=presupuesto)
        if not _estado.get("convertido") and c is not None and _estado.get("dir") and not _estado.get("doc"):
            _adjunto_de_cache(conversion, datos, nombre, claves_candidatas, c)
        return c

    cache_conversion.convertir = convertir


@_seguro("adjunto")
def _adjunto_de_cache(conversion, datos, nombre, claves, c) -> None:
    doc = _abrir_adjunto(conversion, datos, nombre, claves)
    _cerrar_adjunto(doc, c)
