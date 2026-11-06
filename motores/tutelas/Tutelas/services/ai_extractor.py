"""
Servicio de extracción de información usando Gemini AI
Replica la lógica de los agentes de n8n
"""
import json
import re
#import google.generativeai as genai
from google import genai
from google.genai import types
from typing import Optional

try:
    from Tutelas.config import GEMINI_CONFIG
    from Tutelas.models.correo import DatosExtraidos
except ImportError:
    from config import GEMINI_CONFIG
    from models.correo import DatosExtraidos


class AIExtractor:
    #def __init__(self):
    #    self.config = GEMINI_CONFIG
    #    genai.configure()
    #    self.model = genai.GenerativeModel(self.config["model"])


    def __init__(self):
        self.config = GEMINI_CONFIG

        self.client = genai.Client(
            vertexai=True,
            project=self.config["project_id"],
            location=self.config["location"],
        )

        self.model = self.config["model"]

    def _limpiar_json_response(self, response_text: str) -> str:
        """Limpia la respuesta de Gemini para obtener JSON válido"""
        text = response_text.strip()

        # Remover bloques de código markdown
        if "```json" in text:
            text = re.sub(r'```json\n?|\n?```', '', text).strip()
        elif "```" in text:
            text = re.sub(r'```\n?|\n?```', '', text).strip()

        return text

    def extraer_radicado_y_juzgado(self, asunto: str, remitente: str, texto_correo: str) -> DatosExtraidos:
        """
        Extrae el radicado de 23 dígitos y la información del juzgado.
        Replica el agente "Traductor de Juzgados" de n8n.
        """
        prompt = f"""# AGENTE: ANALISTA FORENSE JUDICIAL

Eres un Analista Forense experto en procesos judiciales colombianos. Tu misión es extraer el Radicado de 23 dígitos y la información del Juzgado con precisión absoluta.

## PROTOCOLO DE EXTRACCIÓN

### A. RADICADO DE 23 DÍGITOS
- **Longitud**: Debe ser de exactamente 23 caracteres numéricos.
- **Limpieza**: Remueve OBLIGATORIAMENTE espacios, guiones (-), puntos (.) o paréntesis.
- **Composición**:
    - Código Despacho: 12 dígitos iniciales.
    - Año: 4 dígitos siguientes.
    - Consecutivo: 5 dígitos siguientes.
    - Instancia: 2 dígitos finales.
- **Prioridad**: Si hay discrepancias, el texto del PDF (OCR) tiene prioridad sobre el asunto del correo.

### B. INFORMACIÓN DEL JUZGADO
- **Nombre**: Extrae el nombre oficial completo (ej: JUZGADO TERCERO ADMINISTRATIVO).
- **Código**: Los primeros 12 dígitos del radicado se identifican como codigo_juzgado.
- **Especialidad y Ciudad**: Identifica claramente estos dos campos a partir del nombre del juzgado.

## FORMATO DE SALIDA (JSON)
Entrega estrictamente un objeto JSON con la siguiente estructura:

{{
  "radicado_23": "05045333300320250009900",
  "codigo_juzgado": "050453333003",
  "juzgado_nombre_ocr": "JUZGADO TERCERO ADMINISTRATIVO DEL CIRCUITO DE APARTADÓ",
  "ciudad": "APARTADÓ",
  "es_valido": true
}}

### CRITERIOS DE ERROR:
- Si el radicado tiene menos de 23 dígitos: `es_valido: false` y `radicado_23: "ERROR_INCOMPLETO"`.
- Si no encuentras ningún radicado: `es_valido: false` y `radicado_23: "NO_ENCONTRADO"`.

---

## Contexto recibido:
Asunto: {asunto}
Remitente: {remitente}
Texto del correo/OCR: {texto_correo[:4000]}

RESPONDE SOLO CON EL JSON, SIN EXPLICACIONES ADICIONALES."""

        try:
            #response = self.model.generate_content(prompt)
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )

            response_text = self._limpiar_json_response(response.text)

            data = json.loads(response_text)

            return DatosExtraidos(
                radicado_23=data.get("radicado_23"),
                codigo_juzgado=data.get("codigo_juzgado"),
                juzgado_nombre_ocr=data.get("juzgado_nombre_ocr"),
                ciudad=data.get("ciudad", "").upper() if data.get("ciudad") else None,
                es_valido=data.get("es_valido", False),
            )

        except json.JSONDecodeError as e:
            print(f"Error parseando JSON de Gemini: {e}")
            return DatosExtraidos(es_valido=False)
        except Exception as e:
            print(f"Error en extracción AI: {e}")
            return DatosExtraidos(es_valido=False)

    def extraer_radicado_de_pdf(self, pdf_bytes: bytes) -> Optional[str]:
        """Extrae el radicado de 23 dígitos de un PDF (con texto o escaneado) usando
        Gemini multimodal (visión). Devuelve el radicado de 23 dígitos, o None si el
        documento no contiene uno válido.

        GUARD: solo acepta EXACTAMENTE 23 dígitos (regla del repo). Si el modelo
        devuelve algo distinto (incompleto/alucinado), retorna None -> el caso cae a
        revisión manual en vez de radicar un número inválido.
        """
        prompt = (
            "Eres un analista forense judicial colombiano. En el DOCUMENTO adjunto, "
            "encuentra el NUMERO UNICO DE RADICACION del proceso judicial: EXACTAMENTE "
            "23 digitos (12 codigo despacho + 4 año + 5 consecutivo + 2 instancia). "
            "Puede venir con guiones, espacios o puntos; ignoralos y concatena. "
            "Responde SOLO los 23 digitos, sin separadores ni texto. "
            "Si el documento NO contiene un radicado COMPLETO de 23 digitos, responde exactamente NO_ENCONTRADO. "
            "NO inventes, completes ni rellenes digitos que no esten en el documento."
        )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    prompt,
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                ],
                config=types.GenerateContentConfig(temperature=0),
            )
            digitos = re.sub(r"\D", "", (response.text or ""))
            if len(digitos) == 23:
                return digitos
            return None
        except Exception as e:
            print(f"   [AI-PDF] Error extrayendo radicado de PDF: {e}")
            return None

    def extraer_documento_afectado(self, asunto: str, remitente: str, texto_correo: str) -> DatosExtraidos:
        """
        Extrae el número de identificación del afectado.
        Replica el agente "Documento Afectado" de n8n.
        """
        prompt = f"""Eres un Especialista en Identificación de Sujetos Procesales. Tu misión es extraer el número de identificación (Cédula o NIT) de la persona afectada por la tutela (el accionante).

REGLAS DE EXTRACCIÓN:
1. Jerarquía de Verdad: Si la cédula mencionada en el cuerpo del correo es diferente a la que aparece en el PDF oficial, SIEMPRE elige la del PDF.
2. Búsqueda por Patrones: Busca números después de palabras como: "C.C.", "Cédula", "Identificado con", "NIT", "Documento No.".
3. Limpieza: Entrega solo el número sin puntos ni comas.
4. Validación: Las cédulas en Colombia suelen tener entre 7 y 10 dígitos. Los NIT tienen 9 o 10. Si el número tiene más de 12 dígitos, probablemente es un número de teléfono o un radicado; descártalo.

DECISIONES DE FLUJO:
- Si no encuentras ningún número de identificación, responde con numero_documento: "NO_ENCONTRADO".

ENTREGA EL RESULTADO EN ESTE FORMATO JSON:
{{
  "numero_documento": "12345678",
  "tipo_documento": "CC",
  "fuente": "PDF/Correo"
}}

---

## Contexto recibido:
Asunto: {asunto}
Remitente: {remitente}
Texto del correo/OCR: {texto_correo[:4000]}

RESPONDE SOLO CON EL JSON, SIN EXPLICACIONES ADICIONALES."""

        try:
            #response = self.model.generate_content(prompt)
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )


            response_text = self._limpiar_json_response(response.text)

            data = json.loads(response_text)

            # Limpiar el número (solo dígitos)
            numero = data.get("numero_documento", "")
            if numero and numero != "NO_ENCONTRADO":
                numero = re.sub(r'\D', '', numero)

            return DatosExtraidos(
                numero_documento=numero if numero else None,
                tipo_documento=data.get("tipo_documento", "CC"),
                fuente_documento=data.get("fuente"),
            )

        except json.JSONDecodeError as e:
            print(f"Error parseando JSON de Gemini: {e}")
            return DatosExtraidos()
        except Exception as e:
            print(f"Error en extracción AI: {e}")
            return DatosExtraidos()

    def extraer_todo(self, asunto: str, remitente: str, texto_correo: str) -> DatosExtraidos:
        """Extrae toda la información necesaria del correo"""
        # Extraer radicado y juzgado
        datos = self.extraer_radicado_y_juzgado(asunto, remitente, texto_correo)

        # Extraer documento del afectado
        datos_doc = self.extraer_documento_afectado(asunto, remitente, texto_correo)

        # Combinar resultados
        datos.numero_documento = datos_doc.numero_documento
        datos.tipo_documento = datos_doc.tipo_documento
        datos.fuente_documento = datos_doc.fuente_documento

        return datos
