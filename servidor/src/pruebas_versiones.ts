/**
 * Pruebas del historial de versiones (versiones.ts y rutas_versiones.ts): autor de cada versión (persona o Vera
 * por sus encabezados), el punto «antes de: <pedido>» cuando Vera cambia algo, el resumen y la comparación por
 * paso, conexión y config, restaurar, deshacer (un cambio o todo un pedido) y rehacer sin borrar nunca la
 * historia, la versión que funcionaba según las corridas, y lo mismo para la estructura de una copia de PQRD
 * (con la del código como versión 0 y sin pisar números al volver a ella). Sin red.
 *
 *   node --test servidor/src/pruebas_versiones.ts      (en el contenedor: /app/servidor/src)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Fastify from "fastify";

process.env.RUTA_DATOS = mkdtempSync(join(tmpdir(), "positiva-versiones-"));
process.env.RUTA_ARCHIVOS = mkdtempSync(join(tmpdir(), "positiva-versiones-archivos-"));
process.env.RUTA_BANDEJA = mkdtempSync(join(tmpdir(), "positiva-versiones-bandeja-"));
process.env.RUTA_VIVO = mkdtempSync(join(tmpdir(), "positiva-versiones-vivo-"));
delete process.env.CLAVE_CREDENCIALES;

const credenciales = await import("./credenciales.ts");
const flujos = await import("./flujos.ts");
const estructuras = await import("./estructuras.ts");
const versiones = await import("./versiones.ts");
const rutas = await import("./rutas_versiones.ts");
await credenciales.cargar();
await flujos.cargar();

const avisos: { id: string; extra: Record<string, unknown> }[] = [];
const app = Fastify();
// el mismo gancho del servidor: quién hace el pedido llega hasta el guardado
app.addHook("preHandler", (req, _res, listo) => versiones.contexto.run(versiones.contextoDe(req.headers), listo));
await rutas.registrar(app, { avisar: (id, extra) => avisos.push({ id, extra }) });
app.put<{ Params: { id: string } }>("/api/flujos/:id", async (req, res) => {
  const r = await flujos.guardar({ ...((req.body as { definicion: object }).definicion), id: req.params.id });
  return r.ok ? { flujo: r.flujo } : res.code(400).send({ problemas: r.problemas });
});
app.put<{ Params: { id: string } }>("/api/flujos/:id/estructura", async (req, res) => {
  const r = await estructuras.guardar(req.params.id, flujos.baseDe(req.params.id)!, (req.body as { definicion: unknown }).definicion);
  return r.ok ? { version: r.guardada!.version } : res.code(400).send({ problemas: r.problemas });
});
app.delete<{ Params: { id: string } }>("/api/flujos/:id/estructura", async (req) => estructuras.restablecerConVersion(req.params.id, flujos.baseDe(req.params.id)!));

const VERA = (pedido: string, id = pedido) => ({ "x-autor": "vera", "x-pedido": encodeURIComponent(pedido), "x-pedido-id": encodeURIComponent(id) });
const esperar = (ms: number) => new Promise((r) => setTimeout(r, ms));

const entrada = { id: "inicio", tipo: "entrada_manual", nombre: "Inicio", config: { campos: [{ nombre: "texto", tipo: "texto" }] } };
const fin = { id: "fin", tipo: "salida", nombre: "Fin", config: { desenlace: "completar" } };
const limpiar = { id: "limpiar", tipo: "transformar", nombre: "Limpiar", config: { operaciones: [{ op: "plantilla", plantilla: "{{entrada.texto}}", salida: "limpio" }] } };
const V1 = { id: "ver-prueba", nombre: "Versiones de prueba", modelo: "gemini-2.5-flash", nodos: [entrada, fin], aristas: [{ desde: "inicio", hacia: "fin" }] };
const V2 = { ...V1, nodos: [entrada, limpiar, fin], aristas: [{ desde: "inicio", hacia: "limpiar" }, { desde: "limpiar", hacia: "fin" }] };
const urgente = { id: "urgente", tipo: "condicion", nombre: "¿Urgente?", config: { campo: "limpio", operador: "contiene", valor: "urgente" } };
const aMano = { id: "a_mano", tipo: "salida", nombre: "Revisión", config: { desenlace: "manual", motivo: "URGENTE" } };
const V3 = { ...V2, nodos: [entrada, limpiar, urgente, aMano, fin], aristas: [{ desde: "inicio", hacia: "limpiar" }, { desde: "limpiar", hacia: "urgente" }, { desde: "urgente", hacia: "a_mano", cuando: "si" }, { desde: "urgente", hacia: "fin" }] };
const V4 = { ...V3, nodos: V3.nodos.map((n) => (n.id === "urgente" ? { ...urgente, config: { ...urgente.config, valor: "prioritario" } } : n)) };

const guardar = async (def: object, headers: Record<string, string> = {}) => {
  const r = await app.inject({ method: "PUT", url: "/api/flujos/ver-prueba", payload: { definicion: def }, headers });
  assert.equal(r.statusCode, 200, r.body);
  await esperar(5);
  return r.json().flujo.version as number;
};
const lista = async (id = "ver-prueba") => (await app.inject({ method: "GET", url: `/api/flujos/${id}/versiones` })).json() as { version: number; autor: string | null; punto?: string; resumen?: string; actual: boolean; por?: string; restaurada_de?: number; fecha: number; del_codigo?: boolean; corridas: { bien: number } }[];
const nodos = () => flujos.obtener("ver-prueba")!.definicion!.nodos.map((n) => `${n.id}:${JSON.stringify(n.config)}`).sort();

test("cada versión dice quién la hizo; un cambio de Vera deja el punto «antes de: …» y un resumen en palabras", async () => {
  assert.equal(await guardar(V1), 1);
  assert.equal(await guardar(V2), 2);
  assert.equal(await guardar(V3, VERA("agrega una revisión de lo urgente", "c1:3")), 3);
  assert.equal(await guardar(V4, VERA("agrega una revisión de lo urgente", "c1:3")), 4);
  const l = await lista();
  assert.deepEqual(l.map((v) => v.version), [4, 3, 2, 1]);
  const de = (n: number) => l.find((v) => v.version === n)!;
  assert.equal(de(1).autor, "persona");
  assert.equal(de(2).autor, "persona");
  assert.equal(de(3).autor, "vera");
  assert.equal(de(4).actual, true);
  // el punto va en la versión de antes del pedido (la 2), una sola vez aunque Vera guarde dos veces
  assert.equal(de(2).punto, "antes de: agrega una revisión de lo urgente");
  assert.equal(de(3).punto, undefined);
  assert.match(de(2).resumen!, /Agregó «Limpiar»/);
  assert.match(de(3).resumen!, /agregó «¿Urgente\?», «Revisión»/i);
  assert.match(de(4).resumen!, /^Cambió «¿Urgente\?» \(valor\)$/);
  // un pedido nuevo de Vera: la 4 pasa a ser su punto
  assert.equal(await guardar(V4, VERA("cambia el nombre del flujo", "c1:5")), 5);
  assert.equal((await lista()).find((v) => v.version === 4)!.punto, "antes de: cambia el nombre del flujo");
});

test("comparar dos versiones: pasos agregados y quitados, config campo por campo y conexiones", async () => {
  const c = (await app.inject({ method: "GET", url: "/api/flujos/ver-prueba/versiones/comparar?a=2&b=4" })).json();
  assert.deepEqual(c.pasos.agregados.map((p: { id: string }) => p.id).sort(), ["a_mano", "urgente"]);
  assert.deepEqual(c.pasos.quitados, []);
  assert.ok(c.conexiones.agregadas.includes("urgente → a_mano (si)"), JSON.stringify(c.conexiones));
  assert.ok(c.conexiones.quitadas.includes("limpiar → fin"));
  const c2 = (await app.inject({ method: "GET", url: "/api/flujos/ver-prueba/versiones/comparar?a=3&b=4" })).json();
  assert.deepEqual(c2.pasos.cambiados, [{ id: "urgente", tipo: "condicion", nombre: "¿Urgente?", cambios: [{ campo: "valor", antes: "urgente", despues: "prioritario" }] }]);
  // sin a: contra la anterior a b; sin b: la actual
  const c3 = (await app.inject({ method: "GET", url: "/api/flujos/ver-prueba/versiones/comparar" })).json();
  assert.equal(c3.b, 5);
  assert.equal(c3.a, 4);
  assert.equal(c3.iguales, true);
  assert.equal((await app.inject({ method: "GET", url: "/api/flujos/ver-prueba/versiones/comparar?a=99" })).statusCode, 404);
});

test("deshacer todo el pedido, rehacer, deshacer de a uno y restaurar: siempre una versión nueva, nada se borra", async () => {
  // estado: 5 (Vera, «cambia el nombre…», igual a la 4). Deshacer el pedido entero vuelve a la 4.
  let r = (await app.inject({ method: "POST", url: "/api/flujos/ver-prueba/deshacer", payload: { pedido: true } })).json();
  assert.equal(r.confirmado, true);
  assert.equal(r.version, 6);
  assert.equal(r.restaurada, 4);
  // un pedido de Vera con dos guardados (la 7 y la 8): deshacerlo entero vuelve a la 6, no a la 7
  await guardar(V2, VERA("quita lo urgente", "c1:7"));
  await guardar(V1, VERA("quita lo urgente", "c1:7"));
  r = (await app.inject({ method: "POST", url: "/api/flujos/ver-prueba/deshacer", payload: { pedido: true } })).json();
  assert.equal(r.version, 9);
  assert.equal(r.restaurada, 6);
  assert.deepEqual(nodos(), [...V4.nodos].map((n) => `${n.id}:${JSON.stringify(n.config)}`).sort(), "el flujo quedó como estaba antes del pedido");
  // rehacer vuelve a lo deshecho (la 8, igual a V1)
  r = (await app.inject({ method: "POST", url: "/api/flujos/ver-prueba/rehacer" })).json();
  assert.equal(r.version, 10);
  assert.equal(r.restaurada, 8);
  assert.equal(flujos.obtener("ver-prueba")!.definicion!.nodos.length, 2);
  // deshacer de a uno desde un rehacer: 10 (=8) → 7, y otra vez → 6
  r = (await app.inject({ method: "POST", url: "/api/flujos/ver-prueba/deshacer" })).json();
  assert.equal(r.restaurada, 7);
  r = (await app.inject({ method: "POST", url: "/api/flujos/ver-prueba/deshacer" })).json();
  assert.equal(r.restaurada, 6);
  // volver a la 1 explícitamente
  r = (await app.inject({ method: "POST", url: "/api/flujos/ver-prueba/restaurar", payload: { version: 1 } })).json();
  assert.equal(r.confirmado, true);
  assert.equal(r.restaurada, 1);
  const l = await lista();
  assert.deepEqual(l.map((v) => v.version), [13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1], "el historial completo sigue ahí");
  assert.equal(l[0]!.por, "restaurar");
  assert.equal(l[0]!.restaurada_de, 1);
  assert.equal(l.find((v) => v.version === 9)!.por, "deshacer");
  // sin nada que rehacer (lo último fue restaurar) y sin versión que no existe
  assert.equal((await app.inject({ method: "POST", url: "/api/flujos/ver-prueba/rehacer" })).statusCode, 409);
  assert.equal((await app.inject({ method: "POST", url: "/api/flujos/ver-prueba/restaurar", payload: { version: 77 } })).statusCode, 404);
  assert.ok(avisos.some((a) => a.id === "ver-prueba" && a.extra.version === 13), "la web se entera de cada cambio");
});

test("etiquetas y la versión que funcionaba según las corridas", async () => {
  const p = await app.inject({ method: "PATCH", url: "/api/flujos/ver-prueba/versiones/4", payload: { etiqueta: "La buena de producción" } });
  assert.equal(p.json().etiqueta, "La buena de producción");
  const l = await lista();
  const f4 = l.find((v) => v.version === 4)!.fecha, f5 = l.find((v) => v.version === 5)!.fecha;
  versiones.enlace.corridasDe = () => [{ creada: Math.round((f4 + f5) / 2), estado: "bien" }, { creada: Date.now() + 1, estado: "fallo" }];
  const r = (await app.inject({ method: "GET", url: "/api/flujos/ver-prueba/versiones/funcionaba" })).json();
  assert.equal(r.version, 4);
  assert.equal(r.corridas_bien, 1);
  assert.equal((await lista()).find((v) => v.version === 4)!.corridas.bien, 1);
  versiones.enlace.corridasDe = () => [];
});

test("la estructura de una copia de PQRD tiene versiones: la del código es la 0 y volver a ella no pisa números", async () => {
  assert.equal((await flujos.clonar("pqrd", "PQRD versiones", "pqrd-versiones")).ok, true);
  let l = await lista("pqrd-versiones");
  assert.deepEqual(l.map((v) => [v.version, v.actual, Boolean(v.del_codigo)]), [[0, true, true]]);
  const d = (await estructuras.vista("pqrd-versiones", "pqrd")).definicion;
  d.nodos.find((n) => n.id === "revisor")!.config = { deshabilitado: true };
  const g = await app.inject({ method: "PUT", url: "/api/flujos/pqrd-versiones/estructura", payload: { definicion: d }, headers: VERA("apaga el revisor") });
  assert.equal(g.json().version, 1, g.body);
  l = await lista("pqrd-versiones");
  assert.equal(l.find((v) => v.version === 0)!.punto, "antes de: apaga el revisor");
  assert.equal(l.find((v) => v.version === 1)!.autor, "vera");
  assert.match(l.find((v) => v.version === 1)!.resumen!, /Apagó «/);
  // volver a la del código es la versión 2; guardar otra vez es la 3 (la 1 sigue en disco)
  assert.equal((await app.inject({ method: "DELETE", url: "/api/flujos/pqrd-versiones/estructura" })).json().version, 2);
  assert.equal(flujos.obtener("pqrd-versiones")!.editada, false);
  assert.equal((await app.inject({ method: "PUT", url: "/api/flujos/pqrd-versiones/estructura", payload: { definicion: d } })).json().version, 3);
  // deshacer la 3 vuelve a la del código (2) como versión 4
  const r = (await app.inject({ method: "POST", url: "/api/flujos/pqrd-versiones/deshacer" })).json();
  assert.equal(r.confirmado, true);
  assert.equal(r.restaurada, 2);
  assert.equal(r.version, 4);
  assert.equal(estructuras.obtener("pqrd-versiones"), undefined, "quedó la del código");
  l = await lista("pqrd-versiones");
  assert.deepEqual(l.map((v) => v.version), [4, 3, 2, 1, 0]);
  assert.equal(l[0]!.actual, true);
  // comparar la del código con la 1 y volver a la 1
  const c = (await app.inject({ method: "GET", url: "/api/flujos/pqrd-versiones/versiones/comparar?a=0&b=1" })).json();
  assert.deepEqual(c.pasos.cambiados.map((x: { id: string; cambios: { campo: string; despues: string }[] }) => [x.id, x.cambios[0]!.campo, x.cambios[0]!.despues]), [["revisor", "deshabilitado", "true"]]);
  const rr = (await app.inject({ method: "POST", url: "/api/flujos/pqrd-versiones/restaurar", payload: { version: 1 } })).json();
  assert.equal(rr.version, 5);
  assert.deepEqual(estructuras.obtener("pqrd-versiones")!.apagadas, ["revisor"]);
  assert.equal((await estructuras.vista("pqrd-versiones", "pqrd")).version, 5);
  // el original no tiene historia propia por esto
  assert.deepEqual((await lista("pqrd")).map((v) => v.version), [0]);
  // borrar la copia se lleva su historial
  assert.equal(await flujos.borrar("pqrd-versiones"), true);
  assert.deepEqual(await versiones.leer("pqrd-versiones"), {});
});

test("sin encabezados de Vera, el autor es una persona aunque el pedido diga otra cosa", () => {
  assert.deepEqual(versiones.contextoDe({}), { autor: "persona" });
  assert.deepEqual(versiones.contextoDe({ "x-pedido": "hola" }), { autor: "persona" });
  assert.equal(versiones.contextoDe({ "x-autor": "vera", "x-pedido": encodeURIComponent("cámbialo   ya\n por favor") }).pedido, "cámbialo ya por favor");
  assert.ok(versiones.contextoDe({ "x-autor": "vera", "x-pedido": "x".repeat(400) }).pedido!.length <= 120);
});
