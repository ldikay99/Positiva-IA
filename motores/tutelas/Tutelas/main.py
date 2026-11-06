"""
Orquestador Principal - Radicación Ministerio de Trabajo
Replica el flujo de n8n pero permite radicar archivos INDIVIDUALMENTE

Autor: Migración desde n8n
Fecha: 2025
"""
import os
import re
import time
import traceback
from typing import List, Optional
from datetime import datetime

# Importar configuración y servicios
try:
    # Cuando se importa desde la raíz (002_Ejecucion_Tutelas.py)
    from Tutelas.config import (DOMINIOS_JUDICIALES, MODO_TEST, MODO_UAT,
                                RADICAR_ENLACE_CON_ADJUNTOS)
    from Tutelas import operaciones as op
    from Tutelas.test_recorder import get_recorder
    from Tutelas.models.correo import Correo, DatosExtraidos
    from Tutelas.services.outlook_service import OutlookService
    from Tutelas.services.redis_service import RedisService
    from Tutelas.services.ai_extractor import AIExtractor
    from Tutelas.services.juzgados_service import JuzgadosService
    from Tutelas.services.sgdea_service import SGDEAService
    from Tutelas.services.sharepoint_service import SharePointService
except ImportError:
    # Cuando se ejecuta directamente desde Tutelas/
    from config import (DOMINIOS_JUDICIALES, MODO_TEST, MODO_UAT,
                        RADICAR_ENLACE_CON_ADJUNTOS)
    import operaciones as op
    from test_recorder import get_recorder
    from models.correo import Correo, DatosExtraidos
    from services.outlook_service import OutlookService
    from services.redis_service import RedisService
    from services.ai_extractor import AIExtractor
    from services.juzgados_service import JuzgadosService
    from services.sgdea_service import SGDEAService
    from services.sharepoint_service import SharePointService


