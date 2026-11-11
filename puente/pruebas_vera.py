"""
Vera con una sola ejecución para el chat y la voz, seguimiento de tareas y sin afirmar lo no confirmado.
Sin red ni IA: la API de la plataforma es un doble.

    docker exec -w /app/puente positiva-ia-positiva-ia-1 python3 pruebas_vera.py
"""
from __future__ import annotations

import inspect
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asistente  # noqa: E402
import procedencia  # noqa: E402
import vera_herramientas as vh  # noqa: E402


class Doble:
    """Reemplaza asistente.ejecutar y asistente.api mientras dura la prueba."""

    def __init__(self, ejecutar=None, api=None):
        self.ejecutar, self.api = ejecutar, api
        self.puestos = []

    def __enter__(self):
        self._e, self._a, self._c = asistente.ejecutar, asistente.api, dict(asistente.CONVERSACION)
        if self.ejecutar:
            asistente.ejecutar = self.ejecutar
        if self.api:
            asistente.api = self.api
        return self

    def __exit__(self, *_):
        asistente.ejecutar, asistente.api = self._e, self._a
        asistente.CONVERSACION.clear()
        asistente.CONVERSACION.update(self._c)


# ------------------------------------------------------------------ ejecutor
def prueba_situacion_de_cada_resultado() -> None:
    s = vh.situacion
    assert s("guardar_flujo", {"guardado": True, "version": 3}) == "confirmado"
    assert s("editar_nodo", {"guardado": False, "problemas": ["x"]}) == "fallo"
    assert s("enfocar_flujo", {"ok": True, "confirmado": False}) == "no_confirmado"
    assert s("enfocar_flujo", {"ok": True, "confirmado": None}) == "no_confirmado"
    assert s("enfocar_flujo", {"ok": True, "confirmado": True}) == "confirmado"
    assert s("proponer_borrado", {"propuesto": True}) == "propuesto"
    assert s("correr_correos", {"creadas": [{"id": "a"}]}) == "en_curso"
    assert s("correr_flujo", {"corrida": "a", "nota": "Está corriendo"}) == "en_curso"
    assert s("correr_flujo", {"id": "a", "desenlace": "completar"}) == "confirmado"
    assert s("x", {"error": "no"}) == "fallo"
    assert s("listar_flujos", [{"id": "a"}]) == "confirmado"


def prueba_la_vuelta_marca_y_avisa_con_indice() -> None:
    eventos = []
    with Doble(ejecutar=lambda n, a: {"ok": True, "n": n} if n != "editar_nodo" else {"guardado": False, "problemas": ["no"]}):
        ej = vh.Ejecutor("chat", lambda t, **d: eventos.append((t, d)))
        r = ej.vuelta([("leer_flujo", {"id": "x"}), ("ver_corrida", {"id": "c"}), ("editar_nodo", {"flujo": "x", "nodo": "n", "cambios": {}})])
        ej.cerrar()
    assert [x["situacion"] for x in r] == ["confirmado", "confirmado", "fallo"], r
    assert "nota_situacion" in r[2] and "No se hizo" in r[2]["nota_situacion"]
    res = [d for t, d in eventos if t == "resultado_herramienta"]
    assert sorted(d["indice"] for d in res) == [0, 1, 2] and all("situacion" in d and "cifras" in d for d in res)
    assert [d.get("paralelo") for t, d in eventos if t == "herramienta"] == [2, 2, None], "las de lectura a la vez, la que cambia sola"
    assert [p["nombre"] for p in ej.pasos] == ["leer_flujo", "ver_corrida", "editar_nodo"]


