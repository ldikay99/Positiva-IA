import { spawn } from "node:child_process";
import { createReadStream } from "node:fs";
import { resolve } from "node:path";
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { config } from "./config.ts";
import * as bandeja from "./bandeja.ts";
import * as descargas from "./descargas.ts";
import * as espacio from "./espacio.ts";
import * as flujos from "./flujos.ts";
import type { Corrida } from "./motor.ts";

/**
 * «Probar este paso»: corre SOLO un paso de un flujo declarativo (o el flujo hasta ese paso) en simulación,
 * con un archivo, un correo de su correspondencia, un archivo del flujo o los datos de una ejecución anterior.
 * Nunca escribe: ninguna credencial escribe en la prueba (aunque una persona lo haya habilitado), lo que
 * escribiría en la plataforma se retiene y no se llama a otros flujos. Lo que produce un paso se puede fijar
 * como datos de prueba (en el espacio del flujo, no en su definición) para probar los siguientes.
 *
 *   POST   /api/flujos/:id/probar-paso {nodo, hasta?, entrada?, archivos?, caso?, desde_corrida?, fijados?, definicion?}
 *          → {ok, salida, variables, todas, muestra, archivos, escrituras, mensajes, avisos, eventos, ms, resultado?, error?}
 *   GET    /api/flujos/:id/fijados              → {fijados: [{nodo, variables, salida, fijado}]}
 *   GET    /api/flujos/:id/fijados/:nodo
 *   PUT    /api/flujos/:id/fijados/:nodo {variables, salida?, nota?, origen?}
 *   DELETE /api/flujos/:id/fijados/:nodo
 *   GET    /api/descargas/:flujo/:archivo?vence=&firma=   (enlaces firmados que vencen)
 */

export interface Dependencias {
  /** Una ejecución por su id (de cualquier flujo; aquí se revisa que sea del mismo). */
  buscar: (id: string) => Corrida | null;
}

const MAX_PRUEBA_MS = Number(process.env.PROBAR_PASO_MAX_S ?? 300) * 1000;

const pedido = z.object({
  nodo: z.string().regex(espacio.ID_NODO, "nodo: el id de un paso del flujo"),
  hasta: z.boolean().optional(),
  entrada: z.record(z.string(), z.unknown()).optional(),
  archivos: z.array(z.string().regex(/^a-[0-9a-z]{6,40}$/, "archivos: ids de archivos del flujo")).max(50).optional(),
  caso: z.string().regex(/^[\w.-]{1,200}$/).optional(),
  desde_corrida: z.string().regex(/^[\w.-]{1,80}$/).optional(),
  fijados: z.union([z.boolean(), z.array(z.string().regex(espacio.ID_NODO))]).optional(),
  definicion: z.record(z.string(), z.unknown()).optional(),
});

