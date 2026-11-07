import { useEffect, useMemo, useRef, useState } from "react";
import { pedir, usar } from "../estado.ts";
import { useSubruta } from "../rutas.ts";
import { Icono, type NombreIcono } from "../iconos.tsx";
import { hace } from "../lenguaje.ts";
import { Dialogo } from "./Dialogos.tsx";
import { Cargando, EstadoPantalla } from "./Historial.tsx";
import { peso } from "./Archivos.tsx";
import { Selector } from "./Selector.tsx";
import { pedirAlAsistente } from "./Tablero.tsx";
import "./archivos.css";
import "./documentos.css";

/**
 * Documentos: la biblioteca de plantillas de documentos de la plataforma (Word, Excel,
 * PowerPoint y correo HTML) con {{variables}}, logos y estilos de cada área. Cualquier flujo
 * las llena con el paso «Llenar plantilla de documento» (también con la IA).
 *
 *   GET/POST /api/documentos · GET/PUT/DELETE /api/documentos/:id · /archivo · /recursos · /probar
 *
 * La plantilla abierta vive en la URL: /documentos/<id>.
 */

export interface PlantillaDocumento {
  id: string;
  nombre: string;
  descripcion: string;
  area: string;
  formato: "docx" | "xlsx" | "pptx" | "html";
  tamano: number;
  variables: string[];
  repeticiones: Record<string, string[]>;
  imagenes: string[];
  contenido: boolean;
  prohibidas?: string[];
  asunto?: string;
  diapositivas?: number;
  hojas?: string[];
  recursos: { nombre: string; archivo: string; tipo: string; tamano: number }[];
  texto?: string;
  creada: number;
  actualizada: number;
  origen: string;
}

type Formato = PlantillaDocumento["formato"];
export const FORMATO: Record<Formato, { nombre: string; icono: NombreIcono; ext: string }> = {
  docx: { nombre: "Word", icono: "documento", ext: ".docx" },
  xlsx: { nombre: "Excel", icono: "datos", ext: ".xlsx" },
  pptx: { nombre: "PowerPoint", icono: "capas", ext: ".pptx" },
  html: { nombre: "Correo HTML", icono: "correo", ext: ".html" },
};
const ACEPTA = ".docx,.xlsx,.pptx,.html,.htm";
const PERSONA = { "X-Confirmado-Por-Persona": "si" };

/** Sube un archivo (multipart) a la API de Documentos y devuelve lo que contesta. */
async function subir(ruta: string, archivo: File, campos: Record<string, string> = {}, metodo = "POST"): Promise<PlantillaDocumento> {
  const datos = new FormData();
  for (const [k, v] of Object.entries(campos)) datos.append(k, v);
  datos.append("archivo", archivo, archivo.name);
  const r = await fetch(ruta, { method: metodo, body: datos });
  const cuerpo = (await r.json().catch(() => ({}))) as PlantillaDocumento & { error?: string };
  if (!r.ok) throw new Error(cuerpo.error ?? `error ${r.status}`);
  return cuerpo;
}

const EJEMPLO_HTML = `<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><title>Respuesta a su solicitud</title></head>
<body style="margin:0;background:#f4f8fc;font-family:Arial,Helvetica,sans-serif;color:#334155">
  <div style="max-width:640px;margin:24px auto;background:#fff;border:1px solid #d6e4f2;border-radius:10px;padding:28px 32px">
    <img src="{{imagen:logo}}" alt="" style="height:40px">
    <h1 style="color:#0b4f8a;font-size:22px">Hola, {{nombre}}</h1>
    <p>Recibimos su solicitud con el radicado <b>{{radicado}}</b>.</p>
    <ul>{{#pasos}}<li>{{item}}</li>{{/pasos}}</ul>
    {{contenido}}
    <p style="color:#64748b;font-size:13px">Este correo se generó automáticamente.</p>
  </div>
</body>
</html>`;
const EJEMPLO_TEXTO: Record<Exclude<Formato, "html">, string> = {
  docx: "# Respuesta a su solicitud\n\nBogotá, {{fecha}}\n\nSeñor(a) {{nombre}}:\n\nRecibimos su solicitud con el radicado {{radicado}}.\n\n{{contenido}}\n\n| Concepto | Valor |\n| {{#items}}{{item.concepto}} | {{item.valor}} |\n\nCordialmente,\n\n{{firma}}",
  pptx: "# Informe de {{mes}}\n\n{{area}}\n\n## Resumen\n- {{resumen}}\n\n## Cifras\n| Indicador | Valor |\n| {{#cifras}}{{item.nombre}} | {{item.valor}} |\n\n## Próximos pasos\n- {{proximos_pasos}}",
  xlsx: "Informe | {{mes}}\nConcepto | Valor\n{{#items}}{{item.concepto}} | {{item.valor}}",
};

