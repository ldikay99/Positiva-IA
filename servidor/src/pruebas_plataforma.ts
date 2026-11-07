/**
 * Pruebas del servidor de la plataforma de automatización: credenciales (cifrado,
 * alcance, nada de valores en la API), entorno por flujo (secretos, confirmación,
 * migración), manifiesto, orden de una corrida y cron. Sin red.
 *
 *   node --test servidor/dist/pruebas_plataforma.js      (en el contenedor: /app/servidor/dist)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

process.env.RUTA_DATOS = mkdtempSync(join(tmpdir(), "positiva-prueba-"));
// el espacio de archivos, la bandeja de PQRD (universo) y lo traído del buzón: todo de prueba
process.env.RUTA_ARCHIVOS = mkdtempSync(join(tmpdir(), "positiva-archivos-"));
process.env.RUTA_BANDEJA = mkdtempSync(join(tmpdir(), "positiva-bandeja-"));
process.env.RUTA_VIVO = mkdtempSync(join(tmpdir(), "positiva-vivo-"));
delete process.env.CLAVE_CREDENCIALES;
// la vigilancia de los trabajadores (topes de tiempo, memoria) en décimas, para no esperar en las pruebas
process.env.MOTOR_VIGILANCIA_MS = "150";
process.env.MOTOR_GRACIA_MS = "0";

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

// ------------------------------------- cada flujo independiente: correspondencia, archivos, copias y borrado

const bandeja = await import("./bandeja.ts");
const espacio = await import("./espacio.ts");
const motorMod = await import("./motor.ts");

function caso(base: string, nombre: string, extra: Record<string, unknown> = {}): void {
  mkdirSync(join(base, nombre, "adj"), { recursive: true });
  writeFileSync(join(base, nombre, "mensaje.json"), JSON.stringify({ subject: `Asunto ${nombre}`, receivedDateTime: "2026-11-05T10:00:00Z", from: { emailAddress: { name: "X", address: "x@ejemplo.test" } }, body: { contentType: "text", content: "hola" }, _adjuntos: [], ...extra }));
}

test("correspondencia por flujo: un flujo nuevo nunca ve la bandeja de PQRD", async () => {
  caso(process.env.RUTA_BANDEJA!, "universo-1");
  caso(process.env.RUTA_VIVO!, "vivo-pqrd-1", { _vivo: true });
  caso(process.env.RUTA_VIVO!, "vivo-tutela-1", { _vivo: true, _flujo: "tutelas" });
  caso(join(process.env.RUTA_ARCHIVOS!, "flujo-nuevo", "bandeja"), "prueba-abc", { _origen: "bandeja_de_prueba" });
  caso(join(process.env.RUTA_ARCHIVOS!, "flujo-nuevo", "bandeja"), "buzon-def", { _origen: "buzon_del_flujo:flujo-nuevo" });
  caso(join(process.env.RUTA_ARCHIVOS!, "otro-flujo", "bandeja"), "prueba-ajena");
  bandeja.olvidarDe();
  const ver = async (f: Parameters<typeof bandeja.listarDe>[0]) => (await bandeja.listarDe(f)).map((c) => `${c.caso}:${c.fuente}`).sort();
  assert.deepEqual(await ver({ id: "pqrd", tipo: "codigo", base: "pqrd" }), ["universo-1:universo", "vivo-pqrd-1:buzon_real"]);
  assert.deepEqual(await ver({ id: "copia-pqrd", tipo: "codigo", base: "pqrd" }), ["universo-1:universo", "vivo-pqrd-1:buzon_real"]);
  assert.deepEqual(await ver({ id: "tutelas", tipo: "codigo", base: "tutelas" }), ["vivo-tutela-1:buzon_real"]);
  assert.deepEqual(await ver({ id: "flujo-nuevo", tipo: "declarativo" }), ["buzon-def:buzon_flujo", "prueba-abc:prueba"]);
  const nuevo = { id: "flujo-nuevo", tipo: "declarativo" as const };
  assert.equal(await bandeja.esDe(nuevo, "universo-1"), false, "el universo de PQRD no es suyo");
  assert.equal(await bandeja.esDe(nuevo, "prueba-ajena"), false, "lo de otro flujo tampoco");
  assert.equal(await bandeja.esDe(nuevo, "prueba-abc"), true);
  assert.equal(await bandeja.detalleDe(nuevo, "universo-1"), null);
  assert.equal(await bandeja.rutaAdjuntoDe(nuevo, "prueba-abc", "../../otro-flujo/bandeja/x"), null);
  assert.equal(await bandeja.esDe({ id: "tutelas", tipo: "codigo", base: "tutelas" }, "vivo-pqrd-1"), false);
});

/** Unos bytes con la firma de un formato (lo demás, relleno). */
const firma = (inicio: number[] | string, n = 64) => Buffer.concat([typeof inicio === "string" ? Buffer.from(inicio, "latin1") : Buffer.from(inicio), Buffer.alloc(n, 0x20)]);

