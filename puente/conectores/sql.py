"""
Consultas a bases de datos: PostgreSQL (pg8000, BSD), MySQL/MariaDB (PyMySQL,
MIT), SQLite (biblioteca estándar, solo archivos del espacio de archivos),
SQL Server (python-tds, MIT) y Oracle (python-oracledb en modo «thin»,
Apache-2.0; sin el cliente de Oracle).

Límites:
- una sola sentencia (nunca «;» en medio) y sin comentarios (se quitan antes
  de mandarla: MySQL ejecuta lo que va en /*! … */);
- sql_consulta solo acepta SELECT / WITH / VALUES y corre en una transacción
  de solo lectura (y en SQLite con el archivo abierto en modo lectura y un
  autorizador que niega todo lo demás);
- sql_ejecutar solo acepta INSERT / UPDATE / DELETE, UPDATE y DELETE exigen
  WHERE (salvo permitir_sin_where) y nunca DDL;
- parámetros con nombre (`:documento`): los valores nunca se pegan en el texto;
- tiempo de espera en la conexión y en la sentencia, y tope de filas.
"""
from __future__ import annotations

import re
import sqlite3
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple

from .base import ErrorDeNegocio, a_json, destino_seguro, ruta_segura

PALABRA = re.compile(r"[a-z_][a-z0-9_$]*")
PARAM = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
INICIO_LECTURA = {"select", "with", "values"}
INICIO_ESCRITURA = {"insert", "update", "delete", "with"}
DML = {"insert", "update", "delete", "merge", "upsert"}
# Nunca, en ninguna de las dos (palabras sueltas, fuera de textos y comentarios).
PROHIBIDAS_SIEMPRE = {
    "drop", "alter", "create", "truncate", "grant", "revoke", "attach", "detach", "pragma", "vacuum", "reindex", "copy",
    "call", "exec", "execute", "lock", "unlock", "load", "rename", "begin", "commit", "rollback", "savepoint", "release",
    "prepare", "deallocate", "listen", "notify", "unlisten", "refresh", "cluster", "discard", "checkpoint", "handler",
    "outfile", "dumpfile", "shutdown", "kill", "flush", "install", "uninstall", "reset", "import",
    # funciones peligrosas
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "lo_import", "lo_export", "lo_unlink", "dblink",
    "dblink_exec", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile", "set_config",
    "pg_advisory_lock", "pg_sleep", "pg_sleep_for", "pg_sleep_until", "nextval", "setval", "load_file", "sleep", "benchmark",
    "sys_exec", "sys_eval", "load_extension", "writefile", "readfile", "fts3_tokenizer",
}
# En lectura también los pares «replace into» y «comment on» (replace() y una columna «comment» sí valen).
PROHIBIDAS_LECTURA = PROHIBIDAS_SIEMPRE | DML | {"into", "set", "replace_into", "comment_on", "do"}
PROHIBIDAS_ESCRITURA = PROHIBIDAS_SIEMPRE | {"merge", "upsert", "do"}
# Lo propio de cada motor que ejecuta código, lee archivos o sale a la red (palabras sueltas y prefijos).
PROHIBIDAS_DIALECTO = {
    "sqlserver": ({"openrowset", "opendatasource", "openquery", "openxml", "waitfor", "dbcc", "reconfigure", "bulk", "use", "go",
                   "backup", "restore", "deny", "external"}, ("sp_", "xp_")),
    "oracle": ({"httpuritype", "dburitype", "xdburitype", "java", "declare", "procedure", "function", "trigger", "audit", "noaudit",
                "flashback", "purge"}, ("dbms_", "utl_")),
}


