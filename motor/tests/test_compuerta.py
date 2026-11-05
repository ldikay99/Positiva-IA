"""
Validacion final de los hallazgos antes de radicar.

Las pruebas alimentan la compuerta con salidas que el modelo puede producir
cuando alucina o cuando el correo lo confunde, y comprueban el veredicto.
"""
import pytest

from models.enums import TipoDocumento
from pipeline.compuerta import (
    CONFIANZA_PARA_RADICAR,
    OBLIGATORIOS,
    Veredicto,
    aplicar,
    informe,
    validar,
)
from pipeline.extraccion import Hallazgo
from pipeline.telemetria import MOTIVOS_MANUAL


def _h(campo, valor, confianza=0.9, evidencia="cuerpo del correo"):
    return Hallazgo(campo, valor, evidencia, confianza)


def _completo(**extra):
    base = {
        "numero_documento_afectado": _h("numero_documento_afectado", "41234567"),
        "nombre_afectado": _h("nombre_afectado", "PEDRO RUIZ MEJIA"),
        "nombre_remitente": _h("nombre_remitente", "Ana Lopez"),
        "tipo_solicitud": _h("tipo_solicitud", 16),
        "tipo_remitente": _h("tipo_remitente", 2),
        "tipo_documento_afectado": _h("tipo_documento_afectado", TipoDocumento.CC),
    }
    base.update({k: v for k, v in extra.items() if v is not None})
    for k, v in extra.items():
        if v is None:
            base.pop(k, None)
    return base


# ── Deja pasar lo que esta bien ──

def test_un_caso_completo_se_aprueba():
    d = validar(_completo())
    assert d.veredicto is Veredicto.APROBADO, informe(d)
    assert d.puede_radicar


@pytest.mark.parametrize("campo", OBLIGATORIOS)
def test_falta_un_obligatorio_y_no_se_radica(campo):
    d = validar(_completo(**{campo: None}))
    assert d.veredicto is Veredicto.RECHAZADO
    assert not d.puede_radicar
    assert d.motivo_manual in MOTIVOS_MANUAL, (
        f"'{d.motivo_manual}' no esta en la taxonomia de revision manual")


# ── Alucinaciones tipicas del modelo ──

def test_un_codigo_que_no_existe_no_pasa():
    """Un modelo puede devolver 99 para tipo_remitente. SGDEA lo rechazaria."""
    d = validar(_completo(tipo_remitente=_h("tipo_remitente", 99)))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "FUERA_DE_CATALOGO" for r in d.reparos)


@pytest.mark.parametrize("nombre", [
    "ana.lopez@empresa.com",
    "https://portal.positiva.gov.co",
    "www.empresa.com",
])
def test_un_correo_o_una_url_no_es_un_nombre(nombre):
    """Una direccion de correo o una URL no identifican a una persona."""
    d = validar(_completo(nombre_remitente=_h("nombre_remitente", nombre)))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "NOMBRE_ES_CORREO_O_URL" for r in d.reparos)


@pytest.mark.parametrize("documento", ["11111111", "0000000000", "999999999"])
def test_un_documento_de_relleno_no_pasa(documento):
    """Ante la duda, un modelo inventa un numero repetido."""
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", documento)))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "DOCUMENTO_DE_RELLENO" for r in d.reparos)


@pytest.mark.parametrize("documento", ["1234", "1234567890123"])
def test_un_documento_de_longitud_imposible_no_pasa(documento):
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", documento)))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "LONGITUD_IMPROBABLE" for r in d.reparos)


def test_un_nit_con_digito_de_verificacion_falso_no_pasa():
    """
    El DV es aritmetica de la DIAN. Un NIT que no cuadra no existe, y el
    backend lo rechazaria despues de haber creado el registro a medias.
    """
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", "9001234560"),
        tipo_documento_afectado=_h("tipo_documento_afectado", TipoDocumento.NI),
        nombre_afectado=_h("nombre_afectado", "SIETE M SAS"),
    ))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "DV_NIT_INCORRECTO" for r in d.reparos)


def test_un_nit_correcto_si_pasa():
    """Si rechazara los NIT validos, ninguna empresa podria radicar."""
    from servidor_mcp.herramientas import _digito_verificacion_nit

    base = "900123456"
    nit = base + str(_digito_verificacion_nit(base))
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", nit),
        tipo_documento_afectado=_h("tipo_documento_afectado", TipoDocumento.NI),
        nombre_afectado=_h("nombre_afectado", "SIETE M SAS"),
    ))
    assert d.veredicto is not Veredicto.RECHAZADO, informe(d)


# ── El nombre cumple el mismo criterio que exige el extractor ──

@pytest.mark.parametrize("nombre", [
    "Copia Cedula Trabajador",
    "Reporte De Novedades",
    "Recursos Humanos",
    "Cordial Saludo",
    "Centro De Trabajo",
    "Ruiz",
    "Tel",
])
def test_un_nombre_que_el_extractor_descarta_tampoco_pasa_la_compuerta(nombre):
    """El extractor jamas produce estos."""
    d = validar(_completo(nombre_afectado=_h("nombre_afectado", nombre)))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "NO_PARECE_UN_NOMBRE" for r in d.reparos), informe(d)


