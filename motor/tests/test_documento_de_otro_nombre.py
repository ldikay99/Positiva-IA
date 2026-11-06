"""El documento del afectado escrito junto al nombre de otra persona."""
from pipeline import compuerta

T = compuerta._texto_para_roles


def test_el_asunto_da_la_cedula_de_otra_persona():
    texto = T("Asunto: Ricardo cifuentes Ospina CC 85613077\nme duele la pierna")
    assert compuerta.titular_rotulado_de_otro(
        "85613077", "Rosalba Lozada", texto) == "ricardo cifuentes ospina"


def test_la_cedula_junto_al_mismo_nombre_no_es_de_otro():
    texto = T("Asunto: Ricardo cifuentes Ospina CC 85613077")
    assert compuerta.titular_rotulado_de_otro("85613077", "Ricardo Cifuentes", texto) == ""


def test_sin_rotulo_de_cedula_no_se_afirma_nada():
    texto = T("Radicado Juan Perez Gomez 85613077")
    assert compuerta.titular_rotulado_de_otro("85613077", "Pedro Ruiz", texto) == ""


def test_un_rol_delante_no_es_un_nombre():
    texto = T("solicito la autorizacion de cc 85613077")
    assert compuerta.titular_rotulado_de_otro("85613077", "Pedro Ruiz", texto) == ""


def test_el_numero_pegado_al_nombre_sin_rotulo():
    texto = T("Asunto: 1073.441067 johan mauricio guerra mahecha")
    assert compuerta.numero_pegado_al_nombre(
        "1073441067", "johan mauricio guerra mahecha", texto)
    assert not compuerta.numero_pegado_al_nombre("1073441067", "Ana Lopez Diaz", texto)


def test_el_nit_no_gana_solo_frente_a_una_identificacion_de_persona():
    from pipeline.extraccion import extraer_documento
    texto = ("Empresa: Alcaldia Municipal de Yopal -NIT: 891.855.017-7 - Documento "
             "de identidad de la colaboradora: Lyda Diaz Espinosa, "
             "Identificacion: 65.735.022")
    hallazgo, no_resuelto = extraer_documento(texto)
    assert hallazgo is None
    assert no_resuelto.motivo == "VARIOS_CANDIDATOS_AMBIGUOS"



def test_el_nombre_completo_no_se_corta_al_compararlo():
    # Cortado a cinco palabras, "DEICY ELVIRA TORRES DE LA HOZ" pierde el primer
    # nombre y parece otra persona que "Deicy Torres De La Oz".
    texto = T("Yo, DEICY ELVIRA TORRES DE LA HOZ, identificada con cedula de "
              "ciudadania No. 36.556.493 expedida en Santa Marta")
    assert compuerta.titular_rotulado_de_otro(
        "36556493", "Deicy Torres De La Oz", texto) == ""
    assert compuerta.titular_rotulado_de_otro(
        "36556493", "Horacio Antonio Rivera Donado", texto) == "deicy elvira torres de la hoz"
