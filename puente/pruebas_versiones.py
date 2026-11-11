"""
Las herramientas de versiones de Vera (vera_versiones.py) y lo que manda con cada pedido: quién es (x-autor),
lo que pidió la persona (x-pedido, para el punto «antes de: …») y que solo diga que restauró con
confirmado=true. Por escrito y por voz son las mismas. Sin red ni IA (la API es falsa).

    docker exec -w /app/puente positiva-ia-positiva-ia-1 python3 pruebas_versiones.py
"""
from __future__ import annotations

import inspect
import os
import sys
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asistente  # noqa: E402
import vera_versiones  # noqa: E402
import voz  # noqa: E402


class ApiFalsa:
    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.llamadas = []

    def __call__(self, metodo, ruta, cuerpo=None):
        self.llamadas.append((metodo, ruta, cuerpo))
        for (m, r), res in self.respuestas.items():
            if m == metodo and ruta.startswith(r):
                return res(cuerpo) if callable(res) else res
        return {"error": "HTTP 404"}


def con_api(falsa):
    class Ctx:
        def __enter__(self):
            self.api, self.emitir = asistente.api, asistente.emitir
            self.emitidos = []
            asistente.api = falsa
            asistente.emitir = lambda tipo, **d: self.emitidos.append((tipo, d))
            return self

        def __exit__(self, *x):
            asistente.api, asistente.emitir = self.api, self.emitir
    return Ctx()


def prueba_cada_pedido_dice_que_es_vera_y_lo_que_pidio_la_persona() -> None:
    asistente.fijar_pedido_de({"conversacion": "c-1", "mensajes": [{"rol": "usuario", "texto": "hola"}, {"rol": "asistente", "texto": "…"},
                                                                    {"rol": "usuario", "texto": "Apágale   el revisor\na la copia, por favor"}]})
    h = asistente._encabezados_de_vera()
    assert h["x-autor"] == "vera"
    assert unquote(h["x-pedido"]) == "Apágale el revisor a la copia, por favor"
    assert unquote(h["x-pedido-id"]) == "c-1:2", h
    h["x-pedido"].encode("ascii")  # un encabezado HTTP va en ASCII: las tildes van codificadas
    asistente.fijar_pedido("x" * 500)
    assert len(asistente.PEDIDO["texto"]) == 200
    # la voz fija el pedido con lo que va oyendo del turno
    assert "asistente.fijar_pedido(turno.escuchado" in inspect.getsource(voz)


def prueba_las_herramientas_de_versiones_estan_por_escrito_y_por_voz() -> None:
    nombres = {h["name"] for h in asistente.HERRAMIENTAS}
    nuevas = {"versiones_flujo", "comparar_versiones", "deshacer_cambio", "restaurar_version", "restaurar_version_que_funcionaba"}
    assert nuevas <= nombres
    assert nuevas <= set(asistente.GRUPOS["construir"])
    en_voz = {h["name"] for h in voz.herramientas_de_voz()}
    assert nuevas <= en_voz, nuevas - en_voz
    assert "comparar_versiones" in asistente.PARALELAS and "deshacer_cambio" not in asistente.PARALELAS
    assert "deshacer_cambio" in asistente.SISTEMA and "confirmado=true" in asistente.SISTEMA
    # «déjalo como estaba» carga las herramientas de construir
    assert "construir" in asistente._grupos_para("déjalo como estaba, deshaz lo que hiciste", {})


def prueba_deshacer_confirma_con_la_version_real_y_marca_el_flujo() -> None:
    falsa = ApiFalsa({("POST", "/api/flujos/copia/deshacer"): lambda c: {"ok": True, "confirmado": True, "version": 7, "restaurada": 4, "deshecha": 6, "flujo": "copia"}})
    with con_api(falsa) as ctx:
        r = asistente.ejecutar("deshacer_cambio", {"flujo": "copia"})
    assert falsa.llamadas == [("POST", "/api/flujos/copia/deshacer", {"pedido": True})], falsa.llamadas
    assert r["confirmado"] is True and r["version"] == 7 and r["restaurada"] == 4
    assert ("control", {"accion": "editor", "op": "recargar"}) in ctx.emitidos
    cambiados, propuestas = set(), []
    asistente.anotar_resultado("deshacer_cambio", {"id": "copia"}, r, cambiados, propuestas)
    assert cambiados == {"copia"}
    # solo el último guardado
    falsa2 = ApiFalsa({("POST", "/api/flujos/copia/deshacer"): {"ok": True, "confirmado": True, "version": 8, "restaurada": 6, "flujo": "copia"}})
    with con_api(falsa2):
        asistente.ejecutar("deshacer_cambio", {"id": "copia", "solo_ultimo_guardado": True})
    assert falsa2.llamadas[0][2] == {"pedido": False}


