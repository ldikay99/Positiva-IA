import { useEffect, useMemo, useState } from "react";
import { pedir, usar } from "../estado.ts";
import { useSubruta } from "../rutas.ts";
import { Icono, type NombreIcono } from "../iconos.tsx";
import { arranqueDe, iconoDePaso } from "../lenguaje.ts";
import type { Plantilla } from "../tipos.ts";
import { Dialogo } from "./Dialogos.tsx";
import { Cargando, EstadoPantalla } from "./Historial.tsx";
import { marcarPaso } from "../guia.ts";
import { pedirAlAsistente } from "./Tablero.tsx";

const NECESITA: Record<Plantilla["necesita"][number]["tipo"], { icono: NombreIcono; nombre: string }> = {
  nada: { icono: "ok", nombre: "Listo para usar" },
  correos: { icono: "correo", nombre: "Para probar" },
  archivos: { icono: "adjunto", nombre: "Para probar" },
  datos: { icono: "base", nombre: "Datos" },
  conexion: { icono: "llave", nombre: "Conexión" },
  entorno: { icono: "candado", nombre: "Configuración" },
};

/** El recorrido principal de una plantilla: sus pasos en orden, desde cómo arranca. */
export function ordenDePasos(p: Pick<Plantilla, "nodos" | "aristas" | "disparador">) {
  const inicio = p.nodos.find((n) => n.tipo === p.disparador) ?? p.nodos[0];
  const vistos = new Set<string>();
  const orden: { id: string; tipo: string; nombre: string; ramas: { cuando?: string; hacia: string }[] }[] = [];
  const cola = inicio ? [inicio.id] : [];
  while (cola.length) {
    const id = cola.shift()!;
    if (vistos.has(id)) continue;
    vistos.add(id);
    const n = p.nodos.find((x) => x.id === id);
    if (!n) continue;
    const salen = p.aristas.filter((a) => a.desde === id);
    orden.push({ ...n, ramas: salen.map((a) => ({ cuando: a.cuando, hacia: a.hacia })) });
    for (const a of salen) cola.push(a.hacia);
  }
  for (const n of p.nodos) if (!vistos.has(n.id)) orden.push({ ...n, ramas: [] });
  return orden;
}

/** La vista previa de los pasos: una ruta de fichas, con los finales juntos al final. */
function VistaPrevia({ p, max = 5 }: { p: Plantilla; max?: number }) {
  const orden = ordenDePasos(p);
  const finales = orden.filter((n) => n.tipo === "salida");
  const pasos = orden.filter((n) => n.tipo !== "salida");
  const visibles = pasos.slice(0, max);
  return (
    <ol className="pl-ruta" aria-label={`Pasos: ${orden.map((n) => n.nombre).join(", ")}`}>
      {visibles.map((n) => (
        <li key={n.id}><Icono n={iconoDePaso(n.tipo)} tam={12} />{n.nombre}</li>
      ))}
      {pasos.length > max && <li className="pl-mas">+{pasos.length - max} pasos</li>}
      {finales.length > 0 && <li className="pl-final"><Icono n="bandera" tam={12} />{finales.length === 1 ? finales[0]!.nombre : `${finales.length} finales posibles`}</li>}
    </ol>
  );
}

