-- 0002 · El estado de la plataforma que hoy vive en archivos de /datos/estado: flujos y sus versiones,
-- ejecuciones y sus eventos, aprobaciones, aplazadas, programaciones, conversaciones y configuración.
--
-- Columnas en claro: solo lo Interno (ids, organización, flujo, estado, fechas, tamaños, tipos), que basta
-- para permisos, búsquedas y retención. Lo Personal confidencial y lo Sensible va en columnas bytea
-- cifradas por la aplicación con una DEK de la organización (AES-256-GCM; formato: 0x01 ‖ iv[12] ‖ tag[16]
-- ‖ datos; dato asociado «<organización>|<tabla>|<id>»), con la llave en (organizacion_id, llave_id).
-- Cada columna cifrada lleva al lado su llave_id: así se sabe qué destruir y qué volver a envolver.

-- ------------------------------------------------------------------ particiones mensuales

-- Crea (si faltan) las particiones mensuales de una tabla particionada por rango de fecha, cada una aislada
-- por organización y sin permisos directos para vera_app (solo se consulta por la tabla madre, que aplica la
-- política). La aplicación la llama al arrancar y cuando una escritura no encuentra su mes.
CREATE OR REPLACE FUNCTION vera.asegurar_particiones(p_tabla text, p_desde date, p_meses int DEFAULT 3) RETURNS int
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, vera
  AS $$
DECLARE
  inicio date := date_trunc('month', p_desde)::date;
  creadas int := 0;
  nombre text;
BEGIN
  IF p_tabla NOT IN ('corridas', 'eventos_corrida') THEN
    RAISE EXCEPTION 'tabla sin particiones mensuales: %', p_tabla;
  END IF;
  IF p_meses < 1 OR p_meses > 24 THEN
    RAISE EXCEPTION 'p_meses fuera de rango: %', p_meses;
  END IF;
  -- Lo que se migra puede ser viejo, pero no de otra era; y nada más allá de un año adelante.
  IF inicio < (now() - interval '5 years')::date OR inicio > (now() + interval '1 year')::date THEN
    RAISE EXCEPTION 'p_desde fuera de rango: %', p_desde;
  END IF;
  FOR i IN 0 .. p_meses - 1 LOOP
    nombre := format('%s_%s', p_tabla, to_char(inicio + make_interval(months => i), 'YYYYMM'));
    IF to_regclass('vera.' || nombre) IS NULL THEN
      EXECUTE format('CREATE TABLE vera.%I PARTITION OF vera.%I FOR VALUES FROM (%L) TO (%L)', nombre, p_tabla,
                     inicio + make_interval(months => i), inicio + make_interval(months => i + 1));
      PERFORM vera.aislar(format('vera.%I', nombre)::regclass);
      EXECUTE format('REVOKE ALL ON vera.%I FROM vera_app', nombre);
      creadas := creadas + 1;
    END IF;
  END LOOP;
  RETURN creadas;
