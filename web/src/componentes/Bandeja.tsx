import { useMemo, useState } from "react";
import { usar } from "../estado.ts";
import { NOMBRES_DESENLACE, TONO_DESENLACE } from "../progreso.ts";
import type { Corrida } from "../tipos.ts";

function fecha(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("es-CO", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function ultimaCorrida(corridas: Record<string, Corrida>, orden: string[], caso: string): Corrida | undefined {
  for (const id of orden) if (corridas[id]?.caso === caso) return corridas[id];
  return undefined;
}

export function Bandeja() {
  const bandeja = usar((s) => s.bandeja);
  const corridas = usar((s) => s.corridas);
  const orden = usar((s) => s.orden);
  const procesar = usar((s) => s.procesar);
  const seleccionar = usar((s) => s.seleccionar);
  const seleccionada = usar((s) => s.seleccionada);
  const avisar = usar((s) => s.avisar);
  const [filtro, setFiltro] = useState("");
  const [grupo, setGrupo] = useState("");
  const [marcados, setMarcados] = useState<Set<string>>(new Set());
  const [cuantos, setCuantos] = useState(5);

  const visibles = useMemo(() => {
    const f = filtro.trim().toLowerCase();
    return bandeja.filter(
      (c) =>
        (!grupo || c.desenlaceProduccion === grupo || c.motivoProduccion === grupo) &&
        (!f || `${c.caso} ${c.asunto} ${c.remitente} ${c.remitenteCorreo} ${c.vistaPrevia}`.toLowerCase().includes(f)),
    );
  }, [bandeja, filtro, grupo]);

  const motivos = useMemo(() => [...new Set(bandeja.map((c) => c.motivoProduccion).filter(Boolean))].sort(), [bandeja]);

  const lanzar = async (casos: string[]) => {
    if (!casos.length) return;
    try {
      await procesar(casos);
      setMarcados(new Set());
      avisar(`${casos.length} correo${casos.length > 1 ? "s" : ""} en proceso`);
    } catch (e) {
      avisar((e as Error).message);
    }
  };

  const alternar = (caso: string) =>
    setMarcados((m) => {
      const n = new Set(m);
      if (n.has(caso)) n.delete(caso);
      else n.add(caso);
      return n;
    });

  return (
    <aside className="columna izq">
      <div className="cabeza">
        <div className="cabeza-fila">
          <h2>Bandeja</h2>
          <span className="pastilla">{visibles.length} de {bandeja.length}</span>
        </div>
        <input className="campo" placeholder="Buscar por asunto, remitente o texto" value={filtro} onChange={(e) => setFiltro(e.target.value)} />
        <select className="campo" value={grupo} onChange={(e) => setGrupo(e.target.value)}>
          <option value="">Todos los correos</option>
          <option value="radicado">Producción los radicó</option>
          <option value="manual">Producción los mandó a manual</option>
          {motivos.map((m) => (
            <option key={m} value={m}>Manual por {m}</option>
          ))}
        </select>
        <div className="fila-botones">
          <button className="boton primario" disabled={!marcados.size} onClick={() => lanzar([...marcados])}>
            Procesar {marcados.size || ""} marcado{marcados.size === 1 ? "" : "s"}
          </button>
          <input className="campo" style={{ width: 64 }} type="number" min={1} max={200} value={cuantos} onChange={(e) => setCuantos(Math.max(1, Number(e.target.value)))} />
          <button className="boton" onClick={() => lanzar(visibles.slice(0, cuantos).map((c) => c.caso))}>
            Procesar los primeros
          </button>
        </div>
      </div>
      <div className="desplaza lista-correo">
        {visibles.map((c) => {
          const u = ultimaCorrida(corridas, orden, c.caso);
          const accion = u?.resultado ? String(u.resultado.accion) : u ? "en curso" : "";
          return (
            <div key={c.caso} className={`correo ${u && u.id === seleccionada ? "activo" : ""}`} onClick={() => (u ? seleccionar(u.id) : lanzar([c.caso]))} title={u ? "Ver la última corrida" : "Procesar este correo"}>
              <input type="checkbox" className="check" checked={marcados.has(c.caso)} onClick={(e) => e.stopPropagation()} onChange={() => alternar(c.caso)} />
              <div style={{ minWidth: 0 }}>
                <div className="cabeza-fila" style={{ gap: 6 }}>
                  <span className="de" style={{ flex: 1 }}>{c.remitente || c.remitenteCorreo}</span>
                  <span className="fecha">{fecha(c.recibido)}</span>
                </div>
                <div className="asunto">{c.asunto || "(sin asunto)"}</div>
                <div className="previa">{c.vistaPrevia}</div>
                <div className="meta">
                  <span className="pastilla">{c.caso}</span>
                  {c.adjuntos.filter((a) => !a.enLinea).length > 0 && <span className="pastilla">{c.adjuntos.filter((a) => !a.enLinea).length} adjuntos</span>}
                  {c.desenlaceProduccion && (
                    <span className="pastilla" title={c.motivoProduccion}>
                      Prod: {c.desenlaceProduccion}
                    </span>
                  )}
                  {accion && (
                    <span className={`pastilla ${TONO_DESENLACE[accion] ?? "azul"}`}>
                      Aquí: {NOMBRES_DESENLACE[accion] ?? accion}
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
        {!visibles.length && <div className="vacio">No hay correos en la bandeja.</div>}
      </div>
    </aside>
  );
}
