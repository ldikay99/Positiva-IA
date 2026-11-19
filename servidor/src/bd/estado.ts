/**
 * La capa de repositorio del estado en PostgreSQL. Cada módulo del servidor (flujos, versiones, estructuras, motor,
 * aprobaciones, aplazadas, programador, chats, configuracion, ia, recursos, compuerta) sigue igual y, con
 * DATABASE_URL, en vez de leer y escribir sus archivos llama a estas funciones. Sin DATABASE_URL nada de esto corre.
 *
 * Todas corren con enOrganizacion: la organización la fija la transacción (SET LOCAL) y la filtra Row Level
 * Security; por eso ninguna consulta de aquí lleva «WHERE organizacion_id = …» (sí la ponen los INSERT, que la
 * política revisa). Lo Personal confidencial y lo Sensible se guarda cifrado con la DEK de la organización.
 */
import { isDeepStrictEqual } from "node:util";
import * as cif from "./cifrado.ts";
import { asegurarParticiones, enOrganizacion, fecha, ms, organizacionActual, type Cliente } from "./conexion.ts";

const ORIGINALES = new Set(["pqrd", "tutelas"]);

/** pqrd y tutelas existen siempre (vienen del código); su fila aparece la primera vez que algo suyo se guarda. */
async function asegurarOriginal(c: Cliente, id: string): Promise<void> {
  if (!ORIGINALES.has(id)) return;
  await c.query(
    `INSERT INTO vera.flujos (organizacion_id, id, tipo, base, original, sensible) VALUES ($1, $2, 'codigo', $2, true, true)
     ON CONFLICT (organizacion_id, id) DO NOTHING`, [organizacionActual(), id]);
}

/**
 * La DEK del contenido de una ejecución (y de su aprobación o aplazada): la «sensible» del mes si el flujo es
 * sensible (pqrd, tutelas, sus copias y los declarativos marcados), si no la general. Así, destruir la del mes al
 * vencer su retención corta solo lo sensible (docs/DATOS_SENSIBLES.md › Cifrado › Por periodo).
 */
async function llaveDeContenido(c: Cliente, flujo: string, creada: number): Promise<cif.Llave> {
  const org = organizacionActual();
  const sensible = ORIGINALES.has(flujo) || (await c.query<{ sensible: boolean }>("SELECT sensible FROM vera.flujos WHERE id = $1", [flujo])).rows[0]?.sensible === true;
  return sensible ? cif.llave(c, org, "sensible", cif.periodoDe(creada)) : cif.llave(c, org, "general");
}

const mesesListos = new Set<string>();

/** La partición del mes de una ejecución (una vez por mes y proceso). */
async function asegurarMes(c: Cliente, creada: number): Promise<void> {
  const p = cif.periodoDe(creada);
  if (mesesListos.has(p)) return;
  await asegurarParticiones(c, new Date(creada), 1);
  mesesListos.add(p);
}

/** Tras una transacción revertida: lo que se creó en ella (llaves, particiones) pudo no quedar. */
export function olvidarCaches(): void {
  mesesListos.clear();
  cif.olvidarLlaves();
  mensajesEscritos.clear();
}

const sinIndefinidos = <T extends Record<string, unknown>>(o: T): T => {
  for (const k of Object.keys(o)) if (o[k] === undefined || o[k] === null) delete o[k];
  return o;
};

// =====================================================================================================  flujos

type Nodo = { id?: string; tipo?: string; config?: Record<string, unknown> | null };

/** El token de cada webhook sale de la definición (Interno) y va cifrado aparte. */
function separarSecretos(definicion: unknown): { definicion: Record<string, unknown>; secretos: Record<string, string> | null } {
  const d = structuredClone(definicion) as Record<string, unknown> & { nodos?: Nodo[] };
  const secretos: Record<string, string> = {};
  for (const n of d.nodos ?? []) {
    if (n?.tipo === "webhook" && n.config && typeof n.config.secreto === "string" && n.id) {
      secretos[n.id] = n.config.secreto;
      delete n.config.secreto;
    }
  }
  return { definicion: d, secretos: Object.keys(secretos).length ? secretos : null };
}

function unirSecretos(definicion: Record<string, unknown>, secretos: Record<string, string> | null): Record<string, unknown> {
  if (!secretos) return definicion;
  for (const n of (definicion.nodos as Nodo[] | undefined) ?? []) {
    if (n?.tipo === "webhook" && n.id && secretos[n.id] !== undefined) n.config = { ...(n.config ?? {}), secreto: secretos[n.id] };
  }
  return definicion;
}

interface FilaVersion { version: number; definicion: Record<string, unknown>; extra: Record<string, unknown> | null; secretos: Buffer | null; secretos_llave: string | null; nota: string | null; guardada: Date }

async function definicionDeFila(c: Cliente, flujo: string, clase: string, f: FilaVersion): Promise<Record<string, unknown>> {
  const org = organizacionActual();
  const s = f.secretos && f.secretos_llave
    ? await cif.abrirCon<Record<string, string>>(c, org, f.secretos_llave, cif.aad(org, "flujo_versiones", `${flujo}:${clase}:${f.version}`), f.secretos) : null;
  return unirSecretos(f.definicion, s);
}

async function escribirVersion(c: Cliente, flujo: string, clase: "definicion" | "estructura", version: number, definicion: unknown,
  extra: Record<string, unknown> | null, nota: string | undefined, guardada: number): Promise<void> {
  const org = organizacionActual();
  const { definicion: limpia, secretos } = separarSecretos(definicion);
  let sellado: Buffer | null = null;
  let llave: string | null = null;
  if (secretos) {
    const l = await cif.llave(c, org, "general");
    sellado = cif.sellar(l, cif.aad(org, "flujo_versiones", `${flujo}:${clase}:${version}`), secretos);
    llave = l.id;
  }
  await c.query(
    `INSERT INTO vera.flujo_versiones (organizacion_id, flujo_id, clase, version, definicion, extra, secretos, secretos_llave, nota, guardada)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
     ON CONFLICT (organizacion_id, flujo_id, clase, version) DO NOTHING`,
    [org, flujo, clase, version, JSON.stringify(limpia), extra ? JSON.stringify(extra) : null, sellado, llave, nota ?? null, new Date(guardada)]);
}

