import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";
import { config } from "./config.ts";

/**
 * Los recursos de cada flujo: cuántos trabajadores (procesos de Python) puede tener,
 * cuántas corridas (o lotes) atiende a la vez, cuántas esperan en su cola y el tiempo
 * máximo de cada corrida. Viven en <estado>/recursos.json; lo que un flujo no fija sale
 * de sus valores por defecto (PQRD toma sus trabajadores de la concurrencia de Ajustes,
 * como siempre).
 *
 * Encima de todos está el tope global TRABAJADORES_MAX: el planificador (planificador.ts)
 * lo reparte de forma justa entre los flujos que tienen trabajo.
 */

export interface Recursos {
  /** Procesos de Python que puede tener el flujo a la vez. */
  trabajadores: number;
  /** Corridas (o lotes del job, que van enteros a un trabajador) que atiende a la vez. */
  corridas_simultaneas: number;
  /** Corridas que pueden esperar en su cola; las de más se rechazan (429). */
  cola_max: number;
  /** Tiempo máximo de una corrida (o de cada tanda de un lote): pasado, el trabajador se detiene y arranca otro. */
  tiempo_max_s: number;
}

export const LIMITES = { cola_max: [1, 2000], tiempo_max_s: [30, 7200] } as const;

const esquema = z.object({
  trabajadores: z.number().int().min(1).optional(),
  corridas_simultaneas: z.number().int().min(1).optional(),
  cola_max: z.number().int().min(LIMITES.cola_max[0]).max(LIMITES.cola_max[1]).optional(),
  tiempo_max_s: z.number().int().min(LIMITES.tiempo_max_s[0]).max(LIMITES.tiempo_max_s[1]).optional(),
}).strict();

export type CambioRecursos = z.infer<typeof esquema>;

/** Lo que el servidor sabe de un flujo para darle sus valores por defecto. */
export interface FlujoDeRecursos {
  id: string;
  tipo: "codigo" | "declarativo";
  /** pqrd o tutelas (el motor de un flujo de código o de su copia). */
  base?: string;
}

/** La concurrencia de Ajustes (la de PQRD de siempre); la fija el servidor. */
export const enlace = { concurrenciaPqrd: (): number => 2 };

export function porDefecto(f: FlujoDeRecursos): Recursos {
  const tope = topeGlobal();
  if (f.tipo === "codigo") {
    const n = Math.min(tope, f.base === "pqrd" ? enlace.concurrenciaPqrd() : 2);
    // Un lote del job puede traer hasta 150 correos: la cola los aguanta. El tiempo es el tope del job (Cloud Run).
    return { trabajadores: n, corridas_simultaneas: n, cola_max: 500, tiempo_max_s: 3600 };
  }
  // Los declarativos: dos a la vez, 50 en espera (como antes) y el tope del motor declarativo (30 min).
  const n = Math.min(tope, 2);
  return { trabajadores: n, corridas_simultaneas: n, cola_max: 50, tiempo_max_s: 1800 };
}

/** El tope global de procesos de Python de la plataforma (TRABAJADORES_MAX). */
export const topeGlobal = (): number => Math.max(1, Math.floor(config.trabajadoresMax) || 8);

const ruta = () => join(config.datos, "recursos.json");
let guardados: Record<string, CambioRecursos> = {};

export async function cargar(): Promise<void> {
  try {
    const crudo = JSON.parse(await readFile(ruta(), "utf8")) as Record<string, unknown>;
    guardados = {};
    for (const [id, v] of Object.entries(crudo)) {
      const r = esquema.safeParse(v);
      if (r.success) guardados[id] = r.data;
    }
  } catch {
    guardados = {};
  }
}

async function persistir(): Promise<void> {
  await mkdir(config.datos, { recursive: true });
  const tmp = `${ruta()}.${Date.now()}.tmp`;
  await writeFile(tmp, JSON.stringify(guardados, null, 2), "utf8");
  await rename(tmp, ruta());
}

/** Lo que vale para un flujo: lo suyo encima de sus valores por defecto, siempre dentro de los topes. */
export function de(f: FlujoDeRecursos): Recursos & { propios: (keyof Recursos)[]; por_defecto: Recursos; tope_global: number } {
  const d = porDefecto(f);
  const g = guardados[f.id] ?? {};
  const tope = topeGlobal();
  const trabajadores = Math.max(1, Math.min(tope, g.trabajadores ?? d.trabajadores));
  return {
    trabajadores,
    // nunca más corridas a la vez que trabajadores: cada una (o cada lote) ocupa uno entero
    corridas_simultaneas: Math.max(1, Math.min(trabajadores, g.corridas_simultaneas ?? trabajadores)),
    cola_max: g.cola_max ?? d.cola_max,
    tiempo_max_s: g.tiempo_max_s ?? d.tiempo_max_s,
    propios: Object.keys(g) as (keyof Recursos)[],
    por_defecto: d,
    tope_global: tope,
  };
}

export type ResultadoCambio = { ok: true; recursos: ReturnType<typeof de> } | { ok: false; codigo: number; error: string };

/**
 * Cambia los recursos de un flujo. Un campo en null vuelve a su valor por defecto.
 * trabajadores no pasa del tope global y corridas_simultaneas no pasa de trabajadores.
 */
export async function poner(f: FlujoDeRecursos, cuerpo: unknown): Promise<ResultadoCambio> {
  if (!cuerpo || typeof cuerpo !== "object" || Array.isArray(cuerpo)) return { ok: false, codigo: 400, error: "manda {trabajadores?, corridas_simultaneas?, cola_max?, tiempo_max_s?}" };
  const quitar = Object.entries(cuerpo as Record<string, unknown>).filter(([, v]) => v === null).map(([k]) => k);
  const resto = Object.fromEntries(Object.entries(cuerpo as Record<string, unknown>).filter(([, v]) => v !== null));
  const r = esquema.safeParse(resto);
  const tope = topeGlobal();
  if (!r.success) {
    const p = r.error.issues[0];
    return { ok: false, codigo: 400, error: `${p?.path.join(".") || "recursos"}: ${p?.message ?? "inválido"} (trabajadores 1–${tope}, corridas_simultaneas ≥ 1, cola_max ${LIMITES.cola_max.join("–")}, tiempo_max_s ${LIMITES.tiempo_max_s.join("–")})` };
  }
  if ((r.data.trabajadores ?? 0) > tope) return { ok: false, codigo: 400, error: `trabajadores no puede pasar del tope global de la plataforma (TRABAJADORES_MAX = ${tope})` };
  const nuevo: CambioRecursos = { ...(guardados[f.id] ?? {}), ...r.data };
  for (const k of quitar) delete (nuevo as Record<string, unknown>)[k];
  const trabajadores = nuevo.trabajadores ?? porDefecto(f).trabajadores;
  if ((nuevo.corridas_simultaneas ?? 0) > trabajadores) return { ok: false, codigo: 400, error: `corridas_simultaneas (${nuevo.corridas_simultaneas}) no puede pasar de trabajadores (${trabajadores}): cada corrida ocupa un trabajador entero` };
  if (Object.keys(nuevo).length) guardados[f.id] = nuevo;
  else delete guardados[f.id];
  await persistir();
  return { ok: true, recursos: de(f) };
}

/** Un flujo borrado no deja sus recursos guardados. */
export async function olvidar(id: string): Promise<void> {
  if (!(id in guardados)) return;
  delete guardados[id];
  await persistir();
}
