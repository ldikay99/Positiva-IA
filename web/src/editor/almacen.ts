import { create } from "zustand";
import { guardarLocal, leerLocal, pedir, usar } from "../estado.ts";
import type { Corrida } from "../tipos.ts";
import { marcarPaso } from "../guia.ts";
import { autoOrdenar, normalizarTipos, slugFlujo, type Definicion, type Posicion, type TipoNodo } from "./modelo.ts";
import { deshacerGuardado, rehacerGuardado } from "./versiones.ts";

/**
 * El estado del editor de flujos declarativos: la definición que se está
 * editando, su historia para deshacer, lo que dijo la validación y los
 * catálogos que publica el servidor (tipos de nodo y credenciales).
 */

export interface VariableEntorno {
  nombre: string;
  valor?: string;
  secreto: boolean;
  definido: boolean;
  descripcion?: string;
  actualizado?: number;
  /** Copiada de otro flujo al clonar (el mismo valor; los secretos van cifrados). */
  copiado_de?: string;
}

export function normalizarEntorno(x: unknown): VariableEntorno[] {
  const lista = Array.isArray(x) ? x : Array.isArray((x as { variables?: unknown[] })?.variables) ? (x as { variables: unknown[] }).variables : [];
  return (lista as Record<string, unknown>[])
    .filter((v) => v && typeof v.nombre === "string")
    .map((v) => ({
      nombre: String(v.nombre),
      valor: typeof v.valor === "string" ? v.valor : v.valor === undefined || v.valor === null ? undefined : String(v.valor),
      secreto: Boolean(v.secreto),
      definido: v.definido === undefined ? v.valor !== undefined : Boolean(v.definido),
      descripcion: typeof v.descripcion === "string" ? v.descripcion : undefined,
      actualizado: typeof v.actualizado === "number" ? v.actualizado : undefined,
      ...(typeof v.copiado_de === "string" ? { copiado_de: v.copiado_de } : {}),
    }))
    .sort((a, b) => a.nombre.localeCompare(b.nombre));
}

export interface Credencial {
  nombre: string;
  /** «global» o el id del flujo dueño. */
  alcance?: string;
  tipo: string;
  descripcion?: string;
  campos: string[];
  permitir_escritura: boolean;
  usada_por?: { flujo: string; nodo?: string; nombre?: string }[];
  actualizado?: number;
  /** Cuándo la usó una corrida por última vez. */
  usada?: number;
  /** La guardó una persona desde la web (necesario para red_privada). */
  creada_por_persona?: boolean;
  /** OAuth (google_oauth, microsoft_oauth): la cuenta conectada y sus permisos; nunca tokens. */
  conexion?: { conectada: boolean; cuenta?: string; alcances?: string[]; conectada_en?: number; expira?: number };
}

export interface CampoCredencial {
  nombre: string;
  titulo: string;
  secreto: boolean;
  obligatorio: boolean;
  ayuda?: string;
  tipo?: string;
  placeholder?: string;
  opciones?: string[];
  defecto?: string | number | boolean;
  /** Lo llena la plataforma (tokens OAuth): no va en el formulario. */
  interno?: boolean;
}

export interface TipoCredencial {
  tipo: string;
  nombre: string;
  descripcion?: string;
  ayuda?: string;
  campos: CampoCredencial[];
  escritura?: string;
}

export interface Version {
  version: number;
  actualizado: number;
  nota?: string;
  autor?: "persona" | "vera" | null;
  resumen?: string;
  punto?: string;
  etiqueta?: string;
}

export interface Problema {
  texto: string;
  nodo: string | null;
}

const CLAVE_DISENIO = "positiva-ia:disenio";
const claveDisenio = (flujo: string, version: number) => `${CLAVE_DISENIO}:${flujo}:${version}`;

