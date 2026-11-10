"""
Pruebas de la estructura editable de los flujos de código (PQRD, tutelas y sus copias). Sin red ni IA.

    docker exec -w /app/puente positiva-ia-positiva-ia-1 python3 pruebas_estructura.py
"""
from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import threading

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
MOTOR = os.environ.get("RUTA_MOTOR", os.path.join(os.path.dirname(AQUI), "motor"))
sys.path.insert(0, MOTOR)
for _v in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID", "OUTLOOK_REFRESH_TOKEN", "OUTLOOK_FOLDER_ID",
           "REDIS_PASSWORD", "SUPABASE_SERVICE_ROLE_KEY", "SGDEA_CLIENT_SECRET", "SGDEA_PASSWORD"):
    os.environ.setdefault(_v, "x")
os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:9")
os.environ["MODO_TEST"] = "true"

import estructura_codigo as ec  # noqa: E402


def _pqrd():
    return ec.por_defecto("pqrd")


def _sin(d, desde, hacia):
    d["aristas"] = [a for a in d["aristas"] if (a["desde"], a["hacia"]) != (desde, hacia)]


def _nodo(d, nid):
    return next(n for n in d["nodos"] if n["id"] == nid)


def _insertar_antes_de_validar(d, motivo="SIN_DOCUMENTO"):
    _sin(d, "registro_de_personas", "compuerta")
    d["nodos"] += [{"id": "exigir_documento", "tipo": "condicion", "nombre": "¿Hay documento?",
                    "config": {"campo": "campos.numero_documento_afectado", "operador": "no_vacio"}},
                   {"id": "sin_documento", "tipo": "salida", "nombre": "Sin documento", "config": {"desenlace": "manual", "motivo": motivo}}]
    d["aristas"] += [{"desde": "registro_de_personas", "hacia": "exigir_documento"},
                     {"desde": "exigir_documento", "hacia": "compuerta", "cuando": "si"},
                     {"desde": "exigir_documento", "hacia": "sin_documento", "cuando": "no"}]
    return d


# ------------------------------------------------------------------ validación

def prueba_la_del_codigo_es_valida() -> None:
    for base in ("pqrd", "tutelas"):
        v = ec.validar(base, ec.por_defecto(base))
        assert v["ok"] and not v["tramos"] and not v["apagadas"], (base, v["problemas"])


def prueba_una_etapa_no_se_quita_y_se_explica() -> None:
    d = _pqrd()
    d["nodos"] = [n for n in d["nodos"] if n["id"] != "revisor"]
    v = ec.validar("pqrd", d)
    assert not v["ok"] and any("no se quitan" in p and "deshabilitado" in p for p in v["problemas"]), v["problemas"]


def prueba_se_apaga_lo_que_se_puede_y_lo_demas_dice_por_que() -> None:
    d = _pqrd()
    _nodo(d, "revisor")["config"] = {"deshabilitado": True}
    _nodo(d, "volumen")["config"] = {"deshabilitado": True}
    v = ec.validar("pqrd", d)
    assert v["ok"] and set(v["apagadas"]) == {"revisor", "volumen"}, v
    _nodo(d, "compuerta")["config"] = {"deshabilitado": True}
    v = ec.validar("pqrd", d)
    assert any(p.startswith("compuerta: esta etapa no se puede apagar") for p in v["problemas"]), v["problemas"]


def prueba_lectores_y_cupo_de_vision() -> None:
    d = _pqrd()
    _nodo(d, "conversion_a_markdown")["config"] = {"lectores": {"heic": "vision", "png": "ocr", "docx": "omitir"}}
    _nodo(d, "transcripcion_vision")["config"] = {"max_imagenes": 12}
    assert ec.validar("pqrd", d)["ok"]
    _nodo(d, "conversion_a_markdown")["config"] = {"lectores": {"pdf": "vision", "mp4": "ocr"}}
    p = " | ".join(ec.validar("pqrd", d)["problemas"])
    assert "solo lee imágenes" in p and "«mp4» no es una extensión" in p, p
    _nodo(d, "agente")["config"] = {"lectores": {"png": "ocr"}}
    assert any("agente: «lectores» no es una opción" in x for x in ec.validar("pqrd", d)["problemas"])


