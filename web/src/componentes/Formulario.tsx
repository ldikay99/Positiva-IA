import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Icono } from "../iconos.tsx";
import { describirCron, type Esquema, type Variable } from "../editor/modelo.ts";
import { usarEditor } from "../editor/almacen.ts";
import { Selector } from "./Selector.tsx";
import { EditorJson, Interruptor } from "./Editores.tsx";
import { FormularioCredencial } from "./Conexiones.tsx";
import { CampoArchivoFlujo } from "./Archivos.tsx";
import { CampoPlantillaDocumento } from "./Documentos.tsx";

/**
 * Un formulario que se arma solo a partir del esquema de configuración de un
 * tipo de nodo (un subconjunto de JSON Schema): objetos, listas de objetos con
 * agregar/quitar/reordenar, opciones, interruptores, números, textos con
 * variables, credenciales y horarios.
 */

export interface Contexto {
  variables: Variable[];
}

type AlCambiar = (v: unknown, grupo?: string) => void;

export function humano(clave: string): string {
  const t = clave.replace(/_/g, " ").trim();
  return t ? t[0]!.toUpperCase() + t.slice(1) : t;
}

const tipoDe = (e: Esquema): string => {
  if (Array.isArray(e.type)) return e.type.find((t) => t !== "null") ?? "string";
  if (e.type) return e.type;
  if (e.properties) return "object";
  if (e.items) return "array";
  if (e.enum) return typeof e.enum[0] === "number" ? "number" : "string";
  return "";
};

// --------------------------------------------------------------- armazón

