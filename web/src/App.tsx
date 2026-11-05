import { useEffect, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { usar, type Vista } from "./estado.ts";
import { Icono, type NombreIcono } from "./iconos.tsx";
import { Bandeja } from "./componentes/Bandeja.tsx";
import { Lienzo } from "./componentes/Lienzo.tsx";
import { Inspector } from "./componentes/Inspector.tsx";
import { Dock } from "./componentes/Dock.tsx";
import { VistaConfiguracion, VistaCorridas, VistaHerramientas, VistaLogs, VistaProveedores } from "./componentes/Vistas.tsx";

const NAVEGACION: { id: Vista; nombre: string; icono: NombreIcono }[] = [
  { id: "flujo", nombre: "Flujo", icono: "flujo" },
  { id: "corridas", nombre: "Corridas", icono: "corridas" },
  { id: "logs", nombre: "Logs", icono: "logs" },
  { id: "herramientas", nombre: "Herramientas", icono: "herramientas" },
  { id: "proveedores", nombre: "Proveedores", icono: "proveedores" },
  { id: "configuracion", nombre: "Configuración", icono: "ajustes" },
];

function useAncho(): number {
  const [ancho, setAncho] = useState(window.innerWidth);
  useEffect(() => {
    const f = () => setAncho(window.innerWidth);
    window.addEventListener("resize", f);
    return () => window.removeEventListener("resize", f);
  }, []);
  return ancho;
}

function Avisos() {
  const avisos = usar((s) => s.avisos);
  const cerrar = usar((s) => s.cerrarAviso);
  return (
    <div className="avisos" role="status">
      {avisos.map((a) => (
        <div key={a.id} className={`aviso ${a.tono}`}>
          <Icono n={a.tono === "error" ? "alerta" : "ok"} tam={15} />
          <span className="crece">{a.texto}</span>
          <button aria-label="Cerrar" onClick={() => cerrar(a.id)}><Icono n="cerrar" tam={12} /></button>
        </div>
      ))}
    </div>
  );
}

function BarraSuperior({ alternarBandeja }: { alternarBandeja: () => void }) {
  const motor = usar((s) => s.motor);
  const configuracion = usar((s) => s.configuracion);
  const conectado = usar((s) => s.conectado);
  const irA = usar((s) => s.irA);
  const ocupados = motor?.trabajadores.filter((t) => t.corrida).length ?? 0;
  const listos = motor?.trabajadores.filter((t) => t.listo).length ?? 0;
  const ajustes = Object.keys(configuracion?.parametros ?? {}).length + Object.keys(configuracion?.entorno ?? {}).length;
  return (
    <header className="barra">
      <button className="boton icono solo-movil" aria-label="Bandeja" onClick={alternarBandeja}><Icono n="bandeja" /></button>
      <div className="marca">
        <span className="logo"><Icono n="chispa" tam={16} /></span>
        <div>
          <b>Positiva IA</b>
          <span className="texto-3 diminuto">Plataforma de agentes</span>
        </div>
      </div>
      <div className="flujo-activo">
        <span className="texto-3 diminuto">Flujo</span>
        <b>Radicación de PQRD</b>
        <span className="pastilla verde diminuto">modo prueba</span>
      </div>
      <span className="espacio" />
      <div className="estado-barra">
        {motor?.errorInicio ? (
          <span className="pastilla rojo" title={motor.errorInicio}><span className="punto" />Motor detenido</span>
        ) : (
          <span className={`pastilla ${listos ? "verde" : "ambar"}`}><span className="punto" />{listos ? `${ocupados}/${listos} trabajando` : "Arrancando"}</span>
        )}
        {(motor?.enCola.length ?? 0) > 0 && <span className="pastilla azul">{motor?.enCola.length} en cola</span>}
        <button className="pastilla azul boton-pastilla" onClick={() => irA("configuracion")}><Icono n="chispa" tam={12} />{configuracion?.ia.modelo}</button>
        {ajustes > 0 && <button className="pastilla ambar boton-pastilla" onClick={() => irA("configuracion")}>{ajustes} ajuste{ajustes > 1 ? "s" : ""} activos</button>}
        <span className={`pastilla ${conectado ? "verde" : "rojo"}`} title={conectado ? "Recibiendo eventos en vivo" : "Reconectando"}><span className={`punto ${conectado ? "latiendo" : ""}`} />{conectado ? "En vivo" : "Sin conexión"}</span>
      </div>
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
          <Icono n={n.icono} tam={19} />
          <span>{n.nombre}</span>
          {n.id === "corridas" && (motor?.trabajadores.some((t) => t.corrida) ?? false) && <i className="indicador" />}
        </button>
      ))}
    </nav>
  );
}

function EspacioFlujo({ ancho, bandejaAbierta, cerrarBandeja }: { ancho: number; bandejaAbierta: boolean; cerrarBandeja: () => void }) {
  const foco = usar((s) => s.foco);
  const compacto = ancho < 1100;
  const centro = (
    <Group orientation="vertical" id="centro">
      <Panel id="lienzo" defaultSize="68%" minSize="30%">
        <div className="lienzo">
          <ReactFlowProvider>
            <Lienzo />
          </ReactFlowProvider>
        </div>
      </Panel>
      <Separator className="separador horizontal" />
      <Panel id="dock" defaultSize="32%" minSize="12%" collapsible collapsedSize="0%">
        <Dock />
      </Panel>
    </Group>
  );

  if (compacto) {
    return (
      <div className="espacio-compacto">
        {centro}
        {bandejaAbierta && (
          <div className="cajon izquierda">
            <Bandeja />
            <button className="cajon-fondo" aria-label="Cerrar bandeja" onClick={cerrarBandeja} />
          </div>
        )}
        {foco && <div className="cajon derecha"><Inspector /></div>}
      </div>
    );
  }

  return (
    <Group orientation="horizontal" id="espacio">
      <Panel id="bandeja" defaultSize="22%" minSize="16%" maxSize="40%" collapsible collapsedSize="0%">
        <Bandeja />
      </Panel>
      <Separator className="separador vertical" />
      <Panel id="centro" defaultSize="50%" minSize="30%">
        {centro}
      </Panel>
      <Separator className="separador vertical" />
      <Panel id="inspector" defaultSize="28%" minSize="20%" maxSize="55%">
        <Inspector />
      </Panel>
    </Group>
  );
}

export default function App() {
  const cargar = usar((s) => s.cargar);
  const conectar = usar((s) => s.conectar);
  const avisar = usar((s) => s.avisar);
  const vista = usar((s) => s.vista);
  const ancho = useAncho();
  const [bandejaMovil, setBandejaMovil] = useState(false);

  useEffect(() => {
    cargar().catch((e: Error) => avisar(`No se pudo cargar: ${e.message}`, "error"));
    conectar();
  }, [cargar, conectar, avisar]);

  return (
    <div className="app">
      <BarraSuperior alternarBandeja={() => setBandejaMovil((v) => !v)} />
      <div className="cuerpo">
        <Riel />
        <main className="contenido">
          {vista === "flujo" && <EspacioFlujo ancho={ancho} bandejaAbierta={bandejaMovil} cerrarBandeja={() => setBandejaMovil(false)} />}
          {vista === "corridas" && <VistaCorridas />}
          {vista === "logs" && <VistaLogs />}
          {vista === "herramientas" && <VistaHerramientas />}
          {vista === "proveedores" && <VistaProveedores />}
          {vista === "configuracion" && <VistaConfiguracion />}
        </main>
      </div>
      <Avisos />
    </div>
  );
}