export interface Guardado { definicion: unknown; version: number; actualizado: number; nota?: string }
export interface CopiaGuardada { id: string; base: string; nombre: string; descripcion: string; copia_de: string; creado: number; actualizado: number }

export const flujos = {
  /** Lo que flujos.cargar() leía de flujos/ y flujos_codigo/. */
  cargar: () => enOrganizacion(async (c) => {
    const filas = await c.query<{ id: string; tipo: string; base: string | null; original: boolean; copia_de: string | null; nombre: string | null; descripcion: string | null;
      version_actual: number | null; nota: string | null; creado: Date; actualizado: Date }>("SELECT * FROM vera.flujos ORDER BY id");
    const declarativos: Guardado[] = [];
    const copias: CopiaGuardada[] = [];
    const originales: Record<string, { nombre?: string; descripcion?: string }> = {};
    for (const f of filas.rows) {
      if (f.tipo === "declarativo" && f.version_actual) {
        const v = await c.query<FilaVersion>("SELECT * FROM vera.flujo_versiones WHERE flujo_id = $1 AND clase = 'definicion' AND version = $2", [f.id, f.version_actual]);
        if (!v.rows[0]) continue;
        declarativos.push(sinIndefinidos({ definicion: await definicionDeFila(c, f.id, "definicion", v.rows[0]), version: f.version_actual, actualizado: f.actualizado.getTime(), nota: f.nota ?? undefined }));
      } else if (f.tipo === "codigo" && f.original) {
        if (f.nombre || f.descripcion !== null) originales[f.id] = sinIndefinidos({ nombre: f.nombre ?? undefined, descripcion: f.descripcion ?? undefined });
      } else if (f.tipo === "codigo" && f.base) {
        copias.push({ id: f.id, base: f.base, nombre: f.nombre ?? f.id, descripcion: f.descripcion ?? "", copia_de: f.copia_de ?? f.base, creado: f.creado.getTime(), actualizado: f.actualizado.getTime() });
      }
    }
    return { declarativos, copias, originales };
  }),

  /** La versión vigente de un declarativo (flujos/<id>.json); la anterior ya quedó como su propia fila. */
  guardarDeclarativo: (g: Guardado & { definicion: { id: string; nombre: string; descripcion?: string; [k: string]: unknown } }) => enOrganizacion(async (c) => {
    const d = g.definicion;
    await c.query(
      `INSERT INTO vera.flujos (organizacion_id, id, tipo, nombre, descripcion, version_actual, nota, actualizado)
       VALUES ($1, $2, 'declarativo', $3, $4, $5, $6, $7)
       ON CONFLICT (organizacion_id, id) DO UPDATE SET nombre = EXCLUDED.nombre, descripcion = EXCLUDED.descripcion,
         version_actual = EXCLUDED.version_actual, nota = EXCLUDED.nota, actualizado = EXCLUDED.actualizado`,
      [organizacionActual(), d.id, d.nombre, d.descripcion ?? "", g.version, g.nota ?? null, new Date(g.actualizado)]);
    await escribirVersion(c, d.id, "definicion", g.version, d, null, g.nota, g.actualizado);
  }),

  /** Una versión anterior que no estaba (solo la usa la migración de datos: flujos/versiones/<id>.v<N>.json). */
  guardarVersionAnterior: (id: string, g: Guardado) => enOrganizacion((c) => escribirVersion(c, id, "definicion", g.version, g.definicion, null, g.nota, g.actualizado)),

  leerVersion: (id: string, version: number) => enOrganizacion(async (c) => {
    const v = await c.query<FilaVersion>("SELECT * FROM vera.flujo_versiones WHERE flujo_id = $1 AND clase = 'definicion' AND version = $2", [id, version]);
    const f = v.rows[0];
    return f ? sinIndefinidos({ definicion: await definicionDeFila(c, id, "definicion", f) as unknown, version: f.version, actualizado: f.guardada.getTime(), nota: f.nota ?? undefined }) as Guardado : null;
  }),

  versiones: (id: string) => enOrganizacion(async (c) =>
    (await c.query<{ version: number; guardada: Date; nota: string | null }>(
      "SELECT version, guardada, nota FROM vera.flujo_versiones WHERE flujo_id = $1 AND clase = 'definicion' ORDER BY version", [id])).rows
      .map((r) => sinIndefinidos({ version: r.version, actualizado: r.guardada.getTime(), nota: r.nota ?? undefined }))),

  /** Borra el flujo y, en cascada, sus versiones, su historial y su programación. */
  borrar: (id: string) => enOrganizacion(async (c) => {
    if (ORIGINALES.has(id)) return;
    await c.query("DELETE FROM vera.flujos WHERE id = $1", [id]);
  }),

  guardarCopia: (g: CopiaGuardada) => enOrganizacion(async (c) => {
    await c.query(
      `INSERT INTO vera.flujos (organizacion_id, id, tipo, base, copia_de, nombre, descripcion, creado, actualizado, sensible)
       VALUES ($1, $2, 'codigo', $3, $4, $5, $6, $7, $8, true)
       ON CONFLICT (organizacion_id, id) DO UPDATE SET nombre = EXCLUDED.nombre, descripcion = EXCLUDED.descripcion, actualizado = EXCLUDED.actualizado`,
      [organizacionActual(), g.id, g.base, g.copia_de, g.nombre, g.descripcion, new Date(g.creado), new Date(g.actualizado)]);
  }),

  /** El nombre y la descripción propios de pqrd y tutelas (flujos_codigo/_originales.json). */
  guardarOriginales: (o: Record<string, { nombre?: string; descripcion?: string }>) => enOrganizacion(async (c) => {
    for (const id of ORIGINALES) {
      await asegurarOriginal(c, id);
      await c.query("UPDATE vera.flujos SET nombre = $2, descripcion = $3, actualizado = now() WHERE id = $1", [id, o[id]?.nombre ?? null, o[id]?.descripcion ?? null]);
    }
  }),
};

