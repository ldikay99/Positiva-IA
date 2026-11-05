"""
Conversion de adjuntos a Markdown.

Markdown conserva encabezados, tablas y listas, que el modelo aprovecha.

  - El tipo se decide por los bytes, no por la extension.
  - Los fallos de conversion se registran en el manifiesto, no se silencian.
  - OCR solo si el PDF no trae texto.
"""
from __future__ import annotations

import io
import os
import re
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

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
PRESUPUESTO_OCR_SEGUNDOS = 90.0

# Presupuesto de OCR del CORREO ENTERO, repartido entre sus adjuntos. El de
# arriba acota un documento; sin este, un correo con dieciseis adjuntos
# escaneados los multiplica y se lleva la ventana de media hora entre corridas.
# Tres minutos son tres veces el peor correo medido sobre los adjuntos reales
# del buzon, asi que no recorta trabajo normal y si acota el caso patologico.
PRESUPUESTO_OCR_POR_CORREO_S = 180.0

# Tope por pagina. El presupuesto del documento solo se comprueba ANTES de cada
# pagina, asi que una sola pagina lenta lo desborda entero: se han visto
# conversiones de veinte minutos con el presupuesto en noventa segundos.
TIMEOUT_OCR_PAGINA_S = 25.0

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
                        ocr, fallo_ocr = "", (
                            del_correo.motivo() if manda_el_correo
                            else f"se agoto el presupuesto de OCR "
                                 f"({PRESUPUESTO_OCR_SEGUNDOS:.0f}s) para "
                                 f"este documento"
                        )
                    else:
                        ocr, fallo_ocr = _ocr_de_pagina(pagina)
                    if ocr:
                        partes.append(f"## Pagina {i + 1} (OCR)\n\n{ocr}")
                        continue
                    paginas_ilegibles += 1
                    if fallo_ocr and fallo_ocr not in motivos_ocr:
                        motivos_ocr.append(fallo_ocr)
                    partes.append(
                        f"## Pagina {i + 1}\n\n_(pagina sin texto extraible"
                        + (f": {fallo_ocr}" if fallo_ocr else "") + ")_"
                    )
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
                    doc, pagina, vistas, queda)
                if leido:
                    bloque += "\n\n" + leido
                    imagenes_leidas += len(leido.split("### Imagen")) - 1
                if sin_leer:
                    imagenes_sin_leer += sin_leer
                    if motivo and motivo not in motivos_ocr:
                        motivos_ocr.append(motivo)
                partes.append(bloque)
    except Exception as e:
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
        ocr_aplicado=(paginas_sin_texto > 0 or imagenes_leidas > 0)
                     and "(OCR)" in md,
        truncado=truncado,
        aviso="; ".join(avisos),
        paginas_ilegibles=paginas_ilegibles,
        paginas=total_paginas,
    )


def _texto_de_imagen(imagen, segundos: float) -> Tuple[str, str]:
    """
    OCR de una imagen ya cargada, con tope de tiempo. Devuelve (texto, motivo).

    El tope lo aplica tesseract: sin el, una sola pagina puede tardar minutos y
    desbordar el presupuesto del documento, que solo se comprueba entre paginas.
    """
    import pytesseract

    try:
        return normalizar(pytesseract.image_to_string(
            imagen, lang="spa+eng", timeout=max(1.0, segundos))), ""
    except RuntimeError:
        # Es lo que lanza pytesseract cuando se agota su propio timeout.
        return "", f"el OCR de una pagina paso de {segundos:.0f}s y se corto"
    except Exception as e:
        return "", f"el OCR fallo ({type(e).__name__}: {e})"


def _ocr_de_pagina(pagina) -> Tuple[str, str]:
    """
    OCR de una pagina. Devuelve (texto, motivo_del_fallo).

    No revienta si falta tesseract: el binario no siempre esta instalado. Pero
    el motivo se devuelve, porque "el OCR no corrio" y "el documento no dice
    nada" llevan a conclusiones opuestas: la primera es un problema de entorno
    que se arregla, la segunda manda el correo a revision manual.
    """
    falta = _motor_de_ocr_que_falta()
    if falta:
        return "", f"no hay motor de OCR instalado ({falta})"
    from PIL import Image

    try:
        pix = pagina.get_pixmap(dpi=200)
        imagen = Image.open(io.BytesIO(pix.tobytes("png")))
    except Exception as e:
        return "", f"el OCR fallo ({type(e).__name__}: {e})"
    return _texto_de_imagen(imagen, TIMEOUT_OCR_PAGINA_S)


def _motor_de_ocr_que_falta() -> str:
    """El modulo de OCR que no esta instalado, o cadena vacia si estan los dos."""
    import importlib.util

    for modulo in ("pytesseract", "PIL"):
        if importlib.util.find_spec(modulo) is None:
            return modulo
    return ""


def _ocr_de_imagenes(
    doc, pagina, vistas: set, segundos_restantes: float
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
        texto, fallo = _texto_de_imagen(
            imagen, min(TIMEOUT_OCR_PAGINA_S, segundos_restantes))
        segundos_restantes -= time.monotonic() - inicio
        if texto.strip():
            bloques.append(f"### Imagen incrustada (OCR)\n\n{texto}")
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
        # El OCR no admite todos los modos de color: CMYK de un escaner, paleta
        # con transparencia o 16 bits por canal lo hacen fallar. RGB y escala de
        # grises los acepta siempre, y convertir no pierde nada legible.
        if imagen.mode not in ("RGB", "L"):
            imagen = imagen.convert("RGB")
        texto, fallo = _texto_de_imagen(
            imagen, min(TIMEOUT_OCR_PAGINA_S, del_correo.queda()))
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
    md, truncado = _truncar(texto)
    return Conversion(
        markdown=_encabezado(nombre, md),
        tipo_real="imagen", ocr_aplicado=True, truncado=truncado,
    )


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
