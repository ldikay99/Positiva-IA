"""Coherencia entre la configuracion, la documentacion y el codigo."""
import importlib
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _texto(nombre: str) -> str:
    ruta = RAIZ / nombre
    assert ruta.exists(), f"falta el archivo {nombre}"
    return ruta.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("ruta", [".env.example", "run_local_test.py",
                                  "azure-pipelines.yml", "Dockerfile",
                                  "requirements.txt", "main.py"])
def test_los_archivos_que_la_documentacion_menciona_existen(ruta):
    assert (RAIZ / ruta).exists()


@pytest.mark.parametrize("documento", ["README.md", "VERTEX.md"])
def test_la_documentacion_no_manda_a_correr_un_archivo_inexistente(documento):
    contenido = _texto(documento)
    for mencionado in re.findall(r"python\s+([\w./\\-]+\.py)", contenido):
        if mencionado.startswith("-"):
            continue
        assert (RAIZ / mencionado).exists(), f"{documento} invoca {mencionado}"


def test_el_env_example_cubre_todos_los_secretos_obligatorios():
    from config.settings import Settings

    plantilla = _texto(".env.example")
    obligatorias = [
        n for n, c in Settings.model_fields.items() if c.is_required()
    ]
    assert obligatorias
    faltantes = [n for n in obligatorias if f"{n}=" not in plantilla]
    assert not faltantes, f"faltan en .env.example: {faltantes}"


def test_el_env_example_no_trae_credenciales():
    """
    Ningun campo obligatorio de `Settings` lleva valor en la plantilla.

    Los campos obligatorios son los secretos: no tienen valor por defecto
    porque solo existen en el entorno.
    """
    from config.settings import Settings

    secretos = {n for n, c in Settings.model_fields.items() if c.is_required()}
    con_valor = []
    for linea in _texto(".env.example").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        if clave.strip() in secretos and valor.strip():
            con_valor.append(clave.strip())
    assert not con_valor, f"la plantilla trae credenciales: {con_valor}"


def test_los_interruptores_de_la_plantilla_existen_en_settings():
    """Toda variable de la plantilla corresponde a un campo de `Settings`."""
    from config.settings import Settings

    desconocidas = []
    for linea in _texto(".env.example").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave = linea.partition("=")[0].strip()
        if clave not in Settings.model_fields:
            desconocidas.append(clave)
    assert not desconocidas, f"la plantilla ofrece variables inexistentes: {desconocidas}"


def test_los_modelos_documentados_son_los_que_usa_el_codigo():
    from services.prompts import (
        GEMINI_MODEL,
        GEMINI_MODEL_CLASIFICACION,
        GEMINI_MODEL_EXTRACCION,
    )

    contenido = _texto("VERTEX.md")
    for modelo in {GEMINI_MODEL, GEMINI_MODEL_CLASIFICACION, GEMINI_MODEL_EXTRACCION}:
        assert modelo in contenido, f"VERTEX.md no menciona {modelo}"


def test_el_dockerfile_instala_el_motor_de_ocr():
    dockerfile = _texto("Dockerfile")
    assert "tesseract-ocr" in dockerfile
    assert "tesseract-ocr-spa" in dockerfile


# ── La imagen tiene que llevar lo que el punto de entrada usa ──

def _copiado_por_el_dockerfile():
    """Rutas de primer nivel que el Dockerfile mete en la imagen."""
    copiado = set()
    for linea in _texto("Dockerfile").splitlines():
        if not linea.strip().upper().startswith("COPY "):
            continue
        partes = linea.split()
        if len(partes) < 3:
            continue
        copiado.add(partes[1].rstrip("/"))
    return copiado


def _arranque_del_dockerfile():
    """El archivo que ejecuta el CMD."""
    for linea in _texto("Dockerfile").splitlines():
        if linea.strip().upper().startswith("CMD "):
            for pieza in re.findall(r'"([^"]+)"', linea):
                if pieza.endswith(".py"):
                    return pieza
    return ""


def test_el_cmd_arranca_un_archivo_que_existe():
    arranque = _arranque_del_dockerfile()
    assert arranque, "el Dockerfile no declara que ejecutar"
    assert (RAIZ / arranque).exists(), f"el CMD invoca {arranque}, que no existe"