END $$;
REVOKE ALL ON FUNCTION vera.asegurar_particiones(text, date, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION vera.asegurar_particiones(text, date, int) TO vera_app;

-- ------------------------------------------------------------------ flujos

-- Un flujo de la organización: los declarativos (flujos/<id>.json), las copias de pqrd y tutelas
-- (flujos_codigo/<id>.json) y los originales de código cuando se renombran (flujos_codigo/_originales.json)
-- o tienen historia (estructura editada, metadatos de versión).
CREATE TABLE vera.flujos (
  organizacion_id    uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  id                 text NOT NULL CHECK (id ~ '^[a-z][a-z0-9-]{1,40}$'),
  tipo               text NOT NULL CHECK (tipo IN ('declarativo', 'codigo')),
  -- De código: el motor del que es instancia (pqrd o tutelas).
  base               text CHECK (base IN ('pqrd', 'tutelas')),
  original           boolean NOT NULL DEFAULT false,
  copia_de           text,
  -- En los originales, NULL es «el nombre del código»; en los demás, el nombre que ve la organización.
  nombre             text CHECK (length(nombre) BETWEEN 2 AND 80),
  descripcion        text CHECK (length(descripcion) <= 800),
  -- Declarativo: la versión vigente (fila de flujo_versiones con clase «definicion»).
  version_actual     int CHECK (version_actual >= 1),
  -- De código: la estructura editada vigente (clase «estructura»); NULL es la del código.
  estructura_actual  int CHECK (estructura_actual >= 1),
  nota               text CHECK (length(nota) <= 300),
  -- Trata salud, tutelas o voz (docs/DATOS_SENSIBLES.md): fija los plazos cortos de retención.
  sensible           boolean NOT NULL DEFAULT false,
  creado             timestamptz NOT NULL DEFAULT now(),
  actualizado        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organizacion_id, id),
  CHECK ((tipo = 'codigo') = (base IS NOT NULL)),
  CHECK (tipo = 'declarativo' OR version_actual IS NULL),
  CHECK (tipo = 'codigo' OR estructura_actual IS NULL),
  CHECK (NOT original OR (tipo = 'codigo' AND id = base))
);
SELECT vera.aislar('vera.flujos');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.flujos TO vera_app;

-- Cada versión guardada: la definición de un declarativo (flujos/<id>.json y flujos/versiones/<id>.v<N>.json)
-- o la estructura editada de uno de código (flujos_codigo/<id>.estructura[.v<N>].json). Nunca se reescribe:
-- volver a una versión crea otra.
CREATE TABLE vera.flujo_versiones (
  organizacion_id uuid NOT NULL,
  flujo_id        text NOT NULL,
  clase           text NOT NULL CHECK (clase IN ('definicion', 'estructura')),
  version         int NOT NULL CHECK (version >= 1),
  -- Interno: nodos, aristas, diseño. Sin secretos: el token de cada webhook va aparte, cifrado.
  definicion      jsonb NOT NULL CHECK (jsonb_typeof(definicion) = 'object'),
  -- Estructura: {tramos, intercambio, apagadas}.
  extra           jsonb,
  -- {nodo: token} de los webhooks, cifrado con la DEK general.
  secretos        bytea,
  secretos_llave  uuid,
  nota            text CHECK (length(nota) <= 300),
  guardada        timestamptz NOT NULL,
  PRIMARY KEY (organizacion_id, flujo_id, clase, version),
  FOREIGN KEY (organizacion_id, flujo_id) REFERENCES vera.flujos (organizacion_id, id) ON DELETE CASCADE,
  FOREIGN KEY (organizacion_id, secretos_llave) REFERENCES vera.llaves_datos (organizacion_id, id),
  CHECK ((secretos IS NULL) = (secretos_llave IS NULL))
);
SELECT vera.aislar('vera.flujo_versiones');
-- Inmutables: se agregan y se borran con su flujo, nunca se cambian.
GRANT SELECT, INSERT, DELETE ON vera.flujo_versiones TO vera_app;
CREATE OR REPLACE FUNCTION vera.version_inmutable() RETURNS trigger
  LANGUAGE plpgsql
  AS $$
BEGIN
  RAISE EXCEPTION 'una versión guardada no se cambia: se guarda otra' USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER flujo_versiones_inmutables BEFORE UPDATE ON vera.flujo_versiones
  FOR EACH ROW EXECUTE FUNCTION vera.version_inmutable();

