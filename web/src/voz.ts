import { useCallback, useEffect, useRef, useState } from "react";

/*
 * La llamada con Vera. El micrófono sale como PCM de 16 bits a 16 kHz (con una
 * puerta de ruido para que el ruido de fondo no cuente como voz) y la voz de Vera
 * llega como PCM de 16 bits a 24 kHz, por un WebSocket al servidor, que la conecta
 * con Gemini Live; suena en un AudioWorklet con cola y colchón (/reproductor.js), sin
 * huecos entre fragmentos. Adaptado del asistente Bizz.
 */

export const VOCES: { id: string; nombre: string; tono: string }[] = [
  { id: "Laomedeia", nombre: "Laomedeia", tono: "animada y ágil" },
  { id: "Sadachbia", nombre: "Sadachbia", tono: "vivaz" },
  { id: "Sulafat", nombre: "Sulafat", tono: "cálida" },
  { id: "Kore", nombre: "Kore", tono: "firme" },
  { id: "Aoede", nombre: "Aoede", tono: "ligera" },
  { id: "Leda", nombre: "Leda", tono: "juvenil" },
  { id: "Callirrhoe", nombre: "Callirrhoe", tono: "tranquila" },
  { id: "Despina", nombre: "Despina", tono: "suave" },
  { id: "Vindemiatrix", nombre: "Vindemiatrix", tono: "amable" },
  { id: "Achernar", nombre: "Achernar", tono: "suave" },
  { id: "Zephyr", nombre: "Zephyr", tono: "brillante" },
  { id: "Pulcherrima", nombre: "Pulcherrima", tono: "directa" },
  { id: "Achird", nombre: "Achird", tono: "amable, masculina" },
  { id: "Puck", nombre: "Puck", tono: "animada, masculina" },
  { id: "Fenrir", nombre: "Fenrir", tono: "entusiasta, masculina" },
  { id: "Charon", nombre: "Charon", tono: "informativa, masculina" },
  { id: "Orus", nombre: "Orus", tono: "firme, masculina" },
  { id: "Iapetus", nombre: "Iapetus", tono: "clara, masculina" },
  { id: "Algieba", nombre: "Algieba", tono: "suave, masculina" },
  { id: "Sadaltager", nombre: "Sadaltager", tono: "conocedora, masculina" },
];

const TASA_MIC = 16_000;
const TASA_VOZ = 24_000;
/** Colchón de la vía de respaldo (sin worklet); el worklet arranca con 120 ms y crece si se vacía. */
const COLCHON_S = 0.12;

function nuevoEstado() {
  return { sonando: new Set<AudioBufferSourceNode>(), tocarEn: 0, listo: false, micMudo: false, puerta: crearPuerta(), previos: [] as ArrayBuffer[], veraSuena: false, colaMs: 0, colaEn: 0 };
}

// ------------------------------------------------------------ puerta de ruido
// Cada tramo (~43 ms) trae su energía total y la de la banda de la voz (200-3800 Hz, calculada en
// /microfono.js). Es habla si la banda supera el suelo de ruido por un margen Y concentra buena parte
// de la energía (el ventilador es grave; el siseo y los clics, agudos). El turno abre solo con habla
// sostenida (ATAQUE_MS acumulados; los tramos sin voz descuentan más rápido), y lo que no pasa sale como
// silencio (el flujo no se corta, para que Gemini vea el final de cada turno).
// - Calibración: el primer segundo mide el ruido de fondo (percentil 30, robusto si alguien ya habla).
// - Suelo adaptativo: sube despacio (3 s) y baja rápido (0,6 s) mientras no hay habla; no sube con Vera sonando.
// - Ruido estable: si la puerta lleva 1,5 s abierta con un nivel casi constante (desviación < 2 dB), no es
//   habla (la voz varía 6-10 dB entre tramos): se cierra y el suelo se pone en ese nivel.
// - Mientras Vera habla, interrumpirla exige más nivel y más tiempo (el eco y el ruido no la cortan).

