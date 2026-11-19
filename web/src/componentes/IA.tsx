import { useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { usarEditor } from "../editor/almacen.ts";
import type { IaDelFlujo, IaElegida, NodoDef } from "../editor/modelo.ts";
import { Interruptor } from "./Editores.tsx";
import { Selector } from "./Selector.tsx";

/**
 * La IA en la web: el selector de proveedor → modelo → conexión con «Probar
 * conexión», la IA del flujo (global o propia), la de cada nodo (hereda o
 * propia) y, en Ajustes, la IA global de la plataforma y la de Vera.
 * Todo sale de /api/ia/* (las llaves nunca llegan aquí).
 */

export type Capacidad = "texto" | "imagenes" | "pdf" | "json_estructurado" | "herramientas" | "voz_en_vivo";
export interface FichaModelo {
  id: string;
  nombre: string;
  contexto?: number | null;
  precio_entrada?: number;
  precio_salida?: number;
  acepta: Partial<Record<Capacidad, boolean | null>>;
  solo_voz?: boolean;
  verificado?: string;
  en_vivo?: boolean;
}
export interface ProveedorIa {
  id: string;
  nombre: string;
  descripcion: string;
  autenticacion: string;
  tipo_credencial: string | null;
  requiere_credencial: boolean;
  configurado: boolean;
  credenciales: { nombre: string; alcance: string }[];
  es_global: boolean;
}
interface RespuestaProveedores { proveedores: ProveedorIa[]; global: IaElegida; precios_fecha: string }
interface RespuestaModelos { modelos: FichaModelo[]; en_vivo: boolean; error_en_vivo?: string; precios_fecha?: string }
interface Prueba { ok: boolean; ms?: number; texto?: string; error?: string; tokens_entrada?: number; tokens_salida?: number; costo_estimado_usd?: number }

const CAPACIDAD: Record<Capacidad, string> = {
  texto: "Texto", imagenes: "Imágenes", pdf: "PDF", json_estructurado: "JSON", herramientas: "Herramientas", voz_en_vivo: "Voz en vivo",
};
const NOMBRE_CORTO: Record<string, string> = { vertex: "Vertex", gemini: "Gemini", openai: "OpenAI", azure_openai: "Azure OpenAI", anthropic: "Claude", deepseek: "DeepSeek", mistral: "Mistral", groq: "Groq", openrouter: "OpenRouter", ollama: "Ollama", openai_compatible: "Compatible", cohere: "Cohere", personalizado: "Personalizado" };
export const nombreProveedor = (id: string) => NOMBRE_CORTO[id] ?? id;
export const textoIa = (x: { proveedor?: string; modelo?: string } | null | undefined) => (x?.proveedor ? `${nombreProveedor(x.proveedor)} · ${x.modelo || "sin modelo"}` : "—");

// ------------------------------------------------------------------ consumo

const dinero = (x: number) => (x < 0.01 ? x.toLocaleString("es-CO", { maximumSignificantDigits: 2 }) : x.toLocaleString("es-CO", { maximumFractionDigits: 2 }));

/** «≈ US$ 0,0012» (estimado con precios aproximados), o "" si no hay costo. */
export const textoCosto = (usd: number | undefined | null) => (usd && usd > 0 ? `≈ US$ ${dinero(usd)}` : "");

/** «1.234 tokens · ≈ US$ 0,0012» (costo estimado con precios aproximados), o "" si no hubo IA. */
export function textoConsumo(c: { tokens_entrada?: number; tokens_salida?: number; costo_estimado_usd?: number } | null | undefined): string {
  if (!c) return "";
  const t = Number(c.tokens_entrada ?? 0) + Number(c.tokens_salida ?? 0);
  if (!t) return "";
  const costo = Number(c.costo_estimado_usd ?? 0);
  return `${t.toLocaleString("es-CO")} tokens${costo > 0 ? ` · ≈ US$ ${dinero(costo)}` : ""}`;
}

/** El consumo del resultado de una corrida (o sus tokens, en las de antes). */
export function consumoDe(r: Record<string, unknown> | null | undefined): { tokens_entrada: number; tokens_salida: number; costo_estimado_usd: number } | null {
  if (!r) return null;
  const c = r.consumo as { tokens_entrada?: number; tokens_salida?: number; costo_estimado_usd?: number } | undefined;
  return {
    tokens_entrada: Number(c?.tokens_entrada ?? r.tokens_entrada ?? 0), tokens_salida: Number(c?.tokens_salida ?? r.tokens_salida ?? 0),
    costo_estimado_usd: Number(c?.costo_estimado_usd ?? 0),
  };
}

/** La pastilla de consumo de una corrida (tokens de entrada y salida, costo estimado). */
export function PastillaConsumo({ r }: { r: Record<string, unknown> | null | undefined }) {
  const c = consumoDe(r);
  const t = textoConsumo(c);
  if (!c || !t) return null;
  return <span className="pastilla diminuto" title={`Entrada ${c.tokens_entrada.toLocaleString("es-CO")} · salida ${c.tokens_salida.toLocaleString("es-CO")} tokens. Costo estimado con precios publicados aproximados.`}><Icono n="chispa" tam={11} />{t}</span>;
}

// ------------------------------------------------------------------ datos

const cacheProveedores = new Map<string, Promise<RespuestaProveedores>>();
export function recargarIa(): void {
  cacheProveedores.clear();
}

function useProveedores(flujo?: string | null): RespuestaProveedores | null {
  const [r, setR] = useState<RespuestaProveedores | null>(null);
  useEffect(() => {
    const k = flujo ?? "";
    if (!cacheProveedores.has(k)) cacheProveedores.set(k, pedir<RespuestaProveedores>(`/api/ia/proveedores${k ? `?flujo=${encodeURIComponent(k)}` : ""}`).catch((e) => { cacheProveedores.delete(k); throw e; }));
    let vivo = true;
    void cacheProveedores.get(k)!.then((x) => vivo && setR(x)).catch(() => undefined);
    return () => { vivo = false; };
  }, [flujo]);
  return r;
}

function useModelos(proveedor: string, credencial: string | undefined, flujo?: string | null): RespuestaModelos | null {
  // La respuesta se guarda con la consulta que la pidió: si cambia la consulta, se muestra «cargando» sin borrar en un efecto.
  const consulta = proveedor ? new URLSearchParams({ proveedor, ...(credencial ? { credencial } : {}), ...(flujo ? { flujo } : {}) }).toString() : "";
  const [r, setR] = useState<{ consulta: string; datos: RespuestaModelos } | null>(null);
  useEffect(() => {
    if (!consulta) return;
    let vivo = true;
    void pedir<RespuestaModelos>(`/api/ia/modelos?${consulta}`)
      .then((datos) => vivo && setR({ consulta, datos }))
      .catch((e: Error) => vivo && setR({ consulta, datos: { modelos: [], en_vivo: false, error_en_vivo: e.message } }));
    return () => { vivo = false; };
  }, [consulta]);
  return r && r.consulta === consulta ? r.datos : null;
}

/** La IA global de la plataforma (para decir qué usa un flujo que hereda). */
export function useIaGlobal(): IaElegida | null {
  const r = useProveedores(null);
  return r?.global ?? null;
}

// ------------------------------------------------------------------ selector

function Capacidades({ f }: { f: FichaModelo }) {
  return (
    <div className="fichas">
      {(Object.keys(CAPACIDAD) as Capacidad[]).filter((k) => k !== "texto").map((k) => {
        const v = f.acepta[k];
        if (v === false) return null;
        return <span key={k} className={`ficha diminuto ${v ? "tono-verde" : ""}`} title={v ? "Lo acepta" : "No se sabe si lo acepta"}>{CAPACIDAD[k]}{v ? "" : " ?"}</span>;
      })}
      {f.precio_entrada !== undefined && <span className="ficha diminuto" title="Precio publicado aproximado, en dólares por millón de tokens (entrada / salida)">≈ US$ {f.precio_entrada} / {f.precio_salida} por millón</span>}
      {f.contexto ? <span className="ficha diminuto">{Math.round(f.contexto / 1000).toLocaleString("es-CO")} k de contexto</span> : null}
      {f.verificado && <span className="ficha diminuto tono-verde" title={`Respondió de verdad en el proyecto el ${f.verificado}`}>Verificado</span>}
    </div>
  );
}

/**
 * Proveedor → modelo (filtrado por lo que tiene que aceptar) → conexión, con «Probar conexión».
 * `voz`: solo modelos de voz en vivo; si no, nunca se ofrecen los que son solo de voz.
 */
export function SelectorIA({ valor, alCambiar, flujo, pide = [], voz = false, sinProbar = false }: {
  valor: IaElegida | null; alCambiar: (v: IaElegida) => void; flujo?: string | null; pide?: Capacidad[]; voz?: boolean; sinProbar?: boolean;
}) {
  const datos = useProveedores(flujo);
  const irA = usar((s) => s.irA);
  const prov = datos?.proveedores.find((p) => p.id === valor?.proveedor);
  const modelos = useModelos(valor?.proveedor ?? "", valor?.credencial, flujo);
  const [prueba, setPrueba] = useState<Prueba | null>(null);
  const [probando, setProbando] = useState(false);
  const [otro, setOtro] = useState(false);
  useEffect(() => setPrueba(null), [valor?.proveedor, valor?.modelo, valor?.credencial]);

  const requeridas: Capacidad[] = voz ? ["voz_en_vivo", ...pide] : pide;
  const lista = useMemo(() => (modelos?.modelos ?? []).filter((m) => (voz ? m.acepta.voz_en_vivo !== false : !m.solo_voz) && requeridas.every((c) => m.acepta[c] !== false)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [modelos, voz, requeridas.join(",")]);
  const ficha = (modelos?.modelos ?? []).find((m) => m.id === valor?.modelo);
  const fuera = ficha && !lista.some((m) => m.id === ficha.id);
  const enLista = lista.some((m) => m.id === valor?.modelo);

  if (!datos) return <div className="vacio chico"><span className="girando" />Cargando proveedores…</div>;
  const elegirProveedor = (id: string) => {
    const p = datos.proveedores.find((x) => x.id === id);
    const cred = p?.credenciales[0]?.nombre;
    alCambiar({ proveedor: id, modelo: "", ...(p?.tipo_credencial && cred ? { credencial: cred } : {}) });
    setOtro(false);
  };
  const probar = async () => {
    if (!valor?.proveedor || !valor.modelo) return;
    setProbando(true);
    try {
      setPrueba(await pedir<Prueba>("/api/ia/probar", { method: "POST", body: JSON.stringify({ proveedor: valor.proveedor, modelo: valor.modelo, ...(valor.credencial ? { credencial: valor.credencial } : {}), ...(flujo ? { flujo } : {}) }) }));
    } catch (e) {
      setPrueba({ ok: false, error: (e as Error).message });
    } finally {
      setProbando(false);
    }
  };

  return (
    <div className="pila ia-selector">
      <div className="f-campo">
        <span className="f-etiqueta"><span>Proveedor</span></span>
        <Selector
          etiqueta="Proveedor de IA"
          valor={valor?.proveedor ?? null}
          placeholder="Elegir proveedor…"
          opciones={datos.proveedores.map((p) => ({ valor: p.id, etiqueta: p.nombre, texto: p.nombre, detalle: p.requiere_credencial ? (p.configurado ? `${p.credenciales.length} conexión${p.credenciales.length === 1 ? "" : "es"}` : "sin conexión todavía") : "sin llave: identidad del servicio" }))}
          alCambiar={elegirProveedor}
        />
        {prov && <p className="f-ayuda">{prov.descripcion} · {prov.autenticacion}</p>}
      </div>
      {prov?.tipo_credencial && (
        <div className="f-campo">
          <span className="f-etiqueta"><span>Conexión con la llave</span></span>
          {prov.credenciales.length ? (
            <Selector
              etiqueta="Conexión del proveedor"
              valor={valor?.credencial ?? null}
              placeholder="Elegir conexión…"
              opciones={prov.credenciales.map((c) => ({ valor: c.nombre, etiqueta: c.nombre, detalle: c.alcance === "global" ? "global" : `de ${c.alcance}` }))}
              alCambiar={(c) => alCambiar({ ...valor!, credencial: c })}
            />
          ) : (
            <p className="f-ayuda">No hay conexiones de tipo <code>{prov.tipo_credencial}</code>{flujo ? " que este flujo pueda usar" : " globales"}. Una persona pone la llave en <button type="button" className="enlace" onClick={() => irA("conexiones")}>Conexiones</button>; nunca se escribe en el flujo.</p>
          )}
        </div>
      )}
      {prov && (
        <div className="f-campo">
          <span className="f-etiqueta">
            <span>Modelo</span>
            <button type="button" className="enlace" onClick={() => setOtro(!otro)}>{otro ? "Elegir de la lista" : "Escribir otro"}</button>
          </span>
          {otro || (!lista.length && modelos) ? (
            <input className="campo mono" value={valor?.modelo ?? ""} placeholder={prov.id === "azure_openai" ? "nombre del despliegue" : "id del modelo"} aria-label="Modelo" onChange={(e) => alCambiar({ ...valor!, modelo: e.target.value.trim() })} />
          ) : (
            <Selector
              etiqueta="Modelo de IA"
              valor={enLista ? valor?.modelo ?? null : null}
              placeholder={modelos ? (valor?.modelo ? valor.modelo : "Elegir modelo…") : "Cargando modelos…"}
              deshabilitado={!modelos}
              ancho={340}
              opciones={lista.map((m) => ({
                valor: m.id, etiqueta: m.nombre, texto: m.nombre,
                detalle: [m.id !== m.nombre ? m.id : "", m.precio_entrada !== undefined ? `≈ US$ ${m.precio_entrada} / ${m.precio_salida} por millón` : "", m.acepta.imagenes ? "imágenes" : "", m.acepta.herramientas ? "herramientas" : ""].filter(Boolean).join(" · "),
              }))}
              alCambiar={(m) => alCambiar({ ...valor!, modelo: m })}
            />
          )}
          {modelos?.error_en_vivo && <p className="f-ayuda">No se pudo traer la lista en vivo: {modelos.error_en_vivo}. Se muestra el catálogo.</p>}
          {modelos?.en_vivo && <p className="f-ayuda">Lista leída de la API del proveedor (se guarda una hora).</p>}
          {ficha && <Capacidades f={ficha} />}
          {fuera && <p className="pn-fallo"><Icono n="alerta" tam={13} />Este modelo no acepta lo que se necesita aquí ({requeridas.map((c) => CAPACIDAD[c]).join(", ")}).</p>}
          {requeridas.length > 0 && <p className="f-ayuda">Solo se muestran modelos que aceptan: {requeridas.map((c) => CAPACIDAD[c].toLowerCase()).join(", ")}.</p>}
        </div>
      )}
      {!sinProbar && prov && (
        <div className="fila envuelve">
          <button type="button" className="boton chico" disabled={!valor?.modelo || probando || (Boolean(prov.tipo_credencial) && !valor?.credencial)} onClick={() => void probar()}>
            {probando ? <span className="girando" /> : <Icono n="rayo" tam={13} />}Probar conexión
          </button>
          {prueba && (prueba.ok
            ? <span className="pastilla verde"><Icono n="ok" tam={12} />Responde en {prueba.ms?.toLocaleString("es-CO")} ms{prueba.costo_estimado_usd ? ` · ≈ US$ ${dinero(prueba.costo_estimado_usd)}` : ""}</span>
            : <span className="pastilla rojo" title={prueba.error}><Icono n="alerta" tam={12} />{(prueba.error ?? "falló").slice(0, 140)}</span>)}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ flujo

/** «IA del flujo»: usa la global o una propia. Los flujos de antes (sin `ia`) siguen en Vertex con su modelo. */
export function PanelIaFlujo({ cerrar }: { cerrar: () => void }) {
  const def = usarEditor((s) => s.definicion);
  const cambiar = usarEditor((s) => s.cambiar);
  const flujo = usarEditor((s) => s.flujo);
  const global = useIaGlobal();
  if (!def) return null;
  const ia = def.ia;
  const usaGlobal = ia !== undefined && "usar" in ia;
  const propia = ia && !("usar" in ia) ? (ia as IaElegida) : null;
  const poner = (v: Definicion["ia"] | undefined) => cambiar((d) => {
    const sig = { ...d };
    if (v === undefined) delete sig.ia;
    else sig.ia = v;
    return sig;
  }, "ia");
  return (
    <motion.div className="en-pop ia-pop" role="dialog" aria-label="IA del flujo" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}>
      <header className="fila"><b className="crece">IA del flujo</b><button className="boton-mini" aria-label="Cerrar" onClick={cerrar}><Icono n="cerrar" tam={13} /></button></header>
      <div className="f-campo f-interruptor">
        <div className="crece">
          <span className="f-etiqueta"><span>Usa la IA global</span></span>
          <p className="f-ayuda">{global ? `La de la plataforma: ${textoIa(global)}. Se cambia en Ajustes.` : "La de la plataforma (se cambia en Ajustes)."}</p>
        </div>
        <Interruptor valor={usaGlobal} alCambiar={(v) => poner(v ? { usar: "global" } : { proveedor: global?.proveedor ?? "vertex", modelo: global?.modelo ?? def.modelo ?? "gemini-2.5-flash", ...(global?.credencial ? { credencial: global.credencial } : {}) })} />
      </div>
      {ia === undefined && (
        <p className="texto-2 chico">Flujo de antes: usa <b>Vertex · {def.modelo ?? "gemini-2.5-flash"}</b>. Enciende «Usa la IA global» o elige una propia abajo.</p>
      )}
      {!usaGlobal && (
        <>
          <p className="texto-2 chico"><b>IA propia de este flujo</b>: no cambia aunque cambie la global. Cada nodo de IA puede elegir otra en sus opciones.</p>
          <SelectorIA valor={propia ?? (ia === undefined ? { proveedor: "vertex", modelo: def.modelo ?? "gemini-2.5-flash" } : null)} flujo={flujo} alCambiar={(v) => poner({ ...v, ...(propia?.temperatura !== undefined ? { temperatura: propia.temperatura } : {}) })} />
        </>
      )}
      <p className="texto-3 diminuto">Se guarda con el flujo («Guardar»). Las llaves nunca quedan en el flujo: solo el nombre de la conexión.</p>
    </motion.div>
  );
}

type Definicion = { ia?: IaDelFlujo };

/** Lo que dice el botón de la barra: «IA global», «Claude · …», «Vertex · …». */
export function rotuloIaFlujo(ia: Definicion["ia"] | undefined, modelo: string | undefined): string {
  if (!ia) return `Vertex · ${modelo ?? "gemini-2.5-flash"}`;
  if ("usar" in ia) return "IA global";
  return textoIa(ia);
}

// ------------------------------------------------------------------ nodo

const TIPOS_JSON = new Set(["ia_clasificar", "ia_decidir", "ia_extraer"]);

/** Lo que un nodo le pide a su modelo. */
export function capacidadesDelNodo(n: NodoDef): Capacidad[] {
  const c = n.config;
  if (TIPOS_JSON.has(n.tipo) || (n.tipo === "ia_generar" && (c.esquema || c.formato === "json"))) return ["json_estructurado"];
  if (n.tipo === "leer_adjuntos" && c.vision !== false && c.ocr !== false) return ["imagenes"];
  return [];
}

/** La IA de un nodo: hereda del flujo (y de la global) o usa una propia (config.proveedor, modelo, credencial). */
export function OpcionesIaNodo({ n, poner }: { n: NodoDef; poner: (clave: string, v: unknown, grupo?: string) => void }) {
  const def = usarEditor((s) => s.definicion);
  const flujo = usarEditor((s) => s.flujo);
  const cambiar = usarEditor((s) => s.cambiar);
  const global = useIaGlobal();
  const c = n.config;
  const propia = typeof c.proveedor === "string" && c.proveedor !== "";
  const delFlujo: IaElegida | null = !def ? null : def.ia === undefined ? { proveedor: "vertex", modelo: def.modelo ?? "gemini-2.5-flash" } : "usar" in def.ia ? global : (def.ia as IaElegida);
  const pide = capacidadesDelNodo(n);
  const ponerVarias = (v: IaElegida | null) => cambiar((d) => ({
    ...d, nodos: d.nodos.map((x) => {
      if (x.id !== n.id) return x;
      const config = { ...x.config };
      delete config.proveedor;
      delete config.modelo;
      delete config.credencial;
      if (v) {
        config.proveedor = v.proveedor;
        if (v.modelo) config.modelo = v.modelo;
        if (v.credencial) config.credencial = v.credencial;
      }
      return { ...x, config };
    }),
  }), `${n.id}:ia`);
  const heredada = typeof c.modelo === "string" && c.modelo && !propia ? { ...delFlujo!, modelo: c.modelo } : delFlujo;
  return (
    <fieldset className="f-grupo">
      <legend>IA de este nodo</legend>
      <div className="f-campo f-interruptor">
        <div className="crece">
          <span className="f-etiqueta"><span>Hereda del flujo</span></span>
          <p className="f-ayuda">{heredada ? `Usa ${textoIa(heredada)}${def?.ia && "usar" in def.ia ? " (la IA global)" : ""}.` : "Usa la IA del flujo."}{n.tipo === "leer_adjuntos" ? " Solo se usa para la visión de las hojas que el OCR lee mal." : ""}</p>
        </div>
        <Interruptor valor={!propia} alCambiar={(v) => (v ? ponerVarias(null) : ponerVarias({ proveedor: delFlujo?.proveedor ?? "vertex", modelo: delFlujo?.modelo ?? "", ...(delFlujo?.credencial ? { credencial: delFlujo.credencial } : {}) }))} />
      </div>
      {propia && <SelectorIA valor={{ proveedor: String(c.proveedor), modelo: typeof c.modelo === "string" ? c.modelo : "", ...(typeof c.credencial === "string" ? { credencial: c.credencial } : {}) }} flujo={flujo} pide={pide} alCambiar={ponerVarias} />}
      {!propia && typeof c.modelo === "string" && c.modelo && (
        <p className="f-ayuda">Este nodo fija el modelo <code>{c.modelo}</code> del mismo proveedor. <button type="button" className="enlace" onClick={() => poner("modelo", undefined)}>Usar el del flujo</button></p>
      )}
    </fieldset>
  );
}

// ------------------------------------------------------------------ Ajustes

interface IaVera { texto: IaElegida; voz: IaElegida & { voz?: string; ubicacion?: string }; voz_disponible: boolean; motivo: string }

/** «IA de la plataforma» (la global) e «IA de Vera» (texto con herramientas y voz en vivo). */
export function TarjetasIa() {
  const avisar = usar((s) => s.avisar);
  const [global, setGlobal] = useState<IaElegida | null>(null);
  const [vera, setVera] = useState<IaVera | null>(null);
  const [borrador, setBorrador] = useState<{ global?: IaElegida; texto?: IaElegida; voz?: IaElegida & { voz?: string } }>({});
  const [ocupado, setOcupado] = useState<string | null>(null);
  useEffect(() => {
    void pedir<IaElegida>("/api/ia/global").then(setGlobal).catch(() => undefined);
    void pedir<IaVera>("/api/ia/vera").then(setVera).catch(() => undefined);
  }, []);
  const guardar = async (que: "global" | "texto" | "voz") => {
    setOcupado(que);
    try {
      if (que === "global") {
        const r = await pedir<IaElegida>("/api/ia/global", { method: "PUT", body: JSON.stringify(borrador.global) });
        setGlobal(r);
        recargarIa();
        avisar(`IA de la plataforma: ${textoIa(r)}`);
      } else {
        const r = await pedir<IaVera>("/api/ia/vera", { method: "PUT", body: JSON.stringify({ [que]: borrador[que] }) });
        setVera(r);
        avisar(que === "texto" ? `Vera escribe con ${textoIa(r.texto)}` : `Vera habla con ${textoIa(r.voz)}`);
      }
      setBorrador((b) => ({ ...b, [que]: undefined }));
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setOcupado(null);
    }
  };
  const limpia = (x: IaElegida & { por_defecto?: unknown }): IaElegida => ({ proveedor: x.proveedor, modelo: x.modelo, ...(x.credencial ? { credencial: x.credencial } : {}) });
  // Una función que devuelve el botón (no un componente creado en cada render, que se volvería a montar).
  const botonGuardar = (que: "global" | "texto" | "voz", listo: boolean) => (
    <button className="boton primario chico" disabled={!listo || ocupado === que} onClick={() => void guardar(que)}>{ocupado === que ? <span className="girando" /> : <Icono n="ok" tam={13} />}Guardar</button>
  );
  return (
    <>
      <article className="tarjeta plana">
        <h3>IA de la plataforma</h3>
        <p className="texto-2 chico">La que usan los flujos con «Usa la IA global» y los nodos que heredan de ellos. PQRD y tutelas no cambian: siguen en Vertex.</p>
        {global ? (
          <>
            <SelectorIA valor={borrador.global ?? limpia(global)} alCambiar={(v) => setBorrador((b) => ({ ...b, global: v }))} />
            <div className="fila"><span className="crece texto-3 chico">Ahora: {textoIa(global)}</span>{botonGuardar("global", Boolean(borrador.global?.modelo))}</div>
          </>
        ) : <div className="vacio chico"><span className="girando" />Cargando…</div>}
      </article>
      <article className="tarjeta plana">
        <h3>IA de Vera</h3>
        {vera ? (
          <>
            <p className="texto-2 chico">Aparte de los flujos. Para escribir necesita un modelo con herramientas; para hablar, uno de voz en vivo.</p>
            <h4 className="chico">Texto</h4>
            <SelectorIA valor={borrador.texto ?? limpia(vera.texto)} pide={["herramientas"]} alCambiar={(v) => setBorrador((b) => ({ ...b, texto: v }))} />
            <div className="fila"><span className="crece texto-3 chico">Ahora: {textoIa(vera.texto)}</span>{botonGuardar("texto", Boolean(borrador.texto?.modelo))}</div>
            <h4 className="chico">Voz</h4>
            <SelectorIA valor={borrador.voz ?? limpia(vera.voz)} voz alCambiar={(v) => setBorrador((b) => ({ ...b, voz: { ...v, ...(b.voz?.voz ? { voz: b.voz.voz } : {}) } }))} sinProbar />
            <div className="f-campo">
              <span className="f-etiqueta"><span>Voz (opcional)</span></span>
              <input className="campo" value={borrador.voz?.voz ?? vera.voz.voz ?? ""} placeholder="la de siempre" onChange={(e) => setBorrador((b) => ({ ...b, voz: { ...(b.voz ?? limpia(vera.voz)), voz: e.target.value } }))} />
            </div>
            <div className="fila">
              <span className="crece chico">{vera.voz_disponible ? <span className="pastilla verde"><Icono n="ok" tam={12} />Voz disponible · {textoIa(vera.voz)}</span> : <span className="pastilla rojo" title={vera.motivo}><Icono n="alerta" tam={12} />Sin voz: {vera.motivo}</span>}</span>
              {botonGuardar("voz", Boolean(borrador.voz?.modelo))}
            </div>
          </>
        ) : <div className="vacio chico"><span className="girando" />Cargando…</div>}
      </article>
    </>
  );
}
