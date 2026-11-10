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
- [x] Graph probado de verdad (lectura del buzón real de PQRD con sus secretos copiados).
- [ ] Probar Drive con una cuenta de servicio real (falta una cuenta de prueba).

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

### 2b. Búsqueda robusta en todos los nodos (pedido 2026-11-07)

Al buscar, el nodo encuentra lo que se busca sin importar mayúsculas, tildes, espacios ni mezclas, con IA o sin ella. En cada nodo se pueden agregar los campos de búsqueda que hagan falta.

- [x] `igual` y `contiene` (filtro y condición), categorías de IA, `bifurcar_por_valor`, `buscar_en_tabla` y `en_lista` ya ignoran mayúsculas, tildes y espacios.
- [x] Regex (`coincide`, `no_coincide`, `extraer_regex`, `regex_extraer`, `regex_reemplazar`, nombre de adjunto) que ignoran tildes, devolviendo el texto original encontrado (`puente/conectores/busqueda.py`).
- [x] Varias condiciones por nodo (`condiciones` + «todas» o «alguna»), cada una sobre uno o varios campos; compatible con los flujos de hoy.
- [x] Operadores nuevos:
  - [x] de texto: no contiene, distinto, empieza con, termina con, palabra completa, contiene alguna / todas de una lista, en lista, parecido (tolera errores de escritura);
  - [x] de números y fechas: mayor, menor, mayor o igual, menor o igual, entre, antes de, después de, entre fechas.
- [x] Opciones por condición: distinguir mayúsculas y tildes solo si se pide (por defecto se ignoran).
- [x] Mismos operadores y normalización en `tabla_consultar` (`filtro` {campo: valor} o lista, y `condiciones`).
- [x] Modo de comparación por condición: flexible (ignora mayúsculas, tildes y espacios), exacta (todo cuenta, incluidos espacios) o parecida (similitud con umbral); ajustes finos para distinguir mayúsculas, tildes o espacios.
- [x] Longitud como criterio en todo lo que compara o extrae:
  - [x] operadores `longitud_igual`, `longitud_mayor`, `longitud_menor` y `longitud_entre`, contando caracteres, dígitos, letras o palabras;
  - [x] `extraer_regex` con longitud mínima, máxima o exacta y solo dígitos (elige el candidato que cumple, por ejemplo el radicado de 23 dígitos y no un número más corto);
  - [x] `ia_extraer` con longitud por campo, revisada después de la respuesta del modelo;
  - [x] `validar` con longitud consistente;
  - [x] `transformar` con recortar y rellenar.
- [x] Opciones iguales en todos los nodos que comparan o extraen, para construir cualquier automatización con IA o sin ella.
- [x] Panel del nodo: «Agregar condición» y «Agregar campo» salen del esquema; ayuda y ejemplos de cada operador; Vera los conoce por la ficha del nodo.
- [x] Pruebas con textos mixtos («PETICIÓN», «peticion», «Petición  urgente», «peticiòn»).
- [x] Imagen reconstruida: el servidor acepta `condiciones` (verificado con /api/flujos/validar).

### 2b-bis. Vera arma flujos (evaluación)

- [x] Batería `scripts/evaluar_vera.py`: pedidos realistas medidos por su resultado (el flujo se guarda, valida y corre con lo esperado).
- [x] Evaluación en 6 de 6: el caso que fallaba era que «texto» estaba reservado como nombre de campo; los campos de entrada viven en `{{entrada.<nombre>}}` y ahora aceptan cualquier nombre (Python y zod).
- [x] Un caso tardó 233 s («filtro sin tildes en lista»): la primera llamada a `gemini-3-flash-preview` tardó 226,7 s (latencia de Vertex; en otras 7 corridas tardó 7–21 s); el flujo corre en 0,02 s. Del lado del servidor y del motor: `GET /api/corridas/:id?esperar=N` (la evaluación ya no pregunta cada 2 s) y errores de validación que dicen cómo arreglarse (`datos` de `tabla_guardar`, cálculos en plantillas, `expresion`). Ahora 28–44 s de punta a punta (4 de 4), el 85–90 % es el modelo. Queda para `puente/asistente.py` (de Jonathan): `correr_flujo` con `?esperar=` (ahorra 3 s) y tope de tiempo en el cliente de Vertex. Ver `docs/RENDIMIENTO.md`.

### 2c. Proveedores y modelos de IA por flujo (pedido 2026-11-07)

Cada flujo, y cada nodo de IA, elige su proveedor y su modelo. Hoy todo usa Vertex, y Vertex queda por defecto en los flujos actuales. Otros flujos pueden usar Gemini directo, OpenAI, Claude, DeepSeek, OpenRouter o cualquier servicio compatible con OpenAI. Cada flujo es independiente aunque corran al mismo tiempo.

- [x] Capa de proveedores en el motor (`puente/proveedores/`), con una sola interfaz: texto, JSON con esquema, imágenes y PDF cuando el modelo los acepte, tokens y costo.
  - [x] Vertex (Gemini con la identidad del servicio, como hoy).
  - [x] Gemini directo (API key).
  - [x] OpenAI.
  - [x] Anthropic (Claude).
  - [x] DeepSeek.
  - [x] OpenRouter.
  - [x] «Compatible con OpenAI» genérico (URL base y llave), que sirve para servicios propios o locales.
- [x] Llaves de cada proveedor como conexiones cifradas (tipos de credencial nuevos), globales o propias de un flujo.
- [x] Catálogo de modelos por proveedor, leído de su API cuando la tenga y guardado en caché, con lo que cada modelo acepta (imágenes, PDF, JSON, herramientas) y su precio por millón de tokens para estimar costos.
- [x] Proveedor y modelo por defecto en cada flujo (`definicion.ia`) y por nodo (`config.proveedor`, `config.modelo`); validación de que la conexión exista y el modelo acepte lo que el nodo le pide (por ejemplo, visión).
- [x] PQRD y tutelas fijos en Vertex (lo que usan hoy), mostrados en su manifiesto.
- [x] Cualquier proveedor que funcione, no solo los compatibles con OpenAI:
  - [x] adaptadores propios: Vertex, Gemini, OpenAI, Azure OpenAI, Anthropic, DeepSeek, Mistral, Groq, OpenRouter y locales (Ollama);
  - [x] adaptador «personalizado» por HTTP (URL, encabezados, plantilla del pedido y rutas de la respuesta) para cualquier otro, sin código;
  - [x] registro de proveedores donde agregar uno nuevo es un módulo.
- [x] Capacidades declaradas por modelo (texto, imágenes, PDF, JSON, herramientas, voz en vivo, contexto, precio), usadas por la validación y la pantalla.
- [x] Tres niveles separados que nunca se mezclan:
  - [x] IA global de la plataforma (en Ajustes);
  - [x] IA de cada flujo: usa la global o una propia, con anulación por nodo; se resuelve nodo → flujo → global y se muestra en el editor y en el manifiesto;
  - [x] IA de Vera, aparte: una para el chat escrito (debe aceptar herramientas) y otra para la voz (debe tener voz en vivo).
- [x] Vera escribe con cualquier proveedor que acepte herramientas (capa de proveedores) y habla con Vertex o Gemini directo (voz en vivo); si su proveedor de voz no tiene voz en vivo, el botón de llamar se desactiva con la razón; el chat muestra el proveedor de Vera cuando no es Vertex.
- [x] Proveedores abiertos (compatible, Ollama, personalizado, OpenRouter, Azure): un modelo fuera del catálogo se acepta para Vera solo si pasa una prueba real de herramientas.
- [x] Vera completa (chat con herramientas) probada de verdad sobre un proveedor no Vertex: adaptador estilo OpenAI (endpoint compatible de Vertex), pasó la prueba de herramientas, listó flujos y navegó; luego volvió a Vertex.
- [x] Hallazgo: las llaves `OPENAI_API_KEY` y `GOOGLE_API_KEY` del job de PQRD están vencidas o no son válidas (OpenAI 401, Google 400); el job no las usa. Quedaron como conexiones `openai-positiva` y `gemini-positiva` para cuando haya llaves vigentes.
- [x] Clientes y límites por proveedor, conexión y flujo: dos flujos con proveedores distintos a la vez no comparten estado.
- [x] Reintentos, límite de tasa y concurrencia por proveedor y por flujo; un flujo no frena a otro.
- [x] Consumo por flujo y por corrida: tokens, costo estimado, tiempo; visible en Corridas, en el tablero y en el manifiesto.
- [x] Editor: selector de proveedor y modelo en el flujo y en cada nodo de IA (con lo que acepta cada modelo); «Probar conexión».
- [x] Herramientas de IA para Vera: proveedores_ia, modelos_ia, ver_ia_flujo, configurar_ia_flujo, probar_ia, configurar_ia_global (66 herramientas en total). Pide la llave a la persona (pedir_credencial), nunca la recibe.

### 2d. Recursos y rendimiento (pedido 2026-11-07)

Mediciones y detalle en `docs/RENDIMIENTO.md`.

- [x] Límites por flujo: trabajadores, corridas simultáneas, cola y tiempo máximo por corrida; que varios flujos a la vez no se ahoguen entre sí.
  - [x] `GET/PUT /api/flujos/:id/recursos` y «Entorno › Ejecución» (ahora en todos los flujos), guardados en `<estado>/recursos.json`; PQRD sigue tomando sus trabajadores de la concurrencia de Ajustes y sus lotes «como el job» van enteros a un trabajador.
  - [x] Tope global `TRABAJADORES_MAX` repartido con justicia max-min (`servidor/src/planificador.ts`). Medido: A encola 30 (la 31 recibe 429), B llega después con 4 y espera 1,8 s, no los 30 de A; nunca más de 8 procesos.
  - [x] Tiempo máximo: el trabajador que se pasa se detiene, su corrida queda en error con el motivo y otro sigue con la cola; reinicio a mano desde el tablero.
  - [x] Los declarativos arrancan trabajadores al llegar trabajo (0,25 s) y los retiran tras 10 min sin él.
- [x] Llamadas a proveedores sin bloquear al trabajador: vueltas de `para_cada` y ramas de `paralelo` a la vez (de a 4, `concurrencia` 1–8) cuando se prueba que no cambia el resultado, con los semáforos de cada proveedor. 8 vueltas con IA: de 6,8–19,6 s a 2,1–2,4 s, mismos resultados. Conexiones reutilizadas (un cliente por proveedor y credencial en cada proceso).
- [x] Caché de conversión, OCR y visión por huella del archivo (`puente/cache_conversion.py`), LRU de 2 GB, nunca entre opciones distintas. Declarativo: 27,2 s → 0,02 s. PQRD y tutelas con la compuerta apagada; A/B en PQRD con el mismo correo (c0295, c0107, c0116): mismo desenlace, hallazgos, solicitud y texto; c0295 de 29,9 s a 6,3 s.
- [x] Medición de CPU y memoria por trabajador (`/proc`) en `GET /api/salud?flujo=` y en cada tarjeta del tablero, con avisos suaves (memoria > 80 % de su límite, corrida cerca de su tiempo máximo, arranque trabado) y reciclado de un trabajador libre que pasa su límite.
- [x] Revisado qué conviene en cada lenguaje: Node para el servidor, los eventos y el planificador; Python para el motor, la IA y el OCR. Ninguna medición justifica código nativo: el tiempo es tesseract (ya nativo) o la red del modelo.
- [x] Pruebas: reparto justo, límites y cola, trabajador trabado y reinicio, medición (`servidor/src/pruebas_plataforma.ts`, 22); vueltas y ramas a la vez, caché (aciertos, fallos, opciones aisladas, nada a medias, tope LRU) (`puente/pruebas_declarativo.py`, 141).

### 2e. Cada flujo independiente, nada quemado de PQRD (pedido 2026-11-07)

El proyecto nació como PQRD y quedan cosas de PQRD que se ven en todos los flujos. Cada flujo debe funcionar como lo que es.

