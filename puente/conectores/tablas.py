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


def consultar(ambito: str, tabla: str, clave: Optional[str] = None, filtro: Optional[Dict[str, Any]] = None,
              limite: int = 100, orden: str = "-creada") -> List[dict]:
    tabla = revisar_tabla(tabla)
    sql = "SELECT clave, datos, creada, actualizada FROM filas WHERE tabla = ?"
    args: List[Any] = [tabla]
    if clave not in (None, ""):
        sql += " AND clave = ?"
        args.append(str(clave))
    for k, v in (filtro or {}).items():
        if not CAMPO.match(str(k)):
            raise ErrorDeNegocio(f"campo de filtro inválido «{k}»")
        sql += f" AND CAST(json_extract(datos, '$.{k}') AS TEXT) = ?"
        # json_extract devuelve los booleanos como 1/0 y los números como números
        args.append(v if isinstance(v, str) else json.dumps(v) if isinstance(v, (dict, list)) else str(int(v)) if isinstance(v, bool) else str(v))
    campo, desc = (orden[1:], True) if orden.startswith("-") else (orden, False)
    if campo not in ("creada", "actualizada", "clave"):
        raise ErrorDeNegocio("orden: creada, actualizada o clave (con «-» delante para descendente)")
    sql += f" ORDER BY {campo} {'DESC' if desc else 'ASC'} LIMIT ?"
    args.append(max(1, min(500, int(limite or 100))))
    if not _archivo(ambito).exists():
        return []
    con = _conectar(ambito)
    try:
        filas = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return [{"clave": c, **json.loads(d), "_creada": cr, "_actualizada": ac} for c, d, cr, ac in filas]


def borrar(ambito: str, tabla: str, clave: str) -> bool:
    tabla = revisar_tabla(tabla)
    con = _conectar(ambito)
    try:
        n = con.execute("DELETE FROM filas WHERE tabla = ? AND clave = ?", (tabla, str(clave))).rowcount
        con.commit()
    finally:
        con.close()
    return n > 0
