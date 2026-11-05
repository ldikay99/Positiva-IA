import { readFileSync } from "node:fs";
import { config } from "./config.ts";

/** Cómo se ajusta un parámetro desde el visor: atributo del módulo, variable de entorno, o no se ajusta. */
export type Ajuste = "modulo" | "entorno" | null;

export interface Parametro {
  clave: string;
  modulo: string;
  nombre: string;
  tipo: string;
  valor: unknown;
  descripcion: string;
  env: string | null;
  sobrescribible_en_caliente: boolean;
  nota_sobrescritura?: string;
  ajuste: Ajuste;
  motivo_fijo?: string;
}

export interface Nodo {
  id: string;
  nombre: string;
  descripcion: string;
  etapa_cronometro?: string | null;
  terminal?: boolean;
  modulo?: string;
  funciones?: string[];
  usa_ia?: boolean;
  siguientes?: string[];
  desenlaces?: { accion: string; cuando: string }[];
  reglas?: { id: string; descripcion: string; modulo?: string; funcion?: string }[];
  parametros: Parametro[];
}

export interface Catalogo {
  nodos: Nodo[];
  aristas: { desde: string; hacia: string; etiqueta?: string }[];
  herramientas_agente: { nombre: string; descripcion: string; parametros?: unknown; modulo?: string; escribe?: boolean; efectos?: string }[];
  proveedores: { id: string; nombre: string; tipo: string; modulo?: string; notas?: string; configuracion?: string[]; operaciones?: { nombre: string; metodo?: string; ruta?: string; escribe?: boolean }[] }[];
  desenlaces: { accion: string; descripcion: string }[];
  variables_entorno: { nombre: string; uso: string; secreto: boolean }[];
  catalogos?: { nombre: string; modulo?: string; descripcion?: string; tamano?: number }[];
}

// Lo que haría que el visor escriba en un sistema real, cambie de credenciales
// o de destino no se ajusta desde la web. El trabajador lo vuelve a comprobar.
const PROHIBIDOS = /^(config\.settings\.|correr\.|env\.)/;
const SEGURIDAD = /MODO_TEST|DEDUP_EN_PRUEBAS/;
const ENTORNO_EN_CALIENTE = new Set(["MAX_VUELTAS_AGENTE"]);
// Fijadas como valor por defecto de una función: el trabajador actualiza también ese valor.
const POR_DEFECTO = new Set([
  "pipeline.compuerta.CONFIANZA_PARA_RADICAR",
  "pipeline.extraccion.UMBRAL_CONFIANZA",
  "pipeline.revisor.MAX_VUELTAS_DEL_REVISOR",
  "pipeline.agente.MAX_ITERACIONES",
  // El trabajador cambia solo las descripciones, en la misma lista que usa el orquestador.
  "pipeline.agente.ESQUEMAS_HERRAMIENTAS",
]);

type ParametroCrudo = Omit<Parametro, "ajuste" | "motivo_fijo">;

function clasificar(p: ParametroCrudo): Pick<Parametro, "ajuste" | "motivo_fijo"> {
  if (SEGURIDAD.test(p.clave) || p.clave.startsWith("config.settings."))
    return { ajuste: null, motivo_fijo: "Fijo en el visor: es configuración del servicio (modo de prueba, credenciales, destinos). El visor nunca escribe en un sistema real." };
  if (PROHIBIDOS.test(p.clave)) return { ajuste: null, motivo_fijo: "Lo maneja el visor: la concurrencia y el tope por correo están en Ajustes." };
  if (POR_DEFECTO.has(p.clave)) return { ajuste: "modulo" };
  if (p.sobrescribible_en_caliente) return { ajuste: "modulo" };
  if (p.env && ENTORNO_EN_CALIENTE.has(p.env)) return { ajuste: "entorno" };
  return { ajuste: null, motivo_fijo: p.nota_sobrescritura };
}

let cargado: Catalogo | null = null;

export function catalogo(): Catalogo {
  if (!cargado) {
    const crudo = JSON.parse(readFileSync(config.catalogo, "utf8")) as Partial<Omit<Catalogo, "nodos">> & { nodos?: (Omit<Nodo, "parametros"> & { parametros?: ParametroCrudo[] })[] };
    cargado = {
      nodos: (crudo.nodos ?? []).map((n) => ({ ...n, parametros: (n.parametros ?? []).map((p) => ({ ...p, ...clasificar(p) })) })),
      aristas: crudo.aristas ?? [],
      herramientas_agente: crudo.herramientas_agente ?? [],
      proveedores: crudo.proveedores ?? [],
      desenlaces: crudo.desenlaces ?? [],
      variables_entorno: crudo.variables_entorno ?? [],
      catalogos: crudo.catalogos ?? [],
    };
  }
  return cargado;
}

export function ajustables(): { modulo: Set<string>; entorno: Set<string> } {
  const ps = catalogo().nodos.flatMap((n) => n.parametros);
  return {
    modulo: new Set(ps.filter((p) => p.ajuste === "modulo").map((p) => p.clave)),
    entorno: new Set(ps.filter((p) => p.ajuste === "entorno" && p.env).map((p) => p.env as string)),
  };
}
