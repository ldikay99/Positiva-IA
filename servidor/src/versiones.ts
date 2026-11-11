import { AsyncLocalStorage } from "node:async_hooks";
import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { config } from "./config.ts";

/**
 * El historial de versiones de cada flujo, declarativo o de código: quién hizo cada versión (una persona o Vera),
 * cuándo, con qué etiqueta, qué cambió (en palabras) y de dónde viene (una restauración, deshacer o rehacer).
 * Las definiciones las guardan flujos.ts (declarativos) y estructuras.ts (pqrd, tutelas y sus copias); aquí
 * solo va lo que las acompaña, en <estado>/flujos_historial/<id>.json. Nada de aquí borra una versión.
 *
 * Quién guarda lo dice el pedido: Vera manda `x-autor: vera`, `x-pedido` (lo último que pidió la persona,
 * codificado como URI) y `x-pedido-id`. Cuando Vera guarda por un pedido nuevo, la versión de antes queda como
 * punto de restauración «antes de: <pedido>»: si algo sale mal, se vuelve ahí con una sola llamada.
 */

export type Autor = "persona" | "vera";
export interface Contexto {
  autor: Autor;
  pedido?: string;
  pedidoId?: string;
}

/** El autor del pedido en curso (lo fija un gancho del servidor para cada pedido HTTP). */
export const contexto = new AsyncLocalStorage<Contexto>();

export const recortar = (t: string, max = 120): string => {
  const s = t.replace(/\s+/g, " ").trim();
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
};

const decodificar = (x: unknown): string => {
  const t = Array.isArray(x) ? String(x[0] ?? "") : String(x ?? "");
  try {
    return decodeURIComponent(t).trim();
  } catch {
    return t.trim();
  }
};

/** Quién hace el pedido, por sus encabezados. Sin `x-autor: vera` es una persona. */
export function contextoDe(headers: Record<string, unknown>): Contexto {
  if (decodificar(headers["x-autor"]).toLowerCase() !== "vera") return { autor: "persona" };
  const pedido = recortar(decodificar(headers["x-pedido"]));
  const pedidoId = decodificar(headers["x-pedido-id"]).slice(0, 120);
  return { autor: "vera", ...(pedido ? { pedido } : {}), ...(pedidoId ? { pedidoId } : {}) };
}

export interface Meta {
  /** Sin autor: una versión de antes de este historial. */
  autor?: Autor;
  fecha?: number;
  resumen?: string;
  /** Lo que pidió la persona cuando Vera hizo esta versión. */
  pedido?: string;
  pedido_id?: string;
  /** Punto de restauración: «antes de: <pedido>» (el estado previo a un cambio de Vera). */
  punto?: string;
  /** La etiqueta que le puso una persona. */
  etiqueta?: string;
  /** Es una copia de esa versión (restaurar, deshacer o rehacer). */
  restaurada_de?: number;
  por?: "restaurar" | "deshacer" | "rehacer";
  /** Si vino de deshacer: la versión que se deshizo (a la que vuelve «rehacer»). */
  deshecha?: number;
  /** De código: esta versión es la estructura del código (sin editar). */
  del_codigo?: boolean;
}

interface Archivo {
  versiones: Record<string, Meta>;
}

const carpeta = () => join(config.datos, "flujos_historial");
const ruta = (id: string) => join(carpeta(), `${id}.json`);
const cache = new Map<string, Archivo>();
/** Las escrituras de un mismo flujo van en fila (dos guardados seguidos no se pisan). */
const filas = new Map<string, Promise<unknown>>();

async function leerArchivo(id: string): Promise<Archivo> {
  const c = cache.get(id);
  if (c) return c;
  let a: Archivo = { versiones: {} };
  try {
    const x = JSON.parse(await readFile(ruta(id), "utf8")) as Archivo;
    if (x && typeof x.versiones === "object") a = x;
  } catch {
    /* sin historial todavía */
  }
  cache.set(id, a);
  return a;
}

function enFila<T>(id: string, f: () => Promise<T>): Promise<T> {
  const antes = filas.get(id) ?? Promise.resolve();
  const p = antes.then(f, f);
  filas.set(id, p.catch(() => undefined));
  return p;
}

async function escribir(id: string, a: Archivo): Promise<void> {
  cache.set(id, a);
  await mkdir(carpeta(), { recursive: true });
  const tmp = `${ruta(id)}.tmp`;
  await writeFile(tmp, JSON.stringify(a, null, 2), "utf8");
  await rename(tmp, ruta(id));
}

