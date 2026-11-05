"""
El valor que valida la compuerta es el que llega a SGDEA.

Cada capa puede ser correcta por separado y el conjunto escribir un dato falso:
la compuerta mide un documento sin puntos y el radicador envia el que los trae,
o el radicador rellena un campo despues de la validacion con un criterio que la
compuerta no comparte. Aqui se comprueba el recorrido completo, no cada capa.
"""
from pathlib import Path

import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline.compuerta import CONFIANZA_PARA_RADICAR, Veredicto, validar
from pipeline.extraccion import (
    Hallazgo, UMBRAL_CONFIANZA, motivo_por_el_que_no_es_un_nombre)
from pipeline.radicador import armar_payload
from utils.tipos_documento import solo_digitos

RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"


def _email(nombre="Ana Lopez", direccion="ana@empresa.com"):
    return EmailMessage(
        id="correo-1",
        subject="Solicitud de incapacidad",
        body=EmailBody(content="cuerpo del correo"),
        from_address=EmailAddress(name=nombre, address=direccion),
        received_datetime="2026-08-24T13:30:00Z",
    )


def _hallazgos(**extra):
    base = {
        "nombre_afectado": Hallazgo("nombre_afectado", "MUÑOZ PEÑA JOSE", "e", 0.9),
        "nombre_remitente": Hallazgo("nombre_remitente", "Ana Lopez", "e", 0.9),
        "numero_documento_afectado": Hallazgo(
            "numero_documento_afectado", "41234567", "e", 0.9),
        "tipo_documento_afectado": Hallazgo("tipo_documento_afectado", 1, "e", 0.9),
        "tipo_solicitud": Hallazgo("tipo_solicitud", 16, "e", 0.9),
        "tipo_remitente": Hallazgo("tipo_remitente", 2, "e", 0.9),
        "oficina": Hallazgo("oficina", 2, "e", 1.0),
    }
    base.update(extra)
    for campo in [c for c, h in base.items() if h is None]:
        del base[campo]
    return base


# ── El documento: una sola forma en las dos capas ──

def test_el_documento_llega_a_sgdea_como_lo_midio_la_compuerta():
    """
    El documento es la clave con la que se identifica a una persona. Escrito con
    puntos deja un segundo registro de identidad para el mismo ciudadano y la
    busqueda por documento no lo encuentra.
    """
    hallazgos = _hallazgos(numero_documento_afectado=Hallazgo(
        "numero_documento_afectado", "41.234.567", "e", 0.9))

    dictamen = validar(hallazgos)
    assert not [r for r in dictamen.reparos if r.campo.startswith("numero_")]

    d = armar_payload(_email(), hallazgos).to_form_data()
    assert d["datosAfectadoNumeroDocumento"] == "41234567"


def test_el_documento_del_remitente_tambien_se_normaliza():
    hallazgos = _hallazgos(
        tipo_remitente=Hallazgo("tipo_remitente", 3, "e", 0.9),
        numero_documento_remitente=Hallazgo(
            "numero_documento_remitente", "41 234 567", "e", 0.9),
    )
    d = armar_payload(_email(), hallazgos).to_form_data()
    assert d["datosRemitenteNumeroDocumento"] == "41234567"


# ── El tipo de documento no se inventa ──

def test_un_documento_sin_tipo_no_se_declara_cedula_en_silencio():
    """
    Un NIT tipificado como cedula deja en la base a una empresa con la
    identificacion de una persona. El defecto se aplica igual, pero consta.
    """
    hallazgos = _hallazgos(numero_documento_remitente=Hallazgo(
        "numero_documento_remitente", "900123456", "e", 0.9))
    suposiciones = []

    d = armar_payload(_email(), hallazgos,
                      suposiciones=suposiciones).to_form_data()

    assert d["datosRemitenteTipoDocumentoId"] == 1
    assert any("tipo_documento_remitente no se resolvio" in s
               for s in suposiciones), suposiciones


