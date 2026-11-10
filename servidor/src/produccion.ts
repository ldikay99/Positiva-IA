/**
 * Lo que hacen en PRODUCCIÓN los flujos de código (los jobs de PQRD y tutelas),
 * para su manifiesto. Nombres verificados en el código de cada motor
 * (`motor/config/settings.py` y `motores/tutelas/Tutelas/config.py`); nunca valores.
 * Es lo mismo que explica `docs/PRODUCCION.md`.
 *
 * origen: «secret_manager» solo donde se verificó que el secreto existe ahí; «requerida»
 * cuando el código la exige sin valor por defecto (sale del job o de Secret Manager, según
 * se configure el despliegue); «defecto_en_codigo» cuando el código trae un valor por defecto.
 */

export interface VariableProd { nombre: string; secreto: boolean; para: string; origen: "secret_manager" | "valor_en_el_job" | "requerida" | "defecto_en_codigo"; secreto_gcp?: string }
export interface ConexionProd { sistema: string; como: string; acceso: "lectura" | "lectura_y_escritura"; destino: string }
export interface EscrituraProd { donde: string; que: string; en_este_visor: string }

export interface DescripcionProduccion {
  ejecucion: string;
  disparador: { tipo: string; descripcion: string };
  entorno: VariableProd[];
  conexiones: ConexionProd[];
  escrituras: EscrituraProd[];
  despliegue: string;
  /** Verificado en la configuración real del job (`gcloud run jobs describe`, solo nombres) el 2026-11-07. */
  verificado_en_job: string;
  observaciones: string[];
  /** La IA que usa (fija: los flujos de código no cambian de proveedor). */
  ia?: { proveedor: "vertex"; modelos: string[]; ubicacion: string; autenticacion: string; fija: true };
}

const SIMULADA = "simulada mientras la compuerta de radicación esté apagada (MODO_TEST): queda registrada como escritura retenida";

/**
 * Lo que cada flujo de código lee DE VERDAD con la compuerta apagada (si su entorno
 * tiene las credenciales) y lo que queda simulado. Es lo mismo que `docs/PRODUCCION.md`.
 * Sin las credenciales de un sistema, el trabajador usa el doble de siempre y lo dice
 * en el evento «fuentes» de cada corrida.
 */
export interface LecturaEnSimulacion { sistema: string; con_credenciales: string; sin_credenciales: string; escrituras: string }

export const LECTURAS_EN_SIMULACION: Record<string, LecturaEnSimulacion[]> = {
  pqrd: [
    { sistema: "Microsoft Graph (buzón)", con_credenciales: "lee de verdad la carpeta, el hilo, los radicados con el mismo asunto y los adjuntos (solo GET; MICROSOFT_* y OUTLOOK_FOLDER_ID del entorno, o Secret Manager con el ADC)", sin_credenciales: "carpeta local de la bandeja: las consultas del buzón no encuentran nada", escrituras: "marcar, mover, reenviar y responder quedan retenidas (cliente HTTP que solo deja GET y el token)" },
    { sistema: "Registro de radicados (Cloud SQL)", con_credenciales: "con CLOUDSQL_HOST, CLOUDSQL_USER y CLOUDSQL_PASSWORD consulta iapqrd_radicados_pqrd para la capa de remitente y asunto (sesión de solo lectura, solo SELECT; no aplica a buzones de Positiva). La instancia solo tiene IP privada: si el visor no la alcanza, la capa queda degradada con el motivo, como en el job con la base caída", sin_credenciales: "sin auditoría, como el job sin esa configuración: la capa 6 no consulta y el radicado no se guarda", escrituras: "el INSERT de cada radicado queda retenido en el grabador de MODO_TEST" },
    { sistema: "Redis", con_credenciales: "no se lee: el job (correr.py) no le pasa caché de hashes al orquestador", sin_credenciales: "igual", escrituras: "ninguna" },
    { sistema: "SGDEA", con_credenciales: "el registro de personas (GET) se consulta de verdad, como siempre", sin_credenciales: "igual (no usa secretos)", escrituras: "radicar, asignar y trazabilidad quedan retenidas (MODO_TEST: sin token, sin red)" },
    { sistema: "Vertex AI (Gemini)", con_credenciales: "real, con el ADC del contenedor", sin_credenciales: "igual", escrituras: "ninguna" },
  ],
  tutelas: [
    { sistema: "Microsoft Graph (buzón)", con_credenciales: "lee de verdad la carpeta de entrada y cada hilo con sus adjuntos al traerlos (solo GET; TUTELAS-MICROSOFT-* del entorno, o Secret Manager con el ADC)", sin_credenciales: "correos de la carpeta local", escrituras: "etiquetar, responder y mover quedan retenidas" },
    { sistema: "Redis (deduplicación)", con_credenciales: "consulta de verdad si el radicado ya fue reclamado (GET de la llave tutelas:<hash>, cliente que solo deja leer)", sin_credenciales: "memoria vacía en cada corrida", escrituras: "reclamar (SET NX), liberar y marcar procesado quedan retenidos" },
    { sistema: "Postgres (juzgados)", con_credenciales: "consulta de verdad la tabla juzgados con una sesión de solo lectura (default_transaction_read_only)", sin_credenciales: "la tabla juzgados_actualizada.csv del repositorio, con la misma búsqueda (también si la base no responde desde aquí)", escrituras: "ninguna" },
    { sistema: "SGDEA", con_credenciales: "simulado: la única lectura del job (obtener_info_radicado) es sobre el número que devuelve la radicación, que con la compuerta apagada es de prueba", sin_credenciales: "igual", escrituras: "radicar y trazabilidad quedan retenidas (sesión de prueba sin red)" },
    { sistema: "Vertex AI (Gemini)", con_credenciales: "real, con el ADC del contenedor", sin_credenciales: "igual", escrituras: "ninguna" },
  ],
};

