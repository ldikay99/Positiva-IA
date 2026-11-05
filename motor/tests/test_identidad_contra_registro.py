"""El nombre del correo frente al que el registro tiene para ese documento."""
import pytest

from pipeline.compuerta import Veredicto, validar
from pipeline.extraccion import Hallazgo
from pipeline.personas import nombrado_junto_al_documento, rellenar_desde_el_registro
from utils.generic_filter import es_el_mismo_titular, es_la_misma_persona


def h(campo, valor, metodo="prueba"):
    return Hallazgo(campo=campo, valor=valor, evidencia="del correo",
                    confianza=0.9, metodo=metodo)


def registro(nombre="Ana Maria Lopez Rojas", **extra):
    return lambda _doc, _tipo=None: {"nombre": nombre, **extra}


@pytest.mark.parametrize("uno,otro", [
    ("LISETH GUZMAN TORO", "LIZETH GUZMAN TORO"),
    ("Karen Nuñez Mora", "KAREN NUNEZ MORA"),
    ("NATALIA MOYA TRUJILLO", "NATALY MOYA TRUJILO"),
    ("Hernando de Jesus Montoya Sanchez", "HERNANDO DEJESUS MONTOYA SANCHEZ"),
    ("Lopez Rojas Ana Maria", "ANA MARIA LOPEZ ROJAS"),
    ("Pedro Ruiz", "PEDRO ANTONIO RUIZ GOMEZ"),
])
def test_el_mismo_documento_admite_otra_forma_de_escribir_el_nombre(uno, otro):
    assert es_el_mismo_titular(uno, otro)
    assert es_el_mismo_titular(otro, uno)


@pytest.mark.parametrize("uno,otro", [
    ("LAURA MARTINEZ BRAVO", "KAREN MARTINEZ BRAVO"),
    ("NO APLICA", "ANA MARIA LOPEZ ROJAS"),
    ("ESTABA LABORANDO", "JUAN SEBASTIAN ARIAS ARIAS"),
    ("PEDRO GOMEZ", "PABLO RUIZ"),
    ("Ana", "Anabel Rojas"),
])
def test_compartir_apellidos_no_hace_al_mismo_titular(uno, otro):
    assert not es_el_mismo_titular(uno, otro)


def test_la_comparacion_sin_documento_no_tolera_errores():
    assert not es_la_misma_persona("MARIO LOPEZ RUIZ", "MARIA LOPEZ RUIZ")


def test_un_error_de_escritura_no_es_una_contradiccion():
    contradicciones = []
    nuevos = rellenar_desde_el_registro(
        {"numero_documento_afectado": h("numero_documento_afectado", "41234567"),
         "nombre_afectado": h("nombre_afectado", "ANA MARIA LOPES ROJAS")},
        registro(telefono="3105550001"), [], contradicciones)
    assert not contradicciones
    assert nuevos["telefono_afectado"].valor == "3105550001"


def test_el_nombre_del_registro_junto_al_documento_corrige_el_extraido():
    contradicciones, avisos = [], []
    texto = "Notificacion electronica controversia juntas CC 41.234.567 ANA MARIA LOPEZ ROJAS"
    nuevos = rellenar_desde_el_registro(
        {"numero_documento_afectado": h("numero_documento_afectado", "41234567"),
         "nombre_afectado": h("nombre_afectado", "ELECTRONICA CONTROVERSIA JUNTAS")},
        registro(), avisos, contradicciones, texto=texto, principal=texto)
    assert not contradicciones
    assert nuevos["nombre_afectado"].valor == "Ana Maria Lopez Rojas"
    assert nuevos["nombre_afectado"].metodo == "registro_de_sgdea"
    assert any("corregido con el registro" in a for a in avisos)


def test_un_nombre_que_solo_sale_en_un_adjunto_no_corrige_al_afectado():
    contradicciones = []
    nuevos = rellenar_desde_el_registro(
        {"numero_documento_afectado": h("numero_documento_afectado", "41234567"),
         "nombre_afectado": h("nombre_afectado", "Pago Mes Anterior")},
        registro(), [], contradicciones,
        texto="planilla: Ana Maria Lopez Rojas CC 41234567",
        principal="Historia clinica de Pedro Gomez")
    assert "nombre_afectado" not in nuevos
    assert contradicciones


def test_el_remitente_confirma_al_titular_aunque_el_cuerpo_no_lo_nombre():
    nuevos = rellenar_desde_el_registro(
        {"numero_documento_afectado": h("numero_documento_afectado", "41234567"),
         "nombre_afectado": h("nombre_afectado", "Derecho Peticion Urgente"),
         "nombre_remitente": h("nombre_remitente", "Ana Lopez")},
        registro(), [], [],
        texto="adjunto: Ana Maria Lopez Rojas CC 41234567", principal="Solicito respuesta")
    assert nuevos["nombre_afectado"].valor == "Ana Maria Lopez Rojas"