export function VistaPlantillas() {
  const [lista, setLista] = useState<Plantilla[] | null>(null);
  const [error, setError] = useState("");
  const [categoria, setCategoria] = useState("");
  // La plantilla abierta vive en la URL: /plantillas/<plantilla>
  const [sub, setSub] = useSubruta();
  const abierta = useMemo(() => (sub && lista ? lista.find((x) => x.id === sub) ?? null : null), [sub, lista]);
  const setAbierta = (p: Plantilla | null) => setSub(p ? p.id : null);

  const cargar = () => {
    setError("");
    pedir<Plantilla[]>("/api/plantillas").then(setLista).catch((e: Error) => setError(e.message));
  };
  useEffect(cargar, []);
  // Vera puede pedir que se abra una: {accion:"vista", vista:"plantillas", plantilla}
  useEffect(() => {
    const oir = (e: Event) => {
      const d = (e as CustomEvent<{ accion?: string; vista?: string; plantilla?: string }>).detail;
      if (d?.accion === "vista" && d.vista === "plantillas" && d.plantilla && lista) setAbierta(lista.find((x) => x.id === d.plantilla) ?? null);
    };
    window.addEventListener("positiva:control", oir);
    return () => window.removeEventListener("positiva:control", oir);
  }, [lista]);

  const categorias = useMemo(() => [...new Set((lista ?? []).map((p) => p.categoria))], [lista]);
  const visibles = (lista ?? []).filter((p) => !categoria || p.categoria === categoria);

  return (
    <div className="vista pr-vista plantillas">
      <header className="vista-cabeza">
        <div className="crece">
          <h1>Plantillas</h1>
          <p className="texto-2">Flujos de ejemplo listos para usar. Al instalar uno queda como flujo tuyo: lo corres tal cual o le cambias los pasos que quieras.</p>
        </div>
        <button className="boton" onClick={() => pedirAlAsistente("Quiero un flujo a mi medida. Pregúntame qué necesito automatizar y ármalo.")}><Icono n="chispa" tam={14} />¿No está la tuya? Pídesela a Vera</button>
      </header>
      {lista && categorias.length > 1 && (
        <div className="filtros" role="group" aria-label="Categoría">
          <button className={!categoria ? "activo" : ""} aria-pressed={!categoria} onClick={() => setCategoria("")}>Todas <span>{lista.length}</span></button>
          {categorias.map((c) => (
            <button key={c} className={categoria === c ? "activo" : ""} aria-pressed={categoria === c} onClick={() => setCategoria(categoria === c ? "" : c)}>{c} <span>{lista.filter((p) => p.categoria === c).length}</span></button>
          ))}
        </div>
      )}
      {error ? (
        <EstadoPantalla icono="alerta" tono="rojo" titulo="No se pudieron cargar las plantillas" texto={error} accion={<button className="boton" onClick={cargar}>Intentar de nuevo</button>} />
      ) : !lista ? (
        <Cargando filas={3} />
      ) : !lista.length ? (
        <EstadoPantalla icono="plantilla" titulo="Todavía no hay plantillas" texto="Se agregan como archivos en catalogo/plantillas del servidor. Mientras tanto, crea un flujo a mano o pídeselo a Vera." />
      ) : (
        <ul className="pl-rejilla">
          {visibles.map((p) => <TarjetaPlantilla key={p.id} p={p} abrir={() => setAbierta(p)} />)}
        </ul>
      )}
      <DialogoPlantilla p={abierta} cerrar={() => setAbierta(null)} />
    </div>
  );
}

export function TarjetaPlantilla({ p, abrir, compacta = false }: { p: Plantilla; abrir: () => void; compacta?: boolean }) {
  const a = arranqueDe(p.disparador);
  return (
    <li className={`pl-tarjeta ${p.disponible ? "" : "no-disponible"} ${compacta ? "compacta" : ""}`}>
      <div className="pl-cabeza">
        <span className="pl-icono"><Icono n={a.icono} tam={17} /></span>
        <span className="pl-categoria">{p.categoria}</span>
        {p.usa_ia && <span className="pl-ia" title="Usa la IA de la plataforma"><Icono n="chispa" tam={12} />Con IA</span>}
      </div>
      <h2>{p.nombre}</h2>
      <p className="pl-resumen">{p.resumen}</p>
      {!compacta && <VistaPrevia p={p} />}
      {!compacta && (
        <p className="pl-arranca"><Icono n={a.icono} tam={13} />Arranca {a.nombre.charAt(0).toLowerCase() + a.nombre.slice(1)}</p>
      )}
      <div className="pl-pie">
        {p.disponible ? (
          <button className="boton primario chico" onClick={abrir}><Icono n="plantilla" tam={13} />Ver y usar</button>
        ) : (
          <>
            <button className="boton chico" onClick={abrir}>Ver</button>
            <span className="texto-3 diminuto">Disponible pronto</span>
          </>
        )}
      </div>
    </li>
  );
}

