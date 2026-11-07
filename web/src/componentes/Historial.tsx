import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { pedir, usar } from "../estado.ts";
import { useSubruta } from "../rutas.ts";
import { Icono } from "../iconos.tsx";
import { motivoLegible } from "../progreso.ts";
import { ESTADOS, duracion, fechaHora, hace, iconoDePaso, nombreResultado, origenLegible, ramaLegible, type EstadoLegible } from "../lenguaje.ts";
import type { ArchivoDeEjecucion, DetalleEjecucion, FilaHistorial, RespuestaHistorial } from "../tipos.ts";
import { Selector } from "./Selector.tsx";
import { AnimatePresence } from "motion/react";
import { CapsulaArchivo, VisorArchivo, peso } from "./Archivos.tsx";
import "./probar.css";
import { marcarPaso } from "../guia.ts";

type Rango = "hoy" | "7" | "30" | "todo";
const RANGOS: { id: Rango; nombre: string }[] = [
  { id: "hoy", nombre: "Hoy" },
  { id: "7", nombre: "Últimos 7 días" },
  { id: "30", nombre: "Últimos 30 días" },
  { id: "todo", nombre: "Todo" },
];

function desdeDe(r: Rango): string {
  if (r === "todo") return "";
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  if (r !== "hoy") d.setDate(d.getDate() - (Number(r) - 1));
  return d.toISOString();
}

/** La marca de cómo quedó una ejecución: color y forma, nunca solo color. */
export function MarcaEstado({ estado, grande = false }: { estado: EstadoLegible; grande?: boolean }) {
  const e = ESTADOS[estado];
  return (
    <span className={`pr-marca tono-${e.tono} ${grande ? "grande" : ""}`} title={e.nombre} aria-label={e.nombre}>
      {estado === "en_curso" ? <span className="girando" /> : <Icono n={e.icono} tam={grande ? 18 : 14} strokeWidth={2.2} />}
    </span>
  );
}

