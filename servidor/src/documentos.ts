import type { FastifyInstance, FastifyRequest } from "fastify";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { mkdir, readdir, readFile, rm, unlink, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { config } from "./config.ts";
import * as espacio from "./espacio.ts";

/**
 * La biblioteca de plantillas de documentos (plan 5d, «Salidas a la medida»): Word, Excel, PowerPoint
 * y correos HTML con {{variables}}, logos y estilos de cada área, reutilizables entre flujos. Las llena
 * el paso documento_plantilla (puente/nodos_salidas.py) y las lee puente/salidas.py.
 *
 * Se guardan en <estado>/documentos/<id>/: meta.json, plantilla.<ext> y recursos/<nombre>.<ext> (logos).
 *
 *   GET    /api/documentos                       → {plantillas: [Meta sin texto]}
 *   GET    /api/documentos/:id                   → Meta con su texto (vista previa)
 *   GET    /api/documentos/:id/archivo[?descargar=1]
 *   POST   /api/documentos                       multipart (archivo + nombre, descripcion, area, asunto, id) o JSON
 *                                                {nombre, formato?, contenido? (HTML), texto? (Word/PowerPoint/Excel/HTML desde texto),
 *                                                 archivo? {nombre, datos base64}, descripcion?, area?, asunto?, id?}
 *   PUT    /api/documentos/:id                   JSON {nombre?, descripcion?, area?, asunto?, contenido? (HTML)} o multipart con un archivo nuevo
 *   DELETE /api/documentos/:id                   solo una persona (X-Confirmado-Por-Persona: si)
 *   POST   /api/documentos/:id/recursos          una imagen (logo, firma, sello) con su nombre: {{imagen:<nombre>}}
 *   GET    /api/documentos/:id/recursos/:nombre
 *   DELETE /api/documentos/:id/recursos/:nombre  solo una persona
 *   POST   /api/documentos/:id/probar {datos}    la plantilla llena con datos de ejemplo (descarga)
 */

export const ID = /^[a-z0-9][a-z0-9-]{1,59}$/;
const NOMBRE_RECURSO = /^[a-z0-9][a-z0-9_-]{0,39}$/;
export const FORMATOS = ["docx", "xlsx", "pptx", "html"] as const;
type Formato = (typeof FORMATOS)[number];
const MIME: Record<Formato, string> = {
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  html: "text/html; charset=utf-8",
};
const MAX_MB = 20;
const MAX_RECURSO_MB = 5;
const MAX_PLANTILLAS = 500;

export interface Recurso { nombre: string; archivo: string; tipo: string; tamano: number }
export interface MetaDocumento {
  id: string;
  nombre: string;
  descripcion: string;
  area: string;
  formato: Formato;
  archivo: string;
  nombre_original: string;
  tamano: number;
  sha256: string;
  variables: string[];
  repeticiones: Record<string, string[]>;
  imagenes: string[];
  contenido: boolean;
  prohibidas: string[];
  asunto?: string;
  diapositivas?: number;
  hojas?: string[];
  recursos: Recurso[];
  texto?: string;
  creada: number;
  actualizada: number;
  origen: string;
}

const carpeta = () => resolve(config.datos, "documentos");
const carpetaDe = (id: string) => join(carpeta(), id);
const confirmado = (req: FastifyRequest) => String(req.headers["x-confirmado-por-persona"] ?? "").toLowerCase() === "si";

/** Corre puente/salidas.py y devuelve su JSON. */
function python(args: string[], entrada?: unknown): Promise<Record<string, unknown>> {
  return new Promise((resolver) => {
    const p = spawn(config.python, [resolve(config.raiz, "puente", "salidas.py"), ...args], { env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let salida = "";
    let error = "";
    p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
    p.stderr.setEncoding("utf8").on("data", (t: string) => (error += t));
    const reloj = setTimeout(() => p.kill(), 90_000);
    p.on("error", (e) => resolver({ error: `no se pudo correr Python: ${e.message}` }));
    p.on("exit", () => {
      clearTimeout(reloj);
      try {
        resolver(JSON.parse(salida) as Record<string, unknown>);
      } catch {
        resolver({ error: (error || salida || "sin respuesta de Python").slice(-400) });
      }
    });
    if (entrada !== undefined) p.stdin.end(JSON.stringify(entrada));
    else p.stdin.end();
  });
}

/** docx, xlsx, pptx o html por el contenido (un .docx renombrado a .xlsx se reconoce) y, si no, por el nombre. */
export function formatoDe(nombre: string, datos: Buffer): Formato | null {
  const ext = nombre.toLowerCase().split(".").pop() ?? "";
  if (datos.subarray(0, 4).equals(Buffer.from([0x50, 0x4b, 0x03, 0x04]))) {
    // los nombres de las partes principales están en el directorio del ZIP (no se confunden con datos comprimidos)
    if (datos.includes("word/document.xml")) return "docx";
    if (datos.includes("xl/workbook.xml")) return "xlsx";
    if (datos.includes("ppt/presentation.xml")) return "pptx";
    return null;
  }
  if (ext === "html" || ext === "htm") return "html";
  return null;
}

export function slug(texto: string): string {
  return texto.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 50) || "plantilla";
}

async function leerMeta(id: string): Promise<MetaDocumento | null> {
  if (!ID.test(id)) return null;
  try {
    return JSON.parse(await readFile(join(carpetaDe(id), "meta.json"), "utf8")) as MetaDocumento;
  } catch {
    return null;
  }
}

export async function listar(): Promise<MetaDocumento[]> {
  const ids = await readdir(carpeta()).catch(() => [] as string[]);
  const r: MetaDocumento[] = [];
  for (const id of ids) {
    const m = await leerMeta(id);
    if (m) r.push(m);
  }
  return r.sort((a, b) => b.actualizada - a.actualizada);
}

const sinTexto = ({ texto: _t, ...m }: MetaDocumento) => m;

/** Guarda el archivo de una plantilla (nueva o reemplazo) y la analiza: sus variables, listas, imágenes y texto. */
async function guardarArchivo(id: string, formato: Formato, datos: Buffer, previa: MetaDocumento | null, campos: Partial<MetaDocumento>, nombreOriginal: string, origen: string): Promise<{ ok: true; meta: MetaDocumento } | { ok: false; error: string }> {
  if (datos.length > MAX_MB * 1024 * 1024) return { ok: false, error: `la plantilla pasa de ${MAX_MB} MB` };
  if (!datos.length) return { ok: false, error: "el archivo está vacío" };
  const dir = carpetaDe(id);
  await mkdir(dir, { recursive: true });
  const archivo = `plantilla.${formato}`;
  const temporal = join(dir, `.nueva-${Date.now()}.${formato}`);
  await writeFile(temporal, datos);
  const a = await python(["analizar", temporal, formato]);
  if (a.error) {
    await unlink(temporal).catch(() => undefined);
    if (!previa) await rm(dir, { recursive: true, force: true });
    return { ok: false, error: `no se pudo leer la plantilla como ${formato}: ${String(a.error)}` };
  }
  if (previa && previa.archivo !== archivo) await unlink(join(dir, previa.archivo)).catch(() => undefined);
  await writeFile(join(dir, archivo), datos);
  await unlink(temporal).catch(() => undefined);
  const ahora = Date.now();
  const meta: MetaDocumento = {
    id, nombre: campos.nombre ?? previa?.nombre ?? id, descripcion: campos.descripcion ?? previa?.descripcion ?? "", area: campos.area ?? previa?.area ?? "",
    formato, archivo, nombre_original: nombreOriginal, tamano: datos.length, sha256: createHash("sha256").update(datos).digest("hex"),
    variables: (a.variables as string[]) ?? [], repeticiones: (a.repeticiones as Record<string, string[]>) ?? {}, imagenes: (a.imagenes as string[]) ?? [],
    contenido: Boolean(a.contenido), prohibidas: (a.prohibidas as string[]) ?? [], texto: String(a.texto ?? ""),
    ...(a.diapositivas !== undefined ? { diapositivas: Number(a.diapositivas) } : {}), ...(a.hojas ? { hojas: a.hojas as string[] } : {}),
    ...((campos.asunto ?? previa?.asunto) ? { asunto: campos.asunto ?? previa?.asunto } : {}),
    recursos: previa?.recursos ?? [], creada: previa?.creada ?? ahora, actualizada: ahora, origen: previa?.origen ?? origen,
  };
  await writeFile(join(dir, "meta.json"), JSON.stringify(meta, null, 2));
  return { ok: true, meta };
}

const texto = (x: unknown, max = 200) => (typeof x === "string" ? x.trim().slice(0, max) : undefined);

/** Lo que trae un pedido de alta o cambio: un archivo (multipart o JSON en base64) y sus campos. */
function leerPedido(req: FastifyRequest): { campos: Record<string, string>; archivo?: { nombre: string; datos: Buffer } } {
  const tipo = String(req.headers["content-type"] ?? "");
  if (Buffer.isBuffer(req.body) && /multipart\/form-data/i.test(tipo)) {
    const partes = espacio.multipart(req.body, tipo);
    const campos: Record<string, string> = {};
    let archivo: { nombre: string; datos: Buffer } | undefined;
    for (const p of partes) {
      if (p.archivo !== undefined) archivo ??= { nombre: p.archivo || "plantilla", datos: p.datos };
      else campos[p.campo] = p.datos.toString("utf8");
    }
    return { campos, archivo };
  }
  const b = (req.body ?? {}) as Record<string, unknown>;
  const campos: Record<string, string> = {};
  for (const [k, v] of Object.entries(b)) if (typeof v === "string") campos[k] = v;
  const a = b.archivo as { nombre?: unknown; datos?: unknown } | undefined;
  return { campos, archivo: a && typeof a.datos === "string" ? { nombre: String(a.nombre ?? "plantilla"), datos: Buffer.from(a.datos, "base64") } : undefined };
}

function camposDe(c: Record<string, string>): Partial<MetaDocumento> {
  const r: Partial<MetaDocumento> = {};
  if (texto(c.nombre, 120)) r.nombre = texto(c.nombre, 120)!;
  if (c.descripcion !== undefined) r.descripcion = texto(c.descripcion, 600) ?? "";
  if (c.area !== undefined) r.area = texto(c.area, 80) ?? "";
  if (c.asunto !== undefined) r.asunto = texto(c.asunto, 300) ?? "";
  return r;
}

function tipoImagen(datos: Buffer): { ext: string; tipo: string } | null {
  if (datos.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return { ext: "png", tipo: "image/png" };
  if (datos[0] === 0xff && datos[1] === 0xd8 && datos[2] === 0xff) return { ext: "jpg", tipo: "image/jpeg" };
  if (datos.subarray(0, 4).toString("latin1") === "RIFF" && datos.subarray(8, 12).toString("latin1") === "WEBP") return { ext: "webp", tipo: "image/webp" };
  if (datos.subarray(0, 3).toString("latin1") === "GIF") return { ext: "gif", tipo: "image/gif" };
  return null;
}

export async function registrar(app: FastifyInstance): Promise<void> {
  await mkdir(carpeta(), { recursive: true }).catch(() => undefined);

  app.get<{ Querystring: { q?: string; formato?: string } }>("/api/documentos", async (req) => {
    const q = (req.query.q ?? "").toLowerCase();
    const lista = (await listar()).filter((m) => (!req.query.formato || m.formato === req.query.formato)
      && (!q || `${m.nombre} ${m.descripcion} ${m.area} ${m.id}`.toLowerCase().includes(q)));
    return { plantillas: lista.map(sinTexto) };
  });

  app.get<{ Params: { id: string } }>("/api/documentos/:id", async (req, res) => {
    const m = await leerMeta(req.params.id);
    return m ?? res.code(404).send({ error: "esa plantilla de documentos no existe" });
  });

  app.get<{ Params: { id: string }; Querystring: { descargar?: string } }>("/api/documentos/:id/archivo", async (req, res) => {
    const m = await leerMeta(req.params.id);
    const ruta = m ? join(carpetaDe(m.id), m.archivo) : "";
    if (!m || !existsSync(ruta)) return res.code(404).send({ error: "esa plantilla de documentos no existe" });
    const nombre = `${slug(m.nombre)}.${m.formato}`;
    res.header("Content-Type", MIME[m.formato]).header("Content-Disposition", `${req.query.descargar ? "attachment" : "inline"}; filename*=UTF-8''${encodeURIComponent(nombre)}`)
      .header("Cache-Control", "no-store").header("X-Content-Type-Options", "nosniff");
    // un HTML se ve aislado: sin scripts ni acceso a la plataforma aunque se abra directo
    if (m.formato === "html") res.header("Content-Security-Policy", "sandbox; default-src 'none'; img-src data: https:; style-src 'unsafe-inline'; font-src data: https:");
    return res.send(createReadStream(ruta));
  });

  app.post("/api/documentos", { bodyLimit: (MAX_MB + 2) * 1024 * 1024 }, async (req, res) => {
    let pedido: ReturnType<typeof leerPedido>;
    try {
      pedido = leerPedido(req);
    } catch (e) {
      return res.code(400).send({ error: e instanceof Error ? e.message : String(e) });
    }
    const c = pedido.campos;
    const campos = camposDe(c);
    let datos: Buffer;
    let formato: Formato | null;
    let nombreOriginal = "";
    let origen = "subida";
    if (pedido.archivo) {
      datos = pedido.archivo.datos;
      formato = formatoDe(pedido.archivo.nombre, datos);
      nombreOriginal = pedido.archivo.nombre.slice(0, 160);
      if (!formato) return res.code(400).send({ error: `«${nombreOriginal}» no es una plantilla: sube un Word (.docx), Excel (.xlsx), PowerPoint (.pptx) o HTML (.html)` });
      campos.nombre ??= nombreOriginal.replace(/\.[^.]+$/, "");
    } else {
      formato = (FORMATOS as readonly string[]).includes(c.formato ?? "") ? (c.formato as Formato) : c.contenido ? "html" : null;
      if (!formato) return res.code(400).send({ error: `falta el archivo, o formato (${FORMATOS.join(", ")}) con contenido (HTML) o texto` });
      if (formato === "html" && c.contenido?.trim()) {
        datos = Buffer.from(c.contenido, "utf8");
        origen = "html";
      } else if (c.texto?.trim()) {
        const salida = join(tmpdir(), `plantilla-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${formato}`);
        const r = await python(["crear", formato, salida], { texto: c.texto, titulo: campos.nombre ?? "" });
        if (r.error) return res.code(400).send({ error: `no se pudo armar la plantilla: ${String(r.error)}` });
        datos = await readFile(salida);
        await unlink(salida).catch(() => undefined);
        origen = "texto";
      } else return res.code(400).send({ error: formato === "html" ? "falta contenido: el HTML de la plantilla" : "falta texto: el contenido de la plantilla (con «# » títulos, «- » viñetas y {{variables}})" });
    }
    if (!campos.nombre) return res.code(400).send({ error: "falta nombre: cómo se llama la plantilla" });
    if ((await listar()).length >= MAX_PLANTILLAS) return res.code(400).send({ error: `la biblioteca ya tiene ${MAX_PLANTILLAS} plantillas: borra alguna` });
    let id = c.id ? c.id.trim() : slug(campos.nombre);
    if (!ID.test(id)) return res.code(400).send({ error: "id: minúsculas, números y guiones (2 a 60)" });
    if (c.id && existsSync(carpetaDe(id))) return res.code(409).send({ error: `ya hay una plantilla con id «${id}»` });
    for (let i = 2; existsSync(carpetaDe(id)); i++) id = `${slug(campos.nombre).slice(0, 46)}-${i}`;
    const r = await guardarArchivo(id, formato, datos, null, campos, nombreOriginal, origen);
    if (!r.ok) return res.code(400).send({ error: r.error });
    app.log.info({ plantilla: id, formato }, "plantilla de documentos nueva");
    return res.code(201).send(r.meta);
  });

  app.put<{ Params: { id: string } }>("/api/documentos/:id", { bodyLimit: (MAX_MB + 2) * 1024 * 1024 }, async (req, res) => {
    const previa = await leerMeta(req.params.id);
    if (!previa) return res.code(404).send({ error: "esa plantilla de documentos no existe" });
    let pedido: ReturnType<typeof leerPedido>;
    try {
      pedido = leerPedido(req);
    } catch (e) {
      return res.code(400).send({ error: e instanceof Error ? e.message : String(e) });
    }
    const campos = camposDe(pedido.campos);
    let datos: Buffer | null = null;
    let formato: Formato = previa.formato;
    let nombreOriginal = previa.nombre_original;
    if (pedido.archivo) {
      const f = formatoDe(pedido.archivo.nombre, pedido.archivo.datos);
      if (!f) return res.code(400).send({ error: `«${pedido.archivo.nombre}» no es un Word, Excel, PowerPoint ni HTML` });
      datos = pedido.archivo.datos;
      formato = f;
      nombreOriginal = pedido.archivo.nombre.slice(0, 160);
    } else if (pedido.campos.contenido !== undefined) {
      if (previa.formato !== "html") return res.code(400).send({ error: "contenido solo cambia una plantilla HTML; para las demás sube el archivo nuevo" });
      datos = Buffer.from(pedido.campos.contenido, "utf8");
    }
    if (datos) {
      const r = await guardarArchivo(previa.id, formato, datos, previa, campos, nombreOriginal, previa.origen);
      if (!r.ok) return res.code(400).send({ error: r.error });
      return r.meta;
    }
    const meta: MetaDocumento = { ...previa, ...campos, actualizada: Date.now() };
    await writeFile(join(carpetaDe(previa.id), "meta.json"), JSON.stringify(meta, null, 2));
    return meta;
  });

  app.delete<{ Params: { id: string } }>("/api/documentos/:id", async (req, res) => {
    if (!confirmado(req)) return res.code(403).send({ error: "borrar una plantilla de documentos solo lo hace una persona desde la pantalla Documentos (X-Confirmado-Por-Persona: si)" });
    const m = await leerMeta(req.params.id);
    if (!m) return res.code(404).send({ error: "esa plantilla de documentos no existe" });
    await rm(carpetaDe(m.id), { recursive: true, force: true });
    app.log.warn({ plantilla: m.id }, "plantilla de documentos borrada");
    return { ok: true };
  });

  app.post<{ Params: { id: string } }>("/api/documentos/:id/recursos", { bodyLimit: (MAX_RECURSO_MB + 1) * 1024 * 1024 }, async (req, res) => {
    const m = await leerMeta(req.params.id);
    if (!m) return res.code(404).send({ error: "esa plantilla de documentos no existe" });
    let pedido: ReturnType<typeof leerPedido>;
    try {
      pedido = leerPedido(req);
    } catch (e) {
      return res.code(400).send({ error: e instanceof Error ? e.message : String(e) });
    }
    if (!pedido.archivo) return res.code(400).send({ error: "falta la imagen (multipart con campo «archivo», o JSON {nombre, archivo: {nombre, datos base64}})" });
    const nombre = slug(pedido.campos.nombre ?? pedido.archivo.nombre.replace(/\.[^.]+$/, "")).replace(/-/g, "_").slice(0, 40);
    if (!NOMBRE_RECURSO.test(nombre)) return res.code(400).send({ error: "nombre: minúsculas, números y _ (p. ej. «logo»)" });
    const t = tipoImagen(pedido.archivo.datos);
    if (!t) return res.code(400).send({ error: "la imagen tiene que ser PNG, JPEG, WebP o GIF" });
    if (pedido.archivo.datos.length > MAX_RECURSO_MB * 1024 * 1024) return res.code(400).send({ error: `la imagen pasa de ${MAX_RECURSO_MB} MB` });
    const dir = join(carpetaDe(m.id), "recursos");
    await mkdir(dir, { recursive: true });
    for (const r of m.recursos.filter((x) => x.nombre === nombre)) await unlink(join(dir, r.archivo)).catch(() => undefined);
    const archivo = `${nombre}.${t.ext}`;
    await writeFile(join(dir, archivo), pedido.archivo.datos);
    const meta: MetaDocumento = { ...m, recursos: [...m.recursos.filter((x) => x.nombre !== nombre), { nombre, archivo, tipo: t.tipo, tamano: pedido.archivo.datos.length }], actualizada: Date.now() };
    await writeFile(join(carpetaDe(m.id), "meta.json"), JSON.stringify(meta, null, 2));
    return meta;
  });

  app.get<{ Params: { id: string; nombre: string } }>("/api/documentos/:id/recursos/:nombre", async (req, res) => {
    const m = await leerMeta(req.params.id);
    const r = m?.recursos.find((x) => x.nombre === req.params.nombre);
    const ruta = m && r ? join(carpetaDe(m.id), "recursos", r.archivo) : "";
    if (!r || !existsSync(ruta)) return res.code(404).send({ error: "esa imagen no existe" });
    return res.header("Content-Type", r.tipo).header("Cache-Control", "no-store").header("X-Content-Type-Options", "nosniff").send(createReadStream(ruta));
  });

  app.delete<{ Params: { id: string; nombre: string } }>("/api/documentos/:id/recursos/:nombre", async (req, res) => {
    if (!confirmado(req)) return res.code(403).send({ error: "quitar una imagen de una plantilla solo lo hace una persona desde la pantalla Documentos" });
    const m = await leerMeta(req.params.id);
    const r = m?.recursos.find((x) => x.nombre === req.params.nombre);
    if (!m || !r) return res.code(404).send({ error: "esa imagen no existe" });
    await unlink(join(carpetaDe(m.id), "recursos", r.archivo)).catch(() => undefined);
    const meta: MetaDocumento = { ...m, recursos: m.recursos.filter((x) => x.nombre !== r.nombre), actualizada: Date.now() };
    await writeFile(join(carpetaDe(m.id), "meta.json"), JSON.stringify(meta, null, 2));
    return meta;
  });

  app.post<{ Params: { id: string } }>("/api/documentos/:id/probar", async (req, res) => {
    const m = await leerMeta(req.params.id);
    if (!m) return res.code(404).send({ error: "esa plantilla de documentos no existe" });
    const datos = ((req.body ?? {}) as { datos?: unknown }).datos;
    const salida = join(tmpdir(), `probar-${m.id}-${Date.now()}.${m.formato}`);
    const r = await python(["llenar", join(carpetaDe(m.id), m.archivo), m.formato, salida], { datos: datos && typeof datos === "object" ? datos : {}, recursos: join(carpetaDe(m.id), "recursos") });
    if (r.error) return res.code(400).send({ error: `no se pudo llenar: ${String(r.error)}` });
    const lleno = await readFile(salida);
    await unlink(salida).catch(() => undefined);
    res.header("Content-Type", MIME[m.formato]).header("Content-Disposition", `attachment; filename*=UTF-8''${encodeURIComponent(`prueba-${slug(m.nombre)}.${m.formato}`)}`)
      .header("X-Faltantes", encodeURIComponent(((r.faltantes as string[]) ?? []).join(","))).header("Cache-Control", "no-store");
    if (m.formato === "html") res.header("Content-Security-Policy", "sandbox");
    return res.send(lleno);
  });
}
