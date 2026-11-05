"""
Clasificador de remitentes para determinar si un correo debe derivarse
a Medicina Laboral o procesarse como PQRD normal.

Regla de negocio:
- Si el remitente ES una entidad de salud (EPS, IPS, Junta de Calificación,
  Fondo de Pensiones) → derivar a solicitudesmedicinalaboral@positiva.gov.co
- Si el remitente es el afectado, su empleador u otra empresa NO de salud → radicar como PQRD
"""

from dataclasses import dataclass
from typing import Optional


DESTINO_MEDICINA_LABORAL = "solicitudesmedicinalaboral@positiva.gov.co"
DESTINO_NOTIFICACIONES_JUDICIALES = "notificacionesjudiciales@positiva.gov.co"

# Keywords deterministicos para detectar correos judiciales (tutelas, fallos, etc.)
# Se aplican con word boundaries (regex) para evitar falsos positivos como
# "tutelar"/"tutelaba" o "notifica automaticamente". Si alguno aparece en asunto
# o cuerpo, se reenvia directo a notificaciones judiciales sin pasar por Gemini.
JUDICIAL_KEYWORD_PATTERNS = (
    r"accion de tutela",
    r"tutela\b",                 # palabra exacta "tutela" (no "tutelar", "tutelaba")
    r"tutelas\b",
    r"fallo de tutela",
    r"fallo tutela",
    r"sentencia de tutela",
    r"sentencia tutela",
    r"auto admite tutela",
    r"admite tutela",
    r"auto admisorio",
    r"auto interlocutorio",
    r"juzgado\b",
    r"tribunal superior",
    r"tribunal contencioso",
    r"tribunal administrativo",
    r"habeas corpus",
    r"incidente de desacato",
)


def _normalizar(texto: str) -> str:
    """Normaliza texto para match determinístico (lower, sin tildes)."""
    import unicodedata
    if not texto:
        return ""
    nfd = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _es_judicial_por_keywords(subject: str, body: str) -> Optional[str]:
    """Si el correo contiene keywords judiciales, retorna el patrón detectado.

    Usa regex con word boundaries para evitar falsos positivos.
    """
    import re

    asunto_norm = _normalizar(subject or "")
    cuerpo_norm = _normalizar((body or "")[:3000])
    for pattern in JUDICIAL_KEYWORD_PATTERNS:
        regex = re.compile(r"\b" + pattern, re.IGNORECASE)
        if regex.search(asunto_norm) or regex.search(cuerpo_norm):
            return pattern.replace(r"\b", "")
    return None


@dataclass
class ClasificacionRemitenteResult:
    """Resultado de la clasificación del tipo de remitente"""
    derivar: bool                          # True = enviar a ML, False = procesar como PQRD
    judicial: bool = False                 # True = enviar a notificaciones judiciales
    razon: str = ""
    destino: Optional[str] = None          # Email destino si derivar=True o judicial=True


