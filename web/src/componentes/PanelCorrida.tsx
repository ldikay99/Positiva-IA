import { useEffect, useState } from "react";
import { usar } from "../estado.ts";
import { NOMBRES_DESENLACE, TONO_DESENLACE, nodoDeEtapa, progreso, segundos } from "../progreso.ts";
import type { Correo, Evento } from "../tipos.ts";

function json(v: unknown): string {
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

function Linea({ e, t0 }: { e: Evento; t0: number }) {
  const catalogo = usar((s) => s.catalogo);
  const seg = `${(e.t - t0).toFixed(1)} s`;
  let clase = "";
  let cuerpo: React.ReactNode = null;
  switch (e.tipo) {
    case "inicio":
      cuerpo = <>Empieza con <b>{String(e.modelo)}</b></>;
      break;
    case "etapa_inicio":
      cuerpo = <span className="texto-2">▸ {catalogo ? (nodoDeEtapa(catalogo, String(e.etapa))?.nombre ?? String(e.etapa)) : String(e.etapa)}</span>;
      break;
    case "etapa_fin":
      clase = e.fallo ? "falla" : "";
      cuerpo = (
        <span className="texto-3">
          ✓ {catalogo ? (nodoDeEtapa(catalogo, String(e.etapa))?.nombre ?? String(e.etapa)) : String(e.etapa)} · {segundos(Number(e.ms))}
          {e.fallo ? ` · ${String(e.fallo)}` : ""}
        </span>
      );
      break;
    case "modelo_inicio":
      clase = "modelo";
      cuerpo = (
        <>
          <b>Vuelta {String(e.vuelta)}</b> · pide al modelo ({String(e.mensajes)} mensajes)
          {e.sistema ? <details><summary>Instrucciones del agente</summary><pre>{String(e.sistema)}</pre></details> : null}
          {e.ultimo ? <details><summary>Último mensaje enviado</summary><pre>{String(e.ultimo)}</pre></details> : null}
        </>
      );
      break;
    case "modelo_fin":
      clase = e.ok === false ? "falla" : "modelo";
      cuerpo = e.ok === false ? (
        <>El modelo falló: {String(e.error)}</>
      ) : (
        <>
          Responde en {segundos(Number(e.ms))} · {Number(e.tokens_entrada).toLocaleString("es-CO")} tokens de entrada, {Number(e.tokens_salida).toLocaleString("es-CO")} de salida
          {(e.llamadas as { herramienta: string }[] | undefined)?.length ? <div className="chico">Pide: {(e.llamadas as { herramienta: string }[]).map((l) => l.herramienta).join(", ")}</div> : null}
          {e.texto ? <pre>{String(e.texto)}</pre> : null}
        </>
      );
      break;
    case "herramienta":
      clase = e.ok ? "herramienta" : "falla";
      cuerpo = (
        <>
          <b>{String(e.herramienta)}</b> <span className="texto-3">{segundos(Number(e.ms))}</span>
          <details><summary>Argumentos y respuesta</summary><pre>{json(e.argumentos)}</pre><pre>{String(e.resultado)}</pre></details>
        </>
      );
      break;
    case "proveedor":
      cuerpo = <>Consulta {String(e.proveedor).replace(/_/g, " ")} · {segundos(Number(e.ms))} · {e.ok === false ? `falló: ${String(e.error)}` : e.encontrado ? "encontrado" : "sin resultado"}</>;
      break;
    case "escritura":
      clase = "escritura";
      cuerpo = (
        <>
          <b>{e.sistema === "sgdea" ? "SGDEA" : "Buzón"}</b> · {String(e.operacion)} <span className="texto-3">(simulado, no se escribe)</span>
          {e.payload || e.detalle ? <details><summary>Detalle</summary><pre>{json(e.payload ?? e.detalle)}</pre></details> : null}
        </>
      );
      break;
    case "log":
      clase = e.severidad === "WARNING" || e.severidad === "ERROR" ? "falla" : "";
      cuerpo = (
        <>
          {String(e.mensaje)}
          <details><summary>Campos del registro</summary><pre>{json(e.campos)}</pre></details>
        </>
      );
      break;
    case "configuracion":
      clase = "falla";
      cuerpo = <>Ajustes que no se pudieron aplicar: {json(e.fallidas)}</>;
      break;
    case "resultado":
      clase = e.accion === "radicado" ? "" : "falla";
      cuerpo = <b>{NOMBRES_DESENLACE[String(e.accion)] ?? String(e.accion)}{e.motivo ? ` · ${String(e.motivo)}` : ""}</b>;
      break;
    default:
      return null;
  }
  return (
    <div className={`ev ${clase}`}>
      <span className="seg">{seg}</span>
      <div style={{ minWidth: 0 }}>{cuerpo}</div>
    </div>
  );
}

export function PanelCorrida() {
  const catalogo = usar((s) => s.catalogo);
  const id = usar((s) => s.seleccionada);
  const corrida = usar((s) => (s.seleccionada ? s.corridas[s.seleccionada] : undefined));
  const seleccionar = usar((s) => s.seleccionar);
  const procesar = usar((s) => s.procesar);
  const [correo, setCorreo] = useState<Correo | null>(null);
  const [vista, setVista] = useState<"proceso" | "resultado" | "correo" | "consola">("proceso");

  useEffect(() => {
    setCorreo(null);
    if (!corrida?.caso) return;
    void fetch(`/api/bandeja/${encodeURIComponent(corrida.caso)}`).then(async (r) => r.ok && setCorreo((await r.json()) as Correo));
  }, [corrida?.caso]);

  if (!id || !corrida) return null;
  const p = progreso(catalogo, corrida);
  const r = corrida.resultado;
  const t0 = corrida.eventos[0]?.t ?? 0;
  const radicacion = corrida.eventos.find((e) => e.tipo === "escritura" && e.operacion === "radicar");
  const hallazgos = (r?.hallazgos ?? {}) as Record<string, { valor: unknown; metodo: string; confianza: number; evidencia: string }>;
  const accion = r ? String(r.accion) : "";

  return (
    <aside className="columna der">
      <div className="cabeza">
        <div className="cabeza-fila">
          <h2>Corrida {corrida.caso}</h2>
          <button className="boton chico" onClick={() => void procesar([corrida.caso])}>Repetir</button>
          <button className="boton chico" onClick={() => seleccionar(null)}>Cerrar</button>
        </div>
        <div className="fila-botones">
          {r ? (
            <span className={`pastilla ${TONO_DESENLACE[accion] ?? "azul"}`}><span className="punto" />{NOMBRES_DESENLACE[accion] ?? accion}</span>
          ) : (
            <span className="pastilla azul"><span className="punto" />{corrida.estado === "en_cola" ? "En cola" : "Procesando"}</span>
          )}
          {r?.motivo ? <span className="pastilla rojo">{String(r.motivo)}</span> : null}
          {p.vueltas > 0 && <span className="pastilla azul">{p.vueltas} vueltas · {(p.tokensEntrada + p.tokensSalida).toLocaleString("es-CO")} tokens</span>}
          {r?.segundos !== undefined && <span className="pastilla">{String(r.segundos).replace(".", ",")} s</span>}
        </div>
        <div className="pestanas" style={{ padding: 0 }}>
          {(["proceso", "resultado", "correo", "consola"] as const).map((v) => (
            <button key={v} className={`pestana ${vista === v ? "activa" : ""}`} onClick={() => setVista(v)}>
              {{ proceso: "Proceso en vivo", resultado: "Resultado", correo: "Correo", consola: "Consola" }[v]}
            </button>
          ))}
        </div>
      </div>
      <div className="desplaza">
        {vista === "proceso" && (
          <div className="seccion linea-tiempo">
            {corrida.eventos.filter((e) => e.tipo !== "consola").map((e, i) => <Linea key={i} e={e} t0={t0} />)}
            {corrida.estado === "en_cola" && <div className="vacio">Esperando un trabajador libre…</div>}
          </div>
        )}
        {vista === "resultado" && (
          <>
            {r?.explicacion ? <div className="seccion"><h3>Explicación del agente</h3><div className="texto-2">{String(r.explicacion)}</div></div> : null}
            <div className="seccion">
              <h3>Datos encontrados</h3>
              {Object.keys(hallazgos).length ? (
                <table className="tabla">
                  <thead><tr><th>Campo</th><th>Valor</th><th>Origen</th></tr></thead>
                  <tbody>
                    {Object.entries(hallazgos).map(([campo, h]) => (
                      <tr key={campo} title={h.evidencia}>
                        <td>{campo}</td>
                        <td className="mono">{json(h.valor)}</td>
                        <td><span className={`pastilla ${String(h.metodo).includes("agente") || String(h.metodo).includes("modelo") ? "azul" : ""}`}>{h.metodo}</span> <span className="texto-3">{Math.round(Number(h.confianza) * 100)} %</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <div className="texto-3 chico">Sin datos todavía.</div>}
              {(r?.sin_resolver as string[] | undefined)?.length ? <div className="chico" style={{ marginTop: 8 }}>Sin resolver: {(r?.sin_resolver as string[]).join(", ")}</div> : null}
            </div>
            <div className="seccion">
              <h3>Así se radicaría en SGDEA</h3>
              {radicacion ? <pre className="correo-cuerpo mono">{json(radicacion.payload)}</pre> : <div className="texto-3 chico">Este correo no llegó a enviarse a SGDEA.</div>}
            </div>
            {(r?.avisos as string[] | undefined)?.length ? (
              <div className="seccion"><h3>Avisos</h3>{(r?.avisos as string[]).map((a, i) => <div key={i} className="chico texto-2">• {a}</div>)}</div>
            ) : null}
            {r?.traza ? <div className="seccion"><h3>Traza del error</h3><pre className="correo-cuerpo mono">{String(r.traza)}</pre></div> : null}
          </>
        )}
        {vista === "correo" && correo && (
          <div className="seccion">
            <div className="chico texto-3">De</div>
            <div>{correo.remitente} &lt;{correo.remitenteCorreo}&gt;</div>
            <div className="chico texto-3" style={{ marginTop: 8 }}>Asunto</div>
            <div>{correo.asunto}</div>
            <div className="chico texto-3" style={{ marginTop: 8 }}>En producción</div>
            <div>{correo.desenlaceProduccion} {correo.motivoProduccion && `· ${correo.motivoProduccion}`}</div>
            <div className="chico texto-3" style={{ margin: "8px 0 4px" }}>Cuerpo</div>
            <div className="correo-cuerpo">{correo.cuerpo}</div>
            <div className="chico texto-3" style={{ margin: "8px 0 4px" }}>Adjuntos</div>
            {correo.adjuntos.map((a) => (
              <div key={a.archivo || a.nombre} className="chico">
                {a.archivo ? <a href={`/api/bandeja/${encodeURIComponent(correo.caso)}/adjuntos/${encodeURIComponent(a.archivo)}`} target="_blank" rel="noreferrer">{a.nombre}</a> : a.nombre}
                <span className="texto-3"> · {Math.round(a.tamano / 1024)} KB{a.enLinea ? " · en línea" : ""}</span>
              </div>
            ))}
          </div>
        )}
        {vista === "consola" && (
          <div className="seccion">
            <pre className="correo-cuerpo mono" style={{ maxHeight: "none" }}>
              {corrida.eventos.filter((e) => e.tipo === "consola").map((e) => String(e.linea)).join("\n") || "Sin salida de consola."}
            </pre>
          </div>
        )}
      </div>
    </aside>
  );
}
