import type { NombreIcono } from "./iconos.tsx";
import { NOMBRES_DESENLACE, motivoLegible } from "./progreso.ts";

/**
 * El lenguaje de producto: cómo se dice cada cosa a quien llega por primera vez.
 * En la interfaz, una «corrida» es una ejecución, un «nodo» es un paso y un
 * «desenlace» es el resultado. Lo técnico queda para quien lo busque.
 */

export type EstadoLegible = "en_curso" | "bien" | "revisar" | "fallo";

export const ESTADOS: Record<EstadoLegible, { nombre: string; plural: string; tono: "azul" | "verde" | "ambar" | "rojo"; icono: NombreIcono }> = {
  en_curso: { nombre: "En curso", plural: "En curso", tono: "azul", icono: "reloj" },
  bien: { nombre: "Terminó bien", plural: "Terminaron bien", tono: "verde", icono: "ok" },
  revisar: { nombre: "Para revisar", plural: "Para revisar", tono: "ambar", icono: "persona" },
  fallo: { nombre: "Falló", plural: "Fallaron", tono: "rojo", icono: "alerta" },
};

/** Los resultados de los flujos creados aquí (el motor declarativo) en palabras. */
const RESULTADOS_DECLARATIVOS: Record<string, string> = {
  radicado: "Completado",
  radicar: "Completado",
  manual: "Para revisión de una persona",
  reenviado: "Para otra área",
  reenviar: "Para otra área",
  descartado: "Descartado",
  descartar: "Descartado",
  respondido: "Respuesta lista",
  responder: "Respuesta lista",
  duplicado: "Duplicado",
  error: "Falló",
  cancelada: "Cancelada",
  colgado: "Se quedó sin responder",
  en_espera: "Esperando aprobación",
  detenido: "Detenido con alerta",
  aplazado: "Aplazado para más tarde",
};

/** El nombre de un resultado: los de PQRD y tutelas hablan de SGDEA; los demás, de completar. */
export function nombreResultado(accion: string | null | undefined, deCodigo: boolean): string {
  if (!accion) return "Sin resultado todavía";
  if (deCodigo) return NOMBRES_DESENLACE[accion] ?? RESULTADOS_DECLARATIVOS[accion] ?? motivoLegible(accion);
  return RESULTADOS_DECLARATIVOS[accion] ?? NOMBRES_DESENLACE[accion] ?? motivoLegible(accion);
}

/** Lo que disparó una ejecución. */
export const ORIGENES: Record<string, string> = {
  manual: "A mano",
  bandeja: "Desde la correspondencia",
  lote: "Como el job",
  programado: "A su hora",
  webhook: "Por webhook",
  correo_nuevo: "Llegó un correo",
  reintento: "Reintento",
  aprobacion: "Siguió tras una aprobación",
  vera: "Pedido a Vera",
};

export const origenLegible = (o: string | null | undefined) => (o ? ORIGENES[o] ?? motivoLegible(o) : "");

/** Cómo arranca un flujo, en palabras. */
export const ARRANQUES: Record<string, { nombre: string; icono: NombreIcono }> = {
  entrada_correo: { nombre: "Con un correo", icono: "correo" },
  correo_nuevo: { nombre: "Cuando llega un correo", icono: "bandeja" },
  entrada_manual: { nombre: "A mano, con un formulario", icono: "editar" },
  entrada_archivos: { nombre: "Con archivos que subes", icono: "adjunto" },
  programado: { nombre: "A una hora fija", icono: "calendario" },
  webhook: { nombre: "Cuando otro sistema lo llama", icono: "gancho" },
  buzon_real: { nombre: "Con el buzón de producción", icono: "bandeja" },
};

export const arranqueDe = (d: string | null | undefined) => ARRANQUES[d ?? ""] ?? { nombre: "A mano", icono: "play" as NombreIcono };

