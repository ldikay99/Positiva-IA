import json, sys, os

AQUI = os.path.dirname(os.path.abspath(__file__))
C = json.load(open(os.path.join(AQUI, "constantes.json"), encoding="utf-8"))
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "motor"))
SALIDA = os.path.join(AQUI, "..", "catalogo.json")
try:
    ORIGEN = open(os.path.join(ROOT, "ORIGEN"), encoding="utf-8").read().strip()
except OSError:
    ORIGEN = "desconocido"
TOPE = 3000

# ───────────────────────── NODOS ─────────────────────────

NODOS = [
 {"id": "lote", "nombre": "Lote del Job",
  "descripcion": "Cada corrida (cada 30 min) lee hasta N correos de la bandeja y los atiende de a varios a la vez, con un presupuesto de tiempo para no solaparse con la corrida siguiente.",
  "etapa_cronometro": None, "modulo": "correr.py",
  "funciones": ["correr.main", "correr._presupuesto_de_la_corrida", "correr._credenciales_de_vertex", "correr._auditoria", "OutlookService.get_emails", "Presupuesto.agotado"],
  "usa_ia": False, "siguientes": ["deduplicacion"],
  "desenlaces": [
   {"accion": "sin_tomar", "cuando": "Antes de empezar el correo, presupuesto.agotado() es True (quedan < MINIMO_POR_CORREO_S utiles): el correo no se toca, sin marca, y lo toma la corrida siguiente."},
   {"accion": "colgado", "cuando": "orq.procesar(email) supera TOPE_POR_CORREO_S (asyncio.wait_for): se abandona; conserva su marca EN-PROCESO:N y la corrida siguiente lo retoma."}],
  "reglas": [
   {"id": "lote_exige_vertex", "descripcion": "Sin la bandera --vertex la corrida termina sin procesar nada (return 1): Vertex es el unico backend que resuelve campos y se factura.", "modulo": "correr.py", "funcion": "main"},
   {"id": "lote_seco_exige_modo_test", "descripcion": "--seco exige MODO_TEST=true; con --seco el Orquestador recibe outlook=None y radicador=None (no marca, no mueve, no reenvia; radicar devuelve 'DRY-RUN'). La bandeja se lee igual con get_emails.", "modulo": "correr.py", "funcion": "main"},
   {"id": "lote_carpeta", "descripcion": "--carpeta inbox lee settings.OUTLOOK_FOLDER_ID; --carpeta exitosa lee settings.OUTLOOK_FOLDER_RADICACION_EXITOSA.", "modulo": "correr.py", "funcion": "main"},
   {"id": "lote_tamano", "descripcion": "Se piden get_emails(carpeta, limit=--n) (Graph pide $top=limit*3) y se recorta a --n (por defecto CORREOS_POR_CORRIDA).", "modulo": "correr.py", "funcion": "main"},
   {"id": "lote_concurrencia", "descripcion": "asyncio.Semaphore(max(1, CONCURRENCIA)): correos procesados en paralelo dentro de la misma instancia de Orquestador.", "modulo": "correr.py", "funcion": "main.atender"},
   {"id": "lote_de_a_uno_por_remitente", "descripcion": "Orquestador.procesar toma un candado por remitente (direccion en minusculas): dos correos de la misma persona en la misma corrida pasan uno detras de otro, y el segundo ya ve al primero radicado.", "modulo": "pipeline/orquestador.py", "funcion": "Orquestador.procesar"},
   {"id": "lote_uno_por_hilo", "descripcion": "get_emails pide $top=limit*3 ordenado por receivedDateTime asc y se queda con el PRIMER correo de cada conversationId (el mas viejo), hasta limit.", "modulo": "services/outlook.py", "funcion": "OutlookService.get_emails"},
   {"id": "lote_presupuesto", "descripcion": "Presupuesto total = PRESUPUESTO_S (entorno) o CADENCIA_S*FRACCION_DE_LA_VENTANA (1188 s). agotado() = queda() < MINIMO_POR_CORREO_S, con queda() = total - RESERVA_S - transcurrido.", "modulo": "pipeline/presupuesto.py", "funcion": "Presupuesto.agotado"},
   {"id": "lote_tope_por_correo", "descripcion": "Cada correo corre bajo asyncio.wait_for(orq.procesar(email), TOPE_POR_CORREO_S); al vencer se emite un ERROR y el correo queda 'colgado' (no aparece en el informe).", "modulo": "correr.py", "funcion": "main.atender"},
   {"id": "lote_credenciales_vertex", "descripcion": "Credenciales: primero ADC (google.auth.default, scope cloud-platform); si falla, `gcloud auth print-access-token`.", "modulo": "correr.py", "funcion": "_credenciales_de_vertex"},
   {"id": "lote_servicios", "descripcion": "Se construyen: ModeloVertex(proyecto=GCP_PROJECT, ubicacion='global', endpoint_global=True), OutlookService(), RadicacionService(timeout=TIMEOUT_SGDEA_S), SupabaseService() (auditoria; None si falla), ConsultaDePersonas(url_base=MS_SGDEA_BETA_URL). NO se pasa cache= (la capa Redis queda apagada).", "modulo": "correr.py", "funcion": "main"}],
 },
 {"id": "deduplicacion", "nombre": "Deduplicación (capas 1-4)",
  "descripcion": "Antes de gastar nada, comprueba si el correo ya fue radicado: por su etiqueta, por el hilo visto en esta corrida, por el hilo en el buzón y por el asunto.",
  "etapa_cronometro": "deduplicacion", "modulo": "pipeline/duplicados.py",
  "funciones": ["Detector.comprobar", "Detector._preguntar", "OutlookService.hilo_ya_radicado", "OutlookService.buscar_etiqueta_ent_por_asunto", "utils.radicado.etiqueta_de_radicado", "Orquestador._archivar_duplicado"],
  "usa_ia": False, "siguientes": ["reclamo"],
  "desenlaces": [{"accion": "duplicado", "cuando": "Cualquiera de las capas 1-4 lo detecta: se quitan las marcas de proceso y se mueve a OUTLOOK_FOLDER_DUPLICADOS (sin marca propia)."}],
  "reglas": [
   {"id": "dedup_etiqueta_propia", "descripcion": "Capa 1: si alguna categoria del correo tiene forma de radicado (PATRON_RADICADO ^ENT[\\s-]?\\d o prefijo 'RADICADO:'), es duplicado; radicado = esa etiqueta.", "modulo": "utils/radicado.py", "funcion": "etiqueta_de_radicado"},
   {"id": "dedup_hilo_en_corrida", "descripcion": "Capa 2: si el conversation_id ya esta en Detector.vistos (hilos atendidos en ESTA corrida), es duplicado. El hilo se marca como visto pase lo que pase con las consultas.", "modulo": "pipeline/duplicados.py", "funcion": "Detector.comprobar"},
   {"id": "dedup_hilo_en_buzon", "descripcion": "Capa 3: Graph GET /users/{buzon}/messages filtrando por conversationId; si algun correo del hilo tiene etiqueta de radicado, es duplicado. Apagada si dedup_desactivada() (MODO_TEST y no DEDUP_EN_PRUEBAS).", "modulo": "services/outlook.py", "funcion": "OutlookService.hilo_ya_radicado"},
   {"id": "dedup_asunto", "descripcion": "Capa 4: busca correos radicados con el mismo asunto limpio (mismo remitente via $filter y luego $search por asunto, hasta PAGINAS_POR_ASUNTO paginas de RESULTADOS_POR_PAGINA_DE_ASUNTO, CANDIDATOS_POR_ASUNTO candidatos) y exige que sea el mismo caso (textos parecidos >= PARECIDO_DEL_MISMO_TEXTO o remitentes vinculados). Apagada con dedup_desactivada().", "modulo": "services/outlook.py", "funcion": "OutlookService.buscar_etiqueta_ent_por_asunto"},
   {"id": "dedup_degradar_no_bloquear", "descripcion": "Si una capa falla o no esta configurada, el correo se procesa igual y se anota 'deduplicacion degradada: ...' en los avisos (tambien mira `ultimo_error` del servicio).", "modulo": "pipeline/duplicados.py", "funcion": "Detector._preguntar"},
   {"id": "dedup_en_la_corrida", "descripcion": "Antes de las capas de Graph: compara con lo que el MISMO remitente ya radico en esta corrida (Detector.del_lote). Mismo asunto: criterio de la capa 4 (_es_el_mismo_caso). Otro asunto: exige el mismo texto propio (que distinga) y exactamente los mismos documentos (nombre normalizado y tamaño), al menos uno. Evita radicar dos veces el mismo envio que cae en la misma corrida.", "modulo": "pipeline/duplicados.py", "funcion": "Detector._radicado_en_esta_corrida"},
   {"id": "dedup_campanas", "descripcion": "Dos remitentes distintos solo son el mismo caso si uno nombra al otro, o si los dos citan al mismo remitente Y uno de ellos es un buzon de Positiva (un area que reenvia). Dos personas de afuera que responden a la misma campaña masiva no son duplicado una de otra.", "modulo": "pipeline/duplicados.py", "funcion": "estan_vinculados"},
   {"id": "dedup_luego_anexos", "descripcion": "Si las capas 3, 4 o la de la corrida encuentran el radicado, el correo no se da por duplicado todavia: pasa por la comparacion de documentos (Anexos) y despues por la de seguimiento.", "modulo": "pipeline/duplicados.py", "funcion": "Detector._salvo_documentos_nuevos"}],
 },
 {"id": "anexos", "nombre": "Documentos nuevos frente al radicado",
  "descripcion": "Un correo del caso de un radicado que trae documentos que ningún radicado del caso tiene no se archiva como duplicado ni se radica: va a revisión manual con el número del radicado y la lista de documentos.",
  "etapa_cronometro": None, "modulo": "pipeline/anexos.py",
  "funciones": ["Detector._salvo_documentos_nuevos", "OutlookService.documentos_nuevos_frente_al_radicado", "anexos.documentos_nuevos", "anexos.es_documento"],
  "usa_ia": False, "siguientes": ["seguimiento"],
  "desenlaces": [{"accion": "manual", "cuando": "ANEXO_DE_RADICADO: trae al menos un documento que no tiene ningun correo radicado del hilo (ni el que lleva la etiqueta encontrada)."}],
  "reglas": [
   {"id": "anx_solo_de_afuera", "descripcion": "Solo se compara si el correo trae adjuntos y lo escribe alguien de afuera: lo que llega de un buzon de Positiva es un reenvio o la respuesta con su carta.", "modulo": "pipeline/duplicados.py", "funcion": "Detector._salvo_documentos_nuevos"},
   {"id": "anx_conocido", "descripcion": "Un documento ya esta en el radicado si coincide el nombre normalizado (sin '(1)', '[n]', mayusculas) o el tamaño exacto en bytes.", "modulo": "pipeline/anexos.py", "funcion": "documentos_nuevos"},
   {"id": "anx_no_cuentan", "descripcion": "No cuentan como documentos: adjuntos en linea, correos adjuntos (itemAttachment), cartas de la entidad (nombre ENT/SAL + numero), imagenes de firma (image001.png, logo...) e imagenes de menos de TAMANO_MINIMO_DE_IMAGEN bytes.", "modulo": "pipeline/anexos.py", "funcion": "es_documento", "usa": ["TAMANO_MINIMO_DE_IMAGEN"]},
   {"id": "anx_sin_comparar", "descripcion": "Si la comparacion no se puede hacer, el correo queda como duplicado y se anota 'sin comparar documentos': radicar sin saber si el documento es nuevo es como se radica dos veces.", "modulo": "pipeline/duplicados.py", "funcion": "Detector._salvo_documentos_nuevos"},
   {"id": "anx_no_se_radica", "descripcion": "No se radica: SGDEA no deja anexar a un radicado desde el job y un radicado nuevo abriria un segundo numero para el mismo caso. No se archiva: los documentos se perderian.", "modulo": "pipeline/orquestador.py", "funcion": "Orquestador.procesar"}],
 },
 {"id": "seguimiento", "nombre": "Seguimiento o solicitud nueva (IA)",
  "descripcion": "La misma persona escribe en el caso que radicó, sin documentos nuevos y con otro texto. La IA decide si es seguimiento (insiste, reitera, pregunta) o si pide algo distinto, citando la frase. Lo nuevo va a revisión manual; lo demás queda como duplicado.",
  "etapa_cronometro": None, "modulo": "pipeline/seguimiento.py",
  "funciones": ["Orquestador._pide_algo_nuevo", "seguimiento.clasificar", "seguimiento.leer", "seguimiento.texto_sin_citas", "OutlookService.correo_con_etiqueta"],
  "usa_ia": True, "siguientes": ["duplicado"],
  "desenlaces": [{"accion": "manual", "cuando": "POSIBLE_SOLICITUD_NUEVA: la IA dice SOLICITUD_NUEVA y cita una frase que esta en lo que escribio la persona."},
                 {"accion": "duplicado", "cuando": "SEGUIMIENTO, duda, cita que no esta en el texto, o cualquier fallo."}],
  "reglas": [
   {"id": "seg_cuando", "descripcion": "Solo se consulta si: la capa fue la del hilo, la del asunto o la de la corrida; quien escribe es de afuera y es el mismo remitente del radicado; el texto propio tiene al menos LARGO_MINIMO_DEL_TEXTO_PROPIO caracteres; el radicado tiene texto que distinga; y los textos no son identicos (parecido < 0,995).", "modulo": "pipeline/orquestador.py", "funcion": "Orquestador._pide_algo_nuevo", "usa": ["LARGO_MINIMO_DEL_TEXTO_PROPIO"]},
   {"id": "seg_texto_propio", "descripcion": "Del correo nuevo solo va lo que escribio la persona: se corta en 'De:', 'El ... escribio:', 'On ... wrote:', 'Mensaje original', 'Mensaje reenviado' o la linea de Outlook, y se quita el aviso de remitente poco frecuente. Del radicado va el texto completo (hasta LARGO_MAXIMO_DEL_RADICADO), con lo reenviado.", "modulo": "pipeline/seguimiento.py", "funcion": "texto_sin_citas", "usa": ["LARGO_MAXIMO", "LARGO_MAXIMO_DEL_RADICADO"]},
   {"id": "seg_cita", "descripcion": "La respuesta es JSON {veredicto, cita}. SOLICITUD_NUEVA solo vale con una cita de al menos LARGO_MINIMO_DE_LA_CITA caracteres que aparezca en el texto propio. Ante la duda el prompt pide SEGUIMIENTO.", "modulo": "pipeline/seguimiento.py", "funcion": "leer", "usa": ["LARGO_MINIMO_DE_LA_CITA", "SISTEMA"]},
   {"id": "seg_no_radica", "descripcion": "Una solicitud nueva no se radica sola: si la IA se equivoca, un radicado nuevo seria un duplicado en SGDEA. Va a revision manual con el numero del radicado y la frase citada.", "modulo": "pipeline/orquestador.py", "funcion": "Orquestador.procesar"}],
 },
 {"id": "reclamo", "nombre": "Reclamo del correo (EN-PROCESO)",
  "descripcion": "Antes de trabajar, el correo se marca como tomado por esta corrida para que ninguna otra lo procese a la vez; si ya se reclamó demasiadas veces, va a una persona.",
  "etapa_cronometro": None, "modulo": "pipeline/reclamo.py",
  "funciones": ["reclamo.decidir", "reclamo.marca", "Orquestador._marcar", "OutlookService.marcar", "marcas.fusionar"],
  "usa_ia": False, "siguientes": ["enrutamiento"],
  "desenlaces": [
   {"accion": "manual", "cuando": "motivo RECLAMO_AGOTADO: la marca EN-PROCESO:N previa da N+1 > MAX_RECLAMOS (va por _a_revision_manual; RECLAMO_AGOTADO no se aplaza)."},
   {"accion": "en_proceso", "cuando": "Escribir la marca devuelve 412 (If-Match/ETag): otra corrida lo tomo (OtraCorridaLoTomo). No se toca ni se avisa."},
   {"accion": "sin_reclamar", "cuando": "La escritura de la marca falla por otro motivo (ReclamoNoEscrito): el correo queda intacto y la siguiente corrida lo ve como nuevo. No se avisa."}],
  "reglas": [
   {"id": "reclamo_cuenta", "descripcion": "reclamos_previos = mayor N entre categorias 'EN-PROCESO:N'. Sin marca: intento 1. Con marca: intento N+1; si > MAX_RECLAMOS va a manual.", "modulo": "pipeline/reclamo.py", "funcion": "decidir"},
   {"id": "reclamo_obligatorio", "descripcion": "La marca se escribe con obligatoria=True: cualquier fallo corta el correo (en_proceso o sin_reclamar).", "modulo": "pipeline/orquestador.py", "funcion": "Orquestador._marcar"},
   {"id": "marcas_familias", "descripcion": "Graph reemplaza la lista completa de categorias: poner una marca sustituye solo las de su familia (EN-PROCESO:, REINTENTO:, REVISION-MANUAL:, REENVIADO:, RADICADO/ENT) y conserva las categorias ajenas puestas por personas. Al cerrar (quitar_de_proceso) se retiran EN-PROCESO y REINTENTO.", "modulo": "pipeline/marcas.py", "funcion": "fusionar"},
   {"id": "reclamo_etag", "descripcion": "OutlookService.marcar lee categorias+ETag y hace PATCH con If-Match; un 412 se traduce a OtraCorridaLoTomo.", "modulo": "services/outlook.py", "funcion": "OutlookService.marcar"}],
 },
 {"id": "enrutamiento", "nombre": "Enrutamiento por contenido",
  "descripcion": "Limpia el HTML del cuerpo y decide si el correo es una PQRD que se radica, o si corresponde a Notificaciones Judiciales o a Medicina Laboral, solo con señales escritas.",
  "etapa_cronometro": "enrutamiento", "modulo": "pipeline/enrutamiento.py",
  "funciones": ["utils.html_cleaner.clean_html_content", "enrutamiento.decidir", "enrutamiento._decidir_area", "enrutamiento._judicial", "enrutamiento._ente_de_control", "enrutamiento._seria_un_bucle", "enrutamiento._sin_buzones"],
  "usa_ia": False, "siguientes": ["facturas"],
  "desenlaces": [],
  "reglas": [
   {"id": "enr_remitente_excluido", "descripcion": "Si el remitente (direccion completa, minusculas) esta en REMITENTES_QUE_NO_SE_RADICAN -> destino NO_SE_RADICA.", "modulo": "pipeline/enrutamiento.py", "funcion": "decidir"},
   {"id": "enr_sin_buzones", "descripcion": "Antes de buscar señales se retiran del asunto y cuerpo los encabezados citados (De:...Asunto:), las listas de destinatarios (Para/CC/...) y las direcciones de correo; se normaliza sin acentos y se miran solo los primeros TOPE_DEL_CUERPO caracteres del cuerpo.", "modulo": "pipeline/enrutamiento.py", "funcion": "_sin_buzones"},
   {"id": "enr_derecho_de_peticion", "descripcion": "Si dice 'derecho de peticion' o 'articulo 23 de la constitucion' y la Rama NO esta actuando (_LA_RAMA_ACTUA), se radica (Servicio al Cliente) sin mirar mas.", "modulo": "pipeline/enrutamiento.py", "funcion": "_decidir_area"},
   {"id": "enr_judicial", "descripcion": "Judicial si alguna JUDICIAL_KEYWORD_PATTERNS (con \\b delante) aparece en asunto o cuerpo Y (el remitente es de un dominio de ente/Rama, o la Rama actua, o NO es alguien pidiendo cumplimiento de un fallo [_PIDE_SOBRE_LO_JUDICIAL]); o, sin palabra clave, si _LA_RAMA_ACTUA casa. Destino DESTINO_NOTIFICACIONES_JUDICIALES.", "modulo": "pipeline/enrutamiento.py", "funcion": "_judicial"},
   {"id": "enr_ente_de_control", "descripcion": "Ente de control -> judicial si: el remitente es de _DOMINIO_DE_ENTE; o el ASUNTO nombra un ente (_ENTE_EN_EL_ASUNTO); o el texto nombra desacato/proceso ordinario laboral (_JUDICIAL_EXTRA); o el cuerpo presenta un requerimiento del ente (_ENTE_QUE_REQUIERE).", "modulo": "pipeline/enrutamiento.py", "funcion": "_ente_de_control"},
   {"id": "enr_digitalizacion", "descripcion": "Remitente que termina en DOMINIO_DE_DIGITALIZACION (@e-entrega.co) -> Medicina Laboral.", "modulo": "pipeline/enrutamiento.py", "funcion": "_decidir_area"},
   {"id": "enr_junta", "descripcion": "Si nombra la Junta de Calificacion (_JUNTA) -> Medicina Laboral, SALVO que quien escribe pida algo sobre su propio tramite (_PIDE_SOBRE_LA_JUNTA) y la Junta no este actuando (_LA_JUNTA_ACTUA).", "modulo": "pipeline/enrutamiento.py", "funcion": "_decidir_area"},
   {"id": "enr_orden", "descripcion": "Orden de evaluacion: remitente excluido > derecho de peticion > judicial > ente de control > dominio de digitalizacion > Junta > radicar.", "modulo": "pipeline/enrutamiento.py", "funcion": "decidir"},
   {"id": "enr_no_bucle", "descripcion": "Si el reenvio volveria al mismo buzon que lo escribio (remitente == correo_destino), se radica en vez de devolverselo.", "modulo": "pipeline/enrutamiento.py", "funcion": "_seria_un_bucle"}],
 },
 {"id": "facturas", "nombre": "Filtro de facturas electrónicas",
  "descripcion": "Una factura electrónica no es una PQRD: si el asunto tiene la forma con que se emite una factura o el cuerpo trae marcas de la DIAN, va a revisión manual sin abrir adjuntos.",
  "etapa_cronometro": None, "modulo": "pipeline/facturas.py", "funciones": ["facturas.es_factura"],
  "usa_ia": False, "siguientes": ["destino_del_correo"],
  "desenlaces": [{"accion": "manual", "cuando": "motivo FACTURA_ELECTRONICA (se evalua ANTES de actuar sobre la ruta: una factura no se reenvia)."}],
  "reglas": [
   {"id": "fact_asunto", "descripcion": "Asunto con forma NIT;RAZON SOCIAL;NUMERO;TIPO (_ASUNTO_DE_FACTURA).", "modulo": "pipeline/facturas.py", "funcion": "es_factura"},
   {"id": "fact_cufe", "descripcion": "En los primeros TOPE_DEL_CUERPO caracteres: 'CUFE:'/'CUDE:' (_CODIGO_DE_LA_DIAN) o 'factura electronica de venta', 'nota credito/debito electronica', 'representacion grafica de la factura' (_DOCUMENTO_DE_LA_DIAN).", "modulo": "pipeline/facturas.py", "funcion": "es_factura"}],
 },
 {"id": "destino_del_correo", "nombre": "Aplicar destino (excluir / reenviar)",
  "descripcion": "Actúa sobre la decisión de enrutamiento: los remitentes excluidos van a una persona y los correos de otra área se reenvían a su buzón y se archivan.",
  "etapa_cronometro": None, "modulo": "pipeline/orquestador.py",
  "funciones": ["Orquestador._reenviar", "OutlookService.forward_email", "notificacion.encabezado_de_reenvio", "Orquestador._archivar"],
  "usa_ia": False, "siguientes": ["remitente_identificable"],
  "desenlaces": [
   {"accion": "manual", "cuando": "Destino NO_SE_RADICA: motivo REMITENTE_EXCLUIDO (no se reenvia para que no rebote)."},
   {"accion": "reenviado", "cuando": "Destino JUDICIAL o MEDICINA_LABORAL: forward_email(id, correo_destino, encabezado) y luego marca REENVIADO:<destino> + mover a OUTLOOK_FOLDER_REENVIADO_OTRA_AREA. Si el forward falla, la accion sigue siendo 'reenviado' pero el correo NO se archiva (se queda en la bandeja y la siguiente corrida reintenta)."}],
  "reglas": [
   {"id": "dest_reenvio_sin_archivo_si_falla", "descripcion": "Si forward_email no existe o lanza, se anota el aviso y NO se mueve el correo.", "modulo": "pipeline/orquestador.py", "funcion": "Orquestador._reenviar"}],
 },
 {"id": "remitente_identificable", "nombre": "Remitente externo identificable",
  "descripcion": "Si quien escribe es un buzón de Positiva y en el hilo no aparece ninguna persona externa, no hay de quién sea la PQRD y va a revisión manual.",
  "etapa_cronometro": None, "modulo": "utils/generic_filter.py",
  "funciones": ["generic_filter.es_buzon_de_positiva", "generic_filter.tiene_remitente_externo_identificable", "generic_filter.is_buzon_no_humano"],
  "usa_ia": False, "siguientes": ["descarga_adjuntos"],
  "desenlaces": [{"accion": "manual", "cuando": "motivo REMITENTE_NO_IDENTIFICABLE: From en dominio interno (_INTERNAL_DOMAINS) y ni el From ni ninguna direccion del cuerpo es un correo externo valido."}],
  "reglas": [
   {"id": "rem_dominio_interno", "descripcion": "es_buzon_de_positiva: la direccion termina en @positiva.gov.co o @positivaseguros.com.co.", "modulo": "utils/generic_filter.py", "funcion": "es_buzon_de_positiva"},
   {"id": "rem_externo_valido", "descripcion": "Correo externo valido = no interno y su parte local no contiene ninguna de _NON_HUMAN_LOCAL_PARTS (noreply, notificaciones, postmaster...). Se buscan con _EMAIL_REGEX en el cuerpo limpio.", "modulo": "utils/generic_filter.py", "funcion": "tiene_remitente_externo_identificable"}],
 },
 {"id": "descarga_adjuntos", "nombre": "Descarga de adjuntos",
  "descripcion": "Descarga de Outlook los adjuntos del correo, descartando las imágenes en línea pequeñas (firmas).",
  "etapa_cronometro": "descarga_adjuntos", "modulo": "pipeline/orquestador.py",
  "funciones": ["Orquestador._descargar_adjuntos", "OutlookService.get_attachments", "OutlookService.download_attachment"],
  "usa_ia": False, "siguientes": ["volumen"],
  "desenlaces": [],
  "reglas": [
   {"id": "adj_sin_servicio", "descripcion": "Sin outlook o con email.has_attachments False devuelve lista vacia.", "modulo": "pipeline/orquestador.py", "funcion": "_descargar_adjuntos"},
   {"id": "adj_inline", "descripcion": "Se omiten los adjuntos con isInline=True y size < 20_000 bytes (literal).", "modulo": "pipeline/orquestador.py", "funcion": "_descargar_adjuntos"},
   {"id": "adj_fallo_bitacora", "descripcion": "Un fallo al listar o descargar se registra en la bitacora con paso 'ADJUNTOS' (clasificado por pipeline.errores; uno sin firma reconocida es DESCONOCIDO/FATAL y mas tarde impide radicar con ERROR_INESPERADO, que se aplaza).", "modulo": "pipeline/orquestador.py", "funcion": "_descargar_adjuntos"}],
 },
 {"id": "volumen", "nombre": "Control de volumen",
  "descripcion": "Si el correo trae demasiados adjuntos, páginas o megas, no se procesa automáticamente: va a una persona antes de pagar OCR o modelo.",
  "etapa_cronometro": "volumen", "modulo": "pipeline/volumen.py", "funciones": ["volumen.medir", "volumen._paginas_de", "materializar.es_ruido"],
  "usa_ia": False, "siguientes": ["deduplicacion_contenido"],
  "desenlaces": [{"accion": "manual", "cuando": "motivo VOLUMEN_EXCESIVO: adjuntos > MAX_ADJUNTOS, o paginas > MAX_PAGINAS, o MB > MAX_MEGABYTES (no se aplaza)."}],
  "reglas": [
   {"id": "vol_ruido", "descripcion": "No cuentan los adjuntos vacios ni los decorativos (nombre con outlook/signature/firma/logo/spacer/pixel o imageNNN).", "modulo": "pipeline/materializar.py", "funcion": "es_ruido"},
   {"id": "vol_paginas", "descripcion": "Paginas: imagen = 1; PDF = page_count (tope LIMITE_PAGINAS_QUE_SE_CUENTAN; PDF protegido o ilegible = 0); otros = 0.", "modulo": "pipeline/volumen.py", "funcion": "_paginas_de"}],
 },
 {"id": "deduplicacion_contenido", "nombre": "Deduplicación por contenido (capas 5-6)",
  "descripcion": "Con los nombres de los adjuntos ya conocidos, comprueba el hash del contenido (cache) y el par remitente+asunto en la auditoría histórica de Supabase.",
  "etapa_cronometro": "deduplicacion", "modulo": "pipeline/duplicados.py",
  "funciones": ["Detector.comprobar_contenido", "duplicados.hash_del_correo", "SupabaseService.email_exists", "utils.hash.limpiar_asunto"],
  "usa_ia": False, "siguientes": ["conversion_a_markdown"],
  "desenlaces": [{"accion": "duplicado", "cuando": "El hash ya esta en cache (capa 5, inactiva en el Job: cache=None) o la auditoria tiene un registro con el mismo remitente y asunto limpio (capa 6)."}],
  "reglas": [
   {"id": "dedup_hash", "descripcion": "Capa 5: hash = generate_email_hash(asunto, remitente, '|'.join(sorted(nombres_adjuntos))). Solo si hay cache; correr.py no la pasa, asi que en el Job no corre.", "modulo": "pipeline/duplicados.py", "funcion": "comprobar_contenido"},
   {"id": "dedup_auditoria", "descripcion": "Capa 6: SupabaseService.email_exists(remitente, limpiar_asunto(asunto), recibido) sobre la tabla radicados_pqrd con piso de fecha = recibido - MARGEN_DE_ZONA_HORARIA. Solo si la capa 5 no respondio. Apagada con dedup_desactivada().", "modulo": "services/supabase_client.py", "funcion": "SupabaseService.email_exists"},
   {"id": "dedup_cronometro_compartido", "descripcion": "Usa la misma etapa 'deduplicacion' del cronometro: etapas_ms['deduplicacion'] acumula las dos pasadas y la traza se emite dos veces con esa etapa.", "modulo": "pipeline/orquestador.py", "funcion": "Orquestador.procesar"}],
 },
 {"id": "conversion_a_markdown", "nombre": "Conversión a Markdown y OCR",
  "descripcion": "Convierte el cuerpo y cada adjunto (PDF, imagen, Word, Excel, texto, ZIP, P7M) a Markdown hoja por hoja. La hoja con texto propio se toma tal cual; la escaneada se lee con Tesseract a 300 ppp sin las líneas de tabla, con su confianza, y si sale floja se mira si está de lado y se relee girada; si la lectura se corta, se relee a 200 ppp.",
  "etapa_cronometro": "conversion_a_markdown", "modulo": "pipeline/materializar.py",
  "funciones": ["materializar.materializar", "materializar.materializar_adjuntos", "conversion.convertir", "conversion.detectar_tipo", "conversion.expandir_zip", "conversion._pdf_a_markdown", "conversion._imagen_a_markdown", "conversion._ocr_de_imagenes", "conversion._ocr_de_pagina", "conversion._texto_de_imagen_tesseract", "conversion._giro_probable", "conversion._sin_cuadricula", "Workspace.crear"],
  "usa_ia": False, "siguientes": ["saneamiento", "transcripcion_vision", "indice_por_hoja"],
  "desenlaces": [],
  "reglas": [
   {"id": "conv_hilo", "descripcion": "Corre en asyncio.to_thread para no bloquear el lote.", "modulo": "pipeline/orquestador.py", "funcion": "procesar"},
   {"id": "conv_ocr_300", "descripcion": "Una hoja sin texto propio se renderiza a DPI_MINIMO_OCR..DPI_MAXIMO_OCR (300 ppp), se le borran las lineas de tabla (_sin_cuadricula) y se lee con tesseract (IDIOMAS_OCR='spa', --oem 1 --psm 3) con confianza 0-1 ponderada por palabra. La hoja de PDF no se 'prepara' (enderezar o recortar le borra trazos a los digitos); la foto suelta si.", "modulo": "pipeline/conversion.py", "funcion": "_ocr_de_pagina", "usa": ["DPI_MINIMO_OCR", "DPI_MAXIMO_OCR", "IDIOMAS_OCR"]},
   {"id": "conv_giro", "descripcion": "Solo si la lectura sale floja (confianza < CONFIANZA_REINTENTO_OSD o menos de MINIMO_CARACTERES_OCR caracteres utiles) se pregunta al OSD de tesseract si la hoja esta de lado (seguridad >= CONFIANZA_MINIMA_OSD); si duda, se sondea cada giro en una miniatura de LADO_SONDEO_GIRO_PX y gana el que mejore la confianza en MEJORA_MINIMA_POR_GIRO. La lectura girada solo se queda si supera a la primera.", "modulo": "pipeline/conversion.py", "funcion": "_giro_probable", "usa": ["CONFIANZA_REINTENTO_OSD", "MINIMO_CARACTERES_OCR", "CONFIANZA_MINIMA_OSD", "LADO_SONDEO_GIRO_PX", "MEJORA_MINIMA_POR_GIRO"]},
   {"id": "conv_relectura_200", "descripcion": "La lectura a 300 ppp tiene FRACCION_DEL_TOPE_A_300_DPI del tope de la hoja (TIMEOUT_OCR_PAGINA_S); si se corta sin texto, la hoja se relee a DPI_DE_RESPALDO_OCR (200 ppp) con lo que quede del tope.", "modulo": "pipeline/conversion.py", "funcion": "_ocr_de_pagina", "usa": ["FRACCION_DEL_TOPE_A_300_DPI", "DPI_DE_RESPALDO_OCR", "TIMEOUT_OCR_PAGINA_S"]},
   {"id": "conv_confianza_por_hoja", "descripcion": "Cada hoja leida por OCR lleva su confianza en el encabezado ('## Pagina N (OCR, confianza 0.82)'); por debajo de CONFIANZA_DUDOSA se marca 'lectura dudosa: verificar los datos'.", "modulo": "pipeline/conversion.py", "funcion": "_marca_ocr", "usa": ["CONFIANZA_DUDOSA"]},
   {"id": "conv_descartes", "descripcion": "Se descartan: decorativos (es_ruido), vacios, contenido repetido (sha256) y adjuntos > LIMITE_BYTES_ADJUNTO.", "modulo": "pipeline/materializar.py", "funcion": "materializar_adjuntos"},
   {"id": "conv_zip", "descripcion": "Un ZIP se expande un nivel (un ZIP dentro de otro no se vuelve a expandir).", "modulo": "pipeline/materializar.py", "funcion": "materializar_adjuntos"},
   {"id": "conv_claves_pdf", "descripcion": "Para PDF protegidos se prueban como clave hasta 12 numeros de 6-12 digitos del asunto/cuerpo.", "modulo": "pipeline/materializar.py", "funcion": "claves_candidatas_de"},
   {"id": "conv_ocr_presupuesto", "descripcion": "OCR por documento hasta PRESUPUESTO_OCR_SEGUNDOS, por correo PRESUPUESTO_OCR_POR_CORREO_S compartido, y por pagina/imagen TIMEOUT_OCR_PAGINA_S. Solo PDF hasta LIMITE_PAGINAS_PDF paginas, texto hasta LIMITE_CARACTERES, hojas hasta LIMITE_FILAS_HOJA filas.", "modulo": "pipeline/conversion.py", "funcion": "_pdf_a_markdown"},
   {"id": "conv_imagenes_incrustadas", "descripcion": "Imagenes dentro de un PDF solo se reconocen si su lado menor >= LADO_MINIMO_DE_IMAGEN_PX y ocupan entre FRACCION_MINIMA_DE_PAGINA y FRACCION_MAXIMA_DE_PAGINA de la pagina, maximo MAX_IMAGENES_POR_PAGINA por pagina.", "modulo": "pipeline/conversion.py", "funcion": "_ocr_de_imagenes"},
   {"id": "conv_ilegible", "descripcion": "Un adjunto cuyas paginas no dan texto queda como fallido (ESTADO_ERROR) y se registra en la bitacora con paso 'CONVERSION' (cuerpo=detalle; ADJUNTO_ILEGIBLE/ADJUNTO_PROTEGIDO/SIN_OCR si casa una firma; si no, DESCONOCIDO/FATAL).", "modulo": "pipeline/orquestador.py", "funcion": "procesar"},
   {"id": "conv_workspace", "descripcion": "Workspace en base_workspace/<id seguro> (temporal=False en el Job, queda en disco: correo.md, adjuntos/*.md, manifiesto); si base es None, temporal del sistema y se borra al cerrar.", "modulo": "pipeline/workspace.py", "funcion": "Workspace.crear"}],
 },
 {"id": "saneamiento", "nombre": "Detección de inyección",
  "descripcion": "Busca en el cuerpo y los adjuntos intentos de manipular al modelo (órdenes, cambios de rol, marcadores, texto oculto). No borra nada: solo avisa y marca.",
  "etapa_cronometro": "saneamiento", "modulo": "pipeline/saneamiento.py",
  "funciones": ["saneamiento.detectar", "saneamiento.anotar", "saneamiento.envolver", "saneamiento.sanear_fragmento"],
  "usa_ia": False, "siguientes": ["extraccion_determinista"],
  "desenlaces": [],
  "reglas": [
   {"id": "san_aviso_alta", "descripcion": "Si hay sospechas de severidad ALTA se agrega el aviso 'inyeccion detectada: <categorias>'. No bloquea ni cambia el desenlace.", "modulo": "pipeline/orquestador.py", "funcion": "procesar"},
   {"id": "san_herramientas", "descripcion": "leer_documento y buscar_en_documentos pasan el texto por sanear_fragmento: anota [!] CONTENIDO SOSPECHOSO y lo envuelve en un bloque delimitado con marca aleatoria antes de darselo al modelo.", "modulo": "servidor_mcp/herramientas.py", "funcion": "leer_documento"}],
 },
 {"id": "extraccion_determinista", "nombre": "Extracción determinista",
  "descripcion": "Algoritmos y expresiones regulares leen los datos del caso (documento, nombres, tipo de remitente, solicitud, sexo, teléfono, dirección, geografía) con evidencia y confianza; lo dudoso queda pendiente para la IA.",
  "etapa_cronometro": "extraccion_determinista", "modulo": "pipeline/extraccion.py",
  "funciones": ["extraccion.extraer_todo", "extraer_nombre_afectado", "extraer_documento", "extraer_tipo_documento", "extraer_telefono", "extraer_nombre_remitente", "extraer_sexo", "extraer_tipo_pqrd", "extraer_documento_del_remitente", "extraer_tipo_remitente", "extraer_tipo_solicitud", "extraer_direccion", "extraer_geografia"],
  "usa_ia": False, "siguientes": ["agente"],
  "desenlaces": [],
  "reglas": [
   {"id": "ext_umbral", "descripcion": "Un hallazgo con confianza < UMBRAL_CONFIANZA (0.70) no se acepta: pasa a no_resueltos con motivo CONFIANZA_INSUFICIENTE y el candidato en el detalle.", "modulo": "pipeline/extraccion.py", "funcion": "extraer_todo.registrar"},
   {"id": "ext_orden", "descripcion": "Orden: nombre_afectado -> numero_documento_afectado (desambigua con el nombre) -> tipo_documento_afectado -> telefono -> nombre_remitente -> sexo -> tipo_pqrd -> documento/tipo del remitente -> tipo_remitente -> tipo_solicitud -> direccion -> geografia (departamento/municipio).", "modulo": "pipeline/extraccion.py", "funcion": "extraer_todo"},
   {"id": "ext_correcciones", "descripcion": "La identidad del afectado se busca sobre el texto sin las clausulas que piden anular/corregir un dato (_PIDE_CORREGIR).", "modulo": "pipeline/extraccion.py", "funcion": "_sin_lo_que_se_pide_corregir"},
   {"id": "ext_voz", "descripcion": "La voz del tramite (primera persona propia vs. de otro) se lee solo en el texto nuevo del remitente; si es propia y no de otro, el remitente es el afectado.", "modulo": "pipeline/extraccion.py", "funcion": "voces_del_tramite"},
   {"id": "ext_tipo_pqrd_fijo", "descripcion": "tipo_pqrd siempre 1 (Solicitud), confianza 1.0: es el unico tipo que radica este canal.", "modulo": "pipeline/extraccion.py", "funcion": "extraer_tipo_pqrd"},
   {"id": "ext_tipo_solicitud", "descripcion": "tipo_solicitud solo si el asunto + primeros TOPE_PARA_LA_SOLICITUD caracteres nombran un tramite de _TIPO_SOLICITUD_DECLARADO (gana el primero; confianza 0.85); si no, REQUIERE_CLASIFICACION_SEMANTICA (lo resuelve el agente).", "modulo": "pipeline/extraccion.py", "funcion": "extraer_tipo_solicitud"},
   {"id": "ext_ausencia_constatada", "descripcion": "Los pendientes con motivo en MOTIVOS_DE_AUSENCIA_CONSTATADA (SIN_TELEFONO, SIN_NINGUN_NUMERO_EN_EL_TEXTO) no se le piden al agente.", "modulo": "pipeline/extraccion.py", "funcion": "Extraccion.campos_a_buscar"},
   {"id": "ext_a_confirmar", "descripcion": "Los hallazgos con a_confirmar se le dan al agente para confirmar o sustituir.", "modulo": "pipeline/extraccion.py", "funcion": "Extraccion.campos_a_confirmar"},
   {"id": "ext_contexto", "descripcion": "El contexto que se le pasa al agente es 'Asunto: ...' + las primeras 14 lineas no vacias del cuerpo, maximo 1800 caracteres (literales).", "modulo": "pipeline/extraccion.py", "funcion": "extraer_todo"}],
 },
 {"id": "transcripcion_vision", "nombre": "Transcripción con visión",
  "descripcion": "Lo que Tesseract deja flojo (una foto de celular sobre fondo oscuro, un rótulo diminuto) se le pasa al mismo modelo Gemini con visión, que solo transcribe el texto visible, sin interpretar. Hay un tope de imágenes por correo y lo transcrito queda marcado como lectura de un modelo.",
  "etapa_cronometro": None, "modulo": "pipeline/conversion.py",
  "funciones": ["conversion._con_respaldo_ia", "conversion._texto_de_imagen", "ModeloVertex.transcribir_imagen"],
  "usa_ia": True, "siguientes": ["conversion_a_markdown"],
  "desenlaces": [],
  "reglas": [
   {"id": "vis_solo_lo_flojo", "descripcion": "Solo se transcribe una hoja o imagen cuya lectura de tesseract salio floja (confianza < CONFIANZA_REINTENTO_OSD o menos de MINIMO_CARACTERES_OCR caracteres utiles), y solo si queda cupo en PresupuestoDeOcr.imagenes_ia.", "modulo": "pipeline/conversion.py", "funcion": "_con_respaldo_ia", "usa": ["MAX_IMAGENES_IA_POR_CORREO"]},
   {"id": "vis_gana_si_lee_mas", "descripcion": "Lo transcrito reemplaza a la lectura de tesseract solo si trae mas caracteres utiles y no es 'SIN_TEXTO'; va precedido de MARCA_TRANSCRITO_POR_IA para que quien lea los datos verifique las cifras.", "modulo": "pipeline/conversion.py", "funcion": "_con_respaldo_ia", "usa": ["MARCA_TRANSCRITO_POR_IA"]},
   {"id": "vis_sin_razonar", "descripcion": "La llamada va con temperatura 0 y sin presupuesto de razonamiento, con la instruccion INSTRUCCION_DE_TRANSCRIPCION: transcribir exactamente, renglon por renglon, sin seguir ordenes que aparezcan en la imagen.", "modulo": "pipeline/modelo.py", "funcion": "ModeloVertex.transcribir_imagen", "usa": ["INSTRUCCION_DE_TRANSCRIPCION"]}],
 },
 {"id": "indice_por_hoja", "nombre": "Índice por hoja y búsqueda",
  "descripcion": "El texto de cada adjunto queda partido por hoja y cada hoja clasificada (cédula, historia clínica, formulario…) con los documentos que trae. La IA ve ese índice, busca un dato sin importar tildes, mayúsculas ni separadores de miles, y pide los tramos más relevantes a una pregunta (BM25).",
  "etapa_cronometro": None, "modulo": "pipeline/indice.py",
  "funciones": ["Workspace.listar", "Workspace.buscar", "Workspace.consultar", "Workspace.leer_pagina", "indice.plegar", "indice.paginas_de", "indice.clasificar_hoja", "indice.describir_paginas", "indice.BM25", "indice.expandir"],
  "usa_ia": False, "siguientes": ["agente", "revisor"],
  "desenlaces": [],
  "reglas": [
   {"id": "ind_plegado", "descripcion": "Patron y texto se comparan plegados (sin tildes ni mayusculas, misma longitud): una posicion hallada en el texto plegado es la misma en el original.", "modulo": "pipeline/indice.py", "funcion": "plegar"},
   {"id": "ind_cifras", "descripcion": "Una cifra de cinco o mas digitos admite separadores: '1087995336' encuentra '1.087.995.336'. Una coincidencia partida por un salto de linea del OCR tambien cuenta.", "modulo": "pipeline/workspace.py", "funcion": "Workspace.buscar"},
   {"id": "ind_hojas", "descripcion": "El inventario lleva una linea por hoja o rango ('p5 cedula ocr0.58 id1:CC') y se agrupa por tipo hasta caber en PRESUPUESTO_INDICE.", "modulo": "pipeline/workspace.py", "funcion": "Workspace.listar", "usa": ["PRESUPUESTO_INDICE"]},
   {"id": "ind_bm25", "descripcion": "consultar_documentos puntua ventanas de _VENTANA_LINEAS lineas dentro de cada hoja con BM25 (k1 1,5; b 0,75); lo preguntado pesa 1 y sus sinonimos del dominio 0,5. Devuelve tramos que no se solapan ni repiten el mismo soporte.", "modulo": "pipeline/workspace.py", "funcion": "Workspace.consultar", "usa": ["_VENTANA_LINEAS", "TRAMOS_POR_CONSULTA", "MAX_CARACTERES_CONSULTA"]}],
 },
 {"id": "agente", "nombre": "Agente IA (Vertex Gemini)",
  "descripcion": "Solo si quedan campos pendientes o por confirmar, un modelo Gemini con herramientas de lectura del expediente los resuelve, citando evidencia y confianza; se reintenta si el modelo falla por causas pasajeras.",
  "etapa_cronometro": "agente", "modulo": "pipeline/agente.py",
  "funciones": ["Orquestador._resolver_con_reintento", "agente.resolver", "agente.prompt_de_sistema", "agente.catalogos_de", "agente._compactar", "agente._valor_normalizado", "ModeloVertex.generar", "servidor_mcp.herramientas.invocar"],
  "usa_ia": True, "siguientes": ["combinacion"],
  "desenlaces": [
   {"accion": "manual", "cuando": "FalloDelModelo no reintentable o reintentos agotados: motivo de la bitacora para paso MODELO (p. ej. MODELO_NO_RESPONDIO, MODELO_SIN_PERMISO) -> casi siempre se aplaza (ver revision_manual)."},
   {"accion": "aplazado", "cuando": "Fallo del modelo con la corrida sin tiempo: motivo TIEMPO_DE_CORRIDA_AGOTADO (se aplaza), o MODELO_NO_RESPONDIO/MODELO_SIN_PERMISO/ERROR_INESPERADO mientras REINTENTO:N < MAX_CICLOS."}],
  "reglas": [
   {"id": "ag_no_llamar", "descripcion": "Si campos_para_el_agente esta vacio no se llama al modelo (0 vueltas).", "modulo": "pipeline/agente.py", "funcion": "resolver"},
   {"id": "ag_prompt", "descripcion": "Prompt de sistema = CABECERA + 'CRITERIOS POR CAMPO' + solo los criterios de los campos pedidos; la tarea incluye resumen_para_ia() y los catalogos de los campos-codigo pedidos.", "modulo": "pipeline/agente.py", "funcion": "prompt_de_sistema"},
   {"id": "ag_vueltas", "descripcion": "Maximo MAX_ITERACIONES vueltas de trabajo (cada vuelta = 1 llamada a generar). Las correcciones por escribir la invocacion en texto (hasta MAX_CORRECCIONES_DE_FORMATO) no cuentan como vuelta.", "modulo": "pipeline/agente.py", "funcion": "resolver"},
   {"id": "ag_historial", "descripcion": "Se envia la tarea + las ultimas VENTANA_HISTORIAL*2 entradas del historial; las respuestas de herramienta que no son la ultima se recortan a MAX_CARACTERES_HERRAMIENTA_VIEJA; cada respuesta de herramienta se corta a MAX_CARACTERES_RESPUESTA_HERRAMIENTA.", "modulo": "pipeline/agente.py", "funcion": "_compactar"},
   {"id": "ag_aviso_vueltas", "descripcion": "Cuando iteracion >= max_iteraciones - VUELTAS_DE_AVISO y faltan campos, se le pide cerrar. Si ya no falta nada y vuelve a pedir herramientas mas de TURNOS_TRAS_AVISAR_QUE_NO_FALTA_NADA veces, se corta.", "modulo": "pipeline/agente.py", "funcion": "resolver"},
   {"id": "ag_filtro_confianza", "descripcion": "Un hallazgo del agente se descarta si: el campo ya estaba resuelto y no era a confirmar; confianza < confianza_exigida(campo) (PISO_POR_CAMPO o CONFIANZA_MINIMA=0.60; tipo_solicitud piso 0.0); o el codigo no es numero / no esta en el catalogo. Si la confianza esta entre el piso del campo y 0.60 se acepta con aviso.", "modulo": "pipeline/agente.py", "funcion": "resolver"},
   {"id": "ag_contradice_ausencia", "descripcion": "Si el agente reporta un campo cuyo motivo era de ausencia constatada, se acepta con aviso.", "modulo": "pipeline/agente.py", "funcion": "resolver"},
   {"id": "ag_explicacion", "descripcion": "El texto final ('LISTO' o una frase) se guarda como explicacion (max LARGO_DE_LA_EXPLICACION) y va al aviso si el desenlace es manual por contenido.", "modulo": "pipeline/agente.py", "funcion": "_explicacion_de_cierre"},
   {"id": "ag_reintento", "descripcion": "Cualquier excepcion del agente se clasifica (pipeline.errores.clasificar); si debe_reintentar(clase, intento) y queda presupuesto, espera espera_para(clase, intento) recortada por presupuesto.recortar y repite. Sin tiempo: FalloDelModelo('TIEMPO_DE_CORRIDA_AGOTADO'); si no: FalloDelModelo(motivo de la bitacora o 'MODELO_NO_RESPONDIO').", "modulo": "pipeline/orquestador.py", "funcion": "_resolver_con_reintento"},
   {"id": "ag_modelo", "descripcion": "ModeloVertex.generar: genai.Client(vertexai=True, project=GCP_PROJECT, location='global', base_url=https://aiplatform.googleapis.com, timeout=TIMEOUT_DEL_MODELO_MS) y generate_content(model='gemini-2.5-flash', temperature=0.1, system_instruction=sistema, tools=function_declarations). Corre en asyncio.to_thread.", "modulo": "pipeline/modelo.py", "funcion": "ModeloVertex.generar"},
   {"id": "ag_agotado", "descripcion": "Si agota las vueltas, se agrega el aviso 'el agente agoto las vueltas sin cerrar' con el gasto por herramienta y las llamadas repetidas.", "modulo": "pipeline/orquestador.py", "funcion": "procesar"}],
 },
 {"id": "revisor", "nombre": "Revisor obligatorio (IA)",
  "descripcion": "Antes de radicar, un segundo modelo con solo herramientas de lectura comprueba la identidad que se va a enviar (nombres, documentos, tipo de remitente y tipo de solicitud) contra los documentos: OK, CAMBIAR o SIN RESPALDO. Lo que objeta vuelve a la IA para corregir; lo que el texto prueba se mantiene.",
  "etapa_cronometro": "revisor", "modulo": "pipeline/revisor.py",
  "funciones": ["Orquestador._revision_obligatoria", "Orquestador._llamar_al_revisor", "orquestador._reparos_de_identidad", "revisor.revisar", "revisor.leer_reparos"],
  "usa_ia": True, "siguientes": ["correccion", "bitacora"],
  "desenlaces": [],
  "reglas": [
   {"id": "rev_solo_lectura", "descripcion": "Recibe un Contexto propio y solo los esquemas de HERRAMIENTAS_DEL_REVISOR (listar, leer, buscar); cualquier otra herramienta se rechaza sin invocarse.", "modulo": "pipeline/revisor.py", "funcion": "revisar"},
   {"id": "rev_vueltas", "descripcion": "Maximo MAX_VUELTAS_DEL_REVISOR llamadas al modelo; respuestas de herramienta cortadas a MAX_CARACTERES_RESPUESTA.", "modulo": "pipeline/revisor.py", "funcion": "revisar"},
   {"id": "rev_obligatorio", "descripcion": "Corre siempre que la compuerta deja pasar el correo y quede tiempo de corrida, sobre los CAMPOS_REVISADOS ya corregidos, con extractos del texto donde aparece cada valor (EXTRACTOS_POR_CAMPO de ANCHO_DEL_EXTRACTO caracteres). Si falla, deja aviso y decide la compuerta.", "modulo": "pipeline/orquestador.py", "funcion": "_revision_obligatoria", "usa": ["CAMPOS_REVISADOS", "EXTRACTOS_POR_CAMPO", "ANCHO_DEL_EXTRACTO"]},
   {"id": "rev_solo_aconseja", "descripcion": "En CAMPOS_EN_LOS_QUE_EL_REVISOR_SOLO_AVISA (tipo y documento del remitente) solo deja aviso. No se devuelve el documento del afectado que el texto rotula junto a su nombre ni el nombre que el texto da con su documento rotulado: ahi lo determinista es prueba.", "modulo": "pipeline/orquestador.py", "funcion": "_reparos_de_identidad", "usa": ["CAMPOS_EN_LOS_QUE_EL_REVISOR_SOLO_AVISA"]},
   {"id": "rev_segunda", "descripcion": "Lo que el agente corrige a un valor distinto del que el revisor propuso se revisa una vez mas; si lo vuelve a objetar, no se envia. El tipo de solicitud nunca se quita (CAMPOS_QUE_EL_REVISOR_NO_QUITA).", "modulo": "pipeline/orquestador.py", "funcion": "_revision_obligatoria", "usa": ["CAMPOS_QUE_EL_REVISOR_NO_QUITA"]}],
 },
 {"id": "combinacion", "nombre": "Combinación y atribución",
  "descripcion": "Une lo que leyó el algoritmo con lo del agente, registra quién resolvió cada campo, deduce la oficina a partir del tipo de solicitud y copia el documento del afectado al remitente cuando son la misma persona.",
  "etapa_cronometro": None, "modulo": "pipeline/agente.py",
  "funciones": ["agente.combinar", "agente.atribucion", "extraccion.extraer_oficina", "Orquestador._heredar_datos_del_afectado"],
  "usa_ia": False, "siguientes": ["registro_de_personas"],
  "desenlaces": [],
  "reglas": [
   {"id": "comb_precedencia", "descripcion": "Lo determinista tiene precedencia, salvo en los campos a confirmar que el agente respondio.", "modulo": "pipeline/agente.py", "funcion": "combinar"},
   {"id": "comb_oficina", "descripcion": "Si falta 'oficina' y hay tipo_solicitud, oficina = business_rules.get_office_id(tipo_solicitud) (tabla OFFICE_MAPPING), atribuida a ALGORITMO.", "modulo": "pipeline/extraccion.py", "funcion": "extraer_oficina"},
   {"id": "comb_heredar", "descripcion": "Solo con tipo_remitente == 3: numero/tipo_documento_remitente se copian del afectado si faltan (metodo 'remitente_es_el_afectado').", "modulo": "pipeline/orquestador.py", "funcion": "_heredar_datos_del_afectado"}],
 },
 {"id": "registro_de_personas", "nombre": "Registro de personas (SGDEA)",
  "descripcion": "Consulta por documento el registro de personas de SGDEA para rellenar teléfono, dirección, sexo y geografía que el correo no traía, y detecta si el documento pertenece a otra persona.",
  "etapa_cronometro": "registro_de_personas", "modulo": "pipeline/personas.py",
  "funciones": ["personas.rellenar_desde_el_registro", "ConsultaDePersonas.consultar", "ConsultaDePersonas._pedir", "Orquestador._consultar_persona_del_afectado", "personas.nombrado_junto_al_documento", "personas.lo_nombra_el_correo"],
  "usa_ia": False, "siguientes": ["compuerta"],
  "desenlaces": [{"accion": "manual", "cuando": "motivo DOCUMENTO_DE_OTRA_PERSONA: el registro tiene el documento a nombre de otra persona y el correo no escribe ese nombre junto al documento."}],
  "reglas": [
   {"id": "reg_solo_huecos", "descripcion": "Solo rellena CAMPOS_DEL_REGISTRO que esten vacios (telefono, direccion, sexo, departamento, municipio); nunca sustituye un dato del correo. Confianza CONFIANZA_DEL_REGISTRO, atribucion REGISTRO.", "modulo": "pipeline/personas.py", "funcion": "rellenar_desde_el_registro"},
   {"id": "reg_sexo_invertido", "descripcion": "El genero del registro se traduce con SEXO_DEL_GENERO_DEL_REGISTRO ({1:2, 2:1}); otros codigos se ignoran.", "modulo": "pipeline/personas.py", "funcion": "_sexo_del_registro"},
   {"id": "reg_correccion_nombre", "descripcion": "Si el nombre difiere (o falta) pero el texto escribe el nombre del registro a <= VENTANA_JUNTO_AL_DOCUMENTO caracteres del documento y el asunto/cuerpo (o el remitente) habla de esa persona, se corrige nombre_afectado con el del registro. Si es el mismo titular pero el extraido tiene palabras de mas, se limpia.", "modulo": "pipeline/personas.py", "funcion": "rellenar_desde_el_registro"},
   {"id": "reg_fallo_no_bloquea", "descripcion": "Un fallo de consulta (ConsultaDePersonaFallo) solo agrega aviso; 404 = persona no encontrada, no invalida nada.", "modulo": "pipeline/personas.py", "funcion": "rellenar_desde_el_registro"},
   {"id": "reg_cortocircuito", "descripcion": "Tras AGOTAMIENTOS_PARA_DEJAR_DE_PREGUNTAR timeouts seguidos (TIEMPO_MAXIMO cada uno), el cliente deja de consultar durante la corrida; cualquier respuesta reinicia la cuenta.", "modulo": "pipeline/personas.py", "funcion": "ConsultaDePersonas._pedir"},
   {"id": "reg_sigla", "descripcion": "La sigla del tipo se traduce con SIGLA_DEL_ENDPOINT; sin tipo o desconocido, SIGLA_POR_DEFECTO ('CC').", "modulo": "pipeline/personas.py", "funcion": "_sigla"}],
 },
 {"id": "compuerta", "nombre": "Compuerta de validación",
  "descripcion": "Revisión determinista campo por campo antes de radicar: forma (documento, nombre, teléfono, dirección, catálogos) y sustento (que el valor esté escrito en el correo, los adjuntos o el registro). Aprueba, corrige o rechaza; lo rechazado que tiene arreglo vuelve una vez a la IA.",
  "etapa_cronometro": "compuerta", "modulo": "pipeline/compuerta.py",
  "funciones": ["Orquestador._validar_y_corregir", "compuerta.validar", "compuerta._revisar_sustento", "compuerta.aplicar", "compuerta.avisos", "compuerta._motivo", "compuerta.confianza_exigida"],
  "usa_ia": False, "siguientes": ["correccion", "revisor", "bitacora"],
  "desenlaces": [{"accion": "manual", "cuando": "Veredicto RECHAZADO (algun reparo bloqueante): motivo = _MOTIVO_POR_CODIGO / _MOTIVO_POR_CAUSA / _MOTIVO_POR_CAMPO o VALIDACION_RECHAZO."}],
  "reglas": [
   {"id": "cmp_obligatorios", "descripcion": "Bloquea FALTA_CAMPO_OBLIGATORIO si falta alguno de OBLIGATORIOS (numero_documento_afectado, nombre_afectado, nombre_remitente, tipo_solicitud).", "modulo": "pipeline/compuerta.py", "funcion": "validar"},
   {"id": "cmp_evidencia", "descripcion": "SIN_EVIDENCIA (bloqueante) para todo hallazgo sin evidencia.", "modulo": "pipeline/compuerta.py", "funcion": "validar"},
   {"id": "cmp_confianza", "descripcion": "CONFIANZA_INSUFICIENTE si confianza < confianza_exigida(campo, CONFIANZA_PARA_RADICAR=0.60) (PISO_POR_CAMPO: tipo_solicitud 0.0). Bloquea si el campo es obligatorio; si no, lo corrige a vacio.", "modulo": "pipeline/compuerta.py", "funcion": "validar"},
   {"id": "cmp_catalogo", "descripcion": "tipo_remitente, tipo_documento_*, tipo_solicitud y sexo_afectado deben estar en su catalogo (FUERA_DE_CATALOGO / CATALOGO_NO_DISPONIBLE bloquean); un codigo en texto se corrige a entero.", "modulo": "pipeline/compuerta.py", "funcion": "validar"},
   {"id": "cmp_nombres", "descripcion": "Nombres: quita caracteres de formato invisibles (corrige); bloquea si es correo/URL; quita numeros >= DIGITOS_QUE_NO_NOMBRAN digitos o radicados pegados (corrige; bloquea si no queda nada); quita el cargo del remitente (_CARGOS) (corrige); bloquea NO_PARECE_UN_NOMBRE con el mismo criterio del extractor.", "modulo": "pipeline/compuerta.py", "funcion": "validar"},
   {"id": "cmp_documento", "descripcion": "Documento afectado/remitente: traduce digitos no ASCII (corrige); bloquea DOCUMENTO_NO_NUMERICO (salvo pasaporte/tipos alfanumericos), LONGITUD_IMPROBABLE (LONGITUD_POR_TIPO o fuera de MIN/MAX_DIGITOS), DOCUMENTO_DE_RELLENO (todos iguales), DV_NIT_INCORRECTO, DOCUMENTO_ES_DE_POSITIVA (_IDENTIDAD_DE_POSITIVA).", "modulo": "pipeline/compuerta.py", "funcion": "_revisar_documento"},
   {"id": "cmp_naturaleza", "descripcion": "Razon social con documento de persona natural: bloquea EMPRESA_CON_DOCUMENTO_DE_PERSONA. Documento de organizacion con nombre sin forma de empresa: solo aviso.", "modulo": "pipeline/compuerta.py", "funcion": "_revisar_naturaleza"},
   {"id": "cmp_telefono", "descripcion": "Telefono (no bloquea, corrige a vacio): igual al documento, linea de Positiva, de ejemplo (_TELEFONOS_DE_EJEMPLO), digito repetido, secuencia >= 7, o inexistente en el plan de numeracion.", "modulo": "pipeline/compuerta.py", "funcion": "_revisar_telefono"},
   {"id": "cmp_direccion", "descripcion": "Direccion (no bloquea, corrige a vacio): marca de 'sin dato' (_DIRECCION_VACIA), contiene un correo, o es solo un numero.", "modulo": "pipeline/compuerta.py", "funcion": "_revisar_direccion"},
   {"id": "cmp_coherencia", "descripcion": "tipo_remitente 3 con nombres distintos y sin mismo documento: bloquea. tipo 1/2 con mismo nombre y sin documentos distintos: corrige a 3. tipo 1/2 con el mismo documento que el afectado y nombres distintos: retira el documento del remitente (bloquea si parece NIT de otro). Si quien firma lleva la cedula del afectado desde otro buzon, nombre_remitente = nombre_afectado.", "modulo": "pipeline/compuerta.py", "funcion": "validar"},
   {"id": "cmp_oficina", "descripcion": "Si la oficina no coincide con get_office_id(tipo_solicitud), se corrige (no bloquea).", "modulo": "pipeline/compuerta.py", "funcion": "validar"},
   {"id": "cmp_sustento", "descripcion": "Cada valor tiene que estar escrito en las fuentes (correo, adjuntos, lo que devolvio el registro): las cifras por sus digitos y los nombres por sus palabras en orden. Un valor sin sustento se rechaza en ese campo.", "modulo": "pipeline/compuerta.py", "funcion": "_revisar_sustento"},
   {"id": "cmp_devuelve", "descripcion": "Si rechaza y queda tiempo, campos_a_corregir arma lo que tiene arreglo y se le devuelve UNA vez al agente con el motivo de cada campo; lo corregido vuelve a pasar por la compuerta.", "modulo": "pipeline/orquestador.py", "funcion": "_validar_y_corregir"},
   {"id": "cmp_veredicto", "descripcion": "Algun bloqueante -> RECHAZADO; si no, con correcciones -> CORREGIDO (se aplican); si no, APROBADO.", "modulo": "pipeline/compuerta.py", "funcion": "validar"}],
 },
 {"id": "correccion", "nombre": "Corrección por la IA",
  "descripcion": "Lo que la compuerta o el revisor rechazan se le devuelve al agente con el motivo de cada campo para que lo corrija leyendo otra vez los documentos. Lo corregido vuelve a la compuerta; lo que no se corrige no se envía.",
  "etapa_cronometro": "correccion", "modulo": "pipeline/correccion.py",
  "funciones": ["Orquestador._corregir", "correccion.campos_a_corregir", "correccion.extraccion_para_corregir", "agente.resolver"],
  "usa_ia": True, "siguientes": ["compuerta"],
  "desenlaces": [],
  "reglas": [
   {"id": "cor_una_vez", "descripcion": "La compuerta devuelve sus rechazos una sola vez por correo; el revisor, los suyos tras la compuerta. El agente recibe la extraccion con esos campos como pendientes y el motivo de cada uno.", "modulo": "pipeline/correccion.py", "funcion": "extraccion_para_corregir"},
   {"id": "cor_registro", "descripcion": "Si se corrige el documento del afectado, lo que el registro relleno con el documento anterior se descarta y se vuelve a consultar.", "modulo": "pipeline/orquestador.py", "funcion": "_corregir"},
   {"id": "cor_repetido", "descripcion": "Si el agente repite el valor que el revisor rechazo, no se envia ese campo (si era obligatorio, el correo va a manual).", "modulo": "pipeline/orquestador.py", "funcion": "_revision_obligatoria"}],
 },
 {"id": "bitacora", "nombre": "Bitácora de fallos y política de reintentos",
  "descripcion": "Cada fallo técnico del correo se clasifica (Cloudflare, backend caído, token, límite de tasa, adjunto ilegible…). Si hubo un fallo fatal no se radica; la misma tabla decide cuántas veces y cuánto esperar al reintentar.",
  "etapa_cronometro": None, "modulo": "pipeline/errores.py",
  "funciones": ["Bitacora.registrar", "Bitacora.puede_radicar", "Bitacora.motivo_manual", "errores.clasificar", "errores.debe_reintentar", "errores.espera_para", "errores.puede_repetirse_una_escritura"],
  "usa_ia": False, "siguientes": ["radicacion"],
  "desenlaces": [{"accion": "manual", "cuando": "Hay algun fallo FATAL en la bitacora: motivo = motivo_manual del primer fatal (ajustado por MOTIVO_POR_PASO)."}],
  "reglas": [
   {"id": "bit_clasificar", "descripcion": "clasificar(estado, cuerpo, excepcion): primero firmas textuales (_FIRMAS, la primera que case; las 'solo_local' se ignoran si hubo respuesta HTTP), luego codigo HTTP (dado o buscado en el texto con _ESTADO_EN_TEXTO) via _POR_ESTADO, cualquier 5xx = ORIGEN_INALCANZABLE; si nada, DESCONOCIDO.", "modulo": "pipeline/errores.py", "funcion": "clasificar"},
   {"id": "bit_reintento", "descripcion": "debe_reintentar = clase.reintentable y intento < clase.max_intentos. espera = min(espera_base_s * 2^(intento-1), TOPE_DE_ESPERA_S).", "modulo": "pipeline/errores.py", "funcion": "debe_reintentar"},
   {"id": "bit_puede_radicar", "descripcion": "Se puede radicar si no hay fallos de severidad FATAL; las DEGRADA solo van a los avisos.", "modulo": "pipeline/errores.py", "funcion": "Bitacora.puede_radicar"}],
 },
 {"id": "radicacion", "nombre": "Radicación en SGDEA",
  "descripcion": "Arma el formulario de la PQRD con los datos validados (y valores por defecto anotados para lo que falte) y lo envía a SGDEA junto con los adjuntos y el correo en HTML; luego deja la trazabilidad.",
  "etapa_cronometro": "radicacion", "modulo": "pipeline/radicador.py",
  "funciones": ["Orquestador._radicar", "Orquestador._radicado_por_otra_corrida", "Radicador.radicar", "radicador.armar_payload", "radicador.documento_del_correo", "RadicacionService.radicar_pqrd", "Radicador._dejar_traza", "RadicacionService.crear_trazabilidad"],
  "usa_ia": False, "siguientes": ["cierre_outlook"],
  "desenlaces": [{"accion": "manual", "cuando": "No se obtuvo numero: motivo = bitacora.motivo_manual('RADICACION') (p. ej. SGDEA_CAIDO, CLOUDFLARE_403, PETICION_MAL_FORMADA, RADICACION_SIN_CONFIRMAR, VALIDACION_RECHAZO) o ERROR_INESPERADO."}],
  "reglas": [
   {"id": "rad_dry_run", "descripcion": "Sin radicador (modo --seco) devuelve 'DRY-RUN'. Con MODO_TEST, RadicacionService.radicar_pqrd graba el payload en TEST_OUTPUT_DIR/radicaciones.csv y devuelve id_radicado 'DRY-RUN-ENT'.", "modulo": "pipeline/orquestador.py", "funcion": "_radicar"},
   {"id": "rad_otra_corrida", "descripcion": "Justo antes de radicar se relee el correo (get_email_by_id); si ya tiene etiqueta de radicado, se devuelve ese numero sin radicar de nuevo. Si no se puede releer, se radica igual con aviso.", "modulo": "pipeline/orquestador.py", "funcion": "_radicado_por_otra_corrida"},
   {"id": "rad_defectos", "descripcion": "Defaults anotados en 'suposiciones' (avisos 'por defecto: ...'): tipo_documento -> TIPO_DOCUMENTO_POR_DEFECTO (1, CC); tipo_remitente -> TIPO_REMITENTE_TERCERO (2); sexo -> SEXO_POR_DEFECTO (3, no determinado); tipo_solicitud -> TIPO_SOLICITUD_OTRA (14); oficina -> 15 (literal); departamento/municipio -> 6/149 (Bogota) ambos o ninguno; documento del remitente solo se hereda con tipo_remitente 3.", "modulo": "pipeline/radicador.py", "funcion": "armar_payload"},
   {"id": "rad_constantes_canal", "descripcion": "Fijos del canal: tipo PQRD 1, producto ARL 15, canal 7 (Email IA), medio de respuesta 1 (correo), usuario 'Asignador IA', listado de condiciones '[1]', acceso a informacion publica False, LGTBIQ False, descripcion acceso 2. Observaciones = fecha + asunto + primeros 2000 caracteres del cuerpo.", "modulo": "pipeline/radicador.py", "funcion": "armar_payload"},
   {"id": "rad_correo_afectado", "descripcion": "Correo del afectado = From, salvo buzon no humano/interno: entonces el primer correo externo del hilo, o vacio.", "modulo": "pipeline/radicador.py", "funcion": "_correo_del_afectado"},
   {"id": "rad_correo_html", "descripcion": "Siempre se adjunta 'correo.html' (asunto, de, fecha, cuerpo) salvo que ya venga uno con ese nombre.", "modulo": "pipeline/radicador.py", "funcion": "documento_del_correo"},
   {"id": "rad_reintento", "descripcion": "Solo se repite la escritura si debe_reintentar(clase, intento) Y la clase esta en SIN_EFECTO_EN_EL_SERVIDOR (Cloudflare, DNS, token): radicar no es idempotente. Una respuesta sin numero lanza error (RESPUESTA_SIN_RADICADO, no se repite). Adicionalmente RadicacionService reintenta internamente (max_attempts=3) ante 401 y ConnectError.", "modulo": "pipeline/radicador.py", "funcion": "Radicador.radicar"},
   {"id": "rad_traza", "descripcion": "Tras radicar, crear_trazabilidad(proceso='PQRD<id>', secuencia=radicado, estado='Por asignar', tramite='PQRD', nombre='Asignador IA - <oficina>'). Un fallo se registra en la bitacora (paso TRAZABILIDAD) pero no deshace la radicacion.", "modulo": "pipeline/radicador.py", "funcion": "Radicador._dejar_traza"},
   {"id": "rad_log", "descripcion": "Cada intento emite 'radicacion enviada a SGDEA' con campos_enviados (cuerpo reemplazado por su largo), campos_vacios, por_defecto y adjuntos; cada rechazo emite 'radicacion rechazada: <clase>'.", "modulo": "pipeline/radicador.py", "funcion": "Radicador.radicar"}],
 },
 {"id": "cierre_outlook", "nombre": "Cierre en Outlook (radicado)",
  "descripcion": "Con el número de radicado, guarda el hash para la deduplicación, etiqueta el correo con el radicado y lo mueve a la carpeta de radicación exitosa.",
  "etapa_cronometro": "cierre_outlook", "modulo": "pipeline/orquestador.py",
  "funciones": ["Detector.registrar_radicado", "Orquestador._mover_a_exitosa", "utils.radicado.etiqueta_para", "Orquestador._archivar", "OutlookService.move_email"],
  "usa_ia": False, "siguientes": ["aviso_y_cierre"],
  "desenlaces": [{"accion": "radicado", "cuando": "Se obtuvo numero de radicado (incluido el de otra corrida o 'DRY-RUN'/'DRY-RUN-ENT' en pruebas)."}],
  "reglas": [
   {"id": "cie_etiqueta", "descripcion": "Si el radicado no tiene forma ENT<numero>, se etiqueta como 'RADICADO:<valor>' y se avisa (contrato con SGDEA cambiado).", "modulo": "utils/radicado.py", "funcion": "etiqueta_para"},
   {"id": "cie_mover", "descripcion": "Marca (quitando EN-PROCESO/REINTENTO) y mueve a OUTLOOK_FOLDER_RADICACION_EXITOSA; el id nuevo de Graph reemplaza email.id. Fallos solo se avisan.", "modulo": "pipeline/orquestador.py", "funcion": "_archivar"},
   {"id": "cie_resto_del_hilo", "descripcion": "Tras radicar o detectar un duplicado, del resto del hilo que sigue en la bandeja se mueven a Duplicados de una vez solo los que con seguridad lo son: los de un buzon de Positiva y los reenvios exactos de este correo (mismo remitente, texto propio identico y los mismos documentos). Lo demas se queda para pasar por anexos y seguimiento en la corrida siguiente.", "modulo": "pipeline/orquestador.py", "funcion": "Orquestador._archivar_el_resto_del_hilo"}],
 },
 {"id": "revision_manual", "nombre": "Revisión manual o aplazamiento",
  "descripcion": "Todo camino que no radica pasa por aquí: si el motivo es de infraestructura, el correo se deja en la bandeja para que la siguiente corrida lo reintente (hasta un tope); si no, se marca y se mueve a la carpeta de revisión manual.",
  "etapa_cronometro": None, "modulo": "pipeline/reintentos.py",
  "funciones": ["Orquestador._a_revision_manual", "reintentos.decidir", "reintentos.etiqueta_de_intento", "Orquestador._archivar"],
  "usa_ia": False, "siguientes": ["aviso_y_cierre"],
  "desenlaces": [
   {"accion": "aplazado", "cuando": "motivo en MOTIVOS_QUE_SE_APLAZAN y REINTENTO:N+1 < MAX_CICLOS: marca REINTENTO:N+1 y se queda en la bandeja."},
   {"accion": "manual", "cuando": "Cualquier otro motivo, o aplazamientos agotados, o la marca REINTENTO no quedo escrita: marca REVISION-MANUAL:<motivo> y mueve a OUTLOOK_FOLDER_REVISAR_MANUAL."}],
  "reglas": [
   {"id": "rm_aplazables", "descripcion": "Se aplazan: MODELO_NO_RESPONDIO, MODELO_SIN_PERMISO, SGDEA_CAIDO, CLOUDFLARE_403, PETICION_MAL_FORMADA, SIN_OCR, TIEMPO_DE_CORRIDA_AGOTADO, ERROR_INESPERADO.", "modulo": "pipeline/reintentos.py", "funcion": "se_aplaza"},
   {"id": "rm_tope", "descripcion": "intento = mayor REINTENTO:N + 1; si intento >= MAX_CICLOS ya no se aplaza (va a manual).", "modulo": "pipeline/reintentos.py", "funcion": "decidir"},
   {"id": "rm_marca_no_escrita", "descripcion": "Si tras marcar REINTENTO las categorias no reflejan el intento (marcar devolvio None o fallo) y hay outlook, se cierra a manual para no reintentar sin cuenta.", "modulo": "pipeline/orquestador.py", "funcion": "_a_revision_manual"},
   {"id": "rm_excepciones", "descripcion": "FalloDelModelo -> motivo de la excepcion; cualquier otra excepcion -> bitacora 'PIPELINE' y motivo de su clase o ERROR_INESPERADO; ambos pasan por aqui.", "modulo": "pipeline/orquestador.py", "funcion": "procesar"},
   {"id": "rm_log_no_radicado", "descripcion": "En rechazos de compuerta, contradicciones del registro o fatales de bitacora se emite 'no se radica: <motivo>' con campos_faltantes y campos_leidos.", "modulo": "pipeline/orquestador.py", "funcion": "_registrar_lo_no_radicado"}],
 },
 {"id": "aviso_y_cierre", "nombre": "Aviso interno y telemetría",
  "descripcion": "Punto único de salida: remite al buzón interno de avisos un resumen del desenlace (nunca al ciudadano), registra el correo en el informe de la corrida y su consumo de modelo; al final del lote se escribe el informe.",
  "etapa_cronometro": None, "modulo": "pipeline/orquestador.py",
  "funciones": ["Orquestador._cerrar", "Orquestador._avisar", "notificacion.redactar", "Ejecucion.registrar", "Contador.registrar", "Ejecucion.escribir", "salida.emitir"],
  "usa_ia": False, "siguientes": [],
  "desenlaces": [],
  "reglas": [
   {"id": "av_destino", "descripcion": "El aviso va SOLO a settings.CORREO_DE_AVISOS (vacio por defecto = no se envia) via forward_email; nunca a quien escribio. No se avisa con accion en_proceso o sin_reclamar.", "modulo": "pipeline/orquestador.py", "funcion": "_avisar"},
   {"id": "av_detalle", "descripcion": "Detalle: en reenvio, la linea 'reenviado a ...'; en manual por contenido (motivo fuera de MOTIVOS_AJENOS_AL_CONTENIDO), la frase del agente o la lista de campos NO_RESUELTO; en otros casos vacio. Pie fijo PIE de IA.", "modulo": "pipeline/orquestador.py", "funcion": "_detalle_del_aviso"},
   {"id": "av_telemetria", "descripcion": "Ejecucion.registrar(RegistroCorreo(...)) y contador.registrar('agente', modelo.nombre, tokens_entrada, tokens_salida, turnos=...). Al final: informe en RUTA_LOGS (disco o gs://) ejecuciones/<dia>/<hora>-<id>.txt, diario/<dia>.txt y serie.tsv.", "modulo": "pipeline/telemetria.py", "funcion": "Ejecucion.escribir"},
   {"id": "av_consola", "descripcion": "correr emite una linea por correo (accion, motivo/radicado/area, por_que, vueltas, tokens, duracion) y el resumen del lote; las etapas solo se emiten si fallan o superan su umbral (UMBRAL_POR_ETAPA_MS / UMBRAL_ETAPA_LENTA_MS).", "modulo": "correr.py", "funcion": "_paso"}],
 },
]

