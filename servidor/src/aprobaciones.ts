import { existsSync } from "node:fs";
import { mkdir, readdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { config } from "./config.ts";
import * as flujos from "./flujos.ts";
import type { Corrida, Evento } from "./motor.ts";

/**
 * Aprobación humana: el nodo `aprobacion_humana` del motor deja la corrida «en espera» y escribe la aprobación en
 * <estado>/aprobaciones/<id>.json (con el estado de la corrida aparte, en <id>.estado.json, que nunca sale por la API).
 * Una PERSONA decide desde la plataforma; entonces se encola una corrida nueva del flujo que sigue desde ese nodo
 * por «aprobado», «rechazado» o «vencido», con {{aprobacion.*}}.
 *
 *   GET  /api/aprobaciones?flujo=&estado=   → [{id, flujo, corrida, titulo, detalle, datos, campos_editables, creada, vence?, estado, …}]
 *   GET  /api/aprobaciones/:id              → una
 *   POST /api/aprobaciones/:id {decision: "aprobar"|"rechazar", comentario?, cambios?}
 *        solo con el encabezado X-Confirmado-Por-Persona: si (lo pone la web cuando una persona confirma; Vera nunca).
 *
 * Eventos en el bus: `aprobacion_pendiente` (lo emite el motor al pausar) y `aprobacion_resuelta` (aquí, al decidir o vencer).
 * Las que vencen (tiempo_limite_h) se resuelven solas como «vencido». Todo sobrevive a reinicios: la verdad es el disco.
 */

export type EstadoAprobacion = "pendiente" | "aprobado" | "rechazado" | "vencido";

export interface Aprobacion {
  id: string;
  flujo: string;
  /** La corrida que quedó esperando. */
  corrida: string;
  caso?: string;
  nodo?: string;
  nombre_nodo?: string;
  titulo: string;
  detalle: string;
  datos: Record<string, unknown>;
  campos_editables: string[];
  /** ms desde 1970 */
  creada: number;
  vence?: number | null;
  al_vencer?: "rechazar" | "aprobar" | "error";
  asignado_a?: string;
  estado: EstadoAprobacion;
  /** Las salidas que tiene el nodo (aprobado, rechazado y, si tiene esa arista, vencido). */
  salidas?: string[];
  decision?: "aprobar" | "rechazar" | "vencido";
  comentario?: string;
  cambios?: Record<string, unknown>;
  resuelta?: number;
  /** Quién resolvió: una persona o el vencimiento. */
  por?: "persona" | "vencimiento";
  /** La corrida que siguió el flujo tras la decisión. */
  reanudada?: string;
}

export interface Dependencias {
  /** Encola la corrida que sigue el flujo (origen «aprobacion»); null si la cola del flujo está llena. */
  reanudar: (flujo: string, datos: { entrada: Record<string, unknown>; caso?: string }) => Corrida | null;
  /** Publica un evento en el bus de la plataforma. */
  emitir: (e: Evento) => void;
}

const ID = /^apr-[a-z0-9-]{1,41}-[0-9a-f]{12}$/;
const CAMPOS_PUBLICOS = ["id", "flujo", "corrida", "caso", "nodo", "nombre_nodo", "titulo", "detalle", "datos", "campos_editables", "creada", "vence",
  "al_vencer", "asignado_a", "estado", "salidas", "decision", "comentario", "cambios", "resuelta", "por", "reanudada"] as const;
const REVISION_MS = Number(process.env.APROBACIONES_REVISION_MS ?? 30_000);
/** Una reanudación encolada que no arrancó (reinicio, cola llena) se vuelve a encolar tras este tiempo. */
const REINTENTO_MS = Number(process.env.APROBACIONES_REINTENTO_MS ?? 10 * 60_000);

const carpeta = () => join(config.datos, "aprobaciones");
const ruta = (id: string) => join(carpeta(), `${id}.json`);
const confirmadoPorPersona = (h: Record<string, unknown>) => String(h["x-confirmado-por-persona"] ?? "").trim().toLowerCase() === "si";

export function publica(a: Aprobacion): Aprobacion {
  const r: Record<string, unknown> = {};
  for (const k of CAMPOS_PUBLICOS) if (a[k] !== undefined) r[k] = a[k];
  r.datos ??= {};
  r.campos_editables ??= [];
  return r as unknown as Aprobacion;
}

export async function leer(id: string): Promise<Aprobacion | null> {
  if (!ID.test(id)) return null;
  try {
    return JSON.parse(await readFile(ruta(id), "utf8")) as Aprobacion;
  } catch {
    return null;
  }
}

async function escribir(a: Aprobacion): Promise<void> {
  await mkdir(carpeta(), { recursive: true });
  const tmp = `${ruta(a.id)}.${process.pid}.${randomBytes(3).toString("hex")}.tmp`;
  await writeFile(tmp, JSON.stringify(a), "utf8");
  await rename(tmp, ruta(a.id));
}

export async function listar(filtro: { flujo?: string; estado?: string } = {}): Promise<Aprobacion[]> {
  const archivos = (await readdir(carpeta()).catch(() => [] as string[])).filter((x) => /^apr-.*\.json$/.test(x) && !x.endsWith(".estado.json"));
  const r: Aprobacion[] = [];
  for (const x of archivos) {
    const a = await leer(x.slice(0, -5));
    if (!a) continue;
    if (filtro.flujo && a.flujo !== filtro.flujo) continue;
    if (filtro.estado && a.estado !== filtro.estado) continue;
    r.push(publica(a));
  }
  // las pendientes primero (las que vencen antes arriba), luego las resueltas de la más nueva a la más vieja
  return r.sort((a, b) => (a.estado === "pendiente") !== (b.estado === "pendiente") ? (a.estado === "pendiente" ? -1 : 1)
    : a.estado === "pendiente" ? (a.vence ?? Infinity) - (b.vence ?? Infinity) || b.creada - a.creada : (b.resuelta ?? b.creada) - (a.resuelta ?? a.creada));
}

const pedidoDecision = z.object({
  decision: z.enum(["aprobar", "rechazar"], { error: "decision: aprobar o rechazar" }),
  comentario: z.string().max(4000, "comentario: hasta 4000 caracteres").optional(),
  cambios: z.record(z.string(), z.unknown(), { error: "cambios tiene que ser un objeto {campo: valor nuevo}" }).optional(),
}, { error: "pedido inválido: {decision: aprobar|rechazar, comentario?, cambios?}" });

/** Las que se están resolviendo ahora (dos pedidos a la vez no deciden dos veces). */
const ocupadas = new Set<string>();
/** Cuándo se encoló por última vez la corrida que sigue cada aprobación (en este proceso). */
const encoladas = new Map<string, number>();
let deps: Dependencias | null = null;

type Resultado = { ok: true; aprobacion: Aprobacion; corrida: string | null } | { ok: false; codigo: number; error: string };

/** Resuelve una aprobación (una persona o el vencimiento) y encola la corrida que sigue. */
export async function resolver(id: string, decision: "aprobar" | "rechazar" | "vencido", comentario = "", cambios: Record<string, unknown> = {},
  por: "persona" | "vencimiento" = "persona"): Promise<Resultado> {
  if (ocupadas.has(id)) return { ok: false, codigo: 409, error: "esa aprobación se está resolviendo en este momento" };
  ocupadas.add(id);
  try {
    const a = await leer(id);
    if (!a) return { ok: false, codigo: 404, error: "esa aprobación no existe" };
    if (a.estado !== "pendiente") return { ok: false, codigo: 409, error: `esa aprobación ya está ${a.estado}` };
    const ajenos = Object.keys(cambios).filter((k) => !(a.campos_editables ?? []).includes(k));
    if (ajenos.length) return { ok: false, codigo: 400, error: `no se pueden cambiar ${ajenos.join(", ")} (editables: ${(a.campos_editables ?? []).join(", ") || "ninguno"})` };
    a.estado = decision === "aprobar" ? "aprobado" : decision === "rechazar" ? "rechazado" : "vencido";
    a.decision = decision;
    a.comentario = comentario.trim();
    a.cambios = cambios;
    a.resuelta = Date.now();
    a.por = por;
    await escribir(a);
    const corrida = await encolar(a);
    const publicaA = publica(await leer(id) ?? a);
    deps?.emitir({ tipo: "aprobacion_resuelta", corrida: a.corrida || null, t: Date.now() / 1000, flujo: a.flujo, id: a.id, aprobacion: publicaA, corrida_reanudada: corrida });
    return { ok: true, aprobacion: publicaA, corrida };
  } finally {
    ocupadas.delete(id);
  }
}

/** Encola la corrida que sigue una aprobación resuelta (si el motor no la siguió ya). */
async function encolar(a: Aprobacion): Promise<string | null> {
  if (!deps || a.estado === "pendiente" || existsSync(join(carpeta(), `${a.id}.reanudada`))) return null;
  const f = flujos.obtener(a.flujo);
  if (!f || f.tipo !== "declarativo") return null;
  const disp = f.definicion ? flujos.disparadorDe(f.definicion)?.tipo : undefined;
  const de_correo = flujos.DISPARADORES_DE_CORREO.has(String(disp ?? ""));
  const c = deps.reanudar(a.flujo, { entrada: { aprobacion: a.id }, ...(de_correo && a.caso ? { caso: a.caso } : {}) });
  if (!c) return null;
  encoladas.set(a.id, Date.now());
  a.reanudada = c.id;
  await escribir(a);
  return c.id;
}

/** Vence las que pasaron su tiempo y vuelve a encolar las resueltas que nunca siguieron (reinicio, cola llena). */
export async function revisar(ahora = Date.now()): Promise<void> {
  for (const a of await listar()) {
    if (a.estado === "pendiente" && typeof a.vence === "number" && a.vence <= ahora) {
      await resolver(a.id, "vencido", "", {}, "vencimiento");
    } else if (a.estado !== "pendiente" && !existsSync(join(carpeta(), `${a.id}.reanudada`)) && ahora - (encoladas.get(a.id) ?? 0) > REINTENTO_MS) {
      const completa = await leer(a.id);
      if (completa) await encolar(completa);
    }
  }
}

export async function registrar(app: FastifyInstance, d: Dependencias): Promise<void> {
  deps = d;
  await mkdir(carpeta(), { recursive: true });
  // La corrida que sigue una aprobación lleva en su orden qué aprobación es: el motor restaura el estado guardado.
  flujos.extrasDeOrden.push((_flujo, c) => {
    const id = (c.entrada as { aprobacion?: unknown } | undefined)?.aprobacion;
    return c.origen === "aprobacion" && typeof id === "string" && ID.test(id) ? { reanudar: { aprobacion: id } } : undefined;
  });

  app.get<{ Querystring: { flujo?: string; estado?: string } }>("/api/aprobaciones", async (req, res) => {
    const estado = req.query.estado || undefined;
    if (estado && !["pendiente", "aprobado", "rechazado", "vencido"].includes(estado)) return res.code(400).send({ error: "estado: pendiente, aprobado, rechazado o vencido" });
    return listar({ flujo: req.query.flujo || undefined, estado });
  });

  app.get<{ Params: { id: string } }>("/api/aprobaciones/:id", async (req, res) => {
    const a = await leer(req.params.id);
    return a ? publica(a) : res.code(404).send({ error: "esa aprobación no existe" });
  });

  app.post<{ Params: { id: string } }>("/api/aprobaciones/:id", async (req, res) => {
    // Decidir es de una persona: la web manda este encabezado solo cuando alguien confirma en pantalla. Vera puede listar, no decidir.
    if (!confirmadoPorPersona(req.headers as Record<string, unknown>))
      return res.code(403).send({ error: "solo una persona puede aprobar o rechazar: confírmalo en la bandeja de aprobaciones de la plataforma" });
    const p = pedidoDecision.safeParse(req.body ?? {});
    if (!p.success) return res.code(400).send({ error: p.error.issues[0]?.message ?? "pedido inválido" });
    const r = await resolver(req.params.id, p.data.decision, p.data.comentario ?? "", p.data.cambios ?? {}, "persona");
    if (!r.ok) return res.code(r.codigo).send({ error: r.error });
    return r;
  });

  const tic = setInterval(() => void revisar().catch((e) => app.log.warn({ error: e instanceof Error ? e.message : String(e) }, "no se pudieron revisar las aprobaciones")), REVISION_MS);
  tic.unref();
  app.addHook("onReady", async () => {
    setTimeout(() => void revisar().catch(() => undefined), 2000).unref();
  });
}
