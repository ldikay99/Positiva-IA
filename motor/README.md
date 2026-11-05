# Radicación automática de PQRD — Positiva ARL

Lee el buzón `servicioalcliente@positiva.gov.co`, extrae los datos que exige SGDEA y radica
peticiones, quejas, reclamos y denuncias. Cuando no reúne lo obligatorio con suficiente certeza deja
el correo para revisión de una persona, con el motivo por el que no pudo.

Corre como Cloud Run Job cada 30 minutos. Punto de entrada: `correr.py`.

| | |
|---|---|
| Exactitud de la capa determinista | 96% (420 de 434 campos comparables) |
| Cobertura sin modelo | 58% de los campos |
| Pruebas automatizadas | 2.315 casos |

Las dos primeras se miden contra el corpus de correos reales. El consumo del
modelo y el tiempo por correo dependen del tamaño de los adjuntos y varían
entre corridas: los reporta el informe de cada ejecución, que es donde
corresponden a lo que de verdad pasó.

## Arquitectura

Tres capas secuenciales. Cada una hace una sola cosa y no invade a las otras.

**1. Extracción determinista.** Resuelve con reglas el nombre del afectado y del remitente, número y
tipo de documento de ambos, teléfono, dirección, sexo, tipo de petición y tipo de solicitud. Cada
dato queda con la evidencia textual de donde salió, un nivel de confianza y el método que lo
produjo. Lo que no resuelve queda marcado con el motivo concreto.

**2. Agente único.** Recibe solo los campos pendientes con su motivo, nunca el correo completo. Lee
mediante herramientas: lista documentos, busca dentro de ellos, lee el fragmento que necesita,
consulta catálogos y valida documentos de identidad. Ninguna herramienta escribe en SGDEA ni en el
correo.

**3. Compuerta de validación.** Único punto por el que pasa todo hallazgo antes de radicar. Verifica
coherencia entre campos y exige un piso de confianza por campo. Determinista: no llama al modelo.

El detalle de cada capa está en [`docs/arquitectura-mcp.pdf`](docs/arquitectura-mcp.pdf), que se
regenera con `python docs/arquitectura.py`.

## Desenlaces

| Desenlace | Condición | Resultado |
|---|---|---|
| `radicado` | Campos obligatorios reunidos y compuerta aprobada | Número de SGDEA, etiqueta `ENT…`, movido a radicación exitosa |
| `duplicado` | Detectado por alguna de las seis capas de deduplicación | Se devuelve el radicado existente |
| `reenviado` | No es una PQRD | Derivado a Notificaciones Judiciales o Medicina Laboral |
| `aplazado` | Falló la infraestructura, no el correo | Vuelve a la bandeja con `REINTENTO:N` |
| `manual` | Falta un obligatorio o la compuerta rechazó | Etiqueta `REVISION-MANUAL:<motivo>` |
| `en_proceso` | Otra ejecución lo reclamó primero | El correo se deja intacto |

## Validación de datos

### Documentos de identidad

| Tipo | Longitud aceptada |
|---|---|
| Cédula de ciudadanía | 5–10 dígitos (de las más antiguas al NUIP) |
| Cédula de extranjería | 6–7 |
| Permiso por protección temporal | 8–9 |
| Permiso especial de permanencia | 15 |
| Carné diplomático | 6–10 |
| Tarjeta de identidad / NUIP | 10–11 |
| NIT | 9–10, con dígito de verificación |
| Pasaporte | alfanumérico, reconocido por su forma |

Los separadores de miles se retiran: `41.234.567` llega al registro como `41234567`. Un número
pegado a letras no se parte para quedarse con los dígitos — `ENT3413618689913` es un radicado y
`S001223884` un recibo —, salvo en los tipos alfanuméricos, que se reconocen enteros.

### Contacto y domicilio

| Regla | Comportamiento |
|---|---|
| Teléfono con dígito repetido, secuencia o de ejemplo | Se deja vacío |
| Teléfono fuera del plan de numeración | Se deja vacío |
| Teléfono que es el propio documento | Se deja vacío |
| Dirección con marcador de ausencia (`No reporta`, `N/R`, `undefined`, `N/A`) | Se deja vacía |
| Dirección que contiene un correo o un teléfono | Se recorta antes del contacto |
| Dirección rural (`CORREGIMIENTO`, `KILOMETRO`, `VEREDA`) | Válida, no se descarta |

### Coherencia entre campos

Longitud del documento contra su tipo declarado; dígito de verificación del NIT; nombre que no sea
un correo, una URL, un cargo ni un área; razón social junto a documento de persona natural; remitente
declarado como afectado con nombres o documentos que no coinciden; oficina contra la tabla de tipos
de solicitud.

## Garantías

- **Deduplicación.** Seis comprobaciones antes de invocar el modelo. La reclamación con control de
  concurrencia (`If-Match`) impide que dos ejecuciones simultáneas trabajen el mismo correo.
- **Recuperación.** Un fallo de infraestructura aplaza el correo en lugar de cerrarlo hacia una
  persona: vuelve a la bandeja con su contador, hasta doce ciclos.
- **Aislamiento temporal.** La corrida tiene presupuesto de tiempo; lo que no alcanza queda sin
  marca y lo toma la ejecución siguiente.
- **Sin correo al ciudadano.** La única salida de correo es el reenvío a otra área cuando el caso no
  es una PQRD.
- **Sin datos inventados.** Un valor sin evidencia o por debajo del piso de confianza no llega a
  SGDEA; ante ambigüedad real, el extractor se abstiene y explica por qué.
