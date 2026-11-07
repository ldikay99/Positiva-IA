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

## Fase 3: trabajo, clientes y firma electrónica (probado SOLO con dobles locales)

`puente/pruebas_trabajo.py` (17 pruebas): Jira, Trello, Asana, Notion, HubSpot, Salesforce, Zoho CRM y DocuSign contra un servidor local (el de DocuSign verifica la firma RS256 del JWT). Ninguno se ha probado contra una cuenta real. Para probarlos de verdad (con cuentas de prueba, nunca de Positiva):

- **Jira Cloud** (`jira`): un sitio `https://<empresa>.atlassian.net` (el plan gratuito sirve), el correo de la cuenta y un token de API de https://id.atlassian.com/manage-profile/security/api-tokens; un proyecto de prueba (p. ej. SOP) con un tipo «Bug» o «Task».
- **Trello** (`trello`): un Power-Up en https://trello.com/power-ups/admin para la API key y un token generado con la cuenta; un tablero de prueba.
- **Asana** (`asana`): token de acceso personal (Configuración › Aplicaciones › Herramientas para desarrolladores) y el gid del espacio de trabajo («Probar conexión» lo muestra).
- **Notion** (`notion`): una integración interna en https://www.notion.so/my-integrations, y la base o página compartida con ella (••• › Conexiones).
- **HubSpot** (`hubspot`): una cuenta de desarrollador o de prueba y una aplicación privada con los alcances `crm.objects.contacts/companies/deals` (read y write); su token `pat-…`.
- **Salesforce** (`salesforce`): una Developer Edition, una External Client App (o Connected App) con el flujo **Client Credentials** habilitado y un usuario de ejecución; la instancia My Domain (`https://<x>.my.salesforce.com`), consumer key y secret.
- **Zoho CRM** (`zoho`): un «Self Client» en https://api-console.zoho.com, un código con `ZohoCRM.modules.ALL,ZohoCRM.users.READ` cambiado una vez por un refresh_token (POST a `https://accounts.zoho.<región>/oauth/v2/token` con `grant_type=authorization_code`) y la región de la cuenta.
- **DocuSign** (`docusign`): cuenta de desarrollador (https://developers.docusign.com, entorno **demo**), una app en Apps and Keys con su Integration Key y un par RSA (la llave privada va en la conexión), el User ID y el API Account ID; dar el consentimiento una vez abriendo `https://account-d.docusign.com/oauth/auth?response_type=code&scope=signature%20impersonation&client_id=<integration key>&redirect_uri=<una URI registrada en la app>` (si falta, «Probar conexión» lo dice con ese enlace). En **produccion** hace falta pasar la revisión de DocuSign («Go-Live»).

Leer (buscar, ver, listar, consultar, estado, descargar) sale sin permiso; crear, actualizar, comentar, mover, completar, notas y enviar a firmar quedan retenidos hasta que una persona habilite la escritura de la conexión. Lo que falta de esta fase: OAuth con pantalla de consentimiento para estos servicios (hoy token de aplicación privada o Self Client) y disparadores por webhook (piden dirección pública).

## Limitaciones conocidas

- Los disparadores (`gmail_nuevo`, `outlook_nuevo`, `calendar_evento_proximo`) revisan cada N minutos. Gmail push por Pub/Sub y las suscripciones de Graph no están hechos, porque exigen exponer la plataforma a internet.
- `docs_crear_desde_plantilla` sin copia en Drive arma el PDF con el generador propio y no conserva todo el formato del Doc (en Word sí).
- `teams_mensaje` solo funciona con `microsoft_oauth` (Graph no deja publicar en canales con permisos de aplicación).
- `web_leer` no ejecuta JavaScript ni inicia sesión; la automatización de navegador está pendiente.
- SFTP, WhatsApp Business, Dropbox y Box no están (ver `docs/PLAN.md`, 5d).
