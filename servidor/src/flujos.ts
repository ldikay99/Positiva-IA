import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { mkdir, readdir, readFile, unlink, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { z } from "zod";
import { config } from "./config.ts";
import { ajustables, catalogo as catalogoDePqrd, type Catalogo } from "./catalogo.ts";
import * as credenciales from "./credenciales.ts";
import * as entornos from "./entornos.ts";
import * as ia from "./ia.ts";
import { PRODUCCION, LECTURAS_EN_SIMULACION } from "./produccion.ts";
import * as compuerta from "./compuerta.ts";
import * as espacio from "./espacio.ts";
import * as descargas from "./descargas.ts";
import { programacionDe, estado as estadoDelProgramador, enlace as programadorEnlace } from "./programador.ts";
import type { Corrida } from "./motor.ts";

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
  // disparadores (exactamente uno por flujo)
  "entrada_correo", "entrada_manual", "programado", "webhook", "correo_nuevo", "entrada_archivos",
  // lectura, lógica, IA y datos
  "leer_adjuntos", "filtro", "condicion", "ia_clasificar", "ia_decidir", "ia_extraer", "extraer_regex", "validar",
  "transformar", "buscar_en_tabla", "bifurcar_por_valor", "ia_generar", "resumir", "ia_redactar",
  "para_cada", "fin_ciclo", "paralelo", "unir", "esperar", "expresion", "json", "fecha", "llamar_flujo",
  "tabla_guardar", "tabla_consultar", "archivo_leer", "archivo_guardar",
  // conectores
  "correo_leer", "correo_enviar", "drive_listar", "drive_leer", "drive_subir", "hoja_leer", "sql_consulta", "sql_ejecutar",
  // integración y fin
  "notificar", "enviar", "http", "respuesta_webhook", "salida",
  // de producto (puente/nodos_producto.py): aprobación, documentos, datos personales, hojas, mensajería, IA, datos y control
  "aprobacion_humana", "documento_generar", "pdf_unir", "pdf_dividir", "documento_convertir", "documento_clasificar", "documento_comparar",
  "anonimizar", "hoja_escribir", "mensaje_teams", "mensaje_slack", "webhook_saliente", "traducir", "resumir_largo", "preguntar_documentos",
  "extraer_tablas", "deduplicar", "agrupar", "ordenar_filtrar", "mapear", "validar_contra", "subflujo", "reintentar_mas_tarde", "limite_tasa",
  "ventana_horario", "detener_con_alerta",
  // archivos que salen: responder el correo en su hilo (con adjuntos) e imágenes generadas por IA
  "responder_correo", "ia_imagen",
] as const;
export type TipoDeNodo = (typeof TIPOS_DE_NODO)[number];
export const DISPARADORES = new Set<string>(["entrada_correo", "entrada_manual", "programado", "webhook", "correo_nuevo", "entrada_archivos"]);
/** Los disparadores de correo: solo los flujos que arrancan con ellos tienen «Correspondencia». */
export const DISPARADORES_DE_CORREO = new Set<string>(["entrada_correo", "correo_nuevo"]);
const CONECTORES = ["correo_leer", "correo_enviar", "drive_listar", "drive_leer", "drive_subir", "hoja_leer", "sql_consulta", "sql_ejecutar"];
const TIPOS_IA = new Set<string>(["ia_clasificar", "ia_decidir", "ia_extraer", "ia_generar", "resumir", "ia_redactar",
  "traducir", "resumir_largo", "preguntar_documentos", "extraer_tablas", "documento_clasificar", "documento_comparar"]);
/** Los que hablan con algo de afuera y por eso aceptan reintentos. */
const TIPOS_CON_REINTENTOS = new Set<string>([...TIPOS_IA, "notificar", "enviar", "http", "llamar_flujo", ...CONECTORES,
  "hoja_escribir", "mensaje_teams", "mensaje_slack", "webhook_saliente", "subflujo", "validar_contra", "responder_correo", "ia_imagen"]);
/** Los que escriben afuera: solo con una credencial cuya escritura habilitó una persona (notificar y enviar nunca). */
export const TIPOS_ESCRITURA = new Set<string>(["correo_enviar", "drive_subir", "sql_ejecutar", "http", "enviar", "notificar",
  "hoja_escribir", "mensaje_teams", "mensaje_slack", "webhook_saliente", "responder_correo"]);
/** Ni los disparadores, ni las salidas, ni la estructura de bucles y ramas se pueden saltar. */
const NO_DESHABILITABLES = new Set<string>([...DISPARADORES, "salida", "fin_ciclo", "para_cada", "paralelo", "unir", "detener_con_alerta"]);
const TERMINALES = new Set<string>(["salida", "fin_ciclo", "detener_con_alerta"]);
/** La rama que toma cualquier nodo cuando falla. */
const RAMA_ERROR = "error";

// ------------------------------------------------- config de cada tipo
//
// La forma de la config. Lo semántico (variables que existan antes, aristas por
// cada salida, ciclos) lo valida el motor en Python, que es quien ejecuta.

const RESERVADAS = ["correo", "adjuntos", "asunto", "cuerpo", "remitente", "texto", "fallo", "entorno"];
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
// Búsqueda robusta (igual que puente/conectores/busqueda.py): operadores de texto, números, fechas y longitud;
// por defecto sin distinguir mayúsculas, tildes ni espacios de más.
const OPERADORES = ["contiene", "no_contiene", "igual", "distinto", "empieza_con", "termina_con", "palabra", "contiene_alguna", "contiene_todas",
  "en_lista", "no_en_lista", "parecido", "coincide", "no_coincide", "vacio", "no_vacio", "mayor", "menor", "mayor_igual", "menor_igual", "entre",
  "antes_de", "despues_de", "entre_fechas", "longitud_igual", "longitud_mayor", "longitud_menor", "longitud_entre"] as const;
const OPERADOR = z.enum(OPERADORES, { error: `operador: ${OPERADORES.join(", ")}` });
const OPS_CON_VALOR = new Set<string>(["no_contiene", "igual", "distinto", "empieza_con", "termina_con", "palabra", "parecido", "coincide", "no_coincide",
  "mayor", "menor", "mayor_igual", "menor_igual", "antes_de", "despues_de", "longitud_igual", "longitud_mayor", "longitud_menor"]);
const OPS_CON_LISTA = new Set<string>(["contiene_alguna", "contiene_todas", "en_lista", "no_en_lista"]);
const OPS_CON_RANGO = new Set<string>(["entre", "entre_fechas", "longitud_entre"]);
const CONTAR = z.enum(["caracteres", "digitos", "letras", "palabras"], { error: "contar: caracteres, digitos, letras o palabras" });
const opcionesBusqueda = {
  comparacion: z.enum(["flexible", "exacta", "parecida"], { error: "comparacion: flexible, exacta o parecida" }).optional(),
  umbral: z.number({ error: "umbral tiene que ser un número entre 0 y 1" }).min(0, "umbral entre 0 y 1").max(1, "umbral entre 0 y 1").optional(),
  distinguir_mayusculas: booleano.optional(), distinguir_tildes: booleano.optional(), distinguir_espacios: booleano.optional(),
};
const longitudCampo = {
  longitud: z.number().int().min(0, "longitud: entero de 0 en adelante").optional(),
  longitud_min: z.number().int().min(0, "longitud_min: entero de 0 en adelante").optional(),
  longitud_max: z.number().int().min(0, "longitud_max: entero de 0 en adelante").optional(),
  contar: CONTAR.optional(), solo_digitos: booleano.optional(),
};
const minMaxOk = (c: { longitud_min?: number; longitud_max?: number }) => c.longitud_min === undefined || c.longitud_max === undefined || c.longitud_min <= c.longitud_max;
const vacio = (x: unknown) => x === undefined || x === null || (typeof x === "string" && !x.trim()) || (Array.isArray(x) && !x.length);
const partir = (x: unknown) => (Array.isArray(x) ? x : typeof x === "string" ? x.split(/\s*[|;\n]\s*/).filter((s) => s.trim()) : vacio(x) ? [] : [x]);
const formaCondicion = {
  campo: z.string({ error: "campo tiene que ser un nombre" }).trim().min(1, "falta campo").optional(),
  campos: z.array(z.string().trim().min(1), { error: "campos tiene que ser una lista de nombres" }).min(1, "campos está vacío").optional(),
  operador: OPERADOR.optional(), valor: z.unknown().optional(), valores: z.array(z.unknown(), { error: "valores tiene que ser una lista" }).optional(),
  hasta: z.unknown().optional(), contar: CONTAR.optional(), ...opcionesBusqueda,
};
type Condicion = { campo?: string; campos?: string[]; operador?: string; valor?: unknown; valores?: unknown[]; hasta?: unknown };
/** Lo que el motor exige a una condición (los números y fechas los revisa el motor). */
function revisarCondicion(c: Condicion, ctx: z.RefinementCtx, exigirCampo: boolean) {
  const mal = (path: string, message: string) => ctx.addIssue({ code: "custom", path: [path], message });
  const op = c.operador ?? "contiene";
  if (exigirCampo && !c.campo && !c.campos?.length) mal("campo", "falta campo (o campos)");
  if (OPS_CON_VALOR.has(op) && vacio(c.valor)) mal("valor", `el operador «${op}» necesita valor`);
  else if ((op === "coincide" || op === "no_coincide") && !regex("valor").safeParse(String(c.valor)).success) mal("valor", "patrón inválido");
  if (OPS_CON_LISTA.has(op) && !partir(vacio(c.valores) ? c.valor : c.valores).length) mal("valores", `«${op}» necesita valores (una lista, o un texto con | o ;)`);
  if (OPS_CON_RANGO.has(op)) {
    const v = c.valor;
    const dos = (Array.isArray(v) && v.length === 2) || (!vacio(v) && !vacio(c.hasta)) || (typeof v === "string" && partir(v).length === 2);
    if (!dos) mal("valor", `«${op}» necesita dos límites: valor [mínimo, máximo] o valor y hasta`);
  }
}
const condicionBusqueda = z.looseObject(formaCondicion, { error: "cada condición tiene que ser un objeto {campo, operador, valor}" })
  .superRefine((c, ctx) => revisarCondicion(c, ctx, true));
const listaCondiciones = (que: string) => z.array(condicionBusqueda, { error: `${que} tiene que ser una lista [{campo, operador, valor}]` }).min(1, `${que}: hace falta al menos una condición`);
const MODO_BUSQUEDA = z.enum(["todas", "alguna"], { error: "modo: todas o alguna" });
// Una sola condición en la config (campo, operador, valor) o varias en «condiciones» con modo todas/alguna; no las dos.
const filtroConf = conf({ ...formaCondicion, condiciones: listaCondiciones("condiciones").optional(), modo: MODO_BUSQUEDA.optional() })
  .superRefine((c, ctx) => {
    if (c.condiciones) {
      const mezcla = ["campo", "campos", "operador", "valor", "valores", "hasta"].filter((k) => (c as Record<string, unknown>)[k] !== undefined);
      if (mezcla.length) ctx.addIssue({ code: "custom", path: ["condiciones"], message: `usa ${mezcla.join(", ")} o condiciones, no las dos formas a la vez` });
      return;
    }
    if (!c.campo && !c.campos?.length) return void ctx.addIssue({ code: "custom", path: ["campo"], message: "falta campo (o condiciones)" });
    revisarCondicion(c, ctx, false);
  });
const OPS = ["plantilla", "asignar", "mayusculas", "minusculas", "titulo", "limpiar", "solo_digitos", "sin_tildes", "regex_extraer", "regex_reemplazar",
  "concatenar", "defecto", "recortar", "rellenar"] as const;
const operacion = z.looseObject({
  op: z.enum(OPS, { error: `op: ${OPS.join(", ")}` }),
  salida: variable,
  de: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  plantilla: z.string().optional(),
  patron: regex("patron").optional(),
  reemplazo: z.string().optional(),
  separador: z.string().optional(),
  grupo: z.number().int().min(0).optional(),
  largo: z.number({ error: "largo: entero entre 1 y 100000" }).int("largo: entero entre 1 y 100000").min(1, "largo: entero entre 1 y 100000").max(100000, "largo: entero entre 1 y 100000").optional(),
  lado: z.enum(["izquierda", "derecha"], { error: "lado: izquierda o derecha" }).optional(),
  caracter: z.string().length(1, "caracter: un solo carácter (p. ej. «0»)").optional(),
  sufijo: z.string().optional(),
  ...longitudCampo, distinguir_mayusculas: booleano.optional(), distinguir_tildes: booleano.optional(),
}).superRefine((o, ctx) => {
  if ((o.op === "recortar" || o.op === "rellenar") && o.largo === undefined) ctx.addIssue({ code: "custom", path: ["largo"], message: `${o.op} necesita largo` });
  if (o.op === "recortar" && o.contar && !["caracteres", "palabras"].includes(o.contar)) ctx.addIssue({ code: "custom", path: ["contar"], message: "recortar: contar es caracteres o palabras" });
  if (!minMaxOk(o)) ctx.addIssue({ code: "custom", path: ["longitud_min"], message: "longitud_min es mayor que longitud_max" });
});
const DESENLACES_DECL = ["radicar", "manual", "reenviar", "descartar", "responder", "duplicado"] as const;

// ---------------------------------------------- piezas de los tipos nuevos

const entre = (que: string, min: number, max: number, entero = true) => {
  const m = `${que} tiene que ser ${entero ? "un entero" : "un número"} entre ${min} y ${max}`;
  const n = z.number({ error: m }).min(min, m).max(max, m);
  return entero ? n.int(m) : n;
};
/** El NOMBRE de una credencial guardada (igual que en el motor). */
const CREDENCIAL_GUARDADA = /^[a-z0-9][a-z0-9_.-]{0,62}$/;
const credencialRef = z.string({ error: "falta credencial (el NOMBRE de una credencial guardada)" })
  .regex(CREDENCIAL_GUARDADA, "credencial es el NOMBRE de una credencial guardada (minúsculas, números, - _ .), nunca un valor ni una clave");
const timeoutS = entre("timeout_s", 1, 60, false).optional();
const opcional = booleano.optional();
const mapaPlantillas = (que: string) => z.record(z.string().regex(/^[A-Za-z_][A-Za-z0-9_]*$/, `${que}: nombres de letras, números y _`),
  z.union([z.string(), z.number(), z.boolean(), z.null()]), { error: `${que} tiene que ser un objeto {nombre: plantilla o valor}` });
