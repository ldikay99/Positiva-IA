import type { FastifyInstance } from "fastify";
import { createHash, randomBytes } from "node:crypto";
import * as credenciales from "./credenciales.ts";
import { correrPython } from "./conectores.ts";

/**
 * Conexiones con OAuth 2.0 (Google y Microsoft 365): código de autorización con PKCE (S256),
 * renovación del token y revocación.
 *
 *   GET  /api/oauth/redireccion             → {redirect_uri} (lo que se registra en Google Cloud o Entra ID)
 *   GET  /api/oauth/:credencial/iniciar     → 302 a la pantalla de consentimiento ({url} con ?json=1)
 *   GET  /api/oauth/callback?code&state     → guarda los tokens cifrados en la credencial y muestra una página corta
 *   POST /api/oauth/:credencial/revocar     → revoca (Google) o descarta (Microsoft) y vuelve a solo lectura
 *   POST /api/credenciales/:nombre/probar   → prueba cualquier conexión (OAuth aquí; las demás con el motor de Python)
 *
 * El estado y el verificador de PKCE viven en memoria del servidor, 10 minutos y una sola vez.
 * Nunca se conecta una cuenta de Positiva ni un buzón protegido: los tokens se descartan.
 * Un temporizador renueva cada 5 minutos los tokens que vencen en menos de 10.
 */

const TTL_ESTADO_MS = 10 * 60_000;
const ANTES_DE_VENCER_MS = 10 * 60_000;
const BUZONES_PROTEGIDOS = new Set(["servicioalcliente@positiva.gov.co", "notificacionesjudiciales@positiva.gov.co"]);
const TENANT = /^[A-Za-z0-9.-]{2,80}$/;

/** Las pruebas reemplazan el host de cada servicio por un servidor falso; solo con OAUTH_PRUEBAS=si, nunca en producción. */
function punto(real: string): string {
  const base = process.env.OAUTH_PRUEBAS === "si" ? process.env.OAUTH_PRUEBAS_BASE : undefined;
  if (!base) return real;
  const u = new URL(real);
  return `${base.replace(/\/$/, "")}${u.pathname}${u.search}`;
}

export function redireccion(): string {
  return `${(process.env.URL_PUBLICA ?? "http://localhost:8095").replace(/\/+$/, "")}/api/oauth/callback`;
}

type Proveedor = "google" | "microsoft";
const proveedorDe = (tipo: string): Proveedor | null => (tipo === "google_oauth" ? "google" : tipo === "microsoft_oauth" ? "microsoft" : null);

function tenantDe(v: Record<string, unknown>): string {
  const t = String(v.tenant ?? "common").trim() || "common";
  if (!TENANT.test(t)) throw new Error("tenant inválido: common, organizations o el id / dominio del inquilino");
  return t;
}

/** Los alcances completos que se piden. */
export function alcances(p: Proveedor, texto: unknown): string[] {
  const cortos = String(texto ?? "").split(/[\s,]+/).filter(Boolean);
  if (p === "google") {
    const r = cortos.map((s) => (s.includes("://") || ["openid", "email", "profile"].includes(s) ? s : `https://www.googleapis.com/auth/${s}`));
    return [...new Set(["openid", "email", ...r])];
  }
  return [...new Set(["offline_access", "openid", "email", "User.Read", ...cortos])];
}

const urls = (p: Proveedor, tenant: string) => p === "google"
  ? { autorizar: punto("https://accounts.google.com/o/oauth2/v2/auth"), token: punto("https://oauth2.googleapis.com/token"),
      cuenta: punto("https://openidconnect.googleapis.com/v1/userinfo"), revocar: punto("https://oauth2.googleapis.com/revoke") }
  : { autorizar: punto(`https://login.microsoftonline.com/${tenant}/oauth2/v2.0/authorize`), token: punto(`https://login.microsoftonline.com/${tenant}/oauth2/v2.0/token`),
      cuenta: punto("https://graph.microsoft.com/v1.0/me"), revocar: "" };

