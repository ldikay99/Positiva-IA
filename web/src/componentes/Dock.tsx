import { useEffect, useMemo, useRef, useState } from "react";
import { usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { NOMBRES_DESENLACE, TONO_DESENLACE, nodoDeEtapa, segundos } from "../progreso.ts";
import type { Catalogo, Evento } from "../tipos.ts";

function json(v: unknown): string {
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

export interface LineaEvento {
  tono: "" | "ia" | "herramienta" | "escritura" | "error" | "ok" | "tenue";
  titulo: string;
  detalle?: string;
  extra?: string;
}

/** Cómo se cuenta cada evento del motor en una línea legible. */
export function describir(e: Evento, catalogo: Catalogo | null): LineaEvento | null {
  const etapa = () => (catalogo ? (nodoDeEtapa(catalogo, String(e.etapa))?.nombre ?? String(e.etapa)) : String(e.etapa));
  switch (e.tipo) {
    case "en_cola":
      return { tono: "tenue", titulo: `${String(e.caso)} en cola` };
    case "asignada":
      return { tono: "tenue", titulo: `${String(e.caso)} asignado al trabajador ${String(e.trabajador)}` };
    case "inicio":
      return { tono: "", titulo: `Empieza ${String(e.caso)}`, extra: String(e.modelo) };
    case "etapa_inicio":
      return { tono: "tenue", titulo: `▸ ${etapa()}` };
    case "etapa_fin":
      return e.fallo ? { tono: "error", titulo: `${etapa()} falló`, detalle: String(e.fallo), extra: segundos(Number(e.ms)) } : { tono: "tenue", titulo: `✓ ${etapa()}`, extra: segundos(Number(e.ms)) };
    case "modelo_inicio":
      return { tono: "ia", titulo: `${e.quien === "revisor" ? "Revisor" : "Agente"} · vuelta ${String(e.vuelta)} → ${String(e.modelo)}`, detalle: e.sistema ? `INSTRUCCIONES\n${String(e.sistema)}\n\nÚLTIMO MENSAJE\n${String(e.ultimo)}` : String(e.ultimo || "") || undefined };
    case "modelo_fin":
      return e.ok === false
        ? { tono: "error", titulo: `El modelo falló`, detalle: String(e.error) }
        : {
            tono: "ia",
            titulo: `${e.quien === "revisor" ? "Revisor" : "Agente"} responde${(e.llamadas as { herramienta: string }[] | undefined)?.length ? ` y pide ${(e.llamadas as { herramienta: string }[]).map((l) => l.herramienta).join(", ")}` : ""}`,
            extra: `${segundos(Number(e.ms))} · ${Number(e.tokens_entrada).toLocaleString("es-CO")}+${Number(e.tokens_salida).toLocaleString("es-CO")} tokens`,
            detalle: String(e.texto || "") || undefined,
          };
    case "herramienta":
      return { tono: e.ok ? "herramienta" : "error", titulo: `Herramienta ${String(e.herramienta)}`, extra: segundos(Number(e.ms)), detalle: `${json(e.argumentos)}\n→ ${String(e.resultado)}` };
    case "proveedor":
      return { tono: e.ok === false ? "error" : "", titulo: `Registro de personas · ${e.ok === false ? "falló" : e.encontrado ? "encontrado" : "sin resultado"}`, extra: segundos(Number(e.ms)), detalle: e.error ? String(e.error) : undefined };
    case "escritura":
      return { tono: "escritura", titulo: `${e.sistema === "sgdea" ? "SGDEA" : "Buzón"} · ${String(e.operacion)} (simulado)`, detalle: e.payload || e.detalle ? json(e.payload ?? e.detalle) : undefined };
    case "log":
      return { tono: e.severidad === "WARNING" || e.severidad === "ERROR" ? "error" : "", titulo: String(e.mensaje), extra: String(e.severidad ?? ""), detalle: json(e.campos) };
    case "configuracion":
      return { tono: "error", titulo: "Ajustes que no se aplicaron", detalle: json(e.fallidas) };
    case "resultado":
      return { tono: e.accion === "radicado" ? "ok" : "error", titulo: `${NOMBRES_DESENLACE[String(e.accion)] ?? String(e.accion)}${e.motivo ? ` · ${String(e.motivo)}` : ""}`, extra: e.segundos !== undefined ? `${String(e.segundos)} s` : undefined, detalle: e.traza ? String(e.traza) : undefined };
    default:
      return null;
  }
}

export function LineaDeTiempo({ eventos, t0, mostrarCaso = false }: { eventos: Evento[]; t0: number; mostrarCaso?: boolean }) {
  const catalogo = usar((s) => s.catalogo);
  const corridas = usar((s) => s.corridas);
  const [abierto, setAbierto] = useState<number | null>(null);
  return (
    <div className="linea-tiempo">
      {eventos.map((e, i) => {
        const d = describir(e, catalogo);
        if (!d) return null;
        return (
          <div key={i} className={`ev ${d.tono} ${d.detalle ? "con-detalle" : ""}`} onClick={() => d.detalle && setAbierto(abierto === i ? null : i)}>
            <span className="ev-t mono">{mostrarCaso ? new Date(e.t * 1000).toLocaleTimeString("es-CO", { hour12: false }) : `+${(e.t - t0).toFixed(1)}s`}</span>
            <span className="ev-cuerpo">
              {mostrarCaso && e.corrida && <span className="ficha">{corridas[e.corrida]?.caso ?? ""}</span>}
              <span className="ev-titulo">{d.titulo}</span>
              {d.extra && <span className="ev-extra">{d.extra}</span>}
              {d.detalle && <Icono n="menu" tam={12} className="ev-mas" />}
            </span>
            {abierto === i && d.detalle && <pre className="ev-detalle" onClick={(x) => x.stopPropagation()}>{d.detalle}</pre>}
          </div>
        );
      })}
    </div>
  );
}

export function Dock() {
  const corrida = usar((s) => (s.corridaVisible ? s.corridas[s.corridaVisible] : undefined));
  const orden = usar((s) => s.orden);
  const corridas = usar((s) => s.corridas);
  const verCorrida = usar((s) => s.verCorrida);
  const enfocar = usar((s) => s.enfocar);
  const [vista, setVista] = useState<"proceso" | "logs" | "consola">("proceso");
  const [alFinal, setAlFinal] = useState(true);
  const caja = useRef<HTMLDivElement>(null);

  const eventos = useMemo(() => {
    const ev = corrida?.eventos ?? [];
    if (vista === "logs") return ev.filter((e) => e.tipo === "log" || e.tipo === "resultado" || e.tipo === "configuracion");
    if (vista === "consola") return ev.filter((e) => e.tipo === "consola");
    return ev.filter((e) => e.tipo !== "consola" && e.tipo !== "log");
  }, [corrida, vista]);

  useEffect(() => {
    if (alFinal && caja.current) caja.current.scrollTop = caja.current.scrollHeight;
  }, [eventos, alFinal]);

  const r = corrida?.resultado;
  const accion = r ? String(r.accion) : "";
  return (
    <section className="dock">
      <header className="dock-cabeza">
        <nav className="segmentos">
          <button className={vista === "proceso" ? "activo" : ""} onClick={() => setVista("proceso")}>Proceso en vivo</button>
          <button className={vista === "logs" ? "activo" : ""} onClick={() => setVista("logs")}>Logs</button>
          <button className={vista === "consola" ? "activo" : ""} onClick={() => setVista("consola")}>Consola</button>
        </nav>
        <select className="campo chico-campo" value={corrida?.id ?? ""} onChange={(e) => void verCorrida(e.target.value || null)}>
          <option value="">Elegir corrida…</option>
          {orden.slice(0, 80).map((id) => {
            const c = corridas[id];
            if (!c) return null;
            return <option key={id} value={id}>{c.caso} · {c.resultado ? (NOMBRES_DESENLACE[String(c.resultado.accion)] ?? String(c.resultado.accion)) : c.estado === "en_cola" ? "en cola" : "procesando"}</option>;
          })}
        </select>
        {corrida && (
          <>
            <span className={`pastilla ${r ? (TONO_DESENLACE[accion] ?? "") : "azul"}`}>{r ? (NOMBRES_DESENLACE[accion] ?? accion) : corrida.estado === "en_cola" ? "En cola" : "Procesando…"}</span>
            <button className="boton chico" onClick={() => enfocar({ tipo: "corrida", id: corrida.id })}>Ver resultado</button>
          </>
        )}
        <span className="espacio" />
        <label className="fila chico texto-2"><input type="checkbox" className="check" checked={alFinal} onChange={(e) => setAlFinal(e.target.checked)} />Seguir al final</label>
      </header>
      <div className="dock-cuerpo" ref={caja}>
        {!corrida ? (
          <div className="vacio">Selecciona correos en la bandeja y pulsa Procesar: aquí verás cada paso, cada vuelta del modelo y cada log en vivo.</div>
        ) : vista === "consola" ? (
          <pre className="consola">{eventos.map((e) => String(e.linea)).join("\n") || "Sin salida de consola."}</pre>
        ) : (
          <LineaDeTiempo eventos={eventos} t0={corrida.eventos[0]?.t ?? 0} />
        )}
      </div>
    </section>
  );
}
