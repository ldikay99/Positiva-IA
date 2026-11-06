#!/usr/bin/env bash
# Trae a motores/tutelas el motor de radicación de tutelas tal como está en un
# commit de su repositorio (git archive: nunca el árbol de trabajo). Deja fuera
# credenciales, pruebas locales, salidas y documentación.
#
#   catalogo/sincronizar_tutelas.sh [commit]
set -euo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FUENTE="${FUENTE_TUTELAS:-$RAIZ/../../ms-radicacion-ia-tutelas}"
REF="${1:-HEAD}"
DESTINO="$RAIZ/motores/tutelas"
COMMIT="$(git -C "$FUENTE" rev-parse --verify "$REF^{commit}")"
TEMPORAL="$(mktemp -d)"
trap 'rm -rf "$TEMPORAL"' EXIT
git -C "$FUENTE" archive --format=tar "$COMMIT" | tar -x -C "$TEMPORAL" \
  --exclude='.local_test' --exclude='salidas_prueba' --exclude='docs' --exclude='.env' \
  --exclude='*.env' --exclude='credenciales*' --exclude='*.pem' --exclude='*.key'
printf '%s\n' "$COMMIT" > "$TEMPORAL/ORIGEN"
rm -rf "$DESTINO"; mkdir -p "$DESTINO"; cp -a "$TEMPORAL/." "$DESTINO/"
echo "motores/tutelas quedó en ${COMMIT:0:7} ($(git -C "$FUENTE" log -1 --format=%s "$COMMIT"))"