ARISTAS = [
 ("lote", "deduplicacion", "correo tomado (presupuesto disponible)"),
 ("lote", "fin_sin_tomar", "presupuesto agotado"),
 ("deduplicacion", "duplicado", "lleva su propia etiqueta de radicado"),
 ("deduplicacion", "anexos", "del caso de un radicado (hilo, asunto o corrida)"),
 ("anexos", "revision_manual", "ANEXO_DE_RADICADO"),
 ("anexos", "seguimiento", "sin documentos nuevos"),
 ("seguimiento", "revision_manual", "POSIBLE_SOLICITUD_NUEVA"),
 ("seguimiento", "duplicado", "seguimiento de lo radicado"),
 ("deduplicacion", "reclamo", "no es duplicado"),
 ("reclamo", "revision_manual", "RECLAMO_AGOTADO"),
 ("reclamo", "en_proceso", "412: otra corrida lo tomo"),
 ("reclamo", "sin_reclamar", "no se pudo escribir la marca"),
 ("reclamo", "enrutamiento", "reclamado (EN-PROCESO:N)"),
 ("enrutamiento", "facturas", "ruta calculada"),
 ("facturas", "revision_manual", "FACTURA_ELECTRONICA"),
 ("facturas", "destino_del_correo", "no es factura"),
 ("destino_del_correo", "revision_manual", "REMITENTE_EXCLUIDO"),
 ("destino_del_correo", "reenviado", "judicial / medicina laboral"),
 ("destino_del_correo", "remitente_identificable", "se radica"),
 ("remitente_identificable", "revision_manual", "REMITENTE_NO_IDENTIFICABLE"),
 ("remitente_identificable", "descarga_adjuntos", "remitente identificable"),
 ("descarga_adjuntos", "volumen", "adjuntos descargados"),
 ("volumen", "revision_manual", "VOLUMEN_EXCESIVO"),
 ("volumen", "deduplicacion_contenido", "dentro de los topes"),
 ("deduplicacion_contenido", "duplicado", "ya radicado (capas 5-6)"),
 ("deduplicacion_contenido", "conversion_a_markdown", "no es duplicado"),
 ("conversion_a_markdown", "saneamiento", "workspace listo"),
 ("conversion_a_markdown", "transcripcion_vision", "hoja que el OCR no lee"),
 ("transcripcion_vision", "conversion_a_markdown", "texto transcrito"),
 ("conversion_a_markdown", "indice_por_hoja", "texto por hoja"),
 ("indice_por_hoja", "agente", "inventario y búsquedas"),
 ("indice_por_hoja", "revisor", "búsquedas del revisor"),
 ("saneamiento", "extraccion_determinista", "siempre (solo avisa)"),
 ("extraccion_determinista", "agente", "hallazgos + pendientes"),
 ("agente", "revision_manual", "FalloDelModelo (MODELO_NO_RESPONDIO / TIEMPO_DE_CORRIDA_AGOTADO ...)"),
 ("agente", "combinacion", "resolucion (o sin llamar si no hay pendientes)"),
 ("combinacion", "registro_de_personas", "hallazgos combinados"),
 ("registro_de_personas", "revision_manual", "DOCUMENTO_DE_OTRA_PERSONA"),
 ("registro_de_personas", "compuerta", "sin contradicciones"),
 ("compuerta", "correccion", "rechazo con arreglo: vuelve a la IA"),
 ("correccion", "compuerta", "valida otra vez"),
 ("compuerta", "revision_manual", "RECHAZADO"),
 ("compuerta", "revisor", "APROBADO / CORREGIDO"),
 ("revisor", "correccion", "objeta la identidad"),
 ("revisor", "bitacora", "identidad confirmada"),
 ("bitacora", "revision_manual", "hay fallos FATAL"),
 ("bitacora", "radicacion", "puede radicar"),
 ("radicacion", "cierre_outlook", "numero de radicado"),
 ("radicacion", "revision_manual", "sin numero"),
 ("cierre_outlook", "aviso_y_cierre", "radicado"),
 ("revision_manual", "aviso_y_cierre", "manual / aplazado"),
 ("duplicado", "aviso_y_cierre", "archivado en Duplicados"),
 ("reenviado", "aviso_y_cierre", "archivado en Reenviado otra area"),
 ("en_proceso", "aviso_y_cierre", "sin aviso"),
 ("sin_reclamar", "aviso_y_cierre", "sin aviso"),
]

