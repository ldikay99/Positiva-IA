import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BaseEdge,
  Controls,
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
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { ajustado } from "../parametros.ts";
import { progreso, segundos, type Progreso } from "../progreso.ts";
import type { Catalogo, EstadoNodo, Nodo as NodoCatalogo } from "../tipos.ts";

const CLAVE_POSICIONES = "positiva-ia:posiciones:v4";
const ANCHO = 228;
const ALTO = 104;
const ANCHO_FIN = 176;
const COLUMNAS = 6;
const PASO_X = 300;
const PASO_Y = 300;

interface DatosNodo extends Record<string, unknown> {
  nodo: NodoCatalogo;
  estado: EstadoNodo;
  ms: number;
  veces: number;
  extra: string[];
  ajustes: number;
}

interface DatosCable extends Record<string, unknown> {
  estado: "inactivo" | "recorrido" | "activo";
  etiqueta?: string;
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

const TEXTO_ESTADO: Record<EstadoNodo, string> = { inactivo: "", activo: "En curso", hecho: "Recorrida", fallo: "Falló", desenlace: "" };

function NodoEtapa({ data, selected }: NodeProps<Node<DatosNodo>>) {
  const n = data.nodo;
  return (
    <div className={`nodo ${n.usa_ia ? "ia" : ""} ${data.estado} ${selected ? "sel" : ""}`}>
      <Asas />
      <div className="nodo-cabeza">
        <span className="nodo-icono"><Icono n={n.usa_ia ? "chispa" : n.id.includes("dedup") ? "capas" : n.id.includes("radic") ? "rayo" : "regla"} tam={14} /></span>
        <span className="nodo-titulo">{n.nombre}</span>
        {data.estado !== "inactivo" && <span className={`punto-estado ${data.estado}`} title={TEXTO_ESTADO[data.estado]} />}
      </div>
      <div className="nodo-desc">{n.descripcion}</div>
      <div className="nodo-pie">
        {data.ms > 0 && <span className="dato">{segundos(data.ms)}</span>}
        {data.veces > 1 && <span className="dato">×{data.veces}</span>}
        {data.extra.map((x) => <span key={x} className="dato">{x}</span>)}
        {data.ajustes > 0 && <span className="dato ajustado">{data.ajustes} ajustado{data.ajustes > 1 ? "s" : ""}</span>}
        {data.ms === 0 && data.extra.length === 0 && n.parametros.length > 0 && <span className="dato tenue">{n.parametros.filter((p) => p.ajuste !== null).length} editables</span>}
      </div>
    </div>
  );
}

function NodoFinal({ data, selected }: NodeProps<Node<DatosNodo>>) {
  const accion = data.nodo.desenlaces?.[0]?.accion ?? data.nodo.id;
  return (
    <div className={`nodo final ${accion} ${data.estado !== "inactivo" ? "alcanzado" : ""} ${selected ? "sel" : ""}`}>
      <Asas />
      <span className="nodo-titulo">{data.nodo.nombre}</span>
    </div>
  );
}

// ------------------------------------------------------------------- cables

/** Cable con flecha; mientras la corriente pasa, unas chispas recorren el trazo. */
function Cable({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd }: EdgeProps<Edge<DatosCable>>) {
  const [ruta, lx, ly] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 14, offset: 22 });
  const estado = data?.estado ?? "inactivo";
  return (
    <>
      <BaseEdge id={id} path={ruta} markerEnd={markerEnd} className={`cable ${estado}`} />
      {estado !== "inactivo" && <path d={ruta} className={`cable-corriente ${estado}`} fill="none" />}
      {estado === "activo" &&
        [0, 0.33, 0.66].map((d) => (
          <circle key={d} r={3.6} className="chispa">
            <animateMotion dur="1.1s" begin={`${d}s`} repeatCount="indefinite" path={ruta} />
          </circle>
        ))}
      {data?.etiqueta && (
        <EdgeLabelRenderer>
          <div className={`cable-etiqueta ${estado}`} style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}>
            {data.etiqueta}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const tiposNodo = { etapa: NodoEtapa, final: NodoFinal };
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

/** Serpentina: el camino principal en filas que van y vuelven; los desvíos, bajo la etapa de donde salen. */
function disponer(catalogo: Catalogo): Record<string, { x: number; y: number }> {
  const camino = caminoPrincipal(catalogo);
  const pos: Record<string, { x: number; y: number }> = {};
  camino.forEach((id, i) => {
    const fila = Math.floor(i / COLUMNAS);
    const col = fila % 2 === 0 ? i % COLUMNAS : COLUMNAS - 1 - (i % COLUMNAS);
    pos[id] = { x: col * PASO_X, y: fila * PASO_Y };
  });
  const filas = Math.ceil(camino.length / COLUMNAS);
  const entradas: Record<string, number> = {};
  for (const a of catalogo.aristas) entradas[a.hacia] = (entradas[a.hacia] ?? 0) + 1;
  const ocupados: Record<string, number> = {};
  let lateral = 0;
  for (const n of catalogo.nodos.filter((x) => !pos[x.id])) {
    if ((entradas[n.id] ?? 0) >= 3) {
      pos[n.id] = { x: ((COLUMNAS - 1) * PASO_X) / 2 + lateral * PASO_X, y: (filas - 1) * PASO_Y + ALTO + 160 };
      lateral += 1;
      continue;
    }
    const origen = catalogo.aristas.find((a) => a.hacia === n.id && pos[a.desde])?.desde ?? catalogo.aristas.find((a) => a.desde === n.id && pos[a.hacia])?.hacia;
    const base = origen ? pos[origen]! : { x: 0, y: filas * PASO_Y };
    const k = (ocupados[origen ?? ""] = (ocupados[origen ?? ""] ?? -1) + 1);
    const ancho = n.terminal ? ANCHO_FIN : ANCHO;
    pos[n.id] = { x: base.x + (ANCHO - ancho) / 2 + k * (ancho + 18), y: base.y + ALTO + 62 };
  }
  return pos;
}

function lados(o: { x: number; y: number }, d: { x: number; y: number }): [string, string] {
  const dx = d.x - o.x;
  const dy = d.y - o.y;
  if (Math.abs(dy) > ALTO * 0.8 && Math.abs(dy) > Math.abs(dx) * 0.55) return dy > 0 ? ["s-b", "e-t"] : ["s-t", "e-b"];
  return dx >= 0 ? ["s-r", "e-l"] : ["s-l", "e-r"];
}

function leerPosiciones(): Record<string, { x: number; y: number }> {
  try {
    return JSON.parse(localStorage.getItem(CLAVE_POSICIONES) ?? "{}") as Record<string, { x: number; y: number }>;
  } catch {
    return {};
  }
}

function guardarPosiciones(p: Record<string, { x: number; y: number }>): void {
  try {
    localStorage.setItem(CLAVE_POSICIONES, JSON.stringify(p));
  } catch {
    /* sin almacenamiento: las posiciones viven solo en esta pestaña */
  }
}

function extras(n: NodoCatalogo, p: Progreso): string[] {
  if (!p.nodos[n.id]) return [];
  if (n.id === "revisor" && p.vueltasRevisor) return [`${p.vueltasRevisor} vueltas`];
  if (n.usa_ia && p.vueltas) return [`${p.vueltas} vueltas`, `${(p.tokensEntrada + p.tokensSalida).toLocaleString("es-CO")} tokens`];
  if (n.id === "registro_de_personas" && p.consultas) return [`${p.consultas} consulta${p.consultas > 1 ? "s" : ""}`];
  return [];
}

// ------------------------------------------------------------------- lienzo

export function Lienzo() {
  const catalogo = usar((s) => s.catalogo);
  const corrida = usar((s) => (s.corridaVisible ? s.corridas[s.corridaVisible] : undefined));
  const foco = usar((s) => s.foco);
  const enfocar = usar((s) => s.enfocar);
  const configuracion = usar((s) => s.configuracion);
  const seguir = usar((s) => s.seguirEnVivo);
  const setSeguir = usar((s) => s.setSeguirEnVivo);
  const { fitView } = useReactFlow();
  const [nodos, setNodos] = useState<Node<DatosNodo>[]>([]);
  const encuadrado = useRef(false);

  const p = useMemo(() => progreso(catalogo, corrida), [catalogo, corrida]);

  // Los nodos se crean una vez por catálogo; después solo cambian sus datos,
  // así arrastrar uno no reacomoda los demás ni mueve la cámara.
  useEffect(() => {
    if (!catalogo) return;
    const guardadas = leerPosiciones();
    const auto = disponer(catalogo);
    setNodos(
      catalogo.nodos.map((n) => ({
        id: n.id,
        type: n.terminal ? "final" : "etapa",
        position: guardadas[n.id] ?? auto[n.id] ?? { x: 0, y: 0 },
        data: { nodo: n, estado: "inactivo", ms: 0, veces: 0, extra: [], ajustes: 0 },
      })),
    );
    encuadrado.current = false;
  }, [catalogo]);

  useEffect(() => {
    setNodos((ns) =>
      ns.map((x) => {
        const n = x.data.nodo;
        const est = p.nodos[n.id];
        const datos: DatosNodo = {
          nodo: n,
          estado: est?.estado ?? "inactivo",
          ms: est?.ms ?? 0,
          veces: est?.veces ?? 0,
          extra: extras(n, p),
          ajustes: n.parametros.filter((q) => ajustado(q, configuracion)).length,
        };
        return { ...x, data: datos, selected: foco?.tipo === "nodo" && foco.id === n.id };
      }),
    );
  }, [p, configuracion, foco]);

  const cables: Edge<DatosCable>[] = useMemo(() => {
    if (!catalogo) return [];
    const donde: Record<string, { x: number; y: number }> = {};
    for (const n of nodos) donde[n.id] = n.position;
    // A un nodo con varias entradas se llega por una sola: la de la última etapa recorrida antes de él.
    const tomada: Record<string, string> = {};
    for (const a of catalogo.aristas) {
      const o = p.nodos[a.desde];
      const d = p.nodos[a.hacia];
      if (!o || !d || d.orden <= o.orden) continue;
      if (!tomada[a.hacia] || (p.nodos[tomada[a.hacia]!]?.orden ?? 0) < o.orden) tomada[a.hacia] = a.desde;
    }
    return catalogo.aristas.map((a, i) => {
      const recorrido = tomada[a.hacia] === a.desde;
      const activo = recorrido && p.nodos[a.hacia]?.estado === "activo";
      const [sh, th] = donde[a.desde] && donde[a.hacia] ? lados(donde[a.desde]!, donde[a.hacia]!) : ["s-r", "e-l"];
      const estado: DatosCable["estado"] = activo ? "activo" : recorrido ? "recorrido" : "inactivo";
      return {
        id: `${a.desde}->${a.hacia}-${i}`,
        source: a.desde,
        target: a.hacia,
        sourceHandle: sh,
        targetHandle: th,
        type: "cable",
        data: { estado, etiqueta: a.etiqueta },
        zIndex: estado === "inactivo" ? 0 : 3,
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: estado === "activo" ? "#2f6fd6" : estado === "recorrido" ? "#2f9e6e" : "#b4bcc8" },
      };
    });
  }, [catalogo, nodos, p]);

  const alCambiar = useCallback((cambios: NodeChange<Node<DatosNodo>>[]) => setNodos((ns) => applyNodeChanges(cambios, ns)), []);

  const encuadrar = useCallback(() => void fitView({ padding: 0.08, duration: 300 }), [fitView]);

  const reordenar = () => {
    if (!catalogo) return;
    guardarPosiciones({});
    const auto = disponer(catalogo);
    setNodos((ns) => ns.map((n) => ({ ...n, position: auto[n.id] ?? n.position })));
    setTimeout(encuadrar, 30);
  };

  if (!catalogo) return <div className="vacio">Cargando el mapa del pipeline…</div>;

  return (
    <ReactFlow
      nodes={nodos}
      edges={cables}
      nodeTypes={tiposNodo}
      edgeTypes={tiposCable}
      onNodesChange={alCambiar}
      onNodeDragStop={(_, __, movidos) => {
        const g = leerPosiciones();
        for (const n of nodos) g[n.id] = n.position;
        for (const m of movidos) g[m.id] = m.position;
        guardarPosiciones(g);
      }}
      onNodeClick={(_, n) => enfocar({ tipo: "nodo", id: n.id })}
      onPaneClick={() => foco?.tipo === "nodo" && enfocar(null)}
      onInit={() => {
        if (!encuadrado.current) {
          encuadrado.current = true;
          setTimeout(encuadrar, 50);
        }
      }}
      minZoom={0.12}
      maxZoom={1.8}
      nodesConnectable={false}
      elementsSelectable
      proOptions={{ hideAttribution: true }}
    >
      <Panel position="top-left" className="lienzo-leyenda">
        <span className="pastilla"><span className="punto-estado activo" />En curso</span>
        <span className="pastilla"><span className="punto-estado hecho" />Recorrida</span>
        <span className="pastilla"><span className="punto-estado fallo" />Falló</span>
        <span className="pastilla"><Icono n="chispa" tam={12} />Usa IA</span>
      </Panel>
      <Panel position="top-right" className="lienzo-barra">
        <button className={`boton chico ${seguir ? "activo" : ""}`} onClick={() => setSeguir(!seguir)} title="Mostrar en el lienzo cada corrida en cuanto empieza">
          <Icono n="ojo" tam={14} />Seguir en vivo
        </button>
        <button className="boton chico" onClick={encuadrar}><Icono n="encuadrar" tam={14} />Encuadrar</button>
        <button className="boton chico" onClick={reordenar}><Icono n="reordenar" tam={14} />Reordenar</button>
      </Panel>
      <Controls position="bottom-left" showInteractive={false} />
      <MiniMap pannable zoomable position="bottom-right" style={{ width: 150, height: 96 }} nodeColor={(n) => ({ activo: "#9cc0f5", hecho: "#a6dcc2", fallo: "#f2b8b3" })[(n.data as DatosNodo).estado as string] ?? "#dde1e7"} maskColor="rgba(244,245,247,0.7)" />
    </ReactFlow>
  );
}
