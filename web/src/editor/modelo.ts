/**
 * El modelo del editor de flujos declarativos: la forma de la definición, los
 * tipos de nodo que publica el servidor y las reglas que el lienzo aplica al
 * conectar, ordenar y sugerir variables. Nada aquí toca la pantalla.
 */

export interface Esquema {
  type?: string | string[];
  title?: string;
  description?: string;
  properties?: Record<string, Esquema>;
  required?: string[];
  enum?: (string | number | boolean)[];
  items?: Esquema;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  format?: string;
  additionalProperties?: boolean | Esquema;
  "x-credencial-tipos"?: string[];
  [k: string]: unknown;
}

export interface TipoNodo {
  tipo: string;
  nombre: string;
  categoria: string;
  descripcion: string;
  disparador: boolean;
  escritura: boolean;
  salidas: string[] | "dinamicas";
  config: Esquema;
  ejemplos: Record<string, unknown>[];
  consejos: string[];
  /** Variables que deja (si el servidor lo dice): «correo.*, adjuntos.texto». */
  produce?: string;
  /** De dónde salen las salidas dinámicas: «categorias[].valor», «casos[]». */
  salidas_desde?: string;
  /** Salidas que puede tener además de las fijas («sin_categoria», «error»). */
  salidas_opcionales?: string[];
  /** No tiene salidas: termina el flujo (o el ciclo). */
  terminal?: boolean;
}

export interface NodoDef {
  id: string;
  tipo: string;
  nombre: string;
  descripcion?: string;
  config: Record<string, unknown>;
}

export interface AristaDef {
  desde: string;
  hacia: string;
  cuando?: string;
}

export type Posicion = { x: number; y: number };

/** Una IA elegida: proveedor, modelo y (si el proveedor la pide) el nombre de su credencial. */
export interface IaElegida { proveedor: string; modelo: string; credencial?: string; temperatura?: number }
export type IaDelFlujo = { usar: "global" } | IaElegida;

export interface Definicion {
  id: string;
  nombre: string;
  descripcion?: string;
  modelo?: string;
  /** La IA del flujo: {usar: "global"} o una propia. Sin ella (flujos de antes): Vertex con «modelo». */
  ia?: IaDelFlujo;
  nodos: NodoDef[];
  aristas: AristaDef[];
  /** Dónde está cada nodo en el lienzo: {nodo_id: {x, y}}. */
  disenio?: Record<string, Posicion>;
  [k: string]: unknown;
}

export const RAMA_ERROR = "error";
/** La salida por defecto: una arista sin «cuando» (o con «*»). */
export const POR_DEFECTO = "*";

export const esDefecto = (cuando: string | undefined) => !cuando || cuando.trim() === "" || cuando.trim() === POR_DEFECTO;

/** Colores e íconos por categoría (los mismos tonos del lienzo de código). */
export const CATEGORIA_VISUAL: Record<string, { nombre: string; icono: string; tono: string }> = {
  disparador: { nombre: "Disparador", icono: "rayo", tono: "ambar" },
  entrada: { nombre: "Entrada", icono: "bandeja", tono: "pizarra" },
  ocr: { nombre: "Documentos", icono: "escaner", tono: "turquesa" },
  documentos: { nombre: "Documentos", icono: "documento", tono: "turquesa" },
  filtro: { nombre: "Filtro", icono: "filtro", tono: "ambar" },
  ruta: { nombre: "Enrutamiento", icono: "ruta", tono: "azul" },
  logica: { nombre: "Lógica", icono: "ruta", tono: "azul" },
  conector: { nombre: "Conectores", icono: "enlace", tono: "turquesa" },
  ia: { nombre: "IA", icono: "chispa", tono: "violeta" },
  busqueda: { nombre: "Búsqueda", icono: "buscar", tono: "azul" },
  validacion: { nombre: "Validación", icono: "escudo", tono: "verde" },
  datos: { nombre: "Datos", icono: "base", tono: "turquesa" },
  integracion: { nombre: "Integración", icono: "enviar", tono: "azul" },
  salida: { nombre: "Salida", icono: "bandera", tono: "pizarra" },
};

export function visualDe(categoria: string | undefined): { nombre: string; icono: string; tono: string } {
  const c = (categoria ?? "").toLowerCase();
  return CATEGORIA_VISUAL[c] ?? { nombre: categoria ? categoria[0]!.toUpperCase() + categoria.slice(1) : "Nodo", icono: "flujo", tono: "pizarra" };
}

