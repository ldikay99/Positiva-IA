/**
 * Pruebas del estado en PostgreSQL contra una base REAL (el contenedor «postgres» de docker-compose.yml): migraciones
 * versionadas, aislamiento por organización con Row Level Security (una organización no ve ni toca lo de otra aunque
 * la consulta no filtre), cifrado por organización, la capa de repositorio de cada tipo de estado a través de los
 * módulos del servidor y la migración de datos desde archivos (idempotente, con modo de prueba).
 *
 * Crea una base y un usuario de aplicación propios (vera_prueba_<azar>) y los borra al terminar; no toca la base «vera».
 * Sin DATABASE_URL_PRUEBAS se saltan con un aviso. Nunca se conectan a GCP.
 *
 *   DATABASE_URL_PRUEBAS=postgresql://vera_migrador:<clave>@127.0.0.1:5482/postgres node --test src/pruebas_bd.ts
 */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import pg from "pg";

const ADMIN = process.env.DATABASE_URL_PRUEBAS?.trim();
const saltar = ADMIN ? false : "sin DATABASE_URL_PRUEBAS (la URL de un usuario que pueda crear bases y roles)";
if (!ADMIN) console.warn(`[pruebas_bd] se saltan: ${saltar}. Ver docs/PRODUCCION.md › Base de datos › Pruebas.`);

const sufijo = randomBytes(4).toString("hex");
const BASE = `vera_prueba_${sufijo}`;
const USUARIO = `vera_prueba_app_${sufijo}`;
const CLAVE = randomBytes(18).toString("base64url");
const DATOS = mkdtempSync(join(tmpdir(), "vera-bd-datos-"));
process.env.RUTA_DATOS = DATOS;
process.env.RUTA_ARCHIVOS = mkdtempSync(join(tmpdir(), "vera-bd-archivos-"));
process.env.APROBACIONES_REVISION_MS = "3600000";
process.env.APLAZADAS_REVISION_MS = "3600000";
delete process.env.CLAVE_CREDENCIALES;

const enBase = (url: string, base: string, usuario?: string, clave?: string) => {
  const u = new URL(url);
  u.pathname = `/${base}`;
  if (usuario) u.username = usuario;
  if (clave) u.password = clave;
  return u.toString();
};

let admin: pg.Pool;
let migrador: pg.Pool;
let app: pg.Pool;
const org: Record<string, string> = {};
// Los módulos se importan después de fijar el entorno (leen DATABASE_URL al usarse).
let bd: typeof import("./bd/conexion.ts");
let estado: typeof import("./bd/estado.ts");
let cifrado: typeof import("./bd/cifrado.ts");
let migraciones: typeof import("./bd/migraciones.ts");

before(async () => {
  if (!ADMIN) return;
  admin = new pg.Pool({ connectionString: ADMIN, max: 2 });
  await admin.query(`CREATE DATABASE ${BASE}`);
  migrador = new pg.Pool({ connectionString: enBase(ADMIN, BASE), max: 3 });
  migraciones = await import("./bd/migraciones.ts");
  const aplicadas = await migraciones.migrar(migrador);
  assert.deepEqual(aplicadas.map((m) => m.version), [1, 2, 3]);
  // El usuario con el que entra la aplicación: miembro de vera_app, sin BYPASSRLS, dueño de nada.
  await admin.query(`CREATE ROLE ${USUARIO} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '${CLAVE}' IN ROLE vera_app`);
  for (const s of ["org-a", "org-b", "org-c"]) org[s] = (await migraciones.crearOrganizacion(migrador, s, `Organización ${s}`)).id;
  process.env.DATABASE_URL = enBase(ADMIN, BASE, USUARIO, CLAVE);
  process.env.VERA_KEK_LOCAL = randomBytes(32).toString("base64");
  process.env.VERA_ORGANIZACION = "org-a";
  bd = await import("./bd/conexion.ts");
  estado = await import("./bd/estado.ts");
  cifrado = await import("./bd/cifrado.ts");
  app = new pg.Pool({ connectionString: process.env.DATABASE_URL, max: 2 });
  const r = await bd.iniciar();
  assert.equal(r.organizacion, org["org-a"]);
});

after(async () => {
  if (!ADMIN) return;
  await bd?.cerrar().catch(() => undefined);
  await app?.end().catch(() => undefined);
  await migrador?.end().catch(() => undefined);
  await admin.query(`DROP DATABASE IF EXISTS ${BASE} WITH (FORCE)`).catch(() => undefined);
  await admin.query(`DROP ROLE IF EXISTS ${USUARIO}`).catch(() => undefined);
  await admin.end();
});

const comoOrg = <T>(slug: string, f: () => Promise<T>) => bd.contextoOrganizacion.run(org[slug]!, f);

/** Una consulta cruda como la aplicación, con la organización fijada (o sin ninguna). */
async function crudo<T extends pg.QueryResultRow>(slug: string | null, sql: string, params: unknown[] = []): Promise<pg.QueryResult<T>> {
  const c = await app.connect();
  try {
    await c.query("BEGIN");
    if (slug) await c.query("SELECT set_config('vera.organizacion_id', $1, true)", [org[slug]]);
    const r = await c.query<T>(sql, params);
    await c.query("COMMIT");
    return r;
  } catch (e) {
    await c.query("ROLLBACK");
    throw e;
  } finally {
    c.release();
  }
}

async function codigoDeError(p: Promise<unknown>): Promise<string> {
  try {
    await p;
  } catch (e) {
    return String((e as { code?: string }).code ?? (e as Error).message);
  }
  return "sin error";
}

const hasta = async (f: () => Promise<boolean>, ms = 5000) => {
  for (const t0 = Date.now(); Date.now() - t0 < ms; await new Promise((r) => setTimeout(r, 25))) if (await f()) return;
  assert.fail("no se cumplió a tiempo");
};

// =====================================================================  migraciones

