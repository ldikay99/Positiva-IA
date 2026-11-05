"""
Ningun valor por defecto del payload puede viajar sin quedar anotado.

El radicador rellena campos DESPUES de la compuerta, asi que ella no puede ver
lo que se inventa aqui. Lo unico que separa un valor por defecto de un dato
leido del correo es la linea en `suposiciones`, y esa linea es la que sale en
el informe del correo.

La comprobacion es estructural: lee el codigo del radicador en vez de llevar
una lista escrita a mano, que se desactualiza en cuanto alguien agrega un
campo.
"""
import ast
from pathlib import Path

import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline.extraccion import Hallazgo
from pipeline.radicador import armar_payload

FUENTE = Path(__file__).resolve().parent.parent / "pipeline" / "radicador.py"


def _llamadas_a_valor():
    """
    Cada llamada a `_valor`, con su campo y si pasa `suposiciones`.

    Un defecto vacio ("" o None) no cuenta: es la ausencia del dato, no un
    valor inventado, y `_valor` ya se salta la anotacion en ese caso. Lo que
    hay que vigilar son los defectos con contenido.
    """
    arbol = ast.parse(FUENTE.read_text(encoding="utf-8"))
    salida = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        if getattr(nodo.func, "id", None) != "_valor":
            continue
        # _valor(hallazgos, campo, defecto=None, suposiciones=None)
        campo = None
        if len(nodo.args) >= 2 and isinstance(nodo.args[1], ast.Constant):
            campo = nodo.args[1].value

        defecto = None
        if len(nodo.args) >= 3:
            defecto = nodo.args[2]
        else:
            defecto = next((k.value for k in nodo.keywords
                            if k.arg == "defecto"), None)
        vacio = isinstance(defecto, ast.Constant) and defecto.value in (None, "")
        tiene_defecto = defecto is not None and not vacio

        pasa_suposiciones = len(nodo.args) >= 4 or any(
            k.arg == "suposiciones" for k in nodo.keywords)
        salida.append((campo, tiene_defecto, pasa_suposiciones, nodo.lineno))
    return salida


def test_el_radicador_usa_valor_para_leer_los_hallazgos():
    assert _llamadas_a_valor(), "no se encontro ninguna llamada a _valor"


def test_todo_valor_por_defecto_se_anota_en_suposiciones():
    """Una llamada con valor por defecto y sin suposiciones mete un dato inventado en el payload sin dejar rastro."""
    sin_anotar = [
        f"linea {linea}: campo {campo!r}"
        for campo, tiene_defecto, pasa, linea in _llamadas_a_valor()
        if tiene_defecto and not pasa
    ]
    assert not sin_anotar, (
        "estos valores por defecto viajan sin anotarse:\n  "
        + "\n  ".join(sin_anotar))


# ── La comprobacion estructural, contra el comportamiento real ──

def _email():
    return EmailMessage(
        id="c1", subject="Pago de incapacidad",
        body=EmailBody(content="cuerpo"),
        from_address=EmailAddress(name="Ana Lopez", address="ana@empresa.com"),
    )


def _hallazgos_completos():
    def h(campo, valor):
        return Hallazgo(campo, valor, "evidencia", 0.9)

    return {
        "nombre_afectado": h("nombre_afectado", "PEDRO RUIZ MEJIA"),
        "nombre_remitente": h("nombre_remitente", "PEDRO RUIZ MEJIA"),
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
        "tipo_documento_afectado": h("tipo_documento_afectado", 1),
        "numero_documento_remitente": h("numero_documento_remitente", "41234567"),
        "tipo_documento_remitente": h("tipo_documento_remitente", 1),
        "tipo_remitente": h("tipo_remitente", 3),
        "tipo_solicitud": h("tipo_solicitud", 16),
        "sexo_afectado": h("sexo_afectado", 1),
        "telefono_afectado": h("telefono_afectado", "3105551234"),
        "oficina": h("oficina", 2),
    }


CAMPOS_CON_DEFECTO = [
    "tipo_remitente", "tipo_solicitud", "sexo_afectado", "oficina",
    "tipo_documento_afectado",
]


@pytest.mark.parametrize("campo", CAMPOS_CON_DEFECTO)
def test_al_quitar_un_campo_su_defecto_queda_anotado(campo):
    hallazgos = _hallazgos_completos()
    del hallazgos[campo]
    suposiciones = []
    armar_payload(_email(), hallazgos, suposiciones=suposiciones)
    assert any(campo in s for s in suposiciones), (
        f"{campo} se relleno por defecto sin anotarse: {suposiciones}")


def test_con_todo_resuelto_no_se_inventa_nada_del_afectado():
    """
    Departamento y municipio no vienen en el correo y siempre se suponen; lo
    que no puede aparecer es una suposicion sobre un campo que si se resolvio.
    """
    suposiciones = []
    armar_payload(_email(), _hallazgos_completos(), suposiciones=suposiciones)
    for campo in CAMPOS_CON_DEFECTO + ["numero_documento_remitente",
                                       "tipo_documento_remitente"]:
        assert not any(campo in s for s in suposiciones), \
            f"{campo} estaba resuelto y aun asi se anoto: {suposiciones}"


def test_el_municipio_no_se_inventa_al_serializar():
    """
    Quien decide el municipio es `_geografia`, y lo deja en las suposiciones.
    Sustituirlo en la serializacion afirma donde vive la persona en el ultimo
    paso, donde ya no queda rastro en el informe.
    """
    from models.radicacion import RadicacionPayload

    d = RadicacionPayload(
        datos_afectado_municipio_id_municipio=None,
    ).to_form_data()

    assert d["datosAfectadoMunicipioIdMunicipio"] is None


def test_el_municipio_que_se_decidio_llega_tal_cual():
    from models.radicacion import RadicacionPayload

    d = RadicacionPayload(
        datos_afectado_municipio_id_municipio=1058,
    ).to_form_data()

    assert d["datosAfectadoMunicipioIdMunicipio"] == 1058
