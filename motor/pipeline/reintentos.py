"""
Correos que se aplazan a la siguiente corrida en vez de ir a revision manual.

Un fallo de infraestructura no dice nada del correo. Si Vertex no responde o
SGDEA se cae, mandar el correo a una cola humana le cuesta tiempo a una persona
para que descubra que no habia nada que revisar. El job corre cada 30 minutos:
dejarlo en la bandeja lo reintenta solo.

El limite se cuenta en la ETIQUETA del propio correo y no en Redis ni en la
base de radicados. Un fallo de infraestructura puede ser precisamente que esos servicios
esten caidos, y entonces el contador viviria en lo mismo que fallo. La etiqueta
viaja con el correo, sobrevive al reinicio del job y se lee sin salir a ningun
sitio ademas de Graph, que ya hizo falta para traer el correo.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple

# Cuantas corridas se aplaza antes de rendirse. Con el job cada 30 minutos son
# seis horas, que es lo que puede durar una caida de un servicio externo sin que
# el correo deje de ser radicable. Cerrar antes hacia una persona no arregla
# nada —no habia nada que revisar— y saca el correo de la bandeja, que es de
# donde lo tomaria la corrida en la que el servicio ya responde.
MAX_CICLOS = 12

PREFIJO = "REINTENTO:"
_PATRON = re.compile(rf"^{PREFIJO}\s*(\d+)$", re.IGNORECASE)

# Motivos que NO dicen nada del correo.
#
# El criterio es uno solo: si quien recibe el correo NO puede hacer nada con el,
# no se le manda. Un backend caido, un captcha, una autorizacion que falta o un
# motor de OCR ausente son cosas del entorno; el correo esta bien y la persona
# que lo abra no tendra nada que corregir.
#
# Se aplaza aunque el fallo no parezca pasajero. Un permiso se concede, una
# imagen se despliega con el motor y una peticion mal formada se corrige con un
# despliegue: todos duran mas de media hora, y para eso esta el tope de ciclos.
# Si a las doce corridas sigue fallando, entonces si va a una persona, y para
# entonces ya consta que se intento.
#
# ERROR_INESPERADO entra por lo que ES: el cajon de los fallos que ninguna firma
# reconoce. No saber que fallo no autoriza a afirmar que el correo esta mal.
#
# Lo que NO entra es lo que una persona SI puede resolver mirando el correo: un
# dato que falta, un adjunto ilegible o protegido, datos que no cuadran, o que
# no sea una PQRD.
MOTIVOS_QUE_SE_APLAZAN = frozenset({
    "MODELO_NO_RESPONDIO",
    "MODELO_SIN_PERMISO",
    "SGDEA_CAIDO",
    "CLOUDFLARE_403",
    "PETICION_MAL_FORMADA",
    "SIN_OCR",
    "TIEMPO_DE_CORRIDA_AGOTADO",
    "ERROR_INESPERADO",
})


def se_aplaza(motivo: str) -> bool:
    """Si el correo merece otra corrida en vez de una persona."""
    return str(motivo or "").strip().upper() in MOTIVOS_QUE_SE_APLAZAN


def intentos_previos(categorias: Optional[Iterable]) -> int:
    """
    Cuantas veces se aplazo ya este correo.

    Se queda con el mayor: si una corrida etiqueto y no pudo limpiar la
    anterior, el correo lleva dos marcas y la buena es la mas alta. Contarlas o
    quedarse con la primera daria un limite mas laxo del declarado.
    """
    mayor = 0
    for c in categorias or []:
        m = _PATRON.match(str(c).strip())
        if m:
            mayor = max(mayor, int(m.group(1)))
    return mayor


def etiqueta_de_intento(intento: int) -> str:
    return f"{PREFIJO}{intento}"


def decidir(motivo: str, categorias: Optional[Iterable]) -> Tuple[bool, int, str]:
    """
    Si el correo se aplaza, en que intento va y como explicarlo.

    Devuelve (se_aplaza, intento, explicacion). `intento` es el numero que hay
    que etiquetar cuando se aplaza; cuando no, cuantos hubo, para que el motivo
    que llega a la persona diga que ya se intento.
    """
    previos = intentos_previos(categorias)
    if not se_aplaza(motivo):
        return False, previos, ""

    intento = previos + 1
    if intento >= MAX_CICLOS:
        return False, previos, (
            f"se aplazo {previos} vez(ces) por {motivo} y sigue fallando: "
            f"va a revision manual"
        )
    return True, intento, (
        f"{motivo} no dice nada del correo: se deja en la bandeja y la "
        f"siguiente corrida lo reintenta (intento {intento} de {MAX_CICLOS - 1})"
    )
