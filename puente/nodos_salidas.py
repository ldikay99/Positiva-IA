"""
Los nodos de «salidas a la medida» del motor declarativo (plan, sección 5d):
`archivo_exportar` (JSON, XML, CSV, HTML, texto), `archivo_comprimir` (ZIP) y
`documento_plantilla` (llena una plantilla de la biblioteca de Documentos:
Word, Excel, PowerPoint o correo HTML, también con la IA).

La ficha está en `especificacion_salidas.py`; los formatos y el llenado de
plantillas, en `salidas.py`. Como `nodos_producto`, este módulo no importa el
motor: se enlaza con `enlazar(modulo)`. Nada de esto escribe afuera: los archivos
quedan en el espacio del flujo y como resultado de la ejecución.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import documentos as m_doc
import salidas as m_sal
from conectores.base import ErrorDeNegocio
from especificacion_salidas import FORMATOS_EXPORTAR, FORMATOS_PLANTILLA_SALIDA, MODOS_IA, SEPARADORES, TIPOS  # noqa: F401

fd: Any = None
np_: Any = None
NOMBRE_XML = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,60}$")
EXT = {"json": "json", "xml": "xml", "csv": "csv", "html": "html", "txt": "txt", "md": "md"}
MIME_SIMPLE = {"docx": m_sal.MIME["docx"], "xlsx": m_sal.MIME["xlsx"], "pptx": m_sal.MIME["pptx"], "html": "text/html", "xml": "application/xml",
               "json": "application/json", "csv": "text/csv", "txt": "text/plain", "md": "text/markdown", "zip": "application/zip", "pdf": "application/pdf"}


def enlazar(modulo: Any) -> None:
    global fd, np_
    fd = modulo
    np_ = modulo.np_


# =====================================================================
#   lo que el motor necesita saber de cada tipo
# =====================================================================

def salidas(t: str, c: dict) -> Tuple[List[str], List[str]]:
    return ["ok"], []


def produce(t: str, c: dict, nid: str) -> Set[str]:
    return {c.get("salida") or nid}


def referencias(t: str, c: dict, plantillas_en: Callable, ref_de_fuente: Callable) -> List[Tuple[str, Set[str]]]:
    refs: List[Tuple[str, Set[str]]] = [(r, set()) for r in plantillas_en({k: v for k, v in c.items() if k not in ("archivos", "de")})]
    if t == "archivo_comprimir":
        refs += [(r, set()) for r in np_.refs_archivos(c.get("archivos"), ref_de_fuente)] + [(r, set()) for r in plantillas_en(c.get("archivos"))]
    if t == "archivo_exportar":
        refs += [(r, set()) for r in ref_de_fuente(c.get("de"))]
    d = c.get("desde_ia") if isinstance(c.get("desde_ia"), dict) else {}
    for e in [*(c.get("entradas") or []), *(d.get("entradas") or [])]:
        if isinstance(e, str):
            refs.append((fd._ref_limpia(e), set()))
    return refs


def validar(t: str, c: dict, q: Callable[[str], None]) -> None:
    def texto_si(clave: str) -> None:
        if clave in c and not isinstance(c[clave], str):
            q(f"{clave} tiene que ser texto (admite plantillas)")

    for k in ("nombre", "titulo"):
        texto_si(k)
    if t == "archivo_exportar":
        if c.get("formato") not in FORMATOS_EXPORTAR:
            q(f"archivo_exportar necesita formato ({', '.join(FORMATOS_EXPORTAR)})" if "formato" not in c
              else f"formato «{c.get('formato')}» (válidos: {', '.join(FORMATOS_EXPORTAR)})")
        tiene_de = isinstance(c.get("de"), str) and c["de"].strip()
        tiene_contenido = isinstance(c.get("contenido"), str) and c["contenido"].strip()
        if bool(tiene_de) == bool(tiene_contenido):
            q("archivo_exportar necesita de (la variable con los datos) o contenido (el texto con {{variables}}), uno de los dos")
        for k in ("de", "contenido"):
            texto_si(k)
        if "columnas" in c and not (isinstance(c["columnas"], list) and c["columnas"] and all(isinstance(x, str) and x.strip() for x in c["columnas"])):
            q("columnas tiene que ser una lista de nombres de campo")
        if "separador" in c and c["separador"] not in SEPARADORES:
            q(f"separador «{c['separador']}» (válidos: , ; tab)")
        for k in ("raiz", "elemento"):
            if k in c and not (isinstance(c[k], str) and NOMBRE_XML.match(c[k])):
                q(f"{k} tiene que ser un nombre de elemento XML (letras, números, _ - .; empieza con letra)")
        if tiene_contenido and c.get("formato") == "csv" and c.get("columnas"):
            q("columnas solo aplica con de (con contenido escribes el CSV tú)")
    elif t == "archivo_comprimir":
        if c.get("archivos") in (None, "", []):
            q("archivo_comprimir necesita archivos: los que van en el ZIP («{{documento}}», «adjuntos», ids o filtros)")
        else:
            np_.problemas_archivos(c, "archivos", q)
    elif t == "documento_plantilla":
        p = c.get("plantilla")
        if not (isinstance(p, str) and p.strip()):
            q("documento_plantilla necesita plantilla: el id de una plantilla de la biblioteca de Documentos")
        elif "{{" not in p and not m_sal.ID_PLANTILLA.match(p.strip()):
            q("plantilla es el id de una plantilla de Documentos (minúsculas, números y guiones, p. ej. «carta-respuesta»)")
        fd._validar_mapa_plantillas(c, "datos", q)
        if "formato" in c and c["formato"] not in FORMATOS_PLANTILLA_SALIDA:
            q(f"formato «{c['formato']}» (válidos: original o pdf)")
        if c.get("desde_ia") is not None:
            np_.problemas_desde_ia(c.get("desde_ia"), q)
            d = c["desde_ia"] if isinstance(c["desde_ia"], dict) else {}
            if "modo" in d and d["modo"] not in MODOS_IA:
                q(f"desde_ia.modo «{d['modo']}» (válidos: llenar o completo)")
            if "variables" in d and not (isinstance(d["variables"], list) and all(isinstance(x, str) and x.strip() for x in d["variables"])):
                q("desde_ia.variables tiene que ser una lista de nombres de variables de la plantilla")


def bytes_de_bloques(formato: str, bloques: List[Tuple[str, Any]], titulo: str) -> bytes:
    """Bloques (de un texto, de un Word o de la IA) en el formato pedido: pdf, docx, html o pptx."""
    if formato == "pdf":
        return m_doc.pdf_de_bloques(bloques, titulo)
    if formato == "html":
        return m_sal.html_de_bloques(bloques, titulo)
    if formato == "pptx":
        return m_sal.pptx_de_presentacion(m_sal.presentacion_de_bloques(bloques, titulo))
    return m_doc.docx_de_bloques(bloques, titulo)


def _con_ext(nombre: str, ext: str) -> str:
    base = re.sub(r'[\x00-\x1f\x7f<>:"|?*/\\]', "", nombre or "").strip() or "archivo"
    return base if base.lower().endswith(f".{ext}") else f"{base}.{ext}"


def _escapar_json(t: str) -> str:
    """Un valor dentro de un texto JSON («"nombre": "{{x}}"»): comillas y saltos escapados."""
    return json.dumps(t, ensure_ascii=False)[1:-1]


def _vacio(v: Any) -> bool:
    return v is None or v == "" or v == []


def _clave_ia(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", ref.replace(".", "__"))


def _poner_ruta(destino: Dict[str, Any], ref: str, valor: Any, base: Callable[[str], Any]) -> None:
    """«cliente.nombre» = valor en un ámbito, sin perder los demás campos que «cliente» ya tenía en el flujo."""
    partes = ref.split(".")
    if len(partes) == 1:
        destino[ref] = valor
        return
    raiz = partes[0]
    if raiz not in destino:
        previo = base(raiz)
        destino[raiz] = dict(previo) if isinstance(previo, dict) else {}
    nodo = destino[raiz]
    for p in partes[1:-1]:
        if not isinstance(nodo.get(p), dict):
            nodo[p] = {}
        nodo = nodo[p]
    nodo[partes[-1]] = valor


class NodosSalidas:
    """Los métodos de los nodos de salidas a la medida; Ejecucion (el motor) hereda de aquí."""

    # ----------------------------------------------------------- utilidades
    def _imagen_de_ref(self, ref: str) -> Optional[bytes]:
        """Una imagen de la ejecución por su referencia («imagenes.0», «logo_generado», un id «a-…»)."""
        v = self.resolver(ref) if not fd.ID_ARCHIVO.fullmatch(ref) else ref
        if isinstance(v, list):
            v = v[0] if v else None
        if v in (None, ""):
            return None
        try:
            fichas = self._fichas([v])
        except ErrorDeNegocio:
            return None
        if not fichas:
            return None
        datos = self._bytes(fichas[0])
        return datos if (datos[:8] == b"\x89PNG\r\n\x1a\n" or datos[:3] == b"\xff\xd8\xff" or (fichas[0].get("tipo") or "").startswith("image/")) else None

    def _contexto_salidas(self, pid: str = "", extra: Optional[Dict[str, Any]] = None, contenido: Any = None) -> "m_sal.Contexto":
        def imagen(ref: str) -> Optional[bytes]:
            return (m_sal.recurso_de(pid, ref) if pid else None) or self._imagen_de_ref(ref)
        ctx = m_sal.Contexto(self.resolver, imagen, fd._a_texto, contenido)
        if extra is not None:
            ctx.ambitos.append(extra)  # el mismo objeto: lo que escriba la IA después también se ve
        return ctx

    # ======================================================= exportar datos
    def archivo_exportar(self, n: dict) -> str:
        c = n.get("config") or {}
        formato = c.get("formato") or "json"
        columnas = [str(x) for x in c.get("columnas") or []] or None
        filas = None
        if isinstance(c.get("contenido"), str) and c["contenido"].strip():
            ctx = m_sal.Contexto(self.resolver, None, fd._a_texto)
            escapar = m_sal.escapar_xml if formato in ("xml", "html") else _escapar_json if formato == "json" else None
            datos = ctx.texto(c["contenido"], escapar).encode("utf-8")
            if formato == "csv":
                datos = "﻿".encode("utf-8") + datos
        else:
            valor = self.valor_de(c.get("de"))
            if isinstance(valor, str) and valor.strip()[:1] in ("[", "{"):
                try:
                    valor = json.loads(valor)
                except ValueError:
                    pass
            if formato == "json":
                datos = m_sal.json_de_valor(valor)
            elif formato == "xml":
                datos = m_sal.xml_de_valor(valor, c.get("raiz") or "datos", c.get("elemento") or "elemento")
            elif formato == "csv":
                datos = m_sal.csv_de_valor(valor, columnas, "\t" if c.get("separador") == "tab" else (c.get("separador") or ","))
            elif formato == "html":
                datos = m_sal.html_de_valor(valor, self.render(c.get("titulo") or ""), columnas)
            else:
                texto = fd._a_texto(valor)
                if isinstance(valor, list) and formato == "md":
                    t = m_sal._filas_de(valor, columnas)
                    if t:
                        cab, cuerpo = t
                        texto = "\n".join(["| " + " | ".join(cab) + " |", "| " + " | ".join("---" for _ in cab) + " |"]
                                          + ["| " + " | ".join(x.replace("|", "\\|") for x in f) + " |" for f in cuerpo])
                datos = (texto + ("\n" if not texto.endswith("\n") else "")).encode("utf-8")
            t = m_sal._filas_de(valor, columnas) if isinstance(valor, (list, dict)) else None
            filas = len(t[1]) if t else (len(valor) if isinstance(valor, list) else None)
        nombre = _con_ext(self.render(c.get("nombre") or "") or "datos", EXT[formato])
        ficha = self._guardar_archivo(nombre, datos, MIME_SIMPLE[formato])
        if filas is not None:
            ficha["filas"] = filas
        self.vars[self._np_salida(n)] = ficha
        fd._paso(n["id"], f"Archivo {ficha['nombre']}", "ok", f"{formato.upper()} · {ficha['tamano']:,} bytes" + (f" · {filas} fila(s)" if filas is not None else "")
                 + f" · id {ficha['id']}", archivo=ficha["id"])
        return "ok"

    # ============================================================ comprimir
    def archivo_comprimir(self, n: dict) -> str:
        c = n.get("config") or {}
        fichas = self._archivos_ref(c.get("archivos"))
        if not fichas:
            raise ErrorDeNegocio("archivo_comprimir: las referencias no trajeron ningún archivo")
        if len(fichas) > m_sal.MAX_ZIP:
            raise ErrorDeNegocio(f"archivo_comprimir: son {len(fichas)} archivos y un ZIP lleva hasta {m_sal.MAX_ZIP}")
        try:
            datos, nombres = m_sal.zip_de([(f["nombre"], self._bytes(f)) for f in fichas], np_.MAX_BYTES)
        except m_sal.ErrorDePlantilla as e:
            raise ErrorDeNegocio(f"archivo_comprimir: {e}") from e
        ficha = self._guardar_archivo(_con_ext(self.render(c.get("nombre") or "") or "archivos", "zip"), datos, "application/zip")
        ficha.update({"archivos": nombres, "cantidad": len(nombres)})
        self.vars[self._np_salida(n)] = ficha
        fd._paso(n["id"], f"{len(nombres)} archivo(s) en {ficha['nombre']}", "ok", ", ".join(nombres)[:700] + f" · {ficha['tamano']:,} bytes · id {ficha['id']}",
                 archivo=ficha["id"])
        return "ok"

    # =================================================== plantilla de la biblioteca
    def documento_plantilla(self, n: dict) -> str:
        c = n.get("config") or {}
        pid = self.render(c.get("plantilla") or "").strip()
        try:
            meta, original = m_sal.leer_plantilla(pid)
        except m_sal.ErrorDePlantilla as e:
            raise ErrorDeNegocio(str(e)) from e
        fmt = meta.get("formato") or m_sal.formato_de(str(meta.get("archivo") or ""), original)
        if fmt not in m_sal.FORMATOS_PLANTILLA:
            raise ErrorDeNegocio(f"la plantilla «{pid}» es {fmt or 'de un formato desconocido'}: tiene que ser Word, Excel, PowerPoint o HTML")
        salida_fmt = c.get("formato") or "original"
        if salida_fmt == "pdf" and fmt not in ("docx", "html"):
            raise ErrorDeNegocio(f"formato pdf solo con plantillas de Word o HTML (esta es {fmt})")
        analisis = m_sal.analizar(original, fmt)
        datos_cfg = self.render_profundo(c.get("datos") or {}) if isinstance(c.get("datos"), dict) else {}
        extra: Dict[str, Any] = dict(datos_cfg)
        ctx = self._contexto_salidas(pid, extra)
        contenido = None
        detalle_ia = ""
        if isinstance(c.get("desde_ia"), dict):
            valores, contenido, detalle_ia = self._plantilla_desde_ia(n, c["desde_ia"], meta, fmt, analisis, ctx)
            for ref, v in valores.items():
                _poner_ruta(extra, ref, v, self.resolver)
            ctx.contenido = contenido
        try:
            lleno = m_sal.llenar(fmt, original, ctx)
        except m_sal.ErrorDePlantilla as e:
            raise ErrorDeNegocio(f"plantilla «{pid}»: {e}") from e
        titulo = str(meta.get("nombre") or pid)
        ext = fmt
        if salida_fmt == "pdf":
            bloques = m_doc.bloques_docx(lleno) if fmt == "docx" else m_doc.bloques_de_texto(m_sal._texto_de_html(lleno.decode("utf-8")))
            lleno, ext = m_doc.pdf_de_bloques(bloques, titulo), "pdf"
        nombre = _con_ext(self.render(c.get("nombre") or "") or re.sub(r"[^\w-]+", "-", titulo).strip("-").lower() or pid, ext)
        ficha = self._guardar_archivo(nombre, lleno, MIME_SIMPLE[ext])
        faltantes = sorted(x for x in ctx.faltantes if not x.startswith(("item.", "#")) and x not in ctx.bloqueadas)
        ficha.update({"plantilla": pid, "formato": ext, "faltantes": faltantes, "variables": analisis["variables"]})
        if ext == "pdf":
            ficha["paginas"] = m_doc.cantidad_paginas(lleno)
        if fmt == "html" and ext == "html":
            ficha["html"] = lleno.decode("utf-8")[:300_000]
        if meta.get("asunto"):
            ficha["asunto"] = ctx.texto(str(meta["asunto"]))
        self.vars[self._np_salida(n)] = ficha
        if ctx.bloqueadas:
            self.avisos.append(f"{n['id']}: la plantilla «{pid}» pide variables de entorno ({', '.join(sorted(ctx.bloqueadas))[:200]}): desde una plantilla no se leen")
        if faltantes:
            self.avisos.append(f"{n['id']}: en «{pid}» quedaron vacías: {', '.join(faltantes)[:300]}")
        if ctx.sin_imagen:
            self.avisos.append(f"{n['id']}: en «{pid}» no se encontró la imagen de: {', '.join(sorted(ctx.sin_imagen))[:200]}")
        fd._paso(n["id"], f"Plantilla «{titulo}» llena: {ficha['nombre']}", "aviso" if faltantes else "ok",
                 (detalle_ia + " · " if detalle_ia else "") + f"{len(ctx.usadas)} variable(s) puestas" + (f", vacías: {', '.join(faltantes)[:300]}" if faltantes else "")
                 + f" · {ficha['tamano']:,} bytes · id {ficha['id']}", archivo=ficha["id"])
        return "ok"

    def _plantilla_desde_ia(self, n: dict, d: dict, meta: dict, fmt: str, analisis: dict, ctx: "m_sal.Contexto") -> Tuple[Dict[str, Any], Any, str]:
        """La IA escribe las variables que faltan (y las pedidas) y, en modo completo, el contenido. (valores, contenido, detalle)."""
        modo = d.get("modo") or "llenar"
        pedidas = [str(x).strip() for x in d.get("variables") or [] if str(x).strip()]
        faltan = [v for v in analisis["variables"] if v in pedidas or _vacio(ctx.valor(v))]
        faltan = [v for v in dict.fromkeys(faltan + [p for p in pedidas if p not in faltan]) if not v.startswith(m_sal.PROHIBIDAS) and v not in ("indice",)]
        listas = {r: campos for r, campos in analisis["repeticiones"].items() if r in pedidas or _vacio(ctx.valor(r))}
        props: Dict[str, Any] = {}
        props_valores: Dict[str, Any] = {_clave_ia(v): {"type": "string"} for v in faltan}
        for r, campos in listas.items():
            props_valores[_clave_ia(r)] = ({"type": "array", "items": {"type": "object", "properties": {k: {"type": "string"} for k in campos}}}
                                           if campos else {"type": "array", "items": {"type": "string"}})
        if props_valores:
            props["valores"] = {"type": "object", "properties": props_valores}
        if modo == "completo":
            props["contenido"] = m_sal.ESQUEMA_PRESENTACION if fmt == "pptx" else m_doc.ESQUEMA_HOJAS if fmt == "xlsx" else m_doc.ESQUEMA_DOCUMENTO
        if not props:
            self.avisos.append(f"{n['id']}: la plantilla no tiene variables vacías para la IA (pon desde_ia.variables o modo completo)")
            return {}, None, "la IA no tuvo nada que llenar"
        que = {"docx": "un documento de Word", "xlsx": "una hoja de Excel", "pptx": "una presentación de PowerPoint", "html": "un correo o documento HTML"}[fmt]
        pedido = []
        if props_valores:
            pedido.append("En «valores» escribe el texto de cada variable pedida (" + ", ".join(props_valores) + "): "
                          "las claves con «__» son rutas (cliente__nombre es {{cliente.nombre}}); las listas son filas con sus campos. "
                          "No inventes datos: si no sabes uno, déjalo vacío.")
        if modo == "completo":
            pedido.append({"pptx": "En «contenido» arma la presentación: titulo y diapositivas (cada una con titulo, puntos cortos, notas del orador y, si "
                                   "sirve, una tabla {columnas, filas}).",
                           "xlsx": "En «contenido» arma las hojas: cada una con nombre, columnas (encabezados cortos) y filas (una lista de valores por fila).",
                           }.get(fmt, "En «contenido» redacta el documento: titulo y secciones (cada una con titulo, parrafos, lista de viñetas si sirve y "
                                      "una tabla {columnas, filas} si hace falta). Sin markdown dentro de los textos."))
        sistema = (f"Llenas la plantilla «{meta.get('nombre') or meta.get('id')}» ({que}) en español de Colombia, claro y formal, usando solo lo que "
                   f"recibes. {' '.join(pedido)}\n\nTexto de la plantilla (para saber qué significa cada variable):\n{analisis.get('texto', '')[:3500]}")
        r = self._desde_ia(n, d, {"type": "object", "properties": props}, sistema)
        if not isinstance(r, dict):
            raise ErrorDeNegocio("la IA no devolvió los valores de la plantilla (un JSON con valores y contenido): revisa las instrucciones o prueba otro modelo")
        crudos = r.get("valores") if isinstance(r.get("valores"), dict) else {}
        valores: Dict[str, Any] = {}
        for v in faltan:
            x = crudos.get(_clave_ia(v), crudos.get(v))
            if x not in (None, ""):
                valores[v] = x if isinstance(x, (str, int, float)) else fd._a_texto(x)
        for lista in listas:
            x = crudos.get(_clave_ia(lista), crudos.get(lista))
            if isinstance(x, list):
                valores[lista] = x[:m_sal.MAX_REPETICIONES]
        contenido = None
        if modo == "completo":
            bruto = r.get("contenido")
            if fmt == "pptx":
                contenido = m_sal.presentacion_limpia(bruto) if isinstance(bruto, dict) else None
                contenido = contenido if contenido and contenido["diapositivas"] else None
            elif fmt == "xlsx":
                contenido = m_doc.hojas_limpias(bruto) or None
            else:
                contenido = m_doc.bloques_de_estructura(bruto) if isinstance(bruto, dict) and bruto.get("secciones") else None
            if contenido is None:
                raise ErrorDeNegocio("la IA no devolvió el contenido del documento: revisa las instrucciones o prueba otro modelo")
        partes = [f"{len(valores)} variable(s) escritas por la IA"] if props_valores else []
        if contenido is not None:
            partes.append({"pptx": f"{len(contenido['diapositivas']) if isinstance(contenido, dict) else 0} diapositiva(s)",
                           "xlsx": f"{len(contenido)} hoja(s)"}.get(fmt, f"{sum(1 for b in contenido if b[0] in ('subtitulo', 'titulo'))} sección(es)") + " redactadas por la IA")
        return valores, contenido, " · ".join(partes)
