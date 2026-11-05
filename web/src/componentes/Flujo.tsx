import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlow, Controls, Handle, MarkerType, Position, applyNodeChanges, useNodesInitialized, useReactFlow, type Edge, type Node, type NodeChange, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { usar } from "../estado.ts";
import { progreso, segundos, type Progreso } from "../progreso.ts";
import type { Catalogo, EstadoNodo, Nodo as NodoCatalogo } from "../tipos.ts";

const CLAVE_POSICIONES = "positiva-ia:posiciones:v3";
const ANCHO = 220;
const ALTO = 100;
const ANCHO_FIN = 170;
const COLUMNAS = 6;
const PASO_X = 290;
const PASO_Y = 330;

interface DatosNodo extends Record<string, unknown> {
  nodo: NodoCatalogo;
  estado: EstadoNodo;
  ms: number;
  veces: number;
  extra: string[];
  seleccionado: boolean;
  cambios: number;
}

function Asas() {
  // Una entrada y una salida por lado: cada conexión usa los lados que miran al otro nodo.
  return (
    <>
      {(["l", "r", "t", "b"] as const).map((lado) => {
        const pos = { l: Position.Left, r: Position.Right, t: Position.Top, b: Position.Bottom }[lado];
        return (
          <span key={lado}>
            <Handle id={`e-${lado}`} type="target" position={pos} />
            <Handle id={`s-${lado}`} type="source" position={pos} />
          </span>
        );
      })}
    </>
  );
}

function NodoEtapa({ data }: NodeProps<Node<DatosNodo>>) {
  const n = data.nodo;
  const clases = ["nodo", n.usa_ia ? "ia" : "", data.estado !== "inactivo" ? data.estado : "", data.seleccionado ? "sel" : ""].join(" ");
  return (
    <div className={clases}>
      <Asas />
      <div className="titulo">
        {n.nombre}
        {n.usa_ia && <span className="etiqueta-ia">IA</span>}
      </div>
      <div className="desc">{n.descripcion}</div>
      <div className="pie">
        {data.ms > 0 && <span className="dato">{segundos(data.ms)}</span>}
        {data.veces > 1 && <span className="dato">×{data.veces}</span>}
        {data.extra.map((x) => <span key={x} className="dato">{x}</span>)}
        {data.cambios > 0 && <span className="dato ajustado">{data.cambios} ajuste{data.cambios > 1 ? "s" : ""}</span>}
        {data.ms === 0 && data.extra.length === 0 && <span className="dato">{n.parametros.length} parámetros · {n.reglas?.length ?? 0} reglas</span>}
      </div>
    </div>
  );
}

function NodoFinal({ data }: NodeProps<Node<DatosNodo>>) {
  const accion = data.nodo.desenlaces?.[0]?.accion ?? data.nodo.id;
  return (
    <div className={`nodo desenlace ${accion} ${data.estado !== "inactivo" ? "alcanzado" : ""} ${data.seleccionado ? "sel" : ""}`}>
      <Asas />
      <div className="titulo" style={{ justifyContent: "center" }}>{data.nodo.nombre}</div>
    </div>
  );
}

const tipos = { etapa: NodoEtapa, final: NodoFinal };

/** Camino principal: desde el primer nodo, siempre por la salida que lleva más lejos sin pasar por un final. */
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