- **Sin fallos silenciosos.** Todo fallo queda clasificado con severidad, política de reintento y
  motivo.

## Observabilidad

Cada ejecución produce un informe con la atribución por campo — algoritmo, IA, registro de personas
o revisión manual —, el tiempo por etapa, el consumo del modelo, y el detalle por correo
incluyendo la frase con la que el agente explicó qué dato faltó.

Sale en formato estructurado a Cloud Logging (`jsonPayload.accion`, `jsonPayload.motivo_manual`,
`jsonPayload.por_que`) y se guarda en el destino que indique `RUTA_LOGS`, con una serie acumulada
por corrida. Una PQRD radicada deja además un registro de trazabilidad en SGDEA.

## Estructura

```
correr.py                 punto de entrada del Job
pipeline/
  orquestador.py          el flujo completo de un correo
  extraccion.py           extractores deterministas por campo
  agente.py               el bucle del agente y su prompt
  compuerta.py            validación de coherencia previa a radicar
  duplicados.py           las seis capas de deduplicación
  reclamo.py, marcas.py   reclamo atómico y marcas sobre el correo
  enrutamiento.py         a qué área va el correo
  errores.py              taxonomía de fallos y política de reintento
  reintentos.py           qué se aplaza a la corrida siguiente
  presupuesto.py          el tiempo que le queda a la corrida
  telemetria.py           informe por ejecución, diario y serie
  almacen.py              informes en disco o en bucket
  personas.py             consulta al registro de SGDEA
  notificacion.py         redacción del aviso de resultado
  radicador.py            construcción del payload y envío a SGDEA
  materializar.py         conversión de adjuntos a Markdown y OCR
  saneamiento.py          defensa contra inyección de instrucciones
servidor_mcp/
  herramientas.py         las herramientas del agente
harness/                  medición contra el corpus
tests/                    2.315 casos de prueba
docs/                     documentación del proyecto
```

## Puesta en marcha

```bash
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # completar con los secretos
```

Requiere `tesseract-ocr` con el paquete de español para el OCR de adjuntos escaneados; la imagen del
contenedor ya lo instala.

```bash
python correr.py --vertex --n 5 --seco   # no escribe nada, exige MODO_TEST=true
python correr.py --vertex --n 10         # radica de verdad
pytest -q                                # la suite completa
```

`--vertex` es obligatorio: es el único backend que resuelve campos y cada
llamada se factura, así que sin la bandera la corrida se detiene en vez de
gastar. Para trabajar sin costo está el harness, que mide contra el corpus sin
llamar al modelo.

| Variable | Para qué | Por defecto |
|---|---|---|
| `MODO_TEST` | Con `true` no ejecuta ninguna escritura | `false` |
| `CONCURRENCIA` | Correos atendidos a la vez | `5` |
| `PRESUPUESTO_S` | Tope de tiempo de la corrida | dos tercios de la cadencia |
| `RUTA_LOGS` | Destino de los informes; admite `gs://` | `logs/` |
| `RUTA_WORKSPACE` | Espacio de trabajo por correo | `workspace/` |
| `CORREO_DE_AVISOS` | Buzón interno al que remitir el resultado | vacío: no envía |
| `DEDUP_EN_PRUEBAS` | Ejecuta la deduplicación durante `MODO_TEST` | `false` |

Los secretos van en `.env`, que no se versiona, o en Secret Manager. Faltando alguno, el arranque
falla nombrando cuál.

## Medición

Ningún cambio a las reglas, al prompt o a la compuerta se acepta sin medirlo contra el corpus de
correos reales, que no se versiona.

```bash
python -m harness.cobertura    # cuánto resuelve la capa determinista
python -m harness.exactitud    # cuánto de lo que resuelve es correcto
python -m harness.regresion    # contra una referencia guardada
python -m harness.consumo      # llamadas y tokens por correo
```

Cobertura y exactitud son medidas distintas: un campo puede resolverse siempre y estar mal la mitad
de las veces.

## Limitaciones conocidas

- **Razón social del afectado.** El payload de radicación no tiene campo para ella. Cuando el
  titular del caso es una empresa, el pipeline se abstiene y el correo va a revisión manual en lugar
  de escribir una razón social en el campo destinado al nombre de un ciudadano. Requiere que el
  backend agregue el campo.
- **Dirección.** La capa determinista resuelve el 9% de los casos; el resto escala al agente. Es el
  campo con menor cobertura.
- **Fotos de documentos de identidad.** Una cédula fotografiada con el teléfono y pegada en un PDF
  llega con 50–150 dpi efectivos y compresión JPEG encima. El OCR no la lee, y el correo va a
  revisión manual con `ADJUNTO_ILEGIBLE`. Se midieron ocho configuraciones de OCR sobre siete de
  estos documentos: las que extraen algo devuelven ruido del que no sale ningún número de documento
  válido, así que se prefiere no leer a leer mal. Resolverlo pide entregarle la imagen al modelo
  multimodal, lo que exige extender el protocolo `Modelo`, que hoy solo acepta texto.
- **Serie histórica en bucket.** Requiere que la cuenta de servicio del Job tenga
  `roles/storage.objectAdmin` sobre el bucket de `RUTA_LOGS`. Sin ese permiso la ejecución continúa
  y el informe llega a Cloud Logging; solo se pierde la acumulación entre corridas.

---

Diseño e implementación: **Jonathan C**
