# Cómo estaba enlazado Vertex AI, y cómo volver a enlazarlo

Este documento existe para poder **quitar Vertex** (y dejar de gastar por token)
y **volver a ponerlo** después sin tener que redescubrir nada.

Todo lo que sigue está verificado contra el código, no de memoria.

---

## 1. Dónde está la costura

El pipeline nuevo no llama a Gemini directamente en ningún sitio. Habla con un
protocolo:

```python
# pipeline/modelo.py
class Modelo(Protocol):
    nombre: str
    def generar(self, sistema, mensajes, herramientas) -> Turno: ...
```

Hay **dos** implementaciones de ese contrato:

| Clase | Dónde | Qué hace |
|---|---|---|
| `ModeloVertex` | `pipeline/modelo.py` | Llama a Vertex AI. **Cuesta dinero.** |
| `ModeloFalso` | `pipeline/modelo.py` | Devuelve turnos programados. Para pruebas. |

Quien elige es `correr.py`. **Cambiar de backend es cambiar qué clase se
instancia ahí, y nada más**: ni el agente, ni la compuerta, ni el radicador
saben qué modelo hay detrás.

`ModeloVertex` es el único que resuelve campos de verdad, y por eso `correr.py`
exige `--vertex`: sin la bandera la corrida se detiene en vez de gastar. No hay
backend de reserva, así que ninguna corrida empieza a cobrar por descuido.

---

## 2. Cómo estaba configurado `ModeloVertex`

### El constructor

```python
ModeloVertex(
    proyecto=settings.GCP_PROJECT,      # "positiva-sgda-prd-416417"
    ubicacion=settings.GCP_LOCATION,    # "us-east1"
    nombre="gemini-2.5-flash",          # valor por defecto de la clase
    credenciales=None,                  # None = ADC
    endpoint_global=True,
)
```

### Autenticación: ADC, no API key

El cliente se crea con `genai.Client(vertexai=True, project=..., location=...)`.
Con `vertexai=True` el SDK **no lee `GOOGLE_API_KEY`**: usa Application Default
Credentials.

- **En el Cloud Run Job**: la service account adjunta al job.
- **En local**: `gcloud auth application-default login`.
- El parámetro `credenciales` permite inyectar un token de la sesión de `gcloud`
  cuando no hay ADC disponible.

`GOOGLE_API_KEY` está en el Job como literal en claro y **no se usa**. Quedó
huérfana cuando se migró de `genai.Client()` sin argumentos —que sí la leía— a
la ruta de Vertex. Se puede borrar del Job.

### El parámetro `endpoint_global`

```python
if self.endpoint_global:
    kwargs["http_options"] = types.HttpOptions(
        base_url="https://aiplatform.googleapis.com"
    )
```

**No quitar esto sin probar.** Los endpoints regionales resuelven al bloque
`172.217.x.4`, inalcanzable desde algunas redes; el global resuelve a otro
rango. Si al reactivar Vertex la conexión falla por timeout y no por permisos,
esto es lo primero que hay que mirar.

### La llamada

```python
config = types.GenerateContentConfig(
    temperature=0.1,
    system_instruction=sistema,     # el prompt NO va en contents
)
if herramientas:
    config.tools = [types.Tool(function_declarations=list(herramientas))]

respuesta = cliente.models.generate_content(
    model=self.nombre, contents=contenido, config=config)
```

Dos cosas que importan y que hay que conservar:

1. **`system_instruction`, no `contents`.** Si el prompt del sistema viaja
   dentro de `contents`, llega por el mismo canal que el texto de los documentos
   —que es dato de terceros y puede traer órdenes—. El propio prompt le dice al
   modelo que los documentos son DATO y nunca instrucción, y eso solo se
   sostiene si están en canales distintos. Hay pruebas que lo fijan en
   `tests/test_modelo_vertex.py`.
2. **`temperature=0.1`.** Extracción, no redacción.

### La traducción de la respuesta

`_turno_de_respuesta` recorre `candidates[].content.parts[]` y separa:

- `parte.function_call` → una `Llamada(herramienta, argumentos)`
- `parte.text` → texto del turno

