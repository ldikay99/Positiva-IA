# Inventario de datos e integraciones

Qué datos guarda la plataforma, dónde y quién los escribe, y con qué sistemas externos se conecta. Es la base del
modelo de datos por organización en PostgreSQL y de la clasificación de datos sensibles
([DATOS_SENSIBLES.md](DATOS_SENSIBLES.md)).

## Cómo guarda hoy

Todo el estado vive en archivos locales; no hay una base de datos de la plataforma (salvo las tablas SQLite de cada
flujo). Las rutas salen de `servidor/src/config.ts`; en Docker son `RUTA_DATOS=/datos/estado`,
`RUTA_ARCHIVOS=/datos/archivos` y `RUTA_BANDEJA=/datos/bandeja`, en los volúmenes `positiva-ia-datos` y
`positiva-ia-archivos`.

## Datos que guarda la plataforma

| Grupo | Ruta (bajo `/datos/estado` salvo que se diga otra) | Qué contiene | Lo escribe |
|---|---|---|---|
| Flujos y versiones | `flujos/`, `flujos/versiones/`, `flujos_codigo/`, `flujos_historial/` | Definición de cada flujo, sus versiones y quién cambió qué (persona o Vera) | `flujos.ts`, `estructuras.ts`, `versiones.ts` |
| Entorno por flujo | `entornos/<flujo>.json` | Variables de cada flujo; las secretas, cifradas | `entornos.ts` |
| Ejecuciones | `corridas/<día>.jsonl` | Registro completo de cada ejecución con todos sus eventos | `motor.ts` |
| Archivos de ejecuciones | `/datos/archivos/<flujo>/{entradas,bandeja}`, `recibidos/` | Archivos y correos que entran a cada flujo | `espacio.ts`, `flujo_declarativo.py` |
| Páginas y texto leído | `trabajo/visor/<corrida>`, `cache/conversion/` | Imagen y texto de cada hoja; caché de OCR y visión por huella del archivo | `visor_adjuntos.py`, `cache_conversion.py` |
| Conversaciones de Vera | `chats/<id>.json`, `chats/adjuntos/<id>/` | Mensajes por chat y por voz (transcripción) y archivos adjuntos | `chats.ts` |
| Conexiones y credenciales | `credenciales.json`, `.clave-credenciales` | Credenciales de conectores y de proveedores de IA, tokens OAuth, cifrados; la llave si no viene de `CLAVE_CREDENCIALES` | `credenciales.ts` |
| Enlaces de descarga | `clave_descargas` | Llave que firma los enlaces de descarga (vencen a las 72 h) | `descargas.ts` |
| Tablas de los flujos | `tablas/flujo-<id>.sqlite`, `compartida` | Datos que los flujos guardan en tablas propias | `conectores/tablas.py` |
| Plantillas y documentos | `documentos/<id>/`, `catalogo/plantillas/` | Plantillas, logos y plantillas de flujos incluidas | `documentos.ts`, `salidas.py` |
| Aprobaciones y aplazadas | `aprobaciones/`, `aplazadas/` | Aprobaciones pendientes con el estado de la ejecución y ejecuciones aplazadas con su entrada | `aprobaciones.ts`, `aplazadas.ts` |
| Buzón real | `vivo/<caso>/` | Correos y adjuntos traídos del buzón (solo lectura) | `buzon_vivo.py`, `buzon_tutelas.py` |
| Configuración | `configuracion.json`, `historial.jsonl`, `perfiles/`, `programaciones.json`, `programador.json`, `recursos.json`, `notificaciones.json`, `ia_global.json`, `ia_vera.json`, `disparadores/`, `limites/` | Ajustes, programaciones, proveedor de IA elegido, estado de disparadores y límites | módulos del mismo nombre en `servidor/src/` |
| Telemetría del motor | `trabajo/{logs,grabador,pasos}`, `motor/logs/` | Registros del motor y escrituras retenidas en modo prueba | `trabajador.py`, `test_recorder.py` |
| Navegador | `localStorage` `positiva-ia:*` | Estado de la interfaz y el chat abierto; sin contenido | `web/src/estado.ts` |

