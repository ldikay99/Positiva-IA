-- 0001 · Organizaciones, personas, membresías, sesiones, llaves de datos y auditoría.
--
-- El aislamiento por organización lo hace la base, no la consulta: toda tabla de una organización
-- tiene organizacion_id y una política de Row Level Security que solo deja ver y escribir las filas
-- de la organización fijada en la transacción:
--
--     BEGIN;
--     SELECT set_config('vera.organizacion_id', '<uuid>', true);   -- SET LOCAL: muere con la transacción
--     ... consultas sin filtro de organización ...
--     COMMIT;
--
-- Sin organización fijada, vera.organizacion_actual() es NULL y no se ve ni se escribe nada.
-- La aplicación entra con un usuario miembro de vera_app (sin BYPASSRLS, dueño de nada) y las tablas
-- llevan FORCE ROW LEVEL SECURITY, así que ni su dueño (el migrador) se salta la política.

CREATE SCHEMA IF NOT EXISTS vera;
REVOKE ALL ON SCHEMA vera FROM PUBLIC;

-- El rol de la aplicación: sin inicio de sesión propio. Cada despliegue le da un usuario que entra
-- (local: vera_app_login con clave por secreto; Cloud SQL: la cuenta de servicio con IAM).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vera_app') THEN
    CREATE ROLE vera_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END $$;
GRANT USAGE ON SCHEMA vera TO vera_app;

-- La organización y la persona de la transacción en curso.
CREATE OR REPLACE FUNCTION vera.organizacion_actual() RETURNS uuid
  LANGUAGE sql STABLE PARALLEL SAFE
  AS $$ SELECT nullif(current_setting('vera.organizacion_id', true), '')::uuid $$;

CREATE OR REPLACE FUNCTION vera.persona_actual() RETURNS uuid
  LANGUAGE sql STABLE PARALLEL SAFE
  AS $$ SELECT nullif(current_setting('vera.persona_id', true), '')::uuid $$;

-- Aísla una tabla por organización (RLS + FORCE + política única). Lo usan esta y las siguientes migraciones.
CREATE OR REPLACE FUNCTION vera.aislar(tabla regclass) RETURNS void
  LANGUAGE plpgsql
  AS $$
BEGIN
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tabla);
  EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tabla);
  EXECUTE format('DROP POLICY IF EXISTS por_organizacion ON %s', tabla);
  EXECUTE format('CREATE POLICY por_organizacion ON %s USING (organizacion_id = vera.organizacion_actual()) '
                 'WITH CHECK (organizacion_id = vera.organizacion_actual())', tabla);
END $$;

-- ------------------------------------------------------------------ organizaciones

CREATE TABLE vera.organizaciones (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug          text NOT NULL UNIQUE CHECK (slug ~ '^[a-z][a-z0-9-]{1,40}$'),
  nombre        text NOT NULL CHECK (length(nombre) BETWEEN 2 AND 120),
  estado        text NOT NULL DEFAULT 'activa' CHECK (estado IN ('activa', 'suspendida', 'en_baja')),
  -- La llave maestra (KEK) de la organización en Cloud KMS: projects/…/keyRings/…/cryptoKeys/<org>.
  -- En local, «local:<huella>» (la KEK sale de VERA_KEK_LOCAL).
  kek_ref       text,
  -- Plazos propios de retención (días) que acortan los de la plataforma: {"corridas_contenido": 30, …}.
  retencion     jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(retencion) = 'object'),
  creada        timestamptz NOT NULL DEFAULT now(),
  baja_pedida   timestamptz,
  baja_confirmada_por uuid
);
ALTER TABLE vera.organizaciones ENABLE ROW LEVEL SECURITY;
ALTER TABLE vera.organizaciones FORCE ROW LEVEL SECURITY;
-- Una organización solo se ve a sí misma. Crearlas y darlas de baja es del operador (el migrador).
CREATE POLICY por_organizacion ON vera.organizaciones USING (id = vera.organizacion_actual());
GRANT SELECT ON vera.organizaciones TO vera_app;

