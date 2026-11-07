# Rendimiento y recursos

Medido el 2026-11-07 en el contenedor `positiva-ia-positiva-ia-1` (8 núcleos, 16 GB sin límite de cgroup, `TRABAJADORES_MAX=8`), con correos reales de la bandeja de PQRD y Vertex de verdad. Los scripts de medición quedan fuera del repositorio; cada cifra dice de dónde sale.

## Dónde se va el tiempo

| Qué | Medido | Cuánto pesa |
|---|---|---|
| PQRD, correo c0295 (4 PDF, 3 escaneados), sin caché | 29,9 s y 25,9 s de punta a punta | **conversión y OCR 22,1 s y 20,1 s (≈ 75 %)**; agente IA 4,1–4,5 s; revisor 0,9 s; registro de personas 1,9 s |
| El mismo correo con la caché caliente | 6,3 s | conversión 0 s; agente 4,8 s; revisor 0,8 s |
| Declarativo `entrada_archivos` con esos 4 PDF (OCR + visión) | 27,2 s fría · **0,02 s** caliente | OCR por archivo: 0,2 s (texto propio), 5,9 s, 18,6 s y 2,4 s; 3 llamadas de visión en frío, 0 en caliente |
| `para_cada` de 8 vueltas con `ia_clasificar` (Vertex) | una tras otra: 6,8 s y 19,6 s · a la vez: **2,1 s y 2,4 s** | todo es espera de red del modelo (0,8–2,5 s por llamada); el motor no suma nada medible |
| Motor declarativo sin IA (el caso de Vera: lista → para_cada → filtro → tabla → salida) | 0,02 s | despreciable |
| Arranque de un trabajador (de lanzar el proceso a «listo») | declarativo 0,23–0,27 s · PQRD 0,40–0,44 s · tutelas 0,61–0,66 s | por eso los declarativos arrancan sus trabajadores solo cuando hay trabajo |

Conclusión: el tiempo de una corrida es OCR (CPU, en tesseract, que es C++) o espera del modelo (red). El código propio en Node y en Python no aparece en ninguna medición.

### El caso de Vera que tardó 233 s

Perfil del caso 2 de `scripts/evaluar_vera.py` («filtro sin tildes en lista»), envolviendo desde fuera cada llamada al modelo y cada herramienta del asistente:

- La corrida de 233 s (249 s con la verificación): **226,7 s antes del primer evento**, es decir, la primera llamada a `gemini-3-flash-preview` tardó casi 4 minutos. En las 7 repeticiones siguientes esa misma llamada tardó 7–14 s con 600–1.000 tokens de razonamiento: fue latencia del lado de Vertex (modelo en vista previa; el cliente del chat no le pone tope de tiempo), no del motor ni del servidor.
- Lo demás, medido en 4 corridas: 27–56 s en total, de los que **el modelo es el 72–95 %** (5 a 14 vueltas de 1,5–10 s, con unos 22.000–26.000 tokens de entrada por vuelta). Las herramientas suman 3,5–15,7 s.
- Lo del motor y el servidor: la corrida del flujo tarda **0,02 s**, pero la herramienta `correr_flujo` preguntaba cada 3 s (3,01 s por corrida) y la evaluación cada 2 s. Y cada error de validación que el modelo no sabía arreglar costaba una vuelta más (4–10 s): `datos` de `tabla_guardar` como texto, cálculos dentro de una plantilla (`{{len(entrada.tareas)}}`) y comprensiones en `expresion`.

Después de los arreglos, 4 corridas de la evaluación: **28, 30, 35 y 44 s** (4 de 4 logradas); en 3 perfiles, 33–55 s con el modelo en el 82–89 % del tiempo. El modelo todavía a veces manda `datos` como texto en el primer intento (una vuelta de 4–10 s).

Lo que se arregló:

1. `GET /api/corridas/:id?esperar=N` (hasta 120 s): responde apenas la corrida termina. La evaluación ya lo usa (de 2,0 s de espera fija a lo que dure la corrida).
2. Los errores de validación dicen cómo arreglarlo: el ejemplo exacto de `datos`, «las plantillas no calculan: pon el cálculo en un nodo expresion…» y cómo contar con `len`/`sum` sobre la salida de un `para_cada` o de `tabla_consultar`.

Pendiente fuera del alcance (archivos de Jonathan): en `puente/asistente.py`, `correr_flujo` debería usar `?esperar=60` en vez de dormir 3 s (ahorra ≈ 3 s por corrida) y el cliente de Vertex del chat debería llevar un tope de tiempo con reintento (`HttpOptions(timeout=…)`), que es lo que habría cortado los 226 s.

## Dónde se va la memoria

| Proceso | RSS medido |
|---|---|
| Servidor Node | 105 MB |
| Trabajador declarativo, recién arrancado | 46–47 MB |
| Trabajador de PQRD, recién arrancado | 49–50 MB |
| Trabajador de PQRD tras 9 corridas con OCR | 390 MB (pico 446 MB) |
| Trabajador de tutelas | 81–82 MB |
| Asistente (un proceso por mensaje del chat) | 91 MB |

Lo que crece es el trabajador de PQRD (PyMuPDF, imágenes de las hojas y el modelo de Vertex cargado). Por eso cada trabajador tiene un límite (`MEMORIA_TRABAJADOR_MB`, o el del contenedor repartido entre `TRABAJADORES_MAX`; 1.999 MB aquí): con más del 80 % el tablero lo avisa y, si queda libre por encima del límite, se recicla (sale y arranca otro; ninguna corrida se corta).

