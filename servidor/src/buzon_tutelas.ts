import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { config } from "./config.ts";
import { enmascarar, enmascararTexto, variantes } from "./enmascarar.ts";

/** El entorno del flujo tutelas (TUTELAS-MICROSOFT-*) y sus valores secretos, para enmascararlos. Lo fija el servidor. */
export const enlace = { entorno: (): Record<string, string> => ({}), secretos: (): string[] => [] };

/**
 * El buzón de producción de TUTELAS, solo lectura, a través de `puente/buzon_tutelas.py`.
 *
 * Python lee el secreto de Graph (TUTELAS-MICROSOFT-CLIENT-SECRET) en Secret
 * Manager con el ADC del contenedor y lo usa en memoria; aquí no pasa ninguno.
 * El script solo hace GET a Graph: listar la carpeta que lee el job de tutelas,
 * agrupada por hilo en el orden en que la toma, y bajar un correo con su hilo y
 * sus adjuntos para correrlo en el visor. Los correos quedan en `config.vivo`
 * con `_flujo: "tutelas"`, que es lo que usa el trabajador para juntar el hilo.
 */

const script = process.env.RUTA_BUZON_TUTELAS ?? resolve(config.raiz, "puente", "buzon_tutelas.py");

export interface CorreoDeTutelas {
  id: string;
  caso: string;
  asunto: string;
  remitente: string;
  remitenteCorreo: string;
  recibido: string;
  categorias: string[];
  conAdjuntos: boolean;
  /** Últimos 10 caracteres del conversationId: los correos con el mismo valor son un hilo. */
  hilo: string;
  /** Posición del hilo en el orden del job (1 = el primero que tomaría). */
  ordenHilo: number;
  correosEnHilo: number;
  /** El más reciente del hilo: el que el job usa para extraer los datos. */
  principal: boolean;
  traido?: boolean;
}

export interface ListadoDeTutelas {
  carpeta: string;
  total: number | null;
  correos: CorreoDeTutelas[];
  error?: string;
}

interface Traidos {
  casos: Record<string, string>;
  principales: Record<string, string>;
  traidos: number;
  error?: string;
}

function correrPython(args: string[], tope = 180_000): Promise<unknown> {
  return new Promise((resolver, rechazar) => {
    const mascaras = variantes(enlace.secretos());
    const p = spawn(config.python, [script, ...args], {
      env: { ...process.env, ...enlace.entorno(), PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1", RUTA_TRABAJO: join(config.datos, "trabajo") },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let salida = "";
    let errores = "";
    const reloj = setTimeout(() => p.kill(), tope);
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.stderr.setEncoding("utf8").on("data", (t: string) => (errores += t));
    p.on("exit", () => {
      clearTimeout(reloj);
      // El progreso del motor va a stderr; la respuesta es la última línea JSON.
      const linea = salida.trim().split("\n").filter((l) => l.trim().startsWith("{")).pop() ?? "";
      try {
        resolver(enmascarar(JSON.parse(linea) as unknown, mascaras));
      } catch {
        rechazar(new Error(enmascararTexto(errores.trim().split("\n").pop() || "el buzón de tutelas no respondió", mascaras)));
      }
    });
  });
}

let ultimo: { n: number; cuando: number; listado: ListadoDeTutelas } | null = null;

/** Lo que tomaría el job de tutelas ahora: los hilos en su orden, cada uno del más viejo al más nuevo. */
export async function listar(n: number, fresco = false): Promise<ListadoDeTutelas> {
  if (!fresco && ultimo && ultimo.n === n && Date.now() - ultimo.cuando < 30_000) return marcarTraidos(ultimo.listado);
  const r = (await correrPython(["listar", String(n)])) as ListadoDeTutelas;
  if (r.error) return { carpeta: "", total: null, correos: [], error: r.error };
  ultimo = { n, cuando: Date.now(), listado: r };
  return marcarTraidos(r);
}

function marcarTraidos(l: ListadoDeTutelas): ListadoDeTutelas {
  return { ...l, correos: l.correos.map((c) => ({ ...c, traido: existsSync(join(config.vivo, c.caso, "mensaje.json")) })) };
}

/**
 * Baja los correos (y los demás de su hilo) a la carpeta del visor. Devuelve un
 * caso por HILO, en el orden de los ids: el job procesa el hilo como una unidad,
 * así que dos ids del mismo hilo dan una sola corrida.
 */
export async function traer(ids: string[]): Promise<string[]> {
  await mkdir(config.vivo, { recursive: true });
  const principales: Record<string, string> = {};
  // De a 10 por llamada: cada correo con adjuntos es una descarga, y su hilo también.
  for (let i = 0; i < ids.length; i += 10) {
    const r = (await correrPython(["traer", config.vivo, ...ids.slice(i, i + 10)], 300_000)) as Traidos;
    if (typeof r.error === "string") throw new Error(r.error);
    Object.assign(principales, r.principales);
  }
  return [...new Set(ids.map((id) => principales[id]).filter((c): c is string => Boolean(c)))];
}

/** Vacío si el buzón de tutelas se puede leer; si no, por qué. */
export async function disponible(): Promise<string> {
  const r = (await correrPython(["disponible"], 60_000)) as { error?: string };
  return r.error ?? "";
}