function normalizarCredenciales(x: unknown): Credencial[] {
  if (!Array.isArray(x)) return [];
  return x.filter((c): c is Record<string, unknown> => Boolean(c) && typeof c === "object").map((c) => ({
    nombre: String(c.nombre ?? ""),
    alcance: typeof c.alcance === "string" ? c.alcance : undefined,
    tipo: String(c.tipo ?? ""),
    descripcion: typeof c.descripcion === "string" ? c.descripcion : undefined,
    campos: Array.isArray(c.campos) ? c.campos.map((k) => (typeof k === "string" ? k : String((k as Record<string, unknown>)?.nombre ?? ""))).filter(Boolean) : c.campos && typeof c.campos === "object" ? Object.keys(c.campos) : [],
    permitir_escritura: Boolean(c.permitir_escritura),
    usada_por: Array.isArray(c.usada_por)
      ? c.usada_por.map((u) => (typeof u === "string" ? { flujo: u } : { flujo: String((u as Record<string, unknown>).flujo ?? ""), nodo: (u as Record<string, unknown>).nodo as string | undefined, nombre: (u as Record<string, unknown>).nombre as string | undefined }))
      : undefined,
    actualizado: typeof c.actualizado === "number" ? c.actualizado : typeof c.actualizada === "number" ? c.actualizada : undefined,
    usada: typeof c.usada === "number" ? c.usada : undefined,
    creada_por_persona: c.creada_por_persona === true,
    conexion: c.conexion && typeof c.conexion === "object" ? (c.conexion as Credencial["conexion"]) : undefined,
  }));
}

function normalizarTiposCredencial(x: unknown): TipoCredencial[] {
  const lista = Array.isArray(x) ? x : x && typeof x === "object" ? Object.entries(x as Record<string, unknown>).map(([tipo, v]) => ({ tipo, ...(v as object) })) : [];
  return lista
    .filter((t): t is Record<string, unknown> => Boolean(t) && typeof t === "object")
    .map((t) => {
      const crudos = Array.isArray(t.campos) ? t.campos : t.campos && typeof t.campos === "object" ? Object.entries(t.campos as Record<string, unknown>).map(([nombre, v]) => ({ nombre, ...(v as object) })) : [];
      return {
        tipo: String(t.tipo ?? t.id ?? ""),
        nombre: String(t.nombre ?? t.titulo ?? t.tipo ?? ""),
        descripcion: typeof t.descripcion === "string" ? t.descripcion : undefined,
        ayuda: typeof t.ayuda === "string" ? t.ayuda : undefined,
        escritura: typeof t.escritura === "string" ? t.escritura : undefined,
        // los campos internos (tokens de OAuth, cuenta conectada) los llena la plataforma: no van en el formulario
        campos: (crudos as Record<string, unknown>[]).filter((c) => c.interno !== true).map((c) => ({
          nombre: String(c.nombre ?? c.clave ?? ""),
          titulo: String(c.titulo ?? c.title ?? c.etiqueta ?? c.nombre ?? ""),
          secreto: Boolean(c.secreto),
          obligatorio: Boolean(c.obligatorio ?? c.requerido),
          ayuda: typeof (c.ayuda ?? c.descripcion) === "string" ? String(c.ayuda ?? c.descripcion) : undefined,
          tipo: typeof c.tipo === "string" ? c.tipo : undefined,
          placeholder: typeof (c.placeholder ?? c.ejemplo) === "string" ? String(c.placeholder ?? c.ejemplo) : undefined,
          opciones: Array.isArray(c.opciones) ? c.opciones.map(String) : undefined,
          defecto: typeof c.defecto === "string" || typeof c.defecto === "number" || typeof c.defecto === "boolean" ? c.defecto : undefined,
        })).filter((c) => c.nombre),
      };
    })
    .filter((t) => t.tipo);
}

