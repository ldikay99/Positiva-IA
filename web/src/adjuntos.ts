/**
 * Lo que el motor hizo con cada adjunto y con cada campo, a partir de los
 * eventos reales de la corrida: hojas leídas, búsquedas en el índice y el
 * recorrido de cada dato hasta el dictamen.
 */
import { CAMPOS, OBLIGATORIOS, nombreCampo } from "./flujo.ts";
import type { Corrida, Evento } from "./tipos.ts";

export type Metodo = "texto" | "ocr" | "vision" | "ilegible";

export interface PasoHoja {
  paso: "texto_pdf" | "tesseract" | "sin_preparar" | "giro" | "sondeo" | "girada" | "vision";
  dpi?: number;
  confianza?: number;
  caracteres?: number;
  ms?: number;
  grados?: number;
  usada?: boolean;
  ok?: boolean;
  tablas?: number;
  pixeles?: string;
  confianza_antes?: number;
  error?: string;
}

export interface Hoja {
  numero: number;
  metodo: Metodo;
  confianza?: number | null;
  ms: number;
  girada: number;
  relectura200: boolean;
  vision: boolean;
  visionIntentada: boolean;
  dudosa: boolean;
  motivo: string;
  pasos: PasoHoja[];
  img?: string;
  mini?: string;
  ancho?: number;
  alto?: number;
  imagenesIncrustadas?: { leidas: number; sinLeer: number };
  /** Clasificación del índice: «cedula», «historia_clinica»… */
  tipo?: string;
  ids?: number;
  marcas?: string[];
  caracteres?: number;
}

export interface Documento {
  /** Clave del visor: a1, a2… para adjuntos; «correo» o «hilo» para el cuerpo. */
  clave: string;
  nombre: string;
  tipo: string;
  esCuerpo: boolean;
  estado: "leyendo" | "listo" | "fallo";
  paginas: number;
  hojas: Hoja[];
  /** Id en el espacio de trabajo del motor (lo que la IA usa para buscar). */
  id?: string;
  archivo?: string;
  sinHojas?: boolean;
  error?: string;
  aviso?: string;
  ms?: number;
  bytes?: number;
  tiposHoja: Record<number, { tipo: string; ids: number; marcas: string[]; caracteres: number }>;
  /** La hoja que el OCR está leyendo ahora, con su imagen ya renderizada. */
  leyendo?: { pagina: number; img?: string; mini?: string };
}

export interface Descartado {
  nombre: string;
  detalle: string;
  estado: string;
}

export interface Lectura {
  documentos: Documento[];
  descartados: Descartado[];
  /** La hoja que se está leyendo en este momento, si la corrida sigue. */
  enCurso: { clave: string; pagina: number; img?: string; mini?: string } | null;
  porId: Record<string, string>;
}

const ES_CUERPO = new Set(["correo", "hilo"]);

