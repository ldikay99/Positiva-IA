"""
Atribucion de cada campo a su origen y calculo de la exactitud.

Cubre la comparacion de valores esperados contra obtenidos y el reparto entre
algoritmo, modelo y revision manual.
"""
import pytest

from harness.atribucion import Origen, ResultadoCaso, Tabla, coincide
from harness.caso import Esperado


# ── Comparacion de valores ──

@pytest.mark.parametrize("obtenido,esperado", [
    ("JOSE RAMIREZ PENA", "José Ramírez Peña"),   # acentos y mayusculas
    ("  Maria  Gomez  ", "Maria Gomez"),           # espacios
    ("maria gomez", "MARIA GOMEZ"),
    (18, 18),
    ("18", 18),                                     # numero como texto
    (18, 18),
])
def test_coincidencias_validas(obtenido, esperado):
    assert coincide(obtenido, esperado) is True


@pytest.mark.parametrize("obtenido,esperado", [
    ("Maria Gomez", "Maria Gomes"),
    ("", "Maria Gomez"),
    (None, "Maria Gomez"),
    ("19", 18),
    ("no es numero", 18),
    (None, 18),
])
def test_coincidencias_invalidas(obtenido, esperado):
    assert coincide(obtenido, esperado) is False


def test_un_campo_no_etiquetado_no_cuenta_como_error():
    """None significa 'no evaluado', distinto de 'debe estar vacio'."""
    assert coincide("cualquier cosa", None) is True


def test_cadena_vacia_esperada_exige_vacio():
    """'' significa 'debe quedar vacio' y si trae algo es un error."""
    assert coincide("", "") is True
    assert coincide("algo", "") is False


# ── Atribucion ──

def test_cuenta_el_origen_de_cada_campo():
    r = ResultadoCaso("caso-1")
    r.registrar("numero_documento_afectado", "41234567", Origen.ALGORITMO)
    r.registrar("nombre_afectado", "Maria Gomez", Origen.IA)
    r.registrar("nombre_remitente", "", Origen.NO_RESUELTO,
                motivo_no_resuelto="SIN_NOMBRE_EN_NINGUNA_FUENTE")

    t = Tabla()
    t.agregar(r, Esperado())

    assert t.por_campo["numero_documento_afectado"][Origen.ALGORITMO] == 1
    assert t.por_campo["nombre_afectado"][Origen.IA] == 1
    assert t.por_campo["nombre_remitente"][Origen.NO_RESUELTO] == 1
    assert t.motivos_no_resuelto["SIN_NOMBRE_EN_NINGUNA_FUENTE"] == 1


def test_los_totales_suman_por_origen():
    t = Tabla()
    for i in range(3):
        r = ResultadoCaso(f"caso-{i}")
        r.registrar("a", "x", Origen.ALGORITMO)
        r.registrar("b", "y", Origen.IA)
        t.agregar(r, Esperado())
    totales = t.totales_por_origen()
    assert totales[Origen.ALGORITMO] == 3
    assert totales[Origen.IA] == 3
    assert totales[Origen.NO_RESUELTO] == 0


# ── Exactitud ──

def test_la_exactitud_solo_cuenta_campos_etiquetados():
    r = ResultadoCaso("caso-1")
    r.registrar("nombre_afectado", "Maria Gomez", Origen.IA)
    r.registrar("nombre_remitente", "Quien Sea", Origen.IA)

    # Solo se etiqueta uno de los dos
    t = Tabla()
    t.agregar(r, Esperado(nombre_afectado="Maria Gomez"))

    assert t.exactitud["nombre_afectado"] == (1, 1)
    assert "nombre_remitente" not in t.exactitud


def test_detecta_un_valor_incorrecto():
    r = ResultadoCaso("caso-1")
    r.registrar("numero_documento_afectado", "99999999", Origen.ALGORITMO)
    t = Tabla()
    t.agregar(r, Esperado(numero_documento_afectado="41234567"))
    assert t.exactitud["numero_documento_afectado"] == (0, 1)


def test_resolver_mucho_pero_mal_se_ve_en_el_informe():
    """
    La trampa que estas metricas existen para evitar: 100% resuelto por
    algoritmo y 0% correcto. Si solo se midiera atribucion, se veria perfecto.
    """
    t = Tabla()
    for i in range(5):
        r = ResultadoCaso(f"caso-{i}")
        r.registrar("numero_documento_afectado", "00000000", Origen.ALGORITMO)
        t.agregar(r, Esperado(numero_documento_afectado="41234567"))

    totales = t.totales_por_origen()
    assert totales[Origen.ALGORITMO] == 5           # resolvio todo
    assert t.exactitud["numero_documento_afectado"] == (0, 5)  # y todo mal
    aciertos, evaluados = t.exactitud_global()
    assert (aciertos, evaluados) == (0, 5)


def test_el_informe_se_genera_y_menciona_los_campos():
    t = Tabla()
    r = ResultadoCaso("caso-1")
    r.registrar("nombre_afectado", "Maria Gomez", Origen.IA)
    r.registrar("nombre_remitente", "", Origen.NO_RESUELTO,
                motivo_no_resuelto="SIN_NOMBRE")
    t.agregar(r, Esperado(nombre_afectado="Maria Gomez"))
    texto = t.informe()
    assert "nombre_afectado" in texto
    assert "nombre_remitente" in texto
    assert "SIN_NOMBRE" in texto
    assert "TOTAL" in texto


def test_tabla_vacia_no_revienta():
    assert "TOTAL" in Tabla().informe()


def test_cuenta_casos_con_error():
    t = Tabla()
    r = ResultadoCaso("caso-1", error="explotó el OCR")
    t.agregar(r, Esperado())
    assert t.casos_con_error == 1
