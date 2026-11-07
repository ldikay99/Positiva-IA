import { z } from "zod";
import type { Piezas } from "./flujos_integraciones.ts";

/**
 * La forma de la config de los nodos de la fase 2 de integraciones (plan 5d): páginas sin API
 * (web_leer, rss_leer, rss_nuevo), almacenamiento (FTP/FTPS, S3 compatible), datos (MongoDB,
 * BigQuery, Firestore) y mensajería transaccional (Telegram, SendGrid/Mailgun, Twilio).
 * La ficha (y la validación que manda) está en `puente/especificacion_externos.py` y
 * `puente/nodos_externos.py`; las pruebas comprueban que este zod acepte sus ejemplos y rechace
 * lo mismo que el motor. flujos_integraciones.ts lo junta con los demás.
 */

export const TIPOS_EXTERNOS = [
  "rss_nuevo", "web_leer", "rss_leer", "ftp_archivos", "s3_archivos", "mongo_consulta", "mongo_escribir", "bigquery_consulta", "bigquery_ejecutar",
  "firestore_documentos", "telegram_mensaje", "correo_transaccional", "sms_twilio",
] as const;
export type TipoExterno = (typeof TIPOS_EXTERNOS)[number];
export const DISPARADORES_EXTERNOS = ["rss_nuevo"] as const;
export const ESCRITURA_EXTERNOS: string[] = ["ftp_archivos", "s3_archivos", "mongo_escribir", "bigquery_ejecutar", "firestore_documentos", "telegram_mensaje",
  "correo_transaccional", "sms_twilio"];
export const LECTURA_EXTERNOS: string[] = ["web_leer", "rss_leer", "mongo_consulta", "bigquery_consulta"];

export const NOMBRE_EXTERNO: Record<TipoExterno, string> = {
  rss_nuevo: "Feed RSS · elemento nuevo", web_leer: "Página web · leer", rss_leer: "Feed RSS/Atom · leer", ftp_archivos: "FTP · archivos",
  s3_archivos: "S3 · archivos (S3, MinIO, GCS)", mongo_consulta: "MongoDB · consultar", mongo_escribir: "MongoDB · escribir", bigquery_consulta: "BigQuery · consultar",
  bigquery_ejecutar: "BigQuery · ejecutar", firestore_documentos: "Firestore · documentos", telegram_mensaje: "Telegram · mensaje",
  correo_transaccional: "Correo transaccional (SendGrid / Mailgun)", sms_twilio: "SMS (Twilio)",
};
export const CATEGORIA_EXTERNO: Record<TipoExterno, string> = Object.fromEntries(TIPOS_EXTERNOS.map((t) => [t,
  t === "rss_nuevo" ? "entrada" : ["telegram_mensaje", "correo_transaccional", "sms_twilio"].includes(t) ? "integracion" : "conector"])) as Record<TipoExterno, string>;

type C = Record<string, unknown>;
const lleno = (x: unknown) => x !== undefined && x !== null && !(typeof x === "string" && !x.trim()) && !(Array.isArray(x) && !x.length)
  && !(typeof x === "object" && !Array.isArray(x) && !Object.keys(x as object).length);
const PROHIBIDOS_MONGO = new Set(["$where", "$function", "$accumulator", "$out", "$merge", "$eval", "$code"]);
/** El primer operador de Mongo que ejecuta código o escribe ($where, $function, $out, $merge…), o null. */
function prohibidoMongo(x: unknown): string | null {
  if (Array.isArray(x)) {
    for (const v of x) { const m = prohibidoMongo(v); if (m) return m; }
  } else if (x && typeof x === "object") {
    for (const [k, v] of Object.entries(x as C)) {
      if (PROHIBIDOS_MONGO.has(k)) return k;
      const m = prohibidoMongo(v);
      if (m) return m;
    }
  }
  return null;
}
/** Una URL fija (sin plantilla) tiene que ser https a un host público por nombre (el motor además bloquea Positiva e IP internas). */
function urlMala(u: unknown, plantilla = true): string | null {
  if (typeof u !== "string" || !u.trim() || (plantilla && u.includes("{{"))) return null;
  let x: URL;
  try {
    x = new URL(u.trim());
  } catch {
    return "tiene que ser una dirección https://";
  }
  const h = x.hostname.toLowerCase();
  if (x.protocol !== "https:") return "tiene que ser una dirección https://";
  if (/(^|\.)positiva\.gov\.co$/.test(h) || /sgdea/.test(h) || h === "localhost" || /\.internal$/.test(h) || /^[\d.]+$/.test(h) || h.includes(":") || h.startsWith("["))
    return "bloqueado por seguridad: solo hosts públicos por su nombre (nunca Positiva, SGDEA, IP ni localhost)";
  return null;
}