def test_sin_documento_del_remitente_el_tipo_dice_que_no_describe_nada():
    suposiciones = []
    armar_payload(_email(), _hallazgos(), suposiciones=suposiciones)
    assert any("sin documento que describir" in s
               for s in suposiciones), suposiciones


# ── La geografia se rellena, y se dice ──

def test_el_domicilio_por_defecto_queda_anotado():
    """
    Ningun correo declara departamento ni municipio y el payload los exige. El
    relleno es legitimo; presentarlo como un dato leido del correo no lo es.
    """
    suposiciones = []
    d = armar_payload(_email(), _hallazgos(),
                      suposiciones=suposiciones).to_form_data()

    assert d["datosAfectadoDepartamentoIdDepartamento"] == 6
    assert d["datosAfectadoMunicipioIdMunicipio"] == 149
    assert any("departamento" in s for s in suposiciones), suposiciones
    assert any("municipio" in s for s in suposiciones), suposiciones


# ── Un nombre mas corto es la misma persona ──

def test_el_defecto_de_tipo_remitente_no_afirma_una_identidad():
    """
    Este campo se rellena despues de la compuerta, asi que ella no puede
    corregirlo. De los tres valores del catalogo, "Tercero" es el unico que no
    afirma que quien escribe sea la persona del caso ni autoriza a copiarle su
    documento. Que dos nombres coincidan no prueba lo contrario: puede ser que
    solo se conozca uno de los dos.
    """
    for nombre_remitente in ("FABIOLA ROBLEDO", "Ana Lopez"):
        hallazgos = _hallazgos(
            nombre_afectado=Hallazgo("nombre_afectado",
                                     "FABIOLA ROBLEDO REVELO", "e", 0.9),
            nombre_remitente=Hallazgo("nombre_remitente", nombre_remitente,
                                      "e", 0.9),
            tipo_remitente=None,
        )
        d = armar_payload(_email(), hallazgos).to_form_data()
        assert d["datosRemitenteTipoRemitenteId"] == 2, nombre_remitente


def test_el_criterio_de_misma_persona_sigue_rigiendo_en_la_compuerta():
    """
    La comparacion por subconjunto se dejo de usar para el defecto, pero es la
    que la compuerta aplica a un `tipo_remitente` que SI le llega: con el mismo
    nombre escrito mas corto, un tipo 1 o 2 se corrige a 3.
    """
    hallazgos = _hallazgos(
        nombre_afectado=Hallazgo("nombre_afectado", "FABIOLA ROBLEDO REVELO",
                                 "e", 0.9),
        nombre_remitente=Hallazgo("nombre_remitente", "FABIOLA ROBLEDO",
                                  "e", 0.9),
    )
    dictamen = validar(hallazgos)
    assert dictamen.correcciones.get("tipo_remitente") == 3


# ── El piso de confianza rige para todo campo que va a la base ──

def test_un_campo_no_obligatorio_por_debajo_del_piso_no_se_escribe():
    """
    Un valor poco fiable no se afirma. Se descarta y el radicador envia su
    defecto, que queda anotado: no bloquea el correo porque el campo no es
    obligatorio.
    """
    hallazgos = _hallazgos(
        sexo_afectado=Hallazgo("sexo_afectado", 2, "e", 0.40))
    dictamen = validar(hallazgos)

    assert dictamen.veredicto is Veredicto.CORREGIDO
    assert dictamen.correcciones["sexo_afectado"] == ""
    assert not dictamen.bloqueantes


def test_el_tipo_de_pqrd_poco_fiable_no_decide_el_plazo():
    """Un derecho de peticion tiene termino constitucional de respuesta."""
    hallazgos = _hallazgos(tipo_pqrd=Hallazgo("tipo_pqrd", 2, "e", 0.55))
    dictamen = validar(hallazgos)
    assert dictamen.correcciones["tipo_pqrd"] == ""


