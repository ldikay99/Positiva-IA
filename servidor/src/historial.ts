import { spawn } from "node:child_process";
import { createReadStream } from "node:fs";
import { resolve } from "node:path";
import type { FastifyInstance } from "fastify";
import { config } from "./config.ts";
import type { Corrida, Evento, Motor } from "./motor.ts";
import * as flujos from "./flujos.ts";
import * as bandeja from "./bandeja.ts";
import * as configuracion from "./configuracion.ts";
import * as espacio from "./espacio.ts";

/**
 * Historial de ejecuciones por flujo, con filtros, búsqueda y un detalle que se lee
 * sin saber cómo está hecho el motor: qué entró, qué hizo cada paso, qué salió y por
 * qué terminó así. Y «Reintentar»: vuelve a correr con la misma entrada o el mismo caso.
 *
 *   GET  /api/historial?flujo=&estado=&desenlace=&desde=&hasta=&q=&limite=&desde_n=
 *   GET  /api/historial/:corrida
 *   POST /api/historial/:corrida/reintentar
 */

export interface Dependencias {
  /** El pool de un flujo (lo crea si hace falta, sin arrancar trabajadores). */
  motorDe: (id: string, arrancar?: boolean) => Motor | null;
  /** Todos los pools que existen. */
  motores: () => Iterable<Motor>;
}

/** Cómo quedó una ejecución, en una palabra que entiende cualquiera. */
export type EstadoLegible = "en_curso" | "bien" | "revisar" | "fallo";

const FALLAS = new Set(["error", "cancelada", "colgado"]);
/** Necesitan a una persona: revisión manual, esperar una aprobación, detenerse con alerta. */
const A_REVISAR = new Set(["manual", "aplazado", "sin_reclamar", "sin_tomar", "en_espera", "detenido"]);

export function estadoLegible(c: Pick<Corrida, "estado" | "resultado">): EstadoLegible {
  if (!c.resultado) return c.estado === "fallida" ? "fallo" : "en_curso";
  const a = String(c.resultado.accion ?? "");
  if (FALLAS.has(a) || c.estado === "fallida") return "fallo";
  if (A_REVISAR.has(a)) return "revisar";
  return "bien";
}

// ---- aprobación humana: la ejecución que se pausó y la que siguió después de la decisión

/** Las ejecuciones de un flujo (lo fija registrar). */
let delFlujo: (flujo: string) => Corrida[] = () => [];
/** La aprobación por la que se pausó una ejecución. */
const pausadaPor = (c: Corrida): string | null => {
  const id = (c.resultado?.aprobacion as { id?: unknown } | undefined)?.id;
  return c.resultado?.accion === "en_espera" && typeof id === "string" ? id : null;
};
/** La aprobación tras la que sigue una ejecución. */
const sigueA = (c: Corrida): string | null => {
  const id = (c.entrada as { aprobacion?: unknown } | undefined)?.aprobacion;
  return c.origen === "aprobacion" && typeof id === "string" ? id : null;
};
function relacion(c: Corrida): { viene_de: Corrida | null; continuada_en: Corrida | null } {
  const aid = sigueA(c);
  const pid = pausadaPor(c);
  if (!aid && !pid) return { viene_de: null, continuada_en: null };
  const todas = delFlujo(c.flujo);
  return {
    viene_de: aid ? todas.find((x) => x.id !== c.id && pausadaPor(x) === aid) ?? null : null,
    continuada_en: pid ? todas.find((x) => x.id !== c.id && sigueA(x) === pid) ?? null : null,
  };
}

/** El título de una ejecución: el asunto del correo, sus primeros datos o la fecha. */
function tituloBase(c: Corrida): string {
  const r = c.resultado;
  const inicio = c.eventos.find((e) => e.tipo === "inicio");
  const correo = (r?.variables as { correo?: { asunto?: unknown } } | undefined)?.correo;
  const asunto = String(r?.asunto || inicio?.asunto || correo?.asunto || "").trim();
  return asunto || (sigueA(c) ? "" : tituloDeEntrada(c)) || (/^[a-z_]+-\d{14}-/.test(c.caso) ? `Ejecución del ${new Date(c.creada).toLocaleString("es-CO", { timeZone: "America/Bogota", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}` : c.caso);
}

