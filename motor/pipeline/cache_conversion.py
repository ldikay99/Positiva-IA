"""
Cache de la conversion de adjuntos por huella del contenido.

Convertir un adjunto (texto propio, OCR y vision en las hojas flojas) es lo mas
pesado del correo, y el mismo archivo llega muchas veces: el hilo repite los
adjuntos en cada reenvio, la misma cedula viaja en varios correos, un correo
aplazado se vuelve a leer entero en la corrida siguiente. El resultado se
guarda por su contenido:

    clave = sha256(version del conversor + sha256 del archivo + nombre + opciones)

- La version es la huella del codigo de `conversion.py`, de la instruccion de
  vision, de tesseract y de sus modelos: si cambia cualquiera, nada viejo vale.
- Las opciones son lo que cambia la lectura: si hay vision y con que modelo, los
  topes de paginas y caracteres y la huella (nunca el valor) de las claves de
  los PDF protegidos.
- Solo se guarda una lectura completa. Si se agoto el presupuesto de OCR o el
  cupo de vision, si una hoja se corto por tiempo, si la vision fallo o hubo un
  error, no se guarda: la proxima vez se lee de nuevo.
- Un acierto descuenta del presupuesto del correo los segundos de OCR y las
  imagenes de vision que costo la primera vez, y solo se usa si hoy
  alcanzarian. Asi el correo decide igual que sin cache.

Vive en memoria (LRU, `CACHE_CONVERSION_MEMORIA_MB`, 256 por defecto) y, si
`RUTA_CACHE_CONVERSION` dice donde, tambien en disco, con tope
(`CACHE_CONVERSION_MB`, 2048) y borrando lo menos usado. `CACHE_CONVERSION=no`
la apaga.
"""
from __future__ import annotations

import collections
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
# Lo que en el aviso de una conversion dice que quedo a medias.
_A_MEDIAS = ("presupuesto", "se corto", "paso de", "no se pudo preparar",
             "el OCR fallo", "no se pudieron leer")

# Se llama con (nombre, "acierto" | "fallo") en cada conversion: lo usa quien
# quiera anunciarlo (la plataforma) sin que el motor sepa de eventos.
OBSERVADOR: Optional[Callable[[str, str], None]] = None


def activa() -> bool:
    return (os.environ.get("CACHE_CONVERSION") or "si").strip().lower() \
        not in ("no", "0", "false", "apagada")


def _mb(nombre: str, por_defecto: float) -> int:
    try:
        return max(1, int(float(os.environ.get(nombre) or por_defecto) * 1048576))
    except ValueError:
        return int(por_defecto * 1048576)


def carpeta() -> Optional[Path]:
    ruta = (os.environ.get("RUTA_CACHE_CONVERSION") or "").strip()
    return Path(ruta) if ruta else None


# ── Version del conversor ──

_version: Dict[str, str] = {}


def version_del_conversor(conversion: Any) -> str:
    archivo = getattr(conversion, "__file__", "") or ""
    if archivo in _version:
        return _version[archivo]
    h = hashlib.sha256(f"formato {FORMATO}".encode())
    try:
        h.update(Path(archivo).read_bytes())
    except OSError:
        h.update(b"sin-archivo")
    try:
        from pipeline import modelo

        h.update(modelo.INSTRUCCION_DE_TRANSCRIPCION.encode())
        h.update(str(modelo.MAX_TOKENS_DE_TRANSCRIPCION).encode())
    except Exception:
        pass
    try:
        h.update(subprocess.run(["tesseract", "--version"], capture_output=True,
                                timeout=10).stdout[:200])
    except Exception:
        h.update(b"sin-tesseract")
    datos = os.environ.get("TESSDATA_PREFIX") or ""
    if datos and os.path.isdir(datos):
        for nombre in sorted(os.listdir(datos)):
            if nombre.endswith(".traineddata"):
                tam = os.path.getsize(os.path.join(datos, nombre))
                h.update(f"{nombre}:{tam}".encode())
    _version[archivo] = h.hexdigest()[:16]
    return _version[archivo]


