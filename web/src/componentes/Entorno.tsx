import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { normalizarEntorno, usarEditor, type Credencial, type VariableEntorno } from "../editor/almacen.ts";
import { textoJson } from "../editor/modelo.ts";
import { Dialogo } from "./Dialogos.tsx";
import { Interruptor } from "./Editores.tsx";
import { FormularioCredencial } from "./Conexiones.tsx";

/**
 * El entorno de un flujo (de código o declarativo): sus variables y secretos,
 * sus conexiones propias junto a las globales que usa, y el manifiesto que se
 * revisa antes de llevarlo a producción.
 */

export type SeccionEntorno = "variables" | "conexiones" | "manifiesto";

const NOMBRE = /^[A-Z][A-Z0-9_]{1,63}$/;
const HEADER_CONFIRMADO = { "X-Confirmado-Por-Persona": "si" };

function hace(t?: number): string {
  if (!t) return "";
  const s = (Date.now() - t) / 1000;
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return new Date(t).toLocaleDateString("es-CO", { day: "numeric", month: "short", year: "numeric" });
}

// ------------------------------------------------------------- variables

function FormularioVariable({ flujo, variable, existentes, alTerminar }: { flujo: string; variable?: VariableEntorno; existentes: string[]; alTerminar: (cambio: boolean) => void }) {
  const avisar = usar((s) => s.avisar);
  const [nombre, setNombre] = useState(variable?.nombre ?? "");
  const [secreto, setSecreto] = useState(variable?.secreto ?? false);
  const [valor, setValor] = useState(variable && !variable.secreto ? (variable.valor ?? "") : "");
  const [cambiarSecreto, setCambiarSecreto] = useState(!variable?.secreto || !variable.definido);
  const [descripcion, setDescripcion] = useState(variable?.descripcion ?? "");
  const [ver, setVer] = useState(false);
  const [confirmar, setConfirmar] = useState(false);
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);
  const editando = Boolean(variable);
  const tocaSecreto = secreto && cambiarSecreto && valor !== "";
  // Todo lo que toca un secreto (ponerlo, cambiarlo, describirlo o volverlo público) lo confirma una persona.
  const pideConfirmar = secreto || Boolean(variable?.secreto);

  const revisar = () => {
    setError("");
    if (!NOMBRE.test(nombre)) return setError("El nombre va en mayúsculas con guion bajo, por ejemplo SGDEA_URL o TOKEN_API."), false;
    if (!editando && existentes.includes(nombre)) return setError(`Ya existe ${nombre}: edítala en la lista.`), false;
    if (secreto && !editando && !valor) return setError("Escribe el valor del secreto."), false;
    return true;
  };

  const enviar = async () => {
    setEnviando(true);
    try {
      const cuerpo: Record<string, unknown> = { secreto, descripcion };
      if (!secreto || tocaSecreto) cuerpo.valor = valor;
      await pedir(`/api/flujos/${encodeURIComponent(flujo)}/entorno/${encodeURIComponent(nombre)}`, { method: "PUT", headers: pideConfirmar ? HEADER_CONFIRMADO : undefined, body: JSON.stringify(cuerpo) });
      avisar(editando ? `${nombre} actualizada` : `${nombre} agregada al entorno`);
      alTerminar(true);
    } catch (e) {
      setError((e as Error).message === "no existe" ? "El servidor todavía no guarda el entorno de los flujos." : (e as Error).message);
      setConfirmar(false);
    } finally {
      setEnviando(false);
    }
  };

  const guardar = () => {
    if (!revisar()) return;
    // Poner o cambiar un secreto pide una confirmación explícita antes de enviarse.
    if (pideConfirmar) setConfirmar(true);
    else void enviar();
  };

  return (
    <form className="ent-form" onSubmit={(e) => { e.preventDefault(); guardar(); }}>
      <div className="c-rejilla">
        <div className="f-campo">
          <label className="f-etiqueta" htmlFor="ent-nombre"><span>Nombre</span><span className="f-obligatorio">obligatorio</span></label>
          <input id="ent-nombre" className="campo mono" value={nombre} disabled={editando} placeholder="SGDEA_URL" autoFocus={!editando} onChange={(e) => setNombre(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))} />
        </div>
        <div className="f-campo f-interruptor">
          <div className="crece">
            <span className="f-etiqueta"><span>Es un secreto</span></span>
            <p className="f-ayuda">Una clave o token: se escribe y no se vuelve a mostrar.</p>
          </div>
          <Interruptor valor={secreto} alCambiar={(v) => { setSecreto(v); if (v) setCambiarSecreto(true); }} />
        </div>
      </div>
      <div className="f-campo">
        <label className="f-etiqueta" htmlFor="ent-valor"><span>Valor</span>{secreto && <span className="f-secreto"><Icono n="candado" tam={10} />secreto</span>}</label>
        {secreto && !cambiarSecreto ? (
          <div className="c-guardado">
            <span className="mono" aria-label="Valor guardado, oculto">••••••••</span>
            <span className="texto-3 chico">guardado</span>
            <button type="button" className="boton chico" onClick={() => setCambiarSecreto(true)}>Cambiar</button>
          </div>
        ) : secreto ? (
          <div className="c-secreto">
            <input id="ent-valor" className="campo mono" type={ver ? "text" : "password"} autoComplete="new-password" spellCheck={false} value={valor} onChange={(e) => setValor(e.target.value)} placeholder={editando ? "El nuevo valor" : ""} />
            <button type="button" className="boton-mini" aria-label={ver ? "Ocultar" : "Mostrar lo escrito"} aria-pressed={ver} onClick={() => setVer(!ver)}><Icono n="ojo" tam={14} /></button>
          </div>
        ) : (
          <input id="ent-valor" className="campo mono" value={valor} onChange={(e) => setValor(e.target.value)} placeholder="https://sgdea-pruebas.positiva.gov.co" />
        )}
        <p className="f-ayuda">Los nodos la leen como <code>{`{{entorno.${nombre || "NOMBRE"}}}`}</code>.</p>
      </div>
      <div className="f-campo">
        <label className="f-etiqueta" htmlFor="ent-desc"><span>Para qué es</span></label>
        <input id="ent-desc" className="campo" value={descripcion} placeholder="URL base de SGDEA en pruebas" onChange={(e) => setDescripcion(e.target.value)} />
      </div>
      {error && <p className="f-error" role="alert">{error}</p>}
      <AnimatePresence>
        {confirmar ? (
          <motion.div className="c-confirmar" role="alertdialog" aria-label="Confirmar el secreto" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
            <div className="c-confirmar-texto">
              <Icono n="candado" tam={16} />
              {variable?.secreto && !secreto ? (
                <p>Vas a volver pública <b>{nombre}</b>: su valor dejará de estar cifrado y se verá en esta pantalla y en el manifiesto.</p>
              ) : tocaSecreto ? (
                <p>Vas a {variable?.secreto && variable.definido ? "reemplazar" : "guardar"} el secreto <b>{nombre}</b> de este flujo. Queda cifrado en el servidor, nadie lo vuelve a ver desde aquí y los nodos lo usan al correr.</p>
              ) : (
                <p>Vas a cambiar los datos del secreto <b>{nombre}</b> (su valor no cambia).</p>
              )}
            </div>
            <div className="fila c-acciones">
              <button type="button" className="boton" autoFocus onClick={() => setConfirmar(false)}>Cancelar</button>
              <button type="button" className="boton primario" disabled={enviando} onClick={() => void enviar()}>{enviando && <span className="girando" />}{variable?.secreto && !secreto ? "Sí, volverla pública" : "Sí, guardar"}</button>
            </div>
          </motion.div>
        ) : (
          <div className="fila c-acciones">
            <button type="button" className="boton" onClick={() => alTerminar(false)}>Cancelar</button>
            <button type="submit" className="boton primario" disabled={enviando}>{enviando ? <span className="girando" /> : <Icono n="guardar" tam={14} />}{editando ? "Guardar cambios" : "Agregar variable"}</button>
          </div>
        )}
      </AnimatePresence>
    </form>
  );
}

