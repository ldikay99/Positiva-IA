import { useEffect } from "react";
import { motion } from "motion/react";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { NOMBRES_CORTOS_DESENLACE, TONO_DESENLACE, motivoLegible } from "../progreso.ts";
import type { TableroFlujo } from "../tipos.ts";
import { textoConsumo } from "./IA.tsx";
import { nombreResultado } from "../lenguaje.ts";
import { AccionesFlujo } from "./AccionesFlujo.tsx";

/** Pide algo al asistente: abre la burbuja con el texto ya escrito y lo envía. */
export function pedirAlAsistente(texto: string): void {
  window.dispatchEvent(new CustomEvent("positiva:asistente", { detail: { texto } }));
}

const COMPACTO = new Intl.NumberFormat("es-CO", { notation: "compact", maximumFractionDigits: 1 });
/** 1.844.762 tokens se lee «1,8 M»: la cifra entera queda en el globo. */
function tokensCompactos(c: { tokens_entrada?: number; tokens_salida?: number }): string {
  const t = Number(c.tokens_entrada ?? 0) + Number(c.tokens_salida ?? 0);
  return t ? COMPACTO.format(t) : "—";
}

function hace(t: number): string {
  const s = (Date.now() - t) / 1000;
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return new Date(t).toLocaleDateString("es-CO", { day: "numeric", month: "short" });
}

function Actividad({ dias, deCodigo }: { dias: TableroFlujo["porDia"]; deCodigo: boolean }) {
  const max = Math.max(1, ...dias.map((d) => d.n));
  if (!dias.length) return <p className="tab-sin">Todavía no ha corrido. Ábrelo y pulsa «Correr» para empezar.</p>;
  return (
    <figure className="tab-grafico">
      <figcaption>
        Ejecuciones por día
        <span><i className="clave-radicadas" />{deCodigo ? "radicadas" : "completadas"}</span>
        <span><i className="clave-otras" />otro resultado</span>
      </figcaption>
      <div className="tab-actividad" role="img" aria-label={dias.map((d) => `${d.dia}: ${d.n} ejecuciones, ${d.radicados} ${deCodigo ? "radicadas" : "completadas"}`).join("; ")}>
        {dias.map((d) => (
          <span key={d.dia} title={`${d.dia}: ${d.n} ejecuciones, ${d.radicados} ${deCodigo ? "radicadas" : "completadas"}`}>
            <i style={{ height: `${(d.n / max) * 100}%` }}><b style={{ height: `${d.n ? (d.radicados / d.n) * 100 : 0}%` }} /></i>
          </span>
        ))}
      </div>
    </figure>
  );
}

/**
 * Los trabajadores del flujo ahora: cuántos, CPU y memoria, y avisos suaves (memoria alta, una corrida
 * que se acerca a su tiempo máximo, un arranque que no termina). Un trabajador ocupado se puede reiniciar.
 */
function SaludDelFlujo({ f, recargar }: { f: TableroFlujo; recargar: () => void }) {
  const avisar = usar((s) => s.avisar);
  const s = f.salud;
  if (!s || (!s.procesos && !s.en_cola && !s.error_inicio)) return null;
  const reiniciar = async (trabajador: number) => {
    try {
      await pedir(`/api/flujos/${encodeURIComponent(f.id)}/trabajadores/${trabajador}/reiniciar`, { method: "POST" });
      avisar("Trabajador reiniciado: lo que atendía queda con el motivo y otro sigue con la cola");
      recargar();
    } catch (e) {
      avisar((e as Error).message, "error");
    }
  };
  return (
    <div className="tab-salud" aria-label="Trabajadores del flujo">
      <div className="tab-salud-cifras">
        <span><b>{s.procesos}</b> de {s.trabajadores_max} trabajador{s.trabajadores_max === 1 ? "" : "es"}{s.ocupados ? `, ${s.ocupados} ocupado${s.ocupados === 1 ? "" : "s"}` : ""}</span>
        {s.en_cola > 0 && <span><b>{s.en_cola}</b> en cola (de {s.cola_max})</span>}
        {s.cpu != null && <span title="Suma de sus trabajadores; 100 % es un núcleo entero">CPU <b>{s.cpu} %</b></span>}
        {s.memoria_mb != null && <span>Memoria <b>{s.memoria_mb.toLocaleString("es-CO")} MB</b></span>}
      </div>
      {s.error_inicio && <div className="tab-salud-alerta rojo"><Icono n="alerta" tam={13} /><span>El motor no arrancó: {s.error_inicio.slice(0, 160)}</span></div>}
      {s.alertas.slice(0, 3).map((a) => (
        <div key={`${a.trabajador}-${a.texto}`} className="tab-salud-alerta">
          <Icono n="alerta" tam={13} />
          <span>Trabajador {a.trabajador}: {a.texto}</span>
          {a.ocupado && <button className="boton chico fantasma" onClick={() => void reiniciar(a.trabajador)} title="Lo detiene: lo que atendía queda en error con el motivo y arranca otro">Reiniciar</button>}
        </div>
      ))}
    </div>
  );
}

