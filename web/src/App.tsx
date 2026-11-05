import { useEffect, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { usar } from "./estado.ts";
import { Bandeja } from "./componentes/Bandeja.tsx";
import { Flujo } from "./componentes/Flujo.tsx";
import { PanelNodo } from "./componentes/PanelNodo.tsx";
import { PanelCorrida } from "./componentes/PanelCorrida.tsx";
import { Ajustes, Corridas, Herramientas, Proveedores } from "./componentes/Paginas.tsx";

const PESTANAS = { flujo: "Flujo", corridas: "Corridas", proveedores: "Proveedores y APIs", herramientas: "Herramientas del agente", ajustes: "Ajustes" } as const;
type Pestana = keyof typeof PESTANAS;

export default function App() {
  const cargar = usar((s) => s.cargar);
  const conectar = usar((s) => s.conectar);
  const conectado = usar((s) => s.conectado);
  const motor = usar((s) => s.motor);
  const configuracion = usar((s) => s.configuracion);
  const nodo = usar((s) => s.nodo);
  const seleccionada = usar((s) => s.seleccionada);
  const aviso = usar((s) => s.aviso);
  const avisar = usar((s) => s.avisar);
  const [pestana, setPestana] = useState<Pestana>("flujo");

  useEffect(() => {
    cargar().catch((e: Error) => avisar(`No se pudo cargar: ${e.message}`));
    conectar();
  }, [cargar, conectar, avisar]);

  const ocupados = motor?.trabajadores.filter((t) => t.corrida).length ?? 0;
  const listos = motor?.trabajadores.filter((t) => t.listo).length ?? 0;
  const ajustes = Object.keys(configuracion?.parametros ?? {}).length + Object.keys(configuracion?.entorno ?? {}).length;
  const panel = nodo ? <PanelNodo /> : seleccionada ? <PanelCorrida /> : null;

  return (
    <div className="app">
      <header className="barra">
        <h1>Positiva IA</h1>
        <span className="sub">Radicación de PQRD · pipeline de producción en modo de prueba</span>
        <span className="espacio" />
        {motor?.errorInicio ? (
          <span className="pastilla rojo" title={motor.errorInicio}><span className="punto" />El motor no arrancó</span>
        ) : (
          <span className={`pastilla ${listos ? "verde" : "ambar"}`}><span className="punto" />{listos ? `${ocupados} de ${listos} trabajadores ocupados` : "Arrancando el motor"}</span>
        )}
        {(motor?.enCola.length ?? 0) > 0 && <span className="pastilla azul">{motor?.enCola.length} en cola</span>}
        {configuracion && <span className="pastilla azul">{configuracion.ia.modelo}</span>}
        {ajustes > 0 && <span className="pastilla ambar">{ajustes} ajuste{ajustes > 1 ? "s" : ""}</span>}
        <span className={`pastilla ${conectado ? "verde" : "rojo"}`}><span className="punto" />{conectado ? "En vivo" : "Sin conexión"}</span>
      </header>
      <div className={`cuerpo ${panel ? "" : "sin-panel"}`}>
        <Bandeja />
        <main className="centro">
          <nav className="pestanas">
            {(Object.keys(PESTANAS) as Pestana[]).map((p) => (
              <button key={p} className={`pestana ${pestana === p ? "activa" : ""}`} onClick={() => setPestana(p)}>{PESTANAS[p]}</button>
            ))}
          </nav>
          {pestana === "flujo" ? (
            <div className="lienzo">
              <ReactFlowProvider><Flujo /></ReactFlowProvider>
            </div>
          ) : (
            <div className="lienzo pagina">
              {pestana === "corridas" && <Corridas />}
              {pestana === "proveedores" && <Proveedores />}
              {pestana === "herramientas" && <Herramientas />}
              {pestana === "ajustes" && <Ajustes />}
            </div>
          )}
        </main>
        {panel}
      </div>
      {aviso && <div className="aviso">{aviso}</div>}
    </div>
  );
}
