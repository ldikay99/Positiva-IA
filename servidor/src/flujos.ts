import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { mkdir, readdir, readFile, unlink, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { z } from "zod";
import { config } from "./config.ts";
import { catalogo as catalogoDePqrd, type Catalogo } from "./catalogo.ts";

/**
 * Los flujos de la plataforma.
 *
 * - De código: un motor de verdad (PQRD, Tutelas) que el visor observa. Su
 *   estructura es la del código; desde aquí se ajustan sus parámetros.
 * - Declarativos: un JSON de nodos y aristas que se crea en la web o en el
 *   chat y que ejecuta `puente/flujo_declarativo.py`. Se guardan con versión.
 *
 * Cada flujo tiene sus propias corridas, su configuración y sus trabajadores:
 * nada de un flujo aparece en otro.
 */

/** Los tipos de nodo; su documentación está en `puente/nodos_declarativos.py`. */
export const TIPOS_DE_NODO = [
  "entrada_correo", "leer_adjuntos", "filtro", "condicion", "ia_clasificar", "ia_decidir", "ia_extraer", "extraer_regex", "validar",
  "transformar", "buscar_en_tabla", "bifurcar_por_valor", "ia_generar", "resumir", "ia_redactar", "notificar", "enviar", "http", "salida",
] as const;
export type TipoDeNodo = (typeof TIPOS_DE_NODO)[number];
const TIPOS_IA = new Set<string>(["ia_clasificar", "ia_decidir", "ia_extraer", "ia_generar", "resumir", "ia_redactar"]);
/** Los que hablan con algo de afuera y por eso aceptan reintentos. */
const TIPOS_CON_REINTENTOS = new Set<string>([...TIPOS_IA, "notificar", "enviar", "http"]);
/** Ni la entrada ni las salidas se pueden saltar. */
const NO_DESHABILITABLES = new Set<string>(["entrada_correo", "salida"]);
/** La rama que toma cualquier nodo cuando falla. */
const RAMA_ERROR = "error";

// ------------------------------------------------- config de cada tipo
//
// La forma de la config. Lo semántico (variables que existan antes, aristas por
// cada salida, ciclos) lo valida el motor en Python, que es quien ejecuta.

const RESERVADAS = ["correo", "adjuntos", "asunto", "cuerpo", "remitente", "texto", "fallo"];
const variable = z.string({ error: "tiene que ser un nombre de variable" })
  .regex(/^[A-Za-z_][A-Za-z0-9_]*$/, "nombre de variable: letras, números y guion bajo, sin espacios")
  .refine((x) => !RESERVADAS.includes(x), `nombre reservado (${RESERVADAS.join(", ")})`);
const textoReq = (que: string) => z.string({ error: `falta ${que}` }).trim().min(1, `falta ${que}`);
const booleano = z.boolean({ error: "tiene que ser true o false" });
const regex = (que: string) => z.string({ error: `falta ${que}` }).min(1, `falta ${que}`).refine((p) => {
  try { new RegExp(p.replace(/\(\?P</g, "(?<")); return true; } catch { return false; }
}, `${que}: expresión regular inválida`);
// Reintentos ante fallos pasajeros: intentos cuenta el primero (1 = sin reintentos).
const reintentos = z.looseObject({
  intentos: z.number({ error: "reintentos.intentos: entero entre 1 y 5" }).int("reintentos.intentos: entero entre 1 y 5").min(1, "reintentos.intentos: entero entre 1 y 5").max(5, "reintentos.intentos: entero entre 1 y 5"),
  espera_ms: z.number().int().min(0, "reintentos.espera_ms entre 0 y 60000").max(60000, "reintentos.espera_ms entre 0 y 60000").optional(),
  backoff: z.number().min(1, "reintentos.backoff entre 1 y 10").max(10, "reintentos.backoff entre 1 y 10").optional(),
  maximo_ms: z.number().int().min(0, "reintentos.maximo_ms entre 0 y 120000").max(120000, "reintentos.maximo_ms entre 0 y 120000").optional(),
}, { error: "reintentos tiene que ser un objeto {intentos, espera_ms, backoff, maximo_ms}" });
const comunes = {
  entradas: z.array(z.string().min(1), { error: "entradas tiene que ser una lista de nombres" }).optional(),
  salida: variable.optional(),
  modelo: z.string().min(1).optional(),
  temperatura: z.number({ error: "temperatura tiene que ser un número" }).min(0, "temperatura entre 0 y 2").max(2, "temperatura entre 0 y 2").optional(),
  deshabilitado: z.boolean({ error: "deshabilitado tiene que ser true o false" }).optional(),
  continuar_si_falla: z.boolean({ error: "continuar_si_falla tiene que ser true o false" }).optional(),
  reintentos: reintentos.optional(),
};
const conf = <T extends z.ZodRawShape>(forma: T) => z.looseObject({ ...comunes, ...forma });
// En la salida, «salida» no es una variable (el motor tampoco la revisa ahí).
const { salida: _salidaVariable, ...comunesDeSalida } = comunes;
const noEsError = (x: unknown) => (typeof x === "object" && x ? String((x as Record<string, unknown>).valor ?? "") : String(x)).trim().toLowerCase() !== RAMA_ERROR;
const valorOObjeto = (clave: string) => z.union([z.string().min(1), z.looseObject({ [clave]: z.string({ error: `cada elemento necesita ${clave}` }).min(1) })])
  .refine(noEsError, `«${RAMA_ERROR}» es la rama de fallo de todo nodo: usa otro valor`);

// Rutas dentro de una variable o de una respuesta: a.b, lista.0, lista[0], lista[campo=valor], datos["a.b"] (igual que en Python).
const CORCHETES = String.raw`\[\s*(?:"[^"]*"|'[^']*'|\d+|[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?:"[^"]*"|'[^']*'|[^\]"']*?))\s*\]`;
const RUTA = new RegExp(String.raw`^\s*(?:[A-Za-z0-9_]+|${CORCHETES})(?:\.[A-Za-z0-9_]+|${CORCHETES})*\s*$`);
const ENCABEZADOS_SECRETOS = ["authorization", "proxy-authorization", "cookie", "x-api-key", "api-key", "apikey", "x-auth-token"];
const METODOS_HTTP = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;
const OPERADOR = z.enum(["contiene", "coincide", "no_coincide", "igual", "vacio", "no_vacio"], { error: "operador: contiene, coincide, no_coincide, igual, vacio o no_vacio" });
const filtroConf = conf({ campo: textoReq("campo"), operador: OPERADOR.optional(), valor: z.unknown().optional() });
const OPS = ["plantilla", "asignar", "mayusculas", "minusculas", "titulo", "limpiar", "solo_digitos", "regex_extraer", "regex_reemplazar", "concatenar", "defecto"] as const;
const operacion = z.looseObject({
  op: z.enum(OPS, { error: `op: ${OPS.join(", ")}` }),
  salida: variable,
  de: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  plantilla: z.string().optional(),
  patron: regex("patron").optional(),
  reemplazo: z.string().optional(),
  separador: z.string().optional(),
  grupo: z.number().int().min(0).optional(),
});
const DESENLACES_DECL = ["radicar", "manual", "reenviar", "descartar", "responder", "duplicado"] as const;

export const CONFIG_DE: Record<TipoDeNodo, z.ZodType> = {
  entrada_correo: conf({ leer_adjuntos: booleano.optional(), ocr: booleano.optional() }),
  leer_adjuntos: conf({
    ocr: booleano.optional(), vision: booleano.optional(),
    tipos: z.array(z.string().regex(/^(pdf|imagen|docx|xlsx|texto|p7m|\.[a-z0-9]{1,6})$/i, "tipo: pdf, imagen, docx, xlsx, texto, p7m o una extensión como .pdf")).min(1).optional(),
    nombre_patron: regex("nombre_patron").optional(),
    max_paginas: z.number().int().min(1, "max_paginas entre 1 y 60").max(60, "max_paginas entre 1 y 60").optional(),
    max_caracteres: z.number().int().positive().optional(),
  }),
  filtro: filtroConf,
  condicion: filtroConf,
  ia_clasificar: conf({
    categorias: z.array(valorOObjeto("valor"), { error: "faltan categorias: [{valor, descripcion}]" }).min(1, "faltan categorias"),
    variable: variable.optional(), instrucciones: z.string().optional(),
  }).refine((c) => c.variable || c.salida, { message: "falta variable (o salida) donde dejar la categoría", path: ["variable"] }),
  ia_decidir: conf({
    pregunta: z.string().optional(), instrucciones: z.string().optional(),
    opciones: z.array(z.looseObject({ valor: textoReq("valor").refine(noEsError, `«${RAMA_ERROR}» es la rama de fallo de todo nodo: usa otro valor`), criterio: z.string().optional(), descripcion: z.string().optional() }), { error: "faltan opciones: [{valor, criterio}]" }).min(2, "al menos dos opciones"),
  }).refine((c) => c.pregunta || c.instrucciones, { message: "falta pregunta (o instrucciones)", path: ["pregunta"] }),
  ia_extraer: conf({
    instrucciones: z.string().optional(),
    campos: z.array(z.looseObject({
      nombre: variable, descripcion: z.string().optional(), obligatorio: z.boolean().optional(),
      tipo: z.enum(["texto", "numero", "fecha", "lista", "booleano"], { error: "tipo: texto, numero, fecha, lista o booleano" }).optional(),
    }), { error: "faltan campos: [{nombre, descripcion}]" }).min(1, "faltan campos"),
  }),
  extraer_regex: conf({
    de: z.string().optional(), patron: regex("patron").optional(),
    campos: z.array(z.looseObject({ nombre: variable, patron: regex("patron"), grupo: z.number().int().min(0).optional(), todos: z.boolean().optional(), obligatorio: z.boolean().optional() })).min(1).optional(),
  }).refine((c) => c.campos || (c.patron && c.salida), { message: "faltan campos: [{nombre, patron}] (o patron y salida)", path: ["campos"] }),
  validar: conf({
    reglas: z.array(z.looseObject({
      campo: textoReq("campo"),
      regla: z.enum(["obligatorio", "patron", "en_lista", "longitud"], { error: "regla: obligatorio, patron, en_lista o longitud" }).optional(),
      valor: z.unknown().optional(), codigo: z.string().optional(), mensaje: z.string().optional(),
    }), { error: "faltan reglas: [{campo, regla}]" }).min(1, "faltan reglas"),
  }),
  // Una lista de operaciones o, en corto, una sola operación en la propia config.
  transformar: z.looseObject({ ...comunes, operaciones: z.array(operacion, { error: "operaciones tiene que ser una lista [{op, salida, ...}]" }).min(1, "faltan operaciones").optional() })
    .superRefine((c, ctx) => {
      if (c.operaciones) return;
      if (!("op" in c)) return void ctx.addIssue({ code: "custom", path: ["operaciones"], message: "transformar necesita operaciones: [{op, salida, ...}]" });
      const r = operacion.safeParse(c);
      if (!r.success) for (const i of r.error.issues) ctx.addIssue({ code: "custom", path: i.path.map((p) => (typeof p === "symbol" ? String(p) : p)), message: i.message });
    }),
  buscar_en_tabla: conf({
    buscar: textoReq("buscar"), columna: textoReq("columna"),
    tabla: z.array(z.record(z.string(), z.unknown())).min(1, "la tabla está vacía").optional(),
    archivo: z.string().regex(/^[^:]+\.csv$/i, "archivo: ruta relativa a un .csv del repositorio").refine((r) => !r.split(/[\\/]/).includes(".."), "archivo: sin «..»").optional(),
    traer: z.array(z.string().min(1)).optional(),
    modo: z.enum(["igual", "contiene"], { error: "modo: igual o contiene" }).optional(),
  }).refine((c) => c.tabla || c.archivo, { message: "falta tabla (filas embebidas) o archivo (CSV del repositorio)", path: ["tabla"] }),
  bifurcar_por_valor: conf({
    variable: textoReq("variable"),
    casos: z.array(valorOObjeto("valor"), { error: "faltan casos: [valores]" }).min(1, "faltan casos"),
  }),
  ia_generar: conf({
    instrucciones: textoReq("instrucciones"),
    formato: z.enum(["texto", "json"], { error: "formato: texto o json" }).optional(),
    esquema: z.record(z.string(), z.unknown(), { error: "esquema tiene que ser un objeto" }).optional(),
    mensaje: z.string().optional(),
  }),
  resumir: conf({ longitud: z.string().optional(), enfoque: z.string().optional(), instrucciones: z.string().optional() }),
  ia_redactar: conf({ instrucciones: z.string().optional(), variable: variable.optional() }),
  notificar: conf({ para: textoReq("para"), cuerpo: textoReq("cuerpo"), asunto: z.string().optional(), cc: z.string().optional(), canal: z.string().optional() }),
  enviar: conf({ cuerpo: textoReq("cuerpo"), para: z.string().optional(), asunto: z.string().optional(), cc: z.string().optional() }),
  // Solo los GET a hosts de HTTP_HOSTS_PERMITIDOS salen de verdad; lo demás se arma y queda como escritura bloqueada.
  http: conf({
    metodo: z.enum(METODOS_HTTP, { error: `metodo: ${METODOS_HTTP.join(", ")}` }).optional(),
    url: textoReq("url").refine((u) => u.trim().startsWith("{{") || /^https?:\/\/[^\s/]/i.test(u.trim()), "url tiene que empezar por http:// o https://"),
    query: z.record(z.string(), z.string(), { error: "query tiene que ser un objeto {nombre: plantilla}" }).optional(),
    encabezados: z.record(z.string(), z.string(), { error: "encabezados tiene que ser un objeto {nombre: plantilla}" })
      .refine((e) => !Object.keys(e).some((k) => ENCABEZADOS_SECRETOS.includes(k.trim().toLowerCase())), "encabezados: Authorization y las claves no van en el flujo; usa credencial con el nombre de la variable de entorno")
      .optional(),
    cuerpo: z.union([z.string(), z.record(z.string(), z.unknown()), z.array(z.unknown())], { error: "cuerpo tiene que ser texto (plantilla) o un objeto JSON con plantillas" }).optional(),
    credencial: z.string().regex(/^[A-Z][A-Z0-9_]{2,63}$/, "credencial es el NOMBRE de una variable de entorno (p. ej. HTTP_TOKEN_X), nunca el valor").optional(),
    timeout_ms: z.number({ error: "timeout_ms tiene que ser un entero entre 100 y 60000" }).int().min(100, "timeout_ms entre 100 y 60000").max(60000, "timeout_ms entre 100 y 60000").optional(),
    mapeo: z.array(z.looseObject({
      variable, ruta: z.string({ error: "falta ruta" }).regex(RUTA, "ruta inválida (p. ej. «items.0.id», «items[estado=activo].id», «[\"a.b\"]»)"),
      defecto: z.unknown().optional(), obligatorio: booleano.optional(),
    }), { error: "mapeo tiene que ser una lista [{variable, ruta, defecto, obligatorio}]" })
      .refine((m) => new Set(m.map((x) => x.variable)).size === m.length, "mapeo: hay variables repetidas").optional(),
  }),
  salida: z.looseObject({
    ...comunesDeSalida,
    desenlace: z.enum(DESENLACES_DECL, { error: `desenlace: ${DESENLACES_DECL.join(", ")}` }),
    motivo: z.string().optional(), destino: z.string().optional(), plantilla_respuesta: z.string().optional(),
    campos: z.union([z.array(z.string().min(1)), z.record(z.string(), z.string())], { error: "campos: lista de variables o {nombre: plantilla}" }).optional(),
  }),
};

const nodo = z.object({
  id: z.string().regex(/^[a-z][a-z0-9_]{1,40}$/, "id en minúsculas, sin espacios"),
  tipo: z.enum(TIPOS_DE_NODO, { error: `tipo desconocido (válidos: ${TIPOS_DE_NODO.join(", ")})` }),
  nombre: z.string().min(1).max(80),
  descripcion: z.string().max(600).optional(),
  // La IA a veces manda null por «sin configuración».
  config: z.record(z.string(), z.unknown()).nullish().transform((v) => v ?? {}),
}).superRefine((n, ctx) => {
  const r = CONFIG_DE[n.tipo].safeParse(n.config);
  if (!r.success) for (const i of r.error.issues) ctx.addIssue({ code: "custom", path: ["config", ...i.path.map((p) => (typeof p === "symbol" ? String(p) : p))], message: `${n.id} (${n.tipo}): ${i.message}` });
  // Lo común que depende del tipo (igual que en el motor).
  const c = n.config as Record<string, unknown>;
  const problema = (clave: string, m: string) => ctx.addIssue({ code: "custom", path: ["config", clave], message: `${n.id} (${n.tipo}): ${m}` });
  if (c.deshabilitado === true && NO_DESHABILITABLES.has(n.tipo)) problema("deshabilitado", `un nodo ${n.tipo} no se puede deshabilitar`);
  if (c.continuar_si_falla === true && n.tipo === "salida") problema("continuar_si_falla", "una salida no puede continuar si falla: no tiene por dónde seguir");
  if (c.reintentos !== undefined && !TIPOS_CON_REINTENTOS.has(n.tipo)) problema("reintentos", "reintentos solo aplica a los nodos de IA, enviar, notificar y http");
});

export const definicion = z.object({
  id: z.string().regex(/^[a-z][a-z0-9-]{1,40}$/, "id en minúsculas con guiones"),
  nombre: z.string().min(2).max(80),
  descripcion: z.string().max(800).nullish().transform((v) => v ?? ""),
  modelo: z.string().default("gemini-2.5-flash"),
  nodos: z.array(nodo).min(2).max(60),
  aristas: z.array(z.object({ desde: z.string(), hacia: z.string(), cuando: z.string().optional() })).max(200),
});
export type Definicion = z.infer<typeof definicion>;

export interface Flujo {
  id: string;
  nombre: string;
  descripcion: string;
  tipo: "codigo" | "declarativo";
  /** El trabajador de Python que lo ejecuta. */
  trabajador: string;
  /** Si sabe leer el buzón real (solo PQRD por ahora). */
  buzonReal: boolean;
  /** Si corre varios correos con un solo orquestador, como el job. */
  lote: boolean;
  version?: number;
  actualizado?: number;
  definicion?: Definicion;
}

const raiz = config.raiz;
const carpetaDeclarativos = () => join(config.datos, "flujos");

const DE_CODIGO: Flujo[] = [
  {
    id: "pqrd", nombre: "Radicación de PQRD", tipo: "codigo", buzonReal: true, lote: true,
    descripcion: "Correos de servicioalcliente@positiva.gov.co: deduplica, lee adjuntos con OCR y visión, extrae los datos con algoritmo e IA, valida con la compuerta y el revisor, y radica en SGDEA.",
    trabajador: config.trabajador,
  },
  {
    id: "tutelas", nombre: "Radicación de tutelas", tipo: "codigo", buzonReal: true, lote: false,
    descripcion: "Notificaciones judiciales: valida que el remitente sea un juzgado, detecta tutela o actuación, encuentra el radicado y el juzgado, exige anexos, deduplica y radica en SGDEA.",
    trabajador: resolve(raiz, "puente", "trabajador_tutelas.py"),
  },
];

const declarativos = new Map<string, Flujo>();

export async function cargar(): Promise<void> {
  await mkdir(carpetaDeclarativos(), { recursive: true });
  for (const a of (await readdir(carpetaDeclarativos())).filter((x) => x.endsWith(".json"))) {
    try {
      const guardado = JSON.parse(await readFile(join(carpetaDeclarativos(), a), "utf8")) as { definicion: unknown; version: number; actualizado: number };
      const d = definicion.parse(guardado.definicion);
      declarativos.set(d.id, aFlujo(d, guardado.version, guardado.actualizado));
    } catch {
      /* archivo dañado: se ignora */
    }
  }
}

function aFlujo(d: Definicion, version: number, actualizado: number): Flujo {
  return {
    id: d.id, nombre: d.nombre, descripcion: d.descripcion, tipo: "declarativo", buzonReal: false, lote: false,
    trabajador: resolve(raiz, "puente", "flujo_declarativo.py"), version, actualizado, definicion: d,
  };
}

export function listar(): Flujo[] {
  return [...DE_CODIGO.filter((f) => existsSync(f.trabajador)), ...[...declarativos.values()].sort((a, b) => a.nombre.localeCompare(b.nombre))];
}

export function obtener(id: string): Flujo | undefined {
  return listar().find((f) => f.id === id);
}

/** Los problemas de una definición, con el mismo validador que la ejecuta. */
export function validar(entrada: unknown): Promise<{ ok: boolean; problemas: string[]; definicion?: Definicion }> {
  const r = definicion.safeParse(entrada);
  if (!r.success) return Promise.resolve({ ok: false, problemas: r.error.issues.map((i) => `${i.path.join(".") || "flujo"}: ${i.message}`) });
  return new Promise((resolver) => {
    const p = spawn(config.python, [resolve(raiz, "puente", "flujo_declarativo.py"), "validar"], { stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let salida = "";
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.on("exit", () => {
      try {
        const problemas = JSON.parse(salida.trim().split("\n").pop() ?? "[]") as string[];
        resolver({ ok: problemas.length === 0, problemas, definicion: r.data });
      } catch {
        resolver({ ok: false, problemas: ["no se pudo validar el flujo"], definicion: r.data });
      }
    });
    p.stdin.end(JSON.stringify(r.data));
  });
}

/** Guarda una definición válida (crea o reemplaza) y deja la versión anterior. */
export async function guardar(entrada: unknown): Promise<{ ok: boolean; problemas: string[]; flujo?: Flujo }> {
  const v = await validar(entrada);
  if (!v.ok || !v.definicion) return { ok: false, problemas: v.problemas };
  if (DE_CODIGO.some((f) => f.id === v.definicion!.id)) return { ok: false, problemas: [`«${v.definicion.id}» es un flujo de código: elige otro id`] };
  const previo = declarativos.get(v.definicion.id);
  const version = (previo?.version ?? 0) + 1;
  const actualizado = Date.now();
  await mkdir(join(carpetaDeclarativos(), "versiones"), { recursive: true });
  if (previo?.definicion) await writeFile(join(carpetaDeclarativos(), "versiones", `${previo.id}.v${previo.version}.json`), JSON.stringify(previo.definicion, null, 2), "utf8");
  await writeFile(join(carpetaDeclarativos(), `${v.definicion.id}.json`), JSON.stringify({ definicion: v.definicion, version, actualizado }, null, 2), "utf8");
  const f = aFlujo(v.definicion, version, actualizado);
  declarativos.set(f.id, f);
  return { ok: true, problemas: [], flujo: f };
}

export async function borrar(id: string): Promise<boolean> {
  if (!declarativos.has(id)) return false;
  declarativos.delete(id);
  await unlink(join(carpetaDeclarativos(), `${id}.json`)).catch(() => undefined);
  return true;
}

// ------------------------------------------------------------------ catálogos

const CATEGORIA: Record<TipoDeNodo, string> = {
  entrada_correo: "entrada", leer_adjuntos: "ocr", filtro: "filtro", condicion: "ruta", ia_clasificar: "ia", ia_decidir: "ia",
  ia_extraer: "ia", extraer_regex: "busqueda", validar: "validacion", transformar: "datos", buscar_en_tabla: "datos",
  bifurcar_por_valor: "ruta", ia_generar: "ia", resumir: "ia", ia_redactar: "ia", notificar: "integracion", enviar: "integracion",
  http: "integracion", salida: "salida",
};
const NOMBRE_TIPO: Record<TipoDeNodo, string> = {
  entrada_correo: "Entrada de correo", leer_adjuntos: "Leer adjuntos", filtro: "Filtro", condicion: "Condición",
  ia_clasificar: "IA · clasificar", ia_decidir: "IA · decidir", ia_extraer: "IA · extraer datos", extraer_regex: "Extraer con regex",
  validar: "Validación", transformar: "Transformar", buscar_en_tabla: "Buscar en tabla", bifurcar_por_valor: "Bifurcar por valor",
  ia_generar: "IA · generar", resumir: "IA · resumir", ia_redactar: "IA · redactar", notificar: "Notificar (simulado)",
  enviar: "Enviar respuesta (simulado)", http: "Petición HTTP", salida: "Salida",
};

const valorDe = (x: unknown, clave = "valor") => (typeof x === "object" && x ? String((x as Record<string, unknown>)[clave] ?? "") : String(x));
const lista = (x: unknown) => (Array.isArray(x) ? (x as unknown[]) : []);
const corto = (x: unknown, n = 160) => { const t = typeof x === "string" ? x : JSON.stringify(x ?? ""); return t.length > n ? `${t.slice(0, n)}…` : t; };

function describirConfig(n: Definicion["nodos"][number], aristas: Definicion["aristas"] = []): { id: string; descripcion: string }[] {
  const c = n.config as Record<string, unknown>;
  const r: { id: string; descripcion: string }[] = [];
  const add = (d: string) => r.push({ id: `${n.id}_${r.length + 1}`, descripcion: d });
  const ia = TIPOS_IA.has(n.tipo);
  if (Array.isArray(c.entradas) && c.entradas.length) add(`Recibe: ${(c.entradas as string[]).join(", ")}`);
  else if (ia) add("Recibe: el correo, los adjuntos y las variables obtenidas");
  if (typeof c.instrucciones === "string" && c.instrucciones) add(`Instrucciones a la IA: ${c.instrucciones}`);
  switch (n.tipo) {
    case "entrada_correo":
      add(`Lee asunto, cuerpo y remitente${c.leer_adjuntos === false ? " (los adjuntos se leen aparte o no se leen)" : `, y los adjuntos${c.ocr === false ? " sin OCR" : " con OCR y visión"}`}`);
      break;
    case "leer_adjuntos":
      add(`Lee ${lista(c.tipos).length ? `solo ${lista(c.tipos).join(", ")}` : "todos los adjuntos"}${c.nombre_patron ? ` cuyo nombre coincide con «${String(c.nombre_patron)}»` : ""}`);
      add(`${c.ocr === false ? "Sin OCR (solo el texto propio)" : c.vision === false ? "Con OCR de tesseract, sin visión" : "Con OCR y visión del modelo"} · hasta ${String(c.max_paginas ?? 60)} páginas por PDF`);
      add("Sale por «ok» o «sin_adjuntos»");
      break;
    case "filtro":
    case "condicion":
      add(`${String(c.campo)} ${String(c.operador ?? "contiene")} «${String(c.valor ?? "")}»: sale por «si» o «no»`);
      break;
    case "ia_clasificar":
      add(`Categorías: ${lista(c.categorias).map((x) => valorDe(x)).join(", ")} → variable «${String(c.salida ?? c.variable ?? n.id)}»`);
      break;
    case "ia_decidir":
      if (c.pregunta) add(`Pregunta: ${String(c.pregunta)}`);
      for (const o of lista(c.opciones)) add(`«${valorDe(o)}» si ${valorDe(o, "criterio") || valorDe(o, "descripcion")}`);
      add(`Deja la decisión en «${String(c.salida ?? n.id)}» y la justificación en «${String(c.salida ?? n.id)}_justificacion»`);
      break;
    case "ia_extraer":
    case "extraer_regex":
      if (n.tipo === "extraer_regex") add(`Busca en: ${String(c.de ?? "asunto, cuerpo y adjuntos")}`);
      for (const f of lista(c.campos) as { nombre: string; descripcion?: string; obligatorio?: boolean; patron?: string }[])
        add(`Campo «${f.nombre}»${f.obligatorio ? " (obligatorio)" : ""}: ${f.patron ? `/${f.patron}/` : (f.descripcion ?? "")}`);
      if (n.tipo === "extraer_regex" && c.patron) add(`«${String(c.salida)}»: /${String(c.patron)}/`);
      if (n.tipo === "ia_extraer" && c.salida) add(`Además deja todos los campos juntos en «${String(c.salida)}»`);
      break;
    case "validar":
      for (const x of lista(c.reglas) as { campo: string; regla?: string; valor?: unknown; mensaje?: string }[])
        add(`${x.campo}: ${x.regla ?? "obligatorio"}${x.valor !== undefined ? ` ${JSON.stringify(x.valor)}` : ""}${x.mensaje ? ` — ${x.mensaje}` : ""}`);
      break;
    case "transformar":
      for (const o of (Array.isArray(c.operaciones) ? c.operaciones : [c]) as Record<string, unknown>[])
        add(`«${String(o.salida)}» ← ${String(o.op)}${o.de ? ` de ${corto(o.de)}` : ""}${o.plantilla ? ` «${corto(o.plantilla)}»` : ""}${o.patron ? ` /${String(o.patron)}/` : ""}${o.valor !== undefined ? ` = ${corto(o.valor)}` : ""}`);
      break;
    case "buscar_en_tabla":
      add(`Busca ${String(c.buscar)} en la columna «${String(c.columna)}» de ${c.archivo ? String(c.archivo) : `una tabla de ${lista(c.tabla).length} filas`} (${String(c.modo ?? "igual")})`);
      add(`Trae ${lista(c.traer).length ? lista(c.traer).join(", ") : "todas las columnas"} → «${String(c.salida ?? n.id)}» · sale por «encontrado» o «no_encontrado»`);
      break;
    case "bifurcar_por_valor":
      add(`Según ${String(c.variable)}: ${lista(c.casos).map((x) => valorDe(x)).join(", ")} u «otro»`);
      break;
    case "ia_generar":
      add(`Genera ${c.esquema || c.formato === "json" ? "JSON" : "texto"} → «${String(c.salida ?? n.id)}»${c.temperatura !== undefined ? ` · temperatura ${String(c.temperatura)}` : ""}${c.modelo ? ` · ${String(c.modelo)}` : ""}`);
      if (c.esquema) add(`Esquema: ${corto(c.esquema, 300)}`);
      break;
    case "resumir":
      add(`Resume en ${String(c.longitud ?? "un párrafo breve")}${c.enfoque ? `, priorizando ${String(c.enfoque)}` : ""} → «${String(c.salida ?? "resumen")}»`);
      break;
    case "ia_redactar":
      add(`Redacta → «${String(c.salida ?? c.variable ?? "respuesta")}»`);
      break;
    case "notificar":
    case "enviar":
      add(`Para: ${String(c.para ?? "{{correo.remitente_correo}}")}${c.cc ? ` · cc ${String(c.cc)}` : ""} · asunto: ${String(c.asunto ?? (n.tipo === "enviar" ? "RE: {{correo.asunto}}" : ""))}`);
      add(`Cuerpo: ${corto(c.cuerpo, 300)}`);
      add("Simulado: se arma el mensaje y queda como escritura bloqueada, nunca se envía");
      break;
    case "http": {
      const metodo = String(c.metodo ?? "GET");
      const query = c.query && typeof c.query === "object" ? Object.keys(c.query as object) : [];
      add(`${metodo} ${String(c.url)}${query.length ? ` · query ${query.join(", ")}` : ""} · espera hasta ${String(c.timeout_ms ?? 15000)} ms`);
      if (c.credencial) add(`Credencial: Bearer desde la variable de entorno ${String(c.credencial)} (el valor nunca queda en el flujo)`);
      if (c.cuerpo !== undefined) add(`Cuerpo: ${corto(c.cuerpo, 300)}${metodo === "GET" ? " (no se envía en GET)" : ""}`);
      add(metodo === "GET"
        ? "Sale de verdad solo si el host está en HTTP_HOSTS_PERMITIDOS (nunca a IP privadas ni de metadatos, sin seguir redirecciones); si no, se simula"
        : "Simulado: la petición se arma y queda como escritura bloqueada, nunca se envía");
      for (const m of lista(c.mapeo) as { variable: string; ruta: string; defecto?: unknown; obligatorio?: boolean }[])
        add(`«${m.variable}» ← ${m.ruta}${m.obligatorio ? " (obligatorio)" : ""}${m.defecto !== undefined ? ` · si no está: ${corto(m.defecto, 60)}` : ""}`);
      add(`Deja la respuesta en «${String(c.salida ?? n.id)}» · sale por «ok» o «error_http» (status ≥ 400)`);
      break;
    }
    case "salida":
      add(`Desenlace: ${String(c.desenlace)}${c.motivo ? ` · motivo ${String(c.motivo)}` : ""}${c.destino ? ` · destino ${String(c.destino)}` : ""}`);
      if (c.campos) add(`Entrega: ${Array.isArray(c.campos) ? (c.campos as string[]).join(", ") : Object.keys(c.campos as object).join(", ")}`);
      if (c.plantilla_respuesta) add(`Respuesta: ${corto(c.plantilla_respuesta, 300)}`);
      break;
  }
  if (c.salida && !["ia_clasificar", "ia_decidir", "ia_extraer", "extraer_regex", "transformar", "buscar_en_tabla", "ia_generar", "resumir", "ia_redactar", "http", "salida"].includes(n.tipo)) add(`Deja su resultado en «${String(c.salida)}»`);
  // Lo común a todo nodo: reintentos, qué hace si falla y si está apagado.
  const rt = c.reintentos as { intentos?: number; espera_ms?: number; backoff?: number; maximo_ms?: number } | undefined;
  if (rt && typeof rt === "object" && (rt.intentos ?? 1) > 1)
    add(`Reintenta hasta ${String(rt.intentos)} veces en total ante fallos pasajeros (429, 5xx, tiempo, conexión) · espera ${String(rt.espera_ms ?? 500)} ms × ${String(rt.backoff ?? 2)}, tope ${String(rt.maximo_ms ?? 10000)} ms, al azar`);
  if (n.tipo !== "salida") {
    if (aristas.some((a) => a.desde === n.id && (a.cuando ?? "").trim().toLowerCase() === RAMA_ERROR)) add("Si falla, sigue por la rama «error» con {{fallo.motivo}}");
    else if (c.continuar_si_falla === true) add("Si falla, continúa por su arista normal y lo anota como aviso");
  }
  if (c.deshabilitado === true) add("Deshabilitado: no se ejecuta, el flujo pasa de largo por su arista por defecto");
  return r;
}

/** El catálogo con que el lienzo dibuja un flujo declarativo: nodos, aristas y fases por profundidad. */
export function catalogoDeclarativo(d: Definicion): Catalogo & { fases: { id: string; nombre: string; nodos: string[] }[] } {
  const profundidad = new Map<string, number>();
  const entrada = d.nodos.find((n) => n.tipo === "entrada_correo")?.id ?? d.nodos[0]!.id;
  const cola: [string, number][] = [[entrada, 0]];
  while (cola.length) {
    const [id, p] = cola.shift()!;
    if ((profundidad.get(id) ?? -1) >= p || p > 40) continue;
    profundidad.set(id, p);
    for (const a of d.aristas.filter((x) => x.desde === id)) cola.push([a.hacia, p + 1]);
  }
  const columnas: string[][] = [];
  for (const n of d.nodos) (columnas[profundidad.get(n.id) ?? 0] ??= []).push(n.id);
  // Cada columna se llama por lo que hacen sus nodos ("IA · clasificar", "Validación").
  const fases = columnas.filter(Boolean).map((ids, i) => {
    const tipos = [...new Set(ids.map((x) => d.nodos.find((n) => n.id === x)?.tipo ?? ""))];
    const nombre = i === 0 ? "Entrada" : tipos.every((t) => t === "salida") ? "Salidas" : tipos.map((t) => (NOMBRE_TIPO as Record<string, string>)[t] ?? t).join(" y ");
    return { id: `paso-${i + 1}`, nombre, nodos: ids };
  });
  return {
    nodos: d.nodos.map((n) => ({
      id: n.id, nombre: n.nombre, descripcion: n.descripcion || NOMBRE_TIPO[n.tipo] || n.tipo, etapa_cronometro: n.id,
      usa_ia: TIPOS_IA.has(n.tipo), categoria: CATEGORIA[n.tipo], tipo_declarativo: n.tipo, config: n.config,
      siguientes: d.aristas.filter((a) => a.desde === n.id).map((a) => a.hacia),
      desenlaces: n.tipo === "salida" ? [{ accion: String((n.config as { desenlace?: string }).desenlace ?? ""), cuando: "al llegar aquí" }] : [],
      reglas: describirConfig(n, d.aristas), parametros: [],
    })) as Catalogo["nodos"],
    aristas: d.aristas.map((a) => ({ desde: a.desde, hacia: a.hacia, etiqueta: a.cuando && a.cuando !== "*" ? a.cuando : undefined })),
    herramientas_agente: [], proveedores: [{ id: "vertex", nombre: `Vertex AI (${d.modelo})`, tipo: "ia" }],
    desenlaces: [...new Set(d.nodos.filter((n) => n.tipo === "salida").map((n) => String((n.config as { desenlace?: string }).desenlace)))].map((a) => ({ accion: a, descripcion: a })),
    variables_entorno: [], fases,
  };
}

let catalogoTutelas: Catalogo | null = null;

export function catalogoDe(id: string): Catalogo | null {
  const f = obtener(id);
  if (!f) return null;
  if (f.id === "pqrd") return catalogoDePqrd();
  if (f.id === "tutelas") {
    const ruta = resolve(raiz, "catalogo", "tutelas.json");
    if (!catalogoTutelas && existsSync(ruta)) catalogoTutelas = JSON.parse(readFileSync(ruta, "utf8")) as Catalogo;
    return catalogoTutelas;
  }
  return f.definicion ? catalogoDeclarativo(f.definicion) : null;
}
