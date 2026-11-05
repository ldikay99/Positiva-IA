"""
Recolector de casos reales del buzon hacia el corpus.

Lee correos reales, los anonimiza y los deja como casos del corpus.

SEGURIDAD
  - Solo LEE del buzon. No radica, no etiqueta, no mueve, no reenvia.
  - MODO_TEST se exige activo como cinturon adicional.
  - Ningun caso se escribe sin pasar la verificacion de PII: si algo se escapa
    del anonimizador, se descarta el caso en lugar de guardarlo.
  - El corpus vive fuera de git (ver .gitignore).

Uso:
    python -m harness.recolectar [cantidad] [--carpeta inbox|exitosa]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Tuple

from config import settings
from harness.anonimizar import Anonimizador, fugas_de_original
from harness.caso import Caso, Esperado
from services.outlook import OutlookService
from utils.html_cleaner import clean_html_content

RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"


def _a_markdown(texto: str, titulo: str) -> str:
    """
    Envuelve texto ya limpio como Markdown legible.

    Conversion minima: la conversion real de PDF, OCR y tablas la hace
    `pipeline.conversion`.
    """
    cuerpo = (texto or "").strip() or "_(sin contenido)_"
    return f"# {titulo}\n\n{cuerpo}\n"


def _clasificar_etiquetas(nombre: str, correo: str, cuerpo: str) -> List[str]:
    """
    Etiqueta el caso para poder filtrar subconjuntos del corpus, como los
    correos de buzon corporativo o los que traen adjuntos.
    """
    from utils.generic_filter import es_nombre_persona_valido, is_buzon_no_humano

    etiquetas: List[str] = []
    if not es_nombre_persona_valido(nombre):
        # Display name que no es un nombre de persona: suele ser la propia direccion.
        etiquetas.append("defecto-612")
        etiquetas.append("buzon-sin-display-name")
    if is_buzon_no_humano(nombre, correo):
        etiquetas.append("buzon-no-humano")
    if correo.lower().endswith(".gov.co"):
        etiquetas.append("entidad-publica")
    if "De:" in cuerpo or "From:" in cuerpo:
        etiquetas.append("reenvio")
    if len(cuerpo) < 200:
        etiquetas.append("cuerpo-corto")
    if len(cuerpo) > 5000:
        etiquetas.append("cuerpo-largo")
    return etiquetas or ["ordinario"]


async def _adjuntos_en_markdown(
    outlook: "OutlookService", email, anon: Anonimizador
) -> dict:
    """
    Descarga los adjuntos, los convierte a Markdown y los anonimiza.

    En los correos reales la cedula y la incapacidad vienen en el PDF, no en
    el cuerpo.
    """
    import base64

    from pipeline.conversion import convertir
    from pipeline.materializar import claves_candidatas_de, es_ruido

    if not email.has_attachments:
        return {}

    cuerpo = clean_html_content(email.body.content or "")
    claves = claves_candidatas_de(f"{email.subject}\n{cuerpo}")

    salida: dict = {}
    try:
        lista = await outlook.get_attachments(email.id)
    except Exception:
        return {}

    for a in lista:
        nombre = a.get("name") or "adjunto"
        if es_ruido(nombre):
            continue
        if a.get("isInline") and a.get("size", 0) < 20_000:
            continue
        try:
            b64 = await outlook.download_attachment(email.id, a["id"])
            datos = base64.b64decode(b64) if b64 else b""
        except Exception:
            continue
        if not datos:
            continue
        conv = convertir(datos, nombre, claves)
        if conv.error or conv.vacio:
            continue
        salida[nombre] = anon.texto(conv.markdown)
    return salida


async def recolectar(cantidad: int, carpeta_id: str, sal: str) -> Tuple[int, int]:
    """
    Devuelve (guardados, descartados).

    Descartados son los casos que no pasaron la verificacion de PII. Se cuentan
    y se reportan: un numero alto significa que el anonimizador tiene un hueco.
    """
    if not settings.MODO_TEST:
        raise RuntimeError("MODO_TEST no esta activo. Abortado por seguridad.")

    outlook = OutlookService()
    guardados = descartados = 0
    try:
        correos = await outlook.get_emails(carpeta_id, limit=cantidad)
        correos = correos[:cantidad]
        print(f"Correos leidos: {len(correos)}")

        for i, email in enumerate(correos, 1):
            anon = Anonimizador(sal=f"{sal}-{i:04d}")

            nombre = email.from_address.name if email.from_address else ""
            correo = email.from_address.address if email.from_address else ""
            sender = email.sender.name if email.sender else ""
            cuerpo = clean_html_content(email.body.content or "")

            etiquetas = _clasificar_etiquetas(nombre, correo, cuerpo)

            # Anonimizar. Los nombres se sustituyen aparte porque no hay patron
            # fiable para detectarlos dentro del texto.
            asunto_anon = anon.texto(email.subject or "")
            cuerpo_anon = anon.texto(cuerpo)
            nombres_conocidos = [n for n in (nombre, sender) if n]
            asunto_anon = anon.sustituir_nombres(asunto_anon, nombres_conocidos)
            cuerpo_anon = anon.sustituir_nombres(cuerpo_anon, nombres_conocidos)

            nombre_anon = (
                anon.nombre_persona(nombre)
                if nombre and "@" not in nombre
                else anon.correo(nombre) if nombre else ""
            )
            correo_anon = anon.correo(correo)
            sender_anon = anon.nombre_persona(sender) if sender else ""

            # Los adjuntos se materializan antes de la compuerta de PII, para
            # que su contenido tambien pase por ella.
            adjuntos_md = await _adjuntos_en_markdown(outlook, email, anon)
            if adjuntos_md:
                etiquetas.append("con-adjuntos")

            # Compuerta de PII: se comprueba que ningun valor ORIGINAL sobreviva,
            # incluyendo el contenido de los adjuntos.
            fugas = fugas_de_original(
                anon, asunto_anon, cuerpo_anon,
                nombre_anon, correo_anon, sender_anon,
                *adjuntos_md.values(),
            )
            if fugas:
                print(f"  [{i}] DESCARTADO, PII original sobrevivio: {fugas[:3]}")
                descartados += 1
                continue

            caso = Caso(
                id=f"caso-{i:04d}",
                asunto=asunto_anon,
                remitente_nombre=nombre_anon,
                remitente_correo=correo_anon,
                sender_nombre=sender_anon,
                cuerpo_md=_a_markdown(cuerpo_anon, "Cuerpo del correo"),
                adjuntos=adjuntos_md,
                esperado=Esperado(),  # sin etiquetar; se completa con harness.etiquetar
                motivo="recolectado del buzon real para medir la linea base",
                etiquetas=etiquetas,
            )
            caso.guardar(RAIZ_CORPUS)
            guardados += 1
            print(f"  [{i}] {caso.id}  etiquetas={','.join(etiquetas)}")
    finally:
        await outlook.close()

    return guardados, descartados


def main() -> int:
    p = argparse.ArgumentParser(description="Recolecta casos reales al corpus")
    p.add_argument("cantidad", nargs="?", type=int, default=25)
    p.add_argument("--carpeta", choices=("inbox", "exitosa"), default="exitosa",
                   help="inbox tiene pocos correos; exitosa tiene el historico")
    p.add_argument("--sal", default="corpus-pqrd")
    args = p.parse_args()

    carpeta_id = (
        settings.OUTLOOK_FOLDER_ID if args.carpeta == "inbox"
        else settings.OUTLOOK_FOLDER_RADICACION_EXITOSA
    )
    guardados, descartados = asyncio.run(
        recolectar(args.cantidad, carpeta_id, args.sal)
    )
    print(f"\nGuardados: {guardados}   Descartados por PII: {descartados}")
    print(f"Corpus en: {RAIZ_CORPUS}")
    if descartados:
        print("Revisar el anonimizador: hubo casos con PII detectable.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
