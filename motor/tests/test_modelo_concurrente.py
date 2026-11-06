"""El cliente de Vertex se crea una sola vez aunque lo pidan varios hilos."""
import sys
import threading
import types as tipos

from pipeline.modelo import ModeloVertex


def test_varios_hilos_crean_un_solo_cliente(monkeypatch):
    creados = []

    class Cliente:
        def __init__(self, **kwargs):
            creados.append(kwargs)

    falso_types = tipos.SimpleNamespace(
        HttpOptions=lambda **k: k,
        GenerateContentConfig=lambda **k: k,
        Tool=lambda **k: k,
    )
    genai = tipos.ModuleType("google.genai")
    genai.Client = Cliente
    genai.types = falso_types
    google = tipos.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", falso_types)

    modelo = ModeloVertex(proyecto="p")
    hilos = [threading.Thread(target=modelo._obtener_cliente) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(creados) == 1
