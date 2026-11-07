import { useCallback, useEffect, useRef, useState } from "react";

/*
 * La llamada con Vera. El micrófono sale como PCM de 16 bits a 16 kHz (con una
 * puerta de ruido para que el ruido de fondo no cuente como voz) y la voz de Vera
 * llega como PCM de 16 bits a 24 kHz, por un WebSocket al servidor, que la conecta
 * con Gemini Live. Adaptado del asistente Bizz.
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

// ------------------------------------------------------------ puerta de ruido
// Mide cada tramo en dBFS contra un suelo de ruido adaptativo; lo que no pasa
// sale como silencio (el flujo no se corta, para que el servidor vea el final de
// cada turno). Mientras Vera habla exige más nivel y más tiempo para interrumpirla.

const CALIBRACION_MS = 500, TECHO_SUELO = -45, SUELO_MIN = -80, SILENCIO = -100;
const SUBE_MS = 4000, BAJA_MS = 800, MARGEN = 12, MIN_ABRE = -48, ATAQUE_MS = 200;
const MARGEN_HABLANDO = 24, MIN_HABLANDO = -34, ATAQUE_HABLANDO_MS = 450, COLA_MS = 350, PREVIO_MS = 150;

function nivelDb(t: Float32Array): number {
  let s = 0;
  for (let i = 0; i < t.length; i++) s += t[i]! * t[i]!;
  const rms = Math.sqrt(s / Math.max(1, t.length));
  return rms > 0 ? Math.max(SILENCIO, 20 * Math.log10(rms)) : SILENCIO;
}

function crearPuerta() {
  let reloj = 0, calib = 0, calibSuma = 0, suelo = SUELO_MIN, abierta = false, encima = 0, ultimaVoz = -Infinity;
  const recientes: { t: Float32Array; ms: number }[] = [];
  let recientesMs = 0;
  const adaptar = (db: number, ms: number, puedeSubir: boolean) => {
    const meta = Math.max(SUELO_MIN, db);
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
    return out;
  };
  return {
    abierta: () => abierta,
    procesar(t: Float32Array, tasa: number, veraHabla: boolean): Float32Array[] {
      const ms = (t.length / tasa) * 1000;
      const db = nivelDb(t);
      reloj += ms;
      const mudo = () => [new Float32Array(t.length)];
      if (calib < CALIBRACION_MS) {
        calib += ms;
        calibSuma += db * ms;
        if (calib >= CALIBRACION_MS) suelo = Math.min(TECHO_SUELO, Math.max(SUELO_MIN, calibSuma / calib));
        return mudo();
      }
      const umbral = veraHabla ? Math.max(suelo + MARGEN_HABLANDO, MIN_HABLANDO) : Math.max(suelo + MARGEN, MIN_ABRE);
      const ataque = veraHabla ? ATAQUE_HABLANDO_MS : ATAQUE_MS;
      const sobre = db >= umbral;
      recientes.push({ t: t.slice(), ms });
      recientesMs += ms;
      while (recientes.length > 1 && recientesMs - recientes[0]!.ms >= ATAQUE_HABLANDO_MS + PREVIO_MS) recientesMs -= recientes.shift()!.ms;
      if (!abierta) {
        encima = sobre ? encima + ms : Math.max(0, encima - ms);
        if (!sobre) adaptar(db, ms, !veraHabla);
        if (encima < ataque) return mudo();
        abierta = true;
        ultimaVoz = reloj;
        encima = 0;
        return cola(ataque + PREVIO_MS);
      }
      if (sobre) ultimaVoz = reloj;
      if (reloj - ultimaVoz > COLA_MS) {
        abierta = false;
        return mudo();
      }
      return [t.slice()];
    },
  };
}

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

export type EstadoLlamada = "inactiva" | "conectando" | "en_llamada" | "terminando";
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
    ganancia?: GainNode; analizadorVera?: AnalyserNode; analizadorMic?: AnalyserNode;
    sonando: Set<AudioBufferSourceNode>; tocarEn: number; listo: boolean; micMudo: boolean; puerta: ReturnType<typeof crearPuerta>; previos: ArrayBuffer[];
  }>({ sonando: new Set(), tocarEn: 0, listo: false, micMudo: false, puerta: crearPuerta(), previos: [] });
  const evento = useRef(alEvento);
  evento.current = alEvento;

  const callar = () => {
    const s = r.current;
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
    r.current = { sonando: new Set(), tocarEn: 0, listo: false, micMudo: false, puerta: crearPuerta(), previos: [] };
    setEstado("inactiva");
    setMicMudo(false);
    setEscuchado("");
    setDicho("");
  }, []);

  const tocar = (datos: ArrayBuffer) => {
    const s = r.current;
    if (!s.salida || !s.ganancia) return;
    const muestras = dePcm16(datos);
    if (!muestras.length) return;
    const b = s.salida.createBuffer(1, muestras.length, TASA_VOZ);
    b.copyToChannel(muestras, 0);
    const src = s.salida.createBufferSource();
    src.buffer = b;
    src.connect(s.ganancia);
    const ahora = s.salida.currentTime;
    const en = s.tocarEn > ahora ? s.tocarEn : ahora + 0.15;
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
      s.salida = new AudioContext();
      s.ganancia = s.salida.createGain();
      s.ganancia.gain.value = vozMuda ? 0 : 1;
      s.analizadorVera = s.salida.createAnalyser();
      s.analizadorVera.fftSize = 512;
      s.ganancia.connect(s.analizadorVera).connect(s.salida.destination);
      s.mic = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      const fuente = s.entrada.createMediaStreamSource(s.mic);
      s.analizadorMic = s.entrada.createAnalyser();
      s.analizadorMic.fftSize = 512;
      fuente.connect(s.analizadorMic);
      const tasa = s.entrada.sampleRate;
      const alTramo = (tramo: Float32Array) => {
        const ws = s.ws;
        if (s.micMudo) return;
        const tramos = s.puerta.procesar(tramo, tasa, s.sonando.size > 0).map((t) => aPcm16(bajar(t, tasa)));
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
          await s.entrada.audioWorklet.addModule(`/microfono.js?v=2`);
          conWorklet = true;
        } catch {
          await new Promise((r) => setTimeout(r, 400 * (intento + 1)));
        }
      }
      if (conWorklet) {
        s.nodo = new AudioWorkletNode(s.entrada, "microfono");
        s.nodo.port.onmessage = (ev: MessageEvent<Float32Array>) => alTramo(ev.data);
        fuente.connect(s.nodo);
      } else {
        const proc = s.entrada.createScriptProcessor(2048, 1, 1);
        proc.onaudioprocess = (ev) => alTramo(new Float32Array(ev.inputBuffer.getChannelData(0)));
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
        setEscuchado("");
        setDicho("");
      } else if (e.tipo === "error") setError(String(e.texto ?? "La llamada falló."));
      else if (e.tipo === "terminando") setEstado("terminando");
      evento.current(e);
    };
    ws.onclose = (ev) => {
      if (ev.code === 4429) setError("Hay demasiadas llamadas abiertas; cierra alguna e intenta otra vez.");
      else if (ev.code === 4404) setError("Esa conversación ya no existe.");
      limpiar();
      evento.current({ tipo: "colgada" });
    };
  }, [limpiar, vozMuda]);

  const colgar = useCallback(() => {
    const ws = r.current.ws;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ tipo: "fin" }));
    setEstado("terminando");
    setTimeout(() => r.current.ws && limpiar(), 2500);
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
