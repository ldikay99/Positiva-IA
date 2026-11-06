"""
Tres errores vistos en correos reales, con datos ficticios:

- El telefono del membrete de una clinica (o de su pie de pagina, o de la
  seccion del empleador de un FURAT) enviado como telefono del afectado.
- La direccion de un formulario HTML que arrastra el rotulo siguiente pegado
  ("CALLE 21 N 14-19E-mail:").
- El tipo de solicitud de un correo que solo es la carta de remision: el agente
  tiene que ver el asunto del escrito adjunto.
"""
import pytest

from models.enums import TipoDocumento
from pipeline.agente import prompt_de_sistema
from pipeline.compuerta import Veredicto, informe, validar
from pipeline.extraccion import (
    Hallazgo,
    asuntos_de_los_adjuntos,
    cortar_en_el_rotulo_siguiente,
    extraer_direccion,
    extraer_telefono,
    extraer_todo,
)
from utils.contacto_extractor import (
    extraer_telefonos,
    solo_en_renglones_de_entidad,
)

NOMBRE = "PEDRO RUIZ MEJIA"

ORDEN_DE_CLINICA = """
CLINICA VILLAFICTICIA S.A.S
NIT 900.000.001-2
Direccion: CRA 1 # 2-3, BOGOTA, D.C.
Tel: 3009876543 Correo: atencionalusuario@clinicaficticia.co
Nombre: PEDRO RUIZ MEJIA Identificacion: CC 41.234.567
Sexo: HOMBRE Dir. CRA 9 NO 8 70 Tel. 310 55512
"""


def _numeros(texto):
    return [t["numero"] for t in extraer_telefonos(texto)]


# ── 1. El telefono de una entidad no es del afectado ──

def test_el_telefono_del_membrete_de_la_clinica_no_es_del_afectado():
    # El del paciente viene incompleto: mejor sin telefono que el de la clinica.
    assert "3009876543" not in _numeros(ORDEN_DE_CLINICA)
    h, _ = extraer_telefono(ORDEN_DE_CLINICA, nombre_afectado=NOMBRE)
    assert h is None


@pytest.mark.parametrize("renglon", [
    "Direccion: CALLE 1 NUMERO 2 - 3 -Telefono:6014445566 Bogota-Grandes "
    "Contribuyentes - Web: www.clinicaficticia.co",
    "Telefonos: (601) 444 5566 ext. 1212 - Directos: (601) 2223344",
    "PBX: (601) 444 5566",
    "Conmutador 6014445566",
    "Linea de atencion 6014445566",
])
def test_renglones_de_contacto_de_una_entidad(renglon):
    assert "6014445566" not in _numeros(f"Paciente {NOMBRE}\n{renglon}\n")


def test_si_el_numero_tambien_sale_en_los_datos_del_paciente_se_conserva():
    texto = ORDEN_DE_CLINICA + "\nCelular del paciente: 3009876543\n"
    assert "3009876543" in _numeros(texto)


def test_el_celular_del_ciudadano_con_su_correo_personal_se_conserva():
    texto = (f"Atentamente,\n{NOMBRE}\nCC 41234567\n"
             "Cel: 3105551234 Correo: pedro.ruiz@correoficticio.com\n")
    assert _numeros(texto) == ["3105551234"]


def test_la_prosa_lejos_del_numero_no_lo_vuelve_de_entidad():
    texto = ("Me comunico en linea con lo dicho por la EPS, que es una entidad "
             "que nunca contesta a tiempo, y por eso pido que me llamen a mi "
             "celular 3105551234 para cualquier cosa relacionada con el caso.")
    assert _numeros(texto) == ["3105551234"]