test("migraciones: al día, idempotentes y una migración aplicada no se puede editar", { skip: saltar }, async () => {
  assert.deepEqual(await migraciones.alDia(migrador), { ok: true, faltan: [] });
  assert.deepEqual(await migraciones.migrar(migrador), []);
  const copia = mkdtempSync(join(tmpdir(), "vera-migraciones-"));
  cpSync(migraciones.CARPETA, copia, { recursive: true });
  writeFileSync(join(copia, "0002_estado_de_la_plataforma.sql"), readFileSync(join(copia, "0002_estado_de_la_plataforma.sql"), "utf8") + "\n-- cambio\n");
  await assert.rejects(migraciones.migrar(migrador, copia), /cambió después de aplicarse/);
  writeFileSync(join(copia, "0005_salto.sql"), "SELECT 1;");
  assert.throws(() => migraciones.leer(copia), /falta la 0004/);
  // la aplicación puede ver si está al día, pero no migrar ni crear organizaciones
  assert.deepEqual(await migraciones.alDia(app), { ok: true, faltan: [] });
  assert.equal(await codigoDeError(app.query("INSERT INTO vera.organizaciones (slug, nombre) VALUES ('intrusa', 'Intrusa')")), "42501");
  assert.equal(await codigoDeError(app.query("CREATE TABLE vera.otra (x int)")), "42501");
});

// =====================================================================  aislamiento

test("aislamiento: Row Level Security separa las organizaciones aunque la consulta no filtre", { skip: saltar }, async () => {
  await comoOrg("org-a", () => estado.chats.guardar({ id: "conv-aislada-1", titulo: "De A", flujo: "pqrd", creada: Date.now(), actualizada: Date.now(),
    mensajes: [{ rol: "usuario", texto: "hola desde A", t: Date.now() }] }));
  await comoOrg("org-a", () => estado.corridas.guardar({ id: "20261119120000-aaaaaa", flujo: "pqrd", caso: "c-a", estado: "terminada", creada: Date.now(), eventos: [{ tipo: "inicio", t: Date.now() / 1000 }] }));

  // desde la capa de repositorio
  assert.equal((await comoOrg("org-a", () => estado.chats.listar())).length, 1);
  assert.deepEqual(await comoOrg("org-b", () => estado.chats.listar()), []);
  assert.equal(await comoOrg("org-b", () => estado.chats.leer("conv-aislada-1")), null);
  assert.equal(await comoOrg("org-b", () => estado.corridas.contarDe("pqrd")), 0);
  assert.equal(await comoOrg("org-b", () => estado.chats.borrar("conv-aislada-1")), false);
  assert.equal(await comoOrg("org-b", () => estado.corridas.borrarDe("pqrd")), 0);

  // con SQL crudo y SIN filtro de organización: la base decide
  const contar = (slug: string | null, tabla: string) => crudo<{ n: string }>(slug, `SELECT count(*) AS n FROM vera.${tabla}`).then((r) => Number(r.rows[0]!.n));
  for (const tabla of ["conversaciones", "mensajes", "corridas", "eventos_corrida", "llaves_datos"]) {
    assert.ok((await contar("org-a", tabla)) >= 1, tabla);
    assert.equal(await contar("org-b", tabla), 0, tabla);
    assert.equal(await contar(null, tabla), 0, `${tabla} sin organización fijada`);
  }
  assert.deepEqual((await crudo<{ slug: string }>("org-b", "SELECT slug FROM vera.organizaciones")).rows.map((r) => r.slug), ["org-b"]);
  // no se escribe en otra organización ni se cambia lo suyo
  const ajena = crudo("org-b", "INSERT INTO vera.ajustes (organizacion_id, clave, valor) VALUES ($1, 'recursos', '{}')", [org["org-a"]]);
  assert.equal(await codigoDeError(ajena), "42501");
  assert.equal((await crudo("org-b", "UPDATE vera.conversaciones SET n_mensajes = 99")).rowCount, 0);
  assert.equal((await crudo("org-b", "DELETE FROM vera.corridas")).rowCount, 0);
  // las particiones no se leen directo (se saltarían la política de la tabla madre)
  const mes = new Date().toISOString().slice(0, 7).replace("-", "");
  assert.equal(await codigoDeError(crudo("org-b", `SELECT * FROM vera.corridas_${mes}`)), "42501");
  // una transacción abierta no cambia de organización
  await assert.rejects(bd.enTransaccion(org["org-a"]!, () => bd.enOrganizacion(async () => 1, org["org-b"])), /no cambia de organización/);
});

test("aislamiento: auditoría solo se agrega, versiones inmutables, una organización siempre con administrador", { skip: saltar }, async () => {
  await crudo("org-a", "INSERT INTO vera.auditoria (organizacion_id, actor, accion, objeto_tipo, objeto_id) VALUES ($1, 'sistema', 'prueba.auditoria', 'prueba', 'x')", [org["org-a"]]);
  assert.equal(await codigoDeError(crudo("org-a", "UPDATE vera.auditoria SET accion = 'otra'")), "42501");
  assert.equal(await codigoDeError(crudo("org-a", "DELETE FROM vera.auditoria")), "42501");
  // ni el dueño la cambia
  const duena = await migrador.connect();
  try {
    await duena.query("BEGIN");
    await duena.query("SELECT set_config('vera.organizacion_id', $1, true)", [org["org-a"]]);
    assert.equal(await codigoDeError(duena.query("UPDATE vera.auditoria SET accion = 'otra'")), "42501");
    await duena.query("ROLLBACK");
  } finally {
    duena.release();
  }
  // versión de un flujo: se agrega, no se cambia
  await comoOrg("org-a", () => estado.flujos.guardarDeclarativo({ definicion: { id: "inmutable", nombre: "Inmutable", nodos: [], aristas: [] }, version: 1, actualizado: Date.now() }));
  assert.equal(await codigoDeError(crudo("org-a", "UPDATE vera.flujo_versiones SET nota = 'x'")), "42501");
  // membresías: la organización no se queda sin administrador
  const p = (await migrador.query<{ id: string }>("INSERT INTO vera.personas (correo, nombre) VALUES ($1, 'Ana') RETURNING id", [`ana-${sufijo}@ejemplo.org`])).rows[0]!.id;
  assert.equal(await codigoDeError(crudo("org-a", "INSERT INTO vera.membresias (organizacion_id, persona_id, rol) VALUES ($1, $2, 'lector')", [org["org-a"], p])), "23514");
  await crudo("org-a", "INSERT INTO vera.membresias (organizacion_id, persona_id, rol) VALUES ($1, $2, 'administrador')", [org["org-a"], p]);
  assert.equal(await codigoDeError(crudo("org-a", "UPDATE vera.membresias SET rol = 'editor'")), "23514");
  assert.equal(await codigoDeError(crudo("org-a", "INSERT INTO vera.membresias (organizacion_id, persona_id, rol) VALUES ($1, $2, 'jefe')", [org["org-a"], p])), "23514");
  // la persona se ve desde su organización, no desde otra
  assert.equal((await crudo("org-a", "SELECT 1 FROM vera.personas WHERE id = $1", [p])).rowCount, 1);
  assert.equal((await crudo("org-b", "SELECT 1 FROM vera.personas WHERE id = $1", [p])).rowCount, 0);
  assert.equal((await crudo("org-a", "UPDATE vera.personas SET nombre = 'Otra' WHERE id = $1", [p])).rowCount, 0);  // solo la propia
});

