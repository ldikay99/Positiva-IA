import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { config } from "./config.ts";
import { TIPOS_CREDENCIAL_IA } from "./ia_credenciales.ts";

/**
 * Las credenciales de los conectores (buzones, bases de datos, Drive, APIs).
 *
 * - Se guardan cifradas con AES-256-GCM en <estado>/credenciales.json. La llave
 *   sale de CLAVE_CREDENCIALES o de <estado>/.clave-credenciales (0600), que se
 *   genera la primera vez.
 * - Los valores son de solo escritura: ninguna API los devuelve; solo los nombres
 *   de los campos. El trabajador de Python los recibe por stdin, solo los de las
 *   credenciales que usa la corrida y solo las globales o las del propio flujo.
 * - `permitir_escritura` solo lo cambia una persona (la ruta exige el encabezado
 *   X-Confirmado-Por-Persona: si, que Vera nunca manda). Reemplazar los valores
 *   de una credencial lo vuelve a apagar.
 */

export interface CampoDeCredencial {
  nombre: string;
  titulo: string;
  tipo: "texto" | "entero" | "booleano" | "texto-largo" | "opcion";
  secreto?: boolean;
  obligatorio?: boolean;
  defecto?: string | number | boolean;
  opciones?: string[];
  ayuda?: string;
  /** Lo llena la plataforma (tokens de OAuth, cuenta conectada): no se muestra en el formulario ni se acepta en un PUT. */
  interno?: boolean;
}

export interface TipoDeCredencial {
  tipo: string;
  nombre: string;
  descripcion: string;
  ayuda: string;
  campos: CampoDeCredencial[];
}

const host = (ayuda = "nombre del servidor"): CampoDeCredencial => ({ nombre: "host", titulo: "Servidor", tipo: "texto", obligatorio: true, ayuda });
const puerto = (defecto: number): CampoDeCredencial => ({ nombre: "puerto", titulo: "Puerto", tipo: "entero", defecto });
const usuario = (obligatorio = true): CampoDeCredencial => ({ nombre: "usuario", titulo: "Usuario", tipo: "texto", obligatorio });
const clave = (obligatorio = true): CampoDeCredencial => ({ nombre: "clave", titulo: "Clave", tipo: "texto", secreto: true, obligatorio });
const hosts: CampoDeCredencial = { nombre: "hosts", titulo: "Hosts permitidos", tipo: "texto", obligatorio: true, ayuda: "a qué hosts se puede mandar esta credencial, separados por coma (p. ej. api.ejemplo.com)" };

/** Los alcances por defecto de las conexiones OAuth (nombres cortos; Google les antepone https://www.googleapis.com/auth/). */
export const ALCANCES_GOOGLE = "openid email gmail.modify gmail.send calendar drive documents spreadsheets forms.responses.readonly forms.body.readonly";
export const ALCANCES_MICROSOFT = "offline_access openid email User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite Files.ReadWrite.All Sites.ReadWrite.All ChannelMessage.Send ChatMessage.Send";
/** Lo que deja la conexión OAuth (cifrado como todo lo demás; los tokens son secretos y el motor los enmascara). */
export const CAMPOS_OAUTH: CampoDeCredencial[] = [
  { nombre: "refresh_token", titulo: "Token de renovación", tipo: "texto", secreto: true, interno: true },
  { nombre: "access_token", titulo: "Token de acceso", tipo: "texto", secreto: true, interno: true },
  { nombre: "expira", titulo: "Vence", tipo: "texto", interno: true },
  { nombre: "cuenta", titulo: "Cuenta conectada", tipo: "texto", interno: true },
  { nombre: "alcances", titulo: "Permisos otorgados", tipo: "texto", interno: true },
  { nombre: "conectada", titulo: "Conectada", tipo: "texto", interno: true },
];
export const TIPOS_OAUTH = new Set(["google_oauth", "microsoft_oauth"]);

