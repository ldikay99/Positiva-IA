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
 * El estado (última vez de cada flujo) queda en <estado>/programador.json.
 */

export interface Dependencias {
  flujos: () => Flujo[];
  /** Cuántas corridas de ese origen tiene el flujo en cola o en curso. */
  enCurso: (flujo: string, origen: string) => number;
  disparar: (flujo: string, origen: "programado" | "correo_nuevo", datos: { entrada?: unknown; caso?: string }) => void;
  /** La orden para `flujo_declarativo.py sondear` (definición y credenciales), o null. */
  ordenDeSondeo: (flujo: string) => Record<string, unknown> | null;
  aviso: (msg: string, datos?: Record<string, unknown>) => void;
}

const ruta = () => join(config.datos, "programador.json");
let ultimas: Record<string, number> = {};
const sondeando = new Set<string>();
let reloj: NodeJS.Timeout | null = null;

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
  let cambio = false;
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

export function detener(): void {
  if (reloj) clearInterval(reloj);
  reloj = null;
}

/** Cuándo corrió por última vez cada flujo programado (para el manifiesto y la web). */
export function estado(): Record<string, number> {
  return { ...ultimas };
}
