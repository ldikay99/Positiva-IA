"""Genera catalogo/tutelas.json: las etapas reales del job de tutelas (motores/tutelas).

Cada nodo es un tramo de `OrquestadorRadicacion.procesar_hilo` (Tutelas/main.py) tal
como lo anuncia el propio motor con `op.paso(...)`; el trabajador del visor
(puente/trabajador_tutelas.py, NODO_DEL_PASO) usa los mismos ids. Los nodos que el
motor no numera (`hilo`, `enlace`) los abre el trabajador al llamar a las mismas
funciones del job (`obtener_correos_por_conversacion`, `_buscar_enlace_sharepoint`).

Uso: python tutelas.py ../tutelas.json
"""
import json
import sys

M = "Tutelas/main.py"
OUT = "Tutelas/services/outlook_service.py"
SG = "Tutelas/services/sgdea_service.py"
RD = "Tutelas/services/redis_service.py"

# (id, nombre, categoría, descripción, [(regla, función, módulo)], [etiquetas de no radicación], siguientes)
N = [
    ("hilo", "Tomar el hilo de la carpeta de entrada", "entrada",
     "El job lee los 50 correos más recientes de la carpeta de entrada (TUTELAS-OUTLOOK-FOLDER-ENTRADA), sin filtrar por adjuntos, "
     "los agrupa por conversationId y procesa cada hilo como una unidad: los correos van del más antiguo al más reciente y el último es el principal.",
     [("obtener_correos(limite=50, solo_con_adjuntos=False): $top=50 y $orderby=receivedDateTime desc; los hilos salen en el orden de su correo más reciente.", "obtener_correos_por_conversacion", OUT),
      ("Cada hilo se ordena por receivedDateTime ascendente; sin conversationId, el id del correo es su propio hilo.", "obtener_correos_por_conversacion", OUT),
      ("Un hilo ya trabajado en la misma ejecución (sigue en la bandeja porque no se pudo mover) no se vuelve a tomar.", "_ejecutar_lote", M),
      ("Cualquier excepción dentro del hilo lo cierra con ERROR-INESPERADO.", "_ejecutar_lote", M)],
     ["ERROR-INESPERADO"], ["remitente"]),
    ("remitente", "Validar remitente judicial", "filtro",
     "Solo sigue el hilo en que algún correo proviene de un despacho de la Rama Judicial. El buzón interno (positiva.gov.co) no autoriza: aparece en casi todos los hilos por los reenvíos.",
     [("El dominio del remitente debe estar en DOMINIOS_JUDICIALES (cendoj.ramajudicial.gov.co, notificacionesrj.gov.co).", "es_correo_judicial", M),
      ("El primer correo judicial del hilo es el origen: su remitente y su fecha son los que se reportan a SGDEA.", "buscar_correo_judicial", M)],
     ["DOMINIO-DE-CORREO-INVALIDO"], ["tramite"]),
    ("tramite", "Identificar tipo de trámite", "filtro",
     "Sigue si algún correo del hilo (asunto + cuerpo) nombra la tutela o notifica una actuación procesal. Si solo es actuación, la confirma después un radicado válido; sin él, NO-ES-TUTELA.",
     [("_RE_TUTELA: «acción de tutela», «tutela(s)», «desacato», «incidente de desacato», «requerimiento previo».", "detectar_tutela", M),
      ("_RE_ACTUACION: notifica decisión/sentencia/fallo/auto/providencia/actuación/proceso, comunica actuación, actuación procesal, profirió…, auto admisorio/avoca, impugnación, sentencia, fallo, providencia.", "detectar_actuacion_judicial", M)],
     ["NO-ES-TUTELA"], ["enlace"]),
    ("enlace", "Expediente por enlace", "filtro",
     "Busca en el cuerpo de cada correo un enlace a un portal de expediente (SharePoint judicial, SAMAI, consulta de procesos, OneDrive, Drive, WeTransfer). "
     "Si lo hay y ningún correo del hilo anexa un PDF, no se radica. En producción TUTELAS-RADICAR-ENLACE-CON-ADJUNTOS=true: con enlace y PDF anexo, se ignora el enlace y sigue.",
     [("SharePointService.extraer_enlace_expediente sobre body_content y body_text de cada correo (DOMINIOS_EXPEDIENTE).", "_buscar_enlace_sharepoint", M),
      ("Un anexo cuenta si no es imagen (content-type) y su nombre termina en .pdf (es_documento_expediente).", "procesar_hilo", M),
      ("Con RADICAR_ENLACE_CON_ADJUNTOS=false también se rechaza el hilo con enlace aunque traiga PDF. La descarga desde SharePoint está deshabilitada en el job.", "procesar_hilo", M)],
     ["CORREO-CON-ENLACE"], ["radicado"]),
    ("radicado", "Extraer el radicado de 23 dígitos", "ia",
     "La IA (Vertex, gemini-2.5-flash, temperatura 0) lee el correo principal: radicado, código y nombre del juzgado, ciudad y documento del afectado. "
     "Si el radicado no es válido (23 dígitos), se busca por expresión regular en el principal y luego en cada correo del hilo.",
     [("ai.extraer_todo = extraer_radicado_y_juzgado + extraer_documento_afectado sobre asunto, remitente y los primeros 4000 caracteres del cuerpo del principal.", "extraer_todo", "Tutelas/services/ai_extractor.py"),
      ("Válido = exactamente 23 dígitos. Si la IA lo da válido, el código de juzgado es el que devuelve la IA.", "_es_radicado_valido", M),
      ("Regex: 23 dígitos seguidos, o por grupos con guiones/espacios; si son 21 dígitos en los 7 grupos oficiales (2-3-2-2-3-4-5) y año 1990–2100, se completa la instancia «00».", "extraer_radicado_regex", M),
      ("Con radicado por regex, el código de juzgado son sus 12 primeros dígitos. Sin radicado: SIN-RADICADO-ENCONTRADO si el hilo nombra la tutela; NO-ES-TUTELA si solo era actuación.", "procesar_hilo", M)],
     ["SIN-RADICADO-ENCONTRADO", "NO-ES-TUTELA"], ["juzgado", "radicado_pdf"]),
    ("radicado_pdf", "Buscar el radicado en los anexos PDF", "ocr",
     "Paso 3b: recorre los .pdf de cada correo del hilo. Por cada uno, primero texto + regex; si no aparece, la IA multimodal lee el PDF. "
     "El radicado leído por la IA se acepta solo si su año y consecutivo coinciden con un radicado corto («2026-00253») del asunto o cuerpo del principal, o si este no trae ninguno.",
     [("_extraer_texto_pdf: PyMuPDF (texto y bloques); con menos de 50 caracteres útiles, Tesseract a 300 DPI en las 10 primeras páginas.", "_extraer_texto_pdf", M),
      ("ai.extraer_radicado_de_pdf: Gemini con el PDF adjunto; solo devuelve exactamente 23 dígitos.", "extraer_radicado_de_pdf", "Tutelas/services/ai_extractor.py"),
      ("_radicado_coincide_con_corto: posiciones 13–16 (año) y 17–21 (consecutivo) contra los cortos del correo principal.", "_radicado_coincide_con_corto", M),
      ("El texto leído de los PDF se reutiliza para la huella de deduplicación.", "procesar_hilo", M)],
     ["SIN-RADICADO-ENCONTRADO", "NO-ES-TUTELA"], ["juzgado"]),
    ("juzgado", "Ubicar el juzgado", "datos",
     "Busca el despacho en la tabla juzgados por el código (solo dígitos, contra el código guardado sin caracteres no numéricos); si no aparece, por nombre y ciudad que leyó la IA.",
     [("buscar_por_codigo: regexp_replace(codigo, '[^0-9]', '', 'g') = código. En el visor, sobre juzgados_actualizada.csv.", "buscar_por_codigo", "Tutelas/services/juzgados_service.py"),
      ("buscar_por_nombre: UPPER(nombre) LIKE %nombre% y, si hay ciudad, UPPER(municipio) LIKE %ciudad%.", "buscar_por_nombre", "Tutelas/services/juzgados_service.py")],
     ["JUZGADO-NO-ENCONTRADO"], ["anexos"]),
    ("anexos", "Recopilar los anexos", "documentos",
     "Descarga los adjuntos de todos los correos del hilo, en orden, sin los de tipo imagen (firmas, logos) y sin repetir un nombre ya visto en el hilo. Exige al menos un .pdf.",
     [("obtener_adjuntos + descargar_adjunto de cada correo; get_adjuntos_validos descarta content-type image/*.", "get_adjuntos_validos", "Tutelas/models/correo.py"),
      ("Un nombre de archivo repetido en otro correo del hilo se omite.", "procesar_hilo", M),
      ("Sin ningún .pdf no hay actuación que radicar (el PDF del hilo se agrega después y no cuenta).", "procesar_hilo", M)],
     ["SIN-DOCUMENTACION-ADJUNTA"], ["duplicados"]),
    ("duplicados", "Verificar si ya fue radicada", "filtro",
     "Reclama en Redis (SET NX, 30 días) la llave del hilo y luego una por documento. Si alguna ya existía, la documentación ya se radicó: se liberan las reclamadas y no se radica.",
     [("Llave del hilo: radicado + huella del texto normalizado de los PDF (≥200 caracteres; sin fechas, horas ni paginación); si no, huella binaria (sin image*.png ni correo_completo.pdf); si no, el id del correo principal.", "_clave_dedup", M),
      ("Llave por documento: radicado + huella de cada PDF con texto o de cada archivo binario.", "_claves_por_documento", M),
      ("En el visor Redis va en memoria y vacío: no hay duplicados entre corridas.", "reclamar", RD)],
     ["RADICADO-DUPLICADO"], ["radicar"]),
    ("radicar", "Radicar en SGDEA", "integracion",
     "Agrega correo_completo.pdf (el hilo consolidado) y radica con POST /tutela, multipart con cada archivo por separado y el cuerpo JSON. El remitente es el correo judicial de origen; fecha y hora, las suyas en hora Colombia.",
     [("_generar_pdf_hilo (fpdf) después del reclamo, para que no entre en la huella.", "_generar_pdf_hilo", SG),
      ("Reintenta solo 401, 429, 503 y errores de conexión (3, 9, 27 s). 408/409/500/502/504/522 o timeout de lectura: resultado incierto, no se reintenta ni se libera el reclamo → RADICACION-POR-CONFIRMAR.", "radicar_tutela", SG),
      ("Otro 4xx o reintentos agotados: fallo claro, se liberan las llaves para reintentar en la próxima corrida → ERROR-RADICAR.", "procesar_hilo", M),
      ("En el visor se arma el cuerpo real y se anota; la sesión HTTP es de prueba y devuelve un radicado PRUEBA-….", "radicar_tutela", SG)],
     ["RADICACION-POR-CONFIRMAR", "ERROR-RADICAR"], ["trazabilidad"]),
    ("trazabilidad", "Registrar trazabilidad", "integracion",
     "Con el número de radicado de la respuesta, consulta GET /tutela/info/{radicado} y crea POST /trazabilidad/crear con idTutela e idRadicado. Si falla, se informa pero el hilo queda radicado.",
     [("obtener_info_radicado y crear_trazabilidad (proceso TUT{idTutela}, estado «Por asignar»).", "crear_trazabilidad", SG)],
     [], ["informar"]),
    ("informar", "Informar la radicación", "salida",
     "Responde al correo principal, dirigido a notificacionesjudiciales@positiva.gov.co, con el número de radicado y el aviso de IA.",
     [("responder_radicacion_exitosa (POST /reply). En el visor, anotado.", "responder_radicacion_exitosa", OUT)],
     [], ["archivar"]),
    ("archivar", "Archivar el hilo en TUTELAS", "salida",
     "Marca la llave como procesada y mueve todos los correos del hilo a la carpeta TUTELAS (resuelta por nombre).",
     [("redis.marcar_procesado y mover_hilo_a_tutelas. En el visor, anotado.", "mover_hilo_a_tutelas", OUT)],
     [], []),
]
FASES = [("recepcion", "Recepción", ["hilo", "remitente", "tramite", "enlace"]),
         ("extraccion", "Radicado y juzgado", ["radicado", "radicado_pdf", "juzgado"]),
         ("documentos", "Anexos y duplicados", ["anexos", "duplicados"]),
         ("radicacion", "Radicación", ["radicar", "trazabilidad", "informar", "archivar"])]

