import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { config } from "./config.ts";
import { TIPOS_CREDENCIAL_IA } from "./ia_credenciales.ts";

/**
 * Las credenciales de los conectores (buzones, bases de datos, Drive, APIs).
 *
 * - Se guardan cifradas con AES-256-GCM en <estado>/credenciales.json. La llave
 *   sale de CLAVE_CREDENCIALES o de <estado>/.clave-credenciales (0600), que se
 *   genera la primera vez.
 * - Los valores son de solo escritura: ninguna API los devuelve; solo los nombres
 *   de los campos. El trabajador de Python los recibe por stdin, solo los de las
 *   credenciales que usa la corrida y solo las globales o las del propio flujo.
 * - `permitir_escritura` solo lo cambia una persona (la ruta exige el encabezado
 *   X-Confirmado-Por-Persona: si, que Vera nunca manda). Reemplazar los valores
 *   de una credencial lo vuelve a apagar.
 */

export interface CampoDeCredencial {
  nombre: string;
  titulo: string;
  tipo: "texto" | "entero" | "booleano" | "texto-largo" | "opcion";
  secreto?: boolean;
  obligatorio?: boolean;
  defecto?: string | number | boolean;
  opciones?: string[];
  ayuda?: string;
}

export interface TipoDeCredencial {
  tipo: string;
  nombre: string;
  descripcion: string;
  ayuda: string;
  campos: CampoDeCredencial[];
}

const host = (ayuda = "nombre del servidor"): CampoDeCredencial => ({ nombre: "host", titulo: "Servidor", tipo: "texto", obligatorio: true, ayuda });
const puerto = (defecto: number): CampoDeCredencial => ({ nombre: "puerto", titulo: "Puerto", tipo: "entero", defecto });
const usuario = (obligatorio = true): CampoDeCredencial => ({ nombre: "usuario", titulo: "Usuario", tipo: "texto", obligatorio });
const clave = (obligatorio = true): CampoDeCredencial => ({ nombre: "clave", titulo: "Clave", tipo: "texto", secreto: true, obligatorio });
const hosts: CampoDeCredencial = { nombre: "hosts", titulo: "Hosts permitidos", tipo: "texto", obligatorio: true, ayuda: "a qué hosts se puede mandar esta credencial, separados por coma (p. ej. api.ejemplo.com)" };

