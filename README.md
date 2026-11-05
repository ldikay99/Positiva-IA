# Positiva IA

Visor web del pipeline de radicación de PQRD de Positiva ARL. Muestra el flujo completo como un
diagrama de nodos conectados, deja tomar un correo de la bandeja, procesarlo y ver en tiempo real
cada etapa, cada vuelta del agente de IA, cada herramienta que usa y lo que se enviaría a SGDEA.
Cada nodo se abre con un clic para ver sus reglas y ajustar sus parámetros.

El motor es el mismo código que corre en producción (`motor/`, rama `fix/logs-campos-radicacion`).
El visor no lo reescribe ni lo modifica: lo observa desde fuera. Así lo que se ve y se ajusta aquí
es exactamente lo que hace el servicio real.

## Qué se puede hacer

- **Bandeja:** buscar y filtrar los correos descargados, ver cómo terminaron en producción y
  procesar uno, varios o los primeros N.
- **Flujo:** el pipeline como diagrama manipulable (los nodos se arrastran y la posición se
  recuerda). Durante una corrida la etapa en curso se ilumina, las conexiones recorridas se marcan
  y cada nodo muestra su tiempo. Al terminar, el desenlace (radicado, manual, duplicado,
  reenviado, aplazado) queda resaltado.
- **Nodo:** descripción, reglas, desenlaces posibles y parámetros. Los que el motor lee en tiempo
  de ejecución se ajustan desde la web y aplican a las corridas siguientes; los que están fijos en
  el código se muestran con la razón.
- **Corrida:** la línea de tiempo en vivo (etapas, pedidos al modelo con sus tokens, herramientas
  con argumentos y respuesta, consultas al registro de personas, logs), los datos encontrados con
  su origen y confianza, y el payload que se radicaría en SGDEA.
- **Corridas, Proveedores y APIs, Herramientas del agente, Ajustes:** el resumen de lo procesado
  frente a producción, cada sistema externo con sus operaciones, las herramientas del agente, el
  modelo de Vertex y la concurrencia.

## Seguridad

Nada se escribe en un sistema real. El motor corre con `MODO_TEST`: el envío a SGDEA se corta y
se muestra como simulado, el buzón es una carpeta de disco de solo lectura y las credenciales de
Graph, SGDEA, Redis y Supabase son falsas. Sí se consultan Vertex AI y el registro de personas,
que son lecturas.

Los correos de la bandeja traen datos personales y de salud: la carpeta se monta en solo lectura,
no se versiona y no sale de la máquina donde corre el visor.

## Arquitectura

| Capa | Tecnología | Qué hace |
|---|---|---|
| `web/` | React 19, Vite, React Flow, Zustand | Interfaz: bandeja, diagrama, paneles |
| `servidor/` | TypeScript 7, Node 24, Fastify, WebSocket | API, cola de corridas, pool de trabajadores, eventos en vivo |
| `puente/` | Python | Trabajador que corre el motor sobre un correo y emite cada paso |
| `motor/` | Python | Pipeline de producción, sin cambios |
| `catalogo/` | JSON | Mapa del pipeline: nodos, reglas, parámetros, herramientas, proveedores |

El servidor mantiene un pool de trabajadores de Python (uno por correo simultáneo). Cada
trabajador carga el motor una sola vez y procesa los correos que le asigna la cola; los eventos
llegan al navegador por WebSocket en cuanto ocurren.

## Correr con Docker

```bash
cp .env.example .env      # y completar RUTA_CREDENCIALES y RUTA_BANDEJA
docker compose up --build
```

Abrir http://localhost:8080.

`RUTA_CREDENCIALES` es el archivo de credenciales de Google con acceso a Vertex AI en el proyecto
`positiva-sgda-prd-416417` (el ADC de `gcloud auth application-default login` o una llave de
cuenta de servicio). `RUTA_BANDEJA` es la carpeta de correos: una subcarpeta por correo con
`mensaje.json` (formato de Microsoft Graph) y sus adjuntos en `adj/`.

## Desarrollo

```bash
cd servidor && npm install && npm run dev     # API en :8080
cd web && npm install && npm run dev          # interfaz en :5173 con proxy a la API
```

En desarrollo el servidor usa `py`/`python3` local con las dependencias de
`motor/requirements.txt`, y las rutas se ajustan con `RUTA_BANDEJA`, `RUTA_DATOS` y `PYTHON`.

## Actualizar el motor

`motor/` es una copia del servicio. Para traer una versión nueva, reemplazar la carpeta por el
contenido de la rama correspondiente y regenerar `catalogo/catalogo.json` si cambiaron etapas,
reglas o parámetros.
