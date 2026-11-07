import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { config } from "./config.ts";
import type { Definicion, Flujo } from "./flujos.ts";

/**
 * Lo que corre solo:
 *
 * - `programado` (activo): por cron de 5 campos o cada N minutos, en su zona
 *   (America/Bogota por defecto). Nunca dos corridas programadas del mismo flujo a
 *   la vez (si la anterior sigue, esta se salta) y sin recuperar las perdidas: si la
 *   plataforma estuvo apagada, no se corren de golpe las que no se hicieron.
 * - `correo_nuevo` (activo): cada N minutos revisa el buzón (solo lectura) con el
 *   motor de Python; por cada correo nuevo arranca una corrida.
 *
 * - Flujos de código (pqrd, tutelas) con su programación activa: como el job. En
 *   cada toque del cron toman el siguiente lote del buzón real (solo lectura, por el
 *   mismo camino de /api/buzon/correr) y lo corren; en simulación mientras la
 *   compuerta de radicación esté apagada. Nunca dos corridas del mismo flujo a la
 *   vez y cada corrida tiene el tope del job (3600 s): lo que siga después se corta.
 *
 * El estado (última vez de cada flujo) queda en <estado>/programador.json y la
 * programación de los flujos de código en <estado>/programaciones.json.
 */

export interface Dependencias {
  flujos: () => Flujo[];
  /** Cuántas corridas de ese origen tiene el flujo en cola o en curso. */
  enCurso: (flujo: string, origen: string) => number;
  disparar: (flujo: string, origen: "programado" | "correo_nuevo", datos: { entrada?: unknown; caso?: string }) => void;
  /** La orden para `flujo_declarativo.py sondear` (definición y credenciales), o null. */
  ordenDeSondeo: (flujo: string) => Record<string, unknown> | null;
  aviso: (msg: string, datos?: Record<string, unknown>) => void;
  /** Flujo de código: toma el siguiente lote del buzón real y lo encola. Devuelve las corridas. */
  correrBuzon?: (flujo: string, n: number) => Promise<string[]>;
  /** Corta esas corridas (tope de tiempo del job). */
  cortar?: (flujo: string, ids: string[], motivo: string) => void;
  /** Cuántas de esas corridas siguen en cola o en curso. */
  vivas?: (flujo: string, ids: string[]) => number;
}

const ruta = () => join(config.datos, "programador.json");
let ultimas: Record<string, number> = {};
const sondeando = new Set<string>();
let reloj: NodeJS.Timeout | null = null;

// ------------------------------------------------ programación de los flujos de código

export interface ProgramacionDeCodigo {
  cron: string;
  zona: string;
  activo: boolean;
  /** Correos que toma cada corrida, como el job. */
  n: number;
  cambiado?: number;
}

/**
 * Lo mismo que Cloud Scheduler (verificado): pqrd cada 30 minutos; tutelas a los
 * minutos 5, 25 y 45 de 6 a 17 h; ambas en America/Bogota. El lote es el del job:
 * PQRD toma CORREOS_POR_CORRIDA (50) y tutelas los hilos de sus 50 correos más
 * recientes (`obtener_correos_por_conversacion`). Apagadas hasta que Jonathan las encienda.
 */
export const PROGRAMACION_DEL_JOB: Record<string, ProgramacionDeCodigo> = {
  pqrd: { cron: "*/30 * * * *", zona: "America/Bogota", activo: false, n: 50 },
  tutelas: { cron: "5,25,45 6-17 * * *", zona: "America/Bogota", activo: false, n: 50 },
};
/** El tope de una corrida del job (timeout de la tarea de Cloud Run). */
export const TOPE_DE_CORRIDA_S = 3600;

const rutaProgramaciones = () => join(config.datos, "programaciones.json");
let programaciones: Record<string, ProgramacionDeCodigo> = {};
/** Corridas programadas en curso de cada flujo de código (desde que se pide el lote hasta que terminan). */
const enCursoDeCodigo = new Map<string, { desde: number; ids: string[]; reloj?: NodeJS.Timeout }>();

export function programacionDe(flujo: string): ProgramacionDeCodigo | null {
  const base = PROGRAMACION_DEL_JOB[flujo];
  if (!base) return null;
  return { ...base, ...(programaciones[flujo] ?? {}) };
}

export type ResultadoProgramacion = { ok: true; programacion: ProgramacionDeCodigo } | { ok: false; codigo: number; error: string };

