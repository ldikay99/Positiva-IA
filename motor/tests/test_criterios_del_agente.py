"""
A la IA no se le puede pedir un campo sin decirle como decidirlo.

Un campo pendiente sin criterio se resuelve por intuicion del modelo. La
prueba es estructural: compara lo que
el pipeline marca como pendiente contra lo que el prompt explica, para que
agregar un campo obligue a escribir su criterio.
"""
import pytest

from pipeline.agente import SISTEMA
from pipeline.extraccion import extraer_todo
from servidor_mcp.herramientas import CAMPOS_REPORTABLES

# El prompt viene con saltos de linea para caber en 79 columnas, asi que una
# frase cualquiera puede estar partida. Se compara sobre el texto con los
# espacios normalizados para que reformatear el prompt no rompa las pruebas.
CORRIDO = " ".join(SISTEMA.split())


@pytest.fixture(scope="module")
def pendientes():
    e = extraer_todo(
        asunto="Pago de incapacidad",
        cuerpo="Buen dia, adjunto los soportes solicitados.",
        from_name="Ana Lopez", from_address="ana@empresa.com",
    )
    return sorted(e.no_resueltos)


def test_hay_campos_pendientes(pendientes):
    """Si no hubiera ninguno, la prueba de abajo pasaria sin mirar nada."""
    assert len(pendientes) >= 4, pendientes


def test_todo_campo_que_se_le_pide_tiene_criterio(pendientes):
    sin_criterio = [
        c for c in pendientes
        if f"\n{c} —" not in SISTEMA and f"\n{c} -" not in SISTEMA
    ]
    assert not sin_criterio, (
        f"se le piden a la IA sin explicarle como decidirlos: {sin_criterio}")


@pytest.mark.parametrize("campo", CAMPOS_REPORTABLES)
def test_todo_campo_reportable_tiene_criterio(campo):
    """
    Mas fuerte que la anterior, que solo mira los pendientes de un correo. Si
    el modelo PUEDE reportar el campo, el prompt tiene que decir con que
    criterio.
    """
    assert f"\n{campo} —" in SISTEMA or f"\n{campo} -" in SISTEMA, (
        f"'{campo}' se puede reportar y el prompt no dice con que criterio")


def test_el_criterio_del_documento_del_remitente_previene_la_contaminacion():
    """
    El error natural del modelo es reusar el documento del afectado. Es
    exactamente lo que registra a una empresa con la cedula del trabajador.
    """
    assert "NO copies el documento del afectado" in CORRIDO


def test_el_criterio_del_documento_del_remitente_admite_al_afectado_mismo():
    """Con tipo_remitente 3 son la misma persona y el extractor hereda el documento a proposito."""
    bloque = CORRIDO.split("numero_documento_remitente")[1]
    assert "es el MISMO documento del afectado" in bloque, bloque[:300]


def test_el_prompt_prohibe_deducir_el_sexo_del_nombre():
    """
    Es la unica regla del pipeline que un modelo rompe por defecto: ante
    "MARIA" o "JOSE" la inferencia es inmediata, y el dato queda falso sobre
    una persona real.
    """
    sexo = SISTEMA.split("sexo_afectado —", 1)[-1]
    assert "NUNCA lo deduzcas del nombre de pila" in " ".join(sexo.split())


def test_el_prompt_dice_que_el_remitente_no_es_quien_reenvia():
    """Cuando un buzon de Positiva reenvia, tomar al funcionario deja la PQRD a nombre de la propia entidad."""
    bloque = " ".join(SISTEMA.split("nombre_remitente —", 1)[-1].split())
    assert "no quien reenvia" in bloque
    assert "positiva.gov.co" in bloque.lower()


def test_el_prompt_advierte_del_pagador_en_un_certificado_de_aportes():
    """
    "se certifica que X realizo los siguientes aportes" nombra al empleador. El
    extractor ya lo descarta; el modelo necesita la misma regla.
    """
    bloque = " ".join(SISTEMA.split("nombre_afectado —", 1)[-1].split())
    assert "PAGADOR" in bloque


