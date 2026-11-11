import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  BaseEdge,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  applyNodeChanges,
  getBezierPath,
  useReactFlow,
  useStore,
  type Connection,
  type Edge,
  type EdgeProps,
  type FinalConnectionState,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AnimatePresence, motion } from "motion/react";
import { usar } from "../estado.ts";
import { Icono, type NombreIcono } from "../iconos.tsx";
import { hayCambios, usarEditor, type Version } from "../editor/almacen.ts";
import { resultadosDe, usarCorridaVisible, type ResultadoNodo } from "../editor/corrida.ts";
import {
  ANCHO_NODO,
  ORDEN_CATEGORIAS,
  RAMA_ERROR,
  asaDeArista,
  asasDe,
  autoOrdenar,
  configInicial,
  cuandoDeAsa,
  idNuevo,
  lugarDespues,
  motivoParaNoConectar,
  resumenConfig,
  textoJson,
  tieneRamaError,
  TIPOS_MINIMOS,
  visualDe,
  type AristaDef,
  type Definicion,
  type NodoDef,
  type Posicion,
  type TipoNodo,
} from "../editor/modelo.ts";
import { segundos } from "../progreso.ts";
import { MenuContextual, type Opcion } from "./MenuContextual.tsx";
import { DialogoCorrer, DialogoNombre } from "./Dialogos.tsx";
import { PanelIaFlujo, rotuloIaFlujo } from "./IA.tsx";

/**
 * El editor de un flujo declarativo: paleta de nodos, lienzo donde se
 * conectan arrastrando de un asa a otra, deshacer y rehacer, validación al
 * guardar con los problemas marcados en cada nodo, versiones y «Correr».
 */

// ------------------------------------------------------------------- tipos

interface DatosNodo extends Record<string, unknown> {
  n: NodoDef;
  tipo: TipoNodo | undefined;
  asas: { id: string; etiqueta: string; defecto: boolean }[];
  resumen: string;
  problemas: number;
  resultado?: ResultadoNodo;
  resaltado: boolean;
  usadas: Set<string>;
}

interface DatosCable extends Record<string, unknown> {
  cuando: string;
  error: boolean;
  estado: "inactivo" | "recorrido" | "activo";
  borrar: () => void;
}

const sinAcentos = (t: string) => t.toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");

// ------------------------------------------------------------- popover datos

function VerDatos({ r }: { r: ResultadoNodo }) {
  const [abierto, setAbierto] = useState<DOMRect | null>(null);
  const boton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!abierto) return;
    const cerrar = (e: Event) => {
      if (e instanceof KeyboardEvent && e.key !== "Escape") return;
      if (e instanceof PointerEvent && (e.target as HTMLElement).closest(".en-datos-pop, .en-ver-datos")) return;
      setAbierto(null);
    };
    window.addEventListener("pointerdown", cerrar, true);
    window.addEventListener("keydown", cerrar);
    window.addEventListener("wheel", cerrar as EventListener, { passive: true });
    return () => {
      window.removeEventListener("pointerdown", cerrar, true);
      window.removeEventListener("keydown", cerrar);
      window.removeEventListener("wheel", cerrar as EventListener);
    };
  }, [abierto]);
  const vacio = r.muestra === undefined || r.muestra === null || (typeof r.muestra === "object" && !Object.keys(r.muestra as object).length);
  return (
    <>
      <button ref={boton} type="button" className="en-ver-datos nodrag" aria-expanded={Boolean(abierto)} onClick={(e) => { e.stopPropagation(); setAbierto(abierto ? null : boton.current!.getBoundingClientRect()); }}>
        Ver datos
      </button>
      {abierto &&
        createPortal(
          <motion.div
            className="en-datos-pop"
            role="dialog"
            aria-label="Datos del nodo"
            style={{ left: Math.max(8, Math.min(abierto.left, window.innerWidth - 348)), top: abierto.bottom + 8 > window.innerHeight - 260 ? undefined : abierto.bottom + 8, bottom: abierto.bottom + 8 > window.innerHeight - 260 ? window.innerHeight - abierto.top + 8 : undefined }}
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <header className="fila">
              <b className="crece">Lo que dejó este nodo</b>
              <button className="boton-mini" aria-label="Cerrar" onClick={() => setAbierto(null)}><Icono n="cerrar" tam={13} /></button>
            </header>
            {r.fallo && <p className="pn-fallo"><Icono n="alerta" tam={13} />{r.fallo}</p>}
            {vacio ? <p className="texto-3 chico">No dejó datos para mostrar.</p> : <pre className="en-datos-json">{textoJson(r.muestra)}</pre>}
          </motion.div>,
          document.body,
        )}
    </>
  );
}

// -------------------------------------------------------------------- nodo

const NodoEditor = memo(function NodoEditor({ data, selected }: NodeProps<Node<DatosNodo>>) {
  const { n, tipo, asas, resultado: r } = data;
  const disparador = Boolean(tipo?.disparador);
  const vis = visualDe(disparador ? "disparador" : tipo?.categoria);
  const deshabilitado = n.config.deshabilitado === true;
  return (
    <div className={`en-nodo t-${vis.tono} ${selected ? "sel" : ""} ${deshabilitado ? "apagado" : ""} ${data.problemas ? "con-problema" : ""} ${data.resaltado ? "resaltado" : ""} ${r ? `corrio-${r.estado}` : ""}`}>
      {!disparador && <Handle type="target" position={Position.Left} id="e" className="en-asa-entrada" />}
      <div className="en-cabeza">
        <span className="en-icono"><Icono n={vis.icono as NombreIcono} tam={14} /></span>
        <span className="en-tipo">{tipo?.nombre ?? n.tipo}</span>
        {r?.estado === "activo" && <span className="en-insignia activo" title="En curso"><span className="girando" /></span>}
        {r?.estado === "hecho" && <span className="en-insignia hecho" title="Terminó"><Icono n="ok" tam={10} strokeWidth={2.8} /></span>}
        {r?.estado === "fallo" && <span className="en-insignia fallo" title="Falló"><Icono n="cerrar" tam={10} strokeWidth={2.8} /></span>}
        {deshabilitado && <span className="en-insignia apagada" title="Deshabilitado"><Icono n="pausa" tam={10} strokeWidth={2.6} /></span>}
      </div>
      <div className="en-titulo">{n.nombre}</div>
      <div className="en-resumen" title={data.resumen}>{data.resumen}</div>
      {data.problemas > 0 && <div className="en-problema"><Icono n="alerta" tam={11} />{data.problemas} problema{data.problemas > 1 ? "s" : ""} por resolver</div>}
      {r && r.estado !== "activo" && (
        <div className={`en-resultado ${r.estado}`}>
          <span>{r.estado === "fallo" ? (r.ramaError ? "falló, siguió por la rama de error" : r.continua ? "falló y siguió" : "falló") : r.estado === "omitido" ? "omitido" : r.salida ? `salió por «${r.salida}»` : "terminó"}</span>
          {r.ms > 0 && <span className="mono">{segundos(r.ms)}</span>}
          {(r.fallo || (r.muestra !== undefined && r.muestra !== null && (typeof r.muestra !== "object" || Object.keys(r.muestra as object).length > 0))) && <VerDatos r={r} />}
        </div>
      )}
      {(asas.length > 0 || tieneRamaError(tipo)) && (
        <div className="en-salidas">
          {asas.map((a) => (
            <div key={a.id} className={`en-salida ${data.usadas.has(a.id) ? "usada" : ""} ${r?.salida && (r.salida === a.id || (a.defecto && !asas.some((x) => x.id === r.salida) && r.salida !== RAMA_ERROR)) ? "tomada" : ""}`}>
              <span>{a.etiqueta}</span>
              <Handle type="source" position={Position.Right} id={`s:${a.id}`} className="en-asa-salida" title={`Arrastra para conectar la salida «${a.etiqueta}»`} />
            </div>
          ))}
          {tieneRamaError(tipo) && (
            <div className={`en-salida error ${data.usadas.has(RAMA_ERROR) ? "usada" : ""} ${r?.ramaError ? "tomada" : ""}`}>
              <span>si falla</span>
              <Handle type="source" position={Position.Right} id={`s:${RAMA_ERROR}`} className="en-asa-salida error" title="Arrastra para decidir qué pasa si este nodo falla" />
            </div>
          )}
        </div>
      )}
    </div>
  );
});