// =====================================================================================================  estructuras (flujos de código)

export interface EstructuraGuardada { definicion: unknown; version: number; actualizado: number; nota?: string; tramos: unknown[]; intercambio: unknown; apagadas: string[] }

const aEstructura = (definicion: unknown, f: FilaVersion): EstructuraGuardada => sinIndefinidos({
  definicion, version: f.version, actualizado: f.guardada.getTime(), nota: f.nota ?? undefined,
  tramos: (f.extra?.tramos as unknown[]) ?? [], intercambio: f.extra?.intercambio ?? null, apagadas: (f.extra?.apagadas as string[]) ?? [],
}) as EstructuraGuardada;

export const estructuras = {
  /** Las estructuras vigentes ({flujo: guardada}): flujos_codigo/<id>.estructura.json. */
  cargar: () => enOrganizacion(async (c) => {
    const r: Record<string, EstructuraGuardada> = {};
    const filas = await c.query<FilaVersion & { flujo_id: string }>(
      `SELECT v.* FROM vera.flujos f JOIN vera.flujo_versiones v ON v.flujo_id = f.id AND v.clase = 'estructura' AND v.version = f.estructura_actual`);
    for (const f of filas.rows) r[f.flujo_id] = aEstructura(await definicionDeFila(c, f.flujo_id, "estructura", f), f);
    return r;
  }),

  /** Guarda una versión de la estructura; con `vigente`, además queda como la del flujo. */
  guardar: (id: string, g: EstructuraGuardada, vigente: boolean) => enOrganizacion(async (c) => {
    await asegurarOriginal(c, id);
    await escribirVersion(c, id, "estructura", g.version, g.definicion, { tramos: g.tramos, intercambio: g.intercambio, apagadas: g.apagadas }, g.nota, g.actualizado);
    if (vigente) await c.query("UPDATE vera.flujos SET estructura_actual = $2 WHERE id = $1", [id, g.version]);
  }),

  /** Vuelve a la del código (la editada queda como versión). */
  quitarVigente: (id: string) => enOrganizacion(async (c) => {
    await c.query("UPDATE vera.flujos SET estructura_actual = NULL WHERE id = $1", [id]);
  }),

  leerVersion: (id: string, version: number) => enOrganizacion(async (c) => {
    const f = (await c.query<FilaVersion>("SELECT * FROM vera.flujo_versiones WHERE flujo_id = $1 AND clase = 'estructura' AND version = $2", [id, version])).rows[0];
    return f ? aEstructura(await definicionDeFila(c, id, "estructura", f), f) : null;
  }),

  /** Las versiones guardadas: [{version, actualizado, nota}]. */
  versiones: (id: string) => enOrganizacion(async (c) =>
    (await c.query<{ version: number; guardada: Date; nota: string | null }>(
      "SELECT version, guardada, nota FROM vera.flujo_versiones WHERE flujo_id = $1 AND clase = 'estructura' ORDER BY version", [id])).rows
      .map((r) => sinIndefinidos({ version: r.version, actualizado: r.guardada.getTime(), nota: r.nota ?? undefined }))),

  borrarTodo: (id: string) => enOrganizacion(async (c) => {
    await c.query("UPDATE vera.flujos SET estructura_actual = NULL WHERE id = $1", [id]);
    await c.query("DELETE FROM vera.flujo_versiones WHERE flujo_id = $1 AND clase = 'estructura'", [id]);
  }),
};

// =====================================================================================================  metas de versión (flujos_historial)

export type Meta = Record<string, unknown> & { autor?: string; fecha?: number; resumen?: string; pedido?: string; pedido_id?: string; punto?: string; etiqueta?: string;
  restaurada_de?: number; por?: string; deshecha?: number; del_codigo?: boolean };

export const metas = {
  leer: (id: string) => enOrganizacion(async (c) => {
    const org = organizacionActual();
    const r: Record<string, Meta> = {};
    const filas = await c.query<{ version: number; autor: string | null; fecha: Date | null; resumen: string | null; etiqueta: string | null; restaurada_de: number | null;
      por: string | null; deshecha: number | null; del_codigo: boolean; pedido_id: string | null; textos: Buffer | null; textos_llave: string | null }>(
      "SELECT * FROM vera.metas_version WHERE flujo_id = $1 ORDER BY version", [id]);
    for (const f of filas.rows) {
      const t = f.textos && f.textos_llave ? await cif.abrirCon<{ pedido?: string; punto?: string }>(c, org, f.textos_llave, cif.aad(org, "metas_version", `${id}:${f.version}`), f.textos) : {};
      r[String(f.version)] = sinIndefinidos({
        autor: f.autor ?? undefined, fecha: ms(f.fecha), resumen: f.resumen ?? undefined, pedido: t.pedido, pedido_id: f.pedido_id ?? undefined, punto: t.punto,
        etiqueta: f.etiqueta ?? undefined, restaurada_de: f.restaurada_de ?? undefined, por: f.por ?? undefined, deshecha: f.deshecha ?? undefined,
        ...(f.del_codigo ? { del_codigo: true } : {}),
      });
    }
    return r;
  }),

  /** Reemplaza el historial del flujo (lo que versiones.ts escribía entero en flujos_historial/<id>.json). */
  escribir: (id: string, versiones: Record<string, Meta>) => enOrganizacion(async (c) => {
    const org = organizacionActual();
    await asegurarOriginal(c, id);
    await c.query("DELETE FROM vera.metas_version WHERE flujo_id = $1", [id]);
    for (const [k, m] of Object.entries(versiones)) {
      const v = Number(k);
      if (!Number.isInteger(v) || v < 0) continue;
      let textos: Buffer | null = null;
      let llave: string | null = null;
      if (m.pedido || m.punto) {
        const l = await cif.llave(c, org, "general");
        textos = cif.sellar(l, cif.aad(org, "metas_version", `${id}:${v}`), sinIndefinidos({ pedido: m.pedido, punto: m.punto }));
        llave = l.id;
      }
      await c.query(
        `INSERT INTO vera.metas_version (organizacion_id, flujo_id, version, autor, fecha, resumen, etiqueta, restaurada_de, por, deshecha, del_codigo, pedido_id, textos, textos_llave)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)`,
        [org, id, v, m.autor ?? null, fecha(m.fecha), m.resumen ?? null, m.etiqueta ?? null, m.restaurada_de ?? null, m.por ?? null, m.deshecha ?? null,
          m.del_codigo === true, m.pedido_id ?? null, textos, llave]);
    }
  }),

  borrar: (id: string) => enOrganizacion(async (c) => {
    await c.query("DELETE FROM vera.metas_version WHERE flujo_id = $1", [id]);
  }),
};

