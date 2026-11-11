import type { FastifyInstance } from "fastify";
import { z } from "zod";
import type { Evento } from "./motor.ts";

/**
 * Las tareas largas que Vera lanza en una conversación (por escrito o en la llamada): correr y esperar un flujo,
 * el buzón, probar, diagnosticar, documentos pesados, corridas que se siguen hasta terminar.
 *
 * Las registra el ejecutor común de Vera (`puente/vera_herramientas.py`) desde el proceso que las corre, que
 * puede ser el del chat escrito o el de la voz. El chat las muestra en una línea compacta (evento `vera_tarea`
 * por el canal de eventos) y Vera las consulta con `estado_tareas` desde cualquier proceso de la conversación,
 * también para no lanzar dos veces lo mismo. Viven en memoria: tras reiniciar el servidor no queda ninguna.
 *
 *   PUT /api/chats/:id/tareas   (solo desde la máquina: el puente)  {id, n, nombre, etiqueta, estado, origen, desde, hasta, pid, ...}
 *   GET /api/chats/:id/tareas[?resultado=1]                       → {tareas, en_curso}
 */
const tarea = z.object({
  id: z.string().min(1).max(40),
  n: z.number().int().min(0).max(100000),
  nombre: z.string().max(80),
  etiqueta: z.string().max(160).default(""),
  estado: z.enum(["en_curso", "lista", "fallo"]),
  origen: z.string().max(20).default("chat"),
  desde: z.number(),
  hasta: z.number().nullish(),
  pid: z.number().int().nullish(),
  firma: z.string().max(400).optional(),
  argumentos: z.unknown().optional(),
  resultado: z.string().max(6000).optional(),
});
export type TareaVera = z.infer<typeof tarea>;

const porChat = new Map<string, Map<string, TareaVera>>();
const MAX_POR_CHAT = 60;

function publica(t: TareaVera): Omit<TareaVera, "resultado" | "firma"> {
  const { resultado: _r, firma: _f, ...resto } = t;
  return resto;
}

export function listar(chat: string, conResultado = false): TareaVera[] {
  const lista = [...(porChat.get(chat)?.values() ?? [])].sort((a, b) => a.desde - b.desde);
  return conResultado ? lista : lista.map((t) => ({ ...publica(t), ...(t.firma ? { firma: t.firma } : {}) }) as TareaVera);
}

/** El proceso que corría estas tareas terminó: lo que quedó «en curso» ya no va a terminar. */
export function procesoTerminado(chat: string, pid: number | undefined, emitir: (e: Evento) => void): void {
  if (!pid) return;
  for (const t of porChat.get(chat)?.values() ?? []) {
    if (t.estado === "en_curso" && t.pid === pid) {
      t.estado = "fallo";
      t.hasta = Date.now();
      t.resultado = JSON.stringify({ error: "el proceso que la corría terminó antes de que acabara", situacion: "fallo" });
      emitir({ tipo: "vera_tarea", corrida: null, t: Date.now() / 1000, conversacion: chat, tarea: publica(t) });
    }
  }
}

export function registrar(app: FastifyInstance, emitir: (e: Evento) => void): void {
  app.put<{ Params: { id: string } }>("/api/chats/:id/tareas", async (req, res) => {
    if (!["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(req.ip)) return res.code(403).send({ error: "solo el puente de Vera registra tareas" });
    if (!/^[a-z0-9-]{8,40}$/.test(req.params.id)) return res.code(400).send({ error: "conversación inválida" });
    const r = tarea.safeParse(req.body);
    if (!r.success) return res.code(400).send({ error: "tarea inválida" });
    let m = porChat.get(req.params.id);
    if (!m) {
      m = new Map();
      porChat.set(req.params.id, m);
      if (porChat.size > 300) porChat.delete(porChat.keys().next().value as string);
    }
    m.set(r.data.id, r.data);
    // sin crecer sin fin: se van primero las terminadas más viejas
    while (m.size > MAX_POR_CHAT) {
      const vieja = [...m.values()].filter((t) => t.estado !== "en_curso").sort((a, b) => a.desde - b.desde)[0] ?? [...m.values()][0]!;
      m.delete(vieja.id);
    }
    emitir({ tipo: "vera_tarea", corrida: null, t: Date.now() / 1000, conversacion: req.params.id, tarea: publica(r.data) });
    return { ok: true };
  });

  app.get<{ Params: { id: string }; Querystring: { resultado?: string } }>("/api/chats/:id/tareas", async (req) => {
    const tareas = listar(req.params.id, req.query.resultado === "1");
    return { tareas, en_curso: tareas.filter((t) => t.estado === "en_curso").length };
  });
}
