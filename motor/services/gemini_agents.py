"""
Agentes de IA para extraccion de datos
Basado en el workflow n8n: RADICAR PQRD CORREO UNO V6
"""
import asyncio
import logging
from typing import List, Dict
from google import genai
from google.genai import types
from config import settings
from models.enums import TipoPqrd
from models.radicacion import ExtractedData
from services.gemini_retry import gemini_retry
from services.prompts import PROMPTS, GEMINI_MODEL, GEMINI_MODEL_CLASIFICACION, GEMINI_MODEL_EXTRACCION
from utils.documento_candidatos import extraer_candidatos_documento

log = logging.getLogger(__name__)


def _formatear_candidatos(candidatos: List[Dict]) -> str:
    """Formatea la lista de candidatos de documento para el prompt del LLM."""
    lineas = ["CANDIDATOS (elige UNO por su numero, no inventes):"]
    for i, c in enumerate(candidatos, 1):
        marcas = []
        if c.get("tiene_cc"):
            marcas.append("cc")
        if c.get("tiene_nit"):
            marcas.append("nit")
        if c.get("tiene_ti"):
            marcas.append("ti")
        if c.get("tiene_ce"):
            marcas.append("ce")
        marca_str = ",".join(marcas) if marcas else "sin_marca"
        lineas.append(
            f"{i}. {c['numero']} | marca: {marca_str} | contexto: \"{c['contexto']}\""
        )
    return "\n".join(lineas)


def _mejor_candidato_afectado(candidatos: List[Dict]) -> str:
    """Heuristica determinista: mejor candidato a documento del afectado.

    Se usa como red de seguridad si el LLM devuelve algo fuera de la lista.
    El afectado casi siempre es persona natural -> prioriza CC no-NIT.
    """
    if not candidatos:
        return ""
    ordenados = sorted(
        candidatos,
        key=lambda c: (
            0 if (c["tiene_cc"] and not c["tiene_nit"]) else
            1 if not c["tiene_nit"] else
            2  # NIT al final (afectado rara vez es empresa)
        ),
    )
    return ordenados[0]["numero"]


def _mejor_candidato_remitente(candidatos: List[Dict], tipo_remitente: int) -> str:
    """Heuristica determinista: mejor candidato a documento del remitente."""
    if not candidatos:
        return ""
    if tipo_remitente == 2:
        # Tercero/empresa -> prioriza NIT
        ordenados = sorted(candidatos, key=lambda c: 0 if c["tiene_nit"] else 1)
    else:
        # Persona -> prioriza CC no-NIT
        ordenados = sorted(
            candidatos, key=lambda c: 0 if (c["tiene_cc"] and not c["tiene_nit"]) else 1
        )
    return ordenados[0]["numero"]


