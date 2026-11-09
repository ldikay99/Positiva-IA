import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  getSmoothStepPath,
  useReactFlow,
  useStore,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AnimatePresence, motion } from "motion/react";
import { CLAVE_POSICIONES, guardarLocal, leerLocal, usar } from "../estado.ts";
import { ACCIONES, ANCLA_DEL_PUNTO, CATEGORIAS, FASES, FASE_DEL_FINAL, PUNTOS, categoria, fraseDeRegla, type Categoria } from "../flujo.ts";
import { Icono } from "../iconos.tsx";
import { ajustado, conValor } from "../parametros.ts";
import { NOMBRES_DESENLACE, TONO_DESENLACE, motivoLegible, progreso, segundos, type Progreso } from "../progreso.ts";
import type { Catalogo, Corrida, EstadoNodo, Nodo as NodoCatalogo, Regla } from "../tipos.ts";
import { MenuContextual, type Opcion } from "./MenuContextual.tsx";
import { lectura, urlArchivo } from "../adjuntos.ts";
import "./adjuntos.css";
import { EditorFlujo } from "./Editor.tsx";

/** Los flujos declarativos se editan; los de código (PQRD, Tutelas) se observan y se ajustan. */
export function Lienzo() {
  const declarativo = usar((s) => s.flujos.find((f) => f.id === s.flujo)?.tipo === "declarativo");
  return declarativo ? <EditorFlujo /> : <LienzoDeCodigo />;
}

const ANCHO = 248;
const ANCHO_FIN = 204;
const COLUMNA = 330;
const FILA = 150;
const CABEZA_FASE = 58;

type Posicion = { x: number; y: number };

interface DatosEtapa extends Record<string, unknown> {
  nodo: NodoCatalogo;
  cat: Categoria;
  estado: EstadoNodo;
  ms: number;
  veces: number;
  extra: string[];
  ajustes: number;
  modelo: string;
  /** Lo que el motor va leyendo, sobre el nodo de conversión. */
  lectura?: VistaLectura;
}
interface VistaLectura {
  /** La hoja en curso o la última leída, en grande. */
  foco: { img: string; documento: string; detalle: string; leyendo: boolean } | null;
  /** Miniaturas de las últimas hojas: url y método. */
  hojas: { src: string; metodo: string }[];
  mas: number;
  total: number;
}

const NOMBRE_METODO: Record<string, string> = { texto: "texto propio del PDF", ocr: "OCR", vision: "visión IA", ilegible: "sin texto legible" };

const LecturaEnNodo = memo(function LecturaEnNodo({ v }: { v: VistaLectura }) {
  const irA = usar((s) => s.irA);
  return (
    <button
      type="button"
      className="nodo-lectura nodrag"
      onClick={(e) => {
        e.stopPropagation();
        irA("adjuntos");
      }}
      title="Ver cada hoja en la vista de adjuntos"
    >
      {v.foco && (
        <span className={`nodo-lectura-foco ${v.foco.leyendo ? "leyendo" : ""}`}>
          {v.foco.img ? <img src={v.foco.img} alt="" draggable={false} /> : null}
          {v.foco.leyendo && <span className="escaneo" />}
          <span className="nodo-lectura-pie">
            <span className="nodo-lectura-doc">{v.foco.documento}</span>
            <span>{v.foco.detalle}{v.total > 1 ? ` · ${v.total} hojas en total` : ""}</span>
          </span>
        </span>
      )}
      {v.hojas.length > 0 && (
        <span className="nodo-hojas">
          {v.hojas.map((h) => (
            <motion.span key={h.src} className={`nodo-hoja m-${h.metodo}`} initial={{ opacity: 0, y: 6, scale: 0.8 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ type: "spring", stiffness: 500, damping: 30 }}>
              <img src={h.src} alt="" draggable={false} />
            </motion.span>
          ))}
          {v.mas ? <span className="nodo-hojas-mas">+{v.mas}</span> : null}
        </span>
      )}
    </button>
  );
});
interface DatosRegla extends Record<string, unknown> {
  regla: Regla;
  borrador: boolean;
  resultado: "" | "cumple" | "no";
}
interface DatosFase extends Record<string, unknown> {
  nombre: string;
  numero: string;
  ancho: number;
  alto: number;
  pista?: string;
}
interface DatosExtremo extends Record<string, unknown> {
  tipo: "inicio" | "fin";
  corrida?: Corrida;
  asunto?: string;
}
interface DatosNota extends Record<string, unknown> {
  id: string;
  texto: string;
}
interface DatosCable extends Record<string, unknown> {
  estado: "inactivo" | "recorrido" | "activo";
  clase: "principal" | "desvio" | "regla";
  etiqueta?: string;
  resaltado: boolean;
  /** Altura del pasillo por donde va un desvío; sin ella el cable es escalonado normal. */
  carril?: number;
}

// ------------------------------------------------------------------- nodos

function Asas() {
  return (
    <>
      {(["l", "r", "t", "b"] as const).map((lado) => {
        const pos = { l: Position.Left, r: Position.Right, t: Position.Top, b: Position.Bottom }[lado];
        return (
          <span key={lado}>
            <Handle id={`e-${lado}`} type="target" position={pos} isConnectable={false} />
            <Handle id={`s-${lado}`} type="source" position={pos} isConnectable={false} />
          </span>
        );
      })}
    </>
  );
}

function Insignia({ estado }: { estado: EstadoNodo }) {
  if (estado === "activo") return <span className="insignia activo" title="En curso"><span className="girando" /></span>;
  if (estado === "hecho") return <span className="insignia hecho" title="Recorrida"><Icono n="ok" tam={11} strokeWidth={2.6} /></span>;
  if (estado === "fallo") return <span className="insignia fallo" title="Falló"><Icono n="cerrar" tam={11} strokeWidth={2.6} /></span>;
  return null;
}

const NodoEtapa = memo(function NodoEtapa({ data, selected }: NodeProps<Node<DatosEtapa>>) {
  const n = data.nodo;
  const c = CATEGORIAS[data.cat];
  return (
    <div className={`nodo t-${c.tono} ${data.estado} ${selected ? "sel" : ""} ${n.deshabilitado ? "apagado" : ""} ${n.insertado ? "insertado" : ""}`}>
      <Asas />
      <div className="nodo-cabeza">
        <span className="nodo-icono"><Icono n={c.icono} tam={15} /></span>
        <span className="nodo-cat">{n.insertado ? `Paso del flujo · ${n.insertado.nombre_punto}` : c.nombre}</span>
        <Insignia estado={data.estado} />
      </div>
      <div className="nodo-titulo">{n.nombre}</div>
      <div className="nodo-desc">{n.descripcion}</div>
      {data.lectura && (data.lectura.foco || data.lectura.hojas.length > 0) && <LecturaEnNodo v={data.lectura} />}
      <div className="nodo-pie">
        {(data.cat === "ia" || data.cat === "vision" || data.cat === "correccion") && data.modelo && <span className="dato ia"><Icono n="chispa" tam={10} />{data.modelo}</span>}
        {n.deshabilitado && <span className="dato tenue">Apagada en este flujo</span>}
        {!!n.config?.lectores && <span className="dato ajustado">Lectores propios</span>}
        {typeof n.config?.max_imagenes === "number" && <span className="dato ajustado">Visión: {String(n.config.max_imagenes)} por correo</span>}
        {data.ms > 0 && <span className="dato"><Icono n="reloj" tam={10} />{segundos(data.ms)}</span>}
        {data.veces > 1 && <span className="dato">×{data.veces}</span>}
        {data.extra.map((x) => <span key={x} className="dato">{x}</span>)}
        {data.ajustes > 0 && <span className="dato ajustado">{data.ajustes} ajuste{data.ajustes > 1 ? "s" : ""}</span>}
        {data.ms === 0 && data.extra.length === 0 && data.ajustes === 0 && (n.reglas?.length ?? 0) > 0 && <span className="dato tenue">{n.reglas!.length} reglas · {n.parametros.filter((p) => p.ajuste !== null).length} parámetros</span>}
      </div>
    </div>
  );
});

const NodoFinal = memo(function NodoFinal({ data, selected }: NodeProps<Node<DatosEtapa>>) {
  const alcanzado = data.estado !== "inactivo";
  return (
    <div className={`nodo-final ${alcanzado ? "alcanzado" : ""} ${selected ? "sel" : ""}`}>
      <Asas />
      <Icono n="bandera" tam={14} />
      <span className="crece">{data.nodo.nombre}</span>
    </div>
  );
});

