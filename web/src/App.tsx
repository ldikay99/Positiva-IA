import { useCallback, useEffect, useRef, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { AnimatePresence, motion } from "motion/react";
import { guardarLocal, leerLocal, usar, type Vista } from "./estado.ts";
import { Icono, type NombreIcono } from "./iconos.tsx";
import { Bandeja } from "./componentes/Bandeja.tsx";
import { Lienzo } from "./componentes/Lienzo.tsx";
import { Inspector } from "./componentes/Inspector.tsx";
import { Dock } from "./componentes/Dock.tsx";
import { MenuContextual, type Opcion } from "./componentes/MenuContextual.tsx";
import { VistaAdjuntos } from "./componentes/Adjuntos.tsx";
import { VistaConfiguracion, VistaCorridas, VistaHerramientas, VistaLogs, VistaProveedores } from "./componentes/Vistas.tsx";

const NAVEGACION: { id: Vista; nombre: string; icono: NombreIcono; titulo: string }[] = [
  { id: "flujo", nombre: "Flujo", icono: "flujo", titulo: "Radicación de PQRD" },
  { id: "adjuntos", nombre: "Adjuntos", icono: "documento", titulo: "Adjuntos, hoja por hoja" },
  { id: "corridas", nombre: "Corridas", icono: "corridas", titulo: "Corridas frente a producción" },
  { id: "logs", nombre: "Logs", icono: "logs", titulo: "Todo lo que pasa, en vivo" },
  { id: "herramientas", nombre: "Herramientas", icono: "herramientas", titulo: "Herramientas de la IA" },
  { id: "proveedores", nombre: "Sistemas", icono: "proveedores", titulo: "Sistemas que consulta" },
  { id: "configuracion", nombre: "Ajustes", icono: "ajustes", titulo: "Ajustes del motor" },
];

const CLAVE_MEDIDAS = "positiva-ia:medidas:v1";
type Medidas = { bandeja: number; dock: number; inspector: number };
const MEDIDAS: Medidas = { bandeja: 340, dock: Math.round(Math.max(200, Math.min(300, window.innerHeight * 0.3))), inspector: 470 };

function useAncho(): number {
  const [ancho, setAncho] = useState(window.innerWidth);
  useEffect(() => {
    let espera = 0;
    const f = () => {
      cancelAnimationFrame(espera);
      espera = requestAnimationFrame(() => setAncho(window.innerWidth));
    };
    window.addEventListener("resize", f);
    return () => window.removeEventListener("resize", f);
  }, []);
  return ancho;
}

/**
 * Divisor arrastrable. Durante el arrastre solo cambia una variable CSS del
 * contenedor (sin pasar por React), así el movimiento va al ritmo del monitor.
 */
function Divisor({ contenedor, medida, eje, min, max, calcular, alSoltar, titulo }: { contenedor: React.RefObject<HTMLElement | null>; medida: keyof Medidas; eje: "x" | "y"; min: number; max: () => number; calcular: (e: PointerEvent, caja: DOMRect) => number; alSoltar: (v: number) => void; titulo: string }) {
  const empezar = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = contenedor.current;
    if (!el || e.button !== 0) return;
    e.preventDefault();
    const caja = el.getBoundingClientRect();
    const asa = e.currentTarget;
    asa.setPointerCapture(e.pointerId);
    document.body.classList.add("arrastrando", eje === "x" ? "arrastrando-x" : "arrastrando-y");
    asa.classList.add("activo");
    let ultimo = 0;
    let cuadro = 0;
    const mover = (m: PointerEvent) => {
      ultimo = Math.round(Math.max(min, Math.min(max(), calcular(m, caja))));
      cancelAnimationFrame(cuadro);
      cuadro = requestAnimationFrame(() => el.style.setProperty(`--${medida}`, `${ultimo}px`));
    };
    const soltar = () => {
      asa.removeEventListener("pointermove", mover);
      asa.removeEventListener("pointerup", soltar);
      asa.removeEventListener("pointercancel", soltar);
      document.body.classList.remove("arrastrando", "arrastrando-x", "arrastrando-y");
      asa.classList.remove("activo");
      if (ultimo) alSoltar(ultimo);
    };
    asa.addEventListener("pointermove", mover);
    asa.addEventListener("pointerup", soltar);
    asa.addEventListener("pointercancel", soltar);
  };
  return <div className={`divisor divisor-${eje} divisor-${medida}`} onPointerDown={empezar} onDoubleClick={() => alSoltar(MEDIDAS[medida])} role="separator" aria-orientation={eje === "x" ? "vertical" : "horizontal"} title={`${titulo} · doble clic para restablecer`} />;
}

