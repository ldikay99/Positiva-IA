export interface Parametro {
  clave: string;
  modulo: string;
  nombre: string;
  tipo: string;
  valor: unknown;
  descripcion: string;
  env: string | null;
  sobrescribible_en_caliente: boolean;
  nota_sobrescritura?: string;
  ajuste: "modulo" | "entorno" | null;
  motivo_fijo?: string;
  es_regex?: boolean;
}

export interface Nodo {
  id: string;
  nombre: string;
  descripcion: string;
  etapa_cronometro?: string | null;
  terminal?: boolean;
  modulo?: string;
  funciones?: string[];
  usa_ia?: boolean;
  siguientes?: string[];
  desenlaces?: { accion: string; cuando: string }[];
  reglas?: { id: string; descripcion: string; modulo?: string; funcion?: string }[];
  parametros: Parametro[];
}

export interface Proveedor {
  id: string;
  nombre: string;
  tipo: string;
  modulo?: string;
  configuracion?: string[];
  notas?: string;
  operaciones?: { nombre: string; metodo?: string; ruta?: string; escribe?: boolean }[];
}

export interface Herramienta {
  nombre: string;
  descripcion: string;
  parametros?: unknown;
  modulo?: string;
  escribe?: boolean;
  efectos?: string;
}

export interface Catalogo {
  nodos: Nodo[];
  aristas: { desde: string; hacia: string; etiqueta?: string }[];
  herramientas_agente: Herramienta[];
  proveedores: Proveedor[];
  desenlaces: { accion: string; descripcion: string }[];
  variables_entorno: { nombre: string; uso: string; secreto: boolean }[];
  catalogos?: { nombre: string; modulo?: string; descripcion?: string; tamano?: number }[];
}

export interface Adjunto {
  nombre: string;
  archivo: string;
  tipo: string;
  tamano: number;
  enLinea: boolean;
}

export interface Correo {
  caso: string;
  asunto: string;
  remitente: string;
  remitenteCorreo: string;
  recibido: string;
  vistaPrevia: string;
  adjuntos: Adjunto[];
  desenlaceProduccion: string;
  motivoProduccion: string;
  cuerpo?: string;
}

export interface Configuracion {
  parametros: Record<string, unknown>;
  entorno: Record<string, string>;
  ia: { modelo: string; ubicacion: string; temperatura: number | null };
  concurrencia: number;
  topeSegundos: number;
}

export interface Evento {
  tipo: string;
  corrida: string | null;
  t: number;
  [campo: string]: unknown;
}

export interface Corrida {
  id: string;
  caso: string;
  estado: "en_cola" | "procesando" | "terminada" | "fallida";
  creada: number;
  eventos: Evento[];
  resultado?: Evento;
}

export interface EstadoMotor {
  trabajadores: { id: number; listo: boolean; corrida: string | null; caso: string | null }[];
  enCola: string[];
  errorInicio: string;
}

export type EstadoNodo = "inactivo" | "activo" | "hecho" | "fallo" | "desenlace";
