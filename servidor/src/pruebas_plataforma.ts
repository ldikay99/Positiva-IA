/**
 * Pruebas del servidor de la plataforma de automatización: credenciales (cifrado,
 * alcance, nada de valores en la API), entorno por flujo (secretos, confirmación,
 * migración), manifiesto, orden de una corrida y cron. Sin red.
 *
 *   node --test servidor/dist/pruebas_plataforma.js      (en el contenedor: /app/servidor/dist)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

process.env.RUTA_DATOS = mkdtempSync(join(tmpdir(), "positiva-prueba-"));
delete process.env.CLAVE_CREDENCIALES;

const credenciales = await import("./credenciales.ts");
const entornos = await import("./entornos.ts");
const flujos = await import("./flujos.ts");
const programador = await import("./programador.ts");

const CLAVE = "clave-MUY-secreta-123";
const TOKEN = "token-ULTRA-secreto-456";
await credenciales.cargar();

test("credenciales: cifradas, sin valores en la API y la escritura se apaga al reemplazar", async () => {
  const r = await credenciales.guardar("buzon", { tipo: "imap", valores: { host: "imap.ejemplo.test", usuario: "ops@ejemplo.test", clave: CLAVE } });
  assert.equal(r.ok, true);
  const disco = readFileSync(join(process.env.RUTA_DATOS!, "credenciales.json"), "utf8");
  assert.ok(!disco.includes(CLAVE) && !disco.includes("imap.ejemplo.test"), "en disco va cifrado");
  assert.ok(!JSON.stringify(credenciales.listar()).includes(CLAVE));
  assert.deepEqual(credenciales.listar().find((c) => c.nombre === "buzon")?.campos, ["host", "puerto", "usuario", "clave", "tls"]);
  assert.equal((await credenciales.permitirEscritura("buzon", true)).ok, true);
  // reemplazar sin la clave la conserva, pero apaga la escritura
  await credenciales.guardar("buzon", { tipo: "imap", valores: { host: "imap.ejemplo.test", usuario: "ops@ejemplo.test" } });
  assert.equal(credenciales.listar().find((c) => c.nombre === "buzon")?.permitir_escritura, false);
  assert.equal(credenciales.paraCorrida("x", ["buzon"]).buzon?.valores.clave, CLAVE);
  assert.deepEqual(credenciales.paraCorrida("x", ["buzon"]).buzon?.secretos, ["clave"]);
  // destinos vetados y validación
  for (const [tipo, valores] of [["imap", { host: "correo.positiva.gov.co", usuario: "a@b.co", clave: "x" }],
    ["http_bearer", { token: "x", hosts: "api.ok.test, ms-sgdea-prd.proyectos-3t.tech" }],
    ["microsoft_graph", { tenant: "t", client_id: "c", client_secret: "s", buzon: "servicioalcliente@positiva.gov.co" }],
    ["sqlite", { ruta: "../fuera.db" }], ["imap", { host: "h" }], ["imap", { host: "h", usuario: "u", clave: "c", raro: 1 }]] as const) {
    assert.equal((await credenciales.guardar("mala", { tipo, valores })).ok, false, `${tipo} ${JSON.stringify(valores)}`);
  }
  assert.equal((await credenciales.guardar("Con Espacio", { tipo: "sqlite", valores: { ruta: "a.db" } })).ok, false);
});

test("credenciales: alcance por flujo", async () => {
  await credenciales.guardar("api-de-a", { tipo: "http_bearer", valores: { token: TOKEN, hosts: "api.ejemplo.test" }, alcance: "flujo-a" });
  assert.deepEqual(Object.keys(credenciales.paraCorrida("flujo-a", ["api-de-a"])), ["api-de-a"]);
  assert.deepEqual(credenciales.paraCorrida("flujo-b", ["api-de-a"]), {});  // otro flujo no la recibe
  assert.ok(credenciales.listar("flujo-a").some((c) => c.nombre === "api-de-a"));
  assert.ok(!credenciales.listar("flujo-b").some((c) => c.nombre === "api-de-a"));
  assert.ok(credenciales.listar("flujo-b").some((c) => c.nombre === "buzon"));  // las globales sí
  const d = credenciales.destino("api-de-a", "flujo-b");
  assert.equal(d.usable, false);
  assert.ok(!JSON.stringify(d).includes(TOKEN));
  assert.deepEqual(d.destino, { hosts: "api.ejemplo.test" });
});

test("entorno por flujo: secretos cifrados, confirmación de persona y nombres protegidos", async () => {
  assert.equal((await entornos.poner("flujo-a", "REGION", { valor: "andina" }, false)).ok, true);
  const sin = await entornos.poner("flujo-a", "TOKEN_API", { valor: TOKEN, secreto: true }, false);
  assert.equal(sin.ok ? 0 : sin.codigo, 403);
  assert.equal((await entornos.poner("flujo-a", "TOKEN_API", { valor: TOKEN, secreto: true }, true)).ok, true);
  // cambiar un secreto (o volverlo público) también pide la confirmación
  assert.equal((await entornos.poner("flujo-a", "TOKEN_API", { valor: "otro", secreto: false }, false)).ok, false);
  const lista = await entornos.listar("flujo-a");
  assert.deepEqual(lista.map((v) => [v.nombre, v.secreto, v.definido, v.valor]), [["REGION", false, true, "andina"], ["TOKEN_API", true, true, undefined]]);
  assert.ok(!JSON.stringify(lista).includes(TOKEN));
  assert.ok(!readFileSync(join(process.env.RUTA_DATOS!, "entornos", "flujo-a.json"), "utf8").includes(TOKEN));
  assert.deepEqual(entornos.paraCorrida("flujo-a"), { valores: { REGION: "andina", TOKEN_API: TOKEN }, secretos: ["TOKEN_API"] });
  assert.deepEqual(entornos.paraCorrida("flujo-b"), { valores: {}, secretos: [] });
  for (const n of ["MODO_TEST", "DEDUP_EN_PRUEBAS", "SGDEA_PASSWORD", "PATH", "HTTP_HOSTS_PERMITIDOS", "minusculas", "RUTA_DATOS"]) {
    assert.equal((await entornos.poner("flujo-a", n, { valor: "x" }, true)).ok, false, n);
  }
});

test("migración del entorno global a pqrd: una sola vez y sin pisar", async () => {
  assert.equal(await entornos.migrarDesdeConfiguracion("pqrd", { MAX_VUELTAS_AGENTE: "7", MODO_TEST: "false" }), 1);
  assert.deepEqual(entornos.paraProceso("pqrd"), { MAX_VUELTAS_AGENTE: "7" });  // la protegida no pasa
  assert.equal(await entornos.migrarDesdeConfiguracion("pqrd", { MAX_VUELTAS_AGENTE: "9" }), 0);
  assert.equal(entornos.paraProceso("pqrd").MAX_VUELTAS_AGENTE, "7");
});

test("cron en America/Bogota", () => {
  const lunes7 = new Date("2026-11-09T12:00:00Z");  // 07:00 en Bogotá (UTC-5), lunes
  assert.equal(programador.coincideCron("0 7 * * 1-5", lunes7, "America/Bogota"), true);
  assert.equal(programador.coincideCron("0 7 * * 6,0", lunes7, "America/Bogota"), false);
  assert.equal(programador.coincideCron("*/15 * * * *", new Date("2026-11-09T12:30:00Z"), "America/Bogota"), true);
  assert.equal(programador.coincideCron("*/15 * * * *", new Date("2026-11-09T12:31:00Z"), "America/Bogota"), false);
  assert.equal(programador.coincideCron("0 0 1 * 0", new Date("2026-11-08T05:00:00Z"), "America/Bogota"), true);  // domingo (0 o 7)
  assert.equal(flujos.problemaCron("61 * * * *") !== null, true);
  assert.equal(flujos.problemaCron("0 7 * * 1-5"), null);
});

