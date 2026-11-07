import { readFileSync } from "node:fs";
import { cpus, totalmem } from "node:os";

/**
 * CPU y memoria de cada trabajador, leídas de /proc (Linux, el contenedor), sin
 * dependencias. En otro sistema (Windows, en desarrollo) devuelven null y la salud
 * dice que no hay medición.
 */

/** Ticks por segundo del reloj del kernel (USER_HZ): 100 en Linux. */
const TICKS_POR_S = 100;

export interface Lectura {
  /** utime + stime del proceso y de sus hijos terminados, en ticks. */
  ticks: number;
  /** Memoria residente (RSS) en kB. */
  rssKb: number;
  /** Cuándo se leyó (ms). */
  t: number;
}

export function leerProceso(pid: number | undefined): Lectura | null {
  if (!pid || process.platform !== "linux") return null;
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
    // el nombre va entre paréntesis y puede tener espacios: se cuenta desde el último «)»
    const campos = stat.slice(stat.lastIndexOf(")") + 2).split(" ");
    // utime + stime del proceso y cutime + cstime de sus hijos ya terminados (tesseract corre como hijo del trabajador)
    const ticks = Number(campos[11]) + Number(campos[12]) + Number(campos[13]) + Number(campos[14]);
    const status = readFileSync(`/proc/${pid}/status`, "utf8");
    const rss = /VmRSS:\s+(\d+)\s*kB/.exec(status);
    return { ticks, rssKb: rss ? Number(rss[1]) : 0, t: Date.now() };
  } catch {
    return null;
  }
}

/** % de CPU entre dos lecturas (100 % = un núcleo entero). */
export function cpuEntre(antes: Lectura | null | undefined, ahora: Lectura | null): number | null {
  if (!antes || !ahora || ahora.t <= antes.t) return null;
  const s = (ahora.t - antes.t) / 1000;
  return Math.max(0, Math.round((((ahora.ticks - antes.ticks) / TICKS_POR_S) / s) * 1000) / 10);
}

function numeroDe(ruta: string): number | null {
  try {
    const t = readFileSync(ruta, "utf8").trim();
    if (!t || t === "max") return null;
    const n = Number(t);
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

/** La memoria del contenedor: su límite (cgroup v2 o v1) o la de la máquina, y lo que usa. */
export function memoriaDelSistema(): { limite_mb: number; usada_mb: number | null; con_limite: boolean; cpus: number } {
  const limite = numeroDe("/sys/fs/cgroup/memory.max") ?? numeroDe("/sys/fs/cgroup/memory/memory.limit_in_bytes");
  const usada = numeroDe("/sys/fs/cgroup/memory.current") ?? numeroDe("/sys/fs/cgroup/memory/memory.usage_in_bytes");
  // un límite absurdo (cgroup v1 sin límite) vale como «sin límite»
  const conLimite = limite !== null && limite < totalmem();
  return { limite_mb: Math.round((conLimite ? limite! : totalmem()) / 1048576), usada_mb: usada === null ? null : Math.round(usada / 1048576), con_limite: conLimite, cpus: cpus().length };
}

/**
 * El límite de memoria de UN trabajador: MEMORIA_TRABAJADOR_MB, o lo que le toca del
 * contenedor repartido entre el tope de trabajadores (con un piso de 512 MB).
 */
export function limiteTrabajadorMb(tope: number): number {
  const fijo = Number(process.env.MEMORIA_TRABAJADOR_MB);
  if (Number.isFinite(fijo) && fijo > 0) return Math.round(fijo);
  return Math.max(512, Math.floor(memoriaDelSistema().limite_mb / Math.max(1, tope)));
}
