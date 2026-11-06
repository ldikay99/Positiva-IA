"""
Servicio para radicación en SGDEA API
Permite subir archivos INDIVIDUALMENTE (sin ZIP)
"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import json
import time

try:
    from Tutelas.config import SGDEA_CONFIG, MODO_TEST, DISCLAIMER_IA
    from Tutelas.test_recorder import get_recorder
    from Tutelas.models.correo import Correo, Adjunto, Juzgado
except ImportError:
    from config import SGDEA_CONFIG, MODO_TEST, DISCLAIMER_IA
    from test_recorder import get_recorder
    from models.correo import Correo, Adjunto, Juzgado


class SGDEAService:
    def __init__(self):
        self.config = SGDEA_CONFIG
        self.access_token = None
        self.token_expires = None

        # Configurar session con pool de conexiones limitado
        self.session = requests.Session()

        # Limitar pool de conexiones para no saturar
        adapter = HTTPAdapter(
            pool_connections=5,      # Máximo 5 conexiones en pool
            pool_maxsize=10,         # Máximo 10 conexiones totales
            max_retries=Retry(
                total=0,             # No usar retries automáticos de urllib3
                backoff_factor=0
            )
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

        print(f"   [SGDEA] API URL: {self.config['api_url']}")
        print(f"   [SGDEA] Token URL: {self.config['token_url']}")
        print(f"   [SGDEA] Pool conexiones: 5 (max 10)")

    def _get_token(self) -> str:
        """Obtiene o renueva el token de acceso"""
        if self.access_token and self.token_expires and datetime.now() < self.token_expires:
            return self.access_token

        print(f"      [SGDEA] Obteniendo token desde: {self.config['token_url']}")

        data = {
            "grant_type": "password",
            "client_id": self.config["client_id"],
            "client_secret": self.config["client_secret"],
            "username": self.config["username"],
            "password": self.config["password"],
        }

        response = self.session.post(self.config["token_url"], data=data, timeout=30)
        print(f"      [SGDEA] Token response status: {response.status_code}")

        if response.status_code != 200:
            print(f"      [SGDEA] Token error: {response.text[:300]}")

        response.raise_for_status()

        token_data = response.json()
        self.access_token = token_data["access_token"]

        expires_in = token_data.get("expires_in", 3600) - 300
        self.token_expires = datetime.now() + timedelta(seconds=expires_in)

        print(f"      [SGDEA] Token obtenido (expira en {expires_in}s)")
        return self.access_token

    def _headers(self) -> dict:
        """Headers base para las peticiones"""
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }

    def consultar_persona(self, tipo_documento: str, numero_documento: str, max_reintentos: int = 3) -> Optional[dict]:
        """
        Consulta si una persona existe en el sistema.
        Incluye reintentos automáticos en caso de fallo.
        """
        url = f"{self.config['api_url']}/personas"

        params = {
            "tipoDocumento": tipo_documento,
            "numeroDocumento": numero_documento,
        }

        print(f"\n      ╔══════════════════════════════════════════════════════════")
        print(f"      ║ [SGDEA] CONSULTA PERSONA/AFECTADO")
        print(f"      ╠══════════════════════════════════════════════════════════")
        print(f"      ║ URL: {url}")
        print(f"      ║ Tipo Documento: {tipo_documento}")
        print(f"      ║ Número Documento: {numero_documento}")
        print(f"      ╚══════════════════════════════════════════════════════════")

        for intento in range(1, max_reintentos + 1):
            try:
                print(f"      [SGDEA] Intento {intento}/{max_reintentos}...")
                response = self.session.get(
                    url,
                    headers=self._headers(),
                    params=params,
                    timeout=30
                )

                print(f"      [SGDEA] Status: {response.status_code}")
                print(f"      [SGDEA] Response: {response.text[:500] if response.text else '(vacío)'}")

                if response.status_code == 200:
                    if response.text.strip():
                        data = response.json()
                        print(f"      [SGDEA] ✅ Persona encontrada")
                        return data
                    else:
                        print(f"      [SGDEA] Respuesta vacía")
                        return None
                elif response.status_code == 404:
                    print(f"      [SGDEA] Persona no encontrada en el sistema")
                    return None
                else:
                    print(f"      [SGDEA] Error HTTP {response.status_code}, reintentando...")

            except requests.exceptions.Timeout:
                print(f"      [SGDEA] ⚠️ Timeout en intento {intento}")
            except requests.exceptions.ConnectionError as e:
                print(f"      [SGDEA] ⚠️ Error de conexión en intento {intento}: {e}")
            except json.JSONDecodeError as e:
                print(f"      [SGDEA] ⚠️ Error parseando JSON: {e}")
                print(f"      [SGDEA] Contenido: {response.text[:200] if response.text else '(vacío)'}")
            except Exception as e:
                print(f"      [SGDEA] ⚠️ Error en intento {intento}: {type(e).__name__}: {e}")

            # Esperar antes de reintentar (backoff exponencial)
            if intento < max_reintentos:
                wait_time = 2 ** intento  # 2, 4, 8 segundos
                print(f"      [SGDEA] Esperando {wait_time}s antes de reintentar...")
                time.sleep(wait_time)

        print(f"      [SGDEA] ❌ Falló después de {max_reintentos} intentos")
        return None

    def _generar_pdf_correo(self, correo: Correo, nombres_adjuntos: List[str]) -> bytes:
        """Genera un PDF del correo para adjuntar como archivo"""
        from io import BytesIO
        from fpdf import FPDF

        fecha_formateada = correo.fecha_recibido_local.strftime("%d/%m/%Y %H:%M:%S")

        # Crear PDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)

        # Título
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(0, 114, 198)  # Azul
        pdf.cell(0, 10, "Correo Electronico", ln=True)

        pdf.ln(5)

        # Asunto
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(0, 0, 0)
        # Limpiar caracteres no soportados
        asunto_limpio = correo.subject.encode('latin-1', 'replace').decode('latin-1')
        pdf.multi_cell(0, 8, asunto_limpio)

        pdf.ln(3)
        pdf.set_draw_color(200, 200, 200)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(5)

        # Metadatos
        pdf.set_font("Helvetica", "", 10)

        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(25, 6, "De:", ln=False)
        pdf.set_font("Helvetica", "", 10)
        remitente = f"{correo.remitente_nombre} <{correo.remitente_email}>"
        remitente_limpio = remitente.encode('latin-1', 'replace').decode('latin-1')
        pdf.cell(0, 6, remitente_limpio, ln=True)

        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(25, 6, "Enviado:", ln=False)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, fecha_formateada, ln=True)

        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(25, 6, "Asunto:", ln=False)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, asunto_limpio)

        # Adjuntos
        if nombres_adjuntos:
            pdf.ln(5)
            pdf.set_fill_color(245, 245, 245)
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 8, f"  {len(nombres_adjuntos)} archivo(s) adjunto(s):", ln=True, fill=True)
            pdf.set_font("Helvetica", "", 9)
            for i, nombre in enumerate(nombres_adjuntos):
                nombre_limpio = nombre.encode('latin-1', 'replace').decode('latin-1')
                pdf.cell(0, 6, f"    {i+1}. {nombre_limpio}", ln=True, fill=True)

        # Separador
        pdf.ln(5)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(5)

        # Cuerpo del correo
        pdf.set_font("Helvetica", "", 10)
        # Limpiar el texto del cuerpo
        cuerpo_limpio = correo.body_text.encode('latin-1', 'replace').decode('latin-1')
        # Dividir en líneas para evitar problemas con textos largos
        for linea in cuerpo_limpio.split('\n'):
            if linea.strip():
                pdf.multi_cell(0, 5, linea.strip())
            else:
                pdf.ln(3)

        # Footer
        pdf.ln(10)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(3)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(100, 100, 100)
        _disclaimer_ia = DISCLAIMER_IA.encode("latin-1", "replace").decode("latin-1")
        pdf.multi_cell(0, 4, _disclaimer_ia, align="C")

        # Obtener bytes del PDF
        pdf_bytes = pdf.output()
        return bytes(pdf_bytes)

    def _generar_pdf_hilo(self, correos: list, nombres_adjuntos: list) -> bytes:
        """Genera un PDF consolidado con todos los correos de un hilo de conversación"""
        from io import BytesIO
        from fpdf import FPDF

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)

        # Página de portada
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(0, 114, 198)
        pdf.cell(0, 10, "Hilo de Correo Electronico", ln=True)
        pdf.ln(3)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 7, f"Total de mensajes en el hilo: {len(correos)}", ln=True)
        pdf.cell(0, 7, f"Total de adjuntos: {len(nombres_adjuntos)}", ln=True)
        pdf.ln(5)

        # Lista de adjuntos consolidada
        if nombres_adjuntos:
            pdf.set_fill_color(245, 245, 245)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(0, 8, f"  {len(nombres_adjuntos)} archivo(s) adjunto(s) en total:", ln=True, fill=True)
            pdf.set_font("Helvetica", "", 9)
            for i, nombre in enumerate(nombres_adjuntos):
                nombre_limpio = nombre.encode('latin-1', 'replace').decode('latin-1')
                pdf.cell(0, 6, f"    {i+1}. {nombre_limpio}", ln=True, fill=True)

        # Cada correo del hilo (orden cronológico)
        for idx, correo in enumerate(correos):
            pdf.add_page()
            fecha_formateada = correo.fecha_recibido_local.strftime("%d/%m/%Y %H:%M:%S")

            # Encabezado del mensaje
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(0, 114, 198)
            pdf.cell(0, 8, f"Mensaje {idx + 1} de {len(correos)}", ln=True)

            pdf.ln(3)
            pdf.set_draw_color(0, 114, 198)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)

            # Asunto
            pdf.set_font("Helvetica", "B", 13)
            pdf.set_text_color(0, 0, 0)
            asunto_limpio = correo.subject.encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 7, asunto_limpio)
            pdf.ln(3)

            # Metadatos
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(25, 6, "De:", ln=False)
            pdf.set_font("Helvetica", "", 10)
            remitente = f"{correo.remitente_nombre} <{correo.remitente_email}>"
            remitente_limpio = remitente.encode('latin-1', 'replace').decode('latin-1')
            pdf.cell(0, 6, remitente_limpio, ln=True)

            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(25, 6, "Fecha:", ln=False)
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(0, 6, fecha_formateada, ln=True)

            # Separador
            pdf.ln(3)
            pdf.set_draw_color(200, 200, 200)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)

            # Cuerpo del correo
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(0, 0, 0)
            cuerpo_limpio = correo.body_text.encode('latin-1', 'replace').decode('latin-1')
            for linea in cuerpo_limpio.split('\n'):
                if linea.strip():
                    pdf.multi_cell(0, 5, linea.strip())
                else:
                    pdf.ln(3)

        # Footer
        pdf.ln(10)
        pdf.set_draw_color(200, 200, 200)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(3)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(100, 100, 100)
        _disclaimer_ia = DISCLAIMER_IA.encode("latin-1", "replace").decode("latin-1")
        pdf.multi_cell(0, 4, _disclaimer_ia, align="C")

        pdf_bytes = pdf.output()
        return bytes(pdf_bytes)

    def radicar_tutela(
        self,
        correo: Correo,
        juzgado: Juzgado,
        radicado_23: str,
        adjuntos: List[Tuple[str, bytes, str]],  # [(nombre, contenido, content_type), ...]
        max_reintentos: int = 3
    ) -> Tuple[bool, Optional[dict], Optional[str], bool]:
        """
        Radica una tutela con archivos INDIVIDUALES (no ZIP).

        Args:
            correo: Objeto Correo con la información
            juzgado: Objeto Juzgado con los datos del despacho
            radicado_23: Código de proceso de 23 dígitos
            adjuntos: Lista de tuplas (nombre, contenido_bytes, content_type)

        Returns:
            (exito, respuesta_dict, error_mensaje, incierto)
            incierto=True cuando la radicacion es AMBIGUA (el backend PUDO haber creado
            el radicado: timeout de lectura o 5xx). En ese caso NO se reintenta ni se
            debe re-radicar en la proxima corrida -> verificacion manual.
        """
        # MODO_TEST: no radica en SGDEA; graba en CSV y devuelve respuesta stub.
        if MODO_TEST:
            get_recorder().registrar_radicacion(correo, juzgado, radicado_23, n_adjuntos=len(adjuntos))
            return (True, {"idRadicado": "DRY-RUN-TUT", "id": 0}, None, False)

        url = f"{self.config['api_url']}/tutela"

        # Preparar el body JSON (hora Colombia, no UTC de Graph)
        fecha_correo = correo.fecha_recibido_local.strftime("%Y-%m-%d")
        hora_correo = correo.fecha_recibido_local.strftime("%H:%M")

        # Obtener teléfono y celular del juzgado (con valores por defecto)
        telefono_juzgado = juzgado.telefono if juzgado.telefono and str(juzgado.telefono) != "nan" else "Vacio"
        celular_juzgado = juzgado.celular if juzgado.celular and str(juzgado.celular) != "nan" else "3000000000"

        # El correo del remitente es el correo del juzgado que envió la notificación
        correo_remitente = correo.remitente_email

        # Datos del afectado (extraídos por AI)
        datos_afectado = correo.datos_extraidos
        tipo_documento_afectado = datos_afectado.tipo_documento if datos_afectado and datos_afectado.tipo_documento else "CC"
        numero_documento_afectado = datos_afectado.numero_documento if datos_afectado and datos_afectado.numero_documento else ""

        body_data = {
            "tramite": "TUTELAS",
            "canal": "Mail-IA",
            "juzgado": {
                "nombre": juzgado.nombre,
                "direccion": juzgado.direccion if juzgado.direccion and str(juzgado.direccion) != "nan" else "",
                "pais": "Colombia",
                "departamento": juzgado.departamento,
                "municipio": juzgado.municipio,
                "codigo": juzgado.codigo,
                "telefono": telefono_juzgado,
                "celular": celular_juzgado,
                "correo": correo_remitente,
                "estado": True
            },
            "codigoProceso": radicado_23,
            "fecha": fecha_correo,
            "hora": hora_correo,
            "descripcion": "Radicacion Automatica con IA",
            "idRadicado": "",
            "nodeId": None,
            "pais": "Colombia",
            "departamento": juzgado.departamento,
            "municipio": juzgado.municipio,
            "usuario": "Radicador IA Tutela",
            "correoRemitente": correo_remitente,
            "telefonoRemitente": telefono_juzgado,
            "celularRemitente": celular_juzgado,
            # Datos del afectado/accionante
            "tipoDocumentoAfectado": tipo_documento_afectado,
            "numeroDocumentoAfectado": numero_documento_afectado
        }

        # Preparar los archivos para multipart/form-data
        # IMPORTANTE: Aquí enviamos cada archivo INDIVIDUALMENTE
        files = []

        # Agregar cada adjunto como un archivo separado
        for nombre, contenido, content_type in adjuntos:
            files.append(
                ('archivos', (nombre, contenido, content_type))
            )

        # El body va como campo de formulario
        data = {
            'body': json.dumps(body_data)
        }

        print(f"\n      ╔══════════════════════════════════════════════════════════")
        print(f"      ║ [SGDEA] RADICACIÓN REQUEST")
        print(f"      ╠══════════════════════════════════════════════════════════")
        print(f"      ║ URL: {url}")
        print(f"      ║ Archivos: {[adj[0] for adj in adjuntos]}")
        print(f"      ╠══════════════════════════════════════════════════════════")
        print(f"      ║ BODY PAYLOAD:")
        print(f"      ╠══════════════════════════════════════════════════════════")
        # Imprimir el body formateado
        body_json_formatted = json.dumps(body_data, indent=2, ensure_ascii=False)
        for line in body_json_formatted.split('\n'):
            print(f"      ║ {line}")
        print(f"      ╚══════════════════════════════════════════════════════════\n")

        # Solo se REINTENTA cuando es SEGURO (el backend NO proceso la peticion):
        #   401 (token vencido), 429/503 (rechazo por carga), ConnectTimeout/
        #   ConnectionError (no llego al servidor).
        # Los casos AMBIGUOS (el backend PUDO haber creado el radicado) NO se
        # reintentan -> incierto=True (verificacion manual): ReadTimeout (se envio
        # sin respuesta), 408/500/502/504/522. Esto evita el duplicado por reintento.
        #
        # El 409 tambien es AMBIGUO: lo devuelve el backend cuando un componente
        # interno no esta disponible, y en ese escenario el registro puede haberse
        # creado igual. Tratarlo como fallo limpio hacia que se liberara el claim,
        # se avisara que no se radico y la corrida siguiente lo radicara de nuevo.
        REINTENTABLE_SEGURO = [429, 503]
        AMBIGUO = [408, 409, 500, 502, 504, 522]

        for intento in range(1, max_reintentos + 1):
            try:
                print(f"      [SGDEA] Intento {intento}/{max_reintentos}...")

                # Reconstruir files para cada intento (se consume en cada request)
                files_intento = []
                for nombre, contenido, content_type in adjuntos:
                    files_intento.append(('archivos', (nombre, contenido, content_type)))

                response = self.session.post(
                    url,
                    headers=self._headers(),
                    data=data,
                    files=files_intento,
                    timeout=(15, 150)  # (conexion 15s, lectura 150s) — menos timeouts espurios
                )

                print(f"\n      ╔══════════════════════════════════════════════════════════")
                print(f"      ║ [SGDEA] RADICACIÓN RESPONSE (intento {intento})")
                print(f"      ╠══════════════════════════════════════════════════════════")
                print(f"      ║ Status Code: {response.status_code}")
                print(f"      ║ Response Body:")
                for line in (response.text or '(vacío)').split('\n')[:20]:
                    print(f"      ║   {line[:100]}")
                print(f"      ╚══════════════════════════════════════════════════════════\n")

                if response.status_code in [200, 201]:
                    return True, response.json(), None, False
                elif response.status_code == 401:
                    self.access_token = None  # forzar refresh de token
                    print(f"      [SGDEA] 401 token vencido, refrescando y reintentando...")
                elif response.status_code in REINTENTABLE_SEGURO:
                    print(f"      [SGDEA] {response.status_code} (backend ocupado, no proceso), reintentando...")
                elif response.status_code in AMBIGUO:
                    # El backend PUDO haber creado el radicado -> NO reintentar (evita duplicado)
                    print(f"      [SGDEA] ⚠️ {response.status_code} AMBIGUO: pudo haber radicado -> NO se reintenta (verificacion manual)")
                    return False, None, f"Respuesta ambigua {response.status_code}: {response.text[:200]}", True
                else:
                    # 4xx claro (400/403/404/...): el backend rechazo la peticion
                    # sin crear registro.
                    return False, None, f"Error {response.status_code}: {response.text}", False

            except requests.exceptions.ConnectTimeout:
                print(f"      [SGDEA] Timeout de CONEXION en intento {intento} (no llego al servidor), reintentando...")
            except requests.exceptions.ReadTimeout:
                # Se envio la peticion pero no hubo respuesta -> AMBIGUO -> NO reintentar
                print(f"      [SGDEA] ⚠️ Timeout de LECTURA en intento {intento}: se envio sin respuesta -> NO se reintenta (verificacion manual)")
                return False, None, "Timeout de lectura (ambiguo)", True
            except requests.exceptions.ConnectionError as e:
                print(f"      [SGDEA] Error de conexion en intento {intento} (no llego): {e}, reintentando...")
            except requests.exceptions.Timeout:
                print(f"      [SGDEA] ⚠️ Timeout (generico) en intento {intento} -> AMBIGUO, NO se reintenta")
                return False, None, "Timeout (ambiguo)", True
            except Exception as e:
                print(f"      [SGDEA] ⚠️ Exception en intento {intento}: {type(e).__name__}: {e}")

            # Esperar antes de reintentar (backoff exponencial) — solo casos seguros
            if intento < max_reintentos:
                wait_time = 3 ** intento  # 3, 9, 27 segundos
                print(f"      [SGDEA] Esperando {wait_time}s antes de reintentar...")
                time.sleep(wait_time)

        return False, None, f"Fallo despues de {max_reintentos} intentos", False

    def obtener_info_radicado(self, radicado: str, max_reintentos: int = 3) -> Optional[dict]:
        """
        Obtiene la información de un radicado existente.
        Incluye reintentos automáticos en caso de fallo.
        """
        url = f"{self.config['api_url']}/tutela/info/{radicado}"

        print(f"\n      ╔══════════════════════════════════════════════════════════")
        print(f"      ║ [SGDEA] CONSULTA INFO RADICADO")
        print(f"      ╠══════════════════════════════════════════════════════════")
        print(f"      ║ URL: {url}")
        print(f"      ╚══════════════════════════════════════════════════════════")

        for intento in range(1, max_reintentos + 1):
            try:
                print(f"      [SGDEA] Intento {intento}/{max_reintentos}...")
                response = self.session.get(url, headers=self._headers(), timeout=30)

                print(f"      ║ Status Code: {response.status_code}")
                print(f"      ║ Content-Type: {response.headers.get('Content-Type', 'N/A')}")
                print(f"      ║ Response Body: {response.text[:500] if response.text else '(vacío)'}")

                if response.status_code == 200:
                    if response.text.strip():
                        return response.json()
                    else:
                        print(f"      [SGDEA] Respuesta vacía del servidor")
                        return None
                elif response.status_code == 404:
                    print(f"      [SGDEA] Radicado no encontrado")
                    return None
                else:
                    print(f"      [SGDEA] Error HTTP {response.status_code}, reintentando...")

            except requests.exceptions.Timeout:
                print(f"      [SGDEA] ⚠️ Timeout en intento {intento}")
            except requests.exceptions.ConnectionError as e:
                print(f"      [SGDEA] ⚠️ Error de conexión en intento {intento}: {e}")
            except json.JSONDecodeError as e:
                print(f"      [SGDEA] ⚠️ Error parseando JSON: {e}")
                print(f"      [SGDEA] Contenido: {response.text[:200] if response.text else '(vacío)'}")
            except Exception as e:
                print(f"      [SGDEA] ⚠️ Error en intento {intento}: {type(e).__name__}: {e}")

            # Esperar antes de reintentar (backoff exponencial)
            if intento < max_reintentos:
                wait_time = 2 ** intento  # 2, 4, 8 segundos
                print(f"      [SGDEA] Esperando {wait_time}s antes de reintentar...")
                time.sleep(wait_time)

        print(f"      [SGDEA] ❌ Falló después de {max_reintentos} intentos")
        return None

    def crear_trazabilidad(
        self,
        id_tutela,
        id_radicado: str,
    ) -> bool:
        """Crea registro de trazabilidad para el radicado"""
        if MODO_TEST:
            get_recorder().registrar("TRAZABILIDAD", destino="SGDEA",
                                     detalle={"id_tutela": id_tutela, "id_radicado": id_radicado})
            return True

        url = f"{self.config['api_url']}/trazabilidad/crear"

        data = {
            "proceso": f"TUT{id_tutela}",
            "secuencia": id_radicado,
            "estado": "Por asignar",
            "descripcion": "Usuario: Radicador IA Tutela Rol: Radicador - Mail Descripción: Se ha radicado una Tutela",
            "comentario": "Radicado con IA",
            "nombre": "Radicador IA Tutela",
            "tramite": "Tutela",
            "accion": f"Radicador IA Tutela ha creado el trámite {id_radicado}"
        }

        print(f"\n      ╔══════════════════════════════════════════════════════════")
        print(f"      ║ [SGDEA] TRAZABILIDAD REQUEST")
        print(f"      ╠══════════════════════════════════════════════════════════")
        print(f"      ║ URL: {url}")
        print(f"      ╠══════════════════════════════════════════════════════════")
        print(f"      ║ BODY PAYLOAD:")
        print(f"      ╠══════════════════════════════════════════════════════════")
        body_json_formatted = json.dumps(data, indent=2, ensure_ascii=False)
        for line in body_json_formatted.split('\n'):
            print(f"      ║ {line}")
        print(f"      ╚══════════════════════════════════════════════════════════\n")

        try:
            response = self.session.post(url, headers=self._headers(), json=data, timeout=30)

            print(f"\n      ╔══════════════════════════════════════════════════════════")
            print(f"      ║ [SGDEA] TRAZABILIDAD RESPONSE")
            print(f"      ╠══════════════════════════════════════════════════════════")
            print(f"      ║ Status Code: {response.status_code}")
            print(f"      ║ Response Body: {response.text}")
            print(f"      ╚══════════════════════════════════════════════════════════\n")

            return response.status_code in [200, 201]
        except Exception as e:
            print(f"      [SGDEA] Error creando trazabilidad: {type(e).__name__}: {e}")
            return False

    def close(self):
        """Cierra la sesión HTTP"""
        if self.session:
            self.session.close()
            print("   [SGDEA] Sesión HTTP cerrada")
