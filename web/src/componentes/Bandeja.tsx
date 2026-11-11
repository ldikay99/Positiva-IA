import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { ZonaArchivos } from "./Archivos.tsx";
import { NOMBRES_CORTOS_DESENLACE, NOMBRES_DESENLACE, TONO_DESENLACE, motivoLegible } from "../progreso.ts";
import { nombreResultado } from "../lenguaje.ts";
import { Selector } from "./Selector.tsx";
import type { Correo, CorreoDelBuzon, Corrida } from "../tipos.ts";

// ------------------------------------------------- control desde el asistente

type Control = { accion?: string; fuente?: string; filtro?: string; texto?: string };
/**
 * Si la bandeja estaba oculta, el pedido llega antes de que exista: se guarda
 * un momento y lo toma al montarse.
 */
let ultimoControl: { d: Control; t: number } | null = null;
window.addEventListener("positiva:control", (e) => {
  const d = (e as CustomEvent<Control>).detail;
  if (d?.accion === "correspondencia" || d?.accion === "filtrar_correos") ultimoControl = { d, t: Date.now() };
});
function pendiente(accion: string): Control | null {
  return ultimoControl && ultimoControl.d.accion === accion && Date.now() - ultimoControl.t < 4000 ? ultimoControl.d : null;
}

/** Alto de cada sobre en la lista larga (se dibujan solo los visibles). */
const ALTO_SOBRE = 70;

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
const TONOS = ["azul", "violeta", "turquesa", "rosa"] as const;
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
        <span className="cara dorso"><Icono n="ok" tam={13} strokeWidth={2.6} /></span>
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
          <Icono n="ojo" tam={14} />
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
          {n === 0 && <span className="salida-pista">Ningún correo elegido</span>}
          <div className="salida-acciones">
            {extra}
            {n > 0 && <button className="boton fantasma chico" onClick={alLimpiar} title="Quitar la selección">Quitar</button>}
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
/**
 * Con el buzón al día, el panel no queda en blanco: muestra los últimos correos
 * descargados para correr uno con un clic.
 */
function RecientesDescargados({ porCaso }: { porCaso: Record<string, Corrida> }) {
  const bandeja = usar((s) => s.bandeja);
  const procesar = usar((s) => s.procesar);
  const enfocar = usar((s) => s.enfocar);
  const recientes = [...bandeja].sort((a, b) => b.recibido.localeCompare(a.recibido)).slice(0, 6);
  if (!recientes.length) return null;
  return (
    <section className="recientes-desc" aria-label="Últimos correos descargados">
      <h4>Últimos descargados</h4>
      {recientes.map((c) => {
        const r = porCaso[c.caso]?.resultado;
        return (
          <div key={c.caso} className="reciente-fila">
            <button className="reciente-abrir" onClick={() => enfocar({ tipo: "correo", caso: c.caso })} title="Ver el correo">
              <b>{c.asunto || "(sin asunto)"}</b>
              <small>{c.remitente || c.remitenteCorreo}{c.adjuntos.length ? ` · ${c.adjuntos.length} adjunto${c.adjuntos.length === 1 ? "" : "s"}` : ""}{r ? ` · ${nombreResultado(String(r.accion), true)}` : ""}</small>
            </button>
            <button className="boton chico fantasma" onClick={() => void procesar([c.caso])} title="Correr este correo" aria-label={`Correr ${c.asunto}`}><Icono n="play" tam={12} /></button>
          </div>
        );
      })}
    </section>
  );
}

