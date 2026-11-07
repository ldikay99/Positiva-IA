"""
El asistente del chat: crea, corrige y mejora flujos con Gemini en Vertex.

Trabaja con herramientas sobre la API del propio visor —las mismas que usa la
web—, así lo que hace el chat y lo que se ve en pantalla son una sola cosa:
crea un flujo, lo valida, lo prueba con un correo real y lee el resultado.

Entrada (stdin): {"mensajes": [{"rol": "usuario"|"asistente", "texto"}], "flujo": id, "modelo"}
Salida (stdout): una línea JSON por paso: {"tipo": "herramienta"|"texto"|"fin"|"error", ...}
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

API = os.environ.get("API_INTERNA", f"http://127.0.0.1:{os.environ.get('PUERTO', '8080')}")
PROYECTO = os.environ.get("GCP_PROJECT", "positiva-sgda-prd-416417")
MAX_VUELTAS = 14


def emitir(tipo: str, **datos: Any) -> None:
    sys.stdout.write(json.dumps({"tipo": tipo, **datos}, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def api(metodo: str, ruta: str, cuerpo: Any = None) -> Any:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(API + ruta, data=datos, method=metodo, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return {"error": json.loads(e.read()).get("error"), "detalle": json.loads(e.read() or b"{}")}
        except Exception:
            return {"error": f"HTTP {e.code}"}


TIPOS_DE_NODO = """
Un flujo declarativo es {id, nombre, descripcion, modelo, nodos: [{id, tipo, nombre, descripcion, config}], aristas: [{desde, hacia, cuando}]}.
- id del flujo: minúsculas con guiones (ej. "derechos-de-peticion"); id de nodo: minúsculas con guion bajo.
- Exactamente un nodo entrada_correo; al menos una salida; todo nodo que no es salida tiene arista de salida; todo nodo alcanzable desde la entrada.
- Una arista se toma si "cuando" es igual a la salida del nodo; una arista sin "cuando" (o "*") es la de por defecto.
Tipos y config:
- entrada_correo {leer_adjuntos: bool=true, ocr: bool=true}: lee asunto, cuerpo, remitente y adjuntos (PDF, imágenes con OCR y visión). Sale por "ok".
- filtro {campo: asunto|cuerpo|remitente|adjuntos|texto|<variable>, operador: contiene|coincide(regex)|no_coincide|igual|vacio|no_vacio, valor}: sale por "si" o "no".
- condicion: igual que filtro, pensado para variables ya obtenidas.
- ia_clasificar {instrucciones, categorias: [{valor, descripcion}], variable}: la IA elige una categoría; sale por el valor de la categoría (usa aristas con cuando=<valor>, y una por defecto).
- ia_extraer {instrucciones, campos: [{nombre, descripcion, tipo: texto|numero|fecha|lista|booleano, obligatorio}]}: la IA extrae cada campo con su evidencia; deja vacío lo que no está. Sale por "ok".
- validar {reglas: [{campo, regla: obligatorio|patron|en_lista|longitud, valor, codigo, mensaje}]}: sale por "ok" o "falla" (con los reparos).
- ia_redactar {instrucciones, variable}: la IA redacta un texto (ej. respuesta al ciudadano). Sale por "ok".
- salida {desenlace: radicar|manual|reenviar|descartar|responder|duplicado, motivo, destino, campos: [variables que van en la solicitud]}: termina el flujo.
Modelos: gemini-2.5-flash (por defecto), gemini-2.5-pro, gemini-3-flash-preview, gemini-3.1-flash-lite.
"""

try:
    # La documentación completa de los nodos la mantiene el motor declarativo.
    from nodos_declarativos import DOCUMENTACION as _DOC_NODOS
    TIPOS_DE_NODO = _DOC_NODOS
except Exception:
    pass

SISTEMA = f"""Eres el asistente de Positiva IA, una plataforma para crear y operar flujos que procesan correos (radicación de PQRD, tutelas y los que se creen).
Ayudas a crear flujos nuevos, corregir los que fallan y mejorarlos. Hablas en español, claro y breve.