/** El icono de un tipo de paso (para vistas previas y detalles). */
export function iconoDePaso(tipo: string | null | undefined): NombreIcono {
  const t = tipo ?? "";
  if (ARRANQUES[t]) return ARRANQUES[t].icono;
  if (t.startsWith("ia_") || t === "resumir") return "chispa";
  if (t === "validar" || t === "filtro" || t === "condicion" || t === "bifurcar_por_valor") return "filtro";
  if (t.startsWith("tabla_") || t.startsWith("sql_")) return "base";
  if (t === "salida") return "bandera";
  if (t === "transformar" || t === "expresion" || t === "json" || t === "fecha") return "editar";
  if (t === "enviar" || t === "notificar" || t === "correo_enviar" || t === "respuesta_webhook") return "enviar";
  if (t === "hoja_leer" || t.startsWith("archivo_") || t === "leer_adjuntos" || t.startsWith("drive_")) return "documento";
  if (t === "http") return "enlace";
  if (t === "para_cada" || t === "paralelo" || t === "unir" || t === "fin_ciclo") return "rehacer";
  if (t.startsWith("aprobacion")) return "persona";
  if (t === "esperar") return "reloj";
  return "punto";
}

/** «ok» → «siguió»; las ramas con nombre propio se citan. */
export function ramaLegible(salida: string): string {
  if (!salida) return "";
  const s = salida.toLowerCase();
  if (s === "ok") return "Siguió adelante";
  if (s === "falla") return "No pasó la revisión";
  if (s === "error") return "Falló y tomó el camino de error";
  if (s === "si" || s === "sí") return "La respuesta fue «sí»";
  if (s === "no") return "La respuesta fue «no»";
  if (s === "listo") return "Terminó de recorrer la lista";
  if (s === "cada") return "Recorrió cada elemento";
  if (s === "vacio") return "No encontró nada";
  if (s === "encontrado") return "Encontró lo que buscaba";
  if (s === "no_encontrado") return "No lo encontró";
  if (s === "sin_adjuntos") return "No había adjuntos";
  if (s === "aprobado" || s === "aprobar") return "Lo aprobaron";
  if (s === "rechazado" || s === "rechazar") return "Lo rechazaron";
  return `Siguió por «${salida.replace(/_/g, " ")}»`;
}

export function hace(t: number): string {
  const s = (Date.now() - t) / 1000;
  if (s < 45) return "hace un momento";
  if (s < 3600) return `hace ${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  if (s < 86400 * 6) return `hace ${Math.round(s / 86400)} día${Math.round(s / 86400) === 1 ? "" : "s"}`;
  return new Date(t).toLocaleDateString("es-CO", { day: "numeric", month: "short", year: new Date(t).getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
}

/** Lo que falta para un momento futuro: «en 23 h», «en 5 min»; si ya pasó, «venció». */
export function dentroDe(t: number): string {
  const s = (t - Date.now()) / 1000;
  if (s <= 0) return "ya venció";
  if (s < 3600) return `en ${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400 * 2) return `en ${Math.round(s / 3600)} h`;
  return `en ${Math.round(s / 86400)} días`;
}

export const fechaHora =(t: number) => new Date(t).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

export function duracion(s: number | null | undefined): string {
  if (s === null || s === undefined || Number.isNaN(s)) return "";
  if (s < 1) return "menos de 1 s";
  if (s < 60) return `${String(Math.round(s * 10) / 10).replace(".", ",")} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${Math.round(s % 60)} s`;
}

/** Las palabras de la plataforma, explicadas (guía y ayudas). */
export const GLOSARIO: { palabra: string; explica: string }[] = [
  { palabra: "Flujo", explica: "Una receta de pasos que se repite sola: algo la arranca, los pasos hacen el trabajo y al final queda un resultado." },
  { palabra: "Paso", explica: "Cada cosa que hace el flujo: leer un documento, preguntarle a la IA, revisar una regla, guardar un dato. En el lienzo se ven como tarjetas unidas por flechas." },
  { palabra: "Cómo arranca", explica: "El primer paso: un correo, archivos que subes, una hora fija, un formulario o un sistema que lo llama por internet (webhook)." },
  { palabra: "Ejecución", explica: "Cada vez que el flujo corre. En el historial ves qué entró, qué hizo cada paso y qué salió. En algunas pantallas técnicas aparece como «corrida»." },
  { palabra: "Resultado", explica: "Cómo terminó una ejecución: completado, para revisión de una persona, descartado o con un error." },
  { palabra: "Conexión", explica: "La cuenta o llave con que un paso habla con otro sistema (un buzón, una base de datos). Por defecto solo lee; escribir lo habilita una persona." },
  { palabra: "Simulación", explica: "Lo que un flujo escribiría afuera (radicar, enviar, mover) queda registrado aquí sin enviarse, hasta que una persona lo habilite." },
  { palabra: "Vera", explica: "La asistente de la plataforma: crea y ajusta flujos, los corre y te explica resultados. Está en la burbuja de abajo a la derecha." },
];
