"""
Caché de conversión, OCR y visión por huella del archivo.

Leer un adjunto (texto propio, OCR con tesseract y visión del modelo en las hojas
flojas) es lo más pesado de una corrida: un escaneo de diez hojas son decenas de
segundos de CPU y varias llamadas al modelo. El mismo archivo llega muchas veces
(reenvíos del mismo hilo, la misma cédula en cada correo, reintentos, pruebas), así
que el resultado se guarda por su contenido:

    clave = sha256(versión del motor de conversión + sha256 del archivo + nombre + opciones)

- Versión del motor: la huella del código de conversión, la instrucción de visión,
  la versión de tesseract y sus modelos. Si cambia cualquiera, nada viejo se usa.
- Opciones: OCR sí o no, la visión (proveedor y modelo; sin visión es otra clave),
  el tope de páginas, el de caracteres y las claves de los PDF protegidos (su huella,
  nunca su valor). Nunca se mezclan resultados de opciones distintas.
- Solo se guarda un resultado completo: si se agotó el presupuesto de OCR o el cupo de
  visión, si una hoja se cortó por tiempo, si la visión falló o si hubo error, no se
  guarda (la próxima vez se vuelve a leer).
- Un acierto cuenta como si se hubiera leído: descuenta del presupuesto del correo los
  segundos de OCR y las imágenes de visión que costó la primera vez, y solo se usa si
  hoy alcanzarían. Así la corrida decide igual que sin caché.

Vive en <estado>/cache/conversion/<2>/<clave>.json, con un tope de tamaño
(`CACHE_CONVERSION_MB`, 2048 por defecto) que se respeta borrando lo menos usado
(LRU por fecha de uso). `CACHE_CONVERSION=no` la apaga.
"""
from __future__ import annotations

import contextvars
import dataclasses
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

FORMATO = 1
# Lo que en el aviso de una conversión dice que quedó a medias (por tiempo o por presupuesto).
_A_MEDIAS = ("presupuesto", "se corto", "paso de", "no se pudo preparar", "el OCR fallo", "no se pudieron leer")


def carpeta() -> Path:
    if os.environ.get("RUTA_CACHE_CONVERSION"):
        return Path(os.environ["RUTA_CACHE_CONVERSION"])
    from conectores.base import ruta_estado
    return ruta_estado() / "cache" / "conversion"


def activa() -> bool:
    return (os.environ.get("CACHE_CONVERSION") or "si").strip().lower() not in ("no", "0", "false", "apagada")


def tope_bytes() -> int:
    try:
        return max(1, int(float(os.environ.get("CACHE_CONVERSION_MB") or 2048) * 1024 * 1024))
    except ValueError:
        return 2048 * 1024 * 1024


# ------------------------------------------------------------- versión del motor

_version: Dict[str, str] = {}


def version_motor(conversion: Any) -> str:
    """La huella de todo lo que decide el texto: el código de conversión, la instrucción de visión, tesseract y sus modelos."""
    archivo = getattr(conversion, "__file__", "") or ""
    if archivo in _version:
        return _version[archivo]
    h = hashlib.sha256(f"formato {FORMATO}".encode())
    try:
        h.update(Path(archivo).read_bytes())
    except OSError:
        h.update(b"sin-archivo")
    try:
        from pipeline.modelo import INSTRUCCION_DE_TRANSCRIPCION
        h.update(INSTRUCCION_DE_TRANSCRIPCION.encode())
    except Exception:
        pass
    try:
        h.update(subprocess.run(["tesseract", "--version"], capture_output=True, timeout=10).stdout[:200])
    except Exception:
        h.update(b"sin-tesseract")
    datos = os.environ.get("TESSDATA_PREFIX") or ""
    if datos and os.path.isdir(datos):
        for nombre in sorted(os.listdir(datos)):
            if nombre.endswith(".traineddata"):
                h.update(f"{nombre}:{os.path.getsize(os.path.join(datos, nombre))}".encode())
    _version[archivo] = h.hexdigest()[:16]
    return _version[archivo]


def version_de_funcion(fn: Callable) -> str:
    """La huella del código de una función (para conversores propios, como el de tutelas)."""
    import inspect
    try:
        fuente = inspect.getsource(inspect.unwrap(fn))
    except (OSError, TypeError):
        fuente = repr(fn)
    return hashlib.sha256(f"formato {FORMATO}\n{fuente}".encode()).hexdigest()[:16]


