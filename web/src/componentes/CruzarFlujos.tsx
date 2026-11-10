import { useEffect, useMemo, useState } from "react";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { Dialogo } from "./Dialogos.tsx";
import { Selector, type OpcionSelector } from "./Selector.tsx";
import "./cruzar.css";

/**
 * «Cruzar flujos»: comparar el flujo abierto con otro y traer pasos de otro flujo a este.
 * Usa GET /api/flujos/:id/comparar?con= y POST /api/flujos/:id/traer (servidor/src/cruzar.ts),
 * las mismas rutas que usa Vera (comparar_flujos, traer_nodos).
 */

export type ModoCruzar = "comparar" | "traer";

type Nodo = { id: string; tipo: string; nombre?: string };
type Arista = { desde: string; hacia: string; cuando?: string };
type Ficha = { id: string; tipo: string; nombre: string };
interface Comparacion {
  a: { id: string; nombre: string };
  b: { id: string; nombre: string };
  iguales: boolean;
  resumen: string;
  flujo: { campo: string; a: string; b: string }[];
  nodos: { solo_en_a: Ficha[]; solo_en_b: Ficha[]; distintos: { id: string; nombre: string; cambios: { campo: string; a: string; b: string }[] }[]; iguales: number; parecidos: { en_a: string; en_b: string }[] };
  aristas: { solo_en_a: Arista[]; solo_en_b: Arista[] };
}
interface Traida {
  ok: boolean;
  guardado?: boolean;
  version?: number;
  error?: string;
  problemas?: string[];
  avisos?: string[];
  renombrados?: Record<string, string>;
  traidos?: { de: string; a: string; nombre: string }[];
}
interface Grafo {
  nodos: Nodo[];
  aristas: Arista[];
  puntos?: { id: string; nombre: string; entre: [string, string] }[];
}

/** Los pasos y conexiones de un flujo: la definición (declarativo) o la estructura (de código). */
async function grafoDe(id: string, tipo: string | undefined): Promise<Grafo> {
  if (tipo === "codigo") {
    const e = await pedir<{ definicion: Grafo; reglas: { puntos: { id: string; nombre: string; entre: [string, string] }[] } }>(`/api/flujos/${encodeURIComponent(id)}/estructura`);
    return { ...e.definicion, puntos: e.reglas.puntos };
  }
  const f = await pedir<{ definicion?: Grafo }>(`/api/flujos/${encodeURIComponent(id)}`);
  return f.definicion ?? { nodos: [], aristas: [] };
}

const DISPARADORES = new Set(["entrada_correo", "entrada_manual", "programado", "webhook", "correo_nuevo", "entrada_archivos", "gmail_nuevo", "outlook_nuevo"]);

