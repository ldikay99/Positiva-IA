import { resolve } from "node:path";

const raiz = resolve(import.meta.dirname, "..", "..");

export const config = {
  raiz,
  puerto: Number(process.env.PUERTO ?? 8080),
  host: process.env.HOST ?? "0.0.0.0",
  python: process.env.PYTHON ?? (process.platform === "win32" ? "py" : "python3"),
  trabajador: process.env.RUTA_TRABAJADOR ?? resolve(raiz, "puente", "trabajador.py"),
  bandeja: process.env.RUTA_BANDEJA ?? resolve(raiz, "datos", "bandeja"),
  /** Los correos traídos del buzón real (solo lectura) para correrlos como el job. */
  vivo: process.env.RUTA_VIVO ?? resolve(process.env.RUTA_DATOS ?? resolve(raiz, "datos"), "vivo"),
  buzonVivo: process.env.RUTA_BUZON_VIVO ?? resolve(raiz, "puente", "buzon_vivo.py"),
  datos: process.env.RUTA_DATOS ?? resolve(raiz, "datos"),
  /** El espacio de archivos de los flujos: cada flujo guarda aquí lo suyo (<archivos>/<flujo>/entradas, /bandeja). */
  archivos: process.env.RUTA_ARCHIVOS ?? resolve(raiz, "datos", "archivos"),
  catalogo: process.env.RUTA_CATALOGO ?? resolve(raiz, "catalogo", "catalogo.json"),
  web: process.env.RUTA_WEB ?? resolve(raiz, "web", "dist"),
  trabajadoresMax: Number(process.env.TRABAJADORES_MAX ?? 8),
};
