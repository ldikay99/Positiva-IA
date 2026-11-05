"""
Donde se escriben los informes: disco local o un bucket de Cloud Storage.

El sistema de archivos del Job es efimero y muere con la corrida. El informe de
cada ejecucion tambien sale por la salida estandar y ahi lo recoge Cloud
Logging, pero la serie historica —una fila por corrida— solo sirve si se
acumula, y para eso hace falta que sobreviva.

`RUTA_LOGS` acepta las dos formas:

    /app/logs                          disco
    gs://mi-bucket/radicacion-pqrd     bucket

Cloud Storage no sabe anexar, y reescribir el objeto no es una opcion: GCS
cuenta reemplazar como borrar y crear, asi que exige `storage.objects.delete`
sobre el bucket. En un bucket con documentos de casos, ese permiso no se
concede solo para guardar un informe.

Por eso en el bucket cada `anexar` escribe su propia PARTE, con un nombre que
ordena por fecha, y `leer` las une. Solo hace falta crear objetos, nunca
reemplazarlos ni borrarlos. En disco se anexa de verdad, que es mas simple y no
tiene el problema.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

_GCS = re.compile(r"^gs://([^/]+)/?(.*)$", re.IGNORECASE)


def es_bucket(ruta) -> bool:
    return bool(_GCS.match(str(ruta)))


def unir(base, *partes: str) -> str:
    """
    Une una ruta con sus segmentos.

    No se usa `Path` para los buckets: en Windows convierte `gs://b/x` en
    `gs:\\b\\x` y la direccion deja de existir.
    """
    if es_bucket(base):
        limpia = str(base).rstrip("/")
        return "/".join([limpia, *[p.strip("/") for p in partes if p]])
    return str(Path(base).joinpath(*partes))


def _partes(ruta: str) -> Tuple[str, str]:
    """Bucket y objeto de una direccion `gs://`."""
    m = _GCS.match(str(ruta))
    if not m:
        raise ValueError(f"no es una direccion de bucket: {ruta}")
    return m.group(1), m.group(2)


def _blob(ruta: str):
    """
    El objeto de Cloud Storage que nombra la direccion.

    La direccion se valida antes de importar el cliente para que una
    `RUTA_LOGS` mal escrita diga que le falta el nombre del objeto en vez de
    que falta la libreria.
    """
    bucket, objeto = _partes(ruta)
    if not objeto:
        raise ValueError(f"falta el nombre del objeto en {ruta}")

    from google.cloud import storage

    return storage.Client().bucket(bucket).blob(objeto)


def escribir(destino, contenido: str) -> str:
    """Escribe el contenido, reemplazando lo que hubiera. Devuelve la ruta."""
    destino = str(destino)
    if es_bucket(destino):
        _blob(destino).upload_from_string(contenido, content_type="text/plain")
        return destino
    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido, encoding="utf-8")
    return destino


def leer(destino) -> Optional[str]:
    """
    El contenido, o None si no existe.

    En el bucket se une lo que haya de las DOS formas, en orden: primero el
    objeto de una pieza, que es lo que escribieron las corridas de antes, y
    detras sus partes. Quedarse solo con el objeto dejaria las partes nuevas
    fuera para siempre en cuanto exista uno; quedarse solo con las partes
    perderia el historico ya escrito.
    """
    destino = str(destino)
    if es_bucket(destino):
        blob = _blob(destino)
        de_una_pieza = blob.download_as_text() if blob.exists() else None
        en_partes = _leer_las_partes(destino)
        if de_una_pieza is None:
            return en_partes
        return de_una_pieza + (en_partes or "")
    ruta = Path(destino)
    return ruta.read_text(encoding="utf-8") if ruta.exists() else None


# Sufijo del prefijo donde viven las partes de un destino que se anexa.
SUFIJO_DE_PARTES = ".partes"


def _nombre_de_parte() -> str:
    """
    Nombre que ordena por fecha y no colisiona entre corridas simultaneas.

    La fecha delante para que listar el prefijo devuelva las partes en el orden
    en que se escribieron; el sufijo aleatorio porque dos corridas pueden
    anexar en el mismo segundo.
    """
    momento = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"{momento}-{uuid.uuid4().hex[:8]}.txt"


def anexar(destino, contenido: str) -> str:
    """
    Añade al final, creando el destino si no existia.

    En el bucket no se reescribe el objeto: se crea una parte nueva. Reemplazar
    un objeto en GCS exige permiso de borrado, que este bucket no concede.
    """
    destino = str(destino)
    if es_bucket(destino):
        parte = f"{destino}{SUFIJO_DE_PARTES}/{_nombre_de_parte()}"
        _blob(parte).upload_from_string(contenido, content_type="text/plain")
        return parte
    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("a", encoding="utf-8") as f:
        f.write(contenido)
    return destino


def _leer_las_partes(destino: str) -> Optional[str]:
    """
    Une las partes de un destino anexado, en el orden en que se escribieron.

    Devuelve None si no hay ninguna, para que el llamador distinga "todavia no
    se ha escrito nada" de "esta vacio".
    """
    bucket, objeto = _partes(destino)
    if not objeto:
        raise ValueError(f"falta el nombre del objeto en {destino}")

    from google.cloud import storage

    prefijo = f"{objeto}{SUFIJO_DE_PARTES}/"
    cliente = storage.Client()
    trozos: List[str] = []
    for blob in sorted(cliente.list_blobs(bucket, prefix=prefijo),
                       key=lambda b: b.name):
        trozos.append(blob.download_as_text())
    return "".join(trozos) if trozos else None
