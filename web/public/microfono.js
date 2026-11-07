// Captura del micrófono para la llamada con Vera: junta tramos de ~43 ms y los
// manda al hilo principal, que aplica la puerta de ruido y los baja a 16 kHz.
class Microfono extends AudioWorkletProcessor {
  constructor() {
    super();
    this.tramo = new Float32Array(2048);
    this.n = 0;
  }
  process(entradas) {
    const canal = entradas[0] && entradas[0][0];
    if (!canal) return true;
    for (let i = 0; i < canal.length; i++) {
      this.tramo[this.n++] = canal[i];
      if (this.n === this.tramo.length) {
        this.port.postMessage(this.tramo);
        this.tramo = new Float32Array(2048);
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor("microfono", Microfono);
