# Datos sensibles: clasificación, cifrado, retención y borrado

Clasificación de cada dato que maneja Vera (ver [DATOS_E_INTEGRACIONES.md](DATOS_E_INTEGRACIONES.md)), dónde vive hoy y
cómo se protege. Sobre esta base se definen las reglas de cifrado, retención y borrado por organización, conforme a la
Ley 1581 de 2012 de protección de datos personales.

## Niveles

| Nivel | Qué es | Regla general |
|---|---|---|
| **Secreto** | Da acceso a un sistema en nombre de la organización o de una persona | Nunca en claro en disco, logs, eventos, resultados, repositorio ni respuestas; no se vuelve a mostrar después de guardarse |
| **Sensible** | Datos personales sensibles en el sentido de la Ley 1581: salud, documentos judiciales y voz | Cifrado por organización, acceso mínimo, retención corta y borrado verificable |
| **Personal confidencial** | Datos personales y contenido de trabajo de la organización | Cifrado por organización; solo la organización lo ve |
| **Interno** | Configuración y metadatos sin contenido personal | Protegido por permisos de la base y de la organización |
| **Público** | Código, documentación y plantillas del producto | Repositorio, sin datos personales |

## Clasificación

| Dato | Nivel | Dónde vive hoy | Protección actual |
|---|---|---|---|
| Credenciales de conectores y de proveedores de IA, tokens OAuth | Secreto | `credenciales.json` | AES-256-GCM; la API nunca las devuelve; enmascaradas en eventos y logs |
| Variables secretas de cada flujo | Secreto | `entornos/<flujo>.json` | AES-256-GCM, igual que las credenciales |
| Llave de cifrado de credenciales | Secreto | `CLAVE_CREDENCIALES` o `.clave-credenciales` | Archivo `0600` dentro del volumen de datos |
| Llave que firma las descargas y tokens de webhooks | Secreto | `clave_descargas`, definición del flujo | En el volumen de datos; comparación en tiempo constante |
| Identidad de la nube (ADC o llave de cuenta de servicio) | Secreto | Archivo montado desde `RUTA_CREDENCIALES` | Montado en solo lectura; fuera del repositorio |
| Correos y adjuntos con historias clínicas, certificados de discapacidad y dictámenes | Sensible | `vivo/`, `/datos/archivos/<flujo>/`, `recibidos/` | En claro en el volumen; no se versionan ni salen de la máquina |
| Texto y páginas leídos de esos documentos (OCR y visión) | Sensible | `cache/conversion/`, `trabajo/visor/` | En claro; caché limitada a 2 GB y visor a las 80 ejecuciones más recientes |
| Ejecuciones con sus eventos y campos extraídos | Sensible | `corridas/<día>.jsonl` | En claro; sin retención definida |
| Documentos judiciales (tutelas) | Sensible | Igual que los correos | Igual que los correos |
| Transcripciones de voz | Sensible | `chats/<id>.json` | En claro; el audio no se guarda |
| Conversaciones de Vera por chat y sus adjuntos | Personal confidencial | `chats/` | En claro; sin retención definida |
| Datos de identificación (nombre, documento, teléfono, dirección, correo) | Personal confidencial | Correos, ejecuciones y salidas | En claro, con los correos |
| Aprobaciones pendientes y ejecuciones aplazadas con su entrada | Personal confidencial | `aprobaciones/`, `aplazadas/` | En claro; sin retención definida |
| Tablas que guardan los flujos | Personal confidencial | `tablas/*.sqlite` | Sin cifrado; se borran con el flujo |
| Directorio de juzgados y de funcionarios | Personal confidencial | Repositorio (`motores/tutelas/juzgados_actualizada.csv`, `motor/business_rules/funcionario_directory.py`) | Versionado en el repositorio privado; debe salir del repositorio |
| Definiciones de flujos, versiones e historial de cambios | Interno | `flujos/`, `flujos_historial/` | Volumen de datos |
| Configuración, programaciones, límites y proveedor de IA | Interno | `configuracion.json` y afines | Volumen de datos |
| Telemetría y escrituras retenidas en modo prueba | Personal confidencial | `trabajo/logs`, `trabajo/grabador` | Volumen de datos; pueden incluir contenido de correos |
| Código, plantillas incluidas y documentación | Público | Repositorio | Sin secretos en el código |

