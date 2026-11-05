"""
Compuerta de regresion: compara la tasa de resolucion contra una referencia.

    python -m harness.regresion            informa y sale 1 si hay regresion
    python -m harness.regresion --fijar    guarda la medicion actual

Guarda una instantanea por campo y señala los que bajan mas de la tolerancia.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REFERENCIA = Path(__file__).resolve().parent / "referencia.json"
RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"

# Margen tolerado a la baja, en puntos porcentuales. El corpus cambia cuando se
# recolecta mas, asi que una variacion pequeña no es una regresion.
TOLERANCIA_PP = 3


@dataclass
class Instantanea:
    """Tasa de resolucion determinista por campo, en porcentaje."""

    casos: int
    por_campo: Dict[str, int] = field(default_factory=dict)
    total: int = 0

    def a_dict(self) -> Dict:
        return {"casos": self.casos, "total": self.total, "por_campo": self.por_campo}

    @classmethod
    def de_dict(cls, datos: Dict) -> "Instantanea":
        return cls(
            casos=datos.get("casos", 0),
            total=datos.get("total", 0),
            por_campo=dict(datos.get("por_campo", {})),
        )


def medir(casos) -> Instantanea:
    """Corre la extraccion determinista sobre los casos y calcula las tasas."""
    from harness.caso import extraer_de

    resueltos: Dict[str, int] = {}
    evaluados: Dict[str, int] = {}

    for caso in casos:
        e = extraer_de(caso)
        for campo in e.hallazgos:
            resueltos[campo] = resueltos.get(campo, 0) + 1
            evaluados[campo] = evaluados.get(campo, 0) + 1
        for campo in e.no_resueltos:
            evaluados[campo] = evaluados.get(campo, 0) + 1

    por_campo = {
        campo: resueltos.get(campo, 0) * 100 // n
        for campo, n in evaluados.items() if n
    }
    tot_r = sum(resueltos.values())
    tot_e = sum(evaluados.values()) or 1
    return Instantanea(
        casos=len(casos), por_campo=por_campo, total=tot_r * 100 // tot_e
    )


def guardar(instantanea: Instantanea, ruta: Path = REFERENCIA) -> Path:
    ruta.write_text(
        json.dumps(instantanea.a_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ruta


def cargar(ruta: Path = REFERENCIA) -> Optional[Instantanea]:
    if not Path(ruta).exists():
        return None
    return Instantanea.de_dict(json.loads(Path(ruta).read_text(encoding="utf-8")))


def comparar(
    actual: Instantanea,
    referencia: Instantanea,
    tolerancia: int = TOLERANCIA_PP,
) -> Tuple[List[str], List[str]]:
    """
    Devuelve (regresiones, mejoras) como lineas legibles.

    Un campo que desaparece cuenta como regresion: significa que el extractor
    dejo de evaluarlo.
    """
    regresiones: List[str] = []
    mejoras: List[str] = []

    for campo, antes in sorted(referencia.por_campo.items()):
        if campo not in actual.por_campo:
            regresiones.append(f"{campo}: ya no se evalua (referencia {antes}%)")
            continue
        ahora = actual.por_campo[campo]
        delta = ahora - antes
        if delta < -tolerancia:
            regresiones.append(f"{campo}: {antes}% -> {ahora}% ({delta:+d} pp)")
        elif delta > tolerancia:
            mejoras.append(f"{campo}: {antes}% -> {ahora}% ({delta:+d} pp)")

    nuevos = set(actual.por_campo) - set(referencia.por_campo)
    for campo in sorted(nuevos):
        mejoras.append(f"{campo}: nuevo, {actual.por_campo[campo]}%")

    # El total se compara solo sobre los campos presentes en ambas
    # instantaneas: un campo nuevo bajaria el promedio sin que se pierda nada.
    antes, ahora = _total_comparable(actual, referencia)
    if antes is not None and ahora - antes < -tolerancia:
        regresiones.append(
            f"TOTAL (campos comparables): {antes}% -> {ahora}% "
            f"({ahora - antes:+d} pp)"
        )
    return regresiones, mejoras


def _total_comparable(actual: Instantanea, referencia: Instantanea):
    """
    Promedio sobre los campos comunes a las dos instantaneas.

    Cada campo se evalua una vez por caso, asi que todos tienen el mismo
    denominador y el promedio simple de los porcentajes equivale al total
    ponderado.
    """
    comunes = sorted(set(actual.por_campo) & set(referencia.por_campo))
    if not comunes:
        return None, None
    return (
        sum(referencia.por_campo[c] for c in comunes) // len(comunes),
        sum(actual.por_campo[c] for c in comunes) // len(comunes),
    )


def informe(actual: Instantanea, referencia: Optional[Instantanea]) -> str:
    lineas = [
        f"RESOLUCION DETERMINISTA  ({actual.casos} casos)",
        "-" * 56,
    ]
    for campo, pct in sorted(actual.por_campo.items()):
        ref = referencia.por_campo.get(campo) if referencia else None
        comparacion = f"   (referencia {ref}%)" if ref is not None else ""
        lineas.append(f"  {campo:<30}{pct:>4}%{comparacion}")
    lineas.append("-" * 56)
    lineas.append(f"  {'TOTAL':<30}{actual.total:>4}%")

    if referencia is None:
        lineas.append("\nSin referencia guardada. Usar `guardar` para fijarla.")
        return "\n".join(lineas)

    regresiones, mejoras = comparar(actual, referencia)
    if regresiones:
        lineas.append("\nREGRESIONES")
        lineas += [f"  {r}" for r in regresiones]
    if mejoras:
        lineas.append("\nMEJORAS")
        lineas += [f"  {m}" for m in mejoras]
    if not regresiones and not mejoras:
        lineas.append("\nSin cambios significativos.")
    return "\n".join(lineas)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fijar", action="store_true",
                   help="guardar la medicion actual como referencia")
    args = p.parse_args()

    from harness.caso import cargar_corpus

    casos = cargar_corpus(RAIZ_CORPUS)
    if not casos:
        print("Corpus vacio. Correr primero: python -m harness.recolectar")
        return 1

    actual = medir(casos)
    if args.fijar:
        print(informe(actual, cargar()))
        print(f"\nReferencia guardada en {guardar(actual)}")
        return 0

    referencia = cargar()
    print(informe(actual, referencia))
    if referencia is None:
        return 1
    regresiones, _ = comparar(actual, referencia)
    return 1 if regresiones else 0


if __name__ == "__main__":
    raise SystemExit(main())