/** Lo que hace falta de una corrida para listarla (sin sus eventos). */
function fila(c: Corrida) {
  const r = c.resultado;
  const rel = relacion(c);
  const decidida = rel.continuada_en;
  return {
    id: c.id,
    flujo: c.flujo,
    caso: c.caso,
    titulo: rel.viene_de ? tituloBase(rel.viene_de) : tituloBase(c),
    // una ejecución pausada que ya se decidió no espera nada: se ve como lo que pasó después
    estado: decidida ? estadoLegible(decidida) : estadoLegible(c),
    desenlace: r ? String(r.accion ?? "") : null,
    motivo: decidida ? "Ya se decidió: siguió en otra ejecución" : r ? String(r.motivo ?? "") : "",
    origen: c.origen ?? (c.lote ? "lote" : "bandeja"),
    creada: c.creada,
    segundos: typeof r?.segundos === "number" ? r.segundos : c.fin && c.inicio ? Math.round((c.fin - c.inicio) / 100) / 10 : null,
    reintento_de: (c as Corrida & { reintento_de?: string }).reintento_de ?? null,
    viene_de: rel.viene_de?.id ?? null,
    continuada_en: decidida?.id ?? null,
  };
}

/** Una frase para reconocer una ejecución sin correo: sus primeros datos de entrada. */
function tituloDeEntrada(c: Corrida): string {
  const e = c.entrada;
  if (!e || typeof e !== "object") return "";
  // Primero lo que suele nombrar un caso; si no, los primeros datos (también dentro de un objeto, p. ej. el cuerpo de un webhook).
  const PREFERIDOS = /^(nombre|asunto|titulo|título|documento|cedula|cédula|id|referencia|correo|email)$/i;
  const planos: [string, string][] = [];
  const recorrer = (o: Record<string, unknown>, nivel: number) => {
    for (const [k, v] of Object.entries(o)) {
      if (k === "encabezados" || k === "consulta" || k === "metodo") continue;
      if (k === "archivos" && Array.isArray(v)) planos.push([k, `${v.length} archivo${v.length === 1 ? "" : "s"}`]);
      else if (typeof v === "string" && v.trim()) planos.push([k, v.trim().slice(0, 50)]);
      else if (typeof v === "number" || typeof v === "boolean") planos.push([k, String(v)]);
      else if (Array.isArray(v)) planos.push([k, `${v.length} elemento${v.length === 1 ? "" : "s"}`]);
      else if (v && typeof v === "object" && nivel < 2) recorrer(v as Record<string, unknown>, nivel + 1);
    }
  };
  recorrer(e as Record<string, unknown>, 0);
  const orden = [...planos.filter(([k]) => PREFERIDOS.test(k)), ...planos.filter(([k]) => !PREFERIDOS.test(k))];
  return orden.slice(0, 2).map(([k, v]) => (k === "archivos" ? v : `${humano(k)}: ${v}`)).join(", ");
}

/** Quién escribió y cuándo, de lo que dejó la ejecución (sin el cuerpo ni los adjuntos). */
function correoDe(c: Corrida): { nombre: string; valor: string }[] {
  const inicio = c.eventos.find((e) => e.tipo === "inicio");
  const v = (c.resultado?.variables as { correo?: Record<string, unknown> } | undefined)?.correo ?? {};
  const remitente = String(v.remitente_nombre || v.remitente || inicio?.remitente || "").trim();
  const fecha = String(v.fecha || v.recibido || "").trim();
  const adjuntos = Array.isArray(v.adjuntos) ? v.adjuntos.length : null;
  return [
    ...(remitente ? [{ nombre: "Remitente", valor: remitente.slice(0, 160) }] : []),
    ...(fecha ? [{ nombre: "Recibido", valor: fecha.slice(0, 60) }] : []),
    ...(adjuntos !== null ? [{ nombre: "Adjuntos", valor: String(adjuntos) }] : []),
  ];
}

const quitarTildes = (t: string) => t.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();

function todasDe(deps: Dependencias, flujo?: string): Corrida[] {
  const lista: Corrida[] = [];
  if (flujo) {
    const m = deps.motorDe(flujo, false);
    if (m) lista.push(...m.corridas.values());
  } else for (const m of deps.motores()) lista.push(...m.corridas.values());
  return lista.sort((a, b) => b.creada - a.creada);
}

