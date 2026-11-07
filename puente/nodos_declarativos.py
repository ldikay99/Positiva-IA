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

VERSION = 4

DESENLACES = ["radicar", "manual", "reenviar", "descartar", "responder", "duplicado"]
DISPARADORES = ["entrada_correo", "entrada_manual", "programado", "webhook", "correo_nuevo"]
TIPOS_CREDENCIAL = ["imap", "smtp", "microsoft_graph", "google_servicio", "postgres", "mysql", "sqlite", "http_bearer", "http_basico"]


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
    "modelo": {"tipo": "texto", "descripcion": "Solo nodos de IA: modelo de este nodo (si no, el del flujo)."},
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
    "modelo": texto(COMUNES["modelo"]["descripcion"]),
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
    "entrada.<campo>": "lo que trae el disparador (entrada_manual, programado, webhook, correo_nuevo)",
    "<variable>": "una variable de un nodo anterior", "<variable>.<campo>": "un campo de una variable JSON (p. ej. la fila de buscar_en_tabla)",
    "<lista>.0.<campo>": "un elemento de una lista por su posición (desde 0); también <lista>[0]",
    "<lista>[campo=valor].<otro>": "el primer elemento cuyo campo vale eso (sin distinguir mayúsculas ni tildes); con espacios: [campo=\"dos palabras\"]",
    "<variable>[\"a.b\"]": "una clave que tiene puntos o espacios, entre comillas",
    "fallo.motivo": "en la rama de error: por qué falló (también fallo.nodo, fallo.nombre, fallo.tipo, fallo.error)",
}

_CAMPO_ENTRADA = objeto({"nombre": variable("nombre del campo (queda en {{entrada.<nombre>}})"),
                         "tipo": opcion(["texto", "numero", "booleano", "fecha", "lista", "objeto"], "tipo del valor", "texto"),
                         "obligatorio": booleano("si falta, la corrida termina en error", False),
                         "descripcion": texto("para qué sirve")}, ["nombre"])
_FILTRO_CORREO = objeto({"remitente": plantilla("contiene este remitente"), "asunto": plantilla("el asunto contiene"),
                         "texto": plantilla("el asunto o el cuerpo contiene"), "no_leidos": booleano("solo los no leídos", False)},
                        desc="qué correos traer")