export function configExternos(p: Piezas): Record<TipoExterno, z.ZodType> {
  const { conf, credencialRef, archivosRef, textoReq, entre, opcional, mapaPlantillas, timeoutS } = p;
  const txt = z.string({ error: "tiene que ser texto (admite plantillas)" }).optional();
  const opcion = <T extends readonly [string, ...string[]]>(que: string, vals: T) => z.enum(vals, { error: `${que}: ${vals.join(", ")}` });
  const comun = { credencial: credencialRef, reintentos: z.unknown().optional(), timeout_s: timeoutS };
  const sinCred = { reintentos: z.unknown().optional(), timeout_s: timeoutS };
  const mal = (ctx: z.RefinementCtx, k: string, m: string) => ctx.addIssue({ code: "custom", path: [k], message: m });
  const objeto = (que: string) => z.record(z.string(), z.unknown(), { error: `${que} tiene que ser un objeto JSON {…}` });
  const subida = {
    carpeta: txt, archivo: z.union([z.string(), archivosRef("archivo")]).optional(), nombre: txt, contenido: txt, de: txt,
    formato: opcion("formato", ["texto", "json", "csv"] as const).optional(), como: opcion("como", ["texto", "archivo"] as const).optional(),
    conflicto: opcion("conflicto", ["renombrar", "reemplazar", "fallar"] as const).optional(), ocr: opcional,
    max_caracteres: entre("max_caracteres", 1, 500000).optional(), max: entre("max", 1, 1000).optional(),
  };
  const reglaSubida = (t: string) => (c: C, ctx: z.RefinementCtx) => {
    const op = c.operacion ?? "listar";
    if (op === "leer" && !(typeof c.archivo === "string" && c.archivo.trim())) mal(ctx, "archivo", `${t} leer necesita archivo (la ruta o clave en el servidor)`);
    if (op === "subir") {
      if (lleno(c.archivo)) {
        if (c.contenido || c.de) mal(ctx, "archivo", `${t}: usa archivo (un archivo tal cual) o contenido/de (un texto), no los dos`);
      } else if (!lleno(c.nombre) || !(c.contenido || c.de)) mal(ctx, "archivo", `${t} subir necesita archivo (p. ej. «{{documento}}») o nombre con contenido o de`);
    }
    if (typeof c.nombre === "string" && /[/\\]/.test(c.nombre)) mal(ctx, "nombre", "nombre no puede llevar «/» (pon la carpeta en carpeta)");
    for (const k of ["carpeta", "archivo"]) if (typeof c[k] === "string" && (c[k] as string).replace(/\\/g, "/").split("/").includes("..")) mal(ctx, k, `${k} no puede salir de la carpeta base (..)`);
  };
  const mongoObj = (ctx: z.RefinementCtx, c: C, claves: string[]) => {
    for (const k of claves) {
      const m = prohibidoMongo(c[k]);
      if (m) mal(ctx, k, `${k} no admite ${m} (ejecuta código o escribe)`);
    }
  };
  const proyecto = textoReq("proyecto (el id del proyecto de Google Cloud)");
  const sqlUna = (t: string) => textoReq("sql").refine((s) => !/;\s*\S/.test(String(s)),`${t}: una sola sentencia (hay un «;» en medio)`);

  return {
    rss_nuevo: conf({ url: textoReq("url (la del feed, https)"), cada_minutos: entre("cada_minutos", 5, 1440).optional(), activo: opcional,
      max_por_sondeo: entre("max_por_sondeo", 1, 50).optional(), procesar_existentes: opcional, contiene: txt }).superRefine((c: C, ctx) => {
      if (typeof c.url === "string" && c.url.includes("{{")) mal(ctx, "url", "rss_nuevo necesita la URL fija del feed (sin plantillas)");
      const m = urlMala(c.url, false);
      if (m) mal(ctx, "url", `url ${m}`);
    }),
    web_leer: conf({ ...sinCred, url: textoReq("url"), extraer: opcion("extraer", ["texto", "enlaces", "tablas", "selector"] as const).optional(),
      selector: z.string().optional(), atributo: z.string().optional(), completo: opcional, solo_mismo_sitio: opcional,
      max_caracteres: entre("max_caracteres", 100, 200000).optional(), max_elementos: entre("max_elementos", 1, 500).optional(),
      max_kb: entre("max_kb", 10, 5000).optional() }).superRefine((c: C, ctx) => {
      const m = urlMala(c.url);
      if (m) mal(ctx, "url", `url ${m}`);
      if (c.extraer === "selector" && !(typeof c.selector === "string" && c.selector.trim())) mal(ctx, "selector", "web_leer con extraer selector necesita selector (CSS)");
      if (c.extraer !== "selector" && c.selector) mal(ctx, "selector", "selector solo aplica con extraer: selector");
    }),
    rss_leer: conf({ ...sinCred, url: textoReq("url"), max: entre("max", 1, 200).optional(), desde: txt, contiene: txt }).superRefine((c: C, ctx) => {
      const m = urlMala(c.url);
      if (m) mal(ctx, "url", `url ${m}`);
    }),
    ftp_archivos: conf({ ...comun, operacion: opcion("operacion", ["listar", "leer", "subir"] as const).optional(), ...subida }).superRefine(reglaSubida("ftp_archivos")),
    s3_archivos: conf({ ...comun, operacion: opcion("operacion", ["listar", "leer", "subir"] as const).optional(), ...subida, recursivo: opcional })
      .superRefine(reglaSubida("s3_archivos")),
    mongo_consulta: conf({ ...comun, coleccion: textoReq("coleccion"), operacion: opcion("operacion", ["buscar", "contar", "distintos", "agregar"] as const).optional(),
      filtro: objeto("filtro").optional(), proyeccion: objeto("proyeccion").optional(),
      orden: z.record(z.string(), z.union([z.literal(1), z.literal(-1)]), { error: "orden: {campo: 1 o -1}" }).optional(),
      pipeline: z.array(objeto("cada etapa del pipeline"), { error: "pipeline tiene que ser una lista de etapas" }).optional(), campo: txt,
      max: entre("max", 1, 5000).optional() }).superRefine((c: C, ctx) => {
      mongoObj(ctx, c, ["filtro", "pipeline"]);
      if (c.operacion === "distintos" && !lleno(c.campo)) mal(ctx, "campo", "mongo_consulta distintos necesita campo");
      if (c.operacion === "agregar" && !(Array.isArray(c.pipeline) && c.pipeline.length && c.pipeline.every((x) => x && typeof x === "object" && Object.keys(x).length === 1)))
        mal(ctx, "pipeline", "mongo_consulta agregar necesita pipeline: una lista de etapas {\"$match\": …}");
    }),
    mongo_escribir: conf({ ...comun, coleccion: textoReq("coleccion"), operacion: opcion("operacion", ["insertar", "actualizar", "borrar"] as const).optional(),
      documento: objeto("documento").optional(), documentos: txt, filtro: objeto("filtro").optional(), cambios: objeto("cambios").optional(),
      varios: opcional, permitir_sin_filtro: opcional }).superRefine((c: C, ctx) => {
      mongoObj(ctx, c, ["filtro", "cambios", "documento"]);
      const op = c.operacion ?? "insertar";
      if (op === "insertar" && !lleno(c.documento) && !lleno(c.documentos)) mal(ctx, "documento", "mongo_escribir insertar necesita documento o documentos");
      if (op !== "insertar") {
        if (!lleno(c.filtro) && c.permitir_sin_filtro !== true) mal(ctx, "filtro", `mongo_escribir ${String(op)} sin filtro tocaría toda la colección (o permitir_sin_filtro: true)`);
        if (op === "actualizar" && !(c.cambios && typeof c.cambios === "object" && Object.keys(c.cambios).length && Object.keys(c.cambios).every((k) => k.startsWith("$"))))
          mal(ctx, "cambios", "mongo_escribir actualizar necesita cambios con operadores ($set, $inc…)");
      }
    }),
    bigquery_consulta: conf({ ...comun, proyecto, sql: sqlUna("bigquery_consulta"), parametros: mapaPlantillas("parametros").optional(), ubicacion: txt,
      max: entre("max", 1, 5000).optional() }),
    bigquery_ejecutar: conf({ ...comun, proyecto, sql: sqlUna("bigquery_ejecutar"), parametros: mapaPlantillas("parametros").optional(), ubicacion: txt }),
    firestore_documentos: conf({ ...comun, proyecto, base: txt, operacion: opcion("operacion", ["obtener", "listar", "consultar", "guardar"] as const).optional(),
      coleccion: textoReq("coleccion"), documento: txt,
      filtros: z.array(z.looseObject({ campo: textoReq("campo"), operador: opcion("operador", ["==", "!=", "<", "<=", ">", ">=", "array-contains", "in", "not-in", "array-contains-any"] as const).optional(),
        valor: z.unknown().optional() }), { error: "filtros: lista de {campo, operador, valor}" }).optional(),
      orden: z.array(z.looseObject({ campo: textoReq("campo"), descendente: opcional }), { error: "orden: lista de {campo, descendente}" }).optional(),
      datos: objeto("datos").optional(), combinar: opcional, max: entre("max", 1, 1000).optional() }).superRefine((c: C, ctx) => {
      const op = c.operacion ?? "listar";
      if (op === "obtener" && !lleno(c.documento)) mal(ctx, "documento", "firestore_documentos obtener necesita documento (su id)");
      if (op === "guardar" && !lleno(c.datos)) mal(ctx, "datos", "firestore_documentos guardar necesita datos {campo: valor}");
      if (op !== "consultar" && (lleno(c.filtros) || lleno(c.orden))) mal(ctx, "filtros", "filtros y orden solo aplican con operacion consultar");
    }),
    telegram_mensaje: conf({ ...comun, chat: txt, texto: textoReq("texto"), formato: opcion("formato", ["texto", "html", "markdown"] as const).optional(),
      archivos: archivosRef("archivos").optional(), silencioso: opcional }),
    correo_transaccional: conf({ ...comun, para: textoReq("para"), cc: txt, cco: txt, asunto: textoReq("asunto"), cuerpo: textoReq("cuerpo"), html: txt,
      remitente: txt, nombre_remitente: txt, responder_a: txt, adjuntos: archivosRef("adjuntos").optional() }),
    sms_twilio: conf({ ...comun, para: textoReq("para"), texto: textoReq("texto"), remitente: txt }),
  };
}

