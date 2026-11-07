import { spawn } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { mkdir, rename, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { z } from "zod";
import { config } from "./config.ts";
import * as credenciales from "./credenciales.ts";

/**
 * La IA de la plataforma, en tres niveles que nunca se mezclan:
 *
 * 1. Global (`/api/ia/global`): la que usan los flujos con `ia: {usar: "global"}`.
 *    Vertex · gemini-2.5-flash si nadie la cambia.
 * 2. Del flujo (`definicion.ia`): `{usar: "global"}` o `{proveedor, modelo, credencial?, temperatura?, por_minuto?}`;
 *    y por nodo de IA `config.proveedor/modelo/credencial`. Orden: nodo → flujo → global.
 *    Un flujo sin `ia` (los de antes) sigue en Vertex con su `modelo`.
 * 3. Vera (`/api/ia/vera`): texto (con herramientas) y voz en vivo, aparte de todo lo demás.
 *
 * La capa que llama a los modelos y el catálogo están en `puente/proveedores/` (Python): aquí
 * solo se guarda la configuración, se valida la forma y se le piden al Python el catálogo,
 * la lista en vivo y la prueba de conexión.
 */

/** Los proveedores registrados en `puente/proveedores/` (una prueba compara las dos listas). */
export const PROVEEDORES_IA = ["vertex", "gemini", "openai", "azure_openai", "deepseek", "mistral", "groq", "openrouter", "ollama", "openai_compatible", "anthropic", "cohere", "personalizado"] as const;
export type ProveedorIa = (typeof PROVEEDORES_IA)[number];
/** El tipo de credencial de cada proveedor (null: no necesita, Vertex usa la identidad del servicio). */
export const CREDENCIAL_DE: Record<ProveedorIa, string | null> = {
  vertex: null, gemini: "ia_gemini", openai: "ia_openai", azure_openai: "ia_azure_openai", deepseek: "ia_deepseek", mistral: "ia_mistral",
  groq: "ia_groq", openrouter: "ia_openrouter", ollama: "ia_ollama", openai_compatible: "ia_compatible", anthropic: "ia_anthropic",
  cohere: "ia_cohere", personalizado: "ia_personalizado",
};
const TIPOS_IA = new Set(["ia_clasificar", "ia_decidir", "ia_extraer", "ia_generar", "resumir", "ia_redactar"]);
const CON_IA = new Set([...TIPOS_IA, "leer_adjuntos"]);
const NOMBRE_CREDENCIAL = /^[a-z0-9][a-z0-9_.-]{0,62}$/;

export interface IaElegida { proveedor: string; modelo: string; credencial?: string; temperatura?: number; por_minuto?: number }
export const GLOBAL_DEFECTO: IaElegida = { proveedor: "vertex", modelo: "gemini-2.5-flash" };
export interface IaVera { texto: IaElegida; voz: IaElegida & { voz?: string; ubicacion?: string } }
export const VERA_DEFECTO: IaVera = {
  texto: { proveedor: "vertex", modelo: "gemini-3-flash-preview" },
  voz: { proveedor: "vertex", modelo: "gemini-3.8-live", ubicacion: "us-central1" },
};

// ------------------------------------------------------------------ zod

const proveedor = z.enum(PROVEEDORES_IA, { error: `proveedor de IA: ${PROVEEDORES_IA.join(", ")}` });
const credencial = z.string().regex(NOMBRE_CREDENCIAL, "credencial es el NOMBRE de una credencial guardada, nunca una llave");
const temperatura = z.number({ error: "temperatura tiene que ser un número" }).min(0, "temperatura entre 0 y 2").max(2, "temperatura entre 0 y 2");
const propia = z.object({
  proveedor, modelo: z.string({ error: "falta el modelo" }).trim().min(1, "falta el modelo"), credencial: credencial.optional(),
  temperatura: temperatura.optional(), por_minuto: z.number().int().min(1, "por_minuto entre 1 y 10000").max(10000, "por_minuto entre 1 y 10000").optional(),
});
/** `definicion.ia`: {usar: "global"} o una IA propia del flujo. */
export const iaFlujo = z.union([z.object({ usar: z.literal("global") }).strict(), propia], { error: "ia: {usar: \"global\"} o {proveedor, modelo, credencial, temperatura}" });

/** La forma de las claves de IA de un nodo (lo semántico lo revisa el motor, con las credenciales y el catálogo). */
export function revisarNodo(tipo: string, c: Record<string, unknown>, problema: (clave: string, m: string) => void): void {
  if (!CON_IA.has(tipo)) {
    if (c.proveedor !== undefined) problema("proveedor", "proveedor solo aplica a los nodos de IA y a leer_adjuntos");
    return;
  }
  if (c.proveedor !== undefined && !proveedor.safeParse(c.proveedor).success) problema("proveedor", `proveedor de IA: ${PROVEEDORES_IA.join(", ")}`);
  if (c.modelo !== undefined && !(typeof c.modelo === "string" && c.modelo.trim())) problema("modelo", "modelo tiene que ser el nombre de un modelo");
  if (TIPOS_IA.has(tipo) && c.credencial !== undefined && c.credencial !== "" && !credencial.safeParse(c.credencial).success) problema("credencial", "credencial es el NOMBRE de una credencial guardada, nunca una llave");
}

// ------------------------------------------------------------------ resolución

type Def = { id?: string; modelo?: string; ia?: unknown; nodos?: { id: string; tipo: string; config?: unknown }[] };

export function iaDelFlujo(d: Def, global = obtenerGlobal()): IaElegida & { origen: "flujo" | "global" | "legado" } {
  const ia = d.ia as Record<string, unknown> | undefined;
  if (ia && typeof ia.proveedor === "string") {
    const r: IaElegida = { proveedor: ia.proveedor, modelo: String(ia.modelo ?? "") };
    if (typeof ia.credencial === "string" && ia.credencial) r.credencial = ia.credencial;
    if (typeof ia.temperatura === "number") r.temperatura = ia.temperatura;
    if (typeof ia.por_minuto === "number") r.por_minuto = ia.por_minuto;
    return { ...r, origen: "flujo" };
  }
  if (ia && ia.usar === "global") return { ...global, origen: "global" };
  return { proveedor: "vertex", modelo: d.modelo || GLOBAL_DEFECTO.modelo, origen: "legado" };
}

export function iaDelNodo(c: Record<string, unknown>, delFlujo: IaElegida & { origen: string }): IaElegida & { origen: string } {
  const prov = typeof c.proveedor === "string" ? c.proveedor : undefined;
  let r: IaElegida & { origen: string };
  if (prov && prov !== delFlujo.proveedor) {
    r = { proveedor: prov, modelo: String(c.modelo ?? ""), origen: "nodo", ...(typeof c.credencial === "string" && c.credencial ? { credencial: c.credencial } : {}) };
  } else {
    r = { ...delFlujo };
    if (typeof c.modelo === "string" && c.modelo) r = { ...r, modelo: c.modelo, origen: "nodo" };
    if (typeof c.credencial === "string" && c.credencial) r = { ...r, credencial: c.credencial, origen: "nodo" };
  }
  if (typeof c.temperatura === "number") r.temperatura = c.temperatura;
  return r;
}

/** Las credenciales de IA que necesita una definición (la del flujo, la global si la usa y las de sus nodos de IA). */
export function credencialesDeIa(d: Def): string[] {
  const r = new Set<string>();
  const f = iaDelFlujo(d);
  if (f.credencial) r.add(f.credencial);
  for (const n of d.nodos ?? []) {
    if (!CON_IA.has(n.tipo)) continue;
    const x = iaDelNodo((n.config ?? {}) as Record<string, unknown>, f);
    if (x.credencial) r.add(x.credencial);
  }
  return [...r];
}

/** Un flujo nuevo sin `ia` usa la IA global. */
export function conIaPorDefecto(entrada: unknown, esNuevo: boolean): unknown {
  if (!esNuevo || !entrada || typeof entrada !== "object" || Array.isArray(entrada) || "ia" in (entrada as object)) return entrada;
  return { ...(entrada as object), ia: { usar: "global" } };
}

/** Lo que el motor de Python necesita para validar credenciales de IA: nombre → {tipo, alcance} (nunca valores). */
export function metadatosCredenciales(): Record<string, { tipo: string; alcance: string }> {
  return Object.fromEntries(credenciales.listar().map((c) => [c.nombre, { tipo: c.tipo, alcance: c.alcance }]));
}

// ------------------------------------------------------------------ Python

const PY_CLI = () => resolve(config.raiz, "puente", "proveedores", "cli.py");

export function pedirAlPython<T>(cmd: string, orden: unknown, tope_ms = 90000): Promise<T> {
  return new Promise((resolver, rechazar) => {
    const p = spawn(config.python, [PY_CLI(), cmd], { stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let salida = "";
    let errores = "";
    const reloj = setTimeout(() => p.kill("SIGKILL"), tope_ms);
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.stderr.setEncoding("utf8").on("data", (t: string) => (errores += t));
    p.on("exit", (codigo) => {
      clearTimeout(reloj);
      try {
        resolver(JSON.parse(salida.trim().split("\n").pop() ?? "") as T);
      } catch {
        rechazar(new Error(`la capa de proveedores no respondió (código ${codigo}): ${errores.slice(-300)}`));
      }
    });
    p.stdin.end(JSON.stringify(orden ?? {}));
  });
}

export interface FichaModelo {
  id: string; nombre: string; contexto?: number | null; precio_entrada?: number; precio_salida?: number; precio_aproximado?: boolean;
  acepta: Partial<Record<"texto" | "imagenes" | "pdf" | "json_estructurado" | "herramientas" | "voz_en_vivo", boolean | null>>;
  solo_voz?: boolean; verificado?: string; en_vivo?: boolean; capacidades_conocidas?: boolean; ubicacion?: string;
}
export interface ProveedorDelCatalogo {
  id: string; nombre: string; descripcion: string; autenticacion: string; tipo_credencial: string | null; requiere_credencial: boolean;
  json: string; lista_en_vivo: boolean; por_defecto: Record<string, boolean | null>; modelos: FichaModelo[];
}
export interface Catalogo { precios_fecha: string; precios_aproximados: boolean; capacidades: string[]; proveedores: ProveedorDelCatalogo[] }

let catalogoCache: { mtime: number; c: Catalogo } | null = null;

/** El catálogo de `puente/proveedores/catalogo.py` (se vuelve a leer si el archivo cambia). */
export async function catalogo(): Promise<Catalogo> {
  const archivo = resolve(config.raiz, "puente", "proveedores", "catalogo.py");
  const mtime = existsSync(archivo) ? statSync(archivo).mtimeMs : 0;
  if (catalogoCache && catalogoCache.mtime === mtime) return catalogoCache.c;
  const c = await pedirAlPython<Catalogo>("catalogo", {});
  catalogoCache = { mtime, c };
  return c;
}

const vivos = new Map<string, { t: number; modelos: FichaModelo[] }>();
const UNA_HORA = 3600_000;

/** Los modelos de la API del proveedor con esa credencial (una hora en caché). */
export async function modelosEnVivo(prov: string, nombre: string, valores: Record<string, unknown>, fresco = false): Promise<FichaModelo[]> {
  const k = `${prov}:${nombre}`;
  const x = vivos.get(k);
  if (!fresco && x && Date.now() - x.t < UNA_HORA) return x.modelos;
  const r = await pedirAlPython<{ ok?: boolean; modelos?: FichaModelo[]; error?: string }>("modelos", { proveedor: prov, credencial: valores });
  if (!r.modelos) throw new Error(r.error ?? "no se pudo listar los modelos");
  vivos.set(k, { t: Date.now(), modelos: r.modelos });
  return r.modelos;
}

/** La ficha de un modelo: del catálogo o de una lista en vivo ya traída. */
export async function fichaDe(prov: string, modelo: string): Promise<FichaModelo | null> {
  const c = await catalogo();
  const p = c.proveedores.find((x) => x.id === prov);
  const f = p?.modelos.find((m) => m.id === modelo) ?? (prov === "azure_openai" ? c.proveedores.find((x) => x.id === "openai")?.modelos.find((m) => m.id === modelo) : undefined);
  if (f) return f;
  for (const [k, v] of vivos) if (k.startsWith(`${prov}:`)) { const m = v.modelos.find((x) => x.id === modelo); if (m) return m; }
  return null;
}

// ------------------------------------------------------------------ guardado

const rutaEstado = (archivo: string) => join(config.datos, archivo);

function leerJson<T>(archivo: string): T | null {
  try {
    return JSON.parse(readFileSync(rutaEstado(archivo), "utf8")) as T;
  } catch {
    return null;
  }
}

async function guardarJson(archivo: string, valor: unknown): Promise<void> {
  await mkdir(config.datos, { recursive: true });
  const tmp = `${rutaEstado(archivo)}.${process.pid}.tmp`;
  await writeFile(tmp, JSON.stringify(valor, null, 2), "utf8");
  await rename(tmp, rutaEstado(archivo));
}

let globalCache: IaElegida | null = null;
let veraCache: IaVera | null = null;

export function obtenerGlobal(): IaElegida {
  globalCache ??= { ...GLOBAL_DEFECTO, ...(leerJson<IaElegida>("ia_global.json") ?? {}) };
  return globalCache;
}

export function obtenerVera(): IaVera {
  if (!veraCache) {
    const x = leerJson<Partial<IaVera>>("ia_vera.json") ?? {};
    veraCache = { texto: { ...VERA_DEFECTO.texto, ...(x.texto ?? {}) }, voz: { ...VERA_DEFECTO.voz, ...(x.voz ?? {}) } };
  }
  return veraCache;
}

type Revision = { ok: true; ia: IaElegida } | { ok: false; error: string };

/** Revisa una IA elegida para un uso: proveedor, modelo, credencial (existe, tipo y alcance) y lo que el modelo tiene que aceptar. */
export async function revisar(entrada: unknown, que: string, pide: ("herramientas" | "voz_en_vivo" | "texto")[], alcance = "global"): Promise<Revision> {
  const r = propia.safeParse(entrada);
  if (!r.success) return { ok: false, error: `${que}: ${r.error.issues.map((i) => i.message).join("; ")}` };
  const ia = r.data;
  const tipo = CREDENCIAL_DE[ia.proveedor];
  if (ia.credencial) {
    if (!tipo) return { ok: false, error: `${que}: ${ia.proveedor} no usa credencial (usa la identidad del servicio)` };
    const c = credenciales.listar().find((x) => x.nombre === ia.credencial);
    if (!c) return { ok: false, error: `${que}: la credencial «${ia.credencial}» no existe` };
    if (c.tipo !== tipo) return { ok: false, error: `${que}: la credencial «${ia.credencial}» es de tipo ${c.tipo}; ${ia.proveedor} necesita una ${tipo}` };
    if (c.alcance !== "global" && c.alcance !== alcance) return { ok: false, error: `${que}: la credencial «${ia.credencial}» es del flujo «${c.alcance}»; aquí tiene que ser global` };
  } else if (tipo) {
    return { ok: false, error: `${que}: ${ia.proveedor} necesita una credencial de tipo ${tipo} (la llave la pone una persona en Conexiones)` };
  }
  const f = await fichaDe(ia.proveedor, ia.modelo);
  for (const cap of pide) {
    if (cap === "texto") {
      if (f?.solo_voz) return { ok: false, error: `${que}: «${ia.modelo}» es un modelo de voz en vivo; elige uno de texto` };
      continue;
    }
    const nombre = cap === "herramientas" ? "herramientas (function calling)" : "voz en vivo";
    if (!f) return { ok: false, error: `${que}: «${ia.modelo}» no está en el catálogo de ${ia.proveedor}: no se sabe si acepta ${nombre}; elige un modelo del catálogo que lo acepte` };
    if (f.acepta[cap] !== true) return { ok: false, error: `${que}: el modelo «${ia.modelo}» no acepta ${nombre}` };
  }
  return { ok: true, ia: Object.fromEntries(Object.entries(ia).filter(([, v]) => v !== undefined)) as unknown as IaElegida };
}

export async function ponerGlobal(entrada: unknown): Promise<Revision> {
  const r = await revisar(entrada, "IA global", ["texto"]);
  if (!r.ok) return r;
  globalCache = r.ia;
  await guardarJson("ia_global.json", r.ia);
  return r;
}

export async function ponerVera(entrada: unknown): Promise<{ ok: true; vera: IaVera } | { ok: false; error: string }> {
  const b = (entrada ?? {}) as { texto?: unknown; voz?: unknown };
  const actual = obtenerVera();
  let texto = actual.texto;
  let voz = actual.voz;
  if (b.texto !== undefined) {
    const r = await revisar(b.texto, "IA de Vera (texto)", ["texto", "herramientas"]);
    if (!r.ok) return r;
    texto = r.ia;
  }
  if (b.voz !== undefined) {
    const extra = (b.voz ?? {}) as { voz?: unknown; ubicacion?: unknown };
    const r = await revisar({ ...(b.voz as object), voz: undefined, ubicacion: undefined }, "IA de Vera (voz)", ["voz_en_vivo"]);
    if (!r.ok) return r;
    const f = await fichaDe(r.ia.proveedor, r.ia.modelo);
    voz = { ...r.ia, ...(typeof extra.voz === "string" && extra.voz ? { voz: extra.voz } : {}),
      ...(typeof extra.ubicacion === "string" && extra.ubicacion ? { ubicacion: extra.ubicacion } : f?.ubicacion ? { ubicacion: f.ubicacion } : {}) };
  }
  veraCache = { texto, voz };
  await guardarJson("ia_vera.json", veraCache);
  return { ok: true, vera: veraCache };
}

/** Si la voz de Vera puede funcionar ahora (modelo con voz en vivo y su credencial, si la necesita). */
export async function vozDisponible(): Promise<{ voz_disponible: boolean; motivo: string }> {
  const v = obtenerVera().voz;
  const r = await revisar({ proveedor: v.proveedor, modelo: v.modelo, ...(v.credencial ? { credencial: v.credencial } : {}) }, "Voz de Vera", ["voz_en_vivo"]);
  if (!r.ok) return { voz_disponible: false, motivo: r.error };
  if (!["vertex", "gemini"].includes(v.proveedor)) return { voz_disponible: false, motivo: `la voz en vivo de Vera solo está conectada para Vertex y Gemini (elegido: ${v.proveedor})` };
  return { voz_disponible: true, motivo: "" };
}

// ------------------------------------------------------------------ consumo

export interface Consumo { tokens_entrada: number; tokens_salida: number; costo_estimado_usd: number; llamadas: number; por_proveedor: Record<string, { tokens_entrada: number; tokens_salida: number; costo_estimado_usd: number; llamadas: number }> }

/** Suma el consumo de los resultados de varias corridas (lo que muestra el tablero por flujo). */
export function consumoDe(resultados: unknown[]): Consumo {
  const t: Consumo = { tokens_entrada: 0, tokens_salida: 0, costo_estimado_usd: 0, llamadas: 0, por_proveedor: {} };
  for (const r of resultados) {
    const x = (r as { consumo?: Consumo; tokens_entrada?: number; tokens_salida?: number } | undefined);
    if (!x) continue;
    const c = x.consumo;
    if (!c) {
      t.tokens_entrada += Number(x.tokens_entrada ?? 0);
      t.tokens_salida += Number(x.tokens_salida ?? 0);
      continue;
    }
    t.tokens_entrada += Number(c.tokens_entrada ?? 0);
    t.tokens_salida += Number(c.tokens_salida ?? 0);
    t.costo_estimado_usd += Number(c.costo_estimado_usd ?? 0);
    t.llamadas += Number(c.llamadas ?? 0);
    for (const [p, v] of Object.entries(c.por_proveedor ?? {})) {
      const d = (t.por_proveedor[p] ??= { tokens_entrada: 0, tokens_salida: 0, costo_estimado_usd: 0, llamadas: 0 });
      d.tokens_entrada += Number(v.tokens_entrada ?? 0);
      d.tokens_salida += Number(v.tokens_salida ?? 0);
      d.costo_estimado_usd += Number(v.costo_estimado_usd ?? 0);
      d.llamadas += Number(v.llamadas ?? 0);
    }
  }
  t.costo_estimado_usd = Math.round(t.costo_estimado_usd * 1e6) / 1e6;
  return t;
}

// ------------------------------------------------------------------ manifiesto

/** La IA efectiva de un flujo y de cada nodo que la usa, para el manifiesto. */
export function manifiestoIa(d: Def): Record<string, unknown> {
  const g = obtenerGlobal();
  const f = iaDelFlujo(d, g);
  return {
    nivel: f.origen,
    explicacion: f.origen === "global" ? `Usa la IA global de la plataforma (${g.proveedor} · ${g.modelo})` : f.origen === "flujo" ? "IA propia de este flujo" : "Flujo de antes: Vertex con el modelo del flujo",
    flujo: f,
    nodos: (d.nodos ?? []).filter((n) => CON_IA.has(n.tipo)).map((n) => ({ nodo: n.id, tipo: n.tipo, ...iaDelNodo((n.config ?? {}) as Record<string, unknown>, f) })),
    precios: "costos estimados con precios publicados aproximados (ver /api/ia/proveedores)",
  };
}