def test_la_imagen_lleva_todo_lo_que_el_punto_de_entrada_importa():
    """
    Un paquete que falta en la imagen no da error al construirla: revienta en
    la primera corrida del Job, con el buzon lleno.

    Se recorre el arbol de importaciones desde el CMD y se comprueba que cada
    paquete del proyecto que aparece este entre los COPY.
    """
    import ast

    copiado = _copiado_por_el_dockerfile()
    arranque = _arranque_del_dockerfile()
    pendientes = [RAIZ / arranque]
    vistos, faltan = set(), set()

    while pendientes:
        ruta = pendientes.pop()
        if ruta in vistos or not ruta.exists():
            continue
        vistos.add(ruta)
        arbol = ast.parse(ruta.read_text(encoding="utf-8", errors="replace"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                modulos = [a.name for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom) and nodo.module and not nodo.level:
                modulos = [nodo.module]
            else:
                continue
            for modulo in modulos:
                raiz = modulo.split(".")[0]
                if not (RAIZ / raiz).is_dir():
                    continue
                if raiz not in copiado:
                    faltan.add(f"{ruta.name} importa {modulo}")
                destino = RAIZ / Path(*modulo.split("."))
                pendientes.append(
                    destino / "__init__.py" if destino.is_dir()
                    else destino.with_suffix(".py"))

    assert not faltan, (
        "el Dockerfile no copia todo lo que el punto de entrada necesita: "
        + "; ".join(sorted(faltan)))


def test_el_pipeline_dispara_en_las_tres_ramas():
    disparadores = _texto("azure-pipelines.yml").split("pool:")[0]
    for rama in ("dev", "uat", "prod"):
        assert f"- {rama}" in disparadores


# ── Fallar sin credenciales tiene que servir para arreglarlo ──

def _sin_env_file(monkeypatch, cs) -> None:
    """
    Ignora el `.env` del repo.

    `Settings` lo lee del directorio de trabajo, y en desarrollo ese archivo
    existe con los secretos reales: sin esto, quitar una variable del entorno
    no la quita de verdad y la prueba no comprueba nada.
    """
    monkeypatch.setitem(cs.Settings.model_config, "env_file", None)


def test_sin_un_secreto_el_error_dice_cual_falta(monkeypatch):
    """El error nombra la variable que falta y el archivo donde ponerla."""
    # `config.settings` como atributo es la instancia, no el modulo.
    cs = importlib.import_module("config.settings")
    _sin_env_file(monkeypatch, cs)

    monkeypatch.delenv("SGDEA_PASSWORD", raising=False)
    cs.get_settings.cache_clear()
    try:
        with pytest.raises(cs.ConfiguracionIncompleta) as e:
            cs.get_settings()
        assert e.value.faltantes == ["SGDEA_PASSWORD"]
        assert ".env" in str(e.value)
        assert "SGDEA_PASSWORD" in str(e.value)
    finally:
        cs.get_settings.cache_clear()


def test_el_error_de_configuracion_no_filtra_los_valores(monkeypatch):
    """El mensaje se imprime en consola y en logs: no puede llevar secretos."""
    # `config.settings` como atributo es la instancia, no el modulo.
    cs = importlib.import_module("config.settings")

    _sin_env_file(monkeypatch, cs)

    monkeypatch.setenv("SGDEA_PASSWORD", "clave-secreta-de-produccion")
    monkeypatch.delenv("SGDEA_PASSWORD", raising=False)
    cs.get_settings.cache_clear()
    try:
        with pytest.raises(cs.ConfiguracionIncompleta) as e:
            cs.get_settings()
        assert "clave-secreta-de-produccion" not in str(e.value)
    finally:
        cs.get_settings.cache_clear()


# ── Las URL de servicios van en la configuracion, no en el codigo ──

@pytest.mark.parametrize("modulo", [
    "services/radicacion.py",
    "services/outlook.py",
])
def test_ninguna_url_de_servicio_esta_quemada_en_el_codigo(modulo):
    """Las URL de servicio van en `config/settings.py`, no dentro del modulo."""
    fuente = _texto(modulo)
    quemadas = re.findall(
        r'"https://(?!graph\.microsoft\.com)[^"]*run\.app[^"]*"', fuente)
    assert not quemadas, (
        f"{modulo} tiene URL de servicio escritas dentro: {quemadas}. "
        "Van en config/settings.py.")


def test_los_artefactos_con_pii_estan_excluidos_de_git():
    gitignore = _texto(".gitignore")
    for patron in ("corpus/casos/", "workspace/", "logs/", ".env"):
        assert patron in gitignore, f"falta excluir {patron}"