// =====================================================================  cifrado

test("cifrado: el contenido va cifrado con la DEK de su organización, atado a su fila", { skip: saltar }, async () => {
  const secreto = `Historia clínica de Ana María ${sufijo}`;
  await comoOrg("org-a", () => estado.chats.guardar({ id: "conv-cifrada-1", titulo: "Cifrada", flujo: "pqrd", creada: Date.now(), actualizada: Date.now(),
    mensajes: [{ rol: "usuario", texto: secreto, t: Date.now() }, { rol: "asistente", texto: "otro", t: Date.now() }] }));
  const filas = await crudo<{ orden: number; contenido: Buffer }>("org-a", "SELECT orden, contenido FROM vera.mensajes WHERE conversacion_id = 'conv-cifrada-1' ORDER BY orden");
  assert.equal(filas.rows.length, 2);
  for (const f of filas.rows) assert.ok(!f.contenido.toString("latin1").includes("Ana") && !f.contenido.toString("utf8").includes(secreto));
  // la misma fila copiada a otro lugar no se descifra (dato asociado: organización|tabla|id)
  await crudo("org-a", "UPDATE vera.mensajes SET contenido = $1 WHERE conversacion_id = 'conv-cifrada-1' AND orden = 1", [filas.rows[0]!.contenido]);
  estado.olvidarCaches();
  await assert.rejects(comoOrg("org-a", () => estado.chats.leer("conv-cifrada-1")), /unable to authenticate|Unsupported state/);
  // la DEK se guarda envuelta, nunca en claro; otra KEK no la abre
  const llaves = await crudo<{ proposito: string; kek_ref: string }>("org-a", "SELECT proposito, kek_ref FROM vera.llaves_datos");
  assert.ok(llaves.rows.some((l) => l.proposito === "general") && llaves.rows.some((l) => l.proposito === "sensible"));
  assert.ok(llaves.rows.every((l) => l.kek_ref.startsWith("local:")));
  const kek = process.env.VERA_KEK_LOCAL;
  process.env.VERA_KEK_LOCAL = randomBytes(32).toString("base64");
  cifrado.reiniciar();
  await assert.rejects(comoOrg("org-a", () => estado.chats.listar()), /unable to authenticate|Unsupported state/);
  process.env.VERA_KEK_LOCAL = kek;
  cifrado.reiniciar();
  assert.ok((await comoOrg("org-a", () => estado.chats.listar())).length >= 1);
  // la corrida de un flujo sensible (pqrd) usa la DEK del mes
  const r = await crudo<{ proposito: string; periodo: string }>("org-a",
    "SELECT l.proposito, to_char(l.periodo, 'YYYY-MM-DD') AS periodo FROM vera.corridas c JOIN vera.llaves_datos l ON l.id = c.llave_id AND l.organizacion_id = c.organizacion_id LIMIT 1");
  assert.equal(r.rows[0]!.proposito, "sensible");
  assert.equal(r.rows[0]!.periodo, cifrado.periodoDe(Date.now()));
});

// =====================================================================  cada tipo de estado

test("ejecuciones: eventos en tramos, resultado enlazado, una sola vez, por flujo y borrado", { skip: saltar }, async () => {
  const eventos = Array.from({ length: 1203 }, (_, i) => ({ tipo: i % 2 ? "paso" : "log", t: 1_700_000_000 + i, n: i, texto: `evento ${i}` }));
  const resultado = { tipo: "resultado", t: 1_700_001_300, accion: "radicado", motivo: "ok", corrida: "20261101080000-bbbbbb" };
  eventos.push(resultado as never);
  const c = { id: "20261101080000-bbbbbb", flujo: "decl-x", caso: "caso-1", estado: "terminada", creada: Date.parse("2026-11-01T08:00:00Z"), inicio: Date.parse("2026-11-01T08:00:01Z"),
    fin: Date.parse("2026-11-01T08:01:00Z"), origen: "manual", configuracion: { ia: { modelo: "m" } }, entrada: { nombre: "Ana" }, eventos, resultado };
  assert.equal(await estado.corridas.guardar(c as never), true);
  assert.equal(await estado.corridas.guardar(c as never), false);  // ya estaba
  const tramos = await crudo<{ tramo: number; n: number; tipos: string[] }>("org-a", "SELECT tramo, n, tipos FROM vera.eventos_corrida WHERE corrida_id = $1 ORDER BY tramo", [c.id]);
  assert.deepEqual(tramos.rows.map((t) => t.n), [500, 500, 204]);
  assert.deepEqual(tramos.rows[2]!.tipos.sort(), ["log", "paso", "resultado"]);
  const [x] = await estado.corridas.cargar("decl-x");
  assert.ok(x);
  assert.deepEqual(x.eventos, eventos);
  assert.deepEqual(x.resultado, resultado);
  assert.equal(x.eventos.at(-1), x.resultado);  // el mismo objeto, como en memoria
  assert.deepEqual((x as Record<string, unknown>).entrada, { nombre: "Ana" });
  assert.equal(x.creada, c.creada);
  // las de otro mes y la más reciente primero en la carga (de la más vieja a la más nueva)
  await estado.corridas.guardar({ id: "20260915080000-cccccc", flujo: "decl-x", caso: "caso-0", estado: "fallida", creada: Date.parse("2026-09-15T08:00:00Z"), eventos: [] } as never);
  assert.deepEqual((await estado.corridas.cargar("decl-x")).map((y) => y.id), ["20260915080000-cccccc", "20261101080000-bbbbbb"]);
  assert.deepEqual((await estado.corridas.cargar("decl-x", 1)).map((y) => y.id), ["20261101080000-bbbbbb"]);
  // a través del motor: contar y borrar las de un flujo
  const motor = await import("./motor.ts");
  assert.equal(await motor.contarCorridasDe("decl-x"), 2);
  assert.equal(await motor.borrarCorridasDe("decl-x"), 2);
  assert.equal((await crudo("org-a", "SELECT 1 FROM vera.eventos_corrida WHERE corrida_id = $1", [c.id])).rowCount, 0);
});

