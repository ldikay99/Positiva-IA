import type { Configuracion, Parametro } from "./tipos.ts";

export type Clase = "prompt" | "regex" | "umbral" | "numero" | "booleano" | "texto" | "lista" | "mapa" | "esquemas";

export interface Regex {
  regex: string;
  flags: string;
}

export function esRegex(v: unknown): v is Regex {
  return typeof v === "object" && v !== null && "regex" in v && typeof (v as Regex).regex === "string";
}

const PROMPTS = /(CABECERA|SISTEMA|PROMPT|CRITERIOS|_PLANTILLA|PIE|INSTRUCC)/;

/** Qué editor le corresponde a un parámetro según su nombre y su valor. */
export function clase(p: Parametro): Clase {
  const v = p.valor;
  if (p.clave.endsWith("ESQUEMAS_HERRAMIENTAS")) return "esquemas";
  if (esRegex(v) || p.es_regex) return "regex";
  if (typeof v === "boolean" || p.tipo === "bool") return "booleano";
  if (typeof v === "number") {
    const nombre = p.nombre.toUpperCase();
    return v >= 0 && v <= 1 && !Number.isInteger(v) && /(CONFIANZA|UMBRAL|PISO|MINIM|PROPORC|FRACCION|SIMILITUD|RATIO)/.test(nombre) ? "umbral" : "numero";
  }
  if (typeof v === "string") return PROMPTS.test(p.nombre.toUpperCase()) || v.length > 160 ? "prompt" : "texto";
  if (Array.isArray(v)) return "lista";
  if (v && typeof v === "object") return PROMPTS.test(p.nombre.toUpperCase()) ? "prompt" : "mapa";
  return "texto";
}

export const ETIQUETA_CLASE: Record<Clase, string> = {
  prompt: "Prompt",
  regex: "Regla de búsqueda",
  umbral: "Umbral",
  numero: "Número",
  booleano: "Interruptor",
  texto: "Texto",
  lista: "Lista",
  mapa: "Tabla",
  esquemas: "Herramientas del modelo",
};

export const ORDEN_CLASE: Clase[] = ["prompt", "esquemas", "umbral", "regex", "numero", "booleano", "lista", "mapa", "texto"];

/** El valor vigente: el ajustado si lo hay, si no el del código. */
export function vigente(p: Parametro, c: Configuracion | null): unknown {
  if (p.ajuste === "entorno" && p.env) {
    const e = c?.entorno[p.env];
    return e === undefined ? p.valor : Number.isNaN(Number(e)) ? e : Number(e);
  }
  return c && p.clave in c.parametros ? c.parametros[p.clave] : p.valor;
}

export function ajustado(p: Parametro, c: Configuracion | null): boolean {
  if (!c) return false;
  return p.ajuste === "entorno" && p.env ? p.env in c.entorno : p.clave in c.parametros;
}

/** Devuelve la configuración con el parámetro cambiado, o restaurado si `valor` es undefined. */
export function conValor(c: Configuracion, p: Parametro, valor: unknown | undefined): Configuracion {
  const sig: Configuracion = { ...c, parametros: { ...c.parametros }, entorno: { ...c.entorno } };
  if (p.ajuste === "entorno" && p.env) {
    if (valor === undefined) delete sig.entorno[p.env];
    else sig.entorno[p.env] = String(valor);
  } else if (valor === undefined) delete sig.parametros[p.clave];
  else sig.parametros[p.clave] = valor;
  return sig;
}

export function resumen(v: unknown, max = 60): string {
  const s = typeof v === "string" ? v : esRegex(v) ? `/${v.regex}/` : JSON.stringify(v);
  return s.length > max ? `${s.slice(0, max)}…` : s;
}
