# CLAUDE.md

Guía para Claude Code en este repositorio.

## Proyecto

**ms-radicacion-ia-tutelas** - Sistema de radicación automática de tutelas para Positiva ARL. Se despliega como Cloud Run Job en GCP.

**Idioma:** Español, terminología legal colombiana.

## Arquitectura

```
├── run.py                 # Punto de entrada (Cloud Run Job)
├── Dockerfile
├── azure-pipelines.yml
├── requirements.txt
├── Tutelas/
│   ├── config.py          # Credenciales
│   ├── main.py            # Orquestador
│   ├── models/correo.py   # Dataclasses
│   └── services/
│       ├── outlook_service.py
│       ├── redis_service.py
│       ├── ai_extractor.py
│       ├── juzgados_service.py
│       └── sgdea_service.py
└── juzgados_actualizada.csv
```

## Ejecución

```bash
# Local
python run.py

# Docker
docker build -t ms-radicacion-ia-tutelas .
docker run ms-radicacion-ia-tutelas
```

## Flujo de Datos

1. Leer correos de carpeta "RADICACION CON IA" (Outlook)
2. Validar dominio remitente
3. Detectar tutela por palabras clave
4. Extraer radicado 23 dígitos con Gemini AI
5. Verificar duplicado en Redis
6. Buscar juzgado por código (primeros 12 dígitos)
7. Descargar adjuntos (filtrar imágenes de Outlook)
8. Radicar en SGDEA con archivos individuales (no ZIP)
9. Mover correo a carpeta de éxito o revisión manual

## Patrones Clave

- Radicado: exactamente 23 dígitos numéricos
- Código juzgado: primeros 12 dígitos del radicado
- Dominios válidos: `cendoj.ramajudicial.gov.co`, `notificacionesrj.gov.co`, `positiva.gov.co`
- Campos vacíos del juzgado: dirección="", teléfono="Vacio", celular="3000000000"

## Carpetas de Outlook

- Entrada: "RADICACION CON IA"
- Éxito: "Radicacion Exitosa"
- Fallo: "Revision Manual"