test("flujos, versiones, copias, estructuras e historial de versiones a través de sus módulos", { skip: saltar }, async () => {
  const def = (v: number) => ({ id: "pagos-bd", nombre: `Pagos v${v}`, descripcion: "", modelo: "gemini-2.5-flash",
    nodos: [{ id: "inicio", tipo: "webhook", nombre: "Entrada", config: { secreto: "token-del-webhook-123" } }, { id: "fin", tipo: "salida", nombre: "Fin", config: { desenlace: "completar" } }],
    aristas: [{ desde: "inicio", hacia: "fin" }] });
  await estado.flujos.guardarDeclarativo({ definicion: def(1), version: 1, actualizado: Date.parse("2026-11-01T00:00:00Z") });
  await estado.flujos.guardarDeclarativo({ definicion: def(2), version: 2, actualizado: Date.parse("2026-11-02T00:00:00Z"), nota: "segunda" });
  // el token del webhook no queda en claro en la definición
  const crudoDef = await crudo<{ definicion: { nodos: { config: Record<string, unknown> }[] }; secretos: Buffer | null }>("org-a",
    "SELECT definicion, secretos FROM vera.flujo_versiones WHERE flujo_id = 'pagos-bd' AND version = 2");
  assert.equal(crudoDef.rows[0]!.definicion.nodos[0]!.config.secreto, undefined);
  assert.ok(crudoDef.rows[0]!.secretos && !crudoDef.rows[0]!.secretos.toString("latin1").includes("token-del-webhook"));
  // copia de código, nombre propio de un original y una estructura editada
  await estado.flujos.guardarCopia({ id: "pqrd-norte", base: "pqrd", nombre: "PQRD norte", descripcion: "copia", copia_de: "pqrd", creado: 1, actualizado: 2 });
  await estado.flujos.guardarOriginales({ tutelas: { nombre: "Tutelas (nuevo nombre)" } });
  await estado.estructuras.guardar("pqrd-norte", { definicion: { nodos: [{ id: "a", tipo: "x" }], aristas: [] }, version: 1, actualizado: 3, tramos: [], intercambio: null, apagadas: ["a"] }, true);
  await estado.metas.escribir("pagos-bd", { 1: { autor: "persona", fecha: 10 }, 2: { autor: "vera", fecha: 20, pedido: "cambia el nombre de Ana María", pedido_id: "p1", resumen: "Nombre" } });
  const t = await crudo<{ textos: Buffer }>("org-a", "SELECT textos FROM vera.metas_version WHERE flujo_id = 'pagos-bd' AND version = 2");
  assert.ok(!t.rows[0]!.textos.toString("latin1").includes("Ana"));

  // los módulos de siempre, ahora sobre la base
  const flujos = await import("./flujos.ts");
  const versiones = await import("./versiones.ts");
  const estructuras = await import("./estructuras.ts");
  await flujos.cargar();
  const f = flujos.obtener("pagos-bd")!;
  assert.equal(f.version, 2);
  assert.equal(f.nombre, "Pagos v2");
  assert.equal((f.definicion!.nodos[0]!.config as { secreto?: string }).secreto, "token-del-webhook-123");
  assert.deepEqual((await flujos.versiones("pagos-bd"))!.map((v) => [v.version, v.actual ?? false]), [[2, true], [1, false]]);
  assert.equal((await flujos.definicionDeVersion("pagos-bd", 1))!.nombre, "Pagos v1");
  assert.equal(flujos.baseDe("pqrd-norte"), "pqrd");
  assert.equal(flujos.obtener("tutelas")?.nombre, "Tutelas (nuevo nombre)");
  assert.deepEqual(estructuras.obtener("pqrd-norte")?.apagadas, ["a"]);
  const metas = await versiones.leer("pagos-bd");
  assert.equal(metas[2]!.pedido, "cambia el nombre de Ana María");
  // renombrar una copia y borrar: se van sus versiones e historial (en cascada)
  assert.equal((await flujos.renombrar("pqrd-norte", { nombre: "PQRD del norte" })).ok, true);
  await flujos.cargar();
  assert.equal(flujos.obtener("pqrd-norte")?.nombre, "PQRD del norte");
  assert.equal(await flujos.borrar("pagos-bd"), true);
  assert.equal((await crudo("org-a", "SELECT 1 FROM vera.flujo_versiones WHERE flujo_id = 'pagos-bd'")).rowCount, 0);
  assert.equal((await crudo("org-a", "SELECT 1 FROM vera.metas_version WHERE flujo_id = 'pagos-bd'")).rowCount, 0);
  assert.deepEqual(await versiones.leer("pagos-bd"), {});
  // otra organización no ve nada de esto
  assert.deepEqual(await comoOrg("org-b", () => estado.flujos.cargar()), { declarativos: [], copias: [], originales: {} });
});

