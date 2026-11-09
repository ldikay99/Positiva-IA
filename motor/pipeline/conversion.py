"""
Conversion de adjuntos a Markdown.

Markdown conserva encabezados, tablas y listas, que el modelo aprovecha.

  - El tipo se decide por los bytes, no por la extension.
  - Los fallos de conversion se registran en el manifiesto, no se silencian.
  - OCR solo si el PDF no trae texto.
"""
from __future__ import annotations

import contextvars
import io
import os
import re
import threading
import time
import unicodedata
import zipfile
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

# Cuantos hilos abre tesseract POR DENTRO en cada llamada. No tiene nada que ver
# con la concurrencia del pipeline, que sigue procesando cinco correos a la vez.
#
# Tesseract usa OpenMP y mira los nucleos de la MAQUINA, no la cuota del
# contenedor. El Job tiene dos vCPU y cinco correos en vuelo, asi que cinco
# tesseract abriendo una decena de hilos cada uno se pelean por dos nucleos y
# cada pagina tarda varias veces mas. Medido en un contenedor de dos vCPU con
# cinco paginas simultaneas: 1,93 s por pagina sin esto, 0,45 s con esto.
#
# Se fija aqui, antes de la primera llamada, porque `pytesseract` lanza el
# binario como subproceso y el subproceso hereda el entorno. `setdefault` para
# que un valor puesto en el Job siga mandando.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")


