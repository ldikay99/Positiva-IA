import type { NombreIcono } from "./iconos.tsx";
import type { Nodo, Regla } from "./tipos.ts";

/** Qué tipo de pieza es cada nodo: define su ícono, su tono y la etiqueta que lo distingue. */
export type Categoria = "entrada" | "filtro" | "ruta" | "documentos" | "ocr" | "busqueda" | "ia" | "datos" | "validacion" | "integracion" | "salida" | "final" | "regla";

export const CATEGORIAS: Record<Categoria, { nombre: string; icono: NombreIcono; tono: string }> = {
  entrada: { nombre: "Entrada", icono: "bandeja", tono: "pizarra" },
  filtro: { nombre: "Filtro", icono: "filtro", tono: "ambar" },
  ruta: { nombre: "Enrutamiento", icono: "ruta", tono: "azul" },
  documentos: { nombre: "Documentos", icono: "documento", tono: "turquesa" },
  ocr: { nombre: "OCR · Conversión", icono: "escaner", tono: "turquesa" },
  busqueda: { nombre: "Búsqueda por reglas", icono: "buscar", tono: "azul" },
  ia: { nombre: "IA · Gemini", icono: "chispa", tono: "violeta" },
  datos: { nombre: "Datos", icono: "base", tono: "turquesa" },
  validacion: { nombre: "Validación", icono: "escudo", tono: "verde" },
  integracion: { nombre: "Integración SGDEA", icono: "enviar", tono: "azul" },
  salida: { nombre: "Salida", icono: "bandera", tono: "pizarra" },
  final: { nombre: "Fin", icono: "bandera", tono: "pizarra" },
  regla: { nombre: "Regla personalizada", icono: "regla", tono: "rosa" },
};

const CATEGORIA_DEL_NODO: Record<string, Categoria> = {
  lote: "entrada",
  deduplicacion: "filtro",
  reclamo: "filtro",
  enrutamiento: "ruta",
  facturas: "filtro",
  destino_del_correo: "ruta",
  remitente_identificable: "filtro",
  descarga_adjuntos: "documentos",
  volumen: "filtro",
  deduplicacion_contenido: "filtro",
  conversion_a_markdown: "ocr",
  saneamiento: "validacion",
  extraccion_determinista: "busqueda",
  agente: "ia",
  revisor: "ia",
  combinacion: "datos",
  registro_de_personas: "datos",
  compuerta: "validacion",
  bitacora: "validacion",
  radicacion: "integracion",
  cierre_outlook: "integracion",
  revision_manual: "salida",
  aviso_y_cierre: "salida",
};

export function categoria(n: Nodo): Categoria {
  if (n.terminal) return "final";
  return CATEGORIA_DEL_NODO[n.id] ?? (n.usa_ia ? "ia" : "filtro");
}

/** Las fases en que se agrupa el flujo en el lienzo, de izquierda a derecha. */
export const FASES: { id: string; nombre: string; nodos: string[] }[] = [
  { id: "recepcion", nombre: "Recepción", nodos: ["__inicio", "lote", "deduplicacion", "reclamo"] },
  { id: "clasificacion", nombre: "Clasificación", nodos: ["enrutamiento", "facturas", "destino_del_correo", "remitente_identificable"] },
  { id: "documentos", nombre: "Documentos y OCR", nodos: ["descarga_adjuntos", "volumen", "deduplicacion_contenido", "conversion_a_markdown", "saneamiento"] },
  { id: "extraccion", nombre: "Extracción e IA", nodos: ["extraccion_determinista", "agente", "revisor", "combinacion"] },
  { id: "validacion", nombre: "Validación", nodos: ["registro_de_personas", "compuerta", "bitacora"] },
  { id: "radicacion", nombre: "Radicación", nodos: ["radicacion", "cierre_outlook", "aviso_y_cierre", "__fin"] },
];

/** Dónde se dibujan los finales: bajo la fase de la que salen. */
export const FASE_DEL_FINAL: Record<string, string> = {
  fin_sin_tomar: "recepcion",
  duplicado: "recepcion",
  en_proceso: "recepcion",
  sin_reclamar: "recepcion",
  reenviado: "clasificacion",
  revision_manual: "validacion",
};

/** Dónde se engancha cada punto de las reglas personalizadas. */
export const ANCLA_DEL_PUNTO: Record<Regla["punto"], string> = { entrada: "facturas", validacion: "compuerta" };

export const PUNTOS: Record<Regla["punto"], { nombre: string; descripcion: string }> = {
  entrada: { nombre: "Al entrar", descripcion: "Después del enrutamiento y antes de abrir los adjuntos. Mira el asunto, el cuerpo, el remitente o los nombres de los adjuntos." },
  validacion: { nombre: "Antes de validar", descripcion: "Con los datos ya extraídos por el algoritmo, la IA y el registro, justo antes de la compuerta." },
};

