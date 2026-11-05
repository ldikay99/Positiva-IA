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
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc tesseract-ocr tesseract-ocr-spa poppler-utils libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY motor/requirements.txt motor/requirements.txt
RUN pip install --no-cache-dir -r motor/requirements.txt

COPY motor/ motor/
COPY puente/ puente/
COPY catalogo/ catalogo/
COPY --from=servidor /src/servidor/package.json servidor/package.json
COPY --from=servidor /src/servidor/node_modules servidor/node_modules
COPY --from=servidor /src/servidor/dist servidor/dist
COPY --from=web /src/web/dist web/dist

RUN useradd -m -u 1001 app && mkdir -p /datos/estado /datos/bandeja && chown -R app /datos
USER app

ENV PYTHON=python \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RUTA_DATOS=/datos/estado \
    RUTA_BANDEJA=/datos/bandeja \
    GOOGLE_APPLICATION_CREDENTIALS=/secretos/adc.json \
    PUERTO=8080

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD node -e "fetch('http://127.0.0.1:8080/api/salud').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "servidor/dist/servidor.js"]
