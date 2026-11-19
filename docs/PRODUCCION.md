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
- guarda cada radicado en el registro de radicados de Cloud SQL (base `gcpprolinktic`, tabla `iapqrd_radicados_pqrd`), que es también la capa 6 de la deduplicación (remitente y asunto; no aplica a buzones de Positiva). Desde el motor `1d11744` ya no usa Supabase.

**Entorno (nombres; los valores van en Secret Manager o en el job):**

| Variable | Secreto | Para qué |
|---|---|---|
| `MICROSOFT_TENANT_ID`, `MICROSOFT_CLIENT_ID` | no | App registrada en Entra ID para Graph |
| `MICROSOFT_CLIENT_SECRET` | sí | Secreto de esa app |
| `OUTLOOK_REFRESH_TOKEN` | sí | Token del buzón |
| `OUTLOOK_USER_EMAIL`, `OUTLOOK_FOLDER_ID`, `OUTLOOK_FOLDER_*` | no | Buzón y carpetas de entrada y de destino |
| `SGDEA_TOKEN_URL`, `SGDEA_CLIENT_ID`, `SGDEA_USERNAME` | no | Autenticación con SGDEA |
| `SGDEA_CLIENT_SECRET`, `SGDEA_PASSWORD` | sí | Credenciales de SGDEA |
| `CLOUDSQL_HOST`, `CLOUDSQL_USER` | no | Registro de radicados en Cloud SQL (IP privada, TLS). Sin ellos y sin la clave, el job corre «sin auditoría» |
| `CLOUDSQL_PASSWORD` | sí | Clave del registro de radicados |
| `CLOUDSQL_INSTANCE`, `CLOUDSQL_PORT`, `CLOUDSQL_DB`, `CLOUDSQL_SSL`, `CLOUDSQL_TABLE_RADICADOS_IAPQRD` | no | Por defecto 5432, `gcpprolinktic`, TLS y `iapqrd_radicados_pqrd` |
| `REDIS_HOST`, `REDIS_PORT` | no | Deduplicación |
| `REDIS_PASSWORD` | sí | Clave de Redis |
| `GCP_PROJECT`, `GCP_LOCATION` | no | Vertex AI (Gemini) con la identidad del servicio |
| `CORREO_DE_AVISOS` | no | A dónde van los avisos internos |
| `MODO_TEST`, `DEDUP_EN_PRUEBAS` | no | Modo prueba: deben estar en `false` en producción |