# ------------------------------------------------------------- la visión

_fallos_vision: contextvars.ContextVar = contextvars.ContextVar("fallos_vision", default=None)


class Vigilado:
    """El transcriptor de visión tal cual, que además anota si una llamada falló (la conversión se traga el error)."""

    def __init__(self, original: Callable[[bytes], str]) -> None:
        self.original = original

    def __call__(self, jpeg: bytes, *a: Any, **k: Any) -> str:
        try:
            return self.original(jpeg, *a, **k)
        except Exception:
            fallos = _fallos_vision.get()
            if fallos is not None:
                fallos.append(1)
            raise


def clave_vision(transcriptor: Any) -> Optional[str]:
    """Quién transcribe: «sin_vision», «<proveedor>:<modelo>» o None si no se sabe (entonces no se usa la caché)."""
    t = transcriptor.original if isinstance(transcriptor, Vigilado) else transcriptor
    if t is None:
        return "sin_vision"
    propia = getattr(t, "clave_cache", None)
    if isinstance(propia, str) and propia:
        return propia
    duenio = getattr(t, "__self__", None)
    nombre = getattr(duenio, "nombre", None)
    if duenio is not None and isinstance(nombre, str) and nombre:
        return f"{type(duenio).__name__}:{nombre}"
    return None


# ------------------------------------------------------------- disco

_cerrojo = threading.Lock()
_estimado: Dict[str, int] = {}
contadores = {"aciertos": 0, "fallos": 0, "guardados": 0, "sin_guardar": 0, "borrados": 0}