/** Un problema de validación, ubicado en su nodo cuando se puede. */
function ubicarProblemas(problemas: string[], def: Definicion): Problema[] {
  return problemas.map((texto) => {
    const indice = /nodos\.(\d+)/.exec(texto);
    let nodo: string | null = indice ? (def.nodos[Number(indice[1])]?.id ?? null) : null;
    if (!nodo) {
      // «tema (ia_clasificar): …», «el nodo «tema» …», «arista tema → datos»
      const ids = [...def.nodos].sort((a, b) => b.id.length - a.id.length);
      nodo = ids.find((n) => new RegExp(`(^|[^\\w])${n.id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^\\w]|$)`).test(texto))?.id ?? null;
    }
    // Quitar la ruta técnica del principio: «nodos.3.config.categorias: …»
    const limpio = texto.replace(/^nodos\.\d+(\.config)?(\.[\w.]+)?:\s*/, "");
    return { texto: limpio, nodo };
  });
}

interface EstadoEditor {
  flujo: string | null;
  version: number;
  actualizado: number;
  definicion: Definicion | null;
  /** La definición tal como está guardada, para saber si hay cambios. */
  guardada: string;
  pasado: Definicion[];
  futuro: Definicion[];
  ultimoGrupo: { clave: string; t: number } | null;
  cargando: boolean;
  error: string;
  /** Si el servidor guarda `disenio` en la definición; si no, el diseño vive en este navegador. */
  disenioEnServidor: boolean | null;
  /** Vera (u otra pestaña) guardó una versión mientras había cambios aquí. */
  versionAjena: number | null;
  /** Lo último que pasó fue deshacer lo guardado (en el servidor): Ctrl+Y lo rehace. */
  deshechoEnServidor: boolean;

  tipos: TipoNodo[];
  tiposCargados: boolean;
  credenciales: Credencial[];
  credencialesDisponibles: boolean;
  tiposCredencial: TipoCredencial[];
  /** Las variables de entorno del flujo abierto (para {{entorno.NOMBRE}}). */
  entorno: VariableEntorno[];
  cargarEntorno: (flujo: string) => Promise<VariableEntorno[] | null>;

  problemas: Problema[];
  validando: boolean;
  guardando: boolean;
  renombrando: boolean;
  resaltados: { ids: string[]; n: number };
  /** Pedido al panel del nodo: abrir una sección. `n` cambia en cada pedido. */
  seccionPedida: { seccion: string; n: number } | null;

  cargarTipos: () => Promise<void>;
  cargarCredenciales: () => Promise<void>;
  abrir: (flujo: string) => Promise<void>;
  recargar: () => Promise<void>;
  cambiar: (f: (d: Definicion) => Definicion, grupo?: string) => void;
  deshacer: () => void;
  rehacer: () => void;
  validar: () => Promise<boolean>;
  guardar: (nota?: string) => Promise<boolean>;
  versiones: () => Promise<Version[] | null>;
  restaurar: (version: number) => Promise<void>;
  renombrar: (nombre: string, descripcion?: string) => Promise<void>;
  duplicar: (nombre: string) => Promise<string>;
  borrar: () => Promise<void>;
  correr: (cuerpo: { entrada?: Record<string, unknown>; caso?: string }) => Promise<void>;
  resaltar: (ids: string[]) => void;
  pedirSeccion: (seccion: string) => void;
  limpiarProblemas: (nodo?: string) => void;
}

