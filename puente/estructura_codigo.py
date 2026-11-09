"""
La estructura editable de los flujos de código (PQRD, tutelas y sus copias).

El código del motor no cambia: cada etapa del catálogo es un nodo «etapa» y la
estructura de un flujo dice qué se hace distinto en ESE flujo:
  - apagar una etapa donde el motor lo admite (`config.deshabilitado`);
  - reconfigurarla (`config.lectores` de la conversión, `config.max_imagenes` de
    la visión);
  - intercambiar dos etapas que no dependen una de la otra;
  - insertar nodos declarativos entre dos etapas, en los puntos donde el
    trabajador puede correrlos con los datos de ese momento.

Sin estructura guardada el flujo es el del código, igual que en producción.
Aquí viven las reglas (una sola fuente): el servidor valida con
`python estructura_codigo.py validar` y el trabajador corre los segmentos con
`correr_segmento`.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

RAIZ = Path(__file__).resolve().parent.parent
CATALOGOS = {"pqrd": Path(os.environ.get("RUTA_CATALOGO", RAIZ / "catalogo" / "catalogo.json")),
             "tutelas": RAIZ / "catalogo" / "tutelas.json"}

ID_NODO = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
LECTORES = {"automatico": "como el motor (texto propio, OCR y visión en lo que el OCR no lee)",
            "ocr": "solo OCR, sin visión",
            "vision": "directo a la visión del modelo, sin OCR (solo imágenes)",
            "omitir": "no se lee (queda como adjunto sin contenido)"}
EXT_IMAGEN = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp", "avif", "heic", "heif"}
EXT_LEIBLES = EXT_IMAGEN | {"pdf", "docx", "xlsx", "xlsm", "txt", "csv", "xml", "eml", "p7m", "zip"}

# Lo que no tiene sentido dentro de un flujo de código: otro disparador, responder a un webhook,
# esperar a una persona (el job no se detiene) o llamar a otro flujo.
NO_INSERTABLES = {"respuesta_webhook", "aprobacion_humana", "llamar_flujo", "esperar", "fin_ciclo"}

BASES: Dict[str, Dict[str, Any]] = {
    "pqrd": {
        "puntos": [
            {"id": "al_entrar", "entre": ["enrutamiento", "facturas"], "nombre": "Al entrar",
             "descripcion": "Después del enrutamiento y antes del filtro de facturas: todavía no se abren los adjuntos.",
             "datos": ["correo.asunto", "correo.cuerpo", "correo.remitente", "correo.remitente_correo", "adjuntos.nombres", "adjuntos.cantidad"],
             "manual": True,
             "efectos": "Una salida con desenlace «manual» manda el correo a revisión manual con su motivo."},
            {"id": "con_los_documentos", "entre": ["conversion_a_markdown", "saneamiento"], "nombre": "Con los documentos leídos",
             "descripcion": "Los adjuntos ya pasaron por la conversión y el OCR; aquí se leen los que el motor no leyó o se agrega texto para el agente.",
             "datos": ["correo.*", "adjuntos.texto", "adjuntos.nombres", "no_leidos (los adjuntos que el motor no pudo leer)"],
             "manual": False,
             "efectos": "Lo que lea un leer_adjuntos de este tramo y el texto de la variable «documento_extra» entran como documentos del correo: los ven el agente, el revisor y la compuerta. Un adjunto que el motor no había leído y este tramo sí, deja de contar como no leído."},
            {"id": "antes_de_validar", "entre": ["registro_de_personas", "compuerta"], "nombre": "Antes de validar",
             "descripcion": "Con los campos ya resueltos por el algoritmo, el agente y el registro, justo antes de la compuerta.",
             "datos": ["correo.*", "adjuntos.texto", "campos (los valores resueltos, p. ej. {{campos.documento_afectado}})"],
             "manual": True,
             "efectos": "Se leen como {{campos.X}}. Un paso que deja una variable con el nombre de un campo (p. ej. un transformar con salida «nombre_afectado» o un ia_extraer con el campo «numero_documento_afectado») fija ese valor, que pasa por la compuerta como cualquier otro; una salida «manual» rechaza la radicación con su motivo.",
             "campos": ["nombre_afectado", "numero_documento_afectado", "tipo_documento_afectado", "sexo_afectado", "direccion_afectado",
                        "telefono_afectado", "correo_afectado", "departamento_afectado", "municipio_afectado", "nombre_remitente",
                        "numero_documento_remitente", "tipo_documento_remitente", "tipo_remitente", "tipo_pqrd", "tipo_solicitud", "oficina"]},
            {"id": "tras_radicar", "entre": ["radicacion", "cierre_outlook"], "nombre": "Tras radicar",
             "descripcion": "Con el número de radicado (en esta plataforma siempre simulado) y los campos enviados.",
             "datos": ["correo.*", "campos", "radicado"],
             "manual": False,
             "efectos": "Para avisar o registrar (notificar, tabla_guardar…): lo que escribe afuera queda retenido como siempre."},
        ],
        "apagables": {
            "deduplicacion": "no busca si el correo ya se radicó (capas 1 a 4)",
            "deduplicacion_contenido": "no compara el contenido con lo ya radicado (capas 5 y 6)",
            "enrutamiento": "nada se reenvía a otra área: todo sigue a radicación",
            "facturas": "las facturas electrónicas no se apartan",
            "remitente_identificable": "se radica aunque solo escriba un buzón de Positiva",
            "volumen": "no hay tope de adjuntos, páginas ni megas",
            "transcripcion_vision": "las hojas que el OCR no lee quedan solo con OCR (y los HEIC sin leer)",
            "registro_de_personas": "no se completan datos desde el registro de personas",
            "revisor": "la compuerta decide sola, sin el revisor de identidad",
        },
        "no_apagables": {
            "lote": "es la corrida misma", "descarga_adjuntos": "sin adjuntos no hay qué leer",
            "conversion_a_markdown": "todo lo que sigue lee su resultado; para cambiar cómo lee un tipo de archivo usa config.lectores",
            "saneamiento": "es la defensa contra instrucciones escondidas en los documentos",
            "extraccion_determinista": "el agente parte de lo que deja", "agente": "es quien resuelve los campos",
            "combinacion": "une lo del algoritmo y el agente", "compuerta": "es la que decide si se puede radicar",
            "correccion": "es la vuelta que corrige lo que la compuerta rechaza", "bitacora": "decide si los fallos permiten radicar",
            "radicacion": "es el desenlace (en esta plataforma, simulado)", "reclamo": "evita que dos corridas tomen el mismo correo",
        },
        "configurables": {
            "conversion_a_markdown": {"lectores": "por extensión: " + "; ".join(f"«{k}» {v}" for k, v in LECTORES.items())},
            "transcripcion_vision": {"max_imagenes": "imágenes por correo que pueden ir a la visión (0 a 20; el motor usa 8)"},
        },
        "intercambiables": [["volumen", "deduplicacion_contenido"]],
    },
    "tutelas": {
        "puntos": [
            {"id": "antes_de_radicar", "entre": ["duplicados", "radicar"], "nombre": "Antes de radicar",
             "descripcion": "Con el radicado de 23 dígitos, el juzgado y los anexos listos, justo antes de enviar a SGDEA (simulado).",
             "datos": ["correo.asunto", "correo.cuerpo", "correo.remitente", "radicado_23", "juzgado", "adjuntos.nombres"],
             "manual": True,
             "efectos": "Una salida «manual» no radica: el hilo va a revisión manual con el motivo."},
        ],
        "apagables": {"enlace": "no se busca el expediente por enlace (SharePoint, SAMAI)"},
        "no_apagables": {},
        "configurables": {},
        "intercambiables": [],
    },
}


# ------------------------------------------------------------- catálogo

def catalogo(base: str) -> Dict[str, Any]:
    return json.loads(CATALOGOS[base].read_text(encoding="utf-8"))


def por_defecto(base: str) -> Dict[str, Any]:
    """La estructura del código: cada etapa del catálogo y sus aristas, sin cambios."""
    c = catalogo(base)
    return {"nodos": [{"id": n["id"], "tipo": "etapa", "nombre": n.get("nombre") or n["id"]} for n in c["nodos"]],
            "aristas": [{"desde": a["desde"], "hacia": a["hacia"]} for a in c["aristas"]]}


def describir(base: str) -> Dict[str, Any]:
    b = BASES[base]
    c = catalogo(base)
    nombres = {n["id"]: n.get("nombre") or n["id"] for n in c["nodos"]}
    return {
        "base": base,
        "puntos": [{**p, "entre_nombres": [nombres.get(x, x) for x in p["entre"]]} for p in b["puntos"]],
        "apagables": b["apagables"], "no_apagables": b["no_apagables"], "configurables": b["configurables"],
        "intercambiables": b["intercambiables"], "lectores": LECTORES,
        "como_se_edita": ("Las etapas (tipo «etapa») son el código del motor: no se borran ni se reconectan entre sí; se apagan "
                          "con config.deshabilitado donde se puede y se reconfiguran con las claves de «configurables». Para "
                          "agregar pasos, inserta nodos declarativos en un punto: quita la arista entre las dos etapas del punto "
                          "y une la primera con tu nodo y tu nodo con la segunda. Para intercambiar etapas, reconecta solo esas dos."),
    }


# ------------------------------------------------------------- análisis

def _pares(aristas: List[dict]) -> Set[Tuple[str, str]]:
    return {(str(a.get("desde")), str(a.get("hacia"))) for a in aristas}


def _intercambiar(pares: Set[Tuple[str, str]], a: str, b: str) -> Set[Tuple[str, str]]:
    """El camino …→a→b→… pasa a …→b→a→…; los desvíos de cada etapa no cambian."""
    entra = {p for p in pares if p[1] == a and p[0] != b}
    sale = [p for p in pares if p[0] == b and p[1] != a]
    principal_b = next((p for p in sale if (b, p[1]) in pares and p[1] not in _desvios(pares, b)), None)
    r = set(pares) - entra - {(a, b)}
    r |= {(p[0], b) for p in entra} | {(b, a)}
    if principal_b:
        r.discard(principal_b)
        r.add((a, principal_b[1]))
    return r


def _desvios(pares: Set[Tuple[str, str]], etapa: str) -> Set[str]:
    return {h for d, h in pares if d == etapa and h in ("revision_manual", "duplicado", "no_radicado", "reenviado")}


def segmentos(defi: Dict[str, Any], etapas: Set[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Los tramos de nodos insertados entre dos etapas, y los problemas de forma."""
    nodos = {n.get("id"): n for n in defi.get("nodos") or [] if isinstance(n, dict)}
    aristas = [a for a in defi.get("aristas") or [] if isinstance(a, dict)]
    insertados = {i for i, n in nodos.items() if i not in etapas}
    problemas: List[str] = []
    tramos: List[Dict[str, Any]] = []
    asignados: Dict[str, str] = {}
    for a in aristas:
        if a.get("desde") not in etapas or a.get("hacia") not in insertados:
            continue
        desde, primero = a["desde"], a["hacia"]
        vistos: List[str] = []
        pila = [primero]
        salidas_a: Set[str] = set()
        while pila:
            x = pila.pop()
            if x in vistos:
                continue
            vistos.append(x)
            for b in aristas:
                if b.get("desde") != x:
                    continue
                h = b.get("hacia")
                if h in etapas:
                    salidas_a.add(h)
                elif h in insertados:
                    pila.append(h)
        terminales = [x for x in vistos if (nodos[x].get("tipo") == "salida")]
        if len(salidas_a) > 1:
            problemas.append(f"los pasos que salen de «{desde}» terminan en varias etapas ({', '.join(sorted(salidas_a))}): "
                             "un tramo insertado vuelve a UNA sola etapa, la que seguía")
        if not salidas_a and len(terminales) < len(vistos):
            problemas.append(f"los pasos que salen de «{desde}» no vuelven a ninguna etapa: conecta el último con la etapa que seguía")
        hacia = next(iter(salidas_a), "")
        for x in vistos:
            if x in asignados and asignados[x] != desde:
                problemas.append(f"{x}: está en dos tramos (desde «{asignados[x]}» y desde «{desde}»); cada paso va en un solo punto")
            asignados[x] = desde
        tramos.append({"desde": desde, "hacia": hacia, "primero": primero, "nodos": vistos})
    for x in sorted(insertados - set(asignados)):
        problemas.append(f"{x}: no se llega a él desde ninguna etapa; conéctalo dentro de un punto de inserción")
    vistas_desde: Dict[str, int] = {}
    for t in tramos:
        vistas_desde[t["desde"]] = vistas_desde.get(t["desde"], 0) + 1
    for d, n in vistas_desde.items():
        if n > 1:
            problemas.append(f"de «{d}» salen {n} aristas hacia pasos insertados: el tramo de un punto empieza en un solo paso")
    return tramos, problemas