/** «Entrada», «IA», … en el orden en que se arma un flujo. */
export const ORDEN_CATEGORIAS = ["disparador", "entrada", "ocr", "documentos", "filtro", "ruta", "logica", "ia", "busqueda", "datos", "validacion", "conector", "integracion", "salida"];

// ------------------------------------------------------------------ salidas

const valorDe = (x: unknown): string => {
  if (typeof x === "string" || typeof x === "number" || typeof x === "boolean") return String(x);
  if (x && typeof x === "object") {
    const o = x as Record<string, unknown>;
    return String(o.valor ?? o.nombre ?? o.id ?? o.caso ?? "");
  }
  return "";
};

/** Las salidas con nombre de un nodo (sin la de error). Las «dinámicas» salen de su config. */
export function salidasDe(tipo: TipoNodo | undefined, config: Record<string, unknown>): string[] {
  if (!tipo || tipo.terminal) return [];
  const opcionales = (tipo.salidas_opcionales ?? []).filter((s) => s.toLowerCase() !== RAMA_ERROR);
  if (Array.isArray(tipo.salidas)) {
    const fijas = tipo.salidas.filter((s) => !s.includes("<") && !s.includes("(")).map((s) => s.trim()).filter(Boolean);
    return [...fijas, ...opcionales.filter((o) => !fijas.includes(o))];
  }
  const nombres: string[] = [];
  const agregar = (v: string) => v && v.toLowerCase() !== RAMA_ERROR && !nombres.includes(v) && nombres.push(v);
  const t = tipo.tipo;
  // «categorias[].valor» → cada valor de config.categorias; «casos[]» → cada elemento.
  const desde = /^([A-Za-z_]\w*)\[\](?:\.([A-Za-z_]\w*))?$/.exec(tipo.salidas_desde ?? "");
  if (desde) {
    const l = config[desde[1]!];
    if (Array.isArray(l)) for (const x of l) agregar(desde[2] && x && typeof x === "object" ? String((x as Record<string, unknown>)[desde[2]] ?? "") : valorDe(x));
    for (const o of opcionales) agregar(o);
    return nombres;
  }
  // Las conocidas; para un tipo nuevo, la primera lista de valores de su config.
  const claves = t === "ia_clasificar" ? ["categorias"] : t === "ia_decidir" ? ["opciones"] : t === "bifurcar_por_valor" ? ["casos"] : ["categorias", "opciones", "casos", "ramas", "salidas", "rutas"];
  for (const k of claves) {
    const l = config[k];
    if (Array.isArray(l)) {
      for (const x of l) agregar(valorDe(x));
      break;
    }
  }
  if (t === "ia_clasificar") agregar("sin_categoria");
  else if (t === "ia_decidir") agregar("sin_decision");
  else if (t === "bifurcar_por_valor") agregar("otro");
  for (const o of opcionales) agregar(o);
  return nombres;
}

/**
 * Las asas de salida que dibuja un nodo: una por nombre y, si tiene varias,
 * una más para «cualquier otro caso» (la arista sin «cuando»). Con una sola
 * salida, la arista por defecto sale de esa misma asa.
 */
export function asasDe(tipo: TipoNodo | undefined, config: Record<string, unknown>): { id: string; etiqueta: string; defecto: boolean }[] {
  const s = salidasDe(tipo, config);
  if (!tipo) return [{ id: POR_DEFECTO, etiqueta: "sigue", defecto: true }];
  if (tipo.terminal || (Array.isArray(tipo.salidas) && tipo.salidas.length === 0 && !s.length)) return [];
  if (s.length <= 1) return [{ id: s[0] ?? POR_DEFECTO, etiqueta: s[0] ?? "sigue", defecto: true }];
  return [...s.map((x) => ({ id: x, etiqueta: x, defecto: false })), { id: POR_DEFECTO, etiqueta: "cualquier otro", defecto: true }];
}

/** El asa de la que sale una arista ya guardada. */
export function asaDeArista(a: AristaDef, asas: { id: string; defecto: boolean }[]): string {
  if (a.cuando?.trim().toLowerCase() === RAMA_ERROR) return RAMA_ERROR;
  if (esDefecto(a.cuando)) return asas.find((x) => x.defecto)?.id ?? POR_DEFECTO;
  return asas.some((x) => x.id === a.cuando) ? a.cuando! : a.cuando!;
}

