import { useCallback, useEffect, useMemo, useState } from "react";
import { usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { dentroDe, fechaHora, hace } from "../lenguaje.ts";
import { Selector } from "./Selector.tsx";
import { Cargando, EstadoPantalla } from "./Historial.tsx";

/**
 * La bandeja de aprobaciones: lo que un flujo dejó en pausa hasta que una persona
 * apruebe o rechace (con comentario y, si hace falta, cambios en los datos).
 *
 *   GET  /api/aprobaciones?flujo=
 *   POST /api/aprobaciones/:id {decision: "aprobar"|"rechazar", comentario?, cambios?}
 *
 * Si el servidor todavía no tiene el paso de aprobación (404), la vista lo explica.
 */

interface Aprobacion {
  id: string;
  flujo?: string;
  corrida?: string;
  caso?: string;
  nodo?: string;
  titulo?: string;
  nombre?: string;
  nombre_nodo?: string;
  detalle?: string;
  pregunta?: string;
  instrucciones?: string;
  datos?: Record<string, unknown>;
  /** Los campos de datos que quien decide puede cambiar (si no viene, todos los simples). */
  campos_editables?: string[];
  asignado_a?: string;
  creada?: number;
  t?: number;
  vence?: number | string;
  limite?: number | string;
  estado?: string;
}

const texto = (v: unknown) => (v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));
const editable = (v: unknown) => v === null || ["string", "number", "boolean"].includes(typeof v);
const tiempo = (x: number | string | undefined) => (x === undefined ? null : typeof x === "number" ? (x < 1e12 ? x * 1000 : x) : Date.parse(x));