export const TIPOS_CREDENCIAL: TipoDeCredencial[] = [
  { tipo: "imap", nombre: "Buzón IMAP", descripcion: "Leer correo por IMAP (solo lectura).", ayuda: "Para Gmail usa imap.gmail.com:993 con una contraseña de aplicación. Nunca buzones de Positiva.",
    campos: [host("p. ej. imap.gmail.com"), puerto(993), usuario(), clave(), { nombre: "tls", titulo: "Cifrado", tipo: "opcion", opciones: ["ssl", "starttls", "ninguno"], defecto: "ssl" }] },
  { tipo: "smtp", nombre: "Envío SMTP", descripcion: "Enviar correo por SMTP (escritura: hay que habilitarla).", ayuda: "p. ej. smtp.gmail.com:587 con STARTTLS. Nunca a direcciones de Positiva.",
    campos: [host("p. ej. smtp.gmail.com"), puerto(587), usuario(false), clave(false), { nombre: "tls", titulo: "Cifrado", tipo: "opcion", opciones: ["starttls", "ssl", "ninguno"], defecto: "starttls" },
      { nombre: "remitente", titulo: "Remitente", tipo: "texto", ayuda: "la dirección From (por defecto el usuario)" }] },
  { tipo: "microsoft_graph", nombre: "Microsoft 365 (Graph)", descripcion: "Leer y enviar correo de un buzón de Microsoft 365 con una aplicación de Entra ID.",
    ayuda: "La aplicación necesita Mail.Read (y Mail.Send para enviar) de tipo aplicación. Los buzones de PQRD y tutelas están bloqueados siempre.",
    campos: [{ nombre: "tenant", titulo: "Tenant", tipo: "texto", obligatorio: true }, { nombre: "client_id", titulo: "Client ID", tipo: "texto", obligatorio: true },
      { nombre: "client_secret", titulo: "Client secret", tipo: "texto", secreto: true, obligatorio: true }, { nombre: "buzon", titulo: "Buzón", tipo: "texto", obligatorio: true, ayuda: "la dirección del buzón" }] },
  { tipo: "google_servicio", nombre: "Google (cuenta de servicio)", descripcion: "Drive y Hojas de cálculo con una cuenta de servicio.",
    ayuda: "Pega el JSON de la llave de la cuenta de servicio y comparte con su correo las carpetas que debe ver.",
    campos: [{ nombre: "json", titulo: "JSON de la cuenta de servicio", tipo: "texto-largo", secreto: true, obligatorio: true }] },
  { tipo: "postgres", nombre: "PostgreSQL", descripcion: "Consultar (y, si se habilita, escribir) una base PostgreSQL.", ayuda: "Crea un usuario de solo lectura para consultas. IP privadas bloqueadas.",
    campos: [host(), puerto(5432), { nombre: "base", titulo: "Base de datos", tipo: "texto", obligatorio: true }, usuario(), clave(), { nombre: "ssl", titulo: "SSL", tipo: "booleano", defecto: true }] },
  { tipo: "mysql", nombre: "MySQL / MariaDB", descripcion: "Consultar (y, si se habilita, escribir) una base MySQL o MariaDB.", ayuda: "Crea un usuario de solo lectura para consultas. IP privadas bloqueadas.",
    campos: [host(), puerto(3306), { nombre: "base", titulo: "Base de datos", tipo: "texto", obligatorio: true }, usuario(), clave(), { nombre: "ssl", titulo: "SSL", tipo: "booleano", defecto: true }] },
  { tipo: "sqlite", nombre: "SQLite", descripcion: "Una base SQLite del espacio de archivos (/datos/archivos).", ayuda: "La ruta es relativa a /datos/archivos.",
    campos: [{ nombre: "ruta", titulo: "Ruta", tipo: "texto", obligatorio: true, ayuda: "p. ej. bases/inventario.db" }] },
  { tipo: "http_bearer", nombre: "API con token", descripcion: "Un token Bearer para el nodo http.", ayuda: "Solo se manda a los hosts que digas aquí.",
    campos: [{ nombre: "token", titulo: "Token", tipo: "texto", secreto: true, obligatorio: true }, hosts] },
  { tipo: "http_basico", nombre: "API con usuario y clave", descripcion: "Autenticación básica para el nodo http.", ayuda: "Solo se manda a los hosts que digas aquí.",
    campos: [usuario(), clave(), hosts] },
  { tipo: "webhook_teams", nombre: "Microsoft Teams (webhook)", descripcion: "Publicar mensajes en un canal de Teams (nodo mensaje_teams; escritura: hay que habilitarla).",
    ayuda: "En el canal: Flujos de trabajo › «Publicar en un canal cuando se recibe una solicitud de webhook» (o un webhook entrante) y pega aquí la URL. Quien tiene la URL puede publicar: es secreta.",
    campos: [{ nombre: "url", titulo: "URL del webhook", tipo: "texto", secreto: true, obligatorio: true, ayuda: "https://…webhook.office.com/… o https://…logic.azure.com/…" }] },
  { tipo: "webhook_slack", nombre: "Slack (webhook entrante)", descripcion: "Publicar mensajes en un canal de Slack (nodo mensaje_slack; escritura: hay que habilitarla).",
    ayuda: "En Slack: Aplicaciones › Webhooks entrantes › Agregar a un canal, y pega aquí la URL (https://hooks.slack.com/services/…). Es secreta.",
    campos: [{ nombre: "url", titulo: "URL del webhook", tipo: "texto", secreto: true, obligatorio: true, ayuda: "https://hooks.slack.com/services/…" }] },
  { tipo: "webhook_saliente", nombre: "Webhook saliente", descripcion: "Mandar JSON por POST a otro sistema (nodo webhook_saliente; escritura: hay que habilitarla).",
    ayuda: "Solo https a hosts públicos; nunca Positiva, SGDEA ni redes internas. El token, si lo hay, va como «Bearer» en Authorization o en el encabezado que digas.",
    campos: [{ nombre: "url", titulo: "URL", tipo: "texto", secreto: true, obligatorio: true, ayuda: "https://…" },
      { nombre: "token", titulo: "Token", tipo: "texto", secreto: true },
      { nombre: "encabezado", titulo: "Encabezado del token", tipo: "texto", defecto: "Authorization", ayuda: "p. ej. Authorization o X-Api-Key" }] },
  { tipo: "google_oauth", nombre: "Google (OAuth)", descripcion: "Gmail, Calendar, Drive, Docs, Hojas y Forms de una cuenta de Google, con «Conectar con Google».",
    ayuda: "En Google Cloud crea un cliente OAuth «Aplicación web» con la URI de redirección que se muestra aquí, pega su client_id y client_secret y pulsa «Conectar con Google». Nunca cuentas de Positiva.",
    campos: [{ nombre: "client_id", titulo: "Client ID", tipo: "texto", obligatorio: true, ayuda: "…apps.googleusercontent.com" },
      { nombre: "client_secret", titulo: "Client secret", tipo: "texto", secreto: true, obligatorio: true },
      { nombre: "scopes", titulo: "Permisos (alcances)", tipo: "texto", defecto: ALCANCES_GOOGLE, ayuda: "separados por espacio; los cortos (gmail.modify, calendar, drive…) se completan con https://www.googleapis.com/auth/" },
      ...CAMPOS_OAUTH] },
  { tipo: "microsoft_oauth", nombre: "Microsoft 365 (OAuth)", descripcion: "Outlook, calendario, OneDrive, SharePoint, Teams y Excel en línea de una cuenta de Microsoft 365, con «Conectar con Microsoft».",
    ayuda: "En Entra ID registra una aplicación (plataforma Web) con la URI de redirección que se muestra aquí y permisos delegados, crea un secreto y pulsa «Conectar con Microsoft». Nunca buzones de Positiva.",
    campos: [{ nombre: "tenant", titulo: "Tenant", tipo: "texto", defecto: "common", ayuda: "common, organizations o el id / dominio del inquilino" },
      { nombre: "client_id", titulo: "Client ID", tipo: "texto", obligatorio: true },
      { nombre: "client_secret", titulo: "Client secret", tipo: "texto", secreto: true, obligatorio: true },
      { nombre: "scopes", titulo: "Permisos (alcances)", tipo: "texto", defecto: ALCANCES_MICROSOFT, ayuda: "separados por espacio; offline_access se agrega siempre" },
      ...CAMPOS_OAUTH] },
  { tipo: "http_oauth2", nombre: "API con OAuth2 (cliente)", descripcion: "OAuth2 de credenciales de cliente para el nodo http: el token se pide y se renueva solo.",
    ayuda: "Solo se manda a los hosts que digas aquí. El token se pide a token_url con client_id y client_secret.",
    campos: [{ nombre: "token_url", titulo: "URL del token", tipo: "texto", obligatorio: true, ayuda: "https://…/oauth/token" },
      { nombre: "client_id", titulo: "Client ID", tipo: "texto", obligatorio: true }, { nombre: "client_secret", titulo: "Client secret", tipo: "texto", secreto: true, obligatorio: true },
      { nombre: "scope", titulo: "Alcance", tipo: "texto", ayuda: "opcional" }, hosts] },
  { tipo: "api_openapi", nombre: "API (OpenAPI)", descripcion: "Cualquier API con especificación OpenAPI o Swagger: sus operaciones quedan como pasos (nodo api_operacion).",
    ayuda: "Pega la URL de la especificación (JSON o YAML) o la especificación misma, y la autenticación. GET lee; lo demás escribe y necesita que una persona habilite la escritura.",
    campos: [{ nombre: "spec_url", titulo: "URL de la especificación", tipo: "texto", ayuda: "https://…/openapi.json (o pega la especificación abajo)" },
      { nombre: "spec", titulo: "Especificación (JSON o YAML)", tipo: "texto-largo" },
      { nombre: "base_url", titulo: "URL base", tipo: "texto", ayuda: "opcional: si no, la de «servers» de la especificación" },
      { nombre: "auth", titulo: "Autenticación", tipo: "opcion", opciones: ["ninguna", "api_key", "bearer", "basico", "oauth2"], defecto: "ninguna" },
      { nombre: "llave_nombre", titulo: "Nombre de la llave", tipo: "texto", defecto: "X-API-Key", ayuda: "api_key: el encabezado o parámetro" },
      { nombre: "llave_en", titulo: "La llave va en", tipo: "opcion", opciones: ["encabezado", "consulta"], defecto: "encabezado" },
      { nombre: "token", titulo: "Llave o token", tipo: "texto", secreto: true, ayuda: "api_key o bearer" },
      { nombre: "usuario", titulo: "Usuario", tipo: "texto", ayuda: "basico" }, { nombre: "clave", titulo: "Clave", tipo: "texto", secreto: true, ayuda: "basico" },
      { nombre: "token_url", titulo: "URL del token", tipo: "texto", ayuda: "oauth2 (credenciales de cliente)" },
      { nombre: "client_id", titulo: "Client ID", tipo: "texto", ayuda: "oauth2" }, { nombre: "client_secret", titulo: "Client secret", tipo: "texto", secreto: true, ayuda: "oauth2" },
      { nombre: "scope", titulo: "Alcance", tipo: "texto", ayuda: "oauth2, opcional" },
      { nombre: "red_privada", titulo: "Red privada", tipo: "booleano", defecto: false, ayuda: "aceptar IP privadas (10/8, 172.16/12, 192.168/16): solo si la crea una persona desde la web" }] },
  // ---- fase 2 (plan 5d): bases de datos, almacenamiento y mensajería transaccional (puente/especificacion_externos.py)
  { tipo: "sqlserver", nombre: "SQL Server", descripcion: "Consultar (y, si se habilita, escribir) una base SQL Server o Azure SQL (nodos sql_consulta y sql_ejecutar).",
    ayuda: "Crea un usuario de solo lectura para consultas. Azure SQL exige cifrado. Nunca servidores de Positiva.",
    campos: [host("p. ej. miservidor.database.windows.net"), puerto(1433), { nombre: "base", titulo: "Base de datos", tipo: "texto", obligatorio: true }, usuario(), clave(),
      { nombre: "cifrado", titulo: "Cifrado (TLS)", tipo: "booleano", defecto: true }] },
  { tipo: "oracle", nombre: "Oracle", descripcion: "Consultar (y, si se habilita, escribir) una base Oracle, sin el cliente de Oracle (nodos sql_consulta y sql_ejecutar).",
    ayuda: "Se conecta con el nombre de servicio (no el SID). Crea un usuario de solo lectura para consultas.",
    campos: [host(), puerto(1521), { nombre: "servicio", titulo: "Nombre de servicio", tipo: "texto", obligatorio: true, ayuda: "p. ej. ORCLPDB1 o el service_name de la base" },
      usuario(), clave(), { nombre: "tls", titulo: "TLS (tcps)", tipo: "booleano", defecto: false }] },
  { tipo: "mongodb", nombre: "MongoDB", descripcion: "Consultar (y, si se habilita, escribir) una base MongoDB (nodos mongo_consulta y mongo_escribir).",
    ayuda: "Pon el servidor o varios host:puerto separados por coma (la «Standard connection string» de Atlas); mongodb+srv no se admite. Crea un usuario de solo lectura para consultas.",
    campos: [host("p. ej. cluster0-shard-00-00.abcde.mongodb.net:27017, cluster0-shard-00-01.abcde.mongodb.net:27017"), puerto(27017),
      { nombre: "base", titulo: "Base de datos", tipo: "texto", obligatorio: true }, usuario(false), clave(false),
      { nombre: "auth_base", titulo: "Base de autenticación", tipo: "texto", defecto: "admin" }, { nombre: "tls", titulo: "TLS", tipo: "booleano", defecto: true },
      { nombre: "replica_set", titulo: "Replica set", tipo: "texto", ayuda: "opcional, p. ej. atlas-abc123-shard-0" }] },
  { tipo: "ftp", nombre: "Servidor FTP / FTPS", descripcion: "Listar, leer y (si se habilita la escritura) subir archivos por FTP o FTPS (nodo ftp_archivos).",
    ayuda: "Por defecto con TLS explícito (FTPS); «ninguno» solo si el servidor no tiene TLS (la clave viaja en claro). SFTP no está disponible. Nunca servidores de Positiva.",
    campos: [host("p. ej. ftp.proveedor.com"), puerto(21), usuario(false), clave(false),
      { nombre: "tls", titulo: "Cifrado", tipo: "opcion", opciones: ["explicito", "ninguno"], defecto: "explicito" },
      { nombre: "carpeta_base", titulo: "Carpeta base", tipo: "texto", defecto: "/", ayuda: "los nodos no pueden salir de esta carpeta" },
      { nombre: "red_privada", titulo: "Red privada", tipo: "booleano", defecto: false, ayuda: "aceptar IP privadas: solo si la crea una persona desde la web" }] },
  { tipo: "s3", nombre: "Almacenamiento S3 (S3, MinIO, GCS)", descripcion: "Listar, leer y (si se habilita la escritura) subir objetos a un bucket compatible con S3 (nodo s3_archivos).",
    ayuda: "Amazon S3: deja el endpoint vacío. MinIO: su URL https y estilo «ruta». Google Cloud Storage: endpoint https://storage.googleapis.com, región «auto» y una llave HMAC. Usa una llave limitada al bucket.",
    campos: [{ nombre: "access_key", titulo: "Access key", tipo: "texto", obligatorio: true }, { nombre: "secret_key", titulo: "Secret key", tipo: "texto", secreto: true, obligatorio: true },
      { nombre: "bucket", titulo: "Bucket", tipo: "texto", obligatorio: true }, { nombre: "region", titulo: "Región", tipo: "texto", defecto: "us-east-1" },
      { nombre: "endpoint", titulo: "Endpoint", tipo: "texto", ayuda: "https://… (vacío para Amazon S3)" },
      { nombre: "estilo", titulo: "Estilo de dirección", tipo: "opcion", opciones: ["virtual", "ruta"], defecto: "virtual", ayuda: "ruta para MinIO y compatibles sin subdominios" },
      { nombre: "prefijo", titulo: "Prefijo", tipo: "texto", ayuda: "opcional: los nodos no salen de esta «carpeta» del bucket" },
      { nombre: "red_privada", titulo: "Red privada", tipo: "booleano", defecto: false, ayuda: "aceptar IP privadas (MinIO interno): solo si la crea una persona desde la web" }] },
  { tipo: "telegram_bot", nombre: "Telegram (bot)", descripcion: "Enviar mensajes y archivos a chats, grupos o canales de Telegram (nodo telegram_mensaje; escritura: hay que habilitarla).",
    ayuda: "Crea el bot con @BotFather y pega su token. El bot solo escribe donde lo agregaron.",
    campos: [{ nombre: "token", titulo: "Token del bot", tipo: "texto", secreto: true, obligatorio: true, ayuda: "123456789:ABC…" },
      { nombre: "chat_id", titulo: "Chat por defecto", tipo: "texto", ayuda: "opcional: id del chat o @canal" }] },
  { tipo: "sendgrid", nombre: "SendGrid", descripcion: "Correo transaccional por SendGrid (nodo correo_transaccional; escritura: hay que habilitarla).",
    ayuda: "API key con permiso Mail Send y un remitente verificado. Nunca direcciones de Positiva.",
    campos: [{ nombre: "api_key", titulo: "API key", tipo: "texto", secreto: true, obligatorio: true, ayuda: "SG.…" },
      { nombre: "remitente", titulo: "Remitente", tipo: "texto", obligatorio: true, ayuda: "la dirección verificada en SendGrid" }, { nombre: "nombre_remitente", titulo: "Nombre del remitente", tipo: "texto" }] },
  { tipo: "mailgun", nombre: "Mailgun", descripcion: "Correo transaccional por Mailgun (nodo correo_transaccional; escritura: hay que habilitarla).",
    ayuda: "API key, el dominio de envío (mg.tuempresa.com) y su región. Nunca direcciones ni dominios de Positiva.",
    campos: [{ nombre: "api_key", titulo: "API key", tipo: "texto", secreto: true, obligatorio: true }, { nombre: "dominio", titulo: "Dominio", tipo: "texto", obligatorio: true, ayuda: "p. ej. mg.tuempresa.com" },
      { nombre: "region", titulo: "Región", tipo: "opcion", opciones: ["us", "eu"], defecto: "us" },
      { nombre: "remitente", titulo: "Remitente", tipo: "texto", obligatorio: true, ayuda: "p. ej. avisos@mg.tuempresa.com" }, { nombre: "nombre_remitente", titulo: "Nombre del remitente", tipo: "texto" }] },
  { tipo: "twilio", nombre: "Twilio (SMS)", descripcion: "Enviar SMS con Twilio (nodo sms_twilio; escritura: hay que habilitarla).",
    ayuda: "Account SID (AC…) y Auth Token de la consola de Twilio, y el número remitente (+…) o un Messaging Service (MG…).",
    campos: [{ nombre: "account_sid", titulo: "Account SID", tipo: "texto", obligatorio: true, ayuda: "AC…" }, { nombre: "auth_token", titulo: "Auth token", tipo: "texto", secreto: true, obligatorio: true },
      { nombre: "remitente", titulo: "Remitente", tipo: "texto", obligatorio: true, ayuda: "+15551234567 o MG…" }] },
  // Proveedores de IA (ia_<proveedor>): ver ia_credenciales.ts.
  ...TIPOS_CREDENCIAL_IA,
];

