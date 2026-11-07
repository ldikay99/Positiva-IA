import type { FastifyInstance } from "fastify";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { config } from "./config.ts";
import type { Evento, Motor } from "./motor.ts";
import * as flujos from "./flujos.ts";
import * as credenciales from "./credenciales.ts";
import * as entornos from "./entornos.ts";

/**
 * El centro de notificaciones de la plataforma: lo que pide la atención de una persona.
 *
 * - fallo: una ejecución terminó con error;
 * - aprobacion: un flujo espera que alguien apruebe o rechace (si existe /api/aprobaciones);
 * - conexion: un flujo usa una conexión que no existe o un secreto sin valor;
 * - recursos: un motor que no arranca o un trabajador con alertas (memoria, tiempo);
 * - programacion: un flujo programado que no pudo correr a su hora.
 *
 * Se guardan en <estado>/notificaciones.json (las últimas 300) y cada una nueva se
 * empuja por el canal de eventos ({tipo: "notificacion"}). Las de conexión y las de
 * aprobación se retiran solas cuando lo que las causó se resuelve.
 *
 *   GET  /api/notificaciones[?todas=1]   → {notificaciones, sin_leer}
 *   POST /api/notificaciones/:id/leida   → {ok, sin_leer}
 *   POST /api/notificaciones/leidas      → {ok, sin_leer}  (todas)
 */

export type TipoNotificacion = "fallo" | "aprobacion" | "conexion" | "recursos" | "programacion" | "alerta";

export interface Notificacion {
  id: string;
  tipo: TipoNotificacion;
  titulo: string;
  texto: string;
  t: number;
  leida: boolean;
  flujo?: string;
  corrida?: string;
  aprobacion?: string;
  /** Adónde lleva: la vista de la web y lo que abre en ella. */
  accion?: { vista: "historial" | "aprobaciones" | "conexiones" | "flujo" | "tablero"; flujo?: string; corrida?: string; etiqueta: string };
  /** Para no repetir la misma notificación. */
  clave: string;
}

export interface Dependencias {
  motores: () => Iterable<Motor>;
  escuchar: (f: (e: Evento) => void) => () => void;
  emitir: (e: Evento) => void;
}

const ruta = () => join(config.datos, "notificaciones.json");
const MAXIMO = 300;
const HORA = 3_600_000;
let lista: Notificacion[] = [];
let escribiendo: Promise<void> = Promise.resolve();

async function cargar(): Promise<void> {
  try {
    const x = JSON.parse(await readFile(ruta(), "utf8")) as { notificaciones?: Notificacion[] };
    lista = Array.isArray(x.notificaciones) ? x.notificaciones.slice(0, MAXIMO) : [];
  } catch {
    lista = [];
  }
}

function guardar(): Promise<void> {
  const copia = JSON.stringify({ notificaciones: lista.slice(0, MAXIMO) });
  const p = escribiendo.then(async () => {
    await mkdir(config.datos, { recursive: true });
    const tmp = `${ruta()}.${Date.now()}.tmp`;
    await writeFile(tmp, copia, "utf8");
    await rename(tmp, ruta());
  });
  escribiendo = p.catch(() => undefined);
  return p;
}

const sinLeer = () => lista.filter((n) => !n.leida).length;
const nombreDe = (id: string | undefined) => (id ? flujos.obtener(id)?.nombre ?? id : "");

let emitir: (e: Evento) => void = () => undefined;

function agregar(n: Omit<Notificacion, "id" | "t" | "leida"> & { t?: number }): Notificacion | null {
  if (lista.some((x) => x.clave === n.clave)) return null;
  const nueva: Notificacion = { ...n, id: randomUUID().slice(0, 12), t: n.t ?? Date.now(), leida: false };
  lista.unshift(nueva);
  if (lista.length > MAXIMO) lista.length = MAXIMO;
  void guardar();
  emitir({ tipo: "notificacion", corrida: null, t: Date.now() / 1000, notificacion: nueva, sin_leer: sinLeer() });
  return nueva;
}

