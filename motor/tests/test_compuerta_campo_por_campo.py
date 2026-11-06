"""
La compuerta comprueba que cada campo que se envia tenga el valor que le
corresponde, y que lo que propuso el agente este en el correo.

Los casos reproducen los errores vistos en produccion con datos ficticios.
"""
import pytest

from models.enums import TipoDocumento
from pipeline.compuerta import Fuentes, Veredicto, aplicar, informe, validar
from pipeline.extraccion import Hallazgo

# NIT ficticio de una entidad publica, con su digito de verificacion (DIAN).
NIT_ENTIDAD = "8901020180"
NIT_ENTIDAD_SIN_DV = "890102018"


def _h(campo, valor, metodo="marca_textual", confianza=0.9):
    return Hallazgo(campo, valor, "cuerpo del correo", confianza, metodo)


def _ia(campo, valor):
    return _h(campo, valor, metodo="agente_ia")


def _base(**extra):
    base = {
        "numero_documento_afectado": _h("numero_documento_afectado", "41234567"),
        "nombre_afectado": _h("nombre_afectado", "PEDRO RUIZ MEJIA"),
        "nombre_remitente": _h("nombre_remitente", "Ana Lopez Torres"),
        "tipo_solicitud": _h("tipo_solicitud", 16),
        "tipo_remitente": _h("tipo_remitente", 2),
        "tipo_documento_afectado": _h("tipo_documento_afectado", TipoDocumento.CC),
    }
    for k, v in extra.items():
        if v is None:
            base.pop(k, None)
        else:
            base[k] = v
    return base


CORREO = """
Asunto: Solicitud de pago de incapacidad
Cordial saludo. Escribe Ana Lopez Torres, auxiliar de nomina de
COMERCIALIZADORA EL ROBLE SAS, NIT 890.102.018-0.
El trabajador PEDRO RUIZ MEJIA, identificado con C.C. 41.234.567, solicita el
pago de su incapacidad. Celular: 310 555 1234. Direccion: Calle 10 # 5-20,
barrio Centro.
Radicado No. 2026123456
"""


def _codigos(d):
    return {(r.codigo, r.campo, r.bloqueante) for r in d.reparos}


# ── Sustento: lo del agente tiene que estar en el correo ──

def test_lo_del_agente_que_esta_en_el_correo_pasa():
    d = validar(_base(
        nombre_afectado=_ia("nombre_afectado", "Pedro Ruiz Mejía"),
        numero_documento_afectado=_ia("numero_documento_afectado", "41234567"),
        telefono_afectado=_ia("telefono_afectado", "3105551234"),
        direccion_afectado=_ia("direccion_afectado", "Calle 10 # 5-20"),
    ), fuentes=CORREO)
    assert d.veredicto is Veredicto.APROBADO, informe(d)


def test_nombre_del_agente_en_otro_orden_esta_sustentado():
    d = validar(_base(nombre_afectado=_ia("nombre_afectado", "RUIZ MEJIA PEDRO")),
                fuentes=CORREO)
    assert d.veredicto is Veredicto.APROBADO, informe(d)


def test_nombre_inventado_por_el_agente_va_a_manual():
    # El agente completa un apellido que el correo no trae.
    d = validar(_base(nombre_afectado=_ia("nombre_afectado", "PEDRO RUIZ MEJIA SALAS")),
                fuentes=CORREO)
    assert d.veredicto is Veredicto.RECHAZADO
    assert ("SIN_SUSTENTO_EN_EL_CORREO", "nombre_afectado", True) in _codigos(d)


def test_palabras_del_nombre_dispersas_por_el_correo_no_lo_sustentan():
    texto = "PEDRO llamo ayer. " + "relleno " * 30 + "RUIZ " + "x " * 30 + "MEJIA"
    d = validar(_base(nombre_afectado=_ia("nombre_afectado", "PEDRO RUIZ MEJIA")),
                fuentes=texto)
    assert ("SIN_SUSTENTO_EN_EL_CORREO", "nombre_afectado", True) in _codigos(d)


