"""
Prueba una regla de búsqueda con el motor de expresiones de Python, el mismo
que usa el pipeline. Lee {"patron", "flags", "texto"} por stdin y devuelve las
coincidencias por stdout.
"""
import json
import re
import sys

MAXIMO = 200


def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    orden = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    flags = 0
    for nombre in str(orden.get("flags") or "").split("|"):
        nombre = nombre.strip()
        if nombre:
            flags |= getattr(re, nombre)
    try:
        patron = re.compile(orden["patron"], flags)
    except re.error as e:
        print(json.dumps({"ok": False, "error": f"{e.msg} (posición {e.pos})"}, ensure_ascii=False))
        return
    texto = str(orden.get("texto") or "")
    coincidencias = []
    for m in patron.finditer(texto):
        coincidencias.append({"inicio": m.start(), "fin": m.end(), "texto": m.group(0)[:300],
                              "grupos": [g if g is None else g[:200] for g in m.groups()]})
        if len(coincidencias) >= MAXIMO:
            break
    print(json.dumps({"ok": True, "coincidencias": coincidencias, "grupos": patron.groups},
                     ensure_ascii=False))


if __name__ == "__main__":
    principal()
