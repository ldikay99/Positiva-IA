"""Reexporta la contabilidad de consumo, que vive con el pipeline."""
from pipeline.costo import (  # noqa: F401
    TARIFAS_USD_POR_MILLON,
    TARIFAS_VIGENTES_AL,
    Contador,
    Llamada,
)


if __name__ == "__main__":
    # `Contador` lo instancia el orquestador y se llena durante una corrida:
    # ejecutar este modulo no mide nada. Sin este aviso, salir en silencio no
    # se distingue de una herramienta rota.
    raise SystemExit(
        "harness.costo no es una herramienta: es la contabilidad que el "
        "orquestador va llenando durante una corrida.\n"
        "  Para comparar el consumo viejo contra el nuevo sin gastar modelo:\n"
        "    python -m harness.consumo\n"
        "  Para el consumo real de las corridas registradas: logs/serie.tsv"
    )
