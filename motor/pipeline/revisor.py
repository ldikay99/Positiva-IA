"""
Segundo agente: revisa lo que se va a enviar antes de que se envie.

El agente principal RESUELVE. Este COMPRUEBA, y son dos trabajos distintos con
dos juegos de reglas distintos. Juntarlos en un solo prompt no cabe: los
criterios de resolucion ya ocupan cuatro quintas partes del texto que viaja en
cada vuelta, y el principal agota sus vueltas con frecuencia. Repartirlos deja
a cada uno con su contexto entero.

Lo que este revisa no es coherencia entre campos —de eso ya se encarga la
compuerta, que es determinista y no cuesta— sino lo que ninguna regla fija
puede decidir: si el nombre que se va a registrar es de una persona o del buzon
que mando el correo, si el documento pertenece a quien se nombra, si la ciudad
es donde vive el afectado o donde se expidio su cedula.

Trabaja sobre los documentos YA convertidos del workspace. No convierte nada ni
vuelve a leer el correo original.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from pipeline.modelo import Modelo, Turno
from servidor_mcp.herramientas import Contexto, invocar

# Vueltas del revisor. Menos que el principal: no tiene que resolver nada, solo
# mirar lo que se le entrega y comprobarlo contra los documentos.
MAX_VUELTAS_DEL_REVISOR = 3

# Cuanto se conserva de una respuesta de herramienta.
MAX_CARACTERES_RESPUESTA = 2_000

SISTEMA = """Eres el revisor de una radicacion de PQRD de una ARL. NO resuelves
campos: compruebas los que otro ya resolvio, contra los documentos del caso.

Tienes herramientas para listar, leer y buscar en los documentos ya convertidos.
Usalas antes de opinar. No inventes nada que no este escrito.

QUE COMPRUEBAS, campo por campo:

nombre_afectado — tiene que ser el nombre de la PERSONA del caso.
  Rechazalo si es el nombre de un buzon o de un area ("Notificaciones
  Judiciales", "Gestion Humana", "Soporte Tecnico", "notificaciones
  notificaciones"), si es una razon social ("... S.A.S.", "... Ltda",
  "Agencia de ...") cuando el documento es de persona, o si es el texto de un
  tramite en vez de un nombre ("Informacion Prioritaria", "Solicitud de ...").
  Quien firma el correo NO es el afectado si el correo habla de otra persona.

nombre_remitente — quien escribe. Mismo criterio: un buzon no es una persona.

numero_documento_afectado — tiene que pertenecer a la persona nombrada.
  Rechazalo si en el documento aparece junto a OTRO nombre, si es un numero de
  telefono, un radicado, un NIT de una empresa cuando el afectado es persona, o
  un numero que en el texto identifica a un tercero.

municipio_afectado y departamento_afectado — donde VIVE el afectado.
  La ciudad que sigue a "cedula de ciudadania No. NNN de X" es donde se
  EXPIDIO el documento, no donde vive. Rechazala.

tipo_solicitud — tiene que corresponder a lo que el correo PIDE, no a lo que
  menciona de paso.

COMO RESPONDES:
Una linea por campo revisado, y nada mas. Sin preambulo y sin despedida.

  <campo>: OK
  <campo>: CAMBIAR a <valor> | <en que documento y linea lo viste>
  <campo>: SIN RESPALDO | <que buscaste y no encontraste>

Usa OK cuando el valor esta respaldado por el texto. Usa CAMBIAR solo si
encuentras el valor correcto escrito. Usa SIN RESPALDO cuando el valor que te
dieron no aparece en ninguna parte: es peor registrar un dato equivocado que
dejar que una persona lo revise.

