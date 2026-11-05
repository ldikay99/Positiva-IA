# Positiva IA

Visor web del pipeline de radicación de PQRD de Positiva ARL. Muestra el flujo completo como un
diagrama de nodos conectados, deja tomar un correo de la bandeja, procesarlo y ver en tiempo real
cada etapa, cada vuelta del agente de IA, cada herramienta que usa y lo que se enviaría a SGDEA.
Cada nodo se abre con un clic para ver sus reglas y ajustar sus parámetros.

El motor es el mismo código que corre en producción (`motor/`, rama `fix/logs-campos-radicacion`).
El visor no lo reescribe ni lo modifica: lo observa desde fuera. Así lo que se ve y se ajusta aquí
es exactamente lo que hace el servicio real.

## Qué se puede hacer

- **Flujo:** el pipeline como diagrama de nodos agrupados por fase (recepción, clasificación,
  documentos y OCR, extracción e IA, validación, radicación), con un nodo de entrada que muestra
  el correo en proceso y uno de salida con su desenlace. Cada nodo dice qué es —filtro, OCR,
  búsqueda por reglas, IA, datos, validación, integración con SGDEA— con su ícono y su color.
  Los desvíos van por pasillos hacia la banda de salidas, sin cruzar los nodos. Mientras un
  correo se procesa la corriente recorre los cables con flechas, la etapa en curso late y la
  cámara la acompaña. De lejos los nodos muestran solo su nombre en grande.
- **Edición del flujo:** clic derecho en el lienzo o en un nodo para agregar reglas o notas,
  enfocar, restablecer los ajustes de un nodo o copiar su nombre técnico. Buscador de nodos
  (Ctrl K), controles de zoom, enfoque del nodo elegido (F), bloqueo de posiciones y minimapa.
- **Reglas personalizadas:** se crean desde el lienzo y el motor las ejecuta desde el próximo
  correo. Se evalúan al entrar (asunto, cuerpo, remitente o adjuntos) o antes de validar
  (cualquier campo ya extraído) y pueden mandar a revisión manual con un motivo propio, dejar
  un aviso o fijar un valor. Se activan, duplican y eliminan sin tocar el código.
- **Inspector de nodo:** aparece al elegir un nodo y se cierra al hacer clic fuera o con Esc;
  se ensancha arrastrando su borde. Muestra de dónde llega y hacia dónde sigue el nodo, sus
  reglas del motor con los parámetros que usa cada una, y en la extracción qué busca cada campo,
  con qué reglas y qué encontró en la corrida. Cada parámetro se edita con el editor que le
  corresponde: prompts (con resaltado), descripciones de las herramientas, umbrales, reglas de
  búsqueda con las frases que reconocen y probador contra el correo abierto, listas, tablas e
  interruptores. Se aplican juntos y rigen desde el próximo correo.
- **Bandeja:** buscar y filtrar los correos, seleccionarlos con su casilla y procesarlos con un
  botón explícito. Un clic en la fila abre el correo y sus adjuntos.
- **Proceso en vivo:** la corrida contada por etapas: cada campo que el algoritmo buscó (si lo
  encontró, con qué confianza y dónde), cada vuelta de la IA y la herramienta que decidió usar,
  las consultas al registro, el dictamen de la compuerta y lo que se enviaría a SGDEA. Al lado,
  la solicitud se va llenando campo por campo con su origen: algoritmo, IA, registro o regla.
  También logs con filtro y consola.
- **Corridas:** resultados frente a producción con los mismos correos (cuántos que allá fueron a
  manual aquí se radican y al revés), distribución de desenlaces, tokens y tiempos.
- **Logs:** todos los eventos de todas las corridas en vivo, con filtros y pausa.
- **Herramientas y Proveedores:** cada herramienta del agente y cada sistema externo con su uso
  real en la sesión (llamadas, tiempo promedio, fallos) y sus operaciones.
- **Ajustes:** reglas personalizadas, modelo de Vertex, temperatura, capacidad, cambios activos frente al código
  con su diferencia y su botón para volver, perfiles con nombre, historial de cambios y
  exportación.

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
| `web/` | React 19, Vite, React Flow, Zustand, Motion | Interfaz: bandeja, diagrama, paneles |
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
