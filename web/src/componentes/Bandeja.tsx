import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { NOMBRES_CORTOS_DESENLACE, NOMBRES_DESENLACE, TONO_DESENLACE } from "../progreso.ts";
import type { Correo, CorreoDelBuzon, Corrida } from "../tipos.ts";

/** Alto de cada sobre en la lista larga (se dibujan solo los visibles). */
const ALTO_SOBRE = 112;

function fecha(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  const hoy = new Date();
  const mismoDia = d.toDateString() === hoy.toDateString();
  return mismoDia
    ? d.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("es-CO", { day: "numeric", month: "short" });
}

function iniciales(nombre: string): string {
  const partes = nombre.replace(/[<>"@].*$/, "").split(/[\s._-]+/).filter((p) => /[a-záéíóúñ]/i.test(p));
  return ((partes[0]?.[0] ?? "?") + (partes[1]?.[0] ?? "")).toUpperCase();
}

/** Cada remitente con su propio tono, siempre el mismo: se reconoce a quien escribe varias veces. */
const TONOS = ["azul", "verde", "violeta", "turquesa", "ambar", "rosa"] as const;
function tonoDe(texto: string): string {
  let h = 0;
  for (const c of texto.toLowerCase()) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return TONOS[h % TONOS.length]!;
}

function ultimas(corridas: Record<string, Corrida>, orden: string[]): Record<string, Corrida> {
  const r: Record<string, Corrida> = {};
  for (const id of [...orden].reverse()) {
    const c = corridas[id];
    if (c) r[c.caso] = c;
  }
  return r;
}

// ----------------------------------------------------------------- el sello

/**
 * El sello de radicado sobre el sobre. Cae con un golpe solo cuando el caso
 * acaba de terminar; al volver a pasar por la lista ya está puesto.
 */
function Sello({ corrida }: { corrida: Corrida }) {
  const quieto = useReducedMotion();
  const r = corrida.resultado;
  if (!r) return null;
  const accion = String(r.accion);
  const radicado = String(r.radicado ?? "");
  const reciente = Date.now() / 1000 - Number(r.t ?? 0) < 10;
  const texto = accion === "radicado" ? "Radicado" : NOMBRES_CORTOS_DESENLACE[accion] ?? accion;
  const numero = accion === "radicado" ? (radicado.startsWith("DRY-RUN") ? "en prueba" : radicado) : String(r.motivo ?? "").replace(/_/g, " ").toLowerCase();
  return (
    <motion.span
      className={`sello sello-${TONO_DESENLACE[accion] ?? "gris"}`}
      initial={reciente && !quieto ? { scale: 2.2, rotate: -26, opacity: 0 } : false}
      animate={{ scale: 1, rotate: -9, opacity: 1 }}
      transition={{ type: "spring", stiffness: 520, damping: 17, mass: 0.9 }}
      title={NOMBRES_DESENLACE[accion] ?? accion}
    >
      <b>{texto}</b>
      {numero && <small>{numero}</small>}
    </motion.span>
  );
}

// ------------------------------------------------------------------ el sobre

type Sobre = {
  clave: string;
  remitente: string;
  asunto: string;
  previa?: string;
  recibido: string;
  fichas?: ReactNode;
  elegido: boolean;
  abierto?: boolean;
  corrida?: Corrida;
  alElegir: () => void;
  alAbrir?: () => void;
  estilo?: React.CSSProperties;
  numero?: number;
};

const SobreDeCorreo = memo(function SobreDeCorreo(p: Sobre) {
  const enCurso = p.corrida && !p.corrida.resultado ? (p.corrida.estado === "en_cola" ? "en cola" : "procesando") : "";
  return (
    <div
      className={`sobre ${p.elegido ? "elegido" : ""} ${p.abierto ? "abierto" : ""} ${enCurso ? "en-curso" : ""} ${p.corrida?.resultado ? "con-sello" : ""}`}
      style={p.estilo}
      role="option"
      aria-selected={p.elegido}
      tabIndex={0}
      onClick={p.alElegir}
      onKeyDown={(e) => {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          if (e.key === "Enter" && p.alAbrir) p.alAbrir();
          else p.alElegir();
        }
      }}
    >
      <span className={`sobre-avatar tono-${tonoDe(p.remitente)}`} aria-hidden>
        <span className="cara frente">{iniciales(p.remitente)}</span>
        <span className="cara dorso"><Icono n="ok" tam={15} strokeWidth={2.6} /></span>
      </span>
      <div className="sobre-cuerpo">
        <div className="sobre-linea">
          {p.numero !== undefined && <span className="sobre-orden" title="Orden en que lo tomaría el job">{p.numero}</span>}
          <span className="sobre-de">{p.remitente || "(sin remitente)"}</span>
          <span className="sobre-fecha">{fecha(p.recibido)}</span>
        </div>
        <div className="sobre-asunto">{p.asunto || "(sin asunto)"}</div>
        {p.previa !== undefined && <div className="sobre-previa">{p.previa}</div>}
        <div className="sobre-fichas">
          {enCurso && <span className="ficha tono-azul"><span className="girando" />{enCurso}</span>}
          {p.fichas}
        </div>
      </div>
      {p.alAbrir && (
        <button className="sobre-abrir" aria-label="Abrir el correo" title="Abrir el correo" onClick={(e) => { e.stopPropagation(); p.alAbrir?.(); }}>
          <Icono n="ojo" tam={15} />
        </button>
      )}
      {p.corrida?.resultado && <Sello corrida={p.corrida} />}
    </div>
  );
});

// ------------------------------------------------------- la bandeja de salida

type Modo = "job" | "uno";

function BandejaDeSalida({ elegidos, modo, setModo, alLimpiar, alCorrer, ocupado, extra }: {
  elegidos: { clave: string; nombre: string }[];
  modo: Modo;
  /** Sin esto el flujo no corre como el job y no se ofrece el modo. */
  setModo?: (m: Modo) => void;
  alLimpiar: () => void;
  alCorrer: () => void;
  ocupado: boolean;
  extra?: ReactNode;
}) {
  const n = elegidos.length;
  const visibles = elegidos.slice(-5);
  return (
    <AnimatePresence>
      {(n > 0 || extra) && (
        <motion.footer
          className="salida"
          initial={{ y: 70, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 70, opacity: 0 }}
          transition={{ type: "spring", stiffness: 430, damping: 34 }}
        >
          {n > 0 && (
            <div className="salida-pila" aria-label={`${n} elegidos`}>
              <AnimatePresence initial={false}>
                {visibles.map((e, i) => (
                  <motion.span
                    key={e.clave}
                    layout
                    className={`salida-ficha tono-${tonoDe(e.nombre)}`}
                    style={{ zIndex: i }}
                    initial={{ scale: 0.4, y: 18, opacity: 0 }}
                    animate={{ scale: 1, y: 0, opacity: 1 }}
                    exit={{ scale: 0.4, opacity: 0 }}
                    transition={{ type: "spring", stiffness: 600, damping: 30 }}
                    title={e.nombre}
                  >
                    {iniciales(e.nombre)}
                  </motion.span>
                ))}
              </AnimatePresence>
              <span className="salida-cuenta">
                <motion.b key={n} initial={{ y: -8, opacity: 0 }} animate={{ y: 0, opacity: 1 }}>{n}</motion.b>
                <small>{n === 1 ? "caso" : "casos"}</small>
              </span>
            </div>
          )}
          {n > 1 && setModo && (
            <div className="salida-modo" role="radiogroup" aria-label="Cómo correrlos">
              {(["job", "uno"] as Modo[]).map((m) => (
                <button key={m} role="radio" aria-checked={modo === m} className={modo === m ? "activo" : ""} onClick={() => setModo(m)}
                  title={m === "job" ? "Juntos con un solo orquestador, de a cinco y de a uno por remitente: detecta lo que se repite en la corrida" : "Cada correo con su propio orquestador, por separado"}>
                  {modo === m && <motion.span layoutId="salida-modo-fondo" className="salida-modo-fondo" transition={{ type: "spring", stiffness: 520, damping: 38 }} />}
                  <span>{m === "job" ? "Como el job" : "Uno a uno"}</span>
                </button>
              ))}
            </div>
          )}
          <div className="salida-acciones">
            {extra}
            {n > 0 && <button className="boton fantasma chico" onClick={alLimpiar}>Soltar</button>}
            {n > 0 && (
              <button className="boton primario salida-correr" disabled={ocupado} onClick={alCorrer}>
                {ocupado ? <span className="girando" /> : <Icono n="play" tam={14} />}
                Correr {n === 1 ? "este caso" : `${n} casos`}
              </button>
            )}
          </div>
        </motion.footer>
      )}
    </AnimatePresence>
  );
}

// ------------------------------------------------------------ el buzón real

/**
 * La carpeta que lee el job (AUTOMATIZACION), en el orden en que la tomaría
 * ahora: un correo por hilo, del más viejo al más nuevo. Correrlos aquí es
 * correr el job: un solo orquestador, de a cinco y de a uno por remitente, con
 * las consultas al buzón de verdad. Nada se escribe: lo que el job marcaría,
 * movería o reenviaría aparece en el flujo como bloqueado.
 */
function PanelBuzon({ porCaso }: { porCaso: Record<string, Corrida> }) {
  const buzon = usar((s) => s.buzon);
  const cargando = usar((s) => s.cargandoBuzon);
  const cargar = usar((s) => s.cargarBuzon);
  const correr = usar((s) => s.correrComoElJob);
  const avisar = usar((s) => s.avisar);
  const enfocar = usar((s) => s.enfocar);
  const [n, setN] = useState(10);
  const [elegidos, setElegidos] = useState<string[]>([]);
  const [corriendo, setCorriendo] = useState(false);

  useEffect(() => {
    void cargar(n);
  }, [n, cargar]);

  const correos = buzon?.correos ?? [];
  const porId = useMemo(() => new Map(correos.map((c) => [c.id, c])), [correos]);
  const alternar = (id: string) => setElegidos((m) => (m.includes(id) ? m.filter((x) => x !== id) : [...m, id]));

  const lanzar = async () => {
    if (!elegidos.length) return;
    setCorriendo(true);
    try {
      // En el orden de la carpeta, que es el del job.
      const ids = correos.map((c) => c.id).filter((id) => elegidos.includes(id));
      await correr(n, ids);
      avisar(`${ids.length} ${ids.length === 1 ? "caso" : "casos"} del buzón real corriendo como el job`);
      setElegidos([]);
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setCorriendo(false);
    }
  };

  return (
    <>
      <div className="buzon-controles">
        <label className="buzon-cuantos">
          <span>Próximos</span>
          <select value={n} onChange={(e) => setN(Number(e.target.value))} aria-label="Cuántos correos mirar">
            {[5, 10, 20, 50].map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
          {typeof buzon?.total === "number" && <span className="buzon-total">de {buzon.total} en la carpeta</span>}
        </label>
        <button className="boton fantasma chico" disabled={cargando} onClick={() => void cargar(n, true)} aria-label="Volver a leer el buzón">
          {cargando ? <span className="girando" /> : <Icono n="reordenar" tam={13} />}Leer de nuevo
        </button>
      </div>
      <div className="lista-sobres estatica" role="listbox" aria-multiselectable>
        {buzon?.error && <div className="vacio">No se pudo leer el buzón: {buzon.error}. Revisa la conexión y lee de nuevo.</div>}
        {!buzon && cargando && <div className="vacio"><span className="girando" /> Leyendo el buzón</div>}
        {correos.map((c: CorreoDelBuzon, i) => (
          <SobreDeCorreo
            key={c.id}
            clave={c.id}
            numero={i + 1}
            remitente={c.remitente || c.remitenteCorreo}
            asunto={c.asunto}
            recibido={c.recibido}
            elegido={elegidos.includes(c.id)}
            corrida={porCaso[c.caso]}
            alElegir={() => alternar(c.id)}
            alAbrir={c.traido ? () => enfocar({ tipo: "correo", caso: c.caso }) : undefined}
            fichas={
              <>
                {c.conAdjuntos && <span className="ficha"><Icono n="adjunto" tam={11} /></span>}
                {c.categorias.map((k) => <span key={k} className="ficha tono-ambar" title="Etiqueta que tiene hoy en el buzón">{k}</span>)}
              </>
            }
          />
        ))}
        {buzon && !buzon.error && !correos.length && <div className="vacio">La carpeta del job está vacía: no hay nada por radicar.</div>}
      </div>
      <BandejaDeSalida
        elegidos={elegidos.map((id) => ({ clave: id, nombre: porId.get(id)?.remitente || porId.get(id)?.remitenteCorreo || "?" }))}
        modo="job"
        setModo={undefined}
        alLimpiar={() => setElegidos([])}
        alCorrer={() => void lanzar()}
        ocupado={corriendo}
        extra={!elegidos.length && correos.length ? <button className="boton fantasma chico" onClick={() => setElegidos(correos.map((c) => c.id))}>Elegir los {correos.length}</button> : undefined}
      />
    </>
  );
}

// ------------------------------------------------------- los descargados

type Filtro = "todos" | "sin_procesar" | "procesados" | "del_buzon" | "radicado" | "manual" | string;

function PanelDescargados({ porCaso, conLote }: { porCaso: Record<string, Corrida>; conLote: boolean }) {
  const bandeja = usar((s) => s.bandeja);
  const seleccion = usar((s) => s.seleccion);
  const fijar = usar((s) => s.fijarSeleccion);
  const alternar = usar((s) => s.alternarSeleccion);
  const procesar = usar((s) => s.procesar);
  const foco = usar((s) => s.foco);
  const enfocar = usar((s) => s.enfocar);
  const avisar = usar((s) => s.avisar);
  const motor = usar((s) => s.motor);
  const cancelarCola = usar((s) => s.cancelarCola);
  const [busqueda, setBusqueda] = useState("");
  const [filtro, setFiltro] = useState<Filtro>("todos");
  const [modo, setModo] = useState<Modo>("job");
  const [enviando, setEnviando] = useState(false);
  const lista = useRef<HTMLDivElement>(null);
  const [scroll, setScroll] = useState(0);
  const [alto, setAlto] = useState(600);

  const motivos = useMemo(() => [...new Set(bandeja.map((c) => c.motivoProduccion).filter(Boolean))].sort(), [bandeja]);
  const elegidos = useMemo(() => new Set(seleccion), [seleccion]);
  const porClave = useMemo(() => new Map(bandeja.map((c) => [c.caso, c])), [bandeja]);

  const visibles = useMemo(() => {
    const q = busqueda.trim().toLowerCase();
    return bandeja.filter((c) => {
      if (filtro === "sin_procesar" && porCaso[c.caso]) return false;
      if (filtro === "procesados" && !porCaso[c.caso]) return false;
      if (filtro === "del_buzon" && c.origen !== "buzon") return false;
      if ((filtro === "radicado" || filtro === "manual") && c.desenlaceProduccion !== filtro) return false;
      if (!["todos", "sin_procesar", "procesados", "radicado", "manual", "del_buzon"].includes(filtro) && c.motivoProduccion !== filtro) return false;
      return !q || `${c.caso} ${c.asunto} ${c.remitente} ${c.remitenteCorreo} ${c.vistaPrevia}`.toLowerCase().includes(q);
    });
  }, [bandeja, busqueda, filtro, porCaso]);

  useLayoutEffect(() => {
    const el = lista.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setAlto(el.clientHeight));
    ro.observe(el);
    setAlto(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    lista.current?.scrollTo({ top: 0 });
    setScroll(0);
  }, [busqueda, filtro]);

  const desde = Math.max(0, Math.floor(scroll / ALTO_SOBRE) - 4);
  const hasta = Math.min(visibles.length, Math.ceil((scroll + alto) / ALTO_SOBRE) + 4);
  const procesados = bandeja.filter((c) => porCaso[c.caso]).length;
  const delBuzon = bandeja.filter((c) => c.origen === "buzon").length;
  const enCola = motor?.enCola.length ?? 0;

  const lanzar = async () => {
    if (!seleccion.length) return;
    setEnviando(true);
    try {
      const n = seleccion.length;
      await procesar(seleccion, conLote && modo === "job");
      avisar(`${n} ${n === 1 ? "caso" : "casos"} ${modo === "job" && n > 1 ? "corriendo como el job" : "en cola"}`);
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setEnviando(false);
    }
  };

  const filtros: { id: Filtro; nombre: string; n: number }[] = [
    { id: "todos", nombre: "Todos", n: bandeja.length },
    { id: "sin_procesar", nombre: "Por correr", n: bandeja.length - procesados },
    { id: "procesados", nombre: "Corridos", n: procesados },
    ...(delBuzon ? [{ id: "del_buzon", nombre: "Del buzón real", n: delBuzon }] : []),
  ];

  return (
    <>
      <div className="bandeja-herramientas">
        <label className="buscador-sobres">
          <Icono n="buscar" tam={15} />
          <input placeholder="Buscar remitente, asunto o texto" value={busqueda} onChange={(e) => setBusqueda(e.target.value)} />
          {busqueda && <button aria-label="Borrar la búsqueda" onClick={() => setBusqueda("")}><Icono n="cerrar" tam={12} /></button>}
        </label>
        <div className="filtros" role="tablist" aria-label="Filtrar">
          {filtros.map((f) => (
            <button key={f.id} role="tab" aria-selected={filtro === f.id} className={filtro === f.id ? "activo" : ""} onClick={() => setFiltro(f.id)}>
              {f.nombre}<span>{f.n}</span>
            </button>
          ))}
          <select className={`filtro-produccion ${["radicado", "manual"].includes(filtro) || motivos.includes(filtro) ? "activo" : ""}`} value={["radicado", "manual"].includes(filtro) || motivos.includes(filtro) ? filtro : ""} onChange={(e) => setFiltro(e.target.value || "todos")} aria-label="Filtrar por lo que pasó en producción">
            <option value="">En producción…</option>
            <option value="radicado">Radicados allá</option>
            <option value="manual">A manual allá</option>
            <optgroup label="A manual por">
              {motivos.map((m) => <option key={m} value={m}>{m.replace(/_/g, " ").toLowerCase()}</option>)}
            </optgroup>
          </select>
        </div>
        <div className="bandeja-seleccion">
          <span>{visibles.length} de {bandeja.length}</span>
          <button className="enlace" onClick={() => fijar([...new Set([...seleccion, ...visibles.map((v) => v.caso)])])}>Elegir los visibles</button>
        </div>
      </div>

      <div className="lista-sobres" ref={lista} onScroll={(e) => setScroll(e.currentTarget.scrollTop)} role="listbox" aria-multiselectable>
        <div className="lista-alto" style={{ height: visibles.length * ALTO_SOBRE }}>
          {visibles.slice(desde, hasta).map((c: Correo, i) => {
            const u = porCaso[c.caso];
            const adjuntos = c.adjuntos.filter((a) => !a.enLinea).length;
            return (
              <SobreDeCorreo
                key={c.caso}
                clave={c.caso}
                remitente={c.remitente || c.remitenteCorreo}
                asunto={c.asunto}
                previa={c.vistaPrevia}
                recibido={c.recibido}
                elegido={elegidos.has(c.caso)}
                abierto={foco?.tipo === "correo" && foco.caso === c.caso}
                corrida={u}
                alElegir={() => alternar(c.caso)}
                alAbrir={() => enfocar({ tipo: "correo", caso: c.caso })}
                estilo={{ transform: `translateY(${(desde + i) * ALTO_SOBRE}px)` }}
                fichas={
                  <>
                    {c.origen === "buzon" && <span className="ficha tono-azul" title="Traído del buzón real, con sus etiquetas de hoy">Buzón real</span>}
                    {adjuntos > 0 && <span className="ficha"><Icono n="adjunto" tam={11} />{adjuntos}</span>}
                    {c.desenlaceProduccion && (
                      <span className={`ficha tono-${TONO_DESENLACE[c.desenlaceProduccion] ?? "gris"}`} title={`En producción: ${NOMBRES_DESENLACE[c.desenlaceProduccion] ?? c.desenlaceProduccion}${c.motivoProduccion ? ` · ${c.motivoProduccion}` : ""}`}>
                        Allá: {NOMBRES_CORTOS_DESENLACE[c.desenlaceProduccion] ?? c.desenlaceProduccion}
                      </span>
                    )}
                  </>
                }
              />
            );
          })}
        </div>
        {!visibles.length && <div className="vacio">Nada con ese filtro. Prueba con «Todos» o borra la búsqueda.</div>}
      </div>

      <BandejaDeSalida
        elegidos={seleccion.map((caso) => ({ clave: caso, nombre: porClave.get(caso)?.remitente || porClave.get(caso)?.remitenteCorreo || caso }))}
        modo={modo}
        setModo={conLote ? setModo : undefined}
        alLimpiar={() => fijar([])}
        alCorrer={() => void lanzar()}
        ocupado={enviando}
        extra={enCola > 0 ? <button className="boton peligro chico" onClick={() => void cancelarCola()}>Cancelar {enCola} en cola</button> : undefined}
      />
    </>
  );
}

// -------------------------------------------------------------- la bandeja

export function Bandeja() {
  const corridas = usar((s) => s.corridas);
  const orden = usar((s) => s.orden);
  const flujo = usar((s) => s.flujos.find((f) => f.id === s.flujo));
  const conBuzon = flujo?.buzonReal ?? false;
  const [elegida, setFuente] = useState<"buzon" | "local">("buzon");
  // El buzón real solo existe en los flujos que saben leerlo.
  const fuente = conBuzon ? elegida : "local";
  const porCaso = useMemo(() => ultimas(corridas, orden), [corridas, orden]);

  return (
    <section className="bandeja" aria-label="Correos por radicar">
      <header className="bandeja-titulo">
        <h2>Correspondencia</h2>
        {conBuzon && <div className="fuentes" role="tablist" aria-label="De dónde salen los correos">
          {([["buzon", "Buzón real", "correo"], ["local", "Descargados", "bandeja"]] as const).map(([id, nombre, icono]) => (
            <button key={id} role="tab" aria-selected={fuente === id} className={fuente === id ? "activo" : ""} onClick={() => setFuente(id)}>
              {fuente === id && <motion.span layoutId="fuente-activa" className="fuente-fondo" transition={{ type: "spring", stiffness: 520, damping: 38 }} />}
              <Icono n={icono} tam={13} />
              <span>{nombre}</span>
            </button>
          ))}
        </div>}
      </header>
      {fuente === "buzon" ? <PanelBuzon porCaso={porCaso} /> : <PanelDescargados porCaso={porCaso} conLote={flujo?.lote ?? false} />}
    </section>
  );
}
