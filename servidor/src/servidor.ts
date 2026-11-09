import Fastify from "fastify";
import websocket from "@fastify/websocket";
import estatico from "@fastify/static";
import { spawn } from "node:child_process";
import { createReadStream, existsSync } from "node:fs";
import { basename, resolve } from "node:path";
import { z } from "zod";
import { config } from "./config.ts";
import { catalogo } from "./catalogo.ts";
import * as bandeja from "./bandeja.ts";
import * as buzon from "./buzon.ts";
import * as chats from "./chats.ts";
import * as buzonTutelas from "./buzon_tutelas.ts";
import * as configuracion from "./configuracion.ts";
import { Motor, borrarCorridasDe, contarCorridasDe, planificador, type Corrida, type Evento } from "./motor.ts";
import * as recursos from "./recursos.ts";
import { limiteTrabajadorMb, memoriaDelSistema } from "./medicion.ts";
import * as espacio from "./espacio.ts";
import * as flujos from "./flujos.ts";
import * as credenciales from "./credenciales.ts";
import * as entornos from "./entornos.ts";
import * as programador from "./programador.ts";
import * as ganchos from "./ganchos.ts";
import * as compuerta from "./compuerta.ts";
import * as rutasIa from "./rutas_ia.ts";
import * as historial from "./historial.ts";
import * as plantillas from "./plantillas.ts";
import * as documentos from "./documentos.ts";
import * as notificaciones from "./notificaciones.ts";
import * as aprobaciones from "./aprobaciones.ts";
import * as aplazadas from "./aplazadas.ts";
import * as probar from "./probar.ts";
import * as oauth from "./oauth.ts";
import * as conectores from "./conectores.ts";
import { DISPARADORES_DE_SONDEO } from "./flujos_integraciones.ts";
import { consumoDe, obtenerVera } from "./ia.ts";
import { EventEmitter } from "node:events";

const app = Fastify({ logger: { level: process.env.NIVEL_LOG ?? "info" }, bodyLimit: 4 * 1024 * 1024 });
// Un DELETE (o cualquier pedido) con Content-Type JSON y sin cuerpo es válido: cuerpo vacío.
app.removeContentTypeParser("application/json");
app.addContentTypeParser("application/json", { parseAs: "string" }, (_req, cuerpo, listo) => {
  const texto = String(cuerpo);
  if (!texto.trim()) return listo(null, undefined);
  try {
    listo(null, JSON.parse(texto));
  } catch (e) {
    (e as Error & { statusCode?: number }).statusCode = 400;
    listo(e as Error, undefined);
  }
});
// Archivos de un flujo y correos de prueba: multipart/form-data llega entero y se parte en espacio.multipart.
app.addContentTypeParser("multipart/form-data", { parseAs: "buffer" }, (_req, cuerpo, listo) => listo(null, cuerpo));
// Un pool de trabajadores por flujo. Los eventos de todos salen por el mismo
// bus, cada uno con su flujo, y la web muestra solo los del flujo elegido.
const bus = new EventEmitter();
bus.setMaxListeners(0);
const motores = new Map<string, Motor>();

const arrancados = new Set<string>();

/** El pool de un flujo. Solo arranca trabajadores cuando hay algo que correr. */
function motorDe(id: string, arrancar = true): Motor | null {
  const existente = motores.get(id);
  if (existente) {
    if (arrancar) arrancarMotor(existente);
    return existente;
  }
  const f = flujos.obtener(id);
  if (!f) return null;
  // Cada corrida lleva lo suyo: definición, entrada, credenciales y entorno del flujo (declarativos)
  // o el entorno propio del flujo (de código). Los de código arrancan sus trabajadores con su entorno.
  const m = new Motor(f.id, f.trabajador, (c) => flujos.ordenPara(f.id, c));
  if (f.tipo === "codigo") {
    m.entornoDeProceso = () => entornos.paraProceso(f.id);
    // Lo que sale de sus trabajadores (eventos, consola, resultados) va sin los valores secretos de su entorno.
    m.secretosDeProceso = () => entornos.valoresSecretos(f.id);
    m.radicacionReal = () => compuerta.radicacionReal(f.id);
  }
  // Sus límites (trabajadores, corridas a la vez, cola, tiempo máximo): los guardados o los de su tipo.
  m.recursos = () => recursos.de(deRecursos(id));
  m.on("evento", (e: Evento) => bus.emit("evento", e));
  motores.set(id, m);
  void m.cargarHistorial();
  if (arrancar) arrancarMotor(m);
  return m;
}

/** Lo que recursos.ts necesita de un flujo para darle sus valores por defecto. */
function deRecursos(id: string): recursos.FlujoDeRecursos {
  const f = flujos.obtener(id);
  return { id, tipo: f?.tipo === "codigo" ? "codigo" : "declarativo", ...(f?.tipo === "codigo" ? { base: flujos.baseDe(id) ?? "pqrd" } : {}) };
}

/**
 * Los flujos de código tienen sus trabajadores calientes (arrancar el motor de PQRD tarda);
 * los declarativos los arrancan cuando llega trabajo y los retiran tras un rato sin él.
 */
function arrancarMotor(m: Motor): void {
  if (arrancados.has(m.flujo)) return;
  arrancados.add(m.flujo);
  m.ajustar(flujos.esDeCodigo(m.flujo) ? m.recursos().trabajadores : 0);
}

/** La cola de un flujo está llena: el pedido se rechaza con 429 y el porqué. */
const colaLlena = (m: Motor) => ({ error: `la cola de este flujo está llena (${m.enEspera()} en espera; su límite es ${m.recursos().cola_max}, en Entorno › Ejecución)` });

/** Lo que bandeja.ts necesita saber de un flujo para darle SU correspondencia. */
const deBandeja = (f: flujos.Flujo): bandeja.FlujoDeBandeja => ({ id: f.id, tipo: f.tipo, ...(f.base ? { base: f.base } : {}) });

const flujoDe = (q: unknown): string => (typeof q === "string" && q ? q : "pqrd");

await app.register(websocket);

/**
 * La salud de un flujo: sus trabajadores (CPU, memoria, cuánto llevan, alertas), su cola,
 * sus límites y su parte del tope global; y la de la plataforma (todos los flujos).
 */
app.get<{ Querystring: { flujo?: string } }>("/api/salud", async (req) => {
  const m = motorDe(flujoDe(req.query.flujo)) ?? motor;
  const cuotas = planificador.cuotas();
  const tope = recursos.topeGlobal();
  return {
    ok: true, ...m.estado(), flujo: m.flujo,
    recursos: recursos.de(deRecursos(m.flujo)),
    cuota: cuotas.get(m.flujo) ?? 0,
    incidentes: m.incidentes.slice(-10),
    plataforma: {
      tope_trabajadores: tope,
      vivos: planificador.vivos(),
      limite_trabajador_mb: limiteTrabajadorMb(tope),
      ...memoriaDelSistema(),
      flujos: [...motores.values()].filter((x) => x.procesos() || x.enEspera()).map((x) => ({ flujo: x.flujo, procesos: x.procesos(), en_cola: x.enEspera(), cuota: cuotas.get(x.flujo) ?? 0 })),
    },
  };
});

app.get<{ Params: { id: string } }>("/api/flujos/:id/recursos", async (req, res) => {
  if (!flujos.obtener(req.params.id)) return res.code(404).send({ error: "ese flujo no existe" });
  return recursos.de(deRecursos(req.params.id));
});

/** {trabajadores?, corridas_simultaneas?, cola_max?, tiempo_max_s?}; null vuelve al valor por defecto. Rige desde ya. */
app.put<{ Params: { id: string } }>("/api/flujos/:id/recursos", async (req, res) => {
  const id = req.params.id;
  if (!flujos.obtener(id)) return res.code(404).send({ error: "ese flujo no existe" });
  const r = await recursos.poner(deRecursos(id), req.body);
  if (!r.ok) return res.code(r.codigo).send({ error: r.error });
  const m = motores.get(id);
  // los de código siguen calientes con su nuevo número de trabajadores; los que sobran se retiran al quedar libres
  if (m && arrancados.has(id) && flujos.esDeCodigo(id)) m.ajustar(r.recursos.trabajadores);
  else planificador.pedir();
  avisarFlujo(id, { recursos: true });
  return r.recursos;
});

/** Reinicia un trabajador de un flujo (uno trabado): si está ocupado, sus corridas quedan en error con el motivo. */
app.post<{ Params: { id: string; trabajador: string } }>("/api/flujos/:id/trabajadores/:trabajador/reiniciar", async (req, res) => {
  const m = motores.get(req.params.id);
  if (!m || !m.reiniciarTrabajador(Number(req.params.trabajador), "reiniciado por una persona desde el tablero")) return res.code(404).send({ error: "ese trabajador no existe" });
  return { ok: true };
});

app.get<{ Querystring: { flujo?: string } }>("/api/catalogo", async (req, res) => {
  const c = flujos.catalogoDe(flujoDe(req.query.flujo));
  return c ?? res.code(404).send({ error: "ese flujo no existe" });
});

// -------------------------------------------------------------------- flujos

const sinDefinicion = (f: flujos.Flujo) => ({ ...f, trabajador: undefined, definicion: undefined, nodos: f.definicion?.nodos.length, borrable: !flujos.esOriginal(f.id) });

app.get("/api/flujos", async () => flujos.listar().map(sinDefinicion));

