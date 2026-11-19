import { mkdir, readdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import type { FastifyInstance } from "fastify";
import { config } from "./config.ts";
import * as bd from "./bd/conexion.ts";
import * as estadoBd from "./bd/estado.ts";
import * as flujos from "./flujos.ts";
import { estadoDelTrabajador, type Corrida, type Evento } from "./motor.ts";

/**
 * Corridas aplazadas: reintentar_mas_tarde, limite_tasa (al_exceder aplazar) y ventana_horario (fuera aplazar) terminan la
 * corrida como «aplazada» y el motor deja <estado>/aplazadas/<id>.json con {flujo, caso, entrada, cuando, intento, motivo}.
 * Aquí se revisan cada pocos segundos: a su hora se vuelve a correr el flujo desde el principio con la misma entrada y, en la
 * orden, {aplazamiento: {id, intento, motivo, desde}} ({{aplazamiento.intento}} en el flujo). Al terminar esa corrida el archivo
 * se borra; si la plataforma se reinicia antes, se vuelve a encolar. No ocupa ningún trabajador mientras espera.
 *
 *   GET /api/aplazadas?flujo=   → [{id, flujo, corrida, nodo, cuando, intento, motivo, creada, encolada?}]
 */

export interface Aplazada {
  id: string;
  flujo: string;
  corrida: string;
  caso?: string;
  nodo?: string;
  entrada?: unknown;
  /** ms desde 1970: cuándo se vuelve a correr. */
  cuando: number;
  intento: number;
  motivo: string;
  creada: number;
  /** La corrida que se encoló a su hora (y cuándo). */
  encolada?: string;
  encolada_t?: number;
}

export interface Dependencias {
  disparar: (flujo: string, origen: string, datos: { entrada?: unknown; caso?: string }) => Corrida | null;
  escuchar: (f: (e: Evento) => void) => () => void;
  emitir: (e: Evento) => void;
}

const ID = /^apl-[a-z0-9-]{1,41}-[0-9a-f]{12}$/;
const REVISION_MS = Number(process.env.APLAZADAS_REVISION_MS ?? 15_000);
const REINTENTO_MS = Number(process.env.APLAZADAS_REINTENTO_MS ?? 10 * 60_000);
const carpeta = () => join(config.datos, "aplazadas");
const ruta = (id: string) => join(carpeta(), `${id}.json`);

/** corrida encolada → la aplazada que la originó (para su orden y para borrarla al terminar). */
const porCorrida = new Map<string, Aplazada>();

async function leer(id: string): Promise<Aplazada | null> {
  if (!ID.test(id)) return null;
  if (bd.activa()) return (await estadoBd.aplazadas.leer(id)) as Aplazada | null;
  try {
    return JSON.parse(await readFile(ruta(id), "utf8")) as Aplazada;
  } catch {
    return null;
  }
}

async function escribir(a: Aplazada): Promise<void> {
  if (bd.activa()) return estadoBd.aplazadas.escribir(a as unknown as estadoBd.AplazadaBd);
  await mkdir(carpeta(), { recursive: true });
  const tmp = `${ruta(a.id)}.${process.pid}.${randomBytes(3).toString("hex")}.tmp`;
  await writeFile(tmp, JSON.stringify(a), "utf8");
  await rename(tmp, ruta(a.id));
}

/** Quita una aplazada (ya corrió, o su flujo no existe). */
async function quitar(id: string): Promise<void> {
  if (bd.activa()) await estadoBd.aplazadas.borrar(id);
  else await unlink(ruta(id)).catch(() => undefined);
}

export async function listar(flujo?: string): Promise<Aplazada[]> {
  if (bd.activa()) return (await estadoBd.aplazadas.listar(flujo)) as unknown as Aplazada[];
  const r: Aplazada[] = [];
  for (const x of (await readdir(carpeta()).catch(() => [] as string[])).filter((y) => /^apl-.*\.json$/.test(y))) {
    const a = await leer(x.slice(0, -5));
    if (a && (!flujo || a.flujo === flujo)) r.push(a);
  }
  return r.sort((a, b) => a.cuando - b.cuando);
}

/** Encola las que ya llegaron a su hora (y las que se encolaron pero nunca corrieron). */
export async function revisar(deps: Dependencias, ahora = Date.now()): Promise<string[]> {
  const encoladas: string[] = [];
  for (const a of await listar()) {
    if (a.cuando > ahora) continue;
    // encolada en este proceso y todavía sin resultado: espera (si no la vio terminar en mucho tiempo, se vuelve a encolar)
    if (a.encolada && porCorrida.has(a.encolada) && ahora - (a.encolada_t ?? 0) < REINTENTO_MS) continue;
    if (a.encolada) porCorrida.delete(a.encolada);
    const f = flujos.obtener(a.flujo);
    if (!f || f.tipo !== "declarativo") {
      await quitar(a.id).catch(() => undefined);  // el flujo ya no existe: no hay qué correr
      continue;
    }
    const disp = f.definicion ? flujos.disparadorDe(f.definicion)?.tipo : undefined;
    const de_correo = flujos.DISPARADORES_DE_CORREO.has(String(disp ?? ""));
    const c = deps.disparar(a.flujo, "aplazada", { entrada: a.entrada ?? {}, ...(de_correo && a.caso ? { caso: a.caso } : {}) });
    if (!c) continue;  // cola llena: en la próxima revisión
    a.encolada = c.id;
    a.encolada_t = ahora;
    porCorrida.set(c.id, a);
    await escribir(a);
    encoladas.push(c.id);
    deps.emitir({ tipo: "aplazada_encolada", corrida: c.id, t: Date.now() / 1000, flujo: a.flujo, id: a.id, intento: a.intento, motivo: a.motivo });
  }
  return encoladas;
}

export async function registrar(app: FastifyInstance, deps: Dependencias): Promise<void> {
  if (!bd.activa()) await mkdir(carpeta(), { recursive: true });
  // Con la base, la aplazada que deja el motor llega por su canal de eventos.
  estadoDelTrabajador.push((e) => {
    if (e.tipo !== "estado_aplazada" || !bd.activa()) return false;
    const registro = e.registro as Aplazada | undefined;
    if (registro && typeof registro.id === "string" && ID.test(registro.id))
      void estadoBd.aplazadas.escribir(registro as unknown as estadoBd.AplazadaBd)
        .catch((x: unknown) => app.log.error({ aplazada: registro.id, error: x instanceof Error ? x.message : String(x) }, "no se pudo guardar la aplazada en la base"));
    return true;
  });
  flujos.extrasDeOrden.push((_flujo, c) => {
    const a = c.origen === "aplazada" ? porCorrida.get(c.id) : undefined;
    return a ? { aplazamiento: { id: a.id, intento: a.intento, motivo: a.motivo, desde: a.corrida } } : undefined;
  });
  // La corrida que volvió terminó: su aplazada se borra (si se aplazó otra vez, el motor ya escribió una nueva).
  deps.escuchar((e) => {
    if (e.tipo !== "resultado" || typeof e.corrida !== "string") return;
    const a = porCorrida.get(e.corrida);
    if (!a) return;
    porCorrida.delete(e.corrida);
    void quitar(a.id).catch(() => undefined);
  });
  app.get<{ Querystring: { flujo?: string } }>("/api/aplazadas", async (req) =>
    (await listar(req.query.flujo || undefined)).map(({ entrada: _e, ...resto }) => resto));
  const tic = setInterval(() => void revisar(deps).catch((e) => app.log.warn({ error: e instanceof Error ? e.message : String(e) }, "no se pudieron revisar las corridas aplazadas")), REVISION_MS);
  tic.unref();
  app.addHook("onReady", async () => {
    setTimeout(() => void revisar(deps).catch(() => undefined), 3000).unref();
  });
}