def test_un_obligatorio_por_debajo_del_piso_bloquea():
    hallazgos = _hallazgos(
        nombre_afectado=Hallazgo("nombre_afectado", "Ana Lopez", "e", 0.40))
    dictamen = validar(hallazgos)

    assert dictamen.veredicto is Veredicto.RECHAZADO
    assert any(r.codigo == "CONFIANZA_INSUFICIENTE" and r.bloqueante
               for r in dictamen.reparos)


def test_el_tipo_de_solicitud_dudoso_se_acepta_en_vez_de_bloquear():
    """
    Donde el campo no admite quedar vacio, el piso no compara un dato dudoso
    contra ningun dato: lo compara contra el que se pondria en su lugar.
    Bloquear manda el correo a una cola humana y rellenar por defecto lo enruta
    a la oficina de "Otra"; las dos afirman tanto como el valor dudoso.
    """
    hallazgos = _hallazgos(
        tipo_solicitud=Hallazgo("tipo_solicitud", 16, "e", 0.40))
    dictamen = validar(hallazgos)

    assert dictamen.veredicto is not Veredicto.RECHAZADO
    assert not any(r.codigo == "CONFIANZA_INSUFICIENTE"
                   for r in dictamen.reparos)


def test_la_excepcion_del_piso_no_alcanza_a_los_demas_obligatorios():
    """
    Los otros tres obligatorios SI admiten quedar vacios: sin ellos el correo
    va a revision y nadie escribe un dato falso.
    """
    from pipeline.compuerta import OBLIGATORIOS, PISO_POR_CAMPO

    assert set(PISO_POR_CAMPO) == {"tipo_solicitud"}
    assert set(PISO_POR_CAMPO) < set(OBLIGATORIOS)


def test_las_dos_capas_que_filtran_usan_el_mismo_piso():
    """
    El agente descarta antes de que la compuerta mire. Con dos constantes,
    bajar una dejaba a la otra descartando lo mismo un paso despues.
    """
    from pipeline.agente import CONFIANZA_MINIMA
    from pipeline.compuerta import CONFIANZA_PARA_RADICAR

    assert CONFIANZA_MINIMA == CONFIANZA_PARA_RADICAR


# ── Todo codigo se compara contra su catalogo ──

def test_el_tipo_de_solicitud_se_valida_contra_el_catalogo():
    """Decide la cola de atencion: fuera del catalogo no entra a ninguna."""
    hallazgos = _hallazgos(
        tipo_solicitud=Hallazgo("tipo_solicitud", 99, "e", 0.9))
    dictamen = validar(hallazgos)

    assert any(r.campo == "tipo_solicitud" and r.codigo == "FUERA_DE_CATALOGO"
               for r in dictamen.reparos), dictamen.reparos


def test_un_catalogo_que_no_cargo_no_pasa_por_comprobado():
    """
    Un catalogo ausente es una validacion que no se hizo, no una validacion
    superada: el codigo llegaria a SGDEA sin que nadie lo mirase.
    """
    catalogos = {
        "tipo_remitente": {1: "Familiar", 2: "Tercero", 3: "Afectado"},
        "tipo_documento": {1: "CC"},
        "tipo_pqrd": {1: "Peticion"},
        "sexo": {3: "Otro"},
    }
    dictamen = validar(_hallazgos(), catalogos=catalogos)

    assert any(r.campo == "tipo_solicitud" and r.codigo == "CATALOGO_NO_DISPONIBLE"
               for r in dictamen.reparos), dictamen.reparos


# ── El telefono sirve para llamar a alguien ──

