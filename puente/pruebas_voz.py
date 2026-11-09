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
    en_voz = {h["name"] for h in voz.herramientas_de_voz()}
    faltan = [h["name"] for h in asistente.HERRAMIENTAS if h["name"] not in en_voz and h["name"] not in voz.EXCLUIDAS_DE_VOZ]
    assert not faltan, f"herramientas del chat que la voz no tiene y no están excluidas con motivo: {faltan}"
    for nombre, motivo in voz.EXCLUIDAS_DE_VOZ.items():
        assert nombre in {h["name"] for h in asistente.HERRAMIENTAS}, f"{nombre} está excluida pero no existe en el chat"
        assert isinstance(motivo, str) and len(motivo) > 15, f"{nombre}: la exclusión necesita su motivo"
    assert {"proponer_borrado", "borrar_flujo", "editar_nodo", "agregar_nodo", "eliminar_nodo", "editar_aristas", "ver_estructura"} <= en_voz
    assert {"escribir_en_chat", "responder_por_escrito", "terminar_llamada"} <= en_voz
    nombres = [h["name"] for h in voz.herramientas_de_voz()]
    assert len(nombres) == len(set(nombres)), "una herramienta declarada dos veces"


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
