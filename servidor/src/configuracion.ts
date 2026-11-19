import { readFile, writeFile, mkdir, appendFile, readdir, unlink } from "node:fs/promises";
import * as bd from "./bd/conexion.ts";
import * as estadoBd from "./bd/estado.ts";
import { join } from "node:path";
import { z } from "zod";
import { config } from "./config.ts";
import { ajustables } from "./catalogo.ts";

export const MODELOS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-3.1-flash-lite"] as const;

/** Regla creada desde la web que el motor evalúa en la entrada o antes de la compuerta. */
const regla = z.object({
  id: z.string().regex(/^[a-z0-9-]{3,48}$/),
  nombre: z.string().trim().min(1).max(80),
  descripcion: z.string().max(400).default(""),
  activa: z.boolean().default(true),
  punto: z.enum(["entrada", "validacion"]),
  campo: z.string().regex(/^[a-z_]{2,48}$/),
  operador: z.enum(["coincide", "no_coincide", "falta", "existe"]),
  patron: z.string().max(2000).default(""),
  ignorarMayusculas: z.boolean().default(true),
  accion: z.enum(["manual", "aviso", "fijar"]),
  motivo: z.string().regex(/^[A-Z0-9_]{0,48}$/).default("REGLA_PERSONALIZADA"),
  valor: z.string().max(300).default(""),
});

export type Regla = z.infer<typeof regla>;

const esquema = z.object({
  reglas: z.array(regla).max(60).default([]),
  parametros: z.record(z.string(), z.unknown()).default({}),
  entorno: z.record(z.string(), z.string()).default({}),
  ia: z
    .object({
      modelo: z.string().min(1),
      ubicacion: z.string().min(1),
      temperatura: z.number().min(0).max(2).nullable().default(null),
    })
    .default({ modelo: "gemini-2.5-flash", ubicacion: "global", temperatura: null }),
  concurrencia: z.number().int().min(1).max(config.trabajadoresMax).default(2),
  topeSegundos: z.number().int().min(30).max(1800).default(600),
});

export type Configuracion = z.infer<typeof esquema>;

export interface Cambio {
  t: number;
  clave: string;
  antes: unknown;
  despues: unknown;
  nota?: string;
}

const ruta = () => join(config.datos, "configuracion.json");
const rutaHistorial = () => join(config.datos, "historial.jsonl");
const carpetaPerfiles = () => join(config.datos, "perfiles");
let actual: Configuracion = esquema.parse({});

export async function cargar(): Promise<Configuracion> {
  // En la base, un error de conexión no se confunde con «sin configuración»: no arranca (antes que pisarla).
  if (bd.activa()) return (actual = esquema.parse((await estadoBd.configuracion.ajuste("configuracion")) ?? {}));
  try {
    actual = esquema.parse(JSON.parse(await readFile(ruta(), "utf8")));
  } catch {
    actual = esquema.parse({});
  }
  return actual;
}

export function obtener(): Configuracion {
  return actual;
}

/** Solo se aceptan parámetros que el catálogo permite ajustar desde el visor. */
export function validar(entrada: unknown): { ok: true; valor: Configuracion } | { ok: false; error: string } {
  const r = esquema.safeParse(entrada);
  if (!r.success) return { ok: false, error: r.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ") };
  const permitidos = ajustables();
  const ajenos = Object.keys(r.data.parametros).filter((k) => !permitidos.modulo.has(k));
  if (ajenos.length) return { ok: false, error: `parámetros no ajustables: ${ajenos.join(", ")}` };
  const otras = Object.keys(r.data.entorno).filter((k) => !permitidos.entorno.has(k));
  if (otras.length) return { ok: false, error: `variables no permitidas: ${otras.join(", ")}` };
  const ids = r.data.reglas.map((x) => x.id);
  if (new Set(ids).size !== ids.length) return { ok: false, error: "hay dos reglas con el mismo id" };
  const malas = r.data.reglas.filter((x) => (x.accion === "fijar" && x.punto !== "validacion") || ((x.operador === "coincide" || x.operador === "no_coincide") && !x.patron));
  if (malas.length) return { ok: false, error: `reglas incompletas: ${malas.map((x) => x.nombre).join(", ")}` };
  return { ok: true, valor: r.data };
}

function plano(c: Configuracion): Record<string, unknown> {
  const p: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(c.parametros)) p[k] = v;
  for (const [k, v] of Object.entries(c.entorno)) p[`entorno.${k}`] = v;
  p["ia.modelo"] = c.ia.modelo;
  p["ia.ubicacion"] = c.ia.ubicacion;
  p["ia.temperatura"] = c.ia.temperatura;
  for (const x of c.reglas) p[`regla.${x.id}`] = x;
  p.concurrencia = c.concurrencia;
  p.topeSegundos = c.topeSegundos;
  return p;
}

