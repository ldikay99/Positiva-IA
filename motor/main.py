#!/usr/bin/env python3
"""
Punto de entrada para radicacion automatica de PQRD - Positiva ARL

Procesa un lote de hasta CORREOS_POR_LOTE correos por ejecucion y termina.
Pensado para Cloud Run Job disparado por Cloud Scheduler. La cadencia la
controla el scheduler.
"""

import asyncio
import logging
import sys
import traceback
from datetime import datetime
from typing import Iterable, List

from rich.console import Console

from config import settings
from models.email import EmailMessage
from orchestrator import PqrdOrchestrator
from services.outlook import OutlookService

console = Console()
logger = logging.getLogger(__name__)

CORREOS_POR_LOTE = 20
PARALELISMO = 3
TIMEOUT_POR_CORREO_S = 240  # 4 min, tope individual antes de cancelar la tarea
OUTLOOK_FOLDER = settings.OUTLOOK_FOLDER_ID


async def procesar_correo(
    orchestrator: PqrdOrchestrator,
    email_id: str,
    indice: int,
    total: int,
) -> str:
    """Procesa un correo individual. Retorna la accion resultante."""
    try:
        result = await orchestrator.process_single_email(email_id)
        return result.action
    except asyncio.TimeoutError:
        console.print(f"[red]Timeout correo {indice}/{total} ({TIMEOUT_POR_CORREO_S}s)[/red]")
        return "fallido"
    except Exception as e:
        console.print(f"[red]Error correo {indice}/{total}: {e}[/red]")
        return "fallido"


async def procesar_en_paralelo(
    orchestrator: PqrdOrchestrator,
    emails: List[EmailMessage],
    paralelismo: int,
) -> dict:
    """Procesa emails con concurrencia limitada por semaforo y timeout por tarea."""
    contadores = {"radicado": 0, "duplicado": 0, "reenviado": 0, "manual": 0, "fallido": 0}
    semaforo = asyncio.Semaphore(paralelismo)
    total = len(emails)

    async def _run(idx: int, email: EmailMessage) -> str:
        async with semaforo:
            try:
                return await asyncio.wait_for(
                    procesar_correo(orchestrator, email.id, idx, total),
                    timeout=TIMEOUT_POR_CORREO_S,
                )
            except asyncio.TimeoutError:
                console.print(
                    f"[red]Timeout {idx}/{total} ({TIMEOUT_POR_CORREO_S}s) — saltando[/red]"
                )
                return "fallido"

    tareas = [_run(i + 1, em) for i, em in enumerate(emails)]
    acciones = await asyncio.gather(*tareas, return_exceptions=False)

    for accion in acciones:
        if accion in contadores:
            contadores[accion] += 1
        else:
            contadores["fallido"] += 1
    return contadores


def deduplicar_por_hilo(emails: Iterable[EmailMessage]) -> List[EmailMessage]:
    """Deduplica por conversationId. Correos sin conversationId no se deduplican
    entre si (cada uno se procesa). Si hay duplicados del mismo hilo, gana el mas
    reciente por received_datetime."""
    seen_convs: dict = {}
    for em in emails:
        conv = em.conversation_id
        if not conv:
            # Sin conversationId, indexar por id para no deduplicar entre si
            seen_convs[em.id] = em
            continue
        existing = seen_convs.get(conv)
        if existing is None:
            seen_convs[conv] = em
            continue
        if em.received_datetime and existing.received_datetime and em.received_datetime > existing.received_datetime:
            seen_convs[conv] = em
    return list(seen_convs.values())


async def main() -> int:
    console.print(f"\n{'=' * 60}")
    console.print("RADICACION PQRD - CLOUD RUN JOB")
    console.print(f"Fecha/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    console.print(
        f"Una tanda por invocacion - limite: {CORREOS_POR_LOTE} correos | "
        f"Paralelismo: {PARALELISMO} | Timeout/correo: {TIMEOUT_POR_CORREO_S}s"
    )
    console.print(f"{'=' * 60}\n")

    contadores = {"radicado": 0, "duplicado": 0, "reenviado": 0, "manual": 0, "fallido": 0}

    outlook = OutlookService()
    orchestrator = PqrdOrchestrator(verbose=True)

    try:
        emails = await outlook.get_emails(OUTLOOK_FOLDER, CORREOS_POR_LOTE)
        console.print(f"Correos encontrados: {len(emails)}")

        if not emails:
            console.print("[green]Bandeja vacia. Sin correos a procesar.[/green]")
            return 0

        unique_emails = deduplicar_por_hilo(emails)
        if len(unique_emails) < len(emails):
            console.print(
                f"Deduplicado por hilo: {len(emails)} -> {len(unique_emails)} correos unicos"
            )

        contadores = await procesar_en_paralelo(
            orchestrator, unique_emails, PARALELISMO
        )

    except Exception as e:
        console.print(f"[red]Error procesando lote: {e}[/red]")
        traceback.print_exc()
        logger.exception("Error procesando lote")
        return 1
    finally:
        await orchestrator.close()

    total = sum(contadores.values())
    console.print(f"\n{'=' * 60}")
    console.print("FINALIZADO")
    console.print(f"  Total:      {total}")
    console.print(f"  Radicados:  {contadores['radicado']}")
    console.print(f"  Duplicados: {contadores['duplicado']}")
    console.print(f"  Reenviados: {contadores['reenviado']}")
    console.print(f"  Manual:     {contadores['manual']}")
    console.print(f"  Fallidos:   {contadores['fallido']}")
    console.print(f"{'=' * 60}\n")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Detenido por el usuario.[/yellow]")
        rc = 0
    sys.exit(rc)
