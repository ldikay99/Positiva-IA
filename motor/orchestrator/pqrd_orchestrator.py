"""
Orquestador principal del proceso de radicacion PQRD
"""

import time
import base64
import os
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from datetime import datetime

from config import settings
from models.email import EmailMessage
from models.persona import PersonaData
from models.radicacion import RadicacionPayload, TrazabilidadPayload, AsignacionPayload
from models.enums import (
    get_tipo_documento_id,
    get_tipo_solicitud_nombre,
    get_tipo_documento_codigo,
)

from services.outlook import OutlookService
from services.redis_client import RedisService
from services.gemini import GeminiService
from services.document_extractor import DocumentExtractorService
from services.gemini_agents import LangChainAgents
from services.radicacion import RadicacionService
from services.supabase_client import SupabaseService
from services.cloud_storage import CloudStorageDownloader, extraer_urls_cloud
from services.asignacion_ia_client import AsignacionIAClient

from business_rules import (
    get_office_id,
    get_office_name,
    ClasificadorRemitente,
)

from utils.hash import generate_email_hash, extraer_remitente_original
from utils.contacto_extractor import mejor_telefono
from utils.html_cleaner import clean_html_content, escape_html
from utils.url_extractor import extract_document_urls
from utils.p7m_decoder import extract_urls_from_p7m, is_p7m_file
from utils.generic_filter import (
    is_generic_identity,
    extraer_correo_remitente_real,
    extraer_nombre_remitente_real,
    es_nombre_persona_valido,
    is_buzon_no_humano,
    tiene_remitente_externo_identificable,
)

from logging_system import RichLogger


@dataclass
class ProcessingResult:
    """Resultado del procesamiento de un correo"""

    success: bool = False
    email_id: str = ""
    subject: str = ""
    radicado: str = ""
    error: str = ""
    action: str = "error"  # 'radicado', 'duplicado', 'manual', 'error_radicar'
    duration_ms: int = 0
    extracted_data: dict = field(default_factory=dict)


