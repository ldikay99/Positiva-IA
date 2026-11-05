"""
El segundo agente: revisa lo que se va a enviar antes de que se envie.

El principal resuelve y este comprueba. Lo que aqui se verifica es el contrato
entre los dos: que el revisor solo pueda leer, que su veredicto llegue entero
al principal, y que una respuesta que no se entiende quede constando en vez de
descartarse en silencio.
"""
import pytest

from pipeline.agente import ESQUEMAS_HERRAMIENTAS
from pipeline.materializar import materializar
from pipeline.modelo import Llamada, ModeloFalso, Turno
from pipeline.revisor import (
    HERRAMIENTAS_DEL_REVISOR,
    leer_reparos,
    revisar,
)
from servidor_mcp.herramientas import Contexto, invocar

ESQUEMAS = [e for e in ESQUEMAS_HERRAMIENTAS
            if e["name"] in HERRAMIENTAS_DEL_REVISOR]


@pytest.fixture
def ctx(tmp_path):
    ws = materializar(
        id_correo="revisor-1",
        asunto="Solicitud de incapacidad",
        cuerpo_limpio="Solicito el pago de la incapacidad de JUAN PEREZ\n"
                      "identificado con C.C. 41234567.\n"
                      "Cordialmente, Notificaciones Judiciales",
        base=str(tmp_path), temporal=False,
    )
    contexto = Contexto(workspace=ws)
    yield contexto
    ws.cerrar()


# ── Como se lee su respuesta ──

def test_se_entienden_los_tres_veredictos():
    reparos, ilegibles = leer_reparos(
        "nombre_afectado: OK\n"
        "nombre_remitente: CAMBIAR a JUAN PEREZ | correo.md linea 2\n"
        "direccion_afectado: SIN RESPALDO | busque calle y carrera, no hay\n"
        "REVISADO")

    assert not ilegibles
    assert reparos["nombre_afectado"].veredicto == "OK"
    assert not reparos["nombre_afectado"].contradice
    assert reparos["nombre_remitente"].veredicto == "CAMBIAR"
    assert reparos["nombre_remitente"].valor == "JUAN PEREZ"
    assert reparos["nombre_remitente"].contradice
    assert reparos["direccion_afectado"].veredicto == "SIN RESPALDO"


def test_una_linea_que_no_se_entiende_queda_constando():
    """
    Si el revisor cambia de formato hay que verlo. Descartarla en silencio
    dejaria el campo sin revisar sin que nadie lo sepa.
    """
    _, ilegibles = leer_reparos("nombre_afectado: me parece que si\n"
                                "esto no lleva dos puntos")

    assert len(ilegibles) == 2


# ── Que puede hacer y que no ──

def test_el_revisor_solo_lee_documentos():
    """
    No reporta hallazgos ni consulta el registro: si pudiera reportar, seria un
    segundo extractor y no un revisor.
    """
    assert set(HERRAMIENTAS_DEL_REVISOR) == {
        "listar_documentos", "leer_documento", "buscar_en_documentos"}


def test_una_herramienta_de_escritura_se_le_rechaza(ctx):
    modelo = ModeloFalso(turnos=[
        Turno(llamadas=[Llamada("reportar_hallazgo", {
            "campo": "nombre_afectado", "valor": "OTRO",
            "evidencia": "x", "confianza": 0.9})]),
        Turno(texto="nombre_afectado: OK\nREVISADO"),
    ])

    r = revisar(modelo, ctx, {"nombre_afectado": "JUAN PEREZ"}, ESQUEMAS)

    assert ctx.hallazgos == [], "el revisor no puede reportar nada"
    assert r.reparos["nombre_afectado"].veredicto == "OK"


def test_sin_valores_no_se_llama_al_modelo(ctx):
    modelo = ModeloFalso()

    r = revisar(modelo, ctx, {}, ESQUEMAS)

    assert modelo.veces_llamado == 0
    assert not r.hay_reparos


def test_puede_leer_los_documentos_antes_de_opinar(ctx):
    modelo = ModeloFalso(turnos=[
        Turno(llamadas=[Llamada("buscar_en_documentos", {"patron": "PEREZ"})]),
        Turno(texto="nombre_afectado: OK | correo.md\nREVISADO"),
    ])

    r = revisar(modelo, ctx, {"nombre_afectado": "JUAN PEREZ"}, ESQUEMAS)

    assert r.llamadas_herramienta == 1
    assert r.reparos["nombre_afectado"].veredicto == "OK"


def test_el_veredicto_llega_entero_al_principal(ctx):
    modelo = ModeloFalso(turnos=[Turno(
        texto="nombre_remitente: CAMBIAR a JUAN PEREZ | el firmante es un buzon\n"
              "REVISADO")])

    r = revisar(modelo, ctx, {"nombre_remitente": "Notificaciones Judiciales"},
                ESQUEMAS)

    assert r.hay_reparos
    assert "CAMBIAR a JUAN PEREZ" in r.resumen()


# ── Como lo delega el agente principal ──

def test_la_herramienta_pasa_lo_resuelto_y_lo_reportado(ctx):
    """
    El agente no reporta lo que el algoritmo ya resolvio, pero eso tambien se
    va a enviar: el revisor tiene que verlo.
    """
    visto = {}

    class RevisionFalsa:
        def resumen(self):
            return "sin reparos"
        hay_reparos = False

    def revisar_falso(valores):
        visto.update(valores)
        return RevisionFalsa()

    ctx.resueltos = {"numero_documento_afectado": "41234567"}
    ctx.revisar = revisar_falso
    ctx.hallazgos.append({"campo": "nombre_afectado", "valor": "JUAN PEREZ"})

    r = invocar(ctx, "pedir_revision", {})

    assert r.ok
    assert visto == {"numero_documento_afectado": "41234567",
                     "nombre_afectado": "JUAN PEREZ"}


def test_sin_revisor_configurado_la_herramienta_lo_dice(ctx):
    ctx.hallazgos.append({"campo": "nombre_afectado", "valor": "JUAN PEREZ"})

    r = invocar(ctx, "pedir_revision", {})

    assert not r.ok
    assert "no esta disponible" in r.error
