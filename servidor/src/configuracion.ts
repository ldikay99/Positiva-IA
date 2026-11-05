import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";
import { config } from "./config.ts";
import { ajustables } from "./catalogo.ts";

export const MODELOS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-3.1-flash-lite"] as const;

const esquema = z.object({
  parametros: z.record(z.string(), z.unknown()).default({}),
  entorno: z.record(z.string(), z.string()).default({}),
  ia: z.object({ modelo: z.string().min(1), ubicacion: z.string().min(1) }).default({ modelo: "gemini-2.5-flash", ubicacion: "global" }),
  concurrencia: z.number().int().min(1).max(config.trabajadoresMax).default(2),
  topeSegundos: z.number().int().min(30).max(1800).default(600),
});

export type Configuracion = z.infer<typeof esquema>;

const ruta = () => join(config.datos, "configuracion.json");
let actual: Configuracion = esquema.parse({});

export async function cargar(): Promise<Configuracion> {
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

/** Solo se aceptan parámetros que el catálogo marca como ajustables en caliente. */
export function validar(entrada: unknown): { ok: true; valor: Configuracion } | { ok: false; error: string } {
  const r = esquema.safeParse(entrada);
  if (!r.success) return { ok: false, error: r.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ") };
  const permitidos = ajustables();
  const ajenos = Object.keys(r.data.parametros).filter((k) => !permitidos.modulo.has(k));
  if (ajenos.length) return { ok: false, error: `parámetros no ajustables: ${ajenos.join(", ")}` };
  const otras = Object.keys(r.data.entorno).filter((k) => !permitidos.entorno.has(k));
  if (otras.length) return { ok: false, error: `variables no permitidas: ${otras.join(", ")}` };
  return { ok: true, valor: r.data };
}

export async function guardar(c: Configuracion): Promise<void> {
  actual = c;
  await mkdir(config.datos, { recursive: true });
  await writeFile(ruta(), JSON.stringify(c, null, 2), "utf8");
}
