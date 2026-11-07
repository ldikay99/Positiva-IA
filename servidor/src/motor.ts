import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { appendFile, mkdir, readdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { config } from "./config.ts";
import type { Configuracion } from "./configuracion.ts";
import { enmascarar, enmascararTexto, variantes } from "./enmascarar.ts";
import { Planificador, type Pool } from "./planificador.ts";
import { porDefecto, topeGlobal, type Recursos } from "./recursos.ts";
import { cpuEntre, leerProceso, limiteTrabajadorMb, type Lectura } from "./medicion.ts";

const MARCA = "\x1eEVT ";
/** Cada cuánto se miden los trabajadores y se revisan los topes de tiempo (las pruebas lo bajan). */
const VIGILANCIA_MS = Number(process.env.MOTOR_VIGILANCIA_MS ?? 5000);
/** Un trabajador libre de un flujo sin trabajadores calientes se retira tras este tiempo. */
const OCIOSO_MS = Number(process.env.TRABAJADOR_OCIOSO_S ?? 600) * 1000;
/** Lo que se le da a un trabajador para arrancar antes de darlo por trabado. */
const ARRANQUE_MAX_MS = 180_000;
/** El motor declarativo se detiene solo al llegar a tiempo_max_s; el servidor espera esto más antes de cortarlo. */
const GRACIA_MS = Number(process.env.MOTOR_GRACIA_MS ?? 10_000);

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
  /** Ya se le cerró la entrada o se le mandó la señal: está saliendo. */
  saliendo?: boolean;
  /** Si arrancó con la radicación real (compuerta encendida y segunda llave). */
  real: boolean;
  /** Por qué se cortó (tope de tiempo, reinicio), para el resultado de sus corridas. */
  cortado?: string;
  lanzado: number;
  /** Desde cuándo atiende lo que tiene (ms) y hasta cuándo puede (tiempo máximo). */
  desde?: number;
  limite?: number;
  libreDesde: number;
  lectura?: Lectura | null;
  cpu?: number | null;
  memoriaMb?: number | null;
  /** Lo más alto que llegó su memoria. */
  picoMb?: number;
}

/** Lo que espera trabajador: un correo suelto o un lote entero, que va a un solo trabajador. */
type Pedido = { tipo: "uno"; corrida: Corrida } | { tipo: "lote"; id: string; corridas: Corrida[]; concurrencia: number };

/** El planificador de toda la plataforma: reparte TRABAJADORES_MAX entre los flujos con justicia. */
export const planificador = new Planificador(topeGlobal);

/** Pool de trabajadores de Python de UN flujo: cada uno atiende un correo (o un lote) a la vez. */
export class Motor extends EventEmitter implements Pool {
  private trabajadores: Trabajador[] = [];
  private cola: Pedido[] = [];
  readonly corridas = new Map<string, Corrida>();
  private siguienteId = 1;
  /** Cuántos trabajadores quiere tener calientes aunque no haya trabajo. */
  private caliente = 0;
  private detenido = false;
  /** Tras una salida anormal no se relanza enseguida (un trabajador que se cae al arrancar no gira en loco). */
  private proximoLanzamiento = 0;
  private vigilancia: NodeJS.Timeout;
  errorInicio = "";
  /** Los últimos eventos de todas las corridas, para la vista de logs. */
  readonly recientes: Evento[] = [];
  /** Las salidas y cortes recientes de sus trabajadores (para la salud). */
  readonly incidentes: { t: number; trabajador: number; que: string }[] = [];

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
  /** Los límites del flujo (recursos.ts); el servidor pone los guardados. */
  recursos: () => Recursos = () => porDefecto({ id: this.flujo, tipo: "declarativo" });
  /** Todas las formas de los secretos que conoció algún trabajador de este flujo. */
  private mascaras: string[] = [];

  constructor(flujo = "pqrd", trabajador: string = config.trabajador, ordenExtra: (corrida: Corrida) => Record<string, unknown> = () => ({})) {
    super();
    this.flujo = flujo;
    this.trabajador = trabajador;
    this.ordenExtra = ordenExtra;
    this.setMaxListeners(0);
    planificador.registrar(this);
    this.vigilancia = setInterval(() => this.vigilar(), VIGILANCIA_MS);
    this.vigilancia.unref();
  }

