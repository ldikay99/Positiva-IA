"""
Medicion de una corrida: cada llamada al modelo, las vueltas del agente y lo
que gasta el proceso en CPU y memoria.

Se engancha por fuera del pipeline, como el resto del harness: envuelve el
cliente de Vertex (todas las llamadas pasan por ahi: agente, revisor,
seguimiento y vision), el `resolver` que usa el orquestador y un muestreador de
/proc. Nada de esto cambia lo que el pipeline decide.

Por llamada se guarda quien la hizo, los tokens de entrada, salida, razonamiento
y cache implicita, y cuanto tardo. El costo se calcula con los precios de
`PRECIOS`: el razonamiento se factura como salida y la cache implicita con su
descuento, cosa que el contador del harness viejo no veia.
"""
from __future__ import annotations

import contextvars
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# USD por millon de tokens, gemini-2.5-flash en Vertex. El razonamiento va como
# salida y lo leido de la cache implicita cuesta el 10 % de la entrada.
PRECIOS = {"entrada": 0.30, "salida": 2.50, "cache": 0.03}

CORREO: contextvars.ContextVar = contextvars.ContextVar("correo_medido", default="")
_LLAMADAS: Dict[str, List[Dict[str, Any]]] = {}
_RESOLUCIONES: Dict[str, List[Dict[str, Any]]] = {}
_cerrojo = threading.Lock()


def _quien(config: Any) -> str:
    sistema = getattr(config, "system_instruction", None) or ""
    if not sistema:
        return "vision"
    from pipeline import revisor, seguimiento

    if sistema == revisor.SISTEMA:
        return "revisor"
    if sistema == seguimiento.SISTEMA:
        return "seguimiento"
    return "agente"


def instalar_en_cliente(modelo: Any) -> None:
    """Envuelve `generate_content` del cliente de Vertex del modelo."""
    api = modelo._obtener_cliente().models
    if getattr(api.generate_content, "_medido", False):
        return
    original = api.generate_content

    def generar(*a, **k):
        inicio = time.time()
        quien = _quien(k.get("config"))
        fallo = ""
        respuesta = None
        try:
            respuesta = original(*a, **k)
            return respuesta
        except Exception as e:
            fallo = f"{type(e).__name__}: {str(e)[:120]}"
            raise
        finally:
            meta = getattr(respuesta, "usage_metadata", None)
            fila = {
                "quien": quien,
                "ms": int((time.time() - inicio) * 1000),
                "entrada": getattr(meta, "prompt_token_count", 0) or 0,
                "salida": getattr(meta, "candidates_token_count", 0) or 0,
                "razonamiento": getattr(meta, "thoughts_token_count", 0) or 0,
                "cache": getattr(meta, "cached_content_token_count", 0) or 0,
                "fallo": fallo,
            }
            with _cerrojo:
                _LLAMADAS.setdefault(CORREO.get(), []).append(fila)

    generar._medido = True  # type: ignore[attr-defined]
    api.generate_content = generar


def instalar_en_resolver() -> None:
    """Guarda, por correo, las vueltas y las herramientas de cada `resolver`."""
    from pipeline import orquestador

    original = orquestador.resolver
    if getattr(original, "_medido", False):
        return

    def resolver(*a, **k):
        r = original(*a, **k)
        with _cerrojo:
            _RESOLUCIONES.setdefault(CORREO.get(), []).append({
                "vueltas": r.iteraciones,
                "herramientas": dict(r.herramientas_usadas),
                "repetidas": r.llamadas_repetidas,
                "agotado": r.agotado,
                "pedidos": len(a[2].campos_para_el_agente) if len(a) > 2 else None,
            })
        return r

    resolver._medido = True  # type: ignore[attr-defined]
    orquestador.resolver = resolver


