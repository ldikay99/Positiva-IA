# Divergencias con producción

`motor/` nació como copia sin cambios de `mcp-fix` en `7540e18` (ver `ORIGEN`). Desde el 2026-11-09 diverge: aquí se mide y se aligera el motor. Cada fila dice qué cambió, por qué, cómo se midió y si conviene llevarlo a producción. Las cifras completas están en `docs/RENDIMIENTO_MOTOR.md`.

Para portar un cambio basta con su commit: todos tocan solo `motor/` (salvo los marcados como de la plataforma) y traen sus pruebas.

## Agente y modelo

| Cambio | Por qué | Llevar a producción |
|---|---|---|
| **El agente recibe de entrada el inventario de documentos y las pistas de cada campo pendiente** (`agente.inventario`, `agente.pistas`, `AGENTE_CONTEXTO_INICIAL=no` lo apaga). La cabecera del prompt le pide pedir varias herramientas por vuelta. | La primera vuelta se iba siempre en `listar_documentos` y dos o tres más en ubicar cada dato. Medido en 60 correos: vueltas del agente de 5,15 a 2,85 por correo, llamadas de 312 a 171 y tokens de entrada de 1,46 M a 0,98 M. | **Sí.** Es el cambio de mayor efecto en tiempo y tokens. Las pistas van delimitadas como cualquier lectura de documento (`sanear_fragmento`). La dirección y el teléfono no llevan pista: con ella, en el corpus etiquetado, el agente reportó cuatro direcciones donde el correo no trae la del afectado (el membrete de la IPS, el empleador). |
| `buscar_en_documentos` acepta `documento` (por id o por nombre de archivo). | El modelo la pedía así y la herramienta respondía error: era una vuelta perdida. | **Sí.** |
| **El razonamiento se cuenta como salida** (`Turno.tokens_salida` lo incluye; `tokens_razonamiento` y `tokens_cache` aparte; `Contador.registrar_respuesta` también). | Vertex lo factura como salida y el informe de la corrida no lo veía: el costo real era casi el doble del reportado. | **Sí.** Solo corrige la contabilidad. |
| **La transcripción por visión tiene tope de salida (3.000 tokens) y de tiempo (40 s)**; si llega al tope, la hoja conserva lo de tesseract. | En la línea base 4 de 116 transcripciones no volvieron: el modelo repetía un renglón hasta que la llamada se cortaba a los 90 s (39 % del tiempo de visión). | **Sí.** |
| `RAZONAMIENTO_TOKENS` acota el razonamiento del agente y del revisor (sin la variable, el del modelo). | Con 512, en 60 correos el costo bajó 31 % y el tiempo del agente a la mitad, pero en el corpus etiquetado el agente erró más el tipo de solicitud y tomó el documento de otra persona en un caso. | **No** con 512: la perilla queda apagada por defecto. |

## Conversión y OCR

| Cambio | Por qué | Llevar a producción |
|---|---|---|
| **Las hojas escaneadas de un PDF se leen a la vez** (`OCR_PAGINAS_EN_PARALELO`, por defecto mín(4, núcleos)), con un cupo de lecturas por proceso igual a los núcleos (`OCR_TESSERACT_A_LA_VEZ`) y de hojas renderizadas en memoria. La hoja se renderiza en el hilo del documento y a los hilos solo va la imagen: PyMuPDF compartido entre hilos tumbó al trabajador con SIGSEGV. | Un escaneo de quince hojas se leía de a una aunque hubiera núcleos libres. Con el cupo, cinco correos a la vez ya no ponen más tesseract que núcleos (en el job, dos). | **Sí.** En el job (2 vCPU) el cupo es 2: menos contención que hoy, que pone hasta cinco tesseract sobre dos núcleos. |
| **Tope de píxeles al renderizar una hoja** (`LADO_MAXIMO_OCR_PX = 4200`). | Hay PDF cuya página mide lo que la foto (2550 × 3507 pt): a 300 dpi eran 155 MP por hoja, tesseract se cortaba y todo iba a visión. En c0031 la conversión pasó de 352 s a 51 s y tesseract leyó las seis hojas con confianza 0,88–0,92 sin una sola llamada de visión. | **Sí.** |
| **Las hojas en blanco no pasan por OCR** (tinta < 0,05 %). | Una hoja en blanco costaba cinco pasadas de tesseract (lectura, OSD, tres giros) y una llamada de visión. Medido sobre 143 hojas escaneadas: las 4 en blanco tienen menos de 0,03 % de tinta y la hoja con texto que menos tiene, 0,6 %. | **Sí.** |
| La hoja se arma con los píxeles del render, sin pasar por PNG; a tesseract se le entrega en PPM (sin comprimir). | Comprimir y descomprimir una hoja a 300 dpi costaba hasta medio segundo por hoja y por lectura, y no cambia un píxel (comprobado: misma salida de tesseract). | **Sí.** |
| `_sin_cuadricula` trabaja por franjas de 256 filas. | De una vez pedía cerca de 300 MB de arreglos por hoja; con hojas en paralelo el proceso llegó a 13 GB. | **Sí.** |
| `malloc_trim` tras cada adjunto (`conversion.devolver_memoria`). | glibc se queda con la memoria de las imágenes ya liberadas y el proceso nunca baja de su pico. | **Sí.** |
| **HEIC/HEIF (fotos de iPhone) se leen con visión**: se reconocen por su cabecera ISO-BMFF (`ftyp` + marca) y van tal cual a Gemini con `image/heic` o `image/heif` (`conversion.imagen_con_vision`, `ModeloVertex.transcribir_imagen(datos, mime)`), con la misma marca de «transcrito por IA» y dentro del cupo de imágenes del correo; sin modelo con visión quedan como no soportados. | Un correo real del buzón con 8 fotos HEIC salía con los 8 adjuntos «tipo no soportado». No hay decodificador con licencia permitida: libheif y libde265 son LGPL y x265 GPL (`pillow-heif` los trae), así que tesseract no puede verlas. Medido con ese correo en Vertex (`gemini-2.5-flash`): 8 de 8 leídas (7 llamadas; la repetida sale de la caché), 3.439 tokens de entrada por foto, 164 a 948 de salida, 2 a 7 s cada una. En la plataforma, con `gemini-3.1-flash-lite`, 1.149 de entrada y 171 a 1.011 de salida; en una corrida 2 de 7 fotos se cortaron por repetir un renglón, así que un HEIC cortado se pide una vez más (no tiene tesseract de respaldo). | **Sí.** Ojo: ocho fotos agotan el cupo de visión del correo (8); si además hay hojas flojas, las últimas quedan ilegibles y el correo va a revisión manual, como hoy. |
| WEBP y AVIF se reconocen por su cabecera y pasan por el OCR (Pillow 12 los abre); un TIFF de varias hojas se lee hoja por hoja (antes solo la primera). | Antes caían en «desconocido» aunque Pillow los abría. En el universo hay un TIFF de una hoja, que no cambia. | **Sí.** |
| Un `smime.p7m` que es un correo firmado en claro (`multipart/signed`) se lee con el parser de correo de la biblioteca estándar: el texto y los documentos que traiga. | En el universo dos correos traen así el mensaje y quedaban «el sobre firmado no trae ningún documento reconocible». | **Sí.** |
| **Caché de la lectura de cada adjunto por su huella** (`pipeline/cache_conversion.py`): memoria siempre, disco con `RUTA_CACHE_CONVERSION`. Un acierto descuenta del presupuesto lo que costó la primera vez. | El mismo archivo llega en varios correos (reenvíos del hilo) y un correo aplazado se vuelve a leer entero en la corrida siguiente. | **Sí, en memoria** (gana dentro de la corrida). Entre corridas el job necesita un almacén que sobreviva (el bucket de `RUTA_LOGS`): queda pendiente. |