def prueba_sin_confirmado_no_hay_restauracion() -> None:
    falsa = ApiFalsa({("POST", "/api/flujos/f/restaurar"): {"error": "esa versión ya no es válida", "problemas": ["falta x"]}})
    with con_api(falsa) as ctx:
        r = asistente.ejecutar("restaurar_version", {"id": "f", "version": 3})
    assert r["confirmado"] is False and "no digas que restauraste" in r["nota"]
    assert r["problemas"] == ["falta x"]
    assert not ctx.emitidos, "sin cambio no se recarga el editor"
    cambiados = set()
    asistente.anotar_resultado("restaurar_version", {"id": "f"}, r, cambiados, [])
    assert not cambiados
    # el resumen al agotarse lo cuenta como lo que faltó, no como hecho
    texto = asistente.resumen_al_agotarse([("restaurar_version", {"id": "f", "version": 3}, r)], [], 30)
    assert "no se guardó" in texto and "Quedó hecho" not in texto


def prueba_volver_a_la_que_funcionaba() -> None:
    falsa = ApiFalsa({("GET", "/api/flujos/f/versiones/funcionaba"): {"version": 5, "corridas_bien": 3, "ultima_bien": 1, "actual": 9},
                      ("POST", "/api/flujos/f/restaurar"): lambda c: {"ok": True, "confirmado": True, "version": 10, "restaurada": c["version"], "flujo": {"id": "f"}}})
    with con_api(falsa):
        r = asistente.ejecutar("restaurar_version_que_funcionaba", {"id": "f"})
    assert r["confirmado"] is True and r["version"] == 10 and r["restaurada"] == 5 and r["encontrada"]["corridas_bien"] == 3
    with con_api(falsa):
        solo = asistente.ejecutar("restaurar_version_que_funcionaba", {"id": "f", "restaurar": False})
    assert "confirmado" not in solo and solo["encontrada"]["version"] == 5
    ninguna = ApiFalsa({("GET", "/api/flujos/g/versiones/funcionaba"): {"version": None, "nota": "ninguna versión anterior tiene una corrida que haya terminado bien"}})
    with con_api(ninguna):
        r = asistente.ejecutar("restaurar_version_que_funcionaba", {"id": "g"})
    assert r["confirmado"] is False and [m for m, _, _ in ninguna.llamadas] == ["GET"], "sin versión buena no se restaura nada"


def prueba_listar_y_comparar() -> None:
    lista = [{"version": 3, "autor": "vera", "fecha": 1762880000000, "resumen": "Apagó «Revisor»", "pedido": "apaga el revisor", "actual": True, "corridas": {"bien": 0, "revisar": 0, "fallo": 0}},
             {"version": 2, "autor": "persona", "fecha": 1762870000000, "punto": "antes de: apaga el revisor", "actual": False, "corridas": {"bien": 2, "revisar": 0, "fallo": 0}}]
    falsa = ApiFalsa({("GET", "/api/flujos/f/versiones/comparar"): {"a": 2, "b": 3, "resumen": "Apagó «Revisor»"}, ("GET", "/api/flujos/f/versiones"): lista})
    with con_api(falsa):
        v = asistente.ejecutar("versiones_flujo", {"id": "f"})
        c = asistente.ejecutar("comparar_versiones", {"id": "f", "a": 2})
    assert v["total"] == 2 and v["versiones"][1]["punto"] == "antes de: apaga el revisor" and v["versiones"][1]["corridas"] == {"bien": 2}
    assert v["versiones"][0]["autor"] == "vera" and v["versiones"][0]["actual"] is True
    assert falsa.llamadas[-1][1] == "/api/flujos/f/versiones/comparar?a=2" and c["b"] == 3


if __name__ == "__main__":
    pruebas = [(n, f) for n, f in sorted(globals().items()) if n.startswith("prueba_") and callable(f)]
    for n, f in pruebas:
        f()
        print(f"ok  {n}")
    print(f"{len(pruebas)} pruebas, todas bien")
