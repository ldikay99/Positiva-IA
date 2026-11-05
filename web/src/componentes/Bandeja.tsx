import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { NOMBRES_DESENLACE, TONO_DESENLACE } from "../progreso.ts";
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
          <span className="ficha mono">{c.caso}</span>
          {adjuntos > 0 && <span className="ficha"><Icono n="adjunto" tam={11} />{adjuntos}</span>}
          {c.desenlaceProduccion && <span className={`ficha tono-${TONO_DESENLACE[c.desenlaceProduccion] ?? ""}`} title={`En producción: ${c.motivoProduccion || c.desenlaceProduccion}`}>Prod · {NOMBRES_DESENLACE[c.desenlaceProduccion] ?? c.desenlaceProduccion}</span>}
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
              title="Ver esta corrida"
            >
              {accion === "procesando" ? <span className="girando" /> : null}
              Aquí · {NOMBRES_DESENLACE[accion] ?? (accion === "en_cola" ? "en cola" : "procesando")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
});

export function Bandeja() {
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
      if ((filtro === "radicado" || filtro === "manual") && c.desenlaceProduccion !== filtro) return false;
      if (!["todos", "sin_procesar", "procesados", "radicado", "manual"].includes(filtro) && c.motivoProduccion !== filtro) return false;
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
      await procesar(seleccion);
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
  ];

  return (
    <section className="bandeja">
      <header className="bandeja-cabeza">
        <div className="fila">
          <span className="mini-icono t-pizarra"><Icono n="bandeja" tam={13} /></span>
          <h2 className="crece">Bandeja de entrada</h2>
          <span className="texto-3 diminuto">{visibles.length} de {bandeja.length}</span>
        </div>
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
            {enCola > 0 && <button className="boton chico peligro" onClick={() => void cancelarCola()}>Cancelar {enCola} en cola</button>}
            <button className="boton primario" disabled={!seleccion.length || enviando} onClick={() => void lanzar()}>
              <Icono n="play" tam={14} />Procesar {seleccion.length || ""}
            </button>
          </motion.footer>
        )}
      </AnimatePresence>
    </section>
  );
}