/** Por qué no se puede conectar una cuenta, o null. */
export function cuentaVetada(correo: string): string | null {
  const c = correo.trim().toLowerCase();
  if (BUZONES_PROTEGIDOS.has(c)) return `${c} es un buzón de los jobs de PQRD o tutelas: solo se lee por el buzón de solo lectura de la plataforma`;
  if (/@([\w-]+\.)*positiva\.gov\.co$/.test(c)) return `${c} es una cuenta de Positiva: esta plataforma no se conecta a sus buzones ni a sus servicios`;
  return null;
}

// ------------------------------------------------------------------ estado (PKCE)

const estados = new Map<string, { credencial: string; verificador: string; vence: number; redirect: string }>();
const b64url = (b: Buffer) => b.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

function limpiarEstados(): void {
  const ahora = Date.now();
  for (const [k, v] of estados) if (v.vence < ahora) estados.delete(k);
}

/** La URL de consentimiento de una credencial OAuth (guarda su estado y su verificador, 10 minutos). */
export function urlDeConsentimiento(nombre: string): { url: string } | { error: string; codigo: number } {
  const c = credenciales.valoresDe(nombre);
  if (!c) return { error: "esa conexión no existe", codigo: 404 };
  const p = proveedorDe(c.tipo);
  if (!p) return { error: `la conexión «${nombre}» es ${c.tipo}: solo las google_oauth y microsoft_oauth se conectan con OAuth`, codigo: 400 };
  if (!c.valores.client_id || !c.valores.client_secret) return { error: "a la conexión le faltan client_id y client_secret", codigo: 400 };
  let tenant = "common";
  try {
    tenant = p === "microsoft" ? tenantDe(c.valores) : "common";
  } catch (e) {
    return { error: (e as Error).message, codigo: 400 };
  }
  limpiarEstados();
  const estado = b64url(randomBytes(24));
  const verificador = b64url(randomBytes(48));
  const reto = b64url(createHash("sha256").update(verificador).digest());
  const redirect = redireccion();
  estados.set(estado, { credencial: nombre, verificador, vence: Date.now() + TTL_ESTADO_MS, redirect });
  const q = new URLSearchParams({
    client_id: String(c.valores.client_id), redirect_uri: redirect, response_type: "code", scope: alcances(p, c.valores.scopes).join(" "),
    state: estado, code_challenge: reto, code_challenge_method: "S256",
    ...(p === "google" ? { access_type: "offline", prompt: "consent", include_granted_scopes: "true" } : { prompt: "select_account", response_mode: "query" }),
  });
  return { url: `${urls(p, tenant).autorizar}?${q.toString()}` };
}

// ------------------------------------------------------------------ tokens

interface RespuestaToken { access_token?: string; refresh_token?: string; expires_in?: number; scope?: string; error?: string; error_description?: string }

async function pedirToken(url: string, form: Record<string, string>): Promise<RespuestaToken> {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" }, body: new URLSearchParams(form), redirect: "manual", signal: AbortSignal.timeout(20_000) });
  let j: RespuestaToken = {};
  try {
    j = (await r.json()) as RespuestaToken;
  } catch {
    /* sin cuerpo JSON */
  }
  if (!r.ok || !j.access_token) {
    const detalle = j.error_description || j.error || `respondió ${r.status}`;
    throw new Error(`el proveedor no entregó el token: ${String(detalle).slice(0, 300)}`);
  }
  return j;
}

async function correoDe(p: Proveedor, tenant: string, token: string): Promise<string> {
  const r = await fetch(urls(p, tenant).cuenta, { headers: { Authorization: `Bearer ${token}`, Accept: "application/json" }, redirect: "manual", signal: AbortSignal.timeout(20_000) });
  if (!r.ok) throw new Error(`no se pudo leer la cuenta conectada (respondió ${r.status})`);
  const j = (await r.json()) as { email?: string; mail?: string; userPrincipalName?: string };
  return String((p === "google" ? j.email : j.mail || j.userPrincipalName) ?? "").trim().toLowerCase();
}

async function revocarGoogle(token: string): Promise<boolean> {
  try {
    const r = await fetch(`${urls("google", "common").revocar}?${new URLSearchParams({ token }).toString()}`, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, redirect: "manual", signal: AbortSignal.timeout(15_000) });
    return r.ok;
  } catch {
    return false;
  }
}