ETIQUETAS = ["DOMINIO-DE-CORREO-INVALIDO", "NO-ES-TUTELA", "CORREO-CON-ENLACE", "SIN-RADICADO-ENCONTRADO",
             "SIN-DOCUMENTACION-ADJUNTA", "JUZGADO-NO-ENCONTRADO", "RADICADO-DUPLICADO", "RADICACION-POR-CONFIRMAR",
             "ERROR-RADICAR", "ERROR-INESPERADO"]

nodos, aristas = [], []
for id_, nombre, cat, desc, reglas, etiquetas, siguientes in N:
    sig = siguientes + (["no_radicado"] if etiquetas else [])
    nodos.append({"id": id_, "nombre": nombre, "descripcion": desc, "etapa_cronometro": id_, "categoria": cat, "usa_ia": cat == "ia" or id_ == "radicado_pdf",
                  "modulo": M, "funciones": list(dict.fromkeys(r[1] for r in reglas)), "siguientes": sig,
                  "desenlaces": [{"accion": "manual", "cuando": e} for e in etiquetas] + ([{"accion": "radicado", "cuando": "hilo archivado en TUTELAS"}] if id_ == "archivar" else []),
                  "reglas": [{"id": f"{id_}_{j}", "descripcion": r[0], "modulo": r[2], "funcion": r[1]} for j, r in enumerate(reglas)], "parametros": []})
    for s in siguientes:
        etiqueta = "no está en el correo" if (id_, s) == ("radicado", "radicado_pdf") else "sigue"
        aristas.append({"desde": id_, "hacia": s, "etiqueta": etiqueta})
    if etiquetas:
        aristas.append({"desde": id_, "hacia": "no_radicado", "etiqueta": " / ".join(etiquetas)})

