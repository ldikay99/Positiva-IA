import { timingSafeEqual } from "node:crypto";
import type { FastifyInstance } from "fastify";
import type { Corrida, Evento } from "./motor.ts";
import type { Flujo } from "./flujos.ts";
import { multipart } from "./espacio.ts";

/**
 * Webhooks: POST /api/ganchos/:flujo/:token corre un flujo cuyo disparador es
 * `webhook` con la entrada {cuerpo, consulta, encabezados, metodo}. Los encabezados
 * de autenticación nunca entran al flujo. Responde {corrida} (202) o, si el
 * disparador tiene responder: true, espera hasta esperar_ms lo que arme
 * respuesta_webhook. Un token que no coincide responde lo mismo que un flujo que no
 * existe (404), para no revelar nada.
 */

export interface Dependencias {
  obtener: (id: string) => Flujo | undefined;
  /** Encola la corrida; null si el flujo tiene demasiadas en espera. */
  disparar: (flujo: string, entrada: unknown) => Corrida | null;
  /** Se suscribe a los eventos de todas las corridas; devuelve cómo desuscribirse. */
  escuchar: (f: (e: Evento) => void) => () => void;
  /** multipart: guarda los archivos en el espacio del flujo (video y audio rechazados). */
  guardarArchivos?: (flujo: string, partes: { nombre: string; tipo: string; datos: Buffer }[]) => Promise<{ archivos: { id: string; nombre: string }[]; rechazados: { nombre: string; error: string }[] }>;
}

const QUITAR = /^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|apikey|x-auth-token|x-amz-security-token|x-goog-api-key|x-forwarded-.*|x-real-ip|forwarded|host|connection|content-length)$/i;

function igual(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

export async function registrar(app: FastifyInstance, deps: Dependencias): Promise<void> {
  await app.register(async (sub) => {
    // Aquí llega cualquier tipo de contenido: JSON se interpreta, lo demás queda como texto.
    sub.removeAllContentTypeParsers();
    // multipart/form-data: campos y archivos (los archivos se guardan como los de «Correr»)
    sub.addContentTypeParser("multipart/form-data", { parseAs: "buffer", bodyLimit: 130 * 1024 * 1024 }, (_req, cuerpo, listo) => listo(null, cuerpo));
    sub.addContentTypeParser("*",{ parseAs: "string", bodyLimit: 1024 * 1024 }, (req, cuerpo, listo) => {
      const texto = String(cuerpo);
      if (/json/i.test(String(req.headers["content-type"] ?? "")) && texto.trim()) {
        try {
          return listo(null, JSON.parse(texto));
        } catch {
          return listo(null, texto);
        }
      }
      listo(null, texto);
    });

    sub.post<{ Params: { flujo: string; token: string } }>("/api/ganchos/:flujo/:token", async (req, res) => {
      const f = deps.obtener(req.params.flujo);
      const disp = f?.definicion?.nodos.find((n) => n.tipo === "webhook");
      const c = (disp?.config ?? {}) as { secreto?: string; responder?: boolean; esperar_ms?: number };
      if (!f || !disp || typeof c.secreto !== "string" || !igual(c.secreto, req.params.token)) return res.code(404).send({ error: "no existe" });
      const encabezados = Object.fromEntries(Object.entries(req.headers).filter(([k, v]) => !QUITAR.test(k) && typeof v === "string").slice(0, 50).map(([k, v]) => [k, String(v).slice(0, 2000)]));
      let cuerpo: unknown = req.body ?? null;
      let archivos: string[] = [];
      const tipoCuerpo = String(req.headers["content-type"] ?? "");
      if (Buffer.isBuffer(req.body) && /multipart\/form-data/i.test(tipoCuerpo)) {
        let partes: ReturnType<typeof multipart>;
        try {
          partes = multipart(req.body, tipoCuerpo);
        } catch {
          return res.code(400).send({ error: "multipart inválido" });
        }
        const campos: Record<string, string> = {};
        for (const p of partes) if (p.archivo === undefined && p.campo) campos[p.campo] = p.datos.toString("utf8").slice(0, 20000);
        const subir = partes.filter((p) => p.archivo !== undefined).map((p) => ({ nombre: p.archivo || "archivo", tipo: p.tipo, datos: p.datos }));
        if (subir.length) {
          if (!deps.guardarArchivos) return res.code(415).send({ error: "este servidor no acepta archivos por webhook" });
          const r = await deps.guardarArchivos(f.id, subir);
          if (r.rechazados.length) return res.code(400).send({ error: r.rechazados.map((x) => x.error).join(" · "), rechazados: r.rechazados });
          archivos = r.archivos.map((a) => a.id);
        }
        cuerpo = campos;
      }
      const entrada = { cuerpo, consulta: req.query ?? {}, encabezados, metodo: "POST", recibido: new Date().toISOString(), ...(archivos.length ? { archivos } : {}) };
      // Se escucha antes de encolar: la respuesta puede llegar muy rápido.
      const espera = c.responder === true ? Math.max(0, Math.min(30_000, c.esperar_ms ?? 10_000)) : 0;
      let id = "";
      let resolver: (r: Evento | null) => void = () => undefined;
      const respuesta = new Promise<Evento | null>((ok) => (resolver = ok));
      const soltar = espera ? deps.escuchar((e) => {
        if (e.corrida !== id || !id) return;
        if (e.tipo === "respuesta_webhook") resolver(e);
        else if (e.tipo === "resultado") resolver((e.respuesta_webhook as Evento | null) ?? null);
      }) : () => undefined;
      const corrida = deps.disparar(f.id, entrada);
      if (!corrida) {
        soltar();
        return res.code(429).send({ error: "hay demasiadas corridas en espera para este flujo" });
      }
      id = corrida.id;
      if (!espera) return res.code(202).send({ corrida: corrida.id });
      const reloj = setTimeout(() => resolver(null), espera);
      const r = await respuesta;
      clearTimeout(reloj);
      soltar();
      if (!r) return res.code(202).send({ corrida: corrida.id, esperando: true });
      const estado = Number(r.estado ?? 200);
      const tipo = String(r.tipo_contenido ?? "application/json");
      res.code(estado >= 100 && estado <= 599 ? estado : 200).header("X-Corrida", corrida.id);
      if (tipo === "text/plain") return res.type("text/plain; charset=utf-8").send(typeof r.cuerpo === "string" ? r.cuerpo : JSON.stringify(r.cuerpo ?? ""));
      return res.type("application/json; charset=utf-8").send(JSON.stringify(r.cuerpo ?? { corrida: corrida.id }));
    });
  });
}
