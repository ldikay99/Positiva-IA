import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { join, resolve, sep } from "node:path";
import { config } from "./config.ts";

/**
 * El espacio propio de cada flujo, en <archivos>/<flujo>/:
 *
 * - entradas/<id>/<nombre> + meta.json: los archivos que se suben para correrlo
 *   (entrada_archivos, campos archivo de entrada_manual, webhook multipart, chat).
 *   Se guardan como OBJETOS del flujo (id, nombre, tipo, tamaño, sha256), no como
 *   rutas escritas a mano, y el motor los lee como los adjuntos de un correo.
 * - bandeja/<caso>/mensaje.json + adj/: su bandeja de prueba (.eml/.msg subidos) y
 *   lo traído de SU buzón (credencial del disparador). Nunca la bandeja de PQRD.
 *
 * Video y audio nunca entran: se rechazan por extensión, por el tipo declarado y
 * por su contenido (firma de los primeros bytes).
 */

export const ID_FLUJO = /^[a-z][a-z0-9-]{1,40}$/;
const ID_ARCHIVO = /^a-[0-9a-z]{6,40}$/;
export const MB = 1024 * 1024;
export const MAX_MB = 25;

export const EXT_VIDEO_AUDIO = new Set(["mp4", "m4v", "mov", "avi", "mkv", "webm", "wmv", "flv", "mpg", "mpeg", "3gp", "3g2", "ogv", "ts", "mts", "m2ts", "vob",
  "mp3", "wav", "ogg", "oga", "opus", "m4a", "aac", "flac", "wma", "aiff", "aif", "amr", "mid", "midi", "weba", "caf"]);
/** Lo que acepta un flujo si su disparador no dice otra cosa. */
export const TIPOS_PERMITIDOS = ["jpg", "jpeg", "png", "webp", "tif", "tiff", "bmp", "gif", "heic", "pdf", "doc", "docx", "xls", "xlsx", "csv",
  "ppt", "pptx", "txt", "md", "json", "xml", "html", "eml", "msg", "zip", "rtf", "odt", "ods"];

export const carpetaDe = (flujo: string) => {
  if (!ID_FLUJO.test(flujo)) throw new Error("id de flujo inválido");
  return join(config.archivos, flujo);
};
const entradasDe = (flujo: string) => join(carpetaDe(flujo), "entradas");
export const bandejaDe = (flujo: string) => join(carpetaDe(flujo), "bandeja");

export const extension = (nombre: string) => (nombre.includes(".") ? nombre.toLowerCase().split(".").pop()!.trim() : "");

/** Por qué estos bytes son video o audio (por su firma), o null. HEIC/AVIF (también «ftyp») son imágenes y pasan. */
export function firmaDeVideoOAudio(d: Uint8Array): string | null {
  const t = (a: number, b: number) => Buffer.from(d.subarray(a, b)).toString("latin1");
  if (d.length < 4) return null;
  if (d.length >= 12 && t(4, 8) === "ftyp") {
    const marca = t(8, 12).toLowerCase();
    if (["heic", "heix", "hevc", "hevx", "heim", "heis", "mif1", "msf1", "avif", "avis"].includes(marca)) return null;
    return /^m4[abp]/.test(marca) ? "audio (M4A)" : "video (MP4, MOV o 3GP)";
  }
  if (t(0, 4) === "RIFF" && d.length >= 12) {
    const sub = t(8, 12);
    if (sub === "WAVE") return "audio (WAV)";
    if (sub === "AVI ") return "video (AVI)";
  }
  if (t(0, 4) === "FORM" && d.length >= 12 && ["AIFF", "AIFC"].includes(t(8, 12))) return "audio (AIFF)";
  if (d[0] === 0x1a && d[1] === 0x45 && d[2] === 0xdf && d[3] === 0xa3) return "video (WebM o MKV)";
  if (t(0, 4) === "OggS") return "audio o video (Ogg)";
  if (t(0, 4) === "fLaC") return "audio (FLAC)";
  if (t(0, 3) === "ID3") return "audio (MP3)";
  if (t(0, 3) === "FLV") return "video (FLV)";
  if (t(0, 5) === "#!AMR") return "audio (AMR)";
  if (t(0, 4) === "MThd") return "audio (MIDI)";
  if (t(0, 4) === "caff") return "audio (CAF)";
  if (t(0, 4) === ".RMF") return "video (RealMedia)";
  if (d[0] === 0x30 && d[1] === 0x26 && d[2] === 0xb2 && d[3] === 0x75) return "video o audio (WMV o WMA)";
  if (d[0] === 0x00 && d[1] === 0x00 && d[2] === 0x01 && (d[3] === 0xba || d[3] === 0xb3)) return "video (MPEG)";
  if (d[0] === 0x47 && d.length > 376 && d[188] === 0x47 && d[376] === 0x47) return "video (MPEG-TS)";
  // trama de MP3/AAC: 11 bits en 1 (FF Ex/Fx); FF FE y FF FF son marcas de texto UTF-16, no audio
  if (d[0] === 0xff && d[1] !== undefined && (d[1] & 0xe0) === 0xe0 && d[1] !== 0xfe && d[1] !== 0xff) return "audio (MP3 o AAC)";
  return null;
}