def prueba_el_orden_es_el_del_codigo_salvo_el_intercambio() -> None:
    d = _pqrd()
    _sin(d, "saneamiento", "extraccion_determinista")
    d["aristas"].append({"desde": "saneamiento", "hacia": "agente"})
    v = ec.validar("pqrd", d)
    assert any("no existe en el motor" in p for p in v["problemas"]) and any("falta la conexión" in p for p in v["problemas"]), v["problemas"]
    d = _pqrd()
    for x, y in (("descarga_adjuntos", "volumen"), ("volumen", "deduplicacion_contenido"), ("deduplicacion_contenido", "conversion_a_markdown")):
        _sin(d, x, y)
    d["aristas"] += [{"desde": "descarga_adjuntos", "hacia": "deduplicacion_contenido"}, {"desde": "deduplicacion_contenido", "hacia": "volumen"},
                     {"desde": "volumen", "hacia": "conversion_a_markdown"}]
    v = ec.validar("pqrd", d)
    assert v["ok"] and v["intercambio"] == ["volumen", "deduplicacion_contenido"], v


def prueba_pasos_insertados_en_un_punto() -> None:
    v = ec.validar("pqrd", _insertar_antes_de_validar(_pqrd()))
    assert v["ok"], v["problemas"]
    t = v["tramos"][0]
    assert t["punto"] == "antes_de_validar" and t["primero"] == "exigir_documento" and set(t["nodos"]) == {"exigir_documento", "sin_documento"}


def prueba_fuera_de_un_punto_se_explica_donde_si() -> None:
    d = _pqrd()
    _sin(d, "saneamiento", "extraccion_determinista")
    d["nodos"].append({"id": "nota", "tipo": "transformar", "nombre": "Nota", "config": {"operaciones": [{"op": "plantilla", "plantilla": "x", "salida": "y"}]}})
    d["aristas"] += [{"desde": "saneamiento", "hacia": "nota"}, {"desde": "nota", "hacia": "extraccion_determinista"}]
    p = " | ".join(ec.validar("pqrd", d)["problemas"])
    assert "no se pueden insertar pasos" in p and "Antes de validar (registro_de_personas → compuerta)" in p, p


def prueba_salidas_dentro_del_codigo() -> None:
    d = _insertar_antes_de_validar(_pqrd())
    _nodo(d, "sin_documento")["config"]["desenlace"] = "completar"
    assert any("solo puede mandar a revisión manual" in p for p in ec.validar("pqrd", d)["problemas"])
    d = _pqrd()
    _sin(d, "conversion_a_markdown", "saneamiento")
    d["nodos"] += [{"id": "hay", "tipo": "condicion", "nombre": "¿Hay?", "config": {"campo": "adjuntos.cantidad", "operador": "no_vacio"}},
                   {"id": "fuera", "tipo": "salida", "nombre": "Fuera", "config": {"desenlace": "manual", "motivo": "X"}}]
    d["aristas"] += [{"desde": "conversion_a_markdown", "hacia": "hay"}, {"desde": "hay", "hacia": "saneamiento", "cuando": "si"},
                     {"desde": "hay", "hacia": "fuera", "cuando": "no"}]
    assert any("no se puede mandar a revisión manual" in p for p in ec.validar("pqrd", d)["problemas"])


def prueba_los_pasos_se_validan_como_declarativos() -> None:
    d = _insertar_antes_de_validar(_pqrd())
    _nodo(d, "exigir_documento")["config"]["operador"] = "parecido"
    p = ec.validar("pqrd", d)["problemas"]
    assert any(x.startswith("exigir_documento:") for x in p), p
    d = _insertar_antes_de_validar(_pqrd())
    _nodo(d, "exigir_documento")["config"]["campo"] = "{{no_existe.x}}"
    d2 = copy.deepcopy(d)
    _nodo(d2, "exigir_documento")["tipo"] = "esperar"
    assert any("no va dentro de un flujo de código" in x for x in ec.validar("pqrd", d2)["problemas"])