/** Lo que se guarda en «cuando» al soltar un cable desde un asa. */
export function cuandoDeAsa(asa: string, asas: { id: string; defecto: boolean }[]): string | undefined {
  if (asa === RAMA_ERROR) return RAMA_ERROR;
  const a = asas.find((x) => x.id === asa);
  if (!a || asa === POR_DEFECTO) return undefined;
  // Con una sola salida, «ok» y la arista por defecto son lo mismo: se guarda el nombre.
  return asa;
}

/** ¿Tiene rama «si falla»? Lo dice el servidor; si no, todo lo que no es disparador ni final. */
export function tieneRamaError(tipo: TipoNodo | undefined): boolean {
  if (!tipo) return true;
  if (tipo.salidas_opcionales) return tipo.salidas_opcionales.some((s) => s.toLowerCase() === RAMA_ERROR);
  return !tipo.disparador && !tipo.terminal;
}

// --------------------------------------------------------------- conexiones

export function alcanza(aristas: AristaDef[], desde: string, hasta: string): string[] | null {
  const previo = new Map<string, string>();
  const cola = [desde];
  const vistos = new Set([desde]);
  while (cola.length) {
    const x = cola.shift()!;
    if (x === hasta) {
      const camino = [x];
      let c = x;
      while (previo.has(c)) camino.unshift((c = previo.get(c)!));
      return camino;
    }
    for (const a of aristas) {
      if (a.desde === x && !vistos.has(a.hacia)) {
        vistos.add(a.hacia);
        previo.set(a.hacia, x);
        cola.push(a.hacia);
      }
    }
  }
  return null;
}

/** Por qué no se puede conectar, o null si se puede. */
export function motivoParaNoConectar(def: Definicion, tipos: Map<string, TipoNodo>, desde: string, asa: string, hacia: string): string | null {
  const o = def.nodos.find((n) => n.id === desde);
  const d = def.nodos.find((n) => n.id === hacia);
  if (!o || !d) return "Ese nodo ya no existe";
  if (desde === hacia) return "Un nodo no se conecta consigo mismo";
  const td = tipos.get(d.tipo);
  if (td?.disparador) return `«${d.nombre}» es un disparador: arranca el flujo y no recibe entradas`;
  const asas = asasDe(tipos.get(o.tipo), o.config);
  const cuando = cuandoDeAsa(asa, asas);
  const igual = (a: AristaDef) => a.desde === desde && (asaDeArista(a, asas) === asa || (cuando === undefined ? esDefecto(a.cuando) : a.cuando === cuando));
  if (def.aristas.some((a) => igual(a) && a.hacia === hacia)) return "Esa conexión ya existe";
  const ocupada = def.aristas.find(igual);
  if (ocupada) {
    const destino = def.nodos.find((n) => n.id === ocupada.hacia)?.nombre ?? ocupada.hacia;
    return `La salida «${asa === POR_DEFECTO ? "cualquier otro" : asa === RAMA_ERROR ? "si falla" : asa}» ya va a «${destino}». Quita esa conexión o usa otra salida`;
  }
  const vuelta = alcanza(def.aristas, hacia, desde);
  if (vuelta) {
    const conBucle = [...vuelta, hacia].some((id) => def.nodos.find((n) => n.id === id)?.tipo === "para_cada");
    if (!conBucle) return `Formaría un ciclo (${vuelta.map((id) => def.nodos.find((n) => n.id === id)?.nombre ?? id).join(" → ")} → ${d.nombre}). Solo un bucle «para cada» puede volver atrás`;
  }
  return null;
}

// ----------------------------------------------------------------- variables

export interface Variable {
  nombre: string;
  origen: string;
  pista?: string;
}

const BASE_CORREO: Variable[] = [
  { nombre: "correo.asunto", origen: "Correo", pista: "el asunto" },
  { nombre: "correo.cuerpo", origen: "Correo", pista: "el texto del correo" },
  { nombre: "correo.remitente", origen: "Correo", pista: "nombre de quien escribe" },
  { nombre: "correo.remitente_correo", origen: "Correo", pista: "dirección de quien escribe" },
  { nombre: "correo.recibido", origen: "Correo", pista: "fecha y hora" },
  { nombre: "adjuntos.texto", origen: "Adjuntos", pista: "el texto leído de los adjuntos" },
  { nombre: "adjuntos.leidos", origen: "Adjuntos", pista: "la lista de adjuntos leídos" },
];

