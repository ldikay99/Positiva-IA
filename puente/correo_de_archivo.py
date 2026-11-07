"""
Un correo guardado como archivo (.eml o .msg de Outlook) convertido en un caso
de la bandeja de prueba de UN flujo: <carpeta>/<caso>/mensaje.json y adj/.

Lo usa el servidor cuando una persona sube correos de prueba a un flujo con
disparador entrada_correo. Nunca se mezcla con la bandeja descargada de PQRD.

    python correo_de_archivo.py <carpeta-de-la-bandeja> <nombre-del-archivo>   (stdin: los bytes)

Responde una línea JSON: {ok, caso, asunto, adjuntos} o {ok: false, error}.

- .eml: con la biblioteca estándar (email), cuerpo en texto o HTML y adjuntos.
- .msg: es un archivo compuesto de OLE (Outlook); se lee con olefile (BSD) y los
  flujos de propiedades MAPI: asunto, remitente, fecha, cuerpo y adjuntos.
"""
from __future__ import annotations

import datetime
import email
import email.policy
import email.utils
import hashlib
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_BYTES = 25 * 1024 * 1024


def _nombre_seguro(nombre: str, i: int) -> str:
    ext = re.sub(r"[^A-Za-z0-9]", "", nombre.rsplit(".", 1)[-1])[:8] if "." in nombre else "bin"
    return f"a{i}.{ext or 'bin'}"


def de_eml(datos: bytes) -> Dict[str, Any]:
    m = email.message_from_bytes(datos, policy=email.policy.default)
    nombre, direccion = email.utils.parseaddr(str(m.get("From") or ""))
    fecha = ""
    try:
        d = email.utils.parsedate_to_datetime(str(m.get("Date")))
        fecha = d.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z") if d else ""
    except (TypeError, ValueError):
        pass
    texto, html, adjuntos = "", "", []
    for parte in m.walk():
        if parte.is_multipart():
            continue
        disp = (parte.get_content_disposition() or "").lower()
        archivo = parte.get_filename()
        tipo = parte.get_content_type()
        if disp == "attachment" or archivo:
            datos_a = parte.get_payload(decode=True) or b""
            adjuntos.append({"nombre": archivo or f"adjunto.{tipo.split('/')[-1]}", "tipo": tipo, "datos": datos_a,
                             "en_linea": disp == "inline" and tipo.startswith("image/")})
        elif tipo == "text/plain" and not texto:
            texto = parte.get_content()
        elif tipo == "text/html" and not html:
            html = parte.get_content()
    return {"id": str(m.get("Message-ID") or ""), "asunto": str(m.get("Subject") or ""), "remitente_nombre": nombre,
            "remitente_correo": direccion, "para": str(m.get("To") or ""), "fecha": fecha,
            "cuerpo": html or texto, "html": bool(html), "adjuntos": adjuntos}


# ------------------------------------------------------------------- .msg

def _texto_prop(ole: Any, base: str, prop: str) -> str:
    """Una propiedad de texto: 001F (UTF-16) o 001E (8 bits)."""
    for tipo, cod in (("001F", "utf-16-le"), ("001E", "cp1252")):
        ruta = f"{base}__substg1.0_{prop}{tipo}"
        if ole.exists(ruta):
            return ole.openstream(ruta).read().decode(cod, errors="replace").rstrip("\x00")
    return ""


def _binario_prop(ole: Any, base: str, prop: str) -> bytes:
    ruta = f"{base}__substg1.0_{prop}0102"
    return ole.openstream(ruta).read() if ole.exists(ruta) else b""