def test_entidad_deducida_del_dominio_no_esta_sustentada():
    # "Universidad del Atlantico" deducida de "uniatlantico.edu.co".
    d = validar(_base(
        nombre_remitente=_ia("nombre_remitente", "Universidad del Atlantico")),
        fuentes="De: sst@uniatlantico.edu.co\nAdjunto la solicitud de PEDRO RUIZ MEJIA")
    assert ("SIN_SUSTENTO_EN_EL_CORREO", "nombre_remitente", True) in _codigos(d)


def test_documento_del_agente_se_compara_sin_puntos_ni_espacios():
    d = validar(_base(
        numero_documento_afectado=_ia("numero_documento_afectado", "41234567")),
        fuentes="cedula 41 234 567")
    assert not any(r.codigo == "SIN_SUSTENTO_EN_EL_CORREO" for r in d.reparos)


def test_documento_inventado_va_a_manual():
    d = validar(_base(
        numero_documento_afectado=_ia("numero_documento_afectado", "52987654")),
        fuentes=CORREO)
    assert d.veredicto is Veredicto.RECHAZADO
    assert ("SIN_SUSTENTO_EN_EL_CORREO", "numero_documento_afectado", True) in _codigos(d)


def test_telefono_o_direccion_sin_sustento_se_envian_vacios():
    d = validar(_base(
        telefono_afectado=_ia("telefono_afectado", "3209998877"),
        direccion_afectado=_ia("direccion_afectado", "Carrera 45 # 12-30"),
    ), fuentes=CORREO)
    assert d.veredicto is Veredicto.CORREGIDO, informe(d)
    assert d.correcciones["telefono_afectado"] == ""
    assert d.correcciones["direccion_afectado"] == ""


def test_documento_del_remitente_sin_sustento_se_retira_con_su_tipo():
    d = validar(_base(
        numero_documento_remitente=_ia("numero_documento_remitente", "79111222"),
        tipo_documento_remitente=_h("tipo_documento_remitente", 1),
    ), fuentes=CORREO)
    assert d.puede_radicar, informe(d)
    assert d.correcciones["numero_documento_remitente"] == ""
    assert d.correcciones["tipo_documento_remitente"] == ""


def test_lo_determinista_y_lo_del_registro_no_se_vuelven_a_buscar():
    d = validar(_base(
        nombre_afectado=_h("nombre_afectado", "PEDRO RUIZ MEJIA SALAS",
                           metodo="registro_de_sgdea")),
        fuentes=CORREO)
    assert not any(r.codigo == "SIN_SUSTENTO_EN_EL_CORREO" for r in d.reparos)


def test_sin_fuentes_no_se_comprueba_el_sustento():
    d = validar(_base(nombre_afectado=_ia("nombre_afectado", "PEDRO RUIZ MEJIA SALAS")))
    assert d.veredicto is Veredicto.APROBADO, informe(d)


def test_texto_de_pdf_con_renglones_pegados_sustenta_el_nombre():
    f = Fuentes("Nombre: Angela Maria SuarezCedula de ciudadania: 83170.518")
    assert f.contiene_palabras("Ángela María Suárez")
    assert f.contiene_cifras("83170518")


# ── Nombre: ni trozos del asunto ni cargos ──

def test_nombre_con_verbos_del_asunto_va_a_manual():
    d = validar(_base(nombre_afectado=_h("nombre_afectado", "NIEGA PERMISO SINDICAL AURA")))
    assert d.veredicto is Veredicto.RECHAZADO
    assert ("NOMBRE_CON_PALABRAS_DEL_TRAMITE", "nombre_afectado", True) in _codigos(d)


def test_cargo_delante_y_nombre_cortado_va_a_manual():
    d = validar(_base(nombre_afectado=_h("nombre_afectado", "SERVIDORA PÚBLICA LYDA ESMERALDA")))
    assert d.veredicto is Veredicto.RECHAZADO
    assert ("NOMBRE_CON_ROL_Y_CORTADO", "nombre_afectado", True) in _codigos(d)


def test_cargo_delante_de_un_nombre_completo_se_quita():
    d = validar(_base(nombre_afectado=_h(
        "nombre_afectado", "la servidora publica LUZ MARINA DIAZ ROJAS")))
    assert d.puede_radicar, informe(d)
    assert d.correcciones["nombre_afectado"] == "LUZ MARINA DIAZ ROJAS"