**Verificado en el job `ms-radicacion-ia-pqrd` (us-east1).** Estos vienen de Secret Manager, con el prefijo `PQRD_`:
- `MICROSOFT_TENANT_ID`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`
- `OUTLOOK_FOLDER_ID`, `OUTLOOK_REFRESH_TOKEN`
- `SGDEA_CLIENT_SECRET`, `SGDEA_PASSWORD`
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (verificado el 2026-11-07; el motor `1d11744` ya no los lee. Según `mcp-fix`, infra monta ahora `PQRD_CLOUDSQL_*`: sin verificar en el job desde aquí)
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

## 5b. PQRD y tutelas aquí, como el job: entorno real y compuerta de radicación

PQRD y tutelas corren en la plataforma con la configuración real de su job, pero con una **compuerta de radicación** que viene **apagada**.

### Entorno real

- El entorno de `pqrd` y de `tutelas` acepta **exactamente los nombres de su job** (`NOMBRES_DEL_JOB` en `servidor/src/entornos.ts`), aunque choquen con la lista protegida de los flujos declarativos. Solo tutelas acepta nombres con guiones (`TUTELAS-REDIS-PASSWORD`…), y solo los de su job.
- Ningún flujo puede poner `MODO_TEST`, `MODO_UAT`, `DEDUP_EN_PRUEBAS`, `RADICACION_REAL`, `PERMITIR_RADICACION_REAL`, `SECRETOS_DEL_FLUJO` ni lo interno de la plataforma (`PATH`, `RUTA_*`, `CLAVE_CREDENCIALES`…).
- Cada variable del job, secreta o no, la pone una persona: `PUT /api/flujos/:id/entorno/:nombre` con `X-Confirmado-Por-Persona: si` y `{valor, secreto}`. Los secretos quedan cifrados (AES-256-GCM) y ninguna ruta los devuelve.
- Los trabajadores arrancan con ese entorno encima del de la plataforma. `SECRETOS_DEL_FLUJO` les dice los **nombres** de los secretos, nunca sus valores, para que también los enmascaren.
- **Máscara**: el servidor conoce los valores secretos del entorno de cada flujo de código y los tapa (`••••`) en todo lo que reenvía o guarda: eventos, consola (stdout y stderr, por líneas completas), resultados, historial de corridas, error de arranque y errores del buzón real. Tapa el valor tal cual, escapado en JSON y codificado en URL.

### Qué se lee de verdad con la compuerta apagada

Sin las credenciales de un sistema, el trabajador usa el doble de siempre. Cada corrida lo anuncia en el evento `fuentes` (qué es real y qué es doble) y en el evento `compuerta` («Radicación real: apagada (simulación)»).

**PQRD** (`puente/trabajador.py`):

| Sistema | Con credenciales en el entorno | Escrituras |
|---|---|---|
| Graph (buzón) | Lee la carpeta, el hilo, los radicados con el mismo asunto y los adjuntos. Solo GET y el token: `MICROSOFT_*` y `OUTLOOK_FOLDER_ID` del entorno, o Secret Manager con el ADC si faltan. | Marcar, mover, reenviar y responder quedan retenidas. |
| Registro de radicados (Cloud SQL) | Con `CLOUDSQL_HOST`, `CLOUDSQL_USER` y `CLOUDSQL_PASSWORD`, la capa de remitente y asunto consulta `iapqrd_radicados_pqrd` en una sesión de solo lectura que solo deja SELECT (`solo_lectura.conectar_registro_solo_lectura`). La instancia solo tiene IP privada: si el visor no la alcanza (prueba de 3 s, una vez por proceso), la capa queda degradada con el motivo, como en el job con la base caída. Sin esas variables, «sin auditoría», como el job. | El INSERT de cada radicado va al grabador de MODO_TEST (`REGISTRO_RADICADO`). |
| Redis | No se lee: el job (`correr.py`) no le pasa caché de hashes al orquestador. | Ninguna. |
| SGDEA | El registro de personas (GET) se consulta de verdad, como siempre. | Radicar, asignar y trazabilidad quedan retenidas (MODO_TEST: sin token y sin red). |
| Vertex | Real, con el ADC. | — |

**Tutelas** (`puente/trabajador_tutelas.py`):

| Sistema | Con credenciales en el entorno | Escrituras |
|---|---|---|
| Graph (buzón) | El hilo y sus adjuntos se leen del buzón real al traerlos (solo GET): `TUTELAS-MICROSOFT-*` del entorno, o Secret Manager si falta. | Etiquetar, responder y mover quedan retenidas. |
| Redis | Antes de reclamar un radicado consulta (GET) si la llave `tutelas:<hash>` ya existe, que es lo que decide el `SET NX` del job: si existe, es duplicado. El cliente solo deja GET, EXISTS, TTL y PING. | Reclamar, liberar y marcar procesado quedan retenidos (en memoria). |
| Postgres | Consulta la tabla `juzgados` con una sesión de solo lectura (`default_transaction_read_only=on` y `set_session(readonly=True)`). Si la base no responde desde aquí (está en una IP privada de GCP), usa `juzgados_actualizada.csv` y lo dice. | Ninguna. |
| SGDEA | **Simulado.** La única lectura del job (`obtener_info_radicado`) es sobre el número que devuelve la radicación, que con la compuerta apagada es de prueba. | Radicar y trazabilidad quedan retenidas (sesión de prueba sin red). |
| Vertex | Real, con el ADC. | — |

Lo mismo sale en el manifiesto (`lecturas_con_la_compuerta_apagada`).

### La compuerta de radicación

- `GET /api/flujos/:id/compuerta` → `{radicacion_real, permitido_en_despliegue, cambiado_por?, cambiado?, radicacion_real_efectiva, texto}`.
- `PUT /api/flujos/:id/compuerta` con `{radicacion_real: true|false}`.
- **Encenderla exige dos llaves a la vez:**
  1. `X-Confirmado-Por-Persona: si`, que la web solo manda tras la confirmación explícita en «Entorno › Ejecución». Vera no tiene herramienta para esto y `puente/asistente.py` nunca manda ese encabezado.
  2. `PERMITIR_RADICACION_REAL=si` en el entorno del **contenedor**. **No está en `docker-compose.yml`**: en este despliegue la compuerta no se puede encender.
- Apagarla nunca pide nada.
- El estado vive en `/datos/estado/compuertas.json`. Si el archivo dice «encendida» pero falta la segunda llave, no escribe nada (`radicacion_real_efectiva: false`).
- Al cambiar la compuerta, los trabajadores del flujo se reemplazan al quedar libres. Un trabajador que arrancó con la otra compuerta no recibe más trabajo. Además, cada orden lleva `radicacion_real` y el trabajador no la corre si no coincide con la suya.
- **Encendida**, el trabajador corre como el job: `MODO_TEST=false`, con `OutlookService`, `RadicacionService` y `RegistroDeRadicados` reales (PQRD), o con `OutlookService`, `RedisService` (con el cerrojo del buzón del job), `JuzgadosService` y `SGDEAService` reales (tutelas). Cada escritura se anuncia con `bloqueada: false`.
  - Si falta un secreto del job, el trabajador no arranca.
  - Solo atiende correos e hilos traídos del buzón real, nunca copias de la carpeta local.

**Cómo habilitarla (solo cuando se apruebe):**
1. Agregar `PERMITIR_RADICACION_REAL: "si"` en `environment:` del servicio en el despliegue, o en un archivo de override que no esté en el repositorio.
2. Recrear el contenedor.
3. Una persona la enciende en «Entorno › Ejecución», leyendo la confirmación.

Para cerrarla de nuevo basta apagarla en la web o quitar la variable del contenedor.

Antes de encenderla, el job de Cloud Run del mismo flujo debe estar pausado. Si no, los dos toman los mismos correos (tutelas se protege con el cerrojo de Redis del job; PQRD no tiene cerrojo).

### Programación como el job

- `GET /api/flujos/:id/programacion` y `PUT` con `{cron, zona, activo, n}`.
- Los valores por defecto son los de Cloud Scheduler:
  - PQRD: `*/30 * * * *`;
  - tutelas: `5,25,45 6-17 * * *`;
  - ambas en America/Bogota.
- Lote por corrida: 50 (`CORREOS_POR_CORRIDA` en PQRD; en tutelas, los hilos de sus 50 correos más recientes).
- Vienen con `activo: false`.
- Encendida, en cada toque toma el siguiente lote del buzón real (solo lectura, por el mismo camino de `POST /api/buzon/correr?flujo=`) y lo corre. Con la compuerta apagada, en simulación.
- Nunca dos corridas programadas del mismo flujo a la vez: si la anterior sigue, el toque se salta y se avisa.
- Cada corrida tiene el tope del job (3600 s): lo que siga después se corta.
- Se guarda en `/datos/estado/programaciones.json`.

## 6. Cómo pasar un flujo a producción

1. Revisar el manifiesto del flujo (`/api/flujos/:id/manifiesto`, o «Manifiesto» en la pantalla).
2. Generar los comandos con `scripts/manifiesto_a_gcp.py`: un secreto por cada variable secreta (`FLUJO-<ID>-<NOMBRE>`) con acceso de lectura solo para la cuenta de servicio del job; una persona carga cada valor con `gcloud secrets versions add`.
3. Aplicar el `gcloud run jobs update` que genera el script: variables no secretas como valores y secretas montadas desde Secret Manager.
4. Habilitar las escrituras que el manifiesto declara y que el área aprobó: una por una, con su cuenta de servicio o usuario dedicado.
5. Desplegar por el pipeline correspondiente.
6. Correr primero con `MODO_TEST=true` sobre correos reales (solo lectura) y comparar con el desenlace esperado. Después, apagar el modo prueba.

## 6b. Estado de la plataforma en PostgreSQL (Cloud SQL)

Sin `DATABASE_URL` todo sigue en archivos del volumen, como siempre. Con `DATABASE_URL`, el estado de la plataforma vive
en PostgreSQL con aislamiento por organización: tablas, políticas y cifrado en
[MODELO_DE_DATOS.md](MODELO_DE_DATOS.md).

### Qué pasa a la base y qué no

| En PostgreSQL | Sigue en archivos (por ahora) |
|---|---|
| Flujos declarativos y sus versiones, copias de pqrd y tutelas, nombres propios de los originales, estructuras editadas y sus versiones, historial de versiones (`flujos/`, `flujos_codigo/`, `flujos_historial/`) | Conexiones y variables secretas (`credenciales.json`, `entornos/`): pasan a la DEK de cada organización o a Secret Manager en su propia tarea |
| Ejecuciones y sus eventos (`corridas/`) | `clave_descargas` (va a Secret Manager) |
| Aprobaciones con el estado de la ejecución y la marca de reanudada; aplazadas (`aprobaciones/`, `aplazadas/`) | Notificaciones, plantillas de documentos, tablas de los flujos, límites y estado de los disparadores |
| Programación de los de código y marcas del programador (`programaciones.json`, `programador.json`) | Archivos, bandeja, recibidos, buzón real, visor y caché (sus bytes van a Cloud Storage; la ficha, a `vera.archivos`) |
| Conversaciones y mensajes; la ficha de cada adjunto (`chats/`; los bytes, en el volumen) | Telemetría (`trabajo/`) |
| Configuración, historial de cambios, perfiles, IA global y de Vera, recursos y compuertas | |

Los trabajadores de Python no tocan la base: nunca reciben `DATABASE_URL` ni la llave maestra (el servidor las quita de
su entorno). Con la base, la aprobación y la aplazada que antes dejaban en disco las entregan por su canal de eventos y,
al reanudar, la orden les trae la aprobación y el estado de la corrida.

**Una sola instancia del servidor por organización** mientras no haya inicio de sesión: el programador, las aplazadas y
la reclamación de una aprobación para reanudarla corren en el proceso (la base guarda la marca, pero el reparto entre
instancias no está hecho). En Cloud Run: `--max-instances=1`.

### Variables

| Variable | Para qué |
|---|---|
| `DATABASE_URL` | `postgresql://<usuario>@<host>:<puerto>/<base>` del usuario de la **aplicación** (miembro de `vera_app`). Sin ella, archivos |
| `DATABASE_PASSWORD_FILE` | Archivo con su clave (secreto de Docker o montado). Nunca la clave en la URL de un archivo del repositorio |
| `VERA_ORGANIZACION` | Slug de la organización del despliegue (por defecto `principal`). Tiene que existir |
| `VERA_KEK_LOCAL` / `VERA_KEK_LOCAL_ARCHIVO` | Llave maestra local (base64 de 32 bytes) que envuelve las llaves de datos: Docker local y pruebas |
| `VERA_KEK_KMS` | La KEK en Cloud KMS. Se conecta en el despliegue en GCP; este repositorio no la implementa ni la llama (el servidor no arranca con ella) |
| `BD_URL_MIGRADOR`, `BD_CLAVE_MIGRADOR_ARCHIVO` | Usuario **migrador** (dueño de las tablas) para `migrar`, `organizacion` y `migrar-datos` |
| `BD_CONEXIONES_MAX`, `BD_TOPE_CONSULTA_MS`, `BD_ESPERA_CONEXION_MS` | Pool (10), tope por consulta (60 s) y espera de conexión (10 s) |
| `PUERTO_BD`, `RUTA_SECRETOS_BD`, `MEMORIA_BD` | Docker local: puerto en 127.0.0.1 (5482), carpeta de las claves (`./datos/secretos-bd`) y memoria (1 GB) |