const NodoExtremo = memo(function NodoExtremo({ data, selected }: NodeProps<Node<DatosExtremo>>) {
  const c = data.corrida;
  if (data.tipo === "inicio") {
    return (
      <div className={`nodo-extremo inicio ${c ? "con-correo" : ""} ${selected ? "sel" : ""}`}>
        <Asas />
        <span className="extremo-icono"><Icono n="correo" tam={16} /></span>
        <div className="crece">
          <span className="nodo-cat">Correo entrante</span>
          <div className="extremo-titulo">{c ? c.caso : "Ningún correo en proceso"}</div>
          <div className="nodo-desc">{c ? data.asunto || "(sin asunto)" : "Elige correos en la correspondencia y pulsa «Correr»"}</div>
        </div>
      </div>
    );
  }
  const r = c?.resultado;
  const accion = r ? String(r.accion) : "";
  const tono = r ? (TONO_DESENLACE[accion] ?? "gris") : c ? "azul" : "gris";
  return (
    <div className={`nodo-extremo fin tono-${tono} ${selected ? "sel" : ""}`}>
      <Asas />
      <span className="extremo-icono">{c && !r ? <span className="girando" /> : <Icono n={accion === "radicado" ? "ok" : r ? "bandera" : "bandera"} tam={16} />}</span>
      <div className="crece">
        <span className="nodo-cat">Fin del correo · desenlace</span>
        <div className="extremo-titulo">{r ? (NOMBRES_DESENLACE[accion] ?? accion) : c ? "Procesando…" : "Esperando una corrida"}</div>
        <div className="nodo-desc">
          {r ? (accion === "radicado" ? `Radicado ${String(r.radicado ?? "")} · ${String(r.segundos ?? "")} s` : String(r.motivo || r.razon_del_reenvio || r.razon_del_duplicado || "")) : "Aquí termina cada correo: radicado, a revisión manual, duplicado o reenviado"}
        </div>
      </div>
    </div>
  );
});

const NodoRegla = memo(function NodoRegla({ data, selected }: NodeProps<Node<DatosRegla>>) {
  const r = data.regla;
  return (
    <div className={`nodo t-rosa regla ${!r.activa ? "inactiva" : ""} ${data.resultado === "cumple" ? "cumplida" : ""} ${selected ? "sel" : ""}`}>
      <Asas />
      <div className="nodo-cabeza">
        <span className="nodo-icono"><Icono n="regla" tam={15} /></span>
        <span className="nodo-cat">Regla · {PUNTOS[r.punto].nombre.toLowerCase()}</span>
        {data.borrador ? <span className="dato ajustado">borrador</span> : !r.activa ? <span className="dato tenue">inactiva</span> : null}
      </div>
      <div className="nodo-titulo">{r.nombre}</div>
      <div className="nodo-desc mono">{fraseDeRegla(r)}</div>
      <div className="nodo-pie">
        <span className={`dato ${r.accion === "manual" ? "rojo" : ""}`}>{ACCIONES[r.accion]}</span>
        {data.resultado === "cumple" && <span className="dato cumple"><Icono n="rayo" tam={10} />se cumplió</span>}
        {data.resultado === "no" && <span className="dato tenue">no aplicó</span>}
      </div>
    </div>
  );
});

const NodoFase = memo(function NodoFase({ data }: NodeProps<Node<DatosFase>>) {
  return (
    <div className="fase" style={{ width: data.ancho, height: data.alto }}>
      <div className="fase-cabeza">
        {data.numero && <span className="fase-numero">{data.numero}</span>}
        <span className="fase-nombre">{data.nombre}</span>
        {data.pista && <span className="fase-pista">{data.pista}</span>}
      </div>
    </div>
  );
});

const NodoNota = memo(function NodoNota({ data, selected }: NodeProps<Node<DatosNota>>) {
  const cambiar = usar((s) => s.cambiarNota);
  const borrar = usar((s) => s.borrarNota);
  return (
    <div className={`nota ${selected ? "sel" : ""}`}>
      <div className="nota-cabeza">
        <Icono n="nota" tam={13} />
        <span className="crece">Nota</span>
        <button className="nodrag" aria-label="Borrar nota" onClick={() => borrar(data.id)}><Icono n="cerrar" tam={12} /></button>
      </div>
      <textarea className="nodrag nowheel" value={data.texto} placeholder="Escribe una nota…" onChange={(e) => cambiar(data.id, e.target.value)} />
    </div>
  );
});

// ------------------------------------------------------------------- cables

