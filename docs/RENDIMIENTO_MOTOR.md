# Rendimiento del motor de PQRD

Medido del 2026-11-09 sobre `motor/` (copia de `mcp-fix` en `7540e18`) y sobre el motor mejorado, con Vertex de verdad (`gemini-2.5-flash`) y nada escrito en sistemas del cliente (MODO_TEST, buzón de disco, radicador simulado, registro de personas apagado). Las cifras salen de `harness/medicion.py`; los datos por correo quedan fuera del repositorio. Lo que diverge de producción y si conviene llevarlo, en `motor/DIVERGENCIAS.md`.

## Cómo se midió

- **Universo**: los 389 correos reales de la bandeja (`harness.universo`), de a 5 a la vez como el job, contenedor de 8 núcleos y 16 GB. Por correo: desenlace, motivo, campos enviados, tiempo por etapa, cada llamada al modelo (quién, tokens de entrada, salida, razonamiento y caché, tiempo y fallo), vueltas y herramientas del agente y lo que se leyó con OCR. Por corrida: reloj, CPU propia y de tesseract y memoria pico de todos los procesos.
- **Costo**: precios de Vertex para `gemini-2.5-flash` (0,30 USD por millón de entrada, 2,50 de salida, 0,03 la entrada servida de la caché implícita). El razonamiento se cobra como salida: el contador del harness viejo no lo veía y reportaba cerca de la mitad del costo real.
- **Ruido**: el agente no es determinista. Se repitieron 60 correos con el mismo código: **24 de 60 cambian algún campo y 4 cambian de desenlace** sin tocar nada. Toda comparación se lee contra ese ruido.
- **Calidad con etiqueta**: `harness.punta_a_punta` sobre el corpus etiquetado (102 casos), dos veces con el motor de producción, para tener su ruido, y con cada variante.
- **Corpus sin modelo**: `harness.regresion` (tolerancia de 3 pp) y `harness.exactitud`.

## Línea base: dónde se iba el tiempo, la memoria y el dinero

Universo completo con el motor de `7540e18`:

| | |
|---|---|
| Reloj de la corrida | 4.722 s (78,7 min), 4,94 correos por minuto |
| Por correo | mediana 33,2 s · p95 211,1 s · máximo 499,6 s |
| CPU | 3.057 s del proceso + 2.548 s de tesseract (1,19 núcleos en promedio) |
| Memoria pico | 8.246 MB |
| Desenlaces | 290 se radicarían · 99 a revisión manual |
| Llamadas al modelo | 2.576: agente 1.898, revisor 346, visión 332 (6,6 por correo) |
| Tokens | entrada 10,8 M (27.750 por correo), salida 0,31 M, razonamiento 1,53 M |
| Costo | 6,66 USD (0,0171 por correo); el contador viejo decía unos 3,4 |
| Agente | 4,83 vueltas por correo (p95 12); agotó las vueltas 106 veces |
| Herramientas | `reportar_hallazgo` 1.317, `listar_documentos` 381, `buscar_en_documentos` 342, `leer_documento` 132 |
| OCR | 860 hojas leídas en 166 correos, 36 perdidas por presupuesto, 47 sin texto |
| Visión | 332 transcripciones, 16 no volvieron (90 s cada una: la mitad del tiempo de visión) |

Suma por etapa en todos los correos (la revisión incluye la corrección que dispara):

| Etapa | Suma | Mediana | p95 |
|---|---|---|---|
| Conversión y OCR | 9.582 s | 1,2 s | 147,4 s |
| Agente | 7.324 s | 13,0 s | 57,1 s |
| Corrección | 3.605 s | 0 s | 51,0 s |
| Revisor | 2.820 s | 4,5 s | 23,5 s |
| Extracción, compuerta, descarga | 92 s | 0,02 s | 0,3 s |

Lo que más pesaba, por orden:

1. **El agente daba vueltas para ubicarse.** Casi siempre la primera vuelta era `listar_documentos` y luego buscaba y leía; cada vuelta reenvía el prompt de sistema, las herramientas y la tarea. El razonamiento (600 tokens por vuelta contra 70 de respuesta) es la mayor parte de lo que tarda y cuesta cada una.
2. **El OCR de unos pocos PDF.** Hay PDF cuya página mide lo que la foto que llevan (2550 × 3507 puntos): a 300 dpi eran 155 megapíxeles por hoja, tesseract se cortaba por tiempo y la hoja terminaba en visión. Esos correos se llevaban minutos y la memoria pasaba de 7 GB.
3. **Visión que no volvía**: el 5 % de las transcripciones se quedaba repitiendo un renglón hasta el corte de 90 s.
4. Las hojas de un PDF se leían de a una aunque sobraran núcleos.

