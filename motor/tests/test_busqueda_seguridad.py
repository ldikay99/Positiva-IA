"""
El patron de busqueda lo elige el modelo, y el modelo lee documentos de fuera.

`buscar_en_documentos` compila ese patron como expresion regular. Uno con
cuantificadores anidados, `(a+)+`, hace que el motor explore un numero
exponencial de combinaciones: el proceso se queda colgado. Un adjunto puede
sugerirle al modelo exactamente ese patron, asi que es la misma superficie de
inyeccion que el resto del contenido.
"""
import time

import pytest

from pipeline.materializar import materializar
from servidor_mcp.herramientas import (
    _cuantificador_anidado,
    buscar_en_documentos,
    Contexto,
)

# Contra una linea larga, cada uno de estos cuelga a `re` indefinidamente.
CATASTROFICOS = [
    "(a+)+$",
    "(a*)*b",
    "(x+x+)+y",
    "([a-z]+)*$",
    "(\\d+)+#",
    "(a|a)+$",
    "(a?)*b",          # el cuantificador interno puede ser `?`
    "(a+){2,}b",       # `{n,}` no tiene tope
]

SEGUROS = [
    "cedula",
    "c\\.?c\\.?\\s*\\d+",
    r"\d{6,11}",
    "(cedula|documento|nit)",
    "PEDRO RUIZ",
    r"(?:c\.c\.|cc)\s*\d+",
    # Repeticion ACOTADA de un grupo: prueba pocos caminos, no explota. Son
    # las formas que usan los propios extractores del pipeline.
    r"(?:\+?57[\s\-.]*)?\d{10}",
    r"\bpasaporte\b\s*(?:no\.?|nro\.?|#)?\s*\d+",
    r"(?:[A-Z][a-z]+){1,4}",
]


@pytest.mark.parametrize("patron", CATASTROFICOS)
def test_un_patron_catastrofico_se_rechaza(patron):
    assert _cuantificador_anidado(patron), patron


@pytest.mark.parametrize("patron", SEGUROS)
def test_un_patron_normal_pasa(patron):
    """
    Si rechazara los patrones utiles, el agente se quedaria sin la herramienta
    que evita leer documentos enteros, y el gasto volveria a subir.
    """
    assert not _cuantificador_anidado(patron), patron


def test_un_cuantificador_escapado_no_cuenta():
    """`\\(a\\+\\)\\+` busca el texto literal '(a+)+', no es un anidamiento."""
    assert not _cuantificador_anidado(r"\(a\+\)\+")


def test_un_escape_no_arrastra_su_cuantificador_al_grupo():
    """El escape se retira antes de buscar cuantificadores para que `\\s*` no quede convertido en `*` pegado al parentesis."""
    assert not _cuantificador_anidado(r"(cc|cedula)\s*\d+")


def test_el_prefijo_de_un_grupo_no_capturante_no_es_cuantificador():
    """El `?` de `(?:` no es un cuantificador del cuerpo del grupo."""
    assert not _cuantificador_anidado(r"(?:cedula)+")


@pytest.mark.parametrize("patron", [
    "(a+)?b",          # `?` prueba dos caminos, no explota
    "(a+){2,4}b",      # tope superior: numero acotado de caminos
    "(cc|cedula)?",
])
def test_una_repeticion_acotada_no_se_rechaza(patron):
    """
    Tratar `?` y `{n,m}` como peligrosos seria peor que no filtrarlos: el
    agente se quedaria sin la herramienta que le evita leer documentos
    enteros y el gasto volveria a subir.
    """
    assert not _cuantificador_anidado(patron), patron


def test_un_grupo_repetido_con_alternativas_se_rechaza():
    """
    `(cc|cedula)+` no tiene cuantificador interno, pero si dos alternativas
    pueden coincidir con lo mismo la explosion es igual.
    """
    assert "alternativas" in _cuantificador_anidado(r"(?:cc|cedula)+")


def test_el_grupo_sin_cuantificador_afuera_pasa():
    assert not _cuantificador_anidado("(cedula|cc)\\s*\\d+")


@pytest.mark.parametrize("patron", [
    "(cedula|documento|nit)",
    r"\+(\d{1,3})[\s\-.]*(\d{7,12})",
    "(PEDRO RUIZ)",
])
def test_un_grupo_al_final_del_patron_no_esta_cuantificado(patron):
    """
    `"" in "*+"` es True en Python: hay que cortar el caso vacio, o cualquier
    grupo que cierre al final del patron se toma como repetido.
    """
    assert not _cuantificador_anidado(patron), patron


# ── Efecto en la herramienta ──

@pytest.fixture
def ctx(tmp_path):
    # Una linea larga es lo que dispara el problema: el OCR produce paginas
    # enteras en una sola linea.
    ws = materializar(
        id_correo="c1", asunto="Soportes", cuerpo_limpio="a" * 4000,
        remitente_nombre="Ana", remitente_correo="ana@x.com",
        adjuntos=[], base=str(tmp_path), temporal=False,
    )
    yield Contexto(workspace=ws)
    ws.cerrar()


def test_la_herramienta_rechaza_en_vez_de_colgarse(ctx):
    inicio = time.monotonic()
    r = buscar_en_documentos(ctx, "(a+)+$")
    assert time.monotonic() - inicio < 1.0, "se puso a evaluar el patron"
    assert not r.ok
    assert "colgar el proceso" in r.error


def test_el_error_le_dice_al_modelo_que_hacer(ctx):
    """Un rechazo sin salida hace que el modelo reintente lo mismo."""
    r = buscar_en_documentos(ctx, "(a+)+$")
    assert "texto literal" in r.error


def test_una_busqueda_normal_sigue_funcionando(ctx):
    r = buscar_en_documentos(ctx, "aaa")
    assert r.ok
    assert r.datos["total"] >= 1


def test_un_patron_invalido_sigue_dando_su_error(ctx):
    """Un patron mal escrito sigue dando su propio error, distinto del rechazo por anidamiento."""
    r = buscar_en_documentos(ctx, "(sin cerrar")
    assert not r.ok
    assert "patron invalido" in r.error
