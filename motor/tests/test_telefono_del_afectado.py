"""
Cual de los telefonos del correo es el del afectado.

Un correo trae varios: el conmutador del membrete, el fijo de la casa, el
celular de la persona y el del acudiente. El campo de SGDEA es "telefono
celular", asi que decide primero el rotulo y despues la cercania al nombre.
"""
import pytest

from pipeline.extraccion import extraer_telefono
from utils.contacto_extractor import (
    etiqueta_de_contacto,
    extraer_telefonos,
    rango_de_preferencia,
    rango_fino,
)

NOMBRE = "LUIS FRANCISCO DIAZ VILLAFAÑA"


def _numero(texto, nombre=NOMBRE):
    h, _ = extraer_telefono(texto, nombre_afectado=nombre)
    return h.valor if h else None


# ── El rotulo desempata ──

@pytest.mark.parametrize("contexto,esperado", [
    ("telefono celular: 3100286057", "celular"),
    ("movil 3100286057", "celular"),
    ("whatsapp 3100286057", "celular"),
    ("telefono casa: 3051408709", "otro"),
    ("conmutador 3233183344", "otro"),
    ("telefono oficina 3233183344", "otro"),
    ("telefono: 3233183344", ""),
])
def test_la_etiqueta_se_lee_del_rotulo(contexto, esperado):
    assert etiqueta_de_contacto(contexto) == esperado


def test_el_celular_rotulado_gana_al_telefono_de_la_casa():
    texto = (f"{NOMBRE}\nTelefono Celular: 3100286057\n"
             f"Telefono Casa: 3051408709")
    assert _numero(texto) == "3100286057"


def test_el_rango_fino_no_cambia_el_orden_grueso():
    """
    Otros llamadores siguen usando `rango_de_preferencia`: el primer elemento
    del rango fino tiene que ser exactamente ese.
    """
    for texto in ("celular 3100286057", "telefono fijo 6012345678",
                  "contacto +58 412 1234567"):
        for t in extraer_telefonos(texto):
            assert rango_fino(t)[0] == rango_de_preferencia(t)


# ── La cercania al nombre desempata lo que el rotulo no ──

def test_el_conmutador_del_membrete_no_gana_al_del_afectado():
    """Los dos llevan el rotulo generico "Telefono:" y los dos son celulares: por ranking empatan."""
    texto = (
        "Gencell Pharma S.A.S NIT: 571767797-2 medicina trabajo "
        "Telefono: 3233183344 Direccion: Calle 82\n"
        f"Nombres y Apellidos: {NOMBRE} Telefono: 3100286057"
    )
    assert _numero(texto) == "3100286057"


def test_sin_nombre_del_afectado_se_mantiene_el_orden_de_siempre():
    texto = "Telefono: 3233183344 ... Telefono: 3100286057"
    h, _ = extraer_telefono(texto, nombre_afectado="")
    assert h.valor == "3233183344"
    assert h.metodo == "telefono_celular"


def test_la_evidencia_dice_si_decidio_la_cercania():
    relleno = "detalle administrativo del tramite " * 4
    texto = (f"Telefono: 3233183344 en el membrete\n{relleno}\n"
             f"{NOMBRE} Telefono: 3100286057")
    h, _ = extraer_telefono(texto, nombre_afectado=NOMBRE)
    assert "caracteres del nombre del afectado" in h.evidencia
    assert h.metodo.endswith("_junto_al_nombre")


def test_dos_telefonos_igual_de_cerca_no_se_deciden_por_cercania():
    """
    Con los dos a distancia parecida del nombre, elegir seria adivinar: se
    conserva el orden de siempre y la evidencia no habla de cercania.
    """
    texto = f"Telefono: 3233183344\n{NOMBRE}\nTelefono: 3100286057"
    h, _ = extraer_telefono(texto, nombre_afectado=NOMBRE)
    assert "caracteres del nombre" not in h.evidencia
    assert h.metodo == "telefono_celular"


def test_un_solo_telefono_no_necesita_desempate():
    h, _ = extraer_telefono(f"{NOMBRE} celular 3100286057",
                            nombre_afectado=NOMBRE)
    assert h.valor == "3100286057"


def test_sin_telefono_se_reporta_el_motivo():
    _, nr = extraer_telefono(f"{NOMBRE} solicita una cita",
                             nombre_afectado=NOMBRE)
    assert nr.motivo == "SIN_TELEFONO"


# ── El bloque de datos de un tercero nombrado ──

def test_el_telefono_del_jefe_inmediato_no_es_del_afectado():
    """
    El FURAT lleva un campo para el jefe inmediato. Su telefono esta rotulado
    igual que el del trabajador y puede quedar mas cerca del nombre, asi que ni
    el rotulo ni la cercania lo distinguen: lo distingue la seccion.
    """
    texto = (
        "II INFORMACION DE LA PERSONA QUE SE ACCIDENTO\n"
        f"Primer nombre {NOMBRE}\nTelefono\n3043987367\n"
        "IV DESCRIPCION DEL ACCIDENTE\n"
        "Datos del jefe inmediato\nNombres Completo\nJOSE GOMEZ\n"
        "Telefono\n3190756808\n"
    )
    numeros = [t["numero"] for t in extraer_telefonos(texto)]
    assert "3190756808" not in numeros
    assert "3043987367" in numeros


def test_el_celular_del_membrete_del_medico_no_es_del_afectado():
    """
    Una historia clinica cierra con la firma del medico, donde "R.M." es su
    registro medico. Ese celular lleva su rotulo y pasa las demas reglas.
    """
    texto = (
        f"Nombre paciente :{NOMBRE}\nTelefono :3502092062\n"
        "## Pagina 2\n"
        "IVAN DAVID MAYA RODRIGUEZ\nR.M. 3171040492\n"
        "Calle 20 #31B-40 Oficina 201 - Pasto, Colombia - Cel: 3053322665\n"
    )
    numeros = [t["numero"] for t in extraer_telefonos(texto)]
    assert "3053322665" not in numeros
    assert "3502092062" in numeros


def test_el_numero_que_tambien_sale_fuera_del_bloque_se_conserva():
    """
    Basta una aparicion fuera: el mismo telefono puede estar en el membrete y
    tambien en los datos del paciente, y entonces si es el suyo.
    """
    texto = (
        f"Nombre paciente :{NOMBRE}\nCelular :3053322665\n"
        "## Pagina 2\n"
        "IVAN DAVID MAYA RODRIGUEZ\nR.M. 3171040492\n"
        "Oficina 201 - Pasto - Cel: 3053322665\n"
    )
    assert "3053322665" in [t["numero"] for t in extraer_telefonos(texto)]


def test_una_mencion_en_prosa_al_jefe_no_abre_ningun_bloque():
    """
    "mi jefe inmediato" narrando el accidente no es un rotulo de seccion: sin
    esa distincion el tramo se come la firma del propio afectado.
    """
    texto = (
        "El sabado mi jefe inmediato, la Directora Comercial, me convoco a una "
        "brigada. Por lo anterior exijo respuesta.\n"
        f"{NOMBRE}\nC.C. 8116088965\nTelefono: 3111701816\n"
    )
    assert "3111701816" in [t["numero"] for t in extraer_telefonos(texto)]
