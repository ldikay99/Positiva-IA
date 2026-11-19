-- 0003 · Lo que completa el modelo por organización y todavía no mueve la capa de repositorio:
-- conexiones y variables secretas (hoy credenciales.json y entornos/, cifrados con la llave única de la
-- plataforma), archivos (fichas; los bytes van a Cloud Storage), filas de las tablas de los flujos,
-- notificaciones, plantillas de documentos y el estado de los disparadores. Ver docs/MODELO_DE_DATOS.md.

-- ------------------------------------------------------------------ conexiones y entorno

-- credenciales.json: una conexión por nombre. Los valores (secretos y no secretos, tokens OAuth incluidos)
-- van en un solo bloque cifrado con la DEK general, o en Secret Manager (secreto_ref) en producción.
CREATE TABLE vera.credenciales (
  organizacion_id    uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  nombre             text NOT NULL CHECK (nombre ~ '^[a-z0-9][a-z0-9_.-]{0,62}$'),
  tipo               text NOT NULL CHECK (length(tipo) <= 40),
  descripcion        text NOT NULL DEFAULT '' CHECK (length(descripcion) <= 400),
  -- NULL: de toda la organización; si no, del flujo (y se borra con él).
  flujo_id           text,
  permitir_escritura boolean NOT NULL DEFAULT false,
  escritura_por      uuid,
  escritura_en       timestamptz,
  campos             text[] NOT NULL DEFAULT '{}',
  valores            bytea,
  valores_llave      uuid,
  secreto_ref        text,
  -- {conectada, cuenta, alcances, conectada_en, expira}: la cuenta es un correo → cifrada.
  conexion           bytea,
  conexion_llave     uuid,
  creada             timestamptz NOT NULL DEFAULT now(),
  creada_por         uuid,
  actualizada        timestamptz NOT NULL DEFAULT now(),
  usada              timestamptz,
  PRIMARY KEY (organizacion_id, nombre),
  FOREIGN KEY (organizacion_id, valores_llave) REFERENCES vera.llaves_datos (organizacion_id, id),
  FOREIGN KEY (organizacion_id, conexion_llave) REFERENCES vera.llaves_datos (organizacion_id, id),
  -- Las de un flujo se borran con él.
  FOREIGN KEY (organizacion_id, flujo_id) REFERENCES vera.flujos (organizacion_id, id) ON DELETE CASCADE,
  CHECK ((valores IS NULL) <> (secreto_ref IS NULL)),
  CHECK ((valores IS NULL) = (valores_llave IS NULL)),
  CHECK ((conexion IS NULL) = (conexion_llave IS NULL))
);
CREATE INDEX credenciales_por_flujo ON vera.credenciales (organizacion_id, flujo_id) WHERE flujo_id IS NOT NULL;
CREATE INDEX credenciales_sin_uso ON vera.credenciales (organizacion_id, usada);
SELECT vera.aislar('vera.credenciales');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.credenciales TO vera_app;

-- entornos/<flujo>.json: variables de cada flujo. Las no secretas en claro (Interno); las secretas, cifradas.
CREATE TABLE vera.variables_entorno (
  organizacion_id uuid NOT NULL,
  flujo_id        text NOT NULL,
  nombre          text NOT NULL CHECK (nombre ~ '^[A-Z][A-Z0-9_-]{0,63}$'),
  secreto         boolean NOT NULL,
  valor           text CHECK (length(valor) <= 20000),
  valor_cifrado   bytea,
  valor_llave     uuid,
  secreto_ref     text,
  descripcion     text NOT NULL DEFAULT '' CHECK (length(descripcion) <= 400),
  copiado_de      text,
  actualizado     timestamptz NOT NULL DEFAULT now(),
  actualizado_por uuid,
  PRIMARY KEY (organizacion_id, flujo_id, nombre),
  FOREIGN KEY (organizacion_id, flujo_id) REFERENCES vera.flujos (organizacion_id, id) ON DELETE CASCADE,
  FOREIGN KEY (organizacion_id, valor_llave) REFERENCES vera.llaves_datos (organizacion_id, id),
  CHECK (secreto OR (valor_cifrado IS NULL AND secreto_ref IS NULL)),
  CHECK (NOT secreto OR valor IS NULL),
  -- Una secreta va cifrada o en Secret Manager, no en los dos (sin ninguno: declarada, falta su valor).
  CHECK (NOT (valor_cifrado IS NOT NULL AND secreto_ref IS NOT NULL)),
  CHECK ((valor_cifrado IS NULL) = (valor_llave IS NULL))
);
SELECT vera.aislar('vera.variables_entorno');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.variables_entorno TO vera_app;

