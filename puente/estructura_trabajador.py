"""
La estructura editada de un flujo de PQRD, dentro del trabajador.

Para una orden con `estructura` se instalan, solo mientras dura la orden, los
cambios que el flujo pide sobre el motor (que no se toca): etapas apagadas,
lectores por tipo de archivo, cupo de visión, el intercambio de volumen y
deduplicación por contenido, y los pasos insertados en sus puntos. Al terminar
se quita todo y el siguiente flujo corre como el del código.
"""
from __future__ import annotations

import asyncio
import copy
import functools
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import estructura_codigo as ec

_parches: List[Tuple[Any, str, Any]] = []
_constantes: Dict[str, Any] = {}
# Las copias de los adjuntos que leen los pasos: se borran al terminar la orden (son datos del correo).
_carpetas: List[Path] = []


def _parchear(objeto: Any, nombre: str, nuevo: Any) -> None:
    _parches.append((objeto, nombre, getattr(objeto, nombre)))
    setattr(objeto, nombre, nuevo)


def quitar() -> None:
    from pipeline import conversion

    for objeto, nombre, original in reversed(_parches):
        setattr(objeto, nombre, original)
    _parches.clear()
    if "max_imagenes" in _constantes:
        conversion.MAX_IMAGENES_IA_POR_CORREO = _constantes.pop("max_imagenes")
    for carpeta in _carpetas:
        shutil.rmtree(carpeta, ignore_errors=True)
    _carpetas.clear()


def _correo_de(email: Any) -> Dict[str, str]:
    from utils.html_cleaner import clean_html_content

    de = email.from_address
    return {"asunto": email.subject or "", "cuerpo": clean_html_content(email.body.content or "") if email.body else "",
            "remitente": f"{de.name or ''} <{de.address or ''}>" if de else "", "remitente_correo": de.address if de else "",
            "remitente_nombre": de.name if de else "", "recibido": str(getattr(email, "received_date_time", "") or "")}