def prueba_tutelas_antes_de_radicar_y_enlace() -> None:
    d = ec.por_defecto("tutelas")
    _nodo(d, "enlace")["config"] = {"deshabilitado": True}
    _sin(d, "duplicados", "radicar")
    d["nodos"] += [{"id": "de_bogota", "tipo": "condicion", "nombre": "¿Bogotá?", "config": {"campo": "juzgado.municipio", "operador": "contiene", "valor": "bogota"}},
                   {"id": "otra_ciudad", "tipo": "salida", "nombre": "Otra ciudad", "config": {"desenlace": "manual", "motivo": "OTRA_CIUDAD"}}]
    d["aristas"] += [{"desde": "duplicados", "hacia": "de_bogota"}, {"desde": "de_bogota", "hacia": "radicar", "cuando": "si"},
                     {"desde": "de_bogota", "hacia": "otra_ciudad", "cuando": "no"}]
    v = ec.validar("tutelas", d)
    assert v["ok"] and v["apagadas"] == ["enlace"] and v["tramos"][0]["punto"] == "antes_de_radicar", v


# ------------------------------------------------------------------ correr un tramo

def _correr(d, punto, contexto):
    v = ec.validar("pqrd", d)
    tramo = next(t for t in v["tramos"] if t["punto"] == punto)
    salida = io.StringIO()
    r = ec.correr_segmento(tramo, d, "pqrd", contexto, {}, "prueba-1", salida, threading.Lock())
    eventos = [json.loads(x[5:]) for x in salida.getvalue().split("\n") if x.startswith("\x1eEVT ")]
    return r, eventos


def prueba_el_tramo_corre_con_los_datos_del_punto() -> None:
    d = _insertar_antes_de_validar(_pqrd())
    r, ev = _correr(d, "antes_de_validar", {"correo": {"asunto": "Derecho de petición"}, "variables": {"campos": {"numero_documento_afectado": "12345678"}}})
    assert r["desenlace"] == "sigue", r
    assert any(e["tipo"] == "etapa_fin" and e.get("etapa") == "exigir_documento" and e.get("salida") == "si" and e.get("corrida") == "prueba-1" for e in ev), ev
    r, _ = _correr(d, "antes_de_validar", {"correo": {}, "variables": {"campos": {"numero_documento_afectado": ""}}})
    assert r["desenlace"] == "manual" and r["motivo"] == "SIN_DOCUMENTO", r


def prueba_el_tramo_fija_campos_y_agrega_documento() -> None:
    d = _pqrd()
    _sin(d, "registro_de_personas", "compuerta")
    d["nodos"].append({"id": "normalizar", "tipo": "transformar", "nombre": "Normalizar",
                       "config": {"operaciones": [{"op": "mayusculas", "de": "campos.nombre_afectado", "salida": "nombre_afectado"}]}})
    d["aristas"] += [{"desde": "registro_de_personas", "hacia": "normalizar"}, {"desde": "normalizar", "hacia": "compuerta"}]
    assert ec.validar("pqrd", d)["ok"], ec.validar("pqrd", d)["problemas"]
    r, _ = _correr(d, "antes_de_validar", {"correo": {}, "variables": {"campos": {"nombre_afectado": "ana pérez"}}})
    assert r["variables"]["nombre_afectado"] == "ANA PÉREZ" and r["variables"]["campos"]["nombre_afectado"] == "ana pérez", r["variables"]
    d = _pqrd()
    _sin(d, "conversion_a_markdown", "saneamiento")
    d["nodos"].append({"id": "nota", "tipo": "transformar", "nombre": "Nota",
                       "config": {"operaciones": [{"op": "plantilla", "plantilla": "No leídos: {{no_leidos}}", "salida": "documento_extra"}]}})
    d["aristas"] += [{"desde": "conversion_a_markdown", "hacia": "nota"}, {"desde": "nota", "hacia": "saneamiento"}]
    r, _ = _correr(d, "con_los_documentos", {"correo": {}, "variables": {"no_leidos": ["foto.heic"], "documento_extra": ""}})
    assert "foto.heic" in str(r["variables"]["documento_extra"]), r["variables"]


