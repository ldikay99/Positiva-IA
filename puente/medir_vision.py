"""
Experimento: ¿la visión del modelo lee mejor que tesseract las hojas flojas (60-80 %)?

Convierte cada adjunto dos veces con el motor real: tal como está (la visión
entra solo por debajo de CONFIANZA_REINTENTO_OSD) y con ese umbral en 0,80. Por
cada hoja guarda el texto de las dos lecturas para compararlas a mano.

    python medir_vision.py SALIDA.json CASO:ADJUNTO [CASO:ADJUNTO ...]
"""
import json
import sys
import time
from pathlib import Path

import buzon_vivo

buzon_vivo._arrancar_motor()

from pipeline import conversion  # noqa: E402
from trabajador import modelo_vertex  # noqa: E402

BANDEJA = Path("/datos/bandeja")
VIVO = Path("/datos/estado/vivo")


def adjunto(caso: str, nombre: str):
    for base in (VIVO, BANDEJA):
        m = base / caso / "mensaje.json"
        if m.exists():
            msg = json.loads(m.read_text(encoding="utf-8"))
            for a in msg.get("_adjuntos", []):
                if a.get("name") == nombre and a.get("archivo"):
                    return (base / caso / "adj" / a["archivo"]).read_bytes()
    return None


def convertir(datos: bytes, nombre: str):
    t = time.time()
    c = conversion.convertir(datos, nombre)
    return c.markdown, round(time.time() - t, 1)


def principal():
    salida = Path(sys.argv[1])
    m = modelo_vertex("gemini-2.5-flash", "global")
    resultados = []
    for par in sys.argv[2:]:
        caso, nombre = par.split(":", 1)
        datos = adjunto(caso, nombre)
        if not datos:
            print("no hallado", par)
            continue
        conversion.TRANSCRIPTOR_IA = m.transcribir_imagen
        umbral = conversion.CONFIANZA_REINTENTO_OSD
        base, t1 = convertir(datos, nombre)
        conversion.CONFIANZA_REINTENTO_OSD = 0.80
        con_vision, t2 = convertir(datos, nombre)
        conversion.CONFIANZA_REINTENTO_OSD = umbral
        resultados.append({"caso": caso, "adjunto": nombre, "base": base, "s_base": t1,
                           "vision": con_vision, "s_vision": t2})
        print(par, "base", len(base), f"{t1}s", "con visión", len(con_vision), f"{t2}s",
              "transcritas", con_vision.count("transcrito por IA"))
    salida.write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")


principal()
