import { z } from "zod";
import { CATEGORIA_EXTERNO, configExternos, describirExterno, DISPARADORES_EXTERNOS, ESCRITURA_EXTERNOS, LECTURA_EXTERNOS, NOMBRE_EXTERNO, TIPOS_EXTERNOS } from "./flujos_externos.ts";
import { CATEGORIA_TRABAJO, configTrabajo, describirTrabajo, ESCRITURA_TRABAJO, NOMBRE_TRABAJO, TIPOS_TRABAJO } from "./flujos_trabajo.ts";

/**
 * La forma de la config de los nodos de integración (plan 5d): Google Workspace, Microsoft 365,
 * OpenAPI y los disparadores por evento. La ficha (y la validación que manda) está en
 * `puente/especificacion_integraciones.py`; las pruebas comprueban que este zod acepte sus ejemplos
 * y rechace lo mismo que el motor. flujos.ts pasa sus piezas (así no hay importación circular).
 */

export const TIPOS_INTEGRACION = [
  // disparadores por evento
  "gmail_nuevo", "outlook_nuevo", "calendar_evento_proximo",
  // Google Workspace
  "gmail_buscar", "gmail_enviar", "gmail_responder", "gmail_etiquetar", "calendar_eventos", "calendar_crear", "calendar_actualizar",
  "calendar_disponibilidad", "docs_crear_desde_plantilla", "forms_respuestas",
  // Microsoft 365
  "outlook_buscar", "outlook_enviar", "outlook_responder", "outlook_mover", "outlook_categorizar", "outlook_calendario_eventos",
  "outlook_calendario_crear", "onedrive_listar", "onedrive_leer", "onedrive_subir", "sharepoint_archivos", "sharepoint_lista",
  "teams_mensaje", "excel_en_linea",
  // cualquier API con OpenAPI
  "api_operacion",
  // fase 2 (flujos_externos.ts): páginas sin API, almacenamiento, datos y mensajería transaccional
  ...TIPOS_EXTERNOS,
  // fase 3 (flujos_trabajo.ts): trabajo y clientes, firma electrónica
  ...TIPOS_TRABAJO,
] as const;
export type TipoIntegracion = (typeof TIPOS_INTEGRACION)[number];

export const DISPARADORES_INTEGRACION = ["gmail_nuevo", "outlook_nuevo", "calendar_evento_proximo", ...DISPARADORES_EXTERNOS] as const;
export const DISPARADORES_DE_CORREO_INTEGRACION = ["gmail_nuevo", "outlook_nuevo"] as const;
/** Todos los disparadores que arrancan con un correo (la corrida trae caso y tiene «Correspondencia»). Sin importar flujos.ts. */
export const DISPARADORES_DE_CORREO_TODOS = new Set<string>(["entrada_correo", "correo_nuevo", ...DISPARADORES_DE_CORREO_INTEGRACION]);
/** Los que sondean el programador con `flujo_declarativo.py sondear` (además de programado). */
export const DISPARADORES_DE_SONDEO = new Set<string>(["correo_nuevo", ...DISPARADORES_INTEGRACION]);
export const ESCRITURA_INTEGRACION = ["gmail_enviar", "gmail_responder", "gmail_etiquetar", "calendar_crear", "calendar_actualizar", "docs_crear_desde_plantilla",
  "outlook_enviar", "outlook_responder", "outlook_mover", "outlook_categorizar", "outlook_calendario_crear", "onedrive_subir",
  "sharepoint_archivos", "sharepoint_lista", "teams_mensaje", "excel_en_linea", "api_operacion", ...ESCRITURA_EXTERNOS, ...ESCRITURA_TRABAJO];
export const LECTURA_INTEGRACION = ["gmail_buscar", "calendar_eventos", "calendar_disponibilidad", "forms_respuestas", "outlook_buscar", "outlook_calendario_eventos",
  "onedrive_listar", "onedrive_leer", ...LECTURA_EXTERNOS];
/** Hablan con un servicio de afuera: aceptan reintentos (los disparadores no). */
export const CON_REINTENTOS_INTEGRACION = [...ESCRITURA_INTEGRACION, ...LECTURA_INTEGRACION];