def test_sin_el_nombre_junto_al_documento_sigue_siendo_una_contradiccion():
    contradicciones = []
    texto = "CC 41234567 de Pedro Gomez. Mas abajo, sin relacion, Ana Maria Lopez Rojas." + " x" * 200
    nuevos = rellenar_desde_el_registro(
        {"numero_documento_afectado": h("numero_documento_afectado", "99887766"),
         "nombre_afectado": h("nombre_afectado", "Pedro Gomez")},
        registro(), [], contradicciones, texto=texto)
    assert nuevos == {}
    assert contradicciones


def test_el_registro_resuelve_el_nombre_que_faltaba_si_el_correo_lo_escribe():
    nuevos = rellenar_desde_el_registro(
        {"numero_documento_afectado": h("numero_documento_afectado", "41234567")},
        registro(), [], [], texto="Ana Maria Lopez Rojas, cedula 41234567",
        principal="Solicitud de Ana Maria Lopez Rojas")
    assert nuevos["nombre_afectado"].valor == "Ana Maria Lopez Rojas"


def test_el_nombre_se_comprueba_aunque_no_falte_ningun_campo():
    contradicciones = []
    rellenar_desde_el_registro(
        {"numero_documento_afectado": h("numero_documento_afectado", "41234567"),
         "nombre_afectado": h("nombre_afectado", "LLANTO FACIL DURANTE"),
         "telefono_afectado": h("telefono_afectado", "3009998877"),
         "direccion_afectado": h("direccion_afectado", "CRA 1 # 2-3"),
         "sexo_afectado": h("sexo_afectado", 2),
         "departamento_afectado": h("departamento_afectado", 11),
         "municipio_afectado": h("municipio_afectado", 1)},
        registro(), [], contradicciones)
    assert contradicciones


def test_el_documento_tiene_que_estar_entero_en_el_texto():
    assert not nombrado_junto_al_documento(
        "Ana Maria Lopez Rojas 141234567", "41234567", "Ana Maria Lopez Rojas")


def _base(**campos):
    base = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
        "tipo_documento_afectado": h("tipo_documento_afectado", 1),
        "nombre_afectado": h("nombre_afectado", "Ana Maria Lopez Rojas"),
        "nombre_remitente": h("nombre_remitente", "Pedro Gomez Ruiz"),
        "tipo_solicitud": h("tipo_solicitud", 14),
    }
    base.update(campos)
    return base


def test_el_documento_copiado_no_prueba_que_sean_la_misma_persona():
    d = validar(_base(
        tipo_remitente=h("tipo_remitente", 3),
        numero_documento_remitente=h("numero_documento_remitente", "41234567",
                                     metodo="remitente_es_el_afectado")))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "TIPO_3_CON_NOMBRES_DISTINTOS" for r in d.reparos)


def test_quien_firma_con_la_cedula_del_afectado_es_el_afectado():
    d = validar(_base(
        tipo_remitente=h("tipo_remitente", 3),
        numero_documento_remitente=h("numero_documento_remitente", "41234567",
                                     metodo="documento_de_la_firma")))
    assert not any(r.codigo == "TIPO_3_CON_NOMBRES_DISTINTOS" for r in d.reparos)


def test_tipo_3_admite_otra_forma_de_escribir_el_nombre():
    d = validar(_base(
        tipo_remitente=h("tipo_remitente", 3),
        nombre_remitente=h("nombre_remitente", "ANA MARIA LOPES ROJAS"),
        numero_documento_remitente=h("numero_documento_remitente", "41234567")))
    assert not any(r.codigo == "TIPO_3_CON_NOMBRES_DISTINTOS" for r in d.reparos)


def test_una_persona_con_el_nit_que_tambien_lleva_el_remitente_no_pasa():
    d = validar(_base(
        tipo_remitente=h("tipo_remitente", 2),
        tipo_documento_afectado=h("tipo_documento_afectado", 10),
        numero_documento_afectado=h("numero_documento_afectado", "900123456"),
        numero_documento_remitente=h("numero_documento_remitente", "900123456"),
        tipo_documento_remitente=h("tipo_documento_remitente", 10)))
    assert d.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "DOCUMENTO_HEREDADO_DEL_AFECTADO" and r.bloqueante
               for r in d.reparos)


def test_lo_que_se_pego_al_nombre_se_limpia_con_el_registro():
    nuevos = rellenar_desde_el_registro(
        {"numero_documento_afectado": h("numero_documento_afectado", "41234567"),
         "nombre_afectado": h("nombre_afectado", "Controversia Ana Maria Lopez Rojas")},
        registro(), [], [])
    assert nuevos["nombre_afectado"].valor == "Ana Maria Lopez Rojas"