test("archivos: video y audio rechazados por extensión, por tipo y por su contenido", async () => {
  const reglas = espacio.reglasDe(undefined);
  const malo = (nombre: string, tipo: string, d: Buffer) => assert.ok(espacio.problemaDeArchivo(nombre, tipo, d, reglas), `${nombre} debió rechazarse`);
  malo("clase.mp4", "video/mp4", firma("....ftypisom"));
  malo("nota.mp3", "", firma("ID3"));
  malo("informe.pdf", "application/pdf", firma([0, 0, 0, 0x18, ...Buffer.from("ftypmp42")]));  // un MP4 disfrazado de PDF
  malo("foto.png", "image/png", firma("RIFF\x00\x00\x00\x00WAVEfmt "));                      // un WAV disfrazado
  malo("voz.jpg", "image/jpeg", firma([0xff, 0xfb, 0x90, 0x00]));                             // trama de MP3
  malo("pelicula.webm", "", firma([0x1a, 0x45, 0xdf, 0xa3]));
  malo("algo.txt", "audio/ogg", firma("hola"));
  malo("programa.exe", "application/octet-stream", firma("MZ"));                              // fuera de los tipos permitidos
  const bien = (nombre: string, tipo: string, d: Buffer) => assert.equal(espacio.problemaDeArchivo(nombre, tipo, d, reglas), null, nombre);
  bien("acta.pdf", "application/pdf", firma("%PDF-1.7"));
  bien("foto.heic", "image/heic", firma([0, 0, 0, 0x18, ...Buffer.from("ftypheic")]));        // HEIC también es «ftyp», pero es imagen
  bien("escaneo.JPG", "image/jpeg", firma([0xff, 0xd8, 0xff, 0xe0]));
  bien("notas.txt", "text/plain", Buffer.from("﻿hola", "utf16le"));                       // UTF-16 (FF FE) no es audio
  const solo = espacio.reglasDe({ nodos: [{ tipo: "entrada_archivos", config: { tipos_permitidos: ["pdf", "mp4"], max_mb: 1 } }] });
  assert.deepEqual([...solo.tipos], ["pdf"], "video nunca entra aunque el flujo lo pida");
  assert.ok(espacio.problemaDeArchivo("grande.pdf", "application/pdf", Buffer.concat([firma("%PDF"), Buffer.alloc(1024 * 1024 + 1)]), solo));
  assert.ok(espacio.problemaDeArchivo("foto.png", "image/png", firma([0x89, 0x50]), solo), "tipo fuera de los permitidos");
});

