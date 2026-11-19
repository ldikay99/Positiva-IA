# Diferencias con el repositorio de origen

`motores/tutelas/` es una copia del servicio de tutelas (el commit está en `ORIGEN`). Estos son los cambios hechos aquí
que no están en el origen.

| Cambio | Por qué | Llevar a producción |
|---|---|---|
| **`requirements.txt`: versiones exactas en lugar de rangos `>=`** (2026-11-18): requests 2.34.2, python-dotenv 1.2.3, redis 8.1.0, google-genai 2.23.0, google-auth 2.62.2, fpdf2 2.9.0, psycopg2-binary 2.9.13, PyMuPDF 1.28.2, pytesseract 0.3.13, pillow 13.0.0. | Con rangos la imagen no es reproducible ni auditable. Las que comparte con el motor van en la misma versión del motor, porque la imagen de la plataforma las instala juntas. `pip-audit` sin hallazgos; todos los módulos importan. | **Sí.** |
| **`requirements.txt` al día con PyPI** (2026-11-18): google-genai 2.23.0 → 3.6.1, PyMuPDF 1.28.2 → 2.1, python-dotenv 1.2.3 → 1.2.4. El resto ya estaba en la última versión. Sin cambios de código. | Las tres las comparte con `motor/requirements.txt`, que subió a las mismas versiones (la imagen de la plataforma las instala juntas). google-genai 3 cambia el cliente HTTP a `httpx2` y la versión por defecto de la API con llave a `v1`; aquí el cliente es de Vertex (`vertexai=True`), que sigue en `v1beta1`, y nada captura excepciones de `httpx`. PyMuPDF 2 no cambia `get_text`, `get_text("dict")` ni `get_pixmap`, lo único que se usa (comprobado byte a byte contra 1.28.2); `import fitz` sigue funcionando con un `FutureWarning`. Trae onnxruntime, networkx, numpy y psutil (MIT/BSD/Apache): la imagen crece. `pip-audit` sin hallazgos; los 12 módulos de `Tutelas/` importan en Python 3.12 y 3.13. | **Sí.** |
| **`Dockerfile`: dos etapas (entorno virtual y ejecución), usuario `appuser` sin privilegios y base fijada por digest (`python:3.12.16-slim-trixie`); `.dockerignore` nuevo** (2026-11-18). | Endurecimiento: no corre como root y la imagen final no lleva herramientas de compilación. Construida y verificada. | **Sí.** |
