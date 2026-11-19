# Datos sensibles: clasificación, cifrado, retención y borrado

Clasificación de cada dato que maneja Vera (ver [DATOS_E_INTEGRACIONES.md](DATOS_E_INTEGRACIONES.md)), dónde vive hoy y
cómo se protege. Sobre esta base se definen las reglas de cifrado, retención y borrado por organización, conforme a la
Ley 1581 de 2012 de protección de datos personales.

## Niveles

| Nivel | Qué es | Regla general |
|---|---|---|
| **Secreto** | Da acceso a un sistema en nombre de la organización o de una persona | Nunca en claro en disco, logs, eventos, resultados, repositorio ni respuestas; no se vuelve a mostrar después de guardarse |
| **Sensible** | Datos personales sensibles en el sentido de la Ley 1581: salud, documentos judiciales y voz | Cifrado por organización, acceso mínimo, retención corta y borrado verificable |
| **Personal confidencial** | Datos personales y contenido de trabajo de la organización | Cifrado por organización; solo la organización lo ve |
| **Interno** | Configuración y metadatos sin contenido personal | Protegido por permisos de la base y de la organización |
| **Público** | Código, documentación y plantillas del producto | Repositorio, sin datos personales |

## Clasificación

| Dato | Nivel | Dónde vive hoy | Protección actual |
|---|---|---|---|
| Credenciales de conectores y de proveedores de IA, tokens OAuth | Secreto | `credenciales.json` | AES-256-GCM; la API nunca las devuelve; enmascaradas en eventos y logs |
| Variables secretas de cada flujo | Secreto | `entornos/<flujo>.json` | AES-256-GCM, igual que las credenciales |
| Llave de cifrado de credenciales | Secreto | `CLAVE_CREDENCIALES` o `.clave-credenciales` | Archivo `0600` dentro del volumen de datos |
| Llave que firma las descargas y tokens de webhooks | Secreto | `clave_descargas`, definición del flujo | En el volumen de datos; comparación en tiempo constante |
| Identidad de la nube (ADC o llave de cuenta de servicio) | Secreto | Archivo montado desde `RUTA_CREDENCIALES` | Montado en solo lectura; fuera del repositorio |
| Correos y adjuntos con historias clínicas, certificados de discapacidad y dictámenes | Sensible | `vivo/`, `/datos/archivos/<flujo>/`, `recibidos/` | En claro en el volumen; no se versionan ni salen de la máquina |
| Texto y páginas leídos de esos documentos (OCR y visión) | Sensible | `cache/conversion/`, `trabajo/visor/` | En claro; caché limitada a 2 GB y visor a las 80 ejecuciones más recientes |
| Ejecuciones con sus eventos y campos extraídos | Sensible | `corridas/<día>.jsonl` | En claro; sin retención definida |
| Documentos judiciales (tutelas) | Sensible | Igual que los correos | Igual que los correos |
| Transcripciones de voz | Sensible | `chats/<id>.json` | En claro; el audio no se guarda |
| Conversaciones de Vera por chat y sus adjuntos | Personal confidencial | `chats/` | En claro; sin retención definida |
| Datos de identificación (nombre, documento, teléfono, dirección, correo) | Personal confidencial | Correos, ejecuciones y salidas | En claro, con los correos |
| Aprobaciones pendientes y ejecuciones aplazadas con su entrada | Personal confidencial | `aprobaciones/`, `aplazadas/` | En claro; sin retención definida |
| Tablas que guardan los flujos | Personal confidencial | `tablas/*.sqlite` | Sin cifrado; se borran con el flujo |
| Directorio de juzgados y de funcionarios | Personal confidencial | Repositorio (`motores/tutelas/juzgados_actualizada.csv`, `motor/business_rules/funcionario_directory.py`) | Versionado en el repositorio privado; debe salir del repositorio |
| Definiciones de flujos, versiones e historial de cambios | Interno | `flujos/`, `flujos_historial/` | Volumen de datos |
| Configuración, programaciones, límites y proveedor de IA | Interno | `configuracion.json` y afines | Volumen de datos |
| Telemetría y escrituras retenidas en modo prueba | Personal confidencial | `trabajo/logs`, `trabajo/grabador` | Volumen de datos; pueden incluir contenido de correos |
| Código, plantillas incluidas y documentación | Público | Repositorio | Sin secretos en el código |

## Lo que sale de la plataforma

| Destino | Qué recibe | Nivel |
|---|---|---|
| Vertex AI (y otros proveedores si una organización los configura) | Texto, hojas y adjuntos que procesa cada nodo de IA; mensajes y resultados de herramientas de cada conversación | Sensible cuando el flujo procesa salud o tutelas; la anonimización (`puente/anonimizar.py`) existe como paso opcional |
| Gemini Live | Audio del micrófono durante la llamada | Sensible |
| Sistemas conectados (Google, Microsoft, bases, mensajería) | Lo que el flujo escribe con la escritura habilitada | Según el dato |
| Enlaces de descarga | Archivos de salida, firmados y con vencimiento | Según el dato |

## Brechas que cierran las reglas de tratamiento

- El contenido de correos, adjuntos, ejecuciones y conversaciones se guarda en claro: falta cifrado en reposo por
  organización.
- No hay retención para ejecuciones, buzón, conversaciones ni aprobaciones; solo hay límites de tamaño en la caché y
  el visor.
- La anonimización antes de enviar datos sensibles a un proveedor de IA es opcional.
- Hay datos personales versionados en el repositorio.
- No hay organizaciones ni personas: cualquiera con acceso a la red ve todo (lo resuelven el modelo por organización y
  el inicio de sesión).
