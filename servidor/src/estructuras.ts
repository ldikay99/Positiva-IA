import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readdir, readFile, unlink, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { config } from "./config.ts";
import * as versiones from "./versiones.ts";

/**
 * La estructura editable de un flujo de código (pqrd, tutelas y sus copias): sus etapas como nodos «etapa»
 * (que se apagan o reconfiguran), los nodos declarativos que se le insertan y sus aristas.
 * Las reglas viven en `puente/estructura_codigo.py` (una sola fuente para el servidor y el trabajador).
 * Sin archivo guardado el flujo es el del código, igual que en producción.
 * Se guarda en <estado>/flujos_codigo/<id>.estructura.json y la versión anterior en <id>.estructura.v<N>.json.
 * Los números nunca se repiten: volver a la del código también es una versión (versiones.ts guarda su autor y resumen).
 */

export interface NodoEstructura {
  id: string;
  tipo: string;
  nombre?: string;
  descripcion?: string;
  config?: Record<string, unknown>;
}
export interface DefinicionEstructura {
  nodos: NodoEstructura[];
  aristas: { desde: string; hacia: string; cuando?: string }[];
}
export interface Tramo {
  punto: string;
  nombre: string;
  desde: string;
  hacia: string;
  nodos: string[];
  primero: string;
}
export interface Guardada {
  definicion: DefinicionEstructura;
  version: number;
  actualizado: number;
  nota?: string;
  tramos: Tramo[];
  intercambio: [string, string] | null;
  apagadas: string[];
}
export interface Validacion {
  ok: boolean;
  problemas: string[];
  tramos?: Tramo[];
  intercambio?: [string, string] | null;
  apagadas?: string[];
}

const carpeta = () => join(config.datos, "flujos_codigo");
const ruta = (id: string) => join(carpeta(), `${id}.estructura.json`);
const rutaVersion = (id: string, v: number) => join(carpeta(), `${id}.estructura.v${v}.json`);
const guardadas = new Map<string, Guardada>();
const descripciones = new Map<string, Record<string, unknown>>();
const porDefectoDe = new Map<string, DefinicionEstructura>();

