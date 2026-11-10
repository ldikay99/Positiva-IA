/**
 * Pruebas de «cruzar flujos» (cruzar.ts): comparar dos flujos, traer pasos de uno a otro (declarativo y
 * de código, con ids que chocan, dónde conectarlos, simular sin guardar) y que el origen nunca cambie;
 * y que un paso «subflujo» insertado en una copia de PQRD lleve en su orden el flujo que llama. Sin red.
 *
 *   node --test servidor/src/pruebas_cruzar.ts      (en el contenedor: /app/servidor/src)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Fastify from "fastify";

process.env.RUTA_DATOS = mkdtempSync(join(tmpdir(), "positiva-cruzar-"));
process.env.RUTA_ARCHIVOS = mkdtempSync(join(tmpdir(), "positiva-cruzar-archivos-"));
process.env.RUTA_BANDEJA = mkdtempSync(join(tmpdir(), "positiva-cruzar-bandeja-"));
process.env.RUTA_VIVO = mkdtempSync(join(tmpdir(), "positiva-cruzar-vivo-"));
delete process.env.CLAVE_CREDENCIALES;

const credenciales = await import("./credenciales.ts");
const flujos = await import("./flujos.ts");
const estructuras = await import("./estructuras.ts");
const cruzar = await import("./cruzar.ts");
await credenciales.cargar();
await flujos.cargar();

const avisos: { id: string; extra?: Record<string, unknown> }[] = [];
const app = Fastify();
await cruzar.registrar(app, { avisarFlujo: (id, extra) => avisos.push({ id, extra }) });

const entrada = { id: "inicio", tipo: "entrada_manual", nombre: "Inicio", config: { campos: [{ nombre: "texto", tipo: "texto" }] } };
const fin = { id: "fin", tipo: "salida", nombre: "Fin", config: { desenlace: "completar" } };

const ORIGEN = {
  id: "origen-cruce", nombre: "Origen del cruce", modelo: "gemini-2.5-flash",
  nodos: [
    entrada,
    { id: "limpiar", tipo: "transformar", nombre: "Limpiar", config: { operaciones: [{ op: "plantilla", plantilla: "{{entrada.texto}}", salida: "limpio" }] } },
    { id: "urgente", tipo: "condicion", nombre: "¿Urgente?", config: { campo: "limpio", operador: "contiene", valor: "urgente" } },
    { id: "a_mano", tipo: "salida", nombre: "Revisión", config: { desenlace: "manual", motivo: "URGENTE" } },
    fin,
  ],
  aristas: [{ desde: "inicio", hacia: "limpiar" }, { desde: "limpiar", hacia: "urgente" }, { desde: "urgente", hacia: "a_mano", cuando: "si" }, { desde: "urgente", hacia: "fin" }],
  disenio: { inicio: { x: 0, y: 0 }, limpiar: { x: 200, y: 0 }, urgente: { x: 400, y: 0 }, a_mano: { x: 600, y: 120 }, fin: { x: 600, y: 0 } },
};
const DESTINO = {
  id: "destino-cruce", nombre: "Destino del cruce", modelo: "gemini-2.5-flash",
  nodos: [entrada, { id: "limpiar", tipo: "transformar", nombre: "Otra limpieza", config: { operaciones: [{ op: "plantilla", plantilla: "x{{entrada.texto}}", salida: "limpio" }] } }, fin],
  aristas: [{ desde: "inicio", hacia: "limpiar" }, { desde: "limpiar", hacia: "fin" }],
  disenio: { inicio: { x: 0, y: 0 }, limpiar: { x: 200, y: 0 }, fin: { x: 400, y: 0 } },
};

test("comparar dice qué pasos, conexiones y configuración difieren", async () => {
  assert.equal((await flujos.guardar(ORIGEN)).ok, true);
  assert.equal((await flujos.guardar(DESTINO)).ok, true);
  const r = await app.inject({ method: "GET", url: "/api/flujos/destino-cruce/comparar?con=origen-cruce" });
  assert.equal(r.statusCode, 200, r.body);
  const c = r.json();
  assert.deepEqual(c.nodos.solo_en_b.map((n: { id: string }) => n.id).sort(), ["a_mano", "urgente"]);
  assert.deepEqual(c.nodos.solo_en_a, []);
  const lim = c.nodos.distintos.find((d: { id: string }) => d.id === "limpiar");
  assert.ok(lim.cambios.some((x: { campo: string }) => x.campo === "nombre") && lim.cambios.some((x: { campo: string }) => x.campo === "config.operaciones"), JSON.stringify(lim));
  assert.ok(c.aristas.solo_en_a.some((a: { desde: string; hacia: string }) => a.desde === "limpiar" && a.hacia === "fin"));
  assert.equal(c.iguales, false);
  assert.match(c.resumen, /solo en «Origen del cruce»/);
  const igual = (await app.inject({ method: "GET", url: "/api/flujos/origen-cruce/comparar?con=origen-cruce" })).json();
  assert.equal(igual.iguales, true);
  assert.equal((await app.inject({ method: "GET", url: "/api/flujos/origen-cruce/comparar?con=no-existe" })).statusCode, 404);
  // una copia de PQRD contra el original: los mismos pasos (las etapas)
  assert.equal((await flujos.clonar("pqrd", "PQRD cruce", "pqrd-cruce")).ok, true);
  const pq = (await app.inject({ method: "GET", url: "/api/flujos/pqrd-cruce/comparar?con=pqrd" })).json();
  assert.equal(pq.nodos.solo_en_a.length + pq.nodos.solo_en_b.length + pq.nodos.distintos.length, 0, JSON.stringify(pq.nodos));
});

test("traer pasos a un declarativo: renombra lo que choca, conecta, guarda versión y no toca el origen", async () => {
  const antesOrigen = JSON.stringify(flujos.obtener("origen-cruce")!.definicion);
  const v0 = flujos.obtener("destino-cruce")!.version!;
  // simular no guarda
  const sim = (await app.inject({ method: "POST", url: "/api/flujos/destino-cruce/traer", payload: { desde: "origen-cruce", nodos: ["limpiar", "urgente", "a_mano"], entre: ["limpiar", "fin"], simular: true } })).json();
  assert.equal(sim.ok, true, JSON.stringify(sim));
  assert.equal(sim.guardado, false);
  assert.equal(flujos.obtener("destino-cruce")!.version, v0);
  // de verdad
  const r = await app.inject({ method: "POST", url: "/api/flujos/destino-cruce/traer", payload: { desde: "origen-cruce", nodos: ["limpiar", "urgente", "a_mano"], entre: ["limpiar", "fin"] } });
  assert.equal(r.statusCode, 200, r.body);
  const t = r.json();
  assert.equal(t.guardado, true);
  assert.equal(t.version, v0 + 1);
  assert.deepEqual(t.renombrados, { limpiar: "limpiar_2" });
  const d = flujos.obtener("destino-cruce")!.definicion!;
  const ids = d.nodos.map((n) => n.id);
  assert.ok(["limpiar", "limpiar_2", "urgente", "a_mano"].every((x) => ids.includes(x)), ids.join(","));
  const tiene = (desde: string, hacia: string, cuando?: string) => d.aristas.some((a) => a.desde === desde && a.hacia === hacia && (a.cuando ?? undefined) === cuando);
  assert.ok(tiene("limpiar", "limpiar_2"), "se inserta donde estaba limpiar → fin");
  assert.ok(tiene("limpiar_2", "urgente") && tiene("urgente", "a_mano", "si") && tiene("urgente", "fin"), JSON.stringify(d.aristas));
  assert.ok(!tiene("limpiar", "fin"), "la conexión donde se insertó se quita");
  assert.ok(d.disenio?.limpiar_2 && d.disenio.limpiar_2.x > 400, "lo traído queda a la derecha en el lienzo");
  assert.equal(JSON.stringify(flujos.obtener("origen-cruce")!.definicion), antesOrigen, "el origen no cambia");
  assert.equal(avisos.at(-1)?.id, "destino-cruce");
});

test("lo que no tiene sentido no se trae y dice por qué", async () => {
  const pedir = (url: string, payload: Record<string, unknown>) => app.inject({ method: "POST", url, payload }).then((r) => ({ codigo: r.statusCode, ...r.json() }));
  const disp = await pedir("/api/flujos/destino-cruce/traer", { desde: "origen-cruce", nodos: ["inicio"], entre: ["inicio", "limpiar"] });
  assert.equal(disp.codigo, 400);
  assert.match(disp.error, /disparador/);
  const no = await pedir("/api/flujos/destino-cruce/traer", { desde: "origen-cruce", nodos: ["no_existe"] });
  assert.match(no.error, /no tiene estos pasos/);
  const sinArista = await pedir("/api/flujos/destino-cruce/traer", { desde: "origen-cruce", nodos: ["a_mano"], entre: ["fin", "inicio"] });
  assert.match(sinArista.error, /no tiene la conexión/);
  const suelto = await pedir("/api/flujos/destino-cruce/traer", { desde: "origen-cruce", nodos: ["a_mano"] });
  assert.equal(suelto.codigo, 400);
  assert.ok(suelto.problemas?.length, JSON.stringify(suelto));
  const etapa = await pedir("/api/flujos/destino-cruce/traer", { desde: "pqrd", nodos: ["compuerta"], entre: ["limpiar", "limpiar_2"] });
  assert.match(etapa.error, /etapas del motor/);
});

test("traer pasos de una copia de PQRD a otra va dentro de un punto de inserción; el original sigue igual", async () => {
  // la fuente: otra copia de PQRD con un tramo «antes de validar» (exigir el documento del afectado)
  assert.equal((await flujos.clonar("pqrd", "PQRD fuente", "pqrd-fuente")).ok, true);
  const f = (await estructuras.vista("pqrd-fuente", "pqrd")).definicion;
  f.aristas = f.aristas.filter((a) => !(a.desde === "registro_de_personas" && a.hacia === "compuerta"));
  f.nodos.push({ id: "exigir_documento", tipo: "condicion", nombre: "¿Hay documento?", config: { campo: "campos.numero_documento_afectado", operador: "no_vacio" } },
    { id: "sin_documento", tipo: "salida", nombre: "Sin documento", config: { desenlace: "manual", motivo: "SIN_DOCUMENTO" } });
  f.aristas.push({ desde: "registro_de_personas", hacia: "exigir_documento" }, { desde: "exigir_documento", hacia: "compuerta", cuando: "si" }, { desde: "exigir_documento", hacia: "sin_documento", cuando: "no" });
  assert.equal((await estructuras.guardar("pqrd-fuente", "pqrd", f)).ok, true);
  const antes = JSON.stringify(estructuras.obtener("pqrd-fuente")!.definicion);
  const r = await app.inject({ method: "POST", url: "/api/flujos/pqrd-cruce/traer", payload: { desde: "pqrd-fuente", nodos: ["exigir_documento", "sin_documento"], entre: ["registro_de_personas", "compuerta"] } });
  assert.equal(r.statusCode, 200, r.body);
  const t = r.json();
  assert.equal(t.guardado, true, r.body);
  assert.equal(estructuras.obtener("pqrd-cruce")?.tramos[0]?.punto, "antes_de_validar");
  assert.deepEqual(estructuras.obtener("pqrd-cruce")?.tramos[0]?.nodos.sort(), ["exigir_documento", "sin_documento"]);
  assert.equal(estructuras.obtener("pqrd"), undefined, "el PQRD original no se toca");
  assert.equal(JSON.stringify(estructuras.obtener("pqrd-fuente")!.definicion), antes, "la fuente no cambia");
  // una variable que no existe en ese punto se rechaza con su motivo
  const sinVariable = await app.inject({ method: "POST", url: "/api/flujos/pqrd-cruce/traer", payload: { desde: "origen-cruce", nodos: ["urgente"], entre: ["conversion_a_markdown", "saneamiento"] } });
  assert.equal(sinVariable.statusCode, 400);
  assert.match(sinVariable.json().problemas.join(" "), /limpio/);
  // fuera de un punto no
  const mal = await app.inject({ method: "POST", url: "/api/flujos/pqrd-cruce/traer", payload: { desde: "origen-cruce", nodos: ["limpiar"], entre: ["lote", "deduplicacion"] } });
  assert.equal(mal.statusCode, 400, mal.body);
});

test("un paso subflujo en una copia de PQRD lleva en su orden el flujo que llama", async () => {
  const v = await estructuras.vista("pqrd-cruce", "pqrd");
  const d = v.definicion;
  d.aristas = d.aristas.filter((a) => !(a.desde === "conversion_a_markdown" && a.hacia === "saneamiento"));
  d.nodos.push({ id: "llamar_origen", tipo: "subflujo", nombre: "Llamar al origen", config: { flujo: "origen-cruce", entrada: { texto: "{{correo.asunto}}" }, salida: "otro" } });
  d.aristas.push({ desde: "conversion_a_markdown", hacia: "llamar_origen" }, { desde: "llamar_origen", hacia: "saneamiento" });
  const g = await estructuras.guardar("pqrd-cruce", "pqrd", d, "subflujo");
  assert.equal(g.ok, true, JSON.stringify(g.problemas));
  const orden = flujos.ordenPara("pqrd-cruce", { configuracion: { entorno: {} } } as never) as { pasos?: { subflujos?: Record<string, { definicion: { id: string } }> } };
  assert.equal(orden.pasos?.subflujos?.["origen-cruce"]?.definicion.id, "origen-cruce");
});