const filtroCorreo = z.looseObject({ remitente: z.string().optional(), asunto: z.string().optional(), texto: z.string().optional(), no_leidos: opcional },
  { error: "filtro tiene que ser un objeto {remitente, asunto, texto, no_leidos}" });
const rutaRelativa = z.string({ error: "falta ruta" }).trim().min(1, "falta ruta").refine((r) => {
  if (r.includes("{{")) return true;
  const x = r.replace(/\\/g, "/");
  return !x.startsWith("/") && !/^[A-Za-z]:/.test(x) && !x.startsWith("~") && !x.includes("\0") && !x.split("/").includes("..");
}, "la ruta tiene que ser relativa al espacio de archivos (/datos/archivos), sin «..» ni rutas absolutas");
const nombreTabla = z.string({ error: "falta tabla" }).regex(/^[a-z][a-z0-9_]{0,40}$/, "nombre de tabla: minúsculas, números y _, empieza por letra");
const AMBITO = z.enum(["flujo", "compartida"], { error: "ambito: flujo o compartida" });
const RANGOS_CRON: [number, number][] = [[0, 59], [0, 23], [1, 31], [1, 12], [0, 7]];
/** Por qué un cron de 5 campos no vale, o null (igual que problema_cron del motor). */
export function problemaCron(expr: string): string | null {
  const campos = expr.trim().split(/\s+/);
  if (campos.length !== 5) return "cron tiene que tener 5 campos: minuto hora día mes día_semana (p. ej. «0 7 * * 1-5»)";
  for (let i = 0; i < 5; i++) {
    const [lo, hi] = RANGOS_CRON[i]!;
    for (const parte of campos[i]!.split(",")) {
      const m = /^(\*|(\d+)(?:-(\d+))?)(?:\/(\d+))?$/.exec(parte);
      if (!m) return `cron: «${parte}» no se entiende`;
      if (m[4] !== undefined && !(Number(m[4]) >= 1 && Number(m[4]) <= hi)) return `cron: paso «/${m[4]}» inválido`;
      for (const x of [m[2], m[3]]) if (x !== undefined && !(Number(x) >= lo && Number(x) <= hi)) return `cron: ${x} está fuera de ${lo}..${hi}`;
      if (m[2] !== undefined && m[3] !== undefined && Number(m[2]) > Number(m[3])) return `cron: el rango ${m[2]}-${m[3]} está al revés`;
    }
  }
  return null;
}
const zonaValida = (z_: string) => {
  try {
    new Intl.DateTimeFormat("es-CO", { timeZone: z_ });
    return true;
  } catch {
    return false;
  }
};
const zona = z.string().refine(zonaValida, "zona no es una zona horaria (p. ej. America/Bogota)");
/** Quita textos y comentarios de un SQL para revisar su forma (el motor hace la revisión completa). */
function esqueletoSql(sql: string): string {
  return sql.replace(/--[^\n]*|\/\*[\s\S]*?\*\/|'(?:[^']|'')*'|"(?:[^"]|"")*"|`[^`]*`/g, " ");
}
const sqlTexto = (que: string) => z.string({ error: `falta ${que}` }).trim().min(1, `falta ${que}`).max(20000, `${que} pasa de 20.000 caracteres`)
  .refine((s) => !s.includes("{{"), `${que}: no pegues valores con {{…}} en el SQL (inyección); usa :nombre y parametros`)
  .refine((s) => !esqueletoSql(s).trim().replace(/;\s*$/, "").includes(";"), `${que}: solo se permite UNA sentencia SQL`);