class ClasificadorRemitente:
    """
    Clasifica si el remitente de un correo es una entidad de salud.

    Usa Gemini para analizar el nombre y dominio del remitente en contexto
    del asunto del correo y determinar si corresponde a una entidad que
    presta servicios de salud (EPS, IPS, Junta de Calificación, Fondo de Pensiones).
    """

    PROMPT_SISTEMA = """Eres un clasificador de correos para Positiva ARL Colombia.

Tu única tarea es determinar si el correo debe derivarse a Medicina Laboral,
reenviarse a Notificaciones Judiciales, o procesarse como PQRD normal en Servicio al Cliente.

REGLA DE ORO: Cuando tengas duda, responde RADICAR.
Solo derivas cuando el trámite es CLARAMENTE médico-laboral.
Solo envías a JUDICIAL cuando es CLARAMENTE una tutela, acción judicial u orden de juzgado.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASO 1 — ¿SE QUEDA EN PQRD? (RADICAR)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Si el correo corresponde a CUALQUIERA de los siguientes tipos de solicitud,
responde RADICAR inmediatamente sin analizar nada más:

DERECHOS DE PETICIÓN Y QUEJAS
- El correo menciona "derecho de petición", "derecho de peticion", "D.P.", "DP"
- Quejas, reclamos o sugerencias sobre la atención recibida
- Quejas sobre el call center o atención telefónica
- Quejas sobre atención en oficina física o portal web
- Los derechos de petición SIEMPRE se quedan en PQRD (servicio al cliente), nunca se derivan a Medicina Laboral

AFILIACIÓN Y NOVEDADES
- Actualización de datos personales o de empresa en la ARL
- Solicitud de certificados de afiliación
- Retiro, corrección de nombre/documento o cambio de nivel de riesgo de un trabajador
- Regularización de aportes sin afiliación previa
- Preguntas sobre coberturas de afiliación

APORTES Y CARTERA
- Estado de cuenta de aportes ARL, paz y salvo, deuda pendiente
- Problemas con pagos realizados: datos incorrectos, pagos duplicados, devoluciones
- Cobros coactivos sobre aportes ARL
- Solicitudes de ACCAI: certificaciones o consultas sobre el componente de ahorro

INCAPACIDADES Y REEMBOLSOS
- Pago de incapacidad por accidente de trabajo o enfermedad laboral
- Objeción, rechazo, controversia o reclamo sobre el PAGO o RECONOCIMIENTO de una incapacidad
  (aunque adjunten historia clínica, epicrisis o soportes médicos como prueba del reclamo:
  el trámite es sobre el PAGO, no una calificación médico-laboral → se queda en PQRD)
- Consultas sobre IBC, liquidación o soportes de incapacidad
- Reembolso de gastos médicos que el trabajador ya pagó de su bolsillo
- Reembolso de transporte a terapias o consultas

AUTORIZACIÓN DE SERVICIOS MÉDICOS
- Autorización de citas, medicamentos, terapias, traslados o prótesis
- Solicitud de copia de una autorización existente
- Trámite de calificación de PCL (cuando lo pide el trabajador o la empresa directamente)
- Correos de EPS, IPS o Juntas pidiendo autorización de servicios

MEDICAMENTOS Y TRASLADOS
- Problemas con entrega de medicamentos ya autorizados (demora, incompleto)
- Problemas con logística de traslado médico ya programado

PÓLIZAS DE VIDA
- Facturación, cobros o pagos de póliza de vida
- Modificación de beneficiarios o suma asegurada
- Renovación de póliza de vida
- Suscripción o contratación de nueva póliza de vida
- Reclamación de póliza por muerte, invalidez, incapacidad prolongada o desempleo

PENSIONES
- Mesada de pensión de invalidez o sobrevivencia
- Desprendibles, certificados, descuentos o alivios tributarios de pensión
- Acceso a plataforma de pensionados
- Certificados de estudio de beneficiarios de pensión de sobreviviente

ASESORÍA EMPRESARIAL (SGSST)
- Asesoría para implementar el sistema de gestión de seguridad y salud en el trabajo
- Capacitaciones, visitas, estudios de puesto de trabajo
- Medición de estándares mínimos, COPASST
- Certificación de accidentabilidad

CUENTAS MÉDICAS
- Facturas pendientes de pago a ESE, IPS u hospitales por procedimientos médicos

TECNOLOGÍA Y CANALES
- Problemas técnicos con el portal web, página caída, error de acceso
- Problemas con aplicaciones móviles o IVR

OTROS
- El trabajador afectado escribiendo directamente sobre su caso
- La empresa empleadora del trabajador (sector diferente a salud)
- Un abogado o apoderado gestionando un trámite
- Correos sin ningún trámite médico-laboral identificable
- Cualquier correo con duda: RADICAR

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASO 2 — ¿VA A MEDICINA LABORAL? (DERIVAR)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Solo si NO aplica ningún caso del Paso 1, evalúa si DERIVAR.

DERIVAR automático (sin análisis adicional):
- El correo del remitente termina en @e-entrega.co
- El asunto o cuerpo contiene "JUNTA", "JUNTA REGIONAL", "JRCI" o "JNCI"

DERIVAR si el trámite es CLARAMENTE médico-laboral y no es un derecho de petición:
- Envío de historia clínica (cuando la persona tiene la intencion de enviar pruebas de su caso a Medicina Laboral, no cuando la persona pide su historia clínica)
, adición de diagnóstico, DE PRUEBAS DEL CASO O ENFERMEDAD que se le soliciten, exámenes médicos o dictámenes
  EXCEPCIÓN: si el correo es una objeción, rechazo o reclamo sobre el PAGO/RECONOCIMIENTO de
  una incapacidad, prestación o servicio ya negado, se queda en PQRD (RADICAR) aunque adjunte
  historia clínica; la historia clínica ahí es solo el soporte del reclamo, no un trámite médico-laboral
- Controversia sobre el origen de un accidente o enfermedad laboral
- Solicitud o apelación de porcentaje de PCL enviada por entidad de salud
- Solicitud de expedientes clínicos o adición de diagnósticos
- Determinación de origen laboral tramitada por EPS o IPS
- Calificaciones PCL: inicial, recalificación o integral gestionadas por entidad
- Pagos de honorarios a Juntas de Calificación
- Conceptos de rehabilitación emitidos por entidad de salud
- Notificación de origen o calificación de siniestro mortal

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASO 3 — ¿VA A NOTIFICACIONES JUDICIALES? (JUDICIAL)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Solo si NO aplica Paso 1 ni Paso 2, evalúa si es un asunto judicial directo.

JUDICIAL si el correo es CLARAMENTE un requerimiento legal directo:
- Tutelas o acciones de tutela
- Acciones judiciales, demandas, fallos judiciales
- Órdenes de juzgado o tribunal
- Notificaciones de sentencias judiciales
- Correos de juzgados, tribunales o despachos judiciales

NO es JUDICIAL (se queda en RADICAR):
- Derechos de petición → se quedan en PQRD (servicio al cliente)
- Quejas o reclamos → se quedan en PQRD (servicio al cliente)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESPONDE ÚNICAMENTE con una de estas tres palabras:
DERIVAR
RADICAR
JUDICIAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    def __init__(self, gemini_service):
        self.gemini = gemini_service

    async def clasificar(
        self,
        from_name: str,
        from_email: str,
        subject: str,
        body: str,
    ) -> ClasificacionRemitenteResult:
        """
        Determina si el remitente es una entidad de salud.

        Args:
            from_name: Nombre del remitente (campo From)
            from_email: Correo del remitente
            subject: Asunto del correo
            body: Cuerpo del correo (texto limpio, primeros 1000 chars)

        Returns:
            ClasificacionRemitenteResult indicando si derivar o radicar
        """
        # Check determinístico antes de IA
        if from_email and from_email.lower().endswith("@e-entrega.co"):
            return ClasificacionRemitenteResult(
                derivar=True,
                razon=f"Dominio @e-entrega.co detectado: {from_name} <{from_email}>",
                destino=DESTINO_MEDICINA_LABORAL,
            )

        # Check determinístico judicial: tutelas, fallos, sentencias, juzgados
        kw_judicial = _es_judicial_por_keywords(subject, body)
        if kw_judicial:
            return ClasificacionRemitenteResult(
                derivar=False,
                judicial=True,
                razon=f"Keyword judicial detectado: '{kw_judicial}' en asunto/cuerpo",
                destino=DESTINO_NOTIFICACIONES_JUDICIALES,
            )

        prompt = f"""{self.PROMPT_SISTEMA}