/** Los números de cada flujo, de sus propias corridas: lo que muestra el tablero. */
app.get("/api/tablero", async () =>
  flujos.listar().map((f) => {
    const m = motorDe(f.id, false);
    const lista = [...(m?.corridas.values() ?? [])].sort((a, b) => b.creada - a.creada);
    const desenlaces: Record<string, number> = {};
    const segundos: number[] = [];
    const vueltas: number[] = [];
    let tokens = 0;
    const porDia = new Map<string, { n: number; radicados: number }>();
    for (const c of lista) {
      const r = c.resultado;
      const accion = r ? String(r.accion) : c.estado === "en_cola" ? "en_cola" : "procesando";
      desenlaces[accion] = (desenlaces[accion] ?? 0) + 1;
      if (r && typeof r.segundos === "number") segundos.push(r.segundos);
      if (r && typeof r.vueltas === "number") vueltas.push(r.vueltas);
      tokens += Number(r?.tokens_entrada ?? 0) + Number(r?.tokens_salida ?? 0);
      const dia = new Date(c.creada).toISOString().slice(0, 10);
      const d = porDia.get(dia) ?? { n: 0, radicados: 0 };
      d.n += 1;
      if (accion === "radicado") d.radicados += 1;
      porDia.set(dia, d);
    }
    segundos.sort((a, b) => a - b);
    return {
      id: f.id, nombre: f.nombre, tipo: f.tipo, ...(f.base ? { base: f.base } : {}), ...(f.copia_de ? { copia_de: f.copia_de } : {}), disparador: f.disparador ?? null, borrable: !flujos.esOriginal(f.id),
      corridas: lista.length, desenlaces,
      segundosMediana: segundos.length ? segundos[Math.floor(segundos.length / 2)]! : 0,
      segundosP95: segundos.length ? segundos[Math.min(segundos.length - 1, Math.round(0.95 * (segundos.length - 1)))]! : 0,
      vueltasMedia: vueltas.length ? vueltas.reduce((a, b) => a + b, 0) / vueltas.length : 0, tokens, consumo: consumoDe(lista.map((c) => c.resultado)),
      enCurso: lista.filter((c) => !c.resultado).length,
      ultimas: lista.slice(0, 6).map((c) => ({ id: c.id, caso: c.caso, accion: String(c.resultado?.accion ?? c.estado), motivo: String(c.resultado?.motivo ?? ""), t: c.creada })),
      porDia: [...porDia.entries()].sort().slice(-14).map(([dia, v]) => ({ dia, ...v })),
      salud: m ? saludResumida(m) : null,
    };
  }),
);

/** Lo que el tablero muestra de los trabajadores de un flujo: cuántos, CPU y memoria, y sus alertas. */
function saludResumida(m: Motor) {
  const e = m.estado();
  const r = m.recursos();
  return {
    procesos: e.trabajadores.length,
    ocupados: e.trabajadores.filter((t) => t.corrida).length,
    en_cola: e.enCola.length,
    trabajadores_max: r.trabajadores,
    cola_max: r.cola_max,
    tiempo_max_s: r.tiempo_max_s,
    cpu: e.trabajadores.some((t) => t.cpu != null) ? Math.round(e.trabajadores.reduce((a, t) => a + (t.cpu ?? 0), 0)) : null,
    memoria_mb: e.trabajadores.some((t) => t.memoria_mb != null) ? e.trabajadores.reduce((a, t) => a + (t.memoria_mb ?? 0), 0) : null,
    alertas: e.trabajadores.flatMap((t) => t.alertas.map((texto) => ({ trabajador: t.id, texto, ocupado: !!t.corrida }))),
    error_inicio: e.errorInicio || null,
  };
}

app.get<{ Params: { id: string } }>("/api/flujos/:id", async (req, res) => {
  const f = flujos.obtener(req.params.id);
  return f ? { ...f, trabajador: undefined } : res.code(404).send({ error: "ese flujo no existe" });
});

app.post("/api/flujos/validar", async (req) => flujos.validar((req.body as { definicion?: unknown })?.definicion ?? req.body));

app.put<{ Params: { id: string } }>("/api/flujos/:id", async (req, res) => {
  const def = ((req.body as { definicion?: unknown })?.definicion ?? req.body) as Record<string, unknown>;
  const r = await flujos.guardar({ ...def, id: req.params.id }, (req.body as { nota?: string })?.nota);
  if (!r.ok) return res.code(400).send({ error: "el flujo no es válido", problemas: r.problemas });
  bus.emit("evento", { tipo: "flujo_guardado", corrida: null, t: Date.now() / 1000, flujo: r.flujo!.id, version: r.flujo!.version });
  return { flujo: { ...r.flujo, trabajador: undefined } };
});

/** Lo que se borra con un flujo (para que la persona lo vea antes de confirmar). */
async function loQueSeBorra(id: string) {
  const f = flujos.obtener(id)!;
  const variables = await entornos.listar(id);
  const usoDelEspacio = await espacio.uso(id);
  return {
    flujo: { id, nombre: f.nombre, tipo: f.tipo, ...(f.copia_de ? { copia_de: f.copia_de } : {}) },
    se_puede_borrar: !flujos.esOriginal(id),
    ...(flujos.esOriginal(id) ? { motivo: `«${f.nombre}» es un flujo de producción original: no se borra (puedes borrar sus copias)` } : {}),
    corridas: Math.max(await contarCorridasDe(id), motores.get(id)?.corridas.size ?? 0),
    versiones: f.tipo === "declarativo" ? ((await flujos.versiones(id))?.length ?? 1) : 0,
    variables: variables.filter((v) => !v.secreto).length,
    secretos: variables.filter((v) => v.secreto).length,
    archivos: usoDelEspacio.archivos,
    bytes: usoDelEspacio.bytes,
    correos_de_prueba: usoDelEspacio.correos_de_prueba + usoDelEspacio.correos_recibidos,
    tabla_del_flujo: usoDelEspacio.tabla,
    credenciales: credenciales.listar(id).filter((c) => c.alcance === id).map((c) => c.nombre),
    compuerta: flujos.esDeCodigo(id),
    programacion: flujos.esDeCodigo(id) || (f.disparador === "programado" || DISPARADORES_DE_SONDEO.has(f.disparador ?? "")),
  };
}

app.get<{ Params: { id: string } }>("/api/flujos/:id/borrado", async (req, res) => {
  if (!flujos.obtener(req.params.id)) return res.code(404).send({ error: "ese flujo no existe" });
  return loQueSeBorra(req.params.id);
});

/**
 * Borra un flujo con TODO lo suyo: corridas (en memoria y guardadas), versiones, entorno,
 * compuerta, programación, credenciales de su alcance, archivos, bandeja de prueba, su
 * tabla y lo que trajo su correo_nuevo. Los originales (pqrd, tutelas) no se borran.
 */
app.delete<{ Params: { id: string } }>("/api/flujos/:id", async (req, res) => {
  const id = req.params.id;
  if (!flujos.obtener(id)) return res.code(404).send({ error: "ese flujo no existe" });
  if (flujos.esOriginal(id)) return res.code(400).send({ error: "pqrd y tutelas son los flujos de producción originales: no se borran (sus copias sí)" });
  const resumen = await loQueSeBorra(id);
  motores.get(id)?.cancelarCola();
  motores.get(id)?.detener();
  motores.delete(id);
  arrancados.delete(id);
  if (!(await flujos.borrar(id))) return res.code(404).send({ error: "ese flujo no existe" });
  const corridas = await borrarCorridasDe(id);
  await compuerta.olvidar(id);
  await programador.olvidar(id);
  await recursos.olvidar(id);
  for (const c of resumen.credenciales) await credenciales.borrar(c);
  await espacio.borrarTodo(id);
  bandeja.olvidarDe(id);
  bus.emit("evento", { tipo: "flujo_borrado", corrida: null, t: Date.now() / 1000, flujo: id });
  return { ok: true, borrado: { ...resumen, corridas: Math.max(corridas, resumen.corridas) } };
});

// =====================================================================
// Plataforma de automatización (motor declarativo): tipos de nodo, correr,
// versiones, entorno por flujo, credenciales, manifiesto, webhooks y programador.
// =====================================================================

/** Lo confirma la web después de que una persona acepta explícitamente; Vera nunca lo manda. */
const confirmadoPorPersona = (req: { headers: Record<string, unknown> }) => String(req.headers["x-confirmado-por-persona"] ?? "").trim().toLowerCase() === "si";
// Rótulo de una corrida sin correo: fecha y hora más 4 caracteres al azar, para que dos corridas del mismo segundo se distingan.
const sello = () => `${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "")}-${Math.random().toString(36).slice(2, 6)}`;
const avisarFlujo = (id: string, extra: Record<string, unknown> = {}) => bus.emit("evento", { tipo: "flujo_guardado", corrida: null, t: Date.now() / 1000, flujo: id, ...extra });
/** Encola una corrida de un flujo declarativo sin correo (o con el caso de un correo_nuevo). Null si no existe o su cola está llena. */
function dispararFlujo(id: string, origen: string, datos: { entrada?: unknown; caso?: string }): Corrida | null {
  const f = flujos.obtener(id);
  const m = f?.tipo === "declarativo" ? motorDe(id) : null;
  if (!m || !m.cabe(1)) return null;
  return m.encolar(datos.caso ?? `${origen}-${sello()}`, configuracion.obtener(), { origen, entrada: datos.entrada ?? {} });
}

app.get("/api/nodos/tipos", async (_req, res) => {
  try {
    return await flujos.tiposDeNodo();
  } catch (e) {
    return res.code(500).send({ error: e instanceof Error ? e.message : String(e) });
  }
});

const pedidoDeCorrer = z.object({ entrada: z.record(z.string(), z.unknown()).optional(), caso: z.string().regex(/^[\w.-]{1,200}$/).optional() });

