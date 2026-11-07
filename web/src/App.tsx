import { iniciarRutas } from "./rutas.ts";
import { useCallback, useEffect, useRef, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { guardarLocal, leerLocal, pedir, usar, type Vista } from "./estado.ts";
import { Icono, type NombreIcono } from "./iconos.tsx";
import { Bandeja, PanelSinCorreo } from "./componentes/Bandeja.tsx";
import { AccionesFlujo } from "./componentes/AccionesFlujo.tsx";
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
import { VistaInicio } from "./componentes/Inicio.tsx";
import { VistaHistorial } from "./componentes/Historial.tsx";
import { VistaPlantillas } from "./componentes/Plantillas.tsx";
import { Campana, VistaNotificaciones } from "./componentes/Notificaciones.tsx";
import { VistaAprobaciones } from "./componentes/Aprobaciones.tsx";
import { PanelGuia } from "./componentes/Guia.tsx";
import { PASOS_GUIA, usarGuia } from "./guia.ts";

/**
 * Las secciones, por grupo. «trabajo» es lo de todos los días (y la barra de abajo
 * del celular); «construir» y «supervisar» van debajo, separados; «otras» son
 * vistas técnicas de uso raro y viven en «Más»; «Ajustes» queda fijo abajo.
 */
type GrupoNav = "trabajo" | "construir" | "supervisar" | "otras" | "pie";
const GRUPOS: { id: GrupoNav; nombre: string }[] = [
  { id: "trabajo", nombre: "Trabajo" },
  { id: "construir", nombre: "Construir" },
  { id: "supervisar", nombre: "Supervisar" },
  { id: "otras", nombre: "Otras vistas" },
  { id: "pie", nombre: "Configuración" },
];
const NAVEGACION: { id: Vista; nombre: string; corto?: string; icono: NombreIcono; pista: string; grupo: GrupoNav }[] = [
  { id: "inicio", nombre: "Inicio", icono: "inicio", pista: "Qué es la plataforma, tus flujos y lo que pasó hoy", grupo: "trabajo" },
  { id: "flujo", nombre: "Flujo", icono: "flujo", pista: "El flujo abierto: sus pasos, correrlo y verlo trabajar", grupo: "trabajo" },
  { id: "historial", nombre: "Historial", icono: "historial", pista: "Cada ejecución del flujo: qué entró, qué hizo y cómo terminó", grupo: "trabajo" },
  { id: "aprobaciones", nombre: "Aprobaciones", corto: "Aprobar", icono: "persona", pista: "Casos en pausa que esperan que una persona decida", grupo: "trabajo" },
  { id: "plantillas", nombre: "Plantillas", icono: "plantilla", pista: "Flujos de ejemplo listos para usar", grupo: "construir" },
  { id: "conexiones", nombre: "Conexiones", icono: "llave", pista: "Cuentas y llaves con que los pasos hablan con otros sistemas", grupo: "construir" },
  { id: "tablero", nombre: "Tablero", icono: "corridas", pista: "Las cifras de cada flujo: resultados, tiempos y recursos", grupo: "supervisar" },
  { id: "logs", nombre: "Registro", icono: "logs", pista: "Todo lo que hace el motor, en vivo (logs)", grupo: "supervisar" },
  { id: "proveedores", nombre: "Sistemas", icono: "proveedores", pista: "Sistemas que consulta el flujo", grupo: "supervisar" },
  { id: "adjuntos", nombre: "Adjuntos", icono: "documento", pista: "Cada documento de un correo, hoja por hoja", grupo: "otras" },
  { id: "herramientas", nombre: "Herramientas", icono: "herramientas", pista: "Con qué busca datos la IA", grupo: "otras" },
  { id: "configuracion", nombre: "Ajustes", icono: "ajustes", pista: "Modelo de IA, capacidad y reglas", grupo: "pie" },
];

/** Las vistas de toda la plataforma (no de un flujo): la barra muestra su título, no el selector de flujo. */
const TITULOS: Partial<Record<Vista, string>> = { inicio: "Inicio", tablero: "Tus flujos", plantillas: "Plantillas", notificaciones: "Notificaciones", aprobaciones: "Aprobaciones" };
/** Qué sección del riel se marca en cada vista (el resumen de corridas es parte del historial). */
const SECCION_DE: Partial<Record<Vista, Vista>> = { corridas: "historial" };
const VISTAS_DE_CONTROL = new Set<Vista>(["inicio", "plantillas", "historial", "notificaciones", "aprobaciones", "tablero", "flujo", "conexiones", "adjuntos", "logs", "herramientas", "proveedores", "configuracion", "corridas"]);

/** «Ayuda»: abre la guía de primeros pasos; mientras no esté completa muestra el avance. */
function BotonAyuda() {
  const hechos = usarGuia((s) => s.hechos.length);
  const abierta = usarGuia((s) => s.abierta);
  const abrir = usarGuia((s) => s.abrir);
  const vista = usar((s) => s.vista);
  const completa = hechos >= PASOS_GUIA.length;
  const alternar = () => {
    // En el inicio la guía vive en su tarjeta: se trae a la vista (o reaparece si estaba cerrada).
    if (vista === "inicio" && !usarGuia.getState().cerrada) {
      document.querySelector<HTMLElement>(".in-guia")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      document.querySelector<HTMLElement>(".in-guia .gu-paso")?.focus({ preventScroll: true });
      return;
    }
    abrir(!abierta);
  };
  return (
    <button className={`boton-mini grande gu-ayuda ${abierta ? "activo" : ""} ${completa ? "" : "con-avance"}`} aria-pressed={abierta} aria-label={completa ? "Ayuda: primeros pasos y palabras de la plataforma" : `Primeros pasos: ${hechos} de ${PASOS_GUIA.length}`} title={completa ? "Ayuda" : `Primeros pasos: ${hechos} de ${PASOS_GUIA.length}`} onClick={alternar}>
      <Icono n="ayuda" tam={18} />
      {!completa && <span className="gu-ayuda-avance" aria-hidden>{hechos}/{PASOS_GUIA.length}</span>}
    </button>
  );
}

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
  const titulo = TITULOS[vista];
  return (
    <header className="barra">
      {vista === "flujo" && (
        <button className={`boton-mini grande ${bandejaVisible ? "activo" : ""}`} aria-label={`${bandejaVisible ? "Ocultar" : "Mostrar"} ${flujoActual?.correspondencia === false ? "las corridas" : "la correspondencia"}`} aria-pressed={bandejaVisible} title={`${bandejaVisible ? "Ocultar" : "Mostrar"} ${flujoActual?.correspondencia === false ? "las corridas" : "la correspondencia"}`} onClick={alternarBandeja}>
          <Icono n="lado" tam={17} />
        </button>
      )}
      {titulo ? (
        <h1 className="barra-titulo">{titulo}</h1>
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
              { etiqueta: "Desde una plantilla", icono: "plantilla", alHacer: () => irA("plantillas") },
              { etiqueta: "Crear un flujo con Vera", icono: "chispa", alHacer: () => pedirAlAsistente("Quiero crear un flujo nuevo. Pregúntame lo necesario y ármalo.") },
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
      {vista === "flujo" && flujoActual && <AccionesFlujo flujo={flujoActual.id} />}
      <span className="espacio" />
      {vista === "flujo" && (
        <button className="boton chico boton-entorno" onClick={() => abrirEntorno()} title="Variables, secretos, conexiones y manifiesto de este flujo">
          <Icono n="llave" tam={14} /><span className="etq">Entorno</span>
        </button>
      )}
      <div className="estado-barra">
        {motor?.errorInicio ? (
          <span className="pastilla rojo" title={motor.errorInicio}><span className="punto" />El motor no arrancó</span>
        ) : !listos && motor?.trabajadores.length ? (
          <span className="pastilla ambar" title="El motor se está preparando"><span className="girando" />Preparando el motor</span>
        ) : !listos ? (
          <span className="pastilla verde" title="Sus trabajadores arrancan cuando hay algo que correr (en menos de un segundo) y se retiran tras un rato sin trabajo"><span className="punto" />Listo para correr</span>
        ) : ocupados ? (
          <span className="pastilla azul" title={`Corre hasta ${listos} correos a la vez`}><span className="girando" />Corriendo {ocupados} de {listos}</span>
        ) : (
          <span className="pastilla verde" title={`Corre hasta ${listos} correos a la vez`}><span className="punto" />Listo para correr</span>
        )}
        {enCola > 0 && <span className="pastilla azul" title="Ejecuciones que esperan turno">{enCola} en espera</span>}
        {!titulo && (flujoActual?.tipo === "codigo" ? (
          <PastillaCompuerta flujo={flujo} abrir={() => abrirEntorno("ejecucion")} />
        ) : (
          <span className="pastilla solo-ancho" title="Nada se escribe en sistemas reales: lo que se enviaría, radicaría o movería queda registrado aquí, sin enviarse"><Icono n="candado" tam={11} />Simulación</span>
        ))}
        {!titulo && <button className="pastilla boton-pastilla solo-ancho" onClick={() => irA("configuracion")} title="Modelo de IA en uso. Cámbialo en Ajustes"><Icono n="chispa" tam={12} />{nombreModelo(configuracion?.ia.modelo)}</button>}
        {!titulo && ajustes + reglas > 0 && <button className="pastilla ambar boton-pastilla solo-ancho" onClick={() => irA("configuracion")} title="Cambios propios sobre el código de producción. Revísalos en Ajustes">{ajustes + reglas} cambio{ajustes + reglas === 1 ? "" : "s"} propio{ajustes + reglas === 1 ? "" : "s"}</button>}
      </div>
      <BotonAyuda />
      <Campana />
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
  const botonMas = useRef<HTMLButtonElement>(null);
  const hojaMas = useRef<HTMLDivElement>(null);
  const [posMas, setPosMas] = useState<{ left: number; bottom: number } | null>(null);
  const pendientesAprobar = usar((s) => s.notificaciones.filter((n) => n.tipo === "aprobacion" && !n.leida).length);
  const seccion = SECCION_DE[vista] ?? vista;
  // En el celular la barra de abajo lleva «trabajo»; lo demás va a la hoja «Más». En pantalla grande, solo «otras».
  const enMas = NAVEGACION.filter((n) => (movil ? n.grupo !== "trabajo" : n.grupo === "otras"));
  const enSecundaria = enMas.some((n) => n.id === seccion);

  useEffect(() => {
    if (!mas) return;
    const tecla = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setMas(false);
      botonMas.current?.focus();
    };
    const fuera = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!hojaMas.current?.contains(t) && !botonMas.current?.contains(t)) setMas(false);
    };
    window.addEventListener("keydown", tecla);
    if (!movil) window.addEventListener("pointerdown", fuera);
    return () => {
      window.removeEventListener("keydown", tecla);
      window.removeEventListener("pointerdown", fuera);
    };
  }, [mas, movil]);
  useEffect(() => setMas(false), [movil, vista]);
  useEffect(() => {
    if (!mas || movil) return;
    const r = botonMas.current?.getBoundingClientRect();
    if (r) setPosMas({ left: r.right + 10, bottom: Math.max(8, window.innerHeight - r.bottom) });
    hojaMas.current?.querySelector<HTMLElement>("button")?.focus();
  }, [mas, movil]);

  const ir = (id: Vista) => {
    irA(id);
    setMas(false);
  };
  const fondo = <motion.span layoutId="riel-activo" className="riel-fondo" transition={{ type: "spring", stiffness: 500, damping: 38 }} />;

  const boton = (n: (typeof NAVEGACION)[number]) => (
    <button key={n.id} className={`riel-item ${seccion === n.id ? "activo" : ""}`} onClick={() => ir(n.id)} title={n.pista} aria-label={n.nombre} aria-current={seccion === n.id ? "page" : undefined}>
      {seccion === n.id && fondo}
      <Icono n={n.icono} tam={19} />
      <span className="riel-etq" data-corto={n.corto ?? n.nombre}><span>{n.nombre}</span></span>
      {n.id === "historial" && ocupados > 0 && <i className="indicador" aria-label={`${ocupados} corriendo`} />}
      {n.id === "aprobaciones" && pendientesAprobar > 0 && <i className="indicador" aria-label={`${pendientesAprobar} por decidir`} />}
    </button>
  );

  const opcionMas = (n: (typeof NAVEGACION)[number]) => (
    <button key={n.id} className={`mas-opcion ${seccion === n.id ? "activa" : ""}`} onClick={() => ir(n.id)} aria-current={seccion === n.id ? "page" : undefined}>
      <span className="mas-icono"><Icono n={n.icono} tam={18} /></span>
      <span className="crece"><b>{n.nombre}</b><small>{n.pista}</small></span>
      <Icono n="derecha" tam={14} />
    </button>
  );

  const etiquetaMas = movil && enSecundaria && !mas ? NAVEGACION.find((n) => n.id === seccion)?.nombre ?? "Más" : "Más";
  const botonDeMas = (
    <button ref={botonMas} className={`riel-item riel-mas ${enSecundaria || mas ? "activo" : ""}`} onClick={() => setMas(!mas)} aria-expanded={mas} aria-haspopup="dialog" aria-label="Más secciones" title={movil ? undefined : "Más vistas: adjuntos y herramientas"}>
      {(enSecundaria || mas) && fondo}
      <Icono n="menu" tam={19} />
      <span className="riel-etq" data-corto={etiquetaMas}><span>{etiquetaMas}</span></span>
    </button>
  );

  if (movil) {
    return (
      <nav className="riel" aria-label="Secciones">
        <div className="riel-secciones">
          {NAVEGACION.filter((n) => n.grupo === "trabajo").map(boton)}
          {botonDeMas}
        </div>
        <AnimatePresence>
          {mas && (
            <>
              <motion.button key="fondo" className="mas-fondo" aria-label="Cerrar" onClick={() => setMas(false)} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
              <motion.div key="hoja" ref={hojaMas} className="mas-hoja" role="dialog" aria-label="Más secciones" initial={{ y: "100%" }} animate={{ y: 0 }} exit={{ y: "100%" }} transition={{ type: "spring", stiffness: 460, damping: 40 }}>
                <span className="mas-asa" aria-hidden />
                {GRUPOS.filter((g) => g.id !== "trabajo").map((g) => (
                  <div key={g.id} className="mas-grupo" role="group" aria-label={g.nombre}>
                    <span className="mas-grupo-nombre">{g.nombre}</span>
                    {enMas.filter((n) => n.grupo === g.id).map(opcionMas)}
                  </div>
                ))}
              </motion.div>
            </>
          )}
        </AnimatePresence>
      </nav>
    );
  }

  return (
    <nav className="riel" aria-label="Secciones">
      <div className="riel-marca" title="Positiva IA">
        <span className="riel-logo" aria-hidden>
          <svg viewBox="0 0 32 32" width="20" height="20"><path d="M5 9.5 16 17l11-7.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /><rect x="4" y="7" width="24" height="18" rx="3.5" fill="none" stroke="currentColor" strokeWidth="2.2" /><circle cx="24.5" cy="22.5" r="5" fill="var(--cian)" stroke="var(--azul)" strokeWidth="2" /></svg>
        </span>
        <b>Positiva IA</b>
      </div>
      <div className="riel-secciones">
        {GRUPOS.filter((g) => g.id === "trabajo" || g.id === "construir" || g.id === "supervisar").map((g) => (
          <div key={g.id} className="riel-grupo" role="group" aria-label={g.nombre}>
            <span className="riel-grupo-nombre" aria-hidden>{g.nombre}</span>
            {NAVEGACION.filter((n) => n.grupo === g.id).map(boton)}
          </div>
        ))}
      </div>
      <div className="riel-pie">
        {botonDeMas}
        {NAVEGACION.filter((n) => n.grupo === "pie").map(boton)}
        <div className={`riel-estado ${conectado ? "vivo" : "caido"}`} title={conectado ? "Recibiendo los eventos del motor en vivo" : "Reconectando con el motor"}>
          <span className="riel-pulso" />
          <span>{conectado ? (ocupados ? `${ocupados} corriendo` : "En vivo") : "Sin conexión"}</span>
        </div>
      </div>
      <AnimatePresence>
        {mas && posMas && (
          <motion.div key="pop" ref={hojaMas} className="mas-pop" role="dialog" aria-label="Más vistas" style={{ left: posMas.left, bottom: posMas.bottom }} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -6, transition: { duration: 0.12 } }} transition={{ duration: 0.16 }}>
            <span className="mas-grupo-nombre">Otras vistas</span>
            {enMas.map(opcionMas)}
          </motion.div>
        )}
      </AnimatePresence>
    </nav>
  );
}

