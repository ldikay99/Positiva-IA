import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { usar } from "../estado.ts";
import { Icono, type NombreIcono } from "../iconos.tsx";
import { fechaHora, hace } from "../lenguaje.ts";
import type { Notificacion } from "../tipos.ts";
import { Cargando, EstadoPantalla } from "./Historial.tsx";

const TIPOS: Record<Notificacion["tipo"], { nombre: string; icono: NombreIcono; tono: string }> = {
  fallo: { nombre: "Fallos", icono: "alerta", tono: "rojo" },
  aprobacion: { nombre: "Aprobaciones", icono: "persona", tono: "azul" },
  conexion: { nombre: "Conexiones", icono: "llave", tono: "ambar" },
  recursos: { nombre: "Recursos", icono: "rayo", tono: "violeta" },
  programacion: { nombre: "Horarios", icono: "calendario", tono: "ambar" },
  alerta: { nombre: "Alertas", icono: "bandera", tono: "ambar" },
};

/** Lleva a donde se atiende la notificación y la marca como leída. */
export function useAtender() {
  const marcarLeida = usar((s) => s.marcarLeida);
  const abrirEnHistorial = usar((s) => s.abrirEnHistorial);
  const cambiarFlujo = usar((s) => s.cambiarFlujo);
  const irA = usar((s) => s.irA);
  return async (n: Notificacion) => {
    void marcarLeida(n.id);
    const a = n.accion;
    if (!a) return;
    if (a.vista === "historial" && a.corrida) return abrirEnHistorial(a.corrida, a.flujo);
    if (a.flujo && a.vista !== "conexiones" && a.vista !== "aprobaciones") await cambiarFlujo(a.flujo);
    irA(a.vista);
  };
}

function ItemNotificacion({ n, alAtender }: { n: Notificacion; alAtender: (n: Notificacion) => void }) {
  const marcarLeida = usar((s) => s.marcarLeida);
  const t = TIPOS[n.tipo] ?? TIPOS.fallo;
  return (
    <li className={`nt-item ${n.leida ? "leida" : ""}`}>
      <span className={`nt-icono tono-${t.tono}`}><Icono n={t.icono} tam={15} /></span>
      <div className="nt-cuerpo">
        <b>{n.titulo}</b>
        <p>{n.texto}</p>
        <div className="nt-pie">
          <span className="texto-3" title={fechaHora(n.t)}>{hace(n.t)}</span>
          {n.accion && <button className="enlace" onClick={() => alAtender(n)}>{n.accion.etiqueta}</button>}
          {!n.leida && <button className="enlace nt-leida" onClick={() => void marcarLeida(n.id)}>Marcar como leída</button>}
        </div>
      </div>
      {!n.leida && <span className="nt-nueva" aria-label="Sin leer" />}
    </li>
  );
}