class OrquestadorRadicacion:
    def __init__(self):
        print("=" * 60)
        print("INICIALIZANDO ORQUESTADOR DE RADICACIÓN")
        print("=" * 60)

        self.outlook = OutlookService()
        self.redis = RedisService()
        self.ai = AIExtractor()
        self.juzgados = JuzgadosService()
        self.sgdea = SGDEAService()
        self.sharepoint = SharePointService()

        print("✅ Todos los servicios inicializados")

    @staticmethod
    def es_correo_judicial(correo: Correo) -> bool:
        """Indica si el correo proviene de un despacho de la Rama Judicial."""
        return correo.get_dominio_remitente() in DOMINIOS_JUDICIALES

    @classmethod
    def buscar_correo_judicial(cls, correos_hilo: List[Correo]) -> Optional[Correo]:
        """Primer correo del hilo enviado por un despacho judicial.

        Es el que autoriza la radicacion y el que se reporta a SGDEA como
        remitente. Retorna None si ningun correo del hilo lo cumple.
        """
        for c in correos_hilo:
            if cls.es_correo_judicial(c):
                return c
        return None

    # Terminos que nombran el tramite de forma explicita.
    _RE_TUTELA = re.compile(
        r'accion\s+de\s+tutela|tutela(s)?|desacato|incidente\s+de\s+desacato'
        r'|requerimiento\s+previo', re.IGNORECASE)

    # Actuaciones de un proceso en curso. Los despachos notifican muchas de estas
    # sin escribir la palabra "tutela": "NOTIFICA DECISION", "COMUNICA ACTUACION
    # PROCESAL", "Notificacion Proceso Nro. ...". Por si solas no bastan, porque
    # tambien aparecen en procesos de otra naturaleza; ver `procesar_hilo`.
    # El texto llega con y sin tildes segun el despacho, de ahi las clases [oó].
    _RE_ACTUACION = re.compile(
        r'notifica\w*\W+(decisi[oó]n|sentencia|fallo|auto|providencia|actuaci[oó]n|proceso)'
        r'|comunica\w*\W+actuaci[oó]n'
        r'|actuaci[oó]n\W+procesal'
        r'|profiri[oó]\W+(sentencia|auto|providencia)'
        r'|auto\W+(admisorio|interlocutorio|que\W+avoca|avoca)'
        r'|\bavoc[oa]\b'
        r'|\bimpugnaci[oó]n\b'
        r'|\bsentencia\b|\bfallo\b|\bprovidencia\b'
        r'|fallo\W+de\W+(primera|segunda)\W+instancia',
        re.IGNORECASE)

    def detectar_tutela(self, correo: Correo) -> bool:
        """Detecta si el correo es sobre una tutela"""
        texto = f"{correo.subject} {correo.body_text}"
        return bool(self._RE_TUTELA.search(texto))

    def detectar_actuacion_judicial(self, correo: Correo) -> bool:
        """Detecta la notificacion de una actuacion dentro de un proceso.

        Se usa como respaldo de `detectar_tutela` para los correos en que el
        despacho notifica una decision sin nombrar el tramite. Solo se acepta
        junto con un remitente judicial y un radicado valido (ver `procesar_hilo`).
        """
        texto = f"{correo.subject} {correo.body_text}"
        return bool(self._RE_ACTUACION.search(texto))

    def _buscar_enlace_sharepoint(self, correos_hilo: List[Correo]) -> Optional[str]:
        """Busca un enlace a un portal de expediente en cualquier correo del hilo.

        Incluye SharePoint judicial y los demas portales por los que los despachos
        comparten el expediente en lugar de anexarlo.
        """
        for correo in correos_hilo:
            for texto in (correo.body_content, correo.body_text):
                enlace = self.sharepoint.extraer_enlace_expediente(texto)
                if enlace:
                    return enlace
        return None

    @staticmethod
    def _es_radicado_valido(radicado) -> bool:
        """Un radicado valido es EXACTAMENTE 23 digitos numericos (regla del repo)."""
        return bool(radicado) and str(radicado).isdigit() and len(str(radicado)) == 23

    @staticmethod
    def _extraer_cortos(texto: str) -> set:
        """Extrae referencias 'cortas' de radicado (año + consecutivo) del texto,
        ej: '2026-00253', '2026-253', '2026 253'. Devuelve set de (año, consecutivo_int).
        Sirve para cruzar contra el radicado de 23 digitos (posiciones 13-16 año,
        17-21 consecutivo) y detectar un numero mal leido/ambiguo."""
        cortos = set()
        for m in re.finditer(r'(20\d{2})\s*[-\s]\s*0*(\d{1,5})\b', texto or ""):
            cortos.add((m.group(1), int(m.group(2))))
        for m in re.finditer(r'\b0*(\d{1,5})\s*[-\s]\s*(20\d{2})\b', texto or ""):
            cortos.add((m.group(2), int(m.group(1))))
        return cortos

    def _radicado_coincide_con_corto(self, radicado: str, texto: str) -> bool:
        """True si el año+consecutivo del radicado (23 dig) aparece como radicado corto
        en el texto. Si el texto NO tiene ningun corto, retorna True (no hay con que
        cruzar -> se acepta, mejor que mandar todo a manual)."""
        if not self._es_radicado_valido(radicado):
            return False
        cortos = self._extraer_cortos(texto)
        if not cortos:
            return True  # sin corto de referencia -> usar el radicado
        anio = str(radicado)[12:16]
        consec = int(str(radicado)[16:21])
        return (anio, consec) in cortos

    # Radicado escrito por grupos, separados por espacios o guiones. Cubre tanto el
    # numero completo (23 digitos) como el que el despacho escribe sin el sufijo de
    # instancia (21 digitos), habitual en los encabezados "Radicado: ...".
    _RE_RADICADO_GRUPOS = re.compile(r'\b\d{1,5}(?:[\s\-]\d{1,5}){5,8}\b')

    def extraer_radicado_regex(self, texto: str) -> Optional[str]:
        """
        Extrae radicado de 23 dígitos de un texto.

        Reconoce el numero seguido, escrito por grupos con guiones o espacios, y el
        formato de 21 digitos al que le falta el sufijo de instancia (ver
        `_completar_instancia`).
        """
        # Formato 1: 23 dígitos seguidos
        match = re.search(r'\b\d{23}\b', texto)
        if match:
            return match.group(0)

        # Formato 2: por grupos, con guiones o espacios
        # (ej: 81-001-40-71-001-2026-00020-00 o 05 045 31 87 001 2026 00106 00)
        candidatos_21 = []
        for m in self._RE_RADICADO_GRUPOS.finditer(texto):
            limpio = re.sub(r'\D', '', m.group(0))
            if len(limpio) == 23:
                return limpio
            if len(limpio) == 21:
                candidatos_21.append(m.group(0))

        # Formato 3: 21 digitos -> se completa el sufijo de instancia si la
        # estructura del numero lo respalda.
        for crudo in candidatos_21:
            completado = self._completar_instancia(crudo)
            if completado:
                return completado

        return None

    @staticmethod
    def _completar_instancia(crudo: str) -> Optional[str]:
        """Completa el sufijo de instancia de un radicado escrito con 21 digitos.

        Los despachos suelen omitir los dos ultimos digitos (la instancia) al citar
        el radicado, por ejemplo "Radicado: 05 045 31 87 001 2026 00106". Solo se
        completa cuando el numero viene separado en los siete grupos de la
        estructura oficial (departamento, municipio, entidad, especialidad,
        despacho, año y consecutivo) y el año es plausible; asi se evita construir
        un numero a partir de una cifra cualquiera del documento.
        """
        grupos = re.split(r'[\s\-]+', crudo.strip())
        if [len(g) for g in grupos] != [2, 3, 2, 2, 3, 4, 5]:
            return None
        anio = grupos[5]
        if not (1990 <= int(anio) <= 2100):
            return None
        return re.sub(r'\D', '', crudo) + "00"

    def _extraer_texto_pdf(self, contenido: bytes, nombre: str = "documento.pdf") -> str:
        """
        Extrae texto de un PDF usando cadena de fallbacks:
        1. PyMuPDF texto nativo (3 métodos)
        2. Tesseract OCR a 300 DPI (para PDFs escaneados)
        """
        import io
        import fitz

        texto_partes = []

        try:
            doc = fitz.open(stream=contenido, filetype="pdf")
            total_paginas = len(doc)

            for page_num in range(total_paginas):
                page = doc[page_num]

                # Método 1: texto embebido directo
                text = page.get_text()
                if text and text.strip():
                    texto_partes.append(text)
                    continue

                # Método 2: get_text("dict") — bloques y spans
                try:
                    text_dict = page.get_text("dict")
                    lines = []
                    for block in text_dict.get("blocks", []):
                        if block.get("type") == 0:
                            for line in block.get("lines", []):
                                line_text = "".join(
                                    span.get("text", "") for span in line.get("spans", [])
                                )
                                if line_text.strip():
                                    lines.append(line_text)
                    if lines:
                        texto_partes.append("\n".join(lines))
                        continue
                except Exception:
                    pass

            doc.close()

            resultado = "\n".join(texto_partes)
            chars_utiles = len([c for c in resultado if c.isalnum()])

            if chars_utiles > 50:
                return resultado

        except Exception as e:
            print(f"      [PDF] Error PyMuPDF: {type(e).__name__}: {e}")

        # Fallback: Tesseract OCR a 300 DPI (PDF escaneado)
        print(f"      [PDF] Texto nativo insuficiente en {nombre} → OCR Tesseract 300 DPI")
        try:
            import pytesseract
            from PIL import Image

            doc = fitz.open(stream=contenido, filetype="pdf")
            ocr_partes = []
            paginas_ocr = min(len(doc), 10)  # Máximo 10 páginas para buscar radicado

            for page_num in range(paginas_ocr):
                page = doc[page_num]
                pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
                img_data = pix.tobytes("png")
                image = Image.open(io.BytesIO(img_data))
                ocr_text = pytesseract.image_to_string(image, lang="spa+eng")

                if ocr_text and ocr_text.strip():
                    ocr_partes.append(ocr_text)

            doc.close()

            resultado_ocr = "\n".join(ocr_partes)
            if resultado_ocr.strip():
                print(f"      [PDF] Tesseract OCR: {len(resultado_ocr)} caracteres extraídos")
                return resultado_ocr

        except ImportError:
            print(f"      [PDF] Tesseract no disponible (pytesseract/pillow no instalados)")
        except Exception as e:
            print(f"      [PDF] Error Tesseract: {type(e).__name__}: {e}")

        return ""

    def _mover_hilo_fallido(self, correos_hilo: List[Correo], etiqueta_error: str = "ERROR-GENERAL",
                            radicado_23: Optional[str] = None):
        """Cierra un hilo que no se radico: deja el motivo en consola, avisa al buzon
        interno, etiqueta los correos y los devuelve a Bandeja de Entrada."""
        op.no_radicado(etiqueta_error)

        correo_principal = correos_hilo[-1]
        try:
            if self.outlook.responder_no_radicado(correo_principal.id,
                                                  op.motivo(etiqueta_error), radicado_23,
                                                  accion=op.accion(etiqueta_error),
                                                  asunto_original=correo_principal.subject):
                op.ok("Aviso de no radicacion enviado al buzon interno")
            else:
                op.fallo("Aviso de no radicacion", "no se pudo enviar el correo")
        except Exception as e:
            op.fallo("Aviso de no radicacion", f"{type(e).__name__}: {e}")

        etiquetados = 0
        for c in correos_hilo:
            try:
                if self.outlook.etiquetar_correo(c.id, etiqueta_error):
                    etiquetados += 1
            except Exception as e:
                op.fallo("Etiquetar correo", f"{type(e).__name__}: {e}")
        op.ok("Etiquetado", f"{etiquetados}/{len(correos_hilo)} con '{etiqueta_error}'")

        ids = [c.id for c in correos_hilo]
        movidos = self.outlook.mover_hilo_a_bandeja_entrada(ids)
        if movidos == len(ids):
            op.ok("Correos devueltos a Bandeja de Entrada", f"{movidos}/{len(ids)}")
        else:
            op.fallo("Mover correos a Bandeja de Entrada", f"solo {movidos}/{len(ids)}")

    def _clave_dedup(self, radicado_23: str, adjuntos, textos_pdf: dict,
                     correo_id: str) -> tuple:
        """Llave de deduplicacion del hilo y el criterio con que se construyo.

        Combina el radicado con la huella del texto de los anexos PDF y, si no hay
        texto utilizable, con la huella del contenido binario. La huella de texto
        reconoce el mismo documento aunque el archivo se haya regenerado; la binaria
        cubre los anexos de los que no se puede extraer texto.

        `textos_pdf` trae el texto ya leido en la busqueda del radicado; los PDF que
        no esten ahi se leen una sola vez y se agregan.
        """
        for nombre, contenido, _ct in adjuntos:
            if not nombre.lower().endswith(".pdf"):
                continue
            if nombre in textos_pdf or not contenido:
                continue
            if not self.redis._cuenta_para_huella(nombre, contenido):
                continue
            try:
                textos_pdf[nombre] = self._extraer_texto_pdf(contenido, nombre)
            except Exception as e:
                op.fallo("Leer anexo para verificacion", f"{nombre}: {type(e).__name__}")

        huella_txt = self.redis.huella_texto(
            [t for n, t in textos_pdf.items() if n in {a[0] for a in adjuntos}]
        )
        if huella_txt:
            return f"{radicado_23}:T:{huella_txt}", "por contenido del documento"

        huella_bin = self.redis.huella_documentos(adjuntos)
        if huella_bin:
            return f"{radicado_23}:B:{huella_bin}", "por archivo anexo"

        return f"{radicado_23}:{correo_id}", "sin anexos comparables"

    def _claves_por_documento(self, radicado_23: str, adjuntos, textos_pdf: dict) -> list:
        """Llaves de deduplicacion de cada documento del hilo por separado.

        La llave del hilo cubre el conjunto completo de anexos, de modo que dos
        correos del mismo caso con distinta cantidad de documentos producen llaves
        distintas y ambos se radican. Con una llave por documento basta con que uno
        de ellos ya se haya radicado para reconocer el caso repetido.
        """
        nombres = {a[0] for a in adjuntos}
        claves = []
        for nombre, texto in textos_pdf.items():
            if nombre not in nombres:
                continue
            h = self.redis.huella_texto([texto])
            if h:
                claves.append(f"{radicado_23}:D:{h}")
        for nombre, contenido, ct in adjuntos:
            if nombre in textos_pdf:
                continue
            h = self.redis.huella_documentos([(nombre, contenido, ct)])
            if h:
                claves.append(f"{radicado_23}:D:{h}")
        return claves

    def procesar_hilo(self, correos_hilo: List[Correo]) -> bool:
        """
        Procesa un hilo completo de conversación como una unidad.
        El correo más reciente es el "principal" para extracción de datos.
        Los adjuntos se recopilan de TODOS los correos del hilo.
        Retorna True si se radicó exitosamente.
        """
        # Identificar correo original del juzgado (primer correo con dominio externo)
        # y correo principal (más reciente) para extracción de datos
        correo_principal = correos_hilo[-1]

        # MODO_TEST: fija el hilo actual para correlacionar las escrituras en CSV
        if MODO_TEST:
            get_recorder().set_contexto(correo_principal.subject or correo_principal.id)

        # El remitente que se reporta a SGDEA debe ser el despacho judicial que
        # notifico. Si el hilo no trae ninguno, el paso 1 descarta el hilo.
        correo_origen = self.buscar_correo_judicial(correos_hilo) or correos_hilo[0]
        total_correos = len(correos_hilo)

        print(f"\n{'─' * 50}")
        print(f"📧 Procesando hilo: {correo_principal.subject[:50]}...")
        print(f"   Correos en el hilo: {total_correos}")
        print(f"   Remitente original: {correo_origen.remitente_email}")
        if total_correos > 1:
            for i, c in enumerate(correos_hilo):
                print(f"      [{i+1}] {c.remitente_email} - {c.fecha_recibido.strftime('%Y-%m-%d %H:%M')}")

        # 1. Validar que el hilo provenga de un despacho judicial.
        #    No basta con que algun correo del hilo tenga dominio permitido: el
        #    buzon interno aparece en casi todos los hilos por los reenvios, y
        #    aceptarlo como origen deja pasar correos de particulares reenviados
        #    internamente. La autorizacion la da unicamente la Rama Judicial.
        op.paso(1, "Validar remitente")
        correo_judicial = self.buscar_correo_judicial(correos_hilo)
        if not correo_judicial:
            dominios = sorted({c.get_dominio_remitente() for c in correos_hilo})
            op.fallo("Validar remitente",
                     f"ningun correo proviene de la Rama Judicial: {dominios}")
            self._mover_hilo_fallido(correos_hilo, "DOMINIO-DE-CORREO-INVALIDO")
            return False
        correo_origen = correo_judicial
        op.ok("Remitente autorizado", correo_judicial.remitente_email)

        # 2. Detectar si es tutela - verificar en CUALQUIER correo del hilo.
        #    Si el hilo no nombra el tramite pero notifica una actuacion procesal,
        #    se continua: son correos en que el despacho comunica una decision sin
        #    escribir "tutela". La confirmacion viene del radicado (paso 3), que
        #    debe ser un numero valido de 23 digitos; si no aparece, el hilo se
        #    descarta igual, ahora indicando que no se reconocio el tramite.
        op.paso(2, "Identificar tipo de tramite")
        es_tutela = any(self.detectar_tutela(c) for c in correos_hilo)
        es_actuacion = any(self.detectar_actuacion_judicial(c) for c in correos_hilo)
        if not es_tutela and not es_actuacion:
            op.fallo("Identificar tipo de tramite",
                     "el hilo no menciona tutela ni notifica una actuacion procesal")
            self._mover_hilo_fallido(correos_hilo, "NO-ES-TUTELA")
            return False
        if es_tutela:
            op.ok("Tramite identificado", "accion de tutela")
        else:
            op.ok("Tramite identificado",
                  "actuacion procesal de despacho judicial, sujeta a radicado valido")

        # 2.5 Correos que remiten el expediente por enlace: la documentacion vive
        #     detras del enlace, por lo que el hilo no trae lo que debe radicarse.
        #     Con RADICAR_ENLACE_CON_ADJUNTOS activo se conserva el comportamiento
        #     anterior de radicar cuando el hilo si anexa los PDF.
        enlace_sharepoint = self._buscar_enlace_sharepoint(correos_hilo)
        adjuntos_sharepoint = []
        radicado_sharepoint = None

        if enlace_sharepoint:
            tiene_pdf_adjunto = False
            for _correo in correos_hilo:
                try:
                    for _adj in self.outlook.obtener_adjuntos(_correo.id):
                        # Se exige documentacion del despacho, con el mismo criterio
                        # del paso 5: una firma incrustada o cualquier otro archivo
                        # accesorio no sustituye al expediente que vive tras el enlace.
                        if _adj.es_imagen_outlook():
                            continue
                        if _adj.es_documento_expediente():
                            tiene_pdf_adjunto = True
                            break
                except Exception as e:
                    op.fallo("Revisar anexos del correo", f"{type(e).__name__}: {e}")
                if tiene_pdf_adjunto:
                    break

            if not tiene_pdf_adjunto:
                op.fallo("Verificar anexos", "la documentacion solo esta en un enlace externo")
                self._mover_hilo_fallido(correos_hilo, "CORREO-CON-ENLACE")
                return False
            if not RADICAR_ENLACE_CON_ADJUNTOS:
                op.fallo("Verificar anexos",
                         "el expediente se remite por enlace, aunque el hilo traiga anexos")
                self._mover_hilo_fallido(correos_hilo, "CORREO-CON-ENLACE")
                return False
            op.ok("Verificar anexos", "el hilo trae PDFs adjuntos, se ignora el enlace")

        # TODO: Descarga de SharePoint deshabilitada temporalmente
        # if enlace_sharepoint:
        #     print(f"   📂 Enlace SharePoint detectado")
        #     adjuntos_sharepoint, radicado_sharepoint = self.sharepoint.descargar_expediente(enlace_sharepoint)
        #     if adjuntos_sharepoint:
        #         print(f"   ✅ {len(adjuntos_sharepoint)} documentos descargados de SharePoint")
        #     else:
        #         print(f"   ⚠️ No se pudieron descargar documentos de SharePoint")

        # 3. Extraer radicado con AI (del correo principal, luego fallback en todo el hilo)
        op.paso(3, "Extraer numero de radicado")
        datos = self.ai.extraer_todo(
            correo_principal.subject,
            correo_principal.remitente_email,
            correo_principal.body_text
        )
        correo_principal.datos_extraidos = datos

        # Texto de los PDFs leidos en esta fase; se reutiliza para la huella del paso 6.
        textos_pdf = {}

        if not datos.es_valido or not self._es_radicado_valido(datos.radicado_23):
            radicado_encontrado = None
            origen_radicado = ""

            # Fallback 1: usar radicado extraído del path de SharePoint
            if radicado_sharepoint:
                radicado_encontrado = radicado_sharepoint
                origen_radicado = "ruta de SharePoint"

            # Fallback 2: regex en texto del correo (soporta guiones y espacios)
            if not radicado_encontrado:
                texto_principal = f"{correo_principal.subject} {correo_principal.body_text}"
                radicado_encontrado = self.extraer_radicado_regex(texto_principal)
                if radicado_encontrado:
                    origen_radicado = "cuerpo del correo"
                else:
                    for c in correos_hilo:
                        texto_c = f"{c.subject} {c.body_text}"
                        radicado_encontrado = self.extraer_radicado_regex(texto_c)
                        if radicado_encontrado:
                            origen_radicado = "otro correo del hilo"
                            break

            # Fallback 3: buscar radicado en los PDFs adjuntos.
            # 3a) texto + regex (barato). 3b) si no, Gemini multimodal (lee el PDF con
            #     texto o escaneado; devuelve solo radicados de 23 digitos validados).
            if not radicado_encontrado:
                op.paso("3b", "Buscar radicado en los anexos PDF")
                for correo in correos_hilo:
                    lista_adj = self.outlook.obtener_adjuntos(correo.id)
                    for adj in lista_adj:
                        if not adj.nombre.lower().endswith('.pdf'):
                            continue
                        contenido = self.outlook.descargar_adjunto(correo.id, adj.id)
                        # 3a) texto + regex
                        texto_pdf = self._extraer_texto_pdf(contenido, adj.nombre)
                        if texto_pdf:
                            textos_pdf[adj.nombre] = texto_pdf
                            radicado_encontrado = self.extraer_radicado_regex(texto_pdf)
                            if radicado_encontrado:
                                origen_radicado = f"anexo {adj.nombre}"
                                break
                        # 3b) fallback multimodal (PDF con texto o escaneado).
                        #     El multimodal LEE el documento -> se cruza contra el radicado
                        #     corto del correo para descartar un numero mal leido/ambiguo.
                        #     Si el correo no trae corto, se acepta (mejor que mandar a manual).
                        rad_mm = self.ai.extraer_radicado_de_pdf(contenido)
                        if rad_mm:
                            texto_ref = f"{correo_principal.subject} {correo_principal.body_text}"
                            if self._radicado_coincide_con_corto(rad_mm, texto_ref):
                                radicado_encontrado = rad_mm
                                origen_radicado = f"lectura del anexo {adj.nombre}"
                                break
                            op.omitido("Radicado leido del anexo",
                                       f"{rad_mm} no coincide con la referencia del correo")
                    if radicado_encontrado:
                        break

            if radicado_encontrado and self._es_radicado_valido(radicado_encontrado):
                datos.radicado_23 = radicado_encontrado
                datos.codigo_juzgado = radicado_encontrado[:12]
                datos.es_valido = True
                op.ok("Radicado obtenido", f"{radicado_encontrado} ({origen_radicado})")
            else:
                # Sin radicado no se puede radicar. Si ademas el hilo nunca nombro el
                # tramite (entro solo por ser una actuacion procesal), lo que falta es
                # la confirmacion de que se trata de una tutela.
                if es_tutela:
                    op.fallo("Extraer numero de radicado",
                             "no aparece en el correo ni en los anexos")
                    self._mover_hilo_fallido(correos_hilo, "SIN-RADICADO-ENCONTRADO")
                else:
                    op.fallo("Identificar tipo de tramite",
                             "actuacion procesal sin radicado que confirme la tutela")
                    self._mover_hilo_fallido(correos_hilo, "NO-ES-TUTELA")
                return False
        else:
            op.ok("Radicado obtenido", f"{datos.radicado_23} (lectura del correo)")

        op.ok("Codigo de juzgado", datos.codigo_juzgado)
        tipo_doc = datos.tipo_documento or "CC"
        num_doc = datos.numero_documento or "NO_ENCONTRADO"
        fuente = datos.fuente_documento or "N/A"
        op.ok("Afectado identificado", f"{tipo_doc} {num_doc} (fuente: {fuente})")

        # 4. Buscar juzgado en base de datos
        op.paso(4, "Ubicar juzgado", datos.codigo_juzgado)
        juzgado = self.juzgados.buscar_juzgado(
            codigo=datos.codigo_juzgado,
            nombre=datos.juzgado_nombre_ocr,
            ciudad=datos.ciudad
        )

        if not juzgado:
            op.fallo("Ubicar juzgado", f"codigo {datos.codigo_juzgado} sin registro")
            self._mover_hilo_fallido(correos_hilo, "JUZGADO-NO-ENCONTRADO",
                                     radicado_23=datos.radicado_23)
            return False

        correo_principal.juzgado = juzgado
        op.ok("Juzgado ubicado", juzgado.nombre)

        # 5. Recopilar adjuntos: SharePoint + correos del hilo (deduplicados).
        #    Se recopilan antes del claim porque la llave de dedup se calcula sobre el
        #    contenido de estos documentos (paso 6).
        op.paso(5, "Recopilar anexos")
        adjuntos_para_radicar = []
        nombres_vistos = set()

        # 5a. Primero agregar documentos descargados de SharePoint (si los hay)
        if adjuntos_sharepoint:
            for nombre, contenido, ct in adjuntos_sharepoint:
                adjuntos_para_radicar.append((nombre, contenido, ct))
                nombres_vistos.add(nombre)
                print(f"      - {nombre} ({len(contenido)//1024} KB) [SharePoint]")

        # 5b. Luego agregar adjuntos directos de los correos (deduplicados)
        for idx, correo in enumerate(correos_hilo):
            lista_adjuntos = self.outlook.obtener_adjuntos(correo.id)
            correo.adjuntos = lista_adjuntos

            for adj in correo.get_adjuntos_validos():
                if adj.nombre in nombres_vistos:
                    origen = f" (correo {idx+1})" if total_correos > 1 else ""
                    print(f"      - {adj.nombre} [repetido en el hilo, omitido]{origen}")
                    continue

                contenido = self.outlook.descargar_adjunto(correo.id, adj.id)
                adj.contenido = contenido
                adjuntos_para_radicar.append((adj.nombre, contenido, adj.content_type))
                nombres_vistos.add(adj.nombre)
                origen = f" (correo {idx+1})" if total_correos > 1 else ""
                print(f"      - {adj.nombre} ({len(contenido)} bytes){origen}")
        op.ok("Anexos recopilados", f"{len(adjuntos_para_radicar)} archivo(s)")

        # 5c. Un hilo sin documentacion del despacho no se radica. Mas abajo se
        #     agrega correo_completo.pdf, generado por el agente, de modo que sin
        #     este corte la radicacion quedaba creada con ese unico archivo y el
        #     expediente sin la actuacion que lo motiva.
        documentos_expediente = [
            nombre for nombre, _, _ in adjuntos_para_radicar
            if nombre.lower().endswith(".pdf")
        ]
        if not documentos_expediente:
            op.fallo("Verificar anexos",
                     "el hilo no trae documentacion del despacho")
            self._mover_hilo_fallido(correos_hilo, "SIN-DOCUMENTACION-ADJUNTA",
                                     radicado_23=datos.radicado_23)
            return False

        # 6. Reclamar ATOMICAMENTE el caso (claim SET NX).
        #    La llave combina el radicado con dos huellas de los documentos anexos:
        #    la del contenido binario y la del texto normalizado. La segunda reconoce
        #    el mismo documento cuando el juzgado lo reenvia regenerado o escaneado de
        #    nuevo, caso en el que el binario cambia pero el texto se mantiene.
        #    Una actuacion nueva del proceso trae otro documento, otro texto y otra
        #    llave, por lo que si se radica.
        #    Si los anexos no permiten calcular ninguna huella se usa el id del correo,
        #    para no bloquear el caso.
        op.paso(6, "Verificar si la documentacion ya fue radicada")
        clave_dedup, criterio = self._clave_dedup(datos.radicado_23, adjuntos_para_radicar,
                                                  textos_pdf, correo_principal.id)
        hash_value, reclamado = self.redis.reclamar(clave_dedup)
        correo_principal.hash_correo = hash_value
        correo_principal.es_duplicado = not reclamado

        if not reclamado:
            op.fallo("Verificar documentacion", f"ya radicada ({criterio})")
            self._mover_hilo_fallido(correos_hilo, "RADICADO-DUPLICADO",
                                     radicado_23=datos.radicado_23)
            return False

        # Ademas del conjunto completo se reclama cada documento por separado, para
        # reconocer el caso cuando el mismo proceso llega en dos correos con distinta
        # cantidad de anexos. Si algun documento ya estaba radicado se libera lo
        # reclamado en este hilo y se trata como repetido.
        claves_doc = self._claves_por_documento(datos.radicado_23, adjuntos_para_radicar,
                                                textos_pdf)
        reclamadas = []
        repetido = None
        for clave in claves_doc:
            h, ok = self.redis.reclamar(clave)
            if ok:
                reclamadas.append(h)
            else:
                repetido = clave
                break

        if repetido:
            self.redis.liberar(hash_value)
            for h in reclamadas:
                self.redis.liberar(h)
            op.fallo("Verificar documentacion",
                     "uno de los documentos ya fue radicado en otro correo")
            self._mover_hilo_fallido(correos_hilo, "RADICADO-DUPLICADO",
                                     radicado_23=datos.radicado_23)
            return False

        correo_principal.hashes_documentos = reclamadas
        op.ok("Documentacion sin radicar previamente",
              f"{criterio}, {len(claves_doc)} documento(s) verificado(s)")

        # Generar PDF consolidado del hilo completo. Se agrega despues del claim para
        # que no entre en la huella de dedup: incluye fecha y remitente del hilo, por
        # lo que difiere entre correos que traen el mismo documento.
        nombres_adjuntos = [adj[0] for adj in adjuntos_para_radicar]
        pdf_hilo = self.sgdea._generar_pdf_hilo(correos_hilo, nombres_adjuntos)
        adjuntos_para_radicar.append(("correo_completo.pdf", pdf_hilo, "application/pdf"))
        print(f"      - correo_completo.pdf ({len(pdf_hilo)} bytes) [PDF consolidado del hilo]")

        # 7. RADICAR - Enviando archivos INDIVIDUALMENTE (no ZIP)
        #    Usar correo_origen (juzgado) como remitente, no el reenvío interno
        op.paso(7, "Radicar en SGDEA", f"{len(adjuntos_para_radicar)} archivo(s)")
        exito, respuesta, error, incierto = self.sgdea.radicar_tutela(
            correo=correo_origen,
            juzgado=juzgado,
            radicado_23=datos.radicado_23,
            adjuntos=adjuntos_para_radicar
        )

        if not exito:
            correo_principal.error_radicacion = error
            if incierto:
                # Radicacion AMBIGUA: el registro PUDO haberse creado. No se reintenta
                # y no se libera el claim, para que la siguiente corrida tampoco lo
                # re-radique. Se usa una etiqueta propia porque el mensaje al area es
                # distinto: no se puede afirmar que la tutela quedo sin radicar.
                op.fallo("Radicar en SGDEA",
                         f"resultado no confirmado, no se reintenta: {error}")
                etiqueta = "RADICACION-POR-CONFIRMAR"
            else:
                # Fallo CLARO (sin registro creado): se libera el claim para poder
                # reintentar en una proxima corrida.
                op.fallo("Radicar en SGDEA", str(error))
                self.redis.liberar(hash_value)
                for h in correo_principal.hashes_documentos:
                    self.redis.liberar(h)
                op.ok("Documentacion liberada para reintento en la proxima corrida")
                etiqueta = "ERROR-RADICAR"
            self._mover_hilo_fallido(correos_hilo, etiqueta,
                                     radicado_23=datos.radicado_23)
            return False

        correo_principal.radicado_exitoso = True

        # 8. Obtener el número de radicado de la respuesta
        numero_radicado = respuesta.get("radicado") if respuesta else None
        if numero_radicado:
            op.radicado(numero_radicado, datos.radicado_23, len(adjuntos_para_radicar))
        else:
            op.fallo("Obtener numero de radicado", "la respuesta no lo incluye")

        # 9. Consultar info del radicado para obtener idTutela e idRadicado
        if numero_radicado:
            op.paso(8, "Registrar trazabilidad")
            info_radicado = self.sgdea.obtener_info_radicado(numero_radicado)
            if info_radicado:
                id_tutela = info_radicado.get("idTutela")
                id_radicado = info_radicado.get("idRadicado")

                # 10. Crear trazabilidad con los datos obtenidos
                if self.sgdea.crear_trazabilidad(id_tutela, id_radicado):
                    op.ok("Trazabilidad registrada", f"tutela {id_tutela} / {id_radicado}")
                else:
                    op.fallo("Trazabilidad", "no se registro el movimiento del tramite")
            else:
                op.fallo("Trazabilidad", "no se obtuvo la informacion del radicado")

        # 11. Responder al correo principal con confirmación de radicación
        if numero_radicado:
            op.paso(9, "Informar radicacion al buzon interno")
            if self.outlook.responder_radicacion_exitosa(correo_principal.id, numero_radicado):
                op.ok("Correo de confirmacion enviado", numero_radicado)
            else:
                op.fallo("Correo de confirmacion", "no se pudo enviar")

        # 12. Marcar como procesado en Redis
        self.redis.marcar_procesado(hash_value)

        # 13. Mover TODOS los correos del hilo a carpeta TUTELAS
        op.paso(10, "Archivar hilo en la carpeta TUTELAS")
        ids_hilo = [c.id for c in correos_hilo]
        movidos = self.outlook.mover_hilo_a_tutelas(ids_hilo)
        if movidos == total_correos:
            op.ok("Hilo archivado", f"{movidos}/{total_correos} correos")
        else:
            op.fallo("Archivar hilo", f"solo {movidos}/{total_correos} correos movidos")

        return True

    def _ejecutar_lote(self, limite_correos: int = 10, hilos_vistos: Optional[set] = None):
        """
        Ejecuta un lote de radicación.
        Retorna la cantidad de correos encontrados (0 = bandeja vacía).

        `hilos_vistos` acumula los hilos ya procesados en esta ejecucion, para no
        volver a tomarlos si siguen en la bandeja.
        """
        if hilos_vistos is None:
            hilos_vistos = set()

        print(f"\n{'=' * 60}")
        print(f"INICIANDO LOTE DE RADICACIÓN")
        print(f"Fecha/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Límite de correos: {limite_correos}")
        print(f"{'=' * 60}")

        # Obtener correos agrupados por hilo de conversación
        print("\n📬 Obteniendo correos de la bandeja de entrada...")
        hilos = self.outlook.obtener_correos_por_conversacion(limite=limite_correos)

        if not hilos:
            print("   No hay correos para procesar")
            return 0

        # Descartar los hilos que ya se trabajaron en esta ejecucion: si siguen en
        # la bandeja es porque no se pudieron mover, y volver a procesarlos deja el
        # correo con la etiqueta de un intento y el resultado de otro.
        pendientes = []
        repetidos = 0
        for hilo in hilos:
            clave = hilo[-1].conversation_id or hilo[-1].id
            if clave in hilos_vistos:
                repetidos += 1
                continue
            hilos_vistos.add(clave)
            pendientes.append(hilo)

        if repetidos:
            op.omitido("Hilos ya procesados en esta ejecucion", str(repetidos))
        if not pendientes:
            print("   No quedan hilos nuevos para procesar")
            return 0

        hilos = pendientes
        total_correos = sum(len(hilo) for hilo in hilos)
        print(f"   Total correos: {total_correos} en {len(hilos)} hilo(s)")

        # Procesar cada hilo
        exitosos = 0
        fallidos = 0

        for i, hilo in enumerate(hilos, 1):
            print(f"\n[Hilo {i}/{len(hilos)}] ({len(hilo)} correo(s))", end="")

            try:
                if self.procesar_hilo(hilo):
                    exitosos += 1
                else:
                    fallidos += 1
            except Exception as e:
                op.fallo("Procesar hilo", f"{type(e).__name__}: {e}")
                traceback.print_exc()
                fallidos += 1
                try:
                    self._mover_hilo_fallido(hilo, "ERROR-INESPERADO")
                except Exception as e2:
                    op.fallo("Cerrar hilo con error", f"{type(e2).__name__}: {e2}")

            # Pequeña pausa entre hilos para no saturar APIs
            time.sleep(1)

        # Resumen del lote
        print(f"\n{'─' * 40}")
        print(f"RESUMEN DEL LOTE")
        print(f"   Hilos procesados: {exitosos + fallidos}")
        print(f"   Correos: {total_correos}")
        print(f"   ✅ Exitosos: {exitosos}")
        print(f"   ❌ Fallidos: {fallidos}")
        print(f"{'─' * 40}")

        return total_correos

    def ejecutar(self, limite_correos: int = 10):
        """
        Ejecuta la radicación en lotes hasta que la bandeja quede vacía.
        Cada lote procesa hasta `limite_correos` correos.

        Antes de empezar reserva el buzon: si otra ejecucion sigue en curso, esta
        termina sin procesar. Dos ejecuciones sobre el mismo buzon se pisan, porque
        una mueve los correos mientras la otra trabaja con identificadores que ya
        cambiaron.

        MODO_UAT: como el correo no se mueve ni se marca en Redis (para no
        tocar el buzon real), el mismo lote nunca llegaria a 0 correos y este
        bucle nunca terminaria solo. Por eso se ejecuta UNA sola tanda y se
        detiene, sin importar cuantos correos haya procesado.
        """
        ejecucion = os.environ.get("CLOUD_RUN_EXECUTION") or f"local-{os.getpid()}"
        if not self.redis.tomar_cerrojo(ejecucion):
            op.omitido("Ejecucion",
                       f"otra corrida sigue procesando el buzon ({self.redis.titular_cerrojo()})")
            return

        lote = 0
        total_correos_global = 0
        bandeja_vacia = False
        # Hilos ya vistos en esta ejecucion. Un hilo que no se pudo mover sigue en
        # la bandeja y volveria a salir en el lote siguiente, quedando con la
        # etiqueta del rechazo y una radicacion posterior sobre el mismo correo.
        hilos_vistos = set()

        try:
            while True:
                lote += 1
                print(f"\n{'#' * 60}")
                print(f"### LOTE {lote}")
                print(f"{'#' * 60}")

                correos_en_lote = self._ejecutar_lote(limite_correos=limite_correos,
                                                      hilos_vistos=hilos_vistos)
                total_correos_global += correos_en_lote

                if correos_en_lote == 0:
                    bandeja_vacia = True
                    break

                if MODO_UAT:
                    print(f"\nMODO_UAT: una sola tanda por ejecución, deteniendo aunque la bandeja no esté vacía.")
                    break

                print(f"\n⏳ Pausa de 5 segundos antes del siguiente lote...")
                time.sleep(5)
        finally:
            self.redis.soltar_cerrojo(ejecucion)

        # Resumen global
        print(f"\n{'=' * 60}")
        print(f"RESUMEN FINAL - TODOS LOS LOTES")
        print(f"{'=' * 60}")
        print(f"   Lotes ejecutados: {lote}")
        print(f"   Total correos procesados: {total_correos_global}")
        print(f"   Bandeja vacía: {'✅' if bandeja_vacia else '❌ (MODO_UAT: se detuvo tras una tanda)'}")
        print(f"{'=' * 60}")

    def cerrar(self):
        """Cierra las conexiones"""
        self.redis.close()
        self.sgdea.close()
        self.juzgados.close()


def main():
    """Punto de entrada principal"""
    orquestador = OrquestadorRadicacion()

    try:
        orquestador.ejecutar(limite_correos=50)
    finally:
        orquestador.cerrar()


if __name__ == "__main__":
    main()
