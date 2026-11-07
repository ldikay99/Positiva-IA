import { useCallback, useEffect, useRef, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { guardarLocal, leerLocal, pedir, usar, type Vista } from "./estado.ts";
import { Icono, type NombreIcono } from "./iconos.tsx";
import { Bandeja } from "./componentes/Bandeja.tsx";
import { Lienzo } from "./componentes/Lienzo.tsx";
import { Inspector } from "./componentes/Inspector.tsx";
import { Dock } from "./componentes/Dock.tsx";
import { MenuContextual, type Opcion } from "./componentes/MenuContextual.tsx";
import { VistaAdjuntos } from "./componentes/Adjuntos.tsx";
import { VistaConfiguracion, VistaCorridas, VistaHerramientas, VistaLogs, VistaProveedores } from "./componentes/Vistas.tsx";
import { VistaTablero, pedirAlAsistente } from "./componentes/Tablero.tsx";
import { Chat } from "./componentes/Chat.tsx";
import { VistaConexiones } from "./componentes/Conexiones.tsx";
import { PanelEntorno, type EstadoCompuerta, type SeccionEntorno } from "./componentes/Entorno.tsx";
import { DialogoNuevoFlujo } from "./componentes/Dialogos.tsx";

const NAVEGACION: { id: Vista; nombre: string; icono: NombreIcono; pista: string; principal: boolean }[] = [
  { id: "tablero", nombre: "Tablero", icono: "corridas", pista: "Todos los flujos y cómo les va", principal: true },
  { id: "flujo", nombre: "Flujo", icono: "flujo", pista: "Elegir correos, correrlos y ver cada etapa", principal: true },
  { id: "corridas", nombre: "Corridas", icono: "historial", pista: "Resultados de cada correo corrido", principal: true },
  { id: "adjuntos", nombre: "Adjuntos", icono: "documento", pista: "Cada adjunto hoja por hoja", principal: true },
  { id: "logs", nombre: "Logs", icono: "logs", pista: "Todo lo que hace el motor, en vivo", principal: false },
  { id: "herramientas", nombre: "Herramientas", icono: "herramientas", pista: "Con qué busca datos la IA", principal: false },
  { id: "conexiones", nombre: "Conexiones", icono: "llave", pista: "Credenciales con que los nodos hablan con otros sistemas", principal: false },
  { id: "proveedores", nombre: "Sistemas", icono: "proveedores", pista: "Sistemas que consulta el flujo", principal: false },
  { id: "configuracion", nombre: "Ajustes", icono: "ajustes", pista: "Modelo de IA, capacidad y reglas", principal: false },
];

/** «gemini-3.1-flash-lite» se lee «Gemini 3.1 Flash Lite». */
function nombreModelo(m: string | undefined): string {
  if (!m) return "";
  return m
    .split("-")
    .map((p) => (/^[a-z]/.test(p) ? p[0]!.toUpperCase() + p.slice(1) : p))
    .join(" ");
}

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

/** «Radicación real: apagada (simulación)» o encendida, para los flujos de código. Abre «Ejecución». */
function PastillaCompuerta({ flujo, abrir }: { flujo: string; abrir: () => void }) {
  const [c, setC] = useState<EstadoCompuerta | null>(null);
  useEffect(() => {
    let vivo = true;
    setC(null);
    pedir<EstadoCompuerta>(`/api/flujos/${encodeURIComponent(flujo)}/compuerta`).then((r) => vivo && setC(r)).catch(() => undefined);
    const oir = (e: Event) => {
      const d = (e as CustomEvent<EstadoCompuerta & { flujo: string }>).detail;
      if (d?.flujo === flujo) setC(d);
    };
    window.addEventListener("positiva:compuerta", oir);
    return () => {
      vivo = false;
      window.removeEventListener("positiva:compuerta", oir);
    };
  }, [flujo]);
  const real = Boolean(c && (c.radicacion_real_efectiva ?? (c.radicacion_real && c.permitido_en_despliegue)));
  return (
    <button
      className={`pastilla boton-pastilla ${real ? "rojo" : ""}`}
      onClick={abrir}
      title={real ? "Las corridas de este flujo escriben de verdad en SGDEA y el buzón, como el job" : "Corre completo en simulación: lo que se radicaría o movería queda registrado aquí, sin enviarse"}
    >
      <Icono n={real ? "alerta" : "candado"} tam={11} />
      {real ? "Radicación real: encendida" : "Radicación real: apagada (simulación)"}
    </button>
  );
}

function BarraSuperior({ alternarBandeja, bandejaVisible, abrirEntorno, nuevoFlujo }: { alternarBandeja: () => void; bandejaVisible: boolean; abrirEntorno: (seccion?: SeccionEntorno) => void; nuevoFlujo: () => void }) {
  const motor = usar((s) => s.motor);
  const configuracion = usar((s) => s.configuracion);
  const irA = usar((s) => s.irA);
  const vista = usar((s) => s.vista);
  const flujo = usar((s) => s.flujo);
  const flujos = usar((s) => s.flujos);
  const cambiarFlujo = usar((s) => s.cambiarFlujo);
  const flujoActual = flujos.find((f) => f.id === flujo);
  const [menu, setMenu] = useState<{ x: number; y: number; opciones: Opcion[] } | null>(null);
  const ocupados = motor?.trabajadores.filter((t) => t.corrida).length ?? 0;
  const listos = motor?.trabajadores.filter((t) => t.listo).length ?? 0;
  const ajustes = Object.keys(configuracion?.parametros ?? {}).length + Object.keys(configuracion?.entorno ?? {}).length;
  const reglas = configuracion?.reglas.filter((r) => r.activa).length ?? 0;
  const enCola = motor?.enCola.length ?? 0;
  return (
    <header className="barra">
      {vista === "flujo" && (
        <button className={`boton-mini grande ${bandejaVisible ? "activo" : ""}`} aria-label={bandejaVisible ? "Ocultar la correspondencia" : "Mostrar la correspondencia"} aria-pressed={bandejaVisible} title={bandejaVisible ? "Ocultar la correspondencia" : "Mostrar la correspondencia"} onClick={alternarBandeja}>
          <Icono n="lado" tam={17} />
        </button>
      )}
      {vista === "tablero" ? (
        <h1 className="barra-titulo">Tus flujos</h1>
      ) : (
      <button
        className="selector-flujo"
        aria-haspopup="menu"
        title="Cambiar de flujo"
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setMenu({
            x: r.left,
            y: r.bottom + 6,
            opciones: [
              { titulo: "Flujos" },
              ...flujos.map((f) => ({ etiqueta: f.nombre, icono: (f.tipo === "codigo" ? "terminal" : "flujo") as NombreIcono, marcado: f.id === flujo, pista: f.tipo === "codigo" ? "Motor de producción" : `Creado aquí, versión ${f.version ?? 1}`, alHacer: () => void cambiarFlujo(f.id) })),
              { separador: true },
              { etiqueta: "Nuevo flujo", icono: "mas", alHacer: nuevoFlujo },
              { etiqueta: "Crear un flujo con IA", icono: "chispa", alHacer: () => pedirAlAsistente("Quiero crear un flujo nuevo. Pregúntame lo necesario y ármalo.") },
            ],
          });
        }}
      >
        <span className="flujo-nombre">
          <small>Flujo</small>
          <b>{flujoActual?.nombre ?? flujo}</b>
        </span>
        <Icono n="abajo" tam={14} />
      </button>
      )}
      <span className="espacio" />
      {vista === "flujo" && (
        <button className="boton chico boton-entorno" onClick={() => abrirEntorno()} title="Variables, secretos, conexiones y manifiesto de este flujo">
          <Icono n="llave" tam={14} /><span className="etq">Entorno</span>
        </button>
      )}
      <div className="estado-barra">
        {motor?.errorInicio ? (
          <span className="pastilla rojo" title={motor.errorInicio}><span className="punto" />El motor no arrancó</span>
        ) : !listos ? (
          <span className="pastilla ambar" title="El motor se está preparando"><span className="girando" />Preparando el motor</span>
        ) : ocupados ? (
          <span className="pastilla azul" title={`Corre hasta ${listos} correos a la vez`}><span className="girando" />Corriendo {ocupados} de {listos}</span>
        ) : (
          <span className="pastilla verde" title={`Corre hasta ${listos} correos a la vez`}><span className="punto" />Listo para correr</span>
        )}
        {enCola > 0 && <span className="pastilla azul" title="Correos que esperan turno">{enCola} en espera</span>}
        {flujoActual?.tipo === "codigo" ? (
          <PastillaCompuerta flujo={flujo} abrir={() => abrirEntorno("ejecucion")} />
        ) : (
          <span className="pastilla solo-ancho" title="Nada se escribe en SGDEA ni en el buzón real: lo que se radicaría o movería queda registrado aquí, sin enviarse"><Icono n="candado" tam={11} />Solo lectura</span>
        )}
        <button className="pastilla boton-pastilla solo-ancho" onClick={() => irA("configuracion")} title="Modelo de IA en uso. Cámbialo en Ajustes"><Icono n="chispa" tam={12} />{nombreModelo(configuracion?.ia.modelo)}</button>
        {ajustes + reglas > 0 && <button className="pastilla ambar boton-pastilla solo-ancho" onClick={() => irA("configuracion")} title="Cambios propios sobre el código de producción. Revísalos en Ajustes">{ajustes + reglas} cambio{ajustes + reglas === 1 ? "" : "s"} propio{ajustes + reglas === 1 ? "" : "s"}</button>}
      </div>
      {menu && <MenuContextual x={menu.x} y={menu.y} opciones={menu.opciones} cerrar={() => setMenu(null)} />}
    </header>
  );
}

