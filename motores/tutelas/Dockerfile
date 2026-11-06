FROM python:3.12-slim

WORKDIR /app

# Instalar Tesseract OCR + idiomas español e inglés
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY Tutelas/ Tutelas/
COPY run.py .

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Comando para ejecutar (Cloud Run Job)
CMD ["python", "run.py"]
