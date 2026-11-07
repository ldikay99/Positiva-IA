import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { pedir, usar } from "../estado.ts";
import { useSubruta } from "../rutas.ts";
import { Icono } from "../iconos.tsx";
import { usarEditor, type Credencial, type TipoCredencial } from "../editor/almacen.ts";
import { Interruptor } from "./Editores.tsx";
import { Selector } from "./Selector.tsx";
import { CampoNumero } from "./Formulario.tsx";

/**
 * Las conexiones (credenciales) con que los nodos hablan con otros sistemas.
 * Los secretos se escriben y nunca se leen: el servidor solo devuelve qué
 * campos están guardados. El permiso de escribir de verdad se da con una
 * confirmación explícita que explica el riesgo.
 */

const NOMBRE_VALIDO = /^[a-z0-9][a-z0-9._-]{0,62}$/;

function hace(t?: number): string {
  if (!t) return "";
  const s = (Date.now() - t) / 1000;
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return new Date(t).toLocaleDateString("es-CO", { day: "numeric", month: "short", year: "numeric" });
}

function CampoSecreto({ id, valor, alCambiar, placeholder, largo = false }: { id: string; valor: string; alCambiar: (v: string) => void; placeholder?: string; largo?: boolean }) {
  const [ver, setVer] = useState(false);
  return (
    <div className="c-secreto">
      {largo ? (
        <textarea id={id} className={`campo mono ${ver ? "" : "c-oculto"}`} rows={4} spellCheck={false} autoComplete="off" value={valor} placeholder={placeholder} onChange={(e) => alCambiar(e.target.value)} />
      ) : <input id={id} className="campo mono" type={ver ? "text" : "password"} autoComplete="new-password" spellCheck={false} value={valor} placeholder={placeholder} onChange={(e) => alCambiar(e.target.value)} />}
      <button type="button" className="boton-mini" aria-label={ver ? "Ocultar" : "Mostrar"} aria-pressed={ver} title={ver ? "Ocultar" : "Mostrar lo escrito"} onClick={() => setVer(!ver)}><Icono n="ojo" tam={14} /></button>
    </div>
  );
}