def _limpiar(sql: str, dialecto: str) -> Tuple[str, str, List[str]]:
    """(sql sin comentarios para mandar, esqueleto sin textos para revisar, parámetros en orden)."""
    salida, esqueleto, params = [], [], []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        dos = sql[i:i + 2]
        if dos == "--" or (c == "#" and dialecto == "mysql"):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            salida.append(" ")
            esqueleto.append(" ")
            continue
        if dos == "/*":
            j = sql.find("*/", i + 2)
            if j < 0:
                raise ErrorDeNegocio("la consulta tiene un comentario /* sin cerrar")
            i = j + 2
            salida.append(" ")
            esqueleto.append(" ")
            continue
        if c == "[" and dialecto == "sqlserver":  # [nombre con espacios] de SQL Server: un identificador, no palabras
            j = sql.find("]", i + 1)
            if j < 0:
                raise ErrorDeNegocio("la consulta tiene un [ sin cerrar")
            salida.append(sql[i:j + 1])
            esqueleto.append('""')
            i = j + 1
            continue
        if c in ("'", '"', "`") or (c == "$" and dialecto == "postgres" and re.match(r"\$[A-Za-z_]*\$", sql[i:])):
            if c == "$":
                tag = re.match(r"\$[A-Za-z_]*\$", sql[i:]).group(0)
                j = sql.find(tag, i + len(tag))
                if j < 0:
                    raise ErrorDeNegocio("la consulta tiene un texto $…$ sin cerrar")
                fin = j + len(tag)
            else:
                j = i + 1
                while True:
                    if j >= n:
                        raise ErrorDeNegocio(f"la consulta tiene un {c} sin cerrar")
                    if sql[j] == "\\" and dialecto == "mysql" and c != "`":
                        j += 2
                        continue
                    if sql[j] == c:
                        if j + 1 < n and sql[j + 1] == c:
                            j += 2
                            continue
                        break
                    j += 1
                fin = j + 1
            salida.append(sql[i:fin])
            esqueleto.append("''" if c in ("'", "$") else '""')
            i = fin
            continue
        if c == ":" and i + 1 < n and sql[i + 1] != ":" and (i == 0 or sql[i - 1] != ":"):
            m = PARAM.match(sql, i + 1)
            if m:
                params.append(m.group(0))
                salida.append(sql[i:m.end()])
                esqueleto.append(" ? ")
                i = m.end()
                continue
        salida.append(c)
        esqueleto.append(c)
        i += 1
    return "".join(salida).strip(), "".join(esqueleto).strip(), params


def revisar(sql: Any, modo: str, dialecto: str = "postgres", permitir_sin_where: bool = False) -> Tuple[str, List[str]]:
    """Revisa una sentencia; devuelve (sql listo, parámetros que usa) o ErrorDeNegocio con el porqué."""
    if not isinstance(sql, str) or not sql.strip():
        raise ErrorDeNegocio("falta la consulta SQL")
    if len(sql) > 20000:
        raise ErrorDeNegocio("la consulta pasa de 20.000 caracteres")
    limpio, esqueleto, params = _limpiar(sql, dialecto)
    # una sola sentencia: se admite un «;» final y nada más
    sin_final = esqueleto.rstrip().rstrip(";").rstrip()
    if ";" in sin_final:
        raise ErrorDeNegocio("solo se permite UNA sentencia SQL (hay un «;» en medio)")
    if esqueleto.rstrip().endswith(";"):
        limpio = limpio.rstrip().rstrip(";").rstrip()
    palabras = PALABRA.findall(sin_final.lower())
    if not palabras:
        raise ErrorDeNegocio("la consulta está vacía")
    pares = {f"{a}_{b}" for a, b in zip(palabras, palabras[1:])}
    if dialecto in PROHIBIDAS_DIALECTO:
        sueltas, prefijos = PROHIBIDAS_DIALECTO[dialecto]
        malas = sorted({p for p in palabras if p in sueltas or p.startswith(prefijos)})
        if malas:
            raise ErrorDeNegocio(f"la consulta no admite {', '.join(m.upper() for m in malas)} (ejecuta código, lee archivos o sale a la red)")
    if modo == "lectura":
        if palabras[0] not in INICIO_LECTURA:
            raise ErrorDeNegocio(f"sql_consulta solo acepta SELECT, WITH o VALUES (empieza por {palabras[0].upper()}); para escribir usa sql_ejecutar")
        malas = sorted((set(palabras) | pares) & PROHIBIDAS_LECTURA)
        if malas:
            raise ErrorDeNegocio("sql_consulta es de solo lectura: no admite "
                                 + ", ".join((m.replace("_", " ") if m in pares else m).upper() for m in malas))
    else:
        if palabras[0] not in INICIO_ESCRITURA or (palabras[0] == "with" and not set(palabras) & {"insert", "update", "delete"}):
            raise ErrorDeNegocio(f"sql_ejecutar solo acepta INSERT, UPDATE o DELETE (empieza por {palabras[0].upper()})")
        malas = sorted(set(palabras) & PROHIBIDAS_ESCRITURA)
        if malas:
            raise ErrorDeNegocio(f"sql_ejecutar no admite {', '.join(m.upper() for m in malas)}")
        verbo = next(p for p in palabras if p in ("insert", "update", "delete"))
        if verbo in ("update", "delete") and "where" not in palabras and not permitir_sin_where:
            raise ErrorDeNegocio(f"{verbo.upper()} sin WHERE cambiaría toda la tabla: agrega WHERE (o permitir_sin_where: true)")
    return limpio, list(dict.fromkeys(params))


