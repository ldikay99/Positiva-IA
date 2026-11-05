#!/usr/bin/env python3
"""
Runner LOCAL de validacion (MODO_TEST). Procesa N correos del buzon (default 1)
con el orquestador real, pero como MODO_TEST=True NO toca PRD: las escrituras se
graban en salidas_prueba/ (radicaciones.csv, escrituras.csv).

Uso:
    ./venv/bin/python run_local_test.py [N]
"""
import asyncio
import sys

from rich.console import Console

from config import settings
from orchestrator import PqrdOrchestrator
from services.outlook import OutlookService

console = Console()


async def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    if not settings.MODO_TEST:
        console.print("[red]ABORTADO: MODO_TEST no esta activo. Este runner es solo para dry-run.[/red]")
        return 1

    console.print(f"[cyan]MODO_TEST activo. Procesando {n} correo(s). Nada se escribe en PRD.[/cyan]")
    console.print(f"[cyan]GCP_PROJECT={settings.GCP_PROJECT} | salidas -> {settings.TEST_OUTPUT_DIR}/[/cyan]\n")

    outlook = OutlookService()
    orchestrator = PqrdOrchestrator(verbose=True)
    try:
        emails = await outlook.get_emails(settings.OUTLOOK_FOLDER_ID, n)
        console.print(f"Correos traidos del buzon: {len(emails)}")
        for i, email in enumerate(emails[:n], 1):
            console.print(f"\n[bold]===== Correo {i}/{min(n, len(emails))} =====[/bold]")
            result = await orchestrator.process_single_email(email.id)
            console.print(
                f"[green]Resultado:[/green] accion={result.action} "
                f"radicado={result.radicado or '-'} error={result.error or '-'}"
            )
    finally:
        await orchestrator.close()
        if hasattr(outlook, "close"):
            await outlook.close()
    console.print("\n[bold green]Listo. Revisa salidas_prueba/radicaciones.csv[/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
