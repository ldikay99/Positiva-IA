"""
La direccion del bloque del paciente tambien se lee con los rotulos cortos de
las historias clinicas ("Dir:") y los de los formularios ("Direccion para
notificaciones:"). La de Positiva, que firma como destinataria, no es del
afectado.
"""
from pipeline.extraccion import extraer_direccion

HISTORIA = ("Nombres: PEDRO PABLO RUIZ Identificacion: CC 41234567 Estado Civil: "
            "Soltero Genero: Hombre Dir: VEREDA LA ESPERANZA FINCA EL PORVENIR "
            "Tel: 3105551234")


def test_dir_en_el_bloque_del_paciente():
    h, nr = extraer_direccion(HISTORIA, "PEDRO PABLO RUIZ", "41234567")
    assert nr is None and h.valor == "VEREDA LA ESPERANZA FINCA EL PORVENIR"


def test_direccion_para_notificaciones():
    texto = ("Yo, PEDRO PABLO RUIZ, identificado con CC 41234567. Direccion para "
             "notificaciones: Calle 10 # 5-20 Barrio Centro")
    h, _ = extraer_direccion(texto, "PEDRO PABLO RUIZ", "41234567")
    assert h is not None and h.valor.startswith("Calle 10 # 5-20")


def test_la_direccion_de_positiva_no_es_del_afectado():
    texto = ("PEDRO PABLO RUIZ CC 41234567 Ciudad BOGOTA (BOGOTA) CC: ARL POSITIVA SA "
             "Dir: Avenida Carrera 45 No. 94-72 Tel 3307000")
    h, nr = extraer_direccion(texto, "PEDRO PABLO RUIZ", "41234567")
    assert h is None and nr.motivo == "DIRECCION_DE_OTRO_SUJETO"


def test_domicilio_no_se_toma_por_direccion():
    """En un escrito, el domicilio suele ser la ciudad."""
    texto = "PEDRO PABLO RUIZ, cedula 41234567. Domicilio: Ciudad del Norte."
    h, _ = extraer_direccion(texto, "PEDRO PABLO RUIZ", "41234567")
    assert h is None
