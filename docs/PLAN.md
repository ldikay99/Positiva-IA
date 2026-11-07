# Plan de Positiva IA

Todo lo que Jonathan ha pedido, con su estado. Se actualiza en cada entrega: nada queda solo en el chat.

Estados: ✅ hecho y probado · 🔧 en curso · ⏳ pendiente

## Meta

Una plataforma de flujos de IA con nodos, más potente que n8n, donde **Vera** (o una persona en el editor) crea cualquier automatización sin tocar código: correo, buzones, Drive, bases de datos, APIs, consultas, programaciones. Todo debe funcionar de verdad, dentro de los límites (abajo), y quedar listo para que el proyecto pase a radicar en producción.

## Límites que no se negocian

- Nada escribe en sistemas reales de Positiva: SGDEA, sus buzones de producción y todo `*.positiva.gov.co` quedan bloqueados siempre.
- Los buzones de PQRD y tutelas se leen solo en modo lectura.
- Una escritura hacia afuera (enviar correo, subir a Drive, ejecutar SQL, HTTP distinto de GET) solo ocurre si **una persona** habilitó la escritura en esa conexión, con confirmación explícita. Si no, queda registrada como «escritura retenida».
- Los secretos nunca se muestran ni salen en eventos, logs, resultados ni respuestas de la API. Vera nunca recibe ni escribe claves: abre la pantalla y la persona las ingresa.
- Borrar un flujo requiere confirmación explícita con un botón.
- Licencias: solo MIT, BSD o Apache.

## Vera, la asistente

- ✅ Chat escrito: historial de conversaciones (crear, renombrar, borrar, elegir), adjuntos de cualquier formato con visor dentro del chat, contexto completo y markdown.
- ✅ Sabe en qué pantalla está la persona en cada mensaje.
- ✅ Voz en vivo (Gemini Live `gemini-3.8-live`): selector de voz, silenciar micrófono, silenciar su voz y colgar.
- ✅ La voz trabaja con dos procesos: la voz resume y el proceso escrito hace lo pesado y lo deja en el chat.
- ✅ Voz en paralelo: las tareas largas corren en segundo plano; ella sigue hablando y cuenta el resultado cuando llega.
- ✅ Habla corto y natural: no se presenta, no repite y no dice «Positiva IA». Cuelga sola al despedirse.
- ✅ 54 herramientas: navegar, lienzo, correspondencia, correos, corridas, logs, motor, nodos, ajustes, perfiles, correr correos y el buzón real (en prueba), versiones, duplicar, renombrar, correr flujos, conexiones (sin claves) y control del editor.
- ✅ Ficha de cada nodo (`tipos_de_nodo`, `ayuda_nodo`, `reglas_de_flujos`) para explicar nodos y armar flujos.
- ⏳ Herramientas para el entorno de cada flujo (ver nombres y valores no secretos, definir variables no secretas) y el manifiesto. Depende del motor 🔧.
- ⏳ Usar los disparadores y conectores nuevos cuando el motor los entregue, y probarlo armando automatizaciones reales con Vera.

## Motor de flujos

- ✅ 18 tipos de nodo; rama de error por nodo, reintentos con espera, nodos deshabilitados, plantillas `{{lista[campo=valor]}}` y nodo `http` seguro.
- ✅ Muestra de datos por nodo.
- 🔧 Disparadores: manual, programado, webhook y correo nuevo; correr cualquier flujo con datos de entrada.
- 🔧 Conectores: correo (IMAP, SMTP, Graph), Google Drive, Postgres, MySQL, SQLite, hojas de cálculo y archivos.
- 🔧 Lógica: ciclos (`para_cada`), ramas en paralelo y `unir`, subflujos, expresiones seguras, fechas, JSON y tablas de datos propias.
- 🔧 Conexiones (credenciales) cifradas, con permiso de escritura que solo da una persona.
- 🔧 **Entorno por flujo**: variables y secretos propios de cada flujo, no globales.
  - `{{entorno.NOMBRE}}` en plantillas; los secretos se enmascaran siempre.
  - Las conexiones pueden ser de un flujo o globales.
  - PQRD y tutelas arrancan sus trabajadores con su propio entorno; la configuración global actual pasa a PQRD.
- 🔧 **Manifiesto por flujo** para la revisión antes de producción: disparador, nodos, conexiones externas, variables (sin valores), escrituras y si están habilitadas, programación y webhook.

## Editor visual

- ✅ Selectores y controles propios en toda la plataforma (nada con estilo del navegador).
- ✅ Lienzo legible al cargar, panel de proceso plegable, celular con «Más», corridas por flujo.
- 🔧 Editor de flujos declarativos:
  - paleta, conexiones arrastrando, formularios generados desde el esquema de cada nodo, con ayuda y ejemplos;
  - guardar con validación, versiones, deshacer y rehacer;
  - renombrar, duplicar y borrar con confirmación;
  - «Correr» con formulario de entrada.
- 🔧 Vista «Conexiones».
- 🔧 Sección «Entorno» por flujo y vista del manifiesto.
- 🔧 Eventos `positiva:control` para que Vera maneje el editor.

## Producción (cuando aprueben)

- ⏳ Documento de despliegue: cómo se manejan las variables de entorno y los secretos (Secret Manager en GCP: qué va ahí y qué en el entorno del flujo), cómo se conecta cada cuenta (Graph con su app registrada, Google con su cuenta de servicio, bases de datos), cómo jala la información cada flujo y qué escribe.
- ⏳ Mapeo del entorno por flujo a Secret Manager o a variables del job en Cloud Run, sin secretos en el repo.
- ⏳ Revisar el manifiesto de PQRD y tutelas frente a lo que corre hoy en producción (paridad).

## Pendientes menores

- ⏳ Algunas descripciones del catálogo vienen sin tildes desde el backend.
- ⏳ Reemplazar los `title` nativos por tooltips propios.
- ⏳ Al final, limpiar el scratchpad de datos personales (logs, capturas, buzón, OCR).