def prueba_lo_largo_termina_en_linea_si_alcanza() -> None:
    """Chat: una tarea larga que termina dentro de la espera devuelve su resultado en la misma respuesta."""
    with Doble(ejecutar=lambda n, a: (time.sleep(0.2), {"id": "c1", "desenlace": "completar"})[1],
               api=lambda m, r, b=None: {"tareas": []} if "tareas" in r else {}):
        ej = vh.Ejecutor("chat", lambda t, **d: None, espera_en_linea=3, conversacion="conv-prueba-1")
        r = ej.una("correr_flujo", {"id": "f", "esperar": True})
        ej.cerrar()
    assert r.get("desenlace") == "completar" and r["situacion"] == "confirmado", r
    assert not ej.en_curso()


def prueba_lo_largo_pasa_a_segundo_plano_y_se_reporta() -> None:
    """Chat y voz: lo que no alcanza a terminar responde «en_curso», se registra en el servidor y llega al terminar."""
    puestos, liberar = [], threading.Event()

    def api(m, r, b=None):
        if m == "PUT" and r.endswith("/tareas"):
            puestos.append(dict(b))
            return {"ok": True}
        if r.endswith("/tareas") or "/tareas?" in r:
            return {"tareas": []}
        return {}

    def lento(n, a):
        liberar.wait(5)
        return {"id": "c9", "desenlace": "completar", "campos": {"total": 41}}

    terminadas = []
    with Doble(ejecutar=lento, api=api):
        for modo, espera in (("chat", 0.3), ("voz", 0)):
            puestos.clear()
            liberar.clear()
            eventos = []
            ej = vh.Ejecutor(modo, lambda t, **d: eventos.append((t, d)), espera_en_linea=espera, conversacion="conv-prueba-2",
                             al_terminar_tarea=terminadas.append)
            r = ej.una("probar_flujo", {"flujo": "f", "caso": "c1"})
            assert r["situacion"] == "en_curso" and r["tarea"] == 1 and "No digas que terminó" in r["nota"], r
            # la misma tarea otra vez no se lanza
            dup = ej.una("probar_flujo", {"flujo": "f", "caso": "c1"})
            assert dup.get("estado") == "en_curso" and "no la repitas" in dup["nota"], dup
            assert len(ej.en_curso()) == 1
            assert puestos and puestos[-1]["estado"] == "en_curso" and puestos[-1]["etiqueta"].startswith("Probando el flujo")
            liberar.set()
            t = ej.esperar_siguiente(5)
            assert t is not None and t.resultado["desenlace"] == "completar" and t.estado == "lista"
            time.sleep(0.2)
            assert puestos[-1]["estado"] == "lista" and "completar" in puestos[-1]["resultado"]
            assert ("tarea_iniciada" in [x for x, _ in eventos]) and ("tarea_lista" in [x for x, _ in eventos])
            assert any(n == "probar_flujo" and isinstance(res, dict) and res.get("desenlace") == "completar" for n, _, res in ej.registro)
            ej.cerrar()
    assert len(terminadas) == 2


def prueba_las_corridas_lanzadas_se_siguen() -> None:
    """correr_correos no espera: el ejecutor sigue sus corridas como tarea y su resultado llega al terminar."""
    corridas = {"r1": {"id": "r1", "estado": "procesando"}}

    def api(m, r, b=None):
        if "/tareas" in r:
            return {"tareas": []} if m == "GET" else {"ok": True}
        if r.startswith("/api/corridas/r1"):
            return corridas["r1"]
        return {}

    with Doble(ejecutar=lambda n, a: {"creadas": [{"id": "r1", "caso": "c1"}], "nota": "Están corriendo"}, api=api):
        ej = vh.Ejecutor("chat", lambda t, **d: None, espera_en_linea=0.2, conversacion="conv-prueba-3")
        r = ej.una("correr_correos", {"casos": ["c1"], "flujo": "pqrd"})
        assert r["situacion"] == "en_curso" and r.get("tarea") == 1, r
        corridas["r1"] = {"id": "r1", "estado": "terminada", "resultado": {"accion": "manual", "motivo": "SIN_DOCUMENTO"}, "eventos": []}
        t = ej.esperar_siguiente(10)
        ej.cerrar()
    assert t and t.nombre == "seguir_corridas" and t.resultado["corridas"][0]["desenlace"] == "manual", t and t.resultado


