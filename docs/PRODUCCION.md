# Paso a producción

Este documento responde lo que van a pedir antes de que la plataforma radique de verdad:
- cómo se manejan las variables de entorno y los secretos;
- cómo se conecta cada cuenta;
- cómo jala la información cada flujo;
- qué escribe y bajo qué control.

Los nombres de variables y secretos de PQRD y tutelas se verificaron el 2026-11-07 en la configuración real de los jobs (`gcloud run jobs describe`, leyendo solo nombres, nunca valores) y en el código de cada motor.

## 1. Principios

1. **Cada flujo tiene su propio entorno.** No hay variables globales: cada flujo tiene sus variables (no secretas) y sus secretos, y sus trabajadores arrancan solo con ese entorno. Cambiar el entorno de un flujo no afecta a otro.
2. **Los secretos nunca están en el repo, en los logs ni en la interfaz.**
   - En la plataforma se guardan cifrados (AES-256-GCM) en el volumen de datos; la llave sale de `CLAVE_CREDENCIALES` o de un archivo `0600` generado al arrancar.
   - En producción viven en **Secret Manager** del proyecto `positiva-sgda-prd-416417` y se leen con la identidad del servicio (ADC), sin llaves descargadas.
   - En eventos, resultados y logs aparecen como `••••`.
3. **Solo una persona habilita escrituras.** Ningún flujo escribe afuera (correo, Drive, base de datos, HTTP distinto de GET) si una persona no habilitó la escritura en esa conexión, con confirmación explícita. Si no, la acción queda registrada como «escritura retenida». La asistente Vera no puede habilitarlo ni escribir secretos.
4. **Lista de bloqueo fija.** Hosts `*.positiva.gov.co`, SGDEA, los buzones de producción de PQRD y tutelas, metadatos de GCP e IP privadas no se pueden escribir desde ningún nodo genérico.
5. **Manifiesto por flujo.** `GET /api/flujos/:id/manifiesto` describe para revisión: disparador, nodos, conexiones externas, variables (nombres, sin valores, con los secretos marcados), escrituras y si están habilitadas, programación y webhook. Es la hoja que se aprueba antes de salir.

## 2. Flujo PQRD (servicio al cliente)

**Cómo jala la información.** Un job en Cloud Run lee la carpeta AUTOMATIZACION del buzón `servicioalcliente@positiva.gov.co` por Microsoft Graph, en el orden del job y agrupando por hilo. Descarga los adjuntos y los convierte: PDF, Office y correos, con OCR de tesseract (spa/eng) y visión (Gemini en Vertex) para lo que el OCR lee con baja confianza.

**Qué escribe en producción:**
- radica en SGDEA (token OIDC con su cliente y usuario);
- mueve, etiqueta o reenvía el correo en el buzón según el desenlace (radicado, revisión manual, duplicado, reenviado a otra área);
- deja trazas en Supabase y Redis para la deduplicación.

**Entorno (nombres; los valores van en Secret Manager o en el job):**

| Variable | Secreto | Para qué |
|---|---|---|
| `MICROSOFT_TENANT_ID`, `MICROSOFT_CLIENT_ID` | no | App registrada en Entra ID para Graph |
| `MICROSOFT_CLIENT_SECRET` | sí | Secreto de esa app |
| `OUTLOOK_REFRESH_TOKEN` | sí | Token del buzón |
| `OUTLOOK_USER_EMAIL`, `OUTLOOK_FOLDER_ID`, `OUTLOOK_FOLDER_*` | no | Buzón y carpetas de entrada y de destino |
| `SGDEA_TOKEN_URL`, `SGDEA_CLIENT_ID`, `SGDEA_USERNAME` | no | Autenticación con SGDEA |
| `SGDEA_CLIENT_SECRET`, `SGDEA_PASSWORD` | sí | Credenciales de SGDEA |
| `SUPABASE_URL` | no | Base de trazas |
| `SUPABASE_SERVICE_ROLE_KEY` | sí | Llave de servicio |
| `REDIS_HOST`, `REDIS_PORT` | no | Deduplicación |
| `REDIS_PASSWORD` | sí | Clave de Redis |
| `GCP_PROJECT`, `GCP_LOCATION` | no | Vertex AI (Gemini) con la identidad del servicio |
| `CORREO_DE_AVISOS` | no | A dónde van los avisos internos |
| `MODO_TEST`, `DEDUP_EN_PRUEBAS` | no | Modo prueba: deben estar en `false` en producción |