/** Lo que acompaña a cada versión de un flujo ({número: meta}). */
export async function leer(id: string): Promise<Record<number, Meta>> {
  const a = await leerArchivo(id);
  return Object.fromEntries(Object.entries(a.versiones).map(([k, v]) => [Number(k), v]));
}

/** Mezcla datos en la meta de una versión (p. ej. por qué se creó o una etiqueta). */
export function completar(id: string, version: number, datos: Partial<Meta>): Promise<Meta> {
  return enFila(id, async () => {
    const a = structuredClone(await leerArchivo(id));
    const m = { ...(a.versiones[String(version)] ?? {}), ...datos };
    for (const k of Object.keys(m) as (keyof Meta)[]) if (m[k] === undefined || m[k] === null || m[k] === "") delete m[k];
    a.versiones[String(version)] = m;
    await escribir(id, a);
    return m;
  });
}

export async function borrar(id: string): Promise<void> {
  cache.delete(id);
  await unlink(ruta(id)).catch(() => undefined);
}

// ------------------------------------------------------------------ qué cambió

export interface Grafo {
  nombre?: string;
  descripcion?: string;
  modelo?: unknown;
  ia?: unknown;
  nodos: { id: string; tipo: string; nombre?: string; descripcion?: string; config?: Record<string, unknown> | null }[];
  aristas: { desde: string; hacia: string; cuando?: string }[];
  disenio?: Record<string, { x: number; y: number }>;
}

export interface CambioCampo {
  campo: string;
  antes: string;
  despues: string;
}

export interface Diferencias {
  iguales: boolean;
  resumen: string;
  /** Datos del flujo: nombre, descripción, IA. */
  datos: CambioCampo[];
  pasos: {
    agregados: { id: string; tipo: string; nombre: string }[];
    quitados: { id: string; tipo: string; nombre: string }[];
    cambiados: { id: string; tipo: string; nombre: string; cambios: CambioCampo[] }[];
    iguales: number;
  };
  conexiones: { agregadas: string[]; quitadas: string[] };
  /** Pasos que solo cambiaron de lugar en el lienzo. */
  movidos: number;
}

const texto = (v: unknown): string => {
  if (v === undefined || v === null) return "(sin valor)";
  const t = typeof v === "string" ? v : JSON.stringify(v);
  return t.length > 240 ? `${t.slice(0, 237)}…` : t;
};
const igual = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
const esObjeto = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);

/** La config de un paso como campos con punto (lectores.heic), para decir el cambio campo por campo. */
function aplanar(o: Record<string, unknown>, prefijo = "", nivel = 0, r: Record<string, unknown> = {}): Record<string, unknown> {
  for (const [k, v] of Object.entries(o)) {
    const clave = prefijo ? `${prefijo}.${k}` : k;
    if (esObjeto(v) && nivel < 2 && Object.keys(v).length) aplanar(v, clave, nivel + 1, r);
    else r[clave] = v;
  }
  return r;
}

export const claveArista = (a: { desde: string; hacia: string; cuando?: string }) => `${a.desde} → ${a.hacia}${a.cuando && a.cuando !== "*" ? ` (${a.cuando})` : ""}`;

