import { useEffect, useMemo, useState } from "react";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { ETIQUETA_CLASE, ORDEN_CLASE, ajustado, clase, conValor, esRegex, resumen, vigente, type Clase } from "../parametros.ts";
import { NOMBRES_DESENLACE, TONO_DESENLACE, nodoDeEtapa, progreso, segundos } from "../progreso.ts";
import type { Configuracion, Correo, Nodo, Parametro } from "../tipos.ts";
import { Codigo, Deslizador, EditorJson, EditorLista, EditorMapa, EditorRegex, Interruptor } from "./Editores.tsx";

function texto(v: unknown): string {
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

// ------------------------------------------------------------------ editores

function EditorPrompt({ valor, alCambiar }: { valor: unknown; alCambiar: (v: unknown) => void }) {
  const esMapa = valor !== null && typeof valor === "object" && !Array.isArray(valor);
  const claves = esMapa ? Object.keys(valor as Record<string, unknown>) : [];
  const [clave, setClave] = useState(claves[0] ?? "");
  if (!esMapa) return <Codigo valor={String(valor ?? "")} alCambiar={alCambiar} lenguaje="markdown" minAlto="220px" />;
  const mapa = valor as Record<string, unknown>;
  return (
    <div className="pila">
      <div className="fichas">
        {claves.map((k) => (
          <button key={k} className={`ficha ${k === clave ? "activa" : ""}`} onClick={() => setClave(k)}>{k}</button>
        ))}
      </div>
      {clave && <Codigo key={clave} valor={String(mapa[clave] ?? "")} alCambiar={(t) => alCambiar({ ...mapa, [clave]: t })} lenguaje="markdown" minAlto="200px" />}
    </div>
  );
}

function EditorEsquemas({ valor, alCambiar }: { valor: unknown; alCambiar: (v: unknown) => void }) {
  const lista = (Array.isArray(valor) ? valor : []) as { name: string; description?: string; parameters?: { properties?: Record<string, { description?: string }> } }[];
  const cambiar = (i: number, cambio: (e: (typeof lista)[number]) => (typeof lista)[number]) => alCambiar(lista.map((e, j) => (j === i ? cambio(structuredClone(e)) : e)));
  return (
    <div className="pila">
      {lista.map((h, i) => (
        <div key={h.name} className="subtarjeta">
          <div className="fila"><code className="fuerte">{h.name}</code><span className="texto-3 chico">lo que el modelo lee de esta herramienta</span></div>
          <textarea className="campo" rows={2} value={h.description ?? ""} onChange={(e) => cambiar(i, (x) => ({ ...x, description: e.target.value }))} />
          {Object.entries(h.parameters?.properties ?? {}).map(([p, d]) => (
            <label key={p} className="mapa-fila">
              <code className="mapa-clave">{p}</code>
              <input
                className="campo"
                value={d.description ?? ""}
                onChange={(e) =>
                  cambiar(i, (x) => {
                    x.parameters!.properties![p]!.description = e.target.value;
                    return x;
                  })
                }
              />
            </label>
          ))}
        </div>
      ))}
    </div>
  );
}

function Editor({ p, valor, alCambiar }: { p: Parametro; valor: unknown; alCambiar: (v: unknown) => void }) {
  const c = clase(p);
  if (c === "prompt") return <EditorPrompt valor={valor} alCambiar={alCambiar} />;
  if (c === "esquemas") return <EditorEsquemas valor={valor} alCambiar={alCambiar} />;
  if (c === "regex") return <EditorRegex valor={esRegex(valor) ? valor : { regex: String(valor), flags: "" }} alCambiar={alCambiar} />;
  if (c === "umbral") return <Deslizador valor={Number(valor)} alCambiar={alCambiar} />;
  if (c === "numero") return <input className="campo mono" type="number" step="any" value={Number(valor)} onChange={(e) => alCambiar(Number(e.target.value))} />;
  if (c === "booleano") return <Interruptor valor={Boolean(valor)} alCambiar={alCambiar} />;
  if (c === "lista") return <EditorLista valor={valor as unknown[]} alCambiar={alCambiar} />;
  if (c === "mapa") return <EditorMapa valor={valor as Record<string, unknown>} alCambiar={alCambiar} />;
  if (typeof valor === "string") return <input className="campo" value={valor} onChange={(e) => alCambiar(e.target.value)} />;
  return <EditorJson valor={valor} alCambiar={alCambiar} />;
}

function TarjetaParametro({ p, borrador, setBorrador }: { p: Parametro; borrador: unknown; setBorrador: (v: unknown | undefined) => void }) {
  const configuracion = usar((s) => s.configuracion);
  const actual = vigente(p, configuracion);
  const editado = borrador !== undefined;
  const esAjustado = ajustado(p, configuracion);
  const [abierta, setAbierta] = useState(false);
  const c = clase(p);
  const valor = editado ? borrador : actual;
  return (
    <div className={`param ${esAjustado ? "ajustado" : ""} ${editado ? "editado" : ""}`}>
      <button className="param-cabeza" onClick={() => setAbierta(!abierta)} aria-expanded={abierta}>
        <span className="param-nombre"><code>{p.nombre}</code></span>
        <span className="param-chips">
          <span className={`ficha clase-${c}`}>{ETIQUETA_CLASE[c]}</span>
          {editado && <span className="pastilla azul">sin aplicar</span>}
          {!editado && esAjustado && <span className="pastilla ambar">ajustado</span>}
          {p.ajuste === null && <span className="pastilla">fijo</span>}
        </span>
        <span className="param-resumen texto-3 mono">{resumen(valor)}</span>
      </button>
      {abierta && (
        <div className="param-cuerpo">
          <p className="texto-2 chico">{p.descripcion}</p>
          {p.ajuste === null ? (
            <>
              <Codigo valor={texto(p.valor)} soloLectura lenguaje={typeof p.valor === "string" ? "texto" : "json"} />
              <p className="texto-3 chico">{p.motivo_fijo ?? p.nota_sobrescritura}</p>
            </>
          ) : (
            <>
              <Editor p={p} valor={valor} alCambiar={(v) => setBorrador(JSON.stringify(v) === JSON.stringify(actual) ? undefined : v)} />
              <div className="fila">
                {editado && <button className="boton chico" onClick={() => setBorrador(undefined)}>Descartar edición</button>}
                {esAjustado && !editado && <button className="boton chico" onClick={() => setBorrador(p.valor)}><Icono n="deshacer" tam={13} />Volver al valor del código</button>}
                <span className="espacio" />
                <span className="texto-3 chico mono">{p.modulo}</span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------- inspector nodo

function ActividadDelNodo({ n }: { n: Nodo }) {
  const catalogo = usar((s) => s.catalogo);
  const corrida = usar((s) => (s.corridaVisible ? s.corridas[s.corridaVisible] : undefined));
  if (!corrida || !catalogo) return <div className="vacio">Procesa o elige una corrida para ver qué hizo esta etapa.</div>;
  const p = progreso(catalogo, corrida);
  const est = p.nodos[n.id];
  const propios = corrida.eventos.filter((e) => {
    if ((e.tipo === "etapa_inicio" || e.tipo === "etapa_fin") && nodoDeEtapa(catalogo, String(e.etapa))?.etapa_cronometro === n.etapa_cronometro && n.etapa_cronometro) return true;
    if (n.usa_ia && n.id !== "revisor" && (e.tipo === "modelo_fin" || e.tipo === "herramienta") && e.quien !== "revisor") return true;
    if (n.id === "revisor" && e.tipo === "modelo_fin" && e.quien === "revisor") return true;
    if (n.id === "registro_de_personas" && e.tipo === "proveedor") return true;
    if (n.id === "radicacion" && e.tipo === "escritura" && e.sistema === "sgdea") return true;
    return false;
  });
  return (
    <div className="pila">
      <div className="kpis">
        <div><b>{est ? (est.estado === "activo" ? "En curso" : est.estado === "fallo" ? "Falló" : "Recorrida") : "No pasó"}</b><span>en {corrida.caso}</span></div>
        <div><b>{est?.ms ? segundos(est.ms) : "—"}</b><span>tiempo</span></div>
        {n.usa_ia && <div><b>{(p.tokensEntrada + p.tokensSalida).toLocaleString("es-CO")}</b><span>tokens</span></div>}
      </div>
      {propios.filter((e) => e.tipo !== "etapa_inicio").map((e, i) => (
        <div key={i} className={`ev ${e.tipo}`}>
          <b>{e.tipo === "modelo_fin" ? `Vuelta ${String(e.vuelta)} del modelo` : e.tipo === "herramienta" ? String(e.herramienta) : e.tipo === "proveedor" ? "Consulta al registro" : e.tipo === "escritura" ? `SGDEA · ${String(e.operacion)} (simulado)` : "Etapa terminada"}</b>
          <span className="texto-3 chico"> {e.ms !== undefined ? segundos(Number(e.ms)) : ""}</span>
          {e.tipo === "modelo_fin" && e.texto ? <pre>{String(e.texto)}</pre> : null}
          {e.tipo === "herramienta" ? <pre>{JSON.stringify(e.argumentos)}{"\n→ "}{String(e.resultado).slice(0, 600)}</pre> : null}
          {e.tipo === "escritura" && e.payload ? <pre>{JSON.stringify(e.payload, null, 2)}</pre> : null}
          {e.tipo === "etapa_fin" && e.fallo ? <pre>{String(e.fallo)}</pre> : null}
        </div>
      ))}
    </div>
  );
}

function InspectorNodo({ n }: { n: Nodo }) {
  const configuracion = usar((s) => s.configuracion);
  const guardar = usar((s) => s.guardarConfiguracion);
  const avisar = usar((s) => s.avisar);
  const [pestana, setPestana] = useState<"configurar" | "reglas" | "actividad">("configurar");
  const [busqueda, setBusqueda] = useState("");
  const [soloAjustables, setSoloAjustables] = useState(true);
  const [borradores, setBorradores] = useState<Record<string, unknown>>({});
  const [guardando, setGuardando] = useState(false);
  useEffect(() => {
    setBorradores({});
    setBusqueda("");
  }, [n.id]);

  const grupos = useMemo(() => {
    const q = busqueda.trim().toLowerCase();
    const lista = n.parametros.filter((p) => (!soloAjustables || p.ajuste !== null) && (!q || `${p.nombre} ${p.descripcion}`.toLowerCase().includes(q)));
    const g = new Map<Clase, Parametro[]>();
    for (const p of lista) g.set(clase(p), [...(g.get(clase(p)) ?? []), p]);
    return ORDEN_CLASE.filter((c) => g.has(c)).map((c) => [c, g.get(c)!] as const);
  }, [n, busqueda, soloAjustables]);

  const pendientes = Object.keys(borradores).length;
  const ajustables = n.parametros.filter((p) => p.ajuste !== null).length;

  const aplicar = async () => {
    if (!configuracion) return;
    let c: Configuracion = configuracion;
    for (const [clave, v] of Object.entries(borradores)) {
      const p = n.parametros.find((x) => x.clave === clave)!;
      c = conValor(c, p, JSON.stringify(v) === JSON.stringify(p.valor) ? undefined : v);
    }
    setGuardando(true);
    try {
      const cambios = await guardar(c, `${n.nombre}: ${Object.keys(borradores).map((k) => k.split(".").pop()).join(", ")}`);
      setBorradores({});
      avisar(`${cambios} cambio${cambios === 1 ? "" : "s"} aplicado${cambios === 1 ? "" : "s"}: rigen desde el próximo correo`);
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setGuardando(false);
    }
  };

  return (
    <div className="inspector">
      <header className="inspector-cabeza">
        <div className="fila">
          <span className={`icono-nodo ${n.usa_ia ? "ia" : ""}`}><Icono n={n.usa_ia ? "chispa" : n.terminal ? "ok" : "capas"} /></span>
          <div className="crece">
            <h2>{n.nombre}</h2>
            <span className="texto-3 chico mono">{n.modulo}</span>
          </div>
        </div>
        <p className="texto-2">{n.descripcion}</p>
        <nav className="segmentos">
          <button className={pestana === "configurar" ? "activo" : ""} onClick={() => setPestana("configurar")}>Configurar <span>{ajustables}</span></button>
          <button className={pestana === "reglas" ? "activo" : ""} onClick={() => setPestana("reglas")}>Reglas <span>{n.reglas?.length ?? 0}</span></button>
          <button className={pestana === "actividad" ? "activo" : ""} onClick={() => setPestana("actividad")}>Actividad</button>
        </nav>
      </header>
      <div className="inspector-cuerpo">
        {pestana === "configurar" && (
          <>
            <div className="fila pegajosa">
              <div className="campo-icono crece"><Icono n="buscar" tam={14} /><input className="campo" placeholder={`Buscar en ${n.parametros.length} parámetros`} value={busqueda} onChange={(e) => setBusqueda(e.target.value)} /></div>
              <label className="fila chico texto-2"><Interruptor valor={soloAjustables} alCambiar={setSoloAjustables} />Solo editables</label>
            </div>
            {grupos.map(([c, ps]) => (
              <section key={c} className="grupo-param">
                <h3>{ETIQUETA_CLASE[c]} <span>{ps.length}</span></h3>
                {ps.map((p) => (
                  <TarjetaParametro
                    key={p.clave}
                    p={p}
                    borrador={borradores[p.clave]}
                    setBorrador={(v) =>
                      setBorradores((b) => {
                        const sig = { ...b };
                        if (v === undefined) delete sig[p.clave];
                        else sig[p.clave] = v;
                        return sig;
                      })
                    }
                  />
                ))}
              </section>
            ))}
            {!grupos.length && <div className="vacio">{n.parametros.length ? "Nada coincide con la búsqueda." : "Esta etapa no tiene parámetros: su comportamiento lo deciden las etapas vecinas."}</div>}
          </>
        )}
        {pestana === "reglas" && (
          <div className="pila">
            {(n.desenlaces ?? []).length > 0 && (
              <section className="grupo-param">
                <h3>Puede terminar el correo en</h3>
                {n.desenlaces!.map((d, i) => (
                  <div key={i} className="subtarjeta"><span className={`pastilla ${TONO_DESENLACE[d.accion] ?? ""}`}>{NOMBRES_DESENLACE[d.accion] ?? d.accion}</span><p className="chico texto-2">{d.cuando}</p></div>
                ))}
              </section>
            )}
            <section className="grupo-param">
              <h3>Reglas de la etapa</h3>
              {(n.reglas ?? []).map((r) => (
                <div key={r.id} className="subtarjeta"><code className="fuerte">{r.id}</code><p className="chico texto-2">{r.descripcion}</p>{r.funcion && <span className="texto-3 chico mono">{r.modulo} · {r.funcion}</span>}</div>
              ))}
            </section>
          </div>
        )}
        {pestana === "actividad" && <ActividadDelNodo n={n} />}
      </div>
      {pendientes > 0 && (
        <footer className="inspector-pie">
          <span><b>{pendientes}</b> cambio{pendientes > 1 ? "s" : ""} sin aplicar</span>
          <span className="espacio" />
          <button className="boton" onClick={() => setBorradores({})}>Descartar</button>
          <button className="boton primario" disabled={guardando} onClick={() => void aplicar()}><Icono n="rayo" tam={14} />Aplicar en vivo</button>
        </footer>
      )}
    </div>
  );
}

// ----------------------------------------------------------- inspector correo

function InspectorCorreo({ caso }: { caso: string }) {
  const procesar = usar((s) => s.procesar);
  const seleccion = usar((s) => s.seleccion);
  const alternar = usar((s) => s.alternarSeleccion);
  const avisar = usar((s) => s.avisar);
  const [c, setC] = useState<Correo | null>(null);
  useEffect(() => {
    setC(null);
    void pedir<Correo>(`/api/bandeja/${encodeURIComponent(caso)}`).then(setC).catch(() => undefined);
  }, [caso]);
  if (!c) return <div className="vacio">Cargando correo…</div>;
  return (
    <div className="inspector">
      <header className="inspector-cabeza">
        <div className="fila"><span className="icono-nodo"><Icono n="correo" /></span><div className="crece"><h2>{c.asunto || "(sin asunto)"}</h2><span className="texto-3 chico">{c.remitente} · {c.remitenteCorreo}</span></div></div>
        <div className="fila">
          <span className="pastilla">{c.caso}</span>
          {c.desenlaceProduccion && <span className={`pastilla ${TONO_DESENLACE[c.desenlaceProduccion] ?? ""}`}>Producción: {NOMBRES_DESENLACE[c.desenlaceProduccion] ?? c.desenlaceProduccion}{c.motivoProduccion ? ` · ${c.motivoProduccion}` : ""}</span>}
        </div>
        <div className="fila">
          <button className="boton primario" onClick={() => void procesar([c.caso]).then(() => avisar(`${c.caso} en proceso`)).catch((e: Error) => avisar(e.message, "error"))}><Icono n="play" tam={14} />Procesar este correo</button>
          <button className="boton" onClick={() => alternar(c.caso)}>{seleccion.includes(c.caso) ? "Quitar de la selección" : "Agregar a la selección"}</button>
        </div>
      </header>
      <div className="inspector-cuerpo">
        <section className="grupo-param">
          <h3>Adjuntos <span>{c.adjuntos.length}</span></h3>
          {c.adjuntos.map((a) => (
            <a key={a.archivo || a.nombre} className="adjunto" href={a.archivo ? `/api/bandeja/${encodeURIComponent(c.caso)}/adjuntos/${encodeURIComponent(a.archivo)}` : undefined} target="_blank" rel="noreferrer">
              <Icono n="adjunto" tam={14} /><span className="crece">{a.nombre}</span><span className="texto-3 chico">{Math.max(1, Math.round(a.tamano / 1024))} KB{a.enLinea ? " · en línea" : ""}</span>
            </a>
          ))}
        </section>
        <section className="grupo-param">
          <h3>Cuerpo</h3>
          <div className="cuerpo-correo">{c.cuerpo}</div>
        </section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------- inspector corrida

function InspectorCorrida({ id }: { id: string }) {
  const catalogo = usar((s) => s.catalogo);
  const corrida = usar((s) => s.corridas[id]);
  const procesar = usar((s) => s.procesar);
  if (!corrida) return null;
  const r = corrida.resultado;
  const p = progreso(catalogo, corrida);
  const accion = r ? String(r.accion) : "";
  const hallazgos = (r?.hallazgos ?? {}) as Record<string, { valor: unknown; metodo: string; confianza: number; evidencia: string }>;
  const radicacion = corrida.eventos.find((e) => e.tipo === "escritura" && e.operacion === "radicar");
  return (
    <div className="inspector">
      <header className="inspector-cabeza">
        <div className="fila"><span className="icono-nodo"><Icono n="corridas" /></span><div className="crece"><h2>Corrida de {corrida.caso}</h2><span className="texto-3 chico mono">{corrida.id}</span></div><button className="boton chico" onClick={() => void procesar([corrida.caso])}>Repetir</button></div>
        <div className="kpis">
          <div className={TONO_DESENLACE[accion] ?? ""}><b>{r ? (NOMBRES_DESENLACE[accion] ?? accion) : corrida.estado === "en_cola" ? "En cola" : "Procesando"}</b><span>{r?.motivo ? String(r.motivo) : "desenlace"}</span></div>
          <div><b>{p.vueltas}</b><span>vueltas del agente</span></div>
          <div><b>{(p.tokensEntrada + p.tokensSalida).toLocaleString("es-CO")}</b><span>tokens</span></div>
          <div><b>{r?.segundos !== undefined ? `${String(r.segundos).replace(".", ",")} s` : "—"}</b><span>tiempo</span></div>
        </div>
      </header>
      <div className="inspector-cuerpo">
        {r?.explicacion ? <section className="grupo-param"><h3>Lo que explicó el agente</h3><p className="texto-2">{String(r.explicacion)}</p></section> : null}
        <section className="grupo-param">
          <h3>Datos encontrados <span>{Object.keys(hallazgos).length}</span></h3>
          {Object.entries(hallazgos).map(([campo, h]) => (
            <div key={campo} className="dato-fila" title={h.evidencia}>
              <span className="texto-2 chico">{campo}</span>
              <span className="mono">{texto(h.valor)}</span>
              <span className="fila"><span className={`ficha ${/agente|modelo|ia/.test(h.metodo) ? "clase-prompt" : ""}`}>{h.metodo}</span><span className="barra-conf"><i style={{ width: `${Math.round(Number(h.confianza) * 100)}%` }} /></span></span>
            </div>
          ))}
          {(r?.sin_resolver as string[] | undefined)?.length ? <p className="chico texto-2">Sin resolver: {(r!.sin_resolver as string[]).join(", ")}</p> : null}
        </section>
        <section className="grupo-param">
          <h3>Así se radicaría en SGDEA</h3>
          {radicacion ? <Codigo valor={JSON.stringify(radicacion.payload, null, 2)} lenguaje="json" soloLectura /> : <p className="texto-3 chico">Este correo no llegó a enviarse.</p>}
        </section>
        {(r?.avisos as string[] | undefined)?.length ? <section className="grupo-param"><h3>Avisos</h3>{(r!.avisos as string[]).map((a, i) => <p key={i} className="chico texto-2">• {a}</p>)}</section> : null}
        {r?.traza ? <section className="grupo-param"><h3>Traza del error</h3><Codigo valor={String(r.traza)} soloLectura /></section> : null}
      </div>
    </div>
  );
}

export function Inspector() {
  const foco = usar((s) => s.foco);
  const catalogo = usar((s) => s.catalogo);
  const enfocar = usar((s) => s.enfocar);
  let contenido: React.ReactNode = (
    <div className="vacio grande">
      <Icono n="capas" tam={28} />
      <p>Haz clic en un nodo del flujo para configurarlo, en un correo para verlo o en una corrida para revisar su resultado.</p>
    </div>
  );
  if (foco?.tipo === "nodo") {
    const n = catalogo?.nodos.find((x) => x.id === foco.id);
    if (n) contenido = <InspectorNodo n={n} />;
  } else if (foco?.tipo === "correo") contenido = <InspectorCorreo caso={foco.caso} />;
  else if (foco?.tipo === "corrida") contenido = <InspectorCorrida id={foco.id} />;
  return (
    <aside className="panel-inspector">
      {foco && <button className="cerrar-flotante" aria-label="Cerrar" onClick={() => enfocar(null)}><Icono n="cerrar" tam={14} /></button>}
      {contenido}
    </aside>
  );
}