def test_el_telefono_no_puede_ser_el_numero_de_documento():
    """
    Una cedula de diez digitos que empieza por 3 tiene la forma de un celular y
    el plan de numeracion la acepta. Escribirla en el campo de telefono desplaza
    un dato de identidad a otra columna y deja al ciudadano incontactable.
    """
    hallazgos = _hallazgos(
        numero_documento_afectado=Hallazgo(
            "numero_documento_afectado", "3111701816", "e", 0.9),
        telefono_afectado=Hallazgo("telefono_afectado", "3111701816", "e", 0.9),
    )
    dictamen = validar(hallazgos)

    assert any(r.codigo == "TELEFONO_ES_EL_DOCUMENTO"
               for r in dictamen.reparos), dictamen.reparos
    assert dictamen.correcciones["telefono_afectado"] == ""
    assert not dictamen.bloqueantes


def test_el_telefono_se_envia_en_una_sola_forma():
    """
    El mismo numero con espacios y sin ellos son dos valores distintos en la
    misma columna. El algoritmo lo escribe en digitos pelados y sin el
    indicativo 57; lo que reporta el modelo tambien.
    """
    hallazgos = _hallazgos(
        telefono_afectado=Hallazgo("telefono_afectado", "+57 300 123 4567",
                                   "e", 0.9))
    d = armar_payload(_email(), hallazgos).to_form_data()
    assert d["datosAfectadoTelefonoCelular"] == "3001234567"


def test_el_telefono_de_otro_pais_conserva_su_indicativo():
    hallazgos = _hallazgos(
        telefono_afectado=Hallazgo("telefono_afectado", "+32 470 12 34 56",
                                   "e", 0.9))
    d = armar_payload(_email(), hallazgos).to_form_data()
    assert d["datosAfectadoTelefonoCelular"] == "+32470123456"


# ── Un nombre con marca de empresa sigue teniendo que nombrar a alguien ──

def test_positiva_no_puede_ser_la_afectada_de_su_propia_pqrd():
    """
    La comprobacion de nombre se salta el vocabulario administrativo cuando el
    texto lleva marca de razon social, porque una empresa legitima lleva esas
    palabras. Positiva es quien recibe la PQRD: no es ninguna parte del caso.
    """
    hallazgos = _hallazgos(
        nombre_afectado=Hallazgo("nombre_afectado", "ARL POSITIVA", "e", 0.9))
    dictamen = validar(hallazgos)

    assert any(r.campo == "nombre_afectado" and r.codigo == "NO_PARECE_UN_NOMBRE"
               for r in dictamen.reparos), dictamen.reparos


def test_un_area_de_positiva_no_es_el_remitente():
    hallazgos = _hallazgos(nombre_remitente=Hallazgo(
        "nombre_remitente", "Grupo Medicina Laboral", "e", 0.9))
    dictamen = validar(hallazgos)

    assert any(r.campo == "nombre_remitente" and r.codigo == "NO_PARECE_UN_NOMBRE"
               for r in dictamen.reparos), dictamen.reparos


def test_una_razon_social_ajena_sigue_pasando():
    """
    Si la empresa puede ser el afectado es una decision del negocio, no de esta
    comprobacion: el nombre de una empresa externa no se rechaza aqui.
    """
    for razon_social in ("SIETE M SAS", "CONSTRUCTORA GED S.A.S.",
                         "MUNICIPIO DE ZIPAQUIRA"):
        hallazgos = _hallazgos(nombre_afectado=Hallazgo(
            "nombre_afectado", razon_social, "e", 0.9))
        dictamen = validar(hallazgos)
        assert not [r for r in dictamen.reparos
                    if r.codigo == "NO_PARECE_UN_NOMBRE"], razon_social


# ── El correo del afectado es de una persona ──

def test_un_buzon_de_positiva_no_se_escribe_como_correo_del_afectado():
    """
    Es el canal por el que se responde al ciudadano. El buzon de la propia
    entidad dirige la respuesta a Positiva y deja un registro que parece
    completo.
    """
    correo = _email("Notificaciones", "notificaciones@positiva.gov.co")
    suposiciones = []

    d = armar_payload(
        correo, _hallazgos(),
        "Reenvio la solicitud de juan.perez@gmail.com para su tramite",
        suposiciones=suposiciones,
    ).to_form_data()

    assert d["datosAfectadoCorreoElectronico"] == "juan.perez@gmail.com"
    assert any("no es de una persona" in s for s in suposiciones), suposiciones