export function DialogoCruzar({ abierto, cerrar, flujo, modo: modoInicial }: { abierto: boolean; cerrar: () => void; flujo: string; modo: ModoCruzar }) {
  const flujos = usar((s) => s.flujos);
  const cargarFlujos = usar((s) => s.cargarFlujos);
  const avisar = usar((s) => s.avisar);
  const este = flujos.find((f) => f.id === flujo);
  const [modo, setModo] = useState<ModoCruzar>(modoInicial);
  const [otro, setOtro] = useState<string | null>(null);
  const [comp, setComp] = useState<Comparacion | null>(null);
  const [origen, setOrigen] = useState<Grafo | null>(null);
  const [destino, setDestino] = useState<Grafo | null>(null);
  const [elegidos, setElegidos] = useState<string[]>([]);
  const [donde, setDonde] = useState<string>("");
  const [revision, setRevision] = useState<Traida | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!abierto) return;
    setModo(modoInicial);
    setError("");
    setRevision(null);
  }, [abierto, modoInicial]);

  const opciones: OpcionSelector[] = useMemo(
    () => flujos.filter((f) => f.id !== flujo).map((f) => ({ valor: f.id, etiqueta: f.nombre, texto: `${f.nombre} ${f.id}`, detalle: f.tipo === "codigo" ? "de código" : "declarativo" })),
    [flujos, flujo],
  );
  const tipoDe = (id: string | null) => flujos.find((f) => f.id === id)?.tipo;
  const nombreDe = (id: string | null) => flujos.find((f) => f.id === id)?.nombre ?? id ?? "";

  // lo que se ve depende del otro flujo y del modo
  useEffect(() => {
    if (!abierto || !otro) return;
    let vivo = true;
    setError("");
    setCargando(true);
    setRevision(null);
    const tarea =
      modo === "comparar"
        ? pedir<Comparacion>(`/api/flujos/${encodeURIComponent(flujo)}/comparar?con=${encodeURIComponent(otro)}`).then((c) => vivo && setComp(c))
        : Promise.all([grafoDe(otro, tipoDe(otro)), grafoDe(flujo, este?.tipo)]).then(([o, d]) => {
            if (!vivo) return;
            setOrigen(o);
            setDestino(d);
            setElegidos((e) => e.filter((x) => o.nodos.some((n) => n.id === x)));
          });
    tarea.catch((e: Error) => vivo && setError(e.message)).finally(() => vivo && setCargando(false));
    return () => {
      vivo = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [abierto, otro, modo, flujo]);

  const copiables = (origen?.nodos ?? []).filter((n) => n.tipo !== "etapa" && !(DISPARADORES.has(n.tipo) && (este?.tipo === "codigo" || destino?.nodos.some((x) => DISPARADORES.has(x.tipo)))));
  const nombreNodo = (g: Grafo | null, id: string) => g?.nodos.find((n) => n.id === id)?.nombre ?? id;
  const lugares: OpcionSelector[] = useMemo(() => {
    if (!destino) return [];
    const r: OpcionSelector[] = [{ valor: "", etiqueta: "Sin conectar (los conecto después)", texto: "sin conectar" }];
    const usadas = new Set<string>();
    for (const p of destino.puntos ?? []) {
      if (!destino.aristas.some((a) => a.desde === p.entre[0] && a.hacia === p.entre[1])) continue;
      const k = `${p.entre[0]}>${p.entre[1]}`;
      usadas.add(k);
      r.push({ valor: k, etiqueta: `Punto «${p.nombre}»`, texto: p.nombre, detalle: `${nombreNodo(destino, p.entre[0])} → ${nombreNodo(destino, p.entre[1])}` });
    }
    for (const a of destino.aristas) {
      const k = `${a.desde}>${a.hacia}`;
      if (usadas.has(k)) continue;
      usadas.add(k);
      r.push({ valor: k, etiqueta: `${nombreNodo(destino, a.desde)} → ${nombreNodo(destino, a.hacia)}`, texto: `${a.desde} ${a.hacia}`, detalle: a.cuando ? `cuando «${a.cuando}»` : undefined });
    }
    return r;
  }, [destino]);

  const traer = async (simular: boolean) => {
    if (!otro || !elegidos.length) return;
    setError("");
    setCargando(true);
    try {
      const cuerpo: Record<string, unknown> = { desde: otro, nodos: elegidos, simular };
      if (donde) cuerpo.entre = donde.split(">");
      const r = await fetch(`/api/flujos/${encodeURIComponent(flujo)}/traer`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) });
      const t = (await r.json()) as Traida;
      setRevision(t);
      if (!simular && t.guardado) {
        avisar(`Traje ${t.traidos?.length ?? elegidos.length} paso(s) de «${nombreDe(otro)}» · versión ${t.version}`);
        void cargarFlujos();
        cerrar();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCargando(false);
    }
  };

  const alternar = (id: string) => setElegidos((e) => (e.includes(id) ? e.filter((x) => x !== id) : [...e, id]));

  const pie = modo === "traer" ? (
    <>
      <span className="espacio" />
      <button type="button" className="boton chico" disabled={cargando || !elegidos.length} onClick={() => void traer(true)}>Revisar sin guardar</button>
      <button type="button" className="boton chico primario" disabled={cargando || !elegidos.length} onClick={() => void traer(false)}>
        {cargando && <span className="girando" />}Traer {elegidos.length || ""} paso{elegidos.length === 1 ? "" : "s"}
      </button>
    </>
  ) : undefined;

  return (
    <Dialogo abierto={abierto} cerrar={cerrar} titulo={modo === "comparar" ? "Comparar con otro flujo" : "Traer pasos de otro flujo"} sub={`«${este?.nombre ?? flujo}»${modo === "traer" ? " recibe los pasos; el otro flujo no cambia" : ""}`} pie={pie} ancho={640}>
      <div className="cz-modos" role="tablist" aria-label="Qué hacer">
        <button type="button" role="tab" aria-selected={modo === "comparar"} className={modo === "comparar" ? "activo" : ""} onClick={() => setModo("comparar")}><Icono n="capas" tam={13} />Comparar</button>
        <button type="button" role="tab" aria-selected={modo === "traer"} className={modo === "traer" ? "activo" : ""} onClick={() => setModo("traer")}><Icono n="mas_circulo" tam={13} />Traer pasos</button>
      </div>
      <div className="cz-fila">
        <span className="cz-etq">{modo === "comparar" ? "Con" : "De"}</span>
        <Selector etiqueta="El otro flujo" valor={otro} opciones={opciones} alCambiar={(v) => setOtro(v)} placeholder="Elige un flujo…" className="cz-selector" />
      </div>
      {error && <p className="f-error" role="alert">{error}</p>}
      {cargando && !comp && !origen && <p className="cz-nota"><span className="girando" />Leyendo los flujos…</p>}

      {modo === "comparar" && otro && comp && comp.b.id === otro && (
        <div className="cz-comparacion">
          <p className={`cz-resumen ${comp.iguales ? "igual" : ""}`}><Icono n={comp.iguales ? "check" : "info"} tam={14} />{comp.resumen}</p>
          {!!comp.flujo.length && (
            <section>
              <h3>Datos del flujo</h3>
              <ul className="cz-cambios">{comp.flujo.map((c) => <li key={c.campo}><b>{c.campo}</b><span className="a">{c.a}</span><Icono n="derecha" tam={11} /><span className="b">{c.b}</span></li>)}</ul>
            </section>
          )}
          {!!comp.nodos.solo_en_a.length && (
            <section>
              <h3>Solo en «{comp.a.nombre}»</h3>
              <div className="cz-fichas">{comp.nodos.solo_en_a.map((n) => <span key={n.id} className="cz-ficha" title={n.id}><b>{n.nombre}</b><small>{n.tipo}</small></span>)}</div>
            </section>
          )}
          {!!comp.nodos.solo_en_b.length && (
            <section>
              <h3>Solo en «{comp.b.nombre}»</h3>
              <div className="cz-fichas">
                {comp.nodos.solo_en_b.map((n) => (
                  <span key={n.id} className="cz-ficha" title={n.id}>
                    <b>{n.nombre}</b><small>{n.tipo}</small>
                    {n.tipo !== "etapa" && (
                      <button type="button" className="cz-mini" title="Traerlo a este flujo" onClick={() => { setElegidos([n.id]); setModo("traer"); }}><Icono n="mas_circulo" tam={11} />Traer</button>
                    )}
                  </span>
                ))}
              </div>
            </section>
          )}
          {!!comp.nodos.distintos.length && (
            <section>
              <h3>Con distinta configuración</h3>
              {comp.nodos.distintos.map((d) => (
                <div key={d.id} className="cz-distinto">
                  <b>{d.nombre}</b>
                  <ul className="cz-cambios">{d.cambios.map((c) => <li key={c.campo}><b>{c.campo}</b><span className="a">{c.a}</span><Icono n="derecha" tam={11} /><span className="b">{c.b}</span></li>)}</ul>
                </div>
              ))}
            </section>
          )}
          {!!(comp.aristas.solo_en_a.length + comp.aristas.solo_en_b.length) && (
            <section>
              <h3>Conexiones distintas</h3>
              <ul className="cz-aristas">
                {comp.aristas.solo_en_a.map((a) => <li key={`a${a.desde}${a.hacia}${a.cuando}`}><span className="cz-lado">solo aquí</span>{a.desde} → {a.hacia}{a.cuando ? ` [${a.cuando}]` : ""}</li>)}
                {comp.aristas.solo_en_b.map((a) => <li key={`b${a.desde}${a.hacia}${a.cuando}`}><span className="cz-lado otro">solo en el otro</span>{a.desde} → {a.hacia}{a.cuando ? ` [${a.cuando}]` : ""}</li>)}
              </ul>
            </section>
          )}
          <p className="cz-nota">{comp.nodos.iguales} paso{comp.nodos.iguales === 1 ? "" : "s"} iguales en los dos.</p>
        </div>
      )}

      {modo === "traer" && otro && origen && (
        <>
          <section className="cz-seccion">
            <h3>Pasos de «{nombreDe(otro)}»</h3>
            {!copiables.length && <p className="cz-nota">Ese flujo no tiene pasos que se puedan traer aquí (las etapas del motor y los disparadores no se copian).</p>}
            <ul className="cz-lista" role="group" aria-label="Pasos para traer">
              {copiables.map((n) => {
                const si = elegidos.includes(n.id);
                return (
                  <li key={n.id}>
                    <button type="button" role="checkbox" aria-checked={si} className={`cz-opcion ${si ? "sel" : ""}`} onClick={() => alternar(n.id)}>
                      <span className="cz-caja" aria-hidden>{si && <Icono n="check" tam={11} />}</span>
                      <span className="crece"><b>{n.nombre ?? n.id}</b><small>{n.tipo} · {n.id}{destino?.nodos.some((x) => x.id === n.id) ? " · ya hay uno con ese id: se renombra" : ""}</small></span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
          <div className="cz-fila">
            <span className="cz-etq">Dónde</span>
            <Selector etiqueta="Dónde conectarlos" valor={donde} opciones={lugares} alCambiar={(v) => setDonde(v)} className="cz-selector" />
          </div>
          {este?.tipo === "codigo" && <p className="cz-nota">En un flujo de código los pasos van dentro de un punto de inserción, como al agregar un paso.</p>}
          {revision && (
            <div className={`cz-revision ${revision.ok ? "bien" : "mal"}`} role="status">
              <b>{revision.ok ? (revision.guardado ? `Guardado · versión ${revision.version}` : "Se puede traer así") : revision.error ?? "Así no se puede traer"}</b>
              {!!revision.problemas?.length && <ul>{revision.problemas.slice(0, 6).map((p) => <li key={p}>{p}</li>)}</ul>}
              {!!Object.keys(revision.renombrados ?? {}).length && <p>Se renombran: {Object.entries(revision.renombrados!).map(([a, b]) => `${a} → ${b}`).join(", ")}</p>}
              {revision.avisos?.map((a) => <p key={a} className="aviso"><Icono n="alerta" tam={12} />{a}</p>)}
            </div>
          )}
        </>
      )}
    </Dialogo>
  );
}