/** Las reglas de archivos de un flujo: las de su entrada_archivos o las de siempre. */
export function reglasDe(definicion: { nodos: { tipo: string; config?: unknown }[] } | undefined): { tipos: Set<string>; maxMb: number; maxArchivos: number } {
  const n = definicion?.nodos.find((x) => x.tipo === "entrada_archivos");
  const c = (n?.config ?? {}) as { tipos_permitidos?: unknown; max_mb?: unknown; max_archivos?: unknown };
  const lista = Array.isArray(c.tipos_permitidos) && c.tipos_permitidos.length ? c.tipos_permitidos.map((x) => String(x).trim().toLowerCase().replace(/^\./, "")) : TIPOS_PERMITIDOS;
  return {
    tipos: new Set(lista.filter((x) => !EXT_VIDEO_AUDIO.has(x))),
    maxMb: typeof c.max_mb === "number" ? Math.max(1, Math.min(MAX_MB, c.max_mb)) : MAX_MB,
    maxArchivos: typeof c.max_archivos === "number" ? Math.max(1, Math.min(50, c.max_archivos)) : 20,
  };
}

/** Por qué no se acepta un archivo, o null. */
export function problemaDeArchivo(nombre: string, tipo: string, datos: Uint8Array, reglas: { tipos: Set<string>; maxMb: number }): string | null {
  const ext = extension(nombre);
  if (EXT_VIDEO_AUDIO.has(ext) || /^(video|audio)\//i.test(tipo)) return `«${nombre}» es video o audio: esos archivos no se aceptan`;
  const firma = firmaDeVideoOAudio(datos);
  if (firma) return `«${nombre}» es ${firma} por su contenido: video y audio no se aceptan`;
  if (!ext || !reglas.tipos.has(ext)) return `«${nombre}»: este flujo no acepta archivos ${ext ? `.${ext}` : "sin extensión"} (acepta ${[...reglas.tipos].join(", ")})`;
  if (!datos.length) return `«${nombre}» está vacío`;
  if (datos.length > reglas.maxMb * MB) return `«${nombre}» pasa de ${reglas.maxMb} MB`;
  return null;
}

export interface FichaArchivo {
  id: string;
  nombre: string;
  tipo: string;
  tamano: number;
  sha256: string;
  subido: number;
  origen?: string;
}

/** Un nombre que se puede guardar tal cual (con tildes), sin rutas ni caracteres de control. */
export function nombreSeguro(nombre: string): string {
  const base = String(nombre ?? "").replace(/\\/g, "/").split("/").pop() ?? "";
  // eslint-disable-next-line no-control-regex
  const limpio = base.replace(/[\u0000-\u001f\u007f<>:"|?*]/g, "").replace(/^\.+/, "").trim().slice(0, 160);
  return limpio || "archivo";
}

/** Guarda un archivo en el espacio del flujo (ya revisado) y devuelve su ficha. */
export async function guardarArchivo(flujo: string, nombre: string, tipo: string, datos: Buffer, origen = "web"): Promise<FichaArchivo> {
  const id = `a-${randomBytes(9).toString("hex")}`;
  const carpeta = join(entradasDe(flujo), id);
  await mkdir(carpeta, { recursive: true });
  const n = nombreSeguro(nombre);
  await writeFile(join(carpeta, n), datos);
  const ficha: FichaArchivo = { id, nombre: n, tipo: (tipo || "application/octet-stream").slice(0, 120), tamano: datos.length, sha256: createHash("sha256").update(datos).digest("hex"), subido: Date.now(), origen };
  await writeFile(join(carpeta, "meta.json"), JSON.stringify(ficha, null, 2), "utf8");
  return ficha;
}

/** La ficha y la ruta de un archivo del flujo (solo de ESE flujo). */
export function archivoDe(flujo: string, id: string): (FichaArchivo & { ruta: string }) | null {
  if (!ID_FLUJO.test(flujo) || !ID_ARCHIVO.test(id)) return null;
  const carpeta = join(entradasDe(flujo), id);
  try {
    const ficha = JSON.parse(readFileSync(join(carpeta, "meta.json"), "utf8")) as FichaArchivo;
    const ruta = resolve(carpeta, ficha.nombre);
    if (!ruta.startsWith(resolve(carpeta) + sep) || !existsSync(ruta)) return null;
    return { ...ficha, id, ruta };
  } catch {
    return null;
  }
}

export async function listarArchivos(flujo: string): Promise<FichaArchivo[]> {
  const r: FichaArchivo[] = [];
  for (const id of await readdir(entradasDe(flujo)).catch(() => [] as string[])) {
    const a = archivoDe(flujo, id);
    if (a) r.push({ id: a.id, nombre: a.nombre, tipo: a.tipo, tamano: a.tamano, sha256: a.sha256, subido: a.subido, ...(a.origen ? { origen: a.origen } : {}) });
  }
  return r.sort((a, b) => b.subido - a.subido);
}

export async function borrarArchivo(flujo: string, id: string): Promise<boolean> {
  if (!archivoDe(flujo, id)) return false;
  await rm(join(entradasDe(flujo), id), { recursive: true, force: true });
  return true;
}

/** Los ids de archivo que trae una entrada: entrada.archivos y los campos archivo/archivos del disparador. */
function idsDeEntrada(entrada: unknown, definicion?: { nodos: { tipo: string; config?: unknown }[] }): string[] {
  if (!entrada || typeof entrada !== "object") return [];
  const e = entrada as Record<string, unknown>;
  const disp = definicion?.nodos.find((n) => n.tipo === "entrada_manual" || n.tipo === "entrada_archivos");
  const c = (disp?.config ?? {}) as { campos?: { nombre?: string; tipo?: string }[]; campos_extra?: { nombre?: string; tipo?: string }[] };
  const campos = [...(c.campos ?? []), ...(c.campos_extra ?? [])].filter((x) => x?.tipo === "archivo" || x?.tipo === "archivos").map((x) => String(x.nombre));
  const ids: string[] = [];
  for (const k of ["archivos", ...campos]) {
    const v = e[k];
    for (const x of Array.isArray(v) ? v : v ? [v] : []) {
      const id = typeof x === "string" ? x : x && typeof x === "object" ? String((x as { id?: unknown }).id ?? "") : "";
      if (ID_ARCHIVO.test(id) && !ids.includes(id)) ids.push(id);
    }
  }
  return ids;
}

/** Para la orden de una corrida: las fichas (con su ruta) de los archivos que pide la entrada. */
export function resolverArchivos(flujo: string, entrada: unknown, definicion?: { nodos: { tipo: string; config?: unknown }[] }): (FichaArchivo & { ruta: string })[] {
  return idsDeEntrada(entrada, definicion).map((id) => archivoDe(flujo, id)).filter((x): x is FichaArchivo & { ruta: string } => x !== null);
}

/** Los ids que no existen en el flujo (para rechazar la corrida antes de encolarla). */
export function archivosQueFaltan(flujo: string, entrada: unknown, definicion?: { nodos: { tipo: string; config?: unknown }[] }): string[] {
  return idsDeEntrada(entrada, definicion).filter((id) => !archivoDe(flujo, id));
}

// ------------------------------------------------------------------ multipart

export interface Parte {
  campo: string;
  archivo?: string;
  tipo: string;
  datos: Buffer;
}

/** Un cuerpo multipart/form-data ya leído: sus partes (campos y archivos). */
export function multipart(cuerpo: Buffer, tipoContenido: string): Parte[] {
  const m = /boundary=(?:"([^"]+)"|([^;\s]+))/i.exec(tipoContenido);
  if (!m) throw new Error("multipart sin boundary");
  const limite = Buffer.from(`--${m[1] ?? m[2]}`);
  const partes: Parte[] = [];
  let i = cuerpo.indexOf(limite);
  while (i >= 0 && partes.length < 200) {
    let ini = i + limite.length;
    if (cuerpo[ini] === 0x2d && cuerpo[ini + 1] === 0x2d) break;  // --limite-- : fin
    if (cuerpo[ini] === 0x0d) ini += 2;
    const finCab = cuerpo.indexOf("\r\n\r\n", ini);
    if (finCab < 0) break;
    const cab = cuerpo.subarray(ini, finCab).toString("utf8");
    const sig = cuerpo.indexOf(limite, finCab + 4);
    if (sig < 0) break;
    const datos = cuerpo.subarray(finCab + 4, sig - 2);
    const disp = /content-disposition:[^\r\n]*/i.exec(cab)?.[0] ?? "";
    const campo = /\bname="([^"]*)"/i.exec(disp)?.[1] ?? "";
    const estrella = /filename\*=(?:UTF-8'')?([^;\r\n]+)/i.exec(disp)?.[1];
    const archivo = estrella ? decodeURIComponent(estrella.trim().replace(/^"|"$/g, "")) : /\bfilename="([^"]*)"/i.exec(disp)?.[1];
    const tipo = /content-type:\s*([^\r\n;]+)/i.exec(cab)?.[1]?.trim() ?? (archivo !== undefined ? "application/octet-stream" : "text/plain");
    partes.push({ campo, ...(archivo !== undefined ? { archivo } : {}), tipo, datos: Buffer.from(datos) });
    i = sig;
  }
  return partes;
}

