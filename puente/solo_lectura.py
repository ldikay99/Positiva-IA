"""
Lo que necesitan los trabajadores de PQRD y tutelas para leer de verdad SIN escribir.

Con la compuerta de radicación apagada el motor corre en MODO_TEST, y MODO_TEST
apaga también algunas lecturas (Redis de tutelas no consulta). Aquí no se sale de
MODO_TEST: se ponen adaptadores que solo saben leer.

  - `conectar_registro_solo_lectura`: el `conectar` del registro de radicados de
    PQRD (Cloud SQL, pg8000) en una sesión de solo lectura que solo deja SELECT, con
    una prueba rápida de alcance (la instancia solo tiene IP privada).
  - `ConsultaSupabase`: el `table().select().eq()…execute()` de supabase-py sobre
    PostgREST con GET (PQRD lo usaba hasta el motor 1d11744; queda para quien lo
    necesite). insert, update, upsert, delete y rpc lanzan PermissionError.
  - `RedisSoloLectura`: un cliente de Redis que solo deja GET, EXISTS, TTL y PING.
  - `conectar_postgres_solo_lectura`: una sesión con default_transaction_read_only.

Y lo común: si una variable está de verdad en el entorno (no es el relleno que se
pone para que el motor arranque), la compuerta (`radicacion_real`, con sus dos
llaves) y la máscara de los secretos del flujo.

Ningún valor secreto se imprime: los errores se recortan y pasan por la máscara.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# Lo que los trabajadores ponen para que el motor importe sin credenciales: no cuenta como valor.
RELLENOS = {"", "x", "no-se-usa", "http://127.0.0.1:9"}


def definida(nombre: str) -> bool:
    """Si el entorno del flujo trae esa variable con un valor real."""
    return (os.environ.get(nombre) or "").strip() not in RELLENOS


def faltantes(nombres) -> List[str]:
    return [n for n in nombres if not definida(n)]


def radicacion_real() -> bool:
    """
    La compuerta, con sus dos llaves: el servidor la pone (RADICACION_REAL=si) solo si
    una persona la encendió y el despliegue lo permite; aquí se exige también la segunda
    llave del contenedor. Un flujo no puede poner ninguna de las dos (entornos.ts).
    """
    return (os.environ.get("RADICACION_REAL", "").strip().lower() == "si"
            and os.environ.get("PERMITIR_RADICACION_REAL", "").strip().lower() == "si")


ROTULO_APAGADA = "Radicación real: apagada (simulación)"
ROTULO_ENCENDIDA = "Radicación real: ENCENDIDA (escribe como el job)"

# ------------------------------------------------------------------ máscara


class Mascara:
    """Tapa los valores secretos del flujo (SECRETOS_DEL_FLUJO trae sus NOMBRES) en un texto."""

    def __init__(self) -> None:
        self.valores: List[str] = []

    def recordar(self) -> "Mascara":
        nombres = [n for n in (os.environ.get("SECRETOS_DEL_FLUJO") or "").split(",") if n]
        vistos = set(self.valores)
        for n in nombres:
            v = os.environ.get(n) or ""
            if len(v) >= 4 and v not in RELLENOS and v not in vistos:
                vistos.add(v)
                self.valores.append(v)
                import json as _json
                escapado = _json.dumps(v, ensure_ascii=False)[1:-1]
                if escapado != v:
                    self.valores.append(escapado)
        self.valores.sort(key=len, reverse=True)
        return self

    def __call__(self, texto: str) -> str:
        for v in self.valores:
            if v in texto:
                texto = texto.replace(v, "••••")
        return texto


def _error(e: BaseException, mascara: Optional[Callable[[str], str]] = None) -> str:
    t = f"{type(e).__name__}: {e}"[:300]
    return mascara(t) if mascara else t


# ------------------------------------------------------------------ Supabase


class _Respuesta:
    def __init__(self, data: Any) -> None:
        self.data = data


class _ConsultaDeTabla:
    """Una consulta de PostgREST armada como la arma supabase-py, que solo sabe hacer GET."""

    _PERMITIDOS = {"select", "eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "is_", "limit", "order", "execute"}

    def __init__(self, base: "ConsultaSupabase", tabla: str) -> None:
        self._base, self._tabla = base, tabla
        self._params: List[Tuple[str, str]] = []

    def select(self, columnas: str = "*", *_, **__) -> "_ConsultaDeTabla":
        self._params.append(("select", columnas))
        return self

    def _filtro(self, op: str, columna: str, valor: Any) -> "_ConsultaDeTabla":
        self._params.append((columna, f"{op}.{valor}"))
        return self

    def eq(self, c, v):
        return self._filtro("eq", c, v)

    def neq(self, c, v):
        return self._filtro("neq", c, v)

    def gt(self, c, v):
        return self._filtro("gt", c, v)

    def gte(self, c, v):
        return self._filtro("gte", c, v)

    def lt(self, c, v):
        return self._filtro("lt", c, v)

    def lte(self, c, v):
        return self._filtro("lte", c, v)

    def like(self, c, v):
        return self._filtro("like", c, v)

    def ilike(self, c, v):
        return self._filtro("ilike", c, v)

    def is_(self, c, v):
        return self._filtro("is", c, v)

    def limit(self, n: int, *_, **__) -> "_ConsultaDeTabla":
        self._params.append(("limit", str(int(n))))
        return self

    def order(self, columna: str, desc: bool = False, *_, **__) -> "_ConsultaDeTabla":
        self._params.append(("order", f"{columna}.{'desc' if desc else 'asc'}"))
        return self

    def execute(self) -> _Respuesta:
        return self._base._get(self._tabla, self._params)

    def __getattr__(self, nombre: str):
        # insert, update, upsert, delete, rpc…: nada de eso sale de aquí.
        raise PermissionError(f"Supabase en solo lectura: {nombre} no está permitido")


class ConsultaSupabase:
    """El `Client` de supabase que usa el motor, sin poder escribir: GET a /rest/v1."""

    def __init__(self, url: str, llave: str, evento: Optional[Callable[..., None]] = None,
                 mascara: Optional[Callable[[str], str]] = None, http: Any = None) -> None:
        self._url = url.rstrip("/")
        self._llave = llave
        self._evento = evento
        self._mascara = mascara
        self._http = http

    def table(self, tabla: str) -> _ConsultaDeTabla:
        return _ConsultaDeTabla(self, tabla)

    from_ = table

    def _get(self, tabla: str, params: List[Tuple[str, str]]) -> _Respuesta:
        http = self._http
        if http is None:
            import httpx as http  # noqa: N813
        t = time.time()
        try:
            r = http.get(f"{self._url}/rest/v1/{tabla}", params=params, timeout=20,
                         headers={"apikey": self._llave, "Authorization": f"Bearer {self._llave}", "Accept": "application/json"})
            if r.status_code >= 400:
                raise RuntimeError(f"Supabase respondió {r.status_code}")
            datos = r.json()
        except Exception as e:
            if self._evento:
                self._evento("lectura_real", sistema="supabase", operacion=f"select {tabla}", ok=False,
                             ms=int((time.time() - t) * 1000), error=_error(e, self._mascara))
            raise
        if self._evento:
            self._evento("lectura_real", sistema="supabase", operacion=f"select {tabla}", ok=True,
                         ms=int((time.time() - t) * 1000), resultado=f"{len(datos)} fila(s)")
        return _Respuesta(datos)

    def __getattr__(self, nombre: str):
        raise PermissionError(f"Supabase en solo lectura: {nombre} no está permitido")


# --------------------------------------------------------------------- Redis


class RedisSoloLectura:
    """Un cliente de Redis que solo lee. SET, DEL, EXPIRE… lanzan PermissionError."""

    PERMITIDOS = frozenset({"get", "exists", "ttl", "pttl", "ping", "close", "type"})

    def __init__(self, real) -> None:
        self._real = real

    def __getattr__(self, nombre: str):
        if nombre in self.PERMITIDOS:
            return getattr(self._real, nombre)
        raise PermissionError(f"Redis en solo lectura: {nombre} no está permitido")


# ------------------------------------------- registro de radicados (Cloud SQL)

# Lo único que el registro de radicados del motor ejecuta para leer: fijar la zona
# horaria de la sesión y el SELECT de la capa 6. El INSERT no llega aquí en
# MODO_TEST (el motor lo graba en el grabador); si llegara, se rechaza.
_SENTENCIAS_DE_LECTURA = ("select ", "set time zone ")
# Cloud SQL del job solo tiene IP privada: desde el visor casi nunca se alcanza.
# Se prueba una vez por proceso con un tope corto en vez de esperar los 10 s del
# motor en cada correo.
TIEMPO_DE_PRUEBA_S = 3.0
_alcance: Dict[Tuple[str, int], str] = {}


def alcanza(host: str, puerto: int, tope_s: float = TIEMPO_DE_PRUEBA_S) -> str:
    """Cadena vacía si se abre una conexión TCP a host:puerto; si no, el motivo. Se recuerda por proceso."""
    clave = (host, int(puerto))
    if clave not in _alcance:
        import socket

        try:
            with socket.create_connection(clave, timeout=tope_s):
                _alcance[clave] = ""
        except OSError as e:
            _alcance[clave] = (f"{type(e).__name__}: no se alcanza en {tope_s:g} s "
                               "(Cloud SQL del job solo tiene IP privada, dentro de la VPC)")
    return _alcance[clave]


class _ConexionSoloLectura:
    """Una conexión de pg8000.native que solo deja leer, en una sesión de solo lectura."""

    def __init__(self, real, evento: Optional[Callable[..., None]], mascara: Optional[Callable[[str], str]]) -> None:
        self._real, self._evento, self._mascara = real, evento, mascara

    def run(self, sql: str, **parametros: Any):
        if not sql.strip().lower().startswith(_SENTENCIAS_DE_LECTURA):
            raise PermissionError("registro de radicados en solo lectura: solo SELECT")
        es_consulta = sql.strip().lower().startswith("select ")
        t = time.time()
        try:
            filas = self._real.run(sql, **parametros)
        except Exception as e:
            if self._evento and es_consulta:
                self._evento("lectura_real", sistema="registro de radicados (Cloud SQL)", operacion="select iapqrd_radicados_pqrd",
                             ok=False, ms=int((time.time() - t) * 1000), error=_error(e, self._mascara))
            raise
        if self._evento and es_consulta:
            self._evento("lectura_real", sistema="registro de radicados (Cloud SQL)", operacion="select iapqrd_radicados_pqrd",
                         ok=True, ms=int((time.time() - t) * 1000), resultado=f"{len(filas or [])} fila(s)")
        return filas

    def close(self) -> None:
        self._real.close()


def conectar_registro_solo_lectura(evento: Optional[Callable[..., None]] = None,
                                   mascara: Optional[Callable[[str], str]] = None,
                                   conectar: Optional[Callable[..., Any]] = None,
                                   probar: Callable[[str, int], str] = alcanza) -> Callable[..., Any]:
    """
    El `conectar` que recibe `RegistroDeRadicados` del motor, de solo lectura.

    Antes de abrir prueba si el host se alcanza (una vez por proceso): si no, lanza
    ConnectionError al instante y el motor lo trata como la base caída (la capa 6
    queda «degradada» con el motivo, como en el job). La sesión queda en
    `default_transaction_read_only` y la conexión solo deja pasar SELECT.
    """

    def _conectar(**kw: Any):
        motivo = probar(kw.get("host", ""), int(kw.get("port") or 5432))
        if motivo:
            if evento:
                evento("lectura_real", sistema="registro de radicados (Cloud SQL)", operacion="conectar",
                       ok=False, ms=0, error=_error(ConnectionError(motivo), mascara))
            raise ConnectionError(motivo)
        if conectar is None:
            import pg8000.native

            real = pg8000.native.Connection(**kw)
        else:
            real = conectar(**kw)
        real.run("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        return _ConexionSoloLectura(real, evento, mascara)

    return _conectar


# ------------------------------------------------------------------ Postgres


def conectar_postgres_solo_lectura(conf: Dict[str, Any]):
    """Una conexión de psycopg2 donde ninguna transacción puede escribir."""
    import psycopg2

    conn = psycopg2.connect(host=conf["host"], port=conf["port"], dbname=conf["database"], user=conf["user"],
                            password=conf["password"], connect_timeout=10,
                            options="-c default_transaction_read_only=on")
    conn.set_session(readonly=True, autocommit=True)
    return conn