Cuando termines, escribe REVISADO en la ultima linea."""

# Solo lectura. El revisor no reporta hallazgos ni consulta el registro: mira
# lo que hay en los documentos y responde en texto.
HERRAMIENTAS_DEL_REVISOR = ("listar_documentos", "leer_documento",
                            "buscar_en_documentos")

VEREDICTOS = ("OK", "CAMBIAR", "SIN RESPALDO")


@dataclass
class Reparo:
    """Lo que el revisor dice de un campo."""

    campo: str
    veredicto: str          # OK | CAMBIAR | SIN RESPALDO
    valor: str = ""         # solo con CAMBIAR
    detalle: str = ""

    @property
    def contradice(self) -> bool:
        return self.veredicto in ("CAMBIAR", "SIN RESPALDO")


@dataclass
class Revision:
    """Lo que produjo el revisor."""

    reparos: Dict[str, Reparo] = field(default_factory=dict)
    vueltas: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    llamadas_herramienta: int = 0
    # Lineas que no se pudieron interpretar. Constan en vez de descartarse en
    # silencio: si el revisor cambia de formato, hay que verlo.
    ilegibles: List[str] = field(default_factory=list)

    @property
    def hay_reparos(self) -> bool:
        return any(r.contradice for r in self.reparos.values())

    def resumen(self) -> str:
        """Lo que se le devuelve al agente principal, en una linea por campo."""
        if not self.reparos:
            return "El revisor no encontro nada que objetar."
        lineas = []
        for campo in sorted(self.reparos):
            r = self.reparos[campo]
            valor = f" a {r.valor}" if r.valor else ""
            detalle = f" ({r.detalle})" if r.detalle else ""
            lineas.append(f"{campo}: {r.veredicto}{valor}{detalle}")
        return "\n".join(lineas)


def _tarea(valores: Dict[str, Any]) -> str:
    lineas = ["Comprueba estos valores contra los documentos del caso:"]
    for campo in sorted(valores):
        lineas.append(f"  {campo} = {valores[campo]}")
    return "\n".join(lineas)


def leer_reparos(texto: str) -> tuple[Dict[str, Reparo], List[str]]:
    """
    Interpreta la respuesta del revisor.

    Devuelve (reparos, lineas_ilegibles). Una linea que no encaja no se
    descarta en silencio: se devuelve para que quede en el informe.
    """
    reparos: Dict[str, Reparo] = {}
    ilegibles: List[str] = []
    for cruda in (texto or "").splitlines():
        linea = cruda.strip().lstrip("-* ").strip()
        if not linea or linea.upper().startswith("REVISADO"):
            continue
        campo, sep, resto = linea.partition(":")
        if not sep:
            ilegibles.append(linea[:120])
            continue
        campo = campo.strip().lower().replace(" ", "_")
        cuerpo, _, detalle = resto.strip().partition("|")
        cuerpo = cuerpo.strip()
        arriba = cuerpo.upper()
        veredicto = next(
            (v for v in ("SIN RESPALDO", "CAMBIAR", "OK") if arriba.startswith(v)),
            "")
        if not veredicto:
            ilegibles.append(linea[:120])
            continue
        valor = ""
        if veredicto == "CAMBIAR":
            valor = cuerpo[len("CAMBIAR"):].strip().lstrip("a").strip(" :=")
        reparos[campo] = Reparo(campo=campo, veredicto=veredicto, valor=valor,
                                detalle=detalle.strip()[:200])
    return reparos, ilegibles


def revisar(
    modelo: Modelo,
    ctx: Contexto,
    valores: Dict[str, Any],
    esquemas: Sequence[Dict[str, Any]],
    max_vueltas: int = MAX_VUELTAS_DEL_REVISOR,
) -> Revision:
    """
    Corre al revisor sobre los valores que se van a enviar.

    `esquemas` son los de las herramientas de lectura, ya filtrados por quien
    llama: el revisor no puede reportar hallazgos ni consultar el registro.
    """
    resultado = Revision()
    if not valores:
        return resultado

    historial: List[Dict[str, str]] = []
    tarea = {"rol": "usuario", "contenido": _tarea(valores)}

    for vuelta in range(1, max_vueltas + 1):
        resultado.vueltas = vuelta
        turno: Turno = modelo.generar(SISTEMA, [tarea] + historial, esquemas)
        resultado.tokens_entrada += turno.tokens_entrada
        resultado.tokens_salida += turno.tokens_salida

        if not turno.quiere_herramientas:
            reparos, ilegibles = leer_reparos(turno.texto)
            resultado.reparos = reparos
            resultado.ilegibles = ilegibles
            return resultado

        respuestas = []
        for llamada in turno.llamadas:
            resultado.llamadas_herramienta += 1
            if llamada.herramienta not in HERRAMIENTAS_DEL_REVISOR:
                respuestas.append(
                    f"{llamada.herramienta} -> "
                    f"{{\"ok\": false, \"error\": \"el revisor solo lee "
                    f"documentos: {', '.join(HERRAMIENTAS_DEL_REVISOR)}\"}}")
                continue
            r = invocar(ctx, llamada.herramienta, llamada.argumentos)
            respuestas.append(
                f"{llamada.herramienta} -> "
                + json.dumps(r.a_dict(), ensure_ascii=False,
                             default=str)[:MAX_CARACTERES_RESPUESTA])
        historial.append({
            "rol": "asistente",
            "contenido": (turno.texto + "\n" if turno.texto else "")
            + "Invoco: " + ", ".join(l.herramienta for l in turno.llamadas),
        })
        historial.append({
            "rol": "herramienta",
            "contenido": "\n".join(respuestas)
            + f"\n\nVuelta {vuelta} de {max_vueltas}. "
            + ("Ultima vuelta: responde ya el veredicto de cada campo."
               if vuelta >= max_vueltas - 1 else ""),
        })

    return resultado