export function lectura(corrida: Corrida | undefined): Lectura {
  const docs = new Map<string, Documento>();
  const porId: Record<string, string> = {};
  const descartados: Descartado[] = [];
  let enCurso: Lectura["enCurso"] = null;
  if (!corrida) return { documentos: [], descartados, enCurso, porId };
  const terminada = Boolean(corrida.resultado);
  for (const e of corrida.eventos) {
    switch (e.tipo) {
      case "adjunto_inicio": {
        const clave = String(e.adjunto);
        docs.set(clave, { clave, nombre: String(e.nombre), tipo: String(e.tipo_archivo || tipoDe(e)), esCuerpo: false, estado: "leyendo", paginas: Number(e.paginas ?? 0), hojas: [], bytes: Number(e.bytes ?? 0), tiposHoja: {} });
        break;
      }
      case "hoja": {
        const d = docs.get(String(e.adjunto));
        if (!d) break;
        const h: Hoja = {
          numero: Number(e.pagina),
          metodo: (String(e.metodo) as Metodo) || "texto",
          confianza: e.confianza === undefined ? undefined : (e.confianza as number | null),
          ms: Number(e.ms ?? 0),
          girada: Number(e.girada ?? 0),
          relectura200: Boolean(e.relectura_200),
          vision: Boolean(e.vision),
          visionIntentada: Boolean(e.vision_intentada),
          dudosa: Boolean(e.dudosa),
          motivo: String(e.motivo ?? ""),
          pasos: (e.pasos as PasoHoja[]) ?? [],
          img: e.img as string | undefined,
          mini: e.mini as string | undefined,
          ancho: e.ancho as number | undefined,
          alto: e.alto as number | undefined,
          caracteres: e.caracteres as number | undefined,
        };
        d.hojas = [...d.hojas.filter((x) => x.numero !== h.numero), h].sort((a, b) => a.numero - b.numero);
        if (!d.paginas) d.paginas = Number(e.total ?? 0);
        break;
      }
      case "hoja_inicio": {
        const d = docs.get(String(e.adjunto));
        if (d) d.leyendo = { pagina: Number(e.pagina), img: e.img as string | undefined, mini: e.mini as string | undefined };
        break;
      }
      case "hoja_imagenes": {
        const d = docs.get(String(e.adjunto));
        const h = d?.hojas.find((x) => x.numero === Number(e.pagina));
        if (h) h.imagenesIncrustadas = { leidas: Number(e.leidas ?? 0), sinLeer: Number(e.sin_leer ?? 0) };
        break;
      }
      case "adjunto_fin": {
        const d = docs.get(String(e.adjunto));
        if (!d) break;
        d.estado = e.error ? "fallo" : "listo";
        d.error = String(e.error ?? "") || undefined;
        d.aviso = String(e.aviso ?? "") || undefined;
        d.ms = Number(e.ms ?? 0);
        d.paginas = Number(e.paginas ?? d.paginas);
        if (e.tipo_archivo) d.tipo = String(e.tipo_archivo);
        break;
      }
      case "documento_listo": {
        const id = String(e.id);
        const clave = String(e.adjunto || "") || id;
        porId[id] = clave;
        let d = docs.get(clave);
        if (!d) {
          d = { clave, nombre: String(e.nombre || id), tipo: "texto", esCuerpo: ES_CUERPO.has(id), estado: "listo", paginas: 0, hojas: [], tiposHoja: {} };
          docs.set(clave, d);
        }
        d.id = id;
        d.archivo = String(e.archivo);
        d.sinHojas = Boolean(e.sin_hojas);
        for (const h of (e.hojas as { numero: number; tipo: string; ids: number; marcas: string[]; caracteres: number }[]) ?? []) d.tiposHoja[h.numero] = h;
        break;
      }
      case "manifiesto": {
        const estado = String(e.estado);
        if (estado !== "ok" && !e.id) descartados.push({ nombre: String(e.nombre), detalle: String(e.detalle ?? ""), estado });
        break;
      }
    }
  }
  const documentos = [...docs.values()].sort((a, b) => Number(b.esCuerpo) - Number(a.esCuerpo) || (a.clave === "correo" ? -1 : 0));
  // Las hojas de un cuerpo de correo o de un Word no tienen imagen: una sola hoja de texto.
  for (const d of documentos) {
    for (const h of d.hojas) Object.assign(h, d.tiposHoja[h.numero] ?? {});
    if (!d.hojas.length && d.id) {
      const numeros = Object.keys(d.tiposHoja).map(Number);
      d.hojas = (numeros.length ? numeros : [1]).map((n) => ({ numero: n, metodo: "texto" as Metodo, ms: 0, girada: 0, relectura200: false, vision: false, visionIntentada: false, dudosa: false, motivo: "", pasos: [], ...d.tiposHoja[n] }));
    }
    if (d.estado === "leyendo" && !terminada) {
      const siguiente = (d.hojas.at(-1)?.numero ?? 0) + 1;
      const pagina = d.paginas ? Math.min(siguiente, d.paginas) : siguiente;
      const previa = d.leyendo?.pagina === pagina ? d.leyendo : undefined;
      enCurso = { clave: d.clave, pagina, img: previa?.img, mini: previa?.mini };
    }
  }
  return { documentos, descartados, enCurso, porId };
}

