import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { guardarLocal, leerLocal, pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";

type Adjunto = { id: string; nombre: string; tipo: string; tamano: number };
type Paso = { nombre: string; argumentos?: unknown; resultado?: string };
type Mensaje = { rol: "usuario" | "asistente"; texto: string; t: number; adjuntos?: Adjunto[]; pasos?: Paso[]; cambiados?: string[]; error?: boolean };
type Conversacion = { id: string; titulo: string; flujo: string; creada: number; actualizada: number; mensajes: Mensaje[] };
type Resumen = Omit<Conversacion, "mensajes"> & { n: number; ultimo: string };
type Subiendo = { clave: string; nombre: string; tipo: string; listo?: Adjunto; error?: string };

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
  editar_nodo: "Edita un nodo",
  agregar_nodo: "Agrega un nodo",
  eliminar_nodo: "Elimina un nodo",
  editar_aristas: "Cambia las conexiones",
  borrar_flujo: "Borra el flujo",
  enfocar_flujo: "Abre el flujo en pantalla",
  buscar_correos: "Busca correos para probar",
  probar_flujo: "Prueba con un correo real",
  ultimas_corridas: "Revisa las últimas corridas",
  ver_corrida: "Abre una corrida",
  ajustar_parametro_pqrd: "Ajusta un parámetro de PQRD",
};