  /** Cuántos trabajadores tener calientes (los demás arrancan cuando hay trabajo, hasta su límite). */
  ajustar(cantidad: number): void {
    this.detenido = false;
    this.caliente = Math.max(0, Math.floor(cantidad));
    this.pedirReparto();
  }

  private pedirReparto(): void {
    planificador.pedir();
  }

  // ------------------------------------------------------------ Pool (planificador)

  procesos(): number {
    return this.trabajadores.length;
  }

  private ocupados(): number {
    return this.trabajadores.filter((t) => t.activas.size > 0).length;
  }

  private util(t: Trabajador): boolean {
    return !t.saliendo && !t.retirar;
  }

  libres(): number {
    return this.trabajadores.filter((t) => t.listo && !t.activas.size && this.util(t)).length;
  }

  private arrancando(): number {
    return this.trabajadores.filter((t) => !t.listo && this.util(t)).length;
  }

  quiere(): number {
    if (this.detenido) return 0;
    const r = this.recursos();
    return Math.min(r.corridas_simultaneas, r.trabajadores, this.ocupados() + this.cola.length);
  }

  espera(): number {
    const p = this.cola[0];
    if (!p) return 0;
    return Date.now() - (p.tipo === "uno" ? p.corrida.creada : p.corridas[0]!.creada);
  }

  falta(cuota: number): number {
    const ocupados = this.ocupados();
    const objetivo = Math.min(cuota, ocupados + this.cola.length);
    return objetivo - ocupados - this.arrancando() - this.libres();
  }

  puedeLanzar(): boolean {
    return !this.detenido && !this.errorInicio && Date.now() >= this.proximoLanzamiento && this.trabajadores.length < this.recursos().trabajadores;
  }

  calientes(): number {
    return this.detenido ? 0 : Math.min(this.caliente, this.recursos().trabajadores);
  }

  retirarLibre(): boolean {
    const t = this.trabajadores.find((x) => x.listo && !x.activas.size && this.util(x));
    if (!t) return false;
    this.cerrar(t);
    return true;
  }

  /** Le cierra la entrada a un trabajador libre: termina solo, sin cortar nada. */
  private cerrar(t: Trabajador): void {
    t.listo = false;
    t.saliendo = true;
    t.proceso.stdin.end();
  }

  /** Detiene un trabajador (ocupado o trabado): SIGTERM y, si no sale en 5 s, SIGKILL. Sus corridas quedan con el motivo. */
  private matar(t: Trabajador, motivo: string): void {
    t.cortado ??= motivo;
    t.saliendo = true;
    t.listo = false;
    this.incidentes.push({ t: Date.now(), trabajador: t.id, que: motivo });
    if (this.incidentes.length > 50) this.incidentes.splice(0, this.incidentes.length - 50);
    t.proceso.kill("SIGTERM");
    const p = t.proceso;
    setTimeout(() => {
      if (p.exitCode === null && p.signalCode === null) p.kill("SIGKILL");
    }, 5000).unref();
  }

  despachar(cuota: number): void {
    const real = this.radicacionReal();
    const r = this.recursos();
    for (const t of [...this.trabajadores]) {
      if (!t.listo || t.activas.size || t.saliendo) continue;
      // Un trabajador que arrancó con otra compuerta (o con otro entorno) no recibe nada más: se retira y arranca uno nuevo.
      if (t.retirar || t.real !== real || this.trabajadores.filter((x) => !x.saliendo).length > r.trabajadores) {
        this.cerrar(t);
        continue;
      }
      if (!this.cola.length || this.ocupados() >= Math.min(cuota, r.corridas_simultaneas)) continue;
      this.asignar(t, this.cola.shift()!, r);
    }
  }