/** Crear o editar una conexión. Lo secreto se reemplaza, nunca se muestra. */
export function FormularioCredencial({ credencial, tiposPermitidos, alcance, alTerminar }: { credencial?: Credencial; tiposPermitidos?: string[]; /** El id del flujo si la conexión es solo de ese flujo. */ alcance?: string; alTerminar: (nombre: string | null) => void }) {
  const tipos = usarEditor((s) => s.tiposCredencial);
  const cargar = usarEditor((s) => s.cargarCredenciales);
  const existentes = usarEditor((s) => s.credenciales);
  const avisar = usar((s) => s.avisar);
  const disponibles = tipos.filter((t) => !tiposPermitidos?.length || tiposPermitidos.includes(t.tipo));
  const [tipo, setTipo] = useState<string>(credencial?.tipo ?? disponibles[0]?.tipo ?? "");
  const [nombre, setNombre] = useState(credencial?.nombre ?? "");
  const [descripcion, setDescripcion] = useState(credencial?.descripcion ?? "");
  const [valores, setValores] = useState<Record<string, string>>({});
  const [cambiando, setCambiando] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);
  const t: TipoCredencial | undefined = tipos.find((x) => x.tipo === tipo);
  const editando = Boolean(credencial);

  useEffect(() => {
    if (!tipo && disponibles[0]) setTipo(disponibles[0].tipo);
  }, [tipo, disponibles]);

  const guardar = async () => {
    setError("");
    if (!t) return setError("Elige el tipo de conexión.");
    if (!NOMBRE_VALIDO.test(nombre)) return setError("El nombre va en minúsculas y sin espacios: letras, números, punto, guion o guion bajo (por ejemplo api_pruebas).");
    if (!editando && existentes.some((c) => c.nombre === nombre)) return setError(`Ya existe una conexión «${nombre}». Usa otro nombre o edita la existente.`);
    const faltan = t.campos.filter((c) => c.obligatorio && !valores[c.nombre]?.trim() && !(editando && credencial!.campos.includes(c.nombre)));
    if (faltan.length) return setError(`Falta ${faltan.map((c) => `«${c.titulo}»`).join(", ")}.`);
    const enviar: Record<string, string> = {};
    for (const c of t.campos) {
      const v = valores[c.nombre];
      if (v === undefined) continue;
      if (c.secreto && editando && !cambiando.has(c.nombre)) continue;
      if (v !== "" || !c.secreto) enviar[c.nombre] = v;
    }
    setEnviando(true);
    try {
      const propio = alcance ?? (credencial?.alcance && credencial.alcance !== "global" ? credencial.alcance : undefined);
      await pedir(`/api/credenciales/${encodeURIComponent(nombre)}${propio ? `?flujo=${encodeURIComponent(propio)}` : ""}`, { method: "PUT", body: JSON.stringify({ tipo, valores: enviar, descripcion, ...(propio ? { alcance: propio } : {}) }) });
      await cargar();
      avisar(editando ? `Conexión «${nombre}» actualizada` : `Conexión «${nombre}» creada`);
      alTerminar(nombre);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setEnviando(false);
    }
  };

  if (!tipos.length) {
    return (
      <div className="c-form">
        <p className="texto-2 chico">El servidor todavía no publica los tipos de conexión. Vuelve a intentarlo en un momento.</p>
        <div className="fila"><button type="button" className="boton chico" onClick={() => void cargar()}>Reintentar</button><button type="button" className="boton chico" onClick={() => alTerminar(null)}>Cerrar</button></div>
      </div>
    );
  }

  return (
    <form className="c-form" onSubmit={(e) => { e.preventDefault(); void guardar(); }}>
      <div className="c-rejilla">
        <div className="f-campo">
          <span className="f-etiqueta"><span>Tipo de conexión</span></span>
          <Selector etiqueta="Tipo de conexión" valor={tipo} deshabilitado={editando} opciones={disponibles.map((x) => ({ valor: x.tipo, etiqueta: x.nombre, detalle: x.descripcion }))} alCambiar={(v) => { setTipo(v); setValores({}); }} />
        </div>
        <div className="f-campo">
          <label className="f-etiqueta" htmlFor="c-nombre"><span>Nombre</span><span className="f-obligatorio">obligatorio</span></label>
          <input id="c-nombre" className="campo mono" value={nombre} disabled={editando} placeholder="api_pruebas" onChange={(e) => setNombre(e.target.value.toLowerCase().replace(/\s+/g, "_"))} />
          <p className="f-ayuda">Así la eliges en los nodos. No se puede cambiar después.</p>
        </div>
      </div>
      {t?.ayuda && <p className="c-ayuda"><Icono n="info" tam={13} />{t.ayuda}</p>}
      <div className="f-campo">
        <label className="f-etiqueta" htmlFor="c-desc"><span>Para qué es</span></label>
        <input id="c-desc" className="campo" value={descripcion} placeholder="Ambiente de pruebas de SGDEA" onChange={(e) => setDescripcion(e.target.value)} />
      </div>
      {t?.campos.map((c) => {
        const id = `c-campo-${c.nombre}`;
        const guardado = editando && credencial!.campos.includes(c.nombre);
        return (
          <div key={c.nombre} className="f-campo">
            <label className="f-etiqueta" htmlFor={id}>
              <span>{c.titulo}</span>
              {c.obligatorio && !guardado && <span className="f-obligatorio">obligatorio</span>}
              {c.secreto && <span className="f-secreto"><Icono n="candado" tam={10} />secreto</span>}
            </label>
            {c.secreto && guardado && !cambiando.has(c.nombre) ? (
              <div className="c-guardado">
                <span className="mono" aria-label="Valor guardado, oculto">••••••••</span>
                <span className="texto-3 chico">guardado</span>
                <button type="button" className="boton chico" onClick={() => setCambiando((s) => new Set(s).add(c.nombre))}>Cambiar</button>
              </div>
            ) : c.secreto ? (
              <CampoSecreto id={id} largo={c.tipo === "texto-largo"} valor={valores[c.nombre] ?? ""} placeholder={c.placeholder} alCambiar={(v) => setValores((x) => ({ ...x, [c.nombre]: v }))} />
            ) : c.opciones?.length ? (
              <Selector etiqueta={c.titulo} valor={valores[c.nombre] ?? (c.defecto !== undefined ? String(c.defecto) : null)} opciones={c.opciones.map((o) => ({ valor: o, etiqueta: o }))} alCambiar={(v) => setValores((x) => ({ ...x, [c.nombre]: v }))} />
            ) : c.tipo === "booleano" ? (
              <Interruptor valor={(valores[c.nombre] ?? String(c.defecto ?? false)) === "true"} alCambiar={(v) => setValores((x) => ({ ...x, [c.nombre]: String(v) }))} />
            ) : c.tipo === "entero" ? (
              <CampoNumero id={id} valor={valores[c.nombre] ? Number(valores[c.nombre]) : undefined} min={1} max={65535} placeholder={c.defecto !== undefined ? String(c.defecto) : undefined} alCambiar={(v) => setValores((x) => ({ ...x, [c.nombre]: v === undefined ? "" : String(v) }))} />
            ) : c.tipo === "texto-largo" ? (
              <textarea id={id} className="campo mono" rows={4} value={valores[c.nombre] ?? ""} placeholder={guardado ? "Guardado: escribe para reemplazarlo" : c.placeholder} onChange={(e) => setValores((x) => ({ ...x, [c.nombre]: e.target.value }))} />
            ) : (
              <input id={id} className="campo" value={valores[c.nombre] ?? ""} placeholder={guardado ? "Guardado: escribe para reemplazarlo" : c.placeholder} onChange={(e) => setValores((x) => ({ ...x, [c.nombre]: e.target.value }))} />
            )}
            {c.ayuda && <p className="f-ayuda">{c.ayuda}</p>}
          </div>
        );
      })}
      {error && <p className="f-error" role="alert">{error}</p>}
      <div className="fila c-acciones">
        <button type="button" className="boton" onClick={() => alTerminar(null)}>Cancelar</button>
        <button type="submit" className="boton primario" disabled={enviando}>{enviando ? <span className="girando" /> : <Icono n="guardar" tam={14} />}{editando ? "Guardar cambios" : "Crear conexión"}</button>
      </div>
    </form>
  );
}

