"""
Los formatos de imagen que llegan por correo y antes no se leian: HEIC/HEIF
(fotos de iPhone, por vision porque no hay decodificador con licencia
permitida), WEBP y AVIF (Pillow), TIFF de varias hojas y el smime.p7m que es un
correo firmado en claro.
"""
import io

import pytest
from PIL import Image

from pipeline import cache_conversion, conversion

# Cabecera ISO-BMFF minima: tamaño, «ftyp», marca mayor, version y compatibles.
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
HEIF = b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1heic" + b"\x00" * 64
AVIF = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1miaf" + b"\x00" * 64


def _imagen(formato: str, **extra) -> bytes:
    salida = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(salida, format=formato, **extra)
    return salida.getvalue()


class Vision:
    """Un transcriptor que recibe el formato, como el de Vertex."""

    clave_cache = "prueba:vision"

    def __init__(self, texto="CEDULA DE CIUDADANIA 12.345.678 PEREZ GOMEZ ANA"):
        self.texto = texto
        self.llamadas = []

    def __call__(self, datos, mime="image/jpeg"):
        self.llamadas.append((mime, len(datos)))
        return self.texto


@pytest.fixture
def vision(monkeypatch):
    v = Vision()
    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA", v)
    return v


def test_se_reconocen_por_sus_bytes_y_no_por_la_extension():
    assert conversion.detectar_tipo(HEIC, "foto.jpg") == "heic"
    assert conversion.detectar_tipo(HEIF, "sin_nombre") == "heic"
    assert conversion.detectar_tipo(AVIF, "x.heic") == "imagen"
    assert conversion.detectar_tipo(_imagen("WEBP"), "x.bin") == "imagen"


def test_el_heic_va_tal_cual_al_modelo_con_su_formato(vision):
    p = conversion.PresupuestoDeOcr()
    c = conversion.convertir(HEIC, "1000079553.heic", presupuesto=p)
    assert not c.error and c.tipo_real == "heic"
    assert vision.llamadas == [("image/heic", len(HEIC))]
    assert conversion.MARCA_TRANSCRITO_POR_IA in c.markdown
    assert "12.345.678" in c.markdown and c.markdown.startswith("# 1000079553.heic")
    assert "(OCR)" in c.markdown and c.ocr_aplicado
    assert p.imagenes_ia == conversion.MAX_IMAGENES_IA_POR_CORREO - 1


def test_heif_sin_marca_heic_va_como_image_heif(vision):
    conversion.convertir(HEIF, "foto.heif")
    assert vision.llamadas[0][0] == "image/heif"


def test_sin_vision_el_heic_queda_como_no_soportado(monkeypatch):
    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA", None)
    c = conversion.convertir(HEIC, "foto.heic")
    assert "no soportado" in c.error and "heic" in c.error.lower()


def test_un_transcriptor_que_solo_recibe_jpeg_no_recibe_un_heic(monkeypatch):
    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA", lambda jpeg: pytest.fail("no debia llamarse"))
    c = conversion.convertir(HEIC, "foto.heic")
    assert c.ilegible and "JPEG" in c.aviso


def test_el_heic_gasta_el_cupo_de_vision_del_correo(vision):
    p = conversion.PresupuestoDeOcr()
    p.imagenes_ia = 1
    assert not conversion.convertir(HEIC, "a.heic", presupuesto=p).ilegible
    c = conversion.convertir(HEIC + b"1", "b.heic", presupuesto=p)
    assert c.ilegible and "cupo de vision" in c.aviso
    assert len(vision.llamadas) == 1


def test_sin_texto_es_imagen_sin_texto_y_no_ilegible(monkeypatch):
    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA", Vision("SIN_TEXTO"))
    c = conversion.convertir(HEIC, "paisaje.heic")
    assert not c.ilegible and "sin texto reconocible" in c.markdown


def test_un_fallo_de_la_vision_deja_el_heic_ilegible_con_el_motivo(monkeypatch):
    def falla(datos, mime="image/jpeg"):
        raise TimeoutError("se corto")

    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA", falla)
    c = conversion.convertir(HEIC, "foto.heic")
    assert c.ilegible and "TimeoutError" in c.aviso


def test_el_heic_se_lee_una_vez_por_huella(vision, monkeypatch, tmp_path):
    monkeypatch.setenv("CACHE_CONVERSION", "si")
    monkeypatch.setenv("RUTA_CACHE_CONVERSION", str(tmp_path / "cache"))
    cache_conversion.vaciar_memoria()
    try:
        a = cache_conversion.convertir(HEIC, "a.heic", presupuesto=conversion.PresupuestoDeOcr())
        p = conversion.PresupuestoDeOcr()
        b = cache_conversion.convertir(HEIC, "a.heic", presupuesto=p)
        assert a.markdown == b.markdown and len(vision.llamadas) == 1
        # El acierto descuenta la imagen del cupo, como la primera lectura.
        assert p.imagenes_ia == conversion.MAX_IMAGENES_IA_POR_CORREO - 1
    finally:
        cache_conversion.vaciar_memoria()


def test_webp_y_avif_pasan_por_el_ocr_como_cualquier_imagen(monkeypatch):
    leidas = []
    monkeypatch.setattr(conversion, "_texto_de_imagen",
                        lambda img, s, preparar=True, presupuesto=None: leidas.append(img.size) or ("ORDEN MEDICA", 0.93, ""))
    for formato in ("WEBP", "AVIF"):
        c = conversion.convertir(_imagen(formato), f"x.{formato.lower()}")
        assert not c.error and "ORDEN MEDICA" in c.markdown, formato
    assert leidas == [(400, 300), (400, 300)]


def test_un_tiff_de_varias_hojas_se_lee_hoja_por_hoja(monkeypatch):
    hojas = [Image.new("RGB", (300, 400), "white") for _ in range(3)]
    salida = io.BytesIO()
    hojas[0].save(salida, format="TIFF", save_all=True, append_images=hojas[1:])
    textos = iter(["HOJA UNO", "", "HOJA TRES"])
    monkeypatch.setattr(conversion, "_texto_de_imagen",
                        lambda img, s, preparar=True, presupuesto=None: (next(textos), 0.9, ""))
    c = conversion.convertir(salida.getvalue(), "fax.tif")
    assert c.paginas == 3 and c.paginas_ilegibles == 1 and not c.ilegible
    assert "## Página 1" in c.markdown and "HOJA TRES" in c.markdown and "Página 2" not in c.markdown


def test_un_correo_firmado_smime_se_lee_en_claro():
    firmado = (
        b"Content-Type: multipart/signed; protocol=\"application/x-pkcs7-signature\"; boundary=\"F\"\r\n\r\n"
        b"--F\r\nContent-Type: text/plain; charset=\"utf-8\"\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n"
        b"Solicito la calificaci=C3=B3n de p=C3=A9rdida de capacidad laboral.\r\n"
        b"--F\r\nContent-Type: application/x-pkcs7-signature; name=\"smime.p7s\"\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\nMIAGCSqGSIb3DQEHAqCAMIACAQEx\r\n--F--\r\n")
    c = conversion.convertir(firmado, "smime.p7m")
    assert not c.error, c.error
    assert c.tipo_real == "p7m/correo"
    assert "calificación de pérdida de capacidad laboral" in c.markdown
