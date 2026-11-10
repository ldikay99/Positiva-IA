"""
Correos REALES del universo por el flujo de produccion entero, con el modelo real.

    python -m harness.universo --gastar-modelo --muestra muestra.json --salida DIR --version nuevo
    python -m harness.universo --muestra muestra.json --salida DIR --version prod   # solo cache

Cada correo (`universo/cNNNN/mensaje.json` en formato Graph + `adj/`) se convierte
en `EmailMessage` y pasa por `Orquestador.procesar`, igual que en el Job:
enrutamiento, descarga de adjuntos, materializacion con OCR, extraccion, agente
con su revisor, correccion (si la version la tiene), compuerta y radicacion.

Nada sale a un sistema real:
  - MODO_TEST=true y credenciales ficticias para Graph, SGDEA y Redis; sin base de radicados.
  - El buzon es de disco (lee el mensaje y los adjuntos; toda escritura se anota).
  - Registro de personas apagado (`sgdea=None`): no se consulta SGDEA.
  - El radicador usa un servicio simulado que solo guarda el payload en memoria.
Solo se habla con Vertex (ADC), y por eso se exige `--gastar-modelo`.

Por correo deja `<salida>/<version>/cNNNN.json` (desenlace, motivo, campos y su
origen, payload, paginas OCR leidas y perdidas por presupuesto, tiempos, tokens)
y `<salida>/<version>/textos/cNNNN.txt` con lo que se materializo, para poder
verificar que lo enviado tiene sustento. Un correo con resultado no se repite
salvo con `--sin-cache`.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

for _clave in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET",
               "MICROSOFT_TENANT_ID", "OUTLOOK_REFRESH_TOKEN", "OUTLOOK_FOLDER_ID",
               "REDIS_PASSWORD", "SGDEA_CLIENT_SECRET", "SGDEA_PASSWORD"):
    os.environ[_clave] = "e2e-ficticio"
os.environ["MODO_TEST"] = "true"
os.environ["DEDUP_EN_PRUEBAS"] = "false"

UNIVERSO = Path(os.environ.get("RUTA_UNIVERSO") or (
    r"C:\Users\Jonathan Paredes\Documents\POSITIVA_IA_RADICACION_PQRD"
    r"\mcp\workspace\universo"))
PRECIO = {"entrada": 0.30, "salida": 2.50}  # USD por millon, gemini-2.5-flash


# ── Buzon, radicador y modelo de mentira (o contados) ──

class BuzonLocal:
    """Sirve el correo y sus adjuntos desde disco. Toda escritura queda solo anotada."""

    def __init__(self, carpeta: Path, mensaje: dict) -> None:
        self.carpeta = carpeta
        self.mensaje = mensaje
        self.escrituras: List[str] = []

    async def get_attachments(self, _id):
        return [{"id": a["archivo"], "name": a["name"], "size": a.get("size", 0),
                 "isInline": a.get("isInline", False), "contentType": a.get("contentType")}
                for a in self.mensaje.get("_adjuntos", []) if a.get("archivo")]

    async def download_attachment(self, _id, adjunto):
        return base64.b64encode((self.carpeta / "adj" / adjunto).read_bytes()).decode()

    async def get_email_by_id(self, _id):
        return None

    def __getattr__(self, nombre):
        if nombre.startswith("ultimo"):
            return ""

        async def _anotar(*a, **k):
            self.escrituras.append(nombre)
            return None
        return _anotar


class ServicioSimulado:
    """Lo que SGDEA recibiria. No hay red: guarda el payload y devuelve un numero falso."""

    def __init__(self) -> None:
        self.enviado: Optional[Dict[str, Any]] = None

    async def radicar_pqrd(self, payload, archivos):
        self.enviado = payload.to_form_data()
        self.adjuntos = [n for n, _ in archivos]
        return SimpleNamespace(id=0, id_radicado="E2E-SIMULADO", fecha_radicacion="",
                               claves_recibidas=())


class ModeloContado:
    """El modelo real; suma lo que consumen el agente y el revisor de UN correo."""

    def __init__(self, real) -> None:
        self.real = real
        self.nombre = real.nombre
        self.tokens_entrada = self.tokens_salida = self.llamadas = 0
        self._cerrojo = threading.Lock()

    def generar(self, sistema, mensajes, herramientas):
        turno = self.real.generar(sistema, mensajes, herramientas)
        with self._cerrojo:
            self.llamadas += 1
            self.tokens_entrada += turno.tokens_entrada or 0
            self.tokens_salida += turno.tokens_salida or 0
        return turno

    def __getattr__(self, nombre):
        return getattr(self.real, nombre)


def modelo_vertex():
    from config import settings
    from correr import _credenciales_de_vertex
    from pipeline.modelo import ModeloVertex

    return ModeloVertex(proyecto=settings.GCP_PROJECT, ubicacion="global",
                        credenciales=_credenciales_de_vertex(), endpoint_global=True)


# ── Lo que se leyo de los adjuntos ──

_MATERIALIZADO: Dict[str, Dict[str, Any]] = {}
_RE_OCR = re.compile(r"^#{2,3} (?:Pagina \d+|Imagen incrustada) \(OCR", re.M)
_RE_SIN_TEXTO = re.compile(r"_\(pagina sin texto extraible")
_RE_PRESUPUESTO = re.compile(r"_\(pagina sin texto extraible: se agoto el presupuesto")
_RE_IMG_SIN_LEER = re.compile(
    r"imagenes incrustadas que no se pudieron leer: (\d+) — se agoto el presupuesto")


def _instalar_materializar(dir_textos: Path) -> None:
    """Envuelve `materializar` del orquestador para medir el OCR y guardar el texto."""
    from pipeline import orquestador

    original = orquestador.materializar

    def materializar(*a, **k):
        ws = original(*a, **k)
        try:
            cid = k.get("id_correo", "")
            partes = [f"ASUNTO: {k.get('asunto', '')}",
                      f"REMITENTE: {k.get('remitente_nombre', '')} <{k.get('remitente_correo', '')}>",
                      "CUERPO:", k.get("cuerpo_limpio", "")]
            st = {"adjuntos": [], "paginas_ocr_leidas": 0, "paginas_sin_texto": 0,
                  "paginas_perdidas_presupuesto": 0, "imagenes_perdidas_presupuesto": 0,
                  "adjuntos_fallidos": 0, "caracteres_adjuntos": 0}
            for e in ws.manifiesto.entradas:
                md = ""
                try:
                    md = ws.leer(Path(e.ruta_md).stem) if e.ruta_md else ""
                except Exception:
                    md = ""
                leidas = len(_RE_OCR.findall(md))
                tipo = getattr(e, "tipo_real", "")
                if tipo == "imagen" and getattr(e, "ocr_aplicado", False) and leidas == 0 \
                        and "sin texto reconocible" not in md and md.strip():
                    leidas = 1
                perdidas = len(_RE_PRESUPUESTO.findall(md))
                detalle = getattr(e, "detalle", "") or ""
                if tipo == "imagen" and "presupuesto" in detalle:
                    perdidas += 1
                img_perd = sum(int(x) for x in _RE_IMG_SIN_LEER.findall(detalle))
                st["adjuntos"].append({
                    "tipo": tipo, "estado": getattr(e, "estado", ""),
                    "ocr": bool(getattr(e, "ocr_aplicado", False)),
                    "caracteres": len(md), "paginas_ocr": leidas,
                    "sin_texto": len(_RE_SIN_TEXTO.findall(md)),
                    "perdidas_presupuesto": perdidas,
                    "detalle": detalle[:300]})
                st["paginas_ocr_leidas"] += leidas
                st["paginas_sin_texto"] += len(_RE_SIN_TEXTO.findall(md))
                st["paginas_perdidas_presupuesto"] += perdidas
                st["imagenes_perdidas_presupuesto"] += img_perd
                st["adjuntos_fallidos"] += getattr(e, "estado", "") != "convertido"
                st["caracteres_adjuntos"] += len(md)
                partes += [f"\n===== ADJUNTO ({tipo}) =====", md]
            st["avisos_manifiesto"] = [str(x)[:300] for x in ws.manifiesto.avisos]
            _MATERIALIZADO[cid] = st
            (dir_textos / f"{cid}.txt").write_text("\n".join(partes), encoding="utf-8")
        except Exception as e:  # medir nunca rompe el flujo
            _MATERIALIZADO[k.get("id_correo", "")] = {"error_medida": f"{type(e).__name__}: {e}"}
        return ws

    orquestador.materializar = materializar


# ── Un correo ──

def _valor(v: Any) -> Any:
    return v if isinstance(v, (str, int, float, bool)) or v is None else str(v)


async def correr_correo(cid: str, modelo_real: Any, version: str) -> Dict[str, Any]:
    from harness.enviado import CAMPO_EN_EL_PAYLOAD
    from models.email import EmailMessage
    from pipeline.orquestador import Orquestador
    from pipeline.radicador import Radicador

    carpeta = UNIVERSO / cid
    crudo = json.loads((carpeta / "mensaje.json").read_text(encoding="utf-8"))
    limpio = {k: v for k, v in crudo.items() if not k.startswith("_")}
    limpio["categories"] = []
    limpio["id"] = cid
    email = EmailMessage.from_graph_api(limpio)

    from harness import medicion

    medicion.CORREO.set(cid)
    cpu0 = os.times()
    modelo = ModeloContado(modelo_real)
    buzon = BuzonLocal(carpeta, crudo)
    servicio = ServicioSimulado()
    orq = Orquestador(modelo=modelo, outlook=buzon,
                      radicador=Radicador(servicio=servicio), sgdea=None)
    inicio = time.time()
    salida: Dict[str, Any] = {"caso": cid, "version": version}
    try:
        res = await asyncio.wait_for(orq.procesar(email), 1500)
        desenlace = res.accion
        if desenlace == "radicado" and res.radicado == "E2E-SIMULADO":
            desenlace = "radicaria"
        salida.update({
            "desenlace": desenlace, "motivo_manual": res.motivo_manual,
            "avisos": [str(a)[:400] for a in res.avisos],
            "etapas_ms": res.etapas_ms, "duracion_s": round(res.duracion_ms / 1000, 1),
            "turnos_del_agente": res.turnos_del_agente,
            "explicacion_del_agente": (res.explicacion_del_agente or "")[:600],
            "area_destino": res.area_destino,
            "hallazgos": {c: {"valor": _valor(h.valor),
                              "origen": getattr(res.atribucion.get(c), "value",
                                                str(res.atribucion.get(c, ""))),
                              "metodo": h.metodo, "confianza": h.confianza,
                              "evidencia": str(h.evidencia or "")[:200]}
                          for c, h in sorted(res.hallazgos.items())},
            "atribucion": {c: getattr(o, "value", str(o)) for c, o in res.atribucion.items()},
        })
        if servicio.enviado is not None:
            salida["enviado"] = {c: _valor(servicio.enviado.get(k))
                                 for c, k in CAMPO_EN_EL_PAYLOAD.items()}
    except Exception as e:
        salida.update({"desenlace": "error", "motivo_manual": "ERROR_HARNESS",
                       "error": f"{type(e).__name__}: {e}",
                       "traza": traceback.format_exc()[-1500:],
                       "duracion_s": round(time.time() - inicio, 1)})
    salida["escrituras_buzon"] = buzon.escrituras
    salida["ocr"] = _MATERIALIZADO.pop(cid, {})
    salida["llamadas_modelo"] = modelo.llamadas
    salida["tokens_entrada"] = modelo.tokens_entrada
    salida["tokens_salida"] = modelo.tokens_salida
    salida["costo_usd"] = round((modelo.tokens_entrada * PRECIO["entrada"]
                                 + modelo.tokens_salida * PRECIO["salida"]) / 1e6, 5)
    salida["reloj_s"] = round(time.time() - inicio, 1)
    cpu1 = os.times()
    # Solo dice algo con un trabajador: con varios, se cruzan los correos.
    salida["cpu_s"] = round(sum(cpu1[:4]) - sum(cpu0[:4]), 2)
    salida["medicion"] = medicion.de_correo(cid)
    return salida


# ── Corrida ──

async def correr(ids: List[str], dir_v: Path, version: str, trabajadores: int,
                 tope_usd: float, progreso) -> None:
    from harness import medicion

    modelo = modelo_vertex()
    medicion.instalar_en_cliente(modelo)
    medicion.instalar_en_resolver()
    sem = asyncio.Semaphore(trabajadores)
    gastado = {"usd": 0.0, "hechos": 0}
    parar = asyncio.Event()

    async def uno(cid: str):
        async with sem:
            if parar.is_set():
                return
            r = await correr_correo(cid, modelo, version)
            (dir_v / f"{cid}.json").write_text(
                json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
            gastado["usd"] += max(r["costo_usd"], r["medicion"]["usd"])
            gastado["hechos"] += 1
            ocr = r.get("ocr") or {}
            progreso(f"[{gastado['hechos']}/{len(ids)}] {cid} {r.get('desenlace', ''):<10} "
                     f"{r.get('motivo_manual', ''):<28} {r.get('reloj_s', 0):>6}s "
                     f"ocr={ocr.get('paginas_ocr_leidas', '-')} "
                     f"perd={ocr.get('paginas_perdidas_presupuesto', '-')} "
                     f"${r['medicion']['usd']:.4f} total ${gastado['usd']:.3f}"
                     + (f" ERROR {r.get('error', '')[:80]}" if r.get("error") else ""))
            if gastado["usd"] > tope_usd and not parar.is_set():
                progreso(f"ATENCION: gasto {gastado['usd']:.3f} USD > tope {tope_usd}: se detiene")
                parar.set()

    await asyncio.gather(*(uno(c) for c in ids))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--muestra", default="",
                   help="json con la lista 'muestra' de {id}; sin ella, todo el universo")
    p.add_argument("--salida", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--casos", default="", help="ids separados por coma (subconjunto)")
    p.add_argument("--trabajadores", type=int, default=4)
    p.add_argument("--tope-usd", type=float, default=1.5)
    p.add_argument("--sin-cache", action="store_true")
    p.add_argument("--gastar-modelo", action="store_true")
    args = p.parse_args(argv)

    from config import settings
    if not settings.MODO_TEST:
        print("MODO_TEST no quedo activo: no se corre")
        return 1

    dir_v = Path(args.salida) / args.version
    (dir_v / "textos").mkdir(parents=True, exist_ok=True)
    if args.muestra:
        ids = [m["id"] for m in json.loads(
            Path(args.muestra).read_text(encoding="utf-8"))["muestra"]]
    else:
        ids = sorted(p.name for p in UNIVERSO.iterdir() if (p / "mensaje.json").exists())
    if args.casos:
        quiero = {x.strip() for x in args.casos.split(",") if x.strip()}
        ids = [i for i in ids if i in quiero]
    pendientes = [i for i in ids if args.sin_cache or not (dir_v / f"{i}.json").exists()]
    print(f"{len(ids)} correos, {len(pendientes)} pendientes", file=sys.stderr)
    if not pendientes:
        return 0
    if not args.gastar_modelo:
        print("sin --gastar-modelo: no se llama al modelo", file=sys.stderr)
        return 1

    _instalar_materializar(dir_v / "textos")
    archivo = (dir_v / "progreso.txt").open("a", encoding="utf-8")

    def progreso(linea: str) -> None:
        archivo.write(linea + "\n")
        archivo.flush()
        print(linea, file=sys.stderr, flush=True)

    from harness import medicion

    inicio = time.time()
    muestreo = medicion.Muestreador().iniciar()
    asyncio.run(correr(pendientes, dir_v, args.version, args.trabajadores,
                       args.tope_usd, progreso))
    recursos = muestreo.cerrar()
    recursos.update({"correos": len(pendientes), "trabajadores": args.trabajadores,
                     "correos_por_minuto": round(len(pendientes) / max(1e-9, time.time() - inicio) * 60, 2)})
    corridas = dir_v / "corridas.json"
    previas = json.loads(corridas.read_text(encoding="utf-8")) if corridas.exists() else []
    corridas.write_text(json.dumps(previas + [recursos], indent=1), encoding="utf-8")
    progreso(f"FIN {args.version}: {time.time() - inicio:.0f}s de reloj; {recursos}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