nodos.append({"id": "no_radicado", "nombre": "No se radica", "descripcion": "Cierra el hilo sin radicar: responde al correo principal hacia notificacionesjudiciales@positiva.gov.co con el motivo, etiqueta cada correo con la etiqueta y los devuelve a la Bandeja de entrada.",
              "terminal": True, "categoria": "salida", "etapa_cronometro": None, "modulo": M, "funciones": ["_mover_hilo_fallido"], "siguientes": [],
              "desenlaces": [{"accion": "manual", "cuando": e} for e in ETIQUETAS],
              "reglas": [{"id": "nr_0", "descripcion": "responder_no_radicado(principal, op.motivo(etiqueta), radicado si ya se identificó, accion=op.accion(etiqueta)); RADICACION-POR-CONFIRMAR pide verificar antes de radicar.", "modulo": M, "funcion": "_mover_hilo_fallido"},
                         {"id": "nr_1", "descripcion": "etiquetar_correo agrega la etiqueta a las categorías de cada correo del hilo; mover_hilo_a_bandeja_entrada los devuelve a Inbox.", "modulo": OUT, "funcion": "etiquetar_correo"},
                         {"id": "nr_2", "descripcion": "Etiquetas (operaciones.MOTIVOS): " + ", ".join(ETIQUETAS) + ".", "modulo": "Tutelas/operaciones.py", "funcion": "motivo"}],
              "parametros": []})