export function VistaDocumentos() {
  const [lista, setLista] = useState<PlantillaDocumento[] | null>(null);
  const [error, setError] = useState("");
  const [formato, setFormato] = useState<Formato | "">("");
  const [q, setQ] = useState("");
  const [nueva, setNueva] = useState(false);
  const [subiendo, setSubiendo] = useState(false);
  const [encima, setEncima] = useState(false);
  const entrada = useRef<HTMLInputElement>(null);
  const avisar = usar((s) => s.avisar);
  const [sub, setSub] = useSubruta();
  const abierta = useMemo(() => (sub && lista ? lista.find((x) => x.id === sub) ?? null : null), [sub, lista]);

  const cargar = () => {
    setError("");
    pedir<{ plantillas: PlantillaDocumento[] }>("/api/documentos").then((r) => setLista(r.plantillas)).catch((e: Error) => setError(e.message));
  };
  useEffect(cargar, []);
  // Vera abre una: {accion:"vista", vista:"documentos", documento}
  useEffect(() => {
    const oir = (e: Event) => {
      const d = (e as CustomEvent<{ accion?: string; vista?: string; documento?: string }>).detail;
      if (d?.accion === "vista" && d.vista === "documentos") {
        cargar();
        if (d.documento) setSub(d.documento);
      }
    };
    window.addEventListener("positiva:control", oir);
    return () => window.removeEventListener("positiva:control", oir);
  }, [setSub]);

  const subirArchivos = async (archivos: File[]) => {
    const f = archivos[0];
    if (!f) return;
    if (!/\.(docx|xlsx|pptx|html?)$/i.test(f.name)) return avisar(`«${f.name}»: sube un Word, Excel, PowerPoint o HTML`, "error");
    setSubiendo(true);
    try {
      const p = await subir("/api/documentos", f);
      setLista((l) => [p, ...(l ?? [])]);
      setSub(p.id);
      avisar(`«${p.nombre}» quedó en la biblioteca con ${p.variables.length} variable(s)`);
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setSubiendo(false);
    }
  };

  const conteo = (f: Formato) => (lista ?? []).filter((p) => p.formato === f).length;
  const texto = q.trim().toLowerCase();
  const visibles = (lista ?? []).filter((p) => (!formato || p.formato === formato) && (!texto || `${p.nombre} ${p.descripcion} ${p.area} ${p.id}`.toLowerCase().includes(texto)));

  return (
    <div className={`vista pr-vista documentos ${encima ? "doc-encima" : ""}`}
      onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setEncima(true); } }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setEncima(false); }}
      onDrop={(e) => { e.preventDefault(); setEncima(false); void subirArchivos([...e.dataTransfer.files]); }}>
      <header className="vista-cabeza">
        <div className="crece">
          <h1>Documentos</h1>
          <p className="texto-2">Plantillas de Word, Excel, PowerPoint y correo HTML con el logo y los estilos de cada área, para que cualquier flujo las llene con sus datos o con la IA.</p>
        </div>
        <div className="doc-acciones">
          <button className="boton" onClick={() => setNueva(true)}><Icono n="editar" tam={14} />Escribir una</button>
          <button className="boton primario" disabled={subiendo} onClick={() => entrada.current?.click()}>{subiendo ? <span className="girando" /> : <Icono n="mas" tam={14} />}Subir plantilla</button>
          <input ref={entrada} type="file" hidden accept={ACEPTA} onChange={(e) => { void subirArchivos([...(e.target.files ?? [])]); e.target.value = ""; }} />
        </div>
      </header>
      {lista && lista.length > 0 && (
        <div className="pr-barra-filtros">
          <label className="pr-buscar">
            <Icono n="buscar" tam={15} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar por nombre, área o descripción" aria-label="Buscar plantillas" />
            {q && <button aria-label="Borrar la búsqueda" onClick={() => setQ("")}><Icono n="cerrar" tam={13} /></button>}
          </label>
          <div className="filtros" role="group" aria-label="Formato">
            <button className={!formato ? "activo" : ""} aria-pressed={!formato} onClick={() => setFormato("")}>Todas <span>{lista.length}</span></button>
            {(Object.keys(FORMATO) as Formato[]).filter((f) => conteo(f)).map((f) => (
              <button key={f} className={formato === f ? "activo" : ""} aria-pressed={formato === f} onClick={() => setFormato(formato === f ? "" : f)}>{FORMATO[f].nombre} <span>{conteo(f)}</span></button>
            ))}
          </div>
        </div>
      )}
      {error ? (
        <EstadoPantalla icono="alerta" tono="rojo" titulo="No se pudieron cargar las plantillas" texto={error} accion={<button className="boton" onClick={cargar}>Intentar de nuevo</button>} />
      ) : !lista ? (
        <Cargando filas={3} />
      ) : !lista.length ? (
        <div className="doc-vacio">
          <EstadoPantalla icono="documento" titulo="Todavía no hay plantillas de documentos"
            texto={<>Sube un Word, Excel, PowerPoint o HTML con <code>{"{{variables}}"}</code> donde van los datos (también puedes arrastrarlo aquí), escribe una desde cero o pídesela a Vera.</>}
            accion={<div className="fila doc-vacio-acciones"><button className="boton primario" onClick={() => entrada.current?.click()}><Icono n="mas" tam={14} />Subir plantilla</button><button className="boton" onClick={() => pedirAlAsistente("Ayúdame a crear una plantilla de documento para la biblioteca de Documentos. Pregúntame qué formato y qué datos lleva.")}><Icono n="chispa" tam={14} />Pídesela a Vera</button></div>} />
          <Sintaxis />
        </div>
      ) : !visibles.length ? (
        <EstadoPantalla icono="buscar" titulo="Ninguna plantilla coincide" texto="Prueba con otra palabra o quita el filtro de formato." accion={<button className="boton" onClick={() => { setQ(""); setFormato(""); }}>Ver todas</button>} />
      ) : (
        <ul className="pl-rejilla doc-rejilla">
          {visibles.map((p) => <TarjetaDocumento key={p.id} p={p} abrir={() => setSub(p.id)} />)}
        </ul>
      )}
      {encima && <div className="doc-soltar" aria-hidden><Icono n="adjunto" tam={22} /><b>Suelta el archivo para agregarlo a la biblioteca</b></div>}
      <DialogoDocumento p={abierta} cerrar={() => setSub(null)} cambiar={(p) => setLista((l) => (l ?? []).map((x) => (x.id === p.id ? p : x)))}
        borrada={(id) => { setLista((l) => (l ?? []).filter((x) => x.id !== id)); setSub(null); }} />
      <DialogoNueva abierto={nueva} cerrar={() => setNueva(false)} creada={(p) => { setLista((l) => [p, ...(l ?? [])]); setNueva(false); setSub(p.id); }} />
    </div>
  );
}