# Nodos terminales (desenlaces sin logica propia) para el diagrama
TERMINALES = [
 {"id": "duplicado", "nombre": "Duplicado", "descripcion": "El correo ya estaba radicado: se retiran las marcas de proceso, se mueve a la carpeta de duplicados y con el los reenvios exactos y lo de Positiva que siga del hilo en la bandeja.",
  "etapa_cronometro": None, "modulo": "pipeline/orquestador.py", "funciones": ["Orquestador._archivar_duplicado"], "usa_ia": False,
  "siguientes": ["aviso_y_cierre"], "desenlaces": [{"accion": "duplicado", "cuando": "terminal"}], "reglas": [], "parametros": [], "terminal": True},
 {"id": "reenviado", "nombre": "Reenviado a otra área", "descripcion": "Se reenvió a Notificaciones Judiciales o a Medicina Laboral y se archivó.",
  "etapa_cronometro": None, "modulo": "pipeline/orquestador.py", "funciones": ["Orquestador._reenviar"], "usa_ia": False,
  "siguientes": ["aviso_y_cierre"], "desenlaces": [{"accion": "reenviado", "cuando": "terminal"}], "reglas": [], "parametros": [], "terminal": True},
 {"id": "en_proceso", "nombre": "En proceso por otra corrida", "descripcion": "Otra corrida ganó el reclamo; este proceso no toca el correo ni avisa.",
  "etapa_cronometro": None, "modulo": "pipeline/orquestador.py", "funciones": ["Orquestador.procesar"], "usa_ia": False,
  "siguientes": ["aviso_y_cierre"], "desenlaces": [{"accion": "en_proceso", "cuando": "terminal"}], "reglas": [], "parametros": [], "terminal": True},
 {"id": "sin_reclamar", "nombre": "Sin reclamar", "descripcion": "No se pudo escribir la marca de reclamo; el correo queda intacto para la siguiente corrida.",
  "etapa_cronometro": None, "modulo": "pipeline/orquestador.py", "funciones": ["Orquestador.procesar"], "usa_ia": False,
  "siguientes": ["aviso_y_cierre"], "desenlaces": [{"accion": "sin_reclamar", "cuando": "terminal"}], "reglas": [], "parametros": [], "terminal": True},
 {"id": "fin_sin_tomar", "nombre": "Sin tomar (presupuesto)", "descripcion": "La corrida se quedó sin tiempo antes de empezar este correo; se queda en la bandeja sin marca.",
  "etapa_cronometro": None, "modulo": "correr.py", "funciones": ["correr.main.atender"], "usa_ia": False,
  "siguientes": [], "desenlaces": [{"accion": "sin_tomar", "cuando": "terminal"}], "reglas": [], "parametros": [], "terminal": True},
]