DATOS DEL REMITENTE:
Nombre: {from_name or '(sin nombre)'}
Correo: {from_email or '(sin correo)'}

ASUNTO DEL CORREO:
{subject or '(sin asunto)'}

FRAGMENTO DEL CUERPO (contexto adicional):
{(body or '')[:5000]}

CLASIFICACIÓN:"""

        try:
            respuesta = await self.gemini.analyze_text(prompt, use_clasificacion=True)
            categoria = respuesta.strip().upper()

            if "DERIVAR" in categoria:
                return ClasificacionRemitenteResult(
                    derivar=True,
                    razon=f"Remitente identificado como entidad de salud: {from_name} <{from_email}>",
                    destino=DESTINO_MEDICINA_LABORAL,
                )
            elif "JUDICIAL" in categoria:
                return ClasificacionRemitenteResult(
                    derivar=False,
                    judicial=True,
                    razon=f"Asunto judicial directo (tutela/acción judicial/orden de juzgado): {from_name} <{from_email}>",
                    destino=DESTINO_NOTIFICACIONES_JUDICIALES,
                )
            else:
                return ClasificacionRemitenteResult(
                    derivar=False,
                    razon="Remitente no es entidad de salud, procesar como PQRD",
                )

        except Exception as e:
            # Ante error, no derivar (mejor radicar que perder el correo)
            return ClasificacionRemitenteResult(
                derivar=False,
                razon=f"Error en clasificación ({e}), procesando como PQRD por seguridad",
            )