class PqrdOrchestrator:
    """Orquestador principal del proceso de radicacion PQRD"""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.outlook = OutlookService()
        self.redis = RedisService()
        self.gemini = GeminiService()
        self.document_extractor = DocumentExtractorService()
        self.langchain = LangChainAgents()
        self.radicacion = RadicacionService()
        self.supabase = SupabaseService()
        self.logger = RichLogger(verbose=verbose)
        self.clasificador_remitente = ClasificadorRemitente(self.gemini)
        self.cloud_storage = CloudStorageDownloader()
        self.asignacion_client = AsignacionIAClient(verbose=verbose)

    async def close(self) -> None:
        """Cierra todas las conexiones de los servicios"""
        # Cerrar Redis (async)
        if hasattr(self.redis, "close"):
            await self.redis.close()

        # Cerrar Supabase
        if hasattr(self.supabase, "close"):
            self.supabase.close()

        # Cerrar Gemini client
        if hasattr(self.langchain, "close"):
            self.langchain.close()

        # Cerrar clientes HTTP compartidos
        if hasattr(self, "outlook") and hasattr(self.outlook, "close"):
            await self.outlook.close()
        if hasattr(self, "radicacion_service") and hasattr(self.radicacion_service, "close"):
            await self.radicacion_service.close()
        if hasattr(self, "asignacion_client") and hasattr(self.asignacion_client, "close"):
            await self.asignacion_client.close()

    async def __aenter__(self):
        """Context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cierra conexiones"""
        await self.close()
        return False

    async def process_single_email(self, email_id: str) -> ProcessingResult:
        """Procesa un solo correo por ID con logs detallados"""
        start_time = time.time()
        result = ProcessingResult(email_id=email_id)

        self.logger.start(email_id)

        # El contexto se fija solo con email_id, antes de leer el correo, para
        # que las escrituras del manejo de error se atribuyan a este correo.
        if settings.MODO_TEST:
            from logging_system.test_recorder import get_recorder
            get_recorder().set_contexto(email_id=email_id)

        try:
            # 1. Obtener correo por ID y todos los correos del hilo
            self.logger.step_start("OUTLOOK", "Obteniendo correo de Outlook...")
            email = await self.outlook.get_email_by_id(email_id)
            result.subject = email.subject

            # MODO_TEST: enriquece el contexto con asunto/remitente ya que el correo se leyo
            if settings.MODO_TEST:
                from logging_system.test_recorder import get_recorder
                get_recorder().set_contexto(
                    email_id=email_id,
                    asunto=email.subject or "",
                    remitente=email.from_address.address if email.from_address else "",
                )

            # Obtener todos los correos del hilo
            hilo_ids = [email_id]
            hilo_emails = [email]
            if email.conversation_id:
                try:
                    otros_ids = await self.outlook.get_conversation_message_ids(
                        settings.OUTLOOK_FOLDER_ID, email.conversation_id
                    )
                    for oid in otros_ids:
                        if oid != email_id:
                            try:
                                otro_email = await self.outlook.get_email_by_id(oid)
                                hilo_ids.append(oid)
                                hilo_emails.append(otro_email)
                            except ValueError:
                                # Correo ya fue movido por otro worker, ignorar
                                pass
                except Exception:
                    pass

            self.logger.step_success(
                "Correo obtenido exitosamente",
                {
                    "from": email.from_address.address if email.from_address else "",
                    "hasAttachments": email.has_attachments,
                    "correosEnHilo": len(hilo_ids),
                },
            )
            self.logger.step_info(f"ASUNTO: {email.subject}")

            # 1.5. Verificar si el hilo ya fue radicado (busca etiqueta ENT en TODAS las carpetas)
            self.logger.step_start("HILO_CHECK", "Verificando si el hilo ya fue radicado...")
            try:
                if email.conversation_id:
                    etiqueta_ent = await self.outlook.hilo_ya_radicado(email.conversation_id)
                else:
                    # Sin conversationId, verificar solo las etiquetas locales
                    etiqueta_ent = next((e for e in email.categories if e.startswith("ENT")), None)

                # Fallback: si el ultimo remitente es interno @positiva.gov.co,
                # buscar por asunto normalizado en todo el buzon. Esto cubre el
                # caso de reenvios desde Notificaciones Judiciales / SAC donde
                # Outlook genera un nuevo conversationId.
                if not etiqueta_ent:
                    from_addr_lower = (
                        email.from_address.address.lower()
                        if email.from_address and email.from_address.address
                        else ""
                    )
                    if from_addr_lower.endswith("@positiva.gov.co"):
                        from utils.hash import limpiar_asunto
                        asunto_limpio = limpiar_asunto(email.subject or "")
                        if asunto_limpio:
                            etiqueta_ent = await self.outlook.buscar_etiqueta_ent_por_asunto(
                                asunto_limpio
                            )
                            if etiqueta_ent:
                                self.logger.step_info(
                                    f"ENT encontrado por asunto (reenvio interno): {etiqueta_ent}"
                                )

                if etiqueta_ent:
                    self.logger.step_warning(
                        f"Hilo ya radicado: {etiqueta_ent}. Etiquetando y moviendo a RADICACION_EXITOSA."
                    )
                    try:
                        await self.outlook.etiquetar_correo(email_id, etiqueta_ent)
                    except Exception as tag_err:
                        self.logger.step_warning(f"Error etiquetando reenvio (no critico): {tag_err}")
                    try:
                        await self._mover_hilo_completo(
                            email, email_id, settings.OUTLOOK_FOLDER_RADICACION_EXITOSA
                        )
                    except Exception as mv_err:
                        self.logger.step_warning(f"Error moviendo reenvio (no critico): {mv_err}")
                    result.radicado = etiqueta_ent
                    result.action = "duplicado"
                    result.success = True
                    result.duration_ms = int((time.time() - start_time) * 1000)
                    self.logger.finish_duplicate(result.duration_ms)
                    return result
                self.logger.step_success("Hilo no radicado previamente")
            except Exception as hilo_err:
                self.logger.step_warning(f"Error verificando hilo (continuando): {hilo_err}")

            # 2. Generar hash para deduplicacion (asunto_limpio + remitente_original)
            self.logger.step_start("DEDUPLICACION", "Generando hash del correo...")
            remitente_original = extraer_remitente_original(
                email.from_address.address if email.from_address else "",
                hilo_emails,
            )
            nombres_adjuntos = ""
            if email.has_attachments:
                try:
                    adjuntos_meta = await self.outlook.get_attachments(email_id)
                    nombres_adjuntos = "|".join(
                        sorted(a.get("name", "") for a in adjuntos_meta if a.get("name"))
                    )
                except Exception:
                    pass
            email_hash = generate_email_hash(email.subject, remitente_original, nombres_adjuntos)
            self.logger.step_success(
                "Hash generado",
                {
                    "hash": email_hash[:16] + "...",
                    "remitenteOriginal": remitente_original,
                    "adjuntos": len(nombres_adjuntos.split("|")) if nombres_adjuntos else 0,
                },
            )

            # 3. Verificar duplicado en SUPABASE (por asunto + remitente)
            self.logger.step_start(
                "SUPABASE_CHECK", "Verificando duplicado en Supabase..."
            )
            correo_remitente = email.from_address.address if email.from_address else ""
            if correo_remitente and email.subject:
                exists_in_supabase = await self.supabase.email_exists(
                    correo_remitente, email.subject
                )
                if exists_in_supabase:
                    self.logger.step_warning(
                        "Correo duplicado encontrado en Supabase (mismo asunto + remitente)"
                    )
                    await self.outlook.move_email(
                        email_id, settings.OUTLOOK_FOLDER_DUPLICADOS
                    )
                    result.action = "duplicado"
                    result.success = True
                    result.duration_ms = int((time.time() - start_time) * 1000)
                    self.logger.finish_duplicate(result.duration_ms)
                    return result
            self.logger.step_success("No encontrado en Supabase")

            # 4. Verificar duplicado en REDIS (por hash de contenido)
            self.logger.step_start("REDIS_CHECK", "Verificando duplicado en Redis...")
            exists_in_redis = await self.redis.email_hash_exists(email_hash)
            if exists_in_redis:
                self.logger.step_warning(
                    "Correo duplicado encontrado en Redis (mismo contenido)"
                )
                await self.outlook.move_email(
                    email_id, settings.OUTLOOK_FOLDER_DUPLICADOS
                )
                result.action = "duplicado"
                result.success = True
                result.duration_ms = int((time.time() - start_time) * 1000)
                self.logger.finish_duplicate(result.duration_ms)
                return result
            self.logger.step_success("No encontrado en Redis - Correo es nuevo")

            # 5. Limpiar contenido HTML (de TODO el hilo)
            self.logger.step_start("LIMPIEZA", "Limpiando contenido HTML...")
            # Concatenar bodies de todos los correos del hilo (mas reciente primero)
            all_bodies = [e.body.content for e in hilo_emails if e.body.content]
            combined_body = "\n\n--- Correo anterior del hilo ---\n\n".join(all_bodies)
            cleaned_body = clean_html_content(combined_body)
            self.logger.step_success(
                "Contenido limpiado",
                {
                    "originalLength": len(combined_body),
                    "cleanedLength": len(cleaned_body),
                    "correosEnHilo": len(hilo_emails),
                },
            )

            # Determinar correo efectivo del remitente.
            # Si el From es @positiva.gov.co (reenvío interno), busca el correo
            # real del ciudadano en el cuerpo del correo.
            from_email_raw = email.from_address.address if email.from_address else ""
            from_name_raw_for_filter = email.from_address.name if email.from_address else ""
            correo_remitente_efectivo = extraer_correo_remitente_real(
                from_email_raw, cleaned_body, from_name_raw_for_filter
            )
            if correo_remitente_efectivo != from_email_raw:
                self.logger.step_info(
                    f"Correo reenviado internamente. Remitente real detectado: {correo_remitente_efectivo}"
                )

            # Si el From es buzón interno/automatizado y no logramos identificar
            # un remitente externo válido en el hilo, no podemos radicar con
            # datos confiables del solicitante. Se mueve a REVISION_MANUAL.
            if is_buzon_no_humano(from_name_raw_for_filter, from_email_raw) and \
                    not tiene_remitente_externo_identificable(
                        from_name_raw_for_filter, from_email_raw, cleaned_body
                    ):
                self.logger.step_warning(
                    f"Remitente no humano sin externo identificable: "
                    f"'{from_name_raw_for_filter}' <{from_email_raw}>. "
                    f"Enviando a revision manual."
                )
                try:
                    await self.outlook.etiquetar_correo(email_id, "REMITENTE-NO-IDENTIFICABLE")
                except Exception:
                    pass
                try:
                    await self._mover_hilo_completo(
                        email, email_id, settings.OUTLOOK_FOLDER_REVISAR_MANUAL
                    )
                except Exception as move_err:
                    self.logger.step_warning(f"Error moviendo hilo: {move_err}")
                result.action = "manual"
                result.success = True
                result.duration_ms = int((time.time() - start_time) * 1000)
                self.logger.finish_success(
                    "Remitente no identificable → revision manual", result.duration_ms
                )
                return result

            # 5.5 Detectar facturas electronicas → enviar a revision manual
            es_factura = self._es_factura_electronica(
                from_email_raw, email.subject, cleaned_body
            )
            if es_factura:
                self.logger.step_warning(
                    "Correo es factura electronica. Enviando a revision manual."
                )
                try:
                    await self.outlook.etiquetar_correo(email_id, "FACTURA-ELECTRONICA")
                except Exception:
                    pass
                try:
                    await self._mover_hilo_completo(
                        email, email_id, settings.OUTLOOK_FOLDER_REVISAR_MANUAL
                    )
                except Exception as move_err:
                    self.logger.step_warning(f"Error moviendo factura: {move_err}")
                result.action = "manual"
                result.success = True
                result.duration_ms = int((time.time() - start_time) * 1000)
                self.logger.finish_success("Factura electronica → revision manual", result.duration_ms)
                return result

            # 6. Clasificar remitente (¿es entidad de salud → derivar a ML?)
            self.logger.step_start("CLASIFICACION_REMITENTE", "Clasificando tipo de remitente...")
            from_name = email.from_address.name if email.from_address else ""
            clasificacion = await self.clasificador_remitente.clasificar(
                from_name=from_name,
                from_email=correo_remitente_efectivo,
                subject=email.subject,
                body=cleaned_body,
            )
            if clasificacion.derivar:
                self.logger.step_warning(
                    f"Remitente es entidad de salud. Derivando a Medicina Laboral.",
                    {
                        "remitente": f"{from_name} <{correo_remitente_efectivo}>",
                        "destino": clasificacion.destino,
                        "razon": clasificacion.razon,
                    },
                )
                comentario = self._build_forward_comment(email)
                try:
                    await self.outlook.forward_email(email_id, clasificacion.destino, comentario)
                except Exception as e_fwd:
                    self.logger.step_warning(f"Reenvio fallido (no critico): {e_fwd}")
                try:
                    await self.outlook.move_email(email_id, settings.OUTLOOK_FOLDER_REENVIADO_OTRA_AREA)
                except Exception as e_mv:
                    self.logger.step_warning(f"Mover a REENVIADO fallido: {e_mv}")
                await self.redis.save_email_hash(email_hash)
                result.action = "reenviado"
                result.success = True
                result.duration_ms = int((time.time() - start_time) * 1000)
                self.logger.finish_success(f"Derivado a {clasificacion.destino}", result.duration_ms)
                return result

            if clasificacion.judicial:
                self.logger.step_warning(
                    f"Asunto judicial directo. Reenviando a Notificaciones Judiciales.",
                    {
                        "remitente": f"{from_name} <{correo_remitente_efectivo}>",
                        "destino": clasificacion.destino,
                        "razon": clasificacion.razon,
                    },
                )
                comentario = self._build_forward_comment(email)
                try:
                    await self.outlook.forward_email(email_id, clasificacion.destino, comentario)
                except Exception as e_fwd:
                    self.logger.step_warning(f"Reenvio judicial fallido (no critico): {e_fwd}")
                try:
                    await self.outlook.move_email(email_id, settings.OUTLOOK_FOLDER_REENVIADO_OTRA_AREA)
                except Exception as e_mv:
                    self.logger.step_warning(f"Mover a REENVIADO fallido: {e_mv}")
                await self.redis.save_email_hash(email_hash)
                result.action = "reenviado_judicial"
                result.success = True
                result.duration_ms = int((time.time() - start_time) * 1000)
                self.logger.finish_success(f"Reenviado a {clasificacion.destino}", result.duration_ms)
                return result

            self.logger.step_success("Remitente válido para PQRD, continuando pipeline...")

            # 8. Procesar adjuntos de TODO el hilo de conversacion
            extracted_text = ""
            attachment_files: List[Tuple[str, bytes]] = []

            # Siempre intentar obtener adjuntos (imágenes inline no activan hasAttachments)
            has_any_attachments = True
            if has_any_attachments:
                if len(hilo_ids) > 1:
                    self.logger.step_start("ADJUNTOS", f"Procesando adjuntos de {len(hilo_ids)} correos del hilo...")
                else:
                    self.logger.step_start("ADJUNTOS", "Procesando archivos adjuntos...")

                # Procesar adjuntos de cada correo del hilo (deduplicar por nombre)
                all_texts = []
                nombres_adjuntos_vistos: set = set()
                for idx, msg_id in enumerate(hilo_ids):
                    try:
                        texto, archivos = await self._process_attachments(msg_id)
                        if texto:
                            all_texts.append(texto)
                        if archivos:
                            nuevos = 0
                            for nombre, contenido in archivos:
                                if nombre.lower() not in nombres_adjuntos_vistos:
                                    nombres_adjuntos_vistos.add(nombre.lower())
                                    attachment_files.append((nombre, contenido))
                                    nuevos += 1
                            if len(hilo_ids) > 1:
                                self.logger.step_info(
                                    f"Correo {idx+1}/{len(hilo_ids)}: {len(archivos)} adjuntos ({nuevos} nuevos, {len(archivos)-nuevos} duplicados omitidos)"
                                )
                    except Exception as att_err:
                        self.logger.step_warning(f"Error adjuntos correo {idx+1}: {att_err}")

                if all_texts:
                    extracted_text = self.document_extractor.unify_analysis_results(all_texts)

                self.logger.step_success(
                    "Adjuntos procesados",
                    {
                        "filesCount": len(attachment_files),
                        "extractedTextLength": len(extracted_text),
                        "correosDelHilo": len(hilo_ids),
                    },
                )

            # Buscar links de Google Drive / OneDrive en el cuerpo (siempre, tenga o no adjuntos)
            cloud_urls = extraer_urls_cloud(cleaned_body)
            if cloud_urls:
                self.logger.step_start(
                    "CLOUD_DOCS",
                    f"Encontrados {len(cloud_urls)} links de Drive/OneDrive, descargando...",
                )
                cloud_texts = []
                for url in cloud_urls:
                    resultado = await self.cloud_storage.descargar(url)
                    if resultado:
                        nombre, contenido = resultado
                        attachment_files.append((nombre, contenido))
                        texto = await self.document_extractor.extract(
                            contenido, nombre, ""
                        )
                        if texto:
                            cloud_texts.append(texto)
                        self.logger.step_info(f"Descargado: {nombre} ({len(contenido)} bytes)")
                    else:
                        self.logger.step_info(f"No se pudo descargar: {url}")

                if cloud_texts:
                    cloud_extracted = self.document_extractor.unify_analysis_results(cloud_texts)
                    extracted_text = (
                        extracted_text + "\n\n" + cloud_extracted
                        if extracted_text
                        else cloud_extracted
                    )
                self.logger.step_success(
                    "Documentos cloud procesados",
                    {"descargados": len(cloud_texts), "total_links": len(cloud_urls)},
                )

            if not email.has_attachments and not cloud_urls:
                self.logger.step_start(
                    "ADJUNTOS", "No tiene adjuntos, verificando URLs conocidas..."
                )
                urls = extract_document_urls(
                    email.body.content, email_id, email.subject
                )
                if urls:
                    self.logger.step_info(f"Encontradas {len(urls)} URLs de documentos")
                    (
                        extracted_text,
                        attachment_files,
                    ) = await self._process_url_documents(urls)
                else:
                    self.logger.step_info("No se encontraron URLs de documentos")

            # 6.5 Validar que el correo tiene contenido suficiente para radicar
            tiene_adjuntos = len(attachment_files) > 0
            tiene_texto = len(extracted_text) > 50
            tiene_cuerpo = len(cleaned_body) > 100
            if not tiene_adjuntos and not tiene_texto and not tiene_cuerpo:
                raise ValueError(
                    "Correo sin informacion suficiente: sin adjuntos, sin documentos y cuerpo vacio o muy corto"
                )

            # 7. Extraer datos con IA (emulando workflow n8n)
            self.logger.step_start(
                "IA_EXTRACCION", "Extrayendo datos con agentes de IA..."
            )
            ai_data = await self.langchain.extract_all_data(
                email.subject, cleaned_body, extracted_text
            )
            result.extracted_data = {
                "tipoRemitente": ai_data.tipo_remitente,
                "tipoDocumentoAfectado": ai_data.tipo_documento_afectado,
                "numeroDocumentoAfectado": ai_data.numero_documento_afectado,
                "tipoDocumentoRemitente": ai_data.tipo_documento_remitente,
                "numeroDocumentoRemitente": ai_data.numero_documento_remitente,
                "tipoPqrd": ai_data.tipo_pqrd,
                "tipoSolicitud": ai_data.tipo_solicitud,
            }
            self.logger.step_success("Datos extraidos por IA")
            self.logger.show_extracted_data(result.extracted_data)

            # Validar que se encontró documento del afectado
            if (
                not ai_data.numero_documento_afectado
                or len(ai_data.numero_documento_afectado) < 5
            ):
                # Fallback: buscar documento en cada adjunto con Gemini 3
                self.logger.step_warning(
                    f"Documento no encontrado en extraccion inicial. "
                    f"Intentando fallback con Gemini 3 documento por documento..."
                )
                doc_encontrado = await self._fallback_buscar_documento(attachment_files)
                if doc_encontrado:
                    ai_data.numero_documento_afectado = doc_encontrado
                    self.logger.step_success(
                        f"Documento encontrado por fallback Gemini 3: {doc_encontrado}"
                    )
                else:
                    raise ValueError(
                        f"NO SE ENCONTRÓ DOCUMENTO DEL AFECTADO en el correo ni en adjuntos. "
                        f"Valor extraido: '{ai_data.numero_documento_afectado or 'VACIO'}'"
                    )

            # 8. Clasificar oficina destino (datos de tabla pqrd_config_tipo_solicitud_oficina_ia de PRD)
            self.logger.step_start("CLASIFICACION", "Obteniendo oficina destino...")
            # Intenta API primero; si el endpoint no existe aun, usa mapeo local con datos de PRD
            oficina_id = await self.asignacion_client.obtener_oficina_por_tipo(ai_data.tipo_solicitud)
            if oficina_id is not None:
                self.logger.step_success(
                    "Oficina asignada (desde API)",
                    {"tipoSolicitud": ai_data.tipo_solicitud, "oficinaId": oficina_id},
                )
            else:
                oficina_id = get_office_id(ai_data.tipo_solicitud)
                if oficina_id is not None:
                    self.logger.step_success(
                        "Oficina asignada (desde datos PRD locales)",
                        {"tipoSolicitud": ai_data.tipo_solicitud, "oficinaId": oficina_id},
                    )
                else:
                    raise ValueError(
                        f"No se encontro oficina para tipoSolicitud={ai_data.tipo_solicitud}. "
                        f"Verificar tabla pqrd_config_tipo_solicitud_oficina_ia."
                    )

            # 9. Construir datos del afectado y remitente
            numero_doc_remitente = (
                ai_data.numero_documento_remitente or ai_data.numero_documento_afectado
            )
            tipo_doc_remitente = (
                ai_data.tipo_documento_remitente or ai_data.tipo_documento_afectado
            )

            # Afectado: nombre extraído por IA del correo
            self.logger.step_start("IA_AFECTADO", "Extrayendo nombre del afectado del correo...")
            nombre_extraido = await self.langchain.extract_nombre_afectado(
                email.subject,
                cleaned_body,
                extracted_text,
            )
            afectado_data = PersonaData.from_extracted_data(
                numero_documento=ai_data.numero_documento_afectado,
                tipo_documento=get_tipo_documento_codigo(ai_data.tipo_documento_afectado),
                nombre=nombre_extraido or "",
            )
            # Telefono determinista sobre el cuerpo y el texto de los adjuntos.
            telefono_afectado = mejor_telefono(f"{cleaned_body}\n{extracted_text}")
            if telefono_afectado:
                afectado_data.telefono = telefono_afectado
                self.logger.step_info(f"Telefono afectado detectado: {telefono_afectado}")
            if not afectado_data.nombre_completo:
                raise ValueError(
                    f"NO SE PUDO OBTENER NOMBRE DEL AFECTADO DEL CORREO. "
                    f"Documento: {ai_data.numero_documento_afectado}"
                )
            self.logger.step_success(
                "Nombre del afectado extraído",
                {
                    "nombre": afectado_data.nombre_completo,
                    "documento": ai_data.numero_documento_afectado,
                },
            )

            # Remitente: nombre tomado del campo "De:" del correo
            # Si viene de un área interna de Positiva, extrae el nombre real del cuerpo
            self.logger.step_start("REMITENTE", "Construyendo datos del remitente desde el correo...")
            from_name_raw = email.from_address.name if email.from_address else ""
            from_email_raw_name = email.from_address.address if email.from_address else ""
            # Fuentes adicionales del nombre, en orden:
            #  - 'sender' de Graph: difiere del 'De:' en envíos delegados.
            #  - display names del mismo remitente en otros correos del hilo.
            #  - firma, solo del cuerpo del correo actual: la de un mensaje
            #    anterior del hilo pertenece a otra persona.
            sender_name_raw = email.sender.name if email.sender else ""
            nombres_hilo = [
                (e.from_address.name, e.from_address.address)
                for e in hilo_emails
                if e.from_address
            ]
            cuerpo_email_actual = clean_html_content(email.body.content or "")
            nombre_remitente = extraer_nombre_remitente_real(
                from_name_raw,
                from_email_raw_name,
                cleaned_body,
                sender_name=sender_name_raw,
                nombres_hilo=nombres_hilo,
                cuerpo_email_actual=cuerpo_email_actual,
            )

            # El campo "De:" no dejó un nombre usable: el buzón no tiene display
            # name y Graph devuelve la propia dirección de correo, tipico de
            # buzones corporativos. Se recupera del cuerpo con IA.
            if not nombre_remitente and ai_data.tipo_remitente != 3:
                self.logger.step_info(
                    f"El campo 'De:' no trae un nombre usable ('{from_name_raw}'). "
                    f"Recuperando el nombre del remitente desde el cuerpo con IA..."
                )
                nombre_remitente = await self.langchain.extract_nombre_remitente(
                    email.subject, cleaned_body, extracted_text
                )
                if not es_nombre_persona_valido(nombre_remitente):
                    nombre_remitente = ""

            self.logger.step_info(
                f"Datos del correo — De: '{from_name_raw}' <{from_email_raw_name}> | "
                f"Nombre efectivo: '{nombre_remitente}' | "
                f"Email efectivo: '{correo_remitente_efectivo}'"
            )
            if ai_data.tipo_remitente == 3:
                # El afectado ES el remitente
                remitente_data = afectado_data
            else:
                # Sin nombre confiable no se radica: el manejador de excepciones
                # manda el correo a revisión manual.
                if not nombre_remitente:
                    raise ValueError(
                        f"NO SE PUDO OBTENER EL NOMBRE DEL REMITENTE. "
                        f"De: '{from_name_raw}' <{from_email_raw_name}>. "
                        f"No se radica para no registrar el correo como nombre."
                    )
                remitente_data = PersonaData.from_extracted_data(
                    numero_documento=numero_doc_remitente,
                    tipo_documento=get_tipo_documento_codigo(tipo_doc_remitente),
                    nombre=nombre_remitente,
                )
                if telefono_afectado:
                    remitente_data.telefono = telefono_afectado
            self.logger.step_success(
                "Datos del remitente construidos",
                {
                    "nombre": remitente_data.nombre_completo,
                    "documento": numero_doc_remitente,
                },
            )

            # 10. Extraer sexo del afectado
            self.logger.step_start("IA_SEXO", "Extrayendo sexo del afectado...")
            genero_str = str(afectado_data.genero) if afectado_data.genero else ""
            sexo_afectado = await self.langchain.extract_sexo_afectado(
                afectado_data.nombre1,
                afectado_data.nombre2 or "",
                genero_str,
            )
            self.logger.step_success("Sexo extraido", {"sexo": sexo_afectado})

            # 12. Generar PDF del correo
            self.logger.step_start("GENERACION_PDF", "Generando PDF del correo completo...")
            email_pdf = self._generate_email_pdf(
                email, [f[0] for f in attachment_files], hilo_emails
            )
            attachment_files.append(("correo_completo.pdf", email_pdf))
            self.logger.step_success("PDF del correo generado")

            # 13. Preparar archivos para envío (sin ZIP, archivos individuales)
            self.logger.step_start("ARCHIVOS", "Preparando archivos adjuntos...")
            # Filtrar archivos de Outlook (imágenes de firma, etc.)
            filtered_files = [
                (name, content)
                for name, content in attachment_files
                if not any(
                    x in name.lower() for x in ["outlook", "signature", "firma", "logo"]
                )
            ]
            total_size = sum(len(content) for _, content in filtered_files)
            self.logger.step_success(
                "Archivos preparados",
                {
                    "cantidad": len(filtered_files),
                    "tamañoTotal": f"{total_size / 1024:.1f} KB",
                },
            )

            # 14. Construir payload
            self.logger.step_start("PAYLOAD", "Construyendo payload de radicacion...")
            payload = self._build_radicacion_payload(
                ai_data,
                afectado_data,
                remitente_data,
                sexo_afectado,
                oficina_id,
                email,
                cleaned_body,
                correo_remitente_efectivo,
            )
            self.logger.step_success("Payload construido")
            self.logger.step_info(
                "PAYLOAD COMPLETO (antes de radicar)",
                payload.to_form_data(),
            )

            # 15. Radicar PQRD
            self.logger.step_start("RADICACION", "Enviando radicacion a SGDEA...")
            try:
                radicacion_response = await self.radicacion.radicar_pqrd(
                    payload, filtered_files
                )

                # Validar que el backend devolvio numero de radicado
                if not radicacion_response.id_radicado:
                    raise Exception(
                        f"Backend no devolvio numero de radicado (id_radicado vacio). "
                        f"ID interno: {radicacion_response.id}"
                    )

                result.radicado = radicacion_response.id_radicado
                self.logger.step_success(
                    "PQRD RADICADA EXITOSAMENTE",
                    {
                        "idRadicado": radicacion_response.id_radicado,
                        "id": radicacion_response.id,
                    },
                )

            except Exception as rad_error:
                self.logger.step_error(f"Error en radicacion: {str(rad_error)}")

                # Etiquetar correo con info del error
                try:
                    etiqueta_error = f"ERROR-RADICAR"
                    await self.outlook.etiquetar_correo(email_id, etiqueta_error)
                except Exception:
                    pass

                # Mover correo (y hilo) a carpeta de error al radicar
                try:
                    self.logger.step_start(
                        "OUTLOOK_MOVER", "Moviendo correo a ERROR_RADICAR..."
                    )
                    await self._mover_hilo_completo(
                        email, email_id, settings.OUTLOOK_FOLDER_ERROR_RADICAR
                    )
                except Exception as move_error:
                    self.logger.step_error(f"Error moviendo correo: {str(move_error)}")

                # Actualizar resultado y retornar (no re-lanzar)
                result.error = str(rad_error)
                result.action = "error_radicar"
                result.duration_ms = int((time.time() - start_time) * 1000)
                self.logger.finish_error(result.error, result.duration_ms)
                return result

            # 16. Crear trazabilidad
            self.logger.step_start("TRAZABILIDAD", "Creando registro de trazabilidad...")
            try:
                await self.radicacion.crear_trazabilidad(
                    TrazabilidadPayload(
                        proceso=f"PQRD{radicacion_response.id}",
                        secuencia=radicacion_response.id_radicado,
                        estado="Por asignar",
                        descripcion="Usuario: Asignador IA  Descripcion: Se ha radicado una PQRD por medio del canal Email IA",
                        comentario="Radicado",
                        nombre=f"Asignador IA - {get_office_name(oficina_id)}",
                        accion=f"Asignador IA ha radicado la PQRD {radicacion_response.id_radicado}",
                        tramite="PQRD",
                    )
                )
                self.logger.step_success("Trazabilidad creada")
            except Exception as traza_error:
                self.logger.step_warning(f"Error creando trazabilidad (no critico): {traza_error}")

            asignado_automatico = False
            persona_asignada = ""

            # 18. Guardar hash en Redis
            self.logger.step_start("REDIS_SAVE", "Guardando hash en Redis...")
            redis_saved = await self.redis.save_email_hash(email_hash)
            if redis_saved:
                self.logger.step_success("Hash guardado en Redis")
            else:
                self.logger.step_warning(
                    "Error guardando en Redis (continuando de todas formas)"
                )

            # 19. Guardar en Supabase (no bloqueante - la radicación ya fue exitosa)
            self.logger.step_start("SUPABASE", "Guardando registro en Supabase...")
            try:
                await self.supabase.save_radicado_record(
                    numero_radicado=radicacion_response.id_radicado,
                    fecha_radicacion=radicacion_response.fecha_radicacion,
                    asunto=email.subject,
                    correo_remitente=email.from_address.address
                    if email.from_address
                    else "",
                    asignado_automatico=asignado_automatico,
                    persona_asignada=persona_asignada,
                    email_hash=email_hash,
                    email_id=email_id,
                    numero_documento_afectado=ai_data.numero_documento_afectado,
                )
                self.logger.step_success("Registro guardado en Supabase")
            except Exception as supabase_error:
                # Supabase es opcional - no debe fallar la radicación
                self.logger.step_warning(
                    f"Error en Supabase (no crítico): {str(supabase_error)}"
                )

            # 20. Etiquetar correo con numero de radicado
            try:
                await self.outlook.etiquetar_correo(email_id, result.radicado)
                self.logger.step_success(f"Correo etiquetado: {result.radicado}")
            except Exception as tag_error:
                self.logger.step_warning(f"Error etiquetando correo (no critico): {tag_error}")

            # 21. Mover correo (y todo el hilo) a carpeta de exito
            self.logger.step_start(
                "OUTLOOK_MOVER", "Moviendo correo a RADICACION_EXITOSA..."
            )
            try:
                await self._mover_hilo_completo(
                    email, email_id, settings.OUTLOOK_FOLDER_RADICACION_EXITOSA
                )
            except Exception as move_error:
                self.logger.step_warning(
                    f"Error moviendo correo a EXITOSA (radicacion YA fue exitosa): {move_error}"
                )

            result.success = True
            result.action = "radicado"
            result.duration_ms = int((time.time() - start_time) * 1000)
            self.logger.finish_success(result.radicado, result.duration_ms)

            return result

        except Exception as e:
            result.error = str(e)
            result.action = "manual"
            result.duration_ms = int((time.time() - start_time) * 1000)

            self.logger.step_error(f"Error en el procesamiento: {str(e)}")

            # Etiquetar correo como revision manual
            try:
                await self.outlook.etiquetar_correo(email_id, "REVISION-MANUAL")
            except Exception:
                pass

            # Mover correo (y hilo) a revision manual
            try:
                self.logger.step_start(
                    "OUTLOOK_MOVER", "Moviendo correo a REVISAR_MANUAL..."
                )
                await self._mover_hilo_completo(
                    email, email_id, settings.OUTLOOK_FOLDER_REVISAR_MANUAL
                )
            except Exception as move_error:
                self.logger.step_error(f"Error moviendo correo: {str(move_error)}")

            self.logger.finish_error(result.error, result.duration_ms)
            return result

    def _build_forward_comment(self, email: EmailMessage) -> str:
        """Construye el comentario HTML para el reenvío a otra área"""
        import re as _re

        def _escape(text: str) -> str:
            return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

        def _clean_body(html: str) -> str:
            html = _re.sub(r"<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>", "", html, flags=_re.IGNORECASE)
            html = _re.sub(r"<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>", "", html, flags=_re.IGNORECASE)
            return html

        from_name = email.from_address.name if email.from_address else ""
        from_addr = email.from_address.address if email.from_address else ""
        subject = _escape(email.subject or "Sin asunto")
        body_content = _clean_body(email.body.content if email.body else "Sin contenido")

        banner = (
            '<div style="background-color:#1a3a5c;color:#fff;padding:12px 20px;'
            'border-radius:6px;font-family:Arial,sans-serif;margin-bottom:18px;'
            'border-left:5px solid #f0a500;">'
            '<div style="font-size:15px;font-weight:bold;margin-bottom:4px;">'
            "&#129302; REENV&Iacute;O ELECTR&Oacute;NICO AUTOM&Aacute;TICO CON IA"
            "</div>"
            '<div style="font-size:12px;opacity:0.85;">'
            "Correo derivado autom&aacute;ticamente al &aacute;rea de Medicina Laboral."
            "</div></div>"
        )

        html_correo = (
            f"<h2>Asunto: {subject}</h2>"
            f"<p><strong>De:</strong> {_escape(from_name)} &lt;{_escape(from_addr)}&gt;</p>"
            f"<p><strong>Fecha:</strong> {email.received_datetime or ''}</p>"
            f"<hr>{body_content}"
        )

        return f"{banner}{html_correo}"

    # Maximo de adjuntos para extraccion de texto (todos se suben al radicar)
    MAX_ADJUNTOS_EXTRACCION = 10

    async def _process_attachments(
        self, message_id: str
    ) -> Tuple[str, List[Tuple[str, bytes]]]:
        """Procesa los adjuntos de un correo.

        Todos los archivos se descargan para adjuntar al radicado,
        pero solo los primeros MAX_ADJUNTOS_EXTRACCION se procesan
        para extraccion de texto (IA).
        """
        attachments = await self.outlook.get_attachments(message_id)
        analysis_results = []
        files: List[Tuple[str, bytes]] = []
        extracciones_realizadas = 0

        for attachment in attachments:
            content_type = attachment.get("contentType", "")
            name = attachment.get("name", "")

            # Filtrar solo imágenes de firma de Outlook (outlook0.png, image001.png, etc)
            is_inline = attachment.get("isInline", False)
            if "image" in content_type:
                nombre_lower = name.lower()
                # Omitir imagenes de firma de Outlook
                if "outlook" in nombre_lower:
                    continue
                # Omitir imagenes inline pequeñas tipicas de firma (image001, image002, etc)
                if is_inline and nombre_lower.startswith("image00") and attachment.get("size", 0) < 50000:
                    continue

            content_b64 = await self.outlook.download_attachment(
                message_id, attachment["id"]
            )
            content = base64.b64decode(content_b64)

            # Si es ZIP, extraer archivos individuales (no subir el ZIP)
            if name.lower().endswith(".zip"):
                import zipfile
                import io
                try:
                    with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
                        for archivo_zip in zf.namelist():
                            if archivo_zip.endswith("/"):
                                continue
                            file_data = zf.read(archivo_zip)
                            nombre_archivo = archivo_zip.split("/")[-1] if "/" in archivo_zip else archivo_zip
                            files.append((nombre_archivo, file_data))
                            if extracciones_realizadas < self.MAX_ADJUNTOS_EXTRACCION:
                                text = await self.document_extractor.extract(file_data, nombre_archivo, "")
                                if text:
                                    analysis_results.append(text)
                                extracciones_realizadas += 1
                except Exception as zip_err:
                    self.logger.step_warning(f"Error extrayendo ZIP {name}: {zip_err}")
                    files.append((name, content))
            elif is_p7m_file(name):
                files.append((name, content))
                urls = extract_urls_from_p7m(content_b64)
                for url in urls:
                    file_content = await self.radicacion.download_file(url)
                    if file_content:
                        files.append((f"documento_{len(files)}.pdf", file_content))
                        if extracciones_realizadas < self.MAX_ADJUNTOS_EXTRACCION:
                            text = await self.document_extractor.extract(
                                file_content, "documento.pdf", "application/pdf"
                            )
                            analysis_results.append(text)
                            extracciones_realizadas += 1
            else:
                files.append((name, content))
                if extracciones_realizadas < self.MAX_ADJUNTOS_EXTRACCION:
                    text = await self.document_extractor.extract(
                        content, name, content_type or "application/octet-stream"
                    )
                    analysis_results.append(text)
                    extracciones_realizadas += 1

        return self.document_extractor.unify_analysis_results(analysis_results), files

    async def _process_url_documents(
        self, urls: list
    ) -> Tuple[str, List[Tuple[str, bytes]]]:
        """Procesa URLs de documentos"""
        analysis_results = []
        files: List[Tuple[str, bytes]] = []

        for url_info in urls:
            content = await self.radicacion.download_file(url_info.url)
            if content:
                filename = f"documento_{len(files) + 1}.pdf"
                files.append((filename, content))
                text = await self.document_extractor.extract(
                    content, filename, "application/pdf"
                )
                analysis_results.append(text)

        return self.document_extractor.unify_analysis_results(analysis_results), files

    def _format_email_datetime(self, iso_datetime: str) -> str:
        """Formatea una fecha ISO a formato legible estilo correo."""
        if not iso_datetime:
            return ""
        try:
            dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
            dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
            meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                     "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
            return f"{dias[dt.weekday()]}, {dt.day} de {meses[dt.month - 1]} de {dt.year} {dt.strftime('%I:%M %p')}"
        except Exception:
            return iso_datetime

    def _format_recipients(self, recipients: List) -> str:
        """Formatea lista de destinatarios para mostrar en el PDF."""
        if not recipients:
            return ""
        parts = []
        for r in recipients:
            if r.name and r.address:
                parts.append(f"{escape_html(r.name)} &lt;{escape_html(r.address)}&gt;")
            elif r.address:
                parts.append(escape_html(r.address))
            elif r.name:
                parts.append(escape_html(r.name))
        return "; ".join(parts)

    def _render_single_email_block(self, msg: EmailMessage, is_reply: bool = False) -> str:
        """Renderiza un bloque HTML para un correo individual dentro del hilo."""
        from_name = escape_html(msg.from_address.name) if msg.from_address and msg.from_address.name else ""
        from_addr = escape_html(msg.from_address.address) if msg.from_address and msg.from_address.address else ""
        fecha = self._format_email_datetime(msg.sent_datetime)
        to_str = self._format_recipients(msg.to_recipients)

        sender_display = f"{from_name} &lt;{from_addr}&gt;" if from_name else from_addr

        header_html = f"""<div class="{'reply-header' if is_reply else 'email-header'}">
  <table class="header-table">
    <tr><td class="label">De:</td><td>{sender_display}</td></tr>
    <tr><td class="label">Enviado:</td><td>{fecha}</td></tr>
    <tr><td class="label">Para:</td><td>{to_str}</td></tr>
    <tr><td class="label">Asunto:</td><td>{escape_html(msg.subject)}</td></tr>
  </table>
</div>"""

        body_content = msg.body.content if msg.body and msg.body.content else ""

        if is_reply:
            return f"""<div class="reply-block">
  <div class="reply-separator"></div>
  {header_html}
  <div class="email-body">{body_content}</div>
</div>"""
        else:
            return f"""{header_html}
<div class="email-body">{body_content}</div>"""

    def _generate_email_pdf(
        self, email: EmailMessage, attachment_names: List[str],
        hilo_emails: Optional[List[EmailMessage]] = None
    ) -> bytes:
        """Genera un PDF profesional con el correo completo y su hilo de conversación."""
        import fitz

        # Adjuntos
        attachments_html = ""
        if attachment_names:
            items = "".join(
                f'<tr><td class="attach-icon">📎</td><td class="attach-name">{escape_html(name)}</td></tr>'
                for name in attachment_names
            )
            attachments_html = f"""<div class="attachments-section">
  <div class="attachments-title">Adjuntos ({len(attachment_names)})</div>
  <table class="attachments-table">{items}</table>
</div>"""

        # Correo principal
        main_email_html = self._render_single_email_block(email, is_reply=False)

        # Hilo de conversación (correos anteriores, del más reciente al más antiguo)
        thread_html = ""
        if hilo_emails and len(hilo_emails) > 1:
            for thread_msg in hilo_emails[1:]:
                thread_html += self._render_single_email_block(thread_msg, is_reply=True)

        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