function tipoDe(e: Evento): string {
  // Respaldo: el tipo del archivo se deduce del nombre.
  const n = String(e.nombre ?? "").toLowerCase();
  if (/\.(jpe?g|png|gif|bmp|tiff?|webp|heic)$/.test(n)) return "imagen";
  if (n.endsWith(".pdf")) return "pdf";
  if (/\.docx?$/.test(n)) return "docx";
  if (/\.xlsx?$/.test(n)) return "xlsx";
  if (n.endsWith(".p7m")) return "p7m";
  if (n.endsWith(".zip")) return "zip";
  return "otro";
}

// ------------------------------------------------------------- búsquedas

export interface Resultado {
  id: string;
  nombre: string;
  pagina: number | null;
  fragmento: string;
  spans: [number, number][];
  tramo?: [number, number] | null;
  puntaje?: number;
}

export type Quien = "agente" | "revisor" | "algoritmo" | "motor";

export interface Busqueda {
  n: number;
  t: number;
  quien: Quien;
  modo: "buscar" | "consultar" | "leer" | "inventario" | "campo";
  consulta: string;
  terminos?: string[];
  total: number;
  ms?: number;
  resultados: Resultado[];
  /** En las búsquedas del algoritmo, el campo y lo que encontró. */
  campo?: string;
  valor?: unknown;
  encontrado?: boolean;
  detalle?: string;
}

function ubic(u: unknown): Resultado | null {
  if (!u || typeof u !== "object") return null;
  const x = u as Record<string, unknown>;
  return { id: String(x.id), nombre: String(x.nombre ?? x.id), pagina: (x.pagina as number) ?? null, fragmento: "", spans: (x.spans as [number, number][]) ?? [] };
}

export function busquedas(corrida: Corrida | undefined): Busqueda[] {
  const salida: Busqueda[] = [];
  if (!corrida) return salida;
  let n = 0;
  for (const e of corrida.eventos) {
    if (e.tipo === "consulta") {
      salida.push({ n: n++, t: e.t, quien: e.quien === "revisor" ? "revisor" : e.quien === "motor" ? "motor" : "agente", modo: e.modo === "consultar" ? "consultar" : "buscar", consulta: String(e.consulta ?? ""), terminos: e.terminos as string[] | undefined, total: Number(e.total ?? 0), ms: Number(e.ms ?? 0), resultados: ((e.resultados as Resultado[]) ?? []).map((r) => ({ ...r, pagina: r.pagina ?? null })) });
    } else if (e.tipo === "lectura") {
      const paginas = (e.paginas as number[] | undefined) ?? (e.pagina ? [Number(e.pagina)] : []);
      salida.push({ n: n++, t: e.t, quien: e.quien === "revisor" ? "revisor" : "agente", modo: "leer", consulta: e.pagina ? `la hoja ${String(e.pagina)} de ${String(e.nombre ?? e.id)}` : Number(e.hasta) > 0 ? `las líneas ${String(e.desde ?? 0)} a ${String(e.hasta)} de ${String(e.nombre ?? e.id)}` : `${String(e.nombre ?? e.id)} completo`, total: paginas.length, resultados: (paginas.length ? paginas : [null]).map((p) => ({ id: String(e.id), nombre: String(e.nombre ?? e.id), pagina: p, fragmento: "", spans: [] })) });
    } else if (e.tipo === "inventario") {
      const docs = (e.documentos as { id: string; nombre: string; hojas: string[]; clase: string }[]) ?? [];
      salida.push({ n: n++, t: e.t, quien: e.quien === "revisor" ? "revisor" : "agente", modo: "inventario", consulta: "inventario de documentos", total: docs.length, resultados: docs.map((d) => ({ id: d.id, nombre: d.nombre, pagina: null, fragmento: d.hojas.length ? d.hojas.join(" · ") : d.clase || "", spans: [] })) });
    } else if (e.tipo === "busqueda" && e.campo !== "geografia") {
      const r = ubic(e.ubicacion);
      salida.push({ n: n++, t: e.t, quien: "algoritmo", modo: "campo", consulta: String(e.que ?? e.campo), campo: String(e.campo), valor: e.valor, encontrado: Boolean(e.encontrado), detalle: String(e.evidencia ?? e.detalle ?? e.motivo ?? ""), total: r ? 1 : 0, resultados: r ? [{ ...r, fragmento: String(e.evidencia ?? "") }] : [] });
    }
  }
  return salida;
}

