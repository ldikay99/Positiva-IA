"""
Servicios externos del pipeline.

Los nombres se resuelven al USARLOS, no al importar el paquete. Cada servicio
lee `config.settings`, que exige los diez secretos de produccion, y un modulo
hermano sin esa dependencia —los prompts, por ejemplo— tiene que poder
importarse sin ellos.
"""
from importlib import import_module
from typing import Any

# Nombre exportado -> modulo donde vive.
_SERVICIOS = {
    "OutlookService": "outlook",
    "RedisService": "redis_client",
    "GeminiService": "gemini",
    "LangChainAgents": "gemini_agents",
    "RadicacionService": "radicacion",
    "SupabaseService": "supabase_client",
}

__all__ = list(_SERVICIOS)


def __getattr__(nombre: str) -> Any:
    modulo = _SERVICIOS.get(nombre)
    if modulo is None:
        raise AttributeError(
            f"{__name__!r} no exporta {nombre!r}. Exporta: "
            + ", ".join(sorted(_SERVICIOS))
        )
    return getattr(import_module(f".{modulo}", __name__), nombre)


def __dir__() -> list:
    return sorted(__all__)