## Lo que sale de la plataforma

| Destino | Qué recibe | Nivel |
|---|---|---|
| Vertex AI (y otros proveedores si una organización los configura) | Texto, hojas y adjuntos que procesa cada nodo de IA; mensajes y resultados de herramientas de cada conversación | Sensible cuando el flujo procesa salud o tutelas; la anonimización (`puente/anonimizar.py`) existe como paso opcional |
| Gemini Live | Audio del micrófono durante la llamada | Sensible |
| Sistemas conectados (Google, Microsoft, bases, mensajería) | Lo que el flujo escribe con la escritura habilitada | Según el dato |
| Enlaces de descarga | Archivos de salida, firmados y con vencimiento | Según el dato |

## Marco de las reglas

Las secciones Cifrado, Retención y Borrado son **reglas a implementar** en la versión multiorganización en GCP (Cloud SQL
PostgreSQL, Cloud Storage, Secret Manager y Cloud KMS), sobre el modelo de datos de
[MODELO_DE_DATOS.md](MODELO_DE_DATOS.md). Cada una dice primero cómo es hoy. Los plazos son una **propuesta que aprueba el responsable** del tratamiento de cada organización.

| Norma | Qué exige aquí |
|---|---|
| Ley 1581 de 2012, art. 3 | La organización es la **responsable** del tratamiento; Vera lo hace por cuenta de ella, como **encargada** |
| Ley 1581, art. 4 | Principios de finalidad, circulación restringida, seguridad y confidencialidad |
| Ley 1581, arts. 5 y 6 | Los datos de salud son sensibles y su tratamiento es restringido |
| Ley 1581, art. 8 | El titular puede conocer, actualizar y rectificar sus datos, revocar la autorización y pedir su supresión |
| Ley 1581, art. 15 | Un reclamo se atiende en máximo 15 días hábiles, prorrogables 8 |
| Ley 1581, arts. 17 y 18 | Responsables y encargados conservan la información con seguridad para impedir su pérdida, consulta o uso no autorizado |
| Decreto 1377 de 2013, art. 9 (compilado en el Decreto 1074 de 2015) | La supresión no procede si el titular tiene un deber legal o contractual de permanecer en la base |
| Decreto 1377 de 2013, art. 11 (compilado en el Decreto 1074 de 2015) | Los datos se guardan solo el tiempo razonable y necesario para su finalidad; cumplida, se suprimen, salvo obligación legal o contractual |

Vera no es el archivo de la organización. El expediente de una PQRD o una tutela lo conserva el SGDEA con las tablas de
retención documental de la entidad (Ley 594 de 2000). Vera guarda copias de trabajo: por eso sus plazos son cortos.

Un flujo es **sensible** si trata salud, tutelas o voz. PQRD y tutelas lo son; la organización marca los demás al crearlos.

## Cifrado

### Hoy

- Credenciales, tokens OAuth y variables secretas: AES-256-GCM con vector aleatorio de 12 bytes y etiqueta de
  autenticación (`servidor/src/credenciales.ts`, `entornos.ts`).
- Una sola llave para toda la plataforma: `CLAVE_CREDENCIALES` (si no trae 32 bytes en base64, se deriva con SHA-256) o
  `.clave-credenciales`, que se genera con `0600` en el mismo volumen que `credenciales.json`. No hay rotación.
- Ejecuciones, buzón, archivos, caché, visor, conversaciones, aprobaciones y tablas: en claro en los volúmenes.
- La caché de conversión se indexa por la huella del archivo y las opciones de lectura, sin el flujo: el mismo archivo
  en dos flujos reutiliza la misma lectura.
- El espacio de trabajo de cada correo vive en `/tmp`, en memoria (tmpfs del contenedor).
- El servidor habla HTTP sin TLS propio. Docker lo publica solo en `127.0.0.1` y pide un proxy con TLS para exponerlo.
- Los enlaces de descarga se firman con HMAC-SHA256 y una llave única de la plataforma (`clave_descargas`); vencen a las
  72 h por defecto (`DESCARGAS_VENCE_H`).

### En reposo

