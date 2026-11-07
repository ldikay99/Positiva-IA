"""
Datos (plan 5d, fase 2): MongoDB (pymongo, Apache-2.0), BigQuery y Firestore
(sus API REST con el token de Google que ya da conectores/oauth.py; sin
librerías nuevas).

MongoDB: pymongo se instala SIN dependencias (`pip --no-deps`): su única
dependencia, dnspython, es ISC (no está en la lista permitida) y solo sirve para
las direcciones «mongodb+srv://». Por eso aquí se conecta a host:puerto
(uno o varios separados por coma). Cada host se revisa con `destino_seguro`
(como las bases SQL: IP privadas sí, nunca Positiva, metadatos ni loopback).

Lectura: buscar, contar, distintos y agregar sin $out/$merge; nunca $where,
$function, $accumulator ni JavaScript. Escritura (insertar, actualizar, borrar):
actualizar y borrar exigen filtro (salvo permitir_sin_filtro). BigQuery: la
consulta pasa primero en seco (dryRun) y su statementType decide: SELECT lee;
INSERT/UPDATE/DELETE/MERGE escriben; lo demás (DDL, scripts) no se corre.
"""
from __future__ import annotations

import base64
import datetime
import json
import re
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import rest
from .base import ErrorDeNegocio, ErrorTransitorio, a_json, destino_seguro

# ===================================================================== MongoDB

PROHIBIDOS_MONGO = {"$where", "$function", "$accumulator", "$out", "$merge", "$eval", "$code"}
OPERACIONES_LECTURA = ("buscar", "contar", "distintos", "agregar")
OPERACIONES_ESCRITURA = ("insertar", "actualizar", "borrar")
OID = re.compile(r"^[0-9a-f]{24}$")


def revisar_mongo(x: Any, donde: str = "filtro") -> None:
    """ErrorDeNegocio si el filtro o el pipeline usa algo que ejecuta código o escribe ($where, $function, $out, $merge…)."""
    if isinstance(x, dict):
        for k, v in x.items():
            if str(k) in PROHIBIDOS_MONGO:
                raise ErrorDeNegocio(f"{donde} no admite {k} (ejecuta código o escribe): usa operadores de consulta normales")
            revisar_mongo(v, donde)
    elif isinstance(x, list):
        for v in x:
            revisar_mongo(v, donde)


def hosts_mongo(v: dict) -> List[Tuple[str, int]]:
    puerto = int(v.get("puerto") or 27017)
    r = []
    for parte in re.split(r"[,\s]+", str(v.get("host") or "").strip()):
        if not parte:
            continue
        if parte.startswith("mongodb"):
            raise ErrorDeNegocio("host es el nombre del servidor (o varios host:puerto separados por coma), no una URI mongodb://; "
                                 "mongodb+srv no se admite (pide una librería con licencia que no se permite)")
        h, _, p = parte.rpartition(":") if parte.count(":") == 1 else (parte, "", "")
        r.append(((h or parte).lower(), int(p) if p.isdigit() else puerto))
    if not r:
        raise ErrorDeNegocio("la conexión MongoDB no tiene host")
    return r[:7]


def _cliente_real(v: dict, timeout_s: float) -> Any:
    hosts = hosts_mongo(v)
    for h, p in hosts:
        destino_seguro(h, p, red_privada=True)
    import pymongo  # se carga solo si el destino pasó la revisión
    kw: Dict[str, Any] = {"host": [f"{h}:{p}" for h, p in hosts], "tls": v.get("tls", True) is not False,
                          "serverSelectionTimeoutMS": int(timeout_s * 1000), "connectTimeoutMS": int(timeout_s * 1000),
                          "socketTimeoutMS": int(timeout_s * 1000), "appname": "positiva-ia-flujos", "retryWrites": False}
    if v.get("usuario"):
        kw.update(username=str(v["usuario"]), password=str(v.get("clave") or ""), authSource=str(v.get("auth_base") or "admin"))
    if v.get("replica_set"):
        kw["replicaSet"] = str(v["replica_set"])
    elif len(hosts) == 1:
        kw["directConnection"] = True
    return pymongo.MongoClient(**kw)


cliente_mongo: Callable[[dict, float], Any] = _cliente_real  # las pruebas lo cambian por un doble


def _con_oid(x: Any, clave: str = "") -> Any:
    """«_id» de 24 hexadecimales → ObjectId (si pymongo está), en filtros y documentos."""
    if isinstance(x, dict):
        return {k: _con_oid(v, k) for k, v in x.items()}
    if isinstance(x, list):
        return [_con_oid(v, clave) for v in x]
    if clave == "_id" and isinstance(x, str) and OID.match(x):
        try:
            from bson import ObjectId
            return ObjectId(x)
        except ImportError:
            return x
    return x


