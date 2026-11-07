"""
Pruebas de la compuerta de radicación y de los adaptadores de solo lectura. Sin red.

NUNCA corre nada con la compuerta encendida: la segunda llave
(PERMITIR_RADICACION_REAL) no se pone en ninguna prueba; se comprueba que sin ella
la compuerta queda apagada y que, apagada, toda escritura queda retenida.

    docker exec -w /app/puente positiva-ia-positiva-ia-1 python3 pruebas_compuerta.py
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

SECRETO = "valor-MUY-secreto-789"


def _entorno_base(**extra: str) -> dict:
    e = {k: v for k, v in os.environ.items() if k not in ("RADICACION_REAL", "PERMITIR_RADICACION_REAL", "MODO_TEST", "SECRETOS_DEL_FLUJO")}
    e["RUTA_TRABAJO"] = tempfile.mkdtemp(prefix="compuerta-")
    e["PYTHONIOENCODING"] = "utf-8"
    e.update(extra)
    return e


def _eventos(texto: str) -> list:
    # split("\n") y no splitlines(): para splitlines el \x1e de la marca también corta la línea.
    return [json.loads(l[5:]) for l in texto.split("\n") if l.startswith("\x1eEVT ")]


# ------------------------------------------------------------------ en este proceso

def prueba_compuerta_sin_segunda_llave() -> None:
    import solo_lectura as sl

    assert "PERMITIR_RADICACION_REAL" not in os.environ, "las pruebas nunca ponen la segunda llave"
    os.environ.pop("RADICACION_REAL", None)
    assert sl.radicacion_real() is False
    os.environ["RADICACION_REAL"] = "si"  # la primera llave sola no basta
    try:
        assert sl.radicacion_real() is False
    finally:
        os.environ.pop("RADICACION_REAL", None)


def prueba_supabase_solo_get() -> None:
    import solo_lectura as sl

    llamadas = []

    class Http:
        @staticmethod
        def get(url, params=None, headers=None, timeout=None):
            llamadas.append((url, params, sorted(headers)))

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return [{"numero_radicado": "ENT-1"}]
            return R()

    eventos = []
    c = sl.ConsultaSupabase("https://base.ejemplo.invalid", SECRETO, lambda t, **d: eventos.append((t, d)), http=Http)
    r = c.table("radicados_pqrd").select("numero_radicado").eq("correo_remitente", "a@b.co").gte("fecha_radicacion", "2026-01-01").limit(1).execute()
    assert r.data == [{"numero_radicado": "ENT-1"}]
    url, params, cabeceras = llamadas[0]
    assert url == "https://base.ejemplo.invalid/rest/v1/radicados_pqrd"
    assert ("correo_remitente", "eq.a@b.co") in params and ("limit", "1") in params
    assert "apikey" in cabeceras
    assert SECRETO not in json.dumps(eventos, ensure_ascii=False)
    for escribir in ("insert", "update", "upsert", "delete", "rpc"):
        try:
            getattr(c.table("radicados_pqrd"), escribir)({"x": 1})
        except PermissionError:
            continue
        raise AssertionError(f"Supabase dejó pasar {escribir}")
    try:
        c.rpc("borrar_todo")
    except PermissionError:
        pass
    else:
        raise AssertionError("Supabase dejó pasar rpc")


def prueba_redis_solo_lee() -> None:
    import solo_lectura as sl

    class Falso:
        def get(self, k):
            return "1" if k == "tutelas:abc" else None

        def set(self, *a, **k):
            raise AssertionError("no debía llegar al cliente real")

    r = sl.RedisSoloLectura(Falso())
    assert r.get("tutelas:abc") == "1"
    for escribir in ("set", "setex", "delete", "expire", "incr", "eval"):
        try:
            getattr(r, escribir)
        except PermissionError:
            continue
        raise AssertionError(f"Redis dejó pasar {escribir}")


def prueba_mascara() -> None:
    import solo_lectura as sl

    os.environ["UNA_CLAVE"] = SECRETO
    os.environ["SECRETOS_DEL_FLUJO"] = "UNA_CLAVE,NO_EXISTE"
    try:
        m = sl.Mascara().recordar()
        assert m(f"token={SECRETO}&x=1") == "token=••••&x=1"
        assert SECRETO not in m(json.dumps({"error": f"falló con {SECRETO}"}))
    finally:
        os.environ.pop("UNA_CLAVE", None)
        os.environ.pop("SECRETOS_DEL_FLUJO", None)


# ------------------------------------------- en un proceso aparte (importan el motor)

def escenario_tutelas() -> None:
    """Compuerta apagada: Redis lee (GET) y retiene el reclamo; SGDEA y el buzón que escribe no existen."""
    import trabajador_tutelas as tt

    salida = io.StringIO()
    tt._salida = salida
    assert tt.REAL is False and os.environ["MODO_TEST"] == "true"
    from Tutelas import config

    assert config.MODO_TEST is True
    # con el secreto del entorno del flujo conservado (no se pisa con el relleno)
    assert os.environ["TUTELAS-REDIS-PASSWORD"] == SECRETO
    tt._estado.set(tt.Estado())

    class Falso:
        def __init__(self):
            self.leidas = []

        def get(self, k):
            self.leidas.append(k)
            return "1" if len(self.leidas) == 1 else None

    falso = Falso()
    redis = tt._redis_en_memoria(tt.solo_lectura.RedisSoloLectura(falso))
    _, nuevo = redis.reclamar("11001310300120260012300")
    assert nuevo is False, "si Redis ya lo tiene reclamado es duplicado, como en el job"
    _, nuevo = redis.reclamar("otro-radicado")
    assert nuevo is True and len(falso.leidas) == 2
    sg = tt._sgdea_de_prueba()
    r = sg.session.post(f"{sg.config['api_url']}/tutela", data={"body": json.dumps({"codigoProceso": "X1"})}, files=[])
    assert r.status_code == 201 and r.json()["radicado"].startswith("PRUEBA-")
    try:
        tt.OutlookQueEscribe(object(), "id")
    except PermissionError:
        pass
    else:
        raise AssertionError("el buzón que escribe no puede existir con la compuerta apagada")
    ev = _eventos(salida.getvalue())
    escrituras = [e for e in ev if e["tipo"] == "escritura"]
    assert escrituras and all(e["bloqueada"] is True for e in escrituras), escrituras
    assert {e["sistema"] for e in escrituras} >= {"redis", "sgdea"}
    assert [e["tipo"] for e in ev].count("lectura_real") == 2
    assert SECRETO not in salida.getvalue()
    tt.evento("consola", linea=f"una traza que pegó {SECRETO}")
    assert SECRETO not in salida.getvalue(), "el trabajador también enmascara"
    print("OK")


def escenario_pqrd() -> None:
    """Compuerta apagada: Supabase en solo GET y sus trazas al grabador; el buzón que escribe no existe."""
    import asyncio

    import trabajador as tr

    salida = io.StringIO()
    tr._salida = salida
    assert tr.REAL is False and os.environ["MODO_TEST"] == "true"
    from config import settings

    assert settings.MODO_TEST is True
    assert settings.SUPABASE_SERVICE_ROLE_KEY == SECRETO, "el secreto del entorno del flujo se conserva"
    s = tr._auditoria()
    assert isinstance(s._client, tr.solo_lectura.ConsultaSupabase)
    llamadas = []

    class Http:
        @staticmethod
        def get(url, params=None, headers=None, timeout=None):
            llamadas.append(url)

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return []
            return R()

    s._client._http = Http
    settings.DEDUP_EN_PRUEBAS = True  # como con un correo del buzón real
    assert asyncio.run(s.email_exists("a@b.co", "Asunto", "2026-11-01T10:00:00Z")) is False
    assert llamadas and llamadas[0].endswith("/rest/v1/radicados_pqrd")
    # la traza no sale: MODO_TEST la manda al grabador
    asyncio.run(s.save_radicado_record("ENT-1", "2026-11-06", asunto="x", correo_remitente="a@b.co"))
    assert len(llamadas) == 1
    try:
        s._client.table("radicados_pqrd").insert({"x": 1})
    except PermissionError:
        pass
    else:
        raise AssertionError("Supabase dejó pasar un insert")
    import buzon_vivo

    try:
        buzon_vivo.BuzonQueEscribe(object(), None, {}, tr.evento)
    except PermissionError:
        pass
    else:
        raise AssertionError("el buzón que escribe no puede existir con la compuerta apagada")
    tr._anunciar_fuentes(True)
    ev = _eventos(salida.getvalue())
    compuerta = next(e for e in ev if e["tipo"] == "compuerta")
    assert compuerta["radicacion_real"] is False and "apagada" in compuerta["texto"]
    fuentes = {x["sistema"]: x for x in next(e for e in ev if e["tipo"] == "fuentes")["lecturas"]}
    assert fuentes["Supabase"]["real"] is True and fuentes["SGDEA"]["real"] is False
    assert SECRETO not in salida.getvalue()
    print("OK")


def escenario_pqrd_sin_credenciales() -> None:
    """Sin credenciales de Supabase: la capa queda apagada (el doble de siempre) y se dice."""
    import trabajador as tr

    salida = io.StringIO()
    tr._salida = salida
    assert tr._auditoria() is None
    assert tr._fuentes["supabase"]["real"] is False
    print("OK")


def _correr(escenario: str, **entorno: str) -> None:
    r = subprocess.run([sys.executable, os.path.abspath(__file__), escenario], cwd=AQUI, env=_entorno_base(**entorno),
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert r.returncode == 0 and r.stdout.strip().endswith("OK"), f"{escenario}:\n{r.stdout[-2000:]}\n{r.stderr[-3000:]}"
    assert SECRETO not in r.stdout + r.stderr, f"{escenario}: un secreto salió por la consola"


def principal() -> int:
    if len(sys.argv) > 1:
        {"tutelas": escenario_tutelas, "pqrd": escenario_pqrd, "pqrd_sin": escenario_pqrd_sin_credenciales}[sys.argv[1]]()
        return 0
    pruebas = [
        ("la compuerta no se enciende sin la segunda llave", prueba_compuerta_sin_segunda_llave),
        ("Supabase: solo GET, sin escrituras", prueba_supabase_solo_get),
        ("Redis: solo lectura", prueba_redis_solo_lee),
        ("máscara de los secretos del flujo", prueba_mascara),
        ("tutelas con la compuerta apagada: lee y retiene", lambda: _correr("tutelas", **{"TUTELAS-REDIS-PASSWORD": SECRETO, "SECRETOS_DEL_FLUJO": "TUTELAS-REDIS-PASSWORD"})),
        ("pqrd con la compuerta apagada: Supabase en GET, trazas retenidas", lambda: _correr("pqrd", SUPABASE_URL="https://base.ejemplo.invalid", SUPABASE_SERVICE_ROLE_KEY=SECRETO, SECRETOS_DEL_FLUJO="SUPABASE_SERVICE_ROLE_KEY")),
        ("pqrd sin credenciales: doble de siempre", lambda: _correr("pqrd_sin")),
    ]
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
