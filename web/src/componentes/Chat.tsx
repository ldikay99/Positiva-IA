import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { guardarLocal, leerLocal, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";

type Paso = { nombre: string; argumentos?: unknown; resultado?: string };
type Mensaje = { rol: "usuario" | "asistente"; texto: string; pasos?: Paso[]; cambiados?: string[]; error?: boolean };

const MODELOS = [
  { id: "gemini-3.1-flash-lite", nombre: "Gemini 3.1 Flash" },
  { id: "gemini-3-flash-preview", nombre: "Gemini 3 Flash" },
  { id: "gemini-3.1-pro-preview", nombre: "Gemini 3.1 Pro" },
];

const NOMBRE_HERRAMIENTA: Record<string, string> = {
  listar_flujos: "Revisa los flujos",
  leer_flujo: "Lee el flujo",
  validar_flujo: "Valida el flujo",
  guardar_flujo: "Guarda el flujo",
  buscar_correos: "Busca correos para probar",
  probar_flujo: "Prueba el flujo con un correo real",
  ultimas_corridas: "Revisa las últimas corridas",
  ver_corrida: "Abre una corrida",
  ajustar_parametro_pqrd: "Ajusta un parámetro de PQRD",
};

const SUGERENCIAS = [
  "Crea un flujo para derechos de petición: que detecte si es derecho de petición, extraiga peticionario, cédula y lo que pide, y lo mande a radicar o a manual.",
  "¿Por qué se fueron a manual las últimas corridas de este flujo?",
  "Mejora este flujo para que no se le escape el documento del afectado.",
];

const CLAVE = "positiva-ia:chat:v1";

/** Un texto con **negritas**, `código` y saltos de línea, sin interpretar HTML. */
function Texto({ texto }: { texto: string }) {
  return (
    <>
      {texto.split("\n").map((linea, i) => (
        <p key={i}>
          {linea.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((t, j) =>
            t.startsWith("**") ? <b key={j}>{t.slice(2, -2)}</b> : t.startsWith("`") ? <code key={j}>{t.slice(1, -1)}</code> : <span key={j}>{t}</span>,
          )}
        </p>
      ))}
    </>
  );
}

export function Chat() {
  const flujo = usar((s) => s.flujo);
  const flujos = usar((s) => s.flujos);
  const cambiarFlujo = usar((s) => s.cambiarFlujo);
  const irA = usar((s) => s.irA);
  const [abierto, setAbierto] = useState(false);
  const [mensajes, setMensajes] = useState<Mensaje[]>(() => leerLocal<Mensaje[]>(CLAVE, []));
  const [texto, setTexto] = useState("");
  const [modelo, setModelo] = useState(() => leerLocal(`${CLAVE}:modelo`, MODELOS[0]!.id));
  const [pensando, setPensando] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const caja = useRef<HTMLDivElement>(null);
  const entrada = useRef<HTMLTextAreaElement>(null);
  const cancelar = useRef<AbortController | null>(null);

  useEffect(() => guardarLocal(CLAVE, mensajes.slice(-40)), [mensajes]);
  useEffect(() => {
    caja.current?.scrollTo({ top: caja.current.scrollHeight, behavior: "smooth" });
  }, [mensajes, pensando, abierto]);
  useEffect(() => {
    if (abierto) setTimeout(() => entrada.current?.focus(), 200);
  }, [abierto]);
  const enviarRef = useRef<(t: string) => void>(() => undefined);
  useEffect(() => {
    const pedido = (e: Event) => {
      setAbierto(true);
      const t = (e as CustomEvent<{ texto: string }>).detail?.texto;
      if (t) setTimeout(() => enviarRef.current(t), 250);
    };
    window.addEventListener("positiva:asistente", pedido);
    return () => window.removeEventListener("positiva:asistente", pedido);
  }, []);
  useEffect(() => {
    const atajo = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setAbierto((v) => !v);
      }
      if (e.key === "Escape" && abierto) setAbierto(false);
    };
    window.addEventListener("keydown", atajo);
    return () => window.removeEventListener("keydown", atajo);
  }, [abierto]);

  const enviar = async (contenido: string) => {
    const t = contenido.trim();
    if (!t || ocupado) return;
    const historial: Mensaje[] = [...mensajes, { rol: "usuario", texto: t }];
    setMensajes([...historial, { rol: "asistente", texto: "", pasos: [] }]);
    setTexto("");
    setOcupado(true);
    setPensando("Pensando…");
    const control = new AbortController();
    cancelar.current = control;
    const actualizar = (f: (m: Mensaje) => Mensaje) => setMensajes((ms) => [...ms.slice(0, -1), f(ms[ms.length - 1]!)]);
    try {
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mensajes: historial.slice(-20).map((m) => ({ rol: m.rol, texto: m.texto || "(pasos)" })), flujo, modelo }),
        signal: control.signal,
      });
      if (!r.ok || !r.body) throw new Error(`el chat respondió ${r.status}`);
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
          const d = JSON.parse(l) as { tipo: string; texto?: string; nombre?: string; argumentos?: unknown; resultado?: string; flujos_cambiados?: string[] };
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
            actualizar((m) => ({ ...m, texto: `No pude terminar: ${d.texto}`, error: true }));
          } else if (d.tipo === "fin") {
            actualizar((m) => ({ ...m, cambiados: d.flujos_cambiados ?? [] }));
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") actualizar((m) => ({ ...m, texto: `No pude terminar: ${(e as Error).message}`, error: true }));
      else actualizar((m) => ({ ...m, texto: m.texto || "Detenido.", error: true }));
    } finally {
      setOcupado(false);
      setPensando("");
      cancelar.current = null;
    }
  };

  enviarRef.current = (t: string) => void enviar(t);
  const nombreFlujo = flujos.find((f) => f.id === flujo)?.nombre ?? flujo;

  return createPortal(
    <div className="chat-capa">
      <AnimatePresence>
        {abierto && (
          <motion.section
            key="panel"
            className="chat-panel"
            role="dialog"
            aria-label="Asistente de flujos"
            initial={{ opacity: 0, y: 24, scale: 0.94, transformOrigin: "100% 100%" }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.95, transition: { duration: 0.16 } }}
            transition={{ type: "spring", stiffness: 420, damping: 32 }}
          >
            <header className="chat-cabeza">
              <span className="chat-sello"><Icono n="chispa" tam={16} /></span>
              <div className="chat-titulo">
                <b>Asistente de flujos</b>
                <span>Trabajando sobre «{nombreFlujo}»</span>
              </div>
              <select value={modelo} onChange={(e) => { setModelo(e.target.value); guardarLocal(`${CLAVE}:modelo`, e.target.value); }} aria-label="Modelo">
                {MODELOS.map((m) => <option key={m.id} value={m.id}>{m.nombre}</option>)}
              </select>
              {mensajes.length > 0 && <button className="chat-icono" title="Empezar de nuevo" aria-label="Empezar de nuevo" onClick={() => setMensajes([])}><Icono n="deshacer" tam={15} /></button>}
              <button className="chat-icono" aria-label="Cerrar el asistente" onClick={() => setAbierto(false)}><Icono n="cerrar" tam={15} /></button>
            </header>

            <div className="chat-mensajes" ref={caja}>
              {!mensajes.length && (
                <div className="chat-vacio">
                  <h3>Crea, corrige y mejora flujos conversando.</h3>
                  <p>El asistente arma el flujo, lo valida, lo prueba con correos reales de la bandeja y te dice qué salió. Nada se escribe en sistemas reales.</p>
                  <div className="chat-sugerencias">
                    {SUGERENCIAS.map((s) => <button key={s} onClick={() => void enviar(s)}>{s}</button>)}
                  </div>
                </div>
              )}
              {mensajes.map((m, i) => (
                <div key={i} className={`chat-msj ${m.rol} ${m.error ? "error" : ""}`}>
                  {!!m.pasos?.length && (
                    <details className="chat-pasos" open={i === mensajes.length - 1 && ocupado}>
                      <summary>{m.pasos.length} paso{m.pasos.length === 1 ? "" : "s"} del asistente</summary>
                      <ol>
                        {m.pasos.map((p, j) => (
                          <li key={j}>
                            <span className={`chat-paso-punto ${p.resultado ? "hecho" : "en-curso"}`} />
                            <div>
                              <b>{NOMBRE_HERRAMIENTA[p.nombre] ?? p.nombre}</b>
                              {p.resultado && <code>{p.resultado.slice(0, 260)}</code>}
                            </div>
                          </li>
                        ))}
                      </ol>
                    </details>
                  )}
                  {m.texto && <div className="chat-texto"><Texto texto={m.texto} /></div>}
                  {!!m.cambiados?.length && (
                    <div className="chat-acciones">
                      {m.cambiados.map((id) => (
                        <button key={id} className="boton primario chico" onClick={() => { void cambiarFlujo(id); irA("flujo"); }}>
                          <Icono n="flujo" tam={13} />Abrir «{flujos.find((f) => f.id === id)?.nombre ?? id}»
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {pensando && (
                <div className="chat-pensando"><span className="chat-puntos"><i /><i /><i /></span>{pensando}</div>
              )}
            </div>

            <form className="chat-entrada" onSubmit={(e) => { e.preventDefault(); void enviar(texto); }}>
              <textarea
                ref={entrada}
                value={texto}
                rows={Math.min(6, Math.max(1, texto.split("\n").length))}
                placeholder="Pide un flujo nuevo, un arreglo o una mejora…"
                onChange={(e) => setTexto(e.target.value)}
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
                <button type="submit" className="chat-enviar" aria-label="Enviar" disabled={!texto.trim()}><Icono n="enviar" tam={15} /></button>
              )}
            </form>
          </motion.section>
        )}
      </AnimatePresence>

      <motion.button
        className={`chat-burbuja ${abierto ? "abierta" : ""} ${ocupado ? "ocupada" : ""}`}
        aria-label={abierto ? "Cerrar el asistente" : "Abrir el asistente de flujos"}
        title="Asistente de flujos (Ctrl J)"
        onClick={() => setAbierto((v) => !v)}
        whileHover={{ scale: 1.06 }}
        whileTap={{ scale: 0.94 }}
      >
        <AnimatePresence mode="wait" initial={false}>
          <motion.span key={abierto ? "x" : "c"} initial={{ rotate: -60, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: 60, opacity: 0 }} transition={{ duration: 0.16 }}>
            <Icono n={abierto ? "cerrar" : "chispa"} tam={22} />
          </motion.span>
        </AnimatePresence>
      </motion.button>
    </div>,
    document.body,
  );
}