-- Antes de saber la organización (al arrancar o al entrar) solo se puede resolver el slug a su id.
CREATE OR REPLACE FUNCTION vera.organizacion_por_slug(p_slug text) RETURNS uuid
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, vera
  AS $$ SELECT id FROM vera.organizaciones WHERE slug = p_slug AND estado = 'activa' $$;
REVOKE ALL ON FUNCTION vera.organizacion_por_slug(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION vera.organizacion_por_slug(text) TO vera_app;

-- ------------------------------------------------------------------ personas

-- Una persona existe una vez en la plataforma y pertenece a una o varias organizaciones (membresías).
CREATE TABLE vera.personas (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  correo               text NOT NULL CHECK (correo = lower(correo) AND correo ~ '^[^@\s]+@[^@\s]+$'),
  nombre               text NOT NULL CHECK (length(nombre) BETWEEN 1 AND 120),
  clave_hash           text,                 -- argon2id (PHC); nunca la clave
  segundo_factor       bytea,                -- semilla TOTP cifrada con la DEK general de su primera organización
  segundo_factor_llave uuid,
  segundo_factor_org   uuid,
  estado               text NOT NULL DEFAULT 'activa' CHECK (estado IN ('activa', 'bloqueada', 'retirada')),
  intentos_fallidos    smallint NOT NULL DEFAULT 0 CHECK (intentos_fallidos >= 0),
  bloqueada_hasta      timestamptz,
  creada               timestamptz NOT NULL DEFAULT now(),
  ultima_entrada       timestamptz,
  -- Retirada de todas sus organizaciones: su cuenta se borra desde esta fecha (30 días).
  borrar_desde         timestamptz,
  CHECK ((segundo_factor IS NULL) = (segundo_factor_llave IS NULL) AND (segundo_factor IS NULL) = (segundo_factor_org IS NULL))
);
CREATE UNIQUE INDEX personas_correo ON vera.personas (correo);

-- ------------------------------------------------------------------ membresías

CREATE TABLE vera.membresias (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  persona_id      uuid NOT NULL REFERENCES vera.personas (id) ON DELETE CASCADE,
  -- Un solo rol por organización (docs/ROLES_Y_PERMISOS.md).
  rol             text NOT NULL CHECK (rol IN ('administrador', 'editor', 'operador', 'lector')),
  creada          timestamptz NOT NULL DEFAULT now(),
  invitada_por    uuid REFERENCES vera.personas (id) ON DELETE SET NULL,
  retirada        timestamptz,
  PRIMARY KEY (organizacion_id, persona_id)
);
CREATE INDEX membresias_persona ON vera.membresias (persona_id);
SELECT vera.aislar('vera.membresias');
GRANT SELECT, INSERT, UPDATE, DELETE ON vera.membresias TO vera_app;

-- Una organización siempre tiene al menos un administrador vigente (se revisa al final de la transacción).
CREATE OR REPLACE FUNCTION vera.revisar_administradores() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, vera
  AS $$
DECLARE
  org uuid := coalesce(OLD.organizacion_id, NEW.organizacion_id);
BEGIN
  IF EXISTS (SELECT 1 FROM vera.organizaciones WHERE id = org)
     AND EXISTS (SELECT 1 FROM vera.membresias WHERE organizacion_id = org)
     AND NOT EXISTS (SELECT 1 FROM vera.membresias WHERE organizacion_id = org AND rol = 'administrador' AND retirada IS NULL) THEN
    RAISE EXCEPTION 'la organización % se quedaría sin administrador', org USING ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER membresias_un_administrador
  AFTER INSERT OR UPDATE OR DELETE ON vera.membresias DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION vera.revisar_administradores();

-- Las personas se ven a sí mismas y a las de la organización en curso; cada una solo cambia su propia fila
-- (bloqueos, intentos y retiro los hará el inicio de sesión con funciones propias, no otra organización).
ALTER TABLE vera.personas ENABLE ROW LEVEL SECURITY;
ALTER TABLE vera.personas FORCE ROW LEVEL SECURITY;
CREATE POLICY solo_la_propia ON vera.personas FOR UPDATE
  USING (id = vera.persona_actual()) WITH CHECK (id = vera.persona_actual());
CREATE POLICY propia_o_de_la_organizacion ON vera.personas FOR SELECT
  USING (id = vera.persona_actual()
         OR EXISTS (SELECT 1 FROM vera.membresias m WHERE m.persona_id = personas.id AND m.organizacion_id = vera.organizacion_actual()));
-- Alta de personas: por invitación de un administrador (lo hará el inicio de sesión con una función propia), no por INSERT directo.
GRANT SELECT, UPDATE ON vera.personas TO vera_app;

-- ------------------------------------------------------------------ sesiones

CREATE TABLE vera.sesiones (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organizacion_id  uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  persona_id       uuid NOT NULL REFERENCES vera.personas (id) ON DELETE CASCADE,
  -- SHA-256 del token que lleva la cookie; el token nunca se guarda.
  token_hash       bytea NOT NULL UNIQUE CHECK (length(token_hash) = 32),
  creada           timestamptz NOT NULL DEFAULT now(),
  ultima_actividad timestamptz NOT NULL DEFAULT now(),
  vence            timestamptz NOT NULL,
  segundo_factor   boolean NOT NULL DEFAULT false,
  cerrada          timestamptz,
  motivo_cierre    text CHECK (motivo_cierre IN ('salida', 'vencida', 'retirada', 'bloqueo', 'baja')),
  -- Solo la red /24 (o /48) y el agente recortado: lo justo para «sesiones abiertas».
  red              cidr,
  agente           text CHECK (length(agente) <= 200),
  CHECK (vence > creada),
  CHECK ((cerrada IS NULL) = (motivo_cierre IS NULL))
);
CREATE INDEX sesiones_persona ON vera.sesiones (organizacion_id, persona_id) WHERE cerrada IS NULL;
SELECT vera.aislar('vera.sesiones');
GRANT SELECT, INSERT, UPDATE ON vera.sesiones TO vera_app;

-- La única búsqueda sin organización: de la cookie a la sesión vigente (y con ella, la organización).
CREATE OR REPLACE FUNCTION vera.sesion_vigente(p_token_hash bytea)
  RETURNS TABLE (sesion_id uuid, organizacion_id uuid, persona_id uuid, rol text)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, vera
  AS $$
    SELECT s.id, s.organizacion_id, s.persona_id, m.rol
      FROM vera.sesiones s
      JOIN vera.membresias m ON m.organizacion_id = s.organizacion_id AND m.persona_id = s.persona_id AND m.retirada IS NULL
      JOIN vera.personas p ON p.id = s.persona_id AND p.estado = 'activa'
      JOIN vera.organizaciones o ON o.id = s.organizacion_id AND o.estado = 'activa'
     WHERE s.token_hash = p_token_hash AND s.cerrada IS NULL AND s.vence > now()
  $$;
REVOKE ALL ON FUNCTION vera.sesion_vigente(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION vera.sesion_vigente(bytea) TO vera_app;

-- ------------------------------------------------------------------ llaves de datos (DEK)

-- Cifrado por organización (docs/DATOS_SENSIBLES.md › Cifrado): cada DEK (AES-256-GCM) se guarda envuelta
-- por la KEK de su organización. «general» cifra lo Personal confidencial y lo Secreto; «sensible» va una por
-- mes (periodo) para el contenido Sensible, y vencida su retención se destruye (dek_envuelta NULL); «indice»
-- es la llave HMAC de los índices ciegos de titulares.
CREATE TABLE vera.llaves_datos (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  id              uuid NOT NULL DEFAULT gen_random_uuid(),
  proposito       text NOT NULL CHECK (proposito IN ('general', 'sensible', 'indice')),
  periodo         date CHECK (periodo IS NULL OR extract(day FROM periodo) = 1),
  kek_ref         text NOT NULL,
  kek_version     text,
  dek_envuelta    bytea,
  creada          timestamptz NOT NULL DEFAULT now(),
  destruida       timestamptz,
  PRIMARY KEY (organizacion_id, id),
  CHECK ((proposito = 'sensible') = (periodo IS NOT NULL)),
  CHECK ((dek_envuelta IS NULL) = (destruida IS NOT NULL))
);
-- Una vigente por organización, propósito y mes.
CREATE UNIQUE INDEX llaves_datos_vigente ON vera.llaves_datos (organizacion_id, proposito, coalesce(periodo, 'epoch'::date)) WHERE destruida IS NULL;
SELECT vera.aislar('vera.llaves_datos');
-- La aplicación crea DEK; destruirlas es del trabajo de retención (otro rol), no de la aplicación.
GRANT SELECT, INSERT ON vera.llaves_datos TO vera_app;

-- ------------------------------------------------------------------ auditoría (solo se agrega)

CREATE TABLE vera.auditoria (
  organizacion_id uuid NOT NULL REFERENCES vera.organizaciones (id) ON DELETE CASCADE,
  id              bigint GENERATED ALWAYS AS IDENTITY,
  en              timestamptz NOT NULL DEFAULT now(),
  actor           text NOT NULL CHECK (actor IN ('persona', 'vera', 'sistema', 'retencion')),
  -- Sin llave foránea a propósito: la persona se borra a los 30 días de retirada y su id y nombre se
  -- conservan aquí el plazo de la auditoría (docs/DATOS_SENSIBLES.md › Al retirar una persona).
  persona_id      uuid,
  persona_nombre  text,
  accion          text NOT NULL CHECK (accion ~ '^[a-z_]+(\.[a-z_]+)*$'),
  objeto_tipo     text NOT NULL,
  objeto_id       text,
  -- Conteos, campos cambiados, ids: nunca contenido ni secretos.
  detalle         jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detalle) = 'object'),
  -- Supresión por titular: el HMAC del documento o correo, nunca el dato.
  indice_ciego    bytea,
  PRIMARY KEY (organizacion_id, id)
);
CREATE INDEX auditoria_por_fecha ON vera.auditoria (organizacion_id, en DESC);
CREATE INDEX auditoria_por_objeto ON vera.auditoria (organizacion_id, objeto_tipo, objeto_id);
SELECT vera.aislar('vera.auditoria');
GRANT SELECT, INSERT ON vera.auditoria TO vera_app;

