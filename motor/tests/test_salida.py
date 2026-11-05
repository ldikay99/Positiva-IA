"""
Formato de la telemetria por consola.

Cloud Logging trata cada linea de la salida estandar como una entrada
independiente. Un informe impreso a pelo se convierte en decenas de entradas
sueltas y deja de poder leerse; una linea de JSON es una sola entrada con
campos consultables.
"""
import json

import pytest

from pipeline.salida import (
    AVISO,
    ERROR,
    INFO,
    VARIABLES_DE_CLOUD_RUN,
    emitir,
    en_la_nube,
)


@pytest.fixture
def json_forzado(monkeypatch):
    monkeypatch.setenv("FORMATO_LOG", "json")


@pytest.fixture
def texto_forzado(monkeypatch):
    monkeypatch.setenv("FORMATO_LOG", "texto")


# ── Cuando se usa cada formato ──

def _sin_entorno(monkeypatch):
    monkeypatch.delenv("FORMATO_LOG", raising=False)
    for variable in VARIABLES_DE_CLOUD_RUN:
        monkeypatch.delenv(variable, raising=False)


@pytest.mark.parametrize("variable", VARIABLES_DE_CLOUD_RUN)
def test_cloud_run_se_detecta_solo(monkeypatch, variable):
    """
    Un Service anuncia `K_SERVICE` y un Job anuncia las suyas. Mirando solo la
    del Service, el Job de radicacion imprimia en formato de terminal y cada
    entrada se partia en una linea por campo.
    """
    _sin_entorno(monkeypatch)
    monkeypatch.setenv(variable, "ms-radicacion-ia-pqrd")
    assert en_la_nube()


def test_en_una_terminal_sale_texto(monkeypatch):
    _sin_entorno(monkeypatch)
    assert not en_la_nube()


def test_el_formato_se_puede_forzar(monkeypatch):
    """Para poder comprobar como se vera sin desplegar."""
    _sin_entorno(monkeypatch)
    monkeypatch.setenv("FORMATO_LOG", "json")
    assert en_la_nube()
    monkeypatch.setenv("CLOUD_RUN_JOB", "algo")
    monkeypatch.setenv("FORMATO_LOG", "texto")
    assert not en_la_nube()


# ── El informe no se puede partir ──

def test_el_informe_sale_en_una_sola_linea(json_forzado, capsys):
    """
    Un informe de sesenta renglones impreso a pelo son sesenta entradas, cada
    una con su hora, que no se pueden leer juntas.
    """
    informe = "\n".join(f"renglon {i}" for i in range(60))
    emitir("ejecucion terminada", informe=informe)

    salida = capsys.readouterr().out.strip()
    assert salida.count("\n") == 0
    assert json.loads(salida)["informe"] == informe


def test_los_numeros_quedan_en_campos_propios(json_forzado, capsys):
    """Es lo que permite comparar las 48 corridas del dia desde el explorador."""
    emitir("ejecucion terminada", correos=25, radicados=20, manual=5,
           costo_usd=0.117037)

    entrada = json.loads(capsys.readouterr().out)
    assert entrada["correos"] == 25
    assert entrada["radicados"] == 20
    assert entrada["costo_usd"] == 0.117037


def test_el_mensaje_va_en_el_campo_que_cloud_logging_muestra(json_forzado, capsys):
    emitir("ejecucion terminada: 20 radicados")
    entrada = json.loads(capsys.readouterr().out)
    assert entrada["message"] == "ejecucion terminada: 20 radicados"


@pytest.mark.parametrize("severidad", [INFO, AVISO, ERROR])
def test_la_severidad_viaja_donde_cloud_logging_la_lee(json_forzado, capsys,
                                                      severidad):
    emitir("algo", severidad=severidad)
    assert json.loads(capsys.readouterr().out)["severity"] == severidad


def test_un_dato_que_no_es_json_no_tumba_la_corrida(json_forzado, capsys):
    """Perder el formato de un campo no puede costar el lote entero."""
    class Raro:
        def __str__(self):
            return "un objeto"

    emitir("algo", campo=Raro())
    assert json.loads(capsys.readouterr().out)["campo"] == "un objeto"


