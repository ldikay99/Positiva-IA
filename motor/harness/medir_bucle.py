"""
Valora a tarifa de Vertex los turnos que quedaron en workspace/pruebas/.

    python -m harness.medir_bucle

Los tokens se estiman por caracteres: los turnos guardados en disco no llevan
`usage_metadata`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

from harness.costo import TARIFAS_VIGENTES_AL, Contador

RAIZ_PRUEBAS = Path(__file__).resolve().parent.parent / "workspace" / "pruebas"

# Regla habitual para texto en español: un token por cada cuatro caracteres.
CARACTERES_POR_TOKEN = 4

# Los turnos se valoran con el modelo que usaria el pipeline nuevo en Vertex.
MODELO_DE_REFERENCIA = "gemini-2.5-flash"


def _turnos_del_caso(directorio: Path) -> Tuple[int, int, int]:
    """Turnos contestados y tokens estimados de entrada y salida."""
    modelo = directorio / "modelo"
    respuestas = sorted(modelo.glob("respuesta-*.json"))
    if not respuestas:
        return 0, 0, 0
    entrada = sum(
        len(p.read_text(encoding="utf-8")) // CARACTERES_POR_TOKEN
        for p in sorted(modelo.glob("peticion-*.json"))
    )
    salida = sum(
        len(r.read_text(encoding="utf-8")) // CARACTERES_POR_TOKEN
        for r in respuestas
    )
    return len(respuestas), entrada, salida


def main() -> int:
    if not RAIZ_PRUEBAS.exists():
        print(f"no hay corridas que medir: falta {RAIZ_PRUEBAS}\n"
              f"  Corre un caso primero: python -m harness.probar_caso <id>")
        return 1

    contador = Contador()
    filas: List[Tuple[str, int, int, int]] = []
    for directorio in sorted(RAIZ_PRUEBAS.glob("caso-*")):
        turnos, entrada, salida = _turnos_del_caso(directorio)
        if not turnos:
            continue
        filas.append((directorio.name, turnos, entrada, salida))
        contador.registrar(directorio.name, MODELO_DE_REFERENCIA, entrada, salida)
    contador.correos = len(filas)

    if not filas:
        print(f"ningun caso de {RAIZ_PRUEBAS} tiene turnos contestados.\n"
              f"  Un caso cuenta cuando su modelo/ tiene respuesta-NN.json.\n"
              f"  Corre uno: python -m harness.probar_caso <id>")
        return 1

    print(f"{'caso':<12}{'turnos':>7}{'tok_entrada':>13}{'tok_salida':>12}")
    for nombre, turnos, entrada, salida in filas:
        print(f"{nombre:<12}{turnos:>7}{entrada:>13,}{salida:>12,}")

    correos = contador.correos
    print("-" * 44)
    print(f"{'PROMEDIO':<12}{'':>7}"
          f"{contador.total_entrada // correos:>13,}"
          f"{contador.total_salida // correos:>12,}")

    costo_correo = contador.costo_por_correo
    print(f"\ncasos medidos: {correos}   (tarifas vigentes al {TARIFAS_VIGENTES_AL})")
    if costo_correo is None:
        print(f"costo no calculable: {MODELO_DE_REFERENCIA} no esta en la tabla "
              f"de tarifas de harness/costo.py")
        return 1
    print(f"costo del pipeline nuevo con Vertex: "
          f"USD {costo_correo * 1000:.2f} / 1000 correos")
    print("tokens estimados por caracteres, no medidos con usage_metadata")
    return 0


if __name__ == "__main__":
    sys.exit(main())
