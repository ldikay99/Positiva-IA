"""
La biblioteca de nodos de los flujos declarativos, en un solo lugar.

- `TIPOS`: la especificación de cada tipo: qué hace, su `config` como JSON
  Schema (el subconjunto que entiende el editor: type, properties, required,
  enum, items, description, default, minimum, maximum, title y format ∈
  plantilla, credencial, variable, expresion, texto-largo, cron), por dónde
  sale, si es disparador, si escribe afuera, qué variables deja, un ejemplo de
  nodo, `ejemplos` de config para copiar y `consejos` cortos.
- `tipos_contrato()`: la lista que sirve `GET /api/nodos/tipos` (TipoNodo[]).
- `DOCUMENTACION`: el texto que Vera pone en su prompt para crear flujos.
- `esquema()`: todo junto en JSON, para la web o para pruebas.

El motor (`flujo_declarativo.py`) es quien valida y ejecuta; las pruebas
comprueban que el motor, esta biblioteca y el zod del servidor conozcan los
mismos tipos y acepten los mismos ejemplos.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional, Sequence

VERSION = 7

DESENLACES = ["radicar", "manual", "reenviar", "descartar", "responder", "duplicado"]
DISPARADORES = ["entrada_correo", "entrada_manual", "programado", "webhook", "correo_nuevo", "entrada_archivos"]
# Lo que acepta entrada_archivos por defecto (video y audio nunca).
TIPOS_PERMITIDOS = ["jpg", "jpeg", "png", "webp", "tif", "tiff", "bmp", "gif", "heic", "pdf", "doc", "docx", "xls", "xlsx", "csv",
                    "ppt", "pptx", "txt", "md", "json", "xml", "html", "eml", "msg", "zip", "rtf", "odt", "ods"]
TIPOS_CREDENCIAL_IA = ["ia_gemini", "ia_openai", "ia_azure_openai", "ia_anthropic", "ia_deepseek", "ia_mistral", "ia_groq", "ia_openrouter",
                       "ia_ollama", "ia_compatible", "ia_cohere", "ia_personalizado"]
TIPOS_CREDENCIAL = ["imap", "smtp", "microsoft_graph", "google_servicio", "postgres", "mysql", "sqlite", "http_bearer", "http_basico",
                    "webhook_teams", "webhook_slack", "webhook_saliente", *TIPOS_CREDENCIAL_IA]
# Los proveedores de IA (los registra puente/proveedores/; una prueba compara las listas).
PROVEEDORES_IA = ["vertex", "gemini", "openai", "azure_openai", "deepseek", "mistral", "groq", "openrouter", "ollama", "openai_compatible",
                  "anthropic", "cohere", "personalizado"]


# ----------------------------------------------------------- ayudas de esquema

def _q(d: Dict[str, Any], desc: str = "", defecto: Any = None, titulo: str = "") -> Dict[str, Any]:
    if desc:
        d["description"] = desc
    if defecto is not None:
        d["default"] = defecto
    if titulo:
        d["title"] = titulo
    return d


def texto(desc: str = "", defecto: Any = None, titulo: str = "", formato: str = "") -> Dict[str, Any]:
    return _q({"type": "string", **({"format": formato} if formato else {})}, desc, defecto, titulo)


def plantilla(desc: str = "", defecto: Any = None, titulo: str = "", largo: bool = False) -> Dict[str, Any]:
    d = texto(desc, defecto, titulo, "plantilla")
    if largo:
        d["x-largo"] = True
    return d


def largo(desc: str = "", titulo: str = "") -> Dict[str, Any]:
    return texto(desc, None, titulo, "texto-largo")


def variable(desc: str = "", defecto: Any = None, titulo: str = "") -> Dict[str, Any]:
    return texto(desc, defecto, titulo, "variable")


def expresion(desc: str = "", titulo: str = "") -> Dict[str, Any]:
    return texto(desc, None, titulo, "expresion")


def cron(desc: str = "") -> Dict[str, Any]:
    return texto(desc, None, "", "cron")


def credencial(tipos: Sequence[str], desc: str = "") -> Dict[str, Any]:
    return {**texto(desc or f"nombre de una credencial guardada ({' o '.join(tipos)})", None, "Credencial", "credencial"),
            "x-credencial-tipos": list(tipos)}


def booleano(desc: str = "", defecto: Any = None) -> Dict[str, Any]:
    return _q({"type": "boolean"}, desc, defecto)


def entero(desc: str = "", minimo: Optional[int] = None, maximo: Optional[int] = None, defecto: Any = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"type": "integer"}
    if minimo is not None:
        d["minimum"] = minimo
    if maximo is not None:
        d["maximum"] = maximo
    return _q(d, desc, defecto)


def numero(desc: str = "", minimo: Optional[float] = None, maximo: Optional[float] = None, defecto: Any = None) -> Dict[str, Any]:
    d = entero(desc, None, None, defecto)
    d["type"] = "number"
    if minimo is not None:
        d["minimum"] = minimo
    if maximo is not None:
        d["maximum"] = maximo
    return d


def opcion(valores: Sequence[str], desc: str = "", defecto: Any = None) -> Dict[str, Any]:
    return _q({"type": "string", "enum": list(valores)}, desc, defecto)


def lista(items: Dict[str, Any], desc: str = "", minimo: Optional[int] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"type": "array", "items": items}
    if minimo is not None:
        d["minItems"] = minimo
    return _q(d, desc)


def objeto(props: Dict[str, Any], requeridos: Sequence[str] = (), desc: str = "", libre: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"type": "object", "properties": props}
    if requeridos:
        d["required"] = list(requeridos)
    if libre is not None:
        d["additionalProperties"] = libre
    return _q(d, desc)


def mapa(valores: Dict[str, Any], desc: str = "") -> Dict[str, Any]:
    """Un objeto {nombre: valor} de claves libres."""
    return _q({"type": "object", "additionalProperties": valores}, desc)


def libre(desc: str = "") -> Dict[str, Any]:
    return _q({}, desc)


def conf(props: Dict[str, Any], requeridos: Sequence[str] = ()) -> Dict[str, Any]:
    return objeto(props, requeridos)


SALIDA = lambda defecto="id del nodo", desc="dónde deja su resultado": variable(f"{desc} (por defecto: {defecto})")  # noqa: E731
TIMEOUT = entero("segundos de espera de la conexión y de la operación", 1, 60, 20)
MAX_FILAS = entero("tope de filas que se traen", 1, 5000, 500)

# Lo que todo nodo puede declarar, además de su config propia (documentación para el prompt).
COMUNES: Dict[str, Dict[str, Any]] = {
    "entradas": {"tipo": "lista de texto", "descripcion": "Lo que el nodo recibe: variables («datos», «datos.nombre») o datos del correo "
                 "(«correo», «correo.cuerpo», «adjuntos.texto»). En los nodos de IA, si se da, el modelo ve SOLO esto; si no, ve el correo, los adjuntos y las variables."},
    "salida": {"tipo": "nombre de variable", "descripcion": "Dónde deja su resultado (letras, números y guion bajo)."},
    "proveedor": {"tipo": "texto", "descripcion": "Solo nodos de IA (y leer_adjuntos para la visión): proveedor de IA de este nodo "
                  "(vertex, gemini, openai, azure_openai, deepseek, mistral, groq, openrouter, ollama, openai_compatible, anthropic, cohere, personalizado); si no, el del flujo."},
    "modelo": {"tipo": "texto", "descripcion": "Solo nodos de IA: modelo de este nodo (si no, el del flujo)."},
    "credencial_ia": {"tipo": "credencial ia_<proveedor>", "descripcion": "Solo nodos de IA: en config.credencial, el NOMBRE de la conexión con la llave del proveedor "
                      "(si no, la del flujo). Vertex no la necesita."},
    "temperatura": {"tipo": "número 0..2", "descripcion": "Solo nodos de IA: creatividad (0,1 por defecto; 0,4–0,7 para redactar)."},
    "deshabilitado": {"tipo": "booleano", "defecto": False, "descripcion": "No se ejecuta: sigue por la arista por defecto (o la primera). No vale en disparadores, salida, fin_ciclo, para_cada, paralelo ni unir."},
    "continuar_si_falla": {"tipo": "booleano", "defecto": False, "descripcion": "Si el nodo falla, sigue por su arista normal y anota el fallo como aviso (y en {{fallo.*}})."},
    "reintentos": {"tipo": "{intentos 1..5, espera_ms, backoff, maximo_ms}", "descripcion": "Solo IA, conectores, enviar, notificar y http: repite ante fallos pasajeros "
                   "(429, 5xx, tiempo agotado, conexión) con espera exponencial al azar; intentos cuenta el primero. Nunca reintenta errores de datos."},
}

# Los mismos comunes como JSON Schema (se agregan a cada tipo según aplique, con x-comun).
_REINTENTOS = objeto({"intentos": entero("intentos en total, contando el primero", 1, 5, 1),
                      "espera_ms": entero("espera inicial", 0, 60000, 500), "backoff": numero("factor por intento", 1, 10, 2),
                      "maximo_ms": entero("tope de espera", 0, 120000, 10000)}, ["intentos"], "repite ante fallos pasajeros")
ESQUEMA_COMUNES: Dict[str, Dict[str, Any]] = {
    "entradas": lista(texto(), COMUNES["entradas"]["descripcion"]),
    "proveedor": {**opcion(PROVEEDORES_IA, "proveedor de IA de este nodo (si no, el del flujo)"), "title": "Proveedor de IA", "format": "proveedor-ia"},
    "modelo": {**texto(COMUNES["modelo"]["descripcion"]), "format": "modelo-ia"},
    "credencial": credencial(TIPOS_CREDENCIAL_IA, "conexión con la llave del proveedor de este nodo (si no, la del flujo)"),
    "temperatura": numero(COMUNES["temperatura"]["descripcion"], 0, 2),
    "deshabilitado": booleano("no se ejecuta: el flujo pasa de largo", False),
    "continuar_si_falla": booleano(COMUNES["continuar_si_falla"]["descripcion"], False),
    "reintentos": _REINTENTOS,
}

PLANTILLAS = {
    "correo.asunto": "asunto del correo", "correo.cuerpo": "cuerpo en texto plano", "correo.remitente": "«Nombre <correo>»",
    "correo.remitente_correo": "solo la dirección", "correo.remitente_nombre": "solo el nombre", "correo.recibido": "fecha de recepción",
    "adjuntos.texto": "texto leído de los adjuntos (con OCR si se pidió)", "adjuntos.nombres": "nombres de los adjuntos, separados por coma",
    "adjuntos.cantidad": "cuántos adjuntos trae", "adjuntos.leidos": "nombres de los adjuntos que sí se leyeron",
    "archivos.texto": "texto leído de los archivos subidos a la corrida (con OCR y visión)", "archivos.nombres": "nombres de los archivos subidos",
    "archivos.cantidad": "cuántos archivos se subieron", "archivos.lista": "la ficha de cada archivo [{id, nombre, tipo, tamano, sha256}]",
    "entrada.<campo>": "lo que trae el disparador (entrada_manual, entrada_archivos, programado, webhook, correo_nuevo)",
    "<variable>": "una variable de un nodo anterior", "<variable>.<campo>": "un campo de una variable JSON (p. ej. la fila de buscar_en_tabla)",
    "<lista>.0.<campo>": "un elemento de una lista por su posición (desde 0); también <lista>[0]",
    "<lista>[campo=valor].<otro>": "el primer elemento cuyo campo vale eso (sin distinguir mayúsculas ni tildes); con espacios: [campo=\"dos palabras\"]",
    "<variable>[\"a.b\"]": "una clave que tiene puntos o espacios, entre comillas",
    "fallo.motivo": "en la rama de error: por qué falló (también fallo.nodo, fallo.nombre, fallo.tipo, fallo.error)",
}

_CAMPO_ENTRADA = objeto({"nombre": variable("nombre del campo (queda en {{entrada.<nombre>}})"),
                         "tipo": opcion(["texto", "numero", "booleano", "fecha", "lista", "objeto", "archivo", "archivos"],
                                        "tipo del valor; archivo y archivos se suben en «Correr» (nunca video ni audio)", "texto"),
                         "obligatorio": booleano("si falta, la corrida termina en error", False),
                         "descripcion": texto("para qué sirve")}, ["nombre"])
_FILTRO_CORREO = objeto({"remitente": plantilla("contiene este remitente"), "asunto": plantilla("el asunto contiene"),
                         "texto": plantilla("el asunto o el cuerpo contiene"), "no_leidos": booleano("solo los no leídos", False)},
                        desc="qué correos traer")
# Archivos que se mandan o se entregan: los generados en la corrida, los subidos, los adjuntos del correo o del flujo.
_ARCHIVOS_DESC = ("lista de archivos: «{{documento}}» o «{{pdf.id}}» (generados en la corrida), «{{imagenes}}» (una lista), «{{archivos}}» "
                  "(los subidos a la corrida), «{{adjuntos}}» (los del correo original), un id «a-…» de un archivo del flujo, o un filtro "
                  "{de: \"adjuntos\", nombre: \"factura\", tipo: \"pdf\"} (nombre sin distinguir mayúsculas ni tildes; tipo pdf, imagen, word, excel, "
                  "texto o «.ext»). Hasta 10 archivos y 20 MB en total.")
ARCHIVOS = lambda desc=_ARCHIVOS_DESC: {**libre(desc), "format": "archivos"}  # noqa: E731
# La IA redacta el documento o arma la hoja con sus instrucciones (y su IA propia, si se da).
DESDE_IA = objeto({"instrucciones": plantilla("qué redactar o armar, con {{variables}}", largo=True),
                   "entradas": lista(texto(), "lo que ve la IA (variables, «correo», «adjuntos.texto»); si no, ve todo el contexto"),
                   "proveedor": {**opcion(PROVEEDORES_IA, "proveedor de IA (si no, el del flujo)"), "format": "proveedor-ia"},
                   "modelo": {**texto("modelo (si no, el del flujo)"), "format": "modelo-ia"},
                   "credencial": credencial(TIPOS_CREDENCIAL_IA, "conexión con la llave del proveedor (si no, la del flujo)"),
                   "temperatura": numero("creatividad", 0, 2, 0.3)}, ["instrucciones"],
                  "la IA redacta el contenido (en vez de una plantilla)")

# ------------------------------------------------- búsqueda y comparación
#
# Lo mismo en filtro, condición, tabla_consultar, extraer_regex, ia_extraer,
# validar, transformar, buscar_en_tabla y bifurcar_por_valor (el motor lo
# implementa en conectores/busqueda.py; las pruebas comprueban que coincidan).

OPERADORES_AYUDA: Dict[str, str] = {
    "contiene": "el texto contiene el valor · «Solicitud de PETICIÓN urgente» contiene «peticion» → sí (sin valor: siempre sí)",
    "no_contiene": "el texto no contiene el valor · asunto no_contiene «respuesta automática»",
    "igual": "el texto entero es el valor · « Petición » igual «peticion» → sí",
    "distinto": "el texto entero no es el valor · estado distinto «cerrado»",
    "empieza_con": "el texto empieza con el valor · «RE: Tutela 2026» empieza_con «re:»",
    "termina_con": "el texto termina con el valor · «ACTA.PDF» termina_con «.pdf»",
    "palabra": "el valor aparece como palabra o frase completa · «una tutela nueva» → sí; «tutelado» → no (valor «tutela»)",
    "contiene_alguna": "contiene al menos uno de valores · valores [«tutela», «desacato»]",
    "contiene_todas": "contiene todos los valores · valores [«incapacidad», «pago»]",
    "en_lista": "el texto entero es uno de valores · «Afiliación» en_lista [«afiliacion», «pagos»] → sí",
    "no_en_lista": "el texto entero no es ninguno de valores",
    "parecido": "se parece al valor aunque tenga errores de escritura (umbral 0,85; mira el texto entero y cada trozo) · «peticion urjente» ≈ «Petición urgente»",
    "coincide": "regex que no distingue mayúsculas ni tildes · «radicad[oa]\\s*\\d{23}»",
    "no_coincide": "la regex no aparece",
    "vacio": "no tiene nada (o solo espacios)",
    "no_vacio": "tiene algo",
    "mayor": "número mayor que el valor · «$ 1.500.000» mayor 1000000 → sí (entiende «1.234,56» y «$ 1,234.56»)",
    "menor": "número menor que el valor",
    "mayor_igual": "número mayor o igual que el valor",
    "menor_igual": "número menor o igual que el valor",
    "entre": "número entre valor y hasta, incluidos (o valor [mínimo, máximo])",
    "antes_de": "fecha anterior al valor · «7 de noviembre de 2026» antes_de «2026-12-01» → sí (ISO, DD/MM/AAAA, DD-MM-AAAA, mes en letras, hoy/ayer/mañana)",
    "despues_de": "fecha posterior al valor · fecha despues_de «hoy»",
    "entre_fechas": "fecha entre valor y hasta, incluidas",
    "longitud_igual": "mide exactamente valor (según contar: caracteres, digitos, letras o palabras) · cédula longitud_igual 10 con contar digitos",
    "longitud_mayor": "mide más que valor",
    "longitud_menor": "mide menos que valor",
    "longitud_entre": "mide entre valor y hasta, incluidos · radicado longitud_entre 20 y 23 con contar digitos",
}
OPERADORES = list(OPERADORES_AYUDA)
COMPARACIONES = ["flexible", "exacta", "parecida"]
CONTAR = ["caracteres", "digitos", "letras", "palabras"]
COMPARACION_AYUDA = {
    "flexible": "por defecto: no importan mayúsculas, tildes ni espacios de más («PETICIÓN» = «peticion» = « Petición » = «peticiòn»)",
    "exacta": "tal cual, letra por letra: mayúsculas, tildes y espacios cuentan («Petición» ≠ «peticion»)",
    "parecida": "tolera errores de escritura: similitud ≥ umbral (0,85 por defecto) sobre el texto sin mayúsculas ni tildes («peticion urjente» ≈ «Petición urgente»)",
}


def _opciones_busqueda() -> Dict[str, Any]:
    return {
        "comparacion": opcion(COMPARACIONES, "flexible (por defecto): sin mayúsculas, tildes ni espacios de más; exacta: tal cual; parecida: tolera errores de escritura"),
        "umbral": numero("parecida y parecido: similitud mínima de 0 a 1 (por defecto 0,85)", 0, 1),
        "distinguir_mayusculas": booleano("con flexible: que sí cuenten las mayúsculas"),
        "distinguir_tildes": booleano("con flexible: que sí cuenten las tildes"),
        "distinguir_espacios": booleano("con flexible: que sí cuenten los espacios de más"),
    }


def _longitud(con_contar: bool = True) -> Dict[str, Any]:
    d = {"longitud": entero("longitud exacta del valor", 0), "longitud_min": entero("longitud mínima", 0), "longitud_max": entero("longitud máxima", 0)}
    if con_contar:
        d["contar"] = opcion(CONTAR, "qué se cuenta en la longitud (por defecto caracteres)")
    d["solo_digitos"] = booleano("quitar todo lo que no sea dígito antes de medir y devolver («1.023.456.789» → 1023456789)")
    return d


def _props_condicion(campo_desc: str) -> Dict[str, Any]:
    return {
        "campo": texto(campo_desc, titulo="Campo"),
        "campos": lista(texto(), "o varios campos a la vez: basta que uno cumpla (con no_contiene, distinto, no_coincide, no_en_lista y vacio, tienen que cumplir todos)"),
        "operador": opcion(OPERADORES, "texto: contiene, igual, empieza_con, palabra, parecido, coincide (regex)…; números: mayor, menor, entre; "
                                      "fechas: antes_de, despues_de, entre_fechas; longitud: longitud_igual, longitud_entre…", "contiene"),
        "valor": libre("con qué comparar: texto, número, fecha, regex (coincide) o plantilla {{…}}; en entre, entre_fechas y longitud_entre, el mínimo"),
        "valores": lista(texto(), "contiene_alguna, contiene_todas, en_lista y no_en_lista: los valores (o en valor separados por | o ;)"),
        "hasta": libre("entre, entre_fechas y longitud_entre: el máximo"),
        "contar": opcion(CONTAR, "operadores de longitud: qué se cuenta (por defecto caracteres)"),
        **_opciones_busqueda(),
    }


def _condicion(campo_desc: str) -> Dict[str, Any]:
    d = objeto(_props_condicion(campo_desc), desc="una condición: campo (o campos), operador y valor")
    d["title"] = "Condición"
    return d


_CAMPO_FILTRO = "dónde buscar: asunto, cuerpo, remitente, adjuntos, texto (todo), una ruta (correo.asunto) o una variable"
_CONDICIONES = lista(_condicion(_CAMPO_FILTRO), "varias condiciones (en vez de campo, operador y valor); cada una sobre uno o varios campos", 1)
_MODO = opcion(["todas", "alguna"], "con condiciones: tienen que cumplirse todas (por defecto) o basta alguna", "todas")


def _texto_busqueda() -> str:
    lineas = ["Búsqueda y comparación (filtro, condicion, tabla_consultar y, donde aplica, extraer_regex, ia_extraer, validar, transformar, "
              "buscar_en_tabla y bifurcar_por_valor):",
              "- Una condición es {campo | campos, operador, valor, valores, hasta, comparacion, umbral, distinguir_mayusculas, distinguir_tildes, "
              "distinguir_espacios, contar}. filtro y condicion aceptan una sola (campo, operador y valor en la config) o varias en "
              "condiciones: [...] con modo \"todas\" (por defecto) o \"alguna\"; no mezcles las dos formas. Salen por «si» o «no».",
              "- campos: [\"asunto\", \"cuerpo\"] busca en varios a la vez: basta que uno cumpla (los negativos no_contiene, distinto, no_coincide, "
              "no_en_lista y vacio tienen que cumplirse en todos).",
              "- comparacion:"]
    lineas += [f"  · {k}: {v}" for k, v in COMPARACION_AYUDA.items()]
    lineas += ["  distinguir_mayusculas / distinguir_tildes / distinguir_espacios: true afinan la flexible.",
               "- Operadores:"]
    lineas += [f"  · {k}: {v}" for k, v in OPERADORES_AYUDA.items()]
    lineas += ["- Las regex (coincide, no_coincide, extraer_regex, regex_extraer, regex_reemplazar, nombre_patron de leer_adjuntos) no distinguen "
               "mayúsculas ni tildes («peticion» encuentra «PETICIÓN») y devuelven el texto ORIGINAL con sus tildes; distinguir_tildes / "
               "distinguir_mayusculas: true lo cambian por campo.",
               "- Longitud: en extraer_regex, regex_extraer e ia_extraer cada campo acepta longitud (exacta), longitud_min, longitud_max, "
               "contar (caracteres, digitos, letras, palabras) y solo_digitos; los candidatos que no cumplen se saltan y se toma el siguiente "
               "(p. ej. el radicado de 23 dígitos y no otro número). En ia_extraer, un valor que no cumple se descarta con aviso y cuenta como faltante.",
               "- Ejemplos: {\"condiciones\": [{\"campos\": [\"asunto\", \"cuerpo\"], \"operador\": \"contiene_alguna\", \"valores\": [\"tutela\", \"desacato\"]}, "
               "{\"campo\": \"correo.remitente_correo\", \"operador\": \"termina_con\", \"valor\": \"ramajudicial.gov.co\"}], \"modo\": \"todas\"} · "
               "{\"campo\": \"monto\", \"operador\": \"entre\", \"valor\": 1000000, \"hasta\": 5000000} · "
               "{\"campo\": \"documento\", \"operador\": \"longitud_entre\", \"valor\": 6, \"hasta\": 10, \"contar\": \"digitos\"} · "
               "{\"campo\": \"asunto\", \"operador\": \"parecido\", \"valor\": \"derecho de peticion\", \"umbral\": 0.8} · "
               "extraer_regex {\"nombre\": \"cedula\", \"patron\": \"\\\\d[\\\\d.]{5,14}\", \"solo_digitos\": true, \"longitud_min\": 6, \"longitud_max\": 10}."]
    return "\n".join(lineas)

TIPOS: Dict[str, Dict[str, Any]] = {
    # ------------------------------------------------------------ disparadores
    "entrada_correo": {
        "nombre": "Entrada de correo", "categoria": "entrada",
        "descripcion": "Corre sobre un correo de la bandeja propia del flujo: su bandeja de prueba (correos .eml o .msg que subes) o los que trae de su buzón "
                       "(credencial imap o microsoft_graph y carpeta, solo lectura). Lee asunto, cuerpo, remitente y fecha, y puede leer los adjuntos; "
                       "si el flujo tiene un nodo leer_adjuntos, por defecto no los lee aquí.",
        "config": conf({"leer_adjuntos": booleano("leer los adjuntos aquí (por defecto sí, salvo que haya un nodo leer_adjuntos)"),
                        "ocr": booleano("OCR (tesseract y visión) al leerlos", True),
                        "credencial": credencial(["imap", "microsoft_graph"], "el buzón propio del flujo (solo lectura); sin ella, solo la bandeja de prueba"),
                        "carpeta": texto("carpeta del buzón", "INBOX"), "filtro": _FILTRO_CORREO,
                        "bandeja_prueba": booleano("aceptar correos de prueba subidos como .eml o .msg", True)}),
        "salidas": ["ok"], "produce": "correo.*, adjuntos.*",
        "ejemplo": {"id": "correo", "tipo": "entrada_correo", "nombre": "Correo recibido", "config": {"leer_adjuntos": False}},
    },
    "entrada_manual": {
        "nombre": "Entrada manual", "categoria": "entrada",
        "descripcion": "Se corre a mano (o la llama otro flujo) con un JSON de entrada. Los campos declarados se revisan y quedan en {{entrada.<campo>}}.",
        "config": conf({"campos": lista(_CAMPO_ENTRADA, "los datos que recibe")}),
        "salidas": ["ok"], "produce": "entrada",
        "ejemplo": {"id": "inicio", "tipo": "entrada_manual", "nombre": "Datos de entrada", "config": {
            "campos": [{"nombre": "documento", "tipo": "texto", "obligatorio": True, "descripcion": "cédula a consultar"}]}},
    },
    "entrada_archivos": {
        "nombre": "Entrada de archivos", "categoria": "entrada",
        "descripcion": "Se corre con archivos que subes en «Correr» (arrastrar y soltar o pegar), por webhook (multipart) o desde el chat: imágenes y documentos, "
                       "nunca video ni audio. Se guardan en el espacio del flujo y se leen como los adjuntos de un correo (conversión, OCR y visión); "
                       "quedan en {{archivos.nombres}}, {{archivos.texto}} y {{archivos.cantidad}}.",
        "config": conf({"tipos_permitidos": lista(texto(), "extensiones aceptadas (por defecto imágenes, PDF, Word, Excel, PowerPoint, texto, correos y ZIP)"),
                        "max_archivos": entero("cuántos archivos por corrida", 1, 50, 20),
                        "max_mb": entero("tamaño máximo de cada archivo, en MB", 1, 25, 25),
                        "leer": booleano("leer los archivos aquí (por defecto sí, salvo que haya un nodo leer_adjuntos)"),
                        "ocr": booleano("OCR (tesseract y visión) al leerlos", True),
                        "campos_extra": lista(_CAMPO_ENTRADA, "otros datos que se piden junto con los archivos")}),
        "salidas": ["ok"], "produce": "archivos.*, adjuntos.*, entrada (archivos y campos_extra)",
        "ejemplo": {"id": "archivos", "tipo": "entrada_archivos", "nombre": "Archivos recibidos", "config": {"max_archivos": 10}},
    },
    "programado": {
        "nombre": "Programado", "categoria": "entrada",
        "descripcion": "Corre solo cada cierto tiempo (cron de 5 campos o cada N minutos), en la zona dada. Nace apagado: activo: true lo enciende. "
                       "Nunca se solapan dos corridas del mismo flujo y no recupera las que se perdieron con la plataforma apagada.",
        "config": conf({"cron": cron("minuto hora día mes día_semana, p. ej. «0 7 * * 1-5» (7 a. m. de lunes a viernes)"),
                        "cada_minutos": entero("alternativa al cron: cada cuántos minutos", 1, 10080),
                        "zona": texto("zona horaria", "America/Bogota"), "activo": booleano("encendido", False)}),
        "salidas": ["ok"], "produce": "entrada (programado, hora, zona)",
        "ejemplo": {"id": "cada_manana", "tipo": "programado", "nombre": "Cada mañana", "config": {"cron": "0 7 * * 1-5", "zona": "America/Bogota", "activo": False}},
    },
    "webhook": {
        "nombre": "Webhook", "categoria": "entrada",
        "descripcion": "Corre cuando llega un POST a /api/ganchos/<flujo>/<secreto>. Recibe {cuerpo, consulta, encabezados} (sin los de autenticación). "
                       "Con responder: true espera hasta esperar_ms lo que arme respuesta_webhook.",
        "config": conf({"secreto": texto("se genera solo al guardar; es parte de la URL, no lo compartas"),
                        "metodo": opcion(["POST"], "método aceptado", "POST"),
                        "responder": booleano("esperar y devolver lo que arme respuesta_webhook", False),
                        "esperar_ms": entero("cuánto esperar la respuesta", 0, 30000, 10000)}),
        "salidas": ["ok"], "produce": "entrada (cuerpo, consulta, encabezados, metodo)",
        "ejemplo": {"id": "recibir", "tipo": "webhook", "nombre": "Recibir aviso", "config": {"responder": True, "esperar_ms": 8000}},
    },
    "correo_nuevo": {
        "nombre": "Correo nuevo", "categoria": "entrada",
        "descripcion": "Revisa un buzón (credencial imap o microsoft_graph, solo lectura, sin marcar como leído) cada N minutos y corre el flujo por cada correo nuevo. "
                       "Recuerda los ya vistos. Nace apagado. Nunca los buzones de Positiva.",
        "config": conf({"credencial": credencial(["imap", "microsoft_graph"]), "carpeta": texto("carpeta del buzón", "INBOX"),
                        "cada_minutos": entero("cada cuánto revisar", 1, 1440, 5), "activo": booleano("encendido", False),
                        "filtro": _FILTRO_CORREO, "max_por_sondeo": entero("tope de correos por revisión", 1, 50, 10),
                        "procesar_existentes": booleano("en la primera revisión, correr también los que ya estaban (si no, solo los que lleguen después)", False),
                        "leer_adjuntos": booleano("leer los adjuntos aquí", True), "ocr": booleano("OCR al leerlos", True)}, ["credencial"]),
        "salidas": ["ok"], "produce": "correo.*, adjuntos.*, entrada (id, carpeta)", "x-credencial": True,
        "ejemplo": {"id": "llega", "tipo": "correo_nuevo", "nombre": "Llega un correo", "config": {"credencial": "buzon-soporte", "cada_minutos": 5, "filtro": {"asunto": "factura"}}},
    },
    # ------------------------------------------------------------------ lectura
    "leer_adjuntos": {
        "nombre": "Leer adjuntos", "categoria": "ocr",
        "descripcion": "Convierte los adjuntos a texto con el motor de PQRD: texto propio del PDF/Word/Excel, OCR con tesseract y visión del modelo en hojas flojas.",
        "config": conf({"ocr": booleano("aplicar OCR a escaneos e imágenes", True),
                        "vision": booleano("transcribir con el modelo las hojas que tesseract lee mal", True),
                        "tipos": lista(texto(), "solo estos tipos: pdf, imagen, docx, xlsx, texto, p7m o extensiones como «.pdf»"),
                        "nombre_patron": texto("regex: solo adjuntos cuyo nombre coincida (sin distinguir mayúsculas ni tildes)"),
                        "max_paginas": entero("páginas por PDF", 1, 60, 60), "max_caracteres": entero("caracteres por adjunto", 1, None, 60000),
                        "salida": variable("además de adjuntos.texto, deja el texto en esta variable")}),
        "salidas": ["ok", "sin_adjuntos"], "produce": "adjuntos.texto, adjuntos.leidos (+ salida)",
        "ejemplo": {"id": "adjuntos", "tipo": "leer_adjuntos", "nombre": "Leer PDF", "config": {"tipos": ["pdf", "imagen"], "ocr": True, "max_paginas": 10}},
    },
    # ------------------------------------------------------------- lógica y ruta
    "filtro": {
        "nombre": "Filtro", "categoria": "filtro",
        "descripcion": "Compara uno o varios datos con uno o varios valores y sale por «si» o «no». Por defecto no importan mayúsculas, tildes ni "
                       "espacios de más. Operadores de texto, números, fechas y longitud; varias condiciones con «todas» o «alguna».",
        "config": conf({**_props_condicion(_CAMPO_FILTRO), "condiciones": _CONDICIONES, "modo": _MODO}),
        "salidas": ["si", "no"],
        "ejemplo": {"id": "es_juzgado", "tipo": "filtro", "nombre": "¿Viene de un juzgado?", "config": {"campo": "remitente", "operador": "coincide", "valor": "@cendoj\\.ramajudicial\\.gov\\.co"}},
    },
    "condicion": {
        "nombre": "Condición", "categoria": "ruta",
        "descripcion": "Igual que filtro, pensado para variables ya obtenidas: mismos operadores (texto, números, fechas y longitud) y varias condiciones.",
        "config": conf({**_props_condicion("variable, ruta (datos.documento) o plantilla"),
                        "condiciones": lista(_condicion("variable, ruta (datos.documento) o plantilla"), "varias condiciones (en vez de campo, operador y valor)", 1),
                        "modo": _MODO}),
        "salidas": ["si", "no"],
        "ejemplo": {"id": "tiene_cedula", "tipo": "condicion", "nombre": "¿Hay cédula?", "config": {"campo": "documento", "operador": "no_vacio"}},
    },
    "ia_clasificar": {
        "nombre": "IA · clasificar", "categoria": "ia",
        "descripcion": "La IA elige una categoría con su confianza y evidencia. Sale por el valor de la categoría: arista por cada una o una por defecto.",
        "config": conf({"categorias": lista(objeto({"valor": texto("la categoría (también es el nombre de la salida)"), "descripcion": texto("qué la distingue")}, ["valor"]),
                                            "las categorías posibles", 1),
                        "variable": variable("dónde deja la categoría (o usa salida)"), "salida": variable("igual que variable"),
                        "instrucciones": plantilla("qué mirar para clasificar", largo=True)}, ["categorias"]),
        "salidas": ["<cada categoría>", "sin_categoria (opcional)"], "salidas_desde": "categorias[].valor", "produce": "variable/salida",
        "ejemplo": {"id": "tema", "tipo": "ia_clasificar", "nombre": "¿De qué trata?", "config": {
            "instrucciones": "Clasifica el correo según lo que pide a la ARL.", "variable": "tema",
            "categorias": [{"valor": "incapacidad", "descripcion": "pide pago o transcripción de una incapacidad"}, {"valor": "otro", "descripcion": "cualquier otra cosa"}]}},
    },
    "ia_decidir": {
        "nombre": "IA · decidir", "categoria": "ia",
        "descripcion": "La IA responde una pregunta eligiendo una opción según criterios explícitos y justifica. Sale por el valor elegido.",
        "config": conf({"pregunta": plantilla("la pregunta (o usa instrucciones)"), "instrucciones": plantilla("contexto adicional", largo=True),
                        "opciones": lista(objeto({"valor": texto("la opción (nombre de la salida)"), "criterio": texto("cuándo elegirla")}, ["valor", "criterio"]),
                                          "al menos dos", 2),
                        "salida": variable("deja el valor y <salida>_justificacion (por defecto: id del nodo)")}, ["opciones"]),
        "salidas": ["<cada opción>", "sin_decision (opcional)"], "salidas_desde": "opciones[].valor", "produce": "salida, salida_justificacion",
        "ejemplo": {"id": "urgencia", "tipo": "ia_decidir", "nombre": "¿Es urgente?", "config": {
            "pregunta": "¿La solicitud exige atención inmediata?", "entradas": ["correo", "datos"], "salida": "urgencia",
            "opciones": [{"valor": "urgente", "criterio": "hay riesgo para la vida, un plazo judicial o una hospitalización"},
                         {"valor": "normal", "criterio": "cualquier otro caso"}]}},
    },
    "ia_extraer": {
        "nombre": "IA · extraer datos", "categoria": "ia",
        "descripcion": "La IA extrae cada campo con confianza y evidencia; deja vacío lo que no está. Cada campo queda como variable.",
        "config": conf({"campos": lista(objeto({"nombre": variable("nombre de la variable"), "descripcion": texto("qué buscar"),
                                                "tipo": opcion(["texto", "numero", "fecha", "lista", "booleano"], "", "texto"),
                                                "obligatorio": booleano("", False), **_longitud()}, ["nombre"]),
                                        "los campos a extraer (con longitud: lo que no cumple se descarta con aviso y cuenta como faltante)", 1),
                        "instrucciones": plantilla("indicaciones extra", largo=True),
                        "salida": variable("además, deja todos los campos juntos como JSON")}, ["campos"]),
        "salidas": ["ok"], "produce": "<cada campo> (+ salida)",
        "ejemplo": {"id": "datos", "tipo": "ia_extraer", "nombre": "Datos del trabajador", "config": {
            "entradas": ["correo", "adjuntos.texto"], "salida": "datos",
            "campos": [{"nombre": "nombre", "descripcion": "nombre completo del trabajador", "obligatorio": True},
                       {"nombre": "documento", "descripcion": "número de cédula", "tipo": "numero", "obligatorio": True}]}},
    },
    "extraer_regex": {
        "nombre": "Extraer con regex", "categoria": "busqueda",
        "descripcion": "Extracción determinista con expresiones regulares (sin IA), sin distinguir mayúsculas ni tildes y devolviendo el texto original. "
                       "Con longitud o solo_digitos se salta los candidatos que no cumplen. Sale por «ok» o «incompleto» si falta un obligatorio.",
        "config": conf({"de": plantilla("dónde buscar (por defecto asunto + cuerpo + adjuntos)"),
                        "campos": lista(objeto({"nombre": variable(), "patron": texto("regex (sin distinguir mayúsculas ni tildes)"),
                                                "grupo": entero("grupo (1 si hay grupos, si no 0)", 0),
                                                "todos": booleano("lista con todas las coincidencias que cumplen", False), "obligatorio": booleano("", False),
                                                **_longitud(), "distinguir_mayusculas": booleano("que sí cuenten las mayúsculas"),
                                                "distinguir_tildes": booleano("que sí cuenten las tildes")},
                                               ["nombre", "patron"]), "campos o patron+salida", 1),
                        "patron": texto("atajo: un solo patrón (con salida)"), "salida": variable("con patron: dónde dejarlo")}),
        "salidas": ["ok", "incompleto"], "produce": "<cada campo>",
        "ejemplo": {"id": "radicado", "tipo": "extraer_regex", "nombre": "Radicado del juzgado", "config": {
            "de": "{{correo.asunto}} {{correo.cuerpo}}", "campos": [{"nombre": "radicado", "patron": "\\b(\\d{23})\\b", "obligatorio": True}]}},
    },
    "validar": {
        "nombre": "Validación", "categoria": "validacion",
        "descripcion": "Revisa reglas sobre variables. Sale por «ok» o «falla» (con los reparos).",
        "config": conf({"reglas": lista(objeto({"campo": texto("variable o ruta"), "regla": opcion(["obligatorio", "patron", "en_lista", "longitud"], "", "obligatorio"),
                                                "valor": libre("patron: regex completo; en_lista: lista; longitud: [min, max] o un entero (exacta)"),
                                                "contar": opcion(CONTAR, "longitud: qué se cuenta (por defecto caracteres)"),
                                                "comparacion": opcion(COMPARACIONES, "en_lista: flexible (por defecto), exacta o parecida"),
                                                "umbral": numero("en_lista parecida: similitud mínima de 0 a 1", 0, 1),
                                                "codigo": texto("código del reparo"), "mensaje": plantilla("explicación")}, ["campo"]), "las reglas", 1),
                        "salida": variable("deja la lista de reparos")}, ["reglas"]),
        "salidas": ["ok", "falla"], "produce": "_motivo (+ salida con los reparos)",
        "ejemplo": {"id": "revisar", "tipo": "validar", "nombre": "¿Están los datos?", "config": {"reglas": [
            {"campo": "nombre", "regla": "obligatorio", "codigo": "SIN_NOMBRE"},
            {"campo": "documento", "regla": "patron", "valor": "[0-9]{6,10}", "codigo": "DOCUMENTO_INVALIDO", "mensaje": "la cédula debe tener entre 6 y 10 dígitos"}]}},
    },
    "transformar": {
        "nombre": "Transformar", "categoria": "datos",
        "descripcion": "Asigna, combina y limpia variables sin IA. Una lista de operaciones que se aplican en orden; cada una deja su resultado en «salida».",
        "config": conf({"operaciones": lista(objeto({
            "op": opcion(["plantilla", "asignar", "mayusculas", "minusculas", "titulo", "limpiar", "solo_digitos", "sin_tildes", "regex_extraer",
                          "regex_reemplazar", "concatenar", "defecto", "recortar", "rellenar"]),
            "salida": variable(), "de": libre("variable o plantilla (concatenar: lista)"), "plantilla": plantilla(), "valor": libre("asignar / defecto"),
            "patron": texto("regex (sin distinguir mayúsculas ni tildes)"), "reemplazo": texto(), "separador": texto("", " "), "grupo": entero("", 0),
            "largo": entero("recortar / rellenar: a cuántos caracteres (o palabras)", 1),
            "lado": opcion(["izquierda", "derecha"], "rellenar: de qué lado (por defecto izquierda, como 0001234)"),
            "caracter": texto("rellenar: con qué carácter (por defecto 0)"), "sufijo": texto("recortar: lo que se añade si se cortó (p. ej. «…»)"),
            "contar": opcion(CONTAR, "recortar: caracteres o palabras; regex_extraer: qué cuenta en la longitud"),
            "longitud_min": entero("regex_extraer: longitud mínima del candidato", 0), "longitud_max": entero("regex_extraer: longitud máxima del candidato", 0),
            "solo_digitos": booleano("regex_extraer: quedarse solo con los dígitos"),
            "distinguir_mayusculas": booleano("regex: que sí cuenten las mayúsculas"), "distinguir_tildes": booleano("regex: que sí cuenten las tildes")},
            ["op", "salida"]),
            "op: plantilla {plantilla} | asignar {valor} | mayusculas|minusculas|titulo|limpiar|solo_digitos|sin_tildes {de} | "
            "regex_extraer {de, patron, grupo, defecto, longitud_min, longitud_max, solo_digitos} | regex_reemplazar {de, patron, reemplazo} | "
            "concatenar {de: [..], separador} | defecto {de, valor} | recortar {de, largo, contar, sufijo} | rellenar {de, largo, caracter, lado}", 1)}, ["operaciones"]),
        "salidas": ["ok"], "produce": "<salida de cada operación>",
        "ejemplo": {"id": "normalizar", "tipo": "transformar", "nombre": "Limpiar datos", "config": {"operaciones": [
            {"op": "solo_digitos", "de": "documento", "salida": "documento"},
            {"op": "titulo", "de": "nombre", "salida": "nombre"},
            {"op": "plantilla", "plantilla": "{{nombre}} (CC {{documento}})", "salida": "titular"}]}},
    },
    "buscar_en_tabla": {
        "nombre": "Buscar en tabla", "categoria": "datos",
        "descripcion": "Busca un valor en un CSV del repositorio o en una tabla embebida y trae columnas de la fila. Sale por «encontrado» o «no_encontrado».",
        "config": conf({"buscar": plantilla("variable o plantilla con el valor a buscar"), "tabla": lista(mapa(libre()), "filas embebidas (o archivo)"),
                        "archivo": texto("ruta .csv relativa al repositorio (o tabla)"), "columna": texto("columna donde buscar"),
                        "traer": lista(texto(), "columnas a traer (por defecto todas)"),
                        "modo": opcion(["igual", "contiene"], "igual: el valor entero; contiene: uno dentro del otro", "igual"),
                        **_opciones_busqueda(),
                        "salida": variable("la fila como JSON: {{salida.columna}} (por defecto: id del nodo)")}, ["buscar", "columna"]),
        "salidas": ["encontrado", "no_encontrado"], "produce": "salida",
        "ejemplo": {"id": "area", "tipo": "buscar_en_tabla", "nombre": "Área responsable", "config": {
            "buscar": "tema", "columna": "tema", "traer": ["area", "correo"], "salida": "area",
            "tabla": [{"tema": "incapacidad", "area": "Prestaciones económicas", "correo": "prestaciones@ejemplo.gov.co"},
                      {"tema": "afiliacion", "area": "Afiliaciones", "correo": "afiliaciones@ejemplo.gov.co"}]}},
    },
    "bifurcar_por_valor": {
        "nombre": "Bifurcar por valor", "categoria": "ruta",
        "descripcion": "Switch: sale por el caso que coincide con el valor de la variable (por defecto sin distinguir mayúsculas, tildes ni espacios; "
                       "con comparacion parecida, el caso más parecido) o por «otro».",
        "config": conf({"variable": plantilla("variable o plantilla"), "casos": lista(texto(), "los valores (cada uno es una salida)", 1),
                        **_opciones_busqueda()}, ["variable", "casos"]),
        "salidas": ["<cada caso>", "otro"], "salidas_desde": "casos[]",
        "ejemplo": {"id": "por_tema", "tipo": "bifurcar_por_valor", "nombre": "Según el tema", "config": {"variable": "tema", "casos": ["incapacidad", "afiliacion"]}},
    },
    "para_cada": {
        "nombre": "Para cada", "categoria": "logica",
        "descripcion": "Recorre una lista: por la arista «cada» corre el cuerpo una vez por elemento (con {{elemento}} e {{indice}}); el cuerpo termina en un nodo sin "
                       "aristas que salgan o en fin_ciclo y vuelve aquí. Al acabar sigue por «listo» con lo recogido en salida.",
        "config": conf({"lista": plantilla("la lista a recorrer (variable o ruta, p. ej. «filas» o «entrada.items»)"),
                        "variable": variable("cómo se llama cada elemento", "elemento"), "indice": variable("la posición (desde 0)", "indice"),
                        "recoger": plantilla("qué guardar de cada vuelta (por defecto: las variables que dejó el cuerpo, o el elemento)"),
                        "max_iteraciones": entero("tope de vueltas (tope duro 1000)", 1, 1000, 100),
                        "al_fallar": opcion(["detener", "continuar"], "si una vuelta falla: parar todo o anotarla y seguir", "detener"),
                        "concurrencia": entero("vueltas a la vez cuando el cuerpo solo calcula y pregunta a la IA (con entradas propias); "
                                               "1 = una tras otra. Si el cuerpo escribe, lee adjuntos o depende de la vuelta anterior, van siempre una tras otra", 1, 8, 4),
                        "salida": SALIDA()}, ["lista"]),
        "salidas": ["cada", "listo"], "produce": "variable, indice (en el cuerpo), salida (lista de resultados)",
        "ejemplo": {"id": "por_fila", "tipo": "para_cada", "nombre": "Por cada fila", "config": {"lista": "filas", "variable": "fila", "salida": "resultados", "max_iteraciones": 200}},
    },
    "fin_ciclo": {
        "nombre": "Fin del ciclo", "categoria": "logica",
        "descripcion": "Termina la vuelta actual de un para_cada (o la rama de un paralelo). Va dentro del cuerpo y no tiene aristas que salgan.",
        "config": conf({}), "salidas": [], "produce": "",
        "ejemplo": {"id": "siguiente", "tipo": "fin_ciclo", "nombre": "Siguiente", "config": {}},
    },
    "paralelo": {
        "nombre": "Ramas", "categoria": "logica",
        "descripcion": "Corre varias ramas: una arista por rama (cuando = nombre de la rama). Cada rama termina en un nodo unir "
                       "(el flujo sigue desde ese unir con lo de todas) o en un nodo sin salida (entonces sigue por «listo»). Si las ramas solo calculan y "
                       "preguntan a la IA (con entradas propias) y ninguna usa lo de otra, corren a la vez; si no, una tras otra, en su orden.",
        "config": conf({"ramas": lista(texto(), "los nombres de las ramas (cada uno es una salida)", 1),
                        "concurrencia": entero("ramas a la vez cuando se puede; 1 = una tras otra", 1, 8, 4)}, ["ramas"]),
        "salidas": ["<cada rama>", "listo (si ninguna rama llega a un unir)"], "salidas_desde": "ramas[]", "produce": "",
        "ejemplo": {"id": "consultas", "tipo": "paralelo", "nombre": "Consultar en dos sistemas", "config": {"ramas": ["afiliacion", "pagos"]}},
    },
    "unir": {
        "nombre": "Unir ramas", "categoria": "logica",
        "descripcion": "Donde se juntan las ramas de un paralelo: deja en salida {rama: variables que dejó esa rama} y el flujo sigue desde aquí.",
        "config": conf({"salida": SALIDA()}), "salidas": ["ok"], "produce": "salida",
        "ejemplo": {"id": "juntar", "tipo": "unir", "nombre": "Juntar", "config": {"salida": "consultas"}},
    },
    "esperar": {
        "nombre": "Esperar", "categoria": "logica",
        "descripcion": "Pausa la corrida unos segundos (máximo 60), p. ej. entre llamadas a una API.",
        "config": conf({"segundos": numero("cuánto esperar", 0, 60, 1)}, ["segundos"]), "salidas": ["ok"], "produce": "",
        "ejemplo": {"id": "pausa", "tipo": "esperar", "nombre": "Pausa", "config": {"segundos": 2}},
    },
    "expresion": {
        "nombre": "Expresión", "categoria": "datos",
        "descripcion": "Calcula un valor con una expresión segura sobre las variables: aritmética, comparaciones, and/or/not, «a if c else b», len, min, max, sum, "
                       "round, métodos de texto (lower, split, replace…). Sin comprensiones ni nada que empiece por _. Con como_condicion sale por «si» o «no».",
        "config": conf({"expresion": expresion("p. ej. «fila.monto * 1.19», «len(filas) > 0 and entrada.tipo == 'urgente'»"),
                        "como_condicion": booleano("salir por «si» o «no» según el resultado", False), "salida": SALIDA()}, ["expresion"]),
        "salidas": ["ok"], "produce": "salida",
        "ejemplo": {"id": "total", "tipo": "expresion", "nombre": "Total con IVA", "config": {"expresion": "round(fila.monto * 1.19, 2)", "salida": "total"}},
    },
    "json": {
        "nombre": "JSON", "categoria": "datos",
        "descripcion": "Convierte texto en JSON (parsear), JSON en texto (serializar) o saca un valor por su ruta (seleccionar).",
        "config": conf({"op": opcion(["parsear", "serializar", "seleccionar"], "qué hacer"), "de": plantilla("variable o plantilla"),
                        "ruta": texto("seleccionar: p. ej. «items.0.id» o «items[estado=activo].id»"), "indentar": booleano("serializar con sangría", False),
                        "defecto": libre("si la ruta no está"), "salida": SALIDA()}, ["op", "de"]),
        "salidas": ["ok"], "produce": "salida",
        "ejemplo": {"id": "leer_json", "tipo": "json", "nombre": "Leer JSON", "config": {"op": "parsear", "de": "entrada.cuerpo", "salida": "datos"}},
    },
    "fecha": {
        "nombre": "Fecha", "categoria": "datos",
        "descripcion": "Fechas y horas (zona America/Bogota por defecto): ahora, parsear, formatear, sumar o diferencia.",
        "config": conf({"op": opcion(["ahora", "parsear", "formatear", "sumar", "diferencia"], "qué hacer"),
                        "valor": plantilla("la fecha (ISO, AAAA-MM-DD, DD/MM/AAAA…); por defecto ahora"),
                        "formato": texto("strftime, p. ej. «%d/%m/%Y %H:%M» (por defecto ISO)"), "cantidad": libre("sumar: número o plantilla (negativo para restar)"),
                        "unidad": opcion(["minutos", "horas", "dias", "semanas"], "sumar y diferencia", "dias"),
                        "hasta": plantilla("diferencia: hasta esta fecha (por defecto ahora)"), "zona": texto("zona horaria", "America/Bogota"),
                        "salida": SALIDA()}, ["op"]),
        "salidas": ["ok"], "produce": "salida",
        "ejemplo": {"id": "vence", "tipo": "fecha", "nombre": "Vencimiento", "config": {"op": "sumar", "cantidad": 15, "unidad": "dias", "formato": "%Y-%m-%d", "salida": "vence"}},
    },
    "llamar_flujo": {
        "nombre": "Llamar flujo", "categoria": "logica",
        "descripcion": "Corre otro flujo declarativo (que empiece con entrada_manual) con una entrada y deja su resultado. Hasta 3 niveles y nunca a sí mismo.",
        "config": conf({"flujo": texto("id del flujo a llamar"), "entrada": mapa(plantilla(), "{campo: plantilla} que recibe el otro flujo"),
                        "salida": variable("{accion, motivo, solicitud, respuesta, variables} (por defecto: id del nodo)")}, ["flujo"]),
        "salidas": ["ok"], "produce": "salida",
        "ejemplo": {"id": "consultar", "tipo": "llamar_flujo", "nombre": "Consultar afiliado", "config": {"flujo": "consulta-afiliado", "entrada": {"documento": "{{fila.documento}}"}, "salida": "afiliado"}},
    },
    # -------------------------------------------------------------- datos y estado
    "tabla_guardar": {
        "nombre": "Guardar en tabla", "categoria": "datos",
        "descripcion": "Guarda una fila en la tabla integrada (estado propio de la plataforma, no escribe afuera). Por clave: reemplazar, combinar o insertar "
                       "(si ya existe sale por «existente» sin cambiarla).",
        "config": conf({"tabla": texto("nombre (minúsculas, números y _)"), "clave": plantilla("la clave de la fila (por defecto una nueva)"),
                        "datos": mapa(libre(), "{campo: plantilla o valor}"), "de": plantilla("o guarda esta variable (un objeto)"),
                        "modo": opcion(["reemplazar", "combinar", "insertar"], "", "reemplazar"),
                        "ambito": opcion(["flujo", "compartida"], "flujo: privada de este flujo; compartida: la ven todos", "flujo"),
                        "salida": SALIDA()}, ["tabla"]),
        "salidas": ["ok", "existente (opcional)"], "produce": "salida (la fila)",
        "ejemplo": {"id": "anotar", "tipo": "tabla_guardar", "nombre": "Anotar procesado", "config": {"tabla": "procesados", "clave": "{{fila.id}}", "datos": {"total": "{{total}}"}, "modo": "insertar"}},
    },
    "tabla_consultar": {
        "nombre": "Consultar tabla", "categoria": "datos",
        "descripcion": "Trae filas de la tabla integrada por clave, por campos iguales (filtro) o por condiciones con los operadores de filtro "
                       "(sin distinguir mayúsculas, tildes ni espacios, salvo que se pida). Sale por «encontrado» o «vacio».",
        "config": conf({"tabla": texto("nombre"), "clave": plantilla("una clave"),
                        "filtro": mapa(plantilla(), "{campo: valor} que tienen que ser iguales (también vale una lista de condiciones)"),
                        "condiciones": lista(_condicion("campo de la fila (estado, clave o datos.estado)"), "condiciones con operadores (contiene, mayor, antes_de…)", 1),
                        "modo": _MODO,
                        "limite": entero("tope de filas", 1, 500, 100), "orden": opcion(["-creada", "creada", "-actualizada", "actualizada", "clave", "-clave"], "", "-creada"),
                        "ambito": opcion(["flujo", "compartida"], "", "flujo"), "salida": SALIDA()}, ["tabla"]),
        "salidas": ["encontrado", "vacio"], "produce": "salida (lista de filas)",
        "ejemplo": {"id": "ya_visto", "tipo": "tabla_consultar", "nombre": "¿Ya procesado?", "config": {"tabla": "procesados", "clave": "{{fila.id}}", "salida": "previo"}},
    },
    "archivo_leer": {
        "nombre": "Leer archivo", "categoria": "datos",
        "descripcion": "Lee un archivo guardado en el flujo (súbelo en el panel del nodo) o, en los flujos de antes, una ruta del espacio de archivos: "
                       "texto (PDF y Word con la conversión del motor), JSON o filas (CSV/XLSX). Nunca fuera de esa carpeta ni lo privado de otro flujo.",
        "config": conf({"archivo": {**texto("el archivo subido a este flujo (su id)", None, "Archivo", "archivo-flujo")},
                        # la ruta escrita a mano solo la ven los flujos de antes que ya la tienen (x-legado)
                        "ruta": {**plantilla("solo flujos de antes: ruta relativa, p. ej. «entradas/lista.csv»"), "x-legado": True}, "formato": opcion(["auto", "texto", "json", "filas"], "", "auto"),
                        "hoja": texto("hoja de un XLSX"), "max_caracteres": entero("", 1, 500000, 60000), "max_filas": MAX_FILAS, "salida": SALIDA()}),
        "salidas": ["ok", "no_existe"], "produce": "salida ({texto} | {datos} | {filas})",
        "ejemplo": {"id": "lista", "tipo": "archivo_leer", "nombre": "Leer la lista", "config": {"ruta": "entradas/lista.csv", "salida": "lista"}},
    },
    "archivo_guardar": {
        "nombre": "Guardar archivo", "categoria": "datos",
        "descripcion": "Guarda texto, JSON o CSV en el espacio de archivos (/datos/archivos). Nunca fuera de esa carpeta.",
        "config": conf({"ruta": plantilla("ruta relativa, p. ej. «reportes/{{fecha_hoy}}.csv»"), "contenido": plantilla("el texto (o usa de)", largo=True),
                        "de": plantilla("variable a guardar (lista de filas para CSV, cualquier cosa para JSON)"),
                        "formato": opcion(["texto", "json", "csv"], "", "texto"), "modo": opcion(["crear", "reemplazar", "anexar"], "", "reemplazar"),
                        "salida": SALIDA()}, ["ruta"]),
        "salidas": ["ok"], "produce": "salida {ruta, bytes}",
        "ejemplo": {"id": "reporte", "tipo": "archivo_guardar", "nombre": "Guardar reporte", "config": {"ruta": "reportes/resultado.json", "de": "resultados", "formato": "json"}},
    },
    "hoja_leer": {
        "nombre": "Leer hoja de cálculo", "categoria": "conector",
        "descripcion": "Convierte un CSV o XLSX en filas: de un adjunto del correo, de un archivo de Drive, de una hoja de Google Sheets (origen sheets), "
                       "del espacio de archivos o de una URL de HTTP_HOSTS_PERMITIDOS.",
        "config": conf({"origen": opcion(["adjunto", "drive", "archivo", "url", "sheets"], "de dónde sale"),
                        "adjunto": texto("adjunto: regex del nombre (por defecto el primer .csv/.xlsx)"),
                        "credencial": credencial(["google_servicio"], "drive y sheets: la cuenta de servicio (la hoja tiene que estar compartida con ella)"),
                        "hoja_id": plantilla("sheets: el id de la hoja de Google o su enlace"), "rango": plantilla("sheets: pestaña o rango, p. ej. «Datos» o «Datos!A1:F200»"),
                        "archivo": plantilla("drive: id del archivo; archivo: ruta relativa"), "url": plantilla("url: https://… de un host permitido"),
                        "hoja": texto("hoja del XLSX (por defecto la primera)"), "encabezados": booleano("la primera fila son los nombres de columna", True),
                        "max_filas": MAX_FILAS, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["origen"]),
        "salidas": ["ok", "vacio"], "produce": "salida (lista de filas)",
        "ejemplo": {"id": "planilla", "tipo": "hoja_leer", "nombre": "Leer la planilla", "config": {"origen": "adjunto", "adjunto": "\\.xlsx$", "salida": "filas"}},
    },
    # ---------------------------------------------------------------- conectores
    "correo_leer": {
        "nombre": "Leer correos", "categoria": "conector",
        "descripcion": "Trae correos de un buzón (credencial imap o microsoft_graph), en solo lectura y sin marcarlos como leídos. Nunca los buzones de Positiva.",
        "config": conf({"credencial": credencial(["imap", "microsoft_graph"]), "carpeta": plantilla("carpeta", "INBOX"), "filtro": _FILTRO_CORREO,
                        "desde_fecha": plantilla("solo desde esta fecha (AAAA-MM-DD)"), "max": entero("cuántos (los más recientes)", 1, 100, 20),
                        "incluir_adjuntos": booleano("leer el texto de los adjuntos (con la conversión del motor)", False),
                        "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
        "salidas": ["ok", "vacio"], "produce": "salida (lista de {id, asunto, remitente, fecha, cuerpo, adjuntos})", "x-credencial": True,
        "ejemplo": {"id": "correos", "tipo": "correo_leer", "nombre": "Correos de facturas", "config": {"credencial": "buzon-facturas", "filtro": {"asunto": "factura"}, "max": 10, "salida": "correos"}},
    },
    "correo_enviar": {
        "nombre": "Enviar correo", "categoria": "conector",
        "descripcion": "Envía un correo por SMTP o Microsoft Graph, con adjuntos (documentos generados, archivos subidos o los adjuntos del correo original). "
                       "ESCRIBE AFUERA: solo sale si una persona habilitó la escritura de la credencial; si no, se arma con sus adjuntos y "
                       "queda como escritura bloqueada. Nunca a direcciones de Positiva.",
        "config": conf({"credencial": credencial(["smtp", "microsoft_graph"]), "para": plantilla("destinatarios separados por coma"),
                        "cc": plantilla(), "cco": plantilla(), "asunto": plantilla(), "cuerpo": plantilla("texto del correo", largo=True),
                        "html": plantilla("versión HTML opcional", largo=True), "adjuntos": ARCHIVOS(),
                        "reintentos": _REINTENTOS, "timeout_s": TIMEOUT, "salida": SALIDA()},
                       ["credencial", "para", "asunto", "cuerpo"]),
        "salidas": ["ok"], "produce": "salida {enviado, bloqueado, para, asunto, adjuntos}", "x-credencial": True,
        "ejemplo": {"id": "avisar", "tipo": "correo_enviar", "nombre": "Avisar por correo", "config": {
            "credencial": "smtp-notificaciones", "para": "{{entrada.correo}}", "asunto": "Resultado de su consulta", "cuerpo": "{{resumen}}"}},
    },
    "drive_listar": {
        "nombre": "Drive · listar", "categoria": "conector",
        "descripcion": "Lista archivos de Google Drive (cuenta de servicio): de una carpeta, por nombre o tipo.",
        "config": conf({"credencial": credencial(["google_servicio"]), "carpeta": plantilla("id de la carpeta"), "nombre": plantilla("el nombre contiene"),
                        "tipo_mime": texto("p. ej. application/pdf"), "max": entero("", 1, 200, 50), "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
        "salidas": ["ok", "vacio"], "produce": "salida (lista de {id, nombre, tipo, modificado, tamano, enlace})", "x-credencial": True,
        "ejemplo": {"id": "archivos", "tipo": "drive_listar", "nombre": "Archivos de la carpeta", "config": {"credencial": "drive-operaciones", "carpeta": "1AbCdEfGhIjKlMnOp", "salida": "archivos"}},
    },
    "drive_leer": {
        "nombre": "Drive · leer", "categoria": "conector",
        "descripcion": "Lee un archivo de Drive como texto: Docs y Hojas se exportan; PDF, Word e imágenes pasan por la conversión del motor (con OCR si se pide).",
        "config": conf({"credencial": credencial(["google_servicio"]), "archivo": plantilla("id del archivo"), "ocr": booleano("OCR en escaneos", True),
                        "max_caracteres": entero("", 1, 500000, 60000), "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "archivo"]),
        "salidas": ["ok"], "produce": "salida {id, nombre, tipo, texto}", "x-credencial": True,
        "ejemplo": {"id": "contrato", "tipo": "drive_leer", "nombre": "Leer contrato", "config": {"credencial": "drive-operaciones", "archivo": "{{archivo.id}}", "salida": "contrato"}},
    },
    "drive_subir": {
        "nombre": "Drive · subir", "categoria": "conector",
        "descripcion": "Sube a Drive un archivo de la corrida tal cual (un documento o una imagen generados, un archivo subido o un adjunto del correo) "
                       "o un texto, JSON o CSV. ESCRIBE AFUERA: solo sale si una persona habilitó la escritura de la credencial.",
        "config": conf({"credencial": credencial(["google_servicio"]), "carpeta": plantilla("id de la carpeta destino"),
                        "archivo": ARCHIVOS("el archivo a subir tal cual: «{{documento}}», «{{imagenes.0.id}}», «{{adjuntos}}» o un filtro (se sube el primero)"),
                        "nombre": plantilla("nombre del archivo (con archivo, por defecto el suyo)"),
                        "contenido": plantilla("o un texto (o usa de)", largo=True), "de": plantilla("variable a subir"),
                        "formato": opcion(["texto", "json", "csv"], "", "texto"), "reintentos": _REINTENTOS, "timeout_s": TIMEOUT, "salida": SALIDA()},
                       ["credencial"]),
        "salidas": ["ok"], "produce": "salida {id, nombre, bloqueado}", "x-credencial": True,
        "ejemplo": {"id": "subir", "tipo": "drive_subir", "nombre": "Subir reporte", "config": {"credencial": "drive-operaciones", "carpeta": "1AbCdEfGhIjKlMnOp", "nombre": "reporte.csv", "de": "resultados", "formato": "csv"}},
    },
    "sql_consulta": {
        "nombre": "SQL · consultar", "categoria": "conector",
        "descripcion": "Corre UNA consulta SELECT/WITH en PostgreSQL, MySQL o SQLite (en transacción de solo lectura) y deja las filas. Los valores van como "
                       "parámetros :nombre, nunca pegados en el texto.",
        "config": conf({"credencial": credencial(["postgres", "mysql", "sqlite"]), "consulta": largo("SELECT … WHERE documento = :documento"),
                        "parametros": mapa(plantilla(), "{nombre: plantilla} para cada :nombre"), "max_filas": MAX_FILAS, "timeout_s": TIMEOUT,
                        "salida": SALIDA()}, ["credencial", "consulta"]),
        "salidas": ["ok", "vacio"], "produce": "salida (lista de filas)", "x-credencial": True,
        "ejemplo": {"id": "pendientes", "tipo": "sql_consulta", "nombre": "Casos pendientes", "config": {
            "credencial": "base-local", "consulta": "SELECT id, documento, monto FROM casos WHERE estado = :estado ORDER BY id",
            "parametros": {"estado": "pendiente"}, "salida": "filas"}},
    },
    "sql_ejecutar": {
        "nombre": "SQL · ejecutar", "categoria": "conector",
        "descripcion": "Corre UNA sentencia INSERT/UPDATE/DELETE (UPDATE y DELETE con WHERE). ESCRIBE AFUERA: solo se aplica si una persona habilitó la escritura "
                       "de la credencial; si no, queda como escritura bloqueada.",
        "config": conf({"credencial": credencial(["postgres", "mysql", "sqlite"]), "sentencia": largo("UPDATE casos SET estado = :estado WHERE id = :id"),
                        "parametros": mapa(plantilla(), "{nombre: plantilla}"), "permitir_sin_where": booleano("UPDATE/DELETE sin WHERE (toda la tabla)", False),
                        "reintentos": _REINTENTOS, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "sentencia"]),
        "salidas": ["ok"], "produce": "salida {afectadas, filas, bloqueado}", "x-credencial": True,
        "ejemplo": {"id": "marcar", "tipo": "sql_ejecutar", "nombre": "Marcar atendido", "config": {
            "credencial": "base-local", "sentencia": "UPDATE casos SET estado = 'atendido' WHERE id = :id", "parametros": {"id": "{{fila.id}}"}}},
    },
    # --------------------------------------------------------------------- IA
    "ia_generar": {
        "nombre": "IA · generar", "categoria": "ia",
        "descripcion": "Genera texto libre o JSON (con esquema opcional) a partir de sus entradas y lo deja en su variable de salida.",
        "config": conf({"instrucciones": plantilla("qué generar", largo=True), "formato": opcion(["texto", "json"], "texto (json si hay esquema)"),
                        "esquema": mapa(libre(), "JSON schema {type, properties, required} o atajo {campo: descripcion}"),
                        "mensaje": plantilla("texto extra que se añade a lo que recibe el modelo"), "salida": SALIDA()}, ["instrucciones"]),
        "salidas": ["ok"], "produce": "salida",
        "ejemplo": {"id": "respuesta", "tipo": "ia_generar", "nombre": "Redactar respuesta", "config": {
            "instrucciones": "Redacta una respuesta formal y breve a {{correo.remitente_nombre}} confirmando que recibimos su solicitud con los datos dados. No inventes datos.",
            "entradas": ["correo.asunto", "datos"], "salida": "respuesta", "temperatura": 0.4}},
    },
    "resumir": {
        "nombre": "IA · resumir", "categoria": "ia",
        "descripcion": "Atajo de ia_generar: resume lo que recibe.",
        "config": conf({"longitud": texto("p. ej. «3 viñetas», «50 palabras»", "un párrafo breve"), "enfoque": texto("qué priorizar"),
                        "instrucciones": plantilla(largo=True), "salida": SALIDA("resumen")}),
        "salidas": ["ok"], "produce": "salida",
        "ejemplo": {"id": "resumen", "tipo": "resumir", "nombre": "Resumen del caso", "config": {"entradas": ["correo", "adjuntos.texto"], "longitud": "3 viñetas", "salida": "resumen"}},
    },
    "ia_redactar": {
        "nombre": "IA · redactar", "categoria": "ia",
        "descripcion": "La IA redacta un texto (forma antigua de ia_generar).",
        "config": conf({"instrucciones": plantilla(largo=True), "variable": variable("", "respuesta"), "salida": variable("igual que variable")}),
        "salidas": ["ok"], "produce": "variable/salida",
        "ejemplo": {"id": "redactar", "tipo": "ia_redactar", "nombre": "Respuesta al ciudadano", "config": {"instrucciones": "Redacta una respuesta breve y formal.", "variable": "respuesta"}},
    },
    # ------------------------------------------------------------- integración
    "notificar": {
        "nombre": "Notificar (simulado)", "categoria": "integracion",
        "descripcion": "Arma un aviso (destino, asunto, cuerpo) y lo anota como escritura bloqueada. NUNCA se envía (para enviar de verdad usa correo_enviar).",
        "config": conf({"para": plantilla("destino"), "cuerpo": plantilla(largo=True), "asunto": plantilla(), "cc": plantilla(),
                        "canal": texto("", "correo"), "salida": SALIDA()}, ["para", "cuerpo"]),
        "salidas": ["ok"], "produce": "salida (el mensaje armado)",
        "ejemplo": {"id": "avisar_area", "tipo": "notificar", "nombre": "Avisar al área", "config": {
            "para": "{{area.correo}}", "asunto": "Nuevo caso: {{correo.asunto}}", "cuerpo": "{{resumen}}"}},
    },
    "enviar": {
        "nombre": "Enviar respuesta (simulado)", "categoria": "integracion",
        "descripcion": "Arma la respuesta al remitente y la anota como escritura bloqueada. NUNCA se envía (para enviar de verdad usa correo_enviar).",
        "config": conf({"cuerpo": plantilla(largo=True), "para": plantilla("", "{{correo.remitente_correo}}"),
                        "asunto": plantilla("", "RE: {{correo.asunto}}"), "cc": plantilla(), "adjuntos": ARCHIVOS(), "salida": SALIDA()}, ["cuerpo"]),
        "salidas": ["ok"], "produce": "salida (el mensaje armado, con sus adjuntos)",
        "ejemplo": {"id": "responder", "tipo": "enviar", "nombre": "Responder al remitente", "config": {"cuerpo": "{{respuesta}}"}},
    },
    "http": {
        "nombre": "Petición HTTP", "categoria": "integracion",
        "descripcion": "Consulta una API. Un GET sale de verdad solo a hosts de HTTP_HOSTS_PERMITIDOS o de los hosts de su credencial (nunca a IP privadas, loopback, "
                       "metadatos, Positiva ni SGDEA, sin seguir redirecciones). POST/PUT/PATCH/DELETE ESCRIBEN: solo salen con una credencial guardada cuya escritura "
                       "habilitó una persona; si no, se arman y quedan como escritura bloqueada.",
        "config": conf({"metodo": opcion(["GET", "POST", "PUT", "PATCH", "DELETE"], "", "GET"), "url": plantilla("http(s)://…"),
                        "query": mapa(plantilla(), "{nombre: plantilla}"), "encabezados": mapa(plantilla(), "sin Authorization ni claves: usa credencial"),
                        "cuerpo": libre("plantilla u objeto JSON con plantillas (no se envía en GET)"),
                        "credencial": {**credencial(["http_bearer", "http_basico"], "una credencial guardada (http_bearer o http_basico) o el NOMBRE de una variable de entorno (HTTP_TOKEN_X)")},
                        "timeout_ms": entero("", 100, 60000, 15000),
                        "mapeo": lista(objeto({"variable": variable(), "ruta": texto("«items.0.id», «items[estado=activo].id»"), "defecto": libre(),
                                               "obligatorio": booleano("", False)}, ["variable", "ruta"]), "variables a sacar de la respuesta"),
                        "salida": variable("{status, ok, datos, encabezados, ms} o la petición simulada (por defecto: id del nodo)")}, ["url"]),
        "salidas": ["ok", "error_http"], "produce": "salida, <variable de cada mapeo>",
        "ejemplo": {"id": "consultar_afiliado", "tipo": "http", "nombre": "Consultar afiliado", "config": {
            "metodo": "GET", "url": "https://api.ejemplo.gov.co/afiliados", "query": {"documento": "{{documento}}"},
            "credencial": "HTTP_TOKEN_AFILIADOS", "timeout_ms": 10000, "salida": "afiliado",
            "reintentos": {"intentos": 3, "espera_ms": 500, "backoff": 2, "maximo_ms": 5000},
            "mapeo": [{"variable": "empresa", "ruta": "empresa.nombre", "defecto": ""}]}},
    },
    "respuesta_webhook": {
        "nombre": "Responder al webhook", "categoria": "integracion",
        "descripcion": "Arma lo que devuelve un flujo con disparador webhook y responder: true (estado, cuerpo y, si se dan, archivos: en «archivos» del "
                       "cuerpo JSON, como enlaces de descarga firmados que vencen o en base64). No escribe afuera.",
        "config": conf({"estado": entero("código HTTP", 100, 599, 200), "cuerpo": libre("plantilla u objeto JSON con plantillas"),
                        "tipo_contenido": opcion(["application/json", "text/plain"], "", "application/json"),
                        "archivos": ARCHIVOS("archivos que se devuelven: «{{documento}}», «{{imagenes}}»…"),
                        "archivos_como": opcion(["enlaces", "base64"], "enlaces de descarga con vencimiento o el contenido en base64", "enlaces")}),
        "salidas": ["ok"], "produce": "",
        "ejemplo": {"id": "contestar", "tipo": "respuesta_webhook", "nombre": "Contestar", "config": {"estado": 200, "cuerpo": {"ok": True, "total": "{{total}}"}}},
    },
    # ------------------------------------------------------- aprobación humana
    "aprobacion_humana": {
        "nombre": "Aprobación humana", "categoria": "logica",
        "descripcion": "Detiene el flujo hasta que una persona apruebe o rechace desde la plataforma (bandeja «Aprobaciones»), con comentario y, si se permite, "
                       "cambios en los datos. La corrida queda «en espera» (no ocupa un trabajador y sobrevive a reinicios); al decidir, una corrida nueva sigue "
                       "por «aprobado» o «rechazado» con {{aprobacion.decision}}, {{aprobacion.comentario}}, {{aprobacion.cambios}} y {{aprobacion.datos}} "
                       "(los datos con los cambios). Con tiempo_limite_h, si nadie decide: sale por «vencido» si tiene esa arista o aplica al_vencer. "
                       "Vera puede listarlas, pero solo una persona decide.",
        "config": conf({"titulo": plantilla("lo que se decide, p. ej. «Pago de {{entrada.monto}} a {{entrada.proveedor}}»"),
                        "detalle": plantilla("contexto para quien decide", largo=True),
                        "datos": mapa(libre(), "{campo: plantilla o valor}: lo que se muestra (y se puede editar)"),
                        "campos_editables": lista(texto(), "qué campos de datos puede cambiar quien decide"),
                        "tiempo_limite_h": numero("horas para decidir (si no, espera sin límite)", 0.01, 720),
                        "al_vencer": opcion(["rechazar", "aprobar", "error"], "si vence y no hay arista «vencido»: rechazar, aprobar o error", "rechazar"),
                        "asignado_a": plantilla("quién debería decidir (solo informativo)"),
                        "salida": variable("dónde queda la decisión (por defecto: aprobacion)")}, ["titulo"]),
        "salidas": ["aprobado", "rechazado", "vencido (opcional)"], "produce": "aprobacion {id, decision, estado, aprobado, comentario, cambios, datos, decidida, por}",
        "ejemplo": {"id": "aprobar_pago", "tipo": "aprobacion_humana", "nombre": "¿Se aprueba el pago?", "config": {
            "titulo": "Pago de {{entrada.monto}} a {{entrada.proveedor}}", "detalle": "Factura {{entrada.factura}}",
            "datos": {"monto": "{{entrada.monto}}", "proveedor": "{{entrada.proveedor}}"}, "campos_editables": ["monto"],
            "tiempo_limite_h": 24, "al_vencer": "rechazar"}},
    },
    # -------------------------------------------------------------- documentos
    "documento_generar": {
        "nombre": "Generar documento", "categoria": "documentos",
        "descripcion": "Genera un PDF o un Word desde una plantilla con {{variables}}: un texto (con «# » títulos, «- » viñetas y líneas con «|» como tablas), "
                       "un .docx subido al flujo con {{variables}} en su texto, o desde_ia: la IA redacta el documento con tus instrucciones (título, secciones "
                       "con párrafos, viñetas y tablas) y se arma en PDF o Word. Queda como archivo del flujo y resultado de la ejecución, y deja su ficha "
                       "{id, nombre, tipo, tamano} para adjuntarlo después ({{salida}} o {{salida.id}}).",
        "config": conf({"formato": opcion(["pdf", "docx"], "pdf o docx (Word)"),
                        "plantilla": plantilla("el texto del documento con {{variables}}", largo=True),
                        "plantilla_archivo": texto("o un .docx subido al flujo con {{variables}} en su texto", None, "Plantilla Word", "archivo-flujo"),
                        "desde_ia": DESDE_IA,
                        "nombre": plantilla("nombre del archivo (sin extensión), p. ej. «certificado-{{entrada.documento}}»"),
                        "titulo": plantilla("título del documento (metadatos)"), "salida": SALIDA()}, ["formato"]),
        "salidas": ["ok"], "produce": "salida {id, nombre, tipo, tamano, sha256, paginas}",
        "ejemplo": {"id": "certificado", "tipo": "documento_generar", "nombre": "Certificado", "config": {
            "formato": "pdf", "nombre": "certificado-{{entrada.documento}}",
            "plantilla": "# Certificado\n\nSe certifica que {{entrada.nombre}}, con documento {{entrada.documento}}, está afiliado.\n\n- Fecha: {{hoy}}", "salida": "certificado"}},
    },
    "pdf_unir": {
        "nombre": "Unir PDF", "categoria": "documentos",
        "descripcion": "Une varios PDF en uno, en el orden dado (archivos del flujo, los subidos a la corrida o los adjuntos del correo). Deja la ficha del PDF nuevo.",
        "config": conf({"archivos": libre("lista de ids o plantillas (p. ej. [\"{{carta.id}}\", \"{{anexo.id}}\"]), una variable con fichas, o «adjuntos»"),
                        "nombre": plantilla("nombre del PDF unido", "unido"), "salida": SALIDA()}, ["archivos"]),
        "salidas": ["ok"], "produce": "salida {id, nombre, paginas}",
        "ejemplo": {"id": "expediente", "tipo": "pdf_unir", "nombre": "Armar expediente", "config": {"archivos": ["{{carta.id}}", "{{anexo.id}}"], "nombre": "expediente", "salida": "expediente"}},
    },
    "pdf_dividir": {
        "nombre": "Dividir PDF", "categoria": "documentos",
        "descripcion": "Parte un PDF en uno por página o por rangos («1-3, 4, 5-fin»). Deja la lista de fichas de los PDF nuevos (con sus páginas).",
        "config": conf({"archivo": plantilla("el PDF: id, plantilla como «{{doc.id}}» o «adjuntos»"),
                        "modo": opcion(["cada_pagina", "rangos"], "", "cada_pagina"), "rangos": plantilla("con modo rangos: «1-3, 4, 5-fin» (páginas desde 1)"),
                        "nombre_base": plantilla("nombre de los archivos (se les agrega _1, _2…)"), "salida": SALIDA()}, ["archivo"]),
        "salidas": ["ok"], "produce": "salida (lista de {id, nombre, paginas})",
        "ejemplo": {"id": "separar", "tipo": "pdf_dividir", "nombre": "Separar anexos", "config": {"archivo": "{{expediente.id}}", "modo": "rangos", "rangos": "1-2, 3-fin", "salida": "partes"}},
    },
    "documento_convertir": {
        "nombre": "Convertir documento", "categoria": "documentos",
        "descripcion": "Convierte sin LibreOffice: Word → PDF, texto o markdown (conserva texto, títulos, viñetas y tablas, no el diseño exacto); PDF → texto, "
                       "markdown, Word (texto por página) o PNG (una imagen por página, hasta 20); texto o markdown → PDF o Word; Excel ↔ CSV, Excel o CSV → PDF; "
                       "imagen → PDF. Otras combinaciones no se pueden y lo dice.",
        "config": conf({"archivo": plantilla("el documento: id, plantilla como «{{doc.id}}» o «adjuntos»"),
                        "a": opcion(["pdf", "docx", "txt", "md", "csv", "xlsx", "png"], "a qué formato"),
                        "nombre": plantilla("nombre del archivo nuevo (sin extensión)"), "salida": SALIDA()}, ["archivo", "a"]),
        "salidas": ["ok"], "produce": "salida (ficha del archivo nuevo, o lista si son varias imágenes)",
        "ejemplo": {"id": "a_pdf", "tipo": "documento_convertir", "nombre": "Pasar a PDF", "config": {"archivo": "{{entrada.archivo.id}}", "a": "pdf", "salida": "pdf"}},
    },
    "documento_clasificar": {
        "nombre": "IA · tipo de documento", "categoria": "ia",
        "descripcion": "La IA dice qué tipo de documento es (entre las categorías dadas) con su confianza y la frase que lo muestra. Sale por la categoría "
                       "(o «sin_categoria»). Lee PDF por página, Word, Excel, texto e imágenes (con OCR).",
        "config": conf({"archivo": plantilla("el documento: id, plantilla como «{{doc.id}}» o «adjuntos»"), "texto": plantilla("o su texto", largo=True),
                        "categorias": lista(objeto({"valor": texto("el tipo (también es el nombre de la salida)"), "descripcion": texto("cómo se reconoce")}, ["valor"]),
                                            "los tipos posibles", 1),
                        "instrucciones": plantilla("qué mirar", largo=True), "max_caracteres": entero("cuánto texto ve la IA", 500, 200000, 15000),
                        "salida": variable("deja {tipo, confianza, evidencia, pagina, archivo} (por defecto: id del nodo)")}, ["categorias"]),
        "salidas": ["<cada categoría>", "sin_categoria (opcional)"], "salidas_desde": "categorias[].valor", "produce": "salida {tipo, confianza, evidencia, pagina, archivo}",
        "ejemplo": {"id": "tipo_doc", "tipo": "documento_clasificar", "nombre": "¿Qué documento es?", "config": {
            "archivo": "adjuntos", "salida": "tipo_doc",
            "categorias": [{"valor": "incapacidad", "descripcion": "certificado de incapacidad médica"}, {"valor": "factura", "descripcion": "factura o cuenta de cobro"},
                           {"valor": "otro", "descripcion": "cualquier otro documento"}]}},
    },
    "documento_comparar": {
        "nombre": "Comparar documentos", "categoria": "documentos",
        "descripcion": "Compara dos documentos (o textos) línea por línea: qué se agregó, quitó o cambió, la similitud y, con resumir, un resumen de la IA de las "
                       "diferencias que importan. Sale por «iguales» o «distintos».",
        "config": conf({"a": plantilla("la versión A: un archivo (id o «{{doc.id}}») o un texto"), "b": plantilla("la versión B"),
                        "resumir": booleano("pedir a la IA un resumen de las diferencias", True),
                        "ignorar_espacios": booleano("no contar cambios solo de espacios", True), "instrucciones": plantilla("qué mirar en el resumen", largo=True),
                        "salida": SALIDA()}, ["a", "b"]),
        "salidas": ["iguales", "distintos"], "produce": "salida {iguales, similitud, agregadas, quitadas, cambios, diff, resumen}",
        "ejemplo": {"id": "cambios", "tipo": "documento_comparar", "nombre": "¿Qué cambió en el contrato?", "config": {"a": "{{original.id}}", "b": "{{nuevo.id}}", "salida": "comparacion"}},
    },
    # -------------------------------------------------------- datos personales
    "anonimizar": {
        "nombre": "Anonimizar datos personales", "categoria": "validacion",
        "descripcion": "Encuentra y oculta datos personales de textos colombianos sin salir de la plataforma: cédulas (C.C., C.E., T.I., con puntos), NIT, nombres, "
                       "teléfonos (3xx, 60x, +57), correos, direcciones (Calle 45 # 12-30) y cuentas. Modos: enmascarar («*.***.***.789», «A. M. P.»), etiqueta "
                       "(«[CÉDULA]») o seudonimo («[NOMBRE_1]», el mismo para el mismo valor en toda la corrida). Úsalo antes de mandar un texto a una IA externa "
                       "o a un informe; con guardar_mapa y un segundo anonimizar con operacion revertir, la respuesta vuelve a tener los datos reales.",
        "config": conf({"de": plantilla("el texto o la variable (también objetos y listas)"), "operacion": opcion(["anonimizar", "revertir"], "", "anonimizar"),
                        "tipos": lista(opcion(["cedula", "nit", "nombre", "telefono", "correo", "direccion", "cuenta"]), "qué buscar (por defecto todo)"),
                        "modo": opcion(["enmascarar", "etiqueta", "seudonimo"], "", "seudonimo"),
                        "campos": lista(texto(), "en objetos: solo estos campos (por defecto todos los textos)"),
                        "nombres_extra": lista(plantilla(), "nombres que se sabe que aparecen (p. ej. «{{entrada.nombre}}»)"),
                        "guardar_mapa": booleano("guardar, solo en la corrida, cómo revertir los seudónimos", False),
                        "mapa_de": variable("revertir: la salida del anonimizar con guardar_mapa"),
                        "salida": variable("{texto, valor, conteo, total} (revertir: {texto, devueltos}) (por defecto: id del nodo)")}, ["de"]),
        "salidas": ["ok"], "produce": "salida {texto, valor, conteo, total}",
        "ejemplo": {"id": "sin_datos", "tipo": "anonimizar", "nombre": "Quitar datos personales", "config": {
            "de": "{{correo.cuerpo}}", "modo": "seudonimo", "guardar_mapa": True, "salida": "anonimo"}},
    },
    # --------------------------------------------------------- hojas de cálculo
    "hoja_escribir": {
        "nombre": "Escribir hoja de cálculo", "categoria": "conector",
        "descripcion": "Escribe filas en una hoja: un Excel o CSV del flujo (lo crea o lo actualiza en su lugar, sin escribir afuera) o una hoja de Google Sheets "
                       "(destino sheets: ESCRIBE AFUERA, solo si una persona habilitó la escritura de la credencial). Modos: anexar al final, actualizar por "
                       "una columna clave (y agregar las que falten) o reemplazar todo. Con desde_ia, la IA arma una hoja nueva (una o varias pestañas "
                       "con sus columnas y filas) a partir de tus instrucciones y queda como Excel o CSV del flujo.",
        "config": conf({"destino": opcion(["archivo", "sheets"], "archivo: un .xlsx/.csv del flujo; sheets: Google Sheets", "archivo"),
                        "desde_ia": DESDE_IA,
                        "archivo": texto("archivo: el id de una hoja del flujo (si no, se crea una con nombre)", None, "Hoja del flujo", "archivo-flujo"),
                        "nombre": plantilla("archivo nuevo: su nombre", "hoja"), "formato": opcion(["xlsx", "csv"], "archivo nuevo: xlsx o csv", "xlsx"),
                        "credencial": credencial(["google_servicio"], "sheets: la cuenta de servicio (la hoja tiene que estar compartida con ella)"),
                        "hoja_id": plantilla("sheets: el id de la hoja de Google o su enlace"), "hoja": plantilla("la pestaña (por defecto la primera)"),
                        "modo": opcion(["anexar", "actualizar", "reemplazar"], "", "anexar"), "clave": texto("actualizar: la columna que identifica cada fila"),
                        "insertar_si_falta": booleano("actualizar: agregar las filas cuya clave no está", True),
                        "filas": plantilla("una variable con la lista de filas (objetos)"), "fila": mapa(plantilla(), "o una sola fila {columna: plantilla}"),
                        "timeout_s": TIMEOUT, "salida": SALIDA()}),
        "salidas": ["ok"], "produce": "salida {id, nombre, agregadas, actualizadas, filas} (sheets: {hoja_id, bloqueado})", "x-credencial": True,
        "ejemplo": {"id": "registro", "tipo": "hoja_escribir", "nombre": "Anotar en el registro", "config": {
            "destino": "archivo", "nombre": "registro", "fila": {"fecha": "{{hoy}}", "documento": "{{entrada.documento}}", "estado": "recibido"}, "salida": "registro"}},
    },
    # ---------------------------------------------------------------- mensajería
    "mensaje_teams": {
        "nombre": "Mensaje a Teams", "categoria": "integracion",
        "descripcion": "Publica un mensaje (tarjeta con título, texto y datos) en un canal de Microsoft Teams por su webhook (credencial webhook_teams). ESCRIBE "
                       "AFUERA: solo sale si una persona habilitó la escritura de la credencial; si no, se arma y queda como escritura bloqueada.",
        "config": conf({"credencial": credencial(["webhook_teams"]), "titulo": plantilla("título"), "texto": plantilla("el mensaje", largo=True),
                        "hechos": mapa(plantilla(), "{etiqueta: plantilla}: datos en pares"), "enlace": plantilla("un enlace para abrir"),
                        "archivos": ARCHIVOS("archivos a compartir: van como botones «Descargar» con un enlace firmado que vence (el webhook de Teams no recibe archivos)"),
                        "reintentos": _REINTENTOS, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "texto"]),
        "salidas": ["ok"], "produce": "salida {enviado, bloqueado, status, destino}", "x-credencial": True,
        "ejemplo": {"id": "avisar_teams", "tipo": "mensaje_teams", "nombre": "Avisar en Teams", "config": {
            "credencial": "teams-operaciones", "titulo": "Nuevo caso", "texto": "{{resumen}}", "hechos": {"Radicado": "{{radicado}}"}}},
    },
    "mensaje_slack": {
        "nombre": "Mensaje a Slack", "categoria": "integracion",
        "descripcion": "Publica un mensaje en un canal de Slack por su webhook entrante (credencial webhook_slack). ESCRIBE AFUERA: solo sale si una persona "
                       "habilitó la escritura de la credencial.",
        "config": conf({"credencial": credencial(["webhook_slack"]), "titulo": plantilla("título (en negrita)"), "texto": plantilla("el mensaje", largo=True),
                        "hechos": mapa(plantilla(), "{etiqueta: plantilla}"), "enlace": plantilla("un enlace"),
                        "archivos": ARCHIVOS("archivos a compartir: van como enlaces firmados que vencen (el webhook de Slack no recibe archivos)"),
                        "reintentos": _REINTENTOS, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "texto"]),
        "salidas": ["ok"], "produce": "salida {enviado, bloqueado, status, destino}", "x-credencial": True,
        "ejemplo": {"id": "avisar_slack", "tipo": "mensaje_slack", "nombre": "Avisar en Slack", "config": {"credencial": "slack-alertas", "texto": "Llegó {{correo.asunto}}"}},
    },
    "webhook_saliente": {
        "nombre": "Webhook saliente", "categoria": "integracion",
        "descripcion": "Manda un POST con JSON a la URL de una credencial webhook_saliente (con su token, si tiene). ESCRIBE AFUERA: solo sale si una persona "
                       "habilitó la escritura de la credencial. Solo https a hosts públicos; nunca Positiva, SGDEA, IP privadas ni metadatos.",
        "config": conf({"credencial": credencial(["webhook_saliente"]), "cuerpo": libre("objeto JSON con plantillas (o un texto)"),
                        "encabezados": mapa(plantilla(), "encabezados extra (sin Authorization ni claves: el token va en la credencial)"),
                        "archivos": ARCHIVOS("archivos que se mandan con el cuerpo"),
                        "modo_archivos": opcion(["base64", "multipart", "enlace"], "base64: en «archivos» del JSON; multipart: form-data (el JSON en «datos» y "
                                                                                   "cada archivo en «archivo»); enlace: enlaces firmados que vencen", "base64"),
                        "reintentos": _REINTENTOS, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "cuerpo"]),
        "salidas": ["ok"], "produce": "salida {enviado, bloqueado, status, respuesta}", "x-credencial": True,
        "ejemplo": {"id": "notificar_sistema", "tipo": "webhook_saliente", "nombre": "Avisar al sistema", "config": {
            "credencial": "gancho-crm", "cuerpo": {"evento": "caso_nuevo", "radicado": "{{radicado}}", "resumen": "{{resumen}}"}}},
    },
    # ---------------------------------------------------- archivos que salen
    "responder_correo": {
        "nombre": "Responder correo", "categoria": "conector",
        "descripcion": "Responde en el MISMO hilo al correo que arrancó la ejecución (flujos con entrada_correo o correo_nuevo), por el buzón propio del flujo "
                       "(la credencial de su disparador) o la de este paso: con Microsoft Graph hace createReply sobre el original y lo envía con sus adjuntos; "
                       "por SMTP manda el correo con In-Reply-To y References del original. Lleva adjuntos (documentos generados, archivos subidos, los del "
                       "correo original). ESCRIBE AFUERA: solo sale si una persona habilitó la escritura de la credencial; si no (o si el flujo no tiene buzón), "
                       "se arma completa con sus adjuntos y queda retenida, visible en el detalle de la ejecución. Nunca a direcciones de Positiva.",
        "config": conf({"cuerpo": plantilla("el texto de la respuesta, p. ej. «{{respuesta}}»", largo=True),
                        "html": plantilla("versión HTML opcional", largo=True), "cc": plantilla("con copia a"),
                        "para": plantilla("a quién (por defecto: Reply-To o el remitente del original)"),
                        "asunto": plantilla("asunto (por defecto «RE: » y el del original)"),
                        "adjuntos": ARCHIVOS(), "incluir_original": booleano("citar el correo original debajo", True),
                        "credencial": credencial(["smtp", "microsoft_graph"], "por defecto la del buzón del flujo (su disparador); con un buzón IMAP pon una smtp"),
                        "reintentos": _REINTENTOS, "timeout_s": TIMEOUT, "salida": SALIDA("respuesta_correo")}, ["cuerpo"]),
        "salidas": ["ok"], "produce": "salida {para, asunto, adjuntos, en_respuesta_a, enviado, bloqueado, motivo}", "x-credencial": True,
        "ejemplo": {"id": "responder", "tipo": "responder_correo", "nombre": "Responder con el documento", "config": {
            "cuerpo": "{{respuesta}}", "adjuntos": ["{{documento}}", "{{adjuntos}}"], "incluir_original": True}},
    },
    "ia_imagen": {
        "nombre": "IA · generar imagen", "categoria": "ia",
        "descripcion": "La IA genera imágenes (PNG) a partir de instrucciones: por defecto Gemini con salida de imagen en Vertex (gemini-2.5-flash-image, "
                       "con la identidad del servicio); también Gemini con llave (ia_gemini) y OpenAI (gpt-image-1, ia_openai). Quedan como archivos del "
                       "flujo y resultados de la ejecución: se adjuntan con «{{imagenes}}» o se meten en un documento.",
        "config": conf({"instrucciones": plantilla("qué imagen generar, con {{variables}}", largo=True),
                        "aspecto": opcion(["1:1", "3:4", "4:3", "9:16", "16:9"], "proporción", "1:1"),
                        "tamano": opcion(["cuadrado", "vertical", "horizontal", "historia", "panoramica"], "o un tamaño con nombre (en vez de aspecto)"),
                        "cantidad": entero("cuántas imágenes", 1, 4, 1), "estilo": plantilla("estilo, p. ej. «ilustración plana, colores institucionales»"),
                        "negativo": plantilla("lo que NO debe salir"), "nombre": plantilla("nombre de los archivos (sin extensión)", "imagen"),
                        "proveedor": opcion(["vertex", "gemini", "openai"], "", "vertex"),
                        "modelo": texto("modelo de imágenes (por defecto gemini-2.5-flash-image; openai: gpt-image-1)"),
                        "credencial": credencial(["ia_gemini", "ia_openai"], "con gemini u openai: la conexión con su llave (vertex no la necesita)"),
                        "reintentos": _REINTENTOS, "salida": SALIDA("imagenes")}, ["instrucciones"]),
        "salidas": ["ok"], "produce": "salida (lista de {id, nombre, tipo, tamano, ancho, alto})",
        "ejemplo": {"id": "ilustrar", "tipo": "ia_imagen", "nombre": "Ilustración del aviso", "config": {
            "instrucciones": "Ilustración sencilla para un aviso sobre {{tema}}: personas en una oficina, sin texto", "aspecto": "16:9", "salida": "imagenes"}},
    },
    # ----------------------------------------------------------------------- IA
    "traducir": {
        "nombre": "IA · traducir", "categoria": "ia",
        "descripcion": "Traduce un texto a otro idioma conservando formato, números, nombres y marcadores como [NOMBRE_1]; textos largos van por partes.",
        "config": conf({"de": plantilla("el texto (o variable)", largo=True), "a": texto("idioma de destino", "inglés"), "desde": texto("idioma de origen (si no, lo detecta)"),
                        "tono": opcion(["formal", "neutro", "informal"], "", "neutro"), "glosario": mapa(plantilla(), "{término: traducción obligatoria}"),
                        "salida": SALIDA("traduccion")}, ["de"]),
        "salidas": ["ok"], "produce": "salida (texto)",
        "ejemplo": {"id": "al_ingles", "tipo": "traducir", "nombre": "Traducir al inglés", "config": {"de": "{{respuesta}}", "a": "inglés", "tono": "formal"}},
    },
    "resumir_largo": {
        "nombre": "IA · resumir documento largo", "categoria": "ia",
        "descripcion": "Resume textos que no caben de una vez: los parte (tam_parte caracteres), resume cada parte y une los resúmenes (mapa y reducción).",
        "config": conf({"de": plantilla("el texto largo (o variable), p. ej. «{{adjuntos.texto}}»"), "tam_parte": entero("caracteres por parte", 1000, 30000, 8000),
                        "max_partes": entero("tope de partes", 1, 50, 20), "longitud": texto("del resumen final", "un párrafo breve"),
                        "enfoque": texto("qué priorizar"), "instrucciones": plantilla(largo=True), "salida": SALIDA("resumen")}, ["de"]),
        "salidas": ["ok"], "produce": "salida (texto)",
        "ejemplo": {"id": "resumen_expediente", "tipo": "resumir_largo", "nombre": "Resumir el expediente", "config": {
            "de": "{{adjuntos.texto}}", "longitud": "5 viñetas", "enfoque": "plazos, montos y decisiones", "salida": "resumen"}},
    },
    "preguntar_documentos": {
        "nombre": "IA · preguntar a documentos", "categoria": "ia",
        "descripcion": "Responde una pregunta con lo que dicen los documentos, citando archivo y página (o parte) de cada dato. Busca los fragmentos que más se "
                       "relacionan con la pregunta y la IA responde solo con ellos. Sale por «respondida» o «sin_respuesta» si no está.",
        "config": conf({"pregunta": plantilla("la pregunta"), "archivos": libre("los documentos: ids, plantillas, una variable con fichas o «adjuntos»"),
                        "archivo": plantilla("o un solo documento"), "texto": plantilla("o un texto", largo=True),
                        "max_fragmentos": entero("cuántos fragmentos ve la IA", 1, 20, 6), "tam_fragmento": entero("caracteres por fragmento", 300, 6000, 1800),
                        "instrucciones": plantilla(largo=True), "salida": SALIDA("respuesta")}, ["pregunta"]),
        "salidas": ["respondida", "sin_respuesta"], "produce": "salida {respuesta, encontrada, citas: [{archivo, pagina, cita}]}",
        "ejemplo": {"id": "plazo", "tipo": "preguntar_documentos", "nombre": "¿Cuál es el plazo?", "config": {
            "pregunta": "¿Cuál es el plazo para responder y desde cuándo corre?", "archivos": "adjuntos", "salida": "plazo"}},
    },
    "extraer_tablas": {
        "nombre": "IA · extraer tablas", "categoria": "ia",
        "descripcion": "Saca las tablas de PDF (por página), imágenes (con visión, si el modelo la acepta; si no, con OCR) o un texto, como filas listas para "
                       "agrupar, validar o escribir en una hoja. Sale por «ok» o «vacio».",
        "config": conf({"archivos": libre("los documentos: ids, plantillas, una variable con fichas o «adjuntos»"), "archivo": plantilla("o un solo documento"),
                        "texto": plantilla("o un texto", largo=True), "columnas": lista(texto(), "las columnas que se esperan (si no, las del documento)"),
                        "instrucciones": plantilla(largo=True), "max_paginas": entero("páginas por PDF", 1, 50, 10), "salida": SALIDA("tablas")}),
        "salidas": ["ok", "vacio"], "produce": "salida {tablas: [{archivo, pagina, titulo, columnas, filas}], filas, cantidad}",
        "ejemplo": {"id": "items_factura", "tipo": "extraer_tablas", "nombre": "Ítems de la factura", "config": {
            "archivo": "adjuntos", "columnas": ["descripcion", "cantidad", "valor"], "salida": "items"}},
    },
    # -------------------------------------------------------------------- datos
    "deduplicar": {
        "nombre": "Deduplicar", "categoria": "datos",
        "descripcion": "Quita repetidos por campos o por huella (sin distinguir mayúsculas, tildes ni espacios). Con lista: deja {unicos, duplicados} y sale "
                       "por «ok» o «vacio»; con entre_corridas también descarta lo visto en corridas anteriores. Con elemento: dice si ya se había visto "
                       "(tabla integrada del flujo) y sale por «nuevo» o «duplicado».",
        "config": conf({"lista": plantilla("una variable con la lista"), "elemento": plantilla("o un solo elemento (objeto o texto)"),
                        "campos": lista(texto(), "los campos que definen un repetido (por defecto, el elemento entero)"),
                        "entre_corridas": booleano("recordar entre corridas (con elemento, por defecto sí)"),
                        "registrar": booleano("anotar lo nuevo para las próximas corridas", True), "tabla": texto("dónde se recuerdan las huellas", "huellas"),
                        "olvidar_dias": entero("después de cuántos días algo visto vuelve a contar como nuevo", 1, 3650), "salida": SALIDA()}),
        "salidas": ["ok", "vacio"], "produce": "salida {unicos, duplicados, total} o {huella, duplicado, visto}",
        "ejemplo": {"id": "sin_repetidos", "tipo": "deduplicar", "nombre": "¿Ya llegó este correo?", "config": {
            "elemento": "{{correo}}", "campos": ["asunto", "remitente_correo"], "olvidar_dias": 30, "salida": "visto"}},
    },
    "agrupar": {
        "nombre": "Agrupar y sumar", "categoria": "datos",
        "descripcion": "Agrupa una lista por uno o varios campos y calcula por grupo: contar, sumar, promedio, minimo, maximo, lista o unicos (entiende "
                       "«$ 1.234,56»). Deja una fila por grupo.",
        "config": conf({"lista": plantilla("una variable con la lista"), "por": lista(texto(), "los campos del grupo (vacío: un solo grupo con todo)"),
                        "calculos": lista(objeto({"op": opcion(["contar", "sumar", "promedio", "minimo", "maximo", "lista", "unicos"]),
                                                  "campo": texto("el campo (contar no lo necesita)"), "nombre": texto("cómo se llama el resultado")}, ["op"]),
                                          "por defecto contar"),
                        "orden": texto("ordenar por un cálculo o campo; «-total» de mayor a menor"), "salida": SALIDA()}, ["lista"]),
        "salidas": ["ok"], "produce": "salida (lista de grupos)",
        "ejemplo": {"id": "por_area", "tipo": "agrupar", "nombre": "Totales por área", "config": {
            "lista": "filas", "por": ["area"], "calculos": [{"op": "contar", "nombre": "casos"}, {"op": "sumar", "campo": "monto", "nombre": "total"}],
            "orden": "-total", "salida": "totales"}},
    },
    "ordenar_filtrar": {
        "nombre": "Ordenar y filtrar", "categoria": "datos",
        "descripcion": "Filtra una lista con las mismas condiciones y operadores de filtro (sobre los campos de cada elemento, sin distinguir mayúsculas, tildes ni "
                       "espacios), la ordena por uno o varios campos (texto, número o fecha) y la recorta. Sale por «ok» o «vacio».",
        "config": conf({"lista": plantilla("una variable con la lista"),
                        "condiciones": lista(_condicion("campo del elemento (p. ej. «estado» o «item.datos.monto»)"), "qué elementos quedan", 1), "modo": _MODO,
                        "orden": lista(objeto({"campo": texto("campo"), "direccion": opcion(["asc", "desc"], "", "asc"),
                                               "tipo": opcion(["auto", "texto", "numero", "fecha"], "", "auto")}, ["campo"]), "orden (el primero manda)"),
                        "limite": entero("cuántos dejar", 1, 5000), "desde": entero("saltar los primeros", 0, 100000),
                        "variable": variable("cómo se llama cada elemento en las plantillas de los valores", "item"), "salida": SALIDA()}, ["lista"]),
        "salidas": ["ok", "vacio"], "produce": "salida (la lista)",
        "ejemplo": {"id": "urgentes", "tipo": "ordenar_filtrar", "nombre": "Los 10 más altos pendientes", "config": {
            "lista": "filas", "condiciones": [{"campo": "estado", "operador": "igual", "valor": "pendiente"}],
            "orden": [{"campo": "monto", "direccion": "desc", "tipo": "numero"}], "limite": 10, "salida": "urgentes"}},
    },
    "mapear": {
        "nombre": "Mapear campos", "categoria": "datos",
        "descripcion": "Pasa cada elemento (o un objeto) de una forma a otra: cada campo destino sale de una ruta del elemento (origen), de una plantilla con "
                       "{{item.x}} o de un valor fijo, con defecto, tipo (texto, numero, entero, booleano, fecha, lista, objeto) y obligatorio. Los errores quedan "
                       "en <salida>_errores; con arista «con_errores» sigue por ahí, si no, falla.",
        "config": conf({"de": plantilla("la lista o el objeto"), "campos": lista(objeto({
            "destino": texto("nombre del campo nuevo (con puntos para anidar: «persona.nombre»)"), "origen": texto("ruta en el elemento, p. ej. «datos.cedula»"),
            "plantilla": plantilla("o una plantilla con {{item.x}}"), "valor": libre("o un valor fijo"), "defecto": libre("si queda vacío"),
            "tipo": opcion(["texto", "numero", "entero", "booleano", "fecha", "lista", "objeto"], "", "texto"),
            "formato": texto("fecha: strftime, p. ej. «%d/%m/%Y»"), "obligatorio": booleano("", False)}, ["destino"]), "los campos de la forma nueva", 1),
            "variable": variable("cómo se llama cada elemento en las plantillas", "item"), "conservar_otros": booleano("dejar también los campos que no se mapean", False),
            "salida": SALIDA()}, ["de", "campos"]),
        "salidas": ["ok", "con_errores (opcional)"], "produce": "salida, salida_errores",
        "ejemplo": {"id": "a_formato_crm", "tipo": "mapear", "nombre": "Al formato del CRM", "config": {"de": "filas", "salida": "contactos", "campos": [
            {"destino": "nombre_completo", "plantilla": "{{item.nombres}} {{item.apellidos}}"}, {"destino": "documento", "origen": "cedula", "tipo": "texto", "obligatorio": True},
            {"destino": "valor", "origen": "monto", "tipo": "numero", "defecto": 0}, {"destino": "fecha", "origen": "creado", "tipo": "fecha", "formato": "%Y-%m-%d"}]}},
    },
    "validar_contra": {
        "nombre": "Validar contra lista, tabla o SQL", "categoria": "validacion",
        "descripcion": "Revisa que un valor (o cada uno de una lista) exista en una lista fija, en la tabla integrada o en el resultado de una consulta SQL de solo "
                       "lectura (su primera columna). Compara sin mayúsculas, tildes ni espacios (o exacta o parecida) y sugiere el más parecido. Sale por "
                       "«valido» o «invalido».",
        "config": conf({"valor": plantilla("el valor o una variable con la lista de valores"), "contra": opcion(["lista", "tabla", "sql"], "contra qué"),
                        "valores": lista(texto(), "lista: los válidos"), "tabla": texto("tabla: su nombre"), "campo": texto("tabla: el campo (por defecto clave)"),
                        "ambito": opcion(["flujo", "compartida"], "", "flujo"), "credencial": credencial(["postgres", "mysql", "sqlite"], "sql: la base"),
                        "consulta": largo("sql: SELECT cuya primera columna son los valores válidos"), "parametros": mapa(plantilla(), "{nombre: plantilla}"),
                        **_opciones_busqueda(), "timeout_s": TIMEOUT, "salida": SALIDA()}, ["valor", "contra"]),
        "salidas": ["valido", "invalido"], "produce": "salida {validos, invalidos, todos_validos, sugerencias}",
        "ejemplo": {"id": "area_valida", "tipo": "validar_contra", "nombre": "¿Es un área que existe?", "config": {
            "valor": "{{area}}", "contra": "lista", "valores": ["Prestaciones económicas", "Afiliaciones", "Jurídica"]}},
    },
    # ------------------------------------------------------------------ control
    "subflujo": {
        "nombre": "Subflujo", "categoria": "logica",
        "descripcion": "Corre otro flujo (que empiece con entrada_manual) y usa su salida: {{<salida>.resultado}} (su solicitud), accion, motivo, respuesta y sus "
                       "variables; traer copia variables suyas a este flujo. Sale por el desenlace del otro (radicado, manual, descartado…) si tiene esa "
                       "arista, o por «ok». Como llamar_flujo, hasta 3 niveles y nunca a sí mismo.",
        "config": conf({"flujo": texto("id del flujo a llamar"), "entrada": mapa(plantilla(), "{campo: plantilla} que recibe el otro flujo"),
                        "traer": mapa(texto(), "{variable_local: ruta en las variables del otro flujo}"),
                        "salida": variable("{accion, motivo, resultado, respuesta, variables} (por defecto: id del nodo)")}, ["flujo"]),
        "salidas": ["ok", "<desenlace del otro flujo> (opcional)"], "produce": "salida, <cada variable de traer>",
        "ejemplo": {"id": "validar_afiliado", "tipo": "subflujo", "nombre": "Validar afiliado", "config": {
            "flujo": "consulta-afiliado", "entrada": {"documento": "{{documento}}"}, "traer": {"empresa": "afiliado.empresa"}, "salida": "consulta"}},
    },
    "reintentar_mas_tarde": {
        "nombre": "Reintentar más tarde", "categoria": "logica",
        "descripcion": "Termina la corrida como «aplazada» y la vuelve a correr desde el principio, con la misma entrada, en unos minutos o a una hora "
                       "(America/Bogota). {{aplazamiento.intento}} dice en qué intento va; al llegar a max_intentos sale por «agotado» en vez de aplazar.",
        "config": conf({"en_minutos": entero("en cuántos minutos", 1, 10080), "a_las": plantilla("o a qué hora «HH:MM» (la próxima vez que llegue) o fecha ISO"),
                        "motivo": plantilla("por qué se aplaza"), "max_intentos": entero("cuántas veces como máximo", 1, 20, 3)}),
        "salidas": ["agotado"], "produce": "",
        "ejemplo": {"id": "mas_tarde", "tipo": "reintentar_mas_tarde", "nombre": "Reintentar en 30 min", "config": {
            "en_minutos": 30, "motivo": "el servicio no respondió: {{fallo.motivo}}", "max_intentos": 3}},
    },
    "limite_tasa": {
        "nombre": "Límite de tasa", "categoria": "logica",
        "descripcion": "Deja pasar como máximo N corridas por segundo, minuto, hora o día (por flujo y por clave, también entre trabajadores). Al pasarse: esperar "
                       "(hasta 60 s; si no, aplaza), aplazar la corrida hasta que haya cupo, saltar por «excedido» o fallar.",
        "config": conf({"maximo": entero("cuántas veces en la ventana", 1, 100000), "por": opcion(["segundo", "minuto", "hora", "dia"], "", "minuto"),
                        "clave": plantilla("separa los contadores (p. ej. «{{entrada.cliente}}»)", "general"),
                        "al_exceder": opcion(["esperar", "aplazar", "saltar", "error"], "", "esperar"), "salida": variable("deja {clave, excedido}")}, ["maximo"]),
        "salidas": ["ok", "excedido (con al_exceder saltar)"], "produce": "salida (si se da)",
        "ejemplo": {"id": "cupo_api", "tipo": "limite_tasa", "nombre": "Máximo 30 por minuto", "config": {"maximo": 30, "por": "minuto", "al_exceder": "esperar"}},
    },
    "ventana_horario": {
        "nombre": "Ventana de horario", "categoria": "logica",
        "descripcion": "Sigue solo en días y horas hábiles (America/Bogota por defecto), sin festivos de Colombia (Ley Emiliani, calculados sin red). Fuera de "
                       "la ventana: aplazar la corrida hasta que abra (por defecto), esperar si abre en menos de 60 s o saltar por «fuera».",
        "config": conf({"dias": lista(opcion(["lun", "mar", "mie", "jue", "vie", "sab", "dom"]), "días permitidos (por defecto lun a vie)"),
                        "desde": texto("hora de apertura HH:MM", "08:00"), "hasta": texto("hora de cierre HH:MM", "17:00"),
                        "festivos": booleano("no contar los festivos de Colombia como hábiles", True), "zona": texto("zona horaria", "America/Bogota"),
                        "fuera": opcion(["aplazar", "esperar", "saltar"], "qué hacer fuera de la ventana", "aplazar"), "salida": variable("deja {dentro, ahora, festivo, abre}")}),
        "salidas": ["dentro", "fuera (con fuera saltar)"], "produce": "salida (si se da)",
        "ejemplo": {"id": "horario_habil", "tipo": "ventana_horario", "nombre": "Solo en horario hábil", "config": {"desde": "08:00", "hasta": "17:00", "fuera": "aplazar"}},
    },
    "detener_con_alerta": {
        "nombre": "Detener con alerta", "categoria": "salida",
        "descripcion": "Termina el flujo como «detenido» y deja una alerta en la plataforma (evento «alerta» para las notificaciones) con su nivel; con "
                       "credencial también la publica en Teams o Slack (si una persona habilitó la escritura). No tiene aristas que salgan.",
        "config": conf({"mensaje": plantilla("qué pasó y qué hacer", largo=True), "titulo": plantilla("título de la alerta"),
                        "nivel": opcion(["info", "aviso", "error"], "", "aviso"),
                        "credencial": credencial(["webhook_teams", "webhook_slack"], "además, publicarla en Teams o Slack")}, ["mensaje"]),
        "salidas": [], "produce": "",
        "ejemplo": {"id": "alertar", "tipo": "detener_con_alerta", "nombre": "Detener y avisar", "config": {
            "titulo": "No se pudo radicar", "mensaje": "{{fallo.motivo}}", "nivel": "error"}},
    },
    "salida": {
        "nombre": "Salida", "categoria": "salida",
        "descripcion": "Termina el flujo con un desenlace. Entrega la solicitud con los campos indicados, si se da, la respuesta armada con la plantilla y, "
                       "con archivos, los archivos que entrega (descargables en el historial, en el chat de Vera y en la respuesta del webhook). Todos los "
                       "archivos que la ejecución generó quedan además como sus resultados.",
        "config": conf({"desenlace": opcion(DESENLACES, "cómo termina"), "motivo": plantilla(), "destino": plantilla(),
                        "campos": libre("lista de variables o {nombre: plantilla} (por defecto todas las variables)"),
                        "plantilla_respuesta": plantilla("el texto final que entrega el flujo", largo=True),
                        "archivos": ARCHIVOS("los archivos que entrega el flujo: «{{documento}}», «{{imagenes}}», «{{adjuntos}}»…")}, ["desenlace"]),
        "salidas": [],
        "ejemplo": {"id": "radicar", "tipo": "salida", "nombre": "Radicar", "config": {
            "desenlace": "radicar", "campos": ["nombre", "documento"], "plantilla_respuesta": "{{respuesta}}"}},
    },
}

# Los que escriben afuera: solo escriben si su credencial tiene permitir_escritura (o nunca, si no tienen credencial).
ESCRITURA = {"correo_enviar", "drive_subir", "sql_ejecutar", "http", "enviar", "notificar", "hoja_escribir", "mensaje_teams", "mensaje_slack", "webhook_saliente",
             "responder_correo"}
# Los nodos de IA (proveedor, modelo y credencial de IA propios, con temperatura).
TIPOS_IA = {"ia_clasificar", "ia_decidir", "ia_extraer", "ia_generar", "resumir", "ia_redactar",
            "traducir", "resumir_largo", "preguntar_documentos", "extraer_tablas", "documento_clasificar", "documento_comparar"}
CON_REINTENTOS = TIPOS_IA | {"notificar", "enviar", "http", "correo_leer", "correo_enviar", "drive_listar", "drive_leer", "drive_subir",
                             "sql_consulta", "sql_ejecutar", "hoja_leer", "llamar_flujo", "hoja_escribir", "mensaje_teams", "mensaje_slack",
                             "webhook_saliente", "subflujo", "validar_contra", "responder_correo", "ia_imagen"}
NO_DESHABILITABLES = set(DISPARADORES) | {"salida", "fin_ciclo", "para_cada", "paralelo", "unir", "detener_con_alerta"}
TERMINALES = {"salida", "fin_ciclo", "detener_con_alerta"}
# Las salidas opcionales de cada tipo (además de «error»), para GET /api/nodos/tipos.
OPCIONALES = {"ia_clasificar": ["sin_categoria"], "documento_clasificar": ["sin_categoria"], "ia_decidir": ["sin_decision"], "paralelo": ["listo"],
              "tabla_guardar": ["existente"], "expresion": ["si", "no"], "aprobacion_humana": ["vencido"], "mapear": ["con_errores"],
              "subflujo": ["radicado", "manual", "reenviado", "descartado", "respondido", "duplicado", "detenido"],
              "limite_tasa": ["excedido"], "ventana_horario": ["fuera"], "deduplicar": ["nuevo", "duplicado"]}

# La ayuda de cada tipo: configs para copiar (ejemplos) y consejos cortos.
AYUDA: Dict[str, Dict[str, Any]] = {
    "entrada_correo": {
        "ejemplos": [{"leer_adjuntos": False}, {"leer_adjuntos": True, "credencial": "buzon-soporte", "carpeta": "INBOX"}],
        "consejos": ["Los correos salen de la bandeja propia del flujo: sube .eml o .msg de prueba, o ponle una credencial para leer su buzón (solo lectura).",
                     "Si vas a filtrar antes de leer los adjuntos, pon leer_adjuntos: false y un nodo leer_adjuntos después del filtro.",
                     "Es un disparador: cada flujo tiene exactamente uno."],
    },
    "entrada_archivos": {
        "ejemplos": [{"max_archivos": 5, "max_mb": 10}, {"tipos_permitidos": ["pdf", "jpg", "png"], "leer": False}],
        "consejos": ["Se corre con POST /api/flujos/<id>/correr {entrada: {archivos: [ids]}} después de subirlos a /api/flujos/<id>/archivos.",
                     "Con leer: false pon un nodo leer_adjuntos para elegir tipos o nombres (sin distinguir mayúsculas ni tildes).",
                     "Video y audio se rechazan siempre, por extensión y por su contenido."],
    },
    "entrada_manual": {
        "ejemplos": [{"campos": [{"nombre": "documento", "tipo": "texto", "obligatorio": True}]}, {"campos": []}],
        "consejos": ["Se corre con POST /api/flujos/<id>/correr {entrada: {...}}; los campos quedan en {{entrada.<campo>}}.",
                     "Es el disparador que necesita un flujo para que otro lo llame con llamar_flujo."],
    },
    "programado": {
        "ejemplos": [{"cron": "0 7 * * 1-5", "zona": "America/Bogota", "activo": True}, {"cada_minutos": 30, "activo": False}],
        "consejos": ["Usa cron o cada_minutos, no los dos.", "Nace apagado: enciéndelo con activo: true cuando el flujo esté probado.",
                     "Si la corrida anterior sigue en curso, la siguiente se salta."],
    },
    "webhook": {
        "ejemplos": [{}, {"responder": True, "esperar_ms": 8000}],
        "consejos": ["El secreto se genera al guardar y forma parte de la URL: quien lo tenga puede disparar el flujo.",
                     "Con responder: true pon un nodo respuesta_webhook antes de la salida."],
    },
    "correo_nuevo": {
        "ejemplos": [{"credencial": "buzon-soporte", "cada_minutos": 5}, {"credencial": "buzon-facturas", "filtro": {"asunto": "factura"}, "leer_adjuntos": False}],
        "consejos": ["Solo lee: no marca como leído ni mueve nada.", "La primera revisión solo memoriza lo que ya había (salvo procesar_existentes)."],
    },
    "leer_adjuntos": {
        "ejemplos": [{"tipos": ["pdf", "imagen"], "max_paginas": 10}, {"nombre_patron": "incapacidad", "ocr": False, "salida": "texto_incapacidad"}],
        "consejos": ["Sale por «sin_adjuntos» si no leyó ninguno: dale una arista a revisión manual.",
                     "Bajar max_paginas acelera mucho los PDF escaneados largos.",
                     "nombre_patron no distingue mayúsculas ni tildes: «peticion» lee «PETICIÓN.pdf»."],
    },
    "filtro": {
        "ejemplos": [{"campo": "asunto", "operador": "contiene", "valor": "incapacidad"},
                     {"condiciones": [{"campos": ["asunto", "cuerpo"], "operador": "contiene_alguna", "valores": ["tutela", "desacato"]},
                                      {"campo": "correo.remitente_correo", "operador": "termina_con", "valor": "ramajudicial.gov.co"}], "modo": "todas"}],
        "consejos": ["Por defecto no importan mayúsculas, tildes ni espacios: «peticion» encuentra «PETICIÓN» y «Petición  urgente»; "
                     "comparacion exacta lo distingue todo y parecida tolera errores de escritura.",
                     "«Agregar condición» suma condiciones (modo todas o alguna); campos busca en varios a la vez y basta uno.",
                     "Operadores: contiene, igual, empieza_con, palabra, contiene_alguna, parecido, coincide (regex sin tildes), mayor, entre, "
                     "antes_de, longitud_entre… campo «texto» mira asunto, cuerpo y adjuntos juntos."],
    },
    "condicion": {
        "ejemplos": [{"campo": "documento", "operador": "longitud_entre", "valor": 6, "hasta": 10, "contar": "digitos"},
                     {"condiciones": [{"campo": "monto", "operador": "mayor", "valor": "1.000.000"},
                                      {"campo": "fecha_radicado", "operador": "antes_de", "valor": "hoy"}], "modo": "alguna"}],
        "consejos": ["Mismos operadores que filtro: texto, números («1.234,56», «$ 1,234.56»), fechas (ISO, DD/MM/AAAA, «7 de noviembre de 2026», hoy) y longitud.",
                     "entre, entre_fechas y longitud_entre usan valor (mínimo) y hasta (máximo)."],
    },
    "ia_clasificar": {
        "ejemplos": [{"variable": "tema", "categorias": [{"valor": "incapacidad", "descripcion": "pide pago o transcripción"}, {"valor": "otro", "descripcion": "lo demás"}]},
                     {"variable": "tono", "entradas": ["correo.cuerpo"], "categorias": ["queja", "consulta"], "reintentos": {"intentos": 3}}],
        "consejos": ["Incluye siempre una categoría «otro» para lo que no encaja.", "Describe cada categoría con lo que la distingue, no con su nombre repetido.",
                     "No uses «error» como categoría: es la rama de fallo."],
    },
    "ia_decidir": {
        "ejemplos": [{"pregunta": "¿Hay que responder hoy?", "salida": "prioridad", "opciones": [
            {"valor": "hoy", "criterio": "hay un plazo judicial o riesgo para la vida"}, {"valor": "normal", "criterio": "lo demás"}]}],
        "consejos": ["Los criterios se aplican en orden: pon el más específico primero.", "La justificación queda en <salida>_justificacion para el motivo o la respuesta."],
    },
    "ia_extraer": {
        "ejemplos": [{"entradas": ["correo", "adjuntos.texto"], "salida": "datos", "campos": [
            {"nombre": "nombre", "descripcion": "nombre completo", "obligatorio": True}, {"nombre": "documento", "descripcion": "cédula", "tipo": "numero"}]}],
        "consejos": ["Lo que no está queda vacío (no se inventa): valídalo después con un nodo validar.",
                     "Con entradas limitas lo que ve la IA: menos texto, menos costo y menos errores.",
                     "longitud_min, longitud_max, longitud, contar y solo_digitos por campo: lo que no cumple se descarta con aviso y cuenta como faltante."],
    },
    "extraer_regex": {
        "ejemplos": [{"de": "{{correo.asunto}} {{correo.cuerpo}}", "campos": [{"nombre": "radicado", "patron": "\\b(\\d{23})\\b", "obligatorio": True}]},
                     {"de": "{{correo.cuerpo}} {{adjuntos.texto}}", "campos": [
                         {"nombre": "cedula", "patron": "\\d[\\d.]{5,14}", "solo_digitos": True, "longitud_min": 6, "longitud_max": 10},
                         {"nombre": "cie10", "patron": "\\b[A-Z]\\d{2}(?:\\.\\d)?\\b", "todos": True}]}],
        "consejos": ["Es gratis y determinista: úsalo antes que la IA para números con formato fijo.",
                     "No distingue mayúsculas ni tildes («peticion» encuentra «PETICIÓN») y devuelve el texto original; todos: true deja una lista sin repetidos.",
                     "Con longitud_min/longitud_max (y contar, solo_digitos) salta los números que no cumplen: elige la cédula, el NIT o el radicado correcto."],
    },
    "validar": {
        "ejemplos": [{"reglas": [{"campo": "documento", "regla": "patron", "valor": "[0-9]{6,10}", "codigo": "DOCUMENTO_INVALIDO"}]},
                     {"reglas": [{"campo": "tema", "regla": "en_lista", "valor": ["incapacidad", "afiliacion"]}, {"campo": "nombre", "regla": "longitud", "valor": [3, 120]},
                                 {"campo": "telefono", "regla": "longitud", "valor": 10, "contar": "digitos", "codigo": "TELEFONO_INVALIDO"}]}],
        "consejos": ["El código del primer reparo queda en el motivo de la salida.", "patron exige que todo el texto coincida, no un pedazo.",
                     "en_lista no distingue mayúsculas, tildes ni espacios (comparacion exacta o parecida lo cambia); longitud acepta [min, max] o un entero y contar."],
    },
    "transformar": {
        "ejemplos": [{"operaciones": [{"op": "solo_digitos", "de": "documento", "salida": "documento"}, {"op": "titulo", "de": "nombre", "salida": "nombre"}]},
                     {"operaciones": [{"op": "regex_extraer", "de": "correo.cuerpo", "patron": "nit\\D{0,5}([\\d.\\-]{9,15})", "solo_digitos": True,
                                       "longitud_min": 9, "longitud_max": 10, "salida": "nit"},
                                      {"op": "rellenar", "de": "nit", "largo": 10, "salida": "nit_10"}]}],
        "consejos": ["Las operaciones corren en orden: una puede usar la salida de la anterior.", "defecto rellena una variable vacía con un valor fijo o plantilla.",
                     "Las regex no distinguen mayúsculas ni tildes; recortar corta a largo caracteres o palabras y rellenar completa (0001234)."],
    },
    "buscar_en_tabla": {
        "ejemplos": [{"buscar": "tema", "columna": "tema", "traer": ["area", "correo"], "salida": "area",
                      "tabla": [{"tema": "incapacidad", "area": "Prestaciones", "correo": "prestaciones@ejemplo.gov.co"}]},
                     {"buscar": "{{correo.asunto}}", "modo": "contiene", "columna": "palabra", "salida": "ruta",
                      "tabla": [{"palabra": "tutela", "area": "Jurídica"}, {"palabra": "incapacidad", "area": "Prestaciones"}]}],
        "consejos": ["La fila queda como JSON: úsala con {{salida.columna}}.", "modo contiene sirve cuando el valor viene dentro de un texto más largo.",
                     "Por defecto no importan mayúsculas, tildes ni espacios; comparacion parecida trae la fila más parecida (umbral) y exacta, la idéntica."],
    },
    "bifurcar_por_valor": {
        "ejemplos": [{"variable": "tema", "casos": ["incapacidad", "afiliacion"]},
                     {"variable": "entrada.tipo", "casos": ["peticion", "queja", "reclamo"], "comparacion": "parecida", "umbral": 0.8}],
        "consejos": ["Lo que no coincide sale por «otro»: dale siempre una arista.", "No uses «error» como caso: es la rama de fallo.",
                     "Por defecto «PETICIÓN» cae en el caso «peticion»; con comparacion parecida también «peticon»."],
    },
    "para_cada": {
        "ejemplos": [{"lista": "filas", "variable": "fila", "salida": "resultados"}, {"lista": "correos", "variable": "c", "recoger": "{{c.asunto}}", "al_fallar": "continuar"}],
        "consejos": ["El cuerpo es lo que cuelga de la arista «cada»; termina en un nodo sin salida o en fin_ciclo.",
                     "Lo que sigue después va por «listo» y ve los resultados en salida.", "Si el cuerpo llega a una salida, termina todo el flujo."],
    },
    "fin_ciclo": {
        "ejemplos": [{}],
        "consejos": ["Úsalo para terminar una vuelta desde una rama (p. ej. la rama «no» de una condición)."],
    },
    "paralelo": {
        "ejemplos": [{"ramas": ["afiliacion", "pagos"]}],
        "consejos": ["Las ramas corren en orden, una tras otra.", "Haz que todas terminen en el mismo nodo unir para seguir con lo de todas."],
    },
    "unir": {
        "ejemplos": [{"salida": "consultas"}],
        "consejos": ["Lo de cada rama queda en {{salida.<rama>.<variable>}}."],
    },
    "esperar": {
        "ejemplos": [{"segundos": 2}],
        "consejos": ["Máximo 60 segundos; para esperar más usa un flujo programado."],
    },
    "expresion": {
        "ejemplos": [{"expresion": "round(fila.monto * 1.19, 2)", "salida": "total"}, {"expresion": "len(filas) > 0", "como_condicion": True}],
        "consejos": ["Los campos se leen con punto (entrada.monto) o corchetes (fila['monto']).", "numero(x) convierte textos como «1.234,5»."],
    },
    "json": {
        "ejemplos": [{"op": "parsear", "de": "entrada.cuerpo", "salida": "datos"}, {"op": "seleccionar", "de": "respuesta", "ruta": "items.0.id", "salida": "id"}],
        "consejos": ["serializar sirve para guardar o enviar un objeto como texto."],
    },
    "fecha": {
        "ejemplos": [{"op": "ahora", "formato": "%Y-%m-%d", "salida": "hoy"}, {"op": "diferencia", "valor": "{{fila.fecha}}", "unidad": "dias", "salida": "dias"}],
        "consejos": ["Todo se calcula en America/Bogota salvo que digas otra zona.", "diferencia da hasta − valor en la unidad pedida."],
    },
    "llamar_flujo": {
        "ejemplos": [{"flujo": "consulta-afiliado", "entrada": {"documento": "{{documento}}"}, "salida": "afiliado"}],
        "consejos": ["El flujo llamado tiene que empezar con entrada_manual.", "Sus escrituras siguen las mismas reglas que las de este flujo."],
    },
    "tabla_guardar": {
        "ejemplos": [{"tabla": "procesados", "clave": "{{fila.id}}", "datos": {"total": "{{total}}"}, "modo": "insertar"},
                     {"tabla": "contadores", "clave": "envios", "de": "conteo", "modo": "combinar", "ambito": "compartida"}],
        "consejos": ["modo insertar + arista «existente» sirve para no procesar dos veces lo mismo.", "No escribe afuera: es estado propio de la plataforma."],
    },
    "tabla_consultar": {
        "ejemplos": [{"tabla": "procesados", "clave": "{{fila.id}}", "salida": "previo"},
                     {"tabla": "procesados", "filtro": {"estado": "pendiente"}, "limite": 50,
                      "condiciones": [{"campo": "monto", "operador": "mayor", "valor": 1000000}, {"campo": "creado", "operador": "despues_de", "valor": "2026-01-01"}]}],
        "consejos": ["Sale por «vacio» si no hay filas: úsalo como «¿ya lo vi?».",
                     "filtro {campo: valor} compara sin mayúsculas, tildes ni espacios; condiciones usa los operadores de filtro (modo todas o alguna)."],
    },
    "archivo_leer": {
        "ejemplos": [{"ruta": "entradas/lista.csv", "salida": "lista"}, {"ruta": "contratos/{{entrada.nombre}}.pdf", "formato": "texto", "salida": "contrato"}],
        "consejos": ["Sube el archivo en el panel del nodo: queda guardado en el flujo (config.archivo); no escribas rutas a mano.",
                     "Las rutas de antes siguen valiendo, relativas a /datos/archivos; «..» se rechaza.", "Sale por «no_existe» si el archivo no está."],
    },
    "archivo_guardar": {
        "ejemplos": [{"ruta": "reportes/resultado.json", "de": "resultados", "formato": "json"}, {"ruta": "logs/envios.csv", "de": "filas", "formato": "csv", "modo": "anexar"}],
        "consejos": ["Solo escribe en /datos/archivos.", "modo anexar en CSV no repite los encabezados."],
    },
    "hoja_leer": {
        "ejemplos": [{"origen": "adjunto", "adjunto": "\\.xlsx$", "salida": "filas"},
                     {"origen": "sheets", "credencial": "google-ops", "hoja_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "rango": "Casos", "salida": "filas"}],
        "consejos": ["Con encabezados: false las columnas se llaman columna_1, columna_2…",
                     "origen sheets lee una hoja de Google Sheets compartida con la cuenta de servicio; origen drive exporta una hoja de Drive como CSV.",
                     "Para escribir en una hoja usa hoja_escribir."],
    },
    "correo_leer": {
        "ejemplos": [{"credencial": "buzon-facturas", "filtro": {"asunto": "factura"}, "max": 10, "salida": "correos"},
                     {"credencial": "buzon-soporte", "desde_fecha": "2026-11-01", "incluir_adjuntos": True}],
        "consejos": ["Combínalo con para_cada para procesar cada correo.", "Nunca lee los buzones de Positiva: esos se leen por el buzón de la plataforma."],
    },
    "correo_enviar": {
        "ejemplos": [{"credencial": "smtp-notificaciones", "para": "{{entrada.correo}}", "asunto": "Resultado", "cuerpo": "{{resumen}}"},
                     {"credencial": "smtp-notificaciones", "para": "{{entrada.correo}}", "asunto": "Su certificado", "cuerpo": "Adjuntamos su certificado.",
                      "adjuntos": ["{{certificado}}", {"de": "adjuntos", "tipo": "pdf"}]}],
        "consejos": ["Mientras la credencial no tenga la escritura habilitada por una persona, el correo se arma (con la lista de adjuntos y sus tamaños) y queda bloqueado.",
                     "adjuntos: «{{documento}}» (lo que generó un paso), «{{archivos}}» (lo subido), «{{adjuntos}}» o {de: \"adjuntos\", nombre: \"factura\", tipo: \"pdf\"}; "
                     "hasta 10 archivos y 20 MB (por Graph, 3 MB cada uno).",
                     "Para contestar el correo que arrancó la ejecución en su mismo hilo usa responder_correo.",
                     "Nunca se envía a direcciones de Positiva."],
    },
    "drive_listar": {
        "ejemplos": [{"credencial": "drive-operaciones", "carpeta": "1AbCdEfGhIjKlMnOp", "salida": "archivos"}, {"credencial": "drive-operaciones", "nombre": "factura", "tipo_mime": "application/pdf"}],
        "consejos": ["La cuenta de servicio solo ve lo que le compartieron."],
    },
    "drive_leer": {
        "ejemplos": [{"credencial": "drive-operaciones", "archivo": "{{archivo.id}}", "salida": "contrato"}],
        "consejos": ["Dentro de un para_cada sobre drive_listar lee cada archivo."],
    },
    "drive_subir": {
        "ejemplos": [{"credencial": "drive-operaciones", "carpeta": "1AbCdEfGhIjKlMnOp", "nombre": "reporte.csv", "de": "resultados", "formato": "csv"},
                     {"credencial": "drive-operaciones", "carpeta": "1AbCdEfGhIjKlMnOp", "archivo": "{{documento}}"}],
        "consejos": ["Escribe afuera: queda bloqueado mientras una persona no habilite la escritura de la credencial.",
                     "Con archivo sube tal cual un PDF, Word, Excel o imagen generados en la ejecución (o un adjunto del correo)."],
    },
    "sql_consulta": {
        "ejemplos": [{"credencial": "base-local", "consulta": "SELECT id, documento FROM casos WHERE estado = :estado", "parametros": {"estado": "pendiente"}, "salida": "filas"},
                     {"credencial": "base-local", "consulta": "SELECT COUNT(*) AS n FROM casos", "salida": "conteo"}],
        "consejos": ["Nunca pegues valores con {{…}} en la consulta: usa :nombre y parametros.", "Una sola sentencia y solo lectura; para cambiar datos usa sql_ejecutar."],
    },
    "sql_ejecutar": {
        "ejemplos": [{"credencial": "base-local", "sentencia": "UPDATE casos SET estado = 'atendido' WHERE id = :id", "parametros": {"id": "{{fila.id}}"}}],
        "consejos": ["Escribe afuera: queda bloqueado mientras una persona no habilite la escritura de la credencial.", "UPDATE y DELETE necesitan WHERE."],
    },
    "ia_generar": {
        "ejemplos": [{"instrucciones": "Redacta una respuesta formal y breve. No inventes datos.", "entradas": ["correo.asunto", "datos"], "salida": "respuesta", "temperatura": 0.4},
                     {"instrucciones": "Arma la ficha del caso.", "esquema": {"resumen": "dos frases", "prioridad": "alta|media|baja"}, "salida": "ficha",
                      "reintentos": {"intentos": 3, "espera_ms": 1000}}],
        "consejos": ["Con esquema devuelve JSON y puedes usar {{ficha.campo}} después.", "Ponle reintentos si el modelo suele dar 429 en horas pico."],
    },
    "resumir": {
        "ejemplos": [{"entradas": ["correo", "adjuntos.texto"], "longitud": "3 viñetas", "salida": "resumen"}],
        "consejos": ["enfoque dice qué priorizar (p. ej. «los plazos y montos»)."],
    },
    "ia_redactar": {
        "ejemplos": [{"instrucciones": "Redacta una respuesta breve y formal.", "variable": "respuesta"}],
        "consejos": ["Para flujos nuevos usa ia_generar, que admite JSON y esquema."],
    },
    "notificar": {
        "ejemplos": [{"para": "{{area.correo}}", "asunto": "Nuevo caso: {{correo.asunto}}", "cuerpo": "{{resumen}}"},
                     {"para": "soporte@ejemplo.gov.co", "asunto": "Falló {{fallo.nodo}}", "cuerpo": "{{fallo.motivo}}"}],
        "consejos": ["Nunca se envía: queda como escritura bloqueada para revisar.", "Detrás de una arista «error» sirve para avisar del fallo con {{fallo.motivo}}."],
    },
    "enviar": {
        "ejemplos": [{"cuerpo": "{{respuesta}}\n\nAtentamente,\nPositiva"}, {"cuerpo": "{{respuesta}}", "adjuntos": ["{{documento}}"]}],
        "consejos": ["Por defecto va al remitente con «RE: asunto».", "Nunca se envía: queda como escritura bloqueada, con la lista de sus adjuntos.",
                     "Para responder de verdad (en el mismo hilo y con adjuntos) usa responder_correo."],
    },
    "http": {
        "ejemplos": [{"metodo": "GET", "url": "https://api.ejemplo.gov.co/afiliados/{{documento}}", "timeout_ms": 8000, "salida": "afiliado",
                      "mapeo": [{"variable": "estado", "ruta": "estado", "defecto": "desconocido"}, {"variable": "id_activo", "ruta": "polizas[estado=activa].id"}]},
                     {"metodo": "POST", "url": "https://api.ejemplo.gov.co/casos", "credencial": "api-casos",
                      "cuerpo": {"documento": "{{documento}}", "resumen": "{{resumen}}"}}],
        "consejos": ["POST/PUT/PATCH/DELETE solo salen con una credencial guardada con la escritura habilitada por una persona.",
                     "Una credencial guardada solo se manda a los hosts que dice la credencial.",
                     "Dale una arista a «error_http» (status ≥ 400) y otra a «error» (tiempo agotado, destino bloqueado)."],
    },
    "respuesta_webhook": {
        "ejemplos": [{"estado": 200, "cuerpo": {"ok": True, "total": "{{total}}"}}, {"estado": 422, "cuerpo": "faltan datos: {{fallo.motivo}}", "tipo_contenido": "text/plain"},
                     {"estado": 200, "cuerpo": {"ok": True}, "archivos": ["{{documento}}"], "archivos_como": "enlaces"}],
        "consejos": ["Solo tiene efecto si el disparador es webhook con responder: true.",
                     "Con archivos, el cuerpo JSON trae «archivos»: enlaces de descarga firmados que vencen (o el contenido en base64 con archivos_como)."],
    },
    "aprobacion_humana": {
        "ejemplos": [{"titulo": "¿Se envía la respuesta a {{correo.remitente_nombre}}?", "detalle": "{{respuesta}}", "datos": {"respuesta": "{{respuesta}}"},
                      "campos_editables": ["respuesta"], "tiempo_limite_h": 8, "al_vencer": "rechazar"},
                     {"titulo": "Pago de {{entrada.monto}}", "datos": {"monto": "{{entrada.monto}}"}, "asignado_a": "tesorería"}],
        "consejos": ["Dale aristas «aprobado» y «rechazado»; con tiempo_limite_h, una «vencido» (o al_vencer decide).",
                     "Después usa {{aprobacion.datos.campo}}: trae los cambios que hizo la persona; {{aprobacion.comentario}} trae su comentario.",
                     "No va dentro de un para_cada ni de un flujo llamado. Vera puede listar las pendientes, pero solo una persona decide en la plataforma."],
    },
    "documento_generar": {
        "ejemplos": [{"formato": "docx", "nombre": "respuesta-{{radicado}}", "plantilla": "# Respuesta a su solicitud\n\n{{respuesta}}\n\n| Campo | Valor |\n| Radicado | {{radicado}} |"},
                     {"formato": "pdf", "plantilla_archivo": "a-plantilla01", "nombre": "carta-{{entrada.documento}}", "salida": "carta"},
                     {"formato": "docx", "nombre": "respuesta-{{datos.radicado}}", "salida": "documento", "desde_ia": {
                         "instrucciones": "Redacta la respuesta formal a la solicitud: saludo, resumen de lo pedido, la decisión y los próximos pasos. "
                                          "Incluye una tabla con los datos del caso.", "entradas": ["correo", "datos"]}}],
        "consejos": ["En la plantilla de texto: «# » título, «## » subtítulo, «- » viñeta, líneas con «|» tabla y una línea en blanco separa párrafos.",
                     "Con plantilla_archivo, cada párrafo del .docx que tenga {{…}} se llena (queda con el formato de su primer tramo).",
                     "Con desde_ia la IA redacta el documento (título, secciones, párrafos, viñetas y tablas) y no inventa datos: dale entradas con lo que sabe.",
                     "El archivo queda en el flujo y como resultado de la ejecución: adjúntalo con «{{salida}}» (correo, respuesta, Drive, Teams) o pásalo a pdf_unir."],
    },
    "pdf_unir": {
        "ejemplos": [{"archivos": ["{{carta.id}}", "{{anexo.id}}"], "nombre": "expediente-{{radicado}}"}, {"archivos": "adjuntos", "nombre": "adjuntos-unidos"}],
        "consejos": ["Solo PDF: convierte antes los Word o las imágenes con documento_convertir.", "«adjuntos» toma los del correo (sin los incrustados)."],
    },
    "pdf_dividir": {
        "ejemplos": [{"archivo": "{{expediente.id}}"}, {"archivo": "adjuntos", "modo": "rangos", "rangos": "1, 2-fin", "nombre_base": "parte"}],
        "consejos": ["cada_pagina da un PDF por página; rangos «1-3, 4, 5-fin» da uno por rango.", "Combínalo con para_cada para tratar cada parte."],
    },
    "documento_convertir": {
        "ejemplos": [{"archivo": "{{entrada.archivo.id}}", "a": "pdf"}, {"archivo": "{{informe.id}}", "a": "png", "salida": "paginas"}],
        "consejos": ["Sin LibreOffice: de Word a PDF se conserva el texto, los títulos, las viñetas y las tablas, no el diseño exacto.",
                     "Combinaciones: docx→pdf/txt/md, pdf→txt/md/docx/png, txt/md→pdf/docx, xlsx↔csv, xlsx/csv→pdf, imagen→pdf."],
    },
    "documento_clasificar": {
        "ejemplos": [{"archivo": "adjuntos", "categorias": [{"valor": "tutela", "descripcion": "acción de tutela o auto de un juzgado"},
                                                           {"valor": "otro", "descripcion": "cualquier otro documento"}]},
                     {"texto": "{{archivos.texto}}", "categorias": [{"valor": "cedula", "descripcion": "documento de identidad"}, {"valor": "rut", "descripcion": "RUT de la DIAN"}],
                      "salida": "tipo_doc"}],
        "consejos": ["Sale por la categoría elegida: una arista por cada una o una por defecto.", "{{salida.evidencia}} trae la frase y {{salida.pagina}} la página."],
    },
    "documento_comparar": {
        "ejemplos": [{"a": "{{original.id}}", "b": "{{nuevo.id}}"}, {"a": "{{version_anterior}}", "b": "{{respuesta}}", "resumir": False}],
        "consejos": ["a y b pueden ser archivos del flujo o textos.", "{{salida.resumen}} es el resumen de la IA; {{salida.cambios}} la lista de cambios."],
    },
    "anonimizar": {
        "ejemplos": [{"de": "{{adjuntos.texto}}", "modo": "seudonimo", "guardar_mapa": True, "salida": "anonimo"},
                     {"de": "filas", "modo": "enmascarar", "campos": ["cedula", "telefono"], "tipos": ["cedula", "telefono"], "salida": "filas_seguras"}],
        "consejos": ["Antes de una IA externa: anonimiza, pásale {{anonimo.texto}} y luego revierte su respuesta con otro anonimizar (operacion revertir, mapa_de: anonimo).",
                     "El mapa para revertir solo vive en la corrida: nunca sale en eventos, resultados ni muestras.",
                     "Para nombres que no siguen a «señor», «paciente» o «nombre:», dalos en nombres_extra."],
    },
    "hoja_escribir": {
        "ejemplos": [{"destino": "archivo", "archivo": "a-registro01", "modo": "actualizar", "clave": "documento", "filas": "resultados"},
                     {"destino": "sheets", "credencial": "google-ops", "hoja_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "hoja": "Casos", "fila": {"radicado": "{{radicado}}", "estado": "{{estado}}"}},
                     {"nombre": "resumen-de-facturas", "formato": "xlsx", "salida": "hoja", "desde_ia": {
                         "instrucciones": "Arma una hoja «Facturas» con número, fecha, proveedor y valor de cada factura, y otra «Totales» por proveedor.",
                         "entradas": ["adjuntos.texto"]}}],
        "consejos": ["En un archivo del flujo no escribe afuera: sin archivo crea uno nuevo (nombre y formato) y con archivo lo actualiza en su lugar.",
                     "Con desde_ia la IA arma una hoja nueva con una o varias pestañas; en CSV solo va la primera.",
                     "Google Sheets escribe afuera: queda bloqueado mientras una persona no habilite la escritura de la credencial.",
                     "Las columnas nuevas se agregan al encabezado; actualizar busca por clave y agrega las que faltan (insertar_si_falta)."],
    },
    "mensaje_teams": {
        "ejemplos": [{"credencial": "teams-operaciones", "titulo": "Caso {{radicado}}", "texto": "{{resumen}}", "enlace": "{{enlace}}"},
                     {"credencial": "teams-operaciones", "titulo": "Informe listo", "texto": "{{resumen}}", "archivos": ["{{informe}}"]}],
        "consejos": ["La URL del webhook la guarda una persona en Conexiones (tipo webhook_teams) y es secreta.",
                     "archivos van como botones «Descargar» con enlaces firmados que vencen en 72 horas.",
                     "Escribe afuera: queda bloqueado mientras una persona no habilite la escritura de la credencial."],
    },
    "mensaje_slack": {
        "ejemplos": [{"credencial": "slack-alertas", "titulo": "Falló {{fallo.nodo}}", "texto": "{{fallo.motivo}}"},
                     {"credencial": "slack-alertas", "texto": "Ya está la respuesta de {{correo.asunto}}", "archivos": ["{{documento}}"]}],
        "consejos": ["La URL del webhook entrante (hooks.slack.com) la guarda una persona en Conexiones (tipo webhook_slack).",
                     "Escribe afuera: queda bloqueado mientras una persona no habilite la escritura de la credencial."],
    },
    "webhook_saliente": {
        "ejemplos": [{"credencial": "gancho-crm", "cuerpo": {"radicado": "{{radicado}}", "estado": "{{estado}}"}},
                     {"credencial": "gancho-erp", "cuerpo": {"factura": "{{factura}}"}, "encabezados": {"X-Origen": "positiva-ia"}, "reintentos": {"intentos": 3}},
                     {"credencial": "gancho-crm", "cuerpo": {"radicado": "{{radicado}}"}, "archivos": ["{{documento}}"], "modo_archivos": "multipart"}],
        "consejos": ["La URL y el token viven en la credencial webhook_saliente: nunca en el flujo.",
                     "Con archivos: base64 los mete en «archivos» del JSON, multipart manda form-data y enlace manda enlaces firmados que vencen.",
                     "Solo https a hosts públicos; Positiva, SGDEA, IP privadas y metadatos se bloquean siempre."],
    },
    "traducir": {
        "ejemplos": [{"de": "{{respuesta}}", "a": "inglés", "tono": "formal"}, {"de": "{{correo.cuerpo}}", "a": "español", "glosario": {"claim": "reclamación"}}],
        "consejos": ["Los seudónimos de anonimizar ([NOMBRE_1]) se conservan: puedes traducir sin mandar datos personales."],
    },
    "resumir_largo": {
        "ejemplos": [{"de": "{{adjuntos.texto}}", "longitud": "5 viñetas"}, {"de": "documento.texto", "tam_parte": 12000, "enfoque": "obligaciones y fechas"}],
        "consejos": ["Para textos cortos basta resumir; este parte el texto y une los resúmenes.", "Sube tam_parte si el modelo admite mucho contexto (menos llamadas)."],
    },
    "preguntar_documentos": {
        "ejemplos": [{"pregunta": "¿Qué pide el accionante?", "archivos": "adjuntos"},
                     {"pregunta": "{{entrada.pregunta}}", "archivos": ["{{contrato.id}}", "{{otrosi.id}}"], "max_fragmentos": 8}],
        "consejos": ["Sale por «sin_respuesta» si los documentos no lo dicen: la IA no inventa.", "{{salida.citas}} trae archivo, página y frase de cada dato."],
    },
    "extraer_tablas": {
        "ejemplos": [{"archivo": "adjuntos", "columnas": ["fecha", "concepto", "valor"]}, {"texto": "{{documento.texto}}", "salida": "tablas"}],
        "consejos": ["{{salida.filas}} junta las filas de todas las tablas: pásalas a agrupar, validar_contra u hoja_escribir.",
                     "En imágenes usa visión si el modelo la acepta; si no, el texto del OCR."],
    },
    "deduplicar": {
        "ejemplos": [{"lista": "filas", "campos": ["documento"]}, {"elemento": "{{correo}}", "campos": ["asunto", "remitente_correo"], "olvidar_dias": 30}],
        "consejos": ["Con elemento sale por «nuevo» o «duplicado» y recuerda entre corridas (tabla integrada del flujo).",
                     "Sin campos compara el elemento entero (huella); con campos, solo esos, sin mayúsculas, tildes ni espacios."],
    },
    "agrupar": {
        "ejemplos": [{"lista": "filas", "por": ["area"], "calculos": [{"op": "contar", "nombre": "casos"}]},
                     {"lista": "items.filas", "por": [], "calculos": [{"op": "sumar", "campo": "valor", "nombre": "total"}, {"op": "promedio", "campo": "valor", "nombre": "promedio"}]}],
        "consejos": ["sumar y promedio entienden «$ 1.234,56» y «1,234.56».", "orden «-total» deja primero el grupo con más."],
    },
    "ordenar_filtrar": {
        "ejemplos": [{"lista": "filas", "orden": [{"campo": "fecha", "direccion": "desc", "tipo": "fecha"}], "limite": 5},
                     {"lista": "casos", "condiciones": [{"campo": "monto", "operador": "mayor", "valor": 1000000}, {"campo": "ciudad", "operador": "en_lista", "valores": ["Bogotá", "Medellín"]}]}],
        "consejos": ["Las condiciones miran los campos de cada elemento, con los operadores de filtro.", "Lo que no tiene el campo de orden queda al final."],
    },
    "mapear": {
        "ejemplos": [{"de": "filas", "campos": [{"destino": "nombre", "origen": "[\"NOMBRE COMPLETO\"]"}, {"destino": "edad", "origen": "edad", "tipo": "entero", "defecto": 0}]},
                     {"de": "datos", "campos": [{"destino": "persona.documento", "plantilla": "{{item.tipo_doc}} {{item.numero}}"}, {"destino": "origen", "valor": "positiva-ia"}]}],
        "consejos": ["origen es una ruta dentro del elemento; plantilla usa {{item.x}} y cualquier variable del flujo.",
                     "Dale una arista «con_errores» para seguir aunque algún campo obligatorio falte."],
    },
    "validar_contra": {
        "ejemplos": [{"valor": "{{ciudad}}", "contra": "lista", "valores": ["Bogotá", "Medellín", "Cali"]},
                     {"valor": "documentos", "contra": "sql", "credencial": "base-local", "consulta": "SELECT documento FROM afiliados WHERE estado = :estado", "parametros": {"estado": "activo"}}],
        "consejos": ["Con una lista de valores sale por «valido» solo si todos existen; {{salida.invalidos}} dice cuáles no.",
                     "contra tabla busca en la tabla integrada (por clave o por un campo); contra sql corre un SELECT de solo lectura."],
    },
    "subflujo": {
        "ejemplos": [{"flujo": "consulta-afiliado", "entrada": {"documento": "{{documento}}"}, "salida": "consulta"},
                     {"flujo": "calcular-liquidacion", "entrada": {"dias": "{{dias}}"}, "traer": {"valor": "total"}}],
        "consejos": ["{{salida.resultado}} es la solicitud que entregó el otro flujo; traer copia sus variables aquí.",
                     "Para seguir según cómo terminó el otro flujo, ponle aristas con su desenlace (radicado, manual…)."],
    },
    "reintentar_mas_tarde": {
        "ejemplos": [{"en_minutos": 30, "motivo": "{{fallo.motivo}}"}, {"a_las": "07:00", "motivo": "fuera del horario del proveedor", "max_intentos": 5}],
        "consejos": ["Detrás de una arista «error» sirve para reintentar un servicio caído sin perder la corrida.",
                     "La corrida vuelve desde el principio con la misma entrada; dale una arista «agotado» para cuando no haya más intentos."],
    },
    "limite_tasa": {
        "ejemplos": [{"maximo": 30, "por": "minuto"}, {"maximo": 100, "por": "dia", "clave": "{{entrada.cliente}}", "al_exceder": "saltar"}],
        "consejos": ["El cupo es por flujo y por clave, y lo respetan todos sus trabajadores a la vez.", "Con al_exceder saltar, dale una arista «excedido»."],
    },
    "ventana_horario": {
        "ejemplos": [{"desde": "08:00", "hasta": "17:00"}, {"dias": ["lun", "mar", "mie", "jue", "vie", "sab"], "desde": "07:00", "hasta": "13:00", "fuera": "saltar"}],
        "consejos": ["Los festivos de Colombia se calculan solos (Ley Emiliani y Semana Santa), sin red.",
                     "Con fuera aplazar la corrida vuelve sola cuando abre la ventana, sin ocupar un trabajador."],
    },
    "detener_con_alerta": {
        "ejemplos": [{"mensaje": "{{fallo.motivo}}", "nivel": "error"}, {"titulo": "Revisar a mano", "mensaje": "El caso {{radicado}} no trae anexos", "credencial": "teams-operaciones"}],
        "consejos": ["La alerta queda en las notificaciones de la plataforma; con credencial también sale a Teams o Slack (si una persona habilitó la escritura).",
                     "Es un final: no tiene aristas que salgan."],
    },
    "salida": {
        "ejemplos": [{"desenlace": "radicar", "campos": ["nombre", "documento"]}, {"desenlace": "manual", "motivo": "{{fallo.motivo}}"},
                     {"desenlace": "responder", "plantilla_respuesta": "{{respuesta}}", "archivos": ["{{documento}}", "{{imagenes}}"]}],
        "consejos": ["campos como objeto {nombre: plantilla} arma la solicitud con otros nombres.", "No se puede deshabilitar ni tiene aristas que salgan.",
                     "archivos dice qué entrega el flujo; todo lo que la ejecución generó igual queda en sus resultados (historial, Vera, webhook)."],
    },
    "responder_correo": {
        "ejemplos": [{"cuerpo": "{{respuesta}}", "adjuntos": ["{{documento}}"]},
                     {"cuerpo": "{{respuesta}}", "html": "<p>{{respuesta}}</p>", "cc": "archivo@ejemplo.gov.co", "incluir_original": False,
                      "adjuntos": ["{{documento}}", {"de": "adjuntos", "nombre": "anexo", "tipo": "pdf"}], "credencial": "smtp-respuestas"}],
        "consejos": ["Solo en flujos que arrancan con un correo; usa el buzón del disparador (su credencial) o la de este paso. Con un buzón IMAP pon una smtp.",
                     "Escribe afuera: mientras una persona no habilite la escritura de la credencial (o si el flujo no tiene buzón) la respuesta se arma "
                     "completa, con sus adjuntos y tamaños, y queda retenida en el detalle de la ejecución.",
                     "Por Graph responde con createReply (queda en el hilo del original); por SMTP lleva In-Reply-To y References."],
    },
    "ia_imagen": {
        "ejemplos": [{"instrucciones": "Ilustración sencilla para el boletín sobre {{tema}}, sin texto", "aspecto": "16:9"},
                     {"instrucciones": "Ícono plano de un documento con un sello", "cantidad": 2, "estilo": "minimalista, azul y blanco", "negativo": "texto, marcas de agua"},
                     {"instrucciones": "Portada del informe de {{mes}}", "proveedor": "openai", "modelo": "gpt-image-1", "credencial": "openai-imagenes", "tamano": "vertical"}],
        "consejos": ["Por defecto usa Vertex (gemini-2.5-flash-image) con la identidad del servicio: no necesita llave.",
                     "Deja la lista de imágenes en su salida: adjúntalas con «{{imagenes}}» o la primera con «{{imagenes.0.id}}».",
                     "Las imágenes son de la IA: no las uses como evidencia ni para representar a personas reales."],
    },
}

for _t, _a in AYUDA.items():
    TIPOS[_t]["ejemplos"] = _a["ejemplos"]
    TIPOS[_t]["consejos"] = _a["consejos"]
for _t, _s in TIPOS.items():
    _s["disparador"] = _t in DISPARADORES
    _s["escritura"] = _t in ESCRITURA
    # los comunes que aplican a este tipo, marcados como tales para el editor
    _props = _s["config"].setdefault("properties", {})
    _comunes: Dict[str, Any] = {}
    if _t not in TERMINALES and _t not in DISPARADORES:
        _comunes["entradas"] = ESQUEMA_COMUNES["entradas"]
    if _t in TIPOS_IA or _t == "leer_adjuntos":
        # proveedor, modelo y credencial de IA propios del nodo (o heredan del flujo y de la global)
        _comunes["proveedor"] = ESQUEMA_COMUNES["proveedor"]
        _comunes["modelo"] = ESQUEMA_COMUNES["modelo"]
        _comunes["credencial"] = ESQUEMA_COMUNES["credencial"]
    if _t in TIPOS_IA:
        _comunes["temperatura"] = ESQUEMA_COMUNES["temperatura"]
    if _t not in NO_DESHABILITABLES:
        _comunes["deshabilitado"] = ESQUEMA_COMUNES["deshabilitado"]
    if _t not in TERMINALES:
        _comunes["continuar_si_falla"] = ESQUEMA_COMUNES["continuar_si_falla"]
    if _t in CON_REINTENTOS:
        _comunes["reintentos"] = ESQUEMA_COMUNES["reintentos"]
    for _k, _v in _comunes.items():
        _props.setdefault(_k, {**_v, "x-comun": True})

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

# Una automatización sin correo: lee una base, recorre las filas, calcula y guarda estado.
EJEMPLO_AUTOMATIZACION: Dict[str, Any] = {
    "id": "pendientes-diarios", "nombre": "Pendientes diarios", "descripcion": "Cada mañana revisa los casos pendientes y anota el total de cada uno.",
    "modelo": "gemini-2.5-flash",
    "nodos": [
        {"id": "inicio", "tipo": "programado", "nombre": "Cada mañana", "config": {"cron": "0 7 * * 1-5", "activo": False}},
        {"id": "consulta", "tipo": "sql_consulta", "nombre": "Pendientes", "config": {"credencial": "base-local",
            "consulta": "SELECT id, monto FROM casos WHERE estado = :estado", "parametros": {"estado": "pendiente"}, "salida": "filas"}},
        {"id": "por_fila", "tipo": "para_cada", "nombre": "Por cada caso", "config": {"lista": "filas", "variable": "fila", "salida": "resultados"}},
        {"id": "total", "tipo": "expresion", "nombre": "Total con IVA", "config": {"expresion": "round(fila.monto * 1.19, 2)", "salida": "total"}},
        {"id": "anotar", "tipo": "tabla_guardar", "nombre": "Anotar", "config": {"tabla": "totales", "clave": "{{fila.id}}", "datos": {"total": "{{total}}"}}},
        {"id": "fin", "tipo": "salida", "nombre": "Listo", "config": {"desenlace": "manual", "motivo": "{{resultados}}"}},
        {"id": "sin_filas", "tipo": "salida", "nombre": "Nada pendiente", "config": {"desenlace": "descartar", "motivo": "no hay pendientes"}},
    ],
    "aristas": [{"desde": "inicio", "hacia": "consulta"}, {"desde": "consulta", "hacia": "por_fila", "cuando": "ok"},
                {"desde": "consulta", "hacia": "sin_filas", "cuando": "vacio"}, {"desde": "por_fila", "hacia": "total", "cuando": "cada"},
                {"desde": "total", "hacia": "anotar"}, {"desde": "por_fila", "hacia": "fin", "cuando": "listo"}],
}

REGLAS = """Un flujo declarativo es {id, nombre, descripcion, ia, nodos: [{id, tipo, nombre, descripcion, config}], aristas: [{desde, hacia, cuando}]}.
- id del flujo: minúsculas con guiones (ej. "derechos-de-peticion"); id de nodo: minúsculas con guion bajo.
- Exactamente un DISPARADOR (entrada_correo, entrada_manual, entrada_archivos, programado, webhook o correo_nuevo) y al menos una salida. Todo nodo alcanzable desde el disparador;
  sin ciclos (los bucles se hacen con para_cada); las salidas (y detener_con_alerta, que termina con un aviso) no tienen aristas que salgan.
