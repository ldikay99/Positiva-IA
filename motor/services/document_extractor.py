"""
Servicio de extraccion de documentos hibrido
- Excel/CSV: extraccion directa con openpyxl/pandas
- PDFs nativos: PyMuPDF (fitz)
- PDFs escaneados/imagenes: OCR especializado
- Gemini: solo para interpretacion final
"""

import io
import sys
from typing import Optional, Tuple, List
from pathlib import Path

import httpx

from config import settings
from services.gemini import GeminiService, DOCUMENT_ANALYSIS_PROMPT

print("[DOC_EXTRACTOR] Servicio de extraccion cargado", file=sys.stderr)

# Limite de paginas para no procesar PDFs gigantes
MAX_PAGINAS_PDF = 25

# Limite de archivos dentro de un ZIP para extraccion de texto (todos se suben al radicar)
MAX_ARCHIVOS_EXTRACCION_ZIP = 10


class DocumentExtractorService:
    """Servicio hibrido de extraccion de documentos"""

    def __init__(self):
        self.gemini = GeminiService()

    def is_excel_file(self, filename: str) -> bool:
        """Verifica si es archivo Excel"""
        ext = Path(filename).suffix.lower()
        return ext in [".xlsx", ".xls", ".csv", ".xlsm"]

    def is_word_file(self, filename: str) -> bool:
        """Verifica si es archivo Word"""
        ext = Path(filename).suffix.lower()
        return ext in [".docx", ".doc"]

    def is_zip_file(self, filename: str) -> bool:
        """Verifica si es archivo ZIP"""
        return Path(filename).suffix.lower() == ".zip"

    def is_pdf_file(self, filename: str) -> bool:
        """Verifica si es archivo PDF"""
        return Path(filename).suffix.lower() == ".pdf"

    def is_image_file(self, filename: str, content_type: str = "") -> bool:
        """Verifica si es imagen"""
        ext = Path(filename).suffix.lower()
        image_extensions = [
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".webp",
            ".tiff",
            ".tif",
        ]
        return ext in image_extensions or "image" in (content_type or "").lower()

    async def extract_from_excel(self, content_bytes: bytes, filename: str) -> str:
        """Extrae texto de archivos Excel/CSV directamente"""
        print(
            f"[DOC_EXTRACTOR] EXCEL: Extrayendo {filename} con pandas/openpyxl",
            file=sys.stderr,
        )
        try:
            import pandas as pd

            ext = Path(filename).suffix.lower()

            if ext == ".csv":
                df = pd.read_csv(
                    io.BytesIO(content_bytes), encoding="utf-8", on_bad_lines="skip"
                )
            else:
                df = pd.read_excel(io.BytesIO(content_bytes), engine="openpyxl")

            text_parts = []
            text_parts.append(f"=== EXTRACCION EXCEL: {filename} ===")

            if not df.empty:
                text_parts.append(f"Columnas: {list(df.columns)}")
                text_parts.append(f"Filas: {len(df)}")

                for idx, row in df.iterrows():
                    row_text = " | ".join(
                        [f"{col}: {val}" for col, val in row.items() if pd.notna(val)]
                    )
                    if row_text:
                        text_parts.append(f"Fila {idx + 1}: {row_text}")

            return "\n".join(text_parts)

        except Exception as e:
            return f"Error extrayendo Excel: {str(e)}"

    async def extract_from_word(self, content_bytes: bytes, filename: str) -> str:
        """Extrae texto de archivos Word (.docx, .doc)"""
        print(
            f"[DOC_EXTRACTOR] WORD: Extrayendo {filename} con python-docx",
            file=sys.stderr,
        )
        try:
            import docx

            text_parts = []
            text_parts.append(f"=== EXTRACCION WORD: {filename} ===")

            doc = docx.Document(io.BytesIO(content_bytes))

            for para in doc.paragraphs:
                if para.text.strip():
                    text_parts.append(para.text)

            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(
                        cell.text.strip() for cell in row.cells if cell.text.strip()
                    )
                    if row_text:
                        text_parts.append(f"[TABLA] {row_text}")

            result = "\n".join(text_parts)
            return result if result.strip() else "Documento Word vacio"

        except Exception as e:
            return f"Error extrayendo Word: {str(e)}"

    async def extract_from_zip(self, content_bytes: bytes, filename: str) -> str:
        """Extrae texto de archivos ZIP (contiene PDF, Excel, Word, etc)"""
        print(f"[DOC_EXTRACTOR] ZIP: Extrayendo {filename}", file=sys.stderr)

        try:
            import zipfile

            text_parts = []
            text_parts.append(f"=== EXTRACCION ZIP: {filename} ===")

            with zipfile.ZipFile(io.BytesIO(content_bytes), "r") as zip_ref:
                file_list = zip_ref.namelist()
                archivos_reales = [f for f in file_list if not f.endswith("/")]
                print(
                    f"[DOC_EXTRACTOR] ZIP: {len(archivos_reales)} archivos dentro",
                    file=sys.stderr,
                )

                extraidos = 0
                for file_in_zip in archivos_reales:
                    if extraidos >= MAX_ARCHIVOS_EXTRACCION_ZIP:
                        print(
                            f"[DOC_EXTRACTOR] ZIP: Limite de extraccion alcanzado ({MAX_ARCHIVOS_EXTRACCION_ZIP}), "
                            f"omitiendo {len(archivos_reales) - extraidos} archivos restantes para IA",
                            file=sys.stderr,
                        )
                        break

                    try:
                        file_data = zip_ref.read(file_in_zip)
                        print(
                            f"[DOC_EXTRACTOR] ZIP: Extrayendo {file_in_zip}",
                            file=sys.stderr,
                        )

                        # Extraer cada archivo según su tipo
                        extracted_text = await self.extract(file_data, file_in_zip)
                        if extracted_text and len(extracted_text) > 50:
                            text_parts.append(f"\n--- Archivo: {file_in_zip} ---")
                            text_parts.append(extracted_text)
                        extraidos += 1
                    except Exception as e:
                        text_parts.append(f"\n--- Archivo: {file_in_zip} ---")
                        text_parts.append(f"Error extrayendo: {str(e)}")
                        extraidos += 1

            result = "\n".join(text_parts)
            return (
                result
                if len(result) > 100
                else f"ZIP contiene {len(file_list)} archivos"
            )

        except Exception as e:
            return f"Error extrayendo ZIP: {str(e)}"

    async def extract_from_pdf_native(self, content_bytes: bytes, filename: str) -> str:
        """Extrae texto de PDFs - intenta multiples metodos"""
        print(
            f"[DOC_EXTRACTOR] PDF: Extrayendo {filename} con PyMuPDF",
            file=sys.stderr,
        )
        try:
            import fitz

            text_parts = []
            text_parts.append(f"=== EXTRACCION PDF: {filename} ===")

            # Abrir PDF (PyMuPDF infiere el tipo)
            with fitz.open(stream=content_bytes) as pdf_doc:
                total_pages = len(pdf_doc)
                print(
                    f"[DOC_EXTRACTOR] PDF: {total_pages} paginas detectadas",
                    file=sys.stderr,
                )

                paginas_a_procesar = min(total_pages, MAX_PAGINAS_PDF)
                if total_pages > MAX_PAGINAS_PDF:
                    print(
                        f"[DOC_EXTRACTOR] PDF: Limitando a {MAX_PAGINAS_PDF} paginas (de {total_pages})",
                        file=sys.stderr,
                    )

                for page_num in range(paginas_a_procesar):
                    page = pdf_doc[page_num]

                    # Metodo 1: get_text() - texto embebido
                    text = page.get_text()
                    if text and text.strip():
                        text_parts.append(f"\n--- Pagina {page_num + 1} (texto) ---")
                        text_parts.append(text)
                        continue

                    # Metodo 2: get_text("dict") - mas detallado
                    try:
                        text_dict = page.get_text("dict")
                        for block in text_dict.get("blocks", []):
                            if block.get("type") == 0:  # Text block
                                for line in block.get("lines", []):
                                    line_text = ""
                                    for span in line.get("spans", []):
                                        line_text += span.get("text", "")
                                    if line_text.strip():
                                        text_parts.append(line_text)
                    except Exception:
                        pass

                    # Metodo 3: get_text("xml") - OCR de texto oculto
                    try:
                        text_xml = page.get_text("xml")
                        if text_xml and len(text_xml) > 100:
                            import re

                            text_only = re.sub(r"<[^>]+>", "", text_xml)
                            if text_only.strip():
                                text_parts.append(
                                    f"\n--- Pagina {page_num + 1} (xml) ---"
                                )
                                text_parts.append(text_only)
                    except Exception:
                        pass

                # Metadatos
                try:
                    if pdf_doc.metadata:
                        text_parts.append(f"\n--- Metadatos ---")
                        text_parts.append(
                            f"Titulo: {pdf_doc.metadata.get('title', 'N/A')}"
                        )
                        text_parts.append(
                            f"Autor: {pdf_doc.metadata.get('author', 'N/A')}"
                        )
                        text_parts.append(f"Paginas: {total_pages}")
                except Exception:
                    pass

            result = "\n".join(text_parts)

            # Contar caracteres significativos
            chars = len([c for c in result if c.isalnum() or c.isspace()])
            print(f"[DOC_EXTRACTOR] PDF: Extraidos {chars} caracteres", file=sys.stderr)

            return result if chars > 50 else "PDF sin texto embebido"

        except ImportError:
            return "Error: PyMuPDF no esta instalado"
        except Exception as e:
            print(f"[DOC_EXTRACTOR] PDF: Error: {str(e)}", file=sys.stderr)
            return f"Error extrayendo PDF: {str(e)}"

    async def extract_from_image(self, content_bytes: bytes, filename: str) -> str:
        """Extrae texto de imagen usando OCR"""
        return await self._extract_with_ocr(content_bytes, filename, es_pdf=True)

    async def extract_from_scanned_pdf(
        self, content_bytes: bytes, filename: str
    ) -> str:
        """
        Extrae texto de PDF escaneado - convierte paginas a imagenes y usa OCR
        """
        print(
            f"[DOC_EXTRACTOR] PDF ESCANEADO: Convirtiendo {filename} a imagenes para OCR",
            file=sys.stderr,
        )

        try:
            import fitz
            from PIL import Image
            import pytesseract

            text_parts = []
            text_parts.append(f"=== EXTRACCION PDF ESCANEADO (OCR): {filename} ===")

            with fitz.open(stream=content_bytes) as pdf_doc:
                total_pages = len(pdf_doc)
                paginas_a_procesar = min(total_pages, MAX_PAGINAS_PDF)

                if total_pages > MAX_PAGINAS_PDF:
                    print(
                        f"[DOC_EXTRACTOR] PDF ESCANEADO: {total_pages} paginas detectadas — limitando OCR a {MAX_PAGINAS_PDF} paginas",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"[DOC_EXTRACTOR] PDF ESCANEADO: Renderizando {total_pages} paginas",
                        file=sys.stderr,
                    )

                for page_num in range(paginas_a_procesar):
                    page = pdf_doc[page_num]

                    # Renderizar pagina a imagen (300 DPI para mejor calidad OCR)
                    pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
                    img_data = pix.tobytes("png")

                    # OCR con Tesseract
                    image = Image.open(io.BytesIO(img_data))
                    ocr_text = pytesseract.image_to_string(image, lang="spa+eng")

                    if ocr_text and ocr_text.strip():
                        text_parts.append(f"\n--- Pagina {page_num + 1} ---")
                        text_parts.append(ocr_text)
                        print(
                            f"[DOC_EXTRACTOR] PDF ESCANEADO: Pagina {page_num + 1} - {len(ocr_text)} chars",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"[DOC_EXTRACTOR] PDF ESCANEADO: Pagina {page_num + 1} sin texto",
                            file=sys.stderr,
                        )

            result = "\n".join(text_parts)

            if len(result) > 100:
                print(
                    f"[DOC_EXTRACTOR] PDF ESCANEADO: Exitoso, {len(result)} chars totales",
                    file=sys.stderr,
                )
                return result
            else:
                print(
                    f"[DOC_EXTRACTOR] PDF ESCANEADO: Tesseract no logro extraer, intentando Gemini...",
                    file=sys.stderr,
                )
                # Llamar a OCR con Gemini como fallback
                return await self._extract_with_ocr(
                    content_bytes, filename, es_pdf=True
                )

        except ImportError as e:
            print(
                f"[DOC_EXTRACTOR] PDF ESCANEADO: Dependencia faltante: {e}",
                file=sys.stderr,
            )
            return await self._extract_with_ocr(content_bytes, filename, es_pdf=True)
        except Exception as e:
            print(f"[DOC_EXTRACTOR] PDF ESCANEADO: Error: {e}", file=sys.stderr)
            return await self._extract_with_ocr(content_bytes, filename, es_pdf=True)

    async def _extract_with_ocr(
        self, content_bytes: bytes, filename: str, es_pdf: bool = False
    ) -> str:
        """
        Extrae texto usando OCR. Implementa enfoque por niveles:
        1. Tesseract (local, gratuito) - PRIMERO
        2. Gemini como ULTIMO recurso
        """
        print(f"[DOC_EXTRACTOR] OCR: Intentando extraer {filename}", file=sys.stderr)

        text_parts = []
        text_parts.append(f"=== EXTRACCION OCR: {filename} ===")

        # 1. Intentar con Tesseract primero (local, gratuito)
        # NOTA: Para PDFs ya se intento en extract_from_scanned_pdf
        if not es_pdf:
            print(
                f"[DOC_EXTRACTOR] OCR: Intentando con Tesseract (local)...",
                file=sys.stderr,
            )
            text_parts.append("\n--- Intentando con Tesseract ---")

        try:
            import pytesseract
            from PIL import Image

            image = Image.open(io.BytesIO(content_bytes))
            ocr_text = pytesseract.image_to_string(image, lang="spa+eng")

            if ocr_text and ocr_text.strip() and len(ocr_text.strip()) > 10:
                print(
                    f"[DOC_EXTRACTOR] OCR: Tesseract exitoso! {len(ocr_text)} chars extraidos",
                    file=sys.stderr,
                )
                text_parts.append(ocr_text)
                return "\n".join(text_parts)
            else:
                print(
                    f"[DOC_EXTRACTOR] OCR: Tesseract no obtuvo texto significativo",
                    file=sys.stderr,
                )
                text_parts.append("Tesseract no obtuvo texto significativo")

        except ImportError:
            print("[DOC_EXTRACTOR] Tesseract no esta instalado", file=sys.stderr)
            text_parts.append("Tesseract no esta instalado")
        except Exception as e:
            print(f"[DOC_EXTRACTOR] Tesseract fallo: {str(e)}", file=sys.stderr)
            text_parts.append(f"Tesseract fallo: {str(e)}")

        # 2. Gemini como ULTIMO recurso
        if es_pdf:
            # Si es PDF y llegamos aqui, es porque extract_from_scanned_pdf ya intento con Tesseract
            # Ir directamente a Gemini
            pass
        else:
            print(
                f"[DOC_EXTRACTOR] OCR: Tesseract NO obtuvo texto, intentando con Gemini...",
                file=sys.stderr,
            )
        text_parts.append("\n--- Fallback: Gemini como ultimo recurso ---")

        try:
            #mime_type = self._get_mime_type(filename)
            #result_text = await self.gemini.analyze_image(content_bytes, mime_type)

            mime_type = self._get_mime_type(filename, content_bytes)

            if mime_type == "application/octet-stream":
                text_parts.append(f"Archivo omitido por MIME no soportado: {filename}")
                return "\n".join(text_parts)

            result_text = await self.gemini.analyze_image(content_bytes, mime_type)

            if result_text and result_text.strip():
                text_parts.append(result_text)
                return "\n".join(text_parts)
            else:
                text_parts.append("Gemini no devolvio texto")

        except Exception as e:
            text_parts.append(f"Gemini fallo: {str(e)}")

        # 3. Si todo falla, retornar mensaje de error (NO binario)
        return (
            "\n".join(text_parts)
            + "\n\n[ERROR: No se pudo extraer texto del documento]"
        )

    #def _get_mime_type(self, filename: str) -> str:
        #"""Obtiene el MIME type basado en la extension"""
        #ext = Path(filename).suffix.lower()
        #mime_types = {
        #    ".pdf": "application/pdf",
        #    ".jpg": "image/jpeg",
        #    ".jpeg": "image/jpeg",
        #    ".png": "image/png",
        #    ".gif": "image/gif",
        #    ".bmp": "image/bmp",
        #    ".webp": "image/webp",
        #    ".tiff": "image/tiff",
        #    ".tif": "image/tiff",
        #}
        #return mime_types.get(ext, "application/octet-stream")

    def _get_mime_type(self, filename: str, content_bytes: bytes = b"") -> str:
        """Obtiene el MIME type por contenido real y luego por extension"""
        ext = Path(filename).suffix.lower()

        if content_bytes.startswith(b"%PDF"):
            return "application/pdf"

        if content_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"

        if content_bytes.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"

        if content_bytes.startswith(b"GIF87a") or content_bytes.startswith(b"GIF89a"):
            return "image/gif"

        if content_bytes.startswith(b"RIFF") and b"WEBP" in content_bytes[:20]:
            return "image/webp"

        mime_types = {
            ".pdf": "application/pdf",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }

        return mime_types.get(ext, "application/octet-stream")    



    async def extract(
        self, content_bytes: bytes, filename: str, content_type: str = ""
    ) -> str:
        """
        Metodo principal - routing automatico por tipo de archivo
        """
        ext = Path(filename).suffix.lower()
        print(
            f"[DOC_EXTRACTOR] Extrayendo archivo: {filename} (tipo: {ext})",
            file=sys.stderr,
        )

        if self.is_excel_file(filename):
            print(
                f"[DOC_EXTRACTOR] -> Usando metodo: EXCEL (pandas/openpyxl)",
                file=sys.stderr,
            )
            return await self.extract_from_excel(content_bytes, filename)

        elif self.is_word_file(filename):
            print(
                f"[DOC_EXTRACTOR] -> Usando metodo: WORD (python-docx)",
                file=sys.stderr,
            )
            return await self.extract_from_word(content_bytes, filename)

        elif self.is_zip_file(filename):
            print(
                f"[DOC_EXTRACTOR] -> Usando metodo: ZIP (extraer y procesar contenidos)",
                file=sys.stderr,
            )
            return await self.extract_from_zip(content_bytes, filename)

        elif self.is_pdf_file(filename):
            print(
                f"[DOC_EXTRACTOR] -> Intentando PDF NATIVO primero (PyMuPDF)...",
                file=sys.stderr,
            )
            pdf_text = await self.extract_from_pdf_native(content_bytes, filename)

            if self._is_meaningful_text(pdf_text):
                print(
                    f"[DOC_EXTRACTOR] -> PDF NATIVO exitoso, texto extraido: {len(pdf_text)} chars",
                    file=sys.stderr,
                )
                return pdf_text
            else:
                print(
                    f"[DOC_EXTRACTOR] -> PDF NATIVO sin texto, usando OCR...",
                    file=sys.stderr,
                )
                return await self.extract_from_scanned_pdf(content_bytes, filename)

        elif self.is_image_file(filename, content_type):
            print(f"[DOC_EXTRACTOR] -> Usando metodo: IMAGEN (OCR)", file=sys.stderr)
            return await self.extract_from_image(content_bytes, filename)

        else:
            print(f"[DOC_EXTRACTOR] -> Usando metodo: OCR generico", file=sys.stderr)
            return await self._extract_with_ocr(content_bytes, filename)

    def _is_meaningful_text(self, text: str) -> bool:
        """Verifica si el texto extraido es significativo (no es solo error/vacio)"""
        if not text:
            return False

        text_lower = text.lower()

        meaningless_patterns = [
            "error",
            "fallo",
            "failed",
            "no se pudo",
            "pdf vacio",
            "sin texto",
            "no text",
        ]

        for pattern in meaningless_patterns:
            if pattern in text_lower:
                return False

        return len(text.strip()) > 100

    async def analyze_extracted_text(self, extracted_text: str) -> str:
        """
        Usa Gemini para analizar e interpretar el texto extraido.
        Este es el unico paso donde se usa Gemini para analisis.
        """
        if not extracted_text or len(extracted_text) < 50:
            return "SIN_INFORMACION_SUFICIENTE"

        try:
            prompt = f"""{DOCUMENT_ANALYSIS_PROMPT}

TEXTO EXTRAIDO DEL DOCUMENTO:
{extracted_text}

---
Analiza el texto extraido y extrae los datos solicitados en el formato especificado."""

            result_text = await self.gemini.analyze_text(prompt)
            return result_text if result_text else "SIN_INFORMACION"

        except Exception as e:
            return f"Error en analisis: {str(e)}"

    def unify_analysis_results(self, results: List[str]) -> str:
        """Unifica multiples resultados de extraccion"""
        if not results:
            return "SIN_INFORMACION"

        valid_results = [
            r for r in results if r and r.strip() and "error" not in r.lower()
        ]
        return (
            "\n\n---\n\n".join(valid_results).strip()
            if valid_results
            else "SIN_INFORMACION"
        )


async def test_extraction():
    """Prueba rapida del servicio"""
    extractor = DocumentExtractorService()

    print("=== Test de extraccion ===")
    print(f"Excel: {extractor.is_excel_file('documento.xlsx')}")
    print(f"PDF: {extractor.is_pdf_file('documento.pdf')}")
    print(f"Imagen: {extractor.is_image_file('foto.jpg')}")
    print(f"MIME PDF: {extractor._get_mime_type('archivo.pdf')}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_extraction())
