"""
Forma de una etiqueta de radicado, en un solo sitio.

La etiqueta `ENT...` que se pone al correo al radicarlo es lo que despues
delata que el caso ya se atendio. Quien la escribe y quien la lee comparten
esta definicion: un lector mas laxo descartaria una PQRD legitima, y uno mas
estricto la radicaria dos veces.
"""
import re
from typing import Iterable, Optional, Tuple

# ENT seguido del numero, con o sin separador. El digito es lo que distingue un
# radicado de una palabra que empieza igual.
PATRON_RADICADO = re.compile(r"^ENT[\s\-]?\d", re.IGNORECASE)

# Marca propia para los identificadores que SGDEA devuelve con otra forma: el
# lector los reconoce por el prefijo aunque no encajen en PATRON_RADICADO.
PREFIJO_PROPIO = "RADICADO:"
_PATRON_PROPIO = re.compile(rf"^{PREFIJO_PROPIO}\s*\S", re.IGNORECASE)


def es_radicado(etiqueta: Optional[str]) -> bool:
    limpia = str(etiqueta or "").strip()
    return bool(PATRON_RADICADO.match(limpia) or _PATRON_PROPIO.match(limpia))


def etiqueta_para(radicado: Optional[str]) -> Tuple[str, str]:
    """
    Etiqueta que se le pone al correo, y la anomalia si la hubo.

    Devuelve ("", motivo) si no hay nada que etiquetar. Si el identificador que
    devolvio SGDEA no tiene la forma esperada, se envuelve con la marca propia
    para que el lector lo siga reconociendo, y se reporta: significa que el
    contrato con SGDEA cambio y hay que revisarlo.
    """
    limpio = str(radicado or "").strip()
    if not limpio:
        return "", "no hay numero de radicado que etiquetar"
    if es_radicado(limpio):
        return limpio, ""
    return (
        f"{PREFIJO_PROPIO}{limpio}",
        f"SGDEA devolvio '{limpio}', que no tiene la forma ENT<numero>: se "
        f"etiqueta como '{PREFIJO_PROPIO}{limpio}' para no perder la "
        f"deduplicacion, pero hay que revisar el contrato",
    )


def etiqueta_de_radicado(categorias: Optional[Iterable]) -> str:
    """Primera etiqueta con forma de radicado, o cadena vacia."""
    for c in categorias or []:
        limpia = str(c).strip()
        if es_radicado(limpia):
            return limpia
    return ""
