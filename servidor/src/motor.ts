import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { appendFile, mkdir } from "node:fs/promises";
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
  corrida: Corrida | null;
}

/** Pool de trabajadores de Python: cada uno atiende un correo a la vez. */
export class Motor extends EventEmitter {
  private trabajadores: Trabajador[] = [];
  private cola: Corrida[] = [];
  readonly corridas = new Map<string, Corrida>();
  private siguienteId = 1;
  private deseados = 0;
  errorInicio = "";

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
      trabajadores: this.trabajadores.map((t) => ({ id: t.id, listo: t.listo, corrida: t.corrida?.id ?? null, caso: t.corrida?.caso ?? null })),
      enCola: this.cola.map((c) => c.id),
      errorInicio: this.errorInicio,
    };
  }

  encolar(caso: string, configuracion: Configuracion): Corrida {
    const corrida: Corrida = {
      id: `${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "")}-${randomUUID().slice(0, 6)}`,
      caso,
      estado: "en_cola",
      creada: Date.now(),
      configuracion,
      eventos: [],
    };
    this.corridas.set(corrida.id, corrida);
    this.cola.push(corrida);
    this.publicar({ tipo: "en_cola", corrida: corrida.id, t: Date.now() / 1000, caso });
    this.repartir();
    return corrida;
  }

  cancelarCola(): number {
    const n = this.cola.length;
    for (const c of this.cola.splice(0)) {
      c.estado = "fallida";
      this.publicar({ tipo: "resultado", corrida: c.id, t: Date.now() / 1000, caso: c.caso, accion: "cancelada", motivo: "cancelada antes de empezar" });
    }
    return n;
  }

  private lanzar(): void {
    const proceso = spawn(config.python, [config.trabajador], {
      env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8", RUTA_BANDEJA: config.bandeja, RUTA_TRABAJO: join(config.datos, "trabajo") },
      stdio: ["pipe", "pipe", "pipe"],
    });
    const t: Trabajador = { id: this.siguienteId++, proceso, listo: false, corrida: null };
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
        if (linea.trim()) this.recibir(t, { tipo: "consola", corrida: t.corrida?.id ?? null, t: Date.now() / 1000, linea, error: true });
      }
    });
    proceso.on("exit", (codigo) => {
      this.trabajadores = this.trabajadores.filter((x) => x !== t);
      if (t.corrida && t.corrida.estado === "procesando") {
        this.recibir(t, { tipo: "resultado", corrida: t.corrida.id, t: Date.now() / 1000, caso: t.corrida.caso, accion: "error", motivo: `el trabajador terminó (código ${codigo})` });
      }
      if (this.trabajadores.length < this.deseados && !this.errorInicio) setTimeout(() => this.ajustar(this.deseados), 1000);
      this.publicarEstado();
    });
  }

  private linea(t: Trabajador, linea: string): void {
    if (!linea.startsWith(MARCA)) {
      if (linea.trim()) this.recibir(t, { tipo: "consola", corrida: t.corrida?.id ?? null, t: Date.now() / 1000, linea });
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
    const corrida = t.corrida;
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
      t.corrida = null;
      this.publicarEstado();
      this.repartir();
    }
  }

  private repartir(): void {
    for (const t of [...this.trabajadores]) {
      if (!t.listo || t.corrida) continue;
      if (this.trabajadores.length > this.deseados) {
        t.listo = false;
        t.proceso.stdin.end();
        continue;
      }
      const corrida = this.cola.shift();
      if (!corrida) return;
      t.corrida = corrida;
      corrida.estado = "procesando";
      corrida.inicio = Date.now();
      const c = corrida.configuracion;
      t.proceso.stdin.write(
        JSON.stringify({ cmd: "procesar", corrida: corrida.id, caso: corrida.caso, parametros: c.parametros, entorno: c.entorno, ia: c.ia, tope_s: c.topeSegundos }) + "\n",
      );
      this.publicar({ tipo: "asignada", corrida: corrida.id, t: Date.now() / 1000, caso: corrida.caso, trabajador: t.id });
      this.publicarEstado();
    }
  }

  private publicar(e: Evento): void {
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

  detener(): void {
    for (const t of this.trabajadores) t.proceso.kill();
  }
}