- Una arista se toma si "cuando" es igual a la salida del nodo (sin distinguir mayúsculas ni tildes); una arista sin "cuando" (o "*") es la de por defecto.
  Cada salida posible del nodo necesita su arista o una por defecto (p. ej. ia_clasificar: una por categoría o una por defecto).
- Bucles: para_cada sale por "cada" hacia el cuerpo y por "listo" al terminar. Dentro del cuerpo un nodo puede no tener aristas que salgan (ahí termina la
  vuelta), igual que fin_ciclo; si el cuerpo llega a una salida, termina todo el flujo. paralelo corre sus ramas una tras otra y se juntan en un unir.
- Paso de datos: cada nodo puede declarar en config "entradas" (lo que recibe) y "salida" (la variable donde deja su resultado).
  En los nodos de IA, con "entradas" el modelo ve SOLO eso; sin "entradas" ve el correo, los adjuntos y las variables.
- Plantillas {{...}} en instrucciones, textos, motivos, destinos y mensajes: {{correo.asunto}}, {{correo.cuerpo}}, {{correo.remitente}},
  {{correo.remitente_correo}}, {{correo.remitente_nombre}}, {{adjuntos.texto}}, {{adjuntos.nombres}}, {{archivos.texto}}, {{archivos.nombres}}, {{archivos.cantidad}},
  {{entrada.<campo>}}, {{<variable>}}, {{<variable>.<campo>}},
  {{<lista>.0.<campo>}}, {{<lista>[campo=valor].<otro>}}, {{<variable>["clave.con.puntos"]}}.
  Toda variable usada tiene que salir de un nodo anterior en algún camino; si en la corrida no tiene valor, queda vacía.
