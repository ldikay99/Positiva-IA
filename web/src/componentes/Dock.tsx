import { memo, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "motion/react";
import { usar } from "../estado.ts";
import { NOMBRE_FUENTE, describir, narrar, solicitud, type EstadoCampo, type Paso, type Tramo } from "../eventos.ts";
import { CATEGORIAS } from "../flujo.ts";
import { Icono } from "../iconos.tsx";
import { NOMBRES_DESENLACE, TONO_DESENLACE, segundos } from "../progreso.ts";
import type { Evento } from "../tipos.ts";

// ------------------------------------------------------------ piezas comunes

export const FilaPaso = memo(function FilaPaso({ p, t0 }: { p: Paso; t0: number }) {
  const [abierto, setAbierto] = useState(false);
  return (
    <div className={`paso tono-${p.tono} ${p.detalle ? "con-detalle" : ""} ${abierto ? "abierto" : ""}`} onClick={() => p.detalle && setAbierto(!abierto)}>
      <span className="paso-t mono">+{Math.max(0, p.t - t0).toFixed(1)}s</span>
      <span className="paso-icono"><Icono n={p.icono} tam={13} /></span>
      <span className="paso-cuerpo">
        <span className="paso-titulo">{p.titulo}</span>
        {p.confianza !== undefined && <span className="barra-conf mini" title={`${Math.round(p.confianza * 100)} %`}><i style={{ width: `${Math.round(p.confianza * 100)}%` }} /></span>}
        {p.extra && <span className="paso-extra">{p.extra}</span>}
      </span>
      {p.detalle && <Icono n={abierto ? "abajo" : "derecha"} tam={12} className="paso-mas" />}
      {abierto && p.detalle && <pre className="paso-detalle" onClick={(x) => x.stopPropagation()}>{p.detalle}</pre>}
    </div>
  );
});

export const BloqueTramo = memo(function BloqueTramo({ tr, t0, plegado = false }: { tr: Tramo; t0: number; plegado?: boolean }) {
  const centrar = usar((s) => s.centrar);
  const [abierto, setAbierto] = useState(!plegado);
  useEffect(() => setAbierto(!plegado), [plegado]);
  const c = CATEGORIAS[tr.categoria];
  const resumen = tr.pasos.filter((p) => p.tono === "ok").length;
  return (
    <section className={`tramo ${tr.estado}`}>
      <header className="tramo-cabeza" onClick={() => setAbierto(!abierto)}>
        <span className={`mini-icono t-${c.tono}`}><Icono n={c.icono} tam={13} /></span>
        <b className="crece">{tr.nombre}</b>
        {tr.estado === "activo" ? <span className="pastilla azul diminuto"><span className="girando" />en curso</span> : tr.estado === "fallo" ? <span className="pastilla rojo diminuto">falló</span> : null}
        {tr.pasos.length > 0 && <span className="texto-3 diminuto">{tr.pasos.length} paso{tr.pasos.length > 1 ? "s" : ""}{resumen ? ` · ${resumen} ✓` : ""}</span>}
        {tr.ms !== undefined && <span className="texto-3 diminuto mono">{segundos(tr.ms)}</span>}
        <button className="boton-mini" title="Ver en el diagrama" onClick={(e) => { e.stopPropagation(); centrar(tr.nodo); }}><Icono n="mira" tam={12} /></button>
        <Icono n={abierto ? "abajo" : "derecha"} tam={12} className="texto-3" />
      </header>
      {abierto && tr.pasos.length > 0 && (
        <div className="tramo-pasos">
          {tr.pasos.map((p) => <FilaPaso key={p.id} p={p} t0={t0} />)}
        </div>
      )}
    </section>
  );
});

const COLOR_FUENTE: Record<string, string> = { algoritmo: "azul", ia: "violeta", registro: "turquesa", regla: "rosa", compuerta: "ambar", a_la_ia: "ambar", sin_resolver: "rojo", buscando: "gris" };

function vacio(v: unknown): boolean {
  return v === undefined || v === null || v === "";
}

export function TablaSolicitud({ campos, compacta = false }: { campos: EstadoCampo[]; compacta?: boolean }) {
  if (!campos.length) return <div className="vacio chico">Los campos aparecen aquí a medida que el algoritmo y la IA los encuentran.</div>;
  return (
    <div className={`solicitud ${compacta ? "compacta" : ""}`}>
      {campos.map((c) => (
        <motion.div key={c.campo} layout="position" className={`campo-fila f-${c.fuente}`} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
          <span className="campo-estado">
            {c.fuente === "sin_resolver" ? <Icono n="cerrar" tam={12} strokeWidth={2.4} /> : c.fuente === "compuerta" ? <Icono n="escudo" tam={12} /> : c.fuente === "a_la_ia" || c.fuente === "buscando" ? <Icono n="reloj" tam={12} /> : <Icono n="ok" tam={12} strokeWidth={2.4} />}
          </span>
          <span className="campo-nombre">{c.nombre}{c.obligatorio && <i title="Obligatorio para radicar">*</i>}</span>
          <span className="campo-valor mono" title={c.nota}>
            {!vacio(c.valor) ? String(c.valor) : c.fuente === "compuerta" ? <span className="texto-3">vacío{c.antes ? ` · tenía ${c.antes}` : ""}</span> : <span className="texto-3">{c.nota || "—"}</span>}
          </span>
          <span className="campo-meta">
            <span className={`ficha tono-${COLOR_FUENTE[c.fuente]}`}>{NOMBRE_FUENTE[c.fuente]}</span>
            {c.confianza !== undefined && <span className="barra-conf mini" title={`${Math.round(c.confianza * 100)} %`}><i style={{ width: `${Math.round(c.confianza * 100)}%` }} /></span>}
            {c.reparo && <span className={`ficha ${c.reparo.endsWith("corregido") ? "tono-ambar" : "tono-rojo"}`} title={c.reparoDetalle ?? "Reparo de la compuerta"}>Compuerta: {c.reparo}</span>}
          </span>
        </motion.div>
      ))}
    </div>
  );
}

/** Para la vista de logs: cualquier evento en una línea. */
export function LineaDeTiempo({ eventos, mostrarCaso = false }: { eventos: Evento[]; t0?: number; mostrarCaso?: boolean }) {
  const catalogo = usar((s) => s.catalogo);
  const corridas = usar((s) => s.corridas);
  return (
    <div className="pasos-planos">
      {eventos.map((e, i) => {
        const d = describir(e, catalogo);
        if (!d) return null;
        return (
          <div key={i} className="fila-log">
            <span className="paso-t mono">{new Date(e.t * 1000).toLocaleTimeString("es-CO", { hour12: false })}</span>
            {mostrarCaso && e.corrida ? <span className="ficha">{corridas[e.corrida]?.caso ?? ""}</span> : null}
            <div className="crece"><FilaPaso p={{ ...d, id: i, t: e.t }} t0={e.t} /></div>
          </div>
        );
      })}
    </div>
  );
}

// --------------------------------------------------------------------- dock

export function Dock({ plegado, alternar }: { plegado: boolean; alternar: () => void }) {
  const catalogo = usar((s) => s.catalogo);
  const corrida = usar((s) => (s.corridaVisible ? s.corridas[s.corridaVisible] : undefined));
  const orden = usar((s) => s.orden);
  const corridas = usar((s) => s.corridas);
  const verCorrida = usar((s) => s.verCorrida);
  const enfocar = usar((s) => s.enfocar);
  const irA = usar((s) => s.irA);
  const [vista, setVista] = useState<"vivo" | "logs" | "consola">("vivo");
  const [seguir, setSeguir] = useState(true);
  const [filtro, setFiltro] = useState("");
  const caja = useRef<HTMLDivElement>(null);

  const tramos = useMemo(() => narrar(catalogo, corrida), [catalogo, corrida]);
  const campos = useMemo(() => solicitud(corrida), [corrida]);
  const t0 = corrida?.eventos[0]?.t ?? 0;
  const lineas = useMemo(() => {
    const ev = corrida?.eventos ?? [];
    const q = filtro.trim().toLowerCase();
    const base = vista === "consola" ? ev.filter((e) => e.tipo === "consola") : ev.filter((e) => e.tipo !== "consola");
    return q ? base.filter((e) => JSON.stringify(e).toLowerCase().includes(q)) : base;
  }, [corrida, vista, filtro]);

  useEffect(() => {
    if (!seguir || !caja.current) return;
    const el = caja.current;
    requestAnimationFrame(() => el.scrollTo({ top: el.scrollHeight, behavior: "smooth" }));
  }, [tramos, lineas, seguir]);

  const r = corrida?.resultado;
  const accion = r ? String(r.accion) : "";
  const pestanas = [
    { id: "vivo" as const, nombre: "Proceso en vivo", icono: "rayo" as const },
    { id: "logs" as const, nombre: "Logs", icono: "logs" as const, n: corrida ? corrida.eventos.filter((e) => e.tipo !== "consola").length : 0 },
    { id: "consola" as const, nombre: "Consola", icono: "terminal" as const, n: corrida ? corrida.eventos.filter((e) => e.tipo === "consola").length : 0 },
  ];
  return (
    <section className="dock">
      <header className="dock-cabeza">
        <nav className="pestanas" role="tablist">
          {pestanas.map((p) => (
            <button key={p.id} role="tab" aria-selected={vista === p.id} className={vista === p.id ? "activa" : ""} onClick={() => { setVista(p.id); if (plegado) alternar(); }}>
              <Icono n={p.icono} tam={13} />{p.nombre}{p.n ? <span className="contador">{p.n}</span> : null}
              {vista === p.id && <motion.i layoutId="pestana-dock" className="pestana-linea" />}
            </button>
          ))}
        </nav>
        <span className="espacio" />
        {corrida && (
          <span className={`pastilla ${r ? (TONO_DESENLACE[accion] ?? "") : "azul"}`}>
            {!r && <span className="girando" />}
            {r ? (NOMBRES_DESENLACE[accion] ?? accion) : corrida.estado === "en_cola" ? "En cola" : "Procesando"}
          </span>
        )}
        <select className="campo chico-campo" value={corrida?.id ?? ""} onChange={(e) => verCorrida(e.target.value || null)} aria-label="Corrida">
          <option value="">Elegir corrida…</option>
          {orden.slice(0, 120).map((id) => {
            const c = corridas[id];
            if (!c) return null;
            return <option key={id} value={id}>{c.caso} · {c.resultado ? (NOMBRES_DESENLACE[String(c.resultado.accion)] ?? String(c.resultado.accion)) : c.estado === "en_cola" ? "en cola" : "procesando"}</option>;
          })}
        </select>
        {corrida && <button className="boton chico" onClick={() => irA("adjuntos")} title="Cada adjunto hoja por hoja: imagen, texto, búsquedas y origen de cada dato"><Icono n="documento" tam={13} />Ver hojas</button>}
        {corrida && <button className="boton chico" onClick={() => enfocar({ tipo: "corrida", id: corrida.id })}>Resultado</button>}
        {vista !== "vivo" && <div className="campo-icono filtro-dock"><Icono n="buscar" tam={13} /><input className="campo chico-campo" placeholder="Filtrar" value={filtro} onChange={(e) => setFiltro(e.target.value)} /></div>}
        <button className={`boton-mini ${seguir ? "activo" : ""}`} title="Seguir al final" onClick={() => setSeguir(!seguir)}><Icono n="abajo" tam={14} /></button>
        <button className="boton-mini" title={plegado ? "Desplegar panel" : "Plegar panel"} onClick={alternar}><Icono n={plegado ? "expandir" : "menos"} tam={14} /></button>
      </header>
      {!plegado && (
        <div className="dock-cuerpo">
          {!corrida ? (
            <div className="vacio grande">
              <Icono n="rayo" tam={26} />
              <p>Selecciona correos en la bandeja y pulsa <b>Procesar</b>. Aquí verás, etapa por etapa, qué busca el motor, qué encuentra, qué decide la IA y cómo queda la solicitud.</p>
            </div>
          ) : vista === "vivo" ? (
            <div className="vivo">
              <div className="vivo-tramos" ref={caja} onWheel={() => setSeguir(false)}>
                {tramos.map((tr, i) => <BloqueTramo key={tr.id} tr={tr} t0={t0} plegado={tramos.length > 6 && i < tramos.length - 4 && tr.pasos.length > 6} />)}
                {r && (
                  <div className={`desenlace tono-${TONO_DESENLACE[accion] ?? "gris"}`}>
                    <Icono n={accion === "radicado" ? "ok" : "bandera"} tam={16} />
                    <div className="crece">
                      <b>{NOMBRES_DESENLACE[accion] ?? accion}</b>
                      <span>{accion === "radicado" ? `Radicado ${String(r.radicado ?? "")} (simulado)` : String(r.motivo ?? "")}</span>
                    </div>
                    <span className="mono">{String(r.segundos ?? "")} s</span>
                  </div>
                )}
              </div>
              <aside className="vivo-solicitud">
                <h3>La solicitud <span>{campos.filter((c) => c.valor !== undefined && c.fuente !== "a_la_ia").length}/{campos.length}</span></h3>
                <TablaSolicitud campos={campos} compacta />
              </aside>
            </div>
          ) : vista === "consola" ? (
            <pre className="consola" ref={caja as unknown as React.RefObject<HTMLPreElement>}>{lineas.map((e) => String(e.linea)).join("\n") || "Sin salida de consola."}</pre>
          ) : (
            <div className="logs-dock" ref={caja}>
              {lineas.map((e, i) => {
                const d = describir(e, catalogo);
                if (!d) return null;
                return <FilaPaso key={i} p={{ ...d, id: i, t: e.t }} t0={t0} />;
              })}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