-- Quién hizo cada versión y por qué (flujos_historial/<id>.json). Una fila por número de versión del flujo,
-- de definición o de estructura (la 0 de los de código es «la del código»).
CREATE TABLE vera.metas_version (
  organizacion_id uuid NOT NULL,
  flujo_id        text NOT NULL,
  version         int NOT NULL CHECK (version >= 0),
  autor           text CHECK (autor IN ('persona', 'vera')),
  -- La persona (con el inicio de sesión); hoy NULL.
  persona_id      uuid,
  fecha           timestamptz,
  resumen         text CHECK (length(resumen) <= 8000),
  etiqueta        text CHECK (length(etiqueta) <= 300),
  restaurada_de   int,
  por             text CHECK (por IN ('restaurar', 'deshacer', 'rehacer')),
  deshecha        int,
  del_codigo      boolean NOT NULL DEFAULT false,
  pedido_id       text,
  -- {pedido, punto}: lo que pidió la persona a Vera (texto libre), cifrado con la DEK general.
  textos          bytea,
  textos_llave    uuid,
  PRIMARY KEY (organizacion_id, flujo_id, version),
  FOREIGN KEY (organizacion_id, flujo_id) REFERENCES vera.flujos (organizacion_id, id) ON DELETE CASCADE,
  FOREIGN KEY (organizacion_id, textos_llave) REFERENCES vera.llaves_datos (organizacion_id, id),
  CHECK ((textos IS NULL) = (textos_llave IS NULL))
);
SELECT vera.aislar('vera.metas_version');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.metas_version TO vera_app;

-- ------------------------------------------------------------------ ejecuciones (corridas)

-- Una ejecución terminada (corridas/<día>.jsonl). Particionada por mes de creación: la retención borra por
-- fecha sin recorrer todo y lo vencido de toda la plataforma se va con DROP de la partición.
CREATE TABLE vera.corridas (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  id              text NOT NULL CHECK (length(id) BETWEEN 1 AND 100),
  creada          timestamptz NOT NULL,
  -- Referencia blanda: la ejecución sobrevive a que el flujo se renombre; al borrar el flujo se borran sus
  -- ejecuciones a propósito (y queda en la auditoría con el conteo).
  flujo_id        text NOT NULL,
  -- La versión del flujo con la que corrió (hoy se infiere por fecha; NULL en las migradas).
  version_flujo   int,
  caso            text NOT NULL CHECK (length(caso) <= 1000),
  lote            text,
  estado          text NOT NULL CHECK (estado IN ('en_cola', 'procesando', 'terminada', 'fallida')),
  origen          text,
  inicio          timestamptz,
  fin             timestamptz,
  -- El desenlace (resultado.accion: radicado, manual, completar, error…), para tableros sin descifrar.
  accion          text,
  n_eventos       int NOT NULL DEFAULT 0 CHECK (n_eventos >= 0),
  -- {configuracion, entrada, resultado?}. Flujo sensible: DEK «sensible» del mes; si no, la general. La retención
  -- del contenido lo pone en NULL (y borra sus eventos); los metadatos se quedan su año.
  contenido       bytea,
  llave_id        uuid,
  -- Índice ciego de los titulares (HMAC con la llave «indice»): supresión sin descifrar.
  titulares       bytea[],
  PRIMARY KEY (organizacion_id, id, creada),
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id),
  CHECK ((contenido IS NULL) = (llave_id IS NULL))
) PARTITION BY RANGE (creada);
CREATE INDEX corridas_por_flujo ON vera.corridas (organizacion_id, flujo_id, creada DESC);
CREATE INDEX corridas_por_fecha ON vera.corridas (organizacion_id, creada DESC);
CREATE INDEX corridas_titulares ON vera.corridas USING gin (titulares) WHERE titulares IS NOT NULL;
SELECT vera.aislar('vera.corridas');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.corridas TO vera_app;