def prueba_estado_tareas_junta_las_del_servidor() -> None:
    servidor = [{"id": "v1-1", "n": 1, "etiqueta": "Diagnosticando pqrd", "estado": "en_curso", "origen": "voz", "desde": 1},
                {"id": "c1-2", "n": 2, "etiqueta": "Probando el flujo", "estado": "lista", "origen": "chat", "desde": 2, "resultado": '{"desenlace": "radicado"}'}]
    with Doble(api=lambda m, r, b=None: {"tareas": servidor} if "/tareas" in r else {}):
        ej = vh.Ejecutor("chat", lambda t, **d: None, conversacion="conv-prueba-4")
        r = ej.una("estado_tareas", {})
        una = ej.una("estado_tareas", {"tarea": "2"})
        ej.cerrar()
    assert [x["id"] for x in r["en_curso"]] == ["v1-1"] and r["terminadas"][0]["id"] == "c1-2", r
    assert "radicado" in una["resultado"], una


def prueba_la_voz_ejecuta_sus_propias_por_el_mismo_ejecutor() -> None:
    hechos = []
    with Doble(ejecutar=lambda n, a: {"ok": True}):
        ej = vh.Ejecutor("voz", lambda t, **d: None, espera_en_linea=0, manejadores={"escribir_en_chat": lambda a: hechos.append(a) or {"ok": True}})
        r = ej.vuelta([("escribir_en_chat", {"texto": "hola"}), ("listar_flujos", {})])
        ej.cerrar()
    assert hechos == [{"texto": "hola"}] and r[0]["situacion"] == "confirmado"
    assert ej.lanzadas == [("listar_flujos", {})], "lo propio de la voz no cuenta como «ya lanzado» para el proceso escrito"
    # una de la voz en el chat no existe
    ej2 = vh.Ejecutor("chat", lambda t, **d: None)
    assert "solo existe en la llamada" in ej2.una("terminar_llamada", {})["error"]
    ej2.cerrar()


# ------------------------------------------------------------------ veracidad
def _r(nombre, res):
    return (nombre, {}, vh.marcar(nombre, res))


def prueba_no_afirma_lo_no_confirmado() -> None:
    v = vh.verificar_respuesta
    # «guardé» sin nada que cambie
    assert v("Listo, guardé los cambios en el flujo.", [_r("leer_flujo", {"id": "x"})])
    assert not v("Listo, guardé los cambios: versión 4.", [_r("editar_nodo", {"guardado": True, "version": 4})])
    # el guardado falló
    p = v("Ya quedó guardado el flujo.", [_r("editar_nodo", {"guardado": False, "problemas": ["x"]})])
    assert p and "editar_nodo" in p[0], p
    # pantalla no confirmada
    assert v("Te abrí el flujo, ya lo tienes en pantalla.", [_r("enfocar_flujo", {"ok": True, "confirmado": False})])
    assert not v("Te abrí el flujo.", [_r("enfocar_flujo", {"ok": True, "confirmado": True})])
    # «corrí» con la corrida en curso
    p = v("Corrí el flujo y terminó en manual.", [_r("correr_flujo", {"corrida": "a"})], en_curso=1)
    assert any("en curso" in x for x in p), p
    # en curso sin decirlo
    assert v("Revisé el flujo.", [_r("leer_flujo", {}), ("probar_flujo", {}, {"estado": "en_curso", "situacion": "en_curso"})], en_curso=1)
    assert not v("Lo estoy probando; te aviso cuando termine.", [("probar_flujo", {}, {"estado": "en_curso", "situacion": "en_curso"})], en_curso=1)
    # se radicó en modo prueba
    assert v("El correo se radicó en el SGDEA.", [_r("probar_flujo", {"desenlace": "radicado"})])
    assert not v("El correo quedó listo para radicar (en modo prueba): se radicaría.", [_r("probar_flujo", {"desenlace": "radicado"})])
    # borrar es una propuesta
    assert "botón" in v("Borré la copia.", [_r("proponer_borrado", {"propuesto": True})])[0]
    # documento
    assert v("Aquí tienes el informe en PDF.", [_r("generar_documento", {"error": "No generé el documento"})])
    assert not v("Aquí tienes el informe en PDF.", [_r("generar_documento", {"ok": True, "archivo": "a.pdf"})])
    # subjuntivo y negación no son afirmaciones; tampoco lo de un turno anterior
    assert not v("¿Quieres que lo guarde y lo corra?", [])
    assert not v("No guardé nada porque la validación falló.", [_r("validar_flujo", {"ok": False})])
    assert not v("En el mensaje anterior guardé la versión 3.", [])
    # «Lista» como muletilla de la voz
    assert not v("Lista, ¿qué hacemos?", [])