def _errores_mongo(e: Exception) -> Exception:
    if isinstance(e, (ErrorDeNegocio, ErrorTransitorio)):
        return e
    nombre = type(e).__name__
    if nombre in ("ServerSelectionTimeoutError", "NetworkTimeout", "AutoReconnect", "ConnectionFailure", "ExecutionTimeout", "NotPrimaryError"):
        return ErrorTransitorio(f"MongoDB: {nombre}: {str(e)[:200]}")
    return ErrorDeNegocio(f"MongoDB rechazó la operación: {nombre}: {str(e)[:300]}")


def mongo_leer(v: dict, coleccion: str, operacion: str, filtro: dict, proyeccion: Optional[dict], orden: Optional[dict],
               pipeline: Optional[list], campo: str, maximo: int, timeout_s: float) -> dict:
    if operacion not in OPERACIONES_LECTURA:
        raise ErrorDeNegocio(f"operación de lectura desconocida «{operacion}»")
    revisar_mongo(filtro, "filtro")
    revisar_mongo(pipeline or [], "pipeline")
    base = str(v.get("base") or "").strip()
    if not base or not coleccion:
        raise ErrorDeNegocio("hacen falta base (en la conexión) y coleccion")
    cli = cliente_mongo(v, timeout_s)
    try:
        col = cli[base][coleccion]
        ms = int(timeout_s * 1000)
        f = _con_oid(filtro or {})
        if operacion == "contar":
            return {"total": int(col.count_documents(f, maxTimeMS=ms))}
        if operacion == "distintos":
            if not campo:
                raise ErrorDeNegocio("distintos necesita campo")
            vals = col.distinct(campo, f, maxTimeMS=ms)
            return {"valores": [a_json(x) for x in vals[:maximo]], "total": min(len(vals), maximo), "truncado": len(vals) > maximo}
        if operacion == "agregar":
            etapas = list(pipeline or [])
            if f:
                etapas.insert(0, {"$match": f})
            etapas.append({"$limit": maximo + 1})
            docs = list(col.aggregate(etapas, maxTimeMS=ms))
        else:
            cur = col.find(f, proyeccion or None, max_time_ms=ms).limit(maximo + 1)
            if orden:
                cur = cur.sort(list(orden.items()))
            docs = list(cur)
        return {"documentos": [a_json(d) for d in docs[:maximo]], "total": min(len(docs), maximo), "truncado": len(docs) > maximo}
    except Exception as e:
        raise _errores_mongo(e) from e
    finally:
        try:
            cli.close()
        except Exception:
            pass


def mongo_escribir(v: dict, coleccion: str, operacion: str, filtro: dict, documentos: List[dict], cambios: dict,
                   varios: bool, timeout_s: float) -> dict:
    if operacion not in OPERACIONES_ESCRITURA:
        raise ErrorDeNegocio(f"operación de escritura desconocida «{operacion}»")
    revisar_mongo(filtro, "filtro")
    revisar_mongo(cambios, "cambios")
    revisar_mongo(documentos, "documentos")
    base = str(v.get("base") or "").strip()
    cli = cliente_mongo(v, timeout_s)
    try:
        col = cli[base][coleccion]
        f = _con_oid(filtro or {})
        if operacion == "insertar":
            docs = [_con_oid(d) for d in documentos]
            if len(docs) == 1:
                r = col.insert_one(docs[0])
                return {"insertados": 1, "ids": [str(r.inserted_id)]}
            r = col.insert_many(docs, ordered=True)
            return {"insertados": len(r.inserted_ids), "ids": [str(x) for x in r.inserted_ids]}
        if operacion == "actualizar":
            r = (col.update_many if varios else col.update_one)(f, cambios)
            return {"coincidentes": int(r.matched_count), "modificados": int(r.modified_count)}
        r = (col.delete_many if varios else col.delete_one)(f)
        return {"borrados": int(r.deleted_count)}
    except Exception as e:
        raise _errores_mongo(e) from e
    finally:
        try:
            cli.close()
        except Exception:
            pass


def mongo_probar(v: dict, timeout_s: float = 15) -> str:
    cli = cliente_mongo(v, timeout_s)
    try:
        cli.admin.command("ping")
        return f"MongoDB respondió (base {v.get('base') or '—'})"
    except Exception as e:
        raise _errores_mongo(e) from e
    finally:
        try:
            cli.close()
        except Exception:
            pass