/** Corre un flujo declarativo: con caso (entrada_correo, correo_nuevo) o con una entrada (los demás disparadores). */
app.post<{ Params: { id: string } }>("/api/flujos/:id/correr", async (req, res) => {
  const f = flujos.obtener(req.params.id);
  if (!f) return res.code(404).send({ error: "ese flujo no existe" });
  if (f.tipo !== "declarativo" || !f.definicion) return res.code(400).send({ error: "los flujos de código se corren con /api/corridas o desde el buzón" });
  const r = pedidoDeCorrer.safeParse(req.body ?? {});
  if (!r.success) return res.code(400).send({ error: "pedido inválido: {entrada?: objeto, caso?: texto}" });
  const disp = flujos.disparadorDe(f.definicion);
  const m = motorDe(f.id)!;
  if (!m.cabe(1)) return res.code(429).send(colaLlena(m));
  if (disp && flujos.DISPARADORES_DE_CORREO.has(disp.tipo)) {
    if (!r.data.caso || !bandeja.casoValido(r.data.caso)) return res.code(400).send({ error: `este flujo empieza con ${disp.tipo}: manda caso (un correo de SU correspondencia: GET /api/bandeja?flujo=${f.id})` });
    // solo un correo de su propia correspondencia (nunca el universo de PQRD ni lo de otro flujo)
    if (!(await bandeja.esDe(deBandeja(f), r.data.caso))) return res.code(400).send({ error: `el caso «${r.data.caso}» no es de la correspondencia de este flujo (súbelo a su bandeja de prueba o tráelo de su buzón)` });
    const c = m.encolar(r.data.caso, configuracion.obtener(), { origen: "manual", entrada: r.data.entrada ?? {} });
    return { id: c.id, caso: c.caso };
  }
  const faltan = espacio.archivosQueFaltan(f.id, r.data.entrada, f.definicion);
  if (faltan.length) return res.code(400).send({ error: `estos archivos no están en el flujo: ${faltan.join(", ")} (súbelos con POST /api/flujos/${f.id}/archivos)` });
  if (disp?.tipo === "entrada_archivos" && !espacio.resolverArchivos(f.id, r.data.entrada, f.definicion).length) return res.code(400).send({ error: "este flujo empieza con entrada_archivos: sube al menos un archivo y mándalo en entrada.archivos" });
  const c = m.encolar(r.data.caso ?? `${disp?.tipo === "entrada_archivos" ? "archivos" : "manual"}-${sello()}`, configuracion.obtener(), { origen: "manual", entrada: r.data.entrada ?? {} });
  return { id: c.id, caso: c.caso };
});

app.get<{ Params: { id: string } }>("/api/flujos/:id/versiones", async (req, res) => {
  if (flujos.esDeCodigo(req.params.id)) return [];
  return (await flujos.versiones(req.params.id)) ?? res.code(404).send({ error: "ese flujo no existe" });
});

app.post<{ Params: { id: string } }>("/api/flujos/:id/restaurar", async (req, res) => {
  const r = await flujos.restaurar(req.params.id, Number((req.body as { version?: unknown })?.version));
  if (!r.ok) return res.code(r.codigo).send({ error: r.error, problemas: r.problemas });
  avisarFlujo(r.flujo.id, { version: r.flujo.version });
  return { flujo: { ...r.flujo, trabajador: undefined } };
});

/**
 * Clona cualquier flujo (también pqrd y tutelas: la copia es una instancia propia del mismo motor).
 * {nombre?, id_nuevo?} → {flujo}. /duplicar queda como alias (Vera lo usa con id_nuevo).
 */
const clonarRuta = async (req: { params: { id: string }; body: unknown }, res: { code: (n: number) => { send: (x: unknown) => unknown } }) => {
  const b = (req.body ?? {}) as { id_nuevo?: unknown; nombre?: unknown };
  const r = await flujos.clonar(req.params.id, b.nombre, b.id_nuevo);
  if (!r.ok) return res.code(r.codigo).send({ error: r.error, problemas: r.problemas });
  motorDe(r.flujo.id, r.flujo.tipo === "codigo");
  avisarFlujo(r.flujo.id, { version: r.flujo.version ?? null, clonado_de: req.params.id });
  return { flujo: { ...r.flujo, trabajador: undefined } };
};
app.post<{ Params: { id: string } }>("/api/flujos/:id/clonar", clonarRuta);
app.post<{ Params: { id: string } }>("/api/flujos/:id/duplicar", clonarRuta);

app.patch<{ Params: { id: string } }>("/api/flujos/:id", async (req, res) => {
  const r = await flujos.renombrar(req.params.id, (req.body ?? {}) as { nombre?: unknown; descripcion?: unknown });
  if (!r.ok) return res.code(r.codigo).send({ error: r.error, problemas: r.problemas });
  avisarFlujo(r.flujo.id, { version: r.flujo.version });
  return { flujo: { ...r.flujo, trabajador: undefined } };
});

app.get<{ Params: { id: string } }>("/api/flujos/:id/manifiesto", async (req, res) => {
  const m = await flujos.manifiesto(req.params.id);
  if (!m) return res.code(404).send({ error: "ese flujo no existe" });
  return { ...m, programador: { ultima: programador.estado()[`p:${req.params.id}`] ?? null } };
});

// ------------------------------------------------- entorno por flujo

// Se puede preparar el entorno de un flujo antes de crearlo (sus {{entorno.X}} tienen que existir para guardarlo).
const idDeFlujoValido = (id: string) => /^[a-z][a-z0-9-]{1,40}$/.test(id);

app.get<{ Params: { id: string } }>("/api/flujos/:id/entorno", async (req, res) =>
  idDeFlujoValido(req.params.id) ? entornos.listar(req.params.id) : res.code(404).send({ error: "ese flujo no existe" }));

/** Si cambia el entorno de un flujo de código, sus trabajadores se reinician al quedar libres (ninguna corrida se corta). */
const reiniciarSiEsDeCodigo = (id: string) => {
  if (flujos.esDeCodigo(id)) motores.get(id)?.reiniciarTrabajadores();
};

app.put<{ Params: { id: string; nombre: string } }>("/api/flujos/:id/entorno/:nombre", async (req, res) => {
  if (!idDeFlujoValido(req.params.id)) return res.code(400).send({ error: "id de flujo inválido" });
  const r = await entornos.poner(req.params.id, req.params.nombre, req.body, confirmadoPorPersona(req));
  if (!r.ok) return res.code(r.codigo).send({ error: r.error });
  reiniciarSiEsDeCodigo(req.params.id);
  avisarFlujo(req.params.id, { entorno: r.variable.nombre });
  return r.variable;
});

app.delete<{ Params: { id: string; nombre: string } }>("/api/flujos/:id/entorno/:nombre", async (req, res) => {
  if (!idDeFlujoValido(req.params.id)) return res.code(400).send({ error: "id de flujo inválido" });
  if (!(await entornos.borrar(req.params.id, req.params.nombre))) return res.code(404).send({ error: "esa variable no existe" });
  reiniciarSiEsDeCodigo(req.params.id);
  return { ok: true };
});

// ------------------------------------------- compuerta de radicación (flujos de código)

const avisarCompuerta = (id: string) =>
  bus.emit("evento", { tipo: "compuerta", corrida: null, t: Date.now() / 1000, flujo: id, ...compuerta.obtener(id), radicacion_real_efectiva: compuerta.radicacionReal(id), texto: compuerta.rotulo(id) });

app.get<{ Params: { id: string } }>("/api/flujos/:id/compuerta", async (req, res) => {
  if (!flujos.esDeCodigo(req.params.id)) return res.code(404).send({ error: "solo los flujos de código (pqrd, tutelas) tienen compuerta de radicación" });
  return { ...compuerta.obtener(req.params.id), radicacion_real_efectiva: compuerta.radicacionReal(req.params.id), texto: compuerta.rotulo(req.params.id) };
});

/**
 * Encender exige las DOS llaves: la confirmación de una persona (la web la manda tras
 * la confirmación explícita; Vera nunca) y PERMITIR_RADICACION_REAL=si en el contenedor.
 * Apagar nunca pide nada. Al cambiar, los trabajadores del flujo se reemplazan al quedar
 * libres: ninguna corrida nueva arranca con la compuerta anterior.
 */
app.put<{ Params: { id: string } }>("/api/flujos/:id/compuerta", async (req, res) => {
  const r = await compuerta.cambiar(req.params.id, req.body, confirmadoPorPersona(req));
  if (!r.ok) return res.code(r.codigo).send({ error: r.error });
  if (r.cambio) {
    motores.get(req.params.id)?.reiniciarTrabajadores();
    app.log.warn({ flujo: req.params.id, radicacion_real: r.compuerta.radicacion_real }, "cambió la compuerta de radicación");
  }
  avisarCompuerta(req.params.id);
  return { ...r.compuerta, radicacion_real_efectiva: compuerta.radicacionReal(req.params.id), texto: compuerta.rotulo(req.params.id) };
});

// ------------------------------------------- programación como el job (flujos de código)

app.get<{ Params: { id: string } }>("/api/flujos/:id/programacion", async (req, res) => {
  const p = programador.programacionDe(req.params.id);
  if (!p || !flujos.esDeCodigo(req.params.id)) return res.code(404).send({ error: "solo los flujos de código (pqrd, tutelas) tienen programación como el job" });
  return { ...p, del_job: programador.PROGRAMACION_DEL_JOB[flujos.baseDe(req.params.id) ?? req.params.id], tope_por_corrida_s: programador.TOPE_DE_CORRIDA_S, en_curso: programador.corridaProgramada(req.params.id), ultima_ejecucion: programador.estado()[`j:${req.params.id}`] ?? null, compuerta: compuerta.rotulo(req.params.id) };
});

app.put<{ Params: { id: string } }>("/api/flujos/:id/programacion", async (req, res) => {
  if (!flujos.esDeCodigo(req.params.id)) return res.code(404).send({ error: "solo los flujos de código (pqrd, tutelas) tienen programación como el job" });
  const r = await programador.ponerProgramacion(req.params.id, req.body, flujos.problemaCron);
  if (!r.ok) return res.code(r.codigo).send({ error: r.error });
  bus.emit("evento", { tipo: "programacion", corrida: null, t: Date.now() / 1000, flujo: req.params.id, ...r.programacion });
  return r.programacion;
});

// ------------------------------------------------------- credenciales

app.get<{ Querystring: { flujo?: string } }>("/api/credenciales", async (req) => credenciales.listar(req.query.flujo || undefined));

app.get("/api/credenciales/tipos", async () => credenciales.tipos());