export function VistaHistorial() {
  const flujo = usar((s) => s.flujo);
  const info = usar((s) => s.flujos.find((f) => f.id === s.flujo));
  const irA = usar((s) => s.irA);
  const foco = usar((s) => s.historialFoco);
  const ultima = usar((s) => s.orden[0]);
  const deCodigo = info?.tipo === "codigo";
  const [q, setQ] = useState("");
  const [qListo, setQListo] = useState("");
  const [estado, setEstado] = useState<EstadoLegible | "">("");
  const [desenlace, setDesenlace] = useState("");
  const [rango, setRango] = useState<Rango>("30");
  const [limite, setLimite] = useState(50);
  const [datos, setDatos] = useState<RespuestaHistorial | null>(null);
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);
  // La ejecución abierta vive en la URL: /flujos/<flujo>/historial/<ejecución>
  const [elegida, setElegida] = useSubruta();
  const flujoAntes = useRef(flujo);
  const pedido = useRef(0);

  useEffect(() => {
    const t = setTimeout(() => setQListo(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  // Al cambiar de flujo empieza de cero.
  useEffect(() => {
    if (flujoAntes.current === flujo) return;
    flujoAntes.current = flujo;
    setElegida(null);
    setDesenlace("");
    setLimite(50);
  }, [flujo]);

  // Desde una notificación, el inicio o Vera: abre esa ejecución sin filtros que la escondan.
  useEffect(() => {
    if (!foco) return;
    setElegida(foco.corrida);
    setEstado("");
    setDesenlace("");
    setQ("");
    setRango("todo");
  }, [foco]);

  const cargar = useCallback(async () => {
    const n = ++pedido.current;
    setCargando(true);
    const p = new URLSearchParams({ flujo, limite: String(limite) });
    if (estado) p.set("estado", estado);
    if (desenlace) p.set("desenlace", desenlace);
    if (qListo.trim()) p.set("q", qListo.trim());
    const desde = desdeDe(rango);
    if (desde) p.set("desde", desde);
    try {
      const r = await pedir<RespuestaHistorial>(`/api/historial?${p}`);
      if (n !== pedido.current) return;
      setDatos(r);
      setError("");
    } catch (e) {
      if (n === pedido.current) setError((e as Error).message);
    } finally {
      if (n === pedido.current) setCargando(false);
    }
  }, [flujo, estado, desenlace, qListo, rango, limite]);

  useEffect(() => {
    void cargar();
  }, [cargar, ultima]);

  // Mientras algo está en curso, la lista se refresca sola.
  const hayEnCurso = datos?.corridas.some((c) => c.estado === "en_curso");
  useEffect(() => {
    if (!hayEnCurso) return;
    const t = setInterval(() => void cargar(), 4000);
    return () => clearInterval(t);
  }, [hayEnCurso, cargar]);

  const facetas = datos?.facetas;
  const totalRango = facetas ? Object.values(facetas.estados).reduce((a, b) => a + b, 0) : 0;
  const opcionesResultado = useMemo(
    () => [{ valor: "", etiqueta: "Cualquier resultado" }, ...Object.entries(facetas?.desenlaces ?? {}).sort((a, b) => b[1] - a[1]).map(([d, n]) => ({ valor: d, etiqueta: `${nombreResultado(d, deCodigo)} (${n})`, texto: nombreResultado(d, deCodigo) }))],
    [facetas, deCodigo],
  );
  const hayFiltros = Boolean(estado || desenlace || q.trim() || rango !== "30");
  const limpiar = () => {
    setEstado("");
    setDesenlace("");
    setQ("");
    setRango("30");
  };

  return (
    <div className="vista pr-vista pr-historial">
      <header className="vista-cabeza">
        <div className="crece">
          <h1>Historial</h1>
          <p className="texto-2">Cada vez que «{info?.nombre ?? flujo}» corrió: qué entró, qué hizo cada paso y cómo terminó. Abre una ejecución para leerla o volver a correrla.</p>
        </div>
        {deCodigo && <button className="boton" onClick={() => irA("corridas")} title="Porcentajes, tiempos y la comparación con lo que hizo producción con los mismos correos"><Icono n="corridas" tam={14} />Resumen y comparación con producción</button>}
      </header>

      <div className="pr-barra-filtros" role="search">
        <div className="pr-buscar">
          <Icono n="buscar" tam={15} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar por asunto, dato, motivo o número" aria-label="Buscar en el historial" />
          {q && <button aria-label="Borrar la búsqueda" onClick={() => setQ("")}><Icono n="cerrar" tam={12} /></button>}
        </div>
        <div className="filtros" role="group" aria-label="Cuándo">
          {RANGOS.map((r) => (
            <button key={r.id} className={rango === r.id ? "activo" : ""} aria-pressed={rango === r.id} onClick={() => setRango(r.id)}>{r.nombre}</button>
          ))}
        </div>
        <Selector etiqueta="Resultado" valor={desenlace} opciones={opcionesResultado} alCambiar={setDesenlace} tamano="chico" ancho={240} className="pr-selector" />
      </div>
      <div className="filtros pr-estados" role="group" aria-label="Cómo terminó">
        <button className={!estado ? "activo" : ""} aria-pressed={!estado} onClick={() => setEstado("")}>Todas <span>{totalRango}</span></button>
        {(Object.keys(ESTADOS) as EstadoLegible[]).map((k) => (
          <button key={k} className={estado === k ? "activo" : ""} aria-pressed={estado === k} onClick={() => setEstado(estado === k ? "" : k)}>
            <i className={`pr-punto tono-${ESTADOS[k].tono}`} aria-hidden />{ESTADOS[k].plural} <span>{facetas?.estados[k] ?? 0}</span>
          </button>
        ))}
        {hayFiltros && <button className="pr-limpiar" onClick={limpiar}><Icono n="deshacer" tam={12} />Quitar filtros</button>}
      </div>

      <div className={`pr-hist ${elegida ? "con-detalle" : ""}`}>
        <section className="pr-hist-lista" aria-label="Ejecuciones" aria-busy={cargando}>
          {error ? (
            <EstadoPantalla icono="alerta" tono="rojo" titulo="No se pudo cargar el historial" texto={error} accion={<button className="boton" onClick={() => void cargar()}><Icono n="deshacer" tam={13} />Intentar de nuevo</button>} />
          ) : !datos ? (
            <Cargando filas={6} />
          ) : !datos.corridas.length ? (
            datos.en_memoria === 0 ? (
              <EstadoPantalla icono="historial" titulo={`«${info?.nombre ?? flujo}» todavía no ha corrido`} texto="Cuando lo corras, cada ejecución aparece aquí con lo que entró, lo que hizo cada paso y cómo terminó." accion={<button className="boton primario" onClick={() => { irA("flujo"); window.setTimeout(() => window.dispatchEvent(new CustomEvent("positiva:control", { detail: { accion: "editor", op: "abrir_correr" } })), 400); }}><Icono n="play" tam={13} />Correr el flujo</button>} />
            ) : (
              <EstadoPantalla icono="filtro" titulo="Ninguna ejecución coincide" texto="Hay ejecuciones, pero ninguna pasa estos filtros." accion={<button className="boton" onClick={limpiar}>Quitar filtros</button>} />
            )
          ) : (
            <>
              <p className="pr-cuenta texto-3">{datos.total.toLocaleString("es-CO")} ejecución{datos.total === 1 ? "" : "es"}{cargando ? " · actualizando…" : ""}</p>
              <ul className="pr-filas">
                {datos.corridas.map((c) => <FilaEjecucion key={c.id} c={c} deCodigo={deCodigo} elegida={elegida === c.id} elegir={() => setElegida(c.id)} />)}
              </ul>
              {datos.total > datos.corridas.length && (
                <button className="boton pr-mas" onClick={() => setLimite((l) => l + 50)}>Ver 50 más ({(datos.total - datos.corridas.length).toLocaleString("es-CO")} quedan)</button>
              )}
            </>
          )}
        </section>
        {elegida && (
          <DetalleDeEjecucion
            key={elegida}
            id={elegida}
            cerrar={() => setElegida(null)}
            alReintentar={(nueva) => { setElegida(nueva); void cargar(); }}
          />
        )}
      </div>
    </div>
  );
}

function FilaEjecucion({ c, deCodigo, elegida, elegir }: { c: FilaHistorial; deCodigo: boolean; elegida: boolean; elegir: () => void }) {
  const motivo = c.motivo ? motivoLegible(c.motivo) : "";
  return (
    <li>
      <button className={`pr-fila ${elegida ? "elegida" : ""}`} onClick={elegir} aria-current={elegida || undefined}>
        <MarcaEstado estado={c.estado} />
        <span className="pr-fila-cuerpo">
          <b>{c.titulo}</b>
          <span className="pr-fila-meta">
            <span className={`pr-resultado tono-${ESTADOS[c.estado].tono}`}>{c.estado === "en_curso" ? "En curso" : c.continuada_en ? "Aprobación decidida" : nombreResultado(c.desenlace, deCodigo)}</span>
            {motivo && <span className="pr-fila-motivo">{motivo}</span>}
          </span>
        </span>
        <span className="pr-fila-cuando">
          <span title={fechaHora(c.creada)}>{hace(c.creada)}</span>
          <small>{[origenLegible(c.origen), duracion(c.segundos)].filter(Boolean).join(", ")}</small>
        </span>
      </button>
    </li>
  );
}

// ------------------------------------------------------------------ detalle

function DetalleDeEjecucion({ id, cerrar, alReintentar }: { id: string; cerrar: () => void; alReintentar: (nueva: string) => void }) {
  const [d, setD] = useState<DetalleEjecucion | null>(null);
  const [error, setError] = useState("");
  const [confirmar, setConfirmar] = useState(false);
  const [reintentando, setReintentando] = useState(false);
  const verCorrida = usar((s) => s.verCorrida);
  const enfocar = usar((s) => s.enfocar);
  const irA = usar((s) => s.irA);
  const cambiarFlujo = usar((s) => s.cambiarFlujo);
  const avisar = usar((s) => s.avisar);
  const caja = useRef<HTMLElement>(null);

  const cargar = useCallback(async () => {
    try {
      setD(await pedir<DetalleEjecucion>(`/api/historial/${encodeURIComponent(id)}`));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    void cargar();
    marcarPaso("revisar");
  }, [cargar]);
  useEffect(() => {
    caja.current?.focus({ preventScroll: true });
  }, [id]);
  useEffect(() => {
    if (d?.corrida.estado !== "en_curso") return;
    const t = setInterval(() => void cargar(), 2000);
    return () => clearInterval(t);
  }, [d?.corrida.estado, cargar]);
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => e.key === "Escape" && !(e.target as HTMLElement).closest("input, textarea, .selector-lista") && cerrar();
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [cerrar]);

  const reintentar = async () => {
    setReintentando(true);
    try {
      const r = await pedir<{ id: string }>(`/api/historial/${encodeURIComponent(id)}/reintentar`, { method: "POST" });
      marcarPaso("correr");
      avisar("Reintento en marcha: es la ejecución que ves ahora", "info");
      setConfirmar(false);
      alReintentar(r.id);
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setReintentando(false);
    }
  };

  const verEnLienzo = async () => {
    if (!d) return;
    await cambiarFlujo(d.flujo.id);
    await verCorrida(id);
    enfocar({ tipo: "corrida", id });
    irA("flujo");
  };

  const deCodigo = d?.flujo.tipo === "codigo";
  return (
    <article ref={caja} className="pr-detalle" tabIndex={-1} aria-label="Detalle de la ejecución">
      <header className="pr-detalle-cabeza">
        <button className="boton-mini pr-volver" onClick={cerrar} aria-label="Volver a la lista" title="Volver a la lista (Esc)"><Icono n="atras" tam={16} /></button>
        {d ? (
          <>
            <MarcaEstado estado={d.corrida.estado} grande />
            <div className="crece">
              <h2>{d.corrida.titulo}</h2>
              <p className="texto-3 chico">{fechaHora(d.corrida.creada)}{d.corrida.segundos !== null ? `, tardó ${duracion(d.corrida.segundos)}` : ""}{d.corrida.origen ? `. ${origenLegible(d.corrida.origen)}` : ""}</p>
            </div>
          </>
        ) : <div className="crece"><h2>Ejecución</h2></div>}
        <button className="boton-mini pr-cerrar" onClick={cerrar} aria-label="Cerrar el detalle" title="Cerrar (Esc)"><Icono n="cerrar" tam={15} /></button>
      </header>

      {error ? (
        <EstadoPantalla icono="alerta" tono="rojo" titulo="No se pudo abrir esta ejecución" texto={error} accion={<button className="boton" onClick={() => void cargar()}>Intentar de nuevo</button>} />
      ) : !d ? (
        <Cargando filas={4} />
      ) : (
        <div className="pr-detalle-cuerpo">
          <div className="pr-acciones">
            {confirmar ? (
              <div className="pr-confirmar" role="group" aria-label="Confirmar el reintento">
                <span>¿Volver a correr con {d.entrada.tipo === "correo" ? "el mismo correo" : "la misma entrada"}?</span>
                <button className="boton primario chico" disabled={reintentando} onClick={() => void reintentar()}>{reintentando ? <span className="girando" /> : <Icono n="play" tam={12} />}Sí, reintentar</button>
                <button className="boton chico" onClick={() => setConfirmar(false)}>Cancelar</button>
              </div>
            ) : (
              <>
                <button className="boton primario chico" disabled={!d.se_puede_reintentar} title={d.se_puede_reintentar ? "Corre de nuevo con la misma entrada o el mismo correo" : "Se puede reintentar cuando termine"} onClick={() => setConfirmar(true)}><Icono n="rehacer" tam={13} />Reintentar</button>
                <button className="boton chico" onClick={() => void verEnLienzo()} title="Abre el flujo con esta ejecución marcada paso a paso"><Icono n="flujo" tam={13} />Ver en el lienzo</button>
                {deCodigo && <button className="boton chico fantasma" onClick={() => { verCorrida(id); irA("adjuntos"); }}><Icono n="documento" tam={13} />Ver adjuntos</button>}
              </>
            )}
          </div>

          <Seccion titulo="Por qué terminó así" icono="bandera">
            <Porque d={d} />
            {d.aprobacion && (
              <div className="pr-aprobacion">
                <Icono n="persona" tam={15} />
                <p className="crece">
                  {d.aprobacion.viene_de
                    ? <>Siguió después de que una persona decidiera{d.aprobacion.titulo ? <> «{d.aprobacion.titulo}»</> : null}.</>
                    : d.aprobacion.continuada_en
                      ? <>Se detuvo a esperar una aprobación{d.aprobacion.titulo ? <> («{d.aprobacion.titulo}»)</> : null}. Ya se decidió y el flujo siguió en otra ejecución.</>
                      : <>Está esperando que una persona decida{d.aprobacion.titulo ? <> «{d.aprobacion.titulo}»</> : null}.</>}
                </p>
                {d.aprobacion.viene_de && <button className="boton chico" onClick={() => alReintentar(d.aprobacion!.viene_de!)}>Ver la que pidió la aprobación</button>}
                {d.aprobacion.continuada_en && <button className="boton chico" onClick={() => alReintentar(d.aprobacion!.continuada_en!)}>Ver la que siguió</button>}
                {!d.aprobacion.viene_de && !d.aprobacion.continuada_en && <button className="boton chico primario" onClick={() => irA("aprobaciones")}>Ir a Aprobaciones</button>}
              </div>
            )}
          </Seccion>

          <Seccion titulo="Qué entró" icono={d.entrada.tipo === "correo" ? "correo" : "editar"}>
            <p className="pr-entrada-resumen">{d.entrada.resumen}</p>
            <Campos campos={d.entrada.campos} vacio="No trajo datos de entrada." />
          </Seccion>

          <Seccion titulo={`Qué hizo cada paso (${d.pasos.length})`} icono="ruta">
            {d.pasos.length ? (
              <ol className="pr-pasos">
                {d.pasos.map((p, i) => <PasoLegible key={`${p.id}-${i}`} p={p} n={i + 1} />)}
              </ol>
            ) : <p className="texto-3 chico">{d.corrida.estado === "en_curso" ? "Todavía no empieza: está esperando turno." : "Esta ejecución no registró pasos."}</p>}
          </Seccion>

          <Seccion titulo="Qué salió" icono="bandeja">
            {d.salida.respuesta && <blockquote className="pr-respuesta">{d.salida.respuesta}</blockquote>}
            <Campos campos={d.salida.datos} vacio={d.corrida.estado === "en_curso" ? "Aparece cuando termine." : "No dejó datos de salida."} />
            {d.salida.escrituras.length > 0 && (
              <div className="pr-escrituras">
                <h4>Lo que habría escrito afuera</h4>
                <ul>
                  {d.salida.escrituras.map((w, i) => (
                    <li key={i}><Icono n={w.retenida ? "candado" : "enviar"} tam={13} /><span className="crece">{w.que.replace(/_/g, " ")}{w.destino ? <span className="texto-3"> a {w.destino}</span> : null}{w.asunto ? <span className="texto-3"> · «{w.asunto}»</span> : null}{w.adjuntos?.length ? <span className="texto-3 diminuto"> · con {w.adjuntos.map((a) => `${a.nombre} (${peso(a.tamano)})`).join(", ")}</span> : null}</span><span className={`ficha ${w.retenida ? "tono-ambar" : "tono-verde"}`}>{w.retenida ? "Registrado, no enviado" : "Enviado"}</span></li>
                  ))}
                </ul>
              </div>
            )}
            {(d.salida.archivos?.length ?? 0) > 0 && <ArchivosDeEjecucion corrida={d.corrida.id} flujo={d.flujo.id} archivos={d.salida.archivos!} />}
            {d.salida.avisos.length > 0 && <ul className="pr-avisos">{d.salida.avisos.map((a, i) => <li key={i}><Icono n="info" tam={13} />{a}</li>)}</ul>}
          </Seccion>
          {d.consumo && d.consumo.tokens > 0 && <p className="texto-3 diminuto pr-consumo">Usó la IA {d.consumo.consultas_ia ? `${d.consumo.consultas_ia} ${d.consumo.consultas_ia === 1 ? "vez" : "veces"} ` : ""}({d.consumo.tokens.toLocaleString("es-CO")} unidades de texto, «tokens»).</p>}
        </div>
      )}
    </article>
  );
}

/** Los archivos que salieron de una ejecución: cápsulas con vista previa en su lugar (imagen, PDF; Word y Excel como texto) y descarga. */
function ArchivosDeEjecucion({ corrida, flujo, archivos }: { corrida: string; flujo: string; archivos: ArchivoDeEjecucion[] }) {
  const [visor, setVisor] = useState<ArchivoDeEjecucion | null>(null);
  return (
    <div className="pr-escrituras hi-archivos">
      <h4>Archivos que salieron ({archivos.length})</h4>
      <div className="arch-capsulas">
        {archivos.map((a) => (
          <span key={a.id} className="hi-archivo">
            <CapsulaArchivo flujo={flujo} a={a} {...(a.disponible ? { alAbrir: () => setVisor(a) } : {})} {...(!a.disponible ? { error: "ya no está en el flujo" } : {})} />
            {a.entregado && <span className="hi-entregado" title="Lo entrega la salida del flujo">entregado</span>}
            {a.disponible && (
              <a className="hi-archivo-descargar" href={`/api/corridas/${encodeURIComponent(corrida)}/archivos/${a.id}?descargar=1`} download={a.nombre} aria-label={`Descargar ${a.nombre}`} title="Descargar">
                <Icono n="descargar" tam={14} />
              </a>
            )}
          </span>
        ))}
      </div>
      <AnimatePresence>{visor && <VisorArchivo key="visor" flujo={flujo} a={{ ...visor, sha256: "", subido: 0 }} cerrar={() => setVisor(null)} />}</AnimatePresence>
    </div>
  );
}

function Porque({ d }: { d: DetalleEjecucion }) {
  const deCodigo = d.flujo.tipo === "codigo";
  const p = d.porque;
  if (p.estado === "en_curso") return <p className="pr-porque">Todavía está corriendo{p.paso_final ? `: va en «${p.paso_final}»` : ""}. Esta página se actualiza sola.</p>;
  const resultado = nombreResultado(p.desenlace, deCodigo);
  const motivo = p.motivo ? motivoLegible(p.motivo) : "";
  return (
    <div className={`pr-porque tono-${ESTADOS[p.estado].tono}`}>
      <p><b>{resultado}.</b> {p.estado === "fallo"
        ? <>Se detuvo{p.paso_final ? <> en el paso «{p.paso_final}»</> : null}{p.fallo || motivo ? <>: {p.fallo || motivo}</> : "."}</>
        : <>{p.paso_final ? <>Terminó en el paso «{p.paso_final}».</> : "Terminó."}{motivo && motivo !== p.paso_final ? <> {motivo.charAt(0).toUpperCase() + motivo.slice(1)}{/[.!?]$/.test(motivo) ? "" : "."}</> : null}</>}</p>
      {p.explicacion && p.explicacion !== p.motivo && <p className="texto-2 chico">{p.explicacion}</p>}
      {p.estado === "fallo" && <p className="texto-3 chico">Si fue algo pasajero (una conexión que no respondió), «Reintentar» lo vuelve a correr igual. Si se repite, abre el paso en el lienzo para revisarlo.</p>}
      {p.estado === "revisar" && <p className="texto-3 chico">Quedó para que una persona lo revise: así está diseñado el flujo cuando falta algo o hay dudas.</p>}
    </div>
  );
}

function PasoLegible({ p, n }: { p: DetalleEjecucion["pasos"][number]; n: number }) {
  const [abierto, setAbierto] = useState(false);
  const hay = p.datos.length > 0 || p.notas.length > 1;
  return (
    <li className={`pr-paso ${p.estado}`}>
      <span className="pr-paso-n" aria-hidden>{p.estado === "fallo" ? <Icono n="alerta" tam={12} strokeWidth={2.4} /> : p.estado === "en_curso" ? <span className="girando" /> : n}</span>
      <div className="pr-paso-cuerpo">
        <div className="pr-paso-linea">
          <Icono n={iconoDePaso(p.tipo)} tam={14} className="texto-3" />
          <b>{p.nombre}</b>
          {p.veces > 1 && <span className="ficha">{p.veces} veces</span>}
          <span className="espacio" />
          {p.ms > 0 && <span className="texto-3 diminuto">{p.ms < 1000 ? `${p.ms} ms` : duracion(p.ms / 1000)}</span>}
        </div>
        {p.notas[0] && <p className="chico">{p.notas[0]}</p>}
        {p.fallo && <p className="chico pr-fallo">{p.fallo}</p>}
        {p.salida && <p className="texto-3 diminuto">{ramaLegible(p.salida)}</p>}
        {hay && <button className="enlace" aria-expanded={abierto} onClick={() => setAbierto(!abierto)}>{abierto ? "Ocultar lo que dejó" : "Ver lo que dejó"}</button>}
        {abierto && (
          <div className="pr-paso-mas">
            {p.notas.slice(1).map((x, i) => <p key={i} className="chico texto-2">{x}</p>)}
            <Campos campos={p.datos} vacio="" />
          </div>
        )}
      </div>
    </li>
  );
}

function Seccion({ titulo, icono, children }: { titulo: string; icono: Parameters<typeof Icono>[0]["n"]; children: React.ReactNode }) {
  return (
    <section className="pr-seccion">
      <h3><Icono n={icono} tam={15} />{titulo}</h3>
      {children}
    </section>
  );
}

function Campos({ campos, vacio }: { campos: { nombre: string; valor: string }[]; vacio: string }) {
  if (!campos.length) return vacio ? <p className="texto-3 chico">{vacio}</p> : null;
  return (
    <dl className="pr-campos">
      {campos.map((c, i) => (
        <div key={`${c.nombre}-${i}`}><dt>{c.nombre}</dt><dd>{c.valor || <span className="texto-3">vacío</span>}</dd></div>
      ))}
    </dl>
  );
}

// ------------------------------------------------- estados compartidos

export function EstadoPantalla({ icono, titulo, texto, accion, tono }: { icono: Parameters<typeof Icono>[0]["n"]; titulo: string; texto?: React.ReactNode; accion?: React.ReactNode; tono?: "rojo" }) {
  return (
    <div className={`estado-vacio dentro pr-estado ${tono ?? ""}`} role={tono === "rojo" ? "alert" : undefined}>
      <span className="estado-vacio-icono"><Icono n={icono} tam={22} /></span>
      <h2>{titulo}</h2>
      {texto && <p>{texto}</p>}
      {accion}
    </div>
  );
}

export function Cargando({ filas = 4 }: { filas?: number }) {
  return (
    <div className="pr-cargando" aria-label="Cargando" role="status">
      {Array.from({ length: filas }, (_, i) => <span key={i} style={{ animationDelay: `${i * 80}ms` }} />)}
    </div>
  );
}
