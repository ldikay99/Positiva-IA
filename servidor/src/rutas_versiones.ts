import type { FastifyInstance } from "fastify";
import * as flujos from "./flujos.ts";
import * as estructuras from "./estructuras.ts";
import * as entornos from "./entornos.ts";
import * as versiones from "./versiones.ts";

/**
 * Versiones de cualquier flujo (declarativo o de código), con autor, fecha, etiqueta, resumen y corridas:
 *
 *   GET   /api/flujos/:id/versiones                      → la lista (la más nueva primero)
 *   GET   /api/flujos/:id/versiones/comparar?a=&b=       → qué cambió de a a b, paso por paso (b: la actual; a: la anterior a b)
 *   GET   /api/flujos/:id/versiones/funcionaba           → la última versión con una corrida que terminó bien
 *   GET   /api/flujos/:id/versiones/:n                   → una versión con su definición
 *   PATCH /api/flujos/:id/versiones/:n {etiqueta}        → le pone (o quita) una etiqueta
 *   POST  /api/flujos/:id/restaurar {version}            → vuelve a esa versión (como versión nueva)
 *   POST  /api/flujos/:id/deshacer {pedido?}             → deshace el último cambio (con pedido=true, todo el último pedido de Vera)
 *   POST  /api/flujos/:id/rehacer                        → rehace lo último que se deshizo
 *
 * Volver atrás siempre crea una versión nueva: el historial nunca se borra. Las respuestas que cambian el flujo
 * traen `confirmado` y la versión real que quedó en uso.
 */

export interface Dependencias {
  avisar: (id: string, extra: Record<string, unknown>) => void;
}

export interface Item {
  version: number;
  fecha: number;
  autor: versiones.Autor | null;
  nota?: string;
  resumen?: string;
  etiqueta?: string;
  punto?: string;
  pedido?: string;
  restaurada_de?: number;
  por?: string;
  /** Viene de deshacer (o rehacer): «rehacer» vuelve a esta versión. */
  deshecha?: number;
  del_codigo?: boolean;
  actual: boolean;
  corridas: { bien: number; revisar: number; fallo: number; ultima_bien: number | null };
}

type Res = { code: (n: number) => { send: (x: unknown) => unknown } };

/** Todas las versiones de un flujo con lo que las acompaña; null si el flujo no existe. */
export async function listar(id: string): Promise<{ tipo: "codigo" | "declarativo"; actual: number; items: Item[] } | null> {
  const f = flujos.obtener(id);
  if (!f) return null;
  const metas = await versiones.leer(id);
  const base = f.tipo === "codigo" ? await estructuras.listarVersiones(id) : ((await flujos.versiones(id)) ?? []).map((v) => ({ ...v, del_codigo: false }));
  const corridas = versiones.corridasPorVersion(id, base.map((v) => ({ version: v.version, fecha: v.actualizado || metas[v.version]?.fecha || 0 })));
  const items: Item[] = base.map((v) => {
    const m = metas[v.version] ?? {};
    return {
      version: v.version, fecha: v.actualizado || m.fecha || 0, autor: m.autor ?? null,
      ...(v.nota ? { nota: v.nota } : {}), ...(m.resumen ? { resumen: m.resumen } : {}), ...(m.etiqueta ? { etiqueta: m.etiqueta } : {}),
      ...(m.punto ? { punto: m.punto } : {}), ...(m.pedido ? { pedido: m.pedido } : {}),
      ...(typeof m.restaurada_de === "number" ? { restaurada_de: m.restaurada_de } : {}), ...(m.por ? { por: m.por } : {}),
      ...(typeof m.deshecha === "number" ? { deshecha: m.deshecha } : {}),
      ...(v.del_codigo ? { del_codigo: true } : {}),
      actual: Boolean(v.actual), corridas: corridas.get(v.version) ?? { bien: 0, revisar: 0, fallo: 0, ultima_bien: null },
    };
  });
  return { tipo: f.tipo, actual: items.find((x) => x.actual)?.version ?? 0, items };
}

/** Pasos, conexiones y datos de una versión, para compararla. */
async function grafoDe(id: string, v: number): Promise<versiones.Grafo | null> {
  const f = flujos.obtener(id);
  if (!f) return null;
  if (f.tipo === "codigo") {
    const d = await estructuras.definicionDe(id, flujos.baseDe(id) ?? "pqrd", v);
    return d ? { nodos: d.nodos, aristas: d.aristas } : null;
  }
  const d = await flujos.definicionDeVersion(id, v);
  return d ? { nombre: d.nombre, descripcion: d.descripcion, modelo: (d as { modelo?: unknown }).modelo, ia: (d as { ia?: unknown }).ia, nodos: d.nodos as versiones.Grafo["nodos"], aristas: d.aristas, ...(d.disenio ? { disenio: d.disenio } : {}) } : null;
}

