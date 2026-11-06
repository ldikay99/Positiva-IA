import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { mkdir, readdir, readFile, unlink, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { z } from "zod";
import { config } from "./config.ts";
import { catalogo as catalogoDePqrd, type Catalogo } from "./catalogo.ts";

/**
 * Los flujos de la plataforma.
 *
 * - De código: un motor de verdad (PQRD, Tutelas) que el visor observa. Su
 *   estructura es la del código; desde aquí se ajustan sus parámetros.
 * - Declarativos: un JSON de nodos y aristas que se crea en la web o en el
 *   chat y que ejecuta `puente/flujo_declarativo.py`. Se guardan con versión.
 *
 * Cada flujo tiene sus propias corridas, su configuración y sus trabajadores:
 * nada de un flujo aparece en otro.
 */

export const TIPOS_DE_NODO = ["entrada_correo", "filtro", "condicion", "ia_clasificar", "ia_extraer", "validar", "ia_redactar", "salida"] as const;

const nodo = z.object({
  id: z.string().regex(/^[a-z][a-z0-9_]{1,40}$/, "id en minúsculas, sin espacios"),
  tipo: z.enum(TIPOS_DE_NODO),
  nombre: z.string().min(1).max(80),
  descripcion: z.string().max(600).optional(),
  // La IA a veces manda null por «sin configuración».
  config: z.record(z.string(), z.unknown()).nullish().transform((v) => v ?? {}),
});

export const definicion = z.object({
  id: z.string().regex(/^[a-z][a-z0-9-]{1,40}$/, "id en minúsculas con guiones"),
  nombre: z.string().min(2).max(80),
  descripcion: z.string().max(800).nullish().transform((v) => v ?? ""),
  modelo: z.string().default("gemini-2.5-flash"),
  nodos: z.array(nodo).min(2).max(60),
  aristas: z.array(z.object({ desde: z.string(), hacia: z.string(), cuando: z.string().optional() })).max(200),
});
export type Definicion = z.infer<typeof definicion>;

export interface Flujo {
  id: string;
  nombre: string;
  descripcion: string;
  tipo: "codigo" | "declarativo";
  /** El trabajador de Python que lo ejecuta. */
  trabajador: string;
  /** Si sabe leer el buzón real (solo PQRD por ahora). */
  buzonReal: boolean;
  /** Si corre varios correos con un solo orquestador, como el job. */
  lote: boolean;
  version?: number;
  actualizado?: number;
  definicion?: Definicion;
}

const raiz = config.raiz;
const carpetaDeclarativos = () => join(config.datos, "flujos");

const DE_CODIGO: Flujo[] = [
  {
    id: "pqrd", nombre: "Radicación de PQRD", tipo: "codigo", buzonReal: true, lote: true,
    descripcion: "Correos de servicioalcliente@positiva.gov.co: deduplica, lee adjuntos con OCR y visión, extrae los datos con algoritmo e IA, valida con la compuerta y el revisor, y radica en SGDEA.",
    trabajador: config.trabajador,
  },
  {
    id: "tutelas", nombre: "Radicación de tutelas", tipo: "codigo", buzonReal: false, lote: false,
    descripcion: "Notificaciones judiciales: valida que el remitente sea un juzgado, detecta tutela o actuación, encuentra el radicado y el juzgado, exige anexos, deduplica y radica en SGDEA.",
    trabajador: resolve(raiz, "puente", "trabajador_tutelas.py"),
  },
];

const declarativos = new Map<string, Flujo>();

export async function cargar(): Promise<void> {
  await mkdir(carpetaDeclarativos(), { recursive: true });
  for (const a of (await readdir(carpetaDeclarativos())).filter((x) => x.endsWith(".json"))) {
    try {
      const guardado = JSON.parse(await readFile(join(carpetaDeclarativos(), a), "utf8")) as { definicion: unknown; version: number; actualizado: number };
      const d = definicion.parse(guardado.definicion);
      declarativos.set(d.id, aFlujo(d, guardado.version, guardado.actualizado));
    } catch {
      /* archivo dañado: se ignora */
    }
  }
}

function aFlujo(d: Definicion, version: number, actualizado: number): Flujo {
  return {
    id: d.id, nombre: d.nombre, descripcion: d.descripcion, tipo: "declarativo", buzonReal: false, lote: false,
    trabajador: resolve(raiz, "puente", "flujo_declarativo.py"), version, actualizado, definicion: d,
  };
}

export function listar(): Flujo[] {
  return [...DE_CODIGO.filter((f) => existsSync(f.trabajador)), ...[...declarativos.values()].sort((a, b) => a.nombre.localeCompare(b.nombre))];
}

export function obtener(id: string): Flujo | undefined {
  return listar().find((f) => f.id === id);
}

/** Los problemas de una definición, con el mismo validador que la ejecuta. */
export function validar(entrada: unknown): Promise<{ ok: boolean; problemas: string[]; definicion?: Definicion }> {
  const r = definicion.safeParse(entrada);
  if (!r.success) return Promise.resolve({ ok: false, problemas: r.error.issues.map((i) => `${i.path.join(".") || "flujo"}: ${i.message}`) });
  return new Promise((resolver) => {
    const p = spawn(config.python, [resolve(raiz, "puente", "flujo_declarativo.py"), "validar"], { stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let salida = "";
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.on("exit", () => {
      try {
        const problemas = JSON.parse(salida.trim().split("\n").pop() ?? "[]") as string[];
        resolver({ ok: problemas.length === 0, problemas, definicion: r.data });
      } catch {
        resolver({ ok: false, problemas: ["no se pudo validar el flujo"], definicion: r.data });
      }
    });
    p.stdin.end(JSON.stringify(r.data));
  });
}

/** Guarda una definición válida (crea o reemplaza) y deja la versión anterior. */
export async function guardar(entrada: unknown): Promise<{ ok: boolean; problemas: string[]; flujo?: Flujo }> {
  const v = await validar(entrada);
  if (!v.ok || !v.definicion) return { ok: false, problemas: v.problemas };
  if (DE_CODIGO.some((f) => f.id === v.definicion!.id)) return { ok: false, problemas: [`«${v.definicion.id}» es un flujo de código: elige otro id`] };
  const previo = declarativos.get(v.definicion.id);
  const version = (previo?.version ?? 0) + 1;
  const actualizado = Date.now();
  await mkdir(join(carpetaDeclarativos(), "versiones"), { recursive: true });
  if (previo?.definicion) await writeFile(join(carpetaDeclarativos(), "versiones", `${previo.id}.v${previo.version}.json`), JSON.stringify(previo.definicion, null, 2), "utf8");
  await writeFile(join(carpetaDeclarativos(), `${v.definicion.id}.json`), JSON.stringify({ definicion: v.definicion, version, actualizado }, null, 2), "utf8");
  const f = aFlujo(v.definicion, version, actualizado);
  declarativos.set(f.id, f);
  return { ok: true, problemas: [], flujo: f };
}

export async function borrar(id: string): Promise<boolean> {
  if (!declarativos.has(id)) return false;
  declarativos.delete(id);
  await unlink(join(carpetaDeclarativos(), `${id}.json`)).catch(() => undefined);
  return true;
}

// ------------------------------------------------------------------ catálogos

const CATEGORIA: Record<string, string> = {
  entrada_correo: "entrada", filtro: "filtro", condicion: "ruta", ia_clasificar: "ia", ia_extraer: "ia",
  validar: "validacion", ia_redactar: "ia", salida: "salida",
};
const NOMBRE_TIPO: Record<string, string> = {
  entrada_correo: "Entrada de correo", filtro: "Filtro", condicion: "Condición", ia_clasificar: "IA · clasificar",
  ia_extraer: "IA · extraer datos", validar: "Validación", ia_redactar: "IA · redactar", salida: "Salida",
};

function describirConfig(n: Definicion["nodos"][number]): { id: string; descripcion: string }[] {
  const c = n.config as Record<string, unknown>;
  const r: { id: string; descripcion: string }[] = [];
  const add = (d: string) => r.push({ id: `${n.id}_${r.length + 1}`, descripcion: d });
  if (typeof c.instrucciones === "string" && c.instrucciones) add(`Instrucciones a la IA: ${c.instrucciones}`);
  if (Array.isArray(c.categorias)) add(`Categorías: ${(c.categorias as unknown[]).map((x) => (typeof x === "object" && x ? `${(x as { valor: string }).valor}` : String(x))).join(", ")} → variable «${String(c.variable ?? n.id)}»`);
  if (Array.isArray(c.campos)) for (const f of c.campos as { nombre: string; descripcion?: string; obligatorio?: boolean }[]) add(`Campo «${f.nombre}»${f.obligatorio ? " (obligatorio)" : ""}: ${f.descripcion ?? ""}`);
  if (Array.isArray(c.reglas)) for (const x of c.reglas as { campo: string; regla: string; valor?: unknown; mensaje?: string }[]) add(`${x.campo}: ${x.regla}${x.valor !== undefined ? ` ${JSON.stringify(x.valor)}` : ""}${x.mensaje ? ` — ${x.mensaje}` : ""}`);
  if (n.tipo === "filtro" || n.tipo === "condicion") add(`${String(c.campo)} ${String(c.operador ?? "contiene")} «${String(c.valor ?? "")}»: sale por «si» o «no»`);
  if (n.tipo === "salida") add(`Desenlace: ${String(c.desenlace)}${c.motivo ? ` · motivo ${String(c.motivo)}` : ""}${c.destino ? ` · destino ${String(c.destino)}` : ""}`);
  if (n.tipo === "entrada_correo") add(`Lee asunto, cuerpo y remitente${c.leer_adjuntos === false ? "" : `, y los adjuntos${c.ocr === false ? "" : " con OCR y visión"}`}`);
  return r;
}

/** El catálogo con que el lienzo dibuja un flujo declarativo: nodos, aristas y fases por profundidad. */
export function catalogoDeclarativo(d: Definicion): Catalogo & { fases: { id: string; nombre: string; nodos: string[] }[] } {
  const profundidad = new Map<string, number>();
  const entrada = d.nodos.find((n) => n.tipo === "entrada_correo")?.id ?? d.nodos[0]!.id;
  const cola: [string, number][] = [[entrada, 0]];
  while (cola.length) {
    const [id, p] = cola.shift()!;
    if ((profundidad.get(id) ?? -1) >= p || p > 40) continue;
    profundidad.set(id, p);
    for (const a of d.aristas.filter((x) => x.desde === id)) cola.push([a.hacia, p + 1]);
  }
  const columnas: string[][] = [];
  for (const n of d.nodos) (columnas[profundidad.get(n.id) ?? 0] ??= []).push(n.id);
  const fases = columnas.filter(Boolean).map((ids, i) => ({ id: `paso-${i + 1}`, nombre: i === 0 ? "Entrada" : ids.every((x) => d.nodos.find((n) => n.id === x)?.tipo === "salida") ? "Salidas" : `Paso ${i + 1}`, nodos: ids }));
  return {
    nodos: d.nodos.map((n) => ({
      id: n.id, nombre: n.nombre, descripcion: n.descripcion || NOMBRE_TIPO[n.tipo] || n.tipo, etapa_cronometro: n.id,
      usa_ia: n.tipo.startsWith("ia_"), categoria: CATEGORIA[n.tipo], tipo_declarativo: n.tipo, config: n.config,
      siguientes: d.aristas.filter((a) => a.desde === n.id).map((a) => a.hacia),
      desenlaces: n.tipo === "salida" ? [{ accion: String((n.config as { desenlace?: string }).desenlace ?? ""), cuando: "al llegar aquí" }] : [],
      reglas: describirConfig(n), parametros: [],
    })) as Catalogo["nodos"],
    aristas: d.aristas.map((a) => ({ desde: a.desde, hacia: a.hacia, etiqueta: a.cuando && a.cuando !== "*" ? a.cuando : undefined })),
    herramientas_agente: [], proveedores: [{ id: "vertex", nombre: `Vertex AI (${d.modelo})`, tipo: "ia" }],
    desenlaces: [...new Set(d.nodos.filter((n) => n.tipo === "salida").map((n) => String((n.config as { desenlace?: string }).desenlace)))].map((a) => ({ accion: a, descripcion: a })),
    variables_entorno: [], fases,
  };
}

let catalogoTutelas: Catalogo | null = null;

export function catalogoDe(id: string): Catalogo | null {
  const f = obtener(id);
  if (!f) return null;
  if (f.id === "pqrd") return catalogoDePqrd();
  if (f.id === "tutelas") {
    const ruta = resolve(raiz, "catalogo", "tutelas.json");
    if (!catalogoTutelas && existsSync(ruta)) catalogoTutelas = JSON.parse(readFileSync(ruta, "utf8")) as Catalogo;
    return catalogoTutelas;
  }
  return f.definicion ? catalogoDeclarativo(f.definicion) : null;
}
