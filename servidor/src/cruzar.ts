import type { FastifyInstance } from "fastify";
import { z } from "zod";
import * as flujos from "./flujos.ts";
import * as estructuras from "./estructuras.ts";
import * as entornos from "./entornos.ts";

/**
 * Operaciones entre flujos («cruzar flujos»): compararlos y traer pasos de uno a otro.
 *
 *   GET  /api/flujos/:id/comparar?con=<otro>  → qué nodos, conexiones y configuración difieren
 *   POST /api/flujos/:id/traer {desde, nodos, entre?, aristas?, quitar?, renombrar?, simular?, nota?}
 *        → copia esos nodos de «desde» a :id con sus conexiones internas, renombra los ids que chocan,
 *          los conecta donde se pide, valida y guarda una versión nueva (el origen no se toca).
 *
 * Sirve para declarativos y para los de código (pqrd, tutelas y sus copias): en estos lo que se trae
 * queda dentro de un punto de inserción y lo valida estructura_codigo.py, igual que agregar un paso.
 * Las etapas del motor no se copian (son el código); los disparadores tampoco si el destino ya tiene uno.
 */

type Nodo = { id: string; tipo: string; nombre?: string; descripcion?: string; config?: Record<string, unknown> };
type Arista = { desde: string; hacia: string; cuando?: string };

export interface Grafo {
  id: string;
  nombre: string;
  tipo: "codigo" | "declarativo";
  base?: string;
  nodos: Nodo[];
  aristas: Arista[];
  /** La definición completa (solo declarativos), para guardarla con lo traído. */
  definicion?: Record<string, unknown>;
}

const ID_NODO = /^[a-z][a-z0-9_]{1,40}$/;
const TERMINALES = new Set(["salida", "fin_ciclo", "detener_con_alerta"]);

export async function grafoDe(id: string): Promise<Grafo | null> {
  const f = flujos.obtener(id);
  if (!f) return null;
  if (f.tipo === "codigo") {
    const base = flujos.baseDe(id) ?? "pqrd";
    const v = await estructuras.vista(id, base);
    return { id, nombre: f.nombre, tipo: "codigo", base, nodos: v.definicion.nodos as Nodo[], aristas: v.definicion.aristas };
  }
  const d = structuredClone(f.definicion!) as unknown as Record<string, unknown> & { nodos: Nodo[]; aristas: Arista[] };
  return { id, nombre: f.nombre, tipo: "declarativo", nodos: d.nodos, aristas: d.aristas, definicion: d };
}

