"""
Consulta de personas contra el registro de SGDEA.

El endpoint no valida la sigla del tipo: una equivocada devuelve el mismo 404
que un documento inexistente. Eso obliga a mandar la sigla que el registro
entiende, que no es la misma que usa `models.enums`.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pytest

from models.enums import TipoDocumento
from pipeline.extraccion import Hallazgo, SEXO_FEMENINO, SEXO_MASCULINO
from pipeline.personas import (
    SIGLA_POR_DEFECTO,
    ConsultaDePersonaFallo,
    ConsultaDePersonas,
    rellenar_desde_el_registro,
)

URL = "https://ms-sgdea-beta.ejemplo"

# Forma real de la respuesta, con los datos de la persona sustituidos.
REGISTRO = {
    "id": None,
    "tipoDocumento": "CC",
    "numeroDocumento": "41234567",
    "pais": {"idPais": 425, "nombre": "Colombia", "idContinente": 1,
             "idPositiva": 170},
    "departamento": None,
    "municipio": None,
    "nombre1": "Ana", "nombre2": "Maria",
    "apellido1": "Lopez", "apellido2": "Rojas",
    "telefono": "3105550001",
    "direccion": "CL 63 A SUR 70 G 44",
    "email": None,
    "genero": 2,
    "siniestros": [],
}


@dataclass
class Respuesta:
    status_code: int
    cuerpo: Any = None
    text: str = ""

    def json(self):
        if isinstance(self.cuerpo, Exception):
            raise self.cuerpo
        return self.cuerpo


class ClienteFalso:
    """Registra lo que se pidio y devuelve lo que se le programe."""

    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.peticiones = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.peticiones.append({"url": url, "params": dict(params or {}),
                                "headers": dict(headers or {})})
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        return self.respuesta


def consulta(respuesta):
    cliente = ClienteFalso(respuesta)
    return ConsultaDePersonas(url_base=URL, cliente=cliente), cliente


# ── Lo que devuelve ──

def test_devuelve_los_campos_utiles():
    c, _ = consulta(Respuesta(200, REGISTRO))
    datos = c("41234567")
    assert datos["nombre"] == "Ana Maria Lopez Rojas"
    assert datos["telefono"] == "3105550001"
    assert datos["direccion"] == "CL 63 A SUR 70 G 44"
    assert datos["pais"]["idPais"] == 425


def test_devuelve_el_genero_en_crudo_sin_traducir():
    """
    El cliente no lo interpreta: la traduccion vive en el relleno, que es donde
    se sabe a que catalogo va destinado.
    """
    c, _ = consulta(Respuesta(200, REGISTRO))
    assert c("41234567").get("genero") in (None, 2)


def test_omite_los_campos_vacios():
    """Un campo nulo del registro no puede llegar como dato."""
    c, _ = consulta(Respuesta(200, {**REGISTRO, "telefono": "", "email": None}))
    datos = c("41234567")
    assert "telefono" not in datos
    assert "correo" not in datos
    assert "departamento" not in datos


def test_un_nombre_incompleto_no_deja_huecos():
    c, _ = consulta(Respuesta(200, {**REGISTRO, "nombre2": None,
                                    "apellido2": ""}))
    assert c("41234567")["nombre"] == "Ana Lopez"


# ── Que se le pregunta al registro ──

def test_manda_la_sigla_que_el_registro_entiende():
    """
    El endpoint espera PPT, NUIP y NIT; `models.enums` produce PT, NP y NI.
    Mandar la nuestra no da error, da un 404 que parece "no existe".
    """
    for tipo, sigla in ((TipoDocumento.PPT, "PPT"), (TipoDocumento.NP, "NUIP"),
                        (TipoDocumento.NI, "NIT"), (TipoDocumento.PE, "PEP"),
                        (TipoDocumento.CC, "CC")):
        c, cliente = consulta(Respuesta(200, REGISTRO))
        c("41234567", tipo_documento=int(tipo))
        assert cliente.peticiones[-1]["params"]["tipoDocumento"] == sigla


def test_traduce_tambien_nuestras_siglas_internas():
    for interna, sigla in (("PT", "PPT"), ("NP", "NUIP"), ("NI", "NIT"),
                           ("PE", "PEP")):
        c, cliente = consulta(Respuesta(200, REGISTRO))
        c("41234567", tipo_documento=interna)
        assert cliente.peticiones[-1]["params"]["tipoDocumento"] == sigla


def test_sin_tipo_pregunta_por_cedula_y_no_recorre_todas():
    """
    Probar las nueve siglas no distingue nada —el 404 es el mismo— y
    multiplicaria por nueve las peticiones contra produccion.
    """
    c, cliente = consulta(Respuesta(404, None))
    assert c("41234567") is None
    assert len(cliente.peticiones) == 1
    assert cliente.peticiones[0]["params"]["tipoDocumento"] == SIGLA_POR_DEFECTO


def test_limpia_el_numero_antes_de_preguntar():
    c, cliente = consulta(Respuesta(200, REGISTRO))
    c("41.234.567")
    assert cliente.peticiones[0]["params"]["numeroDocumento"] == "41234567"


# ── No encontrar a alguien no es un fallo ──

def test_no_encontrado_devuelve_none():
    """
    Que el registro no tenga a esa persona no invalida el documento: puede no
    estar vinculada. La radicacion sigue.
    """
    c, _ = consulta(Respuesta(404, None))
    assert c("41234567") is None


def test_una_respuesta_sin_documento_cuenta_como_no_encontrado():
    c, _ = consulta(Respuesta(200, {"numeroDocumento": None}))
    assert c("41234567") is None


# ── Los fallos se distinguen de la ausencia ──

@pytest.mark.parametrize("estado", [400, 401, 500, 502, 503])
def test_un_error_del_registro_no_se_confunde_con_no_encontrado(estado):
    """
    Devolver None ante un 500 afirmaria que la persona no existe. Son cosas
    distintas y el llamador tiene que poder distinguirlas.
    """
    c, _ = consulta(Respuesta(estado, None, text="se cayo"))
    with pytest.raises(ConsultaDePersonaFallo) as e:
        c("41234567")
    assert str(estado) in str(e.value)


def test_un_fallo_de_red_dice_el_motivo():
    c, _ = consulta(TimeoutError("se agoto el tiempo"))
    with pytest.raises(ConsultaDePersonaFallo) as e:
        c("41234567")
    assert "TimeoutError" in str(e.value)


def test_una_respuesta_que_no_es_json_falla_con_su_motivo():
    c, _ = consulta(Respuesta(200, ValueError("no es json")))
    with pytest.raises(ConsultaDePersonaFallo) as e:
        c("41234567")
    assert "no es JSON" in str(e.value)


def test_sin_numero_no_se_pregunta_nada():
    c, cliente = consulta(Respuesta(200, REGISTRO))
    with pytest.raises(ConsultaDePersonaFallo):
        c("   ")
    assert not cliente.peticiones


# ── Rellenar huecos, nunca sustituir ──

def h(campo, valor):
    return Hallazgo(campo=campo, valor=valor, evidencia="del correo",
                    confianza=0.9, metodo="prueba")


def registro_que_devuelve(datos, registrar=None):
    def consultar(documento, tipo=None):
        if registrar is not None:
            registrar.append((documento, tipo))
        if isinstance(datos, Exception):
            raise datos
        return datos
    return consultar


DEL_REGISTRO = {"nombre": "Ana Maria Lopez Rojas", "telefono": "3105550001",
                "direccion": "CL 63 A SUR 70 G 44"}


def test_rellena_solo_lo_que_falta():
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
        "nombre_afectado": h("nombre_afectado", "Ana Maria Lopez Rojas"),
        "telefono_afectado": h("telefono_afectado", "3009998877"),
    }
    nuevos = rellenar_desde_el_registro(
        hallazgos, registro_que_devuelve(DEL_REGISTRO))
    assert "telefono_afectado" not in nuevos
    assert nuevos["direccion_afectado"].valor == "CL 63 A SUR 70 G 44"
    assert nuevos["direccion_afectado"].metodo == "registro_de_sgdea"


def test_lo_que_el_correo_dice_no_se_pisa():
    """
    El dato del correo puede ser justo la actualizacion que el ciudadano pide.
    """
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
        "nombre_afectado": h("nombre_afectado", "Ana Maria Lopez Rojas"),
        "telefono_afectado": h("telefono_afectado", "3009998877"),
        "direccion_afectado": h("direccion_afectado", "CRA 1 # 2-3"),
    }
    assert rellenar_desde_el_registro(
        hallazgos, registro_que_devuelve(DEL_REGISTRO)) == {}


def test_no_rellena_si_el_registro_nombra_a_otra_persona():
    """
    Todo lo que trae el registro descansa en que el documento sea el correcto.
    Si el nombre no cuadra, uno de los dos se extrajo mal y no se sabe cual.
    """
    avisos = []
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
        "nombre_afectado": h("nombre_afectado", "Pedro Gomez Ruiz"),
    }
    assert rellenar_desde_el_registro(
        hallazgos, registro_que_devuelve(DEL_REGISTRO), avisos) == {}
    assert avisos and "otra persona" in avisos[0]


def test_la_forma_corta_del_nombre_no_se_toma_por_otra_persona():
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
        "nombre_afectado": h("nombre_afectado", "Ana Lopez"),
    }
    nuevos = rellenar_desde_el_registro(
        hallazgos, registro_que_devuelve(DEL_REGISTRO))
    assert nuevos["telefono_afectado"].valor == "3105550001"


def test_sin_documento_no_se_consulta():
    pedidas = []
    hallazgos = {"nombre_afectado": h("nombre_afectado", "Ana Lopez")}
    assert rellenar_desde_el_registro(
        hallazgos, registro_que_devuelve(DEL_REGISTRO, pedidas)) == {}
    assert not pedidas


def test_se_pregunta_con_el_tipo_de_documento_del_afectado():
    pedidas = []
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "900123456"),
        "tipo_documento_afectado": h("tipo_documento_afectado", 10),
    }
    rellenar_desde_el_registro(
        hallazgos, registro_que_devuelve(DEL_REGISTRO, pedidas))
    assert pedidas == [("900123456", "10")]


def test_un_fallo_del_registro_no_detiene_la_radicacion():
    """
    La consulta es un extra. Si el registro se cae, se radica con lo del correo
    y el motivo queda anotado.
    """
    avisos = []
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
    }
    nuevos = rellenar_desde_el_registro(
        hallazgos,
        registro_que_devuelve(ConsultaDePersonaFallo("se cayo el registro")),
        avisos,
    )
    assert nuevos == {}
    assert avisos and "no respondio" in avisos[0]


def test_no_encontrar_a_la_persona_no_es_un_aviso():
    """Que no este vinculada es corriente, no un problema que reportar."""
    avisos = []
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
    }
    assert rellenar_desde_el_registro(
        hallazgos, registro_que_devuelve(None), avisos) == {}
    assert avisos == []


def test_no_rellena_el_nombre():
    """
    Rellenar el nombre convierte un caso de revision manual en una radicacion
    automatica apoyada en un documento sin verificar, y ahi el guardian de
    coherencia seria circular.
    """
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
    }
    nuevos = rellenar_desde_el_registro(
        hallazgos, registro_que_devuelve({**DEL_REGISTRO, "genero": 2}))
    assert "nombre_afectado" not in nuevos


# ── Los dos catalogos de genero estan invertidos entre si ──

@pytest.mark.parametrize("genero_del_registro,sexo_del_payload", [
    (2, SEXO_MASCULINO),
    (1, SEXO_FEMENINO),
])
def test_el_genero_del_registro_se_traduce_al_del_payload(
    genero_del_registro, sexo_del_payload
):
    """
    Copiarlo tal cual invierte el sexo de cada persona. El payload se leyo de
    los logs del job de produccion y el registro de sus propias respuestas.
    """
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
    }
    nuevos = rellenar_desde_el_registro(
        hallazgos,
        registro_que_devuelve({**DEL_REGISTRO, "genero": genero_del_registro}))
    assert nuevos["sexo_afectado"].valor == sexo_del_payload


@pytest.mark.parametrize("desconocido", [0, 3, 4, 9, None, "", "F"])
def test_un_genero_que_no_se_ha_visto_no_se_interpreta(desconocido):
    """Un codigo sin comprobar no se traduce: se deja el campo sin resolver."""
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
    }
    nuevos = rellenar_desde_el_registro(
        hallazgos, registro_que_devuelve({**DEL_REGISTRO, "genero": desconocido}))
    assert "sexo_afectado" not in nuevos


def test_el_sexo_que_el_correo_declara_manda_sobre_el_registro():
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
        "sexo_afectado": h("sexo_afectado", SEXO_FEMENINO),
    }
    nuevos = rellenar_desde_el_registro(
        hallazgos, registro_que_devuelve({**DEL_REGISTRO, "genero": 2}))
    assert "sexo_afectado" not in nuevos


def test_la_evidencia_explica_la_traduccion():
    """
    Un 1 que se convierte en 2 sin explicacion parece un error al revisarlo a
    mano.
    """
    hallazgos = {
        "numero_documento_afectado": h("numero_documento_afectado", "41234567"),
    }
    nuevos = rellenar_desde_el_registro(
        hallazgos, registro_que_devuelve({**DEL_REGISTRO, "genero": 2}))
    assert "genero=2" in nuevos["sexo_afectado"].evidencia


# ── No se insiste con un registro que no responde ──

class _ClienteQueSeAgota:
    """Cliente que siempre agota el tiempo de espera."""

    class Agotado(Exception):
        pass

    def __init__(self):
        self.peticiones = 0

    def get(self, url, **kw):
        self.peticiones += 1
        raise TimeoutError("read timeout")


class _ClienteQueResponde:
    def __init__(self, status_code=404):
        self.peticiones = 0
        self.status_code = status_code

    def get(self, url, **kw):
        self.peticiones += 1

        class R:
            status_code = self.status_code
            text = ""

            def json(self_inner):
                return {}

        return R()


def test_tras_varios_agotamientos_seguidos_se_deja_de_preguntar():
    """
    El tiempo maximo se paga por correo. Medido en produccion, las veinte
    consultas de una jornada estaban todas contra el tope de quince segundos:
    cinco minutos de espera pura dentro de una ventana de treinta, sin un solo
    dato. Cuando el registro se cae, se cae para el lote entero.
    """
    from pipeline.personas import (
        AGOTAMIENTOS_PARA_DEJAR_DE_PREGUNTAR,
        ConsultaDePersonaFallo,
        ConsultaDePersonas,
    )

    cliente = _ClienteQueSeAgota()
    consulta = ConsultaDePersonas(url_base="https://ejemplo", cliente=cliente)

    for _ in range(10):
        with pytest.raises(ConsultaDePersonaFallo):
            consulta.consultar("41234567")

    assert cliente.peticiones == AGOTAMIENTOS_PARA_DEJAR_DE_PREGUNTAR


def test_el_corte_dice_por_que_no_pregunto():
    """Nada falla en silencio: la consulta que no se hizo tambien lleva motivo."""
    from pipeline.personas import ConsultaDePersonaFallo, ConsultaDePersonas

    consulta = ConsultaDePersonas(url_base="https://ejemplo",
                                  cliente=_ClienteQueSeAgota())
    for _ in range(4):
        try:
            consulta.consultar("41234567")
        except ConsultaDePersonaFallo as e:
            ultimo = str(e)

    assert "consultas seguidas" in ultimo


def test_una_respuesta_cualquiera_reinicia_la_cuenta():
    """
    Un 404 prueba que el servicio contesta: un bache no puede apagar la
    consulta para lo que queda de la corrida.
    """
    from pipeline.personas import ConsultaDePersonaFallo, ConsultaDePersonas

    que_se_agota = _ClienteQueSeAgota()
    consulta = ConsultaDePersonas(url_base="https://ejemplo",
                                  cliente=que_se_agota)
    for _ in range(2):
        with pytest.raises(ConsultaDePersonaFallo):
            consulta.consultar("41234567")

    responde = _ClienteQueResponde()
    consulta.cliente = responde
    assert consulta.consultar("41234567") is None
    assert consulta.agotamientos_seguidos == 0

    # Y se vuelve a preguntar con normalidad.
    assert consulta.consultar("41234567") is None
    assert responde.peticiones == 2


def test_un_fallo_que_no_es_de_tiempo_no_cuenta_para_el_corte():
    """
    Un documento vacio o un JSON invalido no dicen nada del servicio. Contarlos
    apagaria la consulta por un problema que no es suyo.
    """
    from pipeline.personas import ConsultaDePersonaFallo, ConsultaDePersonas

    consulta = ConsultaDePersonas(url_base="https://ejemplo",
                                  cliente=_ClienteQueResponde(500))
    for _ in range(5):
        with pytest.raises(ConsultaDePersonaFallo):
            consulta.consultar("41234567")

    assert consulta.agotamientos_seguidos == 0