def test_los_acentos_no_se_escapan(json_forzado, capsys):
    """El informe se lee en pantalla: `\\u00f3` no lo lee nadie."""
    emitir("radicación terminada")
    assert "radicación" in capsys.readouterr().out


# ── En texto se sigue leyendo ──

def test_en_texto_el_informe_sale_entero(texto_forzado, capsys):
    emitir("ejecucion terminada", correos=25, informe="renglon 1\nrenglon 2")
    salida = capsys.readouterr().out
    assert "ejecucion terminada" in salida
    assert "correos=25" in salida
    assert "renglon 1\nrenglon 2" in salida


def test_en_texto_no_se_repite_lo_que_el_mensaje_ya_dice(texto_forzado, capsys):
    """
    Un campo por renglon eran trece renglones por correo, y diez repetian la
    primera linea o estaban en blanco.
    """
    emitir("[1/6] [AgT1xLRAAA] radicado ENT1",
           solo_json=("correo", "n", "de", "accion", "radicado"),
           correo="AgT1xLRAAA", n=1, de=6, accion="radicado", radicado="ENT1",
           campos=9, vueltas=5)
    salida = capsys.readouterr().out

    assert salida.count("\n") == 2, salida
    assert "campos=9" in salida and "vueltas=5" in salida
    for repetido in ("correo=", "accion=", "radicado=", "n=1", "de=6"):
        assert repetido not in salida


def test_en_texto_lo_vacio_no_ocupa_renglon(texto_forzado, capsys):
    emitir("un correo", motivo_manual="", por_que=None, sin_resolver=[],
           campos=3)
    salida = capsys.readouterr().out

    assert "motivo_manual" not in salida
    assert "por_que" not in salida
    assert "sin_resolver" not in salida
    assert "campos=3" in salida


def test_los_campos_largos_van_cada_uno_en_su_renglon(texto_forzado, capsys):
    """
    Repartirlos por el largo del valor dejaba el mismo campo unas veces suelto
    y otras dentro de la linea compacta, y no se podia recorrer con la vista.
    """
    emitir("un correo", en_su_linea=("asunto", "sin_resolver", "por_que"),
           asunto="Solicitud de incapacidad", sin_resolver=["a", "b"],
           por_que="No estaba en el correo.", campos=9)
    renglones = capsys.readouterr().out.splitlines()

    assert renglones[1].strip() == "asunto: Solicitud de incapacidad"
    assert renglones[2].strip() == "sin resolver: a, b"
    assert renglones[3].strip() == "por que: No estaba en el correo."
    assert renglones[4].strip() == "campos=9"


def test_en_json_siguen_estando_todos_los_campos(monkeypatch, capsys):
    """
    Lo que se omite es del formato de consola. En Cloud Logging se filtra por
    esos campos, asi que ahi van todos.
    """
    monkeypatch.setenv("FORMATO_LOG", "json")
    emitir("[1/6] [AgT1xLRAAA] radicado ENT1",
           solo_json=("correo", "accion"), en_su_linea=("asunto",),
           correo="AgT1xLRAAA", accion="radicado", asunto="Algo", campos=9)
    entrada = json.loads(capsys.readouterr().out)

    assert entrada["correo"] == "AgT1xLRAAA"
    assert entrada["accion"] == "radicado"
    assert entrada["asunto"] == "Algo"
    assert entrada["campos"] == 9


# ── Seguir un correo concreto en la consola ──

def test_la_referencia_distingue_correos_del_mismo_buzon():
    """
    Los identificadores de Graph del mismo buzon coinciden en casi todo su
    largo: cortarlos por el principio da la misma cadena para todos.
    """
    from correr import referencia_del_correo

    base = "AAMkADcyMDJmMDA3LTVhNzItNDVlNS04NWViLWVjZmUyNWJiYTcxMQBGAAAA"
    uno = base + "AAgT0zHOAAA="
    otro = base + "AAgT0zFcAAA="

    assert referencia_del_correo(uno) != referencia_del_correo(otro)
    assert len(referencia_del_correo(uno)) <= 10


