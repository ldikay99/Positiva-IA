import { z } from "zod";
import type { Piezas } from "./flujos_integraciones.ts";

/**
 * La forma de la config de los nodos de la fase 3 de integraciones (plan 5d), trabajo y clientes:
 * Jira, Trello, Asana, Notion, HubSpot, Salesforce, Zoho CRM y DocuSign. Un nodo por servicio con su
 * `operacion`: leer sale libre; lo que crea, cambia, comenta o envía queda retenido sin la escritura
 * habilitada por una persona. La ficha (y la validación que manda) está en `puente/especificacion_trabajo.py`
 * y `puente/nodos_trabajo.py`; las pruebas (`puente/pruebas_trabajo.py`) comprueban que este zod acepte sus
 * ejemplos y rechace lo mismo que el motor. flujos_integraciones.ts lo junta con los demás.
 */

export const OPERACIONES_TRABAJO = {
  jira_issues: ["buscar", "ver", "crear", "comentar", "cambiar_estado"],
  trello_tarjetas: ["tableros", "listas", "tarjetas", "crear", "mover", "comentar"],
  asana_tareas: ["listar", "ver", "crear", "completar", "comentar"],
  notion_paginas: ["consultar", "crear", "actualizar", "agregar_bloques"],
  hubspot_crm: ["buscar", "ver", "crear", "actualizar", "nota"],
  salesforce_registros: ["consultar", "ver", "crear", "actualizar"],
  zoho_crm: ["buscar", "ver", "crear", "actualizar"],
  docusign_sobre: ["enviar", "estado", "descargar"],
} as const;
export const TIPOS_TRABAJO = Object.keys(OPERACIONES_TRABAJO) as (keyof typeof OPERACIONES_TRABAJO)[];
export type TipoTrabajo = keyof typeof OPERACIONES_TRABAJO;
/** Cada nodo lee o escribe según su operación (como ftp_archivos): todos cuentan como «escribe afuera». */
export const ESCRITURA_TRABAJO: string[] = [...TIPOS_TRABAJO];
export const LEEN_TRABAJO = new Set(["buscar", "ver", "tableros", "listas", "tarjetas", "listar", "consultar", "estado", "descargar"]);

export const NOMBRE_TRABAJO: Record<TipoTrabajo, string> = {
  jira_issues: "Jira · issues", trello_tarjetas: "Trello · tableros y tarjetas", asana_tareas: "Asana · tareas", notion_paginas: "Notion · bases y páginas",
  hubspot_crm: "HubSpot · CRM", salesforce_registros: "Salesforce · registros", zoho_crm: "Zoho CRM", docusign_sobre: "DocuSign · firma electrónica",
};
export const CATEGORIA_TRABAJO: Record<TipoTrabajo, string> = Object.fromEntries(TIPOS_TRABAJO.map((t) => [t, t === "docusign_sobre" ? "integracion" : "conector"])) as Record<TipoTrabajo, string>;

type C = Record<string, unknown>;
const lleno = (x: unknown) => x !== undefined && x !== null && !(typeof x === "string" && !x.trim()) && !(Array.isArray(x) && !x.length)
  && !(typeof x === "object" && !Array.isArray(x) && !Object.keys(x as object).length);
const fijo = (x: unknown): x is string => typeof x === "string" && x.trim() !== "" && !x.includes("{{");
const CLAVE_JIRA = /^([A-Z][A-Z0-9_]{0,30}-\d{1,9}|\d{1,12})$/;
const OBJETO_SF = /^[A-Za-z][A-Za-z0-9_]{0,79}$/;
const ID_SF = /^[A-Za-z0-9]{15}([A-Za-z0-9]{3})?$/;
const MODULO_ZOHO = /^[A-Za-z][A-Za-z0-9_]{1,60}$/;
const ID_DOCUSIGN = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const CORREO = /^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$/;
const CORREO_POSITIVA = /@([\w-]+\.)*positiva\.gov\.co$/i;
export const OPERADORES_HUBSPOT = ["EQ", "NEQ", "LT", "LTE", "GT", "GTE", "CONTAINS_TOKEN", "NOT_CONTAINS_TOKEN", "HAS_PROPERTY", "NOT_HAS_PROPERTY", "BETWEEN", "IN", "NOT_IN"] as const;
/** El mismo chequeo que conectores/trabajo.revisar_soql: un solo SELECT, sin «;». */
export function soqlMala(s: unknown): string | null {
  const x = String(s ?? "").trim();
  if (!/^select\s/is.test(x)) return "la consulta de Salesforce tiene que ser un SELECT (SOQL de solo lectura)";
  if (/;\s*\S/.test(x) || x.endsWith(";")) return "SOQL: una sola consulta, sin «;»";
  return null;
}