function EspacioFlujo({ ancho, bandejaVisible, cerrarBandeja }: { ancho: number; bandejaVisible: boolean; cerrarBandeja: () => void }) {
  const foco = usar((s) => s.foco);
  const enfocar = usar((s) => s.enfocar);
  // Solo los flujos que arrancan con un correo tienen correspondencia; los demás muestran sus corridas.
  const conCorreo = usar((s) => s.flujos.find((f) => f.id === s.flujo)?.correspondencia !== false);
  const Izquierda = conCorreo ? Bandeja : PanelSinCorreo;
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
          <div className="columna-bandeja"><Izquierda /></div>
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
              <Izquierda />
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
    cargar().then(() => iniciarRutas()).catch((e: Error) => avisar(`No se pudo cargar: ${e.message}`, "error"));
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
      // Vera abre cualquier pantalla: {accion:"vista", vista, flujo?, corrida?} y la guía: {accion:"guia", abierta}.
      else if (d?.accion === "vista" && typeof (d as { vista?: unknown }).vista === "string" && VISTAS_DE_CONTROL.has((d as { vista: Vista }).vista)) {
        const x = d as { vista: Vista; flujo?: string; corrida?: string };
        const s = usar.getState();
        if (x.vista === "historial" && typeof x.corrida === "string" && x.corrida) void s.abrirEnHistorial(x.corrida, x.flujo);
        else if (x.flujo && x.flujo !== s.flujo && s.flujos.some((f) => f.id === x.flujo)) void s.cambiarFlujo(x.flujo).then(() => irA(x.vista));
        else irA(x.vista);
        // Lo que se abre dentro de la vista (una plantilla, una conexión) también va en la URL.
        const dentro = (d as { plantilla?: unknown; conexion?: unknown }).plantilla ?? (d as { conexion?: unknown }).conexion;
        if (typeof dentro === "string" && dentro && (x.vista === "plantillas" || x.vista === "conexiones")) s.fijarSub(dentro);
      } else if (d?.accion === "guia") {
        const abierta = (d as { abierta?: unknown }).abierta !== false;
        if (abierta && usar.getState().vista === "inicio" && !usarGuia.getState().cerrada) document.querySelector<HTMLElement>(".in-guia")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        else usarGuia.getState().abrir(abierta);
      }
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
              {vista === "inicio" && <VistaInicio />}
              {vista === "tablero" && <VistaTablero />}
              {vista === "flujo" && <EspacioFlujo ancho={ancho} bandejaVisible={bandeja} cerrarBandeja={() => setBandeja(false)} />}
              {vista === "historial" && <VistaHistorial />}
              {vista === "plantillas" && <VistaPlantillas />}
              {vista === "notificaciones" && <VistaNotificaciones />}
              {vista === "aprobaciones" && <VistaAprobaciones />}
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
      <PanelGuia />
      <Chat />
    </div>
    </MotionConfig>
  );
}
