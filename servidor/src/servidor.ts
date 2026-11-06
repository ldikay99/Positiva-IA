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
import * as configuracion from "./configuracion.ts";
import { Motor, type Evento } from "./motor.ts";

const app = Fastify({ logger: { level: process.env.NIVEL_LOG ?? "info" }, bodyLimit: 4 * 1024 * 1024 });
const motor = new Motor();

await app.register(websocket);

app.get("/api/salud", async () => ({ ok: true, ...motor.estado() }));

app.get("/api/catalogo", async () => catalogo());

// ------------------------------------------------------------------- bandeja

app.get("/api/bandeja", async () => bandeja.listar());

app.get<{ Params: { caso: string } }>("/api/bandeja/:caso", async (req, res) => {
  const d = await bandeja.detalle(req.params.caso);
  return d ?? res.code(404).send({ error: "no existe" });
});

app.get<{ Params: { caso: string; archivo: string } }>("/api/bandeja/:caso/adjuntos/:archivo", async (req, res) => {
  const ruta = bandeja.rutaAdjunto(req.params.caso, req.params.archivo);
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
});

app.post("/api/corridas", async (req, res) => {
  const r = pedido.safeParse(req.body);
  if (!r.success) return res.code(400).send({ error: "pedido inválido" });
  const invalidos = r.data.casos.filter((c) => !bandeja.casoValido(c));
  if (invalidos.length) return res.code(400).send({ error: `casos inválidos: ${invalidos.join(", ")}` });
  const conf = configuracion.obtener();
  if (r.data.comoElJob && r.data.casos.length > 1) {
    const { lote, corridas } = motor.encolarLote(r.data.casos, conf, r.data.concurrencia ?? 0);
    return corridas.map((c) => ({ id: c.id, caso: c.caso, lote }));
  }
  return r.data.casos.map((caso) => {
    const c = motor.encolar(caso, conf);
    return { id: c.id, caso: c.caso };
  });
});

// ----------------------------------------------------------- buzón real

app.get<{ Querystring: { n?: string; fresco?: string } }>("/api/buzon", async (req, res) => {
  const n = Math.max(1, Math.min(150, Number(req.query.n ?? 50) || 50));
  try {
    return await buzon.listar(n, req.query.fresco === "1");
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
app.post("/api/buzon/correr", async (req, res) => {
  const r = pedidoDelBuzon.safeParse(req.body ?? {});
  if (!r.success) return res.code(400).send({ error: "pedido inválido" });
  try {
    const ids = r.data.ids?.length ? r.data.ids : (await buzon.listar(r.data.n ?? 10, true)).correos.map((c) => c.id);
    if (!ids.length) return res.code(404).send({ error: "la carpeta del job está vacía" });
    const casos = await buzon.traer(ids);
    bandeja.olvidar();
    const conf = configuracion.obtener();
    if (casos.length === 1) {
      const c = motor.encolar(casos[0]!, conf);
      return { lote: null, corridas: [{ id: c.id, caso: c.caso }] };
    }
    const { lote, corridas } = motor.encolarLote(casos, conf, r.data.concurrencia ?? 0);
    return { lote, corridas: corridas.map((c) => ({ id: c.id, caso: c.caso })) };
  } catch (e) {
    return res.code(502).send({ error: e instanceof Error ? e.message : String(e) });
  }
});

app.delete("/api/corridas/cola", async () => ({ canceladas: motor.cancelarCola() }));

app.get("/api/corridas", async () =>
  [...motor.corridas.values()]
    .map(({ eventos, configuracion: conf, ...c }) => ({ ...c, modelo: conf.ia.modelo, nEventos: eventos.length }))
    .sort((a, b) => b.creada - a.creada),
);

app.get<{ Params: { id: string } }>("/api/corridas/:id", async (req, res) => {
  const c = motor.corridas.get(req.params.id);
  return c ?? res.code(404).send({ error: "no existe" });
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

app.get("/api/eventos/recientes", async () => motor.recientes.slice(-1500));

app.get("/api/eventos", { websocket: true }, (socket) => {
  const enviar = (e: Evento) => {
    if (socket.readyState === socket.OPEN) socket.send(JSON.stringify(e));
  };
  enviar({ tipo: "motor", corrida: null, t: Date.now() / 1000, ...motor.estado() });
  motor.on("evento", enviar);
  socket.on("close", () => motor.off("evento", enviar));
});

function publicarConfiguracion(cambios: number): void {
  motor.emit("evento", { tipo: "configuracion_guardada", corrida: null, t: Date.now() / 1000, cambios, configuracion: configuracion.obtener() });
}

if (existsSync(config.web)) {
  await app.register(estatico, { root: config.web, wildcard: false });
  app.setNotFoundHandler((req, res) => (req.url.startsWith("/api/") ? res.code(404).send({ error: "no existe" }) : res.sendFile("index.html")));
}

const conf = await configuracion.cargar();
catalogo();
await motor.cargarHistorial();
motor.ajustar(conf.concurrencia);

for (const senal of ["SIGINT", "SIGTERM"] as const) {
  process.on(senal, () => {
    motor.detener();
    void app.close().then(() => process.exit(0));
  });
}

await app.listen({ port: config.puerto, host: config.host });