test("archivos: se guardan como objetos del flujo y solo ese flujo los resuelve", async () => {
  const a = await espacio.guardarArchivo("flujo-nuevo", "Certificación médica.pdf", "application/pdf", firma("%PDF-1.4"));
  assert.match(a.id, /^a-[0-9a-f]{18}$/);
  assert.equal(a.nombre, "Certificación médica.pdf");
  assert.equal(a.sha256.length, 64);
  assert.ok(existsSync(join(process.env.RUTA_ARCHIVOS!, "flujo-nuevo", "entradas", a.id, "Certificación médica.pdf")));
  assert.equal(espacio.nombreSeguro("../../etc/passwd"), "passwd");
  const defi = { nodos: [{ tipo: "entrada_manual", config: { campos: [{ nombre: "soporte", tipo: "archivo" }] } }] };
  assert.deepEqual(espacio.resolverArchivos("flujo-nuevo", { archivos: [a.id] }, defi).map((x) => x.nombre), ["Certificación médica.pdf"]);
  assert.deepEqual(espacio.resolverArchivos("flujo-nuevo", { soporte: a.id }, defi).map((x) => x.id), [a.id]);
  assert.deepEqual(espacio.resolverArchivos("otro-flujo", { archivos: [a.id] }), [], "otro flujo no lo ve");
  assert.deepEqual(espacio.archivosQueFaltan("flujo-nuevo", { archivos: [a.id, "a-noexiste000"] }), ["a-noexiste000"]);
  // multipart: campos y archivos (nombre con tildes)
  const limite = "----limite123";
  const cuerpo = Buffer.concat([
    Buffer.from(`--${limite}\r\nContent-Disposition: form-data; name="nota"\r\n\r\nurgente\r\n`),
    Buffer.from(`--${limite}\r\nContent-Disposition: form-data; name="archivo"; filename="Cédula.png"\r\nContent-Type: image/png\r\n\r\n`), Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a]),
    Buffer.from(`\r\n--${limite}--\r\n`),
  ]);
  const partes = espacio.multipart(cuerpo, `multipart/form-data; boundary=${limite}`);
  assert.deepEqual(partes.map((p) => [p.campo, p.archivo, p.tipo, p.datos.length]), [["nota", undefined, "text/plain", 7], ["archivo", "Cédula.png", "image/png", 6]]);
});

