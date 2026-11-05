"""
Las marcas que el sistema deja en `categories` de un correo.

Graph no tiene "agregar una categoria": el PATCH manda la lista entera. Escribir
`[marca]` a secas borra todo lo demas, y ahi caben dos perdidas distintas: la
categoria que un funcionario puso a mano, y las marcas del propio sistema.

Cada marca pertenece a una FAMILIA, identificada por su prefijo. Poner una marca
sustituye a las de su misma familia y respeta todas las demas, de modo que
`EN-PROCESO:1` y `REINTENTO:2` conviven en el mismo correo y ninguna pisa a la
otra. Lo que no lleve un prefijo conocido es de alguien mas y no se toca nunca.

  EN-PROCESO:N        una corrida lo tomo y todavia no ha terminado
  REINTENTO:N         se aplazo N veces por fallos que no dicen nada del correo
  REVISION-MANUAL:X   se cerro hacia una persona
  REENVIADO:X         se mando a otra area
  ENT... / RADICADO:  se radico, y es la marca de la que vive la deduplicacion
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from utils.radicado import es_radicado


class OtraCorridaLoTomo(RuntimeError):
    """
    Otra corrida modifico el correo entre que se leyo y que se iba a marcar.

    Vive aqui y no en el servicio de correo para que el pipeline pueda
    reconocerla sin depender de Graph: con otro proveedor de correo el 412
    tendria otro nombre y el pipeline no tendria por que enterarse.
    """


class ReclamoNoEscrito(RuntimeError):
    """
    La marca de reclamacion no se pudo escribir por un fallo del servicio.

    No es lo mismo que `OtraCorridaLoTomo`, donde otra corrida gano el correo y
    lo esta trabajando. Aqui nadie lo tiene: la escritura fallo y el correo
    queda sin dueño, asi que seguir procesandolo permite que la corrida
    siguiente lo tome tambien y las dos lo radiquen.
    """


EN_PROCESO = "EN-PROCESO:"
REINTENTO = "REINTENTO:"
REVISION_MANUAL = "REVISION-MANUAL:"
REENVIADO = "REENVIADO:"

# Familias con prefijo. El radicado no esta aqui porque no lo lleva: se reconoce
# por su forma, y de eso sabe `utils.radicado`.
PREFIJOS = (EN_PROCESO, REINTENTO, REVISION_MANUAL, REENVIADO)

# Marcas que solo tienen sentido mientras la corrida trabaja el correo. Cuando
# el correo llega a un desenlace dejan de decir nada y se retiran, para que la
# siguiente corrida no las lea como trabajo a medias.
DE_PROCESO = (EN_PROCESO, REINTENTO)

# Familia con la que se identifica un radicado, que no tiene prefijo propio.
FAMILIA_RADICADO = "RADICADO"


def familia_de(categoria: Optional[str]) -> str:
    """
    A que familia pertenece una categoria, o cadena vacia si es ajena.

    Una categoria ajena es la que puso una persona desde Outlook. Devolver ""
    para ella es lo que hace que `fusionar` la conserve.
    """
    limpia = str(categoria or "").strip()
    if not limpia:
        return ""
    if es_radicado(limpia):
        return FAMILIA_RADICADO
    for prefijo in PREFIJOS:
        if limpia.upper().startswith(prefijo):
            return prefijo
    return ""


def es_nuestra(categoria: Optional[str]) -> bool:
    """Si la marca la escribio el sistema."""
    return bool(familia_de(categoria))


def reclama_el_correo(categoria: Optional[str]) -> bool:
    """
    Si esta marca es la que toma posesion del correo.

    `EN-PROCESO:N` no vale por lo que dice, sino por QUIEN consigue escribirla:
    se escribe con `If-Match` y el 412 del servidor es lo que le dice a la
    segunda corrida que el correo ya tiene dueño. Por eso su escritura no se
    puede omitir aunque el valor a escribir coincida con el que ya esta: dos
    corridas que arrancan a la vez calculan las dos `EN-PROCESO:1`, y sin
    escribir no hay 412 ni forma de saber que otra se adelanto.
    """
    return familia_de(categoria) == EN_PROCESO


def fusionar(
    actuales: Optional[Iterable],
    nueva: str = "",
    quitar_de_proceso: bool = False,
) -> List[str]:
    """
    La lista de categorias que hay que escribir.

    Sustituye las marcas de la familia de `nueva`, conserva todo lo demas y
    respeta el orden de lo que ya habia. Con `quitar_de_proceso` retira ademas
    `EN-PROCESO` y `REINTENTO`: se usa al cerrar el correo, cuando el desenlace
    ya consta en su propia marca.

    Sin `nueva` solo limpia, que es lo que hace falta cuando el desenlace no
    necesita marca propia.
    """
    marca = str(nueva or "").strip()
    familia = familia_de(marca)
    a_quitar = {familia} if familia else set()
    if quitar_de_proceso:
        a_quitar.update(DE_PROCESO)

    resultado: List[str] = []
    for c in actuales or []:
        limpia = str(c).strip()
        if not limpia:
            continue
        if familia_de(limpia) in a_quitar:
            continue
        if limpia.upper() == marca.upper():
            continue
        resultado.append(limpia)

    if marca:
        resultado.append(marca)
    return resultado


def marcas_del_sistema(categorias: Optional[Iterable]) -> List[str]:
    """Solo lo que escribio el sistema. Para informes."""
    return [str(c).strip() for c in categorias or [] if es_nuestra(c)]


def marcas_ajenas(categorias: Optional[Iterable]) -> List[str]:
    """Solo lo que puso una persona. Nunca se modifica."""
    return [str(c).strip() for c in categorias or []
            if str(c).strip() and not es_nuestra(c)]