// ------------------------------------------------------- recorrido de un campo

export type TipoPaso = "algoritmo" | "algoritmo_nada" | "ia" | "registro" | "regla" | "compuerta" | "compuerta_ok" | "devuelto" | "corregido" | "revisor" | "mantiene" | "final";

export interface PasoCampo {
  tipo: TipoPaso;
  t: number;
  titulo: string;
  detalle?: string;
  valor?: unknown;
  confianza?: number;
  ubicacion?: Resultado | null;
  tono: "ok" | "aviso" | "error" | "ia" | "neutro";
}

export interface RecorridoCampo {
  campo: string;
  nombre: string;
  obligatorio: boolean;
  pasos: PasoCampo[];
  valor?: unknown;
  ubicacion?: Resultado | null;
  estado: "enviado" | "no_enviado" | "rechazado" | "pendiente";
  fuente?: string;
}

const VEREDICTO: Record<string, string> = { OK: "lo da por bueno", CAMBIAR: "propone cambiarlo", "SIN RESPALDO": "no lo encuentra en los documentos" };

export function recorridos(corrida: Corrida | undefined): RecorridoCampo[] {
  const m = new Map<string, RecorridoCampo>();
  const de = (campo: string) => {
    let r = m.get(campo);
    if (!r) {
      r = { campo, nombre: nombreCampo(campo), obligatorio: OBLIGATORIOS.has(campo), pasos: [], estado: "pendiente" };
      m.set(campo, r);
    }
    return r;
  };
  if (!corrida) return [];
  let ultimaCompuerta: Evento | null = null;
  for (const e of corrida.eventos) {
    if (e.tipo === "busqueda") {
      const campo = String(e.campo);
      if (campo === "geografia") continue;
      if (e.encontrado) {
        const ok = e.aceptado !== false;
        de(campo).pasos.push({ tipo: "algoritmo", t: e.t, tono: ok ? "ok" : "aviso", valor: e.valor, confianza: Number(e.confianza), ubicacion: ubic(e.ubicacion), titulo: ok ? "El algoritmo lo encontró" : "El algoritmo lo encontró con poca confianza: lo decide la IA", detalle: String(e.evidencia ?? "") });
      } else {
        de(campo).pasos.push({ tipo: "algoritmo_nada", t: e.t, tono: "neutro", titulo: "El algoritmo no lo encontró: pasa a la IA", detalle: [e.motivo, e.detalle].filter(Boolean).map(String).join(" · ") });
      }
    } else if (e.tipo === "herramienta" && e.herramienta === "reportar_hallazgo" && e.ok) {
      const a = (e.argumentos ?? {}) as Record<string, unknown>;
      if (a.campo) de(String(a.campo)).pasos.push({ tipo: "ia", t: e.t, tono: "ia", valor: a.valor, confianza: a.confianza !== undefined ? Number(a.confianza) : undefined, ubicacion: ubic(e.ubicacion), titulo: "La IA lo reportó", detalle: String(a.evidencia ?? "") });
    } else if (e.tipo === "paso" && e.nodo === "registro_de_personas" && e.campos) {
      for (const [c, v] of Object.entries(e.campos as Record<string, unknown>)) de(c).pasos.push({ tipo: "registro", t: e.t, tono: "ok", valor: v, titulo: "Lo completó el registro de personas" });
    } else if (e.tipo === "regla" && e.cumple && e.accion === "fijar" && e.campo) {
      de(String(e.campo)).pasos.push({ tipo: "regla", t: e.t, tono: "ok", valor: e.valor, titulo: `Lo fijó la regla «${String(e.nombre)}»` });
    } else if (e.tipo === "paso" && e.nodo === "compuerta" && e.ronda !== undefined) {
      ultimaCompuerta = e;
      const reparos = (e.reparos as { campo: string; codigo: string; detalle: string; bloqueante: boolean }[]) ?? [];
      const conReparo = new Set<string>();
      for (const r of reparos) {
        if (!r.campo || !(r.campo in CAMPOS)) continue;
        conReparo.add(r.campo);
        de(r.campo).pasos.push({ tipo: "compuerta", t: e.t, tono: r.bloqueante ? "error" : "aviso", titulo: r.bloqueante ? `La compuerta lo rechazó (${String(e.contexto)})` : `La compuerta lo ajustó (${String(e.contexto)})`, detalle: `${r.codigo}: ${r.detalle}` });
      }
      for (const [c, v] of Object.entries((e.campos as Record<string, unknown>) ?? {})) {
        if (conReparo.has(c) || !(c in CAMPOS)) continue;
        const r = de(c);
        if (r.pasos.at(-1)?.tipo === "compuerta_ok") continue;
        r.pasos.push({ tipo: "compuerta_ok", t: e.t, tono: "ok", valor: v, titulo: `Pasó la compuerta (${String(e.contexto)})` });
      }
    } else if (e.tipo === "correccion" && e.fase === "inicio") {
      for (const [c, x] of Object.entries((e.campos as Record<string, { motivo: string; antes: unknown }>) ?? {})) de(c).pasos.push({ tipo: "devuelto", t: e.t, tono: "aviso", valor: x.antes, titulo: e.origen === "revisor" ? "Se devuelve a la IA por lo que objetó el revisor" : "Se devuelve a la IA para corregirlo", detalle: x.motivo });
    } else if (e.tipo === "correccion" && e.fase === "fin") {
      for (const [c, x] of Object.entries((e.campos as Record<string, { antes: unknown; despues: unknown; corregido: boolean }>) ?? {}))
        de(c).pasos.push({ tipo: "corregido", t: e.t, tono: x.corregido ? "ia" : x.despues == null || x.despues === "" ? "error" : "aviso", valor: x.despues, titulo: x.corregido ? `La IA lo corrigió: «${corto(x.antes)}» pasa a «${corto(x.despues)}»` : x.despues == null || x.despues === "" ? "La IA no lo pudo corregir: queda sin valor" : "La IA mantuvo el mismo valor" });
    } else if (e.tipo === "revision" && e.fase === "fin" && e.ok) {
      for (const [c, x] of Object.entries((e.veredictos as Record<string, { veredicto: string; valor: string; detalle: string }>) ?? {})) {
        const v = x.veredicto;
        de(c).pasos.push({ tipo: "revisor", t: e.t, tono: v === "OK" ? "ok" : "aviso", valor: x.valor || undefined, titulo: `El revisor ${VEREDICTO[v] ?? v.toLowerCase()}${x.valor ? `: «${x.valor}»` : ""}`, detalle: x.detalle });
      }
    } else if (e.tipo === "revision" && e.fase === "decision") {
      for (const aviso of (e.se_mantienen as string[]) ?? []) {
        const campo = Object.keys(CAMPOS).find((c) => aviso.includes(c));
        if (campo) de(campo).pasos.push({ tipo: "mantiene", t: e.t, tono: "ok", titulo: "Se mantiene pese al revisor", detalle: aviso });
      }
    } else if (e.tipo === "resultado") {
      const hallazgos = (e.hallazgos ?? {}) as Record<string, { valor: unknown; metodo: string; confianza: number }>;
      const ubicaciones = (e.ubicaciones ?? {}) as Record<string, unknown>;
      const atrib = (e.atribucion ?? {}) as Record<string, string>;
      const bloqueantes = new Set(((ultimaCompuerta?.reparos as { campo: string; bloqueante: boolean }[]) ?? []).filter((r) => r.bloqueante && ultimaCompuerta?.veredicto === "rechazado").map((r) => r.campo));
      for (const [c, h] of Object.entries(hallazgos)) {
        const r = de(c);
        r.valor = h.valor;
        r.ubicacion = ubic(ubicaciones[c]);
        r.fuente = atrib[c] ?? (String(h.metodo).startsWith("regla:") ? "regla" : "algoritmo");
        r.estado = bloqueantes.has(c) ? "rechazado" : "enviado";
      }
      for (const c of (e.sin_resolver as string[]) ?? []) {
        const r = de(c);
        if (r.estado === "pendiente") r.estado = "no_enviado";
      }
      for (const c of bloqueantes) de(c).estado = "rechazado";
      for (const r of m.values()) if (r.estado === "pendiente" && r.pasos.length) r.estado = "no_enviado";
    }
  }
  const orden = Object.keys(CAMPOS);
  return [...m.values()].filter((r) => r.campo in CAMPOS).sort((a, b) => orden.indexOf(a.campo) - orden.indexOf(b.campo));
}

