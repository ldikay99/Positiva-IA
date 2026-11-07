import { existsSync } from "node:fs";
import { readdir, readFile, stat } from "node:fs/promises";
import { join, resolve, sep } from "node:path";
import { config } from "./config.ts";

export interface Adjunto {
  nombre: string;
  archivo: string;
  tipo: string;
  tamano: number;
  enLinea: boolean;
}

export interface Correo {
  caso: string;
  asunto: string;
  remitente: string;
  remitenteCorreo: string;
  recibido: string;
  vistaPrevia: string;
  adjuntos: Adjunto[];
  desenlaceProduccion: string;
  motivoProduccion: string;
  /** "buzon": traído del buzón real, con sus etiquetas de hoy; "local": de la carpeta descargada. */
  origen: "buzon" | "local";
  categorias: string[];
  /**
   * De dónde sale, por flujo: «universo» (la bandeja descargada de PQRD), «buzon_real» (traído del buzón
   * del job), «prueba» (un .eml/.msg subido al flujo), «buzon_flujo» (traído del buzón del flujo) o
   * «recibido» (lo trajo su correo_nuevo).
   */
  fuente?: "universo" | "buzon_real" | "prueba" | "buzon_flujo" | "recibido";
}

/** Lo que el servidor sabe de un flujo para elegir su bandeja (sin importar flujos.ts). */
export interface FlujoDeBandeja {
  id: string;
  tipo: "codigo" | "declarativo";
  /** De código: pqrd o tutelas (también para sus copias). */
  base?: string;
}

interface MensajeGraph {
  subject?: string;
  receivedDateTime?: string;
  body?: { contentType?: string; content?: string };
  from?: { emailAddress?: { name?: string; address?: string } };
  _grupo?: string;
  _motivo_prod?: string;
  _vivo?: boolean;
  categories?: string[];
  _adjuntos?: { name: string; archivo?: string; contentType?: string; size?: number; isInline?: boolean }[];
  /** Los de tutelas los marca su script del buzón: el trabajador junta el hilo con esto. */
  _flujo?: string;
  _origen?: string;
}

const CASO = /^[\w.-]+$/;
let cache: { correos: Correo[]; leido: number } | null = null;

export function textoPlano(html: string): string {
  return html
    .replace(/<(style|script)[\s\S]*?<\/\1>/gi, " ")
    .replace(/<br\s*\/?>|<\/(p|div|tr|li|h\d)>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#(\d+);/g, (_, n: string) => String.fromCharCode(Number(n)))
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s*\n+/g, "\n\n")
    .trim();
}

/** La carpeta de un caso: los traídos del buzón real mandan sobre los de la carpeta local. */
function baseDe(caso: string): string {
  return existsSync(join(config.vivo, caso, "mensaje.json")) ? config.vivo : config.bandeja;
}

async function leerMensaje(caso: string): Promise<MensajeGraph> {
  return JSON.parse(await readFile(join(baseDe(caso), caso, "mensaje.json"), "utf8")) as MensajeGraph;
}

function aCorreo(caso: string, m: MensajeGraph): Correo {
  const cuerpo = m.body?.contentType === "html" ? textoPlano(m.body.content ?? "") : (m.body?.content ?? "");
  return {
    caso,
    asunto: m.subject ?? "",
    remitente: m.from?.emailAddress?.name ?? "",
    remitenteCorreo: m.from?.emailAddress?.address ?? "",
    recibido: m.receivedDateTime ?? "",
    vistaPrevia: cuerpo.slice(0, 220),
    adjuntos: (m._adjuntos ?? []).map((a) => ({
      nombre: a.name,
      archivo: a.archivo ?? "",
      tipo: a.contentType ?? "",
      tamano: a.size ?? 0,
      enLinea: Boolean(a.isInline),
    })),
    desenlaceProduccion: m._grupo ?? "",
    motivoProduccion: m._motivo_prod ?? "",
    origen: m._vivo ? "buzon" : "local",
    categorias: m._vivo ? (m.categories ?? []) : [],
  };
}

async function casosEn(carpeta: string): Promise<string[]> {
  return (await readdir(carpeta, { withFileTypes: true }).catch(() => []))
    .filter((d) => d.isDirectory() && CASO.test(d.name))
    .map((d) => d.name);
}

/** Que la bandeja vuelva a leerse: se acaban de traer correos del buzón real. */
export function olvidar(): void {
  cache = null;
  caches.clear();
}

export async function listar(): Promise<Correo[]> {
  const marca = Math.max(
    (await stat(config.bandeja).catch(() => null))?.mtimeMs ?? 0,
    (await stat(config.vivo).catch(() => null))?.mtimeMs ?? 0,
  );
  if (cache && cache.leido >= marca) return cache.correos;
  const nombres = [...new Set([...(await casosEn(config.vivo)), ...(await casosEn(config.bandeja))])].sort();
  const correos = (
    await Promise.all(
      nombres.map(async (caso) => {
        try {
          return aCorreo(caso, await leerMensaje(caso));
        } catch {
          return null;
        }
      }),
    )
  ).filter((c): c is Correo => c !== null);
  correos.sort((a, b) => b.recibido.localeCompare(a.recibido));
  cache = { correos, leido: Date.now() };
  return correos;
}

export function casoValido(caso: string): boolean {
  return CASO.test(caso);
}

export async function detalle(caso: string) {
  if (!casoValido(caso)) return null;
  try {
    const m = await leerMensaje(caso);
    const html = m.body?.contentType === "html" ? (m.body.content ?? "") : "";
    return { ...aCorreo(caso, m), cuerpo: html ? textoPlano(html) : (m.body?.content ?? "") };
  } catch {
    return null;
  }
}

