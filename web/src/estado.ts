import { create } from "zustand";
import { reglaNueva } from "./flujo.ts";
import type { Buzon, Catalogo, Configuracion, Corrida, Correo, EstadoMotor, Evento, FlujoResumen, Notificacion, Regla, TableroFlujo } from "./tipos.ts";
import { marcarPaso } from "./guia.ts";

/** La ruta de un correo de la correspondencia de un flujo (solo de ESE flujo). */
export const urlCorreo = (flujo: string, caso: string, adjunto?: string) =>
  `/api/bandeja/${encodeURIComponent(caso)}${adjunto ? `/adjuntos/${encodeURIComponent(adjunto)}` : ""}?flujo=${encodeURIComponent(flujo)}`;

export interface Nota {
  id: string;
  texto: string;
}

const CLAVE_NOTAS = "positiva-ia:notas:v1";
export const CLAVE_POSICIONES = "positiva-ia:posiciones:v6";

export function leerLocal<T>(clave: string, defecto: T): T {
  try {
    const v = localStorage.getItem(clave);
    return v ? (JSON.parse(v) as T) : defecto;
  } catch {
    return defecto;
  }
}

export function guardarLocal(clave: string, v: unknown): void {
  try {
    localStorage.setItem(clave, JSON.stringify(v));
  } catch {
    /* sin almacenamiento: vive solo en esta pestaña */
  }
}

/** Fija dónde aparece un nodo nuevo antes de que el lienzo lo dibuje. */
export function ubicar(id: string, p: { x: number; y: number }): void {
  guardarLocal(CLAVE_POSICIONES, { ...leerLocal<Record<string, { x: number; y: number }>>(CLAVE_POSICIONES, {}), [id]: p });
}