**Verificado en el job `ms-radicacion-ia-pqrd` (us-east1).** Estos vienen de Secret Manager, con el prefijo `PQRD_`:
- `MICROSOFT_TENANT_ID`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`
- `OUTLOOK_FOLDER_ID`, `OUTLOOK_REFRESH_TOKEN`
- `SGDEA_CLIENT_SECRET`, `SGDEA_PASSWORD`
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- `REDIS_PASSWORD`

El resto rige con el valor por defecto del código. El visor usa los secretos de Graph solo para **leer** el buzón (GET).

**Observaciones de seguridad:**
- `GOOGLE_API_KEY` está en el job como **valor directo**, no desde Secret Manager, y el código actual no la lee. Conviene quitarla o pasarla a Secret Manager.
- `OPENAI_API_KEY` (`PQRD_OPENAI_API_KEY`) está en el job, pero el código actual no la lee.

**Despliegue:** se hace con el pipeline de Azure DevOps (id 69) desde el repositorio de radicación, nunca desde este visor.

## 3. Flujo de tutelas (notificaciones judiciales)

**Cómo jala la información.** Lee por Graph la carpeta de entrada del buzón `notificacionesjudiciales@positiva.gov.co`. Agrupa los correos por hilo y toma el más reciente como principal. Valida que el remitente sea un juzgado, busca el juzgado en su registro, detecta tutela o actuación, exige anexos y deduplica con Redis y Postgres.

**Qué escribe en producción:**
- radica la tutela y su trazabilidad en SGDEA;
- responde o mueve el correo a las carpetas de radicación exitosa o revisión manual.

**Entorno:**

| Variable | Secreto | Para qué |
|---|---|---|
| `TUTELAS-MICROSOFT-TENANT`, `-CLIENT-ID`, `-USERNAME`, `-GRAPH-URL` | no | App de Graph y buzón |
| `TUTELAS-MICROSOFT-CLIENT-SECRET` | sí | Secreto de la app (Secret Manager) |
| `TUTELAS-OUTLOOK-FOLDER-*` | no | Carpetas de entrada y de destino |
| `TUTELAS-REDIS-HOST`, `-PORT`, `-USERNAME` | no | Deduplicación |
| `TUTELAS-REDIS-PASSWORD` | sí | Clave de Redis |
| `TUTELAS-SGDEA-API-URL`, `-CLIENT-ID`, `-USERNAME` | no | SGDEA |
| `TUTELAS-SGDEA-CLIENT-SECRET`, `TUTELAS-SGDEA-PASSWORD` | sí | Credenciales de SGDEA |
| `DATABASE_HOST`, `DATABASE_PORT`, `TUTELAS-DB-NAME`, `TUTELAS-DB-USER` | no | Postgres |
| `TUTELAS-DATABASE-PASSWORD` | sí | Clave de Postgres |
| `TUTELAS-GCP-PROJECT`, `-GCP-LOCATION`, `-GEMINI-MODEL` | no | Vertex AI |
| `MODO_TEST`, `MODO_UAT`, `TUTELAS-RADICAR-ENLACE-CON-ADJUNTOS` | no | Modo y reglas del job |

**Verificado en el job `ms-radicacion-ia-tutelas` (us-east1).** Estos vienen de Secret Manager:
- `TUTELAS-MICROSOFT-CLIENT-SECRET`, `TUTELAS-MICROSOFT-PASSWORD`
- `TUTELAS-REDIS-PASSWORD`
- `TUTELAS-SGDEA-CLIENT-SECRET`, `TUTELAS-SGDEA-PASSWORD`
- `TUTELAS-DATABASE-PASSWORD`

Van como valor directo `DATABASE_HOST`, `DATABASE_PORT` y `TUTELAS-RADICAR-ENLACE-CON-ADJUNTOS`.

**Observaciones de seguridad:**
- `DATABASE_PASSWORD` está en el job como **valor directo** y el código no la lee: usa `TUTELAS-DATABASE-PASSWORD`, que viene de Secret Manager. Conviene quitarla del job.
- `TUTELAS-MICROSOFT-PASSWORD` está en el job, pero la autenticación con Graph usa el client secret de la app.

## 4. Flujos creados en la plataforma

Se arman con nodos, en el editor o con Vera.

**Cómo jalan la información.** Según su disparador: un correo de la bandeja, una entrada manual, una programación, un webhook o un buzón que se revisa cada N minutos. Con los conectores leen correo (IMAP o Graph), Drive, bases de datos (Postgres, MySQL, SQLite), hojas de cálculo, archivos y APIs HTTP.

**Cómo se conecta una cuenta.** Con una **conexión** del tipo que corresponda; los campos secretos no se pueden volver a leer.

| Conexión | Campos |
|---|---|
| `microsoft_graph` | tenant, client id, client secret, buzón |
| `imap` / `smtp` | host, puerto, usuario, clave, TLS |
| `google_servicio` | JSON de cuenta de servicio, compartiendo la carpeta de Drive con ella |
| `postgres` / `mysql` | host, puerto, base, usuario, clave, SSL; se recomienda un usuario de solo lectura |
| `sqlite` | ruta dentro de `/datos/archivos` |
| `http_bearer` / `http_basico` | token, o usuario y clave |

Una conexión puede ser global o propia de un flujo; un flujo solo usa las globales y las suyas.

**Variables de entorno.** Cada flujo las define en su sección «Entorno» y las usa en los nodos como `{{entorno.NOMBRE}}`. Los secretos se escriben una vez y nunca se muestran.

**Qué escriben.** Lo que su manifiesto declare, y solo si la conexión tiene la escritura habilitada por una persona.

## 5. Lo que ofrece la plataforma para esto

- **`GET /api/flujos/:id/manifiesto`** (y «Manifiesto» en la pantalla, con «Descargar JSON»):
  - en los flujos creados en la plataforma: disparador, nodos, conexiones, hosts, variables (sin valores), escrituras y si están habilitadas, programación, webhook, archivos y tablas;
  - en PQRD y tutelas: además, sus variables de producción con el secreto de GCP del que sale cada una, sus conexiones, sus escrituras y las observaciones de arriba.
- **Entorno por flujo:**
  - `GET /api/flujos/:id/entorno` muestra los secretos siempre enmascarados;
  - `PUT` y `DELETE /api/flujos/:id/entorno/:nombre`: crear o cambiar un secreto exige la confirmación de una persona.
- **Conexiones:**
  - `/api/credenciales`, cifradas y globales o propias de un flujo;
  - el permiso de escritura solo lo da una persona y se apaga si cambia algún valor.
- **`scripts/manifiesto_a_gcp.py <flujo> --job <job>`** imprime, sin ejecutar nada:
  - los `gcloud secrets create` (sin valores) y el acceso de la cuenta de servicio;
  - el `gcloud run jobs update` con las variables y los secretos montados.

## 6. Cómo pasar un flujo a producción

1. Revisar el manifiesto del flujo (`/api/flujos/:id/manifiesto`, o «Manifiesto» en la pantalla).
2. Generar los comandos con `scripts/manifiesto_a_gcp.py`: un secreto por cada variable secreta (`FLUJO-<ID>-<NOMBRE>`) con acceso de lectura solo para la cuenta de servicio del job; una persona carga cada valor con `gcloud secrets versions add`.
3. Aplicar el `gcloud run jobs update` que genera el script: variables no secretas como valores y secretas montadas desde Secret Manager.
4. Habilitar las escrituras que el manifiesto declara y que el área aprobó: una por una, con su cuenta de servicio o usuario dedicado.
5. Desplegar por el pipeline correspondiente.
6. Correr primero con `MODO_TEST=true` sobre correos reales (solo lectura) y comparar con el desenlace esperado. Después, apagar el modo prueba.

## 7. Estado de este documento

- [x] Mapeo del entorno de cada flujo a Secret Manager y Cloud Run (`scripts/manifiesto_a_gcp.py`).
- [x] Manifiesto de PQRD y tutelas verificado contra la configuración real de sus jobs, con observaciones de seguridad.
