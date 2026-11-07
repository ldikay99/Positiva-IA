import { useEffect, useMemo, useState } from "react";
import { AnimatePresence } from "motion/react";
import { pedir } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { usarEditor } from "../editor/almacen.ts";
import { textoJson, type NodoDef } from "../editor/modelo.ts";
import type { ArchivoFlujo, Correo } from "../tipos.ts";
import { segundos } from "../progreso.ts";
import { CapsulaArchivo, VisorArchivo, ZonaArchivos, peso } from "./Archivos.tsx";
import { Codigo, Interruptor } from "./Editores.tsx";
import { Selector } from "./Selector.tsx";
import "./probar.css";

/**
 * «Probar este paso»: corre SOLO este paso (o el flujo hasta él) en simulación, con un archivo
 * arrastrado o pegado, un correo de la correspondencia del flujo, un archivo del flujo o los datos
 * de una ejecución anterior. Nunca escribe afuera ni en la plataforma, ni llama a otros flujos.
 * Lo que produce se puede fijar como datos de prueba para probar los pasos siguientes.
 *
 *   POST /api/flujos/:id/probar-paso · GET/PUT/DELETE /api/flujos/:id/fijados/:nodo
 */

type Fuente = "nada" | "archivo" | "correo" | "flujo" | "ejecucion";

interface ArchivoGenerado { id: string; nombre: string; tipo: string; tamano: number; nodo: string }
interface Escritura { nodo: string; operacion?: string; sistema?: string; destino?: string; motivo?: string; asunto?: string; adjuntos?: { nombre: string; tamano: number }[] }
interface ResultadoPrueba {
  ok: boolean;
  salida?: string | null;
  alcanzado?: boolean;
  error?: string;
  problemas?: string[];
  variables?: Record<string, unknown>;
  muestra?: unknown;
  archivos?: ArchivoGenerado[];
  escrituras?: Escritura[];
  avisos?: string[];
  eventos?: { tipo: string; nodo?: string; titulo?: string; detalle?: string; tono?: string; etapa?: string }[];
  resultado?: { accion?: string; motivo?: string };
  ms?: number;
}
interface Fijado { nodo: string; variables: Record<string, unknown>; salida?: string; fijado: number }
interface FilaHistorial { id: string; titulo: string; creada: number; estado: string }

