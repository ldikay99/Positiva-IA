import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { deflateRawSync, inflateRawSync } from "node:zlib";
import { variable, type Cliente } from "./conexion.ts";

/**
 * Cifrado por organización con llaves envueltas (docs/DATOS_SENSIBLES.md › Cifrado):
 *
 * - Cada organización tiene llaves de datos (DEK, AES-256-GCM) en vera.llaves_datos, guardadas envueltas por la
 *   llave maestra (KEK). «general» para lo Personal confidencial e Interno con secretos; «sensible», una por mes,
 *   para el contenido de ejecuciones, aprobaciones y aplazadas (vencida su retención se destruye la del mes).
 * - La KEK sale de un proveedor:
 *     local  VERA_KEK_LOCAL (base64 de 32 bytes) o VERA_KEK_LOCAL_ARCHIVO: para Docker local y las pruebas;
 *     kms    Cloud KMS (VERA_KEK_KMS=projects/…/cryptoKeys/…): se conecta en el despliegue en GCP con la
 *            identidad del servicio. Aquí no se implementa ni se llama (este repositorio no toca GCP).
 * - Cada dato cifrado lleva como dato asociado (AAD) «<organización>|<tabla>|<id>»: copiado a otra organización u
 *   otra fila, no se descifra.
 * - Formato: 0x01 ‖ iv[12] ‖ tag[16] ‖ JSON, o 0x02 ‖ iv ‖ tag ‖ JSON comprimido (deflate) si pasa de 1 KB.
 * - Una DEK en claro solo vive en la memoria del servidor (y nunca va a los trabajadores de Python).
 */

export type Proposito = "general" | "sensible" | "indice";

export interface Llave {
  id: string;
  dek: Buffer;
}

interface ProveedorKek {
  ref: string;
  version: string | null;
  envolver(dek: Buffer, aad: string): Buffer;
  desenvolver(envuelta: Buffer, aad: string): Buffer;
}

function gcm(llave: Buffer, datos: Buffer, aad: string): Buffer {
  const iv = randomBytes(12);
  const c = createCipheriv("aes-256-gcm", llave, iv);
  c.setAAD(Buffer.from(aad, "utf8"));
  const cifrado = Buffer.concat([c.update(datos), c.final()]);
  return Buffer.concat([iv, c.getAuthTag(), cifrado]);
}

function gcmAbrir(llave: Buffer, x: Buffer, aad: string): Buffer {
  if (x.length < 28) throw new Error("dato cifrado incompleto");
  const d = createDecipheriv("aes-256-gcm", llave, x.subarray(0, 12));
  d.setAAD(Buffer.from(aad, "utf8"));
  d.setAuthTag(x.subarray(12, 28));
  return Buffer.concat([d.update(x.subarray(28)), d.final()]);
}

function kekLocal(): ProveedorKek | null {
  const archivo = variable("VERA_KEK_LOCAL_ARCHIVO")?.trim();
  const crudo = (variable("VERA_KEK_LOCAL")?.trim() || (archivo ? readFileSync(archivo, "utf8").trim() : "")) || "";
  if (!crudo) return null;
  const kek = Buffer.from(crudo, "base64");
  if (kek.length !== 32) throw new Error("VERA_KEK_LOCAL tiene que ser base64 de 32 bytes (openssl rand -base64 32)");
  const huella = createHash("sha256").update(kek).digest("hex").slice(0, 16);
  return {
    ref: `local:${huella}`,
    version: null,
    envolver: (dek, aad) => gcm(kek, dek, aad),
    desenvolver: (envuelta, aad) => gcmAbrir(kek, envuelta, aad),
  };
}

let proveedor: ProveedorKek | null = null;

function kek(): ProveedorKek {
  if (proveedor) return proveedor;
  if (variable("VERA_KEK_KMS")?.trim())
    throw new Error("VERA_KEK_KMS (Cloud KMS) se conecta en el despliegue en GCP; este entorno solo admite VERA_KEK_LOCAL");
  proveedor = kekLocal();
  if (!proveedor) throw new Error("falta la llave maestra: define VERA_KEK_LOCAL (base64 de 32 bytes) o VERA_KEK_LOCAL_ARCHIVO");
  return proveedor;
}

/** Revisa al arrancar que haya llave maestra (y falla con el arreglo exacto si no). */
export function revisarKek(): string {
  return kek().ref;
}

/** Para las pruebas: otra KEK (o volver a leerla del entorno). */
export function reiniciar(): void {
  proveedor = null;
  porPeriodo.clear();
  porId.clear();
}