-- Los eventos de cada ejecución, en tramos de hasta 500 (cada uno cifrado aparte): se agregan mientras
-- corre sin reescribir lo anterior y se leen enteros. Misma partición que su ejecución.
CREATE TABLE vera.eventos_corrida (
  organizacion_id uuid NOT NULL,
  corrida_id      text NOT NULL,
  corrida_creada  timestamptz NOT NULL,
  tramo           int NOT NULL CHECK (tramo >= 0),
  n               int NOT NULL CHECK (n BETWEEN 1 AND 500),
  desde_t         timestamptz,
  hasta_t         timestamptz,
  -- Los tipos de evento del tramo (paso, escritura, resultado…): Interno, para filtrar sin descifrar.
  tipos           text[] NOT NULL,
  contenido       bytea NOT NULL,
  llave_id        uuid NOT NULL,
  PRIMARY KEY (organizacion_id, corrida_id, corrida_creada, tramo),
  FOREIGN KEY (organizacion_id, corrida_id, corrida_creada) REFERENCES vera.corridas (organizacion_id, id, creada) ON DELETE CASCADE,
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id)
) PARTITION BY RANGE (corrida_creada);
CREATE INDEX eventos_corrida_tipos ON vera.eventos_corrida USING gin (tipos);
SELECT vera.aislar('vera.eventos_corrida');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.eventos_corrida TO vera_app;

-- ------------------------------------------------------------------ aprobaciones y aplazadas

-- aprobaciones/apr-*.json, con su .estado.json y su marca .reanudada.
CREATE TABLE vera.aprobaciones (
  organizacion_id   uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  id                text NOT NULL CHECK (id ~ '^apr-[a-z0-9-]{1,41}-[0-9a-f]{12}$'),
  flujo_id          text NOT NULL,
  corrida_id        text NOT NULL,
  caso              text,
  nodo              text,
  estado            text NOT NULL CHECK (estado IN ('pendiente', 'aprobado', 'rechazado', 'vencido')),
  creada            timestamptz NOT NULL,
  vence             timestamptz,
  al_vencer         text CHECK (length(al_vencer) <= 20),
  decision          text CHECK (decision IN ('aprobar', 'rechazar', 'vencido')),
  -- persona | vencimiento
  por               text CHECK (length(por) <= 20),
  resuelta_por      uuid,
  resuelta          timestamptz,
  -- La corrida encolada para seguir el flujo y cuándo arrancó de verdad (la marca .reanudada: una sola vez).
  reanudada         text,
  reanudada_en      timestamptz,
  -- {titulo, detalle, datos, campos_editables, salidas, nombre_nodo, asignado_a, comentario, cambios}.
  contenido         bytea NOT NULL,
  -- El estado completo de la ejecución en pausa (.estado.json). Se borra al reanudar (docs/DATOS_SENSIBLES.md).
  estado_corrida    bytea,
  llave_id          uuid NOT NULL,
  titulares         bytea[],
  PRIMARY KEY (organizacion_id, id),
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id),
  CHECK (estado <> 'pendiente' OR resuelta IS NULL),
  CHECK (reanudada_en IS NULL OR reanudada IS NOT NULL)
);
CREATE INDEX aprobaciones_por_flujo ON vera.aprobaciones (organizacion_id, flujo_id, estado);
CREATE INDEX aprobaciones_que_vencen ON vera.aprobaciones (organizacion_id, vence) WHERE estado = 'pendiente';
CREATE INDEX aprobaciones_sin_seguir ON vera.aprobaciones (organizacion_id) WHERE estado <> 'pendiente' AND reanudada_en IS NULL;
SELECT vera.aislar('vera.aprobaciones');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.aprobaciones TO vera_app;

-- aplazadas/apl-*.json: ejecuciones que vuelven a correr a su hora.
CREATE TABLE vera.aplazadas (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  id              text NOT NULL CHECK (id ~ '^apl-[a-z0-9-]{1,41}-[0-9a-f]{12}$'),
  flujo_id        text NOT NULL,
  corrida_id      text NOT NULL,
  caso            text,
  nodo            text,
  cuando          timestamptz NOT NULL,
  intento         int NOT NULL CHECK (intento >= 0),
  creada          timestamptz NOT NULL,
  encolada        text,
  encolada_t      timestamptz,
  -- {entrada, motivo, archivos}.
  contenido       bytea NOT NULL,
  llave_id        uuid NOT NULL,
  titulares       bytea[],
  PRIMARY KEY (organizacion_id, id),
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id)
);
CREATE INDEX aplazadas_por_hora ON vera.aplazadas (organizacion_id, cuando);
CREATE INDEX aplazadas_por_flujo ON vera.aplazadas (organizacion_id, flujo_id);
SELECT vera.aislar('vera.aplazadas');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.aplazadas TO vera_app;

