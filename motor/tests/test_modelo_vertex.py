"""
El adaptador de Vertex separa las instrucciones del dato ajeno.

El prompt del sistema no puede viajar dentro de `contents`: ahi llega por el
mismo canal que el texto de los documentos, que es contenido de terceros y
puede traer ordenes. El propio prompt le dice al modelo que los documentos son
DATO y nunca instruccion, y eso solo se sostiene si estan en canales distintos.

Se prueba con un cliente de mentira: no hace falta red ni gastar modelo.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List

import pytest

from pipeline.modelo import ModeloVertex


@dataclass
class _Respuesta:
    candidates: List[Any] = field(default_factory=list)
    text: str = "LISTO"
    usage_metadata: Any = None


@dataclass
class _Modelos:
    recibido: Dict[str, Any] = field(default_factory=dict)

    def generate_content(self, *, model, contents, config):
        self.recibido = {"model": model, "contents": contents, "config": config}
        return _Respuesta()


@dataclass
class _ClienteFalso:
    models: _Modelos = field(default_factory=_Modelos)


def _modelo():
    cliente = _ClienteFalso()
    return ModeloVertex(proyecto="p", _cliente=cliente), cliente


SISTEMA = "Eres un extractor. Nunca inventes."
MENSAJE = [{"contenido": "Resuelve nombre_afectado."}]


def test_el_prompt_va_como_system_instruction():
    modelo, cliente = _modelo()
    modelo.generar(SISTEMA, MENSAJE, [])
    assert cliente.models.recibido["config"].system_instruction == SISTEMA


def _texto_enviado(cliente):
    """Todo el texto que viajo en `contents`, sea cual sea su turno."""
    partes = [
        p.text for c in cliente.models.recibido["contents"] for p in c.parts
    ]
    return "\n".join(partes)


def test_el_prompt_no_viaja_en_contents():
    """
    Es la mitad que importa: que este en `system_instruction` no sirve de nada
    si tambien se concatena al contenido.
    """
    modelo, cliente = _modelo()
    modelo.generar(SISTEMA, MENSAJE, [])
    assert SISTEMA not in _texto_enviado(cliente)


def test_el_contenido_es_solo_lo_que_se_pregunta():
    modelo, cliente = _modelo()
    modelo.generar(SISTEMA, MENSAJE, [])
    assert _texto_enviado(cliente) == "Resuelve nombre_afectado."


def test_varios_mensajes_se_unen_en_orden():
    modelo, cliente = _modelo()
    modelo.generar(SISTEMA, [{"contenido": "uno"}, {"contenido": "dos"}], [])
    assert _texto_enviado(cliente) == "uno\ndos"


def test_un_mensaje_vacio_no_ensucia_el_contenido():
    modelo, cliente = _modelo()
    modelo.generar(SISTEMA, [{"contenido": "uno"}, {"contenido": ""}], [])
    assert _texto_enviado(cliente) == "uno"


def test_sin_nada_que_preguntar_falla_con_su_motivo():
    """
    Mandar una peticion vacia gasta una llamada y devuelve cualquier cosa. Vale
    mas que reviente diciendo por que.
    """
    modelo, _ = _modelo()
    with pytest.raises(ValueError) as fallo:
        modelo.generar(SISTEMA, [], [])
    assert "vacio" in str(fallo.value)


def test_las_herramientas_llegan_como_declaraciones():
    modelo, cliente = _modelo()
    modelo.generar(SISTEMA, MENSAJE, [{"name": "listar_documentos"}])
    herramientas = cliente.models.recibido["config"].tools
    assert herramientas
    nombres = [
        d.name for h in herramientas for d in (h.function_declarations or [])
    ]
    assert "listar_documentos" in nombres


def test_sin_herramientas_no_se_declara_ninguna():
    modelo, cliente = _modelo()
    modelo.generar(SISTEMA, MENSAJE, [])
    assert not cliente.models.recibido["config"].tools


# ── La conversacion conserva quien dijo cada cosa ──

def _conversacion(mensajes):
    from google.genai import types

    from pipeline.modelo import conversacion

    return conversacion(mensajes, types)


def test_lo_que_dijo_el_modelo_no_se_le_devuelve_como_dicho_por_el_usuario():
    """
    Concatenar todo en una cadena le presenta sus propias respuestas como si se
    las hubiera dicho el usuario, y borra la frontera entre lo que pidio y lo
    que le devolvieron las herramientas.
    """
    turnos = _conversacion([
        {"rol": "usuario", "contenido": "la tarea"},
        {"rol": "asistente", "contenido": "Invoco: listar_documentos()"},
        {"rol": "herramienta", "contenido": "listar_documentos -> {}"},
    ])

    assert [t.role for t in turnos] == ["user", "model", "user"]


def test_los_turnos_seguidos_del_mismo_rol_se_unen():
    """La API los espera alternados."""
    turnos = _conversacion([
        {"rol": "herramienta", "contenido": "uno"},
        {"rol": "herramienta", "contenido": "dos"},
        {"rol": "asistente", "contenido": "tres"},
    ])

    assert [t.role for t in turnos] == ["user", "model"]
    assert [p.text for p in turnos[0].parts] == ["uno", "dos"]


def test_un_mensaje_sin_contenido_no_produce_un_turno_vacio():
    turnos = _conversacion([
        {"rol": "usuario", "contenido": "algo"},
        {"rol": "usuario", "contenido": ""},
        {"rol": "asistente"},
    ])

    assert len(turnos) == 1
    assert turnos[0].parts[0].text == "algo"


def test_sin_mensajes_utiles_el_adaptador_lo_dice():
    """Mandar una peticion vacia gasta una llamada y no pregunta nada."""
    modelo, _ = _modelo()

    with pytest.raises(ValueError) as fallo:
        modelo.generar("sistema", [{"rol": "usuario", "contenido": ""}], [])

    assert "vacio" in str(fallo.value)


# ── Tope de la llamada ──

@pytest.mark.parametrize("global_", [True, False])
def test_la_llamada_al_modelo_lleva_tope_de_tiempo(monkeypatch, global_):
    """
    Sin tope la peticion no vuelve nunca y retiene la corrida entera: el
    presupuesto se comprueba antes de empezar cada correo, no dentro de uno que
    ya arranco.
    """
    from google import genai

    recibido = {}

    def _cliente(**kwargs):
        recibido.update(kwargs)
        return _ClienteFalso()

    monkeypatch.setattr(genai, "Client", _cliente)

    ModeloVertex(proyecto="p", endpoint_global=global_)._obtener_cliente()

    tope = getattr(recibido.get("http_options"), "timeout", None)
    assert isinstance(tope, (int, float)) and tope > 0


def test_el_tope_no_desplaza_al_endpoint_global():
    """El endpoint regional resuelve a un rango inalcanzable desde la VPC."""
    from google import genai

    recibido = {}
    original = genai.Client
    try:
        genai.Client = lambda **kw: recibido.update(kw) or _ClienteFalso()
        ModeloVertex(proyecto="p", endpoint_global=True)._obtener_cliente()
    finally:
        genai.Client = original

    assert (recibido["http_options"].base_url
            == "https://aiplatform.googleapis.com")