def clave(huella_archivo: str, nombre: str, version: str, opciones: Dict[str, Any]) -> str:
    texto = json.dumps({"v": version, "sha256": huella_archivo, "nombre": nombre, "opciones": opciones}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(texto.encode()).hexdigest()


def _ruta(k: str) -> Path:
    return carpeta() / k[:2] / f"{k}.json"


def leer(k: str) -> Optional[dict]:
    p = _ruta(k)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if d.get("clave") != k:
        return None
    try:
        os.utime(p, None)  # LRU: lo usado queda como reciente
    except OSError:
        pass
    return d


def guardar(k: str, valor: dict) -> None:
    p = _ruta(k)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        texto = json.dumps({**valor, "clave": k, "guardado": time.time()}, ensure_ascii=False)
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(texto, encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        return
    with _cerrojo:
        contadores["guardados"] += 1
        raiz = str(carpeta())
        if raiz not in _estimado or contadores["guardados"] % 200 == 0:
            _estimado[raiz] = tamano()
        else:
            _estimado[raiz] += len(texto.encode())
        if _estimado[raiz] > tope_bytes():
            _estimado[raiz] = recortar()


def tamano() -> int:
    total = 0
    for p in carpeta().glob("*/*.json"):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def recortar(hasta: Optional[int] = None) -> int:
    """Borra lo menos usado hasta quedar en el 90 % del tope. Devuelve el tamaño que queda."""
    objetivo = int(tope_bytes() * 0.9) if hasta is None else hasta
    archivos = []
    for p in carpeta().glob("*/*.json"):
        try:
            s = p.stat()
            archivos.append((s.st_mtime, s.st_size, p))
        except OSError:
            pass
    total = sum(a[1] for a in archivos)
    for _t, tam, p in sorted(archivos, key=lambda a: a[0]):
        if total <= objetivo:
            break
        try:
            p.unlink()
            total -= tam
            contadores["borrados"] += 1
        except OSError:
            pass
    return total


def estado() -> Dict[str, Any]:
    return {"activa": activa(), "carpeta": str(carpeta()), "tope_mb": round(tope_bytes() / 1048576), **contadores}


# ------------------------------------------------------------- la conversión con caché

def convertir(conversion: Any, original: Callable[..., Any], datos: bytes, nombre: str, claves_candidatas: Optional[List[str]] = None,
              presupuesto: Any = None, opciones: Optional[Dict[str, Any]] = None, **extra: Any) -> Tuple[Any, str]:
    """`original(datos, nombre, claves_candidatas, presupuesto=…)` con caché. Devuelve (Conversion, «acierto» | «fallo» | «sin_cache»)."""
    def llamar():
        return original(datos, nombre, claves_candidatas, presupuesto=presupuesto, **extra)

    if not activa() or not datos or extra.get("_profundidad_sobre"):
        return llamar(), "sin_cache"
    trans = getattr(conversion, "TRANSCRIPTOR_IA", None)
    vision = clave_vision(trans)
    if vision is None:
        return llamar(), "sin_cache"
    con_ocr = presupuesto is None or float(getattr(presupuesto, "total_s", 1) or 0) > 0
    cupo = int(getattr(presupuesto, "imagenes_ia", 1) or 0) if presupuesto is not None else 1
    ops = {
        "ocr": con_ocr,
        # sin cupo de visión (o sin transcriptor), la lectura es otra: otra clave
        "vision": vision if (cupo > 0 and vision != "sin_vision") else "sin_vision",
        "max_paginas": getattr(conversion, "LIMITE_PAGINAS_PDF", None),
        "max_caracteres": getattr(conversion, "LIMITE_CARACTERES", None),
        "claves": hashlib.sha256(json.dumps(sorted(map(str, claves_candidatas))).encode()).hexdigest()[:16] if claves_candidatas else "",
        **(opciones or {}),
    }
    huella = hashlib.sha256(datos).hexdigest()
    k = clave(huella, nombre, version_motor(conversion), ops)
    d = leer(k)
    queda_ocr = presupuesto.queda() if presupuesto is not None and hasattr(presupuesto, "queda") else float("inf")
    if d and int(d.get("imagenes_ia") or 0) <= cupo and float(d.get("ocr_s") or 0) <= queda_ocr:
        tipo = getattr(conversion, "Conversion")
        campos = {f.name for f in dataclasses.fields(tipo)}
        c = tipo(**{x: v for x, v in (d.get("conversion") or {}).items() if x in campos})
        if presupuesto is not None:
            presupuesto.gastar(float(d.get("ocr_s") or 0))
            presupuesto.imagenes_ia = max(0, int(presupuesto.imagenes_ia) - int(d.get("imagenes_ia") or 0))
        with _cerrojo:
            contadores["aciertos"] += 1
        return c, "acierto"
    with _cerrojo:
        contadores["fallos"] += 1
    # se lee de verdad, anotando si la visión falla (la conversión se lo calla)
    if trans is not None and not isinstance(trans, Vigilado):
        conversion.TRANSCRIPTOR_IA = Vigilado(trans)
    fallos: List[int] = []
    marca = _fallos_vision.set(fallos)
    gastado0 = float(getattr(presupuesto, "gastado_s", 0) or 0) if presupuesto is not None else 0.0
    t0 = time.monotonic()
    try:
        c = llamar()
    finally:
        _fallos_vision.reset(marca)
    usadas = cupo - int(getattr(presupuesto, "imagenes_ia", cupo) or 0) if presupuesto is not None else 0
    ocr_s = (float(getattr(presupuesto, "gastado_s", 0) or 0) - gastado0) if presupuesto is not None else time.monotonic() - t0
    a_medias = (bool(c.error) or fallos or any(x in (c.aviso or "") for x in _A_MEDIAS)
                or (presupuesto is not None and (getattr(presupuesto, "agotado", False) or (cupo > 0 and presupuesto.imagenes_ia <= 0))))
    if a_medias:
        with _cerrojo:
            contadores["sin_guardar"] += 1
        return c, "fallo"
    guardar(k, {"conversion": dataclasses.asdict(c), "imagenes_ia": max(0, usadas), "ocr_s": round(max(0.0, ocr_s), 3),
                "nombre": nombre, "bytes": len(datos), "sha256": huella, "opciones": ops})
    return c, "fallo"


def texto_con_cache(fn: Callable[[], str], datos: bytes, nombre: str, version: str, opciones: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """Para un conversor propio que devuelve solo texto (el de tutelas): (texto, «acierto» | «fallo» | «sin_cache»)."""
    if not activa() or not datos:
        return fn(), "sin_cache"
    k = clave(hashlib.sha256(datos).hexdigest(), nombre, version, opciones or {})
    d = leer(k)
    if d and isinstance(d.get("texto"), str):
        with _cerrojo:
            contadores["aciertos"] += 1
        return d["texto"], "acierto"
    with _cerrojo:
        contadores["fallos"] += 1
    t = fn()
    # un texto vacío puede ser un fallo pasajero (tesseract no disponible): no se guarda
    if t:
        guardar(k, {"texto": t, "nombre": nombre, "bytes": len(datos)})
    return t, "fallo"