function buscarCorrida(deps: Dependencias, id: string): { m: Motor; c: Corrida } | null {
  for (const m of deps.motores()) {
    const c = m.corridas.get(id);
    if (c) return { m, c };
  }
  return null;
}

// ------------------------------------------------------------------ detalle

/** «leer_adjuntos» → «Leer adjuntos». */
const humano = (id: string) => {
  const t = id.replace(/[_-]+/g, " ").trim();
  return t ? t[0]!.toUpperCase() + t.slice(1) : id;
};

/** Un valor cualquiera, corto y legible (nada de JSON gigante). */
function corto(v: unknown, max = 240): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v.length > max ? `${v.slice(0, max)}…` : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) {
    if (!v.length) return "(lista vacía)";
    if (v.every((x) => typeof x !== "object")) return corto(v.map(String).join(", "), max);
    return `${v.length} elemento${v.length === 1 ? "" : "s"}`;
  }
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if ("valor" in o && Object.keys(o).length <= 6) return corto(o.valor, max);
    const k = Object.keys(o);
    if (!k.length) return "(vacío)";
    return corto(k.slice(0, 6).map((x) => `${x}: ${corto(o[x], 60)}`).join(" · "), max);
  }
  return String(v);
}

/** Lo técnico de una entrada que no le dice nada a quien lee (encabezados HTTP, método). */
const TECNICOS = new Set(["encabezados", "metodo", "consulta"]);
const esPlano = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);

/**
 * Lo de un objeto como lista de campos {nombre, valor} para mostrar. Un objeto pequeño
 * adentro (el cuerpo de un webhook) se abre en sus campos: «Cuerpo › Documento».
 */
function campos(o: unknown, max = 24): { nombre: string; valor: string }[] {
  if (!esPlano(o)) return [];
  const r: { nombre: string; valor: string }[] = [];
  const vale = (k: string, v: unknown) => !k.startsWith("_") && v !== undefined && v !== null && v !== "";
  for (const [k, v] of Object.entries(o)) {
    if (!vale(k, v) || (TECNICOS.has(k) && (!esPlano(v) || !Object.keys(v).length || k === "encabezados"))) continue;
    const hijos = esPlano(v) ? Object.entries(v).filter(([k2, v2]) => vale(k2, v2)) : [];
    if (esPlano(v) && hijos.length && hijos.length <= 10 && !("valor" in v)) for (const [k2, v2] of hijos) r.push({ nombre: `${humano(k)} › ${humano(k2)}`, valor: corto(v2) });
    else r.push({ nombre: humano(k), valor: corto(v) });
    if (r.length >= max) break;
  }
  return r.slice(0, max);
}

interface Paso {
  id: string;
  nombre: string;
  tipo: string | null;
  estado: "hecho" | "fallo" | "en_curso";
  /** Por dónde siguió (rama), en palabras. */
  salida: string;
  ms: number;
  veces: number;
  notas: string[];
  datos: { nombre: string; valor: string }[];
  fallo: string;
}