test("clonar: declarativo y de código, cada copia independiente", async () => {
  await flujos.cargar();
  const d = {
    id: "con-archivos", nombre: "Con archivos", modelo: "gemini-2.5-flash",
    nodos: [{ id: "inicio", tipo: "entrada_archivos", nombre: "Archivos", config: { max_archivos: 3 } }, { id: "fin", tipo: "salida", nombre: "Fin", config: { desenlace: "manual" } }],
    aristas: [{ desde: "inicio", hacia: "fin" }], disenio: { inicio: { x: 10, y: 20 }, fin: { x: 300, y: 20 } },
  };
  assert.equal((await entornos.poner("con-archivos", "REGION", { valor: "caribe" }, false)).ok, true);
  assert.equal((await entornos.poner("con-archivos", "CLAVE_API", { valor: TOKEN, secreto: true }, true)).ok, true);
  const g = await flujos.guardar(d, "primera");
  assert.equal(g.ok, true, g.problemas.join("\n"));
  assert.equal(g.flujo!.disparador, "entrada_archivos");
  assert.equal(g.flujo!.correspondencia, false, "un flujo de archivos no tiene correspondencia");
  const c = await flujos.clonar("con-archivos", "Con archivos (copia)");
  assert.equal(c.ok, true, JSON.stringify(c));
  const copia = c.ok ? c.flujo : null!;
  assert.equal(copia.id, "con-archivos-copia");
  assert.deepEqual(copia.definicion!.disenio, d.disenio, "el diseño va con la copia");
  const ent = await entornos.listar(copia.id);
  assert.deepEqual(ent.map((v) => [v.nombre, v.secreto, v.definido]), [["CLAVE_API", true, false], ["REGION", false, true]], "el secreto queda declarado sin valor");
  assert.equal(entornos.paraCorrida(copia.id).valores.CLAVE_API, undefined);

  // pqrd: una instancia propia del mismo motor
  assert.equal((await entornos.poner("pqrd", "OPENAI_API_KEY", { valor: SECRETO_JOB, secreto: true }, true)).ok, true);
  const k = await flujos.clonar("pqrd", "PQRD de pruebas");
  assert.equal(k.ok, true, JSON.stringify(k));
  const pq = k.ok ? k.flujo : null!;
  assert.equal(pq.tipo, "codigo");
  assert.equal(pq.base, "pqrd");
  assert.equal(pq.copia_de, "pqrd");
  assert.ok(flujos.esDeCodigo(pq.id) && !flujos.esOriginal(pq.id));
  assert.ok(existsSync(join(process.env.RUTA_DATOS!, "flujos_codigo", `${pq.id}.json`)));
  assert.equal(flujos.catalogoDe(pq.id) !== null, true);
  // su entorno: copiado (el secreto cifrado, nunca en claro en disco) y marcado
  const e2 = await entornos.listar(pq.id);
  assert.ok(e2.find((v) => v.nombre === "OPENAI_API_KEY")?.copiado_de === "pqrd");
  assert.equal(entornos.paraCorrida(pq.id).valores.OPENAI_API_KEY, SECRETO_JOB);
  assert.ok(!readFileSync(join(process.env.RUTA_DATOS!, "entornos", `${pq.id}.json`), "utf8").includes(SECRETO_JOB));
  // cambiarlo en la copia no toca el original
  assert.equal((await entornos.poner(pq.id, "OPENAI_API_KEY", { valor: "otro-valor-de-la-copia", secreto: true }, true)).ok, true);
  assert.equal(entornos.paraCorrida("pqrd").valores.OPENAI_API_KEY, SECRETO_JOB);
  // los nombres del job valen en la copia (es el mismo motor)
  assert.equal(entornos.problemaDeNombre(pq.id, "OUTLOOK_FOLDER_ID"), null);
  // su compuerta: propia y apagada
  assert.equal(compuerta.enlace.tieneCompuerta(pq.id), true);
  assert.equal(compuerta.obtener(pq.id).radicacion_real, false);
  assert.equal(compuerta.radicacionReal(pq.id), false);
  // su programación: la del job, apagada, y la suya aparte
  assert.equal(programador.programacionDe(pq.id)?.activo, false);
  assert.equal((await programador.ponerProgramacion(pq.id, { activo: true }, flujos.problemaCron)).ok, true);
  assert.equal(programador.programacionDe("pqrd")?.activo, false, "la del original no cambia");
  await programador.ponerProgramacion(pq.id, { activo: false }, flujos.problemaCron);
  // renombrar: la copia y también el original (solo su nombre en la plataforma)
  assert.equal((await flujos.renombrar(pq.id, { nombre: "PQRD de ensayo" })).ok, true);
  assert.equal(flujos.obtener(pq.id)?.nombre, "PQRD de ensayo");
  assert.equal((await flujos.renombrar("tutelas", { nombre: "Tutelas en producción" })).ok, true);
  assert.equal(flujos.obtener("tutelas")?.nombre, "Tutelas en producción");
  await flujos.renombrar("tutelas", { nombre: "Radicación de tutelas" });
  // tutelas también se clona y su copia lee lo de tutelas
  const t = await flujos.clonar("tutelas", "Tutelas B");
  assert.equal(t.ok && t.flujo.base, "tutelas");
  // las corridas son de cada uno
  const orden = flujos.ordenPara(pq.id, { id: "x", flujo: pq.id, caso: "c", estado: "en_cola", creada: 0, eventos: [], configuracion: { entorno: {} } } as unknown as Parameters<typeof flujos.ordenPara>[1]);
  assert.ok("entorno" in orden);
  // al recargar, las copias siguen ahí
  await flujos.cargar();
  assert.ok(flujos.obtener(pq.id) && flujos.obtener(t.ok ? t.flujo.id : "")?.base === "tutelas");
});