function Variables({ flujo }: { flujo: string }) {
  const avisar = usar((s) => s.avisar);
  const cargarEnEditor = usarEditor((s) => s.cargarEntorno);
  const [lista, setLista] = useState<VariableEntorno[] | null | undefined>(undefined);
  const [editando, setEditando] = useState<string | "nueva" | null>(null);
  const [borrar, setBorrar] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  const cargar = useCallback(async () => {
    try {
      setLista(normalizarEntorno(await pedir<unknown>(`/api/flujos/${encodeURIComponent(flujo)}/entorno`)));
    } catch {
      setLista(null);
    }
    void cargarEnEditor(flujo);
  }, [flujo, cargarEnEditor]);
  useEffect(() => void cargar(), [cargar]);

  if (lista === undefined) return <div className="vacio"><span className="girando" />Cargando el entorno…</div>;
  if (lista === null) {
    return (
      <div className="vacio grande">
        <Icono n="llave" tam={26} />
        <b>El entorno por flujo todavía no está disponible en el servidor.</b>
        <button className="boton" onClick={() => void cargar()}>Volver a intentar</button>
      </div>
    );
  }
  const secretos = lista.filter((v) => v.secreto).length;
  const sinDefinir = lista.filter((v) => !v.definido).length;
  return (
    <div className="pila">
      <div className="fila envuelve">
        <p className="texto-2 chico crece">
          {lista.length ? `${lista.length} variable${lista.length === 1 ? "" : "s"}${secretos ? `, ${secretos} secreta${secretos === 1 ? "" : "s"}` : ""}${sinDefinir ? `. ${sinDefinir} sin valor todavía` : ""}.` : "Este flujo todavía no tiene variables propias."} Son solo de este flujo: otro flujo no las ve.
        </p>
        {editando !== "nueva" && <button className="boton chico primario" onClick={() => setEditando("nueva")}><Icono n="mas" tam={13} />Agregar variable</button>}
      </div>
      <AnimatePresence>
        {editando === "nueva" && (
          <motion.div className="ent-caja" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
            <FormularioVariable flujo={flujo} existentes={lista.map((v) => v.nombre)} alTerminar={(c) => { setEditando(null); if (c) void cargar(); }} />
          </motion.div>
        )}
      </AnimatePresence>
      <ul className="ent-lista">
        {lista.map((v) =>
          editando === v.nombre ? (
            <li key={v.nombre} className="ent-caja">
              <FormularioVariable flujo={flujo} variable={v} existentes={[]} alTerminar={(c) => { setEditando(null); if (c) void cargar(); }} />
            </li>
          ) : (
            <li key={v.nombre} className={`ent-fila ${v.definido ? "" : "sin-valor"}`}>
              <div className="ent-principal">
                <code className="ent-nombre">{v.nombre}</code>
                {v.secreto && <span className="ficha tono-violeta"><Icono n="candado" tam={10} />secreto</span>}
                {!v.definido && <span className="ficha tono-ambar">sin valor</span>}
                <span className="espacio" />
                <button className="boton chico" onClick={() => { setBorrar(null); setEditando(v.nombre); }}><Icono n="editar" tam={12} />Editar</button>
                <button className="boton-mini peligro" aria-label={`Eliminar ${v.nombre}`} title="Eliminar" onClick={() => setBorrar(v.nombre)}><Icono n="papelera" tam={13} /></button>
              </div>
              <div className="ent-valor mono">{v.secreto ? (v.definido ? "••••••••" : "—") : v.valor || <span className="texto-3">vacío</span>}</div>
              {(v.descripcion || v.actualizado) && <p className="texto-3 chico">{v.descripcion}{v.descripcion && v.actualizado ? " · " : ""}{v.actualizado ? `cambiada ${hace(v.actualizado)}` : ""}</p>}
              <AnimatePresence>
                {borrar === v.nombre && (
                  <motion.div className="c-confirmar" role="alertdialog" aria-label={`Eliminar ${v.nombre}`} initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
                    <div className="c-confirmar-texto"><Icono n="alerta" tam={16} /><p>Se elimina <b>{v.nombre}</b>{v.secreto ? " con su valor secreto" : ""}. Los nodos que usan <code>{`{{entorno.${v.nombre}}}`}</code> la recibirán vacía.</p></div>
                    <div className="fila c-acciones">
                      <button className="boton" autoFocus onClick={() => setBorrar(null)}>Cancelar</button>
                      <button
                        className="boton peligro-solido"
                        disabled={ocupado}
                        onClick={async () => {
                          setOcupado(true);
                          try {
                            await pedir(`/api/flujos/${encodeURIComponent(flujo)}/entorno/${encodeURIComponent(v.nombre)}`, { method: "DELETE" });
                            avisar(`${v.nombre} eliminada`);
                            setBorrar(null);
                            await cargar();
                          } catch (e) {
                            avisar((e as Error).message, "error");
                          } finally {
                            setOcupado(false);
                          }
                        }}
                      >
                        {ocupado && <span className="girando" />}Eliminar variable
                      </button>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </li>
          ),
        )}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------ conexiones

function FilaConexion({ c, propia }: { c: Credencial; propia: boolean }) {
  const irA = usar((s) => s.irA);
  return (
    <li className="ent-fila">
      <div className="ent-principal">
        <span className="c-icono chico"><Icono n="llave" tam={14} /></span>
        <code className="ent-nombre">{c.nombre}</code>
        <span className="texto-2 chico">{c.tipo}</span>
        <span className="espacio" />
        <span className={`ficha ${c.permitir_escritura ? "tono-rojo" : "tono-gris"}`}>{c.permitir_escritura ? "puede escribir" : "solo lectura"}</span>
        {!propia && <button className="boton chico" onClick={() => irA("conexiones")}>Ver en Conexiones</button>}
      </div>
      {c.descripcion && <p className="texto-3 chico">{c.descripcion}</p>}
    </li>
  );
}

function ConexionesDelFlujo({ flujo }: { flujo: string }) {
  const [lista, setLista] = useState<Credencial[] | null | undefined>(undefined);
  const [crear, setCrear] = useState(false);
  const cargarGlobal = usarEditor((s) => s.cargarCredenciales);
  const tipos = usarEditor((s) => s.tiposCredencial);
  const cargar = useCallback(async () => {
    try {
      const r = await pedir<unknown>(`/api/credenciales?flujo=${encodeURIComponent(flujo)}`);
      const crudas = Array.isArray(r) ? r : [];
      setLista((crudas as Record<string, unknown>[]).map((c) => ({
        nombre: String(c.nombre ?? ""), tipo: String(c.tipo ?? ""), alcance: typeof c.alcance === "string" ? c.alcance : "global",
        descripcion: typeof c.descripcion === "string" ? c.descripcion : undefined, campos: Array.isArray(c.campos) ? c.campos.map(String) : [],
        permitir_escritura: Boolean(c.permitir_escritura),
        usada_por: Array.isArray(c.usada_por) ? (c.usada_por as unknown[]).map((u) => (typeof u === "string" ? { flujo: u } : { flujo: String((u as Record<string, unknown>).flujo ?? "") })) : undefined,
      })));
    } catch {
      setLista(null);
    }
  }, [flujo]);
  useEffect(() => {
    void cargar();
    if (!tipos.length) void cargarGlobal();
  }, [cargar, cargarGlobal, tipos.length]);

  if (lista === undefined) return <div className="vacio"><span className="girando" />Cargando las conexiones…</div>;
  if (lista === null) return <div className="vacio grande"><Icono n="llave" tam={26} /><b>Las conexiones todavía no están disponibles en el servidor.</b><button className="boton" onClick={() => void cargar()}>Volver a intentar</button></div>;
  const propias = lista.filter((c) => c.alcance === flujo);
  const globales = lista.filter((c) => c.alcance !== flujo && (!c.usada_por || c.usada_por.some((u) => u.flujo === flujo)));
  return (
    <div className="pila">
      <section className="seccion">
        <div className="fila">
          <h3 className="crece">De este flujo <span>{propias.length}</span></h3>
          {!crear && <button className="boton chico primario" onClick={() => setCrear(true)}><Icono n="mas" tam={13} />Crear conexión para este flujo</button>}
        </div>
        <AnimatePresence>
          {crear && (
            <motion.div className="ent-caja" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              <FormularioCredencial alcance={flujo} alTerminar={(n) => { setCrear(false); if (n) void cargar(); }} />
            </motion.div>
          )}
        </AnimatePresence>
        {propias.length ? <ul className="ent-lista">{propias.map((c) => <FilaConexion key={c.nombre} c={c} propia />)}</ul> : !crear && <p className="texto-3 chico">Ninguna conexión es solo de este flujo.</p>}
      </section>
      <section className="seccion">
        <h3>Globales que usa <span>{globales.length}</span></h3>
        {globales.length ? <ul className="ent-lista">{globales.map((c) => <FilaConexion key={c.nombre} c={c} propia={false} />)}</ul> : <p className="texto-3 chico">No usa conexiones globales.</p>}
      </section>
    </div>
  );
}

// ------------------------------------------------------------ manifiesto

const TITULOS: Record<string, string> = {
  flujo: "Flujo", hosts_http: "Hosts HTTP", entorno_sin_definir: "Variables usadas sin definir", flujos_llamados: "Flujos que llama", archivos: "Archivos", tablas: "Tablas", limites: "Límites de la plataforma", programacion: "Programación", secreto_definido: "Secreto definido", nodos_: "Nodos", usado: "Usada",
  disparador: "Cómo arranca", nodos: "Pasos", conexiones: "Conexiones externas", credenciales: "Conexiones externas", entorno: "Variables de entorno", variables: "Variables de entorno",
  escrituras: "Escrituras", capacidades_escritura: "Escrituras", escritura: "Escrituras", horario: "Horario", programado: "Horario", webhook: "Webhook", version: "Versión", modelo: "Modelo de IA",
  descripcion: "Descripción", obligatorio: "Obligatorio", tipo: "Tipo", id: "Id", actualizado: "Actualizado", creado: "Creado", nombre: "Nombre",
  campos: "Campos", direccion: "Dirección", metodo: "Método", credencial: "Conexión", habilitada: "Habilitada", destino: "Destino", alcance: "Alcance",
  operacion: "Operación", consulta: "Consulta", ruta: "Ruta", categoria: "Categoría", accion: "Acción", ultima_ejecucion: "Última ejecución",
};

function etiqueta(k: string): string {
  return TITULOS[k] ?? (k.replace(/_/g, " ").replace(/^./, (x) => x.toUpperCase()));
}

function Valor({ v, clave = "" }: { v: unknown; clave?: string }) {
  if (v === null || v === undefined || v === "") return <span className="texto-3">—</span>;
  if (typeof v === "boolean") {
    const escribe = /escrib|escritura|habilit|activo|permit/.test(clave);
    return <span className={`ficha ${v ? (escribe ? "tono-rojo" : "tono-verde") : "tono-gris"}`}>{v ? "sí" : "no"}</span>;
  }
  if (typeof v === "number" && v > 1e11 && /actualiz|cread|generad|ultima|usada|fecha|^t$/.test(clave)) return <span>{new Date(v).toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short" })}</span>;
  if (typeof v === "string" || typeof v === "number") return <span className={typeof v === "string" && (v.startsWith("http") || /^[A-Z0-9_]+$/.test(v)) ? "mono" : ""}>{String(v)}</span>;
  if (Array.isArray(v)) {
    if (!v.length) return <span className="texto-3">ninguno</span>;
    if (v.every((x) => typeof x !== "object" || x === null) && v.some((x) => String(x).length > 40)) return <ul className="man-frases">{v.map((x, i) => <li key={i}>{String(x)}</li>)}</ul>;
    if (v.every((x) => typeof x !== "object" || x === null)) return <span className="fichas">{v.map((x, i) => <span key={i} className="ficha">{String(x)}</span>)}</span>;
    return (
      <ul className="man-lista">
        {v.map((x, i) => (
          <li key={i}><Objeto o={x as Record<string, unknown>} /></li>
        ))}
      </ul>
    );
  }
  return <Objeto o={v as Record<string, unknown>} />;
}

function Objeto({ o }: { o: Record<string, unknown> }) {
  const titulo = o.nombre ?? o.id ?? o.tipo;
  const resto = Object.entries(o).filter(([k]) => !(k === "nombre" && titulo === o.nombre) && k !== "secreto");
  return (
    <div className="man-objeto">
      {titulo !== undefined && (
        <div className="fila">
          <b className={/^[A-Z0-9_]+$/.test(String(titulo)) ? "mono" : ""}>{String(titulo)}</b>
          {o.secreto === true && <span className="ficha tono-violeta"><Icono n="candado" tam={10} />secreto</span>}
        </div>
      )}
      <dl className="man-dl">
        {resto.filter(([k]) => !(titulo !== undefined && ((k === "id" && titulo === o.id) || (k === "tipo" && titulo === o.tipo)))).map(([k, v]) => (
          <div key={k}><dt>{etiqueta(k)}</dt><dd><Valor v={v} clave={k} /></dd></div>
        ))}
      </dl>
    </div>
  );
}

function Manifiesto({ flujo }: { flujo: string }) {
  const [datos, setDatos] = useState<Record<string, unknown> | null | undefined>(undefined);
  const nombre = usar((s) => s.flujos.find((f) => f.id === flujo)?.nombre ?? flujo);
  const cargar = useCallback(async () => {
    try {
      setDatos(await pedir<Record<string, unknown>>(`/api/flujos/${encodeURIComponent(flujo)}/manifiesto`));
    } catch {
      setDatos(null);
    }
  }, [flujo]);
  useEffect(() => void cargar(), [cargar]);
  const secciones = useMemo(() => {
    if (!datos) return [];
    const orden = ["flujo", "disparador", "horario", "programado", "webhook", "nodos", "conexiones", "credenciales", "entorno", "variables", "escrituras", "capacidades_escritura", "escritura"];
    return Object.entries(datos).filter(([k]) => k !== "manifiesto" && k !== "generado").sort(([a], [b]) => (orden.indexOf(a) + 1 || 99) - (orden.indexOf(b) + 1 || 99));
  }, [datos]);
  const descargar = () => {
    const b = new Blob([textoJson(datos)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = `manifiesto-${flujo}-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };
  if (datos === undefined) return <div className="vacio"><span className="girando" />Armando el manifiesto…</div>;
  if (datos === null) return <div className="vacio grande"><Icono n="documento" tam={26} /><b>El manifiesto todavía no está disponible en el servidor.</b><button className="boton" onClick={() => void cargar()}>Volver a intentar</button></div>;
  const escrituras = JSON.stringify(datos).match(/"(permitir_escritura|escribe|habilitada)":true/g)?.length ?? 0;
  return (
    <div className="pila manifiesto">
      <div className="man-portada">
        <div className="crece">
          <b>Hoja de despliegue de «{nombre}»</b>
          <p className="texto-2 chico">Lo que este flujo necesita y lo que puede hacer en otros sistemas. Revísala antes de pasarlo a producción.{typeof datos.generado === "string" ? ` Generada el ${new Date(datos.generado).toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short" })}.` : ""}</p>
        </div>
        {escrituras > 0 ? <span className="pastilla rojo"><Icono n="alerta" tam={12} />{escrituras} escritura{escrituras === 1 ? "" : "s"} real{escrituras === 1 ? "" : "es"} habilitada{escrituras === 1 ? "" : "s"}</span> : <span className="pastilla verde"><Icono n="candado" tam={12} />Sin escrituras reales</span>}
        <button className="boton chico" onClick={descargar}><Icono n="descargar" tam={13} />Descargar JSON</button>
      </div>
      {secciones.map(([k, v]) => (
        <section key={k} className="man-seccion">
          <h3>{etiqueta(k)}</h3>
          <Valor v={v} clave={k} />
        </section>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- panel

export function PanelEntorno({ abierto, seccion, cambiarSeccion, cerrar }: { abierto: boolean; seccion: SeccionEntorno; cambiarSeccion: (s: SeccionEntorno) => void; cerrar: () => void }) {
  const flujo = usar((s) => s.flujo);
  const nombre = usar((s) => s.flujos.find((f) => f.id === s.flujo)?.nombre ?? s.flujo);
  const tipo = usar((s) => s.flujos.find((f) => f.id === s.flujo)?.tipo);
  return (
    <Dialogo abierto={abierto} cerrar={cerrar} titulo={`Entorno de «${nombre}»`} sub={tipo === "codigo" ? "Flujo de código: estas variables y conexiones son solo suyas." : "Variables, secretos y conexiones solo de este flujo."} ancho={760}>
      <nav className="pestanas ent-pestanas" role="tablist" aria-label="Secciones del entorno">
        {(["variables", "conexiones", "manifiesto"] as const).map((s) => (
          <button key={s} role="tab" aria-selected={seccion === s} className={seccion === s ? "activa" : ""} onClick={() => cambiarSeccion(s)}>
            {s === "variables" ? "Variables" : s === "conexiones" ? "Conexiones" : "Manifiesto"}
            {seccion === s && <motion.i layoutId="pestana-entorno" className="pestana-linea" />}
          </button>
        ))}
      </nav>
      <div className="ent-cuerpo">
        {seccion === "variables" && <Variables flujo={flujo} />}
        {seccion === "conexiones" && <ConexionesDelFlujo flujo={flujo} />}
        {seccion === "manifiesto" && <Manifiesto flujo={flujo} />}
      </div>
    </Dialogo>
  );
}
