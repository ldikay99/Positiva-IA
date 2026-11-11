import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { peso } from "./Archivos.tsx";
import { DialogoCruzar, type ModoCruzar } from "./CruzarFlujos.tsx";
import { atajoDeshacer, deshacerGuardado, listarVersiones, rehacerGuardado } from "../editor/versiones.ts";
import "./archivos.css";

/**
 * Deshacer y rehacer en un flujo de código (pqrd, tutelas y sus copias): el lienzo guarda cada cambio al instante
 * (apagar una etapa, quitar un paso), así que Ctrl+Z vuelve a la versión anterior en el servidor (como versión nueva)
 * y Ctrl+Y (o Ctrl+Shift+Z) rehace. Los declarativos tienen su propio deshacer en el editor.
 */
function DeshacerCodigo({ flujo }: { flujo: string }) {
  const [estado, setEstado] = useState<{ deshacer: boolean; rehacer: boolean }>({ deshacer: false, rehacer: false });
  const [ocupado, setOcupado] = useState(false);
  const leer = useCallback(() => {
    void listarVersiones(flujo)
      .then((l) => {
        const actual = l.find((v) => v.actual);
        setEstado({ deshacer: l.length > 1 && Boolean(actual && actual.version > Math.min(...l.map((v) => v.version))), rehacer: Boolean(actual && (actual.por === "deshacer" || actual.por === "rehacer") && typeof actual.deshecha === "number") });
      })
      .catch(() => setEstado({ deshacer: false, rehacer: false }));
  }, [flujo]);
  const hacer = useCallback(async (que: "deshacer" | "rehacer") => {
    if (ocupado) return;
    setOcupado(true);
    try {
      await (que === "deshacer" ? deshacerGuardado(flujo) : rehacerGuardado(flujo));
    } finally {
      setOcupado(false);
      leer();
    }
  }, [flujo, ocupado, leer]);
  useEffect(() => {
    leer();
    const oir = (e: Event) => (e as CustomEvent<{ flujo?: string }>).detail?.flujo === flujo && leer();
    const tecla = (e: KeyboardEvent) => {
      const que = atajoDeshacer(e);
      if (!que || (e.target as HTMLElement | null)?.closest?.("[role=dialog], .chat, .cr-dialogo")) return;
      e.preventDefault();
      void hacer(que);
    };
    window.addEventListener("positiva:flujo-guardado", oir);
    window.addEventListener("keydown", tecla);
    return () => {
      window.removeEventListener("positiva:flujo-guardado", oir);
      window.removeEventListener("keydown", tecla);
    };
  }, [flujo, leer, hacer]);
  return (
    <>
      <button type="button" className="boton chico fantasma" disabled={!estado.deshacer || ocupado} aria-label="Deshacer" onClick={() => void hacer("deshacer")} title="Deshacer el último cambio (Ctrl+Z)">
        <Icono n="deshacer" tam={13} />
      </button>
      <button type="button" className="boton chico fantasma" disabled={!estado.rehacer || ocupado} aria-label="Rehacer" onClick={() => void hacer("rehacer")} title="Rehacer (Ctrl+Y o Ctrl+Shift+Z)">
        <Icono n="rehacer" tam={13} />
      </button>
    </>
  );
}

/**
 * Renombrar, clonar y borrar un flujo, a la vista (en la cabecera del flujo y en cada
 * tarjeta del tablero). Todo se confirma en su lugar, sin ventanas: el nombre se
 * escribe ahí mismo y el borrado dice antes qué se va a borrar.
 */

type Panel = "renombrar" | "clonar" | "borrar" | null;

interface Borrado {
  se_puede_borrar: boolean;
  motivo?: string;
  corridas: number;
  versiones: number;
  variables: number;
  secretos: number;
  archivos: number;
  bytes: number;
  correos_de_prueba: number;
  tabla_del_flujo: boolean;
  credenciales: string[];
  compuerta: boolean;
  programacion: boolean;
}

function plural(n: number, uno: string, varios: string): string {
  return `${n.toLocaleString("es-CO")} ${n === 1 ? uno : varios}`;
}