test("borrar: los originales no; una copia se va con sus corridas, su entorno y sus archivos", async () => {
  const carpeta = join(process.env.RUTA_DATOS!, "corridas");
  mkdirSync(carpeta, { recursive: true });
  writeFileSync(join(carpeta, "2026-11-05.jsonl"), [
    JSON.stringify({ id: "1", flujo: "con-archivos-copia", caso: "a", eventos: [] }),
    JSON.stringify({ id: "2", flujo: "pqrd", caso: "b", eventos: [] }),
    JSON.stringify({ id: "3", caso: "viejo-sin-flujo", eventos: [] }),
    JSON.stringify({ id: "4", flujo: "con-archivos-copia", caso: "c", eventos: [] }),
  ].join("\n") + "\n");
  assert.equal(await motorMod.contarCorridasDe("con-archivos-copia"), 2);
  await espacio.guardarArchivo("con-archivos-copia", "a.pdf", "application/pdf", firma("%PDF"));
  assert.equal((await espacio.uso("con-archivos-copia")).archivos, 1);
  assert.equal(await flujos.borrar("pqrd"), false, "pqrd no se borra");
  assert.equal(await flujos.borrar("con-archivos-copia"), true);
  assert.equal(await motorMod.borrarCorridasDe("con-archivos-copia"), 2);
  await espacio.borrarTodo("con-archivos-copia");
  const quedan = readFileSync(join(carpeta, "2026-11-05.jsonl"), "utf8").trim().split("\n").map((l) => (JSON.parse(l) as { id: string }).id);
  assert.deepEqual(quedan, ["2", "3"], "las de pqrd y las viejas (de pqrd) quedan");
  assert.equal(existsSync(join(process.env.RUTA_ARCHIVOS!, "con-archivos-copia")), false);
  assert.deepEqual(await entornos.listar("con-archivos-copia"), []);
  assert.equal(flujos.obtener("con-archivos-copia"), undefined);
  // una copia de código también se borra y no deja registro
  const copia = flujos.listar().find((f) => f.copia_de === "pqrd")!;
  await compuerta.olvidar(copia.id);
  await programador.olvidar(copia.id);
  assert.equal(await flujos.borrar(copia.id), true);
  assert.equal(existsSync(join(process.env.RUTA_DATOS!, "flujos_codigo", `${copia.id}.json`)), false);
  assert.equal(flujos.esDeCodigo(copia.id), false);
  assert.equal(compuerta.enlace.tieneCompuerta(copia.id), false);
});

// ------------------------------------- recursos: límites por flujo, reparto justo y trabajadores trabados

const recursosMod = await import("./recursos.ts");
const { repartoJusto } = await import("./planificador.ts");

/** Un trabajador de mentira: cada caso «nombre_segundos» duerme eso; «trabado…» no termina nunca. */
function trabajadorDePrueba(): string {
  const dir = mkdtempSync(join(tmpdir(), "positiva-pool-"));
  const script = join(dir, "trabajador_pool.py");
  writeFileSync(script, [
    "import json, sys, time",
    "M = '\\x1eEVT '",
    "def ev(**d): print(M + json.dumps(d), flush=True)",
    "ev(tipo='listo')",
    "for linea in sys.stdin:",
    "    o = json.loads(linea)",
    "    c, caso = o['corrida'], o['caso']",
    "    ev(tipo='inicio', corrida=c, tiempo_max_s=o.get('tiempo_max_s'))",
    "    time.sleep(10000 if caso.startswith('trabado') else float(caso.rsplit('_', 1)[1]) if '_' in caso else 0.2)",
    "    ev(tipo='resultado', corrida=c, caso=caso, accion='manual', motivo='ok')",
    "    ev(tipo='libre')",
  ].join("\n"), "utf8");
  return script;
}

const confDePrueba = { parametros: {}, entorno: {}, ia: { modelo: "x" }, topeSegundos: 60, reglas: [] } as unknown as Parameters<InstanceType<typeof Motor>["encolar"]>[1];
const esperarQue = async (cond: () => boolean, ms = 20000) => {
  const fin = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > fin) throw new Error("no se cumplió a tiempo");
    await new Promise((r) => setTimeout(r, 30));
  }
};

test("reparto justo: el tope se reparte por igual y lo que uno no usa pasa a los demás", () => {
  const r = repartoJusto(8, [{ id: "a", quiere: 20, espera: 1 }, { id: "b", quiere: 3, espera: 5 }, { id: "c", quiere: 1, espera: 0 }]);
  assert.deepEqual(Object.fromEntries(r), { a: 4, b: 3, c: 1 });
  // sin demanda, nada; con menos tope que flujos, primero el que más espera
  assert.deepEqual(Object.fromEntries(repartoJusto(2, [{ id: "a", quiere: 5, espera: 10 }, { id: "b", quiere: 5, espera: 30 }, { id: "c", quiere: 5, espera: 20 }, { id: "d", quiere: 0, espera: 99 }])), { a: 0, b: 1, c: 1, d: 0 });
  assert.deepEqual(Object.fromEntries(repartoJusto(4, [{ id: "a", quiere: 9, espera: 0 }])), { a: 4 }, "solo, se lleva todo el tope");
});

