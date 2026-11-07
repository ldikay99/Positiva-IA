import type { FastifyInstance } from "fastify";
import * as credenciales from "./credenciales.ts";
import { enmascarar, variantes } from "./enmascarar.ts";
import * as ia from "./ia.ts";

/**
 * Las rutas de la IA de la plataforma (proveedores, modelos, prueba de conexión,
 * IA global y la de Vera). Las llaves nunca salen: el Python las recibe por stdin
 * solo para la llamada y todo lo que vuelve pasa por la máscara.
 *
 *   GET  /api/ia/proveedores?flujo=            proveedores, si están configurados y la IA global/Vera
 *   GET  /api/ia/modelos?proveedor=&credencial=&flujo=&fresco=1   catálogo + lista en vivo (1 h en caché)
 *   POST /api/ia/probar {proveedor, modelo, credencial?, flujo?}   una llamada mínima de verdad
 *   GET  /api/ia/global · PUT /api/ia/global {proveedor, modelo, credencial?, temperatura?}
 *   GET  /api/ia/vera   · PUT /api/ia/vera {texto?: {proveedor, modelo, credencial?}, voz?: {proveedor, modelo, credencial?, voz?, ubicacion?}}
 */

type Valores = { valores: Record<string, unknown>; secretos: string[] } | { error: string; codigo: number };

/** Los valores de una credencial para una llamada: existe, es del tipo del proveedor y el flujo (o lo global) la puede usar. */
function valoresDe(prov: string, nombre: unknown, flujo: unknown): Valores {
  const tipo = ia.CREDENCIAL_DE[prov as ia.ProveedorIa];
  if (tipo === undefined) return { error: `proveedor de IA desconocido «${String(prov)}»`, codigo: 400 };
  if (!nombre) return tipo ? { error: `${prov} necesita una credencial de tipo ${tipo}`, codigo: 400 } : { valores: {}, secretos: [] };
  if (typeof nombre !== "string") return { error: "credencial: el nombre de una credencial guardada", codigo: 400 };
  const alcance = typeof flujo === "string" && flujo ? flujo : "";
  const c = credenciales.paraCorrida(alcance, [nombre])[nombre];
  if (!c) return { error: `la credencial «${nombre}» no existe o ${alcance ? "este flujo" : "la plataforma"} no la puede usar`, codigo: 404 };
  if (c.tipo !== tipo) return { error: `la credencial «${nombre}» es de tipo ${c.tipo}; ${prov} necesita una ${tipo}`, codigo: 400 };
  const secretos = c.secretos.map((k) => c.valores[k]).filter((x): x is string => typeof x === "string");
  return { valores: c.valores, secretos };
}

export async function registrar(app: FastifyInstance): Promise<void> {
  app.get<{ Querystring: { flujo?: string } }>("/api/ia/proveedores", async (req) => {
    const cat = await ia.catalogo();
    const usables = credenciales.listar(req.query.flujo || undefined);
    const g = ia.obtenerGlobal();
    return {
      precios_fecha: cat.precios_fecha, precios_aproximados: true, capacidades: cat.capacidades,
      global: g, vera: ia.obtenerVera(), por_defecto: ia.GLOBAL_DEFECTO,
      proveedores: cat.proveedores.map(({ modelos, ...p }) => {
        const creds = p.tipo_credencial ? usables.filter((c) => c.tipo === p.tipo_credencial).map((c) => ({ nombre: c.nombre, alcance: c.alcance })) : [];
        return { ...p, modelos_en_catalogo: modelos.length, credenciales: creds, configurado: !p.requiere_credencial || creds.length > 0, es_global: g.proveedor === p.id };
      }),
    };
  });

  app.get<{ Querystring: { proveedor?: string; credencial?: string; flujo?: string; fresco?: string } }>("/api/ia/modelos", async (req, res) => {
    const prov = req.query.proveedor ?? "";
    const cat = await ia.catalogo();
    const p = cat.proveedores.find((x) => x.id === prov);
    if (!p) return res.code(400).send({ error: `proveedor de IA desconocido «${prov}» (válidos: ${ia.PROVEEDORES_IA.join(", ")})` });
    const base = p.modelos.length ? p.modelos : prov === "azure_openai" ? (cat.proveedores.find((x) => x.id === "openai")?.modelos ?? []) : [];
    if (!req.query.credencial || !p.lista_en_vivo) return { proveedor: prov, modelos: base, en_vivo: false, precios_fecha: cat.precios_fecha };
    const v = valoresDe(prov, req.query.credencial, req.query.flujo);
    if ("error" in v) return res.code(v.codigo).send({ error: v.error });
    try {
      const vivos = await ia.modelosEnVivo(prov, req.query.credencial, v.valores, req.query.fresco === "1");
      const ids = new Set(vivos.map((m) => m.id));
      return { proveedor: prov, modelos: [...vivos, ...base.filter((m) => !ids.has(m.id)).map((m) => ({ ...m, en_vivo: false }))], en_vivo: true, precios_fecha: cat.precios_fecha };
    } catch (e) {
      return { proveedor: prov, modelos: base, en_vivo: false, error_en_vivo: enmascarar(e instanceof Error ? e.message : String(e), variantes(v.secretos)), precios_fecha: cat.precios_fecha };
    }
  });

  app.post("/api/ia/probar", async (req, res) => {
    const b = (req.body ?? {}) as { proveedor?: string; modelo?: string; credencial?: string; flujo?: string };
    if (!b.proveedor || !b.modelo) return res.code(400).send({ error: "manda {proveedor, modelo, credencial?}" });
    const v = valoresDe(b.proveedor, b.credencial, b.flujo);
    if ("error" in v) return res.code(v.codigo).send({ ok: false, error: v.error });
    const r = await ia.pedirAlPython<Record<string, unknown>>("probar", { proveedor: b.proveedor, modelo: b.modelo, credencial: v.valores }, 90000)
      .catch((e: unknown) => ({ ok: false, error: e instanceof Error ? e.message : String(e) }));
    return enmascarar(r, variantes(v.secretos));
  });

  app.get("/api/ia/global", async () => ({ ...ia.obtenerGlobal(), por_defecto: ia.GLOBAL_DEFECTO }));

  app.put("/api/ia/global", async (req, res) => {
    const r = await ia.ponerGlobal(req.body);
    return r.ok ? r.ia : res.code(400).send({ error: r.error });
  });

  app.get("/api/ia/vera", async () => ({ ...ia.obtenerVera(), ...(await ia.vozDisponible()), por_defecto: ia.VERA_DEFECTO }));

  app.put("/api/ia/vera", async (req, res) => {
    const r = await ia.ponerVera(req.body);
    if (!r.ok) return res.code(400).send({ error: r.error });
    return { ...r.vera, ...(await ia.vozDisponible()) };
  });
}
