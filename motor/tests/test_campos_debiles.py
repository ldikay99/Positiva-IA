"""
tipo_solicitud, nombre_remitente, tipo_remitente y direccion_afectado: los
criterios generales que separan los pares que se confunden.

Cada prueba fija un criterio, no un correo del corpus: los textos son
inventados y solo reproducen la forma del problema.
"""
from pipeline.agente import CRITERIOS
from pipeline.extraccion import (
    desplegar_iniciales,
    extraer_direccion,
    extraer_nombre_remitente,
    extraer_tipo_solicitud,
    extraer_todo,
    Hallazgo,
    _persona_que_firma_por,
)


def _corrido(texto: str) -> str:
    return " ".join(texto.split())


# ── tipo_solicitud: la regla determinista ──

def test_la_constitucion_en_mora_es_pago_de_aportes():
    h, _ = extraer_tipo_solicitud("Re: Constitucion en Mora con Fe de Erratas",
                                  "No entiendo que quieren.")
    assert h.valor == 26
    assert not h.a_confirmar


def test_el_asunto_decide_sobre_lo_que_el_cuerpo_menciona():
    """El asunto es el titulo de la peticion; el cuerpo cuenta antecedentes."""
    h, _ = extraer_tipo_solicitud(
        "Solicitud de estado de cuenta",
        "Les recuerdo que soy pensionado y tuve una incapacidad.")
    assert h.valor == 8
    assert "asunto" in h.evidencia
    assert not h.a_confirmar


def test_dos_tramites_en_el_cuerpo_pasan_a_confirmar():
    """
    Si el cuerpo nombra tramites de dos codigos, la palabra no dice cual se
    pide: se conserva el de mas prioridad, pero el agente lo confirma.
    """
    h, _ = extraer_tipo_solicitud(
        "Solicitud", "me encuentro retirado y no soy pensionado; pido la "
                     "novedad de retiro del trabajador")
    assert h is not None
    assert h.a_confirmar
    assert "17" in h.a_confirmar and "12" in h.a_confirmar


def test_un_solo_tramite_en_el_cuerpo_no_se_confirma():
    h, _ = extraer_tipo_solicitud("", "solicito el reembolso del transporte")
    assert h.valor == 15 and not h.a_confirmar


def test_el_tipo_a_confirmar_viaja_al_agente():
    e = extraer_todo(
        asunto="Solicitud",
        cuerpo="Tengo un dictamen de perdida de capacidad laboral y pido "
               "autorizacion de cita con el especialista.",
        from_name="Ana Lopez", from_address="ana@correo.com",
    )
    assert "tipo_solicitud" in e.campos_a_confirmar
    assert "tipo_solicitud" in e.campos_para_el_agente


# ── tipo_solicitud: el criterio del agente ──

def test_el_criterio_describe_las_categorias_que_se_confunden():
    criterio = _corrido(CRITERIOS["tipo_solicitud"])
    for codigo in ("5 ", "7 ", "8 ", "26 ", "1 ", "12 ", "16 ", "24 "):
        assert codigo in criterio
    # Los desempates de los pares confundidos.
    assert "8 pide INFORMACION sobre la deuda; 26 discute o hace un PAGO" in criterio
    assert "una queja por atencion medica es 5" in criterio
    assert "12 crea o termina una afiliacion" in criterio


def test_la_forma_del_escrito_no_es_la_categoria():
    criterio = _corrido(CRITERIOS["tipo_solicitud"])
    assert "derecho de peticion" in criterio
    assert "lo que se le PIDE" in criterio


# ── tipo_remitente ──

def test_el_1_exige_un_vinculo_personal():
    criterio = _corrido(CRITERIOS["tipo_remitente"])
    assert "El 1 exige un VINCULO PERSONAL" in criterio
    assert "IPS o clinica" in criterio


# ── nombre_remitente: la persona que firma por la organizacion ──

def test_la_razon_social_del_encabezado_cede_a_quien_firma_el_mensaje():
    cuerpo = ("Reenvio para su tramite.\n"
              "De: Constructora Andina S.A.S <gestion@andina.com>\n"
              "Enviado: lunes\nAsunto: solicitud\n"
              "Solicitamos la autorizacion.\nCordialmente,\n"
              "Laura Gomez Ruiz\nCoordinadora SST\n")
    assert _persona_que_firma_por("Constructora Andina S.A.S", cuerpo) == \
        "Laura Gomez Ruiz"