class LangChainAgents:
    """Agentes de IA para extraccion de datos usando Google Gemini"""

    def __init__(self):
        self.client = genai.Client(
            vertexai=True,
            project=settings.GCP_PROJECT,
            location=settings.GCP_LOCATION,
        )
        self._default_config = types.GenerateContentConfig(temperature=0.1)
        self.model_default = GEMINI_MODEL
        self.model_clasificacion = GEMINI_MODEL_CLASIFICACION
        self.model_extraccion = GEMINI_MODEL_EXTRACCION

    def close(self) -> None:
        """Cierra el cliente Gemini"""
        self.client = None

    @gemini_retry
    async def _generate(self, *, model: str, contents, config=None):
        """Wrapper con reintentos exponenciales sobre errores transitorios."""
        return await self.client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config or self._default_config,
        )

    async def _run_agent(self, system_prompt: str, user_message: str, clasificacion: bool = False, extraccion: bool = False) -> str:
        """Ejecuta un agente con prompt de sistema"""
        try:
            if extraccion:
                model = self.model_extraccion
            elif clasificacion:
                model = self.model_clasificacion
            else:
                model = self.model_default
            response = await self._generate(
                model=model,
                contents=f"{system_prompt}\n\n---\n\n{user_message}",
            )
            return response.text.strip() if response.text else ""
        except Exception as e:
            log.warning("Error en agente Gemini: %s", e)
            return ""

    async def extract_tipo_remitente(self, subject: str, body: str, attachment_text: str) -> int:
        """Extrae el tipo de remitente (1=Familiar, 2=Tercero, 3=Afectado)"""
        user_msg = f"Subject: {subject}\n\nBody: {body}\n\nArchivos adjuntos: {attachment_text}"
        result = await self._run_agent(PROMPTS["TIPO_REMITENTE"], user_msg)
        try:
            val = int(result)
            return val if val in [1, 2, 3] else 3
        except:
            return 3

    async def extract_numero_documento_afectado(self, subject: str, body: str, attachment_text: str) -> str:
        """Extrae el documento del afectado eligiendo entre CANDIDATOS deterministas.

        1. Un regex extrae todos los numeros que podrian ser documento (ya limpios,
           sin celulares/fechas/radicados). Si no hay candidatos -> "" (no encontrado
           REAL, no alucinado).
        2. Si hay un solo candidato, se usa directo (nada que elegir).
        3. Si hay varios, el LLM ELIGE cual corresponde al afectado. Si devuelve algo
           fuera de la lista, cae a la heuristica determinista.
        """
        texto = f"{subject}\n{body}\n{attachment_text}"
        candidatos = extraer_candidatos_documento(texto)

        if not candidatos:
            return ""
        if len(candidatos) == 1:
            return candidatos[0]["numero"]

        numeros_validos = {c["numero"] for c in candidatos}
        user_msg = (
            f"Subject: {subject}\n\nBody: {body}\n\nArchivos adjuntos: {attachment_text}"
            f"\n\n{_formatear_candidatos(candidatos)}"
        )
        result = await self._run_agent(PROMPTS["ELEGIR_DOCUMENTO_AFECTADO"], user_msg, extraccion=True)

        if "no encontrado" in result.lower():
            return ""
        elegido = "".join(c for c in result if c.isdigit())
        if elegido in numeros_validos:
            return elegido
        # El LLM devolvio algo fuera de la lista: red de seguridad determinista
        log.warning(
            "AFECTADO: LLM eligio '%s' fuera de candidatos %s. Usando heuristica.",
            elegido or "VACIO", sorted(numeros_validos),
        )
        return _mejor_candidato_afectado(candidatos)

    async def extract_tipo_documento_afectado(self, subject: str, body: str, attachment_text: str) -> int:
        """Extrae el tipo de documento del afectado"""
        user_msg = f"Subject: {subject}\n\nBody: {body}\n\nArchivos adjuntos: {attachment_text}"
        result = await self._run_agent(PROMPTS["TIPO_DOCUMENTO_AFECTADO"], user_msg)
        try:
            val = int(result)
            return val if val in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] else 1
        except:
            return 1

    async def extract_sexo_afectado(self, nombre1: str, nombre2: str = "", genero: str = "") -> int:
        """Determina el sexo del afectado a partir del texto del correo."""
        user_msg = f"{nombre1} {nombre2}"
        if genero:
            # Agregar genero al prompt si viene de la base de datos
            prompt = PROMPTS["SEXO_AFECTADO"] + f"\nTambien si el campo genero trae datos: {genero} para tu decision."
        else:
            prompt = PROMPTS["SEXO_AFECTADO"]
        result = await self._run_agent(prompt, user_msg)
        try:
            val = int(result)
            return val if val in [1, 2, 3] else 1
        except:
            return 1

    async def extract_nombre_afectado(self, subject: str, body: str, attachment_text: str) -> str:
        """Extrae el nombre completo del afectado del correo"""
        user_msg = f"Subject: {subject}\n\nBody: {body}\n\nArchivos adjuntos: {attachment_text}"
        result = await self._run_agent(PROMPTS["NOMBRE_AFECTADO"], user_msg)
        if "no encontrado" in result.lower():
            return ""
        # Limpiar y formatear
        nombre = result.strip()
        # Convertir a titulo si está en mayúsculas
        if nombre.isupper():
            nombre = nombre.title()
        return nombre

    async def extract_nombre_remitente(self, subject: str, body: str, attachment_text: str) -> str:
        """Extrae el nombre de QUIEN ESCRIBE el correo (remitente, no afectado).

        Se invoca solo cuando el encabezado 'De:' no trae un nombre usable
        (buzon sin display name: Graph devuelve la propia direccion de correo).
        Devuelve cadena vacia si no logra identificarlo, para que el
        orquestador mande el correo a revision manual en vez de radicar la
        direccion de correo como nombre del remitente.
        """
        user_msg = f"Subject: {subject}\n\nBody: {body}\n\nArchivos adjuntos: {attachment_text}"
        result = await self._run_agent(PROMPTS["NOMBRE_REMITENTE"], user_msg)
        if not result or "no encontrado" in result.lower():
            return ""
        # Limpiar y formatear
        nombre = result.strip()
        # Convertir a titulo si está en mayúsculas
        if nombre.isupper():
            nombre = nombre.title()
        return nombre

    async def extract_tipo_pqrd(self, subject: str, body: str, attachment_text: str) -> int:
        """Tipo de PQRD: Solicitud, el unico tipo con el que este canal radica."""
        return int(TipoPqrd.SOLICITUD)

    async def extract_tipo_solicitud(self, subject: str, body: str, attachment_text: str) -> int:
        """Extrae el tipo de solicitud (27 categorias)"""
        user_msg = f"Subject: {subject}\n\nBody: {body}\n\nArchivos adjuntos: {attachment_text}"
        result = await self._run_agent(PROMPTS["TIPO_SOLICITUD"], user_msg, clasificacion=True)
        if "no coincide" in result.lower():
            return 14  # Otra
        try:
            val = int(result)
            valid_ids = [1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28]
            return val if val in valid_ids else 14
        except (ValueError, TypeError):
            return 14

    async def extract_numero_documento_remitente(
        self,
        tipo_remitente: int,
        subject: str,
        body: str,
        attachment_text: str,
        numero_documento_afectado: str = ""
    ) -> str:
        """Extrae el documento del remitente eligiendo entre CANDIDATOS deterministas."""
        texto = f"{subject}\n{body}\n{attachment_text}"
        candidatos = extraer_candidatos_documento(texto)

        cleaned = ""
        if candidatos:
            if len(candidatos) == 1:
                cleaned = candidatos[0]["numero"]
            else:
                numeros_validos = {c["numero"] for c in candidatos}
                user_msg = (
                    f"tipoRemitente: {tipo_remitente}\n\nSubject: {subject}\n\nBody: {body}"
                    f"\n\nArchivos adjuntos: {attachment_text}\n\n{_formatear_candidatos(candidatos)}"
                )
                result = await self._run_agent(PROMPTS["ELEGIR_DOCUMENTO_REMITENTE"], user_msg)
                elegido = "".join(c for c in result if c.isdigit())
                if "no encontrado" in result.lower():
                    cleaned = ""
                elif elegido in numeros_validos:
                    cleaned = elegido
                else:
                    log.warning(
                        "REMITENTE: LLM eligio '%s' fuera de candidatos %s. Usando heuristica.",
                        elegido or "VACIO", sorted(numeros_validos),
                    )
                    cleaned = _mejor_candidato_remitente(candidatos, tipo_remitente)

        # Si no se encontro, usar el del afectado como fallback
        if not cleaned and numero_documento_afectado:
            log.warning(
                "FALLBACK: No se encontro documento del remitente, usando documento del afectado: %s",
                numero_documento_afectado
            )
            return numero_documento_afectado

        return cleaned

    async def extract_tipo_documento_remitente(
        self,
        tipo_remitente: int,
        subject: str,
        body: str,
        attachment_text: str
    ) -> int:
        """Extrae el tipo de documento del remitente"""
        user_msg = f"tipoRemitente: {tipo_remitente}\n\nSubject: {subject}\n\nBody: {body}\n\nArchivos adjuntos: {attachment_text}"
        result = await self._run_agent(PROMPTS["TIPO_DOCUMENTO_REMITENTE"], user_msg)

        # Si es tercero, por defecto es NIT (10)
        if tipo_remitente == 2:
            default = 10
        else:
            default = 1

        try:
            val = int(result)
            return val if val in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] else default
        except:
            return default

    async def extract_all_data(self, subject: str, body: str, attachment_text: str) -> ExtractedData:
        """Extrae todos los datos en paralelo (primera fase)"""
        # Primera fase: extraccion en paralelo de datos del afectado
        results = await asyncio.gather(
            self.extract_tipo_remitente(subject, body, attachment_text),
            self.extract_numero_documento_afectado(subject, body, attachment_text),
            self.extract_tipo_documento_afectado(subject, body, attachment_text),
            self.extract_tipo_solicitud(subject, body, attachment_text),
        )

        tipo_remitente = results[0]
        numero_documento_afectado = results[1]

        # Segunda fase: datos del remitente (depende del tipo_remitente)
        numero_documento_remitente, tipo_documento_remitente = await asyncio.gather(
            self.extract_numero_documento_remitente(
                tipo_remitente, subject, body, attachment_text, numero_documento_afectado
            ),
            self.extract_tipo_documento_remitente(
                tipo_remitente, subject, body, attachment_text
            ),
        )

        tipo_documento_afectado = results[2]

        # --- VALIDACION CRUZADA ---

        # Si remitente es Tercero (empresa) pero tipo_documento quedo como CC,
        # corregir a NIT porque las empresas siempre tienen NIT
        if tipo_remitente == 2 and tipo_documento_remitente == 1:
            log.warning(
                "CORRECCION: tipo_remitente=2 (Tercero) pero tipo_doc_remitente=1 (CC). Corrigiendo a 10 (NIT)."
            )
            tipo_documento_remitente = 10

        # Si afectado tiene NIT pero NO es un caso empresarial (tipos 8,26 = estado cuenta/aportes),
        # el afectado es persona, no empresa — corregir a CC
        if tipo_documento_afectado == 10 and results[3] not in [8, 26]:
            log.warning(
                "CORRECCION: tipo_doc_afectado=10 (NIT) pero tipo_solicitud=%d no es empresarial. Corrigiendo a 1 (CC).",
                results[3]
            )
            tipo_documento_afectado = 1

        # Si documento remitente = documento afectado y remitente es Tercero,
        # se uso el fallback — loguear para trazabilidad
        if (tipo_remitente == 2
                and numero_documento_remitente == numero_documento_afectado
                and numero_documento_remitente):
            log.warning(
                "ATENCION: Remitente Tercero tiene mismo documento que afectado (%s). Posible fallback.",
                numero_documento_remitente
            )

        return ExtractedData(
            tipo_remitente=tipo_remitente,
            numero_documento_afectado=numero_documento_afectado,
            tipo_documento_afectado=tipo_documento_afectado,
            tipo_pqrd=await self.extract_tipo_pqrd(subject, body, attachment_text),
            tipo_solicitud=results[3],
            numero_documento_remitente=numero_documento_remitente,
            tipo_documento_remitente=tipo_documento_remitente,
        )
