import { useCallback, useEffect, useMemo, useState } from "react";
import { usar } from "../estado.ts";
import { useSubruta } from "../rutas.ts";
import { Icono } from "../iconos.tsx";
import { usarEditor } from "../editor/almacen.ts";
import { Selector } from "./Selector.tsx";
import {
  atajoDeshacer, compararVersiones, deshacerGuardado, etiquetarVersion, fechaCorta, listarVersiones, nombreAutor, rehacerGuardado, restaurarVersion,
  type Diferencias, type ItemVersion,
} from "../editor/versiones.ts";
import "./versiones.css";

/**
 * El historial de versiones del flujo abierto (declarativo o de código): cada versión con su autor (una persona o
 * Vera), fecha, etiqueta, punto «antes de» y resumen; la comparación de dos, paso por paso; y «Volver a esta
 * versión», que crea una versión nueva sin borrar ninguna. URL: /flujos/<flujo>/versiones[/<n>|/<a>-<b>].
 */

/** Lo abierto en la URL: «7» (la 7 contra la anterior) o «3-7» (la 3 contra la 7). */
function leerSub(sub: string | null): { a: number | null; b: number | null } {
  const m = /^(\d+)(?:-(\d+))?$/.exec(sub ?? "");
  if (!m) return { a: null, b: null };
  return m[2] !== undefined ? { a: Number(m[1]), b: Number(m[2]) } : { a: null, b: Number(m[1]) };
}

const etiquetaVersion = (v: ItemVersion) => (v.del_codigo ? `v${v.version} · la del código` : `v${v.version}`);

function Diferencia({ d }: { d: Diferencias }) {
  if (d.iguales) return <div className="vacio chico">Las dos versiones son iguales en pasos, conexiones y configuración.</div>;
  const { agregados, quitados, cambiados } = d.pasos;
  return (
    <div className="vv-dif">
      <p className="vv-resumen">{d.resumen}</p>
      {d.datos.length > 0 && (
        <section className="vv-bloque">
          <h4>Datos del flujo</h4>
          <table className="vv-tabla"><tbody>
            {d.datos.map((c) => <tr key={c.campo}><th>{c.campo}</th><td className="vv-antes">{c.antes}</td><td className="vv-flecha">→</td><td className="vv-despues">{c.despues}</td></tr>)}
          </tbody></table>
        </section>
      )}
      {(agregados.length > 0 || quitados.length > 0) && (
        <section className="vv-bloque">
          <h4>Pasos</h4>
          <ul className="vv-pasos">
            {agregados.map((p) => <li key={`+${p.id}`} className="vv-mas"><span className="vv-signo">+</span><b>{p.nombre}</b><span className="mono texto-3">{p.id} · {p.tipo}</span></li>)}
            {quitados.map((p) => <li key={`-${p.id}`} className="vv-menos"><span className="vv-signo">−</span><b>{p.nombre}</b><span className="mono texto-3">{p.id} · {p.tipo}</span></li>)}
          </ul>
        </section>
      )}
      {cambiados.map((p) => (
        <section key={p.id} className="vv-bloque">
          <h4>{p.nombre} <span className="mono texto-3">{p.id}</span></h4>
          <table className="vv-tabla"><tbody>
            {p.cambios.map((c) => <tr key={c.campo}><th className="mono">{c.campo}</th><td className="vv-antes">{c.antes}</td><td className="vv-flecha">→</td><td className="vv-despues">{c.despues}</td></tr>)}
          </tbody></table>
        </section>
      ))}
      {(d.conexiones.agregadas.length > 0 || d.conexiones.quitadas.length > 0) && (
        <section className="vv-bloque">
          <h4>Conexiones</h4>
          <ul className="vv-pasos">
            {d.conexiones.agregadas.map((c) => <li key={`+${c}`} className="vv-mas"><span className="vv-signo">+</span><span className="mono">{c}</span></li>)}
            {d.conexiones.quitadas.map((c) => <li key={`-${c}`} className="vv-menos"><span className="vv-signo">−</span><span className="mono">{c}</span></li>)}
          </ul>
        </section>
      )}
      {d.movidos > 0 && <p className="texto-3 chico">{d.movidos} paso{d.movidos === 1 ? "" : "s"} cambiaron de lugar en el lienzo.</p>}
      {d.pasos.iguales > 0 && <p className="texto-3 chico">{d.pasos.iguales} paso{d.pasos.iguales === 1 ? " sigue" : "s siguen"} igual.</p>}
    </div>
  );
}

