import { usar } from "../estado.ts";
import type { Corrida } from "../tipos.ts";

/** Lo que hizo cada nodo en una corrida, leído de sus eventos `etapa_inicio` / `etapa_fin`. */
export interface ResultadoNodo {
  estado: "activo" | "hecho" | "fallo" | "omitido";
  ms: number;
  veces: number;
  salida: string;
  siguiente: string;
  fallo: string;
  ramaError: boolean;
  continua: boolean;
  muestra: unknown;
  orden: number;
}

export function resultadosDe(corrida: Corrida | undefined): Record<string, ResultadoNodo> {
  const r: Record<string, ResultadoNodo> = {};
  if (!corrida) return r;
  let orden = 0;
  for (const e of corrida.eventos) {
    if (e.tipo !== "etapa_inicio" && e.tipo !== "etapa_fin") continue;
    const id = String(e.etapa ?? "");
    if (!id) continue;
    const previo = r[id] ?? { estado: "activo" as const, ms: 0, veces: 0, salida: "", siguiente: "", fallo: "", ramaError: false, continua: false, muestra: undefined, orden: ++orden };
    if (e.tipo === "etapa_inicio") r[id] = { ...previo, estado: "activo", veces: previo.veces + 1 };
    else {
      const fallo = String(e.fallo ?? "");
      r[id] = {
        ...previo,
        estado: e.omitido ? "omitido" : fallo ? "fallo" : "hecho",
        veces: Math.max(1, previo.veces),
        ms: previo.ms + Number(e.ms ?? 0),
        salida: String(e.salida ?? ""),
        siguiente: String(e.siguiente ?? ""),
        fallo,
        ramaError: Boolean(e.rama_error),
        continua: Boolean(e.continua),
        muestra: e.muestra,
      };
    }
  }
  return r;
}

/** La corrida que el lienzo muestra, si es de este flujo. */
export function usarCorridaVisible(): Corrida | undefined {
  return usar((s) => (s.corridaVisible ? s.corridas[s.corridaVisible] : undefined));
}