function PanelBuzon({ porCaso, verDescargados }: { porCaso: Record<string, Corrida>; verDescargados?: () => void }) {
  const buzon = usar((s) => s.buzon);
  const cargando = usar((s) => s.cargandoBuzon);
  const cargar = usar((s) => s.cargarBuzon);
  const correr = usar((s) => s.correrComoElJob);
  const avisar = usar((s) => s.avisar);
  const enfocar = usar((s) => s.enfocar);
  const flujo = usar((s) => s.flujo);
  const [n, setN] = useState(10);
  const [elegidos, setElegidos] = useState<string[]>([]);
  const [corriendo, setCorriendo] = useState(false);

  // Cada flujo lee su propia carpeta del buzón de producción.
  useEffect(() => {
    usar.setState({ buzon: null });
    setElegidos([]);
    void cargar(n);
  }, [n, cargar, flujo]);

  // En tutelas el job toma el hilo como unidad: se elige el correo más reciente de cada hilo.
  const correos = (buzon?.correos ?? []).filter((c) => c.principal !== false);
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
          <Selector valor={n} opciones={[5, 10, 20, 50].map((x) => ({ valor: x, etiqueta: String(x) }))} alCambiar={setN} etiqueta="Cuántos correos mirar" tamano="chico" tono="plano" />
          {typeof buzon?.total === "number" && <span className="buzon-total" title={`${buzon.total} correos en la carpeta que lee el job`}>de {buzon.total}</span>}
        </label>
        <button className="boton fantasma chico" disabled={cargando} onClick={() => void cargar(n, true)} aria-label="Volver a leer el buzón">
          {cargando ? <span className="girando" /> : <Icono n="reordenar" tam={13} />}Leer de nuevo
        </button>
      </div>
      <div className="lista-sobres estatica" role="listbox" aria-multiselectable aria-busy={!buzon && cargando}>
        {buzon?.error && (
          <div className="bandeja-estado">
            <span className="bandeja-estado-icono rojo"><Icono n="alerta" tam={18} /></span>
            <b>No se pudo leer el buzón</b>
            <p>{buzon.error}</p>
            <div className="fila envuelve">
              <button className="boton chico" onClick={() => void cargar(n, true)}><Icono n="reordenar" tam={13} />Intentar de nuevo</button>
              {verDescargados && <button className="boton fantasma chico" onClick={verDescargados}>Usar los descargados</button>}
            </div>
          </div>
        )}
        {!buzon && (
          <>
            <p className="bandeja-cargando"><span className="girando" />Trayendo los próximos {n} correos de la carpeta que lee el job. Solo se leen: nada se marca ni se mueve.</p>
            {Array.from({ length: Math.min(n, 5) }, (_, i) => (
              <div key={i} className="sobre sobre-fantasma" aria-hidden style={{ animationDelay: `${i * 0.12}s` }}>
                <span className="sobre-avatar" />
                <span className="fantasma-lineas"><i /><i /><i /></span>
              </div>
            ))}
          </>
        )}
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
                {(c.correosEnHilo ?? 1) > 1 && <span className="ficha tono-azul" title="El job procesa el hilo completo">hilo de {c.correosEnHilo}</span>}
                {c.categorias.map((k) => <span key={k} className="ficha tono-ambar" title="Etiqueta que tiene hoy en el buzón">{k}</span>)}
              </>
            }
          />
        ))}
        {buzon && !buzon.error && !correos.length && (
          <div className="bandeja-estado">
            <span className="bandeja-estado-icono"><Icono n="ok" tam={18} /></span>
            <b>No hay correos esperando</b>
            <p>La carpeta que lee el job está al día. Cuando llegue un correo aparecerá aquí; mientras tanto puedes correr correos ya descargados.</p>
            {verDescargados && <button className="boton chico" onClick={verDescargados}><Icono n="bandeja" tam={13} />Ver los descargados</button>}
          </div>
        )}
        {buzon && !buzon.error && !correos.length && <RecientesDescargados porCaso={porCaso} />}
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

