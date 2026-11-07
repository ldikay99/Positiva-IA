"""
La ficha de los nodos de la fase 3 de integraciones (plan 5d): trabajo y
clientes. Jira, Trello, Asana y Notion (gestión de trabajo), HubSpot, Salesforce
y Zoho CRM (clientes) y DocuSign (firma electrónica). Un nodo por servicio con
su `operacion`: leer sale libre; todo lo que crea, cambia, comenta o envía
queda retenido mientras una persona no habilite la escritura de la conexión.

Igual que `especificacion_externos.py`: `tipos(h)` recibe `nodos_declarativos`
y devuelve {tipo: ficha} con config (JSON Schema), salidas, lo que deja, un
ejemplo, `ejemplos`, `consejos` y `preparar`. Lo ejecuta `nodos_trabajo.py`;
el zod del servidor está en `servidor/src/flujos_trabajo.ts`.
"""
from __future__ import annotations

from typing import Any, Dict

_ESCRITURA = "Para que lo que escribe salga de verdad una persona tiene que habilitar la escritura de la conexión; si no, queda retenido con todo armado en el detalle de la ejecución (leer sí funciona)."
_POSITIVA = "Nunca se escriben direcciones de Positiva ni los buzones protegidos: si aparecen en lo que se va a escribir, el paso se detiene."

# las operaciones de cada nodo y cuáles solo leen (las pruebas y el zod usan las mismas)
OPERACIONES = {
    "jira_issues": ["buscar", "ver", "crear", "comentar", "cambiar_estado"],
    "trello_tarjetas": ["tableros", "listas", "tarjetas", "crear", "mover", "comentar"],
    "asana_tareas": ["listar", "ver", "crear", "completar", "comentar"],
    "notion_paginas": ["consultar", "crear", "actualizar", "agregar_bloques"],
    "hubspot_crm": ["buscar", "ver", "crear", "actualizar", "nota"],
    "salesforce_registros": ["consultar", "ver", "crear", "actualizar"],
    "zoho_crm": ["buscar", "ver", "crear", "actualizar"],
    "docusign_sobre": ["enviar", "estado", "descargar"],
}
LEEN = {"buscar", "ver", "tableros", "listas", "tarjetas", "listar", "consultar", "estado", "descargar"}
LISTAN = {"buscar", "tableros", "listas", "tarjetas", "listar", "consultar"}
POR_DEFECTO = {t: ops[0] for t, ops in OPERACIONES.items()}
OPERADORES_HUBSPOT = ["EQ", "NEQ", "LT", "LTE", "GT", "GTE", "CONTAINS_TOKEN", "NOT_CONTAINS_TOKEN", "HAS_PROPERTY", "NOT_HAS_PROPERTY", "BETWEEN", "IN", "NOT_IN"]