// =====================================================================================================  ejecuciones

export interface EventoBd { tipo: string; t: number; [k: string]: unknown }
export interface CorridaBd {
  id: string; flujo: string; caso: string; lote?: string; estado: string; creada: number; inicio?: number; fin?: number; origen?: string;
  eventos: EventoBd[]; resultado?: EventoBd; [k: string]: unknown;
}

const TRAMO = 500;

export const corridas = {
  /** Guarda una ejecución terminada con sus eventos (una vez: si ya está, no la repite). Devuelve si la guardó. */
  guardar: (corrida: CorridaBd) => enOrganizacion(async (c) => {
    const org = organizacionActual();
    const { id, flujo, caso, lote, estado, creada, inicio, fin, origen, eventos = [], resultado, ...resto } = corrida;
    await asegurarMes(c, creada);
    const l = await llaveDeContenido(c, flujo, creada);
    // El resultado ya va entre los eventos: se guarda una vez y al leer se vuelve a enlazar.
    let enEvento = -1;
    if (resultado) for (let i = eventos.length - 1; i >= 0; i--) if (eventos[i] === resultado || (eventos[i]?.tipo === "resultado" && isDeepStrictEqual(eventos[i], resultado))) { enEvento = i; break; }
    const contenido = { ...resto, ...(resultado && enEvento < 0 ? { resultado } : {}), ...(enEvento >= 0 ? { _resultado_en: enEvento } : {}) };
    const r = await c.query(
      `INSERT INTO vera.corridas (organizacion_id, id, creada, flujo_id, caso, lote, estado, origen, inicio, fin, accion, n_eventos, contenido, llave_id)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14) ON CONFLICT DO NOTHING`,
      [org, id, new Date(creada), flujo, caso ?? "", lote ?? null, estado, origen ?? null, fecha(inicio), fecha(fin),
        typeof resultado?.accion === "string" ? resultado.accion : null, eventos.length, cif.sellar(l, cif.aad(org, "corridas", id), contenido), l.id]);
    if (!r.rowCount) return false;
    for (let tramo = 0; tramo * TRAMO < eventos.length; tramo++) {
      const parte = eventos.slice(tramo * TRAMO, (tramo + 1) * TRAMO);
      const ts = parte.map((e) => e.t).filter((t) => typeof t === "number" && Number.isFinite(t));
      await c.query(
        `INSERT INTO vera.eventos_corrida (organizacion_id, corrida_id, corrida_creada, tramo, n, desde_t, hasta_t, tipos, contenido, llave_id)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)`,
        [org, id, new Date(creada), tramo, parte.length, ts.length ? new Date(Math.min(...ts) * 1000) : null, ts.length ? new Date(Math.max(...ts) * 1000) : null,
          [...new Set(parte.map((e) => String(e.tipo)))], cif.sellar(l, cif.aad(org, "eventos_corrida", `${id}:${tramo}`), parte), l.id]);
    }
    return true;
  }),

  /** Las `maximo` más recientes de un flujo, de la más vieja a la más nueva, con sus eventos. */
  cargar: (flujo: string, maximo = 500) => enOrganizacion(async (c) => {
    const org = organizacionActual();
    const filas = (await c.query<{ id: string; creada: Date; flujo_id: string; caso: string; lote: string | null; estado: string; origen: string | null;
      inicio: Date | null; fin: Date | null; contenido: Buffer | null; llave_id: string | null }>(
      "SELECT * FROM vera.corridas WHERE flujo_id = $1 ORDER BY creada DESC, id DESC LIMIT $2", [flujo, maximo])).rows.reverse();
    if (!filas.length) return [];
    const eventos = await c.query<{ corrida_id: string; tramo: number; contenido: Buffer; llave_id: string }>(
      `SELECT corrida_id, tramo, contenido, llave_id FROM vera.eventos_corrida
        WHERE corrida_id = ANY($1) AND corrida_creada BETWEEN $2 AND $3 ORDER BY corrida_id, tramo`,
      [filas.map((f) => f.id), filas[0]!.creada, filas.at(-1)!.creada]);
    const porCorrida = new Map<string, EventoBd[]>();
    for (const e of eventos.rows) {
      const lista = porCorrida.get(e.corrida_id) ?? [];
      lista.push(...await cif.abrirCon<EventoBd[]>(c, org, e.llave_id, cif.aad(org, "eventos_corrida", `${e.corrida_id}:${e.tramo}`), e.contenido));
      porCorrida.set(e.corrida_id, lista);
    }
    const r: CorridaBd[] = [];
    for (const f of filas) {
      // Sin contenido: la retención ya lo quitó (quedan los metadatos).
      const { _resultado_en, resultado, ...resto } = f.contenido && f.llave_id
        ? await cif.abrirCon<Record<string, unknown> & { _resultado_en?: number; resultado?: EventoBd }>(c, org, f.llave_id, cif.aad(org, "corridas", f.id), f.contenido)
        : { configuracion: {}, contenido_retirado: true } as Record<string, unknown> & { _resultado_en?: number; resultado?: EventoBd };
      const ev = porCorrida.get(f.id) ?? [];
      const res = typeof _resultado_en === "number" ? ev[_resultado_en] : resultado;
      r.push(sinIndefinidos({
        ...resto, id: f.id, flujo: f.flujo_id, caso: f.caso, lote: f.lote ?? undefined, estado: f.estado, creada: f.creada.getTime(),
        inicio: ms(f.inicio), fin: ms(f.fin), origen: f.origen ?? undefined, eventos: ev, resultado: res,
      }) as CorridaBd);
    }
    return r;
  }),

  borrarDe: (flujo: string) => enOrganizacion(async (c) => (await c.query("DELETE FROM vera.corridas WHERE flujo_id = $1", [flujo])).rowCount ?? 0),

  contarDe: (flujo: string) => enOrganizacion(async (c) => Number((await c.query<{ n: string }>("SELECT count(*) AS n FROM vera.corridas WHERE flujo_id = $1", [flujo])).rows[0]?.n ?? 0)),
};