@pytest.mark.parametrize("nombre", [
    "PEDRO RUIZ MEJIA",
    "Eduardo Andres Soto cano",          # apellido en minuscula, es real
    "Maria del Carmen Gomez Perez",
    "GRUPO 3T SAS",                      # razon social
    "SOCIEDAD PORTUARIA REGIONAL SAS",   # lleva "sociedad", que es de cargo
    "MINIPRECIOS&SURTIBABY",             # comercio de una sola palabra
])
def test_un_nombre_legitimo_pasa(nombre):
    """
    El vocabulario de cargos incluye palabras que si aparecen en una razon
    social, asi que la comprobacion se salta cuando el nombre lleva marca de
    empresa. Rechazarlas mandaria a revision manual un remitente valido.
    """
    d = validar(_completo(nombre_afectado=_h("nombre_afectado", nombre)))
    assert not any(r.codigo == "NO_PARECE_UN_NOMBRE" for r in d.reparos), informe(d)


def test_el_criterio_del_nombre_sale_de_un_solo_sitio():
    """El vocabulario de palabras que no son nombre viene de un solo sitio para el remitente y el afectado."""
    from pipeline.extraccion import _VOCABULARIO_NO_NOMBRE
    from utils.generic_filter import palabras_de_cargo

    assert palabras_de_cargo() <= _VOCABULARIO_NO_NOMBRE


# ── El telefono existe en el plan de numeracion ──

@pytest.mark.parametrize("numero", [
    "3805551234",   # prefijo movil que no existe
    "3905551234",
    "3615551234",
    "12345",        # ni siquiera tiene longitud de telefono
])
def test_un_telefono_que_no_existe_se_descarta(numero):
    """
    El extractor lo rechaza consultando el plan de numeracion. Es el ultimo
    filtro antes de escribir en la base.
    """
    d = validar(_completo(
        telefono_afectado=_h("telefono_afectado", numero)))
    assert any(r.codigo == "TELEFONO_INEXISTENTE" for r in d.reparos), informe(d)
    assert d.correcciones.get("telefono_afectado") == ""


def test_descartar_el_telefono_no_bloquea_la_radicacion():
    """
    El telefono no es obligatorio: rechazar el correo entero por un contacto
    mal escrito manda a revision manual un caso que se puede radicar.
    """
    d = validar(_completo(
        telefono_afectado=_h("telefono_afectado", "3805551234")))
    assert d.puede_radicar
    assert d.veredicto is Veredicto.CORREGIDO


@pytest.mark.parametrize("numero", ["3105551234", "6013334444", "3211592285"])
def test_un_telefono_valido_pasa_intacto(numero):
    d = validar(_completo(telefono_afectado=_h("telefono_afectado", numero)))
    assert not any(r.codigo == "TELEFONO_INEXISTENTE" for r in d.reparos)
    assert "telefono_afectado" not in d.correcciones


def test_sin_telefono_no_se_reclama_nada():
    """El campo es opcional: su ausencia no es un reparo."""
    d = validar(_completo())
    assert not any(r.codigo == "TELEFONO_INEXISTENTE" for r in d.reparos)


def test_la_compuerta_usa_el_mismo_criterio_que_el_extractor():
    """La compuerta y el extractor usan el mismo criterio para decidir si un telefono existe."""
    from utils.contacto_extractor import extraer_telefonos, telefono_existe

    for numero in ("3105551234", "3805551234", "6013334444", "3905551234"):
        del_extractor = bool(extraer_telefonos(f"Celular {numero}"))
        assert telefono_existe(numero) is del_extractor, numero


# ── La forma corta de un nombre es la misma persona ──

@pytest.mark.parametrize("afectado,remitente", [
    ("FABIOLA ROBLEDO REVELO", "FABIOLA ROBLEDO"),
    ("Maria del Carmen Gomez Perez", "Maria Gomez Perez"),
    ("PEDRO RUIZ MEJIA", "Pedro Ruíz Mejía"),
])
def test_el_tipo_3_admite_el_nombre_escrito_mas_corto(afectado, remitente):
    """El display name del correo trae la forma corta y la firma la completa."""
    d = validar(_completo(
        tipo_remitente=_h("tipo_remitente", 3),
        nombre_afectado=_h("nombre_afectado", afectado),
        nombre_remitente=_h("nombre_remitente", remitente),
    ))
    assert not any(r.codigo == "TIPO_3_CON_NOMBRES_DISTINTOS"
                   for r in d.reparos), informe(d)


def test_el_tipo_3_sigue_reclamando_dos_personas_distintas():
    """Compartir un apellido no las convierte en la misma persona."""
    d = validar(_completo(
        tipo_remitente=_h("tipo_remitente", 3),
        nombre_afectado=_h("nombre_afectado", "PEDRO RUIZ MEJIA"),
        nombre_remitente=_h("nombre_remitente", "ANA RUIZ LOPEZ"),
    ))
    assert any(r.codigo == "TIPO_3_CON_NOMBRES_DISTINTOS" for r in d.reparos)


def test_el_tipo_2_con_la_forma_corta_se_corrige_a_3():
    d = validar(_completo(
        tipo_remitente=_h("tipo_remitente", 2),
        nombre_afectado=_h("nombre_afectado", "FABIOLA ROBLEDO REVELO"),
        nombre_remitente=_h("nombre_remitente", "FABIOLA ROBLEDO"),
    ))
    assert d.correcciones.get("tipo_remitente") == 3


# ── El nombre y el tipo de documento hablan del mismo sujeto ──

