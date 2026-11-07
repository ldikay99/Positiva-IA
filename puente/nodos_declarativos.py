"""
La biblioteca de nodos de los flujos declarativos, en un solo lugar.

- `TIPOS`: la especificación de cada tipo (qué hace, su config, por dónde sale,
  qué variables deja y un ejemplo).
- `DOCUMENTACION`: el texto que el asistente del chat pone en su prompt para
  crear flujos (`from nodos_declarativos import DOCUMENTACION`).
- `esquema()`: la misma especificación en JSON, para la web o para pruebas.

El motor (`flujo_declarativo.py`) es quien valida y ejecuta; las pruebas
comprueban que los dos conozcan los mismos tipos.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict

VERSION = 2

DESENLACES = ["radicar", "manual", "reenviar", "descartar", "responder", "duplicado"]

# Lo que todo nodo puede declarar, además de su config propia.
COMUNES: Dict[str, Dict[str, Any]] = {
    "entradas": {"tipo": "lista de texto", "descripcion": "Lo que el nodo recibe: variables («datos», «datos.nombre») o datos del correo "
                 "(«correo», «correo.cuerpo», «adjuntos.texto»). En los nodos de IA, si se da, el modelo ve SOLO esto; si no, ve el correo, los adjuntos y las variables."},
    "salida": {"tipo": "nombre de variable", "descripcion": "Dónde deja su resultado (letras, números y guion bajo)."},
    "modelo": {"tipo": "texto", "descripcion": "Solo nodos de IA: modelo de este nodo (si no, el del flujo)."},
    "temperatura": {"tipo": "número 0..2", "descripcion": "Solo nodos de IA: creatividad (0,1 por defecto; 0,4–0,7 para redactar)."},
}

PLANTILLAS = {
    "correo.asunto": "asunto del correo", "correo.cuerpo": "cuerpo en texto plano", "correo.remitente": "«Nombre <correo>»",
    "correo.remitente_correo": "solo la dirección", "correo.remitente_nombre": "solo el nombre", "correo.recibido": "fecha de recepción",
    "adjuntos.texto": "texto leído de los adjuntos (con OCR si se pidió)", "adjuntos.nombres": "nombres de los adjuntos, separados por coma",
    "adjuntos.cantidad": "cuántos adjuntos trae", "adjuntos.leidos": "nombres de los adjuntos que sí se leyeron",
    "<variable>": "una variable de un nodo anterior", "<variable>.<campo>": "un campo de una variable JSON (p. ej. la fila de buscar_en_tabla)",
}

TIPOS: Dict[str, Dict[str, Any]] = {
    "entrada_correo": {
        "nombre": "Entrada de correo", "categoria": "entrada",
        "descripcion": "Lee asunto, cuerpo, remitente y fecha. Puede leer también los adjuntos; si el flujo tiene un nodo leer_adjuntos, por defecto no los lee aquí.",
        "config": {"leer_adjuntos": {"tipo": "booleano", "defecto": "true (false si hay un nodo leer_adjuntos)", "descripcion": "leer los adjuntos aquí"},
                   "ocr": {"tipo": "booleano", "defecto": True, "descripcion": "OCR (tesseract y visión) al leerlos"}},
        "salidas": ["ok"], "produce": "correo.*, adjuntos.*",
        "ejemplo": {"id": "correo", "tipo": "entrada_correo", "nombre": "Correo recibido", "config": {"leer_adjuntos": False}},
    },
    "leer_adjuntos": {
        "nombre": "Leer adjuntos", "categoria": "ocr",
        "descripcion": "Convierte los adjuntos a texto con el motor de PQRD: texto propio del PDF/Word/Excel, OCR con tesseract y visión del modelo en hojas flojas.",
        "config": {"ocr": {"tipo": "booleano", "defecto": True, "descripcion": "aplicar OCR a escaneos e imágenes"},
                   "vision": {"tipo": "booleano", "defecto": True, "descripcion": "transcribir con el modelo las hojas que tesseract lee mal"},
                   "tipos": {"tipo": "lista", "descripcion": "solo estos tipos: pdf, imagen, docx, xlsx, texto, p7m o extensiones como «.pdf»"},
                   "nombre_patron": {"tipo": "regex", "descripcion": "solo adjuntos cuyo nombre coincida"},
                   "max_paginas": {"tipo": "entero 1..60", "defecto": 60, "descripcion": "páginas por PDF"},
                   "max_caracteres": {"tipo": "entero", "defecto": 60000, "descripcion": "caracteres por adjunto"},
                   "salida": {"tipo": "variable", "descripcion": "además de adjuntos.texto, deja el texto en esta variable"}},
        "salidas": ["ok", "sin_adjuntos"], "produce": "adjuntos.texto, adjuntos.leidos (+ salida)",
        "ejemplo": {"id": "adjuntos", "tipo": "leer_adjuntos", "nombre": "Leer PDF", "config": {"tipos": ["pdf", "imagen"], "ocr": True, "max_paginas": 10}},
    },
    "filtro": {
        "nombre": "Filtro", "categoria": "filtro",
        "descripcion": "Compara un dato con un valor y sale por «si» o «no».",
        "config": {"campo": {"tipo": "texto", "obligatorio": True, "descripcion": "asunto, cuerpo, remitente, adjuntos, texto (todo), una ruta (correo.asunto) o una variable"},
                   "operador": {"tipo": "contiene|coincide|no_coincide|igual|vacio|no_vacio", "defecto": "contiene"},
                   "valor": {"tipo": "texto", "descripcion": "texto o regex (coincide); obligatorio salvo vacio/no_vacio"}},
        "salidas": ["si", "no"],
        "ejemplo": {"id": "es_juzgado", "tipo": "filtro", "nombre": "¿Viene de un juzgado?", "config": {"campo": "remitente", "operador": "coincide", "valor": "@cendoj\\.ramajudicial\\.gov\\.co"}},
    },
    "condicion": {
        "nombre": "Condición", "categoria": "ruta",
        "descripcion": "Igual que filtro, pensado para variables ya obtenidas.",
        "config": {"campo": {"tipo": "texto", "obligatorio": True}, "operador": {"tipo": "como filtro"}, "valor": {"tipo": "texto"}},
        "salidas": ["si", "no"],
        "ejemplo": {"id": "tiene_cedula", "tipo": "condicion", "nombre": "¿Hay cédula?", "config": {"campo": "documento", "operador": "no_vacio"}},
    },
    "ia_clasificar": {
        "nombre": "IA · clasificar", "categoria": "ia",
        "descripcion": "La IA elige una categoría con su confianza y evidencia. Sale por el valor de la categoría: arista por cada una o una por defecto.",
        "config": {"categorias": {"tipo": "lista [{valor, descripcion}]", "obligatorio": True},
                   "variable": {"tipo": "variable", "obligatorio": "variable o salida", "descripcion": "dónde deja la categoría"},
                   "instrucciones": {"tipo": "texto con plantillas"}},
        "salidas": ["<cada categoría>", "sin_categoria (opcional)"], "produce": "variable/salida",
        "ejemplo": {"id": "tema", "tipo": "ia_clasificar", "nombre": "¿De qué trata?", "config": {
            "instrucciones": "Clasifica el correo según lo que pide a la ARL.", "variable": "tema",
            "categorias": [{"valor": "incapacidad", "descripcion": "pide pago o transcripción de una incapacidad"}, {"valor": "otro", "descripcion": "cualquier otra cosa"}]}},
    },
    "ia_decidir": {
        "nombre": "IA · decidir", "categoria": "ia",
        "descripcion": "La IA responde una pregunta eligiendo una opción según criterios explícitos y justifica. Sale por el valor elegido.",
        "config": {"pregunta": {"tipo": "texto con plantillas", "obligatorio": "pregunta o instrucciones"},
                   "opciones": {"tipo": "lista [{valor, criterio}]", "obligatorio": True, "descripcion": "al menos dos"},
                   "salida": {"tipo": "variable", "defecto": "id del nodo", "descripcion": "deja el valor y <salida>_justificacion"}},
        "salidas": ["<cada opción>", "sin_decision (opcional)"], "produce": "salida, salida_justificacion",
        "ejemplo": {"id": "urgencia", "tipo": "ia_decidir", "nombre": "¿Es urgente?", "config": {
            "pregunta": "¿La solicitud exige atención inmediata?", "entradas": ["correo", "datos"], "salida": "urgencia",
            "opciones": [{"valor": "urgente", "criterio": "hay riesgo para la vida, un plazo judicial o una hospitalización"},
                         {"valor": "normal", "criterio": "cualquier otro caso"}]}},
    },
    "ia_extraer": {
        "nombre": "IA · extraer datos", "categoria": "ia",
        "descripcion": "La IA extrae cada campo con confianza y evidencia; deja vacío lo que no está. Cada campo queda como variable.",
        "config": {"campos": {"tipo": "lista [{nombre, descripcion, tipo: texto|numero|fecha|lista|booleano, obligatorio}]", "obligatorio": True},
                   "instrucciones": {"tipo": "texto con plantillas"},
                   "salida": {"tipo": "variable", "descripcion": "además, deja todos los campos juntos como JSON"}},
        "salidas": ["ok"], "produce": "<cada campo> (+ salida)",
        "ejemplo": {"id": "datos", "tipo": "ia_extraer", "nombre": "Datos del trabajador", "config": {
            "entradas": ["correo", "adjuntos.texto"], "salida": "datos",
            "campos": [{"nombre": "nombre", "descripcion": "nombre completo del trabajador", "obligatorio": True},
                       {"nombre": "documento", "descripcion": "número de cédula", "tipo": "numero", "obligatorio": True}]}},
    },
    "extraer_regex": {
        "nombre": "Extraer con regex", "categoria": "busqueda",
        "descripcion": "Extracción determinista con expresiones regulares (sin IA). Sale por «ok» o «incompleto» si falta un obligatorio.",
        "config": {"de": {"tipo": "plantilla o variable", "defecto": "asunto + cuerpo + adjuntos"},
                   "campos": {"tipo": "lista [{nombre, patron, grupo, todos, obligatorio}]", "obligatorio": "campos o patron+salida",
                              "descripcion": "grupo: 1 si el patrón tiene grupos, si no 0; todos: lista con todas las coincidencias"}},
        "salidas": ["ok", "incompleto"], "produce": "<cada campo>",
        "ejemplo": {"id": "radicado", "tipo": "extraer_regex", "nombre": "Radicado del juzgado", "config": {
            "de": "{{correo.asunto}} {{correo.cuerpo}}", "campos": [{"nombre": "radicado", "patron": "\\b(\\d{23})\\b", "obligatorio": True}]}},
    },
    "validar": {
        "nombre": "Validación", "categoria": "validacion",
        "descripcion": "Revisa reglas sobre variables. Sale por «ok» o «falla» (con los reparos).",
        "config": {"reglas": {"tipo": "lista [{campo, regla: obligatorio|patron|en_lista|longitud, valor, codigo, mensaje}]", "obligatorio": True,
                              "descripcion": "patron: regex completo; en_lista: lista; longitud: [min, max]"}},
        "salidas": ["ok", "falla"], "produce": "_motivo (+ salida con los reparos)",
        "ejemplo": {"id": "revisar", "tipo": "validar", "nombre": "¿Están los datos?", "config": {"reglas": [
            {"campo": "nombre", "regla": "obligatorio", "codigo": "SIN_NOMBRE"},
            {"campo": "documento", "regla": "patron", "valor": "[0-9]{6,10}", "codigo": "DOCUMENTO_INVALIDO", "mensaje": "la cédula debe tener entre 6 y 10 dígitos"}]}},
    },
    "transformar": {
        "nombre": "Transformar", "categoria": "datos",
        "descripcion": "Asigna, combina y limpia variables sin IA. Una lista de operaciones que se aplican en orden; cada una deja su resultado en «salida».",
        "config": {"operaciones": {"tipo": "lista [{op, salida, ...}]", "obligatorio": True, "descripcion":
            "op: plantilla {plantilla} | asignar {valor} | mayusculas|minusculas|titulo|limpiar|solo_digitos {de} | "
            "regex_extraer {de, patron, grupo, defecto} | regex_reemplazar {de, patron, reemplazo} | concatenar {de: [..], separador} | defecto {de, valor}"}},
        "salidas": ["ok"], "produce": "<salida de cada operación>",
        "ejemplo": {"id": "normalizar", "tipo": "transformar", "nombre": "Limpiar datos", "config": {"operaciones": [
            {"op": "solo_digitos", "de": "documento", "salida": "documento"},
            {"op": "titulo", "de": "nombre", "salida": "nombre"},
            {"op": "plantilla", "plantilla": "{{nombre}} (CC {{documento}})", "salida": "titular"}]}},
    },
    "buscar_en_tabla": {
        "nombre": "Buscar en tabla", "categoria": "datos",
        "descripcion": "Busca un valor en un CSV del repositorio o en una tabla embebida y trae columnas de la fila. Sale por «encontrado» o «no_encontrado».",
        "config": {"buscar": {"tipo": "variable o plantilla", "obligatorio": True},
                   "tabla": {"tipo": "lista de objetos", "obligatorio": "tabla o archivo"},
                   "archivo": {"tipo": "ruta .csv relativa al repo", "obligatorio": "tabla o archivo"},
                   "columna": {"tipo": "texto", "obligatorio": True, "descripcion": "columna donde buscar"},
                   "traer": {"tipo": "lista", "defecto": "todas", "descripcion": "columnas a traer"},
                   "modo": {"tipo": "igual|contiene", "defecto": "igual", "descripcion": "sin distinguir mayúsculas ni tildes"},
                   "salida": {"tipo": "variable", "defecto": "id del nodo", "descripcion": "la fila como JSON: {{salida.columna}}"}},
        "salidas": ["encontrado", "no_encontrado"], "produce": "salida",
        "ejemplo": {"id": "area", "tipo": "buscar_en_tabla", "nombre": "Área responsable", "config": {
            "buscar": "tema", "columna": "tema", "traer": ["area", "correo"], "salida": "area",
            "tabla": [{"tema": "incapacidad", "area": "Prestaciones económicas", "correo": "prestaciones@ejemplo.gov.co"},
                      {"tema": "afiliacion", "area": "Afiliaciones", "correo": "afiliaciones@ejemplo.gov.co"}]}},
    },
    "bifurcar_por_valor": {
        "nombre": "Bifurcar por valor", "categoria": "ruta",
        "descripcion": "Switch: sale por el caso que coincide con el valor de la variable (sin distinguir mayúsculas ni tildes) o por «otro».",
        "config": {"variable": {"tipo": "variable o plantilla", "obligatorio": True}, "casos": {"tipo": "lista de valores", "obligatorio": True}},
        "salidas": ["<cada caso>", "otro"],
        "ejemplo": {"id": "por_tema", "tipo": "bifurcar_por_valor", "nombre": "Según el tema", "config": {"variable": "tema", "casos": ["incapacidad", "afiliacion"]}},
    },
    "ia_generar": {
        "nombre": "IA · generar", "categoria": "ia",
        "descripcion": "Genera texto libre o JSON (con esquema opcional) a partir de sus entradas y lo deja en su variable de salida.",
        "config": {"instrucciones": {"tipo": "texto con plantillas", "obligatorio": True},
                   "formato": {"tipo": "texto|json", "defecto": "texto (json si hay esquema)"},
                   "esquema": {"tipo": "JSON schema {type, properties, required} o atajo {campo: descripcion}"},
                   "mensaje": {"tipo": "plantilla", "descripcion": "texto extra que se añade a lo que recibe el modelo"},
                   "salida": {"tipo": "variable", "defecto": "id del nodo"},
                   "temperatura": {"tipo": "número", "defecto": 0.3}, "modelo": {"tipo": "texto"}},
        "salidas": ["ok"], "produce": "salida",
        "ejemplo": {"id": "respuesta", "tipo": "ia_generar", "nombre": "Redactar respuesta", "config": {
            "instrucciones": "Redacta una respuesta formal y breve a {{correo.remitente_nombre}} confirmando que recibimos su solicitud con los datos dados. No inventes datos.",
            "entradas": ["correo.asunto", "datos"], "salida": "respuesta", "temperatura": 0.4}},
    },
    "resumir": {
        "nombre": "IA · resumir", "categoria": "ia",
        "descripcion": "Atajo de ia_generar: resume lo que recibe.",
        "config": {"longitud": {"tipo": "texto", "defecto": "un párrafo breve", "descripcion": "p. ej. «3 viñetas», «50 palabras»"},
                   "enfoque": {"tipo": "texto", "descripcion": "qué priorizar"}, "instrucciones": {"tipo": "texto"},
                   "salida": {"tipo": "variable", "defecto": "resumen"}},
        "salidas": ["ok"], "produce": "salida",
        "ejemplo": {"id": "resumen", "tipo": "resumir", "nombre": "Resumen del caso", "config": {"entradas": ["correo", "adjuntos.texto"], "longitud": "3 viñetas", "salida": "resumen"}},
    },
    "ia_redactar": {
        "nombre": "IA · redactar", "categoria": "ia",
        "descripcion": "La IA redacta un texto (forma antigua de ia_generar).",
        "config": {"instrucciones": {"tipo": "texto con plantillas"}, "variable": {"tipo": "variable", "defecto": "respuesta"}},
        "salidas": ["ok"], "produce": "variable/salida",
        "ejemplo": {"id": "redactar", "tipo": "ia_redactar", "nombre": "Respuesta al ciudadano", "config": {"instrucciones": "Redacta una respuesta breve y formal.", "variable": "respuesta"}},
    },
    "notificar": {
        "nombre": "Notificar", "categoria": "integracion",
        "descripcion": "Arma un aviso (destino, asunto, cuerpo) y lo anota como escritura bloqueada. NUNCA se envía.",
        "config": {"para": {"tipo": "plantilla", "obligatorio": True}, "cuerpo": {"tipo": "plantilla", "obligatorio": True},
                   "asunto": {"tipo": "plantilla"}, "cc": {"tipo": "plantilla"}, "canal": {"tipo": "texto", "defecto": "correo"},
                   "salida": {"tipo": "variable", "defecto": "id del nodo"}},
        "salidas": ["ok"], "produce": "salida (el mensaje armado)",
        "ejemplo": {"id": "avisar_area", "tipo": "notificar", "nombre": "Avisar al área", "config": {
            "para": "{{area.correo}}", "asunto": "Nuevo caso: {{correo.asunto}}", "cuerpo": "{{resumen}}"}},
    },
    "enviar": {
        "nombre": "Enviar respuesta", "categoria": "integracion",
        "descripcion": "Arma la respuesta al remitente y la anota como escritura bloqueada. NUNCA se envía.",
        "config": {"cuerpo": {"tipo": "plantilla", "obligatorio": True}, "para": {"tipo": "plantilla", "defecto": "{{correo.remitente_correo}}"},
                   "asunto": {"tipo": "plantilla", "defecto": "RE: {{correo.asunto}}"}, "cc": {"tipo": "plantilla"},
                   "salida": {"tipo": "variable", "defecto": "id del nodo"}},
        "salidas": ["ok"], "produce": "salida (el mensaje armado)",
        "ejemplo": {"id": "responder", "tipo": "enviar", "nombre": "Responder al remitente", "config": {"cuerpo": "{{respuesta}}"}},
    },
    "salida": {
        "nombre": "Salida", "categoria": "salida",
        "descripcion": "Termina el flujo con un desenlace. Entrega la solicitud con los campos indicados y, si se da, la respuesta armada con la plantilla.",
        "config": {"desenlace": {"tipo": "|".join(DESENLACES), "obligatorio": True},
                   "motivo": {"tipo": "plantilla"}, "destino": {"tipo": "plantilla"},
                   "campos": {"tipo": "lista de variables o {nombre: plantilla}", "defecto": "todas las variables"},
                   "plantilla_respuesta": {"tipo": "plantilla", "descripcion": "el texto final que entrega el flujo"}},
        "salidas": [],
        "ejemplo": {"id": "radicar", "tipo": "salida", "nombre": "Radicar", "config": {
            "desenlace": "radicar", "campos": ["nombre", "documento"], "plantilla_respuesta": "{{respuesta}}"}},
    },
}

EJEMPLO_FLUJO: Dict[str, Any] = {
    "id": "respuesta-con-datos", "nombre": "Respuesta con datos", "descripcion": "Lee los PDF, extrae los datos, redacta la respuesta y avisa al área.",
    "modelo": "gemini-2.5-flash",
    "nodos": [
        {"id": "correo", "tipo": "entrada_correo", "nombre": "Correo", "config": {"leer_adjuntos": False}},
        {"id": "adjuntos", "tipo": "leer_adjuntos", "nombre": "Leer adjuntos", "config": {"tipos": ["pdf", "imagen"], "max_paginas": 10}},
        {"id": "datos", "tipo": "ia_extraer", "nombre": "Datos", "config": {"entradas": ["correo", "adjuntos.texto"], "salida": "datos", "campos": [
            {"nombre": "nombre", "descripcion": "nombre completo del solicitante", "obligatorio": True},
            {"nombre": "documento", "descripcion": "número de documento", "tipo": "numero"}]}},
        {"id": "respuesta", "tipo": "ia_generar", "nombre": "Redactar", "config": {"instrucciones": "Redacta una respuesta formal y breve al remitente confirmando los datos recibidos. No inventes.",
                                                                                    "entradas": ["correo.asunto", "datos"], "salida": "respuesta", "temperatura": 0.4}},
        {"id": "avisar", "tipo": "notificar", "nombre": "Avisar", "config": {"para": "mesa@ejemplo.gov.co", "asunto": "Caso de {{nombre}}", "cuerpo": "{{respuesta}}"}},
        {"id": "fin", "tipo": "salida", "nombre": "Responder", "config": {"desenlace": "responder", "campos": ["nombre", "documento"], "plantilla_respuesta": "{{respuesta}}"}},
    ],
    "aristas": [{"desde": "correo", "hacia": "adjuntos"}, {"desde": "adjuntos", "hacia": "datos"}, {"desde": "datos", "hacia": "respuesta"},
                {"desde": "respuesta", "hacia": "avisar"}, {"desde": "avisar", "hacia": "fin"}],
}

REGLAS = """Un flujo declarativo es {id, nombre, descripcion, modelo, nodos: [{id, tipo, nombre, descripcion, config}], aristas: [{desde, hacia, cuando}]}.
- id del flujo: minúsculas con guiones (ej. "derechos-de-peticion"); id de nodo: minúsculas con guion bajo.
- Exactamente un nodo entrada_correo y al menos una salida. Todo nodo alcanzable desde la entrada; sin ciclos; las salidas no tienen aristas que salgan.
- Una arista se toma si "cuando" es igual a la salida del nodo (sin distinguir mayúsculas ni tildes); una arista sin "cuando" (o "*") es la de por defecto.
  Cada salida posible del nodo necesita su arista o una por defecto (p. ej. ia_clasificar: una por categoría o una por defecto).
