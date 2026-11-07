import { useEffect, useMemo, useState } from "react";
import { usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { conValor, resumen } from "../parametros.ts";
import { NOMBRES_DESENLACE, TONO_DESENLACE, motivoLegible } from "../progreso.ts";
import type { Configuracion, Evento, Parametro, Regla } from "../tipos.ts";
import { LineaDeTiempo } from "./Dock.tsx";
import { Deslizador, Interruptor } from "./Editores.tsx";
import { Selector } from "./Selector.tsx";
import { fraseDeRegla } from "../flujo.ts";

function Cabecera({ titulo, sub, children }: { titulo: string; sub: string; children?: React.ReactNode }) {
  return (
    <header className="vista-cabeza">
      <div className="crece">
        <h1>{titulo}</h1>
        <p className="texto-2">{sub}</p>
      </div>
      {children}
    </header>
  );
}

/** «consultar_persona» → «Consultar persona». */
const TILDES: Record<string, string> = { catalogo: "catálogo", revision: "revisión", radicacion: "radicación", busqueda: "búsqueda", asignacion: "asignación", configuracion: "configuración", validacion: "validación", indice: "índice", pagina: "página" };
function nombreLegible(id: string): string {
  const t = id.replace(/[_-]+/g, " ").trim().split(" ").map((p) => TILDES[p] ?? p).join(" ");
  return t ? t[0]!.toUpperCase() + t.slice(1) : id;
}

/** Uso en esta sesión; sin uso, una frase en vez de tres ceros. */
function Uso({ n, ms, fallos, nombre }: { n: number; ms: number; fallos: number; nombre: "uso" | "llamada" }) {
  if (!n) return <div className="mini-kpis tenue">{nombre === "uso" ? "Sin usar todavía en esta sesión" : "Sin llamadas todavía en esta sesión"}</div>;
  return (
    <div className="mini-kpis">
      <span><b>{n.toLocaleString("es-CO")}</b> {nombre === "uso" ? (n === 1 ? "uso" : "usos") : n === 1 ? "llamada" : "llamadas"}</span>
      <span><b>{ms.toLocaleString("es-CO")}</b> ms en promedio</span>
      <span className={fallos ? "rojo" : ""}><b>{fallos}</b> {fallos === 1 ? "fallo" : "fallos"}</span>
    </div>
  );
}

const TIPO_SISTEMA: Record<string, string> = {
  buzon: "Buzón de correo",
  sgdea: "SGDEA",
  registro_personas: "Registro de personas",
  asignacion: "Asignación",
  cache: "Caché",
  almacenamiento: "Almacenamiento",
  modelo: "Modelo de IA",
};

// ------------------------------------------------------------------ corridas

export function VistaCorridas() {
  const corridas = usar((s) => s.corridas);
  const orden = usar((s) => s.orden);
  const bandeja = usar((s) => s.bandeja);
  const verCorrida = usar((s) => s.verCorrida);
  const enfocar = usar((s) => s.enfocar);
  const irA = usar((s) => s.irA);
  const flujo = usar((s) => s.flujo);
  const nombreFlujo = usar((s) => s.flujos.find((f) => f.id === s.flujo)?.nombre ?? s.flujo);
  // La comparación con producción solo existe para PQRD: la bandeja trae lo que hizo el job con esos correos.
  const conProduccion = flujo === "pqrd";
  const lista = orden.map((id) => corridas[id]).filter((c) => c !== undefined);
  const terminadas = lista.filter((c) => c.resultado);
  const prod = useMemo(() => (conProduccion ? Object.fromEntries(bandeja.map((b) => [b.caso, b])) : {}) as Record<string, (typeof bandeja)[number]>, [bandeja, conProduccion]);

  const cuenta: Record<string, number> = {};
  for (const c of terminadas) cuenta[String(c.resultado!.accion)] = (cuenta[String(c.resultado!.accion)] ?? 0) + 1;
  const tokens = terminadas.reduce((s, c) => s + Number(c.resultado?.tokens_entrada ?? 0) + Number(c.resultado?.tokens_salida ?? 0), 0);
  const segs = terminadas.map((c) => Number(c.resultado?.segundos ?? 0)).filter((x) => x > 0).sort((a, b) => a - b);
  const mediana = segs.length ? segs[Math.floor(segs.length / 2)]! : 0;
  const pct = (n: number) => (terminadas.length ? Math.round((n / terminadas.length) * 100) : 0);

  // Frente a producción: de los que allá fueron a manual, cuántos aquí se radican, y al revés.
  const rescatados = terminadas.filter((c) => prod[c.caso]?.desenlaceProduccion === "manual" && c.resultado?.accion === "radicado").length;
  const perdidos = terminadas.filter((c) => prod[c.caso]?.desenlaceProduccion === "radicado" && c.resultado?.accion !== "radicado").length;

  const segmentos = Object.entries(cuenta).sort((a, b) => b[1] - a[1]);
  const abrir = (id: string) => {
    void verCorrida(id);
    enfocar({ tipo: "corrida", id });
    irA("flujo");
  };
  return (
    <div className="vista">
      <Cabecera titulo="Corridas" sub={conProduccion ? "Cómo terminó cada correo corrido aquí, al lado de lo que hizo producción con el mismo correo. Abre una fila para verla en el flujo." : "Cómo terminó cada correo corrido en este flujo. Abre una fila para verla en el flujo."} />
      {!lista.length ? (
        <div className="estado-vacio">
          <span className="estado-vacio-icono"><Icono n="historial" tam={22} /></span>
          <h2>Todavía no has corrido correos en «{nombreFlujo}»</h2>
          <p>Ve al flujo, elige correos en la correspondencia y pulsa «Correr». Cada resultado aparecerá aquí con su motivo, su tiempo y su comparación con producción.</p>
          <button className="boton primario" onClick={() => irA("flujo")}><Icono n="flujo" tam={14} />Ir al flujo</button>
        </div>
      ) : (
        <>
          <section className="resumen" aria-label="Resumen">
            <div className="resumen-cifras">
              <div><b>{terminadas.length.toLocaleString("es-CO")}</b><span>correos terminados{lista.length > terminadas.length ? ` · ${lista.length - terminadas.length} en curso` : ""}</span></div>
              <div><b className="verde">{pct(cuenta.radicado ?? 0)} %</b><span>radicados ({cuenta.radicado ?? 0})</span></div>
              <div><b className="rojo">{pct(cuenta.manual ?? 0)} %</b><span>a revisión manual ({cuenta.manual ?? 0})</span></div>
              <div><b>{mediana ? `${String(mediana).replace(".", ",")} s` : "—"}</b><span>tiempo típico por correo</span></div>
            </div>
            {segmentos.length > 0 && (
              <div className="barra-apilada" role="img" aria-label={segmentos.map(([a, n]) => `${NOMBRES_DESENLACE[a] ?? a}: ${n}`).join(", ")}>
                {segmentos.map(([a, n]) => (
                  <span key={a} className={`seg tono-${TONO_DESENLACE[a] ?? "gris"}`} style={{ flexGrow: n }} title={`${NOMBRES_DESENLACE[a] ?? a}: ${n}`}>
                    {pct(n) >= 9 ? <span>{NOMBRES_DESENLACE[a] ?? a} {pct(n)} %</span> : null}
                  </span>
                ))}
              </div>
            )}
            {segmentos.length > 0 && (
              <ul className="leyenda-barra" aria-hidden>
                {segmentos.map(([a, n]) => (
                  <li key={a}><i className={`seg tono-${TONO_DESENLACE[a] ?? "gris"}`} />{NOMBRES_DESENLACE[a] ?? a} <b>{n}</b></li>
                ))}
              </ul>
            )}
            <div className="resumen-pie">
              {conProduccion && (
                <p>
                  <b>Frente a producción:</b> {rescatados === 1 ? "1 correo que allá fue" : `${rescatados} correos que allá fueron`} a revisión manual aquí se radica{rescatados === 1 ? "" : "n"}
                  {perdidos ? `, y ${perdidos} que allá se radicaron aquí no.` : ", y ninguno que allá se radicó se pierde aquí."}
                </p>
              )}
              <p className="texto-3">{tokens.toLocaleString("es-CO")} tokens de IA en total.</p>
            </div>
          </section>
          <div className="tabla-envoltura">
            <table className="tabla tabla-corridas">
              <thead><tr><th>Correo</th><th>Resultado aquí</th><th>Motivo</th>{conProduccion && <th>En producción</th>}<th className="num" title="Veces que el agente consultó al modelo de IA">Consultas a la IA</th><th className="num">Tokens</th><th className="num">Tiempo</th></tr></thead>
              <tbody>
                {lista.map((c) => {
                  const r = c.resultado;
                  const a = r ? String(r.accion) : "";
                  const pr = prod[c.caso];
                  const motivo = r?.motivo ? String(r.motivo) : "";
                  return (
                    <tr key={c.id} tabIndex={0} onClick={() => abrir(c.id)} onKeyDown={(e) => { if (e.key === "Enter") abrir(c.id); }}>
                      <td className="celda-correo"><span className="ficha mono">{c.caso}</span><span className="texto-2">{pr?.asunto ?? String(c.resultado?.asunto ?? "")}</span></td>
                      <td>{r ? <span className={`pastilla ${TONO_DESENLACE[a] ?? ""}`}>{NOMBRES_DESENLACE[a] ?? a}</span> : <span className="pastilla azul"><span className="girando" />{c.estado === "en_cola" ? "En espera" : "Corriendo"}</span>}</td>
                      <td className="celda-motivo">{motivo ? <><span>{motivoLegible(motivo)}</span>{motivoLegible(motivo) !== motivo && <code>{motivo}</code>}</> : null}</td>
                      {conProduccion && (
                        <td className="celda-motivo">
                          {pr?.desenlaceProduccion ? (
                            <>
                              <span className={`ficha tono-${TONO_DESENLACE[pr.desenlaceProduccion] ?? "gris"}`}>{NOMBRES_DESENLACE[pr.desenlaceProduccion] ?? pr.desenlaceProduccion}</span>
                              {pr.motivoProduccion && <span className="texto-3">{motivoLegible(pr.motivoProduccion)}</span>}
                            </>
                          ) : <span className="texto-3" title="No hay registro de lo que hizo producción con este correo">Sin dato</span>}
                        </td>
                      )}
                      <td className="num">{r ? String(r.vueltas ?? 0) : ""}</td>
                      <td className="num">{r ? (Number(r.tokens_entrada ?? 0) + Number(r.tokens_salida ?? 0)).toLocaleString("es-CO") : ""}</td>
                      <td className="num">{r?.segundos !== undefined ? `${String(r.segundos).replace(".", ",")} s` : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------- logs

const FILTROS: { id: string; nombre: string; pasa: (e: Evento) => boolean }[] = [
  { id: "etapas", nombre: "Etapas", pasa: (e) => e.tipo === "etapa_fin" || e.tipo === "inicio" || e.tipo === "resultado" },
  { id: "ia", nombre: "Modelo", pasa: (e) => e.tipo === "modelo_inicio" || e.tipo === "modelo_fin" },
  { id: "herramientas", nombre: "Herramientas", pasa: (e) => e.tipo === "herramienta" },
  { id: "sistemas", nombre: "Sistemas externos", pasa: (e) => e.tipo === "proveedor" || e.tipo === "escritura" },
  { id: "logs", nombre: "Logs del servicio", pasa: (e) => e.tipo === "log" },
  { id: "errores", nombre: "Solo errores", pasa: (e) => e.ok === false || Boolean(e.fallo) || e.severidad === "WARNING" || e.severidad === "ERROR" || (e.tipo === "resultado" && e.accion === "error") },
];

export function VistaLogs() {
  const recientes = usar((s) => s.recientes);
  const irA = usar((s) => s.irA);
  const [activos, setActivos] = useState<string[]>(["etapas", "ia", "herramientas", "sistemas", "logs"]);
  const [q, setQ] = useState("");
  const [pausa, setPausa] = useState(false);
  const [congelados, setCongelados] = useState<Evento[]>([]);
  useEffect(() => {
    if (!pausa) setCongelados(recientes);
  }, [recientes, pausa]);
  const visibles = useMemo(() => {
    const soloErrores = activos.includes("errores");
    const filtros = FILTROS.filter((f) => activos.includes(f.id) && f.id !== "errores");
    const texto = q.trim().toLowerCase();
    return congelados
      .filter((e) => (soloErrores ? FILTROS.find((f) => f.id === "errores")!.pasa(e) : filtros.some((f) => f.pasa(e))))
      .filter((e) => !texto || JSON.stringify(e).toLowerCase().includes(texto))
      .slice(-800);
  }, [congelados, activos, q]);
  return (
    <div className="vista vista-alta">
      <Cabecera titulo="Logs en vivo" sub="Todo lo que hace el motor en este flujo, de todas las corridas, en el orden en que ocurre.">
        <button className={`boton ${pausa ? "primario" : ""}`} onClick={() => setPausa(!pausa)} aria-pressed={pausa}>{pausa ? <><Icono n="play" tam={14} />Reanudar</> : <><Icono n="pausa" tam={14} />Pausar</>}</button>
      </Cabecera>
      <div className="logs-filtros">
        <div className="filtros" role="group" aria-label="Qué mostrar">
          {FILTROS.map((f) => (
            <button key={f.id} className={activos.includes(f.id) ? "activo" : ""} aria-pressed={activos.includes(f.id)} onClick={() => setActivos((a) => (a.includes(f.id) ? a.filter((x) => x !== f.id) : [...a, f.id]))}>{f.nombre}</button>
          ))}
        </div>
        <div className="campo-icono crece"><Icono n="buscar" tam={14} /><input className="campo" placeholder="Buscar en los eventos (campo, motivo, documento…)" aria-label="Buscar en los eventos" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <span className="texto-3 chico">{visibles.length.toLocaleString("es-CO")} evento{visibles.length === 1 ? "" : "s"}{pausa ? " · en pausa" : ""}</span>
      </div>
      <div className="logs-cuerpo">
        {visibles.length ? (
          <LineaDeTiempo eventos={visibles} t0={0} mostrarCaso />
        ) : congelados.length ? (
          <div className="estado-vacio dentro">
            <span className="estado-vacio-icono"><Icono n="filtro" tam={20} /></span>
            <h2>Ningún evento coincide</h2>
            <p>Hay {congelados.length.toLocaleString("es-CO")} eventos, pero ninguno pasa estos filtros.</p>
            <button className="boton chico" onClick={() => { setQ(""); setActivos(["etapas", "ia", "herramientas", "sistemas", "logs"]); }}>Quitar los filtros</button>
          </div>
        ) : (
          <div className="estado-vacio dentro">
            <span className="estado-vacio-icono"><Icono n="logs" tam={20} /></span>
            <h2>Todavía no ha pasado nada</h2>
            <p>Cuando corras un correo, cada paso del motor aparecerá aquí en el momento: etapas, consultas a la IA, herramientas y sistemas.</p>
            <button className="boton chico" onClick={() => irA("flujo")}><Icono n="flujo" tam={13} />Ir al flujo</button>
          </div>
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------- herramientas

export function VistaHerramientas() {
  const catalogo = usar((s) => s.catalogo);
  const recientes = usar((s) => s.recientes);
  const enfocar = usar((s) => s.enfocar);
  const irA = usar((s) => s.irA);
  const [abierta, setAbierta] = useState<string | null>(null);
  const uso = useMemo(() => {
    const u: Record<string, { n: number; ms: number; fallos: number }> = {};
    for (const e of recientes) {
      if (e.tipo !== "herramienta") continue;
      const h = (u[String(e.herramienta)] ??= { n: 0, ms: 0, fallos: 0 });
      h.n += 1;
      h.ms += Number(e.ms ?? 0);
      if (!e.ok) h.fallos += 1;
    }
    return u;
  }, [recientes]);
  if (!catalogo) return null;
  return (
    <div className="vista">
      <Cabecera titulo="Herramientas de la IA" sub="Con qué busca el agente de IA los datos que las reglas no resolvieron. Ninguna escribe en SGDEA ni en el correo. Toca una para ver el detalle.">
        {catalogo.nodos.some((n) => n.id === "agente") && <button className="boton" onClick={() => { enfocar({ tipo: "nodo", id: "agente" }); irA("flujo"); }}><Icono n="ajustes" tam={14} />Editar cómo las describe al modelo</button>}
      </Cabecera>
      {!catalogo.herramientas_agente.length && (
        <div className="estado-vacio">
          <span className="estado-vacio-icono"><Icono n="herramientas" tam={22} /></span>
          <h2>Este flujo no usa herramientas</h2>
          <p>La IA de este flujo trabaja solo con el texto del correo y sus adjuntos.</p>
        </div>
      )}
      <div className="rejilla">
        {catalogo.herramientas_agente.map((h) => {
          const u = uso[h.nombre];
          const frase = h.descripcion.split(/(?<=\.)\s/)[0];
          const abierto = abierta === h.nombre;
          return (
            <article key={h.nombre} className={`tarjeta ${abierto ? "abierta" : ""}`} role="button" tabIndex={0} aria-expanded={abierto} onClick={() => setAbierta(abierto ? null : h.nombre)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setAbierta(abierto ? null : h.nombre); } }}>
              <div className="fila arriba">
                <span className="icono-nodo ia"><Icono n="herramientas" tam={15} /></span>
                <div className="crece tarjeta-titulo">
                  <b>{nombreLegible(h.nombre)}</b>
                  <code>{h.nombre}</code>
                </div>
                <span className={`pastilla diminuto ${h.escribe ? "ambar" : "verde"}`}>{h.escribe ? "Anota hallazgos" : "Solo lee"}</span>
              </div>
              <p className="texto-2 chico">{frase}</p>
              <Uso n={u?.n ?? 0} ms={u ? Math.round(u.ms / u.n) : 0} fallos={u?.fallos ?? 0} nombre="uso" />
              {abierto && (
                <div className="pila" onClick={(e) => e.stopPropagation()}>
                  <p className="chico">{h.descripcion}</p>
                  {h.efectos && <p className="chico texto-2">Efectos: {h.efectos}</p>}
                  {h.parametros && Object.keys(h.parametros as object).length > 0 ? (
                    <div className="fichas">{Object.keys(h.parametros as object).map((p) => <span key={p} className="ficha mono">{p}</span>)}</div>
                  ) : null}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}

// --------------------------------------------------------------- proveedores

export function VistaProveedores() {
  const catalogo = usar((s) => s.catalogo);
  const recientes = usar((s) => s.recientes);
  const [abierto, setAbierto] = useState<string | null>(null);
  const actividad = useMemo(() => {
    const a: Record<string, { n: number; ms: number; fallos: number }> = {};
    const sumar = (id: string, e: Evento) => {
      const x = (a[id] ??= { n: 0, ms: 0, fallos: 0 });
      x.n += 1;
      x.ms += Number(e.ms ?? 0);
      if (e.ok === false) x.fallos += 1;
    };
    for (const e of recientes) {
      if (e.tipo === "modelo_fin") sumar("vertex", e);
      else if (e.tipo === "proveedor") sumar("registro_personas", e);
      else if (e.tipo === "escritura") sumar(e.sistema === "sgdea" ? "sgdea_radicacion" : "graph", e);
    }
    return a;
  }, [recientes]);
  if (!catalogo) return null;
  return (
    <div className="vista">
      <Cabecera titulo="Sistemas que consulta" sub="Con qué sistemas habla el flujo. Las lecturas son reales (Vertex AI, el registro de personas); lo que se escribiría en SGDEA o en el buzón no se envía: queda registrado para que veas qué habría pasado. Toca uno para ver sus operaciones." />
      <div className="rejilla">
        {catalogo.proveedores.map((p) => {
          const act = actividad[p.id] ?? (p.id.includes("sgdea") && p.id.includes("radic") ? actividad.sgdea_radicacion : undefined);
          const ops = p.operaciones ?? [];
          const escribe = ops.filter((o) => o.escribe).length;
          const lee = ops.length - escribe;
          const esAbierto = abierto === p.id;
          return (
            <article key={p.id} className={`tarjeta ${esAbierto ? "abierta" : ""}`} role="button" tabIndex={0} aria-expanded={esAbierto} onClick={() => setAbierto(esAbierto ? null : p.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setAbierto(esAbierto ? null : p.id); } }}>
              <div className="fila arriba">
                <span className={`icono-nodo ${p.tipo === "modelo" ? "ia" : ""}`}><Icono n={p.tipo === "modelo" ? "chispa" : p.tipo === "buzon" ? "correo" : "proveedores"} tam={15} /></span>
                <div className="crece tarjeta-titulo">
                  <b>{p.nombre}</b>
                  <small>{TIPO_SISTEMA[p.tipo] ?? nombreLegible(p.tipo)}</small>
                </div>
              </div>
              <div className="fila envuelve">
                {lee > 0 && <span className="ficha tono-verde">{lee} {lee === 1 ? "lectura" : "lecturas"}</span>}
                {escribe > 0 && <span className="ficha tono-ambar" title="En esta plataforma no se envían: se registran para mostrar qué habría hecho el job">{escribe} {escribe === 1 ? "escritura retenida" : "escrituras retenidas"}</span>}
              </div>
              <Uso n={act?.n ?? 0} ms={act?.n ? Math.round(act.ms / act.n) : 0} fallos={act?.fallos ?? 0} nombre="llamada" />
              {esAbierto && (
                <div className="pila" onClick={(e) => e.stopPropagation()}>
                  {p.notas && <p className="chico texto-2">{p.notas}</p>}
                  {ops.map((o, i) => (
                    <div key={i} className="operacion">
                      <span className={`metodo m-${(o.metodo ?? "").split(/[+/]/)[0]?.toLowerCase()}`}>{o.metodo}</span>
                      <span className="crece"><b className="chico">{o.nombre}</b>{o.ruta && <span className="texto-3 diminuto mono"> {o.ruta}</span>}</span>
                      <span className={`ficha ${o.escribe ? "tono-ambar" : "tono-verde"}`}>{o.escribe ? "Retenida" : "Lee"}</span>
                    </div>
                  ))}
                  {p.configuracion?.length ? <div className="fichas">{p.configuracion.map((c) => <span key={c} className="ficha mono">{c}</span>)}</div> : null}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------- configuración

export function VistaConfiguracion() {
  const configuracion = usar((s) => s.configuracion);
  const modelos = usar((s) => s.modelos);
  const catalogo = usar((s) => s.catalogo);
  const guardar = usar((s) => s.guardarConfiguracion);
  const perfiles = usar((s) => s.perfiles);
  const historial = usar((s) => s.historial);
  const cargarHistorial = usar((s) => s.cargarHistorial);
  const guardarPerfil = usar((s) => s.guardarPerfil);
  const aplicarPerfil = usar((s) => s.aplicarPerfil);
  const borrarPerfil = usar((s) => s.borrarPerfil);
  const avisar = usar((s) => s.avisar);
  const enfocar = usar((s) => s.enfocar);
  const irA = usar((s) => s.irA);
  const crearRegla = usar((s) => s.crearRegla);
  const centrar = usar((s) => s.centrar);
  const [b, setB] = useState<Configuracion | null>(configuracion);
  const [nombre, setNombre] = useState("");
  const [descripcion, setDescripcion] = useState("");
  useEffect(() => setB(configuracion), [configuracion]);
  useEffect(() => void cargarHistorial(), [cargarHistorial]);

  const porClave = useMemo(() => {
    const m: Record<string, { p: Parametro; nodo: string; nodoId: string }> = {};
    for (const n of catalogo?.nodos ?? []) for (const p of n.parametros) {
      m[p.clave] = { p, nodo: n.nombre, nodoId: n.id };
      if (p.env) m[p.env] = { p, nodo: n.nombre, nodoId: n.id };
    }
    return m;
  }, [catalogo]);

  if (!b || !configuracion) return null;
  const activos = [...Object.keys(configuracion.parametros), ...Object.keys(configuracion.entorno)];
  const intentar = async (c: Configuracion, nota: string) => {
    try {
      const n = await guardar(c, nota);
      avisar(n ? `${n} cambio${n > 1 ? "s" : ""} aplicado${n > 1 ? "s" : ""}` : "Sin cambios");
    } catch (e) {
      avisar((e as Error).message, "error");
    }
  };

  return (
    <div className="vista">
      <Cabecera titulo="Ajustes" sub="Qué modelo de IA usa el flujo, cuántos correos corre a la vez y qué cambios propios aplicas sobre el código de producción. Todo rige desde el próximo correo.">
        <a className="boton" href="/api/configuracion/exportar" title="Descarga estos ajustes en un archivo"><Icono n="descargar" tam={14} />Exportar ajustes</a>
      </Cabecera>
      <div className="columnas-config">
        <div className="pila">
          <article className="tarjeta plana">
            <h3>Modelo de IA</h3>
            <div className="etiqueta">Modelo
              <Selector valor={b.ia.modelo} opciones={[...new Set([...modelos, b.ia.modelo])].map((m) => ({ valor: m, etiqueta: m }))} alCambiar={(m) => setB({ ...b, ia: { ...b.ia, modelo: m } })} etiqueta="Modelo de IA" />
            </div>
            <label className="etiqueta">Temperatura
              <div className="fila">
                <Interruptor valor={b.ia.temperatura !== null} alCambiar={(v) => setB({ ...b, ia: { ...b.ia, temperatura: v ? 0.1 : null } })} />
                {b.ia.temperatura === null ? <span className="texto-3 chico">La del código de producción (0,1)</span> : <Deslizador valor={b.ia.temperatura} min={0} max={1.5} paso={0.05} alCambiar={(t) => setB({ ...b, ia: { ...b.ia, temperatura: t } })} />}
              </div>
            </label>
            <label className="etiqueta">Región de Vertex AI
              <input className="campo" value={b.ia.ubicacion} onChange={(e) => setB({ ...b, ia: { ...b.ia, ubicacion: e.target.value } })} />
            </label>
          </article>
          <article className="tarjeta plana">
            <h3>Capacidad</h3>
            <label className="etiqueta">Correos que corren a la vez: {b.concurrencia}
              <Deslizador valor={b.concurrencia} min={1} max={8} paso={1} alCambiar={(v) => setB({ ...b, concurrencia: v })} />
            </label>
            <label className="etiqueta">Tiempo máximo por correo, en segundos
              <input className="campo" type="number" min={30} max={1800} value={b.topeSegundos} onChange={(e) => setB({ ...b, topeSegundos: Number(e.target.value) })} />
            </label>
            <button className="boton primario" onClick={() => void intentar(b, "modelo y capacidad")}><Icono n="ok" tam={14} />Aplicar modelo y capacidad</button>
          </article>
          <article className="tarjeta plana">
            <h3>Perfiles</h3>
            <p className="texto-2 chico">Guarda la configuración actual con un nombre para volver a ella o compararla.</p>
            <div className="fila">
              <input className="campo crece" placeholder="Nombre del perfil" value={nombre} onChange={(e) => setNombre(e.target.value)} />
              <button className="boton" disabled={!nombre.trim()} onClick={() => void guardarPerfil(nombre.trim(), descripcion).then(() => { setNombre(""); setDescripcion(""); avisar("Perfil guardado"); }).catch((e: Error) => avisar(e.message, "error"))}>Guardar</button>
            </div>
            <input className="campo" placeholder="Descripción (opcional)" value={descripcion} onChange={(e) => setDescripcion(e.target.value)} />
            {perfiles.map((p) => (
              <div key={p.nombre} className="perfil">
                <div className="crece"><b>{p.nombre}</b><div className="texto-3 diminuto">{p.cambios} ajustes · {new Date(p.t).toLocaleString("es-CO")}{p.descripcion ? ` · ${p.descripcion}` : ""}</div></div>
                <button className="boton chico" onClick={() => void aplicarPerfil(p.nombre).then(() => avisar(`Perfil ${p.nombre} aplicado`)).catch((e: Error) => avisar(e.message, "error"))}>Aplicar</button>
                <button className="boton chico peligro" aria-label="Borrar" onClick={() => void borrarPerfil(p.nombre)}><Icono n="cerrar" tam={12} /></button>
              </div>
            ))}
          </article>
        </div>
        <div className="pila">
          <article className="tarjeta plana">
            <div className="fila">
              <h3 className="crece">Reglas personalizadas <span className="pastilla rosa">{configuracion.reglas.length}</span></h3>
              <button className="boton chico" onClick={() => crearRegla("entrada")}><Icono n="mas" tam={13} />Nueva regla</button>
            </div>
            {!configuracion.reglas.length && <p className="texto-3 chico">Ninguna todavía. Una regla propia decide algo antes que el motor (por ejemplo, mandar a revisión manual ciertos remitentes) sin tocar el código. Créala con «Nueva regla» o con clic derecho en el flujo.</p>}
            {configuracion.reglas.map((r) => (
              <div key={r.id} className="cambio">
                <span className="mini-icono t-rosa"><Icono n="regla" tam={12} /></span>
                <div className="crece">
                  <button className="enlace" onClick={() => { irA("flujo"); centrar(r.id); }}>{r.nombre}</button>
                  <div className="texto-3 diminuto mono">{fraseDeRegla(r)}</div>
                </div>
                <Interruptor valor={r.activa} alCambiar={(v) => void intentar({ ...configuracion, reglas: configuracion.reglas.map((x) => (x.id === r.id ? { ...x, activa: v } : x)) }, `regla «${r.nombre}» ${v ? "activada" : "desactivada"}`)} />
              </div>
            ))}
          </article>
          <article className="tarjeta plana">
            <div className="fila">
              <h3 className="crece">Cambios activos <span className="pastilla ambar">{activos.length}</span></h3>
              {activos.length > 0 && <button className="boton chico peligro" onClick={() => void intentar({ ...configuracion, parametros: {}, entorno: {} }, "volver a producción")}>Volver todo a producción</button>}
            </div>
            {!activos.length && <p className="texto-3 chico">Todo corre con los valores del código de producción. Para cambiar uno, abre una etapa en el flujo y ajústala.</p>}
            {activos.map((k) => {
              const info = porClave[k];
              const valor = k in configuracion.parametros ? configuracion.parametros[k] : configuracion.entorno[k];
              return (
                <div key={k} className="cambio">
                  <div className="crece">
                    <button className="enlace" onClick={() => { if (info) { enfocar({ tipo: "nodo", id: info.nodoId }); irA("flujo"); } }}>{info?.nodo ?? "?"} · <code>{info?.p.nombre ?? k}</code></button>
                    <div className="diff"><span className="antes mono">{resumen(info?.p.valor, 80)}</span><span className="despues mono">{resumen(valor, 80)}</span></div>
                  </div>
                  {info && <button className="boton chico" title="Volver al valor del código" onClick={() => void intentar(conValor(configuracion, info.p, undefined), `restaurar ${info.p.nombre}`)}><Icono n="deshacer" tam={13} /></button>}
                </div>
              );
            })}
          </article>
          <article className="tarjeta plana">
            <h3><Icono n="historial" tam={15} /> Historial de cambios</h3>
            <div className="historial">
              {historial.slice(0, 60).map((h, i) => (
                <div key={i} className="hito">
                  <span className="texto-3 diminuto">{new Date(h.t).toLocaleString("es-CO")}</span>
                  {h.clave.startsWith("regla.") ? (
                    <>
                      <span className="chico"><span className="ficha tono-rosa">regla</span> {h.nota ?? h.clave}</span>
                      {(h.despues ?? h.antes) && <span className="texto-3 diminuto mono">{fraseDeRegla((h.despues ?? h.antes) as Regla)}</span>}
                    </>
                  ) : (
                    <>
                      <span className="chico"><code>{h.clave.split(".").pop()}</code>{h.nota ? <span className="texto-3"> · {h.nota}</span> : null}</span>
                      <div className="diff"><span className="antes mono">{resumen(h.antes, 60)}</span><span className="despues mono">{resumen(h.despues, 60)}</span></div>
                    </>
                  )}
                </div>
              ))}
              {!historial.length && <p className="texto-3 chico">Aún no hay cambios. Cada ajuste que apliques queda anotado aquí con su valor anterior.</p>}
            </div>
          </article>
        </div>
      </div>
    </div>
  );
}