export function configTrabajo(p: Piezas): Record<TipoTrabajo, z.ZodType> {
  const { conf, credencialRef, archivosRef, entre, opcional, timeoutS } = p;
  const txt = z.string({ error: "tiene que ser texto (admite plantillas)" }).optional();
  const textos = (que: string) => z.array(z.string().trim().min(1), { error: `${que} tiene que ser una lista de textos` }).optional();
  const objeto = (que: string) => z.record(z.string(), z.unknown(), { error: `${que} tiene que ser un objeto JSON {…}` }).optional();
  const comun = { credencial: credencialRef, reintentos: z.unknown().optional(), timeout_s: timeoutS };
  const mal = (ctx: z.RefinementCtx, k: string, m: string) => ctx.addIssue({ code: "custom", path: [k], message: m });
  const op = <T extends TipoTrabajo>(t: T) => z.enum(OPERACIONES_TRABAJO[t] as unknown as [string, ...string[]], { error: `operacion: ${OPERACIONES_TRABAJO[t].join(", ")}` }).optional();
  /** Para cada nodo: (operación, faltan…) con el mismo texto que el motor. */
  const regla = (t: TipoTrabajo, f: (c: C, o: string, falta: (k: string, que: string) => void, ctx: z.RefinementCtx) => void) => (c: C, ctx: z.RefinementCtx) => {
    const o = String(c.operacion ?? OPERACIONES_TRABAJO[t][0]);
    f(c, o, (k, que) => { if (!lleno(c[k])) mal(ctx, k, `${t} ${o} necesita ${k} (${que})`); }, ctx);
  };
  const correoMalo = (x: unknown): string | null => {
    if (!fijo(x)) return null;
    if (!CORREO.test(x.trim())) return `«${x.slice(0, 60)}» no es una dirección de correo`;
    return CORREO_POSITIVA.test(x.trim()) ? `bloqueado por seguridad: ${x.trim()} es una dirección de Positiva` : null;
  };
  const persona = z.looseObject({ nombre: txt, correo: z.string({ error: "falta correo" }).trim().min(1, "falta correo"), orden: entre("orden", 1, 50).optional() },
    { error: "cada persona es {nombre, correo}" });

  return {
    jira_issues: conf({ ...comun, operacion: op("jira_issues"), jql: txt, campos: textos("campos"), issue: txt, proyecto: txt, tipo_issue: txt, resumen: txt, descripcion: txt,
      prioridad: txt, etiquetas: textos("etiquetas"), asignado: txt, campos_extra: objeto("campos_extra"), comentario: txt, estado: txt, max: entre("max", 1, 1000).optional() })
      .superRefine(regla("jira_issues", (c, o, falta, ctx) => {
        if (o === "buscar") falta("jql", "la consulta JQL");
        if (["ver", "comentar", "cambiar_estado"].includes(o)) {
          falta("issue", "la clave, p. ej. SOP-123");
          if (fijo(c.issue) && !CLAVE_JIRA.test(c.issue.trim().toUpperCase())) mal(ctx, "issue", `issue «${c.issue.slice(0, 40)}» no es una clave de Jira (PROYECTO-123)`);
        }
        if (o === "crear") { falta("proyecto", "la clave del proyecto"); falta("resumen", "el título"); }
        if (o === "comentar") falta("comentario", "el texto");
        if (o === "cambiar_estado") falta("estado", "la transición o el estado destino");
        if (Array.isArray(c.etiquetas) && c.etiquetas.some((x) => typeof x === "string" && x.trim().includes(" "))) mal(ctx, "etiquetas", "etiquetas de Jira van sin espacios");
      })),
    trello_tarjetas: conf({ ...comun, operacion: op("trello_tarjetas"), tablero: txt, lista: txt, tarjeta: txt, nombre: txt, descripcion: txt, vence: txt,
      etiquetas: textos("etiquetas"), posicion: z.enum(["arriba", "abajo"], { error: "posicion: arriba o abajo" }).optional(), comentario: txt, max: entre("max", 1, 1000).optional() })
      .superRefine(regla("trello_tarjetas", (c, o, falta, ctx) => {
        if (o === "listas") falta("tablero", "el id o enlace del tablero");
        if (o === "tarjetas" && !lleno(c.tablero) && !lleno(c.lista)) mal(ctx, "lista", "trello_tarjetas tarjetas necesita lista o tablero");
        if (o === "crear") { falta("lista", "la lista donde queda"); falta("nombre", "el título"); }
        if (o === "mover") { falta("tarjeta", "la tarjeta"); falta("lista", "la lista destino"); }
        if (o === "comentar") { falta("tarjeta", "la tarjeta"); falta("comentario", "el texto"); }
      })),
    asana_tareas: conf({ ...comun, operacion: op("asana_tareas"), proyecto: txt, asignado: txt, espacio: txt, tarea: txt, nombre: txt, notas: txt, vence: txt,
      solo_pendientes: opcional, comentario: txt, max: entre("max", 1, 1000).optional() })
      .superRefine(regla("asana_tareas", (c, o, falta, ctx) => {
        if (o === "listar" && !lleno(c.proyecto) && !lleno(c.asignado)) mal(ctx, "proyecto", "asana_tareas listar necesita proyecto o asignado («me» o un correo)");
        if (["ver", "completar", "comentar"].includes(o)) falta("tarea", "el gid de la tarea");
        if (o === "crear") falta("nombre", "el nombre de la tarea");
        if (o === "comentar") falta("comentario", "el texto");
        if (fijo(c.vence) && !/^\d{4}-\d{2}-\d{2}$/.test(c.vence.trim())) mal(ctx, "vence", "vence: AAAA-MM-DD");
      })),
    notion_paginas: conf({ ...comun, operacion: op("notion_paginas"), base_datos: txt, pagina: txt, filtro: objeto("filtro"),
      orden: z.array(z.record(z.string(), z.unknown()), { error: "orden: una lista de {property, direction}" }).optional(), propiedades: objeto("propiedades"),
      contenido: txt, max: entre("max", 1, 1000).optional() })
      .superRefine(regla("notion_paginas", (_c, o, falta) => {
        if (o === "consultar" || o === "crear") falta("base_datos", "el id o enlace de la base");
        if (o === "actualizar" || o === "agregar_bloques") falta("pagina", "el id o enlace de la página");
        if (o === "crear" || o === "actualizar") falta("propiedades", "{columna: valor}");
        if (o === "agregar_bloques") falta("contenido", "el texto");
      })),
    hubspot_crm: conf({ ...comun, objeto: z.enum(["contactos", "empresas", "negocios"], { error: "objeto: contactos, empresas o negocios" }).optional(),
      operacion: op("hubspot_crm"), texto: txt,
      filtros: z.array(z.looseObject({ propiedad: z.string().trim().min(1), operador: z.enum(OPERADORES_HUBSPOT).optional(), valor: z.unknown().optional() }),
        { error: "filtros: una lista de {propiedad, operador, valor}" }).optional(),
      propiedades: textos("propiedades"), id: txt, datos: objeto("datos"), nota: txt, max: entre("max", 1, 1000).optional() })
      .superRefine(regla("hubspot_crm", (_c, o, falta) => {
        if (["ver", "actualizar", "nota"].includes(o)) falta("id", "el id del registro");
        if (o === "crear" || o === "actualizar") falta("datos", "{propiedad: valor}");
        if (o === "nota") falta("nota", "el texto");
      })),
    salesforce_registros: conf({ ...comun, operacion: op("salesforce_registros"), soql: txt, objeto: txt, id: txt, campos: textos("campos"), datos: objeto("datos"),
      max: entre("max", 1, 2000).optional() })
      .superRefine(regla("salesforce_registros", (c, o, falta, ctx) => {
        if (o === "consultar") {
          falta("soql", "SELECT … FROM …");
          const m = typeof c.soql === "string" && c.soql.trim() ? soqlMala(c.soql) : null;
          if (m) mal(ctx, "soql", m);
        }
        if (["ver", "crear", "actualizar"].includes(o)) falta("objeto", "Account, Contact, Case…");
        if (o === "ver" || o === "actualizar") falta("id", "el Id del registro");
        if (o === "crear" || o === "actualizar") falta("datos", "{Campo: valor}");
        if (fijo(c.objeto) && !OBJETO_SF.test(c.objeto.trim())) mal(ctx, "objeto", "objeto: el nombre de API del objeto (Account, Case, Algo__c)");
        if (fijo(c.id) && !ID_SF.test(c.id.trim())) mal(ctx, "id", "id: el Id de Salesforce (15 o 18 caracteres)");
      })),
    zoho_crm: conf({ ...comun, modulo: p.textoReq("modulo"), operacion: op("zoho_crm"), criterio: txt, correo: txt, telefono: txt, texto: txt, id: txt, datos: objeto("datos"),
      max: entre("max", 1, 1000).optional() })
      .superRefine(regla("zoho_crm", (c, o, falta, ctx) => {
        if (o === "buscar" && !["criterio", "correo", "telefono", "texto"].some((k) => lleno(c[k]))) mal(ctx, "criterio", "zoho_crm buscar necesita criterio, correo, telefono o texto");
        if (o === "ver" || o === "actualizar") falta("id", "el id del registro");
        if (o === "crear" || o === "actualizar") falta("datos", "{Campo: valor}");
        if (fijo(c.modulo) && !MODULO_ZOHO.test(c.modulo.trim())) mal(ctx, "modulo", "modulo: el nombre de API del módulo (Leads, Contacts, Accounts, Deals…)");
      })),
    docusign_sobre: conf({ ...comun, operacion: op("docusign_sobre"), documento: archivosRef("documento").optional(), asunto: txt, mensaje: txt,
      firmantes: z.array(persona, { error: "firmantes: una lista de {nombre, correo}" }).optional(), copias: z.array(persona, { error: "copias: una lista de {nombre, correo}" }).optional(),
      ancla: txt, borrador: opcional, sobre: txt, nombre: txt })
      .superRefine(regla("docusign_sobre", (c, o, falta, ctx) => {
        if (o === "enviar") {
          falta("documento", "el PDF o Word, p. ej. «{{documento}}»");
          falta("asunto", "el asunto");
          falta("firmantes", "[{nombre, correo}]");
          for (const k of ["firmantes", "copias"]) {
            for (const x of Array.isArray(c[k]) ? (c[k] as C[]) : []) {
              const m = correoMalo(x?.correo);
              if (m) mal(ctx, k, `${k}: ${m}`);
            }
          }
        }
        if (o === "estado" || o === "descargar") {
          falta("sobre", "el id del sobre");
          if (fijo(c.sobre) && !ID_DOCUSIGN.test(c.sobre.trim())) mal(ctx, "sobre", "sobre: el id del sobre de DocuSign (un UUID)");
        }
      })),
  };
}

/** Una línea que dice qué hace cada nodo de la fase 3 (para el lienzo); false si no es de aquí. */
export function describirTrabajo(tipo: string, c: Record<string, unknown>, add: (d: string) => void): boolean {
  if (!(TIPOS_TRABAJO as string[]).includes(tipo)) return false;
  const t = tipo as TipoTrabajo;
  const o = String(c.operacion ?? OPERACIONES_TRABAJO[t][0]);
  add(`${NOMBRE_TRABAJO[t]} con la conexión «${String(c.credencial ?? "—")}» · ${o.replace("_", " ")}${t === "hubspot_crm" ? ` (${String(c.objeto ?? "contactos")})` : ""}${t === "zoho_crm" && c.modulo ? ` en ${String(c.modulo)}` : ""}`);
  if (LEEN_TRABAJO.has(o)) add("Solo lectura");
  else add(t === "docusign_sobre" && o === "enviar"
    ? "ESCRIBE AFUERA (manda el sobre a los firmantes): solo sale si una persona habilitó la escritura de la conexión; si no, queda retenido con todo armado"
    : "ESCRIBE AFUERA: solo sale si una persona habilitó la escritura de la conexión; si no, queda retenido con todo armado");
  return true;
}