export const NOMBRE_INTEGRACION: Record<TipoIntegracion, string> = {
  gmail_nuevo: "Gmail · correo nuevo", outlook_nuevo: "Outlook · correo nuevo", calendar_evento_proximo: "Evento próximo del calendario",
  gmail_buscar: "Gmail · buscar", gmail_enviar: "Gmail · enviar", gmail_responder: "Gmail · responder", gmail_etiquetar: "Gmail · etiquetar",
  calendar_eventos: "Calendar · eventos", calendar_crear: "Calendar · crear evento", calendar_actualizar: "Calendar · actualizar evento",
  calendar_disponibilidad: "Calendar · disponibilidad", docs_crear_desde_plantilla: "Docs · documento desde plantilla", forms_respuestas: "Forms · respuestas",
  outlook_buscar: "Outlook · buscar", outlook_enviar: "Outlook · enviar", outlook_responder: "Outlook · responder", outlook_mover: "Outlook · mover",
  outlook_categorizar: "Outlook · categorías", outlook_calendario_eventos: "Outlook · eventos", outlook_calendario_crear: "Outlook · crear evento",
  onedrive_listar: "OneDrive · listar", onedrive_leer: "OneDrive · leer", onedrive_subir: "OneDrive · subir", sharepoint_archivos: "SharePoint · archivos",
  sharepoint_lista: "SharePoint · lista", teams_mensaje: "Teams · mensaje (Graph)", excel_en_linea: "Excel en línea", api_operacion: "API · operación (OpenAPI)",
  ...NOMBRE_EXTERNO,
  ...NOMBRE_TRABAJO,
};

export const CATEGORIA_INTEGRACION: Record<TipoIntegracion, string> = {
  ...Object.fromEntries(TIPOS_INTEGRACION.map((t) => [t,
    (DISPARADORES_INTEGRACION as readonly string[]).includes(t) ? "entrada" : t === "docs_crear_desde_plantilla" ? "documentos" : t === "teams_mensaje" || t === "api_operacion" ? "integracion" : "conector"])),
  ...CATEGORIA_EXTERNO,
  ...CATEGORIA_TRABAJO,
} as Record<TipoIntegracion, string>;

/** Lo que flujos.ts le presta a este módulo. */
export interface Piezas {
  conf: <T extends z.ZodRawShape>(forma: T) => z.ZodObject<z.ZodRawShape>;
  credencialRef: z.ZodType;
  archivosRef: (que: string) => z.ZodType;
  textoReq: (que: string) => z.ZodType;
  entre: (que: string, min: number, max: number, entero?: boolean) => z.ZodType;
  opcional: z.ZodType;
  mapaPlantillas: (que: string) => z.ZodType;
  timeoutS: z.ZodType;
  RUTA: RegExp;
  variable: z.ZodType;
  filtroCorreo: z.ZodType;
}

type C = Record<string, unknown>;
const lleno = (x: unknown) => x !== undefined && x !== null && !(typeof x === "string" && !x.trim()) && !(Array.isArray(x) && !x.length);

