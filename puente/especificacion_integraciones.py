"""
La ficha de los nodos de integración (plan, sección 5d): Google Workspace
(Gmail, Calendar, Docs, Forms), Microsoft 365 (Outlook, calendario, OneDrive,
SharePoint, Teams, Excel en línea), el conector genérico por OpenAPI y los
disparadores por evento (gmail_nuevo, outlook_nuevo, calendar_evento_proximo).

`tipos(h)` recibe el módulo `nodos_declarativos` (sus ayudas de esquema) y
devuelve {tipo: ficha} con config (JSON Schema), salidas, lo que deja, un
ejemplo, `ejemplos` para copiar, `consejos` cortos y `preparar` (los pasos para
dejar lista la conexión: lo que Vera le explica a la persona). Lo ejecuta
`nodos_integraciones.py`; el zod del servidor está en `servidor/src/flujos_integraciones.ts`.
"""
from __future__ import annotations

from typing import Any, Dict

GOOGLE = ["google_oauth"]
GOOGLE_TODAS = ["google_oauth", "google_servicio"]
MICROSOFT = ["microsoft_oauth", "microsoft_graph"]
CALENDARIOS = ["google_oauth", "google_servicio", "microsoft_oauth", "microsoft_graph"]

# Lo que se le dice a la persona para dejar lista cada familia de conexión.
_PREPARAR_GOOGLE = [
    "En Conexiones crea una «Google (OAuth)» con el client_id y el client_secret de un cliente OAuth «Aplicación web» de Google Cloud "
    "(URI de redirección: <URL_PUBLICA>/api/oauth/callback) y pulsa «Conectar con Google»: la persona inicia sesión y acepta los permisos.",
    "Los alcances por defecto cubren Gmail, Calendar, Drive, Docs, Hojas y Forms; si el nodo dice que falta un permiso, edita los alcances y vuelve a conectar.",
    "Para escribir (enviar, etiquetar, crear eventos o archivos) una persona tiene que habilitar la escritura de la conexión; si no, la acción queda retenida.",
]
_PREPARAR_MICROSOFT = [
    "En Conexiones crea una «Microsoft 365 (OAuth)» con el tenant, el client_id y el client_secret de una aplicación de Entra ID "
    "(plataforma Web, URI de redirección: <URL_PUBLICA>/api/oauth/callback, permisos delegados) y pulsa «Conectar con Microsoft».",
    "También sirve una «Microsoft 365 (Graph)» de aplicación (permisos de aplicación y el buzón o usuario en «buzon»), salvo Teams, que exige OAuth.",
    "Para escribir una persona tiene que habilitar la escritura de la conexión; si no, la acción queda retenida con todo armado. Nunca buzones ni direcciones de Positiva.",
]