def test_un_nombre_de_persona_con_nit_se_anota():
    """
    El par se anota porque puede venir de juntar el NIT de la empresa con el
    nombre de la persona.
    """
    from servidor_mcp.herramientas import _digito_verificacion_nit

    base = "900123456"
    d = validar(_completo(
        numero_documento_afectado=_h(
            "numero_documento_afectado",
            base + str(_digito_verificacion_nit(base))),
        tipo_documento_afectado=_h("tipo_documento_afectado", TipoDocumento.NI),
    ))
    assert any(r.codigo == "NOMBRE_SIN_FORMA_DE_EMPRESA" for r in d.reparos)


def test_un_nombre_de_persona_con_nit_no_bloquea():
    """
    El NIT de una persona natural ES su cedula, y un empleador puede serlo:
    "la empresa JHON MAURICIO GOMEZ PEÑA (NIT 81367440)". Bloquear ese par
    manda a revision manual una radicacion valida.
    """
    from servidor_mcp.herramientas import _digito_verificacion_nit

    base = "900123456"
    d = validar(_completo(
        numero_documento_afectado=_h(
            "numero_documento_afectado",
            base + str(_digito_verificacion_nit(base))),
        tipo_documento_afectado=_h("tipo_documento_afectado", TipoDocumento.NI),
    ))
    assert d.veredicto is not Veredicto.RECHAZADO, informe(d)
    assert not any(r.codigo == "NOMBRE_SIN_FORMA_DE_EMPRESA"
                   for r in d.bloqueantes)


@pytest.mark.parametrize("razon_social", [
    "SIETE M SAS", "PYM CONSTRUCTORES SAS", "Fundacion Buen Vivir",
    "COOPERATIVA DE TRABAJO ANDINA", "MUNICIPIO DE TABIO",
    "E.S.E. Hospital San Rafael",
])
def test_una_razon_social_con_nit_si_pasa(razon_social):
    from servidor_mcp.herramientas import _digito_verificacion_nit

    base = "900123456"
    d = validar(_completo(
        numero_documento_afectado=_h(
            "numero_documento_afectado",
            base + str(_digito_verificacion_nit(base))),
        tipo_documento_afectado=_h("tipo_documento_afectado", TipoDocumento.NI),
        nombre_afectado=_h("nombre_afectado", razon_social),
    ))
    assert not any(r.codigo == "NOMBRE_SIN_FORMA_DE_EMPRESA" for r in d.reparos), \
        informe(d)


def test_el_remitente_tambien_se_revisa():
    """El documento del remitente tambien viaja a SGDEA."""
    d = validar(_completo(
        numero_documento_remitente=_h("numero_documento_remitente", "9001234566"),
        tipo_documento_remitente=_h("tipo_documento_remitente", TipoDocumento.NI),
    ))
    assert any(r.codigo == "NOMBRE_SIN_FORMA_DE_EMPRESA"
               and r.campo == "nombre_remitente" for r in d.reparos), informe(d)


def test_un_nombre_de_persona_con_cedula_no_se_reclama():
    """
    La regla solo reclama en la direccion que se puede afirmar. Que un nombre no
    lleve marca de razon social no prueba que sea de una persona.
    """
    d = validar(_completo())
    assert not any(r.codigo == "NOMBRE_SIN_FORMA_DE_EMPRESA" for r in d.reparos)


def test_un_dato_sin_evidencia_no_pasa():
    """Sin procedencia no hay auditoria: el dato no se puede defender."""
    d = validar(_completo(
        nombre_afectado=_h("nombre_afectado", "PEDRO RUIZ", evidencia="")))
    assert any(r.codigo == "SIN_EVIDENCIA" for r in d.reparos)
    assert d.veredicto is Veredicto.RECHAZADO


def test_una_confianza_baja_manda_a_revision():
    d = validar(_completo(nombre_afectado=_h(
        "nombre_afectado", "PEDRO RUIZ", confianza=CONFIANZA_PARA_RADICAR - 0.01)))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "CONFIANZA_INSUFICIENTE" for r in d.reparos)


# ── Incoherencias entre campos ──

def test_decir_que_el_remitente_es_el_afectado_con_otro_nombre_no_pasa():
    """
    tipo_remitente=3 significa "quien escribe es la persona del caso". Con
    nombres distintos, uno de los dos datos esta mal.
    """
    d = validar(_completo(tipo_remitente=_h("tipo_remitente", 3)))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "TIPO_3_CON_NOMBRES_DISTINTOS" for r in d.reparos)


def test_el_mismo_nombre_en_ambos_corrige_el_tipo_sin_bloquear():
    """Con los dos nombres iguales, el tipo se corrige a 3 y el correo sigue."""
    d = validar(_completo(
        nombre_remitente=_h("nombre_remitente", "PEDRO RUIZ MEJIA"),
        tipo_remitente=_h("tipo_remitente", 2),
    ))
    assert d.veredicto is Veredicto.CORREGIDO
    assert d.correcciones["tipo_remitente"] == 3
    assert d.puede_radicar


def test_los_nombres_se_comparan_sin_tildes_ni_mayusculas():
    d = validar(_completo(
        nombre_afectado=_h("nombre_afectado", "José Muñoz Peña"),
        nombre_remitente=_h("nombre_remitente", "JOSE MUNOZ PENA"),
        tipo_remitente=_h("tipo_remitente", 3),
    ))
    assert d.veredicto is not Veredicto.RECHAZADO, informe(d)


