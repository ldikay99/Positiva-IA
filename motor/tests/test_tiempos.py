"""
Desglose por etapa de la duracion de cada correo.

Comprueban que el desglose exista, que cubra las etapas caras y que lo no
medido se declare como tal en vez de repartirse entre las demas.
"""
import pytest

from pipeline.orquestador import Orquestador, Resultado
from pipeline.telemetria import RegistroCorreo, nueva_ejecucion
from tests.test_orquestador import (
    RadicadorFalso,
    _email,
    _modelo_que_clasifica,
)

# Las que pueden tardar de verdad. Si alguna deja de medirse, el tiempo se va a
# "(sin medir)" y la optimizacion se hace a ciegas.
ETAPAS_CARAS = {"descarga_adjuntos", "conversion_a_markdown", "agente"}


def test_el_cronometro_acumula_cuando_la_etapa_entra_dos_veces():
    """
    La deduplicacion corre en dos momentos del pipeline. Si el cronometro
    sobrescribiera, se perderia el primero.
    """
    r = Resultado(email_id="c1")
    with r.cronometrar("etapa"):
        pass
    primero = r.etapas_ms["etapa"]
    with r.cronometrar("etapa"):
        pass
    assert r.etapas_ms["etapa"] >= primero


def test_una_etapa_que_revienta_igual_queda_medida():
    """Lo que falla suele ser justo lo lento: perder su tiempo es perder el dato."""
    r = Resultado(email_id="c1")
    with pytest.raises(ValueError):
        with r.cronometrar("etapa"):
            raise ValueError("x")
    assert "etapa" in r.etapas_ms


@pytest.mark.asyncio
async def test_el_pipeline_mide_sus_etapas_caras(tmp_path):
    orq = Orquestador(modelo=_modelo_que_clasifica(), radicador=RadicadorFalso(),
                      base_workspace=str(tmp_path))
    r = await orq.procesar(_email())

    assert r.accion == "radicado", r.motivo_manual
    faltantes = ETAPAS_CARAS - set(r.etapas_ms)
    assert not faltantes, f"etapas caras sin medir: {faltantes}"


@pytest.mark.asyncio
async def test_las_etapas_no_suman_mas_que_el_correo(tmp_path):
    """Si sumaran mas, alguna se estaria contando dos veces."""
    orq = Orquestador(modelo=_modelo_que_clasifica(), radicador=RadicadorFalso(),
                      base_workspace=str(tmp_path))
    r = await orq.procesar(_email())
    assert sum(r.etapas_ms.values()) <= r.duracion_ms + 5


@pytest.mark.asyncio
async def test_el_desglose_llega_al_informe(tmp_path):
    ejecucion = nueva_ejecucion(tmp_path, id_ejecucion="t")
    orq = Orquestador(modelo=_modelo_que_clasifica(), radicador=RadicadorFalso(),
                      base_workspace=str(tmp_path), ejecucion=ejecucion)
    await orq.procesar(_email())

    informe = ejecucion.informe()
    assert "DONDE SE VA EL TIEMPO" in informe
    assert "agente" in informe


def test_lo_no_medido_no_se_reparte_entre_las_etapas(tmp_path):
    """El porcentaje de cada etapa va sobre lo medido, no sobre el total."""
    ejecucion = nueva_ejecucion(tmp_path, id_ejecucion="t")
    ejecucion.registrar(RegistroCorreo(
        id_correo="c1", accion="radicado", duracion_ms=1000,
        etapas_ms={"agente": 200, "conversion_a_markdown": 100},
    ))
    informe = ejecucion.informe()
    assert "(sin medir)" in informe
    assert "66%" in informe, "el agente es 200 de 300 medidos, no 200 de 1000"


def test_sin_etapas_no_se_inventa_la_seccion(tmp_path):
    ejecucion = nueva_ejecucion(tmp_path, id_ejecucion="t")
    ejecucion.registrar(RegistroCorreo(id_correo="c1", accion="radicado"))
    assert "DONDE SE VA EL TIEMPO" not in ejecucion.informe()


# ── Agrupacion de avisos en el informe ──

def _con_avisos(tmp_path, por_correo):
    ejecucion = nueva_ejecucion(tmp_path, id_ejecucion="t")
    for i, avisos in enumerate(por_correo):
        ejecucion.registrar(RegistroCorreo(
            id_correo=f"correo-{i}", accion="radicado", avisos=list(avisos)))
    return ejecucion


def test_el_mismo_aviso_en_muchos_correos_se_agrupa(tmp_path):
    """Los avisos de la misma clase se cuentan en una sola linea."""
    ejecucion = _con_avisos(tmp_path, [
        ["deduplicacion degradada: redis no responde"] for _ in range(30)
    ] + [["inyeccion detectada: instruccion"]])

    informe = ejecucion.informe()
    assert "30x  deduplicacion degradada" in informe
    assert "inyeccion detectada" in informe, "lo raro quedo tapado por lo repetido"


def test_un_aviso_dice_a_que_correo_pertenece(tmp_path):
    """Sin el correo, el operador no puede ir a mirarlo."""
    ejecucion = _con_avisos(tmp_path, [["por defecto: oficina no se resolvio"]])
    assert "correo-0" in ejecucion.informe()


def test_no_se_recorta_en_silencio(tmp_path):
    """Las clases que no caben en el informe se cuentan explicitamente."""
    from pipeline.telemetria import Ejecucion

    ejecucion = _con_avisos(tmp_path, [
        [f"clase numero {i}: detalle"]
        for i in range(Ejecucion.MAX_CLASES_DE_AVISO + 5)
    ])
    informe = ejecucion.informe()
    assert "y 5 clase(s) mas" in informe


def test_los_descartes_del_agente_se_agrupan_por_causa(tmp_path):
    """Cinco correos donde el modelo reporto un campo ya resuelto son un problema, no cinco."""
    campos = ("nombre_afectado", "tipo_pqrd", "sexo_afectado",
              "tipo_remitente", "telefono_afectado")
    ejecucion = _con_avisos(tmp_path, [
        [f"el agente reporto un campo que ya estaba resuelto: {c}"]
        for c in campos
    ])
    informe = ejecucion.informe()
    assert "5x  el agente reporto un campo que ya estaba resuelto" in informe
    assert "AVISOS (5 en 1 clases)" in informe


def test_el_total_de_avisos_no_miente(tmp_path):
    ejecucion = _con_avisos(tmp_path, [["a: 1", "b: 2"], ["a: 3"]])
    assert "AVISOS (3 en 2 clases)" in ejecucion.informe()


def test_un_detalle_variable_no_crea_una_clase_por_correo(tmp_path):
    """El nombre del archivo no forma parte de la clase del aviso."""
    ejecucion = _con_avisos(tmp_path, [
        [f"no se pudo convertir: soporte-{i}.pdf"] for i in range(10)
    ])
    assert "10x  no se pudo convertir" in ejecucion.informe()


def test_sin_avisos_no_se_inventa_la_seccion(tmp_path):
    assert "AVISOS" not in _con_avisos(tmp_path, [[]]).informe()
