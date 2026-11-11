// Captura del micrófono para la llamada con Vera: junta tramos de ~43 ms y los manda al hilo
// principal (transferidos, sin copia) con su energía total y su energía en la banda de la voz
// (250-3800 Hz, dos biquads). La puerta de ruido del hilo principal decide con las dos: el ventilador
// (grave) y el siseo o los clics (agudos) dejan poca energía en la banda; la voz casi toda.
function biquad(tipo, f, tasa) {
  const w = (2 * Math.PI * f) / tasa, c = Math.cos(w), al = Math.sin(w) / (2 * Math.SQRT1_2);
  const b1 = tipo === "pb" ? 1 - c : -(1 + c), b0 = tipo === "pb" ? (1 - c) / 2 : (1 + c) / 2, a0 = 1 + al;
  return { b0: b0 / a0, b1: b1 / a0, b2: b0 / a0, a1: (-2 * c) / a0, a2: (1 - al) / a0, x1: 0, x2: 0, y1: 0, y2: 0 };
}

function filtrar(q, x) {
  const y = q.b0 * x + q.b1 * q.x1 + q.b2 * q.x2 - q.a1 * q.y1 - q.a2 * q.y2;
  q.x2 = q.x1;
  q.x1 = x;
  q.y2 = q.y1;
  q.y1 = y;
  return y;
}

class Microfono extends AudioWorkletProcessor {
  constructor() {
    super();
    this.tramo = new Float32Array(2048);
    this.n = 0;
    this.total = 0;
    this.banda = 0;
    this.pa = biquad("pa", 250, sampleRate);
    this.pb = biquad("pb", Math.min(3800, sampleRate * 0.45), sampleRate);
  }
  process(entradas) {
    const canal = entradas[0] && entradas[0][0];
    if (!canal) return true;
    for (let i = 0; i < canal.length; i++) {
      const x = canal[i];
      const y = filtrar(this.pb, filtrar(this.pa, x));
      this.total += x * x;
      this.banda += y * y;
      this.tramo[this.n++] = x;
      if (this.n === this.tramo.length) {
        this.port.postMessage({ pcm: this.tramo, total: this.total / this.n, banda: this.banda / this.n }, [this.tramo.buffer]);
        this.tramo = new Float32Array(2048);
        this.n = 0;
        this.total = 0;
        this.banda = 0;
      }
    }
    return true;
  }
}
registerProcessor("microfono", Microfono);