export const TIPOS_CREDENCIAL: TipoDeCredencial[] = [
  { tipo: "imap", nombre: "Buzón IMAP", descripcion: "Leer correo por IMAP (solo lectura).", ayuda: "Para Gmail usa imap.gmail.com:993 con una contraseña de aplicación. Nunca buzones de Positiva.",
    campos: [host("p. ej. imap.gmail.com"), puerto(993), usuario(), clave(), { nombre: "tls", titulo: "Cifrado", tipo: "opcion", opciones: ["ssl", "starttls", "ninguno"], defecto: "ssl" }] },
  { tipo: "smtp", nombre: "Envío SMTP", descripcion: "Enviar correo por SMTP (escritura: hay que habilitarla).", ayuda: "p. ej. smtp.gmail.com:587 con STARTTLS. Nunca a direcciones de Positiva.",
    campos: [host("p. ej. smtp.gmail.com"), puerto(587), usuario(false), clave(false), { nombre: "tls", titulo: "Cifrado", tipo: "opcion", opciones: ["starttls", "ssl", "ninguno"], defecto: "starttls" },
      { nombre: "remitente", titulo: "Remitente", tipo: "texto", ayuda: "la dirección From (por defecto el usuario)" }] },
  { tipo: "microsoft_graph", nombre: "Microsoft 365 (Graph)", descripcion: "Leer y enviar correo de un buzón de Microsoft 365 con una aplicación de Entra ID.",
    ayuda: "La aplicación necesita Mail.Read (y Mail.Send para enviar) de tipo aplicación. Los buzones de PQRD y tutelas están bloqueados siempre.",
    campos: [{ nombre: "tenant", titulo: "Tenant", tipo: "texto", obligatorio: true }, { nombre: "client_id", titulo: "Client ID", tipo: "texto", obligatorio: true },
      { nombre: "client_secret", titulo: "Client secret", tipo: "texto", secreto: true, obligatorio: true }, { nombre: "buzon", titulo: "Buzón", tipo: "texto", obligatorio: true, ayuda: "la dirección del buzón" }] },
  { tipo: "google_servicio", nombre: "Google (cuenta de servicio)", descripcion: "Drive y Hojas de cálculo con una cuenta de servicio.",
    ayuda: "Pega el JSON de la llave de la cuenta de servicio y comparte con su correo las carpetas que debe ver.",
    campos: [{ nombre: "json", titulo: "JSON de la cuenta de servicio", tipo: "texto-largo", secreto: true, obligatorio: true }] },
  { tipo: "postgres", nombre: "PostgreSQL", descripcion: "Consultar (y, si se habilita, escribir) una base PostgreSQL.", ayuda: "Crea un usuario de solo lectura para consultas. IP privadas bloqueadas.",
    campos: [host(), puerto(5432), { nombre: "base", titulo: "Base de datos", tipo: "texto", obligatorio: true }, usuario(), clave(), { nombre: "ssl", titulo: "SSL", tipo: "booleano", defecto: true }] },
  { tipo: "mysql", nombre: "MySQL / MariaDB", descripcion: "Consultar (y, si se habilita, escribir) una base MySQL o MariaDB.", ayuda: "Crea un usuario de solo lectura para consultas. IP privadas bloqueadas.",
    campos: [host(), puerto(3306), { nombre: "base", titulo: "Base de datos", tipo: "texto", obligatorio: true }, usuario(), clave(), { nombre: "ssl", titulo: "SSL", tipo: "booleano", defecto: true }] },
  { tipo: "sqlite", nombre: "SQLite", descripcion: "Una base SQLite del espacio de archivos (/datos/archivos).", ayuda: "La ruta es relativa a /datos/archivos.",
    campos: [{ nombre: "ruta", titulo: "Ruta", tipo: "texto", obligatorio: true, ayuda: "p. ej. bases/inventario.db" }] },
  { tipo: "http_bearer", nombre: "API con token", descripcion: "Un token Bearer para el nodo http.", ayuda: "Solo se manda a los hosts que digas aquí.",
    campos: [{ nombre: "token", titulo: "Token", tipo: "texto", secreto: true, obligatorio: true }, hosts] },
  { tipo: "http_basico", nombre: "API con usuario y clave", descripcion: "Autenticación básica para el nodo http.", ayuda: "Solo se manda a los hosts que digas aquí.",
    campos: [usuario(), clave(), hosts] },
  // Proveedores de IA (ia_<proveedor>): ver ia_credenciales.ts.
  ...TIPOS_CREDENCIAL_IA,
];

const NOMBRE = /^[a-z0-9][a-z0-9_.-]{0,62}$/;
const ID_FLUJO = /^[a-z][a-z0-9-]{1,40}$/;
/** Lo que nunca se acepta como destino de una credencial (el motor además lo bloquea al correr). */
const VETADO = (h: string) => /(^|\.)positiva\.gov\.co$/i.test(h) || /sgdea/i.test(h) || /^(localhost|metadata(\.google\.internal)?)$/i.test(h) || /\.internal$/i.test(h);
const CORREO_VETADO = (d: string) => /@([\w-]+\.)*positiva\.gov\.co$/i.test(d.trim());
/** Campos que se pueden mostrar en el manifiesto de un flujo (dónde se conecta), nunca secretos. */
const CAMPOS_DE_DESTINO = ["host", "puerto", "base", "buzon", "hosts", "ruta", "tenant", "remitente", "usuario", "base_url", "endpoint", "url"];

interface Cifrado { iv: string; tag: string; datos: string }
interface Guardada {
  tipo: string;
  descripcion: string;
  alcance: string;
  permitir_escritura: boolean;
  creada: number;
  actualizada: number;
  usada: number | null;
  campos: string[];
  valores: Cifrado;
}