def prueba_tokens_de_memoria() -> None:
    """La evaluación «HEIC: costo sin inventar»: «~258 tokens por imagen» sin que ninguna herramienta lo diga se devuelve para corregir."""
    with Doble(api=lambda m, r, b=None: {"mensajes": []} if r.startswith("/api/chats/") else {}):
        asistente.CONVERSACION["id"] = "conv-tokens"
        p = vh.verificar_respuesta("Cada foto gasta ~258 tokens con gemini-2.5-flash.", [_r("modelos_ia", {"modelos": [{"id": "gemini-2.5-flash", "precio_entrada": 0.3}]})])
        assert p and "258 tokens" in p[0], p
        reg = [_r("ver_ia_flujo", {"consumo": {"tokens_entrada": 2250447, "tokens_salida": 43907}})]
        assert not vh.verificar_respuesta("Lleva 2,25 millones de tokens de entrada y 43907 tokens de salida.", reg)
        assert vh.verificar_respuesta("Lleva 3,1 millones de tokens de entrada.", reg)


def prueba_el_chat_corrige_antes_de_responder() -> None:
    """El chat escrito: si la respuesta afirma lo no confirmado, se le devuelve al modelo (hasta 2 veces) antes de llegar a la persona."""
    fuente = inspect.getsource(asistente.conversar)
    i = fuente.index("if not llamadas:\n            texto = texto or")
    tramo = fuente[i:i + 900]
    assert "verificar_respuesta" in tramo and "pedido_de_correccion" in tramo and "correcciones < 2" in tramo and "continue" in tramo
    assert "revision_veracidad" in tramo
    assert "Revisión de veracidad" in vh.pedido_de_correccion(["x"]) or "revisión de veracidad" in vh.pedido_de_correccion(["x"])


# ------------------------------------------------------------------ cifras de un documento
INFORME_INVENTADO = """# Informe semanal de radicación
Esta semana (9 al 11 de noviembre de 2026) quedaron listos para radicar **36 correos**.
| Métrica | Esta semana | Semana pasada | Variación |
| Radicados | 36 | 41 | -12,2 % |
| Tiempo promedio | 30.1 s | 54.0 s | -44.3% |
| Costo IA | $0.33 USD | $0.37 USD | -10.8% |
Generado el 11/11/2026 a las 10:30."""


def prueba_las_cifras_del_documento() -> None:
    textos = [c["texto"] for c in procedencia.cifras_del_documento(INFORME_INVENTADO)]
    assert "2026" not in textos and "11" not in textos and "10" not in textos and "9" not in textos, textos  # fechas y horas no son cifras
    assert textos[:3] == ["36", "36", "41"] and "-12,2 %" in textos and "0.33" in textos, textos
    assert [c["texto"] for c in procedencia.cifras_del_documento("## 1. Resumen\n- 2) Detalle\nVer la semana 45 y la diapositiva 3.")] == []
    v = procedencia._valores
    assert v("1.267") == {1267.0, 1.267} and v("12,5") == {12.5} and v("0.009") == {0.009} and v("1.200.000") == {1200000.0}