function Etiqueta({ id, titulo, obligatorio, descripcion, children, ancho = false }: { id?: string; titulo: string; obligatorio?: boolean; descripcion?: string; children: React.ReactNode; ancho?: boolean }) {
  return (
    <div className={`f-campo ${ancho ? "ancho" : ""}`}>
      <label className="f-etiqueta" htmlFor={id}>
        <span>{titulo}</span>
        {obligatorio && <span className="f-obligatorio">obligatorio</span>}
      </label>
      {children}
      {descripcion && <p className="f-ayuda">{descripcion}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ números

export function CampoNumero({ id, valor, alCambiar, min, max, paso = 1, placeholder }: { id?: string; valor: number | undefined; alCambiar: (v: number | undefined) => void; min?: number; max?: number; paso?: number; placeholder?: string }) {
  const [texto, setTexto] = useState(valor === undefined ? "" : String(valor));
  useEffect(() => setTexto(valor === undefined ? "" : String(valor)), [valor]);
  const fijar = (n: number) => alCambiar(Math.min(max ?? Infinity, Math.max(min ?? -Infinity, Math.round(n / paso) * paso)));
  return (
    <div className="f-numero">
      <button type="button" aria-label="Menos" onClick={() => fijar((valor ?? min ?? 0) - paso)} disabled={min !== undefined && (valor ?? min) <= min}><Icono n="menos" tam={13} /></button>
      <input
        id={id}
        className="campo mono"
        inputMode="decimal"
        value={texto}
        placeholder={placeholder}
        onChange={(e) => {
          const t = e.target.value.replace(",", ".");
          setTexto(e.target.value);
          if (t.trim() === "") alCambiar(undefined);
          else if (!Number.isNaN(Number(t))) alCambiar(Number(t));
        }}
        onBlur={() => setTexto(valor === undefined ? "" : String(valor))}
        onKeyDown={(e) => {
          if (e.key === "ArrowUp") { e.preventDefault(); fijar((valor ?? 0) + paso); }
          if (e.key === "ArrowDown") { e.preventDefault(); fijar((valor ?? 0) - paso); }
        }}
      />
      <button type="button" aria-label="Más" onClick={() => fijar((valor ?? min ?? 0) + paso)} disabled={max !== undefined && (valor ?? 0) >= max}><Icono n="mas" tam={13} /></button>
    </div>
  );
}

// ---------------------------------------------------------------- plantillas

/** El fragmento entre «{{» y el cursor, si se está escribiendo una variable. */
function variableEnCurso(texto: string, cursor: number): { inicio: number; q: string } | null {
  const antes = texto.slice(0, cursor);
  const i = antes.lastIndexOf("{{");
  if (i < 0 || antes.slice(i).includes("}}")) return null;
  const q = antes.slice(i + 2);
  if (/[\n{}]/.test(q) || q.length > 60) return null;
  return { inicio: i, q: q.trim() };
}

export function CampoPlantilla({ id, valor, alCambiar, variables, largo = false, placeholder, mono = false }: { id?: string; valor: string; alCambiar: (v: string) => void; variables: Variable[]; largo?: boolean; placeholder?: string; mono?: boolean }) {
  const campo = useRef<HTMLTextAreaElement>(null);
  const [sugerir, setSugerir] = useState<{ inicio: number; q: string } | null>(null);
  const [activa, setActiva] = useState(0);
  const listaId = useId();
  const usadas = useMemo(() => [...new Set([...valor.matchAll(/\{\{\s*([^{}]+?)\s*\}\}/g)].map((m) => m[1]!.trim()))], [valor]);
  const conocidas = new Set(variables.map((v) => v.nombre));
  const existe = (u: string) => {
    const raiz = u.split(/[.[|\s]/)[0]!;
    return conocidas.has(u) || [...conocidas].some((c) => c === raiz || c.startsWith(`${raiz}.`) || u.startsWith(`${c}.`) || u.startsWith(`${c}[`));
  };
  const opciones = useMemo(() => {
    if (!sugerir) return [];
    const q = sugerir.q.toLowerCase();
    return variables.filter((v) => !q || v.nombre.toLowerCase().includes(q) || v.origen.toLowerCase().includes(q)).slice(0, 40);
  }, [sugerir, variables]);

  useLayoutEffect(() => {
    const t = campo.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${Math.min(largo ? 360 : 140, Math.max(largo ? 96 : 38, t.scrollHeight + 2))}px`;
  }, [valor, largo]);

  const insertar = (nombre: string) => {
    const t = campo.current;
    const cursor = t?.selectionStart ?? valor.length;
    const desde = sugerir ? sugerir.inicio : cursor;
    const sig = `${valor.slice(0, desde)}{{${nombre}}}${valor.slice(cursor).replace(/^[^{}\s]*\}\}/, "")}`;
    alCambiar(sig);
    setSugerir(null);
    requestAnimationFrame(() => {
      const pos = desde + nombre.length + 4;
      t?.focus();
      t?.setSelectionRange(pos, pos);
    });
  };

  const revisar = () => {
    const t = campo.current;
    if (!t) return;
    const s = variableEnCurso(t.value, t.selectionStart ?? 0);
    setSugerir(s);
    setActiva(0);
  };

  return (
    <div className="f-plantilla">
      <div className="f-plantilla-caja">
        <textarea
          ref={campo}
          id={id}
          className={`campo ${mono || !largo ? "mono-suave" : ""}`}
          rows={1}
          value={valor}
          placeholder={placeholder}
          spellCheck={largo}
          role="combobox"
          aria-expanded={Boolean(sugerir && opciones.length)}
          aria-controls={listaId}
          aria-autocomplete="list"
          onChange={(e) => {
            alCambiar(e.target.value);
            requestAnimationFrame(revisar);
          }}
          onClick={revisar}
          onBlur={() => setTimeout(() => setSugerir(null), 150)}
          onKeyDown={(e) => {
            if (!largo && e.key === "Enter" && !sugerir) e.preventDefault();
            if (!sugerir || !opciones.length) return;
            if (e.key === "ArrowDown") { e.preventDefault(); setActiva((a) => (a + 1) % opciones.length); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActiva((a) => (a - 1 + opciones.length) % opciones.length); }
            else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); insertar(opciones[activa]!.nombre); }
            else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setSugerir(null); }
          }}
        />
        <button
          type="button"
          className="f-insertar"
          title="Insertar una variable"
          aria-label="Insertar una variable"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            const t = campo.current;
            const c = t?.selectionStart ?? valor.length;
            setSugerir({ inicio: c, q: "" });
            setActiva(0);
            t?.focus();
          }}
        >
          {"{ }"}
        </button>
      </div>
      <AnimatePresence>
        {sugerir && (
          <motion.ul id={listaId} role="listbox" className="f-sugerencias" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}>
            {opciones.length ? (
              opciones.map((v, i) => (
                <li key={v.nombre} role="option" aria-selected={i === activa} className={i === activa ? "activa" : ""} onMouseDown={(e) => e.preventDefault()} onMouseEnter={() => setActiva(i)} onClick={() => insertar(v.nombre)}>
                  <code>{`{{${v.nombre}}}`}</code>
                  <small>{v.origen}{v.pista ? `: ${v.pista}` : ""}</small>
                </li>
              ))
            ) : (
              <li className="f-sin">{variables.length ? "Ninguna variable coincide" : "Todavía no hay variables antes de este nodo: conéctalo después de otro."}</li>
            )}
          </motion.ul>
        )}
      </AnimatePresence>
      {usadas.length > 0 && (
        <div className="f-usadas">
          {usadas.map((u) => (
            <span key={u} className={`ficha ${existe(u) ? "tono-azul" : "tono-ambar"}`} title={existe(u) ? "Llega de un nodo anterior" : "Ningún nodo anterior deja esta variable"}>
              {existe(u) ? <Icono n="ok" tam={10} /> : <Icono n="alerta" tam={10} />}
              {u}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- horario

type ModoCron = "minutos" | "hora" | "dia" | "semana" | "mes" | "avanzado";
const NOMBRES_DIAS = ["Do", "Lu", "Ma", "Mi", "Ju", "Vi", "Sá"];
const NOMBRES_LARGOS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];

function leerCron(expr: string): { modo: ModoCron; n: number; minuto: number; hora: number; dias: number[]; dia: number } {
  const base = { n: 15, minuto: 0, hora: 7, dias: [1, 2, 3, 4, 5], dia: 1 };
  const p = expr.trim().split(/\s+/);
  if (p.length !== 5) return { modo: expr.trim() ? "avanzado" : "dia", ...base };
  const [mi, ho, dm, me, ds] = p as [string, string, string, string, string];
  const cada = /^\*\/(\d+)$/.exec(mi);
  if (cada && ho === "*" && dm === "*" && me === "*" && ds === "*") return { ...base, modo: "minutos", n: Number(cada[1]) };
  if (/^\d+$/.test(mi) && ho === "*" && dm === "*" && me === "*" && ds === "*") return { ...base, modo: "hora", minuto: Number(mi) };
  if (/^\d+$/.test(mi) && /^\d+$/.test(ho) && me === "*") {
    const comun = { ...base, minuto: Number(mi), hora: Number(ho) };
    if (dm === "*" && ds === "*") return { ...comun, modo: "dia" };
    if (dm === "*" && /^[\d,-]+$/.test(ds)) {
      const dias = ds.split(",").flatMap((x) => { const r = /^(\d)-(\d)$/.exec(x); return r ? Array.from({ length: Number(r[2]) - Number(r[1]) + 1 }, (_, i) => Number(r[1]) + i) : [Number(x) % 7]; });
      return { ...comun, modo: "semana", dias };
    }
    if (/^\d+$/.test(dm) && ds === "*") return { ...comun, modo: "mes", dia: Number(dm) };
  }
  return { ...base, modo: "avanzado" };
}

function armarCron(m: ReturnType<typeof leerCron>): string {
  switch (m.modo) {
    case "minutos": return `*/${Math.max(1, m.n)} * * * *`;
    case "hora": return `${m.minuto} * * * *`;
    case "dia": return `${m.minuto} ${m.hora} * * *`;
    case "semana": return `${m.minuto} ${m.hora} * * ${[...m.dias].sort().join(",") || "1"}`;
    case "mes": return `${m.minuto} ${m.hora} ${m.dia} * *`;
    default: return "";
  }
}

function CampoHora({ hora, minuto, alCambiar }: { hora: number; minuto: number; alCambiar: (h: number, m: number) => void }) {
  return (
    <span className="f-hora">
      <CampoNumero valor={hora} min={0} max={23} alCambiar={(h) => alCambiar(h ?? 0, minuto)} />
      <b>:</b>
      <CampoNumero valor={minuto} min={0} max={59} paso={5} alCambiar={(mi) => alCambiar(hora, mi ?? 0)} />
    </span>
  );
}

export function CampoCron({ valor, alCambiar }: { valor: string; alCambiar: (v: string) => void }) {
  const [m, setM] = useState(() => leerCron(valor));
  const [libre, setLibre] = useState(valor);
  useEffect(() => {
    if (armarCron(m) !== valor || m.modo === "avanzado") {
      setM(leerCron(valor));
      setLibre(valor);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [valor]);
  const fijar = (sig: ReturnType<typeof leerCron>) => {
    setM(sig);
    if (sig.modo !== "avanzado") {
      const e = armarCron(sig);
      setLibre(e);
      alCambiar(e);
    }
  };
  const proximo = describirCron(m.modo === "avanzado" ? libre : armarCron(m));
  return (
    <div className="f-cron">
      <Selector<ModoCron>
        etiqueta="Cada cuánto"
        valor={m.modo}
        alCambiar={(modo) => fijar({ ...m, modo })}
        opciones={[
          { valor: "minutos", etiqueta: "Cada tantos minutos" },
          { valor: "hora", etiqueta: "Cada hora" },
          { valor: "dia", etiqueta: "Cada día" },
          { valor: "semana", etiqueta: "Ciertos días de la semana" },
          { valor: "mes", etiqueta: "Un día de cada mes" },
          { valor: "avanzado", etiqueta: "Expresión cron", detalle: "minuto hora día mes día-de-semana" },
        ]}
      />
      <div className="f-cron-detalle">
        {m.modo === "minutos" && <span className="fila">Cada <CampoNumero valor={m.n} min={1} max={59} alCambiar={(n) => fijar({ ...m, n: n ?? 1 })} /> minutos</span>}
        {m.modo === "hora" && <span className="fila">En el minuto <CampoNumero valor={m.minuto} min={0} max={59} paso={5} alCambiar={(n) => fijar({ ...m, minuto: n ?? 0 })} /></span>}
        {(m.modo === "dia" || m.modo === "semana" || m.modo === "mes") && (
          <span className="fila envuelve">
            {m.modo === "mes" && <>El día <CampoNumero valor={m.dia} min={1} max={28} alCambiar={(n) => fijar({ ...m, dia: n ?? 1 })} /></>}
            a las <CampoHora hora={m.hora} minuto={m.minuto} alCambiar={(hora, minuto) => fijar({ ...m, hora, minuto })} />
          </span>
        )}
        {m.modo === "semana" && (
          <div className="f-dias" role="group" aria-label="Días de la semana">
            {[1, 2, 3, 4, 5, 6, 0].map((d) => (
              <button key={d} type="button" aria-pressed={m.dias.includes(d)} title={NOMBRES_LARGOS[d]} className={m.dias.includes(d) ? "si" : ""} onClick={() => fijar({ ...m, dias: m.dias.includes(d) ? m.dias.filter((x) => x !== d) : [...m.dias, d] })}>
                {NOMBRES_DIAS[d]}
              </button>
            ))}
          </div>
        )}
        {m.modo === "avanzado" && (
          <input className="campo mono" value={libre} placeholder="0 7 * * 1-5" onChange={(e) => { setLibre(e.target.value); alCambiar(e.target.value); }} />
        )}
      </div>
      <p className="f-cron-frase"><Icono n="calendario" tam={13} />Corre {proximo}</p>
    </div>
  );
}

// -------------------------------------------------------------- credencial

export function CampoCredencial({ valor, alCambiar, tipos }: { valor: string; alCambiar: (v: string) => void; tipos?: string[] }) {
  const credenciales = usarEditor((s) => s.credenciales);
  const disponibles = usarEditor((s) => s.credencialesDisponibles);
  const cargar = usarEditor((s) => s.cargarCredenciales);
  const [crear, setCrear] = useState(false);
  useEffect(() => {
    void cargar();
  }, [cargar]);
  const lista = credenciales.filter((c) => !tipos?.length || tipos.includes(c.tipo));
  const opciones = [
    ...lista.map((c) => ({ valor: c.nombre, etiqueta: c.nombre, detalle: `${c.tipo}${c.permitir_escritura ? " · puede escribir" : " · solo lectura"}` })),
    ...(valor && !lista.some((c) => c.nombre === valor) ? [{ valor, etiqueta: valor, detalle: "no está entre las conexiones guardadas" }] : []),
  ];
  return (
    <div className="pila f-credencial">
      <div className="fila">
        <div className="crece">
          <Selector etiqueta="Credencial" valor={valor || null} opciones={opciones} alCambiar={alCambiar} placeholder={lista.length ? "Elegir una conexión…" : "Sin conexiones de este tipo"} />
        </div>
        {valor && <button type="button" className="boton-mini" aria-label="Quitar la credencial" title="Quitar" onClick={() => alCambiar("")}><Icono n="cerrar" tam={13} /></button>}
      </div>
      {!crear && (
        <button type="button" className="enlace" onClick={() => setCrear(true)} disabled={!disponibles} title={disponibles ? undefined : "El servidor todavía no ofrece conexiones"}>
          <Icono n="mas" tam={12} /> Crear credencial
        </button>
      )}
      <AnimatePresence>
        {crear && (
          <motion.div className="f-subcaja" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
            <FormularioCredencial
              tiposPermitidos={tipos}
              alTerminar={(nombre) => {
                setCrear(false);
                if (nombre) alCambiar(nombre);
              }}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ----------------------------------------------------------------- listas

function ListaTextos({ valor, alCambiar, placeholder, opciones }: { valor: unknown[]; alCambiar: (v: unknown[]) => void; placeholder?: string; opciones?: (string | number | boolean)[] }) {
  const [nuevo, setNuevo] = useState("");
  const agregar = (t: string) => {
    const x = t.trim();
    if (!x || valor.includes(x)) return;
    alCambiar([...valor, x]);
    setNuevo("");
  };
  return (
    <div className="f-chips">
      {valor.map((x, i) => (
        <span key={`${String(x)}-${i}`} className="ficha editable">
          {String(x)}
          <button type="button" aria-label={`Quitar ${String(x)}`} onClick={() => alCambiar(valor.filter((_, j) => j !== i))}><Icono n="cerrar" tam={11} /></button>
        </span>
      ))}
      {opciones ? (
        <Selector etiqueta="Agregar" tamano="chico" valor={null} placeholder="Agregar…" opciones={opciones.filter((o) => !valor.includes(o)).map((o) => ({ valor: String(o), etiqueta: String(o) }))} alCambiar={(v) => alCambiar([...valor, v])} />
      ) : (
        <input
          className="campo f-chip-entrada"
          value={nuevo}
          placeholder={placeholder ?? "Escribe y pulsa Enter"}
          onChange={(e) => setNuevo(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") { e.preventDefault(); agregar(nuevo); }
            else if (e.key === "Backspace" && !nuevo && valor.length) alCambiar(valor.slice(0, -1));
          }}
          onBlur={() => agregar(nuevo)}
        />
      )}
    </div>
  );
}

function tituloDeElemento(x: unknown, i: number): string {
  if (x && typeof x === "object") {
    const o = x as Record<string, unknown>;
    const t = o.nombre ?? o.valor ?? o.campo ?? o.variable ?? o.salida ?? o.op ?? o.titulo;
    if (typeof t === "string" && t) return t;
  }
  return `Elemento ${i + 1}`;
}

function ListaObjetos({ esquema, valor, alCambiar, ctx, ruta }: { esquema: Esquema; valor: unknown[]; alCambiar: AlCambiar; ctx: Contexto; ruta: string }) {
  const item = esquema.items ?? {};
  const [abierto, setAbierto] = useState<number | null>(valor.length === 1 ? 0 : null);
  const mover = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= valor.length) return;
    const sig = [...valor];
    [sig[i], sig[j]] = [sig[j], sig[i]];
    alCambiar(sig);
    setAbierto(j);
  };
  const nuevo = () => {
    const base: Record<string, unknown> = {};
    for (const [k, p] of Object.entries(item.properties ?? {})) if (p.default !== undefined) base[k] = p.default;
    alCambiar([...valor, base]);
    setAbierto(valor.length);
  };
  return (
    <div className="f-lista">
      {valor.map((x, i) => {
        const abiertoAqui = abierto === i;
        return (
          <div key={i} className={`f-elemento ${abiertoAqui ? "abierto" : ""}`}>
            <div className="f-elemento-cabeza">
              <button type="button" className="f-elemento-titulo" aria-expanded={abiertoAqui} onClick={() => setAbierto(abiertoAqui ? null : i)}>
                <Icono n={abiertoAqui ? "abajo" : "derecha"} tam={12} />
                <span className="crece">{tituloDeElemento(x, i)}</span>
              </button>
              <button type="button" className="boton-mini" aria-label="Subir" title="Subir" disabled={i === 0} onClick={() => mover(i, -1)}><Icono n="arriba" tam={13} /></button>
              <button type="button" className="boton-mini" aria-label="Bajar" title="Bajar" disabled={i === valor.length - 1} onClick={() => mover(i, 1)}><Icono n="abajo" tam={13} /></button>
              <button type="button" className="boton-mini peligro" aria-label="Quitar" title="Quitar" onClick={() => { alCambiar(valor.filter((_, j) => j !== i)); setAbierto(null); }}><Icono n="papelera" tam={13} /></button>
            </div>
            {abiertoAqui && (
              <div className="f-elemento-cuerpo">
                {tipoDe(item) === "object" && item.properties ? (
                  <CamposObjeto esquema={item} valor={(x ?? {}) as Record<string, unknown>} alCambiar={(v, g) => alCambiar(valor.map((y, j) => (j === i ? v : y)), g)} ctx={ctx} ruta={`${ruta}.${i}`} />
                ) : (
                  <Campo clave={String(i)} esquema={item} valor={x} alCambiar={(v, g) => alCambiar(valor.map((y, j) => (j === i ? v : y)), g)} ctx={ctx} ruta={`${ruta}.${i}`} sinEtiqueta />
                )}
              </div>
            )}
          </div>
        );
      })}
      <button type="button" className="boton chico f-agregar" onClick={nuevo}><Icono n="mas" tam={13} />Agregar {(item.title ?? "elemento").toLowerCase()}</button>
    </div>
  );
}

function MapaClaves({ valor, alCambiar, ctx, plantilla }: { valor: Record<string, unknown>; alCambiar: (v: Record<string, unknown>) => void; ctx: Contexto; plantilla: boolean }) {
  const filas = Object.entries(valor);
  const renombrar = (viejo: string, nuevo: string) => alCambiar(Object.fromEntries(filas.map(([k, v]) => [k === viejo ? nuevo : k, v])));
  return (
    <div className="f-mapa">
      {filas.map(([k, v], i) => (
        <div key={i} className="f-mapa-fila">
          <input className="campo mono" value={k} aria-label="Nombre" onChange={(e) => renombrar(k, e.target.value)} />
          {plantilla && typeof v === "string" ? (
            <CampoPlantilla valor={v} variables={ctx.variables} alCambiar={(t) => alCambiar({ ...valor, [k]: t })} />
          ) : (
            <input className="campo" value={typeof v === "string" ? v : JSON.stringify(v)} aria-label={`Valor de ${k}`} onChange={(e) => alCambiar({ ...valor, [k]: e.target.value })} />
          )}
          <button type="button" className="boton-mini peligro" aria-label={`Quitar ${k}`} onClick={() => alCambiar(Object.fromEntries(filas.filter(([x]) => x !== k)))}><Icono n="papelera" tam={13} /></button>
        </div>
      ))}
      <button
        type="button"
        className="boton chico f-agregar"
        onClick={() => {
          let n = "clave";
          for (let i = 2; n in valor; i++) n = `clave_${i}`;
          alCambiar({ ...valor, [n]: "" });
        }}
      >
        <Icono n="mas" tam={13} />Agregar par
      </button>
    </div>
  );
}

// ------------------------------------------------------------------- campos

export function Campo({ clave, esquema, valor, alCambiar, ctx, ruta, obligatorio, sinEtiqueta = false }: { clave: string; esquema: Esquema; valor: unknown; alCambiar: AlCambiar; ctx: Contexto; ruta: string; obligatorio?: boolean; sinEtiqueta?: boolean }) {
  const id = useId();
  const tipo = tipoDe(esquema);
  const titulo = esquema.title ?? humano(clave);
  const desc = esquema.description;
  const envolver = (hijo: React.ReactNode, ancho = false) => (sinEtiqueta ? <>{hijo}</> : <Etiqueta id={id} titulo={titulo} obligatorio={obligatorio} descripcion={desc} ancho={ancho}>{hijo}</Etiqueta>);
  const g = ruta;

  if (esquema.enum?.length) {
    return envolver(
      <Selector<string>
        etiqueta={titulo}
        valor={valor === undefined || valor === null ? null : String(valor)}
        placeholder={esquema.default !== undefined ? `Por defecto: ${String(esquema.default)}` : "Elegir…"}
        opciones={[...(obligatorio ? [] : [{ valor: "\u0000", etiqueta: <span className="texto-3">Sin valor</span>, texto: "sin valor" }]), ...esquema.enum.map((o) => ({ valor: String(o), etiqueta: String(o) }))]}
        alCambiar={(v) => alCambiar(v === "\u0000" ? undefined : typeof esquema.enum![0] === "number" ? Number(v) : typeof esquema.enum![0] === "boolean" ? v === "true" : v)}
      />,
    );
  }
  if (tipo === "boolean") {
    const v = typeof valor === "boolean" ? valor : Boolean(esquema.default);
    return (
      <div className="f-campo f-interruptor">
        <div className="crece">
          <span className="f-etiqueta" id={id}><span>{titulo}</span></span>
          {desc && <p className="f-ayuda">{desc}</p>}
        </div>
        <Interruptor valor={v} alCambiar={(b) => alCambiar(b)} />
      </div>
    );
  }
  if (tipo === "number" || tipo === "integer") {
    return envolver(<CampoNumero id={id} valor={typeof valor === "number" ? valor : undefined} min={esquema.minimum} max={esquema.maximum} paso={tipo === "integer" ? 1 : (esquema.maximum ?? 10) <= 2 ? 0.1 : 1} placeholder={esquema.default !== undefined ? String(esquema.default) : undefined} alCambiar={(n) => alCambiar(n, g)} />);
  }
  if (tipo === "array") {
    const item = esquema.items ?? {};
    const lista = Array.isArray(valor) ? valor : [];
    const it = tipoDe(item);
    if (it === "object" || item.properties) return envolver(<ListaObjetos esquema={esquema} valor={lista} alCambiar={alCambiar} ctx={ctx} ruta={ruta} />, true);
    if (it === "string" || it === "number" || it === "" || item.enum) return envolver(<ListaTextos valor={lista} opciones={item.enum} alCambiar={(v) => alCambiar(v)} />);
    return envolver(<EditorJson valor={lista} alCambiar={(v) => alCambiar(v, g)} />, true);
  }
  if (tipo === "object") {
    const objeto = valor && typeof valor === "object" && !Array.isArray(valor) ? (valor as Record<string, unknown>) : {};
    if (esquema.properties && Object.keys(esquema.properties).length) {
      return (
        <fieldset className="f-grupo">
          <legend>{titulo}</legend>
          {desc && <p className="f-ayuda">{desc}</p>}
          <CamposObjeto esquema={esquema} valor={objeto} alCambiar={alCambiar} ctx={ctx} ruta={ruta} />
        </fieldset>
      );
    }
    const ap = esquema.additionalProperties;
    const plantilla = typeof ap === "object" && ap.format === "plantilla";
    if (ap === false) return envolver(<EditorJson valor={objeto} alCambiar={(v) => alCambiar(v, g)} />, true);
    return envolver(<MapaClaves valor={objeto} alCambiar={(v) => alCambiar(v, g)} ctx={ctx} plantilla={plantilla || ap === undefined || (typeof ap === "object" && tipoDe(ap) === "string")} />, true);
  }
  if (tipo === "string" || tipo === "") {
    const texto = typeof valor === "string" ? valor : valor === undefined || valor === null ? "" : tipo === "" ? "" : String(valor);
    if (tipo === "" && valor !== undefined && typeof valor !== "string") return envolver(<EditorJson valor={valor} alCambiar={(v) => alCambiar(v, g)} />, true);
    const poner = (t: string) => alCambiar(t === "" ? undefined : t, g);
    // Una ruta escrita a mano solo se muestra en los flujos de antes que ya la tienen: lo nuevo es subir el archivo.
    if (esquema["x-legado"] === true && !texto) return null;
    switch (esquema.format) {
      case "archivo-flujo":
        return envolver(<CampoArchivoFlujo valor={texto} alCambiar={(t) => alCambiar(t || undefined)} />, true);
      case "plantilla-documento":
        return envolver(<CampoPlantillaDocumento valor={texto} alCambiar={(t) => alCambiar(t || undefined)} />, true);
      case "plantilla":
        return envolver(<CampoPlantilla id={id} valor={texto} variables={ctx.variables} largo={esquema["x-largo"] === true || texto.length > 70 || /cuerpo|mensaje|plantilla|instrucciones/.test(clave)} placeholder={esquema.default !== undefined ? String(esquema.default) : "Texto con {{variables}}"} alCambiar={poner} />, true);
      case "texto-largo":
        return envolver(<CampoPlantilla id={id} valor={texto} variables={ctx.variables} largo placeholder={esquema.default !== undefined ? String(esquema.default) : undefined} alCambiar={poner} />, true);
      case "credencial":
        return envolver(<CampoCredencial valor={texto} tipos={esquema["x-credencial-tipos"]} alCambiar={(t) => alCambiar(t || undefined)} />, true);
      case "cron":
        return envolver(<CampoCron valor={texto} alCambiar={(t) => alCambiar(t || undefined, g)} />, true);
      case "variable": {
        const malo = texto && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(texto);
        return envolver(
          <>
            <input id={id} className={`campo mono ${malo ? "f-mal" : ""}`} value={texto} placeholder={esquema.default !== undefined ? String(esquema.default) : "nombre_de_variable"} aria-invalid={Boolean(malo)} onChange={(e) => poner(e.target.value)} />
            {malo && <p className="f-error">Solo letras, números y guion bajo, sin espacios y sin empezar por número.</p>}
          </>,
        );
      }
      case "expresion":
        return envolver(<CampoPlantilla id={id} valor={texto} variables={ctx.variables} mono placeholder={esquema.default !== undefined ? String(esquema.default) : "correo.asunto"} alCambiar={poner} />, true);
      default:
        return envolver(<input id={id} className="campo" value={texto} placeholder={esquema.default !== undefined ? String(esquema.default) : undefined} onChange={(e) => poner(e.target.value)} />);
    }
  }
  return envolver(<EditorJson valor={valor ?? null} alCambiar={(v) => alCambiar(v, g)} />, true);
}

export function CamposObjeto({ esquema, valor, alCambiar, ctx, ruta, omitir = [] }: { esquema: Esquema; valor: Record<string, unknown>; alCambiar: AlCambiar; ctx: Contexto; ruta: string; omitir?: string[] }) {
  const props = Object.entries(esquema.properties ?? {}).filter(([k]) => !omitir.includes(k));
  const req = new Set(esquema.required ?? []);
  // Primero lo obligatorio, luego en el orden del esquema.
  props.sort(([a], [b]) => Number(req.has(b)) - Number(req.has(a)));
  return (
    <div className="f-campos">
      {props.map(([k, p]) => (
        <Campo
          key={k}
          clave={k}
          esquema={p}
          valor={valor[k]}
          obligatorio={req.has(k)}
          ctx={ctx}
          ruta={`${ruta}.${k}`}
          alCambiar={(v, g) => {
            const sig = { ...valor };
            if (v === undefined) delete sig[k];
            else sig[k] = v;
            alCambiar(sig, g);
          }}
        />
      ))}
    </div>
  );
}