const NOMBRE = /^[a-z0-9][a-z0-9_.-]{0,62}$/;
const ID_FLUJO = /^[a-z][a-z0-9-]{1,40}$/;
/** Lo que nunca se acepta como destino de una credencial (el motor además lo bloquea al correr). */
const VETADO = (h: string) => /(^|\.)positiva\.gov\.co$/i.test(h) || /sgdea/i.test(h) || /^(localhost|metadata(\.google\.internal)?)$/i.test(h) || /\.internal$/i.test(h);
const CORREO_VETADO = (d: string) => /@([\w-]+\.)*positiva\.gov\.co$/i.test(d.trim());
/** Campos que se pueden mostrar en el manifiesto de un flujo (dónde se conecta), nunca secretos. */
const CAMPOS_DE_DESTINO = ["host", "puerto", "base", "buzon", "hosts", "ruta", "tenant", "remitente", "usuario", "base_url", "endpoint", "url", "cuenta", "spec_url", "token_url",
  "bucket", "region", "prefijo", "dominio", "servicio", "carpeta_base", "chat_id"];

interface Cifrado { iv: string; tag: string; datos: string }
interface Guardada {
  tipo: string;
  descripcion: string;
  alcance: string;
  permitir_escritura: boolean;
  creada: number;
  actualizada: number;
  usada: number | null;
  campos: string[];
  valores: Cifrado;
  /** La guardó una persona desde la web (X-Confirmado-Por-Persona: si) en su último cambio. */
  creada_por_persona?: boolean;
  /** OAuth: lo que se puede mostrar de la conexión (sin tokens), para no descifrar al listar. */
  conexion?: Conexion;
}