export interface Resumen {
  nombre: string;
  tipo: string;
  campos: string[];
  permitir_escritura: boolean;
  alcance: string;
  descripcion: string;
  creada: number;
  actualizada: number;
  usada: number | null;
}

const ruta = () => join(config.datos, "credenciales.json");
let todas: Record<string, Guardada> = {};
let llave: Buffer | null = null;

/** La llave AES-256: de CLAVE_CREDENCIALES o del archivo .clave-credenciales (se crea con 0600). */
function laLlave(): Buffer {
  if (llave) return llave;
  const env = process.env.CLAVE_CREDENCIALES;
  if (env && env.trim()) {
    const b = Buffer.from(env.trim(), "base64");
    llave = b.length === 32 ? b : createHash("sha256").update(env.trim()).digest();
    return llave;
  }
  const archivo = join(config.datos, ".clave-credenciales");
  if (existsSync(archivo)) {
    llave = Buffer.from(readFileSync(archivo, "utf8").trim(), "base64");
  } else {
    mkdirSync(config.datos, { recursive: true });
    llave = randomBytes(32);
    writeFileSync(archivo, llave.toString("base64"), { encoding: "utf8", mode: 0o600 });
  }
  try {
    chmodSync(archivo, 0o600);
  } catch {
    /* Windows */
  }
  if (llave.length !== 32) throw new Error("la llave de credenciales no tiene 32 bytes");
  return llave;
}

export function cifrar(texto: string): Cifrado {
  const iv = randomBytes(12);
  const c = createCipheriv("aes-256-gcm", laLlave(), iv);
  const datos = Buffer.concat([c.update(texto, "utf8"), c.final()]);
  return { iv: iv.toString("base64"), tag: c.getAuthTag().toString("base64"), datos: datos.toString("base64") };
}

export function descifrar(x: Cifrado): string {
  const d = createDecipheriv("aes-256-gcm", laLlave(), Buffer.from(x.iv, "base64"));
  d.setAuthTag(Buffer.from(x.tag, "base64"));
  return Buffer.concat([d.update(Buffer.from(x.datos, "base64")), d.final()]).toString("utf8");
}

export async function cargar(): Promise<void> {
  laLlave();
  try {
    todas = JSON.parse(await readFile(ruta(), "utf8")) as Record<string, Guardada>;
  } catch {
    todas = {};
  }
}

let escribiendo: Promise<void> = Promise.resolve();

/** Escribe el archivo de a una vez (en fila) y de forma atómica. */
function persistir(): Promise<void> {
  const p = escribiendo.then(async () => {
    await mkdir(config.datos, { recursive: true });
    const tmp = `${ruta()}.${randomBytes(4).toString("hex")}.tmp`;
    await writeFile(tmp, JSON.stringify(todas, null, 2), { encoding: "utf8", mode: 0o600 });
    await rename(tmp, ruta());
  });
  escribiendo = p.catch(() => undefined);
  return p;
}

const resumen = (nombre: string, g: Guardada): Resumen => ({
  nombre, tipo: g.tipo, campos: g.campos, permitir_escritura: g.permitir_escritura, alcance: g.alcance, descripcion: g.descripcion,
  creada: g.creada, actualizada: g.actualizada, usada: g.usada,
});

/** Las credenciales (sin valores). Con flujo: las que ese flujo puede usar (globales y las suyas). */
export function listar(flujo?: string): Resumen[] {
  return Object.entries(todas)
    .filter(([, g]) => !flujo || g.alcance === "global" || g.alcance === flujo)
    .map(([n, g]) => resumen(n, g))
    .sort((a, b) => a.nombre.localeCompare(b.nombre));
}

export function tipos(): TipoDeCredencial[] {
  return TIPOS_CREDENCIAL;
}

export function existe(nombre: string): boolean {
  return nombre in todas;
}

export type Resultado = { ok: true; credencial: Resumen } | { ok: false; error: string; codigo?: number };