export function VistaVersiones() {
  const flujo = usar((s) => s.flujo);
  const f = usar((s) => s.flujos.find((x) => x.id === s.flujo));
  const irA = usar((s) => s.irA);
  const avisar = usar((s) => s.avisar);
  const [sub, setSub] = useSubruta();
  const [lista, setLista] = useState<ItemVersion[] | null>(null);
  const [error, setError] = useState("");
  const [dif, setDif] = useState<Diferencias | null>(null);
  const [cargandoDif, setCargandoDif] = useState(false);
  const [confirmar, setConfirmar] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [etiqueta, setEtiqueta] = useState("");

  const cargar = useCallback(async () => {
    try {
      setLista(await listarVersiones(flujo));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [flujo]);

  useEffect(() => {
    setLista(null);
    void cargar();
    const oir = (e: Event) => (e as CustomEvent<{ flujo?: string }>).detail?.flujo === flujo && void cargar();
    window.addEventListener("positiva:flujo-guardado", oir);
    return () => window.removeEventListener("positiva:flujo-guardado", oir);
  }, [flujo, cargar]);

  const actual = lista?.find((v) => v.actual) ?? lista?.[0];
  const elegido = leerSub(sub);
  const b = elegido.b ?? actual?.version ?? null;
  const anteriorA = (n: number) => lista?.filter((v) => v.version < n).sort((x, y) => y.version - x.version)[0]?.version ?? null;
  const a = elegido.a ?? (b !== null ? anteriorA(b) : null);
  const vb = lista?.find((v) => v.version === b) ?? null;

  useEffect(() => {
    setConfirmar(false);
    setEtiqueta(vb?.etiqueta ?? "");
  }, [b, vb?.etiqueta]);

  useEffect(() => {
    if (!lista || a === null || b === null) return setDif(null);
    let vivo = true;
    setCargandoDif(true);
    compararVersiones(flujo, a, b)
      .then((d) => vivo && setDif(d))
      .catch((e: Error) => vivo && (setDif(null), setError(e.message)))
      .finally(() => vivo && setCargandoDif(false));
    return () => { vivo = false; };
  }, [flujo, a, b, lista]);

  /** Tras un cambio: el editor abierto de este flujo se recarga y la URL va a la versión nueva. */
  const trasCambio = useCallback(async (version?: number) => {
    const ed = usarEditor.getState();
    if (ed.flujo === flujo) await ed.recargar();
    await cargar();
    if (version !== undefined) setSub(String(version));
  }, [flujo, cargar, setSub]);

  const puedeRehacer = actual && (actual.por === "deshacer" || actual.por === "rehacer") && typeof actual.deshecha === "number";
  const deshacer = useCallback(async () => { const r = await deshacerGuardado(flujo); if (r) await trasCambio(r.version); }, [flujo, trasCambio]);
  const rehacer = useCallback(async () => { const r = await rehacerGuardado(flujo); if (r) await trasCambio(r.version); }, [flujo, trasCambio]);

  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      const que = atajoDeshacer(e);
      if (!que) return;
      e.preventDefault();
      void (que === "deshacer" ? deshacer() : rehacer());
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [deshacer, rehacer]);

  const opciones = useMemo(() => (lista ?? []).map((v) => ({ valor: v.version, etiqueta: `${etiquetaVersion(v)}${v.actual ? " (en uso)" : ""}`, texto: `v${v.version}`, detalle: v.resumen ?? v.nota })), [lista]);

  const volver = async () => {
    if (!vb) return;
    setOcupado(true);
    try {
      const r = await restaurarVersion(flujo, vb.version);
      avisar(r.sin_cambio ? `El flujo ya estaba en la versión ${vb.version}` : `Volvió a la versión ${vb.version}: queda como versión ${r.version}`);
      await trasCambio(r.version);
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setOcupado(false);
      setConfirmar(false);
    }
  };

  const guardarEtiqueta = async () => {
    if (!vb) return;
    try {
      const r = await etiquetarVersion(flujo, vb.version, etiqueta.trim() || null);
      avisar(r.etiqueta ? `Etiqueta puesta en la versión ${vb.version}` : `Se quitó la etiqueta de la versión ${vb.version}`);
      await cargar();
    } catch (e) {
      avisar((e as Error).message, "error");
    }
  };

  return (
    <div className="vv">
      <header className="vv-cabecera">
        <div className="vv-titulo">
          <h2>Versiones</h2>
          <span className="texto-2 chico">{f?.nombre ?? flujo}{f?.tipo === "codigo" ? " · estructura del flujo de código" : ""}</span>
        </div>
        {actual && <span className="pastilla azul" title="La versión que usan las corridas nuevas">En uso: v{actual.version}</span>}
        <span className="espacio" />
        <button className="boton chico" onClick={() => void deshacer()} disabled={!lista || lista.length < 2} title="Deshacer el último cambio guardado (Ctrl+Z)" aria-label="Deshacer"><Icono n="deshacer" tam={14} />Deshacer</button>
        <button className="boton chico" onClick={() => void rehacer()} disabled={!puedeRehacer} title="Rehacer lo que se deshizo (Ctrl+Y o Ctrl+Shift+Z)" aria-label="Rehacer"><Icono n="rehacer" tam={14} />Rehacer</button>
        <button className="boton chico" onClick={() => irA("flujo")} title="Volver al lienzo del flujo"><Icono n="flujo" tam={14} />Lienzo</button>
      </header>
      {error && <p className="f-error" role="alert">{error}</p>}
      {!lista ? (
        <div className="vacio chico"><span className="girando" />Cargando el historial…</div>
      ) : (
        <div className="vv-cuerpo">
          <ol className="vv-lista" aria-label="Versiones guardadas">
            {lista.map((v) => (
              <li key={v.version}>
                <button className={`vv-item ${v.version === b ? "elegida" : ""}`} aria-current={v.version === b ? "true" : undefined} onClick={() => setSub(String(v.version))}>
                  <span className="vv-linea">
                    <b className="mono">v{v.version}</b>
                    {v.del_codigo && <span className="texto-3 vv-codigo">la del código</span>}
                    {nombreAutor(v.autor) && <span className={`vv-autor ${v.autor ?? ""}`}>{nombreAutor(v.autor)}</span>}
                    <span className="vv-fecha">{fechaCorta(v.fecha)}</span>
                    {v.actual && <span className="pastilla diminuto verde">en uso</span>}
                  </span>
                  <span className="vv-que">{v.resumen ?? v.nota ?? (v.del_codigo ? "La estructura del código, sin editar" : v === lista[lista.length - 1] ? "Primera versión" : "Sin resumen")}</span>
                  {(v.etiqueta || v.punto || v.por || v.corridas.bien + v.corridas.revisar + v.corridas.fallo > 0) && (
                    <span className="vv-marcas">
                      {v.etiqueta && <span className="ficha">{v.etiqueta}</span>}
                      {v.punto && <span className="ficha vv-punto" title="Punto de restauración: así estaba antes de ese pedido">{v.punto[0]!.toUpperCase() + v.punto.slice(1)}</span>}
                      {v.por && typeof v.restaurada_de === "number" && <span className="texto-3">{v.por === "deshacer" ? "deshacer: " : v.por === "rehacer" ? "rehacer: " : ""}volvió a v{v.restaurada_de}</span>}
                      {v.corridas.bien > 0 && <span className="vv-corr bien">{v.corridas.bien} bien</span>}
                      {v.corridas.revisar > 0 && <span className="vv-corr revisar">{v.corridas.revisar} a revisar</span>}
                      {v.corridas.fallo > 0 && <span className="vv-corr fallo">{v.corridas.fallo} con fallo</span>}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ol>
          <section className="vv-detalle" aria-label="Detalle de la versión">
            {!vb ? (
              <div className="vacio chico">Elige una versión de la lista.</div>
            ) : (
              <>
                <div className="vv-detalle-cab">
                  <div>
                    <h3>{etiquetaVersion(vb)}{vb.actual ? " · en uso" : ""}</h3>
                    <p className="texto-2 chico">
                      {nombreAutor(vb.autor) ? `${vb.autor === "vera" ? "La hizo Vera" : "La hizo una persona"}` : "De antes del historial"}
                      {vb.fecha ? ` · ${fechaCorta(vb.fecha)}` : ""}
                      {vb.pedido ? ` · pedido: «${vb.pedido}»` : ""}
                    </p>
                  </div>
                  <span className="espacio" />
                  {!vb.actual && !confirmar && <button className="boton chico primario" onClick={() => setConfirmar(true)}><Icono n="historial" tam={14} />Volver a esta versión</button>}
                </div>
                {confirmar && (
                  <div className="vv-confirmar" role="alertdialog" aria-label={`Volver a la versión ${vb.version}`}>
                    <p className="chico">El flujo queda como la versión {vb.version}, guardado como versión nueva. La de ahora (v{actual?.version}) y todas las demás siguen en el historial.{usarEditor.getState().flujo === flujo && usarEditor.getState().definicion && JSON.stringify(usarEditor.getState().definicion) !== usarEditor.getState().guardada ? " Los cambios sin guardar del editor se pierden." : ""}</p>
                    <div className="fila">
                      <span className="espacio" />
                      <button className="boton chico" onClick={() => setConfirmar(false)}>Cancelar</button>
                      <button className="boton chico primario" disabled={ocupado} onClick={() => void volver()}>{ocupado && <span className="girando" />}Sí, volver a v{vb.version}</button>
                    </div>
                  </div>
                )}
                <div className="vv-etiqueta fila">
                  <input className="campo" value={etiqueta} maxLength={80} placeholder="Etiqueta (por ejemplo, «la que va a producción»)" aria-label="Etiqueta de la versión" onChange={(e) => setEtiqueta(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void guardarEtiqueta()} />
                  <button className="boton chico" disabled={(etiqueta.trim() || "") === (vb.etiqueta ?? "")} onClick={() => void guardarEtiqueta()}>{etiqueta.trim() || !vb.etiqueta ? "Guardar etiqueta" : "Quitar etiqueta"}</button>
                </div>
                <div className="vv-comparar fila">
                  <span className="chico texto-2">Comparar</span>
                  <Selector<number> etiqueta="Versión de antes" tamano="chico" valor={a} opciones={opciones} placeholder="Ninguna" alCambiar={(x) => setSub(`${x}-${b}`)} ancho={190} />
                  <span className="chico texto-2">con</span>
                  <Selector<number> etiqueta="Versión de después" tamano="chico" valor={b} opciones={opciones} alCambiar={(x) => setSub(a !== null ? `${a}-${x}` : String(x))} ancho={190} />
                </div>
                {a === null ? (
                  <div className="vacio chico">Es la primera versión: no hay una anterior con qué compararla.</div>
                ) : cargandoDif && !dif ? (
                  <div className="vacio chico"><span className="girando" />Comparando…</div>
                ) : dif ? (
                  <Diferencia d={dif} />
                ) : null}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