def instalar(tr: Any, orden: Dict[str, Any]) -> Optional[str]:
    """Instala la estructura de la orden; devuelve un error si no se puede correr así."""
    est = (orden.get("estructura") or {}).get("definicion")
    if not est:
        return None
    if tr.REAL:
        return "este flujo tiene la estructura editada: con la radicación real solo corre la estructura del código"
    v = ec.validar("pqrd", est)
    if not v["ok"]:
        return "la estructura del flujo no es válida: " + "; ".join(v["problemas"][:6])
    from pipeline import cache_conversion as cache_motor, conversion, duplicados, facturas, orquestador, volumen
    from pipeline.enrutamiento import Enrutamiento

    O = orquestador.Orquestador
    nodos = {n["id"]: n for n in est["nodos"]}
    conf = {i: (n.get("config") or {}) for i, n in nodos.items() if n.get("tipo") == "etapa"}
    apagadas = set(v["apagadas"])
    tramos = {t["punto"]: t for t in v["tramos"]}
    pasos = orden.get("pasos") or {}
    tr.evento("estructura", version=(orden.get("estructura") or {}).get("version"), apagadas=sorted(apagadas),
              intercambio=v["intercambio"], tramos=[{"punto": t["punto"], "nodos": t["nodos"]} for t in v["tramos"]],
              lectores=conf.get("conversion_a_markdown", {}).get("lectores") or {})

    def apagada(etapa: str) -> None:
        tr.evento("etapa_inicio", etapa=etapa)
        tr._paso(etapa, "Apagada en este flujo", "info", ec.BASES["pqrd"]["apagables"].get(etapa, ""), apagada=True)
        tr.evento("etapa_fin", etapa=etapa, ms=0, fallo="")

    # --- etapas apagadas
    if "deduplicacion" in apagadas:
        async def sin_dedup(self, email):
            apagada("deduplicacion")
            return duplicados.Veredicto()
        _parchear(duplicados.Detector, "comprobar", sin_dedup)
    if "deduplicacion_contenido" in apagadas:
        async def sin_dedup_contenido(self, email, nombres=None, *a, **k):
            apagada("deduplicacion_contenido")
            return duplicados.Veredicto()
        _parchear(duplicados.Detector, "comprobar_contenido", sin_dedup_contenido)
    if "enrutamiento" in apagadas:
        def sin_ruta(*a, **k):
            apagada("enrutamiento")
            return Enrutamiento()
        _parchear(orquestador, "decidir", sin_ruta)
    if "facturas" in apagadas:
        es_factura = facturas.es_factura

        @functools.wraps(es_factura)
        def sin_facturas(*a, **k):
            r = es_factura(*a, **k)
            if r and facturas.MOTIVO == tr._motivo_facturas.get("original"):
                tr._paso("facturas", "Parece factura, pero el filtro está apagado en este flujo: sigue", "info", str(r)[:300])
                return ""
            return r
        _parchear(facturas, "es_factura", sin_facturas)
    if "remitente_identificable" in apagadas:
        def con_remitente(*a, **k):
            apagada("remitente_identificable")
            return True
        _parchear(orquestador, "tiene_remitente_externo_identificable", con_remitente)
    if "volumen" in apagadas:
        medir = volumen.medir

        def sin_topes(adjuntos, *a, **k):
            r = medir(adjuntos, *a, **k)
            if r.excesos:
                tr._paso("volumen", "Se pasa de los topes, pero el control está apagado en este flujo: sigue", "info", "; ".join(r.excesos))
            r.excesos = []
            return r
        _parchear(volumen, "medir", sin_topes)
    if "registro_de_personas" in apagadas:
        def sin_registro(*a, **k):
            apagada("registro_de_personas")
            return {}
        _parchear(orquestador, "rellenar_desde_el_registro", sin_registro)
    if "revisor" in apagadas:
        async def sin_revisor(self, ctx, determinista, hallazgos, dictamen, *a, **k):
            apagada("revisor")
            return hallazgos, dictamen
        _parchear(O, "_revision_obligatoria", sin_revisor)
    max_imagenes = 0 if "transcripcion_vision" in apagadas else conf.get("transcripcion_vision", {}).get("max_imagenes")
    if isinstance(max_imagenes, int):
        _constantes["max_imagenes"] = conversion.MAX_IMAGENES_IA_POR_CORREO
        conversion.MAX_IMAGENES_IA_POR_CORREO = max_imagenes
        if "transcripcion_vision" in apagadas:
            tr._paso("transcripcion_vision", "Apagada en este flujo", "info", ec.BASES["pqrd"]["apagables"]["transcripcion_vision"], apagada=True)

    # --- lectores por tipo de archivo
    lectores = {str(k).lower().lstrip("."): m for k, m in (conf.get("conversion_a_markdown", {}).get("lectores") or {}).items()}
    if lectores:
        convertir = cache_motor.convertir

        @functools.wraps(convertir)
        def con_lectores(datos, nombre, claves_candidatas=None, presupuesto=None):
            ext = nombre.lower().rsplit(".", 1)[-1] if "." in nombre else ""
            modo = lectores.get(ext) or (lectores.get("imagen") if ext in ec.EXT_IMAGEN else None) or "automatico"
            if modo == "automatico":
                return convertir(datos, nombre, claves_candidatas, presupuesto=presupuesto)
            tr._paso("conversion_a_markdown", f"{nombre}: lector «{modo}» (de este flujo)", "info", ec.LECTORES[modo])
            if modo == "omitir":
                return conversion.Conversion(tipo_real=ext or "?", aviso=f"este flujo no lee los .{ext}")
            if modo == "vision":
                tipo = "heic" if conversion.detectar_tipo(datos, nombre) == "heic" else "imagen"
                return conversion.imagen_con_vision(datos, nombre, presupuesto, tipo_real=tipo)
            cupo = presupuesto.imagenes_ia if presupuesto is not None else None
            if presupuesto is not None:
                presupuesto.imagenes_ia = 0
            try:
                return convertir(datos, nombre, claves_candidatas, presupuesto=presupuesto)
            finally:
                if presupuesto is not None:
                    presupuesto.imagenes_ia = cupo
        _parchear(cache_motor, "convertir", con_lectores)

    # --- volumen y deduplicación por contenido intercambiadas
    if v["intercambio"]:
        descargar = O._descargar_adjuntos

        async def descargar_y_comparar(self, email, bitacora):
            adjuntos = await descargar(self, email, bitacora)
            tr._paso("deduplicacion_contenido", "Se compara el contenido antes del control de volumen (orden de este flujo)", "info")
            tr._actual["dedup_adelantado"] = await self.duplicados.comprobar_contenido(email, [n for n, _ in adjuntos])
            return adjuntos
        _parchear(O, "_descargar_adjuntos", descargar_y_comparar)
        comprobar = duplicados.Detector.comprobar_contenido

        async def ya_comparado(self, email, nombres=None, *a, **k):
            if tr._actual.get("dedup_adelantado") is not None:
                return tr._actual.pop("dedup_adelantado")
            return await comprobar(self, email, nombres, *a, **k)
        _parchear(duplicados.Detector, "comprobar_contenido", ya_comparado)
        medir_v = volumen.medir

        def medir_si_no_es_duplicado(adjuntos, *a, **k):
            r = medir_v(adjuntos, *a, **k)
            if getattr(tr._actual.get("dedup_adelantado"), "es_duplicado", False):
                r.excesos = []  # es duplicado: se archiva como tal, sin mirar el volumen
            return r
        _parchear(volumen, "medir", medir_si_no_es_duplicado)

    # --- pasos insertados
    def correr(punto: str, contexto: Dict[str, Any]) -> Dict[str, Any]:
        from pipeline import conversion as conv

        transcriptor = conv.TRANSCRIPTOR_IA  # leer_adjuntos pone el suyo: el del motor vuelve al terminar
        try:
            r = ec.correr_segmento(tramos[punto], est, "pqrd", contexto, pasos, tr._actual["corrida"], tr._salida, tr._cerrojo)
        except Exception as e:
            r = {"desenlace": "error", "motivo": f"{type(e).__name__}: {e}"[:300], "variables": {}, "iniciales": {}, "texto_leido": "", "leidos": [], "avisos": []}
        finally:
            conv.TRANSCRIPTOR_IA = transcriptor
        resumen = {"punto": punto, "desenlace": r["desenlace"], "motivo": r["motivo"], "avisos": r.get("avisos", [])[:5]}
        tr.evento("pasos_insertados", **resumen)
        return r

    if "al_entrar" in tramos:
        es_factura_actual = facturas.es_factura

        @functools.wraps(es_factura_actual)
        def con_pasos_al_entrar(asunto: str = "", cuerpo: str = "", *a, **k):
            c = tr._actual.get("correo") or {}
            r = correr("al_entrar", {"correo": {"asunto": asunto, "cuerpo": cuerpo, "remitente": c.get("remitente", "")},
                                     "adjuntos": [], "adjuntos_nombres": c.get("adjuntos", [])})
            if r["desenlace"] == "manual":
                facturas.MOTIVO = r["motivo"] or "PASO_DEL_FLUJO"
                return f"paso del flujo en «Al entrar»: {r['motivo'] or 'revisión manual'}"
            return es_factura_actual(asunto, cuerpo, *a, **k)
        _parchear(facturas, "es_factura", con_pasos_al_entrar)

    if tramos.keys() & {"con_los_documentos", "antes_de_validar", "tras_radicar"}:
        materializar = orquestador.materializar

        def materializar_con_pasos(**k):
            ws = materializar(**k)
            carpeta = Path(tr.TRABAJO) / "pasos" / str(tr._actual["corrida"])
            shutil.rmtree(carpeta, ignore_errors=True)
            carpeta.mkdir(parents=True, exist_ok=True)
            _carpetas.append(carpeta)
            archivos = []
            for i, (nombre, datos) in enumerate(k.get("adjuntos") or []):
                limpio = re.sub(r"[^\w.\- ]", "_", nombre)[:120] or f"adjunto{i}"
                ruta = carpeta / f"{i:03d}-{limpio}"
                ruta.write_bytes(datos or b"")
                archivos.append((nombre, ruta))
            tr._actual["pasos_adjuntos"] = (carpeta, archivos)
            if "con_los_documentos" in tramos:
                _con_los_documentos(tr, ws, carpeta, archivos, k, correr)
            return ws
        _parchear(orquestador, "materializar", materializar_con_pasos)

    if "antes_de_validar" in tramos:
        validar_y_corregir = O._validar_y_corregir

        async def con_pasos_antes_de_validar(self, ctx, determinista, hallazgos, resolucion, bitacora, res, email, cuerpo,
                                             texto_adjuntos, ws, consultas_del_agente):
            from pipeline.extraccion import Hallazgo

            carpeta, archivos = tr._actual.get("pasos_adjuntos") or (None, [])
            campos = {c: getattr(h, "valor", h) for c, h in hallazgos.items()}
            r = await asyncio.to_thread(correr, "antes_de_validar", {
                "correo": _correo_de(email), "adjuntos": archivos, "carpeta": str(carpeta or ""), "adjuntos_texto": texto_adjuntos,
                "variables": {"campos": copy.deepcopy(campos)}})
            conocidos = set(campos) | set(next(p for p in ec.BASES["pqrd"]["puntos"] if p["id"] == "antes_de_validar")["campos"])
            nuevos = {c: v for c, v in r["variables"].items() if c in conocidos and r["iniciales"].get(c) != v}
            nombre_paso = ", ".join(tramos["antes_de_validar"]["nodos"])
            for campo, valor in nuevos.items():
                if campo in campos and str(campos[campo]) == str(valor):
                    continue
                if valor in (None, ""):
                    continue
                hallazgos[campo] = Hallazgo(campo=campo, valor=valor, evidencia=f"lo fijó un paso de este flujo ({nombre_paso})",
                                            confianza=1.0, metodo="paso_del_flujo")
                res.avisos.append(f"{campo} lo fijó un paso del flujo: «{str(valor)[:80]}»")
            if r["desenlace"] == "manual":
                from pipeline import compuerta

                dictamen = self._validar_de_nuevo(hallazgos, ctx, resolucion, email, cuerpo, texto_adjuntos, ws, consultas_del_agente)
                dictamen.reparos.append(compuerta.Reparo(campo="", codigo=r["motivo"] or "PASO_DEL_FLUJO",
                                                         detalle=f"un paso del flujo lo manda a revisión manual ({nombre_paso})", bloqueante=True))
                dictamen.veredicto = compuerta.Veredicto.RECHAZADO
                dictamen.motivo_manual = r["motivo"] or "PASO_DEL_FLUJO"
                return hallazgos, dictamen
            return await validar_y_corregir(self, ctx, determinista, hallazgos, resolucion, bitacora, res, email, cuerpo,
                                            texto_adjuntos, ws, consultas_del_agente)
        _parchear(O, "_validar_y_corregir", con_pasos_antes_de_validar)

    if "tras_radicar" in tramos:
        radicar = O._radicar

        async def radicar_y_pasos(self, email, hallazgos, bitacora, *a, **k):
            radicado = await radicar(self, email, hallazgos, bitacora, *a, **k)
            if radicado:
                carpeta, archivos = tr._actual.get("pasos_adjuntos") or (None, [])
                await asyncio.to_thread(correr, "tras_radicar", {
                    "correo": _correo_de(email), "adjuntos": archivos, "carpeta": str(carpeta or ""),
                    "variables": {"campos": {c: getattr(h, "valor", h) for c, h in hallazgos.items()}, "radicado": radicado}})
            return radicado
        _parchear(O, "_radicar", radicar_y_pasos)
    return None


