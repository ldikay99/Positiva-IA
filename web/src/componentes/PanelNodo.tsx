import { useEffect, useState } from "react";
import { usar } from "../estado.ts";
import { NOMBRES_DESENLACE, progreso, segundos } from "../progreso.ts";
import type { Parametro } from "../tipos.ts";

function texto(v: unknown): string {
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

function interpretar(p: Parametro, crudo: string): { ok: true; valor: unknown } | { ok: false } {
  try {
    if (p.tipo === "int") {
      const n = Number(crudo);
      return Number.isInteger(n) ? { ok: true, valor: n } : { ok: false };
    }
    if (p.tipo === "float") {
      const n = Number(crudo.replace(",", "."));
      return Number.isFinite(n) ? { ok: true, valor: n } : { ok: false };
    }
    if (p.tipo === "bool") return { ok: true, valor: crudo === "true" };
    if (p.tipo === "str") return { ok: true, valor: crudo };
    return { ok: true, valor: JSON.parse(crudo) as unknown };
  } catch {
    return { ok: false };
  }
}

function EditorParametro({ p }: { p: Parametro }) {
  const configuracion = usar((s) => s.configuracion);
  const guardar = usar((s) => s.guardarConfiguracion);
  const avisar = usar((s) => s.avisar);
  const porEntorno = p.ajuste === "entorno" && p.env !== null;
  const sobrescrito = configuracion?.parametros[p.clave];
  const enEntorno = porEntorno ? configuracion?.entorno[p.env!] : undefined;
  const actual = porEntorno ? (enEntorno ?? p.valor) : (sobrescrito ?? p.valor);
  const [borrador, setBorrador] = useState(texto(actual));
  useEffect(() => setBorrador(texto(actual)), [actual]);
  const ajustable = p.ajuste !== null;
  const cambiado = porEntorno ? enEntorno !== undefined : sobrescrito !== undefined;

  const aplicar = async (valor: unknown | undefined) => {
    if (!configuracion) return;
    const sig = { ...configuracion, parametros: { ...configuracion.parametros }, entorno: { ...configuracion.entorno } };
    if (porEntorno) {
      if (valor === undefined) delete sig.entorno[p.env!];
      else sig.entorno[p.env!] = String(valor);
    } else if (valor === undefined) delete sig.parametros[p.clave];
    else sig.parametros[p.clave] = valor;
    try {
      await guardar(sig);
      avisar(valor === undefined ? `${p.nombre} vuelve a su valor original` : `${p.nombre} ajustado para las próximas corridas`);
    } catch (e) {
      avisar((e as Error).message);
    }
  };

  const largo = typeof actual !== "string" || String(actual).length > 40 || p.tipo === "list" || p.tipo === "dict";
  return (
    <div className={`param ${cambiado ? "cambiado" : ""}`}>
      <div className="nombre">
        <code>{p.nombre}</code>
        <span className="pastilla">{p.tipo}</span>
        {p.env && <span className="pastilla">env {p.env}</span>}
        {!ajustable && <span className="pastilla">fijo</span>}
        {cambiado && <span className="pastilla ambar">ajustado</span>}
      </div>
      <div className="descr">{p.descripcion}</div>
      {ajustable ? (
        <>
          {p.tipo === "bool" ? (
            <select className="campo" value={borrador} onChange={(e) => setBorrador(e.target.value)}>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          ) : largo ? (
            <textarea className="campo mono" value={borrador} onChange={(e) => setBorrador(e.target.value)} />
          ) : (
            <input className="campo mono" value={borrador} onChange={(e) => setBorrador(e.target.value)} />
          )}
          <div className="fila-botones" style={{ marginTop: 6 }}>
            <button
              className="boton chico primario"
              disabled={borrador === texto(actual)}
              onClick={() => {
                const r = interpretar(p, borrador);
                if (!r.ok) return avisar(`Valor inválido para ${p.tipo}`);
                void aplicar(r.valor);
              }}
            >
              Aplicar
            </button>
            {cambiado && (
              <button className="boton chico" onClick={() => void aplicar(undefined)}>
                Restaurar ({texto(p.valor).slice(0, 30)})
              </button>
            )}
          </div>
        </>
      ) : (
        <>
          <pre className="correo-cuerpo mono" style={{ maxHeight: 140 }}>{texto(p.valor)}</pre>
          {(p.motivo_fijo ?? p.nota_sobrescritura) && <div className="texto-3 chico">{p.motivo_fijo ?? p.nota_sobrescritura}</div>}
        </>
      )}
      <div className="texto-3 chico" style={{ marginTop: 4 }}>{p.modulo}</div>
    </div>
  );
}

export function PanelNodo() {
  const catalogo = usar((s) => s.catalogo);
  const id = usar((s) => s.nodo);
  const verNodo = usar((s) => s.verNodo);
  const corrida = usar((s) => (s.seleccionada ? s.corridas[s.seleccionada] : undefined));
  const [busqueda, setBusqueda] = useState("");
  useEffect(() => setBusqueda(""), [id]);
  if (!catalogo || !id) return null;

  if (id.startsWith("fin:")) {
    const accion = id.slice(4);
    const d = catalogo.desenlaces.find((x) => x.accion === accion);
    const origenes = catalogo.nodos.filter((n) => n.desenlaces?.some((x) => x.accion === accion));
    return (
      <aside className="columna der">
        <div className="cabeza"><div className="cabeza-fila"><h2>{NOMBRES_DESENLACE[accion] ?? accion}</h2><button className="boton chico" onClick={() => verNodo(null)}>Cerrar</button></div></div>
        <div className="desplaza">
          <div className="seccion texto-2">{d?.descripcion}</div>
          <div className="seccion">
            <h3>Etapas que llevan aquí</h3>
            {origenes.map((n) => (
              <div key={n.id} className="param" style={{ cursor: "pointer" }} onClick={() => verNodo(n.id)}>
                <div className="nombre">{n.nombre}</div>
                {n.desenlaces?.filter((x) => x.accion === accion).map((x, i) => <div key={i} className="descr">{x.cuando}</div>)}
              </div>
            ))}
          </div>
        </div>
      </aside>
    );
  }

  const n = catalogo.nodos.find((x) => x.id === id);
  if (!n) return null;
  const est = progreso(catalogo, corrida).nodos[n.id];
  const q = busqueda.trim().toLowerCase();
  const visibles = n.parametros.filter((p) => !q || `${p.nombre} ${p.descripcion}`.toLowerCase().includes(q));
  const ajustables = visibles.filter((p) => p.ajuste !== null);
  const fijos = visibles.filter((p) => p.ajuste === null);

  return (
    <aside className="columna der">
      <div className="cabeza">
        <div className="cabeza-fila">
          <h2>{n.nombre}</h2>
          {n.usa_ia && <span className="pastilla azul">Usa IA</span>}
          <button className="boton chico" onClick={() => verNodo(null)}>Cerrar</button>
        </div>
        <div className="texto-2">{n.descripcion}</div>
        {est && (
          <div className="fila-botones">
            <span className={`pastilla ${est.estado === "fallo" ? "rojo" : est.estado === "activo" ? "azul" : "verde"}`}>{est.estado === "activo" ? "En curso" : est.estado === "fallo" ? "Falló" : "Completada"}</span>
            <span className="pastilla">{segundos(est.ms)}</span>
            {est.fallo && <span className="pastilla rojo">{est.fallo}</span>}
          </div>
        )}
      </div>
      <div className="desplaza">
        {n.desenlaces && n.desenlaces.length > 0 && (
          <div className="seccion">
            <h3>Puede terminar el correo en</h3>
            {n.desenlaces.map((d, i) => (
              <div key={i} className="param">
                <div className="nombre">{NOMBRES_DESENLACE[d.accion] ?? d.accion}</div>
                <div className="descr">{d.cuando}</div>
              </div>
            ))}
          </div>
        )}
        {n.reglas && n.reglas.length > 0 && (
          <div className="seccion">
            <h3>Reglas ({n.reglas.length})</h3>
            {n.reglas.map((r) => (
              <div key={r.id} className="param">
                <div className="nombre">{r.id}</div>
                <div className="descr">{r.descripcion}</div>
                {r.funcion && <div className="texto-3 chico">{r.modulo} · {r.funcion}</div>}
              </div>
            ))}
          </div>
        )}
        {n.parametros.length > 8 && (
          <div className="seccion">
            <input className="campo" placeholder={`Buscar entre ${n.parametros.length} parámetros`} value={busqueda} onChange={(e) => setBusqueda(e.target.value)} />
          </div>
        )}
        <div className="seccion">
          <h3>Parámetros ajustables ({ajustables.length})</h3>
          {ajustables.length ? ajustables.map((p) => <EditorParametro key={p.clave} p={p} />) : <div className="texto-3 chico">Esta etapa no tiene parámetros que se puedan cambiar sin tocar el código.</div>}
        </div>
        {fijos.length > 0 && (
          <div className="seccion">
            <h3>Parámetros fijos en el código ({fijos.length})</h3>
            {fijos.map((p) => <EditorParametro key={p.clave} p={p} />)}
          </div>
        )}
        <div className="seccion texto-3 chico">
          {n.modulo} {n.funciones?.length ? `· ${n.funciones.join(", ")}` : ""}
        </div>
      </div>
    </aside>
  );
}