export function detalle(c: Corrida) {
  const f = flujos.obtener(c.flujo);
  const cat = flujos.catalogoDe(c.flujo);
  const nombres = new Map<string, { nombre: string; tipo: string | null }>();
  for (const n of cat?.nodos ?? []) {
    nombres.set(n.id, { nombre: n.nombre, tipo: (n as { tipo_declarativo?: string }).tipo_declarativo ?? null });
    if (n.etapa_cronometro && !nombres.has(n.etapa_cronometro)) nombres.set(n.etapa_cronometro, { nombre: n.nombre, tipo: null });
  }
  const nombreDe = (id: string) => nombres.get(id) ?? { nombre: humano(id), tipo: null };

  // ---- los pasos, en el orden en que ocurrieron
  const pasos: Paso[] = [];
  const abiertos = new Map<string, Paso>();
  const porId = new Map<string, Paso>();
  const nuevo = (id: string): Paso => {
    const n = nombreDe(id);
    const p: Paso = { id, nombre: n.nombre, tipo: n.tipo, estado: "en_curso", salida: "", ms: 0, veces: 0, notas: [], datos: [], fallo: "" };
    pasos.push(p);
    porId.set(id, p);
    return p;
  };
  for (const e of c.eventos) {
    if (e.tipo === "etapa_inicio") {
      const id = String(e.etapa);
      const p = porId.get(id) ?? nuevo(id);
      p.veces += 1;
      p.estado = "en_curso";
      abiertos.set(id, p);
    } else if (e.tipo === "etapa_fin") {
      const id = String(e.etapa);
      const p = abiertos.get(id) ?? porId.get(id) ?? nuevo(id);
      p.ms += Number(e.ms ?? 0);
      p.fallo = String(e.fallo ?? "");
      p.estado = p.fallo ? "fallo" : "hecho";
      const s = String(e.salida ?? "");
      if (s) p.salida = s;
      if (e.muestra && typeof e.muestra === "object") p.datos = campos(e.muestra, 8);
      abiertos.delete(id);
    } else if (e.tipo === "paso" && typeof e.nodo === "string") {
      const p = porId.get(e.nodo) ?? nuevo(e.nodo);
      const t = String(e.titulo ?? "").trim();
      const d = String(e.detalle ?? "").trim();
      if (t && p.notas.length < 6) p.notas.push(d && d.length < 160 && !d.startsWith("{") ? `${t}: ${d}` : t);
      if (p.estado === "en_curso" && !abiertos.has(e.nodo)) p.estado = e.tono === "error" ? "fallo" : "hecho";
    }
  }
  const terminada = Boolean(c.resultado);
  if (terminada) for (const p of pasos) if (p.estado === "en_curso") p.estado = c.estado === "fallida" ? "fallo" : "hecho";

  // ---- qué entró (la que sigue a una aprobación muestra lo que entró al principio)
  const r = c.resultado;
  const rel = relacion(c);
  const origen = rel.viene_de ?? c;
  const conCorreo = f?.correspondencia !== false && (!origen.entrada || Boolean(sigueA(origen)));
  const titulo = tituloBase(origen);
  const entrada = conCorreo
    ? { tipo: "correo" as const, resumen: titulo !== origen.caso ? titulo : "Un correo", campos: [{ nombre: "Caso", valor: origen.caso }, ...correoDe(origen)] }
    : { tipo: "datos" as const, resumen: tituloDeEntrada(origen) || "Sin datos de entrada", campos: campos(origen.entrada) };

  // ---- qué salió
  const hallazgos = r?.hallazgos && typeof r.hallazgos === "object" ? campos(r.hallazgos) : [];
  const solicitud = r?.solicitud && typeof r.solicitud === "object" ? campos(r.solicitud) : [];
  const adjuntosDe = (w: Record<string, unknown>) => (Array.isArray(w.adjuntos) ? (w.adjuntos as Record<string, unknown>[]) : Array.isArray(w.archivos) ? (w.archivos as Record<string, unknown>[]) : [])
    .slice(0, 12).map((a) => ({ nombre: String(a.nombre ?? ""), tipo: String(a.tipo ?? ""), tamano: Number(a.tamano ?? 0) }));
  const escrituras = Array.isArray(r?.escrituras) ? (r!.escrituras as Record<string, unknown>[]).slice(0, 12).map((w) => ({
    que: String(w.tipo ?? w.operacion ?? w.sistema ?? "escritura"), destino: corto(w.destino ?? w.para ?? w.url ?? w.ruta ?? "", 120),
    retenida: w.bloqueado !== false && w.enviada !== true && w.real !== true, motivo: corto(w.motivo ?? "", 240),
    ...(typeof w.asunto === "string" && w.asunto ? { asunto: corto(w.asunto, 160) } : {}), adjuntos: adjuntosDe(w),
  })) : [];
  // enviar y notificar nunca salen: se ven como escrituras retenidas, con sus adjuntos
  const yaEn = new Set(escrituras.map((w) => `${w.destino}|${w.asunto ?? ""}`));
  for (const m of Array.isArray(r?.mensajes) ? (r!.mensajes as Record<string, unknown>[]) : []) {
    if (!["enviar", "notificar"].includes(String(m.operacion ?? "")) || escrituras.length >= 16) continue;
    const w = { que: String(m.operacion), destino: corto(m.para ?? "", 120), retenida: true, motivo: "enviar y notificar nunca envían: se arma y se anota",
      ...(typeof m.asunto === "string" && m.asunto ? { asunto: corto(m.asunto, 160) } : {}), adjuntos: adjuntosDe(m) };
    if (!yaEn.has(`${w.destino}|${w.asunto ?? ""}`)) escrituras.push(w);
  }
  const salida = {
    datos: hallazgos.length ? hallazgos : solicitud,
    respuesta: typeof r?.respuesta === "string" ? corto(r.respuesta, 1200) : "",
    escrituras,
    avisos: Array.isArray(r?.avisos) ? (r!.avisos as unknown[]).slice(0, 8).map((x) => corto(x, 200)) : [],
    /** Los archivos que generó la ejecución (y los que entrega su salida), descargables. */
    archivos: archivosDe(c),
  };

  // ---- por qué terminó así
  const ultimo = [...pasos].reverse().find((p) => p.estado !== "en_curso");
  const fallido = pasos.find((p) => p.estado === "fallo");
  const desenlace = r ? String(r.accion ?? "") : null;
  const porque = {
    desenlace,
    estado: estadoLegible(c),
    motivo: r ? String(r.motivo ?? "") : "",
    explicacion: r ? String(r.explicacion ?? r.razon_del_reenvio ?? r.razon_del_duplicado ?? "") : "",
    paso_final: fallido?.nombre ?? ultimo?.nombre ?? null,
    fallo: fallido?.fallo ?? (desenlace === "error" ? String(r?.motivo ?? "") : ""),
  };

  const aprobacion = (r?.aprobacion ?? (rel.viene_de?.resultado?.aprobacion)) as { titulo?: unknown } | undefined;
  return {
    corrida: fila(c),
    flujo: { id: c.flujo, nombre: f?.nombre ?? c.flujo, tipo: f?.tipo ?? "declarativo" },
    /** Si esperó (o siguió tras) una aprobación: qué se pidió y dónde sigue la historia. */
    aprobacion: rel.viene_de || pausadaPor(c) ? { titulo: String(aprobacion?.titulo ?? ""), viene_de: rel.viene_de?.id ?? null, continuada_en: rel.continuada_en?.id ?? null } : null,
    entrada,
    pasos: pasos.map(({ veces, ...p }) => ({ ...p, veces })),
    salida,
    archivos: salida.archivos,
    porque,
    consumo: r ? { tokens: Number(r.tokens_entrada ?? 0) + Number(r.tokens_salida ?? 0), consultas_ia: Number(r.vueltas ?? 0) } : null,
    se_puede_reintentar: puedeReintentar(c).ok,
  };
}