/** En el panel de un paso (documento_plantilla): elegir una plantilla de la biblioteca, con lo que pide. */
export function CampoPlantillaDocumento({ valor, alCambiar }: { valor: string; alCambiar: (id: string) => void }) {
  const [lista, setLista] = useState<PlantillaDocumento[] | null>(null);
  const irA = usar((s) => s.irA);
  const fijarSub = usar((s) => s.fijarSub);
  useEffect(() => {
    void pedir<{ plantillas: PlantillaDocumento[] }>("/api/documentos").then((r) => setLista(r.plantillas)).catch(() => setLista([]));
  }, []);
  if (valor.includes("{{")) return <input className="campo mono" value={valor} onChange={(e) => alCambiar(e.target.value)} />;
  const elegida = lista?.find((p) => p.id === valor);
  return (
    <div className="pila doc-campo">
      <Selector
        etiqueta="Plantilla de documento"
        placeholder={lista === null ? "Cargando la biblioteca…" : lista.length ? "Elige una plantilla…" : "La biblioteca está vacía"}
        valor={valor || null}
        opciones={[...(lista ?? []).map((p) => ({ valor: p.id, etiqueta: p.nombre, texto: `${p.nombre} ${p.area}`, detalle: `${FORMATO[p.formato].nombre}${p.area ? ` · ${p.area}` : ""} · ${p.variables.length} variable(s)` })),
          ...(valor && lista && !elegida ? [{ valor, etiqueta: valor, detalle: "No está en la biblioteca" }] : [])]}
        alCambiar={(v) => alCambiar(String(v))}
        tamano="chico"
      />
      {valor && lista && !elegida && <p className="f-error">«{valor}» no está en la biblioteca de Documentos: súbela o elige otra.</p>}
      {elegida && (elegida.variables.length > 0 || Object.keys(elegida.repeticiones).length > 0) && (
        <div className="doc-variables">{elegida.variables.slice(0, 10).map((v) => <span key={v} className="ficha suave">{`{{${v}}}`}</span>)}
          {Object.keys(elegida.repeticiones).map((l) => <span key={l} className="ficha activa">{`{{#${l}}}`}</span>)}</div>
      )}
      <button type="button" className="enlace" onClick={() => { irA("documentos"); if (valor) fijarSub(valor); }}>{elegida ? "Ver la plantilla en Documentos" : "Abrir Documentos para subir una"}</button>
    </div>
  );
}