app.put<{ Params: { nombre: string } }>("/api/credenciales/:nombre", async (req, res) => {
  const alcance = (req.body as { alcance?: unknown })?.alcance;
  if (typeof alcance === "string" && alcance !== "global" && !flujos.obtener(alcance)) return res.code(400).send({ error: `alcance: no existe el flujo «${alcance}»` });
  const r = await credenciales.guardar(req.params.nombre, req.body, confirmadoPorPersona(req));
  return r.ok ? r.credencial : res.code(r.codigo ?? 400).send({ error: r.error });
});

app.patch<{ Params: { nombre: string } }>("/api/credenciales/:nombre", async (req, res) => {
  if (!confirmadoPorPersona(req)) return res.code(403).send({ error: "habilitar o quitar la escritura de una credencial solo lo hace una persona desde la web (X-Confirmado-Por-Persona: si)" });
  const permitir = (req.body as { permitir_escritura?: unknown })?.permitir_escritura;
  if (typeof permitir !== "boolean") return res.code(400).send({ error: "manda {permitir_escritura: true|false}" });
  const r = await credenciales.permitirEscritura(req.params.nombre, permitir);
  if (!r.ok) return res.code(r.codigo ?? 400).send({ error: r.error });
  app.log.warn({ credencial: req.params.nombre, permitir_escritura: permitir }, "cambió el permiso de escritura de una credencial");
  return r.credencial;
});

app.delete<{ Params: { nombre: string } }>("/api/credenciales/:nombre", async (req, res) =>
  (await credenciales.borrar(req.params.nombre)) ? { ok: true } : res.code(404).send({ error: "esa credencial no existe" }));

// ---------------------------------------------------------- webhooks

await rutasIa.registrar(app);  // IA: proveedores, modelos, probar, global y Vera (rutas_ia.ts)
await oauth.registrar(app);  // Conexiones OAuth (Google, Microsoft 365) y «Probar conexión» (oauth.ts)
await conectores.registrar(app);  // OpenAPI: analizar una especificación (conectores.ts)

// Producto: historial por flujo, plantillas y notificaciones (historial.ts, plantillas.ts, notificaciones.ts)
await historial.registrar(app, { motorDe, motores: () => motores.values() });
await plantillas.registrar(app, { avisarFlujo });
await documentos.registrar(app);  // la biblioteca de plantillas de documentos: Word, Excel, PowerPoint y correo HTML (documentos.ts)
await notificaciones.registrar(app, { motores: () => motores.values(), escuchar: (f) => { bus.on("evento", f); return () => bus.off("evento", f); }, emitir: (e) => bus.emit("evento", e) });

// «Probar este paso», datos de prueba fijados por paso y enlaces de descarga firmados (probar.ts, descargas.ts)
await probar.registrar(app, {
  buscar: (id) => {
    for (const m of motores.values()) {
      const c = m.corridas.get(id);
      if (c) return c;
    }
    return null;
  },
});

// Aprobación humana y corridas aplazadas (aprobaciones.ts, aplazadas.ts)
await aprobaciones.registrar(app, { reanudar: (id, datos) => dispararFlujo(id, "aprobacion", datos), emitir: (e) => bus.emit("evento", e) });
await aplazadas.registrar(app, { disparar: dispararFlujo, escuchar: (f) => { bus.on("evento", f); return () => bus.off("evento", f); }, emitir: (e) => bus.emit("evento", e) });

await ganchos.registrar(app, {
  obtener: (id) => flujos.obtener(id),
  disparar: (id, entrada) => dispararFlujo(id, "webhook", { entrada }),
  escuchar: (f) => {
    bus.on("evento", f);
    return () => bus.off("evento", f);
  },
  // multipart: los archivos se guardan en el espacio del flujo, con las mismas reglas que «Correr»
  guardarArchivos: (id, partes) => guardarArchivos(id, partes, "webhook"),
});

// ------------------------------------------------------- archivos del flujo

/** Revisa y guarda los archivos que llegan para un flujo. Video y audio nunca (extensión, tipo y contenido). */
async function guardarArchivos(id: string, partes: { nombre: string; tipo: string; datos: Buffer }[], origen: string) {
  const f = flujos.obtener(id)!;
  const reglas = espacio.reglasDe(f.definicion);
  const archivos: espacio.FichaArchivo[] = [];
  const rechazados: { nombre: string; error: string }[] = [];
  for (const p of partes) {
    const problema = espacio.problemaDeArchivo(p.nombre, p.tipo, p.datos, reglas);
    if (problema) rechazados.push({ nombre: p.nombre, error: problema });
    else archivos.push(await espacio.guardarArchivo(id, p.nombre, p.tipo, p.datos, origen));
  }
  return { archivos, rechazados };
}

/** Las partes de un pedido de subida: multipart (campo «archivo» o «archivos») o JSON {nombre, tipo, datos(base64)} / {archivos: [...]}. */
function partesDeSubida(req: { headers: Record<string, unknown>; body: unknown }): { nombre: string; tipo: string; datos: Buffer }[] {
  const tipo = String(req.headers["content-type"] ?? "");
  if (Buffer.isBuffer(req.body) && /multipart\/form-data/i.test(tipo))
    return espacio.multipart(req.body, tipo).filter((p) => p.archivo !== undefined).map((p) => ({ nombre: p.archivo || "archivo", tipo: p.tipo, datos: p.datos }));
  const b = (req.body ?? {}) as { nombre?: string; tipo?: string; datos?: string; archivos?: { nombre?: string; tipo?: string; datos?: string }[] };
  const lista = Array.isArray(b.archivos) ? b.archivos : b.datos ? [b] : [];
  return lista.filter((x) => typeof x?.datos === "string").slice(0, 50).map((x) => ({ nombre: String(x.nombre ?? "archivo").slice(0, 160), tipo: String(x.tipo ?? "").slice(0, 120), datos: Buffer.from(String(x.datos), "base64") }));
}

app.get<{ Params: { id: string } }>("/api/flujos/:id/archivos", async (req, res) => {
  const f = flujos.obtener(req.params.id);
  if (!f) return res.code(404).send({ error: "ese flujo no existe" });
  const r = espacio.reglasDe(f.definicion);
  return { archivos: await espacio.listarArchivos(f.id), reglas: { tipos_permitidos: [...r.tipos], max_mb: r.maxMb, max_archivos: r.maxArchivos } };
});

/** Sube archivos al espacio del flujo → {archivos: [{id, nombre, tipo, tamano, sha256, subido}], rechazados: [{nombre, error}]}. */
app.post<{ Params: { id: string } }>("/api/flujos/:id/archivos", { bodyLimit: 130 * 1024 * 1024 }, async (req, res) => {
  const f = flujos.obtener(req.params.id);
  if (!f) return res.code(404).send({ error: "ese flujo no existe" });
  let partes: { nombre: string; tipo: string; datos: Buffer }[];
  try {
    partes = partesDeSubida(req);
  } catch (e) {
    return res.code(400).send({ error: e instanceof Error ? e.message : String(e) });
  }
  if (!partes.length) return res.code(400).send({ error: "falta el archivo (multipart con campo «archivo», o JSON {nombre, tipo, datos en base64})" });
  const r = await guardarArchivos(f.id, partes, "web");
  if (!r.archivos.length) return res.code(400).send({ error: r.rechazados.map((x) => x.error).join(" · "), ...r });
  return r;
});

app.get<{ Params: { id: string; archivo: string } }>("/api/flujos/:id/archivos/:archivo", async (req, res) => {
  const a = espacio.archivoDe(req.params.id, req.params.archivo);
  if (!a) return res.code(404).send({ error: "no existe" });
  // un HTML (o SVG/XML) del flujo se ve aislado: sin scripts ni acceso a la plataforma
  if (/html|svg|xml/i.test(a.tipo || "") || /\.(html?|svg|xml)$/i.test(a.nombre)) res.header("Content-Security-Policy", "sandbox; default-src 'none'; img-src data: https:; style-src 'unsafe-inline'");
  return res.header("Content-Type", a.tipo || "application/octet-stream").header("Content-Disposition", `inline; filename*=UTF-8''${encodeURIComponent(a.nombre)}`).header("Cache-Control", "private, max-age=3600").send(createReadStream(a.ruta));
});

