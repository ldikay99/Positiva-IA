# syntax=docker/dockerfile:1
# Radicación de tutelas (Cloud Run Job). Dos etapas: las dependencias se instalan
# en un venv y la imagen final solo lleva el venv, tesseract y el código, con un
# usuario sin privilegios.

# python:3.12.16-slim-trixie (la serie 3.12-slim de siempre, fijada por digest)
FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS python

FROM python AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

FROM python
# Instalar Tesseract OCR + idiomas español e inglés
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /opt/venv /opt/venv

WORKDIR /app
# Copy application code (de root: solo lectura para el usuario de la app)
COPY Tutelas/ Tutelas/
COPY run.py .

# salidas_prueba: el TEST_OUTPUT_DIR por defecto del MODO_TEST, lo único que escribe.
RUN useradd -m -u 1001 appuser \
    && mkdir -p /app/salidas_prueba \
    && chown appuser:appuser /app/salidas_prueba
USER appuser

# Set environment variables
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Comando para ejecutar (Cloud Run Job)
CMD ["python", "run.py"]
