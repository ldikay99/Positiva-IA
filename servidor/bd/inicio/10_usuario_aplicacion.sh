#!/bin/sh
# Solo la primera vez que arranca el volumen (docker-entrypoint-initdb.d): el usuario con el que entra la
# aplicación. Es miembro de vera_app (sin BYPASSRLS y dueño de nada), así que Row Level Security siempre le
# aplica. Su clave sale del secreto bd_app; la del migrador (POSTGRES_USER, dueño de las tablas), de bd_migrador.
# Las tablas, las políticas y los permisos los crean las migraciones (npm run bd -- migrar), no este script.
set -eu
clave="$(cat /run/secrets/bd_app)"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v clave="$clave" <<'SQL'
SELECT 'CREATE ROLE vera_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vera_app') \gexec
CREATE ROLE vera_app_login LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'clave' IN ROLE vera_app;
REVOKE ALL ON DATABASE :"DBNAME" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"DBNAME" TO vera_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