export function ProbarPaso({ n }: { n: NodoDef }) {
  const flujo = usarEditor((s) => s.flujo);
  const def = usarEditor((s) => s.definicion);
  const [fuente, setFuente] = useState<Fuente>("nada");
  const [subidos, setSubidos] = useState<ArchivoFlujo[]>([]);
  const [correos, setCorreos] = useState<Correo[]>([]);
  const [caso, setCaso] = useState<string | null>(null);
  const [archivosFlujo, setArchivosFlujo] = useState<ArchivoFlujo[]>([]);
  const [delFlujo, setDelFlujo] = useState<string | null>(null);
  const [ejecuciones, setEjecuciones] = useState<FilaHistorial[]>([]);
  const [ejecucion, setEjecucion] = useState<string | null>(null);
  const [hasta, setHasta] = useState(false);
  const [usarFijados, setUsarFijados] = useState(true);
  const [fijados, setFijados] = useState<Fijado[]>([]);
  const [corriendo, setCorriendo] = useState(false);
  const [r, setR] = useState<ResultadoPrueba | null>(null);
  const [error, setError] = useState("");
  const [visor, setVisor] = useState<ArchivoFlujo | null>(null);
  const [aviso, setAviso] = useState("");
  const base = flujo ? `/api/flujos/${encodeURIComponent(flujo)}` : "";
  const disparador = def?.nodos.find((x) => ["entrada_correo", "correo_nuevo", "entrada_manual", "entrada_archivos", "webhook", "programado"].includes(x.tipo));
  const deCorreo = disparador?.tipo === "entrada_correo" || disparador?.tipo === "correo_nuevo";

  const cargarFijados = () => { if (base) void pedir<{ fijados: Fijado[] }>(`${base}/fijados`).then((x) => setFijados(x.fijados)).catch(() => undefined); };
  useEffect(cargarFijados, [base]);
  useEffect(() => {
    if (!base || !flujo) return;
    if (fuente === "correo" && deCorreo) void pedir<Correo[]>(`/api/bandeja?flujo=${encodeURIComponent(flujo)}`).then(setCorreos).catch(() => setCorreos([]));
    if (fuente === "flujo") void pedir<{ archivos: ArchivoFlujo[] }>(`${base}/archivos`).then((x) => setArchivosFlujo(x.archivos)).catch(() => setArchivosFlujo([]));
    if (fuente === "ejecucion") void pedir<{ corridas: FilaHistorial[] }>(`/api/historial?flujo=${encodeURIComponent(flujo)}&limite=30`).then((x) => setEjecuciones(x.corridas)).catch(() => setEjecuciones([]));
  }, [fuente, base, flujo, deCorreo]);

  const propio = fijados.find((f) => f.nodo === n.id);
  const otrosFijados = fijados.filter((f) => f.nodo !== n.id);
  const opcionesFuente = useMemo(() => [
    { id: "nada" as const, nombre: "Sin datos", icono: "datos" as const },
    { id: "archivo" as const, nombre: "Un archivo", icono: "adjunto" as const },
    ...(deCorreo ? [{ id: "correo" as const, nombre: "Un correo", icono: "correo" as const }] : []),
    { id: "flujo" as const, nombre: "Del flujo", icono: "documento" as const },
    { id: "ejecucion" as const, nombre: "Una ejecución", icono: "reloj" as const },
  ], [deCorreo]);

  if (!flujo || !def) return <div className="vacio">Guarda el flujo para poder probar sus pasos.</div>;

  const probar = async () => {
    setCorriendo(true);
    setError("");
    setR(null);
    try {
      const ids = fuente === "archivo" ? subidos.map((a) => a.id) : fuente === "flujo" && delFlujo ? [delFlujo] : [];
      const cuerpo = {
        nodo: n.id, hasta, definicion: def, fijados: usarFijados,
        ...(ids.length ? { archivos: ids } : {}),
        ...(fuente === "correo" && caso ? { caso } : {}),
        ...(fuente === "ejecucion" && ejecucion ? { desde_corrida: ejecucion } : {}),
      };
      setR(await pedir<ResultadoPrueba>(`${base}/probar-paso`, { method: "POST", body: JSON.stringify(cuerpo) }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCorriendo(false);
    }
  };

  const fijar = async () => {
    if (!r?.ok || !r.variables) return;
    try {
      await pedir(`${base}/fijados/${n.id}`, { method: "PUT", body: JSON.stringify({ variables: r.variables, ...(r.salida ? { salida: String(r.salida) } : {}), origen: "probar este paso" }) });
      setAviso("Quedaron como datos de prueba de este paso: los siguientes los usan sin volver a correrlo.");
      cargarFijados();
    } catch (e) {
      setAviso(`No se pudieron fijar: ${(e as Error).message}`);
    }
  };
  const quitarFijado = async () => {
    await pedir(`${base}/fijados/${n.id}`, { method: "DELETE" }).catch(() => undefined);
    setAviso("");
    cargarFijados();
  };

  const listo = fuente === "nada" || (fuente === "archivo" && subidos.length > 0) || (fuente === "correo" && caso) || (fuente === "flujo" && delFlujo) || (fuente === "ejecucion" && ejecucion);
  const pasos = (r?.eventos ?? []).filter((e) => e.tipo === "paso" && e.titulo);

  return (
    <div className="pila probar-paso arch-ambito">
      <p className="texto-2 chico">Corre solo este paso en simulación y mira lo que produce. Nada se envía ni se escribe: lo que escribiría afuera queda retenido.</p>
      <div className="f-campo">
        <span className="f-etiqueta"><span>Con qué datos</span></span>
        <div className="segmentos pp-fuentes" role="radiogroup" aria-label="Con qué datos probar">
          {opcionesFuente.map((o) => (
            <button key={o.id} type="button" role="radio" aria-checked={fuente === o.id} className={`segmento ${fuente === o.id ? "activo" : ""}`} onClick={() => setFuente(o.id)}>
              <Icono n={o.icono} tam={12} />{o.nombre}
            </button>
          ))}
        </div>
      </div>
      {fuente === "archivo" && (
        <ZonaArchivos flujo={flujo} valor={subidos} alCambiar={setSubidos} maximo={10} texto="Arrastra o pega el archivo con el que quieres probar"
          ayuda="Queda en el espacio del flujo y entra a la prueba como si llegara con la ejecución." />
      )}
      {fuente === "correo" && (
        correos.length ? (
          <Selector etiqueta="Correo de prueba" placeholder="Elige un correo de la correspondencia del flujo…" valor={caso}
            opciones={correos.map((c) => ({ valor: c.caso, etiqueta: c.asunto || "(sin asunto)", detalle: c.remitente }))} alCambiar={(v) => setCaso(String(v))} />
        ) : <p className="texto-3 chico">Este flujo aún no tiene correos: sube un .eml o .msg a su bandeja de prueba o tráelos de su buzón.</p>
      )}
      {fuente === "flujo" && (
        archivosFlujo.length ? (
          <Selector etiqueta="Archivo del flujo" placeholder="Elige un archivo del flujo…" valor={delFlujo}
            opciones={archivosFlujo.map((a) => ({ valor: a.id, etiqueta: a.nombre, detalle: peso(a.tamano) }))} alCambiar={(v) => setDelFlujo(String(v))} />
        ) : <p className="texto-3 chico">El flujo no tiene archivos todavía.</p>
      )}
      {fuente === "ejecucion" && (
        ejecuciones.length ? (
          <Selector etiqueta="Ejecución anterior" placeholder="Usa lo que dejó una ejecución anterior…" valor={ejecucion}
            opciones={ejecuciones.map((c) => ({ valor: c.id, etiqueta: c.titulo || c.id, detalle: new Date(c.creada).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) }))}
            alCambiar={(v) => setEjecucion(String(v))} />
        ) : <p className="texto-3 chico">Este flujo no tiene ejecuciones todavía.</p>
      )}
      <div className="f-campo f-interruptor">
        <div className="crece">
          <span className="f-etiqueta"><span>Correr el flujo hasta este paso</span></span>
          <p className="f-ayuda">{hasta ? "Arranca en el disparador y se detiene al terminar este paso." : "Solo este paso, con el contexto del disparador y los datos de prueba."}</p>
        </div>
        <Interruptor valor={hasta} alCambiar={setHasta} etiqueta="Correr hasta este paso" />
      </div>
      {otrosFijados.length > 0 && (
        <div className="f-campo f-interruptor">
          <div className="crece">
            <span className="f-etiqueta"><span>Usar los datos fijados</span></span>
            <p className="f-ayuda">De {otrosFijados.map((f) => def.nodos.find((x) => x.id === f.nodo)?.nombre ?? f.nodo).join(", ")}: esos pasos no se vuelven a correr.</p>
          </div>
          <Interruptor valor={usarFijados} alCambiar={setUsarFijados} etiqueta="Usar los datos fijados" />
        </div>
      )}
      <div className="fila envuelve">
        <button className="boton primario" disabled={corriendo || !listo} onClick={() => void probar()}>
          {corriendo ? <span className="girando" /> : <Icono n="play" tam={13} />}{corriendo ? "Probando…" : hasta ? "Probar hasta este paso" : "Probar este paso"}
        </button>
        {propio && (
          <span className="pastilla azul pp-fijado" title={`Fijado el ${new Date(propio.fijado).toLocaleString("es-CO")}`}>
            Datos fijados<button type="button" className="enlace" onClick={() => void quitarFijado()}>Quitar</button>
          </span>
        )}
      </div>
      {error && <p className="pn-fallo" role="alert"><Icono n="alerta" tam={14} />{error}</p>}
      {r && (
        <section className="seccion pp-resultado" aria-live="polite">
          <h3>Resultado de la prueba</h3>
          <div className="fichas">
            <span className={`pastilla ${r.ok ? "verde" : "rojo"}`}><Icono n={r.ok ? "ok" : "cerrar"} tam={12} />{r.ok ? "Terminó" : "Falló"}{r.ms ? ` · ${segundos(r.ms)}` : ""}</span>
            {r.ok && r.salida && <span className="pastilla azul">salió por «{r.salida === "error" ? "si falla" : r.salida}»</span>}
            {r.ok && r.alcanzado === false && <span className="pastilla">el camino no pasó por este paso</span>}
            {r.resultado?.accion && <span className="pastilla">el flujo terminó: {r.resultado.accion}</span>}
          </div>
          {!r.ok && <p className="pn-fallo"><Icono n="alerta" tam={14} />{r.error}{r.problemas?.length ? `: ${r.problemas.join(" · ")}` : ""}</p>}
          {(r.archivos?.length ?? 0) > 0 && (
            <div className="f-campo">
              <span className="f-etiqueta"><span>Archivos generados</span></span>
              <div className="arch-capsulas">
                {r.archivos!.map((a) => <CapsulaArchivo key={a.id} flujo={flujo} a={a} alAbrir={() => setVisor({ ...a, sha256: "", subido: Date.now() })} />)}
              </div>
            </div>
          )}
          {(r.escrituras?.length ?? 0) > 0 && (
            <div className="f-campo">
              <span className="f-etiqueta"><span>Lo que habría escrito (retenido)</span></span>
              <ul className="pp-escrituras">
                {r.escrituras!.map((w, i) => (
                  <li key={i}>
                    <b>{w.operacion ?? w.sistema}</b>{w.destino ? ` → ${w.destino}` : ""}{w.asunto ? ` · «${w.asunto}»` : ""}
                    {w.adjuntos?.length ? <span className="texto-3"> · adjuntos: {w.adjuntos.map((a) => `${a.nombre} (${peso(a.tamano)})`).join(", ")}</span> : null}
                    {w.motivo && <small className="texto-3">{w.motivo}</small>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {r.ok && r.variables && Object.keys(r.variables).length > 0 && (
            <div className="f-campo">
              <span className="f-etiqueta"><span>Lo que dejó</span></span>
              <Codigo valor={textoJson(r.variables)} lenguaje="json" soloLectura minAlto="80px" />
              <div className="fila envuelve">
                <button className="boton chico" onClick={() => void fijar()}><Icono n="ok" tam={12} />Fijar como datos de prueba</button>
                {aviso && <span className="texto-3 chico">{aviso}</span>}
              </div>
            </div>
          )}
          {(r.avisos?.length ?? 0) > 0 && <ul className="pp-avisos">{r.avisos!.map((a, i) => <li key={i}>{a}</li>)}</ul>}
          {pasos.length > 0 && (
            <details className="pn-extras">
              <summary>Lo que pasó ({pasos.length})</summary>
              <ol className="pp-pasos">
                {pasos.map((e, i) => <li key={i} className={`tono-${e.tono ?? "info"}`}><b>{e.titulo}</b>{e.detalle ? <small>{e.detalle.slice(0, 300)}</small> : null}</li>)}
              </ol>
            </details>
          )}
        </section>
      )}
      <AnimatePresence>{visor && <VisorArchivo key="visor" flujo={flujo} a={visor} cerrar={() => setVisor(null)} />}</AnimatePresence>
    </div>
  );
}
