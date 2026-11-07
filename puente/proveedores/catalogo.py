"""
El catálogo de modelos por proveedor: lo que acepta cada uno y su precio.

Capacidades (`acepta`): texto, imagenes, pdf, json_estructurado, herramientas
(function calling) y voz_en_vivo (audio en tiempo real). `solo_voz` marca los
modelos que solo sirven en una sesión de voz (no para nodos de un flujo).

Precios en USD por millón de tokens (entrada / salida): valores publicados
APROXIMADOS a la fecha de `PRECIOS_FECHA`; sirven para estimar el costo, no para
facturar. En Vertex, `verificado` es la fecha en que el modelo respondió de
verdad en el proyecto con la identidad del servicio.

Además del catálogo fijo, `en_vivo()` pide la lista a la API del proveedor
cuando hay credencial (el servidor la guarda una hora).
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

PRECIOS_FECHA = "2026-11-06"
CAPACIDADES = ("texto", "imagenes", "pdf", "json_estructurado", "herramientas", "voz_en_vivo")


def _m(id_: str, nombre: str, contexto: int, entrada: float, salida: float, imagenes: bool = False, pdf: bool = False,
       json_: bool = True, herramientas: bool = True, voz: bool = False, solo_voz: bool = False, **extra: Any) -> Dict[str, Any]:
    return {"id": id_, "nombre": nombre, "contexto": contexto, "precio_entrada": entrada, "precio_salida": salida, "precio_aproximado": True,
            "acepta": {"texto": True, "imagenes": imagenes, "pdf": pdf, "json_estructurado": json_, "herramientas": herramientas, "voz_en_vivo": voz},
            **({"solo_voz": True} if solo_voz else {}), **extra}


_GEMINI = [
    _m("gemini-2.5-flash", "Gemini 2.5 Flash", 1_048_576, 0.30, 2.50, True, True),
    _m("gemini-2.5-pro", "Gemini 2.5 Pro", 1_048_576, 1.25, 10.00, True, True),
    _m("gemini-3-flash-preview", "Gemini 3 Flash (vista previa)", 1_048_576, 0.50, 3.00, True, True),
    _m("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", 1_048_576, 0.25, 1.50, True, True),
    _m("gemini-3.1-pro-preview", "Gemini 3.1 Pro (vista previa)", 1_048_576, 2.00, 12.00, True, True),
]
_VERIFICADO = "2026-11-06"

MODELOS: Dict[str, List[Dict[str, Any]]] = {
    # Los cinco de Vertex respondieron de verdad en positiva-sgda-prd-416417 (location global) el 2026-11-06;
    # gemini-2.5-flash-lite, gemini-3-pro-preview y gemini-3.1-flash-lite-preview dieron 404 en el proyecto.
    "vertex": [{**copy.deepcopy(m), "verificado": _VERIFICADO} for m in _GEMINI] + [
        _m("gemini-3.8-live", "Gemini 3.8 Live (voz)", 131_072, 3.00, 12.00, True, False, False, True, True, True, ubicacion="us-central1"),
    ],
    "gemini": copy.deepcopy(_GEMINI) + [
        _m("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite", 1_048_576, 0.10, 0.40, True, True),
        _m("gemini-live-2.5-flash-preview", "Gemini 2.5 Flash Live (voz)", 131_072, 0.50, 2.00, True, False, False, True, True, True),
    ],
    "openai": [
        _m("gpt-5", "GPT-5", 400_000, 1.25, 10.00, True, True),
        _m("gpt-5-mini", "GPT-5 mini", 400_000, 0.25, 2.00, True, True),
        _m("gpt-5-nano", "GPT-5 nano", 400_000, 0.05, 0.40, True, True),
        _m("gpt-4.1", "GPT-4.1", 1_047_576, 2.00, 8.00, True, True),
        _m("gpt-4.1-mini", "GPT-4.1 mini", 1_047_576, 0.40, 1.60, True, True),
        _m("gpt-4o", "GPT-4o", 128_000, 2.50, 10.00, True, True),
        _m("gpt-4o-mini", "GPT-4o mini", 128_000, 0.15, 0.60, True, True),
        _m("o4-mini", "o4-mini (razonamiento)", 200_000, 1.10, 4.40, True, True),
        _m("gpt-realtime", "GPT Realtime (voz)", 32_000, 4.00, 16.00, True, False, False, True, True, True),
    ],
    "anthropic": [
        _m("claude-opus-5-5", "Claude Opus 5.5", 1_000_000, 4.00, 20.00, True, True),
        _m("claude-sonnet-5-5", "Claude Sonnet 5.5", 1_000_000, 2.00, 10.00, True, True),
        _m("claude-haiku-5-5", "Claude Haiku 5.5", 1_000_000, 0.10, 0.50, True, True),
        _m("claude-opus-5", "Claude Opus 5", 1_000_000, 5.00, 25.00, True, True),
        _m("claude-sonnet-5", "Claude Sonnet 5", 1_000_000, 2.00, 10.00, True, True),
        _m("claude-opus-4-8", "Claude Opus 4.8", 1_000_000, 5.00, 25.00, True, True),
        _m("claude-sonnet-4-6", "Claude Sonnet 4.6", 1_000_000, 3.00, 15.00, True, True),
        _m("claude-haiku-4-5", "Claude Haiku 4.5", 200_000, 1.00, 5.00, True, True),
    ],
    "deepseek": [
        _m("deepseek-chat", "DeepSeek Chat", 128_000, 0.28, 0.42),
        _m("deepseek-reasoner", "DeepSeek Reasoner (razonamiento)", 128_000, 0.28, 0.42),
    ],
    "mistral": [
        _m("mistral-large-latest", "Mistral Large", 128_000, 0.50, 1.50, True),
        _m("mistral-medium-latest", "Mistral Medium", 128_000, 0.40, 2.00, True),
        _m("mistral-small-latest", "Mistral Small", 128_000, 0.10, 0.30, True),
    ],
    "groq": [
        _m("llama-3.3-70b-versatile", "Llama 3.3 70B", 131_072, 0.59, 0.79),
        _m("llama-3.1-8b-instant", "Llama 3.1 8B", 131_072, 0.05, 0.08),
        _m("openai/gpt-oss-120b", "GPT-OSS 120B", 131_072, 0.15, 0.75),
        _m("meta-llama/llama-4-scout-17b-16e-instruct", "Llama 4 Scout", 131_072, 0.11, 0.34, True),
    ],
    "openrouter": [
        _m("openai/gpt-4o-mini", "GPT-4o mini (OpenRouter)", 128_000, 0.15, 0.60, True, True),
        _m("google/gemini-2.5-flash", "Gemini 2.5 Flash (OpenRouter)", 1_048_576, 0.30, 2.50, True, True),
        _m("anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6 (OpenRouter)", 1_000_000, 3.00, 15.00, True, True),
        _m("deepseek/deepseek-chat", "DeepSeek Chat (OpenRouter)", 128_000, 0.28, 0.42),
        _m("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B (OpenRouter)", 131_072, 0.12, 0.30),
    ],
    "cohere": [
        _m("command-a-03-2025", "Command A", 256_000, 2.50, 10.00),
        _m("command-a-vision-07-2025", "Command A Vision", 128_000, 2.50, 10.00, True, False, True, False),
        _m("command-r-plus-08-2024", "Command R+", 128_000, 2.50, 10.00),
        _m("command-r-08-2024", "Command R", 128_000, 0.15, 0.60),
        _m("command-r7b-12-2024", "Command R7B", 128_000, 0.0375, 0.15),
    ],
    "ollama": [
        _m("llama3.2", "Llama 3.2 (local)", 131_072, 0, 0),
        _m("qwen2.5", "Qwen 2.5 (local)", 131_072, 0, 0),
        _m("llava", "LLaVA (local, imágenes)", 4_096, 0, 0, True, False, True, False),
    ],
    # azure_openai: el modelo es el nombre del despliegue; se usa el catálogo de OpenAI para estimar.
    "azure_openai": [],
    "openai_compatible": [],
    "personalizado": [],
}


def modelos(proveedor: str) -> List[Dict[str, Any]]:
    return copy.deepcopy(MODELOS.get(proveedor) or [])


def ficha(proveedor: str, modelo: str) -> Optional[Dict[str, Any]]:
    """La ficha de un modelo, o None si no está en el catálogo. Azure usa la de OpenAI con el mismo nombre."""
    for m in MODELOS.get(proveedor) or []:
        if m["id"] == modelo:
            return m
    if proveedor == "azure_openai":
        return ficha("openai", modelo)
    if proveedor == "openrouter" or proveedor == "groq":
        return None
    return None


def acepta(proveedor: str, modelo: str, capacidad: str) -> Optional[bool]:
    """True/False según el catálogo (o lo que el proveedor acepta por defecto); None si no se sabe."""
    f = ficha(proveedor, modelo)
    if f is not None:
        return bool(f["acepta"].get(capacidad))
    from proveedores.registro import PROVEEDORES

    a = PROVEEDORES.get(proveedor)
    return (a.por_defecto or {}).get(capacidad) if a else None


def costo(proveedor: str, modelo: str, entrada: int, salida: int) -> Optional[float]:
    f = ficha(proveedor, modelo)
    if not f:
        return None
    return round((entrada * float(f["precio_entrada"]) + salida * float(f["precio_salida"])) / 1e6, 6)


def todo() -> Dict[str, Any]:
    """El catálogo completo con los proveedores (lo sirve el servidor)."""
    from proveedores.registro import PROVEEDORES

    return {"precios_fecha": PRECIOS_FECHA, "precios_aproximados": True, "capacidades": list(CAPACIDADES),
            "proveedores": [{**a.describir(), "por_defecto": a.por_defecto, "modelos": modelos(a.id)} for a in PROVEEDORES.values()]}


def en_vivo(proveedor: str, cred: dict, flujo: str = "", timeout: float = 20.0) -> List[Dict[str, Any]]:
    """La lista del proveedor (con su API) unida al catálogo: lo conocido conserva su ficha; lo nuevo trae lo que diga la API."""
    from proveedores.registro import obtener

    a = obtener(proveedor)
    a.revisar_credencial(cred or {})
    vivos = a.listar(cred or {}, flujo, timeout)
    r = []
    for v in vivos:
        f = ficha(proveedor, v["id"])
        if f:
            r.append({**copy.deepcopy(f), "en_vivo": True})
        else:
            acep = {k: (a.por_defecto or {}).get(k) for k in CAPACIDADES}
            acep.update({k: x for k, x in (v.get("acepta") or {}).items() if x is not None})
            r.append({"id": v["id"], "nombre": v.get("nombre") or v["id"], "contexto": v.get("contexto"), "acepta": acep, "en_vivo": True,
                      "capacidades_conocidas": bool(v.get("acepta")),
                      **({"precio_entrada": v["precio_entrada"], "precio_salida": v["precio_salida"]} if "precio_entrada" in v else {})})
    return r