export function diferencias(a: Configuracion, b: Configuracion): Omit<Cambio, "t">[] {
  const pa = plano(a);
  const pb = plano(b);
  const claves = new Set([...Object.keys(pa), ...Object.keys(pb)]);
  return [...claves]
    .filter((k) => JSON.stringify(pa[k]) !== JSON.stringify(pb[k]))
    .map((k) => ({ clave: k, antes: pa[k] ?? null, despues: pb[k] ?? null }));
}

export async function guardar(c: Configuracion, nota?: string): Promise<Omit<Cambio, "t">[]> {
  const cambios = diferencias(actual, c);
  actual = c;
  const t = Date.now();
  if (bd.activa()) {
    await estadoBd.configuracion.ponerAjuste("configuracion", c);
    if (cambios.length) await estadoBd.configuracion.agregarCambios(cambios.map((x) => ({ t, ...x, nota })));
    return cambios;
  }
  await mkdir(config.datos, { recursive: true });
  await writeFile(ruta(), JSON.stringify(c, null, 2), "utf8");
  if (cambios.length) await appendFile(rutaHistorial(), cambios.map((x) => JSON.stringify({ t, ...x, nota })).join("\n") + "\n", "utf8");
  return cambios;
}

export async function historial(limite = 300): Promise<Cambio[]> {
  if (bd.activa()) return estadoBd.configuracion.cambios(limite) as Promise<Cambio[]>;
  try {
    const lineas = (await readFile(rutaHistorial(), "utf8")).trim().split("\n").filter(Boolean);
    return lineas.slice(-limite).map((l) => JSON.parse(l) as Cambio).reverse();
  } catch {
    return [];
  }
}

const NOMBRE_PERFIL = /^[\w áéíóúñÁÉÍÓÚÑ.-]{1,60}$/;

export async function perfiles(): Promise<{ nombre: string; t: number; cambios: number; descripcion: string }[]> {
  if (bd.activa()) {
    return (await estadoBd.configuracion.perfiles()).map((p) => {
      const c = p.configuracion as Configuracion;
      return { nombre: p.nombre, t: p.t, descripcion: p.descripcion, cambios: Object.keys(c.parametros ?? {}).length + Object.keys(c.entorno ?? {}).length + (c.reglas?.length ?? 0) };
    }).sort((a, b) => b.t - a.t);
  }
  const nombres = (await readdir(carpetaPerfiles()).catch(() => [])).filter((n) => n.endsWith(".json"));
  const lista = await Promise.all(
    nombres.map(async (n) => {
      const p = JSON.parse(await readFile(join(carpetaPerfiles(), n), "utf8")) as { nombre: string; t: number; descripcion: string; configuracion: Configuracion };
      return { nombre: p.nombre, t: p.t, descripcion: p.descripcion, cambios: Object.keys(p.configuracion.parametros).length + Object.keys(p.configuracion.entorno).length + (p.configuracion.reglas?.length ?? 0) };
    }),
  );
  return lista.sort((a, b) => b.t - a.t);
}

export async function guardarPerfil(nombre: string, descripcion: string): Promise<boolean> {
  if (!NOMBRE_PERFIL.test(nombre)) return false;
  if (bd.activa()) {
    await estadoBd.configuracion.guardarPerfil(nombre, descripcion, actual);
    return true;
  }
  await mkdir(carpetaPerfiles(), { recursive: true });
  await writeFile(join(carpetaPerfiles(), `${nombre}.json`), JSON.stringify({ nombre, descripcion, t: Date.now(), configuracion: actual }, null, 2), "utf8");
  return true;
}

export async function leerPerfil(nombre: string): Promise<Configuracion | null> {
  if (!NOMBRE_PERFIL.test(nombre)) return null;
  try {
    const p = bd.activa() ? { configuracion: await estadoBd.configuracion.leerPerfil(nombre) } : JSON.parse(await readFile(join(carpetaPerfiles(), `${nombre}.json`), "utf8")) as { configuracion: unknown };
    if (p.configuracion === null) return null;
    const v = validar(p.configuracion);
    return v.ok ? v.valor : null;
  } catch {
    return null;
  }
}

export async function borrarPerfil(nombre: string): Promise<boolean> {
  if (!NOMBRE_PERFIL.test(nombre)) return false;
  if (bd.activa()) return estadoBd.configuracion.borrarPerfil(nombre);
  return unlink(join(carpetaPerfiles(), `${nombre}.json`)).then(
    () => true,
    () => false,
  );
}