El código propio (extracción, compuerta, índice) no pesa: menos de 0,1 s por correo.

## Después: el universo completo con el motor nuevo

Mismo universo, mismas condiciones (5 a la vez, contenedor de 8 núcleos), el motor de este repositorio en `6bd263e`. Durante parte de la línea base corrieron a la vez otras mediciones en la misma máquina; la corrida final corrió sola. Para no exagerar la ganancia de tiempo, ver también el barrido de concurrencia, donde los dos motores corrieron uno tras otro sobre la misma muestra.

| Métrica | Antes (`7540e18`) | Después | Cambio |
|---|---|---|---|
| Reloj de la corrida | 4.722 s | **2.717 s** | −42 % |
| Correos por minuto | 4,94 | **8,59** | +74 % |
| Por correo, mediana | 33,2 s | **25,0 s** | −25 % |
| Por correo, p95 | 211,1 s | **102,0 s** | −52 % |
| CPU del proceso | 3.057 s | **1.122 s** | −63 % |
| CPU de tesseract | 2.548 s | 2.743 s | +8 % (34 hojas más leídas) |
| Memoria pico | 8.246 MB | **3.002 MB** | −64 % |
| Llamadas al modelo | 2.576 | **1.879** | −27 % |
| · del agente | 1.898 | **1.225** | −35 % |
| · del revisor | 346 | 352 | = |
| · de visión | 332 (16 sin volver) | 302 (5 sin volver) | −9 % |
| Vueltas del agente por correo | 4,83 (p95 12) | **3,22 (p95 7)** | −33 % |
| `listar_documentos` | 381 | **2** | −99 % |
| `buscar_en_documentos` + `leer_documento` | 474 | 346 | −27 % |
| Tokens de entrada | 10,79 M | **8,69 M** | −19 % |
| Tokens de razonamiento | 1,53 M | 1,43 M | −7 % |
| Costo en Vertex | 6,66 USD | **6,20 USD** | −7 % |
| Hojas leídas con OCR | 860 | 894 | +34 |
| Hojas perdidas por presupuesto de OCR | 36 | **0** | |
| Hojas sin texto | 47 | 14 | |
| Desenlaces | 290 radicaría · 99 manual | 295 radicaría · 94 manual | |

Suma por etapa en todos los correos:

| Etapa | Antes | Después | p95 antes → después |
|---|---|---|---|
| Conversión y OCR | 9.582 s | **3.359 s** (−65 %) | 147,4 s → 44,4 s |
| Agente | 7.324 s | **5.542 s** (−24 %) | 57,1 s → 40,4 s |
| Corrección | 3.605 s | 2.253 s (−38 %) | 51,0 s → 34,9 s |
| Revisor | 2.820 s | 2.348 s (−17 %) | 23,5 s → 20,9 s |

El costo baja poco porque más de la mitad de lo que se paga es razonamiento, y ese no se tocó: con un tope de 512 tokens el costo bajaba un tercio más, pero el agente se equivocó más en el corpus etiquetado (ver «Lo que se probó y no se dejó»).

## Calidad

### Corpus etiquetado, de punta a punta con el modelo (102 casos)

| | Acierta | Falla | Calla | Obligatorios 100 % correctos de lo que pasa |
|---|---|---|---|---|
| Motor de producción, corrida 1 | 512 | 24 | 26 | 69 de 77 (90 %) |
| Motor de producción, corrida 2 | 509 | 26 | 27 | 67 de 76 (88 %) |
| **Motor nuevo** | **515** | **25** | **22** | 67 de 77 (87 %) |

Por campo, el nuevo queda dentro del ruido de las dos corridas de producción, mejor en el número de documento del afectado (68 aciertos y 0 fallas, contra 67/0 y 67/1) y en el sexo, y con uno o dos casos menos en tipo de solicitud y dirección, que son los campos que más varían entre dos corridas iguales.

`harness.regresion`: pasa (sale 0); ningún campo baja más de 1 pp; la dirección por algoritmo baja de 10 % a 9 % porque ya no toma la de Positiva por la del afectado. `harness.exactitud`: 98 % (antes 97 %); la dirección por algoritmo, de 81 % a 90 % de acierto.

### Universo, correo por correo

