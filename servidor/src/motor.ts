import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { appendFile, mkdir, readdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { config } from "./config.ts";
import type { Configuracion } from "./configuracion.ts";
import { enmascarar, enmascararTexto, variantes } from "./enmascarar.ts";

const MARCA = "\x1eEVT ";

export interface Evento {
  tipo: string;
  corrida: string | null;
  t: number;
  [campo: string]: unknown;
}

export type EstadoCorrida = "en_cola" | "procesando" | "terminada" | "fallida";

export interface Corrida {
  id: string;
  /** El flujo al que pertenece: las corridas de un flujo nunca se mezclan con las de otro. */
  flujo: string;
  caso: string;
  /** Si corrió con otros como una corrida del job: un solo orquestador para todos. */
  lote?: string;
  estado: EstadoCorrida;
  creada: number;
  inicio?: number;
  fin?: number;
  configuracion: Configuracion;
  /** Lo que disparó la corrida: bandeja, manual, programado, webhook, correo_nuevo. */
  origen?: string;
  /** La entrada de una corrida sin correo (entrada_manual, programado, webhook). */
  entrada?: unknown;
  eventos: Evento[];
  resultado?: Evento;
}

interface Trabajador {
  id: number;
  proceso: ChildProcessWithoutNullStreams;
  listo: boolean;
  /** Lo que está atendiendo: un correo, o todos los de un lote a la vez. */
  activas: Map<string, Corrida>;
  /** Se retira apenas quede libre (cambió el entorno del flujo): no recibe más trabajo. */
  retirar?: boolean;
  /** Si arrancó con la radicación real (compuerta encendida y segunda llave). */
  real: boolean;
  /** Por qué se cortó (tope de tiempo), para el resultado de sus corridas. */
  cortado?: string;
}

/** Lo que espera trabajador: un correo suelto o un lote entero, que va a un solo trabajador. */
type Pedido = { tipo: "uno"; corrida: Corrida } | { tipo: "lote"; id: string; corridas: Corrida[]; concurrencia: number };

/** Pool de trabajadores de Python de UN flujo: cada uno atiende un correo (o un lote) a la vez. */
export class Motor extends EventEmitter {
  private trabajadores: Trabajador[] = [];
  private cola: Pedido[] = [];
  readonly corridas = new Map<string, Corrida>();
  private siguienteId = 1;
  private deseados = 0;
  errorInicio = "";
  /** Los últimos eventos de todas las corridas, para la vista de logs. */
  readonly recientes: Evento[] = [];

  readonly flujo: string;
  private readonly trabajador: string;
  /** Lo que el flujo agrega a cada orden (la definición, la entrada, las credenciales y el entorno de la corrida). */
  private readonly ordenExtra: (corrida: Corrida) => Record<string, unknown>;
  /** El entorno propio del flujo con que arrancan sus trabajadores (encima del de la plataforma). */
  entornoDeProceso: () => Record<string, string> = () => ({});
  /** Los valores secretos del entorno del flujo: se enmascaran en todo lo que sale de sus trabajadores. */
  secretosDeProceso: () => string[] = () => [];
  /** Si la compuerta de radicación de este flujo está encendida (con la segunda llave). */
  radicacionReal: () => boolean = () => false;
  /** Todas las formas de los secretos que conoció algún trabajador de este flujo. */
  private mascaras: string[] = [];

  constructor(flujo = "pqrd", trabajador: string = config.trabajador, ordenExtra: (corrida: Corrida) => Record<string, unknown> = () => ({})) {
    super();
    this.flujo = flujo;
    this.trabajador = trabajador;
    this.ordenExtra = ordenExtra;
    this.setMaxListeners(0);
  }

  ajustar(cantidad: number): void {
    this.deseados = Math.max(1, Math.min(config.trabajadoresMax, cantidad));
    while (this.trabajadores.length < this.deseados) this.lanzar();
    // Los que sobran se retiran cuando quedan libres.
    this.repartir();
  }

  estado() {
    return {
      trabajadores: this.trabajadores.map((t) => {
        const activas = [...t.activas.values()];
        return { id: t.id, listo: t.listo, corrida: activas[0]?.id ?? null, caso: activas[0]?.caso ?? null, corridas: activas.map((c) => c.id), lote: activas[0]?.lote ?? null };
      }),
      enCola: this.cola.flatMap((p) => (p.tipo === "uno" ? [p.corrida.id] : p.corridas.map((c) => c.id))),
      errorInicio: this.errorInicio,
    };
  }

  private nueva(caso: string, configuracion: Configuracion, lote?: string, extra: { entrada?: unknown; origen?: string } = {}): Corrida {
    const corrida: Corrida = {
      id: `${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "")}-${randomUUID().slice(0, 6)}`,
      flujo: this.flujo,
      caso,
      ...(lote ? { lote } : {}),
      ...(extra.origen ? { origen: extra.origen } : {}),
      ...(extra.entrada !== undefined ? { entrada: extra.entrada } : {}),
      estado: "en_cola",
      creada: Date.now(),
      configuracion,
      eventos: [],
    };
    this.corridas.set(corrida.id, corrida);
    this.publicar({ tipo: "en_cola", corrida: corrida.id, t: Date.now() / 1000, caso, ...(lote ? { lote } : {}), ...(extra.origen ? { origen: extra.origen } : {}) });
    return corrida;
  }

  encolar(caso: string, configuracion: Configuracion, extra: { entrada?: unknown; origen?: string } = {}): Corrida {
    const corrida = this.nueva(caso, configuracion, undefined, extra);
    this.cola.push({ tipo: "uno", corrida });
    this.repartir();
    return corrida;
  }

  /**
   * Varios correos como una corrida del job: van juntos a un mismo trabajador,
   * que los atiende con un solo orquestador y de a `concurrencia` a la vez.
   */
  encolarLote(casos: string[], configuracion: Configuracion, concurrencia = 0, origen?: string): { lote: string; corridas: Corrida[] } {
    const lote = `lote-${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "")}-${randomUUID().slice(0, 4)}`;
    const corridas = casos.map((caso) => this.nueva(caso, configuracion, lote, origen ? { origen } : {}));
    this.cola.push({ tipo: "lote", id: lote, corridas, concurrencia });
    this.repartir();
    return { lote, corridas };
  }

  /** Cuántas corridas de este flujo esperan o están en curso (de un origen, si se da). */
  enCurso(origen?: string): number {
    let n = 0;
    for (const c of this.corridas.values()) if ((c.estado === "en_cola" || c.estado === "procesando") && (!origen || c.origen === origen)) n++;
    return n;
  }

  /**
   * El entorno del flujo cambió: cada trabajador se retira apenas quede libre (no se
   * corta ninguna corrida) y en su lugar arranca uno nuevo con el entorno nuevo.
   */
  reiniciarTrabajadores(): void {
    for (const t of this.trabajadores) t.retirar = true;
    this.repartir();
  }

  /**
   * Corta esas corridas (tope de tiempo, como el job): las que esperan salen de la
   * cola y los trabajadores que las atienden se detienen; al salir, cada corrida
   * cortada queda con el motivo.
   */
  cortar(ids: string[], motivo: string): number {
    const buscadas = new Set(ids);
    let n = 0;
    for (const p of [...this.cola]) {
      const cs = p.tipo === "uno" ? [p.corrida] : p.corridas;
      if (!cs.some((c) => buscadas.has(c.id))) continue;
      this.cola.splice(this.cola.indexOf(p), 1);
      for (const c of cs) {
        c.estado = "fallida";
        this.publicar({ tipo: "resultado", corrida: c.id, t: Date.now() / 1000, caso: c.caso, accion: "error", motivo });
        n++;
      }
    }
    for (const t of this.trabajadores) {
      if (![...t.activas.keys()].some((id) => buscadas.has(id))) continue;
      t.cortado = motivo;
      n += t.activas.size;
      t.proceso.kill();
    }
    return n;
  }

  cancelarCola(): number {
    const pendientes = this.cola.splice(0).flatMap((p) => (p.tipo === "uno" ? [p.corrida] : p.corridas));
    for (const c of pendientes) {
      c.estado = "fallida";
      this.publicar({ tipo: "resultado", corrida: c.id, t: Date.now() / 1000, caso: c.caso, accion: "cancelada", motivo: "cancelada antes de empezar" });
    }
    return pendientes.length;
  }

  /** Suma las formas de los secretos actuales a las que se enmascaran (nunca se quitan: un trabajador viejo pudo conocerlas). */
  private recordarSecretos(): void {
    const todas = new Set([...this.mascaras, ...variantes(this.secretosDeProceso())]);
    this.mascaras = [...todas].sort((a, b) => b.length - a.length);
  }

  /** Lo que sale de los trabajadores, sin los valores secretos del flujo. */
  enmascarar<T>(x: T): T {
    return enmascarar(x, this.mascaras);
  }

  private lanzar(): void {
    this.recordarSecretos();
    const real = this.radicacionReal();
    // La compuerta va DESPUÉS del entorno del flujo: el flujo no la puede poner (y entornos.ts tampoco lo deja).
    const proceso = spawn(config.python, [this.trabajador], {
      env: { ...process.env, ...this.entornoDeProceso(), RADICACION_REAL: real ? "si" : "no", PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8", RUTA_BANDEJA: config.bandeja, RUTA_VIVO: config.vivo, RUTA_TRABAJO: join(config.datos, "trabajo") },
      stdio: ["pipe", "pipe", "pipe"],
    });
    const t: Trabajador = { id: this.siguienteId++, proceso, listo: false, activas: new Map(), real };
    this.trabajadores.push(t);
    let pendiente = "";
    proceso.stdout.setEncoding("utf8");
    proceso.stdout.on("data", (trozo: string) => {
      pendiente += trozo;
      let i: number;
      while ((i = pendiente.indexOf("\n")) >= 0) {
        const linea = pendiente.slice(0, i);
        pendiente = pendiente.slice(i + 1);
        this.linea(t, linea);
      }
    });
    proceso.stderr.setEncoding("utf8");
    // Por líneas completas, como stdout: un secreto partido entre dos trozos no escaparía a la máscara.
    let pendienteError = "";
    const lineaDeError = (linea: string) => {
      if (linea.trim()) this.recibir(t, { tipo: "consola", corrida: this.unica(t)?.id ?? null, t: Date.now() / 1000, linea, error: true });
    };
    proceso.stderr.on("data", (trozo: string) => {
      pendienteError += trozo;
      let i: number;
      while ((i = pendienteError.indexOf("\n")) >= 0) {
        lineaDeError(pendienteError.slice(0, i));
        pendienteError = pendienteError.slice(i + 1);
      }
      if (pendienteError.length > 64_000) {
        lineaDeError(pendienteError);
        pendienteError = "";
      }
    });
    proceso.stderr.on("end", () => {
      lineaDeError(pendienteError);
      pendienteError = "";
    });
    proceso.on("exit", (codigo) => {
      this.trabajadores = this.trabajadores.filter((x) => x !== t);
      for (const c of t.activas.values()) {
        if (c.estado === "procesando") {
          this.recibir(t, { tipo: "resultado", corrida: c.id, t: Date.now() / 1000, caso: c.caso, accion: "error", motivo: t.cortado ?? `el trabajador terminó (código ${codigo})` });
        }
      }
      if (this.trabajadores.length < this.deseados && !this.errorInicio) setTimeout(() => this.ajustar(this.deseados), 1000);
      this.publicarEstado();
    });
  }

  private linea(t: Trabajador, linea: string): void {
    if (!linea.startsWith(MARCA)) {
      if (linea.trim()) this.recibir(t, { tipo: "consola", corrida: this.unica(t)?.id ?? null, t: Date.now() / 1000, linea });
      return;
    }
    try {
      this.recibir(t, JSON.parse(linea.slice(MARCA.length)) as Evento);
    } catch {
      /* línea cortada: se ignora */
    }
  }

  private recibir(t: Trabajador, crudo: Evento): void {
    // Antes de guardarlo o reenviarlo: ningún valor secreto del flujo sale de aquí.
    const e = this.enmascarar(crudo);
    if (e.tipo === "listo") {
      t.listo = true;
      this.errorInicio = "";
      this.publicarEstado();
      this.repartir();
      return;
    }
    if (e.tipo === "fallo_inicio") {
      this.errorInicio = enmascararTexto(String(e.error ?? "fallo al iniciar el motor"), this.mascaras);
      this.publicarEstado();
      return;
    }
    // El trabajador dice de qué corrida es cada evento; en un correo suelto, si
    // no lo dice (una línea de consola), es de la única que tiene.
    const corrida = (typeof e.corrida === "string" ? t.activas.get(e.corrida) : undefined) ?? this.unica(t);
    if (corrida) {
      e.corrida = corrida.id;
      corrida.eventos.push(e);
      if (e.tipo === "resultado") {
        corrida.resultado = e;
        corrida.estado = e.accion === "error" ? "fallida" : "terminada";
        corrida.fin = Date.now();
        void this.guardar(corrida);
      }
    }
    this.publicar(e);
    if (e.tipo === "libre") {
      t.activas.clear();
      this.publicarEstado();
      this.repartir();
    }
  }

  private repartir(): void {
    const real = this.radicacionReal();
    for (const t of [...this.trabajadores]) {
      if (!t.listo || t.activas.size) continue;
      // Un trabajador que arrancó con otra compuerta no recibe nada más: se retira y arranca uno nuevo.
      if (t.retirar || t.real !== real || this.trabajadores.length > this.deseados) {
        t.listo = false;
        t.proceso.stdin.end();
        continue;
      }
      const pedido = this.cola.shift();
      if (!pedido) return;
      const corridas = pedido.tipo === "uno" ? [pedido.corrida] : pedido.corridas;
      for (const corrida of corridas) {
        t.activas.set(corrida.id, corrida);
        corrida.estado = "procesando";
        corrida.inicio = Date.now();
      }
      const c = corridas[0]!.configuracion;
      const comun = { parametros: c.parametros, entorno: c.entorno, ia: c.ia, tope_s: c.topeSegundos, reglas: c.reglas.filter((r) => r.activa), ...this.ordenExtra(corridas[0]!), radicacion_real: t.real };
      const orden =
        pedido.tipo === "uno"
          ? { cmd: "procesar", corrida: pedido.corrida.id, caso: pedido.corrida.caso, ...comun }
          : { cmd: "procesar_lote", lote: pedido.id, concurrencia: pedido.concurrencia, corridas: corridas.map((x) => ({ corrida: x.id, caso: x.caso })), ...comun };
      t.proceso.stdin.write(JSON.stringify(orden) + "\n");
      for (const corrida of corridas) {
        this.publicar({ tipo: "asignada", corrida: corrida.id, t: Date.now() / 1000, caso: corrida.caso, trabajador: t.id, ...(corrida.lote ? { lote: corrida.lote } : {}) });
      }
      this.publicarEstado();
    }
  }

  /** La corrida de un trabajador que atiende un solo correo. */
  private unica(t: Trabajador): Corrida | undefined {
    return t.activas.size === 1 ? t.activas.values().next().value : undefined;
  }

  private publicar(e: Evento): void {
    e.flujo = this.flujo;
    if (e.tipo !== "motor") {
      this.recientes.push(e);
      if (this.recientes.length > 4000) this.recientes.splice(0, this.recientes.length - 4000);
    }
    this.emit("evento", e);
  }

  private publicarEstado(): void {
    this.emit("evento", { tipo: "motor", corrida: null, t: Date.now() / 1000, flujo: this.flujo, ...this.estado() });
  }

  private async guardar(c: Corrida): Promise<void> {
    const carpeta = join(config.datos, "corridas");
    await mkdir(carpeta, { recursive: true });
    const { eventos, ...resto } = c;
    await appendFile(join(carpeta, `${new Date().toISOString().slice(0, 10)}.jsonl`), JSON.stringify({ ...resto, eventos }) + "\n", "utf8");
  }

  /** Las corridas de días anteriores, para que el historial sobreviva a un reinicio. */
  async cargarHistorial(maximo = 500): Promise<void> {
    const carpeta = join(config.datos, "corridas");
    const archivos = (await readdir(carpeta).catch(() => [])).filter((a) => a.endsWith(".jsonl")).sort().reverse();
    const leidas: Corrida[] = [];
    for (const a of archivos) {
      const lineas = (await readFile(join(carpeta, a), "utf8")).trim().split("\n").filter(Boolean).reverse();
      for (const l of lineas) {
        try {
          leidas.push(JSON.parse(l) as Corrida);
        } catch {
          /* línea cortada */
        }
        if (leidas.length >= maximo) break;
      }
      if (leidas.length >= maximo) break;
    }
    // Las corridas de antes de los flujos eran todas de PQRD.
    for (const c of leidas.reverse()) {
      c.flujo ??= "pqrd";
      if (c.flujo === this.flujo && !this.corridas.has(c.id)) this.corridas.set(c.id, c);
    }
  }

  detener(): void {
    // Sin trabajadores deseados: al salir, ninguno se vuelve a lanzar.
    this.deseados = 0;
    for (const t of this.trabajadores) t.proceso.kill();
  }
}

/**
 * Borra del historial guardado TODAS las corridas de un flujo (al borrar el flujo).
 * Reescribe cada archivo del día sin sus líneas, de forma atómica. Devuelve cuántas quitó.
 */
export async function borrarCorridasDe(flujo: string): Promise<number> {
  const carpeta = join(config.datos, "corridas");
  let n = 0;
  for (const a of (await readdir(carpeta).catch(() => [] as string[])).filter((x) => x.endsWith(".jsonl"))) {
    const ruta = join(carpeta, a);
    const lineas = (await readFile(ruta, "utf8")).split("\n").filter(Boolean);
    const quedan = lineas.filter((l) => {
      try {
        return ((JSON.parse(l) as { flujo?: string }).flujo ?? "pqrd") !== flujo;
      } catch {
        return true;
      }
    });
    if (quedan.length === lineas.length) continue;
    n += lineas.length - quedan.length;
    const tmp = `${ruta}.${Date.now()}.tmp`;
    await writeFile(tmp, quedan.length ? quedan.join("\n") + "\n" : "", "utf8");
    await rename(tmp, ruta);
  }
  return n;
}

/** Cuántas corridas guardadas tiene un flujo (para decir qué se borra). */
export async function contarCorridasDe(flujo: string): Promise<number> {
  const carpeta = join(config.datos, "corridas");
  let n = 0;
  const marca = `"flujo":${JSON.stringify(flujo)}`;
  for (const a of (await readdir(carpeta).catch(() => [] as string[])).filter((x) => x.endsWith(".jsonl")))
    for (const l of (await readFile(join(carpeta, a), "utf8")).split("\n")) if (l.includes(marca)) n++;
  return n;
}