def _quien_transcribe(transcriptor: Any) -> Optional[str]:
    """"sin_vision", "<clase>:<modelo>" o None si no se sabe (no se usa la cache)."""
    t = getattr(transcriptor, "original", transcriptor)
    if t is None:
        return "sin_vision"
    propia = getattr(t, "clave_cache", None)
    if isinstance(propia, str) and propia:
        return propia
    duenio = getattr(t, "__self__", None)
    nombre = getattr(duenio, "nombre", None)
    if duenio is not None and isinstance(nombre, str) and nombre:
        real = getattr(duenio, "real", None)  # un envoltorio que observa
        clase = type(real).__name__ if real is not None else type(duenio).__name__
        return f"{clase}:{nombre}"
    return None


# ── Almacen ──

_cerrojo = threading.Lock()
_memoria: "collections.OrderedDict[str, Tuple[int, dict]]" = collections.OrderedDict()
_bytes_en_memoria = 0
_bytes_en_disco: Dict[str, int] = {}
contadores = {"aciertos": 0, "fallos": 0, "guardados": 0, "sin_guardar": 0,
              "borrados": 0}


def clave(huella: str, nombre: str, version: str, opciones: Dict[str, Any]) -> str:
    texto = json.dumps({"v": version, "sha256": huella, "nombre": nombre,
                        "opciones": opciones}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(texto.encode()).hexdigest()


def _ruta(k: str) -> Optional[Path]:
    base = carpeta()
    return base / k[:2] / f"{k}.json" if base else None


def leer(k: str) -> Optional[dict]:
    with _cerrojo:
        if k in _memoria:
            _memoria.move_to_end(k)
            return _memoria[k][1]
    ruta = _ruta(k)
    if ruta is None:
        return None
    try:
        d = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if d.get("clave") != k:
        return None
    try:
        os.utime(ruta, None)  # LRU: lo usado queda como reciente
    except OSError:
        pass
    _a_memoria(k, d, len(json.dumps(d, ensure_ascii=False)))
    return d


def _a_memoria(k: str, d: dict, tam: int) -> None:
    global _bytes_en_memoria
    tope = _mb("CACHE_CONVERSION_MEMORIA_MB", 256)
    with _cerrojo:
        if k in _memoria:
            return
        _memoria[k] = (tam, d)
        _bytes_en_memoria += tam
        while _bytes_en_memoria > tope and _memoria:
            _, (t, _d) = _memoria.popitem(last=False)
            _bytes_en_memoria -= t


def guardar(k: str, valor: dict) -> None:
    d = {**valor, "clave": k, "guardado": time.time()}
    texto = json.dumps(d, ensure_ascii=False)
    _a_memoria(k, d, len(texto))
    with _cerrojo:
        contadores["guardados"] += 1
    ruta = _ruta(k)
    if ruta is None:
        return
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(texto, encoding="utf-8")
        os.replace(tmp, ruta)
    except OSError:
        return
    raiz = str(carpeta())
    with _cerrojo:
        if raiz not in _bytes_en_disco or contadores["guardados"] % 200 == 0:
            _bytes_en_disco[raiz] = _tamano_en_disco()
        else:
            _bytes_en_disco[raiz] += len(texto.encode())
        if _bytes_en_disco[raiz] > _mb("CACHE_CONVERSION_MB", 2048):
            _bytes_en_disco[raiz] = _recortar_disco()


def _tamano_en_disco() -> int:
    base = carpeta()
    total = 0
    for p in (base.glob("*/*.json") if base else ()):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def _recortar_disco() -> int:
    """Borra lo menos usado hasta quedar en el 90 % del tope."""
    base = carpeta()
    objetivo = int(_mb("CACHE_CONVERSION_MB", 2048) * 0.9)
    archivos = []
    for p in (base.glob("*/*.json") if base else ()):
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


def vaciar_memoria() -> None:
    global _bytes_en_memoria
    with _cerrojo:
        _memoria.clear()
        _bytes_en_memoria = 0


def estado() -> Dict[str, Any]:
    return {"activa": activa(), "carpeta": str(carpeta() or ""),
            "en_memoria": len(_memoria), **contadores}


# ── La conversion con cache ──

_fallos_de_vision: contextvars.ContextVar = contextvars.ContextVar(
    "fallos_de_vision", default=None)


class _Vigilado:
    """El transcriptor tal cual; anota si una llamada fallo (la conversion se lo calla)."""

    def __init__(self, original: Callable[[bytes], str]) -> None:
        self.original = original

    def __call__(self, jpeg: bytes, *a: Any, **k: Any) -> str:
        try:
            return self.original(jpeg, *a, **k)
        except Exception:
            fallos = _fallos_de_vision.get()
            if fallos is not None:
                fallos.append(1)
            raise


def _avisar(nombre: str, como: str) -> None:
    if OBSERVADOR is not None:
        try:
            OBSERVADOR(nombre, como)
        except Exception:
            pass


def convertir(datos: bytes, nombre: str, claves_candidatas: Optional[List[str]] = None,
              presupuesto: Any = None) -> Any:
    """`conversion.convertir` con cache. Devuelve la `Conversion`."""
    from pipeline import conversion

    def llamar():
        return conversion.convertir(datos, nombre, claves_candidatas,
                                    presupuesto=presupuesto)

    if not activa() or not datos:
        return llamar()
    trans = conversion.TRANSCRIPTOR_IA
    vision = _quien_transcribe(trans)
    if vision is None:
        return llamar()
    con_ocr = presupuesto is None or float(getattr(presupuesto, "total_s", 1) or 0) > 0
    cupo = int(getattr(presupuesto, "imagenes_ia", 1) or 0) if presupuesto is not None else 1
    opciones = {
        "ocr": con_ocr,
        # Sin cupo de vision la lectura es otra: otra clave.
        "vision": vision if (cupo > 0 and vision != "sin_vision") else "sin_vision",
        "max_paginas": conversion.LIMITE_PAGINAS_PDF,
        "max_caracteres": conversion.LIMITE_CARACTERES,
        "claves": (hashlib.sha256(json.dumps(sorted(map(str, claves_candidatas))).encode())
                   .hexdigest()[:16] if claves_candidatas else ""),
    }
    huella = hashlib.sha256(datos).hexdigest()
    k = clave(huella, nombre, version_del_conversor(conversion), opciones)
    d = leer(k)
    queda = presupuesto.queda() if presupuesto is not None else float("inf")
    if (d and int(d.get("imagenes_ia") or 0) <= cupo
            and float(d.get("ocr_s") or 0) <= queda):
        campos = {f.name for f in dataclasses.fields(conversion.Conversion)}
        c = conversion.Conversion(**{x: v for x, v in (d.get("conversion") or {}).items()
                                     if x in campos})
        if presupuesto is not None:
            presupuesto.gastar(float(d.get("ocr_s") or 0))
            with conversion._CERROJO_DEL_CUPO_IA:
                presupuesto.imagenes_ia = max(
                    0, int(presupuesto.imagenes_ia) - int(d.get("imagenes_ia") or 0))
        with _cerrojo:
            contadores["aciertos"] += 1
        _avisar(nombre, "acierto")
        return c
    with _cerrojo:
        contadores["fallos"] += 1
    vigilado = trans is not None and not isinstance(trans, _Vigilado)
    if vigilado:
        conversion.TRANSCRIPTOR_IA = _Vigilado(trans)
    fallos: List[int] = []
    marca = _fallos_de_vision.set(fallos)
    gastado0 = float(getattr(presupuesto, "gastado_s", 0) or 0) if presupuesto is not None else 0.0
    t0 = time.monotonic()
    try:
        c = llamar()
    finally:
        _fallos_de_vision.reset(marca)
    usadas = (cupo - int(getattr(presupuesto, "imagenes_ia", cupo) or 0)
              if presupuesto is not None else 0)
    ocr_s = ((float(getattr(presupuesto, "gastado_s", 0) or 0) - gastado0)
             if presupuesto is not None else time.monotonic() - t0)
    a_medias = (bool(c.error) or bool(fallos)
                or any(x in (c.aviso or "") for x in _A_MEDIAS)
                or (presupuesto is not None and (
                    presupuesto.agotado or (cupo > 0 and presupuesto.imagenes_ia <= 0))))
    _avisar(nombre, "fallo")
    if a_medias:
        with _cerrojo:
            contadores["sin_guardar"] += 1
        return c
    guardar(k, {"conversion": dataclasses.asdict(c), "imagenes_ia": max(0, usadas),
                "ocr_s": round(max(0.0, ocr_s), 3), "nombre": nombre,
                "bytes": len(datos), "sha256": huella, "opciones": opciones})
    return c