def tipos(h: Any) -> Dict[str, Dict[str, Any]]:
    conf, credencial, plantilla, texto, entero = h.conf, h.credencial, h.plantilla, h.texto, h.entero
    booleano, opcion, lista, objeto, libre = h.booleano, h.opcion, h.lista, h.objeto, h.libre
    SALIDA, TIMEOUT, ARCHIVOS, REINT = h.SALIDA, h.TIMEOUT, h.ARCHIVOS, h._REINTENTOS
    comunes = {"reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}

    def op(t: str, desc: str) -> Dict[str, Any]:
        return opcion(OPERACIONES[t], desc, POR_DEFECTO[t])

    T: Dict[str, Dict[str, Any]] = {
        # ================================================================ Jira
        "jira_issues": {
            "nombre": "Jira · issues", "categoria": "conector",
            "descripcion": "Trabaja con los issues de Jira Cloud (API REST v3, conexión jira): buscar con JQL (con paginación), ver uno, crear, comentar o "
                           "cambiar de estado (por el nombre de la transición o del estado al que lleva). Buscar y ver solo leen. Crear, comentar y "
                           "cambiar_estado ESCRIBEN AFUERA: solo con la escritura habilitada por una persona; si no, quedan retenidos con todo armado.",
            "config": conf({"credencial": credencial(["jira"]), "operacion": op("jira_issues", "buscar y ver leen; crear, comentar y cambiar_estado escriben"),
                            "jql": plantilla("buscar: la consulta JQL; los {{valores}} se escapan dentro de comillas (p. ej. project = SOP AND text ~ \"{{correo.asunto}}\")", largo=True),
                            "campos": lista(texto(), "buscar o ver: campos de Jira que trae (por defecto resumen, estado, tipo, prioridad, asignado, fechas, etiquetas y descripción)"),
                            "issue": plantilla("ver, comentar o cambiar_estado: la clave del issue (p. ej. SOP-123 o «{{issue.clave}}»)"),
                            "proyecto": plantilla("crear: la clave del proyecto (p. ej. SOP)"), "tipo_issue": plantilla("crear: el tipo (Task, Bug, Story… como se llame en el proyecto)", "Task"),
                            "resumen": plantilla("crear: el título del issue"), "descripcion": plantilla("crear: la descripción (texto; las líneas «- » quedan como lista)", largo=True),
                            "prioridad": plantilla("crear: la prioridad (Highest, High, Medium, Low… como se llame en el sitio)"),
                            "etiquetas": lista(texto(), "crear: etiquetas (sin espacios)"), "asignado": plantilla("crear: el accountId de quien queda asignado"),
                            "campos_extra": libre("crear: otros campos de Jira tal cual, p. ej. {\"customfield_10010\": \"x\", \"duedate\": \"2026-12-01\"}"),
                            "comentario": plantilla("comentar: el texto del comentario", largo=True),
                            "estado": plantilla("cambiar_estado: la transición o el estado destino (p. ej. «En curso», «Done»)"),
                            "max": entero("buscar: tope de issues", 1, 1000, 50), **comunes}, ["credencial"]),
            "salidas": ["ok", "vacio (al buscar)"],
            "produce": "salida: lista [{clave, id, url, resumen, estado, tipo, prioridad, asignado, creado, descripcion…}] (buscar), el issue (ver), "
                       "{clave, url, bloqueado} (crear), {id, bloqueado} (comentar), {estado, transicion, bloqueado} (cambiar_estado)",
            "x-credencial": True,
            "ejemplo": {"id": "crear_issue", "tipo": "jira_issues", "nombre": "Crear el issue", "config": {
                "credencial": "jira-soporte", "operacion": "crear", "proyecto": "SOP", "tipo_issue": "Bug", "resumen": "Error reportado: {{correo.asunto}}",
                "descripcion": "Reportado por {{correo.remitente}}\n\n{{correo.cuerpo}}", "prioridad": "High", "etiquetas": ["correo"], "salida": "issue"}},
            "ejemplos": [{"credencial": "jira-soporte", "operacion": "buscar", "jql": "project = SOP AND statusCategory != Done ORDER BY created DESC", "max": 20,
                          "salida": "abiertos"},
                         {"credencial": "jira-soporte", "operacion": "cambiar_estado", "issue": "SOP-12", "estado": "En curso"}],
            "consejos": ["Para no duplicar: busca primero con JQL (p. ej. summary ~ \"{{correo.asunto}}\") y crea solo si salió por «vacio».",
                         "Después de crear, la clave queda en «{{issue.clave}}» y el enlace en «{{issue.url}}»: úsalos en el aviso por Telegram, Teams o correo.",
                         "Si cambiar_estado no encuentra la transición, el error lista las que sí hay desde el estado actual."],
            "preparar": ["Crea un token de API en https://id.atlassian.com/manage-profile/security/api-tokens con la cuenta que va a usar el flujo.",
                         "En Conexiones crea una «Jira Cloud» con el sitio (https://tuempresa.atlassian.net), el correo de esa cuenta y el token; pulsa «Probar conexión» (solo lee tu perfil).",
                         _ESCRITURA, _POSITIVA],
        },
        # ============================================================== Trello
        "trello_tarjetas": {
            "nombre": "Trello · tableros y tarjetas", "categoria": "conector",
            "descripcion": "Trabaja con Trello (conexión trello): listar tableros, las listas de un tablero o las tarjetas de una lista o de un tablero, y crear, "
                           "mover o comentar tarjetas. Listar solo lee. Crear, mover y comentar ESCRIBEN AFUERA: solo con la escritura habilitada por una "
                           "persona; si no, quedan retenidos.",
            "config": conf({"credencial": credencial(["trello"]), "operacion": op("trello_tarjetas", "tableros, listas y tarjetas leen; crear, mover y comentar escriben"),
                            "tablero": plantilla("listas o tarjetas: el id del tablero (o su enlace https://trello.com/b/…)"),
                            "lista": plantilla("tarjetas: la lista que se lee; crear: la lista donde queda; mover: la lista destino (su id)"),
                            "tarjeta": plantilla("mover o comentar: el id de la tarjeta (o su enlace https://trello.com/c/…)"),
                            "nombre": plantilla("crear: el título de la tarjeta"), "descripcion": plantilla("crear: la descripción", largo=True),
                            "vence": plantilla("crear: la fecha de vencimiento (ISO, p. ej. 2026-12-01 o 2026-12-01T17:00:00-05:00)"),
                            "etiquetas": lista(texto(), "crear: ids de etiquetas del tablero"),
                            "posicion": opcion(["arriba", "abajo"], "crear o mover: dónde queda en la lista", "abajo"),
                            "comentario": plantilla("comentar: el texto", largo=True), "max": entero("tableros, listas o tarjetas: tope", 1, 1000, 100), **comunes},
                           ["credencial"]),
            "salidas": ["ok", "vacio (al listar)"],
            "produce": "salida: lista de tableros {id, nombre, url}, de listas {id, nombre} o de tarjetas {id, nombre, descripcion, lista, vence, url, etiquetas}; "
                       "{id, url, bloqueado} al crear; {id, lista, bloqueado} al mover; {id, bloqueado} al comentar",
            "x-credencial": True,
            "ejemplo": {"id": "tarjeta", "tipo": "trello_tarjetas", "nombre": "Tarjeta del pedido", "config": {
                "credencial": "trello-equipo", "operacion": "crear", "lista": "5f1a2b3c4d5e6f7a8b9c0d1e", "nombre": "Pedido de {{datos.cliente}}",
                "descripcion": "{{resumen}}", "vence": "{{datos.fecha_entrega}}", "salida": "tarjeta"}},
            "ejemplos": [{"credencial": "trello-equipo", "operacion": "listas", "tablero": "https://trello.com/b/AbCd1234/operaciones", "salida": "listas"},
                         {"credencial": "trello-equipo", "operacion": "mover", "tarjeta": "{{tarjeta_i.id}}", "lista": "5f1a2b3c4d5e6f7a8b9c0d1f"}],
            "consejos": ["¿No sabes el id de la lista? Corre primero «listas» con el enlace del tablero y toma el id de «{{listas}}».",
                         "Para recorrer las tarjetas usa para_cada sobre «{{pendientes}}».",
                         "La llave y el token viajan en el encabezado Authorization, nunca en la URL."],
            "preparar": ["Crea un Power-Up en https://trello.com/power-ups/admin, copia su API key y genera un token (el enlace «Token» de esa página) con la cuenta que va a usar el flujo.",
                         "En Conexiones crea una «Trello» con la API key y el token; «Probar conexión» solo lee tu usuario.", _ESCRITURA, _POSITIVA],
        },
        # =============================================================== Asana
        "asana_tareas": {
            "nombre": "Asana · tareas", "categoria": "conector",
            "descripcion": "Trabaja con las tareas de Asana (conexión asana): listar las de un proyecto o las asignadas a alguien (con paginación), ver una, "
                           "crear, completar o comentar. Listar y ver solo leen. Crear, completar y comentar ESCRIBEN AFUERA: solo con la escritura "
                           "habilitada por una persona; si no, quedan retenidos.",
            "config": conf({"credencial": credencial(["asana"]), "operacion": op("asana_tareas", "listar y ver leen; crear, completar y comentar escriben"),
                            "proyecto": plantilla("listar o crear: el gid del proyecto (o su enlace https://app.asana.com/0/…)"),
                            "asignado": plantilla("listar: «me» o el correo de la persona (con espacio); crear: a quién se asigna"),
                            "espacio": plantilla("el gid del espacio de trabajo (si no, el de la conexión)"),
                            "tarea": plantilla("ver, completar o comentar: el gid de la tarea"),
                            "nombre": plantilla("crear: el nombre de la tarea"), "notas": plantilla("crear: la descripción", largo=True),
                            "vence": plantilla("crear: la fecha límite (AAAA-MM-DD)"), "solo_pendientes": booleano("listar: solo las que no están completas", True),
                            "comentario": plantilla("comentar: el texto", largo=True), "max": entero("listar: tope de tareas", 1, 1000, 100), **comunes},
                           ["credencial"]),
            "salidas": ["ok", "vacio (al listar)"],
            "produce": "salida: lista [{id, nombre, notas, completada, vence, asignado, url, proyectos}] (listar), la tarea (ver), {id, url, bloqueado} (crear), "
                       "{id, completada, bloqueado} (completar), {id, bloqueado} (comentar)",
            "x-credencial": True,
            "ejemplo": {"id": "tarea_asana", "tipo": "asana_tareas", "nombre": "Tarea para el equipo", "config": {
                "credencial": "asana-equipo", "operacion": "crear", "proyecto": "1204567890123456", "nombre": "Revisar solicitud de {{datos.solicitante}}",
                "notas": "{{resumen}}", "vence": "{{datos.fecha_limite}}", "salida": "tarea"}},
            "ejemplos": [{"credencial": "asana-equipo", "operacion": "listar", "proyecto": "1204567890123456", "max": 50, "salida": "tareas"},
                         {"credencial": "asana-equipo", "operacion": "comentar", "tarea": "{{tarea.id}}", "comentario": "Listo: {{respuesta}}"}],
            "consejos": ["Listar por asignado necesita el espacio de trabajo: ponlo en la conexión o en espacio (Probar conexión te dice los gid de tus espacios).",
                         "Crear sin proyecto deja la tarea en «Mis tareas» del espacio de la conexión."],
            "preparar": ["En Asana: tu foto › Configuración › Aplicaciones › Herramientas para desarrolladores › «Crear token de acceso personal».",
                         "En Conexiones crea una «Asana» con el token y, si quieres, el gid del espacio por defecto; «Probar conexión» lista tus espacios.",
                         _ESCRITURA, _POSITIVA],
        },
        # ============================================================== Notion
        "notion_paginas": {
            "nombre": "Notion · bases y páginas", "categoria": "conector",
            "descripcion": "Trabaja con Notion (conexión notion): consultar una base de datos (filtro y orden de Notion, con paginación), crear una página en una "
                           "base, actualizar sus propiedades o agregarle contenido (bloques). Las propiedades van como valores simples "
                           "({\"Nombre\": \"…\", \"Estado\": \"Hecho\", \"Fecha\": \"2026-12-01\"}) y se convierten al tipo de cada columna. Consultar solo "
                           "lee. Crear, actualizar y agregar_bloques ESCRIBEN AFUERA: solo con la escritura habilitada por una persona.",
            "config": conf({"credencial": credencial(["notion"]), "operacion": op("notion_paginas", "consultar lee; crear, actualizar y agregar_bloques escriben"),
                            "base_datos": plantilla("consultar o crear: el id de la base de datos (o su enlace de Notion)"),
                            "pagina": plantilla("actualizar o agregar_bloques: el id de la página (o su enlace)"),
                            "filtro": libre("consultar: el filtro de Notion, p. ej. {\"property\": \"Estado\", \"status\": {\"equals\": \"Pendiente\"}}"),
                            "orden": lista(libre(), "consultar: p. ej. [{\"property\": \"Fecha\", \"direction\": \"descending\"}]"),
                            "propiedades": libre("crear o actualizar: {columna: valor simple o plantilla} (título, texto, número, selección, varias, estado, fecha, casilla, url, correo, teléfono)"),
                            "contenido": plantilla("crear o agregar_bloques: el texto de la página; «# », «## » títulos, «- » viñetas, «1. » numeradas, «[ ] » tareas", largo=True),
                            "max": entero("consultar: tope de páginas", 1, 1000, 100), **comunes}, ["credencial"]),
            "salidas": ["ok", "vacio (al consultar)"],
            "produce": "salida: lista [{id, url, creada, editada, propiedades: {columna: valor}}] (consultar), {id, url, bloqueado} (crear), "
                       "{id, cambiadas, bloqueado} (actualizar), {id, bloques, bloqueado} (agregar_bloques)",
            "x-credencial": True,
            "ejemplo": {"id": "registro_notion", "tipo": "notion_paginas", "nombre": "Registrar en Notion", "config": {
                "credencial": "notion-equipo", "operacion": "crear", "base_datos": "https://www.notion.so/equipo/2f1c4b5a6d7e8f90a1b2c3d4e5f60718?v=1",
                "propiedades": {"Nombre": "{{datos.solicitante}}", "Estado": "Pendiente", "Fecha": "{{datos.fecha}}"}, "contenido": "# Resumen\n{{resumen}}", "salida": "pagina"}},
            "ejemplos": [{"credencial": "notion-equipo", "operacion": "consultar", "base_datos": "2f1c4b5a6d7e8f90a1b2c3d4e5f60718",
                          "filtro": {"property": "Estado", "status": {"equals": "Pendiente"}}, "orden": [{"property": "Fecha", "direction": "ascending"}], "salida": "pendientes"},
                         {"credencial": "notion-equipo", "operacion": "agregar_bloques", "pagina": "{{pagina.id}}", "contenido": "## Respuesta\n{{respuesta}}\n- Enviada el {{datos.fecha}}"}],
            "consejos": ["Los nombres de las columnas tienen que ser los de la base (se acepta sin distinguir mayúsculas).",
                         "Una columna de un tipo raro (personas, relación, archivos) se puede pasar en el formato de Notion: {\"Relación\": {\"relation\": [{\"id\": \"…\"}]}}.",
                         "En consultar, cada página deja sus columnas como valores simples en «{{pendientes.0.propiedades.Nombre}}»."],
            "preparar": ["Crea una integración interna en https://www.notion.so/my-integrations y copia su token (secret_… o ntn_…).",
                         "En Notion abre la base o la página › ••• › Conexiones › agrega la integración (sin eso, Notion responde 404).",
                         "En Conexiones crea una «Notion» con el token; «Probar conexión» solo lee la integración.", _ESCRITURA, _POSITIVA],
        },
        # ============================================================= HubSpot
        "hubspot_crm": {
            "nombre": "HubSpot · CRM", "categoria": "conector",
            "descripcion": "Trabaja con el CRM de HubSpot (conexión hubspot): contactos, empresas o negocios. Buscar (texto o filtros por propiedad, con "
                           "paginación) y ver (por id, o por correo en contactos) solo leen. Crear, actualizar y nota (una nota asociada al registro) "
                           "ESCRIBEN AFUERA: solo con la escritura habilitada por una persona; si no, quedan retenidos.",
            "config": conf({"credencial": credencial(["hubspot"]), "objeto": opcion(["contactos", "empresas", "negocios"], "", "contactos"),
                            "operacion": op("hubspot_crm", "buscar y ver leen; crear, actualizar y nota escriben"),
                            "texto": plantilla("buscar: texto libre (busca en nombre, correo, dominio…)"),
                            "filtros": lista(objeto({"propiedad": texto("la propiedad interna, p. ej. email, lifecyclestage, amount"),
                                                     "operador": opcion(OPERADORES_HUBSPOT, "", "EQ"), "valor": plantilla("el valor (admite plantillas)")}, ["propiedad"]),
                                             "buscar: condiciones que se cumplen todas"),
                            "propiedades": lista(texto(), "buscar o ver: las propiedades que trae (si no, las principales del objeto)"),
                            "id": plantilla("ver, actualizar o nota: el id del registro (en contactos, ver y actualizar aceptan el correo)"),
                            "datos": libre("crear o actualizar: {propiedad: plantilla o valor}, p. ej. {\"email\": \"{{datos.correo}}\", \"firstname\": \"{{datos.nombre}}\"}"),
                            "nota": plantilla("nota: el texto de la nota (admite HTML simple)", largo=True),
                            "max": entero("buscar: tope de registros", 1, 1000, 50), **comunes}, ["credencial"]),
            "salidas": ["ok", "vacio (al buscar)"],
            "produce": "salida: lista [{id, propiedades…, creado, actualizado}] (buscar), el registro (ver), {id, bloqueado} (crear, actualizar, nota)",
            "x-credencial": True,
            "ejemplo": {"id": "buscar_contacto", "tipo": "hubspot_crm", "nombre": "¿Ya es cliente?", "config": {
                "credencial": "hubspot-ventas", "objeto": "contactos", "operacion": "buscar",
                "filtros": [{"propiedad": "email", "operador": "EQ", "valor": "{{correo.remitente_correo}}"}], "max": 1, "salida": "contacto"}},
            "ejemplos": [{"credencial": "hubspot-ventas", "objeto": "contactos", "operacion": "crear",
                          "datos": {"email": "{{datos.correo}}", "firstname": "{{datos.nombre}}", "lastname": "{{datos.apellido}}", "phone": "{{datos.telefono}}"}, "salida": "nuevo"},
                         {"credencial": "hubspot-ventas", "objeto": "contactos", "operacion": "nota", "id": "{{contacto.0.id}}", "nota": "Pidió cotización: {{resumen}}"}],
            "consejos": ["Patrón «crear o actualizar»: buscar por correo; si sale por «vacio» crea, si no actualiza con «{{contacto.0.id}}».",
                         "Los nombres de propiedad son los internos de HubSpot (Configuración › Propiedades): email, firstname, dealname, amount…",
                         "La nota queda asociada al contacto, empresa o negocio del id (tiene que ser el número, no el correo)."],
            "preparar": ["En HubSpot: Configuración › Integraciones › Aplicaciones privadas › Crear, con los alcances crm.objects.contacts/companies/deals (read y write) y copia el token (pat-…).",
                         "En Conexiones crea una «HubSpot» con ese token; «Probar conexión» solo lee los datos de la cuenta.", _ESCRITURA, _POSITIVA],
        },
        # ========================================================== Salesforce
        "salesforce_registros": {
            "nombre": "Salesforce · registros", "categoria": "conector",
            "descripcion": "Trabaja con Salesforce (conexión salesforce, OAuth de credenciales de cliente): consultar con SOQL de solo lectura (solo SELECT; los "
                           "{{valores}} se escapan dentro de comillas; pagina sola), ver un registro, crear o actualizar uno de cualquier objeto (Account, "
                           "Contact, Lead, Case, Opportunity…). Consultar y ver solo leen. Crear y actualizar ESCRIBEN AFUERA: solo con la escritura "
                           "habilitada por una persona.",
            "config": conf({"credencial": credencial(["salesforce"]), "operacion": op("salesforce_registros", "consultar y ver leen; crear y actualizar escriben"),
                            "soql": plantilla("consultar: SELECT … FROM … (p. ej. SELECT Id, Name FROM Account WHERE Name = '{{datos.empresa}}' LIMIT 5)", largo=True),
                            "objeto": plantilla("ver, crear o actualizar: el objeto (Account, Contact, Case, Lead, Opportunity o uno propio Algo__c)"),
                            "id": plantilla("ver o actualizar: el Id del registro (15 o 18 caracteres)"),
                            "campos": lista(texto(), "ver: qué campos trae (si no, todos)"),
                            "datos": libre("crear o actualizar: {Campo: plantilla o valor}, p. ej. {\"Subject\": \"{{correo.asunto}}\", \"Origin\": \"Email\"}"),
                            "max": entero("consultar: tope de filas", 1, 2000, 200), **comunes}, ["credencial"]),
            "salidas": ["ok", "vacio (al consultar)"],
            "produce": "salida: {filas, total, truncado} (consultar), el registro (ver), {id, bloqueado} (crear), {id, cambiados, bloqueado} (actualizar)",
            "x-credencial": True,
            "ejemplo": {"id": "caso_sf", "tipo": "salesforce_registros", "nombre": "Caso en Salesforce", "config": {
                "credencial": "salesforce-servicio", "operacion": "crear", "objeto": "Case",
                "datos": {"Subject": "{{correo.asunto}}", "Description": "{{correo.cuerpo}}", "Origin": "Email", "SuppliedEmail": "{{correo.remitente_correo}}"},
                "salida": "caso"}},
            "ejemplos": [{"credencial": "salesforce-servicio", "operacion": "consultar",
                          "soql": "SELECT Id, Name, Phone FROM Account WHERE Name LIKE '%{{datos.empresa}}%' ORDER BY CreatedDate DESC LIMIT 10", "salida": "cuentas"},
                         {"credencial": "salesforce-servicio", "operacion": "ver", "objeto": "Contact", "id": "003000000000001AAA", "campos": ["Name", "Email"], "salida": "contacto"}],
            "consejos": ["Las filas quedan en «{{cuentas.filas}}»; los registros relacionados vienen anidados (Account.Name → {{cuentas.filas.0.Account.Name}}).",
                         "Pon el valor de la plantilla entre comillas simples en el SOQL ('{{x}}'): se escapa solo, así un texto no puede cambiar la consulta.",
                         "SOQL no escribe: para crear o actualizar usa operacion crear o actualizar."],
            "preparar": ["En Salesforce Setup › App Manager crea una «External Client App» (o Connected App) con OAuth, el flujo «Client Credentials» habilitado y un usuario de ejecución con los permisos justos.",
                         "En Conexiones crea una «Salesforce» con la instancia (https://tuempresa.my.salesforce.com), el consumer key (client_id) y el secret; «Probar conexión» pide un token y lee los límites.",
                         _ESCRITURA, _POSITIVA],
        },
        # ============================================================ Zoho CRM
        "zoho_crm": {
            "nombre": "Zoho CRM", "categoria": "conector",
            "descripcion": "Trabaja con Zoho CRM (API v6, conexión zoho): buscar en un módulo (Leads, Contacts, Accounts, Deals…) por criterio, correo, "
                           "teléfono o palabra (con paginación), ver un registro, crear o actualizar uno. Buscar y ver solo leen. Crear y actualizar "
                           "ESCRIBEN AFUERA: solo con la escritura habilitada por una persona.",
            "config": conf({"credencial": credencial(["zoho"]), "modulo": plantilla("el módulo: Leads, Contacts, Accounts, Deals, Tasks o uno propio"),
                            "operacion": op("zoho_crm", "buscar y ver leen; crear y actualizar escriben"),
                            "criterio": plantilla("buscar: criterio de Zoho, p. ej. (Email:equals:{{correo.remitente_correo}}) (los valores se escapan)"),
                            "correo": plantilla("buscar: o por correo"), "telefono": plantilla("buscar: o por teléfono"), "texto": plantilla("buscar: o por palabra"),
                            "id": plantilla("ver o actualizar: el id del registro"),
                            "datos": libre("crear o actualizar: {Campo: plantilla o valor}, p. ej. {\"Last_Name\": \"{{datos.apellido}}\", \"Email\": \"{{datos.correo}}\"}"),
                            "max": entero("buscar: tope de registros", 1, 1000, 50), **comunes}, ["credencial", "modulo"]),
            "salidas": ["ok", "vacio (al buscar)"],
            "produce": "salida: lista de registros (buscar), el registro (ver), {id, estado, bloqueado} (crear, actualizar)",
            "x-credencial": True,
            "ejemplo": {"id": "lead_zoho", "tipo": "zoho_crm", "nombre": "Lead en Zoho", "config": {
                "credencial": "zoho-ventas", "modulo": "Leads", "operacion": "crear",
                "datos": {"Last_Name": "{{datos.apellido}}", "First_Name": "{{datos.nombre}}", "Email": "{{datos.correo}}", "Company": "{{datos.empresa}}"}, "salida": "lead"}},
            "ejemplos": [{"credencial": "zoho-ventas", "modulo": "Contacts", "operacion": "buscar", "correo": "{{correo.remitente_correo}}", "max": 1, "salida": "contacto"},
                         {"credencial": "zoho-ventas", "modulo": "Leads", "operacion": "actualizar", "id": "{{lead.id}}", "datos": {"Lead_Status": "Contactado"}}],
            "consejos": ["Los nombres de campo son los de la API (Configuración › API › Nombres de API): Last_Name es obligatorio en Leads y Contacts.",
                         "Si buscar no encuentra nada, Zoho responde vacío y el nodo sale por «vacio»."],
            "preparar": ["En https://api-console.zoho.com crea un «Self Client», genera un código con los alcances ZohoCRM.modules.ALL,ZohoCRM.users.READ y cámbialo por un refresh_token (una sola vez).",
                         "En Conexiones crea una «Zoho CRM» con la región de tu cuenta (com, eu, in…), client_id, client_secret y el refresh_token; «Probar conexión» lee tu usuario.",
                         _ESCRITURA, _POSITIVA],
        },
        # ============================================================ DocuSign
        "docusign_sobre": {
            "nombre": "DocuSign · firma electrónica", "categoria": "integracion",
            "descripcion": "Envía a firmar con DocuSign (eSignature REST v2.1, conexión docusign) un PDF o Word de la ejecución a uno o varios firmantes "
                           "(en orden), con copias opcionales; consulta el estado del sobre y de cada firmante; o descarga el PDF firmado como archivo de "
                           "la ejecución. Estado y descargar solo leen. Enviar ESCRIBE AFUERA (manda correos a los firmantes): solo con la escritura "
                           "habilitada por una persona; si no, el sobre queda armado y retenido. Nunca firmantes de Positiva.",
            "config": conf({"credencial": credencial(["docusign"]), "operacion": op("docusign_sobre", "enviar escribe; estado y descargar leen"),
                            "documento": ARCHIVOS("enviar: el PDF o Word de la ejecución (p. ej. «{{documento}}»)"),
                            "asunto": plantilla("enviar: el asunto del correo de DocuSign"), "mensaje": plantilla("enviar: el mensaje para los firmantes", largo=True),
                            "firmantes": lista(objeto({"nombre": plantilla("nombre"), "correo": plantilla("correo"), "orden": entero("orden de firma", 1, 50)}, ["correo"]),
                                               "enviar: quién firma (en el orden dado, o por «orden»)"),
                            "copias": lista(objeto({"nombre": plantilla("nombre"), "correo": plantilla("correo")}, ["correo"]), "enviar: quién recibe copia al final"),
                            "ancla": texto("enviar: un texto del documento donde va la firma (p. ej. «/firma/»); si no, el firmante pone la firma donde quiera"),
                            "borrador": booleano("enviar: crearlo como borrador (no manda correos; igual escribe en DocuSign)", False),
                            "sobre": plantilla("estado o descargar: el id del sobre (p. ej. «{{firma.sobre}}»)"),
                            "nombre": plantilla("descargar: nombre del PDF (por defecto «firmado-<sobre>»)"), **comunes}, ["credencial"]),
            "salidas": ["ok"],
            "produce": "salida: {sobre, estado, firmantes, bloqueado} (enviar); {sobre, estado, firmantes: [{nombre, correo, estado, firmado}], firmados, "
                       "total_firmantes, completado} (estado); la ficha del PDF firmado (descargar)",
            "x-credencial": True,
            "ejemplo": {"id": "firmar", "tipo": "docusign_sobre", "nombre": "Enviar a firmar", "config": {
                "credencial": "docusign-contratos", "operacion": "enviar", "documento": "{{contrato}}", "asunto": "Contrato para firmar: {{datos.cliente}}",
                "mensaje": "Hola {{datos.nombre}}, por favor revisa y firma el contrato.", "firmantes": [{"nombre": "{{datos.nombre}}", "correo": "{{datos.correo}}"}],
                "ancla": "/firma/", "salida": "firma"}},
            "ejemplos": [{"credencial": "docusign-contratos", "operacion": "estado", "sobre": "{{firma.sobre}}", "salida": "estado_firma"},
                         {"credencial": "docusign-contratos", "operacion": "enviar", "documento": "{{documento}}", "asunto": "Acta",
                          "firmantes": [{"nombre": "Ana", "correo": "ana@cliente.test", "orden": 1}, {"nombre": "Luis", "correo": "luis@cliente.test", "orden": 2}],
                          "copias": [{"nombre": "Archivo", "correo": "archivo@cliente.test"}]}],
            "consejos": ["Genera el documento antes (documento_generar o documento_plantilla) y pon «{{documento}}». Si el documento lleva un texto como «/firma/» "
                         "(en blanco sobre blanco), usa ancla y la firma queda justo ahí.",
                         "Para saber cuándo firmaron: un flujo programado con estado y una condición sobre «{{estado_firma.estado}} == completed»; luego descargar "
                         "(operacion descargar con el mismo sobre) deja el PDF firmado como archivo de la ejecución.",
                         "Firmar es un acto con efectos legales: deja una aprobación humana antes de enviar si el documento lo genera la IA."],
            "preparar": ["En DocuSign (cuenta de desarrollador para probar: https://developers.docusign.com) › Apps and Keys: crea una app, copia el Integration Key, genera un par RSA y guarda la llave privada; anota tu User ID y el API Account ID.",
                         "Da el consentimiento una vez (el mensaje de «Probar conexión» trae el enlace si falta).",
                         "En Conexiones crea una «DocuSign» con entorno (demo o produccion), integration_key, user_id, account_id y la llave privada.",
                         _ESCRITURA, _POSITIVA],
        },
    }
    return T


DISPARADORES: set = set()
ESCRITURA = set(OPERACIONES)  # cada nodo lee o escribe según su operación (como ftp_archivos)
LECTURA: set = set()
TIPOS = set(OPERACIONES)
CON_REINTENTOS = ESCRITURA | LECTURA
CREDENCIALES_DE = {"jira_issues": ("jira",), "trello_tarjetas": ("trello",), "asana_tareas": ("asana",), "notion_paginas": ("notion",),
                   "hubspot_crm": ("hubspot",), "salesforce_registros": ("salesforce",), "zoho_crm": ("zoho",), "docusign_sobre": ("docusign",)}
TIPOS_CREDENCIAL = ["jira", "trello", "asana", "notion", "hubspot", "salesforce", "zoho", "docusign"]
