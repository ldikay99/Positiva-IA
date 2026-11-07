import { create } from "zustand";

// Sin importar estado.ts (que importa este módulo): el almacenamiento, aquí mismo.
function leerLocal<T>(clave: string, defecto: T): T {
  try {
    const v = localStorage.getItem(clave);
    return v ? (JSON.parse(v) as T) : defecto;
  } catch {
    return defecto;
  }
}
function guardarLocal(clave: string, v: unknown): void {
  try {
    localStorage.setItem(clave, JSON.stringify(v));
  } catch {
    /* sin almacenamiento: vive solo en esta pestaña */
  }
}

/**
 * Los primeros pasos de quien llega: conocer la plataforma, crear un flujo, correrlo
 * y ver el resultado. El avance se guarda en este navegador; la guía se puede cerrar
 * y volver a abrir desde «Ayuda» (o pedírselo a Vera: {accion: "guia", abierta}).
 */

export type PasoGuia = "conocer" | "crear" | "correr" | "revisar";
export const PASOS_GUIA: PasoGuia[] = ["conocer", "crear", "correr", "revisar"];

const CLAVE = "positiva-ia:guia:v1";

interface Guardado {
  hechos: PasoGuia[];
  /** La persona la cerró: no se vuelve a abrir sola. */
  cerrada: boolean;
}

interface EstadoGuia extends Guardado {
  /** El panel flotante está a la vista. */
  abierta: boolean;
  marcar: (p: PasoGuia) => void;
  abrir: (abierta: boolean) => void;
  cerrar: () => void;
  reiniciar: () => void;
}

const inicial = leerLocal<Guardado>(CLAVE, { hechos: [], cerrada: false });
const guardar = (g: Guardado) => guardarLocal(CLAVE, { hechos: g.hechos, cerrada: g.cerrada });

export const usarGuia = create<EstadoGuia>((set, get) => ({
  hechos: Array.isArray(inicial.hechos) ? inicial.hechos.filter((x) => PASOS_GUIA.includes(x)) : [],
  cerrada: Boolean(inicial.cerrada),
  abierta: false,
  marcar: (p) => {
    if (get().hechos.includes(p)) return;
    const hechos = [...get().hechos, p];
    set({ hechos });
    guardar({ hechos, cerrada: get().cerrada });
  },
  abrir: (abierta) => {
    set({ abierta, ...(abierta ? { cerrada: false } : {}) });
    if (abierta) guardar({ hechos: get().hechos, cerrada: false });
  },
  cerrar: () => {
    set({ abierta: false, cerrada: true });
    guardar({ hechos: get().hechos, cerrada: true });
  },
  reiniciar: () => {
    set({ hechos: [], cerrada: false, abierta: true });
    guardar({ hechos: [], cerrada: false });
  },
}));

/** Para marcar un paso desde cualquier parte (estado, editor, diálogos). */
export const marcarPaso = (p: PasoGuia) => usarGuia.getState().marcar(p);
