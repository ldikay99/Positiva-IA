import type { Catalogo, Corrida, EstadoNodo, Nodo } from "./tipos.ts";

export interface EstadoDeNodo {
  estado: EstadoNodo;
  ms: number;
  veces: number;
  orden: number;
  fallo: string;
  medido: boolean;
}

export interface Progreso {
  nodos: Record<string, EstadoDeNodo>;
  desenlace: string | null;
  vueltas: number;
  vueltasRevisor: number;
  tokensEntrada: number;
  tokensSalida: number;
  herramientas: Record<string, number>;
  consultas: number;
  escrituras: number;
  hojas: number;
  imagenesVision: number;
  consultasIndice: number;
}

/** El nodo final del diagrama al que lleva cada desenlace del motor. */
const NODO_DEL_DESENLACE: Record<string, string> = {
  radicado: "cierre_outlook",
  manual: "revision_manual",
  aplazado: "revision_manual",
  duplicado: "duplicado",
  reenviado: "reenviado",
  en_proceso: "en_proceso",
  sin_reclamar: "sin_reclamar",
  sin_tomar: "fin_sin_tomar",
};

export function nodoDeEtapa(catalogo: Catalogo, etapa: string, vez = 0): Nodo | undefined {
  const candidatos = catalogo.nodos.filter((n) => n.etapa_cronometro === etapa);
  if (candidatos.length) return candidatos[Math.min(vez, candidatos.length - 1)];
  return catalogo.nodos.find((n) => n.id === etapa);
}