function python<T>(cmd: string, entrada: unknown): Promise<T> {
  return new Promise((resolver, rechazar) => {
    const p = spawn(config.python, [resolve(config.raiz, "puente", "estructura_codigo.py"), cmd], { stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let salida = "";
    let errores = "";
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.stderr.setEncoding("utf8").on("data", (t: string) => (errores += t));
    p.on("exit", (codigo) => {
      try {
        resolver(JSON.parse(salida.trim().split("\n").pop() ?? "null") as T);
      } catch {
        rechazar(new Error(`estructura_codigo.py ${cmd} falló (código ${codigo}): ${errores.slice(-400)}`));
      }
    });
    p.stdin.end(JSON.stringify(entrada));
  });
}

export async function cargar(): Promise<void> {
  guardadas.clear();
  await mkdir(carpeta(), { recursive: true });
  for (const a of (await readdir(carpeta())).filter((x) => x.endsWith(".estructura.json"))) {
    try {
      guardadas.set(a.slice(0, -".estructura.json".length), JSON.parse(await readFile(join(carpeta(), a), "utf8")) as Guardada);
    } catch {
      /* archivo dañado: el flujo queda como el del código */
    }
  }
}

export const obtener = (id: string): Guardada | undefined => guardadas.get(id);

export async function describir(base: string): Promise<Record<string, unknown>> {
  if (!descripciones.has(base)) descripciones.set(base, await python<Record<string, unknown>>("describir", { base }));
  return descripciones.get(base)!;
}

export async function porDefecto(base: string): Promise<DefinicionEstructura> {
  if (!porDefectoDe.has(base)) porDefectoDe.set(base, await python<DefinicionEstructura>("por_defecto", { base }));
  return structuredClone(porDefectoDe.get(base)!);
}

/** Lo que ve quien edita: la estructura vigente (la guardada o la del código), si está editada y cómo se edita. */
export async function vista(id: string, base: string) {
  const g = guardadas.get(id);
  return {
    flujo: id, base, editada: Boolean(g), version: await versionActual(id), actualizado: g?.actualizado ?? null, nota: g?.nota ?? null,
    definicion: g ? structuredClone(g.definicion) : await porDefecto(base),
    tramos: g?.tramos ?? [], intercambio: g?.intercambio ?? null, apagadas: g?.apagadas ?? [],
    reglas: await describir(base),
  };
}

export function validar(base: string, definicion: unknown, entorno: string[] = []): Promise<Validacion> {
  return python<Validacion>("validar", { base, definicion, entorno }).catch((e: unknown) => ({ ok: false, problemas: [e instanceof Error ? e.message : String(e)] }));
}

/** El número más alto usado por este flujo (guardada, versiones en disco o del historial): la siguiente nunca pisa una anterior. */
async function ultimaVersion(id: string): Promise<number> {
  let n = guardadas.get(id)?.version ?? 0;
  const patron = new RegExp(`^${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\.estructura\\.v(\\d+)\\.json$`);
  for (const a of await readdir(carpeta()).catch(() => [] as string[])) {
    const m = patron.exec(a);
    if (m) n = Math.max(n, Number(m[1]));
  }
  for (const k of Object.keys(await versiones.leer(id))) n = Math.max(n, Number(k));
  return n;
}

/** La versión en uso: la guardada o, sin editar, la del código (0, o la última vez que se volvió a ella). */
export async function versionActual(id: string): Promise<number> {
  const g = guardadas.get(id);
  if (g) return g.version;
  const metas = await versiones.leer(id);
  return Math.max(0, ...Object.entries(metas).filter(([, m]) => m.del_codigo).map(([k]) => Number(k)));
}

export async function guardar(id: string, base: string, definicion: unknown, nota?: string, entorno: string[] = []): Promise<Validacion & { guardada?: Guardada }> {
  const v = await validar(base, definicion, entorno);
  if (!v.ok) return v;
  const previa = guardadas.get(id);
  // Guardar lo mismo que ya está no es una versión nueva (el historial no se llena de «sin cambios»).
  if (previa && JSON.stringify(previa.definicion) === JSON.stringify(definicion)) return { ...v, guardada: previa };
  const antes = previa ? previa.definicion : await porDefecto(base).catch(() => null);
  const versionPrevia = await versionActual(id);
  await mkdir(carpeta(), { recursive: true });
  if (previa) await writeFile(rutaVersion(id, previa.version), JSON.stringify(previa, null, 2), "utf8");
  const g: Guardada = {
    definicion: definicion as DefinicionEstructura, version: (await ultimaVersion(id)) + 1, actualizado: Date.now(),
    ...(nota?.trim() ? { nota: nota.trim().slice(0, 300) } : {}), tramos: v.tramos ?? [], intercambio: v.intercambio ?? null, apagadas: v.apagadas ?? [],
  };
  await writeFile(ruta(id), JSON.stringify(g, null, 2), "utf8");
  guardadas.set(id, g);
  await versiones.anotar(id, g.version, { antes, despues: g.definicion, nota: g.nota, previa: versionPrevia });
  return { ...v, guardada: g };
}

/** Vuelve a la estructura del código y lo deja como versión nueva (la editada queda en el historial). */
export async function restablecerConVersion(id: string, base?: string): Promise<{ ok: boolean; version?: number }> {
  const g = guardadas.get(id);
  if (!g) return { ok: false };
  await writeFile(rutaVersion(id, g.version), JSON.stringify(g, null, 2), "utf8");
  await unlink(ruta(id)).catch(() => undefined);
  guardadas.delete(id);
  const version = (await ultimaVersion(id)) + 1;
  const despues = base ? await porDefecto(base).catch(() => null) : null;
  await versiones.anotar(id, version, { antes: g.definicion, despues, nota: "Volvió a la estructura del código", previa: g.version, extra: { del_codigo: true } });
  return { ok: true, version };
}

/** Vuelve a la estructura del código (la editada queda como versión, por si se quiere recuperar). */
export async function restablecer(id: string, base?: string): Promise<boolean> {
  return (await restablecerConVersion(id, base)).ok;
}

// ------------------------------------------------------------------ versiones

export interface VersionEstructura {
  version: number;
  actualizado: number;
  nota?: string;
  actual?: boolean;
  del_codigo?: boolean;
}

const leerGuardada = async (id: string, v: number): Promise<Guardada | null> => {
  try {
    return JSON.parse(await readFile(rutaVersion(id, v), "utf8")) as Guardada;
  } catch {
    return null;
  }
};

/** Todas las versiones de la estructura: la del código (0), las editadas y cada vuelta a la del código. */
export async function listarVersiones(id: string): Promise<VersionEstructura[]> {
  const actual = await versionActual(id);
  const metas = await versiones.leer(id);
  const r = new Map<number, VersionEstructura>();
  r.set(0, { version: 0, actualizado: 0, nota: "La estructura del código", del_codigo: true });
  const patron = new RegExp(`^${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\.estructura\\.v(\\d+)\\.json$`);
  for (const a of await readdir(carpeta()).catch(() => [] as string[])) {
    const m = patron.exec(a);
    if (!m) continue;
    const g = await leerGuardada(id, Number(m[1]));
    if (g) r.set(Number(m[1]), { version: Number(m[1]), actualizado: g.actualizado, ...(g.nota ? { nota: g.nota } : {}) });
  }
  for (const [k, m] of Object.entries(metas)) if (m.del_codigo) r.set(Number(k), { version: Number(k), actualizado: m.fecha ?? 0, nota: "La estructura del código", del_codigo: true });
  const g = guardadas.get(id);
  if (g) r.set(g.version, { version: g.version, actualizado: g.actualizado, ...(g.nota ? { nota: g.nota } : {}) });
  const x = r.get(actual);
  if (x) x.actual = true;
  return [...r.values()].sort((a, b) => b.version - a.version);
}

/** La definición de una versión (la del código si esa versión lo era); null si no existe. */
export async function definicionDe(id: string, base: string, v: number): Promise<DefinicionEstructura | null> {
  const g = guardadas.get(id);
  if (g && g.version === v) return structuredClone(g.definicion);
  const d = await leerGuardada(id, v);
  if (d) return d.definicion;
  if (v === 0 || (await versiones.leer(id))[v]?.del_codigo) return porDefecto(base);
  return null;
}

/** Vuelve a una versión: queda como versión nueva (nunca se borra la historia). */
export async function restaurar(id: string, base: string, v: number, entorno: string[] = []): Promise<{ ok: boolean; version?: number; sin_cambio?: boolean; error?: string; problemas?: string[]; codigo?: number }> {
  const actual = await versionActual(id);
  if (v === actual) return { ok: true, version: actual, sin_cambio: true };
  const d = await definicionDe(id, base, v);
  if (!d) return { ok: false, codigo: 404, error: `no existe la versión ${v}` };
  const delCodigo = v === 0 || Boolean((await versiones.leer(id))[v]?.del_codigo);
  if (delCodigo) {
    if (!guardadas.get(id)) return { ok: true, version: actual, sin_cambio: true };
    const r = await restablecerConVersion(id, base);
    if (r.ok && r.version !== undefined) await versiones.completar(id, r.version, { restaurada_de: v, por: "restaurar" });
    return r.ok ? { ok: true, version: r.version } : { ok: false, codigo: 400, error: "no se pudo volver a la estructura del código" };
  }
  const r = await guardar(id, base, d, `Restaurada la versión ${v}`, entorno);
  if (!r.ok || !r.guardada) return { ok: false, codigo: 400, error: "esa versión ya no es válida", problemas: r.problemas };
  await versiones.completar(id, r.guardada.version, { restaurada_de: v, por: "restaurar" });
  return { ok: true, version: r.guardada.version };
}

/** Una copia nace con la estructura de su original (si la tenía) y desde ahí diverge. */
export async function copiar(desde: string, hacia: string): Promise<void> {
  const g = guardadas.get(desde);
  if (!g) return;
  const nueva: Guardada = { ...structuredClone(g), version: 1, actualizado: Date.now(), nota: `copiada de ${desde} v${g.version}` };
  await mkdir(carpeta(), { recursive: true });
  await writeFile(ruta(hacia), JSON.stringify(nueva, null, 2), "utf8");
  guardadas.set(hacia, nueva);
  await versiones.anotar(hacia, 1, { nota: nueva.nota });
}

export async function borrarTodo(id: string): Promise<void> {
  guardadas.delete(id);
  await versiones.borrar(id);
  await unlink(ruta(id)).catch(() => undefined);
  for (const n of await readdir(carpeta()).catch(() => [] as string[]))
    if (n.startsWith(`${id}.estructura.v`)) await unlink(join(carpeta(), n)).catch(() => undefined);
}

export const existe = (id: string) => guardadas.has(id) && existsSync(ruta(id));
