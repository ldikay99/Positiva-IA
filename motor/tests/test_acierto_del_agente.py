"""Medida del agente: acertar, fallar y callar se cuentan por separado."""
import json

import pytest

from harness import acierto_del_agente as medida


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """Redirige el harness a un corpus y unas corridas de mentira."""
    casos = tmp_path / "corpus"
    pruebas = tmp_path / "pruebas"
    monkeypatch.setattr(medida, "RAIZ_CORPUS", casos)
    monkeypatch.setattr(medida, "RAIZ_PRUEBAS", pruebas)

    def montar(caso_id, etiquetas, hallazgos, sin_resolver=()):
        d = casos / caso_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "caso.json").write_text(json.dumps({
            "id": caso_id, "asunto": "prueba", "cuerpo_md": "",
            "remitente_nombre": "", "remitente_correo": "",
        }, ensure_ascii=False), encoding="utf-8")
        (d / "esperado.json").write_text(
            json.dumps(etiquetas, ensure_ascii=False), encoding="utf-8")
        w = pruebas / caso_id
        w.mkdir(parents=True, exist_ok=True)
        (w / "resolucion.json").write_text(json.dumps({
            "hallazgos": hallazgos,
            "sin_resolver": {c: "MOTIVO" for c in sin_resolver},
        }, ensure_ascii=False), encoding="utf-8")

    return montar


def _reporta(campo, valor, confianza=0.9):
    return {"campo": campo, "valor": valor,
            "evidencia": "cuerpo del correo", "confianza": confianza}


def _tabla(capsys):
    salida = capsys.readouterr().out
    fila = [l for l in salida.splitlines() if l.startswith("TOTAL")][0].split()
    return {"acierta": int(fila[1]), "falla": int(fila[2]), "calla": int(fila[3])}


# ── Lo basico ──

def test_acierta_cuando_coincide_con_la_etiqueta(corpus, capsys):
    corpus("caso-0001", {"tipo_solicitud": 16}, [_reporta("tipo_solicitud", "16")])
    medida.main()
    assert _tabla(capsys) == {"acierta": 1, "falla": 0, "calla": 0}


def test_falla_cuando_no_coincide(corpus, capsys):
    corpus("caso-0001", {"tipo_solicitud": 16}, [_reporta("tipo_solicitud", "5")])
    medida.main()
    assert _tabla(capsys) == {"acierta": 0, "falla": 1, "calla": 0}


def test_calla_cuando_habia_valor_y_no_lo_reporto(corpus, capsys):
    corpus("caso-0001", {"tipo_solicitud": 16}, [], sin_resolver=["tipo_solicitud"])
    medida.main()
    assert _tabla(capsys) == {"acierta": 0, "falla": 0, "calla": 1}


# ── La etiqueta vacia ──

def test_con_etiqueta_vacia_callarse_es_acertar(corpus, capsys):
    """`""` dice que el dato no esta en el correo, no que falte la etiqueta."""
    corpus("caso-0001", {"telefono_afectado": ""}, [],
           sin_resolver=["telefono_afectado"])
    medida.main()
    assert _tabla(capsys) == {"acierta": 1, "falla": 0, "calla": 0}


def test_con_etiqueta_vacia_reportar_algo_es_fallar(corpus, capsys):
    """Si el dato no esta en el correo, cualquier valor se lo invento."""
    corpus("caso-0001", {"telefono_afectado": ""},
           [_reporta("telefono_afectado", "3105551234")])
    medida.main()
    assert _tabla(capsys) == {"acierta": 0, "falla": 1, "calla": 0}


def test_un_campo_sin_etiqueta_no_se_juzga(corpus, capsys):
    """Sin verdad de referencia no hay nada contra que comparar."""
    corpus("caso-0001", {"tipo_solicitud": None},
           [_reporta("tipo_solicitud", "16")])
    medida.main()
    assert _tabla(capsys) == {"acierta": 0, "falla": 0, "calla": 0}


# ── La confianza ──

def test_señala_lo_que_la_compuerta_va_a_descartar(corpus, capsys):
    """Un acierto bajo el umbral cuenta como acierto y ademas se marca."""
    from pipeline.compuerta import CONFIANZA_PARA_RADICAR

    corpus("caso-0001", {"tipo_solicitud": 16},
           [_reporta("tipo_solicitud", "16", CONFIANZA_PARA_RADICAR - 0.1)])
    medida.main()
    salida = capsys.readouterr().out
    assert "BAJO UMBRAL" in salida
    assert _tabla_de(salida) == {"acierta": 1, "falla": 0, "calla": 0}


def _tabla_de(salida):
    fila = [l for l in salida.splitlines() if l.startswith("TOTAL")][0].split()
    return {"acierta": int(fila[1]), "falla": int(fila[2]), "calla": int(fila[3])}


# ── Cuando no hay nada que medir ──

def test_sin_corridas_lo_dice_y_sale_con_error(corpus, capsys):
    """Salir 0 sin medir nada se lee igual que una medida limpia."""
    assert medida.main() == 1
    assert "no hay corridas" in capsys.readouterr().out


def test_una_resolucion_ilegible_se_avisa_y_no_detiene(corpus, capsys, tmp_path):
    corpus("caso-0001", {"tipo_solicitud": 16}, [_reporta("tipo_solicitud", "16")])
    roto = tmp_path / "pruebas" / "caso-0002"
    roto.mkdir(parents=True)
    (roto / "resolucion.json").write_text("{no es json", encoding="utf-8")

    assert medida.main() == 0
    salida = capsys.readouterr().out
    assert "caso-0002" in salida and "ilegible" in salida