def test_la_razon_social_de_la_firma_cede_a_la_persona_vecina():
    cuerpo = ("Solicitamos la autorizacion.\nAtentamente,\n"
              "Laura Gomez Ruiz\nCONSTRUCTORA ANDINA S.A.S\n")
    assert _persona_que_firma_por("Constructora Andina S.A.S", cuerpo) == \
        "Laura Gomez Ruiz"


def test_sin_persona_que_firme_se_queda_la_organizacion():
    cuerpo = ("Solicitamos la autorizacion.\nCordialmente,\n"
              "Area de Seguridad y Salud en el Trabajo\n"
              "Constructora Andina S.A.S\nTelefono: 3001234567\n")
    assert _persona_que_firma_por("Constructora Andina S.A.S", cuerpo) == ""


def test_la_carta_adjunta_dice_quien_firma_por_la_empresa():
    cuerpo = ("Cordialmente,\nArea de Seguridad y Salud en el Trabajo\n"
              "Constructora Andina S.A.S\n")
    carta = ("Solicitamos lo siguiente.\nAtentamente,\n\nLaura Gomez Ruiz\n"
             "CONSTRUCTORA ANDINA S.A.S\nResidente SST\n")
    h, _ = extraer_nombre_remitente(
        "Constructora Andina S.A.S", "gestion@andina.com", cuerpo,
        cuerpo_actual=cuerpo, texto_adjuntos=carta)
    assert h.valor == "Laura Gomez Ruiz"
    assert "es la organizacion" in h.evidencia


def test_un_nombre_de_adjunto_lejos_de_la_empresa_no_firma_por_ella():
    """En un adjunto hay mas nombres: el del trabajador no firma la carta."""
    cuerpo = "Cordialmente,\nConstructora Andina S.A.S\n"
    carta = ("CONSTRUCTORA ANDINA S.A.S\n" + "texto del caso\n" * 20
             + "Atentamente,\nPedro Perez Diaz\n")
    h, _ = extraer_nombre_remitente(
        "Constructora Andina S.A.S", "gestion@andina.com", cuerpo,
        cuerpo_actual=cuerpo, texto_adjuntos=carta)
    assert h.valor == "Constructora Andina S.A.S"


def test_el_criterio_del_agente_prefiere_a_la_persona_que_firma():
    criterio = _corrido(CRITERIOS["nombre_remitente"])
    assert "el remitente es esa persona, aunque escriba en nombre de su empresa" \
        in criterio
    assert "Reporta el nombre COMPLETO" in criterio


# ── nombre_remitente: las iniciales se despliegan ──

def _h(valor):
    return Hallazgo("nombre_remitente", valor, "firma", 0.78, "cascada")


def test_la_inicial_se_despliega_con_el_nombre_entero_del_texto():
    h = desplegar_iniciales(_h("JORGE A. BARRERA NOSSA"),
                            "Firmado: Jorge Alirio Barrera Nossa")
    assert h.valor == "JORGE ALIRIO BARRERA NOSSA"
    assert "forma completa" in h.evidencia


def test_entre_dos_despliegues_gana_el_que_mas_se_repite():
    texto = ("JORGE AIRIO BARRERA NOSSA\nJORGE ALIRIO BARRERA NOSSA\n"
             "JORGE ALIRIO BARRERA NOSSA")
    assert desplegar_iniciales(_h("JORGE A. BARRERA NOSSA"), texto).valor == \
        "JORGE ALIRIO BARRERA NOSSA"


def test_dos_despliegues_empatados_no_se_tocan():
    texto = "JORGE ANDRES BARRERA NOSSA y JORGE ALIRIO BARRERA NOSSA"
    assert desplegar_iniciales(_h("JORGE A. BARRERA NOSSA"), texto).valor == \
        "JORGE A. BARRERA NOSSA"


def test_sin_iniciales_no_se_toca_nada():
    h = _h("JORGE BARRERA")
    assert desplegar_iniciales(h, "JORGE ALIRIO BARRERA NOSSA") is h


