"""
Acierto de PUNTA A PUNTA, con el modelo real, sobre el corpus etiquetado.

    python -m harness.punta_a_punta --gastar-modelo --n 3
    python -m harness.punta_a_punta --gastar-modelo          # los 102 casos
    python -m harness.punta_a_punta --gastar-modelo --casos caso-0002,caso-0004
    python -m harness.punta_a_punta --gastar-modelo --sin-cache --trabajadores 8
    python -m harness.punta_a_punta                          # solo la cache, gratis

Corre cada caso como en produccion: enrutamiento y facturas, extraccion
determinista, agente (con su revisor) para lo pendiente, herencia del afectado,
registro de personas (aqui SIEMPRE apagado: es un sistema del cliente), compuerta,
la ronda de correccion (lo rechazado corregible vuelve al agente una vez) y
payload. Compara lo que se ENVIARIA con `esperado.json`, campo por campo.

Cuesta dinero (Vertex, ADC de la maquina), por eso exige `--gastar-modelo`:
sin la bandera solo informa de lo que ya esta en cache. Cada caso se guarda en
`workspace/pruebas_e2e/<id>.json` y no se vuelve a llamar al modelo si existe,
salvo con `--sin-cache`. El agregado de la corrida queda en
`workspace/pruebas_e2e/corrida.json`.

  ACIERTA  el valor final coincide con la etiqueta (o calla y la etiqueta es "")
  FALLA    afirma un valor distinto, o afirma algo que la etiqueta dice que no esta
  CALLA    no hay valor y la etiqueta trae uno (va a revision o a defecto)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Esta medida no habla con Outlook, Supabase ni SGDEA: solo con Vertex, que va
# por ADC. Los secretos de esos servicios se rellenan con ficticios ANTES de
# importar el proyecto (como tests/conftest.py), asi ningun cliente del cliente
# puede autenticarse aunque algo intentara llamarlo.
for _clave in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET",
               "MICROSOFT_TENANT_ID", "OUTLOOK_REFRESH_TOKEN", "OUTLOOK_FOLDER_ID",
               "REDIS_PASSWORD", "SGDEA_CLIENT_SECRET", "SGDEA_PASSWORD",
               "SUPABASE_SERVICE_ROLE_KEY"):
    os.environ.setdefault(_clave, "e2e-ficticio")
os.environ.setdefault("SUPABASE_URL", "https://e2e-ficticio.supabase.co")
os.environ["MODO_TEST"] = "true"

from harness.atribucion import coincide  # noqa: E402
from harness.caso import Caso, adjuntos_ilegibles_de, cargar_corpus  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
RAIZ_CORPUS = RAIZ / "corpus" / "casos"
RAIZ_E2E = RAIZ / "workspace" / "pruebas_e2e"
TOPE_USD_POR_CASO = 0.02
ANCHO = 92


# ── Modelo ──

@dataclass
class ModeloContado:
    """Envuelve el modelo real y suma lo que consumen el agente Y el revisor."""

    real: Any
    tokens_entrada: int = 0
    tokens_salida: int = 0
    llamadas: int = 0
    _cerrojo: threading.Lock = field(default_factory=threading.Lock)

    @property
    def nombre(self) -> str:
        return self.real.nombre

    def generar(self, sistema, mensajes, herramientas):
        turno = self.real.generar(sistema, mensajes, herramientas)
        with self._cerrojo:
            self.llamadas += 1
            self.tokens_entrada += turno.tokens_entrada or 0
            self.tokens_salida += turno.tokens_salida or 0
        return turno


def modelo_vertex():
    """El mismo `ModeloVertex` que arma `correr.py --vertex`."""
    from config import settings
    from correr import _credenciales_de_vertex
    from pipeline.modelo import ModeloVertex

    return ModeloVertex(
        proyecto=settings.GCP_PROJECT,
        ubicacion="global",
        credenciales=_credenciales_de_vertex(),
        endpoint_global=True,
    )


# ── Un caso ──

def _materializar(caso: Caso):
    """El workspace del caso, temporal: se borra al cerrar."""
    from pipeline.workspace import ESTADO_OK, EntradaManifiesto, Workspace

    ws = Workspace.crear(id_correo=caso.id, base=None, temporal=True)
    ws.escribir_correo(caso.cuerpo_md)
    if caso.hilo_md:
        ws.escribir_hilo(caso.hilo_md)
    for i, (nombre, markdown) in enumerate(sorted(caso.adjuntos.items()), 1):
        ruta = ws.escribir_adjunto(i, nombre, markdown)
        ws.manifiesto.agregar(EntradaManifiesto(
            nombre_original=nombre, ruta_md=str(ruta), tipo_real="pdf",
            estado=ESTADO_OK, caracteres_md=len(markdown),
        ))
    ws.manifiesto.asunto = caso.asunto
    ws.manifiesto.remitente_nombre = caso.remitente_nombre
    ws.manifiesto.remitente_correo = caso.remitente_correo
    return ws


def _valor_json(v: Any) -> Any:
    return v if isinstance(v, (str, int, float, bool)) or v is None else str(v)


def correr_caso(caso: Caso, modelo: Any) -> Dict[str, Any]:
    """
    El flujo de `Orquestador.procesar` desde el cuerpo ya limpio hasta el
    payload, sin Outlook, sin deduplicacion, sin registro de personas y sin
    radicar. Devuelve lo que se enviaria y por que.
    """
    return asyncio.run(_correr_caso(caso, modelo))


async def _correr_caso(caso: Caso, modelo_real: Any) -> Dict[str, Any]:
    from harness.enviado import CAMPO_EN_EL_PAYLOAD, _correo_de
    from pipeline import compuerta, facturas, orquestador
    from pipeline.agente import atribucion, combinar
    from pipeline.atribucion import Origen
    from pipeline.compuerta import Veredicto
    from pipeline.correccion import campos_a_corregir
    from pipeline.enrutamiento import (
        MOTIVO_REMITENTE_EXCLUIDO, MOTIVO_REMITENTE_NO_IDENTIFICABLE, Destino,
        decidir)
    from pipeline.errores import Bitacora, FalloDelModelo
    from pipeline.extraccion import extraer_oficina, extraer_todo
    from pipeline.personas import rellenar_desde_el_registro
    from pipeline.radicador import armar_payload
    from servidor_mcp.herramientas import Contexto
    from utils.generic_filter import (
        es_buzon_de_positiva, tiene_remitente_externo_identificable)

    inicio = time.time()
    modelo = ModeloContado(modelo_real)
    # Sin `sgdea`: `_consultar_persona` devuelve None y el registro no se toca.
    orq = orquestador.Orquestador(modelo=modelo, sgdea=None)
    res = orquestador.Resultado(email_id=caso.id, asunto=caso.asunto)
    bitacora = Bitacora()
    orquestador._BITACORA.set(bitacora)
    email = _correo_de(caso)
    cuerpo = caso.cuerpo_md

    salida: Dict[str, Any] = {
        "caso": caso.id, "desenlace": "", "motivo_manual": "", "rechazos": [],
        "reparos": [], "hallazgos": {}, "enviado": {}, "suposiciones": [],
        "sin_resolver": {}, "avisos": [], "explicacion_del_agente": "",
        "turnos_del_agente": 0, "error": "",
    }

    def cerrar(desenlace: str, motivo: str = "") -> Dict[str, Any]:
        salida["desenlace"] = desenlace
        salida["motivo_manual"] = motivo
        salida["avisos"] = list(res.avisos)
        salida["llamadas_modelo"] = modelo.llamadas
        salida["tokens_entrada"] = modelo.tokens_entrada
        salida["tokens_salida"] = modelo.tokens_salida
        salida["modelo"] = modelo.nombre
        salida["duracion_s"] = round(time.time() - inicio, 1)
        return salida

    # Lo que en produccion se decide antes de abrir los adjuntos.
    ruta = decidir(asunto=caso.asunto, cuerpo=cuerpo,
                   correo_remitente=caso.remitente_correo)
    razon = facturas.es_factura(caso.asunto, cuerpo)
    if razon:
        res.avisos.append(razon)
        return cerrar("manual", facturas.MOTIVO)
    if ruta.destino is Destino.NO_SE_RADICA:
        res.avisos.append(ruta.evidencia)
        return cerrar("manual", MOTIVO_REMITENTE_EXCLUIDO)
    if ruta.se_reenvia:
        res.avisos.append(f"reenviado a {ruta.correo_destino}: {ruta.evidencia}")
        return cerrar("reenviado")
    if (es_buzon_de_positiva(caso.remitente_correo)
            and not tiene_remitente_externo_identificable(
                caso.remitente_nombre, caso.remitente_correo, cuerpo)):
        return cerrar("manual", MOTIVO_REMITENTE_NO_IDENTIFICABLE)

    ws = _materializar(caso)
    try:
        texto_adjuntos = "\n\n".join(
            ws.leer(Path(e.ruta_md).stem) for e in ws.manifiesto.convertidos)
        determinista = extraer_todo(
            asunto=caso.asunto, cuerpo=cuerpo, texto_adjuntos=texto_adjuntos,
            adjuntos_ilegibles=adjuntos_ilegibles_de(caso),
            from_name=caso.remitente_nombre, from_address=caso.remitente_correo,
            sender_name=caso.sender_nombre,
            nombres_hilo=orq._nombres_hilo(email), cuerpo_actual=cuerpo,
        )
        ctx = Contexto(
            workspace=ws, consultar_persona=None,
            resueltos={c: h.valor for c, h in determinista.hallazgos.items()},
            revisar=orq._revisor(ws),
        )
        try:
            resolucion = await orq._resolver_con_reintento(
                ctx, determinista, bitacora, res)
        except FalloDelModelo as e:
            salida["error"] = str(e)
            return cerrar("manual", e.motivo)
        salida["turnos_del_agente"] = resolucion.iteraciones
        salida["agotado"] = resolucion.agotado
        res.turnos_del_agente = resolucion.iteraciones
        salida["explicacion_del_agente"] = resolucion.explicacion
        salida["sin_resolver"] = {
            c: nr.motivo for c, nr in resolucion.sin_resolver.items()}
        res.avisos.extend(resolucion.descartados)
        res.avisos.extend(resolucion.corregidos)
        res.avisos.extend(resolucion.avisos)

        hallazgos = combinar(determinista, resolucion)
        res.atribucion = atribucion(determinista, resolucion)
        if "oficina" not in hallazgos and "tipo_solicitud" in hallazgos:
            h_of, _ = extraer_oficina(hallazgos["tipo_solicitud"].valor)
            if h_of is not None:
                hallazgos["oficina"] = h_of
                res.atribucion["oficina"] = Origen.ALGORITMO
        fuentes = orq._fuentes_del_correo(email, cuerpo, texto_adjuntos, ws, [])
        orq._heredar_datos_del_afectado(
            hallazgos, res, fuentes=fuentes,
            correo_remitente=caso.remitente_correo)

        # Registro de personas apagado: con consulta None no aporta nada, igual
        # que en las pruebas. Se llama para que, si un dia deja de ser asi, se
        # note aqui.
        contradicciones: List[str] = []
        for campo, h in rellenar_desde_el_registro(
                hallazgos, None, res.avisos, contradicciones).items():
            hallazgos[campo] = h
            res.atribucion[campo] = Origen.REGISTRO

        # El mismo tramo de produccion: compuerta, ronda de correccion y
        # revisor obligatorio de la identidad.
        hallazgos, dictamen = await orq._validar_y_corregir(
            ctx, determinista, hallazgos, resolucion, bitacora, res, email,
            cuerpo, texto_adjuntos, ws, [])
        salida["veredicto_antes_de_corregir"] = res.revision.get(
            "veredicto_antes_de_corregir", "")
        salida["corregidos"] = dict(res.revision.get("corregidos", {}))
        if "rechazos_antes_de_corregir" in res.revision:
            salida["rechazos_antes_de_corregir"] = res.revision[
                "rechazos_antes_de_corregir"]
        salida["revisor"] = {k: v for k, v in res.revision.items()
                             if k in ("llamadas", "vueltas", "reparos",
                                      "reparos_tras_corregir")}
        salida["rescatado_por_correccion"] = bool(
            salida["corregidos"]) and dictamen.veredicto is not Veredicto.RECHAZADO
        salida["turnos_totales_del_agente"] = res.turnos_del_agente
        salida["traza_del_agente"] = getattr(resolucion, "traza", [])
        if dictamen.veredicto is Veredicto.CORREGIDO:
            hallazgos = compuerta.aplicar(hallazgos, dictamen)
            res.avisos.extend(compuerta.avisos(dictamen))
        salida["reparos"] = [
            f"{r.codigo}: {r.campo}: {r.detalle}" for r in dictamen.reparos]
        salida["rechazos"] = [
            f"{r.codigo}: {r.campo}: {r.detalle}" for r in dictamen.bloqueantes]
        salida["veredicto"] = dictamen.veredicto.value
        salida["hallazgos"] = {
            campo: {
                "valor": _valor_json(h.valor),
                "origen": getattr(res.atribucion.get(campo), "value",
                                  str(res.atribucion.get(campo, ""))),
                "metodo": h.metodo,
                "confianza": h.confianza,
                "evidencia": str(h.evidencia or "")[:200],
            }
            for campo, h in sorted(hallazgos.items())
        }

        suposiciones: List[str] = []
        payload = armar_payload(email, hallazgos, cuerpo,
                                suposiciones=suposiciones).to_form_data()
        salida["enviado"] = {
            campo: _valor_json(payload.get(clave))
            for campo, clave in CAMPO_EN_EL_PAYLOAD.items()}
        salida["suposiciones"] = suposiciones

        if dictamen.veredicto is Veredicto.RECHAZADO:
            return cerrar("manual", dictamen.motivo_manual)
        if not bitacora.puede_radicar:
            return cerrar("manual", bitacora.motivo_manual())
        return cerrar("radicaria")
    except Exception as e:  # el caso falla, la corrida sigue
        salida["error"] = f"{type(e).__name__}: {e}"
        return cerrar("error", "ERROR_INESPERADO")
    finally:
        ws.cerrar()


# ── Corrida ──

def _ruta_cache(caso_id: str) -> Path:
    return RAIZ_E2E / f"{caso_id}.json"


def costo_usd(r: Dict[str, Any]) -> float:
    from pipeline.costo import Llamada

    c = Llamada("e2e", r.get("modelo") or "gemini-2.5-flash",
                r.get("tokens_entrada", 0), r.get("tokens_salida", 0)).costo_usd
    return c or 0.0


def correr(casos: Sequence[Caso], trabajadores: int, sin_cache: bool,
           tope_usd: float, gastar_modelo: bool = False) -> tuple:
    RAIZ_E2E.mkdir(parents=True, exist_ok=True)
    resultados: Dict[str, Dict[str, Any]] = {}
    pendientes: List[Caso] = []
    for caso in casos:
        ruta = _ruta_cache(caso.id)
        if ruta.exists() and not sin_cache:
            resultados[caso.id] = json.loads(ruta.read_text(encoding="utf-8"))
            resultados[caso.id]["de_cache"] = True
        else:
            pendientes.append(caso)

    detenido = ""
    if pendientes and not gastar_modelo:
        print(f"{len(pendientes)} caso(s) sin resultado en cache y sin "
              f"--gastar-modelo: no se corren")
        return [resultados[c.id] for c in casos if c.id in resultados], detenido
    if pendientes:
        modelo = modelo_vertex()
        # Los modelos pydantic de google-genai se terminan de construir en el
        # primer uso, y con varios hilos a la vez ese primer uso falla a veces
        # ("function_declarations: Extra inputs are not permitted"). Se usan
        # una vez aqui, en un solo hilo, antes de abrir el pool.
        from google.genai import types as _tipos
        from pipeline.agente import ESQUEMAS_HERRAMIENTAS as _esquemas
        _tipos.GenerateContentConfig(temperature=0.1, system_instruction="x").tools = [
            _tipos.Tool(function_declarations=list(_esquemas))]
        print(f"{len(pendientes)} caso(s) al modelo {modelo.nombre}, "
              f"{trabajadores} en paralelo; {len(resultados)} de cache")
        gastado, hechos = 0.0, 0
        parar = threading.Event()

        def uno(caso: Caso):
            if parar.is_set():
                return None
            return correr_caso(caso, modelo)

        with ThreadPoolExecutor(max_workers=trabajadores) as pool:
            futuros = {pool.submit(uno, c): c for c in pendientes}
            for fut in as_completed(futuros):
                caso = futuros[fut]
                try:
                    r = fut.result()
                except Exception as e:
                    r = {"caso": caso.id, "desenlace": "error",
                         "error": f"{type(e).__name__}: {e}"}
                if r is None:
                    continue
                r["costo_usd"] = round(costo_usd(r), 5)
                _ruta_cache(caso.id).write_text(
                    json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
                resultados[caso.id] = r
                hechos += 1
                gastado += r["costo_usd"]
                print(f"  [{hechos}/{len(pendientes)}] {caso.id:<12} "
                      f"{r.get('desenlace', ''):<10} {r.get('motivo_manual', ''):<28} "
                      f"{r.get('duracion_s', 0):>6}s  ${r['costo_usd']:.4f}"
                      + (f"  ERROR {r['error'][:60]}" if r.get("error") else ""),
                      flush=True)
                if hechos >= 3 and gastado / hechos > tope_usd and not parar.is_set():
                    detenido = (f"costo medio {gastado / hechos:.4f} USD/caso > "
                                f"{tope_usd} tras {hechos} casos: se detiene")
                    print("ATENCION: " + detenido)
                    parar.set()
                    for f in futuros:
                        f.cancel()
    return [resultados[c.id] for c in casos if c.id in resultados], detenido


# ── Informe ──

def _vacio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def evaluar(resultados: Sequence[Dict[str, Any]],
            etiquetas: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    from harness.enviado import CAMPO_EN_EL_PAYLOAD, _es_abstencion
    from pipeline.compuerta import OBLIGATORIOS

    tabla: Dict[str, Dict[str, int]] = {}
    fallos: List[Dict[str, Any]] = []
    motivos: Dict[str, int] = {}
    pasan = pasan_obligatorios_ok = 0
    corregidos = sum(1 for r in resultados if r.get("corregidos"))
    rescatados = sum(1 for r in resultados if r.get("rescatado_por_correccion"))
    env_ok = env_total = 0
    for r in resultados:
        esperado = etiquetas.get(r["caso"], {})
        if r.get("desenlace") != "radicaria":
            clave = r.get("motivo_manual") or r.get("desenlace") or "?"
            motivos[clave] = motivos.get(clave, 0) + 1
        hallazgos = r.get("hallazgos") or {}
        for campo in CAMPO_EN_EL_PAYLOAD:
            esp = esperado.get(campo)
            if esp is None:
                continue
            t = tabla.setdefault(campo, {"acierta": 0, "falla": 0, "calla": 0})
            h = hallazgos.get(campo)
            obtenido = None if h is None else h.get("valor")
            if _vacio(obtenido):
                if _vacio(esp):
                    t["acierta"] += 1
                else:
                    t["calla"] += 1
                continue
            if not _vacio(esp) and coincide(obtenido, esp, campo):
                t["acierta"] += 1
                continue
            t["falla"] += 1
            fallos.append({
                "caso": r["caso"], "campo": campo, "obtenido": obtenido,
                "esperado": esp if not _vacio(esp) else "(no esta en el correo)",
                "origen": h.get("origen"), "metodo": h.get("metodo"),
                "confianza": h.get("confianza"),
                "desenlace": r.get("desenlace"),
                "evidencia": h.get("evidencia", ""),
            })

        if r.get("desenlace") != "radicaria":
            continue
        pasan += 1
        enviado = r.get("enviado") or {}
        oblig_ok = True
        for campo in CAMPO_EN_EL_PAYLOAD:
            esp = esperado.get(campo)
            if esp is None:
                continue
            v = enviado.get(campo)
            ok = (_es_abstencion(campo, v) if _vacio(esp)
                  else coincide(v, esp, campo))
            env_total += 1
            env_ok += ok
            if campo in OBLIGATORIOS and not ok:
                oblig_ok = False
        pasan_obligatorios_ok += oblig_ok

    return {
        "tabla": tabla, "fallos": fallos, "motivos": motivos,
        "con_correccion": corregidos, "rescatados_por_correccion": rescatados,
        "casos": len(resultados), "pasan": pasan,
        "pasan_obligatorios_ok": pasan_obligatorios_ok,
        "enviado_ok": env_ok, "enviado_total": env_total,
    }


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "-"


def informe(resultados: Sequence[Dict[str, Any]], ev: Dict[str, Any],
            duracion_s: float, detenido: str) -> str:
    L = [f"PUNTA A PUNTA  ({ev['casos']} casos)", "=" * ANCHO,
         f"{'CAMPO':<28}{'ACIERTA':>8}{'FALLA':>7}{'CALLA':>7}"
         f"{'% resp':>8}{'% total':>9}", "-" * ANCHO]
    ta = tf = tc = 0
    for campo, t in ev["tabla"].items():
        a, f, c = t["acierta"], t["falla"], t["calla"]
        ta, tf, tc = ta + a, tf + f, tc + c
        L.append(f"{campo:<28}{a:>8}{f:>7}{c:>7}{_pct(a, a + f):>8}"
                 f"{_pct(a, a + f + c):>9}")
    L += ["-" * ANCHO,
          f"{'TOTAL':<28}{ta:>8}{tf:>7}{tc:>7}{_pct(ta, ta + tf):>8}"
          f"{_pct(ta, ta + tf + tc):>9}", ""]

    n = ev["casos"]
    L += [
        f"La compuerta deja pasar   {ev['pasan']}/{n}  ({_pct(ev['pasan'], n)})",
        f"  de esos, obligatorios 100% correctos  {ev['pasan_obligatorios_ok']}/"
        f"{ev['pasan']}  ({_pct(ev['pasan_obligatorios_ok'], ev['pasan'])})",
        f"Ronda de correccion: {ev['con_correccion']} caso(s) devueltos al agente, "
        f"{ev['rescatados_por_correccion']} rescatados (la compuerta los deja pasar)",
        f"Campos correctos de lo enviado  {ev['enviado_ok']}/{ev['enviado_total']}"
        f"  ({_pct(ev['enviado_ok'], ev['enviado_total'])})",
        "", "MOTIVOS DE NO RADICAR",
    ]
    for m, k in sorted(ev["motivos"].items(), key=lambda kv: -kv[1]):
        L.append(f"  {k:>4}  {m}")

    te = sum(r.get("tokens_entrada", 0) for r in resultados)
    ts = sum(r.get("tokens_salida", 0) for r in resultados)
    llamadas = sum(r.get("llamadas_modelo", 0) for r in resultados)
    usd = sum(r.get("costo_usd", 0) for r in resultados)
    seg = sum(r.get("duracion_s", 0) for r in resultados)
    L += ["",
          f"Modelo: {llamadas} llamadas, {te:,} tokens entrada, {ts:,} salida, "
          f"{usd:.4f} USD ({usd / n if n else 0:.4f} USD/caso)",
          f"Tiempo: {duracion_s:.0f}s de reloj; suma por caso {seg:.0f}s "
          f"({seg / n if n else 0:.1f}s/caso)"]
    errores = [r for r in resultados if r.get("error")]
    if errores:
        L += ["", f"ERRORES ({len(errores)})"]
        L += [f"  {r['caso']}: {r['error'][:120]}" for r in errores]
    if detenido:
        L += ["", "ATENCION: " + detenido]

    L += ["", f"FALLOS ({len(ev['fallos'])})", "-" * ANCHO]
    for f in sorted(ev["fallos"], key=lambda f: (f["campo"], f["caso"])):
        L.append(f"  {f['campo']:<28}{f['caso']:<12}{f['desenlace']:<11}"
                 f"{f['origen']}/{f['metodo']}  conf {f['confianza']}")
        L.append(f"      obtenido {f['obtenido']!r}   esperado {f['esperado']!r}")
    return "\n".join(L)


def _etiquetas(casos: Sequence[Caso]) -> Dict[str, Dict[str, Any]]:
    from dataclasses import asdict
    return {c.id: asdict(c.esperado) for c in casos}


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--casos", default="", help="ids separados por coma")
    p.add_argument("--n", type=int, default=0, help="solo los N primeros casos")
    p.add_argument("--trabajadores", type=int, default=5)
    p.add_argument("--sin-cache", action="store_true",
                   help="vuelve a llamar al modelo aunque haya resultado guardado")
    p.add_argument("--gastar-modelo", action="store_true",
                   help="permite llamar a Vertex (cuesta dinero)")
    p.add_argument("--tope-por-caso", type=float, default=TOPE_USD_POR_CASO,
                   help="USD medios por caso a partir de los que se detiene")
    args = p.parse_args(argv)

    casos = cargar_corpus(RAIZ_CORPUS)
    if args.casos:
        ids = {x.strip() for x in args.casos.split(",") if x.strip()}
        casos = [c for c in casos if c.id in ids]
    if args.n:
        casos = casos[:args.n]
    if not casos:
        print("ningun caso que correr")
        return 1

    inicio = time.time()
    resultados, detenido = correr(casos, args.trabajadores, args.sin_cache,
                                  args.tope_por_caso, args.gastar_modelo)
    if not resultados:
        print("nada que informar: correr con --gastar-modelo")
        return 1
    duracion = time.time() - inicio
    ev = evaluar(resultados, _etiquetas(casos))
    texto = informe(resultados, ev, duracion, detenido)
    print(texto)

    RAIZ_E2E.mkdir(parents=True, exist_ok=True)
    (RAIZ_E2E / "corrida.json").write_text(json.dumps({
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
        "argumentos": vars(args), "duracion_s": round(duracion, 1),
        "detenido": detenido,
        "resumen": {k: v for k, v in ev.items() if k != "fallos"},
        "fallos": ev["fallos"],
        "casos": [r["caso"] for r in resultados],
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (RAIZ_E2E / "informe.txt").write_text(texto, encoding="utf-8")
    return 2 if detenido else 0


if __name__ == "__main__":
    raise SystemExit(main())
