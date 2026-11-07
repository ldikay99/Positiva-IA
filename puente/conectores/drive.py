"""
Google Drive con una cuenta de servicio (google-auth, Apache-2.0): listar,
leer (Docs y Hojas se exportan a texto/CSV; lo demás se baja y lo convierte
quien llama) y subir.

La cuenta de servicio solo ve lo que se le compartió. `fabrica` se puede
reemplazar en las pruebas por un Drive falso con la misma interfaz.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import ErrorDeNegocio, ErrorTransitorio

API = "https://www.googleapis.com/drive/v3"
SUBIDA = "https://www.googleapis.com/upload/drive/v3/files"
ALCANCE = ["https://www.googleapis.com/auth/drive"]
EXPORTAR = {"application/vnd.google-apps.document": "text/plain", "application/vnd.google-apps.spreadsheet": "text/csv",
            "application/vnd.google-apps.presentation": "text/plain"}
ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
MAX_DESCARGA = 30_000_000


class ClienteDrive:
    """El Drive de verdad, por REST con la sesión autorizada de google-auth."""

    def __init__(self, valores: dict, timeout: float) -> None:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        crudo = valores.get("json")
        try:
            info = json.loads(crudo) if isinstance(crudo, str) else dict(crudo or {})
        except ValueError as e:
            raise ErrorDeNegocio("la credencial google_servicio no tiene un JSON de cuenta de servicio válido") from e
        if info.get("type") != "service_account":
            raise ErrorDeNegocio("la credencial google_servicio tiene que ser el JSON de una cuenta de servicio")
        info["token_uri"] = "https://oauth2.googleapis.com/token"  # nunca a otro host, diga lo que diga el JSON
        cred = service_account.Credentials.from_service_account_info(info, scopes=ALCANCE)
        self.s = AuthorizedSession(cred)
        self.timeout = timeout

    def _r(self, metodo: str, url: str, **kw) -> Any:
        r = self.s.request(metodo, url, timeout=self.timeout, allow_redirects=False, **kw)
        if r.status_code in (408, 429) or r.status_code >= 500:
            raise ErrorTransitorio(f"Drive respondió {r.status_code}")
        if r.status_code >= 400:
            try:
                detalle = r.json().get("error", {}).get("message", "")
            except ValueError:
                detalle = r.text[:200]
            raise ErrorDeNegocio(f"Drive respondió {r.status_code}: {detalle}")
        return r

    def listar(self, q: str, maximo: int) -> List[dict]:
        r = self._r("GET", f"{API}/files", params={"q": q, "pageSize": maximo, "orderBy": "modifiedTime desc",
                                                   "fields": "files(id,name,mimeType,modifiedTime,size,parents,webViewLink)",
                                                   "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
        return r.json().get("files") or []

    def metadatos(self, id_: str) -> dict:
        return self._r("GET", f"{API}/files/{id_}", params={"fields": "id,name,mimeType,modifiedTime,size,parents,webViewLink",
                                                            "supportsAllDrives": "true"}).json()

    def exportar(self, id_: str, tipo: str) -> bytes:
        return self._r("GET", f"{API}/files/{id_}/export", params={"mimeType": tipo}).content[:MAX_DESCARGA]

    def bajar(self, id_: str) -> bytes:
        return self._r("GET", f"{API}/files/{id_}", params={"alt": "media", "supportsAllDrives": "true"}).content[:MAX_DESCARGA]

    def subir(self, nombre: str, carpeta: str, datos: bytes, tipo: str) -> dict:
        frontera = uuid.uuid4().hex
        meta = {"name": nombre, **({"parents": [carpeta]} if carpeta else {})}
        cuerpo = (f"--{frontera}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
                  f"--{frontera}\r\nContent-Type: {tipo}\r\n\r\n").encode() + datos + f"\r\n--{frontera}--".encode()
        return self._r("POST", SUBIDA, params={"uploadType": "multipart", "supportsAllDrives": "true",
                                               "fields": "id,name,mimeType,webViewLink"},
                       data=cuerpo, headers={"Content-Type": f"multipart/related; boundary={frontera}"}).json()


fabrica: Callable[[dict, float], Any] = ClienteDrive


def revisar_id(x: Any, que: str) -> str:
    v = str(x or "").strip()
    if not ID.match(v):
        raise ErrorDeNegocio(f"{que} «{v[:80]}» no es un id de Drive (letras, números, - y _)")
    return v


def consulta(carpeta: str, nombre: str, tipo_mime: str) -> str:
    partes = ["trashed = false"]
    if carpeta:
        partes.append(f"'{revisar_id(carpeta, 'carpeta')}' in parents")
    if nombre:
        partes.append("name contains '" + nombre.replace("\\", "\\\\").replace("'", "\\'") + "'")
    if tipo_mime:
        partes.append("mimeType = '" + tipo_mime.replace("'", "") + "'")
    return " and ".join(partes)


def resumen(f: dict) -> dict:
    return {"id": f.get("id"), "nombre": f.get("name"), "tipo": f.get("mimeType"), "modificado": f.get("modifiedTime"),
            "tamano": int(f["size"]) if str(f.get("size") or "").isdigit() else None, "enlace": f.get("webViewLink")}


def contenido(cliente: Any, id_: str) -> Tuple[dict, bytes, str]:
    """(metadatos, bytes, tipo) de un archivo; Docs y Hojas exportados a texto o CSV."""
    meta = cliente.metadatos(revisar_id(id_, "archivo"))
    tipo = meta.get("mimeType") or ""
    if tipo in EXPORTAR:
        return meta, cliente.exportar(id_, EXPORTAR[tipo]), EXPORTAR[tipo]
    if tipo.startswith("application/vnd.google-apps."):
        raise ErrorDeNegocio(f"ese archivo de Google ({tipo}) no se puede leer como texto")
    return meta, cliente.bajar(id_), tipo