function TarjetaDocumento({ p, abrir }: { p: PlantillaDocumento; abrir: () => void }) {
  const f = FORMATO[p.formato];
  const listas = Object.keys(p.repeticiones ?? {});
  return (
    <li className="pl-tarjeta doc-tarjeta">
      <div className="pl-cabeza">
        <span className={`pl-icono doc-icono ${p.formato}`}><Icono n={f.icono} tam={17} /></span>
        <span className="pl-categoria">{f.nombre}{p.area ? ` · ${p.area}` : ""}</span>
        {p.recursos?.length > 0 && <span className="ficha" title="Imágenes guardadas con la plantilla"><Icono n="ojo" tam={11} />{p.recursos.length === 1 ? "1 imagen" : `${p.recursos.length} imágenes`}</span>}
      </div>
      <h2>{p.nombre}</h2>
      {p.descripcion && <p className="pl-resumen">{p.descripcion}</p>}
      <div className="doc-variables" aria-label="Variables">
        {p.variables.slice(0, 6).map((v) => <span key={v} className="ficha suave">{`{{${v}}}`}</span>)}
        {listas.slice(0, 2).map((l) => <span key={l} className="ficha activa" title={`Se repite por cada elemento de «${l}»`}>{`{{#${l}}}`}</span>)}
        {p.variables.length > 6 && <span className="texto-3 diminuto">+{p.variables.length - 6} más</span>}
        {!p.variables.length && !listas.length && <span className="texto-3 diminuto">Sin variables: sale tal cual (o la IA arma el contenido)</span>}
      </div>
      <div className="pl-pie">
        <button className="boton primario chico" onClick={abrir}><Icono n="ojo" tam={13} />Ver y usar</button>
        <span className="texto-3 diminuto">Cambió {hace(p.actualizada)}</span>
      </div>
    </li>
  );
}

/** Cómo se escribe una plantilla: la misma sintaxis en Word, Excel, PowerPoint y HTML. */
function Sintaxis() {
  return (
    <section className="doc-sintaxis" aria-label="Cómo se escribe una plantilla">
      <h3>Cómo se escribe una plantilla</h3>
      <dl>
        <div><dt><code>{"{{nombre}}"}</code></dt><dd>Un dato: del flujo, de los que le pase el paso o de lo que escriba la IA. En HTML se escapa; <code>{"{{{x}}}"}</code> va tal cual.</dd></div>
        <div><dt><code>{"{{#items}}"}</code></dt><dd>En una fila de tabla o en un párrafo: se repite por cada elemento de «items», con <code>{"{{item.campo}}"}</code> e <code>{"{{indice}}"}</code>. En HTML: <code>{"{{#items}} … {{/items}}"}</code>.</dd></div>
        <div><dt><code>{"{{imagen:logo}}"}</code></dt><dd>Una imagen guardada con la plantilla (logo, firma, sello) o una de la ejecución. Con ancho: <code>{"{{imagen:logo 4cm}}"}</code>.</dd></div>
        <div><dt><code>{"{{contenido}}"}</code></dt><dd>Dónde va lo que redacta la IA (secciones, diapositivas u hojas) con el diseño de la plantilla.</dd></div>
      </dl>
    </section>
  );
}

