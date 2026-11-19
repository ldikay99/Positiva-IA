"""
El proyecto de Google Cloud que usa Vera: solo el del entorno.

Vera no trae escrito el proyecto de ningún cliente. `GCP_PROJECT` es el de
Vertex AI (Gemini con la identidad del servicio) y `PROYECTO_SECRETOS` el de
Secret Manager, que si no se define es el mismo `GCP_PROJECT`. Se leen al
usarse, no al importar: lo que no llama a Google Cloud corre sin ellos, y lo
que sí lo necesita falla con un motivo claro en vez de ir al proyecto de otro.
"""
from __future__ import annotations

import os

from conectores.base import ErrorDeNegocio


class FaltaProyecto(ErrorDeNegocio):
    """Falta el proyecto de Google Cloud en el entorno: reintentar no lo arregla."""


def _del_entorno(nombre: str) -> str:
    return (os.environ.get(nombre) or "").strip()


def proyecto_gcp() -> str:
    """El proyecto de Vertex AI (`GCP_PROJECT`)."""
    valor = _del_entorno("GCP_PROJECT")
    if not valor:
        raise FaltaProyecto(
            "falta GCP_PROJECT: define en el entorno (o en .env) el id del proyecto de Google Cloud "
            "con Vertex AI; Vera no trae uno por defecto")
    return valor


def proyecto_secretos() -> str:
    """El proyecto de Secret Manager (`PROYECTO_SECRETOS`, o `GCP_PROJECT` si no se definió)."""
    valor = _del_entorno("PROYECTO_SECRETOS") or _del_entorno("GCP_PROJECT")
    if not valor:
        raise FaltaProyecto(
            "falta PROYECTO_SECRETOS (o GCP_PROJECT): define en el entorno el id del proyecto de Google Cloud "
            "cuyo Secret Manager guarda las credenciales del buzón; Vera no trae uno por defecto")
    return valor


def descripcion_vertex() -> str:
    """Cómo se autentica Vertex, para mostrarlo sin fallar si aún no hay proyecto."""
    valor = _del_entorno("GCP_PROJECT")
    return ("Identidad del servicio (ADC) del proyecto " + valor) if valor else (
        "Identidad del servicio (ADC) del proyecto de GCP_PROJECT (sin definir)")