# ===================================================================== BigQuery

BASES = {"bigquery": "https://bigquery.googleapis.com/bigquery/v2", "firestore": "https://firestore.googleapis.com/v1"}
ALCANCE_BIGQUERY = "https://www.googleapis.com/auth/bigquery"
ALCANCE_FIRESTORE = "https://www.googleapis.com/auth/datastore"
LECTURA_BQ = {"SELECT"}
ESCRITURA_BQ = {"INSERT", "UPDATE", "DELETE", "MERGE"}
PROYECTO = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$|^[a-z0-9.:-]{6,90}$")


def _param_bq(nombre: str, valor: Any) -> dict:
    if isinstance(valor, bool):
        t, v = "BOOL", "true" if valor else "false"
    elif isinstance(valor, int):
        t, v = "INT64", str(valor)
    elif isinstance(valor, float):
        t, v = "FLOAT64", repr(valor)
    else:
        t, v = "STRING", "" if valor is None else str(valor)
    return {"name": nombre, "parameterType": {"type": t}, "parameterValue": {"value": v}}


def _valor_bq(campo: dict, v: Any) -> Any:
    if v is None:
        return None
    if campo.get("mode") == "REPEATED":
        return [_valor_bq({**campo, "mode": "NULLABLE"}, x.get("v") if isinstance(x, dict) else x) for x in v or []]
    t = campo.get("type")
    if t in ("RECORD", "STRUCT"):
        return {f["name"]: _valor_bq(f, c.get("v")) for f, c in zip(campo.get("fields") or [], (v or {}).get("f") or [])}
    if t in ("INTEGER", "INT64"):
        return int(v)
    if t in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
        n = float(v)
        return int(n) if t in ("NUMERIC", "BIGNUMERIC") and n.is_integer() else n
    if t in ("BOOLEAN", "BOOL"):
        return str(v).lower() == "true"
    if t == "TIMESTAMP":
        try:
            return datetime.datetime.fromtimestamp(float(v), datetime.timezone.utc).isoformat()
        except ValueError:
            return v
    return v


def filas_bq(r: dict) -> Tuple[List[str], List[dict]]:
    campos = ((r.get("schema") or {}).get("fields")) or []
    columnas = [f["name"] for f in campos]
    filas = [{f["name"]: _valor_bq(f, c.get("v")) for f, c in zip(campos, fila.get("f") or [])} for fila in r.get("rows") or []]
    return columnas, filas


class BigQuery:
    def __init__(self, token: Callable[[], str], proyecto: str, timeout: float, ubicacion: str = "") -> None:
        if not PROYECTO.match(proyecto or ""):
            raise ErrorDeNegocio("BigQuery necesita proyecto (el id del proyecto de Google Cloud)")
        self.token, self.proyecto, self.timeout, self.ubicacion = token, proyecto, timeout, ubicacion

    def _consulta(self, sql: str, params: Dict[str, Any], seco: bool, maximo: int) -> dict:
        cuerpo: Dict[str, Any] = {"query": sql, "useLegacySql": False, "dryRun": seco, "timeoutMs": int(self.timeout * 1000),
                                  "maxResults": maximo + 1, "parameterMode": "NAMED",
                                  "queryParameters": [_param_bq(k, v) for k, v in params.items()]}
        if self.ubicacion:
            cuerpo["location"] = self.ubicacion
        return rest.pedir("POST", f"{BASES['bigquery']}/projects/{urllib.parse.quote(self.proyecto)}/queries", token=self.token(),
                          json=cuerpo, timeout=self.timeout + 5, servicio="BigQuery").json()

    def tipo_de(self, sql: str, params: Dict[str, Any]) -> Tuple[str, int]:
        """La sentencia en seco (jobs.insert con dryRun: no corre ni cobra) → (statementType, bytes que procesaría)."""
        q: Dict[str, Any] = {"query": sql, "useLegacySql": False, "parameterMode": "NAMED", "queryParameters": [_param_bq(k, v) for k, v in params.items()]}
        cuerpo: Dict[str, Any] = {"configuration": {"dryRun": True, "query": q}}
        if self.ubicacion:
            cuerpo["jobReference"] = {"projectId": self.proyecto, "location": self.ubicacion}
        r = rest.pedir("POST", f"{BASES['bigquery']}/projects/{urllib.parse.quote(self.proyecto)}/jobs", token=self.token(), json=cuerpo,
                       timeout=self.timeout + 5, servicio="BigQuery").json()
        est = r.get("statistics") or {}
        tipo = str((est.get("query") or {}).get("statementType") or "").upper()
        return tipo, int(est.get("totalBytesProcessed") or (est.get("query") or {}).get("totalBytesProcessed") or 0)

    def leer(self, sql: str, params: Dict[str, Any], maximo: int) -> dict:
        tipo, bytes_ = self.tipo_de(sql, params)
        if tipo not in LECTURA_BQ:
            raise ErrorDeNegocio(f"bigquery_consulta solo lee (SELECT); esta sentencia es {tipo or 'desconocida'}: para escribir usa bigquery_ejecutar")
        r = self._consulta(sql, params, False, maximo)
        if not r.get("jobComplete", True):
            raise ErrorTransitorio("BigQuery no terminó la consulta en el tiempo de espera")
        columnas, filas = filas_bq(r)
        return {"columnas": columnas, "filas": filas[:maximo], "total": min(len(filas), maximo),
                "truncado": len(filas) > maximo or int(r.get("totalRows") or 0) > maximo, "bytes_procesados": bytes_}

    def revisar_escritura(self, sql: str, params: Dict[str, Any]) -> str:
        tipo, _ = self.tipo_de(sql, params)
        if tipo not in ESCRITURA_BQ:
            raise ErrorDeNegocio(f"bigquery_ejecutar solo corre INSERT, UPDATE, DELETE o MERGE; esta sentencia es {tipo or 'desconocida'} (DDL y scripts nunca)")
        return tipo

    def escribir(self, sql: str, params: Dict[str, Any]) -> dict:
        r = self._consulta(sql, params, False, 1)
        return {"afectadas": int(r.get("numDmlAffectedRows") or 0), "trabajo": ((r.get("jobReference") or {}).get("jobId") or "")}