-- ------------------------------------------------------------------ programaciones

-- programaciones.json: la programación como el job de los flujos de código (los declarativos la llevan en
-- su disparador, dentro de la definición).
CREATE TABLE vera.programaciones (
  organizacion_id uuid NOT NULL,
  flujo_id        text NOT NULL,
  cron            text NOT NULL CHECK (length(cron) <= 120),
  zona            text NOT NULL CHECK (length(zona) <= 64),
  activo          boolean NOT NULL,
  n               int NOT NULL CHECK (n BETWEEN 1 AND 150),
  cambiado        timestamptz,
  cambiado_por    uuid,
  PRIMARY KEY (organizacion_id, flujo_id),
  FOREIGN KEY (organizacion_id, flujo_id) REFERENCES vera.flujos (organizacion_id, id) ON DELETE CASCADE
);
SELECT vera.aislar('vera.programaciones');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.programaciones TO vera_app;

-- programador.json: la última vez que tocó cada flujo (claves p:<flujo>, c:<flujo>, j:<flujo>[:minuto]).
CREATE TABLE vera.marcas_programador (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  clave           text NOT NULL CHECK (length(clave) BETWEEN 1 AND 120),
  valor           bigint NOT NULL,
  PRIMARY KEY (organizacion_id, clave)
);
SELECT vera.aislar('vera.marcas_programador');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.marcas_programador TO vera_app;

-- ------------------------------------------------------------------ conversaciones de Vera

-- chats/<id>.json: la conversación; sus mensajes van en filas aparte.
CREATE TABLE vera.conversaciones (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  id              text NOT NULL CHECK (id ~ '^[a-z0-9-]{8,40}$'),
  -- De quién es (con el inicio de sesión: cada persona borra las suyas); hoy NULL.
  persona_id      uuid,
  -- Referencia blanda: al borrar el flujo la conversación se conserva sin el vínculo.
  flujo_id        text,
  creada          timestamptz NOT NULL,
  actualizada     timestamptz NOT NULL,
  n_mensajes      int NOT NULL DEFAULT 0 CHECK (n_mensajes >= 0),
  -- {titulo, ultimo}: el título lo escribe la persona o Vera con lo que se habló.
  contenido       bytea NOT NULL,
  llave_id        uuid NOT NULL,
  titulares       bytea[],
  PRIMARY KEY (organizacion_id, id),
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id)
);
CREATE INDEX conversaciones_recientes ON vera.conversaciones (organizacion_id, actualizada DESC);
CREATE INDEX conversaciones_por_persona ON vera.conversaciones (organizacion_id, persona_id, actualizada DESC);
SELECT vera.aislar('vera.conversaciones');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.conversaciones TO vera_app;

CREATE TABLE vera.mensajes (
  organizacion_id uuid NOT NULL,
  conversacion_id text NOT NULL,
  orden           int NOT NULL CHECK (orden >= 0),
  rol             text NOT NULL CHECK (rol IN ('usuario', 'asistente')),
  t               timestamptz NOT NULL,
  voz             boolean NOT NULL DEFAULT false,
  -- El mensaje completo {texto, adjuntos, pasos, archivos, propuestas…}; con voz, Sensible.
  contenido       bytea NOT NULL,
  llave_id        uuid NOT NULL,
  PRIMARY KEY (organizacion_id, conversacion_id, orden),
  FOREIGN KEY (organizacion_id, conversacion_id) REFERENCES vera.conversaciones (organizacion_id, id) ON DELETE CASCADE,
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id)
);
SELECT vera.aislar('vera.mensajes');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.mensajes TO vera_app;

