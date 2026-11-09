/**
 * Pruebas de la estructura editable de los flujos de código: una copia de PQRD se edita sin tocar el
 * original, el lienzo la ve, la orden de la corrida la lleva y lo que no tiene sentido se rechaza con su
 * motivo. Las reglas son las de puente/estructura_codigo.py (las corre de verdad). Sin red.
 *
 *   node --test servidor/src/pruebas_estructura.ts      (en el contenedor: /app/servidor/src)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

process.env.RUTA_DATOS = mkdtempSync(join(tmpdir(), "positiva-estructura-"));
process.env.RUTA_ARCHIVOS = mkdtempSync(join(tmpdir(), "positiva-archivos-"));
process.env.RUTA_BANDEJA = mkdtempSync(join(tmpdir(), "positiva-bandeja-"));
process.env.RUTA_VIVO = mkdtempSync(join(tmpdir(), "positiva-vivo-"));
delete process.env.CLAVE_CREDENCIALES;

const credenciales = await import("./credenciales.ts");
const flujos = await import("./flujos.ts");
const estructuras = await import("./estructuras.ts");
await credenciales.cargar();
await flujos.cargar();

type Def = import("./estructuras.ts").DefinicionEstructura;

function insertarAntesDeValidar(d: Def): Def {
  d.aristas = d.aristas.filter((a) => !(a.desde === "registro_de_personas" && a.hacia === "compuerta"));
  d.nodos.push({ id: "exigir_documento", tipo: "condicion", nombre: "¿Hay documento?", config: { campo: "campos.numero_documento_afectado", operador: "no_vacio" } },
    { id: "sin_documento", tipo: "salida", nombre: "Sin documento", config: { desenlace: "manual", motivo: "SIN_DOCUMENTO" } });
  d.aristas.push({ desde: "registro_de_personas", hacia: "exigir_documento" }, { desde: "exigir_documento", hacia: "compuerta", cuando: "si" },
    { desde: "exigir_documento", hacia: "sin_documento", cuando: "no" });
  return d;
}

test("una copia de PQRD se edita, el lienzo y la orden la ven y el original sigue como el código", async () => {
  const k = await flujos.clonar("pqrd", "PQRD editable", "pqrd-editable");
  assert.equal(k.ok, true, JSON.stringify(k));
  const v0 = await estructuras.vista("pqrd-editable", "pqrd");
  assert.equal(v0.editada, false);
  const d = insertarAntesDeValidar(v0.definicion);
  d.nodos.find((n) => n.id === "revisor")!.config = { deshabilitado: true };
  const g = await estructuras.guardar("pqrd-editable", "pqrd", d, "paso de prueba");
  assert.equal(g.ok, true, JSON.stringify(g.problemas));
  assert.deepEqual(g.apagadas, ["revisor"]);
  assert.equal(g.tramos?.[0]?.punto, "antes_de_validar");
  // el lienzo: el paso insertado con su punto, la etapa apagada y las aristas nuevas
  const cat = flujos.catalogoDe("pqrd-editable")!;
  const paso = cat.nodos.find((n) => n.id === "exigir_documento") as unknown as { insertado?: { punto: string; desde: string } };
  assert.equal(paso.insertado?.punto, "antes_de_validar");
  assert.equal(paso.insertado?.desde, "registro_de_personas");
  assert.equal((cat.nodos.find((n) => n.id === "revisor") as unknown as { deshabilitado?: boolean }).deshabilitado, true);
  assert.ok(cat.aristas.some((a) => a.desde === "registro_de_personas" && a.hacia === "exigir_documento"));
  assert.ok(!cat.aristas.some((a) => a.desde === "registro_de_personas" && a.hacia === "compuerta"));
  // la orden de la corrida lleva la estructura y lo que necesitan los pasos
  const orden = flujos.ordenPara("pqrd-editable", { configuracion: { entorno: {} } } as never) as { estructura?: { version: number }; pasos?: { flujo: string } };
  assert.equal(orden.estructura?.version, 1);
  assert.equal(orden.pasos?.flujo, "pqrd-editable");
  // el original no cambió: ni estructura, ni catálogo, ni orden
  assert.equal(estructuras.obtener("pqrd"), undefined);
  assert.equal(flujos.obtener("pqrd")?.editada, false);
  assert.equal(flujos.obtener("pqrd-editable")?.editada, true);
  assert.ok(!flujos.catalogoDe("pqrd")!.nodos.some((n) => n.id === "exigir_documento"));
  assert.equal((flujos.ordenPara("pqrd", { configuracion: { entorno: {} } } as never) as { estructura?: unknown }).estructura, undefined);
  // la copia de la copia nace con la estructura y desde ahí diverge
  const k2 = await flujos.clonar("pqrd-editable", "Copia de la copia", "pqrd-editable-2");
  assert.equal(k2.ok, true);
  assert.equal(estructuras.obtener("pqrd-editable-2")?.definicion.nodos.some((n) => n.id === "exigir_documento"), true);
  // borrar la copia se lleva su estructura
  assert.equal(await flujos.borrar("pqrd-editable-2"), true);
  assert.equal(estructuras.obtener("pqrd-editable-2"), undefined);
  assert.ok(!existsSync(join(process.env.RUTA_DATOS!, "flujos_codigo", "pqrd-editable-2.estructura.json")));
  // volver a la del código
  assert.equal(await estructuras.restablecer("pqrd-editable"), true);
  assert.ok(!flujos.catalogoDe("pqrd-editable")!.nodos.some((n) => n.id === "exigir_documento"));
});

test("lo que no tiene sentido no se guarda y dice por qué", async () => {
  const d = (await estructuras.vista("pqrd", "pqrd")).definicion;
  d.nodos = d.nodos.filter((n) => n.id !== "compuerta");
  const r = await estructuras.guardar("pqrd", "pqrd", d);
  assert.equal(r.ok, false);
  assert.ok(r.problemas.some((p) => p.includes("falta la etapa «compuerta»") && p.includes("no se quitan")), r.problemas.join(" | "));
  assert.equal(estructuras.obtener("pqrd"), undefined, "un intento malo no deja nada guardado");
  const d2 = (await estructuras.vista("pqrd", "pqrd")).definicion;
  d2.nodos.find((n) => n.id === "agente")!.config = { deshabilitado: true };
  const r2 = await estructuras.validar("pqrd", d2);
  assert.ok(r2.problemas.some((p) => p.startsWith("agente: esta etapa no se puede apagar")), r2.problemas.join(" | "));
});
