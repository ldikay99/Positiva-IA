import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import * as bd from "./bd/conexion.ts";
import * as estadoBd from "./bd/estado.ts";
import { join } from "node:path";
import { config } from "./config.ts";

/**
 * La compuerta de radicación de cada flujo de código (pqrd, tutelas).
 *
 * - Apagada (por defecto): el flujo corre completo en simulación. Lee de verdad lo
 *   que se puede leer en solo lectura y TODA escritura queda retenida y visible.
 * - Encendida: el trabajador corre como el job (MODO_TEST=false, servicios reales,
 *   escrituras reales: SGDEA, buzón, trazas).
 *
 * Encenderla exige DOS llaves a la vez:
 *   1. una persona lo confirma (encabezado X-Confirmado-Por-Persona: si, que solo
 *      manda la web tras la confirmación explícita; Vera nunca lo manda);
 *   2. el despliegue lo permite: PERMITIR_RADICACION_REAL=si en el entorno del
 *      contenedor. No está en docker-compose: sin ella la compuerta no se enciende.
 * Apagarla nunca pide nada. Si la segunda llave desaparece, la compuerta queda
 * apagada en la práctica aunque el archivo diga lo contrario (`radicacionReal`).
 *
 * El estado vive en <estado>/compuertas.json.
 */

export const FLUJOS_CON_COMPUERTA = new Set(["pqrd", "tutelas"]);
/**
 * Qué flujos tienen compuerta: pqrd, tutelas y sus copias (instancias propias con el
 * mismo motor). Lo fija el servidor con el registro de flujos; cada copia tiene la
 * SUYA, que nace apagada (no hereda nada del original).
 */
export const enlace = { tieneCompuerta: (flujo: string): boolean => FLUJOS_CON_COMPUERTA.has(flujo) };

export interface EstadoCompuerta {
  radicacion_real: boolean;
  permitido_en_despliegue: boolean;
  cambiado_por?: string;
  cambiado?: number;
}

interface Guardada {
  radicacion_real: boolean;
  cambiado_por?: string;
  cambiado?: number;
}

const ruta = () => join(config.datos, "compuertas.json");
let estado: Record<string, Guardada> = {};
let cargado = false;

/** La segunda llave: solo la pone quien despliega, en el entorno del contenedor. */
export const permitidoEnDespliegue = (entorno: NodeJS.ProcessEnv = process.env) => String(entorno.PERMITIR_RADICACION_REAL ?? "").trim().toLowerCase() === "si";

export async function cargar(): Promise<void> {
  if (bd.activa()) {
    estado = (await estadoBd.configuracion.ajuste<Record<string, Guardada>>("compuertas")) ?? {};
    cargado = true;
    return;
  }
  try {
    estado = JSON.parse(await readFile(ruta(), "utf8")) as Record<string, Guardada>;
  } catch {
    estado = {};
  }
  cargado = true;
}

async function guardar(): Promise<void> {
  if (bd.activa()) return estadoBd.configuracion.ponerAjuste("compuertas", estado);
  await mkdir(config.datos, { recursive: true });
  const tmp = `${ruta()}.${Date.now()}.tmp`;
  await writeFile(tmp, JSON.stringify(estado, null, 2), "utf8");
  await rename(tmp, ruta());
}

export function obtener(flujo: string): EstadoCompuerta {
  const g = estado[flujo];
  return {
    radicacion_real: g?.radicacion_real === true,
    permitido_en_despliegue: permitidoEnDespliegue(),
    ...(g?.cambiado_por ? { cambiado_por: g.cambiado_por } : {}),
    ...(g?.cambiado ? { cambiado: g.cambiado } : {}),
  };
}

/** Si ese flujo debe escribir de verdad AHORA: la compuerta encendida Y la segunda llave puesta. */
export function radicacionReal(flujo: string): boolean {
  return cargado && enlace.tieneCompuerta(flujo) && estado[flujo]?.radicacion_real === true && permitidoEnDespliegue();
}

/** Un flujo borrado (una copia) no deja su compuerta guardada. */
export async function olvidar(flujo: string): Promise<void> {
  if (!(flujo in estado)) return;
  delete estado[flujo];
  await guardar();
}

/** La regla de las dos llaves, sin efectos (para probarla sin tocar el entorno). */
export function puedeCambiar(encender: boolean, confirmado: boolean, segundaLlave: boolean): { ok: true } | { ok: false; codigo: number; error: string } {
  if (!encender) return { ok: true };
  if (!segundaLlave) return { ok: false, codigo: 403, error: "este despliegue no permite la radicación real: falta PERMITIR_RADICACION_REAL=si en el entorno del contenedor" };
  if (!confirmado) return { ok: false, codigo: 403, error: "encender la radicación real requiere la confirmación explícita de una persona (X-Confirmado-Por-Persona: si)" };
  return { ok: true };
}

export type ResultadoCambio = { ok: true; compuerta: EstadoCompuerta; cambio: boolean } | { ok: false; codigo: number; error: string };

export async function cambiar(flujo: string, cuerpo: unknown, confirmado: boolean, quien = "persona en la web"): Promise<ResultadoCambio> {
  if (!enlace.tieneCompuerta(flujo)) return { ok: false, codigo: 404, error: "solo los flujos de código (pqrd, tutelas y sus copias) tienen compuerta de radicación" };
  const encender = (cuerpo as { radicacion_real?: unknown } | null)?.radicacion_real;
  if (typeof encender !== "boolean") return { ok: false, codigo: 400, error: "manda {radicacion_real: true|false}" };
  const regla = puedeCambiar(encender, confirmado, permitidoEnDespliegue());
  if (!regla.ok) return regla;
  const previo = estado[flujo]?.radicacion_real === true;
  estado[flujo] = { radicacion_real: encender, cambiado_por: quien.slice(0, 80), cambiado: Date.now() };
  await guardar();
  return { ok: true, compuerta: obtener(flujo), cambio: previo !== encender };
}

/** El texto corto para la interfaz y los avisos. */
export function rotulo(flujo: string): string {
  return radicacionReal(flujo) ? "Radicación real: ENCENDIDA (escribe como el job)" : "Radicación real: apagada (simulación)";
}
