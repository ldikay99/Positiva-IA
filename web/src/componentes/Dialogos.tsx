import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { usar } from "../estado.ts";
import { Icono, type NombreIcono } from "../iconos.tsx";
import { hayCambios, usarEditor } from "../editor/almacen.ts";
import { autoOrdenar, configInicial, slugFlujo, valoresIniciales, textoJson, visualDe, type Definicion, type TipoNodo } from "../editor/modelo.ts";
import { CampoNumero } from "./Formulario.tsx";
import { Codigo, Interruptor } from "./Editores.tsx";
import { Selector } from "./Selector.tsx";
import { ZonaArchivos } from "./Archivos.tsx";
import { pedir } from "../estado.ts";
import type { ArchivoFlujo } from "../tipos.ts";
import { marcarPaso } from "../guia.ts";

type ReglasArchivos = { tipos_permitidos: string[]; max_mb: number; max_archivos: number };
/** Lo que va a la entrada: los archivos de un campo como sus ids (uno solo en «archivo»). */
const idsDe = (v: unknown, uno: boolean) => { const ids = (Array.isArray(v) ? (v as ArchivoFlujo[]) : []).map((a) => a.id); return uno ? ids[0] ?? null : ids; };

/** Una ventana propia: se cierra con Esc o tocando afuera y guarda el foco adentro. */
export function Dialogo({ abierto, cerrar, titulo, sub, children, pie, ancho = 560 }: { abierto: boolean; cerrar: () => void; titulo: string; sub?: string; children: React.ReactNode; pie?: React.ReactNode; ancho?: number }) {
  const caja = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!abierto) return;
    const previo = document.activeElement as HTMLElement | null;
    // El foco entra a la ventana (para Esc y Tab) sin caer en el botón de cerrar: si un campo ya lo tomó, se queda ahí.
    const t = setTimeout(() => {
      const c = caja.current;
      if (!c || c.contains(document.activeElement)) return;
      (c.querySelector<HTMLElement>("[autofocus]") ?? c).focus();
    }, 40);
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // con el visor de un archivo abierto, Esc cierra solo el visor
        if (document.querySelector(".arch-visor")) return;
        e.stopPropagation();
        cerrar();
      } else if (e.key === "Tab" && caja.current) {
        const f = [...caja.current.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), textarea, [tabindex]:not([tabindex='-1']), .cm-content")];
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f.at(-1)!.focus(); }
        else if (!e.shiftKey && document.activeElement === f.at(-1)) { e.preventDefault(); f[0]!.focus(); }
      }
    };
    window.addEventListener("keydown", tecla, true);
    return () => {
      clearTimeout(t);
      window.removeEventListener("keydown", tecla, true);
      previo?.focus?.();
    };
  }, [abierto, cerrar]);
  return createPortal(
    <AnimatePresence>
      {abierto && (
        <motion.div className="dlg-fondo" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onPointerDown={(e) => e.target === e.currentTarget && cerrar()}>
          <motion.div ref={caja} className="dlg" tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={id} style={{ ["--dlg-ancho" as string]: `${ancho}px` }} initial={{ opacity: 0, y: 18, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 10, transition: { duration: 0.12 } }} transition={{ type: "spring", stiffness: 420, damping: 36 }}>
            <header className="dlg-cabeza">
              <div className="crece">
                <h2 id={id}>{titulo}</h2>
                {sub && <p className="texto-2 chico">{sub}</p>}
              </div>
              <button className="boton-mini" aria-label="Cerrar" title="Cerrar (Esc)" onClick={cerrar}><Icono n="cerrar" tam={15} /></button>
            </header>
            <div className="dlg-cuerpo">{children}</div>
            {pie && <footer className="dlg-pie">{pie}</footer>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

// ------------------------------------------------------------------ webhook

/** La dirección pública de un webhook: /api/ganchos/<flujo>/<secreto>. */
export function urlDeGancho(flujo: string, config: Record<string, unknown>): string {
  const secreto = typeof config.secreto === "string" && config.secreto ? config.secreto : typeof config.token === "string" ? config.token : "";
  return secreto ? `${location.origin}/api/ganchos/${flujo}/${secreto}` : "";
}

export function UrlGancho({ url, ayuda }: { url: string; ayuda?: string }) {
  const [copiado, setCopiado] = useState(false);
  return (
    <div className="dlg-gancho">
      <span className="f-etiqueta"><span>Dirección del webhook</span></span>
      {url ? (
        <div className="fila">
          <code className="crece mono">{url}</code>
          <button type="button" className="boton chico" onClick={() => void navigator.clipboard?.writeText(url).then(() => { setCopiado(true); setTimeout(() => setCopiado(false), 1600); })}><Icono n={copiado ? "ok" : "copiar"} tam={13} />{copiado ? "Copiada" : "Copiar"}</button>
        </div>
      ) : (
        <p className="texto-2 chico">Se genera al guardar el flujo: guarda y aquí aparece la dirección para copiar.</p>
      )}
      {ayuda && <p className="f-ayuda">{ayuda}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ correr

interface CampoEntrada {
  nombre: string;
  titulo: string;
  tipo: string;
  obligatorio: boolean;
  descripcion?: string;
  opciones?: string[];
  defecto?: unknown;
}

function camposDe(config: Record<string, unknown>): CampoEntrada[] {
  const c = config.campos;
  if (Array.isArray(c)) {
    return c
      .map((x) => {
        if (typeof x === "string") return { nombre: x, titulo: x.replace(/_/g, " "), tipo: "texto", obligatorio: false };
        const o = (x ?? {}) as Record<string, unknown>;
        const nombre = String(o.nombre ?? o.clave ?? o.id ?? "");
        return {
          nombre,
          titulo: String(o.titulo ?? o.etiqueta ?? o.title ?? nombre.replace(/_/g, " ")),
          tipo: String(o.tipo ?? o.type ?? "texto"),
          obligatorio: Boolean(o.obligatorio ?? o.requerido),
          descripcion: typeof (o.descripcion ?? o.ayuda) === "string" ? String(o.descripcion ?? o.ayuda) : undefined,
          opciones: Array.isArray(o.opciones ?? o.enum) ? ((o.opciones ?? o.enum) as unknown[]).map(String) : undefined,
          defecto: o.defecto ?? o.default,
        };
      })
      .filter((x) => x.nombre);
  }
  if (c && typeof c === "object") return Object.entries(c as Record<string, unknown>).map(([nombre, t]) => ({ nombre, titulo: nombre.replace(/_/g, " "), tipo: typeof t === "string" ? t : "texto", obligatorio: false }));
  return [];
}

function primeraMayuscula(t: string): string {
  return t ? t[0]!.toUpperCase() + t.slice(1) : t;
}

function FormularioEntrada({ campos, valor, alCambiar, flujo, reglas }: { campos: CampoEntrada[]; valor: Record<string, unknown>; alCambiar: (v: Record<string, unknown>) => void; flujo: string; reglas?: ReglasArchivos }) {
  return (
    <div className="f-campos">
      {campos.map((c) => {
        const id = `entrada-${c.nombre}`;
        const v = valor[c.nombre];
        const poner = (x: unknown) => alCambiar({ ...valor, [c.nombre]: x });
        const t = c.tipo.toLowerCase();
        const control = t === "archivo" || t === "archivos" ? (
          // un archivo se sube al espacio del flujo: nunca se escribe una ruta
          <ZonaArchivos id={id} flujo={flujo} valor={Array.isArray(v) ? (v as ArchivoFlujo[]) : []} alCambiar={poner} multiple={t === "archivos"} maximo={t === "archivos" ? reglas?.max_archivos ?? 20 : 1} tipos={reglas?.tipos_permitidos} maxMb={reglas?.max_mb ?? 25} />
        ) : c.opciones?.length ? (
          <Selector etiqueta={c.titulo} valor={v === undefined ? null : String(v)} opciones={c.opciones.map((o) => ({ valor: o, etiqueta: o }))} alCambiar={poner} />
        ) : t.startsWith("bool") ? (
          <Interruptor valor={Boolean(v)} alCambiar={poner} />
        ) : t.startsWith("num") || t === "entero" ? (
          <CampoNumero id={id} valor={typeof v === "number" ? v : undefined} alCambiar={poner} />
        ) : t.includes("largo") || t === "parrafo" ? (
          <textarea id={id} className="campo" rows={4} value={String(v ?? "")} onChange={(e) => poner(e.target.value)} />
        ) : (
          <input id={id} className="campo" type={t === "fecha" ? "date" : "text"} value={String(v ?? "")} onChange={(e) => poner(e.target.value)} />
        );
        return (
          <div key={c.nombre} className={`f-campo ${t.startsWith("bool") ? "f-interruptor" : ""}`}>
            <label className="f-etiqueta" htmlFor={id}><span>{primeraMayuscula(c.titulo)}</span>{c.obligatorio && <span className="f-obligatorio">obligatorio</span>}</label>
            {control}
            {c.descripcion && <p className="f-ayuda">{c.descripcion}</p>}
          </div>
        );
      })}
    </div>
  );
}

const DE_CORREO = new Set(["entrada_correo", "correo_nuevo"]);

export function DialogoCorrer({ abierto, cerrar }: { abierto: boolean; cerrar: () => void }) {
  const def = usarEditor((s) => s.definicion);
  const tipos = usarEditor((s) => s.tipos);
  const version = usarEditor((s) => s.version);
  const sucio = usarEditor(hayCambios);
  const correr = usarEditor((s) => s.correr);
  const guardar = usarEditor((s) => s.guardar);
  const flujo = usarEditor((s) => s.flujo);
  const bandeja = usar((s) => s.bandeja);
  const seleccion = usar((s) => s.seleccion);
  const procesar = usar((s) => s.procesar);
  const avisar = usar((s) => s.avisar);
  const mapa = useMemo(() => new Map(tipos.map((t) => [t.tipo, t])), [tipos]);
  const disparador = def?.nodos.find((n) => mapa.get(n.tipo)?.disparador) ?? def?.nodos.find((n) => DE_CORREO.has(n.tipo) || n.tipo.startsWith("entrada")) ?? def?.nodos[0];
  const tipo = disparador?.tipo ?? "";
  const campos = useMemo(() => (disparador ? camposDe(disparador.config) : []), [disparador]);
  const [valores, setValores] = useState<Record<string, unknown>>({});
  const [json, setJson] = useState("{\n  \n}");
  const [comoJson, setComoJson] = useState(false);
  const [casos, setCasos] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [archivos, setArchivos] = useState<ArchivoFlujo[]>([]);
  const [reglas, setReglas] = useState<ReglasArchivos | undefined>();

  useEffect(() => {
    if (!abierto) return;
    setError("");
    setCasos(seleccion);
    setArchivos([]);
    const ini: Record<string, unknown> = {};
    for (const c of campos) if (c.defecto !== undefined) ini[c.nombre] = c.defecto;
    setValores(ini);
    setComoJson(false);
    if (flujo) void pedir<{ reglas: ReglasArchivos }>(`/api/flujos/${encodeURIComponent(flujo)}/archivos`).then((r) => setReglas(r.reglas)).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [abierto]);

  const deCorreo = DE_CORREO.has(tipo);
  const deArchivos = tipo === "entrada_archivos";
  const extra = useMemo(() => (deArchivos && disparador ? camposDe({ campos: disparador.config.campos_extra }) : []), [deArchivos, disparador]);
  const manual = (tipo === "entrada_manual" && campos.length > 0) || (deArchivos && extra.length > 0);
  const camposForm = deArchivos ? extra : campos;
  /** Los campos archivo/archivos van como ids; lo demás, tal cual. */
  const entradaDe = (v: Record<string, unknown>) => Object.fromEntries(Object.entries(v).map(([k, x]) => {
    const c = camposForm.find((y) => y.nombre === k);
    const t = c?.tipo.toLowerCase();
    return [k, t === "archivo" || t === "archivos" ? idsDe(x, t === "archivo") : x];
  }));
  const urlGancho = tipo === "webhook" && flujo ? urlDeGancho(flujo, disparador!.config) : "";
  const correos = bandeja.filter((c) => !q.trim() || `${c.caso} ${c.asunto} ${c.remitente}`.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 80);

  const lanzar = async () => {
    setError("");
    setEnviando(true);
    try {
      if (sucio && !(await guardar())) {
        setError("No se pudo guardar: revisa los problemas marcados en los nodos.");
        return;
      }
      if (deCorreo) {
        if (!casos.length) {
          setError("Elige al menos un correo.");
          return;
        }
        if (casos.length === 1) {
          try {
            await correr({ caso: casos[0] });
          } catch {
            await procesar(casos);
          }
        } else await procesar(casos);
      } else {
        let entrada: Record<string, unknown> = {};
        if (deArchivos && !archivos.length) {
          setError("Sube al menos un archivo: arrástralo a la zona o haz clic para elegirlo.");
          return;
        }
        if (manual && !comoJson) {
          const vacio = (x: unknown) => x === undefined || x === "" || (Array.isArray(x) && !x.length);
          const faltan = camposForm.filter((c) => c.obligatorio && vacio(valores[c.nombre]));
          if (faltan.length) {
            setError(`Falta ${faltan.map((c) => `«${c.titulo}»`).join(", ")}.`);
            return;
          }
          entrada = entradaDe(valores);
        } else if (deArchivos) {
          entrada = {};
        } else {
          try {
            const t = json.trim();
            entrada = t ? (JSON.parse(t) as Record<string, unknown>) : {};
          } catch {
            setError("El JSON no es válido: revisa comas y comillas.");
            return;
          }
        }
        // los archivos de la zona (entrada_archivos o webhook) van como ids del espacio del flujo
        if (archivos.length) entrada = { ...entrada, archivos: archivos.map((a) => a.id) };
        await correr({ entrada });
      }
      window.dispatchEvent(new CustomEvent("positiva:control", { detail: { accion: "proceso" } }));
      avisar("Corriendo: síguelo en el lienzo; el resultado queda en el Historial", "info");
      cerrar();
    } catch (e) {
      const m = (e as Error).message;
      setError(m === "no existe" || m.includes("404") ? "El servidor todavía no puede correr este flujo desde aquí. Inténtalo en un momento." : m);
    } finally {
      setEnviando(false);
    }
  };

  return (
    <Dialogo
      abierto={abierto}
      cerrar={cerrar}
      titulo={`Correr «${def?.nombre ?? ""}»`}
      sub={disparador ? `Arranca en «${disparador.nombre}» (${mapa.get(tipo)?.nombre ?? tipo})` : undefined}
      ancho={deCorreo ? 640 : 560}
      pie={
        <>
          {sucio && <span className="dlg-nota"><Icono n="info" tam={13} />Hay cambios sin guardar: se guardan antes de correr (versión {version + 1}).</span>}
          <span className="espacio" />
          <button className="boton" onClick={cerrar}>Cancelar</button>
          <button className="boton primario" disabled={enviando || (deCorreo && !casos.length)} onClick={() => void lanzar()}>
            {enviando ? <span className="girando" /> : <Icono n="play" tam={13} />}
            {sucio ? "Guardar y correr" : "Correr"}{deCorreo && casos.length > 1 ? ` ${casos.length} correos` : ""}
          </button>
        </>
      }
    >
      {deCorreo ? (
        <div className="pila">
          <p className="texto-2 chico">Elige correos de la correspondencia de este flujo (su bandeja de prueba o su buzón). Nada se escribe en ningún buzón.</p>
          <div className="fila">
            <div className="campo-icono crece"><Icono n="buscar" tam={14} /><input className="campo" placeholder="Buscar por caso, asunto o remitente" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar correos" /></div>
            {casos.length > 0 && <button className="boton chico" onClick={() => setCasos([])}>Quitar {casos.length}</button>}
          </div>
          <ul className="dlg-correos" role="listbox" aria-multiselectable="true" aria-label="Correos">
            {correos.map((c) => {
              const sel = casos.includes(c.caso);
              return (
                <li key={c.caso} role="option" aria-selected={sel} tabIndex={0} className={sel ? "sel" : ""} onClick={() => setCasos((x) => (sel ? x.filter((y) => y !== c.caso) : [...x, c.caso]))} onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); setCasos((x) => (sel ? x.filter((y) => y !== c.caso) : [...x, c.caso])); } }}>
                  <span className={`casilla ${sel ? "si" : ""}`} aria-hidden>{sel && <Icono n="check" tam={12} strokeWidth={2.6} />}</span>
                  <span className="crece">
                    <b>{c.asunto || "(sin asunto)"}</b>
                    <small className="texto-3">{c.caso} · {c.remitente}{c.adjuntos.length ? ` · ${c.adjuntos.length} adjunto${c.adjuntos.length === 1 ? "" : "s"}` : ""}</small>
                  </span>
                </li>
              );
            })}
            {!correos.length && <li className="vacio chico">{bandeja.length ? "Ningún correo coincide." : "Este flujo todavía no tiene correos: súbelos a su bandeja de prueba (.eml o .msg) o ponle un buzón en su «Entrada de correo»."}</li>}
          </ul>
        </div>
      ) : (
        <div className="pila">
          {urlGancho && <UrlGancho url={urlGancho} ayuda="Otro sistema la llama con un POST (JSON o multipart con archivos); aquí puedes probarla con el cuerpo que mandaría." />}
          {(deArchivos || tipo === "webhook") && flujo && (
            <div className="f-campo">
              <span className="f-etiqueta"><span>{deArchivos ? "Archivos" : "Archivos (opcional)"}</span>{deArchivos && <span className="f-obligatorio">obligatorio</span>}</span>
              <ZonaArchivos flujo={flujo} valor={archivos} alCambiar={setArchivos} maximo={reglas?.max_archivos ?? 20} tipos={deArchivos ? reglas?.tipos_permitidos : undefined} maxMb={reglas?.max_mb ?? 25}
                ayuda={`Imágenes, PDF, Word, Excel, PowerPoint, texto, correos o ZIP; hasta ${reglas?.max_archivos ?? 20} archivos de ${reglas?.max_mb ?? 25} MB. Video y audio no se aceptan.`} />
            </div>
          )}
          {manual && (
            <div className="fila">
              <p className="texto-2 chico crece">{comoJson ? "La entrada completa, en JSON." : deArchivos ? "Los otros datos que pide este flujo." : "Los datos con que arranca esta corrida."}</p>
              <button className="enlace" onClick={() => { if (!comoJson) setJson(textoJson(entradaDe(valores))); setComoJson(!comoJson); }}>{comoJson ? "Volver al formulario" : "Escribir como JSON"}</button>
            </div>
          )}
          {manual && !comoJson ? (
            <FormularioEntrada campos={camposForm} valor={valores} alCambiar={setValores} flujo={flujo ?? ""} reglas={reglas} />
          ) : deArchivos && !manual ? null : (
            <>
              {!manual && <p className="texto-2 chico">{tipo === "programado" ? "Corre ahora, sin esperar el horario. Puedes pasarle datos de prueba en JSON." : "Los datos de entrada, en JSON (puede quedar vacío)."}</p>}
              <Codigo valor={json} alCambiar={setJson} lenguaje="json" minAlto="140px" />
            </>
          )}
        </div>
      )}
      {error && <p className="f-error" role="alert">{error}</p>}
    </Dialogo>
  );
}

// ------------------------------------------------------------- flujo nuevo

const DISPARADORES_BASE: { tipo: string; nombre: string; descripcion: string; icono: NombreIcono }[] = [
  { tipo: "entrada_correo", nombre: "Correo entrante", descripcion: "Corre sobre los correos de su bandeja de prueba o de su buzón.", icono: "correo" },
  { tipo: "entrada_archivos", nombre: "Archivos", descripcion: "Corre con imágenes y documentos que subes al correrlo.", icono: "adjunto" },
];

const ICONO_DISPARADOR: Record<string, NombreIcono> = { entrada_correo: "correo", correo_nuevo: "bandeja", entrada_manual: "editar", programado: "calendario", webhook: "gancho", entrada_archivos: "adjunto" };

export function DialogoNuevoFlujo({ abierto, cerrar }: { abierto: boolean; cerrar: () => void }) {
  const tipos = usarEditor((s) => s.tipos);
  const cargarTipos = usarEditor((s) => s.cargarTipos);
  const flujos = usar((s) => s.flujos);
  const cargarFlujos = usar((s) => s.cargarFlujos);
  const cambiarFlujo = usar((s) => s.cambiarFlujo);
  const irA = usar((s) => s.irA);
  const avisar = usar((s) => s.avisar);
  const [nombre, setNombre] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [disparador, setDisparador] = useState("entrada_correo");
  const [error, setError] = useState<string[]>([]);
  const [creando, setCreando] = useState(false);

  useEffect(() => {
    if (abierto) {
      void cargarTipos();
      setError([]);
    }
  }, [abierto, cargarTipos]);

  const opciones = useMemo(() => {
    const d = tipos.filter((t) => t.disparador);
    return d.length ? d.map((t) => ({ tipo: t.tipo, nombre: t.nombre, descripcion: t.descripcion, icono: ICONO_DISPARADOR[t.tipo] ?? (visualDe(t.categoria).icono as NombreIcono) })) : DISPARADORES_BASE;
  }, [tipos]);

  useEffect(() => {
    if (!opciones.some((o) => o.tipo === disparador) && opciones[0]) setDisparador(opciones[0].tipo);
  }, [opciones, disparador]);

  const crear = async () => {
    const n = nombre.trim();
    if (n.length < 2) return setError(["Ponle un nombre de al menos dos letras."]);
    setCreando(true);
    setError([]);
    try {
      const mapa = new Map<string, TipoNodo>(tipos.map((t) => [t.tipo, t]));
      const td = mapa.get(disparador);
      const ts = mapa.get("salida");
      const id = slugFlujo(n, flujos.map((f) => f.id));
      // El servidor pide al menos dos nodos: el disparador y una salida para empezar.
      const def: Definicion = {
        id,
        nombre: n,
        descripcion: descripcion.trim(),
        modelo: "gemini-2.5-flash",
        // Los flujos nuevos usan la IA global de la plataforma (se cambia en «IA del flujo»).
        ia: { usar: "global" },
        nodos: [
          { id: "inicio", tipo: disparador, nombre: td?.nombre ?? "Correo recibido", config: td ? configInicial(td) : {} },
          { id: "fin", tipo: "salida", nombre: "Revisión manual", config: { ...(ts ? valoresIniciales(ts.config) : {}), desenlace: "manual" } },
        ],
        aristas: [{ desde: "inicio", hacia: "fin" }],
      };
      def.disenio = autoOrdenar(def, mapa);
      const r = await fetch(`/api/flujos/${encodeURIComponent(id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ definicion: def }) });
      const cuerpo = (await r.json().catch(() => ({}))) as { error?: string; problemas?: string[]; flujo?: { version?: number } };
      if (!r.ok) {
        setError(cuerpo.problemas?.length ? cuerpo.problemas : [cuerpo.error ?? `error ${r.status}`]);
        return;
      }
      try { localStorage.setItem(`positiva-ia:disenio:${id}:${cuerpo.flujo?.version ?? 1}`, JSON.stringify(def.disenio)); } catch { /* sin almacenamiento */ }
      marcarPaso("crear");
      await cargarFlujos();
      await cambiarFlujo(id);
      irA("flujo");
      avisar(`Flujo «${n}» creado: agrega pasos con «Agregar»`);
      setNombre("");
      setDescripcion("");
      cerrar();
    } catch (e) {
      setError([(e as Error).message]);
    } finally {
      setCreando(false);
    }
  };

  return (
    <Dialogo
      abierto={abierto}
      cerrar={cerrar}
      titulo="Nuevo flujo"
      sub="Elige cómo arranca. Después le agregas pasos en el lienzo, uno a uno."
      pie={
        <>
          <span className="espacio" />
          <button className="boton" onClick={cerrar}>Cancelar</button>
          <button className="boton primario" disabled={creando} onClick={() => void crear()}>{creando ? <span className="girando" /> : <Icono n="mas" tam={14} />}Crear y abrir</button>
        </>
      }
    >
      <form className="pila" onSubmit={(e) => { e.preventDefault(); void crear(); }}>
        <div className="f-campo">
          <label className="f-etiqueta" htmlFor="nf-nombre"><span>Nombre</span><span className="f-obligatorio">obligatorio</span></label>
          <input id="nf-nombre" className="campo" autoFocus value={nombre} placeholder="Solicitudes de incapacidad" onChange={(e) => setNombre(e.target.value)} />
          {nombre.trim().length > 1 && <p className="f-ayuda">Se guardará como <code>{slugFlujo(nombre, flujos.map((f) => f.id))}</code>.</p>}
        </div>
        <div className="f-campo">
          <label className="f-etiqueta" htmlFor="nf-desc"><span>Qué hace</span></label>
          <input id="nf-desc" className="campo" value={descripcion} placeholder="Una frase para reconocerlo en el tablero" onChange={(e) => setDescripcion(e.target.value)} />
        </div>
        <fieldset className="nf-disparadores">
          <legend className="f-etiqueta"><span>Cómo arranca</span></legend>
          <div className="nf-opciones" role="radiogroup" aria-label="Cómo arranca">
            {opciones.map((o) => (
              <button key={o.tipo} type="button" role="radio" aria-checked={disparador === o.tipo} className={`nf-opcion ${disparador === o.tipo ? "sel" : ""}`} onClick={() => setDisparador(o.tipo)}>
                <span className="nf-icono"><Icono n={o.icono} tam={17} /></span>
                <span className="crece"><b>{o.nombre}</b><small>{o.descripcion}</small></span>
                <span className={`radio ${disparador === o.tipo ? "si" : ""}`} aria-hidden />
              </button>
            ))}
          </div>
        </fieldset>
        {error.length > 0 && <ul className="f-error" role="alert">{error.map((x, i) => <li key={i}>{x}</li>)}</ul>}
        <button type="submit" hidden />
      </form>
    </Dialogo>
  );
}

/** Pedir un nombre (para duplicar un flujo). */
export function DialogoNombre({ abierto, cerrar, titulo, sub, inicial, accion, alAceptar }: { abierto: boolean; cerrar: () => void; titulo: string; sub?: string; inicial: string; accion: string; alAceptar: (nombre: string) => Promise<void> }) {
  const [nombre, setNombre] = useState(inicial);
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);
  useEffect(() => {
    if (abierto) {
      setNombre(inicial);
      setError("");
    }
  }, [abierto, inicial]);
  const aceptar = async () => {
    if (nombre.trim().length < 2) return setError("Ponle un nombre de al menos dos letras.");
    setEnviando(true);
    try {
      await alAceptar(nombre.trim());
      cerrar();
    } catch (e) {
      setError((e as Error).message === "no existe" ? "El servidor todavía no permite esta acción." : (e as Error).message);
    } finally {
      setEnviando(false);
    }
  };
  return (
    <Dialogo abierto={abierto} cerrar={cerrar} titulo={titulo} sub={sub} ancho={460} pie={<><span className="espacio" /><button className="boton" onClick={cerrar}>Cancelar</button><button className="boton primario" disabled={enviando} onClick={() => void aceptar()}>{enviando && <span className="girando" />}{accion}</button></>}>
      <form onSubmit={(e) => { e.preventDefault(); void aceptar(); }} className="pila">
        <input className="campo" autoFocus value={nombre} onChange={(e) => setNombre(e.target.value)} aria-label="Nombre" />
        {error && <p className="f-error" role="alert">{error}</p>}
      </form>
    </Dialogo>
  );
}
