"""
El razonamiento del modelo se factura como salida: se cuenta como tal, y
tambien lo que Vertex sirve de su cache.
"""
from types import SimpleNamespace


def test_el_razonamiento_se_cuenta_como_salida():
    """Se factura como salida: sin contarlo, el costo de una vuelta salia a la mitad."""
    from pipeline.modelo import _turno_de_respuesta

    meta = SimpleNamespace(prompt_token_count=1000, candidates_token_count=50,
                           thoughts_token_count=400, cached_content_token_count=600)
    turno = _turno_de_respuesta(SimpleNamespace(candidates=[], text="LISTO",
                                                usage_metadata=meta), "gemini-2.5-flash")
    assert turno.tokens_salida == 450
    assert turno.tokens_razonamiento == 400 and turno.tokens_cache == 600


def test_el_contador_de_la_corrida_cuenta_el_razonamiento():
    from pipeline.costo import Contador

    c = Contador()
    c.registrar_respuesta("agente", "gemini-2.5-flash", SimpleNamespace(
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5,
                                       thoughts_token_count=20)))
    assert c.total_salida == 25