def prueba_rechaza_cifras_sin_procedencia_y_acepta_las_consultadas() -> None:
    f = procedencia.Fuentes()
    f.agregar({"total": 36, "resumen": {"segundos_promedio": 29.8, "por_desenlace": {"radicado": 36}}}, "historial")
    f.agregar({"total": 41, "resumen": {"segundos_promedio": 55.8}}, "historial")
    r = procedencia.revisar(INFORME_INVENTADO, f)
    sin = [x["cifra"] for x in r["sin_procedencia"]]
    assert not r["ok"] and {"30.1", "54.0", "0.33", "0.37", "-44.3%"} <= set(sin) and "36" not in sin and "41" not in sin, sin
    # con las cifras reales y el cálculo declarado
    bueno = "Radicados: 36 esta semana y 41 la anterior (-12,2 %). Tiempo promedio: 29,8 s y 55.8 s."
    r2 = procedencia.revisar(bueno, f, [{"valor": "-12,2 %", "formula": "(36-41)/41*100"}])
    assert r2["ok"], r2
    # un cálculo que no da, o con números inventados, se rechaza
    r3 = procedencia.revisar("Variación: -15 %.", f, [{"valor": "-15 %", "formula": "(36-41)/41*100"}])
    assert not r3["ok"] and "da" in r3["problemas_calculos"][0], r3
    r4 = procedencia.revisar("Total: 99.", f, [{"valor": "99", "formula": "36+63"}])
    assert not r4["ok"] and "63" in r4["problemas_calculos"][0], r4
    # un conteo suelto no se «redondea» desde un tiempo con decimales
    f2 = procedencia.Fuentes()
    f2.agregar({"segundos": 8.7}, "historial")
    assert not procedencia.revisar("| 9 |", f2)["ok"]
    assert procedencia.revisar("unos 9 s", f2)["ok"]


def prueba_los_conteos_salen_de_las_listas() -> None:
    """«36 radicados» sale de contar los elementos de una lista con desenlace «radicado», aunque la herramienta no lo sume."""
    f = procedencia.Fuentes()
    f.agregar({"corridas": [{"desenlace": "radicado"}] * 7 + [{"desenlace": "manual"}] * 2}, "historial")
    assert procedencia.revisar("Radicados: 7. A revisión: 2. En total 9.", f)["ok"]
    assert not procedencia.revisar("Radicados: 8.", f)["ok"]


def prueba_generar_documento_rechaza_lo_inventado() -> None:
    """De punta a punta con un doble de la API: sin consultar, no se genera; con lo consultado en el turno, sí."""
    conv = {"mensajes": [{"rol": "usuario", "texto": "hazme un informe en PDF de cuántos correos se radicaron esta semana"}]}
    guardados = []

    def api(m, r, b=None):
        if r == "/api/chats/conv-doc":
            return conv
        if m == "POST" and r == "/api/chats/conv-doc/adjuntos":
            guardados.append(b["nombre"])
            return {"id": "a1", "nombre": b["nombre"], "tipo": b["tipo"], "tamano": 10}
        return {}

    pedido = {"formato": "md", "titulo": "Informe", "contenido": "# Informe\nSe radicaron 48 correos y 36 quedaron listos."}
    with Doble(api=api):
        asistente.CONVERSACION["id"] = "conv-doc"
        asistente.emitir, emitir = (lambda *a, **k: None), asistente.emitir
        try:
            r = asistente._generar_documento(dict(pedido))
            assert r.get("error", "").startswith("No generé el documento") and {x["cifra"] for x in r["sin_procedencia"]} == {"48", "36"}, r
            assert not guardados
            ej = vh.Ejecutor("chat", lambda t, **d: None)
            ej.registro.append(("historial", {"resumen": True}, {"total": 48, "resumen": {"por_desenlace": {"radicado": 36}}}))
            r2 = asistente._generar_documento(dict(pedido))
            ej.cerrar()
            assert r2.get("ok") and guardados == ["Informe.md"], r2
            # lo que la persona escribió también vale como fuente
            conv["mensajes"].append({"rol": "usuario", "texto": "pon que la meta era 50"})
            r3 = asistente._generar_documento({"formato": "md", "titulo": "Meta", "contenido": "La meta era 50."})
            assert r3.get("ok"), r3
        finally:
            asistente.emitir = emitir