  lanzar(): void {
    this.recordarSecretos();
    const real = this.radicacionReal();
    // La compuerta va DESPUÉS del entorno del flujo: el flujo no la puede poner (y entornos.ts tampoco lo deja).
    const proceso = spawn(config.python, [this.trabajador], {
      env: { ...process.env, ...this.entornoDeProceso(), RADICACION_REAL: real ? "si" : "no", PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8", RUTA_BANDEJA: config.bandeja, RUTA_VIVO: config.vivo, RUTA_TRABAJO: join(config.datos, "trabajo") },
      stdio: ["pipe", "pipe", "pipe"],
    });
    const t: Trabajador = { id: this.siguienteId++, proceso, listo: false, activas: new Map(), real, lanzado: Date.now(), libreDesde: Date.now() };
    this.trabajadores.push(t);
    // si el proceso se cierra antes de leer, escribirle no tumba el servidor
    proceso.stdin.on("error", () => {});
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
    proceso.on("error", (e) => {
      // no se pudo lanzar (python no existe…): sale igual por «exit»
      this.errorInicio = `no se pudo lanzar el trabajador: ${e.message}`;
    });
    proceso.on("exit", (codigo, senal) => {
      this.trabajadores = this.trabajadores.filter((x) => x !== t);
      for (const c of t.activas.values()) {
        if (c.estado === "procesando") {
          this.recibir(t, { tipo: "resultado", corrida: c.id, t: Date.now() / 1000, caso: c.caso, accion: "error", motivo: t.cortado ?? `el trabajador terminó (código ${codigo ?? senal})` });
        }
      }
      t.activas.clear();
      // Una salida que nadie pidió (se cayó): se espera un segundo antes de lanzar otro.
      if (!t.saliendo) {
        this.proximoLanzamiento = Date.now() + 1000;
        this.incidentes.push({ t: Date.now(), trabajador: t.id, que: `terminó solo (código ${codigo ?? senal})` });
        setTimeout(() => this.pedirReparto(), 1000).unref();
      }
      this.publicarEstado();
      this.pedirReparto();
    });
  }

  estado() {
    const r = this.recursos();
    const limiteMb = limiteTrabajadorMb(topeGlobal());
    const ahora = Date.now();
    return {
      trabajadores: this.trabajadores.map((t) => {
        const activas = [...t.activas.values()];
        const ocupadoS = t.desde ? Math.round((ahora - t.desde) / 1000) : 0;
        return {
          id: t.id, pid: t.proceso.pid ?? null, listo: t.listo, saliendo: !!t.saliendo,
          corrida: activas[0]?.id ?? null, caso: activas[0]?.caso ?? null, corridas: activas.map((c) => c.id), lote: activas[0]?.lote ?? null,
          ocupado_s: ocupadoS, libre_s: activas.length ? 0 : Math.round((ahora - t.libreDesde) / 1000),
          cpu: t.cpu ?? null, memoria_mb: t.memoriaMb ?? null, pico_mb: t.picoMb ?? null,
          alertas: this.alertasDe(t, r, limiteMb, ocupadoS),
        };
      }),
      enCola: this.cola.flatMap((p) => (p.tipo === "uno" ? [p.corrida.id] : p.corridas.map((c) => c.id))),
      errorInicio: this.errorInicio,
    };
  }

  /** Avisos suaves: memoria alta, una corrida que se acerca a su tiempo máximo, un arranque que no termina. */
  private alertasDe(t: Trabajador, r: Recursos, limiteMb: number, ocupadoS: number): string[] {
    const a: string[] = [];
    if (t.memoriaMb != null && t.memoriaMb > limiteMb * 0.8) a.push(`memoria alta: ${t.memoriaMb} MB de ${limiteMb} MB (${Math.round((t.memoriaMb / limiteMb) * 100)} %)`);
    if (t.activas.size && ocupadoS > r.tiempo_max_s * 0.8) a.push(`lleva ${ocupadoS} s en la misma corrida; se detiene a los ${r.tiempo_max_s} s`);
    if (!t.listo && !t.saliendo && Date.now() - t.lanzado > 60_000) a.push(`arrancando hace ${Math.round((Date.now() - t.lanzado) / 1000)} s`);
    if (t.cortado) a.push(t.cortado);
    return a;
  }

  /**
   * Cada 5 s: mide CPU y memoria de cada trabajador y aplica los topes. Un trabajador que
   * pasó el tiempo máximo con lo que atiende se detiene (sus corridas quedan en error con el
   * motivo) y el planificador arranca otro; uno libre que pasa su límite de memoria se
   * recicla; uno libre de más se retira.
   */
  private vigilar(): void {
    const r = this.recursos();
    const limiteMb = limiteTrabajadorMb(topeGlobal());
    const ahora = Date.now();
    let cambio = false;
    for (const t of [...this.trabajadores]) {
      const l = leerProceso(t.proceso.pid);
      if (l) {
        t.cpu = cpuEntre(t.lectura, l);
        t.memoriaMb = Math.round(l.rssKb / 1024);
        t.picoMb = Math.max(t.picoMb ?? 0, t.memoriaMb);
        t.lectura = l;
      }
      if (t.saliendo) continue;
      if (t.activas.size && t.limite && ahora > t.limite) {
        this.matar(t, `pasó el tiempo máximo de ${r.tiempo_max_s} s: se detuvo su trabajador y arranca otro`);
        cambio = true;
      } else if (!t.listo && ahora - t.lanzado > ARRANQUE_MAX_MS) {
        this.matar(t, `el trabajador no terminó de arrancar en ${ARRANQUE_MAX_MS / 1000} s`);
        cambio = true;
      } else if (t.listo && !t.activas.size && t.memoriaMb != null && t.memoriaMb > limiteMb) {
        this.incidentes.push({ t: ahora, trabajador: t.id, que: `reciclado: usaba ${t.memoriaMb} MB (límite ${limiteMb} MB)` });
        this.cerrar(t);
        cambio = true;
      } else if (t.listo && !t.activas.size && ahora - t.libreDesde > OCIOSO_MS && this.trabajadores.filter((x) => !x.saliendo).length > this.calientes()) {
        this.cerrar(t);
        cambio = true;
      }
    }
    if (cambio) this.pedirReparto();
    if (this.trabajadores.length) this.publicarEstado();
  }

  /**
   * Reinicia un trabajador: si está ocupado, se detiene (sus corridas quedan en error con el
   * motivo); si está libre, se retira. En los dos casos el planificador arranca otro si hace falta.
   */
  reiniciarTrabajador(id: number, motivo = "reiniciado por una persona"): boolean {
    const t = this.trabajadores.find((x) => x.id === id);
    if (!t) return false;
    if (t.activas.size || !t.listo) this.matar(t, motivo);
    else this.cerrar(t);
    this.pedirReparto();
    return true;
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

  /** Cuántas corridas esperan en la cola. */
  enEspera(): number {
    return this.cola.reduce((n, p) => n + (p.tipo === "uno" ? 1 : p.corridas.length), 0);
  }

  /** Si caben `n` corridas más en la cola (cola_max del flujo). */
  cabe(n = 1): boolean {
    return this.enEspera() + n <= this.recursos().cola_max;
  }

  encolar(caso: string, configuracion: Configuracion, extra: { entrada?: unknown; origen?: string } = {}): Corrida {
    const corrida = this.nueva(caso, configuracion, undefined, extra);
    this.cola.push({ tipo: "uno", corrida });
    this.pedirReparto();
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
    this.pedirReparto();
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
    this.pedirReparto();
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
      n += t.activas.size;
      this.matar(t, motivo);
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
      if (t.saliendo) return;
      t.listo = true;
      t.libreDesde = Date.now();
      this.errorInicio = "";
      this.publicarEstado();
      this.pedirReparto();
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
      t.desde = undefined;
      t.limite = undefined;
      t.libreDesde = Date.now();
      this.publicarEstado();
      this.pedirReparto();
    }
  }

  private asignar(t: Trabajador, pedido: Pedido, r: Recursos): void {
    const corridas = pedido.tipo === "uno" ? [pedido.corrida] : pedido.corridas;
    for (const corrida of corridas) {
      t.activas.set(corrida.id, corrida);
      corrida.estado = "procesando";
      corrida.inicio = Date.now();
    }
    t.desde = Date.now();
    // el tope es de la corrida o del lote entero (como el timeout de la tarea del job)
    t.limite = t.desde + r.tiempo_max_s * 1000 + GRACIA_MS;
    const c = corridas[0]!.configuracion;
    const comun = { parametros: c.parametros, entorno: c.entorno, ia: c.ia, tope_s: c.topeSegundos, tiempo_max_s: r.tiempo_max_s, reglas: c.reglas.filter((x) => x.activa), ...this.ordenExtra(corridas[0]!), radicacion_real: t.real };
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
    this.detenido = true;
    this.caliente = 0;
    clearInterval(this.vigilancia);
    for (const t of this.trabajadores) {
      t.saliendo = true;
      t.proceso.kill();
    }
    planificador.quitar(this);
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