/** El estado visible de una conexión OAuth: nunca lleva tokens. */
export interface Conexion {
  conectada: boolean;
  cuenta?: string;
  alcances?: string[];
  conectada_en?: number;
  expira?: number;
}

export interface Resumen {
  nombre: string;
  tipo: string;
  campos: string[];
  permitir_escritura: boolean;
  alcance: string;
  descripcion: string;
  creada: number;
  actualizada: number;
  usada: number | null;
  creada_por_persona: boolean;
  conexion?: Conexion;
}

const ruta = () => join(config.datos, "credenciales.json");
let todas: Record<string, Guardada> = {};
let llave: Buffer | null = null;

/** La llave AES-256: de CLAVE_CREDENCIALES o del archivo .clave-credenciales (se crea con 0600). */
function laLlave(): Buffer {
  if (llave) return llave;
  const env = process.env.CLAVE_CREDENCIALES;
  if (env && env.trim()) {
    const b = Buffer.from(env.trim(), "base64");
    llave = b.length === 32 ? b : createHash("sha256").update(env.trim()).digest();
    return llave;
  }
  const archivo = join(config.datos, ".clave-credenciales");
  if (existsSync(archivo)) {
    llave = Buffer.from(readFileSync(archivo, "utf8").trim(), "base64");
  } else {
    mkdirSync(config.datos, { recursive: true });
    llave = randomBytes(32);
    writeFileSync(archivo, llave.toString("base64"), { encoding: "utf8", mode: 0o600 });
  }
  try {
    chmodSync(archivo, 0o600);
  } catch {
    /* Windows */
  }
  if (llave.length !== 32) throw new Error("la llave de credenciales no tiene 32 bytes");
  return llave;
}