// ------------------------------------------------------- archivos que salen

export interface ArchivoDeCorrida {
  id: string;
  nombre: string;
  tipo: string;
  tamano: number;
  nodo: string;
  /** Si lo entrega la salida del flujo (además de haberlo generado). */
  entregado: boolean;
  /** Si todavía está en el espacio del flujo. */
  disponible: boolean;
  url: string;
  texto_url: string;
}

/** Los archivos de una ejecución: los que generó (cada paso) y los que entrega su salida, sin repetir. */
export function archivosDe(c: Corrida): ArchivoDeCorrida[] {
  const r = c.resultado;
  const generados = Array.isArray(r?.archivos) ? (r!.archivos as Record<string, unknown>[]) : [];
  const salida = Array.isArray(r?.archivos_salida) ? (r!.archivos_salida as Record<string, unknown>[]) : [];
  const entregados = new Set(salida.map((x) => String(x.id ?? "")));
  const vistos = new Set<string>();
  const lista: ArchivoDeCorrida[] = [];
  for (const x of [...generados, ...salida]) {
    const id = String(x.id ?? "");
    if (!/^a-[0-9a-z]{6,40}$/.test(id) || vistos.has(id)) continue;
    vistos.add(id);
    const a = espacio.archivoDe(c.flujo, id);
    lista.push({
      id, nombre: String(a?.nombre ?? x.nombre ?? id), tipo: String(a?.tipo ?? x.tipo ?? ""), tamano: Number(a?.tamano ?? x.tamano ?? 0), nodo: String(x.nodo ?? ""),
      entregado: entregados.has(id), disponible: Boolean(a), url: `/api/corridas/${c.id}/archivos/${id}`, texto_url: `/api/corridas/${c.id}/archivos/${id}/texto`,
    });
  }
  return lista.slice(0, 200);
}