def test_el_prompt_no_invita_a_completar_digitos_del_telefono():
    """
    La regla simplificada del prompt ("diez digitos que empieza por 3") acepta
    prefijos que no existen. La compuerta los descarta, asi que reportarlos es
    trabajo perdido.
    """
    bloque = " ".join(SISTEMA.split("telefono_afectado —", 1)[-1].split())
    assert "No completes ni corrijas digitos" in bloque


def test_el_prompt_no_le_pide_el_tipo_de_pqrd():
    """
    El campo es una constante del canal. Nombrarlo en el prompt le da al modelo
    un valor que creer que puede elegir, y lo que reporte se descarta despues.
    """
    assert "tipo_pqrd" not in CORRIDO


# ── Las trampas que el extractor ya esquiva, el modelo tambien las ve ──
#
# El modelo resuelve los mismos campos que el extractor escala, sobre los
# mismos documentos. Una trampa contra la que el algoritmo ya se protege, si no
# esta en el prompt, la vuelve a pisar la IA.

def test_el_prompt_advierte_de_la_identidad_que_se_pide_anular():
    """
    Un correo de correccion trae la identidad equivocada y la correcta, y la
    equivocada lleva el rotulo mas fuerte del texto.
    """
    bloque = " ".join(SISTEMA.split("nombre_afectado —", 1)[-1].split())
    assert "anulacion" in bloque
    assert "no es la buena" in bloque


def test_el_prompt_advierte_de_las_formas_dobles_del_formulario():
    """
    "identificado(a)" y la tabla "M Masculino / F Femenino" son la plantilla y
    las opciones del papel, no una afirmacion sobre el titular.
    """
    bloque = " ".join(SISTEMA.split("sexo_afectado —", 1)[-1].split())
    assert "identificado(a)" in bloque
    assert "tabla de codigos" in bloque


def test_el_prompt_advierte_del_telefono_de_otra_persona():
    """
    El FURAT tiene un campo para el jefe inmediato y una historia clinica cierra
    con el membrete del medico: los dos llevan telefono rotulado igual.
    """
    bloque = " ".join(SISTEMA.split("telefono_afectado —", 1)[-1].split())
    assert "jefe inmediato" in bloque
    assert "R.M." in bloque


def test_el_criterio_del_telefono_no_se_queda_en_el_prefijo_3():
    """
    Con solo el 3, un fijo unificado en 60 o un numero extranjero se descartan
    aunque el plan de numeracion los acepte.
    """
    bloque = " ".join(SISTEMA.split("telefono_afectado —", 1)[-1].split())
    assert "60" in bloque


def test_los_codigos_de_tipo_remitente_del_prompt_existen():
    import re

    from servidor_mcp.herramientas import CATALOGOS

    bloque = SISTEMA.split("tipo_remitente —")[1].split("tipo_solicitud")[0]
    codigos = {int(c) for c in re.findall(r"^\s{2}(\d+)\s", bloque, re.MULTILINE)}
    validos = set(CATALOGOS.get("tipo_remitente", {}))
    assert codigos
    assert codigos <= validos, f"codigos inexistentes: {codigos - validos}"


# ── Las reglas de limpieza que el agente tiene que respetar ──

def test_el_prompt_pide_solo_digitos_en_documento_y_telefono():
    """
    Lo que llega a SGDEA no lleva separadores. Si el agente reporta
    "41.234.567" el valor viaja tal cual y queda asi en el registro.
    """
    assert SISTEMA.count("Reporta SOLO los digitos") >= 2


def test_el_prompt_prohibe_partir_un_alfanumerico_para_quedarse_con_los_digitos():
    """
    "ENT3413618689913" es un radicado y "S001223884" un recibo. Cortarles las
    letras fabrica una cedula que no existe.
    """
    for señal in ("ENT3413618689913", "S001223884", "no reportes el campo"):
        assert señal in SISTEMA


def test_el_prompt_reconoce_el_pasaporte_como_alfanumerico():
    """No toda persona atendida es colombiana."""
    assert "pasaporte, que SI es alfanumerico" in SISTEMA
    assert "AB123456" in SISTEMA


def test_el_prompt_nombra_los_telefonos_de_relleno():
    """Tienen la forma correcta y aun asi no llaman a nadie."""
    for numero in ("3333333333", "6017654821", "6027654321", "3000000000"):
        assert numero in SISTEMA


