"""
Las herramientas de medida corren sin los secretos de produccion.

Miden la capa determinista contra el corpus y no tocan la red, asi que exigir
credenciales las vuelve inejecutables justo cuando se necesitan: en una maquina
de desarrollo o en una revision. Un `__init__` de paquete que importe un
servicio de golpe arrastra `config.settings` a todo el que importe un modulo
hermano, y eso es lo que estas pruebas impiden.

Se comprueba en un proceso APARTE con las variables borradas: dentro de pytest
`conftest.py` las rellena, y ahi no se podria ver.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

# Los diez secretos que `config.settings` exige.
SECRETOS = (
    "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID",
    "OUTLOOK_REFRESH_TOKEN", "OUTLOOK_FOLDER_ID", "REDIS_PASSWORD",
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SGDEA_CLIENT_SECRET",
    "SGDEA_PASSWORD",
)

# Modulos que una herramienta de medida necesita importar.
SIN_CREDENCIALES = [
    "services.prompts",
    "business_rules.geo_mapping",
    "business_rules.office_mapping",
    "pipeline.extraccion",
    "pipeline.compuerta",
    "pipeline.radicador",
    "harness.caso",
    "harness.cobertura",
    "harness.exactitud",
    "harness.regresion",
    "harness.enviado",
    "harness.consumo",
]


def _con_pythonpath(entorno: dict) -> dict:
    """El subproceso corre fuera del repo: hay que decirle donde esta."""
    entorno["PYTHONPATH"] = str(RAIZ)
    return entorno


def _sin_secretos() -> dict:
    entorno = {k: v for k, v in os.environ.items() if k not in SECRETOS}
    entorno["PYTHONPATH"] = str(RAIZ)
    return entorno


def _importar(modulo: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", f"import {modulo}"],
        # Fuera del repo: `env_file=".env"` es relativo al directorio de
        # trabajo, y en desarrollo ese archivo existe con los secretos reales.
        cwd=tempfile.gettempdir(), env=_con_pythonpath(_sin_secretos()),
        capture_output=True, text=True, timeout=120,
    )


def test_el_entorno_de_la_prueba_de_verdad_no_tiene_los_secretos():
    """
    Canario. Si un `.env` en la raiz repusiera los secretos, las pruebas de
    abajo pasarian sin comprobar nada. `services.outlook` los exige de verdad:
    aqui TIENE que fallar.
    """
    proceso = _importar("services.outlook")
    assert proceso.returncode != 0, (
        "services.outlook se importo sin credenciales: el entorno de la prueba "
        "las tiene puestas por algun lado y las pruebas de abajo no valen"
    )


@pytest.mark.parametrize("modulo", SIN_CREDENCIALES)
def test_el_modulo_se_importa_sin_secretos(modulo):
    proceso = _importar(modulo)
    assert proceso.returncode == 0, (
        f"{modulo} exige credenciales:\n{proceso.stderr[-1500:]}"
    )


def test_el_paquete_de_servicios_sigue_exportando_sus_clases():
    """La resolucion perezosa no puede romper a quien importa del paquete."""
    import services

    for nombre in services.__all__:
        assert getattr(services, nombre) is not None, nombre


def test_un_nombre_que_no_existe_dice_cuales_hay():
    import services

    with pytest.raises(AttributeError) as fallo:
        services.ServicioQueNoExiste
    assert "ServicioQueNoExiste" in str(fallo.value)
    assert "OutlookService" in str(fallo.value)
