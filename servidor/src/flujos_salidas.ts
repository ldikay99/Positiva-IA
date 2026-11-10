import { z } from "zod";

/**
 * La forma de la config de los nodos de «salidas a la medida» (plan 5d): exportar datos a JSON, XML, CSV,
 * HTML o texto, comprimir en ZIP y llenar una plantilla de la biblioteca de Documentos. La ficha (y la
 * validación que manda) está en `puente/especificacion_salidas.py` y `puente/nodos_salidas.py`; las pruebas
 * comprueban que este zod acepte sus ejemplos y rechace lo mismo que el motor. flujos.ts pasa sus piezas.
 */

export const TIPOS_SALIDAS = ["archivo_exportar", "archivo_comprimir", "documento_plantilla", "documento_marca"] as const;
export type TipoSalidas = (typeof TIPOS_SALIDAS)[number];

export const NOMBRE_SALIDAS: Record<TipoSalidas, string> = {
  archivo_exportar: "Exportar a archivo", archivo_comprimir: "Comprimir en ZIP", documento_plantilla: "Llenar plantilla de documento", documento_marca: "Aplicar marca",
};
export const CATEGORIA_SALIDAS: Record<TipoSalidas, string> = { archivo_exportar: "documentos", archivo_comprimir: "documentos", documento_plantilla: "documentos", documento_marca: "documentos" };

export const FORMATOS_EXPORTAR = ["json", "xml", "csv", "html", "txt", "md"] as const;
export const ID_PLANTILLA_DOCUMENTO = /^[a-z0-9][a-z0-9-]{1,59}$/;
const NOMBRE_XML = /^[A-Za-z_][A-Za-z0-9_.-]{0,60}$/;

export interface PiezasSalidas {
  conf: <T extends z.ZodRawShape>(forma: T) => z.ZodObject<z.ZodRawShape>;
  archivosRef: (que: string) => z.ZodType;
  mapaPlantillas: (que: string) => z.ZodType;
  /** El desde_ia de documento_generar (instrucciones, entradas, proveedor, modelo, credencial, temperatura). */
  desdeIa: z.ZodType;
}

type C = Record<string, unknown>;

/**
 * La config «marca» (logo, banner, marca de agua, colores y fuente) de documento_generar, documento_plantilla y documento_marca.
 * La misma regla que `puente/marca.py` (problemas): las pruebas de pruebas_salidas.py comprueban que rechacen lo mismo.
 */