export function cifrar(texto: string): Cifrado {
  const iv = randomBytes(12);
  const c = createCipheriv("aes-256-gcm", laLlave(), iv);
  const datos = Buffer.concat([c.update(texto, "utf8"), c.final()]);
  return { iv: iv.toString("base64"), tag: c.getAuthTag().toString("base64"), datos: datos.toString("base64") };
}

export function descifrar(x: Cifrado): string {
  const d = createDecipheriv("aes-256-gcm", laLlave(), Buffer.from(x.iv, "base64"));
  d.setAuthTag(Buffer.from(x.tag, "base64"));
  return Buffer.concat([d.update(Buffer.from(x.datos, "base64")), d.final()]).toString("utf8");
}

export async function cargar(): Promise<void> {
  laLlave();
  try {
    todas = JSON.parse(await readFile(ruta(), "utf8")) as Record<string, Guardada>;
  } catch {
    todas = {};
  }
}

let escribiendo: Promise<void> = Promise.resolve();

/** Escribe el archivo de a una vez (en fila) y de forma atómica. */
function persistir(): Promise<void> {
  const p = escribiendo.then(async () => {
    await mkdir(config.datos, { recursive: true });
    const tmp = `${ruta()}.${randomBytes(4).toString("hex")}.tmp`;
    await writeFile(tmp, JSON.stringify(todas, null, 2), { encoding: "utf8", mode: 0o600 });
    await rename(tmp, ruta());
  });
  escribiendo = p.catch(() => undefined);
  return p;
}