def test_la_seccion_del_empleador_del_furat_no_da_el_telefono():
    furat = (
        "I IDENTIFICACION GENERAL DEL EMPLEADOR CONTRATANTE O COOPERATIVA\n"
        "Nombre o razon social\nCONSULTORES FICTICIOS SAS\n"
        "Direccion sede principal\nCALLE 1 # 2-3\n"
        + "Actividad economica de consultoria y gestion\n" * 6
        + "Telefono\n3201112233\nFax\nE-mail\n"
        "II INFORMACION DE LA PERSONA QUE SE ACCIDENTO\n"
        f"Nombre\n{NOMBRE}\nTelefono\n6012223344\n"
    )
    assert _numeros(furat) == ["6012223344"]


def test_la_seccion_del_empleador_la_cierra_la_de_la_persona_sin_numerar():
    texto = ("Datos del empleador: CONSULTORES FICTICIOS SAS, tel 3201112233.\n"
             f"Datos del trabajador: {NOMBRE}, celular 3105551234.\n")
    assert _numeros(texto) == ["3105551234"]


def test_solo_en_renglones_de_entidad():
    assert solo_en_renglones_de_entidad("3009876543", ORDEN_DE_CLINICA)
    assert solo_en_renglones_de_entidad("+57 300 987 6543", ORDEN_DE_CLINICA)
    assert not solo_en_renglones_de_entidad("3105551234", ORDEN_DE_CLINICA)
    assert not solo_en_renglones_de_entidad(
        "3009876543", ORDEN_DE_CLINICA + "\nCel: 300 987 6543\n")


# ── La compuerta lo vacia, venga de donde venga, sin bloquear ──

def _h(campo, valor, metodo="marca_textual", confianza=0.9):
    return Hallazgo(campo, valor, "cuerpo del correo", confianza, metodo)


def _base(**extra):
    base = {
        "numero_documento_afectado": _h("numero_documento_afectado", "41234567"),
        "nombre_afectado": _h("nombre_afectado", NOMBRE),
        "nombre_remitente": _h("nombre_remitente", NOMBRE),
        "tipo_solicitud": _h("tipo_solicitud", 5),
        "tipo_remitente": _h("tipo_remitente", 3),
        "tipo_documento_afectado": _h("tipo_documento_afectado", TipoDocumento.CC),
    }
    base.update(extra)
    return base


@pytest.mark.parametrize("metodo", ["telefono_celular", "agente_ia"])
def test_la_compuerta_vacia_el_telefono_de_la_entidad(metodo):
    d = validar(_base(telefono_afectado=_h(
        "telefono_afectado", "3009876543", metodo=metodo)),
        fuentes=ORDEN_DE_CLINICA)
    assert d.puede_radicar, informe(d)
    assert d.correcciones.get("telefono_afectado") == ""
    assert any(r.codigo == "TELEFONO_DE_UNA_ENTIDAD" and not r.bloqueante
               for r in d.reparos)


def test_la_compuerta_deja_el_telefono_del_paciente():
    fuentes = ORDEN_DE_CLINICA + "\nCelular: 3105551234\n"
    d = validar(_base(telefono_afectado=_h("telefono_afectado", "3105551234")),
                fuentes=fuentes)
    assert "telefono_afectado" not in d.correcciones, informe(d)


# ── 2. La direccion se corta en el rotulo siguiente, aunque vaya pegado ──

@pytest.mark.parametrize("valor,esperado", [
    ("CALLE 21 N 14-19E-mail:", "CALLE 21 N 14-19"),
    ("CALLE 21 N 14-19E-mail: ana@correoficticio.com", "CALLE 21 N 14-19"),
    ("CALLE 21 N 14-19 * E-mail: ana@correoficticio.com", "CALLE 21 N 14-19"),
    ("Cra 5 # 10-20Teléfono: 3105551234", "Cra 5 # 10-20"),
    ("Cra 5 # 10-20 apto 3Ciudad: Cali", "Cra 5 # 10-20 apto 3"),
    ("Vereda El Alto finca La EsperanzaCorreo: x@y.co", "Vereda El Alto finca La Esperanza"),
    ("Calle 8 # 1-2; Documento: CC 41234567", "Calle 8 # 1-2"),
    ("Cra 5 # 10-20 Cel 3105551234", "Cra 5 # 10-20"),
    # Lo que no es un rotulo no corta.
    ("CARRERA 7 # 12-14 BARRIO SAN CARLOS", "CARRERA 7 # 12-14 BARRIO SAN CARLOS"),
    ("KILOMETRO 17 VIA DINAMARCA", "KILOMETRO 17 VIA DINAMARCA"),
    ("Calle 3 # 4-5 Torre Fecha", "Calle 3 # 4-5 Torre Fecha"),
])
def test_cortar_en_el_rotulo_siguiente(valor, esperado):
    assert cortar_en_el_rotulo_siguiente(valor) == esperado