def _fecha_msg(ole: Any) -> str:
    """PR_MESSAGE_DELIVERY_TIME (0x0E06) o PR_CLIENT_SUBMIT_TIME (0x0039) del flujo de propiedades (FILETIME)."""
    if not ole.exists("__properties_version1.0"):
        return ""
    crudo = ole.openstream("__properties_version1.0").read()
    hallado: Dict[int, int] = {}
    for i in range(32, len(crudo) - 15, 16):
        tipo, pid = struct.unpack("<HH", crudo[i:i + 4])
        if tipo == 0x0040 and pid in (0x0E06, 0x0039):
            hallado[pid] = struct.unpack("<Q", crudo[i + 8:i + 16])[0]
    ft = hallado.get(0x0E06) or hallado.get(0x0039)
    if not ft:
        return ""
    d = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(microseconds=ft // 10)
    return d.isoformat().replace("+00:00", "Z")


def de_msg(datos: bytes) -> Dict[str, Any]:
    import io

    import olefile

    ole = olefile.OleFileIO(io.BytesIO(datos))
    try:
        encabezados = _texto_prop(ole, "", "007D")
        mid = ""
        m = re.search(r"^Message-ID:\s*(.+)$", encabezados, re.I | re.M)
        if m:
            mid = m.group(1).strip()
        correo = _texto_prop(ole, "", "5D01") or _texto_prop(ole, "", "0C1F") or _texto_prop(ole, "", "0065")
        if "@" not in correo:
            m = re.search(r"^From:.*?<([^>]+)>", encabezados, re.I | re.M)
            correo = m.group(1) if m else correo
        html = _binario_prop(ole, "", "1013")
        cuerpo = html.decode("utf-8", errors="replace") if html else _texto_prop(ole, "", "1000")
        adjuntos = []
        for entrada in ole.listdir(streams=False, storages=True):
            raiz = entrada[0]
            if len(entrada) != 1 or not raiz.startswith("__attach_version1.0_"):
                continue
            base = f"{raiz}/"
            nombre = _texto_prop(ole, base, "3707") or _texto_prop(ole, base, "3704") or "adjunto"
            datos_a = _binario_prop(ole, base, "3701")
            tipo = _texto_prop(ole, base, "370E") or "application/octet-stream"
            if datos_a:
                adjuntos.append({"nombre": nombre, "tipo": tipo, "datos": datos_a, "en_linea": False})
        return {"id": mid, "asunto": _texto_prop(ole, "", "0037"), "remitente_nombre": _texto_prop(ole, "", "0C1A"),
                "remitente_correo": correo, "para": _texto_prop(ole, "", "0E04"), "fecha": _fecha_msg(ole),
                "cuerpo": cuerpo, "html": bool(html), "adjuntos": adjuntos}
    finally:
        ole.close()


def a_caso(datos: bytes, nombre: str, carpeta: Path) -> Dict[str, Any]:
    """Guarda el correo como caso de la bandeja de prueba y devuelve {caso, asunto, adjuntos}."""
    if len(datos) > MAX_BYTES:
        raise ValueError("el correo pasa de 25 MB")
    ext = nombre.lower().rsplit(".", 1)[-1] if "." in nombre else ""
    if ext == "msg" or datos[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        m = de_msg(datos)
    elif ext == "eml" or re.match(rb"^[\x20-\x7e]+:", datos[:200] or b""):
        m = de_eml(datos)
    else:
        raise ValueError("solo correos .eml o .msg")
    huella = hashlib.sha256(datos).hexdigest()[:16]
    caso = f"prueba-{huella}"
    dest = carpeta / caso
    (dest / "adj").mkdir(parents=True, exist_ok=True)
    adjuntos: List[Dict[str, Any]] = []
    for i, a in enumerate(m["adjuntos"], 1):
        archivo = _nombre_seguro(a["nombre"], i)
        (dest / "adj" / archivo).write_bytes(a["datos"])
        adjuntos.append({"name": a["nombre"], "archivo": archivo, "contentType": a["tipo"], "size": len(a["datos"]), "isInline": bool(a["en_linea"])})
    (dest / "mensaje.json").write_text(json.dumps({
        "id": m["id"] or f"prueba-{huella}", "subject": m["asunto"], "receivedDateTime": m["fecha"],
        "from": {"emailAddress": {"name": m["remitente_nombre"], "address": m["remitente_correo"]}},
        "toRecipients": [{"emailAddress": {"address": x.strip()}} for x in re.split(r"[;,]", m["para"] or "") if x.strip()],
        "body": {"contentType": "html" if m["html"] else "text", "content": m["cuerpo"] or ""},
        "_adjuntos": adjuntos, "_origen": "bandeja_de_prueba", "_archivo": nombre[:200]}, ensure_ascii=False), encoding="utf-8")
    return {"caso": caso, "asunto": m["asunto"], "adjuntos": len(adjuntos)}


def principal(argv: List[str]) -> Tuple[int, Optional[str]]:
    if len(argv) != 3:
        return 2, json.dumps({"ok": False, "error": "uso: correo_de_archivo.py <carpeta> <nombre>"})
    try:
        r = a_caso(sys.stdin.buffer.read(), argv[2], Path(argv[1]))
        return 0, json.dumps({"ok": True, **r}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 — la web muestra el motivo
        return 0, json.dumps({"ok": False, "error": f"no se pudo leer el correo: {e}"[:300]}, ensure_ascii=False)


if __name__ == "__main__":
    codigo, salida = principal(sys.argv)
    sys.stdout.write((salida or "") + "\n")
    sys.exit(codigo)
