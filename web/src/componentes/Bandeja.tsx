import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { NOMBRES_CORTOS_DESENLACE, NOMBRES_DESENLACE, TONO_DESENLACE } from "../progreso.ts";
import type { Correo, Corrida } from "../tipos.ts";

const ALTO_FILA = 98;

function fecha(iso: string): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("es-CO", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function ultimas(corridas: Record<string, Corrida>, orden: string[]): Record<string, Corrida> {
  const r: Record<string, Corrida> = {};
  for (const id of [...orden].reverse()) {
    const c = corridas[id];
    if (c) r[c.caso] = c;
  }
  return r;
}

type Filtro = "todos" | "sin_procesar" | "procesados" | "radicado" | "manual" | string;

const FilaCorreo = memo(function FilaCorreo({ c, u, marcado, abierto, top }: { c: Correo; u?: Corrida; marcado: boolean; abierto: boolean; top: number }) {
  const alternar = usar((s) => s.alternarSeleccion);
  const enfocar = usar((s) => s.enfocar);
  const verCorrida = usar((s) => s.verCorrida);
  const accion = u?.resultado ? String(u.resultado.accion) : u ? (u.estado === "en_cola" ? "en_cola" : "procesando") : "";
  const adjuntos = c.adjuntos.filter((a) => !a.enLinea).length;
  return (
    <div className={`correo ${marcado ? "marcado" : ""} ${abierto ? "abierto" : ""}`} style={{ transform: `translateY(${top}px)` }} onClick={() => enfocar({ tipo: "correo", caso: c.caso })}>
      <label className="correo-check" onClick={(e) => e.stopPropagation()}>
        <input type="checkbox" className="check" checked={marcado} onChange={() => alternar(c.caso)} aria-label={`Seleccionar ${c.caso}`} />
      </label>
      <div className="correo-cuerpo-lista">
        <div className="fila">
          <span className="correo-de crece">{c.remitente || c.remitenteCorreo}</span>
          <span className="texto-3 diminuto">{fecha(c.recibido)}</span>
        </div>
        <div className="correo-asunto">{c.asunto || "(sin asunto)"}</div>
        <div className="correo-previa">{c.vistaPrevia}</div>
        <div className="fila correo-fichas">
          {c.origen === "buzon" ? <span className="ficha tono-azul" title="Traído del buzón real (solo lectura), con sus etiquetas de hoy">Buzón real</span> : <span className="ficha mono">{c.caso}</span>}
          {adjuntos > 0 && <span className="ficha"><Icono n="adjunto" tam={11} />{adjuntos}</span>}
          {c.desenlaceProduccion && <span className={`ficha tono-${TONO_DESENLACE[c.desenlaceProduccion] ?? ""}`} title={`En producción: ${NOMBRES_DESENLACE[c.desenlaceProduccion] ?? c.desenlaceProduccion}${c.motivoProduccion ? ` · ${c.motivoProduccion}` : ""}`}>Prod · {NOMBRES_CORTOS_DESENLACE[c.desenlaceProduccion] ?? c.desenlaceProduccion}</span>}
          {accion && (
            <button
              className={`ficha boton-ficha solida tono-${TONO_DESENLACE[accion] ?? "azul"}`}
              onClick={(e) => {
                e.stopPropagation();
                if (u) {
                  verCorrida(u.id);
                  enfocar({ tipo: "corrida", id: u.id });
                }
              }}
              title={`Ver esta corrida${NOMBRES_DESENLACE[accion] ? ` · ${NOMBRES_DESENLACE[accion]}` : ""}`}
            >
              {accion === "procesando" ? <span className="girando" /> : null}
              Aquí · {NOMBRES_CORTOS_DESENLACE[accion] ?? (accion === "en_cola" ? "en cola" : "procesando")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
});

/**
 * La carpeta que lee el job (AUTOMATIZACION), en el orden en que la tomaría
 * ahora: un correo por hilo, del más viejo al más nuevo. Correrlos aquí es
 * correr el job: un solo orquestador, de a cinco y de a uno por remitente, con
 * las consultas al buzón de verdad. Nada se escribe: lo que el job marcaría,
 * movería o reenviaría aparece en el flujo como bloqueado.
 */
function PanelBuzon() {
  const buzon = usar((s) => s.buzon);
  const cargando = usar((s) => s.cargandoBuzon);
  const cargar = usar((s) => s.cargarBuzon);
  const correr = usar((s) => s.correrComoElJob);
  const avisar = usar((s) => s.avisar);
  const enfocar = usar((s) => s.enfocar);
  const [n, setN] = useState(10);
  const [marcados, setMarcados] = useState<string[]>([]);
  const [corriendo, setCorriendo] = useState(false);

  useEffect(() => {
    void cargar(n);
  }, [n, cargar]);

  const correos = buzon?.correos ?? [];
  const cuantos = marcados.length || correos.length;
  const lanzar = async () => {
    setCorriendo(true);
    try {
      await correr(n, marcados);
      avisar(`${cuantos} correo${cuantos === 1 ? "" : "s"} del buzón real corriendo como el job`);
      setMarcados([]);
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setCorriendo(false);
    }
  };

  return (
    <>
      <div className="buzon-aviso">
        <Icono n="candado" tam={13} />
        <span>
          <b>Solo lectura.</b> Lee {buzon?.carpeta || "la carpeta del job"} y consulta el buzón como el job (hilo, asunto, radicados, documentos), pero no marca, no mueve, no reenvía y no radica: lo que el job haría aparece en el flujo como bloqueado.
        </span>
      </div>
      <div className="fila buzon-controles">
        <label className="fila chico texto-2 crece">
          Los próximos
          <select className="chip chip-select" value={n} onChange={(e) => setN(Number(e.target.value))} aria-label="Cuántos correos">
            {[5, 10, 20, 50].map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
          que tomaría el job{typeof buzon?.total === "number" ? ` · ${buzon.total} en la carpeta` : ""}
        </label>
        <button className="boton chico" disabled={cargando} onClick={() => void cargar(n, true)}>{cargando ? <span className="girando" /> : <Icono n="reordenar" tam={13} />}Actualizar</button>
      </div>
      <div className="bandeja-lista buzon-lista">
        {buzon?.error && <div className="vacio">No se pudo leer el buzón: {buzon.error}</div>}
        {!buzon && cargando && <div className="vacio"><span className="girando" /> Leyendo el buzón…</div>}
        {correos.map((c, i) => (
          <div key={c.id} className={`correo estatico ${marcados.includes(c.id) ? "marcado" : ""}`} onClick={() => c.traido && enfocar({ tipo: "correo", caso: c.caso })} title={c.traido ? "Ver el correo" : "Se trae al correrlo"}>
            <label className="correo-check" onClick={(e) => e.stopPropagation()}>
              <input type="checkbox" className="check" checked={marcados.includes(c.id)} onChange={() => setMarcados((m) => (m.includes(c.id) ? m.filter((x) => x !== c.id) : [...m, c.id]))} aria-label={`Elegir ${c.asunto}`} />
            </label>
            <div className="correo-cuerpo-lista">
              <div className="fila">
                <span className="texto-3 diminuto mono">{i + 1}</span>
                <span className="correo-de crece">{c.remitente || c.remitenteCorreo}</span>
                <span className="texto-3 diminuto">{fecha(c.recibido)}</span>
              </div>
              <div className="correo-asunto">{c.asunto || "(sin asunto)"}</div>
              <div className="fila correo-fichas">
                {c.conAdjuntos && <span className="ficha"><Icono n="adjunto" tam={11} /></span>}
                {c.categorias.map((k) => <span key={k} className="ficha" title="Etiqueta que tiene hoy en el buzón">{k}</span>)}
                {c.traido && <span className="ficha tono-azul">Traído</span>}
              </div>
            </div>
          </div>
        ))}
        {buzon && !buzon.error && !correos.length && <div className="vacio">La carpeta del job está vacía.</div>}
      </div>
      <footer className="bandeja-pie">
        <span className="crece chico texto-2">{marcados.length ? <><b>{marcados.length}</b> elegidos</> : "Todos los de la lista, en este orden"}</span>
        <button className="boton primario" disabled={corriendo || !correos.length} onClick={() => void lanzar()}>
          {corriendo ? <span className="girando" /> : <Icono n="play" tam={14} />}Correr {cuantos || ""} como el job
        </button>
      </footer>
    </>
  );
}

export function Bandeja() {
  const [fuente, setFuente] = useState<"buzon" | "local">("buzon");
  const [comoElJob, setComoElJob] = useState(true);
  const bandeja = usar((s) => s.bandeja);
  const corridas = usar((s) => s.corridas);
  const orden = usar((s) => s.orden);
  const seleccion = usar((s) => s.seleccion);
  const fijar = usar((s) => s.fijarSeleccion);
  const procesar = usar((s) => s.procesar);
  const foco = usar((s) => s.foco);
  const avisar = usar((s) => s.avisar);
  const motor = usar((s) => s.motor);
  const cancelarCola = usar((s) => s.cancelarCola);
  const [busqueda, setBusqueda] = useState("");
  const [filtro, setFiltro] = useState<Filtro>("todos");
  const [enviando, setEnviando] = useState(false);
  const lista = useRef<HTMLDivElement>(null);
  const [scroll, setScroll] = useState(0);
  const [alto, setAlto] = useState(600);

  const porCaso = useMemo(() => ultimas(corridas, orden), [corridas, orden]);
  const motivos = useMemo(() => [...new Set(bandeja.map((c) => c.motivoProduccion).filter(Boolean))].sort(), [bandeja]);
  const marcados = useMemo(() => new Set(seleccion), [seleccion]);

  const visibles = useMemo(() => {
    const q = busqueda.trim().toLowerCase();
    return bandeja.filter((c) => {
      if (filtro === "sin_procesar" && porCaso[c.caso]) return false;
      if (filtro === "procesados" && !porCaso[c.caso]) return false;
      if (filtro === "del_buzon") return c.origen === "buzon" && (!q || `${c.asunto} ${c.remitente} ${c.remitenteCorreo} ${c.vistaPrevia}`.toLowerCase().includes(q));
      if ((filtro === "radicado" || filtro === "manual") && c.desenlaceProduccion !== filtro) return false;
      if (!["todos", "sin_procesar", "procesados", "radicado", "manual", "del_buzon"].includes(filtro) && c.motivoProduccion !== filtro) return false;
      return !q || `${c.caso} ${c.asunto} ${c.remitente} ${c.remitenteCorreo} ${c.vistaPrevia}`.toLowerCase().includes(q);
    });
  }, [bandeja, busqueda, filtro, porCaso]);

  useLayoutEffect(() => {
    const el = lista.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setAlto(el.clientHeight));
    ro.observe(el);
    setAlto(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    lista.current?.scrollTo({ top: 0 });
    setScroll(0);
  }, [busqueda, filtro]);

  const desde = Math.max(0, Math.floor(scroll / ALTO_FILA) - 4);
  const hasta = Math.min(visibles.length, Math.ceil((scroll + alto) / ALTO_FILA) + 4);
  const todosMarcados = visibles.length > 0 && visibles.every((c) => marcados.has(c.caso));
  const enCola = motor?.enCola.length ?? 0;
  const procesados = bandeja.filter((c) => porCaso[c.caso]).length;

  const lanzar = async () => {
    if (!seleccion.length) return;
    setEnviando(true);
    try {
      const n = seleccion.length;
      await procesar(seleccion, comoElJob);
      avisar(`${n} correo${n > 1 ? "s" : ""} en cola de proceso`);
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setEnviando(false);
    }
  };

  const chips: { id: Filtro; nombre: string; n?: number }[] = [
    { id: "todos", nombre: "Todos", n: bandeja.length },
    { id: "sin_procesar", nombre: "Sin procesar", n: bandeja.length - procesados },
    { id: "procesados", nombre: "Procesados", n: procesados },
    ...(bandeja.some((c) => c.origen === "buzon") ? [{ id: "del_buzon", nombre: "Del buzón real", n: bandeja.filter((c) => c.origen === "buzon").length }] : []),
  ];

  return (
    <section className="bandeja">
      <header className="bandeja-cabeza">
        <div className="fila">
          <span className="mini-icono t-pizarra"><Icono n="bandeja" tam={13} /></span>
          <h2 className="crece">Bandeja de entrada</h2>
          {fuente === "local" && <span className="texto-3 diminuto">{visibles.length} de {bandeja.length}</span>}
        </div>
        <div className="segmentos" role="tablist" aria-label="De dónde salen los correos">
          <button role="tab" aria-selected={fuente === "buzon"} className={`segmento ${fuente === "buzon" ? "activo" : ""}`} onClick={() => setFuente("buzon")}><Icono n="correo" tam={12} />Buzón real</button>
          <button role="tab" aria-selected={fuente === "local"} className={`segmento ${fuente === "local" ? "activo" : ""}`} onClick={() => setFuente("local")}><Icono n="bandeja" tam={12} />Descargados</button>
        </div>
      </header>
      {fuente === "buzon" ? <PanelBuzon /> : <>
      <header className="bandeja-cabeza bandeja-cabeza-local">
        <div className="campo-icono"><Icono n="buscar" tam={14} /><input className="campo" placeholder="Buscar asunto, remitente o texto" value={busqueda} onChange={(e) => setBusqueda(e.target.value)} /></div>
        <div className="chips-filtro">
          {chips.map((c) => (
            <button key={c.id} className={`chip ${filtro === c.id ? "activo" : ""}`} onClick={() => setFiltro(c.id)}>{c.nombre}<span>{c.n}</span></button>
          ))}
          <select className={`chip chip-select ${["radicado", "manual"].includes(filtro) || motivos.includes(filtro) ? "activo" : ""}`} value={["radicado", "manual"].includes(filtro) || motivos.includes(filtro) ? filtro : ""} onChange={(e) => setFiltro(e.target.value || "todos")} aria-label="Filtrar por producción">
            <option value="">Producción…</option>
            <option value="radicado">Radicados en producción</option>
            <option value="manual">A manual en producción</option>
            <optgroup label="Manual en producción por">
              {motivos.map((m) => <option key={m} value={m}>{m}</option>)}
            </optgroup>
          </select>
        </div>
        <label className="fila chico texto-2 seleccionar-todos">
          <input type="checkbox" className="check" checked={todosMarcados} onChange={() => fijar(todosMarcados ? seleccion.filter((c) => !visibles.some((v) => v.caso === c)) : [...new Set([...seleccion, ...visibles.map((v) => v.caso)])])} />
          <span className="crece">Seleccionar los {visibles.length} visibles</span>
          {seleccion.length > 0 && <button className="enlace" onClick={(e) => { e.preventDefault(); fijar([]); }}>Limpiar</button>}
        </label>
      </header>

      <div className="bandeja-lista" ref={lista} onScroll={(e) => setScroll(e.currentTarget.scrollTop)}>
        <div className="bandeja-alto" style={{ height: visibles.length * ALTO_FILA }}>
          {visibles.slice(desde, hasta).map((c, i) => (
            <FilaCorreo key={c.caso} c={c} u={porCaso[c.caso]} marcado={marcados.has(c.caso)} abierto={foco?.tipo === "correo" && foco.caso === c.caso} top={(desde + i) * ALTO_FILA} />
          ))}
        </div>
        {!visibles.length && <div className="vacio">No hay correos con ese filtro.</div>}
      </div>

      <AnimatePresence>
        {(seleccion.length > 0 || enCola > 0) && (
          <motion.footer className="bandeja-pie" initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 40, opacity: 0 }} transition={{ type: "spring", stiffness: 420, damping: 34 }}>
            <span className="crece"><b>{seleccion.length}</b> seleccionado{seleccion.length === 1 ? "" : "s"}</span>
            {seleccion.length > 1 && (
              <label className="fila chico texto-2" title="Un solo orquestador para todos, de a cinco y de a uno por remitente: así detecta lo que se repite dentro de la corrida">
                <input type="checkbox" className="check" checked={comoElJob} onChange={(e) => setComoElJob(e.target.checked)} />Juntos, como el job
              </label>
            )}
            {enCola > 0 && <button className="boton chico peligro" onClick={() => void cancelarCola()}>Cancelar {enCola} en cola</button>}
            <button className="boton primario" disabled={!seleccion.length || enviando} onClick={() => void lanzar()}>
              <Icono n="play" tam={14} />Procesar {seleccion.length || ""}
            </button>
          </motion.footer>
        )}
      </AnimatePresence>
      </>}
    </section>
  );
}
