# Positiva IA

Visor web del pipeline de radicación de PQRD de Positiva ARL. Muestra el flujo completo como un
diagrama de nodos conectados, deja tomar un correo de la bandeja, procesarlo y ver en tiempo real
cada etapa, cada vuelta del agente de IA, cada herramienta que usa y lo que se enviaría a SGDEA.
Cada nodo se abre con un clic para ver sus reglas y ajustar sus parámetros.

El motor es el mismo código que corre en producción (`motor/`, rama `fix/logs-campos-radicacion`).
El visor no lo reescribe ni lo modifica: lo observa desde fuera. Así lo que se ve y se ajusta aquí
es exactamente lo que hace el servicio real.

## Qué se puede hacer

- **Flujo:** el pipeline como diagrama de nodos conectados por cables con flecha. Mientras un
  correo se procesa, la corriente recorre los cables, la etapa en curso late y cada nodo muestra
  su tiempo, las vueltas del modelo y los tokens. Los nodos se arrastran sin mover la cámara y la
  posición se recuerda; hay minimapa, encuadre y modo «seguir en vivo».
- **Inspector de nodo:** al hacer clic en un nodo se editan sus parámetros con el editor que le
  corresponde: prompts del agente y del revisor (con resaltado), descripciones de las
  herramientas que lee el modelo, umbrales de confianza con deslizador, reglas de búsqueda
  (expresiones regulares) con probador en vivo contra el correo abierto, listas, tablas e
  interruptores. Se editan varios y se aplican juntos: rigen desde el próximo correo, sin
  reconstruir ni desplegar. También muestra las reglas de la etapa y lo que hizo en la corrida.
- **Bandeja:** buscar y filtrar los correos, seleccionarlos con su casilla y procesarlos con un
  botón explícito. Un clic en la fila abre el correo y sus adjuntos en el inspector.
- **Panel inferior:** la corrida en vivo (etapas, pedidos y respuestas del modelo, herramientas,
  consultas, escrituras simuladas), los logs del servicio y la consola. Se redimensiona y se
  pliega, igual que la bandeja y el inspector.
- **Corridas:** resultados frente a producción con los mismos correos (cuántos que allá fueron a
  manual aquí se radican y al revés), distribución de desenlaces, tokens y tiempos.
- **Logs:** todos los eventos de todas las corridas en vivo, con filtros y pausa.
- **Herramientas y Proveedores:** cada herramienta del agente y cada sistema externo con su uso
  real en la sesión (llamadas, tiempo promedio, fallos) y sus operaciones.
- **Configuración:** modelo de Vertex, temperatura, capacidad, cambios activos frente al código
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