def test_un_tercero_no_se_registra_con_el_documento_del_afectado():
    """El documento ajeno se retira: el del remitente no es obligatorio y el caso sigue."""
    d = validar(_completo(
        tipo_remitente=_h("tipo_remitente", 2),
        numero_documento_remitente=_h("numero_documento_remitente", "41234567"),
    ))
    assert d.veredicto is Veredicto.CORREGIDO
    assert d.correcciones["numero_documento_remitente"] == ""
    assert any(r.codigo == "DOCUMENTO_HEREDADO_DEL_AFECTADO" for r in d.reparos)


def test_la_oficina_se_corrige_contra_la_tabla():
    """La oficina sale de una tabla; si no coincide, manda la tabla."""
    from business_rules import get_office_id

    d = validar(_completo(
        tipo_solicitud=_h("tipo_solicitud", 16),
        oficina=_h("oficina", 999),
    ))
    assert d.veredicto is Veredicto.CORREGIDO
    assert d.correcciones["oficina"] == get_office_id(16)


# ── El documento identifica; el nombre solo escribe ──

def test_con_el_mismo_documento_una_diferencia_de_nombre_no_es_contradiccion():
    """El documento identifica; el nombre solo escribe. Con el mismo documento, dos grafias del nombre no son una contradiccion."""
    d = validar(_completo(
        tipo_remitente=_h("tipo_remitente", 3),
        nombre_afectado=_h("nombre_afectado", "OMAR CALDERON NIÑO"),
        nombre_remitente=_h("nombre_remitente", "Omar Calderon"),
        numero_documento_afectado=_h("numero_documento_afectado", "32718260"),
        numero_documento_remitente=_h("numero_documento_remitente", "32718260"),
    ))
    assert not any(r.codigo == "TIPO_3_CON_NOMBRES_DISTINTOS"
                   for r in d.reparos), [r.codigo for r in d.reparos]
    assert d.veredicto is not Veredicto.RECHAZADO


def test_sin_documento_que_lo_respalde_los_nombres_distintos_si_bloquean():
    d = validar(_completo(
        tipo_remitente=_h("tipo_remitente", 3),
        nombre_afectado=_h("nombre_afectado", "OMAR CALDERON NIÑO"),
        nombre_remitente=_h("nombre_remitente", "ANA LOPEZ RUIZ"),
    ))
    assert any(r.codigo == "TIPO_3_CON_NOMBRES_DISTINTOS" for r in d.reparos)


def test_con_documentos_distintos_los_nombres_distintos_siguen_bloqueando():
    d = validar(_completo(
        tipo_remitente=_h("tipo_remitente", 3),
        nombre_afectado=_h("nombre_afectado", "OMAR CALDERON NIÑO"),
        nombre_remitente=_h("nombre_remitente", "ANA LOPEZ RUIZ"),
        numero_documento_afectado=_h("numero_documento_afectado", "32718260"),
        numero_documento_remitente=_h("numero_documento_remitente", "76530545"),
    ))
    assert any(r.codigo == "TIPO_3_CON_NOMBRES_DISTINTOS" for r in d.reparos)


def test_dos_personas_con_el_mismo_nombre_no_se_corrigen_a_tipo_3():
    """
    El nombre coincide pero los documentos no: son dos personas distintas que
    se llaman igual, y ahi manda el documento.
    """
    d = validar(_completo(
        tipo_remitente=_h("tipo_remitente", 2),
        nombre_afectado=_h("nombre_afectado", "PEDRO RUIZ MEJIA"),
        nombre_remitente=_h("nombre_remitente", "PEDRO RUIZ MEJIA"),
        numero_documento_afectado=_h("numero_documento_afectado", "41234567"),
        numero_documento_remitente=_h("numero_documento_remitente", "76530545"),
    ))
    assert "tipo_remitente" not in d.correcciones, d.correcciones


# ── El motivo de revision manual dice que falto ──

def test_todo_motivo_que_la_compuerta_puede_devolver_esta_catalogado():
    """
    Un motivo sin entrada en `MOTIVOS_MANUAL` sale en el informe como "motivo
    no catalogado", que no le dice nada a quien lo lee.
    """
    from pipeline.compuerta import _MOTIVO_POR_CAMPO, _MOTIVO_POR_CAUSA

    posibles = (set(_MOTIVO_POR_CAUSA.values())
                | set(_MOTIVO_POR_CAMPO.values())
                | {"VALIDACION_RECHAZO"})
    faltantes = sorted(posibles - set(MOTIVOS_MANUAL))
    assert not faltantes, f"sin catalogar: {faltantes}"


def test_un_correo_que_solo_identifica_empresa_llega_con_su_motivo():
    """
    El motivo del extractor tiene que sobrevivir hasta la revision manual: si
    se traduce a "no se pudo determinar el nombre", el operador busca una
    persona que el correo no contiene.
    """
    hallazgos = _completo()
    del hallazgos["nombre_afectado"]
    d = validar(hallazgos, motivos_no_resueltos={
        "nombre_afectado": "SOLO_IDENTIFICA_UNA_EMPRESA"})
    assert d.motivo_manual == "SOLO_EMPRESA_SIN_AFECTADO"
    assert d.motivo_manual in MOTIVOS_MANUAL