### Local con Docker

El servicio `postgres` de `docker-compose.yml` (perfil `bd`) usa `postgres:18.7-trixie` fijada por digest, corre como
el usuario `postgres` sin capacidades (`cap_drop: ALL`, `no-new-privileges`), con el sistema de archivos de solo
lectura (escribe solo en su volumen `positiva-ia-bd` y en tmpfs), publicado solo en `127.0.0.1:${PUERTO_BD:-5482}`,
con healthcheck y las claves como secretos de Docker. Nada de eso va al repositorio.

```bash
# 1. Una vez: las claves (fuera del repositorio; datos/ no se versiona)
mkdir -p datos/secretos-bd
openssl rand -base64 24 | tr -d '\n' > datos/secretos-bd/migrador
openssl rand -base64 24 | tr -d '\n' > datos/secretos-bd/app
openssl rand -base64 32 | tr -d '\n' > datos/secretos-bd/kek
chmod 600 datos/secretos-bd/*

# 2. La base (la primera vez crea vera_app y vera_app_login con servidor/bd/inicio/)
docker compose --profile bd up -d postgres

# 3. Migraciones y organización (con el migrador)
cd servidor
export BD_URL_MIGRADOR=postgresql://vera_migrador@127.0.0.1:5482/vera BD_CLAVE_MIGRADOR_ARCHIVO=../datos/secretos-bd/migrador
npm run bd -- migrar
npm run bd -- organizacion principal "Positiva"

# 4. El estado de hoy a la base: primero de prueba (cuenta y revierte), luego de verdad; se puede repetir
npm run bd -- migrar-datos --organizacion principal --datos <ruta del volumen positiva-ia-datos> --prueba
npm run bd -- migrar-datos --organizacion principal --datos <ruta del volumen positiva-ia-datos>

# 5. La plataforma con la base
cd .. && docker compose -f docker-compose.yml -f docker-compose.bd.yml --profile bd up -d
```

