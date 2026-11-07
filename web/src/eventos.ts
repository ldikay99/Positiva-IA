import { CAMPOS, OBLIGATORIOS, categoria, nombreCampo, type Categoria } from "./flujo.ts";
import type { NombreIcono } from "./iconos.tsx";
import { NOMBRES_DESENLACE, nodoDeEtapa, segundos } from "./progreso.ts";
import type { Catalogo, Corrida, Evento } from "./tipos.ts";

export type Tono = "ok" | "aviso" | "error" | "info" | "ia" | "herramienta" | "escritura" | "tenue";

export interface Paso {
  id: number;
  t: number;
  tono: Tono;
  icono: NombreIcono;
  titulo: string;
  extra?: string;
  detalle?: string;
  confianza?: number;
}

export interface Tramo {
  id: number;
  nodo: string;
  nombre: string;
  categoria: Categoria;
  inicio: number;
  ms?: number;
  estado: "activo" | "hecho" | "fallo";
  pasos: Paso[];
}

function json(v: unknown): string {
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

function corto(v: unknown, n = 70): string {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function leer(v: unknown): unknown {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
}

const pct = (x: unknown) => `${Math.round(Number(x) * 100)} %`;

/** Lo que hizo una herramienta del agente, contado como una persona lo diría. */
function herramienta(e: Evento): Omit<Paso, "id" | "t"> {
  const a = (e.argumentos ?? {}) as Record<string, unknown>;
  const r = leer(e.resultado) as Record<string, unknown> | unknown[] | string | null;
  const detalle = `${json(a)}\n→ ${String(e.resultado ?? "")}`;
  const extra = segundos(Number(e.ms ?? 0));
  if (!e.ok) return { tono: "error", icono: "alerta", titulo: `${String(e.herramienta)} falló`, extra, detalle };
  switch (e.herramienta) {
    case "buscar_en_documentos": {
      const total = r && typeof r === "object" && !Array.isArray(r) ? Number(r.total ?? 0) : 0;
      return { tono: "herramienta", icono: "buscar", titulo: `Busca «${corto(a.patron, 60)}» en los documentos`, extra: `${total} coincidencia${total === 1 ? "" : "s"} · ${extra}`, detalle };
    }
    case "leer_documento":
      return { tono: "herramienta", icono: "documento", titulo: `Lee ${String(a.documento ?? "un documento")}${a.desde ? ` desde la línea ${String(a.desde)}` : ""}`, extra, detalle };
    case "listar_documentos":
      return { tono: "herramienta", icono: "capas", titulo: "Revisa qué documentos trae el correo", extra: `${Array.isArray(r) ? r.length : "?"} documentos · ${extra}`, detalle };
    case "consultar_catalogo":
      return { tono: "herramienta", icono: "base", titulo: `Consulta el catálogo «${String(a.nombre ?? "")}»`, extra, detalle };
    case "validar_documento":
      return { tono: "herramienta", icono: "escudo", titulo: `Valida el documento ${String(a.numero ?? "")}`, extra, detalle };
    case "consultar_persona":
      return { tono: "herramienta", icono: "persona", titulo: `Consulta en el registro la persona ${String(a.numero_documento ?? "")}`, extra, detalle };
    case "reportar_hallazgo":
      return { tono: "ok", icono: "chispa", titulo: `Encontró ${nombreCampo(String(a.campo))}: ${corto(a.valor, 60)}`, extra: a.confianza !== undefined ? pct(a.confianza) : undefined, confianza: a.confianza !== undefined ? Number(a.confianza) : undefined, detalle: a.evidencia ? `Evidencia: ${String(a.evidencia)}` : detalle };
    case "pedir_revision":
      return { tono: "ia", icono: "escudo", titulo: "Pide a un segundo modelo que revise lo que va a enviar", extra, detalle };
    default:
      return { tono: "herramienta", icono: "herramientas", titulo: String(e.herramienta), extra, detalle };
  }
}

/** Lo que el motor le pide al buzón, dicho como pregunta. */
const OPERACION_DEL_BUZON: Record<string, string> = {
  hilo_ya_radicado: "¿algún correo del hilo ya tiene radicado?",
  buscar_etiqueta_ent_por_asunto: "¿hay un radicado del mismo caso con este asunto?",
  _es_el_mismo_caso: "¿es el mismo caso que el radicado encontrado?",
  documentos_nuevos_frente_al_radicado: "¿trae documentos que el radicado no tiene?",
  correo_con_etiqueta: "lee el correo radicado para compararlo",
  mensajes_del_hilo: "el resto del hilo que sigue en la bandeja",
  get_conversation_message_ids: "el resto del hilo que sigue en la bandeja",
  _nombres_de_adjuntos: "los adjuntos del radicado",
  get_attachments: "los adjuntos de otro correo",
  download_attachment: "descarga un adjunto",
  get_email_by_id: "lee un correo",
  _leer_categorias: "lee las etiquetas del correo",
};

/** Una escritura bloqueada dicha como lo que el job habría hecho. */
function loQueHaria(e: Evento): string {
  const quien = e.propio === false ? "otro correo del hilo" : "el correo";
  switch (String(e.operacion)) {
    case "marcar":
      return e.marca ? `marcaría ${quien} «${String(e.marca)}»${e.quitar_de_proceso ? " y le quitaría las marcas de proceso" : ""}` : `le quitaría a ${quien} las marcas de proceso`;
    case "move_email":
      return `movería ${quien} a «${String(e.destino || "otra carpeta")}»`;
    case "forward_email":
      return `reenviaría ${quien} a ${String(e.destino || "otra área")}`;
    case "responder_correo":
      return `respondería ${quien}`;
    case "etiquetar_correo":
      return `etiquetaría ${quien}`;
    case "mover_hilo_completo":
      return "movería el hilo completo";
    default:
      return String(e.operacion).replace(/_/g, " ");
  }
}

export function describir(e: Evento, _catalogo?: Catalogo | null): Omit<Paso, "id" | "t"> | null {
  switch (e.tipo) {
    case "paso": {
      const tono = (e.tono === "ok" || e.tono === "aviso" || e.tono === "error" ? e.tono : "info") as Tono;
      return { tono, icono: tono === "ok" ? "ok" : tono === "error" ? "alerta" : tono === "aviso" ? "alerta" : "info", titulo: String(e.titulo), detalle: e.detalle ? String(e.detalle) : undefined };
    }
    case "busqueda": {
      const nombre = nombreCampo(String(e.campo));
      if (e.encontrado && e.aceptado !== false)
        return { tono: "ok", icono: "ok", titulo: `${nombre}: ${corto(e.valor, 70)}`, extra: `${pct(e.confianza)} · ${String(e.metodo || "regla")}`, confianza: Number(e.confianza), detalle: `Evidencia: ${String(e.evidencia ?? "")}${e.a_confirmar ? `\nPasa al agente para confirmar: ${String(e.a_confirmar)}` : ""}` };
      if (e.encontrado)
        return { tono: "aviso", icono: "alerta", titulo: `${nombre}: «${corto(e.valor, 50)}» con poca confianza`, extra: `${pct(e.confianza)} < ${pct(e.umbral)} · lo decide la IA`, confianza: Number(e.confianza), detalle: `Evidencia: ${String(e.evidencia ?? "")}` };
      return { tono: "tenue", icono: "cerrar", titulo: `${nombre}: no se encontró en el texto`, extra: String(e.motivo ?? ""), detalle: e.detalle ? String(e.detalle) : undefined };
    }
    case "regla":
      return e.cumple
        ? { tono: e.accion === "manual" ? "error" : "aviso", icono: "regla", titulo: `Regla «${String(e.nombre)}» se cumplió`, extra: e.accion === "manual" ? `→ revisión manual (${String(e.motivo)})` : e.accion === "fijar" ? `→ fija ${String(e.campo)} = ${String(e.valor)}` : "→ aviso", detalle: String(e.detalle ?? "") }
        : { tono: "tenue", icono: "regla", titulo: `Regla «${String(e.nombre)}»: no aplica`, extra: String(e.detalle ?? "") || undefined };
    case "modelo_inicio":
      return { tono: "tenue", icono: "chispa", titulo: `${e.quien === "revisor" ? "Revisor" : "Agente"} · vuelta ${String(e.vuelta)}: consulta a ${String(e.modelo)}`, detalle: e.sistema ? `INSTRUCCIONES\n${String(e.sistema)}\n\nÚLTIMO MENSAJE\n${String(e.ultimo)}` : String(e.ultimo || "") || undefined };
    case "modelo_fin": {
      if (e.ok === false) return { tono: "error", icono: "alerta", titulo: "El modelo no respondió", detalle: String(e.error) };
      const llamadas = (e.llamadas as { herramienta: string }[] | undefined) ?? [];
      return {
        tono: "ia",
        icono: "chispa",
        titulo: llamadas.length ? `${e.quien === "revisor" ? "El revisor" : "La IA"} decide usar ${llamadas.map((l) => l.herramienta.replace(/_/g, " ")).join(", ")}` : `${e.quien === "revisor" ? "El revisor" : "La IA"} responde`,
        extra: `${segundos(Number(e.ms))} · ${(Number(e.tokens_entrada) + Number(e.tokens_salida)).toLocaleString("es-CO")} tokens${Number(e.costo_estimado_usd ?? 0) > 0 ? ` · ≈ US$ ${Number(e.costo_estimado_usd).toLocaleString("es-CO", { maximumSignificantDigits: 2 })}` : ""}${e.proveedor && e.proveedor !== "vertex" ? ` · ${String(e.proveedor)}` : ""}`,
        detalle: String(e.texto || "") || undefined,
      };
    }
    case "herramienta":
      return herramienta(e);
    case "proveedor":
      return { tono: e.ok === false ? "error" : "info", icono: "persona", titulo: e.ok === false ? "El registro de personas falló" : e.encontrado ? `Registro de personas: ${String(e.nombre || "persona encontrada")}` : "Registro de personas: sin resultado", extra: segundos(Number(e.ms)), detalle: e.error ? String(e.error) : e.documento ? `documento ${String(e.documento)}` : undefined };
    case "compuerta":
      return e.radicacion_real
        ? { tono: "error", icono: "alerta", titulo: "Radicación real: encendida", extra: "escribe como el job" }
        : { tono: "tenue", icono: "candado", titulo: "Radicación real: apagada (simulación)", extra: "toda escritura queda retenida" };
    case "fuentes": {
      const l = (e.lecturas as { sistema: string; real: boolean; detalle: string }[] | undefined) ?? [];
      const reales = l.filter((x) => x.real).map((x) => x.sistema);
      return { tono: "info", icono: "proveedores", titulo: `Lee de verdad: ${reales.join(", ") || "nada"}`, extra: `${l.length - reales.length} con doble`, detalle: l.map((x) => `${x.real ? "real " : "doble"} · ${x.sistema}: ${x.detalle}`).join("\n") };
    }
    case "lectura_real":
      return e.ok === false
        ? { tono: "aviso", icono: "alerta", titulo: `${String(e.sistema)} no respondió (${String(e.operacion)})`, detalle: [e.error, e.detalle].filter(Boolean).map(String).join("\n") || undefined }
        : { tono: "info", icono: "base", titulo: `${String(e.sistema)} real · ${String(e.operacion)} (solo lectura)`, extra: `${String(e.resultado ?? "")} · ${segundos(Number(e.ms ?? 0))}` };
    case "aviso_programador":
      return { tono: "aviso", icono: "calendario", titulo: String(e.mensaje), detalle: e.error ? String(e.error) : undefined };
    case "escritura":
      if (e.bloqueada === false)
        return { tono: e.ok === false ? "error" : "escritura", icono: "alerta", titulo: `ESCRIBE DE VERDAD · ${String(e.sistema)} · ${String(e.operacion).replace(/_/g, " ")}`, extra: "compuerta de radicación encendida", detalle: e.detalle || e.error ? json(e.detalle ?? e.error) : undefined };
      if (e.sistema === "sgdea" && e.operacion === "radicar") return { tono: "escritura", icono: "enviar", titulo: "Envía la radicación a SGDEA (simulado, no se escribe)", extra: `${String(e.n_adjuntos ?? 0)} adjuntos`, detalle: json(e.payload) };
      if (e.sistema === "buzon") return { tono: "escritura", icono: "correo", titulo: `El job ${loQueHaria(e)}`, extra: "bloqueado: el visor no escribe en el buzón", detalle: e.detalle ? json(e.detalle) : undefined };
      return { tono: "tenue", icono: "enviar", titulo: `SGDEA · ${String(e.operacion).replace(/_/g, " ")} (simulado)`, detalle: e.payload || e.detalle ? json(e.payload ?? e.detalle) : undefined };
    case "buzon":
      if (e.real === false) return { tono: "tenue", icono: "correo", titulo: `Buzón · ${OPERACION_DEL_BUZON[String(e.operacion)] ?? String(e.operacion)}`, extra: "sin buzón real: correo de la carpeta local" };
      if (e.ok === false) return { tono: "error", icono: "alerta", titulo: `El buzón no respondió: ${OPERACION_DEL_BUZON[String(e.operacion)] ?? String(e.operacion)}`, detalle: String(e.error ?? "") };
      return { tono: "info", icono: "correo", titulo: `Buzón real · ${OPERACION_DEL_BUZON[String(e.operacion)] ?? String(e.operacion)}`, extra: `${String(e.resultado ?? "")} · ${segundos(Number(e.ms ?? 0))}`, detalle: e.error ? `aviso del servicio: ${String(e.error)}` : undefined };
    case "log":
      return { tono: e.severidad === "ERROR" ? "error" : e.severidad === "WARNING" ? "aviso" : "info", icono: "logs", titulo: String(e.mensaje), extra: String(e.severidad ?? ""), detalle: json(e.campos) };
    case "configuracion":
      return { tono: "error", icono: "alerta", titulo: "Ajustes que no se aplicaron", detalle: json(e.fallidas) };
    case "adjunto_inicio":
      return { tono: "info", icono: "adjunto", titulo: `Abre ${String(e.nombre)}`, extra: Number(e.paginas) ? `${String(e.paginas)} hojas` : String(e.tipo_archivo ?? "") };
    case "hoja": {
      const m = String(e.metodo);
      const como = m === "texto" ? "texto propio" : m === "vision" ? "transcrita por visión" : m === "ilegible" ? "sin texto" : `OCR ${e.confianza ? pct(e.confianza) : ""}`;
      const extra = [como, Number(e.girada) ? `girada ${String(e.girada)}°` : "", e.relectura_200 ? "releída a 200 ppp" : "", segundos(Number(e.ms ?? 0))].filter(Boolean).join(" · ");
      return { tono: m === "ilegible" ? "aviso" : m === "vision" ? "ia" : "tenue", icono: m === "vision" ? "ojo" : m === "texto" ? "documento" : "escaner", titulo: `Hoja ${String(e.pagina)}${Number(e.total) ? ` de ${String(e.total)}` : ""}`, extra, confianza: typeof e.confianza === "number" ? e.confianza : undefined };
    }
    case "vision":
      return { tono: e.ok ? "ia" : "error", icono: "ojo", titulo: e.ok ? `Gemini transcribe con visión la hoja ${String(e.pagina ?? "")}` : "La transcripción por visión falló", extra: e.ok ? `${String(e.caracteres ?? 0)} caracteres · ${segundos(Number(e.ms ?? 0))}` : undefined, detalle: e.error ? String(e.error) : undefined };
    case "consulta": {
      const quien = e.quien === "revisor" ? "El revisor" : "La IA";
      const res = (e.resultados as { nombre: string; pagina: number | null }[]) ?? [];
      const donde = res.slice(0, 3).map((r) => `${r.nombre}${r.pagina ? ` hoja ${r.pagina}` : ""}`).join(", ");
      return { tono: "herramienta", icono: "buscar", titulo: e.modo === "consultar" ? `${quien} pregunta al índice «${corto(e.consulta, 60)}»` : `${quien} busca «${corto(e.consulta, 60)}»`, extra: `${String(e.total ?? 0)} resultado${Number(e.total) === 1 ? "" : "s"}${donde ? ` · ${donde}` : ""}` };
    }
    case "correccion":
      return e.fase === "inicio"
        ? { tono: "aviso", icono: "deshacer", titulo: `Se devuelve a la IA para corregir: ${Object.keys((e.campos as object) ?? {}).map(nombreCampo).join(", ")}`, extra: e.origen === "revisor" ? "lo objetó el revisor" : "lo rechazó la compuerta", detalle: json(e.campos) }
        : { tono: "ia", icono: "deshacer", titulo: `Corrección terminada: la compuerta dice ${String(e.veredicto)}`, extra: segundos(Number(e.ms ?? 0)), detalle: json(e.campos) };
    case "revision":
      if (e.fase === "fin" && e.ok) {
        const v = Object.values((e.veredictos as Record<string, { veredicto: string }>) ?? {});
        const objeta = v.filter((x) => x.veredicto !== "OK").length;
        return { tono: objeta ? "aviso" : "ok", icono: "escudo", titulo: objeta ? `El revisor objeta ${objeta} de ${v.length} campos` : `El revisor da por buenos los ${v.length} campos`, extra: segundos(Number(e.ms ?? 0)), detalle: json(e.veredictos) };
      }
      if (e.fase === "decision" && ((e.se_mantienen as unknown[]) ?? []).length) return { tono: "info", icono: "escudo", titulo: "Lo que el texto prueba se mantiene pese al revisor", detalle: ((e.se_mantienen as string[]) ?? []).join("\n") };
      return null;
    default:
      return null;
  }
}

/** La corrida contada por etapas: cada etapa con lo que buscó, encontró y decidió. */
export function narrar(catalogo: Catalogo | null, corrida: Corrida | undefined): Tramo[] {
  const tramos: Tramo[] = [];
  if (!catalogo || !corrida) return tramos;
  const nombre = (id: string) => catalogo.nodos.find((n) => n.id === id)?.nombre ?? id;
  const cat = (id: string): Categoria => {
    const n = catalogo.nodos.find((x) => x.id === id);
    return n ? categoria(n) : "filtro";
  };
  const vecesPorEtapa: Record<string, number> = {};
  const abiertos: Record<string, Tramo> = {};
  let id = 0;
  const nuevo = (nodo: string, t: number, estado: Tramo["estado"]): Tramo => {
    const tr: Tramo = { id: id++, nodo, nombre: nombre(nodo), categoria: cat(nodo), inicio: t, estado, pasos: [] };
    tramos.push(tr);
    return tr;
  };
  const actual = (t: number): Tramo => tramos.at(-1) ?? nuevo("lote", t, "hecho");

  for (const e of corrida.eventos) {
    if (e.tipo === "etapa_inicio") {
      const etapa = String(e.etapa);
      const vez = vecesPorEtapa[etapa] ?? 0;
      vecesPorEtapa[etapa] = vez + 1;
      const n = nodoDeEtapa(catalogo, etapa, vez);
      abiertos[etapa] = nuevo(n?.id ?? etapa, e.t, "activo");
      continue;
    }
    if (e.tipo === "etapa_fin") {
      const tr = abiertos[String(e.etapa)];
      if (tr) {
        tr.ms = Number(e.ms ?? 0);
        tr.estado = e.fallo ? "fallo" : "hecho";
        if (e.fallo) tr.pasos.push({ id: id++, t: e.t, tono: "error", icono: "alerta", titulo: "La etapa falló", detalle: String(e.fallo) });
        delete abiertos[String(e.etapa)];
      }
      continue;
    }
    const p = describir(e, catalogo);
    if (!p) continue;
    let tr = actual(e.t);
    // Las etapas que el motor no cronometra (facturas, combinación…) se anuncian con su nodo.
    if (e.tipo === "paso" && e.nodo && tr.nodo !== e.nodo) {
      const abierto = Object.values(abiertos).find((x) => x.nodo === e.nodo);
      tr = abierto ?? nuevo(String(e.nodo), e.t, "hecho");
    }
    tr.pasos.push({ id: id++, t: e.t, ...p });
  }
  return tramos;
}

// ------------------------------------------------------------- la solicitud

export type Fuente = "algoritmo" | "ia" | "registro" | "regla" | "compuerta" | "a_la_ia" | "sin_resolver" | "buscando";

export interface EstadoCampo {
  campo: string;
  nombre: string;
  obligatorio: boolean;
  fuente: Fuente;
  valor?: unknown;
  confianza?: number;
  nota?: string;
  reparo?: string;
  /** Lo que dijo la compuerta del campo, en palabras. */
  reparoDetalle?: string;
  /** El valor que tenía antes de que la compuerta lo cambiara o lo vaciara. */
  antes?: string;
}

/** Los reparos de la compuerta en palabras: el código queda para el detalle. */
const REPARO_LEGIBLE: Record<string, string> = {
  NIT_DE_ENTIDAD_EN_PERSONA: "un NIT de empresa en una persona",
  NOMBRE_SIN_FORMA_DE_EMPRESA: "el nombre no tiene forma de empresa",
  EMPRESA_CON_DOCUMENTO_DE_PERSONA: "una empresa con documento de persona",
  DOCUMENTO_DE_OTRA_PERSONA: "el documento es de otra persona",
  CONFIANZA_BAJA: "poca confianza",
};

function reparoLegible(codigo: string): string {
  return REPARO_LEGIBLE[codigo] ?? codigo.toLowerCase().replace(/_/g, " ");
}

/** Cómo va quedando cada campo de la solicitud a medida que avanza la corrida. */
export function solicitud(corrida: Corrida | undefined): EstadoCampo[] {
  const m = new Map<string, EstadoCampo>();
  const fijar = (campo: string, c: Partial<EstadoCampo>) => {
    const previo = m.get(campo) ?? { campo, nombre: nombreCampo(campo), obligatorio: OBLIGATORIOS.has(campo), fuente: "buscando" as Fuente };
    m.set(campo, { ...previo, ...c });
  };
  if (!corrida) return [];
  for (const e of corrida.eventos) {
    if (e.tipo === "busqueda") {
      const campo = String(e.campo);
      if (campo === "geografia") continue;
      if (e.encontrado && e.aceptado !== false) fijar(campo, { fuente: "algoritmo", valor: e.valor, confianza: Number(e.confianza), nota: String(e.metodo ?? "") });
      else if (e.encontrado) fijar(campo, { fuente: "a_la_ia", valor: e.valor, confianza: Number(e.confianza), nota: "poca confianza: lo confirma la IA" });
      else fijar(campo, { fuente: "a_la_ia", nota: String(e.motivo ?? "no está en el texto") });
    } else if (e.tipo === "herramienta" && e.herramienta === "reportar_hallazgo" && e.ok) {
      const a = (e.argumentos ?? {}) as Record<string, unknown>;
      if (a.campo) fijar(String(a.campo), { fuente: "ia", valor: a.valor, confianza: a.confianza !== undefined ? Number(a.confianza) : undefined, nota: "lo encontró la IA" });
    } else if (e.tipo === "paso" && e.nodo === "registro_de_personas" && e.campos) {
      for (const [c, v] of Object.entries(e.campos as Record<string, unknown>)) fijar(c, { fuente: "registro", valor: v, nota: "del registro de personas" });
    } else if (e.tipo === "regla" && e.cumple && e.accion === "fijar" && e.campo) {
      fijar(String(e.campo), { fuente: "regla", valor: e.valor, confianza: 1, nota: `regla «${String(e.nombre)}»` });
    } else if (e.tipo === "paso" && e.nodo === "compuerta" && Array.isArray(e.reparos)) {
      // Cada ronda de la compuerta reemplaza a la anterior: lo que la IA ya
      // corrigió no sigue marcado como reparo.
      for (const c of m.values()) {
        c.reparo = undefined;
        c.reparoDetalle = undefined;
      }
      for (const r of e.reparos as { campo: string; codigo: string; bloqueante: boolean; detalle?: string }[]) {
        if (r.campo && m.has(r.campo)) fijar(r.campo, { reparo: `${reparoLegible(r.codigo)}${r.bloqueante ? "" : " · corregido"}`, reparoDetalle: `${r.codigo}${r.detalle ? `: ${r.detalle}` : ""}` });
      }
    } else if (e.tipo === "resultado" && e.hallazgos) {
      const atrib = (e.atribucion ?? {}) as Record<string, string>;
      for (const [c, h] of Object.entries(e.hallazgos as Record<string, { valor: unknown; confianza: number; metodo: string }>)) {
        const origen = atrib[c];
        const ev = String((h as { evidencia?: string }).evidencia ?? "");
        if (h.metodo === "compuerta") {
          // La compuerta cambió o vació el valor: es ella la que decide lo que se envía.
          fijar(c, { valor: h.valor, confianza: undefined, fuente: "compuerta", antes: /antes ([^)]*)\)/.exec(ev)?.[1], nota: ev || "lo corrigió la compuerta" });
          continue;
        }
        fijar(c, { valor: h.valor, confianza: h.confianza, fuente: origen === "ia" ? "ia" : origen === "registro" ? "registro" : String(h.metodo).startsWith("regla:") ? "regla" : "algoritmo", nota: h.metodo });
      }
      for (const c of (e.sin_resolver as string[] | undefined) ?? []) fijar(c, { fuente: "sin_resolver", nota: "nadie lo resolvió" });
    }
  }
  const orden = Object.keys(CAMPOS);
  return [...m.values()].sort((a, b) => (orden.indexOf(a.campo) + 1 || 99) - (orden.indexOf(b.campo) + 1 || 99));
}

export const NOMBRE_FUENTE: Record<Fuente, string> = {
  algoritmo: "Algoritmo",
  ia: "IA",
  registro: "Registro",
  regla: "Regla",
  compuerta: "Compuerta",
  a_la_ia: "Pasa a la IA",
  sin_resolver: "Sin resolver",
  buscando: "Buscando",
};

export function resumenResultado(r: Evento | undefined): string {
  if (!r) return "";
  const accion = String(r.accion);
  return `${NOMBRES_DESENLACE[accion] ?? accion}${r.motivo ? ` · ${String(r.motivo)}` : ""}`;
}