def test_el_despliegue_no_rompe_la_identidad_con_el_afectado():
    """
    Con la inicial, el remitente es el afectado aunque el adjunto escriba su
    nombre con una errata; desplegarlo antes de decidirlo rompia el vinculo.
    """
    e = extraer_todo(
        asunto="Calificacion",
        cuerpo=("De: JORGE A. BARRERA NOSSA <jorge@correo.com>\n"
                "USUARIO: JORGE AIRIO BARRERA NOSSA CC: 73423155\n"
                "Cordialmente,\nJORGE ALIRIO BARRERA NOSSA\n"),
        texto_adjuntos="JORGE ALIRIO BARRERA NOSSA",
        from_name="JORGE A. BARRERA NOSSA", from_address="jorge@correo.com",
    )
    assert e.hallazgos["nombre_remitente"].valor == "JORGE ALIRIO BARRERA NOSSA"
    assert e.hallazgos["tipo_remitente"].valor == 3


# ── direccion_afectado: la fila de la planilla ──

def test_la_direccion_en_la_fila_del_afectado_es_suya():
    planilla = (
        "| TIPO DOC | NUMERO | NOMBRE | DIRECCION | TIPO DOC EMPLEADOR |\n"
        "Obligatorio Unicamente NIT 900123456 | MODALIDAD |\n"
        "|---|---|---|---|---|\n"
        "| CC | 41234567 | ANA LOPEZ | Direccion: Carrera 5 No 4-27 | NI |\n")
    h, nr = extraer_direccion(planilla, nombre_afectado="ANA LOPEZ",
                              documento_afectado="41234567")
    assert h is not None, nr
    assert h.valor == "Carrera 5 No 4-27"


def test_la_fila_de_otro_sujeto_sigue_descartada():
    planilla = (
        "Datos del empleador NIT 900123456\n"
        "| NIT | 900123456 | Direccion: Calle 1 # 2-3 |\n"
        "Trabajador ANA LOPEZ CC 41234567\n")
    h, _ = extraer_direccion(planilla, nombre_afectado="ANA LOPEZ",
                             documento_afectado="41234567")
    assert h is None


def test_en_la_fila_un_sujeto_ajeno_entre_el_afectado_y_la_direccion_la_descarta():
    fila = ("| CC | 41234567 | ANA LOPEZ | razon social EMPRESA SAS "
            "| Direccion: Calle 1 # 2-3 |\n")
    h, _ = extraer_direccion(fila, nombre_afectado="ANA LOPEZ",
                             documento_afectado="41234567")
    assert h is None


# ── tipo_remitente: quien firma el mensaje ──

def test_si_firma_el_afectado_es_el_aunque_el_buzon_sea_de_otro():
    e = extraer_todo(
        asunto="Reporte ARL",
        cuerpo=("Me permito adjuntar el reporte del trabajador ANA MARIA LOPEZ "
                "DIAZ CC 41234567.\nCordialmente,\n--\nAna M. Lopez Diaz\n"
                "Administradora\n"),
        from_name="Pedro Gomez Ruiz", from_address="ventas@empresa.com",
    )
    h = e.hallazgos["tipo_remitente"]
    assert h.valor == 3 and h.metodo == "firma_del_afectado"


def test_la_firma_corrida_en_la_misma_linea_tambien_cuenta():
    from pipeline.extraccion import firmantes_del_mensaje

    texto = "garantizando mi rehabilitacion.Atentamente,CARLOS JAVIER SANCHEZ SANCHEZ"
    assert "CARLOS JAVIER SANCHEZ SANCHEZ" in firmantes_del_mensaje(texto)


def test_si_firma_otra_persona_no_se_concluye_nada():
    e = extraer_todo(
        asunto="Reporte ARL",
        cuerpo=("Adjunto el reporte del trabajador ANA MARIA LOPEZ DIAZ "
                "CC 41234567.\nCordialmente,\nPedro Gomez Ruiz\nJefe de RRHH\n"),
        from_name="Pedro Gomez Ruiz", from_address="rrhh@empresa.com",
    )
    h = e.hallazgos.get("tipo_remitente")
    assert h is None or h.valor != 3
