/**
 * Pruebas del servidor de la plataforma de automatización: credenciales (cifrado,
 * alcance, nada de valores en la API), entorno por flujo (secretos, confirmación,
 * migración), manifiesto, orden de una corrida y cron. Sin red.
 *
 *   node --test servidor/dist/pruebas_plataforma.js      (en el contenedor: /app/servidor/dist)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

process.env.RUTA_DATOS = mkdtempSync(join(tmpdir(), "positiva-prueba-"));
delete process.env.CLAVE_CREDENCIALES;

const credenciales = await import("./credenciales.ts");
const entornos = await import("./entornos.ts");
const flujos = await import("./flujos.ts");
const programador = await import("./programador.ts");
const compuerta = await import("./compuerta.ts");
const { Motor } = await import("./motor.ts");
const { enmascarar, variantes } = await import("./enmascarar.ts");

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
  // reemplazar sin la clave la conserva; la escritura solo se apaga si cambia algún valor
  await credenciales.guardar("buzon", { tipo: "imap", valores: { host: "imap.ejemplo.test", usuario: "ops@ejemplo.test" } });
  assert.equal(credenciales.listar().find((c) => c.nombre === "buzon")?.permitir_escritura, true);
  await credenciales.guardar("buzon", { tipo: "imap", valores: { host: "imap.ejemplo.test", usuario: "otra@ejemplo.test" } });
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
  assert.deepEqual(entornos.paraProceso("pqrd"), { MAX_VUELTAS_AGENTE: "7", SECRETOS_DEL_FLUJO: "" });  // la protegida no pasa
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

// ------------------------------------------------ como el job: entorno, máscara, compuerta, programación

const SECRETO_JOB = "secreto-DEL-job-0123456789";

test("entorno de los flujos de código: los nombres exactos de su job (con guiones solo en tutelas)", async () => {
  // pqrd acepta los nombres de su job, que un flujo declarativo no puede usar
  for (const n of ["MICROSOFT_CLIENT_SECRET", "SUPABASE_SERVICE_ROLE_KEY", "REDIS_PASSWORD", "OUTLOOK_FOLDER_ID", "SGDEA_PASSWORD", "GOOGLE_API_KEY", "GCP_PROJECT"]) {
    assert.equal(entornos.problemaDeNombre("pqrd", n), null, n);
    assert.notEqual(entornos.problemaDeNombre("flujo-a", n), null, `declarativo: ${n}`);
  }
  for (const n of ["OPENAI_API_KEY", "CORREO_DE_AVISOS", "OUTLOOK_USER_EMAIL", "SGDEA_TOKEN_URL"]) assert.equal(entornos.problemaDeNombre("pqrd", n), null, n);
  // tutelas: con guiones, exactamente los del job; DATABASE_* también
  for (const n of ["TUTELAS-MICROSOFT-CLIENT-SECRET", "TUTELAS-REDIS-PASSWORD", "TUTELAS-DATABASE-PASSWORD", "TUTELAS-RADICAR-ENLACE-CON-ADJUNTOS", "DATABASE_HOST", "DATABASE_PORT", "DATABASE_PASSWORD"]) {
    assert.equal(entornos.problemaDeNombre("tutelas", n), null, n);
  }
  // un guion fuera de la lista del job no vale en ningún flujo
  for (const [f, n] of [["tutelas", "TUTELAS-INVENTADA"], ["pqrd", "TUTELAS-REDIS-PASSWORD"], ["flujo-a", "TUTELAS-REDIS-PASSWORD"], ["flujo-a", "MI-VARIABLE"], ["pqrd", "MICROSOFT-CLIENT-SECRET"]] as const) {
    assert.notEqual(entornos.problemaDeNombre(f, n), null, `${f}: ${n}`);
  }
  // lo que nunca se acepta, ni en un flujo de código
  for (const f of ["pqrd", "tutelas", "flujo-a"]) {
    for (const n of ["MODO_TEST", "MODO_UAT", "DEDUP_EN_PRUEBAS", "RADICACION_REAL", "PERMITIR_RADICACION_REAL", "SECRETOS_DEL_FLUJO", "PATH", "RUTA_DATOS", "CLAVE_CREDENCIALES", "PYTHONPATH", "HTTP_HOSTS_PERMITIDOS"]) {
      assert.equal((await entornos.poner(f, n, { valor: "x" }, true)).ok, false, `${f}: ${n}`);
    }
  }
  // se guardan cifrados y llegan al proceso con su nombre de guiones; SECRETOS_DEL_FLUJO lleva solo nombres
  assert.equal((await entornos.poner("tutelas", "TUTELAS-REDIS-PASSWORD", { valor: SECRETO_JOB, secreto: true }, false)).ok, false);  // sin persona no
  assert.equal((await entornos.poner("tutelas", "TUTELAS-REDIS-PASSWORD", { valor: SECRETO_JOB, secreto: true }, true)).ok, true);
  // una variable del job, aunque no sea secreta, también la confirma una persona (Vera no la puede poner)
  assert.equal((await entornos.poner("tutelas", "DATABASE_HOST", { valor: "10.0.0.5" }, false)).ok, false);
  assert.equal((await entornos.poner("tutelas", "DATABASE_HOST", { valor: "10.0.0.5" }, true)).ok, true);
  const p = entornos.paraProceso("tutelas");
  assert.equal(p["TUTELAS-REDIS-PASSWORD"], SECRETO_JOB);
  assert.equal(p.DATABASE_HOST, "10.0.0.5");
  assert.equal(p.SECRETOS_DEL_FLUJO, "TUTELAS-REDIS-PASSWORD");
  assert.deepEqual(entornos.valoresSecretos("tutelas"), [SECRETO_JOB]);
  assert.ok(!JSON.stringify(await entornos.listar("tutelas")).includes(SECRETO_JOB));
  assert.ok(!readFileSync(join(process.env.RUTA_DATOS!, "entornos", "tutelas.json"), "utf8").includes(SECRETO_JOB));
  // el manifiesto de un flujo de código no lleva valores secretos
  assert.ok(!JSON.stringify(await flujos.manifiesto("tutelas")).includes(SECRETO_JOB));
});

test("máscara: el valor, su forma JSON y su forma URL, a cualquier profundidad", () => {
  const raro = 'cl"ave/con&raros+123';
  const v = variantes([raro, "x"]);  // lo de menos de 4 caracteres no se busca
  const e = enmascarar({ linea: `token=${encodeURIComponent(raro)}`, json: JSON.stringify({ k: raro }), [raro]: [raro, 1] }, v);
  const texto = JSON.stringify(e);
  assert.ok(!texto.includes(raro) && !texto.includes(encodeURIComponent(raro)) && !texto.includes(JSON.stringify(raro).slice(1, -1)));
  assert.ok(texto.includes("••••"));
  assert.equal(enmascarar("x marca x", v), "x marca x");
});

test("trabajador de un flujo de código: sus secretos salen enmascarados en eventos, consola y resultados; arranca con la compuerta apagada", async () => {
  const dir = mkdtempSync(join(tmpdir(), "positiva-trabajador-"));
  const script = join(dir, "trabajador_falso.py");
  // Un trabajador que imprime sus secretos por todas partes (lo que nunca debe llegar a la web).
  writeFileSync(script, [
    "import json, os, sys",
    "M = '\\x1eEVT '",
    "s = os.environ.get('CLAVE_DEL_JOB', '')",
    "def ev(**d): print(M + json.dumps(d), flush=True)",
    "ev(tipo='listo')",
    "for linea in sys.stdin:",
    "    o = json.loads(linea)",
    "    ev(tipo='inicio', corrida=o['corrida'], compuerta=os.environ.get('RADICACION_REAL'), orden_real=o.get('radicacion_real'), secretos=os.environ.get('SECRETOS_DEL_FLUJO'))",
    "    print('consola con ' + s, flush=True)",
    "    sys.stderr.write('traza: ' + s[:7]); sys.stderr.flush()",
    "    sys.stderr.write(s[7:] + ' fin\\n'); sys.stderr.flush()",
    "    ev(tipo='paso', corrida=o['corrida'], titulo='usa ' + json.dumps({'k': s}), detalle={'anidado': [s]})",
    "    ev(tipo='resultado', corrida=o['corrida'], caso=o['caso'], accion='manual', motivo='Bearer ' + s)",
    "    ev(tipo='libre')",
  ].join("\n"), "utf8");
  const m = new Motor("pqrd", script);
  m.entornoDeProceso = () => ({ CLAVE_DEL_JOB: SECRETO_JOB, SECRETOS_DEL_FLUJO: "CLAVE_DEL_JOB" });
  m.secretosDeProceso = () => [SECRETO_JOB];
  const vistos: unknown[] = [];
  const fin = new Promise<void>((ok) => m.on("evento", (e: { tipo: string }) => { vistos.push(e); if (e.tipo === "resultado") setTimeout(ok, 300); }));
  m.ajustar(1);
  const conf = { parametros: {}, entorno: {}, ia: { modelo: "x" }, topeSegundos: 60, reglas: [] } as unknown as Parameters<typeof m.encolar>[1];
  const c = m.encolar("caso-1", conf);
  await fin;
  m.detener();
  const texto = JSON.stringify(vistos) + JSON.stringify(c.eventos) + JSON.stringify(m.recientes);
  assert.ok(!texto.includes(SECRETO_JOB), "ningún secreto en lo que se reenvía o guarda");
  assert.ok(texto.includes("consola con ••••") && texto.includes("traza: •••• fin") && texto.includes("Bearer ••••"), texto.slice(0, 2000));
  const inicio = c.eventos.find((e) => e.tipo === "inicio")!;
  assert.equal(inicio.compuerta, "no");  // el servidor la pone apagada
  assert.equal(inicio.orden_real, false);
  assert.equal(inicio.secretos, "CLAVE_DEL_JOB");  // nombres, nunca valores
  assert.equal(c.resultado?.motivo, "Bearer ••••");
});

test("compuerta: no se enciende sin las dos llaves; apagar nunca pide nada; apagada todo es simulación", async () => {
  assert.equal(process.env.PERMITIR_RADICACION_REAL, undefined, "las pruebas nunca ponen la segunda llave");
  await compuerta.cargar();
  assert.deepEqual(compuerta.obtener("pqrd"), { radicacion_real: false, permitido_en_despliegue: false });
  // la regla de las dos llaves, sin tocar el entorno
  assert.equal(compuerta.puedeCambiar(true, true, false).ok, false);   // confirmada, sin segunda llave
  assert.equal(compuerta.puedeCambiar(true, false, true).ok, false);   // segunda llave, sin persona
  assert.equal(compuerta.puedeCambiar(true, false, false).ok, false);
  assert.equal(compuerta.puedeCambiar(true, true, true).ok, true);
  assert.equal(compuerta.puedeCambiar(false, false, false).ok, true);  // apagar
  // de verdad: con la confirmación de una persona y sin la segunda llave, no se enciende
  for (const f of ["pqrd", "tutelas"]) {
    const r = await compuerta.cambiar(f, { radicacion_real: true }, true);
    assert.equal(r.ok ? 0 : r.codigo, 403, f);
    assert.equal(compuerta.radicacionReal(f), false);
    const apagar = await compuerta.cambiar(f, { radicacion_real: false }, false);
    assert.equal(apagar.ok && apagar.compuerta.radicacion_real, false);
  }
  assert.equal((await compuerta.cambiar("flujo-a", { radicacion_real: false }, true)).ok, false);  // solo los de código
  assert.equal((await compuerta.cambiar("pqrd", { radicacion_real: "si" }, true)).ok, false);
  // aunque el archivo dijera «encendida», sin la segunda llave no escribe
  writeFileSync(join(process.env.RUTA_DATOS!, "compuertas.json"), JSON.stringify({ pqrd: { radicacion_real: true } }), "utf8");
  await compuerta.cargar();
  assert.equal(compuerta.obtener("pqrd").radicacion_real, true);
  assert.equal(compuerta.radicacionReal("pqrd"), false);
  const man = (await flujos.manifiesto("pqrd"))!;
  const cm = man.compuerta as { radicacion_real: boolean; estado: string; permitido_en_despliegue: boolean };
  assert.equal(cm.radicacion_real, false);
  assert.equal(cm.permitido_en_despliegue, false);
  assert.match(cm.estado, /apagada \(simulación\)/);
  // apagada: todas las escrituras del manifiesto siguen simuladas
  assert.ok((man.escrituras as { en_este_visor: string }[]).every((x) => !x.en_este_visor.includes("ESCRIBE DE VERDAD")));
  assert.ok(Array.isArray(man.lecturas_con_la_compuerta_apagada) && (man.lecturas_con_la_compuerta_apagada as unknown[]).length > 0);
  await compuerta.cambiar("pqrd", { radicacion_real: false }, false);
});

test("programación como el job: valores del Cloud Scheduler, apagada por defecto y sin solapes", async () => {
  await programador.cargarProgramaciones();
  assert.deepEqual(programador.programacionDe("pqrd"), { cron: "*/30 * * * *", zona: "America/Bogota", activo: false, n: 50 });
  assert.deepEqual(programador.programacionDe("tutelas"), { cron: "5,25,45 6-17 * * *", zona: "America/Bogota", activo: false, n: 50 });
  assert.equal(programador.programacionDe("flujo-a"), null);
  assert.equal((await programador.ponerProgramacion("pqrd", { cron: "61 * * * *" }, flujos.problemaCron)).ok, false);
  assert.equal((await programador.ponerProgramacion("pqrd", { zona: "Marte/Olimpo" }, flujos.problemaCron)).ok, false);
  assert.equal((await programador.ponerProgramacion("flujo-a", { activo: true }, flujos.problemaCron)).ok, false);

  let pedidos = 0;
  let vivas = 1;
  const avisos: string[] = [];
  const cortes: string[][] = [];
  const deps = {
    flujos: () => [{ id: "pqrd", tipo: "codigo" }, { id: "tutelas", tipo: "codigo" }] as unknown as ReturnType<typeof flujos.listar>,
    enCurso: () => 0,
    disparar: () => assert.fail("un flujo de código no se dispara como declarativo"),
    ordenDeSondeo: () => null,
    aviso: (msg: string) => void avisos.push(msg),
    correrBuzon: async (f: string) => { pedidos++; return [`${f}-corrida-${pedidos}`]; },
    vivas: () => vivas,
    cortar: (_f: string, ids: string[]) => void cortes.push(ids),
  };
  const esperar = () => new Promise((ok) => setImmediate(ok));
  // apagada: no corre aunque toque
  programador.tic(deps, new Date("2026-11-09T12:30:00Z"));
  await esperar();
  assert.equal(pedidos, 0);
  // encendida: corre al toque del cron (07:30 en Bogotá)
  assert.equal((await programador.ponerProgramacion("pqrd", { activo: true }, flujos.problemaCron)).ok, true);
  programador.tic(deps, new Date("2026-11-09T12:31:00Z"));  // 07:31 no toca
  await esperar();
  assert.equal(pedidos, 0);
  programador.tic(deps, new Date("2026-11-09T13:00:00Z"));
  await esperar();
  assert.equal(pedidos, 1);
  assert.equal(programador.corridaProgramada("pqrd")?.corridas, 1);
  // el mismo minuto no se repite y, mientras la anterior siga, la siguiente se salta
  programador.tic(deps, new Date("2026-11-09T13:00:20Z"));
  programador.tic(deps, new Date("2026-11-09T13:30:00Z"));
  await esperar();
  assert.equal(pedidos, 1);
  assert.ok(avisos.some((a) => a.includes("sigue")));
  // terminó: el toque siguiente sí corre
  vivas = 0;
  programador.tic(deps, new Date("2026-11-09T14:00:00Z"));
  await esperar();
  assert.equal(pedidos, 2);
  // tutelas apagada por defecto; encendida, toca a las 07:05 y no a las 18:05
  vivas = 0;
  assert.equal((await programador.ponerProgramacion("tutelas", { activo: true }, flujos.problemaCron)).ok, true);
  programador.tic(deps, new Date("2026-11-09T23:05:00Z"));
  await esperar();
  assert.equal(pedidos, 2);
  programador.tic(deps, new Date("2026-11-09T12:05:00Z"));
  await esperar();
  assert.equal(pedidos, 3);
  assert.equal(programador.TOPE_DE_CORRIDA_S, 3600);
  assert.equal(cortes.length, 0);
  programador.reiniciarCodigo();
  await programador.ponerProgramacion("pqrd", { activo: false }, flujos.problemaCron);
  await programador.ponerProgramacion("tutelas", { activo: false }, flujos.problemaCron);
});