cat = {"nodos": nodos, "aristas": aristas, "fases": [{"id": a, "nombre": b, "nodos": c} for a, b, c in FASES],
       "herramientas_agente": [], "desenlaces": [{"accion": "radicado", "descripcion": "Radicado en SGDEA y archivado en TUTELAS"},
                                                 {"accion": "manual", "descripcion": "No se radica: vuelve a la Bandeja de entrada con su etiqueta"}],
       "proveedores": [{"id": "graph", "nombre": "Microsoft Graph (buzón de tutelas)", "tipo": "buzon"}, {"id": "vertex", "nombre": "Vertex AI (Gemini)", "tipo": "ia"},
                       {"id": "juzgados", "nombre": "Tabla de juzgados", "tipo": "registro_personas"}, {"id": "sgdea", "nombre": "SGDEA · radicación de tutelas", "tipo": "sgdea"},
                       {"id": "redis", "nombre": "Redis (deduplicación)", "tipo": "cache"}],
       "variables_entorno": [{"nombre": "TUTELAS-RADICAR-ENLACE-CON-ADJUNTOS", "uso": "En producción vale true: con enlace y PDF anexo se radica ignorando el enlace. El visor usa el mismo valor.", "secreto": False},
                             {"nombre": "TUTELAS-OUTLOOK-FOLDER-ENTRADA", "uso": "Carpeta que lee el job; el job no la define y usa el id por defecto de config.py.", "secreto": False},
                             {"nombre": "TUTELAS-MICROSOFT-USERNAME", "uso": "Buzón que atiende el job; por defecto notificacionesjudiciales@positiva.gov.co.", "secreto": False},
                             {"nombre": "TUTELAS-MICROSOFT-CLIENT-SECRET", "uso": "Secreto de la aplicación de Graph (Secret Manager).", "secreto": True},
                             {"nombre": "TUTELAS-SGDEA-CLIENT-SECRET", "uso": "Cliente de SGDEA (Secret Manager). El visor no lo usa.", "secreto": True},
                             {"nombre": "TUTELAS-REDIS-PASSWORD", "uso": "Redis de deduplicación (Secret Manager). El visor no lo usa.", "secreto": True}]}
json.dump(cat, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("nodos", len(nodos), "aristas", len(aristas))