function PanelDescargados({ porCaso, conLote, fuentes, produccion = true, antes, vacio }: {
  porCaso: Record<string, Corrida>;
  conLote: boolean;
  /** Solo los correos de estas fuentes (la correspondencia ya es del flujo; esto separa sus pestañas). */
  fuentes?: NonNullable<Correo["fuente"]>[];
  /** Los filtros por lo que pasó en producción solo tienen sentido con el universo de PQRD. */
  produccion?: boolean;
  antes?: ReactNode;
  vacio?: ReactNode;
}) {
  const todos = usar((s) => s.bandeja);
  const bandeja = useMemo(() => (fuentes ? todos.filter((c) => c.fuente && fuentes.includes(c.fuente)) : todos), [todos, fuentes]);
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
  // El asistente filtra la lista: {accion:"filtrar_correos", filtro, texto?}.
  useEffect(() => {
    const aplicar = (d: Control | null) => {
      if (d?.accion !== "filtrar_correos") return;
      if (d.filtro && ["todos", "sin_procesar", "procesados", "radicado", "manual"].includes(d.filtro)) setFiltro(d.filtro);
      if (typeof d.texto === "string") setBusqueda(d.texto);
    };
    aplicar(pendiente("filtrar_correos"));
    const oir = (e: Event) => aplicar((e as CustomEvent<Control>).detail);
    window.addEventListener("positiva:control", oir);
    return () => window.removeEventListener("positiva:control", oir);
  }, []);
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
      {antes}
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
          {produccion && <Selector
            className={`filtro-produccion ${["radicado", "manual"].includes(filtro) || motivos.includes(filtro) ? "activo" : ""}`}
            valor={["radicado", "manual"].includes(filtro) || motivos.includes(filtro) ? filtro : null}
            placeholder="En producción…"
            opciones={[
              ...(["radicado", "manual"].includes(filtro) || motivos.includes(filtro) ? [{ valor: "todos", etiqueta: "Cualquier resultado" }] : []),
              { valor: "radicado", etiqueta: "Radicados en producción" },
              { valor: "manual", etiqueta: "A revisión manual en producción" },
              ...motivos.map((m) => ({ valor: m, etiqueta: motivoLegible(m), texto: motivoLegible(m), detalle: "A revisión manual por este motivo" })),
            ]}
            alCambiar={(v) => setFiltro(v)}
            etiqueta="Filtrar por lo que pasó en producción"
            tamano="chico"
            tono="plano"
            ancho={280}
          />}
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
                    {c.fuente === "buzon_flujo" && <span className="ficha tono-azul" title="Traído del buzón de este flujo (solo lectura)">Buzón del flujo</span>}
                    {c.fuente === "prueba" && fuentes && fuentes.length > 1 && <span className="ficha" title="Subido a la bandeja de prueba">De prueba</span>}
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
        {!visibles.length && (
          <div className="bandeja-estado">
            <span className="bandeja-estado-icono neutro"><Icono n="buscar" tam={18} /></span>
            {bandeja.length ? (
              <>
                <b>Ningún correo coincide</b>
                <p>{busqueda ? `Nada contiene «${busqueda}» con este filtro.` : "No hay correos en este grupo."}</p>
                <button className="boton chico" onClick={() => { setBusqueda(""); setFiltro("todos"); }}>Ver todos los correos</button>
              </>
            ) : (
              vacio ?? (
                <>
                  <b>Aún no hay correos descargados</b>
                  <p>Los correos que se descarguen del buzón aparecerán aquí para correrlos cuantas veces quieras.</p>
                </>
              )
            )}
          </div>
        )}
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

/** La bandeja de prueba de un flujo declarativo: los .eml/.msg que se suben, solo suyos. */
function PanelPrueba({ porCaso }: { porCaso: Record<string, Corrida> }) {
  const flujo = usar((s) => s.flujo);
  const cargarBandeja = usar((s) => s.cargarBandeja);
  const avisar = usar((s) => s.avisar);
  return (
    <PanelDescargados
      porCaso={porCaso}
      conLote={false}
      fuentes={["prueba"]}
      produccion={false}
      antes={
        <div className="bandeja-prueba arch-ambito">
          <ZonaArchivos
            flujo={flujo}
            valor={[]}
            alCambiar={() => undefined}
            destino={`/api/bandeja/prueba?flujo=${encodeURIComponent(flujo)}`}
            tipos={["eml", "msg"]}
            texto="Suelta aquí correos .eml o .msg"
            ayuda="Quedan en la bandeja de prueba de este flujo, con sus adjuntos. Nada sale de aquí."
            alSubirCorreos={(r) => { void cargarBandeja(); avisar(`${r.correos.length === 1 ? "Correo agregado" : `${r.correos.length} correos agregados`} a la bandeja de prueba`); }}
          />
        </div>
      }
      vacio={<><b>La bandeja de prueba está vacía</b><p>Sube correos .eml o .msg (los exportas desde Outlook o Gmail) para correr este flujo con ellos.</p></>}
    />
  );
}

/** «Buzón del flujo»: lee su propio buzón (la credencial de su «Entrada de correo», solo lectura). */
function PanelBuzonDelFlujo({ porCaso }: { porCaso: Record<string, Corrida> }) {
  const flujo = usar((s) => s.flujo);
  const cargarBandeja = usar((s) => s.cargarBandeja);
  const [estado, setEstado] = useState<{ cargando: boolean; error?: string; carpeta?: string }>({ cargando: true });
  const leer = async () => {
    setEstado({ cargando: true });
    try {
      const r = await pedir<{ carpeta: string }>(`/api/bandeja/buzon?flujo=${encodeURIComponent(flujo)}&n=20`);
      await cargarBandeja();
      setEstado({ cargando: false, carpeta: r.carpeta });
    } catch (e) {
      setEstado({ cargando: false, error: (e as Error).message });
    }
  };
  useEffect(() => {
    void leer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flujo]);
  return (
    <PanelDescargados
      porCaso={porCaso}
      conLote={false}
      fuentes={["buzon_flujo"]}
      produccion={false}
      antes={
        <div className="buzon-controles">
          <span className="fuente-propia">{estado.carpeta ? `Carpeta «${estado.carpeta}», solo lectura: nada se marca ni se mueve.` : "Su buzón, solo lectura."}</span>
          <button className="boton fantasma chico" disabled={estado.cargando} onClick={() => void leer()} aria-label="Volver a leer el buzón del flujo">
            {estado.cargando ? <span className="girando" /> : <Icono n="reordenar" tam={13} />}Leer de nuevo
          </button>
        </div>
      }
      vacio={
        estado.error ? (
          <><b>No se pudo leer el buzón del flujo</b><p>{estado.error}</p></>
        ) : estado.cargando ? (
          <><b>Leyendo el buzón…</b><p>Solo se leen los correos; nada se marca ni se mueve.</p></>
        ) : (
          <><b>No hay correos en su buzón</b><p>Cuando lleguen correos a la carpeta configurada aparecerán aquí.</p></>
        )
      }
    />
  );
}

/**
 * El panel de la izquierda de un flujo que NO arranca con un correo (manual, archivos,
 * webhook, programado): sus corridas recientes y «Correr» con su formulario.
 */
export function PanelSinCorreo() {
  const f = usar((s) => s.flujos.find((x) => x.id === s.flujo));
  const corridas = usar((s) => s.corridas);
  const orden = usar((s) => s.orden);
  const visible = usar((s) => s.corridaVisible);
  const verCorrida = usar((s) => s.verCorrida);
  const lista = orden.map((id) => corridas[id]).filter((c): c is Corrida => Boolean(c)).slice(0, 60);
  const QUE: Record<string, string> = {
    entrada_archivos: "Arranca con los archivos que subes en «Correr»: imágenes y documentos, que lee como adjuntos.",
    entrada_manual: "Arranca con los datos que escribes en «Correr».",
    webhook: "Arranca cuando otro sistema llama a su webhook; también puedes probarlo con «Correr».",
    programado: "Corre solo en su horario; con «Correr» lo pruebas ahora.",
  };
  const abrirCorrer = () => window.dispatchEvent(new CustomEvent("positiva:control", { detail: { accion: "editor", op: "abrir_correr" } }));
  const hace = (t: number) => {
    const s = (Date.now() - t) / 1000;
    return s < 60 ? "ahora" : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.round(s / 3600)} h` : new Date(t).toLocaleDateString("es-CO", { day: "numeric", month: "short" });
  };
  return (
    <section className="panel-corridas" aria-label="Ejecuciones del flujo">
      <header>
        <h2>Ejecuciones</h2>
        <p>{QUE[f?.disparador ?? ""] ?? "Corre este flujo y revisa aquí cada resultado."}</p>
        <button className="boton primario chico" onClick={abrirCorrer}><Icono n="play" tam={13} />Correr</button>
      </header>
      {lista.length ? (
        <ul className="pc-lista">
          {lista.map((c) => {
            const accion = String(c.resultado?.accion ?? (c.estado === "en_cola" ? "en cola" : c.estado === "procesando" ? "corriendo" : c.estado));
            const nombre = c.resultado ? nombreResultado(accion, false) : accion === "en cola" ? "En espera" : accion === "corriendo" ? "Corriendo" : accion;
            return (
              <li key={c.id}>
                <button className={visible === c.id ? "activa" : ""} onClick={() => void verCorrida(c.id)}>
                  <span className={`punto tono-${c.resultado ? TONO_DESENLACE[accion] ?? "gris" : "azul"}`} />
                  <span className="pc-texto">
                    <b>{nombre}</b>
                    <small>{c.resultado?.motivo ? motivoLegible(String(c.resultado.motivo)) : c.caso}</small>
                  </span>
                  <span className="pc-cuando">{hace(c.creada)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="pc-vacio">Todavía no ha corrido. Pulsa «Correr» para la primera vez: verás cada paso en el lienzo y en el panel de abajo; el resultado queda en el Historial.</p>
      )}
    </section>
  );
}

export function Bandeja() {
  const flujo = usar((s) => s.flujos.find((f) => f.id === s.flujo));
  if (flujo?.tipo === "declarativo") return <BandejaDeclarativa />;
  return <BandejaDeCodigo />;
}

/** Correspondencia de un flujo declarativo: su bandeja de prueba y su buzón, nunca la de PQRD. */
function BandejaDeclarativa() {
  const corridas = usar((s) => s.corridas);
  const orden = usar((s) => s.orden);
  const flujo = usar((s) => s.flujos.find((f) => f.id === s.flujo));
  const porCaso = useMemo(() => ultimas(corridas, orden), [corridas, orden]);
  const [fuente, setFuente] = useState<"prueba" | "buzon">("prueba");
  const recibe = flujo?.disparador === "correo_nuevo";
  return (
    <section className="bandeja" aria-label="Correspondencia del flujo">
      <header className="bandeja-titulo">
        <div>
          <h2 title={recibe ? "Los correos que trajo su revisión del buzón. Elige uno o varios y pulsa «Correr»." : "Solo los correos de este flujo. Elige uno o varios y pulsa «Correr»."}>Correspondencia</h2>
        </div>
        {!recibe && (
          <div className="fuentes" role="tablist" aria-label="De dónde salen los correos">
            {([["prueba", "Bandeja de prueba", "bandeja"], ["buzon", "Buzón del flujo", "correo"]] as const).map(([id, nombre, icono]) => (
              <button key={id} role="tab" aria-selected={fuente === id} className={fuente === id ? "activo" : ""} onClick={() => setFuente(id)}>
                {fuente === id && <motion.span layoutId="fuente-activa" className="fuente-fondo" transition={{ type: "spring", stiffness: 520, damping: 38 }} />}
                <Icono n={icono} tam={13} />
                <span>{nombre}</span>
              </button>
            ))}
          </div>
        )}
      </header>
      {recibe ? (
        <PanelDescargados porCaso={porCaso} conLote={false} fuentes={["recibido"]} produccion={false}
          vacio={<><b>Aún no llegan correos</b><p>Cuando su revisión del buzón traiga correos nuevos aparecerán aquí.</p></>} />
      ) : fuente === "prueba" ? <PanelPrueba porCaso={porCaso} /> : <PanelBuzonDelFlujo porCaso={porCaso} />}
    </section>
  );
}

function BandejaDeCodigo() {
  const corridas = usar((s) => s.corridas);
  const orden = usar((s) => s.orden);
  const flujo = usar((s) => s.flujos.find((f) => f.id === s.flujo));
  const conBuzon = flujo?.buzonReal ?? false;
  const [elegida, setFuente] = useState<"buzon" | "local">("buzon");
  // El asistente cambia la fuente: {accion:"correspondencia", fuente}; filtrar los correos lleva a los descargados.
  useEffect(() => {
    const aplicar = (d: Control | null) => {
      if (d?.accion === "correspondencia" && (d.fuente === "buzon" || d.fuente === "descargados")) setFuente(d.fuente === "buzon" ? "buzon" : "local");
      else if (d?.accion === "filtrar_correos") setFuente("local");
    };
    aplicar(pendiente("correspondencia") ?? pendiente("filtrar_correos"));
    const oir = (e: Event) => aplicar((e as CustomEvent<Control>).detail);
    window.addEventListener("positiva:control", oir);
    return () => window.removeEventListener("positiva:control", oir);
  }, []);
  // El buzón real solo existe en los flujos que saben leerlo.
  const fuente = conBuzon ? elegida : "local";
  const porCaso = useMemo(() => ultimas(corridas, orden), [corridas, orden]);

  return (
    <section className="bandeja" aria-label="Correos por radicar">
      <header className="bandeja-titulo">
        <div>
          <h2 title="Elige uno o varios correos y pulsa «Correr» para ver cómo los procesa el motor.">Correspondencia</h2>
        </div>
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
      {fuente === "buzon" ? <PanelBuzon porCaso={porCaso} verDescargados={() => setFuente("local")} /> : <PanelDescargados porCaso={porCaso} conLote={flujo?.lote ?? false} produccion={(flujo?.base ?? flujo?.id) === "pqrd"} />}
    </section>
  );
}