/** Quita las de un prefijo cuya causa ya no existe (conexión arreglada, aprobación resuelta). */
function retirarResueltas(prefijo: string, vigentes: Set<string>): void {
  const antes = lista.length;
  lista = lista.filter((n) => !n.clave.startsWith(prefijo) || vigentes.has(n.clave));
  if (lista.length !== antes) {
    void guardar();
    emitir({ tipo: "notificaciones", corrida: null, t: Date.now() / 1000, sin_leer: sinLeer() });
  }
}

const CONEXION = /\b(401|403)\b|unauthori[sz]ed|forbidden|credencial|token (inv[aá]lido|vencido|expirado)|autenticaci[oó]n|invalid_grant|expired/i;

// ------------------------------------------------------------- fuentes

function alEvento(e: Evento): void {
  if (e.tipo === "flujo_borrado" && typeof e.flujo === "string") {
    // lo de un flujo que ya no existe no se puede atender
    const antes = lista.length;
    lista = lista.filter((n) => n.flujo !== e.flujo);
    if (lista.length !== antes) {
      void guardar();
      emitir({ tipo: "notificaciones", corrida: null, t: Date.now() / 1000, sin_leer: sinLeer() });
    }
    return;
  }
  if (e.tipo === "resultado" && (e.accion === "error" || e.accion === "colgado") && typeof e.corrida === "string") {
    const flujo = typeof e.flujo === "string" ? e.flujo : undefined;
    const motivo = String(e.motivo ?? "").trim();
    const deConexion = CONEXION.test(motivo);
    agregar({
      tipo: deConexion ? "conexion" : "fallo",
      titulo: deConexion ? `Una conexión rechazó a «${nombreDe(flujo)}»` : `Falló una ejecución de «${nombreDe(flujo)}»`,
      texto: motivo ? motivo.slice(0, 280) : "Terminó con un error sin motivo.",
      flujo, corrida: e.corrida,
      accion: { vista: "historial", flujo, corrida: e.corrida, etiqueta: "Ver la ejecución" },
      clave: `fallo:${e.corrida}`,
    });
  } else if (e.tipo === "aprobacion_pendiente" && e.aprobacion && typeof e.aprobacion === "object") {
    // el paso «Aprobación humana» acaba de pausar una ejecución: se avisa enseguida (sin esperar la revisión periódica)
    const a = e.aprobacion as Record<string, unknown>;
    const id = String(a.id ?? e.id ?? "");
    const flujo = typeof a.flujo === "string" ? a.flujo : typeof e.flujo === "string" ? e.flujo : undefined;
    if (id) {
      aprobacionesDisponibles = true;
      agregar({
        tipo: "aprobacion", titulo: `«${nombreDe(flujo)}» espera tu aprobación`,
        texto: String(a.titulo || a.detalle || "Un paso necesita que alguien apruebe o rechace para seguir.").slice(0, 280),
        flujo, corrida: typeof a.corrida === "string" ? a.corrida : undefined, aprobacion: id,
        accion: { vista: "aprobaciones", flujo, etiqueta: "Revisar" }, clave: `aprobacion:${id}`,
      });
    }
  } else if (e.tipo === "aprobacion_resuelta") {
    // alguien decidió (o venció): ya no hay nada que atender
    const id = String((e.aprobacion as { id?: unknown } | undefined)?.id ?? e.id ?? "");
    if (id) retirarResueltas(`aprobacion:${id}`, new Set());
  } else if (e.tipo === "alerta" && typeof e.corrida === "string") {
    // el paso «Detenerse con alerta»
    const flujo = typeof e.flujo === "string" ? e.flujo : undefined;
    agregar({
      tipo: e.nivel === "error" ? "fallo" : "alerta",
      titulo: String(e.titulo || `Alerta de «${nombreDe(flujo)}»`).slice(0, 160),
      texto: String(e.mensaje || "El flujo se detuvo con una alerta.").slice(0, 280),
      flujo, corrida: e.corrida,
      accion: { vista: "historial", flujo, corrida: e.corrida, etiqueta: "Ver la ejecución" },
      clave: `alerta:${e.corrida}`,
    });
  } else if (e.tipo === "aviso_programador" && typeof e.flujo === "string") {
    const texto = String(e.mensaje ?? "").trim() + (e.error ? `: ${String(e.error)}` : "");
    agregar({
      tipo: "programacion",
      titulo: `«${nombreDe(e.flujo)}» no pudo correr a su hora`,
      texto: texto.slice(0, 280),
      flujo: e.flujo,
      accion: { vista: "flujo", flujo: e.flujo, etiqueta: "Abrir el flujo" },
      clave: `programacion:${e.flujo}:${texto}:${Math.floor(Date.now() / HORA)}`,
    });
  }
}