const expresionTexto = z.string({ error: "falta expresion" }).trim().min(1, "falta expresion").max(2000, "la expresión pasa de 2000 caracteres")
  .refine((s) => !/(^|[^A-Za-z0-9_])_/.test(s), "expresion: nada que empiece por _ (ni __import__, ni __class__…)")
  .refine((s) => !/\blambda\b|\bfor\b.+\bin\b|:=|\bf['"]/.test(s), "expresion: sin lambdas, comprensiones (for … in), := ni f-strings. Para contar o sumar lo de una lista usa len(x), sum(x), min(x) o max(x) sobre la salida de un para_cada (con recoger = lo que cuenta de cada vuelta, p. ej. 1 o 0) o sobre filas de tabla_consultar");
const CAMPO_ENTRADA = z.looseObject({
  // Los campos viven en {{entrada.<nombre>}}: los nombres reservados de variables no aplican aquí.
  nombre: z.string({ error: "tiene que ser un nombre" }).regex(/^[A-Za-z_][A-Za-z0-9_]*$/, "nombre: letras, números y guion bajo, sin espacios"), tipo: z.enum(["texto", "numero", "booleano", "fecha", "lista", "objeto", "archivo", "archivos"], { error: "tipo: texto, numero, booleano, fecha, lista, objeto, archivo o archivos" }).optional(),
  obligatorio: opcional, descripcion: z.string().optional(),
});
const unicos = (xs: string[]) => new Set(xs.map((x) => x.trim().toLowerCase())).size === xs.length;
/** El id de un archivo guardado en el espacio de un flujo (igual que en el motor). */
const ID_ARCHIVO = /^a-[0-9a-z]{6,40}$/;
const listaNombres = (que: string) => z.array(z.string().trim().min(1), { error: `${que} tiene que ser una lista de nombres de campo` });
/** Uno o varios archivos: un id, una plantilla («{{doc.id}}»), «adjuntos» o una lista de ellos. */
const textoOLista = (que: string) => z.union([z.string().trim().min(1), z.array(z.string().trim().min(1)).min(1)], { error: `falta ${que}: ids de archivos, plantillas o «adjuntos»` });
/** Cómo se nombra un tipo en un filtro de archivos (igual que TIPOS_DE_ARCHIVO del motor) o una extensión. */
const TIPO_ARCHIVO = /^\.?[a-z0-9]{1,6}$/i;
const regexOk = (p: string) => {
  try { new RegExp(p.replace(/\(\?P</g, "(?<")); return true; } catch { return false; }
};
/** Un filtro de archivos {de, nombre, tipo}: de «adjuntos», «archivos» o una variable con archivos; nombre sin distinguir mayúsculas ni tildes. */
const filtroArchivos = z.looseObject({
  de: textoReq("de («adjuntos», «archivos» o una variable con archivos)"),
  nombre: z.string({ error: "nombre es un texto" }).refine(regexOk, "nombre inválido (expresión)").optional(),
  tipo: z.union([z.string().regex(TIPO_ARCHIVO, "tipo: pdf, imagen, word, excel, texto o una extensión"), z.array(z.string().regex(TIPO_ARCHIVO, "tipo: pdf, imagen, word, excel, texto o una extensión"))]).optional(),
}, { error: "un filtro de archivos es {de, nombre, tipo}" });
/** Archivos que se mandan o se entregan: textos («{{documento}}», «{{adjuntos}}», ids) o filtros, sueltos o en lista (hasta 50). */
const archivosRef = (que: string) => z.union([
  z.string().trim().min(1, `${que}: está vacío`),
  filtroArchivos,
  z.array(z.union([z.string().trim().min(1, `${que}: hay uno vacío`), filtroArchivos]), { error: `${que}: lista de archivos` }).min(1, `${que} está vacío`).max(50, `${que}: hasta 50`),
], { error: `${que}: «{{documento}}», «{{adjuntos}}», un id «a-…», un filtro {de, nombre, tipo} o una lista de ellos` });
/** desde_ia: la IA redacta el documento o arma la hoja (con su IA propia, si se da). */
const desdeIa = z.looseObject({
  instrucciones: textoReq("desde_ia.instrucciones (qué redactar)"),
  entradas: z.array(z.string().trim().min(1), { error: "desde_ia.entradas: lista de nombres" }).optional(),
  proveedor: z.enum(ia.PROVEEDORES_IA, { error: `desde_ia.proveedor: ${ia.PROVEEDORES_IA.join(", ")}` }).optional(),
  modelo: z.string().trim().min(1, "desde_ia.modelo: el nombre de un modelo").optional(),
  credencial: z.string().regex(CREDENCIAL_GUARDADA, "desde_ia.credencial: el NOMBRE de una credencial guardada").optional(),
  temperatura: entre("desde_ia.temperatura", 0, 2, false).optional(),
}, { error: "desde_ia tiene que ser un objeto {instrucciones, entradas?, proveedor?, modelo?}" })
  .refine((d) => !d.proveedor || Boolean(d.modelo), { message: "desde_ia: con proveedor hace falta también el modelo", path: ["modelo"] });

export const CONFIG_DE: Record<TipoDeNodo, z.ZodType> = {
  // Su correspondencia es SUYA: la bandeja de prueba (.eml/.msg) y, con credencial, su buzón (solo lectura).
  entrada_correo: conf({
    leer_adjuntos: booleano.optional(), ocr: booleano.optional(), bandeja_prueba: opcional,
    credencial: z.string().regex(CREDENCIAL_GUARDADA, "credencial: el NOMBRE de una credencial guardada (imap o microsoft_graph)").optional(),
    carpeta: z.string().optional(), filtro: z.looseObject({ remitente: z.string().optional(), asunto: z.string().optional(), texto: z.string().optional(), no_leidos: z.boolean().optional() }).optional(),
  }),
  entrada_archivos: conf({
    tipos_permitidos: z.array(z.string().regex(/^\.?[a-z0-9]{1,6}$/i, "cada tipo es una extensión (pdf, jpg, docx…)"), { error: "tipos_permitidos: lista de extensiones" }).min(1).optional()
      .refine((xs) => !xs?.some((x) => espacio.EXT_VIDEO_AUDIO.has(x.toLowerCase().replace(/^\./, ""))), "tipos_permitidos: video y audio nunca se aceptan"),
    max_archivos: z.number().int().min(1).max(50).optional(), max_mb: z.number().int().min(1).max(25).optional(),
    leer: z.boolean().optional(), ocr: z.boolean().optional(),
    campos_extra: z.array(z.looseObject({ nombre: z.string().regex(/^[A-Za-z_][A-Za-z0-9_]*$/), tipo: z.string().optional(), obligatorio: z.boolean().optional(), descripcion: z.string().optional() })).optional(),
  }),
  entrada_manual: conf({ campos: z.array(CAMPO_ENTRADA, { error: "campos tiene que ser una lista [{nombre, tipo, obligatorio, descripcion}]" }).optional() })
    .refine((c) => !c.campos || unicos(c.campos.map((x) => x.nombre)), { message: "hay campos con el mismo nombre", path: ["campos"] }),
  programado: conf({
    cron: z.string().optional().superRefine((x, ctx) => { const p = x ? problemaCron(x) : null; if (p) ctx.addIssue({ code: "custom", message: p }); }),
    cada_minutos: entre("cada_minutos", 1, 10080).optional(), zona: zona.optional(), activo: opcional,
  }).refine((c) => Number(!!c.cron) + Number(c.cada_minutos !== undefined) === 1, { message: "programado necesita cron o cada_minutos (uno de los dos)", path: ["cron"] }),
  webhook: conf({
    secreto: z.string().regex(/^[A-Za-z0-9_-]{16,128}$/, "secreto: entre 16 y 128 letras, números, - o _ (se genera solo al guardar)").optional(),
    metodo: z.enum(["POST"], { error: "metodo: POST" }).optional(), responder: opcional, esperar_ms: entre("esperar_ms", 0, 30000).optional(),
  }),
  correo_nuevo: conf({
    credencial: credencialRef, carpeta: z.string().optional(), cada_minutos: entre("cada_minutos", 1, 1440).optional(), activo: opcional,
    filtro: filtroCorreo.optional(), max_por_sondeo: entre("max_por_sondeo", 1, 50).optional(), procesar_existentes: opcional, leer_adjuntos: opcional, ocr: opcional,
  }),
  correo_leer: conf({
    credencial: credencialRef, carpeta: z.string().optional(), filtro: filtroCorreo.optional(), desde_fecha: z.string().optional(),
    max: entre("max", 1, 100).optional(), incluir_adjuntos: opcional, timeout_s: timeoutS,
  }),
  correo_enviar: conf({
    credencial: credencialRef, para: textoReq("para"), cc: z.string().optional(), cco: z.string().optional(), asunto: textoReq("asunto"),
    cuerpo: textoReq("cuerpo"), html: z.string().optional(), adjuntos: archivosRef("adjuntos").optional(), timeout_s: timeoutS,
  }),
  drive_listar: conf({ credencial: credencialRef, carpeta: z.string().optional(), nombre: z.string().optional(), tipo_mime: z.string().optional(), max: entre("max", 1, 200).optional(), timeout_s: timeoutS }),
  drive_leer: conf({ credencial: credencialRef, archivo: textoReq("archivo"), ocr: opcional, max_caracteres: entre("max_caracteres", 1, 500000).optional(), timeout_s: timeoutS }),
  drive_subir: conf({
    credencial: credencialRef, carpeta: z.string().optional(), nombre: z.string().optional(), contenido: z.string().optional(), de: z.string().optional(),
    archivo: archivosRef("archivo").optional(),
    formato: z.enum(["texto", "json", "csv"], { error: "formato: texto, json o csv" }).optional(), timeout_s: timeoutS,
  }).superRefine((c, ctx) => {
    const mal = (k: string, m: string) => ctx.addIssue({ code: "custom", path: [k], message: m });
    if (c.archivo !== undefined) {
      if (c.contenido || c.de) mal("archivo", "drive_subir: usa archivo (un archivo tal cual) o contenido/de (un texto), no los dos");
    } else {
      if (!c.nombre?.trim()) mal("nombre", "falta nombre");
      if (!c.contenido && !c.de) mal("contenido", "drive_subir necesita archivo (p. ej. «{{documento}}»), contenido (plantilla) o de (variable)");
    }
  }),
  hoja_leer: conf({
    origen: z.enum(["adjunto", "drive", "archivo", "url", "sheets"], { error: "origen: adjunto, drive, archivo, url o sheets" }), adjunto: regex("adjunto").optional(),
    credencial: credencialRef.optional(), archivo: z.string().optional(), url: z.string().optional(), hoja: z.string().optional(), encabezados: opcional,
    hoja_id: z.string().optional(), rango: z.string().optional(),
    max_filas: entre("max_filas", 1, 5000).optional(), timeout_s: timeoutS,
  }).superRefine((c, ctx) => {
    const falta = (k: string, m: string) => ctx.addIssue({ code: "custom", path: [k], message: m });
    if ((c.origen === "drive" || c.origen === "sheets") && !c.credencial) falta("credencial", `con origen ${c.origen} hace falta credencial (google_servicio)`);
    if (c.origen === "sheets" && !c.hoja_id?.trim()) falta("hoja_id", "con origen sheets hace falta hoja_id (el id o el enlace de la hoja de Google)");
    if ((c.origen === "drive" || c.origen === "archivo") && !c.archivo?.trim()) falta("archivo", "falta archivo (id de Drive o ruta)");
    if (c.origen === "archivo" && c.archivo && !rutaRelativa.safeParse(c.archivo).success) falta("archivo", "la ruta tiene que ser relativa al espacio de archivos, sin «..»");
    if (c.origen === "url" && !c.url?.trim()) falta("url", "falta url");
  }),
  sql_consulta: conf({ credencial: credencialRef, consulta: sqlTexto("consulta"), parametros: mapaPlantillas("parametros").optional(), max_filas: entre("max_filas", 1, 5000).optional(), timeout_s: timeoutS }),
  sql_ejecutar: conf({ credencial: credencialRef, sentencia: sqlTexto("sentencia"), parametros: mapaPlantillas("parametros").optional(), permitir_sin_where: opcional, timeout_s: timeoutS }),
  // Un archivo subido al flujo (archivo: su id) o, en los flujos de antes, una ruta relativa.
  archivo_leer: conf({
    archivo: z.string().regex(/^(a-[0-9a-z]{6,40}|\s*\{\{[^{}]+\}\}\s*)$/, "archivo: el id de un archivo subido al flujo o una plantilla como «{{entrada.archivos.0.id}}»").optional(), ruta: rutaRelativa.optional(),
    formato: z.enum(["auto", "texto", "json", "filas"], { error: "formato: auto, texto, json o filas" }).optional(), hoja: z.string().optional(),
    max_caracteres: entre("max_caracteres", 1, 500000).optional(), max_filas: entre("max_filas", 1, 5000).optional(),
  }).refine((c) => Number(!!c.archivo) + Number(!!c.ruta) === 1, { message: "archivo_leer necesita el archivo subido al flujo (o, en los flujos de antes, una ruta)", path: ["archivo"] }),
  archivo_guardar: conf({
    ruta: rutaRelativa, contenido: z.string().optional(), de: z.string().optional(),
    formato: z.enum(["texto", "json", "csv"], { error: "formato: texto, json o csv" }).optional(), modo: z.enum(["crear", "reemplazar", "anexar"], { error: "modo: crear, reemplazar o anexar" }).optional(),
  }).refine((c) => c.contenido !== undefined || c.de, { message: "archivo_guardar necesita contenido (plantilla) o de (variable)", path: ["contenido"] }),
  tabla_guardar: conf({
    tabla: nombreTabla, clave: z.string().optional(), datos: z.record(z.string(), z.unknown(), { error: "datos tiene que ser un objeto {campo: plantilla o valor}, p. ej. {\"titulo\": \"{{t.titulo}}\"}; para guardar una variable entera usa de: \"t\"" }).optional(),
    de: z.string().optional(), modo: z.enum(["reemplazar", "combinar", "insertar"], { error: "modo: reemplazar, combinar o insertar" }).optional(), ambito: AMBITO.optional(),
  }).refine((c) => (c.datos && Object.keys(c.datos).length) || c.de, { message: "tabla_guardar necesita datos ({campo: plantilla}) o de (una variable)", path: ["datos"] }),
  tabla_consultar: conf({
    tabla: nombreTabla, clave: z.string().optional(),
    // {campo: valor} (iguales, sin distinguir mayúsculas ni tildes) o una lista de condiciones con operadores
    filtro: z.unknown().superRefine((v, ctx) => {
      const r = (Array.isArray(v) ? listaCondiciones("filtro") : mapaPlantillas("filtro")).safeParse(v);
      if (!r.success) for (const i of r.error.issues) ctx.addIssue({ code: "custom", path: i.path.map((p) => (typeof p === "symbol" ? String(p) : p)), message: i.message });
    }).optional(),
    condiciones: listaCondiciones("condiciones").optional(), modo: MODO_BUSQUEDA.optional(), limite: entre("limite", 1, 500).optional(),
    orden: z.enum(["-creada", "creada", "-actualizada", "actualizada", "clave", "-clave"], { error: "orden: creada, actualizada o clave (con - para descendente)" }).optional(), ambito: AMBITO.optional(),
  }),
  para_cada: conf({
    lista: textoReq("lista"), variable: variable.optional(), indice: variable.optional(), recoger: z.string().optional(),
    max_iteraciones: entre("max_iteraciones", 1, 1000).optional(), al_fallar: z.enum(["detener", "continuar"], { error: "al_fallar: detener o continuar" }).optional(),
    concurrencia: entre("concurrencia", 1, 8).optional(),
  }),
  fin_ciclo: conf({}),
  paralelo: conf({
    ramas: z.array(z.string().trim().min(1), { error: "faltan ramas: [nombres]" }).min(1, "faltan ramas").max(10, "hasta 10 ramas")
      .refine(unicos, "hay ramas repetidas").refine((xs) => !xs.some((x) => ["error", "listo"].includes(x.trim().toLowerCase())), "«error» y «listo» no pueden ser nombres de rama"),
    concurrencia: entre("concurrencia", 1, 8).optional(),
  }),
  unir: conf({}),
  esperar: conf({ segundos: entre("segundos", 0, 60, false) }),
  expresion: conf({ expresion: expresionTexto, como_condicion: opcional }),
  json: conf({
    op: z.enum(["parsear", "serializar", "seleccionar"], { error: "op: parsear, serializar o seleccionar" }), de: textoReq("de"),
    ruta: z.string().regex(RUTA, "ruta inválida (p. ej. «items.0.id»)").optional(), indentar: opcional, defecto: z.unknown().optional(),
  }).refine((c) => c.op !== "seleccionar" || c.ruta, { message: "seleccionar necesita ruta", path: ["ruta"] }),
  fecha: conf({
    op: z.enum(["ahora", "parsear", "formatear", "sumar", "diferencia"], { error: "op: ahora, parsear, formatear, sumar o diferencia" }),
    valor: z.union([z.string(), z.number()]).optional(), formato: z.string().includes("%", { message: "formato es un patrón strftime, p. ej. «%d/%m/%Y»" }).optional(),
    cantidad: z.union([z.number(), z.string()]).optional(), unidad: z.enum(["minutos", "horas", "dias", "semanas"], { error: "unidad: minutos, horas, dias o semanas" }).optional(),
    hasta: z.string().optional(), zona: zona.optional(),
  }).refine((c) => c.op !== "sumar" || c.cantidad !== undefined, { message: "sumar necesita cantidad", path: ["cantidad"] }),
  llamar_flujo: conf({ flujo: z.string({ error: "falta flujo" }).regex(/^[a-z][a-z0-9-]{1,40}$/, "flujo: el id de otro flujo declarativo"), entrada: mapaPlantillas("entrada").optional() }),
  respuesta_webhook: conf({
    estado: entre("estado", 100, 599).optional(), cuerpo: z.unknown().optional(),
    tipo_contenido: z.enum(["application/json", "text/plain"], { error: "tipo_contenido: application/json o text/plain" }).optional(),
    archivos: archivosRef("archivos").optional(), archivos_como: z.enum(["enlaces", "base64"], { error: "archivos_como: enlaces o base64" }).optional(),
  }),
  leer_adjuntos: conf({
    ocr: booleano.optional(), vision: booleano.optional(),
    tipos: z.array(z.string().regex(/^(pdf|imagen|docx|xlsx|texto|p7m|\.[a-z0-9]{1,6})$/i, "tipo: pdf, imagen, docx, xlsx, texto, p7m o una extensión como .pdf")).min(1).optional(),
    nombre_patron: regex("nombre_patron").optional(), // sin distinguir mayúsculas ni tildes
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
    // sin distinguir mayúsculas ni tildes (salvo distinguir_*); longitud y solo_digitos saltan los candidatos que no cumplen
    campos: z.array(z.looseObject({
      nombre: variable, patron: regex("patron"), grupo: z.number().int().min(0).optional(), todos: z.boolean().optional(), obligatorio: z.boolean().optional(),
      ...longitudCampo, distinguir_mayusculas: booleano.optional(), distinguir_tildes: booleano.optional(),
    }).refine(minMaxOk, { message: "longitud_min es mayor que longitud_max", path: ["longitud_min"] })).min(1).optional(),
    ...longitudCampo, distinguir_mayusculas: booleano.optional(), distinguir_tildes: booleano.optional(),
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
  enviar: conf({ cuerpo: textoReq("cuerpo"), para: z.string().optional(), asunto: z.string().optional(), cc: z.string().optional(), adjuntos: archivosRef("adjuntos").optional() }),
  // Solo los GET a hosts de HTTP_HOSTS_PERMITIDOS salen de verdad; lo demás se arma y queda como escritura bloqueada.
  http: conf({
    metodo: z.enum(METODOS_HTTP, { error: `metodo: ${METODOS_HTTP.join(", ")}` }).optional(),
    url: textoReq("url").refine((u) => u.trim().startsWith("{{") || /^https?:\/\/[^\s/]/i.test(u.trim()), "url tiene que empezar por http:// o https://"),
    query: z.record(z.string(), z.string(), { error: "query tiene que ser un objeto {nombre: plantilla}" }).optional(),
    encabezados: z.record(z.string(), z.string(), { error: "encabezados tiene que ser un objeto {nombre: plantilla}" })
      .refine((e) => !Object.keys(e).some((k) => ENCABEZADOS_SECRETOS.includes(k.trim().toLowerCase())), "encabezados: Authorization y las claves no van en el flujo; usa credencial con el nombre de la variable de entorno")
      .optional(),
    cuerpo: z.union([z.string(), z.record(z.string(), z.unknown()), z.array(z.unknown())], { error: "cuerpo tiene que ser texto (plantilla) o un objeto JSON con plantillas" }).optional(),
    credencial: z.string().refine((x) => /^[A-Z][A-Z0-9_]{2,63}$/.test(x) || CREDENCIAL_GUARDADA.test(x),
      "credencial es el NOMBRE de una credencial guardada (http_bearer o http_basico) o de una variable de entorno (p. ej. HTTP_TOKEN_X), nunca el valor").optional(),
    timeout_ms: z.number({ error: "timeout_ms tiene que ser un entero entre 100 y 60000" }).int().min(100, "timeout_ms entre 100 y 60000").max(60000, "timeout_ms entre 100 y 60000").optional(),
    mapeo: z.array(z.looseObject({
      variable, ruta: z.string({ error: "falta ruta" }).regex(RUTA, "ruta inválida (p. ej. «items.0.id», «items[estado=activo].id», «[\"a.b\"]»)"),
      defecto: z.unknown().optional(), obligatorio: booleano.optional(),
    }), { error: "mapeo tiene que ser una lista [{variable, ruta, defecto, obligatorio}]" })
      .refine((m) => new Set(m.map((x) => x.variable)).size === m.length, "mapeo: hay variables repetidas").optional(),
  }),
  // ---------------------------------------------- nodos de producto (puente/nodos_producto.py)
  aprobacion_humana: conf({
    titulo: textoReq("titulo"), detalle: z.string().optional(),
    datos: z.union([z.record(z.string(), z.unknown()), z.string()], { error: "datos tiene que ser un objeto {campo: plantilla o valor} o una variable" }).optional(),
    campos_editables: listaNombres("campos_editables").optional(), tiempo_limite_h: entre("tiempo_limite_h", 0.01, 720, false).optional(),
    al_vencer: z.enum(["rechazar", "aprobar", "error"], { error: "al_vencer: rechazar, aprobar o error" }).optional(), asignado_a: z.string().optional(),
  }).superRefine((c, ctx) => {
    if (c.datos && typeof c.datos === "object" && c.campos_editables) {
      const faltan = c.campos_editables.filter((x) => !(x in (c.datos as Record<string, unknown>)));
      if (faltan.length) ctx.addIssue({ code: "custom", path: ["campos_editables"], message: `campos_editables: ${faltan.join(", ")} no está en datos` });
    }
  }),
  documento_generar: conf({
    formato: z.enum(["pdf", "docx"], { error: "formato: pdf o docx" }), plantilla: z.string().optional(),
    plantilla_archivo: z.string().regex(ID_ARCHIVO, "plantilla_archivo: el id de un .docx subido al flujo").optional(), nombre: z.string().optional(), titulo: z.string().optional(),
    desde_ia: desdeIa.optional(),
  }).refine((c) => Number(Boolean(c.plantilla?.trim())) + Number(Boolean(c.plantilla_archivo)) + Number(c.desde_ia !== undefined) === 1,
    { message: "documento_generar necesita plantilla, plantilla_archivo o desde_ia (una de las tres)", path: ["plantilla"] }),
  pdf_unir: conf({ archivos: textoOLista("archivos"), nombre: z.string().optional() }),
  pdf_dividir: conf({
    archivo: textoReq("archivo"), modo: z.enum(["cada_pagina", "rangos"], { error: "modo: cada_pagina o rangos" }).optional(),
    rangos: z.string().optional(), nombre_base: z.string().optional(),
  }).superRefine((c, ctx) => {
    if (c.modo === "rangos" && !c.rangos?.trim()) ctx.addIssue({ code: "custom", path: ["rangos"], message: "con modo rangos hace falta rangos («1-3, 4, 5-fin»)" });
    else if (c.modo === "rangos" && c.rangos && !c.rangos.includes("{{") && !/^\s*\d+\s*(-\s*(\d+|fin))?\s*([,;]\s*\d+\s*(-\s*(\d+|fin))?\s*)*$/i.test(c.rangos))
      ctx.addIssue({ code: "custom", path: ["rangos"], message: "rangos se escribe como «1-3, 4, 5-fin»" });
  }),
  documento_convertir: conf({ archivo: textoReq("archivo"), a: z.enum(["pdf", "docx", "txt", "md", "csv", "xlsx", "png"], { error: "a: pdf, docx, txt, md, csv, xlsx o png" }), nombre: z.string().optional() }),
  documento_clasificar: conf({
    archivo: textoOLista("archivo").optional(), texto: z.string().optional(), instrucciones: z.string().optional(), variable: variable.optional(),
    categorias: z.array(valorOObjeto("valor"), { error: "faltan categorias: [{valor, descripcion}]" }).min(1, "faltan categorias")
      .refine((xs) => unicos(xs.map((x) => (typeof x === "string" ? x : String(x.valor ?? "")))), "hay categorías repetidas"),
    max_caracteres: entre("max_caracteres", 500, 200000).optional(),
  }).refine((c) => !vacio(c.archivo) || Boolean(c.texto?.trim()), { message: "documento_clasificar necesita archivo o texto", path: ["archivo"] }),
  documento_comparar: conf({ a: textoReq("a"), b: textoReq("b"), resumir: opcional, ignorar_espacios: opcional, instrucciones: z.string().optional() }),
  anonimizar: conf({
    de: textoReq("de"), operacion: z.enum(["anonimizar", "revertir"], { error: "operacion: anonimizar o revertir" }).optional(),
    modo: z.enum(["enmascarar", "etiqueta", "seudonimo"], { error: "modo: enmascarar, etiqueta o seudonimo" }).optional(),
    tipos: z.array(z.enum(["cedula", "nit", "nombre", "telefono", "correo", "direccion", "cuenta"], { error: "tipos: cedula, nit, nombre, telefono, correo, direccion o cuenta" })).min(1).optional(),
    campos: listaNombres("campos").optional(), nombres_extra: z.array(z.string()).optional(), guardar_mapa: opcional, mapa_de: variable.optional(),
  }).superRefine((c, ctx) => {
    if (c.operacion === "revertir" && !c.mapa_de) ctx.addIssue({ code: "custom", path: ["mapa_de"], message: "revertir necesita mapa_de (la salida del anonimizar con guardar_mapa)" });
    if (c.guardar_mapa === true && (c.modo ?? "seudonimo") !== "seudonimo") ctx.addIssue({ code: "custom", path: ["guardar_mapa"], message: "guardar_mapa solo sirve con modo seudonimo" });
  }),
  hoja_escribir: conf({
    destino: z.enum(["archivo", "sheets"], { error: "destino: archivo o sheets" }).optional(), archivo: z.string().optional(), nombre: z.string().optional(),
    formato: z.enum(["xlsx", "csv"], { error: "formato: xlsx o csv" }).optional(), credencial: credencialRef.optional(), hoja_id: z.string().optional(), hoja: z.string().optional(),
    modo: z.enum(["anexar", "actualizar", "reemplazar"], { error: "modo: anexar, actualizar o reemplazar" }).optional(), clave: z.string().optional(),
    insertar_si_falta: opcional, filas: z.string().optional(), fila: z.record(z.string(), z.unknown(), { error: "fila tiene que ser un objeto {columna: plantilla}" }).optional(), timeout_s: timeoutS,
    desde_ia: desdeIa.optional(),
  }).superRefine((c, ctx) => {
    const mal = (k: string, m: string) => ctx.addIssue({ code: "custom", path: [k], message: m });
    if (c.desde_ia !== undefined) {
      if (c.filas || c.fila) mal("desde_ia", "con desde_ia la IA arma las hojas: quita filas y fila");
      if (c.destino === "sheets" || c.archivo) mal("desde_ia", "desde_ia crea una hoja nueva del flujo (destino archivo, sin archivo)");
    } else if (!c.filas && !c.fila) mal("filas", "hoja_escribir necesita filas (una variable), fila ({columna: plantilla}) o desde_ia");
    if (c.modo === "actualizar" && !c.clave?.trim()) mal("clave", "con modo actualizar hace falta clave");
    if (c.destino === "sheets") {
      if (!c.credencial) mal("credencial", "con destino sheets hace falta credencial (google_servicio)");
      if (!c.hoja_id?.trim()) mal("hoja_id", "con destino sheets hace falta hoja_id");
    } else {
      if (c.credencial) mal("credencial", "credencial solo aplica con destino sheets");
      if (c.archivo && !c.archivo.includes("{{") && !ID_ARCHIVO.test(c.archivo)) mal("archivo", "archivo: el id de una hoja del flujo");
    }
  }),
  mensaje_teams: conf({ credencial: credencialRef, titulo: z.string().optional(), texto: textoReq("texto"), hechos: mapaPlantillas("hechos").optional(), enlace: z.string().optional(), archivos: archivosRef("archivos").optional(), timeout_s: timeoutS }),
  mensaje_slack: conf({ credencial: credencialRef, titulo: z.string().optional(), texto: textoReq("texto"), hechos: mapaPlantillas("hechos").optional(), enlace: z.string().optional(), archivos: archivosRef("archivos").optional(), timeout_s: timeoutS }),
  webhook_saliente: conf({
    credencial: credencialRef, archivos: archivosRef("archivos").optional(),
    modo_archivos: z.enum(["base64", "multipart", "enlace"], { error: "modo_archivos: base64, multipart o enlace" }).optional(),
    cuerpo: z.union([z.string(), z.record(z.string(), z.unknown()), z.array(z.unknown())], { error: "falta cuerpo: un objeto JSON con plantillas (o un texto)" }),
    encabezados: z.record(z.string(), z.string(), { error: "encabezados tiene que ser un objeto {nombre: plantilla}" })
      .refine((e) => !Object.keys(e).some((k) => ENCABEZADOS_SECRETOS.includes(k.trim().toLowerCase())), "encabezados: Authorization y las claves van en la credencial, no en el flujo").optional(),
    timeout_s: timeoutS,
  }),
  traducir: conf({ de: textoReq("de"), a: z.string().trim().min(1).optional(), desde: z.string().trim().min(1).optional(),
    tono: z.enum(["formal", "neutro", "informal"], { error: "tono: formal, neutro o informal" }).optional(), glosario: mapaPlantillas("glosario").optional() }),
  resumir_largo: conf({ de: textoReq("de"), tam_parte: entre("tam_parte", 1000, 30000).optional(), max_partes: entre("max_partes", 1, 50).optional(),
    longitud: z.string().optional(), enfoque: z.string().optional(), instrucciones: z.string().optional() }),
  preguntar_documentos: conf({
    pregunta: textoReq("pregunta"), archivos: textoOLista("archivos").optional(), archivo: z.string().optional(), texto: z.string().optional(),
    max_fragmentos: entre("max_fragmentos", 1, 20).optional(), tam_fragmento: entre("tam_fragmento", 300, 6000).optional(), instrucciones: z.string().optional(),
  }).refine((c) => !vacio(c.archivos) || Boolean(c.archivo?.trim()) || Boolean(c.texto?.trim()), { message: "preguntar_documentos necesita archivos, archivo o texto", path: ["archivos"] }),
  extraer_tablas: conf({
    archivos: textoOLista("archivos").optional(), archivo: z.string().optional(), texto: z.string().optional(), columnas: listaNombres("columnas").optional(),
    instrucciones: z.string().optional(), max_paginas: entre("max_paginas", 1, 50).optional(),
  }).refine((c) => !vacio(c.archivos) || Boolean(c.archivo?.trim()) || Boolean(c.texto?.trim()), { message: "extraer_tablas necesita archivos, archivo o texto", path: ["archivos"] }),
  deduplicar: conf({
    lista: z.string().optional(), elemento: z.string().optional(), campos: listaNombres("campos").optional(), entre_corridas: opcional, registrar: opcional,
    tabla: nombreTabla.optional(), olvidar_dias: entre("olvidar_dias", 1, 3650).optional(),
  }).refine((c) => Boolean(c.lista?.trim()) !== Boolean(c.elemento?.trim()), { message: "deduplicar necesita lista o elemento (uno de los dos)", path: ["lista"] }),
  agrupar: conf({
    lista: textoReq("lista"), por: listaNombres("por").optional(),
    calculos: z.array(z.looseObject({ op: z.enum(["contar", "sumar", "promedio", "minimo", "maximo", "lista", "unicos"], { error: "op: contar, sumar, promedio, minimo, maximo, lista o unicos" }),
      campo: z.string().optional(), nombre: z.string().regex(/^[A-Za-z_][A-Za-z0-9_]*$/, "nombre: letras, números y _").optional() })
      .refine((x) => x.op === "contar" || Boolean(x.campo?.trim()), "cada cálculo (salvo contar) necesita campo"), { error: "calculos tiene que ser una lista [{op, campo, nombre}]" }).optional(),
    orden: z.string().regex(/^-?[A-Za-z_][A-Za-z0-9_]*$/, "orden: un cálculo o campo, con - para de mayor a menor").optional(),
  }),
  ordenar_filtrar: conf({
    lista: textoReq("lista"), condiciones: listaCondiciones("condiciones").optional(), modo: MODO_BUSQUEDA.optional(),
    orden: z.array(z.looseObject({ campo: textoReq("campo"), direccion: z.enum(["asc", "desc"], { error: "direccion: asc o desc" }).optional(),
      tipo: z.enum(["auto", "texto", "numero", "fecha"], { error: "tipo: auto, texto, numero o fecha" }).optional() }), { error: "orden tiene que ser una lista [{campo, direccion, tipo}]" }).optional(),
    limite: entre("limite", 1, 5000).optional(), desde: entre("desde", 0, 100000).optional(), variable: variable.optional(),
  }),
  mapear: conf({
    de: textoReq("de"), variable: variable.optional(), conservar_otros: opcional,
    campos: z.array(z.looseObject({
      destino: z.string({ error: "falta destino" }).regex(/^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/, "destino: nombre, o ruta con puntos («persona.nombre»)"),
      origen: z.string().regex(RUTA, "origen: una ruta válida dentro del elemento").optional(), plantilla: z.string().optional(), valor: z.unknown().optional(), defecto: z.unknown().optional(),
      tipo: z.enum(["texto", "numero", "entero", "booleano", "fecha", "lista", "objeto"], { error: "tipo: texto, numero, entero, booleano, fecha, lista u objeto" }).optional(),
      formato: z.string().includes("%", { message: "formato es un patrón strftime" }).optional(), obligatorio: opcional,
    }).refine((x) => ["origen", "plantilla", "valor"].filter((k) => k in x).length === 1, "cada campo lleva uno de origen, plantilla o valor"),
    { error: "faltan campos: [{destino, origen | plantilla | valor}]" }).min(1, "faltan campos")
      .refine((xs) => new Set(xs.map((x) => x.destino)).size === xs.length, "hay destinos repetidos"),
  }),
  validar_contra: conf({
    valor: textoReq("valor"), contra: z.enum(["lista", "tabla", "sql"], { error: "contra: lista, tabla o sql" }), valores: z.array(z.unknown()).optional(),
    tabla: nombreTabla.optional(), campo: z.string().optional(), ambito: AMBITO.optional(), credencial: credencialRef.optional(),
    consulta: sqlTexto("consulta").optional(), parametros: mapaPlantillas("parametros").optional(), ...opcionesBusqueda, timeout_s: timeoutS,
  }).superRefine((c, ctx) => {
    const mal = (k: string, m: string) => ctx.addIssue({ code: "custom", path: [k], message: m });
    if (c.contra === "lista" && !c.valores?.length) mal("valores", "contra lista necesita valores");
    if (c.contra === "tabla" && !c.tabla) mal("tabla", "contra tabla necesita tabla");
    if (c.contra === "sql" && (!c.credencial || !c.consulta)) mal("consulta", "contra sql necesita credencial y consulta");
    if (c.contra !== "sql" && c.credencial) mal("credencial", "credencial solo aplica con contra sql");
  }),
  subflujo: conf({
    flujo: z.string({ error: "falta flujo" }).regex(/^[a-z][a-z0-9-]{1,40}$/, "flujo: el id de otro flujo declarativo"), entrada: mapaPlantillas("entrada").optional(),
    traer: z.record(variable, z.string().regex(RUTA, "traer: rutas válidas en las variables del otro flujo"), { error: "traer tiene que ser {variable_local: ruta}" }).optional(),
  }),
  reintentar_mas_tarde: conf({
    en_minutos: entre("en_minutos", 1, 10080).optional(), a_las: z.string().optional(), motivo: z.string().optional(), max_intentos: entre("max_intentos", 1, 20).optional(),
  }).refine((c) => (c.en_minutos !== undefined) !== Boolean(c.a_las?.trim()), { message: "reintentar_mas_tarde necesita en_minutos o a_las (una de las dos)", path: ["en_minutos"] })
    .refine((c) => !c.a_las || c.a_las.includes("{{") || /^([01]\d|2[0-3]):[0-5]\d$/.test(c.a_las.trim()) || !Number.isNaN(Date.parse(c.a_las)), { message: "a_las: «HH:MM» o una fecha ISO", path: ["a_las"] }),
  limite_tasa: conf({
    maximo: entre("maximo", 1, 100000), por: z.enum(["segundo", "minuto", "hora", "dia"], { error: "por: segundo, minuto, hora o dia" }).optional(), clave: z.string().optional(),
    al_exceder: z.enum(["esperar", "aplazar", "saltar", "error"], { error: "al_exceder: esperar, aplazar, saltar o error" }).optional(),
  }),
  ventana_horario: conf({
    dias: z.array(z.enum(["lun", "mar", "mie", "jue", "vie", "sab", "dom"], { error: "dias: lun, mar, mie, jue, vie, sab o dom" })).min(1, "dias está vacío").optional(),
    desde: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "desde: hora HH:MM").optional(), hasta: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "hasta: hora HH:MM").optional(),
    festivos: opcional, zona: zona.optional(), fuera: z.enum(["esperar", "aplazar", "saltar"], { error: "fuera: esperar, aplazar o saltar" }).optional(),
  }).refine((c) => !c.desde || !c.hasta || c.desde < c.hasta, { message: "desde tiene que ser antes de hasta", path: ["desde"] }),
  detener_con_alerta: z.looseObject({
    ...comunesDeSalida, mensaje: textoReq("mensaje"), titulo: z.string().optional(),
    nivel: z.enum(["info", "aviso", "error"], { error: "nivel: info, aviso o error" }).optional(), credencial: credencialRef.optional(),
  }),
  salida: z.looseObject({
    ...comunesDeSalida,
    desenlace: z.enum(DESENLACES_DECL, { error: `desenlace: ${DESENLACES_DECL.join(", ")}` }),
    motivo: z.string().optional(), destino: z.string().optional(), plantilla_respuesta: z.string().optional(),
    campos: z.union([z.array(z.string().min(1)), z.record(z.string(), z.string())], { error: "campos: lista de variables o {nombre: plantilla}" }).optional(),
    archivos: archivosRef("archivos").optional(),
  }),
  // ---------------------------------------------- archivos que salen (puente/nodos_producto.py)
  responder_correo: conf({
    cuerpo: textoReq("cuerpo"), html: z.string().optional(), cc: z.string().optional(), para: z.string().optional(), asunto: z.string().optional(),
    adjuntos: archivosRef("adjuntos").optional(), incluir_original: opcional, credencial: credencialRef.optional(), timeout_s: timeoutS,
  }),
  ia_imagen: conf({
    instrucciones: textoReq("instrucciones"), aspecto: z.enum(["1:1", "3:4", "4:3", "9:16", "16:9"], { error: "aspecto: 1:1, 3:4, 4:3, 9:16 o 16:9" }).optional(),
    tamano: z.enum(["cuadrado", "vertical", "horizontal", "historia", "panoramica"], { error: "tamano: cuadrado, vertical, horizontal, historia o panoramica" }).optional(),
    cantidad: entre("cantidad", 1, 4).optional(), estilo: z.string().optional(), negativo: z.string().optional(), nombre: z.string().optional(),
    proveedor: z.enum(["vertex", "gemini", "openai"], { error: "proveedor de imágenes: vertex, gemini u openai" }).optional(),
    modelo: z.string().trim().min(1, "modelo: el nombre de un modelo de imágenes").optional(), credencial: credencialRef.optional(),
  }).superRefine((c, ctx) => {
    const mal = (k: string, m: string) => ctx.addIssue({ code: "custom", path: [k], message: m });
    if (c.aspecto && c.tamano) mal("aspecto", "usa aspecto o tamano, no los dos");
    const prov = c.proveedor ?? "vertex";
    if (prov === "vertex" && c.credencial) mal("credencial", "con proveedor vertex no va credencial (usa la identidad del servicio)");
    if (prov !== "vertex" && !c.credencial) mal("credencial", `con proveedor ${prov} hace falta credencial: una conexión ia_${prov}`);
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
  if (c.continuar_si_falla === true && TERMINALES.has(n.tipo)) problema("continuar_si_falla", `un nodo ${n.tipo} no puede continuar si falla: no tiene por dónde seguir`);
  if (c.reintentos !== undefined && !TIPOS_CON_REINTENTOS.has(n.tipo)) problema("reintentos", "reintentos solo aplica a los nodos de IA, conectores, llamar_flujo, enviar, notificar y http");
  ia.revisarNodo(n.tipo, c, problema);  // proveedor, modelo y credencial de IA del nodo (forma; lo demás lo revisa el motor)
});

export const definicion = z.object({
  id: z.string().regex(/^[a-z][a-z0-9-]{1,40}$/, "id en minúsculas con guiones"),
  nombre: z.string().min(2).max(80),
  descripcion: z.string().max(800).nullish().transform((v) => v ?? ""),
  modelo: z.string().default("gemini-2.5-flash"),
  /** La IA del flujo: {usar: "global"} o {proveedor, modelo, credencial?, temperatura?}. Sin ella (flujos de antes): Vertex con «modelo». */
  ia: ia.iaFlujo.optional(),
  nodos: z.array(nodo).min(2).max(100),
  aristas: z.array(z.object({ desde: z.string(), hacia: z.string(), cuando: z.string().optional() })).max(300),
  /** Posición de cada nodo en el editor: {id_nodo: {x, y}}. No afecta la ejecución. */
  disenio: z.record(z.string(), z.object({ x: z.number().finite(), y: z.number().finite() })).optional(),
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
  /** La nota de la versión actual (restaurada, copia, renombrado…). */
  nota?: string;
  /** De código: el motor del que es instancia (pqrd o tutelas). Una copia corre el mismo código con lo suyo. */
  base?: string;
  /** Los de código originales (pqrd, tutelas): no se borran. */
  original?: boolean;
  /** De qué flujo es copia (si lo es). */
  copia_de?: string;
  /** Con qué arranca: el tipo del disparador (o «buzon_real» en los de código). */
  disparador?: string;
  /** Si tiene «Correspondencia» (arranca con un correo): los de código y los de entrada_correo o correo_nuevo. */
  correspondencia?: boolean;
  creado?: number;
}

const raiz = config.raiz;
const carpetaDeclarativos = () => join(config.datos, "flujos");
/** Las copias de los flujos de código: <estado>/flujos_codigo/<id>.json (y los nombres propios de los originales en _originales.json). */
const carpetaCodigo = () => join(config.datos, "flujos_codigo");

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
].map((f) => ({ ...f, tipo: "codigo" as const, base: f.id, original: true, disparador: "buzon_real", correspondencia: true }));

const declarativos = new Map<string, Flujo>();
/** Las copias de pqrd y tutelas: instancias propias (su entorno, compuerta, programación y corridas) del mismo motor. */
const copiasDeCodigo = new Map<string, Flujo>();
/** Nombre y descripción propios de los originales (si alguien los renombró). */
let originales: Record<string, { nombre?: string; descripcion?: string }> = {};

interface CopiaGuardada {
  id: string;
  base: string;
  nombre: string;
  descripcion: string;
  copia_de: string;
  creado: number;
  actualizado: number;
}

function aCopia(g: CopiaGuardada): Flujo | null {
  const b = DE_CODIGO.find((f) => f.id === g.base);
  if (!b) return null;
  return { ...b, id: g.id, nombre: g.nombre, descripcion: g.descripcion, original: false, copia_de: g.copia_de, creado: g.creado, actualizado: g.actualizado };
}

async function guardarCopia(f: Flujo): Promise<void> {
  await mkdir(carpetaCodigo(), { recursive: true });
  const g: CopiaGuardada = { id: f.id, base: f.base!, nombre: f.nombre, descripcion: f.descripcion, copia_de: f.copia_de ?? f.base!, creado: f.creado ?? Date.now(), actualizado: f.actualizado ?? Date.now() };
  await writeFile(join(carpetaCodigo(), `${f.id}.json`), JSON.stringify(g, null, 2), "utf8");
}

async function cargarCopias(): Promise<void> {
  copiasDeCodigo.clear();
  await mkdir(carpetaCodigo(), { recursive: true });
  try {
    originales = JSON.parse(await readFile(join(carpetaCodigo(), "_originales.json"), "utf8")) as typeof originales;
  } catch {
    originales = {};
  }
  for (const a of (await readdir(carpetaCodigo())).filter((x) => x.endsWith(".json") && !x.startsWith("_"))) {
    try {
      const f = aCopia(JSON.parse(await readFile(join(carpetaCodigo(), a), "utf8")) as CopiaGuardada);
      if (f && /^[a-z][a-z0-9-]{1,40}$/.test(f.id) && !DE_CODIGO.some((x) => x.id === f.id)) copiasDeCodigo.set(f.id, f);
    } catch {
      /* archivo dañado: se ignora */
    }
  }
}

/** De qué motor de código es instancia un flujo (pqrd, tutelas o null si es declarativo o no existe). */
export function baseDe(id: string): string | null {
  return DE_CODIGO.find((f) => f.id === id)?.id ?? copiasDeCodigo.get(id)?.base ?? null;
}

export const esOriginal = (id: string) => DE_CODIGO.some((f) => f.id === id);

interface Guardado {
  definicion: unknown;
  version: number;
  actualizado: number;
  nota?: string;
}

export async function cargar(): Promise<void> {
  await cargarCopias();
  // Cada almacén por flujo sabe de qué motor es una copia (nombres del job, compuerta, programación).
  entornos.enlace.baseDe = (id) => baseDe(id) ?? id;
  compuerta.enlace.tieneCompuerta = (id) => baseDe(id) !== null;
  programadorEnlace.baseDe = (id) => baseDe(id);
  await mkdir(carpetaDeclarativos(), { recursive: true });
  for (const a of (await readdir(carpetaDeclarativos())).filter((x) => x.endsWith(".json"))) {
    try {
      const guardado = JSON.parse(await readFile(join(carpetaDeclarativos(), a), "utf8")) as Guardado;
      const d = definicion.parse(guardado.definicion);
      declarativos.set(d.id, aFlujo(d, guardado.version, guardado.actualizado, guardado.nota));
    } catch {
      /* archivo dañado: se ignora */
    }
  }
}

function aFlujo(d: Definicion, version: number, actualizado: number, nota?: string): Flujo {
  const disparador = disparadorDe(d)?.tipo;
  const copia = /^copia de ([a-z][a-z0-9-]{1,40}) v\d+$/.exec(nota ?? "")?.[1];
  return {
    id: d.id, nombre: d.nombre, descripcion: d.descripcion, tipo: "declarativo", buzonReal: false, lote: false,
    trabajador: resolve(raiz, "puente", "flujo_declarativo.py"), version, actualizado, definicion: d, ...(nota ? { nota } : {}),
    ...(disparador ? { disparador } : {}), correspondencia: DISPARADORES_DE_CORREO.has(disparador ?? ""), ...(copia ? { copia_de: copia } : {}),
  };
}

export function listar(): Flujo[] {
  const propios = DE_CODIGO.filter((f) => existsSync(f.trabajador)).map((f) => ({ ...f, ...(originales[f.id] ?? {}) }));
  const copias = [...copiasDeCodigo.values()].filter((f) => existsSync(f.trabajador)).sort((a, b) => a.nombre.localeCompare(b.nombre));
  return [...propios, ...copias, ...[...declarativos.values()].sort((a, b) => a.nombre.localeCompare(b.nombre))];
}

export function obtener(id: string): Flujo | undefined {
  return listar().find((f) => f.id === id);
}

/** pqrd, tutelas y sus copias. */
export const esDeCodigo = (id: string) => baseDe(id) !== null;

/** El disparador de una definición (el único nodo de tipo disparador). */
export function disparadorDe(d: Definicion): Definicion["nodos"][number] | undefined {
  return d.nodos.find((n) => DISPARADORES.has(n.tipo));
}

/** Los problemas de una definición, con el mismo validador que la ejecuta (y los nombres del entorno de ese flujo). */
export function validar(entrada: unknown, nombresExtra: string[] = []): Promise<{ ok: boolean; problemas: string[]; definicion?: Definicion }> {
  const r = definicion.safeParse(entrada);
  if (!r.success) return Promise.resolve({ ok: false, problemas: r.error.issues.map((i) => `${i.path.join(".") || "flujo"}: ${i.message}`) });
  const nombres = [...new Set([...entornos.nombresDeCache(r.data.id), ...nombresExtra])];
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
    p.stdin.end(JSON.stringify({ definicion: r.data, entorno: nombres, credenciales: ia.metadatosCredenciales(), ia_global: ia.obtenerGlobal() }));
  });
}

/** Un webhook sin secreto recibe uno (o conserva el que ya tenía su versión anterior). */
function conSecretos(entrada: unknown, previo?: Definicion): unknown {
  if (!entrada || typeof entrada !== "object" || !Array.isArray((entrada as { nodos?: unknown }).nodos)) return entrada;
  const anterior = previo?.nodos.find((n) => n.tipo === "webhook")?.config as { secreto?: string } | undefined;
  const e = structuredClone(entrada) as { nodos: { tipo?: string; config?: Record<string, unknown> | null }[] };
  for (const n of e.nodos) {
    if (n?.tipo !== "webhook") continue;
    n.config ??= {};
    if (typeof n.config.secreto !== "string" || !n.config.secreto) n.config.secreto = anterior?.secreto ?? randomBytes(24).toString("base64url");
  }
  return e;
}

const rutaVersion = (id: string, v: number) => join(carpetaDeclarativos(), "versiones", `${id}.v${v}.json`);

/** Guarda una definición válida (crea o reemplaza) y deja la versión anterior (con su nota). */
export async function guardar(entrada: unknown, nota?: string, nombresExtra: string[] = []): Promise<{ ok: boolean; problemas: string[]; flujo?: Flujo }> {
  const id = (entrada as { id?: unknown })?.id;
  const previo = typeof id === "string" ? declarativos.get(id) : undefined;
  const v = await validar(conSecretos(ia.conIaPorDefecto(entrada, !previo), previo?.definicion), nombresExtra);
  if (!v.ok || !v.definicion) return { ok: false, problemas: v.problemas };
  if (esDeCodigo(v.definicion.id)) return { ok: false, problemas: [`«${v.definicion.id}» es un flujo de código: elige otro id`] };
  const version = (previo?.version ?? 0) + 1;
  const actualizado = Date.now();
  await mkdir(join(carpetaDeclarativos(), "versiones"), { recursive: true });
  if (previo?.definicion) {
    const anterior: Guardado = { definicion: previo.definicion, version: previo.version ?? 0, actualizado: previo.actualizado ?? 0, ...(previo.nota ? { nota: previo.nota } : {}) };
    await writeFile(rutaVersion(previo.id, previo.version ?? 0), JSON.stringify(anterior, null, 2), "utf8");
  }
  const notaLimpia = typeof nota === "string" && nota.trim() ? nota.trim().slice(0, 300) : undefined;
  await writeFile(join(carpetaDeclarativos(), `${v.definicion.id}.json`), JSON.stringify({ definicion: v.definicion, version, actualizado, ...(notaLimpia ? { nota: notaLimpia } : {}) }, null, 2), "utf8");
  const f = aFlujo(v.definicion, version, actualizado, notaLimpia);
  declarativos.set(f.id, f);
  return { ok: true, problemas: [], flujo: f };
}

export async function borrar(id: string): Promise<boolean> {
  if (copiasDeCodigo.has(id)) {
    // una copia de pqrd o tutelas: su registro y su entorno (lo demás lo limpia el servidor)
    copiasDeCodigo.delete(id);
    await unlink(join(carpetaCodigo(), `${id}.json`)).catch(() => undefined);
    await entornos.borrarTodo(id);
    return true;
  }
  if (!declarativos.has(id)) return false;
  declarativos.delete(id);
  await unlink(join(carpetaDeclarativos(), `${id}.json`)).catch(() => undefined);
  // sus versiones tampoco: un flujo nuevo con el mismo id empieza su propia historia
  for (const n of await readdir(join(carpetaDeclarativos(), "versiones")).catch(() => [] as string[]))
    if (n.startsWith(`${id}.v`) && /^\d+\.json$/.test(n.slice(id.length + 2))) await unlink(join(carpetaDeclarativos(), "versiones", n)).catch(() => undefined);
  await entornos.borrarTodo(id);  // los secretos de un flujo borrado no se quedan
  return true;
}

// ------------------------------------------------------- versiones y copias

export interface Version {
  version: number;
  actualizado: number;
  nota?: string;
  actual?: boolean;
}

async function leerVersion(id: string, v: number): Promise<{ definicion: unknown; actualizado: number; nota?: string } | null> {
  try {
    const crudo = JSON.parse(await readFile(rutaVersion(id, v), "utf8")) as Record<string, unknown>;
    // Las versiones de antes guardaban la definición sola.
    if (crudo && typeof crudo === "object" && "definicion" in crudo && "version" in crudo) return crudo as unknown as Guardado;
    return { definicion: crudo, actualizado: statSync(rutaVersion(id, v)).mtimeMs };
  } catch {
    return null;
  }
}

export async function versiones(id: string): Promise<Version[] | null> {
  const f = declarativos.get(id);
  if (!f) return null;
  const nombres = await readdir(join(carpetaDeclarativos(), "versiones")).catch(() => [] as string[]);
  const r: Version[] = [];
  for (const n of nombres) {
    const m = new RegExp(`^${id}\\.v(\\d+)\\.json$`).exec(n);
    if (!m) continue;
    const x = await leerVersion(id, Number(m[1]));
    if (x && Number(m[1]) !== f.version) r.push({ version: Number(m[1]), actualizado: x.actualizado, ...(x.nota ? { nota: x.nota } : {}) });
  }
  r.push({ version: f.version ?? 1, actualizado: f.actualizado ?? 0, ...(f.nota ? { nota: f.nota } : {}), actual: true });
  return r.sort((a, b) => b.version - a.version);
}

type Operacion = { ok: true; flujo: Flujo } | { ok: false; codigo: number; error: string; problemas?: string[] };

/** Vuelve a una versión anterior: la guarda como versión nueva (no se pierde la historia). */
export async function restaurar(id: string, version: number): Promise<Operacion> {
  const f = declarativos.get(id);
  if (!f) return { ok: false, codigo: 404, error: esDeCodigo(id) ? "los flujos de código no tienen versiones" : "ese flujo no existe" };
  if (!Number.isInteger(version) || version < 1) return { ok: false, codigo: 400, error: "version tiene que ser un entero" };
  if (version === f.version) return { ok: true, flujo: f };
  const x = await leerVersion(id, version);
  if (!x) return { ok: false, codigo: 404, error: `no existe la versión ${version}` };
  const r = await guardar({ ...(x.definicion as object), id }, `restaurada la versión ${version}`);
  return r.ok ? { ok: true, flujo: r.flujo! } : { ok: false, codigo: 400, error: "esa versión ya no es válida", problemas: r.problemas };
}

/** Copia un flujo con otro id y nombre. La copia nace apagada (programado/correo_nuevo) y con un webhook nuevo. */
export async function duplicar(id: string, idNuevo: unknown, nombre: unknown): Promise<Operacion> {
  const f = declarativos.get(id);
  if (!f?.definicion) return { ok: false, codigo: esDeCodigo(id) ? 400 : 404, error: esDeCodigo(id) ? "los flujos de código no se duplican" : "ese flujo no existe" };
  if (typeof idNuevo !== "string" || !/^[a-z][a-z0-9-]{1,40}$/.test(idNuevo)) return { ok: false, codigo: 400, error: "id_nuevo: minúsculas con guiones" };
  if (obtener(idNuevo)) return { ok: false, codigo: 409, error: `ya existe un flujo «${idNuevo}»` };
  const d = structuredClone(f.definicion);
  d.id = idNuevo;
  d.nombre = typeof nombre === "string" && nombre.trim() ? nombre.trim().slice(0, 80) : `${f.nombre} (copia)`;
  for (const n of d.nodos) {
    const c = n.config as Record<string, unknown>;
    if (n.tipo === "webhook") delete c.secreto;
    if (n.tipo === "programado" || n.tipo === "correo_nuevo") c.activo = false;
  }
  // Las variables de entorno no secretas se copian; las secretas no (las vuelve a poner una persona en la copia).
  const original = await entornos.listar(id);
  const r = await guardar(d, `copia de ${id} v${f.version}`, original.map((v) => v.nombre));
  if (r.ok) {
    for (const v of original) {
      if (v.secreto) await entornos.declararSecreto(idNuevo, v.nombre, v.descripcion);
      else await entornos.poner(idNuevo, v.nombre, { valor: v.valor ?? "", descripcion: v.descripcion }, false);
    }
    // los archivos que eligió en sus nodos (archivo_leer) van con la copia: es independiente
    await espacio.copiarArchivos(id, idNuevo);
  }
  return r.ok ? { ok: true, flujo: r.flujo! } : { ok: false, codigo: 400, error: "la copia no es válida", problemas: r.problemas };
}

/** Un id libre a partir de un nombre («Radicación de PQRD (copia)» → «radicacion-de-pqrd-copia», o con -2, -3…). */
export function idLibre(nombre: string): string {
  const base = nombre.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 36) || "flujo";
  const limpio = /^[a-z]/.test(base) ? base : `f-${base}`.slice(0, 36);
  let id = limpio.length >= 2 ? limpio : `${limpio}-1`;
  for (let i = 2; obtener(id) || existsSync(join(config.archivos, id)); i++) id = `${limpio}-${i}`;
  return id;
}

/**
 * Clona cualquier flujo y deja la copia lista para abrir:
 * - declarativo: su definición y su diseño, el entorno no secreto (los secretos quedan declarados sin valor:
 *   «falta valor») y sus archivos; nace apagado (programado/correo_nuevo) y con webhook nuevo;
 * - de código (pqrd, tutelas o una copia): una instancia nueva del MISMO motor con lo suyo: id y nombre propios,
 *   su entorno (copiado del original; los secretos se copian cifrados, marcados copiado_de), sus conexiones
 *   (las del alcance del original no pasan), su compuerta (apagada), su programación (apagada) y sus corridas.
 */
export async function clonar(id: string, nombre: unknown, idNuevo?: unknown): Promise<Operacion> {
  const f = obtener(id);
  if (!f) return { ok: false, codigo: 404, error: "ese flujo no existe" };
  const n = typeof nombre === "string" && nombre.trim().length >= 2 ? nombre.trim().slice(0, 80) : `${f.nombre} (copia)`;
  const nuevo = typeof idNuevo === "string" && idNuevo ? idNuevo : idLibre(n);
  if (!/^[a-z][a-z0-9-]{1,40}$/.test(nuevo)) return { ok: false, codigo: 400, error: "id_nuevo: minúsculas con guiones" };
  if (obtener(nuevo)) return { ok: false, codigo: 409, error: `ya existe un flujo «${nuevo}»` };
  if (f.tipo === "declarativo") return duplicar(id, nuevo, n);
  const base = baseDe(id)!;
  const ahora = Date.now();
  const copia = aCopia({ id: nuevo, base, nombre: n, descripcion: f.descripcion, copia_de: id, creado: ahora, actualizado: ahora })!;
  await guardarCopia(copia);
  copiasDeCodigo.set(nuevo, copia);
  await entornos.copiar(id, nuevo);
  return { ok: true, flujo: copia };
}

/** Cambia nombre o descripción sin mandar toda la definición. */
export async function renombrar(id: string, cambios: { nombre?: unknown; descripcion?: unknown }): Promise<Operacion> {
  if (esDeCodigo(id)) {
    // pqrd, tutelas y sus copias: el nombre y la descripción son de la plataforma (el código no cambia)
    if (cambios.nombre === undefined && cambios.descripcion === undefined) return { ok: false, codigo: 400, error: "manda nombre o descripcion" };
    if (cambios.nombre !== undefined && (typeof cambios.nombre !== "string" || cambios.nombre.trim().length < 2)) return { ok: false, codigo: 400, error: "nombre: entre 2 y 80 caracteres" };
    if (cambios.descripcion !== undefined && typeof cambios.descripcion !== "string") return { ok: false, codigo: 400, error: "descripcion tiene que ser texto" };
    const extra = { ...(typeof cambios.nombre === "string" ? { nombre: cambios.nombre.trim().slice(0, 80) } : {}), ...(typeof cambios.descripcion === "string" ? { descripcion: cambios.descripcion.slice(0, 800) } : {}) };
    const copia = copiasDeCodigo.get(id);
    if (copia) {
      const sig = { ...copia, ...extra, actualizado: Date.now() };
      await guardarCopia(sig);
      copiasDeCodigo.set(id, sig);
      return { ok: true, flujo: sig };
    }
    originales = { ...originales, [id]: { ...(originales[id] ?? {}), ...extra } };
    await mkdir(carpetaCodigo(), { recursive: true });
    await writeFile(join(carpetaCodigo(), "_originales.json"), JSON.stringify(originales, null, 2), "utf8");
    return { ok: true, flujo: obtener(id)! };
  }
  const f = declarativos.get(id);
  if (!f?.definicion) return { ok: false, codigo: 404, error: "ese flujo no existe" };
  const d = structuredClone(f.definicion);
  if (cambios.nombre !== undefined) {
    if (typeof cambios.nombre !== "string" || cambios.nombre.trim().length < 2) return { ok: false, codigo: 400, error: "nombre: entre 2 y 80 caracteres" };
    d.nombre = cambios.nombre.trim().slice(0, 80);
  }
  if (cambios.descripcion !== undefined) {
    if (typeof cambios.descripcion !== "string") return { ok: false, codigo: 400, error: "descripcion tiene que ser texto" };
    d.descripcion = cambios.descripcion.slice(0, 800);
  }
  if (cambios.nombre === undefined && cambios.descripcion === undefined) return { ok: false, codigo: 400, error: "manda nombre o descripcion" };
  const r = await guardar(d, "nombre o descripción cambiados");
  return r.ok ? { ok: true, flujo: r.flujo! } : { ok: false, codigo: 400, error: "el flujo no es válido", problemas: r.problemas };
}

// --------------------------------------------------- lo que recibe una corrida

const ENV_VIEJA = /^[A-Z][A-Z0-9_]{2,63}$/;

/** Las credenciales guardadas que nombra una definición (la forma vieja de http, una variable de entorno, no cuenta). */
export function credencialesUsadas(d: Definicion): string[] {
  // también la de la IA que redacta un documento o arma una hoja (config.desde_ia.credencial)
  const deIaDocumentos = d.nodos.map((n) => ((n.config as { desde_ia?: { credencial?: unknown } }).desde_ia ?? {}).credencial);
  return [...new Set([...d.nodos.map((n) => (n.config as { credencial?: unknown }).credencial), ...deIaDocumentos]
    .filter((x): x is string => typeof x === "string" && !ENV_VIEJA.test(x)).concat(ia.credencialesDeIa(d)))];
}

function subflujosDe(d: Definicion, pila: string[], acumulado: Record<string, unknown> = {}): Record<string, unknown> {
  if (pila.length > 4) return acumulado;
  for (const n of d.nodos) {
    if (n.tipo !== "llamar_flujo" && n.tipo !== "subflujo") continue;
    const id = String((n.config as { flujo?: unknown }).flujo ?? "");
    const sub = declarativos.get(id);
    if (!sub?.definicion || id in acumulado || pila.includes(id)) continue;
    const ent = entornos.paraCorrida(id);
    acumulado[id] = { definicion: sub.definicion, credenciales: credenciales.paraCorrida(id, credencialesUsadas(sub.definicion)), entorno: ent.valores, entorno_secretos: ent.secretos };
    subflujosDe(sub.definicion, [...pila, id], acumulado);
  }
  return acumulado;
}

/**
 * Lo que se agrega a la orden de cada corrida. Declarativos: definición, entrada,
 * credenciales (solo las que usa y puede usar), entorno del flujo y flujos que llama.
 * De código: su entorno propio, de las variables que el trabajador acepta en caliente.
 */
/**
 * Lo que otros módulos agregan a la orden de una corrida declarativa (p. ej. aprobaciones.ts: «reanudar» para seguir
 * la corrida que esperó una decisión; aplazadas.ts: «aplazamiento» con el intento). Cada uno devuelve undefined si no le toca.
 */
export const extrasDeOrden: ((flujo: string, corrida: Corrida) => Record<string, unknown> | undefined)[] = [];

export function ordenPara(id: string, corrida: Corrida): Record<string, unknown> {
  const f = obtener(id);
  if (!f) return {};
  const base = ordenBase(id, f, corrida);
  if (f.tipo !== "declarativo" || !f.definicion) return base;
  return { ...base, ...Object.assign({}, ...extrasDeOrden.map((x) => x(id, corrida) ?? {})) };
}

function ordenBase(id: string, f: Flujo, corrida: Corrida): Record<string, unknown> {
  if (f.tipo === "codigo") {
    const pqrd = baseDe(id) === "pqrd";
    const permitidas = pqrd ? ajustables().entorno : new Set<string>();
    const propias = Object.fromEntries(Object.entries(entornos.paraCorrida(id).valores).filter(([k]) => permitidas.has(k)));
    return { entorno: { ...(pqrd ? corrida.configuracion.entorno : {}), ...propias } };
  }
  if (!f.definicion) return {};
  const ent = entornos.paraCorrida(id);
  return {
    definicion: f.definicion, flujo: id, entrada: corrida.entrada ?? {}, origen: corrida.origen ?? "bandeja",
    // los archivos subidos para esta corrida, solo del espacio de ESTE flujo
    archivos: espacio.resolverArchivos(id, corrida.entrada, f.definicion).map(({ id: a, nombre, tipo, tamano, sha256, ruta }) => ({ id: a, nombre, tipo, tamano, sha256, ruta })),
    credenciales: credenciales.paraCorrida(id, credencialesUsadas(f.definicion)), entorno: ent.valores, entorno_secretos: ent.secretos, ia_global: ia.obtenerGlobal(),
    subflujos: subflujosDe(f.definicion, [id]),
    // para firmar enlaces de descarga con vencimiento (Teams, Slack, webhooks): la clave sale enmascarada de todo
    enlaces: descargas.paraOrden(),
  };
}

/**
 * La orden de «Probar este paso»: como la de una corrida, pero ninguna credencial escribe (permitir_escritura apagado,
 * aunque una persona lo haya habilitado) y sin subflujos (la prueba no llama a otros flujos).
 */
export function ordenDePrueba(id: string, d: Definicion): Record<string, unknown> {
  const ent = entornos.paraCorrida(id);
  const creds = credenciales.paraCorrida(id, credencialesUsadas(d));
  return {
    definicion: d, flujo: id, credenciales: Object.fromEntries(Object.entries(creds).map(([k, v]) => [k, { ...v, permitir_escritura: false }])),
    entorno: ent.valores, entorno_secretos: ent.secretos, ia_global: ia.obtenerGlobal(), enlaces: descargas.paraOrden(),
  };
}

/** Para el sondeo de correo_nuevo: la definición y las credenciales que necesita. */
export function ordenDeSondeo(id: string): Record<string, unknown> | null {
  const f = declarativos.get(id);
  if (!f?.definicion) return null;
  return { flujo: id, definicion: f.definicion, credenciales: credenciales.paraCorrida(id, credencialesUsadas(f.definicion)) };
}

/** Para leer el «Buzón del flujo» (entrada_correo con credencial): la definición y sus credenciales. */
export function ordenDeBuzon(id: string): Record<string, unknown> | null {
  const f = declarativos.get(id);
  const c = f?.definicion ? (disparadorDe(f.definicion)?.config as { credencial?: string } | undefined) : undefined;
  if (!f?.definicion || disparadorDe(f.definicion)?.tipo !== "entrada_correo" || !c?.credencial) return null;
  return { flujo: id, definicion: f.definicion, credenciales: credenciales.paraCorrida(id, credencialesUsadas(f.definicion)) };
}

// ------------------------------------------------------ catálogo de tipos

let tiposCache: { mtime: number; tipos: unknown[] } | null = null;

/** GET /api/nodos/tipos: lo arma la biblioteca de nodos de Python (la única fuente) y se guarda en memoria. */
export async function tiposDeNodo(): Promise<unknown[]> {
  const archivo = resolve(raiz, "puente", "nodos_declarativos.py");
  const mtime = existsSync(archivo) ? statSync(archivo).mtimeMs : 0;
  if (tiposCache && tiposCache.mtime === mtime) return tiposCache.tipos;
  const tipos = await new Promise<unknown[]>((resolver, rechazar) => {
    const p = spawn(config.python, [archivo, "tipos"], { env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let salida = "";
    let errores = "";
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.stderr.setEncoding("utf8").on("data", (t: string) => (errores += t));
    p.on("exit", (codigo) => {
      try {
        resolver(JSON.parse(salida) as unknown[]);
      } catch {
        rechazar(new Error(`no se pudo leer la biblioteca de nodos (código ${codigo}): ${errores.slice(-300)}`));
      }
    });
  });
  tiposCache = { mtime, tipos };
  return tipos;
}

// ------------------------------------------------------------ manifiesto

const hostDe = (url: unknown): string | null => {
  if (typeof url !== "string" || url.trim().startsWith("{{")) return null;
  try {
    return new URL(url.replace(/\{\{[^}]*\}\}/g, "x")).hostname || null;
  } catch {
    return null;
  }
};

/**
 * El documento que se revisa antes de llevar un flujo a producción: disparador,
 * nodos, conexiones (credenciales con su tipo, alcance y destino; nunca secretos),
 * hosts, variables de entorno (las secretas marcadas, sin valores), escrituras y si
 * están habilitadas, programación y ruta del webhook.
 */
export async function manifiesto(id: string): Promise<Record<string, unknown> | null> {
  const f = obtener(id);
  if (!f) return null;
  const variables = (await entornos.listar(id)).map((v) => ({ nombre: v.nombre, secreto: v.secreto, definido: v.definido, descripcion: v.descripcion }));
  const base = { manifiesto: 1, generado: new Date().toISOString(), flujo: { id: f.id, nombre: f.nombre, descripcion: f.descripcion, tipo: f.tipo, version: f.version ?? null, actualizado: f.actualizado ?? null } };
  if (f.tipo === "codigo" || !f.definicion) {
    const prod = PRODUCCION[baseDe(f.id) ?? f.id];
    const c = compuerta.obtener(f.id);
    const real = compuerta.radicacionReal(f.id);
    const p = programacionDe(f.id);
    return {
      ...base,
      disparador: prod?.disparador ?? { tipo: "buzon_real", descripcion: "lo corre el job o el visor sobre el buzón de solo lectura de la plataforma" },
      // La compuerta de radicación: apagada, todo se simula; encendida (con la segunda llave del despliegue), escribe como el job.
      compuerta: {
        radicacion_real: real, encendida_en_la_plataforma: c.radicacion_real, permitido_en_despliegue: c.permitido_en_despliegue,
        estado: compuerta.rotulo(f.id), ...(c.cambiado_por ? { cambiado_por: c.cambiado_por } : {}), ...(c.cambiado ? { cambiado: c.cambiado } : {}),
        como_se_enciende: "Una persona la enciende en la web con confirmación explícita (X-Confirmado-Por-Persona: si) y solo si el contenedor tiene PERMITIR_RADICACION_REAL=si.",
      },
      ...(p ? { programacion: { ...p, como_el_job: true, tope_por_corrida_s: 3600, sin_solapes: true, ultima_ejecucion: estadoDelProgramador()[`j:${f.id}`] ?? null } } : {}),
      lecturas_con_la_compuerta_apagada: LECTURAS_EN_SIMULACION[baseDe(f.id) ?? f.id] ?? [],
      ...(f.copia_de ? { copia_de: f.copia_de, instancia: `copia de «${f.copia_de}»: el mismo motor de ${baseDe(f.id)}, con su entorno, su compuerta y su programación propios` } : {}),
      ...(prod?.ia ? { ia: prod.ia } : {}),
      ejecucion: prod?.ejecucion,
      // Las variables propias del flujo en esta plataforma y las que usa el job en producción (sin valores).
      entorno: variables,
      entorno_produccion: prod?.entorno ?? [],
      conexiones: prod?.conexiones ?? [],
      escrituras: (prod?.escrituras ?? [{ donde: "SGDEA", que: "Radicación", en_este_visor: "simulada (MODO_TEST)" }])
        .map((x) => (real ? { ...x, en_este_visor: "ESCRIBE DE VERDAD: la compuerta de radicación está encendida" } : x)),
      despliegue: prod?.despliegue,
      verificado_en_job: prod?.verificado_en_job,
      observaciones: prod?.observaciones ?? [],
    };
  }
  const d = f.definicion;
  const disp = disparadorDe(d);
  const cd = (disp?.config ?? {}) as Record<string, unknown>;
  const texto = JSON.stringify(d);
  const usadasEntorno = [...new Set([...texto.matchAll(/\{\{\s*entorno\.([A-Z][A-Z0-9_]*)/g)].map((m) => m[1]!))];
  const porNodo = (pred: (c: Record<string, unknown>, t: string) => boolean) => d.nodos.filter((n) => pred(n.config as Record<string, unknown>, n.tipo)).map((n) => n.id);
  const conexiones = credencialesUsadas(d).map((nombre) => {
    const x = credenciales.destino(nombre, id);
    const nodos = porNodo((c) => c.credencial === nombre);
    return { credencial: nombre, ...x, nodos, escribe: d.nodos.some((n) => nodos.includes(n.id) && TIPOS_ESCRITURA.has(n.tipo) && (n.tipo !== "http" || String((n.config as { metodo?: string }).metodo ?? "GET") !== "GET")) };
  });
  const hosts = d.nodos.flatMap((n) => {
    const c = n.config as Record<string, unknown>;
    if (n.tipo !== "http" && !(n.tipo === "hoja_leer" && c.origen === "url")) return [];
    return [{ nodo: n.id, metodo: String(c.metodo ?? "GET"), host: hostDe(c.url) ?? "(dinámico: sale de una plantilla)", credencial: c.credencial ?? null }];
  });
  const escrituras = d.nodos.filter((n) => TIPOS_ESCRITURA.has(n.tipo)).flatMap((n) => {
    const c = n.config as Record<string, unknown>;
    if (n.tipo === "http" && String(c.metodo ?? "GET") === "GET") return [];
    const cred = typeof c.credencial === "string" && !ENV_VIEJA.test(c.credencial) ? credenciales.destino(c.credencial, id) : null;
    const habilitada = n.tipo === "notificar" || n.tipo === "enviar" ? false : !!(cred?.usable && cred.permitir_escritura);
    return [{ nodo: n.id, tipo: n.tipo, credencial: c.credencial ?? null, habilitada,
      descripcion: n.tipo === "notificar" || n.tipo === "enviar" ? "siempre simulada (nunca envía)" : habilitada ? "ESCRIBE DE VERDAD: una persona habilitó la escritura de la credencial" : "bloqueada: se arma y se anota, no sale" }];
  });
  return {
    ...base,
    disparador: {
      tipo: disp?.tipo ?? null,
      ...(disp?.tipo === "programado" ? { programacion: { cron: cd.cron ?? null, cada_minutos: cd.cada_minutos ?? null, zona: cd.zona ?? "America/Bogota", activo: cd.activo === true } } : {}),
      ...(disp?.tipo === "webhook" ? { webhook: { ruta: `/api/ganchos/${id}/<secreto>`, metodo: "POST", secreto_definido: typeof cd.secreto === "string", responder: cd.responder === true, esperar_ms: cd.esperar_ms ?? 10000 } } : {}),
      ...(disp?.tipo === "correo_nuevo" ? { correo_nuevo: { credencial: cd.credencial, carpeta: cd.carpeta ?? "INBOX", cada_minutos: cd.cada_minutos ?? 5, activo: cd.activo === true, solo_lectura: true } } : {}),
      ...(disp?.tipo === "entrada_manual" ? { campos: cd.campos ?? [] } : {}),
      ...(disp?.tipo === "entrada_archivos" ? { archivos: { ...(() => { const r = espacio.reglasDe(d); return { tipos_permitidos: [...r.tipos], max_mb: r.maxMb, max_archivos: r.maxArchivos }; })(), video_y_audio: "siempre rechazados (extensión y contenido)" } } : {}),
      ...(disp?.tipo === "entrada_correo" ? { correo: { bandeja_de_prueba: cd.bandeja_prueba !== false, buzon: cd.credencial ? { credencial: cd.credencial, carpeta: cd.carpeta ?? "INBOX", solo_lectura: true } : null } } : {}),
    },
    nodos: d.nodos.map((n) => ({ id: n.id, tipo: n.tipo, nombre: n.nombre, escritura: TIPOS_ESCRITURA.has(n.tipo), ...((n.config as { credencial?: unknown }).credencial ? { credencial: (n.config as { credencial?: unknown }).credencial } : {}) })),
    conexiones,
    ia: ia.manifiestoIa(d),
    hosts_http: hosts,
    entorno: variables.map((v) => ({ ...v, usado: usadasEntorno.includes(v.nombre) })),
    entorno_sin_definir: usadasEntorno.filter((n) => !variables.some((v) => v.nombre === n)),
    escrituras,
    flujos_llamados: [...new Set(d.nodos.filter((n) => n.tipo === "llamar_flujo" || n.tipo === "subflujo").map((n) => String((n.config as { flujo?: unknown }).flujo)))],
    archivos: d.nodos.filter((n) => n.tipo === "archivo_leer" || n.tipo === "archivo_guardar").map((n) => ({ nodo: n.id, tipo: n.tipo, ...((n.config as { archivo?: string }).archivo ? { archivo: (n.config as { archivo?: string }).archivo } : { ruta: (n.config as { ruta?: unknown }).ruta }) })),
    tablas: d.nodos.filter((n) => n.tipo === "tabla_guardar" || n.tipo === "tabla_consultar").map((n) => ({ nodo: n.id, tabla: (n.config as { tabla?: unknown }).tabla, ambito: (n.config as { ambito?: unknown }).ambito ?? "flujo" })),
    limites: [
      "Las escrituras afuera solo salen si una persona habilitó la escritura de la credencial (X-Confirmado-Por-Persona).",
      "Bloqueo duro: hosts de positiva.gov.co, SGDEA, metadatos, IP privadas y de loopback; buzones de PQRD y tutelas.",
      "Archivos solo dentro de /datos/archivos; SQL de una sola sentencia, solo lectura en sql_consulta, 20 s y 500 filas por defecto.",
      "Secretos (credenciales y entorno secreto) solo llegan a la corrida y salen enmascarados en eventos, resultados y consola.",
    ],
  };
}

// ------------------------------------------------------------------ catálogos

const CATEGORIA: Record<TipoDeNodo, string> = {
  entrada_correo: "entrada", leer_adjuntos: "ocr", filtro: "filtro", condicion: "ruta", ia_clasificar: "ia", ia_decidir: "ia",
  ia_extraer: "ia", extraer_regex: "busqueda", validar: "validacion", transformar: "datos", buscar_en_tabla: "datos",
  bifurcar_por_valor: "ruta", ia_generar: "ia", resumir: "ia", ia_redactar: "ia", notificar: "integracion", enviar: "integracion",
  http: "integracion", salida: "salida",
  entrada_manual: "entrada", programado: "entrada", webhook: "entrada", correo_nuevo: "entrada", entrada_archivos: "entrada",
  para_cada: "logica", fin_ciclo: "logica", paralelo: "logica", unir: "logica", esperar: "logica", llamar_flujo: "logica",
  expresion: "datos", json: "datos", fecha: "datos", tabla_guardar: "datos", tabla_consultar: "datos", archivo_leer: "datos", archivo_guardar: "datos",
  correo_leer: "conector", correo_enviar: "conector", drive_listar: "conector", drive_leer: "conector", drive_subir: "conector", hoja_leer: "conector",
  sql_consulta: "conector", sql_ejecutar: "conector", respuesta_webhook: "integracion",
  aprobacion_humana: "logica", documento_generar: "documentos", pdf_unir: "documentos", pdf_dividir: "documentos", documento_convertir: "documentos",
  documento_clasificar: "ia", documento_comparar: "documentos", anonimizar: "validacion", hoja_escribir: "conector", mensaje_teams: "integracion",
  mensaje_slack: "integracion", webhook_saliente: "integracion", traducir: "ia", resumir_largo: "ia", preguntar_documentos: "ia", extraer_tablas: "ia",
  deduplicar: "datos", agrupar: "datos", ordenar_filtrar: "datos", mapear: "datos", validar_contra: "validacion", subflujo: "logica",
  reintentar_mas_tarde: "logica", limite_tasa: "logica", ventana_horario: "logica", detener_con_alerta: "salida",
  responder_correo: "conector", ia_imagen: "ia",
};
const NOMBRE_TIPO: Record<TipoDeNodo, string> = {
  entrada_correo: "Entrada de correo", leer_adjuntos: "Leer adjuntos", filtro: "Filtro", condicion: "Condición",
  ia_clasificar: "IA · clasificar", ia_decidir: "IA · decidir", ia_extraer: "IA · extraer datos", extraer_regex: "Extraer con regex",
  validar: "Validación", transformar: "Transformar", buscar_en_tabla: "Buscar en tabla", bifurcar_por_valor: "Bifurcar por valor",
  ia_generar: "IA · generar", resumir: "IA · resumir", ia_redactar: "IA · redactar", notificar: "Notificar (simulado)",
  enviar: "Enviar respuesta (simulado)", http: "Petición HTTP", salida: "Salida",
  entrada_manual: "Entrada manual", programado: "Programado", webhook: "Webhook", correo_nuevo: "Correo nuevo", entrada_archivos: "Entrada de archivos",
  para_cada: "Para cada", fin_ciclo: "Fin del ciclo", paralelo: "Ramas", unir: "Unir ramas", esperar: "Esperar", llamar_flujo: "Llamar flujo",
  expresion: "Expresión", json: "JSON", fecha: "Fecha", tabla_guardar: "Guardar en tabla", tabla_consultar: "Consultar tabla",
  archivo_leer: "Leer archivo", archivo_guardar: "Guardar archivo", correo_leer: "Leer correos", correo_enviar: "Enviar correo",
  drive_listar: "Drive · listar", drive_leer: "Drive · leer", drive_subir: "Drive · subir", hoja_leer: "Leer hoja de cálculo",
  sql_consulta: "SQL · consultar", sql_ejecutar: "SQL · ejecutar", respuesta_webhook: "Responder al webhook",
  aprobacion_humana: "Aprobación humana", documento_generar: "Generar documento", pdf_unir: "Unir PDF", pdf_dividir: "Dividir PDF",
  documento_convertir: "Convertir documento", documento_clasificar: "IA · tipo de documento", documento_comparar: "Comparar documentos",
  anonimizar: "Anonimizar datos personales", hoja_escribir: "Escribir hoja de cálculo", mensaje_teams: "Mensaje a Teams", mensaje_slack: "Mensaje a Slack",
  webhook_saliente: "Webhook saliente", traducir: "IA · traducir", resumir_largo: "IA · resumir documento largo", preguntar_documentos: "IA · preguntar a documentos",
  extraer_tablas: "IA · extraer tablas", deduplicar: "Deduplicar", agrupar: "Agrupar y sumar", ordenar_filtrar: "Ordenar y filtrar", mapear: "Mapear campos",
  validar_contra: "Validar contra lista, tabla o SQL", subflujo: "Subflujo", reintentar_mas_tarde: "Reintentar más tarde", limite_tasa: "Límite de tasa",
  ventana_horario: "Ventana de horario", detener_con_alerta: "Detener con alerta",
  responder_correo: "Responder correo", ia_imagen: "IA · generar imagen",
};

const valorDe = (x: unknown, clave = "valor") => (typeof x === "object" && x ? String((x as Record<string, unknown>)[clave] ?? "") : String(x));
const lista = (x: unknown) => (Array.isArray(x) ? (x as unknown[]) : []);
const corto = (x: unknown, n = 160) => { const t = typeof x === "string" ? x : JSON.stringify(x ?? ""); return t.length > n ? `${t.slice(0, n)}…` : t; };

/** «asunto o cuerpo contiene_alguna «tutela | desacato»» con la forma de comparar si no es la flexible. */
function describirCondicion(x: Record<string, unknown>): string {
  const campos = Array.isArray(x.campos) ? (x.campos as unknown[]).map(String) : [];
  if (typeof x.campo === "string" && x.campo) campos.unshift(x.campo);
  const op = String(x.operador ?? "contiene");
  const t = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v));
  const valor = op === "vacio" || op === "no_vacio" ? ""
    : OPS_CON_LISTA.has(op) ? ` «${partir(vacio(x.valores) ? x.valor : x.valores).map(t).join(" | ")}»`
      : OPS_CON_RANGO.has(op) ? (Array.isArray(x.valor) ? ` «${t(x.valor[0])}» y «${t(x.valor[1])}»` : ` «${t(x.valor)}» y «${t(x.hasta)}»`)
        : ` «${t(x.valor ?? "")}»`;
  const comp = x.comparacion === "exacta" ? " (exacta)" : x.comparacion === "parecida" || op === "parecido" ? ` (parecida ≥ ${String(x.umbral ?? 0.85)})` : "";
  return `${campos.length ? campos.join(" o ") : "texto"} ${op}${valor}${comp}${x.contar && op.startsWith("longitud") ? ` (${String(x.contar)})` : ""}`;
}

/** « · solo dígitos, entre 6 y 10 caracteres» para un campo con longitud. */
function describirLongitud(f: Record<string, unknown>): string {
  const u = String(f.contar ?? "caracteres").replace("digitos", "dígitos");
  const partes = [f.solo_digitos === true ? "solo dígitos" : "",
    f.longitud !== undefined ? `exactamente ${String(f.longitud)} ${u}`
      : f.longitud_min !== undefined && f.longitud_max !== undefined ? `entre ${String(f.longitud_min)} y ${String(f.longitud_max)} ${u}`
        : f.longitud_min !== undefined ? `al menos ${String(f.longitud_min)} ${u}` : f.longitud_max !== undefined ? `como mucho ${String(f.longitud_max)} ${u}` : ""].filter(Boolean);
  return partes.length ? ` · ${partes.join(", ")}` : "";
}

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
      add(c.credencial ? `Sus correos: los de su buzón «${String(c.credencial)}» (${String(c.carpeta ?? "INBOX")}, solo lectura) y su bandeja de prueba` : "Sus correos: su bandeja de prueba (.eml o .msg que subes); ponle una credencial para leer su buzón");
      break;
    case "entrada_archivos":
      add(`Se corre con archivos subidos (hasta ${String(c.max_archivos ?? 20)}, ${String(c.max_mb ?? 25)} MB cada uno): ${lista(c.tipos_permitidos).length ? lista(c.tipos_permitidos).join(", ") : "imágenes, PDF, Word, Excel, PowerPoint, texto, correos y ZIP"}; nunca video ni audio`);
      add(c.leer === false ? "Los archivos se leen en un nodo leer_adjuntos" : `Los lee aquí${c.ocr === false ? " sin OCR" : " con OCR y visión"} → {{archivos.texto}}`);
      break;
    case "leer_adjuntos":
      add(`Lee ${lista(c.tipos).length ? `solo ${lista(c.tipos).join(", ")}` : "todos los adjuntos"}${c.nombre_patron ? ` cuyo nombre coincide con «${String(c.nombre_patron)}»` : ""}`);
      add(`${c.ocr === false ? "Sin OCR (solo el texto propio)" : c.vision === false ? "Con OCR de tesseract, sin visión" : "Con OCR y visión del modelo"} · hasta ${String(c.max_paginas ?? 60)} páginas por PDF`);
      add("Sale por «ok» o «sin_adjuntos»");
      break;
    case "filtro":
    case "condicion": {
      const conds = Array.isArray(c.condiciones) ? (c.condiciones as Record<string, unknown>[]) : [c];
      if (conds.length > 1) add(c.modo === "alguna" ? "Basta que se cumpla una de estas condiciones:" : "Se tienen que cumplir todas estas condiciones:");
      for (const x of conds) add(describirCondicion(x));
      add("Sale por «si» o «no»");
      break;
    }
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
        add(`Campo «${f.nombre}»${f.obligatorio ? " (obligatorio)" : ""}: ${f.patron ? `/${f.patron}/` : (f.descripcion ?? "")}${describirLongitud(f as Record<string, unknown>)}`);
      if (n.tipo === "extraer_regex") add("Sin distinguir mayúsculas ni tildes (salvo que se pida); deja el texto tal como aparece");
      if (n.tipo === "extraer_regex" && c.patron) add(`«${String(c.salida)}»: /${String(c.patron)}/`);
      if (n.tipo === "ia_extraer" && c.salida) add(`Además deja todos los campos juntos en «${String(c.salida)}»`);
      break;
    case "validar":
      for (const x of lista(c.reglas) as { campo: string; regla?: string; valor?: unknown; mensaje?: string }[])
        add(`${x.campo}: ${x.regla ?? "obligatorio"}${x.valor !== undefined ? ` ${JSON.stringify(x.valor)}` : ""}${x.mensaje ? ` — ${x.mensaje}` : ""}`);
      break;
    case "transformar":
      for (const o of (Array.isArray(c.operaciones) ? c.operaciones : [c]) as Record<string, unknown>[])
        add(`«${String(o.salida)}» ← ${String(o.op)}${o.de ? ` de ${corto(o.de)}` : ""}${o.plantilla ? ` «${corto(o.plantilla)}»` : ""}${o.patron ? ` /${String(o.patron)}/` : ""}${o.valor !== undefined ? ` = ${corto(o.valor)}` : ""}${o.largo !== undefined ? ` a ${String(o.largo)} ${o.contar === "palabras" ? "palabras" : "caracteres"}` : ""}${describirLongitud(o)}`);
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
    case "entrada_manual":
      add(`Se corre a mano o desde otro flujo con una entrada${lista(c.campos).length ? `: ${lista(c.campos).map((x) => `${valorDe(x, "nombre")}${(x as { obligatorio?: boolean }).obligatorio ? " (obligatorio)" : ""}`).join(", ")}` : ""}`);
      break;
    case "programado":
      add(`${c.cron ? `Cron «${String(c.cron)}»` : `Cada ${String(c.cada_minutos)} minuto(s)`} en ${String(c.zona ?? "America/Bogota")} · ${c.activo === true ? "ENCENDIDO" : "apagado"}`);
      add("Sin corridas solapadas; no recupera las perdidas");
      break;
    case "webhook":
      add(`POST /api/ganchos/<flujo>/<secreto>${c.responder === true ? ` · responde lo que arme respuesta_webhook (espera hasta ${String(c.esperar_ms ?? 10000)} ms)` : " · responde {corrida}"}`);
      break;
    case "correo_nuevo":
      add(`Revisa el buzón de la credencial «${String(c.credencial)}» (${String(c.carpeta ?? "INBOX")}) cada ${String(c.cada_minutos ?? 5)} min, solo lectura · ${c.activo === true ? "ENCENDIDO" : "apagado"}`);
      break;
    case "para_cada":
      add(`Por cada elemento de ${String(c.lista)} («${String(c.variable ?? "elemento")}», «${String(c.indice ?? "indice")}») corre el cuerpo («cada»); al terminar sigue por «listo»`);
      add(`Hasta ${String(c.max_iteraciones ?? 100)} vueltas · si una falla: ${String(c.al_fallar ?? "detener")} · deja la lista en «${String(c.salida ?? n.id)}»`);
      break;
    case "fin_ciclo":
      add("Termina la vuelta actual");
      break;
    case "paralelo":
      add(`Ramas (una tras otra): ${lista(c.ramas).map((x) => String(x)).join(", ")}`);
      break;
    case "unir":
      add(`Junta lo de cada rama en «${String(c.salida ?? n.id)}»`);
      break;
    case "esperar":
      add(`Espera ${String(c.segundos)} s`);
      break;
    case "expresion":
      add(`${c.como_condicion === true ? "Condición" : `«${String(c.salida ?? n.id)}» =`} ${String(c.expresion)}`);
      break;
    case "json":
    case "fecha":
      add(`${String(c.op)}${c.de ? ` de ${String(c.de)}` : ""}${c.ruta ? ` · ruta ${String(c.ruta)}` : ""}${c.valor ? ` · ${String(c.valor)}` : ""} → «${String(c.salida ?? n.id)}»`);
      break;
    case "llamar_flujo":
      add(`Corre el flujo «${String(c.flujo)}» y deja su resultado en «${String(c.salida ?? n.id)}»`);
      break;
    case "tabla_guardar":
    case "tabla_consultar":
      add(`Tabla integrada «${String(c.tabla)}» (${String(c.ambito ?? "flujo")})${c.clave ? ` · clave ${String(c.clave)}` : ""}${c.modo && n.tipo === "tabla_guardar" ? ` · ${String(c.modo)}` : ""}`);
      if (n.tipo === "tabla_consultar") {
        const f = c.filtro;
        const conds = [...(Array.isArray(f) ? (f as Record<string, unknown>[]) : f && typeof f === "object" ? Object.entries(f).map(([campo, valor]) => ({ campo, operador: "igual", valor })) : []),
          ...(Array.isArray(c.condiciones) ? (c.condiciones as Record<string, unknown>[]) : [])];
        if (conds.length > 1) add(c.modo === "alguna" ? "Filas que cumplen alguna de:" : "Filas que cumplen todas:");
        for (const x of conds) add(describirCondicion(x));
      }
      break;
    case "archivo_leer":
    case "archivo_guardar":
      add(c.archivo ? `Lee el archivo guardado en el flujo (${String(c.formato ?? "auto")})` : `${n.tipo === "archivo_leer" ? "Lee" : "Guarda"} /datos/archivos/${String(c.ruta)} (${String(c.formato ?? (n.tipo === "archivo_leer" ? "auto" : "texto"))})`);
      break;
    case "correo_leer":
    case "drive_listar":
    case "drive_leer":
    case "hoja_leer":
    case "sql_consulta":
      add(`Con la credencial «${String(c.credencial ?? "—")}»${c.consulta ? `: ${corto(c.consulta, 300)}` : ""}${c.origen ? ` · origen ${String(c.origen)}` : ""} · solo lectura`);
      break;
    case "correo_enviar":
    case "drive_subir":
    case "sql_ejecutar":
      add(`Con la credencial «${String(c.credencial)}»${c.sentencia ? `: ${corto(c.sentencia, 300)}` : ""}${c.para ? ` · para ${String(c.para)}` : ""}`);
      add("ESCRIBE AFUERA: solo sale si una persona habilitó la escritura de la credencial; si no, queda como escritura bloqueada");
      break;
    case "respuesta_webhook":
      add(`Responde ${String(c.estado ?? 200)}: ${corto(c.cuerpo, 300)}`);
      break;
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
  const entrada = disparadorDe(d)?.id ?? d.nodos[0]!.id;
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
    herramientas_agente: [], proveedores: [(({ proveedor, modelo }) => ({ id: proveedor, nombre: `${proveedor} · ${modelo}`, tipo: "ia" }))(ia.iaDelFlujo(d))],
    desenlaces: [...new Set(d.nodos.filter((n) => n.tipo === "salida").map((n) => String((n.config as { desenlace?: string }).desenlace)))].map((a) => ({ accion: a, descripcion: a })),
    variables_entorno: [], fases,
  };
}

let catalogoTutelas: Catalogo | null = null;

export function catalogoDe(id: string): Catalogo | null {
  const f = obtener(id);
  if (!f) return null;
  const base = baseDe(f.id);
  if (base === "pqrd") return catalogoDePqrd();
  if (base === "tutelas") {
    const ruta = resolve(raiz, "catalogo", "tutelas.json");
    if (!catalogoTutelas && existsSync(ruta)) catalogoTutelas = JSON.parse(readFileSync(ruta, "utf8")) as Catalogo;
    return catalogoTutelas;
  }
  return f.definicion ? catalogoDeclarativo(f.definicion) : null;
}
