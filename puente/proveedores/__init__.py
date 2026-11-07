"""
Proveedores de IA de los flujos: una sola interfaz para todos.

    from proveedores import generar, conversar_con_herramientas, probar, PROVEEDORES

Cada proveedor es un módulo que registra su adaptador (ver `registro.py`):
vertex y gemini (`familia_gemini.py`); openai, azure_openai, deepseek, mistral, groq,
openrouter, ollama y openai_compatible (`estilo_openai.py`); anthropic (`claude.py`);
cohere; y personalizado (HTTP configurable sin código). Los límites, reintentos
y la máscara de llaves están en `base.py`; el catálogo en `catalogo.py`; la
resolución nodo → flujo → global y la validación en `resolver.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PUENTE = str(Path(__file__).resolve().parent.parent)
if _PUENTE not in sys.path:
    sys.path.insert(0, _PUENTE)

# El orden de registro es el de las listas: Vertex primero (el de siempre).
from proveedores import familia_gemini  # noqa: E402,F401,I001  (cada módulo registra sus adaptadores)
from proveedores import estilo_openai  # noqa: E402,F401
from proveedores import claude  # noqa: E402,F401
from proveedores import cohere  # noqa: E402,F401
from proveedores import personalizado  # noqa: E402,F401
from proveedores import catalogo  # noqa: E402,F401
from proveedores.base import ErrorDeProveedor, Parte, enmascarar  # noqa: E402,F401
from proveedores.nucleo import conversar_con_herramientas, generar, probar  # noqa: E402,F401
from proveedores import imagenes  # noqa: E402,F401  (imágenes generadas: vertex, gemini y openai)
from proveedores.imagenes import generar_imagen  # noqa: E402,F401
from proveedores.registro import PROVEEDORES, Adaptador, obtener, registrar  # noqa: E402,F401
from proveedores.resolver import GLOBAL_DEFECTO, ia_del_flujo, ia_del_nodo, ia_global, problemas_ia  # noqa: E402,F401

__all__ = ["generar", "generar_imagen", "imagenes", "probar", "conversar_con_herramientas", "PROVEEDORES", "Adaptador", "registrar", "obtener", "catalogo",
           "problemas_ia", "ia_del_flujo", "ia_del_nodo", "ia_global", "GLOBAL_DEFECTO", "ErrorDeProveedor", "Parte", "enmascarar"]
