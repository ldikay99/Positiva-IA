/**
 * Pasa el estado de la plataforma de los archivos de /datos/estado a PostgreSQL, en la organización dada.
 *
 * - Idempotente: lo que ya está en la base no se repite ni se pisa (cada tipo tiene su llave natural: id de la
 *   corrida, de la aprobación, de la conversación; flujo y número de versión; huella del renglón del historial).
 *   Correrla dos veces deja lo mismo; correrla tras otra parcial completa lo que faltó.
 * - Todo va en UNA transacción: o pasa todo o no pasa nada. Cada elemento va en su propio SAVEPOINT: uno que la base
 *   rechaza (un renglón dañado, una restricción) se cuenta como omitido, con el motivo, y lo demás sigue.
 * - Con --prueba se hace completa y se revierte al final, para ver los conteos sin escribir.
 * - No toca los archivos: siguen ahí para volver atrás (quitar DATABASE_URL) hasta que se decida borrarlos.
 */
import { createHash } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { readdir, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { isDeepStrictEqual } from "node:util";
import { enOrganizacion, enTransaccion, organizacionPorSlug } from "./conexion.ts";
import * as estado from "./estado.ts";

export interface Conteo { leidos: number; escritos: number; ya_estaban: number; omitidos: number; errores: string[] }
export type Informe = { organizacion: string; datos: string; prueba: boolean; tipos: Record<string, Conteo> };
type Desenlace = "escrito" | "ya_estaba";

const ORIGINALES = new Set(["pqrd", "tutelas"]);

async function json<T>(ruta: string): Promise<T> {
  return JSON.parse(await readFile(ruta, "utf8")) as T;
}

async function archivos(carpeta: string, patron: RegExp): Promise<string[]> {
  return (await readdir(carpeta).catch(() => [] as string[])).filter((x) => patron.test(x)).sort();
}

async function* lineas(ruta: string): AsyncGenerator<string> {
  for await (const l of createInterface({ input: createReadStream(ruta, "utf8"), crlfDelay: Infinity })) if (l.trim()) yield l;
}

const existe = (sql: string, params: unknown[]) => enOrganizacion(async (c) => ((await c.query(sql, params)).rowCount ?? 0) > 0);
const flujoExiste = (id: string) => ORIGINALES.has(id) ? Promise.resolve(true) : existe("SELECT 1 FROM vera.flujos WHERE id = $1", [id]);

export async function migrarDatos(o: { slug: string; datos?: string; prueba?: boolean; aviso?: (t: string) => void }): Promise<Informe> {
  const datos = resolve(o.datos ?? process.env.RUTA_DATOS ?? resolve(import.meta.dirname, "..", "..", "..", "datos"));
  if (!existsSync(datos)) throw new Error(`no existe la carpeta de datos ${datos}`);
  const org = await organizacionPorSlug(o.slug);
  if (!org) throw new Error(`la organización «${o.slug}» no existe: créala antes con «organizacion ${o.slug} "<nombre>"»`);
  const aviso = o.aviso ?? (() => undefined);
  const tipos: Record<string, Conteo> = {};
  const conteo = (t: string): Conteo => (tipos[t] ??= { leidos: 0, escritos: 0, ya_estaban: 0, omitidos: 0, errores: [] });

  /** Un elemento: se lee, se pasa en su SAVEPOINT y se cuenta. */
  async function elemento(tipo: string, que: string, f: () => Promise<Desenlace>): Promise<void> {
    const c = conteo(tipo);
    c.leidos++;
    await enOrganizacion((cl) => cl.query("SAVEPOINT elemento"));
    try {
      const d = await f();
      await enOrganizacion((cl) => cl.query("RELEASE SAVEPOINT elemento"));
      if (d === "escrito") c.escritos++;
      else c.ya_estaban++;
    } catch (e) {
      await enOrganizacion((cl) => cl.query("ROLLBACK TO SAVEPOINT elemento"));
      estado.olvidarCaches();  // una llave o partición creada en el paso revertido ya no existe
      c.omitidos++;
      if (c.errores.length < 20) c.errores.push(`${que}: ${e instanceof Error ? e.message : String(e)}`.slice(0, 300));
    }
  }

  /** Un ajuste de un solo documento: se escribe si falta o si es distinto. */
  const ajuste = async (tipo: string, clave: estado.ClaveAjuste, archivo: string) => {
    if (!existsSync(join(datos, archivo))) return;
    await elemento(tipo, archivo, async () => {
      const valor = await json<unknown>(join(datos, archivo));
      if (isDeepStrictEqual(await estado.configuracion.ajuste(clave), valor)) return "ya_estaba";
      await estado.configuracion.ponerAjuste(clave, valor);
      return "escrito";
    });
  };

  const pasar = async (): Promise<void> => {
    // ------------------------------------------------------------ configuración
    await ajuste("configuracion", "configuracion", "configuracion.json");
    await ajuste("ia", "ia_global", "ia_global.json");
    await ajuste("ia", "ia_vera", "ia_vera.json");
    await ajuste("recursos", "recursos", "recursos.json");
    await ajuste("compuertas", "compuertas", "compuertas.json");

    if (existsSync(join(datos, "historial.jsonl"))) {
      let n = 0;
      for await (const linea of lineas(join(datos, "historial.jsonl"))) {
        await elemento("historial_configuracion", `renglón ${++n}`, async () => {
          const x = JSON.parse(linea) as estado.CambioBd;
          if (typeof x.t !== "number" || typeof x.clave !== "string") throw new Error("renglón sin t o clave");
          const nuevos = await estado.configuracion.agregarCambios([{ ...x, huella: createHash("sha256").update(linea).digest() }]);
          return nuevos ? "escrito" : "ya_estaba";
        });
      }
    }

    for (const a of await archivos(join(datos, "perfiles"), /\.json$/)) {
      await elemento("perfiles", a, async () => {
        const p = await json<{ nombre: string; descripcion?: string; t?: number; configuracion: unknown }>(join(datos, "perfiles", a));
        if (isDeepStrictEqual(await estado.configuracion.leerPerfil(p.nombre), p.configuracion)) return "ya_estaba";
        await estado.configuracion.guardarPerfil(p.nombre, p.descripcion ?? "", p.configuracion, p.t ?? Date.now());
        return "escrito";
      });
    }

    // ------------------------------------------------------------ flujos
    const codigo = join(datos, "flujos_codigo");
    for (const a of await archivos(codigo, /^[a-z][a-z0-9-]{1,40}\.json$/)) {
      await elemento("flujos_copias", a, async () => {
        const g = await json<estado.CopiaGuardada>(join(codigo, a));
        if (await existe("SELECT 1 FROM vera.flujos WHERE id = $1", [g.id])) return "ya_estaba";
        await estado.flujos.guardarCopia(g);
        return "escrito";
      });
    }
    if (existsSync(join(codigo, "_originales.json"))) {
      await elemento("flujos_originales", "_originales.json", async () => {
        const nombres = await json<Record<string, { nombre?: string; descripcion?: string }>>(join(codigo, "_originales.json"));
        if (isDeepStrictEqual((await estado.flujos.cargar()).originales, nombres)) return "ya_estaba";
        await estado.flujos.guardarOriginales(nombres);
        return "escrito";
      });
    }

    const decl = join(datos, "flujos");
    for (const a of await archivos(decl, /\.json$/)) {
      await elemento("flujos_declarativos", a, async () => {
        const g = await json<estado.Guardado & { definicion: { id: string; nombre: string; descripcion?: string } }>(join(decl, a));
        const id = g.definicion?.id;
        if (typeof id !== "string") throw new Error("sin definicion.id");
        const ya = await enOrganizacion(async (cl) => (await cl.query<{ version_actual: number | null }>("SELECT version_actual FROM vera.flujos WHERE id = $1", [id])).rows[0]);
        if (ya && (ya.version_actual ?? 0) >= g.version) return "ya_estaba";
        await estado.flujos.guardarDeclarativo(g);
        return "escrito";
      });
    }
    for (const a of await archivos(join(decl, "versiones"), /^[a-z][a-z0-9-]{1,40}\.v\d+\.json$/)) {
      const m = /^(.+)\.v(\d+)\.json$/.exec(a)!;
      const id = m[1]!;
      const v = Number(m[2]);
      await elemento("flujos_versiones", a, async () => {
        if (!(await existe("SELECT 1 FROM vera.flujos WHERE id = $1 AND tipo = 'declarativo'", [id]))) throw new Error("el flujo ya no existe");
        if (await existe("SELECT 1 FROM vera.flujo_versiones WHERE flujo_id = $1 AND clase = 'definicion' AND version = $2", [id, v])) return "ya_estaba";
        const crudo = await json<Record<string, unknown>>(join(decl, "versiones", a));
        // Las versiones viejas guardaban la definición sola.
        const g: estado.Guardado = "definicion" in crudo && "version" in crudo ? crudo as unknown as estado.Guardado : { definicion: crudo, version: v, actualizado: Date.now() };
        await estado.flujos.guardarVersionAnterior(id, { ...g, version: v });
        return "escrito";
      });
    }

    // estructuras de los de código (la vigente y sus versiones)
    for (const a of await archivos(codigo, /\.estructura(\.v\d+)?\.json$/)) {
      const m = /^(.+?)\.estructura(?:\.v(\d+))?\.json$/.exec(a)!;
      const id = m[1]!;
      const vigente = m[2] === undefined;
      await elemento("estructuras", a, async () => {
        if (!ORIGINALES.has(id) && !(await existe("SELECT 1 FROM vera.flujos WHERE id = $1 AND tipo = 'codigo'", [id]))) throw new Error("el flujo ya no existe");
        const g = await json<estado.EstructuraGuardada>(join(codigo, a));
        const hay = await existe("SELECT 1 FROM vera.flujo_versiones WHERE flujo_id = $1 AND clase = 'estructura' AND version = $2", [id, g.version]);
        const sinVigente = vigente && !(await existe("SELECT 1 FROM vera.flujos WHERE id = $1 AND estructura_actual IS NOT NULL", [id]));
        if (hay && !sinVigente) return "ya_estaba";
        await estado.estructuras.guardar(id, g, sinVigente);
        return "escrito";
      });
    }

    for (const a of await archivos(join(datos, "flujos_historial"), /^[a-z][a-z0-9-]{1,40}\.json$/)) {
      const id = a.slice(0, -5);
      await elemento("historial_versiones", a, async () => {
        if (!(await flujoExiste(id))) throw new Error("el flujo ya no existe");
        if (await existe("SELECT 1 FROM vera.metas_version WHERE flujo_id = $1", [id])) return "ya_estaba";
        const x = await json<{ versiones?: Record<string, estado.Meta> }>(join(datos, "flujos_historial", a));
        await estado.metas.escribir(id, x.versiones ?? {});
        return "escrito";
      });
    }

    // ------------------------------------------------------------ programación
    if (existsSync(join(datos, "programaciones.json"))) {
      const actuales = await estado.programacion.leer();
      for (const [flujo, p] of Object.entries(await json<Record<string, estado.ProgramacionBd>>(join(datos, "programaciones.json")))) {
        await elemento("programaciones", flujo, async () => {
          if (isDeepStrictEqual(actuales[flujo], p)) return "ya_estaba";
          if (!(await flujoExiste(flujo))) throw new Error("el flujo ya no existe");
          await estado.programacion.poner(flujo, p);
          return "escrito";
        });
      }
    }
    if (existsSync(join(datos, "programador.json"))) {
      await elemento("programador", "programador.json", async () => {
        const antes = await estado.programacion.marcas();
        const marcas = { ...antes, ...(await json<Record<string, number>>(join(datos, "programador.json"))) };
        if (isDeepStrictEqual(antes, marcas)) return "ya_estaba";
        await estado.programacion.escribirMarcas(marcas);
        return "escrito";
      });
    }

    // ------------------------------------------------------------ ejecuciones
    for (const a of await archivos(join(datos, "corridas"), /\.jsonl$/)) {
      let n = 0;
      for await (const linea of lineas(join(datos, "corridas", a))) {
        await elemento("corridas", `${a} renglón ${++n}`, async () => {
          const x = JSON.parse(linea) as estado.CorridaBd;
          if (typeof x.id !== "string" || typeof x.creada !== "number") throw new Error("sin id o creada");
          x.flujo ??= "pqrd";  // las de antes de los flujos eran todas de PQRD
          return (await estado.corridas.guardar(x)) ? "escrito" : "ya_estaba";
        });
      }
      aviso(`corridas ${a}: ${n}`);
    }

    // ------------------------------------------------------------ aprobaciones y aplazadas
    const apr = join(datos, "aprobaciones");
    for (const a of (await archivos(apr, /^apr-.*\.json$/)).filter((x) => !x.endsWith(".estado.json"))) {
      const id = a.slice(0, -5);
      await elemento("aprobaciones", a, async () => {
        if (await existe("SELECT 1 FROM vera.aprobaciones WHERE id = $1", [id])) return "ya_estaba";
        const rec = await json<estado.AprobacionBd>(join(apr, a));
        const est = existsSync(join(apr, `${id}.estado.json`)) ? await json<unknown>(join(apr, `${id}.estado.json`)) : {};
        await estado.aprobaciones.nueva(rec, est);
        if (existsSync(join(apr, `${id}.reanudada`))) {
          const corrida = (await readFile(join(apr, `${id}.reanudada`), "utf8")).trim() || String(rec.reanudada ?? "") || "(otra)";
          await estado.aprobaciones.marcarReanudada(id, corrida);
        }
        return "escrito";
      });
    }
    for (const a of await archivos(join(datos, "aplazadas"), /^apl-.*\.json$/)) {
      await elemento("aplazadas", a, async () => {
        const rec = await json<estado.AplazadaBd>(join(datos, "aplazadas", a));
        if (await existe("SELECT 1 FROM vera.aplazadas WHERE id = $1", [rec.id])) return "ya_estaba";
        await estado.aplazadas.escribir(rec);
        return "escrito";
      });
    }

    // ------------------------------------------------------------ conversaciones
    const chats = join(datos, "chats");
    for (const a of await archivos(chats, /^[a-z0-9-]{8,40}\.json$/)) {
      let conv: estado.ConversacionBd | null = null;
      await elemento("conversaciones", a, async () => {
        conv = await json<estado.ConversacionBd>(join(chats, a));
        if (await existe("SELECT 1 FROM vera.conversaciones WHERE id = $1", [conv.id])) return "ya_estaba";
        await estado.chats.guardar(conv);
        return "escrito";
      });
      const id = (conv as estado.ConversacionBd | null)?.id;
      if (!id || !(await existe("SELECT 1 FROM vera.conversaciones WHERE id = $1", [id]))) continue;
      // las fichas de sus adjuntos (los bytes se quedan donde están)
      const carpeta = join(chats, "adjuntos", id);
      for (const m of await archivos(carpeta, /^[a-z0-9-]{6,20}\.json$/)) {
        await elemento("adjuntos_conversacion", `${id}/${m}`, async () => {
          const f = await json<{ id: string; nombre: string; tipo: string; tamano: number; extension: string }>(join(carpeta, m));
          if (await estado.chats.adjunto(id, f.id)) return "ya_estaba";
          await estado.chats.guardarAdjunto(id, { ...f, objeto: `chats/adjuntos/${id}/${f.id}${f.extension}` });
          return "escrito";
        });
      }
    }
  };

  try {
    await enTransaccion(org, pasar, Boolean(o.prueba));
  } finally {
    // Lo creado en la transacción (llaves, particiones) no quedó si se revirtió: la memoria no lo da por hecho.
    estado.olvidarCaches();
  }
  return { organizacion: o.slug, datos, prueba: Boolean(o.prueba), tipos };
}