/** Serpentina: el camino principal en filas que van y vuelven; lo demás, junto a la etapa de donde sale. */
function disponer(catalogo: Catalogo): Record<string, { x: number; y: number }> {
  const camino = caminoPrincipal(catalogo);
  const pos: Record<string, { x: number; y: number }> = {};
  camino.forEach((id, i) => {
    const fila = Math.floor(i / COLUMNAS);
    const col = fila % 2 === 0 ? i % COLUMNAS : COLUMNAS - 1 - (i % COLUMNAS);
    pos[id] = { x: col * PASO_X, y: fila * PASO_Y };
  });
  const filas = Math.ceil(camino.length / COLUMNAS);
  const ocupados: Record<string, number> = {};
  const restantes = catalogo.nodos.filter((n) => !pos[n.id]);
  // Los que reciben de muchas etapas van a la derecha, a media altura.
  const entradas: Record<string, number> = {};
  for (const a of catalogo.aristas) entradas[a.hacia] = (entradas[a.hacia] ?? 0) + 1;
  let lateral = 0;
  for (const n of restantes) {
    if ((entradas[n.id] ?? 0) >= 3) {
      pos[n.id] = { x: ((COLUMNAS - 1) * PASO_X) / 2 + lateral * PASO_X, y: (filas - 1) * PASO_Y + ALTO + 150 };
      lateral += 1;
      continue;
    }
    const origen = catalogo.aristas.find((a) => a.hacia === n.id && pos[a.desde])?.desde ?? catalogo.aristas.find((a) => a.desde === n.id && pos[a.hacia])?.hacia;
    const base = origen ? pos[origen]! : { x: 0, y: filas * PASO_Y };
    const k = (ocupados[origen ?? ""] = (ocupados[origen ?? ""] ?? -1) + 1);
    const ancho = n.terminal ? ANCHO_FIN : ANCHO;
    pos[n.id] = { x: base.x + (ANCHO - ancho) / 2 + k * (ancho + 16), y: base.y + ALTO + 70 };
  }
  return pos;
}

function lados(o: { x: number; y: number }, d: { x: number; y: number }): [string, string] {
  const dx = d.x - o.x;
  const dy = d.y - o.y;
  if (Math.abs(dy) > ALTO && Math.abs(dy) > Math.abs(dx) * 0.6) return dy > 0 ? ["s-b", "e-t"] : ["s-t", "e-b"];
  return dx >= 0 ? ["s-r", "e-l"] : ["s-l", "e-r"];
}

function leerPosiciones(): Record<string, { x: number; y: number }> {
  try {
    return JSON.parse(localStorage.getItem(CLAVE_POSICIONES) ?? "{}") as Record<string, { x: number; y: number }>;
  } catch {
    return {};
  }
}

function extras(n: NodoCatalogo, p: Progreso): string[] {
  if (!p.nodos[n.id]) return [];
  if (n.id === "revisor" && p.vueltasRevisor) return [`${p.vueltasRevisor} vueltas`];
  if (n.usa_ia && p.vueltas) return [`${p.vueltas} vueltas`, `${(p.tokensEntrada + p.tokensSalida).toLocaleString("es-CO")} tokens`];
  if (n.id === "registro_de_personas" && p.consultas) return [`${p.consultas} consulta${p.consultas > 1 ? "s" : ""}`];
  return [];
}

