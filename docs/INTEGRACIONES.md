# Integraciones: qué hay que registrar para usar cuentas reales

Estado al 2026-11-07: los conectores de Google, Microsoft 365 y OpenAPI están hechos y probados contra servidores falsos locales (28/28 en `puente/pruebas_integraciones.py`, 7/7 en `servidor/src/pruebas_oauth.ts`). **Ninguno se ha probado todavía contra una cuenta real.**

## Dirección de retorno (redirect URI)

`<URL_PUBLICA>/api/oauth/callback`

- Hoy, sin `URL_PUBLICA` definida: `http://localhost:8095/api/oauth/callback`.
- La pantalla de Conexiones la muestra para copiarla (`GET /api/oauth/redireccion`).
- Cuando la plataforma tenga dirección pública, se define `URL_PUBLICA` y se registra la nueva dirección en Google y en Entra ID.

## Google Cloud (conexión «Google (OAuth)», tipo `google_oauth`)

1. Pantalla de consentimiento OAuth, con la cuenta de quien prueba como usuario de prueba.
2. Habilitar las APIs: Gmail, Calendar, Drive, Docs, Sheets y Forms.
3. Credenciales → ID de cliente OAuth → «Aplicación web», con la dirección de retorno de arriba.
4. En Positiva-IA: Conexiones → nueva «Google (OAuth)» con `client_id` y `client_secret` → «Conectar con Google».

## Microsoft Entra ID (conexión «Microsoft 365 (OAuth)», tipo `microsoft_oauth`)

1. Registrar una aplicación, plataforma **Web**, con la dirección de retorno de arriba, y crear un secreto de cliente.
2. Permisos **delegados**: `offline_access`, `openid`, `email`, `User.Read`, `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `Files.ReadWrite.All`, `Sites.ReadWrite.All`, `ChannelMessage.Send`, `ChatMessage.Send`. Algunos pueden pedir consentimiento de un administrador del tenant.
3. En Positiva-IA: Conexiones → nueva «Microsoft 365 (OAuth)» con `tenant`, `client_id` y `client_secret` → «Conectar con Microsoft».
4. Revocar: en Microsoft no se revoca un solo token; «Desconectar» lo borra aquí y el permiso se quita en https://myapps.microsoft.com.

## Para que algo salga de verdad

Una persona habilita la **escritura** de esa conexión. Sin eso, todo lo que escribe (enviar, responder, crear eventos, subir archivos, publicar en Teams…) queda retenido y armado en el detalle de la ejecución.

## Bloqueos de seguridad

- Nunca se usa como cuenta, remitente, destinatario o invitado una dirección `@positiva.gov.co` ni los buzones protegidos.
- SharePoint: bloqueados `positiva.sharepoint.com` y `positiva-my.sharepoint.com` (`puente/conectores/microsoft.py`, `SITIOS_VETADOS`). **Por confirmar con Jonathan** que ese es el nombre del tenant de Positiva; si es otro, agregarlo ahí.

## Fase 2: páginas sin API, almacenamiento, datos y mensajería (probado SOLO con dobles locales)

`puente/pruebas_externos.py` (21 pruebas): sitio web, feeds, S3, BigQuery, Firestore, Telegram, SendGrid, Mailgun y Twilio contra un servidor local; FTP contra un servidor FTP local; MongoDB, SQL Server y Oracle con controladores falsos. Ninguno se ha probado contra una cuenta real. Lo que hay que tener para probarlos de verdad:

- **Páginas y RSS** (`web_leer`, `rss_leer`, `rss_nuevo`): nada; solo URLs públicas https. Respetan robots.txt y esperan ≥ 2 s entre pedidos al mismo sitio.
- **FTP/FTPS**: servidor, usuario y clave (conexión «Servidor FTP / FTPS»). SFTP no existe (licencias de SSH).
- **S3 / MinIO / GCS**: access key y secret key limitadas a un bucket; GCS con llave HMAC (Configuración › Interoperabilidad).
- **MongoDB**: host:puerto de cada miembro (no mongodb+srv), usuario de solo lectura para consultas.
- **SQL Server / Oracle**: servidor, base (o el nombre de servicio en Oracle) y usuario de solo lectura.
- **BigQuery / Firestore**: cuenta de servicio con BigQuery Data Viewer + Job User (o Cloud Datastore User), o «Google (OAuth)» con los alcances `bigquery` / `datastore` agregados.
- **Telegram**: token de @BotFather y el bot agregado al chat. **SendGrid**: API key con Mail Send y remitente verificado. **Mailgun**: API key, dominio y región. **Twilio**: Account SID, Auth Token y número remitente.

Todo lo que escribe (subir, insertar, DML, guardar, enviar) queda retenido hasta que una persona habilite la escritura de la conexión.

## Limitaciones conocidas

- Los disparadores (`gmail_nuevo`, `outlook_nuevo`, `calendar_evento_proximo`) revisan cada N minutos. Gmail push por Pub/Sub y las suscripciones de Graph no están hechos, porque exigen exponer la plataforma a internet.
- `docs_crear_desde_plantilla` sin copia en Drive arma el PDF con el generador propio y no conserva todo el formato del Doc (en Word sí).
- `teams_mensaje` solo funciona con `microsoft_oauth` (Graph no deja publicar en canales con permisos de aplicación).
- `web_leer` no ejecuta JavaScript ni inicia sesión; la automatización de navegador está pendiente.
- SFTP, WhatsApp Business, Dropbox y Box no están (ver `docs/PLAN.md`, 5d).
