"""
Todo aviso tiene que poder agruparse en el informe.

`Ejecucion._clase_de_aviso` corta en el primer detalle variable (dos puntos,
parentesis o flecha) y agrupa por lo que queda delante. Eso solo funciona si el
aviso empieza por la CAUSA:

  - "paginas sin texto extraible: 3 de 40"  ->  una clase para todos
  - "3 de 40 pagina(s) quedaron sin texto"  ->  una clase por cada par de cifras

Y tiene que ser de una sola linea: el informe imprime un aviso por renglon.
"""
import re

import pytest

from pipeline.compuerta import Dictamen, Reparo, Veredicto, avisos
from pipeline.conversion import Conversion
from pipeline.telemetria import Ejecucion


def _clase(aviso):
    return Ejecucion._clase_de_aviso(aviso)


# ── Forma de cada aviso ──

def _todos_los_avisos():
    """Avisos representativos de cada sitio que los produce."""
    de_la_compuerta = avisos(Dictamen(
        veredicto=Veredicto.RECHAZADO,
        reparos=[
            Reparo("nombre_afectado", "FALTA_CAMPO_OBLIGATORIO", "sin este campo"),
            Reparo("oficina", "OFICINA_NO_CORRESPONDE", "la tabla indica 2",
                   bloqueante=False),
        ],
        correcciones={"tipo_remitente": 3},
    ))
    return de_la_compuerta + [
        "deduplicacion degradada: redis no responde",
        "inyeccion detectada: instruccion",
        "por defecto: oficina no se resolvio",
        "el agente agoto las iteraciones",
        "el agente reporto un campo que ya estaba resuelto: tipo_pqrd",
        "el agente reporto un codigo fuera del catalogo: tipo_pqrd = '99'",
        "etiquetar_correo fallo (PermissionError: 403): puede radicarse dos veces",
        "etiqueta de radicado: el formato no es el esperado",
        "el PDF excede el limite de paginas: 40, se convirtieron las primeras 25",
        "paginas que no se pudieron leer: 3",
        "paginas sin texto extraible: 3 de 40; el OCR no encontro nada",
    ]


@pytest.mark.parametrize("aviso", _todos_los_avisos())
def test_un_aviso_es_de_una_sola_linea(aviso):
    assert "\n" not in aviso, aviso


@pytest.mark.parametrize("aviso", _todos_los_avisos())
def test_la_clase_de_un_aviso_no_empieza_por_una_cifra(aviso):
    """
    Una cifra delante hace que la clase cambie con el dato, y entonces cada
    correo genera la suya y desplaza del informe a todo lo demas.
    """
    clase = _clase(aviso)
    assert clase, aviso
    assert not re.match(r"^\d", clase), f"la clase varia con el dato: {clase!r}"


@pytest.mark.parametrize("aviso", _todos_los_avisos())
def test_la_clase_de_un_aviso_no_lleva_cifras(aviso):
    assert not re.search(r"\d", _clase(aviso)), \
        f"la clase lleva un numero y por tanto varia: {_clase(aviso)!r}"


# ── Los avisos de la conversion, tal como los produce ──

def test_los_avisos_del_pdf_agrupan_igual_con_cifras_distintas():
    de_40 = "paginas sin texto extraible: 3 de 40; el OCR no encontro nada"
    de_12 = "paginas sin texto extraible: 1 de 12; tesseract no instalado"
    assert _clase(de_40) == _clase(de_12)


def test_los_reparos_de_la_compuerta_agrupan_por_codigo():
    """Treinta correos rechazados por lo mismo son una linea, no treinta."""
    uno = avisos(Dictamen(Veredicto.RECHAZADO, [
        Reparo("nombre_afectado", "FALTA_CAMPO_OBLIGATORIO", "sin este campo")]))
    otro = avisos(Dictamen(Veredicto.RECHAZADO, [
        Reparo("nombre_remitente", "FALTA_CAMPO_OBLIGATORIO", "sin este campo")]))
    assert _clase(uno[0]) == _clase(otro[0])


def test_un_reparo_bloqueante_se_distingue_de_un_aviso():
    lineas = avisos(Dictamen(Veredicto.CORREGIDO, [
        Reparo("a", "X", "d"), Reparo("b", "Y", "d", bloqueante=False)]))
    assert any("rechazo" in l for l in lineas)
    assert any("aviso" in l for l in lineas)


def test_una_correccion_queda_como_aviso_propio():
    lineas = avisos(Dictamen(Veredicto.CORREGIDO, [],
                             correcciones={"oficina": 2}))
    assert any("corrigio" in l and "oficina" in l for l in lineas)


def test_una_conversion_sin_problemas_no_deja_avisos():
    assert Conversion(markdown="# x\n\ntexto", tipo_real="pdf").aviso == ""


# ── El nombre del adjunto va detras de la causa ──

@pytest.mark.parametrize("aviso", [
    "adjunto ilegible: 'soporte.pdf' — ninguna pagina dio texto",
    "adjunto truncado por tamaño: 'historia-clinica.pdf'",
    "paginas sin texto extraible: 3 de 40 (adjunto 'escaneo.pdf')",
    "el PDF excede el limite de paginas: 40, se convirtieron las primeras 25 "
    "(adjunto 'expediente.pdf')",
])
def test_la_clase_no_es_el_nombre_del_adjunto(aviso):
    """La clase de un aviso no puede ser el nombre del archivo: eso crearia una clase por archivo en vez de por causa."""
    clase = _clase(aviso)
    assert ".pdf" not in clase, f"la clase es el nombre del archivo: {clase!r}"
    assert "'" not in clase, clase


def test_dos_adjuntos_con_el_mismo_problema_agrupan_juntos():
    uno = "adjunto ilegible: 'a.pdf' — ninguna pagina dio texto"
    otro = "adjunto ilegible: 'b.pdf' — tesseract no instalado"
    assert _clase(uno) == _clase(otro)