En el contenedor de la aplicación los mismos comandos son `node servidor/dist/bd/cli.js <orden>`. `migrar-datos` va en
una sola transacción (o pasa todo o nada), no repite lo que ya está y no toca los archivos: para volver atrás basta
quitar `DATABASE_URL`. Lo que la base rechace (un renglón dañado, un flujo que ya no existe) se omite, se cuenta y se
dice por qué.

El servidor no arranca con la base si faltan la llave maestra, la organización o una migración (dice cuál y cómo), ni si
una migración ya aplicada cambió en el código: las migraciones aplicadas no se editan, se agrega otra.

### Cloud SQL (sin conectarse desde aquí)

- Instancia PostgreSQL 18 (`POSTGRES_18`, la misma versión mayor que la local), solo IP privada, `ssl_mode=ENCRYPTED_ONLY`,
  CMEK, respaldos de 7 días con recuperación a un punto en el tiempo y la marca `cloudsql.iam_authentication=on`.
- Dos identidades, sin claves descargadas:
  - **aplicación**: la cuenta de servicio de Cloud Run como usuario IAM de la base (`<cuenta>@<proyecto>.iam`), con
    `GRANT vera_app TO "<cuenta>@<proyecto>.iam"`. Nunca dueña de tablas ni con `BYPASSRLS`;
  - **migrador**: otra cuenta de servicio (o un usuario propio con su clave en Secret Manager), dueña de las tablas.
    Las migraciones las corre él, nunca el usuario `postgres`: las políticas del dueño se atan a quien corre la 0001.
