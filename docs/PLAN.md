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
- [x] Probadas contra las rutas reales: definir variable, ver entorno y manifiesto.
- [x] Vera armó, validó, corrigió y corrió una automatización real (entrada manual → para cada → fecha → tabla → salida), por escrito y por voz.
- [x] Antes de correr un flujo, Vera revisa la entrada contra lo que pide su disparador y corrige sola.
- [x] Voz en paralelo sin choques: los resultados se entregan solo cuando no hay un turno abierto, una tarea idéntica en curso no se repite, el proceso escrito no repite lo que la voz ya lanzó y no se guardan marcadores ni ruido.
- [x] Conectores SQL probados contra bases reales (Postgres 16 y MariaDB 11 temporales): leen filas reales; sin permiso la escritura queda retenida; con permiso de una persona la escritura llega a la base.
- [x] Bases de datos y servidores de correo de una credencial pueden estar en IP privadas (redes internas); loopback, metadatos, enlace local, Positiva y SGDEA siguen vetados; el nodo http mantiene el bloqueo estricto.
- [ ] Probar Graph y Drive con cuentas reales (Graph: con los secretos de PQRD y tutelas copiados, en solo lectura; Drive: necesita una cuenta de servicio de prueba).

## 2. Motor de flujos

- [x] 18 tipos de nodo base.
- [x] Rama de error por nodo, reintentos con espera, nodos deshabilitados.
- [x] Plantillas `{{lista[campo=valor]}}` y claves con punto.
- [x] Nodo `http` seguro.
- [x] Muestra de datos por nodo.
- [x] Disparadores: manual, programado, webhook y correo nuevo; correr cualquier flujo con datos de entrada.
- [x] Conectores: correo (IMAP, SMTP, Graph), Google Drive, Postgres, MySQL, SQLite, hojas de cálculo y archivos.
- [x] Lógica: `para_cada`, ramas en paralelo y `unir`, subflujos, expresiones seguras, fechas, JSON y tablas de datos propias.
- [x] Conexiones (credenciales) cifradas, con permiso de escritura que solo da una persona.
- [x] Entorno por flujo: variables y secretos propios de cada flujo, no globales.
  - [x] `{{entorno.NOMBRE}}` en plantillas, con los secretos enmascarados siempre.
  - [x] Verificado: agregar, cambiar y quitar variables rige desde la siguiente corrida sin reiniciar; los secretos solo los cambia una persona, nunca se muestran y no se mezclan entre flujos.
  - [x] Conexiones de un flujo o globales.
  - [x] PQRD y tutelas arrancan con su propio entorno; la configuración global actual pasa a PQRD.
- [x] Manifiesto por flujo para la revisión antes de producción.
- [x] Versiones, restaurar, duplicar y renombrar por API.

## 3. Editor visual e interfaz

- [x] Selectores y controles propios en toda la plataforma (nada con estilo del navegador).
- [x] Lienzo legible al cargar, panel de proceso plegable, cabecera sin duplicados.
- [x] Celular con «Más»; vistas que se reacomodan.
- [x] Corridas por flujo, con motivos en palabras.
- [x] Eventos `positiva:control` para lienzo, correspondencia, filtros, proceso y paneles.
- [x] Editor de flujos declarativos:
  - [x] paleta de nodos y conexiones arrastrando;
  - [x] formularios generados desde el esquema de cada nodo, con ayuda y ejemplos;
  - [x] guardar con validación, versiones, deshacer y rehacer;
  - [x] renombrar, duplicar y borrar con confirmación;
  - [x] «Correr» con formulario de entrada.
- [x] Vista «Conexiones».
- [x] Sección «Entorno» por flujo y vista del manifiesto.
- [x] Eventos de control del editor para Vera.
- [x] Tooltips propios en toda la plataforma (capa global; el del navegador ya no aparece).

## 3b. Canal de eventos y paralelismo