/** La campana de la barra: cuántas sin leer y las últimas, a un clic. */
export function Campana() {
  const lista = usar((s) => s.notificaciones);
  const sinLeer = usar((s) => s.sinLeer);
  const cargar = usar((s) => s.cargarNotificaciones);
  const marcarTodas = usar((s) => s.marcarTodasLeidas);
  const irA = usar((s) => s.irA);
  const atender = useAtender();
  const [abierta, setAbierta] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const boton = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void cargar().catch(() => undefined);
  }, [cargar]);

  useLayoutEffect(() => {
    if (!abierta) return;
    const r = boton.current?.getBoundingClientRect();
    if (r) setPos({ top: r.bottom + 8, right: Math.max(8, window.innerWidth - r.right - 8) });
  }, [abierta]);

  useEffect(() => {
    if (!abierta) return;
    const fuera = (e: PointerEvent) => {
      if (!panel.current?.contains(e.target as Node) && !boton.current?.contains(e.target as Node)) setAbierta(false);
    };
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setAbierta(false);
        boton.current?.focus();
      }
    };
    window.addEventListener("pointerdown", fuera, true);
    window.addEventListener("keydown", tecla);
    const t = setTimeout(() => panel.current?.querySelector<HTMLElement>("button")?.focus(), 30);
    return () => {
      clearTimeout(t);
      window.removeEventListener("pointerdown", fuera, true);
      window.removeEventListener("keydown", tecla);
    };
  }, [abierta]);

  const ultimas = lista.slice(0, 8);
  return (
    <>
      <button ref={boton} className={`boton-mini grande nt-campana ${abierta ? "activo" : ""}`} aria-haspopup="dialog" aria-expanded={abierta} aria-label={sinLeer ? `Notificaciones: ${sinLeer} sin leer` : "Notificaciones"} title="Notificaciones" onClick={() => setAbierta(!abierta)}>
        <Icono n="campana" tam={18} />
        {sinLeer > 0 && <span className="nt-cuenta" aria-hidden>{sinLeer > 99 ? "99+" : sinLeer}</span>}
      </button>
      {createPortal(
        <AnimatePresence>
          {abierta && pos && (
            <motion.div ref={panel} className="nt-panel" role="dialog" aria-label="Notificaciones" style={{ top: pos.top, right: pos.right }} initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4, transition: { duration: 0.1 } }} transition={{ duration: 0.16 }}>
              <header>
                <h2>Notificaciones</h2>
                {sinLeer > 0 && <button className="enlace" onClick={() => void marcarTodas()}>Marcar todo como leído</button>}
              </header>
              {ultimas.length ? (
                <ul className="nt-lista">
                  {ultimas.map((n) => <ItemNotificacion key={n.id} n={n} alAtender={(x) => { setAbierta(false); void atender(x); }} />)}
                </ul>
              ) : (
                <div className="nt-vacio">
                  <Icono n="campana" tam={20} />
                  <p>Nada pendiente. Aquí aparecen las ejecuciones que fallan, lo que espera tu aprobación, las conexiones con problemas y los avisos de recursos.</p>
                </div>
              )}
              <footer><button className="boton chico fantasma" onClick={() => { setAbierta(false); irA("notificaciones"); }}>Ver todas<Icono n="derecha" tam={12} /></button></footer>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}

export function VistaNotificaciones() {
  const lista = usar((s) => s.notificaciones);
  const sinLeer = usar((s) => s.sinLeer);
  const cargar = usar((s) => s.cargarNotificaciones);
  const marcarTodas = usar((s) => s.marcarTodasLeidas);
  const atender = useAtender();
  const [filtro, setFiltro] = useState<"" | "sin_leer" | Notificacion["tipo"]>("");
  const [listo, setListo] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    cargar().then(() => setListo(true)).catch((e: Error) => setError(e.message));
  }, [cargar]);

  const visibles = lista.filter((n) => !filtro || (filtro === "sin_leer" ? !n.leida : n.tipo === filtro));
  const tipos = (Object.keys(TIPOS) as Notificacion["tipo"][]).filter((t) => lista.some((n) => n.tipo === t));
  return (
    <div className="vista pr-vista notificaciones">
      <header className="vista-cabeza">
        <div className="crece">
          <h1>Notificaciones</h1>
          <p className="texto-2">Lo que necesita tu atención en todos los flujos: ejecuciones que fallan, aprobaciones pendientes, conexiones con problemas y avisos de recursos. Llegan en vivo.</p>
        </div>
        {sinLeer > 0 && <button className="boton" onClick={() => void marcarTodas()}><Icono n="check" tam={14} />Marcar todo como leído</button>}
      </header>
      {lista.length > 0 && (
        <div className="filtros" role="group" aria-label="Mostrar">
          <button className={!filtro ? "activo" : ""} aria-pressed={!filtro} onClick={() => setFiltro("")}>Todas <span>{lista.length}</span></button>
          <button className={filtro === "sin_leer" ? "activo" : ""} aria-pressed={filtro === "sin_leer"} onClick={() => setFiltro(filtro === "sin_leer" ? "" : "sin_leer")}>Sin leer <span>{sinLeer}</span></button>
          {tipos.map((t) => (
            <button key={t} className={filtro === t ? "activo" : ""} aria-pressed={filtro === t} onClick={() => setFiltro(filtro === t ? "" : t)}>{TIPOS[t].nombre} <span>{lista.filter((n) => n.tipo === t).length}</span></button>
          ))}
        </div>
      )}
      {error ? (
        <EstadoPantalla icono="alerta" tono="rojo" titulo="No se pudieron cargar las notificaciones" texto={error} accion={<button className="boton" onClick={() => void cargar().catch(() => undefined)}>Intentar de nuevo</button>} />
      ) : !listo && !lista.length ? (
        <Cargando filas={4} />
      ) : !visibles.length ? (
        <EstadoPantalla icono="campana" titulo={lista.length ? "Nada con este filtro" : "Todo en orden"} texto={lista.length ? "Prueba con otro filtro." : "Cuando una ejecución falle, un flujo espere tu aprobación o una conexión tenga problemas, lo verás aquí y en la campana."} />
      ) : (
        <ul className="nt-lista nt-grande">
          {visibles.map((n) => <ItemNotificacion key={n.id} n={n} alAtender={(x) => void atender(x)} />)}
        </ul>
      )}
    </div>
  );
}