TIPOS: Dict[str, Dict[str, Any]] = {
    # ------------------------------------------------------------ disparadores
    "entrada_correo": {
        "nombre": "Entrada de correo", "categoria": "entrada",
        "descripcion": "Corre sobre un correo descargado de la bandeja: lee asunto, cuerpo, remitente y fecha. Puede leer también los adjuntos; si el flujo tiene un nodo leer_adjuntos, por defecto no los lee aquí.",
        "config": conf({"leer_adjuntos": booleano("leer los adjuntos aquí (por defecto sí, salvo que haya un nodo leer_adjuntos)"),
                        "ocr": booleano("OCR (tesseract y visión) al leerlos", True)}),
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
                        "nombre_patron": texto("regex: solo adjuntos cuyo nombre coincida"),
                        "max_paginas": entero("páginas por PDF", 1, 60, 60), "max_caracteres": entero("caracteres por adjunto", 1, None, 60000),
                        "salida": variable("además de adjuntos.texto, deja el texto en esta variable")}),
        "salidas": ["ok", "sin_adjuntos"], "produce": "adjuntos.texto, adjuntos.leidos (+ salida)",
        "ejemplo": {"id": "adjuntos", "tipo": "leer_adjuntos", "nombre": "Leer PDF", "config": {"tipos": ["pdf", "imagen"], "ocr": True, "max_paginas": 10}},
    },
    # ------------------------------------------------------------- lógica y ruta
    "filtro": {
        "nombre": "Filtro", "categoria": "filtro",
        "descripcion": "Compara un dato con un valor y sale por «si» o «no».",
        "config": conf({"campo": texto("asunto, cuerpo, remitente, adjuntos, texto (todo), una ruta (correo.asunto) o una variable"),
                        "operador": opcion(["contiene", "coincide", "no_coincide", "igual", "vacio", "no_vacio"], "cómo comparar", "contiene"),
                        "valor": texto("texto o regex (coincide); obligatorio salvo vacio/no_vacio")}, ["campo"]),
        "salidas": ["si", "no"],
        "ejemplo": {"id": "es_juzgado", "tipo": "filtro", "nombre": "¿Viene de un juzgado?", "config": {"campo": "remitente", "operador": "coincide", "valor": "@cendoj\\.ramajudicial\\.gov\\.co"}},
    },
    "condicion": {
        "nombre": "Condición", "categoria": "ruta",
        "descripcion": "Igual que filtro, pensado para variables ya obtenidas.",
        "config": conf({"campo": texto("variable, ruta o plantilla"),
                        "operador": opcion(["contiene", "coincide", "no_coincide", "igual", "vacio", "no_vacio"], "como en filtro", "contiene"),
                        "valor": texto("texto o regex")}, ["campo"]),
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
                                                "obligatorio": booleano("", False)}, ["nombre"]), "los campos a extraer", 1),
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
        "descripcion": "Extracción determinista con expresiones regulares (sin IA). Sale por «ok» o «incompleto» si falta un obligatorio.",
        "config": conf({"de": plantilla("dónde buscar (por defecto asunto + cuerpo + adjuntos)"),
                        "campos": lista(objeto({"nombre": variable(), "patron": texto("regex"), "grupo": entero("grupo (1 si hay grupos, si no 0)", 0),
                                                "todos": booleano("lista con todas las coincidencias", False), "obligatorio": booleano("", False)},
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
                                                "valor": libre("patron: regex completo; en_lista: lista; longitud: [min, max]"),
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
            "op": opcion(["plantilla", "asignar", "mayusculas", "minusculas", "titulo", "limpiar", "solo_digitos", "regex_extraer", "regex_reemplazar", "concatenar", "defecto"]),
            "salida": variable(), "de": libre("variable o plantilla (concatenar: lista)"), "plantilla": plantilla(), "valor": libre("asignar / defecto"),
            "patron": texto("regex"), "reemplazo": texto(), "separador": texto("", " "), "grupo": entero("", 0)}, ["op", "salida"]),
            "op: plantilla {plantilla} | asignar {valor} | mayusculas|minusculas|titulo|limpiar|solo_digitos {de} | regex_extraer {de, patron, grupo, defecto} | "
            "regex_reemplazar {de, patron, reemplazo} | concatenar {de: [..], separador} | defecto {de, valor}", 1)}, ["operaciones"]),
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
                        "modo": opcion(["igual", "contiene"], "sin distinguir mayúsculas ni tildes", "igual"),
                        "salida": variable("la fila como JSON: {{salida.columna}} (por defecto: id del nodo)")}, ["buscar", "columna"]),
        "salidas": ["encontrado", "no_encontrado"], "produce": "salida",
        "ejemplo": {"id": "area", "tipo": "buscar_en_tabla", "nombre": "Área responsable", "config": {
            "buscar": "tema", "columna": "tema", "traer": ["area", "correo"], "salida": "area",
            "tabla": [{"tema": "incapacidad", "area": "Prestaciones económicas", "correo": "prestaciones@ejemplo.gov.co"},
                      {"tema": "afiliacion", "area": "Afiliaciones", "correo": "afiliaciones@ejemplo.gov.co"}]}},
    },
    "bifurcar_por_valor": {
        "nombre": "Bifurcar por valor", "categoria": "ruta",
        "descripcion": "Switch: sale por el caso que coincide con el valor de la variable (sin distinguir mayúsculas ni tildes) o por «otro».",
        "config": conf({"variable": plantilla("variable o plantilla"), "casos": lista(texto(), "los valores (cada uno es una salida)", 1)}, ["variable", "casos"]),
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
        "descripcion": "Corre varias ramas, una tras otra (no a la vez): una arista por rama (cuando = nombre de la rama). Cada rama termina en un nodo unir "
                       "(el flujo sigue desde ese unir con lo de todas) o en un nodo sin salida (entonces sigue por «listo»).",
        "config": conf({"ramas": lista(texto(), "los nombres de las ramas (cada uno es una salida)", 1)}, ["ramas"]),
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
        "descripcion": "Trae filas de la tabla integrada por clave o por igualdad de campos. Sale por «encontrado» o «vacio».",
        "config": conf({"tabla": texto("nombre"), "clave": plantilla("una clave"), "filtro": mapa(plantilla(), "{campo: valor} que tienen que coincidir"),
                        "limite": entero("tope de filas", 1, 500, 100), "orden": opcion(["-creada", "creada", "-actualizada", "actualizada", "clave", "-clave"], "", "-creada"),
                        "ambito": opcion(["flujo", "compartida"], "", "flujo"), "salida": SALIDA()}, ["tabla"]),
        "salidas": ["encontrado", "vacio"], "produce": "salida (lista de filas)",
        "ejemplo": {"id": "ya_visto", "tipo": "tabla_consultar", "nombre": "¿Ya procesado?", "config": {"tabla": "procesados", "clave": "{{fila.id}}", "salida": "previo"}},
    },
    "archivo_leer": {
        "nombre": "Leer archivo", "categoria": "datos",
        "descripcion": "Lee un archivo del espacio de archivos (/datos/archivos): texto (PDF y Word con la conversión del motor), JSON o filas (CSV/XLSX). "
                       "Nunca fuera de esa carpeta.",
        "config": conf({"ruta": plantilla("ruta relativa, p. ej. «entradas/lista.csv»"), "formato": opcion(["auto", "texto", "json", "filas"], "", "auto"),
                        "hoja": texto("hoja de un XLSX"), "max_caracteres": entero("", 1, 500000, 60000), "max_filas": MAX_FILAS, "salida": SALIDA()}, ["ruta"]),
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
        "descripcion": "Convierte un CSV o XLSX en filas: de un adjunto del correo, de un archivo de Drive, del espacio de archivos o de una URL de HTTP_HOSTS_PERMITIDOS.",
        "config": conf({"origen": opcion(["adjunto", "drive", "archivo", "url"], "de dónde sale"),
                        "adjunto": texto("adjunto: regex del nombre (por defecto el primer .csv/.xlsx)"), "credencial": credencial(["google_servicio"], "drive: la cuenta de servicio"),
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
        "descripcion": "Envía un correo por SMTP o Microsoft Graph. ESCRIBE AFUERA: solo sale si una persona habilitó la escritura de la credencial; si no, se arma y "
                       "queda como escritura bloqueada. Nunca a direcciones de Positiva.",
        "config": conf({"credencial": credencial(["smtp", "microsoft_graph"]), "para": plantilla("destinatarios separados por coma"),
                        "cc": plantilla(), "cco": plantilla(), "asunto": plantilla(), "cuerpo": plantilla("texto del correo", largo=True),
                        "html": plantilla("versión HTML opcional", largo=True), "reintentos": _REINTENTOS, "timeout_s": TIMEOUT, "salida": SALIDA()},
                       ["credencial", "para", "asunto", "cuerpo"]),
        "salidas": ["ok"], "produce": "salida {enviado, bloqueado, para, asunto}", "x-credencial": True,
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
        "descripcion": "Sube un archivo de texto, JSON o CSV a Drive. ESCRIBE AFUERA: solo sale si una persona habilitó la escritura de la credencial.",
        "config": conf({"credencial": credencial(["google_servicio"]), "carpeta": plantilla("id de la carpeta destino"), "nombre": plantilla("nombre del archivo"),
                        "contenido": plantilla("el texto (o usa de)", largo=True), "de": plantilla("variable a subir"),
                        "formato": opcion(["texto", "json", "csv"], "", "texto"), "reintentos": _REINTENTOS, "timeout_s": TIMEOUT, "salida": SALIDA()},
                       ["credencial", "nombre"]),
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
                        "asunto": plantilla("", "RE: {{correo.asunto}}"), "cc": plantilla(), "salida": SALIDA()}, ["cuerpo"]),
        "salidas": ["ok"], "produce": "salida (el mensaje armado)",
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
        "descripcion": "Arma lo que devuelve un flujo con disparador webhook y responder: true (estado, cuerpo). No escribe afuera.",
        "config": conf({"estado": entero("código HTTP", 100, 599, 200), "cuerpo": libre("plantilla u objeto JSON con plantillas"),
                        "tipo_contenido": opcion(["application/json", "text/plain"], "", "application/json")}),
        "salidas": ["ok"], "produce": "",
        "ejemplo": {"id": "contestar", "tipo": "respuesta_webhook", "nombre": "Contestar", "config": {"estado": 200, "cuerpo": {"ok": True, "total": "{{total}}"}}},
    },
    "salida": {
        "nombre": "Salida", "categoria": "salida",
        "descripcion": "Termina el flujo con un desenlace. Entrega la solicitud con los campos indicados y, si se da, la respuesta armada con la plantilla.",
        "config": conf({"desenlace": opcion(DESENLACES, "cómo termina"), "motivo": plantilla(), "destino": plantilla(),
                        "campos": libre("lista de variables o {nombre: plantilla} (por defecto todas las variables)"),
                        "plantilla_respuesta": plantilla("el texto final que entrega el flujo", largo=True)}, ["desenlace"]),
        "salidas": [],
        "ejemplo": {"id": "radicar", "tipo": "salida", "nombre": "Radicar", "config": {
            "desenlace": "radicar", "campos": ["nombre", "documento"], "plantilla_respuesta": "{{respuesta}}"}},
    },
}