test("configuración, perfiles, IA, recursos, compuertas y programación a través de sus módulos", { skip: saltar }, async () => {
  const configuracion = await import("./configuracion.ts");
  const recursos = await import("./recursos.ts");
  const compuerta = await import("./compuerta.ts");
  const programador = await import("./programador.ts");
  const ia = await import("./ia.ts");
  const c0 = await configuracion.cargar();
  const cambios = await configuracion.guardar({ ...c0, concurrencia: 1, topeSegundos: 120 }, "más corto");
  assert.deepEqual(cambios.map((x) => x.clave).sort(), ["concurrencia", "topeSegundos"]);
  assert.deepEqual((await configuracion.historial()).map((x) => [x.clave, x.nota]).sort(), [["concurrencia", "más corto"], ["topeSegundos", "más corto"]]);
  assert.equal((await configuracion.cargar()).topeSegundos, 120);
  assert.equal(await configuracion.guardarPerfil("Rápido", "para pruebas"), true);
  assert.deepEqual((await configuracion.perfiles()).map((p) => p.nombre), ["Rápido"]);
  assert.equal((await configuracion.leerPerfil("Rápido"))?.topeSegundos, 120);
  assert.equal(await configuracion.borrarPerfil("Rápido"), true);
  assert.equal(await configuracion.leerPerfil("Rápido"), null);
  // ia_global sin archivo: de la base
  await estado.configuracion.ponerAjuste("ia_global", { proveedor: "vertex", modelo: "gemini-2.5-pro" });
  await ia.precargarDeBd();
  assert.equal(ia.obtenerGlobal().modelo, "gemini-2.5-pro");
  // recursos y compuerta
  assert.equal((await recursos.poner({ id: "pqrd", tipo: "codigo", base: "pqrd" }, { cola_max: 77 })).ok, true);
  await recursos.cargar();
  assert.equal(recursos.de({ id: "pqrd", tipo: "codigo", base: "pqrd" }).cola_max, 77);
  await compuerta.cargar();
  assert.equal(compuerta.obtener("pqrd").radicacion_real, false);
  // programación como el job y las marcas del programador
  const p = await programador.ponerProgramacion("pqrd", { activo: true, n: 10 }, () => null);
  assert.equal(p.ok, true);
  await programador.cargarProgramaciones();
  assert.deepEqual([programador.programacionDe("pqrd")?.activo, programador.programacionDe("pqrd")?.n], [true, 10]);
  await estado.programacion.escribirMarcas({ "p:pqrd": 123, "j:pqrd": 456 });
  assert.deepEqual(await estado.programacion.marcas(), { "p:pqrd": 123, "j:pqrd": 456 });
  await programador.olvidar("pqrd");
  await programador.cargarProgramaciones();
  assert.equal(programador.programacionDe("pqrd")?.activo, false);
  // nada de esto en archivos
  for (const a of ["configuracion.json", "historial.jsonl", "perfiles", "recursos.json", "compuertas.json", "programaciones.json", "programador.json", "ia_global.json"])
    assert.equal(existsSync(join(DATOS, a)), false, a);
  assert.equal(await comoOrg("org-b", () => estado.configuracion.ajuste("recursos")), null);
});

test("conversaciones: mensajes, adjuntos (ficha en la base, bytes en el almacén) y borrado", { skip: saltar }, async () => {
  const chats = await import("./chats.ts");
  const c = await chats.crear("pqrd", "Consulta");
  c.mensajes.push({ rol: "usuario", texto: "¿cuántas corridas?", t: Date.now() }, { rol: "asistente", texto: "12", t: Date.now(), pasos: [{ nombre: "ultimas_corridas" }] });
  await chats.guardar(c);
  c.mensajes[1]!.propuestas = [{ accion: "borrar", id: "x", estado: "hecha" }];
  c.mensajes.push({ rol: "usuario", texto: "gracias", t: Date.now(), voz: true });
  await chats.guardar(c);
  const leida = await chats.leer(c.id);
  assert.deepEqual(leida!.mensajes, c.mensajes);
  assert.equal(((await chats.listar()) as unknown as { id: string; n: number }[]).find((x) => x.id === c.id)?.n, 3);
  const adj = await chats.guardarAdjunto(c.id, "cédula de Ana.pdf", "application/pdf", Buffer.from("%PDF-1.4 prueba"));
  const a = await chats.adjunto(c.id, adj.id);
  assert.equal(a?.nombre, "cédula de Ana.pdf");
  assert.equal(readFileSync(a!.ruta, "utf8"), "%PDF-1.4 prueba");
  assert.equal(existsSync(join(DATOS, "chats", `${c.id}.json`)), false);
  assert.equal(existsSync(join(DATOS, "chats", "adjuntos", c.id, `${adj.id}.json`)), false);  // la ficha está en la base
  assert.equal(await comoOrg("org-b", () => chats.adjunto(c.id, adj.id)), null);
  // menos mensajes: los sobrantes se borran
  c.mensajes.splice(1);
  await chats.guardar(c);
  assert.equal((await chats.leer(c.id))!.mensajes.length, 1);
  assert.equal(await chats.borrar(c.id), true);
  assert.equal(await chats.leer(c.id), null);
  assert.equal(existsSync(join(DATOS, "chats", "adjuntos", c.id)), false);
});

