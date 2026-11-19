import { createHash, randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { config } from "./config.ts";
import * as bd from "./bd/conexion.ts";
import * as estadoBd from "./bd/estado.ts";

/**
 * Las conversaciones del asistente. Cada una es una sesión aparte: su título,
 * el flujo en que empezó y todos sus mensajes —con los adjuntos y los pasos que
 * dio la IA—, que se le vuelven a pasar completos en cada pregunta para que
 * recuerde lo dicho. Viven en el volumen del contenedor.
 */

export interface AdjuntoChat {
  id: string;
  nombre: string;
  tipo: string;
  tamano: number;
}

export interface PasoChat {
  nombre: string;
  argumentos?: unknown;
  resultado?: string;
  /** Cuántas herramientas corrieron a la vez con esta (si fue más de una). */
  paralelo?: number;
  /** Las cifras del resultado completo (el resultado va recortado): de ahí pueden salir las de un documento después. */
  cifras?: string[];
  /** confirmado | en_curso | propuesto | no_confirmado | fallo (vera_herramientas.situacion). */
  situacion?: string;
}

export interface MensajeChat {
  rol: "usuario" | "asistente";
  texto: string;
  t: number;
  adjuntos?: AdjuntoChat[];
  pasos?: PasoChat[];
  cambiados?: string[];
  /** Archivos que generó una ejecución y Vera muestra en el chat (se abren desde la ejecución). */
  archivos?: { corrida: string; id: string; nombre: string; tipo: string; tamano?: number }[];
  error?: boolean;
  /** Dicho en una llamada de voz. */
  voz?: boolean;
  /** Acciones que la IA propuso y la persona confirma con un botón. */
  propuestas?: { accion: string; id: string; estado?: "hecha" | "descartada" }[];
  /** Mensaje que Vera escribió sola al terminar una tarea que había quedado en curso (id de la tarea). */
  seguimiento?: boolean;
  tarea?: string;
}

export interface Conversacion {
  id: string;
  titulo: string;
  flujo: string;
  creada: number;
  actualizada: number;
  mensajes: MensajeChat[];
}

const carpeta = () => join(config.datos, "chats");
const ruta = (id: string) => join(carpeta(), `${id}.json`);
export const carpetaAdjuntos = (id: string) => join(carpeta(), "adjuntos", id);
const ID = /^[a-z0-9-]{8,40}$/;

export const idValido = (id: string) => ID.test(id);

export async function listar(): Promise<Omit<Conversacion, "mensajes"> & { n: number; ultimo: string }[]> {
  if (bd.activa()) return (await estadoBd.chats.listar()) as never;
  await mkdir(carpeta(), { recursive: true });
  const salida = [];
  for (const a of (await readdir(carpeta())).filter((x) => x.endsWith(".json"))) {
    try {
      const c = JSON.parse(await readFile(join(carpeta(), a), "utf8")) as Conversacion;
      const ultimo = c.mensajes.at(-1);
      salida.push({ id: c.id, titulo: c.titulo, flujo: c.flujo, creada: c.creada, actualizada: c.actualizada, n: c.mensajes.length, ultimo: (ultimo?.texto ?? "").slice(0, 90) });
    } catch {
      /* archivo dañado */
    }
  }
  return salida.sort((a, b) => b.actualizada - a.actualizada) as never;
}

export async function leer(id: string): Promise<Conversacion | null> {
  if (bd.activa()) return idValido(id) ? (await estadoBd.chats.leer(id)) as Conversacion | null : null;
  if (!idValido(id) || !existsSync(ruta(id))) return null;
  return JSON.parse(await readFile(ruta(id), "utf8")) as Conversacion;
}

export async function guardar(c: Conversacion): Promise<void> {
  c.actualizada = Date.now();
  if (bd.activa()) return estadoBd.chats.guardar(c as estadoBd.ConversacionBd);
  await mkdir(carpeta(), { recursive: true });
  await writeFile(ruta(c.id), JSON.stringify(c), "utf8");
}

export async function crear(flujo: string, titulo = "Nueva conversación"): Promise<Conversacion> {
  const c: Conversacion = { id: randomUUID().slice(0, 18), titulo, flujo, creada: Date.now(), actualizada: Date.now(), mensajes: [] };
  await guardar(c);
  return c;
}

export async function borrar(id: string): Promise<boolean> {
  if (bd.activa()) {
    if (!idValido(id) || !(await estadoBd.chats.borrar(id))) return false;
    await rm(carpetaAdjuntos(id), { recursive: true, force: true });
    return true;
  }
  if (!idValido(id) || !existsSync(ruta(id))) return false;
  await rm(ruta(id), { force: true });
  await rm(carpetaAdjuntos(id), { recursive: true, force: true });
  return true;
}

/** Guarda un adjunto de la conversación. Devuelve su descripción. */
export async function guardarAdjunto(id: string, nombre: string, tipo: string, datos: Buffer): Promise<AdjuntoChat> {
  await mkdir(carpetaAdjuntos(id), { recursive: true });
  const adj = randomUUID().slice(0, 12);
  const extension = extname(nombre).toLowerCase().replace(/[^.a-z0-9]/g, "").slice(0, 8);
  await writeFile(join(carpetaAdjuntos(id), `${adj}${extension}`), datos);
  // Con la base, la ficha (con el nombre cifrado) va a la base y los bytes se quedan en el almacén de objetos
  // (aquí el volumen; en GCP, Cloud Storage bajo org/<organización>/chats/…).
  if (bd.activa()) await estadoBd.chats.guardarAdjunto(id, { id: adj, nombre, tipo, tamano: datos.length, extension, objeto: `chats/adjuntos/${id}/${adj}${extension}`, sha256: createHash("sha256").update(datos).digest() });
  else await writeFile(join(carpetaAdjuntos(id), `${adj}.json`), JSON.stringify({ id: adj, nombre, tipo, tamano: datos.length, extension }), "utf8");
  return { id: adj, nombre, tipo, tamano: datos.length };
}

/** La ruta en disco y la descripción de un adjunto, si existe. */
export async function adjunto(id: string, adj: string): Promise<{ ruta: string; nombre: string; tipo: string; tamano: number } | null> {
  if (!idValido(id) || !/^[a-z0-9-]{6,20}$/.test(adj)) return null;
  if (bd.activa()) {
    const m = await estadoBd.chats.adjunto(id, adj);
    return m ? { ruta: join(carpetaAdjuntos(id), `${adj}${m.extension}`), nombre: m.nombre, tipo: m.tipo, tamano: m.tamano } : null;
  }
  const meta = join(carpetaAdjuntos(id), `${adj}.json`);
  if (!existsSync(meta)) return null;
  const m = JSON.parse(await readFile(meta, "utf8")) as { nombre: string; tipo: string; tamano: number; extension: string };
  return { ruta: join(carpetaAdjuntos(id), `${adj}${m.extension}`), nombre: m.nombre, tipo: m.tipo, tamano: m.tamano };
}
