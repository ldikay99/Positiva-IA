import { mkdir, readdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { config } from "./config.ts";
import { cifrar, descifrar } from "./credenciales.ts";

/**
 * Variables de entorno POR FLUJO: <estado>/entornos/<flujo>.json.
 *
 * - Cada una es {nombre (MAYÚSCULAS_CON_GUION_BAJO), valor, secreto, descripcion, actualizado}.
 * - Las secretas se guardan cifradas (AES-256-GCM, la misma llave de las
 *   credenciales) y ninguna API devuelve su valor: solo {nombre, secreto: true, definido}.
 * - Crear o cambiar una variable secreta (o volver pública una secreta) exige el
 *   encabezado X-Confirmado-Por-Persona: si; Vera puede poner las no secretas.
 * - Los flujos declarativos las leen en las plantillas como {{entorno.NOMBRE}} (los
 *   valores llegan solo a la corrida y las secretas salen enmascaradas). Los flujos
 *   de código arrancan sus trabajadores con su entorno encima del de la plataforma.
 * - Nunca se aceptan nombres protegidos (los que ponen en modo prueba al motor,
 *   la compuerta de radicación, las rutas o los límites de la plataforma).
 * - Los flujos de código (pqrd, tutelas) aceptan además EXACTAMENTE los nombres de
 *   su job (`NOMBRES_DEL_JOB`), aunque choquen con la lista protegida: así su
 *   entorno es el mismo del job. Solo los de tutelas llevan guiones (TUTELAS-…),
 *   como en el job; ningún otro nombre puede llevarlos.
 */

export const NOMBRE = /^[A-Z][A-Z0-9_]{0,63}$/;
/** Lo que un flujo nunca puede poner en su entorno: lo maneja la plataforma. */
export const PROTEGIDAS = /^(MODO_TEST|DEDUP_EN_PRUEBAS|SUPABASE_\w*|MICROSOFT_\w*|OUTLOOK_\w*|SGDEA_\w*|REDIS_\w*|PQRD_\w*|TUTELAS_\w*|GOOGLE_\w*|GCP_\w*|PROYECTO_SECRETOS|PATH|HOME|USER|SHELL|PYTHON\w*|NODE_\w*|NPM_\w*|LD_\w*|RUTA_\w*|CLAVE_CREDENCIALES|HTTP_HOSTS_PERMITIDOS|HTTPS?_PROXY|NO_PROXY|PUERTO|HOST|TRABAJADORES_MAX|TESSDATA_PREFIX|API_INTERNA|NIVEL_LOG|SSL_\w*|REQUESTS_CA_BUNDLE|CURL_\w*|DATABASE_\w*)$/;
/**
 * Lo que NINGÚN flujo puede poner, ni siquiera un flujo de código con los nombres de
 * su job: el modo prueba, la compuerta y su segunda llave, y lo interno de la plataforma.
 */
export const PROHIBIDAS_SIEMPRE = /^(MODO_TEST|MODO_UAT|DEDUP_EN_PRUEBAS|RADICACION_REAL|PERMITIR_RADICACION_REAL|SECRETOS_DEL_FLUJO|PROYECTO_SECRETOS|PATH|HOME|USER|SHELL|PYTHON\w*|NODE_\w*|NPM_\w*|LD_\w*|RUTA_\w*|CLAVE_CREDENCIALES|HTTP_HOSTS_PERMITIDOS|HTTPS?_PROXY|NO_PROXY|PUERTO|HOST|TRABAJADORES_MAX|TESSDATA_PREFIX|API_INTERNA|NIVEL_LOG|SSL_\w*|REQUESTS_CA_BUNDLE|CURL_\w*|GOOGLE_APPLICATION_CREDENTIALS|TEST_OUTPUT_DIR)$/;

/**
 * Los nombres de cada job, verificados en su configuración (`gcloud run jobs describe`,
 * solo nombres) y en el código de su motor (`motor/config/settings.py`,
 * `motores/tutelas/Tutelas/config.py`). Es la lista de `produccion.ts`, desplegada.
 */
export const NOMBRES_DEL_JOB: Record<string, ReadonlySet<string>> = {
  pqrd: new Set([
    "MICROSOFT_TENANT_ID", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET",
    "OUTLOOK_FOLDER_ID", "OUTLOOK_REFRESH_TOKEN", "OUTLOOK_USER_EMAIL",
    "OUTLOOK_FOLDER_DUPLICADOS", "OUTLOOK_FOLDER_ERROR_RADICAR", "OUTLOOK_FOLDER_RADICACION_EXITOSA",
    "OUTLOOK_FOLDER_REENVIADO_OTRA_AREA", "OUTLOOK_FOLDER_REVISAR_MANUAL",
    "REDIS_PASSWORD", "REDIS_HOST", "REDIS_PORT",
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
    "SGDEA_CLIENT_SECRET", "SGDEA_PASSWORD", "SGDEA_TOKEN_URL", "SGDEA_CLIENT_ID", "SGDEA_USERNAME",
    "OPENAI_API_KEY", "GOOGLE_API_KEY", "GCP_PROJECT", "GCP_LOCATION", "CORREO_DE_AVISOS",
  ]),
  tutelas: new Set([
    "TUTELAS-MICROSOFT-TENANT", "TUTELAS-MICROSOFT-CLIENT-ID", "TUTELAS-MICROSOFT-CLIENT-SECRET",
    "TUTELAS-MICROSOFT-USERNAME", "TUTELAS-MICROSOFT-PASSWORD", "TUTELAS-MICROSOFT-GRAPH-URL",
    "TUTELAS-OUTLOOK-FOLDER-ENTRADA", "TUTELAS-OUTLOOK-FOLDER-REVISION-MANUAL", "TUTELAS-OUTLOOK-FOLDER-RADICACION-EXITOSA",
    "TUTELAS-REDIS-HOST", "TUTELAS-REDIS-PORT", "TUTELAS-REDIS-USERNAME", "TUTELAS-REDIS-PASSWORD",
    "TUTELAS-SGDEA-TOKEN-URL", "TUTELAS-SGDEA-API-URL", "TUTELAS-SGDEA-CLIENT-ID", "TUTELAS-SGDEA-USERNAME",
    "TUTELAS-SGDEA-CLIENT-SECRET", "TUTELAS-SGDEA-PASSWORD",
    "DATABASE_HOST", "DATABASE_PORT", "DATABASE_PASSWORD", "TUTELAS-DB-NAME", "TUTELAS-DB-USER", "TUTELAS-DATABASE-PASSWORD",
    "TUTELAS-GCP-PROJECT", "TUTELAS-GCP-LOCATION", "TUTELAS-GEMINI-MODEL", "TUTELAS-RADICAR-ENLACE-CON-ADJUNTOS",
  ]),
};

/**
 * De qué job es instancia un flujo de código (las copias de pqrd y tutelas aceptan los
 * nombres del job de su original). Lo fija el servidor con el registro de flujos.
 */
export const enlace = { baseDe: (flujo: string): string => flujo };
const delJob = (flujo: string) => NOMBRES_DEL_JOB[enlace.baseDe(flujo)];

/** Por qué un nombre no vale en el entorno de ese flujo, o null si vale. */
export function problemaDeNombre(flujo: string, nombre: string): string | null {
  if (PROHIBIDAS_SIEMPRE.test(nombre)) return `${nombre} es una variable que maneja la plataforma: ningún flujo la puede cambiar`;
  if (delJob(flujo)?.has(nombre)) return null;
  if (!NOMBRE.test(nombre)) return "nombre inválido: MAYÚSCULAS, números y _ (empieza por letra, hasta 64)";
  if (PROTEGIDAS.test(nombre)) return `${nombre} es una variable que maneja la plataforma: un flujo no la puede cambiar`;
  return null;
}
export const nombreValido = (flujo: string, nombre: string) => problemaDeNombre(flujo, nombre) === null;

interface Guardada {
  secreto: boolean;
  valor?: string;
  cifrado?: { iv: string; tag: string; datos: string };
  descripcion: string;
  actualizado: number;
  /** De qué flujo se copió (al clonar): el valor es el mismo del original hasta que una persona lo cambie. */
  copiado_de?: string;
}
interface Archivo {
  variables: Record<string, Guardada>;
  migrado_desde_configuracion?: number;
}

export interface Vista {
  nombre: string;
  secreto: boolean;
  definido: boolean;
  valor?: string;
  descripcion: string;
  actualizado: number;
  copiado_de?: string;
}

const carpeta = () => join(config.datos, "entornos");
const ID = /^[a-z][a-z0-9-]{1,40}$/;
const cache = new Map<string, Archivo>();

async function leer(flujo: string): Promise<Archivo> {
  if (!ID.test(flujo)) throw new Error("id de flujo inválido");
  const c = cache.get(flujo);
  if (c) return c;
  let a: Archivo;
  try {
    a = JSON.parse(await readFile(join(carpeta(), `${flujo}.json`), "utf8")) as Archivo;
    a.variables ??= {};
  } catch {
    a = { variables: {} };
  }
  cache.set(flujo, a);
  return a;
}

let escribiendo: Promise<void> = Promise.resolve();

/** Escribe en fila y de forma atómica (dos cambios seguidos no se pisan). */
function escribir(flujo: string, a: Archivo): Promise<void> {
  cache.set(flujo, a);
  const p = escribiendo.then(async () => {
    await mkdir(carpeta(), { recursive: true });
    const ruta = join(carpeta(), `${flujo}.json`);
    const tmp = `${ruta}.${Date.now()}-${Math.random().toString(36).slice(2, 8)}.tmp`;
    await writeFile(tmp, JSON.stringify(a, null, 2), { encoding: "utf8", mode: 0o600 });
    await rename(tmp, ruta);
  });
  escribiendo = p.catch(() => undefined);
  return p;
}

const vista = (nombre: string, g: Guardada): Vista => ({
  nombre, secreto: g.secreto, definido: g.secreto ? !!g.cifrado : g.valor !== undefined, ...(g.secreto ? {} : { valor: g.valor ?? "" }),
  descripcion: g.descripcion, actualizado: g.actualizado, ...(g.copiado_de ? { copiado_de: g.copiado_de } : {}),
});

/**
 * La copia de un flujo de CÓDIGO (otra instancia del mismo job, en esta misma plataforma):
 * todas sus variables, las secretas tal cual cifradas (nunca se descifran para copiarlas),
 * marcadas con copiado_de. Se cambian en la copia sin tocar el original.
 */
export async function copiar(desde: string, hacia: string): Promise<number> {
  const a = await leer(desde);
  const b = await leer(hacia);
  let n = 0;
  for (const [k, g] of Object.entries(a.variables)) {
    if (b.variables[k] || !nombreValido(hacia, k)) continue;
    b.variables[k] = { ...structuredClone(g), copiado_de: desde, actualizado: Date.now() };
    n++;
  }
  await escribir(hacia, b);
  return n;
}

export async function listar(flujo: string): Promise<Vista[]> {
  const a = await leer(flujo);
  return Object.entries(a.variables).map(([n, g]) => vista(n, g)).sort((x, y) => x.nombre.localeCompare(y.nombre));
}

export async function nombres(flujo: string): Promise<string[]> {
  return Object.keys((await leer(flujo)).variables);
}

export type Resultado = { ok: true; variable: Vista } | { ok: false; error: string; codigo: number };

export async function poner(flujo: string, nombre: string, cuerpo: unknown, confirmado: boolean): Promise<Resultado> {
  const problema = problemaDeNombre(flujo, nombre);
  if (problema) return { ok: false, codigo: 400, error: problema };
  const b = (cuerpo ?? {}) as { valor?: unknown; secreto?: unknown; descripcion?: unknown };
  if (b.valor === undefined || b.valor === null || (typeof b.valor !== "string" && typeof b.valor !== "number" && typeof b.valor !== "boolean"))
    return { ok: false, codigo: 400, error: "falta valor (texto)" };
  const valor = String(b.valor);
  if (valor.length > 20000) return { ok: false, codigo: 400, error: "el valor pasa de 20.000 caracteres" };
  const a = await leer(flujo);
  const previo = a.variables[nombre];
  const secreto = typeof b.secreto === "boolean" ? b.secreto : (previo?.secreto ?? false);
  // Un secreto (o volver pública una que era secreta) solo lo pone una persona.
  if ((secreto || previo?.secreto) && !confirmado)
    return { ok: false, codigo: 403, error: "crear o cambiar una variable secreta requiere la confirmación de una persona (X-Confirmado-Por-Persona: si)" };
  // Las variables del job (aunque no sean secretas) deciden a dónde lee y escribe el flujo: también las pone una persona.
  if (delJob(flujo)?.has(nombre) && !confirmado)
    return { ok: false, codigo: 403, error: `${nombre} es una variable del job: la pone una persona con confirmación (X-Confirmado-Por-Persona: si)` };
  const g: Guardada = {
    secreto, descripcion: typeof b.descripcion === "string" ? b.descripcion.slice(0, 300) : (previo?.descripcion ?? ""), actualizado: Date.now(),
    ...(secreto ? { cifrado: cifrar(valor) } : { valor }),
  };
  a.variables[nombre] = g;
  await escribir(flujo, a);
  return { ok: true, variable: vista(nombre, g) };
}

/** Declara una variable secreta SIN valor (definido: false), p. ej. en la copia de un flujo: el valor lo pone una persona. */
export async function declararSecreto(flujo: string, nombre: string, descripcion = ""): Promise<void> {
  if (!nombreValido(flujo, nombre)) return;
  const a = await leer(flujo);
  if (a.variables[nombre]) return;
  a.variables[nombre] = { secreto: true, descripcion: descripcion.slice(0, 300), actualizado: Date.now() };
  await escribir(flujo, a);
}

export async function borrar(flujo: string, nombre: string): Promise<boolean> {
  const a = await leer(flujo);
  if (!a.variables[nombre]) return false;
  delete a.variables[nombre];
  await escribir(flujo, a);
  return true;
}

export async function borrarTodo(flujo: string): Promise<void> {
  cache.delete(flujo);
  await unlink(join(carpeta(), `${flujo}.json`)).catch(() => undefined);
}

/** Los valores para la corrida (las secretas descifradas) y qué nombres son secretos (para enmascararlos). */
export function paraCorrida(flujo: string): { valores: Record<string, string>; secretos: string[] } {
  const a = cache.get(flujo) ?? { variables: {} };  // precargar() deja todos en la caché y poner() la mantiene
  const valores: Record<string, string> = {};
  const secretos: string[] = [];
  for (const [n, g] of Object.entries(a.variables)) {
    if (!nombreValido(flujo, n)) continue;
    if (g.secreto) {
      if (!g.cifrado) continue;
      valores[n] = descifrar(g.cifrado);
      secretos.push(n);
    } else if (g.valor !== undefined) valores[n] = g.valor;
  }
  return { valores, secretos };
}

/**
 * El entorno con que arrancan los trabajadores de un flujo de código: sus variables
 * válidas (los nombres del job incluidos) y, en SECRETOS_DEL_FLUJO, los NOMBRES de
 * las secretas (nunca sus valores), para que el trabajador también las enmascare.
 */
export function paraProceso(flujo: string): Record<string, string> {
  const { valores, secretos } = paraCorrida(flujo);
  for (const k of Object.keys(valores)) if (!nombreValido(flujo, k)) delete valores[k];
  return { ...valores, SECRETOS_DEL_FLUJO: secretos.join(",") };
}

/** Los VALORES secretos de un flujo, para enmascararlos en todo lo que el servidor reenvía o guarda. */
export function valoresSecretos(flujo: string): string[] {
  const { valores, secretos } = paraCorrida(flujo);
  return secretos.map((n) => valores[n] ?? "").filter((v) => v.length > 0);
}

/** Los nombres desde la caché (síncrono). */
export function nombresDeCache(flujo: string): string[] {
  return Object.keys(cache.get(flujo)?.variables ?? {});
}

export async function precargar(): Promise<void> {
  for (const a of (await readdir(carpeta()).catch(() => [])).filter((x) => x.endsWith(".json"))) {
    const id = a.slice(0, -5);
    if (ID.test(id)) await leer(id);
  }
}

/**
 * Una sola vez: el entorno global de antes (configuracion.entorno) pasa a ser el
 * entorno del flujo pqrd. El comportamiento queda igual (pqrd sigue recibiendo
 * esos valores en cada orden).
 */
export async function migrarDesdeConfiguracion(flujo: string, global: Record<string, string>): Promise<number> {
  const a = await leer(flujo);
  if (a.migrado_desde_configuracion) return 0;
  let n = 0;
  for (const [k, v] of Object.entries(global)) {
    if (!NOMBRE.test(k) || PROTEGIDAS.test(k) || PROHIBIDAS_SIEMPRE.test(k) || a.variables[k]) continue;
    a.variables[k] = { secreto: false, valor: v, descripcion: "migrada desde la configuración global", actualizado: Date.now() };
    n++;
  }
  a.migrado_desde_configuracion = Date.now();
  await escribir(flujo, a);
  return n;
}