def _a_pymysql(sql: str, params: List[str], dialecto: str = "mysql") -> str:
    """:nombre → %(nombre)s, y todo «%» literal → «%%» (PyMySQL y python-tds formatean el texto completo)."""
    limpio, _, _ = _limpiar(sql, dialecto)
    r, i, n = [], 0, len(limpio)
    while i < n:
        c = limpio[i]
        if c == "[" and dialecto == "sqlserver":
            j = limpio.find("]", i + 1)
            r.append(limpio[i:j + 1].replace("%", "%%"))
            i = j + 1
            continue
        if c in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if limpio[j] == "\\" and c != "`" and dialecto == "mysql":
                    j += 2
                    continue
                if limpio[j] == c:
                    if j + 1 < n and limpio[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            r.append(limpio[i:j + 1].replace("%", "%%"))
            i = j + 1
            continue
        if c == ":" and i + 1 < n and limpio[i + 1] != ":" and (i == 0 or limpio[i - 1] != ":"):
            m = PARAM.match(limpio, i + 1)
            if m and m.group(0) in params:
                r.append(f"%({m.group(0)})s")
                i = m.end()
                continue
        r.append("%%" if c == "%" else c)
        i += 1
    return "".join(r)


# ----------------------------------------------------------------- motores

def _sqlite(valores: dict, sql: str, params: Dict[str, Any], modo: str, max_filas: int, timeout_s: float) -> dict:
    ruta = ruta_segura(valores.get("ruta"))
    if modo == "lectura" and not ruta.exists():
        raise ErrorDeNegocio(f"no existe la base SQLite {valores.get('ruta')} en el espacio de archivos")
    if modo == "lectura":
        con = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True, timeout=min(timeout_s, 10))
    else:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(ruta), timeout=min(timeout_s, 10))
    limite = time.monotonic() + timeout_s
    con.set_progress_handler(lambda: 1 if time.monotonic() > limite else 0, 2000)
    permitidos = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    if modo != "lectura":
        permitidos |= {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_TRANSACTION}
    funciones_malas = {"load_extension", "writefile", "readfile", "edit", "fts3_tokenizer"}

    def autorizar(accion, a1, a2, a3, a4):
        if accion not in permitidos:
            return sqlite3.SQLITE_DENY
        if accion == sqlite3.SQLITE_FUNCTION and str(a2 or "").lower() in funciones_malas:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        con.set_authorizer(autorizar)
        try:
            cur = con.execute(sql, params)
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e).lower():
                raise TimeoutError(f"la consulta pasó de {timeout_s:g} s") from e
            raise ErrorDeNegocio(f"SQLite: {e}") from e
        except (sqlite3.DatabaseError, sqlite3.Warning) as e:
            raise ErrorDeNegocio(f"SQLite: {e}") from e
        columnas = [d[0] for d in cur.description or []]
        filas = cur.fetchmany(max_filas + 1) if columnas else []
        afectadas = cur.rowcount if modo != "lectura" else None
        if modo != "lectura":
            con.commit()
    finally:
        con.close()
    return _resultado(columnas, filas, max_filas, afectadas)


def _postgres(valores: dict, sql: str, params: Dict[str, Any], modo: str, max_filas: int, timeout_s: float) -> dict:
    host, puerto = str(valores.get("host") or ""), int(valores.get("puerto") or 5432)
    ip = destino_seguro(host, puerto, red_privada=True)  # antes que nada: un destino vetado no llega ni a cargar el controlador
    import pg8000.native as pgn

    usa_ssl = _verdad(valores.get("ssl"))
    contexto = ssl.create_default_context() if usa_ssl else None
    try:
        con = pgn.Connection(user=str(valores.get("usuario") or ""), password=str(valores.get("clave") or ""),
                             host=host if usa_ssl else ip, port=puerto, database=str(valores.get("base") or "") or None,
                             ssl_context=contexto, timeout=timeout_s, application_name="positiva-ia-flujos")
    except Exception as e:
        raise _error_conexion("PostgreSQL", e)
    try:
        con.run(f"SET statement_timeout = {int(timeout_s * 1000)}")
        con.run("START TRANSACTION READ ONLY" if modo == "lectura" else "START TRANSACTION")
        texto = f"SELECT * FROM (\n{sql}\n) AS _consulta LIMIT {max_filas + 1}" if modo == "lectura" else sql
        try:
            filas = con.run(texto, **params)
        except Exception as e:
            raise _error_sql("PostgreSQL", e)
        columnas = [c.get("name") for c in (con.columns or [])]
        afectadas = con.row_count if modo != "lectura" else None
        con.run("ROLLBACK" if modo == "lectura" else "COMMIT")
    finally:
        try:
            con.close()
        except Exception:
            pass
    return _resultado(columnas, filas or [], max_filas, afectadas)


