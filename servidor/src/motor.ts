import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { appendFile, mkdir, readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { config } from "./config.ts";
import type { Configuracion } from "./configuracion.ts";

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
  caso: string;
  /** Si corrió con otros como una corrida del job: un solo orquestador para todos. */
  lote?: string;
  estado: EstadoCorrida;
  creada: number;
  inicio?: number;
  fin?: number;
  configuracion: Configuracion;
  eventos: Evento[];
  resultado?: Evento;
}

interface Trabajador {
  id: number;
  proceso: ChildProcessWithoutNullStreams;
  listo: boolean;
  /** Lo que está atendiendo: un correo, o todos los de un lote a la vez. */
  activas: Map<string, Corrida>;
}

/** Lo que espera trabajador: un correo suelto o un lote entero, que va a un solo trabajador. */
type Pedido = { tipo: "uno"; corrida: Corrida } | { tipo: "lote"; id: string; corridas: Corrida[]; concurrencia: number };

/** Pool de trabajadores de Python: cada uno atiende un correo a la vez. */
export class Motor extends EventEmitter {
  private trabajadores: Trabajador[] = [];
  private cola: Pedido[] = [];
  readonly corridas = new Map<string, Corrida>();
  private siguienteId = 1;
  private deseados = 0;
  errorInicio = "";
  /** Los últimos eventos de todas las corridas, para la vista de logs. */
  readonly recientes: Evento[] = [];

  constructor() {
    super();
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

  private nueva(caso: string, configuracion: Configuracion, lote?: string): Corrida {
    const corrida: Corrida = {
      id: `${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "")}-${randomUUID().slice(0, 6)}`,
      caso,
      ...(lote ? { lote } : {}),
      estado: "en_cola",
      creada: Date.now(),
      configuracion,
      eventos: [],
    };
    this.corridas.set(corrida.id, corrida);
    this.publicar({ tipo: "en_cola", corrida: corrida.id, t: Date.now() / 1000, caso, ...(lote ? { lote } : {}) });
    return corrida;
  }

  encolar(caso: string, configuracion: Configuracion): Corrida {
    const corrida = this.nueva(caso, configuracion);
    this.cola.push({ tipo: "uno", corrida });
    this.repartir();
    return corrida;
  }

  /**
   * Varios correos como una corrida del job: van juntos a un mismo trabajador,
   * que los atiende con un solo orquestador y de a `concurrencia` a la vez.
   */
  encolarLote(casos: string[], configuracion: Configuracion, concurrencia = 0): { lote: string; corridas: Corrida[] } {
    const lote = `lote-${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "")}-${randomUUID().slice(0, 4)}`;
    const corridas = casos.map((caso) => this.nueva(caso, configuracion, lote));
    this.cola.push({ tipo: "lote", id: lote, corridas, concurrencia });
    this.repartir();
    return { lote, corridas };
  }

  cancelarCola(): number {
    const pendientes = this.cola.splice(0).flatMap((p) => (p.tipo === "uno" ? [p.corrida] : p.corridas));
    for (const c of pendientes) {
      c.estado = "fallida";
      this.publicar({ tipo: "resultado", corrida: c.id, t: Date.now() / 1000, caso: c.caso, accion: "cancelada", motivo: "cancelada antes de empezar" });
    }
    return pendientes.length;
  }

  private lanzar(): void {
    const proceso = spawn(config.python, [config.trabajador], {
      env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8", RUTA_BANDEJA: config.bandeja, RUTA_VIVO: config.vivo, RUTA_TRABAJO: join(config.datos, "trabajo") },
      stdio: ["pipe", "pipe", "pipe"],
    });
    const t: Trabajador = { id: this.siguienteId++, proceso, listo: false, activas: new Map() };
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
    proceso.stderr.on("data", (trozo: string) => {
      for (const linea of trozo.split("\n")) {
        if (linea.trim()) this.recibir(t, { tipo: "consola", corrida: this.unica(t)?.id ?? null, t: Date.now() / 1000, linea, error: true });
      }
    });
    proceso.on("exit", (codigo) => {
      this.trabajadores = this.trabajadores.filter((x) => x !== t);
      for (const c of t.activas.values()) {
        if (c.estado === "procesando") {
          this.recibir(t, { tipo: "resultado", corrida: c.id, t: Date.now() / 1000, caso: c.caso, accion: "error", motivo: `el trabajador terminó (código ${codigo})` });
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

  private recibir(t: Trabajador, e: Evento): void {
    if (e.tipo === "listo") {
      t.listo = true;
      this.errorInicio = "";
      this.publicarEstado();
      this.repartir();
      return;
    }
    if (e.tipo === "fallo_inicio") {
      this.errorInicio = String(e.error ?? "fallo al iniciar el motor");
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
    for (const t of [...this.trabajadores]) {
      if (!t.listo || t.activas.size) continue;
      if (this.trabajadores.length > this.deseados) {
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
      const comun = { parametros: c.parametros, entorno: c.entorno, ia: c.ia, tope_s: c.topeSegundos, reglas: c.reglas.filter((r) => r.activa) };
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
    if (e.tipo !== "motor") {
      this.recientes.push(e);
      if (this.recientes.length > 4000) this.recientes.splice(0, this.recientes.length - 4000);
    }
    this.emit("evento", e);
  }

  private publicarEstado(): void {
    this.emit("evento", { tipo: "motor", corrida: null, t: Date.now() / 1000, ...this.estado() });
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
    for (const c of leidas.reverse()) if (!this.corridas.has(c.id)) this.corridas.set(c.id, c);
  }

  detener(): void {
    for (const t of this.trabajadores) t.proceso.kill();
  }
}