export const usarEditor = create<EstadoEditor>((set, get) => ({
  flujo: null,
  version: 0,
  actualizado: 0,
  definicion: null,
  guardada: "",
  pasado: [],
  futuro: [],
  ultimoGrupo: null,
  cargando: false,
  error: "",
  disenioEnServidor: null,
  versionAjena: null,
  deshechoEnServidor: false,
  tipos: [],
  tiposCargados: false,
  credenciales: [],
  credencialesDisponibles: false,
  tiposCredencial: [],
  entorno: [],
  cargarEntorno: async (flujo) => {
    try {
      const entorno = normalizarEntorno(await pedir<unknown>(`/api/flujos/${encodeURIComponent(flujo)}/entorno`));
      if (get().flujo === flujo || !get().flujo) set({ entorno });
      return entorno;
    } catch {
      if (get().flujo === flujo) set({ entorno: [] });
      return null;
    }
  },
  problemas: [],
  validando: false,
  guardando: false,
  renombrando: false,
  resaltados: { ids: [], n: 0 },
  seccionPedida: null,

  cargarTipos: async () => {
    try {
      const tipos = normalizarTipos(await pedir<unknown>("/api/nodos/tipos"));
      set({ tipos, tiposCargados: tipos.length > 0 });
    } catch {
      set({ tiposCargados: false });
    }
  },

  cargarCredenciales: async () => {
    try {
      const [lista, tipos] = await Promise.all([pedir<unknown>("/api/credenciales"), pedir<unknown>("/api/credenciales/tipos").catch(() => [])]);
      set({ credenciales: normalizarCredenciales(lista), tiposCredencial: normalizarTiposCredencial(tipos), credencialesDisponibles: true });
    } catch {
      set({ credencialesDisponibles: false });
    }
  },

  abrir: async (flujo) => {
    set({ cargando: true, error: "", flujo, entorno: [] });
    if (!get().tiposCargados) void get().cargarTipos();
    void get().cargarEntorno(flujo);
    try {
      const f = await pedir<{ definicion?: Definicion; version?: number; actualizado?: number }>(`/api/flujos/${encodeURIComponent(flujo)}`);
      if (get().flujo !== flujo) return;
      if (!f.definicion) throw new Error("este flujo no tiene una definición editable");
      const version = f.version ?? 1;
      let def = structuredClone(f.definicion);
      const enServidor = Boolean(def.disenio && Object.keys(def.disenio).length);
      if (!enServidor) {
        const local = leerLocal<Record<string, Posicion> | null>(claveDisenio(flujo, version), null);
        def = { ...def, disenio: local ?? autoOrdenar(def, new Map(get().tipos.map((t) => [t.tipo, t]))) };
      }
      set({
        definicion: def,
        guardada: JSON.stringify(def),
        version,
        actualizado: f.actualizado ?? 0,
        pasado: [],
        futuro: [],
        ultimoGrupo: null,
        cargando: false,
        problemas: [],
        versionAjena: null,
        disenioEnServidor: enServidor ? true : get().disenioEnServidor,
      });
    } catch (e) {
      set({ cargando: false, error: (e as Error).message, definicion: null });
    }
  },

  recargar: async () => {
    const f = get().flujo;
    if (f) await get().abrir(f);
  },

  cambiar: (f, grupo) => {
    const d = get().definicion;
    if (!d) return;
    const sig = f(structuredClone(d));
    const ahora = Date.now();
    const g = get().ultimoGrupo;
    // Escribir en un campo es un solo paso de deshacer, no uno por letra.
    const mismo = grupo && g && g.clave === grupo && ahora - g.t < 1200;
    set((s) => ({
      definicion: sig,
      pasado: mismo ? s.pasado : [...s.pasado.slice(-79), d],
      futuro: [],
      ultimoGrupo: grupo ? { clave: grupo, t: ahora } : null,
      deshechoEnServidor: false,
    }));
  },

  deshacer: () => {
    const { pasado, definicion, flujo } = get();
    if (!definicion) return;
    if (!pasado.length) {
      // Nada sin guardar que deshacer aquí: se deshace lo último guardado (también lo que hizo Vera), como versión nueva.
      if (flujo && !hayCambios(get()) && get().version > 1) void deshacerGuardado(flujo).then((r) => { if (r) { set({ deshechoEnServidor: true }); void get().recargar().then(() => set({ deshechoEnServidor: true })); } });
      return;
    }
    set((s) => ({ definicion: pasado.at(-1)!, pasado: pasado.slice(0, -1), futuro: [definicion, ...s.futuro], ultimoGrupo: null }));
  },

  rehacer: () => {
    const { futuro, definicion, flujo } = get();
    if (!definicion) return;
    if (!futuro.length) {
      if (flujo && !hayCambios(get()) && get().deshechoEnServidor) void rehacerGuardado(flujo).then((r) => { if (r) void get().recargar().then(() => set({ deshechoEnServidor: false })); });
      return;
    }
    set((s) => ({ definicion: futuro[0]!, futuro: futuro.slice(1), pasado: [...s.pasado, definicion], ultimoGrupo: null }));
  },

  validar: async () => {
    const d = get().definicion;
    if (!d) return false;
    set({ validando: true });
    try {
      const r = await pedir<{ ok: boolean; problemas: string[] }>("/api/flujos/validar", { method: "POST", body: JSON.stringify({ definicion: d }) });
      set({ problemas: ubicarProblemas(r.problemas ?? [], d) });
      return r.ok;
    } catch (e) {
      set({ problemas: [{ texto: (e as Error).message, nodo: null }] });
      return false;
    } finally {
      set({ validando: false });
    }
  },

  guardar: async () => {
    const { definicion: d, flujo } = get();
    if (!d || !flujo) return false;
    set({ guardando: true });
    try {
      if (!(await get().validar())) return false;
      const r = await fetch(`/api/flujos/${encodeURIComponent(flujo)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ definicion: d }) });
      const cuerpo = (await r.json().catch(() => ({}))) as { flujo?: { version?: number; actualizado?: number; definicion?: Definicion }; error?: string; problemas?: string[] };
      if (!r.ok) {
        set({ problemas: ubicarProblemas(cuerpo.problemas?.length ? cuerpo.problemas : [cuerpo.error ?? `error ${r.status}`], d) });
        return false;
      }
      const version = cuerpo.flujo?.version ?? get().version + 1;
      const enServidor = Boolean(cuerpo.flujo?.definicion?.disenio && Object.keys(cuerpo.flujo.definicion.disenio).length);
      // Si el servidor no guarda el diseño, se queda en este navegador con la versión.
      if (d.disenio) guardarLocal(claveDisenio(flujo, version), d.disenio);
      set({ version, actualizado: cuerpo.flujo?.actualizado ?? Date.now(), guardada: JSON.stringify(d), problemas: [], disenioEnServidor: enServidor, versionAjena: null, deshechoEnServidor: false });
      void usar.getState().cargarFlujos();
      return true;
    } finally {
      set({ guardando: false });
    }
  },

  versiones: async () => {
    const f = get().flujo;
    if (!f) return null;
    try {
      const r = await pedir<unknown>(`/api/flujos/${encodeURIComponent(f)}/versiones`);
      const lista = Array.isArray(r) ? r : Array.isArray((r as { versiones?: unknown[] })?.versiones) ? (r as { versiones: unknown[] }).versiones : [];
      return (lista as Record<string, unknown>[]).map((v) => ({ version: Number(v.version), actualizado: Number(v.actualizado ?? v.fecha ?? 0), nota: typeof v.nota === "string" ? v.nota : undefined, autor: v.autor === "vera" || v.autor === "persona" ? (v.autor as "vera" | "persona") : null, resumen: typeof v.resumen === "string" ? v.resumen : undefined, punto: typeof v.punto === "string" ? v.punto : undefined, etiqueta: typeof v.etiqueta === "string" ? v.etiqueta : undefined })).sort((a, b) => b.version - a.version);
    } catch {
      return null;
    }
  },

  restaurar: async (version) => {
    const f = get().flujo;
    if (!f) return;
    await pedir(`/api/flujos/${encodeURIComponent(f)}/restaurar`, { method: "POST", body: JSON.stringify({ version }) });
    // El diseño de la versión restaurada, si este navegador lo tenía.
    await get().abrir(f);
    const local = leerLocal<Record<string, Posicion> | null>(claveDisenio(f, version), null);
    const d = get().definicion;
    if (local && d && !get().disenioEnServidor) {
      const def = { ...d, disenio: local };
      guardarLocal(claveDisenio(f, get().version), local);
      set({ definicion: def, guardada: JSON.stringify(def) });
    }
    void usar.getState().cargarFlujos();
  },

  renombrar: async (nombre, descripcion) => {
    const { flujo, definicion: d } = get();
    if (!flujo || !d) return;
    const cambios = { nombre, ...(descripcion !== undefined ? { descripcion } : {}) };
    try {
      set({ renombrando: true });
      const r = await pedir<{ flujo?: { version?: number } }>(`/api/flujos/${encodeURIComponent(flujo)}`, { method: "PATCH", body: JSON.stringify(cambios) });
      // El nombre ya quedó guardado: no cuenta como cambio pendiente.
      const guardada = JSON.parse(get().guardada || "{}") as Definicion;
      const version = r.flujo?.version ?? get().version;
      if (version !== get().version && get().definicion?.disenio) guardarLocal(claveDisenio(flujo, version), get().definicion!.disenio);
      // Renombrar también se deshace con Ctrl+Z: vuelve el nombre de antes como cambio sin guardar.
      set((s) => ({ version, definicion: s.definicion ? { ...s.definicion, ...cambios } : s.definicion, guardada: JSON.stringify({ ...guardada, ...cambios }), pasado: [...s.pasado.slice(-79), d], futuro: [], ultimoGrupo: null }));
      void usar.getState().cargarFlujos();
    } catch {
      // Sin PATCH todavía: el nombre entra como cambio y se guarda con «Guardar».
      get().cambiar((x) => ({ ...x, ...cambios }));
    } finally {
      set({ renombrando: false });
    }
  },

  duplicar: async (nombre) => {
    const f = get().flujo;
    if (!f) throw new Error("no hay flujo abierto");
    const id_nuevo = slugFlujo(nombre, usar.getState().flujos.map((x) => x.id));
    await pedir(`/api/flujos/${encodeURIComponent(f)}/duplicar`, { method: "POST", body: JSON.stringify({ id_nuevo, nombre }) });
    const d = get().definicion;
    if (d?.disenio) guardarLocal(claveDisenio(id_nuevo, 1), d.disenio);
    await usar.getState().cargarFlujos();
    return id_nuevo;
  },

  borrar: async () => {
    const f = get().flujo;
    if (!f) return;
    await pedir(`/api/flujos/${encodeURIComponent(f)}`, { method: "DELETE" });
    set({ definicion: null, flujo: null });
    await usar.getState().cargarFlujos();
  },

  correr: async (cuerpo) => {
    const f = get().flujo;
    if (!f) return;
    const r = await pedir<{ id: string; caso: string } | { id: string; caso: string }[]>(`/api/flujos/${encodeURIComponent(f)}/correr`, { method: "POST", body: JSON.stringify(cuerpo) });
    marcarPaso("correr");
    const creadas = Array.isArray(r) ? r : [r];
    usar.setState((s) => {
      const corridas: Record<string, Corrida> = { ...s.corridas };
      for (const c of creadas) corridas[c.id] ??= { id: c.id, caso: c.caso, estado: "en_cola", creada: Date.now(), eventos: [] };
      const nuevas = creadas.map((c) => c.id).filter((id) => !s.orden.includes(id));
      return { corridas, orden: [...nuevas.reverse(), ...s.orden], corridaVisible: creadas[0]?.id ?? s.corridaVisible };
    });
  },

  resaltar: (ids) => set((s) => ({ resaltados: { ids, n: s.resaltados.n + 1 } })),
  pedirSeccion: (seccion) => set((s) => ({ seccionPedida: { seccion, n: (s.seccionPedida?.n ?? 0) + 1 } })),
  limpiarProblemas: (nodo) => set((s) => ({ problemas: nodo ? s.problemas.filter((p) => p.nodo !== nodo) : [] })),
}));

/** ¿Hay cambios sin guardar? Mover un nodo también cuenta: el diseño es parte del flujo. */
export function hayCambios(s: Pick<EstadoEditor, "definicion" | "guardada">): boolean {
  return Boolean(s.definicion) && JSON.stringify(s.definicion) !== s.guardada;
}