- Fallos: cualquier nodo (salvo salida y fin_ciclo) puede tener una arista con cuando "error": si el nodo falla, el flujo sigue por ella con
  {{fallo.motivo}}, {{fallo.nodo}} y {{fallo.tipo}}. Con config.continuar_si_falla: true sigue por su arista normal y lo anota como aviso.
  Sin ninguna de las dos, la corrida termina en error. No uses "error" como categoría, opción, caso ni rama.
- config.reintentos {intentos 1..5, espera_ms, backoff, maximo_ms} (IA, conectores, enviar, notificar, http) repite solo fallos pasajeros (429, 5xx, tiempo, conexión).
- config.deshabilitado: true salta el nodo (sigue por la arista por defecto o la primera); no vale en disparadores, salida, fin_ciclo, para_cada, paralelo ni unir.
- Credenciales: los conectores usan "credencial": el NOMBRE de una credencial guardada en la plataforma (nunca valores ni claves en el flujo).
- Aprobación humana: aprobacion_humana detiene la corrida («en espera», guardada en disco) hasta que una PERSONA decide en la plataforma; una corrida nueva
  sigue por «aprobado», «rechazado» (o «vencido») con {{aprobacion.decision}}, {{aprobacion.comentario}}, {{aprobacion.cambios}} y {{aprobacion.datos}}.
  No va dentro de para_cada, de ramas ni de flujos llamados. Vera puede listar las aprobaciones, nunca decidirlas.