const VACIOS = { refresh_token: null, access_token: null, expira: null, cuenta: null, alcances: null, conectada: null };

/** Cambia el código por los tokens y los guarda cifrados. {ok, credencial, cuenta, alcances} o {ok: false, error}. */
export async function completar(estado: string, codigo: string): Promise<{ ok: true; credencial: string; cuenta: string; alcances: string[] } | { ok: false; error: string; credencial?: string }> {
  limpiarEstados();
  const e = estados.get(estado);
  estados.delete(estado);  // una sola vez, salga bien o mal
  if (!e) return { ok: false, error: "el pedido de conexión venció o ya se usó: vuelve a pulsar «Conectar»" };
  const c = credenciales.valoresDe(e.credencial);
  const p = c ? proveedorDe(c.tipo) : null;
  if (!c || !p) return { ok: false, error: "la conexión ya no existe", credencial: e.credencial };
  try {
    const tenant = p === "microsoft" ? tenantDe(c.valores) : "common";
    const pedidos = alcances(p, c.valores.scopes);
    const t = await pedirToken(urls(p, tenant).token, {
      grant_type: "authorization_code", code: codigo, redirect_uri: e.redirect, client_id: String(c.valores.client_id),
      client_secret: String(c.valores.client_secret), code_verifier: e.verificador, ...(p === "microsoft" ? { scope: pedidos.join(" ") } : {}),
    });
    const cuenta = await correoDe(p, tenant, t.access_token!);
    const vetada = cuenta ? cuentaVetada(cuenta) : "el proveedor no dijo qué cuenta se conectó";
    if (vetada) {
      if (p === "google") await revocarGoogle(t.refresh_token || t.access_token!);
      return { ok: false, error: `bloqueado por seguridad: ${vetada}. No se guardó nada.`, credencial: e.credencial };
    }
    const otraCuenta = typeof c.valores.cuenta === "string" && c.valores.cuenta && c.valores.cuenta !== cuenta;
    const refresh = t.refresh_token || (!otraCuenta && typeof c.valores.refresh_token === "string" ? c.valores.refresh_token : "");
    const otorgados = (t.scope ? t.scope.split(/\s+/) : pedidos).filter(Boolean);
    const ahora = Date.now();
    await credenciales.actualizarInternos(e.credencial, {
      access_token: t.access_token, refresh_token: refresh || null, expira: ahora + Math.max(60, Number(t.expires_in) || 3600) * 1000,
      cuenta, alcances: otorgados.join(" "), conectada: ahora,
    }, Boolean(otraCuenta));
    return { ok: true, credencial: e.credencial, cuenta, alcances: otorgados };
  } catch (err) {
    return { ok: false, error: (err as Error).message, credencial: e.credencial };
  }
}

const enCurso = new Map<string, Promise<{ ok: boolean; error?: string }>>();

/** Renueva el token de acceso de una conexión OAuth (Microsoft puede rotar el de renovación: se guarda el nuevo). */
export function refrescar(nombre: string): Promise<{ ok: boolean; error?: string }> {
  const ya = enCurso.get(nombre);
  if (ya) return ya;
  const p = (async () => {
    const c = credenciales.valoresDe(nombre);
    const prov = c ? proveedorDe(c.tipo) : null;
    if (!c || !prov) return { ok: false, error: "no es una conexión OAuth" };
    const refresh = c.valores.refresh_token;
    if (typeof refresh !== "string" || !refresh) return { ok: false, error: "la conexión no está conectada: pulsa «Conectar»" };
    try {
      const tenant = prov === "microsoft" ? tenantDe(c.valores) : "common";
      const t = await pedirToken(urls(prov, tenant).token, {
        grant_type: "refresh_token", refresh_token: refresh, client_id: String(c.valores.client_id), client_secret: String(c.valores.client_secret),
        ...(prov === "microsoft" ? { scope: alcances(prov, c.valores.scopes).join(" ") } : {}),
      });
      await credenciales.actualizarInternos(nombre, {
        access_token: t.access_token, expira: Date.now() + Math.max(60, Number(t.expires_in) || 3600) * 1000,
        ...(t.refresh_token && t.refresh_token !== refresh ? { refresh_token: t.refresh_token } : {}),
        ...(t.scope ? { alcances: t.scope } : {}),
      });
      return { ok: true };
    } catch (err) {
      return { ok: false, error: (err as Error).message };
    }
  })().finally(() => enCurso.delete(nombre));
  enCurso.set(nombre, p);
  return p;
}