// ---------------------------------------------------------------- reintentar

function puedeReintentar(c: Corrida): { ok: true } | { ok: false; error: string } {
  if (c.estado === "en_cola" || c.estado === "procesando") return { ok: false, error: "esta ejecución todavía no termina" };
  // seguir tras una aprobación ya decidida no se repite: se reintenta la ejecución que la pidió
  if (sigueA(c)) return { ok: false, error: "esta ejecución siguió a una aprobación: reintenta la que la pidió" };
  const f = flujos.obtener(c.flujo);
  if (!f) return { ok: false, error: "el flujo de esta ejecución ya no existe" };
  if (c.lote && f.tipo === "codigo" && !bandeja.casoValido(c.caso)) return { ok: false, error: "esta ejecución no se puede repetir" };
  return { ok: true };
}

const sello = () => `${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "")}-${Math.random().toString(36).slice(2, 6)}`;

async function reintentar(deps: Dependencias, c: Corrida): Promise<{ ok: true; corrida: Corrida } | { ok: false; codigo: number; error: string }> {
  const p = puedeReintentar(c);
  if (!p.ok) return { ok: false, codigo: 400, error: p.error };
  const f = flujos.obtener(c.flujo)!;
  const m = deps.motorDe(f.id, true);
  if (!m) return { ok: false, codigo: 404, error: "ese flujo no existe" };
  if (!m.cabe(1)) return { ok: false, codigo: 429, error: `la cola de este flujo está llena (su límite es ${m.recursos().cola_max})` };
  const conf = configuracion.obtener();
  let nueva: Corrida;
  if (f.tipo === "codigo") {
    // pqrd, tutelas y sus copias: el mismo correo de su correspondencia
    if (!bandeja.casoValido(c.caso) || !(await bandeja.esDe({ id: f.id, tipo: f.tipo, ...(f.base ? { base: f.base } : {}) }, c.caso))) return { ok: false, codigo: 400, error: "ese correo ya no está en la correspondencia de este flujo" };
    nueva = m.encolar(c.caso, conf, { origen: "reintento" });
  } else {
    const disp = f.definicion ? flujos.disparadorDe(f.definicion) : undefined;
    const deCorreo = flujos.DISPARADORES_DE_CORREO.has(disp?.tipo ?? "");
    if (deCorreo) {
      if (!bandeja.casoValido(c.caso) || !(await bandeja.esDe({ id: f.id, tipo: f.tipo }, c.caso))) return { ok: false, codigo: 400, error: "ese correo ya no está en la correspondencia de este flujo" };
      nueva = m.encolar(c.caso, conf, { origen: "reintento", entrada: c.entrada ?? {} });
    } else {
      const faltan = espacio.archivosQueFaltan(f.id, c.entrada, f.definicion);
      if (faltan.length) return { ok: false, codigo: 400, error: `ya no están los archivos de esa ejecución: ${faltan.join(", ")}` };
      const prefijo = (c.caso.split("-")[0] || "manual").replace(/[^\w]/g, "").slice(0, 20) || "manual";
      nueva = m.encolar(`${prefijo}-${sello()}`, conf, { origen: "reintento", entrada: c.entrada ?? {} });
    }
  }
  Object.assign(nueva, { reintento_de: c.id });
  return { ok: true, corrida: nueva };
}

// ------------------------------------------------------------------- rutas

const DIA = 86_400_000;

