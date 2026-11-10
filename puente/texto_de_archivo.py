"""
El texto de cualquier archivo, con la conversión del motor de PQRD (PDF, imágenes
con OCR, Word, Excel, correos, ZIP…). Lo usan el visor del chat, para mostrar un
documento que el navegador no abre, y el asistente, para leer lo que se le adjunta.

    python texto_de_archivo.py RUTA NOMBRE
Imprime el texto (markdown) por la salida estándar.
"""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MOTOR = Path(os.environ.get("RUTA_MOTOR", RAIZ / "motor"))
os.environ["MODO_TEST"] = "true"
for _v in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID", "OUTLOOK_REFRESH_TOKEN", "OUTLOOK_FOLDER_ID",
           "REDIS_PASSWORD", "SGDEA_CLIENT_SECRET", "SGDEA_PASSWORD"):
    os.environ.setdefault(_v, "x")
sys.path.insert(0, str(MOTOR))


def texto(ruta: str, nombre: str) -> str:
    datos = Path(ruta).read_bytes()
    n = nombre.lower()
    if n.endswith((".pptx", ".zip")) and datos[:4] == b"PK\x03\x04":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import salidas  # PowerPoint diapositiva por diapositiva y la lista de un ZIP (el motor no los lee)

        return salidas.texto_pptx(datos) if n.endswith(".pptx") else "Archivos en el ZIP:\n" + salidas.texto_zip(datos)
    if nombre.lower().endswith((".txt", ".csv", ".json", ".md", ".xml", ".html", ".htm", ".log", ".yaml", ".yml", ".tsv")):
        for codificacion in ("utf-8", "latin-1"):
            try:
                return datos.decode(codificacion)
            except UnicodeDecodeError:
                continue
    from pipeline import conversion

    c = conversion.convertir(datos, nombre)
    return c.markdown or (c.error or "")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.stdout.write(texto(sys.argv[1], sys.argv[2])[:400_000])
    except Exception as e:
        sys.stdout.write(f"(no se pudo leer el archivo: {type(e).__name__}: {e})")
