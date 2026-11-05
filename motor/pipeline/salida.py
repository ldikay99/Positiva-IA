"""
Como sale la telemetria por consola.

En una terminal interesa leerla; en Cloud Logging interesa poder filtrarla. Son
formatos distintos y el codigo que informa no deberia tener que saber en cual
esta corriendo.

Cloud Logging trata cada LINEA de la salida estandar como una entrada
independiente, asi que un informe de sesenta renglones se convierte en sesenta
entradas sueltas, cada una con su hora, imposibles de leer juntas. Una linea con
un objeto JSON, en cambio, es una sola entrada con campos: el informe entero
cabe en un campo y los numeros quedan consultables uno a uno.

    jsonPayload.accion="manual"
    jsonPayload.motivo_manual="DOCUMENTO_AMBIGUO"
    jsonPayload.radicados>0
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

# Nombre del campo con el que Cloud Logging pinta el titulo de la entrada.
CAMPO_MENSAJE = "message"
# Cloud Logging lo lee de aqui para colorear y filtrar por nivel.
CAMPO_SEVERIDAD = "severity"

# Severidades que se usan. Un correo a revision manual no es un error del
# sistema —el sistema hizo lo correcto— pero tiene que destacar sobre el resto.
INFO = "INFO"
AVISO = "WARNING"
ERROR = "ERROR"


# Variables con que Cloud Run anuncia que la salida la recoge Cloud Logging.
# `K_SERVICE` solo la define un Service; un Job define las suyas y ninguna
# empieza por K_. Mirando solo `K_SERVICE`, el Job de radicacion imprimia en
# formato de terminal y cada entrada se partia en una linea por campo.
VARIABLES_DE_CLOUD_RUN = (
    "K_SERVICE",           # Cloud Run Service
    "CLOUD_RUN_JOB",       # Cloud Run Job
    "CLOUD_RUN_EXECUTION",
)


def en_la_nube() -> bool:
    """
    Si la salida la va a leer Cloud Logging y no una persona.

    `FORMATO_LOG` permite forzarlo para probar el formato sin desplegar.
    """
    forzado = (os.environ.get("FORMATO_LOG") or "").strip().lower()
    if forzado in ("json", "texto"):
        return forzado == "json"
    return any(os.environ.get(v) for v in VARIABLES_DE_CLOUD_RUN)


VACIOS = (None, "", (), [], {})


def referencia_del_correo(id_correo: str) -> str:
    """
    Un identificador corto que distinga un correo de otro en la consola.

    Los identificadores de Graph del mismo buzon coinciden en casi todo su
    largo y se diferencian al final, asi que el prefijo no sirve para seguir un
    correo: los de una misma corrida salen todos iguales.
    """
    limpio = str(id_correo or "").rstrip("=")
    return limpio[-10:] if limpio else "?"


def datos_del_correo(email: Any) -> Dict[str, Any]:
    """
    Lo que permite ubicar un correo en el buzon a partir del registro.

    `internet_message_id` es el identificador que sirve para buscarlo: el de
    Graph cambia cada vez que el correo se mueve de carpeta.
    """
    de = getattr(email, "from_address", None)
    return {
        "correo": referencia_del_correo(getattr(email, "id", "")),
        "internet_message_id": getattr(email, "internet_message_id", "") or "",
        "remitente_correo": getattr(de, "address", "") or "",
        "asunto": (getattr(email, "subject", "") or "")[:200],
        "recibido": getattr(email, "received_datetime", "") or "",
    }


def _texto_del_valor(valor: Any) -> str:
    if isinstance(valor, (list, tuple, set)):
        return ", ".join(str(v) for v in valor)
    return str(valor)


def para_leer(
    campos: Dict[str, Any], solo_json: Any = (), en_su_linea: Any = ()
) -> str:
    """
    Los campos como los lee una persona.

    Los de `en_su_linea` van uno por renglon, en ese orden; el resto se junta
    en un solo renglon al final. Se omite lo vacio. Repartirlos por el largo
    del valor dejaba el mismo campo unas veces suelto y otras dentro de la
    linea compacta, y la salida no se podia recorrer con la vista.
    """
    omitidos = set(solo_json) | {"informe"}
    lineas = []
    for clave in en_su_linea:
        valor = campos.get(clave)
        if clave in omitidos or valor in VACIOS:
            continue
        lineas.append(f"    {clave.replace('_', ' ')}: {_texto_del_valor(valor)}")

    juntos = [
        f"{clave}={_texto_del_valor(valor)}"
        for clave, valor in campos.items()
        if clave not in omitidos and clave not in set(en_su_linea)
        and valor not in VACIOS
    ]
    if juntos:
        lineas.append("    " + "  ".join(juntos))
    return "\n".join(lineas)


def emitir(
    mensaje: str, severidad: str = INFO, solo_json: Any = (),
    en_su_linea: Any = (), **campos: Any
) -> None:
    """
    Escribe una entrada de telemetria.

    En JSON sale una sola linea con todos los campos, que es lo que mantiene la
    entrada de una pieza y deja cada numero consultable. En texto sale el
    mensaje y, debajo, solo lo que el mensaje no dice ya.

    `solo_json` nombra los campos que el mensaje ya lleva escritos: van al
    registro para poder filtrar por ellos, pero repetirlos en la consola es
    ruido. `en_su_linea` nombra los que se leen mejor sueltos, en ese orden.
    """
    if en_la_nube():
        entrada: Dict[str, Any] = {CAMPO_MENSAJE: mensaje,
                                   CAMPO_SEVERIDAD: severidad}
        entrada.update(campos)
        # `default=str` para que un dato que no sea JSON no tumbe la corrida
        # entera por un problema de formato.
        sys.stdout.write(
            json.dumps(entrada, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
        return

    print(mensaje)
    detalle = para_leer(campos, solo_json, en_su_linea)
    if detalle:
        print(detalle)
    informe = campos.get("informe")
    if informe:
        print(informe)
