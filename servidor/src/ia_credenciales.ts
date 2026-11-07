import type { CampoDeCredencial, TipoDeCredencial } from "./credenciales.ts";

/**
 * Los tipos de credencial de los proveedores de IA (uno por proveedor, `ia_<proveedor>`).
 * Vertex no tiene: usa la identidad del servicio. La llave siempre es secreta y
 * nunca sale de la plataforma; solo llega a la corrida que la usa.
 */

const llave = (obligatoria = true, ayuda = "la llave del servicio (nunca se muestra)"): CampoDeCredencial =>
  ({ nombre: "api_key", titulo: "API key", tipo: "texto", secreto: true, obligatorio: obligatoria, ayuda });
const soloLlave = (tipo: string, nombre: string, descripcion: string, ayuda: string): TipoDeCredencial => ({ tipo, nombre, descripcion, ayuda, campos: [llave()] });

export const TIPOS_CREDENCIAL_IA: TipoDeCredencial[] = [
  soloLlave("ia_gemini", "IA · Gemini (Google AI)", "Gemini directo con una API key de Google AI Studio.", "Créala en aistudio.google.com → Get API key."),
  { tipo: "ia_openai", nombre: "IA · OpenAI", descripcion: "Modelos GPT de OpenAI.", ayuda: "Créala en platform.openai.com → API keys.",
    campos: [llave(), { nombre: "organizacion", titulo: "Organización", tipo: "texto", ayuda: "opcional: el id org-… si la cuenta tiene varias" }] },
  { tipo: "ia_azure_openai", nombre: "IA · Azure OpenAI", descripcion: "Modelos de OpenAI desplegados en un recurso de Azure.", ayuda: "En el flujo, el «modelo» es el nombre del despliegue.",
    campos: [{ nombre: "endpoint", titulo: "Endpoint", tipo: "texto", obligatorio: true, ayuda: "https://<recurso>.openai.azure.com" }, llave(),
      { nombre: "api_version", titulo: "Versión de la API", tipo: "texto", defecto: "2024-10-21" },
      { nombre: "despliegue", titulo: "Despliegue por defecto", tipo: "texto", ayuda: "opcional" }] },
  soloLlave("ia_anthropic", "IA · Anthropic (Claude)", "Modelos Claude de Anthropic.", "Créala en console.anthropic.com → API keys."),
  soloLlave("ia_deepseek", "IA · DeepSeek", "Modelos de DeepSeek.", "Créala en platform.deepseek.com → API keys."),
  soloLlave("ia_mistral", "IA · Mistral AI", "Modelos de Mistral AI.", "Créala en console.mistral.ai → API keys."),
  soloLlave("ia_groq", "IA · Groq", "Modelos abiertos servidos por Groq.", "Créala en console.groq.com → API keys."),
  soloLlave("ia_openrouter", "IA · OpenRouter", "Cientos de modelos con una sola llave.", "Créala en openrouter.ai → Keys."),
  soloLlave("ia_cohere", "IA · Cohere", "Modelos Command de Cohere.", "Créala en dashboard.cohere.com → API keys."),
  { tipo: "ia_ollama", nombre: "IA · Ollama (local)", descripcion: "Un servidor Ollama propio en la red interna.", ayuda: "Loopback y metadatos están vetados; sí se aceptan IP privadas.",
    campos: [{ nombre: "base_url", titulo: "URL base", tipo: "texto", obligatorio: true, defecto: "http://ollama:11434/v1" }, llave(false, "opcional")] },
  { tipo: "ia_compatible", nombre: "IA · Compatible con OpenAI", descripcion: "Cualquier servicio con la API de chat de OpenAI (vLLM, LM Studio, LiteLLM…).", ayuda: "La URL base termina en /v1. Loopback y metadatos están vetados.",
    campos: [{ nombre: "base_url", titulo: "URL base", tipo: "texto", obligatorio: true, ayuda: "p. ej. https://llm.mi-empresa.com/v1" }, llave(false, "opcional (Bearer)"),
      { nombre: "encabezados", titulo: "Encabezados", tipo: "texto-largo", secreto: true, ayuda: "opcional: JSON {nombre: valor}; {{api_key}} se reemplaza por la llave" }] },
  { tipo: "ia_personalizado", nombre: "IA · Personalizado (HTTP)", descripcion: "Cualquier otro servicio de IA por HTTP, sin código.",
    ayuda: "Plantilla del cuerpo con {{modelo}}, {{sistema}}, {{mensajes}}, {{mensajes_con_sistema}}, {{tarea}} y {{temperatura}}; la respuesta se lee con rutas como choices.0.message.content.",
    campos: [{ nombre: "url", titulo: "URL", tipo: "texto", obligatorio: true, ayuda: "admite {{modelo}}" },
      { nombre: "metodo", titulo: "Método", tipo: "opcion", opciones: ["POST", "PUT"], defecto: "POST" }, llave(false, "opcional"),
      { nombre: "encabezados", titulo: "Encabezados", tipo: "texto-largo", secreto: true, ayuda: "JSON {nombre: valor}; {{api_key}} se reemplaza por la llave" },
      { nombre: "cuerpo", titulo: "Plantilla del cuerpo (JSON)", tipo: "texto-largo", obligatorio: true, ayuda: "p. ej. {\"model\": \"{{modelo}}\", \"messages\": \"{{mensajes_con_sistema}}\"}" },
      { nombre: "ruta_texto", titulo: "Ruta del texto", tipo: "texto", obligatorio: true, defecto: "choices.0.message.content" },
      { nombre: "ruta_tokens_entrada", titulo: "Ruta de tokens de entrada", tipo: "texto", ayuda: "opcional, p. ej. usage.prompt_tokens" },
      { nombre: "ruta_tokens_salida", titulo: "Ruta de tokens de salida", tipo: "texto", ayuda: "opcional, p. ej. usage.completion_tokens" }] },
];