const resumen = (nombre: string, g: Guardada): Resumen => ({
  nombre, tipo: g.tipo, campos: g.campos, permitir_escritura: g.permitir_escritura, alcance: g.alcance, descripcion: g.descripcion,
  creada: g.creada, actualizada: g.actualizada, usada: g.usada, creada_por_persona: g.creada_por_persona === true,
  ...(TIPOS_OAUTH.has(g.tipo) ? { conexion: g.conexion ?? { conectada: false } } : {}),
});

/** Lo visible de una conexión OAuth a partir de sus valores (sin tokens). */
function conexionDe(v: Record<string, unknown>): Conexion {
  const conectada = typeof v.refresh_token === "string" && v.refresh_token !== "" || typeof v.access_token === "string" && v.access_token !== "";
  if (!conectada) return { conectada: false };
  return {
    conectada: true, ...(typeof v.cuenta === "string" && v.cuenta ? { cuenta: v.cuenta } : {}),
    alcances: String(v.alcances ?? "").split(/\s+/).filter(Boolean),
    ...(typeof v.conectada === "number" ? { conectada_en: v.conectada } : {}), ...(typeof v.expira === "number" ? { expira: v.expira } : {}),
  };
}

/** Las credenciales (sin valores). Con flujo: las que ese flujo puede usar (globales y las suyas). */
export function listar(flujo?: string): Resumen[] {
  return Object.entries(todas)
    .filter(([, g]) => !flujo || g.alcance === "global" || g.alcance === flujo)
    .map(([n, g]) => resumen(n, g))
    .sort((a, b) => a.nombre.localeCompare(b.nombre));
}

export function tipos(): TipoDeCredencial[] {
  return TIPOS_CREDENCIAL;
}

export function existe(nombre: string): boolean {
  return nombre in todas;
}

export type Resultado = { ok: true; credencial: Resumen } | { ok: false; error: string; codigo?: number };