/** Polilínea ortogonal con esquinas redondeadas. */
function redondeada(puntos: [number, number][], radio = 12): string {
  const pts = puntos.filter((p, i) => i === 0 || p[0] !== puntos[i - 1]![0] || p[1] !== puntos[i - 1]![1]);
  let d = `M ${pts[0]![0]} ${pts[0]![1]}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i - 1]!;
    const [x1, y1] = pts[i]!;
    const [x2, y2] = pts[i + 1]!;
    const d1 = Math.hypot(x1 - x0, y1 - y0);
    const d2 = Math.hypot(x2 - x1, y2 - y1);
    const r = Math.min(radio, d1 / 2, d2 / 2);
    if (r < 0.5) {
      d += ` L ${x1} ${y1}`;
      continue;
    }
    d += ` L ${x1 - ((x1 - x0) / d1) * r} ${y1 - ((y1 - y0) / d1) * r} Q ${x1} ${y1} ${x1 + ((x2 - x1) / d2) * r} ${y1 + ((y2 - y1) / d2) * r}`;
  }
  const ultimo = pts[pts.length - 1]!;
  return `${d} L ${ultimo[0]} ${ultimo[1]}`;
}

/** Recorrido por los pasillos: sale al hueco entre fases, baja o sube al carril y entra al destino. */
function porPasillo(sx: number, sy: number, sp: Position, tx: number, ty: number, tp: Position, carril: number): [string, number, number] {
  const pts: [number, number][] = [[sx, sy]];
  if (sp === Position.Right) pts.push([sx + 40, sy], [sx + 40, carril]);
  else pts.push([sx, carril]);
  const desde = pts[pts.length - 1]![0];
  if (tp === Position.Left) pts.push([tx - 40, carril], [tx - 40, ty]);
  else pts.push([tx, carril]);
  pts.push([tx, ty]);
  const hasta = tp === Position.Left ? tx - 40 : tx;
  return [redondeada(pts, 14), (desde + hasta) / 2, carril];
}

const COLOR_FLECHA = { inactivo: "#bcc8da", recorrido: "#4aa83f", activo: "#0094ff", resaltado: "#5cb8ff", regla: "#5a7fbf" };

/** Cable con flecha; mientras la corriente pasa, unas chispas recorren el trazo. */
const Cable = memo(function Cable({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd }: EdgeProps<Edge<DatosCable>>) {
  const [ruta, lx, ly] =
    data?.carril !== undefined
      ? porPasillo(sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition, data.carril)
      : getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 16, offset: 24 });
  const estado = data?.estado ?? "inactivo";
  const clase = data?.clase ?? "principal";
  const mostrarEtiqueta = data?.etiqueta && (data.resaltado || (estado !== "inactivo" && clase !== "principal"));
  return (
    <>
      <BaseEdge id={id} path={ruta} markerEnd={markerEnd} className={`cable ${clase} ${estado} ${data?.resaltado ? "resaltado" : ""}`} interactionWidth={14} />
      {estado !== "inactivo" && <path d={ruta} className={`cable-corriente ${estado}`} fill="none" />}
      {estado === "activo" &&
        [0, 0.4, 0.8].map((d) => (
          <circle key={d} r={3.4} className="chispa">
            <animateMotion dur="1.2s" begin={`${d}s`} repeatCount="indefinite" path={ruta} />
          </circle>
        ))}
      {mostrarEtiqueta && (
        <EdgeLabelRenderer>
          <div className={`cable-etiqueta ${estado}`} style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}>
            {data!.etiqueta}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
});

const tiposNodo = { etapa: NodoEtapa, final: NodoFinal, extremo: NodoExtremo, regla: NodoRegla, fase: NodoFase, nota: NodoNota };
const tiposCable = { cable: Cable };

// -------------------------------------------------------------- disposición

function caminoPrincipal(catalogo: Catalogo): string[] {
  const finales = new Set(catalogo.nodos.filter((n) => n.terminal).map((n) => n.id));
  const sucesores: Record<string, string[]> = {};
  for (const a of catalogo.aristas) (sucesores[a.desde] ??= []).push(a.hacia);
  const memo: Record<string, number> = {};
  const largo = (id: string, pila: Set<string>): number => {
    if (memo[id] !== undefined) return memo[id];
    pila.add(id);
    let mejor = 0;
    for (const s of sucesores[id] ?? []) if (!pila.has(s) && !finales.has(s)) mejor = Math.max(mejor, 1 + largo(s, pila));
    pila.delete(id);
    return (memo[id] = mejor);
  };
  const destinos = new Set(catalogo.aristas.map((a) => a.hacia));
  let actual: string | undefined = catalogo.nodos.find((n) => !destinos.has(n.id))?.id ?? catalogo.nodos[0]?.id;
  const camino: string[] = [];
  const vistos = new Set<string>();
  while (actual && !vistos.has(actual)) {
    camino.push(actual);
    vistos.add(actual);
    const opciones: string[] = (sucesores[actual] ?? []).filter((s) => !vistos.has(s) && !finales.has(s));
    opciones.sort((a, b) => largo(b, new Set(vistos)) - largo(a, new Set(vistos)));
    actual = opciones[0];
  }
  return camino;
}

interface Disposicion {
  posiciones: Record<string, Posicion>;
  fases: Node<DatosFase>[];
  /** Donde empieza la banda de salidas y donde termina la de reglas: por ahí van los pasillos. */
  ySalidas: number;
  yBajoReglas: number;
  salidas: Set<string>;
  /** Los nodos de cada fase, de arriba abajo: se apilan con su alto real. */
  columnas: string[][];
}

/** Espacio libre entre dos nodos de la misma fase. */
const SEPARACION = 36;

/**
 * Apila cada fase con el alto que cada nodo tiene en pantalla, que crece con
 * lo que muestra (hojas leídas, datos de la corrida), y baja la banda de
 * salidas lo que haga falta. Lo que la persona movió a mano no se toca.
 */
function apilar(nodos: Node[], d: Disposicion, movidos: Record<string, Posicion>): { y: Record<string, number>; altoFase: number; ySalidas: number } | null {
  const porId = new Map(nodos.map((n) => [n.id, n]));
  const y: Record<string, number> = {};
  let fondo = 0;
  for (const ids of d.columnas) {
    let cursor = CABEZA_FASE;
    for (const id of ids) {
      const n = porId.get(id);
      if (!n) continue;
      const alto = n.measured?.height;
      if (!alto) return null;
      if (!movidos[id]) y[id] = cursor;
      cursor += alto + SEPARACION;
    }
    fondo = Math.max(fondo, cursor);
  }
  const altoFase = fondo + 2;
  const ySalidas = altoFase + 72;
  const corrimiento = ySalidas - d.ySalidas;
  for (const id of d.salidas) if (!movidos[id] && d.posiciones[id]) y[id] = d.posiciones[id]!.y + corrimiento;
  return { y, altoFase, ySalidas };
}

/** Columnas por fase, de izquierda a derecha; abajo, los desvíos y salidas; arriba, las reglas propias. */
function disponer(catalogo: Catalogo, reglas: Regla[]): Disposicion {
  const posiciones: Record<string, Posicion> = {};
  const fases: Node<DatosFase>[] = [];
  const existe = new Set(["__inicio", "__fin", ...catalogo.nodos.map((n) => n.id)]);
  const ubicadas = new Set<string>();
  // Cada flujo trae sus fases; PQRD usa las de siempre. La entrada abre la
  // primera fase y el desenlace cierra la última.
  const FASES_DEL_FLUJO = catalogo.fases?.length
    ? catalogo.fases.map((f, i, todas) => ({ ...f, nodos: [...(i === 0 ? ["__inicio"] : []), ...f.nodos, ...(i === todas.length - 1 ? ["__fin"] : [])] }))
    : FASES;
  const FINALES: Record<string, string> = catalogo.fases?.length ? {} : FASE_DEL_FINAL;
  const columnas = FASES_DEL_FLUJO.map((f) => f.nodos.filter((id) => existe.has(id)));
  // Un paso insertado en la estructura va en la fase de la etapa tras la que corre, justo debajo de ella.
  for (const n of catalogo.nodos) {
    if (!n.insertado || columnas.some((c) => c.includes(n.id))) continue;
    const c = columnas.find((x) => x.includes(n.insertado!.desde)) ?? columnas[columnas.length - 1]!;
    const tras = [n.insertado.desde, ...catalogo.nodos.filter((m) => m.insertado?.desde === n.insertado!.desde && c.includes(m.id)).map((m) => m.id)];
    c.splice(Math.max(...tras.map((x) => c.indexOf(x))) + 1, 0, n.id);
  }
  // Lo que el catálogo traiga y no tenga fase va al final de la fase más cercana.
  for (const n of catalogo.nodos) {
    if (n.terminal || FINALES[n.id] || columnas.some((c) => c.includes(n.id))) continue;
    columnas[columnas.length - 1]!.push(n.id);
  }
  const filas = Math.max(...columnas.map((c) => c.length));
  const altoFase = CABEZA_FASE + filas * FILA;
  columnas.forEach((ids, i) => {
    ids.forEach((id, j) => {
      posiciones[id] = { x: i * COLUMNA, y: CABEZA_FASE + j * FILA };
      ubicadas.add(id);
    });
    fases.push({ id: `__fase-${FASES_DEL_FLUJO[i]!.id}`, type: "fase", position: { x: i * COLUMNA - 18, y: 0 }, data: { nombre: FASES_DEL_FLUJO[i]!.nombre, numero: String(i + 1), ancho: ANCHO + 36, alto: altoFase }, draggable: false, selectable: false, focusable: false, zIndex: -1 });
  });

  const anchoTotal = (FASES_DEL_FLUJO.length - 1) * COLUMNA + ANCHO + 36;
  const y0 = altoFase + 72;
  const porFase: Record<string, number> = {};
  for (const n of catalogo.nodos) {
    if (ubicadas.has(n.id)) continue;
    const fase = FINALES[n.id] ?? FASES_DEL_FLUJO[FASES_DEL_FLUJO.length - 1]!.id;
    const i = Math.max(0, FASES_DEL_FLUJO.findIndex((f) => f.id === fase));
    const k = (porFase[fase] = (porFase[fase] ?? -1) + 1);
    const ancho = n.terminal ? ANCHO_FIN : ANCHO;
    posiciones[n.id] = { x: i * COLUMNA + (ANCHO - ancho) / 2, y: y0 + CABEZA_FASE + k * 66 };
  }
  const altoSalidas = CABEZA_FASE + Math.max(2, ...Object.values(porFase).map((k) => k + 1)) * 66 + 70;
  fases.push({ id: "__fase-salidas", type: "fase", position: { x: -18, y: y0 }, data: { nombre: "Desvíos y salidas", numero: "", ancho: anchoTotal, alto: altoSalidas, pista: "donde termina un correo que no se radica" }, draggable: false, selectable: false, focusable: false, zIndex: -1 });

  const yReglas = -280;
  fases.push({ id: "__fase-reglas", type: "fase", position: { x: -18, y: yReglas }, data: { nombre: "Reglas personalizadas", numero: "", ancho: anchoTotal, alto: 214, pista: "clic derecho en el lienzo para agregar una" }, draggable: false, selectable: false, focusable: false, zIndex: -1 });
  const porAncla: Record<string, number> = {};
  for (const r of reglas) {
    const ancla = ANCLA_DEL_PUNTO[r.punto];
    const base = posiciones[ancla] ?? { x: 0, y: 0 };
    const k = (porAncla[ancla] = (porAncla[ancla] ?? -1) + 1);
    posiciones[r.id] = { x: base.x + k * (ANCHO + 16), y: yReglas + CABEZA_FASE };
  }
  const salidas = new Set(catalogo.nodos.filter((n) => !ubicadas.has(n.id)).map((n) => n.id));
  return { posiciones, fases, ySalidas: y0, yBajoReglas: yReglas + 214, salidas, columnas };
}

function lados(o: Posicion, d: Posicion): [string, string] {
  const dx = d.x - o.x;
  const dy = d.y - o.y;
  if (Math.abs(dy) > 90 && Math.abs(dy) > Math.abs(dx) * 0.55) return dy > 0 ? ["s-b", "e-t"] : ["s-t", "e-b"];
  return dx >= 0 ? ["s-r", "e-l"] : ["s-l", "e-r"];
}

function extras(n: NodoCatalogo, p: Progreso): string[] {
  if (!p.nodos[n.id]) return [];
  if (n.id === "conversion_a_markdown" && p.hojas) return [`${p.hojas} hoja${p.hojas === 1 ? "" : "s"}`];
  if (n.id === "transcripcion_vision") return [`${p.imagenesVision} imagen${p.imagenesVision === 1 ? "" : "es"}`];
  if (n.id === "indice_por_hoja") return p.consultasIndice ? [`${p.consultasIndice} búsqueda${p.consultasIndice === 1 ? "" : "s"}`] : ["índice consultado"];
  if (n.id === "revisor" && p.vueltasRevisor) return [`${p.vueltasRevisor} vueltas`];
  if (n.usa_ia && p.vueltas) return [`${p.vueltas} vueltas`, `${(p.tokensEntrada + p.tokensSalida).toLocaleString("es-CO")} tokens`];
  if (n.id === "registro_de_personas" && p.consultas) return [`${p.consultas} consulta${p.consultas > 1 ? "s" : ""}`];
  return [];
}

function igual(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  for (const k of Object.keys(b)) {
    const x = a[k];
    const y = b[k];
    if (x === y) continue;
    if (Array.isArray(x) && Array.isArray(y) && x.length === y.length && x.every((v, i) => v === y[i])) continue;
    return false;
  }
  return true;
}

// ---------------------------------------------------------------- buscador

function Buscador({ catalogo, reglas, ir }: { catalogo: Catalogo; reglas: Regla[]; ir: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [abierto, setAbierto] = useState(false);
  const [activo, setActivo] = useState(0);
  const entrada = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const atajo = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        entrada.current?.focus();
        setAbierto(true);
      }
    };
    window.addEventListener("keydown", atajo);
    return () => window.removeEventListener("keydown", atajo);
  }, []);
  const resultados = useMemo(() => {
    const t = q.trim().toLowerCase();
    const todos = [
      ...catalogo.nodos.map((n) => ({ id: n.id, nombre: n.nombre, cat: categoria(n) })),
      ...reglas.map((r) => ({ id: r.id, nombre: r.nombre, cat: "regla" as Categoria })),
    ];
    return todos.filter((x) => !t || `${x.nombre} ${CATEGORIAS[x.cat].nombre} ${x.id}`.toLowerCase().includes(t)).slice(0, 9);
  }, [q, catalogo, reglas]);
  const elegir = (id: string) => {
    ir(id);
    setQ("");
    setAbierto(false);
    entrada.current?.blur();
  };
  return (
    <div className="buscador">
      <div className="campo-icono">
        <Icono n="buscar" tam={14} />
        <input
          ref={entrada}
          className="campo"
          placeholder="Buscar un nodo…"
          value={q}
          onFocus={() => setAbierto(true)}
          onBlur={() => setTimeout(() => setAbierto(false), 120)}
          onChange={(e) => {
            setQ(e.target.value);
            setActivo(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") setActivo((a) => Math.min(a + 1, resultados.length - 1));
            else if (e.key === "ArrowUp") setActivo((a) => Math.max(a - 1, 0));
            else if (e.key === "Enter" && resultados[activo]) elegir(resultados[activo]!.id);
            else if (e.key === "Escape") entrada.current?.blur();
          }}
        />
        <kbd className="atajo">Ctrl K</kbd>
      </div>
      <AnimatePresence>
        {abierto && resultados.length > 0 && (
          <motion.div className="buscador-lista" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.12 }}>
            {resultados.map((r, i) => {
              const c = CATEGORIAS[r.cat];
              return (
                <button key={r.id} className={`buscador-opcion ${i === activo ? "activa" : ""}`} onMouseEnter={() => setActivo(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => elegir(r.id)}>
                  <span className={`mini-icono t-${c.tono}`}><Icono n={c.icono} tam={13} /></span>
                  <span className="crece">{r.nombre}</span>
                  <span className="texto-3 diminuto">{c.nombre}</span>
                </button>
              );
            })}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Leyenda() {
  const [abierta, setAbierta] = useState(false);
  const caja = useRef<HTMLDivElement>(null);
  // Se cierra con Esc o al tocar cualquier otra cosa, para no quedar encima de otros menús.
  useEffect(() => {
    if (!abierta) return;
    const fuera = (e: PointerEvent) => {
      if (!caja.current?.contains(e.target as globalThis.Node)) setAbierta(false);
    };
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAbierta(false);
    };
    window.addEventListener("pointerdown", fuera, true);
    window.addEventListener("keydown", tecla);
    return () => {
      window.removeEventListener("pointerdown", fuera, true);
      window.removeEventListener("keydown", tecla);
    };
  }, [abierta]);
  // {accion:"panel", panel:"leyenda", abierto}
  useEffect(() => {
    const oir = (e: Event) => {
      const d = (e as CustomEvent<{ accion?: string; panel?: string; abierto?: boolean }>).detail;
      if (d?.accion === "panel" && d.panel === "leyenda") setAbierta(Boolean(d.abierto));
    };
    window.addEventListener("positiva:control", oir);
    return () => window.removeEventListener("positiva:control", oir);
  }, []);
  const usadas: Categoria[] = ["entrada", "filtro", "ruta", "documentos", "ocr", "busqueda", "ia", "datos", "validacion", "integracion", "salida", "regla"];
  return (
    <div className="leyenda" ref={caja}>
      <button className={`boton flotante chico ${abierta ? "activo" : ""}`} aria-expanded={abierta} onClick={() => setAbierta(!abierta)}><Icono n="info" tam={14} />Leyenda</button>
      <AnimatePresence>
        {abierta && (
          <motion.div className="leyenda-caja" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.14 }}>
            <div className="leyenda-grupo">
              {usadas.map((c) => (
                <span key={c} className="leyenda-item"><span className={`mini-icono t-${CATEGORIAS[c].tono}`}><Icono n={CATEGORIAS[c].icono} tam={12} /></span>{CATEGORIAS[c].nombre}</span>
              ))}
            </div>
            <div className="leyenda-grupo">
              <span className="leyenda-item"><span className="muestra-cable principal" />Camino principal</span>
              <span className="leyenda-item"><span className="muestra-cable desvio" />Desvío posible</span>
              <span className="leyenda-item"><span className="muestra-cable recorrido" />Recorrido por el correo</span>
              <span className="leyenda-item"><span className="muestra-cable activo" />Corriente en este momento</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Banda({ corrida, p, catalogo }: { corrida: Corrida | undefined; p: Progreso; catalogo: Catalogo }) {
  const enfocar = usar((s) => s.enfocar);
  const [ahora, setAhora] = useState(() => Date.now());
  const enCurso = corrida && !corrida.resultado;
  useEffect(() => {
    if (!enCurso) return;
    const t = setInterval(() => setAhora(Date.now()), 250);
    return () => clearInterval(t);
  }, [enCurso]);
  if (!corrida) return null;
  const r = corrida.resultado;
  const activo = Object.entries(p.nodos).find(([, e]) => e.estado === "activo")?.[0];
  const etapa = activo ? catalogo.nodos.find((n) => n.id === activo)?.nombre : undefined;
  const t0 = corrida.eventos.find((e) => e.tipo === "inicio")?.t;
  const accion = r ? String(r.accion) : "";
  const hechas = Object.values(p.nodos).filter((x) => x.estado === "hecho").length;
  const total = catalogo.nodos.filter((n) => !n.terminal).length;
  return (
    <motion.button layout className={`banda tono-${r ? (TONO_DESENLACE[accion] ?? "gris") : "azul"}`} onClick={() => enfocar({ tipo: "corrida", id: corrida.id })} initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
      {r ? <Icono n={accion === "radicado" ? "ok" : "bandera"} tam={14} /> : <span className="girando" />}
      <b>{corrida.caso}</b>
      <span className="banda-texto">{r ? `${NOMBRES_DESENLACE[accion] ?? accion}${r.motivo ? ` · ${motivoLegible(String(r.motivo))}` : ""}` : corrida.estado === "en_cola" ? "En cola" : etapa ? etapa : "Arrancando…"}</span>
      <span className="banda-t mono">{r ? `${String(r.segundos ?? "")} s` : t0 ? `${((ahora / 1000 - t0) | 0)} s` : ""}</span>
      {!r && <span className="banda-progreso"><i style={{ width: `${Math.min(100, (hechas / total) * 100)}%` }} /></span>}
    </motion.button>
  );
}

// ------------------------------------------------------------------- lienzo

type Menu = { x: number; y: number; opciones: Opcion[] } | null;

function LienzoDeCodigo() {
  const catalogo = usar((s) => s.catalogo);
  const corrida = usar((s) => (s.corridaVisible ? s.corridas[s.corridaVisible] : undefined));
  const foco = usar((s) => s.foco);
  const enfocar = usar((s) => s.enfocar);
  const configuracion = usar((s) => s.configuracion);
  const guardarConfiguracion = usar((s) => s.guardarConfiguracion);
  const seguir = usar((s) => s.seguirEnVivo);
  const setSeguir = usar((s) => s.setSeguirEnVivo);
  const borradores = usar((s) => s.borradores);
  const notas = usar((s) => s.notas);
  const objetivo = usar((s) => s.objetivo);
  const crearRegla = usar((s) => s.crearRegla);
  const crearNota = usar((s) => s.crearNota);
  const borrarRegla = usar((s) => s.borrarRegla);
  const borrarNota = usar((s) => s.borrarNota);
  const duplicarRegla = usar((s) => s.duplicarRegla);
  const guardarRegla = usar((s) => s.guardarRegla);
  const avisar = usar((s) => s.avisar);
  const bandeja = usar((s) => s.bandeja);
  const flujo = useReactFlow();
  const [nodos, setNodos] = useState<Node[]>([]);
  const [menu, setMenu] = useState<Menu>(null);
  const [encima, setEncima] = useState<string | null>(null);
  const [bloqueado, setBloqueado] = useState(false);
  const [minimapa, setMinimapa] = useState(true);
  const encuadrado = useRef(false);
  // Zoom semántico: de lejos cada nodo muestra solo su categoría y su nombre, en grande.
  const lejos = useStore((s) => s.transform[2] < 0.62);

  const reglas = useMemo(() => {
    const guardadas = configuracion?.reglas ?? [];
    return [...guardadas.filter((r) => !borradores.some((b) => b.id === r.id)), ...borradores];
  }, [configuracion, borradores]);
  const idsBorrador = useMemo(() => new Set(borradores.filter((b) => !(configuracion?.reglas ?? []).some((r) => r.id === b.id)).map((b) => b.id)), [borradores, configuracion]);

  const p = useMemo(() => progreso(catalogo, corrida), [catalogo, corrida]);
  // Las hojas que el motor ya leyó (y la que está leyendo) se ven sobre el nodo de conversión.
  const hojasVivas = useMemo((): VistaLectura | undefined => {
    if (!corrida) return undefined;
    const l = lectura(corrida);
    const todas: { src: string; metodo: string }[] = [];
    let foco: VistaLectura["foco"] = null;
    for (const d of l.documentos) {
      if (d.esCuerpo) continue;
      for (const h of d.hojas) {
        if (h.mini) todas.push({ src: urlArchivo(corrida.id, h.mini), metodo: h.metodo });
        const img = h.img ?? h.mini;
        if (img) foco = { img: urlArchivo(corrida.id, img), documento: d.nombre, detalle: `Hoja ${h.numero} de ${d.paginas || d.hojas.length} · ${NOMBRE_METODO[h.metodo] ?? h.metodo}`, leyendo: false };
      }
    }
    if (l.enCurso) {
      const d = l.documentos.find((x) => x.clave === l.enCurso!.clave);
      const img = l.enCurso.img ?? l.enCurso.mini;
      foco = { img: img ? urlArchivo(corrida.id, img) : "", documento: d?.nombre ?? "", detalle: `Leyendo hoja ${l.enCurso.pagina}${d?.paginas ? ` de ${d.paginas}` : ""}…`, leyendo: true };
    }
    const max = 5;
    return { foco, hojas: todas.slice(-max), mas: Math.max(0, todas.length - max), total: todas.length };
  }, [corrida]);
  const disposicion = useMemo(() => (catalogo ? disponer(catalogo, reglas) : null), [catalogo, reglas]);
  const asunto = useMemo(() => (corrida ? (bandeja.find((c) => c.caso === corrida.caso)?.asunto ?? String(corrida.eventos.find((e) => e.tipo === "inicio")?.asunto ?? "")) : ""), [corrida, bandeja]);
  const evaluadas = useMemo(() => {
    const m: Record<string, "cumple" | "no"> = {};
    for (const e of corrida?.eventos ?? []) if (e.tipo === "regla") m[String(e.id)] = e.cumple ? "cumple" : "no";
    return m;
  }, [corrida]);

  // Los nodos del catálogo se crean una vez; después solo cambian sus datos,
  // así arrastrar uno no reacomoda los demás ni mueve la cámara.
  useEffect(() => {
    if (!catalogo || !disposicion) return;
    const guardadas = leerLocal<Record<string, Posicion>>(CLAVE_POSICIONES, {});
    const pos = (id: string) => guardadas[id] ?? disposicion.posiciones[id] ?? { x: 0, y: 0 };
    setNodos([
      ...disposicion.fases,
      { id: "__inicio", type: "extremo", position: pos("__inicio"), data: { tipo: "inicio" } },
      ...catalogo.nodos.map((n) => ({ id: n.id, type: n.terminal ? "final" : "etapa", position: pos(n.id), data: { nodo: n, cat: categoria(n), estado: "inactivo", ms: 0, veces: 0, extra: [], ajustes: 0, modelo: "" } })),
      { id: "__fin", type: "extremo", position: pos("__fin"), data: { tipo: "fin" } },
    ]);
    encuadrado.current = false;
    // Solo al cambiar de catálogo: las reglas y notas se sincronizan aparte.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalogo]);

  // Reglas y notas entran y salen sin tocar la posición de lo demás.
  useEffect(() => {
    if (!disposicion) return;
    setNodos((ns) => {
      if (!ns.length) return ns;
      const guardadas = leerLocal<Record<string, Posicion>>(CLAVE_POSICIONES, {});
      const quedan = ns.filter((x) => (x.type !== "regla" || reglas.some((r) => r.id === x.id)) && (x.type !== "nota" || notas.some((n) => n.id === x.id)));
      const ids = new Set(quedan.map((x) => x.id));
      const nuevas: Node[] = [
        ...reglas.filter((r) => !ids.has(r.id)).map((r) => ({ id: r.id, type: "regla", position: guardadas[r.id] ?? disposicion.posiciones[r.id] ?? { x: 0, y: -200 }, data: { regla: r, borrador: false, resultado: "" } })),
        ...notas.filter((n) => !ids.has(n.id)).map((n) => ({ id: n.id, type: "nota", position: guardadas[n.id] ?? { x: 0, y: -400 }, data: { id: n.id, texto: n.texto }, style: { width: 220 } })),
      ];
      return nuevas.length || quedan.length !== ns.length ? [...quedan, ...nuevas] : ns;
    });
  }, [reglas, notas, disposicion]);

  // Datos vivos de cada nodo: se reemplazan solo los que cambiaron.
  useEffect(() => {
    setNodos((ns) =>
      ns.map((x) => {
        const sel = (foco?.tipo === "nodo" || foco?.tipo === "regla") && foco.id === x.id;
        let datos: Record<string, unknown> | null = null;
        if (x.type === "etapa" || x.type === "final") {
          const n = (x.data as DatosEtapa).nodo;
          const est = p.nodos[n.id];
          datos = { nodo: n, cat: categoria(n), estado: est?.estado ?? "inactivo", ms: est?.ms ?? 0, veces: est?.veces ?? 0, extra: extras(n, p), ajustes: n.parametros.filter((q) => ajustado(q, configuracion)).length, modelo: configuracion?.ia.modelo ?? "", ...(n.id === "conversion_a_markdown" && hojasVivas ? { lectura: hojasVivas } : {}) };
        } else if (x.type === "extremo") {
          datos = { tipo: (x.data as DatosExtremo).tipo, corrida, asunto };
        } else if (x.type === "regla") {
          const r = reglas.find((y) => y.id === x.id);
          if (r) datos = { regla: r, borrador: idsBorrador.has(r.id), resultado: evaluadas[r.id] ?? "" };
        } else if (x.type === "nota") {
          const n = notas.find((y) => y.id === x.id);
          if (n) datos = { id: n.id, texto: n.texto };
        }
        const mismoDato = !datos || igual(x.data, datos);
        if (mismoDato && Boolean(x.selected) === sel) return x;
        return { ...x, data: mismoDato ? x.data : datos!, selected: sel };
      }),
    );
  }, [p, configuracion, foco, corrida, asunto, reglas, idsBorrador, evaluadas, notas, hojasVivas]);

  // Cuando un nodo crece o se encoge, su fase se reacomoda para que nada quede encima de nada.
  const [ySalidas, setYSalidas] = useState<number | null>(null);
  const alturas = nodos.map((n) => (n.type === "fase" ? "" : `${n.id}:${Math.round(n.measured?.height ?? 0)}`)).join("|");
  useEffect(() => {
    if (!disposicion) return;
    const a = apilar(nodos, disposicion, leerLocal<Record<string, Posicion>>(CLAVE_POSICIONES, {}));
    if (!a) return;
    setYSalidas(a.ySalidas);
    setNodos((ns) => {
      let cambio = false;
      const nuevos = ns.map((n) => {
        if (n.type === "fase") {
          const f = n.data as DatosFase;
          if (n.id === "__fase-salidas") {
            if (Math.abs(n.position.y - a.ySalidas) < 1) return n;
            cambio = true;
            return { ...n, position: { ...n.position, y: a.ySalidas } };
          }
          if (n.id === "__fase-reglas" || Math.abs(f.alto - a.altoFase) < 1) return n;
          cambio = true;
          return { ...n, data: { ...f, alto: a.altoFase } };
        }
        const y = a.y[n.id];
        if (y === undefined || Math.abs(n.position.y - y) < 1) return n;
        cambio = true;
        return { ...n, position: { ...n.position, y } };
      });
      return cambio ? nuevos : ns;
    });
    // Solo cuando cambia algún alto, no en cada movimiento.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alturas, disposicion]);

  const principal = useMemo(() => {
    if (!catalogo) return new Set<string>();
    const c = caminoPrincipal(catalogo);
    const s = new Set<string>();
    for (let i = 0; i < c.length - 1; i++) s.add(`${c[i]}->${c[i + 1]}`);
    return s;
  }, [catalogo]);

  const cables: Edge<DatosCable>[] = useMemo(() => {
    if (!catalogo) return [];
    const donde: Record<string, Posicion> = {};
    for (const n of nodos) donde[n.id] = n.position;
    // A un nodo con varias entradas se llega por una sola: la de la última etapa recorrida antes de él.
    const tomada: Record<string, string> = {};
    for (const a of catalogo.aristas) {
      const o = p.nodos[a.desde];
      const d = p.nodos[a.hacia];
      if (!o || !d || d.orden <= o.orden) continue;
      if (!tomada[a.hacia] || (p.nodos[tomada[a.hacia]!]?.orden ?? 0) < o.orden) tomada[a.hacia] = a.desde;
    }
    const enCurso = Boolean(corrida && !corrida.resultado);
    const foco_ = foco && (foco.tipo === "nodo" || foco.tipo === "regla") ? foco.id : null;
    const lista: { desde: string; hacia: string; etiqueta?: string; clase: DatosCable["clase"]; estado: DatosCable["estado"] }[] = [
      { desde: "__inicio", hacia: catalogo.nodos[0]?.id ?? "lote", clase: "principal", estado: corrida ? (Object.keys(p.nodos).length ? "recorrido" : "activo") : "inactivo" },
      ...catalogo.aristas.map((a) => {
        const recorrido = tomada[a.hacia] === a.desde;
        const activo = recorrido && p.nodos[a.hacia]?.estado === "activo";
        return { desde: a.desde, hacia: a.hacia, etiqueta: a.etiqueta, clase: (principal.has(`${a.desde}->${a.hacia}`) ? "principal" : "desvio") as DatosCable["clase"], estado: (activo ? "activo" : recorrido ? "recorrido" : "inactivo") as DatosCable["estado"] };
      }),
      { desde: catalogo.nodos.some((n) => n.id === "aviso_y_cierre") ? "aviso_y_cierre" : (catalogo.fases?.at(-1)?.nodos.at(-1) ?? catalogo.nodos.at(-1)?.id ?? ""), hacia: "__fin", clase: "principal", estado: corrida?.resultado ? "recorrido" : "inactivo" },
      ...reglas.flatMap((r) => {
        const ancla = ANCLA_DEL_PUNTO[r.punto];
        const ev = evaluadas[r.id];
        const salen = [{ desde: ancla, hacia: r.id, etiqueta: "evalúa", clase: "regla" as const, estado: (ev ? "recorrido" : "inactivo") as DatosCable["estado"] }];
        if (r.accion === "manual") salen.push({ desde: r.id, hacia: "revision_manual", etiqueta: r.motivo || "REGLA_PERSONALIZADA", clase: "regla", estado: ev === "cumple" ? "recorrido" : "inactivo" });
        return salen;
      }),
    ];
    const salidas = disposicion?.salidas ?? new Set<string>();
    const esRegla = new Set(reglas.map((r) => r.id));
    return lista
      .filter((a) => donde[a.desde] && donde[a.hacia])
      .map((a, i) => {
        let [sh, th] = lados(donde[a.desde]!, donde[a.hacia]!);
        let carril: number | undefined;
        if (disposicion && salidas.has(a.hacia) && !salidas.has(a.desde)) {
          // Hacia la banda de salidas: por el pasillo justo encima de ella.
          [sh, th, carril] = ["s-r", "e-t", (ySalidas ?? disposicion.ySalidas) - 36];
        } else if (disposicion && salidas.has(a.desde) && !salidas.has(a.hacia)) {
          // De la banda de salidas al cierre: por el borde superior de la banda.
          [sh, th, carril] = ["s-t", "e-l", (ySalidas ?? disposicion.ySalidas) - 14];
        } else if (disposicion && esRegla.has(a.hacia)) {
          [sh, th, carril] = ["s-r", "e-b", disposicion.yBajoReglas + 26];
        }
        const resaltado = a.desde === encima || a.hacia === encima || a.desde === foco_ || a.hacia === foco_;
        const estado = a.estado === "activo" && !enCurso ? "recorrido" : a.estado;
        const color = estado !== "inactivo" ? COLOR_FLECHA[estado] : resaltado ? COLOR_FLECHA.resaltado : a.clase === "regla" ? COLOR_FLECHA.regla : COLOR_FLECHA.inactivo;
        return {
          id: `${a.desde}->${a.hacia}-${i}`,
          source: a.desde,
          target: a.hacia,
          sourceHandle: sh,
          targetHandle: th,
          type: "cable",
          data: { estado, clase: a.clase, etiqueta: a.etiqueta, resaltado, carril },
          zIndex: 0,
          markerEnd: { type: MarkerType.ArrowClosed, width: 13, height: 13, color },
        };
      })
      // Lo recorrido y lo resaltado se dibuja encima de los demás cables, pero debajo de los nodos.
      .sort((x, y) => Number(x.data.estado !== "inactivo" || x.data.resaltado) - Number(y.data.estado !== "inactivo" || y.data.resaltado));
  }, [catalogo, nodos, p, corrida, reglas, evaluadas, principal, encima, foco, disposicion, ySalidas]);

  const alCambiar = useCallback((cambios: NodeChange[]) => setNodos((ns) => applyNodeChanges(cambios, ns)), []);

  const encuadrar = useCallback(() => {
    const sinReglas = !reglas.length && !notas.length;
    void flujo.fitView({ padding: { top: "64px", left: "60px", right: "24px", bottom: "20px" }, duration: 450, nodes: sinReglas ? flujo.getNodes().filter((n) => n.id !== "__fase-reglas") : undefined });
  }, [flujo, reglas.length, notas.length]);

  /**
   * Al llegar, el diagrama se ve a un tamaño que se lee: si todas las fases
   * caben así, se muestran completas; si no, se arranca por la primera fase y
   * el resto queda a la derecha (el minimapa y «Encuadrar todo» lo muestran).
   */
  const vistaInicial = useCallback((animada = false) => {
    const caja = document.querySelector(".lienzo") as HTMLElement | null;
    const fases = flujo.getNodes().filter((n) => n.type === "fase" && n.id !== "__fase-reglas" && n.id !== "__fase-salidas");
    if (!caja || !fases.length) {
      encuadrar();
      return;
    }
    const w = caja.clientWidth;
    const movil = w < 600;
    const izq = Math.min(...fases.map((f) => f.position.x));
    const der = Math.max(...fases.map((f) => f.position.x + (f.data as DatosFase).ancho));
    const margen = movil ? 12 : 28;
    const minimo = movil ? 0.8 : 0.86;
    const zoom = Math.min(1, Math.max(minimo, (w - margen * 2) / (der - izq)));
    const anchoVisto = (der - izq) * zoom;
    const x = anchoVisto < w - margen * 2 ? (w - anchoVisto) / 2 - izq * zoom : margen - izq * zoom;
    const arriba = reglas.length || notas.length ? -280 : 0;
    void flujo.setViewport({ x, y: (movil ? 60 : 68) - arriba * zoom, zoom }, { duration: animada ? 450 : 0 });
  }, [flujo, encuadrar, reglas.length, notas.length]);

  // El asistente mueve la cámara y abre o cierra el minimapa:
  // {accion:"lienzo", modo} y {accion:"panel", panel:"minimapa", abierto}.
  const camara = useRef({ encuadrar, vistaInicial });
  camara.current = { encuadrar, vistaInicial };
  useEffect(() => {
    const oir = (e: Event) => {
      const d = (e as CustomEvent<{ accion?: string; modo?: string; panel?: string; abierto?: boolean }>).detail;
      if (d?.accion === "lienzo") {
        if (d.modo === "ver_todo") camara.current.encuadrar();
        else if (d.modo === "acercar") void flujo.zoomIn({ duration: 250 });
        else if (d.modo === "alejar") void flujo.zoomOut({ duration: 250 });
        else if (d.modo === "restablecer") camara.current.vistaInicial(true);
      } else if (d?.accion === "panel" && d.panel === "minimapa") setMinimapa(Boolean(d.abierto));
    };
    window.addEventListener("positiva:control", oir);
    return () => window.removeEventListener("positiva:control", oir);
  }, [flujo]);

  // Una sola vez por catálogo, cuando los nodos ya tienen su alto real.
  useEffect(() => {
    if (encuadrado.current || ySalidas === null || !nodos.length) return;
    encuadrado.current = true;
    setTimeout(() => vistaInicial(), 40);
  }, [ySalidas, nodos.length, vistaInicial]);

  /** Lo que tapa la hoja del inspector por la derecha, en píxeles de pantalla. */
  const anchoHoja = useCallback(() => {
    const lienzo = document.querySelector(".lienzo") as HTMLElement | null;
    const centro = document.querySelector(".centro") as HTMLElement | null;
    const w = lienzo?.clientWidth ?? 1200;
    if (w < 760) return 0;
    const v = centro ? parseFloat(getComputedStyle(centro).getPropertyValue("--inspector")) : 470;
    return Math.min(v || 470, w - 20) + 10;
  }, []);

  const centrarEn = useCallback(
    (id: string, zoom?: number, conHoja = true) => {
      const n = flujo.getNode(id);
      if (!n) return;
      const w = n.measured?.width ?? ANCHO;
      const h = n.measured?.height ?? 110;
      const z = zoom ?? Math.max(flujo.getZoom(), 0.95);
      const corrimiento = conHoja ? anchoHoja() / 2 / z : 0;
      void flujo.setCenter(n.position.x + w / 2 + corrimiento, n.position.y + h / 2, { zoom: z, duration: 550 });
    },
    [flujo, anchoHoja],
  );

  /** Si el nodo elegido queda debajo de la hoja, el lienzo se corre lo justo. */
  const asegurarVisible = useCallback(
    (id: string) => {
      const n = flujo.getNode(id);
      const lienzo = document.querySelector(".lienzo") as HTMLElement | null;
      if (!n || !lienzo) return;
      const { x, y, zoom } = flujo.getViewport();
      const derecha = (n.position.x + (n.measured?.width ?? ANCHO)) * zoom + x;
      const limite = lienzo.clientWidth - anchoHoja() - 24;
      if (derecha > limite) void flujo.setViewport({ x: x - (derecha - limite), y, zoom }, { duration: 400 });
    },
    [flujo, anchoHoja],
  );

  const enfocarNodo = useCallback(
    (id: string) => {
      if (id.startsWith("__")) return;
      const esRegla = reglas.some((r) => r.id === id);
      if (esRegla) enfocar({ tipo: "regla", id });
      else if (catalogo?.nodos.some((n) => n.id === id)) enfocar({ tipo: "nodo", id });
    },
    [reglas, catalogo, enfocar],
  );

  // Pedidos de otros paneles: «mostrar este nodo».
  useEffect(() => {
    if (!objetivo) return;
    const t = setTimeout(() => {
      centrarEn(objetivo.id);
      enfocarNodo(objetivo.id);
    }, 60);
    return () => clearTimeout(t);
  }, [objetivo, centrarEn, enfocarNodo]);

  // En vivo: la cámara acompaña a la etapa en curso.
  const activo = useMemo(() => Object.entries(p.nodos).find(([, e]) => e.estado === "activo")?.[0], [p]);
  useEffect(() => {
    if (seguir && activo && corrida && !corrida.resultado) centrarEn(activo, Math.max(flujo.getZoom(), 0.75), Boolean(document.querySelector(".hoja")));
  }, [activo, seguir, corrida, centrarEn, flujo]);

  const reordenar = () => {
    if (!catalogo || !disposicion) return;
    const g = leerLocal<Record<string, Posicion>>(CLAVE_POSICIONES, {});
    const notasPos: Record<string, Posicion> = {};
    for (const n of notas) if (g[n.id]) notasPos[n.id] = g[n.id]!;
    guardarLocal(CLAVE_POSICIONES, notasPos);
    setNodos((ns) => {
      const base = ns.map((n) => ({ ...n, position: disposicion.posiciones[n.id] ?? notasPos[n.id] ?? n.position }));
      // Con el alto real de cada nodo, igual que al crecer.
      const a = apilar(base, disposicion, notasPos);
      return a ? base.map((n) => (a.y[n.id] === undefined ? n : { ...n, position: { ...n.position, y: a.y[n.id]! } })) : base;
    });
    setTimeout(encuadrar, 40);
  };

  const enfocado = foco && (foco.tipo === "nodo" || foco.tipo === "regla") ? foco.id : null;

  // Teclado: F enfoca, Supr borra lo propio, Esc suelta la selección.
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [contenteditable], .cm-editor")) return;
      if (e.key === "Escape" && enfocado) enfocar(null);
      if (e.key.toLowerCase() === "f" && !e.ctrlKey && !e.metaKey) {
        if (enfocado) centrarEn(enfocado, 1.05);
        else encuadrar();
      }
      if ((e.key === "Delete" || e.key === "Backspace") && enfocado && reglas.some((r) => r.id === enfocado)) void borrarRegla(enfocado);
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [enfocado, enfocar, centrarEn, encuadrar, reglas, borrarRegla]);

  const menuDeNodo = (e: React.MouseEvent, n: Node) => {
    e.preventDefault();
    const donde = flujo.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    let opciones: Opcion[] = [];
    if (n.type === "regla") {
      const r = reglas.find((x) => x.id === n.id)!;
      const guardada = (configuracion?.reglas ?? []).some((x) => x.id === r.id);
      opciones = [
        { titulo: r.nombre },
        { etiqueta: "Editar regla", icono: "editar", atajo: "Clic", alHacer: () => enfocar({ tipo: "regla", id: r.id }) },
        { etiqueta: "Enfocar", icono: "mira", atajo: "F", alHacer: () => centrarEn(r.id, 1.05) },
        { separador: true },
        { etiqueta: r.activa ? "Desactivar" : "Activar", icono: r.activa ? "pausa" : "play", deshabilitado: !guardada, pista: guardada ? undefined : "Guárdala primero", alHacer: () => void guardarRegla({ ...r, activa: !r.activa }).then(() => avisar(r.activa ? "Regla desactivada" : "Regla activada")).catch((x: Error) => avisar(x.message, "error")) },
        { etiqueta: "Duplicar", icono: "copiar", alHacer: () => duplicarRegla(r.id) },
        { separador: true },
        { etiqueta: "Eliminar regla", icono: "papelera", atajo: "Supr", peligro: true, alHacer: () => void borrarRegla(r.id).then(() => avisar("Regla eliminada")) },
      ];
    } else if (n.type === "nota") {
      opciones = [{ etiqueta: "Eliminar nota", icono: "papelera", peligro: true, alHacer: () => borrarNota(n.id) }];
    } else if (n.type === "etapa" || n.type === "final") {
      const nodo = (n.data as DatosEtapa).nodo;
      const ajustes = nodo.parametros.filter((q) => ajustado(q, configuracion));
      opciones = [
        { titulo: nodo.nombre },
        { etiqueta: "Abrir y configurar", icono: "ajustes", atajo: "Clic", alHacer: () => enfocar({ tipo: "nodo", id: nodo.id }) },
        { etiqueta: "Enfocar", icono: "mira", atajo: "F", alHacer: () => centrarEn(nodo.id, 1.05) },
        { separador: true },
        { etiqueta: "Agregar regla de entrada aquí", icono: "regla", alHacer: () => crearRegla("entrada", { x: donde.x + 30, y: donde.y + 150 }) },
        { etiqueta: "Agregar regla de validación aquí", icono: "escudo", alHacer: () => crearRegla("validacion", { x: donde.x + 30, y: donde.y + 150 }) },
        { etiqueta: "Agregar nota", icono: "nota", alHacer: () => crearNota({ x: donde.x + 30, y: donde.y + 150 }) },
        { separador: true },
        {
          etiqueta: `Restablecer ${ajustes.length} ajuste${ajustes.length === 1 ? "" : "s"} de este nodo`,
          icono: "deshacer",
          deshabilitado: !ajustes.length,
          alHacer: () => {
            if (!configuracion) return;
            let c = configuracion;
            for (const q of ajustes) c = conValor(c, q, undefined);
            void guardarConfiguracion(c, `${nodo.nombre}: valores del código`).then(() => avisar("Volvió a los valores del código"));
          },
        },
        { etiqueta: "Copiar nombre técnico", icono: "copiar", alHacer: () => void navigator.clipboard?.writeText(nodo.modulo ? `${nodo.modulo} · ${nodo.id}` : nodo.id).then(() => avisar("Copiado")) },
        { etiqueta: "Eliminar", icono: "papelera", deshabilitado: true, pista: "Es una etapa del motor de producción: se configura, no se elimina. Para cambiar su efecto agrega una regla.", alHacer: () => undefined },
      ];
    } else return;
    setMenu({ x: e.clientX, y: e.clientY, opciones });
  };

  const menuDelLienzo = (e: React.MouseEvent | MouseEvent) => {
    e.preventDefault();
    const donde = flujo.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    setMenu({
      x: e.clientX,
      y: e.clientY,
      opciones: [
        { titulo: "Agregar al flujo" },
        { etiqueta: "Regla de entrada", icono: "regla", pista: PUNTOS.entrada.descripcion, alHacer: () => crearRegla("entrada", donde) },
        { etiqueta: "Regla de validación", icono: "escudo", pista: PUNTOS.validacion.descripcion, alHacer: () => crearRegla("validacion", donde) },
        { etiqueta: "Nota", icono: "nota", alHacer: () => crearNota(donde) },
        { separador: true },
        { etiqueta: "Encuadrar todo", icono: "encuadrar", atajo: "F", alHacer: encuadrar },
        { etiqueta: "Reordenar el diagrama", icono: "reordenar", alHacer: reordenar },
        { etiqueta: "Seguir la corrida en vivo", marcado: seguir, alHacer: () => setSeguir(!seguir) },
        { etiqueta: "Mostrar minimapa", marcado: minimapa, alHacer: () => setMinimapa(!minimapa) },
      ],
    });
  };

  if (!catalogo) return <div className="vacio">Cargando el mapa del pipeline…</div>;

  return (
    <>
      <ReactFlow
        nodes={nodos}
        edges={cables}
        nodeTypes={tiposNodo}
        edgeTypes={tiposCable}
        onNodesChange={alCambiar}
        onNodeDragStop={(_, __, movidos) => {
          const g = leerLocal<Record<string, Posicion>>(CLAVE_POSICIONES, {});
          for (const m of movidos) g[m.id] = m.position;
          guardarLocal(CLAVE_POSICIONES, g);
        }}
        onNodeClick={(_, n) => {
          setMenu(null);
          if (n.id === "__fin" && corrida) enfocar({ tipo: "corrida", id: corrida.id });
          else if (n.id === "__inicio" && corrida) enfocar({ tipo: "correo", caso: corrida.caso });
          else enfocarNodo(n.id);
          if (n.type !== "fase") setTimeout(() => asegurarVisible(n.id), 30);
        }}
        onEdgeClick={() => {
          setMenu(null);
          if (foco) enfocar(null);
        }}
        onNodeDoubleClick={(_, n) => centrarEn(n.id, 1.1)}
        onNodeMouseEnter={(_, n) => n.type !== "fase" && setEncima(n.id)}
        onNodeMouseLeave={() => setEncima(null)}
        onNodeContextMenu={menuDeNodo}
        onPaneContextMenu={menuDelLienzo}
        onPaneClick={() => {
          setMenu(null);
          if (foco) enfocar(null);
        }}
        nodesDraggable={!bloqueado}
        minZoom={0.15}
        maxZoom={2}
        nodesConnectable={false}
        deleteKeyCode={null}
        selectionKeyCode={null}
        multiSelectionKeyCode={null}
        elementsSelectable
        onlyRenderVisibleElements={false}
        proOptions={{ hideAttribution: true }}
        className={`${encima ? "con-encima" : ""} ${lejos ? "lejos" : ""}`}
      >
        <Panel position="top-left" className="lienzo-arriba-izq">
          <Buscador catalogo={catalogo} reglas={reglas} ir={(id) => { centrarEn(id, 1.05); enfocarNodo(id); }} />
          <Leyenda />
        </Panel>
        <Panel position="top-center" className="lienzo-centro">
          <AnimatePresence>{corrida && <Banda key={corrida.id} corrida={corrida} p={p} catalogo={catalogo} />}</AnimatePresence>
        </Panel>
        <Panel position="top-right" className="lienzo-barra">
          <button className={`boton flotante chico ${seguir ? "activo" : ""}`} onClick={() => setSeguir(!seguir)} title="La cámara acompaña la etapa en curso y el lienzo muestra cada corrida que empieza">
            <span className={`punto ${seguir ? "latiendo" : ""}`} /><span className="etq">En vivo</span>
          </button>
          <button
            className="boton flotante chico"
            onClick={(e) => {
              const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
              setMenu({
                x: r.left,
                y: r.bottom + 6,
                opciones: [
                  { titulo: "Nueva regla" },
                  { etiqueta: "Regla de entrada", icono: "regla", pista: PUNTOS.entrada.descripcion, alHacer: () => crearRegla("entrada") },
                  { etiqueta: "Regla de validación", icono: "escudo", pista: PUNTOS.validacion.descripcion, alHacer: () => crearRegla("validacion") },
                  { separador: true },
                  { etiqueta: "Nota en el centro", icono: "nota", alHacer: () => { const b = document.querySelector(".react-flow")!.getBoundingClientRect(); crearNota(flujo.screenToFlowPosition({ x: b.left + b.width / 2, y: b.top + b.height / 2 })); } },
                ],
              });
            }}
          >
            <Icono n="mas" tam={14} /><span className="etq">Agregar</span>
          </button>
        </Panel>
        <Panel position="bottom-left" className="controles">
          <button onClick={() => void flujo.zoomIn({ duration: 200 })} title="Acercar" aria-label="Acercar"><Icono n="mas" tam={15} /></button>
          <button onClick={() => void flujo.zoomOut({ duration: 200 })} title="Alejar" aria-label="Alejar"><Icono n="menos" tam={15} /></button>
          <i />
          <button onClick={encuadrar} title="Encuadrar todo (F)" aria-label="Encuadrar todo"><Icono n="encuadrar" tam={15} /></button>
          <button onClick={() => enfocado && centrarEn(enfocado, 1.05)} disabled={!enfocado} title={enfocado ? "Enfocar el nodo seleccionado (F)" : "Selecciona un nodo para enfocarlo"} aria-label="Enfocar selección"><Icono n="mira" tam={15} /></button>
          <i />
          <button className={bloqueado ? "activo" : ""} onClick={() => setBloqueado(!bloqueado)} title={bloqueado ? "Desbloquear posiciones" : "Bloquear posiciones"} aria-label="Bloquear"><Icono n={bloqueado ? "candado" : "abierto"} tam={15} /></button>
          <button className={minimapa ? "activo" : ""} onClick={() => setMinimapa(!minimapa)} title="Minimapa" aria-label="Minimapa"><Icono n="mapa" tam={15} /></button>
        </Panel>
        {minimapa && (
          <MiniMap
            pannable
            zoomable
            position="bottom-right"
            style={{ width: 168, height: 104 }}
            nodeBorderRadius={6}
            nodeColor={(n) => {
              if (n.type === "fase") return "rgba(0,0,0,0)";
              const e = (n.data as DatosEtapa).estado;
              return ({ activo: "#5cb8ff", hecho: "#a9dfa2", fallo: "#f4b4ab" } as Record<string, string>)[e as string] ?? (n.type === "regla" ? "#b7cbe8" : "#cfe3f7");
            }}
            maskColor="rgba(232,241,249,0.72)"
          />
        )}
      </ReactFlow>
      {menu && <MenuContextual x={menu.x} y={menu.y} opciones={menu.opciones} cerrar={() => setMenu(null)} />}
    </>
  );
}

