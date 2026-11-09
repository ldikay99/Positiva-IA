import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readdir, readFile, unlink, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { config } from "./config.ts";

/**
 * La estructura editable de un flujo de código (pqrd, tutelas y sus copias): sus etapas como nodos «etapa»
 * (que se apagan o reconfiguran), los nodos declarativos que se le insertan y sus aristas.
 * Las reglas viven en `puente/estructura_codigo.py` (una sola fuente para el servidor y el trabajador).
 * Sin archivo guardado el flujo es el del código, igual que en producción.
 * Se guarda en <estado>/flujos_codigo/<id>.estructura.json y la versión anterior en <id>.estructura.v<N>.json.
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
    flujo: id, base, editada: Boolean(g), version: g?.version ?? 0, actualizado: g?.actualizado ?? null, nota: g?.nota ?? null,
    definicion: g ? structuredClone(g.definicion) : await porDefecto(base),
    tramos: g?.tramos ?? [], intercambio: g?.intercambio ?? null, apagadas: g?.apagadas ?? [],
    reglas: await describir(base),
  };
}

export function validar(base: string, definicion: unknown, entorno: string[] = []): Promise<Validacion> {
  return python<Validacion>("validar", { base, definicion, entorno }).catch((e: unknown) => ({ ok: false, problemas: [e instanceof Error ? e.message : String(e)] }));
}

export async function guardar(id: string, base: string, definicion: unknown, nota?: string, entorno: string[] = []): Promise<Validacion & { guardada?: Guardada }> {
  const v = await validar(base, definicion, entorno);
  if (!v.ok) return v;
  const previa = guardadas.get(id);
  await mkdir(carpeta(), { recursive: true });
  if (previa) await writeFile(rutaVersion(id, previa.version), JSON.stringify(previa, null, 2), "utf8");
  const g: Guardada = {
    definicion: definicion as DefinicionEstructura, version: (previa?.version ?? 0) + 1, actualizado: Date.now(),
    ...(nota?.trim() ? { nota: nota.trim().slice(0, 300) } : {}), tramos: v.tramos ?? [], intercambio: v.intercambio ?? null, apagadas: v.apagadas ?? [],
  };
  await writeFile(ruta(id), JSON.stringify(g, null, 2), "utf8");
  guardadas.set(id, g);
  return { ...v, guardada: g };
}

/** Vuelve a la estructura del código (la editada queda como versión, por si se quiere recuperar). */
export async function restablecer(id: string): Promise<boolean> {
  const g = guardadas.get(id);
  if (!g) return false;
  await writeFile(rutaVersion(id, g.version), JSON.stringify(g, null, 2), "utf8");
  await unlink(ruta(id)).catch(() => undefined);
  guardadas.delete(id);
  return true;
}

/** Una copia nace con la estructura de su original (si la tenía) y desde ahí diverge. */
export async function copiar(desde: string, hacia: string): Promise<void> {
  const g = guardadas.get(desde);
  if (!g) return;
  const nueva: Guardada = { ...structuredClone(g), version: 1, actualizado: Date.now(), nota: `copiada de ${desde} v${g.version}` };
  await mkdir(carpeta(), { recursive: true });
  await writeFile(ruta(hacia), JSON.stringify(nueva, null, 2), "utf8");
  guardadas.set(hacia, nueva);
}

export async function borrarTodo(id: string): Promise<void> {
  guardadas.delete(id);
  await unlink(ruta(id)).catch(() => undefined);
  for (const n of await readdir(carpeta()).catch(() => [] as string[]))
    if (n.startsWith(`${id}.estructura.v`)) await unlink(join(carpeta(), n)).catch(() => undefined);
}

export const existe = (id: string) => guardadas.has(id) && existsSync(ruta(id));
