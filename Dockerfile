# Positiva IA: visor web del pipeline de radicación de PQRD.
# Tres etapas: la web (React), el servidor (TypeScript 7) y la imagen final
# con Python para el motor y Node para el servidor.

FROM node:24-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM node:24-slim AS servidor
WORKDIR /src/servidor
COPY servidor/package.json servidor/package-lock.json ./
RUN npm ci
COPY servidor/ ./
RUN npm run build && npm prune --omit=dev

FROM node:24-slim AS node

FROM python:3.12-slim
# Tesseract 5 (Apache-2.0). El motor lee con el modelo estándar de español
# (tessdata 4.1.0, LSTM), no con el rápido que trae el paquete de Debian: es con
# el que se midieron sus umbrales y el que lleva la imagen del job. Van también
# el de inglés y el de orientación (osd), que usa para decidir si una hoja está
# de lado. Se descargan con su huella fija.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc tesseract-ocr libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
ENV TESSDATA_PREFIX=/opt/tessdata
RUN mkdir -p "$TESSDATA_PREFIX" && python - <<'PY'
import hashlib, os, urllib.request
destino = os.environ["TESSDATA_PREFIX"]
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
RUN tesseract --list-langs 2>&1 | grep -qx spa && tesseract --list-langs 2>&1 | grep -qx osd
COPY --from=node /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY motor/requirements.txt motor/requirements.txt
COPY puente/requirements.txt puente/requirements.txt
COPY motores/tutelas/requirements.txt motores/tutelas/requirements.txt
RUN pip install --no-cache-dir -r motor/requirements.txt -r puente/requirements.txt -r motores/tutelas/requirements.txt
# pymongo sin dnspython (ISC): ver puente/requirements.txt
COPY puente/requirements-sin-dependencias.txt puente/requirements-sin-dependencias.txt
RUN pip install --no-cache-dir --no-deps -r puente/requirements-sin-dependencias.txt

COPY motor/ motor/
COPY motores/ motores/
COPY puente/ puente/
COPY catalogo/ catalogo/
COPY --from=servidor /src/servidor/package.json servidor/package.json
COPY --from=servidor /src/servidor/node_modules servidor/node_modules
COPY --from=servidor /src/servidor/dist servidor/dist
COPY --from=web /src/web/dist web/dist

# /datos/archivos: el espacio de archivos de los flujos (archivo_leer/guardar, SQLite).
# Para que sobreviva a una reconstrucción, móntalo como volumen.
RUN useradd -m -u 1001 app && mkdir -p /datos/estado /datos/bandeja /datos/archivos && chown -R app /datos
USER app

ENV PYTHON=python \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RUTA_DATOS=/datos/estado \
    RUTA_BANDEJA=/datos/bandeja \
    RUTA_ARCHIVOS=/datos/archivos \
    GOOGLE_APPLICATION_CREDENTIALS=/secretos/adc.json \
    PUERTO=8080

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD node -e "fetch('http://127.0.0.1:8080/api/salud').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "servidor/dist/servidor.js"]
