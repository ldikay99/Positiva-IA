/**
 * El reparto justo del tope global de trabajadores (TRABAJADORES_MAX) entre los flujos.
 *
 * Cada flujo pide lo que puede usar ahora: sus corridas en curso más las que esperan,
 * sin pasar de sus propios límites. El tope se reparte con justicia max-min (water
 * filling): todos reciben por igual hasta que cada uno tiene lo que pide o se acaba el
 * tope; lo que uno no usa pasa a los demás. Si sobra menos de uno por flujo, el que más
 * lleva esperando va primero. Un flujo que inunda su cola no se lleva todo: su cuota es
 * la misma que la de los demás que también tienen trabajo.
 */

export interface Pedido {
  id: string;
  /** Cuántos trabajadores podría usar ahora (en curso + en cola, ya acotado por sus límites). */
  quiere: number;
  /** Desde hace cuánto espera lo más viejo de su cola (ms); 0 si no espera nada. */
  espera: number;
}

export function repartoJusto(tope: number, pedidos: Pedido[]): Map<string, number> {
  const cuota = new Map<string, number>(pedidos.map((p) => [p.id, 0]));
  let quedan = Math.max(0, Math.floor(tope));
  // el que más espera primero: en un empate de restos, a él le toca
  let activos = pedidos.filter((p) => p.quiere > 0).sort((a, b) => b.espera - a.espera || a.id.localeCompare(b.id));
  while (quedan > 0 && activos.length) {
    const parte = Math.max(1, Math.floor(quedan / activos.length));
    for (const p of activos) {
      if (quedan <= 0) break;
      const dar = Math.min(parte, p.quiere - cuota.get(p.id)!, quedan);
      cuota.set(p.id, cuota.get(p.id)! + dar);
      quedan -= dar;
    }
    activos = activos.filter((p) => cuota.get(p.id)! < p.quiere);
  }
  return cuota;
}

/** Lo que el planificador necesita de cada pool (un Motor). */
export interface Pool {
  readonly flujo: string;
  /** Procesos vivos (arrancando, libres, ocupados o retirándose). */
  procesos(): number;
  /** Lo que podría usar ahora, ya acotado por sus límites. */
  quiere(): number;
  espera(): number;
  /** Reparte su cola entre sus trabajadores libres sin pasar de `cuota` ocupados. */
  despachar(cuota: number): void;
  /** Cuántos trabajadores más le faltan para atender su cola dentro de `cuota` (sin contar los que arrancan). */
  falta(cuota: number): number;
  /** Puede lanzar uno más sin pasar de sus límites. */
  puedeLanzar(): boolean;
  lanzar(): void;
  /** Cuántos quiere tener calientes aunque no haya trabajo. */
  calientes(): number;
  /** Retira un trabajador libre (que no tenga trabajo); false si no tiene ninguno libre. */
  retirarLibre(): boolean;
  libres(): number;
}

/**
 * El planificador de la plataforma: un solo tope de procesos para todos los flujos.
 * `equilibrar()` se llama cada vez que algo cambia (llega trabajo, un trabajador queda
 * libre, arranca o sale); se junta en una sola pasada por vuelta del bucle de eventos.
 */
export class Planificador {
  private pools = new Set<Pool>();
  private pendiente = false;
  /** Lo puede cambiar una prueba; en el servidor sale de TRABAJADORES_MAX. */
  tope: () => number;

  constructor(tope: () => number) {
    this.tope = tope;
  }

  registrar(p: Pool): void {
    this.pools.add(p);
  }

  quitar(p: Pool): void {
    this.pools.delete(p);
    this.pedir();
  }

  vivos(): number {
    let n = 0;
    for (const p of this.pools) n += p.procesos();
    return n;
  }

  /** Las cuotas de ahora (para la salud y las pruebas). */
  cuotas(): Map<string, number> {
    return repartoJusto(this.tope(), [...this.pools].map((p) => ({ id: p.flujo, quiere: p.quiere(), espera: p.espera() })));
  }

  pedir(): void {
    if (this.pendiente) return;
    this.pendiente = true;
    queueMicrotask(() => {
      this.pendiente = false;
      this.equilibrar();
    });
  }

  equilibrar(): void {
    const pools = [...this.pools];
    const cuotas = this.cuotas();
    const tope = this.tope();
    // 1. Cada flujo reparte su cola entre sus trabajadores libres, sin pasar de su cuota.
    for (const p of pools) p.despachar(cuotas.get(p.flujo) ?? 0);
    // 2. A los que les faltan trabajadores para su cuota se les lanza, el de más déficit primero.
    let vivos = this.vivos();
    for (;;) {
      const necesitados = pools.filter((p) => p.falta(cuotas.get(p.flujo) ?? 0) > 0 && p.puedeLanzar())
        .sort((a, b) => b.falta(cuotas.get(b.flujo) ?? 0) - a.falta(cuotas.get(a.flujo) ?? 0) || b.espera() - a.espera());
      const p = necesitados[0];
      if (!p) break;
      if (vivos < tope) {
        p.lanzar();
        vivos++;
        continue;
      }
      // 3. Sin lugar: se le pide un trabajador libre al flujo que más tiene por encima de su cuota.
      const donante = pools.filter((d) => d !== p && d.libres() > 0)
        .sort((a, b) => (b.procesos() - (cuotas.get(b.flujo) ?? 0)) - (a.procesos() - (cuotas.get(a.flujo) ?? 0)))[0];
      if (!donante || !donante.retirarLibre()) break;
      // al salir ese proceso, la siguiente pasada lanza el que falta
      break;
    }
    // 4. Con lugar de sobra, los flujos que lo piden quedan con trabajadores calientes.
    for (const p of pools) {
      while (vivos < tope && p.procesos() < p.calientes() && p.puedeLanzar()) {
        p.lanzar();
        vivos++;
      }
    }
  }
}
