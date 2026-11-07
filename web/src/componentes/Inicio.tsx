import { useEffect, useState } from "react";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { ESTADOS, arranqueDe, hace, nombreResultado, type EstadoLegible } from "../lenguaje.ts";
import type { Plantilla, RespuestaHistorial, TableroFlujo } from "../tipos.ts";
import { usarGuia } from "../guia.ts";
import { ContenidoGuia, abrirCorrer } from "./Guia.tsx";
import { MarcaEstado } from "./Historial.tsx";
import { DialogoPlantilla, TarjetaPlantilla } from "./Plantillas.tsx";
import { pedirAlAsistente } from "./Tablero.tsx";

const control = (detail: Record<string, unknown>) => window.dispatchEvent(new CustomEvent("positiva:control", { detail }));

function estadoDe(accion: string): EstadoLegible {
  if (accion === "en_cola" || accion === "procesando") return "en_curso";
  if (accion === "error" || accion === "cancelada" || accion === "colgado") return "fallo";
  if (accion === "manual" || accion === "aplazado") return "revisar";
  return "bien";
}

function medianoche(): string {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

/** El inicio: qué es, cómo se usa, qué pasó hoy y qué hacer ahora. */
export function VistaInicio() {
  const tablero = usar((s) => s.tablero);
  const cargarTablero = usar((s) => s.cargarTablero);
  const flujos = usar((s) => s.flujos);
  const sinLeer = usar((s) => s.sinLeer);
  const irA = usar((s) => s.irA);
  const cerrada = usarGuia((s) => s.cerrada);
  const cerrarGuia = usarGuia((s) => s.cerrar);
  const [hoy, setHoy] = useState<RespuestaHistorial | null>(null);
  const [plantillas, setPlantillas] = useState<Plantilla[]>([]);
  const [plantilla, setPlantilla] = useState<Plantilla | null>(null);
  const [pendientes, setPendientes] = useState<number | null>(null);

  useEffect(() => {
    const traer = () => {
      void cargarTablero().catch(() => undefined);
      void pedir<RespuestaHistorial>(`/api/historial?desde=${encodeURIComponent(medianoche())}&limite=6`).then(setHoy).catch(() => undefined);
    };
    traer();
    const t = setInterval(traer, 10_000);
    void pedir<Plantilla[]>("/api/plantillas").then(setPlantillas).catch(() => undefined);
    return () => clearInterval(t);
  }, [cargarTablero, flujos.length]);

  // Las aprobaciones pendientes, solo si la plataforma ya tiene ese paso.
  const conAprobaciones = usar((s) => s.aprobacionesDisponibles);
  useEffect(() => {
    if (!conAprobaciones) return;
    void pedir<unknown[] | { aprobaciones?: unknown[] }>("/api/aprobaciones").then((c) => {
      setPendientes((Array.isArray(c) ? c : c.aprobaciones ?? []).filter((x) => !(x as { estado?: string }).estado || (x as { estado?: string }).estado === "pendiente").length);
    }).catch(() => undefined);
  }, [conAprobaciones, sinLeer]);

  const e = hoy?.facetas.estados ?? {};
  const totalHoy = Object.values(e).reduce((a, b) => a + b, 0);

  return (
    <div className="vista pr-vista pr-inicio">
      <section className={`in-portada ${cerrada ? "sin-guia" : ""}`}>
        <div className="in-hero">
          <h1>Automatiza el trabajo que llega por correo, en archivos o desde otros sistemas</h1>
          <p className="in-bajada">Positiva IA corre flujos: recetas de pasos que leen, entienden con IA, revisan y dejan un resultado listo para revisar. Todo lo que escribiría en sistemas reales queda registrado aquí hasta que una persona lo habilite.</p>
          <ol className="in-recorrido" aria-label="Cómo funciona un flujo">
            <li>
              <span className="in-rec-n">1</span>
              <b>Algo lo arranca</b>
              <span>Un correo, archivos que subes, una hora fija o un sistema que lo llama.</span>
              <span className="in-rec-fichas" aria-hidden><Icono n="correo" tam={14} /><Icono n="adjunto" tam={14} /><Icono n="calendario" tam={14} /><Icono n="gancho" tam={14} /></span>
            </li>
            <li>
              <span className="in-rec-n">2</span>
              <b>Sus pasos hacen el trabajo</b>
              <span>Leen documentos, la IA extrae y decide, las reglas revisan, se guarda lo que sirve.</span>
              <span className="in-rec-fichas" aria-hidden><Icono n="documento" tam={14} /><Icono n="chispa" tam={14} /><Icono n="filtro" tam={14} /><Icono n="base" tam={14} /></span>
            </li>
            <li className="in-rec-sello">
              <span className="in-rec-n">3</span>
              <b>Queda un resultado</b>
              <span>Completado, para revisión de una persona o descartado, con el porqué.</span>
              <span className="sello sello-verde in-sello" aria-hidden><b>Listo</b><small>con su porqué</small></span>
            </li>
          </ol>
          <div className="in-crear">
            <button className="boton primario" onClick={() => irA("plantillas")}><Icono n="plantilla" tam={15} />Empezar con una plantilla</button>
            <button className="boton" onClick={() => control({ accion: "nuevo_flujo" })}><Icono n="mas" tam={15} />Crear a mano</button>
            <button className="boton in-vera" onClick={() => pedirAlAsistente("Quiero crear un flujo nuevo. Pregúntame lo necesario y ármalo.")}><Icono n="chispa" tam={15} />Pedírselo a Vera</button>
          </div>
        </div>
        {!cerrada && (
          <aside className="in-guia" aria-label="Primeros pasos">
            <header>
              <h2>Primeros pasos</h2>
              <button className="boton-mini" aria-label="Cerrar la guía" title="Cerrar la guía (vuelve con el botón de ayuda de arriba)" onClick={cerrarGuia}><Icono n="cerrar" tam={14} /></button>
            </header>
            <ContenidoGuia />
          </aside>
        )}
      </section>

      <section className="in-bloque in-hoy" aria-labelledby="in-hoy">
        <header>
          <h2 id="in-hoy">Hoy</h2>
          {(sinLeer > 0 || (pendientes ?? 0) > 0) && (
            <div className="in-atencion">
              {(pendientes ?? 0) > 0 && <button className="pastilla azul boton-pastilla" onClick={() => irA("aprobaciones")}><Icono n="persona" tam={12} />{pendientes} esperan tu aprobación</button>}
              {sinLeer > 0 && <button className="pastilla ambar boton-pastilla" onClick={() => irA("notificaciones")}><Icono n="campana" tam={12} />{sinLeer} notificación{sinLeer === 1 ? "" : "es"} sin leer</button>}
            </div>
          )}
        </header>
        {!hoy ? (
          <p className="texto-3">Contando lo de hoy…</p>
        ) : !totalHoy ? (
          <p className="in-hoy-frase">Hoy todavía no ha corrido nada. Elige un flujo de abajo y pulsa «Correr», o deja uno programado.</p>
        ) : (
          <>
            <p className="in-hoy-frase">
              Hoy corrieron <b>{totalHoy.toLocaleString("es-CO")}</b> ejecución{totalHoy === 1 ? "" : "es"}
              {(Object.keys(ESTADOS) as EstadoLegible[]).filter((k) => e[k]).map((k, i, xs) => (
                <span key={k}>{i === 0 ? ": " : i === xs.length - 1 ? " y " : ", "}<span className={`in-cifra tono-${ESTADOS[k].tono}`}>{e[k]} {(k === "en_curso" ? "en curso" : k === "bien" ? (e[k] === 1 ? "terminó bien" : "terminaron bien") : k === "revisar" ? (e[k] === 1 ? "quedó para revisar" : "quedaron para revisar") : e[k] === 1 ? "falló" : "fallaron")}</span></span>
              ))}.
            </p>
            <ul className="in-recientes">
              {hoy.corridas.map((c) => {
                const f = flujos.find((x) => x.id === c.flujo);
                return (
                  <li key={c.id}>
                    <button onClick={() => void usar.getState().abrirEnHistorial(c.id, c.flujo)}>
                      <MarcaEstado estado={c.estado} />
                      <span className="crece"><b>{c.titulo}</b><small>{f?.nombre ?? c.flujo}, {c.estado === "en_curso" ? "en curso" : c.continuada_en ? "aprobación decidida" : nombreResultado(c.desenlace, f?.tipo === "codigo").toLowerCase()}</small></span>
                      <span className="texto-3 diminuto">{hace(c.creada)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </section>

      <section className="in-bloque" aria-labelledby="in-flujos">
        <header>
          <h2 id="in-flujos">Tus flujos</h2>
          <button className="enlace" onClick={() => irA("tablero")}>Ver las cifras de cada uno</button>
        </header>
        {!tablero.length ? (
          <ul className="in-flujos">{[0, 1, 2].map((i) => <li key={i} className="in-flujo fantasma" aria-hidden />)}</ul>
        ) : (
          <ul className="in-flujos">
            {tablero.map((f) => <FilaFlujo key={f.id} f={f} />)}
          </ul>
        )}
      </section>

      {plantillas.length > 0 && (
        <section className="in-bloque" aria-labelledby="in-plantillas">
          <header>
            <h2 id="in-plantillas">Para empezar rápido</h2>
            <button className="enlace" onClick={() => irA("plantillas")}>Ver las {plantillas.length} plantillas</button>
          </header>
          <ul className="pl-rejilla compacta">
            {plantillas.filter((p) => p.disponible).slice(0, 3).map((p) => <TarjetaPlantilla key={p.id} p={p} abrir={() => setPlantilla(p)} compacta />)}
          </ul>
        </section>
      )}
      <DialogoPlantilla p={plantilla} cerrar={() => setPlantilla(null)} />
    </div>
  );
}

function FilaFlujo({ f }: { f: TableroFlujo }) {
  const cambiarFlujo = usar((s) => s.cambiarFlujo);
  const irA = usar((s) => s.irA);
  const info = usar((s) => s.flujos.find((x) => x.id === f.id));
  const a = arranqueDe(f.disparador);
  const u = f.ultimas[0];
  const estado: EstadoLegible | null = f.enCurso > 0 ? "en_curso" : u ? estadoDe(u.accion) : null;
  const ir = async (vista: "flujo" | "historial") => {
    await cambiarFlujo(f.id);
    irA(vista);
  };
  return (
    <li className="in-flujo">
      <button className="in-flujo-abrir" onClick={() => void ir("flujo")} title="Abrir el flujo en el lienzo">
        <span className="in-flujo-icono"><Icono n={a.icono} tam={17} /></span>
        <span className="in-flujo-texto">
          <b>{f.nombre}</b>
          <small>{a.nombre}{f.tipo === "codigo" ? ", motor de producción" : info?.version ? `, versión ${info.version}` : ""}</small>
        </span>
      </button>
      <span className="in-flujo-estado">
        {estado ? (
          <>
            <MarcaEstado estado={estado} />
            <span>
              {estado === "en_curso" ? `${f.enCurso} corriendo` : nombreResultado(u!.accion, f.tipo === "codigo")}
              {u && estado !== "en_curso" && <small className="texto-3">{hace(u.t)}, {f.corridas.toLocaleString("es-CO")} en total</small>}
            </span>
          </>
        ) : <span className="texto-3">Sin ejecuciones todavía</span>}
      </span>
      <span className="in-flujo-acciones">
        <button className="boton chico" onClick={() => { void cambiarFlujo(f.id).then(abrirCorrer); }}><Icono n="play" tam={12} />Correr</button>
        <button className="boton chico fantasma" onClick={() => void ir("historial")}><Icono n="historial" tam={12} />Historial</button>
      </span>
    </li>
  );
}
