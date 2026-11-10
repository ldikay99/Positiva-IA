"""
Registro de radicados en Cloud SQL.

Las primeras pruebas usan una conexion falsa. La ultima habla con un Postgres
de verdad si se le da REGISTRO_PG_PRUEBA=1 y las mismas variables CLOUDSQL_*
que usa el job: crea la tabla, inserta, deduplica y la borra.
"""
import os

import pytest

from config.settings import settings
from services.registro_radicados import (
    RegistroDeRadicados, nombre_de_tabla, piso_de_fecha)


class ConexionFalsa:
    def __init__(self, filas=None, falla=None):
        self.filas = filas if filas is not None else []
        self.falla = falla
        self.consultas = []
        self.abiertas = 0

    def run(self, sql, **parametros):
        if self.falla:
            raise self.falla
        self.consultas.append((sql, parametros))
        if sql.startswith("SET"):
            return None
        return self.filas

    def close(self):
        pass


def _registro(conexion, **kw):
    def conectar(**datos):
        conexion.abiertas += 1
        conexion.datos = datos
        return conexion
    return RegistroDeRadicados(conectar=conectar, **kw)


def test_el_nombre_de_tabla_no_admite_inyeccion():
    assert nombre_de_tabla("iapqrd_radicados_pqrd") == '"iapqrd_radicados_pqrd"'
    for malo in ("x; drop table y", "a.b", "", '"x"', "1tabla"):
        with pytest.raises(ValueError):
            nombre_de_tabla(malo)


def test_el_piso_resta_un_dia_a_la_llegada():
    assert piso_de_fecha("2026-10-27T15:00:00Z") == "2026-10-26T15:00:00"
    assert piso_de_fecha("no es fecha") == ""


def test_solo_se_usa_con_host_usuario_y_clave(monkeypatch):
    for k in ("CLOUDSQL_HOST", "CLOUDSQL_USER", "CLOUDSQL_PASSWORD"):
        monkeypatch.setattr(settings, k, "x")
    assert RegistroDeRadicados.configurado()
    monkeypatch.setattr(settings, "CLOUDSQL_PASSWORD", "")
    assert not RegistroDeRadicados.configurado()


@pytest.mark.asyncio
async def test_se_conecta_con_las_variables_del_job(monkeypatch):
    monkeypatch.setattr(settings, "MODO_TEST", False)
    monkeypatch.setattr(settings, "CLOUDSQL_HOST", "10.0.0.5")
    monkeypatch.setattr(settings, "CLOUDSQL_USER", "usuario")
    monkeypatch.setattr(settings, "CLOUDSQL_PASSWORD", "clave")
    monkeypatch.setattr(settings, "CLOUDSQL_DB", "gcpprolinktic")
    monkeypatch.setattr(settings, "CLOUDSQL_TABLE_RADICADOS_IAPQRD", "iapqrd_radicados_pqrd")
    c = ConexionFalsa(filas=[[1]])
    r = _registro(c)
    assert await r.guardar_radicado("ENT1", asunto="A", correo_remitente="a@b.co",
                                    numero_documento_afectado="123")
    assert c.datos["host"] == "10.0.0.5"
    assert c.datos["user"] == "usuario"
    assert c.datos["password"] == "clave"
    assert c.datos["database"] == "gcpprolinktic"
    assert c.datos["ssl_context"] is not None
    sql, p = c.consultas[-1]
    assert 'INSERT INTO "iapqrd_radicados_pqrd"' in sql
    assert "WHERE NOT EXISTS" in sql
    assert p["documento"] == "123" and p["radicado"] == "ENT1"


@pytest.mark.asyncio
async def test_en_modo_prueba_no_escribe(monkeypatch):
    monkeypatch.setattr(settings, "MODO_TEST", True)
    c = ConexionFalsa()
    assert not await _registro(c).guardar_radicado("ENT1")
    assert c.abiertas == 0


