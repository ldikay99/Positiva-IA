"""
El correo que pide corregir una identidad trae dos, y dice cual es la mala.

Hay un genero de correo que existe justamente para enmendar un dato ya cargado:
"solicito la anulacion del Furat a nombre de X, identificacion que no
corresponde al colaborador accidentado" seguido de "el cargue al colaborador Y,
quien si presento el accidente". La identidad equivocada lleva el marcador mas
fuerte del texto, asi que sin esta distincion es la que gana.
"""
from pipeline.extraccion import extraer_todo

MALO = "Juan Manuel Pardo Tejada"
DOC_MALO = "1098765432"
BUENO = "Jose Luis Julio Bonilla"
DOC_BUENO = "1076543210"

CORRECCION = (
    f"Buenas tardes,\n"
    f"solicito por favor la anulacion del Furat N 808504781 a nombre del señor "
    f"{MALO} CC {DOC_MALO} realizado el pasado 16/07/2026, identificacion la "
    f"cual no corresponde al colaborador accidentado.\n"
    f"de igual manera solicitamos el cargue con la documentacion "
    f"correspondiente al colaborador {BUENO} CC {DOC_BUENO} quien si presento "
    f"el accidente y recibio la atencion.\n"
)


def _extraer(cuerpo, asunto="Correccion de Furat"):
    return extraer_todo(
        asunto=asunto, cuerpo=cuerpo,
        from_name="Yuliana Palacio", from_address="yuliana@empresa.com",
    )


# ── La identidad que se pide anular no es la del afectado ──

def test_el_nombre_que_se_pide_anular_no_es_el_afectado():
    """
    Registrar al trabajador equivocado es el error que el correo viene a
    corregir: repetirlo obliga a mandar un tercer correo.
    """
    h = _extraer(CORRECCION).hallazgos.get("nombre_afectado")
    assert h is not None, "el nombre correcto sigue estando en el texto"
    assert MALO not in str(h.valor)


def test_el_documento_que_se_pide_anular_no_es_el_del_afectado():
    h = _extraer(CORRECCION).hallazgos.get("numero_documento_afectado")
    assert h is not None
    assert str(h.valor) != DOC_MALO


def test_la_identidad_correcta_si_se_resuelve():
    e = _extraer(CORRECCION)
    assert str(e.hallazgos["numero_documento_afectado"].valor) == DOC_BUENO


# ── Los limites de la clausula ──

def test_un_valor_que_tambien_sale_fuera_de_la_clausula_sobrevive():
    """
    Basta una aparicion fuera: el mismo trabajador puede estar nombrado en la
    correccion y tambien en el resto del correo, y entonces si es el afectado.
    """
    cuerpo = (
        f"solicito la anulacion del registro de {BUENO} CC {DOC_BUENO} por un "
        f"error de digitacion.\n"
        f"El accidente lo sufrio {BUENO}, identificado con CC {DOC_BUENO}, "
        f"el 16 de julio.\n"
    )
    e = _extraer(cuerpo)
    assert str(e.hallazgos["numero_documento_afectado"].valor) == DOC_BUENO


def test_la_clausula_termina_en_el_punto():
    """
    Sin cerrar la clausula, el tramo se come el resto del correo y con el la
    identidad correcta.
    """
    cuerpo = (
        f"El dato es incorrecto. El colaborador accidentado es {BUENO}, "
        f"identificado con CC {DOC_BUENO}.\n"
    )
    e = _extraer(cuerpo)
    assert str(e.hallazgos["numero_documento_afectado"].valor) == DOC_BUENO


def test_sin_marca_de_correccion_no_cambia_nada():
    cuerpo = (
        f"El colaborador {BUENO} identificado con CC {DOC_BUENO} sufrio un "
        f"accidente de trabajo el 16 de julio.\n"
    )
    e = _extraer(cuerpo)
    assert str(e.hallazgos["numero_documento_afectado"].valor) == DOC_BUENO
    assert BUENO.split()[0] in str(e.hallazgos["nombre_afectado"].valor)
