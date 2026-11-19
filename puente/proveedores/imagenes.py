"""
Imágenes generadas por IA, igual para todos los proveedores que las tienen:

- vertex: Gemini con salida de imagen en Vertex AI (identidad del servicio, ADC; sin llave).
  Por defecto `gemini-2.5-flash-image`, el que responde en el proyecto (los modelos Imagen
  del editor de Google dan 404 aquí y Google los marcó como en retiro). Un modelo que empieza
  por «imagen-» va por generate_images en la región de GCP_LOCATION_IMAGEN (us-central1).
- gemini: lo mismo con una API key de Google AI Studio (credencial ia_gemini).
- openai: /v1/images/generations (gpt-image-1, dall-e-3) con su llave (credencial ia_openai).

Devuelve PNG siempre (si el servicio entrega JPEG o WebP, se pasa a PNG con Pillow).
Los mismos límites, reintentos y máscara de llaves que el texto (base.py).
"""
from __future__ import annotations

import base64
import io
import os
import time
from typing import Any, Dict, List, Optional

from proveedores.base import (ErrorDeNegocio, ErrorDeProveedor, ErrorTransitorio, balde, con_reintentos, enmascarar, huella, pedir_json,
                              secretos_de, semaforo)

PROVEEDORES_IMAGEN = ("vertex", "gemini", "openai")
MODELO_DEFECTO = {"vertex": "gemini-2.5-flash-image", "gemini": "gemini-2.5-flash-image", "openai": "gpt-image-1"}
ASPECTOS = ("1:1", "3:4", "4:3", "9:16", "16:9")
# Tamaños de OpenAI por aspecto (gpt-image-1 acepta 1024x1024, 1024x1536 y 1536x1024).
TAMANOS_OPENAI = {"1:1": "1024x1024", "3:4": "1024x1536", "9:16": "1024x1536", "4:3": "1536x1024", "16:9": "1536x1024"}
MAX_IMAGENES = 4
UBICACION_IMAGEN = os.environ.get("GCP_LOCATION_IMAGEN", "us-central1")

_clientes: Dict[tuple, Any] = {}


def a_png(datos: bytes) -> bytes:
    """Cualquier imagen como PNG (lo que ya es PNG queda igual)."""
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return datos
    from PIL import Image  # Pillow (HPND)

    s = io.BytesIO()
    Image.open(io.BytesIO(datos)).save(s, format="PNG")
    return s.getvalue()


def _cliente_google(proveedor: str, cred: dict, flujo: str, ubicacion: str):
    from google import genai

    from proyecto_gcp import proyecto_gcp

    k = (proveedor, huella(cred), flujo or "-", ubicacion)
    if k not in _clientes:
        if proveedor == "vertex":
            import google.auth

            proyecto = proyecto_gcp()  # antes del ADC: si falta, que lo diga
            adc, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            _clientes[k] = genai.Client(vertexai=True, project=proyecto, location=ubicacion, credentials=adc)
        else:
            from google.genai import types

            # v1beta, como antes de google-genai 3 (que usa v1, sin los modelos -preview de imagen).
            _clientes[k] = genai.Client(api_key=str(cred.get("api_key") or ""),
                                        http_options=types.HttpOptions(api_version="v1beta"))
    return _clientes[k]


def _google(proveedor: str, modelo: str, prompt: str, aspecto: str, cred: dict, flujo: str, timeout: float) -> bytes:
    from google.genai import types

    from proveedores import familia_gemini as fg

    secretos = secretos_de(cred)
    try:
        if modelo.startswith("imagen"):
            c = _cliente_google(proveedor, cred, flujo, UBICACION_IMAGEN if proveedor == "vertex" else "")
            r = c.models.generate_images(model=modelo, prompt=prompt, config=types.GenerateImagesConfig(
                number_of_images=1, aspect_ratio=aspecto, http_options=types.HttpOptions(timeout=int(timeout * 1000))))
            imgs = [g.image.image_bytes for g in (r.generated_images or []) if g.image and g.image.image_bytes]
        else:
            c = _cliente_google(proveedor, cred, flujo, fg.UBICACION)
            r = c.models.generate_content(model=modelo, contents=prompt, config=types.GenerateContentConfig(
                response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio=aspecto),
                http_options=types.HttpOptions(timeout=int(timeout * 1000))))
            cand = (r.candidates or [None])[0]
            partes = getattr(getattr(cand, "content", None), "parts", None) or []
            imgs = [p.inline_data.data for p in partes if getattr(p, "inline_data", None) and p.inline_data.data]
            if not imgs:
                fin = str(getattr(cand, "finish_reason", "") or "")
                raise ErrorDeNegocio(f"el modelo {modelo} no devolvió una imagen" + (f" ({fin})" if fin else "")
                                     + ": revisa las instrucciones (los filtros de seguridad rechazan algunas)")
    except ErrorDeNegocio:
        raise
    except Exception as e:  # noqa: BLE001 - se traduce a un error con código
        raise fg._error(e, f"{proveedor} ({modelo})", secretos) from None
    if not imgs:
        raise ErrorDeNegocio(f"el modelo {modelo} no devolvió una imagen")
    return imgs[0]


