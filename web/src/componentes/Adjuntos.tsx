import { memo, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { guardarLocal, leerLocal, usar } from "../estado.ts";
import { Icono, type NombreIcono } from "../iconos.tsx";
import { NOMBRES_DESENLACE, TONO_DESENLACE, motivoLegible, segundos } from "../progreso.ts";
import { Selector } from "./Selector.tsx";
import {
  busquedas as derivarBusquedas,
  dictamen as derivarDictamen,
  lectura as derivarLectura,
  nombreTipoHoja,
  recorridos as derivarRecorridos,
  textoDe,
  urlArchivo,
  type Busqueda,
  type Documento,
  type Hoja,
  type PasoHoja,
  type RecorridoCampo,
  type Resultado,
  type TextoDocumento,
} from "../adjuntos.ts";
import "./adjuntos.css";

type Seleccion = { clave: string; pagina: number };
type Marca = { id: string; pagina: number | null; spans: [number, number][]; tramo?: [number, number] | null; etiqueta: string; n: number };

const CLAVE_MEDIDAS = "positiva-ia:adjuntos:v1";
const MEDIDAS = { docs: 300, lado: 400, imagen: 52 };

const pct = (x: number | null | undefined) => (x === null || x === undefined ? "" : `${Math.round(x * 100)} %`);

const NOMBRE_METODO: Record<string, string> = { texto: "Texto del PDF", ocr: "OCR", vision: "Visión IA", ilegible: "Sin texto" };
const ICONO_METODO: Record<string, NombreIcono> = { texto: "documento", ocr: "escaner", vision: "ojo", ilegible: "alerta" };

function iconoDeTipo(tipo: string, esCuerpo: boolean): NombreIcono {
  if (esCuerpo) return "correo";
  if (tipo.includes("imagen")) return "ojo";
  if (tipo.includes("pdf")) return "documento";
  return "adjunto";
}

/** Divisor que solo mueve una variable CSS mientras se arrastra. */
function Divisor({ contenedor, variable, calcular, min, max, alSoltar, eje = "x" }: { contenedor: React.RefObject<HTMLElement | null>; variable: string; calcular: (e: PointerEvent, caja: DOMRect) => number; min: number; max: () => number; alSoltar: (v: number) => void; eje?: "x" | "y" }) {
  return (
    <div
      className={`adj-divisor adj-divisor-${eje}`}
      role="separator"
      aria-orientation={eje === "x" ? "vertical" : "horizontal"}
      onPointerDown={(e) => {
        const el = contenedor.current;
        if (!el || e.button !== 0) return;
        e.preventDefault();
        const caja = el.getBoundingClientRect();
        const asa = e.currentTarget;
        asa.setPointerCapture(e.pointerId);
        asa.classList.add("activo");
        document.body.classList.add("arrastrando", eje === "x" ? "arrastrando-x" : "arrastrando-y");
        let ultimo = 0;
        let cuadro = 0;
        const mover = (m: PointerEvent) => {
          ultimo = Math.max(min, Math.min(max(), calcular(m, caja)));
          cancelAnimationFrame(cuadro);
          cuadro = requestAnimationFrame(() => el.style.setProperty(variable, variable === "--imagen" ? `${ultimo}%` : `${ultimo}px`));
        };
        const soltar = () => {
          asa.removeEventListener("pointermove", mover);
          asa.removeEventListener("pointerup", soltar);
          asa.classList.remove("activo");
          document.body.classList.remove("arrastrando", "arrastrando-x", "arrastrando-y");
          if (ultimo) alSoltar(ultimo);
        };
        asa.addEventListener("pointermove", mover);
        asa.addEventListener("pointerup", soltar);
      }}
    />
  );
}

// -------------------------------------------------------------- documentos

const Miniatura = memo(function Miniatura({ corrida, h, activa, alElegir }: { corrida: string; h: Hoja; activa: boolean; alElegir: () => void }) {
  const proporcion = h.ancho && h.alto ? h.alto / h.ancho : 1.32;
  return (
    <motion.button layout initial={{ opacity: 0, scale: 0.86 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: "spring", stiffness: 420, damping: 30 }} className={`miniatura m-${h.metodo} ${activa ? "activa" : ""}`} onClick={alElegir} title={`Hoja ${h.numero} · ${NOMBRE_METODO[h.metodo]}${h.confianza ? ` · confianza ${pct(h.confianza)}` : ""}`}>
      <span className="miniatura-papel" style={{ aspectRatio: `1 / ${Math.min(1.6, Math.max(0.6, proporcion))}` }}>
        {h.mini ? <img src={urlArchivo(corrida, h.mini)} alt="" loading="lazy" draggable={false} /> : <span className="miniatura-texto"><Icono n="documento" tam={18} /></span>}
        {(h.girada || h.vision || h.relectura200) && (
          <span className="miniatura-marcas">
            {h.girada ? <i title={`Se leyó girada ${h.girada}°`}>↻</i> : null}
            {h.relectura200 ? <i title="Releída a 200 ppp">200</i> : null}
            {h.vision ? <i className="vis" title="La transcribió el modelo con visión"><Icono n="ojo" tam={10} /></i> : null}
          </span>
        )}
      </span>
      <span className="miniatura-pie">
        <b>{h.numero}</b>
        <span className="miniatura-metodo"><Icono n={ICONO_METODO[h.metodo] ?? "documento"} tam={10} />{h.metodo === "ocr" && h.confianza ? pct(h.confianza) : h.metodo === "texto" ? "Texto" : NOMBRE_METODO[h.metodo]}</span>
      </span>
    </motion.button>
  );
});

function HojaEnLectura({ corrida, numero, mini }: { corrida: string; numero: number; mini?: string }) {
  return (
    <div className="miniatura leyendo" title={`Leyendo la hoja ${numero}`}>
      <span className="miniatura-papel">{mini && <img src={urlArchivo(corrida, mini)} alt="" draggable={false} />}<span className="escaneo" /></span>
      <span className="miniatura-pie"><b>{numero}</b><span className="miniatura-metodo">leyendo…</span></span>
    </div>
  );
}

function ListaDocumentos({ corrida, documentos, descartados, sel, enCurso, elegir }: { corrida: string; documentos: Documento[]; descartados: { nombre: string; detalle: string }[]; sel: Seleccion | null; enCurso: (Seleccion & { mini?: string }) | null; elegir: (s: Seleccion) => void }) {
  const [verDescartados, setVerDescartados] = useState(false);
  return (
    <div className="adj-docs desplazable">
      {documentos.map((d) => {
        const ocr = d.hojas.filter((h) => h.metodo === "ocr").length;
        const vision = d.hojas.filter((h) => h.vision).length;
        const ilegibles = d.hojas.filter((h) => h.metodo === "ilegible").length;
        const elegido = sel?.clave === d.clave;
        return (
          <section key={d.clave} className={`adj-doc ${elegido ? "elegido" : ""} ${d.estado}`}>
            <button className="adj-doc-cabeza" onClick={() => elegir({ clave: d.clave, pagina: d.hojas[0]?.numero ?? 1 })}>
              <span className={`adj-doc-icono ${d.esCuerpo ? "es-cuerpo" : `es-${d.tipo}`}`}><Icono n={iconoDeTipo(d.tipo, d.esCuerpo)} tam={15} /></span>
              <span className="crece">
                <b className="adj-doc-nombre">{d.nombre}</b>
                <span className="adj-doc-meta">
                  {d.esCuerpo ? "texto del correo" : d.paginas ? `${d.paginas} hoja${d.paginas === 1 ? "" : "s"}` : d.tipo}
                  {d.ms ? ` · ${segundos(d.ms)}` : ""}
                </span>
              </span>
              {d.estado === "leyendo" && <span className="girando" title="Leyendo" />}
              {d.estado === "fallo" && <span className="ficha tono-rojo" title={d.error}>no se leyó</span>}
            </button>
            {(ocr > 0 || vision > 0 || ilegibles > 0 || d.aviso) && (
              <div className="adj-doc-fichas">
                {ocr > 0 && <span className="ficha tono-turquesa"><Icono n="escaner" tam={11} />{ocr} con OCR</span>}
                {vision > 0 && <span className="ficha tono-violeta"><Icono n="ojo" tam={11} />{vision} por visión</span>}
                {ilegibles > 0 && <span className="ficha tono-rojo">{ilegibles} sin texto</span>}
                {d.aviso && <span className="ficha tono-ambar" title={d.aviso}>aviso</span>}
              </div>
            )}
            {!d.esCuerpo && (d.hojas.length > 0 || enCurso?.clave === d.clave) && (
              <div className="adj-miniaturas">
                <AnimatePresence initial={false}>
                  {d.hojas.map((h) => <Miniatura key={h.numero} corrida={corrida} h={h} activa={elegido && sel?.pagina === h.numero} alElegir={() => elegir({ clave: d.clave, pagina: h.numero })} />)}
                </AnimatePresence>
                {enCurso?.clave === d.clave && !d.hojas.some((h) => h.numero === enCurso.pagina) && <HojaEnLectura corrida={corrida} numero={enCurso.pagina} mini={enCurso.mini} />}
              </div>
            )}
            {d.estado === "fallo" && d.error && <p className="adj-doc-error">{d.error}</p>}
          </section>
        );
      })}
      {descartados.length > 0 && (
        <section className="adj-descartados">
          <button className="fila" onClick={() => setVerDescartados(!verDescartados)}>
            <Icono n={verDescartados ? "abajo" : "derecha"} tam={12} />
            <span className="crece">{descartados.length} adjunto{descartados.length === 1 ? "" : "s"} que no se leyeron</span>
          </button>
          {verDescartados && (
            <ul>
              {descartados.map((x, i) => (
                <li key={i}><b>{x.nombre}</b><span>{x.detalle}</span></li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

// ------------------------------------------------------------ visor de hoja

function describirPaso(p: PasoHoja): { icono: NombreIcono; titulo: string; detalle: string; tono: string } | null {
  switch (p.paso) {
    case "texto_pdf":
      return { icono: "documento", titulo: "Texto propio del PDF", detalle: p.tablas ? `${p.tablas} tabla${p.tablas === 1 ? "" : "s"}` : "sin OCR", tono: "azul" };
    case "tesseract":
      return { icono: "escaner", titulo: p.dpi ? `Tesseract a ${p.dpi} ppp` : "Tesseract", detalle: `${pct(p.confianza)} · ${p.caracteres ?? 0} caracteres · ${segundos(p.ms ?? 0)}`, tono: (p.confianza ?? 0) >= 0.6 ? "turquesa" : "ambar" };
    case "sin_preparar":
      return { icono: "escaner", titulo: p.dpi && p.dpi < 300 ? `Relectura a ${p.dpi} ppp` : "Relectura sin preparar", detalle: `${pct(p.confianza)} · ${segundos(p.ms ?? 0)}`, tono: "turquesa" };
    case "giro":
      return { icono: "deshacer", titulo: p.grados ? `Puede estar girada ${p.grados}°` : "Busca si está de lado", detalle: p.grados ? `la lectura quedó en ${pct(p.confianza_antes)}` : `con ${pct(p.confianza_antes)} ningún giro mejora`, tono: "ambar" };
    case "girada":
      return { icono: "deshacer", titulo: `Releída girada ${p.grados}°`, detalle: `${pct(p.confianza)} · ${p.usada ? "se queda" : "no mejora, se descarta"}`, tono: p.usada ? "verde" : "gris" };
    case "vision":
      return { icono: "ojo", titulo: "La transcribe Gemini con visión", detalle: p.ok ? `${p.caracteres ?? 0} caracteres · ${segundos(p.ms ?? 0)}` : p.error || "no encontró texto", tono: "violeta" };
    default:
      return null;
  }
}

function Recorrido({ h, d }: { h: Hoja; d: Documento }) {
  const pasos = h.pasos.filter((p) => p.paso !== "sondeo");
  const sondeos = h.pasos.filter((p) => p.paso === "sondeo").length;
  const items = pasos.map(describirPaso).filter(Boolean) as NonNullable<ReturnType<typeof describirPaso>>[];
  if (!items.length && !d.esCuerpo) items.push({ icono: d.tipo.includes("imagen") ? "ojo" : "documento", titulo: d.tipo.includes("imagen") ? "Foto" : "Lectura directa", detalle: d.tipo, tono: "azul" });
  const tipo = h.tipo ? nombreTipoHoja(h.tipo) : "";
  return (
    <ol className="recorrido">
      {items.map((x, i) => (
        <li key={i} className={`tono-${x.tono}`}>
          <span className="recorrido-icono"><Icono n={x.icono} tam={13} /></span>
          <span className="recorrido-cuerpo"><b>{x.titulo}</b><span>{x.detalle}{x.icono === "deshacer" && sondeos && i === items.findIndex((y) => y.icono === "deshacer") ? ` · ${sondeos} sondeos` : ""}</span></span>
        </li>
      ))}
      {(tipo || d.id) && (
        <li className="tono-azul indice">
          <span className="recorrido-icono"><Icono n="capas" tam={13} /></span>
          <span className="recorrido-cuerpo"><b>Índice: {tipo || "hoja de texto"}</b><span>{h.ids ? `${h.ids} documento${h.ids === 1 ? "" : "s"} de identidad${h.marcas?.length ? ` (${h.marcas.join(", ")})` : ""}` : `${(h.caracteres ?? 0).toLocaleString("es-CO")} caracteres`}</span></span>
        </li>
      )}
    </ol>
  );
}

type Tramo = { a: number; b: number; clase: string; titulo: string };

function segmentar(texto: string, tramos: Tramo[]): { s: string; clases: string[]; titulos: string[] }[] {
  const cortes = new Set<number>([0, texto.length]);
  const validos = tramos.filter((t) => t.b > t.a && t.a < texto.length).map((t) => ({ ...t, b: Math.min(t.b, texto.length) }));
  for (const t of validos) cortes.add(t.a).add(t.b);
  const orden = [...cortes].sort((x, y) => x - y);
  const salida: { s: string; clases: string[]; titulos: string[] }[] = [];
  for (let i = 0; i < orden.length - 1; i++) {
    const a = orden[i]!;
    const b = orden[i + 1]!;
    const sobre = validos.filter((t) => t.a <= a && t.b >= b);
    salida.push({ s: texto.slice(a, b), clases: [...new Set(sobre.map((t) => t.clase))], titulos: [...new Set(sobre.map((t) => t.titulo))] });
  }
  return salida;
}

function TextoDeHoja({ texto, tramos, marcaN }: { texto: string; tramos: Tramo[]; marcaN: number }) {
  const caja = useRef<HTMLPreElement>(null);
  const segmentos = useMemo(() => segmentar(texto, tramos), [texto, tramos]);
  useEffect(() => {
    const el = caja.current?.querySelector(".m-sel");
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [marcaN, texto]);
  if (!texto.trim()) return <div className="vacio chico">Esta hoja no dio texto.</div>;
  return (
    <pre ref={caja} className="hoja-texto">
      {segmentos.map((g, i) => (g.clases.length ? <mark key={i} className={g.clases.join(" ")} title={g.titulos.join(" · ")}>{g.s}</mark> : <span key={i}>{g.s}</span>))}
    </pre>
  );
}

function VisorHoja({ corrida, d, numero, enCurso, imagenEnCurso, marcas, mover, recorridos }: { corrida: string; d: Documento; numero: number; enCurso: boolean; imagenEnCurso?: string; marcas: Tramo[]; mover: (n: number) => void; recorridos: RecorridoCampo[] }) {
  const h = d.hojas.find((x) => x.numero === numero);
  const [texto, setTexto] = useState<TextoDocumento | null>(null);
  const [zoom, setZoom] = useState(false);
  const cuerpo = useRef<HTMLDivElement>(null);
  const [imagenPct, setImagenPct] = useState(() => leerLocal(`${CLAVE_MEDIDAS}:imagen`, MEDIDAS.imagen));
  useEffect(() => {
    let vivo = true;
    setTexto(null);
    if (d.archivo) void textoDe(corrida, d.archivo).then((t) => vivo && setTexto(t));
    return () => {
      vivo = false;
    };
  }, [corrida, d.archivo]);
  useEffect(() => {
    cuerpo.current?.style.setProperty("--imagen", `${imagenPct}%`);
  }, [imagenPct]);
  const textoHoja = texto?.hojas.find((x) => x.numero === numero)?.texto ?? (texto?.hojas.length === 1 ? texto.hojas[0]!.texto : "");
  const total = d.paginas || d.hojas.length;
  const tieneImagen = Boolean(h?.img) || (enCurso && !d.esCuerpo);
  const campos = recorridos.filter((r) => r.ubicacion && r.ubicacion.id === d.id && (r.ubicacion.pagina === numero || d.sinHojas));
  return (
    <div className="adj-visor">
      <header className="adj-visor-cabeza">
        <div className="crece">
          <b className="adj-visor-titulo">{d.nombre}</b>
          <span className="adj-visor-sub">
            {d.esCuerpo ? "Texto del correo" : `Hoja ${numero}${total ? ` de ${total}` : ""}`}
            {h && !d.esCuerpo ? ` · ${NOMBRE_METODO[h.metodo]}` : ""}
            {h?.confianza ? ` · confianza ${pct(h.confianza)}` : ""}
            {h?.ms ? ` · ${segundos(h.ms)}` : ""}
          </span>
        </div>
        {campos.length > 0 && <span className="adj-visor-campos">{campos.map((c) => <span key={c.campo} className="ficha tono-verde">{c.nombre}</span>)}</span>}
        {!d.esCuerpo && total > 1 && (
          <span className="adj-visor-nav">
            <button className="boton-mini" disabled={numero <= 1} onClick={() => mover(numero - 1)} aria-label="Hoja anterior"><Icono n="derecha" tam={14} style={{ transform: "scaleX(-1)" }} /></button>
            <button className="boton-mini" disabled={numero >= total} onClick={() => mover(numero + 1)} aria-label="Hoja siguiente"><Icono n="derecha" tam={14} /></button>
          </span>
        )}
      </header>
      {h && <Recorrido h={h} d={d} />}
      {h?.dudosa && <p className="adj-visor-aviso">Lectura dudosa: la confianza del OCR quedó por debajo del umbral; el motor marca la hoja para verificar sus cifras.</p>}
      {h?.motivo && h.metodo === "ilegible" && <p className="adj-visor-aviso error">{h.motivo}</p>}
      <div ref={cuerpo} className={`adj-visor-cuerpo ${tieneImagen ? "" : "sin-imagen"}`}>
        {tieneImagen && (
          <>
            <div className={`adj-visor-imagen ${zoom ? "zoom" : ""}`}>
              {h?.img ? (
                <motion.img key={h.img} src={urlArchivo(corrida, h.img)} alt={`Hoja ${numero}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.25 }} onClick={() => setZoom(!zoom)} draggable={false} />
              ) : (
                imagenEnCurso ? (
                  <div className="papel-con-imagen">
                    <img src={urlArchivo(corrida, imagenEnCurso)} alt={`Hoja ${numero}`} draggable={false} />
                    <span className="escaneo" />
                    <span className="papel-estado"><span className="girando" />Tesseract está leyendo esta hoja…</span>
                  </div>
                ) : (
                  <div className="papel-leyendo"><span className="escaneo" /><span>Leyendo la hoja {numero}…</span></div>
                )
              )}
            </div>
            <Divisor contenedor={cuerpo} variable="--imagen" min={20} max={() => 80} calcular={(e, caja) => ((e.clientX - caja.left) / caja.width) * 100} alSoltar={(v) => { setImagenPct(v); guardarLocal(`${CLAVE_MEDIDAS}:imagen`, v); }} />
          </>
        )}
        <div className="adj-visor-texto desplazable">
          {texto === null && d.archivo ? <div className="vacio chico"><span className="girando" />Cargando el texto…</div> : !d.archivo ? <div className="vacio chico">{enCurso ? "El texto aparece cuando termine de leerse el adjunto." : "Este adjunto no produjo texto."}</div> : <TextoDeHoja texto={textoHoja} tramos={marcas} marcaN={marcas.length} />}
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------- campos

const NOMBRE_FUENTE: Record<string, string> = { algoritmo: "Algoritmo", ia: "IA", registro: "Registro", regla: "Regla" };

function TarjetaCampo({ r, ir }: { r: RecorridoCampo; ir: (u: Resultado, etiqueta: string) => void }) {
  const [abierta, setAbierta] = useState(r.estado !== "enviado" || r.pasos.some((p) => p.tipo === "devuelto" || (p.tipo === "revisor" && p.tono !== "ok")));
  const estado = { enviado: ["verde", "Se envía"], no_enviado: ["gris", "Sin valor"], rechazado: ["rojo", "Rechazado"], pendiente: ["azul", "En curso"] }[r.estado];
  return (
    <article className={`campo-tarjeta e-${r.estado}`}>
      <button className="campo-tarjeta-cabeza" onClick={() => setAbierta(!abierta)}>
        <span className="crece">
          <span className="campo-tarjeta-nombre">{r.nombre}{r.obligatorio && <i title="Obligatorio para radicar">*</i>}</span>
          <span className="campo-tarjeta-valor">{r.valor !== undefined && r.valor !== null && r.valor !== "" ? String(r.valor) : "—"}</span>
        </span>
        <span className="campo-tarjeta-meta">
          <span className={`ficha tono-${estado[0]}`}>{estado[1]}</span>
          {r.fuente && <span className="ficha">{NOMBRE_FUENTE[r.fuente] ?? r.fuente}</span>}
        </span>
        <Icono n={abierta ? "abajo" : "derecha"} tam={12} className="texto-3" />
      </button>
      {r.ubicacion && (
        <button className="campo-origen" onClick={() => ir(r.ubicacion!, r.nombre)}>
          <Icono n="mira" tam={12} />
          <span>{r.ubicacion.nombre}{r.ubicacion.pagina && !r.ubicacion.id.startsWith("correo") ? ` · hoja ${r.ubicacion.pagina}` : ""}</span>
        </button>
      )}
      {abierta && (
        <ol className="campo-pasos">
          {r.pasos.map((p, i) => (
            <li key={i} className={`p-${p.tono}`}>
              <span className="campo-paso-punto" />
              <div className="crece">
                <span className="campo-paso-titulo">{p.titulo}</span>
                {p.valor !== undefined && p.valor !== null && p.valor !== "" && !p.titulo.includes("«") && <span className="campo-paso-valor">{String(p.valor)}{p.confianza !== undefined && !Number.isNaN(p.confianza) ? <em>{pct(p.confianza)}</em> : null}</span>}
                {p.detalle && <span className="campo-paso-detalle">{p.detalle}</span>}
                {p.ubicacion && <button className="enlace-hoja" onClick={() => ir(p.ubicacion!, r.nombre)}>ver en {p.ubicacion.nombre}{p.ubicacion.pagina ? `, hoja ${p.ubicacion.pagina}` : ""}</button>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}

// ----------------------------------------------------------- búsquedas

const QUIEN: Record<string, [string, string]> = { agente: ["IA", "violeta"], revisor: ["Revisor", "rosa"], algoritmo: ["Algoritmo", "azul"], motor: ["Motor", "gris"] };
const MODO: Record<string, string> = { buscar: "Busca el texto", consultar: "Pregunta al índice", leer: "Lee", inventario: "Revisa el índice de hojas", campo: "Busca" };

function FilaBusqueda({ b, ir, t0 }: { b: Busqueda; ir: (r: Resultado, etiqueta: string) => void; t0: number }) {
  const [quien, tono] = QUIEN[b.quien]!;
  const titulo = b.modo === "campo" ? `${MODO.campo} ${b.consulta}` : b.modo === "inventario" ? MODO.inventario : b.modo === "leer" ? `${MODO.leer} ${b.consulta}` : `${MODO[b.modo]} «${b.consulta}»`;
  return (
    <li className={`busqueda q-${b.quien}`}>
      <div className="busqueda-cabeza">
        <span className={`ficha tono-${tono}`}>{quien}</span>
        <span className="crece busqueda-titulo">{titulo}</span>
        <span className="texto-3 diminuto mono">+{Math.max(0, b.t - t0).toFixed(0)}s</span>
      </div>
      {b.modo === "campo" && <span className="busqueda-nota">{b.encontrado ? <>Encontró <b>{String(b.valor)}</b></> : "No lo encontró: queda para la IA"}</span>}
      {b.terminos && b.terminos.length > 0 && <span className="busqueda-nota">Términos: {b.terminos.join(", ")}</span>}
      {b.quien === "motor" && <span className="busqueda-nota">La hace el propio motor, no la IA.</span>}
      {b.modo !== "campo" && b.modo !== "leer" && b.modo !== "inventario" && <span className="busqueda-nota">{b.total ? `${b.total} resultado${b.total === 1 ? "" : "s"}` : "Sin resultados"}{b.ms ? ` · ${b.ms} ms` : ""}</span>}
      {b.resultados.length > 0 && (
        <ul className="busqueda-resultados">
          {b.resultados.slice(0, 6).map((r, i) => (
            <li key={i}>
              <button onClick={() => ir(r, titulo)} disabled={b.modo === "inventario"}>
                <span className="resultado-donde">{r.nombre}{r.pagina ? ` · hoja ${r.pagina}` : b.modo === "buscar" && !r.spans?.length ? " · en el nombre del archivo" : ""}{r.puntaje ? ` · puntaje ${r.puntaje}` : ""}</span>
                {r.fragmento && <span className="resultado-fragmento">{r.fragmento}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

// ---------------------------------------------------------------- vista

export function VistaAdjuntos() {
  const catalogo = usar((s) => s.catalogo);
  const corrida = usar((s) => (s.corridaVisible ? s.corridas[s.corridaVisible] : undefined));
  const orden = usar((s) => s.orden);
  const corridas = usar((s) => s.corridas);
  const verCorrida = usar((s) => s.verCorrida);
  const bandeja = usar((s) => s.bandeja);
  const irA = usar((s) => s.irA);
  const lect = useMemo(() => derivarLectura(corrida), [corrida]);
  const lista = useMemo(() => derivarBusquedas(corrida), [corrida]);
  const campos = useMemo(() => derivarRecorridos(corrida), [corrida]);
  const dic = useMemo(() => derivarDictamen(corrida), [corrida]);
  const [sel, setSel] = useState<Seleccion | null>(null);
  const [marca, setMarca] = useState<Marca | null>(null);
  const [panel, setPanel] = useState<"campos" | "busquedas">("campos");
  const [seguir, setSeguir] = useState(true);
  const espacio = useRef<HTMLDivElement>(null);
  const [medidas, setMedidas] = useState(() => ({ ...MEDIDAS, ...leerLocal<Partial<typeof MEDIDAS>>(CLAVE_MEDIDAS, {}) }));

  useEffect(() => {
    espacio.current?.style.setProperty("--docs", `${medidas.docs}px`);
    espacio.current?.style.setProperty("--lado", `${medidas.lado}px`);
  }, [medidas]);

  // Corrida nueva: se arranca por el primer adjunto con hojas.
  useEffect(() => {
    setSel(null);
    setMarca(null);
    setSeguir(true);
  }, [corrida?.id]);

  // En vivo, el visor acompaña a la hoja que el motor está leyendo.
  const ultima = useMemo(() => {
    for (let i = (corrida?.eventos.length ?? 0) - 1; i >= 0; i--) {
      const e = corrida!.eventos[i]!;
      if (e.tipo === "hoja") return { clave: String(e.adjunto), pagina: Number(e.pagina) };
    }
    return null;
  }, [corrida]);
  useEffect(() => {
    if (!seguir || corrida?.resultado) return;
    const objetivo = lect.enCurso ?? ultima;
    if (objetivo) setSel(objetivo);
  }, [lect.enCurso, ultima, seguir, corrida?.resultado]);

  const elegido = sel ?? (lect.documentos.find((d) => !d.esCuerpo && d.hojas.length) ? { clave: lect.documentos.find((d) => !d.esCuerpo && d.hojas.length)!.clave, pagina: lect.documentos.find((d) => !d.esCuerpo && d.hojas.length)!.hojas[0]!.numero } : lect.documentos[0] ? { clave: lect.documentos[0].clave, pagina: 1 } : null);
  const doc = elegido ? lect.documentos.find((d) => d.clave === elegido.clave) : undefined;

  const elegir = (s: Seleccion) => {
    setSeguir(false);
    setSel(s);
    setMarca(null);
  };

  const ir = (r: Resultado, etiqueta: string) => {
    const clave = lect.porId[r.id] ?? r.id;
    setSeguir(false);
    setSel({ clave, pagina: r.pagina ?? 1 });
    setMarca((m) => ({ id: r.id, pagina: r.pagina, spans: r.spans ?? [], tramo: r.tramo ?? null, etiqueta, n: (m?.n ?? 0) + 1 }));
  };

  // Lo que se resalta en el texto de la hoja: lo elegido, lo que encontraron las búsquedas y dónde quedó cada campo.
  const marcas: Tramo[] = useMemo(() => {
    if (!doc?.id || !elegido) return [];
    const enEsta = (r: { id: string; pagina: number | null }) => r.id === doc.id && (doc.sinHojas || doc.esCuerpo || r.pagina === elegido.pagina || r.pagina === null);
    const t: Tramo[] = [];
    for (const b of lista) {
      if (b.modo === "leer" || b.modo === "inventario") continue;
      for (const r of b.resultados) {
        if (!enEsta(r)) continue;
        if (r.tramo) t.push({ a: r.tramo[0], b: r.tramo[1], clase: "m-tramo", titulo: `Tramo devuelto a «${b.consulta}»` });
        for (const [a, z] of r.spans ?? []) t.push({ a, b: z, clase: b.quien === "algoritmo" ? "m-algoritmo" : b.modo === "consultar" ? "m-termino" : "m-busqueda", titulo: b.modo === "campo" ? `Algoritmo: ${b.consulta}` : `${QUIEN[b.quien]![0]}: «${b.consulta}»` });
      }
    }
    for (const c of campos) {
      if (!c.ubicacion || !enEsta(c.ubicacion)) continue;
      for (const [a, z] of c.ubicacion.spans) t.push({ a, b: z, clase: "m-campo", titulo: `${c.nombre} (se envía)` });
    }
    if (marca && enEsta(marca)) {
      if (marca.tramo) t.push({ a: marca.tramo[0], b: marca.tramo[1], clase: "m-sel-tramo", titulo: marca.etiqueta });
      for (const [a, z] of marca.spans) t.push({ a, b: z, clase: "m-sel", titulo: marca.etiqueta });
    }
    return t;
  }, [doc, elegido, lista, campos, marca]);

  const fijar = (k: keyof typeof MEDIDAS, v: number) => setMedidas((m) => {
    const s = { ...m, [k]: v };
    guardarLocal(CLAVE_MEDIDAS, s);
    return s;
  });

  const t0 = corrida?.eventos[0]?.t ?? 0;
  const hojas = lect.documentos.reduce((n, d) => n + (d.esCuerpo ? 0 : d.hojas.length), 0);
  const conOcr = lect.documentos.reduce((n, d) => n + d.hojas.filter((h) => h.metodo === "ocr" || h.metodo === "vision").length, 0);
  const consultasIa = lista.filter((b) => b.quien !== "algoritmo" && b.modo !== "inventario").length;

  if (!catalogo) return <div className="vacio">Cargando…</div>;

  return (
    <div className="adj">
      <header className="adj-barra">
        <div className="adj-correo crece">
          <span className="adj-correo-icono"><Icono n="correo" tam={16} /></span>
          <div className="crece">
            <b>{corrida ? corrida.caso : "Ningún correo elegido"}</b>
            <span className="texto-3 diminuto">{corrida ? String(corrida.eventos.find((e) => e.tipo === "inicio")?.asunto ?? "") || "(sin asunto)" : "Corre un correo en el flujo o elige uno ya corrido"}</span>
          </div>
        </div>
        {corrida && (
          <div className="adj-cifras">
            <span><b>{lect.documentos.filter((d) => !d.esCuerpo).length}</b> adjuntos</span>
            <span><b>{hojas}</b> hojas</span>
            <span><b>{conOcr}</b> leídas por OCR o visión</span>
            <span><b>{consultasIa}</b> consultas de la IA</span>
          </div>
        )}
        <Selector
          valor={corrida?.id ?? null}
          placeholder="Elegir un correo corrido…"
          opciones={orden.slice(0, 120).flatMap((id) => {
            const c = corridas[id];
            if (!c) return [];
            return [{ valor: id, etiqueta: c.caso, texto: c.caso, detalle: c.resultado ? (NOMBRES_DESENLACE[String(c.resultado.accion)] ?? String(c.resultado.accion)) : c.estado === "en_cola" ? "En espera" : "Corriendo" }];
          })}
          alCambiar={(v) => verCorrida(v)}
          etiqueta="Correo corrido a la vista"
          tamano="chico"
          ancho={260}
        />
        {corrida && <button className={`boton chico ${seguir && !corrida.resultado ? "activo" : ""}`} aria-pressed={seguir} onClick={() => setSeguir(!seguir)} title="El visor acompaña la hoja que se está leyendo"><span className={`punto ${seguir ? "latiendo" : ""}`} />En vivo</button>}
        {corrida && <button className="boton chico" onClick={() => irA("flujo")}><Icono n="flujo" tam={13} />Ver en el flujo</button>}
      </header>
      {!corrida ? (
        <div className="estado-vacio">
          <span className="estado-vacio-icono"><Icono n="documento" tam={22} /></span>
          <h2>Elige un correo corrido para ver sus adjuntos</h2>
          <p>Verás cada hoja con su imagen, cómo se leyó (texto del PDF, OCR o visión de la IA), qué buscó la IA y de qué hoja salió cada dato de la solicitud.</p>
          {orden.length > 0 ? (
            <div className="elegir-corrida">
              <small>Últimos correos corridos</small>
              {orden.slice(0, 5).map((id) => {
                const c = corridas[id];
                if (!c) return null;
                const a = c.resultado ? String(c.resultado.accion) : "";
                return (
                  <button key={id} onClick={() => verCorrida(id)}>
                    <span className="ficha mono">{c.caso}</span>
                    <span className="crece">{String(c.resultado?.asunto ?? "") || String(c.eventos.find((e) => e.tipo === "inicio")?.asunto ?? "") || bandeja.find((b) => b.caso === c.caso)?.asunto || "(sin asunto)"}</span>
                    <span className="texto-3">{c.resultado ? (NOMBRES_DESENLACE[a] ?? a) : "corriendo"}</span>
                  </button>
                );
              })}
            </div>
          ) : (
            <button className="boton primario" onClick={() => irA("flujo")}><Icono n="flujo" tam={14} />Ir al flujo a correr un correo</button>
          )}
        </div>
      ) : (
        <div ref={espacio} className="adj-cuerpo">
          <ListaDocumentos corrida={corrida.id} documentos={lect.documentos} descartados={lect.descartados} sel={elegido} enCurso={lect.enCurso} elegir={elegir} />
          <Divisor contenedor={espacio} variable="--docs" min={220} max={() => 520} calcular={(e, caja) => e.clientX - caja.left} alSoltar={(v) => fijar("docs", v)} />
          <div className="adj-centro">
            {doc && elegido ? (
              <VisorHoja corrida={corrida.id} d={doc} numero={elegido.pagina} enCurso={lect.enCurso?.clave === doc.clave && lect.enCurso.pagina === elegido.pagina} imagenEnCurso={lect.enCurso?.clave === doc.clave && lect.enCurso.pagina === elegido.pagina ? lect.enCurso.img : undefined} marcas={marcas} mover={(n) => elegir({ clave: doc.clave, pagina: n })} recorridos={campos} />
            ) : (
              <div className="vacio grande"><span className="girando" /><p>{corrida.resultado ? "Este correo no llegó a abrir sus adjuntos." : "Esperando a que el motor abra los adjuntos…"}</p></div>
            )}
          </div>
          <Divisor contenedor={espacio} variable="--lado" min={300} max={() => 640} calcular={(e, caja) => caja.right - e.clientX} alSoltar={(v) => fijar("lado", v)} />
          <aside className="adj-lado">
            {dic ? (
              <div className={`dictamen tono-${dic.tono}`}>
                <Icono n={dic.accion === "radicado" ? "ok" : "bandera"} tam={18} />
                <div className="crece">
                  <b>{dic.titulo}</b>
                  {dic.motivo && <span title={dic.motivo}>{motivoLegible(dic.motivo)}</span>}
                  {dic.explicacion && <span className="dictamen-explicacion">{dic.explicacion}</span>}
                </div>
                <span className={`pastilla ${TONO_DESENLACE[dic.accion] ?? ""} diminuto`}>{String(corrida.resultado?.segundos ?? "")} s</span>
              </div>
            ) : (
              <div className="dictamen tono-azul"><span className="girando" /><div className="crece"><b>Corriendo</b><span>El resultado aparece cuando el motor termine.</span></div></div>
            )}
            <nav className="pestanas adj-pestanas" role="tablist">
              <button role="tab" aria-selected={panel === "campos"} className={panel === "campos" ? "activa" : ""} onClick={() => setPanel("campos")}><Icono n="escudo" tam={13} />Campos<span className="contador">{campos.length}</span>{panel === "campos" && <motion.i layoutId="pestana-adj" className="pestana-linea" />}</button>
              <button role="tab" aria-selected={panel === "busquedas"} className={panel === "busquedas" ? "activa" : ""} onClick={() => setPanel("busquedas")}><Icono n="buscar" tam={13} />Búsquedas<span className="contador">{lista.length}</span>{panel === "busquedas" && <motion.i layoutId="pestana-adj" className="pestana-linea" />}</button>
            </nav>
            <div className="adj-lado-cuerpo desplazable">
              {panel === "campos" ? (
                campos.length ? campos.map((r) => <TarjetaCampo key={r.campo} r={r} ir={ir} />) : <div className="vacio chico">Los campos aparecen cuando el algoritmo y la IA empiezan a buscarlos.</div>
              ) : lista.length ? (
                <ol className="busquedas">{lista.map((b) => <FilaBusqueda key={b.n} b={b} ir={ir} t0={t0} />)}</ol>
              ) : (
                <div className="vacio chico">Todavía no hay búsquedas en el índice.</div>
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