- Corridas que vuelven: reintentar_mas_tarde, limite_tasa (al_exceder aplazar) y ventana_horario (fuera aplazar) terminan la corrida como «aplazada» y la
  vuelven a correr desde el principio a la hora que toca, con la misma entrada; {{aplazamiento.intento}} dice en qué intento va (0 la primera vez).
- Archivos del flujo: documento_generar, pdf_unir, pdf_dividir, documento_convertir y hoja_escribir dejan fichas {id, nombre, tipo, tamano}; los nodos que
  leen documentos reciben ids («a-…»), plantillas como «{{carta.id}}», una variable con fichas o «adjuntos» (los del correo).
- Datos personales: antes de mandar textos con datos de personas a una IA externa (proveedor distinto de vertex), pásalos por anonimizar (modo seudonimo,
  guardar_mapa) y, si hace falta, revierte la respuesta con otro anonimizar (operacion revertir).
- LÍMITES (no se negocian): los nodos que escriben afuera (correo_enviar, drive_subir, sql_ejecutar, http distinto de GET, mensaje_teams, mensaje_slack,
  webhook_saliente, hoja_escribir con destino sheets y la alerta de detener_con_alerta con credencial) solo escriben si una PERSONA habilitó
  la escritura de su credencial; si no, arman la acción y la anotan como escritura bloqueada. notificar y enviar nunca envían. Nunca se tocan hosts de Positiva
  (positiva.gov.co), SGDEA, metadatos ni IP privadas, ni los buzones de PQRD y tutelas. Archivos solo dentro de /datos/archivos. SQL: una sola sentencia,
  solo lectura en sql_consulta, tiempos de espera (20 s) y tope de filas (500).