def costo(fila: Dict[str, Any]) -> float:
    entrada = max(0, fila["entrada"] - fila["cache"])
    return (entrada * PRECIOS["entrada"] + fila["cache"] * PRECIOS["cache"]
            + (fila["salida"] + fila["razonamiento"]) * PRECIOS["salida"]) / 1e6


def de_correo(cid: str) -> Dict[str, Any]:
    """Lo medido de un correo, agregado por quien llamo. Lo saca del registro."""
    with _cerrojo:
        llamadas = _LLAMADAS.pop(cid, [])
        resoluciones = _RESOLUCIONES.pop(cid, [])
    por_quien: Dict[str, Dict[str, Any]] = {}
    for f in llamadas:
        q = por_quien.setdefault(f["quien"], {
            "llamadas": 0, "ms": 0, "entrada": 0, "salida": 0,
            "razonamiento": 0, "cache": 0, "fallos": 0, "usd": 0.0})
        q["llamadas"] += 1
        q["fallos"] += bool(f["fallo"])
        for c in ("ms", "entrada", "salida", "razonamiento", "cache"):
            q[c] += f[c]
        q["usd"] = round(q["usd"] + costo(f), 6)
    herramientas: Dict[str, int] = {}
    for r in resoluciones:
        for h, n in r["herramientas"].items():
            herramientas[h] = herramientas.get(h, 0) + n
    return {
        "por_quien": por_quien,
        "llamadas": len(llamadas),
        "usd": round(sum(costo(f) for f in llamadas), 6),
        "resoluciones": resoluciones,
        "vueltas_agente": sum(r["vueltas"] for r in resoluciones),
        "herramientas": herramientas,
        # Una fila por llamada: [quien, ms, entrada, salida, razonamiento, cache, fallo].
        "detalle": [[f["quien"], f["ms"], f["entrada"], f["salida"], f["razonamiento"],
                     f["cache"], f["fallo"][:80]] for f in llamadas],
    }


# ── Recursos del proceso ──

def _rss_de_todo_kb() -> int:
    """RSS de todos los procesos visibles (en el contenedor: el harness y sus tesseract)."""
    total = 0
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            for linea in (p / "status").read_text().splitlines():
                if linea.startswith("VmRSS:"):
                    total += int(linea.split()[1])
                    break
        except (OSError, ValueError):
            pass
    return total


def _rss_propio_kb() -> int:
    try:
        for linea in Path("/proc/self/status").read_text().splitlines():
            if linea.startswith("VmRSS:"):
                return int(linea.split()[1])
    except OSError:
        pass
    return 0


class Muestreador:
    """Cada `cada_s` mide la memoria; al cerrar da picos y CPU de la corrida."""

    def __init__(self, cada_s: float = 0.5) -> None:
        self.cada_s = cada_s
        self.pico_total_kb = 0
        self.pico_propio_kb = 0
        self.muestras = 0
        self._parar = threading.Event()
        self._hilo: Optional[threading.Thread] = None
        self._t0 = time.time()
        self._cpu0 = os.times()

    def _bucle(self) -> None:
        while not self._parar.wait(self.cada_s):
            self.muestras += 1
            self.pico_total_kb = max(self.pico_total_kb, _rss_de_todo_kb())
            self.pico_propio_kb = max(self.pico_propio_kb, _rss_propio_kb())

    def iniciar(self) -> "Muestreador":
        if Path("/proc/self/status").exists():
            self._hilo = threading.Thread(target=self._bucle, daemon=True)
            self._hilo.start()
        return self

    def cerrar(self) -> Dict[str, Any]:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=2)
        c = os.times()
        propio = (c.user - self._cpu0.user) + (c.system - self._cpu0.system)
        hijos = ((c.children_user - self._cpu0.children_user)
                 + (c.children_system - self._cpu0.children_system))
        reloj = time.time() - self._t0
        return {
            "reloj_s": round(reloj, 1),
            "cpu_propio_s": round(propio, 1),
            "cpu_hijos_s": round(hijos, 1),
            "cpu_nucleos_promedio": round((propio + hijos) / reloj, 2) if reloj else 0,
            "rss_pico_total_mb": round(self.pico_total_kb / 1024),
            "rss_pico_harness_mb": round(self.pico_propio_kb / 1024),
        }