| Capa | Regla |
|---|---|
| Base | Cloud SQL y Cloud Storage con llave administrada por la plataforma en Cloud KMS (CMEK). Cubre discos, réplicas y respaldos |
| Por organización | Cada organización tiene su llave maestra (KEK) en Cloud KMS. Sus datos se cifran con llaves de datos (DEK, AES-256-GCM) que se guardan envueltas por esa KEK. Una DEK en claro solo existe en la memoria del servicio |
| Qué se cifra con la DEK | Todo dato Secreto, Sensible o Personal confidencial: contenido de ejecuciones, correos y adjuntos, caché, visor, conversaciones y transcripciones, aprobaciones y aplazadas, tablas de flujos, telemetría con contenido, credenciales y variables secretas |
| Qué queda en claro | Solo lo Interno: ids, organización, flujo, estado, fechas y tamaños. Basta para permisos, búsquedas y retención sin descifrar |
| Atado a su lugar | Cada cifrado lleva como dato asociado (AAD) la organización, el tipo y el id del registro. Un dato copiado a otra organización u otro registro no se descifra |
| Por periodo | El contenido Sensible usa una DEK por organización y por mes. Vencida la retención del mes, se destruye su DEK y lo que quede en respaldos ya no se lee |

| Dato | Regla |
|---|---|
| Ejecuciones (`corridas/`) | Filas en Cloud SQL por organización; eventos, campos extraídos y textos cifrados con la DEK; metadatos en claro |
| Correos, adjuntos y archivos | Objetos en Cloud Storage bajo `org/<id>/`, cifrados con la DEK antes de subir. Sin acceso público ni URL firmadas del bucket: se bajan por la API, que comprueba organización y rol |
| Caché de conversión | La clave incluye la organización: un acierto nunca cruza organizaciones. Cifrada con la DEK y con tope por organización |
| Visor | Imágenes y textos de cada hoja cifrados igual que su ejecución; solo los sirve la API |
| Temporales | El espacio de trabajo de cada correo sigue solo en memoria y se borra al terminar el correo |
| Logs de la plataforma (Cloud Logging) | Sin contenido ni datos personales: ids, estados, tiempos y errores sin datos |

### En tránsito

| Tramo | Regla |
|---|---|
| Personas y webhooks → Vera | Solo HTTPS (TLS 1.2 o superior) con HSTS; nada en HTTP plano |
| Vera → Cloud SQL | Conector de Cloud SQL o IP privada, con TLS obligatorio (como hoy el registro de radicados del job de PQRD) |
| Servidor → trabajadores | Hoy las credenciales van por stdin dentro del mismo contenedor. Si se separan, TLS con identidad de servicio |
| Conectores | TLS por defecto, como hoy (IMAP con SSL, SMTP con STARTTLS, PostgreSQL con SSL, FTP con TLS explícito). La opción «ninguno» la habilita solo un administrador y nunca en un flujo sensible |
| Proveedores de IA | Un flujo sensible envía datos solo a Vertex AI con la identidad del servicio. Otro proveedor, solo con anonimización previa obligatoria (`puente/anonimizar.py`) |
| Enlaces de descarga | Solo con `URL_PUBLICA` en https. La firma incluye la organización. Vencen a las 72 h; a las 24 h si el archivo es sensible |

### Gestión y rotación de llaves

| Llave | Dónde | Rotación (propuesta) | Quién la usa |
|---|---|---|---|
| KEK de cada organización | Cloud KMS, un llavero por entorno | Automática cada 90 días. Las versiones viejas solo descifran; las DEK se vuelven a envolver con la nueva | Solo la cuenta de servicio de Vera |
| DEK | Cloud SQL, envuelta por la KEK | Una nueva por mes; nunca se comparte entre organizaciones | El servicio, en memoria |
| CMEK de la base y los buckets | Cloud KMS | Automática cada año | Cloud SQL y Cloud Storage |
| Firma de descargas | Secret Manager | Cada 90 días, con dos versiones válidas mientras vencen los enlaces de la anterior | El servicio |
| Tokens de webhooks | Cifrados con la DEK | Cuando la organización lo pida o si se exponen | El servicio |
| Secretos de conectores | Cifrados con la DEK | OAuth se renueva solo (hoy ya guarda el token de renovación nuevo que entrega Microsoft). Los demás los cambia una persona en el sistema de origen | El trabajador de la corrida |

