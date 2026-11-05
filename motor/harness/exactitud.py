"""
Acierto de la capa determinista contra las etiquetas del corpus.

    python -m harness.exactitud            tabla y lista de fallos
    python -m harness.exactitud --campo nombre_afectado

Cobertura y acierto no son lo mismo: la cobertura cuenta cuantas veces el
algoritmo RESPONDE, y esta cuenta cuantas de esas respuestas son CORRECTAS.
Un campo puede resolverse siempre y estar mal la mitad de las veces.

Solo se comparan los campos etiquetados: un `esperado` en None significa que el
caso no afirma nada sobre ese campo y no cuenta ni a favor ni en contra. Sale
distinto de cero cuando hay algun fallo.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from harness.atribucion import coincide
from harness.caso import CAMPOS_EVALUABLES, cargar_corpus, extraer_de
from pipeline.extraccion import METODOS_POR_DEFECTO

RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"
ANCHO = 84

# La oficina y la accion las decide el pipeline completo, no la extraccion.
CAMPOS = tuple(c for c in CAMPOS_EVALUABLES if c not in ("accion", "oficina"))


@dataclass
class Fallo:
    """Una discrepancia concreta, con lo necesario para reproducirla."""

    caso_id: str
    campo: str
    obtenido: str
    esperado: str
    metodo: str
    evidencia: str


@dataclass
class Marca:
    """Conteos de un campo sobre el corpus."""

    resuelto: int = 0
    # De los resueltos, los que aplican un valor por ausencia de otro en vez de
    # leerlo del correo.
    supuesto: int = 0
    comparable: int = 0
    acierto: int = 0
    # El corpus dice que HAY un valor y el algoritmo no lo hallo: fallo de
    # cobertura, y la IA puede arreglarlo.
    escalado_con_valor: int = 0
    # El corpus dice que el campo va VACIO y el algoritmo lo escala igual:
    # nadie puede contestar esa pregunta, ni el modelo.
    escalado_sin_valor: int = 0

    @property
    def porcentaje(self) -> Optional[int]:
        if not self.comparable:
            return None
        return self.acierto * 100 // self.comparable


@dataclass
class Exactitud:
    """Resultado de la medicion sobre todo el corpus."""

    casos: int = 0
    por_campo: Dict[str, Marca] = field(default_factory=dict)
    fallos: List[Fallo] = field(default_factory=list)

    def medir(self, caso) -> None:
        e = extraer_de(caso)
        self.casos += 1
        for campo in CAMPOS:
            marca = self.por_campo.setdefault(campo, Marca())
            h = e.hallazgos.get(campo)
            if h is not None:
                marca.resuelto += 1
                if h.metodo in METODOS_POR_DEFECTO:
                    marca.supuesto += 1
            esperado = getattr(caso.esperado, campo, None)
            if esperado is None:
                continue
            if h is None:
                if esperado == "":
                    marca.escalado_sin_valor += 1
                else:
                    marca.escalado_con_valor += 1
                continue
            marca.comparable += 1
            if coincide(h.valor, esperado, campo):
                marca.acierto += 1
            else:
                self.fallos.append(Fallo(
                    caso_id=caso.id, campo=campo,
                    obtenido=str(h.valor), esperado=str(esperado),
                    metodo=h.metodo, evidencia=(h.evidencia or "")[:140]))

    def totales(self) -> Tuple[int, int]:
        return (sum(m.acierto for m in self.por_campo.values()),
                sum(m.comparable for m in self.por_campo.values()))

    def informe(self, campo: Optional[str] = None) -> str:
        lineas = [
            f"ACIERTO DE LA CAPA DETERMINISTA  ({self.casos} casos)",
            "=" * ANCHO,
            f"{'CAMPO':<28}{'RESUELVE':>9}{'SUPONE':>8}{'COMPARA':>8}"
            f"{'ACIERTA':>8}{'%':>6}{'ESCALADO':>10}",
            "-" * ANCHO,
        ]
        for nombre in CAMPOS:
            if campo and nombre != campo:
                continue
            m = self.por_campo.get(nombre, Marca())
            pct = "    -" if m.porcentaje is None else f"{m.porcentaje:>4}%"
            lineas.append(
                f"{nombre:<28}{m.resuelto:>9}{m.supuesto:>8}{m.comparable:>8}"
                f"{m.acierto:>8}{pct:>6}{m.escalado_con_valor:>10}")
        aciertos, comparables = self.totales()
        total = f"{aciertos * 100 // comparables:>4}%" if comparables else "    -"
        supuestos = sum(m.supuesto for m in self.por_campo.values())
        lineas += [
            "-" * ANCHO,
            f"{'TOTAL':<28}{'':>9}{supuestos:>8}{comparables:>8}"
            f"{aciertos:>8}{total:>6}",
            "",
            "SUPONE = de los resueltos, los que aplican un valor por ausencia de",
            "         otro en vez de leerlo. Cuentan como cobertura y no son un "
            "dato.",
            "ESCALADO = el corpus dice que HAY un valor y el algoritmo no lo "
            "hallo.",
            "Un campo sin etiquetas no se puede medir: aparece con '-'.",
        ]

        # Escalar un campo cuyo valor correcto es vacio no es falta de
        # cobertura: es una pregunta que nadie puede contestar, tampoco el
        # modelo.
        sin_nada = {n: m.escalado_sin_valor for n, m in self.por_campo.items()
                    if m.escalado_sin_valor and (not campo or n == campo)}
        if sin_nada:
            lineas += [
                "",
                "PREGUNTAS SIN RESPUESTA POSIBLE",
                "-" * ANCHO,
                "El corpus dice que el campo va vacio y el algoritmo lo escala "
                "igual.",
            ]
            for nombre, n in sorted(sin_nada.items(), key=lambda kv: -kv[1]):
                lineas.append(f"  {nombre:<40}{n:>5}")

        fallos = [f for f in self.fallos if not campo or f.campo == campo]
        lineas += ["", f"FALLOS ({len(fallos)})", "-" * ANCHO]
        if not fallos:
            lineas.append("  ninguno")
        for f in sorted(fallos, key=lambda f: (f.campo, f.caso_id)):
            lineas.append(f"  {f.campo:<26} {f.caso_id:<11} {f.metodo}")
            lineas.append(f"      obtuvo {f.obtenido!r}   esperaba {f.esperado!r}")
            if f.evidencia:
                lineas.append(f"      evidencia: {f.evidencia}")
        return "\n".join(lineas)


def correr(raiz: Path = RAIZ_CORPUS, etiqueta: Optional[str] = None) -> Optional[Exactitud]:
    casos = cargar_corpus(raiz, etiqueta=etiqueta)
    if not casos:
        return None
    resultado = Exactitud()
    for caso in casos:
        resultado.medir(caso)
    return resultado


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--campo", choices=CAMPOS, default=None,
                   help="limitar el informe a un campo")
    p.add_argument("--etiqueta", default=None,
                   help="medir solo los casos con esta etiqueta")
    args = p.parse_args()

    resultado = correr(etiqueta=args.etiqueta)
    if resultado is None:
        print("Corpus vacio. Correr primero: python -m harness.recolectar")
        return 1
    print(resultado.informe(campo=args.campo))
    fallos = [f for f in resultado.fallos if not args.campo or f.campo == args.campo]
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
