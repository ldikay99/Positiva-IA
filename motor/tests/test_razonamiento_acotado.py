"""
El razonamiento del modelo se puede acotar por entorno.
"""


def test_el_tope_de_razonamiento_se_lee_del_entorno(monkeypatch):
    from pipeline import modelo as modelo_mod

    monkeypatch.delenv("RAZONAMIENTO_TOKENS", raising=False)
    assert modelo_mod.tope_de_razonamiento() is None
    monkeypatch.setenv("RAZONAMIENTO_TOKENS", "512")
    assert modelo_mod.tope_de_razonamiento() == 512
