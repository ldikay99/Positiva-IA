"""
La ficha de los nodos de la fase 2 de integraciones (plan 5d): páginas sin API
(web_leer, rss_leer y el disparador rss_nuevo), almacenamiento (FTP/FTPS y
S3 compatible: S3, MinIO, Google Cloud Storage), datos (MongoDB, BigQuery,
Firestore; SQL Server y Oracle van por sql_consulta y sql_ejecutar) y mensajería
transaccional (Telegram, SendGrid/Mailgun, Twilio SMS).

Igual que `especificacion_integraciones.py`: `tipos(h)` recibe `nodos_declarativos`
y devuelve {tipo: ficha} con config (JSON Schema), salidas, lo que deja, un
ejemplo, `ejemplos`, `consejos` y `preparar`. Lo ejecuta `nodos_externos.py`;
el zod del servidor está en `servidor/src/flujos_externos.ts`.
"""
from __future__ import annotations

from typing import Any, Dict

GOOGLE_DATOS = ["google_servicio", "google_oauth"]

_PREPARAR_ESCRITURA = "Para que salga de verdad una persona tiene que habilitar la escritura de la conexión; si no, queda retenido con todo armado en el detalle de la ejecución."
_PREPARAR_RED = ("Si el servidor está en una red privada (10.x, 172.16–31.x, 192.168.x), la conexión tiene que crearla una persona desde la web y marcar «Red privada»; "
                 "nunca se aceptan loopback, metadatos ni hosts de Positiva.")