## Extracción y compuerta

| Cambio | Por qué | Llevar a producción |
|---|---|---|
| La dirección se lee también con «Dir:» y «Dirección para notificaciones/de domicilio/actual:»; la de «ARL POSITIVA» no se toma por la del afectado. | Las historias clínicas rotulan «Dir:» en el bloque del paciente y esa dirección se le pedía al agente. En el corpus etiquetado la dirección por algoritmo pasa de 81 % a 90 % de acierto (la de Positiva era uno de los dos fallos). | **Sí.** |
| La compuerta devuelve al agente el documento del afectado que es un recorte (uno o dos dígitos menos) de otro que lo escrito (lo que no salió del OCR) trae junto al mismo nombre, si lo escrito no trae el corto; el prompt dice que vale lo escrito sobre lo leído por OCR. | En el universo el agente tomó «2.417.396» del OCR de la foto de la cédula cuando el cuerpo decía dos veces la cédula entera. En la línea base de producción la misma regla encuentra un recorte que sí se radicaba (seis dígitos de una cédula de ocho). | **Sí.** |
| La compuerta devuelve al agente el documento que propuso cuando no aparece en ninguna parte junto al afectado y el texto sí escribe otro junto a él (sin contar celulares, fechas ni trozos del mismo número). | El agente tomó de un FURAT el documento del «Responsable» y de una planilla transcrita el de otro bloque. Sobre las 78 y 88 radicaciones con documento del agente de dos corridas del universo, la regla salta solo en esos dos casos. | **Sí.** |
| Los adjuntos nombrados «<documento>-<NOMBRE>» cuentan para `VARIOS_AFECTADOS`. | Un correo con cuatro FURAT, uno por persona, se radicaba a nombre de una. | **Sí.** |
| La compuerta no envía como dirección lo que el OCR dejó ilegible (dos o más signos que ninguna dirección lleva). | Con las pistas el agente encontró el rótulo en un formulario escaneado y reportó «calle 9 B N.&! -7..», que pasaba por llevar «barrio». | **Sí.** |

## Harness

| Cambio | Por qué | Llevar a producción |
|---|---|---|
| `harness/medicion.py`: cada llamada al modelo con quién la hizo, tokens de entrada, salida, razonamiento y caché, y su tiempo; vueltas y herramientas del agente; CPU y memoria pico; resumen y comparación de dos corridas correo por correo con la comprobación de sustento. `harness.universo` lo usa y corre el universo entero sin muestra. | Sin esto no se podía decir dónde se iba el tiempo ni el dinero. | **Sí.** No toca el pipeline. |

## Solo en la plataforma

- `puente/trabajador.py`: con la caché del motor, la plataforma le da su carpeta y anuncia cada acierto (evento `cache_conversion`); `modelo_fin` lleva los tokens de razonamiento y de caché.
- `web/`: el detalle de cada ejecución muestra el tiempo por etapa, las llamadas al modelo por quién, los tokens (de entrada desde la caché y de salida en razonamiento), las hojas leídas con OCR y los adjuntos tomados de la caché.