function corto(v: unknown, n = 40): string {
  const s = v === null || v === undefined ? "" : String(v);
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

// ------------------------------------------------------------- dictamen

export interface Dictamen {
  accion: string;
  titulo: string;
  motivo: string;
  explicacion: string;
  tono: "verde" | "rojo" | "ambar" | "azul" | "gris";
}

const MOTIVOS: Record<string, string> = {
  FALTA_CAMPO_OBLIGATORIO: "falta un dato obligatorio",
  VOLUMEN_EXCESIVO: "el correo trae más de lo que se procesa",
  FACTURA_ELECTRONICA: "es una factura electrónica",
  DOCUMENTO_DE_OTRA_PERSONA: "el documento es de otra persona según el registro",
  VALIDACION_RECHAZO: "la validación rechazó un dato",
};

export function dictamen(corrida: Corrida | undefined): Dictamen | null {
  const r = corrida?.resultado;
  if (!r) return null;
  const accion = String(r.accion);
  const motivo = String(r.motivo ?? "");
  if (accion === "radicado") return { accion, titulo: "Se radica", motivo: `Radicado ${String(r.radicado ?? "")} (simulado: en modo prueba no se escribe en SGDEA)`, explicacion: "", tono: "verde" };
  if (accion === "manual") return { accion, titulo: "Va a revisión manual", motivo: MOTIVOS[motivo] ? `${motivo}: ${MOTIVOS[motivo]}` : motivo, explicacion: String(r.explicacion ?? ""), tono: "rojo" };
  if (accion === "reenviado") return { accion, titulo: "Se reenvía a otra área", motivo: String(r.area_destino ?? r.razon_del_reenvio ?? ""), explicacion: String(r.razon_del_reenvio ?? ""), tono: "azul" };
  if (accion === "duplicado") return { accion, titulo: "Es un duplicado", motivo: String(r.razon_del_duplicado ?? ""), explicacion: "", tono: "ambar" };
  return { accion, titulo: accion === "error" ? "La corrida falló" : accion, motivo, explicacion: "", tono: "gris" };
}

// ------------------------------------------------------ texto de las hojas

export interface TextoDocumento {
  id: string;
  nombre: string;
  hojas: { numero: number; texto: string; tipo?: string }[];
}

const cache = new Map<string, Promise<TextoDocumento | null>>();

export function urlArchivo(corrida: string, archivo: string): string {
  return `/api/corridas/${encodeURIComponent(corrida)}/archivos/${archivo}`;
}

export function textoDe(corrida: string, archivo: string): Promise<TextoDocumento | null> {
  const k = `${corrida}/${archivo}`;
  let p = cache.get(k);
  if (!p) {
    p = fetch(urlArchivo(corrida, archivo))
      .then((r) => (r.ok ? (r.json() as Promise<TextoDocumento>) : null))
      .catch(() => null);
    cache.set(k, p);
    void p.then((v) => v === null && cache.delete(k));
  }
  return p;
}

export const NOMBRE_TIPO_HOJA: Record<string, string> = {
  cedula: "Cédula",
  historia_clinica: "Historia clínica",
  formulario: "Formulario",
  incapacidad: "Incapacidad",
  carta: "Carta",
  dictamen: "Dictamen",
  furat: "Reporte de accidente",
  factura: "Factura",
  tabla: "Tabla",
  sin_texto: "Sin texto",
  otro: "Otro",
};

export function nombreTipoHoja(t?: string): string {
  if (!t) return "";
  return NOMBRE_TIPO_HOJA[t] ?? t.replace(/_/g, " ").replace(/^./, (x) => x.toUpperCase());
}