test("aprobaciones y aplazadas: llegan por eventos del trabajador, se deciden, se siguen una sola vez", { skip: saltar }, async () => {
  const Fastify = (await import("fastify")).default;
  const flujos = await import("./flujos.ts");
  const aprobaciones = await import("./aprobaciones.ts");
  const aplazadas = await import("./aplazadas.ts");
  const { estadoDelTrabajador } = await import("./motor.ts");
  await estado.flujos.guardarDeclarativo({ definicion: { id: "pagos-ap", nombre: "Pagos", descripcion: "", modelo: "gemini-2.5-flash",
    nodos: [{ id: "inicio", tipo: "entrada_manual", nombre: "Entrada", config: {} }, { id: "ap", tipo: "aprobacion_humana", nombre: "Aprobar", config: { titulo: "Pago" } },
      { id: "fin", tipo: "salida", nombre: "Fin", config: { desenlace: "completar" } }], aristas: [{ desde: "inicio", hacia: "ap" }, { desde: "ap", hacia: "fin" }] }, version: 1, actualizado: Date.now() });
  await flujos.cargar();
  assert.ok(flujos.obtener("pagos-ap"));
  const servidor = Fastify();
  const encoladas: { flujo: string; entrada: unknown; id: string }[] = [];
  const eventos: { tipo: string }[] = [];
  let n = 0;
  const corrida = (flujo: string, origen: string, entrada: unknown) => ({ id: `c-${++n}`, flujo, caso: "x", estado: "en_cola", creada: Date.now(), origen, entrada, eventos: [] }) as never;
  const oyentes: ((e: never) => void)[] = [];
  await aprobaciones.registrar(servidor, { reanudar: (flujo, d) => { const c = corrida(flujo, "aprobacion", d.entrada); encoladas.push({ flujo, entrada: d.entrada, id: (c as { id: string }).id }); return c; }, emitir: (e) => eventos.push(e) });
  await aplazadas.registrar(servidor, { disparar: (flujo, origen, d) => { const c = corrida(flujo, origen, d.entrada); encoladas.push({ flujo, entrada: d.entrada, id: (c as { id: string }).id }); return c; },
    escuchar: (f) => { oyentes.push(f as never); return () => undefined; }, emitir: (e) => eventos.push(e) });

  // el trabajador pausa: manda la aprobación con el estado de la corrida por su canal de eventos
  const aid = "apr-pagos-ap-0123456789ab";
  const registro = { id: aid, flujo: "pagos-ap", corrida: "c-pausa", caso: "x", nodo: "ap", titulo: "Pago a Ana María", detalle: "doc 1023456789", datos: { monto: 1500 },
    campos_editables: ["monto"], creada: Date.now(), vence: null, al_vencer: "rechazar", asignado_a: "", estado: "pendiente", salidas: ["aprobado", "rechazado"] };
  const evento = { tipo: "estado_aprobacion", corrida: "c-pausa", t: Date.now() / 1000, registro, estado: { version: 1, vars: { nombre: "Ana María" }, definicion: {} } };
  assert.equal(estadoDelTrabajador.some((g) => g(evento as never)), true);
  await hasta(async () => (await aprobaciones.leer(aid)) !== null);
  const lista = await servidor.inject({ method: "GET", url: "/api/aprobaciones?estado=pendiente" });
  assert.deepEqual(lista.json().map((a: { id: string }) => a.id), [aid]);
  assert.equal(JSON.stringify(lista.json()).includes("vars"), false);  // el estado de la corrida no sale por la API
  // decide una persona (con un cambio editable): se encola la corrida que sigue
  const r = await servidor.inject({ method: "POST", url: `/api/aprobaciones/${aid}`, headers: { "x-confirmado-por-persona": "si" }, payload: { decision: "aprobar", cambios: { monto: 900 } } });
  assert.equal(r.statusCode, 200, r.body);
  assert.equal(encoladas.length, 1);
  const sigue = encoladas[0]!;
  const extras = (c: unknown) => Object.assign({}, ...flujos.extrasDeOrden.map((x) => x("pagos-ap", c as never) ?? {})) as { reanudar?: Record<string, unknown> };
  const orden = extras({ id: sigue.id, flujo: "pagos-ap", origen: "aprobacion", entrada: sigue.entrada });
  assert.equal((orden.reanudar!.registro as { cambios: unknown }).cambios !== undefined, true);
  assert.deepEqual(orden.reanudar!.estado, evento.estado);
  // otra corrida que llegue a seguirla ya no puede
  assert.equal(extras({ id: "c-otra", flujo: "pagos-ap", origen: "aprobacion", entrada: sigue.entrada }).reanudar!.ya_reanudada, sigue.id);
  // en la base: quién la siguió, cuándo, y el estado de la corrida ya borrado
  await hasta(async () => (await aprobaciones.leer(aid))?.reanudada_en !== undefined);
  assert.equal((await crudo<{ estado_corrida: Buffer | null }>("org-a", "SELECT estado_corrida FROM vera.aprobaciones WHERE id = $1", [aid])).rows[0]!.estado_corrida, null);
  assert.equal((await servidor.inject({ method: "POST", url: `/api/aprobaciones/${aid}`, headers: { "x-confirmado-por-persona": "si" }, payload: { decision: "rechazar" } })).statusCode, 409);
  assert.deepEqual(await comoOrg("org-b", () => estado.aprobaciones.listar()), []);

  // aplazada: llega por eventos, se encola a su hora y se borra al terminar la corrida que volvió
  const apl = { id: "apl-pagos-ap-abcdef012345", flujo: "pagos-ap", corrida: "c-apl", caso: "x", nodo: "ap", entrada: { nombre: "Ana" }, cuando: Date.now() - 1000,
    motivo: "servicio caído", intento: 1, creada: Date.now() - 5000, archivos: [] };
  assert.equal(estadoDelTrabajador.some((g) => g({ tipo: "estado_aplazada", corrida: "c-apl", t: 0, registro: apl } as never)), true);
  await hasta(async () => (await aplazadas.listar("pagos-ap")).length === 1);
  const [encolada] = await aplazadas.revisar({ disparar: (flujo, origen, d) => corrida(flujo, origen, d.entrada), escuchar: () => () => undefined, emitir: () => undefined });
  assert.ok(encolada);
  assert.equal((await aplazadas.listar("pagos-ap"))[0]!.encolada, encolada);
  for (const o of oyentes) o({ tipo: "resultado", corrida: encolada, t: 0 } as never);
  await hasta(async () => (await aplazadas.listar("pagos-ap")).length === 0);
  // nada en disco
  assert.equal(existsSync(join(DATOS, "aprobaciones")), false);
  assert.equal(existsSync(join(DATOS, "aplazadas")), false);
  await servidor.close();
});

// =====================================================================  migración de datos

function escribir(ruta: string, datos: unknown): void {
  mkdirSync(join(ruta, ".."), { recursive: true });
  writeFileSync(ruta, typeof datos === "string" ? datos : JSON.stringify(datos), "utf8");
}