/** Cambia la programación de un flujo de código; lo que no se manda queda como estaba. */
export async function ponerProgramacion(flujo: string, cuerpo: unknown, problemaCron: (c: string) => string | null): Promise<ResultadoProgramacion> {
  const actual = programacionDe(flujo);
  if (!actual) return { ok: false, codigo: 404, error: "solo los flujos de código (pqrd, tutelas) tienen programación como el job" };
  const b = (cuerpo ?? {}) as Partial<Record<keyof ProgramacionDeCodigo, unknown>>;
  const sig = { ...actual };
  if (b.cron !== undefined) {
    if (typeof b.cron !== "string") return { ok: false, codigo: 400, error: "cron tiene que ser texto" };
    const p = problemaCron(b.cron);
    if (p) return { ok: false, codigo: 400, error: p };
    sig.cron = b.cron.trim();
  }
  if (b.zona !== undefined) {
    try {
      if (typeof b.zona !== "string" || !b.zona) throw new Error();
      new Intl.DateTimeFormat("es-CO", { timeZone: b.zona });
    } catch {
      return { ok: false, codigo: 400, error: "zona no es una zona horaria (p. ej. America/Bogota)" };
    }
    sig.zona = b.zona;
  }
  if (b.activo !== undefined) {
    if (typeof b.activo !== "boolean") return { ok: false, codigo: 400, error: "activo tiene que ser true o false" };
    sig.activo = b.activo;
  }
  if (b.n !== undefined) {
    if (typeof b.n !== "number" || !Number.isInteger(b.n) || b.n < 1 || b.n > 150) return { ok: false, codigo: 400, error: "n: entero entre 1 y 150" };
    sig.n = b.n;
  }
  sig.cambiado = Date.now();
  programaciones[flujo] = sig;
  await mkdir(config.datos, { recursive: true });
  await writeFile(rutaProgramaciones(), JSON.stringify(programaciones, null, 2), "utf8");
  return { ok: true, programacion: sig };
}

/** Si ese flujo de código tiene una corrida programada en curso (y desde cuándo). */
export function corridaProgramada(flujo: string): { desde: number; corridas: number } | null {
  const x = enCursoDeCodigo.get(flujo);
  return x ? { desde: x.desde, corridas: x.ids.length } : null;
}

/** Un toque de la programación de un flujo de código: el siguiente lote del buzón, sin solaparse. */
async function correrComoElJob(deps: Dependencias, flujo: string, p: ProgramacionDeCodigo, ahora: Date): Promise<void> {
  if (!deps.correrBuzon) return;
  const previa = enCursoDeCodigo.get(flujo);
  if (previa) {
    // La anterior sigue: ¿terminó ya todo lo que encoló?
    const vivas = previa.ids.length && deps.vivas ? deps.vivas(flujo, previa.ids) : previa.ids.length ? 1 : 0;
    if (vivas > 0 || !previa.ids.length) {
      deps.aviso("programado: la corrida anterior de este flujo sigue, esta se salta (como el job)", { flujo });
      return;
    }
    if (previa.reloj) clearTimeout(previa.reloj);
    enCursoDeCodigo.delete(flujo);
  }
  if (deps.enCurso(flujo, "programado") > 0) {
    deps.aviso("programado: hay corridas programadas de este flujo en curso, esta se salta", { flujo });
    return;
  }
  const marca = { desde: ahora.getTime(), ids: [] as string[], reloj: undefined as NodeJS.Timeout | undefined };
  enCursoDeCodigo.set(flujo, marca);
  try {
    marca.ids = await deps.correrBuzon(flujo, p.n);
  } catch (e) {
    enCursoDeCodigo.delete(flujo);
    deps.aviso("programado: no se pudo tomar el lote del buzón", { flujo, error: e instanceof Error ? e.message : String(e) });
    return;
  }
  if (!marca.ids.length) {
    enCursoDeCodigo.delete(flujo);
    return;
  }
  // El tope del job: lo que siga después de 3600 s desde el toque se corta.
  const resto = Math.max(1_000, TOPE_DE_CORRIDA_S * 1000 - (Date.now() - marca.desde));
  marca.reloj = setTimeout(() => {
    const vivas = deps.vivas ? deps.vivas(flujo, marca.ids) : 0;
    if (vivas > 0) deps.cortar?.(flujo, marca.ids, `tope de ${TOPE_DE_CORRIDA_S} s de la corrida programada, como el job`);
    if (enCursoDeCodigo.get(flujo) === marca) enCursoDeCodigo.delete(flujo);
  }, resto);
  marca.reloj.unref?.();
}