def tipos(h: Any) -> Dict[str, Dict[str, Any]]:
    conf, credencial, plantilla, texto, entero = h.conf, h.credencial, h.plantilla, h.texto, h.entero
    booleano, opcion, lista, objeto, mapa, libre, largo = h.booleano, h.opcion, h.lista, h.objeto, h.mapa, h.libre, h.largo
    SALIDA, TIMEOUT, ARCHIVOS, REINT = h.SALIDA, h.TIMEOUT, h.ARCHIVOS, h._REINTENTOS
    FILTRO = h._FILTRO_CORREO
    zona = texto("zona horaria de las fechas sin zona", "America/Bogota")
    mensaje_g = plantilla("id del mensaje de Gmail (por defecto, el correo que arrancó la ejecución con gmail_nuevo), p. ej. «{{correo_g.id}}»")
    mensaje_m = plantilla("id de Graph del mensaje (por defecto, el correo que arrancó la ejecución con outlook_nuevo), p. ej. «{{correos.0.graph_id}}»")
    asistentes = libre("correos de los invitados: lista o texto separado por comas (nunca direcciones de Positiva)")
    desde = plantilla("desde cuándo: fecha ISO, «2026-11-10 08:00», «hoy», «mañana» o «ahora»", "ahora")
    hasta = plantilla("hasta cuándo (si no, desde + dias)")
    sitio = plantilla("el sitio de SharePoint: su URL (https://contoso.sharepoint.com/sites/Finanzas), «contoso.sharepoint.com:/sites/Finanzas» o su id")
    paginacion = objeto({
        "tipo": opcion(["cursor", "pagina", "enlace"], "cursor: la respuesta trae el siguiente cursor; pagina: número de página; enlace: el encabezado Link rel=next o una URL en la respuesta"),
        "parametro": texto("el parámetro de la consulta que lleva el cursor o la página (p. ej. «cursor», «page»)"),
        "ruta_cursor": texto("cursor: dónde viene el siguiente cursor en la respuesta (p. ej. «meta.next_cursor»)"),
        "ruta_siguiente": texto("enlace: dónde viene la URL siguiente en la respuesta (si no, el encabezado Link)"),
        "ruta_items": texto("dónde está la lista en cada respuesta (p. ej. «data»); se juntan todas las páginas"),
        "inicio": entero("pagina: la primera página", 0, 1000000, 1),
        "max_paginas": entero("tope de páginas", 1, 50, 5)}, ["tipo"], "traer varias páginas y juntar sus elementos")

    T: Dict[str, Dict[str, Any]] = {
        # ======================================================= disparadores por evento
        "gmail_nuevo": {
            "nombre": "Gmail · correo nuevo", "categoria": "entrada",
            "descripcion": "Revisa Gmail (conexión google_oauth, solo lectura, sin marcar como leído) cada N minutos con el historial de Gmail (historyId): "
                           "corre el flujo una vez por cada correo nuevo que cumpla la búsqueda, sin repetir. Deja correo.* y adjuntos.* como entrada_correo; "
                           "gmail_responder y gmail_etiquetar actúan sobre ese correo sin decir su id. Nace apagado.",
            "config": conf({"credencial": credencial(GOOGLE), "consulta": texto("búsqueda de Gmail que tiene que cumplir (from:, subject:, has:attachment, -label:x…)"),
                            "etiqueta": texto("solo los que llegan a esta etiqueta", "INBOX"), "cada_minutos": entero("cada cuánto revisar", 1, 1440, 5),
                            "activo": booleano("encendido", False), "max_por_sondeo": entero("tope de correos por revisión", 1, 50, 10),
                            "procesar_existentes": booleano("en la primera revisión, correr también los que ya estaban", False),
                            "leer_adjuntos": booleano("leer los adjuntos aquí", True), "ocr": booleano("OCR al leerlos", True)}, ["credencial"]),
            "salidas": ["ok"], "produce": "correo.*, adjuntos.*, entrada {id, hilo, etiquetas}", "x-credencial": True,
            "ejemplo": {"id": "llega_gmail", "tipo": "gmail_nuevo", "nombre": "Llega un correo a Gmail", "config": {
                "credencial": "gmail-soporte", "consulta": "has:attachment subject:solicitud", "cada_minutos": 5}},
            "ejemplos": [{"credencial": "gmail-soporte", "cada_minutos": 5}, {"credencial": "gmail-soporte", "consulta": "from:@proveedor.com", "etiqueta": "Facturas", "leer_adjuntos": True}],
            "consejos": ["Recuerda el último historyId: nunca corre dos veces el mismo correo; la primera revisión solo memoriza (salvo procesar_existentes).",
                         "Para que reaccione al instante en vez de sondear, Gmail ofrece push por Pub/Sub; aquí se usa sondeo para no abrir la plataforma a internet.",
                         "Después de procesar, márcalo con gmail_etiquetar (p. ej. agregar «Procesado») para verlo en Gmail."],
            "preparar": _PREPARAR_GOOGLE,
        },
        "outlook_nuevo": {
            "nombre": "Outlook · correo nuevo", "categoria": "entrada",
            "descripcion": "Revisa una carpeta de Outlook (microsoft_oauth o microsoft_graph, solo lectura) cada N minutos con la consulta delta de Graph: corre "
                           "el flujo una vez por cada correo nuevo, sin repetir. Deja correo.* y adjuntos.*; outlook_responder, outlook_mover y outlook_categorizar "
                           "actúan sobre ese correo sin decir su id. Nunca los buzones de Positiva. Nace apagado.",
            "config": conf({"credencial": credencial(MICROSOFT), "carpeta": texto("carpeta: inbox, archive… o su id", "inbox"), "filtro": FILTRO,
                            "cada_minutos": entero("cada cuánto revisar", 1, 1440, 5), "activo": booleano("encendido", False),
                            "max_por_sondeo": entero("tope de correos por revisión", 1, 50, 10),
                            "procesar_existentes": booleano("en la primera revisión, correr también los que ya estaban", False),
                            "leer_adjuntos": booleano("leer los adjuntos aquí", True), "ocr": booleano("OCR al leerlos", True)}, ["credencial"]),
            "salidas": ["ok"], "produce": "correo.*, adjuntos.*, entrada {id, graph_id, carpeta}", "x-credencial": True,
            "ejemplo": {"id": "llega_outlook", "tipo": "outlook_nuevo", "nombre": "Llega un correo a Outlook", "config": {
                "credencial": "outlook-agenda", "filtro": {"asunto": "reunión"}, "cada_minutos": 5}},
            "ejemplos": [{"credencial": "outlook-agenda", "cada_minutos": 5}, {"credencial": "outlook-agenda", "carpeta": "inbox", "filtro": {"no_leidos": True}}],
            "consejos": ["Guarda el deltaLink de Graph: solo trae lo que cambió desde la revisión anterior y nunca repite un correo.",
                         "Las suscripciones de Graph (webhooks) piden una URL pública con HTTPS; con sondeo no hace falta abrir nada.",
                         "Con una conexión de aplicación (microsoft_graph) lee el buzón de «buzon»; con OAuth, el de la persona que conectó."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "calendar_evento_proximo": {
            "nombre": "Evento próximo del calendario", "categoria": "entrada",
            "descripcion": "Corre el flujo N minutos antes de cada evento de un calendario de Google (google_oauth o google_servicio) o de Outlook "
                           "(microsoft_oauth o microsoft_graph), una sola vez por evento. Deja el evento en {{entrada.evento}} (titulo, inicio, fin, lugar, "
                           "asistentes, enlace, meet). Solo lectura. Nace apagado.",
            "config": conf({"credencial": credencial(CALENDARIOS), "calendario": texto("id del calendario (Google: primary o el correo del calendario; Outlook: vacío = el principal)", "primary"),
                            "minutos_antes": entero("cuántos minutos antes de que empiece", 1, 1440, 15), "consulta": texto("solo eventos cuyo título o descripción contenga esto"),
                            "cada_minutos": entero("cada cuánto revisar", 1, 60, 5), "activo": booleano("encendido", False)}, ["credencial"]),
            "salidas": ["ok"], "produce": "entrada {evento: {id, titulo, inicio, fin, lugar, asistentes, enlace, meet}, minutos_antes}", "x-credencial": True,
            "ejemplo": {"id": "antes_reunion", "tipo": "calendar_evento_proximo", "nombre": "15 minutos antes", "config": {
                "credencial": "google-agenda", "minutos_antes": 15, "consulta": "comité"}},
            "ejemplos": [{"credencial": "google-agenda", "minutos_antes": 30}, {"credencial": "outlook-agenda", "calendario": "", "minutos_antes": 10, "cada_minutos": 2}],
            "consejos": ["Recuerda los eventos ya avisados (por id e inicio): si el evento se mueve, vuelve a avisar en la hora nueva.",
                         "cada_minutos tiene que ser menor que minutos_antes para no llegar tarde.",
                         "Úsalo para enviar el orden del día, un recordatorio por Teams o preparar documentos antes de la reunión."],
            "preparar": _PREPARAR_GOOGLE[:1] + _PREPARAR_MICROSOFT[:1],
        },
        # ============================================================== Gmail
        "gmail_buscar": {
            "nombre": "Gmail · buscar", "categoria": "conector",
            "descripcion": "Busca correos en Gmail (google_oauth, solo lectura) con la búsqueda de Gmail y una etiqueta; deja una lista con asunto, remitente, "
                           "cuerpo y adjuntos, que quedan como archivos de la ejecución (se mandan con «{{correos.0.adjuntos}}» o se leen con preguntar_documentos).",
            "config": conf({"credencial": credencial(GOOGLE), "consulta": plantilla("búsqueda de Gmail: from:, to:, subject:, has:attachment, newer_than:7d, is:unread…"),
                            "etiqueta": plantilla("nombre o id de una etiqueta (INBOX, UNREAD, «Facturas»…)"), "max": entero("cuántos correos", 1, 100, 10),
                            "incluir_adjuntos": booleano("bajar los adjuntos como archivos de la ejecución", True), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
            "salidas": ["ok", "vacio"], "produce": "salida (lista de {id, hilo, asunto, remitente, remitente_correo, para, fecha, cuerpo, etiquetas, adjuntos: [fichas]})", "x-credencial": True,
            "ejemplo": {"id": "buscar_gmail", "tipo": "gmail_buscar", "nombre": "Facturas de la semana", "config": {
                "credencial": "gmail-soporte", "consulta": "subject:factura has:attachment newer_than:7d", "max": 20, "salida": "correos"}},
            "ejemplos": [{"credencial": "gmail-soporte", "consulta": "is:unread", "max": 10, "salida": "correos"},
                         {"credencial": "gmail-soporte", "etiqueta": "Facturas", "incluir_adjuntos": False, "salida": "correos"}],
            "consejos": ["La consulta es la misma del buscador de Gmail: prueba la búsqueda en Gmail y cópiala.",
                         "Recórrelos con para_cada (lista «correos») y responde cada uno con gmail_responder (mensaje «{{correo_i.id}}»)."],
            "preparar": _PREPARAR_GOOGLE,
        },
        "gmail_enviar": {
            "nombre": "Gmail · enviar", "categoria": "conector",
            "descripcion": "Envía un correo desde la cuenta de Gmail conectada (google_oauth), con adjuntos de la ejecución. ESCRIBE AFUERA: solo sale si una persona "
                           "habilitó la escritura de la conexión; si no, se arma completo con sus adjuntos y queda retenido. Nunca a direcciones de Positiva.",
            "config": conf({"credencial": credencial(GOOGLE), "para": plantilla("destinatarios separados por coma"), "cc": plantilla(), "cco": plantilla(),
                            "asunto": plantilla(), "cuerpo": plantilla("texto del correo", largo=True), "html": plantilla("versión HTML opcional", largo=True),
                            "adjuntos": ARCHIVOS(), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "para", "asunto", "cuerpo"]),
            "salidas": ["ok"], "produce": "salida {enviado, bloqueado, id, hilo, para, asunto, adjuntos}", "x-credencial": True,
            "ejemplo": {"id": "enviar_gmail", "tipo": "gmail_enviar", "nombre": "Enviar el informe", "config": {
                "credencial": "gmail-soporte", "para": "{{entrada.correo}}", "asunto": "Informe de {{entrada.mes}}", "cuerpo": "Adjunto el informe.", "adjuntos": ["{{documento}}"]}},
            "ejemplos": [{"credencial": "gmail-soporte", "para": "equipo@empresa.com", "asunto": "Resumen", "cuerpo": "{{resumen}}"}],
            "consejos": ["El remitente es la cuenta que se conectó; hasta 10 adjuntos y 20 MB.", "Mientras la escritura no esté habilitada, mira el correo armado en el detalle de la ejecución."],
            "preparar": _PREPARAR_GOOGLE,
        },
        "gmail_responder": {
            "nombre": "Gmail · responder", "categoria": "conector",
            "descripcion": "Responde en el MISMO hilo de Gmail (threadId, In-Reply-To y References) al correo que arrancó la ejecución (gmail_nuevo) o al de mensaje, "
                           "con adjuntos. ESCRIBE AFUERA: solo sale con la escritura habilitada por una persona; si no, queda retenida con todo armado.",
            "config": conf({"credencial": credencial(GOOGLE), "mensaje": mensaje_g, "cuerpo": plantilla("el texto de la respuesta", largo=True),
                            "html": plantilla("versión HTML opcional", largo=True), "para": plantilla("a quién (por defecto Reply-To o el remitente)"),
                            "cc": plantilla(), "asunto": plantilla("por defecto «Re: » y el del original"), "adjuntos": ARCHIVOS(),
                            "incluir_original": booleano("citar el correo original debajo", True), "todos": booleano("responder a todos (con copia a los demás)", False),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "cuerpo"]),
            "salidas": ["ok"], "produce": "salida {enviado, bloqueado, id, hilo, para, asunto, adjuntos}", "x-credencial": True,
            "ejemplo": {"id": "responder_gmail", "tipo": "gmail_responder", "nombre": "Responder con el documento", "config": {
                "credencial": "gmail-soporte", "cuerpo": "{{respuesta}}", "adjuntos": ["{{documento}}"]}},
            "ejemplos": [{"credencial": "gmail-soporte", "cuerpo": "{{respuesta}}", "incluir_original": True},
                         {"credencial": "gmail-soporte", "mensaje": "{{correo_i.id}}", "cuerpo": "Recibido, gracias.", "todos": True}],
            "consejos": ["Con gmail_nuevo como disparador no hace falta mensaje: responde al correo que llegó.",
                         "Dentro de un para_cada sobre gmail_buscar usa mensaje «{{correo_i.id}}»."],
            "preparar": _PREPARAR_GOOGLE,
        },
        "gmail_etiquetar": {
            "nombre": "Gmail · etiquetar", "categoria": "conector",
            "descripcion": "Pone o quita etiquetas de un correo de Gmail (crea las que falten), lo marca como leído o lo archiva. ESCRIBE AFUERA: solo con la "
                           "escritura habilitada por una persona; si no, queda retenido.",
            "config": conf({"credencial": credencial(GOOGLE), "mensaje": mensaje_g, "agregar": lista(texto(), "etiquetas a poner (nombres; p. ej. «Procesado», STARRED)"),
                            "quitar": lista(texto(), "etiquetas a quitar (p. ej. UNREAD, INBOX)"), "crear_si_falta": booleano("crear las etiquetas que no existan", True),
                            "marcar_leido": booleano("quitar UNREAD", False), "archivar": booleano("quitar INBOX", False),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
            "salidas": ["ok"], "produce": "salida {etiquetas, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "marcar", "tipo": "gmail_etiquetar", "nombre": "Marcar procesado", "config": {
                "credencial": "gmail-soporte", "agregar": ["Procesado"], "marcar_leido": True}},
            "ejemplos": [{"credencial": "gmail-soporte", "agregar": ["Procesado"], "archivar": True}],
            "consejos": ["Las etiquetas del sistema van por su id: INBOX, UNREAD, STARRED, IMPORTANT, SPAM, TRASH.",
                         "Úsalo al final del flujo para que la persona vea en Gmail qué se atendió."],
            "preparar": _PREPARAR_GOOGLE,
        },
        # ============================================================ Calendar
        "calendar_eventos": {
            "nombre": "Calendar · eventos", "categoria": "conector",
            "descripcion": "Lista los eventos de un calendario de Google (google_oauth, o google_servicio con el calendario compartido) en un rango; solo lectura.",
            "config": conf({"credencial": credencial(GOOGLE_TODAS), "calendario": plantilla("primary o el correo / id del calendario", "primary"),
                            "desde": desde, "hasta": hasta, "dias": entero("días desde «desde» si no hay hasta", 1, 366, 7), "consulta": plantilla("texto que contiene el evento"),
                            "zona": zona, "max": entero("cuántos eventos", 1, 250, 50), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
            "salidas": ["ok", "vacio"], "produce": "salida (lista de {id, titulo, inicio, fin, todo_el_dia, lugar, descripcion, asistentes, organizador, enlace, meet})", "x-credencial": True,
            "ejemplo": {"id": "agenda", "tipo": "calendar_eventos", "nombre": "Agenda de la semana", "config": {"credencial": "google-agenda", "desde": "hoy", "dias": 7, "salida": "eventos"}},
            "ejemplos": [{"credencial": "google-agenda", "desde": "hoy", "dias": 1, "salida": "eventos"},
                         {"credencial": "google-agenda", "calendario": "sala-juntas@empresa.com", "consulta": "comité", "salida": "eventos"}],
            "consejos": ["Las fechas sin zona se entienden en America/Bogota.", "Cada evento trae asistentes [{correo, nombre, respuesta}] y el enlace de Meet si lo tiene."],
            "preparar": _PREPARAR_GOOGLE,
        },
        "calendar_crear": {
            "nombre": "Calendar · crear evento", "categoria": "conector",
            "descripcion": "Crea un evento en Google Calendar con invitados y, si se pide, enlace de Google Meet; Google manda las invitaciones. ESCRIBE AFUERA: solo "
                           "con la escritura habilitada por una persona; si no, el evento se arma completo y queda retenido. Nunca invita direcciones de Positiva.",
            "config": conf({"credencial": credencial(GOOGLE_TODAS), "calendario": plantilla("primary o el id del calendario", "primary"),
                            "titulo": plantilla("título del evento"), "inicio": plantilla("inicio: «2026-11-10 15:00», ISO o «{{propuesta.inicio}}»"),
                            "fin": plantilla("fin (o usa duracion_min)"), "duracion_min": entero("duración si no hay fin", 5, 1440, 30),
                            "todo_el_dia": booleano("evento de día completo (inicio es una fecha)", False), "zona": zona,
                            "descripcion": plantilla("descripción", largo=True), "lugar": plantilla("lugar"), "asistentes": asistentes,
                            "con_meet": booleano("agregar enlace de Google Meet", False), "notificar": booleano("mandar las invitaciones por correo", True),
                            "recordatorio_min": entero("recordatorio emergente, minutos antes", 0, 40320),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "titulo", "inicio"]),
            "salidas": ["ok"], "produce": "salida {id, enlace, meet, inicio, fin, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "agendar", "tipo": "calendar_crear", "nombre": "Agendar la reunión", "config": {
                "credencial": "google-agenda", "titulo": "Revisión de {{entrada.tema}}", "inicio": "{{franja.inicio}}", "duracion_min": 45,
                "asistentes": "{{entrada.correo}}", "con_meet": True}},
            "ejemplos": [{"credencial": "google-agenda", "titulo": "Seguimiento", "inicio": "2026-11-12 10:00", "duracion_min": 30, "asistentes": ["ana@empresa.com"], "con_meet": True}],
            "consejos": ["Antes de crear, revisa con calendar_disponibilidad que la franja esté libre.",
                         "Con google_servicio el calendario tiene que estar compartido con la cuenta de servicio, que no puede invitar fuera del dominio."],
            "preparar": _PREPARAR_GOOGLE,
        },
        "calendar_actualizar": {
            "nombre": "Calendar · actualizar evento", "categoria": "conector",
            "descripcion": "Cambia un evento de Google Calendar (título, horario, lugar, descripción, invitados, Meet) o lo cancela. ESCRIBE AFUERA: solo con la "
                           "escritura habilitada por una persona; si no, el cambio queda retenido.",
            "config": conf({"credencial": credencial(GOOGLE_TODAS), "calendario": plantilla("primary o el id del calendario", "primary"), "evento": plantilla("id del evento"),
                            "titulo": plantilla(), "inicio": plantilla(), "fin": plantilla(), "duracion_min": entero("con inicio y sin fin", 5, 1440),
                            "zona": zona, "descripcion": plantilla(largo=True), "lugar": plantilla(),
                            "asistentes": libre("reemplaza la lista de invitados"), "agregar_asistentes": libre("agrega invitados sin quitar los que hay"),
                            "con_meet": booleano("agregar enlace de Meet"), "cancelar": booleano("cancelar el evento", False),
                            "notificar": booleano("avisar a los invitados", True), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "evento"]),
            "salidas": ["ok"], "produce": "salida {id, enlace, cambios, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "mover", "tipo": "calendar_actualizar", "nombre": "Mover la reunión", "config": {
                "credencial": "google-agenda", "evento": "{{evento.id}}", "inicio": "{{nueva_hora}}", "duracion_min": 30}},
            "ejemplos": [{"credencial": "google-agenda", "evento": "{{evento.id}}", "agregar_asistentes": "{{entrada.correo}}"},
                         {"credencial": "google-agenda", "evento": "{{evento.id}}", "cancelar": True}],
            "consejos": ["Solo cambia lo que pones; lo demás queda igual.", "Con cancelar: true los invitados reciben la cancelación (si notificar)."],
            "preparar": _PREPARAR_GOOGLE,
        },
        "calendar_disponibilidad": {
            "nombre": "Calendar · disponibilidad", "categoria": "conector",
            "descripcion": "Consulta libre/ocupado (freeBusy) de uno o varios calendarios de Google en un rango y propone franjas libres de la duración pedida dentro "
                           "del horario. Sale por «libre» si todo el rango está libre y por «ocupado» si no. Solo lectura.",
            "config": conf({"credencial": credencial(GOOGLE_TODAS), "calendarios": libre("ids o correos de los calendarios (lista o separados por coma); por defecto primary"),
                            "desde": plantilla("inicio del rango", "ahora"), "hasta": plantilla("fin del rango (si no, desde + duracion_min o 5 días)"),
                            "duracion_min": entero("duración de la reunión para proponer franjas", 5, 1440), "zona": zona,
                            "horario_desde": texto("hora hábil de inicio HH:MM", "08:00"), "horario_hasta": texto("hora hábil de cierre HH:MM", "17:00"),
                            "max_franjas": entero("cuántas franjas proponer", 1, 50, 5), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
            "salidas": ["libre", "ocupado"], "produce": "salida {libre, ocupado: {calendario: [{inicio, fin}]}, franjas: [{inicio, fin}]}", "x-credencial": True,
            "ejemplo": {"id": "hay_espacio", "tipo": "calendar_disponibilidad", "nombre": "¿Hay espacio?", "config": {
                "credencial": "google-agenda", "desde": "mañana", "hasta": "{{entrada.limite}}", "duracion_min": 30, "salida": "agenda"}},
            "ejemplos": [{"credencial": "google-agenda", "desde": "{{propuesta.inicio}}", "duracion_min": 60, "salida": "agenda"},
                         {"credencial": "google-agenda", "calendarios": ["primary", "sala@empresa.com"], "desde": "hoy", "duracion_min": 30, "horario_desde": "09:00"}],
            "consejos": ["Para saber si una hora puntual está libre pon desde = la hora y duracion_min: sale por «libre» u «ocupado».",
                         "La primera franja libre queda en {{agenda.franjas.0.inicio}}: úsala en calendar_crear."],
            "preparar": _PREPARAR_GOOGLE,
        },
        # ============================================================ Docs y Forms
        "docs_crear_desde_plantilla": {
            "nombre": "Docs · documento desde plantilla", "categoria": "documentos",
            "descripcion": "Llena una plantilla de Google Docs: reemplaza cada {{variable}} del documento por su valor y deja el resultado como archivo de la "
                           "ejecución en PDF o Word. Sin en_drive solo lee la plantilla (la exporta y la llena aquí); con en_drive copia el Doc en Drive y lo "
                           "llena allá (ESCRIBE AFUERA: solo con la escritura habilitada por una persona; si no, se llena aquí y la copia queda retenida).",
            "config": conf({"credencial": credencial(GOOGLE_TODAS), "plantilla": plantilla("id o enlace del Google Doc plantilla"),
                            "variables": mapa(plantilla(), "{variable: plantilla}: el valor de cada {{variable}} del documento (las que no estén se buscan en el flujo)"),
                            "nombre": plantilla("nombre del documento (sin extensión)"), "exportar": opcion(["pdf", "docx", "ninguno"], "en qué formato queda como archivo", "pdf"),
                            "en_drive": booleano("crear la copia llena en Drive (escritura)", False), "carpeta": plantilla("carpeta de Drive de la copia (con en_drive)"),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "plantilla"]),
            "salidas": ["ok"], "produce": "salida {id, nombre, tipo, tamano (el archivo), doc_id, enlace (con en_drive), reemplazos}", "x-credencial": True,
            "ejemplo": {"id": "carta", "tipo": "docs_crear_desde_plantilla", "nombre": "Carta desde la plantilla", "config": {
                "credencial": "google-docs", "plantilla": "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "variables": {"nombre": "{{datos.nombre}}", "fecha": "{{hoy}}"},
                "nombre": "Carta {{datos.nombre}}", "exportar": "pdf", "salida": "documento"}},
            "ejemplos": [{"credencial": "google-docs", "plantilla": "{{entrada.plantilla}}", "exportar": "docx", "salida": "documento"},
                         {"credencial": "google-docs", "plantilla": "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "en_drive": True, "carpeta": "1CarpetaDeSalida0123", "exportar": "pdf", "salida": "documento"}],
            "consejos": ["En el Doc escribe las variables como {{nombre}}; sin variables se usan las del flujo con el mismo nombre.",
                         "El archivo queda en la salida: adjúntalo con «{{documento}}» en gmail_responder, outlook_enviar o drive_subir."],
            "preparar": _PREPARAR_GOOGLE,
        },
        "forms_respuestas": {
            "nombre": "Forms · respuestas", "categoria": "conector",
            "descripcion": "Lee las respuestas de un formulario de Google Forms (solo lectura): cada respuesta como un objeto {pregunta: respuesta} con su fecha y "
                           "el correo de quien respondió (si el formulario lo pide).",
            "config": conf({"credencial": credencial(GOOGLE_TODAS), "formulario": plantilla("id o enlace de edición del formulario"),
                            "desde": plantilla("solo las respuestas enviadas después de esta fecha"), "max": entero("cuántas respuestas", 1, 5000, 500),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "formulario"]),
            "salidas": ["ok", "vacio"], "produce": "salida (lista de {id, enviada, correo, respuestas: {pregunta: valor}})", "x-credencial": True,
            "ejemplo": {"id": "inscripciones", "tipo": "forms_respuestas", "nombre": "Inscripciones nuevas", "config": {
                "credencial": "google-forms", "formulario": "1FAIpQLSdEjemploDeFormulario0123", "desde": "{{ultima_revision}}", "salida": "respuestas"}},
            "ejemplos": [{"credencial": "google-forms", "formulario": "1FAIpQLSdEjemploDeFormulario0123", "salida": "respuestas"}],
            "consejos": ["Combínalo con programado cada hora y deduplicar (por id) para atender solo las nuevas.",
                         "Las preguntas de casillas devuelven una lista de valores."],
            "preparar": _PREPARAR_GOOGLE,
        },
        # =========================================================== Outlook
        "outlook_buscar": {
            "nombre": "Outlook · buscar", "categoria": "conector",
            "descripcion": "Busca correos en una carpeta de Outlook (microsoft_oauth o microsoft_graph, solo lectura) con búsqueda de texto ($search) o filtros; "
                           "deja la lista con asunto, remitente, cuerpo y adjuntos (como archivos de la ejecución). Nunca los buzones de Positiva.",
            "config": conf({"credencial": credencial(MICROSOFT), "carpeta": plantilla("inbox, archive, sentitems… o el id de la carpeta", "inbox"),
                            "consulta": plantilla("búsqueda de texto (from:ana subject:factura) en asunto, cuerpo y remitente"), "filtro": FILTRO,
                            "desde_fecha": plantilla("solo los recibidos desde AAAA-MM-DD"), "max": entero("cuántos correos", 1, 100, 10),
                            "incluir_adjuntos": booleano("bajar los adjuntos como archivos de la ejecución", True), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
            "salidas": ["ok", "vacio"], "produce": "salida (lista de {id, graph_id, hilo, asunto, remitente, remitente_correo, para, fecha, cuerpo, categorias, adjuntos: [fichas]})", "x-credencial": True,
            "ejemplo": {"id": "buscar_outlook", "tipo": "outlook_buscar", "nombre": "Solicitudes de reunión", "config": {
                "credencial": "outlook-agenda", "consulta": "reunión", "filtro": {"no_leidos": True}, "max": 10, "salida": "correos"}},
            "ejemplos": [{"credencial": "outlook-agenda", "filtro": {"asunto": "factura"}, "desde_fecha": "2026-11-01", "salida": "correos"}],
            "consejos": ["Cada correo trae graph_id: úsalo en outlook_responder, outlook_mover y outlook_categorizar (mensaje «{{correo_i.graph_id}}»)."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "outlook_enviar": {
            "nombre": "Outlook · enviar", "categoria": "conector",
            "descripcion": "Envía un correo desde Outlook (microsoft_oauth: la cuenta conectada; microsoft_graph: el buzón de la conexión), con adjuntos. ESCRIBE "
                           "AFUERA: solo con la escritura habilitada por una persona; si no, queda retenido con todo armado. Nunca desde ni a Positiva.",
            "config": conf({"credencial": credencial(MICROSOFT), "para": plantilla("destinatarios separados por coma"), "cc": plantilla(), "cco": plantilla(),
                            "asunto": plantilla(), "cuerpo": plantilla("texto del correo", largo=True), "html": plantilla("versión HTML opcional", largo=True),
                            "adjuntos": ARCHIVOS(), "guardar_enviados": booleano("dejar copia en Elementos enviados", True),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "para", "asunto", "cuerpo"]),
            "salidas": ["ok"], "produce": "salida {enviado, bloqueado, para, asunto, adjuntos}", "x-credencial": True,
            "ejemplo": {"id": "enviar_outlook", "tipo": "outlook_enviar", "nombre": "Mandar la propuesta", "config": {
                "credencial": "outlook-agenda", "para": "{{correo.remitente_correo}}", "asunto": "Propuesta de reunión", "cuerpo": "{{texto}}"}},
            "ejemplos": [{"credencial": "outlook-agenda", "para": "equipo@empresa.com", "asunto": "Informe", "cuerpo": "Adjunto.", "adjuntos": ["{{documento}}"]}],
            "consejos": ["Por Graph cada adjunto va hasta 3 MB (y hasta 10 en total, 20 MB); para más, comparte un enlace de OneDrive."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "outlook_responder": {
            "nombre": "Outlook · responder", "categoria": "conector",
            "descripcion": "Responde en el MISMO hilo de Outlook (createReply o createReplyAll) al correo que arrancó la ejecución (outlook_nuevo) o al de mensaje, con "
                           "adjuntos. ESCRIBE AFUERA: solo con la escritura habilitada por una persona; si no, queda retenida.",
            "config": conf({"credencial": credencial(MICROSOFT), "mensaje": mensaje_m, "cuerpo": plantilla("el texto de la respuesta", largo=True),
                            "html": plantilla("versión HTML opcional", largo=True), "para": plantilla("a quién (por defecto el remitente)"), "cc": plantilla(),
                            "asunto": plantilla("por defecto el de Outlook («RE: …»)"), "adjuntos": ARCHIVOS(),
                            "incluir_original": booleano("citar el original debajo", True), "todos": booleano("responder a todos", False),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "cuerpo"]),
            "salidas": ["ok"], "produce": "salida {enviado, bloqueado, para, asunto, adjuntos, en_respuesta_a}", "x-credencial": True,
            "ejemplo": {"id": "responder_outlook", "tipo": "outlook_responder", "nombre": "Confirmar la reunión", "config": {
                "credencial": "outlook-agenda", "cuerpo": "Quedó agendada para el {{evento.inicio}}.", "incluir_original": True}},
            "ejemplos": [{"credencial": "outlook-agenda", "mensaje": "{{correo_i.graph_id}}", "cuerpo": "{{respuesta}}", "adjuntos": ["{{documento}}"]}],
            "consejos": ["Con outlook_nuevo como disparador no hace falta mensaje.", "Para un buzón IMAP usa responder_correo con una SMTP."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "outlook_mover": {
            "nombre": "Outlook · mover", "categoria": "conector",
            "descripcion": "Mueve un correo de Outlook a otra carpeta (por nombre conocido, por su nombre visible o por id). ESCRIBE AFUERA: solo con la escritura "
                           "habilitada por una persona; si no, queda retenido.",
            "config": conf({"credencial": credencial(MICROSOFT), "mensaje": mensaje_m, "carpeta": plantilla("archive, deleteditems, junkemail, el nombre visible («Atendidos») o el id"),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "carpeta"]),
            "salidas": ["ok"], "produce": "salida {movido, carpeta, nuevo_id, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "archivar", "tipo": "outlook_mover", "nombre": "Archivar", "config": {"credencial": "outlook-agenda", "carpeta": "archive"}},
            "ejemplos": [{"credencial": "outlook-agenda", "carpeta": "Atendidos"}],
            "consejos": ["Al moverlo, Outlook le da otro id: queda en {{salida.nuevo_id}} por si lo necesitas después."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "outlook_categorizar": {
            "nombre": "Outlook · categorías", "categoria": "conector",
            "descripcion": "Pone, reemplaza o quita categorías de un correo de Outlook, y si se pide lo marca como leído o cambia su importancia. ESCRIBE AFUERA: "
                           "solo con la escritura habilitada por una persona; si no, queda retenido.",
            "config": conf({"credencial": credencial(MICROSOFT), "mensaje": mensaje_m, "categorias": lista(plantilla(), "las categorías (p. ej. «Atendido», «Urgente»)"),
                            "modo": opcion(["agregar", "reemplazar", "quitar"], "", "agregar"), "marcar_leido": booleano("marcar como leído"),
                            "importancia": opcion(["low", "normal", "high"], "importancia del correo"), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
            "salidas": ["ok"], "produce": "salida {categorias, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "categorizar", "tipo": "outlook_categorizar", "nombre": "Marcar atendido", "config": {
                "credencial": "outlook-agenda", "categorias": ["Atendido"], "marcar_leido": True}},
            "ejemplos": [{"credencial": "outlook-agenda", "categorias": ["{{tema}}"], "modo": "reemplazar"}],
            "consejos": ["Las categorías que no existen en el buzón se crean con color por defecto en Outlook."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "outlook_calendario_eventos": {
            "nombre": "Outlook · eventos", "categoria": "conector",
            "descripcion": "Lista los eventos del calendario de Outlook (vista de calendario de Graph, con las repeticiones expandidas) en un rango; solo lectura.",
            "config": conf({"credencial": credencial(MICROSOFT), "calendario": plantilla("id del calendario (vacío: el principal)"), "desde": desde, "hasta": hasta,
                            "dias": entero("días desde «desde» si no hay hasta", 1, 366, 7), "consulta": plantilla("texto que contiene el título"), "zona": zona,
                            "max": entero("cuántos eventos", 1, 250, 50), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
            "salidas": ["ok", "vacio"], "produce": "salida (lista de {id, titulo, inicio, fin, todo_el_dia, lugar, descripcion, asistentes, organizador, enlace, teams})", "x-credencial": True,
            "ejemplo": {"id": "agenda_outlook", "tipo": "outlook_calendario_eventos", "nombre": "Agenda de mañana", "config": {"credencial": "outlook-agenda", "desde": "mañana", "dias": 1, "salida": "eventos"}},
            "ejemplos": [{"credencial": "outlook-agenda", "desde": "hoy", "dias": 7, "salida": "eventos"}],
            "consejos": ["Para saber si hay espacio, compara los eventos con un nodo expresion o usa la disponibilidad de Google si la agenda es de Google."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "outlook_calendario_crear": {
            "nombre": "Outlook · crear evento", "categoria": "conector",
            "descripcion": "Crea un evento en el calendario de Outlook con invitados y, si se pide, reunión de Teams; Outlook manda las invitaciones. ESCRIBE AFUERA: "
                           "solo con la escritura habilitada por una persona; si no, queda retenido completo. Nunca invita direcciones de Positiva.",
            "config": conf({"credencial": credencial(MICROSOFT), "calendario": plantilla("id del calendario (vacío: el principal)"), "titulo": plantilla("asunto del evento"),
                            "inicio": plantilla("inicio: «2026-11-10 15:00» o ISO"), "fin": plantilla("fin (o duracion_min)"), "duracion_min": entero("si no hay fin", 5, 1440, 30),
                            "todo_el_dia": booleano("de día completo", False), "zona": zona, "descripcion": plantilla(largo=True), "lugar": plantilla(),
                            "asistentes": asistentes, "con_teams": booleano("reunión en línea de Teams", False),
                            "recordatorio_min": entero("recordatorio, minutos antes", 0, 40320), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()},
                           ["credencial", "titulo", "inicio"]),
            "salidas": ["ok"], "produce": "salida {id, enlace, teams, inicio, fin, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "agendar_outlook", "tipo": "outlook_calendario_crear", "nombre": "Agendar en Outlook", "config": {
                "credencial": "outlook-agenda", "titulo": "{{correo.asunto}}", "inicio": "{{propuesta.inicio}}", "duracion_min": 30,
                "asistentes": "{{correo.remitente_correo}}", "con_teams": True}},
            "ejemplos": [{"credencial": "outlook-agenda", "titulo": "Comité", "inicio": "2026-11-12 09:00", "fin": "2026-11-12 10:00", "asistentes": ["ana@empresa.com"]}],
            "consejos": ["Con una conexión de aplicación (microsoft_graph) el evento se crea en el calendario de «buzon».",
                         "Combínalo con ia_extraer (fecha, hora, tema) sobre el correo para agendar lo que piden."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        # ===================================================== OneDrive y SharePoint
        "onedrive_listar": {
            "nombre": "OneDrive · listar", "categoria": "conector",
            "descripcion": "Lista los archivos de una carpeta de OneDrive (por ruta o id) o busca por nombre; solo lectura.",
            "config": conf({"credencial": credencial(MICROSOFT), "carpeta": plantilla("ruta («/Documentos/Facturas») o id; vacío: la raíz"),
                            "buscar": plantilla("buscar por nombre o contenido en todo el OneDrive"), "max": entero("cuántos", 1, 200, 50),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
            "salidas": ["ok", "vacio"], "produce": "salida (lista de {id, nombre, ruta, tipo, tamano, modificado, enlace, carpeta})", "x-credencial": True,
            "ejemplo": {"id": "facturas_od", "tipo": "onedrive_listar", "nombre": "Facturas en OneDrive", "config": {"credencial": "outlook-agenda", "carpeta": "/Facturas", "salida": "elementos"}},
            "ejemplos": [{"credencial": "outlook-agenda", "buscar": "contrato", "salida": "elementos"}],
            "consejos": ["Cada elemento trae id: léelo con onedrive_leer (archivo «{{elemento.id}}»)."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "onedrive_leer": {
            "nombre": "OneDrive · leer", "categoria": "conector",
            "descripcion": "Baja un archivo de OneDrive (por ruta o id): como texto (Word, PDF, Excel y escaneos pasan por la conversión con OCR) o como archivo de la "
                           "ejecución para adjuntarlo o procesarlo; solo lectura.",
            "config": conf({"credencial": credencial(MICROSOFT), "archivo": plantilla("ruta («/Plantillas/carta.docx») o id"),
                            "como": opcion(["texto", "archivo"], "texto: lo convierte; archivo: lo deja como archivo de la ejecución", "texto"),
                            "ocr": booleano("OCR en escaneos", True), "max_caracteres": entero("", 1, 500000, 60000),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "archivo"]),
            "salidas": ["ok"], "produce": "salida {id, nombre, tipo, texto} o la ficha del archivo", "x-credencial": True,
            "ejemplo": {"id": "leer_od", "tipo": "onedrive_leer", "nombre": "Leer el contrato", "config": {"credencial": "outlook-agenda", "archivo": "/Contratos/{{entrada.numero}}.pdf", "salida": "contrato"}},
            "ejemplos": [{"credencial": "outlook-agenda", "archivo": "{{elemento.id}}", "como": "archivo", "salida": "archivo_od"}],
            "consejos": ["Con como: archivo queda la ficha {id, nombre}: adjúntala con «{{archivo_od}}»."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "onedrive_subir": {
            "nombre": "OneDrive · subir", "categoria": "conector",
            "descripcion": "Sube a una carpeta de OneDrive un archivo de la ejecución tal cual (documento, imagen, adjunto) o un texto, JSON o CSV. ESCRIBE AFUERA: "
                           "solo con la escritura habilitada por una persona; si no, queda retenido.",
            "config": conf({"credencial": credencial(MICROSOFT), "carpeta": plantilla("ruta de la carpeta destino («/Informes/2026»); vacío: la raíz"),
                            "archivo": ARCHIVOS("el archivo a subir tal cual: «{{documento}}», «{{adjuntos}}» o un filtro (se sube el primero)"),
                            "nombre": plantilla("nombre (con archivo, por defecto el suyo)"), "contenido": plantilla("o un texto", largo=True), "de": plantilla("o una variable"),
                            "formato": opcion(["texto", "json", "csv"], "", "texto"), "conflicto": opcion(["renombrar", "reemplazar", "fallar"], "si ya existe", "renombrar"),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
            "salidas": ["ok"], "produce": "salida {id, nombre, enlace, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "subir_od", "tipo": "onedrive_subir", "nombre": "Guardar el informe", "config": {"credencial": "outlook-agenda", "carpeta": "/Informes", "archivo": "{{documento}}"}},
            "ejemplos": [{"credencial": "outlook-agenda", "carpeta": "/Datos", "nombre": "resultado.csv", "de": "filas", "formato": "csv"}],
            "consejos": ["Hasta 250 MB por archivo (se sube en partes si pasa de 4 MB)."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "sharepoint_archivos": {
            "nombre": "SharePoint · archivos", "categoria": "conector",
            "descripcion": "Archivos de una biblioteca de documentos de un sitio de SharePoint: listar una carpeta, leer un archivo (texto o archivo de la ejecución) o "
                           "subir uno. Subir ESCRIBE AFUERA: solo con la escritura habilitada por una persona; si no, queda retenido.",
            "config": conf({"credencial": credencial(MICROSOFT), "sitio": sitio, "biblioteca": plantilla("nombre de la biblioteca (vacío: la de documentos del sitio)"),
                            "operacion": opcion(["listar", "leer", "subir"], "", "listar"), "carpeta": plantilla("ruta dentro de la biblioteca («/Contratos/2026»)"),
                            "archivo": libre("leer: ruta o id del archivo; subir: el archivo de la ejecución («{{documento}}»)"),
                            "nombre": plantilla("subir: nombre (por defecto el del archivo)"), "contenido": plantilla("subir: o un texto", largo=True), "de": plantilla("subir: o una variable"),
                            "formato": opcion(["texto", "json", "csv"], "", "texto"), "como": opcion(["texto", "archivo"], "leer: texto o archivo de la ejecución", "texto"),
                            "conflicto": opcion(["renombrar", "reemplazar", "fallar"], "subir: si ya existe", "renombrar"), "max": entero("listar: cuántos", 1, 200, 50),
                            "ocr": booleano("leer: OCR en escaneos", True), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "sitio"]),
            "salidas": ["ok", "vacio (al listar)"], "produce": "salida: lista (listar), {texto} o ficha (leer), {id, enlace, bloqueado} (subir)", "x-credencial": True,
            "ejemplo": {"id": "contratos_sp", "tipo": "sharepoint_archivos", "nombre": "Contratos del sitio", "config": {
                "credencial": "m365-docs", "sitio": "https://contoso.sharepoint.com/sites/Juridica", "operacion": "listar", "carpeta": "/Contratos", "salida": "contratos"}},
            "ejemplos": [{"credencial": "m365-docs", "sitio": "contoso.sharepoint.com:/sites/Juridica", "operacion": "subir", "carpeta": "/Respuestas", "archivo": "{{documento}}"},
                         {"credencial": "m365-docs", "sitio": "contoso.sharepoint.com:/sites/Juridica", "operacion": "leer", "archivo": "/Plantillas/acta.docx", "como": "archivo", "salida": "acta"}],
            "consejos": ["Con una conexión de aplicación hace falta el permiso Sites.ReadWrite.All (o Sites.Selected con el sitio concedido)."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "sharepoint_lista": {
            "nombre": "SharePoint · lista", "categoria": "conector",
            "descripcion": "Lee los elementos de una lista de SharePoint (con filtro OData sobre sus campos) o agrega uno nuevo. Agregar ESCRIBE AFUERA: solo con la "
                           "escritura habilitada por una persona; si no, queda retenido.",
            "config": conf({"credencial": credencial(MICROSOFT), "sitio": sitio, "lista": plantilla("nombre visible o id de la lista"),
                            "operacion": opcion(["leer", "agregar"], "", "leer"), "filtro": plantilla("leer: filtro OData sobre fields, p. ej. «fields/Estado eq 'Pendiente'»"),
                            "campos": mapa(plantilla(), "agregar: {nombre interno de la columna: plantilla}"), "max": entero("leer: cuántos", 1, 5000, 200),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "sitio", "lista"]),
            "salidas": ["ok", "vacio (al leer)"], "produce": "salida: lista de {id, campos...} (leer) o {id, bloqueado} (agregar)", "x-credencial": True,
            "ejemplo": {"id": "pendientes_sp", "tipo": "sharepoint_lista", "nombre": "Solicitudes pendientes", "config": {
                "credencial": "m365-docs", "sitio": "contoso.sharepoint.com:/sites/Operaciones", "lista": "Solicitudes", "filtro": "fields/Estado eq 'Pendiente'", "salida": "solicitudes"}},
            "ejemplos": [{"credencial": "m365-docs", "sitio": "contoso.sharepoint.com:/sites/Operaciones", "lista": "Solicitudes", "operacion": "agregar",
                          "campos": {"Title": "{{correo.asunto}}", "Solicitante": "{{correo.remitente_correo}}"}}],
            "consejos": ["Usa el nombre INTERNO de cada columna (el de la URL de la columna), no el visible.",
                         "Para filtrar por columnas sin índice, Graph pide el encabezado Prefer: HonorNonIndexedQueriesWarningMayFailRandomly (se manda solo)."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "teams_mensaje": {
            "nombre": "Teams · mensaje (Graph)", "categoria": "integracion",
            "descripcion": "Publica un mensaje en un canal de Teams o en un chat por Microsoft Graph, como la persona que conectó (microsoft_oauth), con archivos como "
                           "enlaces de descarga que vencen. ESCRIBE AFUERA: solo con la escritura habilitada por una persona. Complementa mensaje_teams (webhook).",
            "config": conf({"credencial": credencial(["microsoft_oauth"]), "destino": opcion(["canal", "chat"], "dónde publicar", "canal"),
                            "equipo": plantilla("canal: id del equipo"), "canal": plantilla("canal: id del canal (19:…@thread.tacv2)"), "chat": plantilla("chat: id del chat (19:…)"),
                            "titulo": plantilla("canal: asunto del mensaje"), "texto": plantilla("el mensaje", largo=True), "formato": opcion(["texto", "html"], "", "texto"),
                            "archivos": ARCHIVOS("archivos a compartir: van como enlaces de descarga firmados que vencen"),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "texto"]),
            "salidas": ["ok"], "produce": "salida {id, enlace, enviado, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "avisar_canal", "tipo": "teams_mensaje", "nombre": "Avisar en el canal", "config": {
                "credencial": "teams-ana", "destino": "canal", "equipo": "{{entorno.EQUIPO_ID}}", "canal": "{{entorno.CANAL_ID}}", "titulo": "Nuevo caso", "texto": "{{resumen}}"}},
            "ejemplos": [{"credencial": "teams-ana", "destino": "chat", "chat": "19:abc123@thread.v2", "texto": "Listo el informe: {{documento.nombre}}", "archivos": ["{{documento}}"]}],
            "consejos": ["Los ids del equipo y del canal salen de «Obtener vínculo al canal» en Teams (groupId y el 19:…).",
                         "Graph no deja publicar con una conexión de aplicación: hace falta microsoft_oauth (permisos ChannelMessage.Send y ChatMessage.Send)."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        "excel_en_linea": {
            "nombre": "Excel en línea", "categoria": "conector",
            "descripcion": "Lee o escribe un libro de Excel guardado en OneDrive o SharePoint sin bajarlo: una tabla (por nombre) o un rango de una hoja. Leer deja "
                           "filas como objetos; anexar agrega filas a la tabla; escribir pone valores en un rango. Anexar y escribir ESCRIBEN AFUERA: solo con la "
                           "escritura habilitada por una persona.",
            "config": conf({"credencial": credencial(MICROSOFT), "sitio": plantilla("si el libro está en SharePoint: el sitio (si no, OneDrive)"),
                            "archivo": plantilla("ruta («/Datos/ventas.xlsx») o id del libro"), "hoja": plantilla("nombre de la hoja (con rango)"),
                            "tabla": plantilla("nombre de la tabla de Excel"), "rango": plantilla("rango, p. ej. «A1:F200» (con hoja)"),
                            "operacion": opcion(["leer", "anexar", "escribir"], "", "leer"), "filas": plantilla("anexar o escribir: variable con la lista de objetos"),
                            "fila": mapa(plantilla(), "anexar: una fila {columna: plantilla}"), "encabezados": booleano("leer: la primera fila son los nombres", True),
                            "max_filas": entero("leer: tope de filas", 1, 5000, 500), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "archivo"]),
            "salidas": ["ok", "vacio (al leer)"], "produce": "salida: filas (leer) o {agregadas, rango, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "ventas", "tipo": "excel_en_linea", "nombre": "Leer ventas", "config": {
                "credencial": "outlook-agenda", "archivo": "/Datos/ventas.xlsx", "tabla": "Ventas", "operacion": "leer", "salida": "filas"}},
            "ejemplos": [{"credencial": "outlook-agenda", "archivo": "/Datos/registro.xlsx", "tabla": "Registro", "operacion": "anexar",
                          "fila": {"Fecha": "{{hoy}}", "Asunto": "{{correo.asunto}}"}},
                         {"credencial": "outlook-agenda", "archivo": "/Datos/ventas.xlsx", "hoja": "Resumen", "rango": "A1:C50", "salida": "filas"}],
            "consejos": ["Con tabla, anexar respeta el orden de las columnas de la tabla (por su encabezado).",
                         "Para un Excel que llega como adjunto usa hoja_leer; este nodo es para el libro que vive en OneDrive o SharePoint."],
            "preparar": _PREPARAR_MICROSOFT,
        },
        # ============================================================= OpenAPI
        "api_operacion": {
            "nombre": "API · operación (OpenAPI)", "categoria": "integracion",
            "descripcion": "Llama una operación de cualquier API descrita con OpenAPI/Swagger (credencial api_openapi con la especificación, la URL base y la "
                           "autenticación): se elige por operationId o «MÉTODO /ruta», se revisan los parámetros obligatorios y sus tipos contra la "
                           "especificación, y se pueden juntar varias páginas. GET lee; los demás métodos ESCRIBEN AFUERA: solo con la escritura habilitada "
                           "por una persona (si no, la petición queda retenida). Nunca a Positiva, SGDEA, metadatos ni IP internas.",
            "config": conf({"credencial": credencial(["api_openapi"]), "operacion": texto("operationId (p. ej. «listPets») o «GET /pets/{petId}»"),
                            "parametros": mapa(plantilla(), "{nombre: plantilla} de los parámetros de ruta, consulta y encabezado de la operación"),
                            "cuerpo": libre("el cuerpo JSON (objeto con plantillas) o un texto"), "paginacion": paginacion,
                            "mapeo": lista(objeto({"variable": h.variable("dónde dejar el valor"), "ruta": texto("ruta en la respuesta"),
                                                   "defecto": libre("si no está"), "obligatorio": booleano("", False)}, ["variable", "ruta"]), "valores de la respuesta en variables"),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "operacion"]),
            "salidas": ["ok", "error_http"], "produce": "salida {status, ok, datos, items (con paginación), operacion, simulado}, variables del mapeo", "x-credencial": True,
            "ejemplo": {"id": "mascota", "tipo": "api_operacion", "nombre": "Consultar la mascota", "config": {
                "credencial": "api-petstore", "operacion": "showPetById", "parametros": {"petId": "{{entrada.id}}"}, "salida": "mascota"}},
            "ejemplos": [{"credencial": "api-petstore", "operacion": "GET /pets", "parametros": {"limit": "50"},
                          "paginacion": {"tipo": "cursor", "parametro": "cursor", "ruta_cursor": "next", "ruta_items": "items", "max_paginas": 5}, "salida": "mascotas"},
                         {"credencial": "api-petstore", "operacion": "createPets", "cuerpo": {"id": 7, "name": "{{entrada.nombre}}"}, "salida": "creada"}],
            "consejos": ["Pide las operaciones de la conexión con «Analizar» en Conexiones (POST /api/conectores/openapi/analizar): nombre, método, ruta y parámetros.",
                         "Si la respuesta es ≥ 400 sale por «error_http» con status y datos; con paginación, «items» junta los elementos de todas las páginas.",
                         "Las IP privadas solo se aceptan si una persona creó la conexión y marcó red_privada (como las bases de datos)."],
            "preparar": ["En Conexiones crea una «API (OpenAPI)»: pega la URL de la especificación (JSON o YAML) o la especificación misma, la URL base si "
                         "cambia, y la autenticación (ninguna, llave en encabezado o consulta, Bearer, usuario y clave u OAuth2 de cliente).",
                         "Pulsa «Analizar» para ver las operaciones y «Probar conexión» para confirmar que responde.",
                         "Para POST, PUT, PATCH o DELETE una persona tiene que habilitar la escritura de la conexión."],
        },
    }
    T.update(_ext.tipos(h))  # fase 2: páginas sin API, almacenamiento, datos y mensajería transaccional
    T.update(_trab.tipos(h))  # fase 3: trabajo y clientes, firma electrónica
    return T


import especificacion_externos as _ext  # noqa: E402
import especificacion_trabajo as _trab  # noqa: E402

# Los tipos de cada familia (para el motor y las pruebas).
DISPARADORES = {"gmail_nuevo", "outlook_nuevo", "calendar_evento_proximo"}
DISPARADORES_DE_CORREO = {"gmail_nuevo", "outlook_nuevo"}
ESCRITURA = {"gmail_enviar", "gmail_responder", "gmail_etiquetar", "calendar_crear", "calendar_actualizar", "docs_crear_desde_plantilla",
             "outlook_enviar", "outlook_responder", "outlook_mover", "outlook_categorizar", "outlook_calendario_crear", "onedrive_subir",
             "sharepoint_archivos", "sharepoint_lista", "teams_mensaje", "excel_en_linea", "api_operacion"}
LECTURA = {"gmail_buscar", "calendar_eventos", "calendar_disponibilidad", "forms_respuestas", "outlook_buscar", "outlook_calendario_eventos",
           "onedrive_listar", "onedrive_leer"}
DISPARADORES |= _ext.DISPARADORES | _trab.DISPARADORES
ESCRITURA |= _ext.ESCRITURA | _trab.ESCRITURA
LECTURA |= _ext.LECTURA | _trab.LECTURA
TIPOS = DISPARADORES | ESCRITURA | LECTURA
# Hablan con un servicio de afuera: aceptan reintentos ante fallos pasajeros (los disparadores no).
CON_REINTENTOS = ESCRITURA | LECTURA
# Las conexiones que sirve cada tipo.
CREDENCIALES_DE = {
    "gmail_nuevo": tuple(GOOGLE), "gmail_buscar": tuple(GOOGLE), "gmail_enviar": tuple(GOOGLE), "gmail_responder": tuple(GOOGLE),
    "gmail_etiquetar": tuple(GOOGLE), "calendar_eventos": tuple(GOOGLE_TODAS), "calendar_crear": tuple(GOOGLE_TODAS),
    "calendar_actualizar": tuple(GOOGLE_TODAS), "calendar_disponibilidad": tuple(GOOGLE_TODAS), "docs_crear_desde_plantilla": tuple(GOOGLE_TODAS),
    "forms_respuestas": tuple(GOOGLE_TODAS), "calendar_evento_proximo": tuple(CALENDARIOS), "teams_mensaje": ("microsoft_oauth",),
    "api_operacion": ("api_openapi",),
    **{t: tuple(MICROSOFT) for t in ("outlook_nuevo", "outlook_buscar", "outlook_enviar", "outlook_responder", "outlook_mover", "outlook_categorizar",
                                       "outlook_calendario_eventos", "outlook_calendario_crear", "onedrive_listar", "onedrive_leer", "onedrive_subir",
                                       "sharepoint_archivos", "sharepoint_lista", "excel_en_linea")},
    **_ext.CREDENCIALES_DE,
    **_trab.CREDENCIALES_DE,
}