-- Append-only también para el dueño: nada se cambia. Solo borran la purga de lo vencido (2 años) y la baja de una
-- organización (su paso 4, con vera.baja_organizacion = su id; la constancia de la baja se guarda aparte).
CREATE OR REPLACE FUNCTION vera.auditoria_inmutable() RETURNS trigger
  LANGUAGE plpgsql
  AS $$
BEGIN
  IF TG_OP = 'DELETE' AND current_setting('vera.purga_auditoria', true) = 'si' AND OLD.en < now() - interval '2 years' THEN
    RETURN OLD;
  END IF;
  IF TG_OP = 'DELETE' AND current_setting('vera.baja_organizacion', true) = OLD.organizacion_id::text THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'la auditoría solo se agrega (% no permitido)', TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER auditoria_solo_agregar BEFORE UPDATE OR DELETE ON vera.auditoria
  FOR EACH ROW EXECUTE FUNCTION vera.auditoria_inmutable();
CREATE TRIGGER auditoria_sin_truncar BEFORE TRUNCATE ON vera.auditoria
  FOR EACH STATEMENT EXECUTE FUNCTION vera.auditoria_inmutable();

-- El dueño de las tablas (quien corre las migraciones) es el operador de la plataforma: crea y da de baja
-- organizaciones, y las funciones SECURITY DEFINER de arriba resuelven slug, sesión y administradores con
-- su identidad. Por eso, y solo en estas cuatro tablas, el dueño tiene su propia política; vera_app no.
CREATE POLICY operador ON vera.organizaciones TO CURRENT_USER USING (true) WITH CHECK (true);
CREATE POLICY resolver ON vera.membresias FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY resolver ON vera.personas FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY resolver ON vera.sesiones FOR SELECT TO CURRENT_USER USING (true);

GRANT EXECUTE ON FUNCTION vera.organizacion_actual(), vera.persona_actual() TO vera_app;
REVOKE ALL ON FUNCTION vera.aislar(regclass) FROM PUBLIC;

-- El servidor revisa al arrancar que la base esté al día con su código (sin aplicar nada).
GRANT SELECT ON vera.migraciones TO vera_app;