def percentil(valores: List[float], p: float) -> float:
    if not valores:
        return 0.0
    v = sorted(valores)
    i = min(len(v) - 1, max(0, round(p / 100 * (len(v) - 1))))
    return v[i]


# ── Resumen de una corrida del universo ──

ETAPAS = ("descarga_adjuntos", "conversion_a_markdown", "extraccion_determinista",
          "agente", "compuerta", "correccion", "revisor", "registro_de_personas")


def _cargar(carpeta: Path) -> Dict[str, Dict[str, Any]]:
    import json

    salida = {}
    for p in sorted(Path(carpeta).glob("c[0-9]*.json")):
        try:
            salida[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return salida


def resumir(carpeta: Path) -> Dict[str, Any]:
    """Las cifras de una corrida, sacadas de sus archivos por correo."""
    import json

    correos = _cargar(carpeta)
    relojes = [d.get("reloj_s", 0) for d in correos.values()]
    desenlaces: Dict[str, int] = {}
    etapas: Dict[str, List[float]] = {e: [] for e in ETAPAS}
    quien: Dict[str, Dict[str, float]] = {}
    vueltas: List[int] = []
    herramientas: Dict[str, int] = {}
    ocr = {"paginas_ocr": 0, "perdidas_presupuesto": 0, "sin_texto": 0}
    for d in correos.values():
        desenlaces[d.get("desenlace", "?")] = desenlaces.get(d.get("desenlace", "?"), 0) + 1
        for e in ETAPAS:
            etapas[e].append((d.get("etapas_ms") or {}).get(e, 0) / 1000)
        m = d.get("medicion") or {}
        for q, v in (m.get("por_quien") or {}).items():
            acum = quien.setdefault(q, {})
            for c, x in v.items():
                acum[c] = acum.get(c, 0) + x
        if m.get("resoluciones"):
            vueltas.append(m.get("vueltas_agente", 0))
        for h, n in (m.get("herramientas") or {}).items():
            herramientas[h] = herramientas.get(h, 0) + n
        o = d.get("ocr") or {}
        ocr["paginas_ocr"] += o.get("paginas_ocr_leidas", 0) or 0
        ocr["perdidas_presupuesto"] += o.get("paginas_perdidas_presupuesto", 0) or 0
        ocr["sin_texto"] += o.get("paginas_sin_texto", 0) or 0
    corridas_json = Path(carpeta) / "corridas.json"
    corridas = json.loads(corridas_json.read_text(encoding="utf-8")) if corridas_json.exists() else []
    return {
        "correos": len(correos),
        "desenlaces": dict(sorted(desenlaces.items())),
        "reloj_correo_s": {"p50": percentil(relojes, 50), "p95": percentil(relojes, 95),
                           "max": max(relojes or [0]), "suma": round(sum(relojes), 1)},
        "etapas_s": {e: {"suma": round(sum(v), 1), "p50": round(percentil(v, 50), 2),
                         "p95": round(percentil(v, 95), 2)} for e, v in etapas.items()},
        "modelo": {q: {c: (round(x, 4) if isinstance(x, float) else x) for c, x in v.items()}
                   for q, v in sorted(quien.items())},
        "usd": round(sum(v.get("usd", 0) for v in quien.values()), 4),
        "correos_con_agente": len(vueltas),
        "vueltas_agente": {"media": round(sum(vueltas) / len(vueltas), 2) if vueltas else 0,
                           "p50": percentil(vueltas, 50), "p95": percentil(vueltas, 95)},
        "herramientas": dict(sorted(herramientas.items(), key=lambda kv: -kv[1])),
        "ocr": ocr,
        "corridas": corridas,
    }


VENTANA_DE_SUSTENTO = 300


def sustento(enviado: Dict[str, Any], texto: str) -> Dict[str, Any]:
    """
    Si el documento y el nombre del afectado que se enviarian aparecen en el
    texto, y juntos: a menos de `VENTANA_DE_SUSTENTO` caracteres, o con el
    rotulo o pegados como los reconoce la compuerta.
    """
    import re

    from pipeline import compuerta

    numero = str(enviado.get("numero_documento_afectado") or "")
    nombre = str(enviado.get("nombre_afectado") or "")
    plano = compuerta._normaliza(texto or "")
    digitos = re.sub(r"\D", "", numero)
    posiciones_num = []
    if digitos:
        patron = re.compile(r"(?<!\d)" + r"[.\s,'\-]?".join(digitos) + r"(?!\d)")
        posiciones_num = [m.start() for m in patron.finditer(plano)]
    palabras = [w for w in re.findall(r"[a-z]+", compuerta._normaliza(nombre)) if len(w) > 2]
    posiciones_nom = []
    if palabras:
        patron = re.compile(r"\b" + r"\W+(?:\w+\W+){0,2}".join(map(re.escape, palabras[:2])) + r"\b")
        posiciones_nom = [m.start() for m in patron.finditer(plano)]
    juntos = any(abs(a - b) <= VENTANA_DE_SUSTENTO for a in posiciones_num for b in posiciones_nom)
    rotulado = bool(numero and nombre and (
        compuerta.documento_junto_al_nombre(numero, nombre, texto)
        or compuerta.numero_pegado_al_nombre(numero, nombre, texto)))
    return {"documento_en_texto": bool(posiciones_num), "nombre_en_texto": bool(posiciones_nom),
            "juntos": juntos or rotulado}


def comparar(antes: Path, despues: Path) -> Dict[str, Any]:
    """Correo por correo: desenlace y campos enviados que cambian."""
    a, b = _cargar(antes), _cargar(despues)
    cambios = []
    for cid in sorted(set(a) & set(b)):
        x, y = a[cid], b[cid]
        campos = {}
        ex, ey = x.get("enviado") or {}, y.get("enviado") or {}
        for c in sorted(set(ex) | set(ey)):
            if str(ex.get(c, "")) != str(ey.get(c, "")):
                campos[c] = (ex.get(c, ""), ey.get(c, ""))
        if x.get("desenlace") != y.get("desenlace") or campos or \
                x.get("motivo_manual") != y.get("motivo_manual"):
            fila = {"caso": cid,
                    "desenlace": (x.get("desenlace"), y.get("desenlace")),
                    "motivo": (x.get("motivo_manual"), y.get("motivo_manual")),
                    "campos": campos}
            # Una radicacion nueva, o con otra identidad, tiene que tener sustento.
            if y.get("desenlace") == "radicaria" and (
                    x.get("desenlace") != "radicaria"
                    or "numero_documento_afectado" in campos or "nombre_afectado" in campos):
                textos = Path(despues) / "textos" / f"{cid}.txt"
                texto = textos.read_text(encoding="utf-8") if textos.exists() else ""
                fila["sustento"] = sustento(ey, texto)
            cambios.append(fila)
    return {"comunes": len(set(a) & set(b)), "cambian": len(cambios), "detalle": cambios}


def main(argv=None) -> int:
    import argparse
    import json

    p = argparse.ArgumentParser(description="Resume una corrida del universo o compara dos.")
    p.add_argument("corrida", help="carpeta <salida>/<version> de harness.universo")
    p.add_argument("contra", nargs="?", help="otra corrida para comparar correo por correo")
    args = p.parse_args(argv)
    print(json.dumps(resumir(Path(args.corrida)), ensure_ascii=False, indent=1))
    if args.contra:
        print(json.dumps(comparar(Path(args.corrida), Path(args.contra)),
                         ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