def contraer(defi: Dict[str, Any], etapas: Set[str], tramos: List[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    """Las aristas entre etapas, con cada tramo insertado visto como la arista que reemplaza."""
    pares = {p for p in _pares(defi.get("aristas") or []) if p[0] in etapas and p[1] in etapas}
    return pares | {(t["desde"], t["hacia"]) for t in tramos if t["hacia"]}


def definicion_del_tramo(tramo: Dict[str, Any], defi: Dict[str, Any], punto: Dict[str, Any], base: str) -> Dict[str, Any]:
    """Un flujo declarativo con el tramo: entrada de correo, las variables del punto, el tramo y la salida «sigue»."""
    nodos = {n["id"]: n for n in defi.get("nodos") or []}
    previos: List[dict] = [{"id": "__entrada", "tipo": "entrada_correo", "nombre": "Entrada", "config": {}}]
    for v in variables_del_punto(punto):
        previos.append({"id": f"__{v}", "tipo": "expresion", "nombre": v, "config": {"expresion": "1", "salida": v}})
    cadena = [{"desde": previos[i]["id"], "hacia": previos[i + 1]["id"]} for i in range(len(previos) - 1)]
    propias = [copy.deepcopy(nodos[x]) for x in tramo["nodos"]]
    internas = []
    for a in defi.get("aristas") or []:
        if a.get("desde") in tramo["nodos"]:
            internas.append({**a, "hacia": "__sigue" if a.get("hacia") == tramo["hacia"] else a.get("hacia")})
    return {"id": f"tramo-{base}", "nombre": f"Pasos de {punto['nombre']}", "modelo": "gemini-2.5-flash",
            "nodos": previos + propias + [{"id": "__sigue", "tipo": "salida", "nombre": "Sigue el motor", "config": {"desenlace": "completar"}}],
            "aristas": cadena + [{"desde": previos[-1]["id"], "hacia": tramo["primero"]}] + internas}


def variables_del_punto(punto: Dict[str, Any]) -> List[str]:
    return {"al_entrar": [], "con_los_documentos": ["no_leidos", "documento_extra"], "antes_de_validar": ["campos"],
            "tras_radicar": ["campos", "radicado"], "antes_de_radicar": ["radicado_23", "juzgado"]}.get(punto["id"], [])


def validar(base: str, defi: Any, entorno: Optional[List[str]] = None) -> Dict[str, Any]:
    """{ok, problemas, tramos, intercambio, apagadas} de una estructura propuesta."""
    if base not in BASES:
        return {"ok": False, "problemas": [f"«{base}» no es un motor de código"]}
    if not isinstance(defi, dict) or not isinstance(defi.get("nodos"), list) or not isinstance(defi.get("aristas"), list):
        return {"ok": False, "problemas": ["la estructura es {nodos: [...], aristas: [...]}"]}
    b = BASES[base]
    cat = catalogo(base)
    etapas_cat = {n["id"] for n in cat["nodos"]}
    problemas: List[str] = []
    ids = [n.get("id") for n in defi["nodos"] if isinstance(n, dict)]
    if len(ids) != len(set(ids)):
        problemas.append("hay nodos con el mismo id")
    etapas = {n.get("id") for n in defi["nodos"] if isinstance(n, dict) and n.get("tipo") == "etapa"}
    for x in sorted(etapas_cat - etapas):
        motivo = b["apagables"].get(x)
        problemas.append(f"falta la etapa «{x}»: las etapas del motor no se quitan" +
                         (f"; para que no corra, apágala con config.deshabilitado = true ({motivo})" if motivo else
                          f" ni se apagan ({b['no_apagables'].get(x, 'el resto del motor depende de ella')})"))
    for x in sorted(etapas - etapas_cat):
        problemas.append(f"«{x}» no es una etapa del motor de {base}: un paso nuevo lleva su tipo declarativo (ia_extraer, validar, transformar…), no «etapa»")
    apagadas: List[str] = []
    for n in defi["nodos"]:
        if not isinstance(n, dict):
            problemas.append("cada nodo es un objeto {id, tipo, nombre, config}")
            continue
        c = n.get("config") or {}
        if not isinstance(c, dict):
            problemas.append(f"{n.get('id')}: config tiene que ser un objeto")
            continue
        if n.get("tipo") != "etapa":
            if not ID_NODO.match(str(n.get("id") or "")):
                problemas.append(f"{n.get('id')}: el id de un paso nuevo va en minúsculas con guion bajo")
            if n.get("tipo") in NO_INSERTABLES:
                problemas.append(f"{n.get('id')}: un {n.get('tipo')} no va dentro de un flujo de código (el motor no se detiene ni llama a otros flujos)")
            continue
        eid = n.get("id")
        for k, v in c.items():
            if k == "deshabilitado":
                if v is True and eid not in b["apagables"]:
                    problemas.append(f"{eid}: esta etapa no se puede apagar: {b['no_apagables'].get(eid, 'el resto del motor depende de ella')}")
                elif v is True:
                    apagadas.append(eid)
            elif k not in b["configurables"].get(eid, {}):
                admitidas = ", ".join(b["configurables"].get(eid, {})) or "ninguna (solo deshabilitado, si se puede apagar)"
                problemas.append(f"{eid}: «{k}» no es una opción de esta etapa (admite: {admitidas})")
        problemas += _problemas_config_etapa(eid, c)
    tramos, forma = segmentos(defi, etapas)
    problemas += forma
    reales = contraer(defi, etapas, tramos)
    esperadas = _pares(cat["aristas"])
    intercambio = None
    if reales != esperadas:
        for a, x in b["intercambiables"]:
            if reales == _intercambiar(esperadas, a, x):
                intercambio = [a, x]
                break
        else:
            for d, h in sorted(esperadas - reales):
                if (d, h) not in {(t["desde"], t["hacia"]) for t in tramos}:
                    problemas.append(f"falta la conexión del motor {d} → {h}" +
                                     (" (si insertaste pasos aquí, el último tiene que volver a «" + h + "»)" if any(t["desde"] == d for t in tramos) else ""))
            for d, h in sorted(reales - esperadas):
                problemas.append(f"la conexión {d} → {h} no existe en el motor: el orden de las etapas es el del código, porque cada una usa lo que deja la anterior"
                                 + (f"; lo único que se puede intercambiar es {' y '.join(' ⇄ '.join(p) for p in b['intercambiables'])}" if b["intercambiables"] else ""))
    puntos = {tuple(p["entre"]): p for p in b["puntos"]}
    detalle: List[Dict[str, Any]] = []
    nodos = {n.get("id"): n for n in defi["nodos"] if isinstance(n, dict)}
    for t in tramos:
        p = puntos.get((t["desde"], t["hacia"]))
        if not p:
            lista = "; ".join(f"{q['nombre']} ({q['entre'][0]} → {q['entre'][1]})" for q in b["puntos"])
            problemas.append(f"entre «{t['desde']}» y «{t['hacia'] or '?'}» no se pueden insertar pasos: el motor solo los corre en {lista}")
            continue
        for x in t["nodos"]:
            n = nodos[x]
            if n.get("tipo") == "salida":
                des = (n.get("config") or {}).get("desenlace")
                if des != "manual":
                    problemas.append(f"{x}: dentro de un flujo de código una salida solo puede mandar a revisión manual (desenlace «manual»); para seguir, conecta con «{t['hacia']}»")
                elif not p["manual"]:
                    problemas.append(f"{x}: en «{p['nombre']}» no se puede mandar a revisión manual ({p['efectos']})")
        try:
            import flujo_declarativo as fd
            sintetica = definicion_del_tramo(t, defi, p, base)
            for m in fd.validar(sintetica, entorno=entorno):
                if "__" in m and not any(x in m for x in t["nodos"]):
                    continue
                problemas.append(m.replace("__sigue", f"«{t['hacia']}»").replace("__entrada", "la entrada"))
        except Exception as e:  # la validación del tramo nunca tumba la del resto
            problemas.append(f"no se pudieron validar los pasos de {p['nombre']}: {type(e).__name__}: {e}")
        detalle.append({"punto": p["id"], "nombre": p["nombre"], "desde": t["desde"], "hacia": t["hacia"], "nodos": t["nodos"], "primero": t["primero"]})
    return {"ok": not problemas, "problemas": problemas, "tramos": detalle, "intercambio": intercambio, "apagadas": apagadas}


def _problemas_config_etapa(eid: str, c: dict) -> List[str]:
    p: List[str] = []
    if "lectores" in c:
        lec = c["lectores"]
        if not isinstance(lec, dict):
            return [f"{eid}: lectores es un objeto {{extensión: {'|'.join(LECTORES)}}}"]
        for ext, modo in lec.items():
            e = str(ext).lower().lstrip(".")
            if e not in EXT_LEIBLES:
                p.append(f"{eid}: «{ext}» no es una extensión que el motor reciba ({', '.join(sorted(EXT_LEIBLES))})")
            if modo not in LECTORES:
                p.append(f"{eid}: el lector de «{ext}» tiene que ser {', '.join(LECTORES)}")
            elif modo == "vision" and e not in EXT_IMAGEN:
                p.append(f"{eid}: la visión directa solo lee imágenes; para «{ext}» usa automatico, ocr u omitir")
    if "max_imagenes" in c and not (isinstance(c["max_imagenes"], int) and not isinstance(c["max_imagenes"], bool) and 0 <= c["max_imagenes"] <= 20):
        p.append(f"{eid}: max_imagenes es un entero entre 0 y 20")
    return p


# ------------------------------------------------------------- correr un tramo

def correr_segmento(tramo: Dict[str, Any], defi: Dict[str, Any], base: str, contexto: Dict[str, Any],
                    orden: Dict[str, Any], corrida: str, salida_eventos: Any = None, cerrojo: Any = None) -> Dict[str, Any]:
    """
    Corre los pasos insertados de un punto con los datos de ese momento.

    contexto: {correo: {asunto, cuerpo, remitente, remitente_correo, remitente_nombre, recibido},
               adjuntos: [(nombre, ruta)], adjuntos_texto, variables: {...}}
    Devuelve {desenlace: sigue|manual, motivo, variables, texto_leido, leidos, avisos, consumo, escrituras}.
    """
    import flujo_declarativo as fd

    if salida_eventos is not None:
        fd._salida = salida_eventos
    if cerrojo is not None:
        fd._cerrojo = cerrojo
    marca = fd._corrida.set(corrida)
    punto = next(p for p in BASES[base]["puntos"] if p["id"] == tramo["punto"])
    sintetica = definicion_del_tramo(tramo, defi, punto, base)
    fd.preparar_secretos(orden)
    entorno = {str(k): "" if v is None else str(v) for k, v in (orden.get("entorno") or {}).items()}
    e = fd.Ejecucion(sintetica, corrida, "gemini-2.5-flash", credenciales=orden.get("credenciales") or {}, entorno=entorno,
                     flujo=str(orden.get("flujo") or base), ia_global=orden.get("ia_global"))
    correo = dict(contexto.get("correo") or {})
    nombres = [n for n, _ in contexto.get("adjuntos") or []]
    e.correo = {**correo, "adjuntos_texto": contexto.get("adjuntos_texto") or "", "adjuntos_nombres": nombres,
                "adjuntos_leidos": list(contexto.get("adjuntos_leidos") or [])}
    if contexto.get("adjuntos"):
        e.carpeta = Path(contexto["carpeta"])
        e.msg = {"_adjuntos": [{"name": n, "_ruta": str(r), "contentType": ""} for n, r in contexto["adjuntos"]]}
    e.vars.update(copy.deepcopy(contexto.get("variables") or {}))
    texto0, leidos0 = e.correo["adjuntos_texto"], list(e.correo["adjuntos_leidos"])
    inicial = copy.deepcopy(e.vars)
    try:
        r = e.correr(tramo["primero"])
    finally:
        fd._corrida.reset(marca)
        fd._secretos_activos.clear()
    texto = e.correo.get("adjuntos_texto") or ""
    nuevo = texto[len(texto0):] if texto.startswith(texto0) else texto
    return {"desenlace": "manual" if r.get("accion") == "manual" else "sigue", "motivo": r.get("motivo") or "",
            "variables": {k: v for k, v in e.vars.items() if not k.startswith("_")}, "iniciales": inicial,
            "texto_leido": nuevo.strip(), "leidos": [x for x in e.correo.get("adjuntos_leidos") or [] if x not in leidos0],
            "avisos": e.avisos, "consumo": e.consumo, "escrituras": e.escrituras}


# ------------------------------------------------------------- línea de órdenes

def _principal() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    entrada = json.loads(sys.stdin.read() or "{}") if cmd in ("validar", "describir", "por_defecto") else {}
    if cmd == "validar":
        r = validar(entrada.get("base", ""), entrada.get("definicion"), entrada.get("entorno"))
    elif cmd == "describir":
        r = describir(entrada.get("base", ""))
    elif cmd == "por_defecto":
        r = por_defecto(entrada.get("base", ""))
    else:
        r = {"error": "uso: estructura_codigo.py validar|describir|por_defecto (JSON por stdin)"}
    sys.stdout.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


if __name__ == "__main__":
    _principal()