/** La lista de lo que se borra, en palabras. */
function loQueSeBorra(b: Borrado): string[] {
  const r: string[] = [];
  if (b.corridas) r.push(plural(b.corridas, "corrida con su historial", "corridas con su historial"));
  if (b.versiones > 1) r.push(plural(b.versiones, "versión", "versiones"));
  if (b.variables + b.secretos) r.push(`su entorno (${plural(b.variables, "variable", "variables")}${b.secretos ? ` y ${plural(b.secretos, "secreto", "secretos")}` : ""})`);
  if (b.archivos) r.push(`${plural(b.archivos, "archivo subido", "archivos subidos")} (${peso(b.bytes)})`);
  if (b.correos_de_prueba) r.push(plural(b.correos_de_prueba, "correo de su bandeja", "correos de su bandeja"));
  if (b.tabla_del_flujo) r.push("su tabla integrada");
  if (b.credenciales.length) r.push(`las conexiones solo suyas: ${b.credenciales.join(", ")}`);
  if (b.compuerta) r.push("su compuerta de radicación");
  if (b.programacion) r.push("su programación");
  return r;
}

export function AccionesFlujo({ flujo, variante = "cabecera", alClonar }: { flujo: string; variante?: "cabecera" | "tarjeta"; alClonar?: (id: string) => void }) {
  const f = usar((s) => s.flujos.find((x) => x.id === flujo));
  const renombrar = usar((s) => s.renombrarFlujo);
  const clonar = usar((s) => s.clonarFlujo);
  const borrar = usar((s) => s.borrarFlujo);
  const cambiarFlujo = usar((s) => s.cambiarFlujo);
  const irA = usar((s) => s.irA);
  const avisar = usar((s) => s.avisar);
  const [panel, setPanel] = useState<Panel>(null);
  const [nombre, setNombre] = useState("");
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [borrado, setBorrado] = useState<Borrado | null>(null);
  // comparar con otro flujo y traer pasos de otro (CruzarFlujos.tsx), en la cabecera del flujo
  const [cruzar, setCruzar] = useState<ModoCruzar | null>(null);
  const caja = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!panel || variante === "tarjeta") return;
    const fuera = (e: PointerEvent) => !caja.current?.contains(e.target as Node) && setPanel(null);
    const tecla = (e: KeyboardEvent) => e.key === "Escape" && setPanel(null);
    window.addEventListener("pointerdown", fuera, true);
    window.addEventListener("keydown", tecla);
    return () => {
      window.removeEventListener("pointerdown", fuera, true);
      window.removeEventListener("keydown", tecla);
    };
  }, [panel, variante]);

  if (!f) return null;
  const borrable = f.borrable ?? !f.original;

  const abrir = (p: Panel) => {
    setError("");
    if (p === panel) return setPanel(null);
    setPanel(p);
    if (p === "renombrar") setNombre(f.nombre);
    if (p === "clonar") setNombre(`${f.nombre} (copia)`);
    if (p === "borrar") {
      setBorrado(null);
      void pedir<Borrado>(`/api/flujos/${encodeURIComponent(flujo)}/borrado`).then(setBorrado).catch((e: Error) => setError(e.message));
    }
  };

  const hacer = async () => {
    setError("");
    if ((panel === "renombrar" || panel === "clonar") && nombre.trim().length < 2) return setError("Ponle un nombre de al menos dos letras.");
    setEnviando(true);
    try {
      if (panel === "renombrar") {
        await renombrar(flujo, nombre.trim());
        avisar(`Ahora se llama «${nombre.trim()}»`);
      } else if (panel === "clonar") {
        const id = await clonar(flujo, nombre.trim());
        avisar(`Copia «${nombre.trim()}» creada${f.tipo === "codigo" ? ": su compuerta y su programación nacen apagadas" : ""}`);
        if (alClonar) alClonar(id);
        else {
          await cambiarFlujo(id);
          irA("flujo");
        }
      } else if (panel === "borrar") {
        await borrar(flujo);
        avisar(`«${f.nombre}» borrado con todo lo suyo`);
        if (variante === "cabecera") irA("tablero");
      }
      setPanel(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setEnviando(false);
    }
  };

  const contenido = panel && (
    <motion.div
      key={panel}
      className={`af-panel ${variante} ${panel === "borrar" ? "peligro" : ""}`}
      role={panel === "borrar" ? "alertdialog" : "group"}
      aria-label={panel === "borrar" ? `Borrar ${f.nombre}` : panel === "clonar" ? `Clonar ${f.nombre}` : `Renombrar ${f.nombre}`}
      initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4, transition: { duration: 0.12 } }}
    >
      {panel === "borrar" ? (
        <>
          <p className="af-titulo">¿Borrar «{f.nombre}»?</p>
          {!borrado && !error && <p className="af-nota"><span className="girando" />Revisando qué tiene…</p>}
          {borrado && (
            <>
              {(() => {
                const cosas = loQueSeBorra(borrado);
                return cosas.length ? (
                  <>
                    <p className="af-nota">Se borra el flujo y, sin vuelta atrás:</p>
                    <ul className="af-lista">{cosas.map((x) => <li key={x}>{x}</li>)}</ul>
                  </>
                ) : <p className="af-nota">Se borra el flujo. Todavía no tiene corridas, archivos ni entorno.</p>;
              })()}
            </>
          )}
        </>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); void hacer(); }}>
          <label className="af-titulo" htmlFor={`af-${flujo}-${panel}`}>{panel === "clonar" ? "Nombre de la copia" : "Nuevo nombre"}</label>
          <input id={`af-${flujo}-${panel}`} className="campo" autoFocus value={nombre} maxLength={80} onChange={(e) => setNombre(e.target.value)} />
          {panel === "clonar" && (
            <p className="af-nota">
              {f.tipo === "codigo"
                ? "Una instancia propia del mismo motor: su entorno (copiado, los secretos cifrados), sus conexiones, su compuerta y su programación apagadas, y sus corridas."
                : "Copia los nodos, el diseño y el entorno; los secretos quedan declarados sin valor."}
            </p>
          )}
        </form>
      )}
      {error && <p className="f-error" role="alert">{error}</p>}
      <div className="af-pie">
        <button type="button" className="boton chico" onClick={() => setPanel(null)}>Cancelar</button>
        <button
          type="button"
          className={`boton chico ${panel === "borrar" ? "peligro-solido" : "primario"}`}
          disabled={enviando || (panel === "borrar" && !borrado?.se_puede_borrar)}
          onClick={() => void hacer()}
        >
          {enviando && <span className="girando" />}
          {panel === "borrar" ? "Sí, borrar todo" : panel === "clonar" ? "Clonar y abrir" : "Guardar nombre"}
        </button>
      </div>
    </motion.div>
  );

  return (
    <div className={`af ${variante}`} ref={caja}>
      <div className="af-botones" role="group" aria-label={`Acciones de ${f.nombre}`}>
        <button type="button" className={`boton chico fantasma ${panel === "renombrar" ? "activo" : ""}`} aria-expanded={panel === "renombrar"} aria-label="Renombrar" onClick={() => abrir("renombrar")} title="Cambiar el nombre del flujo">
          <Icono n="editar" tam={13} /><span className="etq">Renombrar</span>
        </button>
        <button type="button" className={`boton chico fantasma ${panel === "clonar" ? "activo" : ""}`} aria-expanded={panel === "clonar"} aria-label="Clonar" onClick={() => abrir("clonar")} title="Hacer una copia independiente para editarla">
          <Icono n="copiar" tam={13} /><span className="etq">Clonar</span>
        </button>
        {variante === "cabecera" && (
          <>
            {f.tipo === "codigo" && <DeshacerCodigo flujo={flujo} />}
            <button type="button" className="boton chico fantasma" aria-label="Versiones" onClick={() => { setPanel(null); irA("versiones"); }} title="Historial de versiones: quién cambió qué, comparar y volver a cualquiera">
              <Icono n="reloj" tam={13} /><span className="etq">Versiones</span>
            </button>
            <button type="button" className={`boton chico fantasma ${cruzar === "comparar" ? "activo" : ""}`} aria-label="Comparar con otro flujo" onClick={() => { setPanel(null); setCruzar("comparar"); }} title="Comparar con otro flujo: pasos, conexiones y configuración">
              <Icono n="capas" tam={13} /><span className="etq">Comparar</span>
            </button>
            <button type="button" className={`boton chico fantasma ${cruzar === "traer" ? "activo" : ""}`} aria-label="Traer pasos de otro flujo" onClick={() => { setPanel(null); setCruzar("traer"); }} title="Copiar pasos de otro flujo a este">
              <Icono n="mas_circulo" tam={13} /><span className="etq">Traer pasos</span>
            </button>
          </>
        )}
        {borrable ? (
          <button type="button" className={`boton chico fantasma af-borrar ${panel === "borrar" ? "activo" : ""}`} aria-expanded={panel === "borrar"} aria-label="Borrar" onClick={() => abrir("borrar")} title="Borrar el flujo con sus corridas, entorno y archivos">
            <Icono n="papelera" tam={13} /><span className="etq">Borrar</span>
          </button>
        ) : (
          <span className="af-fijo" title="Es un flujo de producción original: no se borra. Sus copias sí.">
            <Icono n="candado" tam={12} /><span className="etq">Original</span>
          </span>
        )}
      </div>
      <AnimatePresence>{contenido}</AnimatePresence>
      {variante === "cabecera" && <DialogoCruzar abierto={cruzar !== null} cerrar={() => setCruzar(null)} flujo={flujo} modo={cruzar ?? "comparar"} />}
    </div>
  );
}