# ───────────────────────── PARAMETROS ─────────────────────────

NODO_POR_MODULO = {
 "correr": "lote", "pipeline.presupuesto": "lote", "services.outlook": "lote",
 "pipeline.duplicados": "deduplicacion", "utils.radicado": "deduplicacion",
 "pipeline.anexos": "anexos", "pipeline.seguimiento": "seguimiento",
 "services.supabase_client": "deduplicacion_contenido",
 "pipeline.reclamo": "reclamo", "pipeline.marcas": "reclamo",
 "pipeline.enrutamiento": "enrutamiento", "business_rules.clasificacion_remitente": "enrutamiento",
 "utils.html_cleaner": "enrutamiento",
 "pipeline.facturas": "facturas",
 "pipeline.volumen": "volumen",
 "pipeline.materializar": "conversion_a_markdown", "pipeline.conversion": "conversion_a_markdown",
 "pipeline.saneamiento": "saneamiento",
 "pipeline.extraccion": "extraccion_determinista", "utils.documento_candidatos": "extraccion_determinista",
 "utils.tipos_documento": "extraccion_determinista", "utils.contacto_extractor": "extraccion_determinista",
 "pipeline.geografia": "extraccion_determinista", "utils.generic_filter": "extraccion_determinista",
 "pipeline.agente": "agente", "pipeline.modelo": "agente", "servidor_mcp.herramientas": "agente",
 "pipeline.revisor": "revisor", "pipeline.correccion": "correccion",
 "pipeline.indice": "indice_por_hoja", "pipeline.workspace": "indice_por_hoja",
 "pipeline.personas": "registro_de_personas",
 "pipeline.compuerta": "compuerta",
 "pipeline.errores": "bitacora",
 "pipeline.radicador": "radicacion",
 "pipeline.reintentos": "revision_manual",
 "pipeline.notificacion": "aviso_y_cierre", "pipeline.telemetria": "aviso_y_cierre",
 "pipeline.salida": "aviso_y_cierre", "pipeline.costo": "aviso_y_cierre", "pipeline.almacen": "aviso_y_cierre",
}
NODO_POR_CLAVE = {
 "correr.TIMEOUT_SGDEA_S": "radicacion",
 "correr.UMBRAL_ETAPA_LENTA_MS": "aviso_y_cierre", "correr.UMBRAL_POR_ETAPA_MS": "aviso_y_cierre",
 "correr.LARGO_DEL_FALLO": "aviso_y_cierre", "correr.LOGS": "aviso_y_cierre",
 "correr.WORKSPACES": "conversion_a_markdown",
 "services.outlook.RESULTADOS_POR_PAGINA_DE_ASUNTO": "deduplicacion",
 "services.outlook.PAGINAS_POR_ASUNTO": "deduplicacion",
 "services.outlook.CANDIDATOS_POR_ASUNTO": "deduplicacion",
 "services.outlook.INTENTOS_POR_FALLO_DE_RED": "reclamo",
 "services.outlook.ESPERA_ENTRE_INTENTOS_S": "reclamo",
 "utils.generic_filter._INTERNAL_DOMAINS": "remitente_identificable",
 "utils.generic_filter._NON_HUMAN_LOCAL_PARTS": "remitente_identificable",
 "utils.generic_filter._NON_HUMAN_NAME_PATTERNS": "remitente_identificable",
 "utils.generic_filter._EMAIL_REGEX": "remitente_identificable",
 "pipeline.materializar._RUIDO": "volumen", "pipeline.materializar._IMAGEN_DE_FIRMA": "volumen",
 "pipeline.extraccion.UMBRAL_CONFIANZA": "extraccion_determinista",
 "pipeline.conversion.MAX_IMAGENES_IA_POR_CORREO": "transcripcion_vision",
 "pipeline.conversion.MARCA_TRANSCRITO_POR_IA": "transcripcion_vision",
 "pipeline.conversion.TRANSCRIPTOR_IA": "transcripcion_vision",
 "pipeline.modelo.INSTRUCCION_DE_TRANSCRIPCION": "transcripcion_vision",
 "servidor_mcp.herramientas.TRAMOS_POR_CONSULTA": "indice_por_hoja",
 "servidor_mcp.herramientas.MAX_TRAMOS_POR_CONSULTA": "indice_por_hoja",
 "servidor_mcp.herramientas.MAX_CARACTERES_CONSULTA": "indice_por_hoja",
 "pipeline.orquestador.CAMPOS_DE_IDENTIDAD": "revisor", "pipeline.orquestador.CAMPOS_REVISADOS": "revisor",
 "pipeline.orquestador.ANCHO_DEL_EXTRACTO": "revisor", "pipeline.orquestador.EXTRACTOS_POR_CAMPO": "revisor",
 "pipeline.orquestador.CAMPOS_EN_LOS_QUE_EL_REVISOR_SOLO_AVISA": "revisor",
 "pipeline.orquestador.CAMPOS_QUE_EL_REVISOR_NO_QUITA": "revisor",
}
EXCLUIR = {
 "pipeline.orquestador._BITACORA", "pipeline.conversion._CONVERSORES",
 "pipeline.salida.CAMPO_MENSAJE", "pipeline.salida.CAMPO_SEVERIDAD", "pipeline.salida.INFO",
 "pipeline.salida.AVISO", "pipeline.salida.ERROR", "pipeline.salida.VACIOS",
 "pipeline.telemetria._ORIGENES_DEL_INFORME", "pipeline.telemetria._NOMBRE_DEL_ORIGEN",
 "pipeline.telemetria._ANCHO_DE_COLUMNA", "correr.RAIZ",
 "pipeline.workspace.ESTADO_OK", "pipeline.workspace.ESTADO_VACIO", "pipeline.workspace.ESTADO_NO_SOPORTADO",
 "pipeline.workspace.ESTADO_ERROR", "pipeline.workspace.ESTADO_DEMASIADO_GRANDE",
 "pipeline.workspace._LARGO_LEGIBLE", "pipeline.workspace._LARGO_DEL_RESUMEN",
 "pipeline.revisor.VEREDICTOS", "pipeline.extraccion.METODOS_POR_DEFECTO",
 "pipeline.enrutamiento.MOTIVO_REMITENTE_EXCLUIDO", "pipeline.enrutamiento.MOTIVO_REMITENTE_NO_IDENTIFICABLE",
 "pipeline.facturas.MOTIVO", "pipeline.volumen.MOTIVO",
 "utils.contacto_extractor.SIN_TABLA", "utils.contacto_extractor.INVALIDO",
 "pipeline.extraccion._MAY", "pipeline.extraccion._MIN",
}

