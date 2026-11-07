import { useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";
import { usar } from "../estado.ts";
import { Icono, type NombreIcono } from "../iconos.tsx";
import { usarEditor } from "../editor/almacen.ts";
import { resultadosDe, usarCorridaVisible } from "../editor/corrida.ts";
import { RAMA_ERROR, asaDeArista, asasDe, configInicial, esDefecto, resumenConfig, textoJson, tieneRamaError, variablesDisponibles, visualDe, type Esquema, type NodoDef, type TipoNodo } from "../editor/modelo.ts";
import { segundos } from "../progreso.ts";
import { Codigo, Deslizador, EditorJson, Interruptor } from "./Editores.tsx";
import { Campo, CampoNumero, CamposObjeto, CampoPlantilla, type Contexto } from "./Formulario.tsx";
import { Selector } from "./Selector.tsx";
import { UrlGancho, urlDeGancho } from "./Dialogos.tsx";
import { OpcionesIaNodo } from "./IA.tsx";

/**
 * El panel de un nodo de un flujo declarativo: su configuración (un
 * formulario armado con el esquema del tipo), las opciones comunes a todo
 * nodo, la ayuda con ejemplos y lo que produjo en la última corrida.
 */

type Seccion = "configurar" | "opciones" | "ayuda" | "datos";
const SECCIONES: Seccion[] = ["configurar", "opciones", "ayuda", "datos"];
const COMUNES = ["deshabilitado", "continuar_si_falla", "reintentos", "entradas", "salida", "proveedor", "modelo", "credencial", "temperatura"];

function Pestanas({ valor, cambiar, opciones }: { valor: Seccion; cambiar: (s: Seccion) => void; opciones: { id: Seccion; nombre: string; n?: number; alerta?: boolean }[] }) {
  return (
    <nav className="pestanas" role="tablist" aria-label="Secciones del nodo">
      {opciones.map((o) => (
        <button key={o.id} role="tab" aria-selected={valor === o.id} className={valor === o.id ? "activa" : ""} onClick={() => cambiar(o.id)}>
          {o.nombre}
          {o.n !== undefined && <span className={`contador ${o.alerta ? "alerta" : ""}`}>{o.n}</span>}
          {valor === o.id && <motion.i layoutId="pestana-panel-nodo" className="pestana-linea" />}
        </button>
      ))}
    </nav>
  );
}

function FilaOpcion({ titulo, ayuda, children }: { titulo: string; ayuda?: string; children: React.ReactNode }) {
  return (
    <div className="f-campo f-interruptor">
      <div className="crece">
        <span className="f-etiqueta"><span>{titulo}</span></span>
        {ayuda && <p className="f-ayuda">{ayuda}</p>}
      </div>
      {children}
    </div>
  );
}

function Opciones({ n, tipo, poner, ctx }: { n: NodoDef; tipo: TipoNodo | undefined; poner: (clave: string, v: unknown, grupo?: string) => void; ctx: Contexto }) {
  const modelos = usar((s) => s.modelos);
  const def = usarEditor((s) => s.definicion)!;
  const c = n.config;
  const props = tipo?.config?.properties ?? {};
  // Lo común que el esquema marca con x-comun se edita aquí; lo propio del tipo, en «Configurar».
  const enEsquema = new Set(Object.keys(props).filter((k) => !props[k]?.["x-comun"]));
  const conEsquema = Object.keys(props).length > 0;
  const permite = (k: string, heuristica: boolean) => (conEsquema && Object.values(props).some((p) => p["x-comun"]) ? Boolean(props[k]?.["x-comun"]) : heuristica);
  const esDisparador = Boolean(tipo?.disparador);
  const esSalida = n.tipo === "salida" || (Array.isArray(tipo?.salidas) && tipo.salidas.length === 0);
  const esIA = (tipo?.categoria ?? "") === "ia" || n.tipo.startsWith("ia_") || n.tipo === "resumir";
  const conReintentos = esIA || (tipo?.categoria ?? "") === "integracion" || ["http", "enviar", "notificar"].includes(n.tipo);
  const rt = (c.reintentos ?? {}) as { intentos?: number; espera_ms?: number; backoff?: number; maximo_ms?: number };
  const conErrorRama = def.aristas.some((a) => a.desde === n.id && a.cuando?.trim().toLowerCase() === RAMA_ERROR);
  const ponerRt = (k: string, v: number | undefined) => {
    const sig = { ...rt, [k]: v };
    if (v === undefined) delete (sig as Record<string, unknown>)[k];
    poner("reintentos", sig.intentos && sig.intentos > 1 ? sig : Object.keys(sig).length > 1 ? { intentos: 1, ...sig } : undefined, "reintentos");
  };
  return (
    <div className="pila">
      {permite("deshabilitado", !esDisparador && !esSalida) && !enEsquema.has("deshabilitado") && (
        <FilaOpcion titulo="Deshabilitado" ayuda="No se ejecuta: el flujo pasa de largo por su salida por defecto y lo que iba a dejar queda vacío.">
          <Interruptor valor={c.deshabilitado === true} alCambiar={(v) => poner("deshabilitado", v || undefined)} />
        </FilaOpcion>
      )}
      {permite("continuar_si_falla", !esDisparador && !esSalida) && !enEsquema.has("continuar_si_falla") && (
        <FilaOpcion titulo="Continuar si falla" ayuda={conErrorRama ? "Tiene una rama «si falla» conectada: esa rama manda sobre esta opción." : "Si falla, sigue por su salida normal y anota el fallo como aviso (queda en {{fallo.motivo}})."}>
          <Interruptor valor={c.continuar_si_falla === true} alCambiar={(v) => poner("continuar_si_falla", v || undefined)} />
        </FilaOpcion>
      )}
      {permite("reintentos", conReintentos) && !enEsquema.has("reintentos") && (
        <fieldset className="f-grupo">
          <legend>Reintentos</legend>
          <p className="f-ayuda">Ante fallos pasajeros (límite de cuota, error del servidor, tiempo agotado) vuelve a intentar, esperando cada vez un poco más.</p>
          <div className="f-rejilla">
            <div className="f-campo"><span className="f-etiqueta"><span>Intentos en total</span></span><CampoNumero valor={rt.intentos ?? 1} min={1} max={5} alCambiar={(v) => ponerRt("intentos", v)} /></div>
            <div className="f-campo"><span className="f-etiqueta"><span>Espera inicial (ms)</span></span><CampoNumero valor={rt.espera_ms} min={0} max={60000} paso={100} placeholder="500" alCambiar={(v) => ponerRt("espera_ms", v)} /></div>
            <div className="f-campo"><span className="f-etiqueta"><span>Multiplicador</span></span><CampoNumero valor={rt.backoff} min={1} max={10} paso={0.5} placeholder="2" alCambiar={(v) => ponerRt("backoff", v)} /></div>
            <div className="f-campo"><span className="f-etiqueta"><span>Espera máxima (ms)</span></span><CampoNumero valor={rt.maximo_ms} min={0} max={120000} paso={500} placeholder="10000" alCambiar={(v) => ponerRt("maximo_ms", v)} /></div>
          </div>
        </fieldset>
      )}
      {permite("entradas", !esDisparador) && !enEsquema.has("entradas") && (
        <div className="f-campo">
          <span className="f-etiqueta"><span>Recibe</span></span>
          <div className="f-chips">
            {(Array.isArray(c.entradas) ? (c.entradas as string[]) : []).map((x, i) => (
              <span key={`${x}-${i}`} className="ficha editable">{x}<button type="button" aria-label={`Quitar ${x}`} onClick={() => poner("entradas", (c.entradas as string[]).filter((_, j) => j !== i).length ? (c.entradas as string[]).filter((_, j) => j !== i) : undefined)}><Icono n="cerrar" tam={11} /></button></span>
            ))}
            <Selector
              etiqueta="Agregar una variable que recibe"
              tamano="chico"
              valor={null}
              placeholder={ctx.variables.length ? "Agregar…" : "Sin variables antes"}
              deshabilitado={!ctx.variables.length}
              opciones={ctx.variables.filter((v) => !(Array.isArray(c.entradas) && (c.entradas as string[]).includes(v.nombre))).map((v) => ({ valor: v.nombre, etiqueta: v.nombre, detalle: v.origen }))}
              alCambiar={(v) => poner("entradas", [...(Array.isArray(c.entradas) ? (c.entradas as string[]) : []), v])}
            />
          </div>
          <p className="f-ayuda">{esIA ? "Si lo dejas vacío, la IA recibe el correo, los adjuntos y todo lo obtenido hasta aquí." : "Qué datos usa este nodo; vacío es todo lo disponible."}</p>
        </div>
      )}
      {permite("salida", !esSalida && !esDisparador) && !enEsquema.has("salida") && (
        <div className="f-campo">
          <label className="f-etiqueta" htmlFor="op-salida"><span>Deja su resultado en</span></label>
          <input id="op-salida" className="campo mono" value={typeof c.salida === "string" ? c.salida : ""} placeholder={n.id} onChange={(e) => poner("salida", e.target.value || undefined, "salida")} />
          <p className="f-ayuda">El nombre de la variable que los nodos siguientes leen como {`{{${typeof c.salida === "string" && c.salida ? c.salida : n.id}}}`}.</p>
        </div>
      )}
      {(esIA || n.tipo === "leer_adjuntos") && permite("proveedor", true) && <OpcionesIaNodo n={n} poner={poner} />}
      {esIA && (
        <>
          {!permite("proveedor", true) && permite("modelo", true) && !enEsquema.has("modelo") && (
            <div className="f-campo">
              <span className="f-etiqueta"><span>Modelo</span></span>
              <Selector etiqueta="Modelo" valor={typeof c.modelo === "string" ? c.modelo : ""} opciones={[{ valor: "", etiqueta: `El del flujo (${def.modelo ?? "gemini-2.5-flash"})` }, ...modelos.map((m) => ({ valor: m, etiqueta: m }))]} alCambiar={(v) => poner("modelo", v || undefined)} />
            </div>
          )}
          {permite("temperatura", true) && !enEsquema.has("temperatura") && (
            <div className="f-campo">
              <span className="f-etiqueta"><span>Creatividad (temperatura)</span>{c.temperatura !== undefined && <button type="button" className="enlace" onClick={() => poner("temperatura", undefined)}>Usar la del flujo</button>}</span>
              <Deslizador valor={typeof c.temperatura === "number" ? c.temperatura : 0.1} min={0} max={2} paso={0.05} alCambiar={(v) => poner("temperatura", v, "temperatura")} />
              <p className="f-ayuda">0,1 para clasificar y extraer; 0,4 a 0,7 para redactar.</p>
            </div>
          )}
        </>
      )}
      {esDisparador && <p className="texto-2 chico">Un disparador arranca el flujo: no se puede deshabilitar.</p>}
      {Object.entries(props).filter(([k, p]) => p["x-comun"] && !COMUNES.includes(k)).map(([k, p]) => (
        <Campo key={k} clave={k} esquema={p} valor={c[k]} ctx={ctx} ruta={`${n.id}.${k}`} alCambiar={(v, g) => poner(k, v, g)} />
      ))}
    </div>
  );
}

function Salidas({ n, tipo }: { n: NodoDef; tipo: TipoNodo | undefined }) {
  const def = usarEditor((s) => s.definicion)!;
  const centrar = usar((s) => s.centrar);
  const enfocar = usar((s) => s.enfocar);
  const asas = asasDe(tipo, n.config);
  const filas = [...asas.map((a) => ({ ...a, error: false })), ...(!tieneRamaError(tipo) ? [] : [{ id: RAMA_ERROR, etiqueta: "si falla", defecto: false, error: true }])];
  const entran = def.aristas.filter((a) => a.hacia === n.id);
  return (
    <section className="seccion">
      <h3>Conexiones</h3>
      <div className="pn-conexiones">
        {entran.map((a, i) => {
          const o = def.nodos.find((x) => x.id === a.desde);
          return (
            <button key={`e${i}`} className="conexion" onClick={() => { centrar(a.desde); enfocar({ tipo: "nodo", id: a.desde }); }}>
              <Icono n="atras" tam={13} />
              <span className="crece"><b>{o?.nombre ?? a.desde}</b><span className="texto-3 diminuto">llega por «{esDefecto(a.cuando) ? "por defecto" : a.cuando === RAMA_ERROR ? "si falla" : a.cuando}»</span></span>
            </button>
          );
        })}
        {filas.map((f) => {
          const a = def.aristas.find((x) => x.desde === n.id && asaDeArista(x, asas) === f.id);
          const d = a ? def.nodos.find((x) => x.id === a.hacia) : undefined;
          return (
            <div key={f.id} className={`pn-salida ${f.error ? "error" : ""}`}>
              <span className="pn-salida-nombre">{f.etiqueta}</span>
              <Icono n="flecha" tam={12} />
              {d ? (
                <button className="enlace" onClick={() => { centrar(d.id); enfocar({ tipo: "nodo", id: d.id }); }}>{d.nombre}</button>
              ) : (
                <span className="texto-3 chico">{f.error ? "sin rama: el flujo se detiene con error" : "sin conectar"}</span>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Ayuda({ n, tipo, usarEjemplo }: { n: NodoDef; tipo: TipoNodo | undefined; usarEjemplo: (e: Record<string, unknown>) => void }) {
  const [aplicado, setAplicado] = useState<number | null>(null);
  const deshacer = usarEditor((s) => s.deshacer);
  if (!tipo) return <div className="vacio">La ayuda de este tipo aún no llega del servidor.</div>;
  return (
    <div className="pila">
      <p className="texto-2 pn-desc">{tipo.descripcion}</p>
      <div className="fichas">
        {tipo.disparador && <span className="ficha tono-ambar">Disparador</span>}
        {tipo.escritura && <span className="ficha tono-rojo" title="Si la credencial no tiene permiso de escritura, se simula">Escribe en otro sistema</span>}
        {Array.isArray(tipo.salidas) ? tipo.salidas.length ? <span className="ficha">Sale por {tipo.salidas.join(", ")}</span> : <span className="ficha">Termina el flujo</span> : <span className="ficha">Una salida por cada opción que configures</span>}
      </div>
      {tipo.consejos.length > 0 && (
        <section className="seccion">
          <h3>Consejos</h3>
          <ul className="pn-consejos">{tipo.consejos.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </section>
      )}
      {tipo.ejemplos.length > 0 && (
        <section className="seccion">
          <h3>Ejemplos <span>{tipo.ejemplos.length}</span></h3>
          {tipo.ejemplos.map((e, i) => {
            const conf = e && typeof e === "object" && "config" in e && typeof e.config === "object" ? (e.config as Record<string, unknown>) : e;
            const titulo = typeof e.titulo === "string" ? e.titulo : typeof e.nombre === "string" ? e.nombre : `Ejemplo ${i + 1}`;
            return (
              <div key={i} className="pn-ejemplo">
                <div className="fila">
                  <b className="crece">{titulo}</b>
                  {aplicado === i ? (
                    <span className="fila chico"><Icono n="ok" tam={12} />Aplicado<button className="enlace" onClick={() => { deshacer(); setAplicado(null); }}>Deshacer</button></span>
                  ) : (
                    <button className="boton chico" onClick={() => { usarEjemplo(conf); setAplicado(i); }}>Usar este ejemplo</button>
                  )}
                </div>
                <Codigo valor={textoJson(conf)} lenguaje="json" soloLectura minAlto="40px" />
              </div>
            );
          })}
        </section>
      )}
      {!tipo.consejos.length && !tipo.ejemplos.length && <p className="texto-3 chico">Este tipo no trae consejos ni ejemplos todavía.</p>}
      <p className="texto-3 diminuto mono">tipo {n.tipo}</p>
    </div>
  );
}

function Datos({ n }: { n: NodoDef }) {
  const corrida = usarCorridaVisible();
  const r = useMemo(() => resultadosDe(corrida)[n.id], [corrida, n.id]);
  if (!corrida) return <div className="vacio grande"><Icono n="datos" tam={26} /><span>Corre el flujo para ver aquí lo que este nodo recibió y dejó.</span></div>;
  if (!r) return <div className="vacio">En la corrida {corrida.caso} el flujo no pasó por este nodo.</div>;
  return (
    <div className="pila">
      <div className="fichas">
        <span className={`pastilla ${r.estado === "fallo" ? "rojo" : r.estado === "activo" ? "azul" : r.estado === "omitido" ? "" : "verde"}`}>
          {r.estado === "activo" ? <span className="girando" /> : <Icono n={r.estado === "fallo" ? "cerrar" : r.estado === "omitido" ? "pausa" : "ok"} tam={12} />}
          {r.estado === "activo" ? "En curso" : r.estado === "fallo" ? "Falló" : r.estado === "omitido" ? "Omitido" : "Terminó"}
          {r.ms ? ` · ${segundos(r.ms)}` : ""}
        </span>
        {r.salida && <span className="pastilla azul">salió por «{r.salida === RAMA_ERROR ? "si falla" : r.salida}»</span>}
        {r.veces > 1 && <span className="pastilla">{r.veces} veces</span>}
      </div>
      {r.fallo && <p className="pn-fallo"><Icono n="alerta" tam={14} />{r.fallo}{r.continua ? " (siguió por su salida normal)" : ""}</p>}
      {r.muestra !== undefined && r.muestra !== null && (typeof r.muestra !== "object" || Object.keys(r.muestra as object).length > 0) ? (
        <section className="seccion">
          <h3>Lo que dejó</h3>
          <Codigo valor={textoJson(r.muestra)} lenguaje="json" soloLectura minAlto="80px" />
          <p className="texto-3 diminuto">Una muestra recortada: los textos largos y las listas grandes vienen abreviados.</p>
        </section>
      ) : (
        <p className="texto-3 chico">No dejó datos para mostrar.</p>
      )}
    </div>
  );
}

export function PanelNodo({ id }: { id: string }) {
  const def = usarEditor((s) => s.definicion);
  const tipos = usarEditor((s) => s.tipos);
  const cambiar = usarEditor((s) => s.cambiar);
  const problemas = usarEditor((s) => s.problemas);
  const seccionPedida = usarEditor((s) => s.seccionPedida);
  const enfocar = usar((s) => s.enfocar);
  const centrar = usar((s) => s.centrar);
  const flujoId = usarEditor((s) => s.flujo);
  const corrida = usarCorridaVisible();
  const [seccion, setSeccion] = useState<Seccion>("configurar");
  const mapa = useMemo(() => new Map(tipos.map((t) => [t.tipo, t])), [tipos]);
  const n = def?.nodos.find((x) => x.id === id);
  const tipo = n ? mapa.get(n.tipo) : undefined;
  const vis = visualDe(tipo?.disparador ? "disparador" : tipo?.categoria);
  const entorno = usarEditor((s) => s.entorno);
  const ctx: Contexto = useMemo(
    () => ({
      variables: [
        ...(def && n ? variablesDisponibles(def, mapa, n.id) : []),
        ...entorno.map((v) => ({ nombre: `entorno.${v.nombre}`, origen: "Entorno del flujo", pista: v.secreto ? "secreto: se usa sin mostrarse" : v.descripcion })),
      ],
    }),
    [def, mapa, n, entorno],
  );
  const propios = problemas.filter((p) => p.nodo === id);
  const r = useMemo(() => resultadosDe(corrida)[id], [corrida, id]);

  useEffect(() => {
    if (seccionPedida && SECCIONES.includes(seccionPedida.seccion as Seccion)) setSeccion(seccionPedida.seccion as Seccion);
  }, [seccionPedida]);

  if (!def || !n) return null;

  const ponerNodo = (f: (x: NodoDef) => NodoDef, grupo?: string) => cambiar((d) => ({ ...d, nodos: d.nodos.map((x) => (x.id === id ? f(x) : x)) }), grupo ? `${id}:${grupo}` : undefined);
  const ponerConfig = (v: unknown, grupo?: string) => ponerNodo((x) => ({ ...x, config: v as Record<string, unknown> }), grupo);
  const poner = (clave: string, v: unknown, grupo?: string) =>
    ponerNodo((x) => {
      const config = { ...x.config };
      if (v === undefined) delete config[clave];
      else config[clave] = v;
      return { ...x, config };
    }, grupo);
  const usarEjemplo = (e: Record<string, unknown>) => {
    const comunes = Object.fromEntries(Object.entries(n.config).filter(([k]) => COMUNES.includes(k) && !(k in e)));
    ponerNodo((x) => ({ ...x, config: { ...comunes, ...structuredClone(e) } }));
    setSeccion("configurar");
  };
  const esquema: Esquema | undefined = tipo?.config;
  const comunesDelEsquema = Object.entries(esquema?.properties ?? {}).filter(([, p]) => Boolean(p["x-comun"])).map(([k]) => k);
  const hayCampos = Object.keys(esquema?.properties ?? {}).some((k) => !comunesDelEsquema.includes(k));
  const extras = Object.keys(n.config).filter((k) => !(k in (esquema?.properties ?? {})) && !COMUNES.includes(k));

  return (
    <div className="inspector panel-nodo">
      <header className="inspector-cabeza">
        <div className="fila arriba">
          <span className={`icono-grande t-${vis.tono}`}><Icono n={vis.icono as NombreIcono} tam={18} /></span>
          <div className="crece pn-titulos">
            <span className="etiqueta-cat">{tipo?.nombre ?? n.tipo}</span>
            <input className="pn-nombre" value={n.nombre} aria-label="Nombre del nodo" onChange={(e) => ponerNodo((x) => ({ ...x, nombre: e.target.value }), "nombre")} onBlur={(e) => !e.target.value.trim() && ponerNodo((x) => ({ ...x, nombre: tipo?.nombre ?? x.id }))} />
            <input className="pn-desc-campo" value={n.descripcion ?? ""} placeholder="Agrega una nota sobre qué hace este paso" aria-label="Descripción del nodo" onChange={(e) => ponerNodo((x) => ({ ...x, descripcion: e.target.value || undefined }), "descripcion")} />
          </div>
          <button className="boton-mini" aria-label="Enfocar en el lienzo" title="Enfocar en el lienzo" onClick={() => centrar(id)}><Icono n="mira" tam={15} /></button>
          <button className="boton-mini" aria-label="Cerrar" title="Cerrar (Esc)" onClick={() => enfocar(null)}><Icono n="cerrar" tam={15} /></button>
        </div>
        <div className="fila envuelve pn-estado">
          <code className="ficha mono" title="Identificador del nodo">{n.id}</code>
          {n.config.deshabilitado === true && <span className="pastilla">Deshabilitado</span>}
          {r && <span className={`pastilla ${r.estado === "fallo" ? "rojo" : r.estado === "activo" ? "azul" : "verde"}`}>{r.estado === "fallo" ? "Falló en la corrida" : r.estado === "activo" ? "En curso" : `Corrió${r.ms ? ` en ${segundos(r.ms)}` : ""}`}</span>}
          <span className="texto-3 chico pn-resumen">{resumenConfig(n, tipo)}</span>
        </div>
        <Pestanas
          valor={seccion}
          cambiar={setSeccion}
          opciones={[
            { id: "configurar", nombre: "Configurar", ...(propios.length ? { n: propios.length, alerta: true } : {}) },
            { id: "opciones", nombre: "Opciones" },
            { id: "ayuda", nombre: "Ayuda", ...(tipo?.ejemplos.length ? { n: tipo.ejemplos.length } : {}) },
            { id: "datos", nombre: "Datos", ...(r ? { n: 1 } : {}) },
          ]}
        />
      </header>
      <div className="inspector-cuerpo">
        {propios.length > 0 && (
          <div className="pn-problemas" role="alert">
            <Icono n="alerta" tam={15} />
            <ul>{propios.map((p, i) => <li key={i}>{p.texto}</li>)}</ul>
          </div>
        )}
        {seccion === "configurar" && (
          <>
            {n.tipo === "webhook" && flujoId && <UrlGancho url={urlDeGancho(flujoId, n.config)} ayuda="Quien tenga esta dirección puede disparar el flujo: no la compartas fuera de los sistemas que la necesitan." />}
            {!tipo ? (
              <div className="pila">
                <p className="texto-2 chico">El esquema de «{n.tipo}» aún no llega del servidor: puedes editar la configuración como JSON.</p>
                <EditorJson valor={n.config} alCambiar={(v) => v && typeof v === "object" && !Array.isArray(v) && ponerConfig(v, "json")} />
              </div>
            ) : hayCampos ? (
              <CamposObjeto esquema={esquema!} valor={n.config} alCambiar={ponerConfig} ctx={ctx} ruta={id} omitir={comunesDelEsquema} />
            ) : (
              <p className="texto-2 chico">Este nodo no necesita configuración.</p>
            )}
            {tipo && extras.length > 0 && (
              <details className="pn-extras">
                <summary>Otras claves de la configuración ({extras.join(", ")})</summary>
                {extras.map((k) => (
                  <div key={k} className="f-campo">
                    <span className="f-etiqueta"><code>{k}</code><button type="button" className="enlace" onClick={() => poner(k, undefined)}>Quitar</button></span>
                    {typeof n.config[k] === "string" ? <CampoPlantilla valor={n.config[k] as string} variables={ctx.variables} alCambiar={(t) => poner(k, t, k)} /> : <EditorJson valor={n.config[k]} alCambiar={(v) => poner(k, v, k)} />}
                  </div>
                ))}
              </details>
            )}
            <Salidas n={n} tipo={tipo} />
          </>
        )}
        {seccion === "opciones" && <Opciones n={n} tipo={tipo} poner={poner} ctx={ctx} />}
        {seccion === "ayuda" && <Ayuda n={n} tipo={tipo} usarEjemplo={usarEjemplo} />}
        {seccion === "datos" && <Datos n={n} />}
        {seccion === "configurar" && tipo && (
          <button className="enlace pn-reiniciar" onClick={() => ponerConfig(configInicial(tipo))}>Volver a la configuración inicial del tipo</button>
        )}
      </div>
    </div>
  );
}