Cómo trabajas:
1. Antes de crear o cambiar, entiende qué necesita la persona; si falta algo esencial, pregunta una sola cosa concreta.
2. Para crear o cambiar un flujo declarativo: arma la definición completa, llama a validar_flujo, corrige hasta que no haya problemas y luego guardar_flujo.
3. Después de guardar, pruébalo con probar_flujo sobre un correo real que encaje (búscalo con buscar_correos) y revisa el resultado. Si algo sale mal, corrígelo y vuelve a probar.
4. Para encontrar problemas, mira ultimas_corridas y ver_corrida: desenlaces, campos sin resolver, avisos y pasos.
5. Los flujos de código (pqrd, tutelas) tienen la estructura fija del código: puedes leerlos y ajustar sus parámetros de PQRD con ajustar_parametro_pqrd, no rehacerlos.
6. Nunca inventes datos ni resultados: di solo lo que devolvieron las herramientas. Nada de esto escribe en sistemas reales: SGDEA y el buzón están en modo prueba.
7. Al terminar, resume en pocas líneas qué hiciste, qué salió en la prueba y qué falta, si algo.
8. Para cambios puntuales usa editar_nodo, agregar_nodo, eliminar_nodo y editar_aristas: cada una valida y guarda una versión nueva. Para un flujo nuevo usa guardar_flujo y después enfocar_flujo para dejarlo abierto en pantalla.
9. Tienes la conversación completa: lo que dijo la persona antes, los adjuntos que mandó (imágenes y documentos, que puedes ver) y lo que tú hiciste en turnos anteriores. Úsalo; no pidas que repitan lo ya dicho.