/** El texto de un archivo que el navegador no muestra (Word, Excel, ZIP…), con la conversión del motor. */
app.get<{ Params: { id: string; archivo: string } }>("/api/flujos/:id/archivos/:archivo/texto", async (req, res) => {
  const a = espacio.archivoDe(req.params.id, req.params.archivo);
  if (!a) return res.code(404).send({ error: "no existe" });
  const texto = await new Promise<string>((resolver) => {
    const p = spawn(config.python, [resolve(config.raiz, "puente", "texto_de_archivo.py"), a.ruta, a.nombre], { env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let salida = "";
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.on("exit", () => resolver(salida));
  });
  return { nombre: a.nombre, texto };
});

app.delete<{ Params: { id: string; archivo: string } }>("/api/flujos/:id/archivos/:archivo", async (req, res) =>
  (await espacio.borrarArchivo(req.params.id, req.params.archivo)) ? { ok: true } : res.code(404).send({ error: "no existe" }));

// ------------------------------------------------------------------- bandeja
//
// La correspondencia es POR FLUJO (?flujo=): pqrd ve su universo descargado y lo traído de
// su buzón; tutelas, solo lo suyo; un flujo declarativo, su bandeja de prueba y su buzón.
// Sin ?flujo= se entiende pqrd (como antes).

const flujoDeBandeja = (q: unknown) => flujos.obtener(flujoDe(q));

app.get<{ Querystring: { flujo?: string } }>("/api/bandeja", async (req, res) => {
  const f = flujoDeBandeja(req.query.flujo);
  if (!f) return res.code(404).send({ error: "ese flujo no existe" });
  return f.correspondencia ? bandeja.listarDe(deBandeja(f)) : [];
});

/** Sube correos .eml/.msg a la bandeja de prueba de un flujo con entrada_correo → {correos: [{caso, asunto, adjuntos}], rechazados}. */
app.post<{ Querystring: { flujo?: string } }>("/api/bandeja/prueba", { bodyLimit: 130 * 1024 * 1024 }, async (req, res) => {
  const f = flujoDeBandeja(req.query.flujo);
  if (!f) return res.code(404).send({ error: "ese flujo no existe" });
  if (f.tipo !== "declarativo" || f.disparador !== "entrada_correo") return res.code(400).send({ error: "la bandeja de prueba es de los flujos que arrancan con «Entrada de correo»" });
  let partes: { nombre: string; tipo: string; datos: Buffer }[];
  try {
    partes = partesDeSubida(req);
  } catch (e) {
    return res.code(400).send({ error: e instanceof Error ? e.message : String(e) });
  }
  if (!partes.length) return res.code(400).send({ error: "falta el correo (.eml o .msg)" });
  const correos: { caso: string; asunto: string; adjuntos: number }[] = [];
  const rechazados: { nombre: string; error: string }[] = [];
  for (const p of partes) {
    const r = await espacio.correoDePrueba(f.id, p.nombre, p.datos);
    if (r.ok) correos.push({ caso: r.caso, asunto: r.asunto, adjuntos: r.adjuntos });
    else rechazados.push({ nombre: p.nombre, error: r.error });
  }
  bandeja.olvidarDe(f.id);
  if (!correos.length) return res.code(400).send({ error: rechazados.map((x) => x.error).join(" · "), correos, rechazados });
  return { correos, rechazados };
});

app.delete<{ Params: { caso: string }; Querystring: { flujo?: string } }>("/api/bandeja/prueba/:caso", async (req, res) => {
  const f = flujoDeBandeja(req.query.flujo);
  if (!f || f.tipo !== "declarativo") return res.code(404).send({ error: "ese flujo no tiene bandeja de prueba" });
  const ok = await espacio.borrarCorreoDePrueba(f.id, req.params.caso);
  bandeja.olvidarDe(f.id);
  return ok ? { ok: true } : res.code(404).send({ error: "no existe" });
});

/**
 * «Buzón del flujo»: lee (solo lectura) el buzón que tiene configurado su entrada_correo y deja cada correo
 * en SU bandeja → {carpeta, correos: Correo[]} (los traídos, con fuente «buzon_flujo»).
 */
app.get<{ Querystring: { flujo?: string; n?: string } }>("/api/bandeja/buzon", async (req, res) => {
  const f = flujoDeBandeja(req.query.flujo);
  if (!f) return res.code(404).send({ error: "ese flujo no existe" });
  const orden = flujos.ordenDeBuzon(f.id);
  if (!orden) return res.code(404).send({ error: "este flujo no tiene un buzón propio: ponle una credencial (imap o microsoft_graph) a su «Entrada de correo»" });
  const n = Math.max(1, Math.min(50, Number(req.query.n ?? 20) || 20));
  const r = await new Promise<{ ok?: boolean; carpeta?: string; casos?: string[]; error?: string }>((resolver) => {
    const p = spawn(config.python, [resolve(config.raiz, "puente", "flujo_declarativo.py"), "buzon"], { env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let salida = "";
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    const reloj = setTimeout(() => p.kill(), 120_000);
    p.on("exit", () => {
      clearTimeout(reloj);
      try {
        resolver(JSON.parse(salida.trim().split("\n").pop() ?? "{}"));
      } catch {
        resolver({ ok: false, error: "el buzón no respondió" });
      }
    });
    p.stdin.end(JSON.stringify({ ...orden, n }));
  });
  if (!r.ok) return res.code(502).send({ error: r.error ?? "no se pudo leer el buzón" });
  bandeja.olvidarDe(f.id);
  const todos = await bandeja.listarDe(deBandeja(f));
  const traidos = new Set(r.casos ?? []);
  return { carpeta: r.carpeta ?? "", correos: todos.filter((c) => traidos.has(c.caso)) };
});

app.get<{ Params: { caso: string }; Querystring: { flujo?: string } }>("/api/bandeja/:caso", async (req, res) => {
  // con ?flujo= solo un correo de ESE flujo; sin él, la búsqueda de antes (pqrd y lo traído del buzón)
  const f = req.query.flujo ? flujos.obtener(req.query.flujo) : undefined;
  const d = f ? await bandeja.detalleDe(deBandeja(f), req.params.caso) : await bandeja.detalle(req.params.caso);
  return d ?? res.code(404).send({ error: "no existe" });
});

app.get<{ Params: { caso: string; archivo: string }; Querystring: { flujo?: string } }>("/api/bandeja/:caso/adjuntos/:archivo", async (req, res) => {
  const f = req.query.flujo ? flujos.obtener(req.query.flujo) : undefined;
  const ruta = f ? await bandeja.rutaAdjuntoDe(deBandeja(f), req.params.caso, req.params.archivo) : bandeja.rutaAdjunto(req.params.caso, req.params.archivo);
  if (!ruta || !existsSync(ruta)) return res.code(404).send({ error: "no existe" });
  return res.header("Content-Disposition", `inline; filename="${basename(ruta)}"`).send(createReadStream(ruta));
});

// ------------------------------------------------------------- configuración

app.get("/api/configuracion", async () => ({ ...configuracion.obtener(), modelos: configuracion.MODELOS }));

const conNota = z.object({ configuracion: z.unknown(), nota: z.string().max(300).optional() });

app.put("/api/configuracion", async (req, res) => {
  const cuerpo = conNota.safeParse(req.body);
  const entrada = cuerpo.success && cuerpo.data.configuracion !== undefined ? cuerpo.data.configuracion : req.body;
  const v = configuracion.validar(entrada);
  if (!v.ok) return res.code(400).send({ error: v.error });
  const cambios = await configuracion.guardar(v.valor, cuerpo.success ? cuerpo.data.nota : undefined);
  motor.ajustar(v.valor.concurrencia);
  publicarConfiguracion(cambios.length);
  return { configuracion: v.valor, cambios };
});

app.get("/api/configuracion/historial", async () => configuracion.historial());

app.get("/api/configuracion/exportar", async (_req, res) =>
  res.header("Content-Disposition", `attachment; filename="positiva-ia-configuracion-${new Date().toISOString().slice(0, 10)}.json"`).send(configuracion.obtener()),
);

app.get("/api/perfiles", async () => configuracion.perfiles());

app.post("/api/perfiles", async (req, res) => {
  const r = z.object({ nombre: z.string().min(1).max(60), descripcion: z.string().max(300).default("") }).safeParse(req.body);
  if (!r.success || !(await configuracion.guardarPerfil(r.data.nombre.trim(), r.data.descripcion))) return res.code(400).send({ error: "nombre de perfil inválido" });
  return configuracion.perfiles();
});

app.post<{ Params: { nombre: string } }>("/api/perfiles/:nombre/aplicar", async (req, res) => {
  const c = await configuracion.leerPerfil(req.params.nombre);
  if (!c) return res.code(404).send({ error: "perfil no encontrado o inválido" });
  const cambios = await configuracion.guardar(c, `perfil ${req.params.nombre}`);
  motor.ajustar(c.concurrencia);
  publicarConfiguracion(cambios.length);
  return { configuracion: c, cambios };
});

app.delete<{ Params: { nombre: string } }>("/api/perfiles/:nombre", async (req, res) =>
  (await configuracion.borrarPerfil(req.params.nombre)) ? configuracion.perfiles() : res.code(404).send({ error: "no existe" }),
);

// Prueba una regla de búsqueda con el motor de expresiones de Python, el mismo del pipeline.
app.post("/api/reglas/probar", async (req, res) => {
  const r = z.object({ patron: z.string().max(20000), flags: z.string().max(100).default(""), texto: z.string().max(200000) }).safeParse(req.body);
  if (!r.success) return res.code(400).send({ error: "pedido inválido" });
  const hijo = spawn(config.python, [resolve(config.raiz, "puente", "probar_regex.py")], { env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
  let salida = "";
  hijo.stdout.setEncoding("utf8").on("data", (d: string) => (salida += d));
  hijo.stdin.end(JSON.stringify(r.data));
  const reloj = setTimeout(() => hijo.kill(), 5000);
  await new Promise((ok) => hijo.on("close", ok));
  clearTimeout(reloj);
  try {
    return JSON.parse(salida) as unknown;
  } catch {
    return res.code(500).send({ ok: false, error: "la prueba no terminó (tope de 5 s)" });
  }
});

// ------------------------------------------------------------------- corridas

const pedido = z.object({
  casos: z.array(z.string()).min(1).max(500),
  /** Juntos como una corrida del job: un orquestador, de a `concurrencia`, de a uno por remitente. */
  comoElJob: z.boolean().optional(),
  concurrencia: z.number().int().min(1).max(10).optional(),
  flujo: z.string().optional(),
});

app.post("/api/corridas", async (req, res) => {
  const r = pedido.safeParse(req.body);
  if (!r.success) return res.code(400).send({ error: "pedido inválido" });
  const invalidos = r.data.casos.filter((c) => !bandeja.casoValido(c));
  if (invalidos.length) return res.code(400).send({ error: `casos inválidos: ${invalidos.join(", ")}` });
  const conf = configuracion.obtener();
  const f = flujos.obtener(flujoDe(r.data.flujo));
  const m = f ? motorDe(f.id) : null;
  if (!f || !m) return res.code(404).send({ error: "ese flujo no existe" });
  // cada flujo corre solo SU correspondencia (un flujo nuevo nunca corre el universo de PQRD)
  const ajenos = [];
  for (const caso of r.data.casos) if (!(await bandeja.esDe(deBandeja(f), caso))) ajenos.push(caso);
  if (ajenos.length) return res.code(400).send({ error: `no son de la correspondencia de «${f.nombre}»: ${ajenos.slice(0, 10).join(", ")}` });
  if (!m.cabe(r.data.casos.length)) return res.code(429).send(colaLlena(m));
  if (r.data.comoElJob && f.lote && r.data.casos.length > 1) {
    const { lote, corridas } = m.encolarLote(r.data.casos, conf, r.data.concurrencia ?? 0);
    return corridas.map((c) => ({ id: c.id, caso: c.caso, lote, flujo: f.id }));
  }
  return r.data.casos.map((caso) => {
    const c = m.encolar(caso, conf);
    return { id: c.id, caso: c.caso, flujo: f.id };
  });
});

// ------------------------------------------------------------------ chat IA
//
// Conversaciones separadas: cada una guarda sus mensajes, adjuntos y los pasos
// que dio la IA, y se le pasan completos en cada pregunta.

app.get("/api/chats", async () => chats.listar());

app.post("/api/chats", async (req) => {
  const b = (req.body ?? {}) as { flujo?: string; titulo?: string };
  return chats.crear(typeof b.flujo === "string" ? b.flujo : "pqrd", typeof b.titulo === "string" && b.titulo.trim() ? b.titulo.trim().slice(0, 80) : undefined);
});

app.get<{ Params: { id: string } }>("/api/chats/:id", async (req, res) => (await chats.leer(req.params.id)) ?? res.code(404).send({ error: "esa conversación no existe" }));

app.patch<{ Params: { id: string } }>("/api/chats/:id", async (req, res) => {
  const c = await chats.leer(req.params.id);
  const titulo = (req.body as { titulo?: string })?.titulo;
  if (!c) return res.code(404).send({ error: "esa conversación no existe" });
  if (typeof titulo !== "string" || !titulo.trim()) return res.code(400).send({ error: "el título no puede quedar vacío" });
  c.titulo = titulo.trim().slice(0, 80);
  await chats.guardar(c);
  return c;
});

// Qué está viendo la persona en este momento: el navegador lo reporta cada vez que cambia la pantalla
// (y justo después de aplicar algo que pidió Vera). Vera lo consulta antes de afirmar que algo está en pantalla.
const pantallas = new Map<string, { pantalla: Record<string, unknown>; t: number }>();
app.put<{ Params: { id: string } }>("/api/chats/:id/pantalla", async (req) => {
  const p = req.body && typeof req.body === "object" ? (req.body as Record<string, unknown>) : {};
  if (JSON.stringify(p).length > 8000) return { ok: false };
  pantallas.set(req.params.id, { pantalla: p, t: Date.now() });
  if (pantallas.size > 200) pantallas.delete(pantallas.keys().next().value as string);
  return { ok: true };
});
app.get<{ Params: { id: string } }>("/api/chats/:id/pantalla", async (req) => {
  const x = pantallas.get(req.params.id);
  return x ? { pantalla: x.pantalla, hace_ms: Date.now() - x.t } : { pantalla: null, nota: "el navegador no ha reportado su pantalla en esta conversación" };
});

app.delete<{ Params: { id: string } }>("/api/chats/:id", async (req, res) => ((await chats.borrar(req.params.id)) ? { ok: true } : res.code(404).send({ error: "esa conversación no existe" })));

// Un adjunto llega como JSON con su contenido en base64 (hasta 25 MB).
app.post<{ Params: { id: string } }>("/api/chats/:id/adjuntos", { bodyLimit: 36 * 1024 * 1024 }, async (req, res) => {
  const c = await chats.leer(req.params.id);
  const b = req.body as { nombre?: string; tipo?: string; datos?: string };
  if (!c) return res.code(404).send({ error: "esa conversación no existe" });
  if (!b?.nombre || !b?.datos) return res.code(400).send({ error: "falta el archivo" });
  const datos = Buffer.from(b.datos, "base64");
  if (datos.length > 25 * 1024 * 1024) return res.code(413).send({ error: "el archivo pasa de 25 MB" });
  return chats.guardarAdjunto(c.id, b.nombre.slice(0, 160), (b.tipo || "application/octet-stream").slice(0, 120), datos);
});

app.get<{ Params: { id: string; adj: string } }>("/api/chats/:id/adjuntos/:adj", async (req, res) => {
  const a = await chats.adjunto(req.params.id, req.params.adj);
  if (!a || !existsSync(a.ruta)) return res.code(404).send({ error: "no existe" });
  return res.header("Content-Type", a.tipo).header("Content-Disposition", `inline; filename*=UTF-8''${encodeURIComponent(a.nombre)}`).header("Cache-Control", "private, max-age=3600").send(createReadStream(a.ruta));
});

/** El texto de un adjunto que el navegador no muestra (Word, Excel, ZIP…), con la conversión del motor. */
app.get<{ Params: { id: string; adj: string } }>("/api/chats/:id/adjuntos/:adj/texto", async (req, res) => {
  const a = await chats.adjunto(req.params.id, req.params.adj);
  if (!a) return res.code(404).send({ error: "no existe" });
  const texto = await new Promise<string>((resolver) => {
    const p = spawn(config.python, [resolve(config.raiz, "puente", "texto_de_archivo.py"), a.ruta, a.nombre], { env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let salida = "";
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.on("exit", () => resolver(salida));
  });
  return { nombre: a.nombre, texto };
});

const pedidoDeMensaje = z.object({
  texto: z.string().max(40000),
  adjuntos: z.array(z.string()).max(20).default([]),
  flujo: z.string().nullish(),
  modelo: z.string().max(60).nullish(),
  pantalla: z.record(z.string(), z.unknown()).nullish(),
});

/**
 * Un mensaje a una conversación: se guarda, la IA lo responde con TODA la
 * conversación como contexto (y sus adjuntos), cada paso se transmite apenas
 * ocurre y la respuesta queda guardada al terminar.
 */
app.post<{ Params: { id: string } }>("/api/chats/:id/mensajes", async (req, res) => {
  const c = await chats.leer(req.params.id);
  const r = pedidoDeMensaje.safeParse(req.body);
  if (!c) return res.code(404).send({ error: "esa conversación no existe" });
  if (!r.success || (!r.data.texto.trim() && !r.data.adjuntos.length)) return res.code(400).send({ error: "el mensaje está vacío" });
  const adjuntos = (await Promise.all(r.data.adjuntos.map((a) => chats.adjunto(c.id, a).then((x) => (x ? { id: a, nombre: x.nombre, tipo: x.tipo, tamano: x.tamano } : null))))).filter((x): x is chats.AdjuntoChat => x !== null);
  c.mensajes.push({ rol: "usuario", texto: r.data.texto, t: Date.now(), adjuntos });
  if (c.titulo === "Nueva conversación" && r.data.texto.trim()) c.titulo = r.data.texto.trim().replace(/\s+/g, " ").slice(0, 60);
  if (r.data.flujo) c.flujo = r.data.flujo;
  await chats.guardar(c);

  const contexto = await Promise.all(
    c.mensajes.map(async (m) => ({
      rol: m.rol,
      texto: m.texto,
      pasos: (m.pasos ?? []).map((p) => ({ nombre: p.nombre, argumentos: p.argumentos, resultado: (p.resultado ?? "").slice(0, 600) })),
      adjuntos: await Promise.all((m.adjuntos ?? []).map(async (a) => ({ nombre: a.nombre, tipo: a.tipo, ruta: (await chats.adjunto(c.id, a.id))?.ruta ?? "" }))),
    })),
  );

  res.hijack();
  res.raw.writeHead(200, { "Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-cache", "X-Accel-Buffering": "no" });
  const respuesta: chats.MensajeChat = { rol: "asistente", texto: "", t: Date.now(), pasos: [] };
  const p = spawn(config.python, [resolve(config.raiz, "puente", "asistente.py")], {
    env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1", API_INTERNA: `http://127.0.0.1:${config.puerto}` },
    stdio: ["pipe", "pipe", "pipe"],
  });
  let pendiente = "";
  p.stdout.setEncoding("utf8").on("data", (t: string) => {
    res.raw.write(t);
    pendiente += t;
    const lineas = pendiente.split(String.fromCharCode(10));
    pendiente = lineas.pop() ?? "";
    for (const l of lineas) {
      try {
        const d = JSON.parse(l) as { tipo: string; texto?: string; nombre?: string; argumentos?: unknown; resultado?: string; flujos_cambiados?: string[]; propuesta?: { accion: string; id: string }; archivos?: unknown[] };
        if (d.tipo === "herramienta") respuesta.pasos!.push({ nombre: d.nombre ?? "", argumentos: d.argumentos });
        else if (d.tipo === "resultado_herramienta" && respuesta.pasos!.length) respuesta.pasos![respuesta.pasos!.length - 1]!.resultado = d.resultado;
        else if (d.tipo === "texto") respuesta.texto = (respuesta.texto ? `${respuesta.texto}\n\n` : "") + (d.texto ?? "");
        else if (d.tipo === "error") { respuesta.texto = `No pude terminar: ${d.texto}`; respuesta.error = true; }
        else if (d.tipo === "fin") respuesta.cambiados = d.flujos_cambiados ?? [];
        else if (d.tipo === "propuesta" && d.propuesta) (respuesta.propuestas ??= []).push({ accion: d.propuesta.accion, id: d.propuesta.id });
        else if (d.tipo === "archivos" && Array.isArray(d.archivos)) (respuesta.archivos ??= []).push(...(d.archivos as NonNullable<chats.MensajeChat["archivos"]>).slice(0, 20));
      } catch {
        /* línea cortada */
      }
    }
  });
  let errores = "";
  p.stderr.setEncoding("utf8").on("data", (t: string) => (errores += t));
  p.on("exit", async (codigo) => {
    if (codigo && !respuesta.texto) {
      respuesta.texto = `No pude terminar: ${errores.trim().split(String.fromCharCode(10)).pop() || `código ${codigo}`}`;
      respuesta.error = true;
      res.raw.write(JSON.stringify({ tipo: "error", texto: respuesta.texto }) + String.fromCharCode(10));
    }
    const actual = (await chats.leer(c.id)) ?? c;
    actual.mensajes.push(respuesta);
    await chats.guardar(actual);
    res.raw.end();
  });
  res.raw.on("close", () => {
    if (p.exitCode === null) p.kill();
  });
  // Lo que vino con el mensaje es la pantalla de este instante.
  if (r.data.pantalla) pantallas.set(c.id, { pantalla: r.data.pantalla, t: Date.now() });
  // La IA de Vera (aparte de la de los flujos) y, si usa una conexión, sus valores solo para este proceso.
  const iaVera = obtenerVera().texto;
  const credVera = iaVera.credencial ? credenciales.paraCorrida("vera", [iaVera.credencial])[iaVera.credencial]?.valores : undefined;
  p.stdin.end(JSON.stringify({ mensajes: contexto, flujo: r.data.flujo ?? c.flujo, modelo: r.data.modelo, conversacion: c.id, pantalla: r.data.pantalla ?? {}, ia_vera: iaVera, credencial_ia: credVera ?? null }));
});

/** El resultado de una propuesta (la persona confirmó o descartó el botón). */
app.post<{ Params: { id: string } }>("/api/chats/:id/propuestas", async (req, res) => {
  const c = await chats.leer(req.params.id);
  const b = req.body as { mensaje?: number; accion?: string; objetivo?: string; estado?: "hecha" | "descartada" };
  if (!c) return res.code(404).send({ error: "esa conversación no existe" });
  const m = c.mensajes[Number(b?.mensaje)];
  const p = m?.propuestas?.find((x) => x.accion === b.accion && x.id === b.objetivo);
  if (!p || (b.estado !== "hecha" && b.estado !== "descartada")) return res.code(400).send({ error: "esa propuesta no existe" });
  p.estado = b.estado;
  await chats.guardar(c);
  return { ok: true };
});

// ------------------------------------------------------------- voz en vivo
//
// Un turno de una llamada: lo guarda el puente de voz al terminar cada respuesta.
const turnoDeVoz = z.object({
  usuario: z.object({ texto: z.string().max(20000) }),
  asistente: z.object({
    texto: z.string().max(60000),
    pasos: z.array(z.object({ nombre: z.string(), argumentos: z.unknown().optional(), resultado: z.string().optional() })).max(60).default([]),
    cambiados: z.array(z.string()).max(20).default([]),
    propuestas: z.array(z.object({ accion: z.string(), id: z.string() })).max(10).default([]),
    archivos: z.array(z.object({ corrida: z.string(), id: z.string(), nombre: z.string(), tipo: z.string(), tamano: z.number().optional() })).max(20).default([]),
  }),
});

app.post<{ Params: { id: string } }>("/api/chats/:id/turno", async (req, res) => {
  if (!["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(req.ip)) return res.code(403).send({ error: "solo el puente de voz guarda turnos" });
  const c = await chats.leer(req.params.id);
  const r = turnoDeVoz.safeParse(req.body);
  if (!c) return res.code(404).send({ error: "esa conversación no existe" });
  if (!r.success) return res.code(400).send({ error: "turno inválido" });
  const t = Date.now();
  if (r.data.usuario.texto.trim()) c.mensajes.push({ rol: "usuario", texto: r.data.usuario.texto, t, voz: true });
  const a = r.data.asistente;
  if (a.texto.trim() || a.pasos.length || a.archivos.length) c.mensajes.push({ rol: "asistente", texto: a.texto, t: t + 1, voz: true, pasos: a.pasos, cambiados: a.cambiados, propuestas: a.propuestas, ...(a.archivos.length ? { archivos: a.archivos } : {}) });
  if (c.titulo === "Nueva conversación" && r.data.usuario.texto.trim()) c.titulo = r.data.usuario.texto.trim().replace(/\s+/g, " ").slice(0, 60);
  await chats.guardar(c);
  return { ok: true };
});

/**
 * La llamada: el navegador manda el micrófono (PCM 16 kHz) y mensajes de control;
 * `puente/voz.py` habla con Gemini Live y devuelve la voz (PCM 24 kHz) y eventos.
 * Entre este servidor y Python van tramas: 1 byte de tipo (J/A) + 4 de longitud.
 */
let llamadasActivas = 0;
app.get<{ Params: { id: string } }>("/api/voz/:id", { websocket: true }, (socket, req) => {
  // Los manejadores se ponen de inmediato: lo que llegue antes de tener la
  // conversación y el proceso de voz se guarda y se entrega después.
  const antes: { datos: Buffer; binario: boolean }[] = [];
  let entregar: ((datos: Buffer, binario: boolean) => void) | null = null;
  let cerrada = false;
  socket.on("message", (datos: Buffer, binario: boolean) => (entregar ? entregar(datos, binario) : antes.length < 400 && antes.push({ datos, binario })));
  socket.on("close", () => (cerrada = true));
  void iniciarLlamada(socket, req.params.id, antes, (f) => (entregar = f), () => cerrada);
});

type SocketDeVoz = { readyState: number; OPEN: number; send: (d: Buffer | string, o?: { binary: boolean }) => void; close: (codigo?: number, motivo?: string) => void; on: (ev: "close", f: () => void) => void };

async function iniciarLlamada(
  socket: SocketDeVoz,
  id: string,
  antes: { datos: Buffer; binario: boolean }[],
  alListo: (f: (datos: Buffer, binario: boolean) => void) => void,
  cerrada: () => boolean,
): Promise<void> {
  const c = await chats.leer(id);
  if (!c) return socket.close(4404, "esa conversación no existe");
  if (llamadasActivas >= 3) return socket.close(4429, "hay demasiadas llamadas abiertas");
  if (cerrada()) return;
  llamadasActivas++;
  const p = spawn(config.python, [resolve(config.raiz, "puente", "voz.py")], {
    env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1", API_INTERNA: `http://127.0.0.1:${config.puerto}` },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const trama = (tipo: "J" | "A", datos: Buffer) => {
    if (!p.stdin.writable) return;
    const cab = Buffer.alloc(5);
    cab.write(tipo, 0, "latin1");
    cab.writeUInt32BE(datos.length, 1);
    p.stdin.write(Buffer.concat([cab, datos]));
  };
  let pendiente = Buffer.alloc(0);
  p.stdout.on("data", (b: Buffer) => {
    pendiente = Buffer.concat([pendiente, b]);
    while (pendiente.length >= 5) {
      const n = pendiente.readUInt32BE(1);
      if (pendiente.length < 5 + n) break;
      const tipo = String.fromCharCode(pendiente[0]!);
      const datos = pendiente.subarray(5, 5 + n);
      pendiente = pendiente.subarray(5 + n);
      if (socket.readyState !== socket.OPEN) continue;
      if (tipo === "A") socket.send(datos, { binary: true });
      else socket.send(datos.toString("utf8"));
    }
  });
  let errores = "";
  p.stderr.setEncoding("utf8").on("data", (t: string) => (errores = (errores + t).slice(-4000)));
  const recibir = (datos: Buffer, binario: boolean) => {
    if (binario) {
      if (datos.length <= 64 * 1024) trama("A", datos);
      return;
    }
    try {
      const m = JSON.parse(datos.toString("utf8")) as { tipo?: string };
      if (m.tipo === "inicio") {
        const x = m as Record<string, unknown>;
        x.conversacion = c.id;
        const iaVoz = obtenerVera().voz;
        x.ia_voz = iaVoz;
        x.credencial_ia = iaVoz.credencial ? (credenciales.paraCorrida("vera", [iaVoz.credencial])[iaVoz.credencial]?.valores ?? null) : null;
      }
      const pant = (m as { pantalla?: unknown }).pantalla;
      if ((m.tipo === "inicio" || m.tipo === "pantalla") && pant && typeof pant === "object") pantallas.set(c.id, { pantalla: pant as Record<string, unknown>, t: Date.now() });
      trama("J", Buffer.from(JSON.stringify(m)));
    } catch {
      /* mensaje inválido */
    }
  };
  for (const x of antes.splice(0)) recibir(x.datos, x.binario);
  alListo(recibir);
  const cerrar = () => {
    if (p.exitCode === null) {
      trama("J", Buffer.from(JSON.stringify({ tipo: "fin" })));
      setTimeout(() => p.exitCode === null && p.kill(), 4000);
    }
  };
  socket.on("close", cerrar);
  p.on("exit", (codigo) => {
    llamadasActivas--;
    if (codigo && errores) app.log.warn({ voz: errores.split("\n").slice(-5).join(" | ") }, "la voz terminó con error");
    if (socket.readyState === socket.OPEN) socket.close(1000, "fin de la llamada");
  });
  if (cerrada()) cerrar();
}

// ----------------------------------------------------------- buzón real

// Cada flujo con buzón real lee SU carpeta de producción: PQRD la de AUTOMATIZACION,
// tutelas la de notificaciones judiciales. Nunca se cruzan.
// Las copias de pqrd y tutelas leen la misma carpeta con SU entorno (sus credenciales).
const buzonDe = (flujo: string) => { const b = flujos.baseDe(flujo); return b === "tutelas" ? buzonTutelas : b === "pqrd" ? buzon : null; };

app.get<{ Querystring: { n?: string; fresco?: string; flujo?: string } }>("/api/buzon", async (req, res) => {
  const n = Math.max(1, Math.min(150, Number(req.query.n ?? 50) || 50));
  const flujo = flujoDe(req.query.flujo);
  const b = buzonDe(flujo);
  if (!b) return res.code(404).send({ error: "este flujo no tiene buzón real" });
  try {
    return await b.listar(n, req.query.fresco === "1", flujo);
  } catch (e) {
    return res.code(502).send({ error: e instanceof Error ? e.message : String(e) });
  }
});

const pedidoDelBuzon = z.object({
  n: z.number().int().min(1).max(150).optional(),
  ids: z.array(z.string().min(10).max(400)).max(150).optional(),
  concurrencia: z.number().int().min(1).max(10).optional(),
});

/**
 * Corre ahora lo que tomaría el job: trae del buzón real (solo lectura) los
 * correos de AUTOMATIZACION en su orden y los corre juntos con un orquestador.
 */
type CorridaDelBuzon = { lote: string | null; corridas: { id: string; caso: string }[] } | { vacio: string } | { lleno: string };

/**
 * Lo que hace el job en cada corrida: toma del buzón real (solo lectura) el siguiente
 * lote en su orden, lo baja y lo corre. Lo usan /api/buzon/correr y la programación.
 */
async function correrBuzon(flujo: string, pedidoDe: { n?: number; ids?: string[]; concurrencia?: number }, origen?: string): Promise<CorridaDelBuzon> {
  const conf = configuracion.obtener();
  const extra = origen ? { origen } : {};
  // antes de bajar nada: si no caben en su cola, no se traen
  const pool = motorDe(flujo)!;
  if (!pool.cabe(pedidoDe.ids?.length || pedidoDe.n || 10)) return { lleno: colaLlena(pool).error };
  if (flujos.baseDe(flujo) === "tutelas") {
    // El job de tutelas toma el hilo como unidad: se pide el correo principal de cada hilo.
    const ids = pedidoDe.ids?.length ? pedidoDe.ids : (await buzonTutelas.listar(pedidoDe.n ?? 10, true, flujo)).correos.filter((c) => c.principal).map((c) => c.id);
    if (!ids.length) return { vacio: "la carpeta del job de tutelas está vacía" };
    const casos = await buzonTutelas.traer(ids, flujo);
    bandeja.olvidar();
    const m = motorDe(flujo)!;
    const corridas = casos.map((caso) => m.encolar(caso, conf, extra));
    return { lote: null, corridas: corridas.map((c) => ({ id: c.id, caso: c.caso })) };
  }
  const ids = pedidoDe.ids?.length ? pedidoDe.ids : (await buzon.listar(pedidoDe.n ?? 10, true, flujo)).correos.map((c) => c.id);
  if (!ids.length) return { vacio: "la carpeta del job está vacía" };
  const casos = await buzon.traer(ids, flujo);
  bandeja.olvidar();
  const m = motorDe(flujo)!;
  if (casos.length === 1) {
    const c = m.encolar(casos[0]!, conf, extra);
    return { lote: null, corridas: [{ id: c.id, caso: c.caso }] };
  }
  const { lote, corridas } = m.encolarLote(casos, conf, pedidoDe.concurrencia ?? 0, origen);
  return { lote, corridas: corridas.map((c) => ({ id: c.id, caso: c.caso })) };
}

app.post<{ Querystring: { flujo?: string } }>("/api/buzon/correr", async (req, res) => {
  const r = pedidoDelBuzon.safeParse(req.body ?? {});
  if (!r.success) return res.code(400).send({ error: "pedido inválido" });
  const flujo = flujoDe(req.query.flujo);
  if (!buzonDe(flujo)) return res.code(404).send({ error: "este flujo no tiene buzón real" });
  try {
    const x = await correrBuzon(flujo, r.data);
    if ("vacio" in x) return res.code(404).send({ error: x.vacio });
    if ("lleno" in x) return res.code(429).send({ error: x.lleno });
    return x;
  } catch (e) {
    return res.code(502).send({ error: e instanceof Error ? e.message : String(e) });
  }
});

app.delete<{ Querystring: { flujo?: string } }>("/api/corridas/cola", async (req) => ({ canceladas: motorDe(flujoDe(req.query.flujo))?.cancelarCola() ?? 0 }));

app.get<{ Querystring: { flujo?: string } }>("/api/corridas", async (req) =>
  [...(motorDe(flujoDe(req.query.flujo))?.corridas.values() ?? [])]
    .map(({ eventos, configuracion: conf, ...c }) => ({ ...c, modelo: conf.ia.modelo, nEventos: eventos.length }))
    .sort((a, b) => b.creada - a.creada),
);

/**
 * Una corrida. Con ?esperar=N (hasta 120 s) la respuesta llega apenas la corrida termine
 * (o a los N s, como esté): quien espera un resultado no tiene que preguntar cada pocos segundos.
 */
app.get<{ Params: { id: string }; Querystring: { esperar?: string } }>("/api/corridas/:id", async (req, res) => {
  const buscar = () => {
    for (const m of motores.values()) {
      const c = m.corridas.get(req.params.id);
      if (c) return { m, c };
    }
    return null;
  };
  const x = buscar();
  if (!x) return res.code(404).send({ error: "no existe" });
  const esperar = Math.min(120, Math.max(0, Number(req.query.esperar) || 0));
  const terminal = () => x.c.estado === "terminada" || x.c.estado === "fallida";
  if (esperar && !terminal()) {
    await new Promise<void>((listo) => {
      const reloj = setTimeout(fin, esperar * 1000);
      function oir(e: Evento) {
        if (e.corrida === x!.c.id && e.tipo === "resultado") setImmediate(fin);
      }
      function fin() {
        clearTimeout(reloj);
        x!.m.off("evento", oir);
        listo();
      }
      x.m.on("evento", oir);
      res.raw.once("close", fin);
    });
  }
  return x.c;
});

// Hojas renderizadas y texto por hoja de cada corrida: los escribe el trabajador
// en la carpeta de trabajo, que no sale de esta máquina.
const TIPOS: Record<string, string> = { jpg: "image/jpeg", json: "application/json; charset=utf-8" };

app.get<{ Params: { id: string; "*": string } }>("/api/corridas/:id/archivos/*", async (req, res) => {
  const { id } = req.params;
  const archivo = req.params["*"];
  if (!/^[\w-]+$/.test(id) || !/^(textos\/)?[\w.-]+\.(jpg|json)$/.test(archivo) || archivo.includes("..")) return res.code(400).send({ error: "ruta inválida" });
  const base = resolve(config.datos, "trabajo", "visor", id);
  const ruta = resolve(base, archivo);
  if (!ruta.startsWith(base) || !existsSync(ruta)) return res.code(404).send({ error: "no existe" });
  return res
    .header("Content-Type", TIPOS[archivo.split(".").pop()!]!)
    .header("Cache-Control", "private, max-age=3600")
    .send(createReadStream(ruta));
});

app.get<{ Querystring: { flujo?: string } }>("/api/eventos/recientes", async (req) => motorDe(flujoDe(req.query.flujo))?.recientes.slice(-1500) ?? []);

app.get("/api/eventos", { websocket: true }, (socket) => {
  const enviar = (e: Evento) => {
    if (socket.readyState === socket.OPEN) socket.send(JSON.stringify(e));
  };
  for (const m of motores.values()) enviar({ tipo: "motor", corrida: null, t: Date.now() / 1000, flujo: m.flujo, ...m.estado() });
  bus.on("evento", enviar);
  socket.on("close", () => bus.off("evento", enviar));
});

function publicarConfiguracion(cambios: number): void {
  bus.emit("evento", { tipo: "configuracion_guardada", corrida: null, t: Date.now() / 1000, flujo: "pqrd", cambios, configuracion: configuracion.obtener() });
}

if (existsSync(config.web)) {
  await app.register(estatico, { root: config.web, wildcard: false });
  app.setNotFoundHandler((req, res) => (req.url.startsWith("/api/") ? res.code(404).send({ error: "no existe" }) : res.sendFile("index.html")));
}

await configuracion.cargar();
// PQRD toma sus trabajadores por defecto de la concurrencia de Ajustes, como siempre.
recursos.enlace.concurrenciaPqrd = () => configuracion.obtener().concurrencia;
await recursos.cargar();
await credenciales.cargar();
await entornos.precargar();
await compuerta.cargar();
// Los scripts del buzón real leen con las credenciales del entorno de su flujo (si las tiene) y nada secreto vuelve sin máscara.
// Cada uno con el entorno del flujo que lo pide (pqrd, tutelas o una copia suya).
buzon.enlace.entorno = (f) => entornos.paraProceso(f);
buzon.enlace.secretos = (f) => entornos.valoresSecretos(f);
buzonTutelas.enlace.entorno = (f) => entornos.paraProceso(f);
buzonTutelas.enlace.secretos = (f) => entornos.valoresSecretos(f);
for (const id of compuerta.FLUJOS_CON_COMPUERTA) app.log.info({ flujo: id }, compuerta.rotulo(id));
// Una sola vez: el entorno global de antes pasa a ser el entorno propio de pqrd (sigue llegando igual a sus órdenes).
const migradas = await entornos.migrarDesdeConfiguracion("pqrd", configuracion.obtener().entorno);
if (migradas) app.log.info({ migradas }, "entorno global migrado al flujo pqrd");
await flujos.cargar();
catalogo();
const motor = motorDe("pqrd")!;
// Las copias de código arrancan sus trabajadores cuando se abren o se corren (no todas al iniciar).
for (const f of flujos.listar()) motorDe(f.id, f.tipo === "codigo" && f.original === true);
for (const f of flujos.listar().filter((x) => x.copia_de && x.tipo === "codigo")) app.log.info({ flujo: f.id, copia_de: f.copia_de }, compuerta.rotulo(f.id));
await programador.iniciar({
  flujos: () => flujos.listar(),
  enCurso: (id, origen) => motores.get(id)?.enCurso(origen) ?? 0,
  disparar: (id, origen, datos) => void dispararFlujo(id, origen, datos),
  ordenDeSondeo: (id) => flujos.ordenDeSondeo(id),
  aviso: (msg, datos) => {
    app.log.warn(datos ?? {}, msg);
    if (typeof datos?.flujo === "string") bus.emit("evento", { tipo: "aviso_programador", corrida: null, t: Date.now() / 1000, flujo: datos.flujo, mensaje: msg, ...(datos.error ? { error: String(datos.error) } : {}) });
  },
  // Flujos de código: cada toque toma el siguiente lote del buzón real, como el job (simulado con la compuerta apagada).
  correrBuzon: async (id, n) => {
    const x = await correrBuzon(id, { n }, "programado");
    return "vacio" in x || "lleno" in x ? [] : x.corridas.map((c) => c.id);
  },
  cortar: (id, ids, motivo) => void motores.get(id)?.cortar(ids, motivo),
  vivas: (id, ids) => ids.filter((x) => { const c = motores.get(id)?.corridas.get(x); return c && (c.estado === "en_cola" || c.estado === "procesando"); }).length,
});

for (const senal of ["SIGINT", "SIGTERM"] as const) {
  process.on(senal, () => {
    programador.detener();
    for (const m of motores.values()) m.detener();
    void app.close().then(() => process.exit(0));
  });
}

await app.listen({ port: config.puerto, host: config.host });