## Integraciones

| Sistema | Conexión | Lee | Escribe (solo con escritura habilitada) | Dónde vive la credencial |
|---|---|---|---|---|
| Google Workspace (Gmail, Calendar, Drive, Docs, Sheets, Forms) | OAuth con PKCE o cuenta de servicio | Correos, eventos, archivos, hojas, respuestas | Enviar, crear eventos, subir y editar archivos | `credenciales.json`, cifrada |
| Microsoft 365 (Outlook, OneDrive, SharePoint, Teams, Excel) | OAuth delegado o app de Graph | Correos, calendario, archivos | Enviar, mover, subir, publicar en Teams | `credenciales.json`, cifrada |
| Correo genérico | IMAP y SMTP | Buzones | Enviar | `credenciales.json`, cifrada |
| Bases de datos | PostgreSQL, MySQL, SQLite, SQL Server, Oracle, MongoDB, BigQuery, Firestore | Consultas | DML | `credenciales.json`, cifrada |
| Almacenamiento | S3, GCS, FTP/FTPS | Archivos | Subir | `credenciales.json`, cifrada |
| Mensajería | Teams, Slack, webhooks, Telegram, SendGrid, Mailgun, Twilio | — | Mensajes | `credenciales.json`, cifrada |
| Trabajo, clientes y firma | Jira, Trello, Asana, Notion, HubSpot, Salesforce, Zoho CRM, DocuSign | Registros | Crear, actualizar, enviar a firmar | `credenciales.json`, cifrada |
| APIs y web | OpenAPI/REST, páginas públicas y RSS | Respuestas, páginas | HTTP distinto de GET | `credenciales.json`, cifrada |
| IA (Vera y nodos) | Vertex AI con la identidad del servicio (ADC); opcional con API key: Gemini, OpenAI, Azure OpenAI, Anthropic, DeepSeek, Mistral, Groq, OpenRouter, Cohere, Ollama | — | Envía el texto, las hojas y los adjuntos que procesa cada nodo o conversación | ADC montado (`RUTA_CREDENCIALES`); API keys cifradas |
| Voz de Vera | Gemini Live en Vertex | — | Audio del micrófono en vivo; se transcriben las dos direcciones | ADC |
| OCR | Tesseract local | — | — | — |
| Entrada por webhook | `POST /api/ganchos/:flujo/:token` | Datos que llegan | — | Token por flujo |

Estado de las pruebas (ver [INTEGRACIONES.md](INTEGRACIONES.md)): Google y Microsoft están probados contra
servidores locales y se prueban con cuentas reales en este sprint; el resto, solo contra dobles locales.

## Identidad y acceso hoy

- No hay inicio de sesión, personas, sesiones, roles ni organizaciones; el servidor escucha en `0.0.0.0` y Vera
  llama a la API interna sin credenciales.
- Lo que sí existe: token por flujo en los webhooks (comparación en tiempo constante), enlaces de descarga firmados
  que vencen y el estado de OAuth en memoria.
- Las cabeceras `X-Confirmado-Por-Persona` y `x-autor: vera` marcan quién hizo un cambio, pero no se autentican.

Esto lo resuelven el modelo por organización, el inicio de sesión y el control por rol del sprint
([ROLES_Y_PERMISOS.md](ROLES_Y_PERMISOS.md)).

## Lo propio de Positiva que sale del producto

- Flujos de código `pqrd` y `tutelas` con sus motores (`motor/`, `motores/tutelas/`), su catálogo y sus trabajadores
  (`puente/trabajador*.py`, `buzon_vivo.py`, `buzon_tutelas.py`).
- Valores fijos del proyecto de GCP de Positiva, URLs de SGDEA y listas de bloqueo con sus dominios y buzones.
- Marca: título de la web, claves `positiva-ia:*`, el User-Agent de los conectores y el productor de los PDF.
- Datos versionados en el repositorio: el directorio de juzgados (`motores/tutelas/juzgados_actualizada.csv`) y el
  directorio de funcionarios (`motor/business_rules/funcionario_directory.py`).