/** Qué nodos recorrió una corrida, en qué orden y con qué resultado, a partir de sus eventos. */
export function progreso(catalogo: Catalogo | null, corrida: Corrida | undefined): Progreso {
  const p: Progreso = { nodos: {}, desenlace: null, vueltas: 0, vueltasRevisor: 0, tokensEntrada: 0, tokensSalida: 0, herramientas: {}, consultas: 0, escrituras: 0, hojas: 0, imagenesVision: 0, consultasIndice: 0 };
  if (!catalogo || !corrida) return p;
  let orden = 0;
  const vecesPorEtapa: Record<string, number> = {};
  const abiertos: Record<string, string> = {};
  const marcar = (id: string, cambio: Partial<EstadoDeNodo>) => {
    const previo = p.nodos[id] ?? { estado: "inactivo" as EstadoNodo, ms: 0, veces: 0, orden: ++orden, fallo: "", medido: false };
    p.nodos[id] = { ...previo, ...cambio };
  };

  for (const e of corrida.eventos) {
    if (e.tipo === "etapa_inicio") {
      const etapa = String(e.etapa);
      const vez = vecesPorEtapa[etapa] ?? 0;
      vecesPorEtapa[etapa] = vez + 1;
      const n = nodoDeEtapa(catalogo, etapa, vez);
      if (!n) continue;
      abiertos[etapa] = n.id;
      marcar(n.id, { estado: "activo", veces: (p.nodos[n.id]?.veces ?? 0) + 1, medido: true });
    } else if (e.tipo === "etapa_fin") {
      const etapa = String(e.etapa);
      const id = abiertos[etapa] ?? nodoDeEtapa(catalogo, etapa)?.id;
      if (!id) continue;
      const fallo = String(e.fallo ?? "");
      marcar(id, { estado: fallo ? "fallo" : "hecho", ms: (p.nodos[id]?.ms ?? 0) + Number(e.ms ?? 0), fallo, medido: true });
    } else if (e.tipo === "modelo_fin") {
      if (e.quien === "revisor") p.vueltasRevisor += 1;
      else p.vueltas += 1;
      p.tokensEntrada += Number(e.tokens_entrada ?? 0);
      p.tokensSalida += Number(e.tokens_salida ?? 0);
    } else if ((e.tipo === "vision" || e.tipo === "hoja") && catalogo.nodos.some((n) => n.id === "transcripcion_vision")) {
      // La visión ocurre dentro de la conversión: se dibuja justo después de ella.
      if (e.tipo === "vision") {
        const base = p.nodos.conversion_a_markdown?.orden ?? orden;
        marcar("transcripcion_vision", { estado: "hecho", veces: (p.nodos.transcripcion_vision?.veces ?? 0) + 1, ...(p.nodos.transcripcion_vision ? {} : { orden: base + 0.3 }) });
        p.imagenesVision += 1;
      } else p.hojas += 1;
    } else if ((e.tipo === "consulta" || e.tipo === "lectura" || e.tipo === "inventario") && catalogo.nodos.some((n) => n.id === "indice_por_hoja")) {
      const base = p.nodos.conversion_a_markdown?.orden ?? orden;
      marcar("indice_por_hoja", { estado: "hecho", veces: (p.nodos.indice_por_hoja?.veces ?? 0) + 1, ...(p.nodos.indice_por_hoja ? {} : { orden: base + 0.6 }) });
      if (e.tipo === "consulta") p.consultasIndice += 1;
    } else if (e.tipo === "herramienta") {
      const h = String(e.herramienta);
      p.herramientas[h] = (p.herramientas[h] ?? 0) + 1;
    } else if (e.tipo === "proveedor") {
      p.consultas += 1;
    } else if (e.tipo === "paso" && typeof e.nodo === "string") {
      // Las etapas que corren dentro de otra (anexos y seguimiento van dentro
      // de la deduplicación) no tienen cronómetro propio: las enciende su paso.
      const n = catalogo.nodos.find((x) => x.id === e.nodo);
      if (n && !n.etapa_cronometro && !n.terminal) marcar(n.id, { estado: "hecho", veces: (p.nodos[n.id]?.veces ?? 0) + 1 });
    } else if (e.tipo === "escritura") {
      p.escrituras += 1;
    } else if (e.tipo === "resultado") {
      p.desenlace = String(e.accion ?? "");
    }
  }

  // Las etapas que el motor no cronometra (reclamo, facturas, destino,
  // combinación, bitácora…) se dan por recorridas si el correo pasó por una
  // etapa anterior y por una posterior en el mismo camino.
  const sucesores: Record<string, string[]> = {};
  const predecesores: Record<string, string[]> = {};
  for (const a of catalogo.aristas) {
    (sucesores[a.desde] ??= []).push(a.hacia);
    (predecesores[a.hacia] ??= []).push(a.desde);
  }
  const final = p.desenlace ? NODO_DEL_DESENLACE[p.desenlace] : undefined;
  if (final && catalogo.nodos.some((n) => n.id === final)) marcar(final, { estado: "hecho" });
  if (p.desenlace && catalogo.nodos.some((n) => n.id === "aviso_y_cierre") && p.desenlace !== "error" && p.desenlace !== "cancelada") marcar("aviso_y_cierre", { estado: "hecho" });

  // Los finales reciben flechas de casi todas las etapas: no prueban que el
  // correo haya pasado por una etapa intermedia.
  const sumideros = new Set(["revision_manual", "aviso_y_cierre", ...catalogo.nodos.filter((n) => n.terminal).map((n) => n.id)]);
  const alcanzaVisitado = (desde: string, sentido: Record<string, string[]>): boolean => {
    const vistos = new Set<string>();
    const pila = [...(sentido[desde] ?? [])];
    while (pila.length) {
      const x = pila.pop()!;
      if (vistos.has(x) || (sentido === sucesores && sumideros.has(x))) continue;
      vistos.add(x);
      if (p.nodos[x] && p.nodos[x].estado !== "inactivo") return true;
      const n = catalogo.nodos.find((y) => y.id === x);
      if (n && !n.etapa_cronometro) pila.push(...(sentido[x] ?? []));
    }
    return false;
  };
  let cambio = true;
  while (cambio) {
    cambio = false;
    for (const n of catalogo.nodos) {
      if (n.etapa_cronometro || n.terminal || p.nodos[n.id] || n.id === "transcripcion_vision" || n.id === "indice_por_hoja") continue;
      if (alcanzaVisitado(n.id, predecesores) && alcanzaVisitado(n.id, sucesores)) {
        const antes = (predecesores[n.id] ?? []).map((x) => p.nodos[x]?.orden ?? 0);
        marcar(n.id, { estado: "hecho", orden: Math.max(0, ...antes) + 0.5 });
        cambio = true;
      }
    }
  }
  return p;
}