type Pendiente = { nombre: string; que: "escritura_si" | "escritura_no" | "borrar" } | null;

function TarjetaCredencial({ c, tipo, pendiente, setPendiente, editar }: { c: Credencial; tipo?: TipoCredencial; pendiente: Pendiente; setPendiente: (p: Pendiente) => void; editar: () => void }) {
  const cargar = usarEditor((s) => s.cargarCredenciales);
  const avisar = usar((s) => s.avisar);
  const flujos = usar((s) => s.flujos);
  const [ocupado, setOcupado] = useState(false);
  const aqui = pendiente?.nombre === c.nombre ? pendiente.que : null;
  const destino = tipo?.escritura ?? tipo?.nombre ?? c.tipo;

  const confirmar = async () => {
    setOcupado(true);
    try {
      if (aqui === "borrar") {
        await pedir(`/api/credenciales/${encodeURIComponent(c.nombre)}`, { method: "DELETE" });
        avisar(`Conexión «${c.nombre}» eliminada`);
      } else {
        const si = aqui === "escritura_si";
        await pedir(`/api/credenciales/${encodeURIComponent(c.nombre)}`, { method: "PATCH", headers: { "X-Confirmado-Por-Persona": "si" }, body: JSON.stringify({ permitir_escritura: si }) });
        avisar(si ? `«${c.nombre}» ya puede escribir en ${destino}` : `«${c.nombre}» vuelve a solo lectura`, si ? "info" : "ok");
      }
      setPendiente(null);
      await cargar();
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setOcupado(false);
    }
  };

  return (
    <motion.article layout className={`c-tarjeta ${c.permitir_escritura ? "escribe" : ""}`}>
      <header className="c-cabeza">
        <span className="c-icono"><Icono n="llave" tam={17} /></span>
        <div className="crece">
          <h2 className="mono">{c.nombre}</h2>
          <span className="texto-2 chico">{tipo?.nombre ?? c.tipo}{c.alcance && c.alcance !== "global" ? ` · solo del flujo ${flujos.find((f) => f.id === c.alcance)?.nombre ?? c.alcance}` : ""}{c.actualizado ? ` · actualizada ${hace(c.actualizado)}` : ""}</span>
        </div>
        <button className="boton chico" onClick={editar}><Icono n="editar" tam={13} />Editar</button>
        <button className="boton-mini peligro" aria-label={`Eliminar ${c.nombre}`} title="Eliminar" onClick={() => setPendiente({ nombre: c.nombre, que: "borrar" })}><Icono n="papelera" tam={14} /></button>
      </header>
      {c.descripcion && <p className="texto-2 chico">{c.descripcion}</p>}
      <dl className="c-datos">
        <div><dt>Guardado</dt><dd>{c.campos.length ? c.campos.join(", ") : "nada todavía"}</dd></div>
        <div>
          <dt>Uso</dt>
          <dd>{c.usada_por === undefined ? <span className="texto-3">{c.usada ? `la usó una corrida ${hace(c.usada)}` : "ninguna corrida todavía"}</span> : c.usada_por.length ? c.usada_por.map((u, i) => <span key={i} className="ficha">{flujos.find((f) => f.id === u.flujo)?.nombre ?? u.flujo}{u.nombre || u.nodo ? ` · ${u.nombre ?? u.nodo}` : ""}</span>) : <span className="texto-3">ningún nodo</span>}</dd>
        </div>
      </dl>
      <div className="c-escritura">
        <div className="crece">
          <b>{c.permitir_escritura ? "Puede escribir de verdad" : "Solo lectura"}</b>
          <span className="texto-2 chico">{c.permitir_escritura ? `Los nodos que la usan envían y modifican datos reales en ${destino}.` : "Lo que escribiría un nodo se arma y queda registrado, sin enviarse."}</span>
        </div>
        <Interruptor valor={c.permitir_escritura} alCambiar={(v) => setPendiente({ nombre: c.nombre, que: v ? "escritura_si" : "escritura_no" })} />
      </div>
      <AnimatePresence>
        {aqui && (
          <motion.div className={`c-confirmar ${aqui === "escritura_no" ? "suave" : ""}`} role="alertdialog" aria-label="Confirmar" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
            <div className="c-confirmar-texto">
              <Icono n="alerta" tam={16} />
              {aqui === "escritura_si" && <p>Esta credencial podrá escribir de verdad en <b>{destino}</b>. Los nodos que la usen dejarán de simular: enviarán, radicarán o cambiarán datos reales, y eso no se deshace desde aquí.</p>}
              {aqui === "escritura_no" && <p>Los nodos que usan <b>{c.nombre}</b> volverán a simular sus escrituras: armarán lo que enviarían, sin enviarlo.</p>}
              {aqui === "borrar" && <p>Se elimina <b>{c.nombre}</b> con sus valores guardados.{c.usada_por?.length ? ` ${c.usada_por.length} nodo${c.usada_por.length === 1 ? "" : "s"} la usa${c.usada_por.length === 1 ? "" : "n"} y dejará${c.usada_por.length === 1 ? "" : "n"} de poder conectarse.` : ""}</p>}
            </div>
            <div className="fila c-acciones">
              <button className="boton" onClick={() => setPendiente(null)} autoFocus>Cancelar</button>
              <button className={`boton ${aqui === "escritura_no" ? "primario" : "peligro-solido"}`} disabled={ocupado} onClick={() => void confirmar()}>
                {ocupado && <span className="girando" />}
                {aqui === "escritura_si" ? "Sí, permitir escritura" : aqui === "escritura_no" ? "Volver a solo lectura" : "Eliminar conexión"}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.article>
  );
}

export function VistaConexiones() {
  const credenciales = usarEditor((s) => s.credenciales);
  const tipos = usarEditor((s) => s.tiposCredencial);
  const disponibles = usarEditor((s) => s.credencialesDisponibles);
  const cargar = usarEditor((s) => s.cargarCredenciales);
  const [cargado, setCargado] = useState(false);
  // La conexión abierta vive en la URL: /conexiones/<nombre> o /conexiones/nueva
  const [editando, setEditando] = useSubruta();
  const [pendiente, setPendiente] = useState<Pendiente>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    void cargar().finally(() => setCargado(true));
  }, [cargar]);

  const lista = useMemo(() => {
    const t = q.trim().toLowerCase();
    return credenciales.filter((c) => !t || `${c.nombre} ${c.tipo} ${c.descripcion ?? ""}`.toLowerCase().includes(t)).sort((a, b) => a.nombre.localeCompare(b.nombre));
  }, [credenciales, q]);
  const escriben = credenciales.filter((c) => c.permitir_escritura).length;

  return (
    <div className="vista conexiones">
      <header className="vista-cabeza c-portada">
        <div className="crece">
          <h1>Conexiones</h1>
          <p className="texto-2">Las cuentas y llaves con que los nodos hablan con otros sistemas. Los secretos se guardan en el servidor y no se vuelven a mostrar; por defecto ninguna conexión escribe de verdad.</p>
        </div>
        <button className="boton primario" onClick={() => setEditando("nueva")} disabled={!disponibles}><Icono n="mas" tam={14} />Nueva conexión</button>
      </header>

      {!cargado ? (
        <div className="vacio"><span className="girando" />Cargando las conexiones…</div>
      ) : !disponibles ? (
        <div className="vacio grande">
          <Icono n="llave" tam={30} />
          <b>Las conexiones todavía no están disponibles en el servidor.</b>
          <span>Cuando lo estén, aquí verás cada credencial, qué nodos la usan y si puede escribir.</span>
          <button className="boton" onClick={() => void cargar()}>Volver a intentar</button>
        </div>
      ) : (
        <>
          <AnimatePresence>
            {editando === "nueva" && (
              <motion.section className="c-tarjeta c-nueva" initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
                <h2>Nueva conexión</h2>
                <FormularioCredencial alTerminar={() => setEditando(null)} />
              </motion.section>
            )}
          </AnimatePresence>
          {credenciales.length > 0 && (
            <div className="c-filtros">
              <div className="campo-icono crece"><Icono n="buscar" tam={14} /><input className="campo" placeholder="Buscar una conexión" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar una conexión" /></div>
              <span className="texto-2 chico">{credenciales.length} conexi{credenciales.length === 1 ? "ón" : "ones"}{escriben ? `, ${escriben} con permiso de escritura` : ", todas de solo lectura"}</span>
            </div>
          )}
          {!credenciales.length && editando !== "nueva" ? (
            <div className="vacio grande">
              <Icono n="llave" tam={30} />
              <b>Todavía no hay conexiones.</b>
              <span>Crea una para que un nodo pueda consultar un sistema (por ejemplo una API o SGDEA) sin poner la clave en el flujo.</span>
              <button className="boton primario" onClick={() => setEditando("nueva")}><Icono n="mas" tam={14} />Nueva conexión</button>
            </div>
          ) : (
            <div className="c-rejilla-tarjetas">
              {lista.map((c) =>
                editando === c.nombre ? (
                  <section key={c.nombre} className="c-tarjeta">
                    <h2>Editar <span className="mono">{c.nombre}</span></h2>
                    <FormularioCredencial credencial={c} alTerminar={() => setEditando(null)} />
                  </section>
                ) : (
                  <TarjetaCredencial key={c.nombre} c={c} tipo={tipos.find((t) => t.tipo === c.tipo)} pendiente={pendiente} setPendiente={setPendiente} editar={() => setEditando(c.nombre)} />
                ),
              )}
              {!lista.length && <div className="vacio">Ninguna conexión coincide con «{q}».</div>}
            </div>
          )}
        </>
      )}
    </div>
  );
}