export function Flujo() {
  const catalogo = usar((s) => s.catalogo);
  const corrida = usar((s) => (s.seleccionada ? s.corridas[s.seleccionada] : undefined));
  const nodoSel = usar((s) => s.nodo);
  const verNodo = usar((s) => s.verNodo);
  const configuracion = usar((s) => s.configuracion);
  const [posiciones, setPosiciones] = useState(leerPosiciones);
  const [nodos, setNodos] = useState<Node<DatosNodo>[]>([]);
  const { fitView } = useReactFlow();
  const iniciados = useNodesInitialized();
  const caja = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (iniciados) void fitView({ padding: 0.05 });
  }, [iniciados, fitView]);

  // Al abrir o cerrar un panel el lienzo cambia de ancho: el diagrama se vuelve a encuadrar.
  useEffect(() => {
    const el = caja.current?.parentElement;
    if (!el) return;
    let espera = 0;
    const ro = new ResizeObserver(() => {
      clearTimeout(espera);
      espera = window.setTimeout(() => void fitView({ padding: 0.05, duration: 200 }), 120);
    });
    ro.observe(el);
    return () => {
      clearTimeout(espera);
      ro.disconnect();
    };
  }, [fitView, catalogo]);

  const automaticas = useMemo(() => (catalogo ? disponer(catalogo) : {}), [catalogo]);
  const p = useMemo(() => progreso(catalogo, corrida), [catalogo, corrida]);

  useEffect(() => {
    if (!catalogo) return;
    const cambiados = new Set([...Object.keys(configuracion?.parametros ?? {}), ...Object.keys(configuracion?.entorno ?? {})]);
    setNodos(
      catalogo.nodos.map((n) => {
        const est = p.nodos[n.id];
        return {
          id: n.id,
          type: n.terminal ? "final" : "etapa",
          position: posiciones[n.id] ?? automaticas[n.id] ?? { x: 0, y: 0 },
          data: {
            nodo: n,
            estado: est?.estado ?? "inactivo",
            ms: est?.ms ?? 0,
            veces: est?.veces ?? 0,
            extra: extras(n, p),
            seleccionado: nodoSel === n.id,
            cambios: n.parametros.filter((x) => cambiados.has(x.clave) || (x.env !== null && cambiados.has(x.env))).length,
          },
        };
      }),
    );
  }, [catalogo, automaticas, posiciones, p, nodoSel, configuracion]);

  const aristas: Edge[] = useMemo(() => {
    if (!catalogo) return [];
    const donde: Record<string, { x: number; y: number }> = {};
    for (const n of nodos) donde[n.id] = n.position;
    // A un nodo con varias entradas solo se llega por una: la de la última etapa recorrida antes de él.
    const entradaTomada: Record<string, string> = {};
    for (const a of catalogo.aristas) {
      const o = p.nodos[a.desde];
      const d = p.nodos[a.hacia];
      if (!o || !d || o.estado === "inactivo" || d.orden <= o.orden) continue;
      const actual = entradaTomada[a.hacia];
      if (!actual || (p.nodos[actual]?.orden ?? 0) < o.orden) entradaTomada[a.hacia] = a.desde;
    }
    return catalogo.aristas.map((a, i) => {
      const recorrida = entradaTomada[a.hacia] === a.desde;
      const activa = recorrida && p.nodos[a.hacia]?.estado === "activo";
      const [sh, th] = donde[a.desde] && donde[a.hacia] ? lados(donde[a.desde]!, donde[a.hacia]!) : ["s-r", "e-l"];
      const color = activa ? "#2f6fd6" : recorrida ? "#7fbf9f" : "#c4c9d2";
      return {
        id: `${a.desde}->${a.hacia}-${i}`,
        source: a.desde,
        target: a.hacia,
        sourceHandle: sh,
        targetHandle: th,
        label: a.etiqueta || undefined,
        type: "smoothstep",
        animated: activa,
        className: activa ? "activa" : recorrida ? "recorrida" : "",
        zIndex: recorrida ? 2 : 0,
        markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color },
      };
    });
  }, [catalogo, nodos, p]);

  const alCambiar = useCallback((cambios: NodeChange<Node<DatosNodo>>[]) => {
    setNodos((ns) => applyNodeChanges(cambios, ns));
    const movidos = cambios.filter((c) => c.type === "position" && c.position && !c.dragging);
    if (!movidos.length) return;
    setPosiciones((prev) => {
      const sig = { ...prev };
      for (const c of movidos) if (c.type === "position" && c.position) sig[c.id] = c.position;
      try {
        localStorage.setItem(CLAVE_POSICIONES, JSON.stringify(sig));
      } catch {
        /* sin almacenamiento: las posiciones viven solo en esta pestaña */
      }
      return sig;
    });
  }, []);

  const reordenar = () => {
    try {
      localStorage.removeItem(CLAVE_POSICIONES);
    } catch {
      /* nada que borrar */
    }
    setPosiciones({});
    setTimeout(() => void fitView({ padding: 0.05, duration: 200 }), 50);
  };

  if (!catalogo) return <div className="vacio">Cargando el mapa del pipeline…</div>;

  return (
    <>
      <div ref={caja} style={{ display: "none" }} />
      <ReactFlow
        nodes={nodos}
        edges={aristas}
        nodeTypes={tipos}
        onNodesChange={alCambiar}
        onNodeClick={(_, n) => verNodo(n.id)}
        onPaneClick={() => verNodo(null)}
        fitView
        fitViewOptions={{ padding: 0.05 }}
        minZoom={0.15}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Controls showInteractive={false} />
      </ReactFlow>
      <div className="flotante">
        <button className="boton chico" onClick={reordenar}>Reordenar</button>
      </div>
      <div className="leyenda">
        <span className="pastilla azul"><span className="punto" />En curso</span>
        <span className="pastilla verde"><span className="punto" />Recorrida</span>
        <span className="pastilla rojo"><span className="punto" />Falló</span>
        <span className="pastilla">Fondo azul: usa IA</span>
        {corrida ? <span className="pastilla">Mostrando la corrida de {corrida.caso}</span> : <span className="pastilla">Elige un correo para verlo pasar por el flujo</span>}
      </div>
    </>
  );
}
