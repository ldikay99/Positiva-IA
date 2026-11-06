"""
Runner LOCAL de validacion (MODO_TEST) para tutelas.
Procesa N correos del buzon con el orquestador real, pero como MODO_TEST=True
NO toca produccion: las escrituras se graban en salidas_prueba/ (radicaciones.csv,
escrituras.csv) en vez de radicar/etiquetar/mover/responder.

Uso:
    MODO_TEST=true python run_local_test.py [N]
"""
import sys
from datetime import datetime

from Tutelas.config import MODO_TEST, TEST_OUTPUT_DIR, GEMINI_CONFIG
from Tutelas.main import OrquestadorRadicacion


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    if not MODO_TEST:
        print("ABORTADO: MODO_TEST no esta activo. Este runner es solo para dry-run.")
        print("Corre con: MODO_TEST=true python run_local_test.py [N]")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"TUTELAS - MODO PRUEBA (dry-run). Nada se escribe en PRD.")
    print(f"Proyecto Gemini: {GEMINI_CONFIG.get('project_id')} | salidas -> {TEST_OUTPUT_DIR}/")
    print(f"Fecha/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    orquestador = OrquestadorRadicacion()
    try:
        orquestador.ejecutar(limite_correos=n)
    finally:
        orquestador.cerrar()

    print(f"\n{'='*60}")
    print(f"Listo. Revisa {TEST_OUTPUT_DIR}/radicaciones.csv y escrituras.csv")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