function correrPrueba(orden: Record<string, unknown>): Promise<Record<string, unknown>> {
  return new Promise((resolver) => {
    const p = spawn(config.python, [resolve(config.raiz, "puente", "flujo_declarativo.py"), "probar"], { env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let salida = "";
    let errores = "";
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.stderr.setEncoding("utf8").on("data", (t: string) => (errores = (errores + t).slice(-2000)));
    const reloj = setTimeout(() => p.kill("SIGKILL"), MAX_PRUEBA_MS);
    p.on("exit", (codigo) => {
      clearTimeout(reloj);
      const linea = salida.trim().split("\n").pop() ?? "";
      try {
        resolver(JSON.parse(linea) as Record<string, unknown>);
      } catch {
        resolver({ ok: false, error: codigo === null ? `la prueba pasó de ${MAX_PRUEBA_MS / 1000} s y se detuvo` : "la prueba no terminó bien", detalle_error: errores.slice(-400) });
      }
    });
    p.stdin.on("error", () => undefined);
    p.stdin.end(JSON.stringify(orden));
  });
}

export async function registrar(app: FastifyInstance, deps: Dependencias): Promise<void> {
  app.post<{ Params: { id: string } }>("/api/flujos/:id/probar-paso", { bodyLimit: 8 * 1024 * 1024 }, async (req, res) => {
    const f = flujos.obtener(req.params.id);
    if (!f) return res.code(404).send({ error: "ese flujo no existe" });
    if (f.tipo !== "declarativo" || !f.definicion) return res.code(400).send({ error: "solo los flujos declarativos se prueban paso a paso" });
    const r = pedido.safeParse(req.body ?? {});
    if (!r.success) return res.code(400).send({ error: `pedido inválido: ${r.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ")}` });
    const b = r.data;
    // la definición que se está editando (sin guardar) o la guardada; siempre con el id de ESTE flujo
    let d = f.definicion;
    if (b.definicion) {
      const v = flujos.definicion.safeParse({ ...b.definicion, id: f.id });
      if (!v.success) return res.code(400).send({ error: "el flujo que estás editando no es válido", problemas: v.error.issues.map((i) => `${i.path.join(".") || "flujo"}: ${i.message}`) });
      d = v.data;
    }
    if (!d.nodos.some((n) => n.id === b.nodo)) return res.code(400).send({ error: `el flujo no tiene el paso «${b.nodo}»` });
    let caso = b.caso;
    let entrada = b.entrada;
    let ids = b.archivos ?? [];
    let variables: Record<string, unknown> = {};
    if (b.desde_corrida) {
      const c = deps.buscar(b.desde_corrida);
      if (!c || c.flujo !== f.id) return res.code(404).send({ error: "esa ejecución no es de este flujo" });
      variables = ((c.resultado?.variables ?? {}) as Record<string, unknown>) || {};
      caso ??= bandeja.casoValido(c.caso) ? c.caso : undefined;
      entrada ??= (c.entrada as Record<string, unknown> | undefined) ?? undefined;
      if (!ids.length) ids = espacio.resolverArchivos(f.id, c.entrada, d).map((a) => a.id);
    }
    const disp = flujos.disparadorDe(d);
    const deCorreo = disp?.tipo === "entrada_correo" || disp?.tipo === "correo_nuevo";
    if (caso && (!deCorreo || !(await bandeja.esDe({ id: f.id, tipo: f.tipo }, caso)))) {
      // solo un correo de SU correspondencia: nunca el universo de PQRD ni lo de otro flujo
      if (b.caso) return res.code(400).send({ error: deCorreo ? `el correo «${caso}» no es de la correspondencia de este flujo` : "este flujo no arranca con un correo" });
      caso = undefined;
    }
    const faltan = ids.filter((x) => !espacio.archivoDe(f.id, x));
    if (faltan.length) return res.code(400).send({ error: `estos archivos no están en el flujo: ${faltan.join(", ")}` });
    const archivos = ids.map((x) => espacio.archivoDe(f.id, x)!).map(({ id, nombre, tipo, tamano, sha256, ruta }) => ({ id, nombre, tipo, tamano, sha256, ruta }));
    // los datos fijados de los pasos (todos o los pedidos); el del propio paso no, que es lo que se prueba
    const fijados: Record<string, unknown> = {};
    if (b.fijados) {
      for (const x of await espacio.listarFijados(f.id)) {
        if (x.nodo === b.nodo || (Array.isArray(b.fijados) && !b.fijados.includes(x.nodo))) continue;
        fijados[x.nodo] = { variables: x.variables, ...(x.salida ? { salida: x.salida } : {}) };
      }
    }
    const t0 = Date.now();
    const orden = {
      ...flujos.ordenDePrueba(f.id, d), nodo: b.nodo, hasta: b.hasta === true, entrada: { ...(entrada ?? {}), ...(ids.length ? { archivos: ids } : {}) },
      archivos, ...(caso ? { caso } : {}), variables, fijados,
    };
    const resultado = await correrPrueba(orden);
    return { ...resultado, ms_total: Date.now() - t0, prueba: { nodo: b.nodo, hasta: b.hasta === true, caso: caso ?? null, archivos: ids, desde_corrida: b.desde_corrida ?? null, fijados: Object.keys(fijados) } };
  });

  app.get<{ Params: { id: string } }>("/api/flujos/:id/fijados", async (req, res) => {
    if (!flujos.obtener(req.params.id)) return res.code(404).send({ error: "ese flujo no existe" });
    return { fijados: await espacio.listarFijados(req.params.id) };
  });

  app.get<{ Params: { id: string; nodo: string } }>("/api/flujos/:id/fijados/:nodo", async (req, res) => {
    if (!flujos.obtener(req.params.id)) return res.code(404).send({ error: "ese flujo no existe" });
    return (await espacio.leerFijado(req.params.id, req.params.nodo)) ?? res.code(404).send({ error: "ese paso no tiene datos fijados" });
  });

  app.put<{ Params: { id: string; nodo: string } }>("/api/flujos/:id/fijados/:nodo", { bodyLimit: 3 * 1024 * 1024 }, async (req, res) => {
    const f = flujos.obtener(req.params.id);
    if (!f || f.tipo !== "declarativo") return res.code(404).send({ error: "ese flujo no existe o no es declarativo" });
    if (f.definicion && !f.definicion.nodos.some((n) => n.id === req.params.nodo)) return res.code(400).send({ error: `el flujo no tiene el paso «${req.params.nodo}»` });
    const r = await espacio.ponerFijado(f.id, req.params.nodo, (req.body ?? {}) as Record<string, unknown>);
    return r.ok ? r.fijado : res.code(400).send({ error: r.error });
  });

  app.delete<{ Params: { id: string; nodo: string } }>("/api/flujos/:id/fijados/:nodo", async (req, res) =>
    (await espacio.borrarFijado(req.params.id, req.params.nodo)) ? { ok: true } : res.code(404).send({ error: "ese paso no tiene datos fijados" }));

  // Enlaces firmados que vencen: quien lo tiene baja ESE archivo (para Teams, Slack, webhooks y la respuesta de un webhook).
  app.get<{ Params: { flujo: string; archivo: string }; Querystring: { vence?: string; firma?: string; ver?: string } }>("/api/descargas/:flujo/:archivo", async (req, res) => {
    const problema = descargas.problema(req.params.flujo, req.params.archivo, req.query.vence, req.query.firma);
    if (problema) return res.code(problema.includes("venció") ? 410 : 404).send({ error: problema });
    const a = espacio.archivoDe(req.params.flujo, req.params.archivo);
    if (!a) return res.code(404).send({ error: "ese archivo ya no está" });
    return res.header("Content-Type", a.tipo || "application/octet-stream")
      .header("Content-Disposition", `${req.query.ver ? "inline" : "attachment"}; filename*=UTF-8''${encodeURIComponent(a.nombre)}`)
      .header("Cache-Control", "private, no-store").header("X-Content-Type-Options", "nosniff").send(createReadStream(a.ruta));
  });
}