// =====================================================================================================  aprobaciones

export type AprobacionBd = Record<string, unknown> & { id: string; flujo: string; corrida: string; estado: string; creada: number; reanudada_en?: number };

const CLARO_APROBACION = new Set(["id", "flujo", "corrida", "caso", "nodo", "estado", "creada", "vence", "al_vencer", "decision", "por", "resuelta", "reanudada", "reanudada_en"]);

interface FilaAprobacion { id: string; flujo_id: string; corrida_id: string; caso: string | null; nodo: string | null; estado: string; creada: Date; vence: Date | null;
  al_vencer: string | null; decision: string | null; por: string | null; resuelta: Date | null; reanudada: string | null; reanudada_en: Date | null;
  contenido: Buffer; llave_id: string }

async function aAprobacion(c: Cliente, f: FilaAprobacion): Promise<AprobacionBd> {
  const org = organizacionActual();
  const resto = await cif.abrirCon<Record<string, unknown>>(c, org, f.llave_id, cif.aad(org, "aprobaciones", f.id), f.contenido);
  return sinIndefinidos({
    ...resto, id: f.id, flujo: f.flujo_id, corrida: f.corrida_id, caso: f.caso ?? undefined, nodo: f.nodo ?? undefined, estado: f.estado, creada: f.creada.getTime(),
    vence: f.vence ? f.vence.getTime() : (resto.vence === null ? null : undefined), al_vencer: f.al_vencer ?? undefined, decision: f.decision ?? undefined,
    por: f.por ?? undefined, resuelta: ms(f.resuelta), reanudada: f.reanudada ?? undefined, reanudada_en: ms(f.reanudada_en),
  }) as AprobacionBd;
}

async function escribirAprobacion(c: Cliente, a: AprobacionBd, estadoCorrida?: unknown): Promise<void> {
  const org = organizacionActual();
  const l = await llaveDeContenido(c, a.flujo, a.creada);
  const resto: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(a)) if (!CLARO_APROBACION.has(k)) resto[k] = v;
  if (a.vence === null) resto.vence = null;
  const valores = [org, a.id, a.flujo, a.corrida ?? "", a.caso ?? null, a.nodo ?? null, a.estado, new Date(a.creada), fecha(a.vence as number | null | undefined),
    a.al_vencer ?? null, a.decision ?? null, a.por ?? null, fecha(a.resuelta as number | undefined), a.reanudada ?? null,
    cif.sellar(l, cif.aad(org, "aprobaciones", a.id), resto), l.id];
  if (estadoCorrida !== undefined) {
    await c.query(
      `INSERT INTO vera.aprobaciones (organizacion_id, id, flujo_id, corrida_id, caso, nodo, estado, creada, vence, al_vencer, decision, por, resuelta, reanudada, contenido, llave_id, estado_corrida)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17) ON CONFLICT DO NOTHING`,
      [...valores, cif.sellar(l, cif.aad(org, "aprobaciones.estado", a.id), estadoCorrida)]);
    return;
  }
  await c.query(
    `INSERT INTO vera.aprobaciones (organizacion_id, id, flujo_id, corrida_id, caso, nodo, estado, creada, vence, al_vencer, decision, por, resuelta, reanudada, contenido, llave_id)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
     ON CONFLICT (organizacion_id, id) DO UPDATE SET estado = EXCLUDED.estado, vence = EXCLUDED.vence, al_vencer = EXCLUDED.al_vencer, decision = EXCLUDED.decision,
       por = EXCLUDED.por, resuelta = EXCLUDED.resuelta, reanudada = EXCLUDED.reanudada, contenido = EXCLUDED.contenido, llave_id = EXCLUDED.llave_id`, valores);
}

export const aprobaciones = {
  leer: (id: string) => enOrganizacion(async (c) => {
    const f = (await c.query<FilaAprobacion>("SELECT * FROM vera.aprobaciones WHERE id = $1", [id])).rows[0];
    return f ? aAprobacion(c, f) : null;
  }),

  listar: (filtro: { flujo?: string; estado?: string } = {}) => enOrganizacion(async (c) => {
    const filas = await c.query<FilaAprobacion>(
      "SELECT * FROM vera.aprobaciones WHERE ($1::text IS NULL OR flujo_id = $1) AND ($2::text IS NULL OR estado = $2)", [filtro.flujo ?? null, filtro.estado ?? null]);
    const r: AprobacionBd[] = [];
    for (const f of filas.rows) r.push(await aAprobacion(c, f));
    return r;
  }),

  /** La que escribe el servidor (decisión, corrida encolada). */
  escribir: (a: AprobacionBd) => enOrganizacion((c) => escribirAprobacion(c, a)),

  /** La que deja el motor al pausar, con el estado de la ejecución (no se pisa si ya estaba). */
  nueva: (a: AprobacionBd, estadoCorrida: unknown) => enOrganizacion((c) => escribirAprobacion(c, a, estadoCorrida ?? {})),

  /** El estado guardado de la ejecución en pausa (null si ya se reanudó y se borró). */
  leerEstado: (id: string) => enOrganizacion(async (c) => {
    const org = organizacionActual();
    const f = (await c.query<{ estado_corrida: Buffer | null; llave_id: string }>("SELECT estado_corrida, llave_id FROM vera.aprobaciones WHERE id = $1", [id])).rows[0];
    return f?.estado_corrida ? cif.abrirCon<Record<string, unknown>>(c, org, f.llave_id, cif.aad(org, "aprobaciones.estado", id), f.estado_corrida) : null;
  }),

  /**
   * La marca .reanudada: la primera corrida que la sigue de verdad se queda con ella (una sola vez, también entre
   * instancias) y el estado de la ejecución se borra. Devuelve la corrida que la tiene.
   */
  marcarReanudada: (id: string, corrida: string) => enOrganizacion(async (c) => {
    const r = await c.query<{ reanudada: string }>(
      `UPDATE vera.aprobaciones SET reanudada = $2, reanudada_en = now(), estado_corrida = NULL
        WHERE id = $1 AND reanudada_en IS NULL AND estado <> 'pendiente' RETURNING reanudada`, [id, corrida]);
    if (r.rows[0]) return { ok: true, corrida };
    const x = (await c.query<{ reanudada: string | null }>("SELECT reanudada FROM vera.aprobaciones WHERE id = $1", [id])).rows[0];
    return { ok: false, corrida: x?.reanudada ?? null };
  }),

  borrarDe: (flujo: string) => enOrganizacion(async (c) => (await c.query("DELETE FROM vera.aprobaciones WHERE flujo_id = $1", [flujo])).rowCount ?? 0),
};