/** Crea o reemplaza una credencial. Un campo secreto vacío conserva el valor anterior (si el tipo no cambia). */
export async function guardar(nombre: string, cuerpo: unknown, porPersona = false): Promise<Resultado> {
  if (!NOMBRE.test(nombre)) return { ok: false, error: "nombre inválido: minúsculas, números, -, _ y . (hasta 63)" };
  const b = (cuerpo ?? {}) as { tipo?: unknown; valores?: unknown; descripcion?: unknown; alcance?: unknown };
  const t = TIPOS_CREDENCIAL.find((x) => x.tipo === b.tipo);
  if (!t) return { ok: false, error: `tipo desconocido (válidos: ${TIPOS_CREDENCIAL.map((x) => x.tipo).join(", ")})` };
  if (!b.valores || typeof b.valores !== "object" || Array.isArray(b.valores)) return { ok: false, error: "valores tiene que ser un objeto {campo: valor}" };
  const alcance = b.alcance === undefined || b.alcance === null || b.alcance === "" ? (todas[nombre]?.alcance ?? "global") : String(b.alcance);
  if (alcance !== "global" && !ID_FLUJO.test(alcance)) return { ok: false, error: "alcance: «global» o el id de un flujo" };
  const entrada = b.valores as Record<string, unknown>;
  const ajenos = Object.keys(entrada).filter((k) => !t.campos.some((c) => c.nombre === k && !c.interno));
  if (ajenos.length) return { ok: false, error: `campos desconocidos para ${t.tipo}: ${ajenos.join(", ")}` };
  const previo = todas[nombre];
  const anteriores: Record<string, unknown> = previo && previo.tipo === t.tipo ? (JSON.parse(descifrar(previo.valores)) as Record<string, unknown>) : {};
  const valores: Record<string, unknown> = {};
  for (const c of t.campos) {
    if (c.interno) continue;  // los tokens y la cuenta los pone la conexión OAuth (abajo)
    let v = entrada[c.nombre];
    // Al editar, un campo que no viene conserva su valor (los secretos nunca se devuelven a la web).
    if ((v === undefined || (v === "" && c.secreto)) && anteriores[c.nombre] !== undefined) v = anteriores[c.nombre];
    if (v === undefined || v === "" || v === null) {
      if (c.obligatorio) return { ok: false, error: `falta ${c.nombre} (${c.titulo})` };
      if (c.defecto !== undefined) valores[c.nombre] = c.defecto;
      continue;
    }
    if (c.tipo === "entero") {
      const n = Number(v);
      if (!Number.isInteger(n) || n < 1 || n > 65535) return { ok: false, error: `${c.nombre} tiene que ser un entero entre 1 y 65535` };
      v = n;
    } else if (c.tipo === "booleano") {
      if (typeof v !== "boolean") v = ["true", "si", "sí", "1"].includes(String(v).trim().toLowerCase());
    } else {
      if (typeof v !== "string") v = typeof v === "object" ? JSON.stringify(v) : String(v);
      if ((v as string).length > 20000) return { ok: false, error: `${c.nombre} es demasiado largo` };
      if (c.opciones && !c.opciones.includes(v as string)) return { ok: false, error: `${c.nombre}: ${c.opciones.join(", ")}` };
    }
    valores[c.nombre] = v;
  }
  // Destinos vetados desde ya (el motor los vuelve a bloquear al correr).
  const hostDeUrl = (x: unknown): string | null => {
    if (typeof x !== "string" || !x.trim()) return null;
    try {
      return new URL(x.trim()).hostname.toLowerCase();
    } catch {
      return "";
    }
  };
  for (const k of ["spec_url", "base_url", "token_url", "endpoint"]) {
    const h = hostDeUrl(valores[k]);
    if (h === "") return { ok: false, error: `${k}: no es una URL válida` };
    if (h && (k === "spec_url" || k === "token_url" || k === "endpoint") && !String(valores[k]).trim().toLowerCase().startsWith("https://")) return { ok: false, error: `${k}: tiene que ser https://` };
  }
  // host admite varios «host:puerto» separados por coma (MongoDB); el dominio de Mailgun también se revisa
  const destinos = [...String(valores.host ?? "").split(/[,\s]+/).map((x) => x.replace(/:\d+$/, "")), ...String(valores.hosts ?? "").split(/[,\s]+/),
    ...["spec_url", "base_url", "token_url", "endpoint"].map((k) => hostDeUrl(valores[k])), ...(t.tipo === "mailgun" ? [valores.dominio] : [])]
    .filter((x): x is string => typeof x === "string" && x.trim() !== "");
  const malo = destinos.find((h) => VETADO(h.trim().toLowerCase()));
  if (malo) return { ok: false, error: `bloqueado por seguridad: ${malo} es un destino que esta plataforma nunca toca (Positiva, SGDEA, metadatos o interno)` };
  for (const k of ["buzon", "usuario", "remitente"]) {
    if (typeof valores[k] === "string" && CORREO_VETADO(valores[k] as string)) return { ok: false, error: `bloqueado por seguridad: ${String(valores[k])} es un buzón de Positiva` };
  }
  if (t.tipo.startsWith("webhook_")) {
    // La URL es el secreto: solo https, a un host público y (Teams, Slack) del servicio que dice el tipo.
    let u: URL;
    try {
      u = new URL(String(valores.url));
    } catch {
      return { ok: false, error: "url: no es una URL válida" };
    }
    const h = u.hostname.toLowerCase();
    if (u.protocol !== "https:") return { ok: false, error: "url: tiene que ser https://" };
    if (VETADO(h) || h === "localhost" || /^(\d+\.){3}\d+$/.test(h) || h.includes(":")) return { ok: false, error: `bloqueado por seguridad: ${h} es un destino que esta plataforma nunca toca` };
    const sufijos = t.tipo === "webhook_teams" ? ["webhook.office.com", "logic.azure.com", "powerplatform.com", "powerautomate.com"] : t.tipo === "webhook_slack" ? ["hooks.slack.com"] : [];
    if (sufijos.length && !sufijos.some((s) => h === s || h.endsWith(`.${s}`))) return { ok: false, error: `url: un ${t.nombre} va a ${sufijos.join(", ")}` };
  }
  if (t.tipo === "sqlite") {
    const r = String(valores.ruta).replace(/\\/g, "/");
    if (r.startsWith("/") || /^[A-Za-z]:/.test(r) || r.split("/").includes("..")) return { ok: false, error: "ruta: relativa a /datos/archivos, sin «..»" };
  }
  if (t.tipo === "google_servicio") {
    try {
      if ((JSON.parse(String(valores.json)) as { type?: string }).type !== "service_account") return { ok: false, error: "json: tiene que ser la llave de una cuenta de servicio" };
    } catch {
      return { ok: false, error: "json: no es un JSON válido" };
    }
  }
  if (t.tipo === "api_openapi") {
    if (!valores.spec_url && !valores.spec) return { ok: false, error: "falta spec_url o spec (la URL de la especificación o la especificación pegada)" };
    const auth = String(valores.auth ?? "ninguna");
    if ((auth === "api_key" || auth === "bearer") && !valores.token) return { ok: false, error: `con autenticación ${auth} falta token (la llave o el token)` };
    if (auth === "basico" && (!valores.usuario || !valores.clave)) return { ok: false, error: "con autenticación basico faltan usuario y clave" };
    if (auth === "oauth2" && (!valores.token_url || !valores.client_id || !valores.client_secret)) return { ok: false, error: "con autenticación oauth2 faltan token_url, client_id y client_secret" };
    if (valores.red_privada === true && !porPersona) return { ok: false, error: "red_privada: solo una persona puede permitir IP privadas, desde la web (X-Confirmado-Por-Persona: si)" };
  }
  if ((t.tipo === "ftp" || t.tipo === "s3") && valores.red_privada === true && !porPersona) {
    return { ok: false, error: "red_privada: solo una persona puede permitir IP privadas, desde la web (X-Confirmado-Por-Persona: si)" };
  }
  if (t.tipo === "s3" && !/^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$/.test(String(valores.bucket))) return { ok: false, error: "bucket: minúsculas, números, punto y guion (3 a 63)" };
  if (t.tipo === "twilio" && !/^AC[0-9a-fA-F]{32}$/.test(String(valores.account_sid))) return { ok: false, error: "account_sid: empieza por AC y tiene 34 caracteres" };
  if (t.tipo === "telegram_bot" && !/^\d{5,15}:[A-Za-z0-9_-]{30,60}$/.test(String(valores.token))) return { ok: false, error: "token: el de @BotFather, con la forma 123456789:ABC…" };
  if (t.tipo === "mongodb" && /^mongodb(\+srv)?:/i.test(String(valores.host))) return { ok: false, error: "host: el servidor o varios host:puerto separados por coma, no una URI mongodb:// (mongodb+srv no se admite)" };
  if (TIPOS_OAUTH.has(t.tipo)) {
    // Los tokens siguen mientras no cambie la aplicación ni los permisos; si cambian, hay que volver a conectar.
    const misma = ["client_id", "client_secret", "tenant", "scopes"].every((k) => JSON.stringify(anteriores[k]) === JSON.stringify(valores[k]));
    if (misma) for (const c of t.campos) if (c.interno && anteriores[c.nombre] !== undefined) valores[c.nombre] = anteriores[c.nombre];
  }
  const ahora = Date.now();
  todas[nombre] = {
    tipo: t.tipo, descripcion: typeof b.descripcion === "string" ? b.descripcion.slice(0, 300) : (previo?.descripcion ?? ""), alcance,
    // Si cambia cualquier valor, la escritura se apaga y una persona tiene que volver a habilitarla.
    permitir_escritura: Boolean(previo?.permitir_escritura) && previo?.tipo === t.tipo && JSON.stringify(anteriores) === JSON.stringify(valores), creada: previo?.creada ?? ahora, actualizada: ahora, usada: previo?.usada ?? null,
    campos: Object.keys(valores).filter((k) => !t.campos.find((c) => c.nombre === k)?.interno), valores: cifrar(JSON.stringify(valores)),
    creada_por_persona: porPersona,
    ...(TIPOS_OAUTH.has(t.tipo) ? { conexion: conexionDe(valores) } : {}),
  };
  await persistir();
  return { ok: true, credencial: resumen(nombre, todas[nombre]!) };
}

