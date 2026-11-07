# Plan de Positiva IA

Todo lo que Jonathan ha pedido, con su estado. Se actualiza en cada entrega: nada queda solo en el chat.

- `[x]` hecho y probado
- `[~]` en curso
- `[ ]` falta

## Meta

Una plataforma de flujos de IA con nodos, más potente que n8n, donde **Vera** (o una persona en el editor) crea cualquier automatización sin tocar código: correo, buzones, Drive, bases de datos, APIs, consultas, programaciones. Todo debe funcionar de verdad, dentro de los límites (abajo), y quedar listo para que el proyecto pase a radicar en producción.

## Límites que no se negocian

- Nada escribe en sistemas reales de Positiva: SGDEA, sus buzones de producción y todo `*.positiva.gov.co` quedan bloqueados siempre.
- Los buzones de PQRD y tutelas se leen solo en modo lectura.
- Una escritura hacia afuera (enviar correo, subir a Drive, ejecutar SQL, HTTP distinto de GET) solo ocurre si **una persona** habilitó la escritura en esa conexión, con confirmación explícita. Si no, queda registrada como «escritura retenida».
- Los secretos nunca se muestran ni salen en eventos, logs, resultados ni respuestas de la API. Vera nunca recibe ni escribe claves: abre la pantalla y la persona las ingresa.
- Borrar un flujo requiere confirmación explícita con un botón.
- Licencias: solo MIT, BSD o Apache.

## 1. Vera, la asistente

- [x] Chat escrito: historial de conversaciones (crear, renombrar, borrar, elegir), sesiones independientes y contexto completo.
- [x] Adjuntos de cualquier formato, con cápsula y visor dentro del chat (nada se abre en otra ventana).
- [x] Respuestas en markdown; sin «Cómo lo hice».
- [x] Sabe en qué pantalla está la persona en cada mensaje.
- [x] Voz en vivo (Gemini Live `gemini-3.8-live`), con selector de voz, silenciar micrófono, silenciar su voz y colgar.
- [x] Voz con dos procesos: la voz resume y el proceso escrito hace lo pesado y lo deja en el chat.
- [x] Voz en paralelo: las tareas largas corren en segundo plano, ella sigue hablando y cuenta el resultado al llegar; la barra muestra cuántas tareas hay en curso.
- [x] Habla corto y natural: no se presenta, no repite y no dice «Positiva IA». Cuelga sola al despedirse.
- [x] Barra de la llamada clara, con indicador de voz propio.
- [x] Captura del micrófono con reintento y vía de respaldo.
- [x] 54 herramientas: navegación, lienzo, correspondencia, correos, corridas, logs, motor, nodos, ajustes, perfiles, correr correos y el buzón real (en prueba), versiones, duplicar, renombrar, correr flujos, conexiones (sin claves) y control del editor.
- [x] Ficha de cada nodo (`tipos_de_nodo`, `ayuda_nodo`, `reglas_de_flujos`) para explicarlos y para armar flujos.
- [x] Propone borrar con un botón; nunca borra sola.
- [x] Herramientas para el entorno de cada flujo y el manifiesto (`ver_entorno`, `definir_variable`, `quitar_variable`, `pedir_secreto`, `ver_manifiesto`, `abrir_entorno`); 60 herramientas en total.
- [ ] Probar esas herramientas contra las rutas reales cuando el motor las entregue.
- [ ] Usar los disparadores y conectores nuevos y probarlo armando automatizaciones reales con Vera, por escrito y por voz.

## 2. Motor de flujos

- [x] 18 tipos de nodo base.
- [x] Rama de error por nodo, reintentos con espera, nodos deshabilitados.
- [x] Plantillas `{{lista[campo=valor]}}` y claves con punto.
- [x] Nodo `http` seguro.
- [x] Muestra de datos por nodo.
- [~] Disparadores: manual, programado, webhook y correo nuevo; correr cualquier flujo con datos de entrada.
- [~] Conectores: correo (IMAP, SMTP, Graph), Google Drive, Postgres, MySQL, SQLite, hojas de cálculo y archivos.
- [~] Lógica: `para_cada`, ramas en paralelo y `unir`, subflujos, expresiones seguras, fechas, JSON y tablas de datos propias.
- [~] Conexiones (credenciales) cifradas, con permiso de escritura que solo da una persona.
- [~] Entorno por flujo: variables y secretos propios de cada flujo, no globales.
  - [~] `{{entorno.NOMBRE}}` en plantillas, con los secretos enmascarados siempre.
  - [~] Conexiones de un flujo o globales.
  - [~] PQRD y tutelas arrancan con su propio entorno; la configuración global actual pasa a PQRD.
- [~] Manifiesto por flujo para la revisión antes de producción.
- [~] Versiones, restaurar, duplicar y renombrar por API.

## 3. Editor visual e interfaz

- [x] Selectores y controles propios en toda la plataforma (nada con estilo del navegador).
- [x] Lienzo legible al cargar, panel de proceso plegable, cabecera sin duplicados.
- [x] Celular con «Más»; vistas que se reacomodan.
- [x] Corridas por flujo, con motivos en palabras.
- [x] Eventos `positiva:control` para lienzo, correspondencia, filtros, proceso y paneles.
- [~] Editor de flujos declarativos:
  - [~] paleta de nodos y conexiones arrastrando;
  - [~] formularios generados desde el esquema de cada nodo, con ayuda y ejemplos;
  - [~] guardar con validación, versiones, deshacer y rehacer;
  - [~] renombrar, duplicar y borrar con confirmación;
  - [~] «Correr» con formulario de entrada.
- [~] Vista «Conexiones».
- [~] Sección «Entorno» por flujo y vista del manifiesto.
- [~] Eventos de control del editor para Vera.
- [ ] Tooltips propios en lugar de los `title` nativos.

## 4. Flujos de producción

- [x] PQRD, con el motor de `mcp-fix` y su buzón real en solo lectura.
- [x] Tutelas fiel al job, con buzón real en solo lectura e hilo como unidad.
- [ ] Revisar el manifiesto de PQRD y tutelas frente a lo que corre hoy en producción (paridad).

## 5. Paso a producción (cuando aprueben)

- [x] Documento de despliegue (`docs/PRODUCCION.md`): principios, entorno y secretos de PQRD y tutelas con sus nombres reales, conexiones, cómo jala la información cada flujo, qué escribe y los pasos para salir a producción.
- [ ] Completar `docs/PRODUCCION.md` con lo que entregue el motor (manifiesto real, conexiones y entorno por flujo).
- [ ] Mapeo del entorno de cada flujo a Secret Manager o a las variables del job en Cloud Run, sin secretos en el repo.

## 6. Pendientes menores

- [ ] Descripciones del catálogo sin tildes que vienen del backend.
- [x] Limpiar del scratchpad los datos personales de los análisis (logs, buzón, OCR, corpus, capturas de casos).
- [ ] Borrar las capturas de interfaz y el clon de telar cuando terminen los agentes.
