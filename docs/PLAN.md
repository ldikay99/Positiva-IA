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

- [x] Saludo propio al conectar, según la hora («Buenas noches, habla Vera, cuéntame.»); despedidas variadas y ligadas a lo que se hizo; propone en vez de preguntar en cadena.
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
- [ ] Inicio que explica el producto en una mirada: qué es, los flujos con su estado, lo que pasó hoy y qué hacer después (crear, correr, revisar).
- [ ] Primeros pasos guiados para quien entra la primera vez: crear su primer flujo (a mano, desde plantilla o con Vera), correrlo y ver el resultado.
- [ ] Galería de plantillas de flujos listas para usar (extraer datos de documentos, clasificar correos, responder con IA, revisar tablas, informe programado, webhook a base de datos…), que se instalan como flujo propio y se ajustan.
- [ ] Lenguaje de producto en toda la interfaz: nada de jerga interna (corrida, nodo, desenlace) sin explicación; ayudas contextuales donde haga falta.
- [ ] Historial de ejecuciones por flujo con filtros (estado, fecha, desenlace), búsqueda, detalle legible y reintentar.
- [ ] Notificaciones dentro de la plataforma: corridas que fallan, aprobaciones pendientes, conexiones vencidas, avisos de recursos.
- [ ] Vista de cada corrida legible para quien no es técnico: qué entró, qué hizo cada paso, qué salió, por qué terminó así.
- [ ] Coherencia visual y de interacción en todas las vistas (mismos patrones de tarjetas, listas, botones, estados vacíos, cargando y error), responsiva.

### Nodos y herramientas que un usuario necesita
- [ ] Aprobación humana: el flujo se detiene, alguien aprueba o rechaza (con comentario y cambios) desde la plataforma, y sigue por la rama que toca; con tiempo límite.
- [ ] Documentos: generar PDF o Word desde plantilla, unir o dividir PDF, convertir formatos, clasificar el tipo de documento con IA, comparar dos documentos.
- [ ] Datos personales: anonimizar o enmascarar (cédulas, nombres, teléfonos, correos) antes de enviar a una IA externa o a un informe.
- [ ] Hojas de cálculo: escribir y actualizar Excel/CSV y Google Sheets (lectura ya existe).
- [ ] Mensajería: Teams, Slack y webhooks salientes (escritura habilitada por una persona).
- [ ] IA: traducir, resumir largo por partes, responder preguntas sobre documentos (con citas), extraer tablas.
- [ ] Datos: deduplicar por huella, agrupar y sumar, ordenar y filtrar listas, mapear campos de un formato a otro, validar contra listas o tablas.
- [ ] Control: subflujos con salida, reintentar más tarde, límite de tasa, ventana de horario (solo días hábiles), detenerse con alerta.
- [ ] Cada nodo nuevo con ficha completa (ayuda, ejemplos, consejos), validación Python y zod en paridad, y pruebas.

### Vera en paridad con la pantalla
- [ ] Todo lo nuevo se puede hacer también con Vera (plantillas, aprobaciones, historial, notificaciones, nodos nuevos) y la evaluación de Vera crece con casos de esto.

## 6. Paso a producción (cuando aprueben)

- [x] Documento de despliegue (`docs/PRODUCCION.md`): principios, entorno y secretos de PQRD y tutelas con sus nombres reales, conexiones, cómo jala la información cada flujo, qué escribe y los pasos para salir a producción.
- [x] `docs/PRODUCCION.md` completo: lo verificado en los jobs, las rutas de la plataforma y el script de despliegue.
- [x] Mapeo del entorno de cada flujo a Secret Manager y Cloud Run (`scripts/manifiesto_a_gcp.py`, solo imprime comandos).

## 7. Pendientes menores

- [x] Descripciones del catálogo con tildes (`catalogo/generador/tildes.py`; corrige solo textos para leer, nunca claves, valores ni nombres de código).
- [x] Limpiar del scratchpad los datos personales de los análisis (logs, buzón, OCR, corpus, capturas de casos).
- [x] Borradas las capturas de interfaz y el clon de telar.