function DialogoDocumento({ p, cerrar, cambiar, borrada }: { p: PlantillaDocumento | null; cerrar: () => void; cambiar: (p: PlantillaDocumento) => void; borrada: (id: string) => void }) {
  const avisar = usar((s) => s.avisar);
  const [detalle, setDetalle] = useState<PlantillaDocumento | null>(null);
  const [campos, setCampos] = useState({ nombre: "", area: "", descripcion: "", asunto: "" });
  const [guardando, setGuardando] = useState(false);
  const [confirmarBorrar, setConfirmarBorrar] = useState(false);
  const [error, setError] = useState("");
  const [vuelta, setVuelta] = useState(0);
  const reemplazo = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setDetalle(null);
    setError("");
    setConfirmarBorrar(false);
    if (!p) return;
    setCampos({ nombre: p.nombre, area: p.area ?? "", descripcion: p.descripcion ?? "", asunto: p.asunto ?? "" });
    pedir<PlantillaDocumento>(`/api/documentos/${encodeURIComponent(p.id)}`).then(setDetalle).catch((e: Error) => setError(e.message));
  }, [p?.id, p?.actualizada]);

  if (!p) return <Dialogo abierto={false} cerrar={cerrar} titulo=""><span /></Dialogo>;
  const d = detalle ?? p;
  const f = FORMATO[d.formato];
  const cambio = campos.nombre !== d.nombre || campos.area !== (d.area ?? "") || campos.descripcion !== (d.descripcion ?? "") || campos.asunto !== (d.asunto ?? "");
  const url = `/api/documentos/${encodeURIComponent(d.id)}`;
  const listo = (x: PlantillaDocumento) => { setDetalle(x); cambiar(x); setVuelta((v) => v + 1); };

  const guardar = async () => {
    if (campos.nombre.trim().length < 2) return setError("Ponle un nombre de al menos dos letras.");
    setGuardando(true);
    setError("");
    try {
      listo(await pedir<PlantillaDocumento>(url, { method: "PUT", body: JSON.stringify({ ...campos, nombre: campos.nombre.trim() }) }));
      avisar("Cambios guardados");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setGuardando(false);
    }
  };
  const reemplazar = async (archivo?: File) => {
    if (!archivo) return;
    setGuardando(true);
    setError("");
    try {
      listo(await subir(url, archivo, {}, "PUT"));
      avisar("Archivo reemplazado: los flujos que la usan ya llenan la versión nueva");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setGuardando(false);
    }
  };
  const borrar = async () => {
    try {
      await pedir(url, { method: "DELETE", headers: PERSONA });
      avisar(`«${d.nombre}» se borró de la biblioteca`);
      borrada(d.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Dialogo abierto cerrar={cerrar} titulo={d.nombre} sub={`${f.nombre}${d.area ? ` · ${d.area}` : ""} · ${peso(d.tamano)} · cambió ${hace(d.actualizada)}`} ancho={860}
      pie={
        <>
          <a className="boton chico" href={`${url}/archivo?descargar=1`} download><Icono n="descargar" tam={13} />Descargar</a>
          <button className="boton chico" disabled={guardando} onClick={() => reemplazo.current?.click()}><Icono n="rehacer" tam={13} />Reemplazar archivo</button>
          <input ref={reemplazo} type="file" hidden accept={ACEPTA} onChange={(e) => { void reemplazar(e.target.files?.[0]); e.target.value = ""; }} />
          <span className="espacio" />
          {confirmarBorrar ? (
            <span className="pr-confirmar" role="group" aria-label="Confirmar el borrado">
              <span>¿Borrarla? Los flujos que la usan fallarán.</span>
              <button className="boton peligro chico" onClick={() => void borrar()}>Sí, borrar</button>
              <button className="boton chico" onClick={() => setConfirmarBorrar(false)}>Cancelar</button>
            </span>
          ) : (
            <button className="boton chico peligro" onClick={() => setConfirmarBorrar(true)}><Icono n="papelera" tam={13} />Borrar</button>
          )}
        </>
      }>
      <div className="pila doc-detalle">
        {error && <p className="f-error" role="alert">{error}</p>}
        <div className="doc-columnas">
          <section className="doc-previa" aria-label="Vista previa">
            <h3>Vista previa</h3>
            {d.formato === "html" ? (
              <iframe key={vuelta} src={`${url}/archivo`} title={`Vista previa de ${d.nombre}`} sandbox="" />
            ) : detalle ? (
              <pre>{detalle.texto || "(la plantilla no tiene texto)"}</pre>
            ) : (
              <div className="arch-leyendo"><span className="girando" />Leyendo la plantilla…</div>
            )}
            {d.diapositivas !== undefined && <p className="texto-3 diminuto">{d.diapositivas} diapositiva(s) en la plantilla.</p>}
            {d.hojas && <p className="texto-3 diminuto">Hojas: {d.hojas.join(", ")}</p>}
          </section>
          <section className="doc-datos">
            <form className="pila" onSubmit={(e) => { e.preventDefault(); void guardar(); }}>
              <label className="f-campo"><span className="f-etiqueta"><span>Nombre</span></span><input className="campo" value={campos.nombre} onChange={(e) => setCampos({ ...campos, nombre: e.target.value })} /></label>
              <label className="f-campo"><span className="f-etiqueta"><span>Área</span></span><input className="campo" value={campos.area} placeholder="Ej.: Servicio al cliente" onChange={(e) => setCampos({ ...campos, area: e.target.value })} /></label>
              <label className="f-campo"><span className="f-etiqueta"><span>Para qué sirve</span></span><textarea className="campo" rows={2} value={campos.descripcion} onChange={(e) => setCampos({ ...campos, descripcion: e.target.value })} /></label>
              {d.formato === "html" && (
                <label className="f-campo"><span className="f-etiqueta"><span>Asunto del correo</span></span><input className="campo" value={campos.asunto} placeholder="Ej.: Respuesta a su solicitud {{radicado}}" onChange={(e) => setCampos({ ...campos, asunto: e.target.value })} /></label>
              )}
              {cambio && <div className="fila"><span className="espacio" /><button className="boton primario chico" disabled={guardando}>{guardando ? <span className="girando" /> : <Icono n="guardar" tam={13} />}Guardar cambios</button></div>}
            </form>
            <Variables d={d} />
          </section>
        </div>
        <Recursos d={d} url={url} listo={listo} />
        <ProbarPlantilla d={d} url={url} />
        <UsoEnFlujo d={d} />
      </div>
    </Dialogo>
  );
}

function Variables({ d }: { d: PlantillaDocumento }) {
  const listas = Object.entries(d.repeticiones ?? {});
  const recursos = new Set(d.recursos?.map((r) => r.nombre));
  return (
    <div className="doc-bloque">
      <h3>Lo que pide</h3>
      {d.variables.length ? (
        <div className="doc-variables">{d.variables.map((v) => <span key={v} className="ficha suave">{`{{${v}}}`}</span>)}</div>
      ) : <p className="texto-3 chico">No tiene variables sueltas.</p>}
      {listas.length > 0 && (
        <ul className="doc-listas">
          {listas.map(([l, campos]) => <li key={l}><span className="ficha activa">{`{{#${l}}}`}</span> se repite por cada elemento{campos.length ? <>, con {campos.map((c) => <code key={c}>{`item.${c}`}</code>)}</> : null}</li>)}
        </ul>
      )}
      {d.imagenes.length > 0 && (
        <p className="chico texto-2">Imágenes: {d.imagenes.map((i) => <span key={i} className={`ficha ${recursos.has(i) ? "tono-verde" : ""}`} title={recursos.has(i) ? "Guardada con la plantilla" : "Se busca en la ejecución (o súbela abajo)"}>{i}</span>)}</p>
      )}
      {d.contenido && <p className="chico texto-2"><Icono n="chispa" tam={12} /> Tiene <code>{"{{contenido}}"}</code>: ahí va lo que redacta la IA.</p>}
      {(d.prohibidas?.length ?? 0) > 0 && <p className="chico f-error">Pide variables de entorno ({d.prohibidas!.join(", ")}): desde una plantilla no se leen, quedan vacías.</p>}
    </div>
  );
}

function Recursos({ d, url, listo }: { d: PlantillaDocumento; url: string; listo: (p: PlantillaDocumento) => void }) {
  const avisar = usar((s) => s.avisar);
  const [nombre, setNombre] = useState("logo");
  const [subiendo, setSubiendo] = useState(false);
  const [quitar, setQuitar] = useState<string | null>(null);
  const entrada = useRef<HTMLInputElement>(null);
  const agregar = async (f?: File) => {
    if (!f) return;
    setSubiendo(true);
    try {
      listo(await subir(`${url}/recursos`, f, { nombre }));
      avisar(`Imagen «${nombre}» guardada: úsala con {{imagen:${nombre}}}`);
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setSubiendo(false);
    }
  };
  const borrar = async (n: string) => {
    try {
      listo(await pedir<PlantillaDocumento>(`${url}/recursos/${encodeURIComponent(n)}`, { method: "DELETE", headers: PERSONA }));
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setQuitar(null);
    }
  };
  return (
    <section className="doc-bloque" aria-label="Imágenes de la plantilla">
      <h3>Logos e imágenes</h3>
      <div className="arch-capsulas">
        {d.recursos.map((r) => (
          <span key={r.nombre} className="capsula" title={`{{imagen:${r.nombre}}}`}>
            <span className="capsula-cuerpo"><img src={`${url}/recursos/${encodeURIComponent(r.nombre)}?v=${d.actualizada}`} alt="" />
              <span className="capsula-texto"><b>{r.nombre}</b><small>{peso(r.tamano)}</small></span></span>
            {quitar === r.nombre ? (
              <span className="pr-confirmar doc-quitar"><button className="boton peligro chico" onClick={() => void borrar(r.nombre)}>Quitar</button><button className="boton chico" onClick={() => setQuitar(null)}>No</button></span>
            ) : <button type="button" className="capsula-quitar" aria-label={`Quitar ${r.nombre}`} onClick={() => setQuitar(r.nombre)}><Icono n="cerrar" tam={11} /></button>}
          </span>
        ))}
      </div>
      <div className="fila doc-agregar">
        <label className="f-campo crece"><span className="f-etiqueta"><span>Nombre de la imagen</span></span>
          <input className="campo" value={nombre} onChange={(e) => setNombre(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_").slice(0, 40))} /></label>
        <button className="boton chico" disabled={subiendo || !nombre} onClick={() => entrada.current?.click()}>{subiendo ? <span className="girando" /> : <Icono n="mas" tam={13} />}Elegir imagen</button>
        <input ref={entrada} type="file" hidden accept=".png,.jpg,.jpeg,.webp,.gif" onChange={(e) => { void agregar(e.target.files?.[0]); e.target.value = ""; }} />
      </div>
      <p className="texto-3 diminuto">En la plantilla se pone con <code>{`{{imagen:${nombre || "logo"}}}`}</code>. PNG, JPEG, WebP o GIF, hasta 5 MB.</p>
    </section>
  );
}

function ProbarPlantilla({ d, url }: { d: PlantillaDocumento; url: string }) {
  const avisar = usar((s) => s.avisar);
  const [valores, setValores] = useState<Record<string, string>>({});
  const [probando, setProbando] = useState(false);
  const variables = d.variables.filter((v) => !v.includes("[")).slice(0, 12);
  if (!variables.length && !Object.keys(d.repeticiones ?? {}).length) return null;
  const probar = async () => {
    setProbando(true);
    try {
      const datos: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(valores)) {
        const partes = k.split(".");
        let nodo = datos;
        for (const p of partes.slice(0, -1)) nodo = (nodo[p] ??= {}) as Record<string, unknown>;
        nodo[partes.at(-1)!] = v;
      }
      for (const [l, campos] of Object.entries(d.repeticiones ?? {}))
        datos[l] ??= [1, 2].map((i) => (campos.length ? Object.fromEntries(campos.map((c) => [c, `${c} ${i}`])) : `elemento ${i}`));
      const r = await fetch(`${url}/probar`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ datos }) });
      if (!r.ok) throw new Error(((await r.json().catch(() => ({}))) as { error?: string }).error ?? `error ${r.status}`);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `prueba-${d.id}${FORMATO[d.formato].ext}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setProbando(false);
    }
  };
  return (
    <section className="doc-bloque" aria-label="Probar con datos">
      <h3>Probarla con datos de ejemplo</h3>
      <div className="doc-probar">
        {variables.map((v) => (
          <label key={v} className="f-campo"><span className="f-etiqueta"><span>{v}</span></span>
            <input className="campo" value={valores[v] ?? ""} placeholder={`Valor de ${v}`} onChange={(e) => setValores({ ...valores, [v]: e.target.value })} /></label>
        ))}
      </div>
      <div className="fila">
        <span className="texto-3 diminuto crece">{Object.keys(d.repeticiones ?? {}).length ? "Las listas se llenan con dos elementos de ejemplo. " : ""}Lo vacío queda vacío.</span>
        <button className="boton chico" disabled={probando} onClick={() => void probar()}>{probando ? <span className="girando" /> : <Icono n="descargar" tam={13} />}Descargar llena</button>
      </div>
    </section>
  );
}

function UsoEnFlujo({ d }: { d: PlantillaDocumento }) {
  const avisar = usar((s) => s.avisar);
  const datos = Object.fromEntries(d.variables.filter((v) => !v.includes(".") && !v.includes("[")).slice(0, 8).map((v) => [v, `{{${v}}}`]));
  const paso = { tipo: "documento_plantilla", config: { plantilla: d.id, ...(Object.keys(datos).length ? { datos } : {}), salida: d.formato === "html" ? "correo_html" : "documento" } };
  const texto = JSON.stringify(paso, null, 2);
  return (
    <section className="doc-bloque" aria-label="Usarla en un flujo">
      <div className="fila">
        <h3 className="crece">Usarla en un flujo</h3>
        <button className="boton chico fantasma" onClick={() => void navigator.clipboard.writeText(texto).then(() => avisar("Paso copiado"))}><Icono n="copiar" tam={13} />Copiar el paso</button>
        <button className="boton chico" onClick={() => pedirAlAsistente(`Usa la plantilla de documentos «${d.nombre}» (id ${d.id}) en un flujo: pregúntame en cuál y con qué datos, y agrega el paso documento_plantilla.`)}><Icono n="chispa" tam={13} />Pedírselo a Vera</button>
      </div>
      <p className="chico texto-2">Agrega el paso «Llenar plantilla de documento» y elige esta plantilla. {d.formato === "html" ? <>Luego, en «Enviar correo» o «Responder correo», usa <code>{"{{correo_html.html}}"}</code> como HTML{d.asunto ? <> y <code>{"{{correo_html.asunto}}"}</code> como asunto</> : null}.</> : <>El archivo queda en la ejecución: adjúntalo con <code>{"{{documento}}"}</code>.</>} Con «La IA llena» escribe lo que falte o arma el contenido completo.</p>
      <pre className="doc-codigo">{texto}</pre>
    </section>
  );
}

function DialogoNueva({ abierto, cerrar, creada }: { abierto: boolean; cerrar: () => void; creada: (p: PlantillaDocumento) => void }) {
  const [formato, setFormato] = useState<Formato>("html");
  const [nombre, setNombre] = useState("");
  const [area, setArea] = useState("");
  const [asunto, setAsunto] = useState("");
  const [contenido, setContenido] = useState(EJEMPLO_HTML);
  const [tocado, setTocado] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!tocado) setContenido(formato === "html" ? EJEMPLO_HTML : EJEMPLO_TEXTO[formato]);
  }, [formato, tocado]);
  useEffect(() => {
    if (abierto) { setError(""); setTocado(false); setNombre(""); setArea(""); setAsunto(""); }
  }, [abierto]);
  const guardar = async () => {
    if (nombre.trim().length < 2) return setError("Ponle un nombre de al menos dos letras.");
    setGuardando(true);
    setError("");
    try {
      creada(await pedir<PlantillaDocumento>("/api/documentos", { method: "POST", body: JSON.stringify({ nombre: nombre.trim(), area, formato, ...(formato === "html" ? { contenido, asunto } : { texto: contenido }) }) }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setGuardando(false);
    }
  };
  return (
    <Dialogo abierto={abierto} cerrar={cerrar} titulo="Escribir una plantilla" sub="Un correo HTML tal cual, o un Word, PowerPoint o Excel armado desde texto. Después puedes reemplazarla por un archivo con tu diseño." ancho={760}
      pie={<><span className="dlg-nota"><Icono n="info" tam={13} />Queda en la biblioteca para todos los flujos.</span><span className="espacio" /><button className="boton" onClick={cerrar}>Cancelar</button><button className="boton primario" disabled={guardando} onClick={() => void guardar()}>{guardando ? <span className="girando" /> : <Icono n="guardar" tam={14} />}Guardar plantilla</button></>}>
      <div className="pila">
        <div className="filtros" role="group" aria-label="Formato">
          {(Object.keys(FORMATO) as Formato[]).map((f) => (
            <button key={f} className={formato === f ? "activo" : ""} aria-pressed={formato === f} onClick={() => setFormato(f)}><Icono n={FORMATO[f].icono} tam={12} />{FORMATO[f].nombre}</button>
          ))}
        </div>
        <div className="doc-nueva-campos">
          <label className="f-campo"><span className="f-etiqueta"><span>Nombre</span></span><input className="campo" autoFocus value={nombre} placeholder="Ej.: Respuesta a solicitudes" onChange={(e) => setNombre(e.target.value)} /></label>
          <label className="f-campo"><span className="f-etiqueta"><span>Área</span></span><input className="campo" value={area} placeholder="Opcional" onChange={(e) => setArea(e.target.value)} /></label>
          {formato === "html" && <label className="f-campo"><span className="f-etiqueta"><span>Asunto del correo</span></span><input className="campo" value={asunto} placeholder="Ej.: Su solicitud {{radicado}}" onChange={(e) => setAsunto(e.target.value)} /></label>}
        </div>
        <label className="f-campo">
          <span className="f-etiqueta"><span>{formato === "html" ? "HTML del correo" : "Contenido («# » título, «## » diapositiva o sección, «- » viñeta, «|» tabla)"}</span></span>
          <textarea className="campo doc-editor" rows={14} spellCheck={false} value={contenido} onChange={(e) => { setContenido(e.target.value); setTocado(true); }} />
        </label>
        {error && <p className="f-error" role="alert">{error}</p>}
        <Sintaxis />
      </div>
    </Dialogo>
  );
}