export function VistaAprobaciones() {
  const flujos = usar((s) => s.flujos);
  const avisar = usar((s) => s.avisar);
  const abrirEnHistorial = usar((s) => s.abrirEnHistorial);
  const [flujo, setFlujo] = useState("");
  const [lista, setLista] = useState<Aprobacion[] | null>(null);
  const [disponible, setDisponible] = useState(true);
  const [error, setError] = useState("");
  // El servidor dice (con las notificaciones) si ya existe el paso de aprobación; sin él no se pregunta.
  const conAprobaciones = usar((s) => s.aprobacionesDisponibles);
  const cargarNotificaciones = usar((s) => s.cargarNotificaciones);
  useEffect(() => {
    if (conAprobaciones === null) void cargarNotificaciones().catch(() => undefined);
  }, [conAprobaciones, cargarNotificaciones]);

  const cargar = useCallback(async () => {
    if (conAprobaciones === null) return;
    if (!conAprobaciones) {
      setDisponible(false);
      setLista([]);
      return;
    }
    try {
      const r = await fetch(`/api/aprobaciones${flujo ? `?flujo=${encodeURIComponent(flujo)}` : ""}`);
      if (r.status === 404) {
        const c = (await r.json().catch(() => ({}))) as { error?: string };
        // 404 de «esa ruta no existe» (sin el paso de aprobación) vs. 404 de un flujo que no existe
        if (!c.error || c.error === "no existe") {
          setDisponible(false);
          setLista([]);
          return;
        }
      }
      const cuerpo = (await r.json().catch(() => ({}))) as Aprobacion[] | { aprobaciones?: Aprobacion[]; error?: string };
      if (!r.ok) throw new Error((cuerpo as { error?: string }).error ?? `error ${r.status}`);
      setDisponible(true);
      setLista((Array.isArray(cuerpo) ? cuerpo : cuerpo.aprobaciones ?? []).filter((a) => !a.estado || a.estado === "pendiente"));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [flujo, conAprobaciones]);

  useEffect(() => {
    void cargar();
    const t = setInterval(() => void cargar(), 10_000);
    return () => clearInterval(t);
  }, [cargar]);
  // Una notificación de aprobación nueva: se vuelve a pedir la lista.
  const ultimaAprobacion = usar((s) => s.notificaciones.find((n) => n.tipo === "aprobacion")?.id);
  useEffect(() => {
    if (ultimaAprobacion) void cargar();
  }, [ultimaAprobacion, cargar]);

  const opciones = useMemo(() => [{ valor: "", etiqueta: "Todos los flujos" }, ...flujos.map((f) => ({ valor: f.id, etiqueta: f.nombre }))], [flujos]);

  const decidir = async (a: Aprobacion, decision: "aprobar" | "rechazar", comentario: string, cambios: Record<string, unknown>) => {
    const r = await fetch(`/api/aprobaciones/${encodeURIComponent(a.id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Confirmado-Por-Persona": "si" },
      body: JSON.stringify({ decision, ...(comentario.trim() ? { comentario: comentario.trim() } : {}), ...(Object.keys(cambios).length ? { cambios } : {}) }),
    });
    const c = (await r.json().catch(() => ({}))) as { error?: string };
    if (!r.ok) throw new Error(c.error ?? `error ${r.status}`);
    avisar(decision === "aprobar" ? "Aprobado: el flujo sigue por la rama de aprobación" : "Rechazado: el flujo sigue por la rama de rechazo");
    setLista((l) => (l ?? []).filter((x) => x.id !== a.id));
  };

  return (
    <div className="vista pr-vista aprobaciones">
      <header className="vista-cabeza">
        <div className="crece">
          <h1>Aprobaciones</h1>
          <p className="texto-2">Lo que un flujo dejó en pausa hasta que una persona decida. Al aprobar o rechazar, el flujo sigue por el camino que corresponde.</p>
        </div>
        {disponible && <Selector etiqueta="Flujo" valor={flujo} opciones={opciones} alCambiar={setFlujo} ancho={240} />}
      </header>
      {!disponible ? (
        <EstadoPantalla icono="persona" titulo="Las aprobaciones llegan con el paso «Aprobación humana»" texto="Cuando un flujo tenga ese paso, cada caso que espere una decisión aparecerá aquí con sus datos, para aprobarlo o rechazarlo con un comentario. Ese paso todavía no está disponible en esta versión de la plataforma." />
      ) : error ? (
        <EstadoPantalla icono="alerta" tono="rojo" titulo="No se pudieron cargar las aprobaciones" texto={error} accion={<button className="boton" onClick={() => void cargar()}>Intentar de nuevo</button>} />
      ) : !lista ? (
        <Cargando filas={3} />
      ) : !lista.length ? (
        <EstadoPantalla icono="ok" titulo="Nada espera tu aprobación" texto={flujo ? "Este flujo no tiene casos en pausa." : "Ningún flujo tiene casos en pausa. Cuando llegue uno, también te avisa la campana."} />
      ) : (
        <ul className="ap-lista">
          {lista.map((a) => (
            <TarjetaAprobacion key={a.id} a={a} nombreFlujo={flujos.find((f) => f.id === a.flujo)?.nombre ?? a.flujo ?? ""} decidir={decidir} verEjecucion={a.corrida ? () => void abrirEnHistorial(a.corrida!, a.flujo) : undefined} />
          ))}
        </ul>
      )}
    </div>
  );
}

function TarjetaAprobacion({ a, nombreFlujo, decidir, verEjecucion }: { a: Aprobacion; nombreFlujo: string; decidir: (a: Aprobacion, d: "aprobar" | "rechazar", comentario: string, cambios: Record<string, unknown>) => Promise<void>; verEjecucion?: () => void }) {
  const [comentario, setComentario] = useState("");
  const [valores, setValores] = useState<Record<string, string>>(() => Object.fromEntries(Object.entries(a.datos ?? {}).filter(([k, v]) => editable(v) && (!Array.isArray(a.campos_editables) || a.campos_editables.includes(k))).map(([k, v]) => [k, texto(v)])));
  const [editando, setEditando] = useState(false);
  const [confirmarRechazo, setConfirmarRechazo] = useState(false);
  const [enviando, setEnviando] = useState<"" | "aprobar" | "rechazar">("");
  const [error, setError] = useState("");
  const creada = tiempo(a.creada ?? a.t);
  const vence = tiempo(a.vence ?? a.limite);

  const cambios = () => {
    const c: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(valores)) {
      const original = a.datos?.[k];
      if (texto(original) === v) continue;
      c[k] = typeof original === "number" && v.trim() !== "" && !Number.isNaN(Number(v)) ? Number(v) : typeof original === "boolean" ? v === "true" : v;
    }
    return c;
  };
  const enviar = async (d: "aprobar" | "rechazar") => {
    setEnviando(d);
    setError("");
    try {
      await decidir(a, d, comentario, cambios());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setEnviando("");
    }
  };

  return (
    <li className="ap-tarjeta">
      <header>
        <span className="ap-icono"><Icono n="persona" tam={17} /></span>
        <div className="crece">
          <small className="texto-3">{nombreFlujo}{a.nombre_nodo ? `, paso «${a.nombre_nodo}»` : ""}{a.asignado_a ? `. Le toca a: ${a.asignado_a}` : ""}</small>
          <h2>{a.titulo || a.nombre || "Necesita una decisión"}</h2>
        </div>
        <div className="ap-tiempos">
          {creada && <span className="texto-3 diminuto" title={fechaHora(creada)}>Llegó {hace(creada)}</span>}
          {vence && <span className={`ficha ${vence - Date.now() < 3_600_000 ? "tono-rojo" : "tono-ambar"}`} title={fechaHora(vence)}>{vence < Date.now() ? "Venció" : `Vence ${dentroDe(vence)}`}</span>}
        </div>
      </header>
      {(a.detalle || a.pregunta || a.instrucciones) && <p className="ap-pregunta">{a.detalle || a.pregunta || a.instrucciones}</p>}
      {a.datos && Object.keys(a.datos).length > 0 && (
        <div className="ap-datos">
          <div className="fila">
            <h3 className="crece">Datos del caso</h3>
            {Object.keys(valores).length > 0 && <button className="enlace" aria-pressed={editando} onClick={() => setEditando(!editando)}>{editando ? "Listo" : "Corregir datos"}</button>}
          </div>
          <dl className="pr-campos">
            {Object.entries(a.datos).map(([k, v]) => (
              <div key={k}>
                <dt>{k.replace(/_/g, " ")}</dt>
                <dd>{editando && k in valores ? <input className="campo" value={valores[k]} aria-label={k} onChange={(e) => setValores({ ...valores, [k]: e.target.value })} /> : texto(valores[k] ?? v) || <span className="texto-3">vacío</span>}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      <label className="f-campo">
        <span className="f-etiqueta"><span>Comentario (opcional)</span></span>
        <textarea className="campo" rows={2} value={comentario} onChange={(e) => setComentario(e.target.value)} placeholder="Por qué lo apruebas o lo rechazas" />
      </label>
      {error && <p className="f-error" role="alert">{error}</p>}
      <footer>
        {verEjecucion && <button className="boton chico fantasma" onClick={verEjecucion}><Icono n="historial" tam={13} />Ver la ejecución</button>}
        <span className="espacio" />
        {confirmarRechazo ? (
          <span className="pr-confirmar" role="group" aria-label="Confirmar el rechazo">
            <span>¿Rechazar este caso?</span>
            <button className="boton peligro chico" disabled={Boolean(enviando)} onClick={() => void enviar("rechazar")}>{enviando === "rechazar" && <span className="girando" />}Sí, rechazar</button>
            <button className="boton chico" onClick={() => setConfirmarRechazo(false)}>Cancelar</button>
          </span>
        ) : (
          <>
            <button className="boton chico peligro" disabled={Boolean(enviando)} onClick={() => setConfirmarRechazo(true)}><Icono n="cerrar" tam={12} />Rechazar</button>
            <button className="boton primario chico" disabled={Boolean(enviando)} onClick={() => void enviar("aprobar")}>{enviando === "aprobar" ? <span className="girando" /> : <Icono n="check" tam={13} />}Aprobar{Object.keys(cambios()).length ? " con cambios" : ""}</button>
          </>
        )}
      </footer>
    </li>
  );
}