export type ResultadoCambio = { ok: true; confirmado: true; version: number; restaurada: number; sin_cambio?: boolean; flujo: string } | { ok: false; confirmado: false; codigo: number; error: string; problemas?: string[] };

/** Vuelve a una versión (la deja como versión nueva) y anota por qué. */
export async function restaurar(id: string, v: number, extra: Partial<versiones.Meta> = {}): Promise<ResultadoCambio> {
  const f = flujos.obtener(id);
  if (!f) return { ok: false, confirmado: false, codigo: 404, error: "ese flujo no existe" };
  if (!Number.isInteger(v) || v < 0) return { ok: false, confirmado: false, codigo: 400, error: "version tiene que ser un entero" };
  if (f.tipo === "codigo") {
    const r = await estructuras.restaurar(id, flujos.baseDe(id) ?? "pqrd", v, entornos.nombresDeCache(id));
    if (!r.ok || r.version === undefined) return { ok: false, confirmado: false, codigo: r.codigo ?? 400, error: r.error ?? "no se pudo restaurar", ...(r.problemas ? { problemas: r.problemas } : {}) };
    if (!r.sin_cambio && Object.keys(extra).length) await versiones.completar(id, r.version, extra);
    return { ok: true, confirmado: true, version: r.version, restaurada: v, flujo: id, ...(r.sin_cambio ? { sin_cambio: true } : {}) };
  }
  if (v === f.version) return { ok: true, confirmado: true, version: v, restaurada: v, sin_cambio: true, flujo: id };
  const r = await flujos.restaurar(id, v);
  if (!r.ok) return { ok: false, confirmado: false, codigo: r.codigo, error: r.error, ...(r.problemas ? { problemas: r.problemas } : {}) };
  if (Object.keys(extra).length) await versiones.completar(id, r.flujo.version!, extra);
  return { ok: true, confirmado: true, version: r.flujo.version!, restaurada: v, flujo: id };
}

async function existentes(id: string): Promise<{ actual: number; hay: Set<number>; metas: Record<number, versiones.Meta> } | null> {
  const l = await listar(id);
  if (!l) return null;
  return { actual: l.actual, hay: new Set(l.items.map((x) => x.version)), metas: await versiones.leer(id) };
}

/** Deshace el último cambio (o, con pedido, todo lo que hizo Vera en el último pedido). */
export async function deshacer(id: string, todoElPedido = false): Promise<ResultadoCambio & { deshecha?: number }> {
  const e = await existentes(id);
  if (!e) return { ok: false, confirmado: false, codigo: 404, error: "ese flujo no existe" };
  const existe = (n: number) => e.hay.has(n);
  const objetivo = todoElPedido ? versiones.antesDelPedido(e.actual, e.metas, existe) : versiones.anteriorA(e.actual, e.metas, existe);
  if (objetivo === null) return { ok: false, confirmado: false, codigo: 409, error: "no hay un cambio anterior que deshacer" };
  const r = await restaurar(id, objetivo, { por: "deshacer", restaurada_de: objetivo, deshecha: e.actual });
  return r.ok ? { ...r, deshecha: e.actual } : r;
}

/** Rehace lo último que se deshizo (si lo último que pasó fue deshacer). */
export async function rehacer(id: string): Promise<ResultadoCambio> {
  const e = await existentes(id);
  if (!e) return { ok: false, confirmado: false, codigo: 404, error: "ese flujo no existe" };
  const m = e.metas[e.actual];
  const destino = m?.por === "deshacer" ? m.deshecha : m?.por === "rehacer" ? m.deshecha : undefined;
  if (typeof destino !== "number" || !e.hay.has(destino)) return { ok: false, confirmado: false, codigo: 409, error: "no hay nada que rehacer" };
  // si lo que se rehace venía a su vez de deshacer, el siguiente «rehacer» sigue hacia adelante
  const siguiente = e.metas[destino]?.por === "deshacer" ? e.metas[destino]?.deshecha : undefined;
  return restaurar(id, destino, { por: "rehacer", restaurada_de: destino, ...(typeof siguiente === "number" ? { deshecha: siguiente } : { deshecha: undefined }) });
}

/** La versión más reciente (sin contar la actual, salvo que se pida) que estaba en uso cuando una corrida terminó bien. */
export async function queFuncionaba(id: string, contarActual = false): Promise<{ version: number; corridas_bien: number; ultima_bien: number; actual: number } | null> {
  const l = await listar(id);
  if (!l) return null;
  const buena = l.items.filter((x) => x.corridas.bien > 0 && (contarActual || !x.actual)).sort((a, b) => (b.corridas.ultima_bien ?? 0) - (a.corridas.ultima_bien ?? 0))[0];
  return buena ? { version: buena.version, corridas_bien: buena.corridas.bien, ultima_bien: buena.corridas.ultima_bien ?? 0, actual: l.actual } : null;
}