/** Los valores descifrados de una credencial (para el módulo OAuth y las pruebas de conexión; nunca salen por la API). */
export function valoresDe(nombre: string): { tipo: string; valores: Record<string, unknown>; alcance: string; creada_por_persona: boolean } | null {
  const g = todas[nombre];
  if (!g) return null;
  return { tipo: g.tipo, valores: JSON.parse(descifrar(g.valores)) as Record<string, unknown>, alcance: g.alcance, creada_por_persona: g.creada_por_persona === true };
}

/** Los nombres de las credenciales de unos tipos (el refresco automático de OAuth). */
export function nombresDeTipo(tipos: Set<string>): string[] {
  return Object.entries(todas).filter(([, g]) => tipos.has(g.tipo)).map(([n]) => n);
}

/**
 * Cambia los campos internos de una conexión OAuth (tokens, cuenta, permisos) sin tocar lo demás. `null` borra el campo.
 * Con `apagarEscritura` la escritura vuelve a solo lectura (al desconectar o al cambiar de cuenta).
 */
export async function actualizarInternos(nombre: string, cambios: Record<string, unknown>, apagarEscritura = false): Promise<Resultado> {
  const g = todas[nombre];
  if (!g) return { ok: false, error: "esa credencial no existe", codigo: 404 };
  const t = TIPOS_CREDENCIAL.find((x) => x.tipo === g.tipo);
  const internos = new Set((t?.campos ?? []).filter((c) => c.interno).map((c) => c.nombre));
  const v = JSON.parse(descifrar(g.valores)) as Record<string, unknown>;
  for (const [k, x] of Object.entries(cambios)) {
    if (!internos.has(k)) continue;
    if (x === null || x === undefined) delete v[k];
    else v[k] = x;
  }
  g.valores = cifrar(JSON.stringify(v));
  g.conexion = conexionDe(v);
  if (apagarEscritura) g.permitir_escritura = false;
  g.actualizada = Date.now();
  await persistir();
  return { ok: true, credencial: resumen(nombre, g) };
}

export async function permitirEscritura(nombre: string, permitir: boolean): Promise<Resultado> {
  const g = todas[nombre];
  if (!g) return { ok: false, error: "esa credencial no existe", codigo: 404 };
  g.permitir_escritura = permitir;
  g.actualizada = Date.now();
  await persistir();
  return { ok: true, credencial: resumen(nombre, g) };
}

export async function borrar(nombre: string): Promise<boolean> {
  if (!todas[nombre]) return false;
  delete todas[nombre];
  await persistir();
  return true;
}

export interface ParaCorrida {
  tipo: string;
  valores: Record<string, unknown>;
  secretos: string[];
  permitir_escritura: boolean;
  /** La guardó una persona desde la web: el motor solo acepta IP privadas (red_privada) si es así. */
  creada_por_persona: boolean;
}

/** Lo que recibe el trabajador: solo las credenciales pedidas que el flujo puede usar (globales o suyas). Marca su uso. */
export function paraCorrida(flujo: string, nombres: Iterable<string>): Record<string, ParaCorrida> {
  const r: Record<string, ParaCorrida> = {};
  let cambio = false;
  for (const n of new Set(nombres)) {
    const g = todas[n];
    if (!g || (g.alcance !== "global" && g.alcance !== flujo)) continue;
    const t = TIPOS_CREDENCIAL.find((x) => x.tipo === g.tipo);
    r[n] = { tipo: g.tipo, valores: JSON.parse(descifrar(g.valores)) as Record<string, unknown>, secretos: (t?.campos ?? []).filter((c) => c.secreto).map((c) => c.nombre), permitir_escritura: g.permitir_escritura, creada_por_persona: g.creada_por_persona === true };
    g.usada = Date.now();
    cambio = true;
  }
  if (cambio) void persistir().catch(() => undefined);
  return r;
}

/** Para el manifiesto: tipo, alcance, si escribe y a dónde se conecta (sin ningún secreto). */
export function destino(nombre: string, flujo: string): { existe: boolean; usable: boolean; tipo?: string; alcance?: string; permitir_escritura?: boolean; destino?: Record<string, unknown> } {
  const g = todas[nombre];
  if (!g) return { existe: false, usable: false };
  const t = TIPOS_CREDENCIAL.find((x) => x.tipo === g.tipo);
  const valores = JSON.parse(descifrar(g.valores)) as Record<string, unknown>;
  const visibles = Object.fromEntries(Object.entries(valores).filter(([k]) => CAMPOS_DE_DESTINO.includes(k) && !t?.campos.find((c) => c.nombre === k)?.secreto));
  return { existe: true, usable: g.alcance === "global" || g.alcance === flujo, tipo: g.tipo, alcance: g.alcance, permitir_escritura: g.permitir_escritura, destino: visibles };
}