/** Una línea que dice qué hace cada nodo de la fase 2 (para el lienzo); false si no es de aquí. */
export function describirExterno(tipo: string, c: Record<string, unknown>, add: (d: string) => void): boolean {
  if (!(TIPOS_EXTERNOS as readonly string[]).includes(tipo)) return false;
  const t = tipo as TipoExterno;
  const cred = `con la conexión «${String(c.credencial ?? "—")}»`;
  switch (t) {
    case "rss_nuevo":
      add(`Revisa el feed ${String(c.url ?? "")} cada ${String(c.cada_minutos ?? 30)} min, solo lectura, sin repetir · ${c.activo === true ? "ENCENDIDO" : "apagado"}`);
      break;
    case "web_leer":
      add(`Lee ${String(c.url ?? "")} (${String(c.extraer ?? "texto")}), solo lectura, respetando robots.txt y la frecuencia`);
      break;
    case "rss_leer":
      add(`Lee el feed ${String(c.url ?? "")}, solo lectura`);
      break;
    default:
      add(`${NOMBRE_EXTERNO[t]} ${cred}${c.operacion ? ` · ${String(c.operacion)}` : ""}`);
      if (ESCRITURA_EXTERNOS.includes(t)) add("ESCRIBE AFUERA: solo sale si una persona habilitó la escritura de la conexión; si no, queda retenido con todo armado");
      else add("Solo lectura");
  }
  return true;
}