def test_tratamiento_delante_se_quita():
    d = validar(_base(nombre_remitente=_h("nombre_remitente", "Sra. Ana Lopez")))
    assert d.puede_radicar, informe(d)
    assert d.correcciones["nombre_remitente"] == "Ana Lopez"


def test_entidad_publica_como_afectado_con_cedula_va_a_manual():
    # "Imprenta Nacional" como afectado de un correo de un laboratorio.
    d = validar(_base(nombre_afectado=_h("nombre_afectado", "Imprenta Nacional")))
    assert d.veredicto is Veredicto.RECHAZADO
    assert ("EMPRESA_CON_DOCUMENTO_DE_PERSONA", "nombre_afectado", True) in _codigos(d)


@pytest.mark.parametrize("nombre", [
    "Gestión del Talento Humano",
    "Seguridad y Salud en el Trabajo Uniatlantico",
    "planeacion mani-casanare.gov.co",
])
def test_area_o_buzon_como_remitente_va_a_manual(nombre):
    d = validar(_base(nombre_remitente=_h("nombre_remitente", nombre)))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.campo == "nombre_remitente" and r.bloqueante for r in d.reparos)


# ── Documento: NIT de la entidad como documento de una persona ──

def test_nit_de_la_entidad_como_documento_de_la_trabajadora_va_a_manual():
    d = validar(_base(
        numero_documento_afectado=_h("numero_documento_afectado", NIT_ENTIDAD),
        tipo_documento_afectado=_h("tipo_documento_afectado", TipoDocumento.NI),
        nombre_afectado=_h("nombre_afectado", "LUZ MARINA DIAZ ROJAS"),
        numero_documento_remitente=_h("numero_documento_remitente", NIT_ENTIDAD),
    ))
    assert d.veredicto is Veredicto.RECHAZADO
    assert ("NIT_DE_ENTIDAD_EN_PERSONA", "numero_documento_afectado", True) in _codigos(d)


def test_nit_rotulado_en_el_correo_aunque_el_tipo_diga_cedula():
    d = validar(_base(
        numero_documento_afectado=_h("numero_documento_afectado", NIT_ENTIDAD_SIN_DV),
        nombre_afectado=_h("nombre_afectado", "LUZ MARINA DIAZ ROJAS"),
    ), fuentes="ALCALDIA DE VILLAFICTICIA NIT 890.102.018-0 ... LUZ MARINA DIAZ ROJAS")
    assert ("NIT_DE_ENTIDAD_EN_PERSONA", "numero_documento_afectado", True) in _codigos(d)


def test_nit_de_persona_natural_no_es_de_entidad():
    # El NIT de una persona natural es su cedula mas el digito de verificacion
    # (81367440 -> DV 8).
    d = validar(_base(
        numero_documento_afectado=_h("numero_documento_afectado", "813674408"),
        tipo_documento_afectado=_h("tipo_documento_afectado", TipoDocumento.NI),
        nombre_afectado=_h("nombre_afectado", "JHON MAURICIO GOMEZ PENA"),
    ))
    assert not any(r.codigo == "NIT_DE_ENTIDAD_EN_PERSONA" for r in d.reparos), informe(d)


def test_nit_de_entidad_en_el_remitente_persona_se_retira():
    d = validar(_base(
        numero_documento_remitente=_h("numero_documento_remitente", NIT_ENTIDAD),
        tipo_documento_remitente=_h("tipo_documento_remitente", TipoDocumento.NI),
    ))
    assert d.puede_radicar, informe(d)
    assert d.correcciones["numero_documento_remitente"] == ""
    assert d.correcciones["tipo_documento_remitente"] == ""


def test_entidad_con_su_nit_es_coherente():
    d = validar(_base(
        numero_documento_remitente=_h("numero_documento_remitente", NIT_ENTIDAD),
        tipo_documento_remitente=_h("tipo_documento_remitente", TipoDocumento.NI),
        nombre_remitente=_h("nombre_remitente", "COMERCIALIZADORA EL ROBLE SAS"),
    ))
    assert d.veredicto is Veredicto.APROBADO, informe(d)