def test_la_referencia_aguanta_un_identificador_vacio():
    from correr import referencia_del_correo

    assert referencia_del_correo("") == "?"
    assert referencia_del_correo(None) == "?"


def test_el_informe_y_la_consola_usan_la_misma_referencia():
    """Sin eso no se pueden cruzar las entradas de un correo con su detalle."""
    from correr import referencia_del_correo
    from pipeline.telemetria import referencia

    id_correo = "AAMkADcyMDJmMDA3LTVhNzItNDVlNQBGAAgT0zHOAAA="
    assert referencia_del_correo(id_correo) == referencia(id_correo)


def test_una_etapa_rapida_no_ensucia_la_consola(monkeypatch, capsys):
    """Once etapas por correo tapan la que importa; su tiempo va en el informe."""
    monkeypatch.setenv("FORMATO_LOG", "json")
    import correr

    correr._paso("AAMkADcyBGAAgT0zHOAAA=", "compuerta", 12)
    assert capsys.readouterr().out == ""


def test_una_etapa_lenta_si_se_anuncia(monkeypatch, capsys):
    monkeypatch.setenv("FORMATO_LOG", "json")
    import correr

    correr._paso("AAMkADcyBGAAgT0zHOAAA=", "agente", 92_659)
    salida = capsys.readouterr().out
    assert "agente" in salida
    assert "92659" in salida


def test_una_etapa_que_fallo_se_anuncia_aunque_sea_rapida(monkeypatch, capsys):
    monkeypatch.setenv("FORMATO_LOG", "json")
    import correr

    correr._paso("AAMkADcyBGAAgT0zHOAAA=", "radicacion", 8, fallo="TimeoutError")
    salida = capsys.readouterr().out
    assert "radicacion" in salida
    assert "TimeoutError" in salida


# ── El umbral de etapa lenta depende de la etapa ──

def test_cada_etapa_se_mide_contra_su_propio_tiempo():
    """
    Un umbral igual para todas anuncia como lenta la que SIEMPRE tarda: el
    agente son varias llamadas al modelo y el OCR recorre cada pagina.
    """
    from correr import UMBRAL_ETAPA_LENTA_MS, umbral_de

    assert umbral_de("agente") > UMBRAL_ETAPA_LENTA_MS
    assert umbral_de("conversion_a_markdown") > UMBRAL_ETAPA_LENTA_MS
    assert umbral_de("etapa_que_no_esta_en_la_tabla") == UMBRAL_ETAPA_LENTA_MS


def test_el_agente_en_su_tiempo_normal_no_se_anuncia(monkeypatch, capsys):
    """Veinte segundos de agente es lo normal, no una anomalia."""
    import correr

    monkeypatch.setenv("FORMATO_LOG", "texto")
    correr._paso("AAAAAAAAAA", "agente", 20_000)
    assert capsys.readouterr().out == ""


def test_el_agente_muy_por_encima_si_se_anuncia(monkeypatch, capsys):
    import correr

    monkeypatch.setenv("FORMATO_LOG", "texto")
    correr._paso("AAAAAAAAAA", "agente", 31_062)
    assert "agente" in capsys.readouterr().out


def test_el_fallo_de_varias_lineas_queda_en_un_renglon():
    """
    Cloud Logging convierte cada linea en una entrada: un fallo de GCS trae el
    cuerpo JSON de la respuesta y se partia en veinte entradas sueltas.
    """
    from correr import LARGO_DEL_FALLO, en_una_linea

    crudo = 'Forbidden: 403 POST https://x/y: {\n  "error": {\n    "code": 403\n  }\n}\n'
    linea = en_una_linea(crudo)

    assert "\n" not in linea
    assert len(linea) <= LARGO_DEL_FALLO
    assert linea.startswith("Forbidden: 403")