const SUGERENCIAS = [
  "Crea un flujo para derechos de petición: que detecte si lo es, extraiga peticionario, cédula y lo que pide, redacte una respuesta de acuse y lo mande a radicar o a manual.",
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

/** Un texto con **negritas**, `código` y saltos de línea, sin interpretar HTML. */
function Texto({ texto }: { texto: string }) {
  return (
    <>
      {texto.split("\n").map((linea, i) => (
        <p key={i}>
          {linea.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((t, j) =>
            t.startsWith("**") && t.length > 4 ? <b key={j}>{t.slice(2, -2)}</b> : t.startsWith("`") && t.length > 2 ? <code key={j}>{t.slice(1, -1)}</code> : <span key={j}>{t}</span>,
          )}
        </p>
      ))}
    </>
  );
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

export function Chat() {
  const flujo = usar((s) => s.flujo);
  const flujos = usar((s) => s.flujos);
  const cambiarFlujo = usar((s) => s.cambiarFlujo);
  const irA = usar((s) => s.irA);
  const avisar = usar((s) => s.avisar);
  const [abierto, setAbierto] = useState(false);
  const [lista, setLista] = useState<Resumen[]>([]);
  const [verLista, setVerLista] = useState(false);
  const [actual, setActual] = useState<Conversacion | null>(null);
  const [texto, setTexto] = useState("");
  const [subiendo, setSubiendo] = useState<Subiendo[]>([]);
  const [modelo, setModelo] = useState(() => leerLocal(`${CLAVE}:modelo`, MODELOS[0]!.id));
  const [pensando, setPensando] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [visor, setVisor] = useState<Adjunto | null>(null);
  const [renombrando, setRenombrando] = useState<string | null>(null);
  const [arrastrando, setArrastrando] = useState(false);
  const caja = useRef<HTMLDivElement>(null);
  const entrada = useRef<HTMLTextAreaElement>(null);
  const archivo = useRef<HTMLInputElement>(null);
  const cancelar = useRef<AbortController | null>(null);

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

  useEffect(() => {
    if (!abierto) return;
    void cargarLista();
    const id = leerLocal<string>(`${CLAVE}:actual`, "");
    if (id && !actual) void abrirConversacion(id);
    setTimeout(() => entrada.current?.focus(), 200);
  }, [abierto, cargarLista, abrirConversacion, actual]);

  useEffect(() => {
    caja.current?.scrollTo({ top: caja.current.scrollHeight, behavior: "smooth" });
  }, [actual?.mensajes.length, pensando, abierto, actual]);

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
    try {
      const r = await fetch(`/api/chats/${c.id}/mensajes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texto: t, adjuntos: listos.map((a) => a.id), flujo, modelo }),
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
          const d = JSON.parse(l) as { tipo: string; texto?: string; nombre?: string; argumentos?: unknown; resultado?: string; flujos_cambiados?: string[]; flujo?: string };
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
          } else if (d.tipo === "enfocar" && d.flujo) {
            void cambiarFlujo(d.flujo).then(() => irA("flujo"));
          } else if (d.tipo === "fin") {
            actualizar((m) => ({ ...m, cambiados: d.flujos_cambiados ?? [] }));
          }
        }
      }
    } catch (e) {
      actualizar((m) => ({ ...m, texto: (e as Error).name === "AbortError" ? m.texto || "Detenido." : `No pude terminar: ${(e as Error).message}`, error: true }));
    } finally {
      setOcupado(false);
      setPensando("");
      cancelar.current = null;
      void cargarLista();
      setTimeout(() => void abrirConversacion(c.id), 400);
    }
  };
  enviarRef.current = (t: string) => void enviar(t);

  const renombrar = async (id: string, titulo: string) => {
    setRenombrando(null);
    if (!titulo.trim()) return;
    await pedir(`/api/chats/${id}`, { method: "PATCH", body: JSON.stringify({ titulo }) });
    if (actual?.id === id) setActual({ ...actual, titulo: titulo.trim() });
    void cargarLista();
  };
  const borrar = async (id: string) => {
    if (!window.confirm("¿Borrar esta conversación y sus adjuntos?")) return;
    await pedir(`/api/chats/${id}`, { method: "DELETE" });
    if (actual?.id === id) setActual(null);
    void cargarLista();
    avisar("Conversación borrada", "info");
  };

  const nombreFlujo = flujos.find((f) => f.id === flujo)?.nombre ?? flujo;
  const mensajes = actual?.mensajes ?? [];
  const puedeEnviar = (texto.trim() || subiendo.some((x) => x.listo)) && !subiendo.some((x) => !x.listo && !x.error);

  return createPortal(
    <div className="chat-capa">
      <AnimatePresence>
        {abierto && (
          <motion.section
            key="panel"
            className="chat-panel"
            role="dialog"
            aria-label="Asistente de flujos"
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
                  <b>{verLista ? "Conversaciones" : actual?.titulo ?? "Asistente de flujos"}</b>
                  <span>{verLista ? `${lista.length} guardada${lista.length === 1 ? "" : "s"}` : `Sobre «${nombreFlujo}»`}</span>
                </div>
                <button className="chat-icono" aria-label="Nueva conversación" title="Nueva conversación" onClick={() => void nueva()}><Icono n="mas" tam={16} /></button>
                <button className="chat-icono" aria-label="Cerrar el asistente" onClick={() => setAbierto(false)}><Icono n="cerrar" tam={15} /></button>
              </header>

              {verLista ? (
                  <div className="chat-conversaciones">
                    {lista.map((c) => (
                      <div key={c.id} className={`chat-conv ${actual?.id === c.id ? "activa" : ""}`}>
                        {renombrando === c.id ? (
                          <input autoFocus defaultValue={c.titulo} onBlur={(e) => void renombrar(c.id, e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void renombrar(c.id, e.currentTarget.value); if (e.key === "Escape") setRenombrando(null); }} aria-label="Nuevo título" />
                        ) : (
                          <button className="chat-conv-abrir" onClick={() => { void abrirConversacion(c.id); setVerLista(false); }}>
                            <b>{c.titulo}</b>
                            <small>{flujos.find((f) => f.id === c.flujo)?.nombre ?? c.flujo} · {new Date(c.actualizada).toLocaleDateString("es-CO", { day: "numeric", month: "short" })}</small>
                          </button>
                        )}
                        <span className="chat-conv-acciones">
                          <button aria-label="Renombrar" title="Renombrar" onClick={() => setRenombrando(c.id)}><Icono n="editar" tam={12} /></button>
                          <button aria-label="Borrar" title="Borrar" onClick={() => void borrar(c.id)}><Icono n="papelera" tam={12} /></button>
                        </span>
                      </div>
                    ))}
                    {!lista.length && <p className="chat-sin">Aún no hay conversaciones.</p>}
                  </div>
              ) : (
              <>
              <div className="chat-mensajes" ref={caja}>
                {!mensajes.length && (
                  <div className="chat-vacio">
                    <span className="chat-sello grande"><Icono n="chispa" tam={22} /></span>
                    <h3>Crea, corrige y mejora flujos conversando.</h3>
                    <p>Arma el flujo, lo valida, lo prueba con correos reales y te dice qué salió. Puedes adjuntar ejemplos (fotos, PDF, Word, Excel). Nada se escribe en sistemas reales.</p>
                    <div className="chat-sugerencias">
                      {SUGERENCIAS.map((s) => <button key={s} onClick={() => void enviar(s)}>{s}</button>)}
                    </div>
                  </div>
                )}
                {mensajes.map((m, i) => (
                  <div key={i} className={`chat-msj ${m.rol} ${m.error ? "error" : ""}`}>
                    {!!m.adjuntos?.length && actual && (
                      <div className="chat-adjuntos">{m.adjuntos.map((a) => <Capsula key={a.id} chat={actual.id} a={a} alAbrir={() => setVisor(a)} />)}</div>
                    )}
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
                          <button key={id} className="boton primario chico" onClick={() => { void cambiarFlujo(id).then(() => irA("flujo")); }}>
                            <Icono n="flujo" tam={13} />Abrir «{flujos.find((f) => f.id === id)?.nombre ?? id}»
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                {pensando && <div className="chat-pensando"><span className="chat-puntos"><i /><i /><i /></span>{pensando}</div>}
              </div>

              <form className="chat-entrada" onSubmit={(e) => { e.preventDefault(); void enviar(texto); }}>
                {!!subiendo.length && (
                  <div className="chat-por-enviar">
                    {subiendo.map((x) => (
                      <Capsula key={x.clave} chat={actual?.id ?? ""} a={x.listo ?? { nombre: x.nombre, tipo: x.tipo }} cargando={!x.listo && !x.error} error={x.error} alAbrir={x.listo ? () => setVisor(x.listo!) : undefined} alQuitar={() => setSubiendo((s) => s.filter((y) => y.clave !== x.clave))} />
                    ))}
                  </div>
                )}
                <div className="chat-fila">
                  <button type="button" className="chat-adjuntar" aria-label="Adjuntar fotos o documentos" title="Adjuntar fotos o documentos" onClick={() => archivo.current?.click()}><Icono n="adjunto" tam={17} /></button>
                  <input ref={archivo} type="file" multiple hidden onChange={(e) => { void subir([...(e.target.files ?? [])]); e.target.value = ""; }} />
                  <textarea
                    ref={entrada}
                    value={texto}
                    rows={Math.min(6, Math.max(1, texto.split("\n").length))}
                    placeholder="Pide un flujo, un arreglo o una mejora…"
                    onChange={(e) => setTexto(e.target.value)}
                    onPaste={(e) => {
                      const archivos = [...e.clipboardData.files];
                      if (archivos.length) {
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
                <select className="chat-modelo" value={modelo} onChange={(e) => { setModelo(e.target.value); guardarLocal(`${CLAVE}:modelo`, e.target.value); }} aria-label="Modelo">
                  {MODELOS.map((m) => <option key={m.id} value={m.id}>{m.nombre}</option>)}
                </select>
              </form>
              </>
              )}
            </div>

            <AnimatePresence>{visor && actual && <Visor key="visor" chat={actual.id} a={visor} cerrar={() => setVisor(null)} />}</AnimatePresence>
            {arrastrando && <div className="chat-soltar"><Icono n="adjunto" tam={22} />Suelta para adjuntar</div>}
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