export function configIntegraciones(p: Piezas): Record<TipoIntegracion, z.ZodType> {
  const { conf, credencialRef, archivosRef, textoReq, entre, opcional, mapaPlantillas, timeoutS, RUTA, variable, filtroCorreo } = p;
  const txt = z.string({ error: "tiene que ser texto (admite plantillas)" }).optional();
  const textos = (que: string) => z.array(z.string().trim().min(1, `${que}: hay uno vacío`), { error: `${que} tiene que ser una lista de textos` });
  const listaOTexto = (que: string) => z.union([z.string(), textos(que)], { error: `${que}: un texto separado por comas o una lista de textos` });
  const hora = (que: string) => z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, `${que}: hora HH:MM`);
  const opcion = <T extends readonly [string, ...string[]]>(que: string, vals: T) => z.enum(vals, { error: `${que}: ${vals.join(", ")}` });
  const comun = { credencial: credencialRef, reintentos: z.unknown().optional(), timeout_s: timeoutS };
  const mal = (ctx: z.RefinementCtx, k: string, m: string) => ctx.addIssue({ code: "custom", path: [k], message: m });

  const paginacion = z.looseObject({
    tipo: opcion("paginacion.tipo", ["cursor", "pagina", "enlace"] as const), parametro: z.string().optional(), ruta_cursor: z.string().regex(RUTA, "paginacion.ruta_cursor: una ruta válida").optional(),
    ruta_siguiente: z.string().regex(RUTA, "paginacion.ruta_siguiente: una ruta válida").optional(), ruta_items: z.string().regex(RUTA, "paginacion.ruta_items: una ruta válida").optional(),
    inicio: entre("paginacion.inicio", 0, 1000000).optional(), max_paginas: entre("paginacion.max_paginas", 1, 50).optional(),
  }, { error: "paginacion tiene que ser un objeto {tipo, parametro, ruta_cursor, ruta_items, max_paginas}" }).superRefine((x, ctx) => {
    if (x.tipo === "cursor" && !(x.parametro?.trim() && x.ruta_cursor?.trim())) mal(ctx, "parametro", "paginacion cursor necesita parametro y ruta_cursor");
    if (x.tipo === "pagina" && !x.parametro?.trim()) mal(ctx, "parametro", "paginacion pagina necesita parametro (p. ej. «page»)");
  });
  const mapeo = z.array(z.looseObject({
    variable, ruta: z.string({ error: "falta ruta" }).regex(RUTA, "ruta inválida (p. ej. «items.0.id»)"), defecto: z.unknown().optional(), obligatorio: opcional,
  }), { error: "mapeo tiene que ser una lista [{variable, ruta, defecto, obligatorio}]" }).refine((m) => new Set(m.map((x) => (x as { variable: string }).variable)).size === m.length, "mapeo: hay variables repetidas");

  const sondeo = { cada_minutos: entre("cada_minutos", 1, 1440).optional(), activo: opcional, max_por_sondeo: entre("max_por_sondeo", 1, 50).optional(),
    procesar_existentes: opcional, leer_adjuntos: opcional, ocr: opcional };
  const envio = { para: textoReq("para"), cc: txt, cco: txt, asunto: textoReq("asunto"), cuerpo: textoReq("cuerpo"), html: txt, adjuntos: archivosRef("adjuntos").optional() };
  const respuesta = { mensaje: txt, cuerpo: textoReq("cuerpo"), html: txt, para: txt, cc: txt, asunto: txt, adjuntos: archivosRef("adjuntos").optional(), incluir_original: opcional, todos: opcional };
  const rango = { desde: txt, hasta: txt, dias: entre("dias", 1, 366).optional(), consulta: txt, zona: txt, max: entre("max", 1, 250).optional() };
  const evento = { calendario: txt, titulo: textoReq("titulo"), inicio: textoReq("inicio"), fin: txt, duracion_min: entre("duracion_min", 5, 1440).optional(),
    todo_el_dia: opcional, zona: txt, descripcion: txt, lugar: txt, asistentes: listaOTexto("asistentes").optional(), recordatorio_min: entre("recordatorio_min", 0, 40320).optional() };
  const formato = opcion("formato", ["texto", "json", "csv"] as const).optional();
  const conflicto = opcion("conflicto", ["renombrar", "reemplazar", "fallar"] as const).optional();
  const subida = (ctx: z.RefinementCtx, c: C, prefijo: string) => {
    if (lleno(c.archivo)) {
      if (c.contenido || c.de) mal(ctx, "archivo", `${prefijo}usa archivo (un archivo tal cual) o contenido/de (un texto), no los dos`);
    } else if (!lleno(c.nombre) || !(c.contenido || c.de)) mal(ctx, "archivo", `${prefijo}necesita archivo (p. ej. «{{documento}}») o nombre con contenido (plantilla) o de (variable)`);
  };

  return {
    gmail_nuevo: conf({ ...sondeo, credencial: credencialRef, consulta: txt, etiqueta: txt }),
    outlook_nuevo: conf({ ...sondeo, credencial: credencialRef, carpeta: txt, filtro: filtroCorreo.optional() }),
    calendar_evento_proximo: conf({ credencial: credencialRef, calendario: txt, minutos_antes: entre("minutos_antes", 1, 1440).optional(), consulta: txt,
      cada_minutos: entre("cada_minutos", 1, 60).optional(), activo: opcional }),

    gmail_buscar: conf({ ...comun, consulta: txt, etiqueta: txt, max: entre("max", 1, 100).optional(), incluir_adjuntos: opcional }),
    gmail_enviar: conf({ ...comun, ...envio }),
    gmail_responder: conf({ ...comun, ...respuesta }),
    gmail_etiquetar: conf({ ...comun, mensaje: txt, agregar: textos("agregar").optional(), quitar: textos("quitar").optional(), crear_si_falta: opcional,
      marcar_leido: opcional, archivar: opcional }).superRefine((c: C, ctx) => {
      if (!lleno(c.agregar) && !lleno(c.quitar) && c.marcar_leido !== true && c.archivar !== true) mal(ctx, "agregar", "gmail_etiquetar necesita agregar, quitar, marcar_leido o archivar");
    }),
    calendar_eventos: conf({ ...comun, ...rango }),
    calendar_crear: conf({ ...comun, ...evento, con_meet: opcional, notificar: opcional }),
    calendar_actualizar: conf({ ...comun, calendario: txt, evento: textoReq("evento"), titulo: txt, inicio: txt, fin: txt, duracion_min: entre("duracion_min", 5, 1440).optional(),
      zona: txt, descripcion: txt, lugar: txt, asistentes: listaOTexto("asistentes").optional(), agregar_asistentes: listaOTexto("agregar_asistentes").optional(),
      con_meet: opcional, cancelar: opcional, notificar: opcional }).superRefine((c: C, ctx) => {
      const cambia = ["titulo", "inicio", "fin", "duracion_min", "descripcion", "lugar", "asistentes", "agregar_asistentes", "con_meet"].some((k) => c[k] !== undefined && c[k] !== "") || c.cancelar === true;
      if (!cambia) mal(ctx, "titulo", "calendar_actualizar necesita algo que cambiar (titulo, inicio, fin, descripcion, lugar, asistentes, con_meet…) o cancelar: true");
    }),
    calendar_disponibilidad: conf({ ...comun, calendarios: listaOTexto("calendarios").optional(), desde: txt, hasta: txt, duracion_min: entre("duracion_min", 5, 1440).optional(),
      zona: txt, horario_desde: hora("horario_desde").optional(), horario_hasta: hora("horario_hasta").optional(), max_franjas: entre("max_franjas", 1, 50).optional() }),
    docs_crear_desde_plantilla: conf({ ...comun, plantilla: textoReq("plantilla"), variables: mapaPlantillas("variables").optional(), nombre: txt,
      exportar: opcion("exportar", ["pdf", "docx", "ninguno"] as const).optional(), en_drive: opcional, carpeta: txt }).superRefine((c: C, ctx) => {
      if (c.exportar === "ninguno" && c.en_drive !== true) mal(ctx, "exportar", "exportar ninguno solo sirve con en_drive: true (si no, no queda ningún documento)");
    }),
    forms_respuestas: conf({ ...comun, formulario: textoReq("formulario"), desde: txt, max: entre("max", 1, 5000).optional() }),

    outlook_buscar: conf({ ...comun, carpeta: txt, consulta: txt, filtro: filtroCorreo.optional(), desde_fecha: txt, max: entre("max", 1, 100).optional(), incluir_adjuntos: opcional }),
    outlook_enviar: conf({ ...comun, ...envio, guardar_enviados: opcional }),
    outlook_responder: conf({ ...comun, ...respuesta }),
    outlook_mover: conf({ ...comun, mensaje: txt, carpeta: textoReq("carpeta") }),
    outlook_categorizar: conf({ ...comun, mensaje: txt, categorias: textos("categorias").optional(), modo: opcion("modo", ["agregar", "reemplazar", "quitar"] as const).optional(),
      marcar_leido: opcional, importancia: opcion("importancia", ["low", "normal", "high"] as const).optional() }).superRefine((c: C, ctx) => {
      if (!lleno(c.categorias) && c.marcar_leido !== true && !c.importancia) mal(ctx, "categorias", "outlook_categorizar necesita categorias, marcar_leido o importancia");
    }),
    outlook_calendario_eventos: conf({ ...comun, calendario: txt, ...rango }),
    outlook_calendario_crear: conf({ ...comun, ...evento, con_teams: opcional }),
    onedrive_listar: conf({ ...comun, carpeta: txt, buscar: txt, max: entre("max", 1, 200).optional() }),
    onedrive_leer: conf({ ...comun, archivo: textoReq("archivo"), como: opcion("como", ["texto", "archivo"] as const).optional(), ocr: opcional,
      max_caracteres: entre("max_caracteres", 1, 500000).optional() }),
    onedrive_subir: conf({ ...comun, carpeta: txt, archivo: archivosRef("archivo").optional(), nombre: txt, contenido: txt, de: txt, formato, conflicto })
      .superRefine((c: C, ctx) => subida(ctx, c, "onedrive_subir: ")),
    sharepoint_archivos: conf({ ...comun, sitio: textoReq("sitio"), biblioteca: txt, operacion: opcion("operacion", ["listar", "leer", "subir"] as const).optional(), carpeta: txt,
      archivo: z.union([z.string(), archivosRef("archivo")]).optional(), nombre: txt, contenido: txt, de: txt, formato,
      como: opcion("como", ["texto", "archivo"] as const).optional(), conflicto, max: entre("max", 1, 200).optional(), ocr: opcional }).superRefine((c: C, ctx) => {
      if (c.operacion === "leer" && !lleno(c.archivo)) mal(ctx, "archivo", "sharepoint_archivos leer necesita archivo (ruta o id)");
      if (c.operacion === "subir") subida(ctx, c, "sharepoint_archivos subir ");
    }),
    sharepoint_lista: conf({ ...comun, sitio: textoReq("sitio"), lista: textoReq("lista"), operacion: opcion("operacion", ["leer", "agregar"] as const).optional(), filtro: txt,
      campos: mapaPlantillas("campos").optional(), max: entre("max", 1, 5000).optional() }).superRefine((c: C, ctx) => {
      if (c.operacion === "agregar" && !(c.campos && typeof c.campos === "object" && Object.keys(c.campos).length)) mal(ctx, "campos", "sharepoint_lista agregar necesita campos {columna: plantilla}");
    }),
    teams_mensaje: conf({ ...comun, destino: opcion("destino", ["canal", "chat"] as const).optional(), equipo: txt, canal: txt, chat: txt, titulo: txt,
      texto: textoReq("texto"), formato: opcion("formato", ["texto", "html"] as const).optional(), archivos: archivosRef("archivos").optional() }).superRefine((c: C, ctx) => {
      if ((c.destino ?? "canal") === "canal" && !(lleno(c.equipo) && lleno(c.canal))) mal(ctx, "canal", "teams_mensaje a un canal necesita equipo y canal (sus ids)");
      if (c.destino === "chat" && !lleno(c.chat)) mal(ctx, "chat", "teams_mensaje a un chat necesita chat (su id)");
    }),
    excel_en_linea: conf({ ...comun, sitio: txt, archivo: textoReq("archivo"), hoja: txt, tabla: txt, rango: txt,
      operacion: opcion("operacion", ["leer", "anexar", "escribir"] as const).optional(), filas: txt, fila: mapaPlantillas("fila").optional(), encabezados: opcional,
      max_filas: entre("max_filas", 1, 5000).optional() }).superRefine((c: C, ctx) => {
      if (!lleno(c.tabla) && !lleno(c.hoja)) mal(ctx, "tabla", "excel_en_linea necesita tabla (su nombre) u hoja (con rango)");
      if (c.operacion === "anexar" && !(lleno(c.tabla) && (lleno(c.filas) || (c.fila && Object.keys(c.fila as object).length)))) mal(ctx, "filas", "anexar necesita tabla y filas (variable) o fila {columna: plantilla}");
      if (c.operacion === "escribir" && !(lleno(c.hoja) && lleno(c.rango) && lleno(c.filas))) mal(ctx, "rango", "escribir necesita hoja, rango y filas");
    }),
    api_operacion: conf({ ...comun, operacion: textoReq("operacion (operationId o «GET /ruta»)"), parametros: mapaPlantillas("parametros").optional(),
      cuerpo: z.union([z.string(), z.record(z.string(), z.unknown()), z.array(z.unknown())], { error: "cuerpo: un objeto JSON con plantillas o un texto" }).optional(),
      paginacion: paginacion.optional(), mapeo: mapeo.optional() }),
    ...configExternos(p),
    ...configTrabajo(p),
  };
}