## Qué se cambió y qué dio

- **Límites por flujo y reparto justo** (`servidor/src/recursos.ts`, `planificador.ts`, `motor.ts`): `trabajadores`, `corridas_simultaneas`, `cola_max` y `tiempo_max_s` por flujo, en `<estado>/recursos.json`, con `GET/PUT /api/flujos/:id/recursos` y en «Entorno › Ejecución». El tope global `TRABAJADORES_MAX` se reparte con justicia max-min entre los flujos que tienen trabajo; si no hay lugar, se retira un trabajador libre del que más tiene por encima de su cuota. Medido con dos flujos reales (corridas de 2 s): A encola 30 (la 31 recibe 429 por su `cola_max`) y B llega 3 s después con 4: **B espera 1,8 s** (lo que tarda en liberarse un trabajador de A), toma 4 de los 8 y termina a los 7,0 s; A termina sus 30 a los 10,7 s; nunca hubo más de 8 procesos. Antes, cada flujo tenía 2 trabajadores fijos para siempre y sin tope global: A habría tardado 30 s (30 × 2 s / 2) y cada flujo abierto dejaba 2 procesos vivos.
- **Tiempo máximo y trabajadores trabados**: una corrida (o un lote del job) que pasa `tiempo_max_s` detiene a su trabajador (SIGTERM y, a los 5 s, SIGKILL), queda en error con el motivo y otro trabajador sigue con la cola. Un trabajador también se reinicia a mano desde el tablero (`POST /api/flujos/:id/trabajadores/:n/reiniciar`). El motor declarativo se detiene solo al llegar a su `tiempo_max_s`, 10 s antes del corte.
- **Llamadas a la IA a la vez** (`puente/flujo_declarativo.py`): las vueltas de un `para_cada` y las ramas de un `paralelo` corren a la vez (de a 4, `concurrencia` 1–8 en el nodo) cuando se puede probar que no cambia el resultado: el cuerpo solo calcula o pregunta a la IA, cada nodo de IA declara sus `entradas` y nada lee lo que dejó otra vuelta u otra rama. Si el cuerpo escribe, lee adjuntos o depende de la vuelta anterior, sigue una tras otra. Los eventos se guardan por vuelta y salen en su orden; el semáforo y el balde de cada proveedor siguen mandando. **8 vueltas: de 6,8–19,6 s a 2,1–2,4 s**, con los mismos resultados. Las conexiones ya se reutilizaban (un `httpx.Client` y un cliente de Vertex por proveedor y credencial en cada proceso, y cada proceso es de un solo flujo).
- **Caché de conversión, OCR y visión** (`puente/cache_conversion.py`): por sha256 del archivo + versión del motor de conversión (código, instrucción de visión, tesseract y sus modelos) + opciones (OCR, proveedor y modelo de visión, topes, huella de las claves de PDF). LRU con tope (`CACHE_CONVERSION_MB`, 2.048 por defecto) en `<estado>/cache/conversion`. No guarda nada a medias (presupuesto agotado, hoja cortada por tiempo, visión fallida, error). Un acierto descuenta del presupuesto del correo los segundos de OCR y las imágenes de visión que costó la primera vez, así el orquestador de PQRD decide igual. La usan el motor declarativo (`leer_adjuntos`, `entrada_archivos`, lo que leen los conectores) y, con la compuerta apagada, PQRD y tutelas.
  - A/B en PQRD con el mismo correo (fría, caliente, fría otra vez), en c0295, c0107 y c0116: **desenlace, motivo, los 11–12 hallazgos, la solicitud y el texto de cada adjunto idénticos** en las tres. c0295: 29,9 s → 6,3 s; c0107: 12,3 s → 7,8 s; c0116 trae los mismos adjuntos que c0107 (un reenvío) y ya en la primera pasada los tomó de la caché (5,2 s).
- **Medición por trabajador**: CPU (de `/proc/<pid>/stat`, contando a tesseract como hijo) y memoria (`VmRSS`) cada 5 s, en `GET /api/salud?flujo=` (con la cuota del flujo, sus límites, incidentes y el resumen de la plataforma) y en cada tarjeta del tablero, con avisos suaves.

## Qué va en cada lenguaje

- **Node (TypeScript)**: el servidor HTTP, el WebSocket de eventos, el planificador de trabajadores, la vigilancia de CPU y memoria y el programador. Todo es E/S y estado pequeño; en las mediciones su parte es de milisegundos (la corrida más corta, 0,02 s de punta a punta, incluye el viaje por el servidor).
- **Python**: el motor declarativo, la capa de proveedores de IA, la conversión y el OCR (el código de PQRD y tutelas es Python y se reutiliza tal cual) y el asistente. Su costo propio también es despreciable: el tiempo es tesseract (C++) o la red del modelo.
- **Código nativo**: **ninguna medición lo justifica.** El cuello real es el OCR, que ya es nativo (tesseract); lo que se gana ahí es no repetirlo (la caché: de 22 s a 0) y no esperar en fila a la IA (vueltas a la vez: ×3 a ×8). Reescribir el motor o el servidor en Rust o Go no movería ninguna de estas cifras. Se revisa de nuevo si una medición muestra CPU propia (no de tesseract) por encima del 20 % de una corrida.
