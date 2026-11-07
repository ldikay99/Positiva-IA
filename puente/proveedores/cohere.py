"""Cohere (Command) por la API de chat v2, con REST y httpx (BSD)."""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from proveedores.base import (Peticion, Resultado, a_texto_json, huella, instruccion_json, pedir_json, secretos_de)
from proveedores.registro import Adaptador, registrar

URL = {"base": "https://api.cohere.com"}


class Cohere(Adaptador):
    id, nombre = "cohere", "Cohere"
    descripcion = "Modelos Command de Cohere."
    autenticacion = "API key (Authorization: Bearer)"
    tipo_credencial = "ia_cohere"
    json_modo = "nativo"
    por_defecto = {"texto": True, "imagenes": None, "pdf": False, "json_estructurado": True, "herramientas": True}
    lista_en_vivo = True

    def encabezados(self, cred: dict) -> Dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {cred.get('api_key') or ''}"}

    def cuerpo(self, p: Peticion) -> Dict[str, Any]:
        sistema = p.sistema
        quiere_json = bool(p.esquema or p.json_libre) and not p.herramientas
        if quiere_json and p.instruir_json:
            sistema += instruccion_json(p.esquema)
        mensajes: List[dict] = [{"role": "system", "content": sistema}] if sistema else []
        for t in p.turnos:
            if t["rol"] == "usuario":
                if all(x.tipo == "texto" for x in t["partes"]):
                    contenido: Any = "\n\n".join(x.texto for x in t["partes"])
                else:
                    contenido = [{"type": "text", "text": x.texto} if x.tipo == "texto" else
                                 {"type": "image_url", "image_url": {"url": f"data:{x.mime or 'image/jpeg'};base64,{x.b64()}"}} for x in t["partes"]]
                mensajes.append({"role": "user", "content": contenido})
            elif t["rol"] == "asistente":
                m: Dict[str, Any] = {"role": "assistant"}
                if t.get("texto"):
                    m["content"] = t["texto"]
                if t.get("llamadas"):
                    m["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["nombre"], "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False)}}
                                       for c in t["llamadas"]]
                mensajes.append(m)
            elif t["rol"] == "herramienta":
                mensajes += [{"role": "tool", "tool_call_id": x["id"], "content": a_texto_json(x["resultado"])} for x in t["resultados"]]
        c: Dict[str, Any] = {"model": p.modelo, "messages": mensajes}
        if p.temperatura is not None:
            c["temperature"] = p.temperatura
        if p.max_tokens:
            c["max_tokens"] = p.max_tokens
        if quiere_json:
            c["response_format"] = {"type": "json_object", **({"json_schema": p.esquema} if p.esquema and not p.instruir_json else {})}
        if p.herramientas:
            c["tools"] = [{"type": "function", "function": {"name": h["nombre"], "description": h.get("descripcion") or "",
                                                            "parameters": h.get("parametros") or {"type": "object", "properties": {}}}} for h in p.herramientas]
        return c

    def llamar(self, p: Peticion, cred: dict, flujo: str) -> Resultado:
        j = pedir_json(self.nombre, "POST", f"{URL['base']}/v2/chat", self.encabezados(cred), self.cuerpo(p), p.timeout, secretos_de(cred), huella(cred), flujo)
        m = j.get("message") or {}
        texto = "".join(x.get("text", "") for x in (m.get("content") or []) if isinstance(x, dict) and x.get("type") == "text")
        llamadas = []
        for tc in m.get("tool_calls") or []:
            f = tc.get("function") or {}
            try:
                args = json.loads(f.get("arguments") or "{}")
            except ValueError:
                args = {"_crudo": f.get("arguments")}
            llamadas.append({"id": tc.get("id") or f"llamada-{uuid.uuid4().hex[:8]}", "nombre": f.get("name"), "args": args})
        uso = (j.get("usage") or {}).get("billed_units") or (j.get("usage") or {}).get("tokens") or {}
        return Resultado(texto=texto, llamadas=llamadas, tokens_entrada=int(uso.get("input_tokens") or 0), tokens_salida=int(uso.get("output_tokens") or 0),
                         fin=str(j.get("finish_reason") or ""))

    def listar(self, cred: dict, flujo: str, timeout: float) -> List[dict]:
        j = pedir_json(self.nombre, "GET", f"{URL['base']}/v1/models?endpoint=chat&page_size=100", self.encabezados(cred), None, timeout,
                       secretos_de(cred), huella(cred), flujo)
        return [{"id": m["name"], "nombre": m["name"], **({"contexto": m["context_length"]} if m.get("context_length") else {}),
                 "acepta": {"texto": True, "imagenes": bool(m.get("supports_vision")), "herramientas": "tool_use" in (m.get("features") or []) or None}}
                for m in j.get("models") or [] if m.get("name")]


registrar(Cohere())