/** Ruta de un adjunto, solo si queda dentro de la carpeta del caso. */
export function rutaAdjunto(caso: string, archivo: string): string | null {
  if (!casoValido(caso)) return null;
  const base = resolve(baseDe(caso), caso, "adj");
  const ruta = resolve(base, archivo);
  return ruta.startsWith(base + sep) ? ruta : null;
}

// ============================================================ por flujo
//
// Cada flujo ve SOLO su correspondencia:
// - pqrd (y sus copias): la bandeja descargada (universo) y lo traído del buzón del job de PQRD;
// - tutelas (y sus copias): lo traído del buzón de tutelas (marcado con _flujo: "tutelas");
// - un flujo declarativo: su bandeja de prueba y lo traído de su buzón (<archivos>/<flujo>/bandeja)
//   y, si tiene correo_nuevo, lo que trajo su sondeo. Nunca el universo de PQRD.

const ID_FLUJO = /^[a-z][a-z0-9-]{1,40}$/;
const bandejaPropia = (flujo: string) => join(config.archivos, flujo, "bandeja");
const recibidos = () => join(config.datos, "recibidos");

/** Las carpetas de casos de un flujo, cada una con su fuente y su filtro. */
function fuentesDe(f: FlujoDeBandeja): { carpeta: string; fuente: NonNullable<Correo["fuente"]>; acepta: (caso: string, m: MensajeGraph) => boolean }[] {
  if (f.tipo === "codigo") {
    const tutelas = (f.base ?? f.id) === "tutelas";
    return tutelas
      ? [{ carpeta: config.vivo, fuente: "buzon_real", acepta: (_c, m) => m._flujo === "tutelas" }]
      : [{ carpeta: config.vivo, fuente: "buzon_real", acepta: (_c, m) => m._flujo !== "tutelas" }, { carpeta: config.bandeja, fuente: "universo", acepta: () => true }];
  }
  if (!ID_FLUJO.test(f.id)) return [];
  return [
    { carpeta: bandejaPropia(f.id), fuente: "prueba", acepta: () => true },
    { carpeta: recibidos(), fuente: "recibido", acepta: (caso) => caso.startsWith(`correo-${f.id}-`) },
  ];
}

const caches = new Map<string, { correos: Correo[]; leido: number }>();

async function leerEn(carpeta: string, caso: string): Promise<MensajeGraph | null> {
  try {
    return JSON.parse(await readFile(join(carpeta, caso, "mensaje.json"), "utf8")) as MensajeGraph;
  } catch {
    return null;
  }
}

/** La correspondencia de un flujo: solo sus fuentes, del más nuevo al más viejo. */
export async function listarDe(f: FlujoDeBandeja): Promise<Correo[]> {
  const fuentes = fuentesDe(f);
  const marca = Math.max(0, ...(await Promise.all(fuentes.map(async (x) => (await stat(x.carpeta).catch(() => null))?.mtimeMs ?? 0))));
  const clave = `${f.id}|${f.base ?? ""}`;
  const previa = caches.get(clave);
  if (previa && previa.leido >= marca) return previa.correos;
  const vistos = new Set<string>();
  const correos: Correo[] = [];
  for (const x of fuentes) {
    for (const caso of (await casosEn(x.carpeta)).sort()) {
      if (vistos.has(caso)) continue;
      const m = await leerEn(x.carpeta, caso);
      if (!m || !x.acepta(caso, m)) continue;
      vistos.add(caso);
      const fuente = x.fuente === "prueba" && String(m._origen ?? "").startsWith("buzon_del_flujo") ? "buzon_flujo" : x.fuente;
      correos.push({ ...aCorreo(caso, m), fuente });
    }
  }
  correos.sort((a, b) => b.recibido.localeCompare(a.recibido));
  caches.set(clave, { correos, leido: Date.now() });
  return correos;
}

/** Que la correspondencia de los flujos vuelva a leerse (se subió o se trajo un correo). */
export function olvidarDe(id?: string): void {
  if (!id) caches.clear();
  else for (const k of [...caches.keys()]) if (k.startsWith(`${id}|`)) caches.delete(k);
}

/** La carpeta de un caso de ESE flujo, o null si el caso no es suyo. */
export async function carpetaDeCaso(f: FlujoDeBandeja, caso: string): Promise<string | null> {
  if (!casoValido(caso)) return null;
  for (const x of fuentesDe(f)) {
    const m = await leerEn(x.carpeta, caso);
    if (m && x.acepta(caso, m)) return join(x.carpeta, caso);
  }
  return null;
}

/** Si ese caso es de la correspondencia del flujo (para correrlo con él). */
export async function esDe(f: FlujoDeBandeja, caso: string): Promise<boolean> {
  return (await carpetaDeCaso(f, caso)) !== null;
}

export async function detalleDe(f: FlujoDeBandeja, caso: string) {
  const carpeta = await carpetaDeCaso(f, caso);
  if (!carpeta) return null;
  const m = (await leerEn(join(carpeta, ".."), caso))!;
  const html = m.body?.contentType === "html" ? (m.body.content ?? "") : "";
  return { ...aCorreo(caso, m), cuerpo: html ? textoPlano(html) : (m.body?.content ?? "") };
}

export async function rutaAdjuntoDe(f: FlujoDeBandeja, caso: string, archivo: string): Promise<string | null> {
  const carpeta = await carpetaDeCaso(f, caso);
  if (!carpeta) return null;
  const base = resolve(carpeta, "adj");
  const ruta = resolve(base, archivo);
  return ruta.startsWith(base + sep) ? ruta : null;
}
