"""
Corre UN caso del corpus por la capa determinista y la compuerta, sin modelo.

    python -m harness.probar_caso --lista
    python -m harness.probar_caso <id_del_caso>

No llama a Vertex y por tanto no cuesta nada: sirve para ver, campo por campo,
que resuelve el algoritmo por si solo, que deja pendiente y con que motivo, y
que dictamen emite la compuerta con eso. Lo que el agente anadiria encima se
mide aparte, con `harness.acierto_del_agente`.

Deja en el workspace del caso `extraccion.json`, `resolucion.json` y
`validacion.json`, e imprime la comparacion contra `esperado.json`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.caso import Caso, cargar_corpus, extraer_de

RAIZ = Path(__file__).resolve().parent.parent
RAIZ_CORPUS = RAIZ / "corpus" / "casos"
RAIZ_PRUEBAS = RAIZ / "workspace" / "pruebas"


def listar() -> int:
    casos = cargar_corpus(RAIZ_CORPUS)
    if not casos:
        print("Corpus vacio.")
        return 1
    for c in casos:
        print(f"  {c.id:<40} {c.asunto[:60]}")
    print(f"\n{len(casos)} caso(s). Correr uno:")
    print("  python -m harness.probar_caso <id>")
    return 0


def _materializar_caso(caso: Caso):
    """Construye el workspace persistente del caso, como lo haria materializar."""
    from pipeline.workspace import ESTADO_OK, EntradaManifiesto, Workspace

    ws = Workspace.crear(
        id_correo=caso.id, base=RAIZ_PRUEBAS, temporal=False)
    ws.escribir_correo(caso.cuerpo_md)
    if caso.hilo_md:
        ws.escribir_hilo(caso.hilo_md)
    for i, (nombre, markdown) in enumerate(sorted(caso.adjuntos.items()), 1):
        ruta = ws.escribir_adjunto(i, nombre, markdown)
        ws.manifiesto.agregar(EntradaManifiesto(
            nombre_original=nombre,
            ruta_md=str(ruta),
            tipo_real="pdf",
            estado=ESTADO_OK,
            caracteres_md=len(markdown),
        ))
    ws.manifiesto.asunto = caso.asunto
    ws.manifiesto.remitente_nombre = caso.remitente_nombre
    ws.manifiesto.remitente_correo = caso.remitente_correo
    return ws


def correr(ruta_caso: Path) -> int:
    from pipeline.agente import Resolucion, combinar, atribucion
    from pipeline.compuerta import validar
    from pipeline.extraccion import extraer_oficina
    from servidor_mcp.herramientas import Contexto

    if not (ruta_caso / "caso.json").exists():
        ruta_caso = RAIZ_CORPUS / ruta_caso.name
    if not (ruta_caso / "caso.json").exists():
        print(f"no hay caso en {ruta_caso}. Ver los disponibles con --lista")
        return 1
    caso = Caso.cargar(ruta_caso)

    ws = _materializar_caso(caso)
    extraccion = extraer_de(caso)
    ws.escribir_json("extraccion.json", {
        campo: {
            "valor": h.valor, "evidencia": h.evidencia,
            "confianza": h.confianza, "metodo": h.metodo,
        }
        for campo, h in sorted(extraccion.hallazgos.items())
    })

    ctx = Contexto(workspace=ws)

    # Sin modelo la resolucion queda vacia: lo que se ve en el informe es lo
    # que resolvio el algoritmo, y los campos que aqui salen pendientes son
    # exactamente los que en produccion se le escalarian al agente.
    resolucion = Resolucion()

    ws.escribir_json("resolucion.json", {
        "hallazgos": [
            {"campo": b["campo"], "valor": b["valor"], "evidencia": b["evidencia"],
             "confianza": b["confianza"]}
            for b in ctx.hallazgos
        ],
        "sin_resolver": {c: n.motivo for c, n in resolucion.sin_resolver.items()},
        "descartados": resolucion.descartados,
        "avisos": resolucion.avisos,
        "tokens_entrada": resolucion.tokens_entrada,
    })

    hallazgos = combinar(extraccion, resolucion)
    atrib = atribucion(extraccion, resolucion)

    # Igual que el orquestador: la oficina sale de la tabla despues de
    # conocer el tipo de solicitud.
    if "oficina" not in hallazgos and "tipo_solicitud" in hallazgos:
        h_of, _ = extraer_oficina(hallazgos["tipo_solicitud"].valor)
        if h_of is not None:
            hallazgos["oficina"] = h_of
            atrib["oficina"] = "ALGORITMO"

    dictamen = validar(
        hallazgos, ctx.catalogos,
        motivos_no_resueltos={
            campo: nr.motivo for campo, nr in resolucion.sin_resolver.items()
        },
    )
    ws.escribir_json("validacion.json", {
        "veredicto": dictamen.veredicto.value,
        "motivo_manual": dictamen.motivo_manual,
        "avisos": [str(a) for a in dictamen.reparos],
    })
    ws.cerrar()

    _informe(caso, hallazgos, atrib, resolucion, dictamen)
    return 0


def _informe(caso, hallazgos, atrib, resolucion, dictamen) -> None:
    from harness.atribucion import Origen, coincide

    print("=" * 70)
    print(f"CASO   {caso.id}")
    print(f"       {caso.asunto[:80]}")
    print("-" * 70)
    print(f"{'CAMPO':<28}{'ESPERADO':<24}{'OBTENIDO':<24}OK?")
    print("-" * 70)
    aciertos = evaluados = 0
    for campo in caso.esperado.campos_etiquetados():
        esperado = getattr(caso.esperado, campo)
        h = hallazgos.get(campo)
        obtenido = h.valor if h else ""
        ok = coincide(obtenido, esperado, campo)
        evaluados += 1
        aciertos += bool(ok)
        marca = "si" if ok else "NO"
        origen = ""
        if h is not None and not ok:
            origen = f"  ({getattr(atrib.get(campo), 'value', atrib.get(campo, ''))})"
        print(f"{campo:<28}{str(esperado)[:23]:<24}"
              f"{str(obtenido)[:23]:<24}{marca}{origen}")

    sin_resolver = ", ".join(sorted(resolucion.sin_resolver)) or "(ninguno)"
    veredicto = dictamen.veredicto.value
    print("-" * 70)
    print(f"aciertos      : {aciertos}/{evaluados}")
    print(f"algoritmo/IA  : "
          f"{sum(1 for a in atrib.values() if a is Origen.ALGORITMO)}/"
          f"{sum(1 for a in atrib.values() if a is Origen.IA)}")
    print(f"sin resolver  : {sin_resolver}")
    print(f"compuerta     : {veredicto}" +
          (f" -> {dictamen.motivo_manual}" if dictamen.motivo_manual else ""))
    for aviso in resolucion.descartados + resolucion.avisos:
        print(f"aviso         : {aviso}")
    print("=" * 70)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lista", action="store_true",
                   help="muestra los casos disponibles")
    p.add_argument("caso", nargs="?", default=None,
                   help="id o ruta del directorio del caso")
    args = p.parse_args()

    if args.lista or not args.caso:
        return listar()
    ruta = Path(args.caso)
    if not ruta.is_absolute() and not (ruta / "caso.json").exists():
        ruta = RAIZ_CORPUS / args.caso
    return correr(ruta)


if __name__ == "__main__":
    sys.exit(main())