@pytest.mark.parametrize("firma,nombre", [
    ("Ana Lopez Rojas Analista de Cartera", "Ana Lopez Rojas"),
    ("Auxiliar de Nomina (Ana Lopez Rojas)", "Ana Lopez Rojas"),
])
def test_el_cargo_se_le_quita_al_nombre_de_quien_firma(firma, nombre):
    d = validar(_base(nombre_remitente=h("nombre_remitente", firma)))
    assert d.veredicto is Veredicto.CORREGIDO
    assert d.correcciones["nombre_remitente"] == nombre


@pytest.mark.parametrize("area", ["Gerencia de Indemnizaciones", "Auxiliar Contable",
                                  "S602 CONTUSION EN MANO DERECHA"])
def test_un_area_sin_persona_sigue_sin_ser_un_nombre(area):
    d = validar(_base(nombre_remitente=h("nombre_remitente", area)))
    assert d.veredicto is Veredicto.RECHAZADO


def _quien_firma(nombre_remitente, metodo="documento_de_la_firma"):
    return _base(
        tipo_remitente=h("tipo_remitente", 3),
        nombre_remitente=h("nombre_remitente", nombre_remitente),
        numero_documento_remitente=h("numero_documento_remitente", "41234567", metodo=metodo))


def test_quien_firma_con_la_cedula_del_afectado_lleva_el_nombre_del_afectado():
    d = validar(_quien_firma("Pedro Gomez Ruiz"))
    assert d.veredicto is Veredicto.CORREGIDO
    assert d.correcciones["nombre_remitente"] == "Ana Maria Lopez Rojas"


def test_el_nombre_raro_del_buzon_no_frena_a_quien_firma_con_su_cedula():
    d = validar(_quien_firma("Equipo de Seguridad Soporte"))
    assert d.veredicto is Veredicto.CORREGIDO
    assert d.correcciones["nombre_remitente"] == "Ana Maria Lopez Rojas"


def test_el_documento_copiado_no_autoriza_a_cambiar_el_nombre():
    d = validar(_quien_firma("Pedro Gomez Ruiz", metodo="remitente_es_el_afectado"))
    assert d.veredicto is Veredicto.RECHAZADO
    assert "nombre_remitente" not in d.correcciones


def test_el_mismo_nombre_escrito_distinto_no_se_toca():
    d = validar(_quien_firma("ANA MARIA LOPES ROJAS"))
    assert "nombre_remitente" not in d.correcciones


@pytest.mark.parametrize("firma,organizacion", [
    ("Departamento de Medicina Laboral EPS Sura", "EPS Sura"),
    ("Auxiliar Recobros Eps Nueva", "Eps Nueva"),
])
def test_de_un_area_de_la_organizacion_queda_la_organizacion(firma, organizacion):
    d = validar(_base(nombre_remitente=h("nombre_remitente", firma)))
    assert d.veredicto is Veredicto.CORREGIDO
    assert d.correcciones["nombre_remitente"] == organizacion


@pytest.mark.parametrize("texto", [
    "Certificaciones de la ARL (ANEXO 2)",
    "SEGURIDAD Y SALUD EN EL TRABAJO C&M",
])
def test_un_area_sin_organizacion_clara_no_se_radica(texto):
    d = validar(_base(nombre_remitente=h("nombre_remitente", texto)))
    assert d.veredicto is Veredicto.RECHAZADO


@pytest.mark.parametrize("empresa", ["Constructora Andina SAS", "EPS Sura Colombia", "Fundacion Santa Fe"])
def test_una_razon_social_corriente_no_se_toca(empresa):
    d = validar(_base(nombre_remitente=h("nombre_remitente", empresa)))
    assert "nombre_remitente" not in d.correcciones
    assert d.veredicto is not Veredicto.RECHAZADO


def test_decir_mi_caso_no_autoriza_a_cambiar_el_nombre():
    d = validar(_quien_firma("Pedro Gomez Ruiz", metodo="el_tramite_es_del_remitente"))
    assert "nombre_remitente" not in d.correcciones


def test_el_documento_ajeno_del_remitente_se_retira_sin_bloquear():
    d = validar(_base(
        tipo_remitente=h("tipo_remitente", 2),
        numero_documento_remitente=h("numero_documento_remitente", "41234567"),
        tipo_documento_remitente=h("tipo_documento_remitente", 1)))
    assert d.veredicto is Veredicto.CORREGIDO
    assert d.correcciones["numero_documento_remitente"] == ""
    assert d.correcciones["tipo_documento_remitente"] == ""
