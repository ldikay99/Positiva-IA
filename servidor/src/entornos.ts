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
 *   las credenciales de los jobs, las rutas o los límites de la plataforma).
 */

export const NOMBRE = /^[A-Z][A-Z0-9_]{0,63}$/;
/** Lo que un flujo nunca puede poner en su entorno: lo maneja la plataforma. */
export const PROTEGIDAS = /^(MODO_TEST|DEDUP_EN_PRUEBAS|SUPABASE_\w*|MICROSOFT_\w*|OUTLOOK_\w*|SGDEA_\w*|REDIS_\w*|PQRD_\w*|TUTELAS_\w*|GOOGLE_\w*|GCP_\w*|PROYECTO_SECRETOS|PATH|HOME|USER|SHELL|PYTHON\w*|NODE_\w*|NPM_\w*|LD_\w*|RUTA_\w*|CLAVE_CREDENCIALES|HTTP_HOSTS_PERMITIDOS|HTTPS?_PROXY|NO_PROXY|PUERTO|HOST|TRABAJADORES_MAX|TESSDATA_PREFIX|API_INTERNA|NIVEL_LOG|SSL_\w*|REQUESTS_CA_BUNDLE|CURL_\w*)$/;

interface Guardada {
  secreto: boolean;
  valor?: string;
  cifrado?: { iv: string; tag: string; datos: string };
  descripcion: string;
  actualizado: number;
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
  descripcion: g.descripcion, actualizado: g.actualizado,
});

export async function listar(flujo: string): Promise<Vista[]> {
  const a = await leer(flujo);
  return Object.entries(a.variables).map(([n, g]) => vista(n, g)).sort((x, y) => x.nombre.localeCompare(y.nombre));
}

export async function nombres(flujo: string): Promise<string[]> {
  return Object.keys((await leer(flujo)).variables);
}

export type Resultado = { ok: true; variable: Vista } | { ok: false; error: string; codigo: number };

export async function poner(flujo: string, nombre: string, cuerpo: unknown, confirmado: boolean): Promise<Resultado> {
  if (!NOMBRE.test(nombre)) return { ok: false, codigo: 400, error: "nombre inválido: MAYÚSCULAS, números y _ (empieza por letra, hasta 64)" };
  if (PROTEGIDAS.test(nombre)) return { ok: false, codigo: 400, error: `${nombre} es una variable que maneja la plataforma: un flujo no la puede cambiar` };
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
  if (!NOMBRE.test(nombre) || PROTEGIDAS.test(nombre)) return;
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
    if (PROTEGIDAS.test(n)) continue;
    if (g.secreto) {
      if (!g.cifrado) continue;
      valores[n] = descifrar(g.cifrado);
      secretos.push(n);
    } else if (g.valor !== undefined) valores[n] = g.valor;
  }
  return { valores, secretos };
}

/** El entorno con que arrancan los trabajadores de un flujo de código (sin nombres protegidos). */
export function paraProceso(flujo: string): Record<string, string> {
  const r = paraCorrida(flujo).valores;
  for (const k of Object.keys(r)) if (!NOMBRE.test(k)) delete r[k];
  return r;
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
    if (!NOMBRE.test(k) || PROTEGIDAS.test(k) || a.variables[k]) continue;
    a.variables[k] = { secreto: false, valor: v, descripcion: "migrada desde la configuración global", actualizado: Date.now() };
    n++;
  }
  a.migrado_desde_configuracion = Date.now();
  await escribir(flujo, a);
  return n;
}
