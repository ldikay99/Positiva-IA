"""Genera catalogo/tutelas.json: las etapas reales de procesar_hilo (motores/tutelas/Tutelas/main.py)."""
import json
import sys

M = "Tutelas/main.py"
N = [
    ("remitente", "Validar remitente judicial", "filtro", "Solo un hilo que provenga de un despacho de la Rama Judicial sigue: el buzón interno aparece en casi todos los hilos por los reenvíos y no autoriza.",
     [("Acepta solo remitentes de los dominios de DOMINIOS_JUDICIALES; el primer correo judicial del hilo es el origen que se reporta a SGDEA.", "buscar_correo_judicial")], "DOMINIO-DE-CORREO-INVALIDO"),
    ("tramite", "Identificar tipo de trámite", "filtro", "Sigue si algún correo del hilo menciona una tutela o notifica una actuación procesal (esta última queda sujeta a encontrar un radicado válido).",
     [("detectar_tutela y detectar_actuacion_judicial, por expresiones regulares, sobre cualquier correo del hilo.", "detectar_tutela"),
      ("Si el expediente llega por enlace (SharePoint) y no hay PDF adjunto, no se radica: la documentación vive detrás del enlace.", "_buscar_enlace_sharepoint")], "NO-ES-TUTELA"),
    ("radicado", "Extraer el radicado de 23 dígitos", "ia", "La IA (Vertex) extrae radicado, juzgado, ciudad y documento del afectado; si falla, se busca por expresión regular en el texto.",
     [("ai.extraer_todo con temperatura 0 y respuesta JSON; luego extraer_radicado_regex.", "procesar_hilo")], "SIN-RADICADO-ENCONTRADO"),
    ("radicado_pdf", "Buscar el radicado en los anexos PDF", "ocr", "Si el correo no trae el radicado, lee los PDF (texto propio, Tesseract) y como último recurso la IA multimodal sobre el PDF; el radicado debe coincidir con el corto del asunto.",
     [("_extraer_texto_pdf (PyMuPDF y Tesseract) y ai.extraer_radicado_de_pdf; _radicado_coincide_con_corto.", "procesar_hilo")], "SIN-RADICADO-ENCONTRADO"),
    ("juzgado", "Ubicar el juzgado", "datos", "Busca el juzgado por el código (12 primeros dígitos del radicado) y, si no, por nombre y ciudad.",
     [("juzgados.buscar_juzgado: por código sin caracteres no numéricos; si no, nombre y municipio.", "buscar_juzgado")], "JUZGADO-NO-ENCONTRADO"),
    ("anexos", "Recopilar los anexos", "documentos", "Reúne los adjuntos de todos los correos del hilo sin las imágenes de firma; exige al menos un PDF.",
     [("obtener_adjuntos y descargar_adjunto de cada correo; descarta las imágenes incrustadas.", "procesar_hilo")], "SIN-DOCUMENTACION-ADJUNTA"),
    ("duplicados", "Verificar si ya fue radicada", "filtro", "Calcula la llave del radicado y de cada documento y las reclama en Redis; si ya estaban, es un duplicado.",
     [("_clave_dedup y _claves_por_documento; redis.reclamar (SET NX). En el visor, Redis va en memoria.", "_clave_dedup")], "RADICADO-DUPLICADO"),
    ("radicar", "Radicar en SGDEA", "integracion", "Genera el PDF del hilo y radica la tutela con los archivos individuales.",
     [("sgdea._generar_pdf_hilo y sgdea.radicar_tutela (POST /tutela) con reintentos; en el visor se anota la carga y no se envía.", "radicar_tutela")], "ERROR-RADICAR"),
    ("trazabilidad", "Registrar trazabilidad", "integracion", "Consulta el radicado creado y registra la trazabilidad.", [("obtener_info_radicado y crear_trazabilidad.", "crear_trazabilidad")], None),
    ("informar", "Informar la radicación", "salida", "Responde al buzón interno con el número de radicado.", [("responder_radicacion_exitosa (en el visor, anotado).", "responder_radicacion_exitosa")], None),
    ("archivar", "Archivar el hilo en TUTELAS", "salida", "Marca el hilo como procesado y lo mueve a la carpeta TUTELAS.", [("redis.marcar_procesado y mover_hilo_a_tutelas (en el visor, anotado).", "mover_hilo_a_tutelas")], None),
]
FASES = [("recepcion", "Recepción", ["remitente", "tramite"]), ("extraccion", "Radicado y juzgado", ["radicado", "radicado_pdf", "juzgado"]),
         ("documentos", "Anexos y duplicados", ["anexos", "duplicados"]), ("radicacion", "Radicación", ["radicar", "trazabilidad", "informar", "archivar"])]

