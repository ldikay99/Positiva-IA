/**
 * Pruebas de las conexiones OAuth 2.0 (Google y Microsoft 365) contra un servidor de
 * autorización FALSO en 127.0.0.1 (no hay cuentas reales): consentimiento con PKCE (S256),
 * regreso con el código, tokens cifrados, estado de un solo uso que vence, renovación
 * (y rotación del token de Microsoft), revocación, cuentas de Positiva rechazadas, y las
 * reglas de las credenciales nuevas (http_oauth2, api_openapi con red_privada).
 *
 *   node --test servidor/dist/pruebas_oauth.js      (en el contenedor: /app/servidor/dist)
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer, type IncomingMessage } from "node:http";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { AddressInfo } from "node:net";
import Fastify from "fastify";

process.env.RUTA_DATOS = mkdtempSync(join(tmpdir(), "positiva-oauth-"));
delete process.env.CLAVE_CREDENCIALES;
process.env.URL_PUBLICA = "http://localhost:9999/";

// ------------------------------------------------- el servidor de autorización falso
const pedidos: { ruta: string; form: URLSearchParams; auth?: string }[] = [];
const estadoFalso = { cuentaGoogle: "ana@empresa.test", cuentaMicrosoft: "luis@empresa.test", rotar: 0 };
const leer = (req: IncomingMessage) => new Promise<string>((ok) => { let t = ""; req.on("data", (c) => (t += c)); req.on("end", () => ok(t)); });
const falso = createServer(async (req, res) => {
  const u = new URL(req.url ?? "/", "http://x");
  const form = new URLSearchParams(await leer(req));
  for (const [k, v] of u.searchParams) if (!form.has(k)) form.set(k, v);
  pedidos.push({ ruta: u.pathname, form, auth: req.headers.authorization });
  const json = (codigo: number, x: unknown) => { res.writeHead(codigo, { "Content-Type": "application/json" }); res.end(JSON.stringify(x)); };
  if (u.pathname === "/token" || /\/oauth2\/v2\.0\/token$/.test(u.pathname)) {
    const ms = u.pathname !== "/token";
    if (form.get("grant_type") === "authorization_code") {
      if (form.get("code") !== "codigo-bueno" || !form.get("code_verifier")) return json(400, { error: "invalid_grant", error_description: "código inválido" });
      return json(200, { access_token: `acceso-${ms ? "ms" : "g"}-1`, refresh_token: `renovar-${ms ? "ms" : "g"}-1`, expires_in: 3600, scope: ms ? "Mail.Send User.Read" : "openid email https://www.googleapis.com/auth/gmail.modify" });
    }
    if (form.get("grant_type") === "refresh_token") {
      estadoFalso.rotar++;
      return json(200, { access_token: `acceso-renovado-${estadoFalso.rotar}`, expires_in: 3600, ...(ms ? { refresh_token: `renovar-ms-rotado-${estadoFalso.rotar}` } : {}) });
    }
    return json(400, { error: "unsupported_grant_type" });
  }
  if (u.pathname === "/v1/userinfo") return json(200, { email: estadoFalso.cuentaGoogle });
  if (u.pathname === "/v1.0/me") return json(200, { mail: estadoFalso.cuentaMicrosoft, userPrincipalName: "otro@empresa.test" });
  if (u.pathname === "/revoke") return json(200, {});
  return json(404, { error: "no existe" });
});
await new Promise<void>((ok) => falso.listen(0, "127.0.0.1", () => ok()));
process.env.OAUTH_PRUEBAS = "si";
process.env.OAUTH_PRUEBAS_BASE = `http://127.0.0.1:${(falso.address() as AddressInfo).port}`;
after(() => falso.close());

const credenciales = await import("./credenciales.ts");
const oauth = await import("./oauth.ts");
await credenciales.cargar();
const app = Fastify();
await oauth.registrar(app);
after(() => app.close());

const b64url = (b: Buffer) => b.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const SECRETO = "secreto-del-cliente-XYZ";
const disco = () => readFileSync(join(process.env.RUTA_DATOS!, "credenciales.json"), "utf8");

test("Google: consentimiento con PKCE, regreso, tokens cifrados y estado de un solo uso", async () => {
  assert.equal((await credenciales.guardar("gmail-ops", { tipo: "google_oauth", valores: { client_id: "cliente.apps.googleusercontent.com", client_secret: SECRETO } }, true)).ok, true);
  const r = await app.inject({ method: "GET", url: "/api/oauth/gmail-ops/iniciar" });
  assert.equal(r.statusCode, 302);
  const u = new URL(String(r.headers.location));
  assert.ok(u.pathname.endsWith("/o/oauth2/v2/auth"));
  assert.equal(u.searchParams.get("code_challenge_method"), "S256");
  assert.equal(u.searchParams.get("redirect_uri"), "http://localhost:9999/api/oauth/callback");
  assert.equal(u.searchParams.get("access_type"), "offline");
  const alcances = (u.searchParams.get("scope") ?? "").split(" ");
  assert.ok(alcances.includes("openid") && alcances.includes("https://www.googleapis.com/auth/gmail.modify") && alcances.includes("https://www.googleapis.com/auth/calendar"));
  const estado = u.searchParams.get("state")!;
  const reto = u.searchParams.get("code_challenge")!;
  const cb = await app.inject({ method: "GET", url: `/api/oauth/callback?code=codigo-bueno&state=${encodeURIComponent(estado)}` });
  assert.equal(cb.statusCode, 200, cb.body);
  assert.match(cb.body, /Conexión lista/);
  assert.match(cb.body, /postMessage/);
  // el verificador que llegó al proveedor corresponde al reto (S256)
  const verificador = pedidos.filter((p) => p.ruta === "/token").at(-1)!.form.get("code_verifier")!;
  assert.equal(b64url(createHash("sha256").update(verificador).digest()), reto);
  const v = credenciales.valoresDe("gmail-ops")!.valores;
  assert.equal(v.refresh_token, "renovar-g-1");
  assert.equal(v.cuenta, "ana@empresa.test");
  // cifrado en disco y nunca en la API
  for (const x of ["renovar-g-1", "acceso-g-1", SECRETO]) {
    assert.ok(!disco().includes(x), `${x} no va en claro en disco`);
    assert.ok(!JSON.stringify(credenciales.listar()).includes(x), `${x} no sale en listar`);
  }
  const c = credenciales.listar().find((x) => x.nombre === "gmail-ops")!;
  assert.equal(c.conexion?.conectada, true);
  assert.equal(c.conexion?.cuenta, "ana@empresa.test");
  assert.ok(!c.campos.includes("refresh_token"));
  assert.equal(c.creada_por_persona, true);
  // el mismo estado no sirve dos veces
  const otra = await app.inject({ method: "GET", url: `/api/oauth/callback?code=codigo-bueno&state=${encodeURIComponent(estado)}` });
  assert.equal(otra.statusCode, 400);
  assert.match(otra.body, /venció o ya se usó/);
  // un PUT no puede escribir los tokens; editar sin cambiar la aplicación los conserva
  assert.equal((await credenciales.guardar("gmail-ops", { tipo: "google_oauth", valores: { refresh_token: "inyectado" } })).ok, false);
  await credenciales.guardar("gmail-ops", { tipo: "google_oauth", valores: { client_id: "cliente.apps.googleusercontent.com" }, descripcion: "otra" }, true);
  assert.equal(credenciales.valoresDe("gmail-ops")!.valores.refresh_token, "renovar-g-1");
  // los tokens llegan a la corrida, marcados como secretos (el motor los enmascara)
  const pc = credenciales.paraCorrida("x", ["gmail-ops"])["gmail-ops"]!;
  assert.ok(pc.secretos.includes("refresh_token") && pc.secretos.includes("access_token") && pc.secretos.includes("client_secret"));
});

test("el estado vence a los 10 minutos", async () => {
  const r = oauth.urlDeConsentimiento("gmail-ops");
  assert.ok("url" in r);
  const estado = new URL(r.url).searchParams.get("state")!;
  const real = Date.now;
  Date.now = () => real() + 11 * 60_000;
  try {
    const x = await oauth.completar(estado, "codigo-bueno");
    assert.equal(x.ok, false);
  } finally {
    Date.now = real;
  }
});

test("renovación antes de vencer y revocación (Google)", async () => {
  await credenciales.actualizarInternos("gmail-ops", { expira: Date.now() + 60_000 });
  assert.deepEqual(await oauth.refrescarLasQueVencen(), ["gmail-ops"]);
  const v = credenciales.valoresDe("gmail-ops")!.valores;
  assert.match(String(v.access_token), /^acceso-renovado-/);
  assert.equal(v.refresh_token, "renovar-g-1");  // Google no rota
  assert.ok(Number(v.expira) > Date.now() + 50 * 60_000);
  assert.deepEqual(await oauth.refrescarLasQueVencen(), []);  // ya no vence pronto
  const p = await oauth.probar("gmail-ops");
  assert.equal(p.ok, true);
  assert.equal(p.cuenta, "ana@empresa.test");
  await credenciales.permitirEscritura("gmail-ops", true);
  const r = await app.inject({ method: "POST", url: "/api/oauth/gmail-ops/revocar" });
  assert.equal(r.statusCode, 200);
  assert.ok(pedidos.some((x) => x.ruta === "/revoke" && x.form.get("token") === "renovar-g-1"));
  const g = credenciales.listar().find((x) => x.nombre === "gmail-ops")!;
  assert.equal(g.permitir_escritura, false);
  assert.equal(g.conexion?.conectada, false);
  assert.equal(credenciales.valoresDe("gmail-ops")!.valores.refresh_token, undefined);
});

test("Microsoft: tenant, rotación del token de renovación y cambiar la aplicación borra los tokens", async () => {
  assert.equal((await credenciales.guardar("outlook-ops", { tipo: "microsoft_oauth", valores: { tenant: "empresa.onmicrosoft.com", client_id: "abc", client_secret: SECRETO } }, true)).ok, true);
  const r = oauth.urlDeConsentimiento("outlook-ops");
  assert.ok("url" in r);
  const u = new URL(r.url);
  assert.ok(u.pathname.endsWith("/empresa.onmicrosoft.com/oauth2/v2.0/authorize"));
  assert.ok((u.searchParams.get("scope") ?? "").split(" ").includes("offline_access"));
  const x = await oauth.completar(u.searchParams.get("state")!, "codigo-bueno");
  assert.equal(x.ok, true);
  assert.equal(credenciales.valoresDe("outlook-ops")!.valores.cuenta, "luis@empresa.test");  // mail antes que userPrincipalName
  await credenciales.actualizarInternos("outlook-ops", { expira: Date.now() });
  assert.equal((await oauth.refrescar("outlook-ops")).ok, true);
  assert.match(String(credenciales.valoresDe("outlook-ops")!.valores.refresh_token), /^renovar-ms-rotado-/);
  const rev = await oauth.revocar("outlook-ops");
  assert.match(String(rev.nota), /myapps\.microsoft\.com/);
  // reconectar y cambiar el client_id: los tokens se van
  const r2 = oauth.urlDeConsentimiento("outlook-ops");
  assert.ok("url" in r2);
  await oauth.completar(new URL(r2.url).searchParams.get("state")!, "codigo-bueno");
  await credenciales.guardar("outlook-ops", { tipo: "microsoft_oauth", valores: { tenant: "empresa.onmicrosoft.com", client_id: "otra-app" } }, true);
  assert.equal(credenciales.valoresDe("outlook-ops")!.valores.refresh_token, undefined);
});

test("una cuenta de Positiva nunca se conecta", async () => {
  estadoFalso.cuentaGoogle = "Funcionario@Positiva.gov.co";
  await credenciales.guardar("g-positiva", { tipo: "google_oauth", valores: { client_id: "c", client_secret: "s" } }, true);
  const r = oauth.urlDeConsentimiento("g-positiva");
  assert.ok("url" in r);
  const antes = pedidos.filter((p) => p.ruta === "/revoke").length;
  const cb = await app.inject({ method: "GET", url: `/api/oauth/callback?code=codigo-bueno&state=${encodeURIComponent(new URL(r.url).searchParams.get("state")!)}` });
  assert.equal(cb.statusCode, 400);
  assert.match(cb.body, /Positiva/);
  assert.equal(credenciales.valoresDe("g-positiva")!.valores.refresh_token, undefined);
  assert.equal(pedidos.filter((p) => p.ruta === "/revoke").length, antes + 1);  // y se revocó
  estadoFalso.cuentaMicrosoft = "servicioalcliente@positiva.gov.co";
  await credenciales.guardar("m-positiva", { tipo: "microsoft_oauth", valores: { client_id: "c", client_secret: "s" } }, true);
  const r2 = oauth.urlDeConsentimiento("m-positiva");
  assert.ok("url" in r2);
  assert.equal((await oauth.completar(new URL(r2.url).searchParams.get("state")!, "codigo-bueno")).ok, false);
  assert.equal(oauth.cuentaVetada("ana@empresa.test"), null);
  // el error del proveedor también se muestra
  const neg = await app.inject({ method: "GET", url: "/api/oauth/callback?error=access_denied&state=nada" });
  assert.equal(neg.statusCode, 400);
});

test("iniciar con ?json=1, redirección registrable y tipos que no son OAuth", async () => {
  const r = await app.inject({ method: "GET", url: "/api/oauth/m-positiva/iniciar?json=1" });
  assert.equal(r.statusCode, 200);
  assert.ok(String(r.json().url).includes("code_challenge="));
  assert.equal((await app.inject({ method: "GET", url: "/api/oauth/redireccion" })).json().redirect_uri, "http://localhost:9999/api/oauth/callback");
  await credenciales.guardar("api-x", { tipo: "http_bearer", valores: { token: "t", hosts: "api.ejemplo.test" } });
  assert.equal((await app.inject({ method: "GET", url: "/api/oauth/api-x/iniciar" })).statusCode, 400);
  assert.equal((await app.inject({ method: "GET", url: "/api/oauth/no-existe/iniciar" })).statusCode, 404);
  assert.equal((await app.inject({ method: "POST", url: "/api/credenciales/no-existe/probar" })).statusCode, 404);
});

test("http_oauth2 y api_openapi: https, destinos vetados y red privada solo de una persona", async () => {
  const oauth2 = (token_url: string, hosts = "api.ejemplo.test") => credenciales.guardar("o2", { tipo: "http_oauth2", valores: { token_url, client_id: "c", client_secret: "s", hosts } });
  assert.equal((await oauth2("https://auth.ejemplo.test/token")).ok, true);
  assert.equal((await oauth2("http://auth.ejemplo.test/token")).ok, false);
  assert.equal((await oauth2("https://login.positiva.gov.co/token")).ok, false);
  assert.equal((await oauth2("https://auth.ejemplo.test/token", "api.positiva.gov.co")).ok, false);
  const api = (valores: Record<string, unknown>, persona = false) => credenciales.guardar("petstore", { tipo: "api_openapi", valores }, persona);
  assert.equal((await api({ base_url: "https://api.ejemplo.test" })).ok, false);  // sin especificación
  assert.equal((await api({ spec_url: "http://api.ejemplo.test/openapi.json" })).ok, false);  // solo https
  assert.equal((await api({ spec_url: "https://sgdea.ejemplo.test/openapi.json" })).ok, false);
  assert.equal((await api({ spec: "{}", base_url: "https://x.positiva.gov.co" })).ok, false);
  assert.equal((await api({ spec: "{}", auth: "bearer" })).ok, false);  // bearer sin token
  assert.equal((await api({ spec: "{}", red_privada: true })).ok, false);  // red privada sin persona
  const ok = await api({ spec: "{\"openapi\": \"3.0.0\"}", base_url: "http://10.0.0.5:8080", red_privada: true, auth: "api_key", token: "llave" }, true);
  assert.equal(ok.ok, true);
  const pc = credenciales.paraCorrida("x", ["petstore"]).petstore!;
  assert.equal(pc.creada_por_persona, true);
  assert.equal(pc.valores.red_privada, true);
  assert.ok(pc.secretos.includes("token"));
  // editada por Vera (sin la confirmación): ya no consta como de una persona y la red privada se rechaza
  assert.equal((await api({ spec: "{}", red_privada: true })).ok, false);
});
