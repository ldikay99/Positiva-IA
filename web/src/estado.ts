import { create } from "zustand";
import type { Catalogo, Configuracion, Corrida, Correo, EstadoMotor, Evento } from "./tipos.ts";

export async function pedir<T>(ruta: string, init?: RequestInit): Promise<T> {
  const r = await fetch(ruta, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  const cuerpo = (await r.json().catch(() => ({}))) as T & { error?: string };
  if (!r.ok) throw new Error(cuerpo.error ?? `error ${r.status}`);
  return cuerpo;
}

export type Vista = "flujo" | "corridas" | "logs" | "herramientas" | "proveedores" | "configuracion";
export type Foco = { tipo: "nodo"; id: string } | { tipo: "correo"; caso: string } | { tipo: "corrida"; id: string } | null;

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
  cargar: () => Promise<void>;
  conectar: () => void;
  irA: (v: Vista) => void;
  enfocar: (f: Foco) => void;
  verCorrida: (id: string | null) => void;
  setSeguirEnVivo: (v: boolean) => void;
  alternarSeleccion: (caso: string) => void;
  fijarSeleccion: (casos: string[]) => void;
  procesar: (casos: string[]) => Promise<void>;
  cancelarCola: () => Promise<void>;
  guardarConfiguracion: (c: Configuracion, nota?: string) => Promise<number>;
  cargarPerfiles: () => Promise<void>;
  guardarPerfil: (nombre: string, descripcion: string) => Promise<void>;
  aplicarPerfil: (nombre: string) => Promise<void>;
  borrarPerfil: (nombre: string) => Promise<void>;
  cargarHistorial: () => Promise<void>;
  avisar: (texto: string, tono?: Aviso["tono"]) => void;
  cerrarAviso: (id: number) => void;
}

let siguienteAviso = 1;

export const usar = create<Estado>((set, get) => ({
  catalogo: null,
  bandeja: [],
  configuracion: null,
  modelos: [],
  corridas: {},
  orden: [],
  recientes: [],
  motor: null,
  conectado: false,
  vista: "flujo",
  foco: null,
  corridaVisible: null,
  seguirEnVivo: true,
  seleccion: [],
  avisos: [],
  perfiles: [],
  historial: [],

  cargar: async () => {
    const [catalogo, bandeja, conf, lista, recientes] = await Promise.all([
      pedir<Catalogo>("/api/catalogo"),
      pedir<Correo[]>("/api/bandeja"),
      pedir<Configuracion & { modelos: string[] }>("/api/configuracion"),
      pedir<(Omit<Corrida, "eventos"> & { nEventos: number })[]>("/api/corridas"),
      pedir<Evento[]>("/api/eventos/recientes"),
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
    ws.onopen = () => set({ conectado: true });
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

  procesar: async (casos) => {
    const creadas = await pedir<{ id: string; caso: string }[]>("/api/corridas", { method: "POST", body: JSON.stringify({ casos }) });
    set((s) => {
      const corridas = { ...s.corridas };
      for (const c of creadas) corridas[c.id] ??= { id: c.id, caso: c.caso, estado: "en_cola", creada: Date.now(), eventos: [] };
      const nuevas = creadas.map((c) => c.id).filter((id) => !s.orden.includes(id));
      return { corridas, orden: [...nuevas.reverse(), ...s.orden], corridaVisible: creadas[0]?.id ?? s.corridaVisible, seleccion: [] };
    });
  },

  cancelarCola: async () => {
    const r = await pedir<{ canceladas: number }>("/api/corridas/cola", { method: "DELETE" });
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
}));

function recibir(e: Evento): void {
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