function revisarRecursos(deps: Dependencias): void {
  const hora = Math.floor(Date.now() / HORA);
  for (const m of deps.motores()) {
    const e = m.estado();
    if (e.errorInicio) {
      agregar({
        tipo: "recursos", titulo: `El motor de «${nombreDe(m.flujo)}» no arrancó`, texto: e.errorInicio.slice(0, 280), flujo: m.flujo,
        accion: { vista: "tablero", flujo: m.flujo, etiqueta: "Ver el tablero" }, clave: `recursos:${m.flujo}:inicio:${hora}`,
      });
    }
    for (const t of e.trabajadores) {
      for (const a of t.alertas ?? []) {
        agregar({
          tipo: "recursos", titulo: `Aviso de recursos en «${nombreDe(m.flujo)}»`, texto: `Trabajador ${t.id}: ${a}`.slice(0, 280), flujo: m.flujo,
          accion: { vista: "tablero", flujo: m.flujo, etiqueta: "Ver el tablero" }, clave: `recursos:${m.flujo}:${a}:${hora}`,
        });
      }
    }
  }
}

async function revisarConexiones(): Promise<void> {
  const vigentes = new Set<string>();
  for (const f of flujos.listar()) {
    if (f.tipo === "declarativo" && f.definicion) {
      for (const c of flujos.credencialesUsadas(f.definicion)) {
        if (credenciales.existe(c)) continue;
        const clave = `conexion:${f.id}:falta:${c}`;
        vigentes.add(clave);
        agregar({
          tipo: "conexion", titulo: `«${f.nombre}» usa una conexión que no existe`, texto: `Crea la conexión «${c}» en Conexiones o cambia el paso que la usa.`, flujo: f.id,
          accion: { vista: "conexiones", flujo: f.id, etiqueta: "Abrir Conexiones" }, clave,
        });
      }
    }
    const variables = await entornos.listar(f.id).catch(() => []);
    for (const v of variables) {
      if (!v.secreto || v.definido) continue;
      const clave = `conexion:${f.id}:secreto:${v.nombre}`;
      vigentes.add(clave);
      agregar({
        tipo: "conexion", titulo: `A «${f.nombre}» le falta un secreto`, texto: `${v.nombre} está declarado pero no tiene valor: ponlo en el Entorno del flujo.`, flujo: f.id,
        accion: { vista: "flujo", flujo: f.id, etiqueta: "Abrir el flujo" }, clave,
      });
    }
  }
  retirarResueltas("conexion:", vigentes);
}

/** Las aprobaciones pendientes, si la plataforma ya tiene el paso de aprobación humana. */
/** Si ya existe el paso de aprobación humana (/api/aprobaciones responde): la web no pregunta a una ruta que no existe. */
let aprobacionesDisponibles: boolean | null = null;

