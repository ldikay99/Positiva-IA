"""
Lo que el servidor le pide a la capa de proveedores (una orden JSON por stdin, la
respuesta JSON en la última línea de stdout). La llave llega solo por stdin y
nunca sale: toda respuesta pasa por la máscara.

    python3 puente/proveedores/cli.py catalogo
    python3 puente/proveedores/cli.py modelos   {proveedor, credencial: {valores}}
    python3 puente/proveedores/cli.py probar    {proveedor, modelo, credencial: {valores}}
    python3 puente/proveedores/cli.py validar   {definicion, credenciales: {nombre: {tipo, alcance}}, ia_global}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# La carpeta de este archivo no va en el path: sus módulos no deben tapar bibliotecas de verdad.
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import proveedores  # noqa: E402
from proveedores.base import enmascarar, secretos_de  # noqa: E402


def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    cmd = sys.argv[1] if len(sys.argv) > 1 else "catalogo"
    orden = {} if cmd == "catalogo" else json.loads(sys.stdin.read().lstrip("﻿") or "{}")
    cred = (orden.get("credencial") or {})
    cred = cred.get("valores", cred) if isinstance(cred, dict) else {}
    secretos = secretos_de(cred)
    try:
        if cmd == "catalogo":
            r = proveedores.catalogo.todo()
        elif cmd == "modelos":
            r = {"ok": True, "modelos": proveedores.catalogo.en_vivo(orden["proveedor"], cred, "catalogo", float(orden.get("timeout") or 20))}
        elif cmd == "probar":
            r = proveedores.probar(orden["proveedor"], orden["modelo"], cred, float(orden.get("timeout") or 60))
        elif cmd == "validar":
            r = {"problemas": proveedores.problemas_ia(orden.get("definicion") or {}, orden.get("credenciales"), orden.get("ia_global"))}
        else:
            r = {"ok": False, "error": f"orden desconocida {cmd}"}
    except Exception as e:  # noqa: BLE001 - el error es la respuesta
        r = {"ok": False, "error": f"{e}"[:500], "status": getattr(e, "status_code", None)}
    print(enmascarar(json.dumps(r, ensure_ascii=False, default=str), secretos))


if __name__ == "__main__":
    principal()
