import Fastify from "fastify";
import websocket from "@fastify/websocket";
import estatico from "@fastify/static";
import { createReadStream, existsSync } from "node:fs";
import { basename } from "node:path";
import { z } from "zod";
import { config } from "./config.ts";
import { catalogo } from "./catalogo.ts";
import * as bandeja from "./bandeja.ts";
import * as configuracion from "./configuracion.ts";
import { Motor, type Evento } from "./motor.ts";

const app = Fastify({ logger: { level: process.env.NIVEL_LOG ?? "info" } });
const motor = new Motor();

await app.register(websocket);

app.get("/api/salud", async () => ({ ok: true, ...motor.estado() }));

app.get("/api/catalogo", async () => catalogo());

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

app.get("/api/configuracion", async () => ({ ...configuracion.obtener(), modelos: configuracion.MODELOS }));

app.put("/api/configuracion", async (req, res) => {
  const v = configuracion.validar(req.body);
  if (!v.ok) return res.code(400).send({ error: v.error });
  await configuracion.guardar(v.valor);
  motor.ajustar(v.valor.concurrencia);
  return v.valor;
});

const pedido = z.object({ casos: z.array(z.string()).min(1).max(500), configuracion: z.unknown().optional() });

app.post("/api/corridas", async (req, res) => {
  const r = pedido.safeParse(req.body);
  if (!r.success) return res.code(400).send({ error: "pedido inválido" });
  let conf = configuracion.obtener();
  if (r.data.configuracion !== undefined) {
    const v = configuracion.validar(r.data.configuracion);
    if (!v.ok) return res.code(400).send({ error: v.error });
    conf = v.valor;
  }
  const invalidos = r.data.casos.filter((c) => !bandeja.casoValido(c));
  if (invalidos.length) return res.code(400).send({ error: `casos inválidos: ${invalidos.join(", ")}` });
  return r.data.casos.map((caso) => {
    const c = motor.encolar(caso, conf);
    return { id: c.id, caso: c.caso };
  });
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

app.get("/api/eventos", { websocket: true }, (socket) => {
  const enviar = (e: Evento) => {
    if (socket.readyState === socket.OPEN) socket.send(JSON.stringify(e));
  };
  enviar({ tipo: "motor", corrida: null, t: Date.now() / 1000, ...motor.estado() });
  motor.on("evento", enviar);
  socket.on("close", () => motor.off("evento", enviar));
});

if (existsSync(config.web)) {
  await app.register(estatico, { root: config.web, wildcard: false });
  app.setNotFoundHandler((req, res) => (req.url.startsWith("/api/") ? res.code(404).send({ error: "no existe" }) : res.sendFile("index.html")));
}

const conf = await configuracion.cargar();
catalogo();
motor.ajustar(conf.concurrencia);

for (const senal of ["SIGINT", "SIGTERM"] as const) {
  process.on(senal, () => {
    motor.detener();
    void app.close().then(() => process.exit(0));
  });
}

await app.listen({ port: config.puerto, host: config.host });
