import { useMemo, useState } from "react";
import { usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { NOMBRES_DESENLACE, TONO_DESENLACE } from "../progreso.ts";
import type { Corrida } from "../tipos.ts";

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

type Filtro = "todos" | "sin_procesar" | "radicado" | "manual" | string;

export function Bandeja() {
  const bandeja = usar((s) => s.bandeja);
  const corridas = usar((s) => s.corridas);
  const orden = usar((s) => s.orden);
  const seleccion = usar((s) => s.seleccion);
  const alternar = usar((s) => s.alternarSeleccion);
  const fijar = usar((s) => s.fijarSeleccion);
  const procesar = usar((s) => s.procesar);
  const enfocar = usar((s) => s.enfocar);
  const foco = usar((s) => s.foco);
  const verCorrida = usar((s) => s.verCorrida);
  const avisar = usar((s) => s.avisar);
  const motor = usar((s) => s.motor);
  const cancelarCola = usar((s) => s.cancelarCola);
  const [busqueda, setBusqueda] = useState("");
  const [filtro, setFiltro] = useState<Filtro>("todos");
  const [enviando, setEnviando] = useState(false);

  const porCaso = useMemo(() => ultimas(corridas, orden), [corridas, orden]);
  const motivos = useMemo(() => [...new Set(bandeja.map((c) => c.motivoProduccion).filter(Boolean))].sort(), [bandeja]);

  const visibles = useMemo(() => {
    const q = busqueda.trim().toLowerCase();
    return bandeja.filter((c) => {
      if (filtro === "sin_procesar" && porCaso[c.caso]) return false;
      if ((filtro === "radicado" || filtro === "manual") && c.desenlaceProduccion !== filtro) return false;
      if (!["todos", "sin_procesar", "radicado", "manual"].includes(filtro) && c.motivoProduccion !== filtro) return false;
      return !q || `${c.caso} ${c.asunto} ${c.remitente} ${c.remitenteCorreo} ${c.vistaPrevia}`.toLowerCase().includes(q);
    });
  }, [bandeja, busqueda, filtro, porCaso]);

  const todosMarcados = visibles.length > 0 && visibles.every((c) => seleccion.includes(c.caso));
  const enCola = motor?.enCola.length ?? 0;

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

  return (
    <section className="bandeja">
      <header className="bandeja-cabeza">
        <div className="fila">
          <h2 className="crece">Bandeja</h2>
          <span className="pastilla">{visibles.length} / {bandeja.length}</span>
        </div>
        <div className="campo-icono"><Icono n="buscar" tam={14} /><input className="campo" placeholder="Buscar asunto, remitente o texto" value={busqueda} onChange={(e) => setBusqueda(e.target.value)} /></div>
        <select className="campo" value={filtro} onChange={(e) => setFiltro(e.target.value)}>
          <option value="todos">Todos los correos</option>
          <option value="sin_procesar">Sin procesar aquí</option>
          <option value="radicado">Producción los radicó</option>
          <option value="manual">Producción los mandó a manual</option>
          <optgroup label="Manual en producción por">
            {motivos.map((m) => <option key={m} value={m}>{m}</option>)}
          </optgroup>
        </select>
        <div className="fila">
          <label className="fila chico texto-2 crece">
            <input type="checkbox" className="check" checked={todosMarcados} onChange={() => fijar(todosMarcados ? seleccion.filter((c) => !visibles.some((v) => v.caso === c)) : [...new Set([...seleccion, ...visibles.map((v) => v.caso)])])} />
            Seleccionar los {visibles.length} visibles
          </label>
          {seleccion.length > 0 && <button className="boton chico" onClick={() => fijar([])}>Limpiar</button>}
        </div>
      </header>

      <div className="bandeja-lista">
        {visibles.map((c) => {
          const u = porCaso[c.caso];
          const accion = u?.resultado ? String(u.resultado.accion) : u ? (u.estado === "en_cola" ? "en_cola" : "procesando") : "";
          const marcado = seleccion.includes(c.caso);
          const abierto = foco?.tipo === "correo" && foco.caso === c.caso;
          return (
            <div key={c.caso} className={`correo ${marcado ? "marcado" : ""} ${abierto ? "abierto" : ""}`} onClick={() => enfocar({ tipo: "correo", caso: c.caso })}>
              <input type="checkbox" className="check" checked={marcado} onClick={(e) => e.stopPropagation()} onChange={() => alternar(c.caso)} aria-label={`Seleccionar ${c.caso}`} />
              <div className="correo-cuerpo-lista">
                <div className="fila">
                  <span className="correo-de crece">{c.remitente || c.remitenteCorreo}</span>
                  <span className="texto-3 diminuto">{fecha(c.recibido)}</span>
                </div>
                <div className="correo-asunto">{c.asunto || "(sin asunto)"}</div>
                <div className="correo-previa">{c.vistaPrevia}</div>
                <div className="fila envuelve">
                  <span className="ficha">{c.caso}</span>
                  {c.adjuntos.some((a) => !a.enLinea) && <span className="ficha"><Icono n="adjunto" tam={11} />{c.adjuntos.filter((a) => !a.enLinea).length}</span>}
                  {c.desenlaceProduccion && <span className={`ficha tono-${TONO_DESENLACE[c.desenlaceProduccion] ?? ""}`} title={c.motivoProduccion}>Prod: {c.desenlaceProduccion}</span>}
                  {accion && (
                    <button
                      className={`ficha boton-ficha tono-${TONO_DESENLACE[accion] ?? "azul"}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (u) {
                          void verCorrida(u.id);
                          enfocar({ tipo: "corrida", id: u.id });
                        }
                      }}
                      title="Ver esta corrida"
                    >
                      {accion === "procesando" ? <span className="girando" /> : null}
                      Aquí: {NOMBRES_DESENLACE[accion] ?? (accion === "en_cola" ? "en cola" : "procesando")}
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
        {!visibles.length && <div className="vacio">No hay correos con ese filtro.</div>}
      </div>

      <footer className={`bandeja-pie ${seleccion.length ? "visible" : ""}`}>
        <span className="crece"><b>{seleccion.length}</b> seleccionado{seleccion.length === 1 ? "" : "s"}</span>
        {enCola > 0 && <button className="boton chico peligro" onClick={() => void cancelarCola()}>Cancelar {enCola} en cola</button>}
        <button className="boton primario" disabled={!seleccion.length || enviando} onClick={() => void lanzar()}>
          <Icono n="play" tam={14} />Procesar {seleccion.length || ""}
        </button>
      </footer>
    </section>
  );
}