Contra la línea base cambian 153 de 389 correos en algún campo y 27 de desenlace. El ruido medido (el mismo motor dos veces) es 40 % de correos con algún cambio y 7 % de desenlaces: el nuevo cambia contra la línea base lo mismo que cambia el motor contra sí mismo.

- **16 pasan de manual a radicarse.** En los 16 el documento y el nombre del afectado aparecen juntos en el texto (comprobación de `harness/medicion.sustento`, revisada a mano). Uno es dudoso: el afectado radicado es el representante legal de la empresa que escribe, y el criterio dice que si la empresa pide para sí la afectada es la empresa.
- **11 pasan de radicarse a manual.** Ninguno escribe un dato malo: van a revisión por un obligatorio que falta o porque la compuerta rechaza. Repitiendo diez de ellos, cuatro se radicaron: es variación del agente. De los demás, dos son correos de una empresa que lista varias entidades (el agente se abstuvo) y dos son correos donde el OCR ahora lee hojas que antes se perdían por presupuesto y el algoritmo encuentra otro nombre, que la compuerta no deja pasar.
- **Documento del afectado distinto en lo que se radica (4)**: uno corrige a la línea base (radicaba un recorte de la cédula: los seis últimos dígitos de ocho), uno es el mismo NIT sin el dígito de verificación y dos son correos con listas de varias personas en los que los dos motores eligen a alguien (debilidad que ya estaba).
- **Retrocesos corregidos antes de la corrida final**, con regla y prueba: el agente tomó la cédula del OCR de una foto con un dígito menos teniendo la escrita en el cuerpo (`DOCUMENTO_RECORTADO`), el documento del «Responsable» de un FURAT en vez del del trabajador y el de una planilla transcrita en vez del del cuerpo (`DOCUMENTO_LEJOS_DEL_AFECTADO`), y radicó a una de cuatro personas de un correo cuyos adjuntos van nombrados con el documento de cada una (esos nombres de adjunto ahora cuentan para `VARIOS_AFECTADOS`). Repetidos con el motor final, quedan bien los cuatro.

Ninguna radicación nueva sin sustento en el texto.

## Volumen

Sin modelo (sin visión), un correo a la vez, archivos reales del universo:

| Caso | Antes | Después | Con la caché caliente |
|---|---|---|---|
| PDF escaneado de 80 hojas (se leen 60) | 244,0 s · 58 hojas · 2 perdidas por presupuesto · 1.615 MB | **72,8 s** · 59 hojas · 0 perdidas · 1.472 MB | 0,1 s |
| Correo de 30 adjuntos (PDF, fotos, Word) | 136,6 s · 39 hojas · 967 MB | **72,6 s** · 39 hojas · 1.361 MB | 0,0 s |

- El PDF largo se lee 3,4 veces más rápido: sus hojas van a la vez (4 por documento, con un cupo de lecturas igual a los núcleos para todo el proceso).
- El correo de 30 adjuntos gana menos porque los adjuntos se siguen convirtiendo uno tras otro (solo las hojas de cada PDF van a la vez) y usa más memoria por las hojas en vuelo. Con más de 30 adjuntos u 80 hojas el correo va a revisión manual antes de convertir (`volumen.py`), como antes.
- **Ráfaga**: el universo entero (389 correos, 5 a la vez) no tumbó la corrida en ninguno de los dos motores; con el nuevo la memoria pico bajó de 8,2 a 3,0 GB y ninguna hoja se perdió por presupuesto. En la plataforma, un lote «como el job» de los seis correos más pesados (hasta 15 hojas escaneadas cada uno) corrió sin fallos y, repetido, convirtió cada correo en menos de un segundo.
- La caché por contenido hace que releer un correo (un aplazado, un reenvío, una prueba) cueste lo que leer un JSON.

## Concurrencia: los dos motores, uno tras otro, sobre los mismos 20 correos

Muestra fija de 20 correos elegidos al azar; cada fila corrió sola en la máquina.

| A la vez | Motor | Reloj | Correos/min | Mediana | p95 | Memoria pico | CPU total | Llamadas | Costo |
|---|---|---|---|---|---|---|---|---|---|
| 1 | producción | 1.007 s | 1,19 | 30,7 s | 145,3 s | 937 MB | 368 s | 137 | 0,377 USD |
| 1 | nuevo | **813 s** | **1,48** | 28,6 s | **87,2 s** | 1.339 MB | 399 s | **120** | 0,398 USD |
| 5 | producción | 312 s | 3,85 | 36,5 s | 153,2 s | 1.732 MB | 415 s | 150 | 0,390 USD |
| 5 | nuevo | **292 s** | **4,10** | 33,8 s | **96,3 s** | 2.100 MB | 469 s | **108** | 0,352 USD |
| 10 | producción | 255 s | 4,71 | 45,5 s | 197,0 s | 2.255 MB | 518 s | 156 | 0,397 USD |
| 10 | nuevo | **177 s** | **6,78** | 57,5 s | **109,1 s** | 2.320 MB | 532 s | **114** | 0,374 USD |

