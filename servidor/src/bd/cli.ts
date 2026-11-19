/**
 * Comandos de la base (corren con la URL del MIGRADOR, el dueño de las tablas; nunca con la de la aplicación):
 *
 *   npm run bd -- migrar                                aplica las migraciones pendientes
 *   npm run bd -- estado                                las aplicadas y las que faltan
 *   npm run bd -- organizacion <slug> "<nombre>"        crea la organización (si no existe) y dice su id
 *   npm run bd -- migrar-datos --organizacion <slug> [--prueba] [--datos <ruta>]
 *                                                       pasa el estado de los archivos a la base (idempotente)
 *
 * La URL sale de BD_URL_MIGRADOR (o DATABASE_URL) y la clave, si va aparte, de BD_CLAVE_MIGRADOR_ARCHIVO.
 * Nada de esto se conecta a GCP: con Cloud SQL se usa a través del Cloud SQL Auth Proxy (docs/PRODUCCION.md).
 */
import pg from "pg";
import { opciones, usarPiscina } from "./conexion.ts";
import * as migraciones from "./migraciones.ts";

function piscinaMigrador(): pg.Pool {
  const url = (process.env.BD_URL_MIGRADOR ?? process.env.DATABASE_URL ?? "").trim();
  if (!url) throw new Error("define BD_URL_MIGRADOR (postgresql://vera_migrador@127.0.0.1:5482/vera) y BD_CLAVE_MIGRADOR_ARCHIVO");
  return new pg.Pool({ ...opciones(url, process.env.BD_CLAVE_MIGRADOR_ARCHIVO?.trim() || process.env.DATABASE_PASSWORD_FILE?.trim() || undefined), max: 2 });
}

function argumento(args: string[], nombre: string): string | undefined {
  const i = args.indexOf(nombre);
  return i >= 0 ? args[i + 1] : undefined;
}

async function principal(args: string[]): Promise<number> {
  const orden = args[0];
  const p = piscinaMigrador();
  try {
    if (orden === "migrar") {
      const hechas = await migraciones.migrar(p, undefined, (t) => console.log(t));
      console.log(hechas.length ? `${hechas.length} migración(es) aplicada(s)` : "la base ya estaba al día");
      return 0;
    }
    if (orden === "estado") {
      const c = await p.connect();
      try {
        const hechas = await migraciones.aplicadas(c);
        for (const h of hechas) console.log(`aplicada  ${String(h.version).padStart(4, "0")}_${h.nombre}  ${h.aplicada.toISOString()}`);
        for (const m of migraciones.pendientes(migraciones.leer(), hechas)) console.log(`pendiente ${String(m.version).padStart(4, "0")}_${m.nombre}`);
      } finally {
        c.release();
      }
      return 0;
    }
    if (orden === "organizacion") {
      const slug = args[1];
      if (!slug) throw new Error('uso: organizacion <slug> "<nombre>"');
      const r = await migraciones.crearOrganizacion(p, slug, args[2] ?? slug);
      console.log(`${r.creada ? "creada" : "ya existía"}: ${slug} ${r.id}`);
      return 0;
    }
    if (orden === "migrar-datos") {
      const slug = argumento(args, "--organizacion");
      if (!slug) throw new Error("uso: migrar-datos --organizacion <slug> [--prueba] [--datos <ruta>]");
      const { migrarDatos } = await import("./migrar_datos.ts");
      usarPiscina(p);
      const r = await migrarDatos({ slug, datos: argumento(args, "--datos"), prueba: args.includes("--prueba"), aviso: (t) => console.log(t) });
      console.log(JSON.stringify(r, null, 2));
      return 0;
    }
    console.error("órdenes: migrar | estado | organizacion <slug> <nombre> | migrar-datos --organizacion <slug> [--prueba] [--datos <ruta>]");
    return 2;
  } finally {
    usarPiscina(null);
    await p.end();
  }
}

principal(process.argv.slice(2)).then(
  (codigo) => process.exit(codigo),
  (e: unknown) => {
    console.error(`error: ${e instanceof Error ? e.message : String(e)}`);
    process.exit(1);
  },
);
