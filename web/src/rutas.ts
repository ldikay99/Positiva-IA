/**
 * Rutas: cada pantalla tiene su dirección y la URL siempre dice dónde está la persona.
 *
 *   /inicio  /tablero  /plantillas[/<plantilla>]  /documentos[/<plantilla de documento>]  /notificaciones  /aprobaciones  /conexiones[/<conexión>|nueva]
 *   /flujos/<flujo>                       el lienzo
 *   /flujos/<flujo>/nodo/<nodo>           el lienzo con un paso abierto en el inspector
 *   /flujos/<flujo>/correo/<caso>         el lienzo con un correo abierto
 *   /flujos/<flujo>/ejecucion/<corrida>   el lienzo mostrando una ejecución
 *   /flujos/<flujo>/historial[/<corrida>] /flujos/<flujo>/ejecuciones  /registro  /sistemas  /adjuntos  /herramientas  /ajustes
 *
 * El estado manda: cada cambio de vista, flujo o lo abierto escribe la URL (atrás/adelante funcionan),
 * y al llegar por un enlace, recargar o ir atrás, la URL se aplica al estado.
 */
import { usar, type Vista } from "./estado.ts";

const GLOBALES = new Set<Vista>(["inicio", "tablero", "plantillas", "documentos", "notificaciones", "aprobaciones", "conexiones"]);
/** Nombre de cada vista del flujo en la URL (en el idioma de la pantalla). */
const SLUG: Partial<Record<Vista, string>> = { historial: "historial", corridas: "ejecuciones", logs: "registro", proveedores: "sistemas", adjuntos: "adjuntos", herramientas: "herramientas", configuracion: "ajustes" };
const VISTA_DE_SLUG: Record<string, Vista> = Object.fromEntries(Object.entries(SLUG).map(([v, slug]) => [slug, v as Vista]));
const parte = (x: string) => encodeURIComponent(x);

type Estado = ReturnType<typeof usar.getState>;

/** La URL que corresponde a lo que hay en pantalla. */
export function rutaDe(s: Pick<Estado, "vista" | "flujo" | "sub" | "foco">): string {
  if (GLOBALES.has(s.vista)) return `/${s.vista}${s.sub ? `/${parte(s.sub)}` : ""}`;
  const base = `/flujos/${parte(s.flujo)}`;
  if (s.vista === "flujo") {
    const f = s.foco;
    if (f?.tipo === "nodo") return `${base}/nodo/${parte(f.id)}`;
    if (f?.tipo === "correo") return `${base}/correo/${parte(f.caso)}`;
    if (f?.tipo === "corrida") return `${base}/ejecucion/${parte(f.id)}`;
    return base;
  }
  const slug = SLUG[s.vista] ?? s.vista;
  return `${base}/${slug}${s.sub ? `/${parte(s.sub)}` : ""}`;
}

export interface RutaLeida {
  vista: Vista;
  flujo?: string;
  sub?: string;
  nodo?: string;
  correo?: string;
  corrida?: string;
}

/** Qué pide una URL. Las direcciones desconocidas llevan al inicio. */
export function leerRuta(path: string): RutaLeida {
  const p = path.split("/").filter(Boolean).map((x) => { try { return decodeURIComponent(x); } catch { return x; } });
  if (p.length === 0) return { vista: "inicio" };
  if (p[0] === "flujos" && p[1]) {
    const flujo = p[1];
    const [que, valor] = [p[2], p[3]];
    if (!que) return { vista: "flujo", flujo };
    if (que === "nodo" && valor) return { vista: "flujo", flujo, nodo: valor };
    if (que === "correo" && valor) return { vista: "flujo", flujo, correo: valor };
    if (que === "ejecucion" && valor) return { vista: "flujo", flujo, corrida: valor };
    const vista = VISTA_DE_SLUG[que];
    if (vista) return { vista, flujo, sub: valor };
    return { vista: "flujo", flujo };
  }
  if (GLOBALES.has(p[0] as Vista)) return { vista: p[0] as Vista, sub: p[1] };
  return { vista: "inicio" };
}

/** Antes de cargar: si la URL pide una pantalla general (no de un flujo), se muestra ya, sin pasar por el inicio. */
export function mostrarRutaInicial(): void {
  const r = leerRuta(location.pathname);
  if (!r.flujo && r.vista !== "inicio") usar.setState({ vista: r.vista, sub: r.sub ?? null });
}

let aplicando = false;

/** Lleva el estado a lo que dice la URL. */
export async function aplicarRuta(path: string): Promise<void> {
  const r = leerRuta(path);
  const s = usar.getState();
  aplicando = true;
  try {
    if (r.flujo && r.flujo !== s.flujo) {
      if (s.flujos.length && !s.flujos.some((f) => f.id === r.flujo)) {
        s.avisar(`No existe el flujo «${r.flujo}»`, "error");
        usar.setState({ vista: "inicio", sub: null });
        return;
      }
      await s.cambiarFlujo(r.flujo);
    }
    if (r.vista === "historial" && r.sub) {
      await usar.getState().abrirEnHistorial(r.sub);
      return;
    }
    const foco = r.nodo ? { tipo: "nodo" as const, id: r.nodo } : r.correo ? { tipo: "correo" as const, caso: r.correo } : r.corrida ? { tipo: "corrida" as const, id: r.corrida } : r.vista === "flujo" ? null : usar.getState().foco;
    usar.setState({ vista: r.vista, sub: r.sub ?? null, foco });
    if (r.nodo) usar.getState().centrar(r.nodo);
    if (r.corrida) { usar.getState().setSeguirEnVivo(false); void usar.getState().verCorrida(r.corrida); }
  } finally {
    aplicando = false;
    escribir(true);
  }
}

/** Escribe la URL del estado actual: un paso nuevo en el historial del navegador si cambió la pantalla, o reemplazo si solo cambió el foco. */
function escribir(reemplazar: boolean): void {
  const ruta = rutaDe(usar.getState());
  const actual = location.pathname;
  if (ruta === actual) return;
  if (reemplazar) history.replaceState(null, "", ruta + location.search + location.hash);
  else history.pushState(null, "", ruta);
}

/** Varios cambios seguidos (cambiar de flujo y luego de vista) quedan en un solo paso del historial. */
let pendiente: "push" | "replace" | null = null;
function programar(tipo: "push" | "replace"): void {
  if (pendiente === "push") return;
  const primero = pendiente === null;
  pendiente = tipo;
  if (primero) setTimeout(() => { const t = pendiente; pendiente = null; if (!aplicando) escribir(t === "replace"); }, 0);
}

let iniciado = false;

/** Se llama una vez, tras cargar los flujos: aplica la URL de llegada y desde ahí la mantiene al día. */
export async function iniciarRutas(): Promise<void> {
  if (iniciado) return;
  iniciado = true;
  if (location.pathname !== "/" && location.pathname !== "") await aplicarRuta(location.pathname);
  else escribir(true);
  usar.subscribe((s, antes) => {
    if (aplicando) return;
    const cambioPantalla = s.vista !== antes.vista || s.flujo !== antes.flujo || s.sub !== antes.sub;
    const cambioFoco = s.foco !== antes.foco;
    if (cambioPantalla) programar("push");
    else if (cambioFoco && s.vista === "flujo") programar("replace");
  });
  window.addEventListener("popstate", () => void aplicarRuta(location.pathname));
}

/** Para un componente: lo que tiene abierto dentro de su vista, guardado en la URL. */
export function useSubruta(): [string | null, (v: string | null) => void] {
  return [usar((s) => s.sub), usar((s) => s.fijarSub)];
}
