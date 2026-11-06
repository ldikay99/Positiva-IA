"""
Documentos nuevos que trae un correo de un hilo ya radicado.

Un correo del mismo hilo de un radicado se archivaba como duplicado aunque
trajera documentos que el radicado no tiene: la historia clinica que faltaba,
la incapacidad siguiente, el soporte que se le pidio a la persona. Nadie los
veia. En produccion, del 5 al 6 de noviembre, fueron 13 de 161 duplicados.

Un correo asi no es un duplicado, pero tampoco se puede radicar: abriria un
segundo numero para el mismo caso, y desde el correo no se distingue un anexo
de una solicitud nueva de la misma persona en el mismo hilo (la incapacidad del
mes siguiente, otra autorizacion). Va a revision manual con el numero del
radicado y la lista de documentos nuevos.

No cuentan como documentos nuevos las cartas de la entidad que vuelven adjuntas
(ENT.../SAL...), los correos adjuntos dentro del correo ni las imagenes de la
firma.

Un documento se da por conocido si coincide el nombre normalizado o el tamaño
exacto en bytes. El nombre cambia al reenviar ("cedula (1).pdf"), y el tamaño
exacto de un PDF o una imagen no se repite entre documentos distintos.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

# Por debajo de este tamaño, una imagen es una firma, un logo o un icono que el
# cliente de correo adjunto sin marcarla como inline.
TAMANO_MINIMO_DE_IMAGEN = 30_000

_IMAGEN = re.compile(r"\.(png|jpe?g|gif|bmp)$", re.IGNORECASE)
_COPIA = re.compile(r"\s*(\(\d+\)|\[\d+\])")
# Las cartas que la entidad le envio a la persona vuelven adjuntas cuando ella
# responde: llevan el numero de radicado o de salida como nombre.
_DOCUMENTO_DE_LA_ENTIDAD = re.compile(r"^\d*-?\s*(ENT|SAL)\d{10,}", re.IGNORECASE)
_IMAGEN_DE_FIRMA = re.compile(
    r"^(image\d*|outlook-\w+|logo\w*|firma\w*)\.(png|jpe?g|gif)$", re.IGNORECASE)


def nombre_normalizado(nombre: str) -> str:
    """El nombre sin marcas de copia, mayusculas ni espacios de mas."""
    limpio = _COPIA.sub("", (nombre or "").strip().lower())
    return " ".join(limpio.split())


def es_documento(adjunto: Dict[str, Any]) -> bool:
    """Si el adjunto es un documento del caso y no una imagen de la firma."""
    if adjunto.get("isInline"):
        return False
    # Un correo adjunto dentro de otro es el hilo, no un documento del caso.
    tipo = adjunto.get("@odata.type") or "#microsoft.graph.fileAttachment"
    if tipo != "#microsoft.graph.fileAttachment":
        return False
    nombre = adjunto.get("name") or ""
    if _DOCUMENTO_DE_LA_ENTIDAD.match(nombre.strip()):
        return False
    if _IMAGEN_DE_FIRMA.match(nombre.strip()):
        return False
    if _IMAGEN.search(nombre) and int(adjunto.get("size") or 0) < TAMANO_MINIMO_DE_IMAGEN:
        return False
    return True


def documentos_nuevos(
    propios: Iterable[Dict[str, Any]], conocidos: Iterable[Dict[str, Any]],
) -> List[str]:
    """Los nombres de los documentos de `propios` que no estan en `conocidos`."""
    conocidos = [a for a in conocidos if es_documento(a)]
    nombres = {nombre_normalizado(a.get("name") or "") for a in conocidos}
    tamanos = {int(a.get("size") or 0) for a in conocidos} - {0}
    nuevos = []
    for adjunto in propios:
        if not es_documento(adjunto):
            continue
        if nombre_normalizado(adjunto.get("name") or "") in nombres:
            continue
        if int(adjunto.get("size") or 0) in tamanos:
            continue
        nuevos.append(adjunto.get("name") or "")
    return nuevos