def test_sin_correo_externo_el_campo_va_vacio_y_consta():
    correo = _email("Notificaciones", "notificaciones@positiva.gov.co")
    suposiciones = []

    d = armar_payload(correo, _hallazgos(), "sin ninguna direccion en el cuerpo",
                      suposiciones=suposiciones).to_form_data()

    assert d["datosAfectadoCorreoElectronico"] == ""
    assert any("se envia vacio" in s for s in suposiciones), suposiciones


def test_el_buzon_de_una_persona_se_usa_tal_cual():
    d = armar_payload(_email(), _hallazgos()).to_form_data()
    assert d["datosAfectadoCorreoElectronico"] == "ana@empresa.com"


# ── Sobre el corpus real: el extractor no se contradice con la compuerta ──
#
# Estas comprobaciones no necesitan etiquetas. Miden que las dos capas se
# pongan de acuerdo, asi que valen sobre los 102 casos y no solo sobre los
# campos etiquetados.

@pytest.fixture(scope="module")
def hallazgos_del_corpus():
    """Los hallazgos de cada caso del corpus, una sola vez por modulo."""
    from harness.caso import cargar_corpus, extraer_de

    casos = cargar_corpus(RAIZ_CORPUS)
    if not casos:
        pytest.skip("corpus no recolectado")
    return [(c.id, extraer_de(c).hallazgos) for c in casos]


def test_ningun_nombre_del_extractor_lo_rechaza_la_compuerta(hallazgos_del_corpus):
    """
    La compuerta aplica al nombre el criterio del propio extractor. Un valor que
    el algoritmo produce y la compuerta bloquea manda el correo a revision
    manual por un conflicto que el pipeline se creo a si mismo.
    """
    contradicciones = []
    for caso_id, hallazgos in hallazgos_del_corpus:
        for campo in ("nombre_afectado", "nombre_remitente"):
            h = hallazgos.get(campo)
            if h is None:
                continue
            motivo = motivo_por_el_que_no_es_un_nombre(h.valor)
            if motivo:
                contradicciones.append(f"{caso_id} {campo}={h.valor!r}: {motivo}")
    assert not contradicciones, "\n".join(contradicciones)


def test_ningun_hallazgo_del_extractor_queda_bajo_el_piso_de_la_compuerta(
        hallazgos_del_corpus):
    """
    El extractor exige mas confianza que la compuerta, asi que nada de lo que
    resuelve puede caer por debajo del piso para radicar. Si los dos umbrales se
    cruzan, el algoritmo empieza a producir valores que su propia validacion
    descarta.
    """
    assert UMBRAL_CONFIANZA >= CONFIANZA_PARA_RADICAR
    bajos = [
        f"{caso_id} {campo} confianza {h.confianza:.2f}"
        for caso_id, hallazgos in hallazgos_del_corpus
        for campo, h in hallazgos.items()
        if h.confianza < CONFIANZA_PARA_RADICAR
    ]
    assert not bajos, "\n".join(bajos)


def test_todo_documento_del_extractor_llega_normalizado(hallazgos_del_corpus):
    """
    El extractor produce digitos limpios. Si alguno trae separadores, la
    compuerta mide una forma y el payload envia otra.
    """
    sucios = [
        f"{caso_id} {campo}={h.valor!r}"
        for caso_id, hallazgos in hallazgos_del_corpus
        for campo, h in hallazgos.items()
        if campo.startswith("numero_documento")
        and str(h.valor) != solo_digitos(h.valor)
    ]
    assert not sucios, "\n".join(sucios)
