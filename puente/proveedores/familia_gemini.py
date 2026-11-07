"""
Gemini: en Vertex AI (identidad del servicio, ADC; lo de siempre) y directo con
una API key de Google AI Studio. Los dos con `google-genai` (Apache-2.0).
JSON con esquema nativo (response_schema), imágenes y PDF como partes, y
herramientas (function calling) con las firmas de pensamiento que exige
Gemini 3 (se devuelve el contenido del modelo tal cual en la vuelta siguiente).
"""
from __future__ import annotations

import os
import threading
import uuid
from typing import Any, Dict, List, Tuple

from proveedores.base import ErrorDeProveedor, Peticion, Resultado, enmascarar, huella, instruccion_json, secretos_de
from proveedores.registro import Adaptador, registrar

PROYECTO = os.environ.get("GCP_PROJECT", "positiva-sgda-prd-416417")
UBICACION = os.environ.get("GCP_LOCATION", "global")

_clientes: Dict[Tuple[str, str, str], Any] = {}
_cerrojo = threading.Lock()


def _error(e: Exception, proveedor: str, secretos: List[str]) -> Exception:
    codigo = getattr(e, "code", None)
    if isinstance(codigo, int) and not isinstance(codigo, bool):
        return ErrorDeProveedor(enmascarar(f"{proveedor} respondió {codigo}: {getattr(e, 'message', None) or e}", secretos)[:700], codigo, None, proveedor)
    nombre = type(e).__name__
    if "Timeout" in nombre:
        return TimeoutError(enmascarar(f"{proveedor}: se agotó la espera ({nombre})", secretos))
    if "Connect" in nombre:
        return ConnectionError(enmascarar(f"{proveedor}: no se pudo conectar ({nombre})", secretos))
    return ErrorDeProveedor(enmascarar(f"{proveedor}: {nombre}: {e}", secretos)[:700], None, None, proveedor)