def prueba_un_paso_de_pqrd_llama_a_otro_flujo() -> None:
    """Encadenar: un «subflujo» insertado en una copia de PQRD corre un flujo declarativo y sigue según cómo terminó."""
    sub = {"id": "validar-doc", "nombre": "Validar documento", "modelo": "gemini-2.5-flash",
           "nodos": [{"id": "inicio", "tipo": "entrada_manual", "nombre": "Inicio", "config": {"campos": [{"nombre": "documento", "tipo": "texto"}]}},
                     {"id": "hay", "tipo": "condicion", "nombre": "¿Hay?", "config": {"campo": "entrada.documento", "operador": "no_vacio"}},
                     {"id": "bien", "tipo": "salida", "nombre": "Bien", "config": {"desenlace": "completar"}},
                     {"id": "mal", "tipo": "salida", "nombre": "Mal", "config": {"desenlace": "manual", "motivo": "SIN_DOC_EN_SUBFLUJO"}}],
           "aristas": [{"desde": "inicio", "hacia": "hay"}, {"desde": "hay", "hacia": "bien", "cuando": "si"}, {"desde": "hay", "hacia": "mal", "cuando": "no"}]}
    d = _pqrd()
    _sin(d, "registro_de_personas", "compuerta")
    d["nodos"] += [{"id": "llamar_validador", "tipo": "subflujo", "nombre": "Validar en el otro flujo",
                    "config": {"flujo": "validar-doc", "entrada": {"documento": "{{campos.numero_documento_afectado}}"}, "salida": "validacion"}},
                   {"id": "a_revision", "tipo": "salida", "nombre": "A revisión", "config": {"desenlace": "manual", "motivo": "{{validacion.motivo}}"}}]
    d["aristas"] += [{"desde": "registro_de_personas", "hacia": "llamar_validador"}, {"desde": "llamar_validador", "hacia": "compuerta"},
                     {"desde": "llamar_validador", "hacia": "a_revision", "cuando": "manual"}]
    v = ec.validar("pqrd", d)
    assert v["ok"], v["problemas"]
    tramo = next(t for t in v["tramos"] if t["punto"] == "antes_de_validar")
    orden = {"subflujos": {"validar-doc": {"definicion": sub}}}
    r = ec.correr_segmento(tramo, d, "pqrd", {"correo": {}, "variables": {"campos": {"numero_documento_afectado": "12345678"}}}, orden, "prueba-sub", io.StringIO(), threading.Lock())
    assert r["desenlace"] == "sigue", r
    r = ec.correr_segmento(tramo, d, "pqrd", {"correo": {}, "variables": {"campos": {"numero_documento_afectado": ""}}}, orden, "prueba-sub", io.StringIO(), threading.Lock())
    assert r["desenlace"] == "manual" and r["motivo"] == "SIN_DOC_EN_SUBFLUJO", r
    # sin el flujo llamado en la orden, falla con su motivo (no se inventa; el trabajador lo deja como error del paso)
    try:
        ec.correr_segmento(tramo, d, "pqrd", {"correo": {}, "variables": {"campos": {"numero_documento_afectado": "1"}}}, {}, "prueba-sub", io.StringIO(), threading.Lock())
        raise AssertionError("debió fallar sin el flujo llamado")
    except Exception as e:  # noqa: BLE001
        assert "validar-doc" in str(e), e


# ------------------------------------------------------------------ en el trabajador