def _mysql(valores: dict, sql: str, params: Dict[str, Any], modo: str, max_filas: int, timeout_s: float) -> dict:
    host, puerto = str(valores.get("host") or ""), int(valores.get("puerto") or 3306)
    ip = destino_seguro(host, puerto, red_privada=True)
    import pymysql

    usa_ssl = _verdad(valores.get("ssl"))
    try:
        con = pymysql.connect(host=host if usa_ssl else ip, port=puerto, user=str(valores.get("usuario") or ""),
                              password=str(valores.get("clave") or ""), database=str(valores.get("base") or "") or None,
                              connect_timeout=max(1, int(timeout_s)), read_timeout=max(1, int(timeout_s)),
                              write_timeout=max(1, int(timeout_s)), charset="utf8mb4", autocommit=False, client_flag=0,
                              ssl=ssl.create_default_context() if usa_ssl else None, local_infile=False)
    except Exception as e:
        raise _error_conexion("MySQL", e)
    try:
        cur = con.cursor()
        try:
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_s * 1000)}")
        except Exception:
            pass  # MariaDB no lo tiene
        cur.execute("START TRANSACTION READ ONLY" if modo == "lectura" else "START TRANSACTION")
        texto = f"SELECT * FROM (\n{sql}\n) AS _consulta LIMIT {max_filas + 1}" if modo == "lectura" else sql
        try:
            cur.execute(_a_pymysql(texto, list(params)) if params else texto, params or None)
        except Exception as e:
            raise _error_sql("MySQL", e)
        columnas = [d[0] for d in cur.description or []]
        filas = cur.fetchmany(max_filas + 1) if columnas else []
        afectadas = cur.rowcount if modo != "lectura" else None
        con.rollback() if modo == "lectura" else con.commit()
    finally:
        try:
            con.close()
        except Exception:
            pass
    return _resultado(columnas, filas, max_filas, afectadas)


def _conectar_sqlserver_real(**kw: Any) -> Any:
    import pytds
    return pytds.connect(**kw)


def _conectar_oracle_real(**kw: Any) -> Any:
    import oracledb  # modo thin: sin el cliente de Oracle
    return oracledb.connect(**kw)


# Las pruebas los cambian por dobles (no hay un SQL Server ni un Oracle de verdad para probar).
conectar_sqlserver = _conectar_sqlserver_real
conectar_oracle = _conectar_oracle_real
CA_SISTEMA = "/etc/ssl/certs/ca-certificates.crt"


def _sqlserver(valores: dict, sql: str, params: Dict[str, Any], modo: str, max_filas: int, timeout_s: float) -> dict:
    host, puerto = str(valores.get("host") or ""), int(valores.get("puerto") or 1433)
    ip = destino_seguro(host, puerto, red_privada=True)
    cifrado = _verdad(valores.get("cifrado", True))
    kw: Dict[str, Any] = {"server": host if cifrado else ip, "port": puerto, "database": str(valores.get("base") or "") or None,
                          "user": str(valores.get("usuario") or ""), "password": str(valores.get("clave") or ""),
                          "login_timeout": max(1, int(timeout_s)), "timeout": max(1, int(timeout_s)), "autocommit": False,
                          "appname": "positiva-ia-flujos"}
    if cifrado:
        kw.update(cafile=CA_SISTEMA, validate_host=True)
    try:
        con = conectar_sqlserver(**kw)
    except ImportError as e:
        raise ErrorDeNegocio("el cifrado de SQL Server necesita pyOpenSSL en la imagen; o pon cifrado: false si el servidor no lo exige") from e
    except Exception as e:
        raise _error_conexion("SQL Server", e)
    try:
        cur = con.cursor()
        cur.execute(f"SET LOCK_TIMEOUT {int(timeout_s * 1000)}")
        if modo == "lectura":
            cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        try:
            if params:
                cur.execute(_a_pymysql(sql, list(params), "sqlserver"), params)
            else:
                cur.execute(sql)
        except Exception as e:
            raise _error_sql("SQL Server", e)
        columnas = [d[0] for d in cur.description or []]
        filas = cur.fetchmany(max_filas + 1) if columnas else []
        afectadas = cur.rowcount if modo != "lectura" else None
        con.rollback() if modo == "lectura" else con.commit()  # la lectura nunca deja nada: se deshace siempre
    finally:
        try:
            con.close()
        except Exception:
            pass
    return _resultado(columnas, filas, max_filas, afectadas)


