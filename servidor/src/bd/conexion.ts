import { AsyncLocalStorage } from "node:async_hooks";
import { readFileSync } from "node:fs";
import pg from "pg";

/**
 * La conexión a PostgreSQL (local en Docker o Cloud SQL) y el aislamiento por organización.
 *
 * - Sin `DATABASE_URL` todo sigue en archivos, como siempre: `activa()` es false y nadie abre conexiones.
 * - Con `DATABASE_URL` el estado de la plataforma vive en la base (ver docs/MODELO_DE_DATOS.md). La clave puede ir
 *   aparte en `DATABASE_PASSWORD_FILE` (un secreto de Docker o un archivo montado), nunca en el repositorio.
 * - Cada operación corre en una transacción con `set_config('vera.organizacion_id', <id>, true)` (un SET LOCAL):
 *   las políticas de Row Level Security solo dejan ver y escribir las filas de esa organización, aunque la
 *   consulta no filtre. La organización sale del contexto (AsyncLocalStorage, por pedido) o, mientras no haya
 *   inicio de sesión, de la organización del despliegue (`VERA_ORGANIZACION`, por slug).
 */

export type Cliente = pg.PoolClient;

/** Lo que nunca debe llegar a un proceso de Python: la base y las llaves son solo del servidor. */
export const VARIABLES_DEL_SERVIDOR = ["DATABASE_URL", "DATABASE_PASSWORD_FILE", "DATABASE_URL_PRUEBAS", "VERA_KEK_LOCAL", "VERA_KEK_LOCAL_ARCHIVO", "VERA_KEK_KMS",
  "BD_URL_MIGRADOR", "BD_CLAVE_MIGRADOR_ARCHIVO"] as const;

const retenidas: Partial<Record<string, string>> = {};
let retirado = false;

/** Una variable de la base o de las llaves (del entorno, o de lo que se retiró de él al arrancar). */
export const variable = (nombre: (typeof VARIABLES_DEL_SERVIDOR)[number]): string | undefined => (retirado ? retenidas[nombre] : process.env[nombre]);

/**
 * El servidor arranca muchos procesos de Python (trabajadores, validación, sondeos, Vera) con su mismo entorno:
 * al arrancar se guardan estas variables aquí y se quitan de process.env, así ningún hijo las hereda.
 */
export function retirarDelEntorno(): void {
  for (const k of VARIABLES_DEL_SERVIDOR) {
    if (process.env[k] !== undefined) retenidas[k] = process.env[k];
    delete process.env[k];
  }
  retirado = true;
}

export const activa = (): boolean => Boolean(variable("DATABASE_URL")?.trim());

export function sinVariablesDelServidor(entorno: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const r = { ...entorno };
  for (const k of VARIABLES_DEL_SERVIDOR) delete r[k];
  return r;
}

/** Las opciones del pool a partir de una URL (y la clave de un archivo, si viene aparte). */
export function opciones(url: string, archivoClave?: string): pg.PoolConfig {
  // pg da prioridad a lo que trae la URL sobre las demás opciones: la clave del archivo va dentro de la URL.
  let cadena = url;
  if (archivoClave) {
    const u = new URL(url);
    u.password = encodeURIComponent(readFileSync(archivoClave, "utf8").trim());
    cadena = u.toString();
  }
  const o: pg.PoolConfig = {
    connectionString: cadena,
    max: Number(process.env.BD_CONEXIONES_MAX ?? 10),
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: Number(process.env.BD_ESPERA_CONEXION_MS ?? 10_000),
    application_name: "vera",
    // Ninguna consulta de la plataforma tarda tanto: si pasa, se corta en la base y no deja bloqueos.
    statement_timeout: Number(process.env.BD_TOPE_CONSULTA_MS ?? 60_000),
  };
  return o;
}

let pool: pg.Pool | null = null;
let predeterminada: string | null = null;

export function piscina(): pg.Pool {
  if (!pool) {
    const url = variable("DATABASE_URL")?.trim();
    if (!url) throw new Error("DATABASE_URL no está definida: el estado vive en archivos");
    pool = new pg.Pool(opciones(url, variable("DATABASE_PASSWORD_FILE")?.trim() || undefined));
    // Un cliente ocioso que se cae (reinicio de la base, failover de Cloud SQL) no tumba el servidor.
    pool.on("error", () => undefined);
  }
  return pool;
}

/** Usa otro pool (las pruebas, el comando de migración). */
export function usarPiscina(p: pg.Pool | null): void {
  pool = p;
}

