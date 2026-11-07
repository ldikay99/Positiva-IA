import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { normalizarEntorno, usarEditor, type Credencial, type VariableEntorno } from "../editor/almacen.ts";
import { textoJson } from "../editor/modelo.ts";
import { Dialogo } from "./Dialogos.tsx";
import { Interruptor } from "./Editores.tsx";
import { FormularioCredencial } from "./Conexiones.tsx";
import { CampoCron, CampoNumero } from "./Formulario.tsx";
import { describirCron } from "../editor/modelo.ts";

/**
 * El entorno de un flujo (de código o declarativo): sus variables y secretos,
 * sus conexiones propias junto a las globales que usa, y el manifiesto que se
 * revisa antes de llevarlo a producción. Los flujos de código (pqrd, tutelas)
 * tienen además «Ejecución»: la compuerta de radicación y la programación del job.
 */

export type SeccionEntorno = "variables" | "conexiones" | "manifiesto" | "ejecucion";

const NOMBRE = /^[A-Z][A-Z0-9_]{1,63}$/;
// Tutelas usa en su job nombres con guiones (TUTELAS-REDIS-PASSWORD…): solo ahí se aceptan.
const NOMBRE_CON_GUION = /^[A-Z][A-Z0-9_-]{1,63}$/;
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
  const deCodigo = usar((s) => s.flujos.find((f) => f.id === flujo)?.tipo === "codigo");
  // En un flujo de código las variables son las del job: deciden a dónde lee y escribe, así que también se confirman.
  const pideConfirmar = secreto || Boolean(variable?.secreto) || deCodigo;
  // tutelas y sus copias usan los nombres de su job (con guion)
  const conGuion = usar((s) => (s.flujos.find((f) => f.id === flujo)?.base ?? flujo) === "tutelas");

  const revisar = () => {
    setError("");
    if (!(conGuion ? NOMBRE_CON_GUION : NOMBRE).test(nombre)) return setError(conGuion ? "El nombre va en mayúsculas, como en el job: TUTELAS-REDIS-PASSWORD o DATABASE_HOST." : "El nombre va en mayúsculas con guion bajo, por ejemplo SGDEA_URL o TOKEN_API."), false;
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
          <input id="ent-nombre" className="campo mono" value={nombre} disabled={editando} placeholder="SGDEA_URL" autoFocus={!editando} onChange={(e) => setNombre(e.target.value.toUpperCase().replace(conGuion ? /[^A-Z0-9_-]/g : /[^A-Z0-9_]/g, "_"))} />
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
              ) : !secreto && !variable?.secreto ? (
                <p>Vas a {editando ? "cambiar" : "poner"} <b>{nombre}</b> en el entorno de este flujo de código. Si es una variable del job, decide a dónde lee (y, con la radicación real encendida, a dónde escribe) el flujo.</p>
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
                {!v.definido && <span className="ficha tono-ambar" title="Declarada sin valor (en una copia los secretos no pasan): ponle el suyo">falta valor</span>}
                {(v as { copiado_de?: string }).copiado_de && <span className="ficha tono-azul" title={`Copiada de «${(v as { copiado_de?: string }).copiado_de}» al clonar: es el mismo valor (los secretos van cifrados). Cámbiala aquí sin tocar el original.`}>copiada de {(v as { copiado_de?: string }).copiado_de}</span>}
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
  entorno_produccion: "Variables del job en producción", ejecucion: "Cómo se ejecuta", despliegue: "Despliegue", sistema: "Sistema", como: "Cómo se conecta",
  acceso: "Acceso", donde: "Dónde", que: "Qué hace", en_este_visor: "En este visor", origen: "Origen", para: "Para qué", secreto: "Secreto",
  verificado_en_job: "Verificado en el job", observaciones: "Observaciones", secreto_gcp: "Secreto en GCP",
  operacion: "Operación", consulta: "Consulta", ruta: "Ruta", categoria: "Categoría", accion: "Acción", ultima_ejecucion: "Última ejecución",
  compuerta: "Compuerta de radicación", radicacion_real: "Radicación real", encendida_en_la_plataforma: "Encendida en la plataforma", permitido_en_despliegue: "Permitida en el despliegue",
  estado: "Estado", cambiado_por: "Cambiada por", cambiado: "Cambiada", como_se_enciende: "Cómo se enciende", como_el_job: "Como el job", tope_por_corrida_s: "Tope por corrida (s)",
  sin_solapes: "Sin solapes", activo: "Encendida", zona: "Zona", cron: "Cron", n: "Correos por corrida",
  lecturas_con_la_compuerta_apagada: "Qué se lee de verdad con la compuerta apagada", con_credenciales: "Con credenciales", sin_credenciales: "Sin credenciales",
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
  const escrituras = JSON.stringify(datos).match(/"(permitir_escritura|escribe|habilitada|radicacion_real)":true/g)?.length ?? 0;
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

// ------------------------------------------- ejecución (solo flujos de código)

export interface EstadoCompuerta {
  radicacion_real: boolean;
  radicacion_real_efectiva?: boolean;
  permitido_en_despliegue: boolean;
  cambiado_por?: string;
  cambiado?: number;
  texto?: string;
}

interface Programacion {
  cron: string;
  zona: string;
  activo: boolean;
  n: number;
  del_job?: { cron: string; zona: string; n: number };
  tope_por_corrida_s?: number;
  en_curso?: { desde: number; corridas: number } | null;
  ultima_ejecucion?: number | null;
}

/** Lo que escribe cada job cuando la compuerta está encendida: se lee antes de confirmar. */
const CONSECUENCIAS: Record<string, string[]> = {
  pqrd: [
    "Radica en SGDEA las PQRD que el motor dé por buenas.",
    "Etiqueta, mueve y reenvía los correos del buzón servicioalcliente@positiva.gov.co.",
    "Deja las trazas de radicación en Supabase.",
  ],
  tutelas: [
    "Radica las tutelas en SGDEA y crea su trazabilidad.",
    "Responde, etiqueta y mueve los hilos del buzón notificacionesjudiciales@positiva.gov.co.",
    "Reclama cada radicado en Redis (el job lo verá como ya procesado).",
  ],
};

/** Avisa a la barra superior que la compuerta cambió. */
export const avisarCompuerta = (flujo: string, c: EstadoCompuerta) => window.dispatchEvent(new CustomEvent("positiva:compuerta", { detail: { flujo, ...c } }));

function Compuerta({ flujo }: { flujo: string }) {
  const avisar = usar((s) => s.avisar);
  const [c, setC] = useState<EstadoCompuerta | null | undefined>(undefined);
  const [confirmar, setConfirmar] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const cargar = useCallback(async () => {
    try {
      setC(await pedir<EstadoCompuerta>(`/api/flujos/${encodeURIComponent(flujo)}/compuerta`));
    } catch {
      setC(null);
    }
  }, [flujo]);
  useEffect(() => void cargar(), [cargar]);

  const cambiar = async (encender: boolean) => {
    setOcupado(true);
    try {
      // El encabezado solo va tras la confirmación explícita de una persona en esta pantalla.
      const r = await pedir<EstadoCompuerta>(`/api/flujos/${encodeURIComponent(flujo)}/compuerta`, { method: "PUT", headers: encender ? HEADER_CONFIRMADO : undefined, body: JSON.stringify({ radicacion_real: encender }) });
      setC(r);
      avisarCompuerta(flujo, r);
      avisar(encender ? "Radicación real encendida: las corridas siguientes escriben como el job" : "Radicación real apagada: todo vuelve a la simulación", encender ? "error" : "ok");
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setOcupado(false);
      setConfirmar(false);
    }
  };

  if (c === undefined) return <div className="vacio"><span className="girando" />Leyendo la compuerta…</div>;
  if (c === null) return <div className="vacio"><Icono n="alerta" tam={20} /><b>No se pudo leer la compuerta.</b><button className="boton chico" onClick={() => void cargar()}>Volver a intentar</button></div>;
  const efectiva = c.radicacion_real_efectiva ?? (c.radicacion_real && c.permitido_en_despliegue);
  // Encender necesita la segunda llave; apagar siempre se puede.
  const bloqueado = !efectiva && !c.permitido_en_despliegue && !c.radicacion_real;
  return (
    <section className="seccion">
      <h3>Compuerta de radicación</h3>
      <div className="ent-caja pila">
        <div className="ent-principal">
          <Icono n={efectiva ? "alerta" : "candado"} tam={16} />
          <b className="crece">{efectiva ? "Radicación real: encendida" : "Radicación real: apagada (simulación)"}</b>
          <span className={`ficha ${efectiva ? "tono-rojo" : "tono-gris"}`}>{efectiva ? "escribe como el job" : "nada se escribe"}</span>
          <Interruptor
            valor={c.radicacion_real}
            peligro
            etiqueta="Radicación real"
            deshabilitado={ocupado || bloqueado || confirmar}
            alCambiar={(v) => (v ? setConfirmar(true) : void cambiar(false))}
          />
        </div>
        <p className="texto-2 chico">
          {efectiva
            ? "Las corridas de este flujo (también las programadas) escriben de verdad en SGDEA, el buzón y las trazas, como el job. Apágala para volver a la simulación."
            : "Corre el proceso completo con el entorno real: lee lo que se puede leer en solo lectura y toda escritura (radicar, mover, etiquetar, responder, trazas) queda retenida y visible en cada corrida."}
        </p>
        {!c.permitido_en_despliegue && (
          <p className="texto-3 chico">
            <Icono n="candado" tam={11} /> Este despliegue no permite encenderla: falta la segunda llave <code>PERMITIR_RADICACION_REAL=si</code> en el entorno del contenedor. La pone quien despliega; Vera no puede.
            {c.radicacion_real && " La compuerta figura encendida, pero sin esa llave no escribe nada."}
          </p>
        )}
        {c.cambiado ? <p className="texto-3 chico">Cambiada {hace(c.cambiado)}{c.cambiado_por ? ` · ${c.cambiado_por}` : ""}</p> : null}
        <AnimatePresence>
          {confirmar && (
            <motion.div className="c-confirmar" role="alertdialog" aria-label="Confirmar la radicación real" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
              <div className="c-confirmar-texto">
                <Icono n="alerta" tam={16} />
                <div>
                  <p><b>Vas a encender la radicación real de este flujo.</b> Desde la próxima corrida deja de ser una simulación:</p>
                  <ul className="man-frases">{(CONSECUENCIAS[flujo] ?? ["Escribe en los sistemas reales como el job."]).map((x) => <li key={x}>{x}</li>)}</ul>
                  <p>Lo que se radique o se mueva no se deshace desde aquí. Si el job de producción también está corriendo, los dos pueden tomar los mismos correos.</p>
                </div>
              </div>
              <div className="fila c-acciones">
                <button type="button" className="boton" autoFocus onClick={() => setConfirmar(false)}>Cancelar</button>
                <button type="button" className="boton peligro-solido" disabled={ocupado} onClick={() => void cambiar(true)}>{ocupado && <span className="girando" />}Sí, radicar de verdad</button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </section>
  );
}

function ProgramacionDelJob({ flujo }: { flujo: string }) {
  const avisar = usar((s) => s.avisar);
  const [p, setP] = useState<Programacion | null | undefined>(undefined);
  const [borrador, setBorrador] = useState<Programacion | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const cargar = useCallback(async () => {
    try {
      const r = await pedir<Programacion>(`/api/flujos/${encodeURIComponent(flujo)}/programacion`);
      setP(r);
      setBorrador(r);
    } catch {
      setP(null);
    }
  }, [flujo]);
  useEffect(() => void cargar(), [cargar]);

  const guardar = async (cambios: Partial<Programacion>) => {
    setOcupado(true);
    try {
      await pedir(`/api/flujos/${encodeURIComponent(flujo)}/programacion`, { method: "PUT", body: JSON.stringify(cambios) });
      avisar("Programación guardada");
      await cargar();
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setOcupado(false);
    }
  };

  if (p === null) return <div className="vacio"><b>No se pudo leer la programación.</b><button className="boton chico" onClick={() => void cargar()}>Volver a intentar</button></div>;
  if (p === undefined || !borrador) return <div className="vacio"><span className="girando" />Leyendo la programación…</div>;
  const editado = borrador.cron !== p.cron || borrador.n !== p.n;
  const delJob = p.del_job && p.cron === p.del_job.cron && p.n === p.del_job.n && p.zona === p.del_job.zona;
  return (
    <section className="seccion">
      <h3>Programación como el job</h3>
      <div className="ent-caja pila">
        <div className="ent-principal">
          <Icono n="calendario" tam={16} />
          <b className="crece">{p.activo ? `Encendida: corre ${describirCron(p.cron)}` : "Apagada"}</b>
          {delJob && <span className="ficha tono-gris">igual al Cloud Scheduler</span>}
          <Interruptor valor={p.activo} etiqueta="Programación encendida" deshabilitado={ocupado} alCambiar={(v) => void guardar({ activo: v })} />
        </div>
        <p className="texto-2 chico">
          En cada toque toma del buzón real (solo lectura) los siguientes {p.n} correos en el orden del job y los corre; nunca dos corridas a la vez, y cada una tiene un tope de {p.tope_por_corrida_s ?? 3600} s. Con la compuerta apagada, cada corrida es una simulación.
        </p>
        <CampoCron valor={borrador.cron} alCambiar={(cron) => setBorrador({ ...borrador, cron })} />
        <div className="fila envuelve">
          <span className="texto-2 chico">Correos por corrida</span>
          <CampoNumero valor={borrador.n} min={1} max={150} alCambiar={(n) => setBorrador({ ...borrador, n: n ?? p.n })} />
          <span className="texto-3 chico">Zona {p.zona}</span>
          <span className="espacio" />
          {p.del_job && !delJob && <button type="button" className="boton chico" disabled={ocupado} onClick={() => void guardar({ cron: p.del_job!.cron, zona: p.del_job!.zona, n: p.del_job!.n })}>Volver a la del job</button>}
          {editado && <button type="button" className="boton chico primario" disabled={ocupado} onClick={() => void guardar({ cron: borrador.cron, n: borrador.n })}>{ocupado ? <span className="girando" /> : <Icono n="guardar" tam={13} />}Guardar</button>}
        </div>
        {(p.en_curso || p.ultima_ejecucion) && (
          <p className="texto-3 chico">
            {p.en_curso ? `Corrida programada en curso desde ${hace(p.en_curso.desde)} (${p.en_curso.corridas} correo${p.en_curso.corridas === 1 ? "" : "s"}). ` : ""}
            {p.ultima_ejecucion ? `Último toque ${hace(p.ultima_ejecucion)}.` : ""}
          </p>
        )}
      </div>
    </section>
  );
}

function Ejecucion({ flujo }: { flujo: string }) {
  return (
    <div className="pila">
      <Compuerta flujo={flujo} />
      <ProgramacionDelJob flujo={flujo} />
    </div>
  );
}

// ---------------------------------------------------------------- panel

export function PanelEntorno({ abierto, seccion: pedida, cambiarSeccion, cerrar }: { abierto: boolean; seccion: SeccionEntorno; cambiarSeccion: (s: SeccionEntorno) => void; cerrar: () => void }) {
  const tipoActual = usar((s) => s.flujos.find((f) => f.id === s.flujo)?.tipo);
  // «Ejecución» es solo de los flujos de código: en un declarativo se muestran las variables.
  const seccion: SeccionEntorno = pedida === "ejecucion" && tipoActual !== "codigo" ? "variables" : pedida;
  const flujo = usar((s) => s.flujo);
  const nombre = usar((s) => s.flujos.find((f) => f.id === s.flujo)?.nombre ?? s.flujo);
  const tipo = usar((s) => s.flujos.find((f) => f.id === s.flujo)?.tipo);
  return (
    <Dialogo abierto={abierto} cerrar={cerrar} titulo={`Entorno de «${nombre}»`} sub={tipo === "codigo" ? "Flujo de código: estas variables y conexiones son solo suyas." : "Variables, secretos y conexiones solo de este flujo."} ancho={760}>
      <nav className="pestanas ent-pestanas" role="tablist" aria-label="Secciones del entorno">
        {(tipo === "codigo" ? (["variables", "conexiones", "ejecucion", "manifiesto"] as const) : (["variables", "conexiones", "manifiesto"] as const)).map((s) => (
          <button key={s} role="tab" aria-selected={seccion === s} className={seccion === s ? "activa" : ""} onClick={() => cambiarSeccion(s)}>
            {s === "variables" ? "Variables" : s === "conexiones" ? "Conexiones" : s === "ejecucion" ? "Ejecución" : "Manifiesto"}
            {seccion === s && <motion.i layoutId="pestana-entorno" className="pestana-linea" />}
          </button>
        ))}
      </nav>
      <div className="ent-cuerpo">
        {seccion === "variables" && <Variables flujo={flujo} />}
        {seccion === "conexiones" && <ConexionesDelFlujo flujo={flujo} />}
        {seccion === "ejecucion" && tipo === "codigo" && <Ejecucion flujo={flujo} />}
        {seccion === "manifiesto" && <Manifiesto flujo={flujo} />}
      </div>
    </Dialogo>
  );
}