ENV = {
 "correr.CONCURRENCIA": "CONCURRENCIA", "correr.CORREOS_POR_CORRIDA": "CORREOS_POR_CORRIDA",
 "correr.TOPE_POR_CORREO_S": "TOPE_POR_CORREO_S", "correr.UMBRAL_ETAPA_LENTA_MS": "UMBRAL_ETAPA_LENTA_MS",
 "correr.LOGS": "RUTA_LOGS", "correr.WORKSPACES": "RUTA_WORKSPACE",
 "pipeline.agente.MAX_ITERACIONES": "MAX_VUELTAS_AGENTE",
 "pipeline.volumen.MAX_ADJUNTOS": "MAX_ADJUNTOS_POR_CORREO", "pipeline.volumen.MAX_PAGINAS": "MAX_PAGINAS_POR_CORREO",
 "pipeline.volumen.MAX_MEGABYTES": "MAX_MEGABYTES_POR_CORREO",
}

VALOR_MANUAL = {
 "correr.CONCURRENCIA": (5, "int"), "correr.CORREOS_POR_CORRIDA": (50, "int"),
 "correr.TOPE_POR_CORREO_S": (600.0, "float"), "correr.UMBRAL_ETAPA_LENTA_MS": (5000, "int"),
 "correr.LOGS": ("<motor>/logs", "str"), "correr.WORKSPACES": ("<motor>/workspace", "str"),
 "pipeline.agente.MAX_ITERACIONES": (5, "int"),
 "pipeline.volumen.MAX_ADJUNTOS": (30, "int"), "pipeline.volumen.MAX_PAGINAS": (80, "int"),
 "pipeline.volumen.MAX_MEGABYTES": (50, "int"),
 "services.supabase_client.MARGEN_DE_ZONA_HORARIA": ("timedelta(days=1)", "str"),
 "pipeline.geografia.CATALOGO": ("<motor>/business_rules/municipios.json", "str"),
 "pipeline.errores.POR_CODIGO": ({c["codigo"]: c for c in C["pipeline.errores.CLASES"]["valor"]}, "dict"),
 "pipeline.errores.DESCONOCIDO": ([c for c in C["pipeline.errores.CLASES"]["valor"] if c["codigo"] == "DESCONOCIDO"][0], "dict"),
 "servidor_mcp.herramientas.HERRAMIENTAS": (["listar_documentos", "leer_documento", "buscar_en_documentos", "consultar_documentos", "consultar_catalogo", "validar_documento", "consultar_persona", "reportar_hallazgo", "pedir_revision"], "dict"),
}

# (sobrescribible, nota) que corrigen al automatico
OVERRIDE = {
 "pipeline.agente.MAX_ITERACIONES": (False, "No. Se calcula una sola vez al importar desde MAX_VUELTAS_AGENTE (>0, si no 5) y se usa como valor por defecto de `resolver(..., max_iteraciones=MAX_ITERACIONES)`; el orquestador llama `resolver(self.modelo, ctx, determinista)` sin pasarlo. Para cambiarlo en caliente: setattr(pipeline.orquestador, 'resolver', functools.partial(pipeline.agente.resolver, max_iteraciones=N)) (el orquestador busca `resolver` en sus globales en cada correo)."),
 "pipeline.agente.VUELTAS_POR_DEFECTO": (False, "No. Solo lo lee `_tope_de_vueltas()`, que se ejecuta una vez al importar para fijar MAX_ITERACIONES."),
 "pipeline.agente.CONFIANZA_MINIMA": (False, "No. Es copia de compuerta.CONFIANZA_PARA_RADICAR al importar y se usa como valor por defecto de `resolver(..., confianza_minima=...)`. En caliente: functools.partial(pipeline.agente.resolver, confianza_minima=x) asignado a pipeline.orquestador.resolver."),
 "pipeline.compuerta.CONFIANZA_PARA_RADICAR": (False, "No. Es el valor por defecto de `validar(..., confianza_minima=CONFIANZA_PARA_RADICAR)` y de `confianza_exigida(campo, base=...)` (fijados al definir), y pipeline.agente lo copia al importar en CONFIANZA_MINIMA (otro valor por defecto). En caliente: setattr(pipeline.compuerta, 'validar', functools.partial(validar_original, confianza_minima=x)) — el orquestador llama `compuerta.validar` como atributo del modulo — y el partial equivalente de pipeline.agente.resolver."),
 "pipeline.compuerta.PISO_POR_CAMPO": (True, "Si. `confianza_exigida` lo lee como global en cada llamada (compuerta.validar y agente.resolver la invocan). Tambien vale mutarlo en sitio. Es el piso de confianza POR CAMPO: un campo listado aqui ignora el piso general de 0.60."),
 "pipeline.extraccion.UMBRAL_CONFIANZA": (False, "No. Es el valor por defecto de `extraer_todo(..., umbral=UMBRAL_CONFIANZA)` y el orquestador no lo pasa. En caliente: setattr(pipeline.orquestador, 'extraer_todo', functools.partial(pipeline.extraccion.extraer_todo, umbral=x))."),
 "pipeline.errores.TOPE_DE_ESPERA_S": (False, "No. Es el valor por defecto de `espera_para(clase, intento, tope=TOPE_DE_ESPERA_S)`; orquestador y radicador la llaman sin `tope` (y la importaron por nombre)."),
 "pipeline.errores.CLASES": (False, "No. POR_CODIGO se construye a partir de CLASES al importar y `clasificar` devuelve POR_CODIGO[codigo]. Para cambiar la politica de una clase hay que reemplazar la entrada en pipeline.errores.POR_CODIGO (Clase es un dataclass congelado: usar dataclasses.replace)."),
 "pipeline.errores.POR_CODIGO": (True, "Si. `clasificar` y `Bitacora` leen POR_CODIGO[codigo] en cada llamada: reemplazar una entrada (p. ej. POR_CODIGO['TIEMPO_AGOTADO'] = dataclasses.replace(c, max_intentos=5)) cambia reintentos, esperas, severidad y motivo. Ojo: DESCONOCIDO es una referencia aparte, fijada al importar."),
 "pipeline.revisor.MAX_VUELTAS_DEL_REVISOR": (False, "No. Es el valor por defecto de `revisar(..., max_vueltas=...)`. En caliente: setattr(pipeline.revisor, 'revisar', functools.partial(revisar_original, max_vueltas=N)) — Orquestador._revisor llama `revisor_mod.revisar` como atributo en cada revision."),
 "pipeline.presupuesto.CADENCIA_S": (False, "Parcial. `correr._presupuesto_de_la_corrida` lo re-importa al llamarse (una vez, al arrancar main) y lo usa si PRESUPUESTO_S no esta fijado; pero el valor por defecto de Presupuesto.total_s quedo fijado al importar. Cambiarlo despues de crear el Presupuesto no tiene efecto."),
 "pipeline.presupuesto.FRACCION_DE_LA_VENTANA": (False, "Parcial: igual que CADENCIA_S (solo afecta si se cambia antes de que main() construya el Presupuesto y no hay PRESUPUESTO_S)."),
 "pipeline.presupuesto.RESERVA_S": (False, "No. Es el valor por defecto del campo dataclass Presupuesto.reserva_s (fijado al importar). Se puede cambiar en la instancia: orq.presupuesto.reserva_s = x."),
 "pipeline.presupuesto.MINIMO_POR_CORREO_S": (True, "Si. Presupuesto.agotado() y recortar() lo leen como global en cada llamada."),
 "pipeline.modelo.TIMEOUT_DEL_MODELO_MS": (False, "Antes del primer uso: ModeloVertex._obtener_cliente lo lee al crear el genai.Client y lo cachea en self._cliente; despues de la primera llamada de esa instancia ya no tiene efecto (hay que crear otro ModeloVertex o poner modelo._cliente = None)."),
 "pipeline.agente._INICIO_DE_CRITERIO": (False, "No. Solo lo usa _partir_el_prompt, que se ejecuta una vez al importar para construir CABECERA/CRITERIOS."),
 "pipeline.extraccion._PRIMERA_PERSONA_EN_ETIQUETAS": (False, "Antes del primer uso: solo lo lee _regex_en_nombre_de_otro(), que compila la regex en el primer correo y la cachea en el global _en_nombre_de_otro (poner pipeline.extraccion._en_nombre_de_otro = None para recompilar)."),
 "pipeline.extraccion._ETIQUETAS_AFECTADO": (True, "Si: varias funciones lo leen como global en cada llamada. Excepcion: la regex 'en nombre de otro' se compilo con el en el primer uso y queda cacheada en _en_nombre_de_otro (ponerla a None para recompilar)."),
 "pipeline.geografia.CATALOGO": (False, "Antes del primer uso: lo lee _catalogo(), decorada con @lru_cache(maxsize=1); tras el primer correo el catalogo queda en cache (pipeline.geografia._catalogo.cache_clear() para recargar)."),
 "pipeline.volumen.MAX_ADJUNTOS": (True, "Si. `medir` lo lee como global en cada correo. La variable de entorno solo se lee una vez al importar (pasarla despues no hace nada; usar setattr)."),
 "pipeline.volumen.MAX_PAGINAS": (True, "Si. `medir` lo lee como global en cada correo. MAX_PAGINAS_POR_CORREO solo se lee al importar."),
 "pipeline.volumen.MAX_MEGABYTES": (True, "Si. `medir` lo lee como global en cada correo. MAX_MEGABYTES_POR_CORREO solo se lee al importar."),
 "correr.CONCURRENCIA": (True, "Si, si se cambia antes de llamar a correr.main() (lo lee al crear el Semaphore). La variable de entorno se lee al importar correr."),
 "correr.CORREOS_POR_CORRIDA": (True, "Si, antes de main(): es el default de --n (argparse lo lee en main)."),
 "correr.TOPE_POR_CORREO_S": (True, "Si. atender() lo lee como global en cada correo (asyncio.wait_for)."),
 "correr.UMBRAL_ETAPA_LENTA_MS": (True, "Si. umbral_de() lo lee como global en cada traza."),
 "correr.UMBRAL_POR_ETAPA_MS": (True, "Si. umbral_de() lo lee como global en cada traza (tambien mutable en sitio)."),
 "correr.TIMEOUT_SGDEA_S": (True, "Si, antes de main(): se pasa a RadicacionService(timeout=...) al construirlo; despues queda en el httpx.AsyncClient."),
 "correr.LOGS": (True, "Si, antes de main() (nueva_ejecucion(LOGS)). Se calcula al importar desde RUTA_LOGS."),
 "correr.WORKSPACES": (True, "Si, antes de main() (base_workspace=str(WORKSPACES)). Se calcula al importar desde RUTA_WORKSPACE."),
 "pipeline.enrutamiento._ENTES": (False, "No. Solo se usa al importar para compilar _ENTE_EN_EL_ASUNTO y _ENTE_QUE_REQUIERE; para cambiar el comportamiento hay que reemplazar esas dos regex compiladas."),
 "pipeline.enrutamiento._ROTULOS": (False, "No. Solo se usa al importar para compilar _LISTA_DE_DESTINATARIOS."),
 "pipeline.saneamiento._PATRONES": (False, "No. Se compila al importar en _COMPILADOS; reemplazar pipeline.saneamiento._COMPILADOS (tupla de (nombre, regex_compilada, Severidad, explicacion))."),
 "pipeline.saneamiento._COMPILADOS": (True, "Si. `detectar` lo recorre en cada llamada (etapa saneamiento y sanear_fragmento de las herramientas)."),
 "pipeline.agente._TEXTO_SISTEMA": (False, "No. Se parte al importar en CABECERA y CRITERIOS; cambiar esos dos."),
 "pipeline.agente.SISTEMA": (False, "No. No se usa en el pipeline (solo referencia/medidas)."),
 "pipeline.agente._MARCA_CRITERIOS": (True, "Si (prompt_de_sistema lo usa en cada llamada como titulo del bloque), aunque tambien se uso al importar para partir el prompt."),
 "pipeline.agente.ESQUEMAS_HERRAMIENTAS": (False, "Parcial. agente.resolver lo lee como global en cada vuelta (lo que se le declara al modelo), pero pipeline.orquestador lo copio al importar (esquemas del revisor) y _INVOCACION_EN_TEXTO se compilo con sus nombres al importar."),
 "servidor_mcp.herramientas.CATALOGOS": (True, "Si. Contexto(...) lo copia en cada correo (default_factory=lambda: dict(CATALOGOS)) y compuerta.validar lo re-importa dentro de la funcion. Mutarlo en sitio tambien vale. Incluye 'tipo_solicitud' (copiado de business_rules.office_mapping.TIPO_SOLICITUD_NOMBRES al importar)."),
 "servidor_mcp.herramientas.HERRAMIENTAS": (True, "Si. `invocar` busca HERRAMIENTAS[nombre] en cada llamada: sustituir una entrada por un envoltorio intercepta la herramienta para el agente y el revisor."),
 "pipeline.radicador.TIPO_REMITENTE_ES_EL_AFECTADO": (True, "Si. radicador lo lee como global; Orquestador._heredar_datos_del_afectado lo re-importa dentro de la funcion en cada correo."),
 "pipeline.compuerta.OBLIGATORIOS": (True, "Si. compuerta.validar lo lee como global y el orquestador lo lee como `compuerta.OBLIGATORIOS` (log de campos faltantes)."),
 "utils.tipos_documento.MIN_DIGITOS": (False, "No. Se calcula al importar desde LONGITUD_POR_TIPO y lo copian al importar pipeline.compuerta, pipeline.extraccion (ademas compilado en _ALGUNA_RACHA_DE_DOCUMENTO), servidor_mcp.herramientas y utils.documento_candidatos (alli ademas se copia al importar en MIN_CON_MARCA). Cambiar la copia de cada modulo (p. ej. setattr(pipeline.compuerta, 'MIN_DIGITOS', v))."),
 "utils.tipos_documento.MAX_DIGITOS": (False, "No. Igual que MIN_DIGITOS: copiado al importar en pipeline.compuerta, pipeline.extraccion (y compilado en _ALGUNA_RACHA_DE_DOCUMENTO), servidor_mcp.herramientas y utils.documento_candidatos (alli ademas copiado en MAX_CON_MARCA)."),
 "utils.tipos_documento.LONGITUD_POR_TIPO": (False, "Parcial. Las funciones de utils.tipos_documento (longitud_valida, rango_aceptable, tipos_compatibles) lo leen en cada llamada y mutarlo en sitio afecta tambien a servidor_mcp.herramientas (que importo la misma referencia); pero MIN_DIGITOS/MAX_DIGITOS se calcularon de el al importar y no cambian."),
 "pipeline.seguimiento.SISTEMA": (True, "Si. clasificar lo lee como global en cada consulta."),
 "pipeline.seguimiento.LARGO_MINIMO_DE_LA_CITA": (True, "Si. leer lo lee como global en cada respuesta."),
 "pipeline.seguimiento.LARGO_MINIMO_DEL_TEXTO_PROPIO": (True, "Si. Orquestador._pide_algo_nuevo lo lee como seguimiento.LARGO_MINIMO_DEL_TEXTO_PROPIO en cada correo."),
 "pipeline.anexos.TAMANO_MINIMO_DE_IMAGEN": (True, "Si. es_documento lo lee como global en cada adjunto."),
 "pipeline.agente.CABECERA": (True, "Si. prompt_de_sistema lo lee como global en cada correo."),
 "pipeline.agente.CRITERIOS": (True, "Si. prompt_de_sistema lo lee como global en cada correo (dict campo -> texto del criterio; mutable en sitio)."),
 "pipeline.agente._INVOCACION_EN_TEXTO": (True, "Si. herramienta_pedida_en_texto lo usa en cada turno (se compilo con los nombres de ESQUEMAS_HERRAMIENTAS al importar)."),
}

DESC = {
 "pipeline.duplicados._HILO_CITADO": "Regex que corta el hilo citado (desde 'De:'/'From:') para comparar solo el texto propio del correo.",
 "pipeline.reclamo._PATRON": "Regex que reconoce la marca 'EN-PROCESO:<n>' en las categorias.",
 "pipeline.reclamo.PREFIJO": "Prefijo de la marca de reclamo (EN-PROCESO). Protocolo compartido con corridas anteriores: cambiarlo hace invisibles las marcas ya puestas.",
 "pipeline.marcas.EN_PROCESO": "Prefijo de la familia de marcas de reclamo.",
 "pipeline.marcas.REINTENTO": "Prefijo de la familia de marcas de aplazamiento.",
 "pipeline.marcas.REVISION_MANUAL": "Prefijo de la familia de marcas de revision manual.",
 "pipeline.marcas.REENVIADO": "Prefijo de la familia de marcas de reenvio a otra area.",
 "business_rules.clasificacion_remitente.DESTINO_MEDICINA_LABORAL": "Buzon de Medicina Laboral (origen de la copia usada por pipeline.enrutamiento).",
 "business_rules.clasificacion_remitente.DESTINO_NOTIFICACIONES_JUDICIALES": "Buzon de Notificaciones Judiciales (origen de la copia usada por pipeline.enrutamiento).",
 "pipeline.volumen.MAX_ADJUNTOS": "Tope de adjuntos utiles (sin decorativos) por correo; por encima va a manual VOLUMEN_EXCESIVO.",
 "pipeline.volumen.MAX_PAGINAS": "Tope de paginas (PDF + imagenes) por correo.",
 "pipeline.volumen.MAX_MEGABYTES": "Tope de megabytes de adjuntos utiles por correo.",
 "pipeline.conversion.MAX_PROFUNDIDAD_SOBRE": "Niveles maximos de sobres firmados (.p7m) anidados que se abren.",
 "pipeline.conversion._CONTROL": "Regex de caracteres de control que normalizar() elimina del texto convertido.",
 "pipeline.saneamiento._PATRONES": "Patrones de inyeccion (categoria, regex, severidad, explicacion): anular instrucciones (es/en), redefinir rol, marcadores de sistema, instruccion de salida, forzar clasificacion, exfiltracion, invocacion de herramienta (ALTA); delimitador falso, urgencia/autoridad (MEDIA); base64 largo (BAJA).",
 "pipeline.saneamiento._COMPILADOS": "Version compilada de _PATRONES que usa detectar() (esta es la que hay que sustituir en caliente).",
 "pipeline.extraccion.LARGO_MINIMO_DIRECCION": "Largo minimo (caracteres) para aceptar una direccion extraida.",
 "pipeline.extraccion.TIPO_REMITENTE_AFECTADO": "Codigo de tipo_remitente 'el afectado mismo' (3) que usa el extractor.",
 "pipeline.extraccion._ETIQUETAS_AFECTADO": "Rotulos que preceden al nombre del afectado (trabajador, afiliado, paciente, titular, señor, nombre completo, mi nombre es, yo, ...).",
 "pipeline.extraccion._GENERO_FEMENINO": "Marcas gramaticales femeninas junto al nombre para deducir sexo por concordancia.",
 "pipeline.extraccion._GENERO_MASCULINO": "Marcas gramaticales masculinas junto al nombre para deducir sexo por concordancia.",
 "pipeline.extraccion._ROTULO_DIRECCION": "Rotulo 'Direccion (de residencia/de notificacion):' que abre una direccion.",
 "pipeline.extraccion._SEXO_DECLARADO": "Rotulo explicito 'Sexo:/Genero: M|F|masculino|femenino|hombre|mujer'.",
 "pipeline.extraccion._VALOR_SEXO": "Traduccion del sexo declarado a codigo (1 masculino, 2 femenino).",
 "utils.documento_candidatos._BANDERAS": "Banderas de contexto que se calculan para cada candidato a documento.",
 "utils.documento_candidatos._PREFIJO_MONTO": "Contexto previo que delata un monto ($, valor, pesos...) y descarta el numero como documento.",
 "utils.documento_candidatos._SUFIJO_DECIMAL": "Sufijo ',dd' que delata un monto.",
 "pipeline.extraccion.METODO_TIPO_PQRD_FIJO": "Nombre del metodo con que se registra tipo_pqrd = 1 (Solicitud) por defecto.",
 "pipeline.geografia.CATALOGO": "Ruta del catalogo de municipios (business_rules/municipios.json).",
 "servidor_mcp.herramientas.HERRAMIENTAS": "Registro nombre -> funcion de las herramientas invocables por el agente/revisor.",
 "pipeline.agente.ESQUEMAS_HERRAMIENTAS": "Declaraciones (function_declarations) de las 9 herramientas que se envian al modelo en cada vuelta.",
 "pipeline.agente._TEXTO_SISTEMA": "Prompt de sistema completo del agente (fuente de CABECERA y CRITERIOS).",
 "servidor_mcp.herramientas.CAMPOS_REPORTABLES": "Campos que el agente puede reportar con reportar_hallazgo (enum del esquema).",
 "pipeline.seguimiento.SISTEMA": "Prompt de sistema de la IA que decide si un correo del caso de un radicado es SEGUIMIENTO o SOLICITUD_NUEVA (responde JSON con la cita).",
 "pipeline.seguimiento.LARGO_MINIMO_DE_LA_CITA": "Caracteres minimos de la cita para aceptar SOLICITUD_NUEVA.",
 "pipeline.seguimiento.LARGO_MINIMO_DEL_TEXTO_PROPIO": "Caracteres minimos de lo que escribio la persona para consultar a la IA.",
 "pipeline.anexos.TAMANO_MINIMO_DE_IMAGEN": "Por debajo de estos bytes una imagen adjunta es firma/logo y no cuenta como documento.",
 "pipeline.revisor.SISTEMA": "Prompt de sistema del revisor (criterios por campo y formato 'campo: OK|CAMBIAR a X|SIN RESPALDO | motivo').",
 "pipeline.personas.TIEMPO_MAXIMO": "Timeout (s) de cada consulta GET /personas.",
 "pipeline.errores.DESCONOCIDO": "Clase de fallo por defecto cuando nada casa (FATAL, motivo ERROR_INESPERADO).",
 "pipeline.errores.POR_CODIGO": "Indice codigo -> Clase que usa clasificar (esta es la tabla efectiva de reintentos/esperas/motivos).",
 "pipeline.errores._POR_ESTADO": "Codigo HTTP -> clase de fallo (403 solo si no hay firma de Cloudflare).",
 "pipeline.errores.CLASES": "Catalogo de clases de fallo con severidad, si se reintenta, max_intentos, espera_base_s y motivo_manual.",
 "pipeline.reintentos._PATRON": "Regex que reconoce 'REINTENTO:<n>'.",
 "pipeline.reintentos.PREFIJO": "Prefijo de la marca de aplazamiento (protocolo compartido entre corridas).",
 "pipeline.almacen._GCS": "Regex que reconoce destinos gs://bucket/prefijo.",
 "pipeline.notificacion._QUE_SIGUE": "Frase de 'que sigue' del aviso interno por accion.",
 "pipeline.notificacion._TITULOS": "Titulo del aviso interno por accion.",
 "correr.TIMEOUT_SGDEA_S": "Timeout (s) de cada peticion HTTP a SGDEA (RadicacionService(timeout=...)). radicar_pqrd reintenta hasta 3 veces internamente, asi que multiplica.",
 "correr.CONCURRENCIA": "Correos atendidos a la vez por la corrida (asyncio.Semaphore).",
 "correr.CORREOS_POR_CORRIDA": "Correos que toma cada corrida (default de --n).",
 "correr.TOPE_POR_CORREO_S": "Tope (s) de un solo correo; al vencer se abandona (colgado).",
 "correr.UMBRAL_ETAPA_LENTA_MS": "Desde cuantos ms se emite una etapa como lenta cuando no tiene umbral propio.",
 "correr.UMBRAL_POR_ETAPA_MS": "Umbral de lentitud (ms) por etapa para emitirla en consola/log.",
 "correr.LOGS": "Destino de los informes (disco o gs://bucket/prefijo).",
 "correr.WORKSPACES": "Directorio base de los workspaces por correo (persisten en disco).",
 "pipeline.presupuesto.CADENCIA_S": "Ventana entre corridas del Job (s).",
 "pipeline.presupuesto.FRACCION_DE_LA_VENTANA": "Fraccion de la ventana que puede usar la corrida (total por defecto = 1800*0.66 = 1188 s).",
 "pipeline.presupuesto.RESERVA_S": "Segundos reservados para cerrar servicios y escribir telemetria.",
 "pipeline.presupuesto.MINIMO_POR_CORREO_S": "Por debajo de estos segundos utiles no se empieza un correo nuevo; tambien acota las esperas de reintento del modelo.",
 "pipeline.agente.MAX_ITERACIONES": "Vueltas maximas del agente por correo (cada vuelta = 1 llamada al modelo).",
 "pipeline.compuerta.CONFIANZA_PARA_RADICAR": "Piso general de confianza para radicar un dato (compuerta y filtro del agente).",
 "pipeline.compuerta.PISO_POR_CAMPO": "Piso de confianza por campo que sustituye al general (tipo_solicitud acepta cualquier confianza).",
 "pipeline.compuerta.OBLIGATORIOS": "Campos sin los cuales la compuerta rechaza.",
 "pipeline.extraccion.UMBRAL_CONFIANZA": "Confianza minima para aceptar un hallazgo determinista; por debajo pasa al agente.",
 "pipeline.agente.CABECERA": "Prompt de sistema del agente sin los criterios por campo (instrucciones generales y reglas).",
 "pipeline.agente.CRITERIOS": "Criterios por campo del prompt (solo se envian los de los campos pedidos).",
 "pipeline.modelo.TIMEOUT_DEL_MODELO_MS": "Timeout de una llamada a Vertex (ms).",
 "servidor_mcp.herramientas.CATALOGOS": "Catalogos de codigos validos (tipo_documento, tipo_remitente, sexo, tipo_solicitud).",
}