// ------------------------------------------------------------------- cable

const CableEditor = memo(function CableEditor({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected, markerEnd }: EdgeProps<Edge<DatosCable>>) {
  const [ruta, lx, ly] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, curvature: 0.32 });
  const estado = data?.estado ?? "inactivo";
  return (
    <>
      <BaseEdge id={id} path={ruta} markerEnd={markerEnd} className={`en-cable ${data?.error ? "error" : ""} ${estado} ${selected ? "sel" : ""}`} interactionWidth={18} />
      {estado === "activo" &&
        [0, 0.5].map((d) => (
          <circle key={d} r={3.2} className="chispa">
            <animateMotion dur="1.1s" begin={`${d}s`} repeatCount="indefinite" path={ruta} />
          </circle>
        ))}
      {(data?.cuando || selected) && (
        <EdgeLabelRenderer>
          <div className={`en-cable-etiqueta nodrag nopan ${data?.error ? "error" : ""} ${selected ? "sel" : ""} ${estado}`} style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}>
            {data?.cuando && <span>{data.cuando === RAMA_ERROR ? "si falla" : data.cuando}</span>}
            {selected && (
              <button type="button" aria-label="Quitar esta conexión" title="Quitar esta conexión (Supr)" onClick={(e) => { e.stopPropagation(); data?.borrar(); }}>
                <Icono n="cerrar" tam={11} strokeWidth={2.4} />
              </button>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
});

const tiposNodo = { paso: NodoEditor };
const tiposCable = { cable: CableEditor };

// ------------------------------------------------------------------ paleta

function grupos(tipos: TipoNodo[], q: string): [string, TipoNodo[]][] {
  const t = sinAcentos(q.trim());
  const m = new Map<string, TipoNodo[]>();
  for (const x of tipos) {
    if (t && !sinAcentos(`${x.nombre} ${x.tipo} ${x.descripcion} ${x.categoria}`).includes(t)) continue;
    const c = x.disparador ? "disparador" : x.categoria;
    m.set(c, [...(m.get(c) ?? []), x]);
  }
  return [...m.entries()].sort(([a], [b]) => (ORDEN_CATEGORIAS.indexOf(a) + 100 * Number(!ORDEN_CATEGORIAS.includes(a))) - (ORDEN_CATEGORIAS.indexOf(b) + 100 * Number(!ORDEN_CATEGORIAS.includes(b))));
}

function ItemTipo({ t, elegir, activo = false, conDescripcion = true }: { t: TipoNodo; elegir: (t: TipoNodo) => void; activo?: boolean; conDescripcion?: boolean }) {
  const vis = visualDe(t.disparador ? "disparador" : t.categoria);
  return (
    <button
      type="button"
      className={`pal-item ${activo ? "activo" : ""}`}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("application/x-positiva-tipo", t.tipo);
        e.dataTransfer.effectAllowed = "copy";
      }}
      onClick={() => elegir(t)}
      title={`${t.descripcion}\n\nArrastra al lienzo o haz clic para agregarlo después del nodo elegido.`}
    >
      <span className={`mini-icono t-${vis.tono}`}><Icono n={vis.icono as NombreIcono} tam={13} /></span>
      <span className="crece">
        <b>{t.nombre}</b>
        {conDescripcion && t.descripcion && <small>{t.descripcion}</small>}
      </span>
      {t.escritura && <span className="pal-escribe" title="Escribe en otro sistema (simulado sin permiso)"><Icono n="enviar" tam={11} /></span>}
    </button>
  );
}

function Paleta({ tipos, cargados, elegir, cerrar, busquedaInicial, n }: { tipos: TipoNodo[]; cargados: boolean; elegir: (t: TipoNodo) => void; cerrar: () => void; busquedaInicial: string; n: number }) {
  const [q, setQ] = useState(busquedaInicial);
  const entrada = useRef<HTMLInputElement>(null);
  const cargarTipos = usarEditor((s) => s.cargarTipos);
  useEffect(() => {
    setQ(busquedaInicial);
    setTimeout(() => entrada.current?.focus(), 60);
  }, [busquedaInicial, n]);
  const g = useMemo(() => grupos(tipos, q), [tipos, q]);
  return (
    <motion.aside className="paleta" aria-label="Paleta de nodos" initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16, transition: { duration: 0.14 } }} transition={{ type: "spring", stiffness: 460, damping: 38 }}>
      <header className="paleta-cabeza">
        <b className="crece">Agregar un paso</b>
        <button className="boton-mini" aria-label="Cerrar la paleta" title="Cerrar" onClick={cerrar}><Icono n="cerrar" tam={14} /></button>
      </header>
      <div className="campo-icono paleta-buscar">
        <Icono n="buscar" tam={14} />
        <input ref={entrada} className="campo" placeholder="Buscar: clasificar, http, correo…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar un tipo de nodo" onKeyDown={(e) => { if (e.key === "Enter" && g[0]?.[1][0]) elegir(g[0][1][0]); if (e.key === "Escape") cerrar(); }} />
      </div>
      <div className="paleta-lista">
        {!cargados ? (
          <div className="vacio grande chico-texto">
            <Icono n="capas" tam={24} />
            <span>Los tipos de nodo todavía no llegan del servidor.</span>
            <button className="boton chico" onClick={() => void cargarTipos()}>Volver a intentar</button>
          </div>
        ) : g.length ? (
          g.map(([cat, lista]) => (
            <section key={cat} className="paleta-grupo">
              <h3>{visualDe(cat).nombre}</h3>
              {lista.map((t) => <ItemTipo key={t.tipo} t={t} elegir={elegir} />)}
            </section>
          ))
        ) : (
          <div className="vacio">Ningún tipo coincide con «{q}».</div>
        )}
      </div>
      <p className="paleta-pie texto-3 diminuto">Arrastra un paso al lienzo, o haz clic para ponerlo después del nodo elegido. Doble clic en el lienzo abre la búsqueda rápida.</p>
    </motion.aside>
  );
}

