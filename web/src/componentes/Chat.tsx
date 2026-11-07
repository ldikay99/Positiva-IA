import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { guardarLocal, leerLocal, pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { useVoz, VOCES, type EventoVoz } from "../voz.ts";
import { Markdown } from "./Markdown.tsx";
import { Selector } from "./Selector.tsx";

type Adjunto = { id: string; nombre: string; tipo: string; tamano: number };
type Paso = { nombre: string; argumentos?: unknown; resultado?: string };
type Propuesta = { accion: string; id: string; estado?: "hecha" | "descartada" };
type Mensaje = { rol: "usuario" | "asistente"; texto: string; t: number; adjuntos?: Adjunto[]; pasos?: Paso[]; cambiados?: string[]; error?: boolean; voz?: boolean; propuestas?: Propuesta[] };
type Conversacion = { id: string; titulo: string; flujo: string; creada: number; actualizada: number; mensajes: Mensaje[] };
type Resumen = Omit<Conversacion, "mensajes"> & { n: number; ultimo: string };
type Subiendo = { clave: string; nombre: string; tipo: string; listo?: Adjunto; error?: string };

const NOMBRE = "Vera";

const MODELOS = [
  { id: "gemini-3-flash-preview", nombre: "Equilibrada", detalle: "Buena para casi todo" },
  { id: "gemini-3.1-flash-lite", nombre: "Rápida", detalle: "Respuestas cortas, al instante" },
  { id: "gemini-3.1-pro-preview", nombre: "Profunda", detalle: "Análisis y flujos complejos" },
];

const NOMBRE_HERRAMIENTA: Record<string, string> = {
  listar_flujos: "Revisa los flujos",
  leer_flujo: "Lee el flujo",
  validar_flujo: "Valida el flujo",
  guardar_flujo: "Guarda el flujo",
  editar_nodo: "Edita un nodo",
  agregar_nodo: "Agrega un nodo",
  eliminar_nodo: "Elimina un nodo",
  editar_aristas: "Cambia las conexiones",
  proponer_borrado: "Propone borrar un flujo",
  borrar_flujo: "Propone borrar un flujo",
  enfocar_flujo: "Abre el flujo en pantalla",
  navegar: "Te lleva a otra vista",
  abrir_corrida: "Abre una corrida",
  centrar_nodo: "Te muestra un nodo",
  renombrar_chat: "Renombra la conversación",
  listar_chats: "Revisa las conversaciones",
  buscar_correos: "Busca correos para probar",
  probar_flujo: "Prueba con un correo real",
  ultimas_corridas: "Revisa las últimas corridas",
  ver_corrida: "Lee una corrida",
  ajustar_parametro_pqrd: "Ajusta un parámetro de PQRD",
  escribir_en_chat: "Escribe el detalle en el chat",
  terminar_llamada: "Cuelga la llamada",
};

const SUGERENCIAS = [
  "Crea un flujo para derechos de petición: que detecte si lo es, saque peticionario, cédula y lo que pide, redacte un acuse y lo mande a radicar o a manual.",
  "¿Por qué se fueron a manual las últimas corridas de este flujo?",
  "Te adjunto un ejemplo del documento que llega: arma un flujo que saque sus datos.",
];

const CLAVE = "positiva-ia:chat:v2";
const MAX_MB = 25;

function peso(b: number): string {
  return b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`;
}

function iconoDe(tipo: string, nombre: string): "ojo" | "documento" | "adjunto" {
  if (tipo.startsWith("image/")) return "ojo";
  if (tipo === "application/pdf" || /\.(docx?|xlsx?|pptx?|txt|csv|md|json|xml|html?|eml|msg|zip|rtf|odt|ods)$/i.test(nombre)) return "documento";
  return "adjunto";
}

/** La cápsula de un adjunto: miniatura si es imagen; abre el visor del chat. */
function Capsula({ chat, a, alAbrir, alQuitar, cargando, error }: { chat: string; a: { id?: string; nombre: string; tipo: string; tamano?: number }; alAbrir?: () => void; alQuitar?: () => void; cargando?: boolean; error?: string }) {
  const esImagen = a.tipo.startsWith("image/") && a.id;
  return (
    <span className={`capsula ${error ? "con-error" : ""}`} title={error || a.nombre}>
      <button type="button" className="capsula-cuerpo" onClick={alAbrir} disabled={!alAbrir}>
        {esImagen ? <img src={`/api/chats/${chat}/adjuntos/${a.id}`} alt="" /> : <span className="capsula-icono">{cargando ? <span className="girando" /> : <Icono n={iconoDe(a.tipo, a.nombre)} tam={14} />}</span>}
        <span className="capsula-texto">
          <b>{a.nombre}</b>
          <small>{error ? error : cargando ? "subiendo…" : a.tamano ? peso(a.tamano) : a.tipo}</small>
        </span>
      </button>
      {alQuitar && <button type="button" className="capsula-quitar" aria-label={`Quitar ${a.nombre}`} onClick={alQuitar}><Icono n="cerrar" tam={11} /></button>}
    </span>
  );
}

/** El visor de adjuntos, dentro del chat: nada se abre en otra ventana. */
function Visor({ chat, a, cerrar }: { chat: string; a: Adjunto; cerrar: () => void }) {
  const url = `/api/chats/${chat}/adjuntos/${a.id}`;
  const [texto, setTexto] = useState<string | null>(null);
  const comoTexto = !a.tipo.startsWith("image/") && !a.tipo.startsWith("video/") && !a.tipo.startsWith("audio/") && a.tipo !== "application/pdf";
  useEffect(() => {
    if (!comoTexto) return;
    void pedir<{ texto: string }>(`${url}/texto`).then((r) => setTexto(r.texto)).catch((e: Error) => setTexto(`No se pudo leer: ${e.message}`));
  }, [url, comoTexto]);
  return (
    <motion.div className="visor" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={cerrar}>
      <motion.div className="visor-hoja" initial={{ scale: 0.96, y: 10 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.97 }} onClick={(e) => e.stopPropagation()}>
        <header>
          <span className="capsula-icono"><Icono n={iconoDe(a.tipo, a.nombre)} tam={14} /></span>
          <b>{a.nombre}</b>
          <small>{peso(a.tamano)}</small>
          <button className="chat-icono oscuro" aria-label="Cerrar el visor" onClick={cerrar}><Icono n="cerrar" tam={15} /></button>
        </header>
        <div className="visor-cuerpo">
          {a.tipo.startsWith("image/") && <img src={url} alt={a.nombre} />}
          {a.tipo === "application/pdf" && <iframe src={url} title={a.nombre} />}
          {a.tipo.startsWith("video/") && <video src={url} controls />}
          {a.tipo.startsWith("audio/") && <audio src={url} controls />}
          {comoTexto && (texto === null ? <div className="vacio"><span className="girando" /> Leyendo el documento…</div> : <pre>{texto || "(el documento no tiene texto)"}</pre>)}
        </div>
      </motion.div>
    </motion.div>
  );
}

/**
 * El indicador de la llamada: cinco barras redondeadas que se mueven con la voz.
 * Azules cuando hablas tú, color lacre cuando habla Vera, quietas en silencio.
 */
function Indicador({ analizadores }: { analizadores: () => { vera?: AnalyserNode; mic?: AnalyserNode; micMudo: boolean } }) {
  const caja = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const quieto = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const datos = new Uint8Array(512);
    const nivel = (a?: AnalyserNode) => {
      if (!a) return 0;
      a.getByteTimeDomainData(datos);
      let s = 0;
      for (let i = 0; i < a.fftSize; i++) s += ((datos[i]! - 128) / 128) ** 2;
      return Math.sqrt(s / a.fftSize);
    };
    const suave = [0, 0, 0, 0, 0];
    const forma = [0.55, 0.85, 1, 0.85, 0.55];
    let id = 0;
    const paso = () => {
      const el = caja.current;
      if (el) {
        const { vera, mic, micMudo } = analizadores();
        const v = nivel(vera);
        const m = micMudo ? 0 : nivel(mic);
        const habla = v > 0.015 ? "vera" : m > 0.03 ? "tu" : "";
        const fuerza = Math.min(1, (habla === "vera" ? v * 9 : m * 6));
        el.dataset.habla = habla;
        el.querySelectorAll("i").forEach((barra, k) => {
          const meta = habla ? Math.max(0.18, fuerza * forma[k]! * (0.75 + Math.random() * 0.5)) : 0.18;
          suave[k] = suave[k]! + (meta - suave[k]!) * 0.35;
          (barra as HTMLElement).style.transform = `scaleY(${suave[k]!.toFixed(3)})`;
        });
      }
      id = quieto ? 0 : requestAnimationFrame(paso);
    };
    paso();
    const t = quieto ? setInterval(paso, 300) : 0;
    return () => {
      cancelAnimationFrame(id);
      if (t) clearInterval(t);
    };
  }, [analizadores]);
  return <span ref={caja} className="llamada-indicador" aria-hidden><i /><i /><i /><i /><i /></span>;
}

/** Dónde está la persona: la IA lo recibe en cada mensaje (y en la llamada, cuando cambia). */
function pantallaActual() {
  const s = usar.getState();
  const corrida = s.corridaVisible ? s.corridas[s.corridaVisible] : undefined;
  return {
    vista: s.vista,
    flujo: s.flujo,
    nombreFlujo: s.flujos.find((f) => f.id === s.flujo)?.nombre ?? s.flujo,
    foco: s.foco,
    corrida: corrida ? `${corrida.id} (correo ${corrida.caso}, ${corrida.estado})` : null,
    seleccion: s.seleccion.length ? s.seleccion.slice(0, 20) : null,
  };
}

/** Lo que Vera hace en la pantalla de la persona (igual en el chat escrito y en la llamada). */
async function aplicarEnPantalla(d: { tipo: string; [k: string]: unknown }) {
  const s = usar.getState();
  if (d.tipo === "enfocar" && typeof d.flujo === "string") {
    await s.cambiarFlujo(d.flujo);
    usar.getState().irA("flujo");
  } else if (d.tipo === "navegar" && typeof d.vista === "string") {
    if (typeof d.flujo === "string" && d.flujo && d.flujo !== s.flujo) await s.cambiarFlujo(d.flujo);
    usar.getState().irA(d.vista as Parameters<typeof s.irA>[0]);
  } else if (d.tipo === "abrir_corrida" && typeof d.id === "string") {
    if (typeof d.flujo === "string" && d.flujo && d.flujo !== s.flujo) await s.cambiarFlujo(d.flujo);
    const x = usar.getState();
    await x.verCorrida(d.id);
    x.enfocar({ tipo: "corrida", id: d.id });
    x.irA("flujo");
  } else if (d.tipo === "centrar_nodo" && typeof d.nodo === "string") {
    s.centrar(d.nodo);
    s.enfocar({ tipo: "nodo", id: d.nodo });
  } else if (d.tipo === "abrir_correo" && typeof d.caso === "string") {
    s.irA("flujo");
    window.dispatchEvent(new CustomEvent("positiva:control", { detail: { accion: "correspondencia", fuente: "descargados" } }));
    s.enfocar({ tipo: "correo", caso: d.caso });
  } else if (d.tipo === "seleccionar_correos" && Array.isArray(d.casos)) {
    s.irA("flujo");
    window.dispatchEvent(new CustomEvent("positiva:control", { detail: { accion: "correspondencia", fuente: "descargados" } }));
    s.fijarSeleccion(d.casos.filter((x): x is string => typeof x === "string"));
  } else if (d.tipo === "abrir_adjuntos" && typeof d.id === "string") {
    if (typeof d.flujo === "string" && d.flujo && d.flujo !== s.flujo) await s.cambiarFlujo(d.flujo);
    const x = usar.getState();
    await x.verCorrida(d.id);
    x.irA("adjuntos");
  } else if (d.tipo === "control" && typeof d.accion === "string") {
    // Lo que vive dentro de un panel (lienzo, correspondencia, proceso) se pide al panel.
    if (d.accion === "vista_conexiones") {
      window.dispatchEvent(new CustomEvent("positiva:control", { detail: d }));
      return;
    }
    if (typeof d.flujo === "string" && d.flujo && d.flujo !== s.flujo) await s.cambiarFlujo(d.flujo);
    if (usar.getState().vista !== "flujo") {
      usar.getState().irA("flujo");
      await new Promise((r) => setTimeout(r, 350));
    }
    window.dispatchEvent(new CustomEvent("positiva:control", { detail: d }));
  } else if (d.tipo === "corridas_creadas") {
    if (typeof d.flujo === "string" && d.flujo !== s.flujo) await s.cambiarFlujo(d.flujo);
    const ids = Array.isArray(d.corridas) ? d.corridas.filter((x): x is string => typeof x === "string") : [];
    if (ids[0]) await usar.getState().verCorrida(ids[0]);
  }
}

// En orden: si Vera cambia de flujo y luego centra un nodo, lo segundo espera a lo primero.
let colaDePantalla: Promise<void> = Promise.resolve();
function enPantalla(d: { tipo: string; [k: string]: unknown }) {
  colaDePantalla = colaDePantalla.then(() => aplicarEnPantalla(d)).catch(() => undefined);
}

export function Chat() {
  const flujo = usar((s) => s.flujo);
  const flujos = usar((s) => s.flujos);
  const vista = usar((s) => s.vista);
  const cambiarFlujo = usar((s) => s.cambiarFlujo);
  const cargarFlujos = usar((s) => s.cargarFlujos);
  const irA = usar((s) => s.irA);
  const avisar = usar((s) => s.avisar);
  const [abierto, setAbierto] = useState(false);
  const [lista, setLista] = useState<Resumen[]>([]);
  const [verLista, setVerLista] = useState(false);
  const [actual, setActual] = useState<Conversacion | null>(null);
  const [texto, setTexto] = useState("");
  const [subiendo, setSubiendo] = useState<Subiendo[]>([]);
  const [modelo, setModelo] = useState(() => leerLocal(`${CLAVE}:modelo`, MODELOS[0]!.id));
  // La IA de Vera (aparte de la de los flujos): con qué proveedor escribe y si tiene voz en vivo.
  const [iaVera, setIaVera] = useState<{ texto: { proveedor: string; modelo: string }; voz_disponible: boolean; motivo: string } | null>(null);
  const [voz, setVoz] = useState(() => leerLocal(`${CLAVE}:voz`, VOCES[0]!.id));
  const [pensando, setPensando] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [visor, setVisor] = useState<Adjunto | null>(null);
  const [renombrando, setRenombrando] = useState<string | null>(null);
  const [confirmando, setConfirmando] = useState<string | null>(null);
  const [arrastrando, setArrastrando] = useState(false);
  const [escritoEnLlamada, setEscritoEnLlamada] = useState<string[]>([]);
  // Las tareas largas que Vera corre en segundo plano durante la llamada, con su nombre.
  const [tareas, setTareas] = useState<{ n: number; nombre: string; desde: number; lista?: boolean }[]>([]);
  const caja = useRef<HTMLDivElement>(null);
  const entrada = useRef<HTMLTextAreaElement>(null);
  const archivo = useRef<HTMLInputElement>(null);
  const cancelar = useRef<AbortController | null>(null);
  const actualRef = useRef<Conversacion | null>(null);
  actualRef.current = actual;

  const cargarLista = useCallback(async () => setLista(await pedir<Resumen[]>("/api/chats")), []);
  const abrirConversacion = useCallback(async (id: string) => {
    try {
      const c = await pedir<Conversacion>(`/api/chats/${id}`);
      setActual(c);
      guardarLocal(`${CLAVE}:actual`, id);
    } catch {
      setActual(null);
    }
  }, []);
  const nueva = useCallback(async () => {
    const c = await pedir<Conversacion>("/api/chats", { method: "POST", body: JSON.stringify({ flujo }) });
    setActual(c);
    guardarLocal(`${CLAVE}:actual`, c.id);
    setSubiendo([]);
    setVerLista(false);
    void cargarLista();
    setTimeout(() => entrada.current?.focus(), 100);
    return c;
  }, [flujo, cargarLista]);

  // ------------------------------------------------------------ la llamada
  const alEventoDeVoz = useCallback((e: EventoVoz) => {
    if (e.tipo === "paso") setPensando(`${NOMBRE_HERRAMIENTA[String(e.nombre)] ?? String(e.nombre)}…`);
    else if (e.tipo === "resultado_paso" || e.tipo === "turno") setPensando("");
    else if (e.tipo === "chat") {
      setEscritoEnLlamada((x) => [...x, String(e.texto ?? "")]);
      if (Array.isArray(e.cambiados) && e.cambiados.length) void cargarFlujos();
    }
    else if (e.tipo === "guardado" || e.tipo === "renombrado") {
      const c = actualRef.current;
      setEscritoEnLlamada([]);
      if (c) void abrirConversacion(c.id);
      void cargarLista();
      if (Array.isArray(e.cambiados) && e.cambiados.length) void cargarFlujos();
    } else if (e.tipo === "tarea_iniciada") setTareas((t) => [...t, { n: Number(e.tarea), nombre: String(e.nombre), desde: Date.now() }]);
    else if (e.tipo === "tarea_lista") {
      const n = Number(e.tarea);
      // Se marca lista un momento (para que se vea que terminó) y luego se quita.
      setTareas((t) => t.map((x) => (x.n === n ? { ...x, lista: true } : x)));
      setTimeout(() => setTareas((t) => t.filter((x) => x.n !== n)), 2500);
    }
    else if (e.tipo === "colgada") {
      setTareas([]);
      setPensando("");
      setEscritoEnLlamada([]);
    } else enPantalla(e);
  }, [abrirConversacion, cargarLista, cargarFlujos]);
  const llamada = useVoz(alEventoDeVoz);
  const enLlamada = llamada.estado !== "inactiva";

  const llamar = async () => {
    if (enLlamada) {
      llamada.colgar();
      return;
    }
    setVerLista(false);
    const c = actual ?? (await nueva());
    void llamada.llamar(c.id, voz, pantallaActual());
  };

  // Si en la llamada la persona cambia de vista o de flujo, Vera se entera.
  useEffect(() => {
    if (llamada.estado === "en_llamada") llamada.avisarPantalla(pantallaActual());
  }, [vista, flujo, llamada.estado, llamada.avisarPantalla]);

  useEffect(() => {
    if (!abierto) return;
    void pedir<{ texto: { proveedor: string; modelo: string }; voz_disponible: boolean; motivo: string }>("/api/ia/vera").then(setIaVera).catch(() => setIaVera(null));
    void cargarLista();
    const id = leerLocal<string>(`${CLAVE}:actual`, "");
    if (id && !actual) void abrirConversacion(id);
    setTimeout(() => entrada.current?.focus(), 200);
  }, [abierto, cargarLista, abrirConversacion, actual]);

  useEffect(() => {
    caja.current?.scrollTo({ top: caja.current.scrollHeight, behavior: "smooth" });
  }, [actual?.mensajes.length, pensando, abierto, actual, llamada.escuchado, llamada.dicho, escritoEnLlamada.length]);

  const enviarRef = useRef<(t: string) => void>(() => undefined);
  useEffect(() => {
    const pedido = (e: Event) => {
      setAbierto(true);
      const t = (e as CustomEvent<{ texto: string }>).detail?.texto;
      if (t) setTimeout(() => enviarRef.current(t), 350);
    };
    const atajo = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setAbierto((v) => !v);
      }
    };
    window.addEventListener("positiva:asistente", pedido);
    window.addEventListener("keydown", atajo);
    return () => {
      window.removeEventListener("positiva:asistente", pedido);
      window.removeEventListener("keydown", atajo);
    };
  }, []);

  // ------------------------------------------------------------ adjuntos
  const subir = async (archivos: File[]) => {
    let c = actual;
    if (!c) c = await nueva();
    for (const f of archivos) {
      const clave = `${f.name}-${f.size}-${Math.random()}`;
      if (f.size > MAX_MB * 1024 * 1024) {
        setSubiendo((s) => [...s, { clave, nombre: f.name, tipo: f.type, error: `pasa de ${MAX_MB} MB` }]);
        continue;
      }
      setSubiendo((s) => [...s, { clave, nombre: f.name, tipo: f.type || "application/octet-stream" }]);
      const datos = await new Promise<string>((resolver, rechazar) => {
        const r = new FileReader();
        r.onload = () => resolver(String(r.result).split(",")[1] ?? "");
        r.onerror = () => rechazar(r.error);
        r.readAsDataURL(f);
      });
      try {
        const a = await pedir<Adjunto>(`/api/chats/${c.id}/adjuntos`, { method: "POST", body: JSON.stringify({ nombre: f.name, tipo: f.type || "application/octet-stream", datos }) });
        setSubiendo((s) => s.map((x) => (x.clave === clave ? { ...x, listo: a } : x)));
      } catch (e) {
        setSubiendo((s) => s.map((x) => (x.clave === clave ? { ...x, error: (e as Error).message } : x)));
      }
    }
  };

  // ------------------------------------------------------------ enviar
  const enviar = async (contenido: string) => {
    const t = contenido.trim();
    // En la llamada, lo escrito va a la misma sesión de voz.
    if (llamada.estado === "en_llamada" && t) {
      llamada.enviarTexto(t);
      setTexto("");
      return;
    }
    const listos = subiendo.filter((x) => x.listo).map((x) => x.listo!);
    if ((!t && !listos.length) || ocupado || subiendo.some((x) => !x.listo && !x.error)) return;
    const c = actual ?? (await nueva());
    const mio: Mensaje = { rol: "usuario", texto: t, t: Date.now(), adjuntos: listos };
    const suyo: Mensaje = { rol: "asistente", texto: "", t: Date.now(), pasos: [] };
    setActual({ ...c, mensajes: [...c.mensajes, mio, suyo] });
    setTexto("");
    setSubiendo([]);
    setOcupado(true);
    setPensando("Pensando…");
    const control = new AbortController();
    cancelar.current = control;
    const actualizar = (f: (m: Mensaje) => Mensaje) => setActual((x) => (x ? { ...x, mensajes: [...x.mensajes.slice(0, -1), f(x.mensajes[x.mensajes.length - 1]!)] } : x));
    const lento = setTimeout(() => setPensando((p) => (p === "Pensando…" ? "Sigo trabajando en esto…" : p)), 12000);
    try {
      const r = await fetch(`/api/chats/${c.id}/mensajes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texto: t, adjuntos: listos.map((a) => a.id), flujo, modelo, pantalla: pantallaActual() }),
        signal: control.signal,
      });
      if (!r.ok || !r.body) throw new Error(((await r.json().catch(() => ({}))) as { error?: string }).error ?? `el chat respondió ${r.status}`);
      const lector = r.body.getReader();
      const dec = new TextDecoder();
      let resto = "";
      for (;;) {
        const { done, value } = await lector.read();
        if (done) break;
        resto += dec.decode(value, { stream: true });
        const lineas = resto.split("\n");
        resto = lineas.pop() ?? "";
        for (const l of lineas) {
          if (!l.trim()) continue;
          const d = JSON.parse(l) as { tipo: string; texto?: string; nombre?: string; argumentos?: unknown; resultado?: string; flujos_cambiados?: string[]; propuesta?: Propuesta; [k: string]: unknown };
          if (d.tipo === "herramienta") {
            setPensando(`${NOMBRE_HERRAMIENTA[d.nombre ?? ""] ?? d.nombre}…`);
            actualizar((m) => ({ ...m, pasos: [...(m.pasos ?? []), { nombre: d.nombre ?? "", argumentos: d.argumentos }] }));
          } else if (d.tipo === "resultado_herramienta") {
            actualizar((m) => ({ ...m, pasos: (m.pasos ?? []).map((p, i, todos) => (i === todos.length - 1 ? { ...p, resultado: d.resultado } : p)) }));
          } else if (d.tipo === "progreso" || d.tipo === "pensando") {
            setPensando(d.texto ?? "…");
          } else if (d.tipo === "texto") {
            actualizar((m) => ({ ...m, texto: (m.texto ? `${m.texto}\n\n` : "") + (d.texto ?? "") }));
          } else if (d.tipo === "error") {
            actualizar((m) => ({ ...m, texto: d.texto ?? "No pude terminar.", error: true }));
          } else if (d.tipo === "propuesta" && d.propuesta) {
            const p = d.propuesta;
            actualizar((m) => ({ ...m, propuestas: [...(m.propuestas ?? []), { accion: p.accion, id: p.id }] }));
          } else if (d.tipo === "fin") {
            actualizar((m) => ({ ...m, cambiados: d.flujos_cambiados ?? [] }));
            if (d.flujos_cambiados?.length) void cargarFlujos();
          } else if (d.tipo === "renombrado") {
            void cargarLista();
          } else {
            enPantalla(d);
          }
        }
      }
    } catch (e) {
      actualizar((m) => ({ ...m, texto: (e as Error).name === "AbortError" ? m.texto || "Detenido." : `No pude terminar: ${(e as Error).message}`, error: true }));
    } finally {
      clearTimeout(lento);
      setOcupado(false);
      setPensando("");
      cancelar.current = null;
      void cargarLista();
      setTimeout(() => void abrirConversacion(c.id), 400);
    }
  };
  enviarRef.current = (t: string) => void enviar(t);

  // ------------------------------------------------------------ propuestas
  const resolverPropuesta = async (i: number, p: Propuesta, acepta: boolean) => {
    if (!actual) return;
    try {
      if (acepta && p.accion === "borrar_flujo") {
        await pedir(`/api/flujos/${encodeURIComponent(p.id)}`, { method: "DELETE" });
        avisar(`Flujo «${flujos.find((f) => f.id === p.id)?.nombre ?? p.id}» borrado`, "info");
        void cargarFlujos();
      }
      await pedir(`/api/chats/${actual.id}/propuestas`, { method: "POST", body: JSON.stringify({ mensaje: i, accion: p.accion, objetivo: p.id, estado: acepta ? "hecha" : "descartada" }) });
    } catch (e) {
      avisar((e as Error).message, "error");
    }
    void abrirConversacion(actual.id);
  };

  // ------------------------------------------------------------ historial
  const renombrar = async (id: string, titulo: string) => {
    setRenombrando(null);
    if (!titulo.trim()) return;
    await pedir(`/api/chats/${id}`, { method: "PATCH", body: JSON.stringify({ titulo }) });
    if (actual?.id === id) setActual({ ...actual, titulo: titulo.trim() });
    void cargarLista();
  };
  const borrar = async (id: string) => {
    setConfirmando(null);
    await pedir(`/api/chats/${id}`, { method: "DELETE" });
    if (actual?.id === id) setActual(null);
    void cargarLista();
    avisar("Conversación borrada", "info");
  };

  const nombreFlujo = flujos.find((f) => f.id === flujo)?.nombre ?? flujo;
  const mensajes = actual?.mensajes ?? [];
  const puedeEnviar = llamada.estado === "en_llamada" ? !!texto.trim() : (texto.trim() || subiendo.some((x) => x.listo)) && !subiendo.some((x) => !x.listo && !x.error);
  const dondeEstas = vista === "tablero" ? "En el tablero" : `En «${nombreFlujo}»`;

  return createPortal(
    <div className="chat-capa">
      <AnimatePresence>
        {abierto && (
          <motion.section
            key="panel"
            className={`chat-panel ${enLlamada ? "en-llamada" : ""}`}
            role="dialog"
            aria-label={`${NOMBRE}, la asistente`}
            initial={{ opacity: 0, y: 24, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.96, transition: { duration: 0.16 } }}
            transition={{ type: "spring", stiffness: 420, damping: 34 }}
            onDragOver={(e) => { e.preventDefault(); setArrastrando(true); }}
            onDragLeave={(e) => { if (e.currentTarget === e.target) setArrastrando(false); }}
            onDrop={(e) => { e.preventDefault(); setArrastrando(false); void subir([...e.dataTransfer.files]); }}
          >
            <div className="chat-principal">
              <header className="chat-cabeza">
                <button className="chat-icono" aria-label={verLista ? "Volver al chat" : "Ver conversaciones"} title={verLista ? "Volver al chat" : "Conversaciones"} onClick={() => { if (!verLista) void cargarLista(); setVerLista((v) => !v); }}><Icono n={verLista ? "atras" : "menu"} tam={16} /></button>
                <div className="chat-titulo">
                  <b>{verLista ? "Conversaciones" : actual?.titulo && actual.titulo !== "Nueva conversación" ? actual.titulo : NOMBRE}</b>
                  <span>{verLista ? `${lista.length} guardada${lista.length === 1 ? "" : "s"}` : enLlamada ? (llamada.estado === "conectando" ? "Conectando la llamada…" : `En llamada con ${NOMBRE}`) : dondeEstas}</span>
                </div>
                <button
                  className={`chat-icono llamar ${enLlamada ? "activa" : ""}`}
                  aria-label={enLlamada ? "Colgar" : `Llamar a ${NOMBRE}`}
                  title={enLlamada ? "Colgar" : iaVera && !iaVera.voz_disponible ? `Sin voz: ${iaVera.motivo}` : `Hablar con ${NOMBRE}`}
                  onClick={() => void llamar()}
                  disabled={llamada.estado === "terminando" || (!enLlamada && !!iaVera && !iaVera.voz_disponible)}
                >
                  <Icono n={enLlamada ? "colgar" : "telefono"} tam={16} />
                </button>
                <button className="chat-icono" aria-label="Nueva conversación" title="Nueva conversación" onClick={() => void nueva()} disabled={enLlamada}><Icono n="mas" tam={16} /></button>
                <button className="chat-icono" aria-label="Cerrar el panel" onClick={() => setAbierto(false)}><Icono n="cerrar" tam={15} /></button>
              </header>

              {verLista ? (
                <div className="chat-conversaciones">
                  {lista.map((c) => (
                    <div key={c.id} className={`chat-conv ${actual?.id === c.id ? "activa" : ""}`}>
                      {renombrando === c.id ? (
                        <input autoFocus defaultValue={c.titulo} onBlur={(e) => void renombrar(c.id, e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void renombrar(c.id, e.currentTarget.value); if (e.key === "Escape") setRenombrando(null); }} aria-label="Nuevo título" />
                      ) : confirmando === c.id ? (
                        <div className="chat-conv-confirma">
                          <span>¿Borrar «{c.titulo}»?</span>
                          <button className="boton chico peligro" onClick={() => void borrar(c.id)}>Sí, borrar</button>
                          <button className="boton chico fantasma" autoFocus onClick={() => setConfirmando(null)}>No</button>
                        </div>
                      ) : (
                        <button className="chat-conv-abrir" disabled={enLlamada && actual?.id !== c.id} onClick={() => { void abrirConversacion(c.id); setVerLista(false); }}>
                          <b>{c.titulo}</b>
                          <small>{flujos.find((f) => f.id === c.flujo)?.nombre ?? c.flujo} · {new Date(c.actualizada).toLocaleDateString("es-CO", { day: "numeric", month: "short" })} · {c.n} mensaje{c.n === 1 ? "" : "s"}</small>
                        </button>
                      )}
                      {renombrando !== c.id && confirmando !== c.id && (
                        <span className="chat-conv-acciones">
                          <button aria-label="Renombrar" title="Renombrar" onClick={() => setRenombrando(c.id)}><Icono n="editar" tam={12} /></button>
                          <button aria-label="Borrar" title="Borrar" disabled={enLlamada && actual?.id === c.id} onClick={() => setConfirmando(c.id)}><Icono n="papelera" tam={12} /></button>
                        </span>
                      )}
                    </div>
                  ))}
                  {!lista.length && <p className="chat-sin">Aún no hay conversaciones. Escríbele o llama a {NOMBRE} para empezar una.</p>}
                </div>
              ) : (
                <>
                  <div className="chat-mensajes" ref={caja}>
                    {!mensajes.length && !enLlamada && (
                      <div className="chat-vacio">
                        <span className="chat-sello grande"><Icono n="chispa" tam={20} /></span>
                        <h3>Hola, soy {NOMBRE}.</h3>
                        <p>Creo, corrijo y pruebo flujos contigo, con correos reales y sin escribir en sistemas reales. Escríbeme, adjunta un ejemplo o toca el teléfono para hablar.</p>
                        <div className="chat-sugerencias">
                          {SUGERENCIAS.map((s) => <button key={s} onClick={() => void enviar(s)}>{s}</button>)}
                        </div>
                      </div>
                    )}
                    {mensajes.map((m, i) => (
                      <div key={i} className={`chat-msj ${m.rol} ${m.error ? "error" : ""}`}>
                        {m.voz && <span className="chat-de-voz"><Icono n="mic" tam={11} />{m.rol === "usuario" ? "Dicho en la llamada" : `${NOMBRE} en la llamada`}</span>}
                        {!!m.adjuntos?.length && actual && (
                          <div className="chat-adjuntos">{m.adjuntos.map((a) => <Capsula key={a.id} chat={actual.id} a={a} alAbrir={() => setVisor(a)} />)}</div>
                        )}
                        {m.texto && <div className="chat-texto">{m.rol === "asistente" ? <Markdown texto={m.texto} /> : m.texto.split("\n").map((l, j) => <p key={j}>{l}</p>)}</div>}
                        {!!m.propuestas?.length && (
                          <div className="chat-propuestas">
                            {m.propuestas.map((p) => {
                              const nombre = flujos.find((f) => f.id === p.id)?.nombre ?? p.id;
                              if (p.estado) return <span key={p.id} className="chat-propuesta-hecha">{p.estado === "hecha" ? `Borraste «${nombre}».` : `Decidiste no borrar «${nombre}».`}</span>;
                              return (
                                <div key={p.id} className="chat-propuesta">
                                  <span>¿Borrar el flujo «{nombre}»? No se puede deshacer.</span>
                                  <div>
                                    <button className="boton chico peligro" onClick={() => void resolverPropuesta(i, p, true)}>Sí, borrar</button>
                                    <button className="boton chico fantasma" autoFocus onClick={() => void resolverPropuesta(i, p, false)}>Ahora no</button>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        )}
                        {!!m.cambiados?.length && (
                          <div className="chat-acciones">
                            {m.cambiados.filter((id) => flujos.some((f) => f.id === id)).map((id) => (
                              <button key={id} className="boton primario chico" onClick={() => { void cambiarFlujo(id).then(() => irA("flujo")); }}>
                                <Icono n="flujo" tam={13} />Abrir «{flujos.find((f) => f.id === id)?.nombre ?? id}»
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                    {enLlamada && (llamada.escuchado || llamada.dicho || escritoEnLlamada.length > 0) && (
                      <>
                        {llamada.escuchado && <div className="chat-msj usuario en-vivo"><div className="chat-texto"><p>{llamada.escuchado}</p></div></div>}
                        {(llamada.dicho || escritoEnLlamada.length > 0) && (
                          <div className="chat-msj asistente en-vivo">
                            <div className="chat-texto">
                              {llamada.dicho && <p>{llamada.dicho}</p>}
                              {escritoEnLlamada.map((t, j) => <Markdown key={j} texto={t} />)}
                            </div>
                          </div>
                        )}
                      </>
                    )}
                    {pensando && !enLlamada && <div className="chat-pensando"><span className="chat-puntos"><i /><i /><i /></span>{pensando}</div>}
                  </div>

                  {enLlamada && tareas.length > 0 && (
                    <ul className="llamada-tareas" aria-live="polite" aria-label="Tareas en segundo plano">
                      {tareas.map((t) => (
                        <li key={t.n} className={t.lista ? "lista" : ""}>
                          {t.lista ? <Icono n="check" tam={12} /> : <span className="girando" />}
                          <span>{NOMBRE_HERRAMIENTA[t.nombre] ?? t.nombre}</span>
                          <small>{t.lista ? "listo" : "en curso"}</small>
                        </li>
                      ))}
                    </ul>
                  )}
                  {enLlamada && (
                    <div className="llamada" role="group" aria-label="Llamada con Vera">
                      <Indicador analizadores={llamada.analizadores} />
                      <span className="llamada-estado" aria-live="polite">{llamada.estado === "conectando" ? "Conectando…" : llamada.estado === "reconectando" ? "Reconectando…" : llamada.estado === "terminando" ? "Colgando…" : llamada.micMudo ? "Micrófono apagado" : pensando ? pensando : "Te escucho"}</span>
                      <button className={`llamada-boton ${llamada.micMudo ? "apagado" : ""}`} aria-pressed={llamada.micMudo} aria-label={llamada.micMudo ? "Encender el micrófono" : "Apagar el micrófono"} title={llamada.micMudo ? "Encender el micrófono" : "Apagar el micrófono"} onClick={llamada.alternarMic}><Icono n={llamada.micMudo ? "micMudo" : "mic"} tam={16} /></button>
                      <button className={`llamada-boton ${llamada.vozMuda ? "apagado" : ""}`} aria-pressed={llamada.vozMuda} aria-label={llamada.vozMuda ? `Oír a ${NOMBRE}` : `Silenciar a ${NOMBRE}`} title={llamada.vozMuda ? `Oír a ${NOMBRE}` : `Silenciar a ${NOMBRE}`} onClick={llamada.alternarVoz}><Icono n={llamada.vozMuda ? "altavozMudo" : "altavoz"} tam={16} /></button>
                      <button className="llamada-boton colgar" aria-label="Colgar" title="Colgar" onClick={llamada.colgar}><Icono n="colgar" tam={16} /></button>
                    </div>
                  )}
                  {llamada.error && (
                    <div className="chat-aviso" role="alert">
                      <span>{llamada.error}</span>
                      <button className="chat-icono oscuro" aria-label="Cerrar el aviso" onClick={() => llamada.setError("")}><Icono n="cerrar" tam={13} /></button>
                    </div>
                  )}

                  <form className="chat-entrada" onSubmit={(e) => { e.preventDefault(); void enviar(texto); }}>
                    {!!subiendo.length && (
                      <div className="chat-por-enviar">
                        {subiendo.map((x) => (
                          <Capsula key={x.clave} chat={actual?.id ?? ""} a={x.listo ?? { nombre: x.nombre, tipo: x.tipo }} cargando={!x.listo && !x.error} error={x.error} alAbrir={x.listo ? () => setVisor(x.listo!) : undefined} alQuitar={() => setSubiendo((s) => s.filter((y) => y.clave !== x.clave))} />
                        ))}
                      </div>
                    )}
                    <div className="chat-fila">
                      <button type="button" className="chat-adjuntar" aria-label="Adjuntar fotos o documentos" title={enLlamada ? "Los adjuntos se mandan por escrito, fuera de la llamada" : "Adjuntar fotos o documentos"} disabled={enLlamada} onClick={() => archivo.current?.click()}><Icono n="adjunto" tam={17} /></button>
                      <input ref={archivo} type="file" multiple hidden onChange={(e) => { void subir([...(e.target.files ?? [])]); e.target.value = ""; }} />
                      <textarea
                        ref={entrada}
                        value={texto}
                        rows={Math.min(6, Math.max(1, texto.split("\n").length))}
                        placeholder={enLlamada ? `Escríbele a ${NOMBRE} sin colgar…` : "Escribe lo que necesitas…"}
                        onChange={(e) => setTexto(e.target.value)}
                        onPaste={(e) => {
                          const archivos = [...e.clipboardData.files];
                          if (archivos.length && !enLlamada) {
                            e.preventDefault();
                            void subir(archivos);
                          }
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && !e.shiftKey) {
                            e.preventDefault();
                            void enviar(texto);
                          }
                        }}
                      />
                      {ocupado ? (
                        <button type="button" className="chat-enviar detener" aria-label="Detener" onClick={() => cancelar.current?.abort()}><Icono n="stop" tam={15} /></button>
                      ) : (
                        <button type="submit" className="chat-enviar" aria-label="Enviar" disabled={!puedeEnviar}><Icono n="enviar" tam={15} /></button>
                      )}
                    </div>
                    <div className="chat-pie">
                      <span>Respuesta</span>
                      {!iaVera || iaVera.texto.proveedor === "vertex" ? (
                        <Selector
                          tamano="chico"
                          tono="plano"
                          etiqueta="Cómo responde por escrito"
                          valor={modelo}
                          ancho={220}
                          opciones={MODELOS.map((m) => ({ valor: m.id, etiqueta: m.nombre, detalle: m.detalle }))}
                          alCambiar={(v) => { setModelo(v); guardarLocal(`${CLAVE}:modelo`, v); }}
                        />
                      ) : (
                        <span className="chat-ia-fija" title="Se cambia en Ajustes › IA de Vera">{iaVera.texto.proveedor} · {iaVera.texto.modelo}</span>
                      )}
                      <span>Voz</span>
                      <Selector
                        tamano="chico"
                        tono="plano"
                        etiqueta={`Voz de ${NOMBRE} en la llamada`}
                        titulo={enLlamada ? "Cuelga para cambiar la voz" : undefined}
                        deshabilitado={enLlamada}
                        valor={voz}
                        ancho={220}
                        opciones={VOCES.map((v) => ({ valor: v.id, etiqueta: v.nombre, texto: v.nombre, detalle: v.tono }))}
                        alCambiar={(v) => { setVoz(v); guardarLocal(`${CLAVE}:voz`, v); }}
                      />
                    </div>
                  </form>
                </>
              )}
            </div>

            <AnimatePresence>{visor && actual && <Visor key="visor" chat={actual.id} a={visor} cerrar={() => setVisor(null)} />}</AnimatePresence>
            {arrastrando && !enLlamada && <div className="chat-soltar"><Icono n="adjunto" tam={22} />Suelta para adjuntar</div>}
          </motion.section>
        )}
      </AnimatePresence>

      <motion.button
        className={`chat-burbuja ${abierto ? "abierta" : ""} ${ocupado ? "ocupada" : ""} ${enLlamada ? "en-llamada" : ""}`}
        aria-label={abierto ? `Cerrar el panel de ${NOMBRE}` : `Abrir a ${NOMBRE}, la asistente`}
        title={`${NOMBRE} (Ctrl J)`}
        onClick={() => setAbierto((v) => !v)}
        whileHover={{ scale: 1.06 }}
        whileTap={{ scale: 0.94 }}
      >
        <AnimatePresence mode="wait" initial={false}>
          <motion.span key={abierto ? "x" : enLlamada ? "l" : "c"} initial={{ rotate: -60, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: 60, opacity: 0 }} transition={{ duration: 0.16 }}>
            <Icono n={abierto ? "cerrar" : enLlamada ? "mic" : "chispa"} tam={22} />
          </motion.span>
        </AnimatePresence>
      </motion.button>
    </div>,
    document.body,
  );
}