function Riel({ movil }: { movil: boolean }) {
  const vista = usar((s) => s.vista);
  const irA = usar((s) => s.irA);
  const motor = usar((s) => s.motor);
  const conectado = usar((s) => s.conectado);
  const ocupados = motor?.trabajadores.filter((t) => t.corrida).length ?? 0;
  const [mas, setMas] = useState(false);
  const secundarias = NAVEGACION.filter((n) => !n.principal);
  const enSecundaria = secundarias.some((n) => n.id === vista);
  const visibles = movil ? NAVEGACION.filter((n) => n.principal) : NAVEGACION;

  useEffect(() => {
    if (!mas) return;
    const tecla = (e: KeyboardEvent) => e.key === "Escape" && setMas(false);
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [mas]);
  useEffect(() => {
    if (!movil) setMas(false);
  }, [movil]);

  const boton = (n: (typeof NAVEGACION)[number]) => (
    <button key={n.id} className={vista === n.id ? "activo" : ""} onClick={() => { irA(n.id); setMas(false); }} title={n.pista} aria-current={vista === n.id ? "page" : undefined}>
      {vista === n.id && <motion.span layoutId="riel-activo" className="riel-fondo" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
      <Icono n={n.icono} tam={19} />
      <span>{n.nombre}</span>
      {n.id === "corridas" && ocupados > 0 && <i className="indicador" />}
    </button>
  );

  return (
    <nav className="riel" aria-label="Secciones">
      <div className="riel-marca" title="Positiva IA">
        <span className="riel-logo" aria-hidden>
          <svg viewBox="0 0 32 32" width="22" height="22"><path d="M5 9.5 16 17l11-7.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /><rect x="4" y="7" width="24" height="18" rx="3.5" fill="none" stroke="currentColor" strokeWidth="2.2" /><circle cx="24.5" cy="22.5" r="5" fill="var(--lacre)" stroke="var(--tinta)" strokeWidth="2" /></svg>
        </span>
        <b>Positiva IA</b>
      </div>
      <div className="riel-secciones">
        {visibles.map(boton)}
        {movil && (
          <button className={enSecundaria || mas ? "activo" : ""} onClick={() => setMas(!mas)} aria-expanded={mas} aria-haspopup="dialog" title="Más secciones">
            {(enSecundaria || mas) && <motion.span layoutId="riel-activo" className="riel-fondo" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
            <Icono n="menu" tam={19} />
            <span>{enSecundaria && !mas ? NAVEGACION.find((n) => n.id === vista)?.nombre : "Más"}</span>
          </button>
        )}
      </div>
      <AnimatePresence>
        {movil && mas && (
          <>
            <motion.button key="fondo" className="mas-fondo" aria-label="Cerrar" onClick={() => setMas(false)} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
            <motion.div key="hoja" className="mas-hoja" role="dialog" aria-label="Más secciones" initial={{ y: "100%" }} animate={{ y: 0 }} exit={{ y: "100%" }} transition={{ type: "spring", stiffness: 460, damping: 40 }}>
              <span className="mas-asa" aria-hidden />
              {secundarias.map((n) => (
                <button key={n.id} className={`mas-opcion ${vista === n.id ? "activa" : ""}`} onClick={() => { irA(n.id); setMas(false); }} aria-current={vista === n.id ? "page" : undefined}>
                  <span className="mas-icono"><Icono n={n.icono} tam={18} /></span>
                  <span className="crece"><b>{n.nombre}</b><small>{n.pista}</small></span>
                  <Icono n="derecha" tam={14} />
                </button>
              ))}
            </motion.div>
          </>
        )}
      </AnimatePresence>
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
  // Sin corrida a la vista el panel de abajo queda como una barra delgada;
  // se abre solo cuando aparece una corrida para mostrar.
  const corridaVisible = usar((s) => s.corridaVisible);
  const sinCorrida = !corridaVisible;
  const plegado = dockPlegado || sinCorrida;
  const anterior = useRef(corridaVisible);
  useEffect(() => {
    if (corridaVisible && corridaVisible !== anterior.current) setDockPlegado(false);
    anterior.current = corridaVisible;
  }, [corridaVisible]);

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

  // El asistente abre o pliega el panel de abajo: {accion:"proceso"} o {accion:"panel", panel:"proceso", abierto}.
  useEffect(() => {
    const oir = (e: Event) => {
      const d = (e as CustomEvent<{ accion?: string; panel?: string; abierto?: boolean }>).detail;
      if (d?.accion === "proceso") setDockPlegado(false);
      else if (d?.accion === "panel" && d.panel === "proceso") setDockPlegado(!d.abierto);
    };
    window.addEventListener("positiva:control", oir);
    return () => window.removeEventListener("positiva:control", oir);
  }, []);

  return (
    <div ref={espacio} className={`espacio-flujo ${bandejaVisible && !compacto ? "" : "sin-bandeja"} ${compacto ? "compacto" : ""}`}>
      {!compacto && bandejaVisible && (
        <>
          <div className="columna-bandeja"><Bandeja /></div>
          <Divisor contenedor={espacio} medida="bandeja" eje="x" min={260} max={() => Math.min(620, window.innerWidth * 0.45)} calcular={(e, caja) => e.clientX - caja.left} alSoltar={(v) => fijar("bandeja", v)} titulo="Arrastra para cambiar el ancho de la bandeja" />
        </>
      )}
      <div ref={centro} className={`centro ${plegado ? "dock-plegado" : ""}`}>
        <div className="lienzo">
          <ReactFlowProvider>
            <Lienzo />
          </ReactFlowProvider>
        </div>
        {!plegado && <Divisor contenedor={centro} medida="dock" eje="y" min={140} max={() => (centro.current?.clientHeight ?? 800) - 160} calcular={(e, caja) => caja.bottom - e.clientY} alSoltar={(v) => fijar("dock", v)} titulo="Arrastra para cambiar el alto del panel" />}
        <Dock plegado={plegado} alternar={alternarDock} />
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

  // El asistente muestra u oculta la correspondencia y cierra el inspector.
  const enfocar = usar((s) => s.enfocar);
  const irA = usar((s) => s.irA);
  const [entorno, setEntorno] = useState<{ abierto: boolean; seccion: SeccionEntorno }>({ abierto: false, seccion: "variables" });
  const [nuevo, setNuevo] = useState(false);
  useEffect(() => {
    const oir = (e: Event) => {
      const d = (e as CustomEvent<{ accion?: string; panel?: string; abierto?: boolean; flujo?: string }>).detail;
      if (d?.accion === "vista_conexiones") irA("conexiones");
      else if (d?.accion === "entorno" || d?.accion === "manifiesto") {
        const abrir = () => setEntorno({ abierto: true, seccion: d.accion === "manifiesto" ? "manifiesto" : "variables" });
        const s = usar.getState();
        if (d.flujo && d.flujo !== s.flujo) void s.cambiarFlujo(d.flujo).then(abrir);
        else abrir();
      } else if (d?.accion === "nuevo_flujo") setNuevo(true);
      else if (d?.accion === "correspondencia" || d?.accion === "filtrar_correos") setBandeja(true);
      else if (d?.accion === "panel" && d.panel === "correspondencia") setBandeja(Boolean(d.abierto));
      else if (d?.accion === "panel" && d.panel === "inspector" && !d.abierto) enfocar(null);
    };
    window.addEventListener("positiva:control", oir);
    return () => window.removeEventListener("positiva:control", oir);
  }, [enfocar, irA]);

  const alternar = () =>
    setBandeja((v) => {
      if (ancho >= 1100) guardarLocal("positiva-ia:bandeja-visible", !v);
      return !v;
    });

  return (
    <MotionConfig reducedMotion="user">
    <div className="app">
      <Riel movil={ancho <= 760} />
      <div className="marco">
        <BarraSuperior alternarBandeja={alternar} bandejaVisible={bandeja} abrirEntorno={(seccion) => setEntorno({ abierto: true, seccion: seccion ?? "variables" })} nuevoFlujo={() => setNuevo(true)} />
        <main className="contenido">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={vista} className="contenido-vista" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.16, ease: "easeOut" }}>
              {vista === "tablero" && <VistaTablero />}
              {vista === "flujo" && <EspacioFlujo ancho={ancho} bandejaVisible={bandeja} cerrarBandeja={() => setBandeja(false)} />}
              {vista === "adjuntos" && <VistaAdjuntos />}
              {vista === "corridas" && <VistaCorridas />}
              {vista === "logs" && <VistaLogs />}
              {vista === "herramientas" && <VistaHerramientas />}
              {vista === "conexiones" && <VistaConexiones />}
              {vista === "proveedores" && <VistaProveedores />}
              {vista === "configuracion" && <VistaConfiguracion />}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      <Avisos />
      <PanelEntorno abierto={entorno.abierto} seccion={entorno.seccion} cambiarSeccion={(seccion) => setEntorno({ abierto: true, seccion })} cerrar={() => setEntorno((x) => ({ ...x, abierto: false }))} />
      <DialogoNuevoFlujo abierto={nuevo} cerrar={() => setNuevo(false)} />
      <Chat />
    </div>
    </MotionConfig>
  );
}