test("recursos por flujo: por defecto, propios, topes y vuelta al defecto con null", async () => {
  await recursosMod.cargar();
  const decl = { id: "flujo-r", tipo: "declarativo" as const };
  assert.deepEqual((({ trabajadores, corridas_simultaneas, cola_max, tiempo_max_s }) => ({ trabajadores, corridas_simultaneas, cola_max, tiempo_max_s }))(recursosMod.de(decl)), { trabajadores: 2, corridas_simultaneas: 2, cola_max: 50, tiempo_max_s: 1800 });
  assert.equal(recursosMod.de({ id: "tutelas", tipo: "codigo", base: "tutelas" }).tiempo_max_s, 3600);
  const tope = recursosMod.topeGlobal();
  assert.equal((await recursosMod.poner(decl, { trabajadores: tope + 1 })).ok, false, "no pasa del tope global");
  assert.equal((await recursosMod.poner(decl, { trabajadores: 2, corridas_simultaneas: 3 })).ok, false, "no más corridas a la vez que trabajadores");
  assert.equal((await recursosMod.poner(decl, { tiempo_max_s: 5 })).ok, false);
  assert.equal((await recursosMod.poner(decl, { raro: 1 })).ok, false);
  const r = await recursosMod.poner(decl, { trabajadores: 3, cola_max: 7, tiempo_max_s: 90 });
  assert.equal(r.ok && r.recursos.cola_max, 7);
  assert.deepEqual(r.ok && r.recursos.propios.sort(), ["cola_max", "tiempo_max_s", "trabajadores"]);
  assert.ok(readFileSync(join(process.env.RUTA_DATOS!, "recursos.json"), "utf8").includes("flujo-r"), "se guarda en el estado");
  await recursosMod.cargar();
  assert.equal(recursosMod.de(decl).trabajadores, 3, "sobrevive a un reinicio");
  const v = await recursosMod.poner(decl, { cola_max: null });
  assert.equal(v.ok && v.recursos.cola_max, 50, "null vuelve al valor por defecto");
  await recursosMod.olvidar("flujo-r");
  assert.equal(recursosMod.de(decl).trabajadores, 2);
});

test("límites de un flujo: cola_max rechaza lo que no cabe y nunca hay más corridas a la vez que las suyas", async () => {
  const script = trabajadorDePrueba();
  const m = new Motor("limites", script);
  m.recursos = () => ({ trabajadores: 3, corridas_simultaneas: 2, cola_max: 5, tiempo_max_s: 60 });
  let max = 0;
  m.on("evento", () => { max = Math.max(max, m.estado().trabajadores.filter((t) => t.corrida).length); });
  const cs: ReturnType<typeof m.encolar>[] = [];
  for (let i = 0; i < 5; i++) {
    assert.ok(m.cabe(1), `cabe la ${i + 1}`);
    cs.push(m.encolar(`l${i}_0.3`, confDePrueba));
  }
  // ya hay 5 esperando: la sexta no cabe hasta que empiece alguna
  assert.equal(m.cabe(1), false);
  await esperarQue(() => cs.every((c) => c.estado === "terminada"));
  m.detener();
  assert.ok(max <= 2, `a lo más 2 a la vez (hubo ${max})`);
  assert.equal(cs[0]!.eventos.find((e) => e.tipo === "inicio")?.tiempo_max_s, 60, "el tiempo máximo va en la orden");
});

