import { pedir, usar } from "../estado.ts";

/**
 * Las versiones guardadas de un flujo (declarativo o de código) en el servidor: la lista con autor, fecha,
 * etiqueta, punto «antes de», resumen y corridas; comparar dos; restaurar, deshacer y rehacer lo guardado.
 * Volver atrás siempre crea una versión nueva: el historial no se borra (servidor/src/rutas_versiones.ts).
 */

export interface ItemVersion {
  version: number;
  fecha: number;
  autor: "persona" | "vera" | null;
  nota?: string;
  resumen?: string;
  etiqueta?: string;
  punto?: string;
  pedido?: string;
  restaurada_de?: number;
  por?: "restaurar" | "deshacer" | "rehacer";
  deshecha?: number;
  del_codigo?: boolean;
  actual: boolean;
  corridas: { bien: number; revisar: number; fallo: number; ultima_bien: number | null };
}

export interface CambioCampo {
  campo: string;
  antes: string;
  despues: string;
}

export interface Diferencias {
  flujo: string;
  a: number;
  b: number;
  actual: number;
  iguales: boolean;
  resumen: string;
  datos: CambioCampo[];
  pasos: {
    agregados: { id: string; tipo: string; nombre: string }[];
    quitados: { id: string; tipo: string; nombre: string }[];
    cambiados: { id: string; tipo: string; nombre: string; cambios: CambioCampo[] }[];
    iguales: number;
  };
  conexiones: { agregadas: string[]; quitadas: string[] };
  movidos: number;
}

export interface Cambio {
  confirmado: boolean;
  version: number;
  restaurada: number;
  sin_cambio?: boolean;
  deshecha?: number;
}

const ruta = (flujo: string, resto = "") => `/api/flujos/${encodeURIComponent(flujo)}${resto}`;

export const listarVersiones = (flujo: string) => pedir<ItemVersion[]>(ruta(flujo, "/versiones"));
export const compararVersiones = (flujo: string, a: number, b: number) => pedir<Diferencias>(ruta(flujo, `/versiones/comparar?a=${a}&b=${b}`));
export const restaurarVersion = (flujo: string, version: number) => pedir<Cambio>(ruta(flujo, "/restaurar"), { method: "POST", body: JSON.stringify({ version }) });
export const etiquetarVersion = (flujo: string, version: number, etiqueta: string | null) =>
  pedir<{ etiqueta: string | null }>(ruta(flujo, `/versiones/${version}`), { method: "PATCH", body: JSON.stringify({ etiqueta }) });

/** Deshace lo último guardado (queda como versión nueva) y lo avisa en palabras. */
export async function deshacerGuardado(flujo: string): Promise<Cambio | null> {
  const avisar = usar.getState().avisar;
  try {
    const r = await pedir<Cambio>(ruta(flujo, "/deshacer"), { method: "POST", body: "{}" });
    avisar(`Deshecho: el flujo volvió a la versión ${r.restaurada} (queda como versión ${r.version})`);
    return r;
  } catch (e) {
    avisar((e as Error).message === "no hay un cambio anterior que deshacer" ? "No hay un cambio anterior que deshacer" : (e as Error).message, "error");
    return null;
  }
}

/** Rehace lo último que se deshizo. */
export async function rehacerGuardado(flujo: string): Promise<Cambio | null> {
  const avisar = usar.getState().avisar;
  try {
    const r = await pedir<Cambio>(ruta(flujo, "/rehacer"), { method: "POST", body: "{}" });
    avisar(`Rehecho: el flujo volvió a la versión ${r.restaurada} (queda como versión ${r.version})`);
    return r;
  } catch (e) {
    avisar((e as Error).message === "no hay nada que rehacer" ? "No hay nada que rehacer" : (e as Error).message, "error");
    return null;
  }
}

/** Ctrl+Z → deshacer; Ctrl+Y o Ctrl+Shift+Z → rehacer. Fuera de los campos de texto (ahí deshace el navegador). */
export function atajoDeshacer(e: KeyboardEvent): "deshacer" | "rehacer" | null {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return null;
  const t = e.target as HTMLElement | null;
  if (t?.closest?.("input, textarea, select, [contenteditable], .cm-editor")) return null;
  const k = e.key.toLowerCase();
  if (k === "z" && !e.shiftKey) return "deshacer";
  if ((k === "z" && e.shiftKey) || k === "y") return "rehacer";
  return null;
}

/** «Vera», «Persona» o nada (versiones de antes del historial). */
export const nombreAutor = (a: ItemVersion["autor"]) => (a === "vera" ? "Vera" : a === "persona" ? "Persona" : "");

export function fechaCorta(t: number): string {
  if (!t) return "";
  return new Date(t).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit", timeZone: "America/Bogota" });
}