function camposDeEntrada(config: Record<string, unknown>): string[] {
  const c = config.campos;
  if (!Array.isArray(c)) return [];
  return c.map((x) => (typeof x === "string" ? x : valorDe(x))).filter(Boolean);
}

/** Lo que deja un nodo para los que vienen después. */
export function variablesDeNodo(n: NodoDef, tipo: TipoNodo | undefined): Variable[] {
  const c = n.config ?? {};
  const v: Variable[] = [];
  const add = (nombre: unknown, pista?: string) => {
    if (typeof nombre === "string" && nombre && !v.some((x) => x.nombre === nombre)) v.push({ nombre, origen: n.nombre, pista });
  };
  const t = n.tipo;
  if (t === "entrada_correo" || t === "correo_nuevo") BASE_CORREO.forEach((b) => add(b.nombre, b.pista));
  if (t === "leer_adjuntos") ["adjuntos.texto", "adjuntos.leidos"].forEach((x) => add(x));
  if (t === "entrada_manual" || t === "webhook" || t === "programado") {
    for (const campo of camposDeEntrada(c)) add(`entrada.${campo}`, "dato de la entrada");
    if (t === "webhook") add("entrada", "el cuerpo recibido");
  }
  for (const lista of ["campos", "mapeo", "operaciones"] as const) {
    const l = c[lista];
    if (!Array.isArray(l) || t === "entrada_manual" || t === "salida") continue;
    for (const x of l) if (x && typeof x === "object") add((x as Record<string, unknown>).nombre ?? (x as Record<string, unknown>).variable ?? (x as Record<string, unknown>).salida, "campo");
  }
  if (typeof c.variable === "string" && t !== "bifurcar_por_valor") add(c.variable, t === "para_cada" ? "el elemento de cada vuelta" : undefined);
  if (t === "para_cada") {
    if (!c.variable) add("elemento", "el elemento de cada vuelta");
    add(typeof c.indice === "string" ? c.indice : "indice", "la vuelta, desde 0");
  }
  if (typeof c.salida === "string") add(c.salida, "resultado del nodo");
  if (t === "ia_decidir") {
    const s = typeof c.salida === "string" ? c.salida : n.id;
    add(s, "la opción elegida");
    add(`${s}_justificacion`, "por qué la eligió");
  }
  // Lo que el servidor diga que produce («salida», «adjuntos.texto», …).
  if (tipo?.produce) {
    for (const p of tipo.produce.split(/[,;]/).map((x) => x.trim().replace(/\s*\(.*$/, ""))) {
      if (!p || p.includes("<") || p.endsWith("*")) continue;
      if (p === "salida" || p === "variable/salida") {
        if (!c.salida && !c.variable && t !== "salida") add(n.id, "resultado del nodo");
      } else if (/^[A-Za-z_][\w.]*$/.test(p)) add(p);
    }
  } else if (!c.salida && !["salida", "filtro", "condicion", "bifurcar_por_valor", "validar"].includes(t) && !tipo?.disparador) add(n.id, "resultado del nodo");
  return v;
}

/** Los nodos que corren antes de este (todos los caminos que llegan aquí). */
export function ancestros(def: Definicion, id: string): string[] {
  const r: string[] = [];
  const pila = [id];
  const vistos = new Set([id]);
  while (pila.length) {
    const x = pila.pop()!;
    for (const a of def.aristas) {
      if (a.hacia === x && !vistos.has(a.desde)) {
        vistos.add(a.desde);
        r.push(a.desde);
        pila.push(a.desde);
      }
    }
  }
  return r;
}

/** ¿Se llega a este nodo por una rama de error? Entonces existe {{fallo.*}}. */
export function despuesDeError(def: Definicion, id: string): boolean {
  const previos = new Set([id, ...ancestros(def, id)]);
  return def.aristas.some((a) => a.cuando?.trim().toLowerCase() === RAMA_ERROR && previos.has(a.hacia));
}

export function variablesDisponibles(def: Definicion, tipos: Map<string, TipoNodo>, id: string): Variable[] {
  const vars: Variable[] = [];
  const previos = ancestros(def, id);
  // Del más cercano al disparador al más cercano a este nodo.
  const orden = def.nodos.filter((n) => previos.includes(n.id));
  for (const n of orden) for (const v of variablesDeNodo(n, tipos.get(n.tipo))) if (!vars.some((x) => x.nombre === v.nombre)) vars.push(v);
  if (despuesDeError(def, id)) {
    vars.push({ nombre: "fallo.motivo", origen: "Rama de error", pista: "por qué falló el nodo anterior" });
    vars.push({ nombre: "fallo.nodo", origen: "Rama de error", pista: "qué nodo falló" });
  }
  return vars;
}

// ----------------------------------------------------------------- creación

export function idNuevo(def: Definicion, base: string): string {
  const limpio = base.toLowerCase().normalize("NFD").replace(/\p{M}/gu, "").replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").replace(/^[^a-z]+/, "") || "nodo";
  const raiz = limpio.slice(0, 34);
  const usados = new Set(def.nodos.map((n) => n.id));
  if (!usados.has(raiz) && raiz.length >= 2) return raiz;
  for (let i = 2; ; i++) if (!usados.has(`${raiz}_${i}`)) return `${raiz}_${i}`;
}

export function slugFlujo(nombre: string, usados: string[]): string {
  const base = nombre.toLowerCase().normalize("NFD").replace(/\p{M}/gu, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[^a-z]+/, "").slice(0, 36) || "flujo";
  const b = base.length < 2 ? `${base}-flujo` : base;
  if (!usados.includes(b)) return b;
  for (let i = 2; ; i++) if (!usados.includes(`${b}-${i}`)) return `${b}-${i}`;
}

/** Valores iniciales de un esquema: los «default» y, para lo obligatorio, algo válido. */
export function valoresIniciales(e: Esquema | undefined): Record<string, unknown> {
  const r: Record<string, unknown> = {};
  for (const [k, p] of Object.entries(e?.properties ?? {})) {
    if (p.default !== undefined) r[k] = structuredClone(p.default);
    else if (e?.required?.includes(k) && p.enum?.length) r[k] = p.enum[0];
  }
  return r;
}

export function configInicial(t: TipoNodo): Record<string, unknown> {
  const ej = t.ejemplos?.[0];
  // Un ejemplo puede venir como la config sola o como un nodo completo.
  const conf = ej && typeof ej === "object" && "config" in ej && typeof ej.config === "object" ? (ej.config as Record<string, unknown>) : ej;
  return { ...valoresIniciales(t.config), ...(conf ? structuredClone(conf) : {}) };
}

// ------------------------------------------------------------------ disposición

export const ANCHO_NODO = 264;
const COLUMNA = 340;
const FILA = 214;

/** Capas de izquierda a derecha por el camino más largo; dentro de cada capa, por el orden de sus padres. */
export function autoOrdenar(def: Definicion, tipos: Map<string, TipoNodo>): Record<string, Posicion> {
  const ids = def.nodos.map((n) => n.id);
  const entrantes = new Map<string, string[]>(ids.map((i) => [i, []]));
  const salientes = new Map<string, string[]>(ids.map((i) => [i, []]));
  // Las aristas que vuelven atrás (bucles) no cuentan para las capas.
  const atras = new Set<number>();
  const estado = new Map<string, number>();
  const visitar = (x: string) => {
    estado.set(x, 1);
    def.aristas.forEach((a, i) => {
      if (a.desde !== x || !salientes.has(a.hacia)) return;
      if (estado.get(a.hacia) === 1) atras.add(i);
      else if (!estado.get(a.hacia)) visitar(a.hacia);
    });
    estado.set(x, 2);
  };
  const raices = def.nodos.filter((n) => tipos.get(n.tipo)?.disparador || !def.aristas.some((a) => a.hacia === n.id)).map((n) => n.id);
  for (const r of raices) if (!estado.get(r)) visitar(r);
  for (const i of ids) if (!estado.get(i)) visitar(i);
  def.aristas.forEach((a, i) => {
    if (atras.has(i) || !entrantes.has(a.hacia) || !salientes.has(a.desde)) return;
    entrantes.get(a.hacia)!.push(a.desde);
    salientes.get(a.desde)!.push(a.hacia);
  });
  const capa = new Map<string, number>();
  const calcular = (x: string, pila = new Set<string>()): number => {
    if (capa.has(x)) return capa.get(x)!;
    if (pila.has(x)) return 0;
    pila.add(x);
    const c = Math.max(-1, ...entrantes.get(x)!.map((p) => calcular(p, pila))) + 1;
    capa.set(x, c);
    return c;
  };
  ids.forEach((i) => calcular(i));
  const capas: string[][] = [];
  for (const i of ids) (capas[capa.get(i)!] ??= []).push(i);
  // Barycentro: cada nodo cerca de sus padres; el orden de las salidas se respeta.
  const fila = new Map<string, number>();
  capas.forEach((c, k) => {
    if (k > 0) {
      const peso = (x: string) => {
        const ps = def.aristas.map((a, i) => ({ a, i })).filter(({ a, i }) => a.hacia === x && !atras.has(i) && fila.has(a.desde));
        if (!ps.length) return 1e6;
        return ps.reduce((s, { a, i }) => s + fila.get(a.desde)! + i * 0.001, 0) / ps.length;
      };
      c.sort((x, y) => peso(x) - peso(y));
    }
    c.forEach((x, i) => fila.set(x, i));
  });
  const alto = Math.max(1, ...capas.map((c) => c?.length ?? 0));
  const pos: Record<string, Posicion> = {};
  capas.forEach((c, k) => {
    const corrimiento = ((alto - c.length) * FILA) / 2;
    c.forEach((x, i) => (pos[x] = { x: k * COLUMNA, y: corrimiento + i * FILA }));
  });
  return pos;
}

/** Un lugar libre a la derecha de un nodo, sin taparse con otro. */
export function lugarDespues(_def: Definicion, pos: Record<string, Posicion>, desde: string | null): Posicion {
  const base = desde ? pos[desde] : undefined;
  if (!base) {
    const xs = Object.values(pos);
    if (!xs.length) return { x: 0, y: 0 };
    return { x: Math.max(...xs.map((p) => p.x)) + COLUMNA, y: Math.min(...xs.map((p) => p.y)) };
  }
  const candidato = { x: base.x + COLUMNA, y: base.y };
  const ocupado = (p: Posicion) => Object.values(pos).some((q) => Math.abs(q.x - p.x) < ANCHO_NODO && Math.abs(q.y - p.y) < 120);
  while (ocupado(candidato)) candidato.y += FILA;
  return candidato;
}

// -------------------------------------------------------------------- resumen

const corto = (t: string, n = 64) => (t.length > n ? `${t.slice(0, n - 1)}…` : t);

/** Una línea que dice qué hace el nodo con su configuración actual. */
export function resumenConfig(n: NodoDef, tipo: TipoNodo | undefined): string {
  const c = n.config ?? {};
  if (n.tipo === "programado") {
    if (typeof c.cron === "string" && c.cron) return describirCron(c.cron);
    if (typeof c.cada_minutos === "number") return `cada ${c.cada_minutos} minuto${c.cada_minutos === 1 ? "" : "s"}`;
  }
  if (n.tipo === "salida" && c.desenlace) return `termina: ${String(c.desenlace)}${c.motivo ? ` · ${String(c.motivo)}` : ""}`;
  if (n.tipo === "http" && c.url) return `${String(c.metodo ?? "GET")} ${corto(String(c.url), 50)}`;
  if ((n.tipo === "filtro" || n.tipo === "condicion") && c.campo) return `${String(c.campo)} ${String(c.operador ?? "contiene")} «${corto(String(c.valor ?? ""), 24)}»`;
  const props = tipo?.config?.properties ?? {};
  const claves = [...Object.keys(props), ...Object.keys(c).filter((k) => !(k in props))];
  for (const k of claves) {
    const v = c[k];
    if (["deshabilitado", "continuar_si_falla", "reintentos", "entradas", "modelo", "temperatura"].includes(k)) continue;
    if (typeof v === "string" && v.trim()) return `${props[k]?.title ?? k.replace(/_/g, " ")}: ${corto(v.replace(/\s+/g, " "))}`;
    if (Array.isArray(v) && v.length) {
      const nombres = v.map(valorDe).filter(Boolean);
      return nombres.length ? corto(nombres.join(", ")) : `${v.length} ${(props[k]?.title ?? k).toLowerCase()}`;
    }
  }
  return tipo?.descripcion ? corto(tipo.descripcion, 70) : "Sin configurar";
}

// ----------------------------------------------------------------------- cron

const DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const dos = (n: string | number) => String(n).padStart(2, "0");

/** «0 7 * * *» → «cada día a las 7:00». Lo que no entiende, lo devuelve tal cual. */
export function describirCron(expr: string): string {
  const p = expr.trim().split(/\s+/);
  if (p.length !== 5) return expr.trim() ? `expresión «${expr.trim()}»` : "sin horario";
  const [mi, ho, dm, me, ds] = p as [string, string, string, string, string];
  const hora = /^\d+$/.test(mi) && /^\d+$/.test(ho) ? `${Number(ho)}:${dos(mi)}` : null;
  const cada = /^\*\/(\d+)$/.exec(mi);
  if (cada && ho === "*" && dm === "*" && me === "*" && ds === "*") return `cada ${cada[1]} minuto${cada[1] === "1" ? "" : "s"}`;
  if (mi === "*" && ho === "*" && dm === "*" && me === "*" && ds === "*") return "cada minuto";
  if (/^\d+$/.test(mi) && ho === "*" && dm === "*" && me === "*" && ds === "*") return `cada hora, en el minuto ${Number(mi)}`;
  const cadaH = /^\*\/(\d+)$/.exec(ho);
  if (/^\d+$/.test(mi) && cadaH && dm === "*" && me === "*" && ds === "*") return `cada ${cadaH[1]} horas, en el minuto ${Number(mi)}`;
  if (hora && dm === "*" && me === "*" && ds === "*") return `cada día a las ${hora}`;
  if (hora && dm === "*" && me === "*") {
    if (ds === "1-5") return `de lunes a viernes a las ${hora}`;
    if (ds === "0,6" || ds === "6,0") return `sábados y domingos a las ${hora}`;
    const dias = ds.split(",").map((d) => DIAS[Number(d) % 7]).filter(Boolean);
    if (dias.length) return `cada ${dias.length > 1 ? `${dias.slice(0, -1).join(", ")} y ${dias.at(-1)}` : dias[0]} a las ${hora}`;
  }
  if (hora && /^\d+$/.test(dm) && me === "*" && ds === "*") return `el día ${Number(dm)} de cada mes a las ${hora}`;
  return `expresión «${expr.trim()}»`;
}

export function textoJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2) ?? "";
  } catch {
    return String(v);
  }
}

