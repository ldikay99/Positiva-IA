"""
Un documento que es el recorte de otro escrito junto al mismo nombre no se
radica: el OCR de la foto de la cedula perdio un digito y el correo lo trae
entero.
"""
from pipeline import compuerta
from pipeline.extraccion import Hallazgo

TEXTO = ("Yo Ney Franklin Redondo Melo identificado con la Cc.16769353 de Cali, afiliado "
         "activo, presento derecho de peticion. Atentamente. Ney Franklin Redondo Melo "
         "C.C. 16769353\n# cedula.pdf\n## Pagina 1 (OCR)\nNUMERO 6.769.353 REDONDO MELO")


def test_encuentra_el_documento_entero():
    plegado = compuerta._texto_para_roles(TEXTO)
    assert compuerta.documento_del_que_es_un_recorte(
        "6769353", "Ney Franklin Redondo Melo", plegado) == "16769353"


def test_el_documento_entero_no_es_recorte_de_nada():
    plegado = compuerta._texto_para_roles(TEXTO)
    assert compuerta.documento_del_que_es_un_recorte(
        "16769353", "Ney Franklin Redondo Melo", plegado) == ""


def test_un_numero_parecido_lejos_del_nombre_no_cuenta():
    texto = ("Pedro Ruiz CC 6769353 solicita su certificado. " + "Texto del caso. " * 40
             + "Factura numero 16769353 del prestador.")
    plegado = compuerta._texto_para_roles(texto)
    assert compuerta.documento_del_que_es_un_recorte("6769353", "Pedro Ruiz", plegado) == ""


def test_la_compuerta_lo_devuelve():
    def h(campo, valor):
        return Hallazgo(campo=campo, valor=valor, evidencia="x", confianza=0.95, metodo="agente_ia")

    dictamen = compuerta.validar(
        {"numero_documento_afectado": h("numero_documento_afectado", "6769353"),
         "nombre_afectado": h("nombre_afectado", "Ney Franklin Redondo Melo")},
        fuentes=TEXTO)
    assert any(r.codigo == "DOCUMENTO_RECORTADO" and r.bloqueante for r in dictamen.reparos)


# ── El documento que no aparece junto a la persona, cuando otro si ──

FURAT = ("# 1084868506-angel-esteban-perdomo-calderon.pdf\n## Pagina 1\n"
         "Nombre del trabajador ANGEL ESTEBAN PERDOMO CALDERON identificado con CC 1084868506\n"
         "## Pagina 3\nResponsable | ESTEFANIA VARGAS ORDOÑEZ | Firma | Tipo Doc | CC | "
         "No Documento | 63555077")


def test_el_documento_del_responsable_no_es_del_trabajador():
    plegado = compuerta._texto_para_roles(FURAT)
    assert compuerta.documento_rotulado_de_la_persona(
        "63555077", "ANGEL ESTEBAN PERDOMO CALDERON", plegado) == "1084868506"
    assert compuerta.documento_rotulado_de_la_persona(
        "1084868506", "ANGEL ESTEBAN PERDOMO CALDERON", plegado) == ""


def test_un_celular_o_una_fecha_junto_al_nombre_no_son_rivales():
    texto = ("Pedro Pablo Ruiz celular 3105551234, periodo 202301. "
             + "Relleno del caso. " * 30 + "Documento del caso 41234567.")
    plegado = compuerta._texto_para_roles(texto)
    assert compuerta.documento_rotulado_de_la_persona("41234567", "Pedro Pablo Ruiz", plegado) == ""


def test_solo_se_le_aplica_al_documento_del_agente():
    def h(campo, valor, metodo):
        return Hallazgo(campo=campo, valor=valor, evidencia="x", confianza=0.95, metodo=metodo)

    nombre = h("nombre_afectado", "ANGEL ESTEBAN PERDOMO CALDERON", "agente_ia")
    del_agente = compuerta.validar(
        {"numero_documento_afectado": h("numero_documento_afectado", "63555077", "agente_ia"),
         "nombre_afectado": nombre}, fuentes=FURAT)
    assert any(r.codigo == "DOCUMENTO_LEJOS_DEL_AFECTADO" for r in del_agente.reparos)
    del_algoritmo = compuerta.validar(
        {"numero_documento_afectado": h("numero_documento_afectado", "63555077", "regla"),
         "nombre_afectado": nombre}, fuentes=FURAT)
    assert not any(r.codigo == "DOCUMENTO_LEJOS_DEL_AFECTADO" for r in del_algoritmo.reparos)


def test_si_lo_escrito_trae_el_corto_el_largo_del_ocr_es_ruido():
    texto = ("Cordialmente, ALEXANDER ESTUPINAN ALVARADO C.C. 74.373.954\n"
             "# historia.pdf\n## Pagina 1 (OCR, confianza 0.70)\n"
             "Nombre : ALEXANDER ESTUPINAN ALVARADO Documento 174373954\n")
    plegado = compuerta._texto_para_roles(texto)
    assert compuerta.documento_del_que_es_un_recorte(
        "74373954", "ALEXANDER ESTUPINAN ALVARADO", plegado) == ""


def test_los_ceros_de_la_banda_de_la_cedula_no_hacen_otro_numero():
    texto = ("Yo NEY FRANKLIN REDONDO MELO identificado con la Cc.16769353\n"
             "INDICE DERECHO A-3100100-66133459-M-0016769353-20050614 NEY FRANKLIN REDONDO MELO")
    plegado = compuerta._texto_para_roles(texto)
    assert compuerta.documento_del_que_es_un_recorte(
        "16769353", "NEY FRANKLIN REDONDO MELO", plegado) == ""