/** Crea o reemplaza una credencial. Un campo secreto vacío conserva el valor anterior (si el tipo no cambia). */
export async function guardar(nombre: string, cuerpo: unknown): Promise<Resultado> {
  if (!NOMBRE.test(nombre)) return { ok: false, error: "nombre inválido: minúsculas, números, -, _ y . (hasta 63)" };
  const b = (cuerpo ?? {}) as { tipo?: unknown; valores?: unknown; descripcion?: unknown; alcance?: unknown };
  const t = TIPOS_CREDENCIAL.find((x) => x.tipo === b.tipo);
  if (!t) return { ok: false, error: `tipo desconocido (válidos: ${TIPOS_CREDENCIAL.map((x) => x.tipo).join(", ")})` };
  if (!b.valores || typeof b.valores !== "object" || Array.isArray(b.valores)) return { ok: false, error: "valores tiene que ser un objeto {campo: valor}" };
  const alcance = b.alcance === undefined || b.alcance === null || b.alcance === "" ? (todas[nombre]?.alcance ?? "global") : String(b.alcance);
  if (alcance !== "global" && !ID_FLUJO.test(alcance)) return { ok: false, error: "alcance: «global» o el id de un flujo" };
  const entrada = b.valores as Record<string, unknown>;
  const ajenos = Object.keys(entrada).filter((k) => !t.campos.some((c) => c.nombre === k));
  if (ajenos.length) return { ok: false, error: `campos desconocidos para ${t.tipo}: ${ajenos.join(", ")}` };
  const previo = todas[nombre];
  const anteriores: Record<string, unknown> = previo && previo.tipo === t.tipo ? (JSON.parse(descifrar(previo.valores)) as Record<string, unknown>) : {};
  const valores: Record<string, unknown> = {};
  for (const c of t.campos) {
    let v = entrada[c.nombre];
    // Al editar, un campo que no viene conserva su valor (los secretos nunca se devuelven a la web).
    if ((v === undefined || (v === "" && c.secreto)) && anteriores[c.nombre] !== undefined) v = anteriores[c.nombre];
    if (v === undefined || v === "" || v === null) {
      if (c.obligatorio) return { ok: false, error: `falta ${c.nombre} (${c.titulo})` };
      if (c.defecto !== undefined) valores[c.nombre] = c.defecto;
      continue;
    }
    if (c.tipo === "entero") {
      const n = Number(v);
      if (!Number.isInteger(n) || n < 1 || n > 65535) return { ok: false, error: `${c.nombre} tiene que ser un entero entre 1 y 65535` };
      v = n;
    } else if (c.tipo === "booleano") {
      if (typeof v !== "boolean") v = ["true", "si", "sí", "1"].includes(String(v).trim().toLowerCase());
    } else {
      if (typeof v !== "string") v = typeof v === "object" ? JSON.stringify(v) : String(v);
      if ((v as string).length > 20000) return { ok: false, error: `${c.nombre} es demasiado largo` };
      if (c.opciones && !c.opciones.includes(v as string)) return { ok: false, error: `${c.nombre}: ${c.opciones.join(", ")}` };
    }
    valores[c.nombre] = v;
  }
  // Destinos vetados desde ya (el motor los vuelve a bloquear al correr).
  const destinos = [valores.host, ...String(valores.hosts ?? "").split(/[,\s]+/)].filter((x): x is string => typeof x === "string" && x.trim() !== "");
  const malo = destinos.find((h) => VETADO(h.trim().toLowerCase()));
  if (malo) return { ok: false, error: `bloqueado por seguridad: ${malo} es un destino que esta plataforma nunca toca (Positiva, SGDEA, metadatos o interno)` };
  for (const k of ["buzon", "usuario", "remitente"]) {
    if (typeof valores[k] === "string" && CORREO_VETADO(valores[k] as string)) return { ok: false, error: `bloqueado por seguridad: ${String(valores[k])} es un buzón de Positiva` };
  }
  if (t.tipo === "sqlite") {
    const r = String(valores.ruta).replace(/\\/g, "/");
    if (r.startsWith("/") || /^[A-Za-z]:/.test(r) || r.split("/").includes("..")) return { ok: false, error: "ruta: relativa a /datos/archivos, sin «..»" };
  }
  if (t.tipo === "google_servicio") {
    try {
      if ((JSON.parse(String(valores.json)) as { type?: string }).type !== "service_account") return { ok: false, error: "json: tiene que ser la llave de una cuenta de servicio" };
    } catch {
      return { ok: false, error: "json: no es un JSON válido" };
    }
  }
  const ahora = Date.now();
  todas[nombre] = {
    tipo: t.tipo, descripcion: typeof b.descripcion === "string" ? b.descripcion.slice(0, 300) : (previo?.descripcion ?? ""), alcance,
    // Si cambia cualquier valor, la escritura se apaga y una persona tiene que volver a habilitarla.
    permitir_escritura: Boolean(previo?.permitir_escritura) && previo?.tipo === t.tipo && JSON.stringify(anteriores) === JSON.stringify(valores), creada: previo?.creada ?? ahora, actualizada: ahora, usada: previo?.usada ?? null,
    campos: Object.keys(valores), valores: cifrar(JSON.stringify(valores)),
  };
  await persistir();
  return { ok: true, credencial: resumen(nombre, todas[nombre]!) };
}

