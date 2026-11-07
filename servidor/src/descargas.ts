import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { config } from "./config.ts";

/**
 * Enlaces de descarga firmados y con vencimiento para los archivos de un flujo:
 *
 *   /api/descargas/<flujo>/<archivo>?vence=<segundos epoch>&firma=<hmac>
 *
 * Los usan Teams, Slack, los webhooks salientes (modo enlace) y la respuesta de un webhook:
 * quien recibe el enlace baja ESE archivo hasta que vence, sin ver nada más del flujo. La firma
 * es HMAC-SHA256 con una clave propia de la plataforma (<datos>/clave_descargas, se crea sola,
 * nunca sale en eventos ni resultados: el motor la enmascara). La URL pública sale de URL_PUBLICA
 * (p. ej. https://positiva-ia.ejemplo.gov.co); sin ella, los enlaces son relativos y solo sirven
 * dentro de la plataforma.
 */

export const VENCE_S = Number(process.env.DESCARGAS_VENCE_H ?? 72) * 3600;
const ID_FLUJO = /^[a-z][a-z0-9-]{1,40}$/;
const ID_ARCHIVO = /^a-[0-9a-z]{6,40}$/;

let clave: string | null = null;

function laClave(): string {
  if (clave) return clave;
  const ruta = join(config.datos, "clave_descargas");
  try {
    if (existsSync(ruta)) clave = readFileSync(ruta, "utf8").trim();
  } catch {
    clave = null;
  }
  if (!clave || clave.length < 32) {
    clave = randomBytes(32).toString("hex");
    mkdirSync(config.datos, { recursive: true });
    writeFileSync(ruta, clave, { encoding: "utf8", mode: 0o600 });
  }
  return clave;
}

export const urlPublica = () => String(process.env.URL_PUBLICA ?? "").trim().replace(/\/+$/, "");

/** Lo que el motor necesita para firmar enlaces en una corrida (la clave va enmascarada en todo lo que sale). */
export function paraOrden(): { base: string; clave: string; vence_s: number } {
  return { base: urlPublica(), clave: laClave(), vence_s: VENCE_S };
}

const firmaDe = (flujo: string, archivo: string, vence: number) => createHmac("sha256", laClave()).update(`${flujo}/${archivo}/${vence}`).digest("hex").slice(0, 40);

/** Un enlace firmado (igual al que arma el motor). */
export function enlace(flujo: string, archivo: string, venceS = VENCE_S): { url: string; vence: string } {
  const vence = Math.floor(Date.now() / 1000) + venceS;
  return { url: `${urlPublica()}/api/descargas/${flujo}/${archivo}?vence=${vence}&firma=${firmaDe(flujo, archivo, vence)}`, vence: new Date(vence * 1000).toISOString() };
}

/** Por qué no vale un enlace, o null. */
export function problema(flujo: string, archivo: string, vence: unknown, firma: unknown): string | null {
  if (!ID_FLUJO.test(flujo) || !ID_ARCHIVO.test(archivo)) return "enlace inválido";
  const v = Number(vence);
  if (!Number.isInteger(v) || typeof firma !== "string" || !/^[0-9a-f]{40}$/.test(firma)) return "enlace inválido";
  if (v < Date.now() / 1000) return "el enlace venció: pide uno nuevo";
  const a = Buffer.from(firmaDe(flujo, archivo, v));
  const b = Buffer.from(firma);
  return a.length === b.length && timingSafeEqual(a, b) ? null : "enlace inválido";
}
