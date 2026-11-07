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
  reglas?: { id: string; descripcion: string; modulo?: string; funcion?: string; usa?: string[] }[];
  busquedas?: Busqueda[];
  parametros: Parametro[];
  /** La categoría con que se dibuja, cuando el catálogo la trae (flujos que no son PQRD). */
  categoria?: string;
  /** El tipo y la configuración de un nodo de flujo declarativo. */
  tipo_declarativo?: string;
  config?: Record<string, unknown>;
}

export interface Busqueda {
  funcion: string;
  campos: string[];
  nombre: string;
  descripcion: string;
  usa: string[];
}

/** Regla creada desde la web que el motor evalúa en la entrada o antes de la compuerta. */
export interface Regla {
  id: string;
  nombre: string;
  descripcion: string;
  activa: boolean;
  punto: "entrada" | "validacion";
  campo: string;
  operador: "coincide" | "no_coincide" | "falta" | "existe";
  patron: string;
  ignorarMayusculas: boolean;
  accion: "manual" | "aviso" | "fijar";
  motivo: string;
  valor: string;
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
  /** Las fases del lienzo de un flujo que no es PQRD (Tutelas, declarativos). */
  fases?: { id: string; nombre: string; nodos: string[] }[];
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
  /** "buzon": traído del buzón real con sus etiquetas de hoy; "local": de la carpeta descargada. */
  origen?: "buzon" | "local";
  categorias?: string[];
  cuerpo?: string;
  /** universo (descargados de PQRD), buzon_real, prueba (subido al flujo), buzon_flujo o recibido. */
  fuente?: "universo" | "buzon_real" | "prueba" | "buzon_flujo" | "recibido";
}

/** Un correo de la carpeta que lee el job, tal como lo tomaría ahora. */
export interface CorreoDelBuzon {
  id: string;
  caso: string;
  asunto: string;
  remitente: string;
  remitenteCorreo: string;
  recibido: string;
  categorias: string[];
  conAdjuntos: boolean;
  hilo: string;
  traido?: boolean;
  /** Solo tutelas: el job procesa el hilo como unidad. */
  correosEnHilo?: number;
  principal?: boolean;
}

/** Un flujo de la plataforma: de código (un motor real) o declarativo (creado aquí o por la IA). */
export interface FlujoResumen {
  id: string;
  nombre: string;
  descripcion: string;
  tipo: "codigo" | "declarativo";
  buzonReal: boolean;
  lote: boolean;
  version?: number;
  actualizado?: number;
  nodos?: number;
  /** De código: el motor del que es instancia (pqrd o tutelas), también en sus copias. */
  base?: string;
  /** pqrd y tutelas: los de producción, que no se borran. */
  original?: boolean;
  borrable?: boolean;
  copia_de?: string;
  /** Con qué arranca (tipo del disparador; «buzon_real» en los de código). */
  disparador?: string;
  /** Si arranca con un correo: solo esos muestran «Correspondencia». */
  correspondencia?: boolean;
}

/** Un archivo subido al espacio de un flujo. */
export interface ArchivoFlujo {
  id: string;
  nombre: string;
  tipo: string;
  tamano: number;
  sha256: string;
  subido: number;
}

export interface TableroFlujo {
  id: string;
  nombre: string;
  tipo: "codigo" | "declarativo";
  base?: string;
  copia_de?: string;
  disparador?: string | null;
  borrable?: boolean;
  corridas: number;
  desenlaces: Record<string, number>;
  segundosMediana: number;
  tokens: number;
  /** Lo que gastó en IA (tokens y costo estimado con precios aproximados). */
  consumo?: { tokens_entrada: number; tokens_salida: number; costo_estimado_usd: number; llamadas: number; por_proveedor: Record<string, { tokens_entrada: number; tokens_salida: number; costo_estimado_usd: number; llamadas: number }> };
  enCurso: number;
  ultimas: { id: string; caso: string; accion: string; motivo: string; t: number }[];
  porDia: { dia: string; n: number; radicados: number }[];
  /** Sus trabajadores ahora: cuántos, CPU (% de un núcleo, sumado) y memoria, y las alertas suaves. */
  salud?: SaludFlujo | null;
}

export interface SaludFlujo {
  procesos: number;
  ocupados: number;
  en_cola: number;
  trabajadores_max: number;
  cola_max: number;
  tiempo_max_s: number;
  cpu: number | null;
  memoria_mb: number | null;
  alertas: { trabajador: number; texto: string; ocupado: boolean }[];
  error_inicio: string | null;
}

/** Los límites de un flujo (Entorno › Ejecución). */
export interface RecursosFlujo {
  trabajadores: number;
  corridas_simultaneas: number;
  cola_max: number;
  tiempo_max_s: number;
  propios: string[];
  por_defecto: { trabajadores: number; corridas_simultaneas: number; cola_max: number; tiempo_max_s: number };
  tope_global: number;
}

export interface Buzon {
  carpeta: string;
  total: number | null;
  correos: CorreoDelBuzon[];
  error?: string;
}

export interface Configuracion {
  reglas: Regla[];
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
  /** Corrió junto con otros como una corrida del job (un solo orquestador). */
  lote?: string;
}

export interface EstadoMotor {
  trabajadores: { id: number; listo: boolean; corrida: string | null; caso: string | null; cpu?: number | null; memoria_mb?: number | null; ocupado_s?: number; alertas?: string[] }[];
  enCola: string[];
  errorInicio: string;
}

export type EstadoNodo = "inactivo" | "activo" | "hecho" | "fallo" | "desenlace";