export const NOMBRES_DESENLACE: Record<string, string> = {
  radicado: "Radicado en SGDEA",
  manual: "Revisión manual",
  duplicado: "Duplicado",
  reenviado: "Reenviado a otra área",
  aplazado: "Aplazado",
  en_proceso: "Lo tomó otra corrida",
  sin_reclamar: "Sin reclamar",
  sin_tomar: "Sin tomar",
  colgado: "Colgado",
  error: "Error",
  cancelada: "Cancelada",
};

// Nombre corto para las fichas de la bandeja, donde caben dos desenlaces en una línea.
export const NOMBRES_CORTOS_DESENLACE: Record<string, string> = {
  ...NOMBRES_DESENLACE,
  radicado: "Radicado",
  manual: "Manual",
  reenviado: "Reenviado",
  en_proceso: "Otra corrida",
};

export const TONO_DESENLACE: Record<string, string> = {
  radicado: "verde",
  manual: "rojo",
  error: "rojo",
  duplicado: "ambar",
  reenviado: "azul",
  aplazado: "ambar",
};

/** Los motivos con que el motor manda un correo a revisión manual, en palabras. */
const MOTIVOS: Record<string, string> = {
  SIN_NOMBRE_REMITENTE: "No se sabe quién escribe",
  SIN_NOMBRE_AFECTADO: "No se identificó a la persona afectada",
  SIN_DOCUMENTO: "Falta el documento de identidad",
  DOCUMENTO_AMBIGUO: "Varios documentos posibles",
  DOCUMENTO_DE_OTRA_PERSONA: "El documento es de otra persona",
  EMPRESA_CON_DOCUMENTO_DE_PERSONA: "Empresa con documento de persona",
  SIN_TIPO_SOLICITUD: "No se pudo clasificar la solicitud",
  SOLO_EMPRESA_SIN_AFECTADO: "Solo identifica una empresa",
  ADJUNTO_PROTEGIDO: "Adjunto con contraseña",
  ANEXO_DE_RADICADO: "Anexo de un radicado anterior",
  POSIBLE_SOLICITUD_NUEVA: "Posible solicitud nueva en el hilo",
  ADJUNTO_ILEGIBLE: "Adjunto que no se pudo leer",
  ADJUNTO_NO_LEIDO: "Adjunto en un formato no compatible",
  SIN_OCR: "Imagen sin lectura de texto",
  VALIDACION_RECHAZO: "La validación final rechazó los datos",
  INYECCION_DETECTADA: "Intento de manipular a la IA",
  CLOUDFLARE_403: "SGDEA pidió verificación (captcha)",
  SGDEA_CAIDO: "SGDEA no respondió",
  PETICION_MAL_FORMADA: "SGDEA rechazó la petición",
  RADICACION_SIN_CONFIRMAR: "Radicación sin número de confirmación",
  SIN_TELEFONO: "Falta el teléfono",
  REGLA_PERSONALIZADA: "Lo pidió una regla propia",
};

/** «SIN_NOMBRE_AFECTADO» → «No se identificó a la persona afectada»; lo desconocido, en minúsculas legibles. */
export function motivoLegible(codigo: string): string {
  if (!codigo) return "";
  if (MOTIVOS[codigo]) return MOTIVOS[codigo];
  if (!/^[A-Za-z0-9_-]+$/.test(codigo) || !/[_-]/.test(codigo)) return codigo;
  const t = codigo.replace(/[_-]+/g, " ").toLowerCase();
  return t[0]!.toUpperCase() + t.slice(1);
}

export function segundos(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1).replace(".", ",")} s`;
}
