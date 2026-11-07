import type { FastifyInstance } from "fastify";
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { config } from "./config.ts";
import * as credenciales from "./credenciales.ts";

/**
 * Conectores genéricos: analizar una especificación OpenAPI (sus operaciones como pasos) y
 * probar las conexiones que no son OAuth. Lo hace el motor de Python (`puente/nodos_integraciones.py`),
 * que es quien habla con las APIs con las reglas de destinos seguros; aquí solo se le pasa la orden.
 *
 *   POST /api/conectores/openapi/analizar {credencial?} | {spec_url?, spec?, base_url?}
 *     → {ok, titulo, version, base_url, operaciones: [{id, metodo, ruta, resumen, parametros, cuerpo, escritura}]} | {ok: false, error}
 */

const CLI = () => resolve(config.raiz, "puente", "nodos_integraciones.py");

/** Corre `nodos_integraciones.py <cmd>` con la orden por stdin y devuelve la ÚLTIMA línea de stdout como JSON. */
export function correrPython(cmd: string, orden: unknown, limiteMs = 60_000): Promise<Record<string, unknown>> {
  return new Promise((resolver) => {
    let salida = "";
    let terminado = false;
    const p = spawn(config.python, [CLI(), cmd], { stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    const reloj = setTimeout(() => {
      p.kill();
      fin({ ok: false, error: `no respondió en ${Math.round(limiteMs / 1000)} s` });
    }, limiteMs);
    function fin(r: Record<string, unknown>) {
      if (terminado) return;
      terminado = true;
      clearTimeout(reloj);
      resolver(r);
    }
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.stderr.resume();  // lo que diga por stderr no se muestra: podría llevar valores
    p.on("error", () => fin({ ok: false, error: "no se pudo arrancar el motor de Python" }));
    p.on("exit", () => {
      const ultima = salida.trim().split("\n").pop() ?? "";
      try {
        const r = JSON.parse(ultima) as unknown;
        fin(r && typeof r === "object" ? (r as Record<string, unknown>) : { ok: false, error: "respuesta inválida del motor" });
      } catch {
        fin({ ok: false, error: "el motor no devolvió un resultado" });
      }
    });
    p.stdin.end(JSON.stringify(orden));
  });
}

export async function registrar(app: FastifyInstance): Promise<void> {
  app.post("/api/conectores/openapi/analizar", async (req, res) => {
    const b = (req.body ?? {}) as { credencial?: unknown; spec_url?: unknown; spec?: unknown; base_url?: unknown };
    let orden: Record<string, unknown>;
    if (typeof b.credencial === "string" && b.credencial) {
      const c = credenciales.valoresDe(b.credencial);
      if (!c) return res.code(404).send({ ok: false, error: "esa conexión no existe" });
      if (c.tipo !== "api_openapi") return res.code(400).send({ ok: false, error: `la conexión «${b.credencial}» es ${c.tipo}; aquí sirve una api_openapi` });
      orden = { spec_url: c.valores.spec_url, spec: c.valores.spec, base_url: c.valores.base_url, red_privada: c.valores.red_privada === true, creada_por_persona: c.creada_por_persona };
    } else {
      if (!b.spec_url && !b.spec) return res.code(400).send({ ok: false, error: "manda {credencial} o {spec_url} o {spec}" });
      if (typeof b.spec === "string" && b.spec.length > 2_000_000) return res.code(400).send({ ok: false, error: "la especificación pasa de 2 MB" });
      orden = { spec_url: b.spec_url, spec: b.spec, base_url: b.base_url, red_privada: false, creada_por_persona: false };
    }
    return correrPython("analizar_openapi", orden, 60_000);
  });
}