def test_el_prompt_distingue_una_direccion_ausente_de_una_rural():
    """
    Los marcadores de no-dato no son direcciones; las rurales si lo son. Las
    dos cosas hay que decirlas, porque el error va en las dos direcciones.
    """
    for marca in ("No reporta", "N/R", "undefined"):
        assert marca in SISTEMA
    for rural in ("CORREGIMIENTO", "KILOMETRO", "VEREDA"):
        assert rural in SISTEMA


def test_el_prompt_pide_cortar_la_direccion_antes_del_contacto():
    """En una linea corrida el telefono y el correo vienen pegados."""
    assert "corta antes de ellos" in SISTEMA


def test_el_prompt_advierte_de_la_razon_social_con_documento_de_persona():
    assert "razon social" in SISTEMA
    assert "no se sostiene" in SISTEMA


# ── El prompt lleva solo lo que se va a pedir ──

def test_cada_campo_reportable_tiene_su_criterio_separable():
    """
    El prompt se parte por campo para mandar solo lo pendiente. Un campo cuyo
    criterio no se pueda separar viajaria siempre o no viajaria nunca.
    """
    from pipeline.agente import CRITERIOS

    faltan = sorted(set(CAMPOS_REPORTABLES) - set(CRITERIOS))
    assert not faltan, f"sin criterio separable: {faltan}"


def test_solo_viajan_los_criterios_de_los_campos_pendientes():
    from pipeline.agente import CRITERIOS, prompt_de_sistema

    prompt = prompt_de_sistema(["sexo_afectado"])

    assert "sexo_afectado —" in prompt
    for otro in CRITERIOS:
        if otro != "sexo_afectado":
            assert f"\n{otro} —" not in prompt, otro


def test_las_reglas_generales_viajan_siempre():
    """La cabecera no depende del campo: sin ella el agente no sabe trabajar."""
    from pipeline.agente import prompt_de_sistema

    for campos in ([], ["telefono_afectado"], list(CAMPOS_REPORTABLES)):
        prompt = prompt_de_sistema(campos)
        assert "Nunca inventes" in prompt
        assert "La evidencia es obligatoria" in prompt
        assert "responde LISTO" in prompt


def test_pedir_menos_campos_acorta_el_prompt():
    """Es la razon de existir de la particion: el prompt viaja en cada vuelta."""
    from pipeline.agente import CRITERIOS, prompt_de_sistema

    uno = prompt_de_sistema(["sexo_afectado"])
    todos = prompt_de_sistema(list(CRITERIOS))

    assert len(uno) < len(todos) / 2


# ── Los catalogos viajan con la tarea ──

def test_la_tarea_lleva_los_codigos_de_los_campos_numericos_pendientes():
    """
    Pedirlos con una herramienta costaba una vuelta entera por catalogo, y el
    prompt de sistema se reenvia en cada vuelta aunque lo unico que se pida sea
    una lista de codigos que no cambia nunca.
    """
    from pipeline.agente import catalogos_de
    from servidor_mcp.herramientas import CATALOGOS

    texto = catalogos_de(["sexo_afectado"], CATALOGOS)

    assert "sexo_afectado" in texto
    assert "1=Masculino" in texto
    assert "2=Femenino" in texto


def test_solo_van_los_catalogos_de_lo_que_se_pide():
    from pipeline.agente import catalogos_de
    from servidor_mcp.herramientas import CATALOGOS

    texto = catalogos_de(["sexo_afectado"], CATALOGOS)

    assert "tipo_solicitud" not in texto
    assert "tipo_pqrd" not in texto


def test_un_campo_que_no_es_codigo_no_agrega_catalogo():
    from pipeline.agente import catalogos_de
    from servidor_mcp.herramientas import CATALOGOS

    assert catalogos_de(["nombre_afectado", "direccion_afectado"], CATALOGOS) == ""


def test_los_dos_tipos_de_documento_comparten_un_solo_catalogo():
    """Van al mismo, y listarlo dos veces seria pagar el texto dos veces."""
    from pipeline.agente import catalogos_de
    from servidor_mcp.herramientas import CATALOGOS

    texto = catalogos_de(
        ["tipo_documento_afectado", "tipo_documento_remitente"], CATALOGOS)

    assert texto.count("=Cedula de ciudadania") == 1
    assert "tipo_documento_afectado, tipo_documento_remitente:" in texto
