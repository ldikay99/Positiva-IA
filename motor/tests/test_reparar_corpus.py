"""
Pruebas del reparador de valores falsos del corpus.

Lo que se repara son valores INVENTADOS que quedaron imposibles o ambiguos, y
por tanto hacen que la medicion hable del corpus en vez del pipeline. Dos cosas
importan aqui: que la sustitucion encuentre el texto tal como esta escrito, y
que no toque nada que no haya que tocar.
"""
import pytest

from harness.caso import Caso, Esperado
from harness.reparar_corpus import aplicar, planear
from servidor_mcp.herramientas import _digito_verificacion_nit


def _caso(cuerpo="", asunto="", adjunto="", esperado=None):
    return Caso(
        id="caso-prueba", asunto=asunto,
        remitente_nombre="Alonso Montoya", remitente_correo="a@empresa-uno.com",
        cuerpo_md=cuerpo,
        adjuntos={"anexo": adjunto} if adjunto else {},
        esperado=esperado or Esperado(),
        motivo="prueba",
    )


# ── NIT con el digito de verificacion separado ──

@pytest.mark.parametrize("escrito", [
    "469921223-2",        # guion pegado
    "469921223 - 2",      # guion con espacios
    "469.921.223-2",      # con separadores de miles
])
def test_se_repara_el_digito_de_verificacion_con_guion(escrito):
    """El formato colombiano pone el digito detras de un guion."""
    r = planear(_caso(cuerpo=f"La empresa con NIT {escrito} solicita"))
    assert r.nits, f"no se detecto el NIT mal formado en {escrito!r}"
    viejo, nuevo = next(iter(r.nits.items()))
    assert viejo in f"La empresa con NIT {escrito} solicita", (
        "la clave de sustitucion no es el texto tal como esta escrito")
    correcto = _digito_verificacion_nit("469921223")
    assert nuevo.rstrip().endswith(str(correcto))


def test_se_repara_el_digito_separado_por_espacio_con_rotulo_nit():
    """Sin guion hace falta el rotulo: sin el, cualquier '1234567 8' entraria."""
    r = planear(_caso(cuerpo="POSITIVA SA NIT: 613705920 6 informa"))
    assert r.nits
    assert "613705920" in next(iter(r.nits))


def test_un_numero_con_espacio_sin_rotulo_no_se_toca():
    r = planear(_caso(cuerpo="El radicado 613705920 6 quedo registrado"))
    assert not r.nits


def test_un_nit_con_su_digito_correcto_no_se_toca():
    correcto = _digito_verificacion_nit("469921223")
    r = planear(_caso(cuerpo=f"NIT 469921223-{correcto}"))
    assert not r.nits


def test_la_sustitucion_alcanza_todos_los_textos_del_caso():
    escrito = "469921223-2"
    caso = _caso(cuerpo=f"NIT {escrito} en el cuerpo",
                 asunto=f"Solicitud NIT {escrito}",
                 adjunto=f"Certificado NIT {escrito}")
    r = planear(caso)
    reparado = aplicar(caso, r)
    correcto = str(_digito_verificacion_nit("469921223"))
    for texto in (reparado.asunto, reparado.cuerpo_md, reparado.adjuntos["anexo"]):
        assert escrito not in texto
        assert f"469921223-{correcto}" in texto


# ── Documento con forma de movil ──

def test_un_documento_con_forma_de_movil_se_repara():
    """
    Diez digitos que empiezan en 3 es la forma exacta de un movil colombiano.
    Ninguna regla determinista puede separarlos, y en la realidad no se da.
    """
    r = planear(_caso(cuerpo="La trabajadora con CC 3004709540 solicita"))
    assert "3004709540" in r.documentos
    nuevo = r.documentos["3004709540"]
    assert len(nuevo) == 10
    assert not nuevo.startswith("3")


def test_un_documento_sin_forma_de_movil_no_se_toca():
    r = planear(_caso(cuerpo="La trabajadora con CC 1004709540 solicita"))
    assert not r.documentos


def test_una_racha_con_forma_de_movil_sin_marca_de_documento_no_se_toca():
    """Sin la marca no se puede afirmar que sea un documento y no un telefono."""
    r = planear(_caso(cuerpo="Me pueden llamar al 3004709540"))
    assert not r.documentos


def test_no_se_repara_el_numero_que_tambien_aparece_como_telefono():
    """
    Si el mismo numero esta rotulado como documento y como telefono, cambiarlo
    alteraria los dos datos a la vez.
    """
    r = planear(_caso(
        cuerpo="CC 3004709540 y para contacto celular 3004709540"))
    assert not r.documentos


def test_la_reparacion_del_documento_actualiza_el_esperado():
    """Si el esperado no se actualiza, el caso deja de coincidir consigo mismo."""
    caso = _caso(cuerpo="CC 3004709540",
                 esperado=Esperado(numero_documento_afectado="3004709540"))
    r = planear(caso)
    reparado = aplicar(caso, r)
    assert reparado.esperado.numero_documento_afectado == r.documentos["3004709540"]
    assert reparado.esperado.numero_documento_afectado not in reparado.cuerpo_md \
        or "3004709540" not in reparado.cuerpo_md


# ── Telefono con indicativo de otro pais ──

def test_un_internacional_que_no_existe_se_repara():
    """Un numero extranjero sin sustituir en el corpus repite el original: hay que repararlo igual que el colombiano."""
    from utils.contacto_extractor import telefono_existe

    r = planear(_caso(cuerpo="Contacto +265840403391 para lo del caso"))
    assert "+265840403391" in r.telefonos
    nuevo = r.telefonos["+265840403391"]
    assert nuevo.startswith("+265"), nuevo
    assert telefono_existe(nuevo), nuevo


def test_el_indicativo_lo_decide_la_libreria_no_el_patron():
    """Los codigos de pais tienen uno, dos o tres digitos."""
    r = planear(_caso(cuerpo="Contacto +328049595963"))
    assert r.telefonos["+328049595963"].startswith("+32")


def test_la_reparacion_del_internacional_converge():
    """
    El reemplazo tiene que ser valido: con otro invalido, `--revisar` reportaria
    el mismo numero para siempre y la herramienta no diria nada util.
    """
    caso = _caso(cuerpo="Contacto +265840403391")
    reparado = aplicar(caso, planear(caso))
    assert not planear(reparado).hay_algo, planear(reparado).describir()


def test_un_internacional_valido_no_se_toca():
    r = planear(_caso(cuerpo="Contacto +1 202 555 0143 para lo del caso"))
    assert not r.telefonos


def test_lo_detectado_y_no_reparado_se_informa():
    """
    Callarlo dejaria pensar que el corpus quedo limpio. La herramienta sale
    distinto de cero para que no pase por una corrida buena.
    """
    from harness.reparar_corpus import Reparacion

    r = Reparacion(caso_id="c", sin_reparar=["telefono +999...: sin indicativo"])
    assert r.hay_algo
    assert any("SIN REPARAR" in l for l in r.describir())


# ── Un caso sano no se toca ──

def test_un_caso_bien_formado_no_produce_reparaciones():
    correcto = _digito_verificacion_nit("900123456")
    r = planear(_caso(
        cuerpo=f"La empresa NIT 900123456-{correcto} reporta a la trabajadora "
               f"con CC 41234567, celular 3105551234"))
    assert not r.hay_algo