def _con_los_documentos(tr: Any, ws: Any, carpeta: Path, archivos: List[Tuple[str, Path]], k: Dict[str, Any],
                        correr: Callable[[str, Dict[str, Any]], Dict[str, Any]]) -> None:
    """Corre los pasos con los documentos ya leídos; lo que leen entra al workspace como un documento más."""
    from pipeline.workspace import ESTADO_OK, ESTADO_VACIO, EntradaManifiesto

    m = ws.manifiesto
    leidos = [e.nombre_original for e in m.convertidos]
    no_leidos = [e.nombre_original for e in m.fallidos]
    texto = "\n\n".join(ws.leer(Path(e.ruta_md).stem) for e in m.convertidos if e.ruta_md)
    de = {"asunto": k.get("asunto", ""), "cuerpo": k.get("cuerpo_limpio", ""), "remitente_nombre": k.get("remitente_nombre", ""),
          "remitente_correo": k.get("remitente_correo", ""), "remitente": f"{k.get('remitente_nombre', '')} <{k.get('remitente_correo', '')}>"}
    r = correr("con_los_documentos", {"correo": de, "adjuntos": archivos, "carpeta": str(carpeta), "adjuntos_texto": texto,
                                      "adjuntos_leidos": leidos, "variables": {"no_leidos": no_leidos, "documento_extra": ""}})
    extra = r["variables"].get("documento_extra")
    partes = [x for x in (r.get("texto_leido") or "", extra if isinstance(extra, str) else "") if x and x.strip()]
    if not partes:
        return
    nombre = "pasos del flujo (con los documentos).md"
    md = f"# {nombre}\n\n" + "\n\n".join(partes)
    ruta = ws.escribir_adjunto(len(m.entradas) + 1, nombre, md)
    m.agregar(EntradaManifiesto(nombre_original=nombre, ruta_md=str(ruta.relative_to(ws.raiz)), tipo_real="paso_del_flujo",
                                estado=ESTADO_OK, caracteres_md=len(md), ocr_aplicado=True))
    rescatados = [n for n in r.get("leidos") or [] if n in no_leidos]
    for e in m.entradas:
        if e.nombre_original in rescatados and e.estado != ESTADO_OK:
            e.estado, e.detalle = ESTADO_VACIO, f"el motor no lo leyó; lo leyó un paso del flujo (va en «{nombre}»)"
    m.avisos[:] = [a for a in m.avisos if not any(f"'{n}'" in a for n in rescatados)]
    ws.escribir_manifiesto()
    tr._paso("conversion_a_markdown", f"Los pasos del flujo agregaron un documento ({len(md):,} caracteres)".replace(",", "."), "ok",
             (f"leyeron lo que el motor no: {', '.join(rescatados)}" if rescatados else "texto agregado para el agente"))