function TarjetaFlujo({ f, i }: { f: TableroFlujo; i: number }) {
  const recargar = usar((s) => s.cargarTablero);
  const cambiarFlujo = usar((s) => s.cambiarFlujo);
  const irA = usar((s) => s.irA);
  const verCorrida = usar((s) => s.verCorrida);
  const flujos = usar((s) => s.flujos);
  const info = flujos.find((x) => x.id === f.id);
  const terminadas = f.corridas - f.enCurso;
  const radicados = f.desenlaces.radicado ?? 0;
  const manual = (f.desenlaces.manual ?? 0) + (f.desenlaces.aplazado ?? 0);
  const errores = f.desenlaces.error ?? 0;
  const pct = terminadas ? Math.round((radicados / terminadas) * 100) : 0;
  const abrir = async () => {
    await cambiarFlujo(f.id);
    irA("flujo");
  };
  return (
    <motion.article className="tab-flujo" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05, type: "spring", stiffness: 380, damping: 32 }}>
      <header>
        <span className={`tab-tipo ${f.tipo}`}>{f.tipo === "codigo" ? (f.copia_de ? `Copia del motor de ${f.base === "tutelas" ? "tutelas" : "PQRD"}` : "Motor de producción") : `Creado aquí, versión ${info?.version ?? 1}`}</span>
        {f.enCurso > 0 && <span className="tab-vivo"><span className="girando" />{f.enCurso} corriendo</span>}
      </header>
      <h2>{f.nombre}</h2>
      <p className="tab-desc">{info?.descripcion}</p>
      <div className="tab-cifras">
        <div className="tab-anillo-caja">
          <div className="tab-anillo" style={{ ["--pct" as string]: `${pct}%` }} role="img" aria-label={`${pct} % ${f.tipo === "codigo" ? "radicado" : "completado"}: ${radicados} de ${terminadas} terminadas`}>
            <span><b>{pct}</b><small>%</small></span>
          </div>
          <small>{f.tipo === "codigo" ? "radicado" : "completado"}</small>
        </div>
        <dl>
          <div><dt>Ejecuciones</dt><dd>{f.corridas.toLocaleString("es-CO")}</dd></div>
          <div><dt>A revisión manual</dt><dd>{manual}</dd></div>
          <div><dt>Con error</dt><dd className={errores ? "malo" : ""}>{errores}</dd></div>
          <div><dt>Tiempo típico</dt><dd>{f.segundosMediana ? `${Math.round(f.segundosMediana)} s` : "—"}</dd></div>
          {f.tipo === "codigo" && <div title="El 5 % más lento de las ejecuciones tarda al menos esto"><dt>Las más lentas</dt><dd>{f.segundosP95 ? `${Math.round(f.segundosP95)} s` : "—"}</dd></div>}
          {f.tipo === "codigo" && <div title="Consultas del agente a la IA por correo, en promedio"><dt>Consultas a la IA</dt><dd>{f.vueltasMedia ? f.vueltasMedia.toLocaleString("es-CO", { maximumFractionDigits: 1 }) : "—"}</dd></div>}
          <div title={textoConsumo(f.consumo ?? { tokens_entrada: f.tokens, tokens_salida: 0 }) || "Sin consumo de IA"}><dt>Tokens de IA</dt><dd>{tokensCompactos(f.consumo ?? { tokens_entrada: f.tokens, tokens_salida: 0 })}</dd></div>
        </dl>
      </div>
      <SaludDelFlujo f={f} recargar={() => void recargar()} />
      <Actividad dias={f.porDia} deCodigo={f.tipo === "codigo"} />
      {!!f.ultimas.length && (
        <ul className="tab-ultimas" aria-label="Últimas ejecuciones">
          {f.ultimas.slice(0, 4).map((u) => (
            <li key={u.id}>
              <button onClick={async () => { await abrir(); void verCorrida(u.id); }}>
                <span className={`punto tono-${TONO_DESENLACE[u.accion] ?? "gris"}`} />
                <span className="tab-caso">{u.caso}</span>
                <span className="tab-accion">{f.tipo === "codigo" ? NOMBRES_CORTOS_DESENLACE[u.accion] ?? u.accion : nombreResultado(u.accion, false)}{u.motivo ? <span className="texto-3">: {motivoLegible(u.motivo).toLowerCase()}</span> : null}</span>
                <span className="tab-cuando">{hace(u.t)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <footer>
        <button className="boton primario chico" onClick={() => void abrir()}><Icono n="flujo" tam={13} />Abrir el flujo</button>
        <button className="boton fantasma chico" onClick={() => { void cambiarFlujo(f.id); pedirAlAsistente(`Revisa las últimas ejecuciones del flujo «${f.nombre}» (${f.id}) y dime qué está fallando y cómo mejorarlo.`); }}>
          <Icono n="chispa" tam={13} />Revisar con IA
        </button>
      </footer>
      <AccionesFlujo flujo={f.id} variante="tarjeta" />
    </motion.article>
  );
}

/** Abre el diálogo de flujo nuevo (lo escucha App). */
function nuevoFlujo(): void {
  window.dispatchEvent(new CustomEvent("positiva:control", { detail: { accion: "nuevo_flujo" } }));
}

export function VistaTablero() {
  const tablero = usar((s) => s.tablero);
  const cargar = usar((s) => s.cargarTablero);
  const flujos = usar((s) => s.flujos);

  useEffect(() => {
    void cargar();
    const t = setInterval(() => void cargar(), 8000);
    return () => clearInterval(t);
  }, [cargar, flujos.length]);

  const total = tablero.reduce((a, f) => a + f.corridas, 0);
  const radicados = tablero.reduce((a, f) => a + (f.desenlaces.radicado ?? 0), 0);

  return (
    <div className="vista tablero">
      <section className="tab-portada">
        <p>
          {total
            ? <>Tus {flujos.length} flujos llevan <b>{total.toLocaleString("es-CO")} ejecuciones</b> y <b>{radicados.toLocaleString("es-CO")}</b> terminaron completas (en PQRD y tutelas, radicadas). Abre uno para correrlo o revisar lo que hizo.</>
            : <>Tienes {flujos.length} flujo{flujos.length === 1 ? "" : "s"}. Abre uno y pulsa «Correr» para empezar.</>}
        </p>
        <div className="tab-acciones">
          <button className="boton tab-crear secundario" onClick={nuevoFlujo}>
            <Icono n="mas" tam={15} />Nuevo flujo
          </button>
          <button className="boton primario tab-crear" onClick={() => pedirAlAsistente("Quiero crear un flujo nuevo. Pregúntame lo necesario y ármalo.")}>
            <Icono n="chispa" tam={15} />Crear con Vera
          </button>
        </div>
      </section>
      <div className="tab-rejilla">
        {tablero.map((f, i) => <TarjetaFlujo key={f.id} f={f} i={i} />)}
        <motion.button className="tab-nuevo" onClick={nuevoFlujo} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }}>
          <span><Icono n="mas" tam={22} /></span>
          <b>Nuevo flujo</b>
          <small>Elige cómo arranca y ármalo paso a paso en el lienzo, parte de una plantilla o pídeselo a Vera.</small>
        </motion.button>
      </div>
    </div>
  );
}
