import { useEffect, useMemo, useState } from "react";
import { usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { conValor, resumen } from "../parametros.ts";
import { NOMBRES_DESENLACE, TONO_DESENLACE } from "../progreso.ts";
import type { Configuracion, Evento, Parametro, Regla } from "../tipos.ts";
import { LineaDeTiempo } from "./Dock.tsx";
import { Deslizador, Interruptor } from "./Editores.tsx";
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

// ------------------------------------------------------------------ corridas

export function VistaCorridas() {
  const corridas = usar((s) => s.corridas);
  const orden = usar((s) => s.orden);
  const bandeja = usar((s) => s.bandeja);
  const verCorrida = usar((s) => s.verCorrida);
  const enfocar = usar((s) => s.enfocar);
  const irA = usar((s) => s.irA);
  const lista = orden.map((id) => corridas[id]).filter((c) => c !== undefined);
  const terminadas = lista.filter((c) => c.resultado);
  const prod = useMemo(() => Object.fromEntries(bandeja.map((b) => [b.caso, b])), [bandeja]);

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
  return (
    <div className="vista">
      <Cabecera titulo="Corridas" sub="Lo procesado en este visor, comparado con lo que hizo producción con los mismos correos." />
      <div className="kpis grandes">
        <div><b>{terminadas.length}</b><span>correos procesados</span></div>
        <div className="verde"><b>{pct(cuenta.radicado ?? 0)} %</b><span>radicados ({cuenta.radicado ?? 0})</span></div>
        <div className="rojo"><b>{pct(cuenta.manual ?? 0)} %</b><span>a revisión manual ({cuenta.manual ?? 0})</span></div>
        <div className="azul"><b>{rescatados}</b><span>que producción mandó a manual y aquí se radican</span></div>
        <div className="ambar"><b>{perdidos}</b><span>que producción radicó y aquí no</span></div>
        <div><b>{tokens.toLocaleString("es-CO")}</b><span>tokens · mediana {String(mediana).replace(".", ",")} s por correo</span></div>
      </div>
      {segmentos.length > 0 && (
        <div className="barra-apilada">
          {segmentos.map(([a, n]) => (
            <span key={a} className={`seg tono-${TONO_DESENLACE[a] ?? "gris"}`} style={{ flexGrow: n }} title={`${NOMBRES_DESENLACE[a] ?? a}: ${n}`}>
              {pct(n) >= 8 ? `${NOMBRES_DESENLACE[a] ?? a} ${pct(n)} %` : ""}
            </span>
          ))}
        </div>
      )}
      <div className="tabla-envoltura">
        <table className="tabla">
          <thead><tr><th>Correo</th><th>Aquí</th><th>Motivo</th><th>Producción</th><th className="num">Vueltas IA</th><th className="num">Tokens</th><th className="num">Tiempo</th></tr></thead>
          <tbody>
            {lista.map((c) => {
              const r = c.resultado;
              const a = r ? String(r.accion) : "";
              const pr = prod[c.caso];
              return (
                <tr key={c.id} onClick={() => { void verCorrida(c.id); enfocar({ tipo: "corrida", id: c.id }); irA("flujo"); }}>
                  <td><span className="ficha">{c.caso}</span> <span className="texto-2 chico">{pr?.asunto.slice(0, 50)}</span></td>
                  <td>{r ? <span className={`pastilla ${TONO_DESENLACE[a] ?? ""}`}>{NOMBRES_DESENLACE[a] ?? a}</span> : <span className="pastilla azul"><span className="girando" />{c.estado === "en_cola" ? "En cola" : "Procesando"}</span>}</td>
                  <td className="chico texto-2">{r?.motivo ? String(r.motivo) : ""}</td>
                  <td className="chico">{pr ? <span className={`ficha tono-${TONO_DESENLACE[pr.desenlaceProduccion] ?? ""}`}>{pr.desenlaceProduccion}{pr.motivoProduccion ? ` · ${pr.motivoProduccion}` : ""}</span> : ""}</td>
                  <td className="num">{r ? String(r.vueltas ?? 0) : ""}</td>
                  <td className="num">{r ? (Number(r.tokens_entrada ?? 0) + Number(r.tokens_salida ?? 0)).toLocaleString("es-CO") : ""}</td>
                  <td className="num">{r?.segundos !== undefined ? `${String(r.segundos).replace(".", ",")} s` : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!lista.length && <div className="vacio">Aún no hay corridas. Elige casos en la correspondencia y córrelos.</div>}
      </div>
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
      <Cabecera titulo="Logs en vivo" sub="Todo lo que emite el motor, de todas las corridas, en el orden en que ocurre.">
        <button className={`boton ${pausa ? "primario" : ""}`} onClick={() => setPausa(!pausa)}>{pausa ? <><Icono n="play" tam={14} />Reanudar</> : <><Icono n="stop" tam={14} />Pausar</>}</button>
      </Cabecera>
      <div className="fila envuelve">
        {FILTROS.map((f) => (
          <button key={f.id} className={`ficha ${activos.includes(f.id) ? "activa" : ""}`} onClick={() => setActivos((a) => (a.includes(f.id) ? a.filter((x) => x !== f.id) : [...a, f.id]))}>{f.nombre}</button>
        ))}
        <div className="campo-icono crece"><Icono n="buscar" tam={14} /><input className="campo" placeholder="Filtrar por texto (campo, motivo, documento…)" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <span className="pastilla">{visibles.length} eventos</span>
      </div>
      <div className="logs-cuerpo">
        {visibles.length ? <LineaDeTiempo eventos={visibles} t0={0} mostrarCaso /> : <div className="vacio">Sin eventos con estos filtros.</div>}
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
      <Cabecera titulo="Herramientas del agente" sub="Con qué busca el agente los datos que las reglas no resolvieron. Ninguna escribe en SGDEA ni en el correo.">
        <button className="boton" onClick={() => { enfocar({ tipo: "nodo", id: "agente" }); irA("flujo"); }}><Icono n="ajustes" tam={14} />Editar lo que el modelo lee de ellas</button>
      </Cabecera>
      <div className="rejilla">
        {catalogo.herramientas_agente.map((h) => {
          const u = uso[h.nombre];
          const frase = h.descripcion.split(/(?<=\.)\s/)[0];
          return (
            <article key={h.nombre} className={`tarjeta ${abierta === h.nombre ? "abierta" : ""}`} onClick={() => setAbierta(abierta === h.nombre ? null : h.nombre)}>
              <div className="fila">
                <span className="icono-nodo ia"><Icono n="herramientas" tam={15} /></span>
                <code className="fuerte crece">{h.nombre}</code>
                <span className={`pastilla ${h.escribe ? "ambar" : "verde"}`}>{h.escribe ? "registra" : "lee"}</span>
              </div>
              <p className="texto-2 chico">{frase}</p>
              <div className="mini-kpis">
                <span><b>{u?.n ?? 0}</b> usos</span>
                <span><b>{u ? Math.round(u.ms / u.n) : 0}</b> ms prom.</span>
                <span className={u?.fallos ? "rojo" : ""}><b>{u?.fallos ?? 0}</b> fallos</span>
              </div>
              {abierta === h.nombre && (
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
      <Cabecera titulo="Proveedores y APIs" sub="Los sistemas con los que habla el pipeline. Aquí las escrituras se simulan; Vertex y el registro de personas se consultan de verdad." />
      <div className="rejilla">
        {catalogo.proveedores.map((p) => {
          const act = actividad[p.id] ?? (p.id.includes("sgdea") && p.id.includes("radic") ? actividad.sgdea_radicacion : undefined);
          const ops = p.operaciones ?? [];
          const escribe = ops.filter((o) => o.escribe).length;
          return (
            <article key={p.id} className={`tarjeta ${abierto === p.id ? "abierta" : ""}`} onClick={() => setAbierto(abierto === p.id ? null : p.id)}>
              <div className="fila">
                <span className={`icono-nodo ${p.tipo === "modelo" ? "ia" : ""}`}><Icono n={p.tipo === "modelo" ? "chispa" : p.tipo === "buzon" ? "correo" : "proveedores"} tam={15} /></span>
                <span className="fuerte crece">{p.nombre}</span>
              </div>
              <div className="fila envuelve">
                <span className="ficha">{p.tipo}</span>
                <span className="ficha tono-verde">{ops.length - escribe} lecturas</span>
                {escribe > 0 && <span className="ficha tono-ambar">{escribe} escrituras simuladas</span>}
              </div>
              <div className="mini-kpis">
                <span><b>{act?.n ?? 0}</b> llamadas</span>
                <span><b>{act?.n ? Math.round(act.ms / act.n) : 0}</b> ms prom.</span>
                <span className={act?.fallos ? "rojo" : ""}><b>{act?.fallos ?? 0}</b> fallos</span>
              </div>
              {abierto === p.id && (
                <div className="pila" onClick={(e) => e.stopPropagation()}>
                  {p.notas && <p className="chico texto-2">{p.notas}</p>}
                  {ops.map((o, i) => (
                    <div key={i} className="operacion">
                      <span className={`metodo m-${(o.metodo ?? "").split(/[+/]/)[0]?.toLowerCase()}`}>{o.metodo}</span>
                      <span className="crece"><b className="chico">{o.nombre}</b>{o.ruta && <span className="texto-3 diminuto mono"> {o.ruta}</span>}</span>
                      <span className={`ficha ${o.escribe ? "tono-ambar" : "tono-verde"}`}>{o.escribe ? "escribe" : "lee"}</span>
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
      <Cabecera titulo="Configuración" sub="Modelo, capacidad, cambios activos sobre el código de producción, perfiles guardados e historial. Todo rige desde el próximo correo.">
        <a className="boton" href="/api/configuracion/exportar"><Icono n="descargar" tam={14} />Exportar</a>
      </Cabecera>
      <div className="columnas-config">
        <div className="pila">
          <article className="tarjeta plana">
            <h3>Modelo de IA</h3>
            <label className="etiqueta">Modelo de Vertex
              <select className="campo" value={b.ia.modelo} onChange={(e) => setB({ ...b, ia: { ...b.ia, modelo: e.target.value } })}>
                {[...new Set([...modelos, b.ia.modelo])].map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="etiqueta">Temperatura
              <div className="fila">
                <Interruptor valor={b.ia.temperatura !== null} alCambiar={(v) => setB({ ...b, ia: { ...b.ia, temperatura: v ? 0.1 : null } })} />
                {b.ia.temperatura === null ? <span className="texto-3 chico">La del código (0,1)</span> : <Deslizador valor={b.ia.temperatura} min={0} max={1.5} paso={0.05} alCambiar={(t) => setB({ ...b, ia: { ...b.ia, temperatura: t } })} />}
              </div>
            </label>
            <label className="etiqueta">Ubicación
              <input className="campo" value={b.ia.ubicacion} onChange={(e) => setB({ ...b, ia: { ...b.ia, ubicacion: e.target.value } })} />
            </label>
          </article>
          <article className="tarjeta plana">
            <h3>Capacidad</h3>
            <label className="etiqueta">Correos a la vez · {b.concurrencia}
              <Deslizador valor={b.concurrencia} min={1} max={8} paso={1} alCambiar={(v) => setB({ ...b, concurrencia: v })} />
            </label>
            <label className="etiqueta">Tope por correo (segundos)
              <input className="campo" type="number" min={30} max={1800} value={b.topeSegundos} onChange={(e) => setB({ ...b, topeSegundos: Number(e.target.value) })} />
            </label>
            <button className="boton primario" onClick={() => void intentar(b, "modelo y capacidad")}><Icono n="rayo" tam={14} />Aplicar</button>
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
            {!configuracion.reglas.length && <p className="texto-3 chico">Ninguna todavía. Clic derecho en el lienzo del flujo para crear una: corre dentro del motor desde el próximo correo, sin tocar el código.</p>}
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
            {!activos.length && <p className="texto-3 chico">Todo corre con los valores del código de producción. Ajusta un nodo del flujo para cambiarlos.</p>}
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
              {!historial.length && <p className="texto-3 chico">Sin cambios todavía.</p>}
            </div>
          </article>
        </div>
      </div>
    </div>
  );
}
