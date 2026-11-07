"""
Hojas de cálculo a filas: CSV (detecta «,», «;» o tabulador) y XLSX
(openpyxl, MIT, en solo lectura y con los valores calculados).
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional, Tuple

from .base import ErrorDeNegocio, a_json


def es_xlsx(datos: bytes, nombre: str) -> bool:
    return datos[:2] == b"PK" and not nombre.lower().endswith((".csv", ".txt", ".tsv"))


def _texto(datos: bytes) -> str:
    for cod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return datos.decode(cod)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", errors="replace")


def _encabezados(fila: List[Any]) -> List[str]:
    vistos: Dict[str, int] = {}
    r = []
    for i, x in enumerate(fila):
        k = str(x).strip() if x not in (None, "") else f"columna_{i + 1}"
        if k in vistos:
            vistos[k] += 1
            k = f"{k}_{vistos[k]}"
        else:
            vistos[k] = 1
        r.append(k)
    return r


def filas(datos: bytes, nombre: str, hoja: Optional[str] = None, max_filas: int = 500,
          con_encabezados: bool = True) -> Tuple[List[Dict[str, Any]], bool, List[str]]:
    """(filas como objetos, si se truncó, columnas)."""
    crudas: List[List[Any]] = []
    truncado = False
    if es_xlsx(datos, nombre):
        import openpyxl

        try:
            libro = openpyxl.load_workbook(io.BytesIO(datos), read_only=True, data_only=True)
        except Exception as e:
            raise ErrorDeNegocio(f"no se pudo abrir {nombre} como Excel: {type(e).__name__}") from e
        try:
            if hoja:
                if hoja not in libro.sheetnames:
                    raise ErrorDeNegocio(f"{nombre} no tiene la hoja «{hoja}» (hay: {', '.join(libro.sheetnames)})")
                h = libro[hoja]
            else:
                h = libro.worksheets[0]
            for f in h.iter_rows(values_only=True):
                if len(crudas) > max_filas + 1:
                    truncado = True
                    break
                if f and any(x not in (None, "") for x in f):
                    crudas.append(list(f))
        finally:
            libro.close()
    else:
        texto = _texto(datos)
        primera = texto.splitlines()[0] if texto else ""
        sep = ";" if primera.count(";") > primera.count(",") else ("\t" if "\t" in primera else ",")
        for f in csv.reader(io.StringIO(texto), delimiter=sep):
            if len(crudas) > max_filas + 1:
                truncado = True
                break
            if any(x.strip() for x in f):
                crudas.append(f)
    if not crudas:
        return [], False, []
    if con_encabezados:
        columnas, cuerpo = _encabezados(crudas[0]), crudas[1:]
    else:
        columnas, cuerpo = [f"columna_{i + 1}" for i in range(max(len(f) for f in crudas))], crudas
    if len(cuerpo) > max_filas:
        cuerpo, truncado = cuerpo[:max_filas], True
    r = [{c: a_json(f[i]) if i < len(f) else None for i, c in enumerate(columnas)} for f in cuerpo]
    return r, truncado, columnas
