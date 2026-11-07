"""
El espacio de archivos de los flujos (/datos/archivos): leer y guardar SOLO
dentro de él. Las rutas son relativas; «..», rutas absolutas y enlaces que
apunten afuera se rechazan (`base.ruta_segura`).
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Callable, Dict, List, Optional

from . import hojas
from .base import MAX_BYTES_ARCHIVO, ErrorDeNegocio, ruta_archivos, ruta_segura

FORMATOS_LEER = ("auto", "texto", "json", "filas")
FORMATOS_GUARDAR = ("texto", "json", "csv")
MODOS = ("crear", "reemplazar", "anexar")
TEXTO = (".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".sql", ".yml", ".yaml")


def leer(ruta: str, formato: str = "auto", max_caracteres: int = 60000, max_filas: int = 500, hoja: Optional[str] = None,
         convertir: Optional[Callable[[bytes, str], str]] = None) -> Dict[str, Any]:
    p = ruta_segura(ruta)
    if not p.is_file():
        raise ErrorDeNegocio(f"no existe el archivo «{ruta}» en el espacio de archivos")
    if p.stat().st_size > MAX_BYTES_ARCHIVO:
        raise ErrorDeNegocio(f"«{ruta}» pasa de {MAX_BYTES_ARCHIVO // 1_000_000} MB")
    datos = p.read_bytes()
    nombre = p.name.lower()
    if formato == "auto":
        formato = "json" if nombre.endswith(".json") else "filas" if nombre.endswith((".csv", ".tsv", ".xlsx", ".xlsm")) else "texto"
    r: Dict[str, Any] = {"ruta": str(p.relative_to(ruta_archivos().resolve())).replace("\\", "/"), "nombre": p.name, "bytes": len(datos), "formato": formato}
    if formato == "json":
        try:
            r["datos"] = json.loads(datos.decode("utf-8-sig"))
        except ValueError as e:
            raise ErrorDeNegocio(f"«{ruta}» no es un JSON válido: {e}") from e
    elif formato == "filas":
        r["filas"], r["truncado"], r["columnas"] = hojas.filas(datos, nombre, hoja, max_filas)
    else:
        if nombre.endswith(TEXTO) or not convertir:
            texto = hojas._texto(datos)
        else:
            texto = convertir(datos, p.name)
        r["texto"] = texto[:max_caracteres]
        r["truncado"] = len(texto) > max_caracteres
    return r


def _csv(filas: Any) -> str:
    if not isinstance(filas, list) or not all(isinstance(f, dict) for f in filas):
        raise ErrorDeNegocio("para guardar CSV el contenido tiene que ser una lista de objetos (filas)")
    columnas: List[str] = []
    for f in filas:
        for k in f:
            if k not in columnas:
                columnas.append(str(k))
    s = io.StringIO()
    w = csv.DictWriter(s, fieldnames=columnas, extrasaction="ignore")
    w.writeheader()
    for f in filas:
        w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in f.items()})
    return s.getvalue()


def guardar(ruta: str, contenido: Any, formato: str = "texto", modo: str = "reemplazar") -> Dict[str, Any]:
    if formato not in FORMATOS_GUARDAR:
        raise ErrorDeNegocio(f"formato «{formato}» (válidos: {', '.join(FORMATOS_GUARDAR)})")
    if modo not in MODOS:
        raise ErrorDeNegocio(f"modo «{modo}» (válidos: {', '.join(MODOS)})")
    p = ruta_segura(ruta)
    if p == ruta_archivos().resolve():
        raise ErrorDeNegocio("la ruta tiene que nombrar un archivo")
    if formato == "json":
        texto = json.dumps(contenido, ensure_ascii=False, indent=2, default=str)
    elif formato == "csv":
        texto = _csv(contenido)
    else:
        texto = contenido if isinstance(contenido, str) else json.dumps(contenido, ensure_ascii=False, default=str)
    datos = texto.encode("utf-8")
    if len(datos) > MAX_BYTES_ARCHIVO:
        raise ErrorDeNegocio(f"el archivo pasaría de {MAX_BYTES_ARCHIVO // 1_000_000} MB")
    if p.exists() and modo == "crear":
        raise ErrorDeNegocio(f"ya existe «{ruta}» (modo crear no sobrescribe)")
    p.parent.mkdir(parents=True, exist_ok=True)
    if modo == "anexar" and p.exists():
        if formato == "csv" and p.stat().st_size:
            datos = texto.split("\n", 1)[1].encode("utf-8") if "\n" in texto else b""  # sin repetir encabezados
        with open(p, "ab") as f:
            f.write(datos)
    else:
        p.write_bytes(datos)
    return {"ruta": str(p.relative_to(ruta_archivos().resolve())).replace("\\", "/"), "bytes": p.stat().st_size, "modo": modo}
