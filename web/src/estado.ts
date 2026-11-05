import { create } from "zustand";
import type { Catalogo, Configuracion, Corrida, Correo, EstadoMotor, Evento } from "./tipos.ts";

async function pedir<T>(ruta: string, init?: RequestInit): Promise<T> {
  const r = await fetch(ruta, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  const cuerpo = (await r.json()) as T & { error?: string };
  if (!r.ok) throw new Error(cuerpo.error ?? `error ${r.status}`);
  return cuerpo;
}

interface Estado {
  catalogo: Catalogo | null;
  bandeja: Correo[];
  configuracion: Configuracion | null;
  modelos: string[];
  corridas: Record<string, Corrida>;
  orden: string[];
  seleccionada: string | null;
  nodo: string | null;
  motor: EstadoMotor | null;
  conectado: boolean;
  aviso: string;
  cargar: () => Promise<void>;
  conectar: () => void;
  procesar: (casos: string[]) => Promise<void>;
  cancelarCola: () => Promise<void>;
  guardarConfiguracion: (c: Configuracion) => Promise<void>;
  seleccionar: (id: string | null) => void;
  verNodo: (id: string | null) => void;
  avisar: (texto: string) => void;
}

export const usar = create<Estado>((set, get) => ({
  catalogo: null,
  bandeja: [],
  configuracion: null,
  modelos: [],
  corridas: {},
  orden: [],
  seleccionada: null,
  nodo: null,
  motor: null,
  conectado: false,
  aviso: "",

  cargar: async () => {
    const [catalogo, bandeja, conf, lista] = await Promise.all([
      pedir<Catalogo>("/api/catalogo"),
      pedir<Correo[]>("/api/bandeja"),
      pedir<Configuracion & { modelos: string[] }>("/api/configuracion"),
      pedir<(Omit<Corrida, "eventos"> & { nEventos: number })[]>("/api/corridas"),
    ]);
    const { modelos, ...configuracion } = conf;
    const corridas: Record<string, Corrida> = {};
    for (const c of lista) corridas[c.id] = { ...c, eventos: [] };
    set({ catalogo, bandeja, configuracion, modelos, corridas, orden: lista.map((c) => c.id) });
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

  procesar: async (casos) => {
    const creadas = await pedir<{ id: string; caso: string }[]>("/api/corridas", { method: "POST", body: JSON.stringify({ casos }) });
    set((s) => {
      const corridas = { ...s.corridas };
      for (const c of creadas) corridas[c.id] ??= { id: c.id, caso: c.caso, estado: "en_cola", creada: Date.now(), eventos: [] };
      const nuevas = creadas.map((c) => c.id).filter((id) => !s.orden.includes(id));
      return { corridas, orden: [...nuevas.reverse(), ...s.orden], seleccionada: creadas[0]?.id ?? s.seleccionada };
    });
  },

  cancelarCola: async () => {
    await pedir("/api/corridas/cola", { method: "DELETE" });
  },

  guardarConfiguracion: async (c) => {
    const guardada = await pedir<Configuracion>("/api/configuracion", { method: "PUT", body: JSON.stringify(c) });
    set({ configuracion: guardada });
  },

  seleccionar: async (id) => {
    set({ seleccionada: id });
    const c = id ? get().corridas[id] : null;
    if (id && c && c.eventos.length === 0 && (c.estado === "terminada" || c.estado === "fallida")) {
      const completa = await pedir<Corrida>(`/api/corridas/${id}`);
      set((s) => ({ corridas: { ...s.corridas, [id]: completa } }));
    }
  },

  verNodo: (id) => set({ nodo: id }),

  avisar: (texto) => {
    set({ aviso: texto });
    setTimeout(() => get().aviso === texto && set({ aviso: "" }), 5000);
  },
}));

function recibir(e: Evento): void {
  if (e.tipo === "motor") {
    usar.setState({ motor: e as unknown as EstadoMotor });
    return;
  }
  if (!e.corrida) return;
  const id = e.corrida;
  usar.setState((s) => {
    const previa = s.corridas[id] ?? { id, caso: String(e.caso ?? ""), estado: "en_cola" as const, creada: e.t * 1000, eventos: [] };
    const c: Corrida = { ...previa, eventos: [...previa.eventos, e] };
    if (e.tipo === "asignada" || e.tipo === "inicio") c.estado = "procesando";
    if (e.tipo === "resultado") {
      c.resultado = e;
      c.estado = e.accion === "error" || e.accion === "cancelada" ? "fallida" : "terminada";
    }
    const orden = s.orden.includes(id) ? s.orden : [id, ...s.orden];
    return { corridas: { ...s.corridas, [id]: c }, orden };
  });
}