- `CLAVE_CREDENCIALES` y `.clave-credenciales` desaparecen. Migración: descifrar con la llave actual, cifrar con la DEK
  de la organización y destruir el archivo.
- Quien administra Cloud KMS no administra la base, y al revés.
- Los registros de acceso a datos de Cloud Audit Logs quedan encendidos para Cloud KMS y Secret Manager.
- Llave comprometida: rotación inmediata, nueva envoltura de las DEK y aviso al responsable. Un secreto de conector
  comprometido lo cambia la persona en el sistema de origen.

### Qué nunca se guarda

| Dato | Hoy | Regla |
|---|---|---|
| Audio de la voz | No se guarda: cada turno guarda solo el texto escuchado y dicho. El espacio de un flujo rechaza video y audio | Igual; tampoco en logs ni en Cloud Storage |
| Secretos en eventos, consola, resultados y errores | El motor los enmascara antes de guardar o reenviar, tal cual, escapados en JSON y codificados en URL; también la llave de descargas | Igual. No se aceptan secretos de menos de 4 caracteres, que hoy no se enmascaran |
| Valores de credenciales y variables secretas en respuestas | Ninguna API los devuelve; solo los nombres de los campos | Igual |
| Encabezados de autenticación de un webhook | No entran al flujo | Igual |
| Claves de PDF protegidos | La caché guarda su huella, nunca su valor | Igual |
| Mapa de seudónimos de la anonimización | Solo vive en la corrida | Igual; nunca en la ejecución guardada |
| Contenido en la auditoría | No hay auditoría | Quién, qué y cuándo; nunca contenido ni secretos |

## Retención

- Un trabajo diario por organización borra lo vencido y deja el conteo en la auditoría.
- Los plazos cuentan desde que termina la ejecución o desde el último uso, según la fila.
- La organización puede acortarlos. Alargarlos exige una finalidad escrita (Decreto 1377, art. 11).
- Una obligación legal de conservar se cumple en el SGDEA o en el sistema de destino, no en Vera.

| Dato | Hoy | Flujo sensible (propuesta) | Demás (propuesta) | Por qué |
|---|---|---|---|---|
| Ejecuciones: eventos, campos extraídos y textos (`corridas/`) | Un archivo por día; no se borran salvo al borrar el flujo | 30 días | 90 días | Ventana para revisar, corregir y atender reclamos. El expediente vive en el SGDEA o el sistema de destino |
| Ejecuciones: metadatos sin contenido | Igual | 1 año | 1 año | Estadística y trazabilidad sin datos personales |
| Buzón real (`vivo/`) | No se borra | 7 días | 30 días | Es copia: el original sigue en el buzón de la organización |
| Correos recibidos (`recibidos/`) | Se borran solo con el flujo | 7 días | 30 días | Igual que el buzón |
| Archivos que entran con cada ejecución (`entradas/`) | Hasta que una persona los borre o se borre el flujo | 30 días | 90 días | Van con su ejecución |
| Bandeja de prueba y datos de prueba fijados | Igual | 7 días sin uso | 30 días sin uso | Son pruebas, no trabajo |
| Caché de conversión | Tope de 2 GB (`CACHE_CONVERSION_MB`); borra lo menos usado; sin plazo | 7 días sin uso | 30 días sin uso | Rinde en reenvíos y reintentos cercanos |
| Visor (`trabajo/visor/`) | Las 80 ejecuciones más recientes | 7 días | 30 días | Sirve para revisar cómo se leyó cada hoja; nunca dura más que su ejecución |
| Conversaciones (`chats/`) y sus adjuntos | No se borran; las borra una persona | 30 días sin actividad | 90 días sin actividad | Contexto de trabajo, no registro |
| Transcripciones de voz | Dentro de la conversación | 30 días | 30 días | La voz es sensible en cualquier flujo; el audio nunca se guarda |
| Aprobaciones pendientes | Vencen solas si tienen tiempo límite; si no, esperan sin plazo | Hasta decidir; vencen a los 30 días | Igual | Una decisión vieja ya no sirve |
| Aprobaciones resueltas | Quedan en disco con el estado de la ejecución | El estado de la ejecución se borra al reanudar; la decisión va a la auditoría | Igual | Lo aprobado ya está en la ejecución |
| Ejecuciones aplazadas | Se borran al terminar la nueva corrida o si el flujo ya no existe | 7 días sin reanudarse, con aviso | Igual | No hay finalidad después |
| Tablas de los flujos | Se borran con el flujo; la tabla `compartida`, no | El plazo que declara la organización al crear la tabla; si no declara, 90 días por fila | Si no declara, 1 año por fila | La finalidad de la tabla fija el plazo |
| Telemetría (`trabajo/logs`) | No se borra; los logs del contenedor rotan en 5 archivos de 10 MB | 30 días, sin contenido | 30 días, sin contenido | Diagnóstico |
| Grabador de escrituras retenidas (`trabajo/grabador`) | No se borra | Igual que su ejecución | Igual que su ejecución | Puede llevar correos completos |
| Historial de flujos (`flujos_historial/`, versiones) | Nada borra una versión, salvo borrar el flujo | Mientras exista el flujo | Igual | Es configuración. El pedido que acompaña cada versión pasa por la anonimización antes de guardarse |
| Auditoría | No existe; `historial.jsonl` guarda los cambios de configuración sin plazo | 2 años | 2 años | Prueba de cumplimiento (Ley 1581, art. 17); sin contenido |
| Credenciales y tokens | Hasta que una persona las borre; las de alcance de un flujo, con el flujo. Se anota su último uso | Aviso al administrador a los 90 días sin uso; se borran a los 180 | Igual | Un secreto sin uso es riesgo sin finalidad |
| Enlaces de descarga | Vencen a las 72 h | 24 h | 72 h | Basta para entregar el archivo |
| Directorios de juzgados y funcionarios | Versionados en el repositorio | Tabla de la organización, cifrada; se reemplaza al actualizarse | Igual | Datos de personas fuera del repositorio |

