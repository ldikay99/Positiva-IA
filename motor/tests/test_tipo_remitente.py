"""
Tipo de remitente por identidad o por la voz del texto, no por interpretacion.

Se resuelve cuando el remitente y el afectado resultan ser la misma persona
—mismo documento, mismo nombre— o cuando quien escribe habla de SU propio
tramite. Distinguir familiar de tercero exige leer la relacion y va al modelo.
"""
import pytest

from pipeline.extraccion import (
    TIPO_REMITENTE_AFECTADO,
    extraer_tipo_remitente,
    extraer_todo,
)


# ── Se resuelve por identidad ──

def test_el_mismo_documento_lo_fija_como_afectado():
    h, nr = extraer_tipo_remitente(
        documento_afectado="41234567", documento_remitente="41234567")
    assert h is not None, nr
    assert h.valor == TIPO_REMITENTE_AFECTADO
    assert h.metodo == "mismo_documento_que_el_afectado"


@pytest.mark.parametrize("uno,otro", [
    ("41.234.567", "41234567"),
    ("CC 41234567", "41234567"),
])
def test_los_documentos_se_comparan_solo_por_sus_digitos(uno, otro):
    h, _ = extraer_tipo_remitente(
        documento_afectado=uno, documento_remitente=otro)
    assert h is not None and h.valor == TIPO_REMITENTE_AFECTADO


def test_el_mismo_nombre_tambien_lo_fija():
    h, nr = extraer_tipo_remitente(
        nombre_afectado="PEDRO RUIZ MEJIA", nombre_remitente="Pedro Ruíz Mejía")
    assert h is not None, nr
    assert h.valor == TIPO_REMITENTE_AFECTADO
    assert h.metodo == "mismo_nombre_que_el_afectado"


def test_el_documento_manda_sobre_el_nombre():
    """Dos personas pueden llamarse igual; un documento identifica a una."""
    h, _ = extraer_tipo_remitente(
        documento_afectado="41234567", documento_remitente="41234567",
        nombre_afectado="PEDRO RUIZ", nombre_remitente="ANA LOPEZ")
    assert h.metodo == "mismo_documento_que_el_afectado"


# ── La voz del texto: quien habla de su propio tramite ──

@pytest.mark.parametrize("texto,marca", [
    ("Solicito aclarar mi afiliacion a la ARL", "mi afiliacion"),
    ("les pido que me consignen el dinero", "me consignen"),
    ("adjunto certificacion de mi cuenta pensional", "mi cuenta"),
    ("necesito el certificado de mis ingresos", "mis ingresos"),
    ("soy el afiliado y requiero la autorizacion", "soy el afiliad"),
])
def test_hablar_de_su_propio_tramite_lo_fija_como_afectado(texto, marca):
    h, nr = extraer_tipo_remitente(texto_del_remitente=texto)
    assert h is not None, nr.motivo if nr else ""
    assert h.valor == TIPO_REMITENTE_AFECTADO
    assert h.metodo == "habla_de_su_propio_tramite"
    assert marca in h.evidencia


@pytest.mark.parametrize("texto", [
    "el accidente de mi trabajadora PATRICIA MONA",
    "radico en nombre del señor PEDRO RUIZ",
    "adjunto la incapacidad del trabajador",
    "yo le ayudo con los tramites de las afiliaciones",
    # Nombrar al afectado con articulo es la misma evidencia que con "del"
    "el trabajador PEDRO RUIZ MEJIA sufrio un accidente",
    "la paciente requiere autorizacion urgente",
])
def test_hablar_del_tramite_de_otro_no_resuelve(texto):
    """Sin identidad ni voz propia, la relacion la lee el modelo."""
    h, nr = extraer_tipo_remitente(texto_del_remitente=texto)
    assert h is None
    assert nr.motivo == "REQUIERE_CLASIFICACION_SEMANTICA"


def test_las_dos_voces_a_la_vez_se_escalan():
    """
    Un empleador que escribe "mi trabajadora" y tambien "mi solicitud" mezcla
    las dos voces: decidir cual manda seria interpretar.
    """
    h, nr = extraer_tipo_remitente(
        texto_del_remitente="Sobre mi solicitud por el accidente de mi trabajadora")
    assert h is None
    assert nr.motivo == "HABLA_DE_SU_TRAMITE_Y_DEL_DE_OTRO"
    assert "mi solicitud" in nr.detalle and "mi trabajadora" in nr.detalle