def tipos(h: Any) -> Dict[str, Dict[str, Any]]:
    conf, credencial, plantilla, texto, entero = h.conf, h.credencial, h.plantilla, h.texto, h.entero
    booleano, opcion, lista, objeto, mapa, libre = h.booleano, h.opcion, h.lista, h.objeto, h.mapa, h.libre
    SALIDA, TIMEOUT, ARCHIVOS, REINT = h.SALIDA, h.TIMEOUT, h.ARCHIVOS, h._REINTENTOS
    subida = {"nombre": plantilla("subir: nombre (por defecto el del archivo)"), "contenido": plantilla("subir: o un texto", largo=True),
              "de": plantilla("subir: o una variable (texto, JSON o filas para CSV)"), "formato": opcion(["texto", "json", "csv"], "subir con contenido/de: cómo se guarda", "texto"),
              "como": opcion(["texto", "archivo"], "leer: el texto (con OCR si es escaneo) o el archivo tal cual como archivo de la ejecución", "texto"),
              "conflicto": opcion(["renombrar", "reemplazar", "fallar"], "subir: si ya existe", "renombrar"), "ocr": booleano("leer: OCR en escaneos", True),
              "max_caracteres": entero("leer como texto: tope de caracteres", 1, 500000, 60000)}
    proyecto = plantilla("id del proyecto de Google Cloud (p. ej. «mi-proyecto-123»)")

    def objeto_libre(desc: str) -> Dict[str, Any]:
        """Un objeto JSON libre (claves con $ o puntos, valores anidados): lo revisa nodos_externos.validar."""
        return libre(desc)

    T: Dict[str, Dict[str, Any]] = {
        # ====================================================== páginas sin API
        "web_leer": {
            "nombre": "Página web · leer", "categoria": "conector",
            "descripcion": "Lee una página web pública SIN API (solo GET por https, solo lectura) y deja su texto limpio, sus enlaces, sus tablas o lo que "
                           "marque un selector CSS. Respeta robots.txt, espera entre pedidos al mismo sitio (frecuencia), corta en un tamaño máximo y nunca "
                           "va a Positiva, SGDEA, metadatos ni IP internas. No ejecuta JavaScript: lo que la página arma con JS no aparece.",
            "config": conf({"url": plantilla("la dirección https:// de la página"), "extraer": opcion(["texto", "enlaces", "tablas", "selector"],
                            "texto: el contenido legible (sin menú ni pie); enlaces: los vínculos; tablas: cada <table> como filas; selector: lo que marque el CSS", "texto"),
                            "selector": texto("selector CSS (con extraer selector), p. ej. «article h2 a», «.precio», «#resultados tr»"),
                            "atributo": texto("selector: además, el valor de este atributo de cada elemento (href, src, data-id…)"),
                            "completo": booleano("texto: incluir menú, encabezado y pie", False),
                            "solo_mismo_sitio": booleano("enlaces: solo los del mismo sitio", False),
                            "max_caracteres": entero("texto: tope de caracteres", 100, 200000, 20000),
                            "max_elementos": entero("enlaces, tablas o selector: tope de elementos", 1, 500, 100),
                            "max_kb": entero("tamaño máximo de la página en KB", 10, 5000, 2000),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["url"]),
            "salidas": ["ok", "vacio"], "produce": "salida {url, titulo, descripcion, texto | enlaces | tablas | elementos, total, bytes}",
            "ejemplo": {"id": "leer_pagina", "tipo": "web_leer", "nombre": "Leer la noticia", "config": {
                "url": "https://www.ejemplo.com/noticias/1", "extraer": "texto", "salida": "pagina"}},
            "ejemplos": [{"url": "https://www.ejemplo.com/tarifas", "extraer": "tablas", "salida": "tarifas"},
                         {"url": "{{entrada.url}}", "extraer": "selector", "selector": "article h2 a", "atributo": "href", "max_elementos": 30, "salida": "titulares"}],
            "consejos": ["Para resumir una página: web_leer (texto) y luego resumir o ia_generar con «{{pagina.texto}}».",
                         "Respeta robots.txt (si no deja leer esa ruta, falla con ese motivo) y espera al menos 2 s entre pedidos al mismo sitio (o su Crawl-delay).",
                         "Las páginas que se arman con JavaScript no traen el contenido: busca su RSS, su API (nodo http o api_operacion) o pide la automatización de navegador (pendiente)."],
            "preparar": ["No necesita conexión: solo la URL pública (https). Las páginas con inicio de sesión no se leen."],
        },
        "rss_leer": {
            "nombre": "Feed RSS/Atom · leer", "categoria": "conector",
            "descripcion": "Lee un feed RSS 2.0, RSS 1.0 o Atom (o la página que lo anuncia) y deja sus elementos: título, enlace, fecha, autor, resumen y "
                           "categorías. Solo lectura, con robots.txt, frecuencia y tamaño como web_leer.",
            "config": conf({"url": plantilla("la URL del feed (o de una página que lo anuncie con <link rel=\"alternate\">)"),
                            "max": entero("cuántos elementos (los primeros del feed)", 1, 200, 20),
                            "desde": plantilla("solo los publicados desde esta fecha (ISO, «hoy», «ayer»)"),
                            "contiene": plantilla("solo los que tengan este texto en el título o el resumen (sin mayúsculas ni tildes)"),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["url"]),
            "salidas": ["ok", "vacio"], "produce": "salida {titulo, enlace, tipo, elementos: [{id, titulo, enlace, fecha, autor, resumen, categorias}], total}",
            "ejemplo": {"id": "noticias", "tipo": "rss_leer", "nombre": "Noticias del sector", "config": {
                "url": "https://www.ejemplo.com/feed.xml", "max": 10, "salida": "noticias"}},
            "ejemplos": [{"url": "https://www.ejemplo.com/blog", "desde": "ayer", "contiene": "riesgos laborales", "salida": "novedades"}],
            "consejos": ["Para «avisar de lo nuevo» sin repetir usa el disparador rss_nuevo, no este nodo en un programado.",
                         "Recorre «{{noticias.elementos}}» con para_cada o pásalos enteros a resumir."],
            "preparar": ["No necesita conexión: solo la URL pública del feed (https)."],
        },
        "rss_nuevo": {
            "nombre": "Feed RSS · elemento nuevo", "categoria": "entrada",
            "descripcion": "Revisa un feed RSS/Atom cada N minutos (mínimo 5) y corre el flujo una vez por cada elemento nuevo, sin repetir (recuerda los ya "
                           "vistos y usa ETag / Last-Modified para no bajar el feed si no cambió). Deja el elemento en {{entrada.elemento}} (titulo, enlace, "
                           "fecha, autor, resumen) y el feed en {{entrada.feed}}. Solo lectura, con robots.txt. Nace apagado.",
            "config": conf({"url": texto("la URL del feed (https)"), "cada_minutos": entero("cada cuánto revisar", 5, 1440, 30),
                            "activo": booleano("encendido", False), "max_por_sondeo": entero("tope de elementos nuevos por revisión", 1, 50, 10),
                            "procesar_existentes": booleano("en la primera revisión, correr también los que ya estaban", False),
                            "contiene": texto("solo los que tengan este texto en el título o el resumen")}, ["url"]),
            "salidas": ["ok"], "produce": "entrada {elemento: {id, titulo, enlace, fecha, autor, resumen, categorias}, feed: {titulo, enlace}}",
            "ejemplo": {"id": "llega_noticia", "tipo": "rss_nuevo", "nombre": "Noticia nueva", "config": {
                "url": "https://www.ejemplo.com/feed.xml", "cada_minutos": 60}},
            "ejemplos": [{"url": "https://www.ejemplo.com/feed.xml", "cada_minutos": 30, "contiene": "circular", "procesar_existentes": True}],
            "consejos": ["La primera revisión solo memoriza lo que ya hay (salvo procesar_existentes): así no llega una avalancha al encenderlo.",
                         "Para resumir lo nuevo: web_leer con «{{entrada.elemento.enlace}}» y luego resumir, y avisa por Teams, correo o Telegram.",
                         "Recuerda hasta 2.000 elementos vistos por flujo; si el feed cambia el id de un elemento, lo verá como nuevo."],
            "preparar": ["No necesita conexión. Enciéndelo con activo: true cuando el flujo esté probado."],
        },
        # ======================================================= almacenamiento
        "ftp_archivos": {
            "nombre": "FTP · archivos", "categoria": "conector",
            "descripcion": "Lista, lee o sube archivos en un servidor FTP o FTPS (conexión ftp, por defecto con TLS explícito). Leer deja el texto o el archivo "
                           "como archivo de la ejecución. Subir ESCRIBE AFUERA: solo con la escritura habilitada por una persona; si no, queda retenido. "
                           "Las rutas no salen de la carpeta base de la conexión.",
            "config": conf({"credencial": credencial(["ftp"]), "operacion": opcion(["listar", "leer", "subir"], "", "listar"),
                            "carpeta": plantilla("carpeta (relativa a la base de la conexión), p. ej. «/entrantes»"),
                            "archivo": libre("leer: la ruta del archivo («/entrantes/lista.csv»); subir: el archivo de la ejecución («{{documento}}»)"),
                            **subida, "max": entero("listar: cuántos", 1, 1000, 100), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
            "salidas": ["ok", "vacio (al listar)"], "produce": "salida: lista {nombre, ruta, tipo, tamano, modificado} (listar), {texto} o ficha (leer), {ruta, bloqueado} (subir)",
            "x-credencial": True,
            "ejemplo": {"id": "entrantes", "tipo": "ftp_archivos", "nombre": "Archivos entrantes", "config": {
                "credencial": "ftp-proveedor", "operacion": "listar", "carpeta": "/entrantes", "salida": "archivos"}},
            "ejemplos": [{"credencial": "ftp-proveedor", "operacion": "leer", "archivo": "/entrantes/novedades.csv", "como": "texto", "salida": "novedades"},
                         {"credencial": "ftp-proveedor", "operacion": "subir", "carpeta": "/respuestas", "archivo": "{{documento}}"}],
            "consejos": ["Solo modo pasivo y la conexión de datos va a la misma IP del control (no a la que diga el servidor).",
                         "SFTP (por SSH) no está: las librerías de SSH para Python son LGPL o GPL. Si el proveedor solo da SFTP, pide FTPS o un bucket S3."],
            "preparar": ["En Conexiones crea una «Servidor FTP / FTPS» con servidor, puerto (21), usuario, clave, cifrado (explícito por defecto; «ninguno» solo si el "
                         "servidor no tiene TLS) y, si quieres, la carpeta base a la que se limita.", _PREPARAR_RED, _PREPARAR_ESCRITURA],
        },
        "s3_archivos": {
            "nombre": "S3 · archivos (S3, MinIO, GCS)", "categoria": "conector",
            "descripcion": "Lista, lee o sube objetos en un bucket compatible con S3: Amazon S3, MinIO o Google Cloud Storage (por interoperabilidad, con llaves "
                           "HMAC). Leer deja el texto o el archivo como archivo de la ejecución. Subir ESCRIBE AFUERA: solo con la escritura habilitada por una "
                           "persona. Las claves no salen del prefijo de la conexión.",
            "config": conf({"credencial": credencial(["s3"]), "operacion": opcion(["listar", "leer", "subir"], "", "listar"),
                            "carpeta": plantilla("prefijo (carpeta) dentro del bucket, p. ej. «facturas/2026»"),
                            "archivo": libre("leer: la clave del objeto («facturas/2026/f-1.pdf»); subir: el archivo de la ejecución («{{documento}}»)"),
                            **subida, "recursivo": booleano("listar: también lo de las subcarpetas", False), "max": entero("listar: cuántos", 1, 1000, 100),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial"]),
            "salidas": ["ok", "vacio (al listar)"], "produce": "salida: lista {nombre, clave, tipo, tamano, modificado} (listar), {texto} o ficha (leer), {clave, bloqueado} (subir)",
            "x-credencial": True,
            "ejemplo": {"id": "facturas", "tipo": "s3_archivos", "nombre": "Facturas del mes", "config": {
                "credencial": "bucket-facturas", "operacion": "listar", "carpeta": "facturas/2026-11", "salida": "facturas"}},
            "ejemplos": [{"credencial": "bucket-facturas", "operacion": "leer", "archivo": "{{factura_i.clave}}", "como": "archivo", "salida": "factura"},
                         {"credencial": "bucket-facturas", "operacion": "subir", "carpeta": "respuestas", "archivo": "{{documento}}", "conflicto": "reemplazar"}],
            "consejos": ["MinIO y otros sin subdominios por bucket: pon estilo «ruta» en la conexión.",
                         "Google Cloud Storage: endpoint https://storage.googleapis.com, región «auto» y una llave HMAC (Configuración › Interoperabilidad).",
                         "La firma es AWS Signature V4 hecha aquí (sin boto3): así la conexión va a la IP ya revisada, con el mismo bloqueo que los demás conectores."],
            "preparar": ["En Conexiones crea una «Almacenamiento S3 (S3, MinIO, GCS)» con access_key, secret_key, bucket, región y, si no es Amazon, el endpoint "
                         "(https://…); usa una llave limitada a ese bucket.", _PREPARAR_RED, _PREPARAR_ESCRITURA],
        },
        # ================================================================ datos
        "mongo_consulta": {
            "nombre": "MongoDB · consultar", "categoria": "conector",
            "descripcion": "Consulta una colección de MongoDB (conexión mongodb), solo lectura: buscar (filtro, proyección, orden), contar, distintos (los valores "
                           "de un campo) o agregar (pipeline sin $out ni $merge). Nunca $where, $function ni JavaScript. Los textos del filtro admiten plantillas.",
            "config": conf({"credencial": credencial(["mongodb"]), "coleccion": texto("la colección"),
                            "operacion": opcion(["buscar", "contar", "distintos", "agregar"], "", "buscar"),
                            "filtro": objeto_libre("filtro de MongoDB, p. ej. {\"estado\": \"pendiente\", \"monto\": {\"$gt\": 100}} (valores con {{plantillas}})"),
                            "proyeccion": objeto_libre("buscar: qué campos traer, p. ej. {\"nombre\": 1, \"_id\": 0}"),
                            "orden": objeto_libre("buscar: {campo: 1 (ascendente) o -1 (descendente)}"),
                            "pipeline": lista(libre(), "agregar: las etapas ($match, $group, $sort, $project…)"),
                            "campo": texto("distintos: el campo"), "max": entero("tope de documentos", 1, 5000, 100),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "coleccion"]),
            "salidas": ["ok", "vacio"], "produce": "salida: lista de documentos (buscar, agregar), {total} (contar) o lista de valores (distintos)", "x-credencial": True,
            "ejemplo": {"id": "pendientes_mongo", "tipo": "mongo_consulta", "nombre": "Solicitudes pendientes", "config": {
                "credencial": "mongo-operaciones", "coleccion": "solicitudes", "filtro": {"estado": "pendiente"}, "orden": {"creada": -1}, "max": 50, "salida": "solicitudes"}},
            "ejemplos": [{"credencial": "mongo-operaciones", "coleccion": "solicitudes", "operacion": "contar", "filtro": {"documento": "{{entrada.documento}}"}, "salida": "cuantas"},
                         {"credencial": "mongo-operaciones", "coleccion": "pagos", "operacion": "agregar",
                          "pipeline": [{"$group": {"_id": "$mes", "total": {"$sum": "$monto"}}}, {"$sort": {"_id": 1}}], "salida": "por_mes"}],
            "consejos": ["Un «_id» de 24 hexadecimales en el filtro se convierte solo a ObjectId.",
                         "Crea un usuario de solo lectura para las consultas; el nodo además rechaza lo que escribe o ejecuta código."],
            "preparar": ["En Conexiones crea una «MongoDB» con servidor (o varios host:puerto separados por coma), puerto, base, usuario, clave, base de "
                         "autenticación (admin) y TLS. mongodb+srv:// no se admite: usa la lista de hosts que da el proveedor (Atlas la muestra en «Standard connection string»)."],
        },
        "mongo_escribir": {
            "nombre": "MongoDB · escribir", "categoria": "conector",
            "descripcion": "Inserta, actualiza o borra documentos en MongoDB. ESCRIBE AFUERA: solo con la escritura habilitada por una persona; si no, queda retenido "
                           "con todo armado. Actualizar y borrar exigen filtro (salvo permitir_sin_filtro) y por defecto tocan uno solo (varios: true para todos).",
            "config": conf({"credencial": credencial(["mongodb"]), "coleccion": texto("la colección"),
                            "operacion": opcion(["insertar", "actualizar", "borrar"], "", "insertar"),
                            "documento": objeto_libre("insertar: un documento {campo: plantilla o valor}"),
                            "documentos": plantilla("insertar: o una variable con la lista de documentos, p. ej. «filas»"),
                            "filtro": objeto_libre("actualizar o borrar: a cuáles"), "cambios": objeto_libre("actualizar: {\"$set\": {...}, \"$inc\": {...}}"),
                            "varios": booleano("actualizar o borrar: todos los que cumplan (si no, uno)", False),
                            "permitir_sin_filtro": booleano("actualizar o borrar sin filtro (toda la colección)", False),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "coleccion"]),
            "salidas": ["ok"], "produce": "salida {insertados, ids | coincidentes, modificados | borrados, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "registrar", "tipo": "mongo_escribir", "nombre": "Registrar la solicitud", "config": {
                "credencial": "mongo-operaciones", "coleccion": "solicitudes", "documento": {"documento": "{{datos.documento}}", "estado": "recibida"}}},
            "ejemplos": [{"credencial": "mongo-operaciones", "coleccion": "solicitudes", "operacion": "actualizar", "filtro": {"_id": "{{solicitud_i._id}}"},
                          "cambios": {"$set": {"estado": "atendida"}}}],
            "consejos": ["Los cambios de actualizar van con operadores ($set, $inc, $push…): un documento sin $ lo reemplazaría y no se acepta."],
            "preparar": ["La misma conexión «MongoDB» de mongo_consulta, con un usuario que pueda escribir.", _PREPARAR_ESCRITURA],
        },
        "bigquery_consulta": {
            "nombre": "BigQuery · consultar", "categoria": "conector",
            "descripcion": "Corre una consulta SELECT en BigQuery (SQL estándar, parámetros @nombre) con una cuenta de servicio o una conexión Google (OAuth). "
                           "Primero la prueba en seco: si no es un SELECT, no corre. Deja las filas y cuántos bytes procesó.",
            "config": conf({"credencial": credencial(GOOGLE_DATOS), "proyecto": proyecto,
                            "sql": h.largo("SELECT … FROM `proyecto.dataset.tabla` WHERE documento = @documento"),
                            "parametros": mapa(plantilla(), "{nombre: plantilla} para cada @nombre"), "ubicacion": texto("ubicación de los datos (US, EU, southamerica-east1…)"),
                            "max": entero("tope de filas", 1, 5000, 500), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()},
                           ["credencial", "proyecto", "sql"]),
            "salidas": ["ok", "vacio"], "produce": "salida {columnas, filas, total, truncado, bytes_procesados}", "x-credencial": True,
            "ejemplo": {"id": "ventas_bq", "tipo": "bigquery_consulta", "nombre": "Ventas del mes", "config": {
                "credencial": "gcp-analitica", "proyecto": "analitica-123", "sql": "SELECT region, SUM(monto) AS total FROM `analitica-123.ventas.diarias` "
                                                                                  "WHERE fecha >= @desde GROUP BY region", "parametros": {"desde": "2026-11-01"}, "salida": "ventas"}},
            "ejemplos": [{"credencial": "gcp-analitica", "proyecto": "analitica-123", "sql": "SELECT * FROM `analitica-123.crm.clientes` WHERE documento = @doc",
                          "parametros": {"doc": "{{datos.documento}}"}, "max": 5, "salida": "cliente"}],
            "consejos": ["Las filas quedan en «{{ventas.filas}}»; bytes_procesados dice cuánto cobró Google por la consulta.",
                         "Los valores van como parámetros @nombre: nunca los pegues en el texto."],
            "preparar": ["Usa una «Google (cuenta de servicio)» con el rol BigQuery Data Viewer y BigQuery Job User en el proyecto, o una «Google (OAuth)» "
                         "agregando «bigquery» a sus alcances y volviendo a conectar."],
        },
        "bigquery_ejecutar": {
            "nombre": "BigQuery · ejecutar", "categoria": "conector",
            "descripcion": "Corre UNA sentencia INSERT, UPDATE, DELETE o MERGE en BigQuery (nunca DDL ni scripts: se revisa en seco). ESCRIBE AFUERA: solo con la "
                           "escritura habilitada por una persona; si no, queda retenida.",
            "config": conf({"credencial": credencial(GOOGLE_DATOS), "proyecto": proyecto,
                            "sql": h.largo("UPDATE `proyecto.dataset.casos` SET estado = @estado WHERE id = @id"),
                            "parametros": mapa(plantilla(), "{nombre: plantilla}"), "ubicacion": texto("ubicación de los datos"),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "proyecto", "sql"]),
            "salidas": ["ok"], "produce": "salida {afectadas, tipo, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "marcar_bq", "tipo": "bigquery_ejecutar", "nombre": "Marcar atendido", "config": {
                "credencial": "gcp-analitica", "proyecto": "analitica-123", "sql": "UPDATE `analitica-123.crm.casos` SET estado = 'atendido' WHERE id = @id",
                "parametros": {"id": "{{caso.id}}"}}},
            "ejemplos": [{"credencial": "gcp-analitica", "proyecto": "analitica-123", "sql": "INSERT INTO `analitica-123.crm.eventos` (caso, evento) VALUES (@caso, 'respuesta')",
                          "parametros": {"caso": "{{entrada.caso}}"}}],
            "consejos": ["BigQuery exige WHERE en UPDATE y DELETE: así no se cambia toda la tabla por error."],
            "preparar": ["La conexión de bigquery_consulta, con el rol BigQuery Data Editor en el dataset.", _PREPARAR_ESCRITURA],
        },
        "firestore_documentos": {
            "nombre": "Firestore · documentos", "categoria": "conector",
            "descripcion": "Lee o guarda documentos de Cloud Firestore: obtener (uno por id), listar (una colección), consultar (filtros campo/operador/valor y "
                           "orden) o guardar (crear o actualizar). Guardar ESCRIBE AFUERA: solo con la escritura habilitada por una persona.",
            "config": conf({"credencial": credencial(GOOGLE_DATOS), "proyecto": proyecto, "base": texto("la base de Firestore", "(default)"),
                            "operacion": opcion(["obtener", "listar", "consultar", "guardar"], "", "listar"),
                            "coleccion": plantilla("la colección («clientes» o «clientes/ana/pedidos»)"),
                            "documento": plantilla("obtener o guardar: el id del documento (guardar sin id crea uno nuevo)"),
                            "filtros": lista(objeto({"campo": texto("el campo"), "operador": opcion(["==", "!=", "<", "<=", ">", ">=", "array-contains", "in", "not-in",
                                                                                                     "array-contains-any"], "", "=="),
                                                     "valor": libre("el valor (texto con plantilla, número, booleano o lista)")}, ["campo"]), "consultar: las condiciones (se cumplen todas)"),
                            "orden": lista(objeto({"campo": texto("el campo"), "descendente": booleano("", False)}, ["campo"]), "consultar: el orden"),
                            "datos": objeto_libre("guardar: {campo: plantilla o valor}"), "combinar": booleano("guardar: solo cambia esos campos (si no, reemplaza el documento)", True),
                            "max": entero("listar o consultar: cuántos", 1, 1000, 100), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()},
                           ["credencial", "proyecto", "coleccion"]),
            "salidas": ["ok", "vacio"], "produce": "salida: documento (obtener), lista de documentos {id, ruta, campos…} o {id, ruta, bloqueado} (guardar)", "x-credencial": True,
            "ejemplo": {"id": "clientes_fs", "tipo": "firestore_documentos", "nombre": "Clientes activos", "config": {
                "credencial": "gcp-app", "proyecto": "app-movil-123", "operacion": "consultar", "coleccion": "clientes",
                "filtros": [{"campo": "estado", "operador": "==", "valor": "activo"}], "orden": [{"campo": "creado", "descendente": True}], "max": 20, "salida": "clientes"}},
            "ejemplos": [{"credencial": "gcp-app", "proyecto": "app-movil-123", "operacion": "obtener", "coleccion": "clientes", "documento": "{{entrada.id}}", "salida": "cliente"},
                         {"credencial": "gcp-app", "proyecto": "app-movil-123", "operacion": "guardar", "coleccion": "solicitudes",
                          "datos": {"documento": "{{datos.documento}}", "estado": "recibida"}}],
            "consejos": ["Varias condiciones o el orden por otro campo pueden pedir un índice compuesto: Firestore responde con el enlace para crearlo."],
            "preparar": ["Usa una «Google (cuenta de servicio)» con el rol Cloud Datastore User (o Viewer para solo leer), o una «Google (OAuth)» agregando «datastore» a sus alcances.",
                         _PREPARAR_ESCRITURA],
        },
        # =================================================== mensajería transaccional
        "telegram_mensaje": {
            "nombre": "Telegram · mensaje", "categoria": "integracion",
            "descripcion": "Envía un mensaje (y archivos de la ejecución como documentos) a un chat, grupo o canal de Telegram con un bot (conexión telegram_bot). "
                           "Los textos largos se parten en varios mensajes. ESCRIBE AFUERA: solo con la escritura habilitada por una persona; si no, queda retenido.",
            "config": conf({"credencial": credencial(["telegram_bot"]), "chat": plantilla("id del chat o @canal (si no, el de la conexión)"),
                            "texto": plantilla("el mensaje", largo=True), "formato": opcion(["texto", "html", "markdown"], "html admite <b>, <i>, <a href>…", "texto"),
                            "archivos": ARCHIVOS("archivos de la ejecución que se mandan como documentos"), "silencioso": booleano("sin sonido de notificación", False),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "texto"]),
            "salidas": ["ok"], "produce": "salida {enviado, ids, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "avisar_telegram", "tipo": "telegram_mensaje", "nombre": "Avisar por Telegram", "config": {
                "credencial": "bot-avisos", "texto": "Nuevo caso: {{correo.asunto}}"}},
            "ejemplos": [{"credencial": "bot-avisos", "chat": "@canal_avisos", "texto": "<b>Informe listo</b>\n{{resumen}}", "formato": "html", "archivos": ["{{documento}}"]}],
            "consejos": ["El bot solo puede escribir en chats donde lo agregaron (y en canales, como administrador).",
                         "Para saber el id de un grupo, agrega el bot, escribe algo y mira getUpdates; o usa el @nombre de un canal público."],
            "preparar": ["Crea el bot con @BotFather en Telegram, copia su token y en Conexiones crea una «Telegram (bot)» con el token y, si quieres, el chat por defecto.",
                         _PREPARAR_ESCRITURA],
        },
        "correo_transaccional": {
            "nombre": "Correo transaccional (SendGrid / Mailgun)", "categoria": "integracion",
            "descripcion": "Envía un correo por SendGrid o Mailgun (por su API HTTP, sin SDK), con HTML y adjuntos de la ejecución. ESCRIBE AFUERA: solo con la "
                           "escritura habilitada por una persona; si no, se arma completo con sus adjuntos y queda retenido. Nunca desde ni hacia direcciones de Positiva.",
            "config": conf({"credencial": credencial(["sendgrid", "mailgun"]), "para": plantilla("destinatarios separados por coma"), "cc": plantilla(), "cco": plantilla(),
                            "asunto": plantilla(), "cuerpo": plantilla("texto del correo", largo=True), "html": plantilla("versión HTML opcional", largo=True),
                            "remitente": plantilla("dirección From (si no, la de la conexión; tiene que estar verificada en el servicio)"),
                            "nombre_remitente": plantilla("nombre que se muestra"), "responder_a": plantilla("dirección Reply-To"),
                            "adjuntos": ARCHIVOS(), "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "para", "asunto", "cuerpo"]),
            "salidas": ["ok"], "produce": "salida {enviado, id, para, asunto, adjuntos, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "confirmar", "tipo": "correo_transaccional", "nombre": "Confirmar la solicitud", "config": {
                "credencial": "sendgrid-avisos", "para": "{{datos.correo}}", "asunto": "Recibimos su solicitud", "cuerpo": "Hola {{datos.nombre}}, ya la estamos revisando."}},
            "ejemplos": [{"credencial": "mailgun-avisos", "para": "{{datos.correo}}", "asunto": "Su certificado", "cuerpo": "Adjunto el certificado.",
                          "html": "<p>Adjunto el <b>certificado</b>.</p>", "adjuntos": ["{{documento}}"], "nombre_remitente": "Mesa de ayuda"}],
            "consejos": ["El remitente tiene que estar verificado (Single Sender o dominio autenticado en SendGrid; el dominio en Mailgun).",
                         "Para responder dentro del hilo de un correo que llegó usa responder_correo, gmail_responder u outlook_responder."],
            "preparar": ["SendGrid: crea una API key con permiso Mail Send y en Conexiones una «SendGrid» con la llave y el remitente verificado.",
                         "Mailgun: en Conexiones una «Mailgun» con la API key, el dominio (mg.tuempresa.com), la región (us o eu) y el remitente.",
                         _PREPARAR_ESCRITURA],
        },
        "sms_twilio": {
            "nombre": "SMS (Twilio)", "categoria": "integracion",
            "descripcion": "Envía un SMS con Twilio (por su API HTTP, sin SDK) a un número en formato internacional (+57…). ESCRIBE AFUERA: solo con la escritura "
                           "habilitada por una persona; si no, queda retenido. Los textos de más de 160 caracteres van en varios segmentos (y se cobran así).",
            "config": conf({"credencial": credencial(["twilio"]), "para": plantilla("número destino con el indicativo, p. ej. +573001234567"),
                            "texto": plantilla("el mensaje (hasta 1.600 caracteres)", largo=True), "remitente": plantilla("número o MessagingServiceSid (si no, el de la conexión)"),
                            "reintentos": REINT, "timeout_s": TIMEOUT, "salida": SALIDA()}, ["credencial", "para", "texto"]),
            "salidas": ["ok"], "produce": "salida {enviado, id, estado, segmentos, bloqueado}", "x-credencial": True,
            "ejemplo": {"id": "sms", "tipo": "sms_twilio", "nombre": "Avisar por SMS", "config": {
                "credencial": "twilio-avisos", "para": "{{datos.celular}}", "texto": "Su solicitud {{radicado}} fue recibida."}},
            "ejemplos": [{"credencial": "twilio-avisos", "para": "+573001234567", "texto": "Recordatorio: cita mañana a las {{hora}}.", "remitente": "MG0123456789abcdef0123456789abcdef"}],
            "consejos": ["Pon el número con +57 (o el indicativo del país): sin él, el nodo no lo acepta.",
                         "En cuentas de prueba de Twilio solo llega a números verificados."],
            "preparar": ["En Conexiones crea una «Twilio (SMS)» con el Account SID (AC…), el Auth Token y el número remitente (o un Messaging Service, MG…).",
                         _PREPARAR_ESCRITURA],
        },
    }
    return T


DISPARADORES = {"rss_nuevo"}
ESCRITURA = {"ftp_archivos", "s3_archivos", "mongo_escribir", "bigquery_ejecutar", "firestore_documentos", "telegram_mensaje", "correo_transaccional", "sms_twilio"}
LECTURA = {"web_leer", "rss_leer", "mongo_consulta", "bigquery_consulta"}
TIPOS = DISPARADORES | ESCRITURA | LECTURA
CON_REINTENTOS = ESCRITURA | LECTURA
CREDENCIALES_DE = {
    "ftp_archivos": ("ftp",), "s3_archivos": ("s3",), "mongo_consulta": ("mongodb",), "mongo_escribir": ("mongodb",),
    "bigquery_consulta": tuple(GOOGLE_DATOS), "bigquery_ejecutar": tuple(GOOGLE_DATOS), "firestore_documentos": tuple(GOOGLE_DATOS),
    "telegram_mensaje": ("telegram_bot",), "correo_transaccional": ("sendgrid", "mailgun"), "sms_twilio": ("twilio",),
}
