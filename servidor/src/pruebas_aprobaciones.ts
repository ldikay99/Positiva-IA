/**
 * Pruebas de la aprobación humana y de las corridas aplazadas del lado del servidor: el contrato de
 * /api/aprobaciones (lista, decisión solo de una persona, cambios editables, una sola vez), el vencimiento,
 * los eventos del bus, la orden que sigue la corrida y que todo sale del disco (sobrevive a reinicios). Sin red.
 *
 *   node --test servidor/dist/pruebas_aprobaciones.js      (en el contenedor: /app/servidor/dist)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Fastify from "fastify";

process.env.RUTA_DATOS = mkdtempSync(join(tmpdir(), "positiva-aprob-"));
process.env.RUTA_ARCHIVOS = mkdtempSync(join(tmpdir(), "positiva-aprob-archivos-"));
process.env.APROBACIONES_REVISION_MS = "3600000";  // las revisiones se llaman a mano
process.env.APLAZADAS_REVISION_MS = "3600000";

const flujos = await import("./flujos.ts");
const aprobaciones = await import("./aprobaciones.ts");
const aplazadas = await import("./aplazadas.ts");
type Evento = { tipo: string; corrida: string | null; t: number; [k: string]: unknown };

const DATOS = process.env.RUTA_DATOS!;
const eventos: Evento[] = [];
const encoladas: { flujo: string; origen: string; entrada: unknown; caso?: string; id: string }[] = [];
let siguiente = 1;
const oyentes: ((e: Evento) => void)[] = [];

function disparar(flujo: string, origen: string, datos: { entrada?: unknown; caso?: string }) {
  const id = `c-${siguiente++}`;
  encoladas.push({ flujo, origen, entrada: datos.entrada, ...(datos.caso ? { caso: datos.caso } : {}), id });
  return { id, flujo, caso: datos.caso ?? `${origen}-x`, estado: "en_cola", creada: Date.now(), origen, entrada: datos.entrada, eventos: [] } as never;
}

const app = Fastify();
await flujos.cargar();
const d = {
  id: "pagos", nombre: "Pagos", modelo: "gemini-2.5-flash",
  nodos: [
    { id: "inicio", tipo: "entrada_manual", nombre: "Inicio", config: { campos: [{ nombre: "monto", tipo: "numero" }] } },
    { id: "ap", tipo: "aprobacion_humana", nombre: "¿Se paga?", config: { titulo: "Pago de {{entrada.monto}}", datos: { monto: "{{entrada.monto}}" }, campos_editables: ["monto"], tiempo_limite_h: 1 } },
    { id: "si", tipo: "salida", nombre: "Pagar", config: { desenlace: "radicar" } },
    { id: "no", tipo: "salida", nombre: "No pagar", config: { desenlace: "descartar" } },
  ],
  aristas: [{ desde: "inicio", hacia: "ap" }, { desde: "ap", hacia: "si", cuando: "aprobado" }, { desde: "ap", hacia: "no", cuando: "rechazado" }],
};
const g = await flujos.guardar(d, "prueba");
assert.equal(g.ok, true, g.problemas.join("\n"));
await aprobaciones.registrar(app, { reanudar: (id, datos) => disparar(id, "aprobacion", datos), emitir: (e) => void eventos.push(e as Evento) });
await aplazadas.registrar(app, {
  disparar, emitir: (e) => void eventos.push(e as Evento),
  escuchar: (f) => { oyentes.push(f as (e: Evento) => void); return () => undefined; },
});
await app.ready();

/** Lo que deja el motor al pausar (puente/nodos_producto.py). */
function pendiente(id: string, extra: Record<string, unknown> = {}) {
  mkdirSync(join(DATOS, "aprobaciones"), { recursive: true });
  const a = { id, flujo: "pagos", corrida: "corrida-1", caso: "manual-1", nodo: "ap", titulo: "Pago de 1500", detalle: "Factura 7", datos: { monto: 1500, proveedor: "ACME" },
    campos_editables: ["monto"], creada: Date.now(), vence: Date.now() + 3_600_000, al_vencer: "rechazar", estado: "pendiente", salidas: ["aprobado", "rechazado"], ...extra };
  writeFileSync(join(DATOS, "aprobaciones", `${id}.json`), JSON.stringify(a));
  writeFileSync(join(DATOS, "aprobaciones", `${id}.estado.json`), JSON.stringify({ vars: { secreto_interno: "no sale" } }));
  return a;
}