# ── Telefono ──

def test_telefono_que_el_correo_rotula_como_documento_se_vacia():
    d = validar(_base(telefono_afectado=_h("telefono_afectado", "3105551234")),
                fuentes="Trabajador identificado con cedula 3105551234")
    assert d.puede_radicar
    assert ("TELEFONO_ES_UN_DOCUMENTO", "telefono_afectado", False) in _codigos(d)
    assert d.correcciones["telefono_afectado"] == ""


def test_telefono_rotulado_como_telefono_se_conserva():
    d = validar(_base(telefono_afectado=_h("telefono_afectado", "3105551234")),
                fuentes=CORREO)
    assert "telefono_afectado" not in d.correcciones, informe(d)


# ── Direccion ──

@pytest.mark.parametrize("direccion", [
    "Calle 10 # 5-20", "KR 45 12 30 APTO 201", "Vereda El Salado finca La Esperanza",
    "Kilometro 17 via Dinamarca", "Av. Boyaca 72-10",
])
def test_direcciones_reales_se_conservan(direccion):
    d = validar(_base(direccion_afectado=_h("direccion_afectado", direccion)))
    assert "direccion_afectado" not in d.correcciones, informe(d)


@pytest.mark.parametrize("direccion", [
    "solicito el pago de la incapacidad pendiente",
    "Gestion del Talento Humano",
])
def test_lo_que_no_es_una_direccion_se_envia_vacio(direccion):
    d = validar(_base(direccion_afectado=_h("direccion_afectado", direccion)))
    assert d.puede_radicar
    assert d.correcciones["direccion_afectado"] == ""
    assert ("DIRECCION_SIN_FORMA_DE_DIRECCION", "direccion_afectado", False) in _codigos(d)


# ── Departamento y municipio ──

def test_municipio_de_otro_departamento_se_retira_la_pareja():
    from pipeline.geografia import _catalogo

    por_departamento = _catalogo()[1]
    dep_a, dep_b = sorted(por_departamento)[:2]
    municipio_de_b = next(iter(por_departamento[dep_b].values()))
    d = validar(_base(
        departamento_afectado=_h("departamento_afectado", dep_a),
        municipio_afectado=_h("municipio_afectado", municipio_de_b),
    ))
    assert d.puede_radicar
    assert d.correcciones["departamento_afectado"] == ""
    assert d.correcciones["municipio_afectado"] == ""


def test_municipio_del_departamento_pasa():
    from pipeline.geografia import _catalogo

    dep, municipios = next(iter(_catalogo()[1].items()))
    d = validar(_base(
        departamento_afectado=_h("departamento_afectado", dep),
        municipio_afectado=_h("municipio_afectado", next(iter(municipios.values()))),
    ))
    assert d.veredicto is Veredicto.APROBADO, informe(d)


def test_las_correcciones_llegan_a_los_hallazgos():
    h = _base(direccion_afectado=_h("direccion_afectado", "Gestion del Talento Humano"))
    d = validar(h)
    assert aplicar(h, d)["direccion_afectado"].valor == ""


# ── El orquestador le pasa a la compuerta todo lo que el agente pudo leer ──

def test_fuentes_del_correo_incluyen_hilo_adjuntos_y_registro():
    from models.email import EmailAddress, EmailBody, EmailMessage
    from pipeline.orquestador import Orquestador

    class _Ws:
        def texto_completo(self):
            return "hilo: escribio MARTA GIL PARDO"

    email = EmailMessage(
        id="x", subject="Solicitud de PEDRO RUIZ", body=EmailBody(content=""),
        from_address=EmailAddress(name="Ana Lopez", address="ana@empresa.test"),
    )
    texto = Orquestador._fuentes_del_correo(
        email, "cuerpo", "adjunto: cedula 41234567", _Ws(),
        [{"persona": {"nombre": "PEDRO RUIZ MEJIA", "activo": True}}])
    f = Fuentes(texto)
    for nombre in ("PEDRO RUIZ MEJIA", "MARTA GIL PARDO", "Ana Lopez"):
        assert f.contiene_palabras(nombre)
    assert f.contiene_cifras("41234567")