async function revisarAprobaciones(app: FastifyInstance): Promise<void> {
  let r;
  try {
    r = await app.inject({ method: "GET", url: "/api/aprobaciones" });
  } catch {
    return;
  }
  aprobacionesDisponibles = r.statusCode !== 404;
  if (r.statusCode !== 200) return;
  let cuerpo: unknown;
  try {
    cuerpo = r.json();
  } catch {
    return;
  }
  const items = (Array.isArray(cuerpo) ? cuerpo : ((cuerpo as { aprobaciones?: unknown[] })?.aprobaciones ?? [])) as Record<string, unknown>[];
  const vigentes = new Set<string>();
  for (const a of items) {
    const id = String(a.id ?? "");
    const estado = String(a.estado ?? "pendiente");
    if (!id || estado !== "pendiente") continue;
    const clave = `aprobacion:${id}`;
    vigentes.add(clave);
    const flujo = typeof a.flujo === "string" ? a.flujo : undefined;
    agregar({
      tipo: "aprobacion",
      titulo: `«${nombreDe(flujo)}» espera tu aprobación`,
      texto: String(a.titulo ?? a.pregunta ?? a.nombre ?? "Un paso necesita que alguien apruebe o rechace para seguir.").slice(0, 280),
      flujo, corrida: typeof a.corrida === "string" ? a.corrida : undefined, aprobacion: id,
      accion: { vista: "aprobaciones", flujo, etiqueta: "Revisar" }, clave,
    });
  }
  retirarResueltas("aprobacion:", vigentes);
}

// ------------------------------------------------------------------ rutas

export async function registrar(app: FastifyInstance, deps: Dependencias): Promise<void> {
  await cargar();
  emitir = deps.emitir;
  deps.escuchar(alEvento);

  const vigilar = async () => {
    try {
      revisarRecursos(deps);
      await revisarAprobaciones(app);
    } catch (e) {
      app.log.warn({ error: e instanceof Error ? e.message : String(e) }, "no se pudieron revisar las notificaciones");
    }
  };
  const vigilarConexiones = async () => {
    try {
      await revisarConexiones();
    } catch (e) {
      app.log.warn({ error: e instanceof Error ? e.message : String(e) }, "no se pudieron revisar las conexiones");
    }
  };
  setInterval(() => void vigilar(), 20_000).unref();
  setInterval(() => void vigilarConexiones(), 60_000).unref();
  app.addHook("onReady", async () => {
    setTimeout(() => void vigilarConexiones(), 3000).unref();
    setTimeout(() => void vigilar(), 5000).unref();
  });

  app.get<{ Querystring: { todas?: string } }>("/api/notificaciones", async (req) => {
    const todas = req.query.todas === "1";
    if (aprobacionesDisponibles === null) await revisarAprobaciones(app).catch(() => undefined);
    const pendientes = lista.filter((n) => n.tipo === "aprobacion" && n.clave.startsWith("aprobacion:")).length;
    return { notificaciones: (todas ? lista : lista.slice(0, 100)), sin_leer: sinLeer(), aprobaciones_disponibles: aprobacionesDisponibles === true, aprobaciones_pendientes: pendientes };
  });

  app.post("/api/notificaciones/leidas", async () => {
    let n = 0;
    for (const x of lista) if (!x.leida) { x.leida = true; n++; }
    if (n) void guardar();
    emitir({ tipo: "notificaciones", corrida: null, t: Date.now() / 1000, sin_leer: 0 });
    return { ok: true, sin_leer: 0, marcadas: n };
  });

  app.post<{ Params: { id: string } }>("/api/notificaciones/:id/leida", async (req, res) => {
    const n = lista.find((x) => x.id === req.params.id);
    if (!n) return res.code(404).send({ error: "esa notificación no existe" });
    if (!n.leida) {
      n.leida = true;
      void guardar();
    }
    emitir({ tipo: "notificaciones", corrida: null, t: Date.now() / 1000, sin_leer: sinLeer() });
    return { ok: true, sin_leer: sinLeer() };
  });
}
