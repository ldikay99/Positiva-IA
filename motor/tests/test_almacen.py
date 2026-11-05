"""
Los informes se escriben en disco o en un bucket, con la misma interfaz.

El sistema de archivos del Job es efimero: la serie historica solo acumula si
va a un bucket, y una direccion `gs://` no sobrevive a `Path()`.
"""
import pytest

from pipeline import almacen


@pytest.mark.parametrize("ruta, es", [
    ("gs://bucket/prefijo", True),
    ("GS://Bucket/Prefijo", True),
    ("gs://bucket", True),
    ("/app/logs", False),
    ("C:\\logs", False),
    ("", False),
])
def test_se_distingue_un_bucket_de_una_ruta_de_disco(ruta, es):
    assert almacen.es_bucket(ruta) is es


def test_unir_conserva_la_forma_de_la_direccion():
    """
    `Path` convierte una direccion de bucket en una ruta de Windows y deja de
    existir.
    """
    assert almacen.unir("gs://b/pre", "ejecuciones", "hoy.txt") == (
        "gs://b/pre/ejecuciones/hoy.txt")


def test_unir_no_duplica_separadores():
    assert almacen.unir("gs://b/pre/", "/diario/", "hoy.txt") == (
        "gs://b/pre/diario/hoy.txt")


def test_en_disco_escribe_lee_y_anexa(tmp_path):
    destino = tmp_path / "sub" / "serie.tsv"
    assert almacen.leer(destino) is None
    almacen.escribir(destino, "cabecera\n")
    almacen.anexar(destino, "fila\n")
    assert almacen.leer(destino) == "cabecera\nfila\n"


def test_escribir_reemplaza_lo_que_hubiera(tmp_path):
    destino = tmp_path / "x.txt"
    almacen.escribir(destino, "viejo")
    almacen.escribir(destino, "nuevo")
    assert almacen.leer(destino) == "nuevo"


def test_anexar_crea_el_destino_si_no_existia(tmp_path):
    destino = tmp_path / "nuevo" / "x.txt"
    almacen.anexar(destino, "primero\n")
    assert almacen.leer(destino) == "primero\n"


def test_una_direccion_sin_objeto_no_se_puede_escribir():
    """`gs://bucket` nombra el bucket, no un archivo dentro."""
    with pytest.raises(ValueError):
        almacen._blob("gs://solo-el-bucket")


def test_un_destino_inalcanzable_falla_con_su_motivo(tmp_path, monkeypatch):
    """
    Si el informe no se puede guardar, el fallo tiene que llegar con el motivo.
    Los correos ya quedaron radicados y marcados: quien lo lea necesita saber
    que se perdio el informe, no que se perdio la corrida.
    """
    import pytest

    def revienta(*a, **k):
        raise PermissionError("403 la cuenta no tiene permiso de escritura")

    monkeypatch.setattr(almacen, "_blob", revienta)
    with pytest.raises(PermissionError) as e:
        almacen.escribir("gs://bucket/objeto.txt", "contenido")
    assert "permiso" in str(e.value)


# ── En el bucket se anexa creando partes, nunca reemplazando ──

class BucketFalso:
    """
    Doble del cliente de Cloud Storage que solo permite CREAR objetos.

    Reemplazar uno lanza, igual que un bucket sin `storage.objects.delete`:
    es la condicion real del bucket de produccion y lo que hay que respetar.
    """

    def __init__(self):
        self.objetos = {}

    # -- lo que usa almacen --
    def bucket(self, nombre):
        self._ultimo_bucket = nombre
        return self

    def blob(self, nombre):
        return BlobFalso(self, nombre)

    def list_blobs(self, bucket, prefix=""):
        return [BlobFalso(self, n) for n in sorted(self.objetos)
                if n.startswith(prefix)]


class BlobFalso:
    def __init__(self, almacen_falso, nombre):
        self._a = almacen_falso
        self.name = nombre

    def exists(self):
        return self.name in self._a.objetos

    def upload_from_string(self, contenido, content_type=""):
        if self.name in self._a.objetos:
            raise PermissionError(
                "403 no tiene storage.objects.delete: reemplazar un objeto "
                "cuenta como borrar y crear")
        self._a.objetos[self.name] = contenido

    def download_as_text(self):
        return self._a.objetos[self.name]


@pytest.fixture
def bucket(monkeypatch):
    falso = BucketFalso()

    class ModuloFalso:
        Client = staticmethod(lambda: falso)

    import sys
    import types

    paquete = types.ModuleType("google.cloud.storage")
    paquete.Client = ModuloFalso.Client
    monkeypatch.setitem(sys.modules, "google.cloud.storage", paquete)
    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    cloud.storage = paquete
    google.cloud = cloud
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    return falso


def test_anexar_en_el_bucket_no_reemplaza_el_objeto(bucket):
    """
    Reemplazar exige permiso de borrado, que el bucket de produccion no da.
    Cada llamada crea su propia parte.
    """
    for i in range(3):
        almacen.anexar("gs://b/logs/serie.tsv", f"fila {i}\n")

    assert "logs/serie.tsv" not in bucket.objetos
    partes = [n for n in bucket.objetos if n.startswith("logs/serie.tsv.partes/")]
    assert len(partes) == 3


def test_leer_une_las_partes_en_orden(bucket):
    for i in range(4):
        almacen.anexar("gs://b/logs/serie.tsv", f"fila {i}\n")

    assert almacen.leer("gs://b/logs/serie.tsv") == (
        "fila 0\nfila 1\nfila 2\nfila 3\n")


def test_leer_un_destino_sin_partes_devuelve_none(bucket):
    assert almacen.leer("gs://b/logs/serie.tsv") is None


def test_se_sigue_leyendo_un_objeto_escrito_de_una_pieza(bucket):
    """Las series de antes son un solo objeto y no se pueden perder."""
    almacen.escribir("gs://b/logs/serie.tsv", "todo junto\n")

    assert almacen.leer("gs://b/logs/serie.tsv") == "todo junto\n"


def test_lo_viejo_de_una_pieza_y_lo_nuevo_en_partes_se_leen_juntos(bucket):
    """
    En el bucket ya hay un objeto escrito por las corridas de antes. Si `leer`
    se queda con el, las partes nuevas no se leerian nunca; si se queda con las
    partes, se pierde el historico.
    """
    almacen.escribir("gs://b/logs/serie.tsv", "fila vieja\n")
    almacen.anexar("gs://b/logs/serie.tsv", "fila nueva 1\n")
    almacen.anexar("gs://b/logs/serie.tsv", "fila nueva 2\n")

    assert almacen.leer("gs://b/logs/serie.tsv") == (
        "fila vieja\nfila nueva 1\nfila nueva 2\n")


def test_las_partes_de_dos_corridas_del_mismo_instante_no_colisionan(bucket):
    almacen.anexar("gs://b/logs/diario/hoy.txt", "corrida A\n")
    almacen.anexar("gs://b/logs/diario/hoy.txt", "corrida B\n")

    contenido = almacen.leer("gs://b/logs/diario/hoy.txt")
    assert "corrida A" in contenido and "corrida B" in contenido
