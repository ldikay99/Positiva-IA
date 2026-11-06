import { useEffect } from "react";
import { motion } from "motion/react";
import { usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { NOMBRES_CORTOS_DESENLACE, TONO_DESENLACE } from "../progreso.ts";
import type { TableroFlujo } from "../tipos.ts";

/** Pide algo al asistente: abre la burbuja con el texto ya escrito y lo envía. */
export function pedirAlAsistente(texto: string): void {
  window.dispatchEvent(new CustomEvent("positiva:asistente", { detail: { texto } }));
}

function hace(t: number): string {
  const s = (Date.now() - t) / 1000;
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return new Date(t).toLocaleDateString("es-CO", { day: "numeric", month: "short" });
}

function Actividad({ dias }: { dias: TableroFlujo["porDia"] }) {
  const max = Math.max(1, ...dias.map((d) => d.n));
  return (
    <div className="tab-actividad" aria-label="Corridas por día">
      {dias.map((d) => (
        <span key={d.dia} title={`${d.dia}: ${d.n} corridas, ${d.radicados} radicadas`}>
          <i style={{ height: `${(d.n / max) * 100}%` }}><b style={{ height: `${d.n ? (d.radicados / d.n) * 100 : 0}%` }} /></i>
        </span>
      ))}
      {!dias.length && <em>Sin corridas todavía</em>}
    </div>
  );
}

function TarjetaFlujo({ f, i }: { f: TableroFlujo; i: number }) {
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
        <span className={`tab-tipo ${f.tipo}`}>{f.tipo === "codigo" ? "Motor del job" : `Creado aquí · v${info?.version ?? 1}`}</span>
        {f.enCurso > 0 && <span className="tab-vivo"><span className="girando" />{f.enCurso} corriendo</span>}
      </header>
      <h2>{f.nombre}</h2>
      <p className="tab-desc">{info?.descripcion}</p>
      <div className="tab-cifras">
        <div className="tab-anillo" style={{ ["--pct" as string]: `${pct}%` }} title={`${radicados} de ${terminadas} terminadas se radican`}>
          <span><b>{pct}</b><small>%</small></span>
        </div>
        <dl>
          <div><dt>Corridas</dt><dd>{f.corridas.toLocaleString("es-CO")}</dd></div>
          <div><dt>A manual</dt><dd>{manual}</dd></div>
          <div><dt>Errores</dt><dd className={errores ? "malo" : ""}>{errores}</dd></div>
          <div><dt>Mediana</dt><dd>{f.segundosMediana ? `${Math.round(f.segundosMediana)} s` : "—"}</dd></div>
        </dl>
      </div>
      <Actividad dias={f.porDia} />
      {!!f.ultimas.length && (
        <ul className="tab-ultimas">
          {f.ultimas.slice(0, 4).map((u) => (
            <li key={u.id}>
              <button onClick={async () => { await abrir(); void verCorrida(u.id); }}>
                <span className={`punto tono-${TONO_DESENLACE[u.accion] ?? "gris"}`} />
                <span className="tab-caso">{u.caso}</span>
                <span className="tab-accion">{NOMBRES_CORTOS_DESENLACE[u.accion] ?? u.accion}{u.motivo ? ` · ${u.motivo.replace(/_/g, " ").toLowerCase()}` : ""}</span>
                <span className="tab-cuando">{hace(u.t)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <footer>
        <button className="boton primario chico" onClick={() => void abrir()}><Icono n="flujo" tam={13} />Abrir el flujo</button>
        <button className="boton fantasma chico" onClick={() => { void cambiarFlujo(f.id); pedirAlAsistente(`Revisa las últimas corridas del flujo «${f.nombre}» (${f.id}) y dime qué está fallando y cómo mejorarlo.`); }}>
          <Icono n="chispa" tam={13} />Revisar con IA
        </button>
      </footer>
    </motion.article>
  );
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
        <div>
          <h1>Tus flujos</h1>
          <p>
            {flujos.length} flujo{flujos.length === 1 ? "" : "s"} · {total.toLocaleString("es-CO")} corridas · {radicados.toLocaleString("es-CO")} radicadas. Cada flujo corre con su propio motor y sus propias corridas.
          </p>
        </div>
        <button className="boton primario tab-crear" onClick={() => pedirAlAsistente("Quiero crear un flujo nuevo. Pregúntame lo necesario y ármalo.")}>
          <Icono n="chispa" tam={15} />Crear un flujo con IA
        </button>
      </section>
      <div className="tab-rejilla">
        {tablero.map((f, i) => <TarjetaFlujo key={f.id} f={f} i={i} />)}
        <motion.button className="tab-nuevo" onClick={() => pedirAlAsistente("Quiero crear un flujo nuevo. Pregúntame lo necesario y ármalo.")} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }}>
          <span><Icono n="mas" tam={22} /></span>
          <b>Nuevo flujo</b>
          <small>Descríbelo en el chat: la IA lo arma, lo valida y lo prueba con correos reales.</small>
        </motion.button>
      </div>
    </div>
  );
}