# ===================================================================== Firestore

OPERADORES_FS = {"==": "EQUAL", "!=": "NOT_EQUAL", "<": "LESS_THAN", "<=": "LESS_THAN_OR_EQUAL", ">": "GREATER_THAN",
                 ">=": "GREATER_THAN_OR_EQUAL", "array-contains": "ARRAY_CONTAINS", "in": "IN", "not-in": "NOT_IN",
                 "array-contains-any": "ARRAY_CONTAINS_ANY"}
SEGMENTO_FS = re.compile(r"^[^/\x00]{1,1500}$")


def a_firestore(v: Any) -> dict:
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {str(k): a_firestore(x) for k, x in v.items()}}}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [a_firestore(x) for x in v]}}
    return {"stringValue": str(v)}


def de_firestore(v: dict) -> Any:
    if not isinstance(v, dict):
        return v
    for k, x in v.items():
        if k == "nullValue":
            return None
        if k in ("booleanValue", "stringValue", "doubleValue", "timestampValue", "referenceValue"):
            return x
        if k == "integerValue":
            return int(x)
        if k == "bytesValue":
            return "base64:" + str(x)
        if k == "geoPointValue":
            return {"lat": x.get("latitude"), "lng": x.get("longitude")}
        if k == "mapValue":
            return {a: de_firestore(b) for a, b in (x.get("fields") or {}).items()}
        if k == "arrayValue":
            return [de_firestore(b) for b in x.get("values") or []]
    return None


def documento_fs(d: dict) -> dict:
    nombre = str(d.get("name") or "")
    return {"id": nombre.rsplit("/", 1)[-1], "ruta": nombre.split("/documents/", 1)[-1],
            **{k: de_firestore(x) for k, x in (d.get("fields") or {}).items()},
            "_creado": d.get("createTime", ""), "_actualizado": d.get("updateTime", "")}


def _segmentos(ruta: str) -> List[str]:
    partes = [p for p in str(ruta or "").strip().strip("/").split("/") if p]
    if not partes or any(p in (".", "..") or not SEGMENTO_FS.match(p) for p in partes):
        raise ErrorDeNegocio(f"«{str(ruta)[:120]}» no es una ruta de Firestore válida (colección/documento/…)")
    return partes