test("lista el contrato: pendientes primero y nunca el estado de la corrida", async () => {
  pendiente("apr-pagos-000000000001");
  pendiente("apr-pagos-000000000002", { estado: "aprobado", resuelta: Date.now() });
  const r = await app.inject({ method: "GET", url: "/api/aprobaciones?flujo=pagos" });
  assert.equal(r.statusCode, 200);
  const lista = r.json() as Record<string, unknown>[];
  assert.deepEqual(lista.map((x) => [x.id, x.estado]), [["apr-pagos-000000000001", "pendiente"], ["apr-pagos-000000000002", "aprobado"]]);
  for (const k of ["id", "flujo", "corrida", "titulo", "detalle", "datos", "campos_editables", "creada", "vence", "estado"]) assert.ok(k in lista[0]!, k);
  assert.ok(!JSON.stringify(lista).includes("no sale"), "el estado guardado de la corrida no sale por la API");
  const solo = (await app.inject({ method: "GET", url: "/api/aprobaciones?estado=pendiente" })).json() as unknown[];
  assert.equal(solo.length, 1);
  assert.equal((await app.inject({ method: "GET", url: "/api/aprobaciones?estado=raro" })).statusCode, 400);
  assert.equal((await app.inject({ method: "GET", url: "/api/aprobaciones/apr-pagos-000000000001" })).json().titulo, "Pago de 1500");
  assert.equal((await app.inject({ method: "GET", url: "/api/aprobaciones/apr-pagos-999999999999" })).statusCode, 404);
});

test("decide solo una persona, una sola vez, y la corrida sigue con la decisión", async () => {
  const id = "apr-pagos-000000000003";
  pendiente(id);
  const url = `/api/aprobaciones/${id}`;
  // sin la confirmación de una persona (lo que haría Vera): no
  let r = await app.inject({ method: "POST", url, payload: { decision: "aprobar" } });
  assert.equal(r.statusCode, 403);
  assert.match(r.json().error, /solo una persona/);
  const persona = { "x-confirmado-por-persona": "si" };
  r = await app.inject({ method: "POST", url, headers: persona, payload: { decision: "quizas" } });
  assert.equal(r.statusCode, 400);
  r = await app.inject({ method: "POST", url, headers: persona, payload: { decision: "aprobar", cambios: { proveedor: "OTRO" } } });
  assert.equal(r.statusCode, 400, "solo se cambia lo editable");
  const antes = encoladas.length;
  r = await app.inject({ method: "POST", url, headers: persona, payload: { decision: "aprobar", comentario: "ok tesorería", cambios: { monto: 900 } } });
  assert.equal(r.statusCode, 200, r.body);
  const cuerpo = r.json() as { ok: boolean; aprobacion: Record<string, unknown>; corrida: string };
  assert.equal(cuerpo.aprobacion.estado, "aprobado");
  assert.deepEqual(cuerpo.aprobacion.cambios, { monto: 900 });
  // se encoló la corrida que sigue (origen «aprobacion»), y su orden le dice al motor qué aprobación seguir
  const c = encoladas[antes]!;
  assert.deepEqual([c.flujo, c.origen, c.entrada], ["pagos", "aprobacion", { aprobacion: id }]);
  assert.equal(cuerpo.corrida, c.id);
  const orden = flujos.ordenPara("pagos", { id: c.id, flujo: "pagos", caso: "x", estado: "en_cola", creada: 0, origen: "aprobacion", entrada: c.entrada, eventos: [], configuracion: {} as never });
  assert.deepEqual(orden.reanudar, { aprobacion: id });
  const ev = eventos.find((e) => e.tipo === "aprobacion_resuelta" && e.id === id)!;
  assert.equal((ev.aprobacion as { estado: string }).estado, "aprobado");
  assert.equal(ev.flujo, "pagos");
  // en disco (sobrevive a reinicios) y una sola vez
  assert.equal(JSON.parse(readFileSync(join(DATOS, "aprobaciones", `${id}.json`), "utf8")).decision, "aprobar");
  r = await app.inject({ method: "POST", url, headers: persona, payload: { decision: "rechazar" } });
  assert.equal(r.statusCode, 409);
  assert.equal((await app.inject({ method: "POST", url: "/api/aprobaciones/apr-pagos-999999999999", headers: persona, payload: { decision: "aprobar" } })).statusCode, 404);
});

