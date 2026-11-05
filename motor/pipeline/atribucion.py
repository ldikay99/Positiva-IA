"""Quien resolvio cada campo durante una corrida."""
from __future__ import annotations

from enum import Enum


class Origen(str, Enum):
    """Quien resolvio un campo."""

    ALGORITMO = "algoritmo"
    IA = "ia"
    # No sale del correo sino del registro de SGDEA, consultado por documento.
    # Se distingue de los otros dos porque su acierto no depende de leer bien el
    # correo sino de haber extraido bien el documento.
    REGISTRO = "registro"
    NO_RESUELTO = "no_resuelto"   # nadie pudo -> revision manual
