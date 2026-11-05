"""
Medicion de la linea base del pipeline de produccion sobre el corpus.

    python -m harness.linea_base --gastar-modelo [--n 10]

CUESTA DINERO: corre los agentes de produccion contra Vertex, un correo tras
otro. Por eso el gasto se pide explicitamente y `--n` acota cuantos casos entran.
Para comparar el consumo viejo contra el nuevo SIN gastar, `harness.consumo`.

Mide, sobre el corpus:

  - cuantas llamadas al modelo hace por correo, y a que modelos
  - cuantos tokens gasta y cuanto cuesta
  - que campos resuelve el algoritmo sin necesidad de IA
  - cuanto del gasto se va en reenviar el mismo texto varias veces

No modifica el codigo de produccion: instrumenta el cliente de Gemini con un
envoltorio, corre los agentes tal como estan, y contabiliza. Asi la medicion
refleja el comportamiento real, no una reimplementacion.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
from typing import List, Optional

from harness.atribucion import Origen, ResultadoCaso, Tabla
from harness.caso import Caso, cargar_corpus
from harness.costo import Contador

RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"
RAIZ_INFORMES = Path(__file__).resolve().parent / "informes"


def instrumentar_gemini(contador: Contador):
    """
    Envuelve el cliente de google-genai para contabilizar cada llamada.

    Se parchea `generate_content` a nivel de la clase de modelos asincronos, que
    es el punto por el que pasan TODAS las llamadas del proyecto
    (`gemini.py` y `gemini_agents.py`).

    Devuelve una funcion para restaurar el estado original.
    """
    from google.genai import models as _models

    original = _models.AsyncModels.generate_content

    async def envuelto(self, *, model, contents, config=None, **kw):
        inicio = time.time()
        try:
            respuesta = await original(
                self, model=model, contents=contents, config=config, **kw
            )
        except Exception as e:
            contador.registrar(
                etiqueta=_etiqueta_actual or "desconocido",
                modelo=model,
                duracion_ms=int((time.time() - inicio) * 1000),
                error=f"{type(e).__name__}: {e}",
            )
            raise
        meta = getattr(respuesta, "usage_metadata", None)
        contador.registrar(
            etiqueta=_etiqueta_actual or "desconocido",
            modelo=model,
            tokens_entrada=getattr(meta, "prompt_token_count", 0) or 0,
            tokens_salida=getattr(meta, "candidates_token_count", 0) or 0,
            duracion_ms=int((time.time() - inicio) * 1000),
        )
        return respuesta

    _models.AsyncModels.generate_content = envuelto

    def restaurar():
        _models.AsyncModels.generate_content = original

    return restaurar


# Etiqueta del paso en curso, para atribuir el gasto a quien lo causa.
# Un global simple basta: la medicion corre secuencialmente por caso.
_etiqueta_actual: Optional[str] = None


def marcar(etiqueta: str) -> None:
    global _etiqueta_actual
    _etiqueta_actual = etiqueta


async def medir_caso(caso: Caso, agentes, tabla: Tabla) -> ResultadoCaso:
    """
    Corre la extraccion actual sobre un caso y registra atribucion.

    Se mide la etapa de extraccion, que es donde esta el gasto: el resto del
    pipeline (Outlook, SGDEA, Redis) no consume modelo y no depende del corpus.
    """
    from utils.contacto_extractor import mejor_telefono
    from utils.documento_candidatos import extraer_candidatos_documento
    from utils.generic_filter import extraer_nombre_remitente_real

    resultado = ResultadoCaso(caso.id)
    texto_adjuntos = "\n\n".join(caso.adjuntos.values())
    cuerpo = caso.cuerpo_md

    try:
        # ── Lo que el ALGORITMO puede resolver hoy, sin modelo ──
        marcar("determinista")
        candidatos = extraer_candidatos_documento(
            f"{caso.asunto}\n{cuerpo}\n{texto_adjuntos}"
        )
        if len(candidatos) == 1:
            resultado.registrar(
                "numero_documento_afectado", candidatos[0]["numero"],
                Origen.ALGORITMO, evidencia="candidato unico determinista",
                confianza=0.9,
            )
        telefono = mejor_telefono(f"{cuerpo}\n{texto_adjuntos}")
        if telefono:
            resultado.registrar("telefono_afectado", telefono, Origen.ALGORITMO,
                                evidencia="contacto_extractor", confianza=0.9)

        nombre_rem = extraer_nombre_remitente_real(
            caso.remitente_nombre, caso.remitente_correo, cuerpo,
            sender_name=caso.sender_nombre,
            nombres_hilo=[(caso.remitente_nombre, caso.remitente_correo)],
            cuerpo_email_actual=cuerpo,
        )
        if nombre_rem:
            resultado.registrar("nombre_remitente", nombre_rem, Origen.ALGORITMO,
                                evidencia="cascada de resolucion", confianza=0.9)

        # ── Lo que hoy se delega a la IA ──
        marcar("extract_all_data")
        ai = await agentes.extract_all_data(caso.asunto, cuerpo, texto_adjuntos)

        resultado.registrar("tipo_remitente", ai.tipo_remitente, Origen.IA)
        resultado.registrar("tipo_documento_afectado",
                            ai.tipo_documento_afectado, Origen.IA)
        resultado.registrar("tipo_solicitud", ai.tipo_solicitud, Origen.IA)
        resultado.registrar("tipo_documento_remitente",
                            ai.tipo_documento_remitente, Origen.IA)
        resultado.registrar("numero_documento_remitente",
                            ai.numero_documento_remitente, Origen.IA)
        if "numero_documento_afectado" not in resultado.resoluciones:
            resultado.registrar("numero_documento_afectado",
                                ai.numero_documento_afectado, Origen.IA)

        marcar("nombre_afectado")
        nombre_af = await agentes.extract_nombre_afectado(
            caso.asunto, cuerpo, texto_adjuntos
        )
        if nombre_af:
            resultado.registrar("nombre_afectado", nombre_af, Origen.IA)
        else:
            resultado.registrar("nombre_afectado", "", Origen.NO_RESUELTO,
                                motivo_no_resuelto="IA_NO_ENCONTRO_NOMBRE_AFECTADO")

        if not nombre_rem:
            marcar("nombre_remitente_ia")
            rem_ia = await agentes.extract_nombre_remitente(
                caso.asunto, cuerpo, texto_adjuntos
            )
            if rem_ia:
                resultado.registrar("nombre_remitente", rem_ia, Origen.IA)
            else:
                resultado.registrar(
                    "nombre_remitente", "", Origen.NO_RESUELTO,
                    motivo_no_resuelto="SIN_NOMBRE_EN_NINGUNA_FUENTE",
                )

        partes = (nombre_af or "").split()
        marcar("sexo_afectado")
        sexo = await agentes.extract_sexo_afectado(
            partes[0] if partes else "", partes[1] if len(partes) > 1 else ""
        )
        resultado.registrar("sexo_afectado", sexo, Origen.IA)

    except Exception as e:
        resultado.error = f"{type(e).__name__}: {e}"

    tabla.agregar(resultado, caso.esperado)
    return resultado


async def correr(limite: Optional[int] = None, etiqueta: Optional[str] = None):
    from services.gemini_agents import LangChainAgents

    casos = cargar_corpus(RAIZ_CORPUS, etiqueta=etiqueta)
    if limite:
        casos = casos[:limite]
    if not casos:
        print("Corpus vacio. Correr primero harness.recolectar")
        return None, None

    contador = Contador()
    contador.correos = len(casos)
    tabla = Tabla()
    restaurar = instrumentar_gemini(contador)
    agentes = LangChainAgents()
    try:
        for i, caso in enumerate(casos, 1):
            r = await medir_caso(caso, agentes, tabla)
            estado = "ERROR" if r.error else "ok"
            print(f"  [{i}/{len(casos)}] {caso.id}  {estado}"
                  + (f"  {r.error[:70]}" if r.error else ""))
    finally:
        restaurar()
        agentes.close()

    return tabla, contador


def escribir_informe(tabla: Tabla, contador: Contador, ruta: Path) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    contenido = "\n\n".join([
        "=" * 78,
        "LINEA BASE DEL PIPELINE ACTUAL",
        "=" * 78,
        tabla.informe(),
        contador.informe(),
        _observaciones(contador),
    ])
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


def _observaciones(contador: Contador) -> str:
    """Observaciones derivadas del contador."""
    lineas = ["OBSERVACIONES", "-" * 64]
    por_paso = contador.por_etiqueta()
    total_entrada = contador.total_entrada or 1
    if por_paso:
        mayor = max(por_paso.items(), key=lambda kv: kv[1]["entrada"])
        lineas.append(
            f"  El paso que mas tokens de entrada consume es '{mayor[0]}' "
            f"con {mayor[1]['entrada']:,} ({mayor[1]['entrada'] / total_entrada:.0%} del total)."
        )
    por_modelo = contador.por_modelo()
    if len(por_modelo) > 1:
        lineas.append(
            f"  Se usan {len(por_modelo)} modelos distintos: "
            + ", ".join(sorted(por_modelo)) + "."
        )
    lineas.append(
        f"  Promedio de {contador.llamadas_por_correo:.1f} llamadas al modelo por correo."
    )
    if contador.total_errores:
        lineas.append(
            f"  {contador.total_errores} llamadas fallaron durante la medicion."
        )
    return "\n".join(lineas)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n", type=int, default=None,
                   help="cuantos casos medir; sin esto, los 102")
    p.add_argument("--etiqueta", default=None,
                   help="medir solo los casos con esta etiqueta")
    p.add_argument("--gastar-modelo", action="store_true",
                   help="obligatorio: la medicion llama a Vertex y se paga")
    args = p.parse_args()

    if not args.gastar_modelo:
        print(
            "Esta medicion corre los agentes de produccion contra Vertex y se\n"
            "paga por token. Hay que pedirlo explicitamente:\n"
            "    python -m harness.linea_base --gastar-modelo --n 10\n"
            "\n"
            "Para comparar el consumo viejo contra el nuevo sin gastar:\n"
            "    python -m harness.consumo"
        )
        return 1

    tabla, contador = asyncio.run(correr(args.n, args.etiqueta))
    if tabla is None:
        return 1
    ruta = escribir_informe(tabla, contador, RAIZ_INFORMES / "linea_base.txt")
    print()
    print(tabla.informe())
    print()
    print(contador.informe())
    print(f"\nInforme escrito en {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