function ticDeCodigo(deps: Dependencias, ahora: Date): boolean {
  let cambio = false;
  for (const flujo of Object.keys(PROGRAMACION_DEL_JOB)) {
    const p = programacionDe(flujo)!;
    if (!p.activo || !deps.flujos().some((f) => f.id === flujo && f.tipo === "codigo")) continue;
    const clave = `j:${flujo}:minuto`;
    const minuto = Date.parse(`${partes(ahora, p.zona).clave}:00Z`);
    if (!coincideCron(p.cron, ahora, p.zona) || ultimas[clave] === minuto) continue;
    ultimas[clave] = minuto;
    ultimas[`j:${flujo}`] = ahora.getTime();
    cambio = true;
    void correrComoElJob(deps, flujo, p, ahora);
  }
  return cambio;
}

/** Para las pruebas: olvida las corridas programadas en curso. */
export function reiniciarCodigo(): void {
  for (const x of enCursoDeCodigo.values()) if (x.reloj) clearTimeout(x.reloj);
  enCursoDeCodigo.clear();
}

/** Las partes de una fecha en una zona horaria. */
export function partes(fecha: Date, zona: string): { minuto: number; hora: number; dia: number; mes: number; semana: number; clave: string } {
  const f = new Intl.DateTimeFormat("en-US", { timeZone: zona, hour12: false, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", weekday: "short" });
  const p = Object.fromEntries(f.formatToParts(fecha).map((x) => [x.type, x.value])) as Record<string, string>;
  const hora = Number(p.hour) % 24;
  const semana = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(p.weekday ?? "");
  return { minuto: Number(p.minute), hora, dia: Number(p.day), mes: Number(p.month), semana, clave: `${p.year}-${p.month}-${p.day}T${String(hora).padStart(2, "0")}:${p.minute}` };
}

function coincideCampo(campo: string, valor: number, lo: number, hi: number, esSemana = false): boolean {
  return campo.split(",").some((parte) => {
    const m = /^(\*|(\d+)(?:-(\d+))?)(?:\/(\d+))?$/.exec(parte);
    if (!m) return false;
    const paso = m[4] ? Number(m[4]) : 1;
    const desde = m[1] === "*" ? lo : Number(m[2]);
    const hasta = m[1] === "*" ? hi : m[3] !== undefined ? Number(m[3]) : m[4] ? hi : desde;
    const prueba = (v: number) => v >= desde && v <= hasta && (v - desde) % paso === 0;
    return prueba(valor) || (esSemana && valor === 0 && prueba(7));
  });
}

/** Si un cron de 5 campos toca en ese minuto (día del mes y de la semana: basta uno si los dos están restringidos). */
export function coincideCron(expr: string, fecha: Date, zona: string): boolean {
  const [mi, ho, di, me, se] = expr.trim().split(/\s+/);
  if (!mi || !ho || !di || !me || !se) return false;
  const p = partes(fecha, zona);
  if (!coincideCampo(mi, p.minuto, 0, 59) || !coincideCampo(ho, p.hora, 0, 23) || !coincideCampo(me, p.mes, 1, 12)) return false;
  const d = coincideCampo(di, p.dia, 1, 31);
  const s = coincideCampo(se, p.semana, 0, 7, true);
  return di !== "*" && se !== "*" ? d || s : d && s;
}

async function guardarEstado(): Promise<void> {
  await mkdir(config.datos, { recursive: true });
  await writeFile(ruta(), JSON.stringify(ultimas, null, 2), "utf8");
}

const disparadorDe = (d: Definicion) => d.nodos.find((n) => ["programado", "correo_nuevo"].includes(n.tipo));

/** Un tic del reloj: lo que toca ahora. Se puede llamar a mano (pruebas). */
export function tic(deps: Dependencias, ahora = new Date()): void {
  let cambio = ticDeCodigo(deps, ahora);
  for (const f of deps.flujos()) {
    if (f.tipo !== "declarativo" || !f.definicion) continue;
    const n = disparadorDe(f.definicion);
    if (!n) continue;
    const c = n.config as { activo?: boolean; cron?: string; cada_minutos?: number; zona?: string };
    if (c.activo !== true) continue;
    if (n.tipo === "programado") {
      const zona = c.zona || "America/Bogota";
      const clave = `p:${f.id}`;
      let toca = false;
      if (c.cron) {
        const minuto = partes(ahora, zona).clave;
        // una vez por minuto que coincide; no se recupera lo perdido
        if (coincideCron(c.cron, ahora, zona) && ultimas[`${clave}:minuto`] !== Date.parse(`${minuto}:00Z`)) {
          ultimas[`${clave}:minuto`] = Date.parse(`${minuto}:00Z`);
          toca = true;
        }
      } else if (c.cada_minutos) {
        const previa = ultimas[clave] ?? 0;
        toca = ahora.getTime() - previa >= c.cada_minutos * 60_000 - 2_000;
      }
      if (!toca) continue;
      ultimas[clave] = ahora.getTime();
      cambio = true;
      if (deps.enCurso(f.id, "programado") > 0) {
        deps.aviso("programado: la corrida anterior sigue, esta se salta", { flujo: f.id });
        continue;
      }
      deps.disparar(f.id, "programado", { entrada: { programado: true, hora: ahora.toISOString(), zona, ...(c.cron ? { cron: c.cron } : { cada_minutos: c.cada_minutos }) } });
    } else {
      const clave = `c:${f.id}`;
      const cada = Math.max(1, c.cada_minutos ?? 5);
      if (ahora.getTime() - (ultimas[clave] ?? 0) < cada * 60_000 - 2_000 || sondeando.has(f.id)) continue;
      ultimas[clave] = ahora.getTime();
      cambio = true;
      void sondear(deps, f.id);
    }
  }
  if (cambio) void guardarEstado().catch(() => undefined);
}

/** Revisa el buzón de un correo_nuevo con el motor (solo lectura) y arranca una corrida por cada correo nuevo. */
export async function sondear(deps: Dependencias, flujo: string): Promise<{ casos: { caso: string; id: string; asunto: string }[]; error?: string }> {
  const orden = deps.ordenDeSondeo(flujo);
  if (!orden) return { casos: [], error: "ese flujo no tiene correo_nuevo" };
  sondeando.add(flujo);
  try {
    const r = await new Promise<{ ok?: boolean; casos?: { caso: string; id: string; asunto: string }[]; error?: string }>((resolver) => {
      const p = spawn(config.python, [resolve(config.raiz, "puente", "flujo_declarativo.py"), "sondear"], { env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
      let salida = "";
      p.stdout.setEncoding("utf8").on("data", (t: string) => (salida += t));
      const limite = setTimeout(() => p.kill(), 180_000);
      p.on("exit", () => {
        clearTimeout(limite);
        try {
          resolver(JSON.parse(salida.trim().split("\n").pop() ?? "{}"));
        } catch {
          resolver({ ok: false, error: "el sondeo no terminó" });
        }
      });
      p.stdin.end(JSON.stringify(orden));
    });
    if (!r.ok) {
      deps.aviso("correo_nuevo: no se pudo revisar el buzón", { flujo, error: r.error });
      return { casos: [], error: r.error };
    }
    for (const x of r.casos ?? []) deps.disparar(flujo, "correo_nuevo", { caso: x.caso, entrada: { id: x.id, asunto: x.asunto } });
    return { casos: r.casos ?? [] };
  } finally {
    sondeando.delete(flujo);
  }
}

export async function iniciar(deps: Dependencias, cadaMs = 15_000): Promise<void> {
  try {
    ultimas = JSON.parse(await readFile(ruta(), "utf8")) as Record<string, number>;
  } catch {
    ultimas = {};
  }
  await cargarProgramaciones();
  if (reloj) clearInterval(reloj);
  reloj = setInterval(() => {
    try {
      tic(deps);
    } catch (e) {
      deps.aviso("programador: falló un tic", { error: e instanceof Error ? e.message : String(e) });
    }
  }, cadaMs);
  reloj.unref();
  setTimeout(() => tic(deps), 2_000).unref();
}

export async function cargarProgramaciones(): Promise<void> {
  try {
    programaciones = JSON.parse(await readFile(rutaProgramaciones(), "utf8")) as Record<string, ProgramacionDeCodigo>;
  } catch {
    programaciones = {};
  }
}

export function detener(): void {
  if (reloj) clearInterval(reloj);
  reloj = null;
}

/** Cuándo corrió por última vez cada flujo programado (para el manifiesto y la web). */
export function estado(): Record<string, number> {
  return { ...ultimas };
}