- [x] La «Correspondencia» (bandeja descargada y buzón real de PQRD) solo aparece en los flujos que la usan. Un flujo nuevo no ve los correos de PQRD.
- [x] Un flujo nuevo con disparador de correo se conecta a SU buzón (conexión IMAP o Graph, carpeta) o a su bandeja de prueba propia (subir .eml/.msg), no a la de PQRD.
- [x] Nodo para subir archivos (imágenes y documentos; no video ni audio):
  - [x] disparador «archivos» y campo de archivo en «entrada manual», con arrastrar y soltar en «Correr»;
  - [x] el archivo se guarda como archivo del flujo (no como una ruta escrita a mano) y pasa por la misma lectura, OCR y visión que los adjuntos;
  - [x] también por webhook (multipart) y desde el chat de Vera (los adjuntos del chat sirven para correr un flujo). Webhook multipart hecho; falta el chat (Vera).
- [x] Renombrar, clonar y borrar a la vista en cada flujo (cabecera y tarjeta del tablero), con confirmación al borrar.
- [x] Clonar también PQRD y tutelas: la copia es una instancia propia (su entorno, conexiones, compuerta y programación) que corre el mismo motor; los originales no se borran.
- [x] Vera hace todo esto: crear, editar, renombrar, clonar (también PQRD y tutelas), borrar con botón (no los originales), correspondencia por flujo, leer el buzón propio de un flujo y correrlo con los archivos adjuntos en el chat. Probado: documento adjunto → flujo `entrada_archivos` → extrajo los 5 datos y radicó en 23 s; el flujo nuevo no ve correos de PQRD.

### 2f. Vera, la voz y el chat (pedido 2026-11-07)

- [ ] Probar en una llamada real del navegador la renovación de la sesión (más de 10 minutos) y la despedida completa.

- [x] Saludo propio al conectar, según la hora («Buenas noches, cuéntame.»; desde el 2026-11-10 no dice su nombre, ver 5g); despedidas variadas y ligadas a lo que se hizo; propone en vez de preguntar en cadena.
- [x] Cortes corregidos: la sesión de Gemini Live se renueva por dentro con su identificador de reanudación (la llamada sigue, sin perder turno ni tareas); se cuelga al terminar el turno de la despedida y el navegador deja sonar lo que falte; interrumpir a Vera exige voz más clara (eco y ruido no la cortan).
- [x] Tareas en curso en su propia franja sobre la barra de la llamada, con el nombre de cada una y «listo» al terminar.

### 3a. Detalles visuales

- [x] El fondo claro del área principal no debe tener esquinas redondeadas.

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

- [x] El entorno de PQRD y tutelas acepta exactamente los nombres de su job (con guiones solo en tutelas); cada variable del job la pone una persona.
- [x] Copiadas cifradas las 22 variables de los jobs (12 de PQRD y 10 de tutelas) con `scripts/copiar_entorno_de_job.py`; ningún valor se imprimió. (Lo hace Jonathan por `PUT /api/flujos/:id/entorno/:nombre`.)
- [x] Que PQRD y tutelas usen ese entorno real para lo que leen, con adaptadores de solo lectura (Graph GET, Supabase select por GET, Redis GET, Postgres en sesión de solo lectura). Sin credenciales, los dobles de siempre, anunciados en el evento «fuentes». SGDEA de tutelas queda simulado (ver `docs/PRODUCCION.md` 5b).
- [x] Secretos de los flujos de código enmascarados en eventos, consola, resultados, historial y errores del buzón.
- [x] Compuerta de radicación por flujo, apagada por defecto (`/api/flujos/:id/compuerta`, «Entorno › Ejecución» y pastilla en la barra):
  - [x] apagada: corren completos en simulación, con todas las escrituras retenidas y visibles;
  - [x] encendida: escriben como el job (SGDEA, mover, etiquetar y responder en el buzón, trazas en Supabase y Redis); sin probar contra sistemas reales, a propósito;
  - [x] la enciende solo una persona, con confirmación explícita; Vera no puede;
  - [x] segunda llave en el despliegue (`PERMITIR_RADICACION_REAL=si`), sin la cual la compuerta no se puede encender. No está en docker-compose.
- [x] Programación de PQRD y tutelas igual a la del job (`*/30 * * * *` y `5,25,45 6-17 * * *`, America/Bogota), configurable por flujo, sin solapes y con tope de 3600 s. Queda apagada hasta que Jonathan la encienda.
- [x] PQRD de punta a punta en simulación con el entorno real: leyó Graph y Supabase de verdad, detectó un duplicado real y retuvo sus 12 escrituras; ningún secreto en la corrida.
- [ ] Tutelas de punta a punta en simulación con el entorno real (su carpeta estaba vacía el 2026-11-07; repetir cuando llegue un correo).
- [x] Cualquier flujo creado aquí usa el mismo esquema: entorno propio (variables y secretos), disparador programado como job, conexiones con escritura que solo habilita una persona y compuerta cuando es copia de PQRD o tutelas.

## 5. Flujos de producción

- [x] PQRD, con el motor de `mcp-fix` y su buzón real en solo lectura.
- [x] Tutelas fiel al job, con buzón real en solo lectura e hilo como unidad.
- [x] Manifiesto de PQRD y tutelas verificado contra la configuración real de sus jobs, con observaciones de seguridad (`servidor/src/produccion.ts`).

### 5b. Estado de producción verificado (2026-11-07, solo lectura en GCP)

- [x] PQRD en producción radica: 3 nov 210, 4 nov 241, 5 nov 636 y 6 nov 435 radicados (de 571, 590, 1.267 y 1.170 correos); todas las ejecuciones recientes terminan bien, cada 30 min.
- [x] Tutelas en producción radica: 59 tutelas con IA en las últimas 24 h; 0 advertencias o errores en 4 días; dominios inválidos y «no es tutela» separados como corresponde.
- [x] Sin errores de autenticación (401/403) en ninguno de los dos jobs.
- [x] Las llaves `OPENAI_API_KEY` y `GOOGLE_API_KEY` del job de PQRD están vencidas o no son válidas, pero el código del job no las usa (radica con Vertex por la identidad del servicio): no afectan la radicación.
- [ ] Limpiar del job de PQRD las variables que no usa (`OPENAI_API_KEY`, `GOOGLE_API_KEY`) y del de tutelas `DATABASE_PASSWORD` en texto plano, en el PR de producción (con autorización de Jonathan).
- [ ] Cloudflare frente a SGDEA rechaza a algunos correos puntuales (`CLOUDFLARE_CHALLENGE`, desde el 5 nov 15:02): 8 correos en ~33 h; cada uno se reintenta 3 veces por ejecución y termina aplazado o en manual. Revisar con quien administra SGDEA y Cloudflare (regla del WAF que dispara con esos correos) y en el código cómo se aplazan (no reintentar cada 30 min sin fin).
- [ ] Las 3 ejecuciones de PQRD del 25 jun quedaron sin arrancar por un secreto destruido (`PQRD_SGDEA_CLIENT_SECRET` v5); ya no afecta, pero ensucian la lista: se pueden borrar.

## 5c. Que se entienda como producto (pedido 2026-11-07)

Que alguien que llega por primera vez entienda qué es, qué puede hacer y cómo hacerlo, a mano o con Vera.

### Experiencia en pantalla
- [x] Cada pantalla tiene su dirección (pedido 2026-11-07): `/inicio`, `/tablero`, `/plantillas/<plantilla>`, `/notificaciones`, `/aprobaciones`, `/conexiones/<conexión>|nueva`, `/flujos/<flujo>` (lienzo), `/flujos/<flujo>/nodo/<paso>`, `/flujos/<flujo>/correo/<caso>`, `/flujos/<flujo>/ejecucion/<id>`, `/flujos/<flujo>/historial/<ejecución>`, `/flujos/<flujo>/ejecuciones|registro|sistemas|adjuntos|herramientas|ajustes`. Atrás y adelante funcionan, recargar mantiene el lugar y un enlace abre justo eso (`web/src/rutas.ts`). Probado en el navegador: enlace directo, atrás, recargar, plantilla y conexión abiertas, paso elegido, flujo inexistente → inicio.
- [x] Inicio que explica el producto en una mirada: qué es, los flujos con su estado, lo que pasó hoy y qué hacer después (crear, correr, revisar). Es la vista de entrada (`web/src/componentes/Inicio.tsx`): qué es, el recorrido «algo lo arranca → pasos → resultado», crear desde plantilla, a mano o con Vera, «Hoy» (`GET /api/historial?desde=`) y cada flujo con su último resultado, «Correr» e «Historial».
- [x] Primeros pasos guiados para quien entra la primera vez: crear su primer flujo (a mano, desde plantilla o con Vera), correrlo y ver el resultado. Cuatro pasos que se marcan solos al hacerlos (`web/src/guia.ts`, avance por navegador en `localStorage`), se cierran y se vuelven a abrir con «Ayuda» (o Vera: `{accion:"guia", abierta}`), con glosario de las palabras de la plataforma.
- [x] Galería de plantillas de flujos listas para usar (extraer datos de documentos, clasificar correos, responder con IA, revisar tablas, informe programado, webhook a base de datos…), que se instalan como flujo propio y se ajustan. Siete en `catalogo/plantillas/` (extraer datos de documentos, clasificar correos, responder con IA, revisar una tabla, informe programado, webhook a base de datos y aprobación de pagos); las siete pasan `POST /api/flujos/validar`. Una plantilla que use un paso que el servidor todavía no conoce se lista como «disponible pronto» y no se instala. `GET /api/plantillas`, `GET /api/plantillas/:id`, `POST /api/plantillas/:id/instalar {nombre?, id_nuevo?}` (`servidor/src/plantillas.ts`). Probadas de punta a punta desde la pantalla: «Webhook a base de datos» (instalar, correr, historial, detalle, reintentar), «Revisar una tabla» (con un correo de prueba con CSV) y «Aprobación de pagos» (pausa, campana, aprobar con cambios, sigue y termina).
- [x] Lenguaje de producto en toda la interfaz: nada de jerga interna (corrida, nodo, desenlace) sin explicación; ayudas contextuales donde haga falta. En las vistas nuevas y en el riel, el tablero, el panel de ejecuciones y el panel de abajo: «ejecución», «paso», «resultado» (los flujos creados aquí dicen «Completado», no «Radicado en SGDEA»), «Simulación», glosario en la guía (`web/src/lenguaje.ts`). Lo técnico (Registro/logs, Herramientas, Sistemas, Ajustes) va separado en el riel.
- [x] Historial de ejecuciones por flujo con filtros (estado, fecha, desenlace), búsqueda, detalle legible y reintentar. `GET /api/historial?flujo=&estado=&desenlace=&desde=&hasta=&q=&limite=&desde_n=` y `POST /api/historial/:id/reintentar` (misma entrada o mismo correo; confirmación en línea) en `servidor/src/historial.ts`; vista `web/src/componentes/Historial.tsx`.
- [x] Notificaciones dentro de la plataforma: corridas que fallan, aprobaciones pendientes, conexiones vencidas, avisos de recursos. Campana en la barra y vista propia; `GET /api/notificaciones`, `POST /api/notificaciones/:id/leida`, `POST /api/notificaciones/leidas`, empujadas por el canal de eventos (`{tipo:"notificacion"}`) desde `servidor/src/notificaciones.ts`. Conexiones: las que un flujo usa y no existen, secretos sin valor y ejecuciones rechazadas con 401/403; aprobaciones: el evento `aprobacion_pendiente` y `GET /api/aprobaciones`; recursos: motor que no arranca y alertas de trabajadores; también alertas de «detenerse con alerta» y horarios que no corrieron.
- [x] Vista de cada corrida legible para quien no es técnico: qué entró, qué hizo cada paso, qué salió, por qué terminó así. `GET /api/historial/:id` arma el detalle en el servidor (pasos con su nombre, por dónde siguió, nota y datos; lo que habría escrito afuera, marcado «registrado, no enviado»).
- [x] Coherencia visual y de interacción en todas las vistas (mismos patrones de tarjetas, listas, botones, estados vacíos, cargando y error), responsiva. Vistas nuevas con los mismos estados vacío, cargando y error (`EstadoPantalla`, `Cargando`), desplegables propios, confirmaciones en línea y foco visible; probado en 1440×900 y 390×844 sin errores de página ni de consola. Arreglado de paso: los globos de ayuda se salían por el borde izquierdo y quedaban pegados al tocar en el celular.
- [x] Bandeja de aprobaciones (`web/src/componentes/Aprobaciones.tsx`): cada caso en pausa con sus datos, aprobar o rechazar con comentario y cambios en los campos editables (rechazo con confirmación en línea), contra `GET /api/aprobaciones` y `POST /api/aprobaciones/:id`; el historial une la ejecución que se pausó con la que siguió después de decidir. Probada con el paso `aprobacion_humana` en escritorio y celular.
- [x] `hoja_leer` con `origen: adjunto` encuentra los archivos subidos con `entrada_archivos` (usa `ruta_adjunto`, que mira la `_ruta` del archivo subido; prueba en `pruebas_declarativo.py` «hoja_leer origen adjunto encuentra el subido»).