/** Los campos de la extensión de http (paginación, archivos, descarga) y su regla. */
export function extensionHttp(p: Pick<Piezas, "entre" | "opcional" | "archivosRef" | "RUTA">) {
  const { entre, opcional, archivosRef, RUTA } = p;
  const paginacion = z.looseObject({
    tipo: z.enum(["cursor", "pagina", "enlace"], { error: "paginacion.tipo: cursor, pagina o enlace" }), parametro: z.string().optional(),
    ruta_cursor: z.string().regex(RUTA, "paginacion.ruta_cursor: una ruta válida").optional(), ruta_siguiente: z.string().regex(RUTA, "paginacion.ruta_siguiente: una ruta válida").optional(),
    ruta_items: z.string().regex(RUTA, "paginacion.ruta_items: una ruta válida").optional(), inicio: entre("paginacion.inicio", 0, 1000000).optional(), max_paginas: entre("paginacion.max_paginas", 1, 50).optional(),
  }, { error: "paginacion tiene que ser un objeto {tipo, parametro, ruta_cursor, ruta_items, max_paginas}" }).superRefine((x, ctx) => {
    if (x.tipo === "cursor" && !(x.parametro?.trim() && x.ruta_cursor?.trim())) ctx.addIssue({ code: "custom", path: ["parametro"], message: "paginacion cursor necesita parametro y ruta_cursor" });
    if (x.tipo === "pagina" && !x.parametro?.trim()) ctx.addIssue({ code: "custom", path: ["parametro"], message: "paginacion pagina necesita parametro (p. ej. «page»)" });
  });
  return {
    forma: {
      paginacion: paginacion.optional(), archivos: archivosRef("archivos").optional(),
      modo_archivos: z.enum(["multipart", "binario"], { error: "modo_archivos: multipart o binario" }).optional(), campo_archivo: z.string().trim().min(1).optional(),
      descargar: opcional, nombre_archivo: z.string().optional(),
    },
    regla: (c: { metodo?: string; archivos?: unknown; paginacion?: unknown }, ctx: z.RefinementCtx) => {
      const metodo = c.metodo ?? "GET";
      if (c.archivos !== undefined && !["POST", "PUT", "PATCH"].includes(metodo)) ctx.addIssue({ code: "custom", path: ["archivos"], message: "archivos solo con POST, PUT o PATCH" });
      if (c.paginacion !== undefined && metodo !== "GET") ctx.addIssue({ code: "custom", path: ["paginacion"], message: "paginacion solo con GET" });
    },
  };
}