export const PRODUCCION: Record<string, DescripcionProduccion> = {
  pqrd: {
    ia: { proveedor: "vertex", modelos: ["gemini-2.5-flash"], ubicacion: "global", autenticacion: "identidad del servicio (ADC) del proyecto positiva-sgda-prd-416417", fija: true },
    ejecucion: "Job de Cloud Run (proyecto positiva-sgda-prd-416417) que procesa la carpeta AUTOMATIZACION por lotes, con un orquestador y concurrencia por remitente.",
    disparador: { tipo: "job_programado", descripcion: "El job corre periódicamente y toma los correos de la carpeta AUTOMATIZACION del buzón servicioalcliente@positiva.gov.co, en orden y agrupados por hilo." },
    entorno: [
      { nombre: "MICROSOFT_TENANT_ID", secreto: false, para: "Tenant de Entra ID para Microsoft Graph", origen: "secret_manager", secreto_gcp: "PQRD_MICROSOFT_TENANT_ID" },
      { nombre: "MICROSOFT_CLIENT_ID", secreto: false, para: "App registrada para Graph", origen: "secret_manager", secreto_gcp: "PQRD_MICROSOFT_CLIENT_ID" },
      { nombre: "MICROSOFT_CLIENT_SECRET", secreto: true, para: "Secreto de la app de Graph", origen: "secret_manager", secreto_gcp: "PQRD_MICROSOFT_CLIENT_SECRET" },
      { nombre: "OUTLOOK_REFRESH_TOKEN", secreto: true, para: "Token del buzón", origen: "secret_manager", secreto_gcp: "PQRD_OUTLOOK_REFRESH_TOKEN" },
      { nombre: "OUTLOOK_USER_EMAIL", secreto: false, para: "Buzón que se procesa", origen: "defecto_en_codigo" },
      { nombre: "OUTLOOK_FOLDER_ID", secreto: false, para: "Carpeta de entrada (AUTOMATIZACION)", origen: "secret_manager", secreto_gcp: "PQRD_OUTLOOK_FOLDER_ID" },
      { nombre: "OUTLOOK_FOLDER_DUPLICADOS / _ERROR_RADICAR / _RADICACION_EXITOSA / _REENVIADO_OTRA_AREA / _REVISAR_MANUAL", secreto: false, para: "Carpetas de destino según el desenlace", origen: "defecto_en_codigo" },
      { nombre: "SGDEA_TOKEN_URL", secreto: false, para: "Token OIDC de SGDEA", origen: "defecto_en_codigo" },
      { nombre: "SGDEA_CLIENT_ID", secreto: false, para: "Cliente de SGDEA", origen: "defecto_en_codigo" },
      { nombre: "SGDEA_USERNAME", secreto: false, para: "Usuario que radica", origen: "defecto_en_codigo" },
      { nombre: "SGDEA_CLIENT_SECRET", secreto: true, para: "Secreto del cliente de SGDEA", origen: "secret_manager", secreto_gcp: "PQRD_SGDEA_CLIENT_SECRET" },
      { nombre: "SGDEA_PASSWORD", secreto: true, para: "Clave del usuario que radica", origen: "secret_manager", secreto_gcp: "PQRD_SGDEA_PASSWORD" },
      { nombre: "CLOUDSQL_HOST", secreto: false, para: "Registro de radicados (Cloud SQL, base gcpprolinktic): IP privada de la instancia", origen: "secret_manager", secreto_gcp: "PQRD_CLOUDSQL_HOST" },
      { nombre: "CLOUDSQL_USER", secreto: false, para: "Usuario del registro de radicados", origen: "secret_manager", secreto_gcp: "PQRD_CLOUDSQL_USER" },
      { nombre: "CLOUDSQL_PASSWORD", secreto: true, para: "Clave del registro de radicados", origen: "secret_manager", secreto_gcp: "PQRD_CLOUDSQL_PASSWORD" },
      { nombre: "CLOUDSQL_INSTANCE / CLOUDSQL_TABLE_RADICADOS_IAPQRD", secreto: false, para: "Instancia (informativa) y tabla (iapqrd_radicados_pqrd)", origen: "defecto_en_codigo" },
      { nombre: "REDIS_HOST / REDIS_PORT", secreto: false, para: "Deduplicación", origen: "defecto_en_codigo" },
      { nombre: "REDIS_PASSWORD", secreto: true, para: "Clave de Redis", origen: "secret_manager", secreto_gcp: "PQRD_REDIS_PASSWORD" },
      { nombre: "GCP_PROJECT / GCP_LOCATION", secreto: false, para: "Vertex AI (Gemini) con la identidad del servicio", origen: "defecto_en_codigo" },
      { nombre: "CORREO_DE_AVISOS", secreto: false, para: "Destino de avisos internos", origen: "defecto_en_codigo" },
      { nombre: "MODO_TEST / DEDUP_EN_PRUEBAS", secreto: false, para: "Deben ser false en producción", origen: "defecto_en_codigo" },
    ],
    conexiones: [
      { sistema: "Microsoft Graph", como: "App de Entra ID (client credentials) + token del buzón", acceso: "lectura_y_escritura", destino: "servicioalcliente@positiva.gov.co" },
      { sistema: "SGDEA", como: "OIDC (cliente + usuario)", acceso: "lectura_y_escritura", destino: "SGDEA de Positiva" },
      { sistema: "Registro de radicados (Cloud SQL)", como: "Usuario y clave de Postgres por la VPC, con TLS (pg8000)", acceso: "lectura_y_escritura", destino: "gcpprolinktic, tabla iapqrd_radicados_pqrd" },
      { sistema: "Redis", como: "Clave", acceso: "lectura_y_escritura", destino: "Deduplicación" },
      { sistema: "Vertex AI (Gemini)", como: "Identidad del servicio (ADC)", acceso: "lectura", destino: "positiva-sgda-prd-416417" },
    ],
    escrituras: [
      { donde: "SGDEA", que: "Radica la solicitud con sus datos y adjuntos", en_este_visor: SIMULADA },
      { donde: "Buzón (Graph)", que: "Etiqueta, mueve o reenvía el correo según el desenlace", en_este_visor: SIMULADA },
      { donde: "Registro de radicados (Cloud SQL)", que: "Una fila por radicado (número, asunto, remitente, documento del afectado), la que consulta la capa 6", en_este_visor: "el INSERT queda retenido mientras la compuerta esté apagada (MODO_TEST); la lectura va en solo lectura" },
      { donde: "Redis", que: "Huellas para deduplicar", en_este_visor: "el job no le pasa la caché al orquestador: no se escribe" },
      { donde: "Correo", que: "Avisos internos a CORREO_DE_AVISOS", en_este_visor: SIMULADA },
    ],
    despliegue: "Pipeline de Azure DevOps (id 69) del repositorio de radicación; nunca desde este visor.",
    verificado_en_job: "ms-radicacion-ia-pqrd (us-east1)",
    observaciones: [
      "GOOGLE_API_KEY está en el job como valor directo, no desde Secret Manager. El código actual del motor no la lee: conviene quitarla o pasarla a Secret Manager.",
      "OPENAI_API_KEY (secreto PQRD_OPENAI_API_KEY) está en el job pero el código actual no la lee.",
      "Las variables con valor por defecto en el código (carpetas de destino, SGDEA_TOKEN_URL, REDIS_HOST…) no están en el job: rigen los valores del código.",
    ],
  },
  tutelas: {
    ia: { proveedor: "vertex", modelos: ["gemini-2.5-flash"], ubicacion: "global", autenticacion: "identidad del servicio (ADC) del proyecto positiva-sgda-prd-416417", fija: true },
    ejecucion: "Job de Cloud Run (proyecto positiva-sgda-prd-416417) que procesa por hilo la carpeta de entrada de notificaciones judiciales.",
    disparador: { tipo: "job_programado", descripcion: "El job corre periódicamente y toma los hilos de la carpeta de entrada del buzón notificacionesjudiciales@positiva.gov.co." },
    entorno: [
      { nombre: "TUTELAS-MICROSOFT-TENANT / -CLIENT-ID / -USERNAME / -GRAPH-URL", secreto: false, para: "App de Graph y buzón", origen: "defecto_en_codigo" },
      { nombre: "TUTELAS-MICROSOFT-CLIENT-SECRET", secreto: true, para: "Secreto de la app de Graph", origen: "secret_manager", secreto_gcp: "TUTELAS-MICROSOFT-CLIENT-SECRET" },
      { nombre: "TUTELAS-OUTLOOK-FOLDER-*", secreto: false, para: "Carpetas de entrada y de destino", origen: "defecto_en_codigo" },
      { nombre: "TUTELAS-REDIS-HOST / -PORT / -USERNAME", secreto: false, para: "Deduplicación", origen: "defecto_en_codigo" },
      { nombre: "TUTELAS-REDIS-PASSWORD", secreto: true, para: "Clave de Redis", origen: "secret_manager", secreto_gcp: "TUTELAS-REDIS-PASSWORD" },
      { nombre: "TUTELAS-SGDEA-API-URL / -CLIENT-ID / -USERNAME", secreto: false, para: "SGDEA", origen: "defecto_en_codigo" },
      { nombre: "TUTELAS-SGDEA-CLIENT-SECRET", secreto: true, para: "Secreto del cliente de SGDEA", origen: "secret_manager", secreto_gcp: "TUTELAS-SGDEA-CLIENT-SECRET" },
      { nombre: "TUTELAS-SGDEA-PASSWORD", secreto: true, para: "Clave del usuario que radica", origen: "secret_manager", secreto_gcp: "TUTELAS-SGDEA-PASSWORD" },
      { nombre: "DATABASE_HOST / DATABASE_PORT", secreto: false, para: "Postgres", origen: "valor_en_el_job" },
      { nombre: "TUTELAS-DB-NAME / TUTELAS-DB-USER", secreto: false, para: "Base y usuario de Postgres", origen: "defecto_en_codigo" },
      { nombre: "TUTELAS-DATABASE-PASSWORD", secreto: true, para: "Clave de Postgres", origen: "secret_manager", secreto_gcp: "TUTELAS-DATABASE-PASSWORD" },
      { nombre: "TUTELAS-GCP-PROJECT / -GCP-LOCATION / -GEMINI-MODEL", secreto: false, para: "Vertex AI", origen: "defecto_en_codigo" },
      { nombre: "TUTELAS-RADICAR-ENLACE-CON-ADJUNTOS", secreto: false, para: "Radicar correos con enlace si traen adjuntos", origen: "valor_en_el_job" },
      { nombre: "MODO_TEST / MODO_UAT", secreto: false, para: "Modo prueba; en producción quedan en su valor por defecto (false)", origen: "defecto_en_codigo" },
    ],
    conexiones: [
      { sistema: "Microsoft Graph", como: "App de Entra ID (client credentials)", acceso: "lectura_y_escritura", destino: "notificacionesjudiciales@positiva.gov.co" },
      { sistema: "SGDEA", como: "OIDC (cliente + usuario)", acceso: "lectura_y_escritura", destino: "SGDEA de Positiva" },
      { sistema: "Postgres", como: "Usuario y clave", acceso: "lectura_y_escritura", destino: "Base de tutelas" },
      { sistema: "Redis", como: "Clave", acceso: "lectura_y_escritura", destino: "Deduplicación" },
      { sistema: "Vertex AI (Gemini)", como: "Identidad del servicio (ADC)", acceso: "lectura", destino: "positiva-sgda-prd-416417" },
    ],
    escrituras: [
      { donde: "SGDEA", que: "Radica la tutela y su trazabilidad", en_este_visor: SIMULADA },
      { donde: "Buzón (Graph)", que: "Responde o mueve el correo a radicación exitosa o revisión manual", en_este_visor: SIMULADA },
      { donde: "Postgres y Redis", que: "Registro y deduplicación", en_este_visor: "con la compuerta apagada se leen en solo lectura (si hay credenciales) y las escrituras de Redis quedan retenidas" },
    ],
    despliegue: "Pipeline del repositorio ms-radicacion-ia-tutelas; nunca desde este visor.",
    verificado_en_job: "ms-radicacion-ia-tutelas (us-east1)",
    observaciones: [
      "DATABASE_PASSWORD está en el job como valor directo, no desde Secret Manager. El código no la lee (usa TUTELAS-DATABASE-PASSWORD, que sí viene de Secret Manager): conviene quitarla del job.",
      "TUTELAS-MICROSOFT-PASSWORD (secreto) está en el job; el código la lee pero no la usa para autenticarse (usa la app con client secret).",
      "DATABASE_HOST y DATABASE_PORT van como valor directo en el job (no son secretos).",
    ],
  },
};
