"""
Los servicios con la API de chat de OpenAI (`/chat/completions`), por REST con
httpx (BSD): OpenAI, Azure OpenAI, DeepSeek, Mistral, Groq, OpenRouter, Ollama
(local) y cualquier «compatible con OpenAI» (URL base + llave).

Cada uno es un preset: URL, cómo se autentica, cómo pide JSON y qué acepta un
modelo que no está en el catálogo.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

from proveedores.base import (ErrorDeNegocio, ErrorDeProveedor, Peticion, Resultado, a_texto_json, esquema_estricto, huella,
                              instruccion_json, pedir_json, secretos_de, url_segura)
from proveedores.registro import Adaptador, registrar

# URL base de los servicios fijos (las pruebas las apuntan a un servidor falso local).
URLS: Dict[str, str] = {
    "openai": "https://api.openai.com/v1", "deepseek": "https://api.deepseek.com/v1", "mistral": "https://api.mistral.ai/v1",
    "groq": "https://api.groq.com/openai/v1", "openrouter": "https://openrouter.ai/api/v1",
}
# Modelos de razonamiento de OpenAI que solo aceptan la temperatura por defecto.
_SIN_TEMPERATURA = re.compile(r"^(o\d|gpt-5)", re.I)


def _encabezados_extra(cred: dict) -> Dict[str, str]:
    enc = cred.get("encabezados")
    if isinstance(enc, str) and enc.strip():
        try:
            enc = json.loads(enc)
        except ValueError:
            raise ErrorDeNegocio("encabezados de la credencial: tiene que ser un JSON {nombre: valor}") from None
    if enc and not isinstance(enc, dict):
        raise ErrorDeNegocio("encabezados de la credencial: tiene que ser un JSON {nombre: valor}")
    return {str(k): str(v).replace("{{api_key}}", str(cred.get("api_key") or "")) for k, v in (enc or {}).items()}


class EstiloOpenAI(Adaptador):
    json_modo = "nativo"
    por_defecto = {"texto": True, "imagenes": None, "pdf": None, "json_estructurado": True, "herramientas": None}
    lista_en_vivo = True
    pdf_como_archivo = True  # OpenAI acepta {"type": "file"} con el PDF en base64

    # ------------------------------------------------------------ por preset
    def base(self, cred: dict) -> str:
        return URLS[self.id]

    def encabezados(self, cred: dict) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if cred.get("api_key"):
            h["Authorization"] = f"Bearer {cred['api_key']}"
        return h

    def url_chat(self, cred: dict, modelo: str) -> str:
        return f"{self.base(cred)}/chat/completions"

    def url_modelos(self, cred: dict) -> str:
        return f"{self.base(cred)}/models"

    def con_temperatura(self, modelo: str) -> bool:
        return True

    # ----------------------------------------------------------------- común
    def _contenido(self, partes) -> Any:
        if all(x.tipo == "texto" for x in partes):
            return "\n\n".join(x.texto for x in partes)
        r: List[dict] = []
        for x in partes:
            if x.tipo == "texto":
                r.append({"type": "text", "text": x.texto})
            elif x.tipo == "imagen":
                r.append({"type": "image_url", "image_url": {"url": f"data:{x.mime or 'image/jpeg'};base64,{x.b64()}"}})
            elif x.tipo == "pdf":
                if not self.pdf_como_archivo:
                    raise ErrorDeNegocio(f"{self.nombre} no acepta PDF como parte del mensaje")
                r.append({"type": "file", "file": {"filename": x.nombre or "documento.pdf", "file_data": f"data:application/pdf;base64,{x.b64()}"}})
        return r

    def mensajes(self, p: Peticion, sistema: str) -> List[dict]:
        r: List[dict] = [{"role": "system", "content": sistema}] if sistema else []
        for t in p.turnos:
            if t["rol"] == "usuario":
                r.append({"role": "user", "content": self._contenido(t["partes"])})
            elif t["rol"] == "asistente":
                m: Dict[str, Any] = {"role": "assistant", "content": t.get("texto") or None}
                if t.get("llamadas"):
                    m["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["nombre"], "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False)}}
                                       for c in t["llamadas"]]
                r.append(m)
            elif t["rol"] == "herramienta":
                r += [{"role": "tool", "tool_call_id": x["id"], "content": a_texto_json(x["resultado"])} for x in t["resultados"]]
        return r

    def cuerpo(self, p: Peticion) -> Dict[str, Any]:
        sistema = p.sistema
        modo = "instruido" if p.instruir_json else self.json_modo
        quiere_json = bool(p.esquema or p.json_libre) and not p.herramientas
        if quiere_json and (modo != "nativo" or not p.esquema):
            sistema += instruccion_json(p.esquema)
        c: Dict[str, Any] = {"model": p.modelo, "messages": self.mensajes(p, sistema)}
        if p.temperatura is not None and self.con_temperatura(p.modelo):
            c["temperature"] = p.temperatura
        if p.max_tokens:
            c["max_tokens"] = p.max_tokens
        if quiere_json:
            if modo == "nativo" and p.esquema:
                c["response_format"] = {"type": "json_schema", "json_schema": {"name": "respuesta", "schema": esquema_estricto(p.esquema), "strict": False}}
            elif modo in ("nativo", "objeto"):
                c["response_format"] = {"type": "json_object"}
        if p.herramientas:
            c["tools"] = [{"type": "function", "function": {"name": h["nombre"], "description": h.get("descripcion") or "",
                                                            "parameters": h.get("parametros") or {"type": "object", "properties": {}}}} for h in p.herramientas]
            c["tool_choice"] = "auto"
        return c

    def llamar(self, p: Peticion, cred: dict, flujo: str) -> Resultado:
        j = pedir_json(self.nombre, "POST", self.url_chat(cred, p.modelo), self.encabezados(cred), self.cuerpo(p), p.timeout,
                       secretos_de(cred), huella(cred), flujo)
        elegido = (j.get("choices") or [{}])[0] if isinstance(j, dict) else {}
        m = elegido.get("message") or {}
        texto = m.get("content") or ""
        if isinstance(texto, list):  # algunos servicios devuelven partes
            texto = "".join(x.get("text", "") for x in texto if isinstance(x, dict))
        llamadas = []
        for tc in m.get("tool_calls") or []:
            f = tc.get("function") or {}
            try:
                args = json.loads(f.get("arguments") or "{}")
            except ValueError:
                args = {"_crudo": f.get("arguments")}
            llamadas.append({"id": tc.get("id") or f"llamada-{uuid.uuid4().hex[:8]}", "nombre": f.get("name"), "args": args})
        uso = j.get("usage") or {}
        return Resultado(texto=texto, llamadas=llamadas, tokens_entrada=int(uso.get("prompt_tokens") or 0),
                         tokens_salida=int(uso.get("completion_tokens") or 0), fin=str(elegido.get("finish_reason") or ""))

    def listar(self, cred: dict, flujo: str, timeout: float) -> List[dict]:
        j = pedir_json(self.nombre, "GET", self.url_modelos(cred), self.encabezados(cred), None, timeout, secretos_de(cred), huella(cred), flujo)
        datos = j.get("data") if isinstance(j, dict) else j
        if isinstance(j, dict) and datos is None:
            datos = j.get("models") or []
        r = []
        for m in datos or []:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            x: Dict[str, Any] = {"id": m["id"], "nombre": m.get("name") or m["id"]}
            if m.get("context_length"):
                x["contexto"] = m["context_length"]
            precio = m.get("pricing") or {}
            try:
                if precio.get("prompt") is not None:
                    x["precio_entrada"] = round(float(precio["prompt"]) * 1e6, 4)
                    x["precio_salida"] = round(float(precio.get("completion") or 0) * 1e6, 4)
            except (TypeError, ValueError):
                pass
            modos = ((m.get("architecture") or {}).get("input_modalities")) or []
            params = m.get("supported_parameters") or []
            if modos or params:
                x["acepta"] = {"texto": True, "imagenes": "image" in modos, "pdf": "file" in modos,
                               "herramientas": "tools" in params, "json_estructurado": "response_format" in params or "structured_outputs" in params or None}
            r.append(x)
        return r


class OpenAI(EstiloOpenAI):
    id, nombre = "openai", "OpenAI"
    descripcion = "Modelos GPT de OpenAI."
    autenticacion = "API key (Authorization: Bearer); organización opcional (OpenAI-Organization)"
    tipo_credencial = "ia_openai"

    def encabezados(self, cred: dict) -> Dict[str, str]:
        h = super().encabezados(cred)
        if cred.get("organizacion"):
            h["OpenAI-Organization"] = str(cred["organizacion"])
        return h

    def con_temperatura(self, modelo: str) -> bool:
        return not _SIN_TEMPERATURA.match(modelo)


class AzureOpenAI(EstiloOpenAI):
    id, nombre = "azure_openai", "Azure OpenAI"
    descripcion = "Modelos de OpenAI desplegados en Azure: el «modelo» es el nombre del despliegue."
    autenticacion = "Endpoint del recurso + api-key (encabezado api-key) + api-version"
    tipo_credencial = "ia_azure_openai"

    def revisar_credencial(self, cred: dict) -> None:
        super().revisar_credencial(cred)
        if not cred.get("endpoint"):
            raise ErrorDeNegocio("Azure OpenAI: la credencial necesita endpoint (https://<recurso>.openai.azure.com)")

    def base(self, cred: dict) -> str:
        return url_segura(str(cred.get("endpoint") or ""), permitir_privada=False)

    def encabezados(self, cred: dict) -> Dict[str, str]:
        return {"Content-Type": "application/json", "api-key": str(cred.get("api_key") or "")}

    def version(self, cred: dict) -> str:
        return str(cred.get("api_version") or "2024-10-21")

    def url_chat(self, cred: dict, modelo: str) -> str:
        despliegue = modelo or str(cred.get("despliegue") or "")
        return f"{self.base(cred)}/openai/deployments/{despliegue}/chat/completions?api-version={self.version(cred)}"

    def url_modelos(self, cred: dict) -> str:
        return f"{self.base(cred)}/openai/deployments?api-version=2022-12-01"

    def con_temperatura(self, modelo: str) -> bool:
        return not _SIN_TEMPERATURA.match(modelo)

    def cuerpo(self, p: Peticion) -> Dict[str, Any]:
        c = super().cuerpo(p)
        c.pop("model", None)  # en Azure el modelo es el despliegue de la URL
        return c


class DeepSeek(EstiloOpenAI):
    id, nombre = "deepseek", "DeepSeek"
    descripcion = "Modelos de DeepSeek (API compatible con OpenAI)."
    autenticacion = "API key (Authorization: Bearer)"
    tipo_credencial = "ia_deepseek"
    json_modo = "objeto"
    pdf_como_archivo = False
    por_defecto = {"texto": True, "imagenes": False, "pdf": False, "json_estructurado": True, "herramientas": True}


class Mistral(EstiloOpenAI):
    id, nombre = "mistral", "Mistral AI"
    descripcion = "Modelos de Mistral AI."
    autenticacion = "API key (Authorization: Bearer)"
    tipo_credencial = "ia_mistral"
    pdf_como_archivo = False


class Groq(EstiloOpenAI):
    id, nombre = "groq", "Groq"
    descripcion = "Modelos abiertos servidos por Groq (muy rápidos)."
    autenticacion = "API key (Authorization: Bearer)"
    tipo_credencial = "ia_groq"
    json_modo = "objeto"
    pdf_como_archivo = False


class OpenRouter(EstiloOpenAI):
    id, nombre = "openrouter", "OpenRouter"
    descripcion = "Cientos de modelos de muchos proveedores con una sola llave."
    autenticacion = "API key (Authorization: Bearer) + HTTP-Referer y X-Title"
    tipo_credencial = "ia_openrouter"
    json_modo = "objeto"

    def encabezados(self, cred: dict) -> Dict[str, str]:
        return {**super().encabezados(cred), "HTTP-Referer": "https://positiva-ia.local", "X-Title": "Positiva IA"}


class Ollama(EstiloOpenAI):
    id, nombre = "ollama", "Ollama (local)"
    descripcion = "Modelos que corren en un servidor propio con Ollama (red interna)."
    autenticacion = "URL base del servidor (p. ej. http://ollama:11434/v1); llave opcional"
    tipo_credencial = "ia_ollama"
    requiere_credencial = True
    json_modo = "objeto"
    pdf_como_archivo = False
    por_defecto = {"texto": True, "imagenes": None, "pdf": False, "json_estructurado": True, "herramientas": None}

    def revisar_credencial(self, cred: dict) -> None:
        if not cred.get("base_url"):
            raise ErrorDeNegocio("Ollama: la credencial necesita base_url (p. ej. http://ollama:11434/v1)")

    def base(self, cred: dict) -> str:
        return url_segura(str(cred.get("base_url") or ""))


class Compatible(EstiloOpenAI):
    id, nombre = "openai_compatible", "Compatible con OpenAI"
    descripcion = "Cualquier servicio propio o de terceros con la API de chat de OpenAI (vLLM, LM Studio, LiteLLM, TGI…)."
    autenticacion = "URL base + llave opcional (Bearer) + encabezados opcionales"
    tipo_credencial = "ia_compatible"
    json_modo = "instruido"
    pdf_como_archivo = False
    por_defecto = {"texto": True, "imagenes": None, "pdf": False, "json_estructurado": True, "herramientas": None}

    def revisar_credencial(self, cred: dict) -> None:
        if not cred.get("base_url"):
            raise ErrorDeNegocio("Compatible con OpenAI: la credencial necesita base_url")

    def base(self, cred: dict) -> str:
        return url_segura(str(cred.get("base_url") or ""))

    def encabezados(self, cred: dict) -> Dict[str, str]:
        return {**super().encabezados(cred), **_encabezados_extra(cred)}


for _a in (OpenAI(), AzureOpenAI(), DeepSeek(), Mistral(), Groq(), OpenRouter(), Ollama(), Compatible()):
    registrar(_a)


def preset(proveedor: str) -> Optional[EstiloOpenAI]:
    from proveedores.registro import PROVEEDORES

    a = PROVEEDORES.get(proveedor)
    return a if isinstance(a, EstiloOpenAI) else None


__all__ = ["URLS", "EstiloOpenAI", "preset", "ErrorDeProveedor"]
