"""
Reclamar un correo antes de trabajarlo, para no radicarlo dos veces.

Las capas de deduplicacion son de LECTURA y se apoyan en marcas que se escriben
DESPUES de radicar: la etiqueta con el numero de radicado y el hash en la cache.
Entre que SGDEA devuelve el radicado y que el correo queda etiquetado hay una
ventana en la que cabe otra corrida entera, y por ahi el mismo caso sale
radicado dos veces con minutos de diferencia.

La marca de reclamo se escribe ANTES de empezar, de modo que ningun tramo del
proceso quede sin rastro en el correo.

No usa marcas de tiempo. Una hora no dice si otra corrida sigue viva o murio
hace rato, y comparar relojes entre procesos es justo lo que no se puede hacer
con garantias. La cuenta de reclamos si lo dice: si el correo vuelve con la
marca puesta, la corrida anterior no llego al final.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple

PREFIJO = "EN-PROCESO"
_PATRON = re.compile(rf"^{PREFIJO}:(\d+)$", re.IGNORECASE)

# Cuantas veces se vuelve a tomar un correo que quedo reclamado sin terminar.
#
# Doce, los mismos ciclos que `reintentos.MAX_CICLOS`: con el job cada media
# hora son seis horas. Un correo se queda reclamado sin terminar cuando la
# corrida se cayo, y las corridas se caen por lo mismo que se cae todo —el
# backend, la red, el modelo—, no por el correo. Con un tope corto, una caida
# de horas agota el contador de todo lo que estuviera en vuelo y lo cierra a
# revision manual sin que haya nada que revisar.
#
# A las doce si lo mira una persona: a esas alturas el pipeline se esta cayendo
# siempre con ese correo y la causa ya no parece del entorno.
MAX_RECLAMOS = 12


def marca(intento: int) -> str:
    return f"{PREFIJO}:{intento}"


def reclamos_previos(categorias: Optional[Iterable]) -> int:
    """
    Cuantas veces se reclamo ya este correo sin llegar al final.

    Se queda con el mayor, no con el primero: dos marcas significan que una
    corrida etiqueto y no pudo limpiar, y la buena es la mas alta.
    """
    mayor = 0
    for c in categorias or []:
        m = _PATRON.match(str(c).strip())
        if m:
            mayor = max(mayor, int(m.group(1)))
    return mayor


def esta_reclamado(categorias: Optional[Iterable]) -> bool:
    return reclamos_previos(categorias) > 0


def decidir(categorias: Optional[Iterable]) -> Tuple[bool, int, str]:
    """
    Si se puede trabajar el correo, con que numero de reclamo y por que.

    Devuelve (se_trabaja, intento, explicacion).
    """
    previos = reclamos_previos(categorias)
    if previos == 0:
        return True, 1, ""

    intento = previos + 1
    if intento > MAX_RECLAMOS:
        return False, previos, (
            f"el correo se reclamo {previos} veces y ninguna corrida llego al "
            f"final: va a revision manual en vez de seguir reintentandolo"
        )
    return True, intento, (
        f"venia reclamado por una corrida que no termino: se retoma "
        f"(reclamo {intento} de {MAX_RECLAMOS})"
    )


def otro_lo_radico(categorias: Optional[Iterable]) -> str:
    """
    El numero de radicado que otra corrida le puso mientras tanto, o vacio.

    Se consulta JUSTO ANTES de radicar. Es la ultima ocasion de no escribir dos
    veces en SGDEA, que es lo unico del pipeline que no se puede deshacer.
    """
    from utils.radicado import etiqueta_de_radicado

    return etiqueta_de_radicado(categorias)