function Avisos() {
  const avisos = usar((s) => s.avisos);
  const cerrar = usar((s) => s.cerrarAviso);
  return (
    <div className="avisos" role="status">
      <AnimatePresence initial={false}>
        {avisos.map((a) => (
          <motion.div key={a.id} layout className={`aviso ${a.tono}`} initial={{ opacity: 0, x: 24, scale: 0.97 }} animate={{ opacity: 1, x: 0, scale: 1 }} exit={{ opacity: 0, x: 24, transition: { duration: 0.15 } }} transition={{ type: "spring", stiffness: 500, damping: 36 }}>
            <span className="aviso-icono"><Icono n={a.tono === "error" ? "alerta" : a.tono === "info" ? "info" : "ok"} tam={14} /></span>
            <span className="crece">{a.texto}</span>
            <button aria-label="Cerrar" onClick={() => cerrar(a.id)}><Icono n="cerrar" tam={12} /></button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function BarraSuperior({ alternarBandeja, bandejaVisible }: { alternarBandeja: () => void; bandejaVisible: boolean }) {
  const motor = usar((s) => s.motor);
  const configuracion = usar((s) => s.configuracion);
  const irA = usar((s) => s.irA);
  const vista = usar((s) => s.vista);
  const [menu, setMenu] = useState<{ x: number; y: number; opciones: Opcion[] } | null>(null);
  const ocupados = motor?.trabajadores.filter((t) => t.corrida).length ?? 0;
  const listos = motor?.trabajadores.filter((t) => t.listo).length ?? 0;
  const ajustes = Object.keys(configuracion?.parametros ?? {}).length + Object.keys(configuracion?.entorno ?? {}).length;
  const reglas = configuracion?.reglas.filter((r) => r.activa).length ?? 0;
  return (
    <header className="barra">
      {vista === "flujo" && (
        <button className={`boton-mini grande ${bandejaVisible ? "activo" : ""}`} aria-label="Mostrar u ocultar la bandeja" title="Bandeja" onClick={alternarBandeja}>
          <Icono n="lado" tam={17} />
        </button>
      )}
      <AnimatePresence mode="wait" initial={false}>
        <motion.h1 key={vista} className="barra-titulo" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}>
          {NAVEGACION.find((n) => n.id === vista)?.titulo}
        </motion.h1>
      </AnimatePresence>
      <button
        className="selector-flujo"
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setMenu({
            x: r.left,
            y: r.bottom + 6,
            opciones: [
              { titulo: "Flujos de IA" },
              { etiqueta: "Radicación de PQRD", icono: "correo", marcado: true, alHacer: () => irA("flujo") },
              { separador: true },
              { etiqueta: "Nuevo flujo de IA", icono: "mas", deshabilitado: true, pista: "La plataforma admite más flujos: cada uno trae su motor y su catálogo de nodos.", alHacer: () => undefined },
            ],
          });
        }}
      >
        <span>Flujo</span>
        <b>PQRD</b>
        <Icono n="abajo" tam={12} />
      </button>
      <span className="pastilla verde diminuto solo-ancho" title="Nada se escribe en SGDEA ni en el buzón real"><Icono n="candado" tam={11} />Solo lectura</span>
      <span className="espacio" />
      <div className="estado-barra">
        {motor?.errorInicio ? (
          <span className="pastilla rojo" title={motor.errorInicio}><span className="punto" />Motor detenido</span>
        ) : (
          <span className={`pastilla ${listos ? (ocupados ? "azul" : "verde") : "ambar"}`} title="Trabajadores del motor ocupados / disponibles">
            {ocupados ? <span className="girando" /> : <span className="punto" />}
            {listos ? `${ocupados} de ${listos} procesando` : "Arrancando motor"}
          </span>
        )}
        {(motor?.enCola.length ?? 0) > 0 && <span className="pastilla azul">{motor?.enCola.length} en cola</span>}
        <button className="pastilla violeta boton-pastilla solo-ancho" onClick={() => irA("configuracion")} title="Modelo de Vertex AI"><Icono n="chispa" tam={12} />{configuracion?.ia.modelo}</button>
        {ajustes + reglas > 0 && <button className="pastilla ambar boton-pastilla solo-ancho" onClick={() => irA("configuracion")}>{ajustes} ajuste{ajustes === 1 ? "" : "s"} · {reglas} regla{reglas === 1 ? "" : "s"}</button>}
      </div>
      {menu && <MenuContextual x={menu.x} y={menu.y} opciones={menu.opciones} cerrar={() => setMenu(null)} />}
    </header>
  );
}

function Riel() {
  const vista = usar((s) => s.vista);
  const irA = usar((s) => s.irA);
  const motor = usar((s) => s.motor);
  const conectado = usar((s) => s.conectado);
  const ocupados = motor?.trabajadores.filter((t) => t.corrida).length ?? 0;
  return (
    <nav className="riel" aria-label="Secciones">
      <div className="riel-marca" title="Positiva IA · plataforma de agentes">
        <span className="riel-logo" aria-hidden>
          <svg viewBox="0 0 32 32" width="22" height="22"><path d="M5 9.5 16 17l11-7.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /><rect x="4" y="7" width="24" height="18" rx="3.5" fill="none" stroke="currentColor" strokeWidth="2.2" /><circle cx="24.5" cy="22.5" r="5" fill="var(--lacre)" stroke="var(--tinta)" strokeWidth="2" /></svg>
        </span>
        <b>Positiva IA</b>
      </div>
      <div className="riel-secciones">
        {NAVEGACION.map((n) => (
          <button key={n.id} className={vista === n.id ? "activo" : ""} onClick={() => irA(n.id)} title={n.nombre} aria-current={vista === n.id ? "page" : undefined}>
            {vista === n.id && <motion.span layoutId="riel-activo" className="riel-fondo" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
            <Icono n={n.icono} tam={19} />
            <span>{n.nombre}</span>
            {n.id === "corridas" && ocupados > 0 && <i className="indicador" />}
          </button>
        ))}
      </div>
      <div className={`riel-estado ${conectado ? "vivo" : "caido"}`} title={conectado ? "Recibiendo los eventos del motor en vivo" : "Reconectando con el motor"}>
        <span className="riel-pulso" />
        <span>{conectado ? (ocupados ? `${ocupados} corriendo` : "En vivo") : "Sin conexión"}</span>
      </div>
    </nav>
  );
}

function EspacioFlujo({ ancho, bandejaVisible, cerrarBandeja }: { ancho: number; bandejaVisible: boolean; cerrarBandeja: () => void }) {
  const foco = usar((s) => s.foco);
  const enfocar = usar((s) => s.enfocar);
  const espacio = useRef<HTMLDivElement>(null);
  const centro = useRef<HTMLDivElement>(null);
  const [medidas, setMedidas] = useState<Medidas>(() => ({ ...MEDIDAS, ...leerLocal<Partial<Medidas>>(CLAVE_MEDIDAS, {}) }));
  const [dockPlegado, setDockPlegado] = useState(() => leerLocal(`${CLAVE_MEDIDAS}:dock-plegado`, false));
  const compacto = ancho < 1100;

  const fijar = useCallback((k: keyof Medidas, v: number) => {
    setMedidas((m) => {
      const sig = { ...m, [k]: v };
      guardarLocal(CLAVE_MEDIDAS, sig);
      return sig;
    });
  }, []);

  useEffect(() => {
    // Lo que React fija manda sobre lo que dejó el arrastre.
    for (const [k, v] of Object.entries(medidas)) {
      espacio.current?.style.setProperty(`--${k}`, `${v}px`);
      centro.current?.style.setProperty(`--${k}`, `${v}px`);
    }
  }, [medidas]);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && foco && !(e.target as HTMLElement).closest("input, textarea, select, .cm-editor")) enfocar(null);
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [foco, enfocar]);

  const alternarDock = () =>
    setDockPlegado((v) => {
      guardarLocal(`${CLAVE_MEDIDAS}:dock-plegado`, !v);
      return !v;
    });

  return (
    <div ref={espacio} className={`espacio-flujo ${bandejaVisible && !compacto ? "" : "sin-bandeja"} ${compacto ? "compacto" : ""}`}>
      {!compacto && bandejaVisible && (
        <>
          <div className="columna-bandeja"><Bandeja /></div>
          <Divisor contenedor={espacio} medida="bandeja" eje="x" min={260} max={() => Math.min(620, window.innerWidth * 0.45)} calcular={(e, caja) => e.clientX - caja.left} alSoltar={(v) => fijar("bandeja", v)} titulo="Arrastra para cambiar el ancho de la bandeja" />
        </>
      )}
      <div ref={centro} className={`centro ${dockPlegado ? "dock-plegado" : ""}`}>
        <div className="lienzo">
          <ReactFlowProvider>
            <Lienzo />
          </ReactFlowProvider>
        </div>
        {!dockPlegado && <Divisor contenedor={centro} medida="dock" eje="y" min={140} max={() => (centro.current?.clientHeight ?? 800) - 160} calcular={(e, caja) => caja.bottom - e.clientY} alSoltar={(v) => fijar("dock", v)} titulo="Arrastra para cambiar el alto del panel" />}
        <Dock plegado={dockPlegado} alternar={alternarDock} />
        <AnimatePresence>
          {foco && (
            <motion.aside key="hoja" className="hoja" initial={{ opacity: 0, x: 28 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 28, transition: { duration: 0.16 } }} transition={{ type: "spring", stiffness: 420, damping: 38 }}>
              {!compacto && <Divisor contenedor={centro} medida="inspector" eje="x" min={360} max={() => Math.min(900, (centro.current?.clientWidth ?? 1200) - 240)} calcular={(e, caja) => caja.right - e.clientX - 10} alSoltar={(v) => fijar("inspector", v)} titulo="Arrastra para cambiar el ancho del inspector" />}
              <Inspector />
            </motion.aside>
          )}
        </AnimatePresence>
      </div>
      <AnimatePresence>
        {compacto && bandejaVisible && (
          <>
            <motion.button key="fondo" className="cajon-fondo" aria-label="Cerrar bandeja" onClick={cerrarBandeja} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
            <motion.div key="cajon" className="cajon" initial={{ x: "-100%" }} animate={{ x: 0 }} exit={{ x: "-100%" }} transition={{ type: "spring", stiffness: 420, damping: 40 }}>
              <Bandeja />
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}

export default function App() {
  const cargar = usar((s) => s.cargar);
  const conectar = usar((s) => s.conectar);
  const avisar = usar((s) => s.avisar);
  const vista = usar((s) => s.vista);
  const foco = usar((s) => s.foco);
  const ancho = useAncho();
  const [bandeja, setBandeja] = useState(() => window.innerWidth >= 1100 && leerLocal("positiva-ia:bandeja-visible", true));

  useEffect(() => {
    cargar().catch((e: Error) => avisar(`No se pudo cargar: ${e.message}`, "error"));
    conectar();
  }, [cargar, conectar, avisar]);

  // En pantallas angostas el cajón de la bandeja se cierra al abrir un correo.
  useEffect(() => {
    if (ancho < 1100 && foco?.tipo === "correo") setBandeja(false);
  }, [foco, ancho]);

  const alternar = () =>
    setBandeja((v) => {
      if (ancho >= 1100) guardarLocal("positiva-ia:bandeja-visible", !v);
      return !v;
    });

  return (
    <div className="app">
      <Riel />
      <div className="marco">
        <BarraSuperior alternarBandeja={alternar} bandejaVisible={bandeja} />
        <main className="contenido">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={vista} className="contenido-vista" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.16, ease: "easeOut" }}>
              {vista === "flujo" && <EspacioFlujo ancho={ancho} bandejaVisible={bandeja} cerrarBandeja={() => setBandeja(false)} />}
              {vista === "adjuntos" && <VistaAdjuntos />}
              {vista === "corridas" && <VistaCorridas />}
              {vista === "logs" && <VistaLogs />}
              {vista === "herramientas" && <VistaHerramientas />}
              {vista === "proveedores" && <VistaProveedores />}
              {vista === "configuracion" && <VistaConfiguracion />}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      <Avisos />
    </div>
  );
}