export async function pedir<T>(ruta: string, init?: RequestInit): Promise<T> {
  const r = await fetch(ruta, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  const cuerpo = (await r.json().catch(() => ({}))) as T & { error?: string };
  if (!r.ok) throw new Error(cuerpo.error ?? `error ${r.status}`);
  return cuerpo;
}

export type Vista = "inicio" | "tablero" | "flujo" | "historial" | "plantillas" | "notificaciones" | "aprobaciones" | "conexiones" | "adjuntos" | "corridas" | "logs" | "herramientas" | "proveedores" | "configuracion";
export type Foco = { tipo: "nodo"; id: string } | { tipo: "regla"; id: string } | { tipo: "correo"; caso: string } | { tipo: "corrida"; id: string } | null;

export interface Aviso {
  id: number;
  texto: string;
  tono: "ok" | "error" | "info";
}

export interface Perfil {
  nombre: string;
  t: number;
  cambios: number;
  descripcion: string;
}

export interface Cambio {
  t: number;
  clave: string;
  antes: unknown;
  despues: unknown;
  nota?: string;
}

interface Estado {
  /** El flujo abierto. Todo lo que se ve (catálogo, corridas, eventos) es de este flujo. */
  flujo: string;
  flujos: FlujoResumen[];
  tablero: TableroFlujo[];
  cambiarFlujo: (id: string) => Promise<void>;
  cargarFlujos: () => Promise<void>;
  cargarTablero: () => Promise<void>;
  /** La correspondencia del flujo abierto (GET /api/bandeja?flujo=). */
  cargarBandeja: () => Promise<void>;
  renombrarFlujo: (id: string, nombre: string) => Promise<void>;
  /** Clona cualquier flujo (también pqrd y tutelas) y devuelve el id de la copia. */
  clonarFlujo: (id: string, nombre: string) => Promise<string>;
  borrarFlujo: (id: string) => Promise<void>;
  catalogo: Catalogo | null;
  bandeja: Correo[];
  configuracion: Configuracion | null;
  modelos: string[];
  corridas: Record<string, Corrida>;
  orden: string[];
  recientes: Evento[];
  motor: EstadoMotor | null;
  conectado: boolean;
  vista: Vista;
  foco: Foco;
  /** La corrida que el lienzo y el panel inferior están mostrando. */
  corridaVisible: string | null;
  seguirEnVivo: boolean;
  seleccion: string[];
  avisos: Aviso[];
  perfiles: Perfil[];
  historial: Cambio[];
  /** Reglas creadas en el lienzo que todavía no se guardan. */
  borradores: Regla[];
  notas: Nota[];
  /** Pide al lienzo centrar un nodo; `n` cambia en cada pedido. */
  objetivo: { id: string; n: number } | null;
  /** Parámetro que el inspector debe abrir y mostrar. */
  parametroAbierto: string | null;
  cargar: () => Promise<void>;
  conectar: () => void;
  irA: (v: Vista) => void;
  enfocar: (f: Foco) => void;
  verCorrida: (id: string | null) => void;
  setSeguirEnVivo: (v: boolean) => void;
  alternarSeleccion: (caso: string) => void;
  fijarSeleccion: (casos: string[]) => void;
  procesar: (casos: string[], comoElJob?: boolean) => Promise<void>;
  /** La carpeta que lee el job, en el orden en que la tomaría ahora (solo lectura). */
  buzon: Buzon | null;
  cargandoBuzon: boolean;
  cargarBuzon: (n: number, fresco?: boolean) => Promise<void>;
  /** Trae del buzón real los próximos `n` correos del job y los corre juntos, como él. */
  correrComoElJob: (n: number, ids?: string[]) => Promise<void>;
  cancelarCola: () => Promise<void>;
  guardarConfiguracion: (c: Configuracion, nota?: string) => Promise<number>;
  cargarPerfiles: () => Promise<void>;
  guardarPerfil: (nombre: string, descripcion: string) => Promise<void>;
  aplicarPerfil: (nombre: string) => Promise<void>;
  borrarPerfil: (nombre: string) => Promise<void>;
  cargarHistorial: () => Promise<void>;
  avisar: (texto: string, tono?: Aviso["tono"]) => void;
  cerrarAviso: (id: number) => void;
  crearRegla: (punto: Regla["punto"], donde?: { x: number; y: number }) => void;
  cambiarBorrador: (r: Regla) => void;
  guardarRegla: (r: Regla) => Promise<void>;
  borrarRegla: (id: string) => Promise<void>;
  duplicarRegla: (id: string) => void;
  crearNota: (donde: { x: number; y: number }) => void;
  cambiarNota: (id: string, texto: string) => void;
  borrarNota: (id: string) => void;
  centrar: (id: string) => void;
  abrirParametro: (nodo: string, clave: string) => void;
  /** El centro de notificaciones (GET /api/notificaciones, empujadas por el canal de eventos). */
  notificaciones: Notificacion[];
  sinLeer: number;
  /** Si el servidor ya tiene el paso de aprobación humana (null: todavía no se sabe). */
  aprobacionesDisponibles: boolean | null;
  cargarNotificaciones: () => Promise<void>;
  marcarLeida: (id: string) => Promise<void>;
  marcarTodasLeidas: () => Promise<void>;
  /** La ejecución que el historial debe abrir al llegar (desde una notificación, el inicio o Vera). */
  historialFoco: { corrida: string; n: number } | null;
  abrirEnHistorial: (corrida: string, flujo?: string) => Promise<void>;
}

let siguienteAviso = 1;

const CLAVE_FLUJO = "positiva-ia:flujo";
const q = (ruta: string, flujo: string) => `${ruta}${ruta.includes("?") ? "&" : "?"}flujo=${encodeURIComponent(flujo)}`;

export const usar = create<Estado>((set, get) => ({
  flujo: leerLocal<string>(CLAVE_FLUJO, "pqrd"),
  flujos: [],
  tablero: [],

  cargarFlujos: async () => set({ flujos: await pedir<FlujoResumen[]>("/api/flujos") }),
  cargarTablero: async () => set({ tablero: await pedir<TableroFlujo[]>("/api/tablero") }),

  cambiarFlujo: async (id) => {
    if (id === get().flujo && get().catalogo) return;
    guardarLocal(CLAVE_FLUJO, id);
    // Nada del flujo anterior queda en pantalla.
    set({ flujo: id, catalogo: null, corridas: {}, orden: [], recientes: [], corridaVisible: null, foco: null, seleccion: [], motor: null, bandeja: [], buzon: null });
    const [catalogo, lista, recientes, motor, bandeja] = await Promise.all([
      pedir<Catalogo>(q("/api/catalogo", id)),
      pedir<(Omit<Corrida, "eventos"> & { nEventos: number })[]>(q("/api/corridas", id)),
      pedir<Evento[]>(q("/api/eventos/recientes", id)),
      pedir<EstadoMotor>(q("/api/salud", id)),
      // la correspondencia es de ESTE flujo (un flujo nuevo nunca ve la de PQRD)
      pedir<Correo[]>(q("/api/bandeja", id)).catch(() => [] as Correo[]),
    ]);
    if (get().flujo !== id) return;
    // Lo que llegó por el canal mientras se cargaba la lista no se pierde: se fusiona.
    set((s) => ({ catalogo, motor, bandeja, ...fusionar(lista, recientes, s) }));
  },

  cargarBandeja: async () => {
    const id = get().flujo;
    const bandeja = await pedir<Correo[]>(q("/api/bandeja", id)).catch(() => [] as Correo[]);
    if (get().flujo === id) set({ bandeja });
  },

  renombrarFlujo: async (id, nombre) => {
    await pedir(`/api/flujos/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ nombre }) });
    await Promise.all([get().cargarFlujos(), get().cargarTablero().catch(() => undefined)]);
  },

  clonarFlujo: async (id, nombre) => {
    const r = await pedir<{ flujo: FlujoResumen }>(`/api/flujos/${encodeURIComponent(id)}/clonar`, { method: "POST", body: JSON.stringify({ nombre }) });
    await get().cargarFlujos();
    void get().cargarTablero().catch(() => undefined);
    return r.flujo.id;
  },

  borrarFlujo: async (id) => {
    await pedir(`/api/flujos/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (get().flujo === id) await get().cambiarFlujo("pqrd");
    await get().cargarFlujos();
    void get().cargarTablero().catch(() => undefined);
  },

  catalogo: null,
  bandeja: [],
  configuracion: null,
  modelos: [],
  corridas: {},
  orden: [],
  recientes: [],
  motor: null,
  conectado: false,
  vista: "inicio",
  foco: null,
  corridaVisible: null,
  seguirEnVivo: true,
  seleccion: [],
  avisos: [],
  perfiles: [],
  historial: [],
  borradores: [],
  notas: leerLocal<Nota[]>(CLAVE_NOTAS, []),
  objetivo: null,
  parametroAbierto: null,

  cargar: async () => {
    const flujos = await pedir<FlujoResumen[]>("/api/flujos");
    const flujo = flujos.some((f) => f.id === get().flujo) ? get().flujo : "pqrd";
    set({ flujos, flujo });
    const [catalogo, bandeja, conf, lista, recientes] = await Promise.all([
      pedir<Catalogo>(q("/api/catalogo", flujo)),
      pedir<Correo[]>(q("/api/bandeja", flujo)).catch(() => [] as Correo[]),
      pedir<Configuracion & { modelos: string[] }>("/api/configuracion"),
      pedir<(Omit<Corrida, "eventos"> & { nEventos: number })[]>(q("/api/corridas", flujo)),
      pedir<Evento[]>(q("/api/eventos/recientes", flujo)),
    ]);
    const { modelos, ...configuracion } = conf;
    const corridas: Record<string, Corrida> = {};
    for (const c of lista) corridas[c.id] = { ...c, eventos: [] };
    for (const e of recientes) if (e.corrida && corridas[e.corrida]) corridas[e.corrida]!.eventos.push(e);
    set({ catalogo, bandeja, configuracion, modelos, corridas, orden: lista.map((c) => c.id), recientes });
    void get().cargarPerfiles();
  },

  conectar: () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/eventos`);
    ws.onopen = () => {
      const reconexion = conexiones++ > 0;
      set({ conectado: true });
      // Tras un corte, lo que pasó mientras tanto se trae del servidor: ninguna corrida queda trabada.
      if (reconexion) void resincronizar();
    };
    ws.onclose = () => {
      set({ conectado: false });
      setTimeout(() => get().conectar(), 1500);
    };
    ws.onmessage = (m) => recibir(JSON.parse(m.data as string) as Evento);
  },

  irA: (vista) => set({ vista }),
  enfocar: (foco) => set({ foco }),

  verCorrida: async (id) => {
    set({ corridaVisible: id });
    if (id) marcarPaso("revisar");
    const c = id ? get().corridas[id] : null;
    if (id && c && c.eventos.length === 0 && c.estado !== "en_cola") {
      try {
        const completa = await pedir<Corrida>(`/api/corridas/${id}`);
        set((s) => ({ corridas: { ...s.corridas, [id]: completa } }));
      } catch {
        /* la corrida ya no existe en el servidor */
      }
    }
  },

  setSeguirEnVivo: (seguirEnVivo) => set({ seguirEnVivo }),

  alternarSeleccion: (caso) => set((s) => ({ seleccion: s.seleccion.includes(caso) ? s.seleccion.filter((c) => c !== caso) : [...s.seleccion, caso] })),
  fijarSeleccion: (seleccion) => set({ seleccion }),

  buzon: null,
  cargandoBuzon: false,

  cargarBuzon: async (n, fresco = false) => {
    set({ cargandoBuzon: true });
    try {
      const buzon = await pedir<Buzon>(`/api/buzon?n=${n}${fresco ? "&fresco=1" : ""}&flujo=${encodeURIComponent(get().flujo)}`);
      set({ buzon });
    } catch (e) {
      set({ buzon: { carpeta: "", total: null, correos: [], error: (e as Error).message } });
    } finally {
      set({ cargandoBuzon: false });
    }
  },

  correrComoElJob: async (n, ids) => {
    marcarPaso("correr");
    const r = await pedir<{ lote: string | null; corridas: { id: string; caso: string }[] }>(q("/api/buzon/correr", get().flujo), { method: "POST", body: JSON.stringify(ids?.length ? { ids } : { n }) });
    const bandeja = await pedir<Correo[]>(q("/api/bandeja", get().flujo));
    set((s) => {
      const corridas = { ...s.corridas };
      for (const c of r.corridas) corridas[c.id] ??= { id: c.id, caso: c.caso, estado: "en_cola", creada: Date.now(), eventos: [], ...(r.lote ? { lote: r.lote } : {}) };
      const nuevas = r.corridas.map((c) => c.id).filter((id) => !s.orden.includes(id));
      return { bandeja, corridas, orden: [...nuevas.reverse(), ...s.orden], corridaVisible: r.corridas[0]?.id ?? s.corridaVisible };
    });
    void get().cargarBuzon(n);
  },

  procesar: async (casos, comoElJob = false) => {
    marcarPaso("correr");
    const creadas = await pedir<{ id: string; caso: string; lote?: string }[]>("/api/corridas", { method: "POST", body: JSON.stringify({ casos, comoElJob, flujo: get().flujo }) });
    set((s) => {
      const corridas = { ...s.corridas };
      for (const c of creadas) corridas[c.id] ??= { id: c.id, caso: c.caso, estado: "en_cola", creada: Date.now(), eventos: [], ...(c.lote ? { lote: c.lote } : {}) };
      const nuevas = creadas.map((c) => c.id).filter((id) => !s.orden.includes(id));
      return { corridas, orden: [...nuevas.reverse(), ...s.orden], corridaVisible: creadas[0]?.id ?? s.corridaVisible, seleccion: [] };
    });
  },

  cancelarCola: async () => {
    const r = await pedir<{ canceladas: number }>(q("/api/corridas/cola", get().flujo), { method: "DELETE" });
    get().avisar(`${r.canceladas} corridas canceladas`, "info");
  },

  guardarConfiguracion: async (c, nota) => {
    const r = await pedir<{ configuracion: Configuracion; cambios: unknown[] }>("/api/configuracion", { method: "PUT", body: JSON.stringify({ configuracion: c, nota }) });
    set({ configuracion: r.configuracion });
    void get().cargarHistorial();
    return r.cambios.length;
  },

  cargarPerfiles: async () => set({ perfiles: await pedir<Perfil[]>("/api/perfiles") }),
  guardarPerfil: async (nombre, descripcion) => set({ perfiles: await pedir<Perfil[]>("/api/perfiles", { method: "POST", body: JSON.stringify({ nombre, descripcion }) }) }),
  aplicarPerfil: async (nombre) => {
    const r = await pedir<{ configuracion: Configuracion }>(`/api/perfiles/${encodeURIComponent(nombre)}/aplicar`, { method: "POST", body: "{}" });
    set({ configuracion: r.configuracion });
    void get().cargarHistorial();
  },
  borrarPerfil: async (nombre) => set({ perfiles: await pedir<Perfil[]>(`/api/perfiles/${encodeURIComponent(nombre)}`, { method: "DELETE" }) }),
  cargarHistorial: async () => set({ historial: await pedir<Cambio[]>("/api/configuracion/historial") }),

  avisar: (texto, tono = "ok") => {
    const id = siguienteAviso++;
    set((s) => ({ avisos: [...s.avisos.slice(-3), { id, texto, tono }] }));
    setTimeout(() => get().cerrarAviso(id), tono === "error" ? 8000 : 4000);
  },
  cerrarAviso: (id) => set((s) => ({ avisos: s.avisos.filter((a) => a.id !== id) })),

  crearRegla: (punto, donde) => {
    const r = reglaNueva(punto);
    if (donde) ubicar(r.id, donde);
    set((s) => ({ borradores: [...s.borradores, r], foco: { tipo: "regla", id: r.id }, vista: "flujo" }));
  },
  cambiarBorrador: (r) => set((s) => ({ borradores: s.borradores.some((b) => b.id === r.id) ? s.borradores.map((b) => (b.id === r.id ? r : b)) : [...s.borradores, r] })),
  guardarRegla: async (r) => {
    const c = get().configuracion;
    if (!c) return;
    const existe = c.reglas.some((x) => x.id === r.id);
    const sig: Configuracion = { ...c, reglas: existe ? c.reglas.map((x) => (x.id === r.id ? r : x)) : [...c.reglas, r] };
    await get().guardarConfiguracion(sig, `regla «${r.nombre}» ${existe ? "editada" : "creada"}`);
    set((s) => ({ borradores: s.borradores.filter((b) => b.id !== r.id) }));
  },
  borrarRegla: async (id) => {
    const c = get().configuracion;
    set((s) => ({ borradores: s.borradores.filter((b) => b.id !== id), foco: s.foco?.tipo === "regla" && s.foco.id === id ? null : s.foco }));
    if (c?.reglas.some((x) => x.id === id)) {
      const r = c.reglas.find((x) => x.id === id)!;
      await get().guardarConfiguracion({ ...c, reglas: c.reglas.filter((x) => x.id !== id) }, `regla «${r.nombre}» eliminada`);
    }
  },
  duplicarRegla: (id) => {
    const s = get();
    const r = [...(s.configuracion?.reglas ?? []), ...s.borradores].find((x) => x.id === id);
    if (!r) return;
    const copia: Regla = { ...r, id: `r-${Math.random().toString(36).slice(2, 8)}`, nombre: `${r.nombre} (copia)` };
    const pos = leerLocal<Record<string, { x: number; y: number }>>(CLAVE_POSICIONES, {})[id];
    if (pos) ubicar(copia.id, { x: pos.x + 40, y: pos.y + 40 });
    set((x) => ({ borradores: [...x.borradores, copia], foco: { tipo: "regla", id: copia.id } }));
  },
  crearNota: (donde) => {
    const n: Nota = { id: `nota-${Math.random().toString(36).slice(2, 8)}`, texto: "" };
    ubicar(n.id, donde);
    set((s) => {
      const notas = [...s.notas, n];
      guardarLocal(CLAVE_NOTAS, notas);
      return { notas };
    });
  },
  cambiarNota: (id, texto) =>
    set((s) => {
      const notas = s.notas.map((n) => (n.id === id ? { ...n, texto } : n));
      guardarLocal(CLAVE_NOTAS, notas);
      return { notas };
    }),
  borrarNota: (id) =>
    set((s) => {
      const notas = s.notas.filter((n) => n.id !== id);
      guardarLocal(CLAVE_NOTAS, notas);
      return { notas };
    }),
  centrar: (id) => set((s) => ({ objetivo: { id, n: (s.objetivo?.n ?? 0) + 1 }, vista: "flujo" })),
  abrirParametro: (nodo, clave) => set({ foco: { tipo: "nodo", id: nodo }, parametroAbierto: clave, vista: "flujo" }),

  notificaciones: [],
  sinLeer: 0,
  aprobacionesDisponibles: null,
  cargarNotificaciones: async () => {
    const r = await pedir<{ notificaciones: Notificacion[]; sin_leer: number; aprobaciones_disponibles?: boolean }>("/api/notificaciones");
    set({ notificaciones: r.notificaciones, sinLeer: r.sin_leer, aprobacionesDisponibles: r.aprobaciones_disponibles === true });
  },
  marcarLeida: async (id) => {
    set((s) => ({ notificaciones: s.notificaciones.map((n) => (n.id === id ? { ...n, leida: true } : n)), sinLeer: Math.max(0, s.sinLeer - (s.notificaciones.some((n) => n.id === id && !n.leida) ? 1 : 0)) }));
    const r = await pedir<{ sin_leer: number }>(`/api/notificaciones/${encodeURIComponent(id)}/leida`, { method: "POST" }).catch(() => null);
    if (r) set({ sinLeer: r.sin_leer });
  },
  marcarTodasLeidas: async () => {
    set((s) => ({ notificaciones: s.notificaciones.map((n) => ({ ...n, leida: true })), sinLeer: 0 }));
    await pedir("/api/notificaciones/leidas", { method: "POST" }).catch(() => undefined);
  },
  historialFoco: null,
  abrirEnHistorial: async (corrida, flujo) => {
    if (flujo && flujo !== get().flujo) await get().cambiarFlujo(flujo);
    set((s) => ({ historialFoco: { corrida, n: (s.historialFoco?.n ?? 0) + 1 }, vista: "historial" }));
  },
}));

let conexiones = 0;

type Resumen = Omit<Corrida, "eventos"> & { nEventos: number };

/**
 * Une lo que dice el servidor (lista y eventos recientes) con lo que ya llegó
 * por el canal: para cada corrida gana el estado más avanzado y los eventos no
 * se duplican (se identifican por tipo y momento).
 */
function fusionar(lista: Resumen[], recientes: Evento[], s: { corridas: Record<string, Corrida>; orden: string[]; recientes: Evento[] }) {
  const corridas: Record<string, Corrida> = {};
  for (const c of lista) corridas[c.id] = { ...c, eventos: [] };
  for (const e of recientes) if (e.corrida && corridas[e.corrida]) corridas[e.corrida]!.eventos.push(e);
  const clave = (e: Evento) => `${e.tipo}|${e.t}|${String(e.etapa ?? e.nodo ?? e.campo ?? "")}`;
  const avance = { en_cola: 0, procesando: 1, terminada: 2, fallida: 2 } as Record<string, number>;
  for (const [id, viva] of Object.entries(s.corridas)) {
    const base = corridas[id];
    if (!base) {
      corridas[id] = viva;
      continue;
    }
    const vistos = new Set(base.eventos.map(clave));
    const extra = viva.eventos.filter((e) => !vistos.has(clave(e)));
    const eventos = [...base.eventos, ...extra].sort((a, b) => a.t - b.t);
    const masAvanzada = (avance[viva.estado] ?? 0) > (avance[base.estado] ?? 0) ? viva : base;
    corridas[id] = { ...base, eventos, estado: masAvanzada.estado, resultado: masAvanzada.resultado ?? base.resultado ?? viva.resultado };
  }
  const orden = [...new Set([...s.orden.filter((id) => corridas[id]), ...lista.map((c) => c.id)])].sort((a, b) => (corridas[b]!.creada ?? 0) - (corridas[a]!.creada ?? 0));
  const vistosRecientes = new Set(recientes.map(clave));
  const todos = [...recientes, ...s.recientes.filter((e) => !vistosRecientes.has(clave(e)))].sort((a, b) => a.t - b.t).slice(-3000);
  return { corridas, orden, recientes: todos };
}

async function resincronizar(): Promise<void> {
  const id = usar.getState().flujo;
  try {
    const [lista, recientes, motor] = await Promise.all([
      pedir<Resumen[]>(q("/api/corridas", id)),
      pedir<Evento[]>(q("/api/eventos/recientes", id)),
      pedir<EstadoMotor>(q("/api/salud", id)),
    ]);
    if (usar.getState().flujo !== id) return;
    usar.setState((s) => ({ motor, ...fusionar(lista, recientes, s) }));
    // La corrida a la vista se vuelve a pedir completa, por si le faltan eventos.
    const visible = usar.getState().corridaVisible;
    if (visible) {
      const completa = await pedir<Corrida>(`/api/corridas/${visible}`).catch(() => null);
      if (completa && usar.getState().flujo === id) usar.setState((s) => ({ corridas: { ...s.corridas, [visible]: completa } }));
    }
  } catch {
    /* el servidor aún no responde: la próxima reconexión lo intenta de nuevo */
  }
}

function recibir(e: Evento): void {
  const s0 = usar.getState();
  // Las notificaciones son de toda la plataforma, no del flujo abierto.
  if (e.tipo === "notificacion" || e.tipo === "notificaciones") {
    const n = e.notificacion as Notificacion | undefined;
    usar.setState((s) => ({
      notificaciones: n ? [n, ...s.notificaciones.filter((x) => x.id !== n.id)].slice(0, 100) : s.notificaciones,
      sinLeer: typeof e.sin_leer === "number" ? e.sin_leer : s.sinLeer,
    }));
    if (e.tipo === "notificaciones") void s0.cargarNotificaciones().catch(() => undefined);
    return;
  }
  // Un flujo que no estaba (creado a mano, desde una plantilla o por Vera): un paso de la guía.
  if (e.tipo === "flujo_guardado" && typeof e.flujo === "string" && s0.flujos.length && !s0.flujos.some((f) => f.id === e.flujo)) marcarPaso("crear");
  if (e.tipo === "flujo_guardado" || e.tipo === "flujo_borrado") {
    void s0.cargarFlujos();
    // El editor abierto se recarga solo (o avisa si hay cambios sin guardar).
    if (e.tipo === "flujo_guardado") window.dispatchEvent(new CustomEvent("positiva:flujo-guardado", { detail: { flujo: e.flujo, version: e.version, entorno: e.entorno } }));
    if (e.flujo === s0.flujo) {
      if (e.tipo === "flujo_borrado") void s0.cambiarFlujo("pqrd");
      else void pedir<Catalogo>(q("/api/catalogo", s0.flujo)).then((catalogo) => usar.setState({ catalogo }));
    }
    return;
  }
  // Lo de otro flujo no entra aquí: cada flujo tiene sus propias corridas.
  if (e.flujo && e.flujo !== s0.flujo) return;
  if (e.tipo === "motor") {
    usar.setState({ motor: e as unknown as EstadoMotor });
    return;
  }
  if (e.tipo === "configuracion_guardada") {
    usar.setState({ configuracion: e.configuracion as Configuracion });
    return;
  }
  usar.setState((s) => {
    const recientes = s.recientes.length > 3000 ? [...s.recientes.slice(-2500), e] : [...s.recientes, e];
    if (!e.corrida) return { recientes };
    const id = e.corrida;
    const previa = s.corridas[id] ?? { id, caso: String(e.caso ?? ""), estado: "en_cola" as const, creada: e.t * 1000, eventos: [] };
    const c: Corrida = { ...previa, eventos: [...previa.eventos, e] };
    if (e.tipo === "asignada" || e.tipo === "inicio") c.estado = "procesando";
    if (e.tipo === "resultado") {
      c.resultado = e;
      c.estado = e.accion === "error" || e.accion === "cancelada" ? "fallida" : "terminada";
    }
    const orden = s.orden.includes(id) ? s.orden : [id, ...s.orden];
    // En vivo: el lienzo sigue a la corrida que acaba de empezar.
    const corridaVisible = s.seguirEnVivo && e.tipo === "inicio" ? id : s.corridaVisible;
    return { corridas: { ...s.corridas, [id]: c }, orden, recientes, corridaVisible };
  });
}