/** Renueva las que vencen pronto (lo llama el temporizador; las pruebas lo llaman a mano). */
export async function refrescarLasQueVencen(ahora = Date.now()): Promise<string[]> {
  const hechas: string[] = [];
  for (const n of credenciales.nombresDeTipo(credenciales.TIPOS_OAUTH)) {
    const c = credenciales.valoresDe(n);
    if (!c?.valores.refresh_token) continue;
    const expira = Number(c.valores.expira) || 0;
    if (expira - ahora > ANTES_DE_VENCER_MS) continue;
    if ((await refrescar(n)).ok) hechas.push(n);
  }
  return hechas;
}

/** Desconecta: revoca en Google (Microsoft no tiene revocación por token) y borra los tokens; la escritura vuelve a apagarse. */
export async function revocar(nombre: string): Promise<{ ok: boolean; error?: string; codigo?: number; revocado?: boolean; nota?: string }> {
  const c = credenciales.valoresDe(nombre);
  const p = c ? proveedorDe(c.tipo) : null;
  if (!c) return { ok: false, error: "esa conexión no existe", codigo: 404 };
  if (!p) return { ok: false, error: "no es una conexión OAuth", codigo: 400 };
  let revocado = false;
  const token = String(c.valores.refresh_token || c.valores.access_token || "");
  if (p === "google" && token) revocado = await revocarGoogle(token);
  await credenciales.actualizarInternos(nombre, VACIOS, true);
  return {
    ok: true, revocado,
    nota: p === "microsoft"
      ? "Se borraron los tokens de la plataforma. Microsoft no revoca un token suelto: para quitar el consentimiento entra a https://myapps.microsoft.com (o pide a un administrador revocar las sesiones)."
      : revocado ? "Google revocó el acceso y se borraron los tokens." : "Se borraron los tokens; Google no confirmó la revocación (puedes quitar el acceso en https://myaccount.google.com/permissions).",
  };
}

/** Prueba una conexión: OAuth aquí (renueva si hace falta y lee la cuenta); las demás con el motor de Python. */
export async function probar(nombre: string): Promise<Record<string, unknown>> {
  const c = credenciales.valoresDe(nombre);
  if (!c) return { ok: false, error: "esa conexión no existe" };
  const p = proveedorDe(c.tipo);
  if (!p) {
    const r = await correrPython("probar", { tipo: c.tipo, valores: c.valores, creada_por_persona: c.creada_por_persona }, 60_000);
    return { ...r, tipo: c.tipo };
  }
  if (!c.valores.refresh_token && !c.valores.access_token) return { ok: false, tipo: c.tipo, error: "todavía no está conectada: pulsa «Conectar»" };
  if ((Number(c.valores.expira) || 0) - Date.now() < 60_000) {
    const r = await refrescar(nombre);
    if (!r.ok) return { ok: false, tipo: c.tipo, error: r.error };
  }
  const v = credenciales.valoresDe(nombre)!.valores;
  try {
    const tenant = p === "microsoft" ? tenantDe(v) : "common";
    const cuenta = await correoDe(p, tenant, String(v.access_token));
    const alc = String(v.alcances ?? "").split(/\s+/).filter(Boolean);
    return { ok: true, tipo: c.tipo, cuenta, alcances: alc, detalle: `Conectada como ${cuenta} · ${alc.length} permiso(s) · el token vence ${new Date(Number(v.expira)).toISOString()}` };
  } catch (err) {
    return { ok: false, tipo: c.tipo, error: (err as Error).message };
  }
}

// ------------------------------------------------------------------ página de regreso

const esc = (x: string) => x.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]!);