## Borrado

### Quién borra qué

| Quién | Qué borra |
|---|---|
| Cada persona | Sus conversaciones con Vera. Hoy ya: borrar una conversación quita su archivo y sus adjuntos |
| Editor o administrador | Un flujo, con confirmación; archivos de un flujo |
| Administrador | Ejecuciones por flujo o por rango de fechas, tablas, credenciales y personas; atiende las supresiones |
| Vera | Nada por su cuenta: propone el borrado con un botón y una persona confirma (hoy ya) |
| Trabajo de retención | Lo vencido según la tabla de Retención |

Todo borrado pide confirmación explícita y deja registro en la auditoría, sin contenido.

### Al retirar una persona

Hoy no hay personas. Regla, además de lo que fija [ROLES_Y_PERMISOS.md](ROLES_Y_PERMISOS.md) (acceso retirado al
instante; sus flujos siguen siendo de la organización):

- Sus conversaciones con Vera quedan 30 días para que el administrador las transfiera; después se borran.
- Las conexiones OAuth a su cuenta personal se revocan ante el proveedor y se borran. Los flujos que las usan se
  detienen con aviso al administrador.
- Ejecuciones, tablas y archivos siguen en la organización con sus plazos.
- Su cuenta (correo, nombre, segundo factor) se borra a los 30 días. En la auditoría queda su identificador y su nombre
  durante el plazo de la auditoría.

### Al eliminar un flujo

Hoy `GET /api/flujos/:id/borrado` dice qué se borra y `DELETE /api/flujos/:id` borra: ejecuciones guardadas y en
memoria, versiones e historial, entorno, compuerta, programación, límites, credenciales de su alcance, archivos, bandeja
de prueba, su tabla, sus correos recibidos y el estado de su disparador. PQRD y tutelas originales no se borran.

Hoy **no** borra: las conversaciones que empezaron en el flujo, el visor de sus ejecuciones, su parte de la caché, sus
aprobaciones, su telemetría ni su grabador. Las aplazadas se borran cuando les llega la hora.

Regla: el borrado alcanza todo lo que lleva el id del flujo, incluido lo anterior. Las conversaciones se conservan sin
el vínculo al flujo y con su plazo propio. La auditoría registra el borrado con los conteos.

### Al dar de baja una organización