/** Qué cambió de una versión (antes) a otra (después): pasos, conexiones, configuración y datos del flujo. */
export function diferencias(antes: Grafo, despues: Grafo): Diferencias {
  const enA = new Map(antes.nodos.map((n) => [n.id, n]));
  const enB = new Map(despues.nodos.map((n) => [n.id, n]));
  const ficha = (n: Grafo["nodos"][number]) => ({ id: n.id, tipo: n.tipo, nombre: n.nombre || n.id });
  const agregados = despues.nodos.filter((n) => !enA.has(n.id)).map(ficha);
  const quitados = antes.nodos.filter((n) => !enB.has(n.id)).map(ficha);
  const cambiados: Diferencias["pasos"]["cambiados"] = [];
  let iguales = 0;
  for (const b of despues.nodos) {
    const a = enA.get(b.id);
    if (!a) continue;
    const cambios: CambioCampo[] = [];
    for (const k of ["tipo", "nombre", "descripcion"] as const) if (!igual(a[k], b[k])) cambios.push({ campo: k, antes: texto(a[k]), despues: texto(b[k]) });
    const ca = aplanar(esObjeto(a.config) ? a.config : {}), cb = aplanar(esObjeto(b.config) ? b.config : {});
    for (const k of [...new Set([...Object.keys(ca), ...Object.keys(cb)])].sort())
      if (!igual(ca[k], cb[k])) cambios.push({ campo: k, antes: texto(ca[k]), despues: texto(cb[k]) });
    if (cambios.length) cambiados.push({ ...ficha(b), cambios });
    else iguales += 1;
  }
  const ka = new Set(antes.aristas.map(claveArista)), kb = new Set(despues.aristas.map(claveArista));
  const conexiones = { agregadas: [...kb].filter((k) => !ka.has(k)), quitadas: [...ka].filter((k) => !kb.has(k)) };
  const datos: CambioCampo[] = [];
  for (const k of ["nombre", "descripcion", "modelo", "ia"] as const)
    if ((antes[k] !== undefined || despues[k] !== undefined) && !igual(antes[k], despues[k])) datos.push({ campo: k, antes: texto(antes[k]), despues: texto(despues[k]) });
  let movidos = 0;
  for (const id of enB.keys()) {
    const pa = antes.disenio?.[id], pb = despues.disenio?.[id];
    if (pa && pb && (Math.round(pa.x) !== Math.round(pb.x) || Math.round(pa.y) !== Math.round(pb.y))) movidos += 1;
  }
  const d: Diferencias = { iguales: false, resumen: "", datos, pasos: { agregados, quitados, cambiados, iguales }, conexiones, movidos };
  d.resumen = resumir(d);
  d.iguales = !agregados.length && !quitados.length && !cambiados.length && !conexiones.agregadas.length && !conexiones.quitadas.length && !datos.length && !movidos;
  return d;
}

const lista = (xs: string[], max = 3) => (xs.length <= max ? xs.join(", ") : `${xs.slice(0, max).join(", ")} y ${xs.length - max} más`);

/** El cambio en una frase: «agregó «¿Hay documento?»; apagó «Revisor»; +2 conexiones». */
export function resumir(d: Diferencias): string {
  const r: string[] = [];
  const nombre = (x: { nombre: string }) => `«${x.nombre}»`;
  if (d.pasos.agregados.length) r.push(`agregó ${lista(d.pasos.agregados.map(nombre))}`);
  if (d.pasos.quitados.length) r.push(`quitó ${lista(d.pasos.quitados.map(nombre))}`);
  const apagados = d.pasos.cambiados.filter((c) => c.cambios.some((x) => x.campo === "deshabilitado" && x.despues === "true"));
  const encendidos = d.pasos.cambiados.filter((c) => c.cambios.some((x) => x.campo === "deshabilitado" && x.despues !== "true"));
  const otros = d.pasos.cambiados.filter((c) => !apagados.includes(c) && !encendidos.includes(c));
  if (apagados.length) r.push(`apagó ${lista(apagados.map(nombre))}`);
  if (encendidos.length) r.push(`encendió ${lista(encendidos.map(nombre))}`);
  if (otros.length) r.push(`cambió ${lista(otros.map((c) => `${nombre(c)} (${lista(c.cambios.map((x) => x.campo), 2)})`), 2)}`);
  const { agregadas, quitadas } = d.conexiones;
  if (agregadas.length || quitadas.length) {
    if (agregadas.length + quitadas.length <= 2) {
      if (agregadas.length) r.push(`conectó ${agregadas.join(", ")}`);
      if (quitadas.length) r.push(`desconectó ${quitadas.join(", ")}`);
    } else r.push(`conexiones: ${agregadas.length ? `+${agregadas.length}` : ""}${agregadas.length && quitadas.length ? " " : ""}${quitadas.length ? `−${quitadas.length}` : ""}`);
  }
  for (const c of d.datos) r.push(c.campo === "nombre" ? `renombró el flujo a «${c.despues}»` : c.campo === "descripcion" ? "cambió la descripción" : "cambió la IA del flujo");
  if (!r.length && d.movidos) r.push(`movió ${d.movidos} paso${d.movidos === 1 ? "" : "s"} en el lienzo`);
  const t = r.join("; ");
  return t ? recortar(t[0]!.toUpperCase() + t.slice(1), 300) : "Sin cambios en pasos ni conexiones";
}

// ------------------------------------------------------------------ anotar un guardado

/**
 * Lo que acompaña a una versión recién guardada: autor (del pedido en curso), fecha y resumen del cambio.
 * Si la guardó Vera por un pedido nuevo, la versión anterior queda como punto «antes de: <pedido>».
 */
