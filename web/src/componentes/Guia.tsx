import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { GLOSARIO } from "../lenguaje.ts";
import { PASOS_GUIA, usarGuia, type PasoGuia } from "../guia.ts";
import { pedirAlAsistente } from "./Tablero.tsx";

const control = (detail: Record<string, unknown>) => window.dispatchEvent(new CustomEvent("positiva:control", { detail }));

/** Abre el diálogo «Correr» del flujo abierto (el lienzo lo escucha). */
export function abrirCorrer(): void {
  const s = usar.getState();
  const deCodigo = s.flujos.find((f) => f.id === s.flujo)?.tipo === "codigo";
  s.irA("flujo");
  // PQRD y tutelas se corren desde su correspondencia; los demás, desde el diálogo «Correr» del lienzo.
  window.setTimeout(() => control(deCodigo ? { accion: "correspondencia" } : { accion: "editor", op: "abrir_correr" }), 450);
}

const TEXTOS: Record<PasoGuia, { titulo: string; texto: string }> = {
  conocer: { titulo: "Entiende cómo funciona", texto: "Un flujo es una receta que se repite sola: algo lo arranca (un correo, archivos, una hora, otro sistema), sus pasos hacen el trabajo con reglas e IA y al final queda un resultado que puedes revisar." },
  crear: { titulo: "Crea tu primer flujo", texto: "Lo más rápido es partir de una plantilla. También puedes armarlo a mano, paso a paso, o pedírselo a Vera con tus palabras." },
  correr: { titulo: "Córrelo una vez", texto: "Abre tu flujo y pulsa «Correr»: le das un correo, unos archivos o unos datos de prueba. Nada sale a sistemas reales." },
  revisar: { titulo: "Mira el resultado", texto: "En el historial ves qué entró, qué hizo cada paso, qué salió y por qué terminó así. Desde ahí también lo puedes reintentar." },
};

function Acciones({ paso }: { paso: PasoGuia }) {
  const irA = usar((s) => s.irA);
  const marcar = usarGuia((s) => s.marcar);
  if (paso === "conocer") return (
    <div className="gu-acciones">
      <button className="boton primario chico" onClick={() => marcar("conocer")}><Icono n="check" tam={12} />Entendido</button>
      <button className="boton chico fantasma" onClick={() => { marcar("conocer"); irA("plantillas"); }}>Ver ejemplos</button>
    </div>
  );
  if (paso === "crear") return (
    <div className="gu-acciones">
      <button className="boton primario chico" onClick={() => irA("plantillas")}><Icono n="plantilla" tam={12} />Desde una plantilla</button>
      <button className="boton chico" onClick={() => control({ accion: "nuevo_flujo" })}><Icono n="mas" tam={12} />A mano</button>
      <button className="boton chico" onClick={() => pedirAlAsistente("Quiero crear mi primer flujo. Pregúntame qué necesito automatizar y ármalo.")}><Icono n="chispa" tam={12} />Con Vera</button>
    </div>
  );
  if (paso === "correr") return (
    <div className="gu-acciones">
      <button className="boton primario chico" onClick={abrirCorrer}><Icono n="play" tam={12} />Correr el flujo abierto</button>
    </div>
  );
  return (
    <div className="gu-acciones">
      <button className="boton primario chico" onClick={() => irA("historial")}><Icono n="historial" tam={12} />Abrir el historial</button>
    </div>
  );
}

/** Los pasos de la guía con su avance; el primero sin hacer queda abierto. */
export function ContenidoGuia({ compacta = false }: { compacta?: boolean }) {
  const hechos = usarGuia((s) => s.hechos);
  const marcar = usarGuia((s) => s.marcar);
  const [glosario, setGlosario] = useState(false);
  const [elegido, setElegido] = useState<PasoGuia | null>(null);
  const siguiente = PASOS_GUIA.find((p) => !hechos.includes(p)) ?? null;
  const abierto = elegido ?? siguiente;
  const n = hechos.length;
  return (
    <div className={`gu-contenido ${compacta ? "compacta" : ""}`}>
      <div className="gu-avance" role="progressbar" aria-valuemin={0} aria-valuemax={PASOS_GUIA.length} aria-valuenow={n} aria-label="Avance de los primeros pasos">
        {PASOS_GUIA.map((p) => <i key={p} className={hechos.includes(p) ? "hecho" : ""} />)}
        <span>{n === PASOS_GUIA.length ? "Todo listo" : `${n} de ${PASOS_GUIA.length}`}</span>
      </div>
      <ol className="gu-pasos">
        {PASOS_GUIA.map((p, i) => {
          const hecho = hechos.includes(p);
          const esAbierto = abierto === p;
          return (
            <li key={p} className={`${hecho ? "hecho" : ""} ${esAbierto ? "abierto" : ""}`}>
              <button className="gu-paso" aria-expanded={esAbierto} onClick={() => setElegido(esAbierto ? null : p)}>
                <span className="gu-numero" aria-hidden>{hecho ? <Icono n="check" tam={13} strokeWidth={2.6} /> : i + 1}</span>
                <span className="crece">{TEXTOS[p].titulo}</span>
                {hecho && <span className="texto-3 diminuto">Hecho</span>}
              </button>
              {esAbierto && (
                <div className="gu-detalle">
                  <p>{TEXTOS[p].texto}</p>
                  <Acciones paso={p} />
                  {!hecho && p !== "conocer" && <button className="enlace gu-saltar" onClick={() => { marcar(p); setElegido(null); }}>Ya lo hice</button>}
                </div>
              )}
            </li>
          );
        })}
      </ol>
      {!siguiente && <p className="gu-fin"><Icono n="sello" tam={16} />Ya sabes lo esencial. Vera te ayuda con lo demás: pregúntale lo que quieras.</p>}
      <button className="enlace gu-glosario-boton" aria-expanded={glosario} onClick={() => setGlosario(!glosario)}>{glosario ? "Ocultar las palabras de la plataforma" : "¿Qué significa flujo, paso, ejecución…?"}</button>
      {glosario && (
        <dl className="gu-glosario">
          {GLOSARIO.map((g) => <div key={g.palabra}><dt>{g.palabra}</dt><dd>{g.explica}</dd></div>)}
        </dl>
      )}
    </div>
  );
}

/** La guía como panel flotante: se abre desde «Ayuda» o la pide Vera. */
export function PanelGuia() {
  const abierta = usarGuia((s) => s.abierta);
  const abrir = usarGuia((s) => s.abrir);
  const cerrar = usarGuia((s) => s.cerrar);
  const vista = usar((s) => s.vista);
  // En el inicio la guía ya está a la vista, en su tarjeta.
  const visible = abierta && vista !== "inicio";
  return (
    <AnimatePresence>
      {visible && (
        <motion.aside className="gu-panel" role="dialog" aria-label="Primeros pasos" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 12, transition: { duration: 0.12 } }} transition={{ type: "spring", stiffness: 420, damping: 38 }}>
          <header>
            <h2>Primeros pasos</h2>
            <button className="boton-mini" aria-label="Minimizar la guía" title="Minimizar (la abres de nuevo con el botón de ayuda)" onClick={() => abrir(false)}><Icono n="abajo" tam={15} /></button>
            <button className="boton-mini" aria-label="Cerrar la guía" title="Cerrar la guía" onClick={cerrar}><Icono n="cerrar" tam={14} /></button>
          </header>
          <ContenidoGuia compacta />
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