body {{
  font-family: Calibri, 'Segoe UI', Arial, sans-serif;
  font-size: 9pt;
  color: #333333;
  margin: 20px 24px;
  line-height: 1.4;
}}
.header-table {{
  border-collapse: collapse;
  font-size: 8.5pt;
  color: #444;
  margin: 0;
}}
.header-table td {{
  padding: 1px 0;
  vertical-align: top;
}}
.header-table .label {{
  font-weight: 600;
  color: #555;
  padding-right: 8px;
  white-space: nowrap;
  width: 55px;
}}
.email-header {{
  padding: 8px 0 6px 0;
  border-bottom: 1px solid #e0e0e0;
  margin-bottom: 10px;
}}
.email-body {{
  font-size: 9pt;
  color: #1a1a1a;
  padding: 6px 0 10px 0;
}}
.email-body p {{
  margin: 4px 0;
}}
.email-body img {{
  max-width: 100%;
  height: auto;
}}
.attachments-section {{
  margin-top: 12px;
  padding: 8px 10px;
  background: #f8f8f8;
  border: 1px solid #e8e8e8;
  border-radius: 3px;
}}
.attachments-title {{
  font-size: 8pt;
  font-weight: 600;
  color: #666;
  margin-bottom: 4px;
}}
.attachments-table {{
  border-collapse: collapse;
  font-size: 8pt;
  color: #444;
}}
.attachments-table td {{
  padding: 2px 4px;
  vertical-align: middle;
}}
.attach-icon {{
  width: 16px;
  font-size: 9pt;
}}
.attach-name {{
  color: #0563C1;
}}
.reply-block {{
  margin-top: 10px;
  padding-left: 0;
}}
.reply-separator {{
  border-top: 1px solid #c0c0c0;
  margin: 14px 0 8px 0;
}}
.reply-header {{
  padding: 4px 0;
  margin-bottom: 6px;
}}
.footer {{
  margin-top: 30px;
  padding-top: 8px;
  border-top: 1px solid #e0e0e0;
  font-size: 7pt;
  color: #999;
  text-align: center;
}}
</style>
</head>
<body>
{main_email_html}
{attachments_html}
{thread_html}
<div class="footer">Documento generado automáticamente — Sistema de Radicación PQRD · Positiva ARL</div>
</body>
</html>"""

        try:
            doc = fitz.open(stream=html.encode("utf-8"), filetype="html")
            pdf_bytes = doc.convert_to_pdf()
            doc.close()
            return pdf_bytes
        except Exception as e:
            self.logger.step_warning(f"Error convirtiendo correo a PDF: {e}. Adjuntando como HTML.")
            return html.encode("utf-8")

    async def _mover_hilo_completo(
        self, email: EmailMessage, email_id: str, destination_folder_id: str
    ) -> None:
        """Mueve el correo y todo su hilo a la carpeta destino (misma logica que tutelas)"""
        movidos = await self.outlook.mover_hilo_completo(
            email_id=email_id,
            conversation_id=email.conversation_id,
            folder_origen_id=settings.OUTLOOK_FOLDER_ID,
            folder_destino_id=destination_folder_id,
        )
        if movidos > 1:
            self.logger.step_success(f"Hilo completo movido: {movidos} mensajes")
        else:
            self.logger.step_success("Correo movido exitosamente")

    def _build_radicacion_payload(
        self,
        ai_data,
        afectado_data: PersonaData,
        remitente_data: PersonaData,
        sexo_afectado: int,
        oficina_id: int,
        email: EmailMessage,
        cleaned_body: str,
        correo_remitente_efectivo: str = "",
    ) -> RadicacionPayload:
        """Construye el payload de radicacion"""
        fecha = (
            datetime.fromisoformat(
                email.received_datetime.replace("Z", "+00:00")
            ).strftime("%d/%m/%Y %H:%M:%S")
            if email.received_datetime
            else ""
        )

        return RadicacionPayload(
            datos_remitente_tipo_remitente_id=ai_data.tipo_remitente,
            datos_remitente_tipo_documento_id=get_tipo_documento_id(
                remitente_data.tipo_documento
            ),
            datos_remitente_numero_documento=remitente_data.numero_documento,
            datos_remitente_nombre=remitente_data.nombre_completo,
            datos_afectado_tipo_documento_id=ai_data.tipo_documento_afectado,  # Validacion cruzada ya hecha en langchain_agents
            datos_afectado_numero_documento=afectado_data.numero_documento,
            datos_afectado_nombre=afectado_data.nombre_completo,
            datos_afectado_lgtbtq=False,
            datos_afectado_genero_id=sexo_afectado,
            datos_afectado_correo_electronico=correo_remitente_efectivo or "",
            datos_afectado_telefono_celular=afectado_data.telefono,
            datos_afectado_pais_id_pais=425,
            datos_afectado_departamento_id_departamento=afectado_data.departamento.id_departamento
            or 6,
            datos_afectado_municipio_id_municipio=afectado_data.municipio.id_municipio
            or 149,
            datos_afectado_direccion=afectado_data.direccion,
            datos_afectado_medio_respuesta_id=1,
            solicitud_tipo_pqrd_id=ai_data.tipo_pqrd,
            solicitud_tipo_solicitud_id=ai_data.tipo_solicitud,
            solicitud_tipo_producto_id=15,
            solicitud_acceso_informacion_publica=False,
            solicitud_descripcion_acceso_informacion_id=2,
            solicitud_observaciones=f"Fecha: {fecha}  Asunto: {email.subject} Cuerpo: {cleaned_body[:2000]}",
            solicitud_detalle="",
            usuario_creador="Asignador IA",
            canal_radicacion=7,  # 7=EMAIL-IA
            oficina=oficina_id,
            detalle_afectado="",
            listado_condiciones="[1]",  # Igual que curl produccion
        )

    @staticmethod
    def _es_factura_electronica(from_email: str, subject: str, body: str) -> bool:
        """Detecta si el correo es una factura electronica (no se debe radicar como PQRD)."""
        email_lower = (from_email or "").lower()
        subject_lower = (subject or "").lower()
        body_lower = (body or "")[:2000].lower()

        # Check 1: remitente contiene "facturacion" o "factura"
        email_es_factura = any(x in email_lower for x in [
            "facturacion", "factura", "billing", "invoice",
            "notaelectronica", "notacredito",
        ])

        # Check 2: asunto con formato NIT;EMPRESA;FACTURA;TIPO
        # Ejemplo: "813005265;ESE CARMEN EMILIA OSPINA;NC105687;91;ESE CARMEN EMILIA OSPINA"
        import re
        asunto_formato_factura = bool(re.match(r"^\d{6,};[^;]+;[A-Z]*\d+;\d{1,2};", subject or ""))

        # Check 3: cuerpo menciona factura/nota electronica
        body_es_factura = any(x in body_lower for x in [
            "factura electronica",
            "nota electronica",
            "factura de venta",
            "nota credito electronica",
            "nota debito electronica",
            "documento electronico",
            "cufe:",
            "informamos a usted que el documento",
        ])

        # Es factura si cumple al menos 2 de 3 criterios, o si el asunto tiene formato de factura
        if asunto_formato_factura:
            return True
        return sum([email_es_factura, body_es_factura]) >= 2

    async def _fallback_buscar_documento(self, attachment_files: List[Tuple[str, bytes]]) -> Optional[str]:
        """Fallback: busca numero de documento del afectado en cada adjunto con Gemini 2.5 Pro.

        Solo extrae numero_documento y tipo_documento. Usa el modelo mas potente
        de Gemini para vision/documentos.
        """
        from google.genai import types
        from services.gemini_retry import gemini_retry

        PROMPT = """ERES UN EXTRACTOR ESPECIALIZADO DE NUMEROS DE CEDULA COLOMBIANOS.

