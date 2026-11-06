"""
Punto de entrada para Cloud Run Job
Radicación automática de tutelas - Positiva ARL
"""
import sys
from datetime import datetime

from Tutelas.main import OrquestadorRadicacion


def main():
    print(f"\n{'='*60}")
    print(f"RADICACIÓN TUTELAS - CLOUD RUN JOB")
    print(f"Fecha/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    try:
        orquestador = OrquestadorRadicacion()
        orquestador.ejecutar(limite_correos=10)
        orquestador.cerrar()

        print(f"\n{'='*60}")
        print(f"✓ EJECUCIÓN COMPLETADA")
        print(f"{'='*60}\n")

    except Exception as e:
        print(f"\n{'='*60}")
        print(f"✗ ERROR EN LA EJECUCIÓN")
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"{'='*60}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