const clave = (a: Arista) => `${a.desde}→${a.hacia}${a.cuando && a.cuando !== "*" ? ` [${a.cuando}]` : ""}`;
const corto = (v: unknown) => {
  const t = v === undefined ? "(sin valor)" : JSON.stringify(v);
  return t.length > 220 ? `${t.slice(0, 217)}…` : t;
};
const igual = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** Qué difiere entre dos flujos: nodos (solo en uno, distintos), conexiones y datos del flujo. */
export function comparar(a: Grafo, b: Grafo) {
  const enB = new Map(b.nodos.map((n) => [n.id, n]));
  const enA = new Map(a.nodos.map((n) => [n.id, n]));
  const soloA = a.nodos.filter((n) => !enB.has(n.id));
  const soloB = b.nodos.filter((n) => !enA.has(n.id));
  const distintos: { id: string; nombre: string; cambios: { campo: string; a: string; b: string }[] }[] = [];
  let iguales = 0;
  for (const x of a.nodos) {
    const y = enB.get(x.id);
    if (!y) continue;
    const cambios: { campo: string; a: string; b: string }[] = [];
    for (const k of ["tipo", "nombre", "descripcion"] as const) if (!igual(x[k], y[k])) cambios.push({ campo: k, a: corto(x[k]), b: corto(y[k]) });
    const ca = x.config ?? {}, cb = y.config ?? {};
    for (const k of [...new Set([...Object.keys(ca), ...Object.keys(cb)])].sort())
      if (!igual(ca[k], cb[k])) cambios.push({ campo: `config.${k}`, a: corto(ca[k]), b: corto(cb[k]) });
    if (cambios.length) distintos.push({ id: x.id, nombre: x.nombre ?? x.id, cambios });
    else iguales += 1;
  }
  // un nodo que solo está en uno pero con el mismo tipo y nombre que otro del otro: quizá solo cambió de id
  const parecidos = soloA.flatMap((x) => soloB.filter((y) => y.tipo === x.tipo && (y.nombre ?? "").toLowerCase() === (x.nombre ?? "").toLowerCase()).map((y) => ({ en_a: x.id, en_b: y.id, tipo: x.tipo })));
  const ka = new Set(a.aristas.map(clave)), kb = new Set(b.aristas.map(clave));
  const flujo: { campo: string; a: string; b: string }[] = [];
  if (a.nombre !== b.nombre) flujo.push({ campo: "nombre", a: a.nombre, b: b.nombre });
  if (a.tipo !== b.tipo || a.base !== b.base) flujo.push({ campo: "tipo", a: a.base ? `código (${a.base})` : a.tipo, b: b.base ? `código (${b.base})` : b.tipo });
  for (const k of ["descripcion", "modelo", "ia"]) {
    const va = a.definicion?.[k], vb = b.definicion?.[k];
    if ((va !== undefined || vb !== undefined) && !igual(va, vb)) flujo.push({ campo: k, a: corto(va), b: corto(vb) });
  }
  const resumen = [
    soloA.length ? `${soloA.length} paso(s) solo en «${a.nombre}»` : "",
    soloB.length ? `${soloB.length} paso(s) solo en «${b.nombre}»` : "",
    distintos.length ? `${distintos.length} paso(s) con distinta configuración` : "",
    ka.size !== kb.size || [...ka].some((k) => !kb.has(k)) ? "conexiones distintas" : "",
  ].filter(Boolean);
  const ficha = (n: Nodo) => ({ id: n.id, tipo: n.tipo, nombre: n.nombre ?? n.id });
  return {
    a: { id: a.id, nombre: a.nombre, tipo: a.tipo, ...(a.base ? { base: a.base } : {}), nodos: a.nodos.length, aristas: a.aristas.length },
    b: { id: b.id, nombre: b.nombre, tipo: b.tipo, ...(b.base ? { base: b.base } : {}), nodos: b.nodos.length, aristas: b.aristas.length },
    iguales: !resumen.length && !flujo.length,
    resumen: resumen.length ? resumen.join("; ") : flujo.length ? "los mismos pasos y conexiones; cambian datos del flujo" : "son iguales en pasos, conexiones y configuración",
    flujo,
    nodos: { solo_en_a: soloA.map(ficha), solo_en_b: soloB.map(ficha), distintos, iguales, parecidos },
    aristas: { solo_en_a: a.aristas.filter((x) => !kb.has(clave(x))), solo_en_b: b.aristas.filter((x) => !ka.has(clave(x))) },
  };
}

export const pedidoTraer = z.object({
  desde: z.string().min(1),
  nodos: z.array(z.string().regex(ID_NODO, "nodos: ids de pasos del flujo de origen")).min(1).max(60),
  entre: z.tuple([z.string(), z.string()]).optional(),
  aristas: z.array(z.object({ desde: z.string(), hacia: z.string(), cuando: z.string().optional() })).max(100).optional(),
  quitar: z.array(z.object({ desde: z.string(), hacia: z.string() })).max(100).optional(),
  renombrar: z.record(z.string(), z.string().regex(ID_NODO, "renombrar: ids nuevos en minúsculas con guion bajo")).optional(),
  simular: z.boolean().optional(),
  nota: z.string().max(300).optional(),
});
export type PedidoTraer = z.infer<typeof pedidoTraer>;

function idLibre(base: string, ocupados: Set<string>): string {
  const raiz = base.slice(0, 36);
  for (let i = 2; i < 1000; i++) {
    const c = `${raiz}_${i}`;
    if (!ocupados.has(c)) return c;
  }
  return `${raiz.slice(0, 30)}_${Date.now().toString(36).slice(-6)}`;
}