// ------------------------------------------------------- bandeja de prueba

/** Sube un correo .eml/.msg a la bandeja de prueba del flujo (lo convierte Python en un caso). */
export async function correoDePrueba(flujo: string, nombre: string, datos: Buffer): Promise<{ ok: true; caso: string; asunto: string; adjuntos: number } | { ok: false; error: string }> {
  const ext = extension(nombre);
  if (ext !== "eml" && ext !== "msg") return { ok: false, error: `«${nombre}»: a la bandeja de prueba solo entran correos .eml o .msg` };
  if (datos.length > MAX_MB * MB) return { ok: false, error: `«${nombre}» pasa de ${MAX_MB} MB` };
  await mkdir(bandejaDe(flujo), { recursive: true });
  return new Promise((resolver) => {
    const p = spawn(config.python, [resolve(config.raiz, "puente", "correo_de_archivo.py"), bandejaDe(flujo), nombreSeguro(nombre)], { env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let salida = "";
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    const reloj = setTimeout(() => p.kill(), 60_000);
    p.on("exit", () => {
      clearTimeout(reloj);
      try {
        resolver(JSON.parse(salida.trim().split("\n").pop() ?? "{}"));
      } catch {
        resolver({ ok: false, error: "no se pudo leer el correo" });
      }
    });
    p.stdin.end(datos);
  });
}

export async function borrarCorreoDePrueba(flujo: string, caso: string): Promise<boolean> {
  if (!/^[\w.-]{1,200}$/.test(caso)) return false;
  const ruta = join(bandejaDe(flujo), caso);
  if (!existsSync(join(ruta, "mensaje.json"))) return false;
  await rm(ruta, { recursive: true, force: true });
  return true;
}

// ---------------------------------------------------------- uso y limpieza

const recibidos = () => join(config.datos, "recibidos");
const tablaDe = (flujo: string) => join(config.datos, "tablas", `flujo-${flujo}.sqlite`);

function medir(carpeta: string): { archivos: number; bytes: number } {
  let archivos = 0;
  let bytes = 0;
  const pila = [carpeta];
  while (pila.length) {
    const c = pila.pop()!;
    let hijos: import("node:fs").Dirent[] = [];
    try {
      hijos = readdirSync(c, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const h of hijos) {
      const r = join(c, h.name);
      if (h.isDirectory()) pila.push(r);
      else if (h.isFile() && h.name !== "meta.json" && h.name !== "mensaje.json") {
        archivos++;
        bytes += statSync(r).size;
      }
    }
  }
  return { archivos, bytes };
}

/** Lo que tiene un flujo en su espacio (para decir qué se borra). */
export async function uso(flujo: string): Promise<{ archivos: number; bytes: number; correos_de_prueba: number; correos_recibidos: number; tabla: boolean }> {
  const entradas = (await readdir(entradasDe(flujo)).catch(() => [] as string[])).filter((x) => ID_ARCHIVO.test(x)).length;
  const { bytes } = medir(carpetaDe(flujo));
  const correos = (await readdir(bandejaDe(flujo)).catch(() => [] as string[])).filter((x) => existsSync(join(bandejaDe(flujo), x, "mensaje.json"))).length;
  const rec = (await readdir(recibidos()).catch(() => [] as string[])).filter((x) => x.startsWith(`correo-${flujo}-`)).length;
  return { archivos: entradas, bytes, correos_de_prueba: correos, correos_recibidos: rec, tabla: existsSync(tablaDe(flujo)) };
}

/** Borra TODO lo del espacio de un flujo: archivos, bandeja de prueba, su tabla, sus correos recibidos y el estado de su sondeo. */
export async function borrarTodo(flujo: string): Promise<void> {
  if (!ID_FLUJO.test(flujo)) return;
  await rm(carpetaDe(flujo), { recursive: true, force: true });
  await rm(tablaDe(flujo), { force: true });
  await rm(join(config.datos, "disparadores", `${flujo}.json`), { force: true });
  for (const x of await readdir(recibidos()).catch(() => [] as string[])) if (x.startsWith(`correo-${flujo}-`)) await rm(join(recibidos(), x), { recursive: true, force: true });
}

/** Copia el espacio de archivos (no la bandeja ni los recibidos) de un flujo a otro: para que la copia lea los mismos archivos que su original. */
export async function copiarArchivos(desde: string, hacia: string): Promise<number> {
  let n = 0;
  for (const a of await listarArchivos(desde)) {
    const x = archivoDe(desde, a.id);
    if (!x) continue;
    const destino = join(entradasDe(hacia), a.id);
    await mkdir(destino, { recursive: true });
    await writeFile(join(destino, x.nombre), await readFile(x.ruta));
    await writeFile(join(destino, "meta.json"), JSON.stringify({ ...a, origen: `copia de ${desde}` }, null, 2), "utf8");
    n++;
  }
  return n;
}

export async function existeCarpeta(flujo: string): Promise<boolean> {
  return (await stat(carpetaDe(flujo)).catch(() => null))?.isDirectory() ?? false;
}