# Parametros que no son constantes de modulo (literales, campos de dataclass, settings)
EXTRA = [
 # agente / modelo
 {"nodo": "agente", "clave": "pipeline.modelo.ModeloVertex.nombre", "modulo": "pipeline.modelo", "nombre": "ModeloVertex.nombre",
  "tipo": "str", "valor": "gemini-2.5-flash", "descripcion": "Modelo de Vertex usado por agente y revisor (default del dataclass; correr.py no lo pasa).",
  "env": None, "sobrescribible_en_caliente": False,
  "nota_sobrescritura": "No a nivel de modulo (es el default de un campo dataclass). Si en la instancia: orq.modelo.nombre = 'gemini-2.5-pro' (generar lo lee en cada llamada; el informe y el contador tambien usan modelo.nombre)."},
 {"nodo": "agente", "clave": "pipeline.modelo.ModeloVertex.generar.temperature", "modulo": "pipeline.modelo", "nombre": "temperature (literal)",
  "tipo": "float", "valor": 0.1, "descripcion": "Temperatura de generate_content.", "env": None, "sobrescribible_en_caliente": False,
  "nota_sobrescritura": "No: literal dentro de ModeloVertex.generar. Solo sustituyendo el modelo por un envoltorio/subclase."},
 {"nodo": "agente", "clave": "correr.main.ModeloVertex.ubicacion", "modulo": "correr", "nombre": "ubicacion / endpoint_global (literales)",
  "tipo": "dict", "valor": {"ubicacion": "global", "endpoint_global": True, "base_url": "https://aiplatform.googleapis.com"},
  "descripcion": "Ubicacion y endpoint de Vertex con que correr.py construye el modelo (GCP_LOCATION de settings NO se usa).", "env": None,
  "sobrescribible_en_caliente": False, "nota_sobrescritura": "No: literales en correr.main y en ModeloVertex._obtener_cliente; se fijan al construir el cliente."},
 {"nodo": "agente", "clave": "servidor_mcp.herramientas.reportar_hallazgo.evidencia_min", "modulo": "servidor_mcp.herramientas", "nombre": "evidencia minima (literal)",
  "tipo": "int", "valor": 10, "descripcion": "reportar_hallazgo rechaza evidencias de menos de 10 caracteres; la evidencia se corta a 500 y la confianza debe estar en [0,1].",
  "env": None, "sobrescribible_en_caliente": False, "nota_sobrescritura": "No: literal en la funcion."},
 {"nodo": "agente", "clave": "servidor_mcp.herramientas.buscar_en_documentos.largo_patron", "modulo": "servidor_mcp.herramientas", "nombre": "largo maximo del patron (literal)",
  "tipo": "int", "valor": 200, "descripcion": "buscar_en_documentos rechaza patrones de mas de 200 caracteres y los de cuantificador anidado ((a+)+, (a|b)+).",
  "env": None, "sobrescribible_en_caliente": False, "nota_sobrescritura": "No: literal en la funcion."},
 {"nodo": "agente", "clave": "servidor_mcp.herramientas.leer_documento.lineas", "modulo": "servidor_mcp.herramientas", "nombre": "lineas por defecto (default de argumento)",
  "tipo": "int", "valor": 60, "descripcion": "Lineas que lee leer_documento si el modelo no pasa 'lineas' (tope MAX_LINEAS_POR_LECTURA). buscar_en_documentos usa contexto_lineas=1 por defecto.",
  "env": None, "sobrescribible_en_caliente": False, "nota_sobrescritura": "No: valores por defecto de argumentos."},
 # descarga
 {"nodo": "descarga_adjuntos", "clave": "pipeline.orquestador._descargar_adjuntos.inline_min_bytes", "modulo": "pipeline.orquestador", "nombre": "umbral inline (literal)",
  "tipo": "int", "valor": 20000, "descripcion": "Adjuntos isInline con size < 20000 bytes se ignoran (imagenes de firma).",
  "env": None, "sobrescribible_en_caliente": False, "nota_sobrescritura": "No: literal en Orquestador._descargar_adjuntos."},
 # conversion
 {"nodo": "conversion_a_markdown", "clave": "pipeline.materializar.claves_candidatas_de.tope", "modulo": "pipeline.materializar", "nombre": "claves de PDF a probar (literal)",
  "tipo": "int", "valor": 12, "descripcion": "Maximo de numeros (6-12 digitos del asunto/cuerpo) que se prueban como clave de un PDF protegido.",
  "env": None, "sobrescribible_en_caliente": False, "nota_sobrescritura": "No: literal."},
 {"nodo": "conversion_a_markdown", "clave": "env.OMP_THREAD_LIMIT", "modulo": "pipeline.conversion", "nombre": "OMP_THREAD_LIMIT",
  "tipo": "str", "valor": "1", "descripcion": "Hilos de Tesseract; se fija con os.environ.setdefault al importar pipeline.conversion (un valor del entorno manda).",
  "env": "OMP_THREAD_LIMIT", "sobrescribible_en_caliente": True,
  "nota_sobrescritura": "Si: el subproceso de tesseract hereda os.environ en cada OCR, asi que cambiar os.environ['OMP_THREAD_LIMIT'] afecta a las siguientes llamadas."},
 # extraccion
 {"nodo": "extraccion_determinista", "clave": "pipeline.extraccion.extraer_todo.contexto", "modulo": "pipeline.extraccion", "nombre": "contexto del agente (literales)",
  "tipo": "dict", "valor": {"lineas": 14, "caracteres": 1800}, "descripcion": "Encabezado del correo que se pasa al agente: primeras 14 lineas no vacias, maximo 1800 caracteres.",
  "env": None, "sobrescribible_en_caliente": False, "nota_sobrescritura": "No: literales en extraer_todo."},
 # radicacion
 {"nodo": "radicacion", "clave": "pipeline.radicador.armar_payload.oficina_por_defecto", "modulo": "pipeline.radicador", "nombre": "oficina_por_defecto (default de argumento)",
  "tipo": "int", "valor": 15, "descripcion": "Oficina enviada si no se resolvio (15 = Grupo de Atencion Integral y de Servicio al Ciudadano).",
  "env": None, "sobrescribible_en_caliente": False, "nota_sobrescritura": "No: valor por defecto del argumento; Radicador.radicar no lo pasa."},
 {"nodo": "radicacion", "clave": "services.radicacion.radicar_pqrd.max_attempts", "modulo": "services.radicacion", "nombre": "max_attempts (local)",
  "tipo": "int", "valor": 3, "descripcion": "Intentos internos de radicar_pqrd (solo ante 401 con token renovado y ConnectError). crear_trazabilidad y el token usan @http_retry (3 intentos, espera exponencial 2-10 s, solo timeout/conexion).",
  "env": None, "sobrescribible_en_caliente": False, "nota_sobrescritura": "No: variable local / decorador aplicado al importar."},
 {"nodo": "radicacion", "clave": "pipeline.radicador.solicitud_observaciones.cuerpo", "modulo": "pipeline.radicador", "nombre": "cuerpo en observaciones (literal)",
  "tipo": "int", "valor": 2000, "descripcion": "Caracteres del cuerpo limpio que van en solicitudObservaciones.",
  "env": None, "sobrescribible_en_caliente": False, "nota_sobrescritura": "No: literal en armar_payload."},
]

EXTRA += [
 {"nodo": "extraccion_determinista", "clave": f"utils.documento_candidatos.{n}", "modulo": "utils.documento_candidatos", "nombre": n,
  "tipo": "int", "valor": v, "descripcion": d, "env": None, "sobrescribible_en_caliente": True,
  "nota_sobrescritura": "Si: asignada por tupla a nivel de modulo y leida como global dentro de la funcion de candidatos (linea 223-224) en cada llamada.",
  "archivo": "utils/documento_candidatos.py", "linea": l}
 for n, v, d, l in (
  ("MIN_SIN_MARCA", 6, "Digitos minimos de un candidato a documento SIN marca de contexto (CC, cedula...).", 37),
  ("MAX_SIN_MARCA", 11, "Digitos maximos de un candidato a documento SIN marca de contexto.", 37),
  ("MIN_CON_MARCA", 5, "Digitos minimos de un candidato CON marca (copia de MIN_DIGITOS al importar).", 38),
  ("MAX_CON_MARCA", 15, "Digitos maximos de un candidato CON marca (copia de MAX_DIGITOS al importar).", 38))
]

SETTINGS = [
 ("lote", "MODO_TEST", "bool", False, "Con true no ejecuta ninguna escritura: marcar/mover/reenviar/radicar/trazabilidad se graban en CSV bajo TEST_OUTPUT_DIR. Vertex, registro de personas, lecturas de Graph y GCS siguen ocurriendo.", True,
  "Si en la practica: OutlookService y RadicacionService leen settings.MODO_TEST en cada llamada (objeto `config.settings.settings`). Pero SupabaseService decide crear su cliente al construirse, y correr.main lo comprueba al arrancar. No cambiarlo a mitad de corrida."),
 ("lote", "TEST_OUTPUT_DIR", "str", "salidas_prueba", "Carpeta (relativa al cwd) de escrituras.csv y radicaciones.csv en MODO_TEST.", True, "Si: el grabador lo lee al escribir (get_recorder)."),
 ("deduplicacion", "DEDUP_EN_PRUEBAS", "bool", False, "Con MODO_TEST, ejecuta igualmente las consultas de deduplicacion (Graph hilo/asunto, Supabase, Redis). dedup_desactivada() = MODO_TEST and not DEDUP_EN_PRUEBAS.", True, "Si: dedup_desactivada() lee settings en cada consulta."),
 ("aviso_y_cierre", "CORREO_DE_AVISOS", "str", "", "Buzon interno al que se remite el resultado de cada correo. Vacio = no se envia.", True, "Si: Orquestador._avisar lee settings.CORREO_DE_AVISOS en cada correo."),
 ("agente", "GCP_PROJECT", "str", "positiva-sgda-prd-416417", "Proyecto de Vertex AI.", False, "No en caliente: se pasa a ModeloVertex al construirlo en correr.main y queda en el genai.Client cacheado."),
 ("agente", "GCP_LOCATION", "str", "us-east1", "Declarada pero NO usada por el pipeline nuevo (correr.py fuerza ubicacion='global'); solo la usan los servicios legacy.", False, "Sin efecto en el pipeline nuevo."),
 ("lote", "OUTLOOK_FOLDER_ID", "str", "(secreto/obligatorio)", "Carpeta de entrada (bandeja) que lee el Job.", True, "Si antes de main(): se lee al elegir la carpeta."),
 ("lote", "OUTLOOK_USER_EMAIL", "str", "servicioalcliente@positiva.gov.co", "Buzon de Graph (/users/{buzon}).", False, "No: OutlookService.__init__ lo copia a self.user_email y las URL se arman con esa copia (se puede cambiar en la instancia: outlook.user_email = ...)."),
 ("deduplicacion", "OUTLOOK_FOLDER_DUPLICADOS", "str", "AAMkADcyMDJm...AAeYJzX8AAA=", "Carpeta destino de los duplicados.", True, "Si: _archivar_duplicado lee settings en cada correo."),
 ("cierre_outlook", "OUTLOOK_FOLDER_RADICACION_EXITOSA", "str", "AAMkADcyMDJm...AAdL0MCYAAA=", "Carpeta destino de los radicados.", True, "Si: _mover_a_exitosa lee settings en cada correo."),
 ("destino_del_correo", "OUTLOOK_FOLDER_REENVIADO_OTRA_AREA", "str", "AAMkADcyMDJm...AAeYJzX_AAA=", "Carpeta destino de los reenviados.", True, "Si: _reenviar lee settings en cada correo."),
 ("revision_manual", "OUTLOOK_FOLDER_REVISAR_MANUAL", "str", "AAMkADcyMDJm...AAckGdkYAAA=", "Carpeta destino de revision manual.", True, "Si: _a_revision_manual lee settings en cada correo."),
 ("registro_de_personas", "MS_SGDEA_BETA_URL", "str", "https://ms-sgdea-beta.proyectos-3t.tech", "Base del registro de personas (/personas) y de radicacion (/pqrd/radicar-pqrd, /trazabilidad/crear).", False, "Registro: se copia a ConsultaDePersonas.url_base al construirlo. RadicacionService lo lee de settings al llamar."),
 ("radicacion", "SGDEA_TOKEN_URL", "str", "https://sgdea-prod.proyectos-3t.tech/realms/positiva/protocol/openid-connect/token", "Keycloak para el token de SGDEA (grant password).", True, "Se lee al pedir token (cacheado)."),
 ("radicacion", "SGDEA_CLIENT_ID", "str", "cliente-registrar", "client_id de Keycloak.", True, "Se lee al pedir token."),
 ("radicacion", "SGDEA_USERNAME", "str", "AsignadoIA", "Usuario de Keycloak.", True, "Se lee al pedir token."),
]

# ───────────────────────── RESTO ─────────────────────────

HERRAMIENTAS = [
 {"nombre": "listar_documentos", "descripcion": "Inventario del workspace (correo, hilo, adjuntos convertidos) sin contenido.", "parametros": {}, "modulo": "servidor_mcp/herramientas.py", "escribe": False, "efectos": "ninguno", "disponible_para": ["agente", "revisor"]},
 {"nombre": "leer_documento", "descripcion": "Lee un fragmento por rango de lineas o una hoja entera (`pagina`) de un adjunto con secciones '## Pagina N'; el texto pasa por sanear_fragmento (anota sospechas y lo envuelve como datos). Tope MAX_LINEAS_POR_LECTURA lineas y MAX_CARACTERES_RESPUESTA caracteres.", "parametros": {"documento": {"type": "string", "required": True}, "desde": {"type": "integer", "default": 0}, "lineas": {"type": "integer", "default": 60}, "pagina": {"type": "integer", "description": "hoja entera de un adjunto"}}, "modulo": "servidor_mcp/herramientas.py", "escribe": False, "efectos": "ninguno", "disponible_para": ["agente", "revisor"]},
 {"nombre": "buscar_en_documentos", "descripcion": "Busca un patron (texto o regex, max 200 caracteres, sin cuantificadores anidados) sin tildes ni mayusculas, tambien partido por un salto de linea; una cifra de cinco o mas digitos admite separadores de miles. Devuelve hasta MAX_RESULTADOS_BUSQUEDA coincidencias con documento, hoja, linea y contexto saneado.", "parametros": {"patron": {"type": "string", "required": True}, "contexto_lineas": {"type": "integer", "default": 1}}, "modulo": "servidor_mcp/herramientas.py", "escribe": False, "efectos": "ninguno", "disponible_para": ["agente", "revisor"]},
 {"nombre": "consultar_documentos", "descripcion": "Recuperacion por relevancia (BM25) sobre ventanas de unas quince lineas de cada hoja, en texto sin tildes ni mayusculas y con sinonimos del dominio (cedula/cc, trabajador/afiliado...). Devuelve hasta MAX_TRAMOS_POR_CONSULTA tramos que no se solapan, con documento, hoja, linea y puntaje, entre todos MAX_CARACTERES_CONSULTA caracteres.", "parametros": {"pregunta": {"type": "string", "required": True, "description": "palabras clave del dato, max 300 caracteres"}, "maximo": {"type": "integer", "default": 3}}, "modulo": "servidor_mcp/herramientas.py", "escribe": False, "efectos": "ninguno", "disponible_para": ["agente", "revisor"]},
 {"nombre": "consultar_catalogo", "descripcion": "Valores validos de un catalogo (tipo_documento, tipo_remitente, sexo, tipo_solicitud).", "parametros": {"nombre": {"type": "string", "required": True}}, "modulo": "servidor_mcp/herramientas.py", "escribe": False, "efectos": "ninguno", "disponible_para": ["agente"]},
 {"nombre": "validar_documento", "descripcion": "Valida un numero de documento por estructura: longitud por tipo, tipos compatibles, pasaporte alfanumerico, digitos no ASCII, digito de verificacion del NIT. Calculo puro.", "parametros": {"numero": {"type": "string", "required": True}, "tipo": {"type": "string", "default": ""}}, "modulo": "servidor_mcp/herramientas.py", "escribe": False, "efectos": "ninguno", "disponible_para": ["agente"]},
 {"nombre": "consultar_persona", "descripcion": "Datos de una persona en el registro de SGDEA por documento (nombre, telefono, direccion, geografia, genero crudo). 'No encontrado' no invalida el documento.", "parametros": {"numero_documento": {"type": "string", "required": True}, "tipo_documento": {"type": "string", "description": "codigo o sigla; sin el se pregunta por CC"}}, "modulo": "servidor_mcp/herramientas.py", "escribe": False, "efectos": "GET {MS_SGDEA_BETA_URL}/personas (red, solo lectura) via Orquestador._consultar_persona; un fallo se registra en la bitacora (paso SGDEA_CONSULTA, puede ser FATAL si no se clasifica).", "disponible_para": ["agente"]},
 {"nombre": "reportar_hallazgo", "descripcion": "Registra un campo resuelto (solo acumula en Contexto.hallazgos; el filtro se aplica al terminar el bucle).", "parametros": {"campo": {"type": "string", "enum": ["tipo_remitente", "tipo_documento_afectado", "numero_documento_afectado", "nombre_afectado", "tipo_documento_remitente", "numero_documento_remitente", "nombre_remitente", "tipo_solicitud", "sexo_afectado", "telefono_afectado", "direccion_afectado"], "required": True}, "valor": {"type": "string", "required": True}, "evidencia": {"type": "string", "required": True, "description": "documento y texto de donde sale (min 10 caracteres)"}, "confianza": {"type": "number", "required": True, "description": "0..1"}}, "modulo": "servidor_mcp/herramientas.py", "escribe": False, "efectos": "escribe solo en memoria (ctx.hallazgos)", "disponible_para": ["agente"]},
 {"nombre": "pedir_revision", "descripcion": "Delega en el revisor (segundo agente de solo lectura) la comprobacion de los valores resueltos + reportados; devuelve el veredicto por campo (OK / CAMBIAR / SIN RESPALDO).", "parametros": {}, "modulo": "servidor_mcp/herramientas.py", "escribe": False, "efectos": "Llama al modelo hasta MAX_VUELTAS_DEL_REVISOR veces (Vertex, se factura); esos tokens no se suman al Resultado.", "disponible_para": ["agente"]},
]