# Los que escriben afuera: solo escriben si su credencial tiene permitir_escritura (o nunca, si no tienen credencial).
ESCRITURA = {"correo_enviar", "drive_subir", "sql_ejecutar", "http", "enviar", "notificar"}
TIPOS_IA = {"ia_clasificar", "ia_decidir", "ia_extraer", "ia_generar", "resumir", "ia_redactar"}
CON_REINTENTOS = TIPOS_IA | {"notificar", "enviar", "http", "correo_leer", "correo_enviar", "drive_listar", "drive_leer", "drive_subir",
                             "sql_consulta", "sql_ejecutar", "hoja_leer", "llamar_flujo"}
NO_DESHABILITABLES = set(DISPARADORES) | {"salida", "fin_ciclo", "para_cada", "paralelo", "unir"}
TERMINALES = {"salida", "fin_ciclo"}

# La ayuda de cada tipo: configs para copiar (ejemplos) y consejos cortos.
AYUDA: Dict[str, Dict[str, Any]] = {
    "entrada_correo": {
        "ejemplos": [{"leer_adjuntos": False}, {"leer_adjuntos": True, "ocr": False}],
        "consejos": ["Si vas a filtrar antes de leer los adjuntos, pon leer_adjuntos: false y un nodo leer_adjuntos después del filtro.",
                     "Es un disparador: cada flujo tiene exactamente uno."],
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
                     "Bajar max_paginas acelera mucho los PDF escaneados largos."],
    },
    "filtro": {
        "ejemplos": [{"campo": "asunto", "operador": "contiene", "valor": "incapacidad"},
                     {"campo": "correo.remitente_correo", "operador": "coincide", "valor": "@(cendoj\\.)?ramajudicial\\.gov\\.co$"}],
        "consejos": ["contiene e igual no distinguen mayúsculas ni tildes; coincide usa regex.", "campo «texto» mira asunto, cuerpo y adjuntos juntos."],
    },
    "condicion": {
        "ejemplos": [{"campo": "documento", "operador": "no_vacio"}, {"campo": "{{area.area}}", "operador": "igual", "valor": "Prestaciones"}],
        "consejos": ["Úsala con variables ya obtenidas; para comparar números usa expresion con como_condicion."],
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
                     "Con entradas limitas lo que ve la IA: menos texto, menos costo y menos errores."],
    },
    "extraer_regex": {
        "ejemplos": [{"de": "{{correo.asunto}} {{correo.cuerpo}}", "campos": [{"nombre": "radicado", "patron": "\\b(\\d{23})\\b", "obligatorio": True}]},
                     {"de": "adjuntos.texto", "campos": [{"nombre": "cie10", "patron": "\\b[A-Z]\\d{2}(?:\\.\\d)?\\b", "todos": True}]}],
        "consejos": ["Es gratis y determinista: úsalo antes que la IA para números con formato fijo.", "todos: true deja una lista sin repetidos."],
    },
    "validar": {
        "ejemplos": [{"reglas": [{"campo": "documento", "regla": "patron", "valor": "[0-9]{6,10}", "codigo": "DOCUMENTO_INVALIDO"}]},
                     {"reglas": [{"campo": "tema", "regla": "en_lista", "valor": ["incapacidad", "afiliacion"]}, {"campo": "nombre", "regla": "longitud", "valor": [3, 120]}]}],
        "consejos": ["El código del primer reparo queda en el motivo de la salida.", "patron exige que todo el texto coincida, no un pedazo."],
    },
    "transformar": {
        "ejemplos": [{"operaciones": [{"op": "solo_digitos", "de": "documento", "salida": "documento"}, {"op": "titulo", "de": "nombre", "salida": "nombre"}]},
                     {"operaciones": [{"op": "plantilla", "plantilla": "{{lista[estado=activo].id}}", "salida": "id_activo"}]}],
        "consejos": ["Las operaciones corren en orden: una puede usar la salida de la anterior.", "defecto rellena una variable vacía con un valor fijo o plantilla."],
    },
    "buscar_en_tabla": {
        "ejemplos": [{"buscar": "tema", "columna": "tema", "traer": ["area", "correo"], "salida": "area",
                      "tabla": [{"tema": "incapacidad", "area": "Prestaciones", "correo": "prestaciones@ejemplo.gov.co"}]},
                     {"buscar": "{{correo.asunto}}", "modo": "contiene", "columna": "palabra", "salida": "ruta",
                      "tabla": [{"palabra": "tutela", "area": "Jurídica"}, {"palabra": "incapacidad", "area": "Prestaciones"}]}],
        "consejos": ["La fila queda como JSON: úsala con {{salida.columna}}.", "modo contiene sirve cuando el valor viene dentro de un texto más largo."],
    },
    "bifurcar_por_valor": {
        "ejemplos": [{"variable": "tema", "casos": ["incapacidad", "afiliacion"]}],
        "consejos": ["Lo que no coincide sale por «otro»: dale siempre una arista.", "No uses «error» como caso: es la rama de fallo."],
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
        "ejemplos": [{"tabla": "procesados", "clave": "{{fila.id}}", "salida": "previo"}, {"tabla": "procesados", "filtro": {"estado": "pendiente"}, "limite": 50}],
        "consejos": ["Sale por «vacio» si no hay filas: úsalo como «¿ya lo vi?»."],
    },
    "archivo_leer": {
        "ejemplos": [{"ruta": "entradas/lista.csv", "salida": "lista"}, {"ruta": "contratos/{{entrada.nombre}}.pdf", "formato": "texto", "salida": "contrato"}],
        "consejos": ["Las rutas son relativas a /datos/archivos; «..» se rechaza.", "Sale por «no_existe» si el archivo no está."],
    },
    "archivo_guardar": {
        "ejemplos": [{"ruta": "reportes/resultado.json", "de": "resultados", "formato": "json"}, {"ruta": "logs/envios.csv", "de": "filas", "formato": "csv", "modo": "anexar"}],
        "consejos": ["Solo escribe en /datos/archivos.", "modo anexar en CSV no repite los encabezados."],
    },
    "hoja_leer": {
        "ejemplos": [{"origen": "adjunto", "adjunto": "\\.xlsx$", "salida": "filas"}, {"origen": "drive", "credencial": "drive-operaciones", "archivo": "{{archivo.id}}", "hoja": "Datos"}],
        "consejos": ["Con encabezados: false las columnas se llaman columna_1, columna_2…", "Hojas de Google se exportan como CSV."],
    },
    "correo_leer": {
        "ejemplos": [{"credencial": "buzon-facturas", "filtro": {"asunto": "factura"}, "max": 10, "salida": "correos"},
                     {"credencial": "buzon-soporte", "desde_fecha": "2026-11-01", "incluir_adjuntos": True}],
        "consejos": ["Combínalo con para_cada para procesar cada correo.", "Nunca lee los buzones de Positiva: esos se leen por el buzón de la plataforma."],
    },
    "correo_enviar": {
        "ejemplos": [{"credencial": "smtp-notificaciones", "para": "{{entrada.correo}}", "asunto": "Resultado", "cuerpo": "{{resumen}}"}],
        "consejos": ["Mientras la credencial no tenga la escritura habilitada por una persona, el correo se arma y queda bloqueado.",
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
        "ejemplos": [{"credencial": "drive-operaciones", "carpeta": "1AbCdEfGhIjKlMnOp", "nombre": "reporte.csv", "de": "resultados", "formato": "csv"}],
        "consejos": ["Escribe afuera: queda bloqueado mientras una persona no habilite la escritura de la credencial."],
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
        "ejemplos": [{"cuerpo": "{{respuesta}}\n\nAtentamente,\nPositiva"}],
        "consejos": ["Por defecto va al remitente con «RE: asunto».", "Nunca se envía: queda como escritura bloqueada."],
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
        "ejemplos": [{"estado": 200, "cuerpo": {"ok": True, "total": "{{total}}"}}, {"estado": 422, "cuerpo": "faltan datos: {{fallo.motivo}}", "tipo_contenido": "text/plain"}],
        "consejos": ["Solo tiene efecto si el disparador es webhook con responder: true."],
    },
    "salida": {
        "ejemplos": [{"desenlace": "radicar", "campos": ["nombre", "documento"]}, {"desenlace": "manual", "motivo": "{{fallo.motivo}}"}],
        "consejos": ["campos como objeto {nombre: plantilla} arma la solicitud con otros nombres.", "No se puede deshabilitar ni tiene aristas que salgan."],
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
    if _t in TIPOS_IA:
        _comunes["modelo"] = ESQUEMA_COMUNES["modelo"]
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

REGLAS = """Un flujo declarativo es {id, nombre, descripcion, modelo, nodos: [{id, tipo, nombre, descripcion, config}], aristas: [{desde, hacia, cuando}]}.
- id del flujo: minúsculas con guiones (ej. "derechos-de-peticion"); id de nodo: minúsculas con guion bajo.
- Exactamente un DISPARADOR (entrada_correo, entrada_manual, programado, webhook o correo_nuevo) y al menos una salida. Todo nodo alcanzable desde el disparador;
  sin ciclos (los bucles se hacen con para_cada); las salidas no tienen aristas que salgan.
- Una arista se toma si "cuando" es igual a la salida del nodo (sin distinguir mayúsculas ni tildes); una arista sin "cuando" (o "*") es la de por defecto.
  Cada salida posible del nodo necesita su arista o una por defecto (p. ej. ia_clasificar: una por categoría o una por defecto).
- Bucles: para_cada sale por "cada" hacia el cuerpo y por "listo" al terminar. Dentro del cuerpo un nodo puede no tener aristas que salgan (ahí termina la
  vuelta), igual que fin_ciclo; si el cuerpo llega a una salida, termina todo el flujo. paralelo corre sus ramas una tras otra y se juntan en un unir.
- Paso de datos: cada nodo puede declarar en config "entradas" (lo que recibe) y "salida" (la variable donde deja su resultado).
  En los nodos de IA, con "entradas" el modelo ve SOLO eso; sin "entradas" ve el correo, los adjuntos y las variables.
- Plantillas {{...}} en instrucciones, textos, motivos, destinos y mensajes: {{correo.asunto}}, {{correo.cuerpo}}, {{correo.remitente}},
  {{correo.remitente_correo}}, {{correo.remitente_nombre}}, {{adjuntos.texto}}, {{adjuntos.nombres}}, {{entrada.<campo>}}, {{<variable>}}, {{<variable>.<campo>}},
  {{<lista>.0.<campo>}}, {{<lista>[campo=valor].<otro>}}, {{<variable>["clave.con.puntos"]}}.
  Toda variable usada tiene que salir de un nodo anterior en algún camino; si en la corrida no tiene valor, queda vacía.
- Fallos: cualquier nodo (salvo salida y fin_ciclo) puede tener una arista con cuando "error": si el nodo falla, el flujo sigue por ella con
  {{fallo.motivo}}, {{fallo.nodo}} y {{fallo.tipo}}. Con config.continuar_si_falla: true sigue por su arista normal y lo anota como aviso.
  Sin ninguna de las dos, la corrida termina en error. No uses "error" como categoría, opción, caso ni rama.
- config.reintentos {intentos 1..5, espera_ms, backoff, maximo_ms} (IA, conectores, enviar, notificar, http) repite solo fallos pasajeros (429, 5xx, tiempo, conexión).
- config.deshabilitado: true salta el nodo (sigue por la arista por defecto o la primera); no vale en disparadores, salida, fin_ciclo, para_cada, paralelo ni unir.
- Credenciales: los conectores usan "credencial": el NOMBRE de una credencial guardada en la plataforma (nunca valores ni claves en el flujo).
- LÍMITES (no se negocian): los nodos que escriben afuera (correo_enviar, drive_subir, sql_ejecutar, http distinto de GET) solo escriben si una PERSONA habilitó
  la escritura de su credencial; si no, arman la acción y la anotan como escritura bloqueada. notificar y enviar nunca envían. Nunca se tocan hosts de Positiva
  (positiva.gov.co), SGDEA, metadatos ni IP privadas, ni los buzones de PQRD y tutelas. Archivos solo dentro de /datos/archivos. SQL: una sola sentencia,
  solo lectura en sql_consulta, tiempos de espera (20 s) y tope de filas (500).
- http: un GET sale de verdad solo a hosts de HTTP_HOSTS_PERMITIDOS o a los hosts de su credencial; "credencial" puede ser una credencial guardada
  (http_bearer, http_basico) o el nombre de una variable de entorno (HTTP_TOKEN_X).
- Modelos: gemini-2.5-flash (por defecto), gemini-2.5-pro, gemini-3-flash-preview, gemini-3.1-flash-lite."""


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
    lineas = [REGLAS, "", "Config común: " + _linea_comunes(), "", "Tipos (config · salidas · ejemplo):"]
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
        if tipo in ("ia_clasificar",):
            opcionales = ["sin_categoria"]
        elif tipo == "ia_decidir":
            opcionales = ["sin_decision"]
        elif tipo == "paralelo":
            opcionales = ["listo"]
        elif tipo == "tabla_guardar":
            opcionales = ["existente"]
        else:
            opcionales = []
        salidas_fijas = [] if dinamicas else [s for s in t["salidas"] if " " not in s]
        if tipo == "bifurcar_por_valor":
            salidas_fijas = ["otro"]
        if tipo == "expresion":
            opcionales = ["si", "no"]
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