def test_soy_el_afiliado_es_primera_persona_no_tercera():
    """"soy el afiliado" contiene "el afiliado", que es la marca de tercera persona."""
    h, nr = extraer_tipo_remitente(
        texto_del_remitente="soy el afiliado y requiero la autorizacion")
    assert h is not None, nr.motivo if nr else ""
    assert h.valor == TIPO_REMITENTE_AFECTADO


def test_la_identidad_manda_sobre_la_voz():
    """El documento identifica; la voz solo indica de quien es el tramite."""
    h, _ = extraer_tipo_remitente(
        documento_afectado="41234567", documento_remitente="41234567",
        texto_del_remitente="el accidente de mi trabajadora")
    assert h is not None
    assert h.metodo == "mismo_documento_que_el_afectado"


def test_la_voz_no_se_lee_del_hilo_citado():
    """
    Lo que va detras del arranque del hilo lo escribio otro. En un reenvio de
    Positiva, tomar su "mi" convertiria a cualquiera en el afectado.
    """
    e = extraer_todo(
        asunto="RV: envio de documentos",
        cuerpo=("Se remite para radicacion.\n"
                "De: ciudadano <c@empresa.com>\n"
                "Asunto: mi caso\nQuiero saber como va mi caso"),
        cuerpo_actual="Se remite para radicacion.",
        from_name="Grupo de Medicina Laboral",
        from_address="medicinalaboral@positiva.gov.co",
    )
    h = e.hallazgos.get("tipo_remitente")
    assert h is None or h.metodo != "habla_de_su_propio_tramite"


# ── Un documento de organizacion no fija la relacion ──

@pytest.mark.parametrize("tipo", [10, 8, 9])
def test_un_nit_compartido_no_convierte_al_remitente_en_el_afectado(tipo):
    """
    Cuando la empresa escribe sobre sus propios aportes, su NIT sale como
    documento del afectado y del remitente. Quien escribe es una persona en
    nombre de la empresa, y el empleador es el tercero tipico: dar por hecho
    que es el afectado clasificaria mal todo correo de empleador.
    """
    h, nr = extraer_tipo_remitente(
        documento_afectado="901642982", documento_remitente="901642982",
        tipo_documento_afectado=tipo)
    assert h is None, f"resolvio {h.valor if h else ''} con un documento de empresa"
    assert nr.motivo == "DOCUMENTO_COMPARTIDO_DE_ORGANIZACION"
    assert "901642982" in nr.detalle


@pytest.mark.parametrize("tipo", [1, 2, 6, 11, None])
def test_un_documento_de_persona_si_fija_la_relacion(tipo):
    h, nr = extraer_tipo_remitente(
        documento_afectado="41234567", documento_remitente="41234567",
        tipo_documento_afectado=tipo)
    assert h is not None, nr
    assert h.valor == TIPO_REMITENTE_AFECTADO


# ── La firma que se declara empleador ──

def test_una_firma_de_empleador_no_deja_concluir_que_es_el_afectado():
    """
    Cuando el afectado no aparece en el correo, su documento acaba tomandose de
    la firma del remitente y los dos coinciden. Si esa firma se declara
    empleador, la coincidencia es circular y no dice que sean la misma persona.
    """
    h, nr = extraer_tipo_remitente(
        documento_afectado="93659751", documento_remitente="93659751",
        tipo_documento_afectado=1, firma_de_empleador=True)
    assert h is None
    assert nr.motivo == "LA_FIRMA_SE_DECLARA_EMPLEADOR"
    assert "93659751" in nr.detalle


def test_sin_firma_de_empleador_la_coincidencia_si_vale():
    h, _ = extraer_tipo_remitente(
        documento_afectado="41234567", documento_remitente="41234567",
        tipo_documento_afectado=1, firma_de_empleador=False)
    assert h is not None and h.valor == TIPO_REMITENTE_AFECTADO


@pytest.mark.parametrize("cuerpo,esperado", [
    ("Atentamente,\nMIGUEL ALZATE TORRES\nC.C. 93659751\nEMPLEADOR", True),
    ("Cordialmente, ANA LOPEZ en calidad de empleador de la trabajadora", True),
    ("Atentamente,\nANA LOPEZ RUIZ\nC.C. 41234567\nAdministrador Operativo",
     False),
    ("Cordialmente, ANA LOPEZ RUIZ C.C. 41234567", False),
])
def test_se_reconoce_la_firma_de_empleador(cuerpo, esperado):
    from pipeline.extraccion import firma_declara_empleador

    assert firma_declara_empleador(cuerpo) is esperado


