/**
 * Enmascara los valores secretos de un flujo en todo lo que el servidor reenvía o
 * guarda: eventos, consola, resultados, errores. Cubre el valor tal cual, su forma
 * escapada en JSON y su forma codificada en URL (lo que un trabajador podría
 * imprimir sin querer en una traza o en una petición fallida).
 */

export const MASCARA = "••••";
/** Por debajo de este largo un «secreto» taparía media consola (p. ej. «x»): no se busca. */
const MINIMO = 4;

export function variantes(secretos: string[]): string[] {
  const r = new Set<string>();
  for (const s of secretos) {
    if (typeof s !== "string" || s.length < MINIMO) continue;
    r.add(s);
    const json = JSON.stringify(s).slice(1, -1);
    if (json !== s) r.add(json);
    try {
      const url = encodeURIComponent(s);
      if (url !== s) r.add(url);
    } catch {
      /* texto con sustitutos sueltos: no se codifica */
    }
  }
  // Los largos primero: si uno contiene a otro, se tapa entero.
  return [...r].sort((a, b) => b.length - a.length);
}

export function enmascararTexto(texto: string, lista: string[]): string {
  let t = texto;
  for (const v of lista) if (t.includes(v)) t = t.split(v).join(MASCARA);
  return t;
}

/** Una copia del valor (texto, lista u objeto, a cualquier profundidad) sin los secretos. */
export function enmascarar<T>(valor: T, lista: string[]): T {
  if (!lista.length) return valor;
  const ir = (v: unknown, profundidad: number): unknown => {
    if (typeof v === "string") return enmascararTexto(v, lista);
    if (profundidad > 40 || v === null || typeof v !== "object") return v;
    if (Array.isArray(v)) return v.map((x) => ir(x, profundidad + 1));
    const r: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) r[enmascararTexto(k, lista)] = ir(x, profundidad + 1);
    return r;
  };
  return ir(valor, 0) as T;
}
