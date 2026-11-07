import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { config } from "./config.ts";
import { enmascarar, enmascararTexto, variantes } from "./enmascarar.ts";

/**
 * El entorno del flujo pqrd (MICROSOFT_*, OUTLOOK_FOLDER_ID…): si lo tiene, Python
 * usa esas credenciales en vez de Secret Manager. Los valores secretos se enmascaran
 * en todo lo que vuelve de Python. Lo fija el servidor.
 */
export const enlace = { entorno: (): Record<string, string> => ({}), secretos: (): string[] => [] };

/**
 * El buzón de producción, solo lectura, a través de `puente/buzon_vivo.py`.
 *
 * Python lee los secretos de Graph en Secret Manager con el ADC del contenedor
 * y los usa en memoria; aquí no pasa ninguno. El script solo hace GET a Graph:
 * listar la carpeta que lee el job, en el orden en que la toma, y bajar un
 * correo con sus adjuntos para correrlo en el visor.
 */

export interface CorreoDelBuzon {
  id: string;
  caso: string;
  asunto: string;
  remitente: string;
  remitenteCorreo: string;
  recibido: string;
  categorias: string[];
  conAdjuntos: boolean;
  hilo: string;
  traido?: boolean;
}

export interface Listado {
  carpeta: string;
  total: number | null;
  correos: CorreoDelBuzon[];
  error?: string;
}

function correrPython(args: string[], tope = 180_000): Promise<unknown> {
  return new Promise((resolver, rechazar) => {
    const mascaras = variantes(enlace.secretos());
    const p = spawn(config.python, [config.buzonVivo, ...args], {
      env: { ...process.env, ...enlace.entorno(), PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let salida = "";
    let errores = "";
    const reloj = setTimeout(() => p.kill(), tope);
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.stderr.setEncoding("utf8").on("data", (t: string) => (errores += t));
    p.on("exit", () => {
      clearTimeout(reloj);
      // El motor imprime avisos propios al importarse; la respuesta es la última línea JSON.
      const linea = salida.trim().split("\n").filter((l) => l.trim().startsWith("{")).pop() ?? "";
      try {
        resolver(enmascarar(JSON.parse(linea) as unknown, mascaras));
      } catch {
        rechazar(new Error(enmascararTexto(errores.trim().split("\n").pop() || "el buzón no respondió", mascaras)));
      }
    });
  });
}

let ultimo: { n: number; cuando: number; listado: Listado } | null = null;

/** Lo que tomaría el job ahora: un correo por hilo, del más viejo al más nuevo. */
export async function listar(n: number, fresco = false): Promise<Listado> {
  if (!fresco && ultimo && ultimo.n === n && Date.now() - ultimo.cuando < 30_000) return marcarTraidos(ultimo.listado);
  const r = (await correrPython(["listar", String(n)])) as Listado & { error?: string };
  if (r.error) return { carpeta: "", total: null, correos: [], error: r.error };
  ultimo = { n, cuando: Date.now(), listado: r };
  return marcarTraidos(r);
}

function marcarTraidos(l: Listado): Listado {
  return { ...l, correos: l.correos.map((c) => ({ ...c, traido: existsSync(join(config.vivo, c.caso, "mensaje.json")) })) };
}

/** Baja los correos a la carpeta del visor. Devuelve los casos en el mismo orden. */
export async function traer(ids: string[]): Promise<string[]> {
  await mkdir(config.vivo, { recursive: true });
  const hechos: Record<string, string> = {};
  // De a 10 por llamada: cada correo con adjuntos es una descarga.
  for (let i = 0; i < ids.length; i += 10) {
    const r = (await correrPython(["traer", config.vivo, ...ids.slice(i, i + 10)], 300_000)) as Record<string, string> & { error?: string };
    if (typeof r.error === "string") throw new Error(r.error);
    Object.assign(hechos, r);
  }
  return ids.map((id) => hechos[id]).filter((c): c is string => Boolean(c));
}