/** Lo mínimo para dibujar bien un flujo mientras el catálogo no llega. */
export const TIPOS_MINIMOS: TipoNodo[] = [
  { tipo: "entrada_correo", nombre: "Entrada de correo", categoria: "entrada", descripcion: "", disparador: true, escritura: false, salidas: ["ok"], config: {}, ejemplos: [], consejos: [], salidas_opcionales: ["error"] },
  { tipo: "salida", nombre: "Salida", categoria: "salida", descripcion: "", disparador: false, escritura: false, salidas: [], config: {}, ejemplos: [], consejos: [], terminal: true, salidas_opcionales: [] },
];

/** Normaliza lo que devuelve /api/nodos/tipos, por si falta algún campo. */
export function normalizarTipos(x: unknown): TipoNodo[] {
  if (!Array.isArray(x)) return [];
  return x
    .filter((t): t is Record<string, unknown> => Boolean(t) && typeof t === "object" && typeof (t as Record<string, unknown>).tipo === "string")
    .map((t) => ({
      tipo: String(t.tipo),
      nombre: String(t.nombre ?? t.tipo),
      categoria: String(t.categoria ?? (t.disparador ? "disparador" : "datos")),
      descripcion: String(t.descripcion ?? ""),
      disparador: Boolean(t.disparador),
      escritura: Boolean(t.escritura),
      salidas: t.salidas === "dinamicas" ? "dinamicas" : Array.isArray(t.salidas) ? t.salidas.map(String) : ["ok"],
      config: (t.config && typeof t.config === "object" ? t.config : { type: "object", properties: {} }) as Esquema,
      ejemplos: Array.isArray(t.ejemplos) && t.ejemplos.length ? (t.ejemplos as Record<string, unknown>[]) : t.ejemplo && typeof t.ejemplo === "object" ? [t.ejemplo as Record<string, unknown>] : [],
      consejos: Array.isArray(t.consejos) ? t.consejos.map(String) : [],
      produce: typeof t.produce === "string" ? t.produce : undefined,
      salidas_desde: typeof t.salidas_desde === "string" ? t.salidas_desde : undefined,
      salidas_opcionales: Array.isArray(t.salidas_opcionales) ? t.salidas_opcionales.map(String) : undefined,
      terminal: typeof t.terminal === "boolean" ? t.terminal : undefined,
    }));
}