def _openai(modelo: str, prompt: str, cantidad: int, aspecto: str, cred: dict, flujo: str, timeout: float) -> List[bytes]:
    from proveedores import estilo_openai as eo

    if not cred.get("api_key"):
        raise ErrorDeNegocio("OpenAI necesita una credencial ia_openai con su llave")
    cuerpo: Dict[str, Any] = {"model": modelo, "prompt": prompt, "n": cantidad, "size": TAMANOS_OPENAI.get(aspecto, "1024x1024")}
    if modelo.startswith("dall-e"):
        cuerpo["response_format"] = "b64_json"
        if modelo == "dall-e-3":
            cuerpo.update(n=1, size={"1:1": "1024x1024", "3:4": "1024x1792", "9:16": "1024x1792"}.get(aspecto, "1792x1024"))
    j = pedir_json("OpenAI", "POST", f"{eo.URLS['openai']}/images/generations", {"Content-Type": "application/json", "Authorization": f"Bearer {cred['api_key']}"},
                   cuerpo, timeout, secretos_de(cred), huella(cred), flujo)
    r = [base64.b64decode(x["b64_json"]) for x in (j.get("data") or []) if isinstance(x, dict) and x.get("b64_json")]
    if not r:
        raise ErrorDeNegocio(f"OpenAI ({modelo}) no devolvió imágenes")
    return r


def generar_imagen(proveedor: str, modelo: str, prompt: str, cantidad: int = 1, aspecto: str = "1:1", credencial: Optional[dict] = None,
                   flujo: str = "", timeout: float = 120.0, aviso: Any = None) -> Dict[str, Any]:
    """{imagenes: [png], proveedor, modelo, ms, llamadas}. ErrorDeNegocio si el pedido no sirve; ErrorDeProveedor (con status) si falla el servicio."""
    proveedor = str(proveedor or "vertex")
    if proveedor not in PROVEEDORES_IMAGEN:
        raise ErrorDeNegocio(f"«{proveedor}» no genera imágenes aquí (sí: {', '.join(PROVEEDORES_IMAGEN)})")
    modelo = str(modelo or MODELO_DEFECTO[proveedor]).strip()
    if not prompt.strip():
        raise ErrorDeNegocio("faltan las instrucciones de la imagen")
    cantidad = max(1, min(MAX_IMAGENES, int(cantidad or 1)))
    aspecto = aspecto if aspecto in ASPECTOS else "1:1"
    cred = dict(credencial or {})
    h = huella(cred)
    sem, cubeta = semaforo(proveedor, h), balde(proveedor, h, flujo)

    def una(fn):
        def correr():
            cubeta.tomar(timeout)
            if not sem.acquire(timeout=timeout):
                raise ErrorTransitorio(f"{proveedor}: no se liberó un cupo de concurrencia a tiempo")
            try:
                return fn()
            finally:
                sem.release()
        return con_reintentos(correr, aviso=aviso, secretos=secretos_de(cred))[0]

    t0 = time.time()
    try:
        if proveedor == "openai":
            crudas = una(lambda: _openai(modelo, prompt, cantidad, aspecto, cred, flujo, timeout))[:cantidad]
            llamadas = 1
        else:  # los modelos de Google dan una imagen por pedido
            crudas = [una(lambda: _google(proveedor, modelo, prompt, aspecto, cred, flujo, timeout)) for _ in range(cantidad)]
            llamadas = cantidad
    except (ErrorDeNegocio, ErrorDeProveedor):
        raise
    except Exception as e:  # noqa: BLE001
        msg = enmascarar(str(e), secretos_de(cred))
        if msg != str(e):
            e.args = (msg,)
        raise
    return {"imagenes": [a_png(x) for x in crudas], "proveedor": proveedor, "modelo": modelo, "ms": int((time.time() - t0) * 1000), "llamadas": llamadas}