class _Google(Adaptador):
    vertex = True
    json_modo = "nativo"
    por_defecto = {"texto": True, "imagenes": True, "pdf": True, "json_estructurado": True, "herramientas": True}
    lista_en_vivo = False

    def cliente(self, cred: dict, flujo: str):
        from google import genai

        k = (self.id, huella(cred), flujo or "-")
        with _cerrojo:
            if k not in _clientes:
                if self.vertex:
                    import google.auth

                    adc, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
                    _clientes[k] = genai.Client(vertexai=True, project=PROYECTO, location=UBICACION, credentials=adc)
                else:
                    _clientes[k] = genai.Client(api_key=str(cred.get("api_key") or ""))
            return _clientes[k]

    @staticmethod
    def contenidos(p: Peticion):
        from google.genai import types

        # Un solo turno de texto va como texto plano: así se llamaba siempre a Vertex.
        if len(p.turnos) == 1 and p.turnos[0]["rol"] == "usuario" and all(x.tipo == "texto" for x in p.turnos[0]["partes"]):
            return "\n\n".join(x.texto for x in p.turnos[0]["partes"])
        r = []
        for t in p.turnos:
            if t["rol"] == "usuario":
                partes = [types.Part.from_text(text=x.texto) if x.tipo == "texto" else types.Part.from_bytes(data=x.datos, mime_type=x.mime)
                          for x in t["partes"]]
                r.append(types.Content(role="user", parts=partes))
            elif t["rol"] == "asistente":
                if t.get("nativo") is not None:
                    r.append(t["nativo"])
                else:
                    partes = [types.Part.from_text(text=t.get("texto") or "")] if t.get("texto") else []
                    partes += [types.Part.from_function_call(name=c["nombre"], args=c.get("args") or {}) for c in t.get("llamadas") or []]
                    r.append(types.Content(role="model", parts=partes or [types.Part.from_text(text="")]))
            elif t["rol"] == "herramienta":
                r.append(types.Content(role="user", parts=[
                    types.Part.from_function_response(name=x["nombre"], response=x["resultado"] if isinstance(x["resultado"], dict) else {"resultado": x["resultado"]})
                    for x in t["resultados"]]))
        return r

    def llamar(self, p: Peticion, cred: dict, flujo: str) -> Resultado:
        from google.genai import types

        secretos = secretos_de(cred)
        sistema = p.sistema + (instruccion_json(p.esquema) if p.instruir_json else "")
        config = types.GenerateContentConfig(system_instruction=sistema or None, http_options=types.HttpOptions(timeout=int(p.timeout * 1000)))
        if p.temperatura is not None:
            config.temperature = p.temperatura
        if p.max_tokens:
            config.max_output_tokens = p.max_tokens
        if (p.esquema or p.json_libre) and not p.herramientas:
            config.response_mime_type = "application/json"
            if p.esquema:
                config.response_schema = p.esquema
        if p.sin_razonamiento and "2.5-flash" in p.modelo:
            config.thinking_config = types.ThinkingConfig(thinking_budget=0)
        if p.herramientas:
            config.tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=h["nombre"], description=h.get("descripcion") or "", parameters_json_schema=h.get("parametros") or {"type": "object", "properties": {}})
                for h in p.herramientas])]
            config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
        try:
            r = self.cliente(cred, flujo).models.generate_content(model=p.modelo, contents=self.contenidos(p), config=config)
        except Exception as e:  # noqa: BLE001 - se traduce a un error con código
            raise _error(e, self.nombre, secretos) from None
        uso = getattr(r, "usage_metadata", None)
        llamadas = [{"id": getattr(fc, "id", None) or f"llamada-{uuid.uuid4().hex[:8]}", "nombre": fc.name, "args": dict(fc.args or {})}
                    for fc in (getattr(r, "function_calls", None) or [])]
        cand = (getattr(r, "candidates", None) or [None])[0]
        partes = getattr(getattr(cand, "content", None), "parts", None) or []
        # El texto son las partes de texto que no son pensamiento (sin pasar por r.text, que avisa si hay llamadas).
        texto = "".join(p.text for p in partes if getattr(p, "text", None) and not getattr(p, "thought", False))
        return Resultado(texto=texto, llamadas=llamadas, tokens_entrada=getattr(uso, "prompt_token_count", 0) or 0,
                         tokens_salida=(getattr(uso, "candidates_token_count", 0) or 0) + (getattr(uso, "thoughts_token_count", 0) or 0),
                         nativo=getattr(cand, "content", None), fin=str(getattr(cand, "finish_reason", "") or ""))


class Vertex(_Google):
    id = "vertex"
    nombre = "Vertex AI (Gemini)"
    descripcion = "Gemini en Google Cloud con la identidad del servicio (lo que usan PQRD y tutelas). No necesita llave."
    autenticacion = "Identidad del servicio (ADC) del proyecto " + PROYECTO
    tipo_credencial = None
    requiere_credencial = False
    vertex = True

    def revisar_credencial(self, cred: dict) -> None:
        return None


class Gemini(_Google):
    id = "gemini"
    nombre = "Gemini (API de Google AI)"
    descripcion = "Gemini directo con una API key de Google AI Studio."
    autenticacion = "API key de Google AI Studio (encabezado x-goog-api-key)"
    tipo_credencial = "ia_gemini"
    vertex = False
    lista_en_vivo = True

    def listar(self, cred: dict, flujo: str, timeout: float) -> List[dict]:
        r = []
        try:
            for m in self.cliente(cred, flujo).models.list():
                acciones = list(getattr(m, "supported_actions", None) or [])
                nombre = str(getattr(m, "name", "") or "").removeprefix("models/")
                if not nombre or not any(a in acciones for a in ("generateContent", "bidiGenerateContent")):
                    continue
                r.append({"id": nombre, "nombre": getattr(m, "display_name", None) or nombre, "contexto": getattr(m, "input_token_limit", None),
                          "acepta": {"voz_en_vivo": "bidiGenerateContent" in acciones, **({"texto": True} if "generateContent" in acciones else {})}})
        except Exception as e:  # noqa: BLE001
            raise _error(e, self.nombre, secretos_de(cred)) from None
        return r


registrar(Vertex())
registrar(Gemini())