- http: un GET sale de verdad solo a hosts de HTTP_HOSTS_PERMITIDOS o a los hosts de su credencial; "credencial" puede ser una credencial guardada
  (http_bearer, http_basico) o el nombre de una variable de entorno (HTTP_TOKEN_X).
- IA: "ia" del flujo es {"usar": "global"} (la IA global de la plataforma, lo normal) o {"proveedor", "modelo", "credencial", "temperatura"} propia;
  cada nodo de IA (y leer_adjuntos para la visión) puede poner config.proveedor, config.modelo y config.credencial (si no, hereda: nodo → flujo → global).
  Proveedores: vertex (sin credencial), gemini, openai, azure_openai, deepseek, mistral, groq, openrouter, ollama, openai_compatible, anthropic,
  cohere y personalizado; credencial es el NOMBRE de una conexión ia_<proveedor> que creó una persona (nunca la llave). El modelo tiene que aceptar
  lo que pide el nodo (JSON en clasificar/decidir/extraer, imágenes en leer_adjuntos con vision). Un flujo sin "ia" usa Vertex con "modelo".
- Modelos de Vertex: gemini-2.5-flash (por defecto), gemini-2.5-pro, gemini-3-flash-preview, gemini-3.1-flash-lite, gemini-3.1-pro-preview."""


def _tipo_humano(v: Dict[str, Any]) -> str:
    f = v.get("format")
    if f == "credencial":
        return "credencial " + "|".join(v.get("x-credencial-tipos") or [])
    if f in ("plantilla", "variable", "expresion", "cron"):
        return {"plantilla": "plantilla", "variable": "variable", "expresion": "expresión", "cron": "cron 5 campos"}[f]
    if f == "texto-largo":
        return "texto"
    if v.get("enum"):
        return "|".join(map(str, v["enum"]))
    t = v.get("type")
    if t in ("integer", "number"):
        rango = f" {v['minimum']}..{v.get('maximum', '')}" if "minimum" in v else ""
        return ("entero" if t == "integer" else "número") + rango
    if t == "boolean":
        return "booleano"
    if t == "array":
        it = v.get("items") or {}
        if it.get("type") == "object":
            return "lista [{" + ", ".join(it.get("properties") or {}) + "}]"
        return "lista"
    if t == "object":
        if v.get("properties"):
            return "{" + ", ".join(v["properties"]) + "}"
        return "objeto {nombre: valor}"
    if t == "string":
        return "texto"
    return "valor"


def _linea_config(esq: Dict[str, Any]) -> str:
    partes = []
    req = set(esq.get("required") or [])
    for k, v in (esq.get("properties") or {}).items():
        if v.get("x-comun"):
            continue
        extra = []
        if k in req:
            extra.append("obligatorio")
        if "default" in v:
            d = v["default"]
            extra.append(f"defecto {json.dumps(d, ensure_ascii=False) if not isinstance(d, str) else d}")
        desc = f": {v['description']}" if v.get("description") else ""
        partes.append(f"{k} ({_tipo_humano(v)}{'; ' + '; '.join(extra) if extra else ''}){desc}")
    return "; ".join(partes) or "(sin config)"


def _linea_comunes() -> str:
    partes = []
    for k, v in COMUNES.items():
        extra = f"; defecto {json.dumps(v['defecto'])}" if "defecto" in v else ""
        partes.append(f"{k} ({v.get('tipo', '')}{extra}): {v['descripcion']}")
    return "; ".join(partes)


def _documentacion() -> str:
    lineas = [REGLAS, "", "Config común: " + _linea_comunes(), "", _texto_busqueda(), "", "Tipos (config · salidas · ejemplo):"]
    for tipo, t in TIPOS.items():
        sal = ", ".join(t["salidas"]) or "termina (el flujo o la vuelta)"
        marcas = (" [DISPARADOR]" if t["disparador"] else "") + (" [ESCRIBE AFUERA]" if t["escritura"] else "")
        lineas.append(f"- {tipo}:{marcas} {t['descripcion']}")
        lineas.append(f"  config: {_linea_config(t['config'])}")
        lineas.append(f"  sale por: {sal}" + (f" · deja: {t['produce']}" if t.get("produce") else ""))
        lineas.append(f"  ejemplo: {json.dumps(t['ejemplo'], ensure_ascii=False)}")
    lineas += ["", "Ejemplo de flujo completo (sin OCR en la entrada, adjuntos aparte, IA que extrae y redacta, aviso simulado):",
               json.dumps(EJEMPLO_FLUJO, ensure_ascii=False),
               "", "Ejemplo de automatización programada (SQL, bucle, expresión y tabla integrada):",
               json.dumps(EJEMPLO_AUTOMATIZACION, ensure_ascii=False)]
    return "\n".join(lineas)


DOCUMENTACION = _documentacion()


def tipos_contrato() -> List[Dict[str, Any]]:
    """La lista TipoNodo[] de GET /api/nodos/tipos."""
    r = []
    for tipo, t in TIPOS.items():
        dinamicas = "salidas_desde" in t
        opcionales = list(OPCIONALES.get(tipo, []))
        salidas_fijas = [] if dinamicas else [s for s in t["salidas"] if " " not in s]
        if tipo == "bifurcar_por_valor":
            salidas_fijas = ["otro"]
        r.append({
            "tipo": tipo, "nombre": t["nombre"], "categoria": t["categoria"], "descripcion": t["descripcion"],
            "disparador": t["disparador"], "escritura": t["escritura"],
            "salidas": "dinamicas" if dinamicas else salidas_fijas,
            **({"salidas_desde": t["salidas_desde"], "salidas_fijas": salidas_fijas} if dinamicas else {}),
            "salidas_opcionales": opcionales + ([] if tipo in TERMINALES else ["error"]),
            "terminal": tipo in TERMINALES,
            "config": copy.deepcopy(t["config"]), "ejemplos": copy.deepcopy(t["ejemplos"]), "consejos": list(t["consejos"]),
            "ejemplo": copy.deepcopy(t["ejemplo"]), "produce": t.get("produce", ""),
        })
    return r


def esquema() -> Dict[str, Any]:
    """La especificación de tipos (con sus ejemplos y consejos), serializable a JSON."""
    return copy.deepcopy({"version": VERSION, "reglas": REGLAS, "comunes": COMUNES, "plantillas": PLANTILLAS, "desenlaces": DESENLACES,
                          "disparadores": DISPARADORES, "tipos_credencial": TIPOS_CREDENCIAL, "tipos": TIPOS,
                          "busqueda": {"operadores": OPERADORES_AYUDA, "comparaciones": COMPARACION_AYUDA, "contar": CONTAR, "modos": ["todas", "alguna"]},
                          "ejemplo_flujo": EJEMPLO_FLUJO, "ejemplo_automatizacion": EJEMPLO_AUTOMATIZACION})


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] == ["json"]:
        print(json.dumps(esquema(), ensure_ascii=False, indent=2))
    elif sys.argv[1:2] == ["tipos"]:
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(tipos_contrato(), ensure_ascii=False))
    else:
        print(DOCUMENTACION)