-- ------------------------------------------------------------------ archivos (fichas de objetos)

-- Cada archivo que entra, sale o se genera: los bytes viven en Cloud Storage bajo org/<organización>/…,
-- cifrados con la DEK antes de subir; aquí va la ficha. Cubre entradas/, bandeja/, recibidos/, vivo/,
-- trabajo/visor/, los generados por cada ejecución, las plantillas de documentos y la caché de conversión.
CREATE TABLE vera.archivos (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  id              text NOT NULL CHECK (length(id) BETWEEN 3 AND 80),
  clase           text NOT NULL CHECK (clase IN ('entrada', 'bandeja', 'recibido', 'vivo', 'visor', 'generado',
                                                 'fijado', 'plantilla', 'recurso_plantilla', 'cache_conversion')),
  flujo_id        text,
  corrida_id      text,
  -- La ruta del objeto (gs://<bucket>/org/<organización>/<clase>/…). Nunca una URL firmada.
  objeto          text NOT NULL,
  tipo            text CHECK (length(tipo) <= 120),
  tamano          bigint NOT NULL CHECK (tamano >= 0),
  sha256          bytea CHECK (length(sha256) = 32),
  -- {nombre, origen…}: el nombre del archivo puede ser un dato personal.
  contenido       bytea,
  llave_id        uuid NOT NULL,
  creado          timestamptz NOT NULL DEFAULT now(),
  usado           timestamptz,
  -- La fecha desde la que la retención lo borra (según su clase y si el flujo es sensible).
  vence           timestamptz,
  titulares       bytea[],
  PRIMARY KEY (organizacion_id, id),
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id)
);
CREATE INDEX archivos_por_flujo ON vera.archivos (organizacion_id, flujo_id, clase);
CREATE INDEX archivos_por_corrida ON vera.archivos (organizacion_id, corrida_id) WHERE corrida_id IS NOT NULL;
CREATE INDEX archivos_que_vencen ON vera.archivos (organizacion_id, vence) WHERE vence IS NOT NULL;
CREATE INDEX archivos_cache ON vera.archivos (organizacion_id, sha256) WHERE clase = 'cache_conversion';
CREATE INDEX archivos_titulares ON vera.archivos USING gin (titulares) WHERE titulares IS NOT NULL;
SELECT vera.aislar('vera.archivos');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.archivos TO vera_app;

-- documentos/<id>/meta.json: la biblioteca de plantillas (el archivo y sus recursos, en vera.archivos).
CREATE TABLE vera.plantillas_documento (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  id              text NOT NULL CHECK (id ~ '^[a-z0-9][a-z0-9-]{1,59}$'),
  nombre          text NOT NULL CHECK (length(nombre) <= 120),
  formato         text NOT NULL CHECK (formato IN ('docx', 'xlsx', 'pptx', 'html')),
  archivo_id      text NOT NULL,
  -- Interno: variables, repeticiones, imágenes, recursos, área, descripción.
  meta            jsonb NOT NULL,
  -- El texto completo de la plantilla.
  texto           bytea,
  llave_id        uuid,
  creada          timestamptz NOT NULL,
  actualizada     timestamptz NOT NULL,
  PRIMARY KEY (organizacion_id, id),
  FOREIGN KEY (organizacion_id, archivo_id) REFERENCES vera.archivos (organizacion_id, id),
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id),
  CHECK ((texto IS NULL) = (llave_id IS NULL))
);
SELECT vera.aislar('vera.plantillas_documento');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.plantillas_documento TO vera_app;

