"""
La voz de Vera tiene lo mismo que el chat escrito: todas sus herramientas (salvo las excluidas con
motivo), las mismas reglas y las propuestas de borrado con su botón. Sin red ni IA.

    docker exec -w /app/puente positiva-ia-positiva-ia-1 python3 pruebas_voz.py
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asistente  # noqa: E402
import voz  # noqa: E402


def prueba_la_voz_tiene_todas_las_herramientas_del_chat() -> None:
    """Un solo registro (vera_herramientas): lo que está en un modo y no en el otro está declarado, con su motivo."""
    import vera_herramientas as vh
    en_chat = {h["name"] for h in vh.de_modo("chat")}
    en_voz = {h["name"] for h in voz.herramientas_de_voz()}
    assert voz.herramientas_de_voz() == vh.de_modo("voz"), "la voz declara las del registro único"
    assert {h["name"] for h in asistente.HERRAMIENTAS} <= en_chat, "todas las de asistente.HERRAMIENTAS (también las que se agreguen) están en el registro"
    faltan = sorted(en_chat - en_voz - set(vh.SOLO_CHAT))
    assert not faltan, f"herramientas del chat que la voz no tiene y no están excluidas con motivo: {faltan}"
    sobran = sorted(en_voz - en_chat - set(vh.SOLO_VOZ))
    assert not sobran, f"herramientas de la voz que el chat no tiene y no están declaradas como solo de voz: {sobran}"
    for nombre, motivo in {**vh.SOLO_CHAT, **vh.SOLO_VOZ}.items():
        assert nombre in {h["name"] for h in vh.registro()}, f"{nombre} está excluida pero no existe"
        assert isinstance(motivo, str) and len(motivo) > 15, f"{nombre}: la exclusión necesita su motivo"
    assert voz.EXCLUIDAS_DE_VOZ is vh.SOLO_CHAT and voz.LARGAS is vh.LARGAS
    assert {"proponer_borrado", "borrar_flujo", "editar_nodo", "agregar_nodo", "eliminar_nodo", "editar_aristas", "ver_estructura", "estado_tareas"} <= en_voz
    assert "estado_tareas" in en_chat and "estado_tareas" in asistente.GRUPOS["nucleo"]
    assert {"escribir_en_chat", "responder_por_escrito", "terminar_llamada"} <= en_voz
    for modo in ("chat", "voz"):
        nombres = [h["name"] for h in vh.de_modo(modo)]
        assert len(nombres) == len(set(nombres)), f"una herramienta declarada dos veces en {modo}"
    # el chat escrito carga por grupos (para no saturar), pero del mismo registro: con todos los grupos, todas
    sin_grupo = sorted(en_chat - {n for g in asistente.GRUPOS.values() for n in g})
    assert not sin_grupo, f"herramientas del chat sin grupo (el chat escrito nunca las cargaría): {sin_grupo}"
    assert {h["name"] for h in asistente.herramientas_de(list(asistente.GRUPOS))} == en_chat


def prueba_chat_y_voz_usan_el_mismo_ejecutor() -> None:
    """Una sola forma de ejecutar: el chat escrito (Vertex y otros proveedores) y la voz pasan por vera_herramientas.Ejecutor."""
    llamada = inspect.getsource(voz.llamada)
    assert "vh.Ejecutor(\"voz\"" in llamada and "ejecutor.vuelta" in llamada
    assert "asistente.ejecutar(" not in llamada, "la voz no ejecuta herramientas por su cuenta"
    assert "asyncio.gather(*(herramienta" not in llamada
    conversar = inspect.getsource(asistente.conversar) + inspect.getsource(asistente._conversar_con_proveedor)
    assert conversar.count("_ejecutor_de(") == 2 and "ej.vuelta(" in conversar
    assert "verificar_respuesta" in conversar and "verificar_respuesta" in llamada, "la veracidad se revisa en los dos modos"


def prueba_la_voz_usa_las_reglas_del_escrito() -> None:
    fuente = inspect.getsource(voz.principal)
    assert "asistente.SISTEMA + NOTA_VOZ" in fuente, "la voz arma su sistema con el SISTEMA del chat escrito"
    for regla in ("se radicaría", "se radicó en el SGDEA", "equipo técnico", "modelos_ia", "No contradigas", "proponer_borrado", "Nunca digas «no puedo borrar»"):
        assert regla in asistente.SISTEMA, f"falta en SISTEMA: {regla}"
    assert "estructura fija" not in asistente.SISTEMA
    assert "proponer_borrado" in voz.NOTA_VOZ and "botón" in voz.NOTA_VOZ


def prueba_por_voz_el_borrado_queda_como_propuesta_con_boton() -> None:
    """La ruta de la voz: la herramienta corre como en el chat, emite la propuesta y el turno la guarda."""
    emitidos = []
    api_real, emitir_real, enviar_real = asistente.api, asistente.emitir, voz.enviar
    asistente.api = lambda m, r, b=None: ({"id": "copia-pqrd", "nombre": "Copia de PQRD", "tipo": "codigo", "borrable": True}
                                          if r == "/api/flujos/copia-pqrd" else {"id": "pqrd", "nombre": "PQRD", "tipo": "codigo", "borrable": False})
    voz.enviar = lambda tipo, **d: emitidos.append((tipo, d))
    asistente.emitir = voz._emitir_en_llamada
    try:
        turno = voz.Turno()
        res = asistente.ejecutar("borrar_flujo", {"id": "copia-pqrd"})
        voz.anotar_en_turno(turno, "borrar_flujo", {"id": "copia-pqrd"}, res)
        assert res.get("propuesto") is True and "botón" in res.get("nota", ""), res
        assert ("propuesta", {"propuesta": {"accion": "borrar_flujo", "id": "copia-pqrd", "etiqueta": "Borrar «Copia de PQRD»", "descripcion": ""}}) in emitidos, emitidos
        assert turno.propuestas == [{"accion": "borrar_flujo", "id": "copia-pqrd"}]
        orig = asistente.ejecutar("proponer_borrado", {"id": "pqrd"})
        assert "original" in orig.get("error", "") and "copias" in orig.get("error", ""), orig
    finally:
        asistente.api, asistente.emitir, voz.enviar = api_real, emitir_real, enviar_real


def prueba_las_operaciones_entre_flujos_estan_en_chat_y_voz() -> None:
    """Cruzar flujos, diagnóstico y lo de la pantalla que faltaba: en el chat, en la voz y descritas en el SISTEMA."""
    nuevas = {"comparar_flujos", "traer_nodos", "encadenar_flujos", "diagnosticar_flujo", "ver_programacion", "programar_flujo",
              "ver_recursos", "ajustar_recursos", "archivos_del_flujo", "reiniciar_trabajador"}
    en_chat = {h["name"] for h in asistente.HERRAMIENTAS}
    en_voz = {h["name"] for h in voz.herramientas_de_voz()}
    assert nuevas <= en_chat, nuevas - en_chat
    assert nuevas <= en_voz, nuevas - en_voz
    en_grupos = {n for g in asistente.GRUPOS.values() for n in g}
    assert nuevas <= en_grupos, f"sin grupo (el chat escrito no las cargaría): {nuevas - en_grupos}"
    for x in ("comparar_flujos", "traer_nodos", "encadenar_flujos", "subflujo", "diagnosticar_flujo", "ids=[…]", "Capacidades", "Lo que NO haces"):
        assert x in asistente.SISTEMA, f"falta en SISTEMA: {x}"
    for x in ("comparar_flujos", "diagnosticar_flujo", "capacidades", "ids=[…]"):
        assert x in voz.NOTA_VOZ, f"falta en NOTA_VOZ: {x}"
    # «diagnostica / qué puedes solucionar» y «compara / trae» cargan las herramientas en el chat escrito
    for pedido in ("¿qué encuentras que puedas solucionar en este flujo?", "compara la copia con el original", "trae el paso de validación del otro flujo"):
        assert "construir" in asistente._grupos_para(pedido, {"vista": "tablero"}), pedido


def prueba_no_habla_al_conectar() -> None:
    """Pedido 2026-11-11: Vera no saluda al conectar; solo responde cuando la persona le habla."""
    fuente = inspect.getsource(voz.llamada)
    arranque = fuente[fuente.index("tareas = [asyncio.create_task"):fuente.index("await colgar.wait()")]
    assert "send_client_content" not in arranque and "send_realtime_input" not in arranque, "al conectar no se le manda nada a Gemini Live"
    assert "Saluda tú primero" not in fuente and "franja" not in fuente, "no queda el saludo según la hora"
    assert "No hablas al conectar" in voz.NOTA_VOZ and "saludas primero" not in voz.NOTA_VOZ
    assert "habla Vera" not in fuente, "nunca dice su nombre (regla de Jonathan)"


def prueba_la_deteccion_de_voz_de_gemini() -> None:
    """La segunda puerta (Gemini Live): valores documentados en PLAN 5m y usados en la configuración."""
    from google.genai import types
    assert voz.VAD == {"inicio": "START_SENSITIVITY_LOW", "fin": "END_SENSITIVITY_LOW", "prefix_padding_ms": 300, "silence_duration_ms": 700}
    types.StartSensitivity(voz.VAD["inicio"]), types.EndSensitivity(voz.VAD["fin"])  # nombres válidos
    fuente = inspect.getsource(voz.principal)
    assert 'VAD["silence_duration_ms"]' in fuente and 'VAD["prefix_padding_ms"]' in fuente
    assert "TURN_INCLUDES_ONLY_ACTIVITY" in fuente and "START_OF_ACTIVITY_INTERRUPTS" in fuente


def prueba_la_pantalla_no_corta_lo_que_dice_vera() -> None:
    """El aviso de cambio de pantalla no se manda en cuanto llega (interrumpía la respuesta): espera una pausa en el reloj."""
    fuente = inspect.getsource(voz.llamada)
    subida = fuente[fuente.index("async def subida"):fuente.index("def _a_la_llamada")]
    reloj = fuente[fuente.index("async def reloj"):]
    assert "cambió de pantalla" not in subida, "la subida no debe mandar el aviso de pantalla en caliente"
    assert "cambió de pantalla" in reloj and "en_pausa" in reloj
    # se guarda con una pausa corta: la transcripción tardía queda en el mismo turno
    assert "programar_guardado()" in fuente and "guardar_pendiente()" in fuente


def prueba_las_transcripciones_se_pegan_con_espacio() -> None:
    u = voz.unir_transcripcion
    assert u("Listo, ya", "Tienes el flujo") == "Listo, ya Tienes el flujo"
    assert u("es de la Policía Nacional.", "Ya puse a correr") == "es de la Policía Nacional. Ya puse a correr"
    assert u("Te escu", "cho") == "Te escucho"
    assert u("Hola ", "Vera") == "Hola Vera"
    assert u("son 3.", "5 millones") == "son 3.5 millones"
    assert u("listo", ".") == "listo."
    assert u("", "Hola") == "Hola"
    assert u("Bien,", "¿y tú?") == "Bien, ¿y tú?"


def prueba_borrar_varios_es_una_propuesta_con_un_boton() -> None:
    emitidos = []
    flujos = {"copia-a": {"id": "copia-a", "nombre": "Copia A", "borrable": True}, "copia-b": {"id": "copia-b", "nombre": "Copia B", "borrable": True},
              "pqrd": {"id": "pqrd", "nombre": "PQRD", "borrable": False}}
    api_real, emitir_real, enviar_real = asistente.api, asistente.emitir, voz.enviar
    asistente.api = lambda m, r, b=None: flujos.get(r.rsplit("/", 1)[-1], {"error": "ese flujo no existe"})
    voz.enviar = lambda tipo, **d: emitidos.append((tipo, d))
    asistente.emitir = voz._emitir_en_llamada
    try:
        turno = voz.Turno()
        args = {"ids": ["copia-a", "copia-b", "pqrd", "no-hay"]}
        res = asistente.ejecutar("proponer_borrado", args)
        voz.anotar_en_turno(turno, "proponer_borrado", args, res)
        assert res.get("propuesto") is True and res["ids"] == ["copia-a", "copia-b"], res
        assert {x["id"] for x in res["fuera"]} == {"pqrd", "no-hay"}, res
        prop = [d["propuesta"] for t, d in emitidos if t == "propuesta"]
        assert len(prop) == 1 and prop[0]["accion"] == "borrar_flujos" and prop[0]["id"] == "copia-a,copia-b", prop
        assert turno.propuestas == [{"accion": "borrar_flujos", "id": "copia-a,copia-b"}]
        # uno solo sigue siendo la propuesta de siempre
        turno2 = voz.Turno()
        r1 = asistente.ejecutar("borrar_flujo", {"ids": ["copia-a"]})
        voz.anotar_en_turno(turno2, "borrar_flujo", {"ids": ["copia-a"]}, r1)
        assert turno2.propuestas == [{"accion": "borrar_flujo", "id": "copia-a"}], turno2.propuestas
        assert "error" in asistente.ejecutar("proponer_borrado", {})
    finally:
        asistente.api, asistente.emitir, voz.enviar = api_real, emitir_real, enviar_real


def prueba_al_agotar_las_vueltas_dice_que_quedo_hecho() -> None:
    """La conversación real del 2026-11-09 (16 herramientas, 6 versiones guardadas): nada de «Me detuve tras varios pasos»."""
    assert asistente.MAX_VUELTAS >= 28
    assert "Me detuve tras varios pasos" not in inspect.getsource(asistente)
    f = "pqrd-revision-adjuntos"
    registro = [
        ("ver_estructura", {"flujo": f}, {"flujo": f}),
        ("agregar_nodo", {"flujo": f, "nodo": {"id": "hay_no_leidos"}}, {"guardado": False, "problemas": ["hay_no_leidos: no se llega a él"]}),
        ("agregar_nodo", {"flujo": f, "nodo": {"id": "hay_no_leidos"}, "entre": ["conversion_a_markdown", "saneamiento"]}, {"guardado": True, "version": 1}),
        ("agregar_nodo", {"flujo": f, "nodo": {"id": "nota_adjuntos"}}, {"guardado": True, "version": 2}),
        ("agregar_nodo", {"flujo": f, "nodo": {"id": "revision_sin_documento"}}, {"guardado": False, "problemas": ["revision_sin_documento: no se llega a él desde ninguna etapa"]}),
    ]
    t = asistente.resumen_al_agotarse(registro, [], 30)
    assert "versión 1" in t and "versión 2" in t and "hay_no_leidos" in t, t
    assert "Faltó" in t and "revision_sin_documento" in t, t
    assert "Me detuve" not in t
    # lo que falló y después se arregló no queda como pendiente
    t2 = asistente.resumen_al_agotarse(registro[:3], [], 30)
    assert "Faltó" not in t2, t2
    # sin nada guardado lo dice
    assert "ningún cambio" in asistente.resumen_al_agotarse(registro[:2], [], 30)


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