export async function permitirEscritura(nombre: string, permitir: boolean): Promise<Resultado> {
  const g = todas[nombre];
  if (!g) return { ok: false, error: "esa credencial no existe", codigo: 404 };
  g.permitir_escritura = permitir;
  g.actualizada = Date.now();
  await persistir();
  return { ok: true, credencial: resumen(nombre, g) };
}

export async function borrar(nombre: string): Promise<boolean> {
  if (!todas[nombre]) return false;
  delete todas[nombre];
  await persistir();
  return true;
}

export interface ParaCorrida {
  tipo: string;
  valores: Record<string, unknown>;
  secretos: string[];
  permitir_escritura: boolean;
}

/** Lo que recibe el trabajador: solo las credenciales pedidas que el flujo puede usar (globales o suyas). Marca su uso. */
export function paraCorrida(flujo: string, nombres: Iterable<string>): Record<string, ParaCorrida> {
  const r: Record<string, ParaCorrida> = {};
  let cambio = false;
  for (const n of new Set(nombres)) {
    const g = todas[n];
    if (!g || (g.alcance !== "global" && g.alcance !== flujo)) continue;
    const t = TIPOS_CREDENCIAL.find((x) => x.tipo === g.tipo);
    r[n] = { tipo: g.tipo, valores: JSON.parse(descifrar(g.valores)) as Record<string, unknown>, secretos: (t?.campos ?? []).filter((c) => c.secreto).map((c) => c.nombre), permitir_escritura: g.permitir_escritura };
    g.usada = Date.now();
    cambio = true;
  }
  if (cambio) void persistir().catch(() => undefined);
  return r;
}

/** Para el manifiesto: tipo, alcance, si escribe y a dónde se conecta (sin ningún secreto). */
export function destino(nombre: string, flujo: string): { existe: boolean; usable: boolean; tipo?: string; alcance?: string; permitir_escritura?: boolean; destino?: Record<string, unknown> } {
  const g = todas[nombre];
  if (!g) return { existe: false, usable: false };
  const t = TIPOS_CREDENCIAL.find((x) => x.tipo === g.tipo);
  const valores = JSON.parse(descifrar(g.valores)) as Record<string, unknown>;
  const visibles = Object.fromEntries(Object.entries(valores).filter(([k]) => CAMPOS_DE_DESTINO.includes(k) && !t?.campos.find((c) => c.nombre === k)?.secreto));
  return { existe: true, usable: g.alcance === "global" || g.alcance === flujo, tipo: g.tipo, alcance: g.alcance, permitir_escritura: g.permitir_escritura, destino: visibles };
}
