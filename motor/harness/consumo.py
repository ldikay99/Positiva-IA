"""
Cuanto texto se le manda al modelo: pipeline viejo contra pipeline nuevo.

    python -m harness.consumo

No llama a Vertex ni gasta dinero. Lo que se mide es determinista: cuantas
veces se invoca al modelo por correo y de que tamaño es cada peticion. Eso es
lo que se paga.

El viejo manda `asunto + cuerpo + adjuntos` COMPLETO en cada una de sus
llamadas, una por campo. El nuevo manda una sola tarea con lo que quedo
pendiente y un encabezado acotado, y deja que el agente pida el resto con
herramientas solo si lo necesita.

Los tokens se estiman a partir de los caracteres; la constante esta declarada
abajo.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from harness.caso import Caso, cargar_corpus
from harness.costo import TARIFAS_USD_POR_MILLON
from pipeline.extraccion import extraer_todo

RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"
SERIE = Path(__file__).resolve().parent.parent / "logs" / "serie.tsv"
ANCHO = 72

# Caracteres por token en texto en español. Aproximacion; ver el docstring.
CARACTERES_POR_TOKEN = 4

# Cuantas vueltas da el agente por correo no se puede saber sin correrlo: son
# las que necesite para encontrar los datos, con el tope de `MAX_ITERACIONES`.
# Aqui se mide la UNA vuelta, que es la unidad, y se muestra el rango entre una
# y el tope. Suponer un numero fijo es lo que hacia que la estimacion no se
# pareciera a la corrida.


def _modelo_de_cada_agente() -> Dict[str, str]:
    """
    Que modelo usa cada agente del pipeline viejo.

    Los nombres se leen de `services.prompts`, que es donde el pipeline viejo
    los declara. Cuales dos agentes van al modelo caro es un hecho de
    `gemini_agents._run_agent`: `TIPO_SOLICITUD` con `clasificacion=True` y
    `ELEGIR_DOCUMENTO_AFECTADO` con `extraccion=True`. Los demas usan el
    general, que cuesta cuatro veces menos.
    """
    from services.prompts import (
        GEMINI_MODEL, GEMINI_MODEL_CLASIFICACION, GEMINI_MODEL_EXTRACCION)

    return {
        "TIPO_SOLICITUD": GEMINI_MODEL_CLASIFICACION,
        "ELEGIR_DOCUMENTO_AFECTADO": GEMINI_MODEL_EXTRACCION,
        "__general__": GEMINI_MODEL,
    }


def _costo_entrada(tokens: int, modelo: str) -> Optional[float]:
    """USD de los tokens de entrada. None si el modelo no tiene tarifa."""
    tarifa = TARIFAS_USD_POR_MILLON.get(modelo)
    if not tarifa:
        return None
    return tokens / 1_000_000 * tarifa["entrada"]


def _prompts_viejos():
    from services.prompts import PROMPTS
    return PROMPTS


@dataclass
class Medida:
    correos: int = 0
    llamadas: int = 0
    caracteres: int = 0
    detalle: dict = field(default_factory=dict)

    def sumar(self, etiqueta: str, caracteres: int, modelo: str = "") -> None:
        self.llamadas += 1
        self.caracteres += caracteres
        d = self.detalle.setdefault(
            etiqueta, {"llamadas": 0, "caracteres": 0, "modelo": modelo})
        d["llamadas"] += 1
        d["caracteres"] += caracteres

    @property
    def tokens(self) -> int:
        return self.caracteres // CARACTERES_POR_TOKEN

    @property
    def costo_por_mil_correos(self) -> Optional[float]:
        """
        USD de entrada por cada mil correos, cobrando cada agente a su tarifa.

        Los tokens no bastan para comparar: el pipeline viejo manda dos de sus
        agentes al modelo caro, que cuesta cuatro veces mas por token.
        None si algun modelo usado no tiene tarifa conocida.
        """
        if not self.correos:
            return None
        total = 0.0
        for d in self.detalle.values():
            tokens = d["caracteres"] // CARACTERES_POR_TOKEN
            parcial = _costo_entrada(tokens, d.get("modelo", ""))
            if parcial is None:
                return None
            total += parcial
        return total / self.correos * 1000

    @property
    def llamadas_por_correo(self) -> float:
        return self.llamadas / self.correos if self.correos else 0.0

    @property
    def tokens_por_correo(self) -> int:
        return self.tokens // self.correos if self.correos else 0


def medir_viejo(casos: List[Caso]) -> Medida:
    """
    Replica la estructura de llamadas de `services/gemini_agents.py`.

    Cada agente arma su mensaje como
    `Subject: ... Body: ... Archivos adjuntos: ...`, es decir el correo entero,
    y lo antepone al prompt del sistema.

    Se reproducen tambien sus cortocircuitos: los dos agentes de documento no
    se llaman cuando hay cero o un candidato, y el del nombre del remitente
    solo se llama si la cascada determinista fallo.
    """
    from utils.documento_candidatos import extraer_candidatos_documento
    from utils.generic_filter import extraer_nombre_remitente_real

    prompts = _prompts_viejos()
    modelos = _modelo_de_cada_agente()
    general = modelos["__general__"]
    m = Medida(correos=len(casos))

    # Reciben el correo completo, siempre.
    siempre = (
        "TIPO_REMITENTE", "TIPO_DOCUMENTO_AFECTADO", "TIPO_PQRD",
        "TIPO_SOLICITUD", "TIPO_DOCUMENTO_REMITENTE", "NOMBRE_AFECTADO",
    )
    for caso in casos:
        adjuntos = "\n".join(caso.adjuntos.values())
        completo = (
            f"Subject: {caso.asunto}\n\nBody: {caso.cuerpo_md}\n\n"
            f"Archivos adjuntos: {adjuntos}"
        )
        for nombre in siempre:
            m.sumar(nombre, len(prompts.get(nombre, "")) + len(completo),
                    modelos.get(nombre, general))

        # Los dos de documento solo se llaman si hay que ELEGIR, y ademas
        # adjuntan la lista de candidatos al mensaje.
        candidatos = extraer_candidatos_documento(
            f"{caso.asunto}\n{caso.cuerpo_md}\n{adjuntos}")
        if len(candidatos) > 1:
            lista = sum(len(c.get("contexto", "")) + 20 for c in candidatos)
            for nombre in ("ELEGIR_DOCUMENTO_AFECTADO", "ELEGIR_DOCUMENTO_REMITENTE"):
                m.sumar(nombre, len(prompts.get(nombre, "")) + len(completo) + lista,
                        modelos.get(nombre, general))

        # Solo se llama si la cascada determinista no resolvio el nombre.
        if not extraer_nombre_remitente_real(
            caso.remitente_nombre, caso.remitente_correo, caso.cuerpo_md,
            sender_name=caso.sender_nombre,
            nombres_hilo=[(caso.remitente_nombre, caso.remitente_correo)],
            cuerpo_email_actual=caso.cuerpo_md,
        ):
            m.sumar("NOMBRE_REMITENTE",
                    len(prompts.get("NOMBRE_REMITENTE", "")) + len(completo),
                    general)

        # Este solo recibe el nombre, no el correo.
        m.sumar("SEXO_AFECTADO", len(prompts.get("SEXO_AFECTADO", "")) + 40,
                general)
    return m


@dataclass
class Medido:
    """Consumo real leido de `logs/serie.tsv`, no estimado."""

    corridas: int
    correos: int
    tokens_entrada: int
    llamadas: int

    @property
    def tokens_por_correo(self) -> int:
        return self.tokens_entrada // self.correos if self.correos else 0


def leer_medido(ruta: Path) -> Optional[Medido]:
    """
    Consumo de las corridas registradas EN ESTA MAQUINA.

    `logs/` no se versiona: son las corridas que hizo quien ejecuta el harness,
    con los correos que tuviera delante y con la version del prompt de ese
    momento. No describe la produccion, donde los adjuntos son mas grandes y el
    agente da mas vueltas. Para el consumo de produccion, el informe de la
    corrida del Job.
    """
    if not ruta.exists():
        return None
    filas = [
        l.split("\t") for l in
        ruta.read_text(encoding="utf-8").splitlines()[1:] if l.strip()
    ]
    if not filas:
        return None
    return Medido(
        corridas=len(filas),
        correos=sum(int(f[2]) for f in filas),
        llamadas=sum(int(f[8]) for f in filas),
        tokens_entrada=sum(int(f[9]) for f in filas),
    )


def peso_de_los_esquemas() -> int:
    """
    Caracteres de los esquemas de las herramientas.

    Viajan enteros en cada vuelta y no dependen del correo. El prompt de
    sistema tambien se reenvia, pero su tamaño si depende de cuantos campos
    queden pendientes, asi que se mide por caso.
    """
    import json

    from pipeline.agente import ESQUEMAS_HERRAMIENTAS

    return len(json.dumps(ESQUEMAS_HERRAMIENTAS, ensure_ascii=False))


def medir_nuevo(casos: List[Caso]) -> Medida:
    """
    Lo que cuesta UNA vuelta del agente, por correo que llegue a llamarlo.

    Una vuelta es la unidad real de consumo: el peso fijo mas la tarea. Cuantas
    vueltas da cada correo depende de lo que tenga que buscar, y no se sabe sin
    correr el modelo, asi que aqui no se supone ninguna: el informe muestra el
    rango entre una vuelta y el tope, y `leer_medido` da el numero real.
    """
    from pipeline.agente import catalogos_de, prompt_de_sistema
    from pipeline.modelo import ModeloVertex
    from servidor_mcp.herramientas import CATALOGOS

    # El agente unico corre entero en un solo modelo, el que trae el adaptador.
    modelo = ModeloVertex.__dataclass_fields__["nombre"].default
    esquemas = peso_de_los_esquemas()
    m = Medida(correos=len(casos))
    for caso in casos:
        e = extraer_todo(
            asunto=caso.asunto,
            cuerpo=caso.cuerpo_md,
            texto_adjuntos="\n".join(caso.adjuntos.values()),
            from_name=caso.remitente_nombre,
            from_address=caso.remitente_correo,
            sender_name=caso.sender_nombre,
            nombres_hilo=[(caso.remitente_nombre, caso.remitente_correo)],
            cuerpo_actual=caso.cuerpo_md,
        )
        if not e.campos_a_buscar:
            continue  # nada que preguntarle: no se le llama
        catalogos = catalogos_de(e.campos_a_buscar, CATALOGOS)
        vuelta = (
            len(prompt_de_sistema(e.campos_a_buscar))
            + esquemas
            + len(e.resumen_para_ia())
            + (len(catalogos) + 2 if catalogos else 0)
        )
        m.sumar("agente_unico", vuelta, modelo)
    return m


def _rango_de_vueltas(nuevo: Medida) -> List[str]:
    """
    Que cuesta un correo segun cuantas vueltas de el agente.

    Es lo que hay que mirar al tocar el prompt o el tope de vueltas: el peso
    fijo se paga entero en cada una, asi que las dos palancas se multiplican.
    """
    from pipeline.agente import MAX_ITERACIONES

    por_vuelta = nuevo.tokens_por_correo
    if not por_vuelta:
        return []
    lineas = [
        "",
        f"UN CORREO SEGUN LAS VUELTAS QUE DE   (tope actual: {MAX_ITERACIONES})",
        "-" * ANCHO,
        "  la vuelta reenvia el prompt y los esquemas de las herramientas, que",
        "  no cambian entre vueltas: el tope multiplica ese peso",
    ]
    for vueltas in sorted({1, 2, 3, 5, MAX_ITERACIONES}):
        if vueltas > MAX_ITERACIONES:
            continue
        lineas.append(f"  {vueltas:>2} vuelta(s){por_vuelta * vueltas:>15,} tokens")
    return lineas


def informe(viejo: Medida, nuevo: Medida, medido: Optional[Medido] = None) -> str:
    lineas = [
        f"CONSUMO POR CORREO   ({viejo.correos} casos reales)",
        "=" * ANCHO,
        f"{'':<22}{'VIEJO':>14}{'NUEVO':>14}{'CAMBIO':>16}",
        "-" * ANCHO,
    ]

    def fila(etiqueta, a, b, sufijo=""):
        if a:
            cambio = f"{(b - a) / a * 100:+.0f}%"
        else:
            cambio = "-"
        lineas.append(f"{etiqueta:<22}{a:>13,}{sufijo}{b:>13,}{sufijo}{cambio:>16}")

    fila("llamadas al modelo", round(viejo.llamadas_por_correo, 1),
         round(nuevo.llamadas_por_correo, 1))
    fila("tokens de entrada", viejo.tokens_por_correo, nuevo.tokens_por_correo)
    lineas.append(
        "  la columna NUEVO es UNA vuelta por correo: la unidad de consumo")
    costo_viejo, costo_nuevo = (viejo.costo_por_mil_correos,
                                nuevo.costo_por_mil_correos)
    if costo_viejo is not None and costo_nuevo is not None:
        cambio = (f"{(costo_nuevo - costo_viejo) / costo_viejo * 100:+.0f}%"
                  if costo_viejo else "-")
        lineas.append(
            f"{'USD / 1000 correos':<22}{costo_viejo:>13.2f} {costo_nuevo:>13.2f} "
            f"{cambio:>15}")
    else:
        lineas.append(
            f"{'USD / 1000 correos':<22}"
            "   no calculable: algun modelo no tiene tarifa")
    lineas += [
        "-" * ANCHO,
        "  el costo cobra cada agente a la tarifa de SU modelo: el viejo manda",
        "  dos al caro, que vale cuatro veces mas por token, asi que la caida de",
        "  tokens y la de dinero no son el mismo porcentaje. Solo entrada.",
        f"  estimacion a {CARACTERES_POR_TOKEN} caracteres por token; "
        f"la razon entre columnas no depende de esa constante",
    ]
    lineas += _rango_de_vueltas(nuevo)

    if medido is None:
        lineas.append("\n  Sin corridas reales registradas en logs/serie.tsv.")
    else:
        ahorro = (
            (medido.tokens_por_correo - viejo.tokens_por_correo)
            / viejo.tokens_por_correo * 100
        ) if viejo.tokens_por_correo else 0
        lineas += [
            "",
            f"CORRIDAS REGISTRADAS EN ESTA MAQUINA   ({medido.correos} correos "
            f"en {medido.corridas} corridas)",
            "-" * ANCHO,
            f"  tokens de entrada por correo   {medido.tokens_por_correo:>10,}",
            f"  contra el pipeline viejo       {ahorro:>10.0f}%",
        ]
        # El agente unico corre en un solo modelo, asi que lo medido se puede
        # tarifar directo. Es la cifra que hay que citar, no la estimada.
        modelo_nuevo = next(
            (d.get("modelo") for d in nuevo.detalle.values() if d.get("modelo")),
            "",
        )
        costo_medido = _costo_entrada(medido.tokens_por_correo * 1000,
                                     modelo_nuevo)
        if costo_medido is not None and costo_viejo:
            lineas += [
                f"  USD / 1000 correos             {costo_medido:>10.2f}"
                f"   contra {costo_viejo:.2f} del viejo",
                f"  ahorro en dinero               "
                f"{(costo_medido - costo_viejo) / costo_viejo * 100:>10.0f}%",
            ]
        lineas.append(
            "  Son corridas locales, con la version del prompt de ese momento:")
        lineas.append(
            "  no valen para describir produccion ni para citarlas fuera de aqui.")

    lineas += [
        "",
        "DONDE SE IBA EL TEXTO EN EL PIPELINE VIEJO",
        "-" * ANCHO,
    ]
    for etiqueta, d in sorted(
        viejo.detalle.items(), key=lambda kv: -kv[1]["caracteres"]
    ):
        tokens = d["caracteres"] // CARACTERES_POR_TOKEN
        porcentaje = d["caracteres"] * 100 // (viejo.caracteres or 1)
        lineas.append(
            f"  {etiqueta:<30}{tokens:>10,} tok{porcentaje:>6}%"
            f"   {d.get('modelo', '?')}"
        )
    lineas += [
        "",
        "  Cada agente recibia el correo entero para responder un solo campo.",
        "  El nuevo manda una tarea con lo que quedo pendiente y su motivo.",
    ]
    return "\n".join(lineas)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n", type=int, default=None)
    args = p.parse_args()

    casos = cargar_corpus(RAIZ_CORPUS)
    if not casos:
        print("Corpus vacio. Correr primero: python -m harness.recolectar")
        return 1
    if args.n:
        casos = casos[: args.n]
    print(informe(
        medir_viejo(casos), medir_nuevo(casos), leer_medido(SERIE)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
