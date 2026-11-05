"""
Atribucion y exactitud por campo.

Por cada campo: quien lo resolvio (algoritmo, IA o nadie) y si lo resolvio bien.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pipeline.atribucion import Origen


@dataclass
class Resolucion:
    """Como quedo un campo: valor, quien lo puso, con que evidencia."""

    campo: str
    valor: Any
    origen: Origen
    evidencia: str = ""
    confianza: float = 0.0
    motivo_no_resuelto: str = ""


def _normaliza(v: Any) -> str:
    """
    Normaliza para comparar: sin acentos, minusculas, espacios colapsados.

    Necesario porque el corpus etiqueta 'José Ramírez Peña' y el pipeline puede
    producir 'JOSE RAMIREZ PENA'.
    """
    if v is None:
        return ""
    s = str(v).strip()
    n = unicodedata.normalize("NFD", s.lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return " ".join(n.split())


def _mismas_palabras(uno: str, otro: str) -> bool:
    """Igualdad como conjunto de palabras, sin importar el orden."""
    a, b = _normaliza(uno).split(), _normaliza(otro).split()
    return bool(a) and sorted(a) == sorted(b)


def coincide(obtenido: Any, esperado: Any, campo: str = "") -> bool:
    """
    Compara un valor obtenido con el esperado.

    Los numeros se comparan como numeros (18 == "18") y los textos
    normalizados. Un nombre se acepta ademas en otro orden: la historia
    clinica escribe "SOTO CANO EDUARDO ANDRES" y la firma "Eduardo Andres
    Soto Cano", y son la misma persona.
    """
    if esperado is None:
        return True  # campo no evaluado
    if isinstance(esperado, int) and not isinstance(esperado, bool):
        try:
            return int(str(obtenido).strip()) == esperado
        except (TypeError, ValueError):
            return False
    if _normaliza(obtenido) == _normaliza(esperado):
        return True
    return campo.startswith("nombre_") and _mismas_palabras(obtenido, esperado)


@dataclass
class ResultadoCaso:
    """Lo que produjo el pipeline para un caso, campo por campo."""

    caso_id: str
    resoluciones: Dict[str, Resolucion] = field(default_factory=dict)
    error: str = ""

    def registrar(
        self,
        campo: str,
        valor: Any,
        origen: Origen,
        evidencia: str = "",
        confianza: float = 0.0,
        motivo_no_resuelto: str = "",
    ) -> None:
        self.resoluciones[campo] = Resolucion(
            campo, valor, origen, evidencia, confianza, motivo_no_resuelto
        )


@dataclass
class Tabla:
    """
    Agregado sobre varios casos. Es lo que se imprime en el informe y lo que
    se compara contra la linea base.
    """

    # campo -> origen -> cantidad
    por_campo: Dict[str, Dict[Origen, int]] = field(default_factory=dict)
    # campo -> (aciertos, evaluados)
    exactitud: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    motivos_no_resuelto: Dict[str, int] = field(default_factory=dict)
    casos: int = 0
    casos_con_error: int = 0

    def agregar(self, resultado: ResultadoCaso, esperado) -> None:
        self.casos += 1
        if resultado.error:
            self.casos_con_error += 1
        esperado_d = {
            k: getattr(esperado, k) for k in vars(esperado) if not k.startswith("_")
        }
        for campo, res in resultado.resoluciones.items():
            origenes = self.por_campo.setdefault(
                campo, {o: 0 for o in Origen}
            )
            origenes[res.origen] += 1
            if res.origen is Origen.NO_RESUELTO and res.motivo_no_resuelto:
                self.motivos_no_resuelto[res.motivo_no_resuelto] = (
                    self.motivos_no_resuelto.get(res.motivo_no_resuelto, 0) + 1
                )
            valor_esperado = esperado_d.get(campo)
            if valor_esperado is None:
                continue  # no etiquetado, no cuenta para exactitud
            aciertos, evaluados = self.exactitud.get(campo, (0, 0))
            self.exactitud[campo] = (
                aciertos + (1 if coincide(res.valor, valor_esperado, campo) else 0),
                evaluados + 1,
            )

    # ── Totales ──

    def totales_por_origen(self) -> Dict[Origen, int]:
        totales = {o: 0 for o in Origen}
        for origenes in self.por_campo.values():
            for o, n in origenes.items():
                totales[o] += n
        return totales

    def exactitud_global(self) -> Tuple[int, int]:
        aciertos = sum(a for a, _ in self.exactitud.values())
        evaluados = sum(e for _, e in self.exactitud.values())
        return aciertos, evaluados

    def informe(self) -> str:
        def pct(n: int, total: int) -> str:
            return f"{(n / total * 100):5.1f}%" if total else "    - "

        lineas = [
            "ATRIBUCION Y EXACTITUD POR CAMPO",
            "-" * 78,
            f"  casos evaluados {self.casos}"
            + (f"   con error: {self.casos_con_error}" if self.casos_con_error else ""),
            "",
            f"  {'campo':<28}{'algoritmo':>13}{'IA':>13}{'manual':>13}{'exactitud':>11}",
        ]
        for campo in sorted(self.por_campo):
            o = self.por_campo[campo]
            total = sum(o.values())
            aciertos, evaluados = self.exactitud.get(campo, (0, 0))
            exact = pct(aciertos, evaluados) if evaluados else "   n/e"
            lineas.append(
                f"  {campo:<28}"
                f"{o[Origen.ALGORITMO]:>5} {pct(o[Origen.ALGORITMO], total)}"
                f"{o[Origen.IA]:>5} {pct(o[Origen.IA], total)}"
                f"{o[Origen.NO_RESUELTO]:>5} {pct(o[Origen.NO_RESUELTO], total)}"
                f"{exact:>11}"
            )
        totales = self.totales_por_origen()
        gran_total = sum(totales.values())
        aciertos, evaluados = self.exactitud_global()
        lineas += [
            "-" * 78,
            f"  {'TOTAL':<28}"
            f"{totales[Origen.ALGORITMO]:>5} {pct(totales[Origen.ALGORITMO], gran_total)}"
            f"{totales[Origen.IA]:>5} {pct(totales[Origen.IA], gran_total)}"
            f"{totales[Origen.NO_RESUELTO]:>5} {pct(totales[Origen.NO_RESUELTO], gran_total)}"
            f"{pct(aciertos, evaluados):>11}",
        ]
        if self.motivos_no_resuelto:
            lineas += ["", "  MOTIVOS DE NO RESOLUCION"]
            for motivo, n in sorted(
                self.motivos_no_resuelto.items(), key=lambda kv: -kv[1]
            ):
                lineas.append(f"    {motivo:<40} {n:>4}")
        return "\n".join(lineas)


if __name__ == "__main__":
    # `Tabla` separa lo que resolvio el algoritmo de lo que resolvio la IA, y
    # eso solo se puede saber con el modelo en el circuito: el orquestador la
    # alimenta corrida a corrida. Sin modelo la columna de IA seria cero y el
    # informe repetiria lo que ya da `harness.exactitud`.
    raise SystemExit(
        "harness.atribucion no es una herramienta: es la tabla que separa lo "
        "resuelto por el algoritmo de lo resuelto por la IA, y la llena el "
        "orquestador durante una corrida con modelo.\n"
        "  Para el acierto de la capa determinista sobre el corpus:\n"
        "    python -m harness.exactitud\n"
        "  Para lo que de verdad llega a SGDEA:\n"
        "    python -m harness.enviado"
    )