class Firestore:
    def __init__(self, token: Callable[[], str], proyecto: str, base: str, timeout: float) -> None:
        if not PROYECTO.match(proyecto or ""):
            raise ErrorDeNegocio("Firestore necesita proyecto (el id del proyecto de Google Cloud)")
        self.token, self.timeout = token, timeout
        self.raiz = f"projects/{proyecto}/databases/{base or '(default)'}/documents"

    def _url(self, partes: List[str], extra: str = "") -> str:
        return f"{BASES['firestore']}/{self.raiz}" + "".join("/" + urllib.parse.quote(p, safe="") for p in partes) + extra

    def obtener(self, coleccion: str, doc: str) -> Optional[dict]:
        partes = _segmentos(coleccion) + _segmentos(doc)
        if len(partes) % 2:
            raise ErrorDeNegocio("la ruta de un documento tiene número par de partes (colección/documento)")
        r = rest.pedir("GET", self._url(partes), token=self.token(), timeout=self.timeout, servicio="Firestore", errores=False)
        if r.status == 404:
            return None
        if r.status >= 400:
            _error_fs(r)
        return documento_fs(r.json())

    def listar(self, coleccion: str, maximo: int, orden: str = "") -> List[dict]:
        partes = _segmentos(coleccion)
        if len(partes) % 2 == 0:
            raise ErrorDeNegocio("coleccion tiene que ser una colección (número impar de partes: «clientes» o «clientes/ana/pedidos»)")
        params = {"pageSize": str(maximo)}
        if orden:
            params["orderBy"] = orden
        r = rest.pedir("GET", self._url(partes), params=params, token=self.token(), timeout=self.timeout, servicio="Firestore", errores=False)
        if r.status >= 400:
            _error_fs(r)
        return [documento_fs(d) for d in r.json().get("documents") or []][:maximo]

    def consultar(self, coleccion: str, filtros: List[dict], orden: List[dict], maximo: int) -> List[dict]:
        partes = _segmentos(coleccion)
        if len(partes) % 2 == 0:
            raise ErrorDeNegocio("coleccion tiene que ser una colección")
        padre, nombre = partes[:-1], partes[-1]
        conds = []
        for f in filtros:
            op = OPERADORES_FS.get(str(f.get("operador") or "=="))
            if not op:
                raise ErrorDeNegocio(f"operador «{f.get('operador')}» no válido (válidos: {', '.join(OPERADORES_FS)})")
            conds.append({"fieldFilter": {"field": {"fieldPath": str(f.get("campo"))}, "op": op, "value": a_firestore(f.get("valor"))}})
        q: Dict[str, Any] = {"from": [{"collectionId": nombre}], "limit": maximo}
        if conds:
            q["where"] = conds[0] if len(conds) == 1 else {"compositeFilter": {"op": "AND", "filters": conds}}
        if orden:
            q["orderBy"] = [{"field": {"fieldPath": str(o.get("campo"))}, "direction": "DESCENDING" if o.get("descendente") else "ASCENDING"} for o in orden]
        url = (f"{BASES['firestore']}/{self.raiz}" + "".join("/" + urllib.parse.quote(p, safe="") for p in padre) + ":runQuery")
        r = rest.pedir("POST", url, json={"structuredQuery": q}, token=self.token(), timeout=self.timeout, servicio="Firestore", errores=False)
        if r.status >= 400:
            _error_fs(r)
        return [documento_fs(x["document"]) for x in r.json() if isinstance(x, dict) and x.get("document")][:maximo]

    def guardar(self, coleccion: str, doc: str, datos: dict, combinar: bool) -> dict:
        partes = _segmentos(coleccion)
        cuerpo = {"fields": {str(k): a_firestore(v) for k, v in datos.items()}}
        if doc:
            partes += _segmentos(doc)
            if len(partes) % 2:
                raise ErrorDeNegocio("la ruta de un documento tiene número par de partes (colección/documento)")
            params = [("updateMask.fieldPaths", k) for k in datos] if combinar else None
            url = self._url(partes) + ("?" + urllib.parse.urlencode(params) if params else "")
            r = rest.pedir("PATCH", url, json=cuerpo, token=self.token(), timeout=self.timeout, servicio="Firestore", errores=False)
        else:
            r = rest.pedir("POST", self._url(partes), json=cuerpo, token=self.token(), timeout=self.timeout, servicio="Firestore", errores=False)
        if r.status >= 400:
            _error_fs(r)
        d = documento_fs(r.json())
        return {"id": d["id"], "ruta": d["ruta"]}


def _error_fs(r: rest.Respuesta) -> None:
    try:
        m = (r.json().get("error") or {}).get("message") if isinstance(r.json(), dict) else ""
    except ValueError:
        m = r.text[:200]
    if r.status in (408, 429) or r.status >= 500:
        raise ErrorTransitorio(f"Firestore respondió {r.status}")
    raise ErrorDeNegocio(f"Firestore respondió {r.status}: {str(m)[:300]}")


def b64(x: bytes) -> str:
    return base64.b64encode(x).decode()


def json_compacto(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:2000]
