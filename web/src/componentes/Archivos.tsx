import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { Selector } from "./Selector.tsx";
import type { ArchivoFlujo } from "../tipos.ts";
import "./archivos.css";

/**
 * Archivos de un flujo: la zona para soltarlos («Correr», panel de un nodo, bandeja de
 * prueba), sus cápsulas y el visor en su lugar. Los archivos se guardan en el espacio
 * del flujo (nunca se escribe una ruta a mano) y el motor los lee como adjuntos.
 * Video y audio se rechazan aquí (extensión y tipo) y otra vez en el servidor (contenido).
 */

export const EXT_VIDEO_AUDIO = new Set(["mp4", "m4v", "mov", "avi", "mkv", "webm", "wmv", "flv", "mpg", "mpeg", "3gp", "3g2", "ogv", "ts", "mts", "m2ts", "vob",
  "mp3", "wav", "ogg", "oga", "opus", "m4a", "aac", "flac", "wma", "aiff", "aif", "amr", "mid", "midi", "weba", "caf"]);

const ext = (n: string) => (n.includes(".") ? n.toLowerCase().split(".").pop()! : "");

export function peso(b: number): string {
  return b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(b / 1024))} KB`;
}

function iconoDe(tipo: string, nombre: string): "ojo" | "documento" | "correo" | "adjunto" {
  if (tipo.startsWith("image/")) return "ojo";
  if (/\.(eml|msg)$/i.test(nombre)) return "correo";
  if (tipo === "application/pdf" || /\.(docx?|xlsx?|pptx?|txt|csv|md|json|xml|html?|zip|rtf|odt|ods|pdf)$/i.test(nombre)) return "documento";
  return "adjunto";
}

/** Por qué no se sube (antes de mandarlo), o null. */
export function rechazoLocal(f: File, tipos?: string[], maxMb = 25): string | null {
  const e = ext(f.name);
  if (EXT_VIDEO_AUDIO.has(e) || /^(video|audio)\//.test(f.type)) return `«${f.name}» es video o audio: esos archivos no se aceptan`;
  if (tipos?.length && !tipos.includes(e)) return `«${f.name}»: este flujo no acepta archivos ${e ? `.${e}` : "sin extensión"}`;
  if (f.size > maxMb * 1024 * 1024) return `«${f.name}» pasa de ${maxMb} MB`;
  if (!f.size) return `«${f.name}» está vacío`;
  return null;
}

/** El visor de un archivo del flujo, encima de todo: imagen, PDF o el texto que saca el motor. */
export function VisorArchivo({ flujo, a, cerrar }: { flujo: string; a: ArchivoFlujo; cerrar: () => void }) {
  const url = `/api/flujos/${encodeURIComponent(flujo)}/archivos/${a.id}`;
  // un HTML generado (un correo, un informe) se ve como página, aislado: sin scripts ni acceso a la plataforma
  const html = a.tipo.startsWith("text/html") || /\.html?$/i.test(a.nombre);
  const comoTexto = !a.tipo.startsWith("image/") && a.tipo !== "application/pdf" && !html;
  const [texto, setTexto] = useState<string | null>(null);
  useEffect(() => {
    if (!comoTexto) return;
    void pedir<{ texto: string }>(`${url}/texto`).then((r) => setTexto(r.texto)).catch((e: Error) => setTexto(`No se pudo leer: ${e.message}`));
  }, [url, comoTexto]);
  useEffect(() => {
    const t = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); cerrar(); } };
    window.addEventListener("keydown", t, true);
    return () => window.removeEventListener("keydown", t, true);
  }, [cerrar]);
  return createPortal(
    <motion.div className="arch-visor" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={cerrar} role="dialog" aria-label={`Vista de ${a.nombre}`}>
      <motion.div className="arch-visor-hoja" initial={{ scale: 0.97, y: 8 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.98 }} onClick={(e) => e.stopPropagation()}>
        <header>
          <span className="capsula-icono"><Icono n={iconoDe(a.tipo, a.nombre)} tam={14} /></span>
          <b>{a.nombre}</b>
          <small>{peso(a.tamano)}</small>
          <button className="boton-mini" aria-label="Cerrar la vista" title="Cerrar (Esc)" onClick={cerrar}><Icono n="cerrar" tam={15} /></button>
        </header>
        <div className="arch-visor-cuerpo">
          {a.tipo.startsWith("image/") && <img src={url} alt={a.nombre} />}
          {a.tipo === "application/pdf" && <iframe src={url} title={a.nombre} />}
          {html && <iframe src={url} title={a.nombre} sandbox="" className="arch-html" />}
          {comoTexto && (texto === null ? <div className="arch-leyendo"><span className="girando" />Leyendo el archivo…</div> : <pre>{texto || "(el archivo no tiene texto)"}</pre>)}
        </div>
      </motion.div>
    </motion.div>,
    document.body,
  );
}

/**
 * El archivo de un nodo (archivo_leer): se sube aquí y queda guardado en el flujo,
 * o se elige uno de los que ya tiene. El nodo guarda solo su id: nunca una ruta.
 */
export function CampoArchivoFlujo({ valor, alCambiar }: { valor: string; alCambiar: (id: string) => void }) {
  const flujo = usar((s) => s.flujo);
  const [lista, setLista] = useState<ArchivoFlujo[]>([]);
  const [visor, setVisor] = useState<ArchivoFlujo | null>(null);
  const cargar = () => void pedir<{ archivos: ArchivoFlujo[] }>(`/api/flujos/${encodeURIComponent(flujo)}/archivos`).then((r) => setLista(r.archivos)).catch(() => undefined);
  useEffect(cargar, [flujo]);
  const elegido = lista.find((a) => a.id === valor);
  return (
    <div className="arch-campo arch-ambito">
      {elegido ? (
        <div className="arch-capsulas">
          <CapsulaArchivo flujo={flujo} a={elegido} alAbrir={() => setVisor(elegido)} alQuitar={() => alCambiar("")} />
        </div>
      ) : valor ? (
        <p className="f-error">El archivo elegido ya no está en el flujo: súbelo de nuevo.</p>
      ) : null}
      {!elegido && (
        <ZonaArchivos flujo={flujo} valor={[]} multiple={false} alCambiar={(v) => { if (v[0]) { setLista((l) => [v[0]!, ...l]); alCambiar(v[0].id); } }}
          texto="Suelta aquí el archivo que leerá este paso" ayuda="Queda guardado en el flujo. Imágenes y documentos, hasta 25 MB." />
      )}
      {lista.length > 0 && (
        <Selector
          etiqueta="Usar un archivo que ya está en el flujo"
          placeholder="O elige uno que ya subiste…"
          valor={elegido ? elegido.id : null}
          opciones={lista.map((a) => ({ valor: a.id, etiqueta: a.nombre, detalle: `${peso(a.tamano)} · subido el ${new Date(a.subido).toLocaleDateString("es-CO", { day: "numeric", month: "short" })}` }))}
          alCambiar={(id) => alCambiar(String(id))}
          tamano="chico"
        />
      )}
      <AnimatePresence>{visor && <VisorArchivo key="visor" flujo={flujo} a={visor} cerrar={() => setVisor(null)} />}</AnimatePresence>
    </div>
  );
}

type Subida = { clave: string; nombre: string; tipo: string; tamano: number; listo?: ArchivoFlujo; error?: string };

/** La cápsula de un archivo: miniatura si es imagen; se abre en el visor. */
export function CapsulaArchivo({ flujo, a, alAbrir, alQuitar, cargando, error }: { flujo: string; a: { id?: string; nombre: string; tipo: string; tamano?: number }; alAbrir?: () => void; alQuitar?: () => void; cargando?: boolean; error?: string }) {
  const imagen = a.tipo.startsWith("image/") && a.id && !error;
  return (
    <span className={`capsula ${error ? "con-error" : ""}`} title={error || a.nombre}>
      <button type="button" className="capsula-cuerpo" onClick={alAbrir} disabled={!alAbrir}>
        {imagen ? <img src={`/api/flujos/${encodeURIComponent(flujo)}/archivos/${a.id}`} alt="" /> : <span className={`capsula-icono ${error ? "arch-icono-error" : ""}`}>{cargando ? <span className="girando" /> : <Icono n={error ? "alerta" : iconoDe(a.tipo, a.nombre)} tam={14} />}</span>}
        <span className="capsula-texto">
          <b>{a.nombre}</b>
          <small>{error ? error : cargando ? "subiendo…" : a.tamano ? peso(a.tamano) : a.tipo}</small>
        </span>
      </button>
      {alQuitar && <button type="button" className="capsula-quitar" aria-label={`Quitar ${a.nombre}`} onClick={alQuitar}><Icono n="cerrar" tam={11} /></button>}
    </span>
  );
}

/**
 * La zona para soltar, pegar o elegir archivos. Cada uno se sube apenas llega
 * (POST a `destino`, multipart) y queda como cápsula. `valor` son los ya subidos.
 */
export function ZonaArchivos({ flujo, valor, alCambiar, multiple = true, maximo = 20, tipos, maxMb = 25, destino, texto, ayuda, alSubirCorreos, id }: {
  flujo: string;
  valor: ArchivoFlujo[];
  alCambiar: (v: ArchivoFlujo[]) => void;
  multiple?: boolean;
  maximo?: number;
  tipos?: string[];
  maxMb?: number;
  /** Por defecto, el espacio de archivos del flujo; para correos de prueba, /api/bandeja/prueba. */
  destino?: string;
  texto?: string;
  ayuda?: string;
  /** Con destino de correos: lo que devolvió el servidor. */
  alSubirCorreos?: (r: { correos: { caso: string; asunto: string }[] }) => void;
  id?: string;
}) {
  const [encima, setEncima] = useState(false);
  const [subiendo, setSubiendo] = useState<Subida[]>([]);
  const [visor, setVisor] = useState<ArchivoFlujo | null>(null);
  const entrada = useRef<HTMLInputElement>(null);
  const caja = useRef<HTMLDivElement>(null);
  const valorRef = useRef(valor);
  valorRef.current = valor;
  const url = destino ?? `/api/flujos/${encodeURIComponent(flujo)}/archivos`;

  const subir = async (lista: File[]) => {
    if (!lista.length) return;
    const lugar = multiple ? Math.max(0, maximo - valorRef.current.length) : 1;
    const tomados = lista.slice(0, multiple ? lugar : 1);
    const sobran = lista.length - tomados.length;
    const nuevas: Subida[] = tomados.map((f) => ({ clave: `${f.name}-${f.size}-${Math.random().toString(36).slice(2, 7)}`, nombre: f.name, tipo: f.type || "", tamano: f.size, error: rechazoLocal(f, tipos, maxMb) ?? undefined }));
    if (sobran > 0) nuevas.push({ clave: `sobran-${Date.now()}`, nombre: `${sobran} archivo${sobran === 1 ? "" : "s"} más`, tipo: "", tamano: 0, error: `este flujo acepta hasta ${maximo} por corrida` });
    setSubiendo((s) => [...s, ...nuevas]);
    await Promise.all(tomados.map(async (f, i) => {
      const n = nuevas[i]!;
      if (n.error) return;
      const datos = new FormData();
      datos.append("archivo", f, f.name);
      try {
        const r = await fetch(url, { method: "POST", body: datos });
        const cuerpo = (await r.json().catch(() => ({}))) as { error?: string; archivos?: ArchivoFlujo[]; correos?: { caso: string; asunto: string }[] };
        if (!r.ok) throw new Error(cuerpo.error ?? `error ${r.status}`);
        if (cuerpo.correos) {
          alSubirCorreos?.({ correos: cuerpo.correos });
          setSubiendo((s) => s.filter((x) => x.clave !== n.clave));
          return;
        }
        const listo = cuerpo.archivos?.[0];
        if (!listo) throw new Error("el servidor no devolvió el archivo");
        setSubiendo((s) => s.filter((x) => x.clave !== n.clave));
        // varios terminan casi a la vez: se acumula aquí mismo para que ninguno pise al otro
        valorRef.current = multiple ? [...valorRef.current, listo] : [listo];
        alCambiar(valorRef.current);
      } catch (e) {
        setSubiendo((s) => s.map((x) => (x.clave === n.clave ? { ...x, error: (e as Error).message } : x)));
      }
    }));
  };

  // Pegar (Ctrl+V) dentro del diálogo o del panel donde vive la zona.
  useEffect(() => {
    const pegar = (e: ClipboardEvent) => {
      const zona = caja.current;
      if (!zona) return;
      const dentro = zona.closest(".dlg, .arch-ambito") ?? zona;
      if (!dentro.contains(document.activeElement) && document.activeElement !== document.body) return;
      const fs = [...(e.clipboardData?.files ?? [])];
      if (!fs.length) return;
      e.preventDefault();
      void subir(fs);
    };
    window.addEventListener("paste", pegar);
    return () => window.removeEventListener("paste", pegar);
  });

  const vacio = !valor.length && !subiendo.length;
  return (
    <div className="arch-zona-caja" ref={caja}>
      <div
        className={`arch-zona ${encima ? "encima" : ""} ${vacio ? "" : "con-archivos"}`}
        role="button"
        tabIndex={0}
        id={id}
        aria-label={texto ?? "Agregar archivos"}
        onClick={() => entrada.current?.click()}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); entrada.current?.click(); } }}
        onDragEnter={(e) => { e.preventDefault(); setEncima(true); }}
        onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; setEncima(true); }}
        onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setEncima(false); }}
        onDrop={(e) => { e.preventDefault(); setEncima(false); void subir([...e.dataTransfer.files]); }}
      >
        <span className="arch-zona-icono" aria-hidden><Icono n={destino ? "correo" : "adjunto"} tam={18} /></span>
        <span className="arch-zona-texto">
          <b>{encima ? "Suelta para subir" : texto ?? "Arrastra archivos aquí, pégalos o haz clic para elegirlos"}</b>
          <small>{ayuda ?? `Imágenes y documentos, hasta ${maxMb} MB cada uno. Video y audio no se aceptan.`}</small>
        </span>
        <input ref={entrada} type="file" hidden multiple={multiple} accept={tipos?.length ? tipos.map((t) => `.${t}`).join(",") : undefined}
          onChange={(e) => { void subir([...(e.target.files ?? [])]); e.target.value = ""; }} />
      </div>
      {!vacio && (
        <div className="arch-capsulas" aria-live="polite">
          {valor.map((a) => <CapsulaArchivo key={a.id} flujo={flujo} a={a} alAbrir={() => setVisor(a)} alQuitar={() => alCambiar(valor.filter((x) => x.id !== a.id))} />)}
          {subiendo.map((x) => <CapsulaArchivo key={x.clave} flujo={flujo} a={x} cargando={!x.error} error={x.error} alQuitar={() => setSubiendo((s) => s.filter((y) => y.clave !== x.clave))} />)}
        </div>
      )}
      <AnimatePresence>{visor && <VisorArchivo key="visor" flujo={flujo} a={visor} cerrar={() => setVisor(null)} />}</AnimatePresence>
    </div>
  );
}