nodos, aristas = [], []
orden = [n[0] for n in N]
for i, (id_, nombre, cat, desc, reglas, etiqueta) in enumerate(N):
    sig = orden[i + 1] if i + 1 < len(orden) else None
    if id_ == "radicado":
        sig = "juzgado"
    nodos.append({"id": id_, "nombre": nombre, "descripcion": desc, "etapa_cronometro": id_, "categoria": cat, "usa_ia": cat == "ia",
                  "modulo": M, "funciones": [r[1] for r in reglas], "siguientes": [x for x in [sig, "radicado_pdf" if id_ == "radicado" else None, "no_radicado" if etiqueta else None] if x],
                  "desenlaces": [{"accion": "manual", "cuando": etiqueta}] if etiqueta else [],
                  "reglas": [{"id": f"{id_}_{j}", "descripcion": r[0], "modulo": M, "funcion": r[1]} for j, r in enumerate(reglas)], "parametros": []})
    if sig:
        aristas.append({"desde": id_, "hacia": sig, "etiqueta": "sigue"})
    if id_ == "radicado":
        aristas.append({"desde": "radicado", "hacia": "radicado_pdf", "etiqueta": "no está en el correo"})
    if etiqueta:
        aristas.append({"desde": id_, "hacia": "no_radicado", "etiqueta": etiqueta})
nodos.append({"id": "no_radicado", "nombre": "No se radica", "descripcion": "Responde al buzón interno con el motivo, etiqueta el hilo y lo devuelve a la bandeja de entrada.",
              "terminal": True, "categoria": "salida", "etapa_cronometro": None, "siguientes": [], "desenlaces": [{"accion": "manual", "cuando": "cualquier etiqueta de operaciones.py"}],
              "reglas": [{"id": "nr_1", "descripcion": "Etiquetas: DOMINIO-DE-CORREO-INVALIDO, NO-ES-TUTELA, CORREO-CON-ENLACE, SIN-RADICADO-ENCONTRADO, SIN-DOCUMENTACION-ADJUNTA, JUZGADO-NO-ENCONTRADO, RADICADO-DUPLICADO, RADICACION-POR-CONFIRMAR, ERROR-RADICAR, ERROR-INESPERADO.", "modulo": "Tutelas/operaciones.py", "funcion": "_mover_hilo_fallido"}], "parametros": []})
cat = {"nodos": nodos, "aristas": aristas, "fases": [{"id": a, "nombre": b, "nodos": c} for a, b, c in FASES],
       "herramientas_agente": [], "desenlaces": [{"accion": "radicado", "descripcion": "Radicado en SGDEA"}, {"accion": "manual", "descripcion": "No se radica: vuelve a la bandeja con su etiqueta"}],
       "proveedores": [{"id": "graph", "nombre": "Microsoft Graph (buzón de tutelas)", "tipo": "buzon"}, {"id": "vertex", "nombre": "Vertex AI (Gemini)", "tipo": "ia"},
                       {"id": "juzgados", "nombre": "Tabla de juzgados", "tipo": "registro_personas"}, {"id": "sgdea", "nombre": "SGDEA · radicación de tutelas", "tipo": "sgdea"},
                       {"id": "redis", "nombre": "Redis (deduplicación)", "tipo": "cache"}],
       "variables_entorno": []}
json.dump(cat, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("nodos", len(nodos), "aristas", len(aristas))
