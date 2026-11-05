"""
Las siglas de un formulario no dicen el tipo de documento del afectado.

Los escaneos traen impreso el menu de casillas: "Tipo Doc CNV RC TI CC PEP CE
PA MSI ASI PPT SC". Tomar una de esas siglas como marca escribe en SGDEA un
tipo equivocado.

La regla no es una lista de frases de formulario: se cuenta cuantas siglas
DISTINTAS hay juntas. Una leyenda enumera, una declaracion menciona una sola.
"""
import pytest

from utils.tipos_documento import (
    SIGLAS_PARA_SER_LEYENDA,
    esta_en_leyenda,
    sin_leyendas,
    tramos_de_leyenda,
)

LEYENDAS = [
    "Tipo Doc CNV RC TI CC PEP CE PA MSI ASI PPT SC Identificacion 41234567",
    "Nit (NI) - Cedula Ciudadania (CC) - Cedula Extranjeria (CE) - Pasaporte (PA)",
    "Yo identificado con No. CC TI CE No. CC TI CE No. CC TI CE Proceso",
    "TIPOS DE DOCUMENTO PERMITIDOS: Cedula Ciudadania (CC) - Cedula de "
    "extranjeria (CE) - Pasaporte (PA) - PT: permiso por proteccion temporal",
]

DECLARACIONES = [
    "El trabajador PEDRO RUIZ identificado con C.C. 41234567",
    "ALEJANDRA SANDOVAL titular de TI No. 2149497637 expedida en Pereira",
    "La empresa con NIT 900123456-7 solicita",
    "Adjunto pasaporte AV1234567 del afiliado",
    "Cedula de extranjeria numero 123456",
]


# ── Reconocer la leyenda ──

@pytest.mark.parametrize("texto", LEYENDAS)
def test_un_menu_de_casillas_es_leyenda(texto):
    assert tramos_de_leyenda(texto), texto


@pytest.mark.parametrize("texto", DECLARACIONES)
def test_una_declaracion_no_es_leyenda(texto):
    """
    Si marcara estas como leyenda, se perderia el tipo de documento en los
    correos que SI lo declaran, que es la mayoria.
    """
    assert not tramos_de_leyenda(texto), texto


def test_dos_siglas_juntas_no_bastan():
    """
    "CC o TI" es una pregunta legitima de un formulario corto. Exigir tres
    evita confundirla con el menu completo.
    """
    assert SIGLAS_PARA_SER_LEYENDA >= 3
    assert not tramos_de_leyenda("Marque CC o TI segun corresponda")


def test_siglas_lejanas_no_forman_leyenda():
    """
    Una declaracion de CC al principio y otra de TI al final del correo no son
    un menu: la ventana es lo que las separa.
    """
    texto = (
        "El trabajador con C.C. 41234567 solicita. "
        + "Relleno de la carta. " * 20
        + "Su hijo con T.I. 1012345678 tambien. "
        + "Mas relleno todavia. " * 20
        + "La empresa con NIT 900123456."
    )
    assert not tramos_de_leyenda(texto)


# ── Borrar la leyenda sin mover el resto ──

def test_el_texto_conserva_su_longitud():
    """Otros extractores miden distancias sobre este texto: no puede encogerse."""
    texto = LEYENDAS[0]
    assert len(sin_leyendas(texto)) == len(texto)


def test_lo_que_no_es_leyenda_se_conserva():
    texto = "Tipo Doc RC TI CC PEP CE PA SC. El trabajador PEDRO RUIZ MEJIA."
    limpio = sin_leyendas(texto)
    assert "PEDRO RUIZ MEJIA" in limpio
    assert "PEP" not in limpio


def test_una_declaracion_sobrevive_intacta():
    texto = DECLARACIONES[0]
    assert sin_leyendas(texto) == texto


def test_los_saltos_de_linea_se_respetan():
    """Borrarlos juntaria dos lineas y crearia vecindades que no existen."""
    texto = "Tipo Doc RC TI CC PEP CE PA SC\nPEDRO RUIZ"
    assert "\n" in sin_leyendas(texto)


def test_esta_en_leyenda_ubica_la_posicion():
    """
    Es la disposicion real de un formulario escaneado: el menu de casillas en
    su renglon y los datos del afectado en el siguiente.
    """
    texto = ("Tipo Doc CNV RC TI CC PEP CE PA SC\n"
             "Nombre PEDRO RUIZ MEJIA identificado con C.C. 41234567")
    assert esta_en_leyenda(texto, texto.index("PEP"))
    assert not esta_en_leyenda(texto, texto.index("PEDRO"))


def test_la_cadena_se_corta_en_el_salto_de_linea():
    """
    Sin cortar, el menu de un renglon se une con la declaracion del siguiente y
    se traga el dato bueno.
    """
    texto = "RC TI CC PEP CE PA\nEl trabajador con C.C. 41234567"
    limpio = sin_leyendas(texto)
    assert "C.C. 41234567" in limpio


def test_texto_vacio_no_revienta_la_deteccion_de_leyendas():
    assert tramos_de_leyenda("") == []
    assert sin_leyendas("") == ""
    assert not esta_en_leyenda("", 0)