const porPeriodo = new Map<string, Llave>();
const porId = new Map<string, Buffer>();

/** Las DEK que se conocen en memoria se olvidan (tras una transacción revertida: pudieron no quedar). */
export function olvidarLlaves(): void {
  porPeriodo.clear();
  porId.clear();
}

const aadDek = (org: string, proposito: Proposito, periodo: string | null) => `${org}|dek|${proposito}|${periodo ?? "-"}`;

/** El primer día del mes (UTC) de una fecha, como YYYY-MM-01. */
export const periodoDe = (ms: number): string => `${new Date(ms).toISOString().slice(0, 7)}-01`;

/** La DEK vigente de la organización para ese propósito (y mes); la crea si no existe. */
export async function llave(c: Cliente, org: string, proposito: Proposito, periodo: string | null = null): Promise<Llave> {
  const clave = `${org}|${proposito}|${periodo ?? "-"}`;
  const enMemoria = porPeriodo.get(clave);
  if (enMemoria) return enMemoria;
  const p = kek();
  const buscar = () => c.query<{ id: string; dek_envuelta: Buffer }>(
    "SELECT id, dek_envuelta FROM vera.llaves_datos WHERE proposito = $1 AND periodo IS NOT DISTINCT FROM $2::date AND destruida IS NULL",
    [proposito, periodo]);
  let r = await buscar();
  if (!r.rows[0]) {
    const dek = randomBytes(32);
    await c.query(
      `INSERT INTO vera.llaves_datos (organizacion_id, proposito, periodo, kek_ref, kek_version, dek_envuelta)
       VALUES ($1, $2, $3::date, $4, $5, $6)
       ON CONFLICT (organizacion_id, proposito, coalesce(periodo, 'epoch'::date)) WHERE destruida IS NULL DO NOTHING`,
      [org, proposito, periodo, p.ref, p.version, p.envolver(dek, aadDek(org, proposito, periodo))]);
    r = await buscar();
  }
  const fila = r.rows[0];
  if (!fila) throw new Error("no se pudo crear la llave de datos de la organización");
  const l = { id: fila.id, dek: p.desenvolver(fila.dek_envuelta, aadDek(org, proposito, periodo)) };
  porPeriodo.set(clave, l);
  porId.set(`${org}|${l.id}`, l.dek);
  return l;
}

/** Una DEK por su id (para leer lo que se cifró con ella, aunque ya no sea la vigente). */
export async function llavePorId(c: Cliente, org: string, id: string): Promise<Buffer> {
  const enMemoria = porId.get(`${org}|${id}`);
  if (enMemoria) return enMemoria;
  const r = await c.query<{ proposito: Proposito; periodo: string | null; dek_envuelta: Buffer | null }>(
    "SELECT proposito, to_char(periodo, 'YYYY-MM-DD') AS periodo, dek_envuelta FROM vera.llaves_datos WHERE id = $1", [id]);
  const f = r.rows[0];
  if (!f) throw new Error(`llave de datos ${id} no encontrada en esta organización`);
  if (!f.dek_envuelta) throw new Error(`la llave de datos ${id} fue destruida (retención vencida): su contenido ya no se puede leer`);
  const dek = kek().desenvolver(f.dek_envuelta, aadDek(org, f.proposito, f.periodo));
  porId.set(`${org}|${id}`, dek);
  return dek;
}

/** Cifra un valor JSON atado a su organización, tabla e id. */
export function sellar(l: Llave | Buffer, aad: string, valor: unknown): Buffer {
  const dek = Buffer.isBuffer(l) ? l : l.dek;
  const json = Buffer.from(JSON.stringify(valor ?? null), "utf8");
  const comprimir = json.length > 1024;
  return Buffer.concat([Buffer.from([comprimir ? 2 : 1]), gcm(dek, comprimir ? deflateRawSync(json) : json, aad)]);
}

export function abrir<T>(dek: Buffer, aad: string, x: Buffer): T {
  const formato = x[0];
  if (formato !== 1 && formato !== 2) throw new Error("formato de cifrado desconocido");
  const claro = gcmAbrir(dek, x.subarray(1), aad);
  return JSON.parse((formato === 2 ? inflateRawSync(claro) : claro).toString("utf8")) as T;
}

/** Descifra con la DEK de la fila (buscándola si no está en memoria). */
export async function abrirCon<T>(c: Cliente, org: string, llaveId: string, aad: string, x: Buffer): Promise<T> {
  return abrir<T>(await llavePorId(c, org, llaveId), aad, x);
}

export const aad = (org: string, tabla: string, id: string): string => `${org}|${tabla}|${id}`;