-- ------------------------------------------------------------------ tablas de los flujos

-- tablas/flujo-<id>.sqlite y compartida.sqlite: (tabla, clave) → datos; ambito «compartida» o «flujo-<id>».
CREATE TABLE vera.filas_tabla (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  ambito          text NOT NULL CHECK (ambito = 'compartida' OR ambito ~ '^flujo-[a-z][a-z0-9-]{1,40}$'),
  tabla           text NOT NULL CHECK (length(tabla) <= 64),
  clave           text NOT NULL CHECK (length(clave) <= 500),
  datos           bytea NOT NULL CHECK (length(datos) <= 70000),
  llave_id        uuid NOT NULL,
  creada          timestamptz NOT NULL DEFAULT now(),
  actualizada     timestamptz NOT NULL DEFAULT now(),
  vence           timestamptz,
  titulares       bytea[],
  PRIMARY KEY (organizacion_id, ambito, tabla, clave),
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id)
);
CREATE INDEX filas_tabla_que_vencen ON vera.filas_tabla (organizacion_id, vence) WHERE vence IS NOT NULL;
CREATE INDEX filas_tabla_titulares ON vera.filas_tabla USING gin (titulares) WHERE titulares IS NOT NULL;
SELECT vera.aislar('vera.filas_tabla');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.filas_tabla TO vera_app;

-- ------------------------------------------------------------------ notificaciones y disparadores

-- notificaciones.json (hoy las 300 más recientes de toda la plataforma).
CREATE TABLE vera.notificaciones (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  id              text NOT NULL CHECK (length(id) BETWEEN 6 AND 40),
  tipo            text NOT NULL CHECK (tipo IN ('fallo', 'aprobacion', 'conexion', 'recursos', 'programacion', 'alerta')),
  clave           text NOT NULL CHECK (length(clave) <= 300),
  t               timestamptz NOT NULL,
  leida           boolean NOT NULL DEFAULT false,
  -- NULL: para toda la organización; con el inicio de sesión, la persona a la que va.
  persona_id      uuid,
  flujo_id        text,
  corrida_id      text,
  aprobacion_id   text,
  -- {titulo, texto, accion}: pueden nombrar datos de la ejecución.
  contenido       bytea NOT NULL,
  llave_id        uuid NOT NULL,
  PRIMARY KEY (organizacion_id, id),
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id)
);
CREATE UNIQUE INDEX notificaciones_clave ON vera.notificaciones (organizacion_id, clave) WHERE NOT leida;
CREATE INDEX notificaciones_recientes ON vera.notificaciones (organizacion_id, t DESC);
SELECT vera.aislar('vera.notificaciones');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.notificaciones TO vera_app;

-- disparadores/<flujo>.json: lo ya visto por cada disparador de sondeo (ids de correos y eventos, delta).
CREATE TABLE vera.estado_disparadores (
  organizacion_id uuid NOT NULL,
  flujo_id        text NOT NULL,
  estado          bytea NOT NULL,
  llave_id        uuid NOT NULL,
  actualizado     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organizacion_id, flujo_id),
  FOREIGN KEY (organizacion_id, flujo_id) REFERENCES vera.flujos (organizacion_id, id) ON DELETE CASCADE,
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id)
);
SELECT vera.aislar('vera.estado_disparadores');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.estado_disparadores TO vera_app;

-- limites/flujo-<id>.sqlite: cada uso de un nodo limite_tasa (clave, instante); se borra lo que sale de la ventana.
-- En la base, compartido entre instancias del servidor.
CREATE TABLE vera.usos_limite (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  flujo_id        text NOT NULL,
  clave           text NOT NULL CHECK (length(clave) <= 300),
  t               timestamptz NOT NULL,
  FOREIGN KEY (organizacion_id, flujo_id) REFERENCES vera.flujos (organizacion_id, id) ON DELETE CASCADE
);
CREATE INDEX usos_limite_ventana ON vera.usos_limite (organizacion_id, flujo_id, clave, t);
SELECT vera.aislar('vera.usos_limite');
GRANT SELECT, INSERT, DELETE ON vera.usos_limite TO vera_app;