export async function registrar(app: FastifyInstance, deps: Dependencias): Promise<void> {
  const avisarCambio = (r: ResultadoCambio) => {
    if (r.ok && !r.sin_cambio) deps.avisar(r.flujo, { version: r.version, ...(flujos.esDeCodigo(r.flujo) ? { estructura: true } : {}) });
  };
  const responder = (res: Res, r: ResultadoCambio) => (r.ok ? r : res.code(r.codigo).send({ error: r.error, confirmado: false, ...(r.problemas ? { problemas: r.problemas } : {}) }));

  app.get<{ Params: { id: string } }>("/api/flujos/:id/versiones", async (req, res) => {
    const l = await listar(req.params.id);
    return l ? l.items : res.code(404).send({ error: "ese flujo no existe" });
  });

  app.get<{ Params: { id: string }; Querystring: { a?: string; b?: string } }>("/api/flujos/:id/versiones/comparar", async (req, res) => {
    const id = req.params.id;
    const e = await existentes(id);
    if (!e) return res.code(404).send({ error: "ese flujo no existe" });
    const b = req.query.b !== undefined && req.query.b !== "" ? Number(req.query.b) : e.actual;
    const a = req.query.a !== undefined && req.query.a !== "" ? Number(req.query.a) : [...e.hay].filter((n) => n < b).sort((x, y) => y - x)[0];
    if (a === undefined || !Number.isInteger(a) || !Number.isInteger(b)) return res.code(400).send({ error: "di a y b (números de versión); b es la actual si no se da" });
    const [ga, gb] = await Promise.all([grafoDe(id, a), grafoDe(id, b)]);
    if (!ga || !gb) return res.code(404).send({ error: `no existe la versión ${!ga ? a : b}` });
    return { flujo: id, a, b, actual: e.actual, ...versiones.diferencias(ga, gb) };
  });

  app.get<{ Params: { id: string }; Querystring: { con_actual?: string } }>("/api/flujos/:id/versiones/funcionaba", async (req, res) => {
    if (!flujos.obtener(req.params.id)) return res.code(404).send({ error: "ese flujo no existe" });
    const r = await queFuncionaba(req.params.id, req.query.con_actual === "si" || req.query.con_actual === "true");
    return r ?? { version: null, nota: "ninguna versión anterior tiene una corrida que haya terminado bien" };
  });

  app.get<{ Params: { id: string; n: string } }>("/api/flujos/:id/versiones/:n", async (req, res) => {
    const id = req.params.id;
    const n = Number(req.params.n);
    const l = await listar(id);
    if (!l) return res.code(404).send({ error: "ese flujo no existe" });
    const item = l.items.find((x) => x.version === n);
    const g = item ? await grafoDe(id, n) : null;
    if (!item || !g) return res.code(404).send({ error: `no existe la versión ${req.params.n}` });
    return { ...item, definicion: g };
  });

  app.patch<{ Params: { id: string; n: string } }>("/api/flujos/:id/versiones/:n", async (req, res) => {
    const id = req.params.id;
    const n = Number(req.params.n);
    const l = await listar(id);
    if (!l) return res.code(404).send({ error: "ese flujo no existe" });
    if (!l.items.some((x) => x.version === n)) return res.code(404).send({ error: `no existe la versión ${req.params.n}` });
    const etiqueta = (req.body as { etiqueta?: unknown })?.etiqueta;
    if (etiqueta !== null && etiqueta !== undefined && typeof etiqueta !== "string") return res.code(400).send({ error: "etiqueta: texto (o null para quitarla)" });
    const m = await versiones.completar(id, n, { etiqueta: typeof etiqueta === "string" ? versiones.recortar(etiqueta, 80) : undefined });
    deps.avisar(id, { version: l.actual, versiones: true });
    return { ok: true, version: n, etiqueta: m.etiqueta ?? null };
  });

  app.post<{ Params: { id: string } }>("/api/flujos/:id/restaurar", async (req, res) => {
    const r = await restaurar(req.params.id, Number((req.body as { version?: unknown })?.version));
    avisarCambio(r);
    if (!r.ok) return responder(res, r);
    const { flujo: id, ...resto } = r;
    const f = flujos.obtener(id);
    return { ...resto, flujo: f ? { ...f, trabajador: undefined } : id };
  });

  app.post<{ Params: { id: string } }>("/api/flujos/:id/deshacer", async (req, res) => {
    const r = await deshacer(req.params.id, Boolean((req.body as { pedido?: unknown })?.pedido));
    avisarCambio(r);
    return responder(res, r);
  });

  app.post<{ Params: { id: string } }>("/api/flujos/:id/rehacer", async (req, res) => {
    const r = await rehacer(req.params.id);
    avisarCambio(r);
    return responder(res, r);
  });
}