{TIPOS_DE_NODO}"""

HERRAMIENTAS = [
    {"name": "listar_flujos", "description": "Los flujos que existen, con su tipo (codigo o declarativo).", "parameters": {"type": "object", "properties": {}}},
    {"name": "leer_flujo", "description": "La definición completa de un flujo declarativo, o el resumen de nodos de uno de código.", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "validar_flujo", "description": "Valida una definición de flujo declarativo sin guardarla. Devuelve los problemas.", "parameters": {"type": "object", "properties": {"definicion": {"type": "object"}}, "required": ["definicion"]}},
    {"name": "guardar_flujo", "description": "Crea o reemplaza un flujo declarativo (queda la versión anterior). Solo si validar_flujo no dio problemas.", "parameters": {"type": "object", "properties": {"definicion": {"type": "object"}}, "required": ["definicion"]}},
    {"name": "buscar_correos", "description": "Busca correos en la bandeja para probar un flujo. Devuelve caso, asunto, remitente y adjuntos.", "parameters": {"type": "object", "properties": {"texto": {"type": "string"}, "n": {"type": "integer"}}}},
    {"name": "probar_flujo", "description": "Corre un flujo sobre un correo real (modo prueba) y espera el resultado: desenlace, campos, avisos y pasos.", "parameters": {"type": "object", "properties": {"flujo": {"type": "string"}, "caso": {"type": "string"}}, "required": ["flujo", "caso"]}},
    {"name": "ultimas_corridas", "description": "Las últimas corridas de un flujo con su desenlace y motivo.", "parameters": {"type": "object", "properties": {"flujo": {"type": "string"}, "n": {"type": "integer"}}, "required": ["flujo"]}},
    {"name": "ver_corrida", "description": "El detalle de una corrida: desenlace, campos, avisos y pasos.", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "editar_nodo", "description": "Cambia un nodo de un flujo declarativo: nombre, descripcion, tipo y/o config (la config se mezcla con la actual; pon un campo en null para quitarlo). Valida y guarda.", "parameters": {"type": "object", "properties": {"flujo": {"type": "string"}, "nodo": {"type": "string"}, "cambios": {"type": "object"}}, "required": ["flujo", "nodo", "cambios"]}},
    {"name": "agregar_nodo", "description": "Agrega un nodo a un flujo declarativo con sus aristas de entrada y salida. Valida y guarda.", "parameters": {"type": "object", "properties": {"flujo": {"type": "string"}, "nodo": {"type": "object"}, "aristas": {"type": "array", "items": {"type": "object"}}}, "required": ["flujo", "nodo"]}},
    {"name": "eliminar_nodo", "description": "Elimina un nodo de un flujo declarativo y sus aristas. Con reconectar=true une a quienes llegaban a él con a quienes él llevaba. Valida y guarda.", "parameters": {"type": "object", "properties": {"flujo": {"type": "string"}, "nodo": {"type": "string"}, "reconectar": {"type": "boolean"}}, "required": ["flujo", "nodo"]}},
    {"name": "editar_aristas", "description": "Agrega y/o quita aristas {desde, hacia, cuando} de un flujo declarativo. Valida y guarda.", "parameters": {"type": "object", "properties": {"flujo": {"type": "string"}, "agregar": {"type": "array", "items": {"type": "object"}}, "quitar": {"type": "array", "items": {"type": "object"}}}, "required": ["flujo"]}},
    {"name": "borrar_flujo", "description": "Borra un flujo declarativo (los de código no se borran). Solo si la persona lo pidió.", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "enfocar_flujo", "description": "Abre ese flujo en la pantalla de la persona (por ejemplo, el que acabas de crear).", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "ajustar_parametro_pqrd", "description": "Cambia un parámetro del motor de PQRD (clave del catálogo, ej. pipeline.compuerta.CONFIANZA_PARA_RADICAR). Rige desde el próximo correo.", "parameters": {"type": "object", "properties": {"clave": {"type": "string"}, "valor": {}, "nota": {"type": "string"}}, "required": ["clave", "valor"]}},
]


def _resumen_corrida(c: dict) -> dict:
    r = c.get("resultado") or {}
    pasos = [f"{e.get('nodo')}: {e.get('titulo')}" for e in c.get("eventos", []) if e.get("tipo") == "paso"][:40]
    hall = {k: (v.get("valor") if isinstance(v, dict) else v) for k, v in (r.get("hallazgos") or {}).items()}
    return {"id": c.get("id"), "flujo": c.get("flujo"), "caso": c.get("caso"), "estado": c.get("estado"),
            "desenlace": r.get("accion"), "motivo": r.get("motivo"), "campos": hall, "sin_resolver": r.get("sin_resolver"),
            "avisos": (r.get("avisos") or [])[:12], "pasos": pasos, "segundos": r.get("segundos")}


def _definicion(flujo: str) -> Dict[str, Any]:
    f = api("GET", f"/api/flujos/{flujo}")
    if not isinstance(f, dict) or f.get("error"):
        raise ValueError(f"no existe el flujo {flujo}")
    if f.get("tipo") != "declarativo":
        raise ValueError(f"«{flujo}» es un flujo de código: su estructura es la del código y no se edita por nodos")
    return json.loads(json.dumps(f["definicion"]))


def _guardar(d: Dict[str, Any]) -> Any:
    v = api("POST", "/api/flujos/validar", {"definicion": d})
    if not v.get("ok"):
        return {"guardado": False, "problemas": v.get("problemas")}
    r = api("PUT", f"/api/flujos/{d['id']}", {"definicion": d})
    return {"guardado": True, "version": (r.get("flujo") or {}).get("version")} if r.get("flujo") else r


def ejecutar(nombre: str, a: Dict[str, Any]) -> Any:
    if nombre == "editar_nodo":
        d = _definicion(a["flujo"])
        n = next((x for x in d["nodos"] if x["id"] == a["nodo"]), None)
        if not n:
            return {"error": f"el flujo no tiene el nodo {a['nodo']}", "nodos": [x["id"] for x in d["nodos"]]}
        cambios = a.get("cambios") or {}
        for k in ("nombre", "descripcion", "tipo"):
            if k in cambios and cambios[k] is not None:
                n[k] = cambios[k]
        config = dict(n.get("config") or {})
        for k, v in (cambios.get("config") or {}).items():
            if v is None:
                config.pop(k, None)
            else:
                config[k] = v
        n["config"] = config
        return _guardar(d)
    if nombre == "agregar_nodo":
        d = _definicion(a["flujo"])
        if any(x["id"] == a["nodo"].get("id") for x in d["nodos"]):
            return {"error": f"ya hay un nodo {a['nodo'].get('id')}"}
        d["nodos"].append(a["nodo"])
        d["aristas"] += a.get("aristas") or []
        return _guardar(d)
    if nombre == "eliminar_nodo":
        d = _definicion(a["flujo"])
        quien = a["nodo"]
        entran = [x for x in d["aristas"] if x["hacia"] == quien]
        salen = [x for x in d["aristas"] if x["desde"] == quien]
        d["nodos"] = [x for x in d["nodos"] if x["id"] != quien]
        d["aristas"] = [x for x in d["aristas"] if quien not in (x["desde"], x["hacia"])]
        if a.get("reconectar"):
            for e in entran:
                for s_ in salen:
                    d["aristas"].append({"desde": e["desde"], "hacia": s_["hacia"], **({"cuando": e["cuando"]} if e.get("cuando") else {})})
        return _guardar(d)
    if nombre == "editar_aristas":
        d = _definicion(a["flujo"])
        quitar = [(x.get("desde"), x.get("hacia")) for x in a.get("quitar") or []]
        d["aristas"] = [x for x in d["aristas"] if (x["desde"], x["hacia"]) not in quitar] + list(a.get("agregar") or [])
        return _guardar(d)
    if nombre == "borrar_flujo":
        return api("DELETE", f"/api/flujos/{a['id']}")
    if nombre == "enfocar_flujo":
        f = api("GET", f"/api/flujos/{a['id']}")
        if not isinstance(f, dict) or f.get("error"):
            return {"error": f"no existe el flujo {a['id']}"}
        emitir("enfocar", flujo=a["id"])
        return {"ok": True, "abierto": a["id"]}
    if nombre == "listar_flujos":
        return [{k: f.get(k) for k in ("id", "nombre", "tipo", "descripcion", "version")} for f in api("GET", "/api/flujos")]
    if nombre == "leer_flujo":
        f = api("GET", f"/api/flujos/{a['id']}")
        if f.get("tipo") == "codigo":
            cat = api("GET", f"/api/catalogo?flujo={a['id']}")
            return {"id": f["id"], "tipo": "codigo", "descripcion": f.get("descripcion"),
                    "nodos": [{"id": n["id"], "nombre": n["nombre"], "descripcion": n.get("descripcion"),
                               "parametros": [p["clave"] for p in n.get("parametros", [])][:12]} for n in cat.get("nodos", [])]}
        return f.get("definicion") or f
    if nombre == "validar_flujo":
        return api("POST", "/api/flujos/validar", {"definicion": a["definicion"]})
    if nombre == "guardar_flujo":
        d = a["definicion"]
        return api("PUT", f"/api/flujos/{d.get('id')}", {"definicion": d})
    if nombre == "buscar_correos":
        texto, n = (a.get("texto") or "").lower(), int(a.get("n") or 8)
        todos = api("GET", "/api/bandeja")
        elegidos = [c for c in todos if not texto or texto in f"{c.get('asunto')} {c.get('remitente')} {c.get('remitenteCorreo')} {c.get('vistaPrevia')}".lower()]
        return [{"caso": c["caso"], "asunto": c.get("asunto"), "remitente": c.get("remitenteCorreo"), "adjuntos": [x.get("nombre") for x in c.get("adjuntos", [])][:5],
                 "en_produccion": c.get("desenlaceProduccion")} for c in elegidos[:n]]
    if nombre == "probar_flujo":
        creadas = api("POST", "/api/corridas", {"casos": [a["caso"]], "flujo": a["flujo"]})
        if not isinstance(creadas, list):
            return creadas
        cid = creadas[0]["id"]
        emitir("progreso", texto=f"Corriendo {a['flujo']} sobre {a['caso']}…", corrida=cid)
        for _ in range(120):
            c = api("GET", f"/api/corridas/{cid}")
            if c.get("estado") in ("terminada", "fallida"):
                return _resumen_corrida(c)
            time.sleep(3)
        return {"id": cid, "estado": "sigue corriendo"}
    if nombre == "ultimas_corridas":
        lista = api("GET", f"/api/corridas?flujo={a['flujo']}")[: int(a.get("n") or 10)]
        return [{"id": c["id"], "caso": c["caso"], "estado": c["estado"], "desenlace": (c.get("resultado") or {}).get("accion"),
                 "motivo": (c.get("resultado") or {}).get("motivo")} for c in lista]
    if nombre == "ver_corrida":
        return _resumen_corrida(api("GET", f"/api/corridas/{a['id']}"))
    if nombre == "ajustar_parametro_pqrd":
        conf = api("GET", "/api/configuracion")
        conf.pop("modelos", None)
        conf.setdefault("parametros", {})[a["clave"]] = a["valor"]
        return api("PUT", "/api/configuracion", {"configuracion": conf, "nota": a.get("nota") or "ajustado desde el chat"})
    return {"error": f"herramienta desconocida {nombre}"}


IMAGENES_Y_PDF = ("image/png", "image/jpeg", "image/webp", "image/heic", "image/heif", "application/pdf")


def _partes_de_adjunto(adj: Dict[str, Any], completo: bool, types) -> List[Any]:
    """Un adjunto del chat como partes para Gemini: el archivo mismo o su texto."""
    nombre, tipo, ruta = adj.get("nombre") or "archivo", (adj.get("tipo") or "").lower(), adj.get("ruta") or ""
    if not ruta or not os.path.exists(ruta):
        return [types.Part(text=f"[Adjunto «{nombre}»: ya no está disponible]")]
    if completo and tipo in IMAGENES_Y_PDF and os.path.getsize(ruta) < 18 * 1024 * 1024:
        with open(ruta, "rb") as f:
            return [types.Part(text=f"[Adjunto «{nombre}» ({tipo})]"), types.Part.from_bytes(data=f.read(), mime_type=tipo)]
    try:
        from texto_de_archivo import texto
        contenido = texto(ruta, nombre)
    except Exception as e:
        contenido = f"(no se pudo leer: {type(e).__name__})"
    return [types.Part(text=f"[Adjunto «{nombre}» ({tipo}), su texto:]\n{contenido[:30000 if completo else 4000]}")]


def principal() -> None:
    import google.auth
    from google import genai
    from google.genai import types

    entrada = json.loads(sys.stdin.read() or "{}")
    modelo = entrada.get("modelo") or "gemini-3.1-flash-lite"
    flujo = entrada.get("flujo") or "pqrd"
    cred, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    cliente = genai.Client(vertexai=True, project=PROYECTO, location="global", credentials=cred)
    herramientas = types.Tool(function_declarations=[types.FunctionDeclaration(**h) for h in HERRAMIENTAS])
    config = types.GenerateContentConfig(system_instruction=SISTEMA + f"\n\nEl flujo abierto en pantalla ahora es «{flujo}».",
                                         tools=[herramientas], temperature=0.2,
                                         automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    contenidos: List[Any] = []
    mensajes = entrada.get("mensajes") or []
    # Los adjuntos se le muestran tal cual (imágenes y PDF) en los últimos
    # mensajes; en los anteriores van como texto, para no repetir bytes.
    recientes = {i for i, m in enumerate(mensajes) if m.get("rol") == "usuario"}
    recientes = set(sorted(recientes)[-3:])
    for i, m in enumerate(mensajes):
        partes = []
        if m.get("rol") == "usuario":
            for adj in m.get("adjuntos") or []:
                partes += _partes_de_adjunto(adj, i in recientes, types)
            partes.append(types.Part(text=m.get("texto") or "(sin texto: revisa los adjuntos)"))
            contenidos.append(types.Content(role="user", parts=partes))
        else:
            hechos = "\n".join(f"- {p.get('nombre')}({json.dumps(p.get('argumentos'), ensure_ascii=False)[:200]}) → {(p.get('resultado') or '')[:300]}" for p in m.get("pasos") or [])
            texto = (m.get("texto") or "") + (f"\n\n[Lo que hice en ese turno:\n{hechos}]" if hechos else "")
            contenidos.append(types.Content(role="model", parts=[types.Part(text=texto or "(sin texto)")]))
    cambiados = set()
    for _ in range(MAX_VUELTAS):
        r = cliente.models.generate_content(model=modelo, contents=contenidos, config=config)
        cand = r.candidates[0].content if r.candidates else None
        partes = list(cand.parts or []) if cand else []
        llamadas = [p.function_call for p in partes if getattr(p, "function_call", None)]
        texto = "".join(p.text for p in partes if getattr(p, "text", None))
        if not llamadas:
            emitir("texto", texto=texto or "(sin respuesta)")
            emitir("fin", flujos_cambiados=sorted(cambiados))
            return
        if texto.strip():
            emitir("pensando", texto=texto)
        contenidos.append(cand)
        respuestas = []
        for ll in llamadas:
            args = dict(ll.args or {})
            emitir("herramienta", nombre=ll.name, argumentos={k: (v if k != "definicion" else {"id": (v or {}).get("id"), "nodos": len((v or {}).get("nodos", []))}) for k, v in args.items()})
            try:
                res = ejecutar(ll.name, args)
            except Exception as e:
                res = {"error": f"{type(e).__name__}: {e}"}
            if ll.name == "guardar_flujo" and isinstance(res, dict) and res.get("flujo"):
                cambiados.add(res["flujo"]["id"])
            if ll.name in ("editar_nodo", "agregar_nodo", "eliminar_nodo", "editar_aristas") and isinstance(res, dict) and res.get("guardado"):
                cambiados.add(args["flujo"])
            if ll.name == "ajustar_parametro_pqrd" and isinstance(res, dict) and not res.get("error"):
                cambiados.add("pqrd")
            emitir("resultado_herramienta", nombre=ll.name, resultado=json.dumps(res, ensure_ascii=False, default=str)[:1500])
            respuestas.append(types.Part.from_function_response(name=ll.name, response={"resultado": res}))
        contenidos.append(types.Content(role="user", parts=respuestas))
    emitir("texto", texto="Me detuve tras varios pasos sin terminar. Dime cómo seguimos.")
    emitir("fin", flujos_cambiados=sorted(cambiados))


if __name__ == "__main__":
    try:
        principal()
    except Exception as e:
        emitir("error", texto=f"{type(e).__name__}: {e}"[:600])