// =====================================================================================================  aplazadas

export type AplazadaBd = Record<string, unknown> & { id: string; flujo: string; corrida: string; cuando: number; intento: number; creada: number };
const CLARO_APLAZADA = new Set(["id", "flujo", "corrida", "caso", "nodo", "cuando", "intento", "creada", "encolada", "encolada_t"]);

interface FilaAplazada { id: string; flujo_id: string; corrida_id: string; caso: string | null; nodo: string | null; cuando: Date; intento: number; creada: Date;
  encolada: string | null; encolada_t: Date | null; contenido: Buffer; llave_id: string }

async function aAplazada(c: Cliente, f: FilaAplazada): Promise<AplazadaBd> {
  const org = organizacionActual();
  const resto = await cif.abrirCon<Record<string, unknown>>(c, org, f.llave_id, cif.aad(org, "aplazadas", f.id), f.contenido);
  return sinIndefinidos({
    ...resto, id: f.id, flujo: f.flujo_id, corrida: f.corrida_id, caso: f.caso ?? undefined, nodo: f.nodo ?? undefined, cuando: f.cuando.getTime(),
    intento: f.intento, creada: f.creada.getTime(), encolada: f.encolada ?? undefined, encolada_t: ms(f.encolada_t),
  }) as AplazadaBd;
}

export const aplazadas = {
  leer: (id: string) => enOrganizacion(async (c) => {
    const f = (await c.query<FilaAplazada>("SELECT * FROM vera.aplazadas WHERE id = $1", [id])).rows[0];
    return f ? aAplazada(c, f) : null;
  }),

  listar: (flujo?: string) => enOrganizacion(async (c) => {
    const filas = await c.query<FilaAplazada>("SELECT * FROM vera.aplazadas WHERE ($1::text IS NULL OR flujo_id = $1) ORDER BY cuando, id", [flujo ?? null]);
    const r: AplazadaBd[] = [];
    for (const f of filas.rows) r.push(await aAplazada(c, f));
    return r;
  }),

  escribir: (a: AplazadaBd) => enOrganizacion(async (c) => {
    const org = organizacionActual();
    const l = await llaveDeContenido(c, a.flujo, a.creada);
    const resto: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(a)) if (!CLARO_APLAZADA.has(k)) resto[k] = v;
    await c.query(
      `INSERT INTO vera.aplazadas (organizacion_id, id, flujo_id, corrida_id, caso, nodo, cuando, intento, creada, encolada, encolada_t, contenido, llave_id)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
       ON CONFLICT (organizacion_id, id) DO UPDATE SET cuando = EXCLUDED.cuando, intento = EXCLUDED.intento, encolada = EXCLUDED.encolada,
         encolada_t = EXCLUDED.encolada_t, contenido = EXCLUDED.contenido, llave_id = EXCLUDED.llave_id`,
      [org, a.id, a.flujo, a.corrida ?? "", a.caso ?? null, a.nodo ?? null, new Date(a.cuando), a.intento ?? 0, new Date(a.creada), a.encolada ?? null,
        fecha(a.encolada_t as number | undefined), cif.sellar(l, cif.aad(org, "aplazadas", a.id), resto), l.id]);
  }),

  borrar: (id: string) => enOrganizacion(async (c) => (await c.query("DELETE FROM vera.aplazadas WHERE id = $1", [id])).rowCount ?? 0),
};

// =====================================================================================================  programaciones

export interface ProgramacionBd { cron: string; zona: string; activo: boolean; n: number; cambiado?: number }

export const programacion = {
  leer: () => enOrganizacion(async (c) => {
    const r: Record<string, ProgramacionBd> = {};
    for (const f of (await c.query<{ flujo_id: string; cron: string; zona: string; activo: boolean; n: number; cambiado: Date | null }>("SELECT * FROM vera.programaciones")).rows)
      r[f.flujo_id] = sinIndefinidos({ cron: f.cron, zona: f.zona, activo: f.activo, n: f.n, cambiado: ms(f.cambiado) });
    return r;
  }),

  poner: (flujo: string, p: ProgramacionBd) => enOrganizacion(async (c) => {
    await asegurarOriginal(c, flujo);
    await c.query(
      `INSERT INTO vera.programaciones (organizacion_id, flujo_id, cron, zona, activo, n, cambiado) VALUES ($1, $2, $3, $4, $5, $6, $7)
       ON CONFLICT (organizacion_id, flujo_id) DO UPDATE SET cron = EXCLUDED.cron, zona = EXCLUDED.zona, activo = EXCLUDED.activo, n = EXCLUDED.n, cambiado = EXCLUDED.cambiado`,
      [organizacionActual(), flujo, p.cron, p.zona, p.activo, p.n, fecha(p.cambiado)]);
  }),

  quitar: (flujo: string) => enOrganizacion(async (c) => {
    await c.query("DELETE FROM vera.programaciones WHERE flujo_id = $1", [flujo]);
  }),

  /** programador.json: {clave: ms}. */
  marcas: () => enOrganizacion(async (c) => {
    const r: Record<string, number> = {};
    for (const f of (await c.query<{ clave: string; valor: string }>("SELECT clave, valor FROM vera.marcas_programador")).rows) r[f.clave] = Number(f.valor);
    return r;
  }),

  escribirMarcas: (marcas: Record<string, number>) => enOrganizacion(async (c) => {
    const claves = Object.keys(marcas).filter((k) => Number.isFinite(marcas[k]));
    await c.query("DELETE FROM vera.marcas_programador WHERE NOT (clave = ANY($1))", [claves]);
    if (claves.length)
      await c.query(
        `INSERT INTO vera.marcas_programador (organizacion_id, clave, valor) SELECT $1, k, v FROM unnest($2::text[], $3::bigint[]) AS x(k, v)
         ON CONFLICT (organizacion_id, clave) DO UPDATE SET valor = EXCLUDED.valor`,
        [organizacionActual(), claves, claves.map((k) => Math.round(marcas[k]!))]);
  }),
};

