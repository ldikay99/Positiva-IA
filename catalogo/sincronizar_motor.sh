#!/usr/bin/env bash
# Trae a motor/ el motor de radicación tal como está en un commit del repo fuente
# y regenera el catálogo. Toma el contenido con `git archive` (nunca del árbol de
# trabajo, que puede tener cambios a medias o datos reales) y deja fuera los
# datos, los corpus y los .env. Se puede correr las veces que haga falta: cada
# vez deja motor/ idéntico al commit pedido.
#
#   catalogo/sincronizar_motor.sh [commit]            # por defecto HEAD
#   FUENTE=/ruta/al/repo catalogo/sincronizar_motor.sh
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FUENTE="${FUENTE:-$RAIZ/../../POSITIVA_IA_RADICACION_PQRD/mcp-fix}"
REF="${1:-HEAD}"
DESTINO="$RAIZ/motor"

if ! git -C "$FUENTE" rev-parse --git-dir >/dev/null 2>&1; then
  echo "No hay un repositorio git en $FUENTE (define FUENTE)" >&2
  exit 1
fi
COMMIT="$(git -C "$FUENTE" rev-parse --verify "$REF^{commit}")"

TEMPORAL="$(mktemp -d)"
trap 'rm -rf "$TEMPORAL"' EXIT

# Lo que nunca entra al visor: datos de correos, corpus etiquetados, espacios de
# trabajo, salidas de pruebas y credenciales.
git -C "$FUENTE" archive --format=tar "$COMMIT" | tar -x -C "$TEMPORAL" \
  --exclude='corpus' --exclude='workspace' --exclude='datos' --exclude='data' \
  --exclude='salidas_prueba' --exclude='logs' --exclude='.env' --exclude='*.env' \
  --exclude='credenciales*' --exclude='*.pem' --exclude='*.key'

printf '%s\n' "$COMMIT" > "$TEMPORAL/ORIGEN"

rm -rf "$DESTINO"
mkdir -p "$DESTINO"
cp -a "$TEMPORAL/." "$DESTINO/"

echo "motor/ quedó en ${COMMIT:0:7} ($(git -C "$FUENTE" log -1 --format=%s "$COMMIT"))"

# El catálogo lee el código del motor: se regenera con la copia nueva.
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3 python py; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
if [ -n "$PY" ]; then
  (cd "$RAIZ/catalogo/generador" && "$PY" extraer.py constantes.json && "$PY" build.py && "$PY" enlazar.py)
else
  echo "Sin Python: regenera el catálogo a mano (catalogo/generador/LEEME.md)" >&2
fi
