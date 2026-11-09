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
