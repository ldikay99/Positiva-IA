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
import { VistaConfiguracion, VistaCorridas, VistaHerramientas, VistaLogs, VistaProveedores } from "./componentes/Vistas.tsx";

const NAVEGACION: { id: Vista; nombre: string; icono: NombreIcono }[] = [
  { id: "flujo", nombre: "Flujo", icono: "flujo" },
  { id: "corridas", nombre: "Corridas", icono: "corridas" },
  { id: "logs", nombre: "Logs", icono: "logs" },
  { id: "herramientas", nombre: "Herramientas", icono: "herramientas" },
  { id: "proveedores", nombre: "Proveedores", icono: "proveedores" },
  { id: "configuracion", nombre: "Ajustes", icono: "ajustes" },
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
  const conectado = usar((s) => s.conectado);
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
      <div className="marca">
        <span className="logo"><Icono n="chispa" tam={17} /></span>
        <div>
          <b>Positiva IA</b>
          <span className="texto-3 diminuto">Plataforma de agentes</span>
        </div>
      </div>
      <span className="barra-separador" />
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
        <span className="texto-3 diminuto">Flujo activo</span>
        <b>Radicación de PQRD</b>
        <Icono n="abajo" tam={13} className="texto-3" />
      </button>
      <span className="pastilla verde diminuto solo-ancho" title="Nada se escribe en SGDEA ni en el buzón real">Modo prueba</span>
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
        <span className={`pastilla ${conectado ? "verde" : "rojo"}`} title={conectado ? "Recibiendo eventos del motor en vivo" : "Reconectando"}><span className={`punto ${conectado ? "latiendo" : ""}`} />{conectado ? "En vivo" : "Sin conexión"}</span>
      </div>
      {menu && <MenuContextual x={menu.x} y={menu.y} opciones={menu.opciones} cerrar={() => setMenu(null)} />}
    </header>
  );
}

function Riel() {
  const vista = usar((s) => s.vista);
  const irA = usar((s) => s.irA);
  const motor = usar((s) => s.motor);
  return (
    <nav className="riel" aria-label="Secciones">
      {NAVEGACION.map((n) => (
        <button key={n.id} className={vista === n.id ? "activo" : ""} onClick={() => irA(n.id)} title={n.nombre}>
          {vista === n.id && <motion.span layoutId="riel-activo" className="riel-fondo" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
          <Icono n={n.icono} tam={19} />
          <span>{n.nombre}</span>
          {n.id === "corridas" && (motor?.trabajadores.some((t) => t.corrida) ?? false) && <i className="indicador" />}
        </button>
      ))}
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
      <BarraSuperior alternarBandeja={alternar} bandejaVisible={bandeja} />
      <div className="cuerpo">
        <Riel />
        <main className="contenido">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={vista} className="contenido-vista" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.16, ease: "easeOut" }}>
              {vista === "flujo" && <EspacioFlujo ancho={ancho} bandejaVisible={bandeja} cerrarBandeja={() => setBandeja(false)} />}
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