def prueba_historial_resume_todo_el_periodo() -> None:
    lista = [{"id": f"x{i}", "flujo": "pqrd", "estado": "bien" if i % 4 else "revisar", "desenlace": "radicado" if i % 4 else "manual",
              "creada": 1794200000000 + i * 3600_000 * 7, "segundos": 10.0 + i} for i in range(450)]

    def api(m, r, b=None):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(r).query)
        salto, lim = int(q.get("desde_n", ["0"])[0]), int(q.get("limite", ["50"])[0])
        return {"total": len(lista), "corridas": lista[salto:salto + lim], "facetas": {}}

    with Doble(api=api):
        r = asistente._mover_pantalla("historial", {"flujo": "pqrd", "resumen": True, "agrupar": "dia", "n": 5})
    res = r["resumen"]
    assert len(r["corridas"]) == 5 and res["ejecuciones"] == 450 and res["por_desenlace"] == {"manual": 113, "radicado": 337}, res
    assert sum(d["ejecuciones"] for d in res["por_dia"]) == 450 and res["segundos_mediana"] == round(10 + 224.5, 1)


def prueba_el_proceso_del_chat_es_un_solo_modulo() -> None:
    """asistente.py corre como __main__: vera_herramientas tiene que recibir ese mismo módulo (con la conversación), no
    una copia vacía. Pasó en la primera prueba real: «no hay conversación donde dejar el documento»."""
    fuente = open(asistente.__file__, encoding="utf-8").read()
    i = fuente.index('sys.modules.setdefault("asistente", sys.modules[__name__])')
    assert i < fuente.index("def _ejecutor_de"), "antes de cualquier import de vera_herramientas"
    if "import vera_versiones" in fuente:
        assert i < fuente.index("import vera_versiones")


def prueba_completar_no_se_dice_radicado() -> None:
    """Un flujo declarativo que termina en «completar» se guarda como «radicado»: a Vera le llega «completado», sin la nota del SGDEA."""
    with Doble(api=lambda m, r, b=None: {"tipo": "declarativo"} if r == "/api/flujos/ev-x" else {"tipo": "codigo"}):
        asistente._TIPOS_DE_FLUJO.clear()
        r = asistente._resumen_corrida({"id": "c", "flujo": "ev-x", "resultado": {"accion": "radicado"}, "eventos": []})
        r2 = asistente._resumen_corrida({"id": "c", "flujo": "pqrd-copia", "resultado": {"accion": "radicado"}, "eventos": []})
        asistente._TIPOS_DE_FLUJO.clear()
    assert r["desenlace"] == "completado" and "modo_prueba" not in r, r
    assert r2["desenlace"] == "radicado" and "modo_prueba" in r2, r2


def principal() -> int:
    pruebas = [(n, f) for n, f in globals().items() if n.startswith("prueba_") and callable(f)]
    fallos = 0
    for nombre, f in pruebas:
        try:
            f()
            print(f"ok    {nombre}")
        except Exception as e:  # noqa: BLE001
            fallos += 1
            import traceback
            print(f"FALLA {nombre}: {type(e).__name__}: {e}")
            traceback.print_exc(limit=3)
    print(f"\n{len(pruebas) - fallos} de {len(pruebas)} pruebas pasaron")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
