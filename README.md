# Positiva IA

Visor web del pipeline de radicación de PQRD de Positiva ARL. Muestra el flujo completo como un
diagrama de nodos conectados, deja tomar un correo de la bandeja, procesarlo y ver en tiempo real
cada etapa, cada vuelta del agente de IA, cada herramienta que usa y lo que se enviaría a SGDEA.
Cada nodo se abre con un clic para ver sus reglas y ajustar sus parámetros.

El motor es el mismo código del servicio (`motor/`, copiado de un commit de `mcp-fix`; el commit
exacto queda en `motor/ORIGEN`).
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
- **Adjuntos hoja por hoja:** cada adjunto del correo con la miniatura de cada hoja y, al elegir una,
  su imagen junto al texto que salió de ella. Para cada hoja se ve cómo se leyó: texto propio del
  PDF, Tesseract a 300 ppp con su confianza, si se buscó el giro y si la lectura girada se quedó,
  si se releyó a 200 ppp y si la transcribió Gemini con visión, y qué tipo de hoja le asignó el
  índice (cédula, historia clínica…). Mientras el motor lee, la hoja en curso se muestra con una
  línea que la recorre y las miniaturas van apareciendo también sobre el nodo de conversión del
  flujo. Al lado, cada campo con su recorrido —lo que encontró el algoritmo o la IA y en qué hoja,
  si la compuerta lo rechazó, qué corrigió la IA, qué dijo el revisor— y el dictamen final; y cada
  búsqueda de la IA o del revisor en el índice (texto exacto o pregunta por relevancia) con la hoja
  y el fragmento que encontró, resaltado en el texto al elegirlo. Todo sale de los eventos reales
  de la corrida.
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

Abrir http://localhost:8080 (o el puerto de `PUERTO` en `.env`).

La imagen trae Tesseract 5 con el modelo estándar de español (`tessdata` 4.1.0) y el de
orientación, descargados con su huella fija en `TESSDATA_PREFIX=/opt/tessdata`, y PDFium
(`pypdfium2`, Apache-2.0/BSD-3) para dibujar las hojas en la web. Las imágenes y el texto por hoja
de cada corrida quedan en el volumen de datos (`trabajo/visor/<corrida>`, se conservan las 80 más
recientes) y solo los sirve el servidor local.

Licencias: lo que agrega el visor es MIT, BSD o Apache-2.0. El motor, en cambio, depende de
PyMuPDF (AGPL-3.0) para leer los PDF (`pipeline/conversion.py` y `pipeline/volumen.py`); el visor no
lo cambia porque usa el motor tal cual. Para quitarlo habría que reemplazarlo en el motor.

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

`motor/` es una copia del servicio tomada con `git archive` de un commit de `mcp-fix` (nunca del
árbol de trabajo), sin corpus, espacios de trabajo, datos ni `.env`. Se puede repetir las veces que
haga falta; deja `motor/` idéntico al commit pedido y regenera el catálogo:

```bash
catalogo/sincronizar_motor.sh            # el HEAD de ../../POSITIVA_IA_RADICACION_PQRD/mcp-fix
catalogo/sincronizar_motor.sh <commit>   # otro commit; FUENTE=/ruta cambia el repositorio
docker compose up -d --build
```

Si el motor cambia de etapas, renombra funciones que observa `puente/trabajador.py` o
`puente/visor_adjuntos.py`, o agrega nodos, hay que revisar esos ganchos y las descripciones de
`catalogo/generador/build.py`. Un gancho que ya no encuentra su función se anuncia en la consola de
la corrida con el prefijo `[visor]`; el motor sigue igual.