- Conexión: Cloud SQL Auth Proxy como contenedor auxiliar (o en local, para operar) con IAM automático:
  ```bash
  cloud-sql-proxy --private-ip --auto-iam-authn --port 5432 <proyecto>:<región>:<instancia>
  # DATABASE_URL=postgresql://<cuenta>%40<proyecto>.iam@127.0.0.1:5432/vera   (sin clave: la pone el proxy)
  ```
  El proxy cifra el tramo hasta la instancia; entre la aplicación y el proxy es localhost.
- Migraciones en cada despliegue: un job de Cloud Run con la imagen y la identidad del migrador corre
  `node servidor/dist/bd/cli.js migrar` antes de pasar el tráfico a la revisión nueva.
- La llave maestra de cada organización va en Cloud KMS (`VERA_KEK_KMS`) cuando se conecte; mientras tanto, en GCP no se
  usa la KEK local.

### Pruebas

`servidor/src/pruebas_bd.ts` corre contra un PostgreSQL real: crea su propia base y su usuario de aplicación, los borra
al terminar y se salta con un aviso si no hay `DATABASE_URL_PRUEBAS` (un usuario que pueda crear bases y roles):

```bash
cd servidor
DATABASE_URL_PRUEBAS="postgresql://vera_migrador:$(cat ../datos/secretos-bd/migrador)@127.0.0.1:5482/postgres" npm run prueba:bd
```

Prueba las migraciones (idempotentes, una editada se detecta), el aislamiento con SQL crudo y sin filtro (otra
organización y ninguna organización no ven ni cambian nada; las particiones no se leen directo), la auditoría que solo
se agrega, el cifrado atado a su fila, cada tipo de estado a través de su módulo, la migración de datos (prueba,
completa, idempotente) y que la URL y la llave no lleguen a los procesos hijos.

## 7. Estado de este documento

- [x] Mapeo del entorno de cada flujo a Secret Manager y Cloud Run (`scripts/manifiesto_a_gcp.py`).
- [x] Manifiesto de PQRD y tutelas verificado contra la configuración real de sus jobs, con observaciones de seguridad.
- [x] PQRD y tutelas como el job: entorno con los nombres reales, lecturas reales en solo lectura, compuerta de radicación con dos llaves y programación del Cloud Scheduler (sección 5b).
- [x] Estado de la plataforma en PostgreSQL con migraciones versionadas y aislamiento por organización (sección 6b); probado contra PostgreSQL 18 local, sin conectarse a Cloud SQL.
- [ ] Copiar los valores reales al entorno de cada flujo (lo hace Jonathan con `PUT /api/flujos/:id/entorno/:nombre`).