class _Trabajador:
    """Lo que estructura_trabajador usa del trabajador de PQRD."""

    REAL = False
    TRABAJO = tempfile.mkdtemp(prefix="estructura-")
    _salida = io.StringIO()
    _cerrojo = threading.Lock()
    _motivo_facturas = {"original": "FACTURA_ELECTRONICA"}

    def __init__(self):
        self.eventos = []
        self._actual = {"corrida": "prueba-2"}

    def evento(self, tipo, **d):
        self.eventos.append({"tipo": tipo, **d})

    def _paso(self, nodo, titulo, tono="info", detalle="", **d):
        self.eventos.append({"tipo": "paso", "nodo": nodo, "titulo": titulo})


def prueba_el_trabajador_instala_y_quita_todo() -> None:
    import estructura_trabajador as et
    from pipeline import cache_conversion, conversion, duplicados, orquestador

    antes = (duplicados.Detector.comprobar, orquestador.Orquestador._revision_obligatoria, cache_conversion.convertir,
             orquestador.Orquestador._validar_y_corregir, orquestador.materializar, conversion.MAX_IMAGENES_IA_POR_CORREO)
    d = _insertar_antes_de_validar(_pqrd())
    _nodo(d, "deduplicacion")["config"] = {"deshabilitado": True}
    _nodo(d, "revisor")["config"] = {"deshabilitado": True}
    _nodo(d, "transcripcion_vision")["config"] = {"max_imagenes": 3}
    _nodo(d, "conversion_a_markdown")["config"] = {"lectores": {"docx": "omitir"}}
    tr = _Trabajador()
    assert et.instalar(tr, {"estructura": {"definicion": d, "version": 4}}) is None
    try:
        assert duplicados.Detector.comprobar is not antes[0] and orquestador.Orquestador._revision_obligatoria is not antes[1]
        assert cache_conversion.convertir is not antes[2] and orquestador.Orquestador._validar_y_corregir is not antes[3]
        assert conversion.MAX_IMAGENES_IA_POR_CORREO == 3
        c = cache_conversion.convertir(b"PK\x03\x04 cualquier cosa", "carta.docx")
        assert c.vacio and "no lee los .docx" in c.aviso
        e = next(x for x in tr.eventos if x["tipo"] == "estructura")
        assert e["version"] == 4 and "deduplicacion" in e["apagadas"] and e["tramos"][0]["punto"] == "antes_de_validar"
    finally:
        et.quitar()
    despues = (duplicados.Detector.comprobar, orquestador.Orquestador._revision_obligatoria, cache_conversion.convertir,
               orquestador.Orquestador._validar_y_corregir, orquestador.materializar, conversion.MAX_IMAGENES_IA_POR_CORREO)
    assert despues == antes, "quedó algo instalado después de la orden"


def prueba_sin_estructura_no_toca_nada_y_con_radicacion_real_no_corre() -> None:
    import estructura_trabajador as et
    from pipeline import cache_conversion

    antes = cache_conversion.convertir
    assert et.instalar(_Trabajador(), {}) is None and cache_conversion.convertir is antes
    tr = _Trabajador()
    tr.REAL = True
    assert "radicación real" in (et.instalar(tr, {"estructura": {"definicion": _pqrd()}}) or "")
    malo = _pqrd()
    malo["nodos"] = malo["nodos"][1:]
    assert "no es válida" in (et.instalar(_Trabajador(), {"estructura": {"definicion": malo}}) or "")


def principal() -> int:
    pruebas = [(n, f) for n, f in globals().items() if n.startswith("prueba_") and callable(f)]
    fallos = 0
    for nombre, f in pruebas:
        try:
            f()
            print(f"ok    {nombre}")
        except Exception as e:  # noqa: BLE001
            fallos += 1
            print(f"FALLA {nombre}: {type(e).__name__}: {e}")
    print(f"\n{len(pruebas) - fallos} de {len(pruebas)} pruebas pasaron")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
