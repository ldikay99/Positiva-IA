# Python CLI for PQRD Radicacion
# Base fijada por digest: python:3.12.16-slim-trixie (la serie 3.12-slim del job).
FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

WORKDIR /app

# Install system dependencies (sin gcc: todas las dependencias se instalan de ruedas)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-spa \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Los modelos de OCR con los que se midio el pipeline. El paquete de Debian
# trae los modelos "fast", que leen peor los escaneos de cedulas e historias
# clinicas; estos son los de tesseract-ocr/tessdata (Apache-2.0), fijados por
# version y verificados por su sha256 para que la imagen lea igual que las
# mediciones.
ENV TESSDATA_PREFIX=/opt/tessdata
RUN mkdir -p /opt/tessdata && python -c "\
import hashlib, urllib.request; \
base = 'https://github.com/tesseract-ocr/tessdata/raw/'; \
modelos = {'spa': ('4.1.0', '0b0fcbb4665189e01ab8019e591f014dd7260460de072543edd4b2cb4ede7c96'), \
           'eng': ('4.1.0', 'daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047'), \
           'osd': ('3.04.00', '9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff')}; \
[open(f'/opt/tessdata/{n}.traineddata', 'wb').write(d) for n, (v, h) in modelos.items() \
 for d in [urllib.request.urlopen(f'{base}{v}/{n}.traineddata', timeout=120).read()] \
 if hashlib.sha256(d).hexdigest() == h or (_ for _ in ()).throw(SystemExit(f'sha256 de {n} no coincide'))]"

# Create non-root user
RUN useradd -m -u 1001 appuser

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config/ ./config/
COPY models/ ./models/
COPY services/ ./services/
COPY business_rules/ ./business_rules/
COPY utils/ ./utils/
COPY orchestrator/ ./orchestrator/
COPY logging_system/ ./logging_system/
COPY main.py .

# Pipeline nuevo: extraccion determinista, agente unico y compuerta.
COPY pipeline/ ./pipeline/
COPY servidor_mcp/ ./servidor_mcp/
COPY correr.py .

# El workspace de cada correo y los informes por ejecucion. El sistema de
# archivos del Job es efimero, asi que solo sirven dentro de la corrida: el
# informe tambien se imprime, y ahi lo recoge Cloud Logging.
# salidas_prueba es el TEST_OUTPUT_DIR por defecto del MODO_TEST.
RUN mkdir -p /app/workspace /app/logs /app/salidas_prueba

# Change ownership: el codigo queda de root (solo lectura para appuser); el
# usuario solo escribe en sus carpetas de trabajo.
RUN chown appuser:appuser /app/workspace /app/logs /app/salidas_prueba

# Switch to non-root user
USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Donde se guardan los informes de cada corrida. El sistema de archivos del Job
# es efimero: sin un bucket, la serie historica no llega a acumular nada. Si la
# escritura falla la corrida NO se cae, porque el informe completo sale ademas
# por la salida estandar y ahi lo recoge Cloud Logging.
ENV RUTA_LOGS=gs://positiva-sgdea-correos/ruta_logs_ias/pqrd

# Cloud Run Job entrypoint
#
# `correr.py` exige `--vertex`: sin la bandera se detiene antes de construir el
# modelo, en vez de elegir un backend por su cuenta. No hay backend de reserva,
# y cada llamada a Vertex se factura, asi que ninguna corrida gasta por
# descuido, ni aqui ni en las pruebas.
CMD ["python", "correr.py", "--vertex"]