- En una muestra corta la ganancia es menor que en el universo (−19 % a −30 % de reloj) porque pocos de esos 20 correos traen escaneos grandes; el p95 baja en todas las concurrencias (−40 % a −45 %), que es donde estaban esos correos.
- Con 10 a la vez el motor nuevo sube a 6,8 correos por minuto (+44 %); el de producción casi no gana pasando de 5 a 10, porque se le juntan los OCR en los mismos núcleos. La mediana del nuevo con 10 sube porque el cupo de lecturas hace esperar a los correos con OCR en vez de dejarlos competir.
- El costo por correo casi no depende de la concurrencia; el nuevo hace entre 12 % y 28 % menos llamadas.
- Con un solo correo a la vez el nuevo usa más memoria (hasta cuatro hojas de 300 dpi en vuelo); con varios, lo mismo que producción.

## Qué se cambió (commits)

| Commit | Qué |
|---|---|
| `be96008` | El harness mide cada llamada al modelo, vueltas, herramientas, CPU y memoria, y compara corridas. |
| `729c526` | El agente arranca con el inventario de documentos y las pistas; pide varias herramientas por vuelta; la búsqueda acepta un documento. |
| `9176197` | El razonamiento se cuenta como salida y cada vuelta dice cuánto vino de la caché de Vertex. |
| `dab1b14` | Tope de salida y de tiempo a la transcripción por visión. |
| `2d86dad` | La compuerta descarta la dirección ilegible. |
| `f241ad2` | Dirección con «Dir:» y «Dirección para notificaciones:»; la de Positiva no es del afectado. |
| `efed0cb`, `652cc41`, `6bd263e` | OCR de las hojas a la vez con cupo por núcleos y por memoria, tope de píxeles, hojas en blanco sin OCR, sin PNG, `malloc_trim`; las hojas se renderizan en el hilo del documento. |
| `741ee20` | Caché de la lectura de cada adjunto por su huella; la plataforma le da su carpeta. |
| `34e79c4` | Sin pistas para la dirección y el teléfono. |
| `b735d25` | `RAZONAMIENTO_TOKENS` (apagado por defecto). |
| `ede353a`, `8850d43`, `08815cf` | Compuerta: documento recortado, documento lejos del afectado, varios afectados por los nombres de los adjuntos. |
| `f4a96dc` | Pantalla: rendimiento en el detalle de cada ejecución y en el tablero. |

## Lo que se probó y no se dejó

- **Tope de razonamiento de 512 tokens.** En 60 correos: costo −31 % y la mitad del tiempo del agente. En el corpus etiquetado: 37 fallas contra 27 sin tope, más errores en el tipo de solicitud y un caso con el documento de otra persona. La perilla queda, apagada.
- **Pistas de dirección y teléfono.** Subían la cobertura de dirección, pero en el corpus etiquetado el agente reportó cuatro direcciones donde el correo no trae la del afectado.
- **Cortar el OCR cuando los campos ya están resueltos.** No: la compuerta y el revisor comprueban contra todo el texto, y cortar les quitaría con qué comprobar.
- **Varios hilos sobre el mismo PDF.** La primera versión renderizaba en los hilos de lectura bajo un candado; en la plataforma terminó en SIGSEGV. Ahora solo la imagen sale del hilo del documento.

## Pendiente

- Caché entre corridas del job: en el job vive en memoria (gana dentro de la corrida). Para que un aplazado no se vuelva a leer en la corrida siguiente hay que guardarla en el bucket de `RUTA_LOGS`.
- Convertir los adjuntos de un mismo correo a la vez (hoy solo van a la vez las hojas de cada PDF).
- Descargar los adjuntos grandes por partes: Graph los entrega en base64 dentro del JSON; el tope de 50 MB por correo acota la memoria mientras tanto.
- El agente intentó a veces una herramienta que no existe (`google:python_interpreter`); se le responde con error, pero gasta una vuelta.
- Las listas de varias personas sin documento en el cuerpo ni en el nombre del adjunto pueden radicarse a nombre de una (igual que en producción).
