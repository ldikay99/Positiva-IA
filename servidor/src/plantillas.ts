import type { FastifyInstance } from "fastify";
import { readdir, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { config } from "./config.ts";
import * as flujos from "./flujos.ts";

/**
 * Plantillas de flujos listas para usar: cada una es un JSON en `catalogo/plantillas/`
 * con su definición (la del motor declarativo), qué hace, qué necesita y cómo probarla.
 * Instalar una la copia como flujo propio (con su id y nombre), que se ajusta como
 * cualquier otro. Una plantilla que usa un tipo de paso que este motor todavía no tiene
 * se lista como «no disponible» y no se instala.
 *
 *   GET  /api/plantillas             → [Plantilla sin definición]
 *   GET  /api/plantillas/:id         → Plantilla con definición
 *   POST /api/plantillas/:id/instalar {nombre?, id_nuevo?} → {flujo}
 */

export interface Necesidad {
  /** nada: corre tal cual; correos, archivos o datos: con qué se prueba; conexion o entorno: lo que hay que configurar. */
  tipo: "nada" | "correos" | "archivos" | "datos" | "conexion" | "entorno";
  texto: string;
}

interface PlantillaGuardada {
  id: string;
  nombre: string;
  resumen: string;
  descripcion: string;
  categoria: string;
  orden?: number;
  necesita: Necesidad[];
  como_probar?: string;
  definicion: { nombre?: string; descripcion?: string; nodos: { id: string; tipo: string; nombre: string; config?: Record<string, unknown> }[]; aristas: { desde: string; hacia: string; cuando?: string }[]; [k: string]: unknown };
}

export interface Dependencias {
  /** Avisa a la web (y a Vera) que hay un flujo nuevo. */
  avisarFlujo: (id: string, extra?: Record<string, unknown>) => void;
}

const carpeta = () => resolve(config.raiz, "catalogo", "plantillas");
const ID = /^[a-z][a-z0-9-]{1,40}$/;

let cache: { t: number; lista: PlantillaGuardada[] } | null = null;

async function cargar(): Promise<PlantillaGuardada[]> {
  // Se releen cada poco: agregar una plantilla no exige reiniciar.
  if (cache && Date.now() - cache.t < 10_000) return cache.lista;
  const nombres = (await readdir(carpeta()).catch(() => [] as string[])).filter((n) => n.endsWith(".json")).sort();
  const lista: PlantillaGuardada[] = [];
  for (const n of nombres) {
    try {
      const p = JSON.parse(await readFile(join(carpeta(), n), "utf8")) as PlantillaGuardada;
      if (ID.test(p.id) && Array.isArray(p.definicion?.nodos)) lista.push(p);
    } catch {
      /* archivo dañado: se ignora */
    }
  }
  lista.sort((a, b) => (a.orden ?? 99) - (b.orden ?? 99) || a.nombre.localeCompare(b.nombre));
  cache = { t: Date.now(), lista };
  return lista;
}

const TIPOS = new Set<string>(flujos.TIPOS_DE_NODO);
const IA = new Set(["ia_clasificar", "ia_decidir", "ia_extraer", "ia_generar", "resumir", "ia_redactar"]);

/** Lo que la galería muestra de una plantilla (sin la definición completa). */
function ficha(p: PlantillaGuardada) {
  const faltan = [...new Set(p.definicion.nodos.map((n) => n.tipo).filter((t) => !TIPOS.has(t)))];
  const disp = p.definicion.nodos.find((n) => flujos.DISPARADORES.has(n.tipo));
  return {
    id: p.id,
    nombre: p.nombre,
    resumen: p.resumen,
    descripcion: p.descripcion,
    categoria: p.categoria,
    necesita: p.necesita ?? [],
    como_probar: p.como_probar ?? "",
    disparador: disp?.tipo ?? null,
    usa_ia: p.definicion.nodos.some((n) => IA.has(n.tipo)),
    nodos: p.definicion.nodos.map((n) => ({ id: n.id, tipo: n.tipo, nombre: n.nombre })),
    aristas: p.definicion.aristas.map((a) => ({ desde: a.desde, hacia: a.hacia, ...(a.cuando ? { cuando: a.cuando } : {}) })),
    disponible: faltan.length === 0,
    ...(faltan.length ? { motivo: `usa pasos que esta versión todavía no tiene (${faltan.join(", ")})` } : {}),
  };
}

export async function listar() {
  return (await cargar()).map(ficha);
}

export async function obtener(id: string) {
  const p = (await cargar()).find((x) => x.id === id);
  return p ? { ...ficha(p), definicion: p.definicion } : null;
}

/** Crea un flujo propio a partir de la plantilla y lo deja listo para abrir. */
export async function instalar(id: string, cuerpo: unknown): Promise<{ ok: true; flujo: flujos.Flujo } | { ok: false; codigo: number; error: string; problemas?: string[] }> {
  const p = (await cargar()).find((x) => x.id === id);
  if (!p) return { ok: false, codigo: 404, error: "esa plantilla no existe" };
  const f = ficha(p);
  if (!f.disponible) return { ok: false, codigo: 409, error: `la plantilla «${p.nombre}» ${f.motivo}` };
  const b = (cuerpo ?? {}) as { nombre?: unknown; id_nuevo?: unknown };
  const nombre = typeof b.nombre === "string" && b.nombre.trim().length >= 2 ? b.nombre.trim().slice(0, 80) : p.definicion.nombre ?? p.nombre;
  const nuevo = typeof b.id_nuevo === "string" && b.id_nuevo ? b.id_nuevo : flujos.idLibre(nombre);
  if (!ID.test(nuevo)) return { ok: false, codigo: 400, error: "id_nuevo: minúsculas con guiones (de 2 a 41 caracteres, empieza por letra)" };
  if (flujos.obtener(nuevo)) return { ok: false, codigo: 409, error: `ya existe un flujo «${nuevo}»` };
  const def = structuredClone(p.definicion) as Record<string, unknown>;
  def.id = nuevo;
  def.nombre = nombre;
  def.descripcion ??= p.resumen;
  const r = await flujos.guardar(def, `desde la plantilla «${p.nombre}»`);
  if (!r.ok || !r.flujo) return { ok: false, codigo: 400, error: "la plantilla no pasó la validación del motor", problemas: r.problemas };
  return { ok: true, flujo: r.flujo };
}

export async function registrar(app: FastifyInstance, deps: Dependencias): Promise<void> {
  app.get("/api/plantillas", async () => listar());

  app.get<{ Params: { id: string } }>("/api/plantillas/:id", async (req, res) => (await obtener(req.params.id)) ?? res.code(404).send({ error: "esa plantilla no existe" }));

  app.post<{ Params: { id: string } }>("/api/plantillas/:id/instalar", async (req, res) => {
    const r = await instalar(req.params.id, req.body);
    if (!r.ok) return res.code(r.codigo).send({ error: r.error, ...(r.problemas ? { problemas: r.problemas } : {}) });
    deps.avisarFlujo(r.flujo.id, { version: r.flujo.version ?? 1, plantilla: req.params.id });
    return { flujo: { ...r.flujo, trabajador: undefined }, plantilla: req.params.id };
  });
}
