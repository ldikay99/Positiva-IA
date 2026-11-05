"""
Servicio de Google Gemini para analisis de documentos
"""
import logging
from typing import Optional

from google import genai
from google.genai import types

from config import settings
from services.gemini_retry import gemini_retry

logger = logging.getLogger(__name__)

DOCUMENT_ANALYSIS_PROMPT = """# EXTRACTOR DE DATOS - Positiva Seguros ARL

Eres un OCR inteligente. Extrae TODOS los datos de identificacion del documento.

## BUSCA Y EXTRAE (en orden de prioridad):

### 1. NUMEROS DE DOCUMENTO (CRITICO)
- Cedula (CC): 6-10 digitos, busca cerca de "CC", "C.C.", "cedula", "identificacion"
- NIT: 9 digitos, busca cerca de "NIT", "Nit", puede tener formato XXX.XXX.XXX-X
- Extrae el numero SIN puntos ni guiones: "12.345.678" -> "12345678"

### 2. NOMBRES
- Nombre de persona: busca cerca del numero de documento
- Razon social: busca cerca del NIT, incluye S.A.S, LTDA, etc.

### 3. OTROS DATOS
- Telefono, email, direccion
- Fechas importantes
- Tipo de solicitud

## FORMATO DE RESPUESTA (usa exactamente este formato):

DOCUMENTO: [solo digitos]
TIPO: [CC/NIT/CE/TI/PA]
NOMBRE: [nombre completo o razon social]
TELEFONO: [numero]
EMAIL: [correo]
SOLICITUD: [tipo de solicitud]
RESUMEN: [breve descripcion]

## EJEMPLOS:

Si el documento dice "JUAN PEREZ LOPEZ C.C. 80.123.456":
DOCUMENTO: 80123456
TIPO: CC
NOMBRE: Juan Perez Lopez

Si el documento dice "CONSTRUCTORA ABC S.A.S. NIT 900.123.456-7":
DOCUMENTO: 900123456
TIPO: NIT
NOMBRE: Constructora Abc S.A.S.

## IMPORTANTE
- SIEMPRE extrae el numero de documento si lo ves
- Si hay varios documentos, extrae TODOS
- No inventes datos que no estan en el documento"""


MODEL_DOCUMENTOS = "gemini-2.5-flash"
MODEL_CLASIFICACION = "gemini-2.5-pro"

class GeminiService:
    """Cliente para Google Gemini"""

    def __init__(self):
        #self.client = genai.Client() cambio davis 25-06-26
        self.client = genai.Client(
            vertexai=True,
            project=settings.GCP_PROJECT,
            location=settings.GCP_LOCATION,
        )
        self.model_documentos = MODEL_DOCUMENTOS
        self.model_clasificacion = MODEL_CLASIFICACION

    @gemini_retry
    async def _generate(self, *, model: str, contents, config=None):
        """Wrapper con reintentos exponenciales sobre errores transitorios."""
        return await self.client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

    async def analyze_document(self, file_content: bytes, mime_type: str) -> Optional[str]:
        """Analiza un documento con Gemini"""
        try:
            file_part = types.Part.from_bytes(data=file_content, mime_type=mime_type)
            response = await self._generate(
                model=self.model_clasificacion,
                contents=[file_part, DOCUMENT_ANALYSIS_PROMPT],
            )
            return response.text
        except Exception as e:
            logger.error("[GEMINI] Error analizando documento: %s", e)
            return None

    #async def analyze_image(self, image_content: bytes, mime_type: str) -> Optional[str]:
        #"""Analiza una imagen con Gemini"""
        #try:
        #    file_part = types.Part.from_bytes(data=image_content, mime_type=mime_type)
        #    response = await self._generate(
        #        model=self.model_documentos,
        #        contents=[file_part, DOCUMENT_ANALYSIS_PROMPT],
        #    )
        #    return response.text
        #except Exception as e:
            #logger.error("[GEMINI] Error analizando imagen: %s", e)
            #return None

    async def analyze_image(self, image_content: bytes, mime_type: str) -> Optional[str]:
        """Analiza una imagen con Gemini"""
        try:
            if mime_type == "application/octet-stream":
                logger.warning("[GEMINI] Archivo omitido por MIME no soportado")
                return None

            file_part = types.Part.from_bytes(data=image_content, mime_type=mime_type)
            response = await self._generate(
                model=self.model_documentos,
                contents=[file_part, DOCUMENT_ANALYSIS_PROMPT],
            )
            return response.text
        except Exception as e:
            logger.error("[GEMINI] Error analizando imagen: %s", e)
            return None

    async def analyze_text(self, prompt: str, use_clasificacion: bool = False) -> Optional[str]:
        """Clasifica o analiza texto plano con Gemini"""
        try:
            model = self.model_clasificacion if use_clasificacion else self.model_documentos
            response = await self._generate(model=model, contents=prompt)
            return response.text.strip() if response.text else ""
        except Exception as e:
            logger.error("[GEMINI] Error analizando texto: %s", e)
            return None

    def is_valid_analysis(self, text: str) -> bool:
        """Verifica si el analisis tiene contenido valido"""
        return bool(text and text.strip() and text != "SIN INFORMACION")