/** Una línea que dice qué hace cada nodo de integración (para el lienzo). */
export function describirIntegracion(tipo: string, c: Record<string, unknown>, add: (d: string) => void): boolean {
  if (!(TIPOS_INTEGRACION as readonly string[]).includes(tipo)) return false;
  if (describirExterno(tipo, c, add)) return true;
  if (describirTrabajo(tipo, c, add)) return true;
  const cred = `con la conexión «${String(c.credencial ?? "—")}»`;
  const t = tipo as TipoIntegracion;
  switch (t) {
    case "gmail_nuevo":
      add(`Revisa Gmail ${cred}${c.consulta ? ` («${String(c.consulta)}»)` : ""} cada ${String(c.cada_minutos ?? 5)} min, solo lectura, sin repetir · ${c.activo === true ? "ENCENDIDO" : "apagado"}`);
      break;
    case "outlook_nuevo":
      add(`Revisa Outlook (${String(c.carpeta ?? "inbox")}) ${cred} cada ${String(c.cada_minutos ?? 5)} min, solo lectura, sin repetir · ${c.activo === true ? "ENCENDIDO" : "apagado"}`);
      break;
    case "calendar_evento_proximo":
      add(`Corre ${String(c.minutos_antes ?? 15)} min antes de cada evento del calendario ${String(c.calendario || "principal")} ${cred} · ${c.activo === true ? "ENCENDIDO" : "apagado"}`);
      break;
    case "api_operacion":
      add(`Operación «${String(c.operacion ?? "")}» de la API ${cred}${c.paginacion ? " · junta varias páginas" : ""}`);
      add("GET lee; los demás métodos ESCRIBEN AFUERA: solo salen si una persona habilitó la escritura de la conexión; si no, quedan retenidos");
      break;
    default:
      add(`${NOMBRE_INTEGRACION[t]} ${cred}${c.operacion ? ` · ${String(c.operacion)}` : ""}`);
      if (ESCRITURA_INTEGRACION.includes(t)) add("ESCRIBE AFUERA: solo sale si una persona habilitó la escritura de la conexión; si no, queda retenido con todo armado");
      else add("Solo lectura");
  }
  return true;
}