def test_sin_tipo_de_solicitud_el_motivo_lo_dice():
    """Es el escalado mas frecuente: el campo no se resuelve nunca por algoritmo."""
    hallazgos = _completo()
    del hallazgos["tipo_solicitud"]
    d = validar(hallazgos)
    assert d.veredicto is Veredicto.RECHAZADO
    assert d.motivo_manual == "SIN_TIPO_SOLICITUD"
    assert d.motivo_manual in MOTIVOS_MANUAL


@pytest.mark.parametrize("campo,esperado", [
    ("nombre_afectado", "SIN_NOMBRE_AFECTADO"),
    ("nombre_remitente", "SIN_NOMBRE_REMITENTE"),
    ("numero_documento_afectado", "SIN_DOCUMENTO"),
    ("tipo_solicitud", "SIN_TIPO_SOLICITUD"),
])
def test_cada_campo_obligatorio_tiene_su_motivo(campo, esperado):
    hallazgos = _completo()
    del hallazgos[campo]
    assert validar(hallazgos).motivo_manual == esperado


def test_el_motivo_no_depende_del_orden_de_los_reparos():
    """El motivo especifico de revision manual no depende del orden en que aparecen los reparos."""
    hallazgos = _completo(
        numero_documento_remitente=_h("numero_documento_remitente", "11111111"),
    )
    del hallazgos["nombre_afectado"]
    d = validar(hallazgos)
    codigos = [r.codigo for r in d.reparos]
    assert "DOCUMENTO_DE_RELLENO" in codigos, codigos
    assert d.motivo_manual == "SIN_NOMBRE_AFECTADO"


def test_un_rechazo_sin_campo_faltante_usa_el_motivo_generico():
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", "11111111")))
    assert d.veredicto is Veredicto.RECHAZADO
    assert d.motivo_manual == "VALIDACION_RECHAZO"


# ── Los dos documentos se validan igual ──

@pytest.mark.parametrize("campo,tipo", [
    ("numero_documento_afectado", "tipo_documento_afectado"),
    ("numero_documento_remitente", "tipo_documento_remitente"),
])
class TestFormaDelDocumento:
    """
    El documento del remitente tambien va a SGDEA: se le exige la misma
    longitud, el mismo relleno y el mismo digito de verificacion que al del
    afectado.
    """

    def test_demasiado_corto_se_rechaza(self, campo, tipo):
        d = validar(_completo(**{campo: _h(campo, "123"),
                                 tipo: _h(tipo, 1)}))
        assert any(r.campo == campo and r.codigo == "LONGITUD_IMPROBABLE"
                   for r in d.reparos), [r.codigo for r in d.reparos]

    def test_demasiado_largo_se_rechaza(self, campo, tipo):
        d = validar(_completo(**{campo: _h(campo, "123456789012345678")}))
        assert any(r.campo == campo and r.codigo == "LONGITUD_IMPROBABLE"
                   for r in d.reparos)

    def test_todos_los_digitos_iguales_se_rechaza(self, campo, tipo):
        d = validar(_completo(**{campo: _h(campo, "11111111")}))
        assert any(r.campo == campo and r.codigo == "DOCUMENTO_DE_RELLENO"
                   for r in d.reparos)

    def test_el_digito_del_nit_se_comprueba(self, campo, tipo):
        from servidor_mcp.herramientas import _digito_verificacion_nit

        base = "900123456"
        malo = base + str((_digito_verificacion_nit(base) + 1) % 10)
        d = validar(_completo(**{campo: _h(campo, malo),
                                 tipo: _h(tipo, 10)}))
        assert any(r.campo == campo and r.codigo == "DV_NIT_INCORRECTO"
                   for r in d.reparos)

    def test_un_nit_correcto_no_se_objeta(self, campo, tipo):
        from servidor_mcp.herramientas import _digito_verificacion_nit

        base = "900123456"
        bueno = base + str(_digito_verificacion_nit(base))
        d = validar(_completo(**{campo: _h(campo, bueno),
                                 tipo: _h(tipo, 10)}))
        assert not any(r.campo == campo for r in d.reparos), \
            [r.codigo for r in d.reparos]


def test_un_permiso_especial_de_quince_digitos_no_se_rechaza():
    """
    La longitud valida sale de la misma tabla para el extractor y la
    compuerta: un permiso especial de quince digitos es un documento real.
    """
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado",
                                     "123456789012345"),
        tipo_documento_afectado=_h("tipo_documento_afectado", 5),
    ))
    assert not any(r.codigo == "LONGITUD_IMPROBABLE" for r in d.reparos), \
        [r.detalle for r in d.reparos]
    assert d.veredicto is not Veredicto.RECHAZADO


def test_la_longitud_se_mide_contra_el_tipo_declarado():
    """Ocho digitos son una cedula valida y no un permiso especial."""
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", "41234567"),
        tipo_documento_afectado=_h("tipo_documento_afectado", 5),
    ))
    reparo = next((r for r in d.reparos if r.codigo == "LONGITUD_IMPROBABLE"),
                  None)
    assert reparo is not None
    assert "15" in reparo.detalle


# ── Correcciones y trazabilidad ──