const plantillaOMas = (r: RegExp, m: string) => z.string({ error: m }).refine((x) => x.includes("{{") || r.test(x.trim()), m);
const hex = (que: string) => plantillaOMas(/^#?[0-9A-Fa-f]{6}$/, `${que}: un color en hexadecimal, p. ej. «#0B4F8A»`);
const num = (que: string, min: number, max: number) => z.union([z.number().min(min).max(max), z.string().regex(/\{\{/)], { error: `${que}: un número entre ${min} y ${max}` });
const imagenRef = (que: string) => z.string({ error: `${que}: la imagen («{{logo}}», un id «a-…» o «plantilla/recurso»)` }).trim().min(1, `${que}: falta la imagen`);
export const marcaZod = z.strictObject({
  logo: z.union([imagenRef("marca.logo"), z.strictObject({
    imagen: imagenRef("marca.logo.imagen"), posicion: z.enum(["izquierda", "centro", "derecha"], { error: "marca.logo.posicion: izquierda, centro o derecha" }).optional(),
    ancho_cm: num("marca.logo.ancho_cm", 0.5, 25).optional(),
  })]).optional(),
  banner: z.union([imagenRef("marca.banner"), z.strictObject({
    imagen: imagenRef("marca.banner.imagen"), posicion: z.enum(["arriba", "abajo", "ambos"], { error: "marca.banner.posicion: arriba, abajo o ambos" }).optional(),
    alto_cm: num("marca.banner.alto_cm", 0.3, 15).optional(),
  })]).optional(),
  marca_agua: z.union([z.string().trim().min(1, "marca.marca_agua está vacío"), z.strictObject({
    imagen: z.string().optional(), texto: z.string().optional(), opacidad: num("marca.marca_agua.opacidad", 0.01, 100).optional(),
    tamano: num("marca.marca_agua.tamano", 0.05, 100).optional(), rotacion: num("marca.marca_agua.rotacion", -360, 360).optional(),
    color: hex("marca.marca_agua.color").optional(),
  }).refine((a) => Boolean(a.imagen?.trim()) !== Boolean(a.texto?.trim()), "marca.marca_agua necesita imagen o texto (uno de los dos)")]).optional(),
  colores: z.strictObject({
    titulos: hex("marca.colores.titulos").optional(), acento: hex("marca.colores.acento").optional(), tabla_encabezado: hex("marca.colores.tabla_encabezado").optional(),
    tabla_texto: hex("marca.colores.tabla_texto").optional(), texto: hex("marca.colores.texto").optional(),
  }, { error: "marca.colores: titulos, acento, tabla_encabezado, tabla_texto o texto" }).optional(),
  fuente: z.string().trim().min(1).max(60, "marca.fuente: el nombre de una fuente").optional(),
}, { error: "marca: logo, banner, marca_agua, colores o fuente" }).refine((m) => ["logo", "banner", "marca_agua", "colores", "fuente"].some((k) => {
  const v = (m as C)[k];
  return v !== undefined && v !== null && v !== "" && !(typeof v === "object" && !Object.keys(v as object).length);
}), "marca está vacía: pon al menos logo, banner, marca_agua, colores o fuente");
const lleno = (x: unknown) => x !== undefined && x !== null && !(typeof x === "string" && !x.trim()) && !(Array.isArray(x) && !x.length);

export function configSalidas(p: PiezasSalidas): Record<TipoSalidas, z.ZodType> {
  const { conf, archivosRef, mapaPlantillas, desdeIa } = p;
  const txt = z.string({ error: "tiene que ser texto (admite plantillas)" }).optional();
  const mal = (ctx: z.RefinementCtx, k: string, m: string) => ctx.addIssue({ code: "custom", path: [k], message: m });
  const desdeIaPlantilla = z.intersection(desdeIa, z.looseObject({
    modo: z.enum(["llenar", "completo"], { error: "desde_ia.modo: llenar o completo" }).optional(),
    variables: z.array(z.string().trim().min(1), { error: "desde_ia.variables: lista de nombres de variables" }).optional(),
  }));
  return {
    archivo_exportar: conf({
      formato: z.enum(FORMATOS_EXPORTAR, { error: `formato: ${FORMATOS_EXPORTAR.join(", ")}` }), de: txt, contenido: txt,
      columnas: z.array(z.string().trim().min(1), { error: "columnas: lista de nombres de campo" }).min(1, "columnas está vacío").optional(),
      separador: z.enum([",", ";", "tab"], { error: "separador: , ; o tab" }).optional(),
      raiz: z.string().regex(NOMBRE_XML, "raiz: un nombre de elemento XML").optional(), elemento: z.string().regex(NOMBRE_XML, "elemento: un nombre de elemento XML").optional(),
      titulo: txt, nombre: txt,
    }).superRefine((c: C, ctx) => {
      if (lleno(c.de) === lleno(c.contenido)) mal(ctx, "de", "archivo_exportar necesita de (la variable con los datos) o contenido (el texto con {{variables}}), uno de los dos");
      if (lleno(c.contenido) && c.formato === "csv" && c.columnas) mal(ctx, "columnas", "columnas solo aplica con de");
    }),
    archivo_comprimir: conf({ archivos: archivosRef("archivos"), nombre: txt }),
    documento_plantilla: conf({
      plantilla: z.string({ error: "falta plantilla: el id de una plantilla de Documentos" }).trim().min(1, "falta plantilla")
        .refine((x) => x.includes("{{") || ID_PLANTILLA_DOCUMENTO.test(x), "plantilla: el id de una plantilla de Documentos (minúsculas, números y guiones)"),
      datos: mapaPlantillas("datos").optional(), formato: z.enum(["original", "pdf"], { error: "formato: original o pdf" }).optional(),
      desde_ia: desdeIaPlantilla.optional(), marca: marcaZod.optional(), nombre: txt,
    }),
    documento_marca: conf({ archivos: archivosRef("archivos"), marca: marcaZod, sufijo: txt }),
  };
}

/** Una línea que dice qué hace cada nodo (para el lienzo). */
export function describirSalidas(tipo: string, c: Record<string, unknown>, add: (d: string) => void): boolean {
  if (!(TIPOS_SALIDAS as readonly string[]).includes(tipo)) return false;
  if (tipo === "archivo_exportar") add(`Exporta ${c.de ? `«${String(c.de)}»` : "un texto"} a ${String(c.formato ?? "json").toUpperCase()}${c.nombre ? ` · ${String(c.nombre)}` : ""}`);
  else if (tipo === "documento_marca") {
    const m = (c.marca ?? {}) as Record<string, unknown>;
    const piezas = [m.logo && "logo", m.banner && "banner", m.marca_agua && "marca de agua", m.colores && "colores", m.fuente && "fuente"].filter(Boolean);
    add(`Pone ${piezas.join(", ") || "la marca"} a ${Array.isArray(c.archivos) ? `${c.archivos.length} referencia(s) de documentos` : `«${String(c.archivos ?? "")}»`}`);
  } else if (tipo === "archivo_comprimir") add(`Junta ${Array.isArray(c.archivos) ? `${c.archivos.length} referencia(s) de archivos` : `«${String(c.archivos ?? "")}»`} en un ZIP`);
  else {
    const ia = c.desde_ia as { modo?: string } | undefined;
    add(`Llena la plantilla «${String(c.plantilla ?? "")}» de Documentos${c.formato === "pdf" ? " y la pasa a PDF" : ""}${ia ? ` · la IA ${ia.modo === "completo" ? "arma el contenido" : "escribe lo que falta"}` : ""}`);
  }
  add("No escribe afuera: el archivo queda en el flujo y como resultado de la ejecución");
  return true;
}