@pytest.mark.asyncio
async def test_si_la_base_no_responde_lo_dice_y_no_insiste(monkeypatch):
    monkeypatch.setattr(settings, "DEDUP_EN_PRUEBAS", True)
    c = ConexionFalsa(falla=TimeoutError("sin respuesta"))
    r = _registro(c)
    assert not await r.email_exists("a@b.co", "Asunto")
    assert "TimeoutError" in r.ultimo_error
    abiertas = c.abiertas
    assert not await r.email_exists("a@b.co", "Asunto")
    assert c.abiertas == abiertas, "tras una caida no debe reintentar en cada correo"
    assert r.ultimo_error


@pytest.mark.asyncio
async def test_el_detector_guarda_el_radicado_con_el_documento():
    from types import SimpleNamespace

    from pipeline.duplicados import Detector

    guardados = []

    class Auditoria:
        async def guardar_radicado(self, **kw):
            guardados.append(kw)
            return True

    correo = SimpleNamespace(subject="RV: Solicitud", from_address=SimpleNamespace(address="A@B.co"))
    assert await Detector(auditoria=Auditoria()).guardar_en_auditoria(correo, "ENT9", "123") == ""
    assert guardados == [{"numero_radicado": "ENT9", "asunto": "RV: Solicitud",
                          "correo_remitente": "A@B.co", "numero_documento_afectado": "123"}]


@pytest.mark.asyncio
async def test_si_no_se_puede_guardar_lo_devuelve_sin_romper():
    from types import SimpleNamespace

    from pipeline.duplicados import Detector

    class Auditoria:
        async def guardar_radicado(self, **kw):
            raise ConnectionError("caida")

    correo = SimpleNamespace(subject="S", from_address=None)
    motivo = await Detector(auditoria=Auditoria()).guardar_en_auditoria(correo, "ENT9")
    assert "caida" in motivo
    assert await Detector(auditoria=Auditoria()).guardar_en_auditoria(correo, "DRY-RUN") == ""


# ── Contra un Postgres real, con las variables CLOUDSQL_* del job ──

@pytest.mark.skipif(os.environ.get("REGISTRO_PG_PRUEBA") != "1",
                    reason="necesita un Postgres y REGISTRO_PG_PRUEBA=1")
@pytest.mark.asyncio
async def test_inserta_y_deduplica_en_postgres_real(monkeypatch):
    for k in ("CLOUDSQL_HOST", "CLOUDSQL_USER", "CLOUDSQL_PASSWORD", "CLOUDSQL_DB",
              "CLOUDSQL_TABLE_RADICADOS_IAPQRD"):
        monkeypatch.setattr(settings, k, os.environ[k])
    monkeypatch.setattr(settings, "CLOUDSQL_SSL", os.environ.get("CLOUDSQL_SSL", "true") == "true")
    monkeypatch.setattr(settings, "MODO_TEST", False)
    monkeypatch.setattr(settings, "DEDUP_EN_PRUEBAS", True)
    r = RegistroDeRadicados()
    radicado = "ENTPRUEBA" + os.urandom(4).hex().upper()

    assert await r.guardar_radicado(radicado, asunto="Solicitud de prueba",
                                    correo_remitente="prueba@ejemplo.co",
                                    numero_documento_afectado="1000000001")
    assert not await r.guardar_radicado(radicado, asunto="otra vez"), "no debe duplicar"
    assert await r.email_exists("prueba@ejemplo.co", "Solicitud de prueba", "2020-01-01T00:00:00Z")
    assert not await r.email_exists("prueba@ejemplo.co", "Otro asunto")
    assert r.ultimo_error == ""

    filas = await r._ejecutar(
        f"SELECT numero_documento_afectado, asignado_automatico, persona_asignada, "
        f"fecha_radicacion IS NOT NULL, current_setting('TimeZone') "
        f"FROM {r.tabla} WHERE numero_radicado = :r", r=radicado)
    assert filas == [["1000000001", False, None, True, "America/Bogota"]]
    if os.environ.get("CLOUDSQL_SSL", "true") == "true":
        cifrada = await r._ejecutar("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
        assert cifrada == [[True]]
    await r._ejecutar(f"DELETE FROM {r.tabla} WHERE numero_radicado = :r", r=radicado)