test("migración de datos desde archivos: modo de prueba, completa, idempotente y en su organización", { skip: saltar }, async () => {
  const { migrarDatos } = await import("./bd/migrar_datos.ts");
  const d = mkdtempSync(join(tmpdir(), "vera-bd-origen-"));
  const ahora = Date.parse("2026-11-10T15:00:00Z");
  const defi = { id: "consultas", nombre: "Consultas", descripcion: "", modelo: "gemini-2.5-flash", nodos: [{ id: "inicio", tipo: "webhook", nombre: "Entrada", config: { secreto: "s3cr3t0" } },
    { id: "fin", tipo: "salida", nombre: "Fin", config: {} }], aristas: [{ desde: "inicio", hacia: "fin" }] };
  escribir(join(d, "configuracion.json"), { concurrencia: 3, topeSegundos: 300 });
  escribir(join(d, "ia_global.json"), { proveedor: "vertex", modelo: "gemini-2.5-flash" });
  escribir(join(d, "recursos.json"), { consultas: { cola_max: 9 } });
  escribir(join(d, "compuertas.json"), { pqrd: { radicacion_real: false } });
  escribir(join(d, "historial.jsonl"), `${JSON.stringify({ t: ahora, clave: "concurrencia", antes: 2, despues: 3 })}\n${JSON.stringify({ t: ahora + 1, clave: "topeSegundos", antes: 600, despues: 300, nota: "x" })}\n{dañado\n`);
  escribir(join(d, "perfiles", "Base.json"), { nombre: "Base", descripcion: "", t: ahora, configuracion: { concurrencia: 2 } });
  escribir(join(d, "flujos", "consultas.json"), { definicion: { ...defi, nombre: "Consultas v2" }, version: 2, actualizado: ahora });
  escribir(join(d, "flujos", "versiones", "consultas.v1.json"), { definicion: defi, version: 1, actualizado: ahora - 1000 });
  escribir(join(d, "flujos", "versiones", "borrado.v1.json"), { definicion: { ...defi, id: "borrado" }, version: 1, actualizado: 1 });
  escribir(join(d, "flujos_codigo", "tutelas-sur.json"), { id: "tutelas-sur", base: "tutelas", nombre: "Tutelas sur", descripcion: "", copia_de: "tutelas", creado: ahora, actualizado: ahora });
  escribir(join(d, "flujos_codigo", "_originales.json"), { pqrd: { nombre: "Radicación PQRD (Positiva)" } });
  escribir(join(d, "flujos_codigo", "tutelas-sur.estructura.v1.json"), { definicion: { nodos: [], aristas: [] }, version: 1, actualizado: ahora, tramos: [], intercambio: null, apagadas: [] });
  escribir(join(d, "flujos_codigo", "tutelas-sur.estructura.json"), { definicion: { nodos: [{ id: "b", tipo: "y" }], aristas: [] }, version: 2, actualizado: ahora, tramos: [], intercambio: null, apagadas: ["b"] });
  escribir(join(d, "flujos_historial", "consultas.json"), { versiones: { 1: { autor: "persona", fecha: ahora }, 2: { autor: "vera", fecha: ahora, pedido: "ponle v2" } } });
  escribir(join(d, "programaciones.json"), { pqrd: { cron: "*/30 * * * *", zona: "America/Bogota", activo: false, n: 50, cambiado: ahora } });
  escribir(join(d, "programador.json"), { "p:consultas": ahora });
  escribir(join(d, "corridas", "2026-11-10.jsonl"), [
    { id: "20261110150000-111111", caso: "viejo", estado: "terminada", creada: ahora, configuracion: {}, eventos: [{ tipo: "resultado", t: ahora / 1000, accion: "radicado" }], resultado: { tipo: "resultado", t: ahora / 1000, accion: "radicado" } },
    { id: "20261110150500-222222", flujo: "consultas", caso: "manual-1", estado: "terminada", creada: ahora + 300_000, configuracion: {}, entrada: { x: 1 }, eventos: [] },
  ].map((x) => JSON.stringify(x)).join("\n") + "\n");
  escribir(join(d, "aprobaciones", "apr-consultas-aaaaaaaaaaaa.json"), { id: "apr-consultas-aaaaaaaaaaaa", flujo: "consultas", corrida: "c1", titulo: "T", detalle: "", datos: {}, campos_editables: [], creada: ahora, estado: "pendiente" });
  escribir(join(d, "aprobaciones", "apr-consultas-aaaaaaaaaaaa.estado.json"), { version: 1, vars: { a: 1 } });
  escribir(join(d, "aprobaciones", "apr-consultas-bbbbbbbbbbbb.json"), { id: "apr-consultas-bbbbbbbbbbbb", flujo: "consultas", corrida: "c2", titulo: "T2", detalle: "", datos: {}, campos_editables: [], creada: ahora, estado: "aprobado", resuelta: ahora, reanudada: "c3" });
  escribir(join(d, "aprobaciones", "apr-consultas-bbbbbbbbbbbb.estado.json"), { version: 1 });
  escribir(join(d, "aprobaciones", "apr-consultas-bbbbbbbbbbbb.reanudada"), "c3");
  escribir(join(d, "aplazadas", "apl-consultas-cccccccccccc.json"), { id: "apl-consultas-cccccccccccc", flujo: "consultas", corrida: "c4", entrada: { y: 2 }, cuando: ahora + 60_000, intento: 1, motivo: "m", creada: ahora });
  // una que la base rechaza (intento negativo): se omite con su motivo y lo demás sigue en la misma transacción
  escribir(join(d, "aplazadas", "apl-consultas-dddddddddddd.json"), { id: "apl-consultas-dddddddddddd", flujo: "consultas", corrida: "c5", cuando: ahora, intento: -1, motivo: "m", creada: ahora });
  escribir(join(d, "chats", "chat-0000000001.json"), { id: "chat-0000000001", titulo: "Hola", flujo: "consultas", creada: ahora, actualizada: ahora, mensajes: [{ rol: "usuario", texto: "hola", t: ahora }] });
  escribir(join(d, "chats", "adjuntos", "chat-0000000001", "adj0000001.json"), { id: "adj0000001", nombre: "foto.png", tipo: "image/png", tamano: 3, extension: ".png" });

  const esperado = { configuracion: 1, ia: 1, recursos: 1, compuertas: 1, historial_configuracion: 2, perfiles: 1, flujos_copias: 1, flujos_originales: 1, flujos_declarativos: 1,
    flujos_versiones: 1, estructuras: 2, historial_versiones: 1, programaciones: 1, programador: 1, corridas: 2, aprobaciones: 2, aplazadas: 1, conversaciones: 1, adjuntos_conversacion: 1 };
  const escritos = (i: Awaited<ReturnType<typeof migrarDatos>>) => Object.fromEntries(Object.entries(i.tipos).map(([k, v]) => [k, v.escritos]));

  // 1. de prueba: cuenta todo y no deja nada
  const prueba = await migrarDatos({ slug: "org-c", datos: d, prueba: true });
  assert.deepEqual(escritos(prueba), esperado);
  assert.equal(prueba.tipos.historial_configuracion!.omitidos, 1);  // el renglón dañado
  assert.equal(prueba.tipos.flujos_versiones!.omitidos, 1);  // la versión de un flujo que ya no existe
  assert.equal(prueba.tipos.aplazadas!.omitidos, 1);
  assert.match(prueba.tipos.aplazadas!.errores[0]!, /apl-consultas-dddddddddddd.*check/);
  assert.equal(Number((await crudo<{ n: string }>("org-c", "SELECT count(*) AS n FROM vera.flujos")).rows[0]!.n), 0);
  assert.equal(Number((await crudo<{ n: string }>("org-c", "SELECT count(*) AS n FROM vera.corridas")).rows[0]!.n), 0);
  // 2. de verdad
  assert.deepEqual(escritos(await migrarDatos({ slug: "org-c", datos: d })), esperado);
  // 3. otra vez: nada nuevo, todo «ya estaba»
  const otra = await migrarDatos({ slug: "org-c", datos: d });
  assert.ok(Object.values(otra.tipos).every((c) => c.escritos === 0), JSON.stringify(otra.tipos));
  assert.equal(otra.tipos.corridas!.ya_estaban, 2);
  // lo migrado se lee con la capa de repositorio, en su organización y en ninguna otra
  await comoOrg("org-c", async () => {
    const f = await estado.flujos.cargar();
    assert.deepEqual(f.declarativos.map((x) => [x.version, (x.definicion as typeof defi).nodos[0]!.config.secreto]), [[2, "s3cr3t0"]]);
    assert.deepEqual(f.copias.map((x) => x.id), ["tutelas-sur"]);
    assert.equal(f.originales.pqrd?.nombre, "Radicación PQRD (Positiva)");
    assert.deepEqual((await estado.flujos.versiones("consultas")).map((v) => v.version), [1, 2]);
    assert.deepEqual((await estado.estructuras.cargar())["tutelas-sur"]?.apagadas, ["b"]);
    assert.equal((await estado.metas.leer("consultas"))["2"]?.pedido, "ponle v2");
    assert.deepEqual((await estado.corridas.cargar("pqrd")).map((c) => [c.id, c.resultado?.accion]), [["20261110150000-111111", "radicado"]]);
    assert.deepEqual((await estado.corridas.cargar("consultas"))[0]?.entrada, { x: 1 });
    const ap = await estado.aprobaciones.listar();
    assert.deepEqual(ap.map((a) => [a.id, a.estado, a.reanudada_en !== undefined]).sort(), [["apr-consultas-aaaaaaaaaaaa", "pendiente", false], ["apr-consultas-bbbbbbbbbbbb", "aprobado", true]]);
    assert.deepEqual(await estado.aprobaciones.leerEstado("apr-consultas-aaaaaaaaaaaa"), { version: 1, vars: { a: 1 } });
    assert.equal(await estado.aprobaciones.leerEstado("apr-consultas-bbbbbbbbbbbb"), null);  // ya se siguió: el estado no se guarda
    assert.deepEqual((await estado.aplazadas.listar()).map((a) => a.entrada), [{ y: 2 }]);
    assert.deepEqual((await estado.chats.leer("chat-0000000001"))?.mensajes.map((m) => m.texto), ["hola"]);
    assert.equal((await estado.chats.adjunto("chat-0000000001", "adj0000001"))?.nombre, "foto.png");
    assert.equal((await estado.configuracion.cambios()).length, 2);
    assert.deepEqual(await estado.programacion.marcas(), { "p:consultas": ahora });
    assert.equal((await estado.programacion.leer()).pqrd?.n, 50);
  });
  assert.equal((await comoOrg("org-b", () => estado.corridas.cargar("consultas"))).length, 0);
  // los archivos no se tocan
  assert.ok(existsSync(join(d, "corridas", "2026-11-10.jsonl")) && existsSync(join(d, "aprobaciones", "apr-consultas-aaaaaaaaaaaa.estado.json")));
  await assert.rejects(migrarDatos({ slug: "no-existe", datos: d }), /no existe/);
});

// =====================================================================  el entorno de los procesos hijos (al final: cambia process.env)

test("la URL de la base y la llave maestra no llegan a ningún proceso hijo", { skip: saltar }, async () => {
  const { spawnSync } = await import("node:child_process");
  bd.retirarDelEntorno();
  assert.equal(process.env.DATABASE_URL, undefined);
  assert.equal(process.env.VERA_KEK_LOCAL, undefined);
  assert.equal(bd.activa(), true);  // el servidor las sigue teniendo
  assert.ok((await estado.chats.listar()).length >= 1);
  const hijo = spawnSync(process.execPath, ["-e", "console.log(JSON.stringify(Object.keys(process.env).filter((k) => /DATABASE|KEK|BD_URL/.test(k))))"], { encoding: "utf8" });
  assert.deepEqual(JSON.parse(hijo.stdout), []);
  assert.deepEqual(Object.keys(bd.sinVariablesDelServidor({ DATABASE_URL: "x", VERA_KEK_LOCAL: "y", OTRA: "z" })), ["OTRA"]);
});