// =====================================================================================================  conversaciones

export type MensajeBd = Record<string, unknown> & { rol: string; texto: string; t: number; voz?: boolean };
export interface ConversacionBd { id: string; titulo: string; flujo: string; creada: number; actualizada: number; mensajes: MensajeBd[] }

/** Lo último que se escribió de cada mensaje (por conversación): solo se vuelve a cifrar lo que cambió. */
const mensajesEscritos = new Map<string, string[]>();

export const chats = {
  listar: () => enOrganizacion(async (c) => {
    const org = organizacionActual();
    const filas = await c.query<{ id: string; flujo_id: string | null; creada: Date; actualizada: Date; n_mensajes: number; contenido: Buffer; llave_id: string }>(
      "SELECT id, flujo_id, creada, actualizada, n_mensajes, contenido, llave_id FROM vera.conversaciones ORDER BY actualizada DESC");
    const r: (Omit<ConversacionBd, "mensajes"> & { n: number; ultimo: string })[] = [];
    for (const f of filas.rows) {
      const x = await cif.abrirCon<{ titulo: string; ultimo: string }>(c, org, f.llave_id, cif.aad(org, "conversaciones", f.id), f.contenido);
      r.push({ id: f.id, titulo: x.titulo, flujo: f.flujo_id ?? "", creada: f.creada.getTime(), actualizada: f.actualizada.getTime(), n: f.n_mensajes, ultimo: x.ultimo ?? "" });
    }
    return r;
  }),

  leer: (id: string) => enOrganizacion(async (c) => {
    const org = organizacionActual();
    const f = (await c.query<{ id: string; flujo_id: string | null; creada: Date; actualizada: Date; contenido: Buffer; llave_id: string }>(
      "SELECT id, flujo_id, creada, actualizada, contenido, llave_id FROM vera.conversaciones WHERE id = $1", [id])).rows[0];
    if (!f) return null;
    const x = await cif.abrirCon<{ titulo: string }>(c, org, f.llave_id, cif.aad(org, "conversaciones", id), f.contenido);
    const mensajes: MensajeBd[] = [];
    for (const m of (await c.query<{ orden: number; contenido: Buffer; llave_id: string }>(
      "SELECT orden, contenido, llave_id FROM vera.mensajes WHERE conversacion_id = $1 ORDER BY orden", [id])).rows)
      mensajes.push(await cif.abrirCon<MensajeBd>(c, org, m.llave_id, cif.aad(org, "mensajes", `${id}:${m.orden}`), m.contenido));
    mensajesEscritos.set(`${org}|${id}`, mensajes.map((m) => JSON.stringify(m)));
    return { id, titulo: x.titulo, flujo: f.flujo_id ?? "", creada: f.creada.getTime(), actualizada: f.actualizada.getTime(), mensajes } as ConversacionBd;
  }),

  guardar: (conv: ConversacionBd) => enOrganizacion(async (c) => {
    const org = organizacionActual();
    const l = await cif.llave(c, org, "general");
    const ultimo = (conv.mensajes.at(-1)?.texto ?? "").slice(0, 90);
    await c.query(
      `INSERT INTO vera.conversaciones (organizacion_id, id, flujo_id, creada, actualizada, n_mensajes, contenido, llave_id) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
       ON CONFLICT (organizacion_id, id) DO UPDATE SET flujo_id = EXCLUDED.flujo_id, actualizada = EXCLUDED.actualizada, n_mensajes = EXCLUDED.n_mensajes,
         contenido = EXCLUDED.contenido, llave_id = EXCLUDED.llave_id`,
      [org, conv.id, conv.flujo || null, new Date(conv.creada), new Date(conv.actualizada), conv.mensajes.length,
        cif.sellar(l, cif.aad(org, "conversaciones", conv.id), { titulo: conv.titulo, ultimo }), l.id]);
    const clave = `${org}|${conv.id}`;
    const antes = mensajesEscritos.get(clave);
    const ahora = conv.mensajes.map((m) => JSON.stringify(m));
    for (let i = 0; i < ahora.length; i++) {
      if (antes && antes[i] === ahora[i]) continue;
      const m = conv.mensajes[i]!;
      await c.query(
        `INSERT INTO vera.mensajes (organizacion_id, conversacion_id, orden, rol, t, voz, contenido, llave_id) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
         ON CONFLICT (organizacion_id, conversacion_id, orden) DO UPDATE SET rol = EXCLUDED.rol, t = EXCLUDED.t, voz = EXCLUDED.voz,
           contenido = EXCLUDED.contenido, llave_id = EXCLUDED.llave_id`,
        [org, conv.id, i, m.rol === "asistente" ? "asistente" : "usuario", new Date(typeof m.t === "number" ? m.t : Date.now()), m.voz === true,
          cif.sellar(l, cif.aad(org, "mensajes", `${conv.id}:${i}`), m), l.id]);
    }
    await c.query("DELETE FROM vera.mensajes WHERE conversacion_id = $1 AND orden >= $2", [conv.id, ahora.length]);
    mensajesEscritos.set(clave, ahora);
  }),

  borrar: (id: string) => enOrganizacion(async (c) => {
    mensajesEscritos.delete(`${organizacionActual()}|${id}`);
    return ((await c.query("DELETE FROM vera.conversaciones WHERE id = $1", [id])).rowCount ?? 0) > 0;
  }),

  /** La ficha de un adjunto (los bytes van al almacén de objetos: en local, el volumen). */
  guardarAdjunto: (conversacion: string, a: { id: string; nombre: string; tipo: string; tamano: number; extension: string; objeto: string; sha256?: Buffer }) =>
    enOrganizacion(async (c) => {
      const org = organizacionActual();
      const l = await cif.llave(c, org, "general");
      await c.query(
        `INSERT INTO vera.adjuntos_conversacion (organizacion_id, conversacion_id, id, tipo, tamano, extension, objeto, sha256, contenido, llave_id)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) ON CONFLICT DO NOTHING`,
        [org, conversacion, a.id, a.tipo.slice(0, 120), a.tamano, a.extension, a.objeto, a.sha256 ?? null,
          cif.sellar(l, cif.aad(org, "adjuntos_conversacion", `${conversacion}:${a.id}`), { nombre: a.nombre }), l.id]);
    }),

  adjunto: (conversacion: string, id: string) => enOrganizacion(async (c) => {
    const org = organizacionActual();
    const f = (await c.query<{ tipo: string; tamano: string; extension: string; objeto: string; contenido: Buffer; llave_id: string }>(
      "SELECT tipo, tamano, extension, objeto, contenido, llave_id FROM vera.adjuntos_conversacion WHERE conversacion_id = $1 AND id = $2", [conversacion, id])).rows[0];
    if (!f) return null;
    const x = await cif.abrirCon<{ nombre: string }>(c, org, f.llave_id, cif.aad(org, "adjuntos_conversacion", `${conversacion}:${id}`), f.contenido);
    return { nombre: x.nombre, tipo: f.tipo, tamano: Number(f.tamano), extension: f.extension, objeto: f.objeto };
  }),
};