PROVEEDORES = [
 {"id": "graph", "nombre": "Microsoft Graph (Outlook, buzon servicioalcliente@positiva.gov.co)", "tipo": "buzon", "modulo": "services/outlook.py (OutlookService)",
  "configuracion": ["MICROSOFT_TENANT_ID", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "OUTLOOK_REFRESH_TOKEN (no usado: grant client_credentials)", "OUTLOOK_USER_EMAIL", "OUTLOOK_FOLDER_ID", "OUTLOOK_FOLDER_RADICACION_EXITOSA", "OUTLOOK_FOLDER_DUPLICADOS", "OUTLOOK_FOLDER_REVISAR_MANUAL", "OUTLOOK_FOLDER_REENVIADO_OTRA_AREA", "CORREO_DE_AVISOS"],
  "notas": "httpx.AsyncClient(timeout=60); 401 invalida token y reintenta una vez; 429 hasta INTENTOS_POR_LIMITE_DE_TASA. En MODO_TEST las escrituras se graban en escrituras.csv y no se envian.",
  "operaciones": [
   {"nombre": "_get_access_token", "metodo": "POST", "ruta": "https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token", "escribe": False, "usado_por_pipeline": True},
   {"nombre": "get_emails(folder_id, limit=50)", "metodo": "GET", "ruta": "/v1.0/users/{buzon}/mailFolders/{folder_id}/messages ($top=limit*3)", "escribe": False, "usado_por_pipeline": True},
   {"nombre": "hilo_ya_radicado(conversation_id)", "metodo": "GET", "ruta": "/v1.0/users/{buzon}/messages ($filter conversationId)", "escribe": False, "usado_por_pipeline": True, "modo_test": "None si dedup_desactivada()"},
   {"nombre": "buscar_etiqueta_ent_por_asunto(asunto, email)", "metodo": "GET", "ruta": "/v1.0/users/{buzon}/messages ($filter from + $search subject, ConsistencyLevel: eventual) y GET /messages/{id}?$select=body + adjuntos", "escribe": False, "usado_por_pipeline": True, "modo_test": "None si dedup_desactivada()"},
   {"nombre": "get_email_by_id(message_id)", "metodo": "GET", "ruta": "/v1.0/users/{buzon}/messages/{id}", "escribe": False, "usado_por_pipeline": True},
   {"nombre": "get_attachments(message_id)", "metodo": "GET", "ruta": "/v1.0/users/{buzon}/messages/{id}/attachments ($select=id,name,contentType,size,isInline)", "escribe": False, "usado_por_pipeline": True},
   {"nombre": "download_attachment(message_id, attachment_id)", "metodo": "GET", "ruta": "/v1.0/users/{buzon}/messages/{id}/attachments/{aid}", "escribe": False, "usado_por_pipeline": True},
   {"nombre": "marcar(message_id, marca, quitar_de_proceso)", "metodo": "GET+PATCH", "ruta": "/v1.0/users/{buzon}/messages/{id} ($select=id,categories; PATCH {categories} con If-Match: ETag; 412 -> OtraCorridaLoTomo)", "escribe": True, "usado_por_pipeline": True, "modo_test": "registrar('MARCAR') y devuelve fusionar([], marca)"},
   {"nombre": "move_email(message_id, destination_folder_id)", "metodo": "POST", "ruta": "/v1.0/users/{buzon}/messages/{id}/move", "escribe": True, "usado_por_pipeline": True, "modo_test": "registrar('MOVER'), devuelve ''"},
   {"nombre": "forward_email(message_id, to_email, comment)", "metodo": "POST", "ruta": "/v1.0/users/{buzon}/messages/{id}/forward", "escribe": True, "usado_por_pipeline": True, "modo_test": "registrar('REENVIAR')"},
   {"nombre": "etiquetar_correo(message_id, etiqueta)", "metodo": "PATCH", "ruta": "/v1.0/users/{buzon}/messages/{id}", "escribe": True, "usado_por_pipeline": False},
   {"nombre": "responder_correo / mover_hilo_completo / list_folders / get_conversation_message_ids", "metodo": "POST/GET", "ruta": "varios", "escribe": True, "usado_por_pipeline": False}]},
 {"id": "sgdea_token", "nombre": "SGDEA - Keycloak (token)", "tipo": "sgdea", "modulo": "services/radicacion.py (RadicacionService._get_token)",
  "configuracion": ["SGDEA_TOKEN_URL", "SGDEA_CLIENT_ID", "SGDEA_CLIENT_SECRET", "SGDEA_USERNAME", "SGDEA_PASSWORD"],
  "notas": "grant_type=password, timeout 60 s, cache con margen 60 s, @http_retry 3 intentos (2-10 s) ante timeout/conexion. En MODO_TEST no se llega a pedir (radicar/trazabilidad cortan antes).",
  "operaciones": [{"nombre": "_get_token(force_refresh=False)", "metodo": "POST", "ruta": "{SGDEA_TOKEN_URL}", "escribe": False, "usado_por_pipeline": True}]},
 {"id": "sgdea_radicacion", "nombre": "SGDEA - radicacion (ms-sgdea-beta)", "tipo": "sgdea", "modulo": "services/radicacion.py (RadicacionService)",
  "configuracion": ["MS_SGDEA_BETA_URL", "TIMEOUT_SGDEA_S (correr.py)", "MODO_TEST", "TEST_OUTPUT_DIR"],
  "notas": "MS_DOCUM_SGDEA_URL esta declarada pero no se usa. Cabeceras Origin/Referer https://docu.positiva.gov.co.",
  "operaciones": [
   {"nombre": "radicar_pqrd(payload, attachment_files)", "metodo": "POST", "ruta": "{MS_SGDEA_BETA_URL}/pqrd/radicar-pqrd (multipart: form + files)", "escribe": True, "idempotente": False, "usado_por_pipeline": True, "modo_test": "graba radicaciones.csv y devuelve id_radicado 'DRY-RUN-ENT'"},
   {"nombre": "crear_trazabilidad(payload)", "metodo": "POST", "ruta": "{MS_SGDEA_BETA_URL}/trazabilidad/crear (JSON)", "escribe": True, "usado_por_pipeline": True, "modo_test": "registrar('TRAZABILIDAD')"},
   {"nombre": "asignar_pqrd(pqrd_id, payload)", "metodo": "POST", "ruta": "{MS_SGDEA_BETA_URL}/pqrd/asignar/{pqrd_id}", "escribe": True, "usado_por_pipeline": False},
   {"nombre": "download_file(url)", "metodo": "POST", "ruta": "{AGENTS_IA_URL}/download", "escribe": False, "usado_por_pipeline": False}]},
 {"id": "registro_personas", "nombre": "Registro de personas SGDEA (ms-sgdea-beta)", "tipo": "registro_personas", "modulo": "pipeline/personas.py (ConsultaDePersonas)",
  "configuracion": ["MS_SGDEA_BETA_URL"], "notas": "httpx sincrono, sin autenticacion, timeout TIEMPO_MAXIMO; sin guarda de MODO_TEST (se consulta tambien con --seco y en el sandbox).",
  "operaciones": [{"nombre": "consultar(numero_documento, tipo_documento=None)", "metodo": "GET", "ruta": "{MS_SGDEA_BETA_URL}/personas?tipoDocumento={sigla}&numeroDocumento={numero}", "escribe": False, "usado_por_pipeline": True}]},
 {"id": "agents_ia", "nombre": "agents-ia (descarga de archivos)", "tipo": "asignacion", "modulo": "services/radicacion.py (download_file)",
  "configuracion": ["AGENTS_IA_URL"], "notas": "Solo legacy (orchestrator/pqrd_orchestrator.py).",
  "operaciones": [{"nombre": "download_file(url)", "metodo": "POST", "ruta": "{AGENTS_IA_URL}/download", "escribe": False, "usado_por_pipeline": False}]},
 {"id": "asignacion_ia", "nombre": "Configuracion de asignacion IA (ms-sgdea-prd)", "tipo": "asignacion", "modulo": "services/asignacion_ia_client.py (AsignacionIAClient)",
  "configuracion": ["CONFIGURACION_ASIGNACION_IA_URL", "SGDEA_TOKEN_URL", "SGDEA_CLIENT_ID", "SGDEA_CLIENT_SECRET", "SGDEA_USERNAME", "SGDEA_PASSWORD"],
  "notas": "Ninguna operacion se alcanza desde el pipeline nuevo (la oficina sale de la tabla local OFFICE_MAPPING). incrementar_counter no tiene guarda de MODO_TEST. Bug: referencia a token_url no definida en la rama de error.",
  "operaciones": [
   {"nombre": "resolver_asignacion(tipo_solicitud_id, departamento_id)", "metodo": "GET", "ruta": "{base}/configuracion-asignacion-ia/resolver?tipoSolicitudId=&departamentoId=", "escribe": False, "usado_por_pipeline": False},
   {"nombre": "obtener_oficina_por_tipo(tipo_solicitud_id)", "metodo": "GET", "ruta": "{base}/configuracion-asignacion-ia/tipo-solicitud-oficina?tipoSolicitudId=", "escribe": False, "usado_por_pipeline": False},
   {"nombre": "incrementar_counter(oficina_id, departamento_id)", "metodo": "POST", "ruta": "{base}/configuracion-asignacion-ia/resolver/increment", "escribe": True, "usado_por_pipeline": False}]},
 {"id": "supabase", "nombre": "Supabase (auditoria radicados_pqrd)", "tipo": "registro_personas", "modulo": "services/supabase_client.py (SupabaseService)",
  "configuracion": ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "MODO_TEST", "DEDUP_EN_PRUEBAS"],
  "notas": "Tipo real: auditoria/base de datos. Con MODO_TEST el cliente no se crea (_client=None). El pipeline nuevo solo lee (capa 6 de deduplicacion).",
  "operaciones": [
   {"nombre": "email_exists(correo_remitente, asunto, recibido)", "metodo": "SELECT", "ruta": "table radicados_pqrd .select(numero_radicado).eq(correo_remitente).eq(asunto)[.gte(fecha_radicacion, recibido-1d)].limit(1)", "escribe": False, "usado_por_pipeline": True, "modo_test": "False si dedup_desactivada()"},
   {"nombre": "save_radicado_record(...)", "metodo": "INSERT", "ruta": "table radicados_pqrd", "escribe": True, "usado_por_pipeline": False},
   {"nombre": "radicado_exists_by_doc / get_today_stats", "metodo": "SELECT", "ruta": "table radicados_pqrd", "escribe": False, "usado_por_pipeline": False}]},
 {"id": "redis", "nombre": "Redis Cloud (hashes de correo)", "tipo": "cache", "modulo": "services/redis_client.py (RedisService)",
  "configuracion": ["REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD"],
  "notas": "No se usa en el pipeline nuevo: correr.py construye el Orquestador sin cache=, asi que la capa 5 de deduplicacion y registrar_radicado no tocan Redis. Solo legacy.",
  "operaciones": [
   {"nombre": "email_hash_exists(hash)", "metodo": "GET", "ruta": "redis GET <clave del hash>", "escribe": False, "usado_por_pipeline": False},
   {"nombre": "save_email_hash(hash, ttl_seconds=2592000)", "metodo": "SETEX", "ruta": "redis SETEX <clave> ttl '1'", "escribe": True, "usado_por_pipeline": False}]},
 {"id": "cloud_storage", "nombre": "Google Cloud Storage (informes)", "tipo": "almacenamiento", "modulo": "pipeline/almacen.py (+ services/cloud_storage.py legacy: descargas Drive/OneDrive)",
  "configuracion": ["RUTA_LOGS (gs://bucket/prefijo activa GCS; si no, disco)", "ADC"],
  "notas": "Se escribe siempre (MODO_TEST no lo afecta). services/cloud_storage.CloudStorageDownloader no es GCS y solo lo usa el legacy.",
  "operaciones": [
   {"nombre": "escribir(destino, contenido)", "metodo": "PUT (upload_from_string)", "ruta": "gs://{bucket}/{prefijo}/ejecuciones/{dia}/{hora}-{id}.txt", "escribe": True, "usado_por_pipeline": True},
   {"nombre": "anexar(destino, contenido)", "metodo": "PUT (objeto nuevo)", "ruta": "gs://{bucket}/{prefijo}/diario/{dia}.txt.partes/<ts>-<uuid8>.txt y serie.tsv.partes/...", "escribe": True, "usado_por_pipeline": True},
   {"nombre": "leer(destino)", "metodo": "GET/LIST", "ruta": "blob + list_blobs(prefix=...partes)", "escribe": False, "usado_por_pipeline": False}]},
 {"id": "vertex", "nombre": "Vertex AI (Gemini)", "tipo": "modelo", "modulo": "pipeline/modelo.py (ModeloVertex)",
  "configuracion": ["GCP_PROJECT", "ADC o `gcloud auth print-access-token`", "TIMEOUT_DEL_MODELO_MS", "bandera --vertex"],
  "notas": "Modelo gemini-2.5-flash, temperature 0.1, location 'global', base_url https://aiplatform.googleapis.com. Sin guarda de MODO_TEST: siempre llama y se factura. Sin reintentos propios (los aplica el orquestador).",
  "operaciones": [{"nombre": "generar(sistema, mensajes, herramientas) -> Turno", "metodo": "POST", "ruta": "https://aiplatform.googleapis.com/v1/projects/{GCP_PROJECT}/locations/global/publishers/google/models/gemini-2.5-flash:generateContent (via google-genai models.generate_content)", "escribe": False, "usado_por_pipeline": True}]},
]

DESENLACES = [
 {"accion": "radicado", "descripcion": "Se radico en SGDEA (o otra corrida ya lo habia radicado, o 'DRY-RUN'/'DRY-RUN-ENT' en pruebas): etiqueta ENT.../RADICADO:..., carpeta Radicacion exitosa."},
 {"accion": "duplicado", "descripcion": "Ya estaba radicado (capas 1-6 de deduplicacion): se retiran marcas de proceso y se mueve a Duplicados."},
 {"accion": "reenviado", "descripcion": "No es PQRD de este canal: se reenvio a Notificaciones Judiciales o Medicina Laboral, marca REENVIADO:<destino>, carpeta Reenviado otra area (si el reenvio falla queda en la bandeja)."},
 {"accion": "manual", "descripcion": "Necesita una persona: marca REVISION-MANUAL:<motivo> y carpeta Revisar manual. Lleva motivo_manual."},
 {"accion": "aplazado", "descripcion": "Fallo de infraestructura (motivo aplazable): marca REINTENTO:N y se queda en la bandeja para la siguiente corrida, hasta MAX_CICLOS."},
 {"accion": "en_proceso", "descripcion": "Otra corrida gano el reclamo (412): no se toca ni se avisa."},
 {"accion": "sin_reclamar", "descripcion": "No se pudo escribir la marca de reclamo: no se toca ni se avisa; la siguiente corrida lo ve como nuevo."},
 {"accion": "error", "descripcion": "Valor inicial de Resultado.accion; no deberia quedar al cerrar (todas las ramas lo sustituyen)."},
 {"accion": "sin_tomar", "descripcion": "Nivel lote: el presupuesto se agoto antes de empezar el correo (no pasa por procesar ni aparece en el informe por correo; se cuenta en el resumen)."},
 {"accion": "colgado", "descripcion": "Nivel lote: procesar supero TOPE_POR_CORREO_S y se abandono; conserva EN-PROCESO:N."},
]

VARIABLES = [
 ("MICROSOFT_CLIENT_ID", "Graph: client_id (obligatoria).", True), ("MICROSOFT_CLIENT_SECRET", "Graph: secreto (obligatoria).", True),
 ("MICROSOFT_TENANT_ID", "Graph: tenant (obligatoria).", True), ("OUTLOOK_REFRESH_TOKEN", "Obligatoria por settings pero no usada (grant client_credentials).", True),
 ("OUTLOOK_FOLDER_ID", "Carpeta de entrada que lee el Job (obligatoria).", True),
 ("SUPABASE_URL", "Supabase (auditoria, capa 6 de deduplicacion) (obligatoria).", True), ("SUPABASE_SERVICE_ROLE_KEY", "Supabase: clave service role (obligatoria).", True),
 ("SGDEA_CLIENT_SECRET", "Keycloak SGDEA: secreto del cliente (obligatoria).", True), ("SGDEA_PASSWORD", "Keycloak SGDEA: contrasena de AsignadoIA (obligatoria).", True),
 ("MODO_TEST", "true = no escribe en ningun sistema (CSV en TEST_OUTPUT_DIR). Default false.", False),
 ("TEST_OUTPUT_DIR", "Carpeta de los CSV de MODO_TEST. Default salidas_prueba.", False),
 ("DEDUP_EN_PRUEBAS", "Ejecuta la deduplicacion durante MODO_TEST. Default false.", False),
 ("CORREO_DE_AVISOS", "Buzon interno de avisos; vacio = no envia.", False),
 ("GCP_PROJECT", "Proyecto de Vertex. Default positiva-sgda-prd-416417.", False),
 ("GCP_LOCATION", "Declarada; el pipeline nuevo no la usa (usa 'global').", False),
 ("REDIS_PASSWORD", "Redis (solo legacy).", True), ("REDIS_HOST", "Redis (solo legacy).", False), ("REDIS_PORT", "Redis (solo legacy).", False),
 ("SGDEA_TOKEN_URL", "URL de token Keycloak.", False), ("SGDEA_CLIENT_ID", "client_id Keycloak (cliente-registrar).", False), ("SGDEA_USERNAME", "Usuario Keycloak (AsignadoIA).", False),
 ("MS_DOCUM_SGDEA_URL", "Declarada, no usada.", False), ("MS_SGDEA_BETA_URL", "Registro de personas y endpoints de radicacion/trazabilidad.", False),
 ("AGENTS_IA_URL", "agents-ia (solo legacy).", False), ("CONFIGURACION_ASIGNACION_IA_URL", "Asignacion IA (no usada por el pipeline nuevo).", False),
 ("OUTLOOK_USER_EMAIL", "Buzon de Graph.", False),
 ("OUTLOOK_FOLDER_DUPLICADOS", "Carpeta de duplicados.", False), ("OUTLOOK_FOLDER_ERROR_RADICAR", "Declarada, no usada por el pipeline nuevo.", False),
 ("OUTLOOK_FOLDER_RADICACION_EXITOSA", "Carpeta de radicados.", False), ("OUTLOOK_FOLDER_REENVIADO_OTRA_AREA", "Carpeta de reenviados.", False),
 ("OUTLOOK_FOLDER_REVISAR_MANUAL", "Carpeta de revision manual.", False),
 ("CONCURRENCIA", "Correos a la vez (correr.py, default 5; leida al importar).", False),
 ("CORREOS_POR_CORRIDA", "Correos por corrida (default 50; leida al importar).", False),
 ("TOPE_POR_CORREO_S", "Tope por correo en s (default 600; leida al importar).", False),
 ("PRESUPUESTO_S", "Presupuesto de la corrida en s (default 1188 = 1800*0.66; leida al arrancar main).", False),
 ("UMBRAL_ETAPA_LENTA_MS", "Umbral general de etapa lenta (default 5000; leida al importar).", False),
 ("RUTA_LOGS", "Destino de informes, disco o gs:// (default <motor>/logs).", False),
 ("RUTA_WORKSPACE", "Base de los workspaces (default <motor>/workspace).", False),
 ("MAX_VUELTAS_AGENTE", "Vueltas del agente (default 5; leida al importar pipeline.agente).", False),
 ("MAX_ADJUNTOS_POR_CORREO", "Tope de adjuntos (default 15; leida al importar pipeline.volumen).", False),
 ("MAX_PAGINAS_POR_CORREO", "Tope de paginas (default 40).", False),
 ("MAX_MEGABYTES_POR_CORREO", "Tope de MB (default 25).", False),
 ("FORMATO_LOG", "'json' o 'texto' fuerza el formato de pipeline.salida.emitir (leida en cada emision).", False),
 ("K_SERVICE / CLOUD_RUN_JOB / CLOUD_RUN_EXECUTION", "Si alguna existe, emitir escribe JSON de una linea (Cloud Logging).", False),
 ("OMP_THREAD_LIMIT", "Hilos de Tesseract (setdefault '1' al importar pipeline.conversion).", False),
 ("GOOGLE_APPLICATION_CREDENTIALS (ADC)", "Credenciales de Vertex y GCS via google.auth.default.", True),
]


def resumir(valor):
    s = json.dumps(valor, ensure_ascii=False)
    if len(s) <= TOPE:
        return valor, None
    info = {"resumido": True, "tamano_caracteres": len(s)}
    if isinstance(valor, list):
        info["elementos"] = len(valor)
        muestra = []
        for x in valor:
            muestra.append(x)
            if len(json.dumps(muestra, ensure_ascii=False)) > 1500:
                muestra.pop()
                break
        return muestra, info
    if isinstance(valor, dict):
        info["elementos"] = len(valor)
        if "regex" in valor:
            return {"regex": valor["regex"][:1500] + " …", "flags": valor.get("flags", "")}, info
        muestra = {}
        for k, x in valor.items():
            muestra[k] = x
            if len(json.dumps(muestra, ensure_ascii=False)) > 1500:
                muestra.pop(k)
                break
        return muestra, info
    return str(valor)[:1500] + " …", info


def armar_param(clave, e, nodo):
    m, n = clave.rsplit(".", 1)
    if clave in VALOR_MANUAL:
        valor, tipo = VALOR_MANUAL[clave]
    else:
        valor, tipo = e["valor"], e["tipo"]
    if tipo == "expr":
        tipo = "str"
    es_regex = False
    if tipo == "regex":
        es_regex, tipo = True, "dict"
    valor, info = resumir(valor)
    sob, alc, nota = e["auto_sobrescribible"], e["auto_alcance"], e["auto_nota"]
    if clave in OVERRIDE:
        sob, nota = OVERRIDE[clave]
        alc = "total" if sob else ("parcial" if nota.startswith("Parcial") else
                                   "antes_del_primer_uso" if nota.startswith("Antes del primer uso") else "ninguno")
    desc = DESC.get(clave) or e["comentario"] or ""
    if len(desc) > 600:
        desc = desc[:600].rsplit(" ", 1)[0] + " …"
    p = {"clave": clave, "modulo": m, "nombre": n, "tipo": tipo, "valor": valor,
         "descripcion": desc, "env": ENV.get(clave), "sobrescribible_en_caliente": bool(sob),
         "nota_sobrescritura": nota, "alcance_sobrescritura": alc,
         "archivo": e["archivo"], "linea": e["linea"]}
    if es_regex:
        p["es_regex"] = True
    if info:
        p["valor_resumido"] = info
    if not e.get("evaluado") and clave not in VALOR_MANUAL:
        p["valor_es_expresion_fuente"] = True
    return p


