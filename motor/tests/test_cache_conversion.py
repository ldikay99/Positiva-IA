"""
La cache de conversion no vuelve a leer el mismo archivo, ni en la corrida ni
entre corridas, y descuenta del presupuesto lo que costo la primera vez.
"""
import pytest

from pipeline import cache_conversion, conversion


# ── Cache de conversion ──

@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setenv("CACHE_CONVERSION", "si")
    monkeypatch.setenv("RUTA_CACHE_CONVERSION", str(tmp_path / "cache"))
    monkeypatch.setattr(conversion, "TRANSCRIPTOR_IA", None)
    cache_conversion.vaciar_memoria()
    yield
    cache_conversion.vaciar_memoria()


def test_el_mismo_archivo_no_se_convierte_dos_veces(cache, monkeypatch):
    llamadas = []
    original = conversion.convertir

    def contar(datos, nombre, claves=None, _profundidad_sobre=0, presupuesto=None):
        llamadas.append(nombre)
        if presupuesto is not None:
            presupuesto.gastar(3.0)
        return original(datos, nombre, claves, _profundidad_sobre, presupuesto)

    monkeypatch.setattr(conversion, "convertir", contar)
    p1, p2 = conversion.PresupuestoDeOcr(), conversion.PresupuestoDeOcr()
    a = cache_conversion.convertir(b"hola mundo", "nota.txt", presupuesto=p1)
    b = cache_conversion.convertir(b"hola mundo", "nota.txt", presupuesto=p2)
    assert llamadas == ["nota.txt"]
    assert a.markdown == b.markdown
    # El acierto cuesta lo mismo que la primera lectura: el correo decide igual.
    assert p2.gastado_s == pytest.approx(p1.gastado_s, abs=0.01)


def test_la_cache_sobrevive_entre_corridas(cache, monkeypatch):
    cache_conversion.convertir(b"hola mundo", "nota.txt")
    cache_conversion.vaciar_memoria()  # otra corrida: memoria vacia, disco no
    monkeypatch.setattr(conversion, "convertir",
                        lambda *a, **k: pytest.fail("se volvio a convertir"))
    assert "hola mundo" in cache_conversion.convertir(b"hola mundo", "nota.txt").markdown


def test_una_lectura_a_medias_no_se_guarda(cache, monkeypatch):
    llamadas = []

    def a_medias(datos, nombre, claves=None, _profundidad_sobre=0, presupuesto=None):
        llamadas.append(1)
        return conversion.Conversion(markdown="# x\n\nalgo", tipo_real="pdf",
                                     aviso="se agoto el presupuesto de OCR del correo")

    monkeypatch.setattr(conversion, "convertir", a_medias)
    cache_conversion.convertir(b"%PDF-1.4 x", "x.pdf")
    cache_conversion.convertir(b"%PDF-1.4 x", "x.pdf")
    assert len(llamadas) == 2


def test_sin_presupuesto_para_lo_que_costo_no_se_usa(cache, monkeypatch):
    original = conversion.convertir

    def caro(datos, nombre, claves=None, _profundidad_sobre=0, presupuesto=None):
        if presupuesto is not None:
            presupuesto.gastar(100.0)
        return original(datos, nombre, claves, _profundidad_sobre, presupuesto)

    monkeypatch.setattr(conversion, "convertir", caro)
    cache_conversion.convertir(b"texto", "t.txt", presupuesto=conversion.PresupuestoDeOcr())
    poco = conversion.PresupuestoDeOcr(total_s=10.0)
    llamadas = []
    monkeypatch.setattr(conversion, "convertir",
                        lambda *a, **k: (llamadas.append(1), original(*a, **k))[1])
    cache_conversion.convertir(b"texto", "t.txt", presupuesto=poco)
    assert llamadas, "con menos presupuesto del que costo, se lee de nuevo"


def test_apagada_no_guarda(monkeypatch):
    monkeypatch.setenv("CACHE_CONVERSION", "no")
    cache_conversion.vaciar_memoria()
    cache_conversion.convertir(b"hola", "n.txt")
    assert cache_conversion.estado()["en_memoria"] == 0
