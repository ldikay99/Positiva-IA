"""
Apoyo para etiquetar el corpus a mano.

    python -m harness.etiquetar --ver caso-0002 [caso-0004 ...]
    python -m harness.etiquetar --pendientes [--n 10]
    python -m harness.etiquetar --escribir caso-0002 numero_documento_afectado=41234567

`--ver` imprime lo que hace falta para decidir: el asunto, el encabezado del
cuerpo y los fragmentos donde aparecen numeros, telefonos y rotulos de
documento. No imprime la salida del extractor.

Los casos llevan datos personales reales y no salen de `corpus/casos`.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

from harness.caso import CAMPOS_EVALUABLES, cargar_corpus

RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"

# Lo que se marca en el volcado para poder decidir sin leer el correo entero.
# Las siglas van con frontera de palabra para no casar dentro de otra palabra.
_INTERES = re.compile(
    r"(?:\bc\.?\s?c\.?\b|\bc[eé]dula\b|\bnit\b|\bt\.?\s?i\.?\b|\bc\.?\s?e\.?\b"
    r"|\bpasaporte\b|\bdocumento\b|\bidentificad\w*\b|\btel[eé]fono\b"
    r"|\bcelular\b|\bwhatsapp\b|\bnacid[oa]\b|\bsexo\b|\bmasculino\b"
    r"|\bfemenino\b|derecho\s+de\s+petici[oó]n|\bqueja\b|\breclamo\b"
    r"|felicitaci[oó]n|\bsugerencia\b)",
    re.IGNORECASE,
)

# Las direcciones de correo se quitan del volcado: en un reenvio masivo la
# lista de destinatarios ocupa todos los fragmentos y tapa el contenido.
_CORREO = re.compile(r"<?[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}>?,?\s*")
ANCHO = 130


def _texto_completo(caso) -> str:
    return "\n".join(
        [caso.asunto, caso.cuerpo_md] + list(caso.adjuntos.values())
    )


def fragmentos_de_interes(texto: str, limite: int = 25) -> List[str]:
    """Trozos alrededor de cada rotulo relevante, sin repetir zona."""
    texto = _CORREO.sub(" ", texto or "")
    vistos: List[Tuple[int, int]] = []
    salida: List[str] = []
    for m in _INTERES.finditer(texto):
        ini = max(0, m.start() - ANCHO // 3)
        fin = min(len(texto), m.end() + ANCHO)
        if any(ini < f and i < fin for i, f in vistos):
            continue
        vistos.append((ini, fin))
        salida.append(" ".join(texto[ini:fin].split()))
        if len(salida) >= limite:
            break
    return salida


def ver(caso) -> str:
    texto = _texto_completo(caso)
    lineas = [
        "=" * 78,
        f"{caso.id}   etiquetas: {', '.join(caso.etiquetas) or '-'}",
        f"  De    : {caso.remitente_nombre!r} <{caso.remitente_correo}>",
        f"  sender: {caso.sender_nombre!r}",
        f"  Asunto: {caso.asunto}",
        f"  adjuntos: {', '.join(caso.adjuntos) or '(ninguno)'}",
        "-" * 78,
        "ENCABEZADO DEL CUERPO",
    ]
    cuerpo = [l for l in (caso.cuerpo_md or "").splitlines() if l.strip()][:12]
    lineas += [f"  {l[:150]}" for l in cuerpo]
    lineas += ["-" * 78, "FRAGMENTOS CON ROTULOS"]
    frag = fragmentos_de_interes(texto)
    lineas += [f"  · {f}" for f in frag] or ["  (ninguno)"]
    ya = caso.esperado.campos_etiquetados()
    lineas += ["-" * 78, f"YA ETIQUETADO: {', '.join(ya) or '(nada)'}"]
    return "\n".join(lineas)


def _ruta_esperado(caso_id: str) -> Path:
    return RAIZ_CORPUS / caso_id / "esperado.json"


def _valor_final(campo: str, valor):
    """
    El valor tal como queda en el `esperado.json`.

    `null` significa "no evaluado" y la cadena vacia "debe quedar vacio". Son
    distintos —el modelo de datos los distingue y la medicion tambien— asi que
    aqui no se colapsan: etiquetar "no hay telefono en este correo" es lo que
    convierte un telefono inventado en un fallo visible.
    """
    if valor is None or valor == "":
        return valor
    if campo.startswith(("tipo_", "sexo_", "oficina")):
        return int(valor)
    return str(valor)


def escribir(caso_id: str, asignaciones: Dict[str, object]) -> List[str]:
    """
    Fija campos del `esperado.json` de un caso.

    Devuelve las lineas de lo que quedo escrito.
    """
    ruta = _ruta_esperado(caso_id)
    if not ruta.exists():
        raise FileNotFoundError(f"no existe {ruta}")
    datos = json.loads(ruta.read_text(encoding="utf-8"))

    cambios = []
    for campo, valor in asignaciones.items():
        if campo not in CAMPOS_EVALUABLES:
            raise ValueError(
                f"'{campo}' no es un campo evaluable. Son: "
                + ", ".join(CAMPOS_EVALUABLES))
        datos[campo] = _valor_final(campo, valor)
        cambios.append(f"  {campo} = {datos[campo]!r}")

    ruta.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cambios


def aplicar_lote(ruta: Path) -> List[str]:
    """
    Aplica un archivo JSON `{"caso-0002": {"campo": valor, ...}, ...}`.

    Es la via para etiquetar: los argumentos de linea de comandos pierden las
    tildes y la eñe segun la consola, y un nombre mal escrito se guarda como
    verdad de referencia. Es tambien la unica que distingue `null` de `""`.
    """
    lote = json.loads(Path(ruta).read_text(encoding="utf-8"))
    lineas = []
    for caso_id, asignaciones in lote.items():
        lineas.append(caso_id)
        lineas += escribir(caso_id, asignaciones)
    return lineas


def resumen_de_cobertura_de_etiquetas(casos) -> str:
    por_campo = {c: 0 for c in CAMPOS_EVALUABLES}
    for caso in casos:
        for campo in caso.esperado.campos_etiquetados():
            por_campo[campo] = por_campo.get(campo, 0) + 1
    total = len(casos) or 1
    lineas = [f"Casos en el corpus: {len(casos)}", "",
              f"{'CAMPO':<32}{'ETIQUETADOS':>12}{'%':>7}"]
    lineas.append("-" * 51)
    for campo in CAMPOS_EVALUABLES:
        n = por_campo.get(campo, 0)
        lineas.append(f"{campo:<32}{n:>12}{100 * n // total:>6}%")
    return "\n".join(lineas)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ver", nargs="+", metavar="CASO")
    p.add_argument("--pendientes", action="store_true",
                   help="lista los casos sin ninguna etiqueta")
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--escribir", nargs="+", metavar="CASO campo=valor")
    p.add_argument("--lote", metavar="RUTA.json",
                   help="aplica un JSON {caso: {campo: valor}}")
    p.add_argument("--estado", action="store_true")
    args = p.parse_args()

    casos = cargar_corpus(RAIZ_CORPUS)
    if not casos:
        print(f"corpus vacio en {RAIZ_CORPUS}")
        return 1
    por_id = {c.id: c for c in casos}

    if args.estado:
        print(resumen_de_cobertura_de_etiquetas(casos))
        return 0

    if args.lote:
        for linea in aplicar_lote(Path(args.lote)):
            print(linea)
        return 0

    if args.pendientes:
        sin = [c.id for c in casos if not c.esperado.campos_etiquetados()]
        print(f"sin etiquetar: {len(sin)} de {len(casos)}")
        print(" ".join(sin[: args.n]))
        return 0

    if args.ver:
        for caso_id in args.ver:
            if caso_id not in por_id:
                print(f"no existe {caso_id}")
                continue
            print(ver(por_id[caso_id]))
        return 0

    if args.escribir:
        caso_id, *pares = args.escribir
        if caso_id not in por_id:
            print(f"no existe {caso_id}")
            return 1
        asignaciones = {}
        for par in pares:
            if "=" not in par:
                print(f"'{par}' no tiene la forma campo=valor")
                return 1
            campo, _, valor = par.partition("=")
            # Desde la linea de comandos no hay forma de escribir null, asi que
            # "campo=" borra la etiqueta. Para decir "debe quedar vacio" hay que
            # usar --lote con "".
            asignaciones[campo.strip()] = valor.strip() or None
        for linea in escribir(caso_id, asignaciones):
            print(linea)
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
