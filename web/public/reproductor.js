// La voz de Vera, en el hilo de audio: una cola de fragmentos PCM16 (24 kHz) que suena sin huecos.
// - Cada turno arranca con un colchón (120 ms) para absorber la llegada irregular por la red.
// - Los fragmentos se leen uno tras otro con la misma fase: ninguno empieza «cuando llega», sino
//   exactamente donde terminó el anterior.
// - Remuestreo continuo de 24 kHz a la tasa del contexto (si el contexto ya va a 24 kHz, el paso es 1
//   y el navegador hace el resto con su propio remuestreador).
// - Si la cola se vacía en mitad de un turno (subdesborde) se cuenta, se mide el silencio que se oyó
//   (de vaciarse a volver a sonar) y el colchón crece 60 ms (hasta 300 ms) para el resto de la llamada.
// Mensajes: {tipo:"pcm", datos:ArrayBuffer} · {tipo:"callar"} · {tipo:"fin_turno"} · {tipo:"diag"}.
// Responde con {tipo:"estado", sonando, colaMs, diag} al empezar o dejar de sonar y cada ~200 ms mientras suena.
const COLCHON_MS = 120, COLCHON_MAX_MS = 300, PASO_COLCHON_MS = 60, ESPERA_MAX_MS = 300, PAUSA_MS = 1500, FUNDIDO = 96;

class Reproductor extends AudioWorkletProcessor {
  constructor(opciones) {
    super();
    const o = (opciones && opciones.processorOptions) || {};
    this.tasa = o.tasaFuente || 24000;
    this.paso = this.tasa / sampleRate;
    this.cola = [];
    this.pos = 0; // índice dentro de cola[0]
    this.disponibles = 0; // muestras de la fuente en cola
    this.reiniciar();
    this.estado = "quieto"; // quieto · llenando · sonando · vacio
    this.desde = 0;
    this.finTurno = false;
    this.recarga = null; // cuándo se vació en mitad de un turno (para medir el silencio que se oyó)
    this.fundido = 0;
    this.colchon = COLCHON_MS;
    this.ultimoAviso = 0;
    this.d = { fragmentos: 0, subdesbordes: 0, huecos_ms: [], pausas: 0, arranques: 0, cola_max_ms: 0, colchon_ms: COLCHON_MS, tasa_contexto: sampleRate, paso: this.paso, tocado_s: 0 };
    this.port.onmessage = (ev) => this.mensaje(ev.data);
  }

  reiniciar() {
    this.fase = 1; // en 1: la primera muestra que llegue se lee de inmediato
    this.a = 0; // muestra anterior y siguiente, para interpolar
    this.b = 0;
  }

  ms(n) {
    return (n / this.tasa) * 1000;
  }

  mensaje(m) {
    if (m.tipo === "pcm") {
      const v = new Int16Array(m.datos);
      if (!v.length) return;
      const f = new Float32Array(v.length);
      for (let i = 0; i < v.length; i++) f[i] = v[i] / 32768;
      this.cola.push(f);
      this.disponibles += f.length;
      this.d.fragmentos++;
      this.d.cola_max_ms = Math.max(this.d.cola_max_ms, Math.round(this.ms(this.disponibles)));
      if (this.estado === "vacio") {
        const hueco = (currentTime - this.desde) * 1000;
        if (hueco < PAUSA_MS) {
          this.d.subdesbordes++;
          this.recarga = this.desde;
          this.colchon = Math.min(COLCHON_MAX_MS, this.colchon + PASO_COLCHON_MS);
          this.d.colchon_ms = this.colchon;
        } else this.d.pausas++;
        this.llenar();
      } else if (this.estado === "quieto") this.llenar();
    } else if (m.tipo === "callar") {
      this.cola = [];
      this.pos = 0;
      this.disponibles = 0;
      this.finTurno = false;
      this.reiniciar();
      this.cambiar("quieto");
    } else if (m.tipo === "fin_turno") {
      this.finTurno = true;
      if (this.estado === "vacio") {
        this.reiniciar();
        this.finTurno = false;
        this.cambiar("quieto");
      }
    } else if (m.tipo === "diag") this.avisar();
  }

  llenar() {
    this.finTurno = false;
    this.desde = currentTime;
    this.cambiar("llenando");
  }

  cambiar(estado) {
    if (estado === this.estado) return;
    const antes = this.suena();
    this.estado = estado;
    if (estado === "sonando") {
      this.fundido = FUNDIDO;
      this.d.arranques++;
      if (this.recarga !== null) {
        if (this.d.huecos_ms.length < 50) this.d.huecos_ms.push(Math.round((currentTime - this.recarga) * 1000));
        this.recarga = null;
      }
    }
    if (estado === "quieto") this.recarga = null;
    if (estado === "vacio") this.desde = currentTime;
    if (antes !== this.suena()) this.avisar();
  }

  /** Vera suena (o está por sonar): no cuenta el hueco de un subdesborde ni la espera de una herramienta. */
  suena() {
    return this.estado === "llenando" || this.estado === "sonando";
  }

  avisar() {
    this.ultimoAviso = currentTime;
    this.port.postMessage({ tipo: "estado", sonando: this.suena(), colaMs: Math.round(this.ms(this.disponibles)), diag: this.d });
  }

  siguiente() {
    // la próxima muestra de la fuente, o null si no hay
    while (this.cola.length && this.pos >= this.cola[0].length) {
      this.cola.shift();
      this.pos = 0;
    }
    if (!this.cola.length) return null;
    this.disponibles--;
    return this.cola[0][this.pos++];
  }

  process(_entradas, salidas) {
    const out = salidas[0] && salidas[0][0];
    if (!out) return true;
    if (this.estado === "llenando") {
      const espera = (currentTime - this.desde) * 1000;
      if (this.ms(this.disponibles) >= this.colchon || this.finTurno || espera >= ESPERA_MAX_MS) this.cambiar("sonando");
    }
    if (this.estado !== "sonando") {
      out.fill(0);
    } else {
      let i = 0;
      for (; i < out.length; i++) {
        // interpolación lineal entre la muestra anterior (a) y la siguiente (b); la fase sigue de un
        // fragmento al otro, así que no hay costura entre ellos
        while (this.fase >= 1) {
          const n = this.siguiente();
          if (n === null) break;
          this.a = this.b;
          this.b = n;
          this.fase -= 1;
        }
        if (this.fase >= 1) {
          // se acabó la cola en mitad del bloque
          out.fill(0, i);
          if (this.finTurno) this.reiniciar();
          this.cambiar(this.finTurno ? "quieto" : "vacio");
          this.finTurno = false;
          break;
        }
        let y = this.a + (this.b - this.a) * this.fase;
        if (this.fundido > 0) {
          y *= 1 - this.fundido / FUNDIDO;
          this.fundido--;
        }
        out[i] = y;
        this.fase += this.paso;
      }
      this.d.tocado_s += i / sampleRate;
    }
    for (let c = 1; c < salidas[0].length; c++) salidas[0][c].set(out);
    if (this.suena() && currentTime - this.ultimoAviso > 0.2) this.avisar();
    return true;
  }
}
registerProcessor("reproductor", Reproductor);