- Paso de datos: cada nodo puede declarar en config "entradas" (lo que recibe) y "salida" (la variable donde deja su resultado).
  En los nodos de IA, con "entradas" el modelo ve SOLO eso; sin "entradas" ve el correo, los adjuntos y las variables.
- Plantillas {{...}} en instrucciones, textos, motivos, destinos y mensajes: {{correo.asunto}}, {{correo.cuerpo}}, {{correo.remitente}},
  {{correo.remitente_correo}}, {{correo.remitente_nombre}}, {{adjuntos.texto}}, {{adjuntos.nombres}}, {{<variable>}}, {{<variable>.<campo>}}.
  Toda variable usada tiene que salir de un nodo anterior en algún camino; si en la corrida no tiene valor, queda vacía.
- Nada se envía ni se escribe en sistemas reales: notificar y enviar solo arman el mensaje y lo anotan como escritura bloqueada.
- Modelos: gemini-2.5-flash (por defecto), gemini-2.5-pro, gemini-3-flash-preview, gemini-3.1-flash-lite."""


def _linea_config(conf: Dict[str, Any]) -> str:
    partes = []
    for k, v in conf.items():
        extra = []
        if v.get("obligatorio") is True:
            extra.append("obligatorio")
        elif isinstance(v.get("obligatorio"), str):
            extra.append(v["obligatorio"])
        if "defecto" in v:
            extra.append(f"defecto {json.dumps(v['defecto'], ensure_ascii=False) if not isinstance(v['defecto'], str) else v['defecto']}")
        desc = f": {v['descripcion']}" if v.get("descripcion") else ""
        partes.append(f"{k} ({v.get('tipo', '')}{'; ' + '; '.join(extra) if extra else ''}){desc}")
    return "; ".join(partes)


def _documentacion() -> str:
    lineas = [REGLAS, "", "Config común: " + _linea_config(COMUNES), "", "Tipos (config · salidas · ejemplo):"]
    for tipo, t in TIPOS.items():
        sal = ", ".join(t["salidas"]) or "termina el flujo"
        lineas.append(f"- {tipo}: {t['descripcion']}")
        lineas.append(f"  config: {_linea_config(t['config'])}")
        lineas.append(f"  sale por: {sal}" + (f" · deja: {t['produce']}" if t.get("produce") else ""))
        lineas.append(f"  ejemplo: {json.dumps(t['ejemplo'], ensure_ascii=False)}")
    lineas += ["", "Ejemplo de flujo completo (sin OCR en la entrada, adjuntos aparte, IA que extrae y redacta, aviso simulado):",
               json.dumps(EJEMPLO_FLUJO, ensure_ascii=False)]
    return "\n".join(lineas)


DOCUMENTACION = _documentacion()


def esquema() -> Dict[str, Any]:
    """La especificación de tipos, serializable a JSON."""
    return copy.deepcopy({"version": VERSION, "reglas": REGLAS, "comunes": COMUNES, "plantillas": PLANTILLAS,
                          "desenlaces": DESENLACES, "tipos": TIPOS, "ejemplo_flujo": EJEMPLO_FLUJO})


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] == ["json"]:
        print(json.dumps(esquema(), ensure_ascii=False, indent=2))
    else:
        print(DOCUMENTACION)
