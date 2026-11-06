"""
Lo que la compuerta rechaza se le devuelve al agente para que lo corrija.

La compuerta es la ultima palabra antes de radicar, pero muchos de sus rechazos
tienen arreglo leyendo otra vez el correo: el remitente es el area y no la
entidad, el documento es el NIT de quien escribe y no la cedula del afectado,
el nombre trae el cargo delante. Mandar eso a manual sin intentarlo es trabajo
de una persona que el agente puede hacer, con el motivo ya dicho.

Se intenta una sola vez. Lo que vuelva pasa por la misma compuerta, asi que la
correccion no puede colar nada que la validacion no acepte.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Dict

from pipeline.extraccion import (
    MOTIVOS_DE_AUSENCIA_CONSTATADA,
    Extraccion,
    Hallazgo,
    NoResuelto,
)

MOTIVO_RECHAZADO = "RECHAZADO_EN_LA_VALIDACION"

# Los campos que el agente sabe resolver. Un reparo sobre otra cosa (la oficina,
# el tipo de PQRD) no se arregla leyendo el correo.
CAMPOS_CORREGIBLES = frozenset({
    "nombre_afectado", "numero_documento_afectado", "tipo_documento_afectado",
    "nombre_remitente", "numero_documento_remitente", "tipo_documento_remitente",
    "tipo_remitente", "tipo_solicitud", "telefono_afectado",
    "direccion_afectado", "sexo_afectado",
})


def campos_a_corregir(dictamen, determinista: Extraccion) -> Dict[str, str]:
    """
    {campo: motivo} de los reparos bloqueantes que tiene sentido devolver.

    Un campo obligatorio que falta porque el algoritmo constato que el correo
    no lo trae no se devuelve: el agente lee el mismo texto y la respuesta
    seria la misma.
    """
    salida: Dict[str, str] = {}
    for r in dictamen.bloqueantes:
        if r.campo not in CAMPOS_CORREGIBLES:
            continue
        nr = determinista.no_resueltos.get(r.campo)
        if (r.codigo == "FALTA_CAMPO_OBLIGATORIO" and nr is not None
                and nr.motivo in MOTIVOS_DE_AUSENCIA_CONSTATADA):
            continue
        previo = salida.get(r.campo)
        motivo = f"{r.codigo}: {r.detalle}"
        salida[r.campo] = f"{previo}; {motivo}" if previo else motivo
    return salida


def extraccion_para_corregir(
    determinista: Extraccion, hallazgos: Dict[str, Hallazgo],
    a_corregir: Dict[str, str],
) -> Extraccion:
    """
    La tarea de correccion: lo aceptado queda como resuelto y lo rechazado
    vuelve como pendiente, con el valor que se habia propuesto y por que no
    sirvio, para que el agente no lo repita.
    """
    resueltos = {
        c: replace(h, a_confirmar="") for c, h in hallazgos.items()
        if c not in a_corregir
    }
    pendientes = {}
    for campo, motivo in a_corregir.items():
        anterior = hallazgos.get(campo)
        propuesto = (f"se propuso '{anterior.valor}' y " if anterior is not None
                     else "quedo vacio y ")
        pendientes[campo] = NoResuelto(
            campo, MOTIVO_RECHAZADO,
            f"{propuesto}la validacion lo rechazo ({motivo}). Busca en el "
            f"correo y sus documentos el valor que de verdad corresponde a este "
            f"campo. No repitas el valor rechazado. Si no esta, no lo reportes.",
        )
    return Extraccion(hallazgos=resueltos, no_resueltos=pendientes,
                      contexto=determinista.contexto)