test("vence sola y vuelve a encolar lo que nunca siguió (reinicio)", async () => {
  const id = "apr-pagos-000000000004";
  pendiente(id, { vence: Date.now() - 1000 });
  const antes = encoladas.length;
  await aprobaciones.revisar();
  const a = JSON.parse(readFileSync(join(DATOS, "aprobaciones", `${id}.json`), "utf8"));
  assert.deepEqual([a.estado, a.por], ["vencido", "vencimiento"]);
  assert.ok(encoladas.slice(antes).some((x) => (x.entrada as { aprobacion?: string }).aprobacion === id));
  // una resuelta cuyo motor nunca la siguió (sin el archivo .reanudada) se vuelve a encolar tras el tiempo de reintento
  const otra = "apr-pagos-000000000005";
  pendiente(otra, { estado: "rechazado", resuelta: Date.now() });
  const n = encoladas.length;
  await aprobaciones.revisar(Date.now() + 11 * 60_000);
  assert.ok(encoladas.slice(n).some((x) => (x.entrada as { aprobacion?: string }).aprobacion === otra));
  // si el motor ya la siguió, no
  writeFileSync(join(DATOS, "aprobaciones", `${otra}.reanudada`), "c-x");
  const m = encoladas.length;
  await aprobaciones.revisar(Date.now() + 30 * 60_000);
  assert.ok(!encoladas.slice(m).some((x) => (x.entrada as { aprobacion?: string }).aprobacion === otra));
});

test("aplazadas: vuelven a su hora con su intento y se borran al terminar", async () => {
  mkdirSync(join(DATOS, "aplazadas"), { recursive: true });
  const id = "apl-pagos-00000000000a";
  const archivo = join(DATOS, "aplazadas", `${id}.json`);
  writeFileSync(archivo, JSON.stringify({ id, flujo: "pagos", corrida: "corrida-9", caso: "manual-9", nodo: "r", entrada: { monto: 7 }, cuando: Date.now() + 60_000, intento: 2, motivo: "servicio caído", creada: Date.now() }));
  const deps = { disparar, emitir: (e: Evento) => void eventos.push(e), escuchar: () => () => undefined };
  assert.deepEqual(await aplazadas.revisar(deps as never), [], "todavía no es la hora");
  const lista = (await app.inject({ method: "GET", url: "/api/aplazadas?flujo=pagos" })).json() as Record<string, unknown>[];
  assert.equal(lista[0]!.id, id);
  assert.ok(!("entrada" in lista[0]!), "la entrada no sale en la lista");
  const [c] = await aplazadas.revisar(deps as never, Date.now() + 61_000);
  assert.ok(c);
  const e = encoladas.find((x) => x.id === c)!;
  assert.deepEqual([e.origen, e.entrada], ["aplazada", { monto: 7 }]);
  const orden = flujos.ordenPara("pagos", { id: c, flujo: "pagos", caso: "x", estado: "en_cola", creada: 0, origen: "aplazada", entrada: e.entrada, eventos: [], configuracion: {} as never });
  assert.deepEqual(orden.aplazamiento, { id, intento: 2, motivo: "servicio caído", desde: "corrida-9" });
  assert.deepEqual(await aplazadas.revisar(deps as never, Date.now() + 62_000), [], "no se encola dos veces");
  for (const f of oyentes) f({ tipo: "resultado", corrida: c, t: 0, accion: "manual" });
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(existsSync(archivo), false, "al terminar la corrida que volvió, se borra");
});