def test_representante_legal_no_cuenta_como_declaracion_de_tercero():
    """Quien firma como representante legal de su propia empresa puede ser el afectado: el cargo no dice la relacion."""
    from pipeline.extraccion import firma_declara_empleador

    cuerpo = ("Cordialmente, YAMILE PENARANDA PARRA C.C. No. 61008067 "
              "Representante Legal MINERALS YAMILE PENARANDA S.A.S.")
    assert firma_declara_empleador(cuerpo) is False


# ── Se escala cuando no hay identidad ──

def test_documentos_distintos_se_escalan():
    h, nr = extraer_tipo_remitente(
        documento_afectado="41234567", documento_remitente="76530545",
        nombre_afectado="PEDRO RUIZ", nombre_remitente="ANA LOPEZ")
    assert h is None
    assert nr.motivo == "REQUIERE_CLASIFICACION_SEMANTICA"


def test_sin_ningun_dato_se_escala():
    h, nr = extraer_tipo_remitente()
    assert h is None
    assert nr.motivo == "REQUIERE_CLASIFICACION_SEMANTICA"


def test_un_solo_documento_no_alcanza():
    """Tener el del afectado y no el de quien escribe no dice la relacion."""
    h, nr = extraer_tipo_remitente(documento_afectado="41234567")
    assert h is None
    assert nr.motivo == "REQUIERE_CLASIFICACION_SEMANTICA"


def test_el_motivo_dice_que_falto():
    _, nr = extraer_tipo_remitente(
        documento_afectado="41234567", documento_remitente="76530545")
    assert "identidad" in nr.detalle or "relacion" in nr.detalle


# ── Coherencia con la compuerta ──

def test_no_contradice_a_la_compuerta():
    """
    La compuerta rechaza un tipo 3 con nombres distintos y corrige a 3 cuando
    coinciden. El extractor usa el mismo criterio, asi que lo que resuelve
    nunca dispara un reparo.
    """
    from pipeline.compuerta import Veredicto, validar
    from pipeline.extraccion import Hallazgo

    def h(campo, valor):
        return Hallazgo(campo, valor, "evidencia", 0.9)

    hallazgos = {
        "nombre_afectado": h("nombre_afectado", "PEDRO RUIZ MEJIA"),
        "nombre_remitente": h("nombre_remitente", "PEDRO RUIZ MEJIA"),
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
        "numero_documento_remitente": h("numero_documento_remitente", "41234567"),
        "tipo_documento_afectado": h("tipo_documento_afectado", 1),
        "tipo_solicitud": h("tipo_solicitud", 16),
    }
    resuelto, _ = extraer_tipo_remitente(
        documento_afectado="41234567", documento_remitente="41234567",
        nombre_afectado="PEDRO RUIZ MEJIA", nombre_remitente="PEDRO RUIZ MEJIA")
    hallazgos["tipo_remitente"] = h("tipo_remitente", resuelto.valor)

    d = validar(hallazgos)
    assert d.veredicto is not Veredicto.RECHAZADO, [r.codigo for r in d.reparos]
    assert not any(r.campo == "tipo_remitente" for r in d.reparos), \
        [r.codigo for r in d.reparos]


# ── Enganche con la extraccion completa ──

def test_queda_resuelto_cuando_el_correo_lo_permite():
    e = extraer_todo(
        asunto="Autorizacion medica",
        cuerpo=("Cordial saludo. Yo, PEDRO RUIZ MEJIA, identificado con cedula "
                "de ciudadania 41234567, solicito la autorizacion.\n"
                "El paciente PEDRO RUIZ MEJIA con C.C. 41234567 requiere cita."),
        from_name="PEDRO RUIZ MEJIA",
        from_address="pedro@empresa-uno.com",
    )
    assert e.hallazgos["tipo_remitente"].valor == TIPO_REMITENTE_AFECTADO


def test_sigue_apareciendo_como_pendiente_si_no_se_resuelve():
    e = extraer_todo(
        asunto="Solicitud",
        cuerpo="Buen dia, adjunto los soportes solicitados.",
        from_name="Ana Lopez",
        from_address="ana@empresa-uno.com",
    )
    assert "tipo_remitente" in e.no_resueltos
