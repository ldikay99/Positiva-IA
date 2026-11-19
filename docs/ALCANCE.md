# Alcance de Vera como producto y criterios de aceptación

Vera es una plataforma de flujos de IA armados con nodos, con una asistente que trabaja por chat y por voz. Nació
para los flujos de radicación de Positiva; este documento fija qué entra en la versión que se ofrece a otras
organizaciones, en qué orden y cómo se acepta cada parte.

## Objetivo

Que una organización cree y corra sus propias automatizaciones con IA (correo, documentos, bases de datos, APIs,
calendario, archivos) en el editor o pidiéndoselas a Vera, sin escribir código, con sus datos aislados de los de otras
organizaciones y sin que nada salga hacia un sistema externo sin que una persona lo haya habilitado.

## Dentro del alcance

1. **Flujos de IA con nodos.** Editor visual con disparadores (entrada manual, programación, correo nuevo, webhook),
   nodos de IA, documentos y OCR, datos, validación e integraciones; versiones de cada flujo con deshacer y volver a
   cualquier versión; ejecución con cada paso visible en vivo.
2. **Asistente Vera en chat y voz.** Una sola ejecución para los dos modos: arma, revisa, corrige y corre flujos,
   consulta resultados y explica cada nodo. No afirma lo que no se confirmó y las cifras de sus documentos salen de
   lo consultado.
3. **Integraciones incluidas.**
   - Se prueban con cuentas reales en este sprint: Google Workspace (Gmail, Calendar, Drive, Docs, Sheets, Forms) y
     Microsoft 365 (Outlook, calendario, OneDrive y SharePoint, Teams), conectadas por OAuth.
   - Disponibles: IMAP y SMTP, PostgreSQL, MySQL, SQLite y APIs HTTP. También están hechos y probados contra
     servidores locales, pero sin cuenta real: almacenamiento (S3, GCS), BigQuery, Firestore, mensajería (Telegram,
     SendGrid, Mailgun, Twilio), gestión de trabajo y clientes (Jira, Trello, Asana, Notion, HubSpot, Salesforce,
     Zoho CRM) y firma electrónica (DocuSign).
4. **Uso por organización.** Cada organización tiene sus personas, roles, flujos, conexiones, ejecuciones, archivos y
   conversaciones, aislados de las demás (ver [ROLES_Y_PERMISOS.md](ROLES_Y_PERMISOS.md)). La IA corre en Vertex AI
   con modelos, presupuesto y límites de uso por organización.
5. **Identidad de Vera.** Sin flujos, buzones, textos ni marca de Positiva, y con una biblioteca de flujos de
   demostración para clientes.

Base que lo soporta, en el proyecto de GCP del producto:

- Cloud SQL (PostgreSQL) con migraciones versionadas en lugar de los archivos locales de hoy.
- Cloud Storage cifrado para archivos de ejecuciones, adjuntos y plantillas, con cuota y limpieza por organización.
- Inicio de sesión con contraseña cifrada, segundo factor, bloqueo por intentos fallidos, recuperación y cierre de
  sesión.
- Secretos y claves de cifrado en Secret Manager, con registro de auditoría.
- Despliegue en Cloud Run con HTTPS, dominio propio y Cloud Armor; monitoreo, alertas y respaldos automáticos.

## Fuera del alcance

- Los flujos de producción de Positiva (PQRD y tutelas): siguen en sus jobs y su despliegue; Vera no escribe en
  SGDEA, en sus buzones ni en `*.positiva.gov.co`.
- SFTP, WhatsApp Business, Dropbox y Box.
- Disparadores por notificación inmediata (Gmail por Pub/Sub, suscripciones de Graph); los buzones se revisan cada N
  minutos.
- Automatización de navegador en páginas que exigen iniciar sesión o ejecutar JavaScript.

## Reglas que no cambian

- Ninguna escritura hacia afuera (enviar correo, subir archivos, ejecutar SQL, HTTP distinto de GET) ocurre si una
  persona con permiso no habilitó la escritura en esa conexión; si no, queda como escritura retenida.
- Los secretos nunca se muestran ni salen en eventos, logs, resultados ni respuestas. Vera no recibe ni escribe
  claves: abre la pantalla y la persona las ingresa.
- Borrar un flujo pide confirmación explícita con un botón.
- Ninguna organización ve ni usa datos, flujos o conexiones de otra.
- Solo dependencias con licencia MIT, BSD o Apache.

## Criterios de aceptación

| ID | Frente | Criterio |
|---|---|---|
| CA-1 | Flujos | Una persona arma un flujo con nodos en el editor o pidiéndoselo a Vera, lo corre y ve cada paso con su resultado; puede volver a cualquier versión anterior. |
| CA-2 | Asistente | Por chat y por voz, Vera arma, corrige y corre el mismo flujo con una sola ejecución, y no da por hecho lo que falló. |
| CA-3 | Integraciones | Google Workspace y Microsoft 365 se conectan por OAuth con cuentas reales y sus nodos leen; con la escritura habilitada por una persona, también escriben. |
| CA-4 | Escrituras | Sin la escritura habilitada en la conexión, toda escritura queda retenida y armada en el detalle de la ejecución. |
| CA-5 | Acceso | Cada persona entra con contraseña y segundo factor, y solo hace lo que su rol permite dentro de su organización. |
| CA-6 | Aislamiento | Los flujos, ejecuciones, archivos, conversaciones y conexiones de una organización no son visibles ni utilizables desde otra, y sus flujos corren en trabajadores separados. |
| CA-7 | Uso | Cada organización tiene sus modelos de IA y sus límites (ejecuciones, llamadas a IA, almacenamiento); al llegar al límite se detiene con un aviso claro. |
| CA-8 | Seguridad | Los secretos viven en Secret Manager y nunca aparecen en logs ni respuestas; las pruebas de seguridad con OWASP ASVS no dejan hallazgos altos abiertos. |
| CA-9 | Operación | Vera corre en Cloud Run con HTTPS y dominio propio, con monitoreo, alertas y respaldos automáticos. |
| CA-10 | Producto | No queda nada propio de Positiva; la identidad es la de Vera y hay flujos de demostración listos para mostrar. |