def nucleos() -> int:
    """Los nucleos que de verdad tiene el proceso: afinidad y cuota del cgroup."""
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    try:
        cuota, periodo = open("/sys/fs/cgroup/cpu.max").read().split()[:2]
        if cuota != "max":
            n = min(n, max(1, int(int(cuota) // int(periodo))))
    except (OSError, ValueError):
        pass
    return max(1, n)


_LIBC: list = []


def devolver_memoria() -> None:
    """
    Le devuelve al sistema la memoria que el proceso ya libero.

    Las imagenes de una hoja a 300 dpi son decenas de megas; glibc las saca del
    monton y, al soltarlas, se queda con el espacio. Sin esto el proceso nunca
    baja de su pico, y el pico crece con cada correo con escaneos.
    """
    if not _LIBC:
        import ctypes

        try:
            _LIBC.append(ctypes.CDLL("libc.so.6"))
        except OSError:  # no es glibc (Windows, macOS): no hace falta
            _LIBC.append(None)
    try:
        if _LIBC[0] is not None:
            _LIBC[0].malloc_trim(0)
    except Exception:
        pass


def _entero_del_entorno(nombre: str, por_defecto: int) -> int:
    try:
        return max(1, int(os.environ.get(nombre) or por_defecto))
    except ValueError:
        return por_defecto


# Lecturas de imagen (preparacion y tesseract) a la vez en todo el proceso,
# sumando todos los correos. Con cinco correos y varias hojas por correo en
# paralelo, sin tope habria mas procesos que nucleos, cada hoja tardaria varias
# veces mas (lo que hace saltar el tope por pagina) y la memoria se multiplica.
TESSERACT_A_LA_VEZ = _entero_del_entorno("OCR_TESSERACT_A_LA_VEZ", nucleos())
_CUPO_TESSERACT = threading.BoundedSemaphore(TESSERACT_A_LA_VEZ)
# Hojas de un mismo PDF que se leen a la vez. Un escaneo de ochenta hojas se
# leia de a una aunque la maquina tuviera los nucleos libres.
PAGINAS_OCR_EN_PARALELO = _entero_del_entorno(
    "OCR_PAGINAS_EN_PARALELO", min(4, nucleos()))
# Hojas renderizadas a la vez en todo el proceso. Cada una es una imagen de
# hasta 40 MB que espera su turno de lectura: con una rafaga de correos, sin
# este tope habria cinco correos por cuatro hojas en memoria.
_CUPO_DE_HOJAS = threading.BoundedSemaphore(2 * TESSERACT_A_LA_VEZ)

# Limite por documento. Los adjuntos de 200 paginas existen y no aportan mas
# informacion que las primeras: se convierten hasta el limite y queda anotado.
LIMITE_CARACTERES = 200_000
LIMITE_PAGINAS_PDF = 60
LIMITE_FILAS_HOJA = 2_000

# Presupuesto de OCR por documento, en segundos. El OCR es la etapa mas lenta
# del pipeline: un escaneo de sesenta paginas a 200 dpi puede tardar minutos y
# frenar el lote entero por un solo adjunto. Al agotarse se deja de aplicar OCR
# y las paginas restantes quedan marcadas, que es preferible a un job que se
# queda colgado sin explicacion.
#
# Medido sobre los 913 adjuntos del buzon con los topes anteriores (90 s por
# documento y 180 s por correo): de 242 paginas que quedaron sin texto, 196 se
# perdieron por presupuesto y solo 46 porque no tenian nada que leer. La IA
# nunca veia esas hojas. Las corridas de produccion duran unos tres minutos de
# una ventana de treinta, y el presupuesto de la corrida (`PRESUPUESTO_S`)
# sigue mandando: un correo que no alcance se aplaza, no se pierde.
PRESUPUESTO_OCR_SEGUNDOS = 240.0

# Presupuesto de OCR del CORREO ENTERO, repartido entre sus adjuntos. El de
# arriba acota un documento; sin este, un correo con dieciseis adjuntos
# escaneados los multiplica y se lleva la ventana de media hora entre corridas.
# Siete minutos: la lectura a 300 dpi con segunda pasada de cifras cuesta unos
# diez segundos por hoja, y con el tope anterior de tres minutos un correo con
# un expediente escaneado se quedaba a medias. Sigue acotando el caso
# patologico (decenas de escaneos) dentro de la ventana de la corrida.
PRESUPUESTO_OCR_POR_CORREO_S = 420.0

# Tope por pagina. El presupuesto del documento solo se comprueba ANTES de cada
# pagina, asi que una sola pagina lenta lo desborda entero: se han visto
# conversiones de veinte minutos con el presupuesto en noventa segundos.
TIMEOUT_OCR_PAGINA_S = 40.0
# Tope de imagenes u hojas ilegibles para tesseract que se transcriben con el
# modelo, por correo.
MAX_IMAGENES_IA_POR_CORREO = 8
# Parte del tope que se le da a la lectura a 300 dpi; lo que sobre es para
# releer la hoja a la resolucion de respaldo si la primera se corta.
FRACCION_DEL_TOPE_A_300_DPI = 0.65
DPI_DE_RESPALDO_OCR = 200

# Lado menor, en pixeles, desde el que una imagen incrustada puede llevar un
# documento. Por debajo es un logo, una firma escaneada o un separador, y pasarla
# por OCR es gasto sin dato.
LADO_MINIMO_DE_IMAGEN_PX = 200

# Y cuanto de la pagina tiene que ocupar, entre un minimo y un maximo. El tamaño
# en pixeles no discrimina: hay membretes de 1490 px de ancho. La fraccion de la
# hoja si, y hace falta el tope de arriba:
#
#   - Por debajo del minimo es un logotipo, una firma o un separador.
#   - Por encima del maximo la imagen ES la pagina: un escaneo completo. Esas
#     paginas traen la capa de texto que dejo el escaner, y medido sobre los
#     adjuntos del buzon esa capa esta COMPLETA —ninguna de las veinte paginas
#     asi gano palabras al pasarla por OCR, y varias perdieron—. Leerlas otra
#     vez solo duplica el texto con mas ruido y mete numeros del membrete, como
#     el NIT de la propia Positiva, que el extractor tomaria por un documento.
#
# En la banda de en medio queda lo que importa: la foto de una cedula pegada
# dentro de una comunicacion que si tiene texto propio.
FRACCION_MINIMA_DE_PAGINA = 0.10
FRACCION_MAXIMA_DE_PAGINA = 0.60

# Cuantas imagenes incrustadas se leen por pagina. Una pagina con veinte
# miniaturas no lleva veinte documentos.
MAX_IMAGENES_POR_PAGINA = 4

# ── Preparacion de la imagen antes del OCR ──
#
# Por debajo de este ancho tesseract pierde digitos: las fotos de cedula llegan
# a 600 px y se leian como ruido. Por encima del maximo no gana precision y si
# tarda mas. 1600 y no 1700: un A4 a 200 dpi mide 1654 px y reescalarlo para
# ganar un 3 % de ancho solo cuesta tiempo, que es lo que falta (paginas
# perdidas por presupuesto).
ANCHO_MINIMO_OCR_PX = 1600
ANCHO_MAXIMO_OCR_PX = 3000
# Resolucion de render de una pagina escaneada. Medido sobre 203 hojas
# escaneadas reales del buzon que traen la capa de texto del escaner como
# referencia, contando los numeros de documento con marca (CC, NIT...):
#   200 dpi 64 %, 300 dpi 80 %, 350 dpi 73 %, 400 dpi 67 %.
# A 300 los digitos pequenos dejan de confundirse; por encima tesseract parte
# los trazos y vuelve a perderlos.
DPI_MINIMO_OCR = 300
DPI_MAXIMO_OCR = 300
# La inclinacion fina se busca en este rango; mas alla es una rotacion, que la
# resuelve el OSD. Se mide sobre una miniatura: a 700 px las lineas de texto
# siguen separadas y cuesta poco.
INCLINACION_MAXIMA_GRADOS = 5.0
PASO_INCLINACION_GRADOS = 0.5
LADO_MINIATURA_PX = 700
# Confianza minima del OSD para girar: por debajo suele equivocarse en paginas
# con poco texto y gira una pagina que estaba bien.
CONFIANZA_MINIMA_OSD = 2.0
# Si el OSD no esta seguro y la lectura es muy mala, se prueba cada giro sobre
# una miniatura de este lado mayor (tres veces mas rapida que la hoja entera).
# Un giro solo se elige si mejora la confianza al menos en esto.
LADO_SONDEO_GIRO_PX = 1000
MEJORA_MINIMA_POR_GIRO = 0.10
# Una lectura por debajo de esta confianza, o con menos caracteres utiles que
# el minimo, se considera floja: solo entonces se mira el giro y se relee. Casi
# todas las paginas vienen derechas y el OSD es otra pasada de tesseract (casi
# un segundo por pagina). Una pagina de lado o al reves sale por debajo de 0,40
# y un escaneo derecho mediocre entre 0,60 y 0,80: con 0,80 se pagaba el OSD en
# paginas que no lo necesitaban.
CONFIANZA_REINTENTO_OSD = 0.60
MINIMO_CARACTERES_OCR = 40
# Por debajo de esta confianza la hoja que tesseract leyo se le pasa al modelo
# con vision, que la transcribe (dentro del tope de imagenes por correo). Es
# un umbral aparte del de la relectura: medido el 2026-11-06 sobre once hojas
# reales entre 0,62 y 0,79 —ordenes medicas a mano, historias clinicas,
# formularios—, el modelo leyo el paciente, la cedula, el diagnostico y el sexo
# donde tesseract dejaba ruido ("1isnel Fegec Mura" por "Manuel Feged Murcia").
# Subir el de la relectura a 0,80 hacia buscar el giro en hojas derechas y
# llevo un PDF de 14 s a 193 s sin mejorar nada.
CONFIANZA_PARA_VISION = 0.80
# Por debajo de esta confianza la pagina se marca como lectura dudosa en el
# markdown, para que quien lea los datos de ahi los verifique.
CONFIANZA_DUDOSA = 0.55
# Solo español. Medido sobre treinta adjuntos escaneados del buzon, ya
# preparados: "spa+eng" y "spa" sacan los mismos caracteres utiles, los mismos
# numeros de documento y la misma proporcion de ruido, y "spa" tarda un 23 %
# menos. Con paginas perdidas por presupuesto, ese tiempo vale mas que el ingles.
IDIOMAS_OCR = "spa"

# Firmas de tipo por bytes iniciales.
_FIRMAS: Tuple[Tuple[bytes, str], ...] = (
    (b"%PDF", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "imagen"),
    (b"\xff\xd8\xff", "imagen"),
    (b"GIF87a", "imagen"),
    (b"GIF89a", "imagen"),
    (b"BM", "imagen"),
    (b"II*\x00", "imagen"),
    (b"MM\x00*", "imagen"),
    (b"%!PS", "postscript"),
    (b"{\\rtf", "rtf"),
    (b"\xd0\xcf\x11\xe0", "ofimatica_antigua"),  # doc/xls de Office 97-2003
)


@dataclass
class PresupuestoDeOcr:
    """
    Lo que queda de OCR para el correo, compartido por todos sus adjuntos.

    Sin algo compartido, el tope de noventa segundos por documento se multiplica
    por el numero de adjuntos: dieciseis escaneos son veinticuatro minutos y la
    corrida se pasa de la ventana. Se pasa el mismo objeto a cada conversion.
    """

    # Se lee al construirlo, no al definir la clase, para poder ajustarlo.
    total_s: float = field(
        default_factory=lambda: PRESUPUESTO_OCR_POR_CORREO_S)
    gastado_s: float = 0.0
    # Imagenes que todavia se le pueden pasar al modelo cuando tesseract no
    # las lee. Cada una es una llamada (~0,002 USD y ~10 s).
    imagenes_ia: int = field(
        default_factory=lambda: MAX_IMAGENES_IA_POR_CORREO)

    def queda(self) -> float:
        return max(0.0, self.total_s - self.gastado_s)

    def gastar(self, segundos: float) -> None:
        self.gastado_s += max(0.0, segundos)

    @property
    def agotado(self) -> bool:
        return self.queda() <= 0.0

    def motivo(self) -> str:
        return (f"se agoto el presupuesto de OCR del correo "
                f"({self.total_s:.0f}s repartidos entre sus adjuntos)")


@dataclass
class Conversion:
    """Resultado de convertir un adjunto."""

    markdown: str = ""
    tipo_real: str = ""
    ocr_aplicado: bool = False
    truncado: bool = False
    error: str = ""
    aviso: str = ""
    # Paginas que quedaron sin texto pese al OCR. Un escaneo que no se pudo
    # leer no es lo mismo que un documento que no dice nada, y el motivo de
    # revision manual cambia segun cual sea.
    paginas_ilegibles: int = 0
    paginas: int = 0

    @property
    def vacio(self) -> bool:
        return not self.markdown.strip()

    @property
    def ilegible(self) -> bool:
        """Todas las paginas quedaron sin texto: el contenido no se leyo."""
        return self.paginas > 0 and self.paginas_ilegibles >= self.paginas


def detectar_tipo(datos: bytes, nombre: str = "") -> str:
    """
    Determina el tipo real por contenido, con la extension solo como respaldo.

    Un ZIP requiere inspeccion adicional: docx, xlsx y pptx son ZIP por dentro,
    asi que el mismo encabezado significa cuatro cosas distintas.
    """
    if not datos:
        return "vacio"
    contenedor = _tipo_isobmff(datos)
    if contenedor:
        return contenedor
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "imagen"
    for firma, tipo in _FIRMAS:
        if datos.startswith(firma):
            return tipo
    if datos[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return _tipo_de_zip(datos)
    if nombre.lower().endswith(".p7m"):
        return "p7m"
    # Texto: si decodifica como UTF-8 o latin-1 sin bytes de control raros
    muestra = datos[:4096]
    if b"\x00" not in muestra:
        for cp in ("utf-8", "latin-1"):
            try:
                muestra.decode(cp)
                return "texto"
            except UnicodeDecodeError:
                continue
    ext = nombre.lower().rsplit(".", 1)[-1] if "." in nombre else ""
    return {"pdf": "pdf", "docx": "docx", "xlsx": "xlsx",
            "csv": "texto", "txt": "texto", "eml": "texto"}.get(ext, "desconocido")


# HEIF y AVIF son contenedores ISO-BMFF: «ftyp» en el byte 4 y despues la
# marca mayor y las compatibles. Las fotos de iPhone llegan como HEIC.
_MARCAS_AVIF = {b"avif", b"avis"}
_MARCAS_HEIF = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm",
                b"hevs", b"mif1", b"msf1", b"mif2"}


def _tipo_isobmff(datos: bytes) -> str:
    """"heic" (HEIF/HEIC), "imagen" (AVIF, que Pillow abre) o "" si no lo es."""
    if len(datos) < 16 or datos[4:8] != b"ftyp":
        return ""
    largo = int.from_bytes(datos[0:4], "big")
    marcas = {datos[8:12]} | {datos[i:i + 4] for i in range(16, min(largo, 64), 4)}
    if marcas & _MARCAS_AVIF:
        return "imagen"
    if marcas & _MARCAS_HEIF:
        return "heic"
    return ""


def _tipo_de_zip(datos: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            nombres = set(z.namelist())
    except Exception:
        return "zip"
    if "word/document.xml" in nombres:
        return "docx"
    if any(n.startswith("xl/") for n in nombres):
        return "xlsx"
    if any(n.startswith("ppt/") for n in nombres):
        return "pptx"
    return "zip"


# ── Normalizacion ──

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Espacios que no son el espacio normal: no-break, en/em quad, ideografico.
# Se unifican para que el conteo de tokens y las comparaciones no dependan de
# cual variante uso quien escribio el documento.
_ESPACIOS_RAROS = re.compile("[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")

# Invisibles y controles bidireccionales. Se eliminan porque son el vehiculo
# habitual del texto oculto en documentos:
#   200b-200f  espacios de ancho cero y marcas LTR/RTL
#   202a-202e  overrides bidi: RLO invierte el texto EN PANTALLA, de modo que
#              el documento MUESTRA una cosa y CONTIENE otra
#   2060-2064  invisibles de union y separadores matematicos
#   2066-2069  aislantes bidi, misma familia de ataque que los overrides
#   feff       BOM incrustado a media linea
_INVISIBLES = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]"
)


def normalizar(texto: str) -> str:
    """
    Limpia el texto extraido sin destruir su estructura.

    Se quitan caracteres de control e invisibles porque son el vehiculo habitual
    de la inyeccion de prompts (texto oculto entre caracteres de ancho cero) y
    porque ensucian el conteo de tokens sin aportar nada.
    """
    if not texto:
        return ""
    t = unicodedata.normalize("NFC", texto)
    t = _INVISIBLES.sub("", t)
    t = _ESPACIOS_RAROS.sub(" ", t)
    t = _CONTROL.sub(" ", t)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    # Colapsar espacios pero conservar los saltos, que son la estructura
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{4,}", "\n\n\n", t)
    lineas = [l.rstrip() for l in t.split("\n")]
    return "\n".join(lineas).strip()


def _truncar(texto: str) -> Tuple[str, bool]:
    if len(texto) <= LIMITE_CARACTERES:
        return texto, False
    corte = texto[:LIMITE_CARACTERES]
    # Cortar en un limite de linea para no partir una frase a la mitad
    ultimo = corte.rfind("\n")
    if ultimo > LIMITE_CARACTERES * 0.9:
        corte = corte[:ultimo]
    return corte, True


# ── Conversores por tipo ──

def _pdf_a_markdown(
    datos: bytes, nombre: str, claves_candidatas: Optional[List[str]] = None,
    presupuesto: Optional[PresupuestoDeOcr] = None,
) -> Conversion:
    """
    PDF a Markdown. Usa el texto nativo si existe; OCR solo si no hay texto.

    Distinguir los dos casos importa: aplicar OCR a un PDF nativo pierde
    precision y cuesta tiempo, y no aplicarlo a un escaneado devuelve vacio.

    `claves_candidatas` son contraseñas a probar si el PDF esta protegido:
    normalmente los numeros de documento hallados en el correo.
    """
    aviso_clave = ""
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # nombre antiguo
        except ImportError:
            return Conversion(error="pymupdf no esta instalado", tipo_real="pdf")

    partes: List[str] = []
    paginas_sin_texto = 0
    total_paginas = 0
    paginas_con_error = 0
    paginas_ilegibles = 0
    motivos_ocr: List[str] = []
    imagenes_leidas = 0
    imagenes_sin_leer = 0
    # Las imagenes se identifican por su referencia dentro del PDF. Un logo o un
    # encabezado se repiten en todas las paginas: sin esto, un documento de
    # cincuenta paginas pasaria el mismo logo por OCR cincuenta veces.
    vistas: set = set()
    inicio_ocr = time.monotonic()
    # El tope de este documento se acota por lo que le quede al CORREO: un
    # correo con dieciseis escaneos multiplicaria noventa segundos por dieciseis.
    del_correo = presupuesto or PresupuestoDeOcr()
    tope = min(PRESUPUESTO_OCR_SEGUNDOS, del_correo.queda())
    manda_el_correo = tope < PRESUPUESTO_OCR_SEGUNDOS
    hilos: Optional[ThreadPoolExecutor] = None
    try:
        with pymupdf.open(stream=datos, filetype="pdf") as doc:
            # Muchos PDF marcados como cifrados solo llevan contraseña de
            # PROPIETARIO: restringen imprimir o copiar, no abrir. La bandera
            # que indica que hace falta contraseña para LEER es `needs_pass`,
            # no `is_encrypted`. Sin este intento, los PDF exportados desde
            # WhatsApp y desde varios escaneres fallan con "document closed or
            # encrypted" y su contenido se pierde entero.
            if getattr(doc, "needs_pass", False) or getattr(doc, "is_encrypted", False):
                # Muchos PDF solo llevan contraseña de propietario (vacia para
                # abrir). Si no, se prueban los documentos hallados en el correo:
                # es la clave que suele usar el remitente.
                abierto = doc.authenticate("")
                clave_usada = ""
                if not abierto:
                    for clave in (claves_candidatas or []):
                        if clave and doc.authenticate(str(clave)):
                            abierto = True
                            clave_usada = str(clave)
                            break
                if not abierto:
                    return Conversion(
                        tipo_real="pdf",
                        error="PDF protegido con contraseña de usuario: no se puede leer",
                    )
                if clave_usada:
                    aviso_clave = (
                        "el PDF venia protegido y se abrio con una clave "
                        "candidata tomada del correo"
                    )
                else:
                    aviso_clave = ""
            total_paginas = doc.page_count
            motivo_sin_presupuesto = (
                del_correo.motivo() if manda_el_correo
                else f"se agoto el presupuesto de OCR "
                     f"({PRESUPUESTO_OCR_SEGUNDOS:.0f}s) para este documento")
            hilos = ThreadPoolExecutor(max_workers=PAGINAS_OCR_EN_PARALELO)
            en_vuelo: List[Tuple[int, int, Future]] = []

            def leer(pagina):
                # Se mira el presupuesto al empezar cada hoja, no al ponerla en
                # fila: si no, todas entrarian antes de gastar un segundo.
                with _CUPO_DE_HOJAS:
                    if time.monotonic() - inicio_ocr >= tope:
                        return _LecturaDePagina("", motivo_sin_presupuesto)
                    return _ocr_de_pagina(pagina, del_correo)

            def asentar(i: int, hueco: int, lectura) -> None:
                nonlocal paginas_ilegibles
                ocr, fallo_ocr = lectura
                confianza = getattr(lectura, "confianza", None)
                if ocr:
                    partes[hueco] = (
                        f"## Pagina {i + 1} {_marca_ocr(confianza)}\n\n"
                        + _linea_dudosa(confianza, "pagina") + ocr)
                    return
                paginas_ilegibles += 1
                if fallo_ocr and fallo_ocr not in motivos_ocr:
                    motivos_ocr.append(fallo_ocr)
                partes[hueco] = (
                    f"## Pagina {i + 1}\n\n_(pagina sin texto extraible"
                    + (f": {fallo_ocr}" if fallo_ocr else "") + ")_")

            for i in range(min(total_paginas, LIMITE_PAGINAS_PDF)):
                # Una pagina corrupta no debe costar el documento entero.
                try:
                    pagina = doc.load_page(i)
                    texto = pagina.get_text("text") or ""
                except Exception as e:
                    paginas_con_error += 1
                    partes.append(
                        f"## Pagina {i + 1}\n\n_(no se pudo leer: "
                        f"{type(e).__name__})_"
                    )
                    continue
                if not texto.strip():
                    paginas_sin_texto += 1
                    if time.monotonic() - inicio_ocr >= tope:
                        partes.append("")
                        asentar(i, len(partes) - 1,
                                _LecturaDePagina("", motivo_sin_presupuesto))
                        continue
                    # Tope de hojas en vuelo: cada una es una imagen de 300 dpi
                    # en memoria hasta que termina.
                    while sum(not f.done() for _, _, f in en_vuelo) >= PAGINAS_OCR_EN_PARALELO:
                        wait([f for _, _, f in en_vuelo if not f.done()],
                             return_when=FIRST_COMPLETED)
                    partes.append("")
                    # PyMuPDF no admite otro hilo sobre el documento (en la
                    # plataforma termino en SIGSEGV): la hoja se renderiza aqui
                    # y al hilo solo va la imagen.
                    try:
                        hoja = _HojaRenderizada(pagina)
                    except Exception as e:
                        asentar(i, len(partes) - 1, _LecturaDePagina(
                            "", f"el OCR fallo ({type(e).__name__}: {e})"))
                        continue
                    # Con el contexto del correo: lo que se anota por correo
                    # (fallos de vision, eventos) tiene que verse desde el hilo.
                    en_vuelo.append((i, len(partes) - 1, hilos.submit(
                        contextvars.copy_context().run, leer, hoja)))
                    continue
                tablas = _tablas_de_pagina(pagina)
                bloque = f"## Pagina {i + 1}\n\n{normalizar(texto)}"
                if tablas:
                    bloque += "\n\n" + "\n\n".join(tablas)
                # Una pagina CON texto puede llevar ademas la foto de un
                # documento: el ciudadano escribe su solicitud y pega la cedula
                # debajo. Leer solo el texto deja ese dato fuera y no da ningun
                # aviso, asi que el correo se ve bien convertido y sale a
                # revision manual por falta de un documento que si estaba.
                queda = tope - (time.monotonic() - inicio_ocr)
                leido, sin_leer, motivo = _ocr_de_imagenes(
                    doc, pagina, vistas, queda, del_correo)
                if leido:
                    bloque += "\n\n" + leido
                    imagenes_leidas += len(leido.split("### Imagen")) - 1
                if sin_leer:
                    imagenes_sin_leer += sin_leer
                    if motivo and motivo not in motivos_ocr:
                        motivos_ocr.append(motivo)
                partes.append(bloque)
            try:
                for i, hueco, futuro in en_vuelo:
                    try:
                        lectura = futuro.result()
                    except Exception as e:
                        lectura = _LecturaDePagina(
                            "", f"el OCR fallo ({type(e).__name__}: {e})")
                    asentar(i, hueco, lectura)
            finally:
                hilos.shutdown(wait=True)
    except Exception as e:
        if hilos is not None:
            # Ninguna hoja sigue leyendose sobre un documento ya cerrado.
            hilos.shutdown(wait=True, cancel_futures=True)
        # El detalle importa: "document closed or encrypted" a secas no permite
        # distinguir un PDF con contraseña de uno truncado.
        detalle = (
            f"{type(e).__name__}: {e}"
            f" | bytes={len(datos)}"
            f" | inicia_con_pdf={datos[:5] == b'%PDF-'}"
            f" | tiene_eof={b'%%EOF' in datos[-2048:]}"
        )
        return Conversion(error=detalle, tipo_real="pdf")

    del_correo.gastar(time.monotonic() - inicio_ocr)

    md, truncado = _truncar("\n\n".join(partes))
    # La causa va DELANTE de los dos puntos y las cifras detras: el informe
    # agrupa los avisos por lo que va antes, y con el numero de paginas delante
    # salia una clase por PDF, que desplazaba del informe a todo lo demas.
    avisos = [a for a in (aviso_clave,) if a]
    if total_paginas > LIMITE_PAGINAS_PDF:
        avisos.append(
            f"el PDF excede el limite de paginas: {total_paginas}, se "
            f"convirtieron las primeras {LIMITE_PAGINAS_PDF}")
    if paginas_con_error:
        avisos.append(
            f"paginas que no se pudieron leer: {paginas_con_error}")
    if imagenes_sin_leer:
        # Sin este aviso el correo se ve perfectamente convertido y sale a
        # revision manual por un documento que venia en la imagen.
        avisos.append(
            f"imagenes incrustadas que no se pudieron leer: "
            f"{imagenes_sin_leer} — " + (motivos_ocr[-1] if motivos_ocr
                                         else "el OCR no encontro nada"))
    if paginas_ilegibles:
        # Sin este aviso, un escaneo cuyo OCR no corrio se ve igual que un
        # documento que de verdad no dice nada, y el correo se manda a revision
        # manual con el motivo equivocado.
        # Los motivos del OCR se unen con " / ": el "; " queda reservado para
        # separar causas distintas, que es como las lee el manifiesto.
        avisos.append(
            f"paginas sin texto extraible: {paginas_ilegibles} de "
            f"{total_paginas} — "
            + (" / ".join(motivos_ocr) or "el OCR no encontro nada")
        )
    return Conversion(
        markdown=_encabezado(nombre, md),
        tipo_real="pdf",
        # "(OCR" y no "(OCR)": el encabezado puede llevar la confianza detras.
        ocr_aplicado=(paginas_sin_texto > 0 or imagenes_leidas > 0)
                     and "(OCR" in md,
        truncado=truncado,
        aviso="; ".join(avisos),
        paginas_ilegibles=paginas_ilegibles,
        paginas=total_paginas,
    )


def _marca_ocr(confianza: Optional[float]) -> str:
    """
    Lo que va entre parentesis en el encabezado de un bloque leido por OCR.

    Siempre empieza por "(OCR": `ocr_aplicado` lo busca asi en el markdown.
    """
    if confianza is None:
        return "(OCR)"
    return f"(OCR, confianza {confianza:.2f})"


def _linea_dudosa(confianza: Optional[float], que: str) -> str:
    """Aviso en el propio markdown cuando la lectura salio poco fiable."""
    if confianza is None or confianza >= CONFIANZA_DUDOSA:
        return ""
    return f"_(lectura dudosa: verificar los datos de esta {que})_\n\n"


def _umbral_otsu(gris) -> int:
    """Umbral de Otsu a partir del histograma, sin numpy ni OpenCV."""
    hist = gris.histogram()[:256]
    total = sum(hist)
    suma_total = sum(i * h for i, h in enumerate(hist))
    peso_fondo = suma_fondo = 0.0
    mejor, umbral = -1.0, 127
    for t in range(256):
        peso_fondo += hist[t]
        if peso_fondo == 0:
            continue
        peso_frente = total - peso_fondo
        if peso_frente == 0:
            break
        suma_fondo += t * hist[t]
        media_fondo = suma_fondo / peso_fondo
        media_frente = (suma_total - suma_fondo) / peso_frente
        varianza = peso_fondo * peso_frente * (media_fondo - media_frente) ** 2
        if varianza > mejor:
            mejor, umbral = varianza, t
    return umbral


def _tinta(gris):
    """Imagen binaria con la tinta en blanco (255) y el fondo en negro."""
    umbral = _umbral_otsu(gris)
    return gris.point([255 if v <= umbral else 0 for v in range(256)])


def _medias_de_filas(imagen) -> List[int]:
    """Media de cada fila. Reducir a un pixel de ancho con BOX la calcula en C."""
    from PIL import Image

    return list(imagen.resize((1, imagen.height), Image.BOX).tobytes())


def _puntaje_inclinacion(tinta, angulo: float) -> float:
    """Varianza de las diferencias del perfil de filas: maxima con las lineas derechas."""
    from PIL import Image

    perfil = _medias_de_filas(tinta.rotate(angulo, resample=Image.NEAREST))
    difs = [b - a for a, b in zip(perfil, perfil[1:])]
    if not difs:
        return 0.0
    media = sum(difs) / len(difs)
    return sum((d - media) ** 2 for d in difs) / len(difs)


def _estimar_inclinacion(gris) -> float:
    """
    Angulo que endereza las lineas de texto, por perfil de proyeccion: las
    filas de una pagina derecha alternan entre llenas y vacias, y esa varianza
    es maxima en el angulo correcto. Primero cada grado y luego se afina
    alrededor del mejor, sobre una miniatura.
    """
    miniatura = gris.copy()
    miniatura.thumbnail((LADO_MINIATURA_PX, LADO_MINIATURA_PX))
    tinta = _tinta(miniatura)
    tope = int(INCLINACION_MAXIMA_GRADOS)
    grueso = [float(a) for a in range(-tope, tope + 1)]
    mejor = max(grueso, key=lambda a: _puntaje_inclinacion(tinta, a))
    fino = [mejor + d for d in (-PASO_INCLINACION_GRADOS, PASO_INCLINACION_GRADOS)]
    mejor = max([mejor] + fino, key=lambda a: _puntaje_inclinacion(tinta, a))
    return round(mejor, 2)


def _recortar_bordes(gris):
    """Quita el marco oscuro que deja el escaner alrededor de la hoja."""
    from PIL import Image

    tinta = _tinta(gris)
    ancho, alto = gris.size
    # Una fila o columna con mas del 90 % de tinta es marco, no texto.
    filas = [i for i, v in enumerate(_medias_de_filas(tinta)) if v < 0.9 * 255]
    columnas = [i for i, v in enumerate(
        tinta.resize((ancho, 1), Image.BOX).tobytes()) if v < 0.9 * 255]
    if not filas or not columnas:
        return gris
    margen = 10
    arriba, abajo = max(0, filas[0] - margen), min(alto, filas[-1] + margen)
    izq, der = max(0, columnas[0] - margen), min(ancho, columnas[-1] + margen)
    # Solo se recorta si sobra al menos el 60 % de la hoja: si no, el umbral
    # confundio una pagina muy cargada con un borde.
    if (abajo - arriba) * (der - izq) < 0.6 * ancho * alto:
        return gris
    return gris.crop((izq, arriba, der, abajo))


def _preparar_para_ocr(imagen):
    """
    Escala de grises, ancho util, inclinacion fina, sin marco y con contraste.

    Devuelve (imagen, pasos). El giro de 90 o 180 grados NO se hace aqui: es
    otra pasada de tesseract y casi todas las paginas vienen derechas, asi que
    solo se busca si la primera lectura sale floja (ver `_texto_de_imagen`).
    """
    from PIL import Image, ImageOps

    pasos: List[str] = []
    try:
        # Las fotos del movil traen el giro en el EXIF, no en los pixeles.
        imagen = ImageOps.exif_transpose(imagen)
    except Exception:
        pass
    gris = imagen.convert("L")

    if gris.width < ANCHO_MINIMO_OCR_PX:
        factor = ANCHO_MINIMO_OCR_PX / gris.width
        gris = gris.resize(
            (ANCHO_MINIMO_OCR_PX, max(1, int(gris.height * factor))), Image.LANCZOS)
        pasos.append(f"ampliada x{factor:.1f}")
    elif gris.width > ANCHO_MAXIMO_OCR_PX:
        factor = ANCHO_MAXIMO_OCR_PX / gris.width
        gris = gris.resize(
            (ANCHO_MAXIMO_OCR_PX, max(1, int(gris.height * factor))), Image.LANCZOS)
        pasos.append(f"reducida x{factor:.2f}")

    inclinacion = _estimar_inclinacion(gris)
    # Si el mejor angulo cae en el borde del rango no es una inclinacion: la
    # hoja esta de lado o no tiene renglones, y "enderezarla" 5 grados la
    # estropea (foto de una orden medica tomada de lado).
    if PASO_INCLINACION_GRADOS <= abs(inclinacion) < INCLINACION_MAXIMA_GRADOS:
        gris = gris.rotate(inclinacion, resample=Image.BICUBIC, expand=True,
                           fillcolor=255)
        pasos.append(f"enderezada {inclinacion}")

    recortada = _recortar_bordes(gris)
    if recortada.size != gris.size:
        pasos.append("bordes recortados")
        gris = recortada

    return ImageOps.autocontrast(gris, cutoff=1), pasos


def _orientacion(gris, segundos: float) -> Tuple[int, float]:
    """Grados a girar segun el OSD de tesseract y su confianza; (0, 0) si no se pudo."""
    import pytesseract

    try:
        datos = pytesseract.image_to_osd(
            _sin_comprimir(gris), config="--psm 0", output_type=pytesseract.Output.DICT,
            timeout=max(1.0, segundos))
        return (int(datos.get("rotate", 0)) % 360,
                float(datos.get("orientation_conf", 0.0)))
    except Exception:
        # El OSD falla en paginas con poco texto; no es un fallo de la lectura.
        return 0, 0.0


def _caracteres_utiles(texto: str) -> int:
    return len(re.findall(r"[0-9A-Za-zÁÉÍÓÚÑÜáéíóúñü]", texto or ""))


def _vale_mas(texto_a: str, conf_a: float, texto_b: str, conf_b: float) -> bool:
    """
    Si la lectura A es mejor que la B: caracteres utiles por confianza. Solo la
    confianza premiaria leer tres palabras seguras sobre un parrafo entero.
    """
    return _caracteres_utiles(texto_a) * conf_a > _caracteres_utiles(texto_b) * conf_b


def _floja_para_vision(texto: str, confianza: Optional[float]) -> bool:
    """Si la lectura de tesseract es tan floja que conviene que la lea el modelo."""
    return ((confianza or 0.0) < CONFIANZA_PARA_VISION
            or _caracteres_utiles(texto) < MINIMO_CARACTERES_OCR)


def _floja(texto: str, confianza: float) -> bool:
    return (confianza < CONFIANZA_REINTENTO_OSD
            or _caracteres_utiles(texto) < MINIMO_CARACTERES_OCR)


def _texto_y_confianza(datos: dict) -> Tuple[str, float]:
    """
    Reconstruye el texto de `image_to_data` y su confianza 0-1, promediada por
    palabra y ponderada por su longitud. Los parrafos quedan separados por una
    linea en blanco, como los dejaba `image_to_string`.
    """
    lineas: dict = {}
    suma = peso = 0.0
    for i, palabra in enumerate(datos.get("text", [])):
        palabra = (palabra or "").strip()
        try:
            conf = float(datos["conf"][i])
        except (TypeError, ValueError):
            continue
        if not palabra or conf < 0:
            continue
        clave = (datos["block_num"][i], datos["par_num"][i], datos["line_num"][i])
        lineas.setdefault(clave, []).append(palabra)
        suma += conf * len(palabra)
        peso += len(palabra)
    partes: List[str] = []
    anterior = None
    for clave, palabras in sorted(lineas.items()):
        if anterior is not None:
            partes.append("\n\n" if clave[:2] != anterior[:2] else "\n")
        partes.append(" ".join(palabras))
        anterior = clave
    return normalizar("".join(partes)), (suma / peso / 100.0) if peso else 0.0


def _sin_comprimir(imagen):
    """
    La imagen marcada para que pytesseract la pase a tesseract sin comprimir.

    pytesseract la escribe a un archivo temporal en el formato que diga la
    imagen, y sin formato usa PNG: comprimir una hoja a 300 dpi costaba un tercio
    de segundo por lectura, mas que el resto del trabajo propio. PGM y PPM son
    los mismos pixeles sin comprimir (PIL guarda los tres como "PPM").
    """
    if imagen.mode in ("1", "L", "RGB"):
        imagen.format = "PPM"
    return imagen


def _tesseract(gris, segundos: float) -> Tuple[str, float]:
    import pytesseract

    datos = pytesseract.image_to_data(
        _sin_comprimir(gris), lang=IDIOMAS_OCR, config="--oem 1 --psm 3",
        output_type=pytesseract.Output.DICT, timeout=max(1.0, segundos))
    return _texto_y_confianza(datos)


def _texto_de_imagen(imagen, segundos: float, preparar: bool = True,
                     presupuesto: Optional["PresupuestoDeOcr"] = None
                     ) -> Tuple[str, Optional[float], str]:
    """
    OCR de una imagen con tesseract y, si no la pudo leer, con el modelo.

    Tesseract no lee una foto de celular de una orden medica sobre fondo
    oscuro, ni el rotulo blanco diminuto de una captura de radiografia: el
    modelo con vision si (medido sobre fotos reales de un correo del buzon). Se
    le pasa solo lo que tesseract dejo flojo, con un tope de imagenes por
    correo en `presupuesto`, porque cada una cuesta una llamada. Lo transcrito
    se marca como tal: es lectura de un modelo, no del documento.
    """
    texto, confianza, motivo = _texto_de_imagen_tesseract(
        imagen, segundos, preparar)
    return _con_respaldo_ia(imagen, texto, confianza, motivo, presupuesto)


def _con_respaldo_ia(imagen, texto: str, confianza: Optional[float],
                     motivo: str, presupuesto: Optional["PresupuestoDeOcr"]
                     ) -> Tuple[str, Optional[float], str]:
    """La lectura de tesseract, o la del modelo si tesseract la dejo floja."""
    transcribir = TRANSCRIPTOR_IA
    if (transcribir is None or presupuesto is None
            or not _floja_para_vision(texto, confianza)):
        return texto, confianza, motivo
    # Las hojas de un PDF se leen a la vez: el cupo se toma bajo candado.
    with _CERROJO_DEL_CUPO_IA:
        if presupuesto.imagenes_ia <= 0:
            return texto, confianza, motivo
        presupuesto.imagenes_ia -= 1
    try:
        salida = io.BytesIO()
        imagen.convert("RGB").save(salida, format="JPEG", quality=90)
        transcrito = normalizar(transcribir(salida.getvalue()) or "")
    except Exception:
        return texto, confianza, motivo
    if (transcrito and transcrito.strip().upper() != "SIN_TEXTO"
            and _caracteres_utiles(transcrito) > _caracteres_utiles(texto)):
        return MARCA_TRANSCRITO_POR_IA + "\n\n" + transcrito, None, ""
    return texto, confianza, motivo


# Lo que el modelo transcribe de una imagen va precedido de esta linea, para
# que quien lea los datos de ahi sepa que no salen del OCR del documento.
MARCA_TRANSCRITO_POR_IA = (
    "_(transcrito por IA desde la imagen, que el OCR no pudo leer: "
    "verificar las cifras)_")

# Funcion que transcribe una imagen (bytes JPEG) con el modelo, o None. La
# pone el orquestador cuando trabaja con Vertex; en pruebas y sin modelo queda
# en None y el OCR es solo tesseract.
TRANSCRIPTOR_IA: Optional[Callable[[bytes], str]] = None
_CERROJO_DEL_CUPO_IA = threading.Lock()


def _texto_de_imagen_tesseract(imagen, segundos: float,
                               preparar: bool = True) -> Tuple[str, float, str]:
    # La preparacion y tesseract son CPU y memoria: a la vez, solo tantas
    # lecturas como nucleos. Lo que espera aqui no gasta el tope de la hoja.
    with _CUPO_TESSERACT:
        return _texto_de_imagen_tesseract_sin_cupo(imagen, segundos, preparar)


def _texto_de_imagen_tesseract_sin_cupo(imagen, segundos: float,
                                        preparar: bool = True) -> Tuple[str, float, str]:
    """
    OCR de una imagen ya cargada, con tope de tiempo. Devuelve
    (texto, confianza 0-1, motivo_del_fallo).

    La imagen se prepara antes (ver `_preparar_para_ocr`) y se lee una vez sin
    mirar el giro. Solo si esa lectura sale floja se busca si esta de lado o al
    reves (ver `_giro_probable`) y se relee girada, quedandose con la de mayor
    confianza: es lo que rescata las fotos de cedula tomadas de lado.

    Todo cabe en `segundos`: el OSD y la relectura usan lo que deja la primera
    lectura, y si se cortan por tiempo se conserva la primera. Sin tope una
    sola pagina puede tardar minutos y desbordar el presupuesto del documento,
    que solo se comprueba entre paginas.
    """
    limite = time.monotonic() + segundos
    try:
        # Una hoja de PDF renderizada ya viene derecha, a 300 dpi y con buen
        # contraste: el enderezado, el recorte y el autocontraste le borran
        # trazos a los digitos (sobre las 203 hojas de referencia, 80 % de
        # numeros de documento sin preparar y 74 % preparando). Lo que si
        # necesita preparacion es la foto suelta: pequeña, de lado, con EXIF.
        if preparar:
            gris, _pasos = _preparar_para_ocr(imagen)
        else:
            gris = _sin_cuadricula(imagen.convert("L"))
    except Exception as e:
        return "", 0.0, f"no se pudo preparar la imagen ({type(e).__name__}: {e})"
    try:
        texto, confianza = _tesseract(gris, limite - time.monotonic())
    except RuntimeError:
        # Es lo que lanza pytesseract cuando se agota su propio timeout.
        return "", 0.0, f"el OCR de una pagina paso de {segundos:.0f}s y se corto"
    except Exception as e:
        return "", 0.0, f"el OCR fallo ({type(e).__name__}: {e})"

    if preparar and _floja(texto, confianza) and limite - time.monotonic() > 2:
        # La preparacion no siempre ayuda: en una foto de celular de una hoja
        # de lado, ampliar y "enderezar" dentro de +-5 grados la deja ilegible,
        # cuando tesseract la leia tal como llego (medido sobre ordenes medicas
        # fotografiadas). Se lee tambien la imagen sin preparar y gana la mejor.
        try:
            cruda = imagen.convert("L")
            texto_c, confianza_c = _tesseract(cruda, limite - time.monotonic())
            if _vale_mas(texto_c, confianza_c, texto, confianza):
                texto, confianza, gris = texto_c, confianza_c, cruda
        except Exception:
            pass

    if _floja(texto, confianza) and limite - time.monotonic() > 2:
        rotacion = _giro_probable(gris, confianza, limite)
        queda = limite - time.monotonic()
        if rotacion and queda > 1:
            # Grados en sentido horario; PIL gira antihorario.
            girada = gris.rotate(-rotacion, expand=True, fillcolor=255)
            try:
                texto2, confianza2 = _tesseract(girada, queda)
            except Exception:
                # Se corto la relectura: la primera lectura sigue valiendo.
                texto2, confianza2 = "", 0.0
            if confianza2 > confianza:
                texto, confianza = texto2, confianza2
    return texto, round(confianza, 2), ""


def _giro_probable(gris, confianza: float, limite: float) -> int:
    """
    Grados (horarios) que probablemente enderezan una lectura floja; 0 si no
    hay motivo para girar.

    Primero el OSD de tesseract, que es una sola pasada. Pero con poco texto
    —justo el caso de una cedula— el OSD duda o se equivoca: medido sobre
    imagenes sinteticas, con una cedula de lado o al reves daba confianza por
    debajo de 1 o giro 0. Si no esta seguro y la lectura es muy mala, se lee
    una miniatura en cada giro y manda la confianza de tesseract. La lectura
    completa del giro elegido se compara despues con la primera, asi que un
    giro equivocado nunca empeora el resultado; solo cuesta tiempo.
    """
    queda = limite - time.monotonic()
    rotacion, seguridad = _orientacion(gris, max(1.0, queda / 3))
    if rotacion and seguridad >= CONFIANZA_MINIMA_OSD:
        return rotacion
    if confianza >= CONFIANZA_DUDOSA:
        return 0
    miniatura = gris.copy()
    miniatura.thumbnail((LADO_SONDEO_GIRO_PX, LADO_SONDEO_GIRO_PX))
    mejor, mejor_confianza = 0, confianza + MEJORA_MINIMA_POR_GIRO
    for grados in (90, 270, 180):
        # Se deja tiempo para la relectura completa del giro que gane.
        queda = limite - time.monotonic()
        if queda < 4:
            break
        try:
            _t, sondeo = _tesseract(
                miniatura.rotate(-grados, expand=True, fillcolor=255), queda / 3)
        except Exception:
            continue
        if sondeo > mejor_confianza:
            mejor, mejor_confianza = grados, sondeo
        if mejor_confianza >= CONFIANZA_REINTENTO_OSD:
            # Ya no es una lectura floja: los demas giros no van a ganar.
            break
    return mejor


class _LecturaDePagina(tuple):
    """
    (texto, motivo) de una pagina, con la confianza del OCR como atributo.

    Sigue siendo un par para quien lo desempaqueta en dos —y para los dobles de
    prueba que devuelven una tupla simple—; la confianza se lee con getattr.
    """

    confianza: Optional[float]

    def __new__(cls, texto: str, motivo: str,
                confianza: Optional[float] = None):
        lectura = super().__new__(cls, (texto, motivo))
        lectura.confianza = confianza
        return lectura


# Lado mayor, en pixeles, de una hoja renderizada para OCR: un oficio a 300 dpi.
# Hay PDF cuya pagina mide lo que mide la foto que llevan (2550 x 3507 puntos):
# a 300 dpi eran 155 megapixeles, casi medio giga por hoja, tesseract se cortaba
# por tiempo y la memoria del proceso pasaba de 7 GB. Mas alla de la resolucion
# de la foto no hay nada que leer.
LADO_MAXIMO_OCR_PX = 4200


def _dpi_de_pagina(pagina, dpi_deseado: Optional[int] = None) -> int:
    """La resolucion que lleva la pagina al ancho minimo de OCR, entre topes."""
    try:
        ancho_pt = float(pagina.rect.width) or 612.0
        alto_pt = float(pagina.rect.height) or 792.0
    except Exception:
        return DPI_MINIMO_OCR
    necesario = int(-(-ANCHO_MINIMO_OCR_PX * 72 // ancho_pt))
    dpi = dpi_deseado or max(DPI_MINIMO_OCR, min(DPI_MAXIMO_OCR, necesario))
    tope = int(LADO_MAXIMO_OCR_PX * 72 // max(ancho_pt, alto_pt, 1.0))
    return max(36, min(dpi, tope))


def _imagen_de_pagina(pagina, dpi: int):
    """
    La hoja renderizada como imagen de PIL.

    Se arma con los pixeles del render y no pasando por PNG: a 300 dpi
    comprimir y descomprimir la hoja costaba casi medio segundo y no cambia
    un solo pixel.
    """
    from PIL import Image

    if isinstance(pagina, _HojaRenderizada):
        return pagina.a_dpi(dpi)
    pix = pagina.get_pixmap(dpi=dpi)
    muestras = getattr(pix, "samples", None)
    n = getattr(pix, "n", 0)
    if isinstance(muestras, (bytes, bytearray, memoryview)) and n in (1, 3, 4):
        modo = {1: "L", 3: "RGB", 4: "RGBA"}[n]
        imagen = Image.frombytes(modo, (pix.width, pix.height), bytes(muestras))
        return imagen.convert("RGB") if modo == "RGBA" else imagen
    return Image.open(io.BytesIO(pix.tobytes("png")))


# Una hoja escaneada en blanco (el reverso de una hoja, una separadora) no tiene
# nada que leer, y costaba lo que la peor: tesseract la leia entera, como salia
# floja buscaba el giro con tres sondeos y al final se la mandaba al modelo con
# vision, que respondia SIN_TEXTO. La tinta se mide sobre la mitad de la
# resolucion: un pixel cuenta si es bastante mas oscuro que el fondo de la hoja.
FRACCION_DE_TINTA_DE_UNA_HOJA_EN_BLANCO = 0.0005
CONTRASTE_DE_TINTA = 80
MOTIVO_EN_BLANCO = "la pagina esta en blanco"


def fraccion_de_tinta(imagen) -> float:
    """La parte de la hoja que es tinta, de 0 a 1."""
    gris = imagen.convert("L")
    if min(gris.size) >= 400:
        gris = gris.reduce(2)
    hist = gris.histogram()[:256]
    total = sum(hist) or 1
    # El fondo es el tono mas comun de la mitad clara.
    fondo = max(range(128, 256), key=lambda v: hist[v])
    return sum(hist[:max(0, fondo - CONTRASTE_DE_TINTA)]) / total


def _en_blanco(imagen) -> bool:
    try:
        return fraccion_de_tinta(imagen) < FRACCION_DE_TINTA_DE_UNA_HOJA_EN_BLANCO
    except Exception:
        return False


class _HojaRenderizada:
    """
    Una pagina ya renderizada, para leerla en otro hilo.

    Se renderiza en el hilo del documento: PyMuPDF no admite otro hilo sobre
    el mismo documento. La relectura a menor resolucion, si hace falta, se
    saca de esta misma imagen.
    """

    def __init__(self, pagina) -> None:
        self.number = getattr(pagina, "number", None)
        r = pagina.rect
        self.rect = type("Rect", (), {"width": float(r.width), "height": float(r.height)})()
        self.dpi = _dpi_de_pagina(pagina)
        self.imagen = _imagen_de_pagina(pagina, self.dpi)

    def a_dpi(self, dpi: int):
        if dpi >= self.dpi:
            return self.imagen
        from PIL import Image

        factor = dpi / self.dpi
        return self.imagen.resize((max(1, int(self.imagen.width * factor)),
                                   max(1, int(self.imagen.height * factor))), Image.LANCZOS)


def _ocr_de_pagina(pagina, presupuesto: Optional["PresupuestoDeOcr"] = None
                   ) -> Tuple[str, str]:
    """
    OCR de una pagina. Devuelve (texto, motivo_del_fallo), con la confianza
    en el atributo `confianza` del par (ver `_LecturaDePagina`).

    No revienta si falta tesseract: el binario no siempre esta instalado. Pero
    el motivo se devuelve, porque "el OCR no corrio" y "el documento no dice
    nada" llevan a conclusiones opuestas: la primera es un problema de entorno
    que se arregla, la segunda manda el correo a revision manual.
    """
    falta = _motor_de_ocr_que_falta()
    if falta:
        return _LecturaDePagina("", f"no hay motor de OCR instalado ({falta})")
    from PIL import Image

    # El render cuenta dentro del tope de la pagina.
    inicio = time.monotonic()
    try:
        imagen = _imagen_de_pagina(pagina, _dpi_de_pagina(pagina))
    except Exception as e:
        return _LecturaDePagina("", f"el OCR fallo ({type(e).__name__}: {e})")
    if _en_blanco(imagen):
        return _LecturaDePagina("", MOTIVO_EN_BLANCO)
    # A 300 dpi una hoja densa con el job cargado puede pasar del tope y
    # perderse entera. Se le deja a la lectura principal solo parte del tope,
    # para que si se corta quede tiempo de leerla a 200 dpi: una lectura algo
    # peor es mejor que una hoja que la IA nunca ve.
    queda = max(1.0, TIMEOUT_OCR_PAGINA_S * FRACCION_DEL_TOPE_A_300_DPI
                - (time.monotonic() - inicio))
    texto, confianza, motivo = _texto_de_imagen(imagen, queda, preparar=False)
    if not texto and motivo:
        queda = TIMEOUT_OCR_PAGINA_S - (time.monotonic() - inicio)
        if queda > 3:
            try:
                menor = _imagen_de_pagina(
                    pagina, min(_dpi_de_pagina(pagina, DPI_DE_RESPALDO_OCR),
                                _dpi_de_pagina(pagina) * 2 // 3))
                texto, confianza, motivo2 = _texto_de_imagen(
                    menor, queda, preparar=False)
                motivo = motivo2 if not texto else ""
            except Exception:
                pass
    texto, confianza, motivo = _con_respaldo_ia(
        imagen, texto, confianza, motivo, presupuesto)
    return _LecturaDePagina(texto, motivo, confianza if texto else None)


def _tramos_de_tinta(tinta, largo: int):
    """
    Pixeles que forman parte de un tramo horizontal de tinta de al menos
    `largo` pixeles seguidos. Para los verticales se le pasa la traspuesta.
    """
    import numpy as np

    ancho = tinta.shape[1]
    salida = np.zeros(tinta.shape, dtype=bool)
    # Por franjas de filas, que son independientes: de una vez, una hoja a 300
    # dpi pedia cerca de 300 MB de arreglos intermedios, y con varias hojas a la
    # vez el proceso llegaba a 13 GB.
    for ini in range(0, tinta.shape[0], FILAS_POR_FRANJA):
        franja = tinta[ini: ini + FILAS_POR_FRANJA]
        suma = np.cumsum(np.pad(franja.astype(np.int32), ((0, 0), (1, 0))), axis=1)
        # Donde EMPIEZA un tramo completo de `largo`.
        empieza = (suma[:, largo:] - suma[:, :-largo]) == largo
        empieza = np.pad(empieza.astype(np.int32),
                         ((0, 0), (0, ancho - empieza.shape[1])))
        # Un pixel es del tramo si alguno de los `largo` anteriores lo empezo.
        acumulado = np.pad(np.cumsum(empieza, axis=1), ((0, 0), (largo, 0)))
        salida[ini: ini + FILAS_POR_FRANJA] = (acumulado[:, largo:] - acumulado[:, :ancho]) > 0
    return salida


FILAS_POR_FRANJA = 256


def _sin_cuadricula(gris):
    """
    La hoja sin las lineas de tabla.

    Las planillas y listados traen cada cedula dentro de una celda, y la raya de
    la celda pegada a los digitos hace que tesseract los lea como otra cosa o no
    los lea. Una linea de tabla es un tramo de tinta recto mucho mas largo que
    cualquier letra; se borra eso y nada mas. Sobre las 203 hojas de referencia,
    los numeros de documento recuperados pasan de 80 % a 84 % en una lectura, y
    la lectura es mas rapida.

    Si numpy no esta (viene con pandas), la hoja se lee como esta.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return gris
    try:
        a = np.asarray(gris)
        if a.ndim != 2 or min(a.shape) < 50:
            return gris
        # Umbral de Otsu.
        hist = np.bincount(a.ravel(), minlength=256).astype(np.float64)
        peso = np.cumsum(hist)
        masa = np.cumsum(hist * np.arange(256))
        total = peso[-1]
        varianza = (masa[-1] * peso - masa * total) ** 2 / np.maximum(
            peso * (total - peso), 1)
        tinta = a <= int(np.argmax(varianza))
        # Una vigesimoquinta parte del ancho: a 300 dpi son unos 100 px, y la
        # palabra mas larga de una linea de texto no hace un tramo continuo.
        largo = max(40, a.shape[1] // 25)
        lineas = (_tramos_de_tinta(tinta, largo)
                  | _tramos_de_tinta(tinta.T, largo).T)
        if not lineas.any():
            return gris
        limpia = a.copy()
        limpia[lineas] = 255
        return Image.fromarray(limpia)
    except Exception:
        return gris


def _motor_de_ocr_que_falta() -> str:
    """El modulo de OCR que no esta instalado, o cadena vacia si estan los dos."""
    import importlib.util

    for modulo in ("pytesseract", "PIL"):
        if importlib.util.find_spec(modulo) is None:
            return modulo
    return ""


def _ocr_de_imagenes(
    doc, pagina, vistas: set, segundos_restantes: float,
    presupuesto: Optional["PresupuestoDeOcr"] = None,
) -> Tuple[str, int, str]:
    """
    Lee las imagenes incrustadas de una pagina que YA tiene texto.

    Devuelve (markdown, sin_leer, motivo). `sin_leer` son las que NO se pudieron
    leer —el OCR fallo, se corto por tiempo o el presupuesto se agoto—, no las
    que se leyeron y no decian nada: la foto de un ojo o una firma no llevan
    texto y avisar de ellas seria una alarma por correo. Una que si se intento y
    no se pudo leer, en cambio, puede ser la cedula que falta.

    Solo las imagenes que ocupan buena parte de la hoja, y solo una vez cada
    una: un membrete se repite en todas las paginas.
    """
    if _motor_de_ocr_que_falta():
        return "", 0, ""
    from PIL import Image

    bloques: List[str] = []
    sin_leer = 0
    motivo = ""
    try:
        incrustadas = pagina.get_images(full=True)
        area_pagina = abs(pagina.rect.width * pagina.rect.height) or 1.0
    except Exception:
        return "", 0, ""

    candidatas = 0
    for info in incrustadas:
        if candidatas >= MAX_IMAGENES_POR_PAGINA:
            break
        xref, ancho, alto = info[0], info[2], info[3]
        if min(ancho or 0, alto or 0) < LADO_MINIMO_DE_IMAGEN_PX:
            continue
        if xref in vistas:
            continue
        cubre = _fraccion_de_pagina(pagina, xref, area_pagina)
        if not (FRACCION_MINIMA_DE_PAGINA <= cubre <= FRACCION_MAXIMA_DE_PAGINA):
            continue
        vistas.add(xref)
        candidatas += 1
        if segundos_restantes <= 0:
            sin_leer += 1
            motivo = (
                f"se agoto el presupuesto de OCR "
                f"({PRESUPUESTO_OCR_SEGUNDOS:.0f}s) antes de leer una imagen "
                f"incrustada")
            continue
        try:
            crudo = doc.extract_image(xref)
            imagen = Image.open(io.BytesIO(crudo["image"]))
        except Exception as e:
            sin_leer += 1
            motivo = f"no se pudo abrir una imagen incrustada ({type(e).__name__})"
            continue
        inicio = time.monotonic()
        texto, confianza, fallo = _texto_de_imagen(
            imagen, min(TIMEOUT_OCR_PAGINA_S, segundos_restantes),
            presupuesto=presupuesto)
        segundos_restantes -= time.monotonic() - inicio
        if texto.strip():
            bloques.append(
                f"### Imagen incrustada {_marca_ocr(confianza)}\n\n"
                + _linea_dudosa(confianza, "imagen") + texto)
            continue
        if fallo:
            sin_leer += 1
            motivo = fallo
    return "\n\n".join(bloques), sin_leer, motivo


def _fraccion_de_pagina(pagina, xref: int, area_pagina: float) -> float:
    """Cuanto de la hoja ocupa la imagen, en el sitio donde esta puesta."""
    try:
        mayor = 0.0
        for rect in pagina.get_image_rects(xref):
            mayor = max(mayor, abs(rect.width * rect.height) / area_pagina)
        return mayor
    except Exception:
        # Sin saber su tamaño en la hoja se prefiere pagar el OCR antes que
        # perder el dato de una cedula: se devuelve un valor dentro de la banda.
        return FRACCION_MINIMA_DE_PAGINA


def _tablas_de_pagina(pagina) -> List[str]:
    """Extrae tablas del PDF y las emite como tablas Markdown."""
    salida: List[str] = []
    try:
        encontradas = pagina.find_tables()
    except Exception:
        return salida
    for tabla in getattr(encontradas, "tables", []) or []:
        try:
            filas = tabla.extract()
        except Exception:
            continue
        md = _filas_a_tabla_md(filas)
        if md:
            salida.append(md)
    return salida


def _filas_a_tabla_md(filas) -> str:
    """Convierte filas a tabla Markdown, que el modelo interpreta bien."""
    limpias = [
        [normalizar(str(c)) if c is not None else "" for c in fila]
        for fila in (filas or []) if fila
    ]
    limpias = [f for f in limpias if any(c.strip() for c in f)]
    if len(limpias) < 2:
        return ""
    ancho = max(len(f) for f in limpias)
    limpias = [f + [""] * (ancho - len(f)) for f in limpias]
    cabecera, resto = limpias[0], limpias[1:]
    lineas = [
        "| " + " | ".join(c.replace("|", "\\|") or " " for c in cabecera) + " |",
        "|" + "|".join(["---"] * ancho) + "|",
    ]
    for fila in resto[:LIMITE_FILAS_HOJA]:
        lineas.append("| " + " | ".join(c.replace("|", "\\|") or " " for c in fila) + " |")
    return "\n".join(lineas)


def _imagen_a_markdown(
    datos: bytes, nombre: str, presupuesto: Optional[PresupuestoDeOcr] = None
) -> Conversion:
    if _motor_de_ocr_que_falta():
        return Conversion(
            tipo_real="imagen",
            error="sin motor de OCR (pytesseract o Pillow no instalados)",
        )
    from PIL import Image

    # Una imagen suelta tambien gasta OCR del correo: un correo con quince
    # fotos de la cedula las multiplicaria igual que los PDF.
    del_correo = presupuesto or PresupuestoDeOcr()
    if del_correo.agotado:
        return Conversion(tipo_real="imagen", paginas=1, paginas_ilegibles=1,
                          aviso=del_correo.motivo())
    inicio = time.monotonic()
    try:
        imagen = Image.open(io.BytesIO(datos))
        if imagen.format == "TIFF" and getattr(imagen, "n_frames", 1) > 1:
            return _tiff_de_varias_hojas(imagen, nombre, del_correo)
        # El OCR no admite todos los modos de color: CMYK de un escaner, paleta
        # con transparencia o 16 bits por canal lo hacen fallar. RGB y escala de
        # grises los acepta siempre, y convertir no pierde nada legible.
        # (El giro del EXIF se aplica al preparar la imagen para el OCR.)
        if imagen.mode not in ("RGB", "L"):
            imagen = imagen.convert("RGB")
        texto, confianza, fallo = _texto_de_imagen(
            imagen, min(TIMEOUT_OCR_PAGINA_S, del_correo.queda()),
            presupuesto=del_correo)
        if fallo:
            return Conversion(tipo_real="imagen", paginas=1,
                              paginas_ilegibles=1, aviso=fallo)
    except Exception as e:
        return Conversion(tipo_real="imagen", error=f"{type(e).__name__}: {e}")
    finally:
        del_correo.gastar(time.monotonic() - inicio)
    if not texto.strip():
        return Conversion(
            tipo_real="imagen", ocr_aplicado=True,
            markdown=_encabezado(nombre, "_(imagen sin texto reconocible)_"),
        )
    md, truncado = _truncar(
        f"## Imagen {_marca_ocr(confianza)}\n\n"
        + _linea_dudosa(confianza, "imagen") + texto)
    return Conversion(
        markdown=_encabezado(nombre, md),
        tipo_real="imagen", ocr_aplicado=True, truncado=truncado,
    )


# Un TIFF de fax o de escaner trae una hoja por cuadro; Pillow abre solo la
# primera si no se recorren. El tope es el mismo de las hojas de un PDF.
def _tiff_de_varias_hojas(imagen, nombre: str,
                          del_correo: PresupuestoDeOcr) -> Conversion:
    total = int(getattr(imagen, "n_frames", 1))
    hojas = min(total, LIMITE_PAGINAS_PDF)
    partes: List[str] = []
    avisos: List[str] = []
    ilegibles = 0
    for i in range(hojas):
        if del_correo.queda() <= 0:
            ilegibles += hojas - i
            avisos.append(del_correo.motivo())
            break
        imagen.seek(i)
        hoja = imagen.convert("RGB") if imagen.mode not in ("RGB", "L") else imagen.copy()
        texto, confianza, fallo = _texto_de_imagen(
            hoja, min(TIMEOUT_OCR_PAGINA_S, del_correo.queda()), presupuesto=del_correo)
        if fallo or not texto.strip():
            ilegibles += 1
            if fallo:
                avisos.append(f"hoja {i + 1}: {fallo}")
            continue
        partes.append(f"## Página {i + 1} {_marca_ocr(confianza)}\n\n"
                      + _linea_dudosa(confianza, "página") + texto)
    if total > hojas:
        avisos.append(f"el TIFF trae {total} hojas; se leyeron las primeras {hojas}")
    md, truncado = _truncar("\n\n".join(partes))
    return Conversion(markdown=_encabezado(nombre, md) if partes else "",
                      tipo_real="imagen", ocr_aplicado=True, truncado=truncado,
                      aviso="; ".join(avisos), paginas=hojas,
                      paginas_ilegibles=ilegibles)


# HEIC/HEIF (las fotos de iPhone). Sus decodificadores libres (libheif,
# libde265, x265) son LGPL o GPL, y la licencia del proyecto solo admite MIT,
# BSD o Apache: no hay con que pasarlas a pixeles para tesseract. Gemini las
# recibe tal cual (image/heic, image/heif), asi que se transcriben con vision,
# dentro del mismo cupo de imagenes por correo que las hojas flojas.
LIMITE_BYTES_VISION_DIRECTA = 15 * 1024 * 1024


def _mime_heif(datos: bytes) -> str:
    return "image/heic" if datos[8:12] in (b"heic", b"heix") else "image/heif"


def _acepta_mime(funcion: Callable) -> bool:
    import inspect

    try:
        parametros = inspect.signature(funcion).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "mime" or p.kind is p.VAR_KEYWORD for p in parametros)


def transcribir_con_vision(datos: bytes, mime: str,
                           presupuesto: Optional[PresupuestoDeOcr]
                           ) -> Tuple[str, str]:
    """
    (texto, motivo_del_fallo) de una imagen leida solo por el modelo, sin
    tesseract: para lo que no se puede decodificar (HEIC) y para cuando un flujo
    pide que un tipo de archivo se lea con vision.
    """
    transcribir = TRANSCRIPTOR_IA
    if transcribir is None:
        return "", "no hay modelo con vision en esta corrida"
    if mime != "image/jpeg" and not _acepta_mime(transcribir):
        return "", "el transcriptor de esta corrida solo recibe JPEG"
    if len(datos) > LIMITE_BYTES_VISION_DIRECTA:
        return "", (f"{len(datos) / 1048576:.1f} MB excede lo que se le pasa al "
                    f"modelo ({LIMITE_BYTES_VISION_DIRECTA / 1048576:.0f} MB)")
    del_correo = presupuesto if presupuesto is not None else PresupuestoDeOcr()
    with _CERROJO_DEL_CUPO_IA:
        if del_correo.imagenes_ia <= 0:
            return "", (f"se agoto el cupo de vision del correo "
                        f"({MAX_IMAGENES_IA_POR_CORREO} imagenes)")
        del_correo.imagenes_ia -= 1
    try:
        texto = (transcribir(datos, mime=mime) if mime != "image/jpeg"
                 else transcribir(datos))
    except Exception as e:
        return "", f"la vision fallo ({type(e).__name__}: {e})"[:300]
    texto = normalizar(texto or "")
    if texto.strip().upper() == "SIN_TEXTO":
        return "", ""
    return texto, ""


def imagen_con_vision(datos: bytes, nombre: str,
                      presupuesto: Optional[PresupuestoDeOcr] = None,
                      tipo_real: str = "imagen") -> Conversion:
    """Una imagen leida solo por el modelo, con el mismo markdown que el OCR."""
    mime = ""
    if tipo_real == "heic":
        mime = _mime_heif(datos)
    else:
        try:
            from PIL import Image, ImageOps

            imagen = ImageOps.exif_transpose(Image.open(io.BytesIO(datos)))
            salida = io.BytesIO()
            imagen.convert("RGB").save(salida, format="JPEG", quality=90)
            datos, mime = salida.getvalue(), "image/jpeg"
        except Exception as e:
            return Conversion(tipo_real=tipo_real, error=f"{type(e).__name__}: {e}")
    inicio = time.monotonic()
    try:
        texto, fallo = transcribir_con_vision(datos, mime, presupuesto)
    finally:
        if presupuesto is not None:
            presupuesto.gastar(time.monotonic() - inicio)
    if fallo:
        return Conversion(tipo_real=tipo_real, paginas=1, paginas_ilegibles=1,
                          aviso=f"{nombre}: {fallo}")
    if not texto.strip():
        return Conversion(tipo_real=tipo_real, ocr_aplicado=True,
                          markdown=_encabezado(nombre, "_(imagen sin texto reconocible)_"))
    md, truncado = _truncar(f"## Imagen {_marca_ocr(None)}\n\n"
                            + MARCA_TRANSCRITO_POR_IA + "\n\n" + texto)
    return Conversion(markdown=_encabezado(nombre, md), tipo_real=tipo_real,
                      ocr_aplicado=True, truncado=truncado)


def _heic_a_markdown(datos: bytes, nombre: str,
                     presupuesto: Optional[PresupuestoDeOcr] = None) -> Conversion:
    if TRANSCRIPTOR_IA is None:
        return Conversion(
            tipo_real="heic",
            error="tipo no soportado sin vision: heic (el OCR no decodifica "
                  "HEIC/HEIF y esta corrida no tiene modelo con vision)")
    return imagen_con_vision(datos, nombre, presupuesto, tipo_real="heic")


def _docx_a_markdown(datos: bytes, nombre: str) -> Conversion:
    try:
        import docx
    except ImportError:
        return Conversion(tipo_real="docx", error="python-docx no esta instalado")
    try:
        documento = docx.Document(io.BytesIO(datos))
    except Exception as e:
        return Conversion(tipo_real="docx", error=f"{type(e).__name__}: {e}")

    partes: List[str] = []
    for parrafo in documento.paragraphs:
        texto = normalizar(parrafo.text)
        if not texto:
            continue
        estilo = (parrafo.style.name or "").lower() if parrafo.style else ""
        if "heading 1" in estilo:
            partes.append(f"## {texto}")
        elif "heading" in estilo or "titulo" in estilo:
            partes.append(f"### {texto}")
        elif "list" in estilo:
            partes.append(f"- {texto}")
        else:
            partes.append(texto)
    for tabla in documento.tables:
        filas = [[celda.text for celda in fila.cells] for fila in tabla.rows]
        md = _filas_a_tabla_md(filas)
        if md:
            partes.append(md)
    md, truncado = _truncar("\n\n".join(partes))
    return Conversion(
        markdown=_encabezado(nombre, md), tipo_real="docx", truncado=truncado
    )


def _xlsx_a_markdown(datos: bytes, nombre: str) -> Conversion:
    try:
        import openpyxl
    except ImportError:
        return Conversion(tipo_real="xlsx", error="openpyxl no esta instalado")
    try:
        libro = openpyxl.load_workbook(
            io.BytesIO(datos), read_only=True, data_only=True
        )
    except Exception as e:
        return Conversion(tipo_real="xlsx", error=f"{type(e).__name__}: {e}")

    partes: List[str] = []
    aviso = ""
    try:
        for hoja in libro.worksheets:
            filas: List[List[str]] = []
            for i, fila in enumerate(hoja.iter_rows(values_only=True)):
                if i >= LIMITE_FILAS_HOJA:
                    aviso = (f"la hoja '{hoja.title}' excede "
                             f"{LIMITE_FILAS_HOJA} filas, se truncó")
                    break
                filas.append(["" if c is None else str(c) for c in fila])
            filas = [f for f in filas if any(c.strip() for c in f)]
            if not filas:
                continue
            md_tabla = _filas_a_tabla_md(filas)
            partes.append(f"## Hoja: {hoja.title}\n\n"
                          + (md_tabla or "\n".join(" | ".join(f) for f in filas)))
    finally:
        libro.close()
    md, truncado = _truncar("\n\n".join(partes))
    return Conversion(
        markdown=_encabezado(nombre, md), tipo_real="xlsx",
        truncado=truncado, aviso=aviso,
    )


def _texto_a_markdown(datos: bytes, nombre: str) -> Conversion:
    for cp in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            texto = datos.decode(cp)
            break
        except UnicodeDecodeError:
            continue
    else:
        return Conversion(tipo_real="texto", error="no se pudo decodificar")
    md, truncado = _truncar(normalizar(texto))
    return Conversion(
        markdown=_encabezado(nombre, md), tipo_real="texto", truncado=truncado
    )


# Firmas de los formatos que pueden venir dentro de un sobre firmado. Se busca
# la carga util por su firma, no por su posicion: el sobre trae antes los
# certificados y el tamaño de esa cabecera cambia con cada emisor.
_CARGAS_EN_SOBRE: Tuple[Tuple[bytes, bytes], ...] = (
    (b"%PDF", b"%%EOF"),
    (b"PK\x03\x04", b""),          # docx, xlsx, zip
    (b"\x89PNG\r\n\x1a\n", b""),
    (b"\xff\xd8\xff", b"\xff\xd9"),
)

MAX_PROFUNDIDAD_SOBRE = 3


def _carga_util_del_sobre(datos: bytes) -> Optional[Tuple[bytes, str]]:
    """Busca dentro del sobre un archivo reconocible y lo devuelve recortado."""
    mejor: Optional[Tuple[int, bytes]] = None
    for firma, cierre in _CARGAS_EN_SOBRE:
        i = datos.find(firma)
        if i < 0 or (mejor is not None and i >= mejor[0]):
            continue
        if cierre:
            fin = datos.rfind(cierre)
            recorte = datos[i: fin + len(cierre)] if fin > i else datos[i:]
        else:
            recorte = datos[i:]
        mejor = (i, recorte)
    if mejor is None:
        return None
    return mejor[1], f"extraido de un sobre firmado (offset {mejor[0]})"


def _p7m_a_markdown(datos: bytes, nombre: str,
                    profundidad: int = 0) -> Conversion:
    """
    Abre un .p7m (PKCS#7) y convierte el documento que lleva dentro.

    Sin esto el adjunto se marca ilegible y el correo se va a revision manual
    aunque el documento este ahi. Se busca la carga util por su firma en vez de
    parsear ASN.1: no hace falta validar la firma para leer el contenido, y
    evita una dependencia mas.

    El sobre puede venir en base64, y a veces envuelto dos veces; de ahi el
    limite de profundidad, para que un archivo corrupto no cicle.
    """
    if profundidad >= MAX_PROFUNDIDAD_SOBRE:
        return Conversion(
            tipo_real="p7m",
            error="el sobre firmado anida mas de "
                  f"{MAX_PROFUNDIDAD_SOBRE} niveles",
        )

    if datos.lstrip()[:200].lower().startswith((b"content-type", b"mime-version")):
        return _smime_a_markdown(datos, nombre, profundidad)
    encontrado = _carga_util_del_sobre(datos)
    if encontrado is None:
        # Algunos llegan como texto base64 en vez de DER.
        try:
            import base64

            crudo = base64.b64decode(
                re.sub(rb"\s", b"", datos), validate=False)
        except Exception:
            crudo = b""
        if crudo and crudo != datos:
            return _p7m_a_markdown(crudo, nombre, profundidad + 1)
        return Conversion(
            tipo_real="p7m",
            error="el sobre firmado no trae ningun documento reconocible",
        )

    carga, procedencia = encontrado
    interna = convertir(carga, nombre, _profundidad_sobre=profundidad + 1)
    if interna.error:
        return Conversion(
            tipo_real="p7m",
            error=f"{procedencia}: {interna.error}",
        )
    interna.tipo_real = f"p7m/{interna.tipo_real}"
    interna.aviso = " | ".join(filter(None, (interna.aviso, procedencia)))
    return interna


_FIRMAS_SMIME = {"application/pkcs7-signature", "application/x-pkcs7-signature"}


def _smime_a_markdown(datos: bytes, nombre: str, profundidad: int) -> Conversion:
    """
    Un smime.p7m que es un correo firmado (multipart/signed en texto MIME): el
    mensaje va en claro junto a la firma. Antes se buscaba un PDF dentro y, al no
    haberlo, el adjunto quedaba ilegible aunque el texto estuviera ahi.
    """
    import email
    from email import policy

    try:
        mensaje = email.message_from_bytes(datos, policy=policy.default)
    except Exception as e:
        return Conversion(tipo_real="p7m", error=f"el correo firmado no se pudo leer ({type(e).__name__}: {e})")
    plano: List[str] = []
    html: List[str] = []
    internos: List[Tuple[str, bytes]] = []
    for parte in mensaje.walk():
        if parte.is_multipart():
            continue
        tipo = parte.get_content_type()
        if tipo in _FIRMAS_SMIME:
            continue
        carga = parte.get_payload(decode=True) or b""
        archivo = parte.get_filename()
        if archivo or not tipo.startswith("text/"):
            internos.append((archivo or f"parte.{tipo.split('/')[-1]}", carga))
            continue
        texto = carga.decode(parte.get_content_charset() or "utf-8", "replace")
        (plano if tipo == "text/plain" else html).append(texto)
    cuerpo = "\n\n".join(plano) or re.sub(r"<[^>]+>", " ", "\n\n".join(html))
    partes = [f"## Mensaje firmado\n\n{normalizar(cuerpo)}"] if cuerpo.strip() else []
    avisos = []
    for archivo, carga in internos:
        interna = convertir(carga, archivo, _profundidad_sobre=profundidad + 1)
        if interna.error:
            avisos.append(f"{archivo}: {interna.error}")
        elif interna.markdown:
            partes.append(interna.markdown.replace("# ", "## ", 1))
    if not partes:
        return Conversion(tipo_real="p7m", error="el correo firmado no trae texto ni documentos")
    md, truncado = _truncar("\n\n".join(partes))
    return Conversion(markdown=_encabezado(nombre, md), tipo_real="p7m/correo",
                      truncado=truncado, aviso="; ".join(avisos))


def _encabezado(nombre: str, cuerpo: str) -> str:
    """
    Antepone el nombre del archivo como titulo.

    Sirve para que la IA sepa de que documento viene cada dato: sin esa
    atribucion no hay evidencia posible, y la evidencia es requisito.
    """
    limpio = (cuerpo or "").strip() or "_(sin contenido extraible)_"
    return f"# {nombre}\n\n{limpio}\n"


# Despachador por tipo real
_CONVERSORES = {
    "pdf": _pdf_a_markdown,
    "imagen": _imagen_a_markdown,
    "heic": _heic_a_markdown,
    "docx": _docx_a_markdown,
    "xlsx": _xlsx_a_markdown,
    "texto": _texto_a_markdown,
}


def convertir(
    datos: bytes,
    nombre: str,
    claves_candidatas: Optional[List[str]] = None,
    _profundidad_sobre: int = 0,
    presupuesto: Optional[PresupuestoDeOcr] = None,
) -> Conversion:
    """
    Convierte un adjunto a Markdown segun su tipo real.

    Los tipos no soportados no revientan: devuelven una conversion con el
    error anotado para que el manifiesto lo registre y el pipeline decida.

    `claves_candidatas` solo aplica a PDF protegidos (ver `_pdf_a_markdown`).
    `_profundidad_sobre` es interno: lo usa `_p7m_a_markdown` al abrir el
    documento que venia dentro de un sobre firmado.
    `presupuesto` es el tiempo de OCR que le queda al CORREO: se comparte entre
    sus adjuntos para que un correo con muchos escaneos no multiplique el tope
    de cada documento.
    """
    tipo = detectar_tipo(datos, nombre)
    if tipo == "vacio":
        return Conversion(tipo_real="vacio", error="el archivo esta vacio")
    if tipo == "p7m":
        return _p7m_a_markdown(datos, nombre, _profundidad_sobre)
    conversor = _CONVERSORES.get(tipo)
    if conversor is None:
        return Conversion(
            tipo_real=tipo,
            error=f"tipo no soportado para conversion a Markdown: {tipo}",
        )
    if tipo == "pdf":
        return _pdf_a_markdown(datos, nombre, claves_candidatas, presupuesto)
    if tipo == "imagen":
        return _imagen_a_markdown(datos, nombre, presupuesto)
    if tipo == "heic":
        return _heic_a_markdown(datos, nombre, presupuesto)
    return conversor(datos, nombre)


def expandir_zip(
    datos: bytes,
    limite: int = 30,
    descartados: Optional[List[str]] = None,
) -> List[Tuple[str, bytes]]:
    """
    Expande un ZIP a (nombre, bytes), sin recursion.

    Se rechazan las rutas que escapan del archivo (zip slip) y se limita la
    cantidad de entradas y el tamaño descomprimido, porque un ZIP malicioso
    puede agotar el disco (zip bomb) y este contenido viene de fuera.

    `descartados`, si se pasa, recibe una linea por cada entrada que no se
    extrajo y por que. Un ZIP suele traer los soportes del caso: si se pierden
    callados, el pipeline concluye que el correo no traia documento.
    """
    salida: List[Tuple[str, bytes]] = []
    limite_total = 200 * 1024 * 1024  # 200 MB descomprimidos
    acumulado = 0

    def anotar(mensaje: str) -> None:
        if descartados is not None:
            descartados.append(mensaje)

    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            entradas = z.infolist()
            if len(entradas) > limite:
                anotar(f"el comprimido trae {len(entradas)} entradas; solo se "
                       f"expandieron las primeras {limite}")
            for info in entradas[:limite]:
                if info.is_dir():
                    continue
                nombre = info.filename
                if nombre.startswith("/") or ".." in nombre.replace("\\", "/").split("/"):
                    anotar(f"'{nombre}' se descarto: la ruta escapa del comprimido")
                    continue
                if acumulado + info.file_size > limite_total:
                    anotar(f"'{nombre}' y las siguientes se descartaron: se "
                           f"supero el limite de descompresion")
                    break
                try:
                    contenido = z.read(info)
                except Exception as e:
                    anotar(f"'{nombre}' no se pudo extraer: {type(e).__name__}: {e}")
                    continue
                acumulado += len(contenido)
                salida.append((nombre.replace("\\", "/").split("/")[-1], contenido))
    except Exception as e:
        anotar(f"el comprimido esta dañado: {type(e).__name__}: {e}")
        return salida
    return salida