1. Un administrador pide la baja y un segundo administrador la confirma (o el operador de la plataforma, si hay uno solo).
2. La organización tiene 30 días para exportar sus flujos, tablas y lo que deba conservar fuera de Vera. Mientras tanto
   nada corre.
3. Se revocan sus tokens OAuth ante cada proveedor y se borran sus secretos.
4. Se borran sus filas en Cloud SQL, sus objetos bajo `org/<id>/` y lo suyo en Secret Manager.
5. Se destruye su KEK en Cloud KMS. Pasado el periodo de destrucción programada de Cloud KMS, nada suyo en respaldos ni
   réplicas se puede leer (borrado criptográfico).
6. Se entrega una constancia con los conteos por tipo y la versión de llave destruida. El registro de la baja, sin
   contenido, se conserva 2 años.

### Supresión a pedido del titular

- El titular pide a la organización, como responsable, la supresión de sus datos (Ley 1581, art. 8). Vera la ejecuta
  como encargada dentro del plazo de un reclamo (art. 15).
- Para encontrarlos sin descifrar todo, cada ejecución, correo, conversación y fila de tabla lleva un índice ciego: HMAC,
  con una llave de la organización, del documento y del correo del titular normalizados.
- El administrador ve lo encontrado, confirma y se borra. Los metadatos que deban quedar pierden todo dato personal.
- Si el titular tiene un deber legal o contractual de permanecer (Decreto 1377, art. 9), lo decide la organización sobre
  su expediente oficial. Las copias de trabajo en Vera se borran igual.
- Queda en la auditoría la supresión con el índice ciego, nunca el documento: así se vuelve a aplicar si se restaura
  un respaldo.

### Verificación y respaldos

- Cada borrado (persona, flujo, retención, supresión, baja) registra en la auditoría el tipo, el conteo, quién o qué
  regla, y cuándo.
- Después de cada supresión y cada baja, una búsqueda de control por índice ciego o por organización debe dar cero. Si
  no, alerta al operador de la plataforma.
- El trabajo de retención publica cada día lo borrado por tipo. Si falla dos días seguidos, alerta.
- Una prueba automática borra un flujo de prueba y comprueba que no queda nada con su id.
- Respaldos de Cloud SQL: 7 días, con recuperación a un punto en el tiempo. Cloud Storage: eliminación temporal de 7
  días. Lo borrado sale de los respaldos cuando estos vencen (propuesta).
- Antes de abrir un respaldo restaurado se vuelven a aplicar las supresiones y bajas posteriores a su fecha.

## Brechas que cierran las reglas de tratamiento

| Brecha | La cierra |
|---|---|
| El contenido de correos, adjuntos, ejecuciones y conversaciones se guarda en claro | Cifrado › En reposo |
| Una sola llave para toda la plataforma, en el mismo volumen que lo que cifra y sin rotación | Cifrado › Gestión y rotación de llaves |
| La caché de conversión se comparte entre flujos por la huella del archivo | Cifrado › En reposo (caché por organización) |
| El servidor no tiene TLS propio y los conectores aceptan «ninguno» | Cifrado › En tránsito |
| La anonimización antes de enviar datos sensibles a un proveedor de IA es opcional | Cifrado › En tránsito (proveedores de IA) |
| Los secretos de menos de 4 caracteres no se enmascaran | Cifrado › Qué nunca se guarda |
| No hay retención para ejecuciones, buzón, recibidos, conversaciones, aprobaciones ni telemetría; solo hay límites de tamaño en la caché y el visor | Retención |
| Hay datos personales versionados en el repositorio | Retención (directorios en una tabla de la organización) |
| Borrar un flujo no borra sus conversaciones, visor, caché, aprobaciones, telemetría ni grabador | Borrado › Al eliminar un flujo |
| No se pueden encontrar ni suprimir los datos de un titular | Borrado › Supresión a pedido del titular |
| No hay auditoría de borrados ni respaldos con borrado definido | Borrado › Verificación y respaldos |
| No hay organizaciones ni personas: cualquiera con acceso a la red ve todo | El modelo por organización y el inicio de sesión ([ROLES_Y_PERMISOS.md](ROLES_Y_PERMISOS.md)); todas las reglas de este documento dependen de él |
