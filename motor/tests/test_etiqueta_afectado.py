"""
La etiqueta del afectado tiene que ser una palabra completa.

Las etiquetas cortas sin frontera encajan dentro de otras palabras: "yo" vive
en "TAMAYO", y por la ruta de mayor confianza del extractor la firma de un
tercero puede terminar como nombre del afectado.
"""
import pytest

from pipeline.extraccion import extraer_nombre_afectado

DOC = "41234567"


def _valor(texto):
    h, _ = extraer_nombre_afectado(texto)
    return h.valor if h else None


# ── La etiqueta dentro de otra palabra ──

@pytest.mark.parametrize("firma", [
    "TAMAYO LOPERA DIANA CRISTINA",
    "MONTOYA SALGADO LUIS FELIPE",
    "ARROYO CASTRO MARIA PAULA",
])
def test_un_apellido_que_acaba_en_yo_no_es_una_etiqueta(firma):
    texto = (f"-Entrenamiento en plan casero de ejercicios.\n{firma}\n"
             f"Tarjeta Medica Nro. 6969344")
    assert _valor(texto) is None, f"leyo el 'yo' de {firma.split()[0]}"


def test_el_paciente_de_verdad_si_se_reconoce():
    texto = f"paciente PEDRO RUIZ MEJIA con CC {DOC}"
    assert _valor(texto) == "PEDRO RUIZ MEJIA"


@pytest.mark.parametrize("etiqueta", [
    "yo", "Yo", "trabajador", "trabajadora", "afiliado", "paciente",
    "titular", "señora", "Sr.", "Sra.", "usuaria", "causante",
])
def test_las_etiquetas_siguen_funcionando(etiqueta):
    assert _valor(f"{etiqueta} PEDRO RUIZ MEJIA con CC {DOC}") == "PEDRO RUIZ MEJIA"


def test_una_etiqueta_pegada_a_la_izquierda_no_cuenta():
    """"apoyo", "ensayo" y "mayo" contienen "yo" y no presentan a nadie."""
    for palabra in ("apoyo", "ensayo", "mayo", "arroyo"):
        assert _valor(f"documento de {palabra} PEDRO RUIZ") is None, palabra


def test_un_nombre_que_empieza_en_yo_no_se_come_su_primera_silaba():
    assert _valor(f"afiliada Yolanda Perez Gomez con CC {DOC}") == \
        "Yolanda Perez Gomez"