export function anotar(id: string, version: number, datos: { antes?: Grafo | null; despues?: Grafo | null; nota?: string; previa?: number | null; extra?: Partial<Meta> }): Promise<Meta> {
  const ctx = contexto.getStore() ?? { autor: "persona" as const };
  return enFila(id, async () => {
    const a = structuredClone(await leerArchivo(id));
    const resumen = datos.antes && datos.despues ? resumir(diferencias(datos.antes, datos.despues)) : datos.nota ? recortar(datos.nota, 300) : undefined;
    const m: Meta = {
      autor: ctx.autor, fecha: Date.now(), ...(resumen ? { resumen } : {}),
      ...(ctx.autor === "vera" && ctx.pedido ? { pedido: ctx.pedido } : {}), ...(ctx.autor === "vera" && ctx.pedidoId ? { pedido_id: ctx.pedidoId } : {}),
      ...(datos.extra ?? {}),
    };
    a.versiones[String(version)] = { ...(a.versiones[String(version)] ?? {}), ...m };
    if (ctx.autor === "vera" && ctx.pedido && datos.previa !== null && datos.previa !== undefined) {
      const pm = a.versiones[String(datos.previa)];
      const mismoPedido = pm?.autor === "vera" && (ctx.pedidoId ? pm.pedido_id === ctx.pedidoId : pm.pedido === ctx.pedido);
      if (!mismoPedido) a.versiones[String(datos.previa)] = { ...(pm ?? {}), punto: `antes de: ${ctx.pedido}` };
    }
    await escribir(id, a);
    return a.versiones[String(version)]!;
  });
}

// ------------------------------------------------------------------ deshacer y rehacer

/**
 * La versión a la que lleva «deshacer» desde v: la anterior, salvo que v venga de deshacer o rehacer
 * (entonces la anterior a la que copia). Así, deshacer dos veces seguidas retrocede dos cambios.
 */
export function anteriorA(v: number, metas: Record<number, Meta>, existe: (n: number) => boolean): number | null {
  const vistos = new Set<number>();
  let x = v;
  while (!vistos.has(x)) {
    vistos.add(x);
    const m = metas[x];
    if (m && (m.por === "deshacer" || m.por === "rehacer") && typeof m.restaurada_de === "number") x = m.restaurada_de;
    else break;
  }
  for (let n = x - 1; n >= 0; n--) if (existe(n)) return n;
  return null;
}

/** Hasta dónde lleva deshacer todo el último pedido de Vera: la versión anterior a sus cambios de ese pedido. */
export function antesDelPedido(v: number, metas: Record<number, Meta>, existe: (n: number) => boolean): number | null {
  const m = metas[v];
  if (m?.autor !== "vera" || (!m.pedido && !m.pedido_id)) return anteriorA(v, metas, existe);
  const mismo = (x?: Meta) => x?.autor === "vera" && (m.pedido_id ? x.pedido_id === m.pedido_id : x.pedido === m.pedido);
  let x: number | null = v;
  let objetivo: number | null = null;
  while (x !== null && mismo(metas[x])) {
    objetivo = anteriorA(x, metas, existe);
    x = objetivo;
  }
  return objetivo;
}

// ------------------------------------------------------------------ versiones y corridas

export interface CorridaBreve {
  creada: number;
  estado: string;
}

/** Lo que el servidor le da a este módulo: las corridas de un flujo (con su estado legible). */
export const enlace: { corridasDe: (id: string) => CorridaBreve[] } = { corridasDe: () => [] };

/** Cuántas corridas terminaron bien, a revisar o con fallo mientras cada versión estaba en uso. */
export function corridasPorVersion(id: string, versiones: { version: number; fecha: number }[]): Map<number, { bien: number; revisar: number; fallo: number; ultima_bien: number | null }> {
  const orden = [...versiones].sort((a, b) => a.fecha - b.fecha || a.version - b.version);
  const r = new Map<number, { bien: number; revisar: number; fallo: number; ultima_bien: number | null }>();
  for (const v of orden) r.set(v.version, { bien: 0, revisar: 0, fallo: 0, ultima_bien: null });
  for (const c of enlace.corridasDe(id)) {
    let activa: number | null = null;
    for (const v of orden) if (v.fecha <= c.creada) activa = v.version;
    if (activa === null) continue;
    const x = r.get(activa)!;
    if (c.estado === "bien") {
      x.bien += 1;
      x.ultima_bien = Math.max(x.ultima_bien ?? 0, c.creada);
    } else if (c.estado === "revisar") x.revisar += 1;
    else if (c.estado === "fallo") x.fallo += 1;
  }
  return r;
}