const CALIBRACION_MS = 1000, PERCENTIL_SUELO = 0.3, TECHO_SUELO = -42, TECHO_ADAPTADO = -38, SUELO_MIN = -85, SILENCIO = -100;
const SUBE_MS = 3000, BAJA_MS = 600, MARGEN = 10, MIN_ABRE = -52, RAZON_VOZ = 0.35, ATAQUE_MS = 200, DESCUENTO = 1.5;
const MARGEN_HABLANDO = 26, MIN_HABLANDO = -32, ATAQUE_HABLANDO_MS = 600, COLA_MS = 400, PREVIO_MS = 150;
const HISTERESIS_DB = 3, ESTABLE_MS = 1500, ESTABLE_DB = 2;

type Tramo = { pcm: Float32Array; total: number; banda: number };

const aDb = (e: number) => (e > 0 ? Math.max(SILENCIO, 10 * Math.log10(e)) : SILENCIO);

/** Lo mismo que /microfono.js, para la vía de respaldo (ScriptProcessor). */
function crearMedidor(tasa: number) {
  const biquad = (pb: boolean, f: number) => {
    const w = (2 * Math.PI * f) / tasa, c = Math.cos(w), al = Math.sin(w) / (2 * Math.SQRT1_2), a0 = 1 + al;
    const b0 = (pb ? (1 - c) / 2 : (1 + c) / 2) / a0, b1 = (pb ? 1 - c : -(1 + c)) / a0;
    let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
    return (x: number) => {
      const y = b0 * x + b1 * x1 + b0 * x2 - ((-2 * c) / a0) * y1 - ((1 - al) / a0) * y2;
      x2 = x1; x1 = x; y2 = y1; y1 = y;
      return y;
    };
  };
  const pa = biquad(false, 200), pb = biquad(true, Math.min(3800, tasa * 0.45));
  return (pcm: Float32Array): Tramo => {
    let total = 0, banda = 0;
    for (let i = 0; i < pcm.length; i++) {
      const x = pcm[i]!, y = pb(pa(x));
      total += x * x;
      banda += y * y;
    }
    return { pcm, total: total / Math.max(1, pcm.length), banda: banda / Math.max(1, pcm.length) };
  };
}

function crearPuerta() {
  let reloj = 0, calibMs = 0, suelo = SUELO_MIN, calibrada = false, abierta = false, encima = 0, ultimaVoz = -Infinity, desde = 0;
  const calib: number[] = [];
  const recientes: { t: Float32Array; ms: number }[] = [];
  let recientesMs = 0;
  let niveles: { r: number; db: number }[] = [];
  const diag = { calibrado_db: null as number | null, suelo_db: SUELO_MIN, aperturas: 0, habla_ms: 0, cierres_por_ruido_estable: 0 };
  const adaptar = (db: number, ms: number, puedeSubir: boolean) => {
    const meta = Math.max(SUELO_MIN, Math.min(TECHO_ADAPTADO, db));
    if (meta > suelo && !puedeSubir) return;
    suelo += (meta - suelo) * (1 - Math.exp(-ms / (meta > suelo ? SUBE_MS : BAJA_MS)));
  };
  const cola = (ms: number) => {
    const out: Float32Array[] = [];
    let acc = 0;
    for (let i = recientes.length - 1; i >= 0 && acc < ms; i--) {
      out.unshift(recientes[i]!.t);
      acc += recientes[i]!.ms;
    }
    diag.habla_ms += acc;
    return out;
  };
  return {
    abierta: () => abierta,
    diag: () => ({ ...diag, suelo_db: Math.round(suelo * 10) / 10, habla_ms: Math.round(diag.habla_ms) }),
    procesar(x: Tramo, tasa: number, veraHabla: boolean): Float32Array[] {
      const t = x.pcm;
      const ms = (t.length / tasa) * 1000;
      const db = aDb(x.banda);
      const razon = x.total > 0 ? x.banda / x.total : 0;
      reloj += ms;
      const mudo = () => [new Float32Array(t.length)];
      recientes.push({ t, ms });
      recientesMs += ms;
      while (recientes.length > 1 && recientesMs - recientes[0]!.ms >= ATAQUE_HABLANDO_MS + PREVIO_MS) recientesMs -= recientes.shift()!.ms;
      if (!calibrada) {
        calib.push(db);
        calibMs += ms;
        if (calibMs < CALIBRACION_MS) return mudo();
        const orden = [...calib].sort((a, b) => a - b);
        suelo = Math.min(TECHO_SUELO, Math.max(SUELO_MIN, orden[Math.floor(orden.length * PERCENTIL_SUELO)]!));
        diag.calibrado_db = Math.round(suelo * 10) / 10;
        calibrada = true;
        return mudo();
      }
      const umbral = veraHabla ? Math.max(suelo + MARGEN_HABLANDO, MIN_HABLANDO) : Math.max(suelo + MARGEN, MIN_ABRE);
      if (!abierta) {
        const voz = db >= umbral && razon >= RAZON_VOZ;
        encima = voz ? encima + ms : Math.max(0, encima - ms * DESCUENTO);
        if (!voz) adaptar(db, ms, !veraHabla);
        if (encima < (veraHabla ? ATAQUE_HABLANDO_MS : ATAQUE_MS)) return mudo();
        abierta = true;
        desde = ultimaVoz = reloj;
        encima = 0;
        niveles = [];
        diag.aperturas++;
        return cola((veraHabla ? ATAQUE_HABLANDO_MS : ATAQUE_MS) + PREVIO_MS);
      }
      niveles.push({ r: reloj, db });
      while (niveles.length && reloj - niveles[0]!.r > ESTABLE_MS) niveles.shift();
      if (reloj - desde >= ESTABLE_MS && niveles.length > 8) {
        const media = niveles.reduce((a, n) => a + n.db, 0) / niveles.length;
        const desv = Math.sqrt(niveles.reduce((a, n) => a + (n.db - media) ** 2, 0) / niveles.length);
        if (desv < ESTABLE_DB) {
          abierta = false;
          suelo = Math.min(TECHO_ADAPTADO, Math.max(suelo, media));
          diag.cierres_por_ruido_estable++;
          return mudo();
        }
      }
      if (db >= umbral - HISTERESIS_DB && razon >= RAZON_VOZ * 0.8) ultimaVoz = reloj;
      if (reloj - ultimaVoz > COLA_MS) {
        abierta = false;
        return mudo();
      }
      diag.habla_ms += ms;
      return [t];
    },
  };
}

