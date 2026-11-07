"""
«Personalizado»: cualquier servicio de IA por HTTP sin escribir código. Todo
sale de la credencial `ia_personalizado`:

- `url` (admite {{modelo}}), `metodo` (POST por defecto)
- `api_key` (secreta) y `encabezados`: JSON {nombre: valor} donde {{api_key}} se reemplaza
- `cuerpo`: plantilla JSON del pedido con {{modelo}}, {{sistema}}, {{mensajes}}
  (lista [{role, content}] sin el sistema), {{mensajes_con_sistema}}, {{tarea}}
  (el último texto del usuario) y {{temperatura}}. Un valor que es SOLO una
  plantilla conserva su tipo (lista, número); dentro de un texto se escribe como texto.
- `ruta_texto` (p. ej. «choices.0.message.content»), `ruta_tokens_entrada` y
  `ruta_tokens_salida` (opcionales) para leer la respuesta.

Solo texto (sin imágenes ni herramientas); el JSON se pide por instrucciones y se valida.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Dict, List

from proveedores.base import ErrorDeNegocio, ErrorDeProveedor, Peticion, Resultado, huella, instruccion_json, navegar, pedir_json, secretos_de, url_segura
from proveedores.registro import Adaptador, registrar

_PLANTILLA = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def _json_de_campo(v: Any, que: str) -> Any:
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(str(v or ""))
    except ValueError:
        raise ErrorDeNegocio(f"Personalizado: {que} de la credencial no es un JSON válido") from None


def rellenar(plantilla: Any, valores: Dict[str, Any]) -> Any:
    """Reemplaza {{x}} en una plantilla JSON ya leída, conservando el tipo cuando el texto es solo la plantilla."""
    if isinstance(plantilla, str):
        m = _PLANTILLA.fullmatch(plantilla.strip())
        if m and m.group(1) in valores:
            return valores[m.group(1)]
        return _PLANTILLA.sub(lambda x: (v if isinstance(v := valores.get(x.group(1), x.group(0)), str) else json.dumps(v, ensure_ascii=False)), plantilla)
    if isinstance(plantilla, dict):
        return {k: rellenar(v, valores) for k, v in plantilla.items()}
    if isinstance(plantilla, list):
        return [rellenar(v, valores) for v in plantilla]
    return plantilla


class Personalizado(Adaptador):
    id, nombre = "personalizado", "Personalizado (HTTP)"
    descripcion = "Cualquier otro servicio de IA por HTTP: URL, encabezados, plantilla del pedido y dónde leer la respuesta, todo en la credencial."
    autenticacion = "Lo que diga la credencial: llave en un encabezado o en la URL con {{api_key}}"
    tipo_credencial = "ia_personalizado"
    json_modo = "instruido"
    por_defecto = {"texto": True, "imagenes": False, "pdf": False, "json_estructurado": True, "herramientas": False}

    def revisar_credencial(self, cred: dict) -> None:
        for k in ("url", "cuerpo", "ruta_texto"):
            if not cred.get(k):
                raise ErrorDeNegocio(f"Personalizado: la credencial necesita {k}")

    def llamar(self, p: Peticion, cred: dict, flujo: str) -> Resultado:
        self.revisar_credencial(cred)
        if p.herramientas:
            raise ErrorDeNegocio("Personalizado no admite herramientas")
        sistema = p.sistema + (instruccion_json(p.esquema) if (p.esquema or p.json_libre) else "")
        mensajes: List[dict] = []
        for t in p.turnos:
            if t["rol"] == "usuario":
                if any(x.tipo != "texto" for x in t["partes"]):
                    raise ErrorDeNegocio("Personalizado solo acepta texto (sin imágenes ni PDF)")
                mensajes.append({"role": "user", "content": "\n\n".join(x.texto for x in t["partes"])})
            elif t["rol"] == "asistente":
                mensajes.append({"role": "assistant", "content": t.get("texto") or ""})
        llave = str(cred.get("api_key") or "")
        valores = {"modelo": p.modelo, "sistema": sistema, "mensajes": mensajes, "api_key": llave,
                   "mensajes_con_sistema": ([{"role": "system", "content": sistema}] if sistema else []) + mensajes,
                   "tarea": next((m["content"] for m in reversed(mensajes) if m["role"] == "user"), ""),
                   "temperatura": p.temperatura if p.temperatura is not None else 0.1}
        url = _PLANTILLA.sub(lambda m: urllib.parse.quote(str(valores.get(m.group(1), "")), safe=""), str(cred["url"]))
        url = url_segura(url)
        enc = {str(k): str(v).replace("{{api_key}}", llave) for k, v in (_json_de_campo(cred.get("encabezados") or "{}", "encabezados") or {}).items()}
        enc.setdefault("Content-Type", "application/json")
        cuerpo = rellenar(_json_de_campo(cred["cuerpo"], "cuerpo"), valores)
        j = pedir_json(self.nombre, str(cred.get("metodo") or "POST").upper(), url, enc, cuerpo, p.timeout, secretos_de(cred) + [llave], huella(cred), flujo)
        texto = navegar(j, str(cred["ruta_texto"]))
        if texto is None:
            raise ErrorDeProveedor(f"Personalizado: la respuesta no trae nada en «{cred['ruta_texto']}»")

        def entero(ruta: Any) -> int:
            try:
                return int(navegar(j, str(ruta)) or 0) if ruta else 0
            except (TypeError, ValueError):
                return 0

        return Resultado(texto=texto if isinstance(texto, str) else json.dumps(texto, ensure_ascii=False),
                         tokens_entrada=entero(cred.get("ruta_tokens_entrada")), tokens_salida=entero(cred.get("ruta_tokens_salida")))


registrar(Personalizado())
