# ms-radicacion-ia-tutelas

Sistema de radicación automática de tutelas para Positiva ARL. Extrae correos de Outlook, procesa con Gemini AI, y radica en SGDEA enviando archivos individuales (no ZIP).

## Estructura

```
├── Dockerfile
├── azure-pipelines.yml
├── requirements.txt
├── run.py                    # Punto de entrada (Cloud Run Job)
├── Tutelas/
│   ├── config.py             # Credenciales
│   ├── main.py               # Orquestador principal
│   ├── models/
│   │   └── correo.py         # Dataclasses
│   └── services/
│       ├── outlook_service.py
│       ├── redis_service.py
│       ├── ai_extractor.py
│       ├── juzgados_service.py
│       └── sgdea_service.py
└── juzgados_actualizada.csv  # Base de datos de juzgados (6,919)
```

## Ejecución Local

```bash
pip install -r requirements.txt
python run.py
```

## Despliegue

Se despliega como **Cloud Run Job** en GCP mediante Azure Pipelines.

```bash
# Build Docker
docker build -t ms-radicacion-ia-tutelas .

# Run local
docker run ms-radicacion-ia-tutelas
```

## Flujo de Procesamiento

1. Lee correos de carpeta **"RADICACION CON IA"** en Outlook
2. Valida dominio del remitente
3. Detecta si es tutela
4. Extrae radicado de 23 dígitos con **Gemini AI**
5. Verifica duplicados en **Redis**
6. Busca juzgado en CSV por código
7. Descarga adjuntos (filtra imágenes de Outlook)
8. Radica en **SGDEA** con archivos individuales
9. Mueve correo a carpeta de éxito o revisión manual

## Servicios Externos

| Servicio | Uso |
|----------|-----|
| Microsoft Graph | Lectura de correos (ROPC auth) |
| Redis Cloud | Deduplicación por hash |
| Google Gemini | Extracción de radicado/cédula |
| SGDEA API | Radicación de tutelas |
