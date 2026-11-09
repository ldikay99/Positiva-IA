"""
Un documento que es el recorte de otro escrito junto al mismo nombre no se
radica: el OCR de la foto de la cedula perdio un digito y el correo lo trae
entero.
"""
from pipeline import compuerta
from pipeline.extraccion import Hallazgo

TEXTO = ("Yo Pedro Pablo Ruiz Gomez identificado con la Cc.52417396 de Cali, afiliado "
         "activo, presento derecho de peticion. Atentamente. Pedro Pablo Ruiz Gomez "
         "C.C. 52417396\n# cedula.pdf\n## Pagina 1 (OCR)\nNUMERO 2.417.396 RUIZ GOMEZ")


def test_encuentra_el_documento_entero():
    plegado = compuerta._texto_para_roles(TEXTO)
    assert compuerta.documento_del_que_es_un_recorte(
        "2417396", "Pedro Pablo Ruiz Gomez", plegado) == "52417396"


def test_el_documento_entero_no_es_recorte_de_nada():
    plegado = compuerta._texto_para_roles(TEXTO)
    assert compuerta.documento_del_que_es_un_recorte(
        "52417396", "Pedro Pablo Ruiz Gomez", plegado) == ""


def test_un_numero_parecido_lejos_del_nombre_no_cuenta():
    texto = ("Pedro Ruiz CC 2417396 solicita su certificado. " + "Texto del caso. " * 40
             + "Factura numero 52417396 del prestador.")
    plegado = compuerta._texto_para_roles(texto)
    assert compuerta.documento_del_que_es_un_recorte("2417396", "Pedro Ruiz", plegado) == ""


def test_la_compuerta_lo_devuelve():
    def h(campo, valor):
        return Hallazgo(campo=campo, valor=valor, evidencia="x", confianza=0.95, metodo="agente_ia")

    dictamen = compuerta.validar(
        {"numero_documento_afectado": h("numero_documento_afectado", "2417396"),
         "nombre_afectado": h("nombre_afectado", "Pedro Pablo Ruiz Gomez")},
        fuentes=TEXTO)
    assert any(r.codigo == "DOCUMENTO_RECORTADO" and r.bloqueante for r in dictamen.reparos)


# ── El documento que no aparece junto a la persona, cuando otro si ──

FURAT = ("# 1087654321-juan-carlos-perez-lopez.pdf\n## Pagina 1\n"
         "Nombre del trabajador JUAN CARLOS PEREZ LOPEZ identificado con CC 1087654321\n"
         "## Pagina 3\nResponsable | MARIA LOPEZ DIAZ | Firma | Tipo Doc | CC | "
         "No Documento | 51234987")


def test_el_documento_del_responsable_no_es_del_trabajador():
    plegado = compuerta._texto_para_roles(FURAT)
    assert compuerta.documento_rotulado_de_la_persona(
        "51234987", "JUAN CARLOS PEREZ LOPEZ", plegado) == "1087654321"
    assert compuerta.documento_rotulado_de_la_persona(
        "1087654321", "JUAN CARLOS PEREZ LOPEZ", plegado) == ""


def test_un_celular_o_una_fecha_junto_al_nombre_no_son_rivales():
    texto = ("Pedro Pablo Ruiz celular 3105551234, periodo 202301. "
             + "Relleno del caso. " * 30 + "Documento del caso 41234567.")
    plegado = compuerta._texto_para_roles(texto)
    assert compuerta.documento_rotulado_de_la_persona("41234567", "Pedro Pablo Ruiz", plegado) == ""


def test_solo_se_le_aplica_al_documento_del_agente():
    def h(campo, valor, metodo):
        return Hallazgo(campo=campo, valor=valor, evidencia="x", confianza=0.95, metodo=metodo)

    nombre = h("nombre_afectado", "JUAN CARLOS PEREZ LOPEZ", "agente_ia")
    del_agente = compuerta.validar(
        {"numero_documento_afectado": h("numero_documento_afectado", "51234987", "agente_ia"),
         "nombre_afectado": nombre}, fuentes=FURAT)
    assert any(r.codigo == "DOCUMENTO_LEJOS_DEL_AFECTADO" for r in del_agente.reparos)
    del_algoritmo = compuerta.validar(
        {"numero_documento_afectado": h("numero_documento_afectado", "51234987", "regla"),
         "nombre_afectado": nombre}, fuentes=FURAT)
    assert not any(r.codigo == "DOCUMENTO_LEJOS_DEL_AFECTADO" for r in del_algoritmo.reparos)


def test_si_lo_escrito_trae_el_corto_el_largo_del_ocr_es_ruido():
    texto = ("Cordialmente, LUIS ALBERTO MORA DIAZ C.C. 79.345.678\n"
             "# historia.pdf\n## Pagina 1 (OCR, confianza 0.70)\n"
             "Nombre : LUIS ALBERTO MORA DIAZ Documento 179345678\n")
    plegado = compuerta._texto_para_roles(texto)
    assert compuerta.documento_del_que_es_un_recorte(
        "79345678", "LUIS ALBERTO MORA DIAZ", plegado) == ""


def test_los_ceros_de_la_banda_de_la_cedula_no_hacen_otro_numero():
    texto = ("Yo PEDRO PABLO RUIZ GOMEZ identificado con la Cc.52417396\n"
             "INDICE DERECHO A-3100100-66133459-M-0052417396-20050614 PEDRO PABLO RUIZ GOMEZ")
    plegado = compuerta._texto_para_roles(texto)
    assert compuerta.documento_del_que_es_un_recorte(
        "52417396", "PEDRO PABLO RUIZ GOMEZ", plegado) == ""
