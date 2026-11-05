"""
El historial del agente es la mayor parte de su gasto.

Cada turno reenvia lo anterior. Una respuesta de herramienta puede traer 2.500
caracteres de un documento, y sin recortarla viaja otra vez en cada vuelta. Lo
que ya se uso no hace falta completo; lo ultimo si, porque es sobre lo que el
modelo esta razonando.
"""
import pytest

from pipeline.agente import (
    MAX_CARACTERES_HERRAMIENTA_VIEJA,
    VENTANA_HISTORIAL,
    _compactar,
    resolver,
)
from pipeline.extraccion import extraer_todo
from pipeline.modelo import Llamada, ModeloFalso, Turno
from servidor_mcp.herramientas import Contexto


def _mensaje(rol, largo=3000):
    return {"rol": rol, "contenido": "x" * largo}


# ── Que se recorta y que no ──

def test_la_ultima_respuesta_va_completa():
    """La respuesta mas reciente no se recorta."""
    historial = [_mensaje("herramienta"), _mensaje("asistente"),
                 _mensaje("herramienta")]
    assert _compactar(historial)[-1]["contenido"] == historial[-1]["contenido"]


def test_una_respuesta_vieja_se_recorta():
    historial = [_mensaje("herramienta"), _mensaje("asistente")]
    compactado = _compactar(historial)
    assert len(compactado[0]["contenido"]) < 3000
    assert "recortado" in compactado[0]["contenido"]


def test_los_turnos_del_modelo_no_se_tocan():
    """
    Son lo que le recuerda que herramientas ya invoco. Recortarlos lo haria
    repetir hasta agotar las vueltas.
    """
    historial = [_mensaje("asistente"), _mensaje("herramienta")]
    assert _compactar(historial)[0] == historial[0]


def test_una_respuesta_corta_no_se_toca():
    historial = [_mensaje("herramienta", largo=50), _mensaje("asistente")]
    assert _compactar(historial)[0]["contenido"] == "x" * 50


def test_un_historial_de_un_mensaje_no_se_toca():
    historial = [_mensaje("herramienta")]
    assert _compactar(historial) == historial


def test_no_se_pierde_ningun_mensaje():
    """Recortar no es descartar: el orden y la cantidad se conservan."""
    historial = [_mensaje("herramienta"), _mensaje("asistente"),
                 _mensaje("herramienta"), _mensaje("asistente")]
    compactado = _compactar(historial)
    assert len(compactado) == len(historial)
    assert [m["rol"] for m in compactado] == [m["rol"] for m in historial]


# ── Efecto sobre lo que recibe el modelo ──

def _extraccion():
    return extraer_todo(
        asunto="Pago de incapacidad",
        cuerpo="Incapacidad del trabajador PEDRO RUIZ con CC 41234567",
        from_name="Ana Lopez", from_address="ana@empresa.com",
    )


def _modelo_que_lee_documentos(vueltas=4):
    turnos = [
        Turno(llamadas=[Llamada("listar_documentos", {})])
        for _ in range(vueltas)
    ]
    turnos.append(Turno(texto="LISTO"))
    return ModeloFalso(turnos=turnos)


def test_el_modelo_no_recibe_dos_veces_el_mismo_documento(tmp_path):
    from pipeline.materializar import materializar

    ws = materializar(
        id_correo="c1", asunto="Soportes", cuerpo_limpio="cuerpo",
        remitente_nombre="Ana", remitente_correo="ana@x.com",
        adjuntos=[("soporte.txt", b"contenido largo " * 300)],
        base=str(tmp_path), temporal=False,
    )
    try:
        modelo = _modelo_que_lee_documentos()
        resolver(modelo, Contexto(workspace=ws), _extraccion())

        ultimo = modelo.recibidos[-1]["mensajes"]
        recortados = [
            m for m in ultimo
            if "recortado" in m.get("contenido", "")
        ]
        assert recortados, "ninguna respuesta vieja se recorto"
        for m in recortados:
            assert len(m["contenido"]) < 400
    finally:
        ws.cerrar()


def test_la_tarea_original_nunca_se_recorta(tmp_path):
    """
    Lleva los campos pendientes con su motivo. Si se recortara, el agente
    perderia lo unico que le dice que tiene que hacer.
    """
    modelo = _modelo_que_lee_documentos(vueltas=3)
    extraccion = _extraccion()
    resolver(modelo, Contexto(workspace=None), extraccion)

    # La tarea lleva ademas los catalogos de los campos que son codigo, asi que
    # se comprueba que el resumen viaja entero, no que sea lo unico que hay.
    for recibido in modelo.recibidos:
        assert extraccion.resumen_para_ia() in recibido["mensajes"][0]["contenido"]


def test_la_ventana_sigue_acotando_el_historial(tmp_path):
    """Recortar no reemplaza a la ventana: las dos limitan cosas distintas."""
    modelo = _modelo_que_lee_documentos(vueltas=6)
    resolver(modelo, Contexto(workspace=None), _extraccion())

    for recibido in modelo.recibidos:
        # tarea + ventana
        assert len(recibido["mensajes"]) <= 1 + VENTANA_HISTORIAL * 2


def test_el_recorte_esta_declarado():
    assert MAX_CARACTERES_HERRAMIENTA_VIEJA > 0


def test_una_herramienta_que_revienta_no_tumba_el_correo():
    """
    Sin esto, una excepcion dentro de una herramienta se lleva por delante el
    bucle del agente y con el todo el correo. El modelo tiene que poder leer
    el fallo, cambiar de estrategia y seguir.
    """
    from servidor_mcp.herramientas import invocar

    r = invocar(Contexto(workspace=None), "listar_documentos", {})
    assert not r.ok
    assert "listar_documentos" in r.error
    assert "AttributeError" in r.error