def test_aplicar_deja_escrito_que_lo_cambio_la_compuerta():
    """El hallazgo corregido queda con metodo `compuerta` y el valor previo
    en su evidencia."""
    hallazgos = _completo(
        nombre_remitente=_h("nombre_remitente", "PEDRO RUIZ MEJIA"),
        tipo_remitente=_h("tipo_remitente", 2))
    d = validar(hallazgos)
    corregidos = aplicar(hallazgos, d)
    assert corregidos["tipo_remitente"].valor == 3
    assert corregidos["tipo_remitente"].metodo == "compuerta"
    assert "antes 2" in corregidos["tipo_remitente"].evidencia


def test_aplicar_sin_correcciones_no_toca_nada():
    hallazgos = _completo()
    assert aplicar(hallazgos, validar(hallazgos)) == hallazgos


def test_el_informe_nombra_cada_reparo():
    d = validar(_completo(tipo_remitente=_h("tipo_remitente", 99)))
    texto = informe(d)
    assert "RECHAZADO" in texto
    assert "FUERA_DE_CATALOGO" in texto
    assert "tipo_remitente" in texto


def test_la_causa_del_extractor_llega_al_motivo_manual():
    """La causa que reporta el extractor llega intacta hasta el motivo de revision manual."""
    d = validar(
        _completo(numero_documento_afectado=None),
        motivos_no_resueltos={
            "numero_documento_afectado": "DOCUMENTO_EN_ADJUNTO_ILEGIBLE"},
    )
    assert d.motivo_manual == "ADJUNTO_ILEGIBLE"
    assert d.motivo_manual in MOTIVOS_MANUAL


def test_sin_hallazgos_no_revienta():
    d = validar({})
    assert d.veredicto is Veredicto.RECHAZADO
    assert d.motivo_manual


# ── Datos que el reporte del cliente encontro mal diligenciados ──

@pytest.mark.parametrize("documento", ["N/A", "ABC12345", "*****4567", "n/r"])
def test_lo_que_no_es_un_numero_no_es_un_documento(documento):
    """
    Quitarle las letras a un valor produce digitos con longitud plausible que
    pasarian cualquier comprobacion posterior. La forma se juzga sobre el valor
    tal como se extrajo.
    """
    d = validar(_completo(numero_documento_afectado=_h("numero_documento_afectado", documento)))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "DOCUMENTO_NO_NUMERICO" for r in d.reparos)


@pytest.mark.parametrize("documento", ["41.234.567", "41 234 567", "1.004.366.367"])
def test_los_separadores_de_miles_no_invalidan_el_documento(documento):
    """Es forma de escribirlo, no otro dato."""
    d = validar(_completo(numero_documento_afectado=_h("numero_documento_afectado", documento)))
    assert not [r for r in d.reparos if r.codigo == "DOCUMENTO_NO_NUMERICO"]


@pytest.mark.parametrize("nombre", [
    "PROCALIN SAS", "Constructora Gomez Martinez S.A.S",
    "COMERCIALIZADORA LTDA", "FUNDACION SANTA FE",
])
def test_una_razon_social_con_documento_de_persona_no_se_radica(nombre):
    """
    La razon social es una marca explicita y una sociedad no tiene cedula: la
    pareja es imposible, no dudosa. Uno de los dos datos esta mal y no se puede
    saber cual, asi que radicarla escribe una identidad que no existe.
    """
    d = validar(_completo(nombre_afectado=_h("nombre_afectado", nombre)))
    reparo = [r for r in d.reparos if r.codigo == "EMPRESA_CON_DOCUMENTO_DE_PERSONA"]
    assert reparo, [r.codigo for r in d.reparos]
    assert reparo[0].bloqueante
    assert d.veredicto is Veredicto.RECHAZADO
    assert d.motivo_manual == "EMPRESA_CON_DOCUMENTO_DE_PERSONA"


def test_un_nombre_de_persona_con_su_cedula_no_se_anota():
    d = validar(_completo(nombre_afectado=_h("nombre_afectado", "MARIA FERNANDA LOPEZ")))
    assert not [r for r in d.reparos if r.codigo == "EMPRESA_CON_DOCUMENTO_DE_PERSONA"]


@pytest.mark.parametrize("telefono", [
    "6017654821", "6027654321", "7654321", "3000000000",
    "3333333333", "1234567",
])
def test_un_telefono_de_relleno_no_llega_al_registro(telefono):
    """
    Tienen la forma correcta y aun asi no llaman a nadie: son el ejemplo del
    formulario o un relleno para pasar la validacion. Se corrigen a vacio en
    vez de bloquear, porque el telefono no es obligatorio.
    """
    d = validar(_completo(telefono_afectado=_h("telefono_afectado", telefono)))
    reparo = [r for r in d.reparos
              if r.codigo in ("TELEFONO_DE_RELLENO", "TELEFONO_INEXISTENTE")]
    assert reparo, [r.codigo for r in d.reparos]
    assert not reparo[0].bloqueante
    assert aplicar(_completo(telefono_afectado=_h("telefono_afectado", telefono)), d).get(
        "telefono_afectado").valor == ""


@pytest.mark.parametrize("telefono", ["3105551234", "6013334444", "3186549870"])
def test_un_telefono_real_no_se_toca(telefono):
    d = validar(_completo(telefono_afectado=_h("telefono_afectado", telefono)))
    assert not [r for r in d.reparos if r.codigo.startswith("TELEFONO")]