// =====================================================================================================  configuración

export type ClaveAjuste = "configuracion" | "ia_global" | "ia_vera" | "recursos" | "compuertas";
export interface CambioBd { t: number; clave: string; antes: unknown; despues: unknown; nota?: string }

export const configuracion = {
  ajuste: <T>(clave: ClaveAjuste) => enOrganizacion(async (c) =>
    ((await c.query<{ valor: T }>("SELECT valor FROM vera.ajustes WHERE clave = $1", [clave])).rows[0]?.valor ?? null) as T | null),

  ponerAjuste: (clave: ClaveAjuste, valor: unknown) => enOrganizacion(async (c) => {
    await c.query(
      `INSERT INTO vera.ajustes (organizacion_id, clave, valor, actualizado) VALUES ($1, $2, $3, now())
       ON CONFLICT (organizacion_id, clave) DO UPDATE SET valor = EXCLUDED.valor, actualizado = now()`,
      [organizacionActual(), clave, JSON.stringify(valor)]);
  }),

  /** historial.jsonl. Con huella (la migración de datos), un renglón ya pasado no se repite. */
  agregarCambios: (cambios: (CambioBd & { huella?: Buffer })[]) => enOrganizacion(async (c) => {
    let n = 0;
    for (const x of cambios) {
      const r = await c.query(
        `INSERT INTO vera.cambios_configuracion (organizacion_id, huella, t, clave, antes, despues, nota) VALUES ($1, $2, $3, $4, $5, $6, $7)
         ON CONFLICT (organizacion_id, huella) WHERE huella IS NOT NULL DO NOTHING`,
        [organizacionActual(), x.huella ?? null, new Date(x.t), x.clave, JSON.stringify(x.antes ?? null), JSON.stringify(x.despues ?? null), x.nota ?? null]);
      n += r.rowCount ?? 0;
    }
    return n;
  }),

  /** Los últimos `limite`, del más nuevo al más viejo. */
  cambios: (limite = 300) => enOrganizacion(async (c) =>
    (await c.query<{ t: Date; clave: string; antes: unknown; despues: unknown; nota: string | null }>(
      "SELECT t, clave, antes, despues, nota FROM vera.cambios_configuracion ORDER BY t DESC, id DESC LIMIT $1", [limite])).rows
      .map((r) => sinIndefinidos({ t: r.t.getTime(), clave: r.clave, antes: r.antes, despues: r.despues, nota: r.nota ?? undefined }) as CambioBd)),

  perfiles: () => enOrganizacion(async (c) =>
    (await c.query<{ nombre: string; descripcion: string; t: Date; configuracion: Record<string, unknown> }>(
      "SELECT nombre, descripcion, t, configuracion FROM vera.perfiles_configuracion")).rows
      .map((r) => ({ nombre: r.nombre, descripcion: r.descripcion, t: r.t.getTime(), configuracion: r.configuracion }))),

  guardarPerfil: (nombre: string, descripcion: string, conf: unknown, t = Date.now()) => enOrganizacion(async (c) => {
    await c.query(
      `INSERT INTO vera.perfiles_configuracion (organizacion_id, nombre, descripcion, t, configuracion) VALUES ($1, $2, $3, $4, $5)
       ON CONFLICT (organizacion_id, nombre) DO UPDATE SET descripcion = EXCLUDED.descripcion, t = EXCLUDED.t, configuracion = EXCLUDED.configuracion`,
      [organizacionActual(), nombre, descripcion.slice(0, 400), new Date(t), JSON.stringify(conf)]);
  }),

  leerPerfil: (nombre: string) => enOrganizacion(async (c) =>
    (await c.query<{ configuracion: unknown }>("SELECT configuracion FROM vera.perfiles_configuracion WHERE nombre = $1", [nombre])).rows[0]?.configuracion ?? null),

  borrarPerfil: (nombre: string) => enOrganizacion(async (c) => ((await c.query("DELETE FROM vera.perfiles_configuracion WHERE nombre = $1", [nombre])).rowCount ?? 0) > 0),
};
