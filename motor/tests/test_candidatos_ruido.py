"""
Tres formas de numero que no identifican a nadie: la parte local de una
direccion, el nombre del archivo que encabeza un adjunto y el numero de la
planilla de aportes.
"""
import pytest

from utils.documento_candidatos import extraer_candidatos_documento


def _numeros(texto):
    return [c["numero"] for c in extraer_candidatos_documento(texto)]


def _candidato(texto, numero):
    for c in extraer_candidatos_documento(texto):
        if c["numero"] == numero:
            return c
    return None


# ── El titulo con el nombre del adjunto ──

@pytest.mark.parametrize("titulo", [
    "# 17139237 (5).pdf",
    "## 40050050 (1).PDF",
    "# planilla-25729347.xlsx",
    "# 1835399375.docx",
])
def test_el_nombre_del_archivo_no_es_un_documento(titulo):
    texto = f"Cordialmente, ANDRES CALLAMAND\n{titulo}\n## Pagina 1\ncontenido"
    numeros = _numeros(texto)
    assert not any(n in titulo for n in numeros), numeros


def test_un_numero_del_cuerpo_no_se_pierde_por_haber_titulos():
    texto = ("El trabajador PEDRO RUIZ con C.C. 41234567 solicita el pago.\n"
             "# 17139237 (5).pdf\n## Pagina 1\nTOTALES PARAFISCALES")
    numeros = _numeros(texto)
    assert "41234567" in numeros
    assert "17139237" not in numeros


# ── La planilla de aportes ──

@pytest.mark.parametrize("texto", [
    "pagos efectuados en la PILA de Marzo PILA # 17139237 fecha de pago",
    "planilla No. 40050050 del mes de abril",
    "comprobante # 25729347 por valor de $144.395",
])
def test_el_numero_de_la_planilla_no_es_un_documento(texto):
    assert _numeros(texto) == [], _numeros(texto)


# ── Separadores que parten el numero ──

@pytest.mark.parametrize("separador", ["`", "´", "'", "‘", "’",
                                        ".", " ", "-"])
def test_un_separador_raro_no_parte_el_numero(separador):
    """
    El teclado y el OCR dejan comillas donde iba el punto de miles: hay que
    contarlas como separador, o un NIT queda mutilado con la longitud justa
    para pasar por cedula.
    """
    texto = f"NIT: 901{separador}642{separador}982 de la empresa"
    assert _numeros(texto) == ["901642982"], _numeros(texto)


def test_una_cifra_de_dinero_sigue_sin_ser_un_documento():
    """Las comas separan miles en los importes y no se admiten como parte."""
    assert _numeros("valor total de $3,601,174 pagado en mayo") == []


def test_un_numero_partido_conserva_su_marca():
    c = _candidato("cedula 41`234`567 del trabajador", "41234567")
    assert c is not None
    assert c["marca_pegada"] is True


# ── Un numero que se repite marcado y sin marcar ──

def test_al_repetirse_gana_la_aparicion_rotulada():
    """En el asunto va desnudo y en la firma va con "cc"."""
    texto = ("Devolucion de aportes Rosalba Duarte 95821926\n"
             "Mil gracias\nAndres Callamand\ncc 95821926")
    c = _candidato(texto, "95821926")
    assert c is not None
    assert c["marca_pegada"] is True
    assert c["tiene_cc"] is True


def test_al_repetirse_no_se_duplica_el_candidato():
    texto = "CC 41234567 del trabajador. Se reitera: cedula 41234567."
    assert _numeros(texto).count("41234567") == 1


def test_una_marca_posterior_no_borra_la_anterior():
    texto = "NIT 900123456 de la empresa. Mas abajo: 900123456 sin rotulo."
    c = _candidato(texto, "900123456")
    assert c is not None and c["tiene_nit"] is True