Y lee `usage_metadata.prompt_token_count` / `candidates_token_count` para la
contabilidad. Si `usage_metadata` no viene, quedan en cero y el informe lo
muestra como llamada sin métricas, que es mejor que estimar.

---

## 3. Variables de entorno que usa

| Variable | Valor | Obligatoria |
|---|---|---|
| `GCP_PROJECT` | `positiva-sgda-prd-416417` | No: hardcodeada en `config/settings.py` |
| `GCP_LOCATION` | `us-east1` | No: hardcodeada |
| `GOOGLE_API_KEY` | — | **No se usa.** Ver arriba |

Los secretos obligatorios de `config/settings.py` son de Outlook y SGDEA, y los
del registro de radicados son de Cloud SQL. **Ninguno es de Vertex**: Vertex va por ADC.

### Permisos de la service account

Necesita `roles/aiplatform.user` en `positiva-sgda-prd-416417`. Si al reactivar
sale un 403 de `aiplatform.endpoints.predict`, es esto.

---

## 4. Qué modelos y a qué precio

Tarifas en `harness/costo.py`, USD por millón de tokens, vigentes al
2026-08-24:

| Modelo | Entrada | Salida |
|---|---|---|
| `gemini-2.5-flash` | 0,30 | 2,50 |
| `gemini-2.5-pro` | 1,25 | 10,00 |
| `gemini-2.0-flash` | 0,15 | 0,60 |

- El **pipeline nuevo** usa un solo modelo: `gemini-2.5-flash`.
- El **pipeline viejo** (`services/gemini_agents.py`) usa los dos: manda
  `TIPO_SOLICITUD` y `ELEGIR_DOCUMENTO_AFECTADO` a `gemini-2.5-pro`, que cuesta
  4,17× más por token. Son el 27% de sus tokens.

Costo de entrada por cada mil correos, medido con `python -m harness.consumo`:

| | USD / 1000 correos |
|---|---|
| Viejo (7 agentes) | **24,92** |
| Nuevo, medido contra Vertex | **3,90** |

`gemini-2.0-flash` está en la tabla de tarifas pero **fue retirado el
2026-06-01**: si algún día aparece en un log, es una configuración vieja.

---

## 5. Cómo volver a enlazar Vertex

1. Comprobar ADC:
   ```bash
   gcloud auth application-default login
   gcloud config set project positiva-sgda-prd-416417
   ```
2. Confirmar que la SA tiene `roles/aiplatform.user`.
3. Correr **una** vez con volumen mínimo, para no gastar de más:
   ```bash
   python correr.py --vertex --n 1 --seco
   ```
4. Verificar el gasto en la consola de GCP → Billing → Reports, filtrando por
   proyecto y servicio *Vertex AI*.
5. **Poner un presupuesto con alerta** en ese proyecto antes de subir el volumen.

### Si algo falla

| Síntoma | Causa probable |
|---|---|
| Timeout, no 403 | `endpoint_global=False`, o red que no alcanza el bloque regional |
| 403 `aiplatform.endpoints.predict` | Falta `roles/aiplatform.user` en la SA |
| 404 del modelo | El nombre del modelo no existe en esa región |
| Pide `GOOGLE_API_KEY` | Se perdió `vertexai=True`: cayó a la ruta de la Developer API |

---

## 6. Qué sigue funcionando sin Vertex

Sin la bandera `--vertex` no hay corrida, pero el resto del pipeline no depende
del modelo y se puede ejercitar aparte, sin gastar:

- La capa determinista completa, la compuerta y el armado del payload
- La conversión de adjuntos a Markdown y el OCR (Tesseract, local)
- Las seis capas de deduplicación
- Las medidas del harness (`cobertura`, `exactitud`, `regresion`), que corren
  contra el corpus sin llamar a ningún modelo

```bash
python -m harness.probar_caso <id>   # un caso, solo algoritmo y compuerta
python -m harness.cobertura          # cuánto resuelve el algoritmo por sí solo
```

Lo único que Vertex aporta es **resolver los campos que el extractor escala**.
