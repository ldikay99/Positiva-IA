import { useEffect, useState } from "react";
import { usar } from "../estado.ts";
import { NOMBRES_DESENLACE, TONO_DESENLACE, progreso } from "../progreso.ts";
import type { Configuracion } from "../tipos.ts";

export function Corridas() {
  const catalogo = usar((s) => s.catalogo);
  const corridas = usar((s) => s.corridas);
  const orden = usar((s) => s.orden);
  const seleccionar = usar((s) => s.seleccionar);
  const cancelarCola = usar((s) => s.cancelarCola);
  const lista = orden.map((id) => corridas[id]).filter((c) => c !== undefined);
  const terminadas = lista.filter((c) => c.resultado);
  const cuenta: Record<string, number> = {};
  for (const c of terminadas) cuenta[String(c.resultado?.accion)] = (cuenta[String(c.resultado?.accion)] ?? 0) + 1;
  const tokens = terminadas.reduce((s, c) => s + Number(c.resultado?.tokens_entrada ?? 0) + Number(c.resultado?.tokens_salida ?? 0), 0);
  const segs = terminadas.map((c) => Number(c.resultado?.segundos ?? 0)).filter((x) => x > 0).sort((a, b) => a - b);
  const mediana = segs.length ? segs[Math.floor(segs.length / 2)] : 0;
  const pct = (n: number) => (terminadas.length ? `${Math.round((n / terminadas.length) * 100)} %` : "—");
  const enCola = lista.filter((c) => c.estado === "en_cola").length;

  return (
    <div>
      <div className="tarjetas" style={{ marginBottom: 18 }}>
        <div className="tarjeta"><b>{terminadas.length}</b><span>correos procesados</span></div>
        <div className="tarjeta verde"><b>{pct(cuenta.radicado ?? 0)}</b><span>radicados ({cuenta.radicado ?? 0})</span></div>
        <div className="tarjeta rojo"><b>{pct(cuenta.manual ?? 0)}</b><span>a revisión manual ({cuenta.manual ?? 0})</span></div>
        <div className="tarjeta ambar"><b>{pct((cuenta.duplicado ?? 0) + (cuenta.aplazado ?? 0))}</b><span>duplicados o aplazados</span></div>
        <div className="tarjeta azul"><b>{tokens.toLocaleString("es-CO")}</b><span>tokens de IA</span></div>
        <div className="tarjeta"><b>{String(mediana ?? 0).replace(".", ",")} s</b><span>mediana por correo</span></div>
      </div>
      <div className="cabeza-fila" style={{ marginBottom: 8 }}>
        <h2 style={{ fontSize: 15, flex: 1 }}>Corridas de esta sesión</h2>
        {enCola > 0 && <button className="boton chico peligro" onClick={() => void cancelarCola()}>Cancelar {enCola} en cola</button>}
      </div>
      <table className="tabla">
        <thead><tr><th>Correo</th><th>Estado</th><th>Motivo</th><th>Vueltas IA</th><th>Tokens</th><th>Tiempo</th><th>Comparado con producción</th></tr></thead>
        <tbody>
          {lista.map((c) => {
            const r = c.resultado;
            const a = r ? String(r.accion) : "";
            const p = progreso(catalogo, c);
            const prod = usar.getState().bandeja.find((b) => b.caso === c.caso);
            return (
              <tr key={c.id} style={{ cursor: "pointer" }} onClick={() => seleccionar(c.id)}>
                <td>{c.caso}</td>
                <td>{r ? <span className={`pastilla ${TONO_DESENLACE[a] ?? "azul"}`}>{NOMBRES_DESENLACE[a] ?? a}</span> : <span className="pastilla azul">{c.estado === "en_cola" ? "En cola" : "Procesando"}</span>}</td>
                <td className="chico">{r?.motivo ? String(r.motivo) : ""}</td>
                <td>{p.vueltas || Number(r?.vueltas ?? 0) || ""}</td>
                <td>{r ? (Number(r.tokens_entrada ?? 0) + Number(r.tokens_salida ?? 0)).toLocaleString("es-CO") : ""}</td>
                <td>{r?.segundos !== undefined ? `${String(r.segundos).replace(".", ",")} s` : ""}</td>
                <td className="chico">{prod?.desenlaceProduccion ? `${prod.desenlaceProduccion}${prod.motivoProduccion ? ` · ${prod.motivoProduccion}` : ""}` : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!lista.length && <div className="vacio">Elige correos en la bandeja y procésalos para verlos aquí.</div>}
    </div>
  );
}

export function Proveedores() {
  const catalogo = usar((s) => s.catalogo);
  if (!catalogo) return null;
  return (
    <div>
      <p className="texto-2" style={{ marginTop: 0 }}>
        Los sistemas con los que habla el pipeline. En este visor nada se escribe: el envío a SGDEA y los cambios en el buzón se simulan y se muestran en cada corrida. Vertex y el registro de personas sí se consultan.
      </p>
      <div className="cuadricula-2">
        {catalogo.proveedores.map((p) => (
          <div key={p.id} className="bloque">
            <h4>{p.nombre} <span className="pastilla">{p.tipo}</span></h4>
            {p.modulo && <div className="texto-3 chico">{p.modulo}</div>}
            {p.configuracion?.length ? <div className="chico texto-2" style={{ margin: "6px 0" }}>Configuración: {p.configuracion.join(", ")}</div> : null}
            {p.operaciones?.map((o, i) => (
              <div key={i} className="op">
                <span className="metodo">{o.metodo ?? ""}</span>
                <span><b>{o.nombre}</b>{o.ruta ? <span className="texto-3 mono"> {o.ruta}</span> : null}</span>
                {o.escribe ? <span className="pastilla ambar">escribe · simulado</span> : <span className="pastilla verde">lee</span>}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function Herramientas() {
  const catalogo = usar((s) => s.catalogo);
  if (!catalogo) return null;
  return (
    <div>
      <p className="texto-2" style={{ marginTop: 0 }}>
        El agente recibe solo los campos que la capa de reglas no resolvió y los busca con estas herramientas. Ninguna escribe en SGDEA ni en el correo.
      </p>
      <div className="cuadricula-2">
        {catalogo.herramientas_agente.map((h) => (
          <div key={h.nombre} className="bloque">
            <h4><code>{h.nombre}</code>{h.escribe ? <span className="pastilla ambar">registra</span> : <span className="pastilla verde">lee</span>}</h4>
            <div className="texto-2 chico">{h.descripcion}</div>
            {h.parametros ? <details style={{ marginTop: 6 }}><summary>Parámetros</summary><pre className="correo-cuerpo mono">{JSON.stringify(h.parametros, null, 2)}</pre></details> : null}
          </div>
        ))}
      </div>
      {catalogo.catalogos?.length ? (
        <>
          <h2 style={{ fontSize: 15, margin: "10px 0" }}>Catálogos que consulta</h2>
          <table className="tabla">
            <tbody>
              {catalogo.catalogos.map((c) => (
                <tr key={c.nombre}><td><b>{c.nombre}</b></td><td className="texto-2">{c.descripcion}</td><td className="texto-3">{c.tamano ? `${c.tamano} entradas` : ""}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </div>
  );
}

export function Ajustes() {
  const configuracion = usar((s) => s.configuracion);
  const modelos = usar((s) => s.modelos);
  const catalogo = usar((s) => s.catalogo);
  const guardar = usar((s) => s.guardarConfiguracion);
  const avisar = usar((s) => s.avisar);
  const [b, setB] = useState<Configuracion | null>(configuracion);
  useEffect(() => setB(configuracion), [configuracion]);
  if (!b || !catalogo) return null;
  const ajustados = Object.keys(b.parametros).length + Object.keys(b.entorno).length;

  const enviar = async (c: Configuracion) => {
    try {
      await guardar(c);
      avisar("Ajustes guardados");
    } catch (e) {
      avisar((e as Error).message);
    }
  };

  return (
    <div style={{ maxWidth: 720 }}>
      <div className="bloque">
        <h4>Modelo de IA</h4>
        <div className="texto-2 chico" style={{ marginBottom: 8 }}>Vertex AI con las credenciales de la cuenta de servicio montada en el contenedor.</div>
        <div className="cuadricula-2">
          <label className="chico texto-2">Modelo
            <input className="campo" list="modelos" value={b.ia.modelo} onChange={(e) => setB({ ...b, ia: { ...b.ia, modelo: e.target.value } })} />
            <datalist id="modelos">{modelos.map((m) => <option key={m} value={m} />)}</datalist>
          </label>
          <label className="chico texto-2">Ubicación
            <input className="campo" value={b.ia.ubicacion} onChange={(e) => setB({ ...b, ia: { ...b.ia, ubicacion: e.target.value } })} />
          </label>
        </div>
      </div>
      <div className="bloque">
        <h4>Procesamiento</h4>
        <div className="cuadricula-2">
          <label className="chico texto-2">Correos a la vez
            <input className="campo" type="number" min={1} max={8} value={b.concurrencia} onChange={(e) => setB({ ...b, concurrencia: Number(e.target.value) })} />
          </label>
          <label className="chico texto-2">Tope por correo (segundos)
            <input className="campo" type="number" min={30} max={1800} value={b.topeSegundos} onChange={(e) => setB({ ...b, topeSegundos: Number(e.target.value) })} />
          </label>
        </div>
      </div>
      <div className="fila-botones" style={{ marginBottom: 18 }}>
        <button className="boton primario" onClick={() => void enviar(b)}>Guardar</button>
        {ajustados > 0 && <button className="boton peligro" onClick={() => void enviar({ ...b, parametros: {}, entorno: {} })}>Restaurar {ajustados} parámetro{ajustados > 1 ? "s" : ""} al valor de producción</button>}
      </div>
      <div className="bloque">
        <h4>Parámetros ajustados</h4>
        {ajustados === 0 ? <div className="texto-3 chico">Todo corre con los valores de producción. Ajusta un parámetro desde su nodo en el flujo.</div> : (
          <table className="tabla">
            <tbody>
              {Object.entries(b.parametros).map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="mono">{JSON.stringify(v)}</td></tr>)}
              {Object.entries(b.entorno).map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="mono">{v}</td></tr>)}
            </tbody>
          </table>
        )}
      </div>
      <div className="bloque">
        <h4>Variables de entorno del servicio</h4>
        <table className="tabla">
          <tbody>
            {catalogo.variables_entorno.map((v) => <tr key={v.nombre}><td className="mono">{v.nombre}</td><td className="chico texto-2">{v.uso}</td><td>{v.secreto ? <span className="pastilla rojo">secreto</span> : null}</td></tr>)}
          </tbody>
        </table>
      </div>
    </div>
  );
}
