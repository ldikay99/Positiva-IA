"""
Google Sheets con una cuenta de servicio (google-auth, Apache-2.0): leer un
rango como filas y escribir (anexar, actualizar por clave o reemplazar), por la
API REST v4. La hoja tiene que estar compartida con la cuenta de servicio.

`fabrica` se reemplaza en las pruebas por unas Sheets falsas con la misma
interfaz (valores, anexar, actualizar, limpiar).
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import ErrorDeNegocio, ErrorTransitorio, a_json

API = "https://sheets.googleapis.com/v4/spreadsheets"
ALCANCE = ["https://www.googleapis.com/auth/spreadsheets"]
ID = re.compile(r"^[A-Za-z0-9_-]{20,200}$")


class ClienteSheets:
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
        info["token_uri"] = "https://oauth2.googleapis.com/token"
        self.s = AuthorizedSession(service_account.Credentials.from_service_account_info(info, scopes=ALCANCE))
        self.timeout = timeout

    def _r(self, metodo: str, url: str, **kw) -> Any:
        r = self.s.request(metodo, url, timeout=self.timeout, allow_redirects=False, **kw)
        if r.status_code in (408, 429) or r.status_code >= 500:
            raise ErrorTransitorio(f"Sheets respondió {r.status_code}")
        if r.status_code >= 400:
            try:
                detalle = r.json().get("error", {}).get("message", "")
            except ValueError:
                detalle = r.text[:200]
            raise ErrorDeNegocio(f"Sheets respondió {r.status_code}: {detalle}")
        return r.json() if r.content else {}

    def _url(self, hoja: str, rango: str, sufijo: str = "") -> str:
        return f"{API}/{hoja}/values/{urllib.parse.quote(rango, safe='!:$')}{sufijo}"

    def valores(self, hoja: str, rango: str) -> List[List[Any]]:
        return self._r("GET", self._url(hoja, rango), params={"valueRenderOption": "UNFORMATTED_VALUE"}).get("values") or []

    def anexar(self, hoja: str, rango: str, filas: List[List[Any]]) -> dict:
        return self._r("POST", self._url(hoja, rango, ":append"), params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                       json={"values": filas})

    def actualizar(self, hoja: str, rango: str, filas: List[List[Any]]) -> dict:
        return self._r("PUT", self._url(hoja, rango), params={"valueInputOption": "USER_ENTERED"}, json={"values": filas})

    def limpiar(self, hoja: str, rango: str) -> dict:
        return self._r("POST", self._url(hoja, rango, ":clear"), json={})


fabrica: Callable[[dict, float], Any] = ClienteSheets
BASE_TOKEN = API  # las pruebas lo cambian por un servidor local


class ClienteSheetsToken(ClienteSheets):
    """Las mismas Sheets con un token (una conexión google_oauth), por el cliente REST seguro de los conectores."""

    def __init__(self, token_de: Callable[[List[str]], str], timeout: float) -> None:  # noqa: D107
        self.token_de, self.timeout = token_de, timeout

    def _r(self, metodo: str, url: str, **kw) -> Any:
        from . import rest

        r = rest.pedir(metodo, url.replace(API, BASE_TOKEN), token=self.token_de(ALCANCE), timeout=self.timeout, servicio="Sheets", errores=False,
                       params=kw.get("params"), json=kw.get("json"))
        if r.status_code in (408, 429) or r.status_code >= 500:
            raise ErrorTransitorio(f"Sheets respondió {r.status_code}")
        if r.status_code >= 400:
            try:
                detalle = r.json().get("error", {}).get("message", "")
            except (ValueError, AttributeError):
                detalle = r.text[:200]
            raise ErrorDeNegocio(f"Sheets respondió {r.status_code}: {detalle}")
        return r.json() if r.content else {}


def cliente(cred: dict, timeout: float) -> Any:
    """El cliente de Sheets de una credencial: google_servicio (fabrica) o google_oauth."""
    if cred.get("tipo") == "google_oauth":
        from . import oauth

        return ClienteSheetsToken(lambda alcances: oauth.google(cred, alcances, timeout), timeout)
    return fabrica(cred.get("valores") or {}, timeout)


def revisar_id(x: Any) -> str:
    v = str(x or "").strip()
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", v)  # también se acepta el enlace de la hoja
    if m:
        v = m.group(1)
    if not ID.match(v):
        raise ErrorDeNegocio(f"«{v[:80]}» no es el id de una hoja de Google (o su enlace)")
    return v


def a_filas(valores: List[List[Any]], con_encabezados: bool = True, max_filas: int = 5000) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not valores:
        return [], []
    if con_encabezados:
        columnas = [str(x).strip() or f"columna_{i + 1}" for i, x in enumerate(valores[0])]
        cuerpo = valores[1:]
    else:
        columnas = [f"columna_{i + 1}" for i in range(max(len(f) for f in valores))]
        cuerpo = valores
    return [{c: a_json(f[i]) if i < len(f) else None for i, c in enumerate(columnas)} for f in cuerpo[:max_filas]], columnas


def _celda(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def escribir(cliente: Any, hoja: str, pestana: str, filas: List[Dict[str, Any]], modo: str, clave: Optional[str] = None,
             insertar_si_falta: bool = True) -> Dict[str, Any]:
    """Escribe filas (objetos) en una pestaña. anexar: al final, con encabezados si está vacía; actualizar: por la columna
    clave (las que no están se agregan si insertar_si_falta); reemplazar: borra y escribe todo."""
    rango = pestana or "A:ZZ"
    actuales = cliente.valores(hoja, rango) if modo != "reemplazar" else []
    columnas = [str(x) for x in actuales[0]] if actuales else []
    for f in filas:
        for k in f:
            if k not in columnas:
                columnas.append(str(k))
    def fila_de(f: Dict[str, Any]) -> List[Any]:
        return [_celda(f.get(c)) for c in columnas]
    if modo == "reemplazar":
        cliente.limpiar(hoja, rango)
        cliente.actualizar(hoja, f"{pestana}!A1" if pestana else "A1", [columnas] + [fila_de(f) for f in filas])
        return {"agregadas": len(filas), "actualizadas": 0, "columnas": columnas}
    if modo == "anexar" or not clave:
        if not actuales:
            cliente.actualizar(hoja, f"{pestana}!A1" if pestana else "A1", [columnas] + [fila_de(f) for f in filas])
        else:
            if len(columnas) > len(actuales[0]):  # columnas nuevas: se agregan al encabezado
                cliente.actualizar(hoja, f"{pestana}!A1" if pestana else "A1", [columnas])
            cliente.anexar(hoja, rango, [fila_de(f) for f in filas])
        return {"agregadas": len(filas), "actualizadas": 0, "columnas": columnas}
    # actualizar por clave: se reescribe la pestaña con los cambios
    if clave not in columnas:
        raise ErrorDeNegocio(f"la columna clave «{clave}» no está en la hoja (hay: {', '.join(columnas)})")
    existentes, _ = a_filas(actuales)
    indice = {str(f.get(clave)): i for i, f in enumerate(existentes)}
    actualizadas = agregadas = 0
    for f in filas:
        k = str(f.get(clave))
        if k in indice:
            existentes[indice[k]] = {**existentes[indice[k]], **f}
            actualizadas += 1
        elif insertar_si_falta:
            existentes.append(dict(f))
            indice[k] = len(existentes) - 1
            agregadas += 1
    cliente.actualizar(hoja, f"{pestana}!A1" if pestana else "A1", [columnas] + [fila_de(f) for f in existentes])
    return {"agregadas": agregadas, "actualizadas": actualizadas, "columnas": columnas}
