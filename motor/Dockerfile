# Python CLI for PQRD Radicacion
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    tesseract-ocr \
    tesseract-ocr-spa \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

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
RUN mkdir -p /app/workspace /app/logs

# Change ownership
RUN chown -R appuser:appuser /app

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