test("manifiesto y orden de corrida: conexiones, escrituras, entorno y webhook sin secretos", async () => {
  await credenciales.guardar("base", { tipo: "sqlite", valores: { ruta: "base.db" } });
  const d = {
    id: "flujo-a", nombre: "Flujo A", modelo: "gemini-2.5-flash",
    nodos: [
      { id: "gancho", tipo: "webhook", nombre: "Recibir", config: { responder: true } },
      { id: "anota", tipo: "sql_ejecutar", nombre: "Anotar", config: { credencial: "base", sentencia: "INSERT INTO t (x) VALUES (:x)", parametros: { x: "{{entrada.cuerpo.x}}" } } },
      { id: "avisa", tipo: "http", nombre: "Avisar", config: { metodo: "POST", url: "https://api.ejemplo.test/avisos", credencial: "api-de-a", cuerpo: { r: "{{entorno.REGION}}", t: "{{entorno.TOKEN_API}}" } } },
      { id: "fin", tipo: "salida", nombre: "Fin", config: { desenlace: "manual" } },
    ],
    aristas: [{ desde: "gancho", hacia: "anota" }, { desde: "anota", hacia: "avisa" }, { desde: "avisa", hacia: "fin" }],
  };
  const g = await flujos.guardar(d, "primera");
  assert.equal(g.ok, true, g.problemas.join("\n"));
  const secreto = (g.flujo!.definicion!.nodos[0]!.config as { secreto?: string }).secreto!;
  assert.match(secreto, /^[A-Za-z0-9_-]{32}$/);  // se generó al guardar
  const g2 = await flujos.guardar({ ...d, nombre: "Flujo A2" }, "segunda");
  assert.equal((g2.flujo!.definicion!.nodos[0]!.config as { secreto?: string }).secreto, secreto);  // y se conserva
  const m = (await flujos.manifiesto("flujo-a"))!;
  const texto = JSON.stringify(m);
  for (const s of [secreto, TOKEN, CLAVE]) assert.ok(!texto.includes(s), "el manifiesto no lleva secretos");
  assert.deepEqual((m.disparador as { webhook: { ruta: string } }).webhook.ruta, "/api/ganchos/flujo-a/<secreto>");
  const esc = m.escrituras as { nodo: string; habilitada: boolean }[];
  assert.deepEqual(esc.map((x) => [x.nodo, x.habilitada]), [["anota", false], ["avisa", false]]);
  const con = m.conexiones as { credencial: string; tipo: string; alcance: string; escribe: boolean }[];
  assert.deepEqual(con.map((x) => [x.credencial, x.tipo, x.alcance, x.escribe]), [["base", "sqlite", "global", true], ["api-de-a", "http_bearer", "flujo-a", true]]);
  assert.deepEqual((m.entorno as { nombre: string; secreto: boolean; usado: boolean }[]).map((v) => [v.nombre, v.secreto, v.usado]), [["REGION", false, true], ["TOKEN_API", true, true]]);
  assert.deepEqual((m.hosts_http as { host: string }[]).map((h) => h.host), ["api.ejemplo.test"]);
  // habilitar la escritura se ve en el manifiesto
  await credenciales.permitirEscritura("base", true);
  assert.equal(((await flujos.manifiesto("flujo-a"))!.escrituras as { habilitada: boolean }[])[0]!.habilitada, true);
  // la orden de una corrida lleva solo lo que el flujo usa y puede usar
  const corrida = { id: "c1", flujo: "flujo-a", caso: "manual-1", estado: "en_cola", creada: 0, eventos: [], entrada: { cuerpo: { x: 1 } }, origen: "webhook",
    configuracion: { entorno: { GLOBAL: "no" } } } as unknown as Parameters<typeof flujos.ordenPara>[1];
  const o = flujos.ordenPara("flujo-a", corrida) as { credenciales: Record<string, unknown>; entorno: Record<string, string>; entorno_secretos: string[]; origen: string };
  assert.deepEqual(Object.keys(o.credenciales).sort(), ["api-de-a", "base"]);
  assert.deepEqual(o.entorno, { REGION: "andina", TOKEN_API: TOKEN });
  assert.deepEqual(o.entorno_secretos, ["TOKEN_API"]);
  assert.equal(o.origen, "webhook");
  // {{entorno.X}} que el flujo no tiene: la validación lo marca
  const malo = structuredClone(d);
  (malo.nodos[3]!.config as Record<string, unknown>).motivo = "{{entorno.NO_EXISTE}}";
  const v = await flujos.validar(malo);
  assert.ok(v.problemas.some((p) => p.includes("NO_EXISTE")), v.problemas.join("\n"));
  // versiones, restaurar, duplicar y renombrar
  const vs = (await flujos.versiones("flujo-a"))!;
  assert.deepEqual(vs.map((x) => [x.version, x.nota]), [[2, "segunda"], [1, "primera"]]);
  const r = await flujos.restaurar("flujo-a", 1);
  assert.equal(r.ok && r.flujo.nombre, "Flujo A");
  const dup = await flujos.duplicar("flujo-a", "flujo-copia", "Copia");
  assert.equal(dup.ok, true, JSON.stringify(dup));
  assert.notEqual(dup.ok && (dup.flujo.definicion!.nodos[0]!.config as { secreto?: string }).secreto, secreto);  // la copia tiene otro webhook
  assert.equal((await flujos.renombrar("pqrd", { nombre: "x" })).ok, false);
  const ren = await flujos.renombrar("flujo-copia", { nombre: "Copia renombrada" });
  assert.equal(ren.ok && ren.flujo.nombre, "Copia renombrada");
  assert.equal(await flujos.borrar("flujo-copia"), true);
});