def test_la_extraccion_corta_la_direccion_del_formulario_html_aplanado():
    cuerpo = (f"Datos del asegurado:\nNombre: {NOMBRE}Documento: CC 41234567"
              "Teléfono: 3105551234Dirección: CALLE 21 N 14-19"
              "E-mail: pedro@correoficticio.com\n")
    h, _ = extraer_direccion(cuerpo, nombre_afectado=NOMBRE,
                             documento_afectado="41234567")
    assert h is not None and h.valor == "CALLE 21 N 14-19"


def test_la_compuerta_limpia_la_direccion_que_arrastra_un_rotulo():
    d = validar(_base(direccion_afectado=_h(
        "direccion_afectado", "CALLE 21 N 14-19E-mail:")))
    assert d.puede_radicar, informe(d)
    assert d.correcciones.get("direccion_afectado") == "CALLE 21 N 14-19"
    assert any(r.codigo == "DIRECCION_CON_ROTULO_AJENO" for r in d.reparos)


def test_la_compuerta_no_toca_una_direccion_limpia():
    d = validar(_base(direccion_afectado=_h(
        "direccion_afectado", "CALLE 21 N 14-19")))
    assert "direccion_afectado" not in d.correcciones
    assert d.veredicto is Veredicto.APROBADO, informe(d)


# ── 3. La carta de remision: el agente ve el asunto del escrito adjunto ──

ADJUNTOS = """# PETICION AUTORIZAR PROTESIS DE RODILLA.pdf

## Pagina 1

DERECHO DE PETICION
Senores: ARL
ASUNTO: REQUERIMIENTO URGENTE PARA
AUTORIZAR PROTESIS
DE RODILLA IZQUIERDA. CUMPLIMIENTO DEL FALLO DE TUTELA.

Yo, PEDRO RUIZ MEJIA, presento derecho de peticion...

# historia_clinica.pdf

## Pagina 1

Paciente PEDRO RUIZ MEJIA. Ortopedia.
"""


def test_asuntos_de_los_adjuntos():
    lineas = asuntos_de_los_adjuntos(ADJUNTOS).splitlines()
    assert len(lineas) == 2
    assert lineas[0].startswith("- PETICION AUTORIZAR PROTESIS DE RODILLA.pdf")
    assert ("ASUNTO: REQUERIMIENTO URGENTE PARA AUTORIZAR PROTESIS DE RODILLA "
            "IZQUIERDA. CUMPLIMIENTO DEL FALLO DE TUTELA.") in lineas[0]
    assert lineas[1] == "- historia_clinica.pdf"
    assert asuntos_de_los_adjuntos("") == ""


def test_el_contexto_del_agente_lleva_los_adjuntos():
    ext = extraer_todo(
        asunto="Derecho de Peticion",
        cuerpo="Adjunto Derecho de Peticion con sus anexos.\nPEDRO RUIZ MEJIA",
        texto_adjuntos=ADJUNTOS,
    )
    assert "ADJUNTOS" in ext.contexto
    assert "AUTORIZAR PROTESIS" in ext.contexto
    assert "AUTORIZAR PROTESIS" in ext.resumen_para_ia() or not ext.no_resueltos


def test_el_criterio_no_deja_clasificar_14_por_la_forma_del_escrito():
    prompt = prompt_de_sistema(["tipo_solicitud"])
    assert "carta de remision" in prompt
    assert "nunca justifica 14" in prompt