export async function registrar(app: FastifyInstance, deps: Dependencias): Promise<void> {
  delFlujo = (flujo) => [...(deps.motorDe(flujo, false)?.corridas.values() ?? [])];
  app.get<{ Querystring: Record<string, string | undefined> }>("/api/historial", async (req, res) => {
    const q = req.query;
    if (q.flujo && !flujos.obtener(q.flujo)) return res.code(404).send({ error: "ese flujo no existe" });
    const todas = todasDe(deps, q.flujo || undefined);
    const desde = q.desde ? Date.parse(q.desde) : NaN;
    // «hasta» es un día entero: hasta el final de esa fecha
    const hasta = q.hasta ? Date.parse(q.hasta) + (/^\d{4}-\d{2}-\d{2}$/.test(q.hasta) ? DIA : 0) : NaN;
    const texto = quitarTildes((q.q ?? "").trim());
    const filas = todas.map(fila);
    // las facetas cuentan dentro de la fecha y la búsqueda (no dentro de su propio filtro)
    const enRango = filas.filter((x) => (Number.isNaN(desde) || x.creada >= desde) && (Number.isNaN(hasta) || x.creada < hasta))
      .filter((x) => !texto || quitarTildes(`${x.titulo} ${x.caso} ${x.motivo} ${x.desenlace ?? ""} ${x.id}`).includes(texto));
    const estados: Record<string, number> = {};
    const desenlaces: Record<string, number> = {};
    for (const x of enRango) {
      estados[x.estado] = (estados[x.estado] ?? 0) + 1;
      if (x.desenlace) desenlaces[x.desenlace] = (desenlaces[x.desenlace] ?? 0) + 1;
    }
    const lista = enRango.filter((x) => (!q.estado || x.estado === q.estado) && (!q.desenlace || x.desenlace === q.desenlace));
    const limite = Math.max(1, Math.min(200, Number(q.limite) || 50));
    const salto = Math.max(0, Number(q.desde_n) || 0);
    return { total: lista.length, corridas: lista.slice(salto, salto + limite), facetas: { estados, desenlaces }, en_memoria: todas.length };
  });

  app.get<{ Params: { id: string } }>("/api/historial/:id", async (req, res) => {
    const x = buscarCorrida(deps, req.params.id);
    if (!x) return res.code(404).send({ error: "esa ejecución no existe" });
    return detalle(x.c);
  });

  // Los archivos que generó una ejecución: lista, descarga (o vista en el navegador) y texto de lo que el navegador no muestra.
  app.get<{ Params: { id: string } }>("/api/corridas/:id/archivos", async (req, res) => {
    const x = buscarCorrida(deps, req.params.id);
    if (!x) return res.code(404).send({ error: "esa ejecución no existe" });
    return { corrida: x.c.id, flujo: x.c.flujo, archivos: archivosDe(x.c) };
  });

  const archivoDeCorrida = (id: string, archivo: string) => {
    const x = buscarCorrida(deps, id);
    if (!x || !archivosDe(x.c).some((a) => a.id === archivo)) return null;  // solo los de ESA ejecución
    return espacio.archivoDe(x.c.flujo, archivo);
  };

  app.get<{ Params: { id: string; archivo: string }; Querystring: { descargar?: string } }>("/api/corridas/:id/archivos/:archivo", async (req, res) => {
    const a = archivoDeCorrida(req.params.id, req.params.archivo);
    if (!a) return res.code(404).send({ error: "ese archivo no es de esta ejecución o ya no está" });
    return res.header("Content-Type", a.tipo || "application/octet-stream")
      .header("Content-Disposition", `${req.query.descargar ? "attachment" : "inline"}; filename*=UTF-8''${encodeURIComponent(a.nombre)}`)
      .header("Cache-Control", "private, max-age=3600").header("X-Content-Type-Options", "nosniff").send(createReadStream(a.ruta));
  });

  app.get<{ Params: { id: string; archivo: string } }>("/api/corridas/:id/archivos/:archivo/texto", async (req, res) => {
    const a = archivoDeCorrida(req.params.id, req.params.archivo);
    if (!a) return res.code(404).send({ error: "ese archivo no es de esta ejecución o ya no está" });
    const texto = await new Promise<string>((resolver) => {
      const p = spawn(config.python, [resolve(config.raiz, "puente", "texto_de_archivo.py"), a.ruta, a.nombre], { env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
      let salida = "";
      p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
      const reloj = setTimeout(() => p.kill(), 60_000);
      p.on("exit", () => {
        clearTimeout(reloj);
        resolver(salida);
      });
    });
    return { nombre: a.nombre, texto };
  });

  app.post<{ Params: { id: string } }>("/api/historial/:id/reintentar", async (req, res) => {
    const x = buscarCorrida(deps, req.params.id);
    if (!x) return res.code(404).send({ error: "esa ejecución no existe" });
    const r = await reintentar(deps, x.c);
    if (!r.ok) return res.code(r.codigo).send({ error: r.error });
    return { id: r.corrida.id, caso: r.corrida.caso, flujo: r.corrida.flujo, reintento_de: x.c.id };
  });
}

export type { Evento };