export const CAMPOS_DE_ENTRADA: Record<string, string> = {
  asunto: "Asunto",
  cuerpo: "Cuerpo del correo",
  remitente: "Remitente (nombre y correo)",
  adjuntos: "Nombres de los adjuntos",
};

/** Los campos de la solicitud, en el orden en que se leen. */
export const CAMPOS: Record<string, string> = {
  nombre_afectado: "Nombre del afectado",
  numero_documento_afectado: "Documento del afectado",
  tipo_documento_afectado: "Tipo de documento",
  telefono_afectado: "Teléfono",
  sexo_afectado: "Sexo",
  direccion_afectado: "Dirección",
  departamento_afectado: "Departamento",
  municipio_afectado: "Municipio",
  nombre_remitente: "Nombre del remitente",
  numero_documento_remitente: "Documento del remitente",
  tipo_documento_remitente: "Tipo de doc. del remitente",
  tipo_remitente: "Tipo de remitente",
  tipo_pqrd: "Tipo de PQRD",
  tipo_solicitud: "Tipo de solicitud",
  oficina: "Oficina destino",
};

export const OBLIGATORIOS = new Set(["numero_documento_afectado", "nombre_afectado", "nombre_remitente", "tipo_solicitud"]);

export function nombreCampo(c: string): string {
  return CAMPOS[c] ?? c.replace(/_/g, " ").replace(/^./, (x) => x.toUpperCase());
}

export const OPERADORES: Record<Regla["operador"], string> = {
  coincide: "contiene el patrón",
  no_coincide: "no contiene el patrón",
  falta: "está vacío",
  existe: "tiene valor",
};

export const ACCIONES: Record<Regla["accion"], string> = {
  manual: "Enviar a revisión manual",
  aviso: "Solo dejar un aviso",
  fijar: "Fijar un valor en el campo",
};

export function reglaNueva(punto: Regla["punto"]): Regla {
  const id = `r-${Math.random().toString(36).slice(2, 8)}`;
  return {
    id,
    nombre: punto === "entrada" ? "Nueva regla de entrada" : "Nueva regla de validación",
    descripcion: "",
    activa: true,
    punto,
    campo: punto === "entrada" ? "asunto" : "telefono_afectado",
    operador: punto === "entrada" ? "coincide" : "falta",
    patron: "",
    ignorarMayusculas: true,
    accion: punto === "entrada" ? "manual" : "aviso",
    motivo: "REGLA_PERSONALIZADA",
    valor: "",
  };
}

/** Una regla en una frase: «Si el asunto contiene /tutela/ → revisión manual». */
export function fraseDeRegla(r: Regla): string {
  const campo = r.punto === "entrada" ? (CAMPOS_DE_ENTRADA[r.campo] ?? r.campo).toLowerCase() : nombreCampo(r.campo).toLowerCase();
  const cond = r.operador === "coincide" || r.operador === "no_coincide" ? `${OPERADORES[r.operador]} /${r.patron || "…"}/` : OPERADORES[r.operador];
  const accion = r.accion === "manual" ? `revisión manual (${r.motivo || "REGLA_PERSONALIZADA"})` : r.accion === "fijar" ? `fijar «${r.valor}»` : "aviso";
  return `Si ${campo} ${cond} → ${accion}`;
}

/** Lo que busca una expresión regular, en palabras: sus alternativas de primer nivel. */
export function alternativas(regex: string): string[] {
  const partes: string[] = [];
  let nivel = 0;
  let clase = false;
  let actual = "";
  for (let i = 0; i < regex.length; i++) {
    const c = regex[i]!;
    if (c === "\\") {
      actual += c + (regex[i + 1] ?? "");
      i++;
      continue;
    }
    if (c === "[") clase = true;
    else if (c === "]") clase = false;
    else if (!clase && c === "(") nivel++;
    else if (!clase && c === ")") nivel--;
    if (c === "|" && nivel === 0 && !clase) {
      partes.push(actual);
      actual = "";
      continue;
    }
    actual += c;
  }
  partes.push(actual);
  const legible = (p: string) =>
    p
      .replace(/^\^|\$$/g, "")
      .replace(/\(\?[:=!<]+/g, "(")
      .replace(/\\b/g, "")
      .replace(/\\s[+*]?|\\s\{[^}]*\}/g, " ")
      .replace(/\\d\{(\d+),(\d+)\}/g, "‹$1-$2 dígitos›")
      .replace(/\\d\{(\d+)\}/g, "‹$1 dígitos›")
      .replace(/\\d[+*]/g, "‹dígitos›")
      .replace(/\\\./g, ".")
      .replace(/\.\{0,(\d+)\}\??/g, "…")
      .replace(/\.[*+]\??/g, "…")
      .replace(/\\(.)/g, "$1")
      .replace(/\s+/g, " ")
      .trim();
  return partes.map(legible).filter(Boolean);
}