// Lo que mide la llamada (para las pruebas y para diagnosticar cortes): reproducción, puerta, contexto.
const DIAG: { salida?: unknown; puerta?: unknown; contexto_hz?: number; reproduccion?: "worklet" | "fuentes" } = {};
let puertaViva: ReturnType<typeof crearPuerta> | null = null;
(globalThis as { __vozDiag?: () => unknown }).__vozDiag = () => ({ ...DIAG, puerta: puertaViva?.diag() ?? DIAG.puerta });

function bajar(t: Float32Array, de: number, a = TASA_MIC): Float32Array {
  if (a >= de) return t;
  const r = de / a;
  const out = new Float32Array(Math.floor(t.length / r));
  for (let i = 0; i < out.length; i++) {
    const ini = Math.floor(i * r), fin = Math.min(t.length, Math.floor((i + 1) * r));
    let s = 0;
    for (let j = ini; j < fin; j++) s += t[j]!;
    out[i] = fin > ini ? s / (fin - ini) : 0;
  }
  return out;
}

function aPcm16(t: Float32Array): ArrayBuffer {
  const out = new ArrayBuffer(t.length * 2);
  const v = new DataView(out);
  for (let i = 0; i < t.length; i++) {
    const s = Math.max(-1, Math.min(1, t[i]!));
    v.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return out;
}

function dePcm16(d: ArrayBuffer): Float32Array<ArrayBuffer> {
  const v = new DataView(d);
  const out = new Float32Array(Math.floor(d.byteLength / 2));
  for (let i = 0; i < out.length; i++) out[i] = v.getInt16(i * 2, true) / 0x8000;
  return out;
}

/** Dos notas cortas: suben al conectar, bajan al colgar. */
function tono(ctx: AudioContext, tipo: "inicio" | "fin") {
  const notas = tipo === "inicio" ? [523.25, 783.99] : [659.25, 440];
  const t0 = ctx.currentTime + 0.02;
  notas.forEach((f, i) => {
    const en = t0 + i * 0.11;
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = "sine";
    o.frequency.value = f;
    g.gain.setValueAtTime(0, en);
    g.gain.linearRampToValueAtTime(0.12, en + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, en + 0.14);
    o.connect(g).connect(ctx.destination);
    o.start(en);
    o.stop(en + 0.16);
  });
}

// --------------------------------------------------------------- la llamada

export type EstadoLlamada = "inactiva" | "conectando" | "en_llamada" | "reconectando" | "terminando";
export type EventoVoz = { tipo: string; [k: string]: unknown };

export function useVoz(alEvento: (e: EventoVoz) => void) {
  const [estado, setEstado] = useState<EstadoLlamada>("inactiva");
  const [micMudo, setMicMudo] = useState(false);
  const [vozMuda, setVozMuda] = useState(() => {
    try {
      return localStorage.getItem("positiva-ia:voz:muda") === "1";
    } catch {
      return false;
    }
  });
  const [escuchado, setEscuchado] = useState("");
  const [dicho, setDicho] = useState("");
  const [error, setError] = useState("");
  const r = useRef<{
    ws?: WebSocket; entrada?: AudioContext; salida?: AudioContext; mic?: MediaStream; nodo?: AudioWorkletNode; respaldo?: ScriptProcessorNode;
    ganancia?: GainNode; analizadorVera?: AnalyserNode; analizadorMic?: AnalyserNode; reproductor?: AudioWorkletNode;
    sonando: Set<AudioBufferSourceNode>; tocarEn: number; listo: boolean; micMudo: boolean; puerta: ReturnType<typeof crearPuerta>; previos: ArrayBuffer[];
    veraSuena: boolean; colaMs: number; colaEn: number;
  }>(nuevoEstado());
  const evento = useRef(alEvento);
  evento.current = alEvento;

  const callar = () => {
    const s = r.current;
    s.reproductor?.port.postMessage({ tipo: "callar" });
    s.veraSuena = false;
    s.colaMs = 0;
    for (const x of s.sonando) {
      try {
        x.stop();
      } catch {
        /* ya paró */
      }
    }
    s.sonando.clear();
    s.tocarEn = 0;
  };

  const limpiar = useCallback(() => {
    const s = r.current;
    callar();
    s.nodo?.disconnect();
    s.respaldo?.disconnect();
    s.mic?.getTracks().forEach((t) => t.stop());
    if (s.ws && s.ws.readyState <= WebSocket.OPEN) s.ws.close();
    const salida = s.salida;
    if (salida) {
      try {
        tono(salida, "fin");
      } catch {
        /* sin audio */
      }
      setTimeout(() => void salida.close().catch(() => undefined), 500);
    }
    void s.entrada?.close().catch(() => undefined);
    s.reproductor?.disconnect();
    DIAG.puerta = s.puerta.diag();
    if (puertaViva === s.puerta) puertaViva = null;
    r.current = nuevoEstado();
    setEstado("inactiva");
    setMicMudo(false);
    setEscuchado("");
    setDicho("");
  }, []);

  const tocar = (datos: ArrayBuffer) => {
    const s = r.current;
    if (s.reproductor) {
      // al hilo de audio, transferido (sin copia ni trabajo en el hilo principal)
      s.reproductor.port.postMessage({ tipo: "pcm", datos }, [datos]);
      return;
    }
    // Respaldo sin worklet: fuentes encadenadas, cada una donde termina la anterior.
    if (!s.salida || !s.ganancia) return;
    const muestras = dePcm16(datos);
    if (!muestras.length) return;
    const b = s.salida.createBuffer(1, muestras.length, TASA_VOZ);
    b.copyToChannel(muestras, 0);
    const src = s.salida.createBufferSource();
    src.buffer = b;
    src.connect(s.ganancia);
    const ahora = s.salida.currentTime;
    const en = s.tocarEn > ahora ? s.tocarEn : ahora + COLCHON_S;
    src.start(en);
    s.tocarEn = en + b.duration;
    s.sonando.add(src);
    src.onended = () => s.sonando.delete(src);
  };

  const llamar = useCallback(async (conversacion: string, voz: string, contexto: Record<string, unknown>) => {
    if (r.current.ws) return;
    setError("");
    setEstado("conectando");
    const s = r.current;
    try {
      // Los contextos de audio se crean dentro del clic, para que el navegador deje sonar.
      s.entrada = new AudioContext();
      // La salida va a 24 kHz, la tasa de Gemini: el navegador remuestrea al dispositivo con su propio
      // remuestreador, continuo entre fragmentos. Si no lo acepta, el worklet remuestrea.
      try {
        s.salida = new AudioContext({ sampleRate: TASA_VOZ, latencyHint: "interactive" });
      } catch {
        s.salida = new AudioContext({ latencyHint: "interactive" });
      }
      s.ganancia = s.salida.createGain();
      s.ganancia.gain.value = vozMuda ? 0 : 1;
      s.analizadorVera = s.salida.createAnalyser();
      s.analizadorVera.fftSize = 512;
      s.ganancia.connect(s.analizadorVera).connect(s.salida.destination);
      DIAG.contexto_hz = s.salida.sampleRate;
      DIAG.salida = undefined;
      // La voz de Vera suena en un AudioWorklet con cola y colchón (/reproductor.js); sin él, fuentes encadenadas.
      try {
        await s.salida.audioWorklet.addModule(`/reproductor.js?v=2`);
        const rep = new AudioWorkletNode(s.salida, "reproductor", { numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1], processorOptions: { tasaFuente: TASA_VOZ } });
        rep.port.onmessage = (ev: MessageEvent<{ tipo: string; sonando: boolean; colaMs: number; diag: unknown }>) => {
          if (ev.data.tipo !== "estado" || r.current.reproductor !== rep) return;
          s.veraSuena = ev.data.sonando;
          s.colaMs = ev.data.colaMs;
          s.colaEn = performance.now();
          DIAG.salida = ev.data.diag;
        };
        rep.connect(s.ganancia);
        s.reproductor = rep;
        DIAG.reproduccion = "worklet";
      } catch {
        DIAG.reproduccion = "fuentes";
      }
      s.mic = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      const fuente = s.entrada.createMediaStreamSource(s.mic);
      s.analizadorMic = s.entrada.createAnalyser();
      s.analizadorMic.fftSize = 512;
      fuente.connect(s.analizadorMic);
      const tasa = s.entrada.sampleRate;
      DIAG.puerta = undefined;
      puertaViva = s.puerta;
      const alTramo = (tramo: Tramo) => {
        const ws = s.ws;
        if (s.micMudo) return;
        const veraHabla = s.reproductor ? s.veraSuena : s.sonando.size > 0;
        const tramos = s.puerta.procesar(tramo, tasa, veraHabla).map((t) => aPcm16(bajar(t, tasa)));
        // Lo que se dice mientras conecta (hasta ~4 s) se guarda y sale al conectar.
        if (!s.listo || !ws || ws.readyState !== WebSocket.OPEN) {
          s.previos.push(...tramos);
          if (s.previos.length > 95) s.previos.splice(0, s.previos.length - 95);
          return;
        }
        for (const t of tramos) ws.send(t);
      };
      // La captura va en un AudioWorklet (/microfono.js, con dos reintentos); si el
      // navegador no lo carga, se usa la vía clásica para no perder la llamada.
      let conWorklet = false;
      for (let intento = 0; intento < 3 && !conWorklet; intento++) {
        try {
          await s.entrada.audioWorklet.addModule(`/microfono.js?v=3`);
          conWorklet = true;
        } catch {
          await new Promise((r) => setTimeout(r, 400 * (intento + 1)));
        }
      }
      if (conWorklet) {
        s.nodo = new AudioWorkletNode(s.entrada, "microfono");
        s.nodo.port.onmessage = (ev: MessageEvent<Tramo>) => alTramo(ev.data);
        fuente.connect(s.nodo);
      } else {
        const proc = s.entrada.createScriptProcessor(2048, 1, 1);
        const medir = crearMedidor(tasa);
        proc.onaudioprocess = (ev) => alTramo(medir(new Float32Array(ev.inputBuffer.getChannelData(0))));
        const mudo = s.entrada.createGain();
        mudo.gain.value = 0;
        fuente.connect(proc);
        proc.connect(mudo).connect(s.entrada.destination);
        s.respaldo = proc;
      }
    } catch (e) {
      const m = (e as Error).name === "NotAllowedError" ? "El navegador no dio permiso para usar el micrófono." : `No pude abrir el micrófono: ${(e as Error).message}`;
      setError(m);
      limpiar();
      return;
    }
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/voz/${encodeURIComponent(conversacion)}`);
    ws.binaryType = "arraybuffer";
    s.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ tipo: "inicio", voz, pantalla: contexto, flujo: contexto.flujo }));
    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) {
        tocar(ev.data);
        return;
      }
      const e = JSON.parse(String(ev.data)) as EventoVoz;
      if (e.tipo === "listo") {
        s.listo = true;
        for (const t of s.previos.splice(0)) ws.send(t);
        setEstado("en_llamada");
        if (s.salida) tono(s.salida, "inicio");
      } else if (e.tipo === "escuchado") setEscuchado(String(e.total ?? ""));
      else if (e.tipo === "dicho") setDicho(String(e.total ?? ""));
      else if (e.tipo === "interrumpido") callar();
      else if (e.tipo === "turno") {
        // lo que quede en la cola es el final del turno: cuando se acabe no es un corte
        s.reproductor?.port.postMessage({ tipo: "fin_turno" });
        setEscuchado("");
        setDicho("");
      } else if (e.tipo === "error") setError(String(e.texto ?? "La llamada falló."));
      else if (e.tipo === "terminando") setEstado("terminando");
      else if (e.tipo === "reconectando") setEstado("reconectando");
      else if (e.tipo === "reconectado") setEstado("en_llamada");
      evento.current(e);
    };
    ws.onclose = (ev) => {
      if (ev.code === 4429) setError("Hay demasiadas llamadas abiertas; cierra alguna e intenta otra vez.");
      else if (ev.code === 4404) setError("Esa conversación ya no existe.");
      const salida = s.salida;
      const falta = s.reproductor
        ? (s.veraSuena ? Math.max(0, s.colaMs - (performance.now() - s.colaEn)) / 1000 + 0.15 : 0)
        : salida && s.sonando.size ? Math.max(0, s.tocarEn - salida.currentTime) : 0;
      // El micrófono se apaga ya; la voz de Vera termina su frase y después suena el tono de fin.
      s.listo = false;
      s.mic?.getTracks().forEach((t) => t.stop());
      setTimeout(() => {
        limpiar();
        evento.current({ tipo: "colgada" });
      }, Math.min(15000, falta * 1000 + 250));
    };
  }, [limpiar, vozMuda]);

  const colgar = useCallback(() => {
    const ws = r.current.ws;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ tipo: "fin" }));
    setEstado("terminando");
    // Si el servidor no cierra a tiempo, se cierra aquí (dejando sonar lo que falte).
    setTimeout(() => r.current.ws && r.current.ws.readyState !== WebSocket.CLOSED && r.current.ws.close(), 4000);
  }, [limpiar]);

  const alternarMic = useCallback(() => {
    const s = r.current;
    s.micMudo = !s.micMudo;
    setMicMudo(s.micMudo);
    if (s.micMudo && s.ws?.readyState === WebSocket.OPEN) s.ws.send(JSON.stringify({ tipo: "mic_off" }));
  }, []);

  const alternarVoz = useCallback(() => {
    setVozMuda((v) => {
      const n = !v;
      if (r.current.ganancia) r.current.ganancia.gain.value = n ? 0 : 1;
      try {
        localStorage.setItem("positiva-ia:voz:muda", n ? "1" : "0");
      } catch {
        /* sin almacenamiento */
      }
      return n;
    });
  }, []);

  const enviarTexto = useCallback((texto: string) => {
    const ws = r.current.ws;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ tipo: "texto", texto }));
  }, []);

  const avisarPantalla = useCallback((pantalla: Record<string, unknown>) => {
    const ws = r.current.ws;
    if (ws?.readyState === WebSocket.OPEN && r.current.listo) ws.send(JSON.stringify({ tipo: "pantalla", pantalla }));
  }, []);

  useEffect(() => () => limpiar(), [limpiar]);

  return {
    estado, micMudo, vozMuda, escuchado, dicho, error, setError,
    llamar, colgar, alternarMic, alternarVoz, enviarTexto, avisarPantalla,
    analizadores: () => ({ vera: r.current.analizadorVera, mic: r.current.analizadorMic, micMudo: r.current.micMudo }),
  };
}