- [x] Paleta en azules monocromáticos de Linktic: superficies blancas o azul claro, sin superficies oscuras; el riel lateral agrupado (Trabajo, Construir, Supervisar, «Más») cabe sin barra de desplazamiento en 1280×720 a 1920×1080, tableta y celular (revisado con capturas).

### Nodos y herramientas que un usuario necesita
Motor en `puente/nodos_producto.py` (+ `documentos.py`, `anonimizar.py`, `festivos.py`, `conectores/sheets.py`, `conectores/mensajeria.py`); fichas en `puente/nodos_declarativos.py` (VERSION 8, 74 tipos en `GET /api/nodos/tipos`, con `responder_correo` e `ia_imagen`); zod en `servidor/src/flujos.ts`. Pruebas al 2026-11-07: `pruebas_declarativo.py` 191 en verde (1 omitida: la llamada real a Vertex, que se corre con `PRUEBA_IMAGEN_REAL=si`), `pruebas_plataforma.ts` 22 y `pruebas_aprobaciones.ts` 4.

- [x] Aprobación humana (`aprobacion_humana`): la corrida queda «en espera» con su estado en `<estado>/aprobaciones/` (no ocupa trabajador, sobrevive a reinicios); una persona decide con `POST /api/aprobaciones/:id` (exige `X-Confirmado-Por-Persona: si`; Vera solo lista con `GET /api/aprobaciones?flujo=&estado=`); sigue por «aprobado», «rechazado» o «vencido» con `{{aprobacion.*}}` (comentario, cambios editables, datos); `tiempo_limite_h` + `al_vencer`; eventos `aprobacion_pendiente` y `aprobacion_resuelta`. Servidor: `servidor/src/aprobaciones.ts`. Probado: pausa → decisión → sigue en OTRO proceso de Python, una sola vez, vencimiento con y sin arista, sin secretos en disco (`AprobacionHumana`, 4) y el contrato HTTP (`servidor/src/pruebas_aprobaciones.ts`, 4).
- [x] Documentos: `documento_generar` (PDF o Word desde texto con {{variables}} o desde un .docx del flujo), `pdf_unir`, `pdf_dividir`, `documento_convertir` (sin LibreOffice: docx→pdf/txt/md, pdf→txt/md/docx/png, txt/md→pdf/docx, xlsx↔csv, imagen→pdf; lo demás lo rechaza diciendo por qué), `documento_clasificar` (IA, con evidencia y página) y `documento_comparar` (diferencias + resumen de IA). Sin dependencias nuevas (pypdfium2, python-docx, openpyxl, Pillow; PDF con generador propio). Probado (`Documentos`, 4).
- [x] Datos personales: `anonimizar` (cédulas C.C./C.E./T.I. y con puntos, NIT con dígito de verificación, nombres, celulares 3xx, fijos 60x y +57, correos, direcciones «Calle 45 # 12-30», cuentas y tarjetas con Luhn), modos enmascarar, etiqueta y seudónimo consistente; mapa para revertir solo en la corrida. Probado con formatos colombianos y que la IA externa no ve ningún dato (`DatosPersonales`, 3).
- [x] Hojas de cálculo: `hoja_escribir` (XLSX/CSV del flujo: crear, anexar, actualizar por clave, reemplazar; Google Sheets con credencial google_servicio y escritura habilitada por una persona) y `hoja_leer` origen `sheets`. Probado con Sheets falsas (`Hojas`, 3).
- [x] Mensajería: `mensaje_teams`, `mensaje_slack` y `webhook_saliente` (credenciales `webhook_teams`, `webhook_slack`, `webhook_saliente`; URL secreta, solo https, hosts del servicio, `destino_seguro`, Positiva/SGDEA/metadatos/IP privadas bloqueados; sin permiso queda «escritura retenida»). Probado contra un servidor local (`Mensajeria`, 3).
- [x] IA: `traducir`, `resumir_largo` (mapa y reducción), `preguntar_documentos` (citas a archivo y página; descarta citas inventadas; «sin_respuesta») y `extraer_tablas` (PDF por página, imágenes con visión u OCR). Probado (`IaDeProducto`, 3).
- [x] Datos: `deduplicar` (por campos o huella, en la lista y entre corridas con la tabla integrada, con olvido), `agrupar` (contar/sumar/promedio/mínimo/máximo/lista/únicos), `ordenar_filtrar` (operadores de búsqueda), `mapear` (rutas, plantillas, tipos, defectos, obligatorios) y `validar_contra` (lista, tabla integrada o SQL de solo lectura, con sugerencias). Probado (`DatosDeProducto`, 5).
- [x] Control: `subflujo` (usa la salida del otro flujo y sale por su desenlace), `reintentar_mas_tarde` (corrida «aplazada» que vuelve sola; `servidor/src/aplazadas.ts`, `{{aplazamiento.intento}}`), `limite_tasa` (por flujo y clave, entre trabajadores), `ventana_horario` (días y horas hábiles de Bogotá con festivos de Colombia calculados sin red) y `detener_con_alerta` (evento `alerta` y, con credencial, Teams o Slack). Probado (`Control`, 6).
- [x] Cada nodo nuevo con ficha completa (ayuda, ejemplos, consejos), validación Python y zod en paridad, y pruebas: `puente/pruebas_declarativo.py` 173 en verde (incluye la paridad con zod y `EscriturasConPermiso`), `pruebas_plataforma.ts` 22 y `pruebas_aprobaciones.ts` 4; `npx tsc --noEmit` limpio.
- [x] Todo nodo que lee «un adjunto» (hoja_leer origen adjunto, archivo_leer con `{{entrada.archivos.0.id}}`, documentos, pdf, extraer_tablas, preguntar_documentos, leer_adjuntos) lee igual los adjuntos del correo y los archivos subidos a la corrida (`Hojas.test_archivos_subidos_se_leen_como_adjuntos`).

### Archivos que entran y archivos que salen (pedido 2026-11-07)

Que en un flujo entren documentos o imágenes por donde vengan (arrastrados, por correo, de cualquier fuente) y que también SALGAN archivos (Word, Excel, PDF, imágenes) generados con o sin IA, para automatizaciones completas como «leer el correo, redactar la respuesta, generar el documento y responder con los adjuntos».

- [x] Probar un paso desde su panel: arrastrar un archivo, o elegir un correo de su correspondencia, un archivo del flujo o la salida de una ejecución anterior, y correr solo ese paso (o hasta ese paso), viendo lo que produce; fijar esos datos de prueba para los pasos siguientes. Pestaña «Probar» del panel del nodo (`web/src/componentes/ProbarPaso.tsx`): arrastrar o pegar, correo, archivo del flujo o ejecución anterior; «Correr el flujo hasta este paso»; salida, lo que dejó, archivos generados (cápsulas con vista), escrituras retenidas con sus adjuntos y lo que pasó; «Fijar como datos de prueba». Servidor (`servidor/src/probar.ts`): `POST /api/flujos/:id/probar-paso {nodo, hasta?, entrada?, archivos?, caso?, desde_corrida?, fijados?, definicion?}` → `{ok, salida, alcanzado, variables, todas, muestra, archivos, escrituras, mensajes, avisos, eventos, ms, resultado?}`; `GET/PUT/DELETE /api/flujos/:id/fijados/:nodo` (en `<archivos>/<flujo>/fijados/`, nunca en la definición). Motor: `flujo_declarativo.py probar`; ninguna credencial escribe en la prueba (aunque una persona lo haya habilitado), tablas, archivos guardados, aprobaciones y aplazadas quedan retenidas y no llama a otros flujos; los pasos con datos fijados no se vuelven a correr. Probado (`ProbarUnPaso`, 4) y en vivo con la plantilla 08.
- [x] Enviar correo con adjuntos (generados en la ejecución, archivos de entrada o adjuntos originales del correo). `correo_enviar` y `enviar` con `adjuntos`: «{{documento}}», «{{pdf.id}}», «{{imagenes}}», «{{archivos}}», «{{adjuntos}}», ids «a-…» o filtros `{de, nombre, tipo}` (búsqueda sin mayúsculas ni tildes); hasta 10 archivos y 20 MB (por Graph, 3 MB cada uno), con errores claros. Sin permiso se arma con la lista de adjuntos y tamaños y queda retenida; Positiva sigue bloqueada. Probado con SMTP falso (`ArchivosQueSalen`).
- [x] Nodo «Responder correo» en el mismo hilo, con adjuntos (de la bandeja o el buzón del flujo; escritura habilitada por una persona). `responder_correo {cuerpo, html?, cc?, para?, asunto?, adjuntos?, incluir_original?, credencial?}`: por defecto la credencial del disparador; Graph con createReply → PATCH → adjuntos → send; SMTP con In-Reply-To y References (un buzón IMAP pide una smtp). Sin permiso o sin buzón, la respuesta completa queda retenida en el detalle de la ejecución. Probado con SMTP y Graph falsos (cabeceras del hilo, orden de llamadas, adjuntos en base64).
- [x] La IA genera documentos a partir de instrucciones: Word y PDF con títulos, párrafos, listas y tablas, y Excel con hojas y columnas. `documento_generar.desde_ia {instrucciones, entradas?, proveedor?, modelo?, credencial?, temperatura?}` (esquema `titulo`, `secciones[{titulo, parrafos, lista, tabla{columnas, filas}}]`, con el generador de PDF propio y python-docx) y `hoja_escribir.desde_ia` (hojas, columnas y filas → XLSX con openpyxl o CSV). Probado con el modelo falso y en vivo con Vertex (plantilla 08).
- [x] Nodo de imagen generada por IA (Vertex Imagen y otros proveedores que la tengan). `ia_imagen {instrucciones, aspecto|tamano, cantidad 1–4, estilo?, negativo?, proveedor vertex|gemini|openai, modelo?, credencial?}` → PNG como archivos del flujo y resultados (`puente/proveedores/imagenes.py`). En el proyecto, los modelos Imagen (`imagen-3.0-*`, `imagen-4.0-*`) dan 404 y Google los marcó en retiro: el que responde con ADC es `gemini-2.5-flash-image` (global), que queda por defecto; un modelo «imagen-…» va por generate_images (us-central1); OpenAI por `/v1/images/generations`. Llamada real a Vertex: 1 imagen PNG en ~5 s (`ImagenRealEnVertex`, con `PRUEBA_IMAGEN_REAL=si`).
- [x] La salida del flujo entrega archivos: descargables en el historial y la ejecución, en el chat de Vera y en la respuesta del webhook. Todo archivo generado queda como resultado (`archivos` del resultado: id, nombre, tipo, tamaño, nodo; evento `archivo_generado`); `salida.archivos` dice lo que entrega (`archivos_salida`). `GET /api/corridas/:id/archivos`, `GET /api/corridas/:id/archivos/:archivo` (en línea o `?descargar=1`) y `/texto`; en `/api/historial/:id` (`archivos`). Historial: cápsulas con vista en su lugar (imagen, PDF; Word y Excel como texto) y descarga. Webhook con `responder: true`: `respuesta_webhook.archivos` (o los de la salida) como enlaces firmados que vencen (`/api/descargas/…`, 72 h) o base64 (`archivos_como`). Probado en vivo. La parte del chat de Vera queda en el punto de Vera.
- [x] Drive, Teams, Slack y webhooks salientes también llevan los archivos generados. `drive_subir.archivo` (sube el archivo tal cual), `mensaje_teams`/`mensaje_slack.archivos` (botones o enlaces de descarga firmados que vencen) y `webhook_saliente.archivos` con `modo_archivos` base64, multipart o enlace; todo con la escritura habilitada por una persona. Probado contra un servidor local y un Drive falso. Para que los enlaces sirvan fuera, define `URL_PUBLICA` en el despliegue.
- [x] Plantilla «Responder correos con documento y adjuntos». `catalogo/plantillas/08-responder-correos-con-documento.json`: correo → leer adjuntos → IA extrae los datos → documento Word redactado por la IA → texto del correo → responder en el hilo con el documento y los adjuntos originales → salida con el documento. Pasa la validación (`PlantillaResponderConDocumento`) y corrió en vivo: «respondido», respuesta retenida con `respuesta-1023456789.docx` y el adjunto original.
- [x] Vera: probar_paso con lo que se le adjunta en el chat (y fijar datos de prueba) y archivos_de_corrida, que muestra lo generado como cápsulas en el chat (abrir en el visor o descargar). Probado: pidió un informe Word con IA, lo corrió y lo mostró en el chat en 38 s.
- [x] Nunca se pide una ruta del servidor a quien corre un flujo (2026-11-07). «Extracción de datos de documentos» pedía «Ruta del archivo en /datos/archivos». Corrección:
  - La validación rechaza en `entrada_manual` un campo de texto que pide ruta, carpeta o path, y rechaza un `archivo_leer` cuya ruta es lo que escribió la persona (`RUTA_A_MANO`). El documento se pide con un campo de tipo «archivo» y se lee con `archivo: «{{entrada.<campo>}}»`.
  - Vera tiene la regla y el ejemplo en la documentación del nodo.
  - El flujo quedó en v4: se sube el documento en «Correr», la IA saca los 5 datos de `archivos.texto` (PDF, Word, imagen con OCR o texto) y termina «Completado». Probado en el navegador.
  - Pruebas: `test_nunca_se_pide_una_ruta_a_quien_corre`, `test_campo_archivo_de_entrada_manual_se_lee` y caso de evaluación «documento se sube, no se pide ruta» (OK, 17 s).
- [x] Desenlace «completar» en la salida (2026-11-07). Es el fin normal de un flujo que hizo su trabajo y se ve «Completado» en verde. Antes los flujos generales terminaban en «manual», que se ve como «Para revisión de una persona» en rojo. Vera usa «manual» solo cuando una persona tiene que revisar algo.

### Vera en paridad con la pantalla
- [x] Vera comprueba la pantalla antes de afirmar (pedido 2026-11-07: dijo «ya tienes en pantalla el flujo» estando la persona en Inicio, y «ya te lo abrí» sin comprobar). El navegador reporta su pantalla real a la conversación activa cada vez que cambia y justo después de aplicar lo que pidió Vera (`PUT/GET /api/chats/:id/pantalla`, también desde la llamada); herramienta `ver_pantalla`; `enfocar_flujo`, `navegar`, `abrir_vista`, `abrir_corrida`, `centrar_nodo` y `abrir_correo` esperan lo reportado y devuelven `confirmado` y `en_pantalla`; la descripción de la pantalla ya no dice «flujo en pantalla» cuando está en Inicio, Plantillas o Conexiones; regla en el chat y en la voz. Casos de evaluación «volver al flujo» y «no afirma sin ver» (navegador simulado que no obedece); evaluación 14/14. Probado con el navegador real: desde Inicio, «volvamos al flujo donde estábamos» abre `/flujos/tutelas` con el lienzo y solo entonces lo afirma.
- [x] Todo lo nuevo se puede hacer también con Vera (plantillas, aprobaciones, historial, notificaciones, nodos nuevos) y la evaluación de Vera crece con casos de esto: herramientas plantillas, instalar_plantilla, historial, reintentar_corrida, notificaciones, aprobaciones (sin decidir), abrir_vista; casos «instalar una plantilla», «historial de fallos», «aprobaciones: no decide», «notificaciones», «aprobación humana» y los de conectores nuevos, todos OK.

## 5d. Integraciones y salidas a la medida (pedido 2026-11-07)

Jonathan: «un programa que te permite crear flujos y automatizaciones que no pueda generar salidas personalizadas no sirve tanto». Si les gusta, van a querer integrarlo con lo que usan y probar de todo: Gmail, Calendar, Outlook o cualquier otra página. Hay que tener el soporte y que Vera lo maneje todo sin saturarse.

### Base común para conectar cualquier servicio
- [x] Conexiones con OAuth 2.0 (pantalla de consentimiento, renovación del token, revocar), además de llaves y usuario/clave; cada conexión con alcance global o por flujo, permiso de escritura que da una persona, y prueba de conexión. Tipos `google_oauth` {client_id, client_secret, scopes} y `microsoft_oauth` {tenant, client_id, client_secret, scopes} (los de antes, `google_servicio` y `microsoft_graph`, siguen igual). Código de autorización con PKCE S256 y `state` en el servidor (10 min, un solo uso): `GET /api/oauth/:credencial/iniciar` (o `?json=1`), `GET /api/oauth/callback` (guarda cifrados refresh/access token, cuenta y alcances; rechaza cuentas de Positiva), `POST /api/oauth/:credencial/revocar`, `GET /api/oauth/redireccion` (`URL_PUBLICA` + `/api/oauth/callback`, por defecto http://localhost:8095), renovación automática antes de vencer (cada 5 min; rota el refresh de Microsoft) y en el motor si llega vencido; `POST /api/credenciales/:nombre/probar` para todo tipo. Conexiones: «Conectar con Google/Microsoft» (ventana del proveedor), cuenta y permisos, «Probar conexión», «Desconectar». `creada_por_persona` al guardar con la confirmación de una persona. Probado SOLO con un servidor de autorización falso (`servidor/src/pruebas_oauth.ts`, 7 pruebas; `pruebas_integraciones.py`: renovación y token revocado); falta la prueba real con una cuenta de Google y una de Microsoft (registrar las aplicaciones).
- [x] Conector genérico por OpenAPI/Swagger: importar la especificación de cualquier API y obtener sus operaciones como pasos configurables (con sus parámetros y su autenticación), sin escribir código. Conexión `api_openapi` {spec_url o spec JSON/YAML, base_url, auth ninguna|api_key (encabezado o consulta)|bearer|basico|oauth2 de cliente, red_privada}; `POST /api/conectores/openapi/analizar` → operaciones (id, método, ruta, parámetros, escritura); nodo `api_operacion` {operacion (operationId o «MÉTODO /ruta»), parametros, cuerpo, paginacion, mapeo} que revisa obligatorios, tipos y enumeraciones contra la especificación; GET lee, lo demás queda retenido sin permiso; IP privadas solo con red_privada en una conexión creada por una persona. Probado con una petstore local (`OpenAPIYHttp`).
- [x] Conector HTTP avanzado: paginación, OAuth, reintentos, mapeo de la respuesta, carga y descarga de archivos. `http` con `paginacion` {cursor|pagina|enlace, solo GET y mismo host → `items`}, credencial `http_oauth2` (token de cliente que se pide y renueva solo), `archivos` + `modo_archivos` multipart|binario (POST/PUT/PATCH) y `descargar` (la respuesta queda como archivo de la ejecución); el bloqueo SSRF y HTTP_HOSTS_PERMITIDOS siguen igual. Probado contra un servidor local.
- [x] Páginas sin API: lectura de páginas web y RSS (solo lectura, respetando robots y límites). Nodos `web_leer` {url, extraer texto|enlaces|tablas|selector (CSS con soupsieve), atributo, completo, max_kb…} y `rss_leer` (RSS 2.0, RSS 1.0 y Atom, o la página que anuncia su feed; desde, contiene) y el disparador `rss_nuevo` (cada ≥ 5 min, sin repetir: recuerda los vistos y pide con ETag/Last-Modified; la primera revisión solo memoriza). `puente/conectores/web.py`: solo GET https por `rest.pedir` a la IP ya revisada (nunca Positiva, SGDEA, metadatos ni IP internas, también en la validación), robots.txt con agente «Positiva-IA» (401/403/5xx = no se lee), frecuencia por sitio ≥ 2 s o su Crawl-delay entre procesos (`<estado>/web/ultimas.json`), tope de tamaño y de tipo, hasta 3 redirecciones revisadas una a una, feeds sin `<!ENTITY>`. Probado con un sitio local (`puente/pruebas_externos.py`, `PaginasSinApi`).
- [ ] Automatización de navegador (páginas que se arman con JavaScript o piden inicio de sesión) en entorno aislado con aprobación: pendiente; no se hizo en esta tanda (pide un navegador sin cabeza aislado, su licencia y su costo de recursos).
- [x] Disparadores por evento (webhooks entrantes de cada servicio, sondeo con marcas para no repetir) además de los programados. `gmail_nuevo` (historial de Gmail con historyId), `outlook_nuevo` (deltaLink de Graph) y `calendar_evento_proximo` (N minutos antes, una vez por evento e inicio), solo lectura y sin repetir, por el programador (`flujo_declarativo.py sondear`); el webhook entrante genérico ya existía y cada ficha explica cómo conectarlo con el servicio (push de Gmail por Pub/Sub o suscripciones de Graph piden una URL pública). Probado con dobles (`Disparadores`).

### Servicios (por fases)
- [~] Fase 1, Google Workspace: Gmail (leer, buscar, enviar, responder con adjuntos, etiquetas), Calendar (eventos, disponibilidad, invitaciones), Drive (ya hay), Sheets (ya hay), Docs (crear desde plantilla), Forms (respuestas). Nodos `gmail_buscar`, `gmail_enviar`, `gmail_responder` (mismo hilo), `gmail_etiquetar`, `calendar_eventos`, `calendar_crear` y `calendar_actualizar` (invitados y Meet), `calendar_disponibilidad` (freeBusy y franjas libres, festivos de Colombia), `docs_crear_desde_plantilla` (llena {{variables}}; PDF o Word; copia en Drive solo con permiso), `forms_respuestas`; Drive y Hojas aceptan también `google_oauth`. Ficha completa (ayuda, ejemplos, consejos y «preparar») en `puente/especificacion_integraciones.py`, validación en Python y zod en paridad, escrituras retenidas con todo armado, nunca direcciones de Positiva. Plantilla «Responder correos de Gmail con documento» (`catalogo/plantillas/09-…`). Probado SOLO con dobles locales (`puente/pruebas_integraciones.py`, 28 pruebas); **pendiente: la prueba real con una cuenta de Google**.
- [~] Fase 1, Microsoft 365: Outlook (leer ya hay; enviar, responder, mover, categorías), Calendario, OneDrive y SharePoint (archivos y listas), Teams (canales y chats, ya hay webhooks), Excel en línea. Nodos `outlook_buscar`, `outlook_enviar`, `outlook_responder`, `outlook_mover`, `outlook_categorizar`, `outlook_calendario_eventos`, `outlook_calendario_crear` (Teams), `onedrive_listar`/`leer`/`subir` (por partes sobre 4 MB), `sharepoint_archivos`, `sharepoint_lista`, `teams_mensaje` (canal o chat por Graph, solo OAuth) y `excel_en_linea` (tabla o rango), con `microsoft_oauth` o `microsoft_graph`; buzones, direcciones y sitios de Positiva bloqueados. Plantilla «Agendar reuniones desde correos de Outlook» (`catalogo/plantillas/10-…`). Probado SOLO con un Graph falso; **pendiente: la prueba real con una cuenta de Microsoft 365**.
- [ ] Prueba real de Google: registrar el cliente OAuth en Google Cloud (pasos en `docs/INTEGRACIONES.md`), conectar una cuenta de prueba y correr la plantilla «Responder correos de Gmail con documento».
- [ ] Prueba real de Microsoft 365: registrar la aplicación en Entra ID (pasos en `docs/INTEGRACIONES.md`), conectar y correr «Agendar reuniones desde correos de Outlook».
- [ ] Confirmar con Jonathan el nombre del tenant de SharePoint de Positiva (hoy se bloquean `positiva.sharepoint.com` y `positiva-my.sharepoint.com`).
- [ ] Disparadores instantáneos (Gmail push por Pub/Sub, suscripciones de Graph) cuando la plataforma tenga dirección pública; hoy revisan cada N minutos.
- [ ] `docs_crear_desde_plantilla` a PDF sin pasar por Drive conserva todo el formato del Doc.
- [~] Fase 2, mensajería: `telegram_mensaje` (Bot API: texto partido en mensajes de 4.096, HTML o Markdown, archivos como documentos), `correo_transaccional` (SendGrid o Mailgun, HTML y adjuntos; nunca remitentes ni destinatarios de Positiva ni los buzones protegidos) y `sms_twilio` (número internacional, hasta 1.600 caracteres), todo por HTTP sin SDK (`puente/conectores/transaccional.py`); escriben → retenidos con todo armado sin permiso de escritura. Conexiones `telegram_bot`, `sendgrid`, `mailgun`, `twilio` con «Probar conexión» que no envía (getMe, scopes, dominio, cuenta). Probado SOLO con dobles locales (`Mensajeria`). Falta WhatsApp Business (pide cuenta de Meta verificada y plantillas aprobadas) y la prueba real de cada servicio.
- [~] Fase 2, almacenamiento: `ftp_archivos` (FTP/FTPS con ftplib, TLS explícito por defecto, solo pasivo y los datos a la IP del control) y `s3_archivos` (S3, MinIO, Google Cloud Storage por interoperabilidad con HMAC), cada uno con listar, leer (texto con OCR o archivo de la ejecución) y subir (retenido sin permiso; conflicto renombrar|reemplazar|fallar); las rutas no salen de la carpeta base o prefijo de la conexión. S3 firmado con SigV4 propio sobre `rest.pedir` en vez de boto3 (Apache, sí se podía): boto3 resuelve el DNS por su cuenta y no deja conectar a la IP ya revisada, y pesa ~90 MB; la firma se prueba con el ejemplo oficial de AWS. Conexiones `ftp` y `s3` (red_privada solo si la crea una persona). Probado SOLO con un FTP local y un S3 local (`Almacenamiento`). **SFTP no**: paramiko y ssh2-python son LGPL y asyncssh EPL/GPL; no hay librería SSH MIT/BSD/Apache madura. Dropbox y Box no se hicieron en esta tanda (OAuth propio de cada uno).
- [~] Fase 2, datos: SQL Server (python-tds, MIT; TLS con pyOpenSSL, Apache) y Oracle (python-oracledb «thin», Apache) dentro de `sql_consulta`/`sql_ejecutar` (conexiones `sqlserver`, `oracle`; lectura que se deshace siempre o `SET TRANSACTION READ ONLY`, y se vetan OPENROWSET, xp_/sp_, WAITFOR, DBMS_/UTL_…); `mongo_consulta` (buscar, contar, distintos, agregar con tope; nunca $where, $function, $out ni $merge) y `mongo_escribir` (insertar, actualizar con operadores, borrar; con filtro obligatorio) con pymongo (Apache) instalado SIN dnspython (ISC) por `puente/requirements-sin-dependencias.txt` (por eso no hay mongodb+srv://; se dan los host:puerto); `bigquery_consulta` (pasa en seco con jobs.insert dryRun: solo SELECT) y `bigquery_ejecutar` (solo INSERT/UPDATE/DELETE/MERGE, retenido sin permiso) y `firestore_documentos` (obtener, listar, consultar con filtros y orden, guardar retenido) por REST con `google_servicio`/`google_oauth`. Probado SOLO con dobles: controladores falsos para MongoDB, SQL Server y Oracle y un BigQuery/Firestore local (`Datos`); falta la prueba contra bases reales.
- [~] Fase 3, trabajo y clientes: un nodo por servicio con `operacion`, todo por HTTP sin SDK sobre `rest.pedir` (`puente/conectores/trabajo.py`): `jira_issues` (Jira Cloud REST v3: buscar con JQL por `/search/jql` con nextPageToken, ver, crear con descripción en ADF, comentar, cambiar_estado por el nombre de la transición o del estado destino), `trello_tarjetas` (tableros, listas, tarjetas, crear, mover, comentar; llave y token en el encabezado, nunca en la URL), `asana_tareas` (listar por proyecto o asignado con paginación, ver, crear, completar, comentar), `notion_paginas` (consultar una base con filtro y orden y paginación, crear y actualizar con valores simples convertidos al tipo de cada columna, agregar_bloques desde texto en tandas de 100), `hubspot_crm` (contactos, empresas, negocios: buscar con texto o filtros y paginación, ver por id o correo, crear, actualizar, nota asociada), `salesforce_registros` (SOQL solo SELECT con paginación por nextRecordsUrl, ver, crear, actualizar; OAuth de credenciales de cliente con `oauth.cliente`), `zoho_crm` (v6: buscar por criterio, correo, teléfono o palabra; ver, crear, actualizar; token con el refresh_token de un Self Client) y `docusign_sobre` (enviar un PDF o Word de la ejecución a firmantes en orden con copias y ancla de firma, estado, descargar el PDF firmado; JWT RS256 firmado con `cryptography`, Apache/BSD). Los `{{valores}}` de JQL, SOQL y criterios de Zoho se escapan; sitio de Jira solo `*.atlassian.net`/`jira.com`, instancia de Salesforce solo `*.salesforce.com`/`force.com`, base de DocuSign solo `*.docusign.net`/`.com`. Leer sale libre; todo lo que escribe queda retenido con todo armado sin la escritura de la conexión; cualquier dirección de Positiva o buzón protegido en lo que se iba a escribir (o como firmante) detiene el paso y queda como escritura vetada. Conexiones `jira`, `trello`, `asana`, `notion`, `hubspot`, `salesforce`, `zoho`, `docusign` (secretos cifrados; «Probar conexión» solo lee perfil, cuenta o pide un token). Ficha para Vera en `puente/especificacion_trabajo.py`, zod en paridad en `servidor/src/flujos_trabajo.ts`. Plantilla «Correos con «error» a issues de Jira» (`catalogo/plantillas/11-…`, valida). Caso de evaluación de Vera «error por correo a Jira» (OK, 20 s). Probado SOLO con dobles locales (`puente/pruebas_trabajo.py`, 17 pruebas; el doble de DocuSign verifica la firma del JWT con la llave pública). **Pendiente: la prueba real de cada servicio** (ninguna cuenta de prueba todavía; lo que hace falta está en `docs/INTEGRACIONES.md`).
- [ ] Fase 3, lo que no se hizo: OAuth con pantalla de consentimiento para HubSpot, Notion, Asana, Trello y Zoho (hoy van con token de aplicación privada o Self Client, que basta para un equipo); disparadores por evento de estos servicios (webhooks de Jira, Trello, HubSpot o DocuSign Connect: piden dirección pública); otras firmas electrónicas (Adobe Sign, firma colombiana con certificado) y la prueba real.
- [ ] Cada servicio: sus pasos con ficha completa (ayuda, ejemplos, consejos), validación en paridad, escrituras retenidas sin permiso, pruebas con dobles y una prueba real cuando haya cuenta de prueba.

### Salidas a la medida
- [x] Formatos de salida: Word, PDF, Excel, PowerPoint, CSV, JSON, XML, HTML, imágenes y ZIP. `documento_generar` ahora también `pptx` (16:9, portada, viñetas, tablas y notas, azules de Linktic; desde texto, desde un .pptx del flujo o desde_ia) y `html`; nodos nuevos `archivo_exportar` (JSON, XML, CSV con «,» «;» o tabulador y BOM, HTML con tabla, txt, md; desde una variable o un texto con los valores escapados) y `archivo_comprimir` (ZIP, hasta 50 archivos y 30 MB, nombres repetidos «(2)»). Todo queda como archivo de la ejecución (historial, chat de Vera, webhook); el visor muestra el PowerPoint diapositiva por diapositiva, la lista del ZIP y el HTML en un marco aislado (`Content-Security-Policy: sandbox`). python-pptx 1.0.2 (MIT, trae XlsxWriter BSD) en `puente/requirements.txt`. Código: `puente/salidas.py`, `puente/nodos_salidas.py`, `puente/especificacion_salidas.py`, zod en `servidor/src/flujos_salidas.ts`.
- [x] Biblioteca de plantillas de documentos (Word, Excel, PowerPoint, correo HTML) con variables, logos y estilos de cada área, reutilizables entre flujos. En `<estado>/documentos/<id>/` (meta, archivo y `recursos/` con logos); API `GET/POST /api/documentos`, `GET/PUT/DELETE /api/documentos/:id` (borrar solo una persona), `/archivo`, `/recursos[/:nombre]`, `/probar` (`servidor/src/documentos.ts`, analiza con `salidas.py analizar`). Sintaxis igual en los cuatro formatos: `{{x}}`, `{{#lista}}` en una fila de tabla o un párrafo (en HTML `{{#lista}}…{{/lista}}`) con `{{item.campo}}`/`{{indice}}`, `{{imagen:logo [4cm]}}`, `{{contenido}}`; se conserva el formato (encabezados y pies de Word, estilos y fórmulas de Excel, patrón de PowerPoint); una sola pasada (un valor con `{{…}}` no se vuelve a leer) y `entorno` nunca se lee desde una plantilla. Paso `documento_plantilla {plantilla, datos, formato original|pdf, desde_ia}`; en HTML deja `{{salida.html}}` y `{{salida.asunto}}` para el correo. Pantalla `web/src/componentes/Documentos.tsx` en `/documentos[/<id>]` (riel «Construir»): tarjetas con filtros por formato y búsqueda, subir o arrastrar, escribir una (correo HTML o Word/PowerPoint/Excel desde texto), detalle con vista previa, lo que pide, logos, probar con datos (descarga) y el paso para copiar; borrar con confirmación en línea; el panel del paso elige la plantilla con el desplegable propio.
- [x] La IA llena plantillas o arma el documento completo (Word, Excel, PowerPoint, imagen) según instrucciones. `documento_plantilla.desde_ia` modo `llenar` (la IA escribe solo las variables y listas que el flujo no tiene, o las de `variables`) y modo `completo` (además secciones, diapositivas u hojas en `{{contenido}}` con el diseño de la plantilla); `documento_generar` formato pptx con desde_ia (esquema de presentación); Word, PDF y HTML con desde_ia; Excel con `hoja_escribir.desde_ia`; imagen con `ia_imagen`. Vera: herramientas `plantillas_documento` y `crear_plantilla_documento` (grupo construir) y `abrir_vista documentos`. Pruebas: `puente/pruebas_salidas.py` 18 en verde (formatos, biblioteca en los cuatro formatos, IA con el modelo falso, validación y paridad con zod, Vera); `pruebas_declarativo.py` 191, `pruebas_integraciones.py` 28, `pruebas_plataforma.ts` 22, `pruebas_aprobaciones.ts` 4 y `pruebas_oauth.ts` 7 en verde; probado en vivo con Vertex (correo HTML de la biblioteca, PowerPoint de la IA, plantilla de PowerPoint en modo completo, CSV y ZIP en una ejecución) y evaluación de Vera «PowerPoint generado» (35 s, lo mostró en el chat) y «plantilla de correo HTML» (7 s), 2 de 2.
- [x] Pantalla Documentos revisada en el navegador (1280×720, 1440×900 y 390×844): riel sin barra de desplazamiento, sin desborde horizontal, sin errores; enlace directo `/documentos/<id>` abre el detalle. Arreglado de paso: al abrir un enlace directo la URL decía /documentos y se veía el Inicio (ahora las pantallas generales se muestran antes de cargar y la URL se aplica aunque la carga falle); las ventanas ya no ponen el foco en «Cerrar» (salía su globo) y las áreas de texto no muestran el tirador del navegador.
- [ ] Excel: al repetir filas, las celdas combinadas, las imágenes y las fórmulas que están debajo no se corren (openpyxl no lo hace); las diapositivas que la IA agrega a una plantilla usan su diseño «Título y contenido», no copian una diapositiva de ejemplo.
- [ ] PowerPoint de la IA con las imágenes de `ia_imagen` en sus diapositivas (el generador ya acepta `imagen` por diapositiva; falta pedírsela a la IA y conectarla).
- [x] Entregar la salida donde se necesite: responder o enviar correo con adjuntos (correo_enviar, gmail_responder, outlook_responder, correo_transaccional), subir a Drive/OneDrive/SharePoint/S3/FTP, publicar en Teams/Slack/Telegram/SMS, devolver por webhook (respuesta_webhook) y dejar para descargar (archivos de la ejecución, cápsulas en el chat). WhatsApp sigue pendiente (Fase 2, mensajería).

### Vera a la altura, sin saturarse
- [x] Herramientas por grupos (núcleo + construir, correr, correo, conexiones, ia, producto, pqrd) elegidos por el pedido y la pantalla; Vera carga más con cargar_herramientas; en la voz un conjunto fijo y lo demás se delega al proceso escrito.
- [x] Documentación de nodos bajo demanda: el prompt lleva un índice de una línea por tipo y la ficha se pide con ayuda_nodo. Prompt de 94 mil a 17 mil caracteres; herramientas por mensaje de 23 mil a 8–16 mil.
- [x] Planificar antes de construir automatizaciones grandes: regla en las instrucciones de Vera (plan corto con disparador, pasos, conexiones que faltan y qué escribe afuera; una sola pregunta si falta algo; construir, validar, probar con probar_paso y reportar). En la voz lo largo ya va a un proceso aparte (responder_por_escrito). Caso de evaluación «automatización grande M365» (Outlook → IA → Excel en línea → aprobación → responder): OK en 45 s.
- [x] Presupuesto de contexto: `compactar()` en `puente/asistente.py` deja completos los últimos 12 mensajes (y bajo 60 mil caracteres) y condensa lo anterior en una memoria con lo pedido, lo hecho, los flujos usados en orden y los adjuntos (que siguen disponibles); variables `VERA_TURNOS_COMPLETOS` y `VERA_TOPE_CONTEXTO`. Probado: conversación de 18 mensajes, «volvamos al flujo donde estábamos» abre tutelas, que solo estaba en la memoria.
- [~] La evaluación de Vera crece con cada conector y salida: 20 casos (flujos, nodos, plantillas, historial, aprobaciones, notificaciones, Word, PowerPoint, plantilla de correo HTML, Gmail, automatización grande de Microsoft 365, RSS, pantalla confirmada, volver al flujo); corrida completa del 2026-11-07: 19/20 y el que faltó (anonimizar y PDF) era la verificación, que solo buscaba el PDF en los datos y no en los archivos de la ejecución: corregida, 20/20. Caso 21 «error por correo a Jira» (Fase 3: Gmail → Jira crear → Telegram, sin encender): OK en 20 s. Sigue creciendo con cada conector (OpenAPI, FTP/S3, bases de datos).
- [x] Los archivos que se generan durante una llamada de voz quedan guardados en el turno de la conversación (`archivos` en `POST /api/chats/:id/turno`) y se ven como cápsulas al abrirla.
- [x] Las herramientas aceptan «flujo» o «id» indistintamente y avisan qué argumento falta (era la causa de un fallo de la evaluación).

## 5e. Motor de PQRD más liviano (pedido 2026-11-09)

Medir primero, mejorar lo que más pesa y demostrarlo contra la línea base. Cifras en `docs/RENDIMIENTO_MOTOR.md`; lo que diverge de producción, en `motor/DIVERGENCIAS.md`.

- [x] Medición en el harness: cada llamada al modelo (quién, tokens de entrada, salida, razonamiento y caché, tiempo), vueltas y herramientas del agente, CPU y memoria pico, resumen y comparación de corridas con la comprobación de sustento (`harness/medicion.py`).
- [x] Línea base del universo completo (389 correos) con el motor de `7540e18`, y ruido medido repitiendo 60 correos con el mismo código.
- [x] El agente arranca con el inventario de documentos y las pistas de cada campo; pide varias herramientas por vuelta; la búsqueda acepta un documento.
- [x] El razonamiento se cuenta como salida (el costo reportado era la mitad del real).
- [x] Tope de salida y de tiempo a la transcripción por visión.
- [x] OCR de las hojas de un PDF a la vez, con cupo por núcleos; tope de píxeles por hoja; hojas en blanco sin OCR; sin PNG intermedio; memoria acotada (`_sin_cuadricula` por franjas, `malloc_trim`).
- [x] Caché de la lectura de cada adjunto por su huella (memoria y disco); la plataforma le da su carpeta.
- [x] Dirección: rótulos «Dir:» y «Dirección para notificaciones:», la de Positiva excluida; la compuerta descarta la dirección ilegible.
- [x] Tope de razonamiento medido con el corpus etiquetado de punta a punta: con 512 baja el costo un tercio pero empeora la calidad; queda apagado (`RAZONAMIENTO_TOKENS`).
- [x] Pantalla: rendimiento por etapa, llamadas, tokens de razonamiento y de caché y adjuntos desde la caché en el detalle de cada ejecución; el tablero suma el p95 y las consultas a la IA por correo. Verificado con Playwright en escritorio y celular.
- [x] Volumen: PDF de 80 hojas (244 s → 73 s), correo de 30 adjuntos (137 s → 73 s) y ráfaga del universo (memoria pico 8,2 → 3,0 GB).
- [x] Universo completo y regresión del corpus con el motor nuevo; calidad correo por correo y retrocesos justificados o corregidos (reglas de documento recortado, documento lejos del afectado y varios afectados por el nombre de los adjuntos).
- [x] Las hojas se renderizan en el hilo del documento: con PyMuPDF en varios hilos el trabajador cayó con SIGSEGV en la plataforma.
- [x] Pruebas, comentarios y prompt sin datos del universo (nombres y documentos inventados).
- [ ] Convertir a la vez los adjuntos de un mismo correo.
- [ ] Descargar los adjuntos grandes por partes.
- [ ] Caché entre corridas del job en el bucket (hoy el job solo tiene la de memoria).

## 5f. HEIC, flujos de código editables y Vera sin inventar (pedido 2026-11-09)

Viene de una llamada real con Vera: un correo de PQRD con 8 fotos HEIC salió con los 8 adjuntos sin leer, Vera dijo que PQRD tenía «estructura fija», no propuso el borrado de la copia y afirmó cosas falsas (que se radicó en el SGDEA, un «equipo técnico», un costo inventado).

- [x] HEIC/HEIF se reconocen por su cabecera y se leen con la visión de Gemini (`image/heic`/`image/heif`), con la marca de transcrito por IA, dentro del cupo de visión del correo y con caché por huella. No hay decodificador con licencia permitida (libheif y libde265 son LGPL, x265 GPL). Medido con el correo real: 8 de 8 fotos leídas (7 llamadas, la repetida desde la caché).
- [x] WEBP y AVIF pasan por el OCR (antes «desconocido»), el TIFF de varias hojas se lee hoja por hoja y el `smime.p7m` firmado en claro se lee como correo (dos del universo). Anotado en `motor/DIVERGENCIAS.md`; pruebas en `motor/tests/test_formatos_de_imagen.py`.
- [ ] RAR y 7z siguen sin leerse: no hay biblioteca de Python con licencia permitida (libarchive es BSD pero su enlace de Python es CC0 y `bsdtar` no está en la imagen).
- [x] Los flujos de código se editan de verdad (`puente/estructura_codigo.py`, `servidor/src/estructuras.ts`, `puente/estructura_trabajador.py`): cada etapa es un nodo «etapa» que se apaga donde el motor lo admite (deduplicación, enrutamiento, facturas, volumen, visión, registro, revisor…; en tutelas el enlace) o se reconfigura (lectores por tipo de archivo: automático, OCR, visión u omitir; cupo de visión), volumen y deduplicación por contenido se intercambian, y se insertan nodos declarativos en cuatro puntos de PQRD (al entrar, con los documentos leídos, antes de validar, tras radicar) y uno de tutelas (antes de radicar). El trabajador los corre con los datos de ese momento y quita todo al terminar la orden.
- [x] Lo que no tiene sentido se rechaza con el motivo (quitar una etapa, apagar la compuerta, reconectar etapas fuera del orden del código, insertar donde no hay punto, una salida que no es «manual», la config de un paso mal armada).
- [x] Una copia diverge del original sin tocarlo: la estructura es por flujo, la copia hereda la del original y el borrado se la lleva. PQRD original sin estructura es el código de producción.
- [x] El lienzo muestra los pasos insertados junto a su etapa (contorno punteado), las etapas apagadas tenues y los lectores propios; el inspector apaga o enciende una etapa y quita un paso insertado.
- [x] Vera edita PQRD, tutelas y sus copias con `editar_nodo`, `agregar_nodo` (con `entre`), `eliminar_nodo` y `editar_aristas`, más `ver_estructura` y `restablecer_estructura`; se quitó la regla de «estructura fija».
- [x] `TRANSCRIPTOR_IA` ya no es un parámetro ajustable: Vera lo había puesto como texto en el PQRD original y lo dejaba sin visión; se revirtió.
- [x] La voz tiene las mismas herramientas que el chat escrito: declara todas las de `asistente.HERRAMIENTAS` salvo `EXCLUIDAS_DE_VOZ`, cada una con su motivo (hoy solo `cargar_herramientas`: la sesión ya las trae todas); usa el mismo SISTEMA y lo largo va al chat con `escribir_en_chat`. `puente/pruebas_voz.py` falla si una herramienta del chat no está en la voz sin motivo, si faltan reglas en el SISTEMA o si el borrado por voz no queda como propuesta con botón.
- [x] Borrar por voz y por escrito: `proponer_borrado` y `borrar_flujo` en la llamada (y las herramientas de edición); el chequeo que trataba una copia de PQRD como original estaba roto (la API no devolvía `borrable`); Vera dice que dejó el botón para confirmar.
- [x] Veracidad en las instrucciones de Vera: en modo prueba «se radicaría», nunca «se radicó en el SGDEA»; no inventa equipos ni procesos; costos y modelos solo de `modelos_ia` y de la corrida; no contradice sin evidencia.
- [x] Pruebas: `puente/pruebas_estructura.py` (14), `servidor/src/pruebas_estructura.ts` (2) y seis casos nuevos en `scripts/evaluar_vera.py` (costo del HEIC, editar la copia de PQRD, borrar la copia, «se radicó», y por la ruta de la voz —su sistema, sus herramientas y su turno, con el modelo de texto— borrar la copia y «se radicó»).
- [x] `agregar_nodo` agrega varios nodos y quita aristas en un solo guardado: un tramo con ramas ya no falla a medio armar (Vera se quedaba sin vueltas).
- [ ] Tutelas: la estructura se probó con sus pruebas, no con un hilo real (la carpeta del job estaba vacía).

## 5h. Motor al día con producción, 6ecdf40 (2026-11-10)

`mcp-fix` pasó de `7540e18` a `6ecdf40`: `1d11744` cambia Supabase por el registro de radicados en Cloud SQL (base `gcpprolinktic`, tabla `iapqrd_radicados_pqrd`, `services/registro_radicados.py`, pg8000, `CLOUDSQL_*`), que también responde la capa 6; `6ecdf40` hace que la capa 6 (remitente y asunto) no aplique a buzones de Positiva, porque la app manda todas las PQRD con el mismo remitente y asunto.

- [x] `motor/` con los dos commits. No se corrió `catalogo/sincronizar_motor.sh 6ecdf40`: deja `motor/` idéntico al commit y habría borrado las divergencias de 5e y 5f. Se aplicó el diff `7540e18..6ecdf40` encima (`git apply`, sin conflictos) y `ORIGEN` dice `6ecdf40`. Comprobado contra el `git archive` de `6ecdf40`: solo difieren los archivos de `motor/DIVERGENCIAS.md`. La próxima sincronización se hace igual.
- [x] Catálogo regenerado (`extraer.py`, `build.py`, `enlazar.py`): 33 nodos, 566 parámetros, 10 sistemas. El proveedor «Supabase» pasa a «Registro de radicados (Cloud SQL, iapqrd_radicados_pqrd)», con sus SELECT e INSERT y sus variables. Hay reglas nuevas: `dedup_auditoria_no_positiva` y `cie_registro` (guardar el radicado al cerrar). El catálogo anterior no se había regenerado desde 5e, así que también entran los parámetros de esa tanda.
- [x] Trabajador de PQRD, que vale también para las copias (`pqrd-revision-adjuntos`, `pqrd-test-heic`):
  - sin `CLOUDSQL_HOST`, `CLOUDSQL_USER` o `CLOUDSQL_PASSWORD` corre «sin auditoría», como el job, y «fuentes» lo dice como un hecho («sin auditoría», no «doble» ni error);
  - con esas variables y la compuerta apagada, el `RegistroDeRadicados` del motor va sobre `solo_lectura.conectar_registro_solo_lectura`: sesión `READ ONLY`, solo SELECT y una prueba de alcance de 3 s, una vez por proceso, porque la instancia solo tiene IP privada. Si no se alcanza, la capa 6 queda degradada con el motivo, como en el job con la base caída;
  - el INSERT del radicado lo retiene el grabador (`REGISTRO_RADICADO`) y sale como escritura `registro_radicados`;
  - con la compuerta encendida, el registro real.
- [x] Quitado lo que quedaba de Supabase en PQRD: rellenos `SUPABASE_*` del puente; `produccion.ts` (lecturas en simulación, entorno con `PQRD_CLOUDSQL_*`, conexiones y escrituras); consecuencias de la compuerta en `Entorno.tsx`; README; `docs/PRODUCCION.md`; textos de eventos y de la vista Sistemas (`eventos.ts`, `Vistas.tsx`). En `entornos.ts`, `CLOUDSQL_*` queda entre los nombres del job de PQRD y entre las variables protegidas; `SUPABASE_*` se sigue aceptando para no romper los entornos ya copiados.
- [x] `puente/requirements.txt` fija `pg8000==1.31.6`, la versión del motor. Con 1.31.5 la imagen no se construía.
- [x] Tutelas no usa Supabase (ni `motores/tutelas` ni su trabajador) y su repo fuente sigue en `4d51bc6`, igual que `motores/tutelas/ORIGEN`: no se tocó.
- [x] Pruebas, todas en verde:

  | Suite | Resultado |
  |---|---|
  | `pruebas_compuerta.py` | 9 de 9 (nuevas: solo lectura del registro, INSERT retenido, inalcanzable al instante, sin auditoría) |
  | `pruebas_estructura.py` | 15 |
  | `pruebas_declarativo.py` | 193 (1 omitida) |
  | `pruebas_externos.py` | 21 |
  | `pruebas_integraciones.py` | 28 |
  | `pruebas_salidas.py` | 18 |
  | `pruebas_trabajo.py` | 17 |
  | `pruebas_voz.py` | 9 |
  | Pruebas del motor (`pytest tests`, en el contenedor) | 3140 pasan, 22 omitidas |
  | Servidor (`node --test`) | 22 + 4 + 5 + 2 + 7 |
  | `tsc --noEmit` (servidor y web) | limpio |

- [x] Corridas reales en el contenedor (Vertex real, modo prueba):
  - c0070 (4 adjuntos con OCR y visión) y c0071: «radicado» `DRY-RUN-ENT`, con «sin auditoría» en fuentes;
  - c0055 (remitente `@positiva.gov.co`): ya no trae el aviso de la capa 6, porque la capa no aplica.
- [x] Capa 6 con correos reales de la bandeja y un registro falso que tiene su mismo par remitente y asunto: c0055, c0067 y c0068 (buzones de Positiva) no consultan y no son duplicados; c0070 y c0071 (externos) sí lo son. En la bandeja no hay correos de `appconexionpositiva@`: ese caso lo cubre la prueba del motor `test_la_app_de_positiva_no_junta_a_personas_distintas`.
- [ ] Verificar en el job (solo lectura, `gcloud run jobs describe`) que ya monta `PQRD_CLOUDSQL_*` y si todavía trae `SUPABASE_*`. Si los trae, quitarlos en el PR de producción. Después, copiar `CLOUDSQL_*` al entorno de PQRD con `scripts/copiar_entorno_de_job.py` (lo hace Jonathan). Aun así, desde el visor no se alcanza la IP privada: la capa 6 quedará degradada con el motivo.
- [ ] El generador del catálogo marca como ajustables constantes que no lo son: cerrojos y semáforos (`_CERROJO_DEL_CUPO_IA`, `_CUPO_TESSERACT`, `_CANDADO_DEL_CLIENTE`) y `_IDENTIFICADOR`. Excluirlos en `build.py`.
- [ ] El aviso del motor «deduplicacion degradada: la auditoria no esta configurada» sale tal cual en los avisos del resultado. Si se quiere suavizar en pantalla, va en las vistas que muestran los avisos.

## 5g. Vera y operaciones entre flujos (pedido 2026-11-10)

Jonathan pidió que Vera haga todo en la plataforma, por texto y por voz (borrar, clonar, editar, renombrar, «cruzar flujos»), sin los fallos de las conversaciones `095d3d20-dd70-4b2b`, `776e0c45-b45c-4096` y `9a25aae8-e92f-4faf`.

### Fallos de las conversaciones reales

- [x] **Chat escrito cortado.** Un pedido de varias ediciones (21 herramientas) terminaba con «Me detuve tras varios pasos sin terminar» porque el tope era de 14 vueltas.
  - El tope ahora es de 30 vueltas (`VERA_MAX_VUELTAS`).
  - Si igual se agota, la respuesta la arma `resumen_al_agotarse` con lo que devolvieron las herramientas: lo guardado con su versión, lo que faltó con su motivo, lo que alcanzó a revisar y «si me dices sigue, continúo».
  - El modelo ya no redacta ese cierre. En la prueba, obligado a contestar sin herramientas, dijo que había guardado cambios que no guardó.
  - Lo mismo aplica con otros proveedores (`_conversar_con_proveedor`).
  - La regla de qué flujos cambió y qué propuestas dejó una herramienta es una sola (`anotar_resultado`), compartida por el chat, los otros proveedores y la voz.
- [x] **Voz cortada y con palabras pegadas.** La causa: cada cambio de pantalla (el que manda el navegador al quedar en llamada y el que llega tras `enfocar_flujo`) se mandaba a Gemini Live como contenido apenas llegaba, y eso interrumpe lo que Vera está diciendo. De ahí salían «Buenas tardes, habla», «Listo, ya» + «Tienes…» y «Me pediste que te».
  - El aviso de pantalla ahora espera una pausa (ni Vera ni la persona hablando) y solo se manda si cambió de verdad.
  - Los fragmentos de transcripción se pegan con `unir_transcripcion`, que pone el espacio que falta entre frases sin partir palabras.
  - El turno se guarda 0,9 s después del fin del turno, para que la transcripción que llega tarde quede en el mismo turno. Si la persona vuelve a hablar, se guarda antes.
  - El recordatorio «di el resultado» ya no se manda mientras suena su voz.
- [x] **Saludo.** Ya no se presenta: «Buenas tardes, cuéntame qué hacemos hoy.» No dice su nombre ni el de la plataforma.
- [x] **Capacidades.** El SISTEMA trae la lista concreta y agrupada: crear, editar (también PQRD y copias), operar la plataforma (clonar, comparar, traer pasos, encadenar, borrar con botón, programar, recursos), correr, revisar y diagnosticar, y moverse por la pantalla. También dice lo que no hace. Por voz lo dice en dos o tres frases y deja la lista en el chat.
- [x] **Diagnóstico de verdad.** La herramienta nueva es `diagnosticar_flujo`. Revisa:
  - las últimas corridas: desenlaces, motivos de revisión manual con ejemplos, errores, avisos frecuentes y campos sin resolver;
  - la validación de la definición o de la estructura;
  - las etapas y pasos apagados;
  - el entorno sin valor;
  - las notificaciones y la programación.

  A cada hallazgo le sugiere el arreglo que Vera puede aplicar. Ante «¿qué puedes solucionar?» la usa, responde con cifras y arreglos y ofrece aplicarlos (en una copia si es PQRD o tutelas).

### Cruzar flujos

- [x] **Comparar.** `GET /api/flujos/:id/comparar?con=` (`servidor/src/cruzar.ts`) compara declarativos, flujos de código o uno de cada. Devuelve:
  - los pasos que solo están en uno;
  - los pasos con distinta configuración, campo por campo;
  - las conexiones distintas;
  - los datos del flujo (nombre, descripción, IA);
  - los «parecidos» (mismo tipo y nombre con otro id).

  Vera lo hace con `comparar_flujos`.
- [x] **Traer pasos.** `POST /api/flujos/:id/traer {desde, nodos, entre?, aristas?, quitar?, renombrar?, simular?}` copia los pasos con sus conexiones internas:
  - renombra los ids que chocan (`limpiar` → `limpiar_2`);
  - con `entre` los inserta en esa conexión: el único comienzo recibe la conexión, y cada paso sin salida por defecto sigue hacia el destino;
  - ubica lo traído en el lienzo a la derecha;
  - avisa si usan variables de entorno que el destino no tiene o conexiones;
  - valida y guarda una versión nueva; el origen no se toca.

  En PQRD, tutelas y sus copias va por `estructuras.guardar`, así que solo entra dentro de un punto de inserción y con las variables de ese punto. No se copian las etapas del motor ni un disparador si el destino ya tiene uno. `simular=true` lo revisa sin guardar. Vera lo hace con `traer_nodos`.
- [x] **Encadenar.** Ya existían `subflujo` y `llamar_flujo` en `nodos_declarativos.py`: corren otro flujo declarativo que empiece con `entrada_manual`, hasta 3 niveles y nunca a sí mismo. Lo que faltaba:
  - Vera los conoce y los explica (SISTEMA).
  - `encadenar_flujos` agrega el paso «subflujo». Revisa que el llamado se pueda llamar y arma la entrada con los campos del llamado (si el que llama tiene campos con el mismo nombre). Si falta un campo, dice cuáles pide. Evita ciclos y guarda.
  - Un paso «subflujo» insertado en una copia de PQRD o tutelas ahora sí corre: la orden de sus pasos lleva `subflujos` (`flujos.ts`) y `estructura_codigo.correr_segmento` se los pasa a la ejecución. Antes fallaba con «no existe».
- [x] **Borrar varios.** `proponer_borrado` y `borrar_flujo` aceptan `ids=[…]` y dejan una sola propuesta `borrar_flujos` con un botón («Sí, borrar los 2»). Los originales y los que no existen quedan fuera, con su motivo. Nunca se borra sin el botón. El chat los borra uno por uno y dice cuál falló.
- [x] **Lo que la pantalla tenía y Vera no:**
  - `ver_programacion` / `programar_flujo`: la programación como el job de los flujos de código;
  - `ver_recursos` / `ajustar_recursos`;
  - `archivos_del_flujo`;
  - `reiniciar_trabajador`.

  Son 94 herramientas en el chat y 96 en la voz (las mismas más `responder_por_escrito`, `escribir_en_chat` y `terminar_llamada`, menos `cargar_herramientas`).
- [x] **Pantalla.** La cabecera del flujo trae «Comparar» y «Traer pasos» (`AccionesFlujo.tsx`). Abren `CruzarFlujos.tsx` (con su `cruzar.css`), que usa `Selector` y casillas propias. Tiene:
  - pestañas Comparar / Traer pasos;
  - las diferencias en rojo y verde;
  - «Traer» directo desde un paso que solo está en el otro flujo;
  - «Dónde» con los puntos de inserción en los flujos de código;
  - «Revisar sin guardar» y «Traer N pasos».

  En el teléfono el diálogo sale como hoja inferior.

### Pruebas (corridas el 2026-11-10)

- [x] Pruebas automáticas, todas en verde:

  | Suite | Resultado | Qué cubre |
  |---|---|---|
  | `servidor/src/pruebas_cruzar.ts` (nueva) | 5 de 5 | Comparar; traer a un declarativo con renombre y versión; rechazos; de una copia de PQRD a otra en «antes de validar»; la orden lleva `subflujos` |
  | `puente/pruebas_voz.py` | 9 de 9 (6 nuevas) | Paridad de las herramientas nuevas; saludo; aviso de pantalla en pausa; espacios; borrar varios; resumen al agotarse |
  | `puente/pruebas_estructura.py` | 15 de 15 (+1) | Un paso de PQRD llama a otro flujo y sigue según su desenlace |
  | `pruebas_declarativo.py` | 193 (1 omitida) | |
  | `pruebas_plataforma.ts` | 22 | |
  | `pruebas_estructura.ts` | 2 | |
  | `pruebas_aprobaciones.ts` | 4 | |
  | `pruebas_oauth.ts` | 7 | |

  `tsc` está limpio en el servidor y en la web.
- [x] `scripts/evaluar_vera.py` tiene 10 casos nuevos y una opción `--nombre`. Contra el contenedor: 10 de 10, más 3 de 3 de los casos anteriores de copias de PQRD. Los casos nuevos:
  - capacidades, por escrito y por voz;
  - diagnóstico, por escrito y por voz;
  - comparar;
  - traer pasos;
  - encadenar y correr;
  - borrar dos, por escrito y por voz;
  - el pedido real de varias ediciones (ahora 7 herramientas en 29 s, sin cortarse).
- [x] Tope forzado (`VERA_MAX_VUELTAS=4`): la respuesta dice «Quedó hecho: agregué … en `ev-tope`: guardado, versión 1» en vez del texto genérico.
- [x] Llamada de voz real por `/api/voz` con Gemini Live (texto en vez de micrófono). El script reporta la pantalla al conectar y al abrir un flujo, como el navegador. Resultados:
  - Saludo completo sin nombre; ninguna interrupción.
  - Capacidades, abrir PQRD, diagnóstico («De 30 corridas, seis fueron a revisión manual…»), comparar, traer (versión 2) y un botón para borrar dos copias, que siguen existiendo.
  - Lo guardado no tiene frases cortadas ni palabras pegadas.
  - El script está en el scratchpad: `prueba_voz_cruzar.py`.
- [x] Navegador real (Playwright):
  - Comparar muestra las diferencias.
  - «Traer» desde la comparación y «Revisar sin guardar» dicen «Se puede traer así».
  - Al traer, se guarda la versión 2 y el origen queda intacto.
  - En 390 px el diálogo cabe sin desbordar.

### Pendiente

- [ ] En el teléfono la barra del flujo esconde Renombrar, Clonar, Comparar y Traer pasos: es el estilo de la barra (`archivos.css`, del rediseño), así que hoy esas acciones solo están en pantallas anchas o por Vera.
- [ ] Probar la voz con micrófono real en el navegador (la prueba fue por la ruta de voz con texto).
- [ ] Faltan operaciones que la pantalla tiene y Vera no:
  - borrar archivos del flujo, correos de la bandeja de prueba, perfiles, conversaciones y plantillas de documentos; hoy solo los lista o no los toca, y si se agregan deben ir con botón como el borrado de flujos;
  - editar una plantilla de documento existente.
- [ ] La comparación de flujos de código mira la estructura (etapas, pasos insertados y conexiones), no los parámetros del motor de cada copia (`ajustar_parametro_pqrd` es global de PQRD).

## 5i. Espacio, desbordes y el escáner que no se veía (pedido 2026-11-10)

Jonathan vio textos desbordados, espacio mal usado y que la animación del escáner mostraba imágenes rotas. Los arreglos de diseño van en `web/src/espacio.css`, cargado al final.

- [x] Hojas de los adjuntos en 404: `/api/corridas/:id/archivos/:archivo` (archivos que salen) tapaba la ruta de las imágenes de cada hoja. `historial.hojaDelVisor` las sirve desde las dos rutas. Probado en vivo: escaneo visible en «Adjuntos» y en el nodo de conversión.
- [x] Adjuntos que salen de la caché de conversión no pasaban por el visor (el motor no llama a `conversion.convertir`): ahora se anuncian con cada hoja renderizada y método `cache` («Leída antes»). c0070: 37 de 37 hojas con imagen.
- [x] Registro: el texto de cada evento caía en una columna de 20 px (la fila tenía una columna de más).
- [x] Barra superior: nombre del flujo sin cortar; acciones en iconos por debajo de 1600 px; «Simulación» en vez de «Radicación real: apagada (simulación)».
- [x] Flujo: la correspondencia cede su espacio al abrir un paso o una ejecución (entre 1100 y 1720 px) y vuelve al cerrarlo; con el buzón al día muestra los últimos descargados para correrlos; minimapa junto a los controles y cerrado por defecto bajo 1680 px; sin banda de reglas vacía; la banda de la corrida baja bajo la barra en lienzos angostos; etiquetas de cables de ida y vuelta separadas.
- [x] Inspector: «Se radicaría · simulación» en vez de DRY-RUN-ENT; nombres de campo enteros y valores que bajan de línea; en celular, campo en dos renglones.
- [x] Tablero con tokens compactos; Adjuntos con más ancho para la hoja; Inicio sin el texto apretado por el sello.
- [ ] En el celular la barra del flujo esconde Renombrar, Clonar, Comparar y Traer pasos (solo por Vera).
- [ ] En vivo, algunas líneas de hoja salen repetidas en el panel de proceso (fusión de eventos del canal con los ya cargados).

## 5j. Marca en los documentos, Vera en paralelo y un chat que respira (pedido 2026-11-10)

Jonathan pidió que Vera ponga el logo, un banner o una marca de agua que le pasen por el chat (con opacidad, colores y todo), también en un flujo para masivos; que consulte esas opciones con sus herramientas en vez de cargarlas en el prompt; paralelismo; y un chat que no se comprima con los pasos.

- [x] `puente/marca.py`: la marca de un documento `{logo {imagen, posicion, ancho_cm}, banner {imagen, posicion arriba|abajo|ambos, alto_cm}, marca_agua {imagen | texto, opacidad, tamano, rotacion, color}, colores {titulos, acento, tabla_encabezado, tabla_texto, texto}, fuente}`, su validación y cómo se pone en cada formato. Opacidad de verdad: PDF con ExtGState `/ca` y SMask en el generador propio (`documentos._PDF`, que además corre el texto para no quedar bajo el logo y pinta títulos, acento y encabezado de tablas), PowerPoint con `alphaModFix` (marca de agua al fondo de cada diapositiva), Word con la imagen aclarada con Pillow, anclada detrás del texto en el encabezado, y estilos de títulos, tablas y fuente; HTML con CSS; PNG/JPG con Pillow. A un PDF que ya existe se le estampa una hoja transparente por página (pypdfium2, `page_as_xobject`). Sin dependencias nuevas.
- [x] Flujos: `marca` en `documento_generar` y `documento_plantilla` (un recurso suelto, «logo», es de esa plantilla) y el nodo nuevo `documento_marca` («Aplicar marca»), que marca hasta 100 documentos de la corrida o del lote (generados, subidos, adjuntos o filtros) y deja la lista de los marcados; los formatos que no la admiten se saltan con aviso. Imágenes: «{{logo}}», un id «a-…» o «plantilla/recurso» de Documentos. Zod en paridad (`flujos_salidas.ts`, `marcaZod`), catálogo y paleta como los demás.
- [x] Vera: `generar_documento` con `marca`, `aplicar_marca` (a lo que hay en el chat: lo que adjuntó la persona o lo que ella generó) y `ayuda_documentos` (opciones, imágenes, formatos y ejemplos de chat y de flujos). El prompt solo nombra la capacidad en una línea; las herramientas de marca van en el grupo «documentos» (se carga por las palabras del pedido o con cargar_herramientas).
- [x] Paralelismo: cuando el modelo pide varias herramientas en una vuelta, las que solo leen (y las que arman un archivo nuevo en el chat) corren a la vez en hasta 4 hilos (`VERA_PARALELO`); las que mueven la pantalla, cambian un flujo o corren algo hacen de barrera y van solas, en orden. Las respuestas vuelven en el orden pedido; los avisos llevan `indice` (y `paralelo`) para que el servidor y el chat emparejen cada resultado; la salida tiene candado. Igual con los otros proveedores (`ejecutar_lote` en `proveedores/nucleo.py`). El prompt dice, en una línea, que pida juntas las independientes.
- [x] Chat: los pasos son una línea plegable («Hizo 6 pasos · algunos a la vez»), en vivo la misma línea cambia («3 pasos a la vez…») y al abrirla hay una línea de tiempo con el detalle de cada paso y su resultado plegado en una caja con scroll propio; mensajes con más aire; botón para ampliar el MISMO panel (860 px de ancho y casi toda la altura, conversación en columna; en celular ya es pantalla completa); las tareas de la llamada en una sola línea; el vacío ya no presenta a Vera. Revisado a 1440, 1280 y 390 px.
- [x] Pruebas (`pruebas_salidas.py`, 28): marca en PDF, Word, PowerPoint y HTML (transparencia, ExtGState, ancla detrás del texto, alphaModFix, colores, fuente), `documento_marca` sobre tres documentos con un recurso de la biblioteca y un .txt que se salta, plantilla con su logo, estampado de un PDF y una imagen, validación en paridad con el zod, tandas y orden del paralelismo, avisos emparejados y las herramientas de Vera con adjuntos falsos. De punta a punta por la API: un chat con un logo adjunto pidió Word y PDF con el logo arriba y marca de agua al 15 % (Vera los armó a la vez), y un flujo con `documento_marca` marcó un PDF, un Word y un PowerPoint.
- [ ] Un PDF que ya existe recibe la marca encima: si su texto empieza muy arriba, el logo puede quedar sobre él (para eso, la marca al generarlo). Las páginas giradas (/Rotate) no se corrigen.
- [ ] Propuesta: una herramienta `en_lote` para que Vera mande en una sola llamada varias tareas largas (correr varios flujos y esperar), con avance por tarea.

## 6. Paso a producción (cuando aprueben)

- [x] Documento de despliegue (`docs/PRODUCCION.md`): principios, entorno y secretos de PQRD y tutelas con sus nombres reales, conexiones, cómo jala la información cada flujo, qué escribe y los pasos para salir a producción.
- [x] `docs/PRODUCCION.md` completo: lo verificado en los jobs, las rutas de la plataforma y el script de despliegue.
- [x] Mapeo del entorno de cada flujo a Secret Manager y Cloud Run (`scripts/manifiesto_a_gcp.py`, solo imprime comandos).

## 7. Pendientes menores

- [x] Descripciones del catálogo con tildes (`catalogo/generador/tildes.py`; corrige solo textos para leer, nunca claves, valores ni nombres de código).
- [x] Limpiar del scratchpad los datos personales de los análisis (logs, buzón, OCR, corpus, capturas de casos).
- [x] Borradas las capturas de interfaz y el clon de telar.