def _oracle(valores: dict, sql: str, params: Dict[str, Any], modo: str, max_filas: int, timeout_s: float) -> dict:
    host, puerto = str(valores.get("host") or ""), int(valores.get("puerto") or 1521)
    ip = destino_seguro(host, puerto, red_privada=True)
    tls = _verdad(valores.get("tls"))
    kw: Dict[str, Any] = {"user": str(valores.get("usuario") or ""), "password": str(valores.get("clave") or ""),
                          "host": host if tls else ip, "port": puerto, "service_name": str(valores.get("servicio") or "") or None,
                          "protocol": "tcps" if tls else "tcp", "tcp_connect_timeout": float(timeout_s)}
    try:
        con = conectar_oracle(**kw)
    except Exception as e:
        raise _error_conexion("Oracle", e)
    try:
        con.call_timeout = int(timeout_s * 1000)
        cur = con.cursor()
        if modo == "lectura":
            cur.execute("SET TRANSACTION READ ONLY")
        texto = f"SELECT * FROM (\n{sql}\n) FETCH FIRST {max_filas + 1} ROWS ONLY" if modo == "lectura" else sql
        try:
            cur.execute(texto, params or {})
        except Exception as e:
            raise _error_sql("Oracle", e)
        columnas = [d[0] for d in cur.description or []]
        filas = cur.fetchmany(max_filas + 1) if columnas else []
        afectadas = cur.rowcount if modo != "lectura" else None
        con.rollback() if modo == "lectura" else con.commit()
    finally:
        try:
            con.close()
        except Exception:
            pass
    return _resultado(columnas, filas, max_filas, afectadas)


def _verdad(x: Any) -> bool:
    return x is True or str(x).strip().lower() in ("true", "si", "sí", "1", "requerido", "require")


def _error_conexion(que: str, e: Exception) -> Exception:
    if isinstance(e, (ErrorDeNegocio, TimeoutError)):
        return e
    texto = f"{type(e).__name__}: {e}"
    if "timed out" in texto.lower() or "timeout" in texto.lower():
        return TimeoutError(f"{que} no respondió a tiempo")
    if isinstance(e, (ConnectionError, OSError)):
        return ConnectionError(f"no se pudo conectar a {que}: {type(e).__name__}")
    return ErrorDeNegocio(f"no se pudo conectar a {que}: {texto[:300]}")


def _error_sql(que: str, e: Exception) -> Exception:
    texto = f"{e}"
    if "statement timeout" in texto.lower() or "max_execution_time" in texto.lower() or "timed out" in texto.lower():
        return TimeoutError(f"la consulta a {que} pasó del tiempo de espera")
    return ErrorDeNegocio(f"{que} rechazó la consulta: {texto[:400]}")


def _resultado(columnas: List[str], filas: List[Any], max_filas: int, afectadas: Optional[int]) -> dict:
    truncado = len(filas) > max_filas
    filas = filas[:max_filas]
    dicts = [{str(c): a_json(v) for c, v in zip(columnas, f)} for f in filas]
    r = {"columnas": columnas, "filas": dicts, "total": len(dicts), "truncado": truncado}
    if afectadas is not None:
        r["afectadas"] = afectadas
    return r


MOTORES = {"sqlite": _sqlite, "postgres": _postgres, "mysql": _mysql, "sqlserver": _sqlserver, "oracle": _oracle}


def ejecutar(tipo: str, valores: dict, sql: str, params: Dict[str, Any], modo: str, max_filas: int, timeout_s: float,
             permitir_sin_where: bool = False) -> dict:
    """Revisa y corre una sentencia. modo: «lectura» (sql_consulta) o «escritura» (sql_ejecutar)."""
    if tipo not in MOTORES:
        raise ErrorDeNegocio(f"tipo de base desconocido «{tipo}» (válidos: {', '.join(MOTORES)})")
    dialecto = tipo if tipo in ("mysql", "sqlite", "sqlserver", "oracle") else "postgres"
    limpio, usados = revisar(sql, modo, dialecto, permitir_sin_where)
    faltan = [p for p in usados if p not in params]
    if faltan:
        raise ErrorDeNegocio(f"la consulta usa :{', :'.join(faltan)} y no están en parametros")
    return MOTORES[tipo](valores, limpio, {k: params[k] for k in usados}, modo, max_filas, timeout_s)
