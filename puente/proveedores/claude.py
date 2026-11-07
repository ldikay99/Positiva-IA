"""
Claude (Anthropic) por la Messages API, con REST y httpx (BSD).

- JSON con esquema nativo: `output_config.format = {type: "json_schema", schema}`.
- Imágenes y PDF como bloques `image` y `document` en base64.
- Herramientas: `tools` + `tool_choice: auto` (los modelos nuevos rechazan forzar
  una herramienta); el contenido del asistente se devuelve tal cual en la
  vuelta siguiente (incluye los bloques de pensamiento).
- No se manda temperatura: los modelos actuales la rechazan.
"""
from __future__ import annotations

from typing import Any, Dict, List

from proveedores.base import (ErrorDeNegocio, Peticion, Resultado, a_texto_json, esquema_estricto, huella, instruccion_json,
                              pedir_json, secretos_de)
from proveedores.registro import Adaptador, registrar

URL = {"base": "https://api.anthropic.com"}
VERSION = "2023-06-01"
MAX_TOKENS = 16000


class Anthropic(Adaptador):
    id, nombre = "anthropic", "Anthropic (Claude)"
    descripcion = "Modelos Claude de Anthropic."
    autenticacion = "API key (encabezado x-api-key) + anthropic-version"
    tipo_credencial = "ia_anthropic"
    json_modo = "nativo"
    por_defecto = {"texto": True, "imagenes": True, "pdf": True, "json_estructurado": True, "herramientas": True}
    lista_en_vivo = True

    def encabezados(self, cred: dict) -> Dict[str, str]:
        return {"Content-Type": "application/json", "x-api-key": str(cred.get("api_key") or ""), "anthropic-version": VERSION}

    @staticmethod
    def _bloques(partes) -> List[dict]:
        r = []
        for x in partes:
            if x.tipo == "texto":
                r.append({"type": "text", "text": x.texto or " "})
            elif x.tipo == "imagen":
                r.append({"type": "image", "source": {"type": "base64", "media_type": x.mime or "image/jpeg", "data": x.b64()}})
            elif x.tipo == "pdf":
                r.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": x.b64()}})
        return r

    def cuerpo(self, p: Peticion) -> Dict[str, Any]:
        sistema = p.sistema
        quiere_json = bool(p.esquema or p.json_libre) and not p.herramientas
        if quiere_json and (p.instruir_json or not p.esquema):
            sistema += instruccion_json(p.esquema)
        mensajes: List[dict] = []
        for t in p.turnos:
            if t["rol"] == "usuario":
                mensajes.append({"role": "user", "content": self._bloques(t["partes"])})
            elif t["rol"] == "asistente":
                if t.get("nativo"):
                    mensajes.append({"role": "assistant", "content": t["nativo"]})
                else:
                    bloques = [{"type": "text", "text": t["texto"]}] if t.get("texto") else []
                    bloques += [{"type": "tool_use", "id": c["id"], "name": c["nombre"], "input": c.get("args") or {}} for c in t.get("llamadas") or []]
                    mensajes.append({"role": "assistant", "content": bloques})
            elif t["rol"] == "herramienta":
                mensajes.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": x["id"], "content": a_texto_json(x["resultado"])}
                                                              for x in t["resultados"]]})
        c: Dict[str, Any] = {"model": p.modelo, "max_tokens": p.max_tokens or MAX_TOKENS, "messages": mensajes}
        if sistema:
            c["system"] = sistema
        if quiere_json and p.esquema and not p.instruir_json:
            c["output_config"] = {"format": {"type": "json_schema", "schema": esquema_estricto(p.esquema)}}
        if p.herramientas:
            c["tools"] = [{"name": h["nombre"], "description": h.get("descripcion") or "",
                           "input_schema": h.get("parametros") or {"type": "object", "properties": {}}} for h in p.herramientas]
            c["tool_choice"] = {"type": "auto"}
        return c

    def llamar(self, p: Peticion, cred: dict, flujo: str) -> Resultado:
        j = pedir_json(self.nombre, "POST", f"{URL['base']}/v1/messages", self.encabezados(cred), self.cuerpo(p), p.timeout,
                       secretos_de(cred), huella(cred), flujo)
        if j.get("stop_reason") == "refusal":
            raise ErrorDeNegocio("Claude declinó responder esta solicitud (stop_reason: refusal)")
        contenido = j.get("content") or []
        texto = "".join(b.get("text", "") for b in contenido if b.get("type") == "text")
        llamadas = [{"id": b.get("id"), "nombre": b.get("name"), "args": b.get("input") or {}} for b in contenido if b.get("type") == "tool_use"]
        uso = j.get("usage") or {}
        entrada = int(uso.get("input_tokens") or 0) + int(uso.get("cache_read_input_tokens") or 0) + int(uso.get("cache_creation_input_tokens") or 0)
        return Resultado(texto=texto, llamadas=llamadas, tokens_entrada=entrada, tokens_salida=int(uso.get("output_tokens") or 0),
                         nativo=contenido, fin=str(j.get("stop_reason") or ""))

    def listar(self, cred: dict, flujo: str, timeout: float) -> List[dict]:
        j = pedir_json(self.nombre, "GET", f"{URL['base']}/v1/models?limit=100", self.encabezados(cred), None, timeout, secretos_de(cred), huella(cred), flujo)
        r = []
        for m in j.get("data") or []:
            x: Dict[str, Any] = {"id": m.get("id"), "nombre": m.get("display_name") or m.get("id")}
            if m.get("max_input_tokens"):
                x["contexto"] = m["max_input_tokens"]
            r.append(x)
        return [x for x in r if x["id"]]


registrar(Anthropic())