@pytest.mark.parametrize("direccion", [
    "No reporta", "NO REPORTA", "N/R", "NO REGISTRA", "undefined", "N/A",
    "juan@empresa.com", "3105551234",
])
def test_lo_que_no_es_una_direccion_se_deja_vacio(direccion):
    """
    El reporte del cliente cuenta siete formas distintas de escribir que no hay
    direccion, mas correos y telefonos en ese campo. Guardarlas escribe en el
    registro una direccion que no lleva a ninguna parte.
    """
    d = validar(_completo(direccion_afectado=_h("direccion_afectado", direccion)))
    reparo = [r for r in d.reparos if r.codigo == "DIRECCION_NO_ES_DIRECCION"]
    assert reparo, [r.codigo for r in d.reparos]
    assert not reparo[0].bloqueante


@pytest.mark.parametrize("direccion", [
    "CORREGIMIENTO EL SALADO LOTE 1",
    "KILOMETRO 17 VIA DINAMARCA",
    "VEREDA LA ESPERANZA FINCA EL PORVENIR",
    "CALLE 10 # 5-20 APTO 301",
    "CRA 45 A SUR # 12-34",
])
def test_una_direccion_rural_es_una_direccion(direccion):
    """
    Buena parte de las direcciones del pais no empiezan por calle o carrera.
    Exigir una forma urbana descartaria direcciones legitimas.
    """
    d = validar(_completo(direccion_afectado=_h("direccion_afectado", direccion)))
    assert not [r for r in d.reparos if r.codigo == "DIRECCION_NO_ES_DIRECCION"]


# ── No toda persona atendida es colombiana ──

@pytest.mark.parametrize("documento, tipo", [
    ("AB123456", TipoDocumento.PA),
    ("A1234567B", TipoDocumento.PA),
    ("AQ4521398", TipoDocumento.PA),
])
def test_un_pasaporte_lleva_letras_y_es_valido(documento, tipo):
    """Exigirle digitos rechazaria el documento de quien no es colombiano."""
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", documento),
        tipo_documento_afectado=_h("tipo_documento_afectado", tipo)))
    assert not [r for r in d.reparos if r.codigo == "DOCUMENTO_NO_NUMERICO"]


def test_sin_tipo_declarado_se_acepta_la_forma_de_pasaporte():
    """
    El extractor no siempre resuelve el tipo. Rechazar por llevar letras
    descartaria a un extranjero cuyo tipo quedo pendiente.
    """
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", "AB123456"),
        tipo_documento_afectado=None))
    assert not [r for r in d.reparos if r.codigo == "DOCUMENTO_NO_NUMERICO"]


def test_una_cedula_declarada_con_letras_si_se_rechaza():
    """Con un tipo numerico declarado no hay excepcion que valga."""
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", "ABC12345"),
        tipo_documento_afectado=_h("tipo_documento_afectado", TipoDocumento.CC)))
    assert any(r.codigo == "DOCUMENTO_NO_NUMERICO" for r in d.reparos)


@pytest.mark.parametrize("documento, tipo", [
    ("1234567", TipoDocumento.CE),      # cedula de extranjeria
    ("12345678", TipoDocumento.PPT),    # permiso por proteccion temporal
    ("123456789012345", TipoDocumento.PE),   # permiso especial de permanencia
])
def test_los_documentos_de_migracion_pasan_con_su_longitud(documento, tipo):
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", documento),
        tipo_documento_afectado=_h("tipo_documento_afectado", tipo)))
    assert not [r for r in d.reparos
                if r.codigo in ("DOCUMENTO_NO_NUMERICO", "LONGITUD_IMPROBABLE")]


@pytest.mark.parametrize("documento", [
    "123456", "1234567", "19123456", "41234567", "52123456", "1004366367",
])
def test_la_cedula_vale_desde_la_mas_antigua_hasta_la_mas_reciente(documento):
    """
    Las cedulas mas antiguas tienen seis digitos y las expedidas sobre el NUIP
    tienen diez. El rango cubre las dos puntas.
    """
    d = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", documento),
        tipo_documento_afectado=_h("tipo_documento_afectado", TipoDocumento.CC)))
    assert not [r for r in d.reparos if r.codigo == "LONGITUD_IMPROBABLE"]


# ── El nombre no lleva el documento, el telefono ni el radicado ──

@pytest.mark.parametrize("sucio,limpio", [
    ("JUAN CARLOS PEREZ 3105551234", "JUAN CARLOS PEREZ"),
    ("PEDRO RUIZ MEJIA 41234567", "PEDRO RUIZ MEJIA"),
    ("ANA LOPEZ 41.234.567", "ANA LOPEZ"),
    ("TORRES SANCHEZ 900123456-7", "TORRES SANCHEZ"),
    ("LUIS PARRA ENT20260001682739", "LUIS PARRA"),
    ("3105551234 MARIA GOMEZ RUIZ", "MARIA GOMEZ RUIZ"),
])
def test_del_nombre_se_retira_el_numero_que_venia_pegado(sucio, limpio):
    """
    El campo del nombre viaja al registro tal como se envie. Un telefono, una
    cedula o un radicado pegados quedan ahi para siempre.
    """
    d = validar(_completo(nombre_afectado=_h("nombre_afectado", sucio)))

    assert d.correcciones["nombre_afectado"] == limpio
    assert any(r.codigo == "NOMBRE_CON_NUMERO_AJENO" for r in d.reparos)
    assert d.veredicto is not Veredicto.RECHAZADO