export function paginaDeRegreso(r: { ok: boolean; credencial?: string; cuenta?: string; alcances?: string[]; error?: string }): string {
  const datos = JSON.stringify({ tipo: "oauth", credencial: r.credencial ?? null, ok: r.ok, ...(r.error ? { error: r.error } : {}) }).replace(/</g, "\\u003c");
  const enlace = r.credencial ? `/conexiones/${encodeURIComponent(r.credencial)}` : "/conexiones";
  const permisos = (r.alcances ?? []).map((a) => a.replace("https://www.googleapis.com/auth/", "")).join(", ");
  const cuerpo = r.ok
    ? `<h1>Conexión lista</h1><p>Cuenta <b>${esc(r.cuenta ?? "")}</b>.</p><p class="t">Permisos: ${esc(permisos)}</p><p>Puedes cerrar esta pestaña y volver a la plataforma.</p>`
    : `<h1>No se pudo conectar</h1><p class="e">${esc(r.error ?? "")}</p><p>Cierra esta pestaña y vuelve a intentarlo desde Conexiones.</p>`;
  return `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${r.ok ? "Conexión lista" : "No se pudo conectar"}</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f8fd;color:#0b2a4a;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
main{background:#fff;border:1px solid #d6e4f5;border-radius:14px;padding:28px 30px;max-width:520px;width:100%;box-shadow:0 8px 28px rgba(0,82,163,.08)}
h1{font-size:20px;margin:0 0 10px;color:#0052a3}.t{color:#4a6688;font-size:13px;word-break:break-word}.e{color:#b42318}a{color:#0066cc;font-weight:600}</style></head>
<body><main>${cuerpo}<p><a href="${esc(enlace)}">Ir a la conexión</a></p></main>
<script>try{window.opener&&window.opener.postMessage(${datos},location.origin)}catch(e){}${r.ok ? "setTimeout(function(){try{window.close()}catch(e){}},1500);" : ""}</script></body></html>`;
}

// ------------------------------------------------------------------ rutas

let reloj: NodeJS.Timeout | null = null;

export async function registrar(app: FastifyInstance): Promise<void> {
  app.get("/api/oauth/redireccion", async () => ({ redirect_uri: redireccion() }));

  app.get<{ Params: { credencial: string }; Querystring: { json?: string } }>("/api/oauth/:credencial/iniciar", async (req, res) => {
    const r = urlDeConsentimiento(req.params.credencial);
    if ("error" in r) return res.code(r.codigo).send({ error: r.error });
    if (req.query.json === "1" || req.query.json === "si") return { url: r.url, redirect_uri: redireccion() };
    return res.redirect(r.url, 302);
  });

  app.get<{ Querystring: { code?: string; state?: string; error?: string; error_description?: string } }>("/api/oauth/callback", async (req, res) => {
    const q = req.query;
    let r: { ok: boolean; credencial?: string; cuenta?: string; alcances?: string[]; error?: string };
    if (q.error) {
      const e = q.state ? estados.get(q.state) : undefined;
      if (q.state) estados.delete(q.state);
      r = { ok: false, credencial: e?.credencial, error: `el proveedor no dio el permiso: ${String(q.error_description || q.error).slice(0, 300)}` };
    } else if (!q.code || !q.state) {
      r = { ok: false, error: "faltan code y state en el regreso" };
    } else {
      r = await completar(q.state, q.code);
    }
    if (r.ok) app.log.warn({ credencial: r.credencial }, "se conectó una cuenta por OAuth");
    return res.code(r.ok ? 200 : 400).header("Content-Type", "text/html; charset=utf-8").header("Cache-Control", "no-store").send(paginaDeRegreso(r));
  });

  app.post<{ Params: { credencial: string } }>("/api/oauth/:credencial/revocar", async (req, res) => {
    const r = await revocar(req.params.credencial);
    if (!r.ok) return res.code(r.codigo ?? 400).send({ error: r.error });
    app.log.warn({ credencial: req.params.credencial }, "se desconectó una cuenta OAuth");
    return r;
  });

  app.post<{ Params: { nombre: string } }>("/api/credenciales/:nombre/probar", async (req, res) => {
    if (!credenciales.existe(req.params.nombre)) return res.code(404).send({ ok: false, error: "esa conexión no existe" });
    return probar(req.params.nombre);
  });

  if (!reloj) {
    reloj = setInterval(() => void refrescarLasQueVencen().catch(() => undefined), 5 * 60_000);
    reloj.unref();
  }
}