Tu UNICA tarea es encontrar el NUMERO DE DOCUMENTO DE IDENTIDAD del PACIENTE o AFECTADO en este documento.

DONDE BUSCAR (en orden de prioridad):
1. Campo "CC", "C.C.", "Cedula", "No. Identificacion", "Documento"
2. Encabezado del documento medico (datos del paciente)
3. Cerca del nombre de la persona (NO del medico, NO del empleador)
4. En tablas con datos personales
5. En cualquier parte donde aparezca un numero de 6-10 digitos asociado a una persona

QUE IGNORAR:
- Numeros de telefono (10 digitos que empiezan con 3)
- Codigos de diagnostico (S431, M545, K251)
- Fechas (01/04/2026)
- NIT de empresas/clinicas (a menos que sea el unico documento)
- Numeros de radicado o consecutivos
- Codigos de poliza o afiliacion largos (mas de 10 digitos)

FORMATO DE RESPUESTA:
- Si encuentras el documento, responde SOLO el numero sin puntos ni guiones
- Si NO encuentras ningun documento, responde: NO_ENCONTRADO
- NO respondas con texto adicional, explicaciones ni JSON"""

        gemini_client = self.gemini.client  # reutiliza Client ya instanciado

        @gemini_retry
        async def _call(file_part, prompt_text):
            return await gemini_client.aio.models.generate_content(
                model="gemini-2.5-pro",
                contents=[file_part, prompt_text],
            )

        mime_map = {
            "pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "gif": "image/gif", "bmp": "image/bmp",
            "tiff": "image/tiff", "tif": "image/tiff",
        }

        for nombre, contenido in attachment_files:
            ext = nombre.lower().split(".")[-1] if "." in nombre else ""
            if ext not in mime_map:
                continue

            try:
                self.logger.step_info(f"Fallback Gemini 2.5 Pro: analizando {nombre}...")
                mime = mime_map[ext]

                file_part = types.Part.from_bytes(data=contenido, mime_type=mime)
                response = await _call(file_part, PROMPT)

                resultado = (response.text or "").strip()
                if "NO_ENCONTRADO" in resultado.upper():
                    continue

                doc_limpio = "".join(c for c in resultado if c.isdigit())

                if doc_limpio and 6 <= len(doc_limpio) <= 10:
                    if not (len(doc_limpio) == 10 and doc_limpio.startswith("3")):
                        self.logger.step_success(f"Fallback: documento {doc_limpio} encontrado en {nombre}")
                        return doc_limpio

            except Exception as e:
                self.logger.step_warning(f"Fallback: error en {nombre}: {e}")
                continue

        return None