@pytest.mark.parametrize("razon_social", [
    "CONSTRUCTORA 2000 SAS",
    "GRUPO 3T SOLUCIONES S.A.S",
    "INVERSIONES 5 ESTRELLAS LTDA",
])
def test_una_razon_social_conserva_sus_numeros(razon_social):
    """Una empresa si puede llamarse con numeros, y son parte de su nombre."""
    d = validar(_completo(nombre_afectado=_h("nombre_afectado", razon_social)))

    assert "nombre_afectado" not in d.correcciones
    assert not any(r.codigo == "NOMBRE_CON_NUMERO_AJENO" for r in d.reparos)


def test_un_nombre_que_es_solo_un_radicado_se_bloquea():
    """Corregirlo a vacio radicaria a alguien sin nombre."""
    d = validar(_completo(
        nombre_afectado=_h("nombre_afectado", "ENT20260001682739")))

    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "NOMBRE_ES_SOLO_UN_NUMERO" and r.bloqueante
               for r in d.reparos)


# ── Los datos de Positiva no son los del ciudadano ──

@pytest.mark.parametrize("numero,tipo", [
    ("6502200", int(TipoDocumento.CC)),
    ("6016502200", int(TipoDocumento.CC)),
    ("601 650 2200", int(TipoDocumento.TI)),
    ("8600111536", int(TipoDocumento.NI)),
    ("860.011.153-6", int(TipoDocumento.NI)),
])
def test_un_identificador_de_positiva_no_es_el_documento_de_nadie(numero, tipo):
    """
    La linea institucional va en la firma de cada correo y el NIT de la entidad
    en el membrete de cada escaneo, asi que los dos llegan con facilidad al
    campo del documento. Sin este reparo, el mismo numero queda registrado como
    documento de personas distintas.
    """
    v = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", numero),
        tipo_documento_afectado=_h("tipo_documento_afectado", tipo),
    ))

    assert not v.puede_radicar, informe(v)
    assert any(r.codigo == "DOCUMENTO_ES_DE_POSITIVA" for r in v.reparos), informe(v)


def test_el_documento_de_un_ciudadano_cualquiera_si_pasa():
    """El filtro es de los numeros de la entidad, no de cualquier telefono."""
    v = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", "41234567"),
    ))

    assert not any(r.codigo == "DOCUMENTO_ES_DE_POSITIVA" for r in v.reparos)


def test_la_linea_de_positiva_no_se_registra_como_telefono_del_afectado():
    """
    No bloquea: el telefono no es obligatorio y corregirlo a vacio es mejor que
    mandar a una persona una radicacion que por lo demas esta completa.
    """
    v = validar(_completo(
        telefono_afectado=_h("telefono_afectado", "6016502200"),
    ))

    assert v.puede_radicar, informe(v)
    assert v.correcciones.get("telefono_afectado") == ""


# ── Digitos que parecen digitos y no lo son ──

def test_un_documento_con_digitos_de_otro_alfabeto_se_normaliza():
    """
    `str.isdigit()` acepta los digitos de ancho completo y los arabigo-indicos,
    asi que un numero escrito con ellos pasaba longitud y tipo como si fuera
    ASCII y quedaba asi en el registro: ninguna busqueda posterior por el
    documento real lo encuentra.
    """
    v = validar(_completo(
        numero_documento_afectado=_h("numero_documento_afectado", "４１２３４５６７"),
    ))

    assert v.puede_radicar, informe(v)
    assert v.correcciones.get("numero_documento_afectado") == "41234567"


def test_un_nombre_con_un_invisible_que_invierte_el_texto_se_limpia():
    """
    U+202E invierte el texto EN PANTALLA: el registro muestra una cosa y
    contiene otra, y quien revisa no lo ve.
    """
    v = validar(_completo(
        nombre_afectado=_h("nombre_afectado", "JUAN PEREZ‮ LICARRAO"),
    ))

    corregido = v.correcciones.get("nombre_afectado")
    assert corregido is not None, informe(v)
    assert "‮" not in corregido
    assert corregido == "JUAN PEREZ LICARRAO"


# ── Una sociedad no tiene cedula ──

def test_el_caso_que_reporto_el_area_no_se_radica():
    """El nombre del afectado es la empresa y el documento tiene forma de cedula."""
    d = validar(_completo(
        nombre_afectado=_h("nombre_afectado", "525 Design Group SAS"),
        numero_documento_afectado=_h("numero_documento_afectado", "1032363630"),
    ))

    assert d.veredicto is Veredicto.RECHAZADO
    assert d.motivo_manual == "EMPRESA_CON_DOCUMENTO_DE_PERSONA"
    assert d.motivo_manual in MOTIVOS_MANUAL


def test_una_persona_con_nit_si_se_radica():
    """
    En Colombia el NIT de una persona natural es su propia cedula, y un
    empleador puede serlo. Bloquearlo manda a revision una radicacion valida.
    """
    d = validar(_completo(
        nombre_afectado=_h("nombre_afectado", "JHON MAURICIO GOMEZ PEÑA"),
        tipo_documento_afectado=_h("tipo_documento_afectado",
                                   int(TipoDocumento.NI)),
        numero_documento_afectado=_h("numero_documento_afectado", "813674409"),
    ))

    assert d.veredicto is not Veredicto.RECHAZADO, [
        (r.codigo, r.detalle) for r in d.reparos if r.bloqueante]
