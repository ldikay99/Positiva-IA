"""
Ningun campo del payload queda huerfano.

Un huerfano es un campo que el radicador envia a SGDEA y que ninguna de las tres
fuentes puede llenar: no lo resuelve el algoritmo, no aparece como pendiente —de
modo que al agente nunca se le pide— y no lo aporta el registro de personas. Su
valor por defecto llegaria a produccion como si fuera un dato leido del correo.

La comprobacion lee los campos directamente del codigo del radicador, para no
depender de una lista escrita a mano.
"""
import re
from pathlib import Path

import pytest

from pipeline.extraccion import extraer_todo
from servidor_mcp.herramientas import CAMPOS_REPORTABLES

RADICADOR = Path(__file__).resolve().parent.parent / "pipeline" / "radicador.py"

# `oficina` se deriva de tipo_solicitud DESPUES del agente, en el orquestador:
# no puede estar entre los pendientes porque depende de algo que aun no existe.
DERIVADOS = {"oficina"}


def _campos_que_el_radicador_lee():
    fuente = RADICADOR.read_text(encoding="utf-8")
    directos = set(re.findall(r'_valor\(hallazgos,\s*"([a-z_]+)"', fuente))
    # `_documento_del_remitente(hallazgos, "numero_documento", ...)`
    for prefijo in re.findall(
        r'_documento_del_remitente\(hallazgos,\s*"([a-z_]+)"', fuente
    ):
        directos.add(f"{prefijo}_remitente")
    return directos - DERIVADOS


@pytest.fixture(scope="module")
def extraccion():
    return extraer_todo(
        asunto="Pago de incapacidad",
        cuerpo="Incapacidad del trabajador PEDRO RUIZ con CC 41234567",
        from_name="Ana Lopez", from_address="ana@empresa.com",
    )


def test_el_radicador_lee_campos_de_verdad():
    """Si el regex dejara de encontrar nada, la prueba pasaria sin mirar nada."""
    campos = _campos_que_el_radicador_lee()
    assert len(campos) >= 8, campos
    assert "nombre_afectado" in campos


def test_ningun_campo_del_payload_queda_sin_resolver_ni_pedir(extraccion):
    from pipeline.personas import CAMPOS_DEL_REGISTRO

    del_registro = {campo for campo, _ in CAMPOS_DEL_REGISTRO}
    conocidos = (set(extraccion.hallazgos) | set(extraccion.no_resueltos)
                 | del_registro)
    huerfanos = sorted(_campos_que_el_radicador_lee() - conocidos)
    assert not huerfanos, (
        f"campos que el radicador envia y nadie resuelve ni pide: {huerfanos}. "
        "Van a viajar a SGDEA con su valor por defecto."
    )


def test_lo_que_solo_aporta_el_registro_no_se_le_pide_al_agente():
    """
    La geografia no esta en el correo casi nunca y el agente no la puede
    inventar: sale del registro o va por defecto. Pedirsela gastaria una tarea
    para recibir la misma ausencia.
    """
    from pipeline.personas import CAMPOS_DEL_REGISTRO
    from servidor_mcp.herramientas import CAMPOS_REPORTABLES

    solo_del_registro = {"departamento_afectado", "municipio_afectado"}
    assert solo_del_registro <= {c for c, _ in CAMPOS_DEL_REGISTRO}
    assert not solo_del_registro & set(CAMPOS_REPORTABLES)


def test_todo_pendiente_lo_puede_reportar_la_ia(extraccion):
    """
    Si un campo se marca pendiente pero no esta en CAMPOS_REPORTABLES, se le
    pide a la IA algo que la herramienta le va a rechazar.
    """
    imposibles = sorted(
        c for c in extraccion.no_resueltos
        if c not in CAMPOS_REPORTABLES and c not in DERIVADOS
    )
    assert not imposibles, (
        f"se le piden a la IA campos que no puede reportar: {imposibles}")


def test_lo_pendiente_llega_a_la_tarea_del_agente(extraccion):
    tarea = extraccion.resumen_para_ia()
    for campo in extraccion.no_resueltos:
        assert campo in tarea, f"{campo} esta pendiente pero no se le pide"


# ── Las constantes del contrato con SGDEA ──

def test_el_payload_no_lleva_numeros_sueltos():
    """
    Un literal en la llamada a RadicacionPayload no dice de donde salio ni por
    que. Nombrarlo obliga a escribir el motivo al lado, que es lo unico que
    permite revisarlo despues.
    """
    fuente = RADICADOR.read_text(encoding="utf-8")
    cuerpo = fuente.split("return RadicacionPayload(")[1].split("\n    )")[0]
    sueltos = re.findall(r"^\s*(\w+)=(\d+|True|False|\"\[\d+\]\"),\s*$",
                         cuerpo, re.MULTILINE)
    assert not sueltos, (
        f"campos con valor literal en el payload: {sueltos}. "
        "Declararlos arriba con su motivo."
    )


def test_las_constantes_del_contrato_estan_fijadas():
    """
    No sustituyen a ningun dato del correo: son siempre lo mismo para este
    canal. Fijarlas aqui hace que cambiarlas sea deliberado.
    """
    from pipeline import radicador as r

    assert r.CANAL_EMAIL_IA == 7
    assert r.TIPO_PRODUCTO_ARL == 15
    assert r.USUARIO_CREADOR == "Asignador IA"
    assert r.LISTADO_CONDICIONES == "[1]"
    assert r.DESCRIPCION_ACCESO_INFORMACION == 2
    assert r.MEDIO_RESPUESTA_CORREO == 1


def test_lo_que_no_se_declara_no_se_afirma():
    """
    Ningun correo de PQRD dice si la persona es LGBTIQ+ ni si su peticion es
    de acceso a informacion publica. Se envian en falso porque el campo es
    obligatorio, no porque se sepa.
    """
    from pipeline import radicador as r

    assert r.LGTBIQ_NO_DECLARADO is False
    assert r.ACCESO_INFORMACION_PUBLICA is False


def test_los_defectos_que_tapan_un_dato_faltante_se_anotan():
    """
    La distincion es la que importa: una constante del contrato no ensucia el
    informe, pero un valor que reemplaza a un dato que no se pudo extraer si
    tiene que aparecer.
    """
    from models.email import EmailAddress, EmailBody, EmailMessage
    from pipeline.radicador import armar_payload

    email = EmailMessage(
        id="c1", subject="Solicitud", body=EmailBody(content="cuerpo"),
        from_address=EmailAddress(name="Ana Lopez", address="ana@empresa.com"),
    )
    suposiciones = []
    armar_payload(email, {}, suposiciones=suposiciones)

    anotados = " ".join(suposiciones)
    for campo in ("oficina", "tipo_solicitud", "sexo_afectado"):
        assert campo in anotados, f"{campo} se relleno sin anotarlo"
    for constante in ("TIPO_PRODUCTO", "TIPO_PQRD"):
        assert constante not in anotados.upper(), (
            "una constante del contrato no deberia ensuciar el informe")
