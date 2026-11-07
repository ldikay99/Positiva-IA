"""
La tabla integrada: un almacén pequeño para que una automatización guarde
estado sin una base externa (lo ya procesado, contadores, un registro…).

Un archivo SQLite por ámbito en <estado>/tablas: «flujo-<id>» (privado del
flujo, por defecto) o «compartida» (la ven todos los flujos). Cada fila es
{clave, datos JSON, creada, actualizada}. Topes: 50.000 filas por ámbito y
64 KB por fila.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import busqueda
from .base import ErrorDeNegocio, ruta_estado

NOMBRE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
CAMPO = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$")
MAX_FILAS_AMBITO = 50_000
MAX_BYTES_FILA = 65_536
MODOS = ("reemplazar", "combinar", "insertar")


def _archivo(ambito: str) -> Path:
    if not re.fullmatch(r"(flujo-[a-z0-9-]{1,41}|compartida)", ambito):
        raise ErrorDeNegocio(f"ámbito de tabla inválido «{ambito}»")
    carpeta = ruta_estado() / "tablas"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta / f"{ambito}.sqlite"


def _conectar(ambito: str) -> sqlite3.Connection:
    con = sqlite3.connect(str(_archivo(ambito)), timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS filas (tabla TEXT NOT NULL, clave TEXT NOT NULL, datos TEXT NOT NULL, "
                "creada REAL NOT NULL, actualizada REAL NOT NULL, PRIMARY KEY (tabla, clave))")
    return con


def revisar_tabla(tabla: Any) -> str:
    t = str(tabla or "").strip()
    if not NOMBRE.match(t):
        raise ErrorDeNegocio(f"nombre de tabla «{t[:60]}» inválido (minúsculas, números y _, empieza por letra)")
    return t


def guardar(ambito: str, tabla: str, clave: Optional[str], datos: Any, modo: str = "reemplazar") -> Tuple[str, bool, dict]:
    """(clave, si era nueva, la fila guardada). En modo «insertar», si la clave ya existe no cambia nada."""
    tabla = revisar_tabla(tabla)
    if modo not in MODOS:
        raise ErrorDeNegocio(f"modo «{modo}» (válidos: {', '.join(MODOS)})")
    if not isinstance(datos, dict):
        datos = {"valor": datos}
    clave = str(clave).strip() if clave not in (None, "") else uuid.uuid4().hex[:16]
    if len(clave) > 300:
        raise ErrorDeNegocio("la clave pasa de 300 caracteres")
    ahora = time.time()
    con = _conectar(ambito)
    try:
        previo = con.execute("SELECT datos, creada FROM filas WHERE tabla = ? AND clave = ?", (tabla, clave)).fetchone()
        if previo and modo == "insertar":
            return clave, False, {"clave": clave, **json.loads(previo[0])}
        if previo and modo == "combinar":
            datos = {**json.loads(previo[0]), **datos}
        texto = json.dumps(datos, ensure_ascii=False, default=str)
        if len(texto.encode("utf-8")) > MAX_BYTES_FILA:
            raise ErrorDeNegocio(f"la fila pasa de {MAX_BYTES_FILA // 1024} KB")
        if not previo:
            n = con.execute("SELECT COUNT(*) FROM filas").fetchone()[0]
            if n >= MAX_FILAS_AMBITO:
                raise ErrorDeNegocio(f"la tabla integrada llegó a su tope de {MAX_FILAS_AMBITO:,} filas en «{ambito}»")
        con.execute("INSERT INTO filas (tabla, clave, datos, creada, actualizada) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (tabla, clave) DO UPDATE SET datos = excluded.datos, actualizada = excluded.actualizada",
                    (tabla, clave, texto, previo[1] if previo else ahora, ahora))
        con.commit()
    finally:
        con.close()
    return clave, not previo, {"clave": clave, **datos}


def campo_valido(campo: Any) -> bool:
    """Un campo de la fila («estado», «clave») o una ruta dentro de un objeto («datos.estado»)."""
    return isinstance(campo, str) and all(CAMPO.match(x) for x in campo.split("."))


def _campo_de_fila(fila: dict, campo: str) -> Any:
    v: Any = fila
    for parte in campo.split("."):
        if not isinstance(v, dict):
            return None
        v = v.get(parte)
    return v


def consultar(ambito: str, tabla: str, clave: Optional[str] = None, filtro: Any = None,
              limite: int = 100, orden: str = "-creada", modo: str = "todas") -> List[dict]:
    """Las filas de una tabla. filtro: {campo: valor} (cada par tiene que ser igual) o una lista de condiciones
    {campo | campos, operador, valor, …} con los mismos operadores y la misma normalización que filtro y condición
    (sin distinguir mayúsculas, tildes ni espacios, salvo que se pida); modo: todas o alguna."""
    tabla = revisar_tabla(tabla)
    condiciones = busqueda.condiciones_de_filtro(filtro)
    for c in condiciones:
        for campo in busqueda.campos_de(c, ""):
            if not campo_valido(campo):
                raise ErrorDeNegocio(f"campo de filtro inválido «{campo}»")
        if (c.get("operador") or "contiene") not in busqueda.OPERADORES:
            raise ErrorDeNegocio(f"operador de filtro desconocido «{c.get('operador')}»")
    if modo not in busqueda.MODOS:
        raise ErrorDeNegocio("modo: todas o alguna")
    sql = "SELECT clave, datos, creada, actualizada FROM filas WHERE tabla = ?"
    args: List[Any] = [tabla]
    if clave not in (None, ""):
        sql += " AND clave = ?"
        args.append(str(clave))
    campo, desc = (orden[1:], True) if orden.startswith("-") else (orden, False)
    if campo not in ("creada", "actualizada", "clave"):
        raise ErrorDeNegocio("orden: creada, actualizada o clave (con «-» delante para descendente)")
    tope = max(1, min(500, int(limite or 100)))
    sql += f" ORDER BY {campo} {'DESC' if desc else 'ASC'}"
    if not condiciones:
        sql += " LIMIT ?"
        args.append(tope)
    if not _archivo(ambito).exists():
        return []
    r: List[dict] = []
    con = _conectar(ambito)
    try:
        for c_, d, cr, ac in con.execute(sql, args):
            fila = {"clave": c_, **json.loads(d), "_creada": cr, "_actualizada": ac}
            if condiciones and not busqueda.evaluar(condiciones, modo, lambda k: _campo_de_fila(fila, k))[0]:
                continue
            r.append(fila)
            if len(r) >= tope:
                break
    finally:
        con.close()
    return r


def borrar(ambito: str, tabla: str, clave: str) -> bool:
    tabla = revisar_tabla(tabla)
    con = _conectar(ambito)
    try:
        n = con.execute("DELETE FROM filas WHERE tabla = ? AND clave = ?", (tabla, str(clave))).rowcount
        con.commit()
    finally:
        con.close()
    return n > 0
