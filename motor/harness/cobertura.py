"""
Cuanto resuelve el algoritmo y cuanto queda para la IA, sobre el corpus real.

    python -m harness.cobertura

No usa red ni modelo: mide solo la capa determinista. La columna ALGORITMO
cuenta los campos que resuelve, y A LA IA los que escala. La tabla final
agrupa los motivos por los que un campo se escala.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import List, Optional

from harness.caso import Caso, cargar_corpus, extraer_de

RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"
ANCHO = 76


class Cobertura:
    """Conteos por campo, metodo y motivo."""

    def __init__(self) -> None:
        self.casos = 0
        self.resueltos: Counter = Counter()
        self.pendientes: Counter = Counter()
        self.motivos: Counter = Counter()
        self.metodos: Counter = Counter()
        self.confianzas: List[float] = []

    def medir(self, caso: Caso) -> None:
        e = extraer_de(caso)
        self.casos += 1
        for h in e.hallazgos.values():
            self.resueltos[h.campo] += 1
            self.metodos[h.metodo] += 1
            self.confianzas.append(h.confianza)
        for campo, nr in e.no_resueltos.items():
            self.pendientes[campo] += 1
            self.motivos[f"{campo}: {nr.motivo}"] += 1

    @property
    def total_algoritmo(self) -> int:
        return sum(self.resueltos.values())

    @property
    def total_ia(self) -> int:
        return sum(self.pendientes.values())

    def porcentaje_sin_ia(self) -> int:
        gran = self.total_algoritmo + self.total_ia
        return self.total_algoritmo * 100 // gran if gran else 0

    def informe(self) -> str:
        lineas = [
            f"Casos reales evaluados: {self.casos}",
            "",
            "=" * ANCHO,
            f"{'CAMPO':<30}{'ALGORITMO':>14}{'A LA IA':>14}{'% SIN IA':>12}",
            "=" * ANCHO,
        ]
        for campo in sorted(set(self.resueltos) | set(self.pendientes)):
            a = self.resueltos.get(campo, 0)
            i = self.pendientes.get(campo, 0)
            t = a + i or 1
            lineas.append(
                f"{campo:<30}{a:>8} {a * 100 // t:>4}%"
                f"{i:>9} {i * 100 // t:>4}%{a * 100 // t:>11}%"
            )
        gran = self.total_algoritmo + self.total_ia or 1
        lineas += [
            "-" * ANCHO,
            f"{'TOTAL':<30}{self.total_algoritmo:>8} "
            f"{self.total_algoritmo * 100 // gran:>4}%"
            f"{self.total_ia:>9} {self.total_ia * 100 // gran:>4}%"
            f"{self.porcentaje_sin_ia():>11}%",
            "",
            f"Campos evaluados por correo: {gran / (self.casos or 1):.1f}",
        ]
        if self.confianzas:
            lineas.append(
                "Confianza media de lo resuelto: "
                f"{sum(self.confianzas) / len(self.confianzas):.2f}"
            )
        lineas += ["", "COMO LO RESOLVIO EL ALGORITMO", "-" * ANCHO]
        for metodo, n in self.metodos.most_common():
            lineas.append(f"  {metodo:<34}{n:>5}")
        lineas += ["", "POR QUE SE ESCALO A LA IA", "-" * ANCHO]
        for motivo, n in self.motivos.most_common():
            lineas.append(f"  {motivo:<62}{n:>5}")
        return "\n".join(lineas)


def correr(limite: Optional[int] = None, raiz: Path = RAIZ_CORPUS) -> Optional[Cobertura]:
    casos = cargar_corpus(raiz)
    if not casos:
        return None
    if limite:
        casos = casos[:limite]
    c = Cobertura()
    for caso in casos:
        c.medir(caso)
    return c


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n", type=int, default=None, help="limitar numero de casos")
    args = p.parse_args()

    c = correr(limite=args.n)
    if c is None:
        print("Corpus vacio. Correr primero: python -m harness.recolectar")
        return 1
    print(c.informe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
