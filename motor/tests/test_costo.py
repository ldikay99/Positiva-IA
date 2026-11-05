"""Contabilidad del consumo del modelo."""
from pipeline.costo import Contador


def test_un_registro_puede_valer_varias_invocaciones():
    """
    El agente resuelve un correo en varias vueltas y se anota una sola vez, con
    los tokens ya sumados. El total de llamadas suma las vueltas, no los
    registros.
    """
    contador = Contador(correos=2)
    contador.registrar("agente", "gemini-2.5-flash", 50_000, 500, turnos=10)
    contador.registrar("agente", "gemini-2.5-flash", 8_000, 100, turnos=2)

    assert contador.total_llamadas == 12
    assert contador.llamadas_por_correo == 6.0
    assert contador.total_entrada == 58_000


def test_el_desglose_por_paso_cuenta_las_mismas_invocaciones():
    contador = Contador(correos=1)
    contador.registrar("agente", "gemini-2.5-flash", 1_000, 10, turnos=4)

    assert contador.por_etiqueta()["agente"]["llamadas"] == 4
    assert contador.por_modelo()["gemini-2.5-flash"]["llamadas"] == 4


def test_un_correo_que_no_llamo_al_modelo_no_suma_invocaciones():
    """La capa determinista resuelve sin modelo: ese correo no gasta nada."""
    contador = Contador(correos=1)
    contador.registrar("agente", "gemini-2.5-flash", 0, 0, turnos=0)

    assert contador.total_llamadas == 0
    assert contador.costo_usd == 0.0


def test_el_costo_sale_de_los_tokens_y_no_de_las_vueltas():
    """Las vueltas describen cuantas veces se llamo, no lo que se factura."""
    uno = Contador(correos=1)
    uno.registrar("agente", "gemini-2.5-flash", 1_000_000, 0, turnos=1)
    varias = Contador(correos=1)
    varias.registrar("agente", "gemini-2.5-flash", 1_000_000, 0, turnos=9)

    assert uno.costo_usd == varias.costo_usd