/** Cambia en la config los textos que son exactamente un id copiado (p. ej. la lista de ramas de un «unir»). */
function renombrarEnConfig(v: unknown, mapa: Record<string, string>): unknown {
  if (typeof v === "string") return mapa[v] ?? v;
  if (Array.isArray(v)) return v.map((x) => renombrarEnConfig(x, mapa));
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, renombrarEnConfig(x, mapa)]));
  return v;
}

export interface ResultadoTraer {
  ok: boolean;
  codigo?: number;
  error?: string;
  problemas?: string[];
  guardado?: boolean;
  version?: number;
  traidos?: { de: string; a: string; tipo: string; nombre: string }[];
  renombrados?: Record<string, string>;
  aristas_nuevas?: Arista[];
  quitadas?: Arista[];
  avisos?: string[];
  definicion?: { nodos: Nodo[]; aristas: Arista[] };
  tramos?: unknown;
}

/** Arma el destino con los pasos traídos (sin guardar). */
export function armarTraida(origen: Grafo, destino: Grafo, p: PedidoTraer, disparadores: Set<string>, entornoDestino: string[]): ResultadoTraer & { nodos?: Nodo[]; aristas?: Arista[] } {
  const porId = new Map(origen.nodos.map((n) => [n.id, n]));
  const faltan = p.nodos.filter((x) => !porId.has(x));
  if (faltan.length) return { ok: false, codigo: 400, error: `«${origen.nombre}» no tiene estos pasos: ${faltan.join(", ")}`, problemas: [`pasos de «${origen.id}»: ${origen.nodos.map((n) => n.id).join(", ")}`] };
  const elegidos = [...new Set(p.nodos)].map((x) => porId.get(x)!);
  const etapas = elegidos.filter((n) => n.tipo === "etapa");
  if (etapas.length) return { ok: false, codigo: 400, error: `${etapas.map((n) => n.id).join(", ")} son etapas del motor de ${origen.base ?? "código"}: no se copian (en el destino se apagan o se configuran con editar_nodo). Se traen los pasos insertados y los nodos declarativos.` };
  const tieneDisparador = destino.nodos.some((n) => disparadores.has(n.tipo)) || destino.tipo === "codigo";
  const disp = elegidos.filter((n) => disparadores.has(n.tipo));
  if (disp.length && tieneDisparador) return { ok: false, codigo: 400, error: `${disp.map((n) => n.id).join(", ")} es un disparador y «${destino.nombre}» ya tiene el suyo: un flujo arranca de una sola forma` };

  const ocupados = new Set(destino.nodos.map((n) => n.id));
  const mapa: Record<string, string> = {};
  for (const n of elegidos) {
    const pedido = p.renombrar?.[n.id];
    if (pedido && ocupados.has(pedido)) return { ok: false, codigo: 400, error: `renombrar: «${pedido}» ya existe en «${destino.nombre}»` };
    const nuevo = pedido ?? (ocupados.has(n.id) ? idLibre(n.id, ocupados) : n.id);
    mapa[n.id] = nuevo;
    ocupados.add(nuevo);
  }
  const nuevos: Nodo[] = elegidos.map((n) => ({ ...structuredClone(n), id: mapa[n.id]!, config: renombrarEnConfig(structuredClone(n.config ?? {}), mapa) as Record<string, unknown> }));
  const internas: Arista[] = origen.aristas.filter((a) => a.desde in mapa && a.hacia in mapa).map((a) => ({ ...a, desde: mapa[a.desde]!, hacia: mapa[a.hacia]! }));
  const aristas = destino.aristas.map((a) => ({ ...a }));
  const quitadas: Arista[] = [];
  const quitar = (desde: string, hacia: string) => {
    for (let i = aristas.length - 1; i >= 0; i--) if (aristas[i]!.desde === desde && aristas[i]!.hacia === hacia) quitadas.push(...aristas.splice(i, 1));
  };
  for (const q of p.quitar ?? []) quitar(q.desde, q.hacia);
  const nuevasAristas: Arista[] = [...internas];
  const ids = new Set(nuevos.map((n) => n.id));
  if (p.entre) {
    const [desde, hacia] = p.entre;
    const vieja = aristas.find((a) => a.desde === desde && a.hacia === hacia);
    if (!vieja) return { ok: false, codigo: 400, error: `«${destino.nombre}» no tiene la conexión ${desde} → ${hacia} donde insertar`, problemas: [`conexiones que salen de ${desde}: ${aristas.filter((a) => a.desde === desde).map((a) => a.hacia).join(", ") || "ninguna"}`] };
    const entradas = nuevos.filter((n) => !internas.some((a) => a.hacia === n.id));
    if (entradas.length !== 1) return { ok: false, codigo: 400, error: `con «entre» los pasos traídos deben tener un solo comienzo y tienen ${entradas.length} (${entradas.map((n) => n.id).join(", ")}): manda las conexiones en «aristas»` };
    quitar(desde, hacia);
    nuevasAristas.push({ desde, hacia: entradas[0]!.id, ...(vieja.cuando ? { cuando: vieja.cuando } : {}) });
    // cada paso que no termina y no tiene salida por defecto dentro de lo traído sigue hacia «hacia»
    for (const n of nuevos) {
      if (TERMINALES.has(n.tipo)) continue;
      const salen = internas.filter((a) => a.desde === n.id);
      if (!salen.some((a) => !a.cuando || a.cuando === "*")) nuevasAristas.push({ desde: n.id, hacia });
    }
  }
  for (const a of p.aristas ?? []) {
    // se aceptan los ids de origen de lo traído (se traducen) además de los nuevos
    const traducir = (x: string) => (ids.has(x) || destino.nodos.some((n) => n.id === x) ? x : mapa[x] ?? x);
    nuevasAristas.push({ desde: traducir(a.desde), hacia: traducir(a.hacia), ...(a.cuando ? { cuando: a.cuando } : {}) });
  }
  const vistas = new Set(aristas.map(clave));
  for (const a of nuevasAristas) if (!vistas.has(clave(a))) { aristas.push(a); vistas.add(clave(a)); }

  const avisos: string[] = [];
  const texto = JSON.stringify(nuevos.map((n) => n.config));
  const faltanVars = [...new Set([...texto.matchAll(/\{\{\s*entorno\.([A-Z][A-Z0-9_]*)/g)].map((m) => m[1]!))].filter((v) => !entornoDestino.includes(v));
  if (faltanVars.length) avisos.push(`usa variables de entorno que «${destino.nombre}» no tiene: ${faltanVars.join(", ")} (defínelas en su Entorno)`);
  const creds = [...new Set(nuevos.map((n) => n.config?.credencial).filter((c): c is string => typeof c === "string" && !!c))];
  if (creds.length) avisos.push(`usa la(s) conexión(es) ${creds.join(", ")}: revisa que «${destino.nombre}» pueda usarlas`);
  if (!p.entre && !(p.aristas ?? []).length) avisos.push("no se pidió dónde conectarlos: sin «entre» ni «aristas» quedan sueltos y la validación lo dirá");
  const renombrados = Object.fromEntries(Object.entries(mapa).filter(([k, v]) => k !== v));
  return {
    ok: true, nodos: [...destino.nodos, ...nuevos], aristas,
    traidos: elegidos.map((n) => ({ de: n.id, a: mapa[n.id]!, tipo: n.tipo, nombre: n.nombre ?? n.id })),
    renombrados, aristas_nuevas: nuevasAristas, quitadas, avisos,
  };
}

/** Ubica lo traído en el lienzo del destino: a la derecha de lo que hay, con la forma que tenía en el origen. */
function conDisenio(destino: Record<string, unknown>, origen: Record<string, unknown> | undefined, traidos: { de: string; a: string }[]): void {
  const dd = (destino.disenio ?? {}) as Record<string, { x: number; y: number }>;
  const od = (origen?.disenio ?? {}) as Record<string, { x: number; y: number }>;
  const pos = traidos.map((t) => od[t.de]).filter(Boolean) as { x: number; y: number }[];
  if (!pos.length || !Object.keys(dd).length) return;
  const maxX = Math.max(...Object.values(dd).map((p) => p.x));
  const minY = Math.min(...Object.values(dd).map((p) => p.y));
  const ox = Math.min(...pos.map((p) => p.x)), oy = Math.min(...pos.map((p) => p.y));
  for (const t of traidos) {
    const p = od[t.de];
    if (p) dd[t.a] = { x: maxX + 320 + (p.x - ox), y: minY + (p.y - oy) };
  }
  destino.disenio = dd;
}

export async function traer(destinoId: string, p: PedidoTraer): Promise<ResultadoTraer> {
  const origen = await grafoDe(p.desde);
  if (!origen) return { ok: false, codigo: 404, error: `no existe el flujo de origen «${p.desde}»` };
  const destino = await grafoDe(destinoId);
  if (!destino) return { ok: false, codigo: 404, error: `no existe el flujo «${destinoId}»` };
  const r = armarTraida(origen, destino, p, flujos.DISPARADORES, entornos.nombresDeCache(destinoId));
  if (!r.ok) return r;
  const { nodos, aristas, ...info } = r;
  const nota = p.nota ?? `pasos traídos de «${origen.nombre}»: ${info.traidos!.map((t) => t.a).join(", ")}`;
  if (destino.tipo === "codigo") {
    const def = { nodos: nodos!, aristas: aristas! };
    if (p.simular) {
      const v = await estructuras.validar(destino.base!, def, entornos.nombresDeCache(destinoId));
      return { ...info, ok: v.ok, guardado: false, problemas: v.problemas, definicion: def, tramos: v.tramos };
    }
    const g = await estructuras.guardar(destinoId, destino.base!, def, nota, entornos.nombresDeCache(destinoId));
    if (!g.ok || !g.guardada) return { ...info, ok: false, codigo: 400, error: "lo traído no cabe así en el flujo de código", problemas: g.problemas, guardado: false };
    return { ...info, ok: true, guardado: true, version: g.guardada.version, tramos: g.tramos };
  }
  const def = { ...destino.definicion!, nodos: nodos!, aristas: aristas! };
  conDisenio(def, origen.definicion, info.traidos!);
  if (p.simular) {
    const v = await flujos.validar(def);
    return { ...info, ok: v.ok, guardado: false, problemas: v.problemas, definicion: { nodos: nodos!, aristas: aristas! } };
  }
  const g = await flujos.guardar(def, nota);
  if (!g.ok || !g.flujo) return { ...info, ok: false, codigo: 400, error: "el flujo con lo traído no es válido", problemas: g.problemas, guardado: false };
  return { ...info, ok: true, guardado: true, version: g.flujo.version };
}

export interface Dependencias {
  avisarFlujo: (id: string, extra?: Record<string, unknown>) => void;
}

export async function registrar(app: FastifyInstance, deps: Dependencias): Promise<void> {
  app.get<{ Params: { id: string }; Querystring: { con?: string } }>("/api/flujos/:id/comparar", async (req, res) => {
    const con = String(req.query.con ?? "");
    if (!con) return res.code(400).send({ error: "manda ?con=<id del otro flujo>" });
    const a = await grafoDe(req.params.id), b = await grafoDe(con);
    if (!a) return res.code(404).send({ error: `no existe el flujo «${req.params.id}»` });
    if (!b) return res.code(404).send({ error: `no existe el flujo «${con}»` });
    return comparar(a, b);
  });

  app.post<{ Params: { id: string } }>("/api/flujos/:id/traer", async (req, res) => {
    const pedido = pedidoTraer.safeParse(req.body ?? {});
    if (!pedido.success) return res.code(400).send({ error: "pedido inválido", problemas: pedido.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`) });
    const r = await traer(req.params.id, pedido.data);
    if (!r.ok && !pedido.data.simular) return res.code(r.codigo ?? 400).send(r);
    if (r.guardado) deps.avisarFlujo(req.params.id, { version: r.version, ...(flujos.esDeCodigo(req.params.id) ? { estructura: true } : {}), traido_de: pedido.data.desde });
    return r;
  });
}