export function DialogoPlantilla({ p, cerrar }: { p: Plantilla | null; cerrar: () => void }) {
  const flujos = usar((s) => s.flujos);
  const cargarFlujos = usar((s) => s.cargarFlujos);
  const cambiarFlujo = usar((s) => s.cambiarFlujo);
  const irA = usar((s) => s.irA);
  const avisar = usar((s) => s.avisar);
  const [nombre, setNombre] = useState("");
  const [instalando, setInstalando] = useState(false);
  const [problemas, setProblemas] = useState<string[]>([]);

  useEffect(() => {
    if (!p) return;
    // un nombre libre: «Clasificar correos», «Clasificar correos (2)»…
    let n = p.nombre;
    for (let i = 2; flujos.some((f) => f.nombre.toLowerCase() === n.toLowerCase()); i++) n = `${p.nombre} (${i})`;
    setNombre(n);
    setProblemas([]);
  }, [p, flujos]);

  if (!p) return <Dialogo abierto={false} cerrar={cerrar} titulo=""><span /></Dialogo>;
  const orden = ordenDePasos(p);
  const nombreDe = (id: string) => p.nodos.find((n) => n.id === id)?.nombre ?? id;

  const instalar = async () => {
    if (nombre.trim().length < 2) return setProblemas(["Ponle un nombre de al menos dos letras."]);
    setInstalando(true);
    setProblemas([]);
    try {
      const r = await fetch(`/api/plantillas/${encodeURIComponent(p.id)}/instalar`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nombre: nombre.trim() }) });
      const cuerpo = (await r.json().catch(() => ({}))) as { error?: string; problemas?: string[]; flujo?: { id: string; nombre: string } };
      if (!r.ok || !cuerpo.flujo) {
        setProblemas(cuerpo.problemas?.length ? cuerpo.problemas : [cuerpo.error ?? `error ${r.status}`]);
        return;
      }
      marcarPaso("crear");
      await cargarFlujos();
      await cambiarFlujo(cuerpo.flujo.id);
      irA("flujo");
      avisar(`«${cuerpo.flujo.nombre}» es tuyo: pulsa «Correr» para probarlo`);
      cerrar();
    } catch (e) {
      setProblemas([(e as Error).message]);
    } finally {
      setInstalando(false);
    }
  };

  return (
    <Dialogo
      abierto={Boolean(p)}
      cerrar={cerrar}
      titulo={p.nombre}
      sub={p.resumen}
      ancho={680}
      pie={
        p.disponible ? (
          <>
            <span className="dlg-nota"><Icono n="info" tam={13} />Se crea como flujo tuyo; la plantilla no cambia.</span>
            <span className="espacio" />
            <button className="boton" onClick={cerrar}>Cancelar</button>
            <button className="boton primario" disabled={instalando} onClick={() => void instalar()}>{instalando ? <span className="girando" /> : <Icono n="mas" tam={14} />}Usar esta plantilla</button>
          </>
        ) : (
          <>
            <span className="dlg-nota"><Icono n="info" tam={13} />{p.motivo ? `Todavía no se puede instalar: ${p.motivo}.` : "Todavía no se puede instalar."}</span>
            <span className="espacio" />
            <button className="boton" onClick={cerrar}>Cerrar</button>
          </>
        )
      }
    >
      <div className="pila pl-detalle">
        <p>{p.descripcion}</p>
        <div className="pl-detalle-col">
          <section>
            <h3>Qué hace, paso a paso</h3>
            <ol className="pl-pasos">
              {orden.map((n) => (
                <li key={n.id} className={n.tipo === "salida" ? "final" : ""}>
                  <span className="pl-paso-icono"><Icono n={iconoDePaso(n.tipo)} tam={13} /></span>
                  <span className="crece">
                    <b>{n.nombre}</b>
                    {n.ramas.length > 1 && (
                      <small>{n.ramas.map((r) => `${r.cuando ? `si «${r.cuando.replace(/_/g, " ")}»` : "luego"}: ${nombreDe(r.hacia)}`).join(". ")}</small>
                    )}
                  </span>
                </li>
              ))}
            </ol>
          </section>
          <section>
            <h3>Qué necesita</h3>
            <ul className="pl-necesita">
              {p.necesita.map((x, i) => (
                <li key={i}><span className={`pl-nec-icono ${x.tipo}`}><Icono n={NECESITA[x.tipo]?.icono ?? "info"} tam={13} /></span><span>{x.texto}</span></li>
              ))}
            </ul>
            {p.como_probar && (
              <>
                <h3>Cómo probarla</h3>
                <p className="chico texto-2">{p.como_probar}</p>
              </>
            )}
          </section>
        </div>
        {p.disponible && (
          <form className="f-campo" onSubmit={(e) => { e.preventDefault(); void instalar(); }}>
            <label className="f-etiqueta" htmlFor="pl-nombre"><span>Nombre de tu flujo</span></label>
            <input id="pl-nombre" className="campo" value={nombre} onChange={(e) => setNombre(e.target.value)} />
          </form>
        )}
        {problemas.length > 0 && <ul className="f-error" role="alert">{problemas.map((x, i) => <li key={i}>{x}</li>)}</ul>}
      </div>
    </Dialogo>
  );
}
