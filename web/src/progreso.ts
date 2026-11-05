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
  const p: Progreso = { nodos: {}, desenlace: null, vueltas: 0, vueltasRevisor: 0, tokensEntrada: 0, tokensSalida: 0, herramientas: {}, consultas: 0, escrituras: 0 };
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
    } else if (e.tipo === "modelo_inicio" && e.quien === "revisor") {
      if (catalogo.nodos.some((n) => n.id === "revisor")) marcar("revisor", { estado: "activo" });
    } else if (e.tipo === "herramienta") {
      const h = String(e.herramienta);
      p.herramientas[h] = (p.herramientas[h] ?? 0) + 1;
    } else if (e.tipo === "proveedor") {
      p.consultas += 1;
    } else if (e.tipo === "escritura") {
      p.escrituras += 1;
    } else if (e.tipo === "resultado") {
      p.desenlace = String(e.accion ?? "");
    }
  }
  if (p.nodos.revisor?.estado === "activo" && p.nodos.agente?.estado !== "activo") p.nodos.revisor.estado = "hecho";

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
      if (n.etapa_cronometro || n.terminal || p.nodos[n.id] || n.id === "revisor") continue;
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

export const TONO_DESENLACE: Record<string, string> = {
  radicado: "verde",
  manual: "rojo",
  error: "rojo",
  duplicado: "ambar",
  reenviado: "azul",
  aplazado: "ambar",
};

export function segundos(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1).replace(".", ",")} s`;
}
