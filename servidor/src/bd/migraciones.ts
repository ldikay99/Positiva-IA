import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type pg from "pg";

/**
 * Migraciones versionadas en SQL numerado (servidor/migraciones/NNNN_nombre.sql), sin ORM:
 *
 * - se aplican en orden, cada una en su transacción, con un candado de la base (pg_advisory_xact_lock) para que
 *   dos instancias que arrancan a la vez no se pisen;
 * - vera.migraciones guarda versión, nombre, huella SHA-256 y fecha: una migración aplicada no se vuelve a correr
 *   y, si su archivo cambió después de aplicarla, se detiene (las migraciones aplicadas no se editan: se agrega
 *   otra);
 * - las corre el migrador (el dueño de las tablas), nunca el usuario de la aplicación.
 */

export const CARPETA = resolve(import.meta.dirname, "..", "..", "migraciones");
const CANDADO = 0x7665_7261; // «vera»

export interface Migracion {
  version: number;
  nombre: string;
  sql: string;
  huella: string;
}

export function leer(carpeta = CARPETA): Migracion[] {
  const r: Migracion[] = [];
  for (const a of readdirSync(carpeta).filter((x) => x.endsWith(".sql")).sort()) {
    const m = /^(\d{4})_([a-z0-9_]+)\.sql$/.exec(a);
    if (!m) throw new Error(`nombre de migración inválido: ${a} (NNNN_nombre.sql)`);
    const sql = readFileSync(join(carpeta, a), "utf8").replace(/\r\n/g, "\n");
    r.push({ version: Number(m[1]), nombre: m[2]!, sql, huella: createHash("sha256").update(sql).digest("hex") });
  }
  r.forEach((x, i) => {
    if (x.version !== i + 1) throw new Error(`las migraciones van seguidas desde 0001: falta la ${String(i + 1).padStart(4, "0")}`);
  });
  return r;
}

export interface Aplicada { version: number; nombre: string; huella: string; aplicada: Date }

async function tabla(c: pg.ClientBase): Promise<void> {
  await c.query("CREATE SCHEMA IF NOT EXISTS vera");
  await c.query(`CREATE TABLE IF NOT EXISTS vera.migraciones (
    version int PRIMARY KEY, nombre text NOT NULL, huella text NOT NULL, aplicada timestamptz NOT NULL DEFAULT now(), duracion_ms int NOT NULL)`);
}

export async function aplicadas(c: pg.ClientBase): Promise<Aplicada[]> {
  const existe = await c.query<{ t: string | null }>("SELECT to_regclass('vera.migraciones')::text AS t");
  if (!existe.rows[0]?.t) return [];
  return (await c.query<Aplicada>("SELECT version, nombre, huella, aplicada FROM vera.migraciones ORDER BY version")).rows;
}

/** Lo que falta por aplicar (y error si una aplicada cambió o no existe en el repositorio). */
export function pendientes(todas: Migracion[], hechas: Aplicada[]): Migracion[] {
  for (const h of hechas) {
    const m = todas.find((x) => x.version === h.version);
    if (!m) throw new Error(`la base tiene la migración ${h.version} (${h.nombre}) y este código no: ¿versión vieja del servidor?`);
    if (m.huella !== h.huella) throw new Error(`la migración ${h.version} (${h.nombre}) cambió después de aplicarse: no se editan, se agrega otra`);
  }
  return todas.filter((m) => !hechas.some((h) => h.version === m.version));
}

/** Aplica las pendientes. Devuelve las que aplicó. */
export async function migrar(pool: pg.Pool, carpeta = CARPETA, aviso: (t: string) => void = () => undefined): Promise<Migracion[]> {
  const todas = leer(carpeta);
  const c = await pool.connect();
  const hechas: Migracion[] = [];
  try {
    for (;;) {
      await c.query("BEGIN");
      try {
        await c.query("SELECT pg_advisory_xact_lock($1)", [CANDADO]);
        await tabla(c);
        const siguiente = pendientes(todas, await aplicadas(c))[0];
        if (!siguiente) {
          await c.query("COMMIT");
          break;
        }
        const t0 = Date.now();
        await c.query(siguiente.sql);
        await c.query("INSERT INTO vera.migraciones (version, nombre, huella, duracion_ms) VALUES ($1, $2, $3, $4)",
          [siguiente.version, siguiente.nombre, siguiente.huella, Date.now() - t0]);
        await c.query("COMMIT");
        hechas.push(siguiente);
        aviso(`aplicada ${String(siguiente.version).padStart(4, "0")}_${siguiente.nombre} (${Date.now() - t0} ms)`);
      } catch (e) {
        await c.query("ROLLBACK").catch(() => undefined);
        throw e;
      }
    }
  } finally {
    c.release();
  }
  return hechas;
}

/** Sin aplicar nada: si la base está al día con este código (lo revisa el servidor al arrancar). */
export async function alDia(pool: pg.Pool, carpeta = CARPETA): Promise<{ ok: boolean; faltan: string[] }> {
  const c = await pool.connect();
  try {
    const faltan = pendientes(leer(carpeta), await aplicadas(c)).map((m) => `${String(m.version).padStart(4, "0")}_${m.nombre}`);
    return { ok: faltan.length === 0, faltan };
  } finally {
    c.release();
  }
}

/** Crea una organización (si no existe). Es del operador: corre con el migrador, dueño de vera.organizaciones. */
export async function crearOrganizacion(p: pg.Pool, slug: string, nombre: string): Promise<{ id: string; creada: boolean }> {
  if (!/^[a-z][a-z0-9-]{1,40}$/.test(slug)) throw new Error("slug: minúsculas, números y guiones (2 a 41)");
  const r = await p.query<{ id: string; creada: boolean }>(
    `INSERT INTO vera.organizaciones (slug, nombre) VALUES ($1, $2)
     ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug
     RETURNING id, (xmax = 0) AS creada`, [slug, nombre]);
  return r.rows[0]!;
}