function BusquedaRapida({ x, y, tipos, elegir, cerrar, titulo }: { x: number; y: number; tipos: TipoNodo[]; elegir: (t: TipoNodo) => void; cerrar: () => void; titulo?: string }) {
  const [q, setQ] = useState("");
  const [activo, setActivo] = useState(0);
  const caja = useRef<HTMLDivElement>(null);
  const lista = useMemo(() => grupos(tipos, q).flatMap(([, l]) => l).slice(0, 12), [tipos, q]);
  useEffect(() => {
    const fuera = (e: PointerEvent) => !caja.current?.contains(e.target as globalThis.Node) && cerrar();
    window.addEventListener("pointerdown", fuera, true);
    return () => window.removeEventListener("pointerdown", fuera, true);
  }, [cerrar]);
  const w = 320;
  const left = Math.max(8, Math.min(x, window.innerWidth - w - 8));
  const top = Math.max(8, Math.min(y, window.innerHeight - 380));
  return createPortal(
    <motion.div ref={caja} className="busqueda-rapida" role="dialog" aria-label="Agregar un paso" style={{ left, top, width: w }} initial={{ opacity: 0, scale: 0.97, y: -4 }} animate={{ opacity: 1, scale: 1, y: 0 }} transition={{ duration: 0.12 }}>
      {titulo && <p className="br-titulo">{titulo}</p>}
      <div className="campo-icono">
        <Icono n="buscar" tam={14} />
        <input
          className="campo"
          autoFocus
          placeholder="¿Qué paso agregar?"
          value={q}
          aria-label="Buscar un tipo de nodo"
          onChange={(e) => { setQ(e.target.value); setActivo(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setActivo((a) => Math.min(a + 1, lista.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActivo((a) => Math.max(a - 1, 0)); }
            else if (e.key === "Enter" && lista[activo]) elegir(lista[activo]!);
            else if (e.key === "Escape") { e.stopPropagation(); cerrar(); }
          }}
        />
      </div>
      <div className="br-lista" role="listbox">
        {lista.map((t, i) => <ItemTipo key={t.tipo} t={t} elegir={elegir} activo={i === activo} conDescripcion={i === activo} />)}
        {!lista.length && <div className="vacio chico">{tipos.length ? "Nada coincide." : "Los tipos de nodo aún no llegan del servidor."}</div>}
      </div>
    </motion.div>,
    document.body,
  );
}

// -------------------------------------------------------------- versiones

function hace(t: number): string {
  const s = (Date.now() - t) / 1000;
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return new Date(t).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

function PanelVersiones({ cerrar }: { cerrar: () => void }) {
  const versiones = usarEditor((s) => s.versiones);
  const restaurar = usarEditor((s) => s.restaurar);
  const actual = usarEditor((s) => s.version);
  const sucio = usarEditor(hayCambios);
  const avisar = usar((s) => s.avisar);
  const [lista, setLista] = useState<Version[] | null | undefined>(undefined);
  const [confirmar, setConfirmar] = useState<number | null>(null);
  const [ocupado, setOcupado] = useState(false);
  useEffect(() => {
    void versiones().then(setLista);
  }, [versiones, actual]);
  return (
    <motion.div className="en-pop versiones" role="dialog" aria-label="Versiones" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}>
      <header className="fila"><b className="crece">Versiones guardadas</b><button className="boton chico" title="Quién hizo cada versión, qué cambió y comparar dos" onClick={() => { cerrar(); usar.getState().irA("versiones"); }}>Historial completo</button><button className="boton-mini" aria-label="Cerrar" onClick={cerrar}><Icono n="cerrar" tam={13} /></button></header>
      {lista === undefined ? (
        <div className="vacio chico"><span className="girando" />Cargando…</div>
      ) : lista === null ? (
        <div className="vacio chico">El servidor todavía no ofrece el historial de versiones.</div>
      ) : !lista.length ? (
        <div className="vacio chico">Solo existe la versión actual.</div>
      ) : (
        <ul className="ver-lista">
          {lista.map((v) => (
            <li key={v.version} className={v.version === actual ? "actual" : ""}>
              <div className="fila">
                <b className="mono">v{v.version}</b>
                <span className="crece texto-2 chico">{v.actualizado ? hace(v.actualizado) : ""}{v.autor === "vera" ? " · Vera" : ""}{v.resumen || v.nota ? ` · ${v.resumen ?? v.nota}` : ""}{v.punto ? ` · ${v.punto}` : ""}</span>
                {v.version === actual ? <span className="pastilla diminuto verde">en uso</span> : confirmar !== v.version && <button className="boton chico" onClick={() => setConfirmar(v.version)}>Restaurar</button>}
              </div>
              {confirmar === v.version && (
                <div className="ver-confirmar">
                  <p className="chico">{sucio ? "Perderás los cambios sin guardar. " : ""}La versión {v.version} pasa a ser la actual (como versión {actual + 1}); la de ahora queda en el historial.</p>
                  <div className="fila">
                    <button className="boton chico" onClick={() => setConfirmar(null)}>Cancelar</button>
                    <button
                      className="boton chico primario"
                      disabled={ocupado}
                      onClick={async () => {
                        setOcupado(true);
                        try {
                          await restaurar(v.version);
                          avisar(`Versión ${v.version} restaurada`);
                          cerrar();
                        } catch (e) {
                          avisar((e as Error).message === "no existe" ? "El servidor todavía no permite restaurar versiones" : (e as Error).message, "error");
                        } finally {
                          setOcupado(false);
                        }
                      }}
                    >
                      {ocupado && <span className="girando" />}Sí, restaurar v{v.version}
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </motion.div>
  );
}

// --------------------------------------------------------------- barra

function NombreEditable() {
  const def = usarEditor((s) => s.definicion);
  const renombrar = usarEditor((s) => s.renombrar);
  const avisar = usar((s) => s.avisar);
  const [editando, setEditando] = useState(false);
  const [texto, setTexto] = useState("");
  const campo = useRef<HTMLInputElement>(null);
  if (!def) return null;
  const aceptar = async () => {
    const t = texto.trim();
    setEditando(false);
    if (t.length < 2 || t === def.nombre) return;
    await renombrar(t);
    avisar(`Ahora se llama «${t}»`);
  };
  return editando ? (
    <input
      ref={campo}
      className="en-nombre-campo"
      value={texto}
      autoFocus
      maxLength={80}
      aria-label="Nombre del flujo"
      onChange={(e) => setTexto(e.target.value)}
      onBlur={() => void aceptar()}
      onKeyDown={(e) => {
        if (e.key === "Enter") void aceptar();
        if (e.key === "Escape") { e.stopPropagation(); setEditando(false); }
      }}
    />
  ) : (
    <button className="en-nombre" title="Cambiar el nombre" onClick={() => { setTexto(def.nombre); setEditando(true); }}>
      <span>{def.nombre}</span>
      <Icono n="editar" tam={13} />
    </button>
  );
}

function BarraEditor({ abrirPaleta, paletaAbierta, abrirCorrer, ordenar }: { abrirPaleta: () => void; paletaAbierta: boolean; abrirCorrer: () => void; ordenar: () => void }) {
  const s = usarEditor();
  const sucio = hayCambios(s);
  const avisar = usar((x) => x.avisar);
  const cambiarFlujo = usar((x) => x.cambiarFlujo);
  const irA = usar((x) => x.irA);
  const [pop, setPop] = useState<"versiones" | "mas" | "borrar" | "ia" | null>(null);
  const [duplicar, setDuplicar] = useState(false);
  const [borrando, setBorrando] = useState(false);
  const caja = useRef<HTMLDivElement>(null);
  const malos = s.problemas.length;
  useEffect(() => {
    if (!pop) return;
    // Las listas de los selectores se abren en una capa propia (fuera de la barra): elegir en ellas no cierra el panel.
    const fuera = (e: PointerEvent) => !caja.current?.contains(e.target as globalThis.Node) && !(e.target as Element | null)?.closest?.(".selector-lista") && setPop(null);
    const tecla = (e: KeyboardEvent) => e.key === "Escape" && setPop(null);
    window.addEventListener("pointerdown", fuera, true);
    window.addEventListener("keydown", tecla);
    return () => {
      window.removeEventListener("pointerdown", fuera, true);
      window.removeEventListener("keydown", tecla);
    };
  }, [pop]);
  const guardar = async () => {
    const ok = await s.guardar();
    if (ok) avisar(`Guardado como versión ${usarEditor.getState().version}`);
    else avisar(`No se guardó: ${usarEditor.getState().problemas.length} problema${usarEditor.getState().problemas.length === 1 ? "" : "s"} por resolver`, "error");
  };
  if (!s.definicion) return null;
  return (
    <div className="en-barra" ref={caja}>
      <button className={`boton chico ${paletaAbierta ? "activo" : ""} en-agregar`} onClick={abrirPaleta} aria-pressed={paletaAbierta} title="Agregar un paso (/)"><Icono n="mas" tam={14} /><span className="etq">Agregar paso</span></button>
      <div className="en-titulo-flujo">
        <NombreEditable />
        <span className={`en-estado ${sucio ? "sucio" : ""}`} aria-live="polite">
          {s.guardando ? <><span className="girando" />Guardando…</> : sucio ? <><i />Cambios sin guardar</> : <>v{s.version} guardada</>}
        </span>
      </div>
      <span className="espacio" />
      <div className="en-grupo" role="group" aria-label="Deshacer y rehacer">
        <button className="boton-mini" disabled={!s.pasado.length && (sucio || s.version <= 1)} onClick={s.deshacer} aria-label="Deshacer" title={s.pasado.length ? "Deshacer (Ctrl+Z)" : "Deshacer lo último guardado, también lo de Vera (Ctrl+Z)"}><Icono n="deshacer" tam={15} /></button>
        <button className="boton-mini" disabled={!s.futuro.length && !(s.deshechoEnServidor && !sucio)} onClick={s.rehacer} aria-label="Rehacer" title="Rehacer (Ctrl+Y o Ctrl+Shift+Z)"><Icono n="rehacer" tam={15} /></button>
      </div>
      <div className="en-pop-ancla">
        <button className={`boton chico en-ia-boton ${pop === "ia" ? "activo" : ""}`} aria-expanded={pop === "ia"} title="IA del flujo: la global o una propia" onClick={() => setPop(pop === "ia" ? null : "ia")}><Icono n="chispa" tam={14} /><span className="etq">{rotuloIaFlujo(s.definicion.ia, s.definicion.modelo)}</span></button>
        <button className={`boton chico solo-ancho-editor ${pop === "versiones" ? "activo" : ""}`} aria-expanded={pop === "versiones"} onClick={() => setPop(pop === "versiones" ? null : "versiones")}><Icono n="historial" tam={14} />Versiones</button>
        <button className={`boton-mini ${pop === "mas" || pop === "borrar" ? "activo" : ""}`} aria-label="Más acciones" aria-expanded={pop === "mas"} title="Más acciones" onClick={() => setPop(pop ? null : "mas")}><Icono n="menu" tam={15} /></button>
        <AnimatePresence>
          {pop === "versiones" && <PanelVersiones key="v" cerrar={() => setPop(null)} />}
          {pop === "ia" && <PanelIaFlujo key="ia" cerrar={() => setPop(null)} />}
          {pop === "mas" && (
            <motion.div key="m" className="en-pop menu" role="menu" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}>
              <button role="menuitem" className="menu-opcion solo-movil-editor" onClick={() => setPop("versiones")}><span className="menu-icono"><Icono n="historial" tam={14} /></span>Versiones</button>
              <button role="menuitem" className="menu-opcion" onClick={() => { setPop(null); ordenar(); }}><span className="menu-icono"><Icono n="reordenar" tam={14} /></span>Ordenar automáticamente</button>
              <button role="menuitem" className="menu-opcion" onClick={() => { setPop(null); void s.validar().then((ok) => avisar(ok ? "El flujo es válido" : `${usarEditor.getState().problemas.length} problema(s) marcados en los nodos`, ok ? "ok" : "error")); }}><span className="menu-icono"><Icono n="escudo" tam={14} /></span>Validar sin guardar</button>
              {/* Renombrar, clonar y borrar están a la vista en la cabecera del flujo (AccionesFlujo). */}
            </motion.div>
          )}
          {pop === "borrar" && (
            <motion.div key="b" className="en-pop borrar" role="alertdialog" aria-label="Eliminar el flujo" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}>
              <p><b>¿Eliminar «{s.definicion.nombre}»?</b></p>
              <p className="chico texto-2">Se borra el flujo con sus {s.definicion.nodos.length} nodos. Las corridas que ya hizo se conservan, pero no se podrá volver a correr.</p>
              <div className="fila">
                <span className="espacio" />
                <button className="boton chico" autoFocus onClick={() => setPop(null)}>Cancelar</button>
                <button
                  className="boton chico peligro-solido"
                  disabled={borrando}
                  onClick={async () => {
                    setBorrando(true);
                    const nombre = s.definicion!.nombre;
                    try {
                      await s.borrar();
                      await cambiarFlujo("pqrd");
                      irA("tablero");
                      avisar(`«${nombre}» eliminado`);
                    } catch (e) {
                      avisar((e as Error).message, "error");
                    } finally {
                      setBorrando(false);
                      setPop(null);
                    }
                  }}
                >
                  {borrando && <span className="girando" />}Sí, eliminar
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <button className={`boton chico ${malos ? "con-problemas" : ""}`} disabled={s.guardando || (!sucio && !malos)} onClick={() => void guardar()} title="Validar y guardar (Ctrl+S)">
        {s.guardando || s.validando ? <span className="girando" /> : <Icono n={malos ? "alerta" : "guardar"} tam={14} />}
        <span className="etq">{malos ? `${malos} problema${malos > 1 ? "s" : ""}` : "Guardar"}</span>
      </button>
      <button className="boton primario chico" onClick={abrirCorrer} title="Correr el flujo"><Icono n="play" tam={13} /><span className="etq">Correr</span></button>
      <DialogoNombre
        abierto={duplicar}
        cerrar={() => setDuplicar(false)}
        titulo="Duplicar el flujo"
        sub="Una copia independiente, con sus nodos y su configuración guardada."
        inicial={`${s.definicion.nombre} (copia)`}
        accion="Duplicar y abrir"
        alAceptar={async (nombre) => {
          const id = await s.duplicar(nombre);
          await cambiarFlujo(id);
          avisar(`Copia «${nombre}» creada`);
        }}
      />
    </div>
  );
}

// ------------------------------------------------------------------ editor

type Menu = { x: number; y: number; opciones: Opcion[] } | null;
type Rapida = { x: number; y: number; donde: Posicion; desde?: { nodo: string; asa?: string } } | null;

export function EditorFlujo() {
  const flujoId = usar((s) => s.flujo);
  const foco = usar((s) => s.foco);
  const enfocar = usar((s) => s.enfocar);
  const objetivo = usar((s) => s.objetivo);
  const avisar = usar((s) => s.avisar);
  const st = usarEditor();
  const { definicion: def, tipos, tiposCargados, problemas, resaltados } = st;
  const rf = useReactFlow();
  const corrida = usarCorridaVisible();
  const resultados = useMemo(() => resultadosDe(corrida), [corrida]);
  const mapa = useMemo(() => new Map((tipos.length ? tipos : TIPOS_MINIMOS).map((t) => [t.tipo, t])), [tipos]);
  const [nodos, setNodos] = useState<Node<DatosNodo>[]>([]);
  const [aristaSel, setAristaSel] = useState<string | null>(null);
  const [menu, setMenu] = useState<Menu>(null);
  const [rapida, setRapida] = useState<Rapida>(null);
  const [paleta, setPaleta] = useState<{ abierta: boolean; q: string; n: number }>(() => ({ abierta: window.innerWidth >= 1600, q: "", n: 0 }));
  const [correr, setCorrer] = useState(false);
  const [aviso, setAviso] = useState<{ texto: string; x: number; y: number; n: number } | null>(null);
  const [resalteVivo, setResalteVivo] = useState<string[]>([]);
  const motivoRef = useRef<string | null>(null);
  const envoltura = useRef<HTMLDivElement>(null);
  const encuadrado = useRef<string | null>(null);
  const lejos = useStore((s) => s.transform[2] < 0.55);
  const seleccionado = foco?.tipo === "nodo" ? foco.id : null;

  // Abrir el flujo y los catálogos.
  useEffect(() => {
    if (flujoId && (st.flujo !== flujoId || !def)) void st.abrir(flujoId);
    if (!tiposCargados) void st.cargarTipos();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flujoId]);

  // Vera guardó por la API (o se guardó en otra pestaña): recargar si no hay cambios aquí.
  useEffect(() => {
    const oir = (e: Event) => {
      const d = (e as CustomEvent<{ flujo?: string; version?: number; entorno?: string }>).detail;
      const s = usarEditor.getState();
      if (!d || d.flujo !== s.flujo) return;
      // Cambió una variable de entorno: solo se refrescan las sugerencias {{entorno.*}}.
      if (d.entorno !== undefined && d.version === undefined) return void s.cargarEntorno(d.flujo);
      // Lo que guardamos desde aquí también llega por el canal: no es ajeno.
      if (s.guardando || s.renombrando) return;
      if (d.version !== undefined && d.version <= s.version) return;
      if (hayCambios(s)) usarEditor.setState({ versionAjena: d.version ?? s.version + 1 });
      else void s.recargar();
    };
    window.addEventListener("positiva:flujo-guardado", oir);
    return () => window.removeEventListener("positiva:flujo-guardado", oir);
  }, []);

  // Resaltado temporal pedido por Vera.
  useEffect(() => {
    if (!resaltados.ids.length) return;
    setResalteVivo(resaltados.ids);
    const t = setTimeout(() => setResalteVivo([]), 4200);
    return () => clearTimeout(t);
  }, [resaltados]);

  const disenio = useMemo(() => {
    if (!def) return {} as Record<string, Posicion>;
    const d = { ...(def.disenio ?? {}) };
    const faltan = def.nodos.filter((n) => !d[n.id]);
    if (faltan.length) {
      const auto = autoOrdenar(def, mapa);
      for (const n of faltan) d[n.id] = auto[n.id] ?? { x: 0, y: 0 };
    }
    return d;
  }, [def, mapa]);

  const problemasPorNodo = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of problemas) if (p.nodo) m[p.nodo] = (m[p.nodo] ?? 0) + 1;
    return m;
  }, [problemas]);

  // Los nodos del lienzo: se arman de la definición y conservan lo que mide React Flow.
  useEffect(() => {
    if (!def) return setNodos([]);
    setNodos((previos) => {
      const porId = new Map(previos.map((x) => [x.id, x]));
      return def.nodos.map((n) => {
        const p = porId.get(n.id);
        const tipo = mapa.get(n.tipo);
        const asas = asasDe(tipo, n.config);
        const usadas = new Set(def.aristas.filter((a) => a.desde === n.id).map((a) => asaDeArista(a, asas)));
        const data: DatosNodo = { n, tipo, asas, resumen: resumenConfig(n, tipo), problemas: problemasPorNodo[n.id] ?? 0, resultado: resultados[n.id], resaltado: resalteVivo.includes(n.id), usadas };
        return {
          ...(p ?? {}),
          id: n.id,
          type: "paso",
          position: p?.dragging ? p.position : (disenio[n.id] ?? { x: 0, y: 0 }),
          data,
          selected: n.id === seleccionado,
          style: { width: ANCHO_NODO },
        } as Node<DatosNodo>;
      });
    });
  }, [def, mapa, disenio, problemasPorNodo, resultados, resalteVivo, seleccionado]);

  const cables: Edge<DatosCable>[] = useMemo(() => {
    if (!def) return [];
    return def.aristas
      .filter((a) => def.nodos.some((n) => n.id === a.desde) && def.nodos.some((n) => n.id === a.hacia))
      .map((a, i) => {
        const o = def.nodos.find((n) => n.id === a.desde)!;
        const asas = asasDe(mapa.get(o.tipo), o.config);
        const asa = asaDeArista(a, asas);
        const error = asa === RAMA_ERROR;
        const ro = resultados[a.desde];
        const rd = resultados[a.hacia];
        const recorrido = Boolean(ro && ro.siguiente === a.hacia && rd);
        const estado: DatosCable["estado"] = recorrido ? (rd?.estado === "activo" ? "activo" : "recorrido") : "inactivo";
        const id = `${a.desde}|${i}|${a.hacia}`;
        const sel = aristaSel === id;
        const color = estado !== "inactivo" ? (estado === "activo" ? "#0094ff" : "#137a02") : sel ? "#015b9d" : error ? "#c73d2b" : "#a9b7cc";
        return {
          id,
          source: a.desde,
          target: a.hacia,
          sourceHandle: `s:${asas.some((x) => x.id === asa) || error ? asa : (asas[0]?.id ?? "*")}`,
          targetHandle: "e",
          type: "cable",
          selected: sel,
          zIndex: sel || estado !== "inactivo" ? 2 : 1,
          data: { cuando: asas.length > 1 || error || (a.cuando && !asas.some((x) => x.id === a.cuando)) ? (a.cuando ?? "") : "", error, estado, borrar: () => borrarArista(i) },
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color },
        };
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [def, mapa, resultados, aristaSel]);

  // ---------------------------------------------------------- acciones

  const avisarEn = useCallback((texto: string, x?: number, y?: number) => {
    const caja = envoltura.current?.getBoundingClientRect();
    setAviso({ texto, x: x ?? (caja ? caja.left + caja.width / 2 : 200), y: y ?? (caja ? caja.top + 80 : 120), n: Date.now() });
  }, []);
  useEffect(() => {
    if (!aviso) return;
    const t = setTimeout(() => setAviso(null), 3800);
    return () => clearTimeout(t);
  }, [aviso]);

  const borrarArista = useCallback((i: number) => {
    st.cambiar((d) => ({ ...d, aristas: d.aristas.filter((_, j) => j !== i) }));
    setAristaSel(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const borrarNodo = useCallback((id: string) => {
    const d = usarEditor.getState().definicion;
    const n = d?.nodos.find((x) => x.id === id);
    if (!d || !n) return;
    usarEditor.getState().cambiar((x) => {
      const disenio = { ...(x.disenio ?? {}) };
      delete disenio[id];
      return { ...x, nodos: x.nodos.filter((y) => y.id !== id), aristas: x.aristas.filter((a) => a.desde !== id && a.hacia !== id), disenio };
    });
    if (usar.getState().foco?.tipo === "nodo") usar.getState().enfocar(null);
    usarEditor.getState().limpiarProblemas(id);
    avisar(`«${n.nombre}» quitado. Ctrl+Z lo trae de vuelta`, "info");
  }, [avisar]);

  const duplicarNodo = useCallback((id: string) => {
    const d = usarEditor.getState().definicion;
    const n = d?.nodos.find((x) => x.id === id);
    if (!d || !n) return;
    if (mapa.get(n.tipo)?.disparador) return avisarEn("Un flujo arranca con un solo disparador: no se duplica");
    const nuevo: NodoDef = { ...structuredClone(n), id: idNuevo(d, n.id), nombre: `${n.nombre} (copia)` };
    const p = disenio[id] ?? { x: 0, y: 0 };
    usarEditor.getState().cambiar((x) => ({ ...x, nodos: [...x.nodos, nuevo], disenio: { ...disenio, ...(x.disenio ?? {}), [nuevo.id]: { x: p.x + 36, y: p.y + 120 } } }));
    enfocar({ tipo: "nodo", id: nuevo.id });
  }, [mapa, disenio, enfocar, avisarEn]);

  const alternarNodo = useCallback((id: string) => {
    usarEditor.getState().cambiar((x) => ({ ...x, nodos: x.nodos.map((n) => { if (n.id !== id) return n; const config = { ...n.config }; if (config.deshabilitado === true) delete config.deshabilitado; else config.deshabilitado = true; return { ...n, config }; }) }));
  }, []);

  const centrarEn = useCallback((id: string, zoom?: number) => {
    const n = rf.getNode(id);
    if (!n) return;
    const panel = document.querySelector(".hoja") as HTMLElement | null;
    const w = envoltura.current?.clientWidth ?? 1000;
    const tapado = panel && w > 760 ? panel.clientWidth / 2 : 0;
    const z = zoom ?? Math.max(rf.getZoom(), 0.9);
    void rf.setCenter(n.position.x + (n.measured?.width ?? ANCHO_NODO) / 2 + tapado / z, n.position.y + (n.measured?.height ?? 120) / 2, { zoom: z, duration: 450 });
  }, [rf]);

  const encuadrar = useCallback(() => void rf.fitView({ padding: 0.18, duration: 400, maxZoom: 1.05 }), [rf]);

  const ordenar = useCallback(() => {
    usarEditor.getState().cambiar((d) => ({ ...d, disenio: autoOrdenar(d, mapa) }));
    setTimeout(encuadrar, 60);
  }, [mapa, encuadrar]);

  /** Agrega un paso: donde se soltó, o después del nodo elegido (conectado por su primera salida libre). */
  const agregar = useCallback(
    (t: TipoNodo, donde?: Posicion, desde?: { nodo: string; asa?: string }) => {
      const d = usarEditor.getState().definicion;
      if (!d) return;
      if (t.disparador) {
        const ya = d.nodos.find((n) => mapa.get(n.tipo)?.disparador);
        if (ya) return avisarEn(`Este flujo ya arranca con «${ya.nombre}». Un flujo tiene un solo disparador: quita ese primero si quieres cambiarlo.`);
      }
      const id = idNuevo(d, t.tipo.replace(/^ia_/, ""));
      const usados = new Set(d.nodos.map((n) => n.nombre));
      let nombre = t.nombre;
      for (let i = 2; usados.has(nombre); i++) nombre = `${t.nombre} ${i}`;
      const nuevo: NodoDef = { id, tipo: t.tipo, nombre, config: configInicial(t) };
      const origen = desde?.nodo ?? (donde ? null : seleccionado);
      let arista: AristaDef | null = null;
      if (origen && !t.disparador) {
        const o = d.nodos.find((n) => n.id === origen);
        if (o) {
          const asas = asasDe(mapa.get(o.tipo), o.config);
          const libre = desde?.asa ?? asas.find((a) => !d.aristas.some((x) => x.desde === o.id && asaDeArista(x, asas) === a.id))?.id;
          if (libre) {
            const prueba: Definicion = { ...d, nodos: [...d.nodos, nuevo] };
            if (!motivoParaNoConectar(prueba, mapa, o.id, libre, id)) arista = { desde: o.id, hacia: id, ...(cuandoDeAsa(libre, asas) !== undefined ? { cuando: cuandoDeAsa(libre, asas) } : {}) };
          }
        }
      }
      const pos = donde ?? lugarDespues(d, disenio, origen);
      usarEditor.getState().cambiar((x) => ({ ...x, nodos: [...x.nodos, nuevo], aristas: arista ? [...x.aristas, arista] : x.aristas, disenio: { ...disenio, ...(x.disenio ?? {}), [id]: pos } }));
      enfocar({ tipo: "nodo", id });
      setRapida(null);
      if (window.innerWidth <= 900) setPaleta((p) => ({ ...p, abierta: false }));
      setTimeout(() => centrarEn(id), 80);
    },
    [mapa, disenio, seleccionado, enfocar, avisarEn, centrarEn],
  );

  // ---------------------------------------------------------- conexiones

  const motivo = useCallback((c: Connection | Edge): string | null => {
    const d = usarEditor.getState().definicion;
    if (!d || !c.source || !c.target) return "Conexión incompleta";
    const asa = String(c.sourceHandle ?? "s:*").replace(/^s:/, "");
    return motivoParaNoConectar(d, mapa, c.source, asa, c.target);
  }, [mapa]);

  const esValida = useCallback((c: Connection | Edge) => {
    const m = motivo(c);
    motivoRef.current = m;
    return m === null;
  }, [motivo]);

  const alConectar = useCallback((c: Connection) => {
    const m = motivo(c);
    if (m) return avisarEn(m);
    const d = usarEditor.getState().definicion!;
    const o = d.nodos.find((n) => n.id === c.source)!;
    const asas = asasDe(mapa.get(o.tipo), o.config);
    const asa = String(c.sourceHandle ?? "s:*").replace(/^s:/, "");
    const cuando = cuandoDeAsa(asa, asas);
    usarEditor.getState().cambiar((x) => ({ ...x, aristas: [...x.aristas, { desde: c.source!, hacia: c.target!, ...(cuando !== undefined ? { cuando } : {}) }] }));
  }, [motivo, mapa, avisarEn]);

  const alSoltarCable = useCallback((e: MouseEvent | TouchEvent, estado: FinalConnectionState) => {
    const p = "changedTouches" in e ? e.changedTouches[0]! : e;
    if (estado.isValid) return;
    // Soltado sobre una ficha sin asa de entrada (un disparador): también se explica.
    const sobre = estado.toNode?.id ?? (document.elementFromPoint(p.clientX, p.clientY) as HTMLElement | null)?.closest?.(".react-flow__node")?.getAttribute("data-id");
    if (sobre && estado.fromNode) {
      const m = (estado.toNode ? motivoRef.current : null) ?? motivoParaNoConectar(usarEditor.getState().definicion!, mapa, estado.fromNode.id, String(estado.fromHandle?.id ?? "s:*").replace(/^s:/, ""), sobre);
      if (m) avisarEn(m, p.clientX, p.clientY);
      return;
    }
    // Soltar en el vacío: búsqueda rápida para agregar un paso conectado a esa salida.
    if (estado.fromNode && !estado.toNode && (e.target as HTMLElement | null)?.closest?.(".react-flow__pane")) {
      const asa = String(estado.fromHandle?.id ?? "s:*").replace(/^s:/, "");
      setRapida({ x: p.clientX, y: p.clientY, donde: rf.screenToFlowPosition({ x: p.clientX, y: p.clientY - 30 }), desde: { nodo: estado.fromNode.id, asa } });
    }
  }, [mapa, avisarEn, rf]);

  // ---------------------------------------------------------- arrastre

  const alCambiarNodos = useCallback((cambios: NodeChange<Node<DatosNodo>>[]) => {
    // La selección la maneja el foco; lo demás (medidas, posición en arrastre) se aplica aquí.
    const utiles = cambios.filter((c) => c.type !== "select" && c.type !== "remove");
    if (utiles.length) setNodos((ns) => applyNodeChanges(utiles, ns));
  }, []);

  const alSoltarNodo = useCallback((_: unknown, __: Node, movidos: Node[]) => {
    const d = usarEditor.getState().definicion;
    if (!d) return;
    const cambiados = movidos.filter((m) => {
      const p = disenio[m.id];
      return !p || Math.abs(p.x - m.position.x) > 0.5 || Math.abs(p.y - m.position.y) > 0.5;
    });
    if (!cambiados.length) return;
    usarEditor.getState().cambiar((x) => ({ ...x, disenio: { ...disenio, ...(x.disenio ?? {}), ...Object.fromEntries(cambiados.map((m) => [m.id, { x: Math.round(m.position.x), y: Math.round(m.position.y) }])) } }));
  }, [disenio]);

  // ---------------------------------------------------------- teclado

  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const enCampo = Boolean(t.closest("input, textarea, select, [contenteditable], .cm-editor, [role=dialog]"));
      const ctrl = e.ctrlKey || e.metaKey;
      const k = e.key.toLowerCase();
      if (ctrl && k === "s") {
        e.preventDefault();
        void usarEditor.getState().guardar().then((ok) => avisar(ok ? `Guardado como versión ${usarEditor.getState().version}` : "No se guardó: revisa los problemas marcados", ok ? "ok" : "error"));
        return;
      }
      if (enCampo) return;
      if (ctrl && k === "z" && !e.shiftKey) { e.preventDefault(); usarEditor.getState().deshacer(); return; }
      if ((ctrl && k === "z" && e.shiftKey) || (ctrl && k === "y")) { e.preventDefault(); usarEditor.getState().rehacer(); return; }
      if (ctrl && k === "d" && seleccionado) { e.preventDefault(); duplicarNodo(seleccionado); return; }
      if (e.key === "Delete" || e.key === "Backspace") {
        if (aristaSel) {
          const i = Number(aristaSel.split("|")[1]);
          e.preventDefault();
          borrarArista(i);
        } else if (seleccionado) {
          e.preventDefault();
          borrarNodo(seleccionado);
        }
        return;
      }
      if (e.key === "/" && !ctrl) {
        e.preventDefault();
        const b = envoltura.current?.getBoundingClientRect();
        if (b) setRapida({ x: b.left + b.width / 2 - 160, y: b.top + 90, donde: rf.screenToFlowPosition({ x: b.left + b.width / 2, y: b.top + b.height / 2 }), ...(seleccionado ? { desde: { nodo: seleccionado } } : {}) });
        return;
      }
      if (k === "f" && !ctrl) {
        if (seleccionado) centrarEn(seleccionado, 1.05);
        else encuadrar();
      }
      if (e.key === "Escape") setAristaSel(null);
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [seleccionado, aristaSel, borrarArista, borrarNodo, duplicarNodo, centrarEn, encuadrar, avisar, rf]);

  // ---------------------------------------------------------- cámara

  useEffect(() => {
    if (!def || !nodos.length || encuadrado.current === `${def.id}`) return;
    if (!nodos.every((n) => n.measured?.width)) return;
    encuadrado.current = def.id;
    // En un teléfono se arranca legible (por el disparador), no con todo diminuto.
    const angosto = (envoltura.current?.clientWidth ?? 1000) < 600;
    setTimeout(() => void rf.fitView({ padding: 0.2, maxZoom: 1, minZoom: angosto ? 0.62 : 0.2, duration: 0, ...(angosto ? { nodes: def.nodos.slice(0, 2).map((n) => ({ id: n.id })) } : {}) }), 30);
  }, [def, nodos, rf]);

  useEffect(() => {
    if (!objetivo) return;
    const t = setTimeout(() => centrarEn(objetivo.id), 80);
    return () => clearTimeout(t);
  }, [objetivo, centrarEn]);

  // ---------------------------------------------------------- Vera

  const control = useRef({ centrarEn, encuadrar, ordenar, rf });
  control.current = { centrarEn, encuadrar, ordenar, rf };
  useEffect(() => {
    const oir = (e: Event) => {
      const d = (e as CustomEvent<Record<string, unknown>>).detail;
      if (!d) return;
      const c = control.current;
      if (d.accion === "lienzo") {
        if (d.modo === "ver_todo") c.encuadrar();
        else if (d.modo === "acercar") void c.rf.zoomIn({ duration: 250 });
        else if (d.modo === "alejar") void c.rf.zoomOut({ duration: 250 });
        else if (d.modo === "restablecer") void c.rf.fitView({ padding: 0.2, maxZoom: 1, duration: 400 });
        return;
      }
      if (d.accion !== "editor") return;
      const nodo = typeof d.nodo === "string" ? d.nodo : "";
      const existe = (id: string) => Boolean(usarEditor.getState().definicion?.nodos.some((n) => n.id === id));
      switch (d.op) {
        case "seleccionar_nodo":
          if (!existe(nodo)) return;
          usar.getState().enfocar({ tipo: "nodo", id: nodo });
          setTimeout(() => c.centrarEn(nodo), 120);
          break;
        case "abrir_panel_nodo":
          if (!existe(nodo)) return;
          usar.getState().enfocar({ tipo: "nodo", id: nodo });
          if (typeof d.seccion === "string") setTimeout(() => usarEditor.getState().pedirSeccion(String(d.seccion)), 60);
          setTimeout(() => c.centrarEn(nodo), 120);
          break;
        case "cerrar_panel":
          usar.getState().enfocar(null);
          break;
        case "zoom_nodo":
          if (existe(nodo)) c.centrarEn(nodo, 1.25);
          break;
        case "zoom": {
          const nivel = Math.max(0.2, Math.min(2, Number(d.nivel) || 1));
          void c.rf.zoomTo(nivel, { duration: 300 });
          break;
        }
        case "auto_ordenar":
          c.ordenar();
          break;
        case "resaltar":
          if (Array.isArray(d.nodos)) usarEditor.getState().resaltar(d.nodos.filter((x): x is string => typeof x === "string"));
          break;
        case "recargar":
          void usarEditor.getState().recargar();
          break;
        case "abrir_paleta":
          setPaleta((p) => ({ abierta: true, q: typeof d.busqueda === "string" ? d.busqueda : "", n: p.n + 1 }));
          break;
        case "abrir_correr":
          setCorrer(true);
          break;
      }
    };
    window.addEventListener("positiva:control", oir);
    return () => window.removeEventListener("positiva:control", oir);
  }, []);

  // ---------------------------------------------------------- menús

  const menuDeNodo = (e: React.MouseEvent, n: Node<DatosNodo>) => {
    e.preventDefault();
    const nd = n.data.n;
    const disparador = Boolean(n.data.tipo?.disparador);
    setMenu({
      x: e.clientX,
      y: e.clientY,
      opciones: [
        { titulo: nd.nombre },
        { etiqueta: "Abrir y configurar", icono: "ajustes", atajo: "Clic", alHacer: () => enfocar({ tipo: "nodo", id: nd.id }) },
        { etiqueta: "Agregar un paso después", icono: "mas", alHacer: () => { const b = (e.target as HTMLElement).getBoundingClientRect(); setRapida({ x: b.right + 10, y: b.top, donde: lugarDespues(def!, disenio, nd.id), desde: { nodo: nd.id } }); } },
        { etiqueta: "Enfocar", icono: "mira", atajo: "F", alHacer: () => centrarEn(nd.id, 1.05) },
        { separador: true },
        { etiqueta: nd.config.deshabilitado === true ? "Habilitar" : "Deshabilitar", icono: nd.config.deshabilitado === true ? "play" : "pausa", deshabilitado: disparador || nd.tipo === "salida", pista: disparador ? "Un disparador no se deshabilita" : undefined, alHacer: () => alternarNodo(nd.id) },
        { etiqueta: "Duplicar", icono: "copiar", atajo: "Ctrl D", deshabilitado: disparador, alHacer: () => duplicarNodo(nd.id) },
        { etiqueta: "Ver lo que dejó", icono: "datos", deshabilitado: !resultados[nd.id], alHacer: () => { enfocar({ tipo: "nodo", id: nd.id }); setTimeout(() => usarEditor.getState().pedirSeccion("datos"), 60); } },
        { separador: true },
        { etiqueta: "Quitar del flujo", icono: "papelera", atajo: "Supr", peligro: true, alHacer: () => borrarNodo(nd.id) },
      ],
    });
  };

  const menuDeCable = (e: React.MouseEvent, c: Edge) => {
    e.preventDefault();
    setAristaSel(c.id);
    const i = Number(c.id.split("|")[1]);
    setMenu({ x: e.clientX, y: e.clientY, opciones: [{ etiqueta: "Quitar esta conexión", icono: "papelera", atajo: "Supr", peligro: true, alHacer: () => borrarArista(i) }] });
  };

  const menuDelLienzo = (e: React.MouseEvent | MouseEvent) => {
    e.preventDefault();
    const donde = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    setMenu({
      x: e.clientX,
      y: e.clientY,
      opciones: [
        { titulo: "Lienzo" },
        { etiqueta: "Agregar un paso aquí", icono: "mas", atajo: "Doble clic", alHacer: () => setRapida({ x: e.clientX, y: e.clientY, donde }) },
        { etiqueta: "Ordenar automáticamente", icono: "reordenar", alHacer: ordenar },
        { etiqueta: "Encuadrar todo", icono: "encuadrar", atajo: "F", alHacer: encuadrar },
        { separador: true },
        { etiqueta: "Deshacer", icono: "deshacer", atajo: "Ctrl Z", deshabilitado: !st.pasado.length && (hayCambios(st) || st.version <= 1), alHacer: st.deshacer },
        { etiqueta: "Rehacer", icono: "rehacer", atajo: "Ctrl Y", deshabilitado: !st.futuro.length && !st.deshechoEnServidor, alHacer: st.rehacer },
      ],
    });
  };

  // ---------------------------------------------------------- vista

  if (st.error && !def) {
    return (
      <div className="vacio grande">
        <Icono n="alerta" tam={26} />
        <b>No se pudo abrir el flujo.</b>
        <span>{st.error}</span>
        <button className="boton" onClick={() => flujoId && void st.abrir(flujoId)}>Volver a intentar</button>
      </div>
    );
  }
  if (!def) return <div className="vacio"><span className="girando" />Abriendo el flujo…</div>;

  const generales = problemas.filter((p) => !p.nodo);

  return (
    <div className={`editor-flujo ${paleta.abierta ? "con-paleta" : ""}`}>
      <BarraEditor abrirPaleta={() => setPaleta((p) => ({ abierta: !p.abierta, q: "", n: p.n + 1 }))} paletaAbierta={paleta.abierta} abrirCorrer={() => setCorrer(true)} ordenar={ordenar} />
      {st.versionAjena !== null && (
        <div className="en-franja" role="status">
          <Icono n="info" tam={14} />
          <span className="crece">Se guardó la versión {st.versionAjena} de este flujo desde otro lado (quizá Vera) mientras editabas.</span>
          <button className="boton chico" onClick={() => usarEditor.setState({ versionAjena: null })}>Seguir con lo mío</button>
          <button className="boton chico primario" onClick={() => void st.recargar()}>Cargar la versión {st.versionAjena}</button>
        </div>
      )}
      {generales.length > 0 && (
        <div className="en-franja error" role="alert">
          <Icono n="alerta" tam={14} />
          <ul className="crece">{generales.map((p, i) => <li key={i}>{p.texto}</li>)}</ul>
          <button className="boton-mini" aria-label="Ocultar" onClick={() => st.limpiarProblemas()}><Icono n="cerrar" tam={13} /></button>
        </div>
      )}
      <div className="en-cuerpo">
        {!paleta.abierta && (
          <button className="en-fab" aria-label="Agregar un paso" onClick={() => setPaleta((p) => ({ abierta: true, q: "", n: p.n + 1 }))}><Icono n="mas" tam={20} /></button>
        )}
        <AnimatePresence>
          {paleta.abierta && <Paleta key="paleta" tipos={tipos} cargados={tiposCargados} elegir={(t) => agregar(t)} cerrar={() => setPaleta((p) => ({ ...p, abierta: false }))} busquedaInicial={paleta.q} n={paleta.n} />}
        </AnimatePresence>
        <div
          ref={envoltura}
          className="en-lienzo"
          onDragOver={(e) => {
            if (e.dataTransfer.types.includes("application/x-positiva-tipo")) {
              e.preventDefault();
              e.dataTransfer.dropEffect = "copy";
            }
          }}
          onDrop={(e) => {
            const tipo = e.dataTransfer.getData("application/x-positiva-tipo");
            const t = mapa.get(tipo);
            if (!t) return;
            e.preventDefault();
            const p = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
            agregar(t, { x: Math.round(p.x - ANCHO_NODO / 2), y: Math.round(p.y - 30) });
          }}
          onDoubleClick={(e) => {
            if (!(e.target as HTMLElement).closest(".react-flow__pane")) return;
            setRapida({ x: e.clientX, y: e.clientY, donde: rf.screenToFlowPosition({ x: e.clientX - ANCHO_NODO / 2, y: e.clientY - 30 }) });
          }}
        >
          <ReactFlow
            nodes={nodos}
            edges={cables}
            nodeTypes={tiposNodo}
            edgeTypes={tiposCable}
            onNodesChange={alCambiarNodos}
            onNodeDragStop={alSoltarNodo}
            onConnect={alConectar}
            onConnectEnd={alSoltarCable}
            isValidConnection={esValida}
            onNodeClick={(_, n) => { setMenu(null); setAristaSel(null); (document.activeElement as HTMLElement | null)?.blur?.(); enfocar({ tipo: "nodo", id: n.id }); }}
            onEdgeClick={(_, c) => { setMenu(null); setAristaSel(c.id); (document.activeElement as HTMLElement | null)?.blur?.(); }}
            onNodeContextMenu={menuDeNodo}
            onEdgeContextMenu={menuDeCable}
            onPaneContextMenu={menuDelLienzo}
            onPaneClick={() => { setMenu(null); setAristaSel(null); if (foco) enfocar(null); }}
            zoomOnDoubleClick={false}
            deleteKeyCode={null}
            selectionKeyCode={null}
            multiSelectionKeyCode={null}
            connectionRadius={28}
            minZoom={0.2}
            maxZoom={2}
            snapToGrid
            snapGrid={[8, 8]}
            proOptions={{ hideAttribution: true }}
            className={lejos ? "lejos" : ""}
            connectionLineStyle={{ stroke: "#0094ff", strokeWidth: 2 }}
          >
            {!def.nodos.some((n) => mapa.get(n.tipo)?.disparador || n.tipo.startsWith("entrada")) && def.nodos.length > 0 && tiposCargados && (
              <Panel position="top-center" className="en-pista"><Icono n="info" tam={13} />Este flujo no tiene disparador: agrega uno desde la paleta para que pueda correr.</Panel>
            )}
            <Panel position="bottom-left" className="controles">
              <button onClick={() => void rf.zoomIn({ duration: 200 })} title="Acercar" aria-label="Acercar"><Icono n="mas" tam={15} /></button>
              <button onClick={() => void rf.zoomOut({ duration: 200 })} title="Alejar" aria-label="Alejar"><Icono n="menos" tam={15} /></button>
              <i />
              <button onClick={encuadrar} title="Encuadrar todo (F)" aria-label="Encuadrar todo"><Icono n="encuadrar" tam={15} /></button>
              <button onClick={ordenar} title="Ordenar automáticamente" aria-label="Ordenar automáticamente"><Icono n="reordenar" tam={15} /></button>
            </Panel>
            <MiniMap pannable zoomable position="bottom-right" style={{ width: 160, height: 100 }} nodeBorderRadius={8} nodeColor={(n) => ((n.data as DatosNodo).problemas ? "#f8d2cc" : (n.data as DatosNodo).resultado?.estado === "hecho" ? "#c8ecc1" : "#cfe3f7")} maskColor="rgba(232,241,249,0.72)" />
          </ReactFlow>
          {def.nodos.length === 0 && (
            <div className="en-vacio">
              <b>Un flujo vacío.</b>
              <span>Empieza por un disparador: abre la paleta o haz doble clic en el lienzo.</span>
            </div>
          )}
        </div>
      </div>
      {rapida && <BusquedaRapida x={rapida.x} y={rapida.y} tipos={tipos.filter((t) => !rapida.desde || !t.disparador)} titulo={rapida.desde ? `Conectar después de «${def.nodos.find((n) => n.id === rapida.desde!.nodo)?.nombre ?? ""}»` : undefined} elegir={(t) => agregar(t, rapida.donde, rapida.desde)} cerrar={() => setRapida(null)} />}
      {menu && <MenuContextual x={menu.x} y={menu.y} opciones={menu.opciones} cerrar={() => setMenu(null)} />}
      {aviso &&
        createPortal(
          <motion.div key={aviso.n} className="en-aviso" role="alert" style={{ left: Math.max(12, Math.min(aviso.x - 160, window.innerWidth - 332)), top: Math.max(12, aviso.y + 14) }} initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}>
            <Icono n="alerta" tam={14} />
            <span>{aviso.texto}</span>
          </motion.div>,
          document.body,
        )}
      <DialogoCorrer abierto={correr} cerrar={() => setCorrer(false)} />
    </div>
  );
}
