"""
Compara lo que reporto el agente contra las etiquetas del corpus.

    python -m harness.acierto_del_agente

Lee `workspace/pruebas/*/resolucion.json`, que deja `harness.probar_caso`.

  ACIERTA  reporto y coincide
  FALLA    reporto un valor distinto al de la etiqueta
  CALLA    no reporto un campo con etiqueta no vacia

Una etiqueta vacia (`""`) significa que el dato no esta en el correo: callarse
cuenta como acierto y reportar algo cuenta como fallo.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from harness.atribucion import coincide

RAIZ = Path(__file__).resolve().parent.parent
RAIZ_CORPUS = RAIZ / "corpus" / "casos"
RAIZ_PRUEBAS = RAIZ / "workspace" / "pruebas"

# Debajo de este valor la compuerta descarta el hallazgo y el campo se queda
# sin resolver. Un valor reportado por debajo no llega al payload.
from pipeline.compuerta import CONFIANZA_PARA_RADICAR


def _corridas() -> List[Tuple[str, dict]]:
    """Cada caso con corrida terminada, con su resolucion."""
    salida = []
    for directorio in sorted(RAIZ_PRUEBAS.glob("caso-*")):
        ruta = directorio / "resolucion.json"
        if not ruta.exists():
            continue
        try:
            salida.append((directorio.name, json.loads(ruta.read_text(encoding="utf-8"))))
        except json.JSONDecodeError as e:
            print(f"AVISO: {directorio.name}/resolucion.json ilegible: {e}")
    return salida


def _etiquetas_de(caso_id: str) -> Dict[str, object]:
    """
    Las etiquetas del caso. Vacio si el caso no existe en el corpus.

    Lee `esperado.json` directamente: cargar el `Caso` entero exigiria tambien
    el correo y los adjuntos, que aqui no hacen falta.
    """
    ruta = RAIZ_CORPUS / caso_id / "esperado.json"
    if not ruta.exists():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(
            f"{caso_id}/esperado.json no es JSON valido: {e}") from e
    if not isinstance(datos, dict):
        raise ValueError(
            f"{caso_id}/esperado.json tiene que ser un objeto, "
            f"no {type(datos).__name__}")
    return datos


def main() -> int:
    corridas = _corridas()
    if not corridas:
        print(f"no hay corridas terminadas en {RAIZ_PRUEBAS}\n"
              f"  Corre un caso: python -m harness.probar_caso <id>")
        return 1

    # campo -> [aciertos, fallos, callados, descartados_por_confianza]
    tabla: Dict[str, List[int]] = defaultdict(lambda: [0, 0, 0, 0])
    detalle_fallos: List[Tuple[str, str, object, object, float]] = []
    detalle_callados: List[Tuple[str, str, object]] = []

    for caso_id, resolucion in corridas:
        reportados = {
            h["campo"]: h for h in resolucion.get("hallazgos", [])
        }
        pendientes = set(resolucion.get("sin_resolver", {}))

        etiquetas = _etiquetas_de(caso_id)
        for campo in sorted(pendientes | set(reportados)):
            esperado = etiquetas.get(campo)
            if esperado is None:
                continue        # sin etiqueta no se puede juzgar
            h = reportados.get(campo)
            sin_valor = isinstance(esperado, str) and not esperado.strip()

            if h is None:
                if sin_valor:
                    tabla[campo][0] += 1     # el dato no existe: callarse acierta
                else:
                    tabla[campo][2] += 1
                    detalle_callados.append((caso_id, campo, esperado))
                continue

            confianza = float(h.get("confianza") or 0)
            if sin_valor:
                # La etiqueta dice que el dato no esta: reportarlo es inventarlo.
                tabla[campo][1] += 1
                detalle_fallos.append(
                    (caso_id, campo, h.get("valor"), "(no existe en el correo)", confianza))
            elif coincide(h.get("valor"), esperado, campo):
                tabla[campo][0] += 1
            else:
                tabla[campo][1] += 1
                detalle_fallos.append(
                    (caso_id, campo, h.get("valor"), esperado, confianza))
            if confianza < CONFIANZA_PARA_RADICAR:
                tabla[campo][3] += 1

    print(f"ACIERTO DEL AGENTE   ({len(corridas)} corridas con resolucion)")
    print("=" * 76)
    print(f"{'CAMPO':<28}{'ACIERTA':>8}{'FALLA':>7}{'CALLA':>7}"
          f"{'%':>6}   {'BAJO UMBRAL':>12}")
    print("-" * 76)

    t_ok = t_mal = t_calla = t_bajo = 0
    for campo, (ok, mal, calla, bajo) in sorted(tabla.items()):
        respondio = ok + mal
        pct = f"{100 * ok // respondio}%" if respondio else "-"
        print(f"{campo:<28}{ok:>8}{mal:>7}{calla:>7}{pct:>6}   {bajo:>12}")
        t_ok += ok
        t_mal += mal
        t_calla += calla
        t_bajo += bajo

    respondio = t_ok + t_mal
    print("-" * 76)
    pct = f"{100 * t_ok // respondio}%" if respondio else "-"
    print(f"{'TOTAL':<28}{t_ok:>8}{t_mal:>7}{t_calla:>7}{pct:>6}   {t_bajo:>12}")
    print()
    print(f"  De {respondio + t_calla} campos etiquetados que se le escalaron:")
    print(f"    respondio bien   {t_ok}")
    print(f"    respondio mal    {t_mal}    <- lo unico que llega al payload equivocado")
    print(f"    se callo         {t_calla}    <- va a revision manual, es correcto")
    print(f"  BAJO UMBRAL son los que reporto con confianza < "
          f"{CONFIANZA_PARA_RADICAR:.2f}: la compuerta los descarta.")

    if detalle_fallos:
        print()
        print("FALLOS")
        print("-" * 76)
        for caso_id, campo, obtenido, esperado, conf in detalle_fallos:
            marca = "  (la compuerta lo descarta)" if conf < CONFIANZA_PARA_RADICAR else ""
            print(f"  {campo:<26} {caso_id}")
            print(f"      respondio {obtenido!r}   esperaba {esperado!r}   "
                  f"confianza {conf:.2f}{marca}")

    if detalle_callados:
        print()
        print(f"SE CALLO ({len(detalle_callados)})")
        print("-" * 76)
        for caso_id, campo, esperado in detalle_callados:
            print(f"  {campo:<26} {caso_id}   habia etiqueta: {esperado!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
