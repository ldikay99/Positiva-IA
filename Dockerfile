# syntax=docker/dockerfile:1
# Positiva IA: visor web del pipeline de radicación de PQRD.
# Etapas: la web (React), el servidor (TypeScript 7), las dependencias de Python
# (en un venv, con su auditoría), los modelos de OCR y la imagen final, que solo
# lleva lo que corre: Python para el motor, Node para el servidor, tesseract y tini.
#
# Imágenes base fijadas por digest (índice multiarquitectura de Docker Hub); la
# etiqueta va al lado como referencia. Para actualizarlas, tomar el digest nuevo de
#   https://hub.docker.com/v2/repositories/library/<node|python>/tags?name=<etiqueta>
# Node 26 es la LTS activa. Python 3.13: el más nuevo con ruedas para todas las
# dependencias fijadas (3.14 aún no tiene las de oracledb 2.5.1 ni PyYAML 6.0.2;
# Pillow 13 pide >= 3.11). Sin compilador: todo se instala de ruedas.

# node:26.13.1-trixie-slim
FROM node:26.13.1-trixie-slim@sha256:e6bedef217677b276c591795e169840edd9096dbf13f29df8ef8d1856b8820bc AS node

# python:3.13.18-slim-trixie
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS python

# --- Web ---------------------------------------------------------------------
# --ignore-scripts: el único paquete con script de instalación es fsevents (macOS,
# opcional). La auditoría corta la construcción ante cualquier vulnerabilidad.
FROM node AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts --no-fund --no-audit \
    && npm audit --audit-level=low
COPY web/ ./
RUN npm run build

# --- Servidor ----------------------------------------------------------------
FROM node AS servidor
WORKDIR /src/servidor
COPY servidor/package.json servidor/package-lock.json ./
RUN npm ci --ignore-scripts --no-fund --no-audit \
    && npm audit --audit-level=low
COPY servidor/ ./
RUN npm run build && npm prune --omit=dev --ignore-scripts

# --- Dependencias de Python --------------------------------------------------
FROM python AS python-deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
WORKDIR /src
COPY motor/requirements.txt motor/requirements.txt
COPY puente/requirements.txt puente/requirements.txt
COPY puente/requirements-sin-dependencias.txt puente/requirements-sin-dependencias.txt
COPY motores/tutelas/requirements.txt motores/tutelas/requirements.txt
# pymongo va sin dnspython (ISC): ver puente/requirements.txt
RUN /opt/venv/bin/pip install -r motor/requirements.txt -r puente/requirements.txt -r motores/tutelas/requirements.txt \
    && /opt/venv/bin/pip install --no-deps -r puente/requirements-sin-dependencias.txt

# --- Auditoría de Python -----------------------------------------------------
# Audita exactamente lo instalado en el venv (incluido pip) contra la base de
# vulnerabilidades de PyPI. pip-audit va en su propio venv y no llega a la imagen
# final; esta solo copia la constancia, para que la etapa no se pueda saltar.
FROM python-deps AS auditoria-python
RUN python -m venv /opt/pip-audit \
    && /opt/pip-audit/bin/pip install pip-audit==2.10.1 \
    && mkdir /auditoria \
    && /opt/venv/bin/pip freeze --all > /auditoria/instalado.txt \
    && { /opt/pip-audit/bin/pip-audit --strict --no-deps --disable-pip --progress-spinner off --desc on \
            -r /auditoria/instalado.txt > /auditoria/pip-audit.txt 2>&1; s=$?; cat /auditoria/pip-audit.txt; exit $s; }

# --- Modelos de OCR ----------------------------------------------------------
# Tesseract 5 (Apache-2.0). El motor lee con el modelo estándar de español
# (tessdata 4.1.0, LSTM), no con el rápido que trae el paquete de Debian: es con
# el que se midieron sus umbrales y el que lleva la imagen del job. Van también
# el de inglés y el de orientación (osd), que usa para decidir si una hoja está
# de lado. Se descargan con su huella fija.
FROM python AS tessdata
RUN mkdir -p /opt/tessdata && python - <<'PY'
import hashlib, os, urllib.request
destino = "/opt/tessdata"
# Los mismos que la imagen del job (motor/Dockerfile), con su misma versión.
modelos = {
    "spa": ("4.1.0", "0b0fcbb4665189e01ab8019e591f014dd7260460de072543edd4b2cb4ede7c96"),
    "eng": ("4.1.0", "daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047"),
    "osd": ("3.04.00", "9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff"),
}
for nombre, (version, huella) in modelos.items():
    url = f"https://github.com/tesseract-ocr/tessdata/raw/{version}/{nombre}.traineddata"
    datos = urllib.request.urlopen(url, timeout=120).read()
    if hashlib.sha256(datos).hexdigest() != huella:
        raise SystemExit(f"{nombre}.traineddata no coincide con su huella")
    open(os.path.join(destino, f"{nombre}.traineddata"), "wb").write(datos)
PY

# --- Imagen final ------------------------------------------------------------
FROM python
# Solo lo que corre: tesseract, libstdc++ (la pide node) y tini (PID 1: reenvía
# las señales al servidor y recoge los procesos huérfanos de los trabajadores).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr libstdc++6 tini \
    && rm -rf /var/lib/apt/lists/*
ENV TESSDATA_PREFIX=/opt/tessdata
COPY --from=tessdata /opt/tessdata /opt/tessdata
RUN tesseract --list-langs 2>&1 | grep -qx spa && tesseract --list-langs 2>&1 | grep -qx osd
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=python-deps /opt/venv /opt/venv
COPY --from=auditoria-python /auditoria/pip-audit.txt /opt/venv/pip-audit.txt

WORKDIR /app
COPY motor/ motor/
COPY motores/ motores/
COPY puente/ puente/
COPY catalogo/ catalogo/
COPY --from=servidor /src/servidor/package.json servidor/package.json
COPY --from=servidor /src/servidor/node_modules servidor/node_modules
COPY --from=servidor /src/servidor/dist servidor/dist
COPY --from=web /src/web/dist web/dist

# El código queda de root (solo lectura para el usuario de la app). Lo único que
# escribe la app es /datos:
#   /datos/estado    ajustes, corridas y el trabajo de cada una (volumen)
#   /datos/archivos  el espacio de archivos de los flujos (archivo_leer/guardar, SQLite; volumen)
#   /datos/bandeja   los correos de prueba (montaje de solo lectura)
RUN useradd -m -u 1001 app && mkdir -p /datos/estado /datos/bandeja /datos/archivos && chown -R app:app /datos
USER app

ENV PATH=/opt/venv/bin:$PATH \
    PYTHON=/opt/venv/bin/python \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RUTA_DATOS=/datos/estado \
    RUTA_BANDEJA=/datos/bandeja \
    RUTA_ARCHIVOS=/datos/archivos \
    GOOGLE_APPLICATION_CREDENTIALS=/secretos/adc.json \
    PUERTO=8080

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:8080/api/salud').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "servidor/dist/servidor.js"]