def main():
    por_nodo = {nd["id"]: [] for nd in NODOS}
    usados = set()
    for clave, e in C.items():
        m = e["modulo"]
        if clave in EXCLUIR:
            continue
        nodo = NODO_POR_CLAVE.get(clave) or NODO_POR_MODULO.get(m)
        if not nodo:
            continue
        por_nodo[nodo].append(armar_param(clave, e, nodo))
        usados.add(clave)

    # Constantes no detectadas por el escaner (asignacion por tupla)
    agente_src = open(os.path.join(ROOT, "pipeline", "agente.py"), encoding="utf-8").read()
    texto = C["pipeline.agente._TEXTO_SISTEMA"]["valor"]
    cab, _, resto = texto.partition("CRITERIOS POR CAMPO")
    import re
    crit = {}
    for bloque in re.split(r"\n(?=[a-z_]+ —)", resto.strip("\n")):
        nom = bloque.split(" —")[0].strip()
        if nom:
            crit[nom] = bloque.rstrip()
    for clave, valor, tipo in (("pipeline.agente.CABECERA", cab.rstrip(), "str"), ("pipeline.agente.CRITERIOS", crit, "dict")):
        e = {"valor": valor, "tipo": tipo, "comentario": "", "archivo": "pipeline/agente.py", "linea": 315,
             "evaluado": True, "auto_sobrescribible": True, "auto_alcance": "total", "auto_nota": ""}
        p = armar_param(clave, e, "agente")
        if tipo == "dict":
            # Cada criterio cabe; se conserva completo campo por campo aunque el total pase del tope
            p["valor"] = crit
            p.pop("valor_resumido", None)
            p["valor_nota"] = f"{len(json.dumps(crit, ensure_ascii=False))} caracteres en total; se incluye completo porque cada criterio es editable por separado"
        por_nodo["agente"].append(p)

    # Copias en modulos consumidores que SI son sobrescribibles alli
    copias = [
     ("enrutamiento", "pipeline.enrutamiento", "JUDICIAL_KEYWORD_PATTERNS", "business_rules.clasificacion_remitente.JUDICIAL_KEYWORD_PATTERNS",
      "Lista de palabras/regex judiciales que usa el enrutamiento (copia local hecha al importar). Se compilan con \\b delante en cada llamada."),
     ("enrutamiento", "pipeline.enrutamiento", "DESTINO_NOTIFICACIONES_JUDICIALES", "business_rules.clasificacion_remitente.DESTINO_NOTIFICACIONES_JUDICIALES",
      "Buzon al que se reenvia lo judicial y lo de entes de control (copia local)."),
     ("enrutamiento", "pipeline.enrutamiento", "DESTINO_MEDICINA_LABORAL", "business_rules.clasificacion_remitente.DESTINO_MEDICINA_LABORAL",
      "Buzon al que se reenvia lo de Medicina Laboral (copia local)."),
     ("compuerta", "pipeline.compuerta", "MIN_DIGITOS", "utils.tipos_documento.MIN_DIGITOS", "Minimo de digitos de cualquier documento (copia en la compuerta)."),
     ("compuerta", "pipeline.compuerta", "MAX_DIGITOS", "utils.tipos_documento.MAX_DIGITOS", "Maximo de digitos de cualquier documento (copia en la compuerta)."),
     ("compuerta", "pipeline.compuerta", "TIPOS_ALFANUMERICOS", "utils.tipos_documento.TIPOS_ALFANUMERICOS", "Tipos que admiten letras (11 Pasaporte, 8 Sociedad extranjera) (copia en la compuerta)."),
     ("compuerta", "pipeline.compuerta", "TIPOS_DE_ORGANIZACION", "utils.tipos_documento.TIPOS_DE_ORGANIZACION", "Tipos de documento de organizacion (10 NIT, 8 SE, 9 Fideicomiso) (copia en la compuerta)."),
    ]
    for nodo, mod, nom, origen, desc in copias:
        e = C[origen]
        valor, tipo = VALOR_MANUAL.get(origen, (e["valor"], e["tipo"]))
        valor, info = resumir(valor)
        p = {"clave": f"{mod}.{nom}", "modulo": mod, "nombre": nom, "tipo": "list" if isinstance(valor, list) else tipo,
             "valor": valor, "descripcion": desc, "env": None, "sobrescribible_en_caliente": True,
             "nota_sobrescritura": f"Si: {mod} hizo `from {origen.rsplit('.',1)[0]} import {nom}` al importar y sus funciones leen ese nombre como global en cada llamada; setattr({mod}, '{nom}', v) cambia el comportamiento de {mod} (no el de otros modulos que tengan su propia copia).",
             "alcance_sobrescritura": "total", "archivo": mod.replace('.', '/') + ".py", "linea": None, "copia_de": origen}
        if info:
            p["valor_resumido"] = info
        por_nodo[nodo].append(p)

    for x in EXTRA:
        x = dict(x)
        nodo = x.pop("nodo")
        x.setdefault("alcance_sobrescritura", "total" if x["sobrescribible_en_caliente"] else "ninguno")
        por_nodo[nodo].append(x)

    for nodo, nom, tipo, valor, desc, sob, nota in SETTINGS:
        por_nodo[nodo].append({"clave": f"config.settings.settings.{nom}", "modulo": "config.settings", "nombre": f"settings.{nom}",
                               "tipo": tipo, "valor": valor, "descripcion": desc, "env": nom,
                               "sobrescribible_en_caliente": sob,
                               "nota_sobrescritura": nota + " (Es atributo de la instancia pydantic `config.settings.settings`, no constante de modulo; se cambia con setattr(config.settings.settings, '" + nom + "', v).)",
                               "alcance_sobrescritura": "total" if sob else "ninguno", "archivo": "config/settings.py", "linea": None})

    nodos = []
    for nd in NODOS:
        nd = dict(nd)
        nd["parametros"] = sorted(por_nodo[nd["id"]], key=lambda p: (not p["sobrescribible_en_caliente"], p["clave"]))
        nodos.append(nd)
    nodos.extend(TERMINALES)

    # Catalogos
    mun = json.load(open(os.path.join(ROOT, "business_rules", "municipios.json"), encoding="utf-8"))
    total_mun = sum(len(v) for v in mun.values())
    def tam(clave):
        v = C[clave]["valor"]
        return len(v) if isinstance(v, (list, dict)) else 1
    catalogos = [
     {"nombre": "OFFICE_MAPPING", "modulo": "business_rules/office_mapping.py", "descripcion": "tipo_solicitud -> oficina (get_office_id). Fuente de la oficina y de la correccion en compuerta.", "tamano": tam("business_rules.office_mapping.OFFICE_MAPPING"), "valor": C["business_rules.office_mapping.OFFICE_MAPPING"]["valor"]},
     {"nombre": "TIPO_SOLICITUD_NOMBRES", "modulo": "business_rules/office_mapping.py", "descripcion": "Catalogo tipo_solicitud (codigo -> nombre); copiado a herramientas.CATALOGOS['tipo_solicitud'] al importar.", "tamano": tam("business_rules.office_mapping.TIPO_SOLICITUD_NOMBRES"), "valor": C["business_rules.office_mapping.TIPO_SOLICITUD_NOMBRES"]["valor"]},
     {"nombre": "OFFICE_NAMES", "modulo": "business_rules/office_mapping.py", "descripcion": "oficina -> nombre (aviso y trazabilidad).", "tamano": tam("business_rules.office_mapping.OFFICE_NAMES"), "valor": C["business_rules.office_mapping.OFFICE_NAMES"]["valor"]},
     {"nombre": "CATALOGOS (herramientas)", "modulo": "servidor_mcp/herramientas.py", "descripcion": "tipo_documento, tipo_remitente, sexo, tipo_solicitud: codigos validos para agente y compuerta.", "tamano": 4},
     {"nombre": "TIPO_DOCUMENTO_CODIGO / CODIGO_TO_TIPO_DOCUMENTO", "modulo": "models/enums.py", "descripcion": "Codigo SGDEA <-> sigla interna del tipo de documento.", "tamano": tam("models.enums.TIPO_DOCUMENTO_CODIGO"), "valor": C["models.enums.TIPO_DOCUMENTO_CODIGO"]["valor"]},
     {"nombre": "TIPO_PQRD_NOMBRE", "modulo": "models/enums.py", "descripcion": "Tipos de PQRD (el canal solo radica 1 Solicitud).", "tamano": tam("models.enums.TIPO_PQRD_NOMBRE"), "valor": C["models.enums.TIPO_PQRD_NOMBRE"]["valor"]},
     {"nombre": "LONGITUD_POR_TIPO", "modulo": "utils/tipos_documento.py", "descripcion": "Rango de digitos por tipo de documento (compuerta, extractor, validar_documento).", "tamano": 8, "valor": C["utils.tipos_documento.LONGITUD_POR_TIPO"]["valor"]},
     {"nombre": "DEPARTAMENTOS_MAP", "modulo": "business_rules/geo_mapping.py", "descripcion": "Nombre de departamento -> id (geografia y default Bogota=6).", "tamano": tam("business_rules.geo_mapping.DEPARTAMENTOS_MAP")},
     {"nombre": "CAPITAL_MUNICIPIO", "modulo": "business_rules/geo_mapping.py", "descripcion": "Departamento -> municipio capital.", "tamano": tam("business_rules.geo_mapping.CAPITAL_MUNICIPIO")},
     {"nombre": "municipios.json", "modulo": "business_rules/municipios.json (pipeline.geografia.CATALOGO)", "descripcion": f"Municipios por departamento (divipola, id, nombre) para extraer_geografia: {len(mun)} departamentos.", "tamano": total_mun},
     {"nombre": "SIGLA_DEL_ENDPOINT", "modulo": "pipeline/personas.py", "descripcion": "Codigo de tipo de documento -> sigla del endpoint /personas.", "tamano": 9},
     {"nombre": "MOTIVOS_MANUAL", "modulo": "pipeline/telemetria.py", "descripcion": "Motivos de revision manual -> descripcion para el informe.", "tamano": tam("pipeline.telemetria.MOTIVOS_MANUAL"), "valor": C["pipeline.telemetria.MOTIVOS_MANUAL"]["valor"]},
     {"nombre": "MOTIVO_PARA_EL_CORREO", "modulo": "pipeline/notificacion.py", "descripcion": "Motivo -> frase del aviso interno.", "tamano": tam("pipeline.notificacion.MOTIVO_PARA_EL_CORREO")},
     {"nombre": "CLASES (errores)", "modulo": "pipeline/errores.py", "descripcion": "Clases de fallo con severidad, reintentos, espera base y motivo manual.", "tamano": tam("pipeline.errores.CLASES")},
     {"nombre": "_PATRONES (saneamiento)", "modulo": "pipeline/saneamiento.py", "descripcion": "Patrones de inyeccion con severidad.", "tamano": tam("pipeline.saneamiento._PATRONES")},
     {"nombre": "_TIPO_SOLICITUD_DECLARADO", "modulo": "pipeline/extraccion.py", "descripcion": "Regex -> tipo_solicitud (gana la primera).", "tamano": tam("pipeline.extraccion._TIPO_SOLICITUD_DECLARADO")},
     {"nombre": "FUNCIONARIO_DIRECTORY / CONFIG_SUCURSALES / SUCURSAL_MAP", "modulo": "business_rules/funcionario_directory.py, sucursal_config.py", "descripcion": "Asignacion de funcionarios por sucursal: SOLO legacy, el pipeline nuevo no asigna.", "tamano": tam("business_rules.funcionario_directory.FUNCIONARIO_DIRECTORY")},
     {"nombre": "TARIFAS_USD_POR_MILLON", "modulo": "pipeline/costo.py", "descripcion": "Tarifas por modelo para el costo del informe (vigentes al 2026-08-24).", "tamano": 3, "valor": C["pipeline.costo.TARIFAS_USD_POR_MILLON"]["valor"]},
    ]
    motivos = sorted(set(C["pipeline.telemetria.MOTIVOS_MANUAL"]["valor"].keys()))

    puntos = [
     {"nombre": "traza (callback del Orquestador)",
      "firma": "traza(correo: str, etapa: str, ms: int, fallo: str) -> None",
      "como": "Orquestador(..., traza=fn). Se guarda en Resultado.traza y la invoca _Cronometro.__exit__ al terminar cada `with res.cronometrar(etapa)`, TAMBIEN cuando la etapa revienta (fallo = 'Tipo: mensaje', y la excepcion sigue propagandose).",
      "detalles": "correo = email.id de Graph (correr la abrevia con referencia_del_correo). Etapas: deduplicacion (2 veces por correo), enrutamiento, descarga_adjuntos, volumen, conversion_a_markdown, saneamiento, extraccion_determinista, agente, registro_de_personas, compuerta, correccion (0 a 2 veces), revisor, radicacion, cierre_outlook. NO se cronometran: reclamo, facturas, destino, remitente identificable, combinacion, bitacora, revision manual, aviso. Es sincrona y se llama desde el bucle de eventos (no bloquear). Res.etapas_ms acumula por etapa. correr.py usa _paso(correo, etapa, ms, fallo='')."},
     {"nombre": "Resultado de procesar",
      "firma": "async Orquestador.procesar(email: EmailMessage) -> Resultado",
      "como": "Campos: email_id, asunto, accion, radicado, motivo_manual, hallazgos: Dict[str, Hallazgo(campo, valor, evidencia, confianza, metodo, a_confirmar)], atribucion: Dict[str, Origen(ALGORITMO|IA|REGISTRO|NO_RESUELTO)], avisos: List[str], duracion_ms, etapas_ms, tokens_entrada, tokens_salida, turnos_del_agente, explicacion_del_agente, area_destino, razon_del_reenvio, razon_del_duplicado."},
     {"nombre": "pipeline.salida.emitir",
      "firma": "emitir(mensaje: str, severidad: str = 'INFO', solo_json: Any = (), en_su_linea: Any = (), **campos: Any) -> None",
      "como": "Se importa POR NOMBRE en correr, pipeline.orquestador y pipeline.radicador: hay que parchear cada copia (setattr(pipeline.orquestador, 'emitir', f); setattr(pipeline.radicador, 'emitir', f); setattr(correr, 'emitir', f)); parchear pipeline.salida.emitir no basta. Alternativa sin parches: FORMATO_LOG=json y leer sys.stdout, una linea JSON por evento con 'message' y 'severity'.",
      "eventos": ["radicador: '[ref] radicacion enviada a SGDEA' (intento, campos_enviados, campos_vacios, por_defecto, adjuntos, remitente_correo, asunto, recibido, internet_message_id)",
                  "radicador: '[ref] radicacion rechazada: <CLASE>' (intento, clase, fallo)",
                  "orquestador: '[ref] no se radica: <motivo>' (motivo_manual, rechazos, campos_faltantes, campos_leidos)",
                  "correr: etapa lenta/fallida (correo, etapa, ms, fallo), linea por correo (accion, radicado, motivo_manual, area_destino, por_que, vueltas, tokens_entrada, sin_resolver, duracion_ms), abandono por tope, resumen del lote con informe"]},
     {"nombre": "Interfaz del modelo (pipeline/modelo.py)",
      "firma": "Modelo.generar(sistema: str, mensajes: Sequence[Dict[str, str]], herramientas: Sequence[Dict[str, Any]]) -> Turno ; atributo nombre: str",
      "como": "Pasar al Orquestador un envoltorio con `nombre` y `generar` que delegue en ModeloVertex. Cada llamada a generar = 1 turno. Turno(texto: str, llamadas: List[Llamada(herramienta: str, argumentos: Dict)], tokens_entrada: int (usage_metadata.prompt_token_count), tokens_salida: int (candidates_token_count), modelo: str).",
      "detalles": "El mismo self.modelo lo usan el agente y el revisor: distinguirlos por `sistema` (== pipeline.revisor.SISTEMA para el revisor) o por herramientas (3 esquemas de lectura en el revisor). Los tokens del revisor NO llegan a Resultado ni al Contador: solo el envoltorio los ve. mensajes: [{'rol': 'usuario'|'asistente'|'herramienta', 'contenido': str}]. generar se ejecuta en un hilo (asyncio.to_thread) y con CONCURRENCIA correos en paralelo: el envoltorio debe ser thread-safe y asociar la llamada al correo (p. ej. con un contextvar copiado por to_thread, o por el contenido de la tarea)."},
     {"nombre": "Invocacion de herramientas del agente",
      "firma": "servidor_mcp.herramientas.invocar(ctx: Contexto, nombre: str, argumentos: Optional[Dict[str, Any]] = None) -> Resultado(ok: bool, datos: Any, error: str, truncado: bool) ; Resultado.a_dict()",
      "como": "Opcion A (cubre agente y revisor): envolver las funciones del registro `servidor_mcp.herramientas.HERRAMIENTAS[nombre]` (firma fn(ctx: Contexto, **args) -> Resultado), que invocar busca en cada llamada; conservar la firma para que un TypeError de argumentos siga devolviendose como error. Opcion B: `invocar` se importo por nombre en pipeline.agente y pipeline.revisor → setattr(pipeline.agente, 'invocar', w) y setattr(pipeline.revisor, 'invocar', w) (parchear servidor_mcp.herramientas.invocar no basta).",
      "detalles": "Las llamadas del revisor a herramientas no permitidas se rechazan antes de invocar. ctx.hallazgos acumula lo reportado (dicts campo/valor/evidencia/confianza); ctx.resueltos trae lo determinista; ctx.workspace da acceso al expediente."},
     {"nombre": "Funciones de etapa interceptables (sin tocar codigo)",
      "firma": "setattr(<modulo>, '<funcion>', envoltorio)",
      "como": "El orquestador resuelve en tiempo de llamada: atributos de modulo `compuerta.validar`, `compuerta.aplicar`, `facturas.es_factura`, `volumen.medir`, `reclamo.decidir`, `reintentos.decidir`, `notificacion.redactar`, `revisor.revisar` (parchear en su modulo); y nombres importados en pipeline.orquestador: decidir, extraer_todo, materializar, detectar, resolver, combinar, atribucion, rellenar_desde_el_registro, clean_html_content, es_buzon_de_positiva, tiene_remitente_externo_identificable, etiqueta_para, clasificar, debe_reintentar, espera_para (parchear en pipeline.orquestador). En pipeline.radicador: clasificar, debe_reintentar, espera_para, puede_repetirse_una_escritura, emitir, armar_payload.",
      "detalles": "Sirve para registrar entradas/salidas por etapa o inyectar parametros que son valores por defecto (umbral de extraer_todo, max_iteraciones/confianza_minima de resolver, confianza_minima de validar, max_vueltas de revisar)."},
     {"nombre": "Servicios inyectados (duck typing)",
      "firma": "Orquestador(modelo, outlook=None, radicador=None, sgdea=None, base_workspace=None, ejecucion=None, cache=None, auditoria=None, dormir=None, traza=None, presupuesto=None)",
      "como": "outlook: get_attachments(id), download_attachment(id, aid) -> base64, get_email_by_id(id), marcar(id, marca, quitar_de_proceso) -> categorias|None, move_email(id, carpeta) -> nuevo_id, forward_email(id, destino, html), hilo_ya_radicado(conv), buscar_etiqueta_ent_por_asunto(asunto, email), ultimo_error. radicador: radicar(email, hallazgos, archivos, cuerpo_limpio) -> str, atributos bitacora y suposiciones (o Radicador(servicio=X) con X.radicar_pqrd(payload: RadicacionPayload, archivos) -> obj con id_radicado/id y X.crear_trazabilidad(TrazabilidadPayload)). sgdea: consultar(documento, tipo=None) -> dict|None, ultimo_error. auditoria: email_exists(remitente, asunto, recibido). cache: email_hash_exists(h), save_email_hash(h). dormir: async fn(segundos).",
      "detalles": "Envolver cualquiera de estos objetos permite ver cada lectura/escritura con sus argumentos. Radicador.servicio.radicar_pqrd recibe el RadicacionPayload final (payload.to_form_data())."},
     {"nombre": "Telemetria de la corrida",
      "firma": "Ejecucion.registrar(registro: RegistroCorreo) -> None ; Contador.registrar(etiqueta: str, modelo: str, tokens_entrada: int, tokens_salida: int, turnos=...) ",
      "como": "Pasar ejecucion=nueva_ejecucion(ruta) (o una subclase de Ejecucion) al Orquestador: _cerrar llama ejecucion.registrar(RegistroCorreo(id_correo, asunto, accion, radicado, motivo_manual, area_destino, razon_del_reenvio, explicacion, duracion_ms, etapas_ms, atribucion, avisos)) y ejecucion.contador.registrar('agente', modelo.nombre, tokens_entrada, tokens_salida, turnos=turnos_del_agente). ejecucion.informe() / resumen() / contador.costo_usd."},
     {"nombre": "Bitacora del correo (ContextVar)",
      "firma": "pipeline.orquestador._bitacora() -> Bitacora ; Bitacora.fallos: List[Fallo(clase, paso, detalle, intento, excepcion)]",
      "como": "Dentro de la tarea de un correo (p. ej. desde un envoltorio de herramienta o de servicio) devuelve la bitacora de ESE correo; Bitacora.registrar es interceptable para ver cada fallo clasificado."},
     {"nombre": "Workspace en disco",
      "firma": "<base_workspace>/<id seguro>/ correo.md, adjuntos/*.md, manifiesto",
      "como": "Con base_workspace (RUTA_WORKSPACE en el Job) el workspace no es temporal y queda en disco tras ws.cerrar(): permite ver exactamente lo que leyo el extractor y el agente."},
     {"nombre": "Detector de duplicados",
      "firma": "orq.duplicados: Detector(outlook, cache, auditoria, vistos: Set[str])",
      "como": "orq.duplicados.vistos lista los conversation_id ya atendidos en la corrida; sus metodos comprobar/comprobar_contenido/registrar_radicado son envolvibles en la instancia."},
    ]

    sandbox = {
     "archivo": "puente/trabajador.py",
     "como_se_corre": "python correr_sandbox.py <corrida> [--n N] [--grupo G] [--casos a,b] [--concurrencia 5]; lee /datos/universo/<caso>/mensaje.json (+ adj/), escribe /datos/corridas/<corrida>.jsonl (reanuda saltando casos hechos); exige RUTA_WORKSPACE; sys.path /app.",
     "entorno_forzado": {"MODO_TEST": "true", "DEDUP_EN_PRUEBAS": "false"},
     "sustituye": [
      {"objeto": "outlook", "por": "BuzonLocal(carpeta, mensaje)", "detalle": "get_attachments y download_attachment sirven los adjuntos desde <caso>/adj/; get_email_by_id devuelve None; cualquier otro metodo (marcar, move_email, forward_email, hilo_ya_radicado, buscar_etiqueta_ent_por_asunto, ...) es un no-op async que devuelve None y se anota en buzon.escrituras; atributos 'ultimo*' devuelven ''."},
      {"objeto": "sgdea (consulta de personas)", "por": "Registro(ConsultaDePersonas real)", "detalle": "Envoltorio que llama al registro REAL y guarda documento -> nombre devuelto (fila['registro'])."},
      {"objeto": "email", "por": "EmailMessage.from_graph_api(mensaje.json sin claves '_') con categories=[]", "detalle": "Sin categorias previas: no hay reclamos ni reintentos anteriores."},
      {"objeto": "Orquestador", "por": "uno nuevo por caso", "detalle": "Orquestador(modelo, outlook=BuzonLocal, radicador=Radicador(RadicacionService(timeout=45)), base_workspace=RUTA_WORKSPACE, sgdea=Registro). Sin traza, sin ejecucion, sin presupuesto, sin auditoria ni cache (Detector.vistos no se comparte entre casos). Tope por caso: asyncio.wait_for(..., 600)."}],
     "llamadas_reales_que_siguen": [
      "Vertex AI generate_content (ModeloVertex real con ADC, ubicacion global, gemini-2.5-flash): se factura; agente y revisor.",
      "Registro de personas GET {MS_SGDEA_BETA_URL}/personas (sin guarda de MODO_TEST), tanto en registro_de_personas como en la herramienta consultar_persona.",
      "Escrituras locales: workspaces en RUTA_WORKSPACE, CSV de MODO_TEST en TEST_OUTPUT_DIR (radicaciones.csv con el payload completo; escrituras.csv solo si algun servicio real escribe) y el .jsonl de resultados.",
      "OCR local (tesseract/pymupdf)."],
     "no_ocurren": [
      "Radicacion en SGDEA: RadicacionService.radicar_pqrd con MODO_TEST graba el payload y devuelve id_radicado 'DRY-RUN-ENT' (no pide token); crear_trazabilidad tambien se corta.",
      "Graph: ninguna llamada (BuzonLocal).", "Supabase y Redis: no se construyen (auditoria=None, cache=None).", "GCS: no hay Ejecucion ni escritura de informes."],
     "efectos_en_desenlaces": [
      "Deduplicacion: capas 3-4 devuelven None (no-op) y la capa 6 queda 'no configurada' -> aviso 'deduplicacion degradada'; solo funcionan las capas 1-2.",
      "Radicados: accion 'radicado' con radicado 'DRY-RUN-ENT'; como no tiene forma ENT<numero>, etiqueta_para avisa y etiquetaria 'RADICADO:DRY-RUN-ENT' (no-op).",
      "Aplazamientos: BuzonLocal.marcar devuelve None, asi que las categorias no reflejan REINTENTO:N y _a_revision_manual cierra a 'manual' (con aviso 'no quedo escrita la marca') los motivos que en produccion serian 'aplazado'.",
      "Reclamo: la marca EN-PROCESO se 'escribe' sin error (no-op), nunca hay en_proceso/sin_reclamar.",
      "Aviso: solo si CORREO_DE_AVISOS esta fijado, y es no-op (queda en escrituras)."],
     "salida_por_caso": ["caso", "grupo", "motivo_prod", "accion", "motivo", "radicado", "avisos", "explicacion", "vueltas", "tok_in", "tok_out", "etapas", "ms", "hallazgos{campo:{v,m,c,e}}", "sin_resolver", "registro", "escrituras", "s"],
    }

    total_params = sum(len(n.get("parametros", [])) for n in nodos)
    sob = sum(1 for n in nodos for p in n.get("parametros", []) if p["sobrescribible_en_caliente"])

    out = {
     "meta": {"fuente": f"motor/ (mcp-fix, {ORIGEN[:7]})", "generado": __import__("datetime").date.today().isoformat(), "punto_de_entrada": "correr.py -> pipeline/orquestador.py Orquestador.procesar",
              "total_nodos": len(nodos), "total_parametros": total_params, "parametros_sobrescribibles": sob,
              "criterio_sobrescribible": "true solo si setattr(modulo, NOMBRE, v) (o setattr(config.settings.settings, NOMBRE, v) para settings) cambia el comportamiento en tiempo de ejecucion; ver nota_sobrescritura y alcance_sobrescritura (total|parcial|antes_del_primer_uso|ninguno).",
              "motivos_manual": motivos},
     "nodos": nodos,
     "aristas": [{"desde": a, "hacia": b, "etiqueta": c} for a, b, c in ARISTAS],
     "herramientas_agente": HERRAMIENTAS,
     "proveedores": PROVEEDORES,
     "desenlaces": DESENLACES,
     "variables_entorno": [{"nombre": n, "uso": u, "secreto": s} for n, u, s in VARIABLES],
     "catalogos": catalogos,
     "puntos_de_enganche": puntos,
     "sandbox": sandbox,
    }
    # validar aristas
    ids = {n["id"] for n in nodos}
    for a in out["aristas"]:
        assert a["desde"] in ids and a["hacia"] in ids, a
    for n in nodos:
        for s in n["siguientes"]:
            assert s in ids, (n["id"], s)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("nodos", len(nodos), "params", total_params, "sobrescribibles", sob,
          "herramientas", len(HERRAMIENTAS), "proveedores", len(PROVEEDORES))


if __name__ == "__main__":
    main()