-- chats/adjuntos/<id>/: la ficha va aquí; los bytes, en el almacén de objetos (Cloud Storage bajo
-- org/<organización>/chats/<conversación>/<adjunto>, cifrados con la DEK; en local, el volumen).
CREATE TABLE vera.adjuntos_conversacion (
  organizacion_id uuid NOT NULL,
  conversacion_id text NOT NULL,
  id              text NOT NULL CHECK (id ~ '^[a-z0-9-]{6,20}$'),
  tipo            text NOT NULL CHECK (length(tipo) <= 120),
  tamano          bigint NOT NULL CHECK (tamano >= 0),
  extension       text NOT NULL CHECK (extension ~ '^(\.[a-z0-9]{1,7})?$'),
  objeto          text NOT NULL,
  sha256          bytea CHECK (length(sha256) = 32),
  creado          timestamptz NOT NULL DEFAULT now(),
  -- {nombre}: el nombre del archivo puede ser un dato personal.
  contenido       bytea NOT NULL,
  llave_id        uuid NOT NULL,
  PRIMARY KEY (organizacion_id, conversacion_id, id),
  FOREIGN KEY (organizacion_id, conversacion_id) REFERENCES vera.conversaciones (organizacion_id, id) ON DELETE CASCADE,
  FOREIGN KEY (organizacion_id, llave_id) REFERENCES vera.llaves_datos (organizacion_id, id)
);
SELECT vera.aislar('vera.adjuntos_conversacion');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.adjuntos_conversacion TO vera_app;

-- ------------------------------------------------------------------ configuración

-- Los ajustes de la organización, uno por clave: configuracion (configuracion.json), ia_global
-- (ia_global.json), ia_vera (ia_vera.json), recursos (recursos.json) y compuertas (compuertas.json). Interno.
CREATE TABLE vera.ajustes (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  clave           text NOT NULL CHECK (clave IN ('configuracion', 'ia_global', 'ia_vera', 'recursos', 'compuertas')),
  valor           jsonb NOT NULL,
  actualizado     timestamptz NOT NULL DEFAULT now(),
  actualizado_por uuid,
  PRIMARY KEY (organizacion_id, clave)
);
SELECT vera.aislar('vera.ajustes');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.ajustes TO vera_app;

-- historial.jsonl: cada cambio de la configuración (clave aplanada, antes y después). Solo se agrega.
CREATE TABLE vera.cambios_configuracion (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  id              bigint GENERATED ALWAYS AS IDENTITY,
  -- Huella del renglón de origen: la migración de datos no repite cambios.
  huella          bytea CHECK (length(huella) = 32),
  t               timestamptz NOT NULL,
  clave           text NOT NULL CHECK (length(clave) <= 200),
  antes           jsonb,
  despues         jsonb,
  nota            text CHECK (length(nota) <= 2000),
  persona_id      uuid,
  PRIMARY KEY (organizacion_id, id)
);
CREATE INDEX cambios_configuracion_recientes ON vera.cambios_configuracion (organizacion_id, t DESC, id DESC);
CREATE UNIQUE INDEX cambios_configuracion_huella ON vera.cambios_configuracion (organizacion_id, huella) WHERE huella IS NOT NULL;
SELECT vera.aislar('vera.cambios_configuracion');
GRANT SELECT, INSERT ON vera.cambios_configuracion TO vera_app;

-- perfiles/<nombre>.json: configuraciones guardadas con nombre.
CREATE TABLE vera.perfiles_configuracion (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  nombre          text NOT NULL CHECK (nombre ~ '^[\w áéíóúñÁÉÍÓÚÑ.-]{1,60}$'),
  descripcion     text NOT NULL DEFAULT '' CHECK (length(descripcion) <= 400),
  t               timestamptz NOT NULL,
  configuracion   jsonb NOT NULL,
  PRIMARY KEY (organizacion_id, nombre)
);
SELECT vera.aislar('vera.perfiles_configuracion');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.perfiles_configuracion TO vera_app;