/** La organización de la operación en curso (la del pedido o la del despliegue). */
export const contextoOrganizacion = new AsyncLocalStorage<string>();
/** La transacción en curso, si quien llama ya abrió una (la migración de datos corre todo en una sola). */
const contextoTransaccion = new AsyncLocalStorage<{ cliente: Cliente; organizacion: string }>();

export function fijarPredeterminada(id: string | null): void {
  predeterminada = id;
}

export function organizacionActual(): string {
  const o = contextoTransaccion.getStore()?.organizacion ?? contextoOrganizacion.getStore() ?? predeterminada;
  if (!o) throw new Error("no hay organización para esta operación (VERA_ORGANIZACION sin resolver)");
  return o;
}

async function fijar(c: Cliente, organizacion: string): Promise<void> {
  await c.query("SELECT set_config('vera.organizacion_id', $1, true)", [organizacion]);
}

/** Corre `f` en una transacción de la organización (o dentro de la que ya está abierta). */
export async function enOrganizacion<T>(f: (c: Cliente) => Promise<T>, organizacion?: string): Promise<T> {
  const abierta = contextoTransaccion.getStore();
  const org = organizacion ?? organizacionActual();
  if (abierta) {
    if (abierta.organizacion !== org) throw new Error("una transacción abierta no cambia de organización");
    return f(abierta.cliente);
  }
  const c = await piscina().connect();
  try {
    await c.query("BEGIN");
    await fijar(c, org);
    const r = await f(c);
    await c.query("COMMIT");
    return r;
  } catch (e) {
    await c.query("ROLLBACK").catch(() => undefined);
    throw e;
  } finally {
    c.release();
  }
}

/**
 * Abre UNA transacción de la organización y corre `f` dentro: toda operación de los repositorios que se haga
 * mientras tanto va en ella. Con `deshacer` se revierte al final (modo de prueba de la migración de datos).
 */
export async function enTransaccion<T>(organizacion: string, f: () => Promise<T>, deshacer = false): Promise<T> {
  const c = await piscina().connect();
  try {
    await c.query("BEGIN");
    await fijar(c, organizacion);
    const r = await contextoTransaccion.run({ cliente: c, organizacion }, f);
    await c.query(deshacer ? "ROLLBACK" : "COMMIT");
    return r;
  } catch (e) {
    await c.query("ROLLBACK").catch(() => undefined);
    throw e;
  } finally {
    c.release();
  }
}

/** El id de una organización por su slug (sin organización fijada: función SECURITY DEFINER de la base). */
export async function organizacionPorSlug(slug: string): Promise<string | null> {
  const r = await piscina().query<{ id: string | null }>("SELECT vera.organizacion_por_slug($1) AS id", [slug]);
  return r.rows[0]?.id ?? null;
}

/** Las particiones mensuales de las ejecuciones para el mes de `fecha` y los `meses` siguientes. */
export async function asegurarParticiones(c: Cliente | pg.Pool, fecha: Date, meses = 1): Promise<void> {
  const dia = fecha.toISOString().slice(0, 10);
  await c.query("SELECT vera.asegurar_particiones('corridas', $1::date, $2)", [dia, meses]);
  await c.query("SELECT vera.asegurar_particiones('eventos_corrida', $1::date, $2)", [dia, meses]);
}

/**
 * Al arrancar el servidor con la base: resuelve la organización del despliegue y deja listas las particiones de
 * este mes y los dos siguientes. Sin la organización creada no arranca (dice cómo crearla).
 */
export async function iniciar(): Promise<{ organizacion: string; slug: string }> {
  const slug = (process.env.VERA_ORGANIZACION ?? "principal").trim();
  const id = await organizacionPorSlug(slug);
  if (!id) throw new Error(`la organización «${slug}» no existe en la base: créala con «npm run bd -- organizacion ${slug} "<nombre>"» (con la URL del migrador)`);
  fijarPredeterminada(id);
  await asegurarParticiones(piscina(), new Date(), 3);
  return { organizacion: id, slug };
}

export async function cerrar(): Promise<void> {
  const p = pool;
  pool = null;
  predeterminada = null;
  await p?.end();
}

/** ms desde 1970 ↔ timestamptz. */
export const fecha = (ms: number | null | undefined): Date | null => (typeof ms === "number" && Number.isFinite(ms) ? new Date(ms) : null);
export const ms = (d: Date | null | undefined): number | undefined => (d instanceof Date ? d.getTime() : undefined);