- [x] Prueba de carga: 8 corridas en paralelo de 3 flujos (declarativo, tutelas, PQRD). 222 eventos, ninguno sin flujo ni cruzado; cada corrida completa, en orden, con un solo resultado e igual a su historial.
- [x] En el navegador: 4 corridas en paralelo del flujo abierto aparecen todas; la de otro flujo no se cuela.
- [x] Tras un corte del WebSocket, la web se resincroniza con el servidor y ninguna corrida queda trabada.
- [x] Al cambiar de flujo, lo que llega mientras carga se fusiona en vez de perderse.
- [x] Rótulos únicos para corridas sin correo (`manual-<fecha>-<4 caracteres>`).
- [x] Posición de los nodos guardada en el servidor (`definicion.disenio`).
- [x] Editar una conexión conserva los campos no enviados; la escritura solo se apaga si cambia algún valor.
- [x] Volumen persistente para `/datos/archivos`.

## 4. La plataforma corre todo, como un job o como cualquier automatización

Pedido de Jonathan (2026-11-07): que desde aquí se puedan hacer TODOS los flujos de IA que necesitan, con sus variables, secretos y conectores. PQRD y tutelas corren aquí con todo lo real, pero con una compuerta de radicación apagada: pasan por la simulación completa y solo radican de verdad cuando él la active. No se trata de replicar el job, sino de que la plataforma funcione como un job, como un flujo o como cualquier automatización, con IA o sin ella.

- [ ] Copiar las variables y los secretos de los jobs `ms-radicacion-ia-pqrd` y `ms-radicacion-ia-tutelas` con sus valores (Secret Manager y valores directos) al entorno cifrado de cada flujo. Ningún valor se imprime ni queda en el repo.
- [ ] Que PQRD y tutelas usen ese entorno real (no dobles) para todo lo que lee: buzón por Graph, Supabase, Redis, Postgres, SGDEA en lectura y Vertex.
- [ ] Compuerta de radicación por flujo, apagada por defecto:
  - [ ] apagada: corren completos en simulación, con todas las escrituras retenidas y visibles;
  - [ ] encendida: escriben como el job (SGDEA, mover, etiquetar y responder en el buzón, trazas en Supabase y Redis);
  - [ ] la enciende solo una persona, con confirmación explícita;
  - [ ] segunda llave en el despliegue (`PERMITIR_RADICACION_REAL=si`), sin la cual la compuerta no se puede encender.
- [ ] Programación de PQRD y tutelas igual a la del job (leída de Cloud Scheduler), configurable por flujo. Queda apagada hasta que Jonathan la encienda.
- [ ] Probar de punta a punta en simulación con el entorno real: PQRD y tutelas leen su buzón real, procesan y dejan las escrituras retenidas, sin escribir nada.
- [ ] Cualquier flujo creado aquí puede usar el mismo esquema: entorno real, programación como job y escrituras habilitadas por una persona.

## 5. Flujos de producción

- [x] PQRD, con el motor de `mcp-fix` y su buzón real en solo lectura.
- [x] Tutelas fiel al job, con buzón real en solo lectura e hilo como unidad.
- [x] Manifiesto de PQRD y tutelas verificado contra la configuración real de sus jobs, con observaciones de seguridad (`servidor/src/produccion.ts`).

## 6. Paso a producción (cuando aprueben)

- [x] Documento de despliegue (`docs/PRODUCCION.md`): principios, entorno y secretos de PQRD y tutelas con sus nombres reales, conexiones, cómo jala la información cada flujo, qué escribe y los pasos para salir a producción.
- [x] `docs/PRODUCCION.md` completo: lo verificado en los jobs, las rutas de la plataforma y el script de despliegue.
- [x] Mapeo del entorno de cada flujo a Secret Manager y Cloud Run (`scripts/manifiesto_a_gcp.py`, solo imprime comandos).

## 7. Pendientes menores

- [x] Descripciones del catálogo con tildes (`catalogo/generador/tildes.py`; corrige solo textos para leer, nunca claves, valores ni nombres de código).
- [x] Limpiar del scratchpad los datos personales de los análisis (logs, buzón, OCR, corpus, capturas de casos).
- [x] Borradas las capturas de interfaz y el clon de telar.
