"""
Los tokens con que los conectores hablan con Google, Microsoft y las APIs con
OAuth2. El servidor guarda (cifrados) y renueva los tokens de las conexiones
OAuth; aquí se usa el que llegó a la corrida y, si ya venció o está por vencer,
se renueva con el refresh_token SOLO para esta corrida (en memoria).

- google_oauth: access_token / refresh_token de la persona que conectó.
- google_servicio: la cuenta de servicio (google-auth, Apache-2.0) con los alcances del nodo.
- microsoft_oauth: tokens delegados de la persona que conectó.
- microsoft_graph: credenciales de cliente de una aplicación de Entra ID.
- http_oauth2 y api_openapi con auth oauth2: credenciales de cliente contra su token_url.

Nunca se usa una conexión de una cuenta de Positiva (bloqueo duro). `ENDPOINTS`
y `fabrica_servicio` se cambian en las pruebas.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from . import rest
from .base import ErrorDeNegocio, direccion_vetada, host_vetado

ENDPOINTS = {
    "google_token": "https://oauth2.googleapis.com/token",
    "microsoft_token": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
}
MARGEN_S = 120  # se renueva si le quedan menos de 2 minutos
_cache: Dict[str, Tuple[str, float]] = {}
_cerrojo = threading.Lock()


def _ahora() -> float:
    return time.time()


def revisar_cuenta(valores: dict) -> str:
    """La cuenta conectada (correo); ErrorDeNegocio si es de Positiva o un buzón protegido."""
    cuenta = str(valores.get("cuenta") or valores.get("buzon") or "").strip()
    motivo = direccion_vetada(cuenta) if cuenta else None
    if motivo:
        raise ErrorDeNegocio(f"bloqueado por seguridad: {motivo}")
    return cuenta


def _expira_s(valores: dict) -> float:
    x = valores.get("expira")
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v / 1000 if v > 1e11 else v  # el servidor la guarda en milisegundos


def _token_de_refresco(tipo: str, valores: dict, timeout: float) -> str:
    """Pide un access_token nuevo con el refresh_token (y lo deja en valores para el resto de la corrida)."""
    refresh = str(valores.get("refresh_token") or "")
    if not refresh:
        raise ErrorDeNegocio("la conexión no está conectada: en Conexiones pulsa «Conectar» y acepta los permisos")
    form = {"client_id": str(valores.get("client_id") or ""), "client_secret": str(valores.get("client_secret") or ""),
            "grant_type": "refresh_token", "refresh_token": refresh}
    if tipo == "google_oauth":
        url = ENDPOINTS["google_token"]
    else:
        tenant = str(valores.get("tenant") or "common").strip()
        if not re.fullmatch(r"[A-Za-z0-9.-]{3,80}", tenant):
            raise ErrorDeNegocio("tenant inválido en la conexión de Microsoft")
        url = ENDPOINTS["microsoft_token"].replace("{tenant}", tenant)
        if valores.get("scopes"):
            form["scope"] = " ".join(sorted(set(str(valores["scopes"]).replace(",", " ").split()) | {"offline_access"}))
    r = rest.pedir("POST", url, data=form, timeout=timeout, servicio="el servidor de autorización", errores=False)
    if r.status >= 400:
        try:
            d = r.json()
        except ValueError:
            d = {}
        if r.status in (400, 401) and str(d.get("error")) in ("invalid_grant", "unauthorized_client", "invalid_client"):
            raise ErrorDeNegocio("la conexión venció o se revocó: vuelve a conectarla en Conexiones («Conectar»)")
        raise ErrorDeNegocio(f"no se pudo renovar el token ({r.status}): {d.get('error_description') or d.get('error') or r.text[:200]}")
    d = r.json()
    token = str(d.get("access_token") or "")
    if not token:
        raise ErrorDeNegocio("el servidor de autorización no entregó un token")
    valores["access_token"] = token
    valores["expira"] = int((_ahora() + float(d.get("expires_in") or 3600)) * 1000)
    if d.get("refresh_token"):
        valores["refresh_token"] = d["refresh_token"]  # Microsoft lo rota; el servidor guarda el suyo al renovar
    return token


def _de_conexion(tipo: str, valores: dict, timeout: float) -> str:
    revisar_cuenta(valores)
    with _cerrojo:
        token = str(valores.get("access_token") or "")
        if token and _expira_s(valores) - _ahora() > MARGEN_S:
            return token
        return _token_de_refresco(tipo, valores, timeout)


def _servicio_real(info: dict, alcances: Iterable[str]) -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    cred = service_account.Credentials.from_service_account_info(info, scopes=list(alcances))
    cred.refresh(Request())
    return str(cred.token)


fabrica_servicio: Callable[[dict, Iterable[str]], str] = _servicio_real


def google(cred: dict, alcances: Iterable[str], timeout: float = 20.0) -> str:
    """El token de Google de una credencial google_oauth o google_servicio."""
    v = cred.get("valores") or {}
    if cred.get("tipo") == "google_oauth":
        return _de_conexion("google_oauth", v, timeout)
    if cred.get("tipo") == "google_servicio":
        crudo = v.get("json")
        try:
            info = json.loads(crudo) if isinstance(crudo, str) else dict(crudo or {})
        except ValueError as e:
            raise ErrorDeNegocio("la credencial google_servicio no tiene un JSON de cuenta de servicio válido") from e
        if info.get("type") != "service_account":
            raise ErrorDeNegocio("la credencial google_servicio tiene que ser el JSON de una cuenta de servicio")
        info["token_uri"] = "https://oauth2.googleapis.com/token"
        clave = f"sa:{info.get('client_email')}:{' '.join(sorted(alcances))}"
        with _cerrojo:
            x = _cache.get(clave)
            if x and x[1] - _ahora() > MARGEN_S:
                return x[0]
        token = fabrica_servicio(info, alcances)
        with _cerrojo:
            _cache[clave] = (token, _ahora() + 3000)
        return token
    raise ErrorDeNegocio(f"para Google la conexión tiene que ser google_oauth o google_servicio (es {cred.get('tipo')})")


def microsoft(cred: dict, timeout: float = 20.0) -> str:
    """El token de Graph de una credencial microsoft_oauth (delegado) o microsoft_graph (aplicación)."""
    v = cred.get("valores") or {}
    if cred.get("tipo") == "microsoft_oauth":
        return _de_conexion("microsoft_oauth", v, timeout)
    if cred.get("tipo") == "microsoft_graph":
        tenant = str(v.get("tenant") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9.-]{3,80}", tenant):
            raise ErrorDeNegocio("la credencial de Microsoft Graph necesita tenant (id o dominio)")
        return cliente(ENDPOINTS["microsoft_token"].replace("{tenant}", tenant), str(v.get("client_id") or ""), str(v.get("client_secret") or ""),
                       "https://graph.microsoft.com/.default", timeout)
    raise ErrorDeNegocio(f"para Microsoft 365 la conexión tiene que ser microsoft_oauth o microsoft_graph (es {cred.get('tipo')})")


def cliente(token_url: str, client_id: str, client_secret: str, scope: str = "", timeout: float = 20.0, red_privada: bool = False) -> str:
    """Un token de credenciales de cliente (client_credentials), guardado en memoria hasta que esté por vencer."""
    u = rest.revisar_url(token_url)
    vetado = host_vetado(u.hostname or "")
    if vetado:
        raise ErrorDeNegocio(f"bloqueado por seguridad: {vetado}")
    clave = f"cc:{token_url}:{client_id}:{scope}"
    with _cerrojo:
        x = _cache.get(clave)
        if x and x[1] - _ahora() > MARGEN_S:
            return x[0]
    form = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret, **({"scope": scope} if scope else {})}
    d = rest.pedir("POST", token_url, data=form, timeout=timeout, red_privada=red_privada, servicio="el servidor de autorización").json()
    token = str(d.get("access_token") or "")
    if not token:
        raise ErrorDeNegocio("el servidor de autorización no entregó un token")
    with _cerrojo:
        _cache[clave] = (token, _ahora() + float(d.get("expires_in") or 3600))
    return token


def olvidar() -> None:
    """Borra los tokens guardados en memoria (pruebas)."""
    with _cerrojo:
        _cache.clear()
