import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "motion/react";
import { pedir, usar } from "../estado.ts";
import { narrar, solicitud } from "../eventos.ts";
import { ACCIONES, ANCLA_DEL_PUNTO, CAMPOS, CAMPOS_DE_ENTRADA, CATEGORIAS, OPERADORES, PUNTOS, alternativas, categoria, fraseDeRegla, nombreCampo, type Categoria } from "../flujo.ts";
import { Icono, type NombreIcono } from "../iconos.tsx";
import { ETIQUETA_CLASE, ORDEN_CLASE, ajustado, clase, conValor, esRegex, resumen, vigente, type Clase } from "../parametros.ts";
import { NOMBRES_DESENLACE, TONO_DESENLACE, progreso, segundos } from "../progreso.ts";
import type { Configuracion, Correo, Nodo, Parametro, Regla } from "../tipos.ts";
import { BloqueTramo, TablaSolicitud } from "./Dock.tsx";
import { Codigo, Deslizador, EditorJson, EditorLista, EditorMapa, EditorRegex, Interruptor } from "./Editores.tsx";

function texto(v: unknown): string {
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

// ------------------------------------------------------------------ armazón

function Cabeza({ icono, tono, etiqueta, titulo, subtitulo, children }: { icono: NombreIcono; tono: string; etiqueta: string; titulo: string; subtitulo?: string; children?: React.ReactNode }) {
  const enfocar = usar((s) => s.enfocar);
  return (
    <header className="inspector-cabeza">
      <div className="fila arriba">
        <span className={`icono-grande t-${tono}`}><Icono n={icono} tam={18} /></span>
        <div className="crece">
          <span className="etiqueta-cat">{etiqueta}</span>
          <h2>{titulo}</h2>
          {subtitulo && <span className="texto-3 diminuto mono">{subtitulo}</span>}
        </div>
        <button className="boton-mini" aria-label="Cerrar" title="Cerrar (Esc)" onClick={() => enfocar(null)}><Icono n="cerrar" tam={15} /></button>
      </div>
      {children}
    </header>
  );
}

function Pestanas<T extends string>({ id, valor, opciones, cambiar }: { id: string; valor: T; opciones: { id: T; nombre: string; n?: number }[]; cambiar: (v: T) => void }) {
  return (
    <nav className="pestanas" role="tablist">
      {opciones.map((o) => (
        <button key={o.id} role="tab" aria-selected={valor === o.id} className={valor === o.id ? "activa" : ""} onClick={() => cambiar(o.id)}>
          {o.nombre}
          {o.n !== undefined && <span className="contador">{o.n}</span>}
          {valor === o.id && <motion.i layoutId={`pestana-${id}`} className="pestana-linea" />}
        </button>
      ))}
    </nav>
  );
}

// ------------------------------------------------------------------ editores

function EditorPrompt({ valor, alCambiar }: { valor: unknown; alCambiar: (v: unknown) => void }) {
  const esMapa = valor !== null && typeof valor === "object" && !Array.isArray(valor);
  const claves = esMapa ? Object.keys(valor as Record<string, unknown>) : [];
  const [clave, setClave] = useState(claves[0] ?? "");
  if (!esMapa) return <Codigo valor={String(valor ?? "")} alCambiar={alCambiar} lenguaje="markdown" minAlto="240px" />;
  const mapa = valor as Record<string, unknown>;
  return (
    <div className="pila">
      <div className="fichas">
        {claves.map((k) => (
          <button key={k} className={`ficha ${k === clave ? "activa" : ""}`} onClick={() => setClave(k)}>{nombreCampo(k)}</button>
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
          <div className="fila"><span className="mini-icono t-violeta"><Icono n="herramientas" tam={12} /></span><code className="fuerte">{h.name}</code></div>
          <span className="texto-3 diminuto">Lo que el modelo lee de esta herramienta</span>
          <textarea className="campo" rows={3} value={h.description ?? ""} onChange={(e) => cambiar(i, (x) => ({ ...x, description: e.target.value }))} />
          {Object.entries(h.parameters?.properties ?? {}).map(([p, d]) => (
            <label key={p} className="mapa-fila">
              <code className="mapa-clave">{p}</code>
              <input className="campo" value={d.description ?? ""} onChange={(e) => cambiar(i, (x) => { x.parameters!.properties![p]!.description = e.target.value; return x; })} />
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

/** Lo que busca una regla, en palabras: las frases alternativas del patrón. */
function QueBusca({ valor }: { valor: unknown }) {
  const r = esRegex(valor) ? valor.regex : typeof valor === "string" ? valor : "";
  const alt = useMemo(() => alternativas(r), [r]);
  if (!r || alt.length < 2) return null;
  return (
    <div className="que-busca">
      <span className="texto-3 diminuto">Busca cualquiera de estas {alt.length} formas:</span>
      <div className="fichas">
        {alt.slice(0, 24).map((a, i) => <span key={i} className="ficha suave">{a}</span>)}
        {alt.length > 24 && <span className="ficha">+{alt.length - 24}</span>}
      </div>
    </div>
  );
}

interface PropsTarjeta {
  p: Parametro;
  borrador: unknown;
  setBorrador: (v: unknown | undefined) => void;
  abierta: boolean;
  alternar: () => void;
  ir?: boolean;
}

function TarjetaParametro({ p, borrador, setBorrador, abierta, alternar, ir = false }: PropsTarjeta) {
  const configuracion = usar((s) => s.configuracion);
  const actual = vigente(p, configuracion);
  const editado = borrador !== undefined;
  const esAjustado = ajustado(p, configuracion);
  const c = clase(p);
  const valor = editado ? borrador : actual;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ir) return;
    const t = setTimeout(() => ref.current?.scrollIntoView({ block: "start", behavior: "smooth" }), 260);
    return () => clearTimeout(t);
  }, [ir]);
  return (
    <div ref={ref} className={`param ${esAjustado ? "ajustado" : ""} ${editado ? "editado" : ""} ${abierta ? "abierto" : ""}`}>
      <button className="param-cabeza" onClick={alternar} aria-expanded={abierta}>
        <span className="param-nombre"><code>{p.nombre}</code></span>
        <span className="param-chips">
          {editado && <span className="pastilla azul diminuto">sin aplicar</span>}
          {!editado && esAjustado && <span className="pastilla ambar diminuto">ajustado</span>}
          {p.ajuste === null && <span className="pastilla diminuto" title={p.motivo_fijo}>solo lectura</span>}
          <Icono n={abierta ? "abajo" : "derecha"} tam={12} className="texto-3" />
        </span>
        <span className="param-desc">{p.descripcion}</span>
        {!abierta && <span className="param-resumen mono">{resumen(valor, 90)}</span>}
      </button>
      {abierta && (
        <motion.div className="param-cuerpo" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} transition={{ duration: 0.18 }}>
          {c === "regex" && <QueBusca valor={valor} />}
          {p.ajuste === null ? (
            <>
              <Codigo valor={texto(p.valor)} soloLectura lenguaje={typeof p.valor === "string" ? "texto" : "json"} />
              <p className="texto-3 chico"><Icono n="candado" tam={12} /> {p.motivo_fijo ?? p.nota_sobrescritura}</p>
            </>
          ) : (
            <>
              <Editor p={p} valor={valor} alCambiar={(v) => setBorrador(JSON.stringify(v) === JSON.stringify(actual) ? undefined : v)} />
              <div className="fila">
                {editado && <button className="boton chico" onClick={() => setBorrador(undefined)}>Descartar edición</button>}
                {esAjustado && !editado && <button className="boton chico" onClick={() => setBorrador(p.valor)}><Icono n="deshacer" tam={13} />Volver al valor del código</button>}
                <span className="espacio" />
                <span className="texto-3 diminuto mono">{p.modulo}</span>
              </div>
            </>
          )}
        </motion.div>
      )}
    </div>
  );
}

// ------------------------------------------------------------- inspector nodo

type PestanaNodo = "resumen" | "configurar" | "reglas" | "busquedas" | "actividad";

function Conexiones({ n }: { n: Nodo }) {
  const catalogo = usar((s) => s.catalogo)!;
  const centrar = usar((s) => s.centrar);
  const nombre = (id: string) => catalogo.nodos.find((x) => x.id === id);
  const entran = catalogo.aristas.filter((a) => a.hacia === n.id);
  const salen = catalogo.aristas.filter((a) => a.desde === n.id);
  const lista = (aristas: typeof entran, campo: "desde" | "hacia") => (
    <div className="conexiones">
      {aristas.map((a, i) => {
        const otro = nombre(a[campo]);
        if (!otro) return null;
        const c = CATEGORIAS[categoria(otro)];
        return (
          <button key={i} className="conexion" onClick={() => centrar(otro.id)}>
            <span className={`mini-icono t-${c.tono}`}><Icono n={c.icono} tam={12} /></span>
            <span className="crece"><b>{otro.nombre}</b>{a.etiqueta && <span className="texto-3 diminuto">{a.etiqueta}</span>}</span>
            <Icono n="flecha" tam={13} className="texto-3" />
          </button>
        );
      })}
    </div>
  );
  return (
    <>
      {entran.length > 0 && <section className="seccion"><h3>Llega desde</h3>{lista(entran, "desde")}</section>}
      {salen.length > 0 && <section className="seccion"><h3>Sigue hacia</h3>{lista(salen, "hacia")}</section>}
    </>
  );
}

function Busquedas({ n, abrir }: { n: Nodo; abrir: (clave: string) => void }) {
  const corrida = usar((s) => (s.corridaVisible ? s.corridas[s.corridaVisible] : undefined));
  const configuracion = usar((s) => s.configuracion);
  const campos = useMemo(() => solicitud(corrida), [corrida]);
  const porClave = useMemo(() => new Map(n.parametros.map((p) => [p.clave, p])), [n]);
  const eventos = useMemo(() => (corrida?.eventos ?? []).filter((e) => e.tipo === "busqueda"), [corrida]);
  const [todas, setTodas] = useState<Set<string>>(new Set());
  return (
    <div className="pila">
      <p className="texto-2 chico">Cada campo de la solicitud lo busca una función con sus propias reglas. Abre una regla para ver qué frases reconoce, editarla y probarla contra el correo abierto.</p>
      {(n.busquedas ?? []).map((b) => {
        const ev = eventos.filter((e) => b.campos.includes(String(e.campo)) || (b.funcion === "extraer_geografia" && e.campo === "geografia"));
        const hallado = ev.find((e) => e.encontrado);
        const peso = (p: Parametro) => (clase(p) === "regex" ? 0 : clase(p) === "umbral" ? 1 : p.ajuste !== null ? 2 : 3);
        const usa = b.usa
          .map((k) => porClave.get(k))
          .filter((p): p is Parametro => Boolean(p))
          .sort((x, y) => peso(x) - peso(y));
        const ajustes = usa.filter((p) => ajustado(p, configuracion)).length;
        return (
          <section key={b.funcion} className="busqueda">
            <header className="fila">
              <span className="mini-icono t-azul"><Icono n="buscar" tam={12} /></span>
              <b className="crece">{b.nombre}</b>
              {ajustes > 0 && <span className="pastilla ambar diminuto">{ajustes} ajustada{ajustes > 1 ? "s" : ""}</span>}
              {corrida && (ev.length === 0 ? <span className="pastilla diminuto">sin correr</span> : hallado ? <span className={`pastilla diminuto ${hallado.aceptado === false ? "ambar" : "verde"}`}>{Math.round(Number(hallado.confianza) * 100)} %</span> : <span className="pastilla diminuto rojo">no encontrado</span>)}
            </header>
            <p className="texto-2 chico">{b.descripcion}</p>
            {corrida && ev.length > 0 && (
              <div className={`resultado-busqueda ${hallado ? "si" : "no"}`}>
                {hallado ? (
                  <>
                    <span className="mono fuerte">{String(hallado.valor)}</span>
                    <span className="texto-3 diminuto">{String(hallado.metodo ?? "")} · {String(hallado.evidencia ?? "")}</span>
                  </>
                ) : (
                  <span className="texto-2 diminuto">{String(ev[0]!.motivo ?? "")}: {String(ev[0]!.detalle ?? "")}</span>
                )}
              </div>
            )}
            {usa.length > 0 ? (
              <div className="fichas">
                <span className="texto-3 diminuto">Cómo lo busca:</span>
                {usa.slice(0, todas.has(b.funcion) ? usa.length : 6).map((p) => (
                  <button key={p.clave} className={`ficha boton-ficha ${clase(p) === "regex" ? "clase-regex" : clase(p) === "umbral" ? "clase-umbral" : ""}`} onClick={() => abrir(p.clave)} title={p.descripcion}>
                    {clase(p) === "regex" && <Icono n="buscar" tam={10} />}
                    {p.nombre.replace(/^_+/, "").replace(/_/g, " ").toLowerCase()}
                  </button>
                ))}
                {usa.length > 6 && (
                  <button className="enlace" onClick={() => setTodas((t) => { const x = new Set(t); if (x.has(b.funcion)) x.delete(b.funcion); else x.add(b.funcion); return x; })}>
                    {todas.has(b.funcion) ? "ver menos" : `+${usa.length - 6} más`}
                  </button>
                )}
              </div>
            ) : (
              <span className="texto-3 diminuto">Se resuelve con datos del correo o con una tabla, sin reglas de texto.</span>
            )}
            {campos.filter((c) => b.campos.includes(c.campo)).length > 0 && <TablaSolicitud campos={campos.filter((c) => b.campos.includes(c.campo))} compacta />}
          </section>
        );
      })}
    </div>
  );
}

function InspectorNodo({ n }: { n: Nodo }) {
  const catalogo = usar((s) => s.catalogo)!;
  const configuracion = usar((s) => s.configuracion);
  const corrida = usar((s) => (s.corridaVisible ? s.corridas[s.corridaVisible] : undefined));
  const guardar = usar((s) => s.guardarConfiguracion);
  const avisar = usar((s) => s.avisar);
  const crearRegla = usar((s) => s.crearRegla);
  const centrar = usar((s) => s.centrar);
  const enfocar = usar((s) => s.enfocar);
  const parametroAbierto = usar((s) => s.parametroAbierto);
  const borradoresRegla = usar((s) => s.borradores);
  const [pestana, setPestana] = useState<PestanaNodo>("resumen");
  const [busqueda, setBusqueda] = useState("");
  const [soloEditables, setSoloEditables] = useState(true);
  const [borradores, setBorradores] = useState<Record<string, unknown>>({});
  const [abiertos, setAbiertos] = useState<Set<string>>(new Set());
  const [guardando, setGuardando] = useState(false);
  const [irA, setIrA] = useState<string | null>(null);

  useEffect(() => {
    setBorradores({});
    setBusqueda("");
    setAbiertos(new Set());
    setPestana(n.busquedas?.length ? "busquedas" : "resumen");
  }, [n.id, n.busquedas]);

  // Otro panel pidió mostrar un parámetro de este nodo.
  useEffect(() => {
    if (!parametroAbierto || !n.parametros.some((p) => p.clave === parametroAbierto)) return;
    setPestana("configurar");
    setBusqueda("");
    setSoloEditables(false);
    setAbiertos(new Set([parametroAbierto]));
    setIrA(parametroAbierto);
    usar.setState({ parametroAbierto: null });
  }, [parametroAbierto, n]);

  const abrirParametro = (clave: string) => {
    setPestana("configurar");
    setBusqueda("");
    setSoloEditables(false);
    setAbiertos(new Set([clave]));
    setIrA(clave);
  };

  const grupos = useMemo(() => {
    const q = busqueda.trim().toLowerCase();
    const lista = n.parametros.filter((p) => (!soloEditables || p.ajuste !== null || abiertos.has(p.clave)) && (!q || `${p.nombre} ${p.descripcion}`.toLowerCase().includes(q)));
    const g = new Map<Clase, Parametro[]>();
    for (const p of lista) g.set(clase(p), [...(g.get(clase(p)) ?? []), p]);
    return ORDEN_CLASE.filter((c) => g.has(c)).map((c) => [c, g.get(c)!] as const);
  }, [n, busqueda, soloEditables, abiertos]);

  const p = useMemo(() => progreso(catalogo, corrida), [catalogo, corrida]);
  const tramos = useMemo(() => narrar(catalogo, corrida).filter((t) => t.nodo === n.id), [catalogo, corrida, n.id]);
  const est = p.nodos[n.id];
  const pendientes = Object.keys(borradores).length;
  const editables = n.parametros.filter((x) => x.ajuste !== null).length;
  const ajustes = n.parametros.filter((x) => ajustado(x, configuracion)).length;
  const cat: Categoria = categoria(n);
  const c = CATEGORIAS[cat];
  const ancla = (Object.entries(ANCLA_DEL_PUNTO).find(([, id]) => id === n.id)?.[0] ?? null) as Regla["punto"] | null;
  const propias = [...(configuracion?.reglas ?? []), ...borradoresRegla].filter((r) => ancla && r.punto === ancla);
  const porClave = new Map(n.parametros.map((x) => [x.clave, x]));

  const aplicar = async () => {
    if (!configuracion) return;
    let conf: Configuracion = configuracion;
    for (const [clave, v] of Object.entries(borradores)) {
      const prm = n.parametros.find((x) => x.clave === clave)!;
      conf = conValor(conf, prm, JSON.stringify(v) === JSON.stringify(prm.valor) ? undefined : v);
    }
    setGuardando(true);
    try {
      const cambios = await guardar(conf, `${n.nombre}: ${Object.keys(borradores).map((k) => k.split(".").pop()).join(", ")}`);
      setBorradores({});
      avisar(`${cambios} cambio${cambios === 1 ? "" : "s"} en vivo: rigen desde el próximo correo`);
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setGuardando(false);
    }
  };

  const pestanas: { id: PestanaNodo; nombre: string; n?: number }[] = [
    { id: "resumen", nombre: "Resumen" },
    ...(n.busquedas?.length ? [{ id: "busquedas" as const, nombre: "Qué busca", n: n.busquedas.length }] : []),
    { id: "configurar", nombre: "Configurar", n: editables },
    { id: "reglas", nombre: "Reglas", n: (n.reglas?.length ?? 0) + propias.length },
    { id: "actividad", nombre: "Actividad", n: tramos.reduce((a, t) => a + t.pasos.length, 0) || undefined },
  ];

  return (
    <div className="inspector">
      <Cabeza icono={c.icono} tono={c.tono} etiqueta={c.nombre} titulo={n.nombre} subtitulo={n.modulo}>
        <div className="fila envuelve">
          {est ? <span className={`pastilla ${est.estado === "activo" ? "azul" : est.estado === "fallo" ? "rojo" : "verde"}`}>{est.estado === "activo" ? <span className="girando" /> : <Icono n={est.estado === "fallo" ? "cerrar" : "ok"} tam={12} />}{est.estado === "activo" ? "En curso" : est.estado === "fallo" ? "Falló" : "Recorrida"}{est.ms ? ` · ${segundos(est.ms)}` : ""}</span> : corrida ? <span className="pastilla">No pasó por aquí</span> : null}
          {ajustes > 0 && <span className="pastilla ambar">{ajustes} ajuste{ajustes > 1 ? "s" : ""} activo{ajustes > 1 ? "s" : ""}</span>}
          <span className="espacio" />
          <button className="boton chico" onClick={() => centrar(n.id)}><Icono n="mira" tam={13} />Enfocar</button>
        </div>
        <Pestanas id="nodo" valor={pestana} opciones={pestanas} cambiar={setPestana} />
      </Cabeza>

      <div className="inspector-cuerpo">
        {pestana === "resumen" && (
          <>
            <p className="texto-2">{n.descripcion}</p>
            <div className="kpis">
              <div><b>{n.reglas?.length ?? 0}</b><span>reglas</span></div>
              <div><b>{editables}</b><span>parámetros editables</span></div>
              <div><b>{n.funciones?.length ?? 0}</b><span>funciones del motor</span></div>
            </div>
            {(n.desenlaces ?? []).length > 0 && (
              <section className="seccion">
                <h3>Puede terminar el correo en</h3>
                {n.desenlaces!.map((d, i) => (
                  <div key={i} className="desenlace-fila"><span className={`pastilla ${TONO_DESENLACE[d.accion] ?? ""}`}>{NOMBRES_DESENLACE[d.accion] ?? d.accion}</span><span className="chico texto-2">{d.cuando}</span></div>
                ))}
              </section>
            )}
            <Conexiones n={n} />
            {(n.funciones ?? []).length > 0 && (
              <section className="seccion">
                <h3>Código que ejecuta</h3>
                <div className="fichas">{n.funciones!.map((f) => <code key={f} className="ficha">{f}</code>)}</div>
              </section>
            )}
          </>
        )}

        {pestana === "busquedas" && <Busquedas n={n} abrir={abrirParametro} />}

        {pestana === "configurar" && (
          <>
            <div className="barra-filtros pegajosa">
              <div className="campo-icono crece"><Icono n="buscar" tam={14} /><input className="campo" placeholder={`Buscar en ${n.parametros.length} parámetros`} value={busqueda} onChange={(e) => setBusqueda(e.target.value)} /></div>
              <label className="fila chico texto-2"><Interruptor valor={soloEditables} alCambiar={setSoloEditables} />Solo editables</label>
            </div>
            {grupos.map(([cl, ps]) => (
              <section key={cl} className="seccion">
                <h3>{ETIQUETA_CLASE[cl]} <span>{ps.length}</span></h3>
                {ps.map((prm) => (
                  <TarjetaParametro
                    key={prm.clave}
                    p={prm}
                    abierta={abiertos.has(prm.clave)}
                    ir={irA === prm.clave}
                    alternar={() => setAbiertos((a) => { const s = new Set(a); if (s.has(prm.clave)) s.delete(prm.clave); else s.add(prm.clave); return s; })}
                    borrador={borradores[prm.clave]}
                    setBorrador={(v) => setBorradores((b) => { const sig = { ...b }; if (v === undefined) delete sig[prm.clave]; else sig[prm.clave] = v; return sig; })}
                  />
                ))}
              </section>
            ))}
            {!grupos.length && <div className="vacio">{n.parametros.length ? "Nada coincide con la búsqueda." : "Esta etapa no tiene parámetros propios: su comportamiento lo deciden las etapas vecinas."}</div>}
          </>
        )}

        {pestana === "reglas" && (
          <>
            {ancla && (
              <section className="seccion">
                <h3>Reglas personalizadas en este punto <span>{propias.length}</span></h3>
                <p className="texto-2 chico">{PUNTOS[ancla].descripcion}</p>
                {propias.map((r) => (
                  <button key={r.id} className="conexion" onClick={() => enfocar({ tipo: "regla", id: r.id })}>
                    <span className="mini-icono t-rosa"><Icono n="regla" tam={12} /></span>
                    <span className="crece"><b>{r.nombre}</b><span className="texto-3 diminuto mono">{fraseDeRegla(r)}</span></span>
                    {!r.activa && <span className="pastilla diminuto">inactiva</span>}
                  </button>
                ))}
                <button className="boton chico" onClick={() => crearRegla(ancla)}><Icono n="mas" tam={13} />Agregar regla aquí</button>
              </section>
            )}
            <section className="seccion">
              <h3>Reglas del motor <span>{n.reglas?.length ?? 0}</span></h3>
              {(n.reglas ?? []).map((r) => {
                const usa = (r.usa ?? []).map((k) => porClave.get(k)).filter((x): x is Parametro => Boolean(x));
                return (
                  <div key={r.id} className="regla-motor">
                    <p>{r.descripcion}</p>
                    {usa.length > 0 && (
                      <div className="fichas">
                        <span className="texto-3 diminuto">Se ajusta con:</span>
                        {usa.map((x) => (
                          <button key={x.clave} className={`ficha boton-ficha ${ajustado(x, configuracion) ? "tono-ambar" : clase(x) === "regex" ? "clase-regex" : clase(x) === "umbral" ? "clase-umbral" : ""}`} onClick={() => abrirParametro(x.clave)} title={x.descripcion}>
                            {x.nombre}
                          </button>
                        ))}
                      </div>
                    )}
                    {r.funcion && <span className="texto-3 diminuto mono">{r.modulo} · {r.funcion}</span>}
                  </div>
                );
              })}
              {!(n.reglas ?? []).length && <div className="vacio chico">Sin reglas propias.</div>}
            </section>
            {!ancla && (
              <div className="nota-suave">
                <Icono n="info" tam={14} />
                <span>Para cambiar lo que pasa con un correo sin tocar el código, agrega una regla personalizada: clic derecho en el lienzo o el botón <b>Agregar</b>.</span>
              </div>
            )}
          </>
        )}

        {pestana === "actividad" && (tramos.length ? tramos.map((t) => <BloqueTramo key={t.id} tr={t} t0={corrida?.eventos[0]?.t ?? 0} />) : <div className="vacio">{corrida ? `En ${corrida.caso} esta etapa no anunció pasos.` : "Procesa o elige una corrida para ver qué hizo esta etapa."}</div>)}
      </div>

      {pendientes > 0 && (
        <motion.footer className="inspector-pie" initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }}>
          <span className="crece"><b>{pendientes}</b> cambio{pendientes > 1 ? "s" : ""} sin aplicar</span>
          <button className="boton" onClick={() => setBorradores({})}>Descartar</button>
          <button className="boton primario" disabled={guardando} onClick={() => void aplicar()}><Icono n="rayo" tam={14} />Aplicar en vivo</button>
        </motion.footer>
      )}
    </div>
  );
}

// ------------------------------------------------------------ inspector regla

function InspectorRegla({ id }: { id: string }) {
  const configuracion = usar((s) => s.configuracion);
  const borradores = usar((s) => s.borradores);
  const cambiarBorrador = usar((s) => s.cambiarBorrador);
  const guardarRegla = usar((s) => s.guardarRegla);
  const borrarRegla = usar((s) => s.borrarRegla);
  const duplicarRegla = usar((s) => s.duplicarRegla);
  const centrar = usar((s) => s.centrar);
  const avisar = usar((s) => s.avisar);
  const corrida = usar((s) => (s.corridaVisible ? s.corridas[s.corridaVisible] : undefined));
  const guardada = configuracion?.reglas.find((r) => r.id === id);
  const borrador = borradores.find((r) => r.id === id);
  const r = borrador ?? guardada;
  const [guardando, setGuardando] = useState(false);
  if (!r) return <div className="vacio">La regla ya no existe.</div>;
  const cambiar = (c: Partial<Regla>) => cambiarBorrador({ ...r, ...c });
  const conPatron = r.operador === "coincide" || r.operador === "no_coincide";
  const sinGuardar = Boolean(borrador);
  const completa = r.nombre.trim() && (!conPatron || r.patron.trim()) && (r.accion !== "fijar" || r.valor.trim());
  const evaluacion = (corrida?.eventos ?? []).find((e) => e.tipo === "regla" && e.id === r.id);
  const campos = r.punto === "entrada" ? CAMPOS_DE_ENTRADA : CAMPOS;

  const guardar = async () => {
    setGuardando(true);
    try {
      await guardarRegla(r);
      avisar(guardada ? "Regla actualizada: rige desde el próximo correo" : "Regla creada y activa en el motor");
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setGuardando(false);
    }
  };

  return (
    <div className="inspector">
      <Cabeza icono="regla" tono="rosa" etiqueta={`Regla personalizada · ${PUNTOS[r.punto].nombre.toLowerCase()}`} titulo={r.nombre || "Sin nombre"} subtitulo={r.id}>
        <div className="fila envuelve">
          {sinGuardar ? <span className="pastilla ambar">{guardada ? "Cambios sin guardar" : "Borrador: aún no corre"}</span> : r.activa ? <span className="pastilla verde"><span className="punto" />Activa en el motor</span> : <span className="pastilla">Inactiva</span>}
          {evaluacion && <span className={`pastilla ${evaluacion.cumple ? "rosa" : ""}`}>{evaluacion.cumple ? "Se cumplió" : "No aplicó"} en {corrida?.caso}</span>}
          <span className="espacio" />
          <button className="boton chico" onClick={() => centrar(r.id)}><Icono n="mira" tam={13} />Enfocar</button>
        </div>
      </Cabeza>
      <div className="inspector-cuerpo">
        <div className="frase-regla mono">{fraseDeRegla(r)}</div>
        <label className="etiqueta">Nombre<input className="campo" value={r.nombre} onChange={(e) => cambiar({ nombre: e.target.value })} /></label>
        <label className="etiqueta">Para qué sirve<textarea className="campo" rows={2} value={r.descripcion} placeholder="Opcional: explica por qué existe esta regla" onChange={(e) => cambiar({ descripcion: e.target.value })} /></label>

        <section className="seccion">
          <h3>Cuándo se evalúa</h3>
          <div className="opciones-grandes">
            {(Object.keys(PUNTOS) as Regla["punto"][]).map((pt) => (
              <button key={pt} className={`opcion-grande ${r.punto === pt ? "activa" : ""}`} onClick={() => cambiar({ punto: pt, campo: pt === "entrada" ? "asunto" : "telefono_afectado", accion: pt === "entrada" && r.accion === "fijar" ? "manual" : r.accion })}>
                <b>{PUNTOS[pt].nombre}</b>
                <span>{PUNTOS[pt].descripcion}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="seccion">
          <h3>Condición</h3>
          <div className="rejilla-form">
            <label className="etiqueta">Si<select className="campo" value={r.campo} onChange={(e) => cambiar({ campo: e.target.value })}>{Object.entries(campos).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            <label className="etiqueta">…<select className="campo" value={r.operador} onChange={(e) => cambiar({ operador: e.target.value as Regla["operador"] })}>{Object.entries(OPERADORES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          </div>
          {conPatron && (
            <>
              <EditorRegex valor={{ regex: r.patron, flags: r.ignorarMayusculas ? "IGNORECASE" : "" }} alCambiar={(v) => cambiar({ patron: v.regex })} />
              <label className="fila chico texto-2"><Interruptor valor={r.ignorarMayusculas} alCambiar={(v) => cambiar({ ignorarMayusculas: v })} />Ignorar mayúsculas y minúsculas</label>
              {r.punto === "validacion" && <p className="texto-3 diminuto">El patrón se compara con el valor que quedó en el campo, no con el texto del correo.</p>}
            </>
          )}
        </section>

        <section className="seccion">
          <h3>Entonces</h3>
          <div className="opciones-grandes tres">
            {(Object.keys(ACCIONES) as Regla["accion"][]).filter((a) => a !== "fijar" || r.punto === "validacion").map((a) => (
              <button key={a} className={`opcion-grande ${r.accion === a ? "activa" : ""}`} onClick={() => cambiar({ accion: a })}>
                <Icono n={a === "manual" ? "bandera" : a === "fijar" ? "editar" : "alerta"} tam={15} />
                <b>{ACCIONES[a]}</b>
              </button>
            ))}
          </div>
          {r.accion === "manual" && <label className="etiqueta">Motivo de revisión manual<input className="campo mono" value={r.motivo} onChange={(e) => cambiar({ motivo: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_").slice(0, 48) })} /></label>}
          {r.accion === "fijar" && <label className="etiqueta">Valor que se fija en {nombreCampo(r.campo).toLowerCase()}<input className="campo mono" value={r.valor} onChange={(e) => cambiar({ valor: e.target.value })} /></label>}
        </section>

        <label className="fila chico texto-2"><Interruptor valor={r.activa} alCambiar={(v) => cambiar({ activa: v })} />Activa: el motor la evalúa en cada correo</label>
        <div className="fila envuelve">
          <button className="boton chico" onClick={() => duplicarRegla(r.id)}><Icono n="copiar" tam={13} />Duplicar</button>
          <button className="boton chico peligro" onClick={() => void borrarRegla(r.id).then(() => avisar("Regla eliminada"))}><Icono n="papelera" tam={13} />Eliminar</button>
        </div>
      </div>
      {sinGuardar && (
        <motion.footer className="inspector-pie" initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }}>
          <span className="crece chico">{completa ? "Rige desde el próximo correo que se procese" : conPatron && !r.patron.trim() ? "Falta el patrón" : r.accion === "fijar" && !r.valor.trim() ? "Falta el valor" : "Falta el nombre"}</span>
          {guardada && <button className="boton" onClick={() => usar.setState((s) => ({ borradores: s.borradores.filter((b) => b.id !== r.id) }))}>Descartar</button>}
          <button className="boton primario" disabled={!completa || guardando} onClick={() => void guardar()}><Icono n="rayo" tam={14} />{guardada ? "Guardar en vivo" : "Crear y activar"}</button>
        </motion.footer>
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
  if (!c) return <div className="vacio"><span className="girando" /> Cargando correo…</div>;
  return (
    <div className="inspector">
      <Cabeza icono="correo" tono="pizarra" etiqueta={`Correo ${c.caso}`} titulo={c.asunto || "(sin asunto)"} subtitulo={`${c.remitente} · ${c.remitenteCorreo}`}>
        <div className="fila envuelve">
          {c.desenlaceProduccion && <span className={`pastilla ${TONO_DESENLACE[c.desenlaceProduccion] ?? ""}`}>Producción: {NOMBRES_DESENLACE[c.desenlaceProduccion] ?? c.desenlaceProduccion}{c.motivoProduccion ? ` · ${c.motivoProduccion}` : ""}</span>}
        </div>
        <div className="fila">
          <button className="boton primario" onClick={() => void procesar([c.caso]).then(() => avisar(`${c.caso} en proceso`)).catch((e: Error) => avisar(e.message, "error"))}><Icono n="play" tam={14} />Procesar este correo</button>
          <button className="boton" onClick={() => alternar(c.caso)}>{seleccion.includes(c.caso) ? "Quitar de la selección" : "Agregar a la selección"}</button>
        </div>
      </Cabeza>
      <div className="inspector-cuerpo">
        <section className="seccion">
          <h3>Adjuntos <span>{c.adjuntos.length}</span></h3>
          {c.adjuntos.map((a) => (
            <a key={a.archivo || a.nombre} className="adjunto" href={a.archivo ? `/api/bandeja/${encodeURIComponent(c.caso)}/adjuntos/${encodeURIComponent(a.archivo)}` : undefined} target="_blank" rel="noreferrer">
              <span className="mini-icono t-turquesa"><Icono n="documento" tam={12} /></span><span className="crece">{a.nombre}</span><span className="texto-3 diminuto">{Math.max(1, Math.round(a.tamano / 1024))} KB{a.enLinea ? " · en línea" : ""}</span>
            </a>
          ))}
          {!c.adjuntos.length && <span className="texto-3 chico">Sin adjuntos.</span>}
        </section>
        <section className="seccion">
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
  const avisar = usar((s) => s.avisar);
  const campos = useMemo(() => solicitud(corrida), [corrida]);
  if (!corrida) return null;
  const r = corrida.resultado;
  const p = progreso(catalogo, corrida);
  const accion = r ? String(r.accion) : "";
  const radicacion = corrida.eventos.find((e) => e.tipo === "escritura" && e.operacion === "radicar");
  return (
    <div className="inspector">
      <Cabeza icono="corridas" tono={r ? (accion === "radicado" ? "verde" : "ambar") : "azul"} etiqueta="Resultado de la corrida" titulo={`${corrida.caso} · ${r ? (NOMBRES_DESENLACE[accion] ?? accion) : corrida.estado === "en_cola" ? "En cola" : "Procesando"}`} subtitulo={corrida.id}>
        <div className="kpis">
          <div className={TONO_DESENLACE[accion] ?? ""}><b>{r ? (accion === "radicado" ? String(r.radicado ?? "Radicado") : NOMBRES_DESENLACE[accion] ?? accion) : "…"}</b><span>{r?.motivo ? String(r.motivo) : "desenlace"}</span></div>
          <div><b>{p.vueltas}</b><span>vueltas de la IA</span></div>
          <div><b>{(p.tokensEntrada + p.tokensSalida).toLocaleString("es-CO")}</b><span>tokens</span></div>
          <div><b>{r?.segundos !== undefined ? `${String(r.segundos).replace(".", ",")} s` : "—"}</b><span>tiempo</span></div>
        </div>
        <div className="fila"><button className="boton chico" onClick={() => void procesar([corrida.caso]).then(() => avisar("Corrida repetida"))}><Icono n="play" tam={13} />Repetir</button></div>
      </Cabeza>
      <div className="inspector-cuerpo">
        {r?.explicacion ? <section className="seccion"><h3>Lo que explicó la IA</h3><p className="texto-2">{String(r.explicacion)}</p></section> : null}
        <section className="seccion">
          <h3>La solicitud <span>{campos.length} campos</span></h3>
          <TablaSolicitud campos={campos} />
        </section>
        <section className="seccion">
          <h3>Así se radicaría en SGDEA</h3>
          {radicacion ? (
            typeof radicacion.payload === "string" ? (
              <Codigo valor={radicacion.payload.replace(/^\w+\(/, "").replace(/\)$/, "").replace(/, (?=\w+=)/g, "\n")} soloLectura />
            ) : (
              <Codigo valor={JSON.stringify(radicacion.payload, null, 2)} lenguaje="json" soloLectura />
            )
          ) : (
            <p className="texto-3 chico">Este correo no llegó a enviarse.</p>
          )}
        </section>
        {(r?.avisos as string[] | undefined)?.length ? <section className="seccion"><h3>Avisos <span>{(r!.avisos as string[]).length}</span></h3>{(r!.avisos as string[]).map((a, i) => <p key={i} className="aviso-linea chico">{a}</p>)}</section> : null}
        {r?.traza ? <section className="seccion"><h3>Traza del error</h3><Codigo valor={String(r.traza)} soloLectura /></section> : null}
      </div>
    </div>
  );
}

export function Inspector() {
  const foco = usar((s) => s.foco);
  const catalogo = usar((s) => s.catalogo);
  if (foco?.tipo === "nodo") {
    const n = catalogo?.nodos.find((x) => x.id === foco.id);
    return n ? <InspectorNodo n={n} /> : null;
  }
  if (foco?.tipo === "regla") return <InspectorRegla id={foco.id} />;
  if (foco?.tipo === "correo") return <InspectorCorreo caso={foco.caso} />;
  if (foco?.tipo === "corrida") return <InspectorCorrida id={foco.id} />;
  return null;
}
