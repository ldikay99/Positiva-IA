"""
Configuracion de pytest.

config/settings.py declara los secretos como campos obligatorios, asi que
importar cualquier modulo del proyecto falla si no estan en el entorno. Estas
pruebas son de logica pura y no hablan con ningun servicio externo, por lo que
se rellenan con valores ficticios ANTES de cualquier import del proyecto.

Asi la suite corre en cualquier maquina y en CI sin necesidad de credenciales
de produccion.
"""
import os
import sys
from pathlib import Path

# Permite ejecutar pytest desde la raiz del repositorio
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# Valores ficticios: estas pruebas NO usan Outlook, Redis, la base de radicados ni SGDEA.
_SECRETOS_FICTICIOS = {
    "MICROSOFT_CLIENT_ID": "test",
    "MICROSOFT_CLIENT_SECRET": "test",
    "MICROSOFT_TENANT_ID": "test",
    "OUTLOOK_REFRESH_TOKEN": "test",
    "OUTLOOK_FOLDER_ID": "test",
    "REDIS_PASSWORD": "test",
    "SGDEA_CLIENT_SECRET": "test",
    "SGDEA_PASSWORD": "test",
    # Cinturon extra: si algo intentara escribir, MODO_TEST lo bloquea.
    "MODO_TEST": "true",
}

for _clave, _valor in _SECRETOS_FICTICIOS.items():
    os.environ.setdefault(_clave, _valor)

# La cache de conversion recuerda por contenido: entre pruebas que cambian el
# OCR con dobles, un PDF leido en una le daria a otra lo que leyo el doble. Las
# pruebas de la cache la encienden a proposito.
os.environ.setdefault("CACHE_CONVERSION", "no")