test("reparto justo entre flujos: uno que inunda su cola no se lleva todos los trabajadores", async () => {
  const script = trabajadorDePrueba();
  const tope = motorMod.planificador.tope;
  motorMod.planificador.tope = () => 4;
  try {
    const a = new Motor("inunda", script);
    const b = new Motor("llega-despues", script);
    for (const m of [a, b]) m.recursos = () => ({ trabajadores: 4, corridas_simultaneas: 4, cola_max: 100, tiempo_max_s: 60 });
    let maxVivos = 0;
    const asignadas: { flujo: string; t: number }[] = [];
    for (const m of [a, b]) m.on("evento", (e: { tipo: string }) => {
      maxVivos = Math.max(maxVivos, motorMod.planificador.vivos());
      if (e.tipo === "asignada") asignadas.push({ flujo: m.flujo, t: Date.now() });
    });
    const deA = Array.from({ length: 12 }, (_, i) => a.encolar(`a${i}_1.0`, confDePrueba));
    await esperarQue(() => a.estado().trabajadores.filter((t) => t.corrida).length === 4);
    const deB = Array.from({ length: 4 }, (_, i) => b.encolar(`b${i}_1.0`, confDePrueba));
    await esperarQue(() => [...deA, ...deB].every((c) => c.estado === "terminada"), 40000);
    a.detener();
    b.detener();
    const ultimaDeA = Math.max(...asignadas.filter((x) => x.flujo === "inunda").map((x) => x.t));
    const finDeB = Math.max(...deB.map((c) => c.fin!));
    assert.ok(maxVivos <= 4, `nunca más procesos que el tope (hubo ${maxVivos})`);
    assert.ok(finDeB < ultimaDeA, "las 4 del que llegó después terminan antes de que el que inundó empiece su última");
    // mientras los dos tenían trabajo, cada uno tuvo su mitad
    const durante = deB.map((c) => c.inicio!);
    assert.ok(Math.max(...durante) - Math.min(...durante) < 3500, "las de B no esperan a que A vacíe su cola");
  } finally {
    motorMod.planificador.tope = tope;
  }
});

test("trabajador trabado: pasado su tiempo máximo se detiene, su corrida queda en error y otro atiende la cola", async () => {
  const script = trabajadorDePrueba();
  const m = new Motor("trabados", script);
  m.recursos = () => ({ trabajadores: 1, corridas_simultaneas: 1, cola_max: 10, tiempo_max_s: 1 });
  const trabada = m.encolar("trabado-1", confDePrueba);
  const siguiente = m.encolar("normal_0.1", confDePrueba);
  await esperarQue(() => siguiente.estado === "terminada", 20000);
  assert.equal(trabada.estado, "fallida");
  assert.match(String(trabada.resultado?.motivo), /tiempo máximo de 1 s/);
  assert.ok(m.incidentes.some((x) => /tiempo máximo/.test(x.que)));
  // reiniciar a mano uno ocupado: su corrida queda en error con el motivo y el siguiente sigue
  m.recursos = () => ({ trabajadores: 1, corridas_simultaneas: 1, cola_max: 10, tiempo_max_s: 600 });
  const otra = m.encolar("trabado-2", confDePrueba);
  const despues = m.encolar("normal_0.1", confDePrueba);
  await esperarQue(() => otra.estado === "procesando");
  const t = m.estado().trabajadores.find((x) => x.corrida === otra.id)!;
  assert.equal(m.reiniciarTrabajador(t.id, "reiniciado en la prueba"), true);
  await esperarQue(() => despues.estado === "terminada", 20000);
  assert.equal(otra.resultado?.motivo, "reiniciado en la prueba");
  m.detener();
});

test("salud: cada trabajador trae su CPU y su memoria (en Linux) y las alertas son suaves", async () => {
  const script = trabajadorDePrueba();
  const m = new Motor("medido", script);
  m.recursos = () => ({ trabajadores: 1, corridas_simultaneas: 1, cola_max: 10, tiempo_max_s: 60 });
  const c = m.encolar("medir_1.0", confDePrueba);
  await esperarQue(() => c.estado === "terminada");
  const e = m.estado();
  m.detener();
  assert.equal(e.trabajadores.length, 1);
  if (process.platform === "linux") {
    assert.ok((e.trabajadores[0]!.memoria_mb ?? 0) > 0, JSON.stringify(e.trabajadores[0]));
    assert.ok(e.trabajadores[0]!.cpu !== undefined);
  }
  assert.deepEqual(e.trabajadores[0]!.alertas, []);
});
