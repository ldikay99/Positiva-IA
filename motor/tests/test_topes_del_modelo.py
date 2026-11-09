"""
La transcripcion por vision tiene tope de salida y de tiempo: una que no
vuelve o que se queda repitiendo un renglon no retiene al correo.
"""
from types import SimpleNamespace

import pytest


def test_una_transcripcion_que_llega_al_tope_no_se_usa(monkeypatch):
    from pipeline import modelo as modelo_mod

    respuesta = SimpleNamespace(
        text="renglon " * 500,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))])
    cliente = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **k: respuesta))
    m = modelo_mod.ModeloVertex(proyecto="p")
    monkeypatch.setattr(m, "_obtener_cliente", lambda: cliente)
    with pytest.raises(modelo_mod.TranscripcionCortada):
        m.transcribir_imagen(b"\xff\xd8\xff")
