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
}

interface MensajeGraph {
  subject?: string;
  receivedDateTime?: string;
  body?: { contentType?: string; content?: string };
  from?: { emailAddress?: { name?: string; address?: string } };
  _grupo?: string;
  _motivo_prod?: string;
  _adjuntos?: { name: string; archivo?: string; contentType?: string; size?: number; isInline?: boolean }[];
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

async function leerMensaje(caso: string): Promise<MensajeGraph> {
  return JSON.parse(await readFile(join(config.bandeja, caso, "mensaje.json"), "utf8")) as MensajeGraph;
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
  };
}

export async function listar(): Promise<Correo[]> {
  const marca = (await stat(config.bandeja).catch(() => null))?.mtimeMs ?? 0;
  if (cache && cache.leido >= marca) return cache.correos;
  const nombres = (await readdir(config.bandeja, { withFileTypes: true }).catch(() => []))
    .filter((d) => d.isDirectory() && CASO.test(d.name))
    .map((d) => d.name)
    .sort();
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
  const base = resolve(config.bandeja, caso, "adj");
  const ruta = resolve(base, archivo);
  return ruta.startsWith(base + sep) ? ruta : null;
}
